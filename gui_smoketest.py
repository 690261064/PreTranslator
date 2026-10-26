# -*- coding: utf-8 -*-
"""GUI 离屏冒烟测试: 实例化完整界面, 触发新增交互路径, 断言关键行为。
不启动网络任务线程 (mock _net_and_dlx), 不弹真实模态框 (mock messagebox)。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import pt_gui
from pt_core import config, games, runlog

# ---- 准备: 假游戏目录 + 完成标记 + 断点缓存
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_smoke_tmp')
import shutil
shutil.rmtree(BASE, ignore_errors=True)
GROOT = os.path.join(BASE, 'MyGame')
TD = os.path.join(GROOT, 'BepInEx', 'Translation', 'zh-CN')
os.makedirs(TD, exist_ok=True)
with open(os.path.join(TD, runlog.MARKER_NAME), 'w', encoding='utf-8') as f:
    f.write('{"at": "2026-10-27 00:00", "pairs": 3}')

cfg = config.load()
cfg['games'] = {GROOT: {}}
cfg['deepl_keys'] = 'sample-free-key:fx'

root = tk.Tk()
root.withdraw()
pt_gui._apply_theme(root)
app = pt_gui.App.__new__(pt_gui.App)  # 跳过 __init__ 的 _startup 线程
app.root = root
app.cfg = cfg
app.q = __import__('queue').Queue()
app.games = {}
app.dlx_inst = {'direct': None, 'proxy': None}
app.net = {'direct': (None, ''), 'proxy': (None, '')}
app.ip = {'direct': '', 'proxy': ''}
app.api_ok = 0
app.job = None
app.job_stop = __import__('threading').Event()
app._prog_t0 = None
app._scan_running = False
app._quota = None
app._hinted_429 = False
app._build()
root.update()

# ---- 1. 新列与新按钮存在
cols = app.tree['columns']
assert 'state' in cols, cols
assert str(app.btn_batch['text']) == '▶▶ 批量预翻译'
app._set_job_ui(False)
assert not app.btn_batch.instate(['disabled'])
app._set_job_ui(True)
assert app.btn_batch.instate(['disabled']) and app.btn_start.instate(['disabled'])
app._set_job_ui(False)

# ---- 2. _fill_tree 显示完成标记状态
g = games.detect_at(GROOT)
assert g is not None, 'smoke game not detected'
app._fill_tree([g])
vals = app.tree.item(GROOT, 'values')
assert vals[4] == '✓ 已完成', vals
print('fill_tree state column OK:', vals)

# ---- 3. dispatch: quota -> 通道行显示额度, 80% 告警; 429 日志触发密钥引导
assert app._hinted_429 is False
app._dispatch(('quota', 850000, 1000000))
app._dispatch(('log', 'warn', '通道[x] 429限速, 暂停 12s'))
assert app._quota == (850000, 1000000)
assert '额度 850000/1000000 (85%)' in str(app.lbl_channels['text'])
assert app._hinted_429 is True  # api_ok==0 时 429 日志触发引导
assert '官方免费密钥' in str(app.lbl_api['text'])
print('quota + 429 hint OK:', app.lbl_channels['text'])

# ---- 4. apikey dispatch -> lbl_api 更新
app._dispatch(('apikey', 2))
assert '2 个有效' in str(app.lbl_api['text'])
app._dispatch(('apikey', 0))
assert '无效' in str(app.lbl_api['text'])
app._dispatch(('apikey', 1))

# ---- 5. gstate -> 状态列更新
app._dispatch(('gstate', GROOT, '翻译中…'))
assert app.tree.set(GROOT, 'state') == '翻译中…'

# ---- 6. jobdone: perm 提示 + 字体高亮 + 配额入状态
app._dispatch(('jobdone', {
    'translated': 10, 'perm': 2, 'stats': {'hits429': 3, 'requests': 50,
                                           'penalty_trips': 1},
    'write': {'files': 2, 'new_lines': 8, 'kept': 0},
    'verify': {'ok': 8, 'bad': 0}, 'font': {'bake_blocked': True},
    'quota': [900000, 1000000], 'game': GROOT, 'lang': 'zh-CN'}))
prog = app.var_prog.get()
assert '永久失败 2 句' in prog and '不可烘焙' in prog, prog
print('jobdone hints OK:', prog)

# ---- 7. jobdone batch 分支
app._dispatch(('jobdone', {'batch': True, 'results': [('A', 'OK'), ('B', '已停止')]}))
assert '批量完成' in app.var_prog.get()

# ---- 8. 维护对话框可打开 (Toplevel 不进入 mainloop 也能构建)
pt_gui.messagebox = type('M', (), {'showinfo': staticmethod(lambda *a, **k: None),
                                   'showwarning': staticmethod(lambda *a, **k: None),
                                   'askyesno': staticmethod(lambda *a, **k: False),
                                   'showerror': staticmethod(lambda *a, **k: None)})
app._btn_maint()
assert app._known_cache_names()  # GROOT 对应的缓存名

# ---- 9. _btn_batch: 未选游戏时应弹提示而非崩溃 (messagebox 已 mock)
app._btn_batch()

# ---- 10. runlog.is_done / marker 与真实游戏目录联动
assert runlog.is_done(g, 'zh-CN')

root.destroy()
shutil.rmtree(BASE, ignore_errors=True)
print('GUI SMOKE OK')
