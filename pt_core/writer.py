# -*- coding: utf-8 -*-
"""把翻译结果写入游戏的 Translation/<目标语言> 目录, 并做往返校验。

  1) RedirectedResources/<原结构>/...  供资源重定向器 (如 KK_TextResourceRedirector) 加载
  2) Text/PreTranslations.txt          供 XUAT 核心静态查表
写入前自动备份已有目标目录; 文件中已有的人工翻译行原样保留。
"""
import datetime
import os
import shutil

from .xuformat import encode, iter_translation_files, parse_existing, read_lines, split_pair_simple


def backup_dir(target):
    backup = target.rstrip('/\\') + '.backup-' + datetime.date.today().isoformat()
    if os.path.exists(backup):
        return None
    shutil.copytree(target, backup)
    return backup


def write_all(target_dir, files, zh_map, log_fn=print, text_keys=(),
              note='机翻: DLX(DeepLX) ja->zh', keep_existing=True, value_transform=None):
    """keep_existing=False 时强制重翻: 不保留文件中已有行, 全部按翻译库重新生成。
    value_transform: 写盘前对译文做最后处理 (如字体缺字替换), 不改动翻译库本身。"""
    def vt(v):
        if v and value_transform:
            return value_transform(v)
        return v
    made_backup = None
    if os.path.isdir(target_dir):
        made_backup = backup_dir(target_dir)
        if made_backup:
            log_fn('已备份原目录 -> %s' % made_backup)
    os.makedirs(target_dir, exist_ok=True)
    redir_out = os.path.join(target_dir, 'RedirectedResources')
    os.makedirs(redir_out, exist_ok=True)
    header = '// %s 预翻译缓存 | %s | 由 PreTranslator 生成 %s' % (
        os.path.basename(target_dir.rstrip('/\\')), note,
        datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    nfiles = nnew = nkept = nmiss = 0
    for rel, entries in files.items():
        dst = os.path.join(redir_out, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        existing = parse_existing(dst) if keep_existing else {}
        entry_keys = {e['k'] for e in entries}
        lines = [header]
        for e in entries:
            if e['k'] in existing:
                lines.append(existing[e['k']])
                nkept += 1
                continue
            v = zh_map.get(e['k'])
            if v:
                lines.append(e['raw'] + '=' + encode(vt(v)))
                nnew += 1
            else:
                nmiss += 1
        for dk, rawline in existing.items():
            if dk not in entry_keys:
                lines.append(rawline)
        with open(dst, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        nfiles += 1
    text_dir = os.path.join(target_dir, 'Text')
    os.makedirs(text_dir, exist_ok=True)
    pre_path = os.path.join(text_dir, 'PreTranslations.txt')
    existing_pre = parse_existing(pre_path) if keep_existing else {}
    out, seen = [], set()
    for dk, rawline in existing_pre.items():
        out.append(rawline)
        seen.add(dk)
    for rel, entries in files.items():
        for e in entries:
            k = e['k']
            if k in seen:
                continue
            v = zh_map.get(k)
            if v:
                seen.add(k)
                out.append(encode(k) + '=' + encode(vt(v)))
    for k in text_keys:  # Text/ 来源的独立 key (不进入 RedirectedResources 结构)
        if k in seen:
            continue
        v = zh_map.get(k)
        if v:
            seen.add(k)
            out.append(encode(k) + '=' + encode(vt(v)))
    with open(pre_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    log_fn('RedirectedResources: %d 文件 | 新翻译 %d 行 | 保留原有 %d 行 | 无译文 %d'
           % (nfiles, nnew, nkept, nmiss))
    log_fn('Text/PreTranslations.txt: 累计 %d 对' % len(out))
    return {'files': nfiles, 'new_lines': nnew, 'kept': nkept,
            'missing': nmiss, 'pre_pairs': len(out), 'backup': made_backup}


def verify(target_dir, zh_map, log_fn=print, value_transform=None):
    """往返校验: 按 XUAT 规则重新解析写出的文件, 与翻译库比对
    (译文同样经过 value_transform)。"""
    def vt(v):
        if v and value_transform:
            return value_transform(v)
        return v
    root = os.path.join(target_dir, 'RedirectedResources')
    ok = bad = nfiles = 0
    samples = []
    if os.path.isdir(root):
        for dirpath, _dns, fns in os.walk(root):
            for fn in fns:
                if not fn.lower().endswith('.txt'):
                    continue
                nfiles += 1
                try:
                    lines = read_lines('txt', os.path.join(dirpath, fn), None)
                except OSError:
                    continue
                for line in lines:
                    line = line.rstrip('\r\n')
                    if not line.strip() or line.startswith('//'):
                        continue
                    sp = split_pair_simple(line)
                    if sp is None:
                        bad += 1
                        continue
                    k, v = sp
                    # 两侧都过 value_transform: 写盘值已做过缺字替换,
                    # 只比原文会把启用替换的游戏永远判成"不一致"
                    if vt(zh_map.get(k)) == vt(v):
                        ok += 1
                    else:
                        bad += 1
                        if len(samples) < 5:
                            samples.append(k[:60])
    log_fn('校验 RedirectedResources: %d 文件 | 一致 %d 行 | 不一致 %d 行'
           % (nfiles, ok, bad))
    # PreTranslations.txt 校验
    pre = os.path.join(target_dir, 'Text', 'PreTranslations.txt')
    pok = pbad = 0
    if os.path.isfile(pre):
        try:
            lines = read_lines('txt', pre, None)
        except OSError:
            lines = []
        for line in lines:
            line = line.rstrip('\r\n')
            if not line.strip() or line.startswith('//'):
                continue
            sp = split_pair_simple(line)
            if sp is None:
                pbad += 1
                continue
            k, v = sp
            if vt(zh_map.get(k)) == vt(v):
                pok += 1
            else:
                pbad += 1
                if len(samples) < 5:
                    samples.append(k[:60])
    log_fn('校验 PreTranslations.txt: 一致 %d 行 | 不一致 %d 行' % (pok, pbad))
    if samples:
        log_fn('不一致样例: %s' % ' ; '.join(samples), 'warn')
    return {'files': nfiles, 'ok': ok + pok, 'bad': bad + pbad, 'samples': samples}
