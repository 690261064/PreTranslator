# -*- coding: utf-8 -*-
"""静态 SDF 图集烘焙: 把中文字形直接焊进 TMP 图集, 不依赖运行时动态加字.

背景: 本作 Unity 2022.3 + TMP 1.4 的原生动态加字路径
(FontEngine.TryAddGlyphToTexture -> 原生 TryAddGlyphToTexture_Internal)
必定崩溃 (0xC0000005)。已排除: 图集纹理可读性、纹理对象本身、PointSize 字段、
中文字体替换、XUAT 钩子。因此改为在打包阶段把翻译用到的字符按 SDF 预烘焙进图集,
TMP 以 Static 模式渲染, 永不进入原生动态路径。

图集策略 (不引入多图集/多材质):
  原图集 W x H 的像素整块搬到新纹理的 (0,0), 新字形写入其下方的空白区,
  新纹理高 = H + 新增行数 (向上取 2 的幂)。m_AtlasWidth/Height 同步更新,
  原有 glyphRect 全部保持有效。
"""
import io
import math
import os
import struct
import sys

import numpy as np

from . import fontpatch as F

PAD = 5              # SDF 扩散半径 (与原资产 m_AtlasPadding 一致)
SUPERSAMPLE = 3      # 字形栅格超采样倍数
GUTTER = 1           # 图集内字形间距


# ---------------------------------------------------------------- 字体 / SDF
class GlyphBaker(object):
    """用 PIL/FreeType 栅格化 + 距离变换生成 TMP 规格的 SDF 字形位图。"""

    def __init__(self, font_bytes, em, pad=None):
        from PIL import ImageFont
        self._bio = io.BytesIO(font_bytes)
        self.em = int(em)
        self.hi_em = self.em * SUPERSAMPLE
        self.font = ImageFont.truetype(self._bio, self.hi_em)
        self.pad = int(PAD if pad is None else pad)
        self._adv_cache = {}

    def advance(self, ch):
        if ch not in self._adv_cache:
            self._adv_cache[ch] = self.font.getlength(ch) / SUPERSAMPLE
        return self._adv_cache[ch]

    def render(self, ch):
        """返回 (bitmap uint8 [h,w], metrics dict) 或 None (空白/无字形)。"""
        from PIL import Image, ImageDraw
        from scipy import ndimage

        hi = self.hi_em
        W = hi * 3
        H = hi * 3
        img = Image.new('L', (W, H), 0)
        dr = ImageDraw.Draw(img)
        bx, by = hi, hi * 2
        try:
            dr.text((bx, by), ch, font=self.font, fill=255, anchor='ls')
        except Exception:
            return None
        arr = np.asarray(img)
        ys, xs = np.nonzero(arr)
        if len(ys) == 0:
            return None
        l, t = int(xs.min()), int(ys.min())
        r, b = int(xs.max()) + 1, int(ys.max()) + 1

        # 有符号距离场 (高分辨率像素单位): 内部为正
        mask = arr > 127
        d_out = ndimage.distance_transform_edt(~mask)
        d_in = ndimage.distance_transform_edt(mask)
        sd_hi = d_in - d_out

        # 目标分辨率下的位图尺寸 (不含 padding)
        bw = max(1, int(math.ceil((r - l) / float(SUPERSAMPLE))))
        bh = max(1, int(math.ceil((b - t) / float(SUPERSAMPLE))))
        tw = bw + 2 * self.pad
        th = bh + 2 * self.pad

        # 目标像素中心映射回高分辨率坐标采样
        jj = np.arange(tw, dtype=np.float64)
        ii = np.arange(th, dtype=np.float64)
        xs_co = l - self.pad * SUPERSAMPLE + (jj + 0.5) * SUPERSAMPLE
        ys_co = t - self.pad * SUPERSAMPLE + (ii + 0.5) * SUPERSAMPLE
        gy, gx = np.meshgrid(ys_co, xs_co, indexing='ij')
        sd = ndimage.map_coordinates(sd_hi, [gy, gx], order=1, mode='nearest')
        sd = sd / float(SUPERSAMPLE)

        vals = 128.0 + 128.0 * (sd / float(self.pad or 1))
        bmp = np.clip(np.rint(vals), 0, 255).astype(np.uint8)
        # 位图边缘强制收敛到"完全在外", 避免相邻字形渗色
        bmp[:1, :] = 0
        bmp[-1:, :] = 0
        bmp[:, :1] = 0
        bmp[:, -1:] = 0

        metrics = {
            'width': float(tw),
            'height': float(th),
            'bearing_x': (l - bx) / float(SUPERSAMPLE) - self.pad,
            'bearing_y': (by - t) / float(SUPERSAMPLE) + self.pad,
            'advance': self.advance(ch),
        }
        return bmp, metrics


# ------------------------------------------------------------------- 打包
def _shelf_pack(sizes, width, height_min):
    """货架式装箱: sizes=[(w,h)...] 已按高降序; 返回 (placements, height)。"""
    placements = []
    x = y = 0
    row_h = 0
    maxw = 0
    for i, (w, h) in enumerate(sizes):
        if w > width:
            placements.append(None)
            continue
        if x + w > width:
            y += row_h + GUTTER
            x = 0
            row_h = 0
        placements.append((x, y))
        x += w + GUTTER
        row_h = max(row_h, h)
        maxw = max(maxw, x)
    total_h = y + row_h
    return placements, max(height_min, total_h)


def _next_pow2(v):
    p = 1
    while p < v:
        p <<= 1
    return p


def _find_atlas_settings(buf, fi_at, aw, ah):
    """在遗留 m_fontInfo 之后的字段区里找图集设置槽位.

    返回 (offset, m_AtlasPadding, m_AtlasRenderMode) 或 None。
    (m_AtlasWidth, m_AtlasHeight) 必然等于图集纹理当前宽高, 其后紧跟
    m_AtlasPadding (0..32) 与 m_AtlasRenderMode (几千量级)。"""
    end = min(len(buf), fi_at + 4096)
    for off in range(fi_at, end - 15, 4):
        w, hh, pd, rm = struct.unpack_from('<4i', buf, off)
        if w == aw and hh == ah and 0 <= pd <= 32 and 4000 <= rm <= 200000:
            return off, pd, rm
    return None


def _guess_em(raw, lay):
    """从现有字形的 advance 反推字面尺寸。

    不能直接取最大值: 前一次烘焙写进去的字形会带上其它字号的 advance,
    一旦被当成 em 就会让新旧字形大小不一致。CJK 字形的 advance 高度集中
    (整百上千个字同为 77), 所以取 90 分位再取整, 对个别异常值免疫。
    """
    advs = []
    for i in range(min(lay['glyph_n'], 4000)):
        try:
            v, = struct.unpack_from('<f', raw, lay['glyph_off'] + 4 + 52 * i + 20)
        except struct.error:
            break
        if 8.0 <= v <= 512.0:
            advs.append(v)
    if not advs:
        return 77
    advs.sort()
    return int(round(advs[int(len(advs) * 0.9) if len(advs) > 9 else -1]))


# ------------------------------------------------------------- 资产读写工具
def _read_stream_pixels(data_dir, tt):
    sd = tt.get('m_StreamData') or {}
    path = sd.get('path') or ''
    off = int(sd.get('offset') or 0)
    size = int(sd.get('size') or 0)
    if not path or size <= 0:
        return None, None, 0, 0
    p = os.path.join(data_dir, os.path.basename(path))
    if not os.path.isfile(p):
        return None, None, 0, 0
    with open(p, 'rb') as f:
        f.seek(off)
        data = f.read(size)
    if len(data) != size:
        return None, None, 0, 0
    return p, data, off, size


# ------------------------------------------------------------------- 主流程
def bake_tmp_atlas(assets_path, font_bytes, charset, log_fn=print,
                   em=None, dry=False):
    """把 charset 中缺失的字形烘焙进 assets 里的 TMP 图集.

    返回 dict(baked, skipped, atlas_w, atlas_h, resS_growth)."""
    import UnityPy

    res = {'baked': 0, 'skipped': 0, 'atlas_w': 0, 'atlas_h': 0,
           'resS_growth': 0, 'missing': []}
    data_dir = os.path.dirname(assets_path)
    env = UnityPy.load(assets_path)
    objs = {o.path_id: o for o in env.objects}
    tex_pids = {pid for pid, o in objs.items() if o.type.name == 'Texture2D'}

    # 选字形表最大的那个静态 TMP 资产作为烘焙目标
    best = None
    n_tmp = 0
    for pid, o in objs.items():
        if o.type.name != 'MonoBehaviour':
            continue
        try:
            raw = o.get_raw_data()
        except Exception:
            continue
        if b'SDF' not in raw[:64]:
            continue
        n_tmp += 1
        h = F._parse_tmp_header(raw)
        if not h or h['file_id'] != 0 or h['mode'] != 0:
            continue
        lay = F._tmp_layout(raw, h['face_end'], tex_pids, h['face'])
        if not lay:
            continue
        if best is None or lay['glyph_n'] > best[2]['glyph_n']:
            best = (pid, h, lay, raw)
    if not best:
        if n_tmp:
            log_fn('  %s: %d 个 TMP 资产都不是可烘焙的静态图集, 跳过'
                   % (os.path.basename(assets_path), n_tmp), 'info')
        return res
    pid, hdr, lay, raw = best
    log_fn('  烘焙目标: TMP 资产 pathID %d (face=%r, 现有字形 %d)'
           % (pid, hdr['face'], lay['glyph_n']))

    atlas_pid = lay['atlas_pid']
    tt_tex = objs[atlas_pid].read_typetree()
    aw = int(tt_tex.get('m_Width', 1024))
    ah = int(tt_tex.get('m_Height', 1024))
    res['atlas_w'], res['atlas_h'] = aw, ah
    fmt = int(tt_tex.get('m_TextureFormat', 1))
    if fmt != 1:
        log_fn('  图集纹理格式 %d 非 Alpha8, 放弃烘焙' % fmt, 'warn')
        return res
    stream_path, pixels, s_off, s_size = _read_stream_pixels(data_dir, tt_tex)
    if pixels is None or len(pixels) != aw * ah:
        log_fn('  读不到图集像素 (stream=%r size=%d), 放弃烘焙'
               % (stream_path, s_size), 'warn')
        return res

    # 现有字符集 / 字形表
    g_off, g_n = lay['glyph_off'], lay['glyph_n']
    used = []
    for i in range(lay['used_n']):
        used.append(struct.unpack_from('<4i', raw, lay['used_cnt_off'] + 4 + 16 * i))
    existing = set()
    max_gi = -1
    for i in range(lay['char_n']):
        et, uc, gi = struct.unpack_from('<3i', raw, lay['char_start'] + 16 * i)
        if et == 1:
            existing.add(uc)
    for i in range(g_n):
        v, = struct.unpack_from('<i', raw, g_off + 4 + 52 * i)
        if v > max_gi:
            max_gi = v
    next_gi = max_gi + 1

    want = []
    for ch in sorted(set(charset)):
        cp = ord(ch)
        if cp in existing or ch.isspace():
            continue
        want.append(ch)
    log_fn('  待烘焙字符: %d (已有 %d)' % (len(want), len(existing)))

    if em is None:
        em = _guess_em(raw, lay)
    settings = _find_atlas_settings(raw, lay['fi_at'], aw, ah)
    pad = settings[1] if settings else PAD
    baker = GlyphBaker(font_bytes, em, pad=pad)
    log_fn('  字面尺寸 em=%d, SDF 半径 pad=%d' % (em, baker.pad))
    baked = []
    miss = []
    for ch in want:
        r = baker.render(ch)
        if r is None:
            miss.append(ch)
            continue
        bmp, m = r
        baked.append((ch, bmp, m))
    res['missing'] = miss
    res['skipped'] = len(miss)
    if not baked:
        log_fn('  没有可烘焙的字形', 'warn')
        return res

    # 装箱: 字形写进原图集下方的空白区
    order = sorted(range(len(baked)), key=lambda i: -baked[i][1].shape[0])
    sizes = [(baked[i][1].shape[1], baked[i][1].shape[0]) for i in order]
    place, extra_h = _shelf_pack(sizes, aw, 1)
    new_h = _next_pow2(ah + extra_h)
    log_fn('  图集 %dx%d -> %dx%d (新增行 %d, 装载率 %.0f%%)'
           % (aw, ah, aw, new_h, extra_h,
              100.0 * sum(w * h for w, h in sizes) / float(aw * extra_h)
              if extra_h else 0))

    canvas = np.zeros((new_h * aw,), dtype=np.uint8)
    canvas[:ah * aw] = np.frombuffer(pixels, dtype=np.uint8)
    bmp2d = canvas.reshape(new_h, aw)

    glyph_bytes = []
    char_bytes = []
    new_rects = []
    for slot, idx in enumerate(order):
        ch, bmp, m = baked[idx]
        pos = place[slot]
        if pos is None:
            miss.append(ch)
            continue
        px, py = pos
        ry = ah + py
        # Unity 的纹理数据行序与 PIL 图像行序相反, 位图需上下翻转后再写入,
        # 否则烘焙出的新字形在游戏内会上下倒置(原有字形不受影响)。
        bmp2d[ry:ry + bmp.shape[0], px:px + bmp.shape[1]] = bmp[::-1]
        gi = next_gi + len(glyph_bytes)
        glyph_bytes.append(struct.pack(
            '<i5f4ifii', gi,
            m['width'], m['height'], m['bearing_x'],
            m['bearing_y'], m['advance'],
            px, ry, bmp.shape[1], bmp.shape[0],
            1.0, 0, 0))
        char_bytes.append(struct.pack('<iiif', 1, ord(ch), gi, 1.0))
        new_rects.append((px, ry, bmp.shape[1], bmp.shape[0]))
    res['baked'] = len(glyph_bytes)
    res['atlas_h'] = new_h

    # 重建字符表: 原有条目 + 新条目, 按 unicode 归并
    old_chars = []
    for i in range(lay['char_n']):
        b = raw[lay['char_start'] + 16 * i:lay['char_start'] + 16 * (i + 1)]
        et, uc = struct.unpack_from('<2i', b)
        old_chars.append((uc, b))
    new_chars = []
    for b in char_bytes:
        uc, = struct.unpack_from('<i', b, 4)
        new_chars.append((uc, b))
    merged = sorted(old_chars + new_chars, key=lambda t: t[0])
    char_blob = b''.join(b for _, b in merged)

    glyph_blob = raw[g_off + 4:g_off + 4 + 52 * g_n] + b''.join(glyph_bytes)

    head = raw[:g_off]
    body = (head + struct.pack('<i', g_n + len(glyph_bytes)) + glyph_blob
            + struct.pack('<i', len(merged)) + char_blob)

    tail0 = raw[lay['atlas_list_cnt_off']:lay['used_cnt_off']]
    used_blob = b''.join(struct.pack('<4i', *r) for r in used)
    used_blob += b''.join(struct.pack('<4i', *r) for r in new_rects)
    legacy = raw[lay['fi_at']:]

    fi_at_new = len(body) + len(tail0) + 4 + len(used_blob) + 4
    out = bytearray()
    out += body
    out += tail0
    out += struct.pack('<i', len(used) + len(new_rects))
    out += used_blob
    out += struct.pack('<i', 0)          # free 矩形清空: 静态模式不再装箱
    out += legacy
    dims = _find_atlas_settings(out, fi_at_new, aw, ah)
    if dims is None:
        log_fn('  找不到 m_AtlasWidth/Height 槽位, 放弃', 'warn')
        return res
    dims_at = dims[0]
    struct.pack_into('<i', out, dims_at, aw)
    struct.pack_into('<i', out, dims_at + 4, new_h)
    log_fn('  m_AtlasWidth/Height 槽位 @%d: %dx%d -> %dx%d'
           % (dims_at, aw, ah, aw, new_h))
    if not F._parse_tmp_header(bytes(out)):
        log_fn('  重建后头部自检失败, 放弃', 'warn')
        return res

    new_pixels = bmp2d.tobytes()
    # .resS 每次都是"整块追加" (旧块留在文件里不回收), 所以增长量就是新图集字节数
    res['resS_growth'] = len(new_pixels)
    if dry:
        log_fn('  [dry] 未写盘 (预计 .resS +%.1f MB)'
               % (len(new_pixels) / 1048576.0))
        return res

    # 写 .resS (追加) + 改 Texture2D
    sidecar = stream_path + '.origlen'
    if os.path.isfile(sidecar):
        try:
            orig_len = int(open(sidecar).read().strip())
        except Exception:
            orig_len = 0
        if orig_len > 0 and os.path.getsize(stream_path) > orig_len:
            with open(stream_path, 'rb+') as f:
                f.truncate(orig_len)
            log_fn('  .resS 回滚到原始长度 %d B' % orig_len)
    else:
        with open(sidecar, 'w') as f:
            f.write(str(os.path.getsize(stream_path)))
    with open(stream_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        new_off = f.tell()
    with open(stream_path, 'ab') as f:
        f.write(new_pixels)
    tt_tex['m_Width'] = aw
    tt_tex['m_Height'] = new_h
    tt_tex['m_CompleteImageSize'] = len(new_pixels)
    sd = dict(tt_tex.get('m_StreamData') or {})
    sd['offset'] = new_off
    sd['size'] = len(new_pixels)
    tt_tex['m_StreamData'] = sd
    objs[atlas_pid].save_typetree(tt_tex)

    objs[pid].set_raw_data(bytes(out))
    data = env.file.save()
    bak = assets_path + '.bak-PreTranslator'
    if not os.path.exists(bak):
        with open(assets_path, 'rb') as f, open(bak, 'wb') as g:
            g.write(f.read())
    with open(assets_path, 'wb') as f:
        f.write(data)
    log_fn('  已写入 %s (+%d B), .resS 追加 %.1f MB'
           % (os.path.basename(assets_path), len(data) - os.path.getsize(bak)
              if os.path.exists(bak) else len(data),
              len(new_pixels) / 1048576.0))
    return res
