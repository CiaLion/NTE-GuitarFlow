# -*- coding: utf-8 -*-
"""gui_b_legacy.py -- 《异键演奏》v1.4 演奏台界面（三层音乐工作站 + 多乐器编译与播放器）。

界面功能：
  ① 左侧曲库     本地 MIDI 曲库（235px 玻璃卡片）：搜索 / 乐曲库搜谱 / 刷新与导入
  ② 顶部动态头图 吉他·轮盘 / 吉他·钢琴键 36键 / 钢琴·36键 胶囊切换 + 曲名与卷帘入口
  ③ 中部编配区   轮盘 3 角色与校准 / 36 键音轨范围·移调·智能移调·键位图与 88 键热力分布条
  ④ DAW 控制舱   开始/停止（F8/F12）、实时进度时间码、准备引导与通道自检
  ⑤ 帧率与日志   30/60/90 帧预设、跟随本曲参数（带并发竞态保护）、可折叠运行日志
"""
import inspect
import os
import re
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
TEST_MIDI_DIR = os.path.join(PROJECT_DIR, "assets", "test_midis")
OUT_DIR = os.path.join(PROJECT_DIR, "out")

for _cand_plug in (
    os.path.join(PROJECT_DIR, ".venv", "Lib", "site-packages", "PyQt5", "Qt5", "plugins"),
    os.path.join(sys.prefix, "Lib", "site-packages", "PyQt5", "Qt5", "plugins"),
):
    if os.path.isdir(_cand_plug) and not os.environ.get("QT_PLUGIN_PATH"):
        os.environ["QT_PLUGIN_PATH"] = _cand_plug
        break

from PyQt5.QtCore import QEvent, QSize, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup,
                             QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                             QFileDialog, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QRadioButton,
                             QScrollArea, QShortcut, QSizePolicy, QSlider,
                             QSpinBox, QStackedWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

import input_engine as ie
import midi_compiler as MC
import midi_loader as ML
import player_config as PC
import recorder as REC
import timeline_runner as TR
from game_io import GameIO

try:
    import core_utils as CU
except Exception:
    CU = None

try:
    import ui_widgets as UW
except Exception:
    UW = None

try:
    import gui_theme as GT
except Exception:
    GT = None

try:
    import keyboard_layout as KL
except Exception:
    KL = None

_LOG = (CU.get_logger("gui_b_legacy")
        if (CU is not None and hasattr(CU, "get_logger")) else None)

INSTRUMENT_GUITAR_WHEEL = getattr(KL, "INSTRUMENT_GUITAR_WHEEL", "guitar_wheel")
INSTRUMENT_GUITAR_36 = getattr(KL, "INSTRUMENT_GUITAR_36", "guitar_36")
INSTRUMENT_PIANO_36 = getattr(KL, "INSTRUMENT_PIANO_36", "piano_36")
INSTRUMENT_MODES = (
    INSTRUMENT_GUITAR_WHEEL,
    INSTRUMENT_GUITAR_36,
    INSTRUMENT_PIANO_36,
)
INSTRUMENT_RANGES = getattr(KL, "INSTRUMENT_RANGES", {
    INSTRUMENT_GUITAR_WHEEL: (40, 88),
    INSTRUMENT_GUITAR_36: (52, 87),
    INSTRUMENT_PIANO_36: (60, 95),
})
INSTRUMENT_SHORT_CN = {
    INSTRUMENT_GUITAR_WHEEL: "吉他 · 轮盘",
    INSTRUMENT_GUITAR_36: "吉他 · 钢琴键 36键",
    INSTRUMENT_PIANO_36: "钢琴 · 36键",
}
INSTRUMENT_RANGE_BADGE = {
    INSTRUMENT_GUITAR_WHEEL: "E2~E6 · 品格/和弦",
    INSTRUMENT_GUITAR_36: "E3~D#6 · 36键有延音",
    INSTRUMENT_PIANO_36: "C4~B6 · 36键无延音",
}
KB_TRACK_SCOPES = (
    ("all", "全部音轨合弹"),
    ("melody", "仅主旋律"),
    ("accomp", "仅伴奏与和声"),
)
KB_ARRANGE_MODES = (
    ("raw", "原谱直出"),
    ("solo", "1P 独奏 · 旋律+伴奏"),
    ("duet_lead", "1P 主音 · 纯旋律"),
    ("duet_rhythm", "2P 伴奏 · 柱式/律动"),
    ("duet_rhythm_fret", "2P 伴奏 · 琶音/根音"),
)
KB_ARP_PATTERNS = (
    ("ascending", "从低到高"),
    ("pingpong", "往返折返"),
    ("bass_chord", "根音交替"),
)


def _supports_kw(fn, name):
    """检查函数签名是否接受指定关键字参数，兼容模块分步联调期。"""
    try:
        sig = inspect.signature(fn)
        if name in sig.parameters:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD
                   for p in sig.parameters.values())
    except Exception:
        return False


def _filter_supported_kwargs(fn, kwargs):
    """按目标函数实际签名过滤可选关键字参数。"""
    try:
        sig = inspect.signature(fn)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values()):
            return dict(kwargs)
        return {k: v for k, v in kwargs.items() if k in sig.parameters}
    except Exception:
        return dict(kwargs)


def _refresh_widget_style(widget):
    """动态切换 objectName 后刷新 Qt 样式表。"""
    try:
        st = widget.style()
        st.unpolish(widget)
        st.polish(widget)
        widget.update()
    except Exception as exc:
        if _LOG:
            _LOG.debug("refresh_widget_style failed: %s", exc)


def _current_theme_mode():
    """实时检测当前生效主题 ('dark' 或 'light')，优先读取应用调色板亮度。"""
    try:
        app = QApplication.instance()
        if app is not None:
            col = app.palette().window().color()
            if col.isValid():
                return "dark" if col.lightness() < 128 else "light"
    except Exception:
        pass
    if GT is not None and hasattr(GT, "load_mode"):
        try:
            m = str(GT.load_mode() or "dark").lower()
            return "light" if m == "light" else "dark"
        except Exception:
            pass
    return "dark"


def _make_vector_icon(name, color="#E6EDF7", size=18):
    """安全调用 ui_widgets.make_vector_icon，未就绪时返回空 QIcon。"""
    if UW is not None and hasattr(UW, "make_vector_icon"):
        try:
            return UW.make_vector_icon(name, color=color, size=size)
        except Exception as exc:
            if _LOG:
                _LOG.debug("make_vector_icon(%s) failed: %s", name, exc)
    return QIcon()


def _make_glass_card(object_name="card"):
    """创建玻璃拟态卡片；未加载 ui_widgets 时回退 QFrame。"""
    if UW is not None and hasattr(UW, "GlassCard"):
        try:
            return UW.GlassCard(object_name=object_name or "card")
        except Exception as exc:
            if _LOG:
                _LOG.debug("GlassCard init failed: %s", exc)
    card = QFrame()
    card.setObjectName(object_name or "card")
    return card


class _FallbackHeroBanner(QFrame):
    """ui_widgets.HeroBannerWidget 尚未就绪时的轻量化头图兜底容器。"""

    def __init__(self, banner_key="guitar", height=108, parent=None):
        super(_FallbackHeroBanner, self).__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(int(height))
        self.setMaximumHeight(int(height) + 16)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(14, 10, 14, 10)
        self._lay.setSpacing(12)
        self._banner = banner_key
        self._theme = "dark"

    def set_banner(self, key):
        self._banner = key

    def set_theme_mode(self, mode):
        self._theme = mode

    def content_layout(self):
        return self._lay


class _FallbackRangeMeter(QWidget):
    """ui_widgets.RangeMeterWidget 尚未就绪时的轻量化热力条兜底。"""

    def __init__(self, parent=None):
        super(_FallbackRangeMeter, self).__init__(parent)
        self.setFixedHeight(36)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lbl = QLabel("88 键音域分布（选歌后显示）")
        self._lbl.setObjectName("sub")
        lay.addWidget(self._lbl)

    def set_data(self, pitches, instrument_mode="guitar_36", transpose=0, theme_mode="dark"):
        lo, hi = INSTRUMENT_RANGES.get(instrument_mode, (52, 87))
        ps = [int(p) + int(transpose or 0) for p in (pitches or [])]
        if not ps:
            self._lbl.setText("88 键音域分布（目标 %d~%d · 选歌后显示）" % (lo, hi))
            return
        hit = sum(1 for p in ps if lo <= p <= hi)
        pct = hit * 100.0 / float(len(ps))
        self._lbl.setText("88 键分布：目标 [%d~%d] 命中 %d/%d (%.0f%%)" % (lo, hi, hit, len(ps), pct))


def _create_hero_banner(banner_key="guitar", height=108):
    if UW is not None and hasattr(UW, "HeroBannerWidget"):
        try:
            return UW.HeroBannerWidget(banner_key=banner_key, height=height)
        except Exception as exc:
            if _LOG:
                _LOG.debug("HeroBannerWidget init failed: %s", exc)
    return _FallbackHeroBanner(banner_key=banner_key, height=height)


def _create_range_meter():
    if UW is not None and hasattr(UW, "RangeMeterWidget"):
        try:
            return UW.RangeMeterWidget()
        except Exception as exc:
            if _LOG:
                _LOG.debug("RangeMeterWidget init failed: %s", exc)
    return _FallbackRangeMeter()


def _fmt_mmss(sec):
    """将秒数格式化为 MM:SS 时间码。"""
    s = max(0, int(round(float(sec or 0.0))))
    return "%02d:%02d" % (s // 60, s % 60)

ROLE_ORDER = (MC.ROLE_SOLO, MC.ROLE_DUET_LEAD, MC.ROLE_DUET_RHYTHM_FRET,
              MC.ROLE_DUET_RHYTHM)
ROLE_TITLE = {
    MC.ROLE_SOLO: "1P 单人独奏 (Solo 指弹)",
    MC.ROLE_DUET_LEAD: "双人合奏 · 1P 主音",
    MC.ROLE_DUET_RHYTHM_FRET: "双人合奏 · 2P 伴奏（琶音/根音）",
    MC.ROLE_DUET_RHYTHM: "双人合奏 · 2P 伴奏（柱式/扫弦）",
}


def _fit_screen(widget, width, height, min_w=560, min_h=420):
    """按屏幕可用尺寸安全调整窗口大小（委托 core_utils.fit_screen）。"""
    if CU is not None and hasattr(CU, "fit_screen"):
        try:
            w, h = CU.fit_screen(widget, width, height, min_w=min_w, min_h=min_h)
            widget.setMinimumSize(min(int(min_w), w), min(int(min_h), h))
            return widget
        except Exception as exc:
            if _LOG:
                _LOG.debug("core_utils.fit_screen failed: %s", exc)
    try:
        avail = QApplication.desktop().availableGeometry(widget)
        width = min(int(width), int(avail.width() * 0.94))
        height = min(int(height), int(avail.height() * 0.90))
        min_w = min(int(min_w), int(avail.width() * 0.70))
        min_h = min(int(min_h), int(avail.height() * 0.65))
    except Exception:
        pass
    widget.resize(max(min_w, width), max(min_h, height))
    widget.setMinimumSize(min_w, min_h)
    return widget


ROLE_TIP = {
    MC.ROLE_SOLO: "主旋律 + 同把位双音与颗粒琶音智能合流",
    MC.ROLE_DUET_LEAD: "纯品格模式：主旋律与间奏 Solo",
    MC.ROLE_DUET_RHYTHM_FRET: "和弦/品格 Z 键智能混切：全音域纯拨弦分解琶音",
    MC.ROLE_DUET_RHYTHM: "和弦/品格 Z 键智能混切：多弦同按柱式和弦 / 扫弦律动",
}


def _timing_dep_text(key):
    """每一项参数在面板第 4 列显示的简明作用说明。"""
    return {
        "min_action_interval": "全局基准：进入换格/翻页预算",
        "fret_click_hold": "点格按住时长",
        "pluck_hold": "数字键按住时长",
        "sustain_max": "游戏侧约 4.05s 到顶",
        "select_click_hold": "和弦选格按住时长",
        "strum_park_wait": "扫弦前挪开指针后等待",
        "strum_click_hold": "扫弦按住时长",
        "strum_hold": "0 = 点一下；>0 = 按住延音",
        "page_key_hold": "翻页键按住时长",
        "page_turn_gap": "进入翻页预算",
        "after_page_key_release": "翻页键松开后根音生效延迟",
        "settle_after_page_turn": "直接生效",
        "settle_same_page": "直接生效",
        "settle_page_turn": "直接生效",
        "sustain_min": "判断门槛，不是等待",
        "repluck_gap": "同弦重拨最小间隔",
        "start_delay": "只加在时间轴开头",
        "preschedule_lead": "额外提前量",
    }.get(key, "直接生效")


def _same_path(a, b):
    """两个路径是否指向同一个文件（大小写不敏感的简化判断）。"""
    try:
        return (os.path.abspath(str(a)).lower() == os.path.abspath(str(b)).lower())
    except Exception:
        return False


class CompileWorker(threading.Thread):
    """后台编译线程（避免阻塞主界面）。"""

    def __init__(self, path, role, done, fail, enable_arpeggio=True,
                 arpeggio_pattern="pingpong", enable_chord_rhythm=True,
                 chord_strum_mode=False, timing=None, max_prune_ratio=0.0,
                 instrument_mode=INSTRUMENT_GUITAR_WHEEL,
                 kb_track_scope="all", transpose=0,
                 kb_arrange_mode="raw", kb_auto_accomp=True,
                 kb_enable_arpeggio=True, kb_enable_chord_rhythm=True,
                 kb_strum_roll=False, kb_arp_pattern="ascending",
                 kb_solo_arpeggio=None, kb_p2_arpeggio=None):
        super(CompileWorker, self).__init__(daemon=True)
        self.path, self.role = path, role
        self.done, self.fail = done, fail
        self.enable_arpeggio = enable_arpeggio
        self.arpeggio_pattern = arpeggio_pattern
        self.enable_chord_rhythm = enable_chord_rhythm
        self.chord_strum_mode = chord_strum_mode
        self.timing = timing
        self.max_prune_ratio = max_prune_ratio
        self.instrument_mode = instrument_mode or INSTRUMENT_GUITAR_WHEEL
        self.kb_track_scope = kb_track_scope or "all"
        self.transpose = int(transpose or 0)
        self.kb_arrange_mode = str(kb_arrange_mode or "raw")
        self.kb_auto_accomp = bool(kb_auto_accomp)
        self.kb_enable_arpeggio = bool(kb_enable_arpeggio)
        self.kb_solo_arpeggio = (bool(kb_enable_arpeggio) if kb_solo_arpeggio is None
                                 else bool(kb_solo_arpeggio))
        self.kb_p2_arpeggio = (bool(kb_enable_arpeggio) if kb_p2_arpeggio is None
                               else bool(kb_p2_arpeggio))
        self.kb_enable_chord_rhythm = bool(kb_enable_chord_rhythm)
        self.kb_strum_roll = bool(kb_strum_roll)
        self.kb_arp_pattern = str(kb_arp_pattern or "ascending")

    def run(self):
        try:
            if (self.instrument_mode == INSTRUMENT_GUITAR_WHEEL
                    and self.role in (MC.ROLE_DUET_RHYTHM, MC.ROLE_DUET_RHYTHM_FRET)):
                meta = MC.read_meta(self.path)
                has_chords = any(MC.midi_adapter.is_chords_track(n)
                                 for n in (meta.get("track_names") or []))
                if not has_chords:
                    self.done(self._empty_timeline())
                    return
            kwargs = {
                "role": self.role,
                "enable_arpeggio": self.enable_arpeggio,
                "arpeggio_pattern": self.arpeggio_pattern,
                "enable_chord_rhythm": self.enable_chord_rhythm,
                "chord_strum_mode": self.chord_strum_mode,
                "timing": self.timing,
                "max_prune_ratio": self.max_prune_ratio,
                "instrument_mode": self.instrument_mode,
                "kb_track_scope": self.kb_track_scope,
                "transpose": self.transpose,
                "kb_arrange_mode": self.kb_arrange_mode,
                "kb_auto_accomp": self.kb_auto_accomp,
                "kb_enable_arpeggio": self.kb_enable_arpeggio,
                "kb_enable_chord_rhythm": self.kb_enable_chord_rhythm,
                "kb_strum_roll": self.kb_strum_roll,
                "kb_arp_pattern": self.kb_arp_pattern,
                "kb_solo_arpeggio": self.kb_solo_arpeggio,
                "kb_p2_arpeggio": self.kb_p2_arpeggio,
            }
            if (self.instrument_mode != INSTRUMENT_GUITAR_WHEEL
                    and not _supports_kw(MC.compile_file, "instrument_mode")
                    and KL is not None
                    and hasattr(KL, "compile_keyboard_song")):
                tl = KL.compile_keyboard_song(
                    self.path, self.instrument_mode,
                    timing=self.timing,
                    track_scope=self.kb_track_scope,
                    transpose=self.transpose,
                    arrange_mode=self.kb_arrange_mode,
                    enable_auto_accomp=self.kb_auto_accomp,
                    enable_arpeggio=self.kb_enable_arpeggio,
                    enable_chord_rhythm=self.kb_enable_chord_rhythm,
                    strum_roll=self.kb_strum_roll,
                    arp_pattern=self.kb_arp_pattern,
                    solo_arpeggio=self.kb_solo_arpeggio,
                    p2_arpeggio=self.kb_p2_arpeggio)
            else:
                tl = MC.compile_file(
                    self.path, **_filter_supported_kwargs(MC.compile_file, kwargs))
            self.done(tl)
        except Exception as exc:
            self.fail(exc)

    def _empty_timeline(self):
        return {
            "version": 1, "role": self.role,
            "role_cn": MC.ROLE_CN.get(self.role, self.role),
            "kind": "chord", "source": os.path.abspath(self.path),
            "start": {"page": 1, "sector": 1, "mode_cn": "和弦", "wheel": ""},
            "stats": {"frames": 0, "notes": 0, "voices": 0, "events": 0,
                      "turns": 0, "clicks": 0, "plucks": 0, "strums": 0,
                      "switches": 0, "late": 0, "max_late": 0.0,
                      "duration": 0.0, "hw_events": 0, "no_chord_track": True},
            "compile_stats": {}, "hw_stats": {"events": 0, "turns": 0, "late": 0,
                                              "max_late": 0.0, "hw_events": 0,
                                              "duration": 0.0},
            "adapter": {"no_chord_track": True,
                        "chords": {"tracks": 0, "no_chord_track": True}},
            "timing": {}, "events": [],
        }


class PlayWorker(threading.Thread):
    """后台演奏线程（支持同步内录系统声音与倍速机械脉冲保护）。"""

    def __init__(self, timeline, speed, sync, log, progress, done, rec=None):
        super(PlayWorker, self).__init__(daemon=True)
        self.timeline, self.speed, self.sync = timeline, speed, sync
        self.log, self.progress, self.done = log, progress, done
        self.rec = rec
        self.stop_requested = False
        self.paused = False
        self.runner = None

    def run(self):
        io = GameIO()
        self.runner = TR.TimelineRunner(io, log=self.log,
                                        on_progress=lambda a, b, i: self.progress(a, b),
                                        on_focus_lost=self._focus_lost,
                                        on_pause_state_changed=self._pause_state_changed)
        if self.rec is not None:
            try:
                self.rec.start()
            except Exception as exc:
                self.log("! 录音没能开始：%s（演奏照常）" % exc)
                self.rec = None
        stats = {}
        try:
            play_tl = self.timeline
            run_speed = float(self.speed or 1.0)
            if abs(run_speed - 1.0) > 1e-4:
                if hasattr(TR, "reschedule_for_speed"):
                    play_tl = TR.reschedule_for_speed(self.timeline, run_speed)
                    run_speed = 1.0
                elif hasattr(MC, "reschedule_for_speed"):
                    play_tl = MC.reschedule_for_speed(self.timeline, run_speed)
                    run_speed = 1.0
            stats = self.runner.run(
                play_tl, speed=run_speed, sync_start=self.sync,
                stop_flag=lambda: self.stop_requested,
                pause_flag=lambda: self.paused)
        finally:
            if self.rec is not None:
                path = self.rec.stop(keep=not self.stop_requested)
                if path:
                    self.log("录音已保存：%s（%.1f 秒）" % (path, self.rec.recorded))
                elif self.stop_requested:
                    self.log("演奏被手动中断 → 本次录音已作废删除")
        self.done(stats)

    def pause(self, reason="手动暂停"):
        self.paused = True
        if self.runner is not None:
            try:
                self.runner.pause(reason)
            except Exception:
                pass
        elif self.rec is not None:
            try:
                self.rec.pause()
            except Exception:
                pass

    def _pause_state_changed(self, paused, reason):
        if self.rec is None:
            return
        try:
            if paused:
                self.rec.pause()
            else:
                self.rec.resume()
        except Exception:
            pass

    def _focus_lost(self):
        self.paused = True

    def resume(self):
        if self.runner is not None and not self.runner.resume():
            self.log("! 请先点击切回游戏窗口后再按 F8 继续（程序不会抢焦点）")
            return False
        self.paused = False
        return True

    def stop(self):
        self.stop_requested = True


class MidiTreeWidget(QTreeWidget):
    def __init__(self, owner, parent=None):
        super(MidiTreeWidget, self).__init__(parent)
        self.owner = owner

    def mimeData(self, items):
        data = super(MidiTreeWidget, self).mimeData(items)
        paths = []
        for it in items:
            val = it.data(0, Qt.UserRole)
            if val and os.path.isfile(str(val)):
                paths.append(str(val))
        if paths:
            data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return data

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
        else:
            super(MidiTreeWidget, self).dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
        else:
            super(MidiTreeWidget, self).dragMoveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasUrls():
            self.owner._midi_drop_files(ev)
            ev.acceptProposedAction()
        else:
            super(MidiTreeWidget, self).dropEvent(ev)


class MainWindow(QMainWindow):
    sig_compiled = pyqtSignal(object)
    sig_failed = pyqtSignal(object)
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(float, float)
    sig_finished = pyqtSignal(object)
    sig_devices = pyqtSignal(object)
    sig_song_timing = pyqtSignal(object)
    sig_diag_status = pyqtSignal(str)
    sig_stop_play = pyqtSignal()

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle("《异键演奏》v1.4")
        _fit_screen(self, 960, 780, 660, 520)
        self.setAcceptDrops(True)
        self.timelines = {}
        self.path = None
        self.custom_role_notes = {}
        self.player = None
        self.io = GameIO()
        self.role = MC.ROLE_SOLO
        self._role_pick = MC.ROLE_SOLO
        self._hot_last = {}
        self._hotkey_timer = None
        self.timing_spins = {}
        self._diag_status_lbl = None
        self._diag_timer = None
        self._diag_busy = False
        self._kb_preview_dlg = None
        self._midi_live_dlg = None
        self._compile_seq = 0
        self._timing_seq = 0
        self._raw_notes_cache = None
        self._log_expanded = False
        self._total_duration = 0.0

        cfg = PC.load()
        mode = str(cfg.get("instrument_mode") or INSTRUMENT_GUITAR_WHEEL)
        if mode not in INSTRUMENT_MODES:
            mode = INSTRUMENT_GUITAR_WHEEL
        self.instrument_mode = mode
        scope = str(cfg.get("kb_track_scope") or "all")
        if scope not in ("all", "melody", "accomp"):
            scope = "all"
        self.kb_track_scope = scope
        arr_mode = str(cfg.get("kb_arrange_mode") or "raw")
        if arr_mode not in dict(KB_ARRANGE_MODES):
            arr_mode = "raw"
        self.kb_arrange_mode = arr_mode
        self.kb_auto_accomp = bool(cfg.get("kb_auto_accomp", True))
        self.kb_enable_arpeggio = bool(cfg.get("kb_enable_arpeggio", True))
        self.kb_solo_arpeggio = bool(cfg.get("kb_solo_arpeggio", self.kb_enable_arpeggio))
        self.kb_p2_arpeggio = bool(cfg.get("kb_p2_arpeggio", self.kb_enable_arpeggio))
        self.kb_enable_chord_rhythm = bool(cfg.get("kb_enable_chord_rhythm", True))
        self.kb_strum_roll = bool(cfg.get("kb_strum_roll", False))
        arp_pat = str(cfg.get("kb_arp_pattern") or "ascending")
        if arp_pat not in dict(KB_ARP_PATTERNS):
            arp_pat = "ascending"
        self.kb_arp_pattern = arp_pat
        self._studio_inline = None
        self._kb_preview_inline = None
        try:
            tr = int(cfg.get("transpose", 0) or 0)
        except (TypeError, ValueError):
            tr = 0
        self.transpose = max(-12, min(12, tr))

        self.sig_compiled.connect(self._on_compiled)
        self.sig_failed.connect(self._on_failed)
        self.sig_log.connect(self._append_log)
        self.sig_progress.connect(self._on_progress)
        self.sig_finished.connect(self._on_finished)
        self.sig_devices.connect(self._on_devices)
        self.sig_song_timing.connect(self._on_song_timing)
        self.sig_diag_status.connect(self._on_diag_status)
        self.sig_stop_play.connect(self._on_midi_live_before_start_main)

        self._build_ui()
        self.role = self._effective_role()
        self._update_switch_hints(self.role)
        self._apply_instrument_ui_state()
        self._sync_theme_widgets()
        self._hotkeys()

    # out/ 中的中间过程文件不列入曲库
    MIDI_INTERMEDIATE_STEMS = ("main_melody", "chords", "chords_merged",
                               "melody_vocal", "melody_instrumental",
                               "transcription")

    def _build_midi_library(self):
        """左侧曲库栏（宽 202px，玻璃拟态）：标题 + 搜索 + 乐曲库搜谱 + 树形曲库 + 刷新/导入。"""
        box = _make_glass_card("card")
        box.setFixedWidth(202)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.lbl_lib_icon = QLabel()
        self.lbl_lib_icon.setFixedSize(18, 18)
        head.addWidget(self.lbl_lib_icon)
        title = QLabel("本地 MIDI 曲库")
        title.setObjectName("section")
        head.addWidget(title, 1)
        lay.addLayout(head)

        self.edit_midi_search = QLineEdit()
        self.edit_midi_search.setPlaceholderText("搜索曲名…")
        self.edit_midi_search.setClearButtonEnabled(True)
        self.edit_midi_search.textChanged.connect(self._filter_midi_list)
        lay.addWidget(self.edit_midi_search)

        self.btn_studio = QPushButton("搜谱 / 多轨工坊")
        self.btn_studio.setObjectName("accent")
        self.btn_studio.setCheckable(True)
        self.btn_studio.setMinimumHeight(32)
        self.btn_studio.setToolTip("在当前页面内切换在线搜谱、多轨分轨与人声对齐（无需弹窗）")
        self.btn_studio.clicked.connect(self._toggle_studio_stage)
        lay.addWidget(self.btn_studio)

        self.midi_list = MidiTreeWidget(self)
        self.midi_list.setObjectName("midiList")
        self.midi_list.setHeaderHidden(True)
        self.midi_list.setColumnCount(1)
        self.midi_list.setAlternatingRowColors(True)
        self.midi_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.midi_list.setTextElideMode(Qt.ElideRight)
        self.midi_list.setMinimumWidth(0)
        self.midi_list.setDragDropMode(QAbstractItemView.DragDrop)
        self.midi_list.setDragEnabled(True)
        self.midi_list.setAcceptDrops(True)
        self.midi_list.setDropIndicatorShown(True)
        self.midi_list.setDefaultDropAction(Qt.CopyAction)
        self.midi_list.itemClicked.connect(self._on_midi_item_clicked)
        self.midi_list.itemDoubleClicked.connect(self._on_midi_item_clicked)
        self.midi_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.midi_list.customContextMenuRequested.connect(self._midi_menu)
        sc_f2 = QShortcut(QKeySequence("F2"), self.midi_list, activated=self._midi_rename)
        sc_f2.setContext(Qt.WidgetWithChildrenShortcut)
        sc_del = QShortcut(QKeySequence("Delete"), self.midi_list, activated=self._midi_delete)
        sc_del.setContext(Qt.WidgetWithChildrenShortcut)
        lay.addWidget(self.midi_list, 1)

        foot_btns = QHBoxLayout()
        foot_btns.setSpacing(6)
        self.btn_midi_refresh = QPushButton("刷新")
        self.btn_midi_refresh.setMinimumHeight(30)
        self.btn_midi_refresh.setToolTip("重新扫描内置测试曲与 out/ 目录")
        self.btn_midi_refresh.clicked.connect(self.refresh_midi_list)
        foot_btns.addWidget(self.btn_midi_refresh, 1)
        self.btn_midi_import = QPushButton("导入")
        self.btn_midi_import.setMinimumHeight(30)
        self.btn_midi_import.setToolTip(
            "从电脑任意位置选 .mid / .midi，自动复制到项目 out/ 目录归档")
        self.btn_midi_import.clicked.connect(self.import_midi_files)
        foot_btns.addWidget(self.btn_midi_import, 1)
        lay.addLayout(foot_btns)

        self.lbl_midi_count = QLabel("（正在扫描…）")
        self.lbl_midi_count.setObjectName("sub")
        lay.addWidget(self.lbl_midi_count)
        return box

    MULTIPART_PREFIXES = ("part0_", "part1_", "part2_", "part3_", "all_parts_")

    def _midi_library_tree(self):
        """返回 (分组列表, 顶层单曲列表)；分组 = (标题, [(名, 路径)], 目录)。"""
        groups, singles = [], []
        demo = os.path.join(TEST_MIDI_DIR, "sample_demo.mid")
        if os.path.isfile(demo):
            singles.append(("[内置测试] sample_demo", demo))
        try:
            names = sorted(os.listdir(OUT_DIR), key=lambda x: x.lower())
        except Exception:
            names = []
        for name in names:
            path = os.path.join(OUT_DIR, name)
            if os.path.isdir(path):
                if name.startswith("_") or name.lower().startswith("temp_sep"):
                    continue
                kids = []
                try:
                    for fn in sorted(os.listdir(path), key=lambda x: x.lower()):
                        low = fn.lower()
                        if not low.endswith((".mid", ".midi")):
                            continue
                        if os.path.splitext(low)[0] in self.MIDI_INTERMEDIATE_STEMS:
                            continue
                        kids.append((fn, os.path.join(path, fn)))
                except Exception:
                    kids = []
                title = ("📁 %s (%d首)" % (name, len(kids)) if kids
                         else "📁 %s (空)" % name)
                groups.append((title, kids, path))
                continue
            low = name.lower()
            if not low.endswith((".mid", ".midi")):
                continue
            if os.path.splitext(low)[0] in self.MIDI_INTERMEDIATE_STEMS:
                continue
            singles.append((name, path))
        multi = [x for x in singles if x[0].lower().startswith(self.MULTIPART_PREFIXES)]
        if multi:
            groups.insert(0, ("📁 多声部合奏工程 (%d首)" % len(multi), multi, OUT_DIR))
            singles = [x for x in singles if x not in multi]
        return groups, singles

    def refresh_midi_list(self):
        """重扫曲库（扒谱完成、卷帘另存、导入后都会自动调用）。"""
        if not hasattr(self, "midi_list"):
            return
        keep = getattr(self, "_midi_selected_path", None) or self.path
        try:
            self.midi_list.clear()
            groups, singles = self._midi_library_tree()
            count = 0
            for title, kids, folder in groups:
                top = QTreeWidgetItem([title])
                top.setData(0, Qt.UserRole + 2, folder)
                top.setToolTip(0, folder)
                self.midi_list.addTopLevelItem(top)
                for name, path in kids:
                    item = QTreeWidgetItem([name])
                    item.setData(0, Qt.UserRole, path)
                    item.setToolTip(0, path)
                    top.addChild(item)
                    count += 1
            for name, path in singles:
                item = QTreeWidgetItem([name])
                item.setData(0, Qt.UserRole, path)
                item.setToolTip(0, path)
                self.midi_list.addTopLevelItem(item)
                count += 1
            self.midi_list.expandAll()
            self._midi_total = count
            if keep:
                self._select_midi_path(keep)
            self._filter_midi_list()
            self.lbl_midi_count.setText("共 %d 首（点一下即加载）" % count)
        except Exception as exc:
            self._append_log("× 曲库刷新失败：%s" % exc)

    def _select_midi_path(self, path):
        target = os.path.abspath(str(path))
        for i in range(self.midi_list.topLevelItemCount()):
            top = self.midi_list.topLevelItem(i)
            items = [top] + [top.child(j) for j in range(top.childCount())]
            for item in items:
                got = item.data(0, Qt.UserRole)
                if got and os.path.abspath(str(got)) == target:
                    self.midi_list.setCurrentItem(item)
                    return

    def _filter_midi_list(self, _text=None):
        """按搜索框过滤，命中项自动展开父节点。"""
        if not hasattr(self, "midi_list"):
            return
        key = (self.edit_midi_search.text() if hasattr(self, "edit_midi_search") else "")
        key = str(key or "").strip().lower()
        shown = 0
        for i in range(self.midi_list.topLevelItemCount()):
            top = self.midi_list.topLevelItem(i)
            if top.childCount():
                hit_any = False
                for j in range(top.childCount()):
                    child = top.child(j)
                    hit = (not key) or (key in child.text(0).lower())
                    child.setHidden(not hit)
                    if hit:
                        hit_any = True
                        shown += 1
                top.setHidden(not hit_any)
                if key and hit_any:
                    top.setExpanded(True)
            else:
                hit = (not key) or (key in top.text(0).lower())
                top.setHidden(not hit)
                if hit:
                    shown += 1
        if hasattr(self, "lbl_midi_count"):
            if key:
                self.lbl_midi_count.setText("匹配 %d 首（点一下即加载）" % shown)
            else:
                self.lbl_midi_count.setText(
                    "共 %d 首（点一下即加载）" % int(getattr(self, "_midi_total", shown)))

    def _midi_menu(self, pos):
        item = self.midi_list.itemAt(pos)
        if item is None:
            return
        self.midi_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("新建文件夹…", self._midi_new_folder)
        menu.addAction("重命名 (F2)", self._midi_rename)
        menu.addAction("删除 (Del)", self._midi_delete)
        menu.addAction("打开所在文件夹", self._midi_reveal)
        menu.exec_(self.midi_list.viewport().mapToGlobal(pos))

    def _midi_target(self):
        item = self.midi_list.currentItem()
        if item is None:
            return None
        folder = item.data(0, Qt.UserRole + 2)
        if folder:
            return str(folder)
        path = item.data(0, Qt.UserRole)
        return str(path) if path else None

    def _midi_folder_of(self, item=None):
        item = item if item is not None else self.midi_list.currentItem()
        if item is None:
            return OUT_DIR
        folder = item.data(0, Qt.UserRole + 2)
        if folder:
            return str(folder)
        path = item.data(0, Qt.UserRole)
        if path and os.path.isfile(str(path)):
            return os.path.dirname(str(path))
        return OUT_DIR

    def _midi_new_folder(self):
        base = self._midi_folder_of()
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        name = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "").strip()).strip(" ._")
        if not ok or not name:
            return
        path = os.path.join(base, name)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "新建失败", str(exc))
            return
        self.refresh_midi_list()

    def _midi_drop_files(self, ev):
        folder = self._midi_folder_of(self.midi_list.itemAt(ev.pos()))
        src = ev.source()
        moved = 0
        for url in ev.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if not (os.path.isfile(path) and path.lower().endswith((".mid", ".midi"))):
                continue
            dest = os.path.join(folder, os.path.basename(path))
            if os.path.abspath(dest) == os.path.abspath(path):
                continue
            try:
                if src is self.midi_list:
                    shutil.move(path, dest)
                else:
                    shutil.copy2(path, dest)
                moved += 1
            except Exception as exc:  # noqa: BLE001
                self._append_log("× 拖入失败：%s" % exc)
        if moved:
            self._append_log("已拖入 %d 个 MIDI → %s"
                             % (moved, os.path.basename(folder) or "out"))
            self.refresh_midi_list()

    def _midi_after_change(self, old_path, new_path=None, was_dir=False):
        cur = str(self.path or "")
        old_abs = os.path.abspath(str(old_path))
        same_file = bool(cur) and os.path.abspath(cur) == old_abs
        inside_dir = bool(cur) and was_dir and cur.startswith(old_abs + os.sep)
        if (same_file or inside_dir) and new_path:
            self.path = new_path
            self._raw_notes_cache = None
            self.lbl_file.setText(os.path.basename(new_path))
        elif same_file or inside_dir:
            self.path = None
            self.timelines = {}
            self._raw_notes_cache = None
            self.lbl_file.setText("（还没选歌）")
        self.refresh_midi_list()

    def _midi_rename(self):
        path = self._midi_target()
        if not path or not os.path.exists(path):
            return
        is_dir = os.path.isdir(path)
        base = os.path.basename(path.rstrip("\\/"))
        stem = base if is_dir else os.path.splitext(base)[0]
        new, ok = QInputDialog.getText(self, "重命名", "新名称：", text=stem)
        new = (new or "").strip()
        if not ok or not new or new == stem:
            return
        if not is_dir:
            ext = os.path.splitext(base)[1] or ".mid"
            if not new.lower().endswith((".mid", ".midi")):
                new += ext
        dest = os.path.join(os.path.dirname(path), new)
        if os.path.exists(dest):
            QMessageBox.warning(self, "重命名失败", "已存在同名文件：%s" % new)
            return
        try:
            os.rename(path, dest)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        moved = os.path.join(dest, os.path.relpath(str(self.path), path)) if (
            is_dir and self.path and os.path.abspath(str(self.path)).startswith(
                os.path.abspath(path) + os.sep)) else None
        self._midi_after_change(path, moved if is_dir else dest, was_dir=is_dir)

    def _midi_delete(self):
        path = self._midi_target()
        if not path or not os.path.exists(path):
            return
        name = os.path.basename(path.rstrip("\\/"))
        was_dir = os.path.isdir(path)
        if QMessageBox.question(
                self, "从曲库删除",
                "确定要从曲库删除“%s”吗？\n（文件将移至 logs/trash 回收夹，可随时找回）" % name,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        trash = os.path.join(PROJECT_DIR, "logs", "trash")
        try:
            os.makedirs(trash, exist_ok=True)
            dest = os.path.join(trash, "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), name))
            shutil.move(path, dest)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._midi_after_change(path, None, was_dir=was_dir)

    def _midi_reveal(self):
        path = self._midi_target()
        if not path:
            return
        try:
            if os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(OUT_DIR)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打不开目录", str(exc))

    def _on_midi_item_clicked(self, item, _col=0):
        """点曲目节点 → 自动切回演奏台并立即加载编译。"""
        path = item.data(0, Qt.UserRole) if item is not None else None
        if not path or not os.path.isfile(str(path)):
            return
        self.show_play_stage()
        if self.path and os.path.abspath(str(path)) == os.path.abspath(str(self.path)):
            return
        self._midi_selected_path = str(path)
        self.load(str(path))

    def show_play_stage(self):
        studio = getattr(self, "_studio_inline", None)
        if studio is not None and hasattr(studio, "stop_preview"):
            try:
                studio.stop_preview()
            except Exception:
                pass
        if hasattr(self, "stage_stack"):
            self.stage_stack.setCurrentIndex(0)
        if hasattr(self, "btn_studio"):
            self.btn_studio.blockSignals(True)
            self.btn_studio.setChecked(False)
            self.btn_studio.blockSignals(False)

    def _toggle_studio_stage(self, checked=False):
        if checked or (hasattr(self, "stage_stack") and self.stage_stack.currentIndex() != 1):
            self.open_multipart_studio()
        else:
            self.show_play_stage()

    def _on_studio_generated(self):
        self.refresh_midi_list()

    def open_multipart_studio(self):
        """在演奏页右侧舞台区原地切换至「搜谱 / 多轨工坊」（不弹窗）。"""
        studio = getattr(self, "_studio_inline", None)
        if studio is None:
            try:
                from multipart_studio import MultipartStudioDialog
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "乐曲库不可用", "乐曲库加载失败：%s" % exc)
                if hasattr(self, "btn_studio"):
                    self.btn_studio.setChecked(False)
                return None
            studio = MultipartStudioDialog(
                self,
                on_done=self._on_studio_generated,
                embedded=True,
                on_back=self.show_play_stage,
            )
            self._studio_inline = studio
            if hasattr(self, "_studio_host_lay"):
                self._studio_host_lay.addWidget(studio, 1)
            studio.show()
        elif hasattr(studio, "fill_align_sources"):
            try:
                studio.fill_align_sources()
            except Exception:
                pass
        if hasattr(self, "stage_stack"):
            self.stage_stack.setCurrentIndex(1)
        if hasattr(self, "btn_studio"):
            self.btn_studio.blockSignals(True)
            self.btn_studio.setChecked(True)
            self.btn_studio.blockSignals(False)
        return studio

    def import_midi_files(self):
        """把电脑任意位置的 MIDI 复制进 out/ 归档，然后刷新曲库。"""
        dlg = QFileDialog(self, "导入 MIDI 到曲库", "", "MIDI 文件 (*.mid *.midi)")
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setFileMode(QFileDialog.ExistingFiles)
        if not dlg.exec_():
            return 0
        return self.archive_midi_files(dlg.selectedFiles() or [])

    def archive_midi_files(self, files):
        """把给定路径的 MIDI 归档进 out/（重名自动加序号），返回成功数量。"""
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
        except Exception as exc:
            self._append_log("× 创建 out 目录失败：%s" % exc)
            return 0
        added = 0
        last_dst = None
        for src in (files or []):
            try:
                base = os.path.basename(str(src))
                stem, ext = os.path.splitext(base)
                dst = os.path.join(OUT_DIR, base)
                n = 2
                while os.path.exists(dst) and not _same_path(dst, src):
                    dst = os.path.join(OUT_DIR, "%s (%d)%s" % (stem, n, ext))
                    n += 1
                if not _same_path(dst, src):
                    shutil.copy2(src, dst)
                last_dst = dst
                added += 1
                self._append_log("＋ 已归档到 out/%s" % os.path.basename(dst))
            except Exception as exc:
                self._append_log("× 导入失败（%s）：%s" % (src, exc))
        self.refresh_midi_list()
        if added:
            self._append_log("曲库已刷新：新导入 %d 首" % added)
            self.load(last_dst)
        return added

    def _build_ui(self):
        """左侧曲库 + 右侧三层音乐工作站舞台（动态头图 / 编配卡片 / DAW 演奏控制舱 / 帧率与日志）。"""
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        outer.addWidget(self._build_midi_library())

        self.stage_stack = QStackedWidget()
        right = QWidget()
        v = QVBoxLayout(right)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.stage_stack.addWidget(right)

        self._studio_host = QWidget()
        self._studio_host_lay = QVBoxLayout(self._studio_host)
        self._studio_host_lay.setContentsMargins(0, 0, 0, 0)
        self._studio_host_lay.setSpacing(0)
        self.stage_stack.addWidget(self._studio_host)
        outer.addWidget(self.stage_stack, 1)

        # 层 1：顶部动态 HeroBannerWidget（仅左侧安全区放置胶囊/曲名/音域，右侧留空 >=340px 展示原画）
        init_banner = ("piano" if self.instrument_mode == INSTRUMENT_PIANO_36
                       else "guitar")
        self.hero_banner = _create_hero_banner(banner_key=init_banner, height=104)
        hero_lay = self.hero_banner.content_layout()

        hero_left = QVBoxLayout()
        hero_left.setContentsMargins(0, 0, 0, 0)
        hero_left.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.mode_buttons = {}
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        mode_specs = (
            (INSTRUMENT_GUITAR_WHEEL, "吉他 · 轮盘", "E2~E6 · 品格/和弦轮盘演奏", "wheel"),
            (INSTRUMENT_GUITAR_36, "吉他 · 36键", "E3~D#6 · 36 键直映射（支持长按延音）", "guitar"),
            (INSTRUMENT_PIANO_36, "钢琴 · 36键", "C4~B6 · 36 键（Shift # / Ctrl b 互斥）", "piano"),
        )
        for m_key, m_label, m_tip, _ico in mode_specs:
            btn = QPushButton(m_label)
            btn.setObjectName("modePill")
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            btn.setIconSize(QSize(15, 15))
            btn.setToolTip(m_tip)
            btn.setChecked(m_key == self.instrument_mode)
            btn.toggled.connect(lambda on, mk=m_key: on and self.set_instrument_mode(mk))
            self.mode_group.addButton(btn)
            self.mode_buttons[m_key] = btn
            mode_row.addWidget(btn)
        mode_row.addStretch(1)
        hero_left.addLayout(mode_row)

        song_row = QHBoxLayout()
        song_row.setSpacing(6)

        self.lbl_file = QLabel("（还没选歌 · 请从左侧选择 MIDI）")
        self.lbl_file.setObjectName("heroTitle")
        self.lbl_file.setMinimumWidth(80)
        self.lbl_file.setMaximumWidth(230)
        self.lbl_file.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        song_row.addWidget(self.lbl_file, 1)

        self.lbl_mode_range = QLabel(INSTRUMENT_RANGE_BADGE.get(self.instrument_mode, ""))
        self.lbl_mode_range.setObjectName("violetPill")
        song_row.addWidget(self.lbl_mode_range)
        song_row.addStretch(1)
        hero_left.addLayout(song_row)

        hero_spacer = QWidget()
        hero_spacer.setMinimumWidth(120)
        hero_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hero_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        hero_spacer.setStyleSheet("background: transparent;")

        hero_lay.addLayout(hero_left, 0)
        hero_lay.addStretch(1)
        hero_lay.addWidget(hero_spacer)
        v.addWidget(self.hero_banner)

        # 层 2：中部编配卡片（GlassCard）
        c1 = _make_glass_card("card")
        a = QVBoxLayout(c1)
        a.setContentsMargins(10, 8, 10, 8)
        a.setSpacing(6)

        self.btn_roll = QPushButton("钢琴卷帘微调")
        self.btn_roll.setObjectName("primary")
        self.btn_roll.setMinimumHeight(34)
        self.btn_roll.setIconSize(QSize(15, 15))
        self.btn_roll.setToolTip("打开当前模式的音符编排；改完可直接重编并更新演奏台")
        self.btn_roll.clicked.connect(self.open_piano_roll)

        self.chk_auto_accomp = QCheckBox("开启和弦自动生成伴奏")
        self.chk_auto_accomp.setToolTip(
            "开启：1P 为指弹独奏；关闭：1P 只弹主音旋律，2P 伴奏角色一并置灰")
        self.chk_auto_accomp.setChecked(bool(PC.load().get("enable_auto_accomp", True)))
        self.chk_auto_accomp.toggled.connect(self._on_auto_accomp_toggled)

        self.chk_arpeggio = QCheckBox("开启和弦内音琶音")
        self.chk_arpeggio.setChecked(bool(PC.load().get("enable_arpeggio", True)))
        self.chk_arpeggio.toggled.connect(self._on_arpeggio_toggled)
        self.chk_chord_rhythm = QCheckBox("开启和弦扫弦律动")
        self.chk_chord_rhythm.setChecked(bool(PC.load().get("enable_chord_rhythm", True)))
        self.chk_chord_rhythm.toggled.connect(self._on_chord_rhythm_toggled)
        self.chk_strum_mode = QCheckBox("柱式改用鼠标扫弦")
        self.chk_strum_mode.setChecked(bool(PC.load().get("chord_strum_mode", False)))
        self.chk_strum_mode.toggled.connect(self._on_strum_mode_toggled)

        self.wheel_role_panel = QWidget()
        self.radios = {}
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)
        grid = QGridLayout(self.wheel_role_panel)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        role_text = {
            MC.ROLE_SOLO: ("1P 主音 / 独奏",
                           "开启自动伴奏时为指弹独奏；关闭时为纯主音旋律"),
            MC.ROLE_DUET_RHYTHM_FRET: (ROLE_TITLE[MC.ROLE_DUET_RHYTHM_FRET],
                                       ROLE_TIP[MC.ROLE_DUET_RHYTHM_FRET]),
            MC.ROLE_DUET_RHYTHM: (ROLE_TITLE[MC.ROLE_DUET_RHYTHM],
                                  ROLE_TIP[MC.ROLE_DUET_RHYTHM]),
        }
        for i, r in enumerate(role_text):
            title, tip = role_text[r]
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(1)
            rb = QRadioButton(title)
            rb.setObjectName("roleRadio")
            rb.setChecked(r == self.role)
            rb.toggled.connect(lambda on, rr=r: on and self.choose_role(rr))
            self.role_group.addButton(rb, i)
            cv.addWidget(rb)
            desc = QLabel(tip)
            desc.setObjectName("roleDesc")
            desc.setContentsMargins(20, 0, 0, 0)
            desc.setWordWrap(True)
            desc.setCursor(Qt.PointingHandCursor)
            desc.mousePressEvent = lambda _ev, box=rb: box.setChecked(True)
            cv.addWidget(desc)
            if r == MC.ROLE_DUET_RHYTHM_FRET:
                row_opt = QHBoxLayout()
                row_opt.setContentsMargins(20, 2, 0, 0)
                row_opt.addWidget(self.chk_arpeggio)
                row_opt.addStretch(1)
                cv.addLayout(row_opt)
            elif r == MC.ROLE_DUET_RHYTHM:
                row_opt = QHBoxLayout()
                row_opt.setContentsMargins(20, 2, 0, 0)
                row_opt.setSpacing(8)
                row_opt.addWidget(self.chk_chord_rhythm)
                row_opt.addWidget(self.chk_strum_mode)
                row_opt.addStretch(1)
                cv.addLayout(row_opt)
            cv.addStretch(1)
            grid.addWidget(cell, i // 2, i % 2)
            self.radios[r] = rb
        rb_lead = QRadioButton(ROLE_TITLE[MC.ROLE_DUET_LEAD])
        rb_lead.setVisible(False)
        self.role_group.addButton(rb_lead, 9)
        self.radios[MC.ROLE_DUET_LEAD] = rb_lead

        # 第 4 格：自动伴奏开关 + 校准轮盘 + 钢琴卷帘微调
        wheel_opt_cell = QWidget()
        wov = QVBoxLayout(wheel_opt_cell)
        wov.setContentsMargins(4, 0, 0, 0)
        wov.setSpacing(6)
        wov.addWidget(self.chk_auto_accomp)
        cal_row = QHBoxLayout()
        cal_row.setSpacing(8)
        self.btn_calibrate = QPushButton("校准轮盘")
        self.btn_calibrate.setMinimumHeight(34)
        self.btn_calibrate.setIconSize(QSize(15, 15))
        self.btn_calibrate.setToolTip("打开置顶半透明指示圈，对齐游戏内 6 扇区吉他轮盘")
        self.btn_calibrate.clicked.connect(self._auto_calibrate)
        cal_row.addWidget(self.btn_calibrate)
        self._wheel_btn_slot = cal_row
        cal_row.addStretch(1)
        wov.addLayout(cal_row)
        wov.addStretch(1)
        grid.addWidget(wheel_opt_cell, 1, 1)

        a.addWidget(self.wheel_role_panel)

        # 36 键编配面板（仅 guitar_36 / piano_36 显示：顶部工具栏 + 2×2 平铺角色矩阵 + 热力条 + 内联键位图）
        self.kb_panel = QWidget()
        kb_lay = QVBoxLayout(self.kb_panel)
        kb_lay.setContentsMargins(0, 0, 0, 0)
        kb_lay.setSpacing(6)

        kb_row1 = QHBoxLayout()
        kb_row1.setSpacing(6)
        lbl_tr = QLabel("移调：")
        lbl_tr.setObjectName("sub")
        kb_row1.addWidget(lbl_tr)
        self.spin_transpose = QSpinBox()
        self.spin_transpose.setRange(-12, 12)
        self.spin_transpose.setSingleStep(1)
        self.spin_transpose.setSuffix(" 半音")
        self.spin_transpose.setFixedWidth(84)
        self.spin_transpose.setValue(self.transpose)
        self.spin_transpose.setToolTip("对 36 键曲目做整体半音移调（-12 ~ +12）")
        self.spin_transpose.valueChanged.connect(self._on_transpose_changed)
        kb_row1.addWidget(self.spin_transpose)

        self.btn_smart_transpose = QPushButton("智能移调")
        self.btn_smart_transpose.setMinimumHeight(34)
        self.btn_smart_transpose.setIconSize(QSize(15, 15))
        self.btn_smart_transpose.setToolTip("自动计算当前音域命中率最高的半音移调并立即重编")
        self.btn_smart_transpose.clicked.connect(self._smart_transpose)
        kb_row1.addWidget(self.btn_smart_transpose)

        kb_row1.addStretch(1)
        self.btn_kb_preview = QPushButton("36 键位图")
        self.btn_kb_preview.setObjectName("accent")
        self.btn_kb_preview.setCheckable(True)
        self.btn_kb_preview.setMinimumHeight(34)
        self.btn_kb_preview.setIconSize(QSize(15, 15))
        self.btn_kb_preview.setToolTip("在当前页面内展开/收起交互式 36 键位图（无需弹窗）")
        self.btn_kb_preview.clicked.connect(self.open_keyboard_preview)
        kb_row1.addWidget(self.btn_kb_preview)
        self._kb_btn_slot = kb_row1
        kb_lay.addLayout(kb_row1)

        # 2×2 平铺角色矩阵（彻底避免切换角色时的高度跳动，且非当前角色子选项自动联动置灰防串扰）
        self.kb_role_panel = QWidget()
        kb_grid = QGridLayout(self.kb_role_panel)
        kb_grid.setContentsMargins(0, 2, 0, 2)
        kb_grid.setHorizontalSpacing(10)
        kb_grid.setVerticalSpacing(6)
        self.kb_role_group = QButtonGroup(self)
        self.kb_role_group.setExclusive(True)
        self.kb_role_radios = {}

        def _make_role_cell(key, title, desc_txt):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(2)
            rb = QRadioButton(title)
            rb.setObjectName("roleRadio")
            rb.toggled.connect(lambda on, rk=key: on and self._on_kb_role_picked(rk))
            self.kb_role_group.addButton(rb)
            self.kb_role_radios[key] = rb
            cv.addWidget(rb)
            desc = QLabel(desc_txt)
            desc.setObjectName("roleDesc")
            desc.setContentsMargins(20, 0, 0, 0)
            desc.setWordWrap(True)
            desc.setCursor(Qt.PointingHandCursor)
            desc.mousePressEvent = lambda _ev, box=rb: box.setChecked(True)
            cv.addWidget(desc)
            sub_row = QHBoxLayout()
            sub_row.setContentsMargins(20, 2, 0, 0)
            sub_row.setSpacing(8)
            cv.addLayout(sub_row)
            cv.addStretch(1)
            return cell, sub_row

        # 格 (0,0)：原谱直出
        cell_raw, row_raw = _make_role_cell(
            "raw", "原谱直出", "按选定音轨直接映射演奏（适合原本编配完整的曲谱）")
        self.lbl_kb_scope = QLabel("音轨：")
        self.lbl_kb_scope.setObjectName("sub")
        row_raw.addWidget(self.lbl_kb_scope)
        self.cmb_kb_scope = QComboBox()
        for sc_key, sc_label in KB_TRACK_SCOPES:
            self.cmb_kb_scope.addItem(sc_label, sc_key)
        self.cmb_kb_scope.setCurrentIndex(max(0, self.cmb_kb_scope.findData(self.kb_track_scope)))
        self.cmb_kb_scope.setMinimumWidth(104)
        self.cmb_kb_scope.currentIndexChanged.connect(self._on_kb_scope_changed)
        row_raw.addWidget(self.cmb_kb_scope)
        row_raw.addStretch(1)
        kb_grid.addWidget(cell_raw, 0, 0)

        # 格 (0,1)：1P 独奏 / 主音
        cell_solo, row_solo = _make_role_cell(
            "solo", "1P 独奏 / 主音", "开启智能伴奏时为旋律+伴奏；关闭时退化为纯 1P 主音")
        self.chk_kb_auto_accomp = QCheckBox("智能低音伴奏")
        self.chk_kb_auto_accomp.setChecked(self.kb_auto_accomp)
        self.chk_kb_auto_accomp.toggled.connect(self._on_kb_opt_changed)
        row_solo.addWidget(self.chk_kb_auto_accomp)
        self.chk_kb_solo_arp = QCheckBox("句间和弦琶音")
        self.chk_kb_solo_arp.setChecked(self.kb_solo_arpeggio)
        self.chk_kb_solo_arp.toggled.connect(self._on_kb_opt_changed)
        row_solo.addWidget(self.chk_kb_solo_arp)
        row_solo.addStretch(1)
        kb_grid.addWidget(cell_solo, 0, 1)

        # 格 (1,0)：2P 伴奏 · 柱式/律动
        cell_chord, row_chord = _make_role_cell(
            "duet_rhythm", "2P 伴奏 · 柱式/律动", "提取和声做 3~4 音柱式抓弦，可按整拍律动或错峰扫弦")
        self.chk_kb_chord_rhythm = QCheckBox("开启和弦律动")
        self.chk_kb_chord_rhythm.setChecked(self.kb_enable_chord_rhythm)
        self.chk_kb_chord_rhythm.toggled.connect(self._on_kb_opt_changed)
        row_chord.addWidget(self.chk_kb_chord_rhythm)
        self.chk_kb_strum_roll = QCheckBox("柱式错峰扫弦")
        self.chk_kb_strum_roll.setToolTip("同一柱式和弦声部按 18ms 依次错峰击键，模拟拨片上下扫弦/滚和弦")
        self.chk_kb_strum_roll.setChecked(self.kb_strum_roll)
        self.chk_kb_strum_roll.toggled.connect(self._on_kb_opt_changed)
        row_chord.addWidget(self.chk_kb_strum_roll)
        row_chord.addStretch(1)
        kb_grid.addWidget(cell_chord, 1, 0)

        # 格 (1,1)：2P 伴奏 · 琶音/根音
        cell_arp, row_arp = _make_role_cell(
            "duet_rhythm_fret", "2P 伴奏 · 琶音/根音", "按拍点做和弦内音分解；关闭时稳健弹奏低音根五度")
        self.chk_kb_arpeggio = QCheckBox("开启分解琶音")
        self.chk_kb_arpeggio.setChecked(self.kb_p2_arpeggio)
        self.chk_kb_arpeggio.toggled.connect(self._on_kb_opt_changed)
        row_arp.addWidget(self.chk_kb_arpeggio)
        self.cmb_kb_arp_pattern = QComboBox()
        for pk, pl in KB_ARP_PATTERNS:
            self.cmb_kb_arp_pattern.addItem(pl, pk)
        self.cmb_kb_arp_pattern.setCurrentIndex(
            max(0, self.cmb_kb_arp_pattern.findData(self.kb_arp_pattern)))
        self.cmb_kb_arp_pattern.setFixedWidth(92)
        self.cmb_kb_arp_pattern.currentIndexChanged.connect(self._on_kb_opt_changed)
        row_arp.addWidget(self.cmb_kb_arp_pattern)
        row_arp.addStretch(1)
        kb_grid.addWidget(cell_arp, 1, 1)

        kb_grid.setColumnStretch(0, 1)
        kb_grid.setColumnStretch(1, 1)
        init_radio_key = "solo" if self.kb_arrange_mode in ("solo", "duet_lead") else self.kb_arrange_mode
        if init_radio_key in self.kb_role_radios:
            self.kb_role_radios[init_radio_key].blockSignals(True)
            self.kb_role_radios[init_radio_key].setChecked(True)
            self.kb_role_radios[init_radio_key].blockSignals(False)
        if self.kb_arrange_mode == "duet_lead":
            self.chk_kb_auto_accomp.blockSignals(True)
            self.chk_kb_auto_accomp.setChecked(False)
            self.chk_kb_auto_accomp.blockSignals(False)
            self.kb_auto_accomp = False
        kb_lay.addWidget(self.kb_role_panel)

        kb_row2 = QHBoxLayout()
        kb_row2.setSpacing(6)
        self.range_meter = _create_range_meter()
        kb_row2.addWidget(self.range_meter, 1)
        self.lbl_coverage_badge = QLabel("音域：--")
        self.lbl_coverage_badge.setObjectName("accentPill")
        self.lbl_coverage_badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        kb_row2.addWidget(self.lbl_coverage_badge)
        kb_lay.addLayout(kb_row2)

        self.kb_preview_host = QFrame()
        self.kb_preview_host.setObjectName("card_inner")
        self.kb_preview_host.setVisible(False)
        self._kb_preview_lay = QVBoxLayout(self.kb_preview_host)
        self._kb_preview_lay.setContentsMargins(6, 6, 6, 6)
        self._kb_preview_lay.setSpacing(0)
        kb_lay.addWidget(self.kb_preview_host)

        self.lbl_kb_mode_hint = QLabel("")
        self.lbl_kb_mode_hint.setObjectName("sub")
        self.lbl_kb_mode_hint.setWordWrap(True)
        self.lbl_kb_mode_hint.setVisible(False)

        a.addWidget(self.kb_panel)
        v.addWidget(c1)

        # 层 3：DAW 风格演奏控制舱（GlassCard, objectName="transportDeck"）
        c2 = _make_glass_card("transportDeck")
        c2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        b = QVBoxLayout(c2)
        b.setContentsMargins(10, 8, 10, 8)
        b.setSpacing(6)

        deck_row1 = QHBoxLayout()
        deck_row1.setSpacing(5)
        self.btn_play = QPushButton("开始演奏 (F8)")
        self.btn_play.setObjectName("playBtn")
        self.btn_play.setMinimumHeight(42)
        self.btn_play.setMinimumWidth(116)
        self.btn_play.setIconSize(QSize(15, 15))
        self.btn_play.setCheckable(True)
        self.btn_play.clicked.connect(self.toggle_play)
        deck_row1.addWidget(self.btn_play)

        self.btn_stop_play = QPushButton("停止 (F12)")
        self.btn_stop_play.setObjectName("danger")
        self.btn_stop_play.setMinimumHeight(42)
        self.btn_stop_play.setStyleSheet("padding: 4px 8px;")
        self.btn_stop_play.setIconSize(QSize(14, 14))
        self.btn_stop_play.setEnabled(False)
        self.btn_stop_play.clicked.connect(self.stop_play)
        deck_row1.addWidget(self.btn_stop_play)

        prog_box = QVBoxLayout()
        prog_box.setSpacing(3)
        self.lbl_timecode = QLabel("00:00 / 00:00")
        self.lbl_timecode.setObjectName("sub")
        self.lbl_timecode.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_timecode.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.lbl_timecode.setMinimumWidth(68)
        self.lbl_timecode.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 11px; font-weight: 700;")
        prog_box.addWidget(self.lbl_timecode)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setMinimumWidth(68)
        prog_box.addWidget(self.bar)
        deck_row1.addLayout(prog_box, 1)

        spin_lab = QLabel("倍速")
        spin_lab.setObjectName("sub")
        deck_row1.addWidget(spin_lab)
        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.5, 1.5)
        self.spin_speed.setSingleStep(0.05)
        self.spin_speed.setValue(1.0)
        self.spin_speed.setFixedWidth(62)
        deck_row1.addWidget(self.spin_speed)
        self.chk_sync = QCheckBox("整5秒同步")
        self.chk_sync.setToolTip("等待至系统时钟下一个整 5 秒边界自动起奏，方便联机合奏")
        deck_row1.addWidget(self.chk_sync)

        self.btn_diag = QPushButton("硬件自检（4 步）")
        self.btn_diag.setCheckable(True)
        self.btn_diag.setMinimumHeight(34)
        self.btn_diag.setStyleSheet("padding: 4px 8px;")
        self.btn_diag.setIconSize(QSize(14, 14))
        self.btn_diag.setToolTip("在当前页面内原地展开/收起当前模式的底层通道自检（无需弹窗）")
        self.btn_diag.clicked.connect(self._open_diag)
        deck_row1.addWidget(self.btn_diag)
        b.addLayout(deck_row1)

        deck_row2 = QHBoxLayout()
        deck_row2.setSpacing(10)
        self.lbl_guide = QLabel("请在左侧曲库选择一首曲目")
        self.lbl_guide.setObjectName("guide_text")
        self.lbl_guide.setWordWrap(True)
        self.lbl_guide.setMinimumHeight(48)
        self.lbl_guide.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.lbl_guide.setTextInteractionFlags(Qt.TextSelectableByMouse)
        deck_row2.addWidget(self.lbl_guide, 1)
        b.addLayout(deck_row2)

        # 内联自检面板（默认隐藏，替代原独立弹窗）
        self.diag_inline_box = QFrame()
        self.diag_inline_box.setObjectName("card_inner")
        self.diag_inline_box.setVisible(False)
        diag_lay = QVBoxLayout(self.diag_inline_box)
        diag_lay.setContentsMargins(10, 8, 10, 8)
        diag_lay.setSpacing(6)
        diag_head = QHBoxLayout()
        diag_head.setSpacing(8)
        self.lbl_diag_tip = QLabel("")
        self.lbl_diag_tip.setObjectName("sub")
        self.lbl_diag_tip.setWordWrap(True)
        diag_head.addWidget(self.lbl_diag_tip, 1)
        self._diag_status_lbl = QLabel("就绪")
        self._diag_status_lbl.setObjectName("accentPill")
        diag_head.addWidget(self._diag_status_lbl)
        btn_diag_fold = QPushButton("▲ 收起")
        btn_diag_fold.setObjectName("ghost")
        btn_diag_fold.setMinimumHeight(26)
        btn_diag_fold.clicked.connect(lambda: self._toggle_inline_diag(False))
        diag_head.addWidget(btn_diag_fold)
        diag_lay.addLayout(diag_head)
        self._diag_btns_row = QHBoxLayout()
        self._diag_btns_row.setSpacing(6)
        diag_lay.addLayout(self._diag_btns_row)
        b.addWidget(self.diag_inline_box)

        v.addWidget(c2)

        # 层 4：底部精简「帧率与运行日志区」（GlassCard）
        c3 = _make_glass_card("card")
        c = QVBoxLayout(c3)
        c.setContentsMargins(10, 8, 10, 8)
        c.setSpacing(6)
        self.chk_rec = QCheckBox("同步内录系统声音")
        self.chk_rec.setChecked(False)
        self.cmb_rec = QComboBox()
        self.cmb_rec.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_rec.setMinimumContentsLength(12)
        self.cmb_rec.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cmb_rec.setMinimumWidth(140)
        self.cmb_rec.addItem("（正在查找声卡…）")
        self._load_devices()
        self._build_advanced(c)

        log_head = QHBoxLayout()
        log_head.setSpacing(8)
        log_head.addWidget(self._section("运行日志"))
        log_head.addStretch(1)
        self.btn_toggle_log = QPushButton("展开日志")
        self.btn_toggle_log.setMinimumHeight(26)
        self.btn_toggle_log.setStyleSheet("padding: 2px 10px; min-height: 24px; font-size: 12px;")
        self.btn_toggle_log.clicked.connect(self._toggle_log_expand)
        log_head.addWidget(self.btn_toggle_log)
        c.addLayout(log_head)

        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(54)
        self.log.setMaximumHeight(82)
        c.addWidget(self.log, 1)
        v.addWidget(c3, 1)
        self.refresh_midi_list()

    def _section(self, text):
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith((".mid", ".midi")):
                self.load(p)
                return

    def open_test_midi(self):
        """加载内置测试 MIDI"""
        demo = os.path.join(TEST_MIDI_DIR, "sample_demo.mid")
        if not os.path.isfile(demo):
            try:
                cands = [os.path.join(TEST_MIDI_DIR, f) for f in os.listdir(TEST_MIDI_DIR)
                         if f.lower().endswith((".mid", ".midi"))]
                demo = cands[0] if cands else ""
            except Exception:
                demo = ""
        if demo and os.path.isfile(demo):
            self.load(demo)
        else:
            QMessageBox.information(self, "未找到测试曲", "未找到内置测试曲：%s" % demo)

    def _build_advanced(self, v):
        """帧率预设（主界面醒目按钮）+「高级时间参数」按钮行。"""
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        self.rad_preset = {}
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        tips = {
            "30fps": "单帧 33.3ms：最稳，慢速与密集曲都不易吞键",
            "60fps": "单帧 16.7ms：均衡档，机器能稳跑 60 帧时选它",
            "90fps": "单帧 11.1ms：高刷机器用，动作预算最紧",
        }
        for key in ("30fps", "60fps", "90fps"):
            title, bpm = PC.PRESET_INFO[key]
            btn = QPushButton("%s\n%s" % (title, bpm))
            btn.setObjectName("presetBtn")
            btn.setCheckable(True)
            btn.setMinimumHeight(52)
            btn.setToolTip(tips[key])
            btn.toggled.connect(lambda on, k=key: on and self.apply_timing_preset(k))
            self.preset_group.addButton(btn)
            self.rad_preset[key] = btn
            preset_row.addWidget(btn, 1)
        v.addLayout(preset_row)

        row_follow = QHBoxLayout()
        row_follow.setSpacing(6)
        self.chk_follow_song = QCheckBox("跟随本曲参数")
        self.chk_follow_song.setToolTip("按当前曲目复杂度自动推导最低不晚出帧率参数（选歌后可用）")
        self.chk_follow_song.setEnabled(False)
        self.chk_follow_song.toggled.connect(self._on_follow_toggled)
        row_follow.addWidget(self.chk_follow_song)
        self.lbl_prune = QLabel("删音：0%")
        self.lbl_prune.setObjectName("sub")
        row_follow.addWidget(self.lbl_prune)
        self.sld_prune = QSlider(Qt.Horizontal)
        self.sld_prune.setRange(0, 15)
        self.sld_prune.setFixedWidth(74)
        self.sld_prune.setToolTip("0% = 不删音；1~15% 可在密集处精简伴奏音以防吞音")
        try:
            self.sld_prune.setValue(int(PC.load().get("prune_ratio", 0) or 0))
        except Exception:
            self.sld_prune.setValue(0)
        self.sld_prune.valueChanged.connect(self._on_prune_changed)
        row_follow.addWidget(self.sld_prune)
        self.lbl_compile_badge = QLabel("")
        self.lbl_compile_badge.setWordWrap(True)
        self.lbl_compile_badge.setMinimumWidth(0)
        self.lbl_compile_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_compile_badge.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row_follow.addWidget(self.lbl_compile_badge, 1)
        v.addLayout(row_follow)
        self._update_prune_label(self.sld_prune.value())

        # 仅当配置文件仍含历史废弃键时才做一次性迁移，绝不覆盖用户自定义的 min_action_interval > 0.005
        if self._current_preset_name() is None:
            try:
                import json as _json
                with open(PC.CONFIG_PATH, "r", encoding="utf-8") as _cf:
                    _raw_cfg = _json.load(_cf)
                _raw_timing = (_raw_cfg.get("timing") or {}) if isinstance(_raw_cfg, dict) else {}
                if any(k in _raw_timing for k in ("fret_strum_gap", "arpeggio_gap")):
                    PC.save_timing(PC.TIMING_PRESETS[PC.DEFAULT_TIMING_PRESET], merge=False)
            except Exception:
                pass
        self._sync_preset_radios()

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_adv = QPushButton("高级时间参数（6 项）▼")
        self.btn_adv.setCheckable(True)
        self.btn_adv.setToolTip("原地展开/收起 6 项核心时间参数与内录设置（无需弹窗）")
        self.btn_adv.clicked.connect(self._open_timing_dialog)
        row.addWidget(self.btn_adv)
        self.lbl_preset_tip = QLabel(
            "提示：先按歌曲 BPM 选一档；进游戏若吞音/漏翻页请【降一档】，若编排提示音符晚出请【升一档】。")
        self.lbl_preset_tip.setObjectName("sub")
        self.lbl_preset_tip.setWordWrap(True)
        row.addWidget(self.lbl_preset_tip, 1)
        v.addLayout(row)

        self.adv_box = QGroupBox("")
        av = QVBoxLayout(self.adv_box)
        av.setContentsMargins(8, 8, 8, 8)
        av.setSpacing(6)

        self._timing_hint_base = (
            "点击上方预设可按游戏帧率一键配置；也可手动微调单项参数，"
            "点击「保存当前修改」后立即生效。")
        self._timing_dirty = False
        self.lbl_timing_hint = QLabel(self._timing_hint_base)
        self.lbl_timing_hint.setObjectName("sub")
        self.lbl_timing_hint.setWordWrap(True)
        av.addWidget(self.lbl_timing_hint)

        inner = QWidget()
        inner.setObjectName("adv_inner")
        grid = QGridLayout(inner)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        for col, text in enumerate(("参数名称", "当前值", "实际生效",
                                    "允许范围 / 依赖")):
            lab = QLabel(text)
            lab.setObjectName("section")
            grid.addWidget(lab, 0, col)

        current = PC.load_timing()
        self.timing_effects = {}
        for i, (key, cn, dflt, unit, lo, hi, level, note) in enumerate(PC.TIMING_INFO):
            lab = QLabel(cn)
            lab.setToolTip("参数 key：%s\n%s\n%s" % (key, level, note))
            grid.addWidget(lab, i + 1, 0)

            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(float(lo), float(hi))
            spin.setSingleStep(0.005)
            spin.setSuffix(" " + unit)
            spin.setMinimumWidth(94)
            spin.setToolTip("%s\n允许范围 %s ~ %s %s\n%s"
                            % (cn, lo, hi, unit, note))
            try:
                spin.setValue(float(current.get(key, dflt)))
            except (TypeError, ValueError):
                spin.setValue(float(dflt))
            spin.valueChanged.connect(self._on_timing_spin_changed)
            self.timing_spins[key] = spin
            grid.addWidget(spin, i + 1, 1)

            eff = QLabel("-")
            eff.setObjectName("sub")
            eff.setMinimumWidth(104)
            self.timing_effects[key] = eff
            grid.addWidget(eff, i + 1, 2)

            rng = QLabel("%s ~ %s %s ｜ %s" % (lo, hi, unit, _timing_dep_text(key)))
            rng.setObjectName("sub")
            rng.setWordWrap(True)
            rng.setToolTip(_timing_dep_text(key))
            grid.addWidget(rng, i + 1, 3)
            grid.setRowMinimumHeight(i + 1, 30)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(230)
        scroll.setMaximumHeight(290)
        av.addWidget(scroll, 1)

        self.lbl_timing_budget = QLabel("")
        self.lbl_timing_budget.setObjectName("sub")
        self.lbl_timing_budget.setWordWrap(True)
        av.addWidget(self.lbl_timing_budget)

        rec_inline = QHBoxLayout()
        rec_inline.setSpacing(8)
        rec_inline.addWidget(self.chk_rec)
        rec_lab = QLabel("声卡：")
        rec_lab.setObjectName("sub")
        rec_inline.addWidget(rec_lab)
        rec_inline.addWidget(self.cmb_rec, 1)
        self.btn_reset_timing = QPushButton("恢复默认")
        self.btn_reset_timing.setMinimumHeight(30)
        self.btn_reset_timing.setToolTip("恢复所有延迟参数到默认预设并重编")
        self.btn_reset_timing.clicked.connect(self.reset_timing)
        rec_inline.addWidget(self.btn_reset_timing)
        self.btn_save_timing = QPushButton("保存当前修改")
        self.btn_save_timing.setObjectName("primary")
        self.btn_save_timing.setMinimumHeight(30)
        self.btn_save_timing.clicked.connect(self.save_timing)
        rec_inline.addWidget(self.btn_save_timing)
        btn_fold_adv = QPushButton("▲ 收起")
        btn_fold_adv.setObjectName("ghost")
        btn_fold_adv.setMinimumHeight(30)
        btn_fold_adv.clicked.connect(lambda: self._toggle_inline_timing(False))
        rec_inline.addWidget(btn_fold_adv)
        av.addLayout(rec_inline)

        self._update_timing_effects()

        self.adv_box.setVisible(False)
        v.addWidget(self.adv_box)
        try:
            inner.adjustSize()
        except Exception:
            pass

    def _follow_active(self):
        chk = getattr(self, "chk_follow_song", None)
        return bool(chk is not None and chk.isEnabled() and chk.isChecked())

    def _on_follow_toggled(self, checked):
        if checked:
            if self.path:
                self._compile()
        else:
            self.apply_timing_preset(self._current_preset_name()
                                     or PC.DEFAULT_TIMING_PRESET)

    def _on_prune_changed(self, val):
        self._update_prune_label(int(val))
        try:
            PC.save({"prune_ratio": int(val)})
        except Exception:
            pass
        if not self.path:
            return
        if self._follow_active():
            self._update_song_timing()
        else:
            self._compile()

    def _update_prune_label(self, val):
        if not hasattr(self, "lbl_prune"):
            return
        self.lbl_prune.setText("删音：%d%%" % int(val))

    def _active_timeline(self):
        """获取当前乐器模式下的有效编译时间轴。"""
        if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL:
            return self.timelines.get(self.role)
        return (self.timelines.get(self.instrument_mode)
                or self.timelines.get(self.role))

    def _update_compile_badge(self, tl=None):
        if not hasattr(self, "lbl_compile_badge"):
            return
        tl = tl or self._active_timeline() or {}
        st = tl.get("stats") or {}
        late = int(st.get("late") or 0)
        cut = int(st.get("trimmed_accomp") or 0)
        if late == 0 and cut == 0:
            if self.instrument_mode != INSTRUMENT_GUITAR_WHEEL and st:
                voices = int(st.get("voices") or st.get("notes") or 0)
                self.lbl_compile_badge.setText("就绪：0 晚出 · %d 音" % voices)
            else:
                self.lbl_compile_badge.setText("就绪：0 晚出 · 0 删音")
            self.lbl_compile_badge.setStyleSheet(
                "color:#16a34a; font-weight: 700; padding-right: 6px;")
        else:
            self.lbl_compile_badge.setText(
                "晚出 %d 音 · 删减 %d 音（可升帧率或调高删音）"
                % (late, cut))
            self.lbl_compile_badge.setStyleSheet(
                "color:#d97706; font-weight: 700; padding-right: 4px;")
        self.lbl_compile_badge.updateGeometry()

    def _update_song_timing(self):
        self._song_timing = None
        self._timing_seq += 1
        seq = self._timing_seq
        if not hasattr(self, "chk_follow_song"):
            return
        path, role = self.path, self.role
        mode = self.instrument_mode
        scope = self.kb_track_scope
        transpose = int(self.transpose or 0)
        prune = (self.sld_prune.value() / 100.0) if hasattr(self, "sld_prune") else 0.0
        if not path:
            self.chk_follow_song.setEnabled(False)
            self.chk_follow_song.setText("跟随本曲参数")
            return
        self.chk_follow_song.setEnabled(False)
        self.chk_follow_song.setText("跟随本曲 (推导中…)")

        def work():
            info = None
            try:
                kw = {
                    "role": role,
                    "max_prune_ratio": prune,
                    "instrument_mode": mode,
                    "kb_track_scope": scope,
                    "transpose": transpose,
                    "kb_arrange_mode": getattr(self, "kb_arrange_mode", "raw"),
                    "kb_auto_accomp": bool(getattr(self, "kb_auto_accomp", True)),
                    "kb_enable_arpeggio": bool(getattr(self, "kb_p2_arpeggio", True)),
                    "kb_enable_chord_rhythm": bool(getattr(self, "kb_enable_chord_rhythm", True)),
                    "kb_strum_roll": bool(getattr(self, "kb_strum_roll", False)),
                    "kb_arp_pattern": str(getattr(self, "kb_arp_pattern", "ascending")),
                    "kb_solo_arpeggio": bool(getattr(self, "kb_solo_arpeggio", True)),
                    "kb_p2_arpeggio": bool(getattr(self, "kb_p2_arpeggio", True)),
                }
                info = MC.deduce_song_timing(
                    path, **_filter_supported_kwargs(MC.deduce_song_timing, kw))
            except Exception as exc:
                if _LOG:
                    _LOG.debug("deduce_song_timing failed: %s", exc)
                info = None
            payload = dict(info) if isinstance(info, dict) else {"_empty": True}
            payload["_seq"] = seq
            try:
                self.sig_song_timing.emit(payload)
            except Exception as exc:
                if _LOG:
                    _LOG.debug("sig_song_timing emit failed: %s", exc)

        threading.Thread(target=work, daemon=True).start()

    def _on_song_timing(self, info):
        if isinstance(info, dict) and "_seq" in info:
            if info.get("_seq") != self._timing_seq:
                return
            if info.get("_empty"):
                info = None
        if not info or not self.path:
            if hasattr(self, "chk_follow_song"):
                self.chk_follow_song.setEnabled(False)
                self.chk_follow_song.setText("跟随本曲参数")
            return
        self._song_timing = info
        self.chk_follow_song.setEnabled(True)
        self.chk_follow_song.setText("跟随本曲 (≥%d帧)" % info["recommend_fps"])
        if self._follow_active():
            self._compile()

    def _toggle_inline_timing(self, show=None):
        want = (not self.adv_box.isVisible()) if show is None else bool(show)
        self.adv_box.setVisible(want)
        if hasattr(self, "btn_adv"):
            self.btn_adv.blockSignals(True)
            self.btn_adv.setChecked(want)
            self.btn_adv.setText("收起高级时间参数 ▲" if want else "高级时间参数（6 项）▼")
            self.btn_adv.blockSignals(False)
        return self.adv_box

    def _open_timing_dialog(self):
        return self._toggle_inline_timing()

    def _timing_values(self):
        return dict((k, float(sp.value())) for k, sp in self.timing_spins.items())

    def _on_timing_spin_changed(self, _v=None):
        self._timing_dirty = True
        try:
            self.lbl_timing_hint.setText("● 有未保存修改：点「保存当前修改」后才生效"
                                         "（会自动重新编译）\n" + self._timing_hint_base)
        except Exception:
            pass
        self._update_timing_effects()

    def _update_timing_effects(self):
        try:
            vals = self._timing_values()
            t = MC.Timing(vals)
        except Exception:
            return
        for key, lbl in self.timing_effects.items():
            v = float(vals.get(key, 0.0) or 0.0)
            if key == "min_action_interval":
                txt = "全局基准（只进预算）"
            elif key == "strum_hold":
                txt = "点一下（扫弦按住）" if v <= 1e-9 else "%.0f ms" % (v * 1000)
            elif key == "strum_click_hold" and float(vals.get("strum_hold") or 0) > 1e-9:
                txt = "由「扫弦按住延音」决定"
            elif key == "sustain_max":
                txt = "%.1f s" % v
            elif key == "sustain_min":
                txt = "%.0f ms（门槛）" % (v * 1000)
            else:
                txt = "%.0f ms" % (v * 1000)
                if v < 0.014 - 1e-9:
                    txt += "（直接生效，需高帧率）"
            lbl.setText(txt)
        try:
            s0 = t.switch_span(0, False, True)
            s1 = t.switch_span(1, True, True)
            s2 = t.switch_span(2, True, True)
            self.lbl_timing_budget.setText(
                "换格/翻页预算（按当前 6 项 + 底层 12 项实时计算）：\n"
                "同页换格 %.0f ms = 2×最小间隔 + 点格按住 + 同页等待\n"
                "翻 1 页 %.0f ms = 翻页键按住 + 松开后根音延迟 + 翻页后等待 + 点格按住 + 跨页等待\n"
                "翻 2 页 %.0f ms = 2×翻页按住 + 连按间隔 + 松开后根音延迟 + 翻页后等待 + 点格按住 + 跨页等待"
                % (s0 * 1000, s1 * 1000, s2 * 1000))
        except Exception:
            pass

    def _current_preset_name(self):
        try:
            cur = dict(PC.load_timing())
        except Exception:
            return None
        for key, vals in PC.TIMING_PRESETS.items():
            if all(abs(float(cur.get(k, -1)) - float(v)) < 1e-9 for k, v in vals.items()):
                return key
        return None

    def _sync_preset_radios(self, name=None):
        name = name or self._current_preset_name() or PC.DEFAULT_TIMING_PRESET
        for key, rb in (getattr(self, "rad_preset", None) or {}).items():
            try:
                rb.blockSignals(True)
                rb.setChecked(key == name)
                rb.blockSignals(False)
            except Exception:
                pass

    def apply_timing_preset(self, name):
        vals = dict(PC.TIMING_PRESETS.get(str(name)) or {})
        if not vals:
            return False
        for k, sp in self.timing_spins.items():
            if k in vals:
                sp.setValue(float(vals[k]))
        ok = PC.save_timing(vals, merge=False)
        if ok:
            self._timing_dirty = False
            try:
                self.lbl_timing_hint.setText(self._timing_hint_base)
            except Exception:
                pass
            if getattr(self, "chk_follow_song", None) is not None:
                self.chk_follow_song.blockSignals(True)
                self.chk_follow_song.setChecked(False)
                self.chk_follow_song.blockSignals(False)
            self._append_log("已切换到 %s 预设（%d 项已写盘）" % (name, len(vals)))
            self._sync_preset_radios(name)
            if self.path:
                self._compile()
            self._refresh_active_roll()
        else:
            self._append_log("× 预设写盘失败，请检查文件权限")
        self._update_timing_effects()
        return ok

    def _refresh_active_roll(self):
        dlg = getattr(self, "_active_roll_dlg", None)
        if dlg is not None and getattr(dlg, "isVisible", lambda: False)():
            try:
                dlg.on_timing_updated()
            except Exception as exc:
                self._append_log("× 卷帘刷新失败：%s" % exc)

    def save_timing(self):
        vals = self._timing_values()
        ok = PC.save_timing(vals)
        n = len(vals)
        if ok:
            self._timing_dirty = False
            try:
                self.lbl_timing_hint.setText(self._timing_hint_base)
            except Exception:
                pass
            self._append_log("已保存 %d 项参数（按住时长已同步到 6 个底层键；立即重编并刷新卷帘）" % n)
            self._sync_preset_radios()
            if self.path:
                self._compile()
            self._refresh_active_roll()
        else:
            self._append_log("× 保存失败（player_config.json 没写进去），请检查文件权限")
        self._update_timing_effects()
        return ok

    def reset_timing(self):
        d = PC.timing_defaults()
        ok = PC.save_timing(d, merge=False)
        for k, sp in self.timing_spins.items():
            if k in d:
                sp.setValue(float(d[k]))
        if ok:
            self._timing_dirty = False
            try:
                self.lbl_timing_hint.setText(self._timing_hint_base)
            except Exception:
                pass
            self._append_log("已恢复默认 %s 预设（%d 项，已写盘）"
                             % (PC.DEFAULT_TIMING_PRESET, len(d)))
            self._sync_preset_radios(PC.DEFAULT_TIMING_PRESET)
            if self.path:
                self._compile()
            self._refresh_active_roll()
        else:
            self._append_log("× 恢复默认时写盘失败，请检查文件权限")
        self._update_timing_effects()
        return ok

    def _load_devices(self):
        def work():
            try:
                names = REC.list_speakers()
                dflt = REC.default_speaker_name()
            except Exception:
                names, dflt = [], None
            try:
                self.sig_devices.emit((names, dflt))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_devices(self, payload):
        names, dflt = payload
        try:
            self.cmb_rec.clear()
            self.cmb_rec.addItems(names or ["（没找到输出设备）"])
            if dflt and dflt in (names or []):
                self.cmb_rec.setCurrentText(dflt)
        except Exception:
            pass

    def changeEvent(self, ev):
        super(MainWindow, self).changeEvent(ev)
        try:
            if ev.type() in (QEvent.StyleChange, QEvent.PaletteChange,
                             QEvent.ApplicationFontChange):
                self._sync_theme_widgets()
        except Exception:
            pass

    def _sync_theme_widgets(self):
        """按当前深浅主题同步头图遮罩、音域分布条与矢量图标配色。"""
        theme_mode = _current_theme_mode()
        fg = "#E6EDF7" if theme_mode == "dark" else "#1E293B"
        accent_fg = "#052E2B" if theme_mode == "dark" else "#1D4ED8"
        if hasattr(self, "hero_banner"):
            try:
                self.hero_banner.set_theme_mode(theme_mode)
            except Exception:
                pass
        if hasattr(self, "lbl_lib_icon"):
            ico = _make_vector_icon("library", color=fg, size=16)
            if not ico.isNull():
                self.lbl_lib_icon.setPixmap(ico.pixmap(16, 16))
        icon_map = (
            (getattr(self, "btn_midi_refresh", None), "refresh", fg, 14),
            (getattr(self, "btn_midi_import", None), "import", fg, 14),
            (getattr(self, "btn_studio", None), "spark", accent_fg, 15),
            (getattr(self, "btn_roll", None), "roll", "#FFFFFF", 15),
            (getattr(self, "btn_calibrate", None), "wheel", fg, 15),
            (getattr(self, "btn_smart_transpose", None), "spark", fg, 15),
            (getattr(self, "btn_kb_preview", None), "keyboard", accent_fg, 15),
            (getattr(self, "btn_play", None), "play", "#FFFFFF", 16),
            (getattr(self, "btn_stop_play", None), "stop", "#F43F5E" if theme_mode == "dark" else "#B91C1C", 15),
            (getattr(self, "btn_diag", None), "shield", fg, 15),
            (getattr(self, "btn_adv", None), "settings", fg, 15),
        )
        for btn, name, col, sz in icon_map:
            if btn is not None:
                try:
                    btn.setIcon(_make_vector_icon(name, color=col, size=sz))
                except Exception:
                    pass
        live_dlg = self._existing_midi_live_dialog()
        if live_dlg is not None and hasattr(live_dlg, "sync_theme"):
            try:
                live_dlg.sync_theme(theme_mode)
            except Exception:
                pass
        mode_icons = (
            (INSTRUMENT_GUITAR_WHEEL, "wheel"),
            (INSTRUMENT_GUITAR_36, "guitar"),
            (INSTRUMENT_PIANO_36, "piano"),
        )
        for m_key, ico_name in mode_icons:
            btn = getattr(self, "mode_buttons", {}).get(m_key)
            if btn is not None:
                col = "#FFFFFF" if btn.isChecked() else fg
                try:
                    btn.setIcon(_make_vector_icon(ico_name, color=col, size=15))
                except Exception:
                    pass
        if hasattr(self, "range_meter") and self.instrument_mode != INSTRUMENT_GUITAR_WHEEL:
            self._update_range_meter()
        if getattr(self, "_kb_preview_inline", None) is not None and hasattr(self._kb_preview_inline, "_sync_ui"):
            try:
                self._kb_preview_inline._sync_ui()
            except Exception:
                pass
        studio = getattr(self, "_studio_inline", None)
        if studio is not None:
            try:
                if hasattr(studio, "refresh_theme"):
                    studio.refresh_theme()
                elif hasattr(studio, "canvas"):
                    studio.canvas.update()
            except Exception:
                pass

    def on_theme_changed(self, mode=None):
        """外部主窗口切换主题时主动同步矢量图标与联动弹窗主题。"""
        self._sync_theme_widgets()

    def _toggle_log_expand(self):
        if not hasattr(self, "log") or not hasattr(self, "btn_toggle_log"):
            return
        self._log_expanded = not getattr(self, "_log_expanded", False)
        if self._log_expanded:
            self.log.setMaximumHeight(280)
            self.btn_toggle_log.setText("收起日志")
        else:
            self.log.setMaximumHeight(82)
            self.btn_toggle_log.setText("展开日志")

    def _update_range_meter(self):
        if not hasattr(self, "range_meter"):
            return
        if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL:
            return
        pitches = []
        for n in self._get_current_raw_notes():
            p = n.get("pitch") if isinstance(n, dict) else getattr(n, "pitch", None)
            if p is not None:
                pitches.append(int(p))
        try:
            self.range_meter.set_data(
                pitches,
                instrument_mode=self.instrument_mode,
                transpose=int(self.transpose or 0),
                theme_mode=_current_theme_mode(),
            )
        except Exception as exc:
            if _LOG:
                _LOG.debug("range_meter.set_data failed: %s", exc)

    def _apply_instrument_ui_state(self):
        """按当前乐器模式动态切换头图、控制区显隐与文案，防止高分屏拥挤。"""
        is_wheel = self.instrument_mode == INSTRUMENT_GUITAR_WHEEL
        if hasattr(self, "hero_banner"):
            try:
                self.hero_banner.set_banner(
                    "piano" if self.instrument_mode == INSTRUMENT_PIANO_36 else "guitar")
            except Exception:
                pass
        if hasattr(self, "mode_buttons"):
            theme_mode = _current_theme_mode()
            fg = "#E6EDF7" if theme_mode == "dark" else "#1E293B"
            ico_names = {
                INSTRUMENT_GUITAR_WHEEL: "wheel",
                INSTRUMENT_GUITAR_36: "guitar",
                INSTRUMENT_PIANO_36: "piano",
            }
            for m_key, btn in self.mode_buttons.items():
                try:
                    btn.blockSignals(True)
                    btn.setChecked(m_key == self.instrument_mode)
                    btn.blockSignals(False)
                    btn.setIcon(_make_vector_icon(
                        ico_names.get(m_key, "guitar"),
                        color="#FFFFFF" if m_key == self.instrument_mode else fg,
                        size=15))
                except Exception:
                    pass
        if hasattr(self, "lbl_mode_range"):
            self.lbl_mode_range.setText(
                INSTRUMENT_RANGE_BADGE.get(self.instrument_mode, ""))
        if hasattr(self, "wheel_role_panel"):
            self.wheel_role_panel.setVisible(is_wheel)
        if hasattr(self, "chk_auto_accomp"):
            self.chk_auto_accomp.setVisible(is_wheel)
        if hasattr(self, "kb_panel"):
            self.kb_panel.setVisible(not is_wheel)
        if hasattr(self, "btn_calibrate"):
            self.btn_calibrate.setVisible(is_wheel)
        if (hasattr(self, "btn_roll") and hasattr(self, "_wheel_btn_slot")
                and hasattr(self, "_kb_btn_slot")):
            target_slot = self._wheel_btn_slot if is_wheel else self._kb_btn_slot
            other_slot = self._kb_btn_slot if is_wheel else self._wheel_btn_slot
            try:
                other_slot.removeWidget(self.btn_roll)
                if is_wheel:
                    target_slot.insertWidget(1, self.btn_roll)
                else:
                    target_slot.addWidget(self.btn_roll)
                self.btn_roll.setVisible(True)
            except Exception:
                pass
        if hasattr(self, "btn_diag"):
            self.btn_diag.setText(
                "硬件自检（4 步）" if is_wheel else "键盘通道自检")
            if hasattr(self, "diag_inline_box") and self.diag_inline_box.isVisible():
                self._populate_inline_diag()
        if hasattr(self, "lbl_kb_mode_hint"):
            if self.instrument_mode == INSTRUMENT_GUITAR_36:
                self.lbl_kb_mode_hint.setText(
                    "E3~D#6 直映射 · 支持延音 · 越界音自动折叠")
            elif self.instrument_mode == INSTRUMENT_PIANO_36:
                self.lbl_kb_mode_hint.setText(
                    "C4~B6 · 无延音 · Shift(#)/Ctrl(b) 互斥切换")
            else:
                self.lbl_kb_mode_hint.setText("")
        if not is_wheel:
            self._sync_kb_arrange_controls()
            self._update_range_meter()
            self._sync_inline_kb_preview()
        elif hasattr(self, "kb_preview_host") and self.kb_preview_host.isVisible():
            self.kb_preview_host.setVisible(False)
            if hasattr(self, "btn_kb_preview"):
                self.btn_kb_preview.blockSignals(True)
                self.btn_kb_preview.setChecked(False)
                self.btn_kb_preview.blockSignals(False)
        if hasattr(self, "btn_roll"):
            self.btn_roll.setToolTip(
                "打开当前角色的转译序列；改完可直接重编并更新演奏台" if is_wheel
                else "打开 36 键钢琴卷帘查看键帽映射、红框越界校验与试听微调")

    def set_instrument_mode(self, mode):
        """切换顶部三乐器分段模式，持久化并重编当前曲目。"""
        mode = str(mode or INSTRUMENT_GUITAR_WHEEL)
        if mode not in INSTRUMENT_MODES:
            mode = INSTRUMENT_GUITAR_WHEEL
        if mode == self.instrument_mode:
            self._apply_instrument_ui_state()
            return
        self.instrument_mode = mode
        try:
            PC.save({"instrument_mode": mode})
        except Exception:
            pass
        self.timelines.clear()
        self.custom_role_notes.clear()
        self._raw_notes_cache = None
        roll_dlg = getattr(self, "_active_roll_dlg", None)
        if roll_dlg is not None:
            self._active_roll_dlg = None
            try:
                roll_dlg.close()
            except Exception:
                pass
        self._apply_instrument_ui_state()
        self._append_log("已切换演奏乐器：%s"
                         % INSTRUMENT_SHORT_CN.get(mode, mode))
        live_dlg = self._existing_midi_live_dialog()
        if live_dlg is not None and hasattr(live_dlg, "set_instrument_mode"):
            try:
                live_dlg.set_instrument_mode(mode)
            except Exception as exc:
                if _LOG:
                    _LOG.debug("sync live dialog mode failed: %s", exc)
        self._update_song_timing()
        if self.path:
            self._compile()
        else:
            self.refresh_guide(None)
            self._update_coverage_badge(None)
            self._update_compile_badge(None)

    def _on_kb_scope_changed(self, _idx=0):
        if not hasattr(self, "cmb_kb_scope"):
            return
        scope = str(self.cmb_kb_scope.currentData() or "all")
        if scope not in ("all", "melody", "accomp"):
            scope = "all"
        if scope == self.kb_track_scope:
            return
        self.kb_track_scope = scope
        try:
            PC.save({"kb_track_scope": scope})
        except Exception:
            pass
        self._raw_notes_cache = None
        self.timelines.clear()
        self.custom_role_notes.clear()
        self._append_log("36 键音轨范围：%s" % self.cmb_kb_scope.currentText())
        self._update_range_meter()
        self._update_song_timing()
        if self.path:
            self._compile()
        else:
            self._update_coverage_badge(None)
        self._sync_inline_kb_preview()

    def _kb_arrange_opts_dict(self):
        return {
            "kb_auto_accomp": bool(getattr(self, "kb_auto_accomp", True)),
            "kb_enable_arpeggio": bool(getattr(self, "kb_p2_arpeggio", True)),
            "kb_solo_arpeggio": bool(getattr(self, "kb_solo_arpeggio", True)),
            "kb_p2_arpeggio": bool(getattr(self, "kb_p2_arpeggio", True)),
            "kb_enable_chord_rhythm": bool(getattr(self, "kb_enable_chord_rhythm", True)),
            "kb_strum_roll": bool(getattr(self, "kb_strum_roll", False)),
            "kb_arp_pattern": str(getattr(self, "kb_arp_pattern", "ascending")),
        }

    def _selected_kb_radio_key(self):
        radios = getattr(self, "kb_role_radios", None) or {}
        for k, rb in radios.items():
            if rb.isChecked():
                return k
        return "raw"

    def _sync_kb_arrange_controls(self):
        if not hasattr(self, "kb_role_radios"):
            return
        cur = self._selected_kb_radio_key()
        is_raw = (cur == "raw")
        is_solo = (cur == "solo")
        is_chord = (cur == "duet_rhythm")
        is_arp = (cur == "duet_rhythm_fret")

        if is_solo:
            auto_on = bool(self.chk_kb_auto_accomp.isChecked())
            self.kb_arrange_mode = "solo" if auto_on else "duet_lead"
        else:
            self.kb_arrange_mode = cur

        def _set_sub(w, on, tip_on, tip_off="请先选中上方对应角色后生效"):
            if w is None:
                return
            w.setEnabled(bool(on))
            w.setStyleSheet("" if on else "color: #64748B;")
            w.setToolTip(tip_on if on else tip_off)

        _set_sub(getattr(self, "lbl_kb_scope", None), is_raw, "按选定音轨范围读取原谱")
        _set_sub(getattr(self, "cmb_kb_scope", None), is_raw, "切换原谱读取的音轨范围")

        _set_sub(getattr(self, "chk_kb_auto_accomp", None), is_solo,
                 "开启：主旋律+低音伴奏；关闭：纯 1P 主音旋律")
        solo_arp_en = is_solo and bool(self.chk_kb_auto_accomp.isChecked())
        _set_sub(
            getattr(self, "chk_kb_solo_arp", None), solo_arp_en,
            "仅对「1P 独奏」有效：旋律长空隙自动填入柔和和弦琶音",
            "关闭智能伴奏或切换至其他角色后停用" if is_solo else "请先选中「1P 独奏 / 主音」",
        )

        _set_sub(getattr(self, "chk_kb_chord_rhythm", None), is_chord,
                 "按节拍网格生成律动柱式；关闭则每和弦只按 1 次")
        _set_sub(getattr(self, "chk_kb_strum_roll", None), is_chord,
                 "同一柱式和弦声部按 18ms 依次错峰击键，模拟拨片上下扫弦")

        _set_sub(getattr(self, "chk_kb_arpeggio", None), is_arp,
                 "仅对「2P 伴奏·琶音/根音」有效：开启后按拍点分解和弦")
        pat_en = is_arp and bool(self.chk_kb_arpeggio.isChecked())
        _set_sub(
            getattr(self, "cmb_kb_arp_pattern", None), pat_en,
            "选择 2P 分解琶音的演奏形态（从低到高 / 往返折返 / 根音交替）",
            "开启分解琶音后可选形态" if is_arp else "请先选中「2P 伴奏 · 琶音/根音」",
        )

    def set_kb_arrange_mode(self, mode):
        """切换 36 键编配角色（支持 raw / solo / duet_lead / duet_rhythm / duet_rhythm_fret）。"""
        mode = str(mode or "raw").strip().lower()
        if mode not in dict(KB_ARRANGE_MODES):
            mode = "raw"
        radio_key = "solo" if mode in ("solo", "duet_lead") else mode
        if hasattr(self, "kb_role_radios") and radio_key in self.kb_role_radios:
            rb = self.kb_role_radios[radio_key]
            rb.blockSignals(True)
            rb.setChecked(True)
            rb.blockSignals(False)
        if hasattr(self, "chk_kb_auto_accomp") and mode in ("solo", "duet_lead"):
            want_auto = (mode == "solo")
            self.chk_kb_auto_accomp.blockSignals(True)
            self.chk_kb_auto_accomp.setChecked(want_auto)
            self.chk_kb_auto_accomp.blockSignals(False)
            self.kb_auto_accomp = want_auto
        self.kb_arrange_mode = mode
        try:
            PC.save({"kb_arrange_mode": mode, "kb_auto_accomp": bool(getattr(self, "kb_auto_accomp", True))})
        except Exception:
            pass
        self._sync_kb_arrange_controls()
        self.timelines.clear()
        self.custom_role_notes.clear()
        self._update_song_timing()
        if self.path:
            self._compile()
        else:
            self._update_coverage_badge(None)
        self._sync_inline_kb_preview()

    def _on_kb_role_picked(self, key):
        key = str(key or "raw")
        if key == "solo":
            am = "solo" if self.chk_kb_auto_accomp.isChecked() else "duet_lead"
        else:
            am = key
        self.kb_arrange_mode = am
        try:
            PC.save({"kb_arrange_mode": am})
        except Exception:
            pass
        self._sync_kb_arrange_controls()
        self.timelines.clear()
        self.custom_role_notes.clear()
        self._append_log("36 键智能编配：%s" % dict(KB_ARRANGE_MODES).get(am, am))
        self._update_song_timing()
        if self.path:
            self._compile()
        else:
            self._update_coverage_badge(None)
        self._sync_inline_kb_preview()

    def _on_kb_opt_changed(self, *_args):
        if hasattr(self, "chk_kb_auto_accomp"):
            self.kb_auto_accomp = bool(self.chk_kb_auto_accomp.isChecked())
        if hasattr(self, "chk_kb_solo_arp"):
            self.kb_solo_arpeggio = bool(self.chk_kb_solo_arp.isChecked())
        if hasattr(self, "chk_kb_arpeggio"):
            self.kb_p2_arpeggio = bool(self.chk_kb_arpeggio.isChecked())
            self.kb_enable_arpeggio = self.kb_p2_arpeggio
        if hasattr(self, "chk_kb_chord_rhythm"):
            self.kb_enable_chord_rhythm = bool(self.chk_kb_chord_rhythm.isChecked())
        if hasattr(self, "chk_kb_strum_roll"):
            self.kb_strum_roll = bool(self.chk_kb_strum_roll.isChecked())
        if hasattr(self, "cmb_kb_arp_pattern"):
            self.kb_arp_pattern = str(self.cmb_kb_arp_pattern.currentData() or "ascending")
        self._sync_kb_arrange_controls()
        save_d = self._kb_arrange_opts_dict()
        save_d["kb_arrange_mode"] = self.kb_arrange_mode
        try:
            PC.save(save_d)
        except Exception:
            pass
        self.timelines.clear()
        self.custom_role_notes.clear()
        if self.path and self.kb_arrange_mode != "raw":
            self._update_song_timing()
            self._compile()

    def _on_transpose_changed(self, val):
        val = max(-12, min(12, int(val)))
        if val == self.transpose:
            return
        self.transpose = val
        try:
            PC.save({"transpose": val})
        except Exception:
            pass
        self.timelines.clear()
        self.custom_role_notes.clear()
        self._append_log("36 键移调：%s"
                         % ("%+d 半音" % val if val != 0 else "0（原调）"))
        self._update_range_meter()
        self._update_song_timing()
        if self.path:
            self._compile()
        else:
            self._update_coverage_badge(None)
        self._sync_inline_kb_preview()

    def _get_current_raw_notes(self):
        """读取当前曲目按 36 键音轨范围筛选后的原始音符列表（带路径+范围缓存）。"""
        if not self.path or not os.path.isfile(str(self.path)):
            return []
        scope = self.kb_track_scope or "all"
        cache = getattr(self, "_raw_notes_cache", None)
        if cache and cache[0] == self.path and cache[1] == scope:
            return cache[2]
        notes = []
        try:
            if KL is not None and hasattr(KL, "select_notes_by_scope"):
                selected, _sel_info = KL.select_notes_by_scope(
                    self.path, track_scope=scope)
                notes = list(selected or [])
            else:
                flat, _meta = ML.read_flat_notes(self.path, drop_drums=True)
                if scope == "all":
                    notes = list(flat)
                else:
                    import mido
                    mid = mido.MidiFile(self.path)
                    names = [getattr(tr, "name", "") or "" for tr in mid.tracks]
                    mel_idx, acc_idx = set(), set()
                    for idx, name in enumerate(names):
                        if MC.midi_adapter.is_chords_track(name):
                            acc_idx.add(idx)
                        elif MC.midi_adapter.is_melody_track(name):
                            mel_idx.add(idx)
                    if scope == "melody":
                        if mel_idx:
                            notes = [n for n in flat if n.track_idx in mel_idx]
                        elif acc_idx:
                            notes = [n for n in flat if n.track_idx not in acc_idx]
                        else:
                            notes = list(flat)
                    else:
                        if acc_idx:
                            notes = [n for n in flat if n.track_idx in acc_idx]
                        elif mel_idx:
                            notes = [n for n in flat if n.track_idx not in mel_idx]
                        else:
                            notes = list(flat)
        except Exception:
            notes = []
        self._raw_notes_cache = (self.path, scope, notes)
        return notes

    def _compute_range_stats(self, notes, instrument_mode, shift=0):
        """计算指定移调下的音域命中统计与最佳移调推荐。"""
        pitches = []
        for n in (notes or []):
            p = n.get("pitch") if isinstance(n, dict) else getattr(n, "pitch", None)
            if p is not None:
                pitches.append(int(p))
        total = len(pitches)
        lo, hi = INSTRUMENT_RANGES.get(instrument_mode, (52, 87))
        if not total:
            return {
                "in_range": 0, "total": 0, "coverage": 0.0,
                "best_shift": 0, "best_coverage": 0.0, "low": lo, "high": hi,
            }
        shifted = [{"pitch": p + int(shift)} for p in pitches]
        if KL is not None and hasattr(KL, "analyze_range_and_transpose"):
            cur_info = KL.analyze_range_and_transpose(shifted, instrument_mode)
            base_info = (cur_info if shift == 0
                         else KL.analyze_range_and_transpose(
                             [{"pitch": p} for p in pitches], instrument_mode))
            return {
                "in_range": int(cur_info.get("in_range", 0)),
                "total": int(cur_info.get("total", total)),
                "coverage": float(cur_info.get("coverage", 0.0)),
                "best_shift": int(base_info.get("best_shift", 0)),
                "best_coverage": float(base_info.get("best_coverage", 0.0)),
                "low": int(cur_info.get("low", lo)),
                "high": int(cur_info.get("high", hi)),
            }
        in_cur = sum(1 for p in pitches if lo <= p + int(shift) <= hi)
        best_s, best_cnt = 0, -1
        for s in range(-12, 13):
            cnt = sum(1 for p in pitches if lo <= p + s <= hi)
            if cnt > best_cnt or (cnt == best_cnt and abs(s) < abs(best_s)):
                best_cnt, best_s = cnt, s
        return {
            "in_range": in_cur,
            "total": total,
            "coverage": in_cur / float(total),
            "best_shift": best_s,
            "best_coverage": best_cnt / float(total),
            "low": lo,
            "high": hi,
        }

    def _update_coverage_badge(self, tl=None):
        """刷新 36 键编配面板上的音域覆盖率徽章。"""
        if not hasattr(self, "lbl_coverage_badge"):
            return
        if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL:
            return
        if not self.path:
            self.lbl_coverage_badge.setText("音域：--")
            self.lbl_coverage_badge.setToolTip("音域覆盖：选歌后自动统计命中率")
            self.lbl_coverage_badge.setObjectName("accentPill")
            _refresh_widget_style(self.lbl_coverage_badge)
            return
        ri = (tl or {}).get("range_info") if isinstance(tl, dict) else None
        if isinstance(ri, dict) and ri.get("total"):
            in_cnt = int(ri.get("in_range") or 0)
            total = int(ri.get("total") or 0)
            cov = float(ri.get("coverage") if ri.get("coverage") is not None
                        else (in_cnt / float(total) if total else 0.0))
        else:
            st = self._compute_range_stats(
                self._get_current_raw_notes(), self.instrument_mode, self.transpose)
            in_cnt, total, cov = st["in_range"], st["total"], st["coverage"]
        if total <= 0:
            self.lbl_coverage_badge.setText("音域：0/0")
            self.lbl_coverage_badge.setToolTip("当前音轨范围内没有检测到音符")
            self.lbl_coverage_badge.setObjectName("goldPill")
            _refresh_widget_style(self.lbl_coverage_badge)
            return
        pct = cov * 100.0
        tr_tag = " · %+d" % self.transpose if self.transpose != 0 else ""
        if pct >= 99.9:
            self.lbl_coverage_badge.setText(
                "命中 100%%（%d/%d%s）" % (in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setToolTip(
                "音域命中 100%%（%d/%d%s）" % (in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setObjectName("accentPill")
        elif pct >= 85.0:
            self.lbl_coverage_badge.setText(
                "命中 %.0f%%（%d/%d%s）" % (pct, in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setToolTip(
                "音域命中 %.1f%%（%d/%d%s · 越界音已自动折叠）"
                % (pct, in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setObjectName("accentPill")
        else:
            self.lbl_coverage_badge.setText(
                "命中 %.0f%%（%d/%d%s · 建议移调）" % (pct, in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setToolTip(
                "音域命中 %.1f%%（%d/%d%s · 建议点击「智能移调」优化音域）"
                % (pct, in_cnt, total, tr_tag))
            self.lbl_coverage_badge.setObjectName("goldPill")
        _refresh_widget_style(self.lbl_coverage_badge)

    def _smart_transpose(self):
        """一键智能移调：调用 analyze_range_and_transpose 自动设最佳移调并重编。"""
        if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL:
            return
        if not self.path:
            QMessageBox.information(self, "还没选歌", "先在左侧曲库选择一首曲目，再计算智能移调。")
            return
        notes = self._get_current_raw_notes()
        if not notes:
            QMessageBox.information(self, "无可分析音符", "当前音轨范围内没有检测到有效音符。")
            return
        st = self._compute_range_stats(notes, self.instrument_mode, shift=0)
        best_shift = int(st.get("best_shift", 0) or 0)
        best_cov = float(st.get("best_coverage", 0.0) or 0.0) * 100.0
        cur_cov = float(st.get("coverage", 0.0) or 0.0) * 100.0
        self._append_log(
            "智能移调分析（%s）：原调命中 %.1f%% → 推荐 %+d 半音（命中 %.1f%%）"
            % (INSTRUMENT_SHORT_CN.get(self.instrument_mode, self.instrument_mode),
               cur_cov, best_shift, best_cov))
        if hasattr(self, "spin_transpose") and self.spin_transpose.value() != best_shift:
            self.spin_transpose.setValue(best_shift)
        else:
            self._update_coverage_badge(self._active_timeline())

    def _collect_used_pitches(self):
        """统计当前曲目（未移调）各 MIDI 音高的出现频次，供 36 键位图热度展示。"""
        counts = {}
        for n in self._get_current_raw_notes():
            p = n.get("pitch") if isinstance(n, dict) else getattr(n, "pitch", None)
            if p is None:
                continue
            pi = int(p)
            counts[pi] = counts.get(pi, 0) + 1
        return counts

    def _existing_midi_live_dialog(self):
        """获取当前仍有效的 MidiLiveDialog 实例（兼容主窗口共享）。"""
        cands = [getattr(self, "_midi_live_dlg", None)]
        try:
            host = self.window()
            if host is not None and host is not self:
                cands.append(getattr(host, "_midi_live_dlg", None))
        except Exception:
            pass
        for dlg in cands:
            if dlg is None:
                continue
            try:
                _ = dlg.isVisible()
                return dlg
            except RuntimeError:
                continue
            except Exception:
                return dlg
        return None

    def _on_midi_live_mode_changed(self, mode):
        """实时 MIDI 弹窗内切换乐器模式时同步回演奏台。"""
        if mode in INSTRUMENT_MODES and mode != self.instrument_mode:
            self.set_instrument_mode(mode)

    def _on_midi_live_before_start(self):
        """实时 MIDI 引擎即将启动时，自动停止正在进行的曲谱自动演奏。"""
        if QThread.currentThread() != self.thread():
            try:
                self.sig_stop_play.emit()
            except Exception:
                pass
            return
        self._on_midi_live_before_start_main()

    def _on_midi_live_before_start_main(self):
        if self.player and self.player.is_alive():
            self.stop_play()
            self._append_log("已自动停止曲谱自动演奏，切换为实时 MIDI 键盘直弹")

    def _stop_midi_live_if_running(self):
        """开始曲谱自动演奏前，若实时 MIDI 直弹正在运行则自动停止。"""
        stopped = False
        seen = set()
        cands = []
        dlg = self._existing_midi_live_dialog()
        if dlg is not None:
            cands.append(dlg)
        try:
            for w in QApplication.topLevelWidgets():
                if type(w).__name__ == "MidiLiveDialog":
                    cands.append(w)
        except Exception:
            pass
        for item in cands:
            if id(item) in seen:
                continue
            seen.add(id(item))
            if hasattr(item, "stop_if_running"):
                try:
                    was_running = bool(
                        getattr(item, "is_running", lambda: False)()
                        or getattr(getattr(item, "engine", None), "is_running", lambda: False)()
                    )
                    ret = item.stop_if_running()
                    if ret or was_running:
                        stopped = True
                except Exception as exc:
                    if _LOG:
                        _LOG.debug("stop_if_running failed: %s", exc)
        if stopped:
            self._append_log("已自动停止实时 MIDI 直弹，切换为曲谱自动演奏")
        return stopped

    def open_midi_live(self):
        """优先切换至主窗内嵌的实时 MIDI 键盘直弹页（零弹窗）；独立运行才退化为弹窗。"""
        try:
            host = self.window()
            if host is not None and host is not self and hasattr(host, "open_midi_live"):
                dlg = host.open_midi_live()
                if dlg is not None:
                    self._midi_live_dlg = dlg
                    return dlg
        except Exception as exc:
            if _LOG:
                _LOG.debug("host.open_midi_live failed: %s", exc)
        dlg = self._existing_midi_live_dialog()
        if dlg is not None:
            self._midi_live_dlg = dlg
            if hasattr(dlg, "set_instrument_mode"):
                try:
                    dlg.set_instrument_mode(self.instrument_mode)
                except Exception:
                    pass
            if hasattr(dlg, "sync_theme"):
                try:
                    dlg.sync_theme(_current_theme_mode())
                except Exception:
                    pass
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        try:
            from midi_live_ui import MidiLiveDialog
        except Exception as exc:
            QMessageBox.warning(self, "MIDI 直弹不可用",
                                "载入 player/midi_live_ui.py 失败：%s" % exc)
            return None
        host = None
        try:
            host = self.window()
        except Exception:
            host = None
        kw = {
            "instrument_mode": self.instrument_mode,
            "io": getattr(self, "io", None),
            "on_mode_changed": self._on_midi_live_mode_changed,
            "on_before_start": self._on_midi_live_before_start,
            "parent": host or self,
        }
        dlg = MidiLiveDialog(**_filter_supported_kwargs(MidiLiveDialog, kw))
        self._midi_live_dlg = dlg
        if host is not None and host is not self and hasattr(host, "_midi_live_dlg"):
            host._midi_live_dlg = dlg
        try:
            dlg.destroyed.connect(lambda _o=None: setattr(self, "_midi_live_dlg", None))
        except Exception:
            pass
        dlg.setModal(False)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _sync_inline_kb_preview(self):
        dlg = getattr(self, "_kb_preview_inline", None)
        if dlg is None:
            return
        mode = (self.instrument_mode if self.instrument_mode in
                (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                else INSTRUMENT_PIANO_36)
        used = self._collect_used_pitches()
        if hasattr(dlg, "update_preview"):
            try:
                dlg.update_preview(
                    instrument_mode=mode,
                    used_pitches=used,
                    transpose=int(self.transpose or 0),
                )
            except Exception:
                pass

    def toggle_inline_keyboard_preview(self, show=None):
        """在 36 键编配区正下方原地展开/收起内联 36 键位图。"""
        if not hasattr(self, "kb_preview_host"):
            return None
        want = (not self.kb_preview_host.isVisible()) if show is None else bool(show)
        if not want:
            self.kb_preview_host.setVisible(False)
            if hasattr(self, "btn_kb_preview"):
                self.btn_kb_preview.blockSignals(True)
                self.btn_kb_preview.setChecked(False)
                self.btn_kb_preview.blockSignals(False)
            return getattr(self, "_kb_preview_inline", None)

        mode = (self.instrument_mode if self.instrument_mode in
                (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36)
                else INSTRUMENT_PIANO_36)
        used = self._collect_used_pitches()
        dlg = getattr(self, "_kb_preview_inline", None)
        if dlg is None:
            try:
                from keyboard_preview import KeyboardPreviewDialog
            except Exception as exc:
                QMessageBox.warning(self, "36 键位图不可用",
                                    "载入 player/keyboard_preview.py 失败：%s" % exc)
                if hasattr(self, "btn_kb_preview"):
                    self.btn_kb_preview.setChecked(False)
                return None
            dlg = KeyboardPreviewDialog(
                instrument_mode=mode,
                used_pitches=used,
                transpose=int(self.transpose or 0),
                parent=self.kb_preview_host,
                embedded=True,
                on_back=lambda: self.toggle_inline_keyboard_preview(False),
            )
            self._kb_preview_inline = dlg
            self._kb_preview_lay.addWidget(dlg, 1)
            dlg.show()
        else:
            self._sync_inline_kb_preview()
        self.kb_preview_host.setVisible(True)
        if hasattr(self, "btn_kb_preview"):
            self.btn_kb_preview.blockSignals(True)
            self.btn_kb_preview.setChecked(True)
            self.btn_kb_preview.blockSignals(False)
        return dlg

    def open_keyboard_preview(self):
        """切入内联 36 键位图展示（不再弹窗）。"""
        return self.toggle_inline_keyboard_preview()

    def load(self, path):
        self.path = path
        self._raw_notes_cache = None
        self._update_song_timing()
        self.timelines = {}
        self.custom_role_notes = {}
        self.lbl_file.setText(os.path.basename(path))
        self._append_log("载入：%s" % path)
        self._update_range_meter()
        self._update_coverage_badge(None)
        self._compile()

    def _effective_role(self):
        if self._role_pick == MC.ROLE_SOLO:
            return MC.ROLE_SOLO if self.chk_auto_accomp.isChecked() else MC.ROLE_DUET_LEAD
        return self._role_pick

    def _on_auto_accomp_toggled(self, on):
        PC.save({"enable_auto_accomp": bool(on)})
        for r in (MC.ROLE_DUET_RHYTHM_FRET, MC.ROLE_DUET_RHYTHM):
            rb = self.radios.get(r)
            if rb is not None:
                rb.setEnabled(bool(on))
        if not on and self._role_pick in (MC.ROLE_DUET_RHYTHM_FRET, MC.ROLE_DUET_RHYTHM):
            self._role_pick = MC.ROLE_SOLO
        self.role = self._effective_role()
        rb_solo = self.radios.get(MC.ROLE_SOLO)
        if rb_solo is not None and not rb_solo.isChecked():
            rb_solo.setChecked(True)
        self.timelines.clear()
        self._update_switch_hints(self.role)
        self._append_log("和弦自动生成伴奏已%s" % ("开启" if on else "关闭（1P 纯主音）"))
        if self.path:
            self._compile()

    def choose_role(self, role):
        self._role_pick = role
        self.role = self._effective_role()
        self._update_switch_hints(self.role)
        self._update_song_timing()
        if self.path:
            self._compile()

    def _update_switch_hints(self, role):
        """按当前角色启用/置灰三个伴奏开关，并给出对应提示。"""
        arp_on = role in (MC.ROLE_SOLO, MC.ROLE_DUET_RHYTHM_FRET)
        rhy_on = role == MC.ROLE_DUET_RHYTHM
        str_on = role == MC.ROLE_DUET_RHYTHM
        specs = (
            (getattr(self, "chk_arpeggio", None), arp_on,
             "当前生效：1P 独奏句间点缀 / 2P 琶音从低到高顺拨",
             "当前角色不使用「和弦内音琶音」（仅 1P 独奏与 2P 琶音·根音有效）"),
            (getattr(self, "chk_chord_rhythm", None), rhy_on,
             "当前生效：2P 柱式/扫弦按整拍律动（和弦段与品格段同步）",
             "当前角色不使用「和弦扫弦律动」（仅 2P 柱式·扫弦有效）"),
            (getattr(self, "chk_strum_mode", None), str_on,
             "当前生效：可切换 2P 和弦段用柱式抓弦 / 鼠标右键下扫",
             "当前角色不使用「柱式/扫弦」开关（仅 2P 柱式·扫弦有效）"),
        )
        for w, on, tip_on, tip_off in specs:
            if w is None:
                continue
            try:
                w.setEnabled(bool(on))
                w.setStyleSheet("" if on else "color: #64748B;")
                w.setToolTip(tip_on if on else tip_off)
            except Exception:
                pass
        auto_on = True
        try:
            auto_on = bool(self.chk_auto_accomp.isChecked())
        except Exception:
            pass
        for r in (MC.ROLE_DUET_RHYTHM_FRET, MC.ROLE_DUET_RHYTHM):
            rb = getattr(self, "radios", {}).get(r)
            if rb is not None:
                try:
                    rb.setEnabled(bool(auto_on))
                except Exception:
                    pass

    def _on_arpeggio_toggled(self, checked):
        PC.save({"enable_arpeggio": bool(checked)})
        for r in (MC.ROLE_SOLO, MC.ROLE_DUET_RHYTHM_FRET):
            self.timelines.pop(r, None)
        self._append_log("和弦内音琶音已%s" % ("开启（从低到高顺拨）" if checked else "关闭（只垫根音）"))
        if self.path:
            self._compile()

    def _on_chord_rhythm_toggled(self, checked):
        PC.save({"enable_chord_rhythm": bool(checked)})
        self.timelines.pop(MC.ROLE_DUET_RHYTHM, None)
        self._append_log("2P 和弦扫弦律动已%s" % ("开启（按整拍律动）" if checked
                                                   else "关闭（每个切片只弹 1 次）"))
        if self.path:
            self._compile()

    def _on_strum_mode_toggled(self, checked):
        PC.save({"chord_strum_mode": bool(checked)})
        self.timelines.pop(MC.ROLE_DUET_RHYTHM, None)
        self._append_log("2P 和弦段发声方式：%s"
                         % ("鼠标右键下扫" if checked else "多弦同按柱式抓弦（默认）"))
        if self.path:
            self._compile()

    def _compile(self):
        if not self.path:
            return
        self._compile_seq += 1
        seq = self._compile_seq
        self.lbl_guide.setText("正在编译……")

        def ok(tl, _seq=seq):
            try:
                if isinstance(tl, dict):
                    tl["_ui_compile_seq"] = _seq
                self.sig_compiled.emit(tl)
            except Exception:
                pass

        def bad(exc, _seq=seq):
            try:
                if _seq == self._compile_seq:
                    self.sig_failed.emit(exc)
            except Exception:
                pass

        enable_arpeggio = self.chk_arpeggio.isChecked() if hasattr(self, "chk_arpeggio") else True
        arpeggio_pattern = "ascending"
        enable_chord_rhythm = (self.chk_chord_rhythm.isChecked()
                               if hasattr(self, "chk_chord_rhythm") else True)
        chord_strum_mode = (self.chk_strum_mode.isChecked()
                            if hasattr(self, "chk_strum_mode") else False)
        timing, prune = None, 0.0
        if self._follow_active() and getattr(self, "_song_timing", None):
            timing = MC.Timing(dict(self._song_timing["timing"]))
        if hasattr(self, "sld_prune"):
            prune = self.sld_prune.value() / 100.0
        w = CompileWorker(self.path, self.role, ok, bad,
                          enable_arpeggio=enable_arpeggio,
                          arpeggio_pattern=arpeggio_pattern,
                          enable_chord_rhythm=enable_chord_rhythm,
                          chord_strum_mode=chord_strum_mode,
                          timing=timing, max_prune_ratio=prune,
                          instrument_mode=self.instrument_mode,
                          kb_track_scope=self.kb_track_scope,
                          transpose=int(self.transpose or 0),
                          kb_arrange_mode=getattr(self, "kb_arrange_mode", "raw"),
                          kb_auto_accomp=bool(getattr(self, "kb_auto_accomp", True)),
                          kb_enable_arpeggio=bool(getattr(self, "kb_p2_arpeggio", True)),
                          kb_enable_chord_rhythm=bool(getattr(self, "kb_enable_chord_rhythm", True)),
                          kb_strum_roll=bool(getattr(self, "kb_strum_roll", False)),
                          kb_arp_pattern=str(getattr(self, "kb_arp_pattern", "ascending")),
                          kb_solo_arpeggio=bool(getattr(self, "kb_solo_arpeggio", True)),
                          kb_p2_arpeggio=bool(getattr(self, "kb_p2_arpeggio", True)))
        w.start()

    def _on_compiled(self, tl):
        if not isinstance(tl, dict):
            return
        seq = tl.pop("_ui_compile_seq", None)
        if seq is not None and seq != self._compile_seq:
            return
        tl_mode = tl.get("instrument_mode") or INSTRUMENT_GUITAR_WHEEL
        if tl_mode == INSTRUMENT_GUITAR_WHEEL:
            self.timelines[tl.get("role") or self.role] = tl
        else:
            self.timelines[tl_mode] = tl
            self.timelines[self.role] = tl
        try:
            self._append_log(MC.report_text(tl).replace("\n", " ｜ "))
        except Exception:
            st = tl.get("stats") or {}
            self._append_log("编译完成：%s（%d 音符 / %d 动作）"
                             % (tl.get("role_cn") or tl_mode,
                                int(st.get("notes") or 0),
                                len(tl.get("events") or [])))
        dur = float((tl.get("hw_stats") or {}).get("duration")
                    or (tl.get("stats") or {}).get("duration") or 0.0)
        self._total_duration = dur
        if hasattr(self, "lbl_timecode") and not (self.player and self.player.is_alive()):
            self.lbl_timecode.setText("00:00 / %s" % _fmt_mmss(dur))
        self.refresh_guide(tl)
        self._update_compile_badge(tl)
        self._update_coverage_badge(tl)
        self._update_range_meter()
        if tl_mode != INSTRUMENT_GUITAR_WHEEL:
            self._sync_inline_kb_preview()

    def refresh_guide(self, tl=None):
        """按当前乐器模式刷新游戏内准备提示与统计信息。"""
        tl = tl or self._active_timeline()
        mode = self.instrument_mode
        if not tl:
            if mode == INSTRUMENT_GUITAR_36:
                self.lbl_guide.setText(
                    "请在左侧曲库选择一首曲目\n"
                    "【吉他 · 钢琴键 36 键】游戏内切入「吉他 · 钢琴键模式」（E3~D#6），无需轮盘，支持长按延音"
                )
            elif mode == INSTRUMENT_PIANO_36:
                self.lbl_guide.setText(
                    "请在左侧曲库选择一首曲目\n"
                    "【钢琴 · 36 键】游戏内切入「钢琴模式」（C4~B6，Shift # / Ctrl b 互斥切换），无延音，无需轮盘"
                )
            else:
                self.lbl_guide.setText("请在左侧曲库选择一首曲目")
            return

        if mode in (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36):
            st = tl.get("stats") or {}
            hw = tl.get("hw_stats") or {}
            ri = tl.get("range_info") or {}
            if ri and ri.get("total"):
                cov_pct = float(ri.get("coverage") or 0.0) * 100.0
                cov_str = "%.0f%%（%d/%d）" % (
                    cov_pct, int(ri.get("in_range") or 0), int(ri.get("total") or 0))
            else:
                rs = self._compute_range_stats(
                    self._get_current_raw_notes(), mode, self.transpose)
                cov_str = ("%.0f%%（%d/%d）" % (rs["coverage"] * 100.0,
                                               rs["in_range"], rs["total"])
                           if rs["total"] else "--")
            tr_val = int(self.transpose or 0)
            tr_str = "%+d" % tr_val if tr_val != 0 else "原调"
            voices = int(st.get("voices") or st.get("notes") or 0)
            hw_cnt = int(hw.get("hw_events") or len(tl.get("events") or []))
            dur = float(hw.get("duration") or st.get("duration") or 0.0)
            if mode == INSTRUMENT_GUITAR_36:
                self.lbl_guide.setText(
                    "游戏内切「吉他 · 钢琴键模式」（E3~D#6 · 支持延音 · 无需轮盘）\n"
                    "覆盖 %s ｜ 移调 %s ｜ %d 音/%d 动作 (%.1fs) ｜ F8 开弹，F12 停止"
                    % (cov_str, tr_str, voices, hw_cnt, dur))
            else:
                self.lbl_guide.setText(
                    "游戏内切「钢琴模式」（C4~B6 · Shift#/Ctrl b 互斥 · 无延音）\n"
                    "覆盖 %s ｜ 移调 %s ｜ %d 音/%d 动作 (%.1fs) ｜ F8 开弹，F12 停止"
                    % (cov_str, tr_str, voices, hw_cnt, dur))
            return

        st = tl.get("start") or {}
        self.lbl_guide.setText(
            "吉他模式：%s ｜ 起始页码：第 %s 页（%s）\n"
            "切回游戏按 F8 开始/暂停，按 F12 停止"
            % (st.get("mode_cn") or "品格", st.get("page") or 1,
               st.get("wheel") or "无需翻页"))

    def sync_external(self, path, timelines=None):
        self.path = str(path)
        self._raw_notes_cache = None
        self.custom_role_notes = {}
        try:
            self.lbl_file.setText(os.path.basename(self.path))
        except Exception:
            pass
        if timelines:
            self.timelines.update(dict(timelines))
        if self.instrument_mode != INSTRUMENT_GUITAR_WHEEL:
            self._compile()
            return
        tl = self._active_timeline() or next(iter(self.timelines.values()), None)
        self.refresh_guide(tl)
        self._update_compile_badge(tl)
        st = (tl or {}).get("stats") or {}
        if st:
            self._append_log("当前角色统计：%d 发声 / %d 翻页 / 晚出 %d（最大 %.0f ms）"
                             % (int(st.get("voices") or 0), int(st.get("turns") or 0),
                                int(st.get("late") or 0),
                                float(st.get("max_late") or 0) * 1000))

    def apply_custom_timeline(self, tl, role_notes=None):
        if not tl:
            return False
        if role_notes:
            self.custom_role_notes = {k: [dict(n) for n in v]
                                      for k, v in role_notes.items()}
        tl_mode = tl.get("instrument_mode") or self.instrument_mode
        if tl_mode == INSTRUMENT_GUITAR_WHEEL:
            self.timelines[tl.get("role") or self.role] = tl
        else:
            self.timelines[tl_mode] = tl
            self.timelines[self.role] = tl
        dur = float((tl.get("hw_stats") or {}).get("duration")
                    or (tl.get("stats") or {}).get("duration") or 0.0)
        self._total_duration = dur
        if hasattr(self, "lbl_timecode") and not (self.player and self.player.is_alive()):
            self.lbl_timecode.setText("00:00 / %s" % _fmt_mmss(dur))
        self._append_log("已应用卷帘微调：%s（%d 条动作，晚出 %d）"
                         % (tl.get("role_cn") or tl_mode, len(tl.get("events") or []),
                            (tl.get("hw_stats") or {}).get("late", 0)))
        self.refresh_guide(tl)
        self._update_compile_badge(tl)
        self._update_coverage_badge(tl)
        return True

    def on_roll_flags_changed(self, enable_arpeggio, enable_chord_rhythm):
        try:
            PC.save({"enable_arpeggio": bool(enable_arpeggio),
                     "enable_chord_rhythm": bool(enable_chord_rhythm)})
        except Exception:
            pass
        self.timelines = {}
        self._append_log("卷帘内切换：琶音=%s，扫弦律动=%s"
                         % ("开" if enable_arpeggio else "关",
                            "开" if enable_chord_rhythm else "关"))
        if self.path:
            self._compile()

    def open_piano_roll(self):
        """打开钢琴卷帘：直接进当前乐器模式与角色的转译序列，并延续上一次应用的微调。"""
        if not self.path:
            QMessageBox.information(self, "还没选歌", "先选一份 MIDI，再打开钢琴卷帘。")
            return None
        try:
            from piano_roll import PianoRollDialog
        except Exception as exc:
            QMessageBox.warning(self, "钢琴卷帘不可用", "载入 player/piano_roll.py 失败：%s" % exc)
            return None
        roll_kw = {
            "role": self.role,
            "parent": self,
            "on_apply": self.apply_custom_timeline,
            "instrument_mode": self.instrument_mode,
            "kb_track_scope": self.kb_track_scope,
            "transpose": int(self.transpose or 0),
            "kb_arrange_mode": getattr(self, "kb_arrange_mode", "raw"),
            "kb_arrange_opts": self._kb_arrange_opts_dict(),
        }
        try:
            dlg = PianoRollDialog(self.path, **_filter_supported_kwargs(PianoRollDialog, roll_kw))
        except Exception as exc:  # noqa: BLE001
            self._append_log("× 打开钢琴卷帘失败：%s" % exc)
            QMessageBox.warning(self, "无法打开钢琴卷帘", "当前曲目解析失败，无法在卷帘中打开：\n%s" % exc)
            return None
        try:
            target_key = self.role if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL else self.instrument_mode
            if self.custom_role_notes:
                dlg.model.role_notes = {k: [dict(n) for n in v]
                                        for k, v in self.custom_role_notes.items()}
                if target_key in dlg.model.role_notes:
                    dlg.model.notes = [dict(n) for n in dlg.model.role_notes[target_key]]
                    if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL:
                        if hasattr(dlg.model, "_normalize_role_note_flags"):
                            dlg.model._normalize_role_note_flags()
                        if hasattr(dlg.model, "_sync_role_mode_state"):
                            dlg.model._sync_role_mode_state()
                    dlg.model.compile()
                    dlg._fill_tracks()
                    dlg._refresh_buttons()
                    dlg.canvas.viewport().update()
        except Exception:
            pass
        self._active_roll_dlg = dlg
        dlg.setModal(False)
        dlg.finished.connect(lambda _r: setattr(self, "_active_roll_dlg", None))
        dlg.show()
        dlg.raise_()
        return dlg

    def _on_failed(self, exc):
        self.lbl_guide.setText("编译失败：%s" % exc)
        self._append_log("× 编译失败：%s" % exc)

    def _auto_calibrate(self):
        try:
            from gui_main import WheelOverlayDialog
        except Exception as exc:
            QMessageBox.warning(self, "校准不可用", "请从《异键演奏》主界面打开校准：%s" % exc)
            return
        dlg = WheelOverlayDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            cfg = dlg.compute_ratios()
            self._append_log("√ 指示圈已锁定：x=%.4f y=%.4f r=%.4f"
                             % (cfg["wheel_x_ratio"], cfg["wheel_y_ratio"],
                                cfg["wheel_radius_ratio"]))
            QMessageBox.information(self, "轮盘校准完成",
                                    "x=%.4f  y=%.4f  r=%.4f\n已写入 player_config.json"
                                    % (cfg["wheel_x_ratio"], cfg["wheel_y_ratio"],
                                       cfg["wheel_radius_ratio"]))

    def _on_diag_status(self, text):
        lbl = getattr(self, "_diag_status_lbl", None)
        if lbl is not None:
            try:
                lbl.setText(str(text))
            except RuntimeError:
                pass
            except Exception:
                pass

    def _populate_inline_diag(self):
        if not hasattr(self, "_diag_btns_row"):
            return
        while self._diag_btns_row.count():
            item = self._diag_btns_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        mode = self.instrument_mode
        if mode == INSTRUMENT_GUITAR_36:
            self.lbl_diag_tip.setText(
                "吉他 36 键通道自检（点按钮后 2 秒内切回游戏）：A~Enter / Q~] / 1~=。")
            tests = (
                ("g36_sample", "代表键试音"),
                ("g36_sustain", "并发延音 (A+D+G)"),
                ("g36_sweep", "36 键快速扫音"),
            )
        elif mode == INSTRUMENT_PIANO_36:
            self.lbl_diag_tip.setText(
                "钢琴 36 键通道自检（点按钮后 2 秒内切回游戏）：自然音 + Shift(#) + Ctrl(b)。")
            tests = (
                ("p36_natural", "自然音三八度试音"),
                ("p36_shift", "升号通路 (LShift)"),
                ("p36_ctrl", "降号通路 (LCtrl)"),
            )
        else:
            self.lbl_diag_tip.setText(
                "轮盘通道自检（点按钮后 2 秒内切回游戏吉他界面）：拨弦 / 扇区 / 翻页。")
            tests = (
                (1, "1. 拨弦 (1~6)"),
                (2, "2. 轮盘定位"),
                (3, "3. 选格+拨弦"),
                (4, "4. T/R 翻页"),
            )
        for n, text in tests:
            btn = QPushButton(text)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _=False, nn=n: self._diag_countdown(nn, self._diag_status_lbl))
            self._diag_btns_row.addWidget(btn)
        self._diag_btns_row.addStretch(1)

    def _toggle_inline_diag(self, show=None):
        if not hasattr(self, "diag_inline_box"):
            return None
        want = (not self.diag_inline_box.isVisible()) if show is None else bool(show)
        if want:
            self._populate_inline_diag()
        self.diag_inline_box.setVisible(want)
        if hasattr(self, "btn_diag"):
            self.btn_diag.blockSignals(True)
            self.btn_diag.setChecked(want)
            self.btn_diag.blockSignals(False)
        return self.diag_inline_box

    def _open_diag(self):
        return self._toggle_inline_diag()

    def _diag_countdown(self, n, status):
        if getattr(self, "_diag_busy", False):
            self._on_diag_status("自检正在准备或执行中，请稍候…")
            return
        if self.player and self.player.is_alive():
            self._on_diag_status("请先停止曲谱自动演奏再自检")
            return
        live_dlg = self._existing_midi_live_dialog()
        if live_dlg is not None and bool(getattr(live_dlg, "is_running", lambda: False)()):
            self._on_diag_status("请先停止实时 MIDI 直弹再自检")
            return
        self._diag_busy = True
        try:
            if status is not None:
                status.setText("2 秒后执行……请切回游戏")
        except RuntimeError:
            self._diag_busy = False
            return
        old_timer = getattr(self, "_diag_timer", None)
        if old_timer is not None:
            try:
                old_timer.stop()
                old_timer.deleteLater()
            except Exception:
                pass
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._diag_exec(n, status))
        self._diag_timer = timer
        timer.start(2000)

    def _diag_exec(self, n, status):
        if getattr(self, "_closing", False):
            self._diag_busy = False
            return
        if self.player and self.player.is_alive():
            self._diag_busy = False
            self._on_diag_status("自检已取消：自动演奏进行中")
            return
        try:
            if status is not None:
                status.setText("执行中……")
        except RuntimeError:
            self._diag_busy = False
            return
        try:
            self.sig_log.emit("硬件自检 %s 开始" % n)
        except RuntimeError:
            self._diag_busy = False
            return

        def job():
            io = GameIO()
            held_keys = []
            held_mods = []
            try:
                if not io.has_window():
                    self.sig_log.emit("! 没找到游戏窗口")
                    self.sig_diag_status.emit("! 没找到游戏窗口")
                    return
                if n == 1:
                    for s in "123456":
                        io.pluck_key(s)
                        time.sleep(0.25)
                elif n == 2:
                    m = io.metrics()
                    for sector in range(1, 7):
                        io.move(*io.sector_point(sector, m))
                        time.sleep(0.30)
                    io.move(*io.park_point(m))
                elif n == 3:
                    m = io.metrics()
                    x, y = io.sector_point(1, m)
                    io.move(x, y)
                    io.click("left", hold=0.025)
                    time.sleep(0.02)
                    io.pluck_key("6")
                elif n == 4:
                    m = io.metrics()
                    io.tap("T", hold=0.025)
                    time.sleep(0.035)
                    x, y = io.sector_point(2, m)
                    io.move(x, y)
                    io.click("left", hold=0.025)
                    io.pluck_key("6")
                    time.sleep(0.05)
                    io.tap("R", hold=0.025)
                elif n == "g36_sample":
                    for k in ("A", "G", "Q", "Y", "1", "6", "Enter", "]", "="):
                        if k.isdigit():
                            io.pluck_key(k, hold=0.04)
                        else:
                            io.tap(k, hold=0.04)
                        time.sleep(0.18)
                elif n == "g36_sustain":
                    chord = ("A", "D", "G")
                    for k in chord:
                        io.tap_key_down(k)
                        held_keys.append(k)
                    time.sleep(0.60)
                    for k in reversed(chord):
                        io.tap_key_up(k)
                        if k in held_keys:
                            held_keys.remove(k)
                elif n == "g36_sweep":
                    keys = (
                        "A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'", "Enter",
                        "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]",
                        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
                    )
                    for k in keys:
                        if k.isdigit():
                            io.pluck_key(k, hold=0.035)
                        else:
                            io.tap(k, hold=0.035)
                        time.sleep(0.08)
                elif n == "p36_natural":
                    for k in ("Z", "C", "G", "A", "F", "J", "Q", "R", "U"):
                        io.tap(k, hold=0.04)
                        time.sleep(0.18)
                elif n == "p36_shift":
                    io.tap_key_down("LShift")
                    held_mods.append("LShift")
                    time.sleep(0.02)
                    for k in ("Z", "A"):
                        io.tap_key_down(k)
                        held_keys.append(k)
                        time.sleep(0.06)
                        io.tap_key_up(k)
                        if k in held_keys:
                            held_keys.remove(k)
                        time.sleep(0.22)
                    io.tap_key_up("LShift")
                    if "LShift" in held_mods:
                        held_mods.remove("LShift")
                elif n == "p36_ctrl":
                    io.tap_key_down("LCtrl")
                    held_mods.append("LCtrl")
                    time.sleep(0.02)
                    for k in ("C", "E"):
                        io.tap_key_down(k)
                        held_keys.append(k)
                        time.sleep(0.06)
                        io.tap_key_up(k)
                        if k in held_keys:
                            held_keys.remove(k)
                        time.sleep(0.22)
                    io.tap_key_up("LCtrl")
                    if "LCtrl" in held_mods:
                        held_mods.remove("LCtrl")
                self.sig_log.emit("√ 硬件自检 %s 完成（所有按键已释放）" % n)
                self.sig_diag_status.emit("√ 自检 %s 完成（所有按键已释放）" % n)
            except Exception as exc:
                self.sig_log.emit("! 硬件自检 %s 失败：%s" % (n, exc))
                self.sig_diag_status.emit("! 自检 %s 失败：%s" % (n, exc))
            finally:
                for k in reversed(list(held_keys)):
                    try:
                        if str(k).isdigit():
                            io.pluck_key_up(k)
                        else:
                            io.tap_key_up(k)
                    except Exception:
                        pass
                held_keys.clear()
                for m_key in reversed(list(held_mods)):
                    try:
                        io.tap_key_up(m_key)
                    except Exception:
                        pass
                held_mods.clear()
                try:
                    for btn in ("left", "right"):
                        io.click_up(btn)
                except Exception:
                    pass
                self._diag_busy = False

        threading.Thread(target=job, daemon=True).start()

    def stop_play(self):
        if self.player and self.player.is_alive():
            self.player.stop()
        self.player = None
        self.btn_play.setChecked(False)
        self.btn_play.setText("开始演奏 (F8)")
        self.bar.setValue(0)
        if hasattr(self, "lbl_timecode"):
            self.lbl_timecode.setText("00:00 / %s" % _fmt_mmss(getattr(self, "_total_duration", 0.0)))
        if hasattr(self, "btn_stop_play"):
            self.btn_stop_play.setEnabled(False)
        self._append_log("× 已停止演奏")

    def toggle_play(self):
        if self.player and self.player.is_alive():
            if self.player.paused:
                self._stop_midi_live_if_running()
                if self.player.resume():
                    self._append_log("▶ 已继续演奏")
                    self.btn_play.setText("暂停 (F8)")
                return
            self.player.pause()
            self._append_log("|| 已暂停（F12 紧急停止）")
            self.btn_play.setText("继续 (F8)")
            return
        tl = self._active_timeline()
        if not tl:
            self.btn_play.setChecked(False)
            QMessageBox.information(self, "还没准备好", "先选一份 MIDI，等它编译完。")
            return
        if not self.io.has_window():
            self.btn_play.setChecked(False)
            QMessageBox.warning(self, "没找到游戏", "先把《异环》打开，进到乐器演奏界面。")
            return
        self._stop_midi_live_if_running()
        self.bar.setValue(0)
        self.btn_play.setText("暂停 (F8)")
        self.btn_play.setChecked(True)
        if hasattr(self, "btn_stop_play"):
            self.btn_stop_play.setEnabled(True)

        rec = None
        if self.chk_rec.isChecked():
            try:
                tag = (MC.ROLE_CN.get(self.role, "")
                       if self.instrument_mode == INSTRUMENT_GUITAR_WHEEL
                       else INSTRUMENT_SHORT_CN.get(self.instrument_mode, self.instrument_mode))
                path = REC.make_path(os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "录音"),
                    os.path.basename(self.path or ""), tag)
                rec = REC.LoopbackRecorder(path, log=lambda m: self.sig_log.emit(m),
                                           device_name=self.cmb_rec.currentText() or None)
                self._rec_path = path
            except Exception as exc:
                self._append_log("! 录音初始化失败：%s（演奏照常）" % exc)
                rec = None
        else:
            self._rec_path = None

        self.player = PlayWorker(
            tl, float(self.spin_speed.value()), bool(self.chk_sync.isChecked()),
            lambda m: self.sig_log.emit(m),
            lambda a, b: self.sig_progress.emit(a, b),
            lambda st: self.sig_finished.emit(st), rec=rec)
        self.player.start()

    def _on_progress(self, a, b):
        if b > 0:
            self.bar.setValue(int(max(0.0, min(1.0, a / b)) * 100))
            if hasattr(self, "lbl_timecode"):
                self.lbl_timecode.setText("%s / %s" % (_fmt_mmss(a), _fmt_mmss(b)))

    def _on_finished(self, stats):
        self.btn_play.setText("开始演奏 (F8)")
        self.btn_play.setChecked(False)
        if hasattr(self, "btn_stop_play"):
            self.btn_stop_play.setEnabled(False)
        if hasattr(self, "lbl_timecode"):
            self.lbl_timecode.setText("00:00 / %s" % _fmt_mmss(getattr(self, "_total_duration", 0.0)))
        if stats:
            self._append_log("本次：%d 条动作，迟到 %d 条（最多 %.0f 毫秒）"
                             % (stats.get("events", 0), stats.get("late", 0),
                                (stats.get("max_late") or 0) * 1000))

    def _append_log(self, msg):
        self.log.appendPlainText(time.strftime("%H:%M:%S ") + str(msg))

    def _hotkeys(self):
        if getattr(self, "_hotkey_timer", None) is None:
            self._hotkey_timer = QTimer(self)
            self._hotkey_timer.timeout.connect(self._poll_hotkeys)
        if not self._hotkey_timer.isActive():
            self._hotkey_timer.start(30)

    def _poll_hotkeys(self):
        for name, vk in (("F8", ie.VK_F8), ("F12", ie.VK_F12)):
            down = ie.is_key_down(vk)
            if down and not self._hot_last.get(name):
                if name == "F8":
                    self.toggle_play()
                else:
                    if self.player and self.player.is_alive():
                        self.stop_play()
                        self._append_log("F12 紧急停止")
                    dlg = self._existing_midi_live_dialog()
                    if dlg is not None and hasattr(dlg, "engine"):
                        try:
                            if dlg.engine.is_running():
                                dlg.engine.release_all()
                                if hasattr(dlg, "keyboard_strip"):
                                    dlg.keyboard_strip.clear_active_notes()
                                self._append_log("F12 紧急全松键（实时 MIDI 直弹）")
                        except RuntimeError:
                            pass
                        except Exception:
                            pass
            self._hot_last[name] = down

    def closeEvent(self, ev):
        self._closing = True
        for t_attr in ("_hotkey_timer", "_diag_timer"):
            timer = getattr(self, t_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        studio = getattr(self, "_studio_inline", None)
        if studio is not None:
            try:
                if hasattr(studio, "stop_preview"):
                    studio.stop_preview()
                studio.close()
            except Exception:
                pass
        roll_dlg = getattr(self, "_active_roll_dlg", None)
        if roll_dlg is not None:
            self._active_roll_dlg = None
            try:
                roll_dlg.close()
            except Exception:
                pass
        self._stop_midi_live_if_running()
        dlg = self._existing_midi_live_dialog()
        if dlg is not None:
            try:
                dlg.close()
            except Exception:
                pass
        if self.player and self.player.is_alive():
            self.player.stop()
            time.sleep(0.3)
        ev.accept()


def main():
    app = QApplication(sys.argv)
    try:
        from gui_theme import apply_theme
        apply_theme(app)
    except Exception:
        pass
    w = MainWindow()
    if "--smoke" in sys.argv:
        w.show()
        QTimer.singleShot(1000, app.quit)
        rc = app.exec_()
        print("GUI_SMOKE_OK title=%s" % w.windowTitle())
        return 0 if rc == 0 else rc
    w.show()
    return app.exec_()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())