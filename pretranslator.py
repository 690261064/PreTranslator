# -*- coding: utf-8 -*-
"""Unity 游戏预翻译工具 (XUnity.AutoTranslator + DLX/DeepLX)。

在游戏运行前批量完成翻译, 生成 XUAT 本地缓存:
  Translation/<目标语言>/Text/PreTranslations.txt
  Translation/<目标语言>/RedirectedResources/<原结构>/...
游戏启动时 XUAT 直接查表加载, 全程离线。

用法:
  python pretranslator.py                    # 图形界面
  python pretranslator.py --nogui scan       # 列出检测到的游戏
  python pretranslator.py --nogui check      # 检查直连/代理/DLX 状态
  python pretranslator.py --nogui translate --game <游戏目录>
          [--extra-source DIR]... [--limit N] [--dry-run]
"""
import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

from pt_core import config, dlx, engine, extract, fontcover, fontpatch, \
    gamestrings, games, netcheck, runlog, writer
from pt_core import xuformat
from pt_core.xuformat import has_japanese


def _cli_log(msg, lv='info'):
    print('[%s] %s' % (lv, msg), flush=True)


def _charset_from_translation(game, target_lang='zh-CN'):
    """收集目标语言目录里已生成译文用到的全部字符 (供字体烘焙定字符集)。"""
    charset = set()
    base = game.translation_dir
    root = os.path.join(base, target_lang)
    if not os.path.isdir(root):
        return charset
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith('.txt'):
                continue
            try:
                raw = open(os.path.join(dirpath, fn), 'rb').read()
            except OSError:
                continue
            for line in raw.decode('utf-8', 'replace').splitlines():
                sp = xuformat.split_line_raw(line)
                if sp is None:
                    continue
                charset.update(xuformat.decode(sp[1]))
    return charset


def _split_multi(s):
    """逗号/分号/换行分隔的多值配置项 (代理列表 / API密钥列表)。"""
    return [x.strip() for x in re.split(r'[,;\r\n]+', s or '') if x.strip()]


def build_channels(cfg, log_fn=_cli_log):
    """构建翻译通道, 返回 engine.Channel 列表。

    通道构成 (每条通道出口IP或账号互不相同, 分散 DeepL 上游限速):
      1. DeepL 官方免费API密钥通道 (deepl_keys, 每号每月50万字符起、实测有100万, 限速最宽松)
      2. 直连 DLX 实例
      3. 每个代理地址各一条 DLX 实例 (proxy_url 支持逗号分隔多个)
    各通道端口严格隔离: 直连=dlx_port, 代理依次 dlx_proxy_port, dlx_proxy_port+1…
    即使某通道健康检查偶发失败也不会抢占相邻通道的实例。
    """
    channels = []
    proxies = _split_multi(cfg.get('proxy_url'))
    keys = _split_multi(cfg.get('deepl_keys'))
    proxy0 = proxies[0] if proxies else None
    for i, key in enumerate(keys):
        ok, d = netcheck.check_deepl_key(key, proxy=proxy0)
        if ok:
            log_fn('DeepL 官方API 密钥#%d: %s' % (i + 1, d), 'ok')
            channels.append(engine.Channel(
                netcheck.DEEPL_API + '/translate', kind='deepl_api', key=key,
                proxy=proxy0, label='官方API#%d' % (i + 1)))
        else:
            log_fn('DeepL 官方API 密钥#%d: 不可用 (%s)' % (i + 1, d), 'warn')
    exe = dlx.find_exe(cfg.get('dlx_path'))
    if not exe:
        if not channels:
            log_fn('未找到 DLX 程序 (deeplx-fixed.exe)。可将其放到工具目录, '
                   '或在配置中设置 dlx_path。', 'err')
        return channels

    used_ports = set()

    def next_port(base):
        p = int(base)
        while p in used_ports:
            p += 1
        used_ports.add(p)
        return p

    ok_d, d_d = netcheck.check_deepl(None, timeout=6)
    log_fn('直连 DeepL: %s (%s)' % ('可用' if ok_d else '不可用', d_d))
    if ok_d:
        inst = dlx.ensure_dlx(exe, next_port(cfg['dlx_port']), proxy=None,
                              strict=True, log_fn=log_fn)
        if inst:
            channels.append(engine.Channel(inst.url, label=inst.describe))
    for px in proxies:
        ok_p, d_p = netcheck.check_deepl(px, timeout=8)
        log_fn('代理 %s -> DeepL: %s (%s)' % (px, '可用' if ok_p else '不可用', d_p))
        if ok_p:
            inst = dlx.ensure_dlx(exe, next_port(cfg['dlx_proxy_port']), proxy=px,
                                  strict=True, log_fn=log_fn)
            if inst:
                channels.append(engine.Channel(inst.url, label=inst.describe))
    if not channels:
        log_fn('没有可用翻译通道 (DLX 未运行且上游不可达)', 'err')
    else:
        log_fn('翻译通道 %d 个: %s' % (len(channels),
                                     ' ; '.join(c.label for c in channels)))
    return channels


def build_combo_pairs(units, valmap, max_chars, max_lines=5):
    """多行整串的连续行组合键。

    XUAT 运行时对短文本整串查表, 而游戏显示元素可能是多行原文里的任意连续
    行段 (单行/两行/三行…)。行级译文已存在时, 组合键译文可直接拼接合成,
    不耗翻译额度。返回 {组合键: 组合译文}。
    """
    out = {}
    for u in units:
        parts = re.split(r'(\r\n|\r|\n)', u)
        lines = parts[0::2]
        seps = parts[1::2]
        n = len(lines)
        for size in range(2, max_lines + 1):
            for i in range(0, n - size + 1):
                seg = lines[i:i + size]
                if not any(x.strip() for x in seg):
                    continue
                key = seg[0]
                val = '' if not seg[0].strip() else valmap.get(seg[0], '')
                ok = bool(val) or not seg[0].strip()
                for j in range(1, size):
                    sep = seps[i + j - 1]
                    key += sep + seg[j]
                    if seg[j].strip():
                        v2 = valmap.get(seg[j], '')
                        if not v2:
                            ok = False
                        val += sep + v2
                    else:
                        val += sep
                if ok and val and len(key) <= max_chars and key not in valmap:
                    out[key] = val
    return out


def run_pipeline(cfg, game, log_fn=_cli_log, extra_sources=None, limit=0,
                 dry_run=False, stop_event=None, progress_fn=None, redo=False):
    """完整流水线: 提取原文 -> 修配置 -> DLX 翻译(术语一致) -> 写入游戏 -> 往返校验。"""
    if game.kind == 'unity':
        hint = ('该游戏尚未安装 XUnity.AutoTranslator (裸 Unity 游戏%s)。'
                % ('/IL2CPP' if game.il2cpp else ''))
        if game.il2cpp:
            hint += ' IL2CPP 游戏需手动安装 BepInEx6-IL2CPP + XUAT-IL2CPP 版, 暂不支持自动安装。'
        else:
            hint += (' 请先点「安装XUAT(独立版)」把 SetupReiPatcherAndAutoTranslator.exe 复制到'
                     '游戏目录并运行一次, 再重新扫描。')
        raise RuntimeError(hint)
    all_extras = list((cfg.get('games', {}).get(game.root, {}) or {}).get('extra_sources') or [])
    for p in extra_sources or []:
        if p not in all_extras:
            all_extras.append(p)
    sources = extract.collect_sources(game.translation_dir, cfg['target_lang'], all_extras)
    # XUAT 对超过 MaxCharactersPerTranslation 的文本直接忽略; 工具把配置抬到
    # 至少 1000, 让较长的多行对话块也能整串命中, 提取/组合键按同一上限对齐
    max_chars = max(games.read_xuat_max_chars(game), 1000)
    files, keys, text_keys = {}, [], []
    bkeys, bunits = [], []
    if sources:
        for label, p in sources:
            log_fn('来源[%s]: %s' % (label, p))
        files, keys, _stats, text_keys = extract.extract(sources, log_fn=log_fn)
    if not keys:
        log_fn('翻译包来源未提供原文, 改为直接从游戏文件提取 (程序集字符串 + 资源扫描)',
               'warn')
        bkeys, bunits = gamestrings.extract_from_game(
            game, cap=cfg.get('binary_extract_cap', 20000), log_fn=log_fn,
            max_chars=max_chars)
        if not bkeys:
            raise RuntimeError('翻译包与游戏文件中均未提取到可翻译文本, 无法预翻译')
        text_keys = list(bkeys)
        keys = list(bkeys)
        log_fn('游戏文件提取到 %d 句 (含多行整串 %d; 启发式, 个别杂质可后期手删)'
               % (len(bkeys), len(bunits)))
    elif sources and keys and game.kind == 'standalone' and cfg.get(
            'binary_supplement_standalone', True):
        # 独立版游戏常只有少量UI翻译缓存, 剧情文本在资源里, 追加游戏文件直提
        have = set(keys) | set(text_keys)
        bkeys, bunits = gamestrings.extract_from_game(
            game, cap=cfg.get('binary_extract_cap', 20000), log_fn=log_fn,
            max_chars=max_chars)
        bkeys = [k for k in bkeys if k not in have]
        if bkeys:
            log_fn('独立版补充提取: 游戏文件中新增 %d 句 (含多行整串 %d)'
                   % (len(bkeys), len(bunits)))
            text_keys = list(text_keys) + bkeys
            keys = list(keys) + bkeys
    # 语言识别: 按句自动判定 JA/EN, 源语言取多数
    ja_n = sum(1 for k in keys if has_japanese(k))
    en_n = len(keys) - ja_n
    from_lang = 'ja' if ja_n >= en_n else 'en'
    log_fn('语言识别: 日文 %d 句 | 英文 %d 句 (每句自动判定源语言)' % (ja_n, en_n))
    game.fix_config(cfg['target_lang'], from_lang=from_lang,
                    override_font=cfg.get('override_font'), log_fn=log_fn,
                    max_chars=max_chars)
    # 字体缺字根治 (静态 SDF 烘焙) 放在翻译完成之后做, 见下方 write_all 之前 ——
    # 必须拿"译文实际用到的字符"当字符集, 早期版本用日文原文当字符集是错的:
    # 日文字形原图集本来就有, 而简体专用字 (获/游/选…) 一个都没烘进去。
    font_fallback = cfg.get('font_safe_substitute', True)
    font_cmap = None
    fp = None
    insts = build_channels(cfg, log_fn)
    if not insts:
        raise RuntimeError('没有可用翻译通道 (DLX 未运行且上游不可达)')
    cdir = games.cache_dir_for(game, TOOL_DIR)
    eng = engine.Engine(insts, cdir,
                        source_lang=cfg['dlx_source_lang'],
                        target_lang=cfg['dlx_target_lang'],
                        batch_cap=cfg['batch_cap'], log_fn=log_fn,
                        progress_fn=progress_fn,
                        stop_event=stop_event or threading.Event())
    if redo:
        log_fn('强制重翻: 忽略已有翻译与断点, 全部重新生成', 'warn')
    else:
        n_ckpt = eng.load_checkpoint()
        if n_ckpt:
            log_fn('载入断点 %d 条 (上次进度)' % n_ckpt)
    target = game.target_dir(cfg['target_lang'])
    existing = {} if redo else extract.load_existing_translations(target)
    if not redo:
        log_fn('游戏内已有 %s 翻译 %d 条 (将保留并跳过)' % (cfg['target_lang'], len(existing)))
    need = [k for k in keys if k not in eng.map and k not in existing]
    if limit and len(need) > limit:
        need = need[:limit]
        log_fn('--limit 生效: 本次只翻译 %d 句' % limit)
    # 术语表: 高频片假名名词统一译名, 送翻前替换, 保证名词一致
    # 自定义术语表 (glossary_file) 优先级最高, 机器挖掘的次之
    transform = None
    if cfg.get('enable_glossary', True) and need:
        user_terms = engine.load_glossary_file(cfg.get('glossary_file'), log_fn=log_fn)
        terms = engine.mine_terms(need, max_terms=cfg.get('glossary_max_terms', 300))
        auto_tmap = {}
        if terms:
            log_fn('挖掘到 %d 个高频片假名名词, 先统一翻译术语表...' % len(terms))
            auto_tmap = engine.translate_glossary(
                terms, insts,
                source_lang=cfg['dlx_source_lang'],
                target_lang=cfg['dlx_target_lang'],
                log_fn=log_fn, stop_event=stop_event)
        tmap = dict(auto_tmap)
        tmap.update(user_terms)  # 自定义覆盖机器译名
        if tmap:
            for t, v in sorted(tmap.items()):
                log_fn('  术语 %s = %s' % (t, v))
            transform = engine.make_transform(tmap)
    st = eng.translate(need, transform=transform)
    if stop_event is not None and stop_event.is_set():
        log_fn('已停止, 断点已保存, 未写入游戏目录', 'warn')
        return {'keys': len(keys), 'existing': len(existing), 'engine': st,
                'stopped': True}
    if dry_run:
        log_fn('--dry-run: 跳过写入游戏目录')
        return {'keys': len(keys), 'existing': len(existing), 'engine': st}
    merged = dict(existing)
    merged.update(eng.map)
    # 多行整串的连续行组合键: 行译文拼接合成, 不耗额度
    if bunits:
        combos = build_combo_pairs(bunits, merged, max_chars)
        n_new = 0
        for k, v in combos.items():
            if k not in merged:
                merged[k] = v
                n_new += 1
        if n_new:
            log_fn('多行组合键: 合成 %d 条 (覆盖任意连续行段的显示元素)' % n_new)
            text_keys = list(text_keys) + [k for k in combos if k in merged]
    # 字体缺字根治: TMP 静态图集游戏把中文缺字直接烘焙进图集 (静态 SDF),
    # 游戏保持 Static 模式, 运行时永不触发原生加字 —— 那条路在 Unity 2022 +
    # 本类 TMP 资产上会 0xC0000005。字符集取自"原文 + 译文", 失败时退回
    # "缺字替换为日式写法"的保底方案。
    if cfg.get('font_tmp_patch', True) and (keys or merged):
        try:
            fp = fontpatch.patch_game_font(
                game, log_fn=log_fn,
                charset=set(''.join(keys) + ''.join(merged.values())),
                mode=cfg.get('font_patch_mode', 'bake'),
                font_file=cfg.get('font_bake_file') or None)
            if fp.get('patched'):
                font_cmap = fp.get('cmap') or None
                if font_cmap:
                    font_fallback = False
        except Exception as e:
            log_fn('字体补丁异常: %s' % e, 'warn')
    # 字体缺字兜底: 有补丁字体 cmap 时按字体覆盖替换个别符号缺字;
    # 补丁不可用时, 把译文缺字替换为原文覆盖的日式写法
    vtransform = None
    covered = None
    if font_cmap:
        # patch_game_font 的 cmap 是整数码点集合, 转成字符集合再参与替换/报告
        covered = fontcover.chars_from_cmap(font_cmap)
        covered.update(chr(c) for c in range(0x20, 0x7F))
        vtransform = fontcover.make_transform(covered)
        log_fn('字体检查: 译文按补丁字体 (%s) 覆盖范围自动替换缺字符号'
               % fp.get('font_name'))
    elif font_fallback and fontcover.has_tmp(game):
        covered = fontcover.build_coverage(
            [k for k in keys if has_japanese(k)] + [k for k in merged if has_japanese(k)])
        vtransform = fontcover.make_transform(covered)
        log_fn('字体检查: 按原文覆盖范围替换译文缺字 (保底方案)')
    w = writer.write_all(target, files, merged, log_fn=log_fn, text_keys=text_keys,
                         keep_existing=not redo, value_transform=vtransform)
    v = writer.verify(target, merged, log_fn=log_fn, value_transform=vtransform)
    if covered is not None:
        n_sub = 0
        sub_vals = []
        for v0 in merged.values():
            nv, k = fontcover.substitute_text(v0, covered)
            n_sub += k
            sub_vals.append(nv)
        if n_sub:
            log_fn('字体替换: %d 个字符已替换为日式写法' % n_sub)
        miss = fontcover.report(sub_vals, covered, log_fn=log_fn)
        if miss:
            try:
                with open(os.path.join(cdir, 'font_missing.txt'), 'w',
                          encoding='utf-8') as f:
                    f.write('译文缺字 (游戏字体不含, 显示为方框): %d 种\n%s\n'
                            % (len(miss), ''.join(sorted(miss))))
            except OSError:
                pass
    # 结构化字体结果交给上层展示 (去掉大体积的字体二进制)
    fp_res = None
    if fp is not None:
        fp_res = dict(fp)
        fp_res.pop('font_bytes', None)
    # 官方API当月用量 (跑完刷新一次, 供界面/报告显示剩余额度)
    quota = None
    qkeys = _split_multi(cfg.get('deepl_keys'))
    if qkeys:
        qpx = _split_multi(cfg.get('proxy_url'))
        quota = netcheck.get_usage(qkeys[0], proxy=qpx[0] if qpx else None, timeout=8)
    # 运行报告: 一行 JSON 追加到工具目录 run_report.jsonl, 长期复盘用
    entry = runlog.build_entry(game, {'engine': st, 'write': w, 'verify': v,
                                      'font': fp_res},
                               lang=cfg['target_lang'], quota=quota)
    runlog.append_report(TOOL_DIR, entry, log_fn=log_fn)
    return {'keys': len(keys), 'existing': len(existing), 'engine': st,
            'write': w, 'verify': v, 'font': fp_res, 'quota': quota,
            'report': entry}


def bundle_dlx(log_fn=_cli_log):
    """把修复版 DLX 与 XUAT 安装器复制到工具目录 (find_exe 优先用工具目录内副本)。"""
    dst = os.path.join(TOOL_DIR, 'deeplx-fixed.exe')
    if not os.path.isfile(dst):
        for cand in dlx.DEFAULT_EXE_CANDIDATES:
            p = cand if os.path.isabs(cand) else os.path.join(TOOL_DIR, cand)
            if p and os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(dst):
                try:
                    shutil.copy2(p, dst)
                    log_fn('已内置 DLX: %s' % dst, 'ok')
                    break
                except OSError as e:
                    log_fn('内置 DLX 失败: %s' % e, 'warn')
    setup_dst = os.path.join(TOOL_DIR, 'SetupReiPatcherAndAutoTranslator.exe')
    if not os.path.isfile(setup_dst):
        # 仓库不含安装器时, 可在 config.json 的 xuat_setup_exe 指定本机路径
        for cand in (config.load().get('xuat_setup_exe') or '',):
            if cand and os.path.isfile(cand) and os.path.abspath(cand) != os.path.abspath(setup_dst):
                try:
                    shutil.copy2(cand, setup_dst)
                    log_fn('已内置 XUAT 安装器: %s' % setup_dst, 'ok')
                except OSError as e:
                    log_fn('内置 XUAT 安装器失败: %s' % e, 'warn')
    return dst if os.path.isfile(dst) else None


def make_share_zip(log_fn=_cli_log):
    """生成脱密分享包: 工具代码 + 内置 DLX/XUAT安装器 + 全新默认配置; 不含用户数据。"""
    bundle_dlx(log_fn)
    stamp = time.strftime('%Y%m%d')
    out = os.path.join(TOOL_DIR, 'PreTranslator-share-%s.zip' % stamp)
    include_files = ['pretranslator.py', 'pt_gui.py', 'selftest.py', 'batch_run.py',
                     'README.md', '启动预翻译工具.bat', 'deeplx-fixed.exe',
                     'SetupReiPatcherAndAutoTranslator.exe']
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fn in include_files:
            p = os.path.join(TOOL_DIR, fn)
            if os.path.isfile(p):
                zf.write(p, fn)
            else:
                log_fn('分享包缺少: %s' % fn, 'warn')
        for dp, dns, fns in os.walk(os.path.join(TOOL_DIR, 'pt_core')):
            dns[:] = [d for d in dns if d != '__pycache__']
            for fn in fns:
                if fn.endswith('.py'):
                    full = os.path.join(dp, fn)
                    zf.write(full, os.path.relpath(full, TOOL_DIR))
        for dp, dns, fns in os.walk(os.path.join(TOOL_DIR, 'docs')):
            for fn in sorted(fns):
                if fn.endswith('.md'):
                    full = os.path.join(dp, fn)
                    zf.write(full, os.path.relpath(full, TOOL_DIR))
        clean = dict(config.DEFAULTS)
        clean['games'] = {}
        zf.writestr('config.json', json.dumps(clean, ensure_ascii=False, indent=2))
        for note in (os.path.join(TOOL_DIR, 'DLX修复说明.md'),):
            if os.path.isfile(note):
                zf.write(note, 'DLX修复说明.md')
                break
        zf.writestr('首次使用.txt', (
            'PreTranslator 使用步骤\r\n'
            '---------------------\r\n'
            '1. 解压到任意文件夹 (路径建议不含特殊字符)\r\n'
            '2. 双击「启动预翻译工具.bat」 (需要电脑装有 Python 3.8+)\r\n'
            '3. 工具自动启动内置 DLX 翻译服务并检测网络/代理\r\n'
            '4. 点「重新扫描」找到游戏, 选中后点「▶ 开始预翻译」\r\n'
            '5. 裸 Unity 游戏先点「安装XUAT(独立版)」, 按提示操作后重新扫描\r\n'
            '6. 自带中文的游戏: 选中后点「✕ 移除所选(不再扫描)」\r\n'
            '7. 界面里的「使用说明…」有翻译通道/术语表/常见问题等详细经验\r\n'
            '\r\n'
            '更多说明见 README.md。如译名不满意, 在 config.json 的 glossary_file\r\n'
            '指向的文本文件里写「日文原文=固定译名」每行一条, 重新强制重翻即可。\r\n'))
    log_fn('分享包已生成: %s' % out, 'ok')
    return out


def cmd_check(cfg):
    _cli_log('== 网络检测 ==')
    ok, d = netcheck.check_deepl(None)
    _cli_log('直连 DeepL: %s (%s)' % ('OK' if ok else 'FAIL', d))
    ip = netcheck.exit_ip(None)
    _cli_log('直连出口 IP: %s' % (ip or '获取失败'))
    proxies = _split_multi(cfg['proxy_url'])
    for px in proxies:
        ok, d = netcheck.check_deepl(px)
        ip = netcheck.exit_ip(px)
        _cli_log('代理 -> DeepL (%s): %s (%s) | 出口IP: %s'
                 % (px, 'OK' if ok else 'FAIL', d, ip or '获取失败'))
    _cli_log('== DeepL 官方API密钥 ==')
    keys = _split_multi(cfg.get('deepl_keys'))
    if not keys:
        _cli_log('未配置 (可选: 到 deepl.com 免费申请 API 密钥, 每号每月50万字符起'
                 ' (实测新注册有100万, 以 /usage 接口为准),'
                 ' 填入 config.json 的 deepl_keys, 逗号分隔可多个)')
    for i, key in enumerate(keys):
        ok, d = netcheck.check_deepl_key(key, proxy=proxies[0] if proxies else None)
        _cli_log('密钥#%d: %s (%s)' % (i + 1, 'OK' if ok else 'FAIL', d))
    _cli_log('== DLX 检测 ==')
    exe = dlx.find_exe(cfg.get('dlx_path'))
    _cli_log('DLX 程序: %s' % (exe or '未找到 (候选: %s)' % ', '.join(dlx.DEFAULT_EXE_CANDIDATES)))
    if exe:
        for tag, port in (('直连', cfg['dlx_port']), ('代理通道', cfg['dlx_proxy_port'])):
            url = 'http://127.0.0.1:%d/translate' % port
            ok, d = netcheck.dlx_translate(url)
            _cli_log('DLX %s %s: %s (%s)' % (tag, url, 'OK' if ok else 'FAIL', d))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-g', '--gui'):
        import pt_gui
        pt_gui.run()
        return 0
    if argv[0] != '--nogui':
        print(__doc__)
        return 2
    argv = argv[1:]
    p = argparse.ArgumentParser(prog='pretranslator --nogui')
    sub = p.add_subparsers(dest='cmd', required=True)
    scan_p = sub.add_parser('scan', help='扫描本机游戏')
    scan_p.add_argument('--root', help='只扫描该目录')
    sub.add_parser('check', help='检测网络/代理/DLX')
    sub.add_parser('share', help='生成脱密分享包 (内置DLX+全新配置)')
    fp_p = sub.add_parser('fontpatch', help='只打中文字体缺字补丁 (不翻译)')
    fp_p.add_argument('--game', required=True, help='游戏根目录')
    fp_p.add_argument('--font', default='',
                      help='指定字体 ttf/ttc/otf, 留空=自动挑选 (雅黑粗体优先)')
    fp_p.add_argument('--mode', default='', choices=['', 'bake', 'dynamic'],
                      help='bake=静态图集烘焙(默认/推荐); dynamic=改动态图集(仅诊断)')
    fp_p.add_argument('--dry-run', action='store_true',
                      help='只统计缺字, 不改任何游戏文件')
    tr = sub.add_parser('translate', help='无界面执行完整预翻译流水线')
    tr.add_argument('--game', required=True, help='游戏根目录 (含 BepInEx)')
    tr.add_argument('--extra-source', action='append', default=[],
                    help='外部翻译来源目录 (XUAT 结构), 可多次')
    tr.add_argument('--limit', type=int, default=0, help='限制本次翻译句数 (测试)')
    tr.add_argument('--dry-run', action='store_true', help='不写入游戏目录')
    tr.add_argument('--redo', action='store_true',
                    help='强制重翻: 忽略已有翻译与断点, 全部重新生成')
    a = p.parse_args(argv)
    cfg = config.load()
    if a.cmd == 'scan':
        roots = [a.root] if a.root else games.default_roots(cfg.get('scan_roots'))
        _cli_log('扫描根: %s' % ' ; '.join(roots))
        gs = games.scan(roots, log_fn=_cli_log)
        if not gs:
            _cli_log('未发现带 BepInEx + XUAT 的游戏')
        for g in gs:
            print('%s\t%s\t%s' % (g.name, g.root, 'XUAT✓' if g.has_xuat else 'XUAT✗'))
        return 0
    if a.cmd == 'check':
        return cmd_check(cfg)
    if a.cmd == 'share':
        p = make_share_zip()
        print(p)
        return 0
    if a.cmd == 'fontpatch':
        g = games.detect_at(a.game)
        if not g:
            _cli_log('不是有效的游戏目录 (未找到 XUAT/BepInEx): %s' % a.game, 'err')
            return 2
        charset = _charset_from_translation(g, cfg.get('target_lang', 'zh-CN'))
        if not charset:
            _cli_log('目标语言目录里还没有译文, 字符集取不到; '
                     '先跑一次预翻译, 或用 --font 配合 GUI 操作', 'warn')
            charset = None
        else:
            _cli_log('字符集: %d 字 (来自已生成译文)' % len(charset))
        try:
            r = fontpatch.patch_game_font(
                g, log_fn=_cli_log, charset=charset,
                mode=a.mode or cfg.get('font_patch_mode', 'bake'),
                font_file=a.font or cfg.get('font_bake_file') or None,
                dry=a.dry_run)
        except Exception as e:
            _cli_log('失败: %s' % e, 'err')
            traceback.print_exc()
            return 1
        _cli_log('结果: mode=%s patched=%d baked=%d 字体=%s'
                 % (r.get('mode'), r.get('patched', 0), r.get('baked', 0),
                    r.get('font_name')))
        if a.dry_run:
            return 0
        return 0 if r.get('patched') else 1
    if a.cmd == 'translate':
        g = games.detect_at(a.game)
        if not g:
            _cli_log('不是有效的游戏目录 (未找到 BepInEx): %s' % a.game, 'err')
            return 2
        _cli_log('游戏: %s (%s)' % (g.name, g.root))
        try:
            s = run_pipeline(cfg, g, extra_sources=a.extra_source,
                             limit=a.limit, dry_run=a.dry_run, redo=a.redo)
        except Exception as e:
            _cli_log('失败: %s' % e, 'err')
            traceback.print_exc()
            return 1
        _cli_log('流水线完成')
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
