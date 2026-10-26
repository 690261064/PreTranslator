# -*- coding: utf-8 -*-
"""翻译引擎: 多通道并行 + 每通道自适应节流 + 断点续传 + 二分补翻。

通道模型 (解决 429 限速的核心):
- 每条通道 (DLX 本地实例 / DeepL 官方免费API密钥) 由一个专属 worker 使用,
  各通道出口 IP 或账号互不相同, 上游限速额度相互独立。
- 每条通道维护自适应请求间隔: 遇 429 指数放大 (上限30s, 连续4次进入>=45s冷却),
  成功后缓慢回落 (x0.85), 在限速边缘自动寻找可持续速率。
- 429 响应若带 Retry-After 头则照办。
- 批次按 UTF-16 码元长度装箱 (默认上限1400; DeepL 匿名接口约1500即报413);
  官方API通道内部再按每请求最多45条分块 (官方接口单请求条数上限)。
- 非限速失败(503等)整批转入二分补翻, 隔离导致失败的"毒苹果"句子。
- 结果实时追加 jsonl 断点文件, 重跑只补缺口。
"""
import json
import os
import random
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .xuformat import has_japanese


class RateLimited(Exception):
    def __init__(self, retry_after=None):
        super().__init__('429')
        self.retry_after = retry_after  # 上游 Retry-After 头 (秒)


def utf16len(s):
    return len(s.encode('utf-16-le')) // 2


class Channel:
    """一条翻译通道: DLX 本地实例 (kind='dlx') 或 DeepL 官方免费API (kind='deepl_api')。

    interval 为该通道的请求间隔 (秒), 由 worker 根据 429/成功动态调节。
    """

    def __init__(self, url, kind='dlx', label='', key='', proxy=None,
                 min_interval=None, max_interval=None, start_interval=None):
        self.url = url
        self.kind = kind
        self.key = key
        self.proxy = proxy
        self.label = label or url
        if kind == 'deepl_api':
            # 官方API限速宽松, 节流档位远高于匿名网页接口
            self.min_interval = 0.3 if min_interval is None else min_interval
            self.max_interval = 15.0 if max_interval is None else max_interval
            self.interval = 0.6 if start_interval is None else start_interval
            self.backoff_floor = 2.0
            self.slow_at = 10.0
        else:
            self.min_interval = 0.8 if min_interval is None else min_interval
            self.max_interval = 30.0 if max_interval is None else max_interval
            self.interval = 1.5 if start_interval is None else start_interval
            self.backoff_floor = 3.0
            self.slow_at = 20.0
        self.requests = 0   # 发出的翻译请求次数 (逻辑请求)
        self.hits429 = 0    # 收到的 429 次数 (统计用, 不影响重试逻辑)
        self._opener = None

    def opener(self):
        if self._opener is None:
            handler = urllib.request.ProxyHandler(
                {'http': self.proxy, 'https': self.proxy} if self.proxy else {})
            self._opener = urllib.request.build_opener(handler)
        return self._opener

    def backoff(self, factor=2.2):
        self.interval = min(self.max_interval,
                            max(self.interval * factor, self.backoff_floor))

    def relax(self, decay=0.85):
        self.interval = max(self.min_interval, self.interval * decay)


def default_transform(key):
    """按句判定语言: 含日文按 JA, 否则按 EN。"""
    return key, ('JA' if has_japanese(key) else 'EN')


def make_transform(term_map):
    """构造术语表变换: 翻译前把已统一的名词替换成固定译名。"""
    if not term_map:
        return None
    pat = re.compile('|'.join(re.escape(t) for t in
                              sorted(term_map, key=len, reverse=True)))

    def transform(key):
        send = pat.sub(lambda m: term_map[m.group(0)], key)
        return send, ('JA' if has_japanese(key) else 'EN')
    return transform


_KATA_RE = re.compile(r'[\u30A0-\u30FF]{2,}')

# 语法词/代词/拟声词/多义常用词: 交给 DeepL 按上下文翻译, 不做术语统一
TERM_STOPWORDS = {
    'アレ', 'アタシ', 'アンタ', 'キミ', 'コト', 'トコ', 'ハズ', 'ワケ', 'ヤツ',
    'マジ', 'ダメ', 'ヘン', 'イイ', 'ゴメン', 'ホラ', 'モテ', 'ナメ', 'ヨシ',
    'ドキ', 'ギク', 'コク', 'ググ', 'ネタ', 'マタ', 'イマ', 'アシタ', 'キノウ',
    'ムリ', 'ヒマ', 'ジブン', 'ハンブン', 'チョウド', 'ダイジョウブ',
    # 多义外来语: 无上下文统一翻译易错 (ドラッグ=毒品/拖拽)
    'ドラッグ', 'サイト', 'キー', 'パン',
}


def mine_terms(keys, max_terms=300, min_count=2):
    """挖掘高频片假名名词 (人名/专有名词), 用于全文译名一致。"""
    from collections import Counter
    counter = Counter()
    for k in keys:
        for t in set(_KATA_RE.findall(k)):
            counter[t] += 1
    terms = [t for t, n in counter.most_common()
             if n >= min_count and t not in TERM_STOPWORDS
             and not re.fullmatch(r'[ー・]+', t)]
    for k in keys:  # 纯片假名短句 (如人名池条目) 即使只出现一次也纳入
        if (len(k) <= 20 and _KATA_RE.fullmatch(k) and k not in terms
                and k not in TERM_STOPWORDS and not re.fullmatch(r'[ー・]+', k)):
            terms.append(k)
    return terms[:max_terms]


def chunk_key(k, cap=1300):
    """把超长句在标点处切断, 每段不超过 cap 个 UTF-16 码元。"""
    parts, cur = [], ''
    for ch in k:
        cur += ch
        if utf16len(cur) >= cap and ch in '。！？…!?\n」』':
            parts.append(cur)
            cur = ''
    if cur:
        parts.append(cur)
    out = []
    for p in parts:
        while utf16len(p) > cap:
            out.append(p[:cap])
            p = p[cap:]
        if p:
            out.append(p)
    return out


def make_batches(items, cap):
    batches, cur, curlen = [], [], 0
    for it in items:
        l = utf16len(it)
        if cur and curlen + l > cap:
            batches.append(cur)
            cur, curlen = [], 0
        cur.append(it)
        curlen += l
    if cur:
        batches.append(cur)
    return batches


class BatchSource:
    def __init__(self, batches):
        self.batches = batches
        self.i = 0
        self.lock = threading.Lock()

    def next(self):
        with self.lock:
            if self.i >= len(self.batches):
                return None
            b = self.batches[self.i]
            self.i += 1
            return b


def _pack_items(items, cap):
    """把 (key, send, lang) 条目按语言分组并按 UTF-16 长度贪心装箱。"""
    batches = []
    for lang in sorted({it[2] for it in items}):
        cur, curlen = [], 0
        for it in items:
            if it[2] != lang:
                continue
            l = utf16len(it[1])
            if cur and curlen + l > cap:
                batches.append(cur)
                cur, curlen = [], 0
            cur.append(it)
            curlen += l
        if cur:
            batches.append(cur)
    return batches


class Engine:
    def __init__(self, endpoints, cache_dir, source_lang='JA', target_lang='ZH',
                 batch_cap=1400, log_fn=print, progress_fn=None, stop_event=None):
        """endpoints: engine.Channel 列表, 或纯 URL 字符串列表 (自动包装)。"""
        self.channels = [c if isinstance(c, Channel) else Channel(c)
                         for c in endpoints]
        if not self.channels:
            raise ValueError('没有可用翻译通道')
        self.cache_dir = cache_dir
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.batch_cap = batch_cap
        self.log = log_fn
        self.progress = progress_fn or (lambda done, total, elapsed: None)
        self.stop_event = stop_event or threading.Event()
        os.makedirs(cache_dir, exist_ok=True)
        self.map_path = os.path.join(cache_dir, 'zh_map.jsonl')
        self.keys_path = os.path.join(cache_dir, 'keys.json')
        self.perm_path = os.path.join(cache_dir, 'permanent_failures.json')
        self.map = {}
        self.reused = 0
        self._map_lock = threading.Lock()
        self._ch_lock = threading.Lock()
        self._ch_i = 0
        self.failed_batches = []
        self._fb_lock = threading.Lock()
        self._penalty_until = 0.0
        self._penalty_lock = threading.Lock()
        self.penalty_seconds = 120.0  # 全局熔断时长
        self.penalty_trips = 0        # 熔断触发次数 (按熔断窗口计)
        self.done = 0
        self.total = 0
        self.perm_failed = []
        self.t0 = None

    # ---------- 全局熔断: 上游进入惩罚窗口时全体通道暂停, 避免烧请求/误判失败 ----------
    def _trip_penalty(self, seconds=None):
        with self._penalty_lock:
            until = time.time() + (seconds or self.penalty_seconds)
            if self._penalty_until <= time.time():
                self.penalty_trips += 1  # 只统计新开的熔断窗口
            self._penalty_until = max(self._penalty_until, until)

    def _penalty_wait(self):
        while not self.stop_event.is_set():
            with self._penalty_lock:
                remain = self._penalty_until - time.time()
            if remain <= 0:
                return
            time.sleep(min(remain, 5.0))

    # ---------- 断点 ----------
    def load_checkpoint(self):
        if os.path.exists(self.map_path):
            with open(self.map_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        if o.get('k') and 'v' in o:
                            self.map[o['k']] = o['v'] or ''
                    except Exception:
                        pass
        self.reused = len(self.map)
        return self.reused

    def save_keys(self, keys):
        try:
            with open(self.keys_path, 'w', encoding='utf-8') as f:
                json.dump(keys, f, ensure_ascii=False)
        except OSError:
            pass

    # ---------- 请求 ----------
    def _call(self, ch, texts, lang=None, timeout=90):
        ch.requests += 1
        if ch.kind == 'deepl_api':
            return self._call_api(ch, texts, lang, timeout=timeout)
        body = json.dumps({'text': texts,
                           'source_lang': lang or self.source_lang,
                           'target_lang': self.target_lang}).encode('utf-8')
        req = urllib.request.Request(ch.url, data=body,
                                     headers={'Content-Type': 'application/json'})
        try:
            with ch.opener().open(req, timeout=timeout) as r:
                o = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ch.hits429 += 1
                raise RateLimited(_retry_after(e))
            raise RuntimeError('HTTP%d' % e.code)
        if o.get('code') == 200:
            data = o.get('data')
            if isinstance(data, list):
                return data
            raise RuntimeError('BAD_SHAPE')
        if o.get('code') == 429:
            ch.hits429 += 1
            raise RateLimited()
        raise RuntimeError(str(o)[:160])

    def _call_api(self, ch, texts, lang=None, timeout=90):
        """DeepL 官方免费API: 单请求最多50条, 这里按45分块保平安, 顺序拼接。"""
        out = []
        for i in range(0, len(texts), 45):
            part = texts[i:i + 45]
            body = json.dumps({'text': part,
                               'source_lang': lang or self.source_lang,
                               'target_lang': self.target_lang}).encode('utf-8')
            req = urllib.request.Request(
                ch.url, data=body,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'DeepL-Auth-Key ' + ch.key})
            try:
                with ch.opener().open(req, timeout=timeout) as r:
                    o = json.loads(r.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    ch.hits429 += 1
                    raise RateLimited(_retry_after(e))
                if e.code == 403:
                    raise RuntimeError('API密钥无效(403)')
                if e.code == 456:
                    raise RuntimeError('API额度已用尽(456), 换密钥或等下月')
                raise RuntimeError('API HTTP%d' % e.code)
            trans = o.get('translations')
            if not isinstance(trans, list) or len(trans) != len(part):
                raise RuntimeError('API返回形状异常: %s' % str(o)[:120])
            out.extend((t.get('text') or '') for t in trans)
        return out

    def _next_channel(self):
        with self._ch_lock:
            ch = self.channels[self._ch_i % len(self.channels)]
            self._ch_i += 1
            return ch

    def _commit(self, pairs):
        with self._map_lock:
            with open(self.map_path, 'a', encoding='utf-8') as f:
                for k, v in pairs:
                    if not v:
                        continue  # 空响应: 不记录, 下次重试
                    # 原样返回(不可译句, 如JSON配置碎片)记空串标记:
                    # 不写入游戏, 但占住断点, 重跑不再重复发送
                    val = v if v != k else ''
                    self.map[k] = val
                    f.write(json.dumps({'k': k, 'v': val}, ensure_ascii=False) + '\n')
            self.done += len(pairs)

    def _report(self):
        el = (time.time() - self.t0) if self.t0 else 0.0
        self.progress(self.done, self.total, el)

    # ---------- 阶段1: 每通道一个 worker, 各自节流 ----------
    def _worker(self, ch, src):
        consec = 0
        while not self.stop_event.is_set():
            batch = src.next()
            if batch is None:
                return
            texts = [it[1] for it in batch]
            lang = batch[0][2]
            data = None
            attempts = 0
            while attempts < 7 and not self.stop_event.is_set():
                self._penalty_wait()
                try:
                    data = self._call(ch, texts, lang=lang)
                    break
                except RateLimited as e:
                    consec += 1
                    ch.backoff()
                    if ch.interval >= ch.slow_at:
                        self._trip_penalty()
                        self.log('通道[%s] 持续限速, 全体通道暂停约%d秒后自动重试'
                                 % (ch.label, int(self.penalty_seconds)), 'warn')
                    wait = e.retry_after if (e.retry_after or 0) > 0 else (
                        ch.interval if consec < 4 else max(45.0, ch.interval))
                    self.log('通道[%s] 429限速, 暂停 %.0fs (连续%d次, 请求间隔已调至%.1fs)'
                             % (ch.label, wait, consec, ch.interval), 'warn')
                    time.sleep(wait)
                    # 限速不消耗重试次数: 可翻译句子不会因此被判永久失败
                except Exception as e:
                    attempts += 1
                    self.log('通道[%s] 请求异常: %s' % (ch.label, str(e)[:120]), 'warn')
                    time.sleep(5)
            if data is not None and len(data) == len(batch):
                consec = 0
                ch.relax()
                self._commit(list(zip([it[0] for it in batch], data)))
            else:
                with self._fb_lock:
                    self.failed_batches.append(batch)
                if data is not None:
                    self.log('批次结果数不匹配(%d/%d), 转入二分补翻'
                             % (len(data), len(batch)), 'warn')
            self._report()
            time.sleep(ch.interval * (0.75 + 0.5 * random.random()))

    # ---------- 阶段3: 二分补翻 ----------
    def _bisect(self, batch):
        if len(batch) == 1:
            self.log('永久失败: %s' % batch[0][0][:50], 'err')
            return list(batch)
        tries = 0
        limited = 0
        while tries < 6 and limited < 40 and not self.stop_event.is_set():
            self._penalty_wait()
            ch = self._next_channel()
            try:
                data = self._call(ch, [it[1] for it in batch], lang=batch[0][2])
                if len(data) == len(batch):
                    self._commit(list(zip([it[0] for it in batch], data)))
                    return []
                raise RuntimeError('MISMATCH')
            except RateLimited as e:
                limited += 1
                ch.backoff()
                if ch.interval >= ch.slow_at:
                    self._trip_penalty()
                time.sleep(e.retry_after if (e.retry_after or 0) > 0
                           else min(60.0, 3.0 * (2 ** min(limited, 5))))
            except Exception:
                break
        mid = len(batch) // 2
        return self._bisect(batch[:mid]) + self._bisect(batch[mid:])

    # ---------- 主入口 ----------
    def translate(self, keys, transform=None):
        """翻译 keys 中 self.map 尚不存在的句子, 返回统计 dict。

        transform(key) -> (发送文本, 源语言)，缺省按句自动判定 JA/EN。
        """
        self.t0 = time.time()
        pending = [k for k in keys if k not in self.map and k.strip()
                   and utf16len(k) <= 20000]
        self.total = len(pending)
        self.done = 0
        self.log('待翻译 %d 句 (断点跳过 %d 句)' % (len(pending), len(keys) - len(pending)))
        if not pending:
            return self._finish()
        if transform is None:
            transform = default_transform
        items = []
        for k in pending:
            send, lang = transform(k)
            if send:
                items.append((k, send, lang))
        shorts = [it for it in items if utf16len(it[1]) <= 1300]
        longs = [it for it in items if utf16len(it[1]) > 1300]
        batches = _pack_items(shorts, self.batch_cap)
        langs = sorted({it[2] for it in items})
        self.log('批次 %d 个 | 超长句 %d 条 | 语言 %s | 通道 %d 个 (%s)'
                 % (len(batches), len(longs), '/'.join(langs), len(self.channels),
                    ' ; '.join(c.label for c in self.channels)))
        src = BatchSource(batches)
        threads = [threading.Thread(target=self._worker, args=(ch, src), daemon=True)
                   for ch in self.channels]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for it in longs:  # 超长句串行
            if self.stop_event.is_set():
                break
            k, send, lang = it
            data = None
            for attempt in range(7):
                if self.stop_event.is_set():
                    break
                self._penalty_wait()
                ch = self._next_channel()
                try:
                    data = self._call(ch, chunk_key(send), lang=lang)
                    break
                except RateLimited as e:
                    ch.backoff()
                    if ch.interval >= ch.slow_at:
                        self._trip_penalty()
                    time.sleep(e.retry_after if (e.retry_after or 0) > 0
                               else min(60.0, 2.0 * (2 ** attempt)))
                except Exception:
                    time.sleep(5)
            if data:
                self._commit([(k, ''.join(data))])
            else:
                self.perm_failed.append(k)
        while not self.stop_event.is_set():
            with self._fb_lock:
                todo = self.failed_batches
                self.failed_batches = []
            if not todo:
                break
            self.log('二分补翻 %d 个失败批次...' % len(todo), 'warn')
            for b in todo:
                if self.stop_event.is_set():
                    break
                self.perm_failed += self._bisect(b)
        try:
            with open(self.perm_path, 'w', encoding='utf-8') as f:
                json.dump(self.perm_failed, f, ensure_ascii=False)
        except OSError:
            pass
        return self._finish()

    def _finish(self):
        el = (time.time() - self.t0) if self.t0 else 0.0
        rate = self.done / el if el > 0 else 0.0
        st = {'done_now': self.done, 'total': self.total, 'elapsed': el,
              'rate': rate, 'perm_failed': len(self.perm_failed),
              'reused': self.reused, 'stopped': self.stop_event.is_set(),
              'hits429': sum(c.hits429 for c in self.channels),
              'requests': sum(c.requests for c in self.channels),
              'penalty_trips': self.penalty_trips,
              'channels': [{'label': c.label, 'kind': c.kind,
                            'requests': c.requests, 'hits429': c.hits429,
                            'interval': round(c.interval, 2)}
                           for c in self.channels]}
        self.log('翻译阶段结束: 本次完成 %d | 永久失败 %d | 用时 %.0fs (%.1f句/秒)'
                 % (self.done, len(self.perm_failed), el, rate))
        if st['hits429']:
            self.log('限速统计: 请求 %d 次, 遇 429 共 %d 次, 全局熔断 %d 次 (%s)'
                     % (st['requests'], st['hits429'], st['penalty_trips'],
                        ' ; '.join('%s: %d次' % (c['label'], c['hits429'])
                                   for c in st['channels'] if c['hits429'])))
        return st


def _retry_after(e):
    """从 HTTPError 提取 Retry-After 头 (秒), 无效返回 None。"""
    try:
        v = e.headers.get('Retry-After') if e.headers else None
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def load_glossary_file(path, log_fn=print):
    """加载用户自定义术语表 (XUAT 格式: 术语=译名 每行一条, // 为注释)。"""
    from .xuformat import split_pair_simple
    out = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line.strip() or line.startswith('//'):
                    continue
                sp = split_pair_simple(line)
                if sp and sp[0] and sp[1]:
                    out[sp[0]] = sp[1]
    except OSError as e:
        log_fn('术语表读取失败: %s' % e, 'warn')
    if out:
        log_fn('自定义术语表: %d 条' % len(out))
    return out


def translate_glossary(terms, endpoints, source_lang='JA', target_lang='ZH',
                       log_fn=print, stop_event=None):
    """把术语统一翻译一次, 返回 {术语: 固定译名}, 保证全文译名一致。"""
    if not terms or not endpoints:
        return {}
    eng = Engine(endpoints, os.path.join(tempfile.gettempdir(), 'pt_glossary'),
                 source_lang=source_lang, target_lang=target_lang,
                 log_fn=log_fn, stop_event=stop_event or threading.Event())
    tmap = {}
    pending = list(dict.fromkeys(terms))
    rounds = 0
    while pending and rounds < 6:
        rounds += 1
        failed = []
        for b in make_batches(pending, 1300):
            data = None
            for attempt in range(6):
                if eng.stop_event.is_set():
                    return tmap
                eng._penalty_wait()
                ch = eng._next_channel()
                try:
                    data = eng._call(ch, b)
                    break
                except RateLimited as e:
                    ch.backoff()
                    if ch.interval >= ch.slow_at:
                        eng._trip_penalty()
                    time.sleep(e.retry_after if (e.retry_after or 0) > 0
                               else min(30.0, 1.5 * (2 ** attempt)))
                except Exception as e:
                    log_fn('术语翻译异常: %s' % str(e)[:100], 'warn')
                    time.sleep(4)
            if data and len(data) == len(b):
                for t, v in zip(b, data):
                    if v and v != t:
                        tmap[t] = v
            else:
                failed.extend(b)
        pending = failed
        if pending:
            log_fn('术语批次重试 %d 个' % len(pending), 'warn')
            time.sleep(2)
    log_fn('术语表: %d 个名词已统一译名' % len(tmap))
    return tmap
