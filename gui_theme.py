# -*- coding: utf-8 -*-
"""gui_theme.py -- 《异键演奏》v1.4 星曜玻璃拟态双主题设计系统。

设计口径：
  * 字体：`Microsoft YaHei UI` 10pt（抗锯齿，PreferAntialias）；字号四级
    appTitle 20px / sectionTitle 16px / stepTitle 15px / 正文 13px。
  * 控件：普通按钮 36px、主按钮 40px、演奏键 44px；输入框统一 34px。
  * 主题：星曜暗夜（#090C15 / 半透明玻璃卡片）与四层学院星昼玻璃（#E8EFFA / 珍珠白卡）。
  * 主色系：星辰钴蓝、星辉流金、星裙紫罗兰、电光青、绯樱赤光与翡翠绿演奏渐变。

对外接口（与旧版兼容）：
  `setup_app_font(app)` / `build_stylesheet(mode)` / `apply_theme(app, mode=None)`
  / `load_mode()` / `save_mode(mode)`；旧名 `DARK_QSS` / `LIGHT_QSS` 仍保留。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "gui_settings.json"

UI_FONT = "Microsoft YaHei UI"
MONO_FONT = "Consolas"

# ── 设计令牌 ────────────────────────────────────────────────────────────────
DARK = {
    "bg": "#090C15",
    "card": "rgba(17, 22, 38, 212)",
    "card_solid": "#111626",
    "inner": "rgba(13, 18, 32, 192)",
    "nav_bg": "rgba(10, 14, 26, 224)",
    "nav_line": "rgba(56, 189, 248, 38)",
    "deck_bg": "rgba(12, 18, 36, 228)",
    "deck_line": "rgba(56, 189, 248, 72)",
    "line": "#232C47",
    "text": "#E6EDF7",
    "text_dim": "#94A3B8",
    "text_mute": "#64748B",
    "input": "#131B2E",
    "input_line": "#2A3656",
    "hover": "#1D2844",
    "blue": "#3B6EF5",
    "blue_hover": "#5B8BFF",
    "primary_grad": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B6EF5, stop:1 #6366F1)",
    "primary_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5B8BFF, stop:1 #818CF8)",
    "gold": "#F4C869",
    "gold_bg": "rgba(244, 200, 105, 0.14)",
    "gold_line": "rgba(244, 200, 105, 0.45)",
    "violet": "#8B5CF6",
    "violet_bg": "rgba(139, 92, 246, 0.16)",
    "violet_line": "rgba(139, 92, 246, 0.48)",
    "accent_bg": "rgba(59, 110, 245, 0.18)",
    "accent_line": "rgba(56, 189, 248, 0.45)",
    "cyan": "#38BDF8",
    "green": "#059669",
    "green_grad": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #0D9488)",
    "green_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10B981, stop:1 #14B8A6)",
    "green_text": "#34D399",
    "accent_btn_bg": "#38BDF8",
    "accent_btn_bd": "#38BDF8",
    "accent_fg": "#052E2B",
    "accent_hover_bg": "#5B8BFF",
    "accent_hover_bd": "#5B8BFF",
    "accent_hover_fg": "#FFFFFF",
    "preset_checked_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B6EF5, stop:1 #6366F1)",
    "preset_checked_bd": "#F4C869",
    "preset_checked_fg": "#FFFFFF",
    "guide_bg": "rgba(18, 28, 54, 210)",
    "guide_line": "#2A4278",
    "guide_text": "#E0ECFF",
    "danger": "#F43F5E",
    "danger_line": "#9F1239",
    "ghost_bg": "rgba(15, 22, 41, 205)",
    "ghost_text": "#CBD5E1",
    "pill_bg": "rgba(15, 22, 41, 210)",
    "pill_text": "#CBD5E1",
    "pill_checked": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B6EF5, stop:1 #4F46E5)",
    "nav_hover": "rgba(59, 110, 245, 0.16)",
    "nav_checked": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(59, 110, 245, 0.34), stop:1 rgba(139, 92, 246, 0.18))",
    "nav_checked_bd": "rgba(244, 200, 105, 0.48)",
    "nav_checked_fg": "#FFFFFF",
    "tree_hover": "rgba(59, 110, 245, 0.14)",
    "tree_sel": "rgba(59, 110, 245, 0.28)",
    "tree_sel_text": "#FFFFFF",
    "groove": "#232C47",
}
LIGHT = {
    "bg": "#E8EFFA",
    "card": "rgba(255, 255, 255, 236)",
    "card_solid": "#FFFFFF",
    "inner": "rgba(244, 248, 255, 224)",
    "nav_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(233, 240, 253, 236), stop:1 rgba(223, 233, 250, 242))",
    "nav_line": "rgba(148, 173, 218, 165)",
    "deck_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 248), stop:1 rgba(242, 247, 255, 242))",
    "deck_line": "rgba(59, 98, 223, 92)",
    "line": "#CEDAF0",
    "text": "#0F172A",
    "text_dim": "#475569",
    "text_mute": "#94A3B8",
    "input": "#F7F9FE",
    "input_line": "#CBD5E1",
    "hover": "#E9EFFA",
    "blue": "#2F5CE5",
    "blue_hover": "#1D4ED8",
    "primary_grad": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2F5CE5, stop:1 #4F46E5)",
    "primary_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1D4ED8, stop:1 #4338CA)",
    "gold": "#C2821A",
    "gold_bg": "#FEF3C7",
    "gold_line": "#F59E0B",
    "violet": "#6D28D9",
    "violet_bg": "#EDE9FE",
    "violet_line": "#A78BFA",
    "accent_bg": "#E0ECFF",
    "accent_line": "#93C5FD",
    "cyan": "#0284C7",
    "green": "#059669",
    "green_grad": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #0D9488)",
    "green_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #0F766E)",
    "green_text": "#047857",
    "accent_btn_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E8F1FF, stop:1 #EFEAFF)",
    "accent_btn_bd": "#93B4F8",
    "accent_fg": "#1D4ED8",
    "accent_hover_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #DCEAFF, stop:1 #E5DEFF)",
    "accent_hover_bd": "#608FF5",
    "accent_hover_fg": "#1E3A8A",
    "preset_checked_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #EAF1FF, stop:1 #EFEBFF)",
    "preset_checked_bd": "#3B62DF",
    "preset_checked_fg": "#1E3A8A",
    "guide_bg": "#EFF6FF",
    "guide_line": "#BFDBFE",
    "guide_text": "#1E3A8A",
    "danger": "#E11D48",
    "danger_line": "#FDA4AF",
    "ghost_bg": "rgba(255, 255, 255, 224)",
    "ghost_text": "#334155",
    "pill_bg": "rgba(247, 249, 254, 232)",
    "pill_text": "#334155",
    "pill_checked": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2F5CE5, stop:1 #4338CA)",
    "nav_hover": "rgba(59, 98, 223, 0.10)",
    "nav_checked": "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(59, 98, 223, 0.16), stop:1 rgba(124, 58, 237, 0.10))",
    "nav_checked_bd": "rgba(59, 98, 223, 0.36)",
    "nav_checked_fg": "#1E3A8A",
    "tree_hover": "#EDF2FC",
    "tree_sel": "#E0EAFF",
    "tree_sel_text": "#1E3A8A",
    "groove": "#DCE3F2",
}


def setup_app_font(app):
    """把应用字体强制设为 10pt Microsoft YaHei UI，并开启抗锯齿。"""
    from PyQt5.QtGui import QFont
    f = QFont(UI_FONT, 10)
    try:
        f.setStyleStrategy(QFont.PreferAntialias)
    except Exception:
        pass
    app.setFont(f)
    return f


def build_stylesheet(mode="dark"):
    """按主题生成完整 QSS。

    兼容两种调用：``build_stylesheet(True/False)`` 或
    ``build_stylesheet("dark"/"light")``。运行日志框（QPlainTextEdit）的
    底色/字色/边框跟随主题切换，浅色主题为 #F8FAFC 白底深字。
    """
    if isinstance(mode, str):
        dark = str(mode).lower() != "light"
    else:
        dark = bool(mode)
    c = DARK if dark else LIGHT
    if dark:
        log_bg, log_fg, log_bd = "#090D16", "#E2E8F0", "#334155"
        stage_chunk = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                       "stop:0 #3B6EF5, stop:0.55 #8B5CF6, stop:1 #38BDF8)")
    else:
        log_bg, log_fg, log_bd = "#F8FAFC", "#0F172A", "#CBD5E1"
        stage_chunk = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                       "stop:0 #2F5CE5, stop:0.6 #6D28D9, stop:1 #0284C7)")
    return """
    QWidget {{
        background: {bg}; color: {text};
        font-family: "{font}"; font-size: 13px;
    }}
    QMainWindow, QDialog {{ background: {bg}; }}
    QWidget#stageHost, QWidget#stagePage, QScrollArea#stageScroll,
    QScrollArea#stageScroll > QWidget > QWidget {{
        background: transparent;
    }}
    QLabel {{ background: transparent; color: {text}; }}
    QLabel#appTitle {{ color: {title}; font-size: 17px; font-weight: 800; letter-spacing: 0.4px; }}
    QLabel#title {{ color: {title}; font-size: 20px; font-weight: 800; }}
    QLabel#heroTitle {{ color: {title}; font-size: 18px; font-weight: 800; }}
    QLabel#heroSub {{ color: {text_dim}; font-size: 12px; font-weight: 600; }}
    QLabel#sectionTitle {{ color: {title}; font-size: 16px; font-weight: 700; }}
    QLabel#cardtitle {{ color: {title}; font-size: 16px; font-weight: 700; }}
    QLabel#stepTitle {{ color: {cyan}; font-size: 15px; font-weight: 700; }}
    QLabel#section {{ color: {text_dim}; font-size: 13px; font-weight: 700; }}
    QLabel#subtitle, QLabel#cardsub, QLabel#sub, QLabel#fieldname {{
        color: {text_dim}; font-size: 12px; }}
    QLabel#roleDesc {{ color: {text_dim}; font-size: 12px; }}
    QLabel#dev {{ color: {cyan}; font-weight: 600; }}
    QLabel#status {{ color: {green_text}; font-size: 13px; }}
    QLabel#statusBadge {{
        background: {badge_bg}; color: {green_text};
        border: 1px solid {badge_line}; border-radius: 9px;
        padding: 6px 12px; min-height: 26px;
        font-size: 12px; font-weight: 600; }}
    QLabel#accentPill {{
        background: {accent_bg}; color: {cyan};
        border: 1px solid {accent_line}; border-radius: 9px;
        padding: 2px 9px; font-size: 12px; font-weight: 700; }}
    QLabel#goldPill {{
        background: {gold_bg}; color: {gold};
        border: 1px solid {gold_line}; border-radius: 9px;
        padding: 2px 9px; font-size: 12px; font-weight: 700; }}
    QLabel#violetPill {{
        background: {violet_bg}; color: {violet};
        border: 1px solid {violet_line}; border-radius: 9px;
        padding: 2px 9px; font-size: 12px; font-weight: 700; }}
    QLabel#guide_text {{
        color: {guide_text}; background: {guide_bg};
        border: 1px solid {guide_line}; border-radius: 10px;
        padding: 8px 12px; font-size: 13px; }}
    QLabel#badge {{
        background: {blue}; color: #FFFFFF; border-radius: 17px;
        font-weight: 700; font-size: 16px; }}

    QFrame#navRail {{
        background: {nav_bg};
        border: none;
        border-right: 1px solid {nav_line}; }}
    QFrame#card, QFrame#hero, QFrame#guide, QGroupBox {{
        background: {card}; border: 1px solid {line}; border-radius: 12px; }}
    QFrame#transportDeck {{
        background: {deck_bg}; border: 1px solid {deck_line}; border-radius: 13px; }}
    QFrame#card_inner, QFrame#subcard {{
        background: {inner}; border: 1px solid {line}; border-radius: 10px; }}
    QGroupBox {{ margin-top: 12px; padding: 14px 12px 10px 12px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px;
                        color: {text_dim}; font-weight: 600; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {input}; color: {text}; border: 1px solid {input_line};
        border-radius: 8px; padding: 4px 9px; min-height: 34px; font-size: 14px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {blue}; }}
    QComboBox QAbstractItemView {{
        background: {card_solid}; color: {text}; border: 1px solid {line};
        selection-background-color: {blue}; selection-color: #FFFFFF; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {text_mute}; }}

    QPushButton {{
        background: {input}; color: {text}; border: 1px solid {input_line};
        border-radius: 8px; padding: 4px 10px; min-height: 36px;
        font-size: 13px; font-weight: 600; }}
    QPushButton:hover {{ background: {hover}; border-color: {blue}; }}
    QPushButton:pressed {{ background: {inner}; }}
    QPushButton:disabled {{ color: {text_mute}; background: {inner};
                            border-color: {line}; }}
    QPushButton#navBtn {{
        background: transparent; color: {text_dim};
        border: 1px solid transparent; border-radius: 10px;
        text-align: left; padding: 6px 12px 6px 16px;
        min-height: 40px; font-size: 13px; font-weight: 600; }}
    QPushButton#navBtn:hover {{
        background: {nav_hover}; color: {text}; border-color: transparent; }}
    QPushButton#navBtn:checked {{
        background: {nav_checked}; color: {nav_checked_fg};
        border: 1px solid {nav_checked_bd}; font-weight: 700; }}
    QPushButton#primary {{
        background: {primary_grad}; border: 1px solid {blue}; color: #FFFFFF;
        min-height: 40px; font-size: 15px; font-weight: 700; }}
    QPushButton#primary:hover {{
        background: {primary_hover}; border-color: {blue_hover}; }}
    QPushButton#playBtn, QPushButton#play {{
        background: {green_grad}; border: 1px solid {green}; color: #FFFFFF;
        min-height: 44px; min-width: 112px; padding: 4px 10px;
        font-size: 14px; font-weight: 800; }}
    QPushButton#playBtn:hover, QPushButton#play:hover {{
        background: {green_hover}; border-color: #10B981; }}
    QPushButton#accent {{
        background: {accent_btn_bg}; border: 1px solid {accent_btn_bd}; color: {accent_fg};
        font-weight: 700; }}
    QPushButton#accent:hover {{
        background: {accent_hover_bg}; border-color: {accent_hover_bd}; color: {accent_hover_fg}; }}
    QPushButton#ghost {{
        background: {ghost_bg}; color: {ghost_text}; border-color: {line}; }}
    QPushButton#ghost:hover {{
        background: {hover}; border-color: {blue}; color: {text}; }}
    QPushButton#danger {{
        color: {danger}; border-color: {danger_line}; background: {ghost_bg}; }}
    QPushButton#danger:hover {{
        border-color: {danger}; background: {hover}; }}
    QPushButton#modePill {{
        background: {pill_bg}; color: {pill_text};
        border: 1px solid {line}; border-radius: 15px;
        padding: 4px 10px; min-height: 30px;
        font-size: 13px; font-weight: 600; }}
    QPushButton#modePill:hover {{
        background: {hover}; border-color: {blue}; color: {text}; }}
    QPushButton#modePill:checked {{
        background: {pill_checked}; border: 1.5px solid {gold};
        color: #FFFFFF; font-weight: 700; }}
    QPushButton#modePill:checked:hover {{
        background: {primary_hover}; border: 1.5px solid {gold}; }}
    QPushButton#presetBtn {{
        min-height: 46px; text-align: left; padding: 5px 12px;
        font-size: 13px; font-weight: 600; }}
    QPushButton#presetBtn:checked {{
        background: {preset_checked_bg}; border: 2px solid {preset_checked_bd}; color: {preset_checked_fg}; }}

    QTreeWidget, QTreeWidget#midiList {{
        background: {inner}; color: {text};
        border: 1px solid {line}; border-radius: 10px;
        padding: 4px; outline: 0;
        alternate-background-color: {card_solid}; }}
    QTreeWidget::item, QTreeWidget#midiList::item {{
        padding: 5px 8px; margin: 1px 0;
        border-radius: 6px; border: 1px solid transparent; }}
    QTreeWidget::item:hover, QTreeWidget#midiList::item:hover {{
        background: {tree_hover}; }}
    QTreeWidget::item:selected, QTreeWidget#midiList::item:selected {{
        background: {tree_sel}; color: {tree_sel_text};
        border: 1px solid {blue}; font-weight: 600; }}
    QHeaderView::section {{
        background: {card_solid}; color: {text_dim};
        border: none; border-bottom: 1px solid {line};
        padding: 6px 8px; font-size: 12px; font-weight: 700; }}

    QCheckBox, QRadioButton {{
        background: transparent; color: {text}; spacing: 6px;
        font-size: 13px; font-weight: 600; padding: 2px 0; }}
    QRadioButton#roleRadio {{ font-size: 14px; font-weight: 700; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
    QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {{
        border: 1px solid {input_line}; background: {input}; }}
    QCheckBox::indicator:unchecked {{ border-radius: 4px; }}
    QRadioButton::indicator:unchecked {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        border: 1px solid {blue}; background: {blue}; }}
    QCheckBox::indicator:checked {{ border-radius: 4px; }}
    QRadioButton::indicator:checked {{ border-radius: 8px; }}

    QTabWidget#stageTabs::pane {{
        border: none; background: transparent; top: 0px; }}
    QTabWidget::pane {{
        border: 1px solid {line}; background: {bg};
        border-radius: 11px; top: -1px; }}
    QTabBar::tab {{
        background: {card}; color: {text_dim};
        padding: 9px 20px; margin-right: 5px;
        border: 1px solid {line}; border-bottom: none;
        border-top-left-radius: 10px; border-top-right-radius: 10px;
        font-size: 13px; font-weight: 600; }}
    QTabBar::tab:selected {{
        background: {bg}; color: {title};
        border-color: {line}; border-top: 2px solid {blue};
        border-bottom: 2px solid {cyan}; font-weight: 700; }}
    QTabBar::tab:hover:!selected {{
        color: {text}; background: {hover}; }}

    QProgressBar {{
        background: {input}; border: 1px solid {input_line};
        border-radius: 6px; text-align: center; color: {text};
        min-height: 10px; }}
    QProgressBar::chunk {{ background: {blue}; border-radius: 5px; }}
    QProgressBar#stageBar {{
        background: {input}; border: 1px solid {input_line};
        border-radius: 9px; text-align: center; color: {title};
        min-height: 26px; max-height: 26px;
        font-size: 13px; font-weight: 700; }}
    QProgressBar#stageBar::chunk {{
        background: {stage_chunk}; border-radius: 8px; }}

    QSlider::groove:horizontal {{
        height: 6px; background: {groove}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{
        background: {blue}; border-radius: 3px; }}
    QSlider::add-page:horizontal {{
        background: {groove}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        background: {cyan}; border: 2px solid #FFFFFF;
        width: 16px; height: 16px; margin: -6px 0; border-radius: 9px; }}
    QSlider::handle:horizontal:hover {{ background: {blue_hover}; }}
    QSlider::groove:vertical {{
        width: 6px; background: {groove}; border-radius: 3px; }}
    QSlider::handle:vertical {{
        background: {cyan}; border: 2px solid #FFFFFF;
        width: 16px; height: 16px; margin: 0 -6px; border-radius: 9px; }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QWidget#adv_inner {{ background: transparent; }}

    QPlainTextEdit {{
        background-color: {log_bg};
        color: {log_fg};
        font-family: "{mono}", "{font}", monospace;
        font-size: 13px;
        border: 1px solid {log_bd};
        border-radius: 8px;
        padding: 8px;
        selection-background-color: {blue};
        selection-color: #FFFFFF; }}
    QPlainTextEdit#log {{
        background-color: {log_bg};
        color: {log_fg};
        border: 1px solid {log_bd};
        border-radius: 8px;
        font-family: "{mono}", "{font}", monospace;
        font-size: 13px;
        padding: 8px;
        selection-background-color: {blue};
        selection-color: #FFFFFF; }}
    QTextEdit {{
        background: {input}; color: {text}; border: 1px solid {input_line};
        border-radius: 8px; }}

    QMenu, QMessageBox, QFileDialog, QDialog {{ background: {card_solid}; color: {text}; }}
    QToolTip {{ background: {card_solid}; color: {text};
               border: 1px solid {line}; border-radius: 6px; padding: 5px 8px; }}

    QScrollBar:vertical {{ background: transparent; width: 9px; border-radius: 4px;
                          margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {input_line}; min-height: 28px;
                                  border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {text_mute}; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; border-radius: 4px;
                            margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {input_line}; min-width: 28px;
                                    border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """.format(
        font=UI_FONT, mono=MONO_FONT,
        log_bg=log_bg, log_fg=log_fg, log_bd=log_bd,
        stage_chunk=stage_chunk,
        title="#FFFFFF" if c is DARK else "#0F172A",
        badge_bg="#0B2A20" if c is DARK else "#ECFDF5",
        badge_line="#14532D" if c is DARK else "#A7F3D0",
        **c
    )


DARK_QSS = build_stylesheet("dark")
LIGHT_QSS = build_stylesheet("light")


def load_mode():
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        mode = str(data.get("theme") or "").lower()
        if mode in ("dark", "light"):
            return mode
    except Exception:
        pass
    return None


def save_mode(mode):
    mode = "light" if str(mode).lower() == "light" else "dark"
    try:
        data = {}
        if SETTINGS.is_file():
            try:
                data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["theme"] = mode
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except Exception:
        pass
    return mode


def _is_os_dark():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return int(val) == 0
    except Exception:
        return True


def apply_theme(app, mode=None):
    """应用主题：Fusion 调色板 + 10pt 微软雅黑 UI + 全套 QSS。"""
    from PyQt5.QtGui import QColor, QPalette
    mode = (mode or load_mode() or ("dark" if _is_os_dark() else "light")).lower()
    mode = "light" if mode == "light" else "dark"
    c = LIGHT if mode == "light" else DARK
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["inner"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["input"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(c["blue"]))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["card_solid"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    app.setPalette(pal)
    setup_app_font(app)
    app.setStyleSheet(build_stylesheet(mode))
    return mode
