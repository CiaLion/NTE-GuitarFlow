# -*- coding: utf-8 -*-
"""gui_main.py -- 《异键演奏》一体化主界面。

顶部全局状态栏 + 两个大标签页：
  【AI 智能扒谱】复用 transcriber/gui_a_legacy.py 的完整三步流水线；
  【吉他自动演奏】复用 player/gui_b_legacy.py 的选曲/角色/播放/校准/自检。

另含 WheelOverlayDialog：半透明置顶指示圈，鼠标拖圆心、滚轮调半径，
确认后把屏幕坐标换算成客户区比例写入 player_config.json。
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT, ROOT / "player", ROOT / "transcriber"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    import core_utils as CU  # noqa: E402
except Exception:
    CU = None


def heal_portable_venvs(root_dir=ROOT):
    """把两处 pyvenv.cfg 的 home/executable 指回当前 runtime，随目录移动自动修复。"""
    if CU is not None and hasattr(CU, "heal_portable_venvs"):
        return CU.heal_portable_venvs(root_dir)
    root = Path(root_dir)
    runtime = root / "runtime" / "python311"
    exe = runtime / "python.exe"
    if not exe.is_file():
        return []
    want = os.path.normcase(os.path.abspath(str(runtime)))
    fixed = []
    for cfg in (root / ".venv" / "pyvenv.cfg",
                root / "vendor" / "sheetsage2" / ".venv" / "pyvenv.cfg"):
        if not cfg.is_file():
            continue
        try:
            lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
            changed = False
            for i, line in enumerate(lines):
                key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
                if key in ("home", "executable"):
                    cur = line.split("=", 1)[1].strip()
                    if os.path.normcase(os.path.abspath(cur)) != (
                            want if key == "home" else os.path.normcase(os.path.abspath(str(exe)))):
                        lines[i] = "%s = %s" % (key, runtime if key == "home" else exe)
                        changed = True
            if changed:
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                fixed.append(str(cfg))
        except Exception:
            pass
    return fixed

_QT_PLUGINS = Path(sys.prefix) / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins"
if _QT_PLUGINS.is_dir() and not os.environ.get("QT_PLUGIN_PATH"):
    os.environ["QT_PLUGIN_PATH"] = str(_QT_PLUGINS)

from PyQt5.QtCore import (QPoint, QProcess, QRect, QSize, Qt, QThread, QTimer,
                          pyqtSignal)  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QTextCursor  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame,
                             QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton,
                             QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout,
                             QWidget)

import gui_a_legacy as AGui  # noqa: E402
import gui_b_legacy as BGui  # noqa: E402
import game_io as GIO  # noqa: E402
import player_config as PC  # noqa: E402
import midi_compiler as MC  # noqa: E402
import gui_theme as GT  # noqa: E402
import ui_widgets as UW  # noqa: E402
import updater  # noqa: E402

SECTOR_ANGLES = GIO.SECTOR_ANGLES


def fit_screen(widget, width, height, min_w=560, min_h=420):
    """把窗口尺寸夹到当前屏幕可用区域，小屏/高缩放下不越界。"""
    if CU is not None and hasattr(CU, "fit_screen"):
        CU.fit_screen(widget, width, height, min_w=min_w, min_h=min_h)
    else:
        widget.resize(max(int(min_w), int(width)), max(int(min_h), int(height)))
    try:
        avail = QApplication.desktop().availableGeometry(widget)
        lo_w = min(int(min_w), int(avail.width() * 0.72))
        lo_h = min(int(min_h), int(avail.height() * 0.68))
    except Exception:
        lo_w, lo_h = int(min_w), int(min_h)
    widget.setMinimumSize(lo_w, lo_h)
    return widget

def apply_theme(app, mode=None):
    """星曜暗夜 / 星辉浅昼双主题（gui_theme 统一 QSS）。"""
    return GT.apply_theme(app, mode=mode)


class WheelOverlayDialog(QDialog):
    """半透明置顶指示圈：拖圆心、滚轮调半径，确认后写比例。"""

    @staticmethod
    def _safe_ratio(val, default: float, lo: float, hi: float) -> float:
        import math
        try:
            v = float(val)
            if not math.isfinite(v):
                return default
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        try:
            io = GIO.GameIO()
            m = io.metrics()
        except Exception:
            m = None
        if m:
            self._client = tuple(m)
        else:
            self._client = (0, 0, 1920, 1080)
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                None, "游戏未启动",
                "未检测到游戏窗口，本次校准将按 1920x1080 推算比例。\n"
                "如果你实际屏幕不是 1920x1080，比例会偏，请先启动游戏再校准。"
            )
        geo = QApplication.desktop().geometry()
        self.setGeometry(geo)
        left, top, cw, ch = self._client
        cw = max(1.0, float(cw))
        ch = max(1.0, float(ch))
        cfg = PC.load()
        rx = self._safe_ratio(cfg.get("wheel_x_ratio"), 0.6937, 0.05, 0.95)
        ry = self._safe_ratio(cfg.get("wheel_y_ratio"), 0.4378, 0.05, 0.95)
        rr = self._safe_ratio(cfg.get("wheel_radius_ratio"), 0.2957, 0.05, 0.48)
        self.cx = left + cw * rx
        self.cy = top + ch * ry
        self.radius = max(40.0, ch * rr)
        self._drag = None
        self._bar = QWidget(self)
        self._bar.setStyleSheet(
            "background: rgba(20,24,32,235); border-radius: 8px;")
        lay = QHBoxLayout(self._bar)
        lay.setContentsMargins(8, 4, 8, 4)
        tip = QLabel("左键拖动圆心对准游戏轮盘 | 滚轮或 [-][+] 调整大小")
        tip.setStyleSheet("color:#eaf2ff; padding:4px; font-size:13px;"
                          "background: transparent;")
        lay.addWidget(tip, 1)
        btn_css = ("QPushButton{background:#2f81f7;color:#ffffff;border:none;"
                   "border-radius:6px;padding:6px 12px;font-size:13px;"
                   "font-weight:bold;} QPushButton:hover{background:#4b96ff;}")
        self.btn_minus = QPushButton("[-]")
        self.btn_plus = QPushButton("[+]")
        self.btn_ok = QPushButton("确认锁定")
        self.btn_cancel = QPushButton("取消")
        for b in (self.btn_minus, self.btn_plus, self.btn_ok, self.btn_cancel):
            b.setStyleSheet(btn_css)
            b.setMinimumHeight(30)
            lay.addWidget(b)
        self.btn_minus.clicked.connect(lambda: self._scale(0.95))
        self.btn_plus.clicked.connect(lambda: self._scale(1.05))
        self.btn_ok.clicked.connect(self.confirm)
        self.btn_cancel.clicked.connect(self.reject)
        self._bar.setGeometry(14, 14, min(self.width() - 28, 860), 48)
        self.raise_()

    def _scale(self, k):
        self.radius = max(40.0, min(self.height() * 0.5, self.radius * k))
        self.update()

    def set_circle(self, cx, cy, radius):
        import math
        try:
            cx_f, cy_f, r_f = float(cx), float(cy), float(radius)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(cx_f) and math.isfinite(cy_f) and math.isfinite(r_f)):
            return
        self.cx, self.cy = cx_f, cy_f
        self.radius = max(20.0, r_f)
        self.update()

    def compute_ratios(self, client=None):
        left, top, cw, ch = tuple(client or self._client)
        cw = max(1.0, float(cw))
        ch = max(1.0, float(ch))
        return {"wheel_x_ratio": (self.cx - left) / cw,
                "wheel_y_ratio": (self.cy - top) / ch,
                "wheel_radius_ratio": max(20.0, self.radius) / ch}

    def save_ratios(self, client=None):
        ratios = self.compute_ratios(client)
        PC.save(ratios)
        return ratios

    def confirm(self):
        m = GIO.GameIO().metrics()
        ratios = self.save_ratios(m if m else None)
        self.accept()
        return ratios

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.confirm()
        elif ev.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            lcx = self.cx - self.x()
            lcy = self.cy - self.y()
            dx = ev.pos().x() - lcx
            dy = ev.pos().y() - lcy
            if dx * dx + dy * dy <= self.radius * self.radius:
                self._drag = (dx, dy)          # 记录“按下点相对圆心的偏移”（窗口内坐标）
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.cx = self.x() + ev.pos().x() - self._drag[0]
            self.cy = self.y() + ev.pos().y() - self._drag[1]
            self.update()
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def wheelEvent(self, ev):
        self._scale(1.04 if ev.angleDelta().y() > 0 else 1 / 1.04)

    def paintEvent(self, ev):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.translate(-self.x(), -self.y())
        c = QColor(0, 230, 255, 55)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(int(self.cx), int(self.cy)), int(self.radius), int(self.radius))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 230, 255), 3))
        p.drawEllipse(QPoint(int(self.cx), int(self.cy)), int(self.radius), int(self.radius))
        p.drawLine(int(self.cx - 15), int(self.cy), int(self.cx + 15), int(self.cy))
        p.drawLine(int(self.cx), int(self.cy - 15), int(self.cx), int(self.cy + 15))
        pen = QPen(QColor(255, 210, 80), 2, Qt.DashLine)
        p.setPen(pen)
        for ang in range(0, 360, 60):
            t = math.radians(ang)
            p.drawLine(QPoint(int(self.cx), int(self.cy)),
                       QPoint(int(self.cx + self.radius * math.cos(t)),
                              int(self.cy + self.radius * math.sin(t))))
        p.setPen(QPen(QColor(255, 90, 140), 2))
        for i, ang in enumerate(SECTOR_ANGLES):
            t = math.radians(ang)
            tx = self.cx + self.radius * GIO.SECTOR_CLICK_FRACTION * math.cos(t)
            ty = self.cy + self.radius * GIO.SECTOR_CLICK_FRACTION * math.sin(t)
            p.drawEllipse(QPoint(int(tx), int(ty)), 10, 10)
            p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
            p.drawText(QRect(int(tx - 12), int(ty - 30), 24, 24), Qt.AlignCenter, str(i + 1))
        # 安全停靠点
        px = self.x() + self.width() * GIO.STRUM_PARK[0]
        py = self.y() + self.height() * GIO.STRUM_PARK[1]
        p.setPen(QPen(QColor(180, 255, 120), 2))
        p.drawEllipse(QPoint(int(px), int(py)), 9, 9)
        p.end()


class EnvCheckWorker(QThread):
    """后台跑 check_env.run_env_diagnostics()，避免体检时界面卡住。"""

    done = pyqtSignal(object, str)

    def run(self):
        try:
            import check_env
            info = check_env.run_env_diagnostics()
            self.done.emit(info, check_env.format_report(info))
        except Exception as exc:  # noqa: BLE001
            self.done.emit({}, "体检失败：%s: %s" % (type(exc).__name__, exc))


class EnvCheckDialog(QDialog):
    """环境体检 + 一键安装 / 修复（GUI 内直接调用 setup/bootstrap.ps1）。

    安装走 QProcess（powershell -File setup\\bootstrap.ps1 -Force），
    实时把日志打到窗口里，并通过进度条 + 磁盘文件字节监控实时展示进度；装完自动重新体检。
    """

    TOTAL_WEIGHT_BYTES = 228738564 + 2529812848  # SheetSage2 + MERT-v2-FullSong (~2.57 GB)

    def __init__(self, parent=None, embedded=False, on_back=None):
        super(EnvCheckDialog, self).__init__(parent)
        self._embedded = bool(embedded)
        self._on_back = on_back
        self.setWindowTitle("环境与显卡体检 / 一键安装")
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setMinimumSize(0, 0)
        else:
            fit_screen(self, 820, 680, 560, 420)
        self._worker = None
        self._proc = None
        self._mode = "idle"  # "idle" | "check" | "install"
        self._t0 = 0.0
        self._stage_pct = 0
        self._stage_label = "准备就绪"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QLabel("环境与显卡体检 / 一键安装")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        tip = QLabel("先看下面的体检结果；如果有模型缺失、推理 Python 不存在、"
                     "或显卡显示 CPU，点「一键安装 / 修复环境」。"
                     "安装会联网下载约 8GB，可能需要 5~15 分钟，期间不要关闭程序。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.install_bar = QProgressBar()
        self.install_bar.setObjectName("stageBar")
        self.install_bar.setRange(0, 100)
        self.install_bar.setValue(0)
        self.install_bar.setTextVisible(True)
        self.install_bar.setFormat("准备体检…")
        lay.addWidget(self.install_bar)

        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(360)
        lay.addWidget(self.log, 1)

        self.status = QLabel("准备体检…")
        self.status.setObjectName("sub")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_check = QPushButton("重新体检")
        self.btn_check.setObjectName("ghost")
        self.btn_check.clicked.connect(self.start_check)
        row.addWidget(self.btn_check)
        self.btn_install = QPushButton("一键安装 / 修复环境")
        self.btn_install.setObjectName("primary")
        self.btn_install.clicked.connect(self.start_install)
        row.addWidget(self.btn_install)
        row.addStretch(1)
        self.btn_close = QPushButton("← 返回演奏台" if self._embedded else "关闭")
        self.btn_close.clicked.connect(self._handle_close_or_back)
        row.addWidget(self.btn_close)
        lay.addLayout(row)

        self._prog_timer = QTimer(self)
        self._prog_timer.setInterval(400)
        self._prog_timer.timeout.connect(self._tick_progress)

    # ── 进度监控辅助 ──────────────────────────────────────────────────
    def _current_weight_bytes(self) -> int:
        models_dir = ROOT / "vendor" / "sheetsage2" / "models"
        total = 0
        for sub, expected in (("SheetSage2", 228738564), ("MERT-v2-FullSong", 2529812848)):
            d = models_dir / sub
            w = d / "model.safetensors"
            part = d / "model.safetensors.part"
            b = 0
            try:
                if w.is_file() and w.stat().st_size > 0:
                    b = w.stat().st_size
                elif part.is_file() and part.stat().st_size > 0:
                    b = part.stat().st_size
            except Exception:
                b = 0
            total += min(b, expected)
        return total

    def _update_bar(self, pct: int, text: str):
        pct = max(0, min(100, int(pct)))
        self.install_bar.setValue(pct)
        self.install_bar.setFormat(text)

    def _parse_install_log_stage(self, chunk: str):
        for line in (chunk or "").splitlines():
            s = line.strip()
            if not s:
                continue
            if "[heal]" in s or "Locating base Python" in s:
                self._stage_pct = max(self._stage_pct, 5)
                self._stage_label = "正在检查并修复 Python 运行时路径"
            elif "Main environment (.venv)" in s:
                self._stage_pct = max(self._stage_pct, 12)
                self._stage_label = "正在检查/配置主程序环境 (.venv)"
            elif "Main environment already verified" in s or "main program dependencies verified" in s:
                self._stage_pct = max(self._stage_pct, 20)
                self._stage_label = "主程序环境已就绪，准备检查 AI 推理环境"
            elif "SheetSage2 environment" in s or "installing torch" in s:
                self._stage_pct = max(self._stage_pct, 24)
                self._stage_label = "正在配置显卡 AI 推理核心 (PyTorch)"
            elif "downloading from " in s:
                self._stage_pct = max(self._stage_pct, 28)
                self._stage_label = "正在下载显卡 PyTorch 加速组件"
            elif "installing SheetSage2 from" in s:
                self._stage_pct = max(self._stage_pct, 38)
                self._stage_label = "正在验证/安装 SheetSage2 依赖库"
            elif "SheetSage2 environment already verified" in s or "SheetSage2 dependencies verified" in s:
                self._stage_pct = max(self._stage_pct, 45)
                self._stage_label = "Python 环境已就绪，准备核验 AI 模型权重"
            elif "Downloading model weights" in s or "Fetching m-a-p/SheetSage2" in s:
                self._stage_pct = max(self._stage_pct, 48)
                self._stage_label = "正在下载 SheetSage2 模型权重 (0.21 GB)"
            elif "Fetching m-a-p/MERT-v2-FullSong" in s:
                self._stage_pct = max(self._stage_pct, 56)
                self._stage_label = "正在下载 MERT-v2 音乐底座权重 (2.36 GB)"
            elif "restored " in s and "patched model code" in s:
                self._stage_pct = max(self._stage_pct, 98)
                self._stage_label = "正在同步显卡加速补丁与校验哈希"

    def _tick_progress(self):
        elapsed = max(0.0, time.time() - self._t0)
        if self._mode == "check":
            # 体检通常耗时 2~6 秒，平滑推进至 90%
            pct = min(90, int(15 + 75 * (1.0 - (2.71828 ** (-elapsed / 2.5)))))
            self._update_bar(pct, "正在探测系统、显卡与模型状态… (%d%%) · 已用时 %.1fs" % (pct, elapsed))
            return
        if self._mode == "install":
            if self._stage_pct >= 45:
                cur_b = self._current_weight_bytes()
                ratio = min(1.0, float(cur_b) / float(self.TOTAL_WEIGHT_BYTES))
                pct = max(self._stage_pct, 45 + int(52 * ratio))
                gb_cur = cur_b / (1024.0 ** 3)
                gb_tot = self.TOTAL_WEIGHT_BYTES / (1024.0 ** 3)
                self._update_bar(
                    pct,
                    "%s：%.2f / %.2f GB (%d%%) · 已用时 %ds"
                    % (self._stage_label, gb_cur, gb_tot, pct, int(elapsed))
                )
            else:
                # 环境与 PyTorch 阶段（0~44%），随耗时微幅渐进防假死感
                creep = min(44, self._stage_pct + int(elapsed / 8.0))
                self._update_bar(
                    creep,
                    "%s (%d%%) · 已用时 %ds" % (self._stage_label, creep, int(elapsed))
                )

    # ── 体检 ──────────────────────────────────────────────────────────
    def _append(self, text):
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())

    def _set_status(self, text):
        self.status.setText(text)

    def start_check(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            return
        self.btn_install.setEnabled(False)
        self._mode = "check"
        self._t0 = time.time()
        self._update_bar(10, "正在探测系统、显卡与模型状态… (10%)")
        self._prog_timer.start()
        self._set_status("正在体检…（探测显卡/模型可能要几秒）")
        self._append("\n=== 环境体检 ===\n")
        self._worker = EnvCheckWorker(self)
        self._worker.done.connect(self._on_check_done)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.start()

    def _on_check_done(self, info, text):
        self._prog_timer.stop()
        self._mode = "idle"
        elapsed = max(0.1, time.time() - self._t0)
        self._update_bar(100, "✔ 环境与显卡体检完成 (100%%) · 耗时 %.1fs" % elapsed)
        self._append(text)
        self._set_status("体检完成。如果上面有缺失项，点「一键安装 / 修复环境」。")

    def _on_check_finished(self):
        self._prog_timer.stop()
        self.btn_install.setEnabled(True)
        w = self._worker
        if w is not None and not w.isRunning():
            self._worker = None

    # ── 一键安装 ──────────────────────────────────────────────────────
    def start_install(self):
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            return
        script = ROOT / "setup" / "bootstrap.ps1"
        if not script.is_file():
            QMessageBox.warning(self, "缺少安装脚本",
                                "找不到：\n%s\n\n请用完整包，或把 setup 文件夹补齐。" % script)
            return
        if QMessageBox.question(
                self, "一键安装 / 修复环境",
                "将自动完成：\n"
                "1. 创建/修复 Python 环境\n"
                "2. 按显卡安装 XPU / CUDA / CPU 版 PyTorch\n"
                "3. 从 hf-mirror 直链下载两个模型（约 2.6GB，免登录）\n\n"
                "总共约 8GB，需要联网，可能 5~15 分钟。继续吗？") != QMessageBox.Yes:
            return
        self.btn_check.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.btn_install.setEnabled(False)
        self._mode = "install"
        self._t0 = time.time()
        self._stage_pct = 2
        self._stage_label = "正在初始化安装脚本"
        self._update_bar(2, "正在启动一键安装 / 修复流程… (2%)")
        self._prog_timer.start()
        self._append("\n=== 一键安装开始（setup/bootstrap.ps1 -Force）===\n")
        self._append("提示：安装期间可以看日志，但不要关闭这个窗口。\n\n")
        self._set_status("正在安装…（上方进度条实时显示阶段与模型下载进度，请勿关闭窗口）")

        self._proc = QProcess(self)
        self._proc.setProgram("powershell.exe")
        self._proc.setArguments([
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Force",
        ])
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._read_proc)
        self._proc.finished.connect(self._install_finished)
        self._proc.errorOccurred.connect(self._install_error)
        self._proc.start()

    def _decode(self, data):
        raw = bytes(data)
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _read_proc(self):
        if self._proc is None:
            return
        try:
            chunk = self._decode(self._proc.readAllStandardOutput())
            self._parse_install_log_stage(chunk)
            self._append(chunk)
            self._tick_progress()
        except Exception:
            pass

    def _install_finished(self, code, _status):
        self._prog_timer.stop()
        self._mode = "idle"
        self._proc = None
        elapsed = max(0.1, time.time() - self._t0)
        self._append("\n=== 安装结束，退出码 %s ===\n" % code)
        self.btn_check.setEnabled(True)
        self.btn_close.setEnabled(True)
        self.btn_install.setEnabled(True)
        if code == 0:
            self._update_bar(100, "✔ 一键安装与校验全部完成 (100%%) · 总耗时 %ds" % int(elapsed))
            # 重新探测一次设备，让顶部“AI 加速”状态也刷新。
            try:
                owner = self.window()
                if owner is not None and hasattr(owner, "a"):
                    owner.a._start_probe()
            except Exception:
                pass
            self._set_status("安装完成，正在重新体检…")
            self.start_check()
        else:
            self._update_bar(self.install_bar.value(),
                             "✖ 安装未完成（退出码 %s）· 请查看下方日志" % code)
            self._set_status("安装没有完成（退出码 %s）。看上面的日志，修好后再点一次。" % code)

    def _install_error(self, err):
        self._prog_timer.stop()
        self._mode = "idle"
        self._proc = None
        self._update_bar(0, "✖ 安装进程启动失败")
        self._append("\n[启动安装进程失败] %s\n" % err)
        self.btn_check.setEnabled(True)
        self.btn_close.setEnabled(True)
        self.btn_install.setEnabled(True)
        self._set_status("安装进程启动失败。")

    def _handle_close_or_back(self):
        if self._embedded:
            if callable(self._on_back):
                self._on_back()
            return
        self.close()

    def reject(self):
        if self._embedded:
            self._handle_close_or_back()
            return
        super(EnvCheckDialog, self).reject()

    def keyPressEvent(self, ev):
        if self._embedded and ev.key() == Qt.Key_Escape:
            self._handle_close_or_back()
            ev.accept()
            return
        super(EnvCheckDialog, self).keyPressEvent(ev)

    def closeEvent(self, ev):
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "安装进行中",
                                    "一键安装还在运行，请等它结束再关闭这个窗口。")
            ev.ignore()
            return
        self._prog_timer.stop()
        super(EnvCheckDialog, self).closeEvent(ev)


class UpdateDialog(QDialog):
    """检查更新、下载替换与回退；网络与磁盘操作都在后台线程。"""

    sig_checked = pyqtSignal(object)
    sig_progress = pyqtSignal(str)
    sig_applied = pyqtSignal(object)
    sig_rolled = pyqtSignal(object)
    sig_failed = pyqtSignal(str)

    def __init__(self, parent=None, embedded=False, on_back=None):
        super().__init__(parent)
        self._embedded = bool(embedded)
        self._on_back = on_back
        self.setWindowTitle("检查更新")
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setMinimumSize(0, 0)
        else:
            self.setModal(False)
            fit_screen(self, 640, 500, 520, 380)
        self.info = None
        self._busy = False
        self._closing = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.lbl_title = QLabel("正在检查更新……")
        self.lbl_title.setObjectName("section")
        lay.addWidget(self.lbl_title)

        self.lbl_meta = QLabel("正在并发探测最快可用更新源（单源超时 4 秒）")
        self.lbl_meta.setObjectName("sub")
        self.lbl_meta.setWordWrap(True)
        lay.addWidget(self.lbl_meta)

        lay.addWidget(QLabel("更新日志："))
        self.txt_log = QPlainTextEdit()
        self.txt_log.setObjectName("log")
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(170)
        lay.addWidget(self.txt_log, 1)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        lay.addWidget(self.bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("sub")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        foot = QHBoxLayout()
        self.btn_recheck = QPushButton("重新检查")
        self.btn_recheck.setObjectName("ghost")
        self.btn_recheck.setMinimumHeight(34)
        self.btn_recheck.clicked.connect(self.start_check)
        foot.addWidget(self.btn_recheck)
        foot.addStretch(1)
        self.btn_update = QPushButton("立即更新")
        self.btn_update.setObjectName("primary")
        self.btn_update.setMinimumHeight(34)
        self.btn_update.clicked.connect(self.start_update)
        self.btn_update.hide()
        foot.addWidget(self.btn_update)

        self.btn_restart = QPushButton("立即重启软件")
        self.btn_restart.setMinimumHeight(34)
        self.btn_restart.setToolTip("关掉当前进程并用同样的参数重新启动（更新要重启才生效）")
        self.btn_restart.clicked.connect(self.restart_app)
        self.btn_restart.hide()
        foot.addWidget(self.btn_restart)

        self.btn_rollback = QPushButton("回退上一版本")
        self.btn_rollback.setMinimumHeight(34)
        self.btn_rollback.setToolTip("用 logs\\backup_prev 里上一次更新的备份恢复旧文件")
        self.btn_rollback.clicked.connect(self.rollback)
        self.btn_rollback.hide()
        foot.addWidget(self.btn_rollback)

        self.btn_close = QPushButton("← 返回演奏台" if self._embedded else "关闭")
        self.btn_close.setMinimumHeight(34)
        self.btn_close.clicked.connect(self._handle_close_or_back)
        foot.addWidget(self.btn_close)
        lay.addLayout(foot)

        self.sig_checked.connect(self._on_checked)
        self.sig_progress.connect(self._on_progress)
        self.sig_applied.connect(self._on_applied)
        self.sig_rolled.connect(self._on_rolled)
        self.sig_failed.connect(self._on_failed)

    def _handle_close_or_back(self):
        if self._embedded:
            if callable(self._on_back):
                self._on_back()
            return
        self.close()

    def reject(self):
        if self._embedded:
            self._handle_close_or_back()
            return
        super().reject()

    def keyPressEvent(self, ev):
        if self._embedded and ev.key() == Qt.Key_Escape:
            self._handle_close_or_back()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self._closing = True
        super().closeEvent(ev)

    # ── 状态辅助 ─────────────────────────────────────────────────────────
    def _append(self, msg):
        self.txt_log.appendPlainText(str(msg))

    def _set_busy(self, busy, text=""):
        self._busy = busy
        if busy:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1)
        for btn in (self.btn_recheck, self.btn_update, self.btn_restart, self.btn_rollback):
            btn.setEnabled(not busy)
        if text:
            self.lbl_status.setText(text)

    def _emit_safe(self, sig, val):
        if self._closing:
            return
        try:
            sig.emit(val)
        except RuntimeError:
            pass

    # ── 检查 ─────────────────────────────────────────────────────────────
    def start_check(self):
        if self._busy:
            return
        self.info = None
        self.txt_log.clear()
        self.lbl_title.setText("正在检查更新……")
        self.lbl_meta.setText("正在并发探测最快可用更新源（单源超时 4 秒）")
        self.btn_update.hide()
        self.btn_restart.hide()
        self.btn_rollback.setVisible(updater.has_backup())
        self._set_busy(True, "正在连接镜像…")
        threading.Thread(target=self._work_check, daemon=True).start()

    def _work_check(self):
        try:
            info = updater.check_for_update(
                log=lambda m: self._emit_safe(self.sig_progress, m))
        except Exception as exc:  # noqa: BLE001
            self._emit_safe(self.sig_failed, str(exc))
            return
        self._emit_safe(self.sig_checked, info)

    def _on_progress(self, msg):
        self._append(msg)

    def _on_checked(self, info):
        self.info = info
        self._set_busy(False)
        local_v = info.get("local_version") or "?"
        remote_v = info.get("remote_version") or "?"
        changes = info.get("changed") or []
        logs = info.get("changelog") or []
        self.lbl_meta.setText("本地版本 v%s　→　远端版本 v%s　｜　镜像：%s"
                              % (local_v, remote_v, info.get("base") or "?"))
        self.txt_log.clear()
        if logs:
            for line in logs:
                self._append("· %s" % line)
        else:
            self._append("（远端没有提供更新日志）")

        if info.get("status") == "latest":
            self.lbl_title.setText("当前已是最新版本 (v%s)" % remote_v)
            self.lbl_status.setText("共核对 %d 个文件，全部一致，无需更新。"
                                    % int(info.get("same_count") or 0))
            self.btn_update.hide()
        elif info.get("status") == "ahead":
            self.lbl_title.setText("本地版本 v%s 高于远端 v%s" % (local_v, remote_v))
            self.lbl_status.setText("远端还是旧版本，无需更新；更新按钮已停用，避免被反向覆盖。")
            self.btn_update.hide()
        else:
            self.lbl_title.setText("发现新版本 v%s（%d 个文件需要更新）"
                                   % (remote_v, len(changes)))
            self._append("")
            self._append("需要更新的文件：")
            for rel in changes:
                self._append("  - %s" % rel)
            self.lbl_status.setText(
                "更新前会自动备份到 logs\\backup_prev；下载后逐个核对 sha256 与语法，"
                "任何一项不符就整批放弃，软件保持原样。")
            self.btn_update.show()
        self.btn_rollback.setVisible(updater.has_backup())

    # ── 更新 ─────────────────────────────────────────────────────────────
    def start_update(self):
        if self._busy or not self.info:
            return
        if self.info.get("status") != "update":
            return
        if QMessageBox.question(
                self, "确认更新",
                "将更新 %d 个文件到 v%s。\n更新过程中请不要关闭软件。\n\n现在开始？"
                % (len(self.info.get("changed") or []),
                   self.info.get("remote_version") or "?")) != QMessageBox.Yes:
            return
        self.btn_update.hide()
        self._set_busy(True, "正在下载并校验……")
        self.lbl_title.setText("正在更新……")
        threading.Thread(target=self._work_update, daemon=True).start()

    def _work_update(self):
        try:
            staged = updater.download_update(
                self.info,
                log=lambda m: self._emit_safe(self.sig_progress, m),
                progress=lambda m: self._emit_safe(self.sig_progress, m))
            summary = updater.apply_update(
                self.info, staged,
                log=lambda m: self._emit_safe(self.sig_progress, m),
                progress=lambda m: self._emit_safe(self.sig_progress, m))
        except Exception as exc:  # noqa: BLE001
            self._emit_safe(self.sig_failed, str(exc))
            return
        self._emit_safe(self.sig_applied, summary)

    def _on_applied(self, summary):
        self._set_busy(False)
        self.lbl_title.setText("更新完成：v%s" % (summary.get("version") or "?"))
        self.lbl_status.setText(
            "已替换 %d 个文件，旧文件备份在 logs\\backup_prev。\n"
            "点击「立即重启软件」后新代码才生效（软件不会自己热重载模块）。"
            % int(summary.get("files") or 0))
        self._append("")
        self._append("更新完成，重启后生效。")
        self.btn_update.hide()
        self.btn_restart.show()
        self.btn_rollback.setVisible(True)

    # ── 回退 ─────────────────────────────────────────────────────────────
    def rollback(self):
        if self._busy:
            return
        if QMessageBox.question(
                self, "回退上一版本",
                "用 logs\\backup_prev 里的备份恢复上一次更新前的文件？\n"
                "回退后同样需要重启软件。") != QMessageBox.Yes:
            return
        self._set_busy(True, "正在回退……")
        self.lbl_title.setText("正在回退……")
        threading.Thread(target=self._work_rollback, daemon=True).start()

    def _work_rollback(self):
        try:
            summary = updater.rollback_update(
                log=lambda m: self._emit_safe(self.sig_progress, m))
        except Exception as exc:  # noqa: BLE001
            self._emit_safe(self.sig_failed, str(exc))
            return
        self._emit_safe(self.sig_rolled, summary)

    def _on_rolled(self, summary):
        self._set_busy(False)
        self.info = None
        self.lbl_title.setText("已回退上一版本")
        msg = "已恢复 %d 个文件（删除新增 %d 个）" % (
            int(summary.get("restored") or 0), int(summary.get("deleted") or 0))
        if summary.get("missing"):
            msg += "\n以下文件备份缺失：%s" % "、".join(summary["missing"])
        msg += "\n点击「立即重启软件」后生效。"
        self.lbl_status.setText(msg)
        self._append("")
        self._append(msg.replace("\n", " "))
        self.btn_update.hide()
        self.btn_restart.show()

    def _on_failed(self, msg):
        self._set_busy(False)
        self.lbl_title.setText("操作失败（软件保持原样）")
        self.lbl_status.setText(msg)
        self._append("")
        self._append("× %s" % msg)
        if self.info and self.info.get("status") == "update":
            self.btn_update.show()
        self.btn_rollback.setVisible(updater.has_backup())

    # ── 重启 ─────────────────────────────────────────────────────────────
    def restart_app(self):
        win = self.window()
        if win is not None and win is not self:
            if not win.close():
                return
        elif not self.close():
            return
        try:
            exe, args = updater.restart_argv()
            ok = QProcess.startDetached(exe, args, str(ROOT))
        except Exception as exc:  # noqa: BLE001
            ok = False
            self._append("× 重启失败：%s" % exc)
        if not ok:
            QMessageBox.warning(self, "重启失败", "自动重启没成功，请手动关掉软件再双击启动。")
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()


class UnifiedWindow(QMainWindow):
    sig_external = pyqtSignal(str, object)
    sig_status_ready = pyqtSignal(object, bool)
    sig_midi_status = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("异键演奏")
        fit_screen(self, 1280, 820, min_w=820, min_h=540)
        try:
            avail = QApplication.desktop().availableGeometry(self)
            self.setMinimumSize(min(820, int(avail.width() * 0.80)),
                                min(540, int(avail.height() * 0.75)))
        except Exception:
            self.setMinimumSize(820, 540)

        try:
            _pal_dark = QApplication.palette().window().color().lightness() < 128
            self.theme_mode = "dark" if _pal_dark else "light"
        except Exception:
            self.theme_mode = GT.load_mode() or "dark"
        self._closing = False
        self._status_probing = False
        self._pill_states = {}
        self._midi_nav_state = None
        self._last_tab_idx = 1

        backdrop = UW.StageBackdropWidget(theme_mode=self.theme_mode)
        self._backdrop = backdrop
        self.setCentralWidget(backdrop)

        shell = QHBoxLayout(backdrop)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # ── 左侧星曜导航轨（182px） ─────────────────────────────────────
        nav_rail = QFrame()
        nav_rail.setObjectName("navRail")
        nav_rail.setFixedWidth(182)
        nv = QVBoxLayout(nav_rail)
        nv.setContentsMargins(12, 16, 12, 14)
        nv.setSpacing(10)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        self.avatar_badge = UW.AvatarRingBadge(46, nav_rail, theme_mode=self.theme_mode)
        brand_row.addWidget(self.avatar_badge)
        brand_txt = QVBoxLayout()
        brand_txt.setSpacing(3)
        title = QLabel("★ 异键演奏")
        title.setObjectName("appTitle")
        title.setToolTip("[F8] 开始/暂停　[F12] 紧急停止")
        brand_txt.addWidget(title)
        ver_pill = QLabel("v1.4 ♪")
        ver_pill.setObjectName("goldPill")
        ver_pill.setAlignment(Qt.AlignCenter)
        ver_pill.setMaximumWidth(64)
        brand_txt.addWidget(ver_pill)
        brand_row.addLayout(brand_txt, 1)
        nv.addLayout(brand_row)
        nv.addSpacing(8)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self.nav_play = UW.NavRailButton("乐器演奏台", "guitar")
        self.nav_live = UW.NavRailButton("MIDI 键盘直弹", "keyboard")
        self.nav_ai = UW.NavRailButton("AI 智能扒谱", "ai")
        for btn in (self.nav_play, self.nav_live, self.nav_ai):
            self._nav_group.addButton(btn)
            nv.addWidget(btn)
        nv.addStretch(1)

        # 底部紧凑状态胶囊（AI / 游戏 / 管理员）
        self.lbl_ai = QLabel("AI: 检测中…")
        self.lbl_game = QLabel("游戏: 检测中…")
        self.lbl_admin = QLabel("管理: 检测中…")
        for w in (self.lbl_ai, self.lbl_game, self.lbl_admin):
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            nv.addWidget(w)

        nv.addSpacing(4)
        self.btn_guide = QPushButton("使用指南")
        self.btn_guide.setObjectName("ghost")
        self.btn_guide.setIconSize(QSize(16, 16))
        self.btn_guide.setToolTip("打开新手引导、三模式对照与 MIDI 键盘直弹速查手册")
        self.btn_guide.clicked.connect(lambda: self.open_guide(first_run=False))
        nv.addWidget(self.btn_guide)

        self.btn_env_check = QPushButton("环境与显卡体检")
        self.btn_env_check.setObjectName("ghost")
        self.btn_env_check.setIconSize(QSize(16, 16))
        self.btn_env_check.clicked.connect(self.show_env_check)
        nv.addWidget(self.btn_env_check)

        self.btn_update_check = QPushButton("检查更新")
        self.btn_update_check.setObjectName("ghost")
        self.btn_update_check.setIconSize(QSize(16, 16))
        self.btn_update_check.setToolTip(
            "联网检查 GitHub 上的新版本（多镜像探测 + 哈希/语法校验 + 自动备份与回退）")
        self.btn_update_check.clicked.connect(self.open_update_dialog)
        nv.addWidget(self.btn_update_check)

        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("ghost")
        self.btn_theme.setIconSize(QSize(16, 16))
        self.btn_theme.clicked.connect(self.toggle_theme)
        nv.addWidget(self.btn_theme)
        shell.addWidget(nav_rail)

        # ── 右侧透明舞台区（隐藏原生 TabBar，保持 tabs=2） ─────────────
        stage_host = QWidget()
        stage_host.setObjectName("stageHost")
        stage_lay = QVBoxLayout(stage_host)
        stage_lay.setContentsMargins(8, 8, 8, 8)
        stage_lay.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("stageTabs")
        self.tabs.tabBar().hide()
        stage_lay.addWidget(self.tabs, 1)
        shell.addWidget(stage_host, 1)

        # 标签 0：扒谱
        self.tab_a = QWidget()
        self.tab_a.setObjectName("stagePage")
        la = QVBoxLayout(self.tab_a)
        la.setContentsMargins(0, 0, 0, 0)
        self.a = AGui.MainWindow()
        a_central = self.a.takeCentralWidget()
        if a_central is not None:
            a_central.setObjectName("stagePage")
            sa = QScrollArea()
            sa.setObjectName("stageScroll")
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.viewport().setAutoFillBackground(False)
            sa.setWidget(a_central)
            la.addWidget(sa, 1)
        self.tabs.addTab(self.tab_a, "AI 智能扒谱（音频转 MIDI）")

        # 标签 1：演奏
        self.tab_b = QWidget()
        self.tab_b.setObjectName("stagePage")
        lb = QVBoxLayout(self.tab_b)
        lb.setContentsMargins(0, 0, 0, 0)
        self.b = BGui.MainWindow()
        b_central = self.b.takeCentralWidget()
        if b_central is not None:
            b_central.setObjectName("stagePage")
            sb = QScrollArea()
            sb.setObjectName("stageScroll")
            sb.setWidgetResizable(True)
            sb.setFrameShape(QFrame.NoFrame)
            sb.viewport().setAutoFillBackground(False)
            sb.setWidget(b_central)
            lb.addWidget(sb, 1)
        self.tabs.addTab(self.tab_b, "乐器自动演奏（吉他 · 钢琴）")

        # 其余功能页全部内嵌进同一主窗工作区（零弹窗）：
        # 2: MIDI 直弹 / 3: 使用指南 / 4: 环境体检 / 5: 检查更新
        self._midi_live_dlg = None
        self._guide_dlg = None
        self._env_dialog = None
        self._update_dlg = None
        self._tab_hosts = {}
        for idx_p, name_p in (
            (2, "MIDI 键盘直弹"),
            (3, "使用指南"),
            (4, "环境与显卡体检"),
            (5, "检查更新"),
        ):
            host_p = QWidget()
            host_p.setObjectName("stagePage")
            hl = QVBoxLayout(host_p)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            self._tab_hosts[idx_p] = (host_p, hl)
            self.tabs.addTab(host_p, name_p)

        # 导航联动 + 默认进入演奏台 (tab 1)
        self.nav_ai.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.nav_play.clicked.connect(self._activate_play_tab)
        self.nav_live.clicked.connect(self.open_midi_live)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.setCurrentIndex(1)
        self._last_tab_idx = 1
        self._sync_nav_by_tab(1)
        self._sync_theme_button()

        # 一键全流程完成后自动送入演奏台
        try:
            self.a.sig_all_done.connect(self._on_a_all_done)
            self.a.sendRequested.connect(self.send_current_to_player)
        except Exception:
            pass

        self.sig_external.connect(self._apply_external)
        self.sig_status_ready.connect(self._on_status_ready)
        self.sig_midi_status.connect(self._on_midi_status)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_status)
        self._timer.start(2000)
        self.refresh_status()
        if (
            "--smoke" not in sys.argv
            and os.environ.get("QT_QPA_PLATFORM") != "offscreen"
            and not bool(PC.load().get("guide_shown", False))
        ):
            QTimer.singleShot(450, lambda: self.open_guide(first_run=True))

    def _on_tab_changed(self, idx: int) -> None:
        prev = getattr(self, "_last_tab_idx", None)
        self._last_tab_idx = idx
        if prev == 2 and idx != 2:
            dlg_live = getattr(self, "_midi_live_dlg", None) or getattr(self.b, "_midi_live_dlg", None)
            if dlg_live is not None and hasattr(dlg_live, "stop_if_running"):
                try:
                    dlg_live.stop_if_running()
                except Exception:
                    pass
        self._sync_nav_by_tab(idx)

    def _activate_play_tab(self) -> None:
        self.tabs.setCurrentIndex(1)
        if hasattr(self.b, "show_play_stage"):
            try:
                self.b.show_play_stage()
            except Exception:
                pass

    def _sync_nav_by_tab(self, idx: int) -> None:
        self._nav_group.setExclusive(False)
        for btn, target_idx in ((self.nav_ai, 0), (self.nav_play, 1), (self.nav_live, 2)):
            btn.blockSignals(True)
            btn.setChecked(idx == target_idx)
            btn.blockSignals(False)
            btn._refresh_icon()
        self._nav_group.setExclusive(True)

    def _open_studio_from_nav(self) -> None:
        self.tabs.setCurrentIndex(1)
        try:
            if hasattr(self.b, "open_multipart_studio"):
                self.b.open_multipart_studio()
        except Exception as exc:
            if CU is not None:
                CU.get_logger("gui_main").debug("open_multipart_studio failed: %s", exc)

    def _embed_into_tab(self, tab_idx: int, widget: QWidget) -> None:
        pair = self._tab_hosts.get(tab_idx)
        if not pair:
            return
        _host, lay = pair
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None and w is not widget:
                w.setParent(None)
        if widget.parent() is not _host:
            lay.addWidget(widget, 1)
        widget.show()

    def open_guide(self, first_run=False):
        """切入主窗内嵌《异键演奏》新手引导与使用指南页（不额外弹窗）。"""
        dlg = getattr(self, "_guide_dlg", None)
        if dlg is None:
            import guide_dialog as GD
            dlg = GD.GuideDialog(
                theme_mode=self.theme_mode,
                first_run=bool(first_run),
                parent=self,
                embedded=True,
                on_back=self._activate_play_tab,
            )
            self._guide_dlg = dlg
            self._embed_into_tab(3, dlg)
        elif first_run and hasattr(dlg, "chk_no_auto"):
            dlg._first_run = True
            dlg.chk_no_auto.setVisible(True)
        if hasattr(dlg, "sync_theme"):
            try:
                dlg.sync_theme(self.theme_mode)
            except Exception:
                pass
        self.tabs.setCurrentIndex(3)
        self._sync_nav_by_tab(3)
        return dlg

    def open_midi_live(self):
        """切入主窗内嵌实时 MIDI 键盘直弹工作台，与演奏台双向联动（零弹窗）。"""
        dlg = getattr(self, "_midi_live_dlg", None) or getattr(self.b, "_midi_live_dlg", None)
        if dlg is None:
            import midi_live_ui as MLU
            mode = getattr(self.b, "instrument_mode", "guitar_wheel")
            on_mode = (
                self.b._on_midi_live_mode_changed
                if hasattr(self.b, "_on_midi_live_mode_changed")
                else (lambda m: self.b.set_instrument_mode(m) if hasattr(self.b, "set_instrument_mode") else None)
            )
            on_start = (
                self.b._on_midi_live_before_start
                if hasattr(self.b, "_on_midi_live_before_start")
                else (lambda: self.b.stop_play() if hasattr(self.b, "stop_play") else None)
            )
            dlg = MLU.MidiLiveDialog(
                instrument_mode=mode,
                io=getattr(self.b, "io", None),
                on_mode_changed=on_mode,
                on_before_start=on_start,
                parent=self,
                embedded=True,
                on_back=self._activate_play_tab,
            )
            self._embed_into_tab(2, dlg)
        else:
            mode = getattr(self.b, "instrument_mode", "guitar_wheel")
            if hasattr(dlg, "set_instrument_mode"):
                try:
                    dlg.set_instrument_mode(mode)
                except Exception:
                    pass
        self._midi_live_dlg = dlg
        if hasattr(self.b, "_midi_live_dlg"):
            self.b._midi_live_dlg = dlg
        try:
            if hasattr(dlg, "sync_theme"):
                dlg.sync_theme(self.theme_mode)
        except Exception:
            pass
        self.tabs.setCurrentIndex(2)
        self._sync_nav_by_tab(2)
        return dlg

    def _sync_theme_button(self):
        dark = (self.theme_mode != "light")
        icon_col = "#CBD5E1" if dark else "#334155"
        if hasattr(self, "btn_guide"):
            self.btn_guide.setIcon(UW.make_vector_icon("book", icon_col, 16))
        self.btn_env_check.setIcon(UW.make_vector_icon("shield", icon_col, 16))
        self.btn_update_check.setIcon(UW.make_vector_icon("update", icon_col, 16))
        for nb in (self.nav_play, self.nav_ai, self.nav_live):
            nb.set_theme_mode(self.theme_mode)
        if hasattr(self, "avatar_badge") and hasattr(self.avatar_badge, "set_theme_mode"):
            self.avatar_badge.set_theme_mode(self.theme_mode)
        if dark:
            self.btn_theme.setText("星辉浅昼")
            self.btn_theme.setIcon(UW.make_vector_icon("sun", "#F4C869", 16))
            self.btn_theme.setToolTip("当前：星曜暗夜；点击切换星辉浅昼")
        else:
            self.btn_theme.setText("星曜暗夜")
            self.btn_theme.setIcon(UW.make_vector_icon("moon", "#2F5CE5", 16))
            self.btn_theme.setToolTip("当前：星辉浅昼；点击切换星曜暗夜")

    def toggle_theme(self):
        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        GT.apply_theme(QApplication.instance(), self.theme_mode)
        GT.save_mode(self.theme_mode)
        self._backdrop.set_theme_mode(self.theme_mode)
        self._sync_theme_button()
        self._pill_states.clear()
        self.refresh_status()
        for sub in (self.a, self.b):
            try:
                if hasattr(sub, "on_theme_changed"):
                    sub.on_theme_changed(self.theme_mode)
            except Exception as exc:
                if CU is not None:
                    CU.get_logger("gui_main").debug("on_theme_changed error: %s", exc)
        dlg_live = getattr(self, "_midi_live_dlg", None) or getattr(self.b, "_midi_live_dlg", None)
        if dlg_live is not None and hasattr(dlg_live, "sync_theme"):
            try:
                dlg_live.sync_theme(self.theme_mode)
            except Exception:
                pass
        dlg_guide = getattr(self, "_guide_dlg", None)
        if dlg_guide is not None and hasattr(dlg_guide, "sync_theme"):
            try:
                dlg_guide.sync_theme(self.theme_mode)
            except Exception:
                pass

    def show_env_check(self):
        dlg = getattr(self, "_env_dialog", None)
        if dlg is None:
            dlg = EnvCheckDialog(self, embedded=True, on_back=self._activate_play_tab)
            self._env_dialog = dlg
            self._embed_into_tab(4, dlg)
        self.tabs.setCurrentIndex(4)
        self._sync_nav_by_tab(4)
        if not dlg.log.toPlainText().strip():
            dlg.start_check()
        return dlg

    def open_update_dialog(self):
        """切入主窗内嵌「检查更新」页面：每次点都重新探测远端。"""
        dlg = getattr(self, "_update_dlg", None)
        if dlg is None:
            dlg = UpdateDialog(self, embedded=True, on_back=self._activate_play_tab)
            self._update_dlg = dlg
            self._embed_into_tab(5, dlg)
        self.tabs.setCurrentIndex(5)
        self._sync_nav_by_tab(5)
        dlg.start_check()
        return dlg

    def _on_a_all_done(self, path):
        try:
            if self.a.chk_auto_send.isChecked():
                self.send_to_player(path)
                self.tabs.setCurrentIndex(1)
        except Exception:
            pass
        # v1.2：扒谱完成后刷新演奏页左侧「本地 MIDI 曲库」
        try:
            self.b.refresh_midi_list()
        except Exception:
            pass

    def _set_pill_state(self, label, state):
        dark = (self.theme_mode != "light")
        key = (state, dark)
        if self._pill_states.get(label) == key:
            return
        self._pill_states[label] = key
        if state == "ok":
            bg, fg, bd = (("rgba(16, 185, 129, 0.16)", "#34D399", "rgba(52, 211, 153, 0.40)")
                          if dark else ("rgba(209, 250, 229, 0.88)", "#065F46", "rgba(52, 211, 153, 0.58)"))
        elif state == "warn":
            bg, fg, bd = (("rgba(244, 200, 105, 0.16)", "#F4C869", "rgba(244, 200, 105, 0.42)")
                          if dark else ("rgba(254, 243, 199, 0.90)", "#92400E", "rgba(245, 158, 11, 0.54)"))
        else:
            bg, fg, bd = (("rgba(148, 163, 184, 0.14)", "#94A3B8", "rgba(148, 163, 184, 0.28)")
                          if dark else ("rgba(226, 232, 240, 0.78)", "#475569", "rgba(148, 163, 184, 0.48)"))
        label.setStyleSheet(
            "padding: 3px 9px; border-radius: 7px; font-size: 12px; font-weight: 600; "
            f"background: {bg}; color: {fg}; border: 1px solid {bd};"
        )

    @staticmethod
    def _compact_ai_label(dev: str) -> tuple[str, str]:
        """把设备检测文案缩写为适合 182px 侧栏胶囊的紧凑标签，返回 (短文案, 状态)。"""
        short_dev = (str(dev or "").replace("当前可用：", "")
                        .replace("（点右上角「环境与显卡体检」一键安装）", "")
                        .replace("（点左侧「环境与显卡体检」一键安装）", ""))
        head = short_dev.split("（", 1)[0].strip() or short_dev
        if "未安装" in short_dev:
            return "未装AI模型", "warn"
        if "正在探测" in short_dev or "检测中" in short_dev:
            return "探测中…", "idle"
        if "XPU" in short_dev:
            return "XPU 就绪", "ok"
        if "CUDA" in short_dev:
            return "CUDA 就绪", "ok"
        if "ROCm" in short_dev:
            return "ROCm 就绪", "ok"
        if "CPU" in short_dev:
            return "CPU 模式", "warn"
        if "失败" in short_dev:
            return "探测失败", "warn"
        return head, "idle"

    def _set_elided_pill_text(self, label: QLabel, text: str) -> None:
        max_w = max(96, (label.width() if label.width() > 40 else 158) - 20)
        elided = label.fontMetrics().elidedText(text, Qt.ElideRight, max_w)
        label.setText(elided)

    def refresh_status(self):
        try:
            dev = self.a.device_state.text()
        except Exception:
            dev = "未知"
        compact_dev, ai_state = self._compact_ai_label(dev)
        self._set_elided_pill_text(self.lbl_ai, "AI: %s" % compact_dev)
        self.lbl_ai.setToolTip("AI 加速：%s" % dev)
        self._set_pill_state(self.lbl_ai, ai_state)

        if self._status_probing or self._closing:
            return
        self._status_probing = True

        def _probe():
            m = None
            adm = False
            midi_devs = []
            try:
                m = self.b.io.metrics()
            except Exception as exc:
                if CU is not None:
                    CU.get_logger("gui_main").debug("io.metrics failed: %s", exc)
            try:
                adm = ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                adm = False
            try:
                import midi_live as ML
                midi_devs = ML.list_midi_inputs() or []
            except Exception as exc:
                if CU is not None:
                    CU.get_logger("gui_main").debug("list_midi_inputs failed: %s", exc)
                midi_devs = []
            if not self._closing:
                try:
                    self.sig_status_ready.emit(m, bool(adm))
                except Exception:
                    pass
                try:
                    self.sig_midi_status.emit(midi_devs)
                except Exception:
                    pass

        threading.Thread(target=_probe, daemon=True).start()

    @staticmethod
    def _sanitize_device_name(name: str, max_len: int = 24) -> str:
        try:
            import midi_live as ML
            fn = getattr(ML, "sanitize_device_name", None)
            if callable(fn):
                return str(fn(name, max_len=max_len) or "").strip()
        except Exception:
            pass
        cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or "")).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"(?i)\b(sn|serial|uid|vid|pid)[:=_-]?[a-z0-9]{4,}\b", "", cleaned).strip()
        if max_len and len(cleaned) > max_len:
            cleaned = cleaned[: max(1, max_len - 1)].rstrip() + "…"
        return cleaned

    def _on_midi_status(self, devs):
        if self._closing:
            return
        devs = list(devs or [])
        dlg_live = getattr(self, "_midi_live_dlg", None) or getattr(self.b, "_midi_live_dlg", None)
        eng = getattr(dlg_live, "engine", None) if dlg_live is not None else None
        hw_connected = False
        if eng is not None:
            try:
                hw_connected = bool(
                    eng.is_running()
                    and getattr(eng, "is_hardware_connected", lambda: False)()
                )
            except Exception:
                hw_connected = False
        if hw_connected:
            dev_name = ""
            try:
                dev_name = str(getattr(eng, "connected_device_name", lambda: "")() or "").strip()
            except Exception:
                dev_name = ""
            if not dev_name and devs:
                dev_name = "、".join(
                    self._sanitize_device_name(d.get("name"))
                    for d in devs if isinstance(d, dict) and d.get("name")
                )
            else:
                dev_name = self._sanitize_device_name(dev_name)
            text = "直弹 · 已连"
            tip = f"已连接 MIDI 设备：{dev_name}" if dev_name else "MIDI 键盘直弹已连接"
        elif devs:
            clean_names = [
                self._sanitize_device_name(d.get("name"))
                for d in devs if isinstance(d, dict) and d.get("name")
            ]
            names = "、".join(n for n in clean_names if n) or "MIDI 键盘"
            text = "直弹 · 就绪"
            tip = f"已检测到 MIDI 键盘：{names}（点击即插即用直弹）"
        else:
            text = "MIDI 键盘直弹"
            tip = "连接外接 USB MIDI 键盘或用虚拟琴键实时直弹"
        state = (text, tip)
        if state != self._midi_nav_state:
            self._midi_nav_state = state
            if self.nav_live.text() != text:
                self.nav_live.setText(text)
            if self.nav_live.toolTip() != tip:
                self.nav_live.setToolTip(tip)

    def _on_status_ready(self, m, adm: bool):
        self._status_probing = False
        if self._closing:
            return
        if m:
            self.lbl_game.setText("游戏: 已连接")
            self.lbl_game.setToolTip("游戏窗口：%d×%d" % (m[2], m[3]))
            self._set_pill_state(self.lbl_game, "ok")
        else:
            self.lbl_game.setText("游戏: 未检测")
            self.lbl_game.setToolTip("游戏窗口：未检测到（启动游戏后自动识别）")
            self._set_pill_state(self.lbl_game, "idle")
        self.lbl_admin.setText("管理: %s" % ("有" if adm else "无"))
        self.lbl_admin.setToolTip("没有管理员权限时按键送不进游戏；请用启动器重新打开")
        self._set_pill_state(self.lbl_admin, "ok" if adm else "warn")

    def _apply_external(self, path, payload):
        """主线程里刷新演奏页（后台线程只 emit，不碰控件）。"""
        if self._closing:
            return
        if isinstance(payload, dict) and "compiled" in payload:
            compiled = payload.get("compiled") or {}
            for err in (payload.get("errors") or []):
                try:
                    self.b._append_log(err)
                except Exception:
                    pass
        else:
            compiled = payload or {}
        try:
            self.b.sync_external(path, compiled)
            self.b._append_log("√ 已送入演奏台并同步当前模式：%s"
                               % os.path.basename(path))
        except Exception as exc:  # noqa: BLE001
            try:
                self.b._append_log("× 送入演奏台失败：%s" % exc)
            except Exception:
                pass

    def send_to_player(self, path, wait=False):
        """内部联动：加载 .mid 到演奏页并预编译四角色（UI 状态一并同步）。"""
        path = str(path)
        self._activate_play_tab()
        self.b.path = path
        try:
            self.b.lbl_file.setText(os.path.basename(path))
        except Exception:
            pass
        roles = (MC.ROLE_SOLO, MC.ROLE_DUET_LEAD,
                 MC.ROLE_DUET_RHYTHM, MC.ROLE_DUET_RHYTHM_FRET)
        try:
            ap = bool(self.b.chk_arpeggio.isChecked())
        except Exception:
            ap = True
        try:
            cr = bool(self.b.chk_chord_rhythm.isChecked())
        except Exception:
            cr = True
        mode = getattr(self.b, "instrument_mode", "guitar_wheel")

        def job():
            compiled = {}
            errors = []
            if mode == "guitar_wheel":
                for role in roles:
                    try:
                        tl = MC.compile_file(path, role=role, enable_arpeggio=ap,
                                             enable_chord_rhythm=cr)
                        compiled[role] = tl
                    except Exception as exc:  # noqa: BLE001
                        errors.append("× 预编译 %s 失败：%s" % (role, exc))
            return {"compiled": compiled, "errors": errors}

        if wait:
            self._apply_external(path, job())
            return (len(self.b.timelines) >= 4) if mode == "guitar_wheel" else True

        def bg():
            payload = job()
            if not self._closing:
                try:
                    self.sig_external.emit(path, payload)
                except Exception:
                    pass

        threading.Thread(target=bg, daemon=True).start()
        return True

    def send_current_to_player(self):
        path = None
        try:
            r3 = (self.a.results or {}).get(3)
            if r3:
                path = (r3.get("outputs") or {}).get("final_midi")
        except Exception:
            path = None
        if not path or not os.path.isfile(path):
            from PyQt5.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "选择要演奏的 MIDI",
                                                  str(ROOT / "out"), "MIDI (*.mid)")
        if path:
            self.send_to_player(path)

    def closeEvent(self, ev):
        env_dlg = getattr(self, "_env_dialog", None)
        if env_dlg is not None:
            proc = getattr(env_dlg, "_proc", None)
            if proc is not None and proc.state() != QProcess.NotRunning:
                QMessageBox.information(
                    self, "安装进行中",
                    "一键安装还在运行，请等它结束再关闭程序。",
                )
                ev.ignore()
                return
        self._closing = True
        try:
            self._timer.stop()
        except Exception:
            pass
        if env_dlg is not None:
            try:
                env_dlg._prog_timer.stop()
            except Exception:
                pass
            w = getattr(env_dlg, "_worker", None)
            if w is not None and w.isRunning():
                w.wait(8000)
                if w.isRunning():
                    w.quit()
                    w.wait(2000)
                    if w.isRunning():
                        w.terminate()
                        w.wait(1000)
            try:
                env_dlg.close()
            except Exception:
                pass
        upd_dlg = getattr(self, "_update_dlg", None)
        if upd_dlg is not None:
            try:
                upd_dlg._closing = True
                upd_dlg.close()
            except Exception:
                pass
        for dlg_live in (getattr(self, "_midi_live_dlg", None), getattr(self.b, "_midi_live_dlg", None)):
            if dlg_live is not None:
                try:
                    if hasattr(dlg_live, "stop_if_running"):
                        dlg_live.stop_if_running()
                    dlg_live.close()
                except Exception:
                    pass
        dlg_guide = getattr(self, "_guide_dlg", None)
        if dlg_guide is not None:
            try:
                dlg_guide.close()
            except Exception:
                pass
        try:
            PC.clean_tmp_wav()
        except Exception:
            pass
        try:
            self.a.close()
        except Exception:
            pass
        try:
            if self.b.player and self.b.player.is_alive():
                self.b.player.stop()
        except Exception:
            pass
        try:
            self.b.close()
        except Exception:
            pass
        ev.accept()


def _load_app_icon():
    """窗口/任务栏图标：优先 assets\\icon.ico，缺失时退回 icon.png；没有就忽略。"""
    try:
        from PyQt5.QtGui import QIcon
    except Exception:
        return None
    for name in ("icon.ico", "icon.png"):
        path = ROOT / "assets" / name
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


def main(argv=None):
    heal_portable_venvs(ROOT)
    try:
        PC.clean_tmp_wav()
    except Exception:
        pass
    argv = list(sys.argv if argv is None else argv)
    # High-DPI 抗锯齿（必须在 QApplication 创建之前设置）
    try:
        from PyQt5.QtCore import Qt, QCoreApplication
        from PyQt5.QtGui import QGuiApplication
        if QCoreApplication.instance() is None:
            QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
            QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
            if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
                QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    # 让任务栏把本程序当成独立应用（用自己的图标，不跟 python 合并）
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "YijianYanzou.Desktop")
        
        if os.name == "nt":
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00008000)  # ABOVE_NORMAL_PRIORITY_CLASS

            class _POWER_THROTTLING(ctypes.Structure):
                _fields_ = [("Version", ctypes.c_ulong),
                            ("ControlMask", ctypes.c_ulong),
                            ("StateMask", ctypes.c_ulong)]

            pt = _POWER_THROTTLING(1, 0x3, 0x0)  # 禁用后台降频(0x1) + 禁用忽略高精度时钟(0x2)
            k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(pt), ctypes.sizeof(pt))
    except Exception:
        pass
    app = QApplication(argv)
    _icon = _load_app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)
    apply_theme(app)
    win = UnifiedWindow()
    if "--smoke" in argv:
        win.show()

        def _finish():
            try:
                if getattr(win.a, "probe", None) is not None:
                    win.a.probe.wait(20000)
            except Exception:
                pass
            app.quit()

        QTimer.singleShot(1000, _finish)
        rc = app.exec_()
        print("GUI_SMOKE_OK title=%s tabs=%d" % (win.windowTitle(), win.tabs.count()))
        return 0 if rc == 0 else rc
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
