# -*- coding: utf-8 -*-
"""guide_dialog.py -- 《异键演奏》v1.4 新手引导与使用指南弹窗（星曜暗夜 / 学院星昼双主题）。

包含 5 个核心模块图文指南，支持首次启动柔和引导（可勾选不再自动弹出）与主界面侧栏随时唤出。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT, ROOT / "player", ROOT / "transcriber"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

for _cand_plug in (
    ROOT / ".venv" / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins",
    Path(sys.prefix) / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins",
):
    if _cand_plug.is_dir() and not os.environ.get("QT_PLUGIN_PATH"):
        os.environ["QT_PLUGIN_PATH"] = str(_cand_plug)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_cand_plug / "platforms"))
        break

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog,  # noqa: E402
                             QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

try:
    import core_utils as CU  # noqa: E402
except Exception:
    CU = None

try:
    import ui_widgets as UW  # noqa: E402
except Exception:
    UW = None

try:
    import player_config as PC  # noqa: E402
except Exception:
    PC = None


def _make_icon(name: str, color: str = "#E6EDF7", size: int = 16):
    if UW is not None and hasattr(UW, "make_vector_icon"):
        try:
            return UW.make_vector_icon(name, color=color, size=size)
        except Exception:
            pass
    from PyQt5.QtGui import QIcon
    return QIcon()


def _make_card(object_name: str = "card") -> QFrame:
    if UW is not None and hasattr(UW, "GlassCard"):
        try:
            return UW.GlassCard(object_name=object_name)
        except Exception:
            pass
    frame = QFrame()
    frame.setObjectName(object_name or "card")
    return frame


def _is_dark_theme(theme_mode: str | None = None, widget: QWidget | None = None) -> bool:
    if theme_mode in ("dark", "light"):
        return theme_mode == "dark"
    try:
        pal = widget.palette() if widget is not None else QApplication.palette()
        return pal.window().color().lightness() < 128
    except Exception:
        return True


def _section_card(title: str, tag: str, bullets: list[str], tag_obj: str = "accentPill") -> QFrame:
    card = _make_card("card")
    v = QVBoxLayout(card)
    v.setContentsMargins(14, 11, 14, 11)
    v.setSpacing(6)

    head = QHBoxLayout()
    head.setSpacing(8)
    lbl_t = QLabel(title)
    lbl_t.setObjectName("sectionTitle")
    head.addWidget(lbl_t, 1)
    if tag:
        pill = QLabel(tag)
        pill.setObjectName(tag_obj or "accentPill")
        pill.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        head.addWidget(pill, 0, Qt.AlignVCenter)
    v.addLayout(head)

    for item in bullets:
        lbl = QLabel(item)
        lbl.setWordWrap(True)
        v.addWidget(lbl)
    return card


def _wrap_scroll_page(cards: list[QWidget]) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("stageScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.viewport().setAutoFillBackground(False)

    host = QWidget()
    host.setObjectName("stagePage")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(4, 2, 8, 6)
    lay.setSpacing(10)
    for c in cards:
        lay.addWidget(c)
    lay.addStretch(1)
    area.setWidget(host)
    return area


_CHAPTERS = [
    ("30 秒极速上手", "spark", "从启动到游戏内响起第一段旋律"),
    ("三种演奏模式", "guitar", "吉他轮盘 / 吉他 36 键 / 钢琴 36 键"),
    ("MIDI 键盘直弹", "keyboard", "即插即用热拔插 · 25~88 键适配"),
    ("AI 扒谱与工坊", "ai", "音频转 MIDI · 搜谱 · 卷帘微调"),
    ("帧率与排错速查", "shield", "帧率选择 · 热更新 · 常见问题"),
]


class GuideDialog(QDialog):
    """星曜暗夜 / 学院星昼双主题软件使用指南窗口。"""

    def __init__(self, theme_mode: str = "dark", first_run: bool = False,
                 parent=None, embedded: bool = False, on_back=None):
        super().__init__(parent)
        self._embedded = bool(embedded)
        self._on_back = on_back
        self._theme_mode = "light" if str(theme_mode).lower() == "light" else "dark"
        self._first_run = bool(first_run)
        self.setWindowTitle("《异键演奏》v1.4 新手引导与使用指南")
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setMinimumSize(0, 0)
        else:
            self.setModal(False)
            if CU is not None and hasattr(CU, "fit_screen"):
                CU.fit_screen(self, 900, 620, min_w=720, min_h=500)
            else:
                self.resize(900, 620)
                self.setMinimumSize(720, 500)

        self._build_ui()
        self.select_page(0)
        self.sync_theme(self._theme_mode)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ── 顶部标题卡片 ───────────────────────────────────────────────
        top_card = _make_card("card")
        tv = QHBoxLayout(top_card)
        tv.setContentsMargins(14, 10, 14, 10)
        tv.setSpacing(12)

        left_box = QVBoxLayout()
        left_box.setSpacing(3)
        lbl_title = QLabel("欢迎使用《异键演奏》 · 使用指南与功能速览")
        lbl_title.setObjectName("appTitle")
        left_box.addWidget(lbl_title)
        lbl_sub = QLabel(
            "覆盖自动演奏、AI 智能扒谱、乐曲库搜谱、钢琴卷帘微调与外接 MIDI 键盘实时直弹。"
        )
        lbl_sub.setObjectName("sub")
        lbl_sub.setWordWrap(True)
        left_box.addWidget(lbl_sub)
        tv.addLayout(left_box, 1)

        ver_badge = QLabel("v1.4 ♪")
        ver_badge.setObjectName("goldPill")
        ver_badge.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        tv.addWidget(ver_badge, 0, Qt.AlignVCenter)
        root.addWidget(top_card)

        # ── 主体：左侧 5 章导航 + 右侧内容堆栈 ─────────────────────────
        body = QHBoxLayout()
        body.setSpacing(10)

        nav_card = _make_card("card")
        nav_card.setFixedWidth(186)
        nv = QVBoxLayout(nav_card)
        nv.setContentsMargins(10, 10, 10, 10)
        nv.setSpacing(6)

        nav_head = QLabel("目录章节")
        nav_head.setObjectName("fieldname")
        nv.addWidget(nav_head)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_btns: list[tuple[QPushButton, str]] = []
        for idx, (label, icon_name, tip) in enumerate(_CHAPTERS):
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda _=False, i=idx: self.select_page(i))
            self._nav_group.addButton(btn)
            self._nav_btns.append((btn, icon_name))
            nv.addWidget(btn)
        nv.addStretch(1)

        tip_box = QLabel("随时可从主窗口\n左下角「使用指南」\n再次打开本教程。")
        tip_box.setObjectName("sub")
        tip_box.setWordWrap(True)
        nv.addWidget(tip_box)
        body.addWidget(nav_card)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_page_quickstart())
        self.stack.addWidget(self._build_page_modes())
        self.stack.addWidget(self._build_page_midi_live())
        self.stack.addWidget(self._build_page_ai_studio())
        self.stack.addWidget(self._build_page_faq())
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # ── 底部操作栏 ─────────────────────────────────────────────────
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.chk_no_auto = QCheckBox("启动时不再自动弹出本指南")
        self.chk_no_auto.setChecked(True)
        self.chk_no_auto.setVisible(self._first_run)
        foot.addWidget(self.chk_no_auto)
        foot.addStretch(1)

        self.btn_prev = QPushButton("上一章")
        self.btn_prev.setObjectName("ghost")
        self.btn_prev.clicked.connect(lambda: self.select_page(max(0, self.stack.currentIndex() - 1)))
        foot.addWidget(self.btn_prev)

        self.btn_next = QPushButton("下一章")
        self.btn_next.setObjectName("ghost")
        self.btn_next.clicked.connect(
            lambda: self.select_page(min(self.stack.count() - 1, self.stack.currentIndex() + 1))
        )
        foot.addWidget(self.btn_next)

        self.btn_close = QPushButton("进入演奏台" if self._embedded else "我知道了 / 关闭")
        self.btn_close.setObjectName("playBtn")
        self.btn_close.setMinimumWidth(140)
        self.btn_close.clicked.connect(self._on_close_clicked)
        foot.addWidget(self.btn_close)
        root.addLayout(foot)

    def _build_page_quickstart(self) -> QScrollArea:
        c_admin = _section_card(
            "最重要的一件事：必须以管理员权限运行",
            "核心前提",
            [
                "● 游戏在交互桌面接收底层硬件按键注入（SendInput）；若游戏是以管理员启动的，未提权的演奏工具按键会被系统拦截。",
                "● 正确启动方式：直接双击根目录的「双击启动-异键演奏.bat」，脚本会自动申请管理员权限并拉起主界面。",
                "● 自检提示：主窗左下角状态胶囊会实时显示「游戏: 已连接」与「管理: 有」。",
            ],
            tag_obj="goldPill",
        )
        c_steps = _section_card(
            "四步完成首次演奏",
            "30 秒上手",
            [
                "1) 进入游戏并切换到乐器演奏界面（吉他轮盘 / 吉他 36 键 / 钢琴 36 键之一）。",
                "2) 在《异键演奏》演奏台左侧「本地 MIDI 曲库」选中曲目（或右上切换「乐曲库工坊 / AI 扒谱」找歌）。",
                "3) 顶部切换与游戏当前一致的乐器模式；若用【吉他 · 轮盘】首次使用请先在编配区点「轮盘位置校准」对齐 6 个扇区。",
                "4) 切回游戏窗口按 F8 开始/暂停演奏；任何时候按 F12 立即急停并松开全部按键（MIDI 直弹也同样适用）。",
            ],
            tag_obj="accentPill",
        )
        c_safety = _section_card(
            "后台安全守卫机制",
            "防卡键保护",
            [
                "● 切出保护：演奏或直弹过程中切换到浏览器/聊天窗口，或按下 Alt / Win 键，工具会在 40ms 内自动松开所有按住键。",
                "● 自动恢复：自动演奏切出后会自动暂停，切回游戏窗口按 F8 即可从断点继续。",
            ],
            tag_obj="violetPill",
        )
        return _wrap_scroll_page([c_admin, c_steps, c_safety])

    def _build_page_modes(self) -> QScrollArea:
        c_wheel = _section_card(
            "1. 吉他 · 轮盘模式（默认 / 1P 独奏与 2P 合奏）",
            "E2 ~ E6 (40~88)",
            [
                "● 对应游戏默认 6 弦 + 24 品轮盘（4 页×6 扇区 + 第 5 页 0 品空弦），支持按住延音、柱式和弦抓弦与右键扫弦。",
                "● 支持「1P 单人独奏（主旋律+智能琶音合流）」以及双人合奏的「1P 主音 / 2P 伴奏（琶音或扫弦）」，可开启「整 5 秒同步」实现两台电脑毫秒级合奏。",
                "● 若更换游戏分辨率或移动窗口位置，只需点一次「轮盘位置校准」拖圆心滚轮对齐即可。",
            ],
            tag_obj="accentPill",
        )
        c_g36 = _section_card(
            "2. 吉他 · 钢琴键模式（36 键直按）",
            "E3 ~ D#6 (52~87)",
            [
                "● 对应游戏内吉他 36 键布局：3 行 × 12 列完整半音阶（低音 A..Enter、中音 Q..]、高音 1..=），无修饰键冲突，支持任意多音同时按下与长按延音。",
                "● 编配区既支持「原谱直出（全部/旋律/伴奏）」，也支持移植自轮盘的「智能编配（1P 独奏 / 1P 主音 / 2P 柱式律动 / 2P 错峰扫弦 / 2P 琶音根音）」，还可一键「智能移调」或内联展开 36 键位图。",
            ],
            tag_obj="goldPill",
        )
        c_p36 = _section_card(
            "3. 钢琴 · 36 键模式（Shift # / Ctrl b 瞬时移位）",
            "C4 ~ B6 (60~95)",
            [
                "● 左下角 Z 为 C4，三行 7 个自然音基键（Z..M / A..J / Q..U）。",
                "● 按住 LShift 仅将 QRT / AFG / ZVB 三列右移半音到 #1/#4/#5；按住 LCtrl 仅将 EU / DJ / CM 两列左移半音到 b3/b7；其余列自然音不受影响。",
                "● 钢琴模式无延音（每次触发按固定按住时长释放）；系统保证 LShift 与 LCtrl 严格互斥，同拍混合黑白键自动无感拆批防串音。",
                "● 同样支持「原谱直出 / 柱式 / 琶音 / 错峰滚奏 / 角色拆分」；点击「36 键位图」即可在当前页面下方直接展开交互布局图，无需弹窗。",
            ],
            tag_obj="violetPill",
        )
        return _wrap_scroll_page([c_wheel, c_g36, c_p36])

    def _build_page_midi_live(self) -> QScrollArea:
        c_plug = _section_card(
            "即插即用 · 原生热拔插自动连接",
            "零配置直弹",
            [
                "● 插上 USB MIDI 键盘（或开启虚拟 MIDI 端口）后点左侧「MIDI 键盘直弹」打开直弹窗口，软件会自动扫描并连接本机设备，支持拔插自动断线释放与重插恢复。",
                "● 下方可视化琴键条支持 25 / 37 / 49 / 61 / 76 / 88 键全规格切换，鼠标直接点击或拖动琴键也能试弹映射。",
                "● 窗口顶部实时显示游戏桥接状态（如发现游戏管理员权限而自身未提权，会直接给出红色提示）。",
            ],
            tag_obj="accentPill",
        )
        c_play = _section_card(
            "三模式直弹与关键实用设置",
            "手速响应",
            [
                "● 吉他 36 键：跳过聚合等待 0ms 直通触发，支持按住延音与外接 CC64 延音踏板。",
                "● 钢琴 36 键：采用非阻塞异步释放状态机，连续快弹琶音互不卡顿；若游戏低帧率场景偶发黑键漏变音，展开高级参数调高「修饰提前（5~8ms）」即可。",
                "● 吉他轮盘：支持「智能品格直弹（自动找最近品位翻页）」与「和弦轮盘速弹（支持单键直触 24 和弦或按多音实时匹配）」。",
            ],
            tag_obj="goldPill",
        )
        c_adv = _section_card(
            "「高级参数」说明（大白话版）",
            "按需展开",
            [
                "● MIDI 通道：一根 MIDI 线可分 Ch1~Ch16 逻辑轨。普通单键盘固定发 Ch1，默认「全部通道」不用改；分键双音色电钢或多轨宿主输出时才需要指定通道。",
                "● 忽略 Ch10 鼓轨：行业默认第 10 通道专传架子鼓点，勾选可防止伴奏鼓点误触发琴键。",
                "● 合批窗（ms）：把几毫秒内先后落指的和弦微聚合成同一批和弦分析（吉他 36 键自动跳过该等待）。",
            ],
            tag_obj="violetPill",
        )
        return _wrap_scroll_page([c_plug, c_play, c_adv])

    def _build_page_ai_studio(self) -> QScrollArea:
        c_ai = _section_card(
            "AI 智能扒谱：把任意歌曲转成可演奏 MIDI",
            "三步全流程",
            [
                "● 首次使用前请先点左下「环境与显卡体检」→「一键安装 / 修复环境」，自动配置推理环境与模型权重（绿色基础包约 296MB 不含模型）。",
                "● 支持导入 MP3/WAV/FLAC 等音频，选好「人声主旋律 / 器乐旋律 / 全轨合流」与目标乐器预设音域，点「一键全流程」即可自动分离人声伴奏、转录并送入演奏台。",
                "● 支持 Intel Arc (XPU) / NVIDIA (CUDA) 显卡加速及无独显 CPU 自动回退。",
            ],
            tag_obj="accentPill",
        )
        c_studio = _section_card(
            "乐曲库工坊：在线搜谱与分轨清洗",
            "多轨编排",
            [
                "● 在线检索吉他谱并按「人声 / 主音 / 节奏 / 贝斯 / 键盘」分别导出，系统内置三层打击乐过滤与三模式同帧清洗算法。",
                "● 支持「人声对齐器」把 AI 扒出的人声与原曲多轨叠在同一时间轴对齐微调后覆盖保存。",
            ],
            tag_obj="goldPill",
        )
        c_roll = _section_card(
            "钢琴卷帘可视化微调",
            "所见即所得",
            [
                "● 在演奏台点「钢琴卷帘微调」可拖拽移动音符、增删错音、切换和弦与实时内置波形试听。",
                "● 红色高亮拦截：轮盘同帧异品格冲突或 36 键超音域音符会直接标红提示，彻底消除红音后才允许保存应用。",
            ],
            tag_obj="violetPill",
        )
        return _wrap_scroll_page([c_ai, c_studio, c_roll])

    def _build_page_faq(self) -> QScrollArea:
        c_fps = _section_card(
            "演奏帧率与「跟随本曲参数」怎么选",
            "FPS 对照",
            [
                "● 30 帧（默认·最稳）：适合游戏锁定 30~60 帧、BPM ≤ 120 的抒情曲，兼容性最好。",
                "● 60 帧（均衡）：适合 BPM 120~170 的流行/动漫快歌，快速切换更跟手。",
                "● 90 帧（高刷）：适合高配电脑与高频极速曲目，配合低延迟窗口可做到近 0 晚出。",
                "● 勾选「跟随本曲」：系统会自动在后台反推刚好让本曲零迟到的最稳帧时长（无需盲猜）。",
            ],
            tag_obj="accentPill",
        )
        c_qa = _section_card(
            "高频排错与热更新",
            "速查 5 条",
            [
                "1. 打开后没反应/游戏没声音：确认用管理员启动本软件、游戏界面正处于乐器演奏画面、且右上语言/输入法非中文拦截状态。",
                "2. 吉他 36 键高音区 1~0 没声音：高音数字键走虚拟键码通路，若偶发丢失请检查是否调高过高帧率或未提权。",
                "3. 钢琴模式偶尔黑键变回白键：调高直弹窗「高级参数 → 修饰提前」到 5.0~8.0ms，并确认游戏帧率稳定。",
                "4. 自动更新与回退：左下「检查更新」支持多镜像自动校验更新，若升级后想回滚可直接点击「回退上一版本」。",
                "5. 紧急停音：游戏卡住任何音时直接按 F12，自动演奏与 MIDI 键盘直弹会同步全松键并清零。",
            ],
            tag_obj="goldPill",
        )
        return _wrap_scroll_page([c_fps, c_qa])

    def select_page(self, index: int) -> None:
        idx = max(0, min(self.stack.count() - 1, int(index)))
        self.stack.setCurrentIndex(idx)
        for i, (btn, _icon) in enumerate(self._nav_btns):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setEnabled(idx < self.stack.count() - 1)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        dark = _is_dark_theme(self._theme_mode, self)
        dim = "#CBD5E1" if dark else "#334155"
        for btn, icon_name in self._nav_btns:
            fg = "#FFFFFF" if (dark and btn.isChecked()) else ("#1D4ED8" if btn.isChecked() else dim)
            btn.setIcon(_make_icon(icon_name, fg, 16))
        self.btn_close.setIcon(_make_icon("check", "#FFFFFF", 16))

    def sync_theme(self, mode: str) -> None:
        self._theme_mode = "light" if str(mode).lower() == "light" else "dark"
        self._refresh_icons()
        self.update()

    def _persist_dont_show(self) -> None:
        if not self._first_run or not self.chk_no_auto.isChecked():
            return
        if PC is not None and hasattr(PC, "save"):
            try:
                PC.save({"guide_shown": True})
            except Exception:
                pass

    def _on_close_clicked(self) -> None:
        self._persist_dont_show()
        if self._embedded:
            if callable(self._on_back):
                self._on_back()
            return
        self.close()

    def reject(self) -> None:
        if self._embedded:
            self._on_close_clicked()
            return
        super().reject()

    def keyPressEvent(self, event) -> None:
        if self._embedded and event.key() == Qt.Key_Escape:
            self._on_close_clicked()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._persist_dont_show()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import gui_theme as GT
        GT.apply_theme(app, "dark")
    except Exception:
        pass
    dlg = GuideDialog(theme_mode="dark", first_run=True)
    if "--smoke" in sys.argv:
        dlg.show()
        for i in range(dlg.stack.count()):
            dlg.select_page(i)
            app.processEvents()
        dlg.close()
        print("GUIDE_DIALOG_SMOKE_OK")
    else:
        dlg.exec_()
