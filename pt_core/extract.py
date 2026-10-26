# -*- coding: utf-8 -*-
"""从翻译来源目录提取日文原文。

来源 = 游戏 Translation/<其他语言> 目录 + 用户指定的外部来源目录,
要求目录下有 Text/ 或 RedirectedResources/ 子目录 (XUAT 翻译包标准结构)。
输出:
- files: {相对路径: [{raw: 原始key, k: 解码key}]}  — RedirectedResources 结构
- keys:  有序去重的含日文 key 列表 (待翻译)
"""
import os

from .xuformat import (decode, has_japanese, iter_translation_files,
                       plausible_english, read_lines, split_line_raw,
                       split_pair_simple)


def is_source_folder(p):
    return (os.path.isdir(os.path.join(p, 'Text'))
            or os.path.isdir(os.path.join(p, 'RedirectedResources')))


def collect_sources(translation_dir, target_lang, extra_sources):
    """汇总可用翻译来源: 游戏内置其他语言目录 + 外部来源。返回 [(标签, 路径)]。"""
    sources = []
    if translation_dir and os.path.isdir(translation_dir):
        for nm in sorted(os.listdir(translation_dir)):
            p = os.path.join(translation_dir, nm)
            if not os.path.isdir(p):
                continue
            if nm.lower() == str(target_lang).lower() or '.backup' in nm.lower():
                continue
            if is_source_folder(p):
                sources.append(('游戏内置(%s)' % nm, p))
    for p in extra_sources or []:
        if os.path.isdir(p) and is_source_folder(p):
            sources.append(('外部来源', p))
    return sources


def _rel_for(kind, base, a, b):
    if kind == 'txt':
        return os.path.relpath(a, base).replace('\\', '/')
    # zip 内的 txt: 以 zip 相对路径 + 条目路径合并 (写出为普通文件)
    rel = os.path.relpath(a, base).replace('\\', '/')
    if rel.lower().endswith('.zip'):
        rel = rel[:-4]
    ent = b.replace('\\', '/')
    if ent.lower().endswith('.txt'):
        ent = ent[:-4]
    return rel + '/' + ent


def extract(sources, log_fn=print):
    files = {}
    key_order = {}
    text_keys = {}
    stats = []
    npairs = 0
    nread = 0
    for label, src in sources:
        s_files0 = len(files)
        s_pairs = 0
        for sub, structural in (('RedirectedResources', True), ('Text', False)):
            base = os.path.join(src, sub)
            if not os.path.isdir(base):
                continue
            for kind, a, b in iter_translation_files(base):
                try:
                    lines = read_lines(kind, a, b)
                except Exception:
                    continue
                nread += 1
                if nread % 2000 == 0:
                    log_fn('  已解析 %d 个文件...' % nread)
                rel = _rel_for(kind, base, a, b) if structural else None
                entries = files.setdefault(rel, []) if rel else None
                seen_rel = {e['k'] for e in entries} if entries else set()
                for line in lines:
                    line = line.rstrip('\r\n')
                    if not line.strip():
                        continue
                    sp = split_line_raw(line)
                    if sp is None:
                        continue
                    raw_key, _v = sp
                    if '//' in raw_key:
                        continue
                    kd = decode(raw_key)
                    if not kd or not kd.strip():
                        continue
                    if entries is not None:
                        if kd in seen_rel:
                            continue
                        seen_rel.add(kd)
                        entries.append({'raw': raw_key, 'k': kd})
                    else:
                        text_keys[kd] = True
                    if kd not in key_order:
                        key_order[kd] = True
                    s_pairs += 1
                    npairs += 1
        keys_all = len(key_order)
        stats.append({'label': label, 'path': src, 'files': len(files) - s_files0,
                      'pairs': s_pairs, 'keys': keys_all})
        log_fn('来源[%s]: 文件+%d 行+%d (累计唯一key %d)' % (label, len(files) - s_files0, s_pairs, keys_all))
    keys_jp = [k for k in key_order if has_japanese(k)]
    keys_en = [k for k in key_order if not has_japanese(k) and plausible_english(k)]
    keys = keys_jp + keys_en
    struct_keys = {e['k'] for es in files.values() for e in es}  # 只构建一次 (勿内联到下方条件里)
    text_only = [k for k in text_keys if k not in struct_keys]
    log_fn('提取完成: %d 个结构文件 | %d 行 | 唯一key %d | 日文 %d | 英文 %d | Text独有key %d'
           % (len(files), npairs, len(key_order), len(keys_jp), len(keys_en),
              len(text_only)))
    return files, keys, stats, text_only


def load_existing_translations(target_dir):
    """读取目标语言目录中已有的翻译 {key: 译文}。已有条目不会被覆盖。"""
    out = {}
    if not target_dir or not os.path.isdir(target_dir):
        return out
    for kind, a, b in iter_translation_files(target_dir):
        try:
            lines = read_lines(kind, a, b)
        except Exception:
            continue
        for line in lines:
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            sp = split_pair_simple(line)
            if sp and sp[0] and sp[1]:
                out[sp[0]] = sp[1]
    return out
