# 《异键演奏》版本演进与验收记录

## v1.4.0

**对外更新日志（写进 `version.json` 的 4 条）**

1. 新增吉他钢琴键(36键)与钢琴(36键)自动演奏全链路适配，含 2×2 智能织体编配与内联 36 键位图
2. 新增三模式实时MIDI键盘映射与WinMM直弹工作台（支持热拔插、25~88键适配、24和弦与延音踏板）
3. 底层UI重构：单窗体零弹窗工作站、星曜暗夜/学院星昼双主题玻璃拟态、内置5章《使用指南》与并发极速检查更新
4. 底层架构深度优化：修复Win32句柄泄漏与推导竞态、升级二分读谱缓存与卷帘视口裁剪、统一基础设施与全链路健壮性加固

**详细变更**

### 36 键编译核心与底层输入（`player/keyboard_layout.py` / `input_engine.py` / `game_io.py` / `timeline_runner.py` / `midi_compiler.py`）

- 新增 `player/keyboard_layout.py` 作为三乐器模式与 36 键物理键位的唯一真相源：
  - `guitar_36`（`52..87`, E3~D#6）：三行 36 键直按，支持长按延音截断至 `sustain_max`、任意多音并发及同键 `repluck_gap` 重按保护；
  - `piano_36`（`60..95`, C4~B6）：21 基键 + `LShift`/`LCtrl` 严格互斥，利用列不影响特性将同帧冲突音符最多拆成 2 批（0 漏音），批前后保留 `min_action_interval` 修饰包络，无延音一律按 `pluck_hold`；
  - 提供 `all / melody / accomp` 多轨范围筛选、`fold_pitch` 八度折叠及 `-12..+12` 半音覆盖率分析与智能移调建议。
- 强化输入执行链：`input_engine.py` 补全 `ZXCV` 行/`LShift`/`LCtrl`/`Enter` 扫描码与 `VK_NUMBERS['0']`，`_send_key()` 优先用 `MapVirtualKeyW(sc, 3)` 区分左右修饰键；`game_io.py` 兼容大小写键名与 `'0'..'9'` 拨弦；`timeline_runner.py` 在 `release_all()` 先放普通键再放修饰键，并在 `reschedule_for_speed()` 保护修饰键包络不被倍速早松。
- `midi_compiler.py` 扩展 `compile_file` / `compile_custom_role` / `deduce_song_timing` / `report_text` 路由至 36 键管线，`guitar_wheel` 保持原路径零改动；`reschedule_for_speed()` 统一委托 `timeline_runner.reschedule_for_speed()` 消除实现分歧。

### 底层架构与性能优化（`core_utils.py` / `input_engine.py` / `midi_loader.py`）

- 新增 `core_utils.py` 统一提供 `ROOT`、`heal_portable_venvs()`、`fit_screen()`、流式分块 `sha256_file()` 及延迟初始化 `RotatingFileHandler` `get_logger()`（输出至 `logs/app.log`），由 `main.py`、`updater.py`、`update_manifest.py` 与各业务模块共同复用。
- `player/input_engine.py` 重写 `find_game_window()`：采用 `_LAST_GAME_HWND` 轻缓存复验 + `EnumWindows` 主路径，仅在回退时尝试 `OpenInputDesktop` 且通过 `try/finally CloseDesktop` 与桌面句柄还原彻底消灭内核句柄泄漏。
- `player/midi_loader.py` 将 `build_time_converter()` 升级为 `bisect.bisect_right` O(log N) 查表，并为 `read_flat_notes()` 增加 32 槽 `(abs_path, mtime_ns, size, drop_drums, tracks)` LRU 缓存（命中返回隔离拷贝并提供 `clear_midi_cache()`）。

### 原创美术资产、星曜双主题与单窗一体化工作站（`ui_widgets.py` / `gui_theme.py` / `gui_main.py` / `guide_dialog.py` / `updater.py`）

- 引入 `assets/ui/` 8 张原创舞台背景与暗夜/浅昼双版本乐器艺术头图；新建 `ui_widgets.py` 提供 26 种超采样抗锯齿矢量图标及 `StageBackdropWidget`、`AvatarRingBadge`、`HeroBannerWidget`（右侧艺术主体零遮挡渐变遮罩）、`GlassCard`、`RangeMeterWidget`、`NavRailButton`。
- `gui_theme.py` 升级为「星曜暗夜 / 学院星昼」双主题玻璃拟态系统；`gui_main.py` 重构为左侧 182px `NavRail`（含缩写防截断 AI 状态胶囊与完整型号 tooltip）+ 右侧 6 页单窗内嵌切换的 App Shell，全面消除日常功能弹窗（MIDI 直弹、5 章《使用指南》、环境与显卡体检、检查更新均原生嵌入主窗），并针对 `1280×820` 整窗做零横向滚动裁切优化。
- 新增 `guide_dialog.py`：提供 5 章节双主题《使用指南》（30 秒极速上手 / 三种演奏模式 / MIDI 键盘直弹 / AI 扒谱与工坊 / 帧率与排错速查），支持首次启动柔和引导与「不再自动弹出」记忆。
- `updater.py` 升级为多镜像并发竞速探测（单源 4s 超时、最快有效源胜出，升级 `User-Agent/1.4`），补全 `version.json` 随更新原子备份（保持纯 CRLF）与回退还原闭环，受保护配置与 `out/` 永不覆写。
- `transcriber/gui_a_legacy.py` 顶部融入 `HeroBannerWidget("ai")` 并新增三乐器有效音域快捷预设；新建 `player/keyboard_preview.py` 支持在演奏页原地展开交互式 36 键位图（随 `piano_36` Shift/Ctrl 三态动态移位高亮及热度发光）。

### 三模式实时 MIDI 键盘映射与直弹工作台（`player/midi_live.py` / `player/midi_live_ui.py` / `gui_main.py` / `player/gui_b_legacy.py`）

- 新增 `player/midi_live.py`：纯标准库 `ctypes` 封装 64 位 `winmm.dll`（回调防 GC 纯入队 `SimpleQueue` + `WinTimer(1)` 微聚合窗消费），提供 `list_midi_inputs()`、`describe_mode_mapping()` 与 `LiveMidiEngine`：
  - 支持 `guitar_36`（延音、CC64 踏板与 `repluck_gap`）、`piano_36`（无延音、列不影响双批与 `LShift`/`LCtrl` 严格互斥）、`guitar_wheel`（智能寻品/第 5 页 0 品免点/跨页同扇区必点击 + 单键/多音 24 和弦触发）；
  - 支持多端口热拔插自动监测重连、全局 `F12` 紧急松键联动与前台焦点守卫自动 `release_all()`（非前台仍驱动预览动画）。
- 新增 `player/midi_live_ui.py`：构建 `LiveKeyboardStrip`（支持 25/37/49/61/76/88 键规格动态适配与鼠标试弹）与 `MidiLiveDialog`；侧边导航栏与控制舱共用唯一实例，实现双向模式切换联动及与自动演奏 SendInput 的严格并发互斥，并接入跨权限桥接检测预警。

### 三层工作站演奏台、2×2 智能织体编配、曲库工坊与钢琴卷帘（`player/gui_b_legacy.py` / `player/kb_arranger.py` / `player/multipart_studio.py` / `player/piano_roll.py` / `player/player_config.py`）

- 新增 `player/kb_arranger.py` 并在 `player/gui_b_legacy.py` 落地 36 键 **2×2 平铺编配矩阵**（原谱直出 `all/melody/accomp`、1P 独奏/主音带智能低音伴奏与句间琶音、2P 柱式/18ms 错峰扫弦、2P 分解琶音/根五律动），非当前角色子开关自动联动置灰隔离，配合 `RangeMeterWidget` 88 键音域热力条与一键智能移调。
- `player/gui_b_legacy.py` 将搜谱/多轨工坊、36 键位图、高级时间参数与通道自检全面内联嵌入工作站，增强损坏 MIDI 打开卷帘及空曲库异常边界防护；`player/player_config.py` 新增通用数值字段有限值与边界清洗，彻底防御异常配置文件导致校准圈或初始化报错。
- `player/multipart_studio.py` 扩展 `sanitize_fret_frame(..., constraint_mode="guitar_36"|"piano_36")`（保留完整多音和声），修复 `write_parts()` 多条无关键词非 ASCII 轨同名冲突，完善空搜索结果提示并接入主题同步。
- `player/piano_roll.py` 适配三乐器音域边界、键帽标签展示、36 键越界红框校验及钢琴清脆泛音合成试听，新增视口时间/音高双向裁剪（保留跨边界长音符）、流金菱冠播放指针、微光圆角音符与矢量工具栏图标，保持 `guitar_wheel` 门禁全量兼容。

---

## v1.3.0

**对外更新日志（写进 `version.json` 的 4 条）**

1. 新增乐曲库，可联网搜索下载乐曲 MIDI
2. 优化布局及游戏内演奏逻辑
3. 新增人声器乐分离功能
4. 修复若干 BUG

**详细变更**

### 乐曲库与人声手动对齐（`player/multipart_studio.py`）

- 接入 Songsterr 双 CloudFront CDN 搜谱与本地多轨 MIDI 导入；修复 `0-based` 弦号索引、连音线 `tie` 累加、`instrumentId=1024` 鼓轨过滤。
- 生成文件保留原曲谱轨道名作文件名（`<原轨道名>-<歌名>.mid`），MIDI 内部轨名走 `_ascii_name()`：`ASCII_TRACKS` 精确表优先，纯 ASCII 名字原样保留，含非 ASCII 时按 `vocal/人声`、`lead/主音`、`rhythm/节奏`、`bass/贝斯`、`piano/key/键盘` 映射，都不匹配给 `Part 1`；保证播放器能识别主旋律并正确合流。
- 独立分轨执行同品格非变调清洗（`sanitize_fret_frame`，0 红音），`全声部试听-<歌名>.mid` 保留完整和声。
- 重构双轨对齐画布：全曲滚动平移、滚轮缩放、时间/拍点标尺、网格吸附（自由 / 1/4 / 1/2 / 1 拍）、黄色播放指针任意位置起播、空格播放/暂停、关窗自动停播。
- 覆盖保存时人声轨**就地替换**（`_is_vocal_name()` 兼容 `Vocal / 人声轨 / 含 vocal 的原名`），旧版写坏的 `Track N` 残留自动收敛，不再每保存一次多堆一条。

### 演奏台与曲库（`player/gui_b_legacy.py` / `midi_compiler.py` / `midi_adapter.py`）

- 左侧曲库升级为 `QTreeWidget` 折叠树：新建文件夹、文件拖入/拖出、右键及 `F2 / Del` 重命名与删除（移入 `logs/trash`）、空文件夹可见。
- 新增「开启和弦自动生成伴奏」总开关，将两个 1P 角色合并为 `1P 主音 / 独奏`，形成 3 角色清爽布局。
- 落地物理时序三定律（`F+2ms` 松开延迟、`F+1ms` 按住时长、`1ms` 按下即触发），新增基于真实 `preschedule` 二分搜索的「跟随本曲参数」与 `0%~15%` 删音滑块（默认 `0%` 不删音）。
- 修复 2P 混切琶音音高乱跳、低音升八度断崖、3 音跨拍错位及换页前步长压缩问题。

### AI 扒谱与按需音源分离（`transcriber/` / `vendor/sheetsage2/`）

- 支持双勾直出与单选人声/器乐按需前置分离（内置直出 / `HT-Demucs` 约 319 MB / `Mel-Band RoFormer` 约 871 MB），锁定 `audio-separator==0.30.2`，并实现 Intel Arc XPU 混合设备推理。
- 分离阶段独立占用进度条 `3%~35%` 区间并实时读秒，分离完成后再平滑移交步骤 ① 的渐近曲线（旧版卡 `<32%` 上限导致长时间"假死"）。
- `setup_env.ps1` 补齐 `requirements-sep.txt` 安装（含"离线快通"分支的补装），修复 PowerShell 5.1 双引号吞噬导致 XPU/ROCm 被误卸降级的缺陷。
- 一键全流程选择**人声分离**时，`step3(..., with_chords=False)`，`人声轨-<歌名>.mid` 只导出 `Main Melody` 单轨，不带 2P 和弦轨；乐器轨与合轨仍为两轨。

### 更新机制与分发

- `updater.check_for_update()` 增加版本语义比较：本地版本高于远端时返回 `ahead`，界面隐藏「立即更新」并拒绝覆盖（防反向降级）；同版本号仍按文件哈希出增量。
- 受管清单扩至 **37 个文件**（纳入 `vendor/sheetsage2/requirements-base.txt`、`requirements-sep.txt`），`docs/` 永不纳管。
- 打包门禁新增黑名单：`_backup/`、`logs/trash/`、`.numba_cache/`、`.torch_cache/`、`.hf_cache/`、`gui_settings.json`、`transcriber/gui_settings.json` 等。
- 子进程临时目录（`TMP/TEMP`）锁进 `logs/tmp_wav/`，不再向系统 `%TEMP%` 泄漏。

### 界面精简与高分屏适配

- 钢琴卷帘重构为紧凑双行工具栏（含音量条与配置记忆）；第 1 行最小宽收敛至 1220px，150%（逻辑 1280px）下零越界，右侧「应用微调」按钮右边界 1274px。
- 主界面移出低频内录控件与冗余输入框；主窗口 `setMinimumSize` 下限 820×540。

---

## v1.2.0

- 新增左侧本地 MIDI 曲库列表与搜索过滤；
- 新增 `updater.py` 与 `update_manifest.py`，实现多镜像探测（`ghfast.top` / `ghproxy` / GitHub Raw）、`sha256` + `ast.parse` 双校验、`logs/backup_prev/` 原子热更新与回退；
- 将 18 项时间参数精简为 6 项核心参数弹窗，主界面提供 30/60/90 帧三档预设。

## v1.1.0

- 落地 2P 伴奏「和弦模式 ↔ 品格模式」`Z` 键智能混切（保留跨模式扇区继承与第 5 页空弦页无点击优化）；
- 接入 SheetSage2 原生 `chord.lab` 四音和弦与斜杠转位低音解析，修复高音弦丢失与独奏句间伴奏节奏问题。

## v1.0.0

- 完成 A 端（SheetSage2 AI 扒谱）与 B 端（《异环》吉他物理编译器与 SendInput 执行器）双模块合并为一体化桌面应用《异键演奏》，建立双虚拟环境隔离与 `verify_release.py` 自动化门禁。

---

## 验收记录

| 日期 | 版本 | 门禁 | 关键指标 |
|---|---|---|---|
| 2026-10-20 | v1.2 | 10/10 | 首版发布；分享包约 296 MB；受管 35 文件 |
| 2026-10-27 | v1.3.0 | 10/10（148.5s → 128.6s → 91.9s 多轮） | 分享包 295.8 MB；受管 37 文件；四角色 `late` 全 0；15s 转录 17~20s |
| 2026-11-14 | v1.3.0（归档收尾 + 以 GitHub 为准回同步） | **10/10（100.3s）** | 15s 转录 **14.49s**；分享包 296.6 MB、残留 0、冒烟 1.60s；去敏扫描 178 文件 0 命中；1P 红线 `(631,1,0,2476)`、`sha=2b6bd8260a920084` |
| 2026-11-26 | v1.4.0（三乐器 36 键、实时 MIDI 直弹、星曜双主题与底层重构） | **全量专项 + 核心门禁全过** | 受管 43 文件；`test_keyboard_modes.py` 17/17 + `test_midi_live.py` 全过；200 次找窗句柄零泄漏；1P 红线 `(631,1,0,2476)`、`sha=2b6bd8260a920084`；去敏 0 命中；双主题与实时直弹视觉验收通过 |

> 门禁原始数据：`logs/release_check_report.json`（每次运行覆盖）。四角色基准：`duet_lead 2476 / solo 4358 / duet_rhythm 6512 / duet_rhythm_fret 2868`，`late` 必须全为 0。
