# TMP 字体崩溃排查经验（从 0xC0000005 到静态烘焙）

本文是一次真实故障的完整复盘：日文 Unity 游戏打上"中文字体补丁"后启动崩溃，
先后出现过**两个不同的崩溃地址**，最后定位到根因并改成完全绕开原生代码的方案。
里面每一条结论都附了当时的证据和踩过的坑，后面的同类问题可以直接照方抓药。

> 涉及的游戏用「巢系列独立版」代称。引擎 Unity 2022.3.62f2（Mono + ReiPatcher/XUAT）。

---

## 1. 一句话结论

- **不要在这类游戏上用 TMP 动态图集加字**。托管侧检查全绿（纹理可读、Alpha8、
  原生指针非零、`GetGlyphIndex` 能拿到字形）的情况下，原生
  `FontEngine.TryAddGlyphToTexture` 依然会踩空指针。
- **正确方案是静态 SDF 烘焙**：把缺少的几百个字形离线渲成 SDF 写进原图集，
  图集保持 `m_AtlasPopulationMode=0`，游戏运行时**永不调用**那段原生代码。

---

## 2. 两个崩溃签名，别把它们当成一个

| 时间 | 异常代码 | 模块偏移 | 补丁版本 | 崩溃含义 |
|---|---|---|---|---|
| 2026-10-25 23:48 起 | 0xC0000005 | `UnityPlayer.dll+0xcf7589` | 原始动态补丁 | 空对象虚调用 |
| 2026-10-26 00:55 起 | 0xC0000005 | `UnityPlayer.dll+0x47aa8d` | "修了 PointSize" 之后 | 坏指针引用计数释放 |

`0xcf7589` 处指令是 `mov rax,[rbx]`（rbx=0）——对一个空对象取虚表；
`0x47aa8d` 是 `lock xadd [rcx+4],eax`（rcx=8）——对一个空对象做 AddRef/Release。
**两次崩溃其实是同一个根因、同一个调用点**，只是栈上对象为空时先踩哪一步不同。

### 坑：把 float 写进 int 字段

前任的判断是"`m_FaceInfo.m_PointSize` 被写成了 0"，于是补了一条"修复"：

```python
cur, = struct.unpack_from('<f', new_raw, h['style_end'])
if not 8 <= cur <= 512:
    struct.pack_into('<f', buf, h['style_end'], 36.0)   # ← 错误
```

`m_PointSize` 在 TextCore 里是 **int**。原值本来就是 77（按 float 读当然是 1e-43，
看起来像 0）。写进去的 `36.0f` 位模式是 `1108344832`，等于把一个健康字段砸坏，
于是崩溃从 A 点挪到了 B 点，**看起来像"修了但还崩"**。

教训：
- **字段类型要以程序集元数据为准**，别从二进制反推。用 Mono.Cecil 读一遍
  `UnityEngine.TextCore.FaceInfo` 的字段顺序和类型，比反汇编猜快得多。
- 反汇编只能证明"这台机器上这条指令崩了"，不能证明"这个字段的值是错的"。
  崩溃转储里 `Rax=0x429a0000`（= float 77.0）其实已经把答案写在脸上了。

---

## 3. 取证链条（按性价比排序）

### 3.1 Windows 事件日志：最快拿到崩溃地址

```powershell
Get-WinEvent -FilterHashtable @{
  LogName='Application'; ProviderName='Application Error'
  StartTime=(Get-Date).AddDays(-30)
} -MaxEvents 200 | Where-Object { $_.Message -match '<游戏名>' }
```

从 Message 里正则抽 `异常代码：` / `错误偏移：` / `出错模块名称：`。
这一步就能判掉"两个崩溃点是不是同一个"。

### 3.2 崩溃转储：拿寄存器

- `C:\ProgramData\Microsoft\Windows\WER\ReportArchive` 下的报告目录**经常全是
  `Access denied`**，pdb/mdmp 拿不到。
- 真正的 minidump 在 **`%TEMP%\<Company>\<Product>\Crashes\Crash_*\crash.dmp`**
  （本例是 `E:\Temp\...`，因为 TEMP 被改过）。每个目录旁边还有同刻的 `Player.log`。
- 手写 minidump 解析（不需要 windbg）：`MINIDUMP_HEADER` → `Directory` →
  `ModuleList`(108B/条，`ModuleNameRva` → UTF-16 串) / `ThreadList`(48B/条) /
  `ExceptionStream`（ExceptionRecord 在 +8，ExceptionAddress 在 +16，
  NumberOfParameters +24，ExceptionInformation +32…）。
  `CONTEXT_AMD64` 的寄存器从 `ThreadContext rva + 0x78` 起按 Rax…Rip 排列。
- **注意**：minidump 默认只采栈和少量页，堆地址查不到（`--mem` 会说
  "not in dump"）。要判断"某个参数是不是 0"，看寄存器比看内存可靠。

### 3.3 反汇编：没有 dumpbin 也能干

机器上可能一个反汇编器都没有（capstone/dumpbin/objdump/r2/windbg 全缺）。
`pip install` 也可能因 `OSError: [Errno 13] Permission denied` 失败（临时目录不可写）。
可行做法：**直接 `urllib` 拉 wheel 再用 `zipfile` 解压**到工作目录，
`sys.path.insert` 后 `import capstone` 就能用。

自己写个 150 行的 PE 解析（段表 + `.pdata` 的 RUNTIME_FUNCTION）配合 capstone 即可。
**坑**：`.pdata` 的函数边界不可靠（实测覆盖目标地址的条目比真实函数小一截），
只能当提示，不能当"函数入口"用。

### 3.4 托管侧：Mono.Cecil 反编译 + IL 注入探针

- 用 `Mono.Cecil` 读游戏 `Managed\*.dll`，能拿到**字段顺序/类型**（权威）、
  方法 IL（看前置条件链）、枚举真实取值（`GlyphRenderMode.SDFAA=4165`、
  `GlyphPackingMode.BestShortSideFit=0` 这类，别信文档版本）。
- IL 注入探针是本例的转折点：在 `TryAddCharacterInternal` 里脚本化插入
  `Debug.LogError` 打印 `m_AtlasTextures[0]` 的 `GetInstanceID()` /
  `isReadable` / `GetNativeTexturePtr()` / `format` / 宽高。
  结果：**全部正常**，从而把"托管侧状态错误"整条排除，只剩下原生。

注入器的三个必踩坑：
1. **必须更新 `MaxStackSize`**，否则插进去的序列超过原方法栈深度 → 校验失败/行为异常。
2. **注入源必须是原版 DLL**。拿已注入过的 DLL 再注入会双重注入
   （IL 长度翻倍、锚点变成上一条注入指令）。
3. 探针里**不要引用 `IntPtr`/`System.Runtime.InteropServices` 之外的现代 API**，
   否则游戏侧会刷 `FileNotFoundException: Could not load file or assembly
   'System.Private.CoreLib, Version=10.0.0.0'`（用 .NET 10 的编译器编出来的引用
   在 Mono 运行时里加载不到），表现为**文字全部变白框**。

### 3.5 沙箱：启动游戏必须在完整权限下

`workspace-write` 沙箱里启动游戏会 **6 秒后 code=1 退出，`Player.log` 完全不更新**——
因为游戏要写 `C:\Users\<用户>\AppData\LocalLow\<Company>\<Product>\Player.log`，
被挡在工作区外。杀掉进程也一样（`PermissionError: [WinError 5] 拒绝访问`
出现在 `capture_output=True` 的管道创建上）。用 `DEVNULL` + `terminate()/kill()`，
并给足文件权限。

---

## 4. 为什么最后放弃"动态加字"

排除实验（每次从 `.bak` 还原，只改一个变量）：

| 实验 | 改动 | 结果 |
|---|---|---|
| S | 运行时用新建 `Texture2D` 顶替图集纹理 | 仍崩（`GetInstanceID` 变成 -114，确认换新） |
| E1 | 不换字体（用原版 Rounded M+），只改动态 + 清表 | 仍崩 |
| E1b | 禁用 XUAT 插件 | 仍崩 |
| 探针 | 托管侧断点打印全部字段 | 全绿 |

反汇编桥接函数后看到真相：传给原生的第 10 个参数是 `[font+0x58]`，
**恒为 NULL**，而它在 icall 后被直接拿去 `lea rcx,[rbx+8]; call Release`。
这个子对象带引用计数（`+0xc` AddRef / `+8` 基址 Release）、有虚表、
`+0x140` 有返回 bool 的虚函数——是字体面（FT_Face）体系的对象，
**不是图集纹理**（否则实验 S 会改变结果）。

结论：这台机器上"动态加字"这条路是死路，任何托管侧修补都救不了。
于是改为完全绕开：静态烘焙，让运行时根本不进这段代码。

---

## 5. 静态烘焙的坑（实现细节）

1. **图集行序和 PIL 相反**。按 `rect` 从图集里切出来的字形看着是正的，
   但游戏里显示成上下颠倒（"获"看起来像"洋"、"游"像"淞"）。
   写入时必须 `bmp[::-1]` 翻转整块位图。
2. **字形索引不能用表序号**。原字形的 `m_Index` 是 FreeType 原生字形序号
   （散列分布，实测到 8222），新字形要从 `max(m_Index)+1` 起编号，
   否则会与旧字形撞键导致张冠李戴。
3. **`len(list)` 不是字节数**。`gi = g_n + len(glyph_bytes)//52` 里
   `glyph_bytes` 是元素列表不是 bytes，`//52` 让所有新字形索引都变成同一个值。
4. **原图集基本没有空闲空间**（实测 used 2537 块占 96.9%），
   所以只能**加高**图集（4096x4096 → 4096x8192），旧像素整块搬到新纹理上半区。
5. **图集尺寸字段的位置不能硬编码**。它跟在遗留 `m_fontInfo` 的族名字符串后面，
   偏移随名字长度变化（一个资产是 fi_at+96，另一个是 fi_at+20）。
   按 `(宽, 高, 0<=pad<=32, 4000<=renderMode<=200000)` 四元组在 fi_at 之后扫描。
6. **`.resS` 会重复膨胀**。每次烘焙都把新图集整块追加到 `.resS` 末尾，
   连跑两次就 +64 MB。用 sidecar 文件 `<resS>.origlen` 记住原始长度，
   每次先 `truncate` 回去再追加。
7. **em 不能取最大值**。第二次烘焙时，上次写进去的字形带着别的字号 advance，
   取 max 会把 em 从 77 带偏到 83，新字就会大小不一致。
   改取 **90 分位**（CJK 的 advance 高度集中，几千个字同为 77）。
8. **可重复执行**：烘焙前先把 `*.bak-PreTranslator` 还原回原文件。
   否则第二次会从"已经加高过的图集"继续加高（8192 → 16384 → 32768）。
9. **字符集必须来自译文**。早期实现用日文原文当字符集——日文字形原图集本来就有，
   简体专用字（获/游/选）一个都没烘进去，等于白干。正确做法是在翻译完成之后，
   用 `译文 ∪ 原文` 的字符集烘焙。

---

## 6. 字体选择

把原图集里的字（如"好男身中"）和候选字体同尺寸渲染做视觉对比，
按笔画粗细挑最接近的：

- Rounded M+ 1c Bold（原版）：笔画最粗、转折圆润
- **Microsoft YaHei Bold（`msyhbd.ttc`）：字重最接近** ← 默认选它
- SimHei：明显偏细，一眼能看出混排
- 幼圆 / 等线 / Noto Sans SC：更细

另外 SDF 的 `pad`（`m_AtlasPadding`，本例是 5）必须与原图集一致，
`m_AtlasRenderMode`（本例 4165 = SDFAA）和 `TextureFormat`（1 = Alpha8）同理，
否则新字形会糊或者被判定成不可烘焙。

---

## 7. 复用清单

给未来的自己：

1. 先看事件日志拿崩溃地址，确认是"同一个点"还是"挪窝了"。
2. `%TEMP%` 下找 `Crashes\Crash_*\crash.dmp`，用现成解析器取寄存器，
   看"那个可疑指针"到底是不是 0。
3. 字段类型/枚举取值一律用 Mono.Cecil 从程序集里读，别猜。
4. 需要判断"托管侧到底传了什么"时，IL 注入探针比反汇编便宜一百倍。
5. 只有确认"托管侧全绿且原生侧对空指针动手"之后，才去改数据格式绕开那条代码。
6. 绕开方案（静态烘焙）落地时，把"行序、索引、容量、尺寸字段位置、幂等、
   字符集来源"这六个点逐条自检一遍。
