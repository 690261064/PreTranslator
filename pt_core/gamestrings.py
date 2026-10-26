# -*- coding: utf-8 -*-
"""从游戏文件直接提取日文原文 (用于没有任何翻译包的游戏)。

两个来源:
1. Managed/*.dll  — .NET 程序集的 #US (User Strings) 堆: 字面量字符串, 精确可靠
2. Unity 资源文件 — resources.assets / level / sharedassets / TextAsset 等,
   按 UTF-8 / UTF-16LE 解码后按行扫描含日文的行 (启发式, 可能有少量杂质)

提取结果作为 text_keys 参与 DLX 翻译, 以静态翻译 (PreTranslations.txt) 形式生效。
"""
import os
import re
import struct

from .xuformat import has_japanese, plausible_english

JP_HEAD = r'\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff01-\uff9f'
JP_RUN = re.compile('[' + JP_HEAD + ']{2,}')

# 扩展字符: 日文run之间允许出现的标点/数字/字母 (扩展后首尾会再裁回日文)
_EXTRA = set('。、！？!?\u2026\u3000「」『』（）()[]【】<>〜~・：:；;，,.\'-_/\\+%$#＠@＝=×÷&\" '
             '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')


def clean_candidate(s):
    if not s:
        return ''
    s = s.replace('\ufffd', '')
    s = s.strip().strip('\x00').strip()
    if '\x00' in s:
        s = s.split('\x00')[0].strip()
    if not s or len(s) > 1000:
        return ''
    # 控制字符(除\t) => 二进制噪声
    for ch in s:
        o = ord(ch)
        if o < 32 and o not in (9, 10, 13):
            return ''
    return s


# 韩文/私用区/代理区/部首符号等: 日文文本不会出现, 出现即说明是二进制噪声
_BAD_RANGES = ((0xAC00, 0xD7AF), (0xE000, 0xF8FF), (0xD800, 0xDFFF),
               (0x2E80, 0x2EFF), (0x2FF0, 0x2FFF), (0x31C0, 0x31EF),
               (0x1900, 0x19FF), (0xA960, 0xA97F), (0x1B000, 0x1BFFF))


def has_bad_block(s):
    for ch in s:
        o = ord(ch)
        for lo, hi in _BAD_RANGES:
            if lo <= o <= hi:
                return True
    return False


def has_kana(s):
    for ch in s:
        if 0x3040 <= ord(ch) <= 0x30FF:
            return True
    return False


# 纯汉字短标签 (无假名) 允许出现的标点
_KANJI_ONLY_OK = set('。、！？：；・％　')
# XUAT 运行时对不超过 MaxCharactersPerTranslation 的文本整串查表, 超长直接忽略。
# 我们把多行整串作为整键提取, 但上限与游戏配置一致 (默认 200, 工具会调到 500)。
DEFAULT_MAX_CHARS = 200


def plausible_japanese(s, require_kana=True):
    """启发式扫描结果的可信度过滤。"""
    if not s or has_bad_block(s):
        return False
    # 日文字符占比过低 => 混入大量ASCII/符号 => 二进制噪声
    jp = sum(1 for ch in s if 0x3040 <= ord(ch) <= 0x30FF or 0x4E00 <= ord(ch) <= 0x9FFF
             or 0xFF01 <= ord(ch) <= 0xFF9F or 0x3000 <= ord(ch) <= 0x303F)
    ratio = jp / len(s)
    if ratio < 0.45:
        return False
    if not has_japanese(s):
        return False
    if require_kana and not has_kana(s):
        # 纯汉字 UI 标签 (断面図/警告/羊皮紙): 放行短而干净的; 其余视为噪声
        if len(s) > 12:
            return False
        for ch in s:
            if not (0x4E00 <= ord(ch) <= 0x9FFF) and ch not in _KANJI_ONLY_OK:
                return False
    if len(s) <= 4 and ratio < 0.9:
        return False
    return True


def _decode_passes(raw):
    for enc in ('utf-8', 'utf-16-le'):
        yield raw.decode(enc, 'ignore')


def _expand_run(text, start, end):
    lim = 100
    n = 0
    while end < len(text) and n < lim and text[end] in _EXTRA and text[end] not in '\r\n':
        end += 1
        n += 1
    n = 0
    while start > 0 and n < lim and text[start - 1] in _EXTRA and text[start - 1] not in '\r\n':
        start -= 1
        n += 1
    seg = text[start:end]
    # 裁掉首尾非日文字符
    while seg and not has_japanese(seg[0]):
        seg = seg[1:]
    while seg and not has_japanese(seg[-1]):
        seg = seg[:-1]
    return seg


def extract_dotnet_us_strings(path, log_fn=print):
    """解析 .NET 程序集 #US 字符串堆, 返回其中含日文的字符串列表。"""
    out = []
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return out
    try:
        us = _locate_us_heap(data)
    except Exception:
        us = None
    if us is not None:
        off, size = us
        end = min(off + size, len(data))
        pos = off + 1  # 跳过堆首保留字节
        while pos < end:
            try:
                b = data[pos]
                if b < 0x80:
                    ln, hdr = b, 1
                elif b < 0xC0:
                    ln, hdr = ((b & 0x3F) << 8) | data[pos + 1], 2
                elif b < 0xE0:
                    ln, hdr = (((b & 0x1F) << 24) | (data[pos + 1] << 16)
                               | (data[pos + 2] << 8) | data[pos + 3]), 4
                else:
                    break
            except IndexError:
                break
            pos += hdr
            if ln <= 1:
                continue
            raw = data[pos:pos + ln - 1]  # 末字节是标志位
            pos += ln
            try:
                s = raw.decode('utf-16-le')
            except Exception:
                continue
            s = clean_candidate(s)
            if s and not has_bad_block(s):
                out.append(s)
    if not out:
        # 兜底: 整个文件按 UTF-16 扫描
        text = data.decode('utf-16-le', 'ignore')
        for m in JP_RUN.finditer(text):
            seg = _expand_run(text, m.start(), m.end())
            if seg and plausible_japanese(seg, require_kana=False):
                out.append(seg)
    # CLI 嵌入资源区: .resources 中字符串 = 7位长度前缀 + UTF-16LE (对话/文本表常在这里)
    try:
        region = _cli_resources_region(data)
    except Exception:
        region = None
    if region:
        out.extend(_scan_7bit_utf16(region))
    return out


def _sections_of(data):
    if data[:2] != b'MZ' or len(data) < 0x100:
        return None, None
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\x00\x00':
        return None, None
    coff = pe + 4
    nsec = struct.unpack_from('<H', data, coff + 2)[0]
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from('<H', data, opt)[0]
    ddir = opt + (96 if magic == 0x10B else 112 if magic == 0x20B else 0)
    if not ddir:
        return None, None
    sections = []
    sec_off = opt + optsize
    for i in range(nsec):
        base = sec_off + i * 40
        vsize, va, rsize, praw = struct.unpack_from('<IIII', data, base + 8)
        sections.append((va, vsize, praw, rsize))
    return sections, ddir


def _rva2off(sections, rva):
    for va, vsize, praw, rsize in sections:
        if va <= rva < va + max(vsize, rsize):
            return rva - va + praw
    return None


def _cli_resources_region(data):
    sections, ddir = _sections_of(data)
    if not sections:
        return None
    com_rva = struct.unpack_from('<I', data, ddir + 14 * 8)[0]
    if not com_rva:
        return None
    cli = _rva2off(sections, com_rva)
    if cli is None:
        return None
    res_rva, res_size = struct.unpack_from('<II', data, cli + 24)
    if not res_rva or not res_size:
        return None
    off = _rva2off(sections, res_rva)
    if off is None:
        return None
    return data[off:off + res_size]


def _scan_7bit_utf16(region):
    """扫描 [7位长度前缀][UTF-16LE] 形式的字符串 (.resources 的名称与值)。"""
    out = []
    n = len(region)
    pos = 0
    while pos < n - 2:
        b = region[pos]
        if b < 0x80:
            ln, hdr = b, 1
        elif b < 0xC0:
            if pos + 1 >= n:
                break
            ln, hdr = ((b & 0x3F) << 8) | region[pos + 1], 2
        elif b < 0xE0:
            if pos + 3 >= n:
                break
            ln, hdr = (((b & 0x1F) << 24) | (region[pos + 1] << 16)
                       | (region[pos + 2] << 8) | region[pos + 3]), 4
        else:
            pos += 1
            continue
        if ln < 2 or ln % 2 or pos + hdr + ln > n:
            pos += 1
            continue
        try:
            s = region[pos + hdr:pos + hdr + ln].decode('utf-16-le')
        except Exception:
            pos += 1
            continue
        pos += hdr + ln
        s = clean_candidate(s)
        if s and plausible_japanese(s, require_kana=True):
            out.append(s)
    return out


def _locate_us_heap(data):
    if data[:2] != b'MZ' or len(data) < 0x100:
        return None
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\x00\x00':
        return None
    coff = pe + 4
    nsec = struct.unpack_from('<H', data, coff + 2)[0]
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from('<H', data, opt)[0]
    ddir = opt + (96 if magic == 0x10B else 112 if magic == 0x20B else 0)
    if not ddir:
        return None
    com_rva = struct.unpack_from('<I', data, ddir + 14 * 8)[0]
    if not com_rva:
        return None
    sections = []
    sec_off = opt + optsize
    for i in range(nsec):
        base = sec_off + i * 40
        vsize, va, rsize, praw = struct.unpack_from('<IIII', data, base + 8)
        sections.append((va, vsize, praw, rsize))

    def rva2off(rva):
        for va, vsize, praw, rsize in sections:
            if va <= rva < va + max(vsize, rsize):
                return rva - va + praw
        return None

    cli = rva2off(com_rva)
    if cli is None:
        return None
    meta_rva = struct.unpack_from('<I', data, cli + 8)[0]
    mroot = rva2off(meta_rva)
    if mroot is None or struct.unpack_from('<I', data, mroot)[0] != 0x424A5342:
        return None
    ver_len = struct.unpack_from('<I', data, mroot + 12)[0]
    p = mroot + 16 + ver_len
    _flags, nstreams = struct.unpack_from('<HH', data, p)
    p += 4
    for _ in range(nstreams):
        so, ss = struct.unpack_from('<II', data, p)
        q = p + 8
        name_end = data.index(b'\x00', q)
        name = data[q:name_end].decode('ascii', 'replace')
        p = mroot + ((name_end + 1 - mroot + 3) & ~3)
        if name == '#US':
            return (mroot + so, ss)
    return None


_LOGTAG_RE = re.compile(r'^\[[\w.]+\]\s')  # 日志格式串: [SomeClass] ... {0}


def _clean_soft(seg):
    """clean_candidate 的宽容版: 序列化字符串首尾常粘连长度前缀/二进制垃圾
    (控制字符与无意义 ASCII), 剥掉后重试一次。仍失败返回 ''。"""
    s = clean_candidate(seg)
    if s:
        return s
    t = seg.strip('\x00\r\n\t ')
    while t and (ord(t[0]) < 32 or (ord(t[0]) < 0x80 and not t[0].isalnum()
                                    and t[0] not in '"\'([{')):
        t = t[1:]
    while t and (ord(t[-1]) < 32 or (ord(t[-1]) < 0x80 and not t[-1].isalnum()
                                     and t[-1] not in '"\')]}!.?:;%')):
        t = t[:-1]
    return clean_candidate(t) if t != seg else ''


def _collect(seg, out, units):
    """单个候选片段: 整体可信则收集 (含换行的进 units 整串键, 行级同时收集);
    否则按行/日文段再切。"""
    s = _clean_soft(seg)
    if not s:
        return
    if plausible_japanese(s, require_kana=True):
        if '\n' in s or '\r' in s:
            units.append(s)
            for ln in s.splitlines():
                _collect(ln, out, units)
        else:
            out.append(s)
        return
    if '\n' not in s and '\r' not in s and ' ' in s and plausible_english(s) \
            and not _LOGTAG_RE.match(s):
        out.append(s)
        return
    if '\n' in s or '\r' in s:
        # 多行块整体不可信: 逐行重试 (行内还可能粘着二进制前缀)
        for ln in s.splitlines():
            _collect(ln, out, units)
        return
    for m in JP_RUN.finditer(s):
        seg2 = _expand_run(s, m.start(), m.end())
        if seg2 and plausible_japanese(seg2, require_kana=True):
            out.append(seg2)


def scan_asset_file(path, log_fn=None):
    """单个资源文件: Unity 序列化字符串为 UTF-8 (长度前缀+对齐)。

    解码后按 NUL 切分成片段 —— 长度前缀/填充字节里充满 NUL, 片段边界天然对齐
    序列化字符串, 避免二进制前缀把字符串吞掉。片段可能是带换行的多行对话块
    (XUAT 运行时按整串查表), 收进 units 作为整串键。"""
    out = []
    units = []
    try:
        if os.path.getsize(path) > 160 * 1024 * 1024:
            return out, units
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        return out, units
    text = raw.decode('utf-8', 'ignore')
    for chunk in text.split('\x00'):
        if not chunk:
            continue
        if '\n' in chunk or '\r' in chunk:
            _collect(chunk, out, units)
            continue
        s = _clean_soft(chunk)
        if not s:
            continue
        if plausible_japanese(s, require_kana=True):
            out.append(s)
        elif ' ' in s and plausible_english(s) and not _LOGTAG_RE.match(s):
            out.append(s)
        else:
            for m in JP_RUN.finditer(s):
                seg = _expand_run(s, m.start(), m.end())
                if seg and plausible_japanese(seg, require_kana=True):
                    out.append(seg)
    return out, units


def il2cpp_metadata_strings(path, log_fn=None):
    """扫描 IL2CPP 的 global-metadata.dat (字符串字面量均为 null 分隔的 UTF-8)。"""
    out = []
    try:
        if os.path.getsize(path) > 400 * 1024 * 1024:
            return out
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        return out
    text = raw.decode('utf-8', 'ignore')
    for line in text.split('\x00'):
        s = clean_candidate(line)
        if not s:
            continue
        if plausible_japanese(s, require_kana=True):
            out.append(s)
        elif ' ' in s and plausible_english(s) and not _LOGTAG_RE.match(s):
            out.append(s)
    return out


def extract_from_game(game, cap=20000, log_fn=print, max_chars=DEFAULT_MAX_CHARS):
    """从游戏二进制提取原文 (日文优先, 其余收集疑似英文显示文本)。

    返回 (keys, units): keys 参与翻译; units 是带换行的多行整串
    (XUAT 运行时对短文本整串查表, 需要整串键), 全部不超过 max_chars
    (超过的 XUAT 直接忽略, 不值得翻译, 其行级内容仍会提取)。
    """
    from .xuformat import plausible_english
    from . import games as _games
    ja, en, units, seen = [], [], [], set()

    def _classify(s):
        if has_japanese(s):
            ja.append(s)
        elif plausible_english(s):
            en.append(s)

    def add(s):
        if not s or s in seen:
            return
        seen.add(s)
        if '\n' in s or '\r' in s:
            if len(s) <= max_chars and (plausible_japanese(s) or plausible_english(s)):
                units.append(s)
            for ln in s.splitlines():
                if ln and ln not in seen:
                    seen.add(ln)
                    _classify(ln)   # 行级内容单独提取, 供组合键与单行显示命中
            return
        _classify(s)

    notes = []
    meta = None
    try:
        from . import games as _g2
        meta = _g2.il2cpp_metadata_file(game)
    except Exception:
        meta = None
    if meta:
        n0 = len(ja) + len(en) + len(units)
        for s in il2cpp_metadata_strings(meta):
            add(clean_candidate(s))
        log_fn('IL2CPP 元数据 (%s): %d 条'
               % (os.path.basename(meta), len(ja) + len(en) + len(units) - n0))
    for dll in _games.managed_dlls(game):
        n0 = len(ja) + len(en) + len(units)
        for s in extract_dotnet_us_strings(dll):
            add(clean_candidate(s))
        if len(ja) + len(en) + len(units) > n0:
            notes.append('%s: %d' % (os.path.basename(dll),
                                     len(ja) + len(en) + len(units) - n0))
    if notes:
        log_fn('程序集字符串: %s' % ', '.join(notes))
    assets = _games.unity_asset_files(game)
    asset_start = len(ja) + len(en) + len(units)
    nassets = 0
    for ap in assets:
        lines, aus = scan_asset_file(ap)
        for s in lines:
            add(clean_candidate(s))
        for s in aus:
            add(s)
        nassets += 1
        if nassets % 20 == 0:
            log_fn('  资源扫描 %d/%d 文件 (累计 %d)'
                   % (nassets, len(assets), len(ja) + len(en) + len(units)))
    if assets:
        log_fn('资源扫描: %d 文件, 新增 %d (含多行整串 %d)'
               % (nassets, len(ja) + len(en) + len(units) - asset_start, len(units)))
    keys = units + ja + en
    if len(keys) > cap:
        log_fn('超出上限 %d, 截断 (提高 binary_extract_cap 可放宽)' % cap, 'warn')
        keys = keys[:cap]
    return keys, units
