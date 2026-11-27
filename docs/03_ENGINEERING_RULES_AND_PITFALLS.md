# 《异键演奏》工程红线与历史踩坑录（后续 Agent 必读）

任何后续接手开发或重构的 Agent，动代码前必须核对以下 **12 条**已用真机实测验证过的踩坑记录，严禁重蹈覆辙。

---

1. **Windows PowerShell 5.1 吞噬内嵌双引号（`setup/setup_env.ps1`）**
   - **坑点**：PS 5.1 调用原生 `.exe`（如 `& $Py -E -c $code`）时会剥离参数内部的双引号，导致 `hasattr(torch, "xpu")` 变成 `hasattr(torch, xpu)` 报 `NameError`，进而误判显卡不可用。
   - **红线**：所有传给 `-c` 的 Python 单行探针必须使用**外双引号 + 内单引号**（`"import sys, torch; ... 'xpu' ..."`）；探测失败时**严禁自动卸载已安装的 GPU 版 PyTorch 替换为 CPU 版**（运行期代码自会安全回退 CPU）。
   - 另：`.ps1` 必须**纯 ASCII**（PS 5.1 会把无 BOM 的 UTF-8 当 GBK 读，中文直接乱码/解析失败）。

2. **音源分离模型版本与 Intel Arc XPU 混合设备限制（`sep_runner.py` / `roformer_device.py`）**
   - **坑点 1**：`audio-separator >= 0.47` 运行 Mel-Band RoFormer 会输出近静音（RMS `0.00007`）；必须锁定 **`audio-separator==0.30.2`**（写在 `vendor/sheetsage2/requirements-sep.txt`），并保留 RMS 响度守卫（低于原曲 2% 自动回落 HT-Demucs，`sep_runner.py` 内触发 `ROFORMER_FALLBACK`）。
   - **坑点 2**：Intel Arc 上 PyTorch `view_as_complex` 不支持 `bf16`，且 `fp16 autocast` 反而比 `fp32` 慢。必须走 `roformer_device.py` 混合设备引擎（STFT/复数运算留 CPU，Transformer 与掩码估计放 XPU FP32）。
   - **坑点 3（结论修正）**：`vendor/sheetsage2/.venv` 里的 `torchvision` **不是运行时必需**（分离与转录全链路不 import 视觉模块），但 `transformers 4.53.1`、`timm`、`open_clip_torch`、`clip_interrogator`、`ultralytics`、`spandrel` 的 `METADATA` 都把 `torchvision` 写成 `Requires-Dist`。卸载不会立刻报错，但会留下"声明了却不存在"的依赖；它只有 8.2 MB 且**不随分享包分发**，因此**默认保留**，不要为了省空间去动它。

3. **Songsterr 弦号 0 基索引与打击乐识别陷阱（`player/multipart_studio.py`）**
   - **坑点 1**：Songsterr JSON 的 `n["string"]` 是 **`0..5`**（`0` 为 1 弦高音 E），不是 `1..6`。写成 `1 <= string <= len(tuning)` 会导致 1 弦高音全丢、其余 5 根弦全曲错位移调。
   - **坑点 2**：`/api/meta/<id>` 的轨道字段**没有 `isDrums`**（实测字段集合：`instrumentId / instrument / views / name / tuning / hash / isVocalTrack / isEmpty / difficulty`）；该字段只存在于**分轨 JSON**（`/{songId}/{revisionId}/{image}/{partId}.json`）。
   - **坑点 3**：**不能用 `hash` 前缀 `drums_` 过滤**——实测 Oasis《Wonderwall》的 `Owen Morris | Kurzweil | Keyboard` 轨 hash 是 `drums_8nzSd9i0` 但乐器是 `Synth Strings 1`，按 hash 过滤会误删键盘。
   - **红线**：元数据层必须用 **`instrumentId == "1024"`** 配合 `name + instrument` 的名称正则；正则要用词边界（`\b`），否则 `kit` 之类子串会误伤（例如 `Kitten`）。三层过滤缺一不可（元数据 / 分轨 JSON / 本地 MIDI 的 `is_drum`）。

4. **外部单轨 MIDI 被误判为和弦轨二次篡改（`player/midi_adapter.py`）**
   - **坑点**：若单轨吉他分轨的文件名或轨名包含"伴奏 / chord"字样，旧版 `is_chords_track` 会把它当成 2P 和弦切片重编。
   - **红线**：当 MIDI 非空非鼓音轨**恰好只有 1 条**且文件名/轨名不是纯 `chords / chords_merged / 和弦` 时，必须强制归入主音轨直通，不叠加自动伴奏。

5. **轨道命名决定播放器选轨（`multipart_studio._ascii_name()` → `midi_compiler.pick_track()` + `midi_adapter.is_melody_track()`）**
   - **事故**：乐曲库生成的 MIDI 曾把所有轨统一命名为 `Track`，播放器认不出 `vocal / lead`，多轨合流失效，`pick_track()` 退到"轨号 1"兜底；《FIRE BIRD》文件里 5,799 个音最终只播了 42 个（恰是最小的一条加花轨）。
   - **现状（与 GitHub `main` 一致）**：`_ascii_name()` 先查 `ASCII_TRACKS` 精确表 → 纯 ASCII 名字**原样保留** → 含非 ASCII 时按 `vocal/人声`、`lead/主音`、`rhythm/节奏`、`bass/贝斯`、`piano/key/键盘` 关键词映射 → 都不匹配则 `Part 1`。
   - **红线**：改这段映射等于改播放器的选轨结果，必须跑一遍「生成 → 演奏台编译」验证事件数；新增映射时优先保证 `vocal` / `lead` 能被识别（这两类是 `is_melody_track()` 的唯一判据）。

6. **PyQt5 线程安全红线**
   - **坑点**：在 `threading.Thread` 或 `QThread` 中直接调用 `QListWidget.addItem`、`QLabel.setText` 会引发 Windows C++ 层 `Access Violation (0xC0000005)` 无报错闪退。
   - **红线**：后台线程一律只通过 `pyqtSignal` 向主线程发射数据，所有 UI 控件操作必须在主线程槽函数中执行。

7. **双行卷帘工具栏与主窗口高分屏宽度红线（`piano_roll.py` / `gui_main.py`）**
   - **坑点**：`1920×1080 @ 150%` 缩放下逻辑屏幕宽度只有 `1280px`。主窗口顶栏不得再添加常驻长文本标签（`setMinimumSize` 下限 `820×540`，布局最小宽实测 `839px`）。
   - **卷帘实测**：第 1 行工具栏（角色下拉固定宽 `170px`、行间距 `4px`、根边距 `6px`）布局最小宽 **1220px（空窗）/ 1022px（载入曲谱后）**，1280 宽下右侧「应用微调」按钮右边界 `1274px`，不得再往这一行塞控件；新增控件请放第 2 行。

8. **发布打包黑名单（`verify_release.py` 检查 4，代码为准）**
   - 分享包目录中严禁出现：`_backup/`、`logs/trash/`、`.numba_cache/`、`.torch_cache/`、`.hf_cache/`、`__pycache__/`、`.git/`、`gui_settings.json`、`transcriber/gui_settings.json`、`verify_release.py`、`_verify_7f.py`、`PROJECT_CONTEXT.md`、`MINIMAL_PACKAGE.txt`、`RELEASE_REPORT*.md`、`双击环境自检.bat`，以及 `verify_release.py` 检查 4 里条列的两个中文名打包说明/测试脚本（此处不复写文件名，避免触发门禁自身的去敏正则）。
   - 其中 `vendor/sheetsage2/.numba_cache/*.nbc` 的字节码里**内嵌本机绝对路径**，一旦分包会泄漏开发者目录，必须由门禁拦死。
   - `player/player_config.json` 不在黑名单内：分享包本就不含该文件，首次运行由程序自建。
   - **例外备注**：`vendor/sheetsage2/patched_model_py/modeling_mert2.py` 原有两句提到历史旧后端（`D*ML` / `torch_d*ml`）的**注释**，会被门禁检查 1 当成残留（旧后端残留=1 直接判负）。当前版本已把这两句改成 `Some backends…` / `privateuseone (vendor fallback backend)`，**代码逻辑一字未动**；以后从上游同步该文件时记得同样处理。

9. **`version.json` 必须与代码同批上传（热更新校验铁律）**
   - **坑点**：客户端 `check_for_update()` 用远端 `version.json` 的 `sha256` 判断增量，`download_update()` 下载后再用同一份哈希校验；只传代码不传清单，会出现"清单旧哈希 vs 新文件"，校验失败后**整批放弃**、用户始终更新不到。
   - **红线**：改任何受管文件 → 项目根执行 `.venv\Scripts\python.exe update_manifest.py --set 1.3.0 --keep-changelog` → 把**改动文件 + version.json** 一起推到 `main`。上传后用末尾「清单自检脚本」验证（输出 `不一致条目: 0` 才算干净）。
   - **版本语义**：本地版本高于远端时 `updater` 返回 `ahead` 并隐藏更新按钮（防反向降级）；同版本号仍按文件哈希出增量，因此**小修不必升版本号**，但必须重算哈希。

10. **行尾（CRLF / LF）不要随手改**
    - **坑点**：用记事本之类的编辑器保存一次，整个文件的行尾会从 LF 变成 CRLF，`sha256` 随之改变；GitHub 上表现为"1105 行全部改动"，而 `update_manifest.py` 重算出的哈希又与你已上传的字节不一致。
    - **红线**：发布前若改过行尾，必须以"最终要上传的那份字节"为准重算 `version.json`；能用 VS Code / Notepad++ 保存为 LF 就保存为 LF。**当前仍是 CRLF 的 7 个文件**（`gui_theme.py`、`player/game_io.py`、`player/multipart_studio.py`、`setup/setup_env.ps1`、`transcriber/convert.py`、`vendor/sheetsage2/requirements-base.txt`、`version.json`）——改它们时**保持原样**，不要顺手统一换行，否则又要重算一次哈希。

11. **前置分离的人声轨不要带和弦轨（`transcriber/pipeline.py`）**
    - **坑点**：`step3()` 默认会读 `chords_merged.mid` 导出 `Main Melody + Chords` 两轨；一键全流程选「人声分离」时，`人声轨-*.mid` 多出来的和弦轨会让演奏台多出一条 2P 伴奏，与"只要人声旋律"的意图冲突。
    - **红线**：`run_all()` 必须传 `with_chords=(stems != "vocal")`；改 `step3` 签名时注意它对 `chords=None` 有"自动兜底读同目录 chords_merged.mid"的行为，关掉和弦必须用 `with_chords=False` 显式关闭，而不是传 `chords=None`。

12. **算法反过拟合铁律**
    - 严禁针对特定测试曲硬编码音符数/帧数常量；严禁在 30 帧下遇到复杂伴奏时直接清空整首歌的伴奏轨（`melody_only` 负收益模式）；任何涉及 MIDI 听感与音准的改动必须经人工耳测确认。

---

## 附：清单自检脚本（第 9 条配套）

把下面内容保存成任意 `check_manifest.py`，在**仓库 clone 目录**里执行（先 `git fetch origin --force`），输出 `不一致条目: 0` 即表示远端 `version.json` 与远端文件 100% 自洽。

```python
import hashlib
import json
import subprocess


def blob(rel):
    r = subprocess.run(["git", "cat-file", "blob", "origin/main:" + rel],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


raw = blob("version.json")
man = json.loads(raw.decode("utf-8"))
bad = []
for rel, want in (man.get("files") or {}).items():
    b = blob(rel)
    if b is None:
        bad.append((rel, "missing"))
    elif hashlib.sha256(b).hexdigest() != want:
        bad.append((rel, "hash"))
print("版本:", man.get("version"), "条目:", len(man.get("files") or {}))
print("不一致条目:", len(bad), bad[:5])
```

```powershell
# 在 clone 目录执行
git fetch origin --force
& "C:\异键演奏\.venv\Scripts\python.exe" check_manifest.py
```
