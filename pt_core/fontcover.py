# -*- coding: utf-8 -*-
"""游戏字体缺字检测与译文安全替换。

背景: 很多日文游戏 (尤其用 TextMeshPro 静态 SDF 图集的) 字体里只烘焙了
原文日文出现过的字形, 简体专用字 (额/释/说/读…) 显示为方框。XUAT 的
OverrideFontTextMeshPro 对 TMP < 3.2 的游戏无效 (无 OS 字体创建 API),
游戏内也没有可用的同名字体资产, 所以在译文层面做保守替换:
  简体字 -> 日式写法 (fontmap.S2JP), 且仅当替换字确实出现在
  游戏原文 (即字体图集覆盖范围) 内才替换。
"""
import os

from .fontmap import S2JP

# TMP (TextMeshPro) 静态图集游戏判定: 装了 TMP 才可能有静态图集缺字问题
_TMP_HINTS = ('unity.textmeshpro.dll', 'textmeshpro.dll')


def has_tmp(game):
    """游戏是否使用 TextMeshPro (Managed 目录里有 TMP dll)。"""
    roots = []
    if game.managed_dir:
        roots.append(game.managed_dir)
    roots.append(os.path.join(game.root, 'BepInEx', 'plugins'))
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for fn in os.listdir(root):
                if fn.lower() in _TMP_HINTS:
                    return True
        except OSError:
            continue
    return False


def build_coverage(ja_keys):
    """用游戏原文出现过的字符近似字体图集覆盖范围 (TMP 静态图集只烘焙原文字形)。"""
    covered = set()
    for k in ja_keys:
        covered.update(k)
    covered.update(chr(c) for c in range(0x20, 0x7F))  # ASCII
    covered.update('。、！？：；…―ー〜「」『』（）・＜＞　')
    return covered


# 符号缺字兜底: 字体 cmap 里没有、且没有日式写法可替的常用符号
_SYMBOL_SUBS = {'♡': '♥', '♪': '☆', '♫': '☆', '♩': '☆', '♬': '☆',
                '⁇': '？？', '⁈': '？！', '⁉': '！？', '‼': '！！'}


def chars_from_cmap(cmap):
    """把字体 cmap 转成单字字符串集合。

    fontpatch/_load_cmap 返回的是整数码点集合 (fontTools getBestCmap 的 keys),
    而 substitute_text/report 按字符判定 —— 曾因直接传 int 集合导致烘焙路径下
    缺字替换与缺字报告全部失效、警告误报全部字符。
    """
    out = set()
    for c in cmap or ():
        if isinstance(c, int):
            if 0 <= c <= 0x10FFFF:
                out.add(chr(c))
        else:
            out.add(c)
    return out


def substitute_text(text, covered):
    """把 text 中字体覆盖不到、且有日式写法且写法在覆盖内的字符替换掉。
    无日式写法的常用符号 (♡♪ 等) 用覆盖内的近似符号兜底。
    返回 (新文本, 替换数)。覆盖外的字符保持原样。"""
    if not text:
        return text, 0
    out = []
    n = 0
    for ch in text:
        if ord(ch) > 0x2000 and ch not in covered:
            alt = S2JP.get(ch)
            if alt and alt in covered:
                out.append(alt)
                n += 1
                continue
            alt = _SYMBOL_SUBS.get(ch)
            if alt and all(c in covered for c in alt):
                out.append(alt)
                n += 1
                continue
        out.append(ch)
    return ''.join(out), n


def make_transform(covered):
    """给 writer 的 value_transform: 写盘前对译文做缺字替换。"""
    def _t(v):
        return substitute_text(v, covered)[0]
    return _t


def report(values, covered, log_fn=print):
    """统计译文中字体覆盖不到的字符 (替换后仍缺的), 返回缺失字符集合。"""
    missing = set()
    for v in values:
        if not v:
            continue
        for ch in v:
            if ord(ch) > 0x2000 and ch not in covered:
                missing.add(ch)
    if missing:
        log_fn('字体缺字警告: 译文中有 %d 种字符不在游戏字体范围内, 会显示为方框: %s'
               % (len(missing), ''.join(sorted(missing))), 'warn')
        log_fn('(游戏字体为日文静态图集, 无对应日式写法的简体字无法绕过; '
               '根治需给游戏打字体补丁)')
    else:
        log_fn('字体覆盖: 译文所有字符均在游戏字体范围内, 无缺字', 'ok')
    return missing
