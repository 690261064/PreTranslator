# -*- coding: utf-8 -*-
"""XUnity.AutoTranslator 翻译文件格式。

行格式: 原文=译文 ; '\' 转义下一字符 ; '//' 为注释 ; 兼容旧版 %3D 表示 '='。
解析规则模仿 XUAT 的 TextHelper.ReadTranslationLineAndDecode / ParseTranslationLine。
"""
import os
import re
import zipfile


def split_line_raw(line):
    """按第一个未转义的 '=' 切分, 返回 (raw_key, raw_value); 无法解析返回 None。
    key 部分遇到 '//' 注释时按 XUAT 行为截断/丢弃。"""
    parts = []
    builder = []
    escape = False
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if escape:
            builder.append(c)
            escape = False
        elif c == '\\':
            builder.append(c)
            escape = True
        elif c == '=':
            if len(parts) >= 2:
                return None
            parts.append(''.join(builder))
            builder = []
        elif c == '/':
            if i + 1 < n and line[i + 1] == '/':
                if len(parts) == 1:
                    return (parts[0], ''.join(builder))
                return None
            builder.append(c)
        else:
            builder.append(c)
        i += 1
    parts.append(''.join(builder))
    if len(parts) == 2:
        return (parts[0], parts[1])
    return None


def split_pair_simple(line):
    """宽松解析: 跳过 '//' 开头的行, 取第一个未转义的 '=' 前后并解码。"""
    if line.startswith('//'):
        return None
    idx, esc = -1, False
    for i, c in enumerate(line):
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '=':
            idx = i
            break
    if idx <= 0:
        return None
    return decode(line[:idx]), decode(line[idx + 1:])


def decode(s):
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nc = s[i + 1]
            if nc in ('=', '\\', '/'):
                out.append(nc)
                i += 2
                continue
            if nc == 'n':
                out.append('\n')
                i += 2
                continue
            if nc == 'r':
                out.append('\r')
                i += 2
                continue
            if nc == 'u' and i + 5 < n:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(c)
            i += 1
            continue
        if c == '%' and s[i:i + 3] == '%3D':
            out.append('=')
            i += 3
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def encode(s):
    """按 XUAT TextHelper.EscapeNewlines 转义 ('//' 转义防被当行内注释截断)。"""
    return (s.replace('\\', '\\\\').replace('=', '\\=')
            .replace('\r', '\\r').replace('\n', '\\n')
            .replace('//', '\\/\\/'))


def has_japanese(s):
    for ch in s:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0xFF01 <= o <= 0xFF9F:
            return True
    return False


_EN_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
_EN_OK = _EN_CHARS | set("0123456789 ',.!?:;%()\u3009\u3008[]-+\u2026\u2019\u201c\u201d\"/&")


def plausible_english(s):
    """疑似英文显示文本 (用于原文为英文的游戏)。标识符/路径类会被排除。"""
    if not s or len(s) > 500:
        return False
    if has_japanese(s):
        return False
    alpha = sum(1 for ch in s if ch in _EN_CHARS)
    if alpha < 3:
        return False
    for ch in s:
        if ch not in _EN_OK:
            return False
    if re.fullmatch(r'[A-Z_0-9]+', s):
        return False  # 常量/枚举名
    if re.fullmatch(r'[\w.\'\- ]{1,40} \(\d+\)', s):
        return False  # Unity 对象名 (Name (实例ID)), 不是显示文本
    if ' ' in s:
        # JSON 键值碎片 (Unity InputAction 等配置) 不是显示文本
        if re.search(r'"\s*:', s):
            return False
        if s.rstrip().endswith((',', '[', '{')) and s.count('"') >= 2:
            return False
        if re.search(r'[0-9a-f]{8}-[0-9a-f]{4}', s, re.I):
            return False  # UUID
        return max(len(t.strip('"\'[]{}:, ')) for t in s.split()) >= 3
    # 无空格单词: 排除 camelCase / 蛇形 / 过长标识符
    if '_' in s or len(s) > 25:
        return False
    return not any(c.isupper() for c in s[1:])


def iter_translation_files(folder):
    """遍历目录, 产出 ('txt', 路径, None) 与 ('zip', zip路径, 条目名)。"""
    if not folder or not os.path.isdir(folder):
        return
    for dirpath, _dns, fns in os.walk(folder):
        for fn in sorted(fns):
            low = fn.lower()
            full = os.path.join(dirpath, fn)
            if low.endswith('.txt'):
                yield ('txt', full, None)
            elif low.endswith('.zip'):
                try:
                    with zipfile.ZipFile(full) as zf:
                        for nm in zf.namelist():
                            if nm.lower().endswith('.txt') and not nm.endswith('/'):
                                yield ('zip', full, nm)
                except (zipfile.BadZipFile, OSError):
                    continue


def read_lines(kind, a, b):
    if kind == 'txt':
        with open(a, encoding='utf-8-sig', errors='replace') as f:
            return f.read().splitlines()
    with zipfile.ZipFile(a) as zf:
        return zf.read(b).decode('utf-8-sig', 'replace').splitlines()


def parse_existing(path):
    """解析已存在的翻译文件, 返回 {解码key: 原始行}。用于保留人工翻译。"""
    result = {}
    if not path or not os.path.isfile(path):
        return result
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line.strip() or line.startswith('//'):
                    continue
                sp = split_pair_simple(line)
                if sp and sp[0]:
                    result[sp[0]] = line
    except OSError:
        pass
    return result
