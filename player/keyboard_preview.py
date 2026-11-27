# -*- coding: utf-8 -*-
"""player/keyboard_preview.py -- 《异键演奏》v1.4 交互式 36 键位图。

与 player/keyboard_layout.py 契约对齐：
  - INSTRUMENT_GUITAR_36 = "guitar_36" (MIDI 52..87，3 行 × 12 独立键帽)
  - INSTRUMENT_PIANO_36  = "piano_36"  (MIDI 60..95，3 行 × 12 简谱圈 + Shift/Ctrl 动态指示)
  - pitch_to_binding(pitch, instrument_mode)

本模块使用 QPainter 绘制星曜暗夜 / 星辉浅昼双主题抗锯齿圆角键帽，
支持曲目音高热度发光边框、曲目覆盖率与移调后命中统计，以及钢琴 36 键
「默认自然音 / 按住 Shift (#) / 按住 Ctrl (b)」三态动态移位指示。
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

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,  # noqa: E402
                         QPen, QPolygonF)
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame,  # noqa: E402
                             QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QSpinBox, QVBoxLayout, QWidget)

try:
    import ui_widgets as UW  # type: ignore
except Exception:
    UW = None

try:
    from . import keyboard_layout as KL  # type: ignore
except Exception:
    try:
        import keyboard_layout as KL  # type: ignore
    except Exception:
        KL = None

INSTRUMENT_GUITAR_36 = getattr(KL, "INSTRUMENT_GUITAR_36", "guitar_36")
INSTRUMENT_PIANO_36 = getattr(KL, "INSTRUMENT_PIANO_36", "piano_36")

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SOLFEGE_12 = ("1", "#1", "2", "b3", "3", "4", "#4", "5", "#5", "6", "b7", "7")

_GUITAR_36_ROWS = (
    ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="),       # 76..87 E5..D#6
    ("Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]"),       # 64..75 E4..D#5
    ("A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'", "Enter"),   # 52..63 E3..D#4
)

_PIANO_36_BASE = (
    ("Q", "W", "E", "R", "T", "Y", "U"),  # 84..95 高音 C6..B6
    ("A", "S", "D", "F", "G", "H", "J"),  # 72..83 中音 C5..B5
    ("Z", "X", "C", "V", "B", "N", "M"),  # 60..71 低音 C4..B4
)

# pc (0..11) -> (7音列索引 0..6, 修饰键)
_PIANO_PC_MAP = {
    0: (0, None),
    1: (0, "LShift"),
    2: (1, None),
    3: (2, "LCtrl"),
    4: (2, None),
    5: (3, None),
    6: (3, "LShift"),
    7: (4, None),
    8: (4, "LShift"),
    9: (5, None),
    10: (6, "LCtrl"),
    11: (6, None),
}

# LShift 仅右移 {0, 3, 4} (QRT / AFG / ZVB); LCtrl 仅左移 {2, 6} (EU / DJ / CM)
_SHIFT_COLS = getattr(KL, "LSHIFT_AFFECTED_COLS", frozenset({0, 3, 4}))
_CTRL_COLS = getattr(KL, "LCTRL_AFFECTED_COLS", frozenset({2, 6}))
_RANGES = getattr(KL, "INSTRUMENT_RANGES", {
    INSTRUMENT_GUITAR_36: (52, 87),
    INSTRUMENT_PIANO_36: (60, 95),
})


def _midi_to_name(pitch: int) -> str:
    p = int(pitch)
    return f"{_NOTE_NAMES[p % 12]}{p // 12 - 1}"


def _fallback_pitch_to_binding(pitch: int, instrument_mode: str):
    p = int(pitch)
    if instrument_mode == INSTRUMENT_GUITAR_36:
        if not (52 <= p <= 87):
            return None
        offset = p - 52
        octave_from_bottom = offset // 12  # 0=低音, 1=中音, 2=高音
        row = 2 - octave_from_bottom       # 显示行: 0=高音, 1=中音, 2=低音
        col = offset % 12
        key = _GUITAR_36_ROWS[row][col]
        return {
            "key": key,
            "mod": None,
            "label": key,
            "row": row,
            "col": col,
        }
    if instrument_mode == INSTRUMENT_PIANO_36:
        if not (60 <= p <= 95):
            return None
        offset = p - 60
        octave_from_bottom = offset // 12
        row = 2 - octave_from_bottom
        pc = offset % 12
        base_col, mod = _PIANO_PC_MAP[pc]
        key = _PIANO_36_BASE[row][base_col]
        prefix = "⇧" if mod == "LShift" else ("⌃" if mod == "LCtrl" else "")
        return {
            "key": key,
            "mod": mod,
            "label": prefix + key,
            "row": row,
            "col": pc,
            "base_col": base_col,
        }
    return None


def pitch_to_binding(pitch: int, instrument_mode: str):
    """优先调用 keyboard_layout.pitch_to_binding，未就绪时使用内置契约兜底。"""
    if KL is not None and hasattr(KL, "pitch_to_binding"):
        try:
            b = KL.pitch_to_binding(int(pitch), instrument_mode)
            if b is not None:
                return b
        except Exception:
            pass
    return _fallback_pitch_to_binding(pitch, instrument_mode)


def _normalize_used_pitches(used_pitches) -> dict[int, int]:
    """将传入的 {pitch: count} 或 pitch 集合标准化为 {int_pitch: int_count}。"""
    if not used_pitches:
        return {}
    out: dict[int, int] = {}
    if isinstance(used_pitches, dict):
        for k, v in used_pitches.items():
            try:
                p = int(k)
                cnt = int(v)
            except (TypeError, ValueError):
                continue
            if cnt > 0:
                out[p] = out.get(p, 0) + cnt
        return out
    try:
        for item in used_pitches:
            try:
                p = int(item)
            except (TypeError, ValueError):
                continue
            out[p] = out.get(p, 0) + 1
    except TypeError:
        pass
    return out


def compute_pitch_stats(used_pitches, instrument_mode: str, transpose: int = 0) -> dict:
    """计算移调后的音域命中统计与曲目覆盖率。"""
    raw = _normalize_used_pitches(used_pitches)
    lo, hi = (52, 87) if instrument_mode == INSTRUMENT_GUITAR_36 else (60, 95)
    total = sum(raw.values())
    shifted_counts: dict[int, int] = {}
    in_range = 0
    low_out = 0
    high_out = 0
    for p, cnt in raw.items():
        sp = p + int(transpose)
        if lo <= sp <= hi:
            in_range += cnt
            shifted_counts[sp] = shifted_counts.get(sp, 0) + cnt
        elif sp < lo:
            low_out += cnt
        else:
            high_out += cnt
    coverage = (float(in_range) / float(total) * 100.0) if total > 0 else 0.0
    return {
        "total": total,
        "in_range": in_range,
        "out_of_range": total - in_range,
        "low_out": low_out,
        "high_out": high_out,
        "coverage": coverage,
        "shifted_counts": shifted_counts,
        "unique_pitches": len(shifted_counts),
        "low": lo,
        "high": hi,
    }


def _is_dark_palette(widget: QWidget | None = None) -> bool:
    try:
        pal = widget.palette() if widget is not None else QApplication.palette()
        bg = pal.window().color()
        return bg.lightness() < 128
    except Exception:
        return True


class KeyboardMapWidget(QWidget):
    """星曜风格 3 行 × 12 列交互式 36 键位图控件。"""

    keyClicked = pyqtSignal(int)

    def __init__(self, instrument_mode: str = INSTRUMENT_PIANO_36,
                 used_pitches=None, transpose: int = 0, parent=None):
        super().__init__(parent)
        self._mode = (instrument_mode if instrument_mode in
                      (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                      else INSTRUMENT_PIANO_36)
        self._mod_state = "natural"  # "natural" | "LShift" | "LCtrl"
        self._raw_used = _normalize_used_pitches(used_pitches)
        self._transpose = int(transpose or 0)
        self._stats = compute_pitch_stats(self._raw_used, self._mode, self._transpose)
        self._cell_rects: list[tuple[QRectF, dict]] = []
        self._hover_pitch: int | None = None
        self._selected_pitch: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(560, 250)

    def sizeHint(self) -> QSize:
        return QSize(940, 380)

    def set_mode(self, instrument_mode: str) -> None:
        mode = (instrument_mode if instrument_mode in
                (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                else INSTRUMENT_PIANO_36)
        if mode != self._mode:
            self._mode = mode
            self._stats = compute_pitch_stats(self._raw_used, self._mode, self._transpose)
            self._selected_pitch = None
            self.update()

    def set_modifier_state(self, mod_state: str) -> None:
        state = mod_state if mod_state in ("natural", "LShift", "LCtrl") else "natural"
        if state != self._mod_state:
            self._mod_state = state
            self.update()

    def set_used_pitches(self, used_pitches, transpose: int = 0) -> None:
        self._raw_used = _normalize_used_pitches(used_pitches)
        self._transpose = int(transpose or 0)
        self._stats = compute_pitch_stats(self._raw_used, self._mode, self._transpose)
        self.update()

    def set_transpose(self, transpose: int) -> None:
        t = int(transpose or 0)
        if t != self._transpose:
            self._transpose = t
            self._stats = compute_pitch_stats(self._raw_used, self._mode, self._transpose)
            self.update()

    def _build_cells(self) -> list[dict]:
        """构造 3 行 × 12 列（共 36 键）的元数据描述，与 keyboard_layout 契约对齐。"""
        cells = []
        lo, _hi = _RANGES.get(self._mode, (52, 87) if self._mode == INSTRUMENT_GUITAR_36 else (60, 95))
        for row in range(3):
            oct_idx = 2 - row  # 0=高音(顶部), 1=中音, 2=低音(底部)
            base_pitch = lo + oct_idx * 12
            for col in range(12):
                pitch = base_pitch + col
                binding = pitch_to_binding(pitch, self._mode) or _fallback_pitch_to_binding(pitch, self._mode)
                pc = col
                note_name = _midi_to_name(pitch)
                if self._mode == INSTRUMENT_GUITAR_36:
                    key_cap = binding["label"]
                    base_key = binding["key"]
                    is_accidental = "#" in note_name
                    cells.append({
                        "row": row,
                        "col": col,
                        "pitch": pitch,
                        "note_name": note_name,
                        "solfege": "",
                        "key_cap": key_cap,
                        "base_key": base_key,
                        "mod": binding.get("mod"),
                        "base_col": binding.get("col", col),
                        "is_accidental": is_accidental,
                        "binding": binding,
                    })
                else:
                    mod = binding.get("mod")
                    base_key = binding["key"]
                    base_col = binding.get("base_col", binding.get("col", _PIANO_PC_MAP[pc][0]))
                    cells.append({
                        "row": row,
                        "col": col,
                        "pitch": pitch,
                        "note_name": note_name,
                        "solfege": _SOLFEGE_12[pc],
                        "key_cap": binding["label"],
                        "base_key": base_key,
                        "mod": mod,
                        "base_col": base_col,
                        "is_accidental": mod is not None,
                        "binding": binding,
                    })
        return cells

    def _theme_tokens(self) -> dict:
        if _is_dark_palette(self):
            return {
                "canvas": QColor("#0D1220"),
                "canvas_border": QColor("#232C47"),
                "row_bg": QColor("#111626"),
                "row_label": QColor("#94A3B8"),
                "row_sub": QColor("#64748B"),
                "key_nat": QColor("#151D33"),
                "key_nat_b": QColor("#2B385C"),
                "key_acc": QColor("#101628"),
                "key_acc_b": QColor("#3B2F63"),
                "key_dim": QColor("#0B101D"),
                "key_dim_b": QColor("#1C2338"),
                "text": QColor("#E6EDF7"),
                "text_dim": QColor("#94A3B8"),
                "text_mute": QColor("#4B556B"),
                "blue": QColor("#3B6EF5"),
                "blue_soft": QColor(59, 110, 245, 55),
                "gold": QColor("#F4C869"),
                "gold_soft": QColor(244, 200, 105, 58),
                "violet": QColor("#8B5CF6"),
                "violet_soft": QColor(139, 92, 246, 52),
                "cyan": QColor("#38BDF8"),
                "cyan_soft": QColor(56, 189, 248, 52),
                "ctrl_col": QColor("#38BDF8"),
                "ctrl_soft": QColor(56, 189, 248, 52),
                "green": QColor("#34D399"),
                "green_soft": QColor(52, 211, 153, 44),
            }
        return {
            "canvas": QColor("#F4F8FF"),
            "canvas_border": QColor("#C7D6EE"),
            "row_bg": QColor("#FFFFFF"),
            "row_label": QColor("#1E293B"),
            "row_sub": QColor("#475569"),
            "key_nat": QColor("#FFFFFF"),
            "key_nat_b": QColor("#C7D6EE"),
            "key_acc": QColor("#F5F3FF"),
            "key_acc_b": QColor("#C4B5FD"),
            "key_dim": QColor("#EEF2FA"),
            "key_dim_b": QColor("#D5E0F2"),
            "text": QColor("#1E293B"),
            "text_dim": QColor("#475569"),
            "text_mute": QColor("#94A3B8"),
            "blue": QColor("#2F5CE5"),
            "blue_soft": QColor(47, 92, 229, 32),
            "gold": QColor("#D97706"),
            "gold_soft": QColor("#FEF3C7"),
            "violet": QColor("#7C3AED"),
            "violet_soft": QColor("#EDE9FE"),
            "cyan": QColor("#0284C7"),
            "cyan_soft": QColor(2, 132, 199, 34),
            "ctrl_col": QColor("#7C3AED"),
            "ctrl_soft": QColor("#EDE9FE"),
            "green": QColor("#059669"),
            "green_soft": QColor(5, 150, 105, 34),
        }

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        t = self._theme_tokens()

        w = float(self.width())
        h = float(self.height())
        outer = QRectF(1.0, 1.0, max(10.0, w - 2.0), max(10.0, h - 2.0))
        p.setBrush(t["canvas"])
        p.setPen(QPen(t["canvas_border"], 1.2))
        p.drawRoundedRect(outer, 12.0, 12.0)

        pad_x = 12.0 if w < 760 else 14.0
        pad_y = 12.0 if h < 300 else 14.0
        label_col_w = 72.0 if w < 760 else 88.0
        gap_x = 4.0 if w < 760 else 7.0
        gap_y = 8.0 if h < 300 else 11.0
        grid_x = pad_x + label_col_w
        grid_w = max(120.0, w - grid_x - pad_x)
        grid_h = max(120.0, h - pad_y * 2.0)
        cell_w = (grid_w - gap_x * 11.0) / 12.0
        cell_h = (grid_h - gap_y * 2.0) / 3.0

        row_titles = (
            ("高音区", "E5 ~ D#6", "1 ~ =") if self._mode == INSTRUMENT_GUITAR_36 else ("高音区", "C6 ~ B6", "Q ~ U")
        ), (
            ("中音区", "E4 ~ D#5", "Q ~ ]") if self._mode == INSTRUMENT_GUITAR_36 else ("中音区", "C5 ~ B5", "A ~ J")
        ), (
            ("低音区", "E3 ~ D#4", "A ~ ↵") if self._mode == INSTRUMENT_GUITAR_36 else ("低音区", "C4 ~ B4", "Z ~ M")
        )

        cells = self._build_cells()
        shifted_counts = self._stats["shifted_counts"]
        max_hit = max(shifted_counts.values()) if shifted_counts else 0
        self._cell_rects = []
        rect_by_rc: dict[tuple[int, int], QRectF] = {}

        # 行首区域标签
        for row in range(3):
            ry = pad_y + row * (cell_h + gap_y)
            r_box = QRectF(pad_x, ry, label_col_w - 10.0, cell_h)
            p.setBrush(t["row_bg"])
            p.setPen(QPen(t["canvas_border"], 1.0))
            p.drawRoundedRect(r_box, 9.0, 9.0)

            rt, rrng, rkeys = row_titles[row]
            p.setPen(t["row_label"])
            p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
            p.drawText(QRectF(r_box.x(), r_box.y() + 6, r_box.width(), cell_h * 0.36),
                       Qt.AlignCenter, rt)
            p.setPen(t["row_sub"])
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.drawText(QRectF(r_box.x(), r_box.y() + cell_h * 0.40, r_box.width(), cell_h * 0.28),
                       Qt.AlignCenter, rrng)
            p.setPen(t["cyan"])
            p.setFont(QFont("Consolas", 8))
            p.drawText(QRectF(r_box.x(), r_box.y() + cell_h * 0.68, r_box.width(), cell_h * 0.26),
                       Qt.AlignCenter, rkeys)

        # 键帽逐格绘制
        for cell in cells:
            row, col = cell["row"], cell["col"]
            cx = grid_x + col * (cell_w + gap_x)
            cy = pad_y + row * (cell_h + gap_y)
            rect = QRectF(cx, cy, cell_w, cell_h)
            rect_by_rc[(row, col)] = rect
            self._cell_rects.append((rect, cell))

            pitch = cell["pitch"]
            hits = shifted_counts.get(pitch, 0)
            is_hover = (self._hover_pitch == pitch)
            is_sel = (self._selected_pitch == pitch)

            # 计算 piano_36 三态状态
            role_state = "active"  # active | shifted_target | shifted_ghost | pass_through | inactive
            if self._mode == INSTRUMENT_PIANO_36:
                mod = cell["mod"]
                base_col = cell["base_col"]
                if self._mod_state == "natural":
                    role_state = "active" if mod is None else "inactive"
                elif self._mod_state == "LShift":
                    if mod == "LShift":
                        role_state = "shifted_target"
                    elif mod is None and base_col in _SHIFT_COLS:
                        role_state = "shifted_ghost"
                    elif mod is None:
                        role_state = "pass_through"
                    else:
                        role_state = "inactive"
                elif self._mod_state == "LCtrl":
                    if mod == "LCtrl":
                        role_state = "shifted_target"
                    elif mod is None and base_col in _CTRL_COLS:
                        role_state = "shifted_ghost"
                    elif mod is None:
                        role_state = "pass_through"
                    else:
                        role_state = "inactive"

            # 确定键帽底色与边框
            bg_col = t["key_acc"] if cell["is_accidental"] else t["key_nat"]
            bd_col = t["key_acc_b"] if cell["is_accidental"] else t["key_nat_b"]
            bd_w = 1.2
            text_col = t["text"]
            sub_col = t["text_dim"]
            dashed = False

            if self._mode == INSTRUMENT_PIANO_36:
                if role_state == "inactive":
                    bg_col = t["key_dim"]
                    bd_col = t["key_dim_b"]
                    text_col = t["text_mute"]
                    sub_col = t["text_mute"]
                elif role_state == "shifted_ghost":
                    bg_col = t["key_dim"]
                    bd_col = t["gold"] if self._mod_state == "LShift" else t["ctrl_col"]
                    text_col = t["text_mute"]
                    sub_col = t["text_mute"]
                    dashed = True
                elif role_state == "shifted_target":
                    accent = t["gold"] if self._mod_state == "LShift" else t["ctrl_col"]
                    bg_col = t["gold_soft"] if self._mod_state == "LShift" else t["ctrl_soft"]
                    bd_col = accent
                    bd_w = 2.0
                elif role_state == "pass_through":
                    bg_col = t["key_nat"]
                    bd_col = t["green"]
                    bd_w = 1.4

            # 曲目热度外发光边框
            if hits > 0 and max_hit > 0:
                ratio = min(1.0, 0.28 + 0.72 * (hits / float(max_hit)))
                halo_col = QColor(t["gold"])
                halo_col.setAlpha(int(45 + 115 * ratio))
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(halo_col, 4.5))
                p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 10.5, 10.5)
                if role_state in ("active", "pass_through"):
                    bd_col = t["gold"] if ratio >= 0.5 else t["cyan"]
                    bd_w = 1.8
                if role_state == "inactive":
                    # 即使在默认视图，曲目用到的升降音也要清晰显现
                    text_col = t["text"]
                    sub_col = t["text_dim"]
                    bg_col = t["violet_soft"] if cell["mod"] == "LCtrl" else t["gold_soft"]
                    bd_col = t["gold"]
                    bd_w = 1.6

            if is_hover or is_sel:
                bd_col = t["cyan"] if is_hover else t["blue"]
                bd_w = 2.2

            # 绘制键帽主体
            p.setBrush(bg_col)
            pen = QPen(bd_col, bd_w, Qt.DashLine if dashed else Qt.SolidLine)
            p.setPen(pen)
            p.drawRoundedRect(rect, 9.0, 9.0)

            # 顶部内高光微线（质感）
            if role_state not in ("inactive", "shifted_ghost"):
                hi_pen = QPen(QColor(255, 255, 255, 26 if _is_dark_palette(self) else 150), 1.0)
                p.setPen(hi_pen)
                p.drawLine(QPointF(rect.x() + 7, rect.y() + 1.5),
                           QPointF(rect.right() - 7, rect.y() + 1.5))

            if self._mode == INSTRUMENT_GUITAR_36:
                # 上部：音名（如 E5 / F#5）
                p.setPen(t["violet"] if cell["is_accidental"] else sub_col)
                p.setFont(QFont("Consolas", 8, QFont.Bold))
                p.drawText(QRectF(rect.x() + 5, rect.y() + 5, rect.width() - 10, 16),
                           Qt.AlignLeft | Qt.AlignVCenter, cell["note_name"])

                # 中部：物理键帽（Enter 特殊缩字号）
                cap = cell["key_cap"]
                cap_font_size = 9 if len(cap) > 2 else 13
                p.setPen(text_col)
                p.setFont(QFont("Consolas", cap_font_size, QFont.Bold))
                p.drawText(QRectF(rect.x() + 3, rect.y() + cell_h * 0.26, rect.width() - 6, cell_h * 0.48),
                           Qt.AlignCenter, cap)

                # 底部：MIDI 编号小字
                p.setPen(t["row_sub"])
                p.setFont(QFont("Consolas", 7))
                p.drawText(QRectF(rect.x() + 4, rect.bottom() - 16, rect.width() - 8, 13),
                           Qt.AlignCenter, f"M{pitch}")
            else:
                # 顶部：音名（C4 / C#4）
                p.setPen(sub_col)
                p.setFont(QFont("Consolas", 7, QFont.Bold))
                p.drawText(QRectF(rect.x() + 5, rect.y() + 4, rect.width() - 10, 14),
                           Qt.AlignLeft | Qt.AlignVCenter, cell["note_name"])

                # 中部：简谱圈（1 #1 2 b3 ... 7）
                circle_r = min(cell_w, cell_h) * 0.25
                cx_mid = rect.center().x()
                cy_mid = rect.y() + cell_h * 0.45
                c_rect = QRectF(cx_mid - circle_r, cy_mid - circle_r, circle_r * 2.0, circle_r * 2.0)

                if role_state == "shifted_target":
                    c_bg = t["gold"] if self._mod_state == "LShift" else t["ctrl_col"]
                    c_fg = QColor("#090C15") if _is_dark_palette(self) else QColor("#FFFFFF")
                    c_bd = c_bg
                elif cell["mod"] is None and role_state in ("active", "pass_through"):
                    c_bg = t["blue_soft"]
                    c_fg = text_col
                    c_bd = t["green"] if role_state == "pass_through" else t["blue"]
                else:
                    c_bg = Qt.transparent
                    c_fg = text_col
                    c_bd = bd_col

                p.setBrush(c_bg)
                p.setPen(QPen(c_bd, 1.3))
                p.drawEllipse(c_rect)
                p.setPen(c_fg)
                p.setFont(QFont("Microsoft YaHei UI", 9 if len(cell["solfege"]) <= 1 else 8, QFont.Bold))
                p.drawText(c_rect, Qt.AlignCenter, cell["solfege"])

                # 底部：物理键位（自然音高亮单键，升降显示 ⇧Q / ⌃E）
                cap_col = text_col
                if role_state == "shifted_target":
                    cap_col = t["gold"] if self._mod_state == "LShift" else t["ctrl_col"]
                elif role_state == "pass_through":
                    cap_col = t["green"]
                p.setPen(cap_col)
                p.setFont(QFont("Consolas", 9, QFont.Bold))
                p.drawText(QRectF(rect.x() + 2, rect.bottom() - 20, rect.width() - 4, 16),
                           Qt.AlignCenter, cell["key_cap"])

            # 右上角命中次数徽章
            if hits > 0:
                badge_txt = str(hits) if hits < 100 else "99+"
                bw = 20.0 if len(badge_txt) <= 2 else 25.0
                b_rect = QRectF(rect.right() - bw - 3.0, rect.y() + 3.0, bw, 14.0)
                p.setBrush(t["gold"])
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(b_rect, 6.0, 6.0)
                p.setPen(QColor("#090C15") if _is_dark_palette(self) else QColor("#FFFFFF"))
                p.setFont(QFont("Consolas", 7, QFont.Bold))
                p.drawText(b_rect, Qt.AlignCenter, badge_txt)

        # piano_36 动态 Shift/Ctrl 移位箭头覆盖层
        if self._mode == INSTRUMENT_PIANO_36 and self._mod_state in ("LShift", "LCtrl"):
            if self._mod_state == "LShift":
                pairs = ((0, 1), (5, 6), (7, 8))  # 1->#1, 4->#4, 5->#5
                arrow_col = t["gold"]
            else:
                pairs = ((4, 3), (11, 10))         # 3->b3, 7->b7
                arrow_col = t["ctrl_col"]

            p.setBrush(arrow_col)
            p.setPen(QPen(arrow_col, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for row in range(3):
                for src_col, dst_col in pairs:
                    r_src = rect_by_rc.get((row, src_col))
                    r_dst = rect_by_rc.get((row, dst_col))
                    if r_src is None or r_dst is None:
                        continue
                    y_mid = r_src.center().y()
                    if dst_col > src_col:
                        x1 = r_src.right() - 4.0
                        x2 = r_dst.left() + 4.0
                        p.drawLine(QPointF(x1, y_mid), QPointF(x2, y_mid))
                        head = QPolygonF([
                            QPointF(x2 + 2.0, y_mid),
                            QPointF(x2 - 5.0, y_mid - 4.0),
                            QPointF(x2 - 5.0, y_mid + 4.0),
                        ])
                        p.drawPolygon(head)
                    else:
                        x1 = r_src.left() + 4.0
                        x2 = r_dst.right() - 4.0
                        p.drawLine(QPointF(x1, y_mid), QPointF(x2, y_mid))
                        head = QPolygonF([
                            QPointF(x2 - 2.0, y_mid),
                            QPointF(x2 + 5.0, y_mid - 4.0),
                            QPointF(x2 + 5.0, y_mid + 4.0),
                        ])
                        p.drawPolygon(head)

        p.end()

    def _hit_test(self, pos) -> dict | None:
        pt = QPointF(pos)
        for rect, cell in self._cell_rects:
            if rect.contains(pt):
                return cell
        return None

    def mouseMoveEvent(self, event):
        cell = self._hit_test(event.pos())
        pitch = cell["pitch"] if cell else None
        if pitch != self._hover_pitch:
            self._hover_pitch = pitch
            if cell:
                hits = self._stats["shifted_counts"].get(pitch, 0)
                tip = f"{cell['note_name']} (MIDI {pitch}) · 键位 {cell['key_cap']}"
                if cell.get("solfege"):
                    tip += f" · 简谱 {cell['solfege']}"
                if cell.get("mod") == "LShift":
                    tip += "（按住 LShift 触发，仅右移 QRT/AFG/ZVB）"
                elif cell.get("mod") == "LCtrl":
                    tip += "（按住 LCtrl 触发，仅左移 EU/DJ/CM）"
                if hits > 0:
                    tip += f"\n本曲命中：{hits} 次"
                self.setToolTip(tip)
            else:
                self.setToolTip("")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_pitch is not None:
            self._hover_pitch = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            cell = self._hit_test(event.pos())
            if cell is not None:
                self._selected_pitch = cell["pitch"]
                self.keyClicked.emit(cell["pitch"])
                self.update()
                return
        super().mousePressEvent(event)


class KeyboardPreviewDialog(QDialog):
    """36 键位图交互预览弹窗（支持吉他 36 键 / 钢琴 36 键 / 曲目热度覆盖）。"""

    def __init__(self, instrument_mode: str = INSTRUMENT_PIANO_36,
                 used_pitches=None, transpose: int = 0, parent=None,
                 embedded: bool = False, on_back=None):
        super().__init__(parent)
        self._embedded = bool(embedded)
        self._on_back = on_back
        self._mode = (instrument_mode if instrument_mode in
                      (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                      else INSTRUMENT_PIANO_36)
        self._raw_used = _normalize_used_pitches(used_pitches)
        self._transpose = int(transpose or 0)
        self._mod_state = "natural"

        self.setWindowTitle("36 键位交互式布局图")
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setMinimumSize(0, 290)
        else:
            try:
                avail = QApplication.desktop().availableGeometry(self)
                self.resize(min(1040, int(avail.width() * 0.92)),
                            min(600, int(avail.height() * 0.88)))
                self.setMinimumSize(min(680, int(avail.width() * 0.80)),
                                    min(440, int(avail.height() * 0.72)))
            except Exception:
                self.resize(1000, 560)
                self.setMinimumSize(680, 440)

        self._build_ui()
        self._sync_ui()

    def update_preview(self, instrument_mode: str | None = None,
                       used_pitches=None, transpose: int | None = None) -> None:
        if instrument_mode in (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36):
            self._mode = instrument_mode
            if hasattr(self, "map_widget"):
                self.map_widget.set_mode(self._mode)
        if used_pitches is not None:
            self._raw_used = _normalize_used_pitches(used_pitches)
            if hasattr(self, "map_widget"):
                self.map_widget.set_used_pitches(self._raw_used, transpose=self._transpose)
        if transpose is not None:
            self._transpose = int(transpose or 0)
            if hasattr(self, "spin_transpose"):
                self.spin_transpose.blockSignals(True)
                self.spin_transpose.setValue(self._transpose)
                self.spin_transpose.setPrefix("+" if self._transpose > 0 else "")
                self.spin_transpose.blockSignals(False)
            if hasattr(self, "map_widget"):
                self.map_widget.set_transpose(self._transpose)
        self._sync_ui()

    def _handle_close_or_back(self) -> None:
        if self._embedded:
            if callable(self._on_back):
                self._on_back()
            return
        self.accept()

    def reject(self) -> None:
        if self._embedded:
            self._handle_close_or_back()
            return
        super().reject()

    def keyPressEvent(self, event) -> None:
        if self._embedded and event.key() == Qt.Key_Escape:
            self._handle_close_or_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 顶栏：模式切换胶囊 + 曲目命中统计 + 移调预览（内嵌模式下隐藏以免与演奏台顶栏重复）
        top_card = UW.GlassCard() if (UW is not None and hasattr(UW, "GlassCard")) else QFrame()
        top_card.setObjectName("card")
        self.top_card = top_card
        top_lay = QHBoxLayout(top_card)
        top_lay.setContentsMargins(12, 8, 12, 8)
        top_lay.setSpacing(6)

        lbl_title = QLabel("36 键位")
        lbl_title.setObjectName("sectionTitle")
        top_lay.addWidget(lbl_title)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self.btn_mode_guitar = QPushButton("吉他 36 键")
        self.btn_mode_guitar.setToolTip("吉他 · 36 键（MIDI 52 E3 ~ 87 D#6）")
        self.btn_mode_piano = QPushButton("钢琴 36 键")
        self.btn_mode_piano.setToolTip("钢琴 · 36 键（MIDI 60 C4 ~ 95 B6）")
        for btn, mode in ((self.btn_mode_guitar, INSTRUMENT_GUITAR_36),
                          (self.btn_mode_piano, INSTRUMENT_PIANO_36)):
            btn.setObjectName("modePill")
            btn.setCheckable(True)
            btn.setIconSize(QSize(14, 14))
            btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, m=mode: self.set_instrument_mode(m))
            self._mode_group.addButton(btn)
            top_lay.addWidget(btn)

        top_lay.addStretch(1)

        self.lbl_transpose = QLabel("移调")
        self.lbl_transpose.setObjectName("sub")
        self.spin_transpose = QSpinBox()
        self.spin_transpose.setRange(-24, 24)
        self.spin_transpose.setValue(self._transpose)
        if self._transpose > 0:
            self.spin_transpose.setPrefix("+")
        self.spin_transpose.setSuffix(" 半音")
        self.spin_transpose.setFixedWidth(82)
        self.spin_transpose.valueChanged.connect(self._on_transpose_changed)
        top_lay.addWidget(self.lbl_transpose)
        top_lay.addWidget(self.spin_transpose)

        self.lbl_coverage = QLabel("未传入曲目")
        self.lbl_coverage.setObjectName("accentPill")
        self.lbl_coverage.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        top_lay.addWidget(self.lbl_coverage)

        self.lbl_hits = QLabel("")
        self.lbl_hits.setObjectName("goldPill")
        self.lbl_hits.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        if not self._embedded:
            top_lay.addWidget(self.lbl_hits)

        top_card.setVisible(not self._embedded)
        root.addWidget(top_card)

        # 钢琴三态切换栏 + 互斥规则说明（内嵌模式采用紧凑双行以免长说明被挤窄）
        self.ctrl_card = QFrame()
        self.ctrl_card.setObjectName("card_inner")
        if self._embedded:
            ctrl_vbox = QVBoxLayout(self.ctrl_card)
            ctrl_vbox.setContentsMargins(10, 6, 10, 6)
            ctrl_vbox.setSpacing(5)
            ctrl_lay = QHBoxLayout()
            ctrl_lay.setSpacing(8)
            ctrl_vbox.addLayout(ctrl_lay)
        else:
            ctrl_lay = QHBoxLayout(self.ctrl_card)
            ctrl_lay.setContentsMargins(12, 8, 12, 8)
            ctrl_lay.setSpacing(8)

        self._mod_group = QButtonGroup(self)
        self._mod_group.setExclusive(True)
        self.btn_mod_nat = QPushButton("默认自然音")
        self.btn_mod_shift = QPushButton("按住 Shift (#)")
        self.btn_mod_ctrl = QPushButton("按住 Ctrl (b)")
        for btn, st in ((self.btn_mod_nat, "natural"),
                        (self.btn_mod_shift, "LShift"),
                        (self.btn_mod_ctrl, "LCtrl")):
            btn.setObjectName("modePill")
            btn.setCheckable(True)
            btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _=False, s=st: self.set_modifier_state(s))
            self._mod_group.addButton(btn)
            ctrl_lay.addWidget(btn)

        self.lbl_rule_hint = QLabel()
        self.lbl_rule_hint.setObjectName("sub")
        self.lbl_rule_hint.setWordWrap(True)

        btn_close = QPushButton("▲ 收起键位图" if self._embedded else "关闭")
        btn_close.setMinimumWidth(112 if self._embedded else 96)
        btn_close.setIconSize(QSize(15, 15))
        self.btn_close = btn_close
        btn_close.clicked.connect(self._handle_close_or_back)
        if self._embedded:
            ctrl_lay.addStretch(1)
            ctrl_lay.addWidget(self.lbl_hits, 0, Qt.AlignVCenter)
            ctrl_lay.addWidget(btn_close, 0, Qt.AlignVCenter)
            ctrl_vbox.addWidget(self.lbl_rule_hint)
        else:
            ctrl_lay.addWidget(self.lbl_rule_hint, 1)
        root.addWidget(self.ctrl_card)

        # 主画布：3 × 12 键位图
        self.map_widget = KeyboardMapWidget(
            instrument_mode=self._mode,
            used_pitches=self._raw_used,
            transpose=self._transpose,
            parent=self,
        )
        root.addWidget(self.map_widget, 1)

        # 底部状态与关闭栏（内嵌模式已并入控制栏，不再重复占一行）
        if not self._embedded:
            foot = QHBoxLayout()
            foot.setSpacing(8)
            self.lbl_footer = QLabel("提示：悬停任意键帽可查看 MIDI 音高、物理按键与本曲命中次数。")
            self.lbl_footer.setObjectName("sub")
            foot.addWidget(self.lbl_footer, 1)
            foot.addWidget(btn_close)
            root.addLayout(foot)
        else:
            self.lbl_footer = QLabel("")
            self.lbl_footer.setVisible(False)

    def set_instrument_mode(self, mode: str) -> None:
        self._mode = (mode if mode in (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                      else INSTRUMENT_PIANO_36)
        self.map_widget.set_mode(self._mode)
        self._sync_ui()

    def set_modifier_state(self, mod_state: str) -> None:
        self._mod_state = mod_state if mod_state in ("natural", "LShift", "LCtrl") else "natural"
        self.map_widget.set_modifier_state(self._mod_state)
        self._sync_ui()

    def _on_transpose_changed(self, val: int) -> None:
        self._transpose = int(val)
        self.spin_transpose.setPrefix("+" if self._transpose > 0 else "")
        self.map_widget.set_transpose(self._transpose)
        self._refresh_button_icons()
        self._sync_stats_banner()

    def _refresh_button_icons(self) -> None:
        if UW is None or not hasattr(UW, "make_vector_icon"):
            return
        dark = _is_dark_palette(self)
        dim_col = "#CBD5E1" if dark else "#334155"
        self.btn_mode_guitar.setIcon(UW.make_vector_icon(
            "guitar", "#FFFFFF" if self.btn_mode_guitar.isChecked() else dim_col, 15))
        self.btn_mode_piano.setIcon(UW.make_vector_icon(
            "piano", "#FFFFFF" if self.btn_mode_piano.isChecked() else dim_col, 15))
        self.btn_mod_nat.setIcon(UW.make_vector_icon(
            "keyboard", "#FFFFFF" if self.btn_mod_nat.isChecked() else dim_col, 15))
        self.btn_mod_shift.setIcon(UW.make_vector_icon(
            "spark", "#F4C869" if self.btn_mod_shift.isChecked() else dim_col, 15))
        self.btn_mod_ctrl.setIcon(UW.make_vector_icon(
            "spark", "#38BDF8" if self.btn_mod_ctrl.isChecked() else dim_col, 15))
        if hasattr(self, "btn_close") and not self._embedded:
            self.btn_close.setIcon(UW.make_vector_icon("check", dim_col, 15))

    def _sync_stats_banner(self) -> None:
        has_song = bool(self._raw_used)
        self.lbl_transpose.setVisible(has_song)
        self.spin_transpose.setVisible(has_song)
        if not has_song:
            lo, hi = (52, 87) if self._mode == INSTRUMENT_GUITAR_36 else (60, 95)
            self.lbl_coverage.setText(f"音域 {_midi_to_name(lo)}({lo}) ~ {_midi_to_name(hi)}({hi})")
            self.lbl_hits.setVisible(False)
            return

        st = compute_pitch_stats(self._raw_used, self._mode, self._transpose)
        self.lbl_coverage.setText(f"覆盖 {st['coverage']:.1f}%")
        self.lbl_coverage.setToolTip(f"曲目在当前 36 键音域内的覆盖率：{st['coverage']:.1f}%")
        self.lbl_hits.setVisible(True)
        shift_tag = f"{self._transpose:+d}" if self._transpose != 0 else "0"
        self.lbl_hits.setText(
            f"命中 {st['in_range']}/{st['total']}"
            + (f" · 越界 {st['out_of_range']}" if st["out_of_range"] > 0 else " · 全命中")
        )
        self.lbl_hits.setToolTip(
            f"移调 {shift_tag} 半音后命中 {st['in_range']}/{st['total']} 音"
            + (f"（低音越界 {st['low_out']}，高音越界 {st['high_out']}）"
               if st["out_of_range"] > 0 else "（全部音符在音域内）")
        )

    def _sync_ui(self) -> None:
        is_piano = (self._mode == INSTRUMENT_PIANO_36)
        self.btn_mode_guitar.blockSignals(True)
        self.btn_mode_piano.blockSignals(True)
        self.btn_mode_guitar.setChecked(not is_piano)
        self.btn_mode_piano.setChecked(is_piano)
        self.btn_mode_guitar.blockSignals(False)
        self.btn_mode_piano.blockSignals(False)

        self.btn_mod_nat.setVisible(is_piano)
        self.btn_mod_shift.setVisible(is_piano)
        self.btn_mod_ctrl.setVisible(is_piano)

        if is_piano:
            self.btn_mod_nat.blockSignals(True)
            self.btn_mod_shift.blockSignals(True)
            self.btn_mod_ctrl.blockSignals(True)
            self.btn_mod_nat.setChecked(self._mod_state == "natural")
            self.btn_mod_shift.setChecked(self._mod_state == "LShift")
            self.btn_mod_ctrl.setChecked(self._mod_state == "LCtrl")
            self.btn_mod_nat.blockSignals(False)
            self.btn_mod_shift.blockSignals(False)
            self.btn_mod_ctrl.blockSignals(False)

            if self._mod_state == "LShift":
                self.lbl_rule_hint.setText(
                    "Shift 与 Ctrl 互斥不可同按：按住 LShift 时仅右移 QRT / AFG / ZVB → #1 / #4 / #5，"
                    "其余列（W/E/Y/U、S/D/H/J、X/C/N/M）仍发自然音；钢琴模式无延音。"
                )
            elif self._mod_state == "LCtrl":
                self.lbl_rule_hint.setText(
                    "Shift 与 Ctrl 互斥不可同按：按住 LCtrl 时仅左移 EU / DJ / CM → b3 / b7，"
                    "其余列（Q/W/R/T/Y 等）仍发自然音；钢琴模式无延音。"
                )
            else:
                self.lbl_rule_hint.setText(
                    "Shift 与 Ctrl 互斥不可同按：LShift 仅右移 QRT/AFG/ZVB，LCtrl 仅左移 EU/DJ/CM；"
                    "点击左侧三态按钮可预览移位高亮（钢琴模式无延音，按固定按住时长触发）。"
                )
        else:
            self.lbl_rule_hint.setText(
                "吉他 · 36 键（MIDI 52 E3 ~ 87 D#6）：3 行 × 12 独立按键直接对应半音阶，"
                "无需 Shift/Ctrl 修饰键，长音支持按住延音。"
            )

        self._refresh_button_icons()
        self._sync_stats_banner()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import gui_theme as GT
        GT.apply_theme(app)
    except Exception:
        pass
    demo_used = {60: 14, 61: 4, 62: 9, 64: 18, 67: 12, 69: 7, 72: 11, 75: 5, 79: 6}
    dlg = KeyboardPreviewDialog(INSTRUMENT_PIANO_36, used_pitches=demo_used, transpose=0)
    if "--smoke" in sys.argv:
        dlg.show()
        app.processEvents()
        dlg.close()
        print("KEYBOARD_PREVIEW_SMOKE_OK")
    else:
        dlg.exec_()
