# PreTranslator — Unity 游戏预翻译工具 (XUnity.AutoTranslator + DLX/DeepLX)

在**游戏运行前**批量完成翻译, 生成 XUnity.AutoTranslator 的本地翻译缓存。
游戏启动时 XUAT 直接查表加载, **全程离线**, 不再依赖在线接口。

```
扫描本机游戏 → 提取日文原文 → 多通道机翻 (官方API/直连DLX/代理DLX)
→ 写入翻译缓存 (自动备份, 保留人工翻译) → 往返校验 → 完成标记/运行报告
```

支持四类游戏:

| 类型 | 识别特征 | 翻译目录 |
|---|---|---|
| BepInEx 版 | `BepInEx/` 目录 | `BepInEx/Translation/zh-CN/` |
| XUAT 独立版 (ReiPatcher) | `AutoTranslator/Config.ini` 或 Managed 中已有 XUAT 补丁 | `AutoTranslator/Translation/zh-CN/` |
| 裸 Unity (Mono) | `UnityPlayer.dll` + Managed | 「安装XUAT(独立版)」后即可翻译 |
| 裸 Unity (IL2CPP) | `GameAssembly.dll` | 「安装XUAT(独立版)」会自动下载 BepInEx 6 (IL2CPP) + XUAT IL2CPP 版 |

## 快速开始

1. 双击 `启动预翻译工具.bat` (需要已安装 Python 3.8+; 纯标准库, 无需 pip 装任何东西)
2. 工具会自动:
   - 后台静默拉起 DLX 翻译服务 (直连 + 每个代理各一条通道, 端口被占自动换)
   - 校验 API 密钥并**显示本月官方API额度** (已用/上限, 超过 80% 黄色提醒)
   - 扫描本机所有盘符 + Steam 库, 找出 BepInEx 版 / 独立版 / 裸 Unity 游戏
3. 列表中点选游戏 → 点 **▶ 开始预翻译** → 看进度条, 结束后点 **▶ 启动游戏** 直接开玩
   (启动前会自动补齐缺失的 XUAT 配置, 避免首次进游戏按默认语言初始化)
4. **多个游戏一起翻**: 按住 Ctrl 在列表多选 → 点 **▶▶ 批量预翻译**。
   已完整翻完的游戏 (有完成标记) 自动跳过, 中途可停止、随时续跑。
5. 裸 Unity 游戏: 点 **安装XUAT(独立版)**
   - Mono 游戏: 复制 `SetupReiPatcherAndAutoTranslator.exe` → 双击运行 → 重新扫描
   - IL2CPP 游戏: 自动下载并解压 BepInEx 6 + XUAT IL2CPP 版 → **启动一次游戏**
     (BepInEx 首次生成 interop, 黑屏较久属正常) → 正常退出 → 重新扫描 → 预翻译
   - 已运行过安装器但没生成 Config.ini 的独立版游戏也能直接识别并预生成配置
   - 部分游戏文本不在标准位置 (如 VSGoblin 的 metadata 里几乎没有字符串),
     这类游戏提取不到原文, 属于个例

## 强烈建议: 配一个 DeepL 官方免费 API 密钥

到 deepl.com 注册 API Free 账户, 把密钥 (格式 `xxxxx...:fx`) 填进「API密钥」框
(多个逗号分隔)。实测对比:

| 通道 | 速度 | 限速 |
|---|---|---|
| 匿名 DLX (无密钥兜底) | 1–4 句/秒 | 频繁 429, 需自适应节流+熔断 |
| **官方免费 API** | **20+ 句/秒** | 宽松; 每号每月 50 万字符起 (实测 2026-10 注册为 100 万, 以界面显示为准) |

整批 13 个游戏 15,000+ 句实测: 官方API通道 **零 429**, 只消耗 22.6 万字符额度。
没配密钥且日志频繁出现 429 时, 界面会主动提示去申请; 跑完每个游戏后额度自动刷新,
快用完会提前提醒 (可再注册一个号, 逗号分隔填入即可)。

## 翻译通道与 429 限速 (无密钥时的兜底)

DeepL 匿名网页接口按 **出口 IP** 限速。工具为每个"独立额度来源"建一条通道,
每条通道由专属线程使用并自适应节流: 遇 429 自动放大请求间隔 (上限 30s, 连续
4 次进入 ≥45s 冷却), 持续成功后缓慢提速; 429 响应若带 `Retry-After` 照办;
持续限速触发**全局熔断** (全体通道暂停约 2 分钟), 429 不消耗重试次数,
可翻句子不会被误判为永久失败。**通道越多, 总吞吐越高**:

1. **直连 DLX** (本机出口 IP) — 端口 `dlx_port`
2. **代理 DLX** (每个代理一条) — `proxy_url` 逗号分隔可填多个
3. **DeepL 官方免费 API** (每密钥一条) — 见上一节

各通道端口严格隔离 (直连=`dlx_port`, 代理依次 `dlx_proxy_port`+1…),
即使某通道健康检查偶发失败也不会抢占相邻通道的实例。
跑完后日志给出限速统计: `请求 N 次 / 429 共 M 次 / 全局熔断 K 次` (分通道),
运行报告 (见下) 里也逐通道记录。

## 批量预翻译与完成标记

- 批量跑完一个游戏且**校验全一致、无永久失败**时, 会在该游戏翻译目录写
  完成标记 `.pt_done.json`; 之后再点批量会自动跳过它。
- 有遗留句 (永久失败) 或校验不一致的游戏**不会**被标记 —— 下次批量会重跑补齐
  (断点全命中, 只补缺口, 不重复消耗额度)。
- 单个游戏跑完同样写标记; 「强制重翻」勾选时忽略标记与已有翻译全部重来。
- 命令行批跑 `python batch_run.py <游戏根目录>` 与 GUI 批量共用同一套标记判定。

## 断点与缓存维护

- 翻译进度逐句实时存到工具目录 `cache/<游戏名-hash>/zh_map.jsonl`,
  停止/崩溃/断网后重跑自动续, 只翻没翻过的句子。
- 界面「**缓存维护…**」列出全部缓存目录及状态 (在用 / 重复 / 孤儿):
  - **重复**: 同一游戏因历史路径写法不同产生的多份缓存, 一键合并 (按句去重, 不丢句子)
  - **孤儿**: 对应游戏已移动/删除后残留的缓存, 可选中删除
- 有「永久失败」句时状态栏会提示: 直接再点一次「▶ 开始预翻译」即可只补翻遗留句。

## 运行报告

每次预翻译结束, 工具目录 `run_report.jsonl` 追加一行 JSON:
时间、游戏、翻译句数、断点复用数、速度、永久失败数、分通道请求/429统计、
写出对数、校验结果、字体补丁结果、官方API额度消耗。纯文本可长期留存复盘。

## 日文原文从哪来? (三级来源, 自动回退)

1. **翻译包**: 游戏自带其他语言目录 (如 `Translation/en`), 或「添加外部翻译来源…」
   指定 XUAT 结构翻译包 (含 `Text/` 或 `RedirectedResources/`, 行格式 `日文=某种译文`)
2. **游戏文件直接提取** (没有翻译包时自动启用):
   - Managed 程序集: 解析 .NET `#US` 字符串堆 (界面/标签文本, 精确)
   - Unity 资源文件: `level*` / `*.assets` 等按 UTF-8 扫描含假名的行与日文连续段
3. **已有翻译**: 目标语言目录里已有的中文 (人工或历史机翻) 自动保留并跳过

## 语言识别与名词一致性

- **源语言自动识别**: 每句独立判定日文(JA)/英文(EN), 分别按对应源语言翻译;
  XUAT 的 `FromLanguage` 自动取多数语言。
- **名词一致性 (术语表)**: 翻译前自动挖掘高频片假名名词 (默认上限300个),
  先统一翻译成固定译名再送翻——全文人名译名一致。语法词/代词在停用表中。
- **自定义术语表**: 「术语表…」每行写「日文原文=固定译名」(`//` 注释),
  覆盖机器译名; 改完勾「强制重翻」生效。
- **多行整串提取**: 多行原文按整串提取, 并合成"任意连续行段"的组合键
  (行译文拼接, 零额度); 同时把 `MaxCharactersPerTranslation` 抬到 1000。
- **不可译句**: DeepL 原样返回的句子 (如嵌入资源的 JSON 配置碎片) 记空译文标记,
  重跑跳过、绝不写入游戏。

## 中文字体缺字修复 (三级方案)

日文游戏的字体图集只烘了日文字形, 简体专用字 (获/游/选…) 会显示方框。

1. **根治 (默认)**: 静态 SDF 图集烘焙 — 把译文用到而原图集没有的简体字渲染成
   SDF 写进原 TMP 图集 (4096²→4096×8192, 新像素追加 `.resS`, 字形/字符表同步重建),
   游戏保持 Static 模式, 运行时**不会**调用原生加字接口 (部分 Unity 2022 上必崩)。
   原文件自动备份 `.bak-PreTranslator`; 「选择字体…」可指定 TTF/TTC,
   缺省自动挑雅黑粗体。幂等: 重复打补丁先还原再烘, 不会膨胀。
2. **诊断**: 旧「静态图集改动态」路线保留为 `font_patch_mode="dynamic"`, 仅排查用。
3. **保底**: 图集不可烘焙 (日志/界面会提示「图集不可烘焙」) 或没装 UnityPy 时,
   把译文缺字替换为原图集覆盖的日式写法 (説/歩/済 等), 并生成
   `cache/<游戏>/font_missing.txt` 缺字清单。

详细文件级改动与排查见 **[docs/操作手册.md](docs/操作手册.md)** 与
**[docs/TMP字体补丁排查经验.md](docs/TMP字体补丁排查经验.md)**。

## 重要: 必须使用修复版 DLX

2026-10 起 DeepL 风控拉黑了 utls 的 iOS TLS 指纹, **官方版 DLX 会持续 429**
(提示 "IP blocked", 实为误报)。请使用已删除 `SetTLSFingerprintIOS()` 的修复版
`deeplx-fixed.exe` (仓库内置; 根因与重新编译方法见 [DLX修复说明.md](DLX修复说明.md))。
工具自动查找: 配置 `dlx_path` → 工具目录内的 `deeplx-fixed.exe`。

## 第三方组件与致谢

| 组件 | 来源 | 说明 |
|---|---|---|
| DeepLX (DLX) | [OwO-N1/DeepLX](https://github.com/OwO-N1/DeepLX) | 仓库内 `deeplx-fixed.exe` 为其重新编译版, 仅移除 `SetTLSFingerprintIOS()` 一行调用 (原因见 DLX修复说明.md) |
| XUnity.AutoTranslator | [bbepis/XUnity.AutoTranslator](https://github.com/bbepis/XUnity.AutoTranslator) (MIT) | 仓库内 `SetupReiPatcherAndAutoTranslator.exe` 为其 ReiPatcher 发行包安装器, 未做修改 |
| BepInEx | [BepInEx](https://github.com/BepInEx/BepInEx) | IL2CPP 游戏首次运行时按需下载解压 |

翻译由 DeepL 提供 (匿名网页接口 / 官方免费 API)。本工具不收集任何数据, 全部文件仅保存在本机。

## 许可证

本项目代码以 [MIT License](LICENSE) 发布。内置的第三方可执行文件版权归各自上游所有。

## 特性一览

- **零依赖**: 纯标准库跑完整流程; 可选 `pip install UnityPy fonttools numpy pillow scipy` 启用图集烘焙
- **批量队列**: GUI 多选顺序跑 + 完成标记跳过 + 断点续跑
- **多通道并行**: 官方API×N + 直连DLX + 每代理一条DLX, 端口严格隔离, 各通道独立节流
- **自适应节流 + 全局熔断**: 429 不烧重试次数; 分通道 429/熔断统计
- **额度监控**: 启动校验密钥并显示用量, 跑完自动刷新, >80% 提醒
- **二分补翻**: 整批失败(503等)自动对半拆分, 隔离"毒苹果"句子
- **安全写入**: 写入前备份 `zh-CN.backup-日期`; 人工翻译行原样保留
- **往返校验**: 写出后按 XUAT 解析规则重新读回比对 (含缺字替换后的值)
- **缓存维护**: 重复缓存一键合并、孤儿缓存清理
- **运行报告**: 每次运行一行 JSON, 长期可复盘
- **分享包**: 「导出分享包」生成脱密 zip (不含路径/缓存), 对方解压即用

## 命令行 (无界面)

```
python pretranslator.py --nogui scan       # 列出检测到的游戏
python pretranslator.py --nogui check      # 检测直连/代理/DLX/API密钥/额度
python pretranslator.py --nogui translate --game <游戏目录> [--extra-source DIR]...
                                           [--limit N] [--dry-run] [--redo]
python pretranslator.py --nogui fontpatch --game <游戏目录> [--font FILE]
                                           [--mode bake|dynamic] [--dry-run]
python pretranslator.py --nogui share      # 生成脱密分享包
python batch_run.py <游戏根目录>            # 批量: 装 XUAT + 逐个预翻译 (完成标记跳过)
python selftest.py                         # 格式/引擎/字体/报告 自测
```

## 配置 (config.json, 与主程序同目录)

| 键 | 默认 | 说明 |
|---|---|---|
| `proxy_url` | `http://127.0.0.1:7897` | 代理地址, 逗号分隔可多个 (每个代理一条 DLX 通道) |
| `deepl_keys` | 空 | DeepL 官方免费API密钥, 逗号分隔可多个 |
| `dlx_path` | 自动查找 | deeplx-fixed.exe 路径 |
| `dlx_port` / `dlx_proxy_port` | 1188 / 1189 | 直连/代理通道起始端口 (严格隔离不串位) |
| `target_lang` | `zh-CN` | 写入的翻译目录名 (zh-TW 时 DLX 用 ZT) |
| `scan_roots` | `[]` (全部固定盘+Steam库) | 额外扫描根目录 |
| `ignored_games` | `[]` | 移出扫描列表的游戏根目录 |
| `games` | `{}` | 每个游戏的 `extra_sources` 外部翻译来源 |
| `font_tmp_patch` | `true` | 是否打 TMP 缺字补丁 |
| `font_patch_mode` | `"bake"` | `bake`=静态图集烘焙 (推荐); `dynamic`=仅诊断 |
| `font_bake_file` | 空 | 烘焙用字体文件路径; 空=自动挑选 |
| `font_safe_substitute` | `true` | 补丁不可用时把缺字替换为日式写法 |

## 目录结构

```
PreTranslator/
├── pretranslator.py        # 入口: GUI / CLI
├── pt_gui.py               # tkinter 图形界面
├── batch_run.py            # 命令行批量 (与 GUI 共用完成标记判定)
├── selftest.py             # 自测 (格式/引擎/限速/报告/维护)
├── gui_smoketest.py        # GUI 离屏冒烟测试
├── deeplx-fixed.exe        # 内置修复版 DLX (第三方, 见 DLX修复说明.md)
├── SetupReiPatcherAndAutoTranslator.exe  # 内置 XUAT 安装器 (第三方)
├── pt_core/                # 核心库
│   ├── games.py            #   游戏检测 (BepInEx+XUAT 特征, Steam库解析)
│   ├── extract.py          #   日文原文提取 (XUAT 格式解析)
│   ├── gamestrings.py      #   程序集/资源直提 (多行整串)
│   ├── dlx.py              #   DLX 服务托管 (静默启动/健康检查/端口隔离)
│   ├── netcheck.py         #   直连/代理/出口IP/官方API额度检测
│   ├── engine.py           #   翻译引擎 (多通道/自适应节流/熔断/断点/二分补翻)
│   ├── runlog.py           #   运行报告 + 完成标记 + 缓存维护
│   ├── writer.py           #   写出与往返校验 (备份/保留人工行)
│   ├── xuformat.py         #   XUAT 文件格式 (转义/注释/编码)
│   ├── fontpatch.py        #   TMP 缺字补丁总控
│   ├── fontbake.py         #   SDF 图集烘焙
│   ├── fontcover.py        #   字体覆盖分析与缺字替换 (保底方案)
│   ├── fontmap.py          #   图集字形表/字符表布局解析
│   └── config.py           #   配置持久化
├── testdata/FakeGame/      # 合成假游戏 fixture (selftest 用)
├── docs/                   # 操作手册 + 字体排查经验
├── logs/                   # DLX 实例日志 (运行时生成, 不入库)
├── cache/                  # 断点缓存 (运行时生成, 不入库)
├── config.json             # 配置 (运行时生成, 不入库 — 含密钥)
├── run_report.jsonl        # 运行报告 (运行时生成, 不入库)
├── DLX修复说明.md
├── LICENSE
└── 启动预翻译工具.bat
```

## 常见问题

- **第一次启动游戏没有中文?** 三种常见原因:
  1. **IL2CPP 游戏 (目录里有 GameAssembly.dll) 首跑先生成 interop 组件**,
     这一次 XUAT 尚未生效; 正常退出后再启动一次即可 (黑屏/启动慢属正常)。
  2. **XUAT 配置缺失**: 旧版工具翻完不生成配置, XUAT 首跑会按自身默认值
     (`Language=en`) 初始化, `zh-CN` 预翻译永远不被加载 —— 现版预翻译和
     「▶ 启动游戏」都会自动预生成/修正 (`Language=zh-CN`)。存量游戏可重跑
     一次预翻译 (断点全命中, 不花额度) 或直接点「▶ 启动游戏」补齐。
  3. **文字不在提取范围**: 标题菜单图片、视频内嵌字幕 (FMV 游戏)、Unity
     Localization 资源表 (StreamingAssets/aa 下的 bundle) —— 这类文字工具
     提取不到, 需用游戏自带的语言选项或等游戏内切换。
- **游戏还是日文?** 确认 `BepInEx/config/AutoTranslatorConfig.ini` 里
  `Language=zh-CN`、`FromLanguage=ja`; 看游戏窗口左上角是否出现加载条。
  个别句子缺失是正常的 (动态拼接文本 XUAT 查不到), XUAT 的在线兜底端点会补。
- **想改某句翻译?** 直接编辑游戏 `Translation/zh-CN/` 下对应文件
  (`ReloadTranslationsOnFileChange=True` 时保存即热生效)。
- **批量为什么又跑了一遍某个游戏?** 它上次有永久失败句或校验不一致, 没写完成标记;
  重跑只补缺口 (断点全命中, 不花额度)。若反复出现, 看状态栏"永久失败"数字与日志。
- **中文显示方框 / 缺字?** 确认勾了「缺字修复(TMP图集烘焙)」且装好可选依赖;
  日志出现「图集不可烘焙」时工具已自动走保底 (日式写法), 也可「选择字体…」后强制重翻。
- **进游戏直接闪退?** 旧版动态补丁所致: 改回 `font_patch_mode="bake"`,
  从 `.bak-PreTranslator` 还原资源后重打。
- **额度快用完了?** 界面「翻译通道」行实时显示用量; 再注册一个 DeepL Free 账号,
  逗号分隔填入「API密钥」即可扩容。
