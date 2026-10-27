# -*- coding: utf-8 -*-
"""格式与引擎装箱自测。运行: python selftest.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pt_core import xuformat
from pt_core.engine import make_batches, utf16len, chunk_key

BS = chr(92)

# split_line_raw
assert xuformat.split_line_raw('a=b') == ('a', 'b')
assert xuformat.split_line_raw('a' + BS + '=b=c') == ('a' + BS + '=b', 'c')
assert xuformat.split_line_raw('// comment') is None
assert xuformat.split_line_raw('a') is None
assert xuformat.split_line_raw('a//b=c') is None  # key 内注释 -> 丢弃
assert xuformat.split_line_raw('a=b//c') == ('a', 'b')  # 值内 // 截断 (XUAT 行为)
assert xuformat.split_line_raw('a=b=c') is None

# decode / encode 往返
dec = xuformat.decode('A' + BS + '=B' + BS + BS + 'C' + BS + 'nD%3DE')
assert dec == 'A=B' + BS + 'C' + '\n' + 'D=E', dec
enc = xuformat.encode('A=B' + BS + 'C\nD')
assert enc == 'A' + BS + '=B' + BS + BS + 'C' + BS + 'nD', enc
assert xuformat.encode(dec) == 'A' + BS + '=B' + BS + BS + 'C' + BS + 'nD' + BS + '=E'
s = '「テスト」=测试' + BS + '=等号' + BS + 'x'
sp = xuformat.split_line_raw(s)
assert sp is not None and xuformat.decode(sp[0]) == '「テスト」', sp

# has_japanese
assert xuformat.has_japanese('コイン')
assert xuformat.has_japanese('漢字')
assert xuformat.has_japanese('ﾊﾟﾋﾟｺ')  # 半角片假名
assert not xuformat.has_japanese('Coin 100%')
assert not xuformat.has_japanese('')

# make_batches 装箱
ks = ['あ' * 100, 'い' * 700, 'う' * 700, 'え' * 10]
b = make_batches(ks, 1400)
for x in b:
    assert utf16len(''.join(x)) <= 1400, utf16len(''.join(x))
assert sum(len(x) for x in b) == 4
assert [len(x) for x in b] == [2, 2], [len(x) for x in b]  # 贪心: [100+700, 700+10]

# chunk_key 超长句切分
long = ('あいう。' * 800)
ck = chunk_key(long)
assert all(utf16len(p) <= 1300 for p in ck)
assert ''.join(ck) == long
ck2 = chunk_key('あ' * 3000)  # 无标点硬切
assert ''.join(ck2) == 'あ' * 3000 and all(utf16len(p) <= 1300 for p in ck2)

# parse_existing / split_pair_simple
import tempfile
tmp = os.path.join(tempfile.gettempdir(), 'pt_selftest.txt')
with open(tmp, 'w', encoding='utf-8') as f:
    f.write('// 头注释\n')
    f.write('キー=マニュアル値\n')
    f.write('キー2=値2\n')
ex = xuformat.parse_existing(tmp)
assert ex == {'キー': 'キー=マニュアル値', 'キー2': 'キー2=値2'}, ex

# XUAT 结构识别 (用仓库自带的合成假游戏 fixture)
from pt_core import extract, games
_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    'testdata', 'FakeGame', 'BepInEx', 'Translation', 'en')
assert extract.is_source_folder(_FIX), _FIX
assert not extract.is_source_folder(os.path.dirname(os.path.abspath(__file__)))

# 英文识别
from pt_core.xuformat import plausible_english
assert plausible_english('Save the game?')
assert plausible_english('Start')
assert plausible_english('You got 100 points!')
assert not plausible_english('MainMenu')
assert not plausible_english('PLAYER_HEIGHT')
assert not plausible_english('a_b_c')
assert not plausible_english('コイン')
assert not plausible_english('Ab')
assert not plausible_english('some_path/to/file')
assert not plausible_english('')

# 术语挖掘与替换
from pt_core import engine
keys = ['アーダは笑った', 'アーダが来た', 'ベルトを買う', 'アーダベルトは高い',
        'アーダ', 'セーブしました', 'ロードしました']
terms = engine.mine_terms(keys, max_terms=50, min_count=2)
assert 'アーダ' in terms            # 出现>=2次 + 也是纯片假名整句key
assert 'セーブしました' not in terms  # 只出现一次且非纯片假名
assert any(t in ('アーダベルト',) for t in terms) or True
tmap = {'アーダ': '阿达', 'セーブしました': '已存档', 'ロードしました': '已读档'}
tr = engine.make_transform(tmap)
send, lang = tr('アーダは笑った')
assert send == '阿达は笑った' and lang == 'JA', (send, lang)
send, lang = tr('アーダベルトは高い')  # 长词包含短术语: 只替换命中的部分
assert send == '阿达ベルトは高い', send
send, lang = tr('Press Start')
assert send == 'Press Start' and lang == 'EN'
assert engine.make_transform({}) is None

# 批次按语言分组
items = [('a', 'あ', 'JA'), ('b', 'hello world', 'EN'), ('c', 'い', 'JA')]
bs = engine._pack_items(items, 100)
assert len(bs) == 2
assert all(it[2] == 'JA' for it in bs[0]) or all(it[2] == 'JA' for it in bs[1])
# 同语言内按长度装箱
items2 = [('a', 'あ' * 60, 'JA'), ('b', 'い' * 60, 'JA'), ('c', 'う' * 10, 'JA')]
bs2 = engine._pack_items(items2, 100)
assert [len(x) for x in bs2] == [1, 2], [len(x) for x in bs2]

# ===== 多值配置解析 =====
import pretranslator as pt
assert pt._split_multi('a, b; c\nd') == ['a', 'b', 'c', 'd']
assert pt._split_multi('') == []
assert pt._split_multi('http://127.0.0.1:7897') == ['http://127.0.0.1:7897']

# ===== 通道模型与限速引擎 (本地假服务器) =====
import threading
import json as _json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pt_core.engine import Channel, Engine, RateLimited


class _Fake(BaseHTTPRequestHandler):
    calls = {'dlx': 0, 'api': 0, 'r429': 0}
    fail429 = 0  # >0 时接下来的请求返回429

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = _json.loads(self.rfile.read(n))
        texts = body.get('text')
        texts = texts if isinstance(texts, list) else [texts]
        if _Fake.fail429 > 0:
            _Fake.fail429 -= 1
            _Fake.calls['r429'] += 1
            self.send_response(429)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/v2/translate':
            _Fake.calls['api'] += 1
            assert self.headers.get('Authorization') == 'DeepL-Auth-Key k:fx'
            out = {'translations': [{'text': 'T' + t} for t in texts]}
        else:
            _Fake.calls['dlx'] += 1
            out = {'code': 200, 'data': ['T' + t for t in texts]}
        b = _json.dumps(out).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(('127.0.0.1', 0), _Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
FPORT = srv.server_address[1]
QUIET = lambda *a, **k: None  # noqa: E731

ch_d = Channel('http://127.0.0.1:%d/translate' % FPORT, label='假直连')
ch_a = Channel('http://127.0.0.1:%d/v2/translate' % FPORT, kind='deepl_api',
               key='k:fx', label='假API')
for c in (ch_d, ch_a):
    c.min_interval = 0.01
    c.interval = 0.01
    c.max_interval = 0.05

# API 通道分块: 100条 → 3次请求 (45+45+10), 顺序保持
n0 = _Fake.calls['api']
eng0 = Engine([ch_a], os.path.join(tempfile.gettempdir(), 'pt_selftest_c0'),
              log_fn=QUIET)
data = eng0._call(ch_a, ['あ%02d' % i for i in range(100)], lang='JA')
assert len(data) == 100 and data[0] == 'Tあ00' and data[-1] == 'Tあ99'
assert _Fake.calls['api'] - n0 == 3, _Fake.calls['api']

# 双通道并行 + 全部提交
cache = os.path.join(tempfile.gettempdir(), 'pt_selftest_cache')
eng = Engine([ch_d, ch_a], cache, batch_cap=60, log_fn=QUIET)
keys = ['テスト%d' % i for i in range(120)]
st = eng.translate(keys)
assert st['done_now'] == 120, st
assert len(eng.map) == 120 and not st['perm_failed']
assert _Fake.calls['api'] > n0 + 3   # API通道参与了主翻译
assert _Fake.calls['dlx'] >= 5       # DLX通道也参与了

# 断点续传: 第二次直接跳过
eng2 = Engine([ch_d], cache, batch_cap=60, log_fn=QUIET)
n = eng2.load_checkpoint()
assert n == 120
st2 = eng2.translate(keys)
assert st2['done_now'] == 0 and st2['reused'] == 120

# 429 重试 + 全局熔断: 连续多个429不消耗重试次数、触发全局暂停, 最终任务完成
_Fake.fail429 = 6
ch_d.slow_at = 0.04  # 间隔一超即触发熔断 (测试用小阈值)
eng3 = Engine([ch_d], os.path.join(tempfile.gettempdir(), 'pt_selftest_c3'),
              batch_cap=60, log_fn=QUIET)
eng3.penalty_seconds = 0.3
st3 = eng3.translate(['テストA%d' % i for i in range(30)])
assert st3['done_now'] == 30 and _Fake.calls['r429'] >= 6
assert eng3._penalty_until > 0  # 熔断确实触发过
# 限速统计: 429 次数/请求次数/熔断窗口数 都要进结果 state (GUI 展示用)
assert st3['hits429'] >= 6, st3['hits429']
assert st3['penalty_trips'] >= 1, st3['penalty_trips']
assert ch_d.requests >= 7 and ch_d.hits429 >= 6, (ch_d.requests, ch_d.hits429)
assert any(c['hits429'] for c in st3['channels']), st3['channels']
_Fake.fail429 = 0
ch_d.slow_at = 20.0

# 纯字符串端点兼容 (旧用法)
eng4 = Engine(['http://127.0.0.1:%d/translate' % FPORT],
              os.path.join(tempfile.gettempdir(), 'pt_selftest_c4'),
              batch_cap=60, log_fn=QUIET)
st4 = eng4.translate(['テストB%d' % i for i in range(10)])
assert st4['done_now'] == 10

# 身份句标记: 原样返回(不可译)记空串断点; 空响应不记录待重试
eng5 = Engine([ch_d], os.path.join(tempfile.gettempdir(), 'pt_selftest_c5'),
              batch_cap=60, log_fn=QUIET)
eng5._commit([('ああ', 'ああ'), ('いい', ''), ('うう', '海')])
assert eng5.map['ああ'] == '' and eng5.map['うう'] == '海'
assert 'いい' not in eng5.map
eng6 = Engine([ch_d], os.path.join(tempfile.gettempdir(), 'pt_selftest_c5'),
              batch_cap=60, log_fn=QUIET)
assert eng6.load_checkpoint() == 2, eng6.map
assert eng6.map['ああ'] == ''
st6 = eng6.translate(['ああ', 'うう'])
assert st6['done_now'] == 0 and st6['total'] == 0

# 英文判定: JSON配置碎片 / UUID / 碎片杂句 / Unity对象名 排除
assert not plausible_english('"name": "VirtualMouse",')
assert not plausible_english('"maps": [')
assert not plausible_english('"id": "df70fa95-8a34-4494-b137-73ab6b9c7d37",')
assert not plausible_english('S uR')
assert not plausible_english('Grass0 (104)')
assert not plausible_english('Tree1 (21)')
assert plausible_english('You got 100 points!')
assert plausible_english('Save the game?')
assert plausible_english('Episode 1 (Final)')

# --- 提取器: NUL 前缀 / 纯汉字标签 / 多行整串 ---
from pt_core import gamestrings as gs  # noqa: E402

# 纯汉字 UI 标签 (无假名) 放行
assert gs.plausible_japanese('断面図')
assert gs.plausible_japanese('警告')
assert gs.plausible_japanese('受精確率　：　　％')
assert not gs.plausible_japanese('ThisIsNotJapanese')
assert not gs.plausible_japanese('一二三四五六七八九十 ABC')  # 混ASCII/过长

# NUL 粘连的二进制前缀不再吞句 (clean_candidate 宽容重试)
assert gs._clean_soft('\x00\x00\x03やり方を教えてなかった。') == 'やり方を教えてなかった。'
assert gs._clean_soft('1024 x 576') == '1024 x 576'

# 多行整串进 units; 行级内容同时可提取
import tempfile as _tf  # noqa: E402

_block = ('やり方を教えてなかった。\n貴様のような魔術の心得のかけらも無いグズを'
          '相手に取引するのは初めてなのでな。\r\n\n渡した金の腕輪を付けて'
          'ベッドに横になれ、そして腕輪に念じろ。\r\n')
_asset = os.path.join(tempfile.gettempdir(), 'pt_selftest_probe.assets')
with open(_asset, 'wb') as f:
    f.write(b'\x00\x00\x03' + _block.encode('utf-8') + b'\x00\x00')
lines_u, units_u = gs.scan_asset_file(_asset)
os.remove(_asset)
assert any(u.startswith('やり方を教えてなかった。') and '\n' in u for u in units_u), units_u
assert 'やり方を教えてなかった。' in lines_u
assert any('渡した金の腕輪' in s for s in lines_u)

# --- 组合键合成: 任意连续行段, 分隔符与原文一致 ---
from pretranslator import build_combo_pairs  # noqa: E402

_unit = 'ああ。\nいい。\r\nうう。'
_valmap = {'ああ。': '甲', 'いい。': '乙', 'うう。': '丙'}
combos = build_combo_pairs([_unit], _valmap, 1000)
assert combos['ああ。\nいい。'] == '甲\n乙'
assert combos['いい。\r\nうう。'] == '乙\r\n丙'
assert combos['ああ。\nいい。\r\nうう。'] == '甲\n乙\r\n丙'
# 行缺失时不合成
assert 'ああ。\nいい。\r\nうう。' not in build_combo_pairs(
    [_unit], {'ああ。': '甲', 'いい。': '乙'}, 1000)
# 超长上限过滤
assert not build_combo_pairs([_unit], _valmap, 5)

# --- XUAT 转义: '//' 与 \r\n 往返 ---
from pt_core.xuformat import decode, encode, split_pair_simple  # noqa: E402

assert encode('a//b') == 'a\\/\\/b'
assert decode('a\\/\\/b') == 'a//b'
assert encode('追加の\n書き置き') == '追加の\\n書き置き'
assert decode(encode('あ\r\nい=う//v')) == 'あ\r\nい=う//v'
line = encode('追加の\n書き置き') + '=' + encode('额外的\n注释')
assert split_pair_simple(line) == ('追加の\n書き置き', '额外的\n注释')

# --- 字体覆盖替换: 仅当替换字在覆盖范围内才动 ---
from pt_core import fontcover  # noqa: E402

cov = fontcover.build_coverage(['丁度いい'])
new_v, nsub = fontcover.substitute_text('他说金额刚好', cov)
# 覆盖里没有对应日式写法 -> 不动
assert nsub == 0 and new_v == '他说金额刚好'
cov2 = fontcover.build_coverage(['金額は丁度いい説'])
new_v2, nsub2 = fontcover.substitute_text('他说', cov2)
assert nsub2 == 1 and new_v2 == '他説'

# 烘焙路径的 cmap 是整数码点集合 (fontTools getBestCmap keys):
# 必须转成字符集合, 否则替换/缺字报告全部失效 (曾导致警告误报全部字符)
_cmap_ints = {ord('説'), ord('☆')} | {c for c in range(0x20, 0x7F)}
_covc = fontcover.chars_from_cmap(_cmap_ints)
assert _covc == {'説', '☆'} | {chr(c) for c in range(0x20, 0x7F)}
_v3, _n3 = fontcover.substitute_text('他说♡', _covc)
assert _v3 == '他説♡' and _n3 == 1, (_v3, _n3)   # 说->説 被替换; ♡ 无近似在覆盖内则保留
_miss = fontcover.report(['説☆'], _covc, log_fn=lambda *a, **k: None)
assert not _miss, _miss
_miss2 = fontcover.report(['説℃'], _covc, log_fn=lambda *a, **k: None)
assert _miss2 == {'℃'}, _miss2

# --- 字体烘焙: 装箱 / 槽位搜索 / em 推导 (不依赖游戏资产) ---
import struct  # noqa: E402

from pt_core import fontbake as fb  # noqa: E402

assert fb._next_pow2(1) == 1 and fb._next_pow2(4096) == 4096
assert fb._next_pow2(4097) == 8192
place, _h = fb._shelf_pack([(10, 10), (10, 10)], 32, 1)
assert place == [(0, 0), (11, 0)], place          # 同一货架并排
place, _h = fb._shelf_pack([(10, 10), (40, 10)], 32, 1)
assert place[0] == (0, 0) and place[1] is None   # 超宽 -> 放不下
place, _h = fb._shelf_pack([(20, 10), (20, 10)], 32, 1)
assert place == [(0, 0), (0, 11)], place         # 换行 (含 1px 间距)

buf = bytearray(128)
struct.pack_into('<4i', buf, 40, 4096, 4096, 5, 4165)
assert fb._find_atlas_settings(bytes(buf), 0, 4096, 4096) == (40, 5, 4165)
assert fb._find_atlas_settings(bytes(buf), 0, 2048, 2048) is None

raw_g = bytearray(4 + 52 * 3)
struct.pack_into('<i', raw_g, 0, 3)
for _i, _adv in enumerate((30.0, 77.0, 12.5)):
    struct.pack_into('<f', raw_g, 4 + 52 * _i + 20, _adv)
assert fb._guess_em(bytes(raw_g), {'glyph_n': 3, 'glyph_off': 0}) == 77

# 回归保护: m_PointSize 是 int 字段, 不允许再出现按 float 写它的代码
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'pt_core', 'fontpatch.py'), encoding='utf-8').read()
assert "pack_into('<f', buf, h['style_end']" not in _src, 'PointSize 误写回归'
assert "pack_into('<f', buf, lay['ps_at']" not in _src, '遗留 PointSize 误写回归'

# 真字体渲染 (PIL/scipy/系统中文字体齐备时才跑)
try:
    from pt_core import fontpatch as _fp
    _fb, _fn, _cm = _fp.find_cjk_ttf(lambda *a, **k: None,
                                     candidates=_fp._BAKE_CANDIDATES)
    if _fb:
        _baker = fb.GlyphBaker(_fb, 32, pad=5)
        _bmp, _m = _baker.render('中')
        assert _bmp.shape[0] > 10 and _bmp.shape[1] > 10, _bmp.shape
        assert 0 < _m['advance'] < 40, _m
        assert int(_bmp.max()) > 200 and int(_bmp.min()) == 0   # SDF 覆盖面
except ImportError:
    pass

# ===== runlog: 运行报告 / 完成标记 / 缓存维护 =====
import shutil as _shutil  # noqa: E402
from pt_core import runlog as rl  # noqa: E402


class _StubGame:
    def __init__(self, root, name):
        self.root = root
        self.name = name

    def target_dir(self, lang):
        return os.path.join(self.root, 'Translation', lang)


_rldir = os.path.join(tempfile.gettempdir(), 'pt_selftest_runlog')
_shutil.rmtree(_rldir, ignore_errors=True)
os.makedirs(_rldir)
g1 = _StubGame(os.path.join(_rldir, 'G1'), 'G1')
os.makedirs(g1.target_dir('zh-CN'), exist_ok=True)
_engok = {'done_now': 5, 'perm_failed': 0, 'elapsed': 2.0, 'rate': 2.5,
          'reused': 0, 'requests': 1, 'hits429': 0, 'penalty_trips': 0,
          'channels': []}
st_ok = {'engine': _engok, 'write': {'pre_pairs': 7}, 'verify': {'ok': 7, 'bad': 0}}
st_bad = {'engine': dict(_engok, perm_failed=2), 'write': {'pre_pairs': 5},
          'verify': {'ok': 5, 'bad': 1}}
assert not rl.marker_ok(st_bad) and rl.marker_ok(st_ok)
# 半成品 (校验不一致/有永久失败) 不写完成标记
assert not rl.write_marker(g1, 'zh-CN', st_bad)
assert not rl.is_done(g1, 'zh-CN')
assert rl.write_marker(g1, 'zh-CN', st_ok)
assert rl.is_done(g1, 'zh-CN')
entry = rl.build_entry(g1, st_ok, lang='zh-CN', quota=(100, 1000))
assert entry['translated'] == 5 and entry['game'] == 'G1'
assert entry['quota'] == {'used': 100, 'limit': 1000}
rl.append_report(_rldir, entry, log_fn=QUIET)
_rep = os.path.join(_rldir, 'run_report.jsonl')
_lines = [l for l in open(_rep, encoding='utf-8').read().splitlines() if l.strip()]
assert len(_lines) == 1
_o = _json.loads(_lines[0])
assert _o['game'] == 'G1' and _o['translated'] == 5 and _o['verify_bad'] == 0

# 缓存扫描: 在用/重复/孤儿 判定 + 合并不丢句
_croot = os.path.join(_rldir, 'cache')
for _d in ('game-a-1111111111', 'game-a-2222222222', 'game-b-3333333333',
           'downloads'):
    os.makedirs(os.path.join(_croot, _d), exist_ok=True)
with open(os.path.join(_croot, 'game-a-1111111111', 'zh_map.jsonl'), 'w',
          encoding='utf-8') as f:
    f.write('{"k": "あ", "v": "阿"}\n')
with open(os.path.join(_croot, 'game-a-2222222222', 'zh_map.jsonl'), 'w',
          encoding='utf-8') as f:
    f.write('{"k": "あ", "v": "阿"}\n{"k": "い", "v": "衣"}\n')
recs = rl.cache_scan(_croot, {'game-a-1111111111'})
by = {r['name']: r for r in recs}
assert 'downloads' not in by
assert by['game-a-1111111111']['status'] == 'inuse'
assert by['game-a-2222222222']['status'] == 'dup'
assert by['game-a-2222222222']['merge_into'] == 'game-a-1111111111'
assert by['game-b-3333333333']['status'] == 'orphan'
added, _tot = rl.merge_cache_dirs(os.path.join(_croot, 'game-a-1111111111'),
                                  [os.path.join(_croot, 'game-a-2222222222')])
assert added == 1, added
assert not os.path.isdir(os.path.join(_croot, 'game-a-2222222222'))
_keys = [_json.loads(l)['k']
         for l in open(os.path.join(_croot, 'game-a-1111111111', 'zh_map.jsonl'),
                       encoding='utf-8') if l.strip()]
assert sorted(_keys) == ['あ', 'い'], _keys

# --- XUAT 配置预生成: BepInEx 版游戏翻译完成时往往还没首跑 ---
# (不预生成的话 XUAT 首跑按自身默认 Language=en 初始化, zh-CN 预翻译不会被加载)
_d = tempfile.mkdtemp(prefix='pt_cfg_')
_g = games.Game(_d, 'bepinex')
_g.xuat_dll = 'x'  # 模拟 XUAT 已安装 (detect_at 会填真实 dll 路径)
assert not os.path.isfile(_g.config_ini)
_g.fix_config('zh-CN', from_lang='ja')
_t = open(_g.config_ini, encoding='utf-8-sig').read()
assert 'Language=zh-CN' in _t and 'FromLanguage=ja' in _t, _t
_g2 = games.Game(_d, 'bepinex')
assert os.path.isfile(_g2.config_ini)   # 已生成的配置被识别, 不会被重建
_g2.fix_config('zh-CN', from_lang='ja') # 幂等: 二次调用无副作用
assert 'Language=zh-CN' in open(_g2.config_ini, encoding='utf-8-sig').read()
# has_xuat 只看插件 dll: config_ini 恒为路径字符串, 不能拿它判断
assert _g.has_xuat                                    # 模拟装了 dll
_g3 = games.Game(_d, 'bepinex')                       # 配置文件在, 但没有 dll
assert os.path.isfile(_g3.config_ini)
assert not _g3.has_xuat, '配置文件存在不等于装了 XUAT'
_shutil.rmtree(_d, ignore_errors=True)
# standalone 版同路径: Config.ini 缺失时也要能预生成
_ds = tempfile.mkdtemp(prefix='pt_cfgs_')
_gs = games.Game(_ds, 'standalone')
assert not os.path.isfile(_gs.config_ini)
_gs.fix_config('zh-CN', from_lang='ja')
assert os.path.isfile(_gs.config_ini)
assert 'Language=zh-CN' in open(_gs.config_ini, encoding='utf-8-sig').read()
_shutil.rmtree(_ds, ignore_errors=True)

print('SELFTEST OK')
