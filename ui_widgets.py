# -*- coding: utf-8 -*-
"""ui_widgets.py -- 《异键演奏》v1.4 星曜玻璃拟态共享视觉组件库。

公开共享契约（供 gui_main / gui_a_legacy / gui_b_legacy / piano_roll / keyboard_preview 复用）：
  1. make_vector_icon(name, color="#E6EDF7", size=18) -> QIcon
  2. StageBackdropWidget(theme_mode="dark", parent=None)
  3. AvatarRingBadge(size=52, parent=None)
  4. HeroBannerWidget(banner_key="guitar", height=108, parent=None)
  5. GlassCard(parent=None)
  6. RangeMeterWidget(parent=None)
  7. NavRailButton(text="", icon_name="", parent=None)
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                         QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QPushButton,
                             QSizePolicy, QWidget)

try:
    from core_utils import ROOT, get_logger
except Exception:
    ROOT = Path(__file__).resolve().parent
    get_logger = None

_ASSETS_DIR = ROOT / "assets"
_UI_ASSETS_DIR = _ASSETS_DIR / "ui"

_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}
_PIXMAP_CACHE: dict[str, QPixmap] = {}


def _log_debug(msg: str, *args) -> None:
    if get_logger is not None:
        try:
            get_logger("ui_widgets").debug(msg, *args)
        except Exception:
            pass


def _load_pixmap_cached(path: Path) -> QPixmap | None:
    key = str(path)
    if key in _PIXMAP_CACHE:
        pm = _PIXMAP_CACHE[key]
        return pm if not pm.isNull() else None
    if not path.is_file():
        return None
    pm = QPixmap(key)
    if pm.isNull():
        _log_debug("Failed to load image asset: %s", key)
        return None
    _PIXMAP_CACHE[key] = pm
    return pm


# ══════════════════════════════════════════════════════════════════════════
#  1. 矢量图标引擎（26 种 QPainter 抗锯齿超采样矢量图标）
# ══════════════════════════════════════════════════════════════════════════
def _draw_icon_path(p: QPainter, name: str) -> None:
    """在 24x24 逻辑坐标系内绘制指定矢量图标。"""
    n = str(name or "").strip().lower()
    pen = p.pen()
    col = pen.color()

    if n == "guitar":
        p.drawLine(QPointF(13.5, 10.5), QPointF(20.0, 4.0))
        p.drawRoundedRect(QRectF(18.5, 2.5, 3.0, 3.0), 1.0, 1.0)
        body = QPainterPath()
        body.moveTo(11.5, 8.5)
        body.cubicTo(8.0, 7.2, 4.5, 9.5, 4.2, 13.0)
        body.cubicTo(3.8, 17.0, 7.2, 20.2, 11.2, 19.8)
        body.cubicTo(14.5, 19.4, 16.6, 16.0, 15.3, 12.5)
        body.closeSubpath()
        p.drawPath(body)
        p.drawEllipse(QPointF(9.5, 14.5), 1.8, 1.8)
    elif n == "piano":
        p.drawRoundedRect(QRectF(3.0, 4.5, 18.0, 15.0), 2.5, 2.5)
        for x in (9.0, 15.0):
            p.drawLine(QPointF(x, 12.5), QPointF(x, 19.5))
        p.setBrush(col)
        p.drawRoundedRect(QRectF(6.8, 4.5, 3.0, 8.2), 0.8, 0.8)
        p.drawRoundedRect(QRectF(14.2, 4.5, 3.0, 8.2), 0.8, 0.8)
    elif n == "wheel":
        p.drawEllipse(QPointF(12.0, 12.0), 8.5, 8.5)
        p.drawEllipse(QPointF(12.0, 12.0), 2.6, 2.6)
        for deg in range(0, 360, 60):
            rad = math.radians(deg)
            p.drawLine(
                QPointF(12.0 + 2.8 * math.cos(rad), 12.0 + 2.8 * math.sin(rad)),
                QPointF(12.0 + 8.3 * math.cos(rad), 12.0 + 8.3 * math.sin(rad)),
            )
    elif n == "ai":
        star = QPainterPath()
        star.moveTo(11.0, 3.0)
        star.quadTo(11.5, 10.5, 19.0, 11.0)
        star.quadTo(11.5, 11.5, 11.0, 19.0)
        star.quadTo(10.5, 11.5, 3.0, 11.0)
        star.quadTo(10.5, 10.5, 11.0, 3.0)
        p.drawPath(star)
        p.setBrush(col)
        p.drawEllipse(QPointF(18.5, 18.0), 1.6, 1.6)
        p.drawEllipse(QPointF(18.5, 5.2), 1.1, 1.1)
    elif n == "library":
        p.drawRoundedRect(QRectF(4.0, 4.0, 4.2, 16.0), 1.2, 1.2)
        p.drawRoundedRect(QRectF(10.0, 4.0, 4.2, 16.0), 1.2, 1.2)
        p.save()
        p.translate(16.2, 4.5)
        p.rotate(-12.0)
        p.drawRoundedRect(QRectF(0.0, 0.0, 4.0, 15.2), 1.2, 1.2)
        p.restore()
    elif n == "play":
        p.setBrush(col)
        poly = QPolygonF([QPointF(7.0, 4.8), QPointF(19.0, 12.0), QPointF(7.0, 19.2)])
        p.drawPolygon(poly)
    elif n == "stop":
        p.setBrush(col)
        p.drawRoundedRect(QRectF(6.0, 6.0, 12.0, 12.0), 2.2, 2.2)
    elif n == "pause":
        p.setBrush(col)
        p.drawRoundedRect(QRectF(6.2, 5.2, 4.0, 13.6), 1.4, 1.4)
        p.drawRoundedRect(QRectF(13.8, 5.2, 4.0, 13.6), 1.4, 1.4)
    elif n == "sun":
        p.drawEllipse(QPointF(12.0, 12.0), 4.2, 4.2)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            p.drawLine(
                QPointF(12.0 + 6.4 * math.cos(rad), 12.0 + 6.4 * math.sin(rad)),
                QPointF(12.0 + 9.0 * math.cos(rad), 12.0 + 9.0 * math.sin(rad)),
            )
    elif n == "moon":
        path = QPainterPath()
        path.moveTo(14.8, 4.2)
        path.arcTo(QRectF(4.2, 4.2, 15.6, 15.6), 68.0, 215.0)
        path.cubicTo(11.0, 16.5, 11.2, 8.2, 14.8, 4.2)
        p.drawPath(path)
        p.setBrush(col)
        p.drawEllipse(QPointF(17.5, 6.5), 1.1, 1.1)
    elif n == "shield":
        path = QPainterPath()
        path.moveTo(12.0, 3.2)
        path.lineTo(19.2, 6.2)
        path.cubicTo(19.2, 13.2, 16.2, 18.0, 12.0, 20.8)
        path.cubicTo(7.8, 18.0, 4.8, 13.2, 4.8, 6.2)
        path.closeSubpath()
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(8.8, 12.0), QPointF(11.2, 14.4), QPointF(15.6, 9.6)]))
    elif n in ("update", "refresh"):
        p.drawArc(QRectF(4.2, 4.2, 15.6, 15.6), 35 * 16, 260 * 16)
        p.drawPolyline(QPolygonF([QPointF(15.5, 4.5), QPointF(19.2, 5.2), QPointF(18.5, 9.0)]))
        if n == "update":
            p.drawLine(QPointF(12.0, 8.5), QPointF(12.0, 15.0))
            p.drawPolyline(QPolygonF([QPointF(9.5, 11.0), QPointF(12.0, 8.2), QPointF(14.5, 11.0)]))
    elif n == "keyboard":
        p.drawRoundedRect(QRectF(2.8, 5.5, 18.4, 13.0), 2.5, 2.5)
        for row_y in (9.0, 12.2):
            for x in (6.0, 9.0, 12.0, 15.0, 18.0):
                p.drawPoint(QPointF(x, row_y))
        p.drawLine(QPointF(7.2, 15.5), QPointF(16.8, 15.5))
    elif n == "roll":
        p.drawRoundedRect(QRectF(3.0, 4.5, 18.0, 15.0), 2.2, 2.2)
        p.drawLine(QPointF(7.5, 4.5), QPointF(7.5, 19.5))
        for y in (9.5, 14.5):
            p.drawLine(QPointF(3.0, y), QPointF(7.5, y))
        p.setBrush(col)
        p.drawRoundedRect(QRectF(9.6, 7.2, 5.4, 2.2), 1.0, 1.0)
        p.drawRoundedRect(QRectF(13.2, 11.2, 5.4, 2.2), 1.0, 1.0)
        p.drawRoundedRect(QRectF(9.6, 15.2, 4.2, 2.2), 1.0, 1.0)
    elif n == "spark":
        star = QPainterPath()
        star.moveTo(10.5, 3.2)
        star.quadTo(11.0, 10.0, 17.8, 10.5)
        star.quadTo(11.0, 11.0, 10.5, 17.8)
        star.quadTo(10.0, 11.0, 3.2, 10.5)
        star.quadTo(10.0, 10.0, 10.5, 3.2)
        p.drawPath(star)
        p.drawLine(QPointF(18.0, 15.0), QPointF(18.0, 20.4))
        p.drawLine(QPointF(15.3, 17.7), QPointF(20.7, 17.7))
    elif n == "folder":
        path = QPainterPath()
        path.moveTo(3.2, 6.2)
        path.lineTo(9.4, 6.2)
        path.lineTo(11.2, 8.4)
        path.lineTo(20.8, 8.4)
        path.lineTo(20.8, 18.5)
        path.lineTo(3.2, 18.5)
        path.closeSubpath()
        p.drawPath(path)
    elif n == "import":
        p.drawLine(QPointF(12.0, 4.2), QPointF(12.0, 14.8))
        p.drawPolyline(QPolygonF([QPointF(8.2, 11.2), QPointF(12.0, 15.2), QPointF(15.8, 11.2)]))
        p.drawPolyline(QPolygonF([QPointF(4.5, 15.5), QPointF(4.5, 19.2), QPointF(19.5, 19.2), QPointF(19.5, 15.5)]))
    elif n == "settings":
        p.drawEllipse(QPointF(12.0, 12.0), 3.4, 3.4)
        p.drawEllipse(QPointF(12.0, 12.0), 7.2, 7.2)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            p.drawLine(
                QPointF(12.0 + 7.2 * math.cos(rad), 12.0 + 7.2 * math.sin(rad)),
                QPointF(12.0 + 9.4 * math.cos(rad), 12.0 + 9.4 * math.sin(rad)),
            )
    elif n == "check":
        p.drawPolyline(QPolygonF([QPointF(5.0, 12.5), QPointF(9.8, 17.2), QPointF(19.2, 7.2)]))
    elif n == "undo":
        p.drawPolyline(QPolygonF([QPointF(8.2, 6.0), QPointF(4.2, 9.8), QPointF(8.2, 13.6)]))
        path = QPainterPath()
        path.moveTo(4.6, 9.8)
        path.lineTo(14.5, 9.8)
        path.cubicTo(19.2, 9.8, 19.2, 17.2, 14.5, 17.2)
        path.lineTo(8.5, 17.2)
        p.drawPath(path)
    elif n == "redo":
        p.drawPolyline(QPolygonF([QPointF(15.8, 6.0), QPointF(19.8, 9.8), QPointF(15.8, 13.6)]))
        path = QPainterPath()
        path.moveTo(19.4, 9.8)
        path.lineTo(9.5, 9.8)
        path.cubicTo(4.8, 9.8, 4.8, 17.2, 9.5, 17.2)
        path.lineTo(15.5, 17.2)
        p.drawPath(path)
    elif n == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6.0, 6.0)
        p.drawLine(QPointF(15.0, 15.0), QPointF(19.8, 19.8))
    elif n == "trash":
        p.drawLine(QPointF(4.5, 6.8), QPointF(19.5, 6.8))
        p.drawRoundedRect(QRectF(9.2, 3.8, 5.6, 3.0), 1.0, 1.0)
        p.drawRoundedRect(QRectF(6.5, 6.8, 11.0, 13.2), 1.6, 1.6)
        p.drawLine(QPointF(10.0, 10.2), QPointF(10.0, 16.5))
        p.drawLine(QPointF(14.0, 10.2), QPointF(14.0, 16.5))
    elif n == "chevron_down":
        p.drawPolyline(QPolygonF([QPointF(6.2, 9.2), QPointF(12.0, 15.0), QPointF(17.8, 9.2)]))
    elif n == "chevron_right":
        p.drawPolyline(QPolygonF([QPointF(9.2, 6.2), QPointF(15.0, 12.0), QPointF(9.2, 17.8)]))
    elif n == "midi":
        p.drawEllipse(QPointF(12.0, 12.0), 8.6, 8.6)
        p.setBrush(col)
        for deg in (20, 65, 90, 115, 160):
            rad = math.radians(deg)
            px = 12.0 + 4.4 * math.cos(rad)
            py = 13.0 - 4.4 * math.sin(rad)
            p.drawEllipse(QPointF(px, py), 0.9, 0.9)
    elif n == "plug":
        p.drawLine(QPointF(9.0, 4.2), QPointF(9.0, 8.8))
        p.drawLine(QPointF(15.0, 4.2), QPointF(15.0, 8.8))
        p.drawRoundedRect(QRectF(6.5, 8.8, 11.0, 6.4), 2.5, 2.5)
        p.drawLine(QPointF(12.0, 15.2), QPointF(12.0, 20.2))
    elif n == "book":
        p.drawLine(QPointF(12.0, 5.2), QPointF(12.0, 19.2))
        left_p = QPainterPath()
        left_p.moveTo(12.0, 5.5)
        left_p.quadTo(8.0, 4.2, 4.0, 5.6)
        left_p.lineTo(4.0, 18.6)
        left_p.quadTo(8.0, 17.4, 12.0, 19.0)
        p.drawPath(left_p)
        right_p = QPainterPath()
        right_p.moveTo(12.0, 5.5)
        right_p.quadTo(16.0, 4.2, 20.0, 5.6)
        right_p.lineTo(20.0, 18.6)
        right_p.quadTo(16.0, 17.4, 12.0, 19.0)
        p.drawPath(right_p)
    else:
        p.drawEllipse(QPointF(12.0, 12.0), 6.0, 6.0)


def make_vector_icon(name: str, color: str = "#E6EDF7", size: int = 18) -> QIcon:
    """绘制锐利抗锯齿矢量 QIcon（支持 2x 超采样与缓存）。"""
    sz = max(10, int(size or 18))
    qc = QColor(color)
    if not qc.isValid():
        qc = QColor("#E6EDF7")
    key = (str(name or "").strip().lower(), qc.name(QColor.HexArgb), sz)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    dpr = 2
    pm = QPixmap(sz * dpr, sz * dpr)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(float(dpr))

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(sz / 24.0, sz / 24.0)
    pen = QPen(qc, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    _draw_icon_path(p, key[0])
    p.end()

    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon


# ══════════════════════════════════════════════════════════════════════════
#  2. 舞台大气底图容器（星曜暗夜 / 星辉浅昼双主题自适应）
# ══════════════════════════════════════════════════════════════════════════
class StageBackdropWidget(QWidget):
    """铺满主窗口底部的动态舞台氛围底图容器。"""

    def __init__(self, theme_mode: str = "dark", parent=None):
        super().__init__(parent)
        self._theme_mode = "light" if str(theme_mode).lower() == "light" else "dark"
        self._scaled_cache: tuple[str, int, int, QPixmap] | None = None

    def set_theme_mode(self, mode: str) -> None:
        norm = "light" if str(mode).lower() == "light" else "dark"
        if norm != self._theme_mode:
            self._theme_mode = norm
            self._scaled_cache = None
            self.update()

    def _get_scaled_bg(self, w: int, h: int) -> QPixmap | None:
        if (self._scaled_cache is not None
                and self._scaled_cache[0] == self._theme_mode
                and self._scaled_cache[1] == w
                and self._scaled_cache[2] == h):
            return self._scaled_cache[3]
        fname = "stage_bg_light.jpg" if self._theme_mode == "light" else "stage_bg_dark.jpg"
        src = _load_pixmap_cached(_UI_ASSETS_DIR / fname)
        if src is None:
            return None
        scaled = src.scaled(max(1, w), max(1, h), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x_off = max(0, (scaled.width() - w) // 2)
        y_off = max(0, (scaled.height() - h) // 2)
        cropped = scaled.copy(x_off, y_off, w, h)
        self._scaled_cache = (self._theme_mode, w, h, cropped)
        return cropped

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = max(1, self.width()), max(1, self.height())
        bg = self._get_scaled_bg(w, h)
        if bg is not None:
            p.drawPixmap(0, 0, bg)
            if self._theme_mode == "dark":
                p.fillRect(0, 0, w, h, QColor(9, 12, 21, 95))
            else:
                p.fillRect(0, 0, w, h, QColor(238, 244, 253, 45))
        else:
            grad = QLinearGradient(0, 0, w, h)
            if self._theme_mode == "dark":
                grad.setColorAt(0.0, QColor("#090C15"))
                grad.setColorAt(0.55, QColor("#0E1529"))
                grad.setColorAt(1.0, QColor("#12162E"))
            else:
                grad.setColorAt(0.0, QColor("#F4F6FB"))
                grad.setColorAt(0.6, QColor("#EBF1FC"))
                grad.setColorAt(1.0, QColor("#E4ECFA"))
            p.fillRect(0, 0, w, h, grad)
        p.end()


# ══════════════════════════════════════════════════════════════════════════
#  3. 左侧导航星环头像徽章
# ══════════════════════════════════════════════════════════════════════════
class AvatarRingBadge(QWidget):
    """圆角头像 + 星辰钴蓝/星辉流金双环光晕描边。"""

    def __init__(self, size: int = 52, parent=None, theme_mode: str = "dark"):
        super().__init__(parent)
        self._size = max(28, int(size or 52))
        self._theme_mode = "light" if str(theme_mode).lower() == "light" else "dark"
        self.setFixedSize(self._size, self._size)

    def set_theme_mode(self, mode: str) -> None:
        norm = "light" if str(mode).lower() == "light" else "dark"
        if norm != self._theme_mode:
            self._theme_mode = norm
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        s = float(self._size)
        outer = QRectF(2.0, 2.0, s - 4.0, s - 4.0)

        dark = (self._theme_mode != "light")
        ring_grad = QLinearGradient(0.0, 0.0, s, s)
        if dark:
            ring_grad.setColorAt(0.0, QColor("#3B6EF5"))
            ring_grad.setColorAt(0.55, QColor("#8B5CF6"))
            ring_grad.setColorAt(1.0, QColor("#F4C869"))
            inner_fill = QColor(17, 22, 38, 215)
        else:
            ring_grad.setColorAt(0.0, QColor("#2F5CE5"))
            ring_grad.setColorAt(0.55, QColor("#6D28D9"))
            ring_grad.setColorAt(1.0, QColor("#D99726"))
            inner_fill = QColor("#F8FAFF")

        # 外晕环
        p.setBrush(inner_fill)
        p.setPen(QPen(QBrush(ring_grad), 2.2))
        p.drawEllipse(outer)

        inner = outer.adjusted(3.0, 3.0, -3.0, -3.0)
        avatar = _load_pixmap_cached(_ASSETS_DIR / "icon.png")
        if avatar is not None:
            clip = QPainterPath()
            clip.addEllipse(inner)
            p.save()
            p.setClipPath(clip)
            scaled = avatar.scaled(
                int(math.ceil(inner.width() * 2)),
                int(math.ceil(inner.height() * 2)),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            p.drawPixmap(inner, scaled, QRectF(scaled.rect()))
            p.restore()
        else:
            p.setPen(QPen(QColor("#F4C869" if dark else "#2F5CE5"), 2.0))
            p.setFont(QFont("Microsoft YaHei UI", max(10, int(s * 0.34)), QFont.Bold))
            p.drawText(inner, Qt.AlignCenter, "♪")

        p.end()


# ══════════════════════════════════════════════════════════════════════════
#  4. 顶部动态主题头图横幅
# ══════════════════════════════════════════════════════════════════════════
class HeroBannerWidget(QWidget):
    """圆角舞台头图横幅（支持 guitar / piano / ai 与双主题左侧高对比遮罩）。"""

    def __init__(self, banner_key: str = "guitar", height: int = 112, parent=None):
        super().__init__(parent)
        self._banner_key = self._norm_key(banner_key)
        self._theme_mode = "dark"
        self._height = max(112, int(height or 112))
        self.setMinimumHeight(self._height)
        self.setMaximumHeight(132)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._content = QHBoxLayout(self)
        self._content.setContentsMargins(18, 12, 18, 12)
        self._content.setSpacing(12)

    @staticmethod
    def _norm_key(key: str) -> str:
        k = str(key or "guitar").strip().lower()
        return k if k in ("guitar", "piano", "ai") else "guitar"

    def set_banner(self, key: str) -> None:
        nk = self._norm_key(key)
        if nk != self._banner_key:
            self._banner_key = nk
            self.update()

    def set_theme_mode(self, mode: str) -> None:
        norm = "light" if str(mode).lower() == "light" else "dark"
        if norm != self._theme_mode:
            self._theme_mode = norm
            self.update()

    def content_layout(self) -> QHBoxLayout:
        return self._content

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, max(10.0, w - 1.0), max(10.0, h - 1.0))
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14.0, 14.0)

        p.save()
        p.setClipPath(clip)

        dark = (self._theme_mode != "light")
        # 底色填充（左侧延伸纯底）
        p.fillRect(rect, QColor("#0B1124") if dark else QColor("#F4F8FF"))

        # 浅昼优先加载专用浅色头图（缺失回退暗图），靠右对齐绘制
        pm = None
        if not dark:
            pm = _load_pixmap_cached(_UI_ASSETS_DIR / f"hero_banner_{self._banner_key}_light.jpg")
        if pm is None:
            pm = _load_pixmap_cached(_UI_ASSETS_DIR / f"hero_banner_{self._banner_key}.jpg")

        draw_x = w * 0.48
        if pm is not None:
            target_h = max(1, int(math.ceil(h)))
            scaled = pm.scaled(max(1, int(math.ceil(h * pm.width() / max(1, pm.height())))),
                               target_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            draw_x = w - scaled.width()
            p.drawPixmap(QPointF(draw_x, 0.0), scaled)
        else:
            fallback = QLinearGradient(0, 0, w, h)
            if dark:
                fallback.setColorAt(0.0, QColor("#0E152B"))
                fallback.setColorAt(1.0, QColor("#1D2A54"))
            else:
                fallback.setColorAt(0.0, QColor("#FFFFFF"))
                fallback.setColorAt(1.0, QColor("#DCE7FA"))
            p.fillRect(rect, fallback)

        # 左侧平滑过渡遮罩：fade_end <= 0.68，右侧 42%~100% 艺术主体 100% 透亮无蒙灰
        seam = max(0.28, min(0.52, draw_x / max(1.0, w)))
        fade_end = min(0.68, seam + 0.16)
        mask = QLinearGradient(0.0, 0.0, w, 0.0)
        if dark:
            mask.setColorAt(0.0, QColor(11, 16, 33, 240))
            mask.setColorAt(seam, QColor(13, 19, 38, 165))
            mask.setColorAt(fade_end, QColor(13, 19, 38, 0))
            mask.setColorAt(1.0, QColor(13, 19, 38, 0))
        else:
            mask.setColorAt(0.0, QColor(252, 254, 255, 235))
            mask.setColorAt(seam, QColor(246, 250, 255, 140))
            mask.setColorAt(fade_end, QColor(244, 248, 255, 0))
            mask.setColorAt(1.0, QColor(244, 248, 255, 0))
        p.fillRect(rect, mask)
        p.restore()

        # 外框描边 + 顶部温润香槟金/星辉晨光高光
        bd_col = QColor(56, 189, 248, 68) if dark else QColor(59, 98, 223, 55)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(bd_col, 1.1))
        p.drawRoundedRect(rect, 14.0, 14.0)

        hi_grad = QLinearGradient(rect.left() + 18, 0.0, rect.right() - 18, 0.0)
        hi_grad.setColorAt(0.0, QColor(244, 200, 105, 0))
        if dark:
            hi_grad.setColorAt(0.28, QColor(244, 200, 105, 130))
            hi_grad.setColorAt(0.72, QColor(56, 189, 248, 110))
        else:
            hi_grad.setColorAt(0.28, QColor(217, 151, 38, 135))
            hi_grad.setColorAt(0.72, QColor(59, 98, 223, 95))
        hi_grad.setColorAt(1.0, QColor(56, 189, 248, 0))
        p.setPen(QPen(QBrush(hi_grad), 1.0))
        p.drawLine(QPointF(rect.left() + 16, rect.top() + 1.0),
                   QPointF(rect.right() - 16, rect.top() + 1.0))
        p.end()


# ══════════════════════════════════════════════════════════════════════════
#  5. 玻璃拟态卡片（QFrame + 顶部微光高光 + 浅昼底部冷蓝浮起投影）
# ══════════════════════════════════════════════════════════════════════════
class GlassCard(QFrame):
    """玻璃拟态卡片容器：沿用 QFrame#card QSS 并绘制双主题立体边缘微光。"""

    def __init__(self, parent=None, object_name: str = "card"):
        super().__init__(parent)
        self.setObjectName(object_name or "card")
        self._theme_mode: str | None = None

    def set_theme_mode(self, mode: str) -> None:
        self._theme_mode = "light" if str(mode).lower() == "light" else "dark"
        self.update()

    def _is_light(self) -> bool:
        if self._theme_mode is not None:
            return self._theme_mode == "light"
        try:
            return QApplication.palette().window().color().lightness() >= 128
        except Exception:
            return False

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        if w > 32:
            if self._is_light():
                hi = QLinearGradient(14.0, 0.0, w - 14.0, 0.0)
                hi.setColorAt(0.0, QColor(255, 255, 255, 0))
                hi.setColorAt(0.30, QColor(255, 255, 255, 225))
                hi.setColorAt(0.75, QColor(255, 255, 255, 195))
                hi.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setPen(QPen(QBrush(hi), 1.2))
                p.drawLine(QPointF(14.0, 1.0), QPointF(w - 14.0, 1.0))
                sh = QLinearGradient(16.0, 0.0, w - 16.0, 0.0)
                sh.setColorAt(0.0, QColor(59, 98, 223, 0))
                sh.setColorAt(0.25, QColor(59, 98, 223, 28))
                sh.setColorAt(0.75, QColor(59, 98, 223, 28))
                sh.setColorAt(1.0, QColor(59, 98, 223, 0))
                p.setPen(QPen(QBrush(sh), 1.5))
                p.drawLine(QPointF(14.0, max(2.0, h - 1.2)), QPointF(w - 14.0, max(2.0, h - 1.2)))
            else:
                hi = QLinearGradient(16.0, 0.0, w - 16.0, 0.0)
                hi.setColorAt(0.0, QColor(255, 255, 255, 0))
                hi.setColorAt(0.35, QColor(255, 255, 255, 42))
                hi.setColorAt(0.70, QColor(244, 200, 105, 34))
                hi.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setPen(QPen(QBrush(hi), 1.0))
                p.drawLine(QPointF(14.0, 1.0), QPointF(w - 14.0, 1.0))
        p.end()


# ══════════════════════════════════════════════════════════════════════════
#  6. 全 88 键音域覆盖热力条
# ══════════════════════════════════════════════════════════════════════════
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_RANGE_MAP = {
    "guitar_wheel": (40, 88),
    "guitar_36": (52, 87),
    "piano_36": (60, 95),
}


class RangeMeterWidget(QWidget):
    """全 88 键（MIDI 21..108）音域覆盖分布热力条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[int, int] = {}
        self._mode = "guitar_36"
        self._transpose = 0
        self._theme_mode = "dark"
        self.setMinimumHeight(44)
        self.setMaximumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, pitches, instrument_mode: str = "guitar_36",
                 transpose: int = 0, theme_mode: str = "dark") -> None:
        norm: dict[int, int] = {}
        if isinstance(pitches, dict):
            for k, v in pitches.items():
                try:
                    p, c = int(k), int(v)
                    if c > 0:
                        norm[p] = norm.get(p, 0) + c
                except (TypeError, ValueError):
                    continue
        elif pitches:
            for item in pitches:
                try:
                    p = int(item)
                    norm[p] = norm.get(p, 0) + 1
                except (TypeError, ValueError):
                    continue
        self._counts = norm
        self._mode = str(instrument_mode or "guitar_36")
        self._transpose = int(transpose or 0)
        self._theme_mode = "light" if str(theme_mode).lower() == "light" else "dark"
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        dark = (self._theme_mode != "light")
        w, h = float(self.width()), float(self.height())
        outer = QRectF(0.5, 0.5, max(10.0, w - 1.0), max(10.0, h - 1.0))

        bg_col = QColor(13, 18, 32, 195) if dark else QColor("#E5EDF9")
        bd_col = QColor("#232C47") if dark else QColor("#C7D6EE")
        p.setBrush(bg_col)
        p.setPen(QPen(bd_col, 1.0))
        p.drawRoundedRect(outer, 9.0, 9.0)

        info_w = 148.0
        bar_rect = QRectF(10.0, 5.0, max(60.0, w - info_w - 18.0), h - 10.0)
        lo, hi = _RANGE_MAP.get(self._mode, (52, 87))

        # 88 键底槽 (MIDI 21..108)，顶部留 12px 标签带避免边界标与柱重叠
        key_lo, key_hi = 21, 108
        span = float(key_hi - key_lo + 1)
        kw = bar_rect.width() / span

        p.setBrush(QColor(21, 29, 51, 150) if dark else QColor(216, 227, 245, 195))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(bar_rect, 5.0, 5.0)

        # 目标有效音域窗口高亮
        win_x1 = bar_rect.left() + (lo - key_lo) * kw
        win_x2 = bar_rect.left() + (hi - key_lo + 1) * kw
        win_rect = QRectF(win_x1, bar_rect.top(), max(4.0, win_x2 - win_x1), bar_rect.height())
        win_bg = QColor(59, 110, 245, 48) if dark else QColor(59, 98, 223, 28)
        p.setBrush(win_bg)
        p.setPen(QPen(QColor("#38BDF8") if dark else QColor("#3B62DF"), 1.1))
        p.drawRoundedRect(win_rect, 4.0, 4.0)

        # 左右边界刻度标置于顶部 12px 标签带内
        lo_txt = f"{_NOTE_NAMES[lo % 12]}{lo // 12 - 1}"
        hi_txt = f"{_NOTE_NAMES[hi % 12]}{hi // 12 - 1}"
        p.setPen(QColor("#94A3B8") if dark else QColor("#3B62DF"))
        p.setFont(QFont("Consolas", 7, QFont.Bold))
        p.drawText(QRectF(win_rect.left() + 3, bar_rect.top() + 1, 36, 11),
                   Qt.AlignLeft | Qt.AlignTop, lo_txt)
        p.drawText(QRectF(win_rect.right() - 39, bar_rect.top() + 1, 36, 11),
                   Qt.AlignRight | Qt.AlignTop, hi_txt)

        # 移调后频次柱（顶部下移 12px 避让标签）
        plot_top = bar_rect.top() + 12.0
        plot_h = max(6.0, bar_rect.bottom() - 3.0 - plot_top)
        shifted: dict[int, int] = {}
        total = 0
        in_range = 0
        for raw_p, cnt in self._counts.items():
            total += cnt
            sp = raw_p + self._transpose
            if lo <= sp <= hi:
                in_range += cnt
            if key_lo <= sp <= key_hi:
                shifted[sp] = shifted.get(sp, 0) + cnt
        max_cnt = max(shifted.values()) if shifted else 0

        if max_cnt > 0:
            for pitch, cnt in shifted.items():
                ratio = 0.25 + 0.75 * (cnt / float(max_cnt))
                col_h = max(4.0, plot_h * ratio)
                cx = bar_rect.left() + (pitch - key_lo) * kw
                cy = bar_rect.bottom() - 3.0 - col_h
                inside = (lo <= pitch <= hi)
                if dark:
                    c = QColor("#F4C869" if (inside and ratio > 0.65) else
                               ("#38BDF8" if inside else "#F43F5E"))
                else:
                    c = QColor("#D97706" if (inside and ratio > 0.65) else
                               ("#2563EB" if inside else "#E11D48"))
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(cx + 0.3, cy, max(1.6, kw - 0.6), col_h), 1.0, 1.0)

        # 右侧覆盖率读数
        info_rect = QRectF(bar_rect.right() + 8.0, 4.0, info_w - 6.0, h - 8.0)
        if total > 0:
            cov = in_range / float(total) * 100.0
            p.setPen(QColor("#F4C869") if (dark and cov >= 95.0) else
                     (QColor("#38BDF8") if dark else QColor("#2F5CE5")))
            p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
            p.drawText(QRectF(info_rect.left(), info_rect.top(), info_rect.width(), info_rect.height() * 0.56),
                       Qt.AlignLeft | Qt.AlignVCenter, f"覆盖 {cov:.1f}%")
            p.setPen(QColor("#94A3B8") if dark else QColor("#64748B"))
            p.setFont(QFont("Consolas", 8))
            p.drawText(QRectF(info_rect.left(), info_rect.top() + info_rect.height() * 0.52,
                              info_rect.width(), info_rect.height() * 0.45),
                       Qt.AlignLeft | Qt.AlignVCenter, f"命中 {in_range}/{total}")
        else:
            p.setPen(QColor("#94A3B8") if dark else QColor("#64748B"))
            p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            p.drawText(info_rect, Qt.AlignVCenter | Qt.AlignLeft, f"有效域 {lo}~{hi}")

        p.end()


# ══════════════════════════════════════════════════════════════════════════
#  7. 左侧导航栏按钮（选中带流金/钴蓝竖向光条与矢量图标）
# ══════════════════════════════════════════════════════════════════════════
class NavRailButton(QPushButton):
    """左侧导航栏按钮：支持矢量图标与选中态竖向流金光条。"""

    def __init__(self, text: str = "", icon_name: str = "", parent=None):
        super().__init__(text, parent)
        self._icon_name = str(icon_name or "")
        self._theme_mode = "dark"
        self.setObjectName("navBtn")
        self.setCheckable(True)
        self.setMinimumHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.toggled.connect(lambda _c: self._refresh_icon())
        self._refresh_icon()

    def set_theme_mode(self, mode: str) -> None:
        self._theme_mode = "light" if str(mode).lower() == "light" else "dark"
        self._refresh_icon()
        self.update()

    def _refresh_icon(self) -> None:
        if not self._icon_name:
            return
        if self.isChecked():
            col = "#F4C869" if self._theme_mode == "dark" else "#1E40AF"
        else:
            col = "#94A3B8" if self._theme_mode == "dark" else "#475569"
        self.setIcon(make_vector_icon(self._icon_name, col, 18))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            grad = QLinearGradient(0.0, 6.0, 0.0, float(self.height() - 6))
            if self._theme_mode == "dark":
                grad.setColorAt(0.0, QColor("#F4C869"))
                grad.setColorAt(1.0, QColor("#38BDF8"))
            else:
                grad.setColorAt(0.0, QColor("#2F5CE5"))
                grad.setColorAt(1.0, QColor("#D99726"))
            p.setBrush(grad)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(2.5, 7.0, 3.5, max(12.0, self.height() - 14.0)), 1.8, 1.8)
            p.end()
