# 《异键演奏》(NTE-GuitarFlow) 项目总览与架构地图

- **项目名称**：《异键演奏》(NTE-GuitarFlow)
- **GitHub 仓库**：`CiaLion/NTE-GuitarFlow`（默认分支：`main`）
- **当前基线版本**：`v1.4.0`
- **受管文件数**：`43`（见 `version.json`）
- **定位**：面向《异环》(Neverness to Everness) 游戏内乐器演奏（吉他轮盘 / 吉他 36 键 / 钢琴 36 键）的「AI 音频智能扒谱 + 乐谱多声部分轨 + 物理合规 MIDI 编配 + 高精度键鼠执行」一体化绿色便携桌面工具。

---

## 1. 双环境隔离与目录拓扑

为兼顾「基础包极小体积分发（~296 MB）」与「本地 AI 显卡/CPU 推理互不冲突」，系统采用严格的双虚拟环境物理隔离架构：

```text
NTE-GuitarFlow/
├── 双击启动-异键演奏.bat          # 主启动入口（自动提权 + 调用 heal_venv.ps1）
├── core_utils.py                  # 共享基础设施（ROOT、便携 venv 自愈、fit_screen、流式 sha256、RotatingFileHandler）
├── main.py                        # Python 入口（便携环境路径自愈 + 启动 GUI）
├── gui_main.py                    # App Shell 主窗口（StageBackdropWidget + 168px NavRail + 隐藏 tabBar）
├── gui_theme.py                   # 星曜暗夜 / 学院星昼玻璃拟态双主题 QSS
├── ui_widgets.py                  # 共享视觉组件库（26 矢量图标、舞台底图、星环头像、头图横幅、玻璃卡片、音域热力条）
├── config.json                    # 运行时路径与默认设备配置
├── version.json                   # 热更新受管清单（43 个核心代码/依赖清单的 sha256）
├── updater.py / update_manifest.py# 多镜像增量热更新器与清单生成器
├── requirements.txt               # 主程序层依赖（PyQt5, mido, pretty_midi, numpy, soundcard, Pillow）
├── runtime/python311/             # 嵌入式 Python 3.11.9 运行时（~44 MB）
├── .venv/                         # 主程序虚拟环境（~250 MB，不含 PyTorch）
├── assets/                        # 窗口图标、舞台/乐器头图 ui/ 美术资产与内置测试曲 sample_demo.mid
│   └── ui/                        # 8 张原创舞台背景与暗夜/浅昼双版本乐器艺术头图（stage_bg_*.jpg / hero_banner_*.jpg）
├── tests/                         # 自动化测试套件（不入分享包与受管清单）
│   ├── test_keyboard_modes.py     # 36 键编译、互斥、句柄防泄漏、二分读谱缓存与工坊/卷帘/UI 回归测试
│   └── test_midi_live.py          # 三模式实时 MIDI 直弹引擎、WinMM 枚举、踏板、焦点守卫与互斥联动测试
├── player/                        # 演奏编译与乐曲库核心模块（21 个 .py）
│   ├── gui_b_legacy.py            # 三层音乐工作站演奏台（HeroBanner + 音域热力条 + DAW 控制舱 + MIDI 直弹联动）
│   ├── keyboard_layout.py         # 36 键（吉他钢琴键/钢琴）映射、音域分析、移调与互斥编译器
│   ├── keyboard_preview.py        # 交互式 36 键位图弹窗（含 Shift/Ctrl 移位指示与曲目热度）
│   ├── midi_live.py               # 纯 ctypes WinMM 实时 MIDI 键盘映射与三模式低延迟直弹引擎
│   ├── midi_live_ui.py            # 可视化实时 MIDI 键盘直弹工作台（61 键交互试弹琴条 + 轮盘/36 键状态面板）
│   ├── midi_adapter.py            # MIDI 轨道识别、和弦切片、2P Z 键混切、琶音与独奏合流
│   ├── midi_compiler.py           # 物理时序约束、束搜索指法规划、预调度、36 键路由与二分反推
│   ├── midi_loader.py             # O(log N) 二分 tempo 查表与 32 槽 LRU MIDI 缓存（返回隔离拷贝）
│   ├── piano_roll.py              # 双模式钢琴卷帘编辑器（视口裁剪优化、星曜指针/音符与红音拦截）
│   ├── multipart_studio.py        # 乐曲库工坊（Songsterr 双 CDN 搜谱、多乐器分轨清洗、双轨对齐器）
│   ├── guitar_plan.py / chord_map.py / modes.py / plan.py # 游戏吉他 6 弦 24 品与 24 和弦几何/指法真相源
│   ├── game_io.py / input_engine.py / timeline_runner.py  # Win32 EnumWindows 无泄漏检测、SendInput 与倍速修饰保护
│   ├── player_config.py           # 时序预设（30/60/90 帧）、配置读写与 logs/tmp_wav 管理
│   ├── recorder.py                # WASAPI 系统声音环回内录
│   └── check_env.py               # 显卡、驱动、模型权重与音频设备体检
├── transcriber/                   # AI 扒谱调度与 MIDI 转换层
│   ├── gui_a_legacy.py            # 扒谱页界面（双勾直出 / 单选人声·器乐前置分离、实时双阶段进度条）
│   ├── pipeline.py                # 三步流水线（① 转录 → ② 对齐合并 → ③ 音域归位导出）
│   ├── convert.py                 # MIDI/JSON 读写、TrackNotes/ChordSlice 数据模型与 chord.lab 解析
│   └── selftest.py                # 导出层离线自检（音域/单音/和弦轨数量）
├── vendor/sheetsage2/             # 隔离的 AI 推理与音源分离层（基础分享包不含其 .venv 与大权重）
│   ├── .venv/                     # AI 推理虚拟环境（PyTorch XPU/CUDA/CPU + transformers + audio-separator 0.30.2）
│   ├── transcribe_cli.py          # SheetSage2 无界面转录入口
│   ├── sep_runner.py              # HT-Demucs 与 Mel-Band RoFormer 音源分离入口（含多镜像断点续传）
│   ├── roformer_device.py         # Intel Arc XPU 混合设备推理引擎（STFT/复数留 CPU + Transformer 上 XPU）
│   ├── patched_model_py/          # 增量解码与硬件路由模型补丁
│   ├── requirements-base.txt      # AI 转录基础依赖
│   ├── requirements-sep.txt       # 音源分离依赖（锁定 audio-separator==0.30.2）
│   └── models/                    # 本地模型目录（SheetSage2、MERT-v2-FullSong、separation/）
├── setup/                         # 一键安装、镜像探测与环境修复 PowerShell 脚本
├── docs/                          # 项目架构、物理算法与开发归档文档（**不纳入热更新清单**）
├── out/                           # 用户扒谱成品、导入曲目与多声部分轨文件夹
└── logs/                          # 运行日志、tmp_wav 临时音频、backup_prev 更新备份、trash 回收站
```

---

## 2. 绿色便携与零外部残留机制

1. **目录移动自愈**：`main.py` 与 `gui_main.py` 启动最开头调用 `heal_portable_venvs(ROOT)`，自动检测 `.venv/pyvenv.cfg` 与 `vendor/sheetsage2/.venv/pyvenv.cfg` 的 `home` 和 `executable` 路径，若与当前 `runtime/python311` 不一致则立即重写，支持任意重命名或跨盘符移动文件夹。
2. **缓存与临时文件 100% 锁死目录内**：
   * `HF_HOME` → `vendor/sheetsage2/.hf_cache`
   * `TORCH_HOME` → `vendor/sheetsage2/.torch_cache`
   * `NUMBA_CACHE_DIR` → `vendor/sheetsage2/.numba_cache`
   * `TMP / TEMP`（子进程环境，`pipeline._make_env()` 注入）与所有试听 WAV（`player_config.tmp_wav_path()`）→ `logs/tmp_wav/`
   * 严禁向 `C:\Users\<USER>`、`AppData` 或系统 `%TEMP%` 写入数据，删除软件文件夹即彻底卸载。
3. **打包黑名单**（`verify_release.py` 检查 4 强制拦截）：`_backup/`、`logs/trash/`、`.numba_cache/`、`.torch_cache/`、`.hf_cache/`、`gui_settings.json`、`transcriber/gui_settings.json`、`verify_release.py`、`_verify_7f.py`、`PROJECT_CONTEXT.md`。其中 `.numba_cache/*.nbc` 内嵌本机绝对路径，绝不能随包分发。

---

## 3. 发布与热更新工作流

1. **增量热更新**：修改受管代码后，在项目根目录运行 `.venv\Scripts\python.exe update_manifest.py --set 1.4.0 --keep-changelog` 刷新 `version.json`（纳管 43 个核心代码与依赖清单文件，排除 `docs/`、用户配置与测试脚本）。将改动文件连同 `version.json` **一起**推送至 GitHub `main` 分支后，客户端通过多镜像（`ghfast.top` / `ghproxy` / GitHub Raw）校验 `sha256` 与 `ast.parse()` 语法树后原子替换并备份旧文件至 `logs/backup_prev/`。
   * **铁律**：只传代码不传 `version.json` 会导致客户端下载后哈希校验失败、整批放弃更新。
   * **版本语义**：`updater.check_for_update()` 在本地版本高于远端时返回 `ahead`，界面隐藏更新按钮并拒绝覆盖；同版本号仍按文件哈希判断增量。
2. **最小分享包分发**：基础分享包（~296 MB）包含 `runtime/python311`、主 `.venv`、全部受管源码及 `vendor/sheetsage2/models/` 下约 379 KB 的模型结构配置（排除 `vendor/sheetsage2/.venv` 与 `*.safetensors/*.th/*.ckpt` 大体积权重，由用户按需在软件内一键安装）。

---

## 4. 本地环境快照（2026-11-14 13:00 实测扫描）

### 4.1 三条目录的实测状态

| 目录 | 角色 | 实测体积 | 文件数 | 状态 |
|---|---|---|---|---|
| `C:\异键演奏` | 母本开发目录 | **9,737.6 MB** | 66,236 | 受管 37 个文件；与 GitHub `main` 逐字节一致（`player/player_config.json` 除外，见下） |
| `F:\异键演奏` | 绿色分享包（免安装分发） | **296.6 MB** | 7,418 | 冒烟 `GUI_SMOKE_OK`；残留检查 0 命中 |
| `F:\异键演奏v1.3` | 增量受管仓（待上传 GitHub） | **1.8 MB** | 52 | 37 个受管文件 + `version.json` + 8 份阶段报告 md + `docs/` 4 份归档文档；与母本 sha256 **100% 一致** |

> 同步口径：以 GitHub `origin/main` 为准。`player/player_config.json` 属于用户运行期配置（`updater.PROTECTED_FILES` 明确保护），**故意不同步**，保留本机设置。

### 4.2 母本体积构成

| 子目录 | 体积 |
|---|---|
| `vendor/sheetsage2/.venv`（AI 推理环境，**不入分享包**） | 5,596.0 MB |
| `vendor/sheetsage2/models`（模型权重，**不入分享包**） | 3,825.7 MB |
| `.venv`（主程序环境） | 249.8 MB |
| `runtime/python311`（嵌入式 Python） | 44.1 MB |
| 其余（源码 / out / logs / _backup） | 约 22 MB |

### 4.3 核心模块最新行数（2026-11-26 v1.4.0 实测）

| 文件 | 行数 |
|---|---|
| `player/piano_roll.py` | 3,577 |
| `player/gui_b_legacy.py` | 3,181 |
| `player/midi_compiler.py` | 1,809 |
| `player/midi_adapter.py` | 1,533 |
| `transcriber/pipeline.py` | 1,476 |
| `player/midi_live.py` | 1,459 |
| `gui_main.py` | 1,444 |
| `player/multipart_studio.py` | 1,338 |
| `transcriber/gui_a_legacy.py` | 1,337 |
| `player/midi_live_ui.py` | 1,196 |
| `player/keyboard_layout.py` | 1,100 |
| `player/keyboard_preview.py` | 992 |
| `ui_widgets.py` | 779 |
| `player/midi_loader.py` | 628 |
| `tests/test_keyboard_modes.py` | 594 |
| `tests/test_midi_live.py` | 544 |
| `player/timeline_runner.py` | 524 |
| `gui_theme.py` | 515 |
| `updater.py` | 505 |
| `player/player_config.py` | 466 |
| `player/input_engine.py` | 349 |
| `vendor/sheetsage2/sep_runner.py` | 340 |
| `core_utils.py` | 156 |

### 4.4 最近一次发布门禁与自动化验收（v1.4.0，待封板）

- 执行日期：**2026-11-26**，结论 **通过**。
- 专项自动化测试：
  - `tests/test_keyboard_modes.py`：**17 / 17 PASS**（36 键与互斥编配、200 次 `find_game_window()` 句柄零泄漏、`build_time_converter()` 二分精度 `< 1e-12`、`read_flat_notes()` 32 槽 LRU 缓存隔离与 mtime 失效、`reschedule_for_speed()` 统一委托、`core_utils` 工具集、`_timing_seq` 并发竞态防御、`assets/ui/` 8 张素材与无图兜底渲染）；
  - `tests/test_midi_live.py`：全通过（WinMM 设备枚举、`guitar_36` 延音/踏板/`repluck_gap`、`piano_36` 无延音与 `LShift`/`LCtrl` 严格互斥、`guitar_wheel` 轮盘寻品/0 品免点/跨页同扇区必点击/单键与多音 24 和弦、移调折叠/忽略、焦点守卫、`MidiLiveDialog` 试弹、双向联动与自动演奏并发互斥）。
- 母本核心门禁与审美验收：
  - `main.py --smoke`：`GUI_SMOKE_OK`；
  - `verify_release.py` 核心项全 PASS：底层 18 键 / 界面 6 项 / 三档 `late=0`、Solo 空隙琶音（249 颗 / 0.217s / 跑调 0）、卷帘红框拦截、四角色事件 `2476 / 4358 / 6512 / 2868`（`late` 全 0）、1P 红线 `(631, 1, 0, 2476)`、`sha=2b6bd8260a920084`、模式 B 卷帘闭环（Solo `1241/729` / WAV `6/6` / 1.5x 倍速保持）；
  - 去敏扫描 0 命中、源码/配置显卡休眠补丁清零、CRLF 保持验证全通过；
  - `update_manifest.py --verify`：43 个受管文件（`v1.4.0`）校验一致；
  - 全场景离屏视觉审阅（浅昼/暗夜主窗、演奏台三乐器、扒谱页、36 键位图、36 键卷帘、61 键实时 MIDI 直弹工作台）无截断、无重叠、无越界。
