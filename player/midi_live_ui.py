# -*- coding: utf-8 -*-
"""player/midi_live_ui.py -- 《异键演奏》v1.4 实时 MIDI 键盘直弹可视化交互窗口。

支持：
  - 外接 USB MIDI 键盘 / 虚拟 MIDI 端口实时演奏（guitar_wheel / guitar_36 / piano_36）；
  - 61 键（C2~C7，MIDI 36..96）交互式钢琴键盘条，高亮有效音域与按下状态，支持鼠标点击/拖动试弹；
  - 通道过滤、半音移调、越界策略（八度折叠 / 严格忽略）、CC64 延音踏板、合批窗、焦点守卫与轮盘子模式控制；
  - 后台引擎回调全部通过 pyqtSignal 安全转发至主线程更新 UI。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (_ROOT, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

for _cand_plug in (
    _ROOT / ".venv" / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins",
    Path(sys.prefix) / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins",
):
    if _cand_plug.is_dir():
        os.environ.setdefault("QT_PLUGIN_PATH", str(_cand_plug))
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_cand_plug / "platforms"))
        break

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt5.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,  # noqa: E402
                         QPen)
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox,  # noqa: E402
                             QDialog, QDoubleSpinBox, QFrame, QHBoxLayout,
                             QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

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

try:
    from . import keyboard_layout as KL  # type: ignore  # noqa: E402
except Exception:
    try:
        import keyboard_layout as KL  # type: ignore  # noqa: E402
    except Exception:
        KL = None

try:
    from . import midi_live as ML  # type: ignore  # noqa: E402
except Exception:
    try:
        import midi_live as ML  # type: ignore  # noqa: E402
    except Exception:
        ML = None


def _log_debug(msg: str, *args) -> None:
    if CU is not None and hasattr(CU, "get_logger"):
        try:
            CU.get_logger("midi_live_ui").debug(msg, *args)
        except Exception:
            pass


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


# ── 安全兜底契约（midi_live.py 未就绪时保证 UI 可独立创建自检） ──────────────
_FALLBACK_CONFIG = {
    "device_name": "",
    "keyboard_size": 61,
    "channel": 0,
    "ignore_drums": True,
    "transpose": 0,
    "out_of_range": "fold",
    "sustain_pedal": True,
    "chord_window_ms": 8.0,
    "mod_lead_ms": 2.0,
    "focus_guard": True,
    "auto_connect": True,
    "wheel_submode": "fret",
    "chord_strum_mode": False,
    "single_key_chord": False,
    "custom_key_map": {},
}

KEYBOARD_SPECS = {
    25: (48, 72, "25 键 (C3~C5)"),
    37: (48, 84, "37 键 (C3~C6)"),
    49: (36, 84, "49 键 (C2~C6)"),
    61: (36, 96, "61 键 (C2~C7)"),
    76: (28, 103, "76 键 (E1~G7)"),
    88: (21, 108, "88 键 (A0~C8)"),
}

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_BLACK_PCS = frozenset({1, 3, 6, 8, 10})


def _midi_name(pitch: int) -> str:
    p = int(pitch)
    return f"{_NOTE_NAMES[p % 12]}{p // 12 - 1}"


def _fallback_describe_mapping(instrument_mode: str, wheel_submode: str = "fret",
                               transpose: int = 0, single_key_chord: bool = False) -> dict:
    mode = str(instrument_mode or "guitar_wheel")
    labels: dict[int, str] = {}
    if mode in ("guitar_36", "piano_36"):
        lo, hi = (52, 87) if mode == "guitar_36" else (60, 95)
        for p in range(lo, hi + 1):
            if KL is not None and hasattr(KL, "key_label_for_pitch"):
                labels[p] = KL.key_label_for_pitch(p, mode)
        title = "吉他 · 钢琴键（36 键）" if mode == "guitar_36" else "钢琴 · 36 键"
        hint = "E3~D#6 36键直弹（支持延音）" if mode == "guitar_36" else "C4~B6 36键直弹（无延音，Shift/Ctrl 互斥）"
        return {"range": (lo, hi), "title": title, "hint": hint, "key_labels": labels}
    if wheel_submode == "chord" and single_key_chord:
        return {
            "range": (48, 71),
            "title": "吉他 · 和弦轮盘（单键 24 和弦）",
            "hint": "C3~B4 一键触发 4 页 × 6 扇区和弦",
            "key_labels": {p: f"C{p - 47}" for p in range(48, 72)},
        }
    return {
        "range": (40, 88),
        "title": "吉他 · 轮盘（品格/和弦）",
        "hint": "E2~E6 轮盘状态机直弹",
        "key_labels": {p: f"p{p}" for p in range(40, 89)},
    }


class _FallbackLiveEngine:
    """当 player/midi_live.py 尚不可用时的安全测试兜底引擎。"""

    def __init__(self, io=None, config=None, on_event=None, on_status=None, on_before_start=None):
        self.io = io
        self.config = dict(_FALLBACK_CONFIG)
        if config:
            self.config.update(config)
        self.on_event = on_event
        self.on_status = on_status
        self.on_before_start = on_before_start
        self._running = False
        self._hw_connected = False
        self._waiting_device = False
        self._dev_name = ""
        self._dev_names = []
        self._mode = "guitar_wheel"
        self._stats = {"received": 0, "played": 0, "folded": 0, "dropped": 0}
        self._wheel = {"page": 0, "sector": 0, "fret": 1}

    def start(self, device_id_or_name=None, instrument_mode="guitar_wheel"):
        if self.on_before_start:
            self.on_before_start()
        self._mode = instrument_mode
        self._running = True
        if isinstance(device_id_or_name, str) and device_id_or_name.strip().lower() in ("all", "auto", "*"):
            devs = _list_midi_inputs()
            self._dev_names = [str(d.get("name") or f"MIDI #{d.get('id', 0)}") for d in devs]
            self._hw_connected = bool(self._dev_names)
            self._waiting_device = not self._hw_connected
            self._dev_name = " + ".join(self._dev_names)
            if self.on_status:
                if self._hw_connected:
                    self.on_status({
                        "kind": "started",
                        "running": True,
                        "hardware_connected": True,
                        "devices": list(self._dev_names),
                        "text": f"实时 MIDI 已连接：{self._dev_name}",
                    })
                else:
                    self.on_status({
                        "kind": "waiting_device",
                        "running": True,
                        "hardware_connected": False,
                        "devices": [],
                        "text": "等待插入 MIDI 键盘（即插即用）",
                    })
            return True
        has_hw = device_id_or_name is not None and str(device_id_or_name).strip() != ""
        self._hw_connected = has_hw
        self._waiting_device = False
        self._dev_name = f"MIDI #{device_id_or_name}" if has_hw else ""
        self._dev_names = [self._dev_name] if has_hw else []
        if self.on_status:
            self.on_status({
                "kind": "started",
                "running": True,
                "hardware_connected": has_hw,
                "devices": list(self._dev_names),
                "text": f"预览直弹：{self._dev_name or '虚拟试弹'}",
            })
        return True

    def stop(self):
        self._running = False
        self._hw_connected = False
        self._waiting_device = False
        self._dev_name = ""
        self._dev_names = []
        if self.on_status:
            self.on_status({"kind": "stopped", "running": False, "hardware_connected": False, "devices": []})

    def is_running(self):
        return self._running

    def is_hardware_connected(self):
        return bool(self._running and self._hw_connected)

    def connected_device_names(self):
        return list(self._dev_names) if self.is_hardware_connected() else []

    def connected_device_name(self):
        names = self.connected_device_names()
        return " + ".join(names) if names else ""

    def check_game_bridge_status(self):
        return {
            "found": False,
            "foreground": False,
            "is_admin": False,
            "game_elevated": None,
            "uipi_blocked": False,
            "reason": "未检测到游戏窗口（仅本地预览）",
        }

    def update_config(self, partial):
        if partial:
            self.config.update(partial)

    def set_instrument_mode(self, mode):
        self._mode = mode

    def reset_wheel_state(self, page=0, sector=0, fret=1):
        self._wheel = {"page": page, "sector": sector, "fret": fret}

    def inject_note_on(self, pitch, vel=100, channel=0):
        p = int(pitch)
        sp = p + int(self.config.get("transpose", 0))
        info = _fallback_describe_mapping(self._mode, self.config.get("wheel_submode", "fret"),
                                          0, bool(self.config.get("single_key_chord", False)))
        lo, hi = info["range"]
        in_range = (lo <= sp <= hi)
        folded = False
        target = sp
        if not in_range and self.config.get("out_of_range", "fold") == "fold":
            while target < lo:
                target += 12
            while target > hi:
                target -= 12
            folded = (lo <= target <= hi)
        self._stats["received"] += 1
        if in_range or folded:
            self._stats["played"] += 1
        if folded:
            self._stats["folded"] += 1
        lbl = info["key_labels"].get(target, "")
        if self.on_event:
            self.on_event({
                "type": "note_on",
                "pitch": p,
                "mapped_pitch": target,
                "vel": vel,
                "channel": channel,
                "in_range": in_range,
                "folded": folded,
                "dropped": not (in_range or folded),
                "label": lbl,
                "detail": lbl or "越界忽略",
                "injected": False,
            })

    def inject_note_off(self, pitch, channel=0):
        if self.on_event:
            self.on_event({"type": "note_off", "pitch": int(pitch), "channel": channel, "injected": False})

    def inject_cc(self, cc, val, channel=0):
        pass

    def release_all(self):
        pass

    def get_stats(self):
        return dict(self._stats)

    def get_wheel_state(self):
        return dict(self._wheel)


def _get_default_live_config() -> dict:
    if ML is not None and hasattr(ML, "DEFAULT_LIVE_CONFIG"):
        return dict(ML.DEFAULT_LIVE_CONFIG)
    return dict(_FALLBACK_CONFIG)


def _sanitize_dev_name(name: str, max_len: int = 36) -> str:
    if ML is not None and hasattr(ML, "sanitize_device_name"):
        try:
            return ML.sanitize_device_name(name, max_len=max_len)
        except Exception:
            pass
    import re as _re
    cleaned = _re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(name or ""))
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def _list_midi_inputs() -> list[dict]:
    if ML is not None and hasattr(ML, "list_midi_inputs"):
        try:
            return list(ML.list_midi_inputs() or [])
        except Exception as exc:
            _log_debug("list_midi_inputs failed: %s", exc)
    return []


def _describe_mode_mapping(instrument_mode: str, wheel_submode: str = "fret",
                           transpose: int = 0, single_key_chord: bool = False) -> dict:
    if ML is not None and hasattr(ML, "describe_mode_mapping"):
        try:
            res = ML.describe_mode_mapping(
                instrument_mode,
                wheel_submode=wheel_submode,
                transpose=transpose,
                single_key_chord=single_key_chord,
            )
            if isinstance(res, dict) and "range" in res:
                return res
        except Exception as exc:
            _log_debug("describe_mode_mapping failed: %s", exc)
    return _fallback_describe_mapping(instrument_mode, wheel_submode, transpose, single_key_chord)


def _create_live_engine(**kwargs):
    if ML is not None and hasattr(ML, "LiveMidiEngine"):
        return ML.LiveMidiEngine(**kwargs)
    return _FallbackLiveEngine(**kwargs)


# ══════════════════════════════════════════════════════════════════════════
#  交互式全规格可视化钢琴键盘条（25 / 37 / 49 / 61 / 76 / 88 键）
# ══════════════════════════════════════════════════════════════════════════
class LiveKeyboardStrip(QWidget):
    """自绘多规格（25/37/49/61/76/88 键）交互式钢琴键盘，支持高亮映射与鼠标试弹。"""

    notePressed = pyqtSignal(int, int)   # pitch, velocity
    noteReleased = pyqtSignal(int)       # pitch

    LOW_PITCH = 36   # 默认 61 键 C2
    HIGH_PITCH = 96  # 默认 61 键 C7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme_mode = "dark"
        self._instrument_mode = "guitar_wheel"
        self._keyboard_size = 61
        self._low_pitch = self.LOW_PITCH
        self._high_pitch = self.HIGH_PITCH
        self._valid_range = (40, 88)
        self._key_labels: dict[int, str] = {}
        self._transpose = 0
        self._out_of_range = "fold"
        self._active_notes: dict[int, dict] = {}
        self._mapped_targets: set[int] = set()
        self._mouse_pitch: int | None = None
        self._key_rects: list[tuple[QRectF, int, bool]] = []  # (rect, pitch, is_black)

        self.setMouseTracking(True)
        self.setMinimumSize(360, 108)
        self.setMaximumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_keyboard_size(self, key_count: int) -> None:
        try:
            kc = int(key_count)
        except (TypeError, ValueError):
            kc = 61
        if kc not in KEYBOARD_SPECS:
            kc = 61
        lo, hi, _title = KEYBOARD_SPECS[kc]
        self._keyboard_size = kc
        self._low_pitch = lo
        self._high_pitch = hi
        self.update()

    def set_theme_mode(self, mode: str) -> None:
        self._theme_mode = "light" if str(mode).lower() == "light" else "dark"
        self.update()

    def set_mapping(self, instrument_mode: str, valid_range: tuple[int, int],
                    key_labels: dict[int, str], transpose: int = 0,
                    out_of_range: str = "fold") -> None:
        self._instrument_mode = str(instrument_mode or "guitar_wheel")
        self._valid_range = (int(valid_range[0]), int(valid_range[1]))
        self._key_labels = {int(k): str(v) for k, v in (key_labels or {}).items()}
        self._transpose = int(transpose or 0)
        self._out_of_range = str(out_of_range or "fold").lower()
        self.update()

    def set_note_active(self, pitch: int, active: bool, info: dict | None = None) -> None:
        p = int(pitch)
        if active:
            self._active_notes[p] = dict(info or {})
        else:
            self._active_notes.pop(p, None)
        self._recompute_targets()
        self.update()

    def clear_active_notes(self) -> None:
        if self._active_notes or self._mapped_targets:
            self._active_notes.clear()
            self._mapped_targets.clear()
            self.update()

    def _recompute_targets(self) -> None:
        targets: set[int] = set()
        for info in self._active_notes.values():
            mp = info.get("pitch") if "raw_pitch" in info else info.get("mapped_pitch", info.get("pitch"))
            if mp is not None and not info.get("dropped"):
                targets.add(int(mp))
        self._mapped_targets = targets

    def _white_pitches(self) -> list[int]:
        return [p for p in range(self._low_pitch, self._high_pitch + 1) if (p % 12) not in _BLACK_PCS]

    def _build_geometry(self) -> tuple[list[tuple[QRectF, int]], list[tuple[QRectF, int]]]:
        w = float(self.width())
        h = float(self.height())
        pad_x = 8.0
        top_y = 20.0
        bot_pad = 6.0
        kb_w = max(120.0, w - pad_x * 2.0)
        kb_h = max(80.0, h - top_y - bot_pad)

        whites = self._white_pitches()
        ww = kb_w / float(max(1, len(whites)))
        bw = ww * 0.62
        bh = kb_h * 0.58

        white_rects: list[tuple[QRectF, int]] = []
        white_x_by_pitch: dict[int, float] = {}
        for i, p in enumerate(whites):
            x = pad_x + i * ww
            white_rects.append((QRectF(x, top_y, ww - 1.0, kb_h), p))
            white_x_by_pitch[p] = x

        black_rects: list[tuple[QRectF, int]] = []
        for p in range(self._low_pitch, self._high_pitch + 1):
            if (p % 12) in _BLACK_PCS:
                prev_wx = white_x_by_pitch.get(p - 1)
                if prev_wx is not None:
                    bx = prev_wx + ww - bw * 0.52
                    black_rects.append((QRectF(bx, top_y, bw, bh), p))
        return white_rects, black_rects

    def _label_info_for_raw_pitch(self, raw_p: int) -> tuple[str, bool, int | None]:
        """返回 (键帽文本, 是否折叠映射, 目标物理音高)。"""
        sp = raw_p + self._transpose
        if sp in self._key_labels:
            return self._key_labels[sp], False, sp
        if self._out_of_range not in ("ignore", "drop"):
            lo, hi = self._valid_range
            if KL is not None and hasattr(KL, "fold_pitch"):
                fp = KL.fold_pitch(sp, lo, hi)
            else:
                fp = sp
                while fp < lo:
                    fp += 12
                while fp > hi:
                    fp -= 12
                fp = max(lo, min(hi, fp))
            lbl = self._key_labels.get(fp, "")
            if lbl:
                return lbl, True, fp
        return "", False, None

    def _label_for_raw_pitch(self, raw_p: int) -> str:
        """查询某 MIDI 键在当前移调下对应的目标按键字符。"""
        lbl, _is_fold, _fp = self._label_info_for_raw_pitch(raw_p)
        return lbl

    @staticmethod
    def _compact_label(lbl: str, is_black: bool, rw: float) -> str:
        if "p·" in lbl or "弦" in lbl:
            disp = lbl.replace("弦", "").replace("p·", "p" if is_black else "·")
            max_ch = 3 if is_black or rw < 14.0 else 4
            return disp[:max_ch]
        # 和弦名（如 Cmaj7 / G#dim）或 36 键键帽尽量完整保留，不强截断后缀
        if is_black:
            return lbl[:5] if len(lbl) > 5 else lbl
        return lbl[:6] if rw < 14.0 and len(lbl) > 6 else lbl

    def _is_mapped_target_kb_pitch(self, kb_pitch: int) -> bool:
        """判定琴键位置 kb_pitch（在当前键盘坐标系下对应 kb_pitch + transpose）是否为正在触发的目标音。"""
        return (kb_pitch + self._transpose) in self._mapped_targets

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        dark = _is_dark_theme(self._theme_mode, self)
        w, h = float(self.width()), float(self.height())
        outer = QRectF(0.5, 0.5, max(10.0, w - 1.0), max(10.0, h - 1.0))

        bg_col = QColor("#0B1020") if dark else QColor("#EDF2FC")
        bd_col = QColor("#232C47") if dark else QColor("#C7D6EE")
        p.setBrush(bg_col)
        p.setPen(QPen(bd_col, 1.1))
        p.drawRoundedRect(outer, 10.0, 10.0)

        white_rects, black_rects = self._build_geometry()
        # 命中检测顺序：先黑键后白键
        self._key_rects = [(r, pt, True) for r, pt in black_rects] + [(r, pt, False) for r, pt in white_rects]

        lo, hi = self._valid_range
        # 计算移调后键盘上对应有效物理区间（raw_p + transpose in [lo, hi]）
        raw_lo = lo - self._transpose
        raw_hi = hi - self._transpose

        # 顶部音域指示带
        all_rect_by_p = {pt: r for r, pt in white_rects}
        all_rect_by_p.update({pt: r for r, pt in black_rects})
        vis_in = [r for pt, r in all_rect_by_p.items() if raw_lo <= pt <= raw_hi]
        if vis_in:
            x1 = min(r.left() for r in vis_in)
            x2 = max(r.right() for r in vis_in)
            band = QRectF(x1, 4.0, max(10.0, x2 - x1), 13.0)
            p.setBrush(QColor(59, 110, 245, 58) if dark else QColor(47, 92, 229, 32))
            p.setPen(QPen(QColor("#38BDF8") if dark else QColor("#2F5CE5"), 1.0))
            p.drawRoundedRect(band, 4.0, 4.0)
            p.setPen(QColor("#E6EDF7") if dark else QColor("#1E3A8A"))
            p.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
            rng_txt = f"有效直弹区：{_midi_name(lo)}({lo}) ~ {_midi_name(hi)}({hi})"
            if self._transpose != 0:
                rng_txt += f"  [移调 {self._transpose:+d}]"
            p.drawText(band, Qt.AlignCenter, rng_txt)

        # 绘制白键
        for rect, pt in white_rects:
            in_valid = (raw_lo <= pt <= raw_hi)
            act = self._active_notes.get(pt)
            is_target = self._is_mapped_target_kb_pitch(pt)
            if act is not None:
                if act.get("dropped"):
                    fill = QColor("#F43F5E")
                elif act.get("folded"):
                    fill = QColor("#8B5CF6")
                else:
                    fill = QColor("#F4C869") if dark else QColor("#3B6EF5")
                bd = fill
            elif is_target:
                fill = QColor(56, 189, 248, 130) if dark else QColor(147, 197, 253, 180)
                bd = QColor("#38BDF8") if dark else QColor("#2F5CE5")
            elif in_valid:
                fill = QColor("#E8EFFB") if dark else QColor("#FFFFFF")
                bd = QColor("#4A5D88") if dark else QColor("#B9C9E4")
            else:
                fill = QColor("#6C7893") if dark else QColor("#DDE5F3")
                bd = QColor("#2D3856") if dark else QColor("#C7D4EA")

            p.setBrush(fill)
            p.setPen(QPen(bd, 1.3 if (act or in_valid) else 0.8))
            p.drawRoundedRect(rect, 3.5, 3.5)

            rw = rect.width()
            # C 音八度标（88 键宽较窄时自适应更小字号）
            if pt % 12 == 0 and rw >= 10.0:
                p.setPen(QColor("#0F172A") if (act is None and in_valid) else
                         (QColor("#FFFFFF") if act else QColor("#475569")))
                c_pt = 7 if rw >= 18.0 else (6 if rw >= 13.0 else 5)
                p.setFont(QFont("Consolas", c_pt, QFont.Bold))
                p.drawText(QRectF(rect.x(), rect.bottom() - 15, rw, 13),
                           Qt.AlignCenter, _midi_name(pt))

            # 游戏键位映射标（88 键窄键宽下仍保留清晰键帽，fold 模式下区外键淡示折叠目标）
            lbl, is_fold_lbl, _fp = self._label_info_for_raw_pitch(pt)
            if lbl and rw >= 10.0:
                disp = self._compact_label(lbl, False, rw)
                if act is not None:
                    txt_col = QColor("#090C15") if dark else QColor("#FFFFFF")
                elif is_fold_lbl:
                    txt_col = QColor("#C4B5FD") if dark else QColor("#5B21B6")
                elif in_valid:
                    txt_col = QColor("#1D4ED8")
                else:
                    txt_col = QColor("#475569")
                p.setPen(txt_col)
                if rw >= 18.0:
                    f_sz = 7 if len(disp) <= 2 else (6 if len(disp) <= 4 else 5)
                elif rw >= 13.0:
                    f_sz = 5 if len(disp) <= 3 else 4
                else:
                    f_sz = 4
                p.setFont(QFont("Consolas", f_sz, QFont.Bold))
                y_off = rect.bottom() - (28.0 if pt % 12 == 0 else 19.0)
                p.drawText(QRectF(rect.x() + 0.5, y_off, rw - 1.0, 13.0),
                           Qt.AlignCenter, disp)

        # 绘制黑键
        for rect, pt in black_rects:
            in_valid = (raw_lo <= pt <= raw_hi)
            act = self._active_notes.get(pt)
            is_target = self._is_mapped_target_kb_pitch(pt)
            if act is not None:
                if act.get("dropped"):
                    fill = QColor("#F43F5E")
                elif act.get("folded"):
                    fill = QColor("#A78BFA")
                else:
                    fill = QColor("#38BDF8") if dark else QColor("#2F5CE5")
                bd = QColor("#F4C869")
            elif is_target:
                fill = QColor("#1E40AF")
                bd = QColor("#38BDF8")
            elif in_valid:
                fill = QColor("#1B233A") if dark else QColor("#1E293B")
                bd = QColor("#3B4D78") if dark else QColor("#0F172A")
            else:
                fill = QColor("#101524") if dark else QColor("#475569")
                bd = QColor("#1E263D") if dark else QColor("#334155")

            p.setBrush(fill)
            p.setPen(QPen(bd, 1.1))
            p.drawRoundedRect(rect, 3.0, 3.0)

            lbl, is_fold_lbl, _fp = self._label_info_for_raw_pitch(pt)
            if lbl and rect.width() >= 7.5:
                disp = self._compact_label(lbl, True, rect.width())
                if act or in_valid:
                    txt_col = QColor("#FFFFFF")
                elif is_fold_lbl:
                    txt_col = QColor("#C4B5FD")
                else:
                    txt_col = QColor("#94A3B8")
                p.setPen(txt_col)
                bf_sz = 6 if (rect.width() >= 11.0 and len(disp) <= 3) else (5 if rect.width() >= 9.5 else 4)
                p.setFont(QFont("Consolas", bf_sz, QFont.Bold))
                p.drawText(QRectF(rect.x(), rect.bottom() - 15, rect.width(), 13),
                           Qt.AlignCenter, disp)

        # 键外音提示角标（例如用 25 键窗口但硬件敲了更低或更高音）
        out_lo = [pt for pt in self._active_notes if pt < self._low_pitch]
        out_hi = [pt for pt in self._active_notes if pt > self._high_pitch]
        if out_lo or out_hi:
            p.setFont(QFont("Microsoft YaHei UI", 7, QFont.Bold))
            p.setPen(QColor("#F4C869") if dark else QColor("#1D4ED8"))
            if out_lo:
                p.drawText(QRectF(10.0, 4.0, 120.0, 14.0), Qt.AlignLeft | Qt.AlignVCenter,
                           f"◀ 低音 {_midi_name(min(out_lo))}")
            if out_hi:
                p.drawText(QRectF(w - 130.0, 4.0, 120.0, 14.0), Qt.AlignRight | Qt.AlignVCenter,
                           f"高音 {_midi_name(max(out_hi))} ▶")

        p.end()

    def _hit_pitch(self, pos) -> int | None:
        pt = QPointF(pos)
        for rect, pitch, _is_black in self._key_rects:
            if rect.contains(pt):
                return pitch
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pitch = self._hit_pitch(event.pos())
            if pitch is not None:
                self._mouse_pitch = pitch
                self.notePressed.emit(pitch, 100)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pitch = self._hit_pitch(event.pos())
        if event.buttons() & Qt.LeftButton:
            if pitch != self._mouse_pitch:
                if self._mouse_pitch is not None:
                    self.noteReleased.emit(self._mouse_pitch)
                self._mouse_pitch = pitch
                if pitch is not None:
                    self.notePressed.emit(pitch, 100)
        if pitch is not None:
            sp = pitch + self._transpose
            lbl, is_fold, fp = self._label_info_for_raw_pitch(pitch)
            tip = f"MIDI {_midi_name(pitch)} ({pitch})"
            if self._transpose:
                tip += f" → 移调后 {_midi_name(sp)} ({sp})"
            if is_fold and fp is not None:
                tip += f" → 折叠至 {_midi_name(fp)} ({fp})"
            if lbl:
                tip += f" · 映射按键 {lbl}"
            self.setToolTip(tip)
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._mouse_pitch is not None:
            self.noteReleased.emit(self._mouse_pitch)
            self._mouse_pitch = None
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._mouse_pitch is not None:
            self.noteReleased.emit(self._mouse_pitch)
            self._mouse_pitch = None
        super().leaveEvent(event)


# ══════════════════════════════════════════════════════════════════════════
#  实时 MIDI 键盘直弹主弹窗
# ══════════════════════════════════════════════════════════════════════════
class MidiLiveDialog(QDialog):
    """星曜双主题实时 MIDI 键盘直弹窗口（支持三乐器与鼠标/外接键盘联动）。"""

    sig_live_event = pyqtSignal(dict)
    sig_live_status = pyqtSignal(dict)

    def __init__(self, instrument_mode: str = "guitar_wheel", io=None,
                 on_mode_changed=None, on_before_start=None, parent=None,
                 embedded: bool = False, on_back=None):
        super().__init__(parent)
        self._embedded = bool(embedded)
        self._on_back = on_back
        self._mode = str(instrument_mode or "guitar_wheel")
        if self._mode not in ("guitar_wheel", "guitar_36", "piano_36"):
            self._mode = "guitar_wheel"
        self.io = io
        self.on_mode_changed = on_mode_changed
        self.on_before_start = on_before_start
        self._theme_mode = "dark" if _is_dark_theme(None, self) else "light"
        self._cfg = self._load_saved_config()

        self.setWindowTitle("实时 MIDI 键盘直弹")
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setMinimumSize(0, 0)
        else:
            self.setModal(False)
            if CU is not None and hasattr(CU, "fit_screen"):
                CU.fit_screen(self, 1060, 700, min_w=780, min_h=560)
            else:
                self.resize(1040, 680)
            try:
                avail = QApplication.desktop().availableGeometry(self)
                self.setMinimumSize(min(780, int(avail.width() * 0.88)),
                                    min(560, int(avail.height() * 0.88)))
            except Exception:
                self.setMinimumSize(780, 560)

        self.sig_live_event.connect(self._on_engine_event)
        self.sig_live_status.connect(self._on_engine_status)

        self.engine = _create_live_engine(
            io=self.io,
            config=self._cfg,
            on_event=self._emit_event_safe,
            on_status=self._emit_status_safe,
            on_before_start=self._call_before_start,
        )

        self._build_ui()
        self._bridge_state_key = None
        self._user_manually_stopped = False
        self._hotplug_listening = False
        self._last_device_names = []
        self._bridge_timer = QTimer(self)
        self._bridge_timer.setInterval(900)
        self._bridge_timer.timeout.connect(self._poll_game_bridge)
        self._hotplug_timer = QTimer(self)
        self._hotplug_timer.setInterval(1500)
        self._hotplug_timer.timeout.connect(self._poll_device_hotplug)
        self._load_cfg_to_ui()
        self.refresh_devices()
        self._sync_mode_ui()
        self.sync_theme(self._theme_mode)

    def _emit_event_safe(self, payload: dict) -> None:
        try:
            self.sig_live_event.emit(dict(payload or {}))
        except Exception:
            pass

    def _emit_status_safe(self, payload: dict) -> None:
        try:
            self.sig_live_status.emit(dict(payload or {}))
        except Exception:
            pass

    def _call_before_start(self) -> None:
        if callable(self.on_before_start):
            try:
                self.on_before_start()
            except Exception as exc:
                _log_debug("on_before_start failed: %s", exc)

    def _load_saved_config(self) -> dict:
        cfg = _get_default_live_config()
        if PC is not None and hasattr(PC, "load"):
            try:
                all_cfg = PC.load()
                saved = all_cfg.get("midi_live")
                if isinstance(saved, dict):
                    cfg.update(saved)
            except Exception as exc:
                _log_debug("load midi_live config failed: %s", exc)
        return cfg

    def _save_config(self) -> None:
        if PC is not None and hasattr(PC, "save"):
            try:
                PC.save({"midi_live": dict(self._cfg)})
            except Exception as exc:
                _log_debug("save midi_live config failed: %s", exc)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ── 顶部连接与乐器模式卡片 ─────────────────────────────────────
        top_card = _make_card("card")
        tv = QVBoxLayout(top_card)
        tv.setContentsMargins(12, 7, 12, 7)
        tv.setSpacing(5)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        lbl_dev = QLabel("MIDI 设备")
        lbl_dev.setObjectName("fieldname")
        row1.addWidget(lbl_dev)

        self.cmb_device = QComboBox()
        self.cmb_device.setMinimumWidth(160)
        self.cmb_device.setToolTip("实时扫描本机连接的 USB/虚拟 MIDI 键盘，默认自动连接并支持热拔插")
        self.cmb_device.currentIndexChanged.connect(self._on_device_changed)
        row1.addWidget(self.cmb_device, 1)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("ghost")
        self.btn_refresh.setIconSize(QSize(15, 15))
        self.btn_refresh.clicked.connect(self.refresh_devices)
        row1.addWidget(self.btn_refresh)

        self.lbl_state = QLabel("等待插入 MIDI 键盘（支持热拔插）")
        self.lbl_state.setObjectName("accentPill")
        self.lbl_state.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.lbl_state.setMaximumHeight(30)
        self.lbl_state.setMaximumWidth(280)
        row1.addWidget(self.lbl_state, 0, Qt.AlignVCenter)
        tv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        lbl_m = QLabel("演奏乐器")
        lbl_m.setObjectName("fieldname")
        row2.addWidget(lbl_m)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self.btn_mode_wheel = QPushButton("吉他 · 轮盘")
        self.btn_mode_g36 = QPushButton("吉他 · 36键")
        self.btn_mode_p36 = QPushButton("钢琴 · 36键")
        for btn, mode in (
            (self.btn_mode_wheel, "guitar_wheel"),
            (self.btn_mode_g36, "guitar_36"),
            (self.btn_mode_p36, "piano_36"),
        ):
            btn.setObjectName("modePill")
            btn.setCheckable(True)
            btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _=False, m=mode: self._user_select_mode(m))
            self._mode_group.addButton(btn)
            row2.addWidget(btn)

        row2.addStretch(1)
        self.lbl_game_bridge = QLabel("未检测到游戏 · 仅预览")
        self.lbl_game_bridge.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.lbl_game_bridge.setMaximumHeight(30)
        row2.addWidget(self.lbl_game_bridge, 0, Qt.AlignVCenter)

        self.lbl_mode_hint = QLabel("")
        self.lbl_mode_hint.setObjectName("goldPill")
        self.lbl_mode_hint.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.lbl_mode_hint.setMaximumHeight(30)
        row2.addWidget(self.lbl_mode_hint, 0, Qt.AlignVCenter)
        tv.addLayout(row2)
        root.addWidget(top_card)

        # ── 中部参数与轮盘控制卡片 ─────────────────────────────────────
        mid_card = _make_card("card")
        mv = QVBoxLayout(mid_card)
        mv.setContentsMargins(12, 8, 12, 8)
        mv.setSpacing(6)

        cfg_r1 = QHBoxLayout()
        cfg_r1.setSpacing(5)
        cfg_r1.addWidget(QLabel("键盘"))
        self.cmb_kb_size = QComboBox()
        for kc in (25, 37, 49, 61, 76, 88):
            self.cmb_kb_size.addItem(KEYBOARD_SPECS[kc][2], kc)
        self.cmb_kb_size.setMinimumWidth(104)
        self.cmb_kb_size.setMaximumWidth(134)
        self.cmb_kb_size.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.cmb_kb_size.setToolTip("选择你手中的 MIDI 键盘键数，下方琴键与提示区会按对应规格自适应")
        self.cmb_kb_size.currentIndexChanged.connect(self._on_cfg_changed)
        cfg_r1.addWidget(self.cmb_kb_size)

        cfg_r1.addWidget(QLabel("移调"))
        self.spin_transpose = QSpinBox()
        self.spin_transpose.setRange(-24, 24)
        self.spin_transpose.setSuffix(" 半音")
        self.spin_transpose.setFixedWidth(74)
        self.spin_transpose.valueChanged.connect(self._on_cfg_changed)
        cfg_r1.addWidget(self.spin_transpose)

        for delta, txt in ((-12, "-12"), (0, "0"), (12, "+12")):
            qb = QPushButton(txt)
            qb.setObjectName("ghost")
            qb.setFixedWidth(36)
            qb.setStyleSheet("padding: 2px 4px;")
            qb.clicked.connect(lambda _=False, d=delta: self._shift_transpose(d))
            cfg_r1.addWidget(qb)

        cfg_r1.addWidget(QLabel("越界"))
        self.cmb_oor = QComboBox()
        self.cmb_oor.addItem("自动八度折叠", "fold")
        self.cmb_oor.addItem("严格忽略", "ignore")
        self.cmb_oor.setMinimumWidth(104)
        self.cmb_oor.setMaximumWidth(132)
        self.cmb_oor.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.cmb_oor.currentIndexChanged.connect(self._on_cfg_changed)
        cfg_r1.addWidget(self.cmb_oor)
        cfg_r1.addStretch(1)
        mv.addLayout(cfg_r1)

        cfg_r2 = QHBoxLayout()
        cfg_r2.setSpacing(10)
        self.chk_pedal = QCheckBox("CC64 延音踏板")
        self.chk_pedal.toggled.connect(self._on_cfg_changed)
        cfg_r2.addWidget(self.chk_pedal)

        self.chk_focus_guard = QCheckBox("仅游戏前台发键")
        self.chk_focus_guard.setToolTip("开启焦点守卫：仅当游戏窗口处于前台时实际发键，切出窗口自动松键仅界面预览")
        self.chk_focus_guard.toggled.connect(self._on_cfg_changed)
        cfg_r2.addWidget(self.chk_focus_guard)

        cfg_r2.addStretch(1)
        self.btn_adv_toggle = QPushButton("高级参数 ▼")
        self.btn_adv_toggle.setObjectName("ghost")
        self.btn_adv_toggle.setCheckable(True)
        self.btn_adv_toggle.setChecked(False)
        self.btn_adv_toggle.setToolTip("展开或收起 MIDI 通道筛选、Ch10 鼓轨过滤、合批窗与修饰提前等进阶参数")
        self.btn_adv_toggle.toggled.connect(self._toggle_advanced_panel)
        cfg_r2.addWidget(self.btn_adv_toggle)
        mv.addLayout(cfg_r2)

        # 可折叠进阶参数区（默认收起，保持主界面清爽）
        self.adv_panel = QWidget()
        adv_lay = QHBoxLayout(self.adv_panel)
        adv_lay.setContentsMargins(0, 2, 0, 2)
        adv_lay.setSpacing(6)

        adv_lay.addWidget(QLabel("MIDI 通道"))
        self.cmb_channel = QComboBox()
        self.cmb_channel.addItem("全部通道", 0)
        for ch in range(1, 17):
            self.cmb_channel.addItem(f"Ch {ch}", ch)
        self.cmb_channel.setFixedWidth(88)
        self.cmb_channel.setToolTip(
            "一根 MIDI 线可分 1~16 个逻辑轨。普通单键盘默认选「全部通道」即可；"
            "分键电钢或编曲软件多轨并发时，可在此指定只听某一个通道。"
        )
        self.cmb_channel.currentIndexChanged.connect(self._on_cfg_changed)
        adv_lay.addWidget(self.cmb_channel)

        self.chk_ignore_drums = QCheckBox("忽略 Ch10 鼓轨")
        self.chk_ignore_drums.setToolTip(
            "MIDI 行业标准约定第 10 通道专门传输电子鼓/打击乐，勾选可防止鼓点误触发游戏琴键。"
        )
        self.chk_ignore_drums.toggled.connect(self._on_cfg_changed)
        adv_lay.addWidget(self.chk_ignore_drums)

        adv_lay.addWidget(QLabel("合批窗"))
        self.spin_window = QDoubleSpinBox()
        self.spin_window.setRange(0.0, 35.0)
        self.spin_window.setSingleStep(1.0)
        self.spin_window.setDecimals(1)
        self.spin_window.setSuffix(" ms")
        self.spin_window.setFixedWidth(74)
        self.spin_window.setToolTip("微聚合同一瞬按下的和弦音符；吉他 36 键始终 0ms 直通")
        self.spin_window.valueChanged.connect(self._on_cfg_changed)
        adv_lay.addWidget(self.spin_window)

        adv_lay.addWidget(QLabel("修饰提前"))
        self.spin_mod_lead = QDoubleSpinBox()
        self.spin_mod_lead.setRange(1.0, 20.0)
        self.spin_mod_lead.setSingleStep(0.5)
        self.spin_mod_lead.setDecimals(1)
        self.spin_mod_lead.setValue(2.0)
        self.spin_mod_lead.setSuffix(" ms")
        self.spin_mod_lead.setFixedWidth(74)
        self.spin_mod_lead.setToolTip("按住 Shift/Ctrl 前后保护间隔，低帧率偶发漏变音可调至 5~8ms")
        self.spin_mod_lead.valueChanged.connect(self._on_cfg_changed)
        adv_lay.addWidget(self.spin_mod_lead)
        adv_lay.addStretch(1)
        self.adv_panel.setVisible(False)
        mv.addWidget(self.adv_panel)

        # 轮盘子模式控制行（仅 guitar_wheel 可见）
        self.wheel_row_widget = QWidget()
        wr = QHBoxLayout(self.wheel_row_widget)
        wr.setContentsMargins(0, 2, 0, 0)
        wr.setSpacing(5)
        lbl_w = QLabel("轮盘模式")
        lbl_w.setObjectName("fieldname")
        wr.addWidget(lbl_w)

        self._wheel_group = QButtonGroup(self)
        self._wheel_group.setExclusive(True)
        self.btn_wheel_fret = QPushButton("智能品格直弹")
        self.btn_wheel_chord = QPushButton("和弦轮盘速弹")
        for btn, sub in ((self.btn_wheel_fret, "fret"), (self.btn_wheel_chord, "chord")):
            btn.setObjectName("modePill")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, s=sub: self._set_wheel_submode(s))
            self._wheel_group.addButton(btn)
            wr.addWidget(btn)

        self.chk_single_chord = QCheckBox("单键 24 和弦")
        self.chk_single_chord.setToolTip("开启后 MIDI C3~B4 (48~71) 单键直触 4 页 × 6 扇区 24 个游戏和弦")
        self.chk_single_chord.toggled.connect(self._on_cfg_changed)
        wr.addWidget(self.chk_single_chord)

        self.btn_reset_wheel = QPushButton("重置轮盘")
        self.btn_reset_wheel.setToolTip("将软件内记录的轮盘位置重置至第 1 页 · 1 品")
        self.btn_reset_wheel.setObjectName("ghost")
        self.btn_reset_wheel.setIconSize(QSize(14, 14))
        self.btn_reset_wheel.clicked.connect(self._reset_wheel)
        wr.addWidget(self.btn_reset_wheel)

        wr.addStretch(1)
        self.lbl_wheel_state = QLabel("第 1 页 · 1 品")
        self.lbl_wheel_state.setObjectName("violetPill")
        self.lbl_wheel_state.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        wr.addWidget(self.lbl_wheel_state, 0, Qt.AlignVCenter)
        mv.addWidget(self.wheel_row_widget)

        root.addWidget(mid_card)

        # ── 底部演奏区卡片：轨迹条 + 61 键键盘条 + 事件流 ─────────────
        play_card = _make_card("card")
        pv = QVBoxLayout(play_card)
        pv.setContentsMargins(12, 8, 12, 8)
        pv.setSpacing(6)

        trace_row = QHBoxLayout()
        trace_row.setSpacing(8)
        self.lbl_trace = QLabel("等待 MIDI 输入或点击下方琴键试弹…")
        self.lbl_trace.setObjectName("sectionTitle")
        self.lbl_trace.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        trace_row.addWidget(self.lbl_trace, 1, Qt.AlignVCenter)

        self.lbl_stats = QLabel("收音 0 · 发声 0 · 折叠 0")
        self.lbl_stats.setObjectName("goldPill")
        self.lbl_stats.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        trace_row.addWidget(self.lbl_stats, 0, Qt.AlignVCenter)
        pv.addLayout(trace_row, 0)

        self.keyboard_strip = LiveKeyboardStrip(self)
        self.keyboard_strip.notePressed.connect(self._on_strip_note_on)
        self.keyboard_strip.noteReleased.connect(self._on_strip_note_off)
        pv.addWidget(self.keyboard_strip, 1)

        self.event_log = QPlainTextEdit()
        self.event_log.setObjectName("log")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(120)
        self.event_log.setMinimumHeight(36)
        self.event_log.setMaximumHeight(96)
        pv.addWidget(self.event_log, 0)
        root.addWidget(play_card, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_release = QPushButton("紧急全松键")
        self.btn_release.setObjectName("ghost")
        self.btn_release.clicked.connect(self._release_all_keys)
        foot.addWidget(self.btn_release)

        self.btn_close = QPushButton("← 返回演奏台" if self._embedded else "关闭")
        self.btn_close.setMinimumWidth(116 if self._embedded else 92)
        self.btn_close.setVisible(not self._embedded)
        self.btn_close.clicked.connect(self._handle_close_or_back)
        foot.addWidget(self.btn_close)
        root.addLayout(foot)

    def _handle_close_or_back(self) -> None:
        if self._embedded:
            if callable(self._on_back):
                self._on_back()
            return
        self.close()

    def reject(self) -> None:
        if self._embedded:
            self._handle_close_or_back()
            return
        if hasattr(self, "_bridge_timer") and self._bridge_timer.isActive():
            self._bridge_timer.stop()
        if hasattr(self, "_hotplug_timer") and self._hotplug_timer.isActive():
            self._hotplug_timer.stop()
        self.stop_if_running()
        super().reject()

    def keyPressEvent(self, event) -> None:
        if self._embedded and event.key() == Qt.Key_Escape:
            self._handle_close_or_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def _toggle_advanced_panel(self, checked: bool) -> None:
        self.adv_panel.setVisible(bool(checked))
        self.btn_adv_toggle.setText("收起高级 ▲" if checked else "高级参数 ▼")

    def _load_cfg_to_ui(self) -> None:
        widgets = (self.cmb_kb_size, self.cmb_channel, self.chk_ignore_drums, self.spin_transpose,
                   self.cmb_oor, self.chk_pedal, self.spin_window, self.spin_mod_lead,
                   self.chk_focus_guard, self.chk_single_chord)
        for w in widgets:
            w.blockSignals(True)
        kb_size = int(self._cfg.get("keyboard_size") or 61)
        kb_idx = self.cmb_kb_size.findData(kb_size)
        self.cmb_kb_size.setCurrentIndex(kb_idx if kb_idx >= 0 else 3)
        self.keyboard_strip.set_keyboard_size(int(self.cmb_kb_size.currentData() or 61))
        ch_idx = self.cmb_channel.findData(int(self._cfg.get("channel") or 0))
        self.cmb_channel.setCurrentIndex(ch_idx if ch_idx >= 0 else 0)
        self.chk_ignore_drums.setChecked(bool(self._cfg.get("ignore_drums", True)))
        self.spin_transpose.setValue(int(self._cfg.get("transpose") or 0))
        oor_idx = self.cmb_oor.findData(str(self._cfg.get("out_of_range") or "fold"))
        self.cmb_oor.setCurrentIndex(oor_idx if oor_idx >= 0 else 0)
        self.chk_pedal.setChecked(bool(self._cfg.get("sustain_pedal", True)))
        self.spin_window.setValue(float(self._cfg.get("chord_window_ms", 8.0)))
        self.spin_mod_lead.setValue(float(self._cfg.get("mod_lead_ms", 2.0)))
        self.chk_focus_guard.setChecked(bool(self._cfg.get("focus_guard", True)))
        self.chk_single_chord.setChecked(bool(self._cfg.get("single_key_chord", False)))
        sub = str(self._cfg.get("wheel_submode") or "fret")
        self.btn_wheel_fret.setChecked(sub != "chord")
        self.btn_wheel_chord.setChecked(sub == "chord")
        for w in widgets:
            w.blockSignals(False)

    def refresh_devices(self) -> None:
        devs = _list_midi_inputs()
        self._last_device_names = [str(item.get("name") or "") for item in devs]
        saved_name = str(self._cfg.get("device_name") or "").strip()
        self.cmb_device.blockSignals(True)
        self.cmb_device.clear()
        if len(devs) == 1:
            d0 = devs[0].get("display_name") or _sanitize_dev_name(devs[0].get("name"))
            auto_label = f"自动连接全部设备（已发现 {d0}）"
        elif len(devs) > 1:
            auto_label = f"自动连接全部设备（共 {len(devs)} 台）"
        else:
            auto_label = "自动连接（插上 MIDI 键盘即用）"
        self.cmb_device.addItem(auto_label, "all")
        for item in devs:
            disp = item.get("display_name") or _sanitize_dev_name(item.get("name"))
            self.cmb_device.addItem(f"{disp} (#{item['id']})", item["id"])
        selected = 0
        if saved_name and saved_name.lower() != "all":
            saved_disp = _sanitize_dev_name(saved_name)
            for i in range(1, self.cmb_device.count()):
                txt = self.cmb_device.itemText(i)
                item_disp = txt.rsplit(" (#", 1)[0]
                raw_n = devs[i - 1].get("name", "") if (i - 1) < len(devs) else ""
                if saved_name == raw_n or saved_disp == item_disp or saved_disp in txt:
                    selected = i
                    break
        self.cmb_device.setCurrentIndex(selected)
        self.cmb_device.blockSignals(False)

    def _on_device_changed(self) -> None:
        idx = self.cmb_device.currentIndex()
        data = self.cmb_device.itemData(idx)
        hw = bool(getattr(self.engine, "is_hardware_connected", lambda: False)())
        if data == "all":
            self._cfg["device_name"] = "all"
            self.engine.update_config({"device_name": "all"})
            self._save_config()
            if self.engine.is_running() and (hw or self._hotplug_listening):
                self._call_before_start()
                try:
                    self.engine.start(device_id_or_name="all", instrument_mode=self._mode)
                    self._hotplug_listening = True
                    self._append_log("▶ 已切换为自动连接全部 MIDI 设备（支持热拔插）")
                except Exception as exc:
                    QMessageBox.warning(self, "切换设备失败", f"无法开启自动连接监听：\n{exc}")
                    self._append_log(f"× 切换设备失败：{exc}")
                self._sync_running_ui()
            return
        if data is not None and int(data) >= 0:
            txt = self.cmb_device.currentText()
            new_name = txt.rsplit(" (#", 1)[0]
            self._cfg["device_name"] = new_name
            self.engine.update_config({"device_name": new_name})
            self._save_config()
            if self.engine.is_running() and (hw or self._hotplug_listening):
                self._call_before_start()
                try:
                    self.engine.start(device_id_or_name=int(data), instrument_mode=self._mode)
                    self._hotplug_listening = True
                    self._append_log(f"▶ 已切换并重连 MIDI 设备：{txt}")
                except Exception as exc:
                    QMessageBox.warning(self, "切换设备失败", f"无法打开 MIDI 输入设备：\n{exc}")
                    self._append_log(f"× 切换设备失败：{exc}")
                self._sync_running_ui()

    def _shift_transpose(self, delta: int) -> None:
        if delta == 0:
            self.spin_transpose.setValue(0)
        else:
            self.spin_transpose.setValue(max(-24, min(24, self.spin_transpose.value() + delta)))

    def _set_wheel_submode(self, sub: str) -> None:
        self._cfg["wheel_submode"] = "chord" if sub == "chord" else "fret"
        self.engine.update_config({"wheel_submode": self._cfg["wheel_submode"]})
        self._save_config()
        self._sync_mode_ui()

    def _on_cfg_changed(self) -> None:
        kb_size = int(self.cmb_kb_size.currentData() or 61)
        self._cfg.update({
            "keyboard_size": kb_size,
            "channel": int(self.cmb_channel.currentData() or 0),
            "ignore_drums": self.chk_ignore_drums.isChecked(),
            "transpose": int(self.spin_transpose.value()),
            "out_of_range": str(self.cmb_oor.currentData() or "fold"),
            "sustain_pedal": self.chk_pedal.isChecked(),
            "chord_window_ms": float(self.spin_window.value()),
            "mod_lead_ms": float(self.spin_mod_lead.value()),
            "focus_guard": self.chk_focus_guard.isChecked(),
            "auto_connect": True,
            "single_key_chord": self.chk_single_chord.isChecked(),
        })
        self.keyboard_strip.set_keyboard_size(kb_size)
        self.engine.update_config(self._cfg)
        self._save_config()
        self._sync_mode_ui()

    def _user_select_mode(self, mode: str) -> None:
        self.set_instrument_mode(mode)
        if callable(self.on_mode_changed):
            try:
                self.on_mode_changed(self._mode)
            except Exception as exc:
                _log_debug("on_mode_changed failed: %s", exc)

    def set_instrument_mode(self, mode: str) -> None:
        norm = str(mode or "guitar_wheel")
        if norm not in ("guitar_wheel", "guitar_36", "piano_36"):
            norm = "guitar_wheel"
        if norm != self._mode:
            self._mode = norm
            self.keyboard_strip.clear_active_notes()
            try:
                self.engine.set_instrument_mode(self._mode)
            except Exception as exc:
                _log_debug("engine.set_instrument_mode failed: %s", exc)
        self._sync_mode_ui()

    def _sync_mode_ui(self) -> None:
        for btn, m in ((self.btn_mode_wheel, "guitar_wheel"),
                       (self.btn_mode_g36, "guitar_36"),
                       (self.btn_mode_p36, "piano_36")):
            btn.blockSignals(True)
            btn.setChecked(self._mode == m)
            btn.blockSignals(False)

        is_wheel = (self._mode == "guitar_wheel")
        is_piano = (self._mode == "piano_36")
        self.wheel_row_widget.setVisible(is_wheel)
        sub = str(self._cfg.get("wheel_submode") or "fret")
        self.chk_single_chord.setEnabled(is_wheel and sub == "chord")
        self.chk_pedal.setEnabled(not is_piano)
        self.chk_pedal.setText("CC64 踏板（钢琴无延音）" if is_piano else "CC64 延音踏板")
        self.spin_mod_lead.setEnabled(is_piano)

        info = _describe_mode_mapping(
            self._mode,
            wheel_submode=sub,
            transpose=0,
            single_key_chord=bool(self._cfg.get("single_key_chord", False)),
        )
        lo, hi = info.get("range", (40, 88))
        short_hint = {
            "guitar_wheel": f"{_midi_name(lo)}~{_midi_name(hi)} · 轮盘",
            "guitar_36": f"{_midi_name(lo)}~{_midi_name(hi)} · 有延音",
            "piano_36": f"{_midi_name(lo)}~{_midi_name(hi)} · 无延音",
        }.get(self._mode, f"{_midi_name(lo)}~{_midi_name(hi)}")
        self.lbl_mode_hint.setText(short_hint)
        self.lbl_mode_hint.setToolTip(info.get("hint") or info.get("title") or short_hint)
        self.keyboard_strip.set_mapping(
            self._mode,
            info.get("range", (40, 88)),
            info.get("key_labels", {}),
            transpose=int(self._cfg.get("transpose") or 0),
            out_of_range=str(self._cfg.get("out_of_range") or "fold"),
        )
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        dark = _is_dark_theme(self._theme_mode, self)
        dim = "#CBD5E1" if dark else "#334155"
        self.btn_refresh.setIcon(_make_icon("refresh", dim, 15))
        self.btn_reset_wheel.setIcon(_make_icon("refresh", dim, 14))
        self.btn_mode_wheel.setIcon(_make_icon("wheel", "#FFFFFF" if self.btn_mode_wheel.isChecked() else dim, 15))
        self.btn_mode_g36.setIcon(_make_icon("guitar", "#FFFFFF" if self.btn_mode_g36.isChecked() else dim, 15))
        self.btn_mode_p36.setIcon(_make_icon("piano", "#FFFFFF" if self.btn_mode_p36.isChecked() else dim, 15))

    def sync_theme(self, mode: str) -> None:
        self._theme_mode = "light" if str(mode).lower() == "light" else "dark"
        self.keyboard_strip.set_theme_mode(self._theme_mode)
        self._bridge_state_key = None
        self._poll_game_bridge()
        self._refresh_icons()
        self.update()

    def auto_connect_now(self, force: bool = False) -> bool:
        """自动连接选定或全部 MIDI 输入设备（原生支持热拔插，无多余开关）。"""
        hw = bool(self.engine.is_running()
                  and getattr(self.engine, "is_hardware_connected", lambda: False)())
        if not force and hw:
            return True
        self.refresh_devices()
        devs = _list_midi_inputs()
        target = self.cmb_device.currentData()
        if target == "all":
            start_arg = "all"
        elif isinstance(target, int) and target >= 0:
            start_arg = int(target)
        else:
            start_arg = "all"

        self._call_before_start()
        try:
            self.engine.start(device_id_or_name=start_arg, instrument_mode=self._mode)
            self._hotplug_listening = True
            if start_arg == "all":
                if devs:
                    names = " + ".join(
                        str(d.get("display_name") or _sanitize_dev_name(d.get("name")))
                        for d in devs
                    )
                    self._append_log(f"▶ 已自动连接：{names}")
                else:
                    self._append_log("… 等待插入 MIDI 键盘（已启用热拔插自动连接）")
            else:
                self._append_log(f"▶ 已连接 MIDI 设备：{self.cmb_device.currentText()}")
        except Exception as exc:
            self._hotplug_listening = False
            if force:
                QMessageBox.warning(self, "连接设备失败", f"无法打开 MIDI 输入设备：\n{exc}")
            self._append_log(f"× 自动连接失败：{exc}")
            self._sync_running_ui()
            return False
        self._sync_running_ui()
        return bool(self.engine.is_running())

    def _maybe_auto_connect(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        if not self.isVisible():
            return
        self.auto_connect_now(force=False)

    def _poll_device_hotplug(self) -> None:
        if hasattr(self, "_hotplug_timer") and not self.isVisible():
            self._hotplug_timer.stop()
            return
        devs = _list_midi_inputs()
        cur_names = [str(d.get("name") or "") for d in devs]
        prev_names = list(self._last_device_names)
        if cur_names != prev_names:
            added = [n for n in cur_names if n not in prev_names] or (len(cur_names) > len(prev_names))
            self.refresh_devices()
            hw = bool(self.engine.is_running()
                      and getattr(self.engine, "is_hardware_connected", lambda: False)())
            already_listening = bool(self.engine.is_running() and self._hotplug_listening)
            if added and not hw and not already_listening:
                self.auto_connect_now(force=False)

    def stop_if_running(self) -> None:
        self._hotplug_listening = False
        try:
            if self.engine.is_running():
                self.engine.stop()
                self._append_log("× 已停止实时 MIDI 监听并释放全部按键")
        except Exception as exc:
            _log_debug("stop_if_running error: %s", exc)
        self.keyboard_strip.clear_active_notes()
        self._sync_running_ui()

    def _sync_running_ui(self, last_status_kind: str = "") -> None:
        running = bool(self.engine.is_running())
        hw = bool(getattr(self.engine, "is_hardware_connected", lambda: False)())
        names_fn = getattr(self.engine, "connected_device_names", None)
        names = list(names_fn()) if callable(names_fn) else []
        dev_name = " + ".join(names) if names else str(getattr(self.engine, "connected_device_name", lambda: "")() or "")
        if running and hw:
            self._hotplug_listening = True
            self.lbl_state.setText(f"已连接：{dev_name}" if dev_name else "已连接 MIDI 设备")
        elif running and self._hotplug_listening:
            self.lbl_state.setText(
                "已拔出 · 等待重插" if last_status_kind == "disconnected" else "等待插入 MIDI 键盘（热拔插）"
            )
        elif running:
            self.lbl_state.setText("虚拟试弹中（支持热拔插）")
        else:
            self._hotplug_listening = False
            self.lbl_state.setText("待机（插上键盘自动连接）")
        self._refresh_icons()

    def _reset_wheel(self) -> None:
        try:
            self.engine.reset_wheel_state(page=0, sector=0, fret=1)
        except Exception:
            pass
        self.lbl_wheel_state.setText("第 1 页 · 1 品")
        self._append_log("已重置轮盘状态至第 1 页 · 1 品")

    def _release_all_keys(self) -> None:
        try:
            self.engine.release_all()
        except Exception:
            pass
        self.keyboard_strip.clear_active_notes()
        self._append_log("已触发紧急全松键")

    def _on_strip_note_on(self, pitch: int, vel: int) -> None:
        if not self.engine.is_running():
            try:
                self.engine.start(device_id_or_name=None, instrument_mode=self._mode)
                self._sync_running_ui()
            except Exception as exc:
                _log_debug("virtual start on strip press failed: %s", exc)
        try:
            self.engine.inject_note_on(int(pitch), vel=int(vel), channel=0)
        except Exception as exc:
            _log_debug("inject_note_on failed: %s", exc)

    def _on_strip_note_off(self, pitch: int) -> None:
        try:
            self.engine.inject_note_off(int(pitch), channel=0)
        except Exception as exc:
            _log_debug("inject_note_off failed: %s", exc)

    def _on_engine_event(self, ev: dict) -> None:
        etype = ev.get("type")
        raw_p = ev.get("raw_pitch", ev.get("pitch"))
        mapped_p = ev.get("pitch", raw_p)
        if etype == "note_on" and raw_p is not None:
            self.keyboard_strip.set_note_active(int(raw_p), True, ev)
            lbl = ev.get("label") or ev.get("detail") or "-"
            tag = " [折叠]" if ev.get("folded") else (" [越界]" if ev.get("dropped") else "")
            inj = "" if ev.get("injected", True) else "（仅预览·非游戏前台）"
            detail = ev.get("detail")
            if detail:
                trace = f"{detail}{tag}{inj}"
            else:
                src_name = f"{_midi_name(int(raw_p))} ({raw_p})"
                if mapped_p is not None and int(mapped_p) != int(raw_p):
                    src_name += f" → {_midi_name(int(mapped_p))}"
                trace = f"MIDI {src_name}  →  {lbl}{tag}{inj}"
            self.lbl_trace.setText(trace)
            self._append_log(f"ON  {_midi_name(int(raw_p)):>3} ({int(raw_p):>3}) -> {lbl}{tag}{inj}")
        elif etype == "note_off" and raw_p is not None:
            self.keyboard_strip.set_note_active(int(raw_p), False)
        elif etype == "cc" and int(ev.get("cc") or 0) in (120, 123):
            self.keyboard_strip.clear_active_notes()

        st = self.engine.get_stats()
        blk = int(st.get("blocked", 0) or 0)
        stat_txt = f"收音 {st.get('received', 0)} · 发声 {st.get('played', 0)} · 折叠 {st.get('folded', 0)}"
        if blk > 0:
            stat_txt += f" · 预览 {blk}"
        self.lbl_stats.setText(stat_txt)
        ws = self.engine.get_wheel_state()
        if ws:
            self.lbl_wheel_state.setText(f"第 {int(ws.get('page', 0)) + 1} 页 · {int(ws.get('fret', 1))} 品")

    def _on_engine_status(self, payload: dict) -> None:
        kind = str(payload.get("kind") or "")
        if kind in ("device_connected", "waiting_device", "disconnected"):
            if kind in ("device_connected", "waiting_device"):
                self._hotplug_listening = True
            if kind == "disconnected":
                self.keyboard_strip.clear_active_notes()
            self.refresh_devices()
            self._sync_running_ui(last_status_kind=kind)
        elif "running" in payload or kind in ("started", "stopped", "error"):
            if kind in ("focus_lost", "stopped"):
                self.keyboard_strip.clear_active_notes()
            self._sync_running_ui(last_status_kind=kind)
        elif kind == "focus_lost":
            self.keyboard_strip.clear_active_notes()
        txt = payload.get("text") or payload.get("message")
        if txt:
            self._append_log(str(txt))
        ws = payload.get("wheel")
        if isinstance(ws, dict):
            self.lbl_wheel_state.setText(f"第 {int(ws.get('page', 0)) + 1} 页 · {int(ws.get('fret', 1))} 品")

    def _poll_game_bridge(self) -> None:
        if hasattr(self, "_bridge_timer") and not self.isVisible():
            self._bridge_timer.stop()
            return
        fn = getattr(self.engine, "check_game_bridge_status", None)
        if not callable(fn):
            return
        try:
            st = fn() or {}
        except Exception as exc:
            _log_debug("check_game_bridge_status failed: %s", exc)
            return
        uipi = bool(st.get("uipi_blocked"))
        found = bool(st.get("found"))
        fg = bool(st.get("foreground"))
        reason = str(st.get("reason") or "")
        if uipi:
            level, text = "err", "游戏管理员权限 · 需提权运行"
        elif found and fg:
            level, text = "ok", "游戏前台 · 直弹发声"
        elif found:
            level, text = "warn", "已找到游戏 · 切回发声"
        else:
            level, text = "idle", "未检测到游戏 · 仅预览"
        self.lbl_game_bridge.setText(text)
        self.lbl_game_bridge.setToolTip(reason or text)
        self._apply_bridge_pill_style(level)

    def _apply_bridge_pill_style(self, level: str) -> None:
        dark = (self._theme_mode != "light")
        key = (level, dark)
        if self._bridge_state_key == key:
            return
        self._bridge_state_key = key
        if level == "err":
            bg, fg, bd = (("rgba(244, 63, 94, 0.20)", "#FB7185", "rgba(244, 63, 94, 0.52)")
                          if dark else ("rgba(254, 226, 226, 0.92)", "#B91C1C", "rgba(239, 68, 68, 0.55)"))
        elif level == "ok":
            bg, fg, bd = (("rgba(16, 185, 129, 0.18)", "#34D399", "rgba(52, 211, 153, 0.44)")
                          if dark else ("rgba(209, 250, 229, 0.90)", "#065F46", "rgba(16, 185, 129, 0.50)"))
        elif level == "warn":
            bg, fg, bd = (("rgba(244, 200, 105, 0.16)", "#F4C869", "rgba(244, 200, 105, 0.44)")
                          if dark else ("rgba(254, 243, 199, 0.92)", "#92400E", "rgba(245, 158, 11, 0.54)"))
        else:
            bg, fg, bd = (("rgba(148, 163, 184, 0.14)", "#94A3B8", "rgba(148, 163, 184, 0.30)")
                          if dark else ("rgba(226, 232, 240, 0.80)", "#475569", "rgba(148, 163, 184, 0.46)"))
        self.lbl_game_bridge.setStyleSheet(
            "padding: 3px 9px; border-radius: 9px; font-size: 12px; font-weight: 700; "
            f"background: {bg}; color: {fg}; border: 1px solid {bd};"
        )

    def _append_log(self, msg: str) -> None:
        self.event_log.appendPlainText(str(msg))

    def showEvent(self, event):
        super().showEvent(event)
        self._poll_game_bridge()
        if hasattr(self, "_bridge_timer") and not self._bridge_timer.isActive():
            self._bridge_timer.start()
        if hasattr(self, "_hotplug_timer") and not self._hotplug_timer.isActive():
            self._hotplug_timer.start()
        QTimer.singleShot(120, self._maybe_auto_connect)

    def hideEvent(self, event):
        if hasattr(self, "_bridge_timer") and self._bridge_timer.isActive():
            self._bridge_timer.stop()
        if hasattr(self, "_hotplug_timer") and self._hotplug_timer.isActive():
            self._hotplug_timer.stop()
        self.stop_if_running()
        super().hideEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "_bridge_timer") and self._bridge_timer.isActive():
            self._bridge_timer.stop()
        if hasattr(self, "_hotplug_timer") and self._hotplug_timer.isActive():
            self._hotplug_timer.stop()
        self.stop_if_running()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import gui_theme as GT
        GT.apply_theme(app, "dark")
    except Exception:
        pass
    dlg = MidiLiveDialog(instrument_mode="piano_36")
    if "--smoke" in sys.argv:
        dlg.show()
        app.processEvents()
        dlg.close()
        print("MIDI_LIVE_UI_SMOKE_OK")
    else:
        dlg.exec_()
