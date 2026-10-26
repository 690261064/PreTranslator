# -*- coding: utf-8 -*-
"""PreTranslator 图形界面 (tkinter, 无第三方依赖)。"""
import os
import queue
import shutil
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk

from pt_core import config, dlx, extract, games, netcheck, runlog
import pretranslator

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
FONT = 'Microsoft YaHei UI'
C_OK, C_BAD, C_WARN, C_IDLE = '#15803d', '#c62828', '#b26a00', '#8a8a8a'
BG = '#eef1f6'      # 窗口底色
CARD = '#ffffff'    # 卡片底色
BORD = '#d7dce5'    # 卡片描边
ACC = '#2563eb'     # 主色 (蓝)
ACC_D = '#1d4ed8'   # 主色深
ACC_L = '#dbeafe'   # 主色浅 (横幅副标题)
STRIPE = '#f3f6fb'  # 列表斑马纹
TXT = '#1f2937'
MUT = '#6b7280'
AMBER = '#b45309'

HELP_TEXT = """【翻译通道与额度】
· 官方免费 API (强烈推荐): 到 deepl.com 注册 API Free, 密钥 (形如 xxxx:fx) 填入
  「API密钥」框, 多个用逗号分隔。每号每月 50 万字符起 (实测 2026-10 注册的账号为
  100 万, 以启动时显示的实际额度为准)。限速宽松, 实测 20+ 句/秒, 是匿名通道的
  20 倍左右; 界面会实时显示本月已用额度, 超过 80% 会提醒。
· 匿名 DLX 通道 (没有密钥时的兜底): DeepL 按出口 IP/客户端特征限流。每条通道自动
  节流, 遇 429 自动等待并恢复; 连续限速会全体暂停约 2 分钟 (熔断), 不会把可翻句子
  误判为失败。跑完后日志会给出「请求 N 次 / 429 共 M 次 / 熔断 K 次」的统计。
  若没有密钥且频繁 429, 界面会主动提示去申请免费密钥。
· 多代理: 「代理地址」逗号分隔填多个 → 每个代理各起一条 DLX 通道
  (Clash 可给不同节点组开不同 mixed-port 来分流出口)。

【批量预翻译】
· 列表按住 Ctrl/Shift 可多选, 点「▶▶ 批量预翻译」按顺序整批跑。
· 每个游戏完整跑完且校验通过后, 会在其翻译目录写完成标记 (.pt_done.json);
  批量再跑时自动跳过, 半成品 (有遗留句/校验不一致) 不会被标记, 会重跑补齐。
· 单个游戏跑完同样会写标记。强制重翻 (勾选) 时忽略标记与断点全部重来。

【断点与遗留句】
· 翻译进度按句存在工具目录 cache/<游戏名-hash>/zh_map.jsonl, 停止/中断后
  再点一次「▶ 开始预翻译」只补缺口, 不重复花钱。
· 有「永久失败」句 (超长/异常句子) 时状态栏会提示, 同样再点一次开始即可补翻。

【缓存维护】
· 「缓存维护…」列出全部断点缓存目录及状态 (在用 / 重复 / 孤儿), 可一键合并
  重复项 (不丢句子)、删除孤儿 (游戏已删除/移动后残留)。

【名词译名一致性】
· 勾选「名词术语统一」: 自动挖掘高频片假名名词先统一译名再翻译, 全文人名一致。
· 「术语表…」可手写「日文原文=固定译名」(每行一条, // 注释), 优先级高于机器译名;
  修改后对已有翻译需勾选「强制重翻」才生效。

【自带中文的游戏】(如《空洞骑士》《丝之歌》)
· 选中后点「✕ 移除所选(不再扫描)」, 之后扫描不再出现; 「恢复忽略的游戏…」可找回。
· 只是不再显示, 不会动游戏文件。

【常见问题】
· 游戏还有零星日文? 动态拼接的句子 XUAT 查不到, 游戏内在线端点 (Bing) 会兜底。
  重跑一次预翻译可大幅改善: 工具现在按"整串+连续行组合键"提取, 命中率更高。
· 想改某句译文? 直接编辑游戏 Translation/zh-CN/ 下的文件即可
  (ReloadTranslationsOnFileChange=True 时保存即热生效)。
· IL2CPP 游戏首次启动黑屏较久是 BepInEx 在生成 interop, 只发生一次, 属正常。
· 中文显示方框 (缺字)? 「缺字修复(TMP图集烘焙)」勾选后, 工具会把译文用到的字
  直接渲染成 SDF 字形烘焙进游戏字体图集: 图集按需加高、新字形追加到 .resS、
  字符/字形/占用矩形表同步重建。游戏保持静态字体模式, 启动即显示中文,
  运行时不会触发引擎的加字接口 (那条路在部分 Unity 版本上会直接崩)。
  原文件自动备份 .bak-PreTranslator; 「选择字体…」可指定 TTF/TTC,
  缺省自动挑雅黑粗体 (字重最接近原图集)。
  若日志提示「图集不是可烘焙的静态图集」, 工具会自动改用保底方案 (译文缺字
  替换为游戏字体覆盖的日式写法), 此时指定一款 TTF 字体再强制重翻或许更好。
· 每次预翻译完成后, 工具目录的 run_report.jsonl 追加一行本次运行的统计
  (句数/速度/429次数/额度消耗), 可用文本编辑器查看长期趋势。
· 分享给朋友: 点「导出分享包」生成脱密 zip (不含你的路径/缓存), 对方解压即用。

【同类工具与经验】(GitHub 调研)
· XUAT 本体没有预扫描/批量模式 (issue #639), 预翻译只能由外部工具写它的缓存。
· 桥接类工具 (Bridging-Software 等) 需要游戏开着实时翻; Sugoi / 本地大模型端点
  只是换离线引擎, 翻译仍是运行时逐句 — 与本工具的离线缓存互为补充。
· 本工具路线 = 启动前批量提取 (翻译包/程序集/资源/IL2CPP元数据) + 多通道机翻
  + 写回 XUAT 缓存 + 往返校验, 全程离线, 游戏启动即加载。
"""


def _apply_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('.', background=BG, foreground=TXT, font=(FONT, 9), borderwidth=0)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=TXT)
    style.configure('Muted.TLabel', background=BG, foreground=MUT)
    # 卡片容器
    style.configure('Card.TLabelframe', background=CARD, bordercolor=BORD,
                    relief='solid', borderwidth=1)
    style.configure('Card.TLabelframe.Label', background=CARD, foreground=ACC_D,
                    font=(FONT, 9, 'bold'))
    style.configure('Card.TFrame', background=CARD)
    style.configure('Card.TLabel', background=CARD, foreground=TXT)
    style.configure('CardMuted.TLabel', background=CARD, foreground=MUT)
    # 按钮
    style.configure('TButton', background=CARD, foreground=TXT, padding=(9, 3),
                    bordercolor=BORD, focusthickness=0)
    style.map('TButton', background=[('active', '#e8edf6'), ('pressed', '#dbe3f0')],
              bordercolor=[('active', '#b9c4d8')])
    style.configure('Accent.TButton', background=ACC, foreground='#ffffff')
    style.map('Accent.TButton',
              background=[('disabled', '#9db8ec'), ('active', ACC_D), ('pressed', ACC_D)],
              foreground=[('disabled', '#eef2ff')])
    style.configure('Danger.TButton', background='#fdecec', foreground=C_BAD)
    style.map('Danger.TButton', background=[('disabled', '#f4f4f5'), ('active', '#f9d9d9')],
              foreground=[('disabled', '#c7c7cc')])
    # 复选/输入
    style.configure('Card.TCheckbutton', background=CARD, foreground=TXT)
    style.map('Card.TCheckbutton', background=[('active', CARD)])
    style.configure('TCheckbutton', background=BG, foreground=TXT)
    style.map('TCheckbutton', background=[('active', BG)])
    style.configure('TEntry', fieldbackground='#fbfcfe', bordercolor='#c9d0dc',
                    lightcolor=BORD, darkcolor=BORD, padding=2)
    style.configure('TCombobox', fieldbackground='#fbfcfe', bordercolor='#c9d0dc',
                    arrowsize=12)
    # 列表
    style.configure('Treeview', background=CARD, fieldbackground=CARD, rowheight=26,
                    borderwidth=0, foreground=TXT)
    style.map('Treeview', background=[('selected', '#dbe7ff')],
              foreground=[('selected', TXT)])
    style.configure('Treeview.Heading', background='#eef2f9', foreground=ACC_D,
                    font=(FONT, 9, 'bold'), padding=(4, 4), borderwidth=0)
    style.map('Treeview.Heading', background=[('active', '#e2e9f5')])
    style.configure('TScrollbar', background='#e2e6ee', bordercolor=BG,
                    troughcolor=BG, arrowsize=12)
    # 进度条 / 滚动条 / 状态栏
    style.configure('Accent.Horizontal.TProgressbar', troughcolor='#e2e6ee',
                    background=ACC, lightcolor=ACC, darkcolor=ACC, borderwidth=0)
    style.configure('Status.TLabel', background=CARD, foreground=MUT, padding=(10, 4))


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.q = queue.Queue()
        self.games = {}
        self.dlx_inst = {'direct': None, 'proxy': None}
        self.net = {'direct': (None, ''), 'proxy': (None, '')}
        self.ip = {'direct': '', 'proxy': ''}
        self.api_ok = 0
        self.job = None
        self.job_stop = threading.Event()
        self._prog_t0 = None
        self._scan_running = False
        self._quota = None          # (已用, 上限) 官方API当月额度
        self._hinted_429 = False    # 本次任务是否已提示过"建议配密钥"
        self._build()
        self.root.after(150, self._poll)
        threading.Thread(target=self._startup, daemon=True).start()

    # ------------------------------------------------------------------ UI
    def _banner(self, r, row):
        c = tk.Canvas(r, height=56, highlightthickness=0, bd=0)
        c.grid(row=row, column=0, sticky='ew', padx=8, pady=(8, 0))
        c1, c2 = (37, 99, 235), (109, 58, 217)  # 蓝 -> 紫
        for i in range(0, 2400, 2):
            t = i / 2400.0
            col = '#%02x%02x%02x' % tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
            c.create_rectangle(i, 0, i + 3, 56, outline='', fill=col)
        c.create_text(14, 17, anchor='w', text='Unity 游戏预翻译工具',
                      fill='#ffffff', font=(FONT, 14, 'bold'))
        c.create_text(16, 40, anchor='w',
                      text='XUnity.AutoTranslator · 多通道机翻 · 游戏启动前离线生成全部翻译缓存',
                      fill=ACC_L, font=(FONT, 9))
        c.create_text(2384, 28, anchor='e', text='PreTranslator',
                      fill='#c7d2fe', font=(FONT, 10, 'bold'))

    def _build(self):
        r = self.root
        r.configure(bg=BG)
        r.columnconfigure(0, weight=1)
        r.rowconfigure(5, weight=3)
        r.rowconfigure(6, weight=4)
        pad = {'padx': 8, 'pady': 4}
        self._banner(r, 0)

        def card(row, text):
            f = ttk.Labelframe(r, text=text, style='Card.TLabelframe')
            f.grid(row=row, column=0, sticky='ew', **pad)
            return f

        # --- 翻译服务区
        f1 = card(1, ' 翻译服务 (DLX / DeepL 官方API, 后台静默运行) ')
        f1.columnconfigure(1, weight=1)
        self.var_dlx = tk.StringVar(value='(检测中...)')
        ttk.Label(f1, text='DLX程序:', style='Card.TLabel').grid(
            row=0, column=0, sticky='w', padx=10, pady=3)
        ttk.Entry(f1, textvariable=self.var_dlx, state='readonly').grid(
            row=0, column=1, sticky='ew', padx=6)
        bf1 = ttk.Frame(f1, style='Card.TFrame')
        bf1.grid(row=0, column=2, padx=8)
        ttk.Button(bf1, text='浏览…', width=8, command=self._pick_dlx).pack(side='left', padx=2)
        ttk.Button(bf1, text='启动/检查服务', width=14, command=self._btn_service).pack(side='left', padx=2)
        ttk.Button(bf1, text='导出分享包', width=12, command=self._btn_share).pack(side='left', padx=2)
        ttk.Label(f1, text='代理地址(可多个):', style='Card.TLabel').grid(
            row=1, column=0, sticky='w', padx=10)
        self.var_proxy = tk.StringVar(value=self.cfg.get('proxy_url', ''))
        ttk.Entry(f1, textvariable=self.var_proxy, width=28).grid(
            row=1, column=1, sticky='w', padx=6)
        self.lbl_net = ttk.Label(f1, text='网络: 检测中…', style='Card.TLabel')
        self.lbl_net.grid(row=1, column=2, sticky='w', padx=8)
        ttk.Label(f1, text='API密钥:', style='Card.TLabel').grid(
            row=2, column=0, sticky='w', padx=10)
        self.var_deepl_keys = tk.StringVar(value=self.cfg.get('deepl_keys', ''))
        ttk.Entry(f1, textvariable=self.var_deepl_keys, width=28).grid(
            row=2, column=1, sticky='w', padx=6)
        self.lbl_api = ttk.Label(f1, text='可选: DeepL官方免费API密钥, 逗号分隔多个',
                                 style='CardMuted.TLabel', foreground=MUT)
        self.lbl_api.grid(row=2, column=2, sticky='w', padx=8)
        self.lbl_channels = ttk.Label(f1, text='翻译通道: 未启动', style='Card.TLabel',
                                      foreground=C_IDLE)
        self.lbl_channels.grid(row=3, column=0, columnspan=3, sticky='w', padx=10,
                               pady=(0, 4))

        # --- 游戏列表区
        f2 = card(2, ' 本地 Unity 游戏 (BepInEx + XUnity.AutoTranslator) ')
        f2.columnconfigure(0, weight=1)
        f2.rowconfigure(1, weight=1)
        bar = ttk.Frame(f2, style='Card.TFrame')
        bar.grid(row=0, column=0, sticky='ew', padx=8, pady=4)
        ttk.Button(bar, text='重新扫描', command=self._btn_scan).pack(side='left', padx=2)
        ttk.Button(bar, text='缓存维护…', command=self._btn_maint).pack(side='left', padx=2)
        ttk.Button(bar, text='手动添加游戏文件夹…', command=self._btn_add_game).pack(side='left', padx=2)
        ttk.Button(bar, text='添加扫描目录…', command=self._btn_add_root).pack(side='left', padx=2)
        ttk.Button(bar, text='✕ 移除所选(不再扫描)', style='Danger.TButton',
                   command=self._btn_remove_game).pack(side='right', padx=2)
        ttk.Button(bar, text='恢复忽略的游戏…', command=self._btn_ignored).pack(side='right', padx=2)
        cols = ('name', 'path', 'xuat', 'cache', 'state')
        self.tree = ttk.Treeview(f2, columns=cols, show='headings', height=7,
                                 selectmode='extended')
        for cid, txt, w in (('name', '游戏', 150), ('path', '路径', 430),
                            ('xuat', 'XUAT', 70), ('cache', '翻译缓存', 80),
                            ('state', '状态', 120)):
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=w, anchor='w')
        self.tree.grid(row=1, column=0, sticky='ew', padx=8, pady=(0, 4))
        self.tree.tag_configure('odd', background=STRIPE)
        self.tree.tag_configure('kind_ok', foreground=C_OK)
        self.tree.tag_configure('kind_none', foreground=AMBER)
        self.tree.tag_configure('kind_il2cpp', foreground=ACC)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # --- 任务区
        f3 = card(3, ' 预翻译任务 ')
        f3.columnconfigure(0, weight=1)
        self.var_detail = tk.StringVar(value='未选择游戏 (在上方列表中点选)')
        ttk.Label(f3, textvariable=self.var_detail, wraplength=980, justify='left',
                  style='Card.TLabel').grid(row=0, column=0, columnspan=2, sticky='w',
                                            padx=10, pady=2)
        bar3 = ttk.Frame(f3, style='Card.TFrame')
        bar3.grid(row=1, column=0, sticky='w', padx=8, pady=3)
        ttk.Label(bar3, text='目标语言:', style='Card.TLabel').pack(side='left')
        self.var_lang = tk.StringVar(value=self.cfg.get('target_lang', 'zh-CN'))
        ttk.Combobox(bar3, textvariable=self.var_lang, width=8,
                     values=['zh-CN', 'zh-TW']).pack(side='left', padx=4)
        self.var_redo = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar3, text='强制重翻(忽略已有)', variable=self.var_redo,
                        style='Card.TCheckbutton').pack(side='left', padx=4)
        self.btn_start = ttk.Button(bar3, text='▶ 开始预翻译', style='Accent.TButton',
                                    command=self._btn_start)
        self.btn_start.pack(side='left', padx=8)
        self.btn_batch = ttk.Button(bar3, text='▶▶ 批量预翻译',
                                    command=self._btn_batch)
        self.btn_batch.pack(side='left', padx=2)
        self.btn_stop = ttk.Button(bar3, text='■ 停止', style='Danger.TButton',
                                   command=self._btn_stop, state='disabled')
        self.btn_stop.pack(side='left', padx=2)
        ttk.Button(bar3, text='添加外部翻译来源…', command=self._btn_add_source).pack(side='left', padx=8)
        ttk.Button(bar3, text='打开翻译目录', command=self._btn_open_dir).pack(side='left', padx=2)
        ttk.Button(bar3, text='安装XUAT(独立版)', command=self._btn_install_xuat).pack(side='left', padx=2)
        bar4 = ttk.Frame(f3, style='Card.TFrame')
        bar4.grid(row=2, column=0, sticky='w', padx=8, pady=(0, 4))
        self.var_glossary = tk.BooleanVar(value=bool(self.cfg.get('enable_glossary', True)))
        ttk.Checkbutton(bar4, text='名词术语统一', variable=self.var_glossary,
                        style='Card.TCheckbutton').pack(side='left')
        ttk.Button(bar4, text='术语表…', command=self._btn_glossary).pack(side='left', padx=4)
        ttk.Label(bar4, text='自动统一人名/名词译名; 术语表中手写「日文=译名」优先',
                  style='CardMuted.TLabel').pack(side='left', padx=6)
        ttk.Button(bar4, text='使用说明…', command=self._btn_help).pack(side='right')
        bar5 = ttk.Frame(f3, style='Card.TFrame')
        bar5.grid(row=3, column=0, sticky='w', padx=8, pady=(0, 4))
        self.var_fontpatch = tk.BooleanVar(
            value=bool(self.cfg.get('font_tmp_patch', True)))
        ttk.Checkbutton(bar5, text='缺字修复(TMP图集烘焙)', variable=self.var_fontpatch,
                        style='Card.TCheckbutton').pack(side='left')
        ttk.Button(bar5, text='选择字体…', command=self._btn_pick_font).pack(side='left', padx=4)
        self.lbl_font = ttk.Label(bar5, text=self._font_label(),
                                  style='CardMuted.TLabel')
        self.lbl_font.pack(side='left', padx=6)

        # --- 进度 + 日志
        f4 = card(4, ' 进度 ')
        f4.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(f4, maximum=100, style='Accent.Horizontal.TProgressbar')
        self.bar.grid(row=0, column=0, sticky='ew', padx=10, pady=(4, 0))
        self.var_prog = tk.StringVar(value='等待任务')
        ttk.Label(f4, textvariable=self.var_prog, style='Card.TLabel').grid(
            row=1, column=0, sticky='w', padx=10, pady=2)
        f5 = ttk.Labelframe(r, text=' 日志 ', style='Card.TLabelframe')
        f5.grid(row=5, column=0, sticky='nsew', **pad)
        f5.columnconfigure(0, weight=1)
        f5.rowconfigure(0, weight=1)
        self.txt = scrolledtext.ScrolledText(f5, height=10, state='disabled',
                                             font=('Consolas', 9), background=CARD,
                                             relief='flat', bd=0)
        self.txt.grid(row=0, column=0, sticky='nsew', padx=8, pady=4)
        for tag, color in (('info', '#374151'), ('ok', C_OK), ('warn', C_WARN), ('err', C_BAD)):
            self.txt.tag_configure(tag, foreground=color)

        self.lbl_status = ttk.Label(r, text='就绪', style='Status.TLabel',
                                    relief='solid', borderwidth=1, anchor='w')
        self.lbl_status.grid(row=6, column=0, sticky='ew', padx=8, pady=(0, 8))
        self._set_job_ui(False)

    def _set_job_ui(self, running):
        self.btn_start.config(state='disabled' if running else 'normal')
        self.btn_batch.config(state='disabled' if running else 'normal')
        self.btn_stop.config(state='normal' if running else 'disabled')

    # ------------------------------------------------------------- 忽略列表
    def _ignored_roots(self):
        return {os.path.normcase(os.path.normpath(x))
                for x in (self.cfg.get('ignored_games') or [])}

    def _allowed(self, glist):
        ig = self._ignored_roots()
        return [g for g in glist
                if os.path.normcase(os.path.normpath(g.root)) not in ig]

    def _btn_remove_game(self):
        g = self._selected_game()
        if not g:
            messagebox.showinfo('提示', '请先在列表中选择游戏。')
            return
        if not messagebox.askyesno(
                '移除游戏',
                '将「%s」从列表移除并不再扫描?\n\n'
                '适合自带中文或不想翻译的游戏 (如《空洞骑士》《丝之歌》)。\n'
                '不会删除任何文件; 可随时在「恢复忽略的游戏…」中找回。' % g.name):
            return
        ig = list(self.cfg.get('ignored_games') or [])
        if g.root not in ig:
            ig.append(g.root)
        self.cfg['ignored_games'] = ig
        config.save(self.cfg)
        self.games.pop(g.root, None)
        self.tree.delete(g.root)
        self.var_detail.set('未选择游戏 (在上方列表中点选)')
        self._log('已移出扫描列表: %s (%s)' % (g.name, g.root), 'ok')
        self._refresh_statusbar()

    def _btn_ignored(self):
        ig = list(self.cfg.get('ignored_games') or [])
        win = tk.Toplevel(self.root)
        win.title('被忽略的游戏')
        win.geometry('760x380')
        win.configure(bg=BG)
        ttk.Label(win, text='以下游戏已被移出扫描列表 (仅本工具不再显示, 不影响游戏本身)。'
                            '选中后可恢复扫描:', style='TLabel', wraplength=720,
                  justify='left').pack(anchor='w', padx=12, pady=(12, 4))
        lb = tk.Listbox(win, activestyle='dotline', font=(FONT, 9),
                        background=CARD, highlightthickness=1,
                        highlightbackground=BORD)
        lb.pack(fill='both', expand=True, padx=12, pady=4)
        for x in ig:
            lb.insert('end', x)
        if not ig:
            lb.insert('end', '(空) — 目前没有忽略任何游戏')
            lb.configure(state='disabled')
        bar = ttk.Frame(win)
        bar.pack(fill='x', padx=12, pady=10)

        def restore(indices):
            if not ig or not indices:
                return
            removed = []
            for i in sorted(set(indices), reverse=True):
                if 0 <= i < len(ig):
                    removed.append(ig.pop(i))
            if not removed:
                return
            self.cfg['ignored_games'] = ig
            config.save(self.cfg)
            for x in removed:
                self._log('已恢复扫描: %s' % x, 'ok')
            lb.configure(state='normal')
            lb.delete(0, 'end')
            for x in ig:
                lb.insert('end', x)
            if not ig:
                lb.insert('end', '(空) — 目前没有忽略任何游戏')
                lb.configure(state='disabled')
            threading.Thread(target=self._scan, daemon=True).start()

        ttk.Button(bar, text='恢复所选', command=lambda: restore(list(lb.curselection()))
                   ).pack(side='left', padx=2)
        ttk.Button(bar, text='全部恢复', command=lambda: restore(list(range(lb.size())))
                   ).pack(side='left', padx=2)
        ttk.Button(bar, text='关闭', command=win.destroy).pack(side='right', padx=2)

    # ------------------------------------------------------------- 术语表/说明
    def _font_label(self):
        p = (self.cfg.get('font_bake_file') or '').strip()
        if p:
            return '字体: %s (手动指定)' % os.path.basename(p)
        return '字体: 自动挑选 (雅黑粗体优先, 贴合原图集字重)'

    def _btn_pick_font(self):
        p = filedialog.askopenfilename(
            title='选择烘焙用的中文字体',
            filetypes=[('字体文件', '*.ttf *.ttc *.otf'), ('全部文件', '*.*')])
        if not p:
            return
        self.cfg['font_bake_file'] = p
        config.save(self.cfg)
        self.lbl_font.config(text=self._font_label())
        self._log('烘焙字体设为: %s' % p, 'ok')

    def _btn_glossary(self):
        p = self.cfg.get('glossary_file') or os.path.join(TOOL_DIR, '术语表.txt')
        if not os.path.isfile(p):
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write('// 自定义术语表: 每行一条「日文原文=固定译名」\r\n'
                            '// 以 // 开头的行为注释; 优先级高于机器统一译名\r\n'
                            '// 例:\r\n'
                            '// アーダ=阿达\r\n'
                            '// 保存后重新预翻译; 已有译名需勾选「强制重翻」才会覆盖\r\n')
            except OSError as e:
                messagebox.showerror('创建失败', str(e))
                return
        self.cfg['glossary_file'] = p
        config.save(self.cfg)
        os.startfile(p)

    def _btn_help(self):
        win = tk.Toplevel(self.root)
        win.title('使用说明与经验')
        win.geometry('780x540')
        win.configure(bg=CARD)
        txt = scrolledtext.ScrolledText(win, wrap='word', font=(FONT, 9),
                                        background=CARD, relief='flat', bd=0,
                                        padx=12, pady=10)
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', HELP_TEXT)
        txt.config(state='disabled')

    # ------------------------------------------------------------- 线程通信
    def _emit(self, item):
        self.q.put(item)

    def _log(self, msg, lv='info'):
        self._emit(('log', lv, msg))

    def _poll(self):
        try:
            for _ in range(600):
                self._dispatch(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _dispatch(self, item):
        tag = item[0]
        if tag == 'log':
            _, lv, msg = item
            self.txt.config(state='normal')
            self.txt.insert('end', time.strftime('%H:%M:%S  ') + msg + '\n', lv)
            self.txt.see('end')
            self.txt.config(state='disabled')
            # 没配密钥却频繁 429 -> 主动引导申请官方免费密钥
            if '429' in msg and not self.api_ok:
                self._hint_api_key()
        elif tag == 'dlxpath':
            self.var_dlx.set(item[1] or '(未找到, 请浏览选择 deeplx-fixed.exe)')
        elif tag == 'net':
            _, which, ok, detail = item
            self.net[which] = (ok, detail)
            self._refresh_net()
        elif tag == 'ip':
            self.ip[item[1]] = item[2]
            self._refresh_net()
        elif tag == 'dlx':
            _, which, inst, msg = item
            self.dlx_inst[which] = inst
            self._log(msg, 'info' if inst else 'warn')
            self._refresh_channels()
        elif tag == 'apikey':
            self.api_ok = item[1]
            if item[1]:
                self.lbl_api.config(text='官方API密钥: %d 个有效 (额度见"翻译通道"行)'
                                    % item[1], foreground=C_OK)
            else:
                self.lbl_api.config(text='API密钥均无效, 请检查', foreground=C_BAD)
            self._refresh_channels()
        elif tag == 'quota':
            self._quota = (item[1], item[2])
            self._refresh_channels()
            used, lim = self._quota
            if lim and used * 100 >= lim * 80:
                self._log('注意: 官方API本月额度已用 %d/%d 字符 (%d%%), 接近上限;'
                          '可在 deepl.com 再注册一个号, 逗号分隔填入「API密钥」'
                          % (used, lim, used * 100 // lim), 'warn')
        elif tag == 'gstate':
            try:
                self.tree.set(item[1], 'state', item[2])
            except tk.TclError:
                pass
        elif tag == 'games':
            self._fill_tree(item[1])
        elif tag == 'scanflag':
            self._scan_running = item[1]
        elif tag == 'phase':
            self._log('◆ ' + item[1], 'ok')
        elif tag == 'progress':
            self._show_progress(item[1], item[2], item[3])
        elif tag == 'jobdone':
            self._on_jobdone(item[1])
        elif tag == 'share_done':
            _, p, ok = item
            if ok:
                messagebox.showinfo('分享包已生成', p)
            else:
                messagebox.showerror('生成失败', p)
        elif tag == 'il2cpp_install':
            _, root_, ok = item
            if ok:
                self._log('IL2CPP 组件安装完成! 下一步: 启动一次该游戏 (首次黑屏较久, '
                          'BepInEx 正在生成 interop), 正常退出后点「重新扫描」, '
                          '再点「▶ 开始预翻译」。', 'ok')
                messagebox.showinfo(
                    '安装完成',
                    'BepInEx 6 + XUAT (IL2CPP) 已解压到游戏目录。\n\n'
                    '1. 启动一次游戏 — 首次启动黑屏时间较长 (生成 interop), 请耐心等待\n'
                    '2. 正常退出游戏\n'
                    '3. 回到本工具点「重新扫描」\n'
                    '4. 点「▶ 开始预翻译」')
            else:
                messagebox.showerror('安装失败', '部分组件下载/解压失败, 请看日志。')
        self._refresh_statusbar()

    def _hint_api_key(self):
        if self._hinted_429 or self.api_ok:
            return
        self._hinted_429 = True
        self.lbl_api.config(
            text='检测到429限速 — 建议在「API密钥」填官方免费密钥 (deepl.com 免费, 实测快20倍)',
            foreground=C_BAD)

    def _refresh_net(self):
        def part(which, name):
            ok, detail = self.net[which]
            dot = {True: '● 可用', False: '● 不可用', None: '…'}[ok]
            ip = (' (%s)' % self.ip[which]) if self.ip[which] else ''
            return '%s %s%s' % (name, dot, ip)
        self.lbl_net.config(text='网络:  %s    %s    |    %s' % (
            part('direct', '直连DeepL'), part('proxy', '代理'),
            (self.net['proxy'][1] if not self.net['proxy'][0] else '代理通道独立限速')))

    def _refresh_channels(self):
        parts = []
        for k in sorted(self.dlx_inst):
            inst = self.dlx_inst[k]
            if not inst:
                continue
            name = ('直连' if k == 'direct'
                    else ('代理%s' % k[5:] if k.startswith('proxy') else k))
            parts.append('%s:%d%s' % (name, inst.port, '(复用)' if inst.reused else ''))
        if self.api_ok:
            parts.append('官方API×%d' % self.api_ok)
        if self._quota and self._quota[1]:
            used, lim = self._quota
            parts.append('额度 %d/%d (%d%%)' % (used, lim, used * 100 // lim))
        self.lbl_channels.config(
            text='翻译通道: ' + ('  '.join(parts) if parts else '未启动'),
            foreground=C_OK if (parts or self.api_ok) else C_IDLE)

    def _refresh_statusbar(self):
        okd = self.net['direct'][0]
        okp = self.net['proxy'][0]
        nch = sum(1 for i in self.dlx_inst.values() if i) + self.api_ok
        self.lbl_status.config(text='翻译通道 %d | 直连DeepL %s | 代理 %s | 游戏 %d 个 | 忽略 %d 个'
                               % (nch,
                                  {True: '●', False: '●', None: '…'}[okd],
                                  {True: '●', False: '●', None: '…'}[okp],
                                  len(self.games),
                                  len(self.cfg.get('ignored_games') or [])))

    # ------------------------------------------------------------- 启动流程
    def _startup(self):
        self._log('PreTranslator 启动, 游戏与 DLX 服务将自动检测')
        exe = dlx.find_exe(self.cfg.get('dlx_path'))
        self._emit(('dlxpath', exe or ''))
        # 先展示记住的游戏
        remembered = []
        for root_ in list(self.cfg.get('games', {}).keys()):
            g = games.detect_at(root_)
            if g:
                remembered.append(g)
        remembered = self._allowed(remembered)
        if remembered:
            self._emit(('games', remembered))
        # 网络与 DLX 并行, 扫描同步进行
        threading.Thread(target=self._net_and_dlx,
                         args=(exe, self.cfg.get('proxy_url', '')),
                         daemon=True).start()
        self._emit(('scanflag', True))
        self._log('开始扫描本机游戏 (首次可能需要 1-2 分钟, 可随时用"添加扫描目录"补充)')
        gs = games.scan(games.default_roots(self.cfg.get('scan_roots')), log_fn=self._log)
        for root_ in list(self.cfg.get('games', {}).keys()):
            if root_ not in {x.root for x in gs}:
                g = games.detect_at(root_)
                if g:
                    gs.append(g)
        gs = self._allowed(gs)
        self._emit(('games', gs))
        self._emit(('scanflag', False))
        self._log('扫描完成, 共 %d 个游戏' % len(gs))

    def _net_and_dlx(self, exe, proxy=None):
        # 注意: 本函数在线程中运行, 不可访问 tkinter 变量, 代理地址由调用方传入
        proxies = pretranslator._split_multi(proxy or self.cfg.get('proxy_url', ''))
        if not proxies:
            proxies = ['']
        self._log('检测网络: 直连 DeepL …')
        ok, d = netcheck.check_deepl(None)
        self._emit(('net', 'direct', ok, d))
        ip = netcheck.exit_ip(None)
        if ip:
            self._emit(('ip', 'direct', ip))
        for j, px in enumerate(proxies):
            if not px:
                continue
            self._log('检测网络: 代理 %s …' % px)
            ok2, d2 = netcheck.check_deepl(px)
            if j == 0:
                self._emit(('net', 'proxy', ok2, d2))
            ip2 = netcheck.exit_ip(px)
            if ip2:
                self._emit(('ip', 'proxy' if j == 0 else 'proxy%d' % j, ip2))
        # DeepL 官方免费API密钥通道: 不依赖 DLX
        keys = pretranslator._split_multi(self.cfg.get('deepl_keys', ''))
        self.api_ok = 0
        quota_shown = False
        for i, key in enumerate(keys):
            okk, dk = netcheck.check_deepl_key(key, proxy=proxies[0] or None)
            if okk:
                self.api_ok += 1
                if not quota_shown:
                    q = netcheck.get_usage(key, proxy=proxies[0] or None)
                    if q:
                        quota_shown = True
                        self._emit(('quota', q[0], q[1]))
            self._log('DeepL 官方API 密钥#%d: %s (%s)'
                      % (i + 1, '有效' if okk else '不可用', dk), 'ok' if okk else 'warn')
        if keys:
            self._emit(('apikey', self.api_ok))
        if not exe:
            if not self.api_ok:
                self._log('未找到 DLX 程序且无有效API密钥, 翻译不可用。请点击"浏览"'
                          '选择 deeplx-fixed.exe, 或在"API密钥"填入官方免费密钥', 'err')
            return
        okd, _ = netcheck.check_deepl(None, timeout=6)
        if okd:
            inst = dlx.ensure_dlx(exe, self.cfg['dlx_port'], proxy=None,
                                  strict=True, log_fn=self._log)
            self._emit(('dlx', 'direct', inst, 'DLX 直连通道: %s' %
                        ('端口 %d%s' % (inst.port, '(复用已有实例)' if inst.reused else '已启动') if inst else '启动失败')))
        else:
            self._emit(('dlx', 'direct', None, '直连 DeepL 不可达, 跳过直连通道'))
        for j, px in enumerate(proxies):
            if not px:
                continue
            which = 'proxy' if j == 0 else 'proxy%d' % j
            ok2, _ = netcheck.check_deepl(px, timeout=8)
            if ok2:
                inst2 = dlx.ensure_dlx(exe, self.cfg['dlx_proxy_port'] + j,
                                       proxy=px, strict=True, log_fn=self._log)
                self._emit(('dlx', which, inst2, 'DLX 代理通道(%s): %s' %
                            (px, ('端口 %d%s' % (inst2.port, '(复用已有实例)' if inst2.reused else '已启动'))
                             if inst2 else '启动失败')))
            else:
                self._emit(('dlx', which, None, '代理 %s 不可达, 跳过该通道' % px))

    # ------------------------------------------------------------- 按钮
    def _pick_dlx(self):
        p = filedialog.askopenfilename(title='选择 deeplx-fixed.exe',
                                       filetypes=[('可执行文件', '*.exe'), ('所有文件', '*.*')])
        if p:
            self.cfg['dlx_path'] = p
            config.save(self.cfg)
            self.var_dlx.set(p)
            self._log('已设置 DLX 程序: %s, 正在启动服务…' % p)
            threading.Thread(target=self._net_and_dlx,
                             args=(p, self.var_proxy.get().strip()), daemon=True).start()

    def _persist_service_fields(self):
        """把服务区输入框落盘到 config.json (代理/API密钥)。"""
        self.cfg['proxy_url'] = self.var_proxy.get().strip() or 'http://127.0.0.1:7897'
        self.cfg['deepl_keys'] = self.var_deepl_keys.get().strip()
        config.save(self.cfg)

    def _btn_service(self):
        self._persist_service_fields()
        exe = dlx.find_exe(self.cfg.get('dlx_path'))
        threading.Thread(target=self._net_and_dlx,
                         args=(exe, self.cfg['proxy_url']), daemon=True).start()

    def _btn_share(self):
        self._log('正在生成脱密分享包 (工具代码 + 内置DLX + 全新配置)…')
        self._persist_service_fields()

        def work():
            try:
                p = pretranslator.make_share_zip(log_fn=self._log)
                self._emit(('share_done', p, True))
            except Exception as e:
                self._log('分享包生成失败: %s' % e, 'err')
                self._emit(('share_done', str(e), False))
        threading.Thread(target=work, daemon=True).start()

    def _btn_scan(self):
        if self._scan_running:
            self._log('扫描已在进行中…', 'warn')
            return
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        self._emit(('scanflag', True))
        gs = games.scan(games.default_roots(self.cfg.get('scan_roots')), log_fn=self._log)
        for root_ in list(self.cfg.get('games', {}).keys()):
            if root_ not in {x.root for x in gs}:
                g = games.detect_at(root_)
                if g:
                    gs.append(g)
        gs = self._allowed(gs)
        self._emit(('games', gs))
        self._emit(('scanflag', False))
        self._log('扫描完成, 共 %d 个游戏' % len(gs))

    def _btn_add_game(self):
        p = filedialog.askdirectory(title='选择游戏根目录 (内含 BepInEx)')
        if not p:
            return
        g = games.detect_at(p)
        if not g:
            messagebox.showwarning('无效目录', '该目录下没有 BepInEx 文件夹, 不是 BepInEx 游戏根目录。')
            return
        merged = {x.root: x for x in self.games.values()}
        merged[g.root] = g
        self._emit(('games', list(merged.values())))
        self._log('已添加游戏: %s (%s)' % (g.name, g.root))

    def _btn_add_root(self):
        p = filedialog.askdirectory(title='选择要加入扫描的目录')
        if not p:
            return
        roots = self.cfg.setdefault('scan_roots', [])
        if p not in roots:
            roots.append(p)
            config.save(self.cfg)
        self._log('已添加扫描目录: %s, 重新扫描中…' % p)
        threading.Thread(target=self._scan, daemon=True).start()

    def _btn_add_source(self):
        g = self._selected_game()
        if not g:
            messagebox.showinfo('提示', '请先在列表中选择游戏。')
            return
        p = filedialog.askdirectory(title='选择翻译来源目录 (含 Text/ 或 RedirectedResources/ 子目录)')
        if not p:
            return
        if not extract.is_source_folder(p):
            messagebox.showwarning('无效来源',
                                   '所选目录下没有 Text/ 或 RedirectedResources/ 子目录。\n'
                                   '请选择 XUAT 翻译包目录, 例如 KoikatsuTranslation 仓库的 translation/en。')
            return
        info = self.cfg['games'].setdefault(g.root, {'extra_sources': []})
        extras = info.setdefault('extra_sources', [])
        if p not in extras:
            extras.append(p)
        config.save(self.cfg)
        self._log('已为 %s 添加外部翻译来源: %s' % (g.name, p))
        self._on_select()

    def _btn_open_dir(self):
        g = self._selected_game()
        if not g:
            return
        t = g.target_dir(self.var_lang.get().strip() or 'zh-CN')
        import subprocess
        if os.path.isdir(t):
            os.startfile(t)
        elif os.path.isdir(g.translation_dir):
            os.startfile(g.translation_dir)
        else:
            messagebox.showinfo('提示', '翻译目录还不存在, 先运行一次预翻译吧。')

    def _btn_start(self):
        if self.job and self.job.is_alive():
            return
        g = self._selected_game()
        if not g:
            messagebox.showinfo('提示', '请先在列表中选择游戏。')
            return
        if g.kind == 'unity':
            if g.il2cpp:
                import re as _re
                ga = os.path.join(g.root, 'GameAssembly.dll')
                arch = games.pe_arch(ga) if os.path.isfile(ga) else None
                if not arch:
                    arch = 'x64'
                if messagebox.askyesno(
                        'IL2CPP 游戏安装',
                        '「%s」是 IL2CPP 游戏且尚未安装 XUAT。\n\n'
                        '将自动下载并解压到游戏目录 (共约35MB):\n'
                        '  · BepInEx 6.0.0-pre.2 (IL2CPP-%s)\n'
                        '  · XUnity.AutoTranslator IL2CPP 版 + 资源重定向器\n\n'
                        '完成后需要: ① 启动一次游戏 (BepInEx 生成 interop, '
                        '首次启动会有较长时间黑屏, 属正常)\n'
                        '② 正常退出后回到本工具点「重新扫描」, 再开始预翻译。\n\n继续?'
                        % (g.name, arch)):
                    self._install_il2cpp(g)
            else:
                if messagebox.askyesno(
                        '未安装XUAT',
                        '「%s」尚未安装 XUnity.AutoTranslator。\n\n'
                        '是否把 SetupReiPatcherAndAutoTranslator.exe 复制到游戏目录?\n'
                        '复制后请双击运行它 (会在游戏目录安装独立版XUAT并生成 Patch and Run 快捷方式),'
                        '完成后回到本工具重新扫描。' % g.name):
                    self._install_xuat(g)
            return
        healthy = [i for i in self.dlx_inst.values()
                   if i and i.healthy(timeout=12)]
        self._persist_service_fields()
        self.cfg['target_lang'] = self.var_lang.get().strip() or 'zh-CN'
        self.cfg['enable_glossary'] = bool(self.var_glossary.get())
        self.cfg['font_tmp_patch'] = bool(self.var_fontpatch.get())
        self.cfg['glossary_file'] = (self.cfg.get('glossary_file')
                                     or os.path.join(TOOL_DIR, '术语表.txt'))
        config.save(self.cfg)
        if not healthy and not self.api_ok:
            messagebox.showerror('翻译通道未就绪',
                                 '没有健康的 DLX 通道, 也没有有效的官方API密钥。\n'
                                 '请检查网络/代理后点击"启动/检查服务"。')
            return
        target_lang = self.var_lang.get().strip() or 'zh-CN'
        self.job_stop.clear()
        self._hinted_429 = False
        self._prog_t0 = None
        self._set_job_ui(True)
        self.var_prog.set('准备中…')
        self._log('开始预翻译: %s -> %s (%s版)'
                  % (g.name, target_lang, g.kind_label))
        self.job = threading.Thread(target=self._job_main,
                                    args=(g, target_lang, bool(self.var_redo.get())),
                                    daemon=True)
        self.job.start()

    def _install_xuat(self, g):
        cands = [self.cfg.get('xuat_setup_exe') or '',
                 os.path.join(TOOL_DIR, 'SetupReiPatcherAndAutoTranslator.exe')]
        src = next((c for c in cands if c and os.path.isfile(c)), '')
        if not src:
            messagebox.showerror(
                '找不到安装器',
                'SetupReiPatcherAndAutoTranslator.exe 不存在。\n'
                '可从 XUnity.AutoTranslator 的 ReiPatcher 发行包获取, 放到工具目录,'
                '或在配置 config.json 的 xuat_setup_exe 指定路径。')
            return
        import shutil
        dst = os.path.join(g.root, os.path.basename(src))
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            messagebox.showerror('复制失败', str(e))
            return
        self._log('已复制安装器到: %s' % dst, 'ok')
        self._log('请到游戏目录双击运行 SetupReiPatcherAndAutoTranslator.exe (会生成'
                  ' AutoTranslator/ 与 Patch and Run 快捷方式), 完成后点「重新扫描」。', 'ok')
        messagebox.showinfo(
            '下一步',
            '安装器已复制到游戏目录。\n\n'
            '1. 打开游戏目录, 双击运行 SetupReiPatcherAndAutoTranslator.exe\n'
            '2. 完成后回到本工具点「重新扫描」\n'
            '3. 再点「▶ 开始预翻译」')

    def _install_il2cpp(self, g):
        self._log('开始为 %s 下载并安装 BepInEx6-IL2CPP + XUAT-IL2CPP …' % g.name)
        self._emit(('phase', 'IL2CPP 安装中 (下载约35MB)'))

        def work():
            ok = games.install_il2cpp(g, self.cfg, log_fn=self._log)
            self._emit(('il2cpp_install', g.root, ok))

        threading.Thread(target=work, daemon=True).start()

    def _btn_install_xuat(self):
        g = self._selected_game()
        if not g:
            messagebox.showinfo('提示', '请先在列表中选择游戏。')
            return
        if g.kind == 'bepinex' or g.kind == 'standalone':
            messagebox.showinfo('提示', '该游戏已安装 XUAT (%s), 无需安装。' % g.kind_label)
            return
        if g.il2cpp:
            self._install_il2cpp(g)
            return
        self._install_xuat(g)

    def _btn_stop(self):
        self.job_stop.set()
        self._log('停止请求已发出, 等待当前请求完成…', 'warn')

    # ------------------------------------------------------------- 任务
    def _job_main(self, g, target_lang, redo=False):
        try:
            s = pretranslator.run_pipeline(
                self.cfg, g, log_fn=self._log, redo=redo,
                stop_event=self.job_stop,
                progress_fn=lambda d, t, e: self._emit(('progress', d, t, e)))
            if s.get('stopped') or self.job_stop.is_set():
                self._log('已停止, 不写入游戏目录', 'warn')
                self._emit(('jobdone', {'stopped': True}))
                return
            # 校验全一致且无永久失败才写完成标记 (批量跑时据此跳过该游戏)
            ok_m = runlog.write_marker(g, target_lang, s, log_fn=self._log)
            self._log('预翻译完成! 启动游戏即可离线加载翻译。%s'
                      % ('(完成标记已写入)' if ok_m else '(有遗留/不一致, 未写完成标记)'),
                      'ok' if ok_m else 'warn')
            self._emit(('jobdone', {'keys': s.get('keys', 0),
                                    'existing': s.get('existing', 0),
                                    'translated': s.get('engine', {}).get('done_now', 0),
                                    'perm': s.get('engine', {}).get('perm_failed', 0),
                                    'stats': s.get('engine', {}),
                                    'write': s.get('write', {}),
                                    'verify': s.get('verify', {}),
                                    'font': s.get('font'),
                                    'quota': s.get('quota'),
                                    'game': g.root, 'lang': target_lang}))
        except Exception as e:
            self._log('任务失败: %s' % e, 'err')
            self._log(traceback.format_exc()[-600:], 'err')
            self._emit(('jobdone', {'error': str(e)}))

    def _btn_batch(self):
        if self.job and self.job.is_alive():
            return
        sel = self.tree.selection()
        gs = [self.games[r] for r in sel if r in self.games]
        if not gs:
            messagebox.showinfo('提示', '请先在列表中选择游戏 (按住 Ctrl 可多选)。')
            return
        for g in gs:
            if g.kind == 'unity':
                messagebox.showwarning(
                    '批量预翻译',
                    '「%s」尚未安装 XUAT, 请先单独处理 (安装后重新扫描) 再批量。' % g.name)
                return
        healthy = [i for i in self.dlx_inst.values()
                   if i and i.healthy(timeout=12)]
        self._persist_service_fields()
        self.cfg['target_lang'] = self.var_lang.get().strip() or 'zh-CN'
        self.cfg['enable_glossary'] = bool(self.var_glossary.get())
        self.cfg['font_tmp_patch'] = bool(self.var_fontpatch.get())
        self.cfg['glossary_file'] = (self.cfg.get('glossary_file')
                                     or os.path.join(TOOL_DIR, '术语表.txt'))
        config.save(self.cfg)
        if not healthy and not self.api_ok:
            messagebox.showerror('翻译通道未就绪',
                                 '没有健康的 DLX 通道, 也没有有效的官方API密钥。\n'
                                 '请检查网络/代理后点击"启动/检查服务"。')
            return
        if not messagebox.askyesno(
                '批量预翻译',
                '将按顺序预翻译所选的 %d 个游戏。\n\n'
                '· 已有完成标记 (此前完整跑完且校验通过) 的自动跳过\n'
                '· 中途可点「■ 停止」, 断点已保存, 随时可续\n'
                '· 勾选「强制重翻」则忽略标记与已有翻译全部重来\n\n继续?'
                % len(gs)):
            return
        target_lang = self.cfg['target_lang']
        self.job_stop.clear()
        self._hinted_429 = False
        self._set_job_ui(True)
        self.var_prog.set('批量准备中…')
        self._log('批量预翻译开始: %d 个游戏 -> %s (强制重翻=%s)'
                  % (len(gs), target_lang, bool(self.var_redo.get())))
        self.job = threading.Thread(target=self._job_batch,
                                    args=(gs, target_lang,
                                          bool(self.var_redo.get())),
                                    daemon=True)
        self.job.start()

    def _job_batch(self, gs, target_lang, redo):
        results = []
        try:
            for idx, g in enumerate(gs):
                if self.job_stop.is_set():
                    break
                try:
                    if not redo and runlog.is_done(g, target_lang):
                        self._log('批量[%d/%d] %s: 已有完成标记, 跳过'
                                  % (idx + 1, len(gs), g.name))
                        self._emit(('gstate', g.root, '✓ 已完成'))
                        results.append((g.name, 'OK(已完成)'))
                        continue
                    self._emit(('gstate', g.root, '翻译中…'))
                    self._log('批量[%d/%d] %s [%s]: 开始预翻译'
                              % (idx + 1, len(gs), g.name, g.kind_label))
                    s = pretranslator.run_pipeline(
                        self.cfg, g, log_fn=self._log, redo=redo,
                        stop_event=self.job_stop,
                        progress_fn=lambda d, t, e: self._emit(
                            ('progress', d, t, e)))
                    if s.get('stopped') or self.job_stop.is_set():
                        self._log('已停止: %s 断点已保存, 未写入游戏目录'
                                  % g.name, 'warn')
                        self._emit(('gstate', g.root, '已停止(可续翻)'))
                        results.append((g.name, '已停止'))
                        break
                    ok = runlog.write_marker(g, target_lang, s, log_fn=self._log)
                    self._emit(('gstate', g.root,
                                '✓ 已完成' if ok else '⚠ 有遗留句'))
                    self._log('批量: %s 完成 翻译%d句 写出%d对 校验一致%d/不一致%d '
                              '永久失败%d'
                              % (g.name, s.get('engine', {}).get('done_now', 0),
                                 s.get('write', {}).get('pre_pairs', 0),
                                 s.get('verify', {}).get('ok', 0),
                                 s.get('verify', {}).get('bad', 0),
                                 s.get('engine', {}).get('perm_failed', 0)),
                              'ok' if ok else 'warn')
                    results.append((g.name, 'OK' if ok else '有遗留句(可续翻)'))
                except Exception as e:
                    self._log('批量: %s 失败 %s' % (g.name, e), 'err')
                    self._log(traceback.format_exc()[-400:], 'err')
                    self._emit(('gstate', g.root, '✗ 失败'))
                    results.append((g.name, 'FAIL: %s' % str(e)[:80]))
        finally:
            nok = sum(1 for _n, r in results if r.startswith('OK'))
            self._log('==== 批量结束: 共 %d 个, 完整完成 %d ===='
                      % (len(results), nok))
            for n, r in results:
                self._log('  %-36s %s' % (n[:36], r),
                          'ok' if r.startswith('OK') else 'warn')
            self._emit(('jobdone', {'batch': True, 'results': results,
                                    'stopped': self.job_stop.is_set()}))

    def _show_progress(self, done, total, elapsed):
        if total <= 0:
            return
        pct = 100.0 * done / total
        self.bar.config(maximum=100, value=min(pct, 100))
        rate = done / elapsed if elapsed > 0.5 else 0
        extra = ''
        if rate > 0 and done < total:
            eta = (total - done) / rate
            extra = ' | %.1f句/秒 | 剩余约 %d分%02d秒' % (rate, int(eta // 60), int(eta % 60))
        self.var_prog.set('%.1f%%  |  %d / %d 句%s' % (pct, done, total, extra))

    def _on_jobdone(self, s):
        self._set_job_ui(False)
        if s.get('batch'):
            res = s.get('results') or []
            nok = sum(1 for _n, r in res if r.startswith('OK'))
            self.var_prog.set('批量完成: %d 个游戏中完整完成 %d' % (len(res), nok))
            if s.get('stopped'):
                self._log('批量被手动停止: 已完成的部分保持完成状态, '
                          '剩余的下次再点「▶▶ 批量预翻译」会自动接着跑', 'warn')
            return
        if s.get('stopped'):
            self.var_prog.set('已停止 (断点已保存, 可随时续翻)')
            return
        if s.get('error'):
            self.var_prog.set('失败: %s' % s['error'])
            messagebox.showerror('任务失败', s['error'])
            return
        w, v = s.get('write', {}), s.get('verify', {})
        st = s.get('stats', {})
        perm = s.get('perm', 0)
        msg = '完成: 翻译 %d 句 | 写出 %d 文件 | 校验一致 %d / 不一致 %d' % (
            s.get('translated', 0), w.get('files', 0), v.get('ok', 0), v.get('bad', 0))
        if perm:
            msg += ' | 永久失败 %d 句 (再点一次「▶ 开始预翻译」即可只补翻这些)' % perm
        self.var_prog.set(msg)
        root = s.get('game')
        if root in self.games:
            g = self.games[root]
            self.tree.set(root, 'cache', '已存在缓存')
            self.tree.set(root, 'state', '✓ 已完成' if not perm else '⚠ 有遗留句')
        self._log('本次翻译 %d 句, 永久失败 %d; 写出 %d 文件 (新 %d 行, 保留原有 %d 行); '
                  '校验一致 %d / 不一致 %d'
                  % (s.get('translated', 0), perm, w.get('files', 0),
                     w.get('new_lines', 0), w.get('kept', 0), v.get('ok', 0),
                     v.get('bad', 0)), 'ok' if not perm else 'warn')
        if perm:
            self._log('提示: %d 句永久失败 (多为超长/异常句)。断点已保存, '
                      '直接再点「▶ 开始预翻译」即可只补翻这些遗留句。' % perm, 'warn')
        if st.get('hits429'):
            self._log('限速统计: 请求 %d 次, 遇 429 共 %d 次, 全局熔断 %d 次'
                      % (st.get('requests', 0), st['hits429'],
                         st.get('penalty_trips', 0)), 'warn')
        fp = s.get('font') or {}
        if fp.get('bake_blocked'):
            self._log('字体: 该游戏的 TMP 图集不是可烘焙的静态图集, 已自动改用保底方案 '
                      '(译文缺字替换为日式写法)。若游戏中仍见方框, 请点「选择字体…」'
                      '指定一款 TTF 后强制重翻。', 'warn')
            self.var_prog.set(self.var_prog.get() + ' | 字体图集不可烘焙(已用保底方案)')
        q = s.get('quota')
        if q and q[1]:
            self._quota = (q[0], q[1])
            self._refresh_channels()
            self._log('官方API额度: 本月已用 %d/%d 字符' % (q[0], q[1]))

    # ------------------------------------------------------------- 列表
    def _fill_tree(self, gs):
        self.games = {g.root: g for g in gs}
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        lang = self.var_lang.get().strip() or 'zh-CN'
        for i, g in enumerate(gs):
            tdir = g.target_dir(lang)
            tags = ['odd'] if i % 2 else []
            if g.kind == 'unity':
                tags.append('kind_il2cpp' if g.il2cpp else 'kind_none')
            else:
                tags.append('kind_ok')
            self.tree.insert('', 'end', iid=g.root, tags=tags, values=(
                g.name, g.root, g.kind_label,
                '已存在' if os.path.isdir(tdir) else '—',
                '✓ 已完成' if (g.kind != 'unity' and runlog.is_done(g, lang))
                else '—'))
        if sel:
            try:
                self.tree.selection_set(sel)
            except tk.TclError:
                pass

    def _selected_game(self):
        sel = self.tree.selection()
        return self.games.get(sel[0]) if sel else None

    # ------------------------------------------------------------- 缓存维护
    def _known_cache_names(self):
        """当前已知游戏根目录对应的缓存目录名集合 (维护界面的"在用"判定)。"""
        names = set()
        roots = set(self.cfg.get('games', {}).keys())
        roots |= {g.root for g in self.games.values()}
        roots |= set(self.cfg.get('ignored_games') or [])
        for r in roots:
            if os.path.isdir(r):
                names.add(games.cache_name_for(r))
        return names

    def _btn_maint(self):
        win = tk.Toplevel(self.root)
        win.title('缓存维护 (断点/翻译缓存)')
        win.geometry('880x480')
        win.configure(bg=BG)
        ttk.Label(win, text='每个游戏的翻译断点存放在工具目录 cache/ 下 (目录名=游戏名-hash)。'
                            '游戏被移动/删除后会残留孤儿目录; 同一游戏可能因历史路径写法不同'
                            '存在多份。合并重复项不会丢失任何已翻译句子。',
                  style='TLabel', wraplength=840, justify='left'
                  ).pack(anchor='w', padx=12, pady=(10, 4))
        cols = ('name', 'keys', 'status')
        tv = ttk.Treeview(win, columns=cols, show='headings', selectmode='extended')
        for cid, txt, wd in (('name', '缓存目录', 330), ('keys', '断点句数', 90),
                             ('status', '状态', 300)):
            tv.heading(cid, text=txt)
            tv.column(cid, width=wd, anchor='w')
        tv.pack(fill='both', expand=True, padx=12, pady=4)
        state = {'recs': []}

        def refresh():
            tv.delete(*tv.get_children())
            state['recs'] = runlog.cache_scan(os.path.join(TOOL_DIR, 'cache'),
                                              self._known_cache_names())
            for r in state['recs']:
                txt = {'inuse': '在用',
                       'dup': '重复 → 可并入 %s' % r.get('merge_into', '?'),
                       'orphan': '孤儿 (游戏不在已知列表, 可能已移动/删除)'}[r['status']]
                tv.insert('', 'end', iid=r['path'], values=(r['name'], r['keys'], txt))
            if not state['recs']:
                tv.insert('', 'end', values=('(缓存目录为空)', '', ''))

        def do_merge():
            dups = [r for r in state['recs'] if r['status'] == 'dup']
            if not dups:
                messagebox.showinfo('缓存维护', '没有需要合并的重复缓存。', parent=win)
                return
            for r in dups:
                dst = os.path.join(TOOL_DIR, 'cache', r['merge_into'])
                added, _t = runlog.merge_cache_dirs(dst, [r['path']],
                                                    remove_src=True,
                                                    log_fn=self._log)
                self._log('缓存合并: %s -> %s (补入 %d 句)'
                          % (r['name'], r['merge_into'], added), 'ok')
            messagebox.showinfo('缓存维护', '已合并 %d 个重复缓存目录。' % len(dups),
                                parent=win)
            refresh()

        def do_del():
            sel = [p for p in tv.selection()
                   if p in {r['path'] for r in state['recs']}]
            if not sel:
                messagebox.showinfo('缓存维护', '请先在列表中选择要删除的缓存目录。',
                                    parent=win)
                return
            if not messagebox.askyesno(
                    '删除缓存',
                    '将删除所选 %d 个缓存目录 (仅断点/进度记录, 不影响游戏文件)。\n'
                    '删除后对应游戏再预翻译会从头开始。\n\n确定?' % len(sel),
                    parent=win):
                return
            for p in sel:
                shutil.rmtree(p, ignore_errors=True)
                self._log('已删除缓存目录: %s' % os.path.basename(p))
            refresh()

        bar = ttk.Frame(win)
        bar.pack(fill='x', padx=12, pady=10)
        ttk.Button(bar, text='合并重复项', command=do_merge).pack(side='left', padx=2)
        ttk.Button(bar, text='删除所选', style='Danger.TButton',
                   command=do_del).pack(side='left', padx=2)
        ttk.Button(bar, text='刷新', command=refresh).pack(side='left', padx=8)
        ttk.Button(bar, text='关闭', command=win.destroy).pack(side='right', padx=2)
        refresh()

    def _on_select(self, _ev=None):
        g = self._selected_game()
        if not g:
            return
        ini = g.read_ini()
        parts = ['%s  |  %s  |  XUAT: %s' % (
            g.name, g.root, g.kind_label)]
        if g.kind == 'unity':
            parts.append('尚未安装XUAT — 用下方「安装XUAT(独立版)」按钮')
        if ini:
            parts.append('配置: %s→%s (Endpoint=%s)' % (
                ini.get('FromLanguage', '?'), ini.get('Language', '?'),
                ini.get('Endpoint', '(空)')))
        extras = (self.cfg.get('games', {}).get(g.root, {}) or {}).get('extra_sources') or []
        if extras:
            parts.append('外部来源: %s' % ' ; '.join(extras))
        self.var_detail.set('    '.join(parts))

    def _on_close(self):
        if self.job and self.job.is_alive():
            if not messagebox.askokcancel('退出', '翻译任务仍在运行, 确定停止并退出?\n(已翻译部分已存断点, 下次可续)'):
                return
        self.job_stop.set()
        for i in self.dlx_inst.values():
            if i:
                i.stop()
        self._persist_service_fields()
        self.root.destroy()


def run():
    cfg = config.load()
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        for n in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont'):
            try:
                tkfont.nametofont(n).configure(family=FONT, size=9)
            except Exception:
                pass
        _apply_theme(root)
    except Exception:
        pass
    root.title('Unity 游戏预翻译工具 · DLX(DeepLX) 离线缓存生成')
    root.geometry('1080x800')
    root.minsize(960, 660)
    app = App(root, cfg)
    root.protocol('WM_DELETE_WINDOW', app._on_close)
    root.mainloop()
