# 《异键演奏》游戏物理机制与核心算法规范

> 本文是代码行为的「真相源说明」。数值均可在源码中找到对应常量，改动前先读第 3 份文档《工程红线与历史踩坑录》。

## 1. 《异环》吉他物理真相源

### 1.1 音域与定弦映射

- **有效物理音域**：MIDI `40` (E2) 至 `88` (E6)，共 49 个半音。超出边界的音符按八度折叠（`< 40` 循环 `+12`，`> 88` 循环 `-12`）。
- **六弦空弦基准**（`midi_compiler.OPEN_PITCH`，项目内部统一 **1 = 粗弦低音 E，6 = 细弦高音 e**）：

  ```python
  OPEN_PITCH = {1: 40, 2: 45, 3: 50, 4: 55, 5: 59, 6: 64}
  ```

- **单帧大横按物理约束**：
  - 品格模式下，同一 `15 ms` 时间帧内的多根弦必须位于**完全相同的品格** `f ∈ [0, 24]`（即 `Pitch(s, f) = OPEN_PITCH[s] + f`）。
  - 25 个品格划分为 5 页：第 1~4 页（Page `0~3`，每页 6 个品格，对应轮盘 6 个扇区），第 5 页（`PAGE_ZERO = 4`）代表 0 品空弦页，**进入第 5 页无需点击轮盘扇区，且不篡改当前轮盘扇区记忆**。

### 1.2 和弦模式与 Z 键混切物理特性

- **和弦模式**：`chord_map.CHORD_STRINGS = 24`（4 页 × 6 扇区），单音拨弦仅使用剔除小二度错音后的安全弦集合（`pluck_safe_strings`）。
- **Z 键模式切换两项硬规律**：
  1. 按下 `Z` 在「和弦模式 ↔ 品格模式」之间切换时，**页码强制重置回第 1 页（Page 0）**；
  2. **轮盘扇区位置跨模式继承**（例如和弦模式停在 3 号扇区，按 `Z` 切到品格模式后仍处于第 1 页 3 号扇区，无需重复点击同一扇区，防止触发游戏内的左键扫弦误判）。

---

## 2. 物理时序三定律与预调度算法（`midi_compiler.py` / `player_config.py`）

设目标帧率为 `FPS`，单帧物理毫秒数 `F = ⌊1000 / FPS⌋`（30 帧 = `33ms`，60 帧 = `16ms`，90 帧 = `11ms`）。`player_config.build_timing_for_frame_ms()` 按 `F` 生成整套时序：

| 物理动作类型 | 时序公式 | 30 帧默认 | 60 帧均衡 | 90 帧高刷 | 说明 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1. 松开触发延迟**（翻页 `T/R`、切模式 `Z`）—— 键名 `after_page_key_release` | **`F + 2 ms`** | `35 ms` | `18 ms` | `13 ms` | 翻页/切模式键松开后，需等待新音高生效的最小间隔 |
| **2. 按住时长与同弦连按间隔** —— `pluck_hold / fret_click_hold / select_click_hold / strum_click_hold / page_key_hold / page_turn_gap / repluck_gap` | **`F + 1 ms`** | `34 ms` | `17 ms` | `12 ms` | 拨弦、翻页、点选轮盘的按键按住时长及同键重按间隔 |
| **3. 按下即触发衔接延迟** —— `min_action_interval` | **统一 `1 ms`** | `1 ms` | `1 ms` | `1 ms` | 鼠标左键按下点选轮盘扇区后仅需 1ms 即可立刻按数字键拨弦 |

### 关键调度规则

1. **键鼠双通道并行**：点选扇区时，在目标发声时刻 `t_on` 前 `2ms` 移动光标、前 `1ms` 按下鼠标左键（立即选中扇区）、`t_on` 正点按下琴弦数字键，左键按满 `F+1 ms` 后自然松开（`_interlock` 互斥锁仅拦截带 `sound=True` 的鼠标扫弦，绝不提前截断选格左键）。
2. **「跟随本曲参数」二分搜索反推（`deduce_song_timing`）**：不使用静态分位数猜测，直接遍历候选单帧毫秒数 `(33, 28, 24, 20, 16, 14, 11)` 调用真实 `preschedule`（可结合删音滑块 `prune_ratio` `0% ~ 15%`，默认 `0%` 完全不删伴奏），取使 `late == 0` 的最大安全帧时长 `F*`。

---

## 3. 角色视图、自动伴奏总开关与 2P 混切算法（`midi_adapter.py`）

1. **「开启和弦自动生成伴奏」总开关（`enable_auto_accomp`）**：
   - **开启（默认）**：`1P 主音 / 独奏` 路由至 `ROLE_SOLO`（主旋律锚定 + 同把位双音/低音 + 句间颗粒琶音合流），两个 2P 伴奏角色正常可选；
   - **关闭**：两个 2P 角色及子开关置灰禁用，`1P 主音 / 独奏` 路由至 `ROLE_DUET_LEAD`（100% 纯主音旋律，不混入任何自动伴奏）。
2. **2P 伴奏 `Z` 键智能混切与琶音编配规则**：
   - 当切片和弦音级能被 24 和弦安全弦覆盖时，优先留在和弦模式（0 翻页）；遇到超出 24 和弦的色彩音/挂留音时，自动插入 `Z` 键切入品格模式补齐。
   - **琶音步长与声部连续性铁律**：
     - 琶音步长严格锁定原曲半拍节拍网格，**绝不因换页/切模式预留窗口而压缩步长**（宁可末尾少弹最后一颗音，绝不抢拍加速）；
     - 音符序列严格按音高升序排列（`sorted(set(pool + extra))`），保留 `40~46` 真实低音根音（禁止切入品格模式时强行把低音升八度）；
     - 3 音和弦在 4/4 拍下自动扩展为 4 音往返型 `[c0, c1, c2, c1]`，保证强拍永远对齐根音。

---

## 4. 乐曲库（Songsterr / 多轨 MIDI）解析与清洗规范（`multipart_studio.py`）

### 4.1 Songsterr 分轨 JSON 解析

- 弦号 `n["string"]` 为 **0 基索引（`0 ~ len(tuning)-1`）**，音高为 `tuning[string] + capo + fret`，写入内部弦号 `string = 6 - raw_string`；越界时回退 `OPEN_PITCH[6 - string]`。
- **打击乐过滤三层**：
  1. 元数据层：`str(t.get("instrumentId") or "") == DRUM_INSTRUMENT_ID`（`"1024"`），或 `name + instrument` 命中 `DRUM_HINT`；
  2. 分轨 JSON 层：`js.get("isDrums") == True` 时直接标记"打击乐轨已跳过"；
  3. 本地 MIDI 导入层：`track.is_drum` 或轨名命中 `DRUM_HINT`（`read_midi_notes()` 会统计 `skipped_drums`）。
- `DRUM_HINT` 当前为词边界版本，避免 `kit` 之类的子串误伤普通轨名：
  `r"\b(drum|drums|percussion|cymbals?)\b|\bdrum\s*kit\b|\btama\b"`（`re.IGNORECASE`）。

### 4.2 轨道命名（`_ascii_name()` —— 与 GitHub `main` 现行实现一致）

MIDI 轨名只能是 latin-1，因此写盘时按下列顺序确定轨名（`write_parts()` 逐轨调用）：

| 顺序 | 判断 | 输出轨名 |
| :--- | :--- | :--- |
| 1 | 精确命中 `ASCII_TRACKS`（`人声轨 / 主音吉他轨 / 节奏吉他轨 / 贝斯轨 / 键盘轨 / 全声部试听`） | `Vocal / Lead Guitar / Rhythm Guitar / Bass / Keys / Full Mix` |
| 2 | 名字本身是纯 ASCII（`Yukina (vocals)`、`Guitar Lead _ Sayo`、`Bass _ Lisa`…） | **原样保留**（截断 64 字符） |
| 3 | 含非 ASCII，但命中关键词：`vocal / 人声`、`lead / 主音`、`rhythm / 节奏`、`bass / 贝斯`、`piano / key / 键盘` | `Vocal / Lead Guitar / Rhythm Guitar / Bass / Keys` |
| 4 | 其余（日文/中文且无关键词） | `Part 1` |

**为什么轨名重要**：`midi_adapter.is_melody_track()` 靠名字里的 `vocal / lead / melody` 认主旋律，`midi_compiler.pick_track()` 靠 `SOLO_MASTER / DUET_P1_LEAD / DUET_P2_RHYTHM` 选轨。轨名一旦全部退化成同一个占位名，播放器只会按"轨号 1"兜底，整首曲子只播一小段（历史事故：FIRE BIRD 5,799 个音只播了 42 个）。

**已知限制**：第 4 种情况恒为 `Part 1`（`write_parts()` 未向 `_ascii_name()` 传轨序号），因此一首曲子里若有多条"无关键词的非 ASCII 轨"，它们会同名。要修的话，把 `write_parts()` 里的调用改成 `_ascii_name(prefix, i)` 并让 `_ascii_name` 用 `index` 拼 `Part N` 即可——但这属于代码改动，当前版本以 GitHub `main` 为准，未落地。

**文件名**仍沿用原曲谱轨道名：`<原轨道名>-<歌名>.mid`，与 MIDI 内部轨名解耦。

### 4.3 分轨清洗 vs 试听总谱

- **独立分轨**：经 `_fold_notes` 折叠至 `[40, 88]` 后执行 `sanitize_fret_frame(notes, frame_ms=15.0, constraint_mode="guitar_fret")`——同帧枚举 `0~24` 品，严格保留 `pitch == top` 的最高音原音高，不跨八度篡改低音，剔除同帧异品格冲突音，保证导入演奏台 0 红音。
- **`全声部试听-<歌名>.mid`**：写入**未做同品格剔除**的完整音符（饱满和声），供试听与人声对齐参考；因此它比各分轨"看起来"音更多，属预期行为。
- **人声对齐全流程**：对齐器把参考轨/待对齐轨画在同一画布上，`确认对齐并覆盖保存` 时——
  1. 目标文件整体重写为单条 `Vocal` 轨；
  2. 同名目录下的 `全声部试听-*.mid` 复用 `_is_vocal_name()`（含 `vocal` / `人声`）定位人声轨并**就地替换**，旧版写坏的 `Track N` 残留一并收敛，不会再"每保存一次多一条"。

### 4.4 前置分离与和弦轨的取舍

- `transcriber.pipeline.step3(..., with_chords=True)` 默认导出 `Main Melody + Chords` 两轨。
- **一键全流程**按 `stems` 决定：`stems == "vocal"`（人声轨）时传 `with_chords=False`，最终 `人声轨-<歌名>.mid` **只有一条 Main Melody 轨**，不会给 1P 演奏塞进 2P 伴奏；`stems == "inst"`（乐器轨）与 `stems == "both"`（合轨）仍保留和弦轨。
- 单步「步骤③」与 `transcriber/selftest.py` 使用默认值，行为保持两轨不变。

---

## 5. v1.4「吉他 36 键 / 钢琴 36 键」物理机制与互斥编译算法（`player/keyboard_layout.py`）

### 5.1 三乐器模式与物理音域

| 模式常量 | 中文名 | 物理音域 | 延音 | 修饰键约束 |
| :--- | :--- | :---: | :---: | :--- |
| `guitar_wheel` | 吉他 · 轮盘（品格/和弦） | `40..88` (E2~E6) | 支持（`sustain_max`） | 无（轮盘点选 + T/R/Z） |
| `guitar_36` | 吉他 · 钢琴键（36 键） | `52..87` (E3~D#6) | 支持（`sustain_max`） | 无（36 个独立按键直按） |
| `piano_36` | 钢琴 · 36 键 | `60..95` (C4~B6) | **无延音**（一律 `pluck_hold`） | **LShift / LCtrl 严格互斥** |

### 5.2 物理键位表与列不影响规则

1. **吉他 · 钢琴键（`guitar_36`，52 E3 .. 87 D#6）**：
   - 低音 `52..63`：`A S D F G H J K L ; ' Enter`
   - 中音 `64..75`：`Q W E R T Y U I O P [ ]`
   - 高音 `76..87`：`1 2 3 4 5 6 7 8 9 0 - =`（其中 `'0'..'9'` 走 `VK_NUMBERS` 虚拟键码通路，其余走硬件扫描码通路）
2. **钢琴 · 36 键（`piano_36`，60 C4 .. 95 B6，最低音位于左下角 C4）**：
   - 三行各 7 个基键：低音 `Z X C V B N M`、中音 `A S D F G H J`、高音 `Q W E R T Y U`。
   - 八度内半音偏移 `pc (0..11) -> (col_idx, mod)`：
     `0:(0,None)`、`1:(0,'LShift')`、`2:(1,None)`、`3:(2,'LCtrl')`、`4:(2,None)`、`5:(3,None)`、`6:(3,'LShift')`、`7:(4,None)`、`8:(4,'LShift')`、`9:(5,None)`、`10:(6,'LCtrl')`、`11:(6,None)`。
   - **列不影响铁律**：
     - `LShift` 仅影响列 `{0, 3, 4}`（`QRT / AFG / ZVB`）；列 `{1, 2, 5, 6}` 在按住 `LShift` 时仍发自然音。
     - `LCtrl` 仅影响列 `{2, 6}`（`EU / DJ / CM`）；列 `{0, 1, 3, 4, 5}` 在按住 `LCtrl` 时仍发自然音。

### 5.3 编译与并发调度算法

1. **`guitar_36` 编译（`compile_guitar_36`）**：
   - 支持任意多音并发；`dur >= sustain_min` 时按住 `min(dur, sustain_max)`，否则按 `pluck_hold`（下限 `MIN_HOLD = 0.005s`）。
   - 同物理键重按时提前截断前一次松开时刻，确保松开至下次按下间隔 `>= repluck_gap` 且按住 `>= MIN_HOLD`。
2. **`piano_36` 互斥双批编译（`compile_piano_36`）**：
   - **同帧最多拆 2 批**：因为 `LShift` 兼容集 `{LShift, None@col∉{0,3,4}}` 与 `LCtrl` 兼容集 `{LCtrl, None@col∉{2,6}}` 的并集覆盖全部 36 个音高，任意同帧（`≤15ms`）多音和弦必然能在 1~2 批内完整发声，**0 漏音**；仅含单修饰键与受影响自然音时，拆为该修饰批与 `None` 批，最高音所在批优先准点发出。
   - **修饰键包络时序**：批前 `min_action_interval (1ms)` 按下 `LShift/LCtrl` → 正点按下本批基键 → `pluck_hold` 后松开基键 → `min_action_interval` 后松开修饰键；若批 B 与批 A 共用物理基键，批 B 自动顺延至 `repluck_gap` 之后。任意时刻 `LShift` 与 `LCtrl` 绝不并发按下。
   - **倍速与紧急停播保护（`timeline_runner.py`）**：`reschedule_for_speed()` 在倍速重排时保证 `LShift/LCtrl` 的 `key_up` 时刻不早于被其修饰音符的最大 `key_up + min_action_interval`；`release_all()` 先释放普通按键，最后释放修饰键。
3. **音轨筛选、智能移调与分轨清洗**：
   - `select_notes_by_scope()` 支持 `all`（合并非鼓轨去重）、`melody`（主旋律优先）、`accomp`（和弦/伴奏优先）；`analyze_range_and_transpose()` 在 `-12..+12` 半音内搜索最优移调，并结合 `fold_pitch()` 按八度折叠入物理音域。
   - `multipart_studio.sanitize_fret_frame()` 已启用 `constraint_mode="guitar_36" | "piano_36"` 分支，折叠至目标音域并对同帧同音高去重，保留完整多音和声。

---

## 6. v1.4 三模式实时 MIDI 键盘映射与直弹引擎（`player/midi_live.py`）

1. **64 位 WinMM 低延迟采集与噪声过滤（`WinMMMidiInput`）**：
   - 纯标准库 `ctypes` 封装 `winmm.dll`（`HMIDIIN = HANDLE`、`DWORD_PTR = c_size_t`、`CALLBACK_FUNCTION = 0x00030000`、`MIM_DATA = 0x3C3`、`MIM_CLOSE = 0x3C2`）；
   - 回调实例强绑定至 `self._cb_ref` 防 GC，回调线程在解析 `status = p1 & 0xFF` 后立即丢弃 `status >= 0xF0` 系统实时/公共噪声消息（如 Timing Clock `0xF8`、Active Sensing `0xFE`、Start/Stop `0xFA/0xFC`），仅将有效音符与控制器消息轻量入队 `queue.SimpleQueue`；
   - `WinMMMidiInput.open()` 对 `midiInOpen` 返回错误码 `4`（设备被 DAW/浏览器占用）抛出明确中文占用提示；消费线程启用 `WinTimer(1)` 并按 `chord_window_ms`（默认 `8.0ms`）微聚合同时按下的和弦音符（`guitar_36` 模式下直接以 `get_nowait()` `0ms` 透传跳过和弦窗干等）。
2. **三模式映射、非阻塞状态机与防卡键跟踪**：
   - **`guitar_36` (`52..87`)**：支持长按延音（保底 `>= pluck_hold`、超时截断 `<= sustain_max`）、`CC64 >= 64` 延音踏板滞留至松开后统一释放，同键重弹前强制先释放并等待 `repluck_gap`；通过 `_g36_note_map[(ch, raw_pitch)]` 与物理键 `holders` 引用计数集合跟踪按下时的键位映射，即使按住中途修改 `transpose` / `out_of_range` 或多通道八度折叠共用同键，`Note Off` 仍精准释放且互不误切。
   - **`piano_36` (`60..95`)**：**无延音**（`Note Off` 与踏板不延长按键，每音一律按 `pluck_hold`）；同微窗 `Note On` 复用 `keyboard_layout` 的列不影响规则最多拆 2 批执行，任意时刻 `LShift` 与 `LCtrl` 绝不并发按下。消费线程运行态（`_running=True`）启用 `_p36_active` / `_p36_active_mod` / `_p36_mod_up_t` **非阻塞异步释放状态机**：
     - 修饰保护提前量由 `mod_lead_ms`（默认 `2.0ms`，钳位 `1.0..20.0ms`，下限 `>= min_action_interval`）控制；
     - 单批或双批拆分的末批在按下键后立即非阻塞返回，交由 `_consume_loop` 的 `_next_expiry_timeout()` 精度定时唤醒 `_flush_expired()` 先放普通键、满 `mod_lead` 后再放修饰键；连续同修饰（或同无修饰）音符复用当前已按住修饰键走零阻塞快路径；双批拆分时批 1 持满 `hold` 并释放旧修饰后再切批 2。
   - **`guitar_wheel` (`40..88`)**：
     - `fret` 子模式：维护 `(page, sector, fret)` 状态机，优先 0 翻页 0 换格 → 同页换扇区 → 第 5 页 0 品免点扇区 → 最短 `T/R` 翻页（跨页后无论扇区号是否与旧扇区相同均触发左键选格，仅同页同扇区跳过）；支持按住延音、`CC64` 踏板与 `_wheel_note_map[(ch, raw_pitch)]` / `holders` 引用计数跟踪；
     - `chord` 子模式：支持 `single_key_chord`（MIDI `48..71` 一对一触发 24 和弦）与多音和弦窗实时匹配 `guitar_plan.ALL_CHORDS`（右键下扫或安全弦柱式抓弦），切换新和弦前释放旧残留按住弦与 `_wheel_chord_note_map` 记录并保证 `repluck_gap`。
   - **全品牌 CC 协议**：`_handle_cc()` 支持 `CC 120 / 123`（All Sound Off / All Notes Off）立即全释放所有活跃音、踏板滞留与修饰键并发射提示事件；支持 `CC 121`（Reset All Controllers）重置 `_pedal_down = False` 并释放全部踏板滞留音。
3. **即插即用多端口热插拔、全键数适配与焦点/UIPI 桥梁检测**：
   - 严格隔离虚拟与硬件模式：`start(None)` 纯虚拟试弹绝不读取 `config["device_name"]`；`start("all" | "auto" | "*")` 开启原生即插即用全端口模式，打开窗口即自动连接可用 MIDI 输入（0 台设备时静默进入 `waiting_device` 等待状态而不报错）；指定 `int` ID 或非空设备名进入单口模式；消费线程每 `1.5s` 静默巡检 `list_midi_inputs()` 并响应 `MIM_CLOSE`，断线自动 `release_all()` 发 `disconnected`，设备插入/重连自动打开并发 `device_connected`，提供 `connected_device_names()` / `connected_device_name()` / `is_hardware_connected()` 与 `devices` 状态字段；
   - 可视化键盘条（`LiveKeyboardStrip`）支持 **25 / 37 / 49 / 61 / 76 / 88 键**全规格自适应缩放与角标提示，所有模式（含 `guitar_wheel` 与越界忽略）在松键或失焦/停音时均发射 `note_off` / 清空高亮；「MIDI 通道（Ch1~Ch16）」、「忽略 Ch10 鼓轨」、「合批窗」与「修饰提前」收纳在可折叠高级参数面板中；
   - `focus_guard=True` 时仅在游戏窗口处于前台且未按 `Alt/Win` 时调用 `GameIO` 发键鼠（并对 `find_window() == None` 施加 `0.2s` `_last_window_miss_t` 节流缓存避免未开游戏时高频枚举拖慢轮询），切出瞬间自动调用 `release_all()`（先释放普通键与鼠标，最后释放 `LShift`/`LCtrl`）；非前台时仍通过 `on_event(..., injected=False)` 驱动界面实时预览；
   - 提供只读 `check_game_bridge_status(io=None)`（及 `LiveMidiEngine.check_game_bridge_status()`），通过 `OpenProcess(0x1000)` + `OpenProcessToken(0x0008)` + `GetTokenInformation(TokenElevation=20)` 探测游戏前台状态及管理员 UIPI 提权拦截风险；
   - 与演奏台自动播放通过 `on_before_start` / `stop_if_running()` 严格互斥，禁止双路同时向游戏注入键鼠。
