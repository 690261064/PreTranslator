# -*- coding: utf-8 -*-
"""运行报告 (run_report.jsonl) 与批量完成标记 (.pt_done.json) 的公共逻辑。

GUI、CLI 批跑共用同一套判定, 避免两处标记行为不一致:
- 每次流水线跑完追加一行 JSON 报告 (句数/速度/429统计/配额), 供长期复盘。
- 完成标记只有在「校验全一致 AND 无永久失败」时才写 —— 只看校验一致会把
  翻了一部分的游戏误标为已完成, 后续批次会跳过它。
"""
import json
import os
import shutil
import time

REPORT_NAME = 'run_report.jsonl'
MARKER_NAME = '.pt_done.json'


def report_path(tool_dir):
    return os.path.join(tool_dir, REPORT_NAME)


def build_entry(game, state, lang='', quota=None):
    """从流水线结果 state 提取一条报告记录 (不含任何用户敏感信息)。"""
    eng = state.get('engine') or {}
    w = state.get('write') or {}
    v = state.get('verify') or {}
    entry = {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'game': game.name,
        'root': game.root,
        'lang': lang,
        'translated': eng.get('done_now', 0),
        'reused': eng.get('reused', 0),
        'total': eng.get('total', 0),
        'elapsed': round(eng.get('elapsed', 0.0), 1),
        'rate': round(eng.get('rate', 0.0), 2),
        'perm_failed': eng.get('perm_failed', 0),
        'requests': eng.get('requests', 0),
        'hits429': eng.get('hits429', 0),
        'penalty_trips': eng.get('penalty_trips', 0),
        'channels': eng.get('channels', []),
        'pre_pairs': w.get('pre_pairs', 0),
        'verify_ok': v.get('ok', 0),
        'verify_bad': v.get('bad', 0),
        'stopped': bool(state.get('stopped')),
    }
    fp = state.get('font')
    if fp is not None:
        entry['font'] = {'mode': fp.get('mode'), 'patched': fp.get('patched'),
                         'baked': fp.get('baked'), 'skipped': fp.get('skipped'),
                         'bake_blocked': fp.get('bake_blocked', False)}
    if quota:
        entry['quota'] = {'used': quota[0], 'limit': quota[1]}
    return entry


def append_report(tool_dir, entry, log_fn=print):
    try:
        with open(report_path(tool_dir), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as e:
        log_fn('运行报告写入失败: %s' % e, 'warn')


def marker_path(game, lang):
    return os.path.join(game.target_dir(lang), MARKER_NAME)


def is_done(game, lang):
    return os.path.isfile(marker_path(game, lang))


def marker_ok(state):
    """校验全一致且无永久失败才算完成。"""
    v = state.get('verify') or {}
    eng = state.get('engine') or {}
    return v.get('bad', 1) == 0 and eng.get('perm_failed', 0) == 0


def write_marker(game, lang, state, log_fn=print):
    if not marker_ok(state):
        return False
    try:
        with open(marker_path(game, lang), 'w', encoding='utf-8') as mf:
            json.dump({'at': time.strftime('%Y-%m-%d %H:%M'),
                       'pairs': (state.get('write') or {}).get('pre_pairs', 0)}, mf)
        return True
    except OSError as e:
        log_fn('完成标记写入失败: %s' % e, 'warn')
        return False


# ---------------------------------------------------------------- 缓存维护
def cache_scan(cache_root, expected_names):
    """扫描断点缓存目录。expected_names: 在用的目录名集合 (由当前已知游戏路径算出)。

    返回 [{name, path, prefix, keys, mtime, status}], status ∈
    'inuse' / 'dup' / 'orphan'; downloads 等非缓存目录被忽略。
    dup 标在每组重复里"非保留"的那些 (保留 = 在用者, 否则 mtime 最新者)。
    """
    seen_prefix = {}
    out = []
    if not os.path.isdir(cache_root):
        return out
    dirs = sorted(d for d in os.listdir(cache_root)
                  if os.path.isdir(os.path.join(cache_root, d)) and d != 'downloads')
    for d in dirs:
        p = os.path.join(cache_root, d)
        n = _count_keys(os.path.join(p, 'zh_map.jsonl'))
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0.0
        out.append({'name': d, 'path': p, 'prefix': d.rsplit('-', 1)[0],
                    'keys': n, 'mtime': mt,
                    'status': 'inuse' if d in expected_names else 'orphan'})
    # 重复组: 同前缀多个目录 -> 只保留一个 (在用优先, 其次最新)
    groups = {}
    for rec in out:
        groups.setdefault(rec['prefix'], []).append(rec)
    for _pref, recs in groups.items():
        if len(recs) < 2:
            continue
        keep = next((r for r in recs if r['status'] == 'inuse'), None) \
            or max(recs, key=lambda r: r['mtime'])
        for r in recs:
            if r is not keep:
                r['status'] = 'dup'
                r['merge_into'] = keep['name']
    return out


def _count_keys(path):
    n = 0
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        pass
    return n


def merge_cache_dirs(dst_path, src_paths, remove_src=True, log_fn=print):
    """把 src 目录的 zh_map.jsonl 去重合并进 dst, 返回 (补入键数, 总键数)。

    remove_src=True 时合并后删除源目录 (源必须是同一游戏分裂出的缓存,
    由调用方保证; 本函数只动 zh_map.jsonl 所在的缓存目录)。
    """
    have = set()
    dst_file = os.path.join(dst_path, 'zh_map.jsonl')
    if os.path.isfile(dst_file):
        try:
            with open(dst_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get('k'):
                        have.add(o['k'])
        except OSError:
            pass
    added = 0
    total = len(have)
    lines = []
    for src in src_paths:
        p = os.path.join(src, 'zh_map.jsonl')
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if not (o.get('k') and 'v' in o):
                        continue
                    total += 1
                    if o['k'] not in have:
                        have.add(o['k'])
                        added += 1
                        lines.append(json.dumps(o, ensure_ascii=False))
        except OSError as e:
            log_fn('读取 %s 失败: %s' % (p, e), 'warn')
    if lines:
        os.makedirs(dst_path, exist_ok=True)
        try:
            with open(dst_file, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
        except OSError as e:
            log_fn('合并写入失败: %s' % e, 'warn')
            return 0, total
    if remove_src:
        for src in src_paths:
            shutil.rmtree(src, ignore_errors=True)
    return added, total
