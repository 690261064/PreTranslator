# -*- coding: utf-8 -*-
"""TMP 静态图集游戏的字体根治补丁。

问题: 日文游戏的 TMP_FontAsset 通常是静态图集, 只烘焙了原文日文用到的字形,
译文里的简体字显示为方框; XUAT 的 OverrideFontTextMeshPro 需要 TMP>=3.2 或
游戏内同名资产, 老游戏两条路都不通。

两条路线 (mode 参数):

  'bake' (默认, 推荐) 静态 SDF 烘焙 —— 见 fontbake.py
      只把"缺的那几百个简体字"用系统字体渲成 SDF, 写进现有图集的下半区,
      图集高度翻倍, 新像素追加到 .resS 末尾; 游戏保持
      m_AtlasPopulationMode=0(Static)。运行时 TMP 根本不会调用原生
      FontEngine.TryAddGlyphToTexture, 因此不受 TextCore 动态加字崩溃影响
      (实测 Unity 2022.3 + 本类 TMP 资产在动态路线上必崩 0xC0000005,
      崩在 native 对字体面子对象 [font+0x58] 的空指针虚调用)。
      原图集原有字形一个不动, 风格差异只在新增字上。

  'dynamic' (仅诊断) 旧路线:
      1. 把 TMP 资产改为【动态图集】并把源字体指向本游戏内嵌的 Font 对象,
         再把该 Font 的 TTF 换成系统中文字体;
      2. 图集纹理翻 【Readable】位 (打包时 Read/Write Disabled 时 TMP 报
         "Please make the texture readable", 动态加字全部失败);
      3. 清空烘焙的字形表/字符表 (旧字形与运行时新字形索引冲突会张冠李戴)。
      注意: 很多 Unity 2022.3 游戏在这条路线上启动即崩, 只用来验证引擎行为。

布局按内容签名定位 (不依赖固定偏移, 带多重校验):
  [字形数G][字形条目 52B×G][字符数C][字符条目 16B×C]
  [图集纹理计数][纹理PPtr][索引][多页bool][清空bool]
  [used矩形 计数+条目][free矩形 计数+条目][遗留 m_fontInfo…]

依赖 UnityPy + fontTools (烘焙另需 numpy/pillow/scipy)。patch 前自动备份原文件
为 *.bak-PreTranslator, 且 bake 每次执行前会先从备份还原, 保证可重复执行。
"""
import os
import shutil
import struct

_TMP_VERSIONS = (b'1.1.0', b'1.2.0', b'1.3.0', b'1.4.0', b'2.1.0', b'3.0.0')
# CJK 能力检查用字符 (简体专用字, 日文字体通常没有)
_CJK_PROBE = '额释说读这'
# 候选顺序即优先级: SimHei 自带粗重字重、且覆盖 ♪♡ 等游戏常用符号,
# 与原游戏粗圆体的观感差距最小; 其余为回退。
_FONT_CANDIDATES = ('simhei.ttf', 'msyh.ttc', 'msyh.ttf', 'SIMYOU.TTF',
                    'Deng.ttf', 'simsun.ttc')
# 烘焙进 SDF 图集时用的字体序: 需要字重尽量贴近原图集 (Rounded M+ 1c Bold),
# 实测 msyhbd (雅黑粗体) 的笔画粗细最接近, SimHei 明显偏细。
_BAKE_CANDIDATES = ('msyhbd.ttc', 'msyh.ttf', 'msyh.ttc', 'simhei.ttf',
                    'Dengb.ttf', 'NotoSansSC-VF.ttf', 'SIMYOU.TTF')
_FONT_LABELS = {'simhei.ttf': 'SimHei', 'msyh.ttc': 'Microsoft YaHei',
                'msyh.ttf': 'Microsoft YaHei', 'msyhbd.ttc': 'Microsoft YaHei Bold',
                'SIMYOU.TTF': 'YouYuan', 'Deng.ttf': 'DengXian',
                'Dengb.ttf': 'DengXian Bold', 'NotoSansSC-VF.ttf': 'Noto Sans SC',
                'simsun.ttc': 'SimSun'}
_CHAR_ENTRY = 16           # 字符表条目 (m_ElementType=1, unicode, glyphIndex, scale)
_GLYPH_ENTRY = 52          # 字形表条目 (index + 5 float metrics + rect + scale + 8B)


def _load_cmap(ttf_bytes):
    """返回字体 cmap 字符集合 (失败返回 None)。"""
    try:
        import io
        from fontTools.ttLib import TTFont
        tf = TTFont(io.BytesIO(ttf_bytes), fontNumber=0, lazy=True)
        return set(tf.getBestCmap().keys())
    except Exception:
        return None


def _covers_cjk(ttf_bytes):
    cmap = _load_cmap(ttf_bytes)
    if not cmap:
        return False
    return all(ord(c) in cmap for c in _CJK_PROBE)


def find_cjk_ttf(log_fn=print, charset=None, candidates=None, tag=''):
    """从系统字体目录按优先级取一个中文字体 (TTC 抽取第一字形)。
    charset 给定时按覆盖率把关 (缺字超过 5% 的候选跳过)。
    candidates 给定时改写候选顺序 (烘焙用 _BAKE_CANDIDATES)。
    返回 (字节, 名称, cmap集合或None)。"""
    cands = tuple(candidates) if candidates else _FONT_CANDIDATES
    windir = os.environ.get('WINDIR', r'C:\Windows')
    fontdir = os.path.join(windir, 'Fonts')
    fallback = None
    for fn in cands:
        p = os.path.join(fontdir, fn)
        if not os.path.isfile(p):
            continue
        try:
            raw = open(p, 'rb').read()
        except OSError:
            continue
        name = _FONT_LABELS.get(fn, fn)
        if raw[:4] == b'ttcf':  # TTC 集合: 抽取第一个字体为 TTF
            try:
                from fontTools.ttLib import TTFont
                import io
                tf = TTFont(p, fontNumber=0)
                buf = io.BytesIO()
                tf.save(buf)
                raw = buf.getvalue()
            except Exception as e:
                log_fn('字体 %s 抽取失败: %s' % (fn, e), 'warn')
                continue
        cmap = _load_cmap(raw)
        if not cmap or not all(ord(c) in cmap for c in _CJK_PROBE):
            log_fn('字体 %s 不含简体字形, 跳过' % fn, 'info')
            continue
        if charset:
            miss = [c for c in charset if ord(c) > 0x7F and ord(c) not in cmap]
            if len(miss) > 0.05 * max(1, len(charset)):
                log_fn('字体 %s 对游戏文本缺字 %d/%d, 跳过'
                       % (fn, len(miss), len(charset)), 'info')
                continue
            if fallback is None or len(miss) < fallback[2]:
                fallback = (raw, name, len(miss))
            if len(miss) == 0 or fn == cands[0]:
                break       # 优先级最高的候选达标即用
        else:
            return raw, name, cmap
    if fallback:
        return fallback[0], fallback[1], _load_cmap(fallback[0])
    return None, None, None


def _parse_tmp_header(raw):
    """按版本字符串锚点解析 TMP_FontAsset 头部。

    布局: [材质PPtr][4B杂项][m_Version串][m_SourceFontFileGUID串]
          [m_SourceFontFile PPtr(12B)][m_AtlasPopulationMode int][4B][FaceInfo.m_Name串]…
    返回 dict 或 None (不像 TMP_FontAsset)。
    """
    vpos = -1
    ver = None
    for ver in _TMP_VERSIONS:
        vpos = raw.find(ver)
        if vpos >= 0:
            break
    if vpos < 0:
        return None
    if vpos < 4 or struct.unpack_from('<i', raw, vpos - 4)[0] != len(ver):
        return None
    g = (vpos + len(ver) + 3) & ~3          # GUID 长度字段位置
    if g + 4 > len(raw):
        return None
    guid_len = struct.unpack_from('<i', raw, g)[0]
    if not 8 <= guid_len <= 64:             # m_SourceFontFileGUID
        return None
    g2 = (g + 4 + guid_len + 3) & ~3        # 源字体 PPtr 位置
    if g2 + 24 > len(raw):
        return None
    file_id, = struct.unpack_from('<i', raw, g2)
    path_id, = struct.unpack_from('<q', raw, g2 + 4)
    mode, = struct.unpack_from('<i', raw, g2 + 12)
    extra, = struct.unpack_from('<i', raw, g2 + 16)
    nlen, = struct.unpack_from('<i', raw, g2 + 20)
    if extra != 0 or not 1 <= nlen <= 64 or g2 + 24 + nlen > len(raw):
        return None
    try:
        face = raw[g2 + 24:g2 + 24 + nlen].decode('utf-8')
    except UnicodeDecodeError:
        return None
    if not all(32 <= ord(c) < 127 for c in face):
        return None
    face_end = (g2 + 24 + nlen + 3) & ~3    # 字符串按 4 字节对齐
    if face_end + 4 > len(raw):
        return None
    slen, = struct.unpack_from('<i', raw, face_end)     # m_StyleName 串长
    if not 0 <= slen <= 64 or face_end + 4 + slen > len(raw):
        return None
    style_end = (face_end + 4 + slen + 3) & ~3   # 随后是 FaceInfo 18 个 float
    return {'ver': ver.decode(), 'pptr_at': g2, 'file_id': file_id,
            'path_id': path_id, 'mode': mode, 'face': face,
            'face_end': face_end, 'style_end': style_end}


def _scan_same_file_tex_pptrs(raw, tex_pids):
    """扫描 raw 中所有指向本文件 Texture2D 的 PPtr 位置。"""
    out = []
    for o in range(0, len(raw) - 12, 4):
        fid, = struct.unpack_from('<i', raw, o)
        if fid != 0:
            continue
        pid, = struct.unpack_from('<q', raw, o + 4)
        if pid in tex_pids:
            out.append((o, pid))
    return out


def _find_char_table_back(raw, x_atlas):
    """从图集纹理 PPtr 前的 m_AtlasTextures 计数向前回扫字符表。

    布局 (TMP 2.1 过渡版, 由游戏 DLL 元数据校准):
      [G][字形条目 52B×G][C][字符条目 16B×C = (m_ElementType=1, unicode,
      glyphIndex, scale=1.0)][m_AtlasTextures 计数][PPtr]…
    返回 dict 或 None。"""
    list_cnt_off = x_atlas - 4
    if list_cnt_off < 0:
        return None
    n_lists, = struct.unpack_from('<i', raw, list_cnt_off)
    if not 1 <= n_lists <= 8:
        return None
    fid, = struct.unpack_from('<i', raw, x_atlas)
    if fid != 0:
        return None
    o = list_cnt_off - _CHAR_ENTRY
    u_prev = 0x110000
    while o >= 0:
        et, u, g = struct.unpack_from('<III', raw, o)
        s, = struct.unpack_from('<f', raw, o + 12)
        if not (et == 1 and 0 <= u < u_prev and g < 200000
                and abs(s - 1.0) < 0.02):
            break
        u_prev = u
        o -= _CHAR_ENTRY
    s_start = o + _CHAR_ENTRY
    c, rem = divmod(list_cnt_off - s_start, _CHAR_ENTRY)
    if c < 16 or rem:
        return None
    cnt_off = s_start - 4
    if cnt_off < 0:
        return None
    cnt, = struct.unpack_from('<i', raw, cnt_off)
    if cnt != c:
        return None
    return {'char_cnt_off': cnt_off, 'char_start': s_start, 'char_n': c,
            'atlas_list_cnt_off': list_cnt_off}


def _find_glyph_head(raw, face_end, char_cnt_off):
    """在 FaceInfo 之后找字形表: [G][52B 条目×G] 恰好结束于字符表计数。
    返回 (G偏移, G) 或 None。"""
    for o in range(face_end, char_cnt_off - 12, 4):
        g, = struct.unpack_from('<i', raw, o)
        if not 16 <= g <= 60000:
            continue
        span = char_cnt_off - o - 4
        if span <= 0 or span % _GLYPH_ENTRY:
            continue
        if span // _GLYPH_ENTRY != g:
            continue
        idx0, = struct.unpack_from('<I', raw, o + 4)
        if idx0 >= 200000:
            continue
        return o, g
    return None


def _tmp_layout(raw, face_end, tex_pids, face=''):
    """定位图集纹理 PPtr / 字形表 / 字符表 / used+free 矩形表。"""
    for x, pid in _scan_same_file_tex_pptrs(raw, tex_pids):
        if x <= face_end:
            continue
        ct = _find_char_table_back(raw, x)
        if not ct:
            continue
        char_cnt_off = ct['char_cnt_off']
        gt = _find_glyph_head(raw, face_end, char_cnt_off)
        if not gt:
            continue
        g_off, g = gt
        # PPtr 之后: [m_AtlasTextureIndex int][m_IsMultiAtlas bool]
        #            [m_ClearDynamicDataOnBuild bool][used 计数]
        used_cnt_off = x + 24
        if used_cnt_off + 4 > len(raw):
            continue
        used_n, = struct.unpack_from('<i', raw, used_cnt_off)
        if not 0 <= used_n <= 60000:
            continue
        used_end = used_cnt_off + 4 + 16 * used_n
        if used_end + 4 > len(raw):
            continue
        free_n, = struct.unpack_from('<i', raw, used_end)
        if not 0 <= free_n <= 60000:
            continue
        # 遗留 m_fontInfo 应紧随其后, 首字段为字体族名字符串 —— 终极校验
        # (新版 TMP 序列化不再填充遗留字段, 空串同样接受)
        fi_at = used_end + 4 + 16 * free_n
        if fi_at + 4 > len(raw):
            continue
        nlen, = struct.unpack_from('<i', raw, fi_at)
        if nlen == 0:
            pass
        elif 1 <= nlen <= 64:
            try:
                fi_name = raw[fi_at + 4:fi_at + 4 + nlen].decode('utf-8')
            except UnicodeDecodeError:
                continue
            if face and fi_name != face:
                continue
        else:
            continue
        # 遗留 m_fontInfo: [名字串][PointSize float][Scale float]…
        # (2.1 过渡版的动态加字按遗留 PointSize 取光栅化尺寸 —— 为零同样崩溃)
        # 用 FaceInfo 的 LineHeight (glyph 表前第 4 个 float) 交叉验证真伪
        ps_at = (fi_at + 4 + nlen + 3) & ~3
        legacy_ps = None
        if ps_at + 8 <= len(raw) and g_off >= 60:
            ps, = struct.unpack_from('<f', raw, ps_at)
            lh, = struct.unpack_from('<f', raw, g_off - 60)
            if 8 <= ps <= 512 and 0.9 < lh / ps < 1.6:
                legacy_ps = ps
        return {'atlas_pptr_at': x, 'atlas_pid': pid,
                'fi_at': fi_at, 'ps_at': ps_at, 'legacy_ps': legacy_ps,
                'glyph_off': g_off, 'glyph_n': g,
                'char_cnt_off': char_cnt_off,
                'char_start': ct['char_start'], 'char_n': ct['char_n'],
                'atlas_list_cnt_off': ct['atlas_list_cnt_off'],
                'used_cnt_off': used_cnt_off, 'used_n': used_n,
                'used_end': used_end, 'free_n': free_n}
    return None


def _clear_tables(raw, lay, atlas_w, atlas_h, multi_atlas=True):
    """清空烘焙字形表/字符表/used 矩形, free 矩形重置为整张图集,
    打开多页图集开关 —— 运行时把全部字形按需从补丁字体重新生成。

    tail (atlas_list_cnt_off 起) 布局:
      [m_AtlasTextures 计数][PPtr 12B][m_AtlasTextureIndex]
      [m_IsMultiAtlas bool][m_ClearDynamicDataOnBuild bool]
      [m_UsedGlyphRects 计数+条目][m_FreeGlyphRects 计数+条目][其余字段…]"""
    g_off = lay['glyph_off']
    t = raw[lay['atlas_list_cnt_off']:]
    used_cnt_t = 28                       # 4+12+4+4+4: 计数/PPtr/索引/两个 bool
    free_cnt_t = used_cnt_t + 4 + 16 * lay['used_n']
    out = bytearray()
    out += raw[:g_off]
    out += struct.pack('<i', 0)           # m_GlyphTable 数 = 0
    out += struct.pack('<i', 0)           # m_CharacterTable 数 = 0
    out += t[:used_cnt_t]                 # m_AtlasTextures 计数 + PPtr + 索引 + 多页开关
    out += struct.pack('<i', 0)           # m_UsedGlyphRects 数 = 0
    out += struct.pack('<i', 1)           # m_FreeGlyphRects 数 = 1
    out += struct.pack('<4i', 0, 0, atlas_w, atlas_h)
    out += t[free_cnt_t + 4 + 16 * lay['free_n']:]
    if multi_atlas:
        out[g_off + 8 + 20] = 1           # m_IsMultiAtlasTexturesEnabled = true
    return bytes(out)


def has_dynamic_tmp(game):
    """游戏里是否已有处于动态模式的 TMP 图集 (补丁过或本来就动态)。"""
    try:
        import UnityPy
    except ImportError:
        return False
    if game.managed_dir:
        data_dir = os.path.dirname(game.managed_dir)
    else:
        # IL2CPP 游戏没有 Managed 目录, 用 <exe名>_Data 目录定位
        import glob as _glob
        cands = sorted(_glob.glob(os.path.join(game.root, '*_Data')))
        data_dir = cands[0] if cands else None
    if not data_dir or not os.path.isdir(data_dir):
        return False
    for fn in os.listdir(data_dir):
        if not fn.endswith('.assets'):
            continue
        try:
            env = UnityPy.load(os.path.join(data_dir, fn))
            for o in env.objects:
                if o.type.name != 'MonoBehaviour':
                    continue
                try:
                    raw = o.get_raw_data()
                except Exception:
                    continue
                if b'SDF' not in raw[:64]:
                    continue
                h = _parse_tmp_header(raw)
                if h and h['file_id'] == 0 and h['path_id'] != 0 and h['mode'] == 1:
                    return True
        except Exception:
            continue
    return False


def _restore_baks(data_dir, log_fn=print):
    """烘焙前把上次补丁过的资源还原成原版, 让重复执行幂等。

    不还原的话第二次烘焙会在"已经加高过一倍的图集"上再加高, 图集会一路
    4096x8192 -> 16384 -> 32768, 而且 em 也会被上次写入的字形带偏。
    原始备份 (--.bak-PreTranslator) 是永远保留的, 所以还原是无损的。
    """
    n = 0
    for fn in sorted(os.listdir(data_dir)):
        if not fn.endswith('.bak-PreTranslator'):
            continue
        src = os.path.join(data_dir, fn)
        dst = os.path.join(data_dir, fn[:-len('.bak-PreTranslator')])
        try:
            if os.path.getsize(src) <= 0:
                continue
            shutil.copyfile(src, dst)
            n += 1
        except OSError as e:
            log_fn('还原 %s 失败: %s' % (fn, e), 'warn')
    if n:
        log_fn('烘焙前已把 %d 个上次补丁过的资源还原为原版 (保证可重复执行)' % n,
               'info')
    return n


def patch_game_font(game, log_fn=print, charset=None, mode='bake',
                    font_file=None, dry=False):
    """给游戏内静态 TMP 图集补上中文字形。

    mode='bake'    : 静态 SDF 烘焙 (默认)。把缺字渲成 SDF 写进现有图集,
                     游戏保持 m_AtlasPopulationMode=0, 运行时永不调用原生
                     TryAddGlyphToTexture —— 这条路在 Unity 2022 + 这套 TMP
                     资产上实测必崩, 静态烘焙完全绕开它。
    mode='dynamic' : 旧路线。静态图集改动态 + 翻纹理 readable + 清空烘焙表,
                     由 TMP 运行时按需加字。仅用于确认目标引擎支持动态加字
                     的场合; 不支持时表现为启动即 0xC0000005。
    font_file      : 指定 TTF/TTC 路径; 缺省按候选表从系统字体目录挑。
    dry            : 只算不改盘 (烘焙路线支持)。

    返回 dict: {mode, patched, baked, skipped, bake_blocked, font_bytes,
                font_name, cmap}。bake_blocked=True 表示该游戏有 TMP 资产
                但没有一张可烘焙的静态图集 —— 已自动走保底方案
                (译文缺字替换为日式写法), 界面应提醒用户。
    """
    res = {'mode': mode, 'patched': 0, 'baked': 0, 'skipped': 0,
           'bake_blocked': False, 'font_bytes': None, 'font_name': None,
           'cmap': None}
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        log_fn('未安装 UnityPy (pip install UnityPy fonttools), 跳过字体补丁',
               'warn')
        return res
    if game.managed_dir:
        data_dir = os.path.dirname(game.managed_dir)
    else:
        # IL2CPP 游戏没有 Managed 目录, 用 <exe名>_Data 目录定位
        import glob as _glob
        cands = sorted(_glob.glob(os.path.join(game.root, '*_Data')))
        data_dir = cands[0] if cands else None
    if not data_dir or not os.path.isdir(data_dir):
        log_fn('未找到游戏 Data 目录, 跳过字体补丁', 'warn')
        return res
    charset = set(charset or ())

    cands = _BAKE_CANDIDATES if mode == 'bake' else _FONT_CANDIDATES
    if font_file:
        font_bytes, font_name = _load_font_file(font_file, log_fn)
        cmap = _load_cmap(font_bytes) if font_bytes else None
    else:
        font_bytes, font_name, cmap = find_cjk_ttf(log_fn, charset or None,
                                                   candidates=cands)
    if not font_bytes:
        log_fn('系统未找到可用的中文字体, 跳过字体补丁', 'warn')
        return res
    res['font_bytes'] = font_bytes
    res['font_name'] = font_name
    res['cmap'] = cmap or set()
    if charset:
        covered = sum(1 for c in charset if ord(c) in res['cmap'])
        log_fn('字体补丁(%s): 使用 %s (%.1f MB, 覆盖游戏文本 %d/%d 字)'
               % (mode, font_name, len(font_bytes) / 1048576.0, covered,
                  len(charset)))
    else:
        log_fn('字体补丁(%s): 使用 %s (%.1f MB)'
               % (mode, font_name, len(font_bytes) / 1048576.0))

    if mode == 'bake' and not dry:
        _restore_baks(data_dir, log_fn)
    for fn in sorted(os.listdir(data_dir)):
        if not fn.endswith('.assets'):
            continue
        path = os.path.join(data_dir, fn)
        try:
            if mode == 'bake':
                r = _bake_assets_file(path, font_bytes, charset, log_fn, dry)
                if r['baked']:
                    log_fn('字体烘焙 %s: %d 字 (图集 %dx%d, .resS +%.1f MB)'
                           % (fn, r['baked'], r['atlas_w'], r['atlas_h'],
                              r['resS_growth'] / 1048576.0), 'ok')
                    if r['missing']:
                        log_fn('  %s 缺字 %d 个 (字体不含): %s'
                               % (fn, len(r['missing']),
                                  ''.join(r['missing'][:20])), 'warn')
                res['baked'] += r['baked']
                res['skipped'] += r['skipped']
                if r['baked']:
                    res['patched'] += 1
            else:
                n = _patch_assets_file(path, font_bytes, font_name, log_fn,
                                       clear=True, multi_atlas=True)
                if n:
                    log_fn('字体补丁 %s: %d 处修改 (动态图集/readable/清空烘焙表)'
                           % (fn, n), 'ok')
                    res['patched'] += n
        except Exception as e:
            log_fn('字体补丁 %s 失败: %s' % (fn, e), 'err')
            continue
    if not res['patched']:
        log_fn('未发现需要补丁的 TMP 图集 (可能已补丁过)', 'info')
    # 有 TMP 资产但全都不是可烘焙的静态图集 -> 标记出来 (GUI 高亮提示)
    res['bake_blocked'] = (mode == 'bake' and not dry
                           and res['baked'] == 0 and res['skipped'] > 0)
    return res


def _bake_assets_file(path, font_bytes, charset, log_fn, dry):
    """对单个 .assets 走静态烘焙; 返回 fontbake 的结果 dict。"""
    from . import fontbake    # 延迟导入: fontbake 反向依赖本模块的解析函数
    return fontbake.bake_tmp_atlas(path, font_bytes, charset, log_fn, dry=dry)


def _load_font_file(path, log_fn):
    """读取用户指定的字体文件 (TTC 取第一套字形)。"""
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        log_fn('指定的字体无法读取 (%s): %s' % (path, e), 'warn')
        return None, None
    if raw[:4] == b'ttcf':
        try:
            import io
            from fontTools.ttLib import TTFont
            tf = TTFont(path, fontNumber=0)
            buf = io.BytesIO()
            tf.save(buf)
            raw = buf.getvalue()
        except Exception as e:
            log_fn('字体 %s 抽取失败: %s' % (path, e), 'warn')
            return None, None
    name = os.path.splitext(os.path.basename(path))[0]
    return raw, name


def _pick_font(objs, font_bytes):
    """找内嵌 Font 对象; 若其内嵌字体与目标不一致返回 (pid, True)。"""
    for pid, o in objs.items():
        if o.type.name != 'Font':
            continue
        try:
            d = o.read()
        except Exception:
            continue
        fd = bytes(getattr(d, 'm_FontData', b'') or b'')
        if not fd:
            continue
        if fd[:65536] != font_bytes[:65536] or len(fd) != len(font_bytes):
            return pid, True
    return None, False


def _flip_readable(obj, log_fn):
    """把 Texture2D 的 m_IsReadable 翻为 True (TMP 运行时写图集的前提)。"""
    tt = obj.read_typetree()
    if tt.get('m_IsReadable'):
        return False
    tt['m_IsReadable'] = True
    obj.save_typetree(tt)
    log_fn('  图集纹理 "%s" (%dx%d): Readable -> True'
           % (tt.get('m_Name'), tt.get('m_Width'), tt.get('m_Height')))
    return True


def _patch_assets_file(path, font_bytes, font_name, log_fn, clear=True,
                       multi_atlas=True):
    import UnityPy
    env = UnityPy.load(path)
    objs = {o.path_id: o for o in env.objects}
    tex_pids = {pid for pid, o in objs.items()
                if o.type.name == 'Texture2D'}
    font_pid, font_needs_swap = _pick_font(objs, font_bytes)
    if not font_pid:
        return 0
    nchanged = 0
    for pid, o in objs.items():
        if o.type.name != 'MonoBehaviour':
            continue
        try:
            raw = o.get_raw_data()
        except Exception:
            continue
        if b'SDF' not in raw[:64]:
            continue
        h = _parse_tmp_header(raw)
        if not h or h['file_id'] != 0:
            continue
        if h['mode'] not in (0, 1):
            continue
        if h['mode'] == 0 and h['path_id'] != 0:
            continue    # 静态但已带源字体指针的不明资产, 不动
        new_raw, changed = raw, False
        if h['mode'] == 0:
            buf = bytearray(raw)
            # m_SourceFontFile -> 本文件内嵌 Font 对象 (运行时光栅化的源)
            struct.pack_into('<q', buf, h['pptr_at'] + 4, font_pid)
            struct.pack_into('<i', buf, h['pptr_at'] + 12, 1)   # 动态图集
            new_raw, changed = bytes(buf), True
            log_fn('  TMP 图集 "%s" (pathID %d): 静态 -> 动态, '
                   '源字体 -> Font(pathID %d)' % (h['face'], pid, font_pid))
        lay = _tmp_layout(new_raw, h['face_end'], tex_pids, h['face'])
        # 注意: 这里曾经"修"过 FaceInfo.m_PointSize 和遗留 m_fontInfo.PointSize,
        # 把 float 写进按 int 序列化的槽位, 结果把一个本来健康的字段写坏
        # (77 -> 1117388800), 崩溃反而从"空纹理虚调用"变成"坏指针引用计数释放"。
        # 已确认 FaceInfo.m_PointSize 是 int, 原值本就正确, 一律不动。
        if lay:
            if _flip_readable(objs[lay['atlas_pid']], log_fn):
                changed = True
            if clear:
                try:
                    tt_tex = objs[lay['atlas_pid']].read_typetree()
                    aw = int(tt_tex.get('m_Width', 1024))
                    ah = int(tt_tex.get('m_Height', 1024))
                except Exception:
                    aw = ah = 1024
                cleared = _clear_tables(new_raw, lay, aw, ah,
                                        multi_atlas=multi_atlas)
                if cleared != new_raw:
                    new_raw = cleared
                    changed = True
                    log_fn('  TMP 图集 (pathID %d): 清空烘焙字形 %d / 字符 %d / '
                           'used 矩形 %d (统一由补丁字体生成)'
                           % (pid, lay['glyph_n'], lay['char_n'], lay['used_n']))
        elif h['mode'] == 0:
            # 刚转为动态但布局识别失败 (其他 TMP 版本): 尽力翻本文件内
            # 引用的图集纹理 readable; 不碰本来就动态的第三方示例资产
            for x, tpid in _scan_same_file_tex_pptrs(new_raw, tex_pids):
                try:
                    if _flip_readable(objs[tpid], log_fn):
                        changed = True
                except Exception:
                    continue
        if not changed:
            continue
        # 回读自检: 头部仍可解析
        if not _parse_tmp_header(new_raw):
            log_fn('  TMP (pathID %d) 补丁后自检失败, 跳过该对象' % pid,
                   'warn')
            continue
        o.set_raw_data(new_raw)
        nchanged += 1
    if not nchanged:
        return 0
    if font_needs_swap:
        tt = objs[font_pid].read_typetree()
        tt['m_FontData'] = font_bytes
        tt['m_FontNames'] = [font_name]
        objs[font_pid].save_typetree(tt)
        log_fn('  Font 对象 (pathID %d) 内嵌字体 -> %s (%.1f MB)'
               % (font_pid, font_name, len(font_bytes) / 1048576.0))
    bak = path + '.bak-PreTranslator'
    if not os.path.exists(bak):
        with open(path, 'rb') as f, open(bak, 'wb') as g:
            g.write(f.read())
        log_fn('  已备份原文件 -> %s' % os.path.basename(bak))
    data = env.file.save()
    with open(path, 'wb') as f:
        f.write(data)
    return nchanged
