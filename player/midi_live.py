# -*- coding: utf-8 -*-
"""midi_live.py -- 《异键演奏》v1.4 三模式实时 MIDI 键盘映射与直弹引擎。

特点：
- 零外部新依赖，纯标准库 ctypes 封装 Windows winmm.dll（严格 64 位句柄与回调类型）；
- 支持三乐器模式实时直弹：
  1. guitar_36 (52..87)：支持长按延音、CC64 踏板与同键 repluck_gap 保护；
  2. piano_36 (60..95)：无延音，微聚合窗内利用列不影响规则最多拆 2 批，LShift/LCtrl 严格互斥；
  3. guitar_wheel (40..88)：支持智能品格状态机直弹、单键 24 和弦触发与多音和弦窗实时匹配；
- 前台焦点守卫：切出游戏窗口或按下 Alt/Win 瞬间自动释放全部按键（先普通键/鼠标，最后 LShift/LCtrl），
  非前台时仍通过 on_event(..., injected=False) 驱动界面键盘实时预览动画。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import queue
import re
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import guitar_plan as GP  # noqa: E402
import keyboard_layout as KL  # noqa: E402
import midi_adapter as MA  # noqa: E402
import midi_compiler as MC  # noqa: E402
import modes  # noqa: E402
import player_config as PC  # noqa: E402
import timeline_runner as TR  # noqa: E402

try:
    from core_utils import get_logger
except Exception:
    get_logger = None


def _log_debug(msg, *args):
    if get_logger is not None:
        try:
            get_logger("midi_live").debug(msg, *args)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
#  1. 公开默认配置与 WinMM 64 位 C 接口定义
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_LIVE_CONFIG = {
    "device_name": "",
    "auto_connect": True,        # 打开即连 & 后台热插拔自动发现/重连
    "keyboard_size": 61,         # 可视化键盘规格: 25 | 37 | 49 | 61 | 76 | 88
    "channel": 0,                # 0 = 全部通道；1..16 = 指定通道
    "ignore_drums": True,        # 忽略 Ch10 (channel index 9)
    "transpose": 0,              # 半音移调 (-24..+24)
    "out_of_range": "fold",      # "fold" = 八度折叠；"ignore" = 严格忽略
    "sustain_pedal": True,       # CC64 延音踏板
    "chord_window_ms": 8.0,      # 同批微聚合窗（毫秒）
    "mod_lead_ms": 2.0,          # 钢琴修饰键前后保护提前量（1.0..20.0 ms）
    "focus_guard": True,         # 仅游戏前台发键
    "wheel_submode": "fret",     # "fret" | "chord"
    "chord_strum_mode": False,   # 和弦轮盘子模式下是否右键扫弦
    "single_key_chord": False,   # MIDI 48..71 单键直触 24 和弦
    "custom_key_map": {},        # {pitch: key_str} 可选单音键位覆写
}

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

HMIDIIN = wintypes.HANDLE
DWORD_PTR = ctypes.c_size_t
CALLBACK_FUNCTION = 0x00030000
MIM_OPEN = 0x3C1
MIM_CLOSE = 0x3C2
MIM_DATA = 0x3C3
MIDIINPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_size_t,
    ctypes.c_size_t,
    ctypes.c_size_t,
)

_MOD_KEYS = frozenset({"LShift", "LCtrl", "LSHIFT", "LCTRL", "Shift", "Ctrl", "SHIFT", "CTRL"})

_PERSONAL_OWNER_RE = re.compile(
    r"([\w\u4e00-\u9fff.\-]+?)\s*(?:'s|’s|的)\s*"
    r"(iPhone|iPad|MacBook|iMac|Mac|Watch|Galaxy|Pixel|Phone|Laptop|Desktop|PC|手机|电脑|平板|蓝牙)",
    re.IGNORECASE,
)
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_SERIAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]{2,4}-){2,}[A-Za-z0-9]{2,}(?![A-Za-z0-9])|\b\d{6,}\b")


def sanitize_device_name(name, max_len=36, mask_personal=True):
    """清洗控制字符并脱敏个人设备标识/序列号，用于 UI 展示与日志输出（不影响底层打开匹配）。"""
    raw = str(name or "")
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    if mask_personal:
        cleaned = _PERSONAL_OWNER_RE.sub(r"*** \2", cleaned)
        cleaned = _MAC_RE.sub("**-**-**-**-**-**", cleaned)

        def _mask_sn(m):
            tok = m.group(0)
            if len(tok) <= 4:
                return "****"
            return tok[:2] + "****" + tok[-2:]

        cleaned = _SERIAL_TOKEN_RE.sub(_mask_sn, cleaned)
    if max_len and int(max_len) > 3 and len(cleaned) > int(max_len):
        cleaned = cleaned[: int(max_len) - 1].rstrip() + "…"
    return cleaned


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwSupport", wintypes.DWORD),
    ]


_WINMM = None
_WINMM_INIT_DONE = False


def _get_winmm():
    global _WINMM, _WINMM_INIT_DONE
    if _WINMM_INIT_DONE:
        return _WINMM
    _WINMM_INIT_DONE = True
    try:
        wm = ctypes.WinDLL("winmm")
        wm.midiInGetNumDevs.argtypes = []
        wm.midiInGetNumDevs.restype = wintypes.UINT

        wm.midiInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MIDIINCAPSW), wintypes.UINT]
        wm.midiInGetDevCapsW.restype = wintypes.UINT

        wm.midiInOpen.argtypes = [
            ctypes.POINTER(HMIDIIN),
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_size_t,
            wintypes.DWORD,
        ]
        wm.midiInOpen.restype = wintypes.UINT

        wm.midiInStart.argtypes = [HMIDIIN]
        wm.midiInStart.restype = wintypes.UINT

        wm.midiInStop.argtypes = [HMIDIIN]
        wm.midiInStop.restype = wintypes.UINT

        wm.midiInReset.argtypes = [HMIDIIN]
        wm.midiInReset.restype = wintypes.UINT

        wm.midiInClose.argtypes = [HMIDIIN]
        wm.midiInClose.restype = wintypes.UINT
        _WINMM = wm
    except Exception as exc:
        _log_debug("WinMM initialization failed: %s", exc)
        _WINMM = None
    return _WINMM


def list_midi_inputs():
    """枚举当前 Windows 可用 MIDI 输入设备列表：[{'id': int, 'name': str}, ...]。"""
    wm = _get_winmm()
    if wm is None:
        return []
    out = []
    try:
        cnt = int(wm.midiInGetNumDevs())
    except Exception as exc:
        _log_debug("midiInGetNumDevs failed: %s", exc)
        return []
    caps = MIDIINCAPSW()
    sz = ctypes.sizeof(MIDIINCAPSW)
    for dev_id in range(cnt):
        try:
            rc = int(wm.midiInGetDevCapsW(dev_id, ctypes.byref(caps), sz))
            if rc == 0:
                raw_name = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(caps.szPname or ("MIDI Input %d" % dev_id))).strip()
                out.append({
                    "id": int(dev_id),
                    "name": raw_name,
                    "display_name": sanitize_device_name(raw_name),
                })
        except Exception as exc:
            _log_debug("midiInGetDevCapsW(%d) failed: %s", dev_id, exc)
    return out


class WinMMMidiInput(object):
    """基于 Windows winmm.dll 的低延迟回调式 MIDI 输入封装。"""

    def __init__(self, out_queue=None):
        self._queue = out_queue if out_queue is not None else queue.SimpleQueue()
        self._io_lock = threading.Lock()
        self._handle = HMIDIIN()
        self._opened = False
        self._device_id = None
        self._device_name = ""
        # 必须强持有回调引用，防止 GC 回收导致 C 回调崩溃
        self._cb_ref = MIDIINPROC(self._on_midi_msg)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def queue(self):
        return self._queue

    @property
    def is_open(self):
        return bool(self._opened and self._handle.value)

    @property
    def device_name(self):
        return self._device_name

    @property
    def display_name(self):
        return sanitize_device_name(self._device_name)

    def _on_midi_msg(self, _hmidi, w_msg, _dw_inst, dw_param1, _dw_param2):
        # 回调线程绝不调用 WinMM/SendInput/Qt，仅做轻量入队
        try:
            msg = int(w_msg)
            if msg == MIM_DATA:
                p1 = int(dw_param1)
                status = p1 & 0xFF
                if status >= 0xF0:
                    return
                data1 = (p1 >> 8) & 0xFF
                data2 = (p1 >> 16) & 0xFF
                self._queue.put((time.perf_counter(), status, data1, data2))
            elif msg == MIM_CLOSE:
                cur_id = self._device_id
                dev_id = int(cur_id) if cur_id is not None else -1
                self._queue.put((time.perf_counter(), 0, dev_id, -1))
        except Exception:
            pass

    def open(self, device_id_or_name=0):
        """打开指定 ID 或名称的 MIDI 输入设备（空字符串/None 不再静默打开首台）。"""
        self.close()
        wm = _get_winmm()
        if wm is None:
            raise RuntimeError("winmm.dll 不可用，无法打开 MIDI 输入设备")

        if device_id_or_name is None or isinstance(device_id_or_name, bool):
            raise ValueError("必须指定有效的 MIDI 输入设备 ID 或名称")
        if isinstance(device_id_or_name, str) and not device_id_or_name.strip():
            raise ValueError("MIDI 输入设备名称不能为空")

        devices = list_midi_inputs()
        if not devices:
            raise RuntimeError("未检测到可用 MIDI 输入设备")

        chosen = None
        if isinstance(device_id_or_name, int):
            for d in devices:
                if d["id"] == device_id_or_name:
                    chosen = d
                    break
        elif isinstance(device_id_or_name, str):
            want = device_id_or_name.strip().lower()
            for d in devices:
                if d["name"].lower() == want:
                    chosen = d
                    break
            if chosen is None:
                for d in devices:
                    if want in d["name"].lower():
                        chosen = d
                        break

        if chosen is None:
            raise ValueError("找不到指定 MIDI 输入设备：%r" % (sanitize_device_name(device_id_or_name),))

        handle = HMIDIIN()
        cb_ptr = ctypes.cast(self._cb_ref, ctypes.c_void_p).value or 0
        rc = int(wm.midiInOpen(
            ctypes.byref(handle),
            wintypes.UINT(chosen["id"]),
            ctypes.c_size_t(cb_ptr),
            ctypes.c_size_t(0),
            wintypes.DWORD(CALLBACK_FUNCTION),
        ))
        if rc == 4:
            raise RuntimeError(
                "MIDI 设备正被其他音乐软件/浏览器占用，请先关闭占用程序后重试（错误码 4）"
            )
        if rc != 0 or not handle.value:
            raise RuntimeError("midiInOpen 失败（错误码 %d）：%s" % (rc, sanitize_device_name(chosen["name"])))

        rc_start = int(wm.midiInStart(handle))
        if rc_start != 0:
            try:
                wm.midiInReset(handle)
            except Exception:
                pass
            try:
                wm.midiInClose(handle)
            except Exception:
                pass
            raise RuntimeError("midiInStart 失败（错误码 %d）" % rc_start)

        with self._io_lock:
            self._handle = handle
            self._opened = True
            self._device_id = chosen["id"]
            self._device_name = chosen["name"]
        return chosen

    def close(self):
        with self._io_lock:
            if not self._opened:
                return
            wm = _get_winmm()
            h = self._handle
            self._opened = False
            self._handle = HMIDIIN()
            self._device_id = None
            self._device_name = ""
        if wm is not None and h and h.value:
            try:
                wm.midiInStop(h)
            except Exception:
                pass
            try:
                wm.midiInReset(h)
            except Exception:
                pass
            try:
                wm.midiInClose(h)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════
#  2. 辅助映射与公开 describe_mode_mapping API
# ══════════════════════════════════════════════════════════════════════════
def pitch_name(pitch):
    p = int(pitch)
    return "%s%d" % (NOTE_NAMES[p % 12], (p // 12) - 1)


def _best_fret_candidate_for_pitch(pitch):
    cands = MC.fret_candidates_for_pitch(int(pitch))
    if not cands:
        return None
    # 0 品优先，否则品格最小优先、高弦序便于直观看弦位
    return min(cands, key=lambda fs: (fs[0], -fs[1]))


def describe_mode_mapping(instrument_mode, wheel_submode="fret", transpose=0, single_key_chord=False):
    """描述指定模式的有效音域、说明文案与每个 MIDI 音高的键帽/指法标签。"""
    mode = str(instrument_mode or KL.INSTRUMENT_GUITAR_WHEEL)
    sub = str(wheel_submode or "fret").lower()
    tr = int(transpose or 0)
    tr_hint = ("（移调 %+d）" % tr) if tr else ""

    if mode == KL.INSTRUMENT_GUITAR_36:
        lo, hi = KL.INSTRUMENT_RANGES[KL.INSTRUMENT_GUITAR_36]
        labels = {p: KL.key_label_for_pitch(p, KL.INSTRUMENT_GUITAR_36) for p in range(lo, hi + 1)}
        return {
            "range": (lo, hi),
            "title": "吉他 · 钢琴键（36 键）%s" % tr_hint,
            "hint": "音域 E3~D#6（52~87），36 键独立直按，支持长按延音与 CC64 延音踏板。",
            "key_labels": labels,
        }

    if mode == KL.INSTRUMENT_PIANO_36:
        lo, hi = KL.INSTRUMENT_RANGES[KL.INSTRUMENT_PIANO_36]
        labels = {p: KL.key_label_for_pitch(p, KL.INSTRUMENT_PIANO_36) for p in range(lo, hi + 1)}
        return {
            "range": (lo, hi),
            "title": "钢琴 · 36 键%s" % tr_hint,
            "hint": "音域 C4~B6（60~95），无延音，同批和弦自动拆批保证 LShift(#) 与 LCtrl(b) 严格互斥。",
            "key_labels": labels,
        }

    # guitar_wheel
    if sub == "chord" and single_key_chord:
        chords = list(GP.ALL_CHORDS)
        lo = 48
        hi = lo + len(chords) - 1  # 48..71
        labels = {lo + i: name for i, name in enumerate(chords)}
        return {
            "range": (lo, hi),
            "title": "吉他 · 轮盘（单键 24 和弦）%s" % tr_hint,
            "hint": "MIDI 48~71（C3~B4）直接触发 4 页×6 扇区 24 个游戏和弦。",
            "key_labels": labels,
        }

    lo, hi = KL.INSTRUMENT_RANGES[KL.INSTRUMENT_GUITAR_WHEEL]
    if sub == "chord":
        labels = {p: pitch_name(p) for p in range(lo, hi + 1)}
        return {
            "range": (lo, hi),
            "title": "吉他 · 轮盘（多音和弦匹配）%s" % tr_hint,
            "hint": "音域 E2~E6（40~88），微聚合窗自动匹配最优游戏和弦并拨弦/扫弦。",
            "key_labels": labels,
        }

    labels = {}
    for p in range(lo, hi + 1):
        fs = _best_fret_candidate_for_pitch(p)
        if fs is not None:
            f, s = fs
            labels[p] = "%dp·%d" % (f, s)
    return {
        "range": (lo, hi),
        "title": "吉他 · 轮盘（智能品格直弹）%s" % tr_hint,
        "hint": "音域 E2~E6（40~88），自动就近选品/最短 T·R 翻页/第5页 0 品免点扇区，支持 CC64 延音。",
        "key_labels": labels,
    }


def check_game_bridge_status(io=None):
    """只读检测游戏窗口、前台状态及 UIPI 管理员权限隔离情况。"""
    is_admin = False
    try:
        if io is not None and hasattr(io, "is_admin"):
            is_admin = bool(io.is_admin())
        else:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin() != 0)
    except Exception:
        is_admin = False

    hwnd = None
    try:
        if io is not None and hasattr(io, "find_window"):
            hwnd = io.find_window()
        else:
            import input_engine as _ie
            hwnd = _ie.find_game_window()
    except Exception:
        hwnd = None

    found = bool(hwnd)
    foreground = False
    if found:
        try:
            if io is not None and hasattr(io, "foreground_window"):
                fg = io.foreground_window()
            else:
                fg = ctypes.windll.user32.GetForegroundWindow()
            foreground = bool(fg and fg == hwnd)
        except Exception:
            foreground = False

    game_elevated = None
    uipi_blocked = False
    if found and isinstance(hwnd, int) and hwnd > 0:
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if pid.value:
                hproc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                if hproc:
                    try:
                        htok = wintypes.HANDLE()
                        if advapi32.OpenProcessToken(hproc, 0x0008, ctypes.byref(htok)):  # TOKEN_QUERY
                            try:
                                elev = wintypes.DWORD(0)
                                ret_len = wintypes.DWORD(0)
                                ok = advapi32.GetTokenInformation(
                                    htok,
                                    20,  # TokenElevation
                                    ctypes.byref(elev),
                                    ctypes.sizeof(elev),
                                    ctypes.byref(ret_len),
                                )
                                if ok:
                                    game_elevated = bool(elev.value != 0)
                            finally:
                                kernel32.CloseHandle(htok)
                    finally:
                        kernel32.CloseHandle(hproc)
        except Exception as exc:
            _log_debug("check_game_bridge_status token check failed: %s", exc)
            game_elevated = None

    if found and (game_elevated is True) and (not is_admin):
        uipi_blocked = True

    if uipi_blocked:
        reason = "游戏以管理员权限运行，请以管理员身份重启本程序后才能注入按键"
    elif found and foreground:
        reason = "游戏在前台，可正常直弹发声"
    elif found:
        reason = "已检测到游戏窗口，切回游戏前台即可发声"
    else:
        reason = "未检测到游戏窗口（当前仅界面预览）"

    return {
        "found": found,
        "foreground": foreground,
        "is_admin": is_admin,
        "game_elevated": game_elevated,
        "uipi_blocked": uipi_blocked,
        "reason": reason,
    }


# ══════════════════════════════════════════════════════════════════════════
#  3. 实时 MIDI 映射与直弹引擎
# ══════════════════════════════════════════════════════════════════════════
class LiveMidiEngine(object):
    """三模式低延迟实时 MIDI 键盘直弹引擎。"""

    def __init__(self, io=None, config=None, on_event=None, on_status=None, on_before_start=None):
        if io is None:
            import game_io
            io = game_io.GameIO()
        self.io = io
        self._on_event = on_event
        self._on_status = on_status
        self._on_before_start = on_before_start

        self.config = dict(DEFAULT_LIVE_CONFIG)
        try:
            saved = (PC.load().get("midi_live") or {})
            if isinstance(saved, dict):
                self.config.update(saved)
        except Exception:
            pass
        if isinstance(config, dict):
            self.config.update(config)

        self._instrument_mode = KL.INSTRUMENT_GUITAR_WHEEL
        self._timing = MC.Timing()
        self._in_queue = queue.SimpleQueue()
        self._midi_inputs = []       # list[WinMMMidiInput]，多端口共享 self._in_queue
        self._midi_in = WinMMMidiInput(self._in_queue)  # 指向首台已开设备或空闲实例以兼容外部读取
        self._hotplug_mode = None    # None | "all" | "single"
        self._target_device_name = ""
        self._last_hotplug_check_t = 0.0

        self._running = False
        self._stop_ev = threading.Event()
        self._thread = None
        self._state_lock = threading.RLock()

        # 当前真实/记录的按住键集合（确保安全释放与 LShift/LCtrl 互斥）
        self._held_keys = set()
        self._held_mouse = set()
        self._was_focused = True

        # 延音踏板 & 通道活跃音状态
        self._pedal_down = False
        self._last_window_miss_t = 0.0

        # guitar_36: key -> {pitch, down_t, min_up_t, max_up_t, pedal_held, holders}
        self._g36_active = {}
        self._g36_note_map = {}      # (ch, raw_pitch) -> key
        self._g36_last_up = {}

        # piano_36: 异步非阻塞释放状态机
        self._p36_active = {}        # key -> {"up_t": float, "down_t": float, "col": int, "mod": str | None}
        self._p36_active_mod = None
        self._p36_mod_up_t = 0.0
        self._p36_last_up = {}
        self._p36_last_mod_up = 0.0
        self._p36_last_mod_up_name = None

        # guitar_wheel 状态机（page: 0..4, sector: 0..5, fret: 0..24）
        self._wheel_state = {
            "page": 0,
            "sector": 0,
            "fret": 1,
            "chord": None,
            "submode": str(self.config.get("wheel_submode") or "fret"),
        }
        # 轮盘当前正在按住的弦：string(1..6) -> {pitch, key, down_t, min_up_t, max_up_t, pedal_held, holders}
        self._wheel_active_strings = {}
        self._wheel_note_map = {}        # (ch, raw_pitch) -> string(int)
        self._wheel_last_up = {}
        self._wheel_chord_held = []      # [{key, pitch, raw_pitch, min_up_t, max_up_t, pedal_held, holders}]
        self._wheel_chord_note_map = {}  # (ch, raw_pitch) -> list[dict]

        self._stats = {
            "received": 0,
            "played": 0,
            "folded": 0,
            "dropped": 0,
            "blocked": 0,
        }

    # ── 配置与状态接口 ───────────────────────────────────────────────────
    def is_running(self):
        return bool(self._running)

    def connected_device_names(self):
        with self._state_lock:
            return [
                sanitize_device_name(inp.device_name)
                for inp in self._midi_inputs
                if inp.is_open and inp.device_name
            ]

    def is_hardware_connected(self):
        with self._state_lock:
            return bool(self._midi_inputs)

    def connected_device_name(self):
        return " + ".join(self.connected_device_names())

    def check_game_bridge_status(self):
        return check_game_bridge_status(self.io)

    def update_config(self, partial):
        if not isinstance(partial, dict):
            return dict(self.config)
        new_timing = MC.Timing()
        with self._state_lock:
            old_sub = self._wheel_state.get("submode")
            self.config.update(partial)
            if "wheel_submode" in partial:
                new_sub = str(partial["wheel_submode"] or "fret")
                self._wheel_state["submode"] = new_sub
                if new_sub != old_sub and self._instrument_mode == KL.INSTRUMENT_GUITAR_WHEEL:
                    self.release_all()
            self._timing = new_timing
            if not self.config.get("sustain_pedal", True):
                self._pedal_down = False
                self._release_pedal_held(time.perf_counter())
            return dict(self.config)

    def set_instrument_mode(self, mode):
        if mode not in KL.INSTRUMENT_MODES:
            raise ValueError("未知乐器模式：%r" % (mode,))
        changed = False
        with self._state_lock:
            if mode != self._instrument_mode:
                self.release_all()
                self._pedal_down = False
                self._instrument_mode = mode
                changed = True
        if changed:
            self._emit_status("mode", "已切换直弹模式：%s" % KL.INSTRUMENT_CN.get(mode, mode))
        return self._instrument_mode

    def reset_wheel_state(self, page=0, sector=0, fret=1):
        with self._state_lock:
            self._wheel_state["page"] = int(page)
            self._wheel_state["sector"] = int(sector)
            self._wheel_state["fret"] = int(fret)
            self._wheel_state["chord"] = None
        self._emit_status("wheel", "轮盘状态已重置：第 %d 页 · 第 %d 格 · %d 品" % (int(page) + 1, int(sector) + 1, int(fret)))

    def get_wheel_state(self):
        with self._state_lock:
            return dict(self._wheel_state)

    def get_stats(self):
        with self._state_lock:
            out = dict(self._stats)
            names = self.connected_device_names()
            out["active_notes"] = (
                len(self._g36_active)
                + len(self._p36_active)
                + len(self._wheel_active_strings)
                + len(self._wheel_chord_held)
            )
            out["pedal"] = bool(self._pedal_down)
            out["devices"] = names
            out["device"] = " + ".join(names)
            out["hardware_connected"] = bool(self._midi_inputs)
            out["mode"] = self._instrument_mode
            return out

    def _emit_status(self, kind, text):
        cb = self._on_status
        if cb is None:
            return
        try:
            with self._state_lock:
                names = self.connected_device_names()
                running = bool(self._running)
                hw = bool(self._midi_inputs)
                wheel = dict(self._wheel_state)
                stats = self.get_stats()
            cb({
                "kind": str(kind),
                "text": str(text),
                "running": running,
                "hardware_connected": hw,
                "devices": names,
                "wheel": wheel,
                "stats": stats,
            })
        except Exception:
            pass

    def _emit_event(self, payload):
        cb = self._on_event
        if cb is None:
            return
        try:
            cb(dict(payload))
        except Exception:
            pass

    # ── 启停与消息注入 ───────────────────────────────────────────────────
    def _close_all_inputs(self):
        with self._state_lock:
            inputs = list(self._midi_inputs)
            self._midi_inputs = []
            idle = WinMMMidiInput(self._in_queue)
            self._midi_in = idle
        for inp in inputs:
            try:
                inp.close()
            except Exception:
                pass

    def _poll_hotplug(self, force=False, closed_dev_id=None):
        if not self._running or self._stop_ev.is_set() or self._hotplug_mode not in ("all", "single"):
            return
        now = time.perf_counter()
        if not force and closed_dev_id is None and (now - self._last_hotplug_check_t) < 1.5:
            return
        self._last_hotplug_check_t = now

        try:
            devs = list_midi_inputs()
        except Exception as exc:
            _log_debug("list_midi_inputs failed during hotplug poll: %s", exc)
            return

        sys_by_id = {d["id"]: d["name"] for d in devs}
        disconnected_any = False
        kept = []
        to_close = []

        with self._state_lock:
            for inp in self._midi_inputs:
                still_ok = (
                    inp.is_open
                    and inp._device_id in sys_by_id
                    and sys_by_id[inp._device_id] == inp.device_name
                )
                if closed_dev_id is not None and inp._device_id == closed_dev_id:
                    still_ok = False
                if still_ok:
                    kept.append(inp)
                else:
                    disconnected_any = True
                    to_close.append(inp)
            if disconnected_any:
                self._midi_inputs = kept
                self._midi_in = kept[0] if kept else WinMMMidiInput(self._in_queue)

        if disconnected_any:
            for inp in to_close:
                try:
                    inp.close()
                except Exception:
                    pass
            self.release_all()
            self._emit_status("disconnected", "MIDI 设备已断开，等待重新插入自动重连…")

        if not self.config.get("auto_connect", True) and not force:
            return

        if self._hotplug_mode == "all":
            with self._state_lock:
                if not self._running or self._stop_ev.is_set():
                    return
                opened_ids = {inp._device_id for inp in self._midi_inputs if inp.is_open}
            for dev in devs:
                if not self._running or self._stop_ev.is_set():
                    return
                if dev["id"] in opened_ids:
                    continue
                inp = WinMMMidiInput(self._in_queue)
                try:
                    chosen = inp.open(dev["id"])
                except Exception as exc:
                    _log_debug("hotplug open(%r) skipped: %s", sanitize_device_name(dev.get("name")), exc)
                    continue
                aborted = False
                with self._state_lock:
                    if not self._running or self._stop_ev.is_set():
                        aborted = True
                    else:
                        self._midi_inputs.append(inp)
                        if self._midi_inputs:
                            self._midi_in = self._midi_inputs[0]
                if aborted:
                    try:
                        inp.close()
                    except Exception:
                        pass
                    return
                self._emit_status("device_connected", "已自动连接 MIDI 设备：%s" % sanitize_device_name(chosen["name"]))
        elif self._hotplug_mode == "single" and not self.is_hardware_connected() and self._target_device_name:
            want = self._target_device_name.strip().lower()
            matched = None
            for dev in devs:
                if dev["name"].lower() == want:
                    matched = dev
                    break
            if matched is None:
                for dev in devs:
                    if want in dev["name"].lower():
                        matched = dev
                        break
            if matched is not None and self._running and not self._stop_ev.is_set():
                inp = WinMMMidiInput(self._in_queue)
                try:
                    chosen = inp.open(matched["id"])
                except Exception as exc:
                    _log_debug("hotplug reconnect(%r) failed: %s", sanitize_device_name(matched.get("name")), exc)
                    return
                aborted = False
                with self._state_lock:
                    if not self._running or self._stop_ev.is_set():
                        aborted = True
                    else:
                        self._midi_inputs.append(inp)
                        self._midi_in = inp
                if aborted:
                    try:
                        inp.close()
                    except Exception:
                        pass
                    return
                self._emit_status("device_connected", "已自动重连 MIDI 设备：%s" % sanitize_device_name(chosen["name"]))

    def start(self, device_id_or_name=None, instrument_mode="guitar_wheel"):
        """启动实时 MIDI 直弹引擎。
        - None: 纯虚拟试弹模式（绝不读取旧 device_name）
        - 'all'/'auto'/'*': 即插即用全端口模式（0 台设备也静默等待插入）
        - int ID / 具体设备名: 打开指定设备
        """
        self.stop()
        if instrument_mode in KL.INSTRUMENT_MODES:
            self._instrument_mode = instrument_mode
        if self._on_before_start is not None:
            try:
                self._on_before_start()
            except Exception as exc:
                _log_debug("on_before_start failed: %s", exc)

        self._timing = MC.Timing()
        opened_inputs = []
        hp_mode = None
        target_name = ""

        if device_id_or_name is None:
            pass
        elif isinstance(device_id_or_name, str) and device_id_or_name.strip().lower() in ("all", "auto", "*"):
            hp_mode = "all"
            for dev in list_midi_inputs():
                inp = WinMMMidiInput(self._in_queue)
                try:
                    inp.open(dev["id"])
                    opened_inputs.append(inp)
                except Exception as exc:
                    _log_debug("all-mode open(%r) skipped: %s", sanitize_device_name(dev.get("name")), exc)
        elif (isinstance(device_id_or_name, int) and not isinstance(device_id_or_name, bool) and device_id_or_name >= 0) or (
            isinstance(device_id_or_name, str) and device_id_or_name.strip()
        ):
            hp_mode = "single"
            inp = WinMMMidiInput(self._in_queue)
            try:
                chosen = inp.open(
                    device_id_or_name if isinstance(device_id_or_name, int) else device_id_or_name.strip()
                )
            except Exception:
                raise
            target_name = chosen["name"]
            opened_inputs = [inp]

        # 清空残留队列
        while True:
            try:
                self._in_queue.get_nowait()
            except queue.Empty:
                break

        with self._state_lock:
            self._stats = {"received": 0, "played": 0, "folded": 0, "dropped": 0, "blocked": 0}
            self._pedal_down = False
            self._last_window_miss_t = 0.0
            self._hotplug_mode = hp_mode
            self._target_device_name = target_name
            self._last_hotplug_check_t = time.perf_counter()
            self._midi_inputs = opened_inputs
            if opened_inputs:
                self._midi_in = opened_inputs[0]
            self._stop_ev.clear()
            self._running = True
            self._thread = threading.Thread(target=self._consume_loop, name="MidiLiveEngine", daemon=True)
            self._thread.start()

        names = self.connected_device_names()
        if names:
            self._emit_status("started", "实时 MIDI 已连接：%s" % " + ".join(names))
        elif self._hotplug_mode == "all":
            self._emit_status("waiting_device", "等待插入 MIDI 键盘（即插即用）")
        else:
            self._emit_status("started", "实时直弹引擎已就绪（虚拟琴键/消息注入模式）")
        return True

    def stop(self):
        with self._state_lock:
            self._stop_ev.set()
            self._running = False
            self._hotplug_mode = None
            self._target_device_name = ""
            th = self._thread
            self._thread = None
        self._close_all_inputs()
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=1.0)
        self.release_all()
        self._emit_status("stopped", "实时 MIDI 已停止")

    def inject_message(self, status, data1, data2):
        """注入原始 MIDI 短消息（status, data1, data2）。"""
        item = (time.perf_counter(), int(status) & 0xFF, int(data1) & 0xFF, int(data2) & 0xFF)
        if self._running:
            self._in_queue.put(item)
        else:
            self._process_single_raw(*item)

    def inject_note_on(self, pitch, vel=100, channel=0):
        status = 0x90 | (int(channel) & 0x0F)
        self.inject_message(status, int(pitch) & 0x7F, max(1, min(127, int(vel))))

    def inject_note_off(self, pitch, channel=0):
        status = 0x80 | (int(channel) & 0x0F)
        self.inject_message(status, int(pitch) & 0x7F, 0)

    def inject_cc(self, cc, val, channel=0):
        status = 0xB0 | (int(channel) & 0x0F)
        self.inject_message(status, int(cc) & 0x7F, int(val) & 0x7F)

    # ── 底层硬件发送与焦点守卫 ───────────────────────────────────────────
    def _alt_or_win_down(self):
        fn = getattr(self.io, "is_key_down", None)
        if fn is None:
            return False
        for vk in (0x12, 0x5B, 0x5C):
            try:
                if fn(vk):
                    return True
            except Exception:
                pass
        return False

    def _can_inject(self):
        if not self.config.get("focus_guard", True):
            return True
        if self._alt_or_win_down():
            if self._was_focused:
                self._was_focused = False
                self.release_all()
                self._emit_status("focus_lost", "检测到 Alt/Win，已自动松开全部按键")
            return False
        has_find = hasattr(self.io, "find_window") and hasattr(self.io, "foreground_window")
        if has_find:
            now = time.perf_counter()
            if self._last_window_miss_t > 0.0 and (now - self._last_window_miss_t) < 0.2:
                ok = False
            else:
                try:
                    hwnd = self.io.find_window()
                    if not hwnd:
                        self._last_window_miss_t = now
                        ok = False
                    else:
                        self._last_window_miss_t = 0.0
                        fg = self.io.foreground_window()
                        ok = bool(fg == hwnd)
                except Exception:
                    ok = False
        else:
            ok = True
        if not ok:
            if self._was_focused:
                self._was_focused = False
                self.release_all()
                self._emit_status("focus_lost", "游戏不在前台，已安全释放按键（仅预览）")
            return False
        self._was_focused = True
        return True

    def _press_key(self, key):
        k = str(key)
        if k.isdigit():
            self.io.pluck_key_down(k)
        else:
            self.io.tap_key_down(k)
        self._held_keys.add(k)

    def _release_key(self, key):
        k = str(key)
        if k not in self._held_keys:
            return
        self._held_keys.discard(k)
        try:
            if k.isdigit():
                self.io.pluck_key_up(k)
            else:
                self.io.tap_key_up(k)
        except Exception:
            pass

    def _press_mouse(self, btn):
        self.io.click_down(btn)
        self._held_mouse.add(btn)

    def _release_mouse(self, btn):
        if btn not in self._held_mouse:
            return
        self._held_mouse.discard(btn)
        try:
            self.io.click_up(btn)
        except Exception:
            pass

    def release_all(self):
        """安全释放所有按住的通道：先放普通键与鼠标，最后放 LShift/LCtrl，并复位延音踏板。"""
        with self._state_lock:
            held_k = list(self._held_keys)
            held_m = list(self._held_mouse)
            for k in held_k:
                if k not in _MOD_KEYS:
                    self._release_key(k)
            for b in held_m:
                self._release_mouse(b)
            for k in held_k:
                if k in _MOD_KEYS:
                    self._release_key(k)
            self._pedal_down = False
            self._g36_active.clear()
            self._g36_note_map.clear()
            self._p36_active.clear()
            self._p36_active_mod = None
            self._p36_mod_up_t = 0.0
            self._wheel_active_strings.clear()
            self._wheel_note_map.clear()
            self._wheel_chord_held.clear()
            self._wheel_chord_note_map.clear()

    def _next_expiry_timeout(self, now):
        earliest = None
        with self._state_lock:
            for st in self._g36_active.values():
                if st.get("pending_off"):
                    t = st["min_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
                if st.get("max_up_t", 0.0) > 0:
                    t = st["max_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
            for st in self._p36_active.values():
                t = st["up_t"]
                if earliest is None or t < earliest:
                    earliest = t
            if self._p36_active_mod is not None and self._p36_mod_up_t > 0.0:
                if earliest is None or self._p36_mod_up_t < earliest:
                    earliest = self._p36_mod_up_t
            for st in self._wheel_active_strings.values():
                if st.get("pending_off"):
                    t = st["min_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
                if st.get("max_up_t", 0.0) > 0:
                    t = st["max_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
            for st in self._wheel_chord_held:
                if st.get("pending_off"):
                    t = st["min_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
                if st.get("max_up_t", 0.0) > 0:
                    t = st["max_up_t"]
                    if earliest is None or t < earliest:
                        earliest = t
        if earliest is None:
            return 0.004
        return max(0.0005, min(0.004, earliest - now))

    def _wait_unblocked(self, sec):
        """在释放 _state_lock 的情况下等待 sec 秒（若运行中收到 stop 则提前返回 False）。"""
        delay = float(sec or 0.0)
        if delay <= 0.0:
            return not self._stop_ev.is_set()
        if self._stop_ev.is_set():
            return False
        if self._running or self._thread is threading.current_thread():
            return not self._stop_ev.wait(delay)
        with TR.WinTimer(1):
            time.sleep(delay)
        return True

    # ── 消费线程与微聚合窗调度 ───────────────────────────────────────────
    def _consume_loop(self):
        with TR.WinTimer(1):
            while not self._stop_ev.is_set():
                now = time.perf_counter()
                self._flush_expired(now)

                # 后台热插拔静默巡检（每 1.5s）
                if self._hotplug_mode in ("all", "single"):
                    self._poll_hotplug(force=False)

                # 关注焦点守卫变化（切出瞬间立即释放）
                with self._state_lock:
                    has_held = bool(self._held_keys or self._held_mouse)
                if self.config.get("focus_guard", True) and has_held:
                    self._can_inject()

                wait_to = self._next_expiry_timeout(time.perf_counter())
                try:
                    item = self._in_queue.get(timeout=wait_to)
                except queue.Empty:
                    continue

                if item[3] == -1 and item[1] == 0:
                    closed_id = item[2] if int(item[2]) >= 0 else None
                    if self._hotplug_mode in ("all", "single"):
                        self._poll_hotplug(force=True, closed_dev_id=closed_id)
                    else:
                        self.release_all()
                        self._emit_status("disconnected", "MIDI 设备已断开")
                    continue

                # guitar_36 跳过 chord_window_ms 干等，只非阻塞捞取队列里已就绪消息
                msgs = [item]
                closed_sentinels = []
                win_sec = max(0.0, float(self.config.get("chord_window_ms") or 0.0)) / 1000.0
                first_kind = self._classify_raw(item[1], item[3])
                if self._instrument_mode == KL.INSTRUMENT_GUITAR_36:
                    while True:
                        try:
                            nxt = self._in_queue.get_nowait()
                        except queue.Empty:
                            break
                        if nxt[3] == -1 and nxt[1] == 0:
                            closed_sentinels.append(nxt)
                            break
                        msgs.append(nxt)
                elif win_sec > 0.0005 and first_kind == "note_on":
                    deadline = item[0] + win_sec
                    while not self._stop_ev.is_set():
                        rem = deadline - time.perf_counter()
                        if rem <= 0:
                            break
                        try:
                            nxt = self._in_queue.get(timeout=min(rem, 0.002))
                            if nxt[3] == -1 and nxt[1] == 0:
                                closed_sentinels.append(nxt)
                                break
                            msgs.append(nxt)
                        except queue.Empty:
                            pass

                self._process_batch(msgs)
                for cs in closed_sentinels:
                    closed_id = cs[2] if int(cs[2]) >= 0 else None
                    if self._hotplug_mode in ("all", "single"):
                        self._poll_hotplug(force=True, closed_dev_id=closed_id)
                    else:
                        self.release_all()
                        self._emit_status("disconnected", "MIDI 设备已断开")

    def _classify_raw(self, status, data2):
        hi = status & 0xF0
        if hi == 0x90 and data2 > 0:
            return "note_on"
        if hi == 0x80 or (hi == 0x90 and data2 == 0):
            return "note_off"
        if hi == 0xB0:
            return "cc"
        return "other"

    def _process_single_raw(self, ts, status, data1, data2):
        self._process_batch([(ts, status, data1, data2)])

    def _filter_and_map_pitch(self, raw_pitch, mode, custom_range=None):
        """应用 transpose、越界折叠/忽略与 custom_key_map 检查。"""
        if custom_range is not None:
            lo, hi = custom_range
        else:
            lo, hi = KL.INSTRUMENT_RANGES.get(mode, (40, 88))
        tr = int(self.config.get("transpose") or 0)
        shifted = int(raw_pitch) + tr
        folded = False
        if shifted < lo or shifted > hi:
            policy = str(self.config.get("out_of_range") or "fold").lower()
            if policy in ("ignore", "drop"):
                return None, False, True
            shifted = KL.fold_pitch(shifted, lo, hi)
            folded = True
        return shifted, folded, False

    def _process_batch(self, msgs):
        note_ons = []
        other_msgs = []
        for (ts, status, data1, data2) in msgs:
            if status == 0 and data2 == -1:
                continue
            hi = status & 0xF0
            ch = status & 0x0F
            ch_filter = int(self.config.get("channel") or 0)
            if ch_filter > 0 and (ch + 1) != ch_filter:
                continue
            if self.config.get("ignore_drums", True) and ch == 9:
                continue

            kind = self._classify_raw(status, data2)
            if kind == "note_on":
                with self._state_lock:
                    self._stats["received"] += 1
                note_ons.append((ts, ch, data1, data2))
            elif kind in ("note_off", "cc"):
                other_msgs.append((ts, kind, ch, data1, data2))

        with self._state_lock:
            mode = self._instrument_mode

        if note_ons:
            if mode == KL.INSTRUMENT_PIANO_36:
                self._handle_piano_36_batch(note_ons)
            elif mode == KL.INSTRUMENT_GUITAR_36:
                for item in note_ons:
                    self._handle_guitar_36_on(*item)
            else:
                self._handle_wheel_batch(note_ons)

        for (ts, kind, ch, d1, d2) in other_msgs:
            with self._state_lock:
                cur_mode = self._instrument_mode
            if kind == "cc":
                self._handle_cc(ts, ch, d1, d2, cur_mode)
            elif kind == "note_off":
                self._handle_note_off(ts, ch, d1, cur_mode)

    def _flush_expired(self, now):
        with self._state_lock:
            # 1) guitar_36 到期自动松键（含最短 hold 到期与 sustain_max 超时）
            for k in list(self._g36_active.keys()):
                st = self._g36_active[k]
                should_release = False
                if st.get("pending_off") and now >= st["min_up_t"] - 1e-6:
                    should_release = True
                elif st.get("max_up_t", 0.0) > 0 and now >= st["max_up_t"] - 1e-6:
                    should_release = True
                if should_release:
                    self._release_key(k)
                    self._g36_last_up[k] = now
                    for nk in list(st.get("holders") or ()):
                        if self._g36_note_map.get(nk) == k:
                            self._g36_note_map.pop(nk, None)
                    self._g36_active.pop(k, None)

            # 2) piano_36 异步到期释放（先放普通键，再放修饰键）
            for k in list(self._p36_active.keys()):
                st = self._p36_active[k]
                if now >= st["up_t"] - 1e-6:
                    self._release_key(k)
                    self._p36_last_up[k] = now
                    self._p36_active.pop(k, None)

            if self._p36_active_mod is not None:
                dep_exists = any(
                    st.get("mod") == self._p36_active_mod
                    for st in self._p36_active.values()
                )
                if (not dep_exists or not self._p36_active) and now >= self._p36_mod_up_t - 1e-6:
                    rel_mod = self._p36_active_mod
                    self._release_key(rel_mod)
                    self._p36_last_mod_up = now
                    self._p36_last_mod_up_name = rel_mod
                    self._p36_active_mod = None
                    self._p36_mod_up_t = 0.0

            # 3) guitar_wheel 品格弦到期自动松键
            for s in list(self._wheel_active_strings.keys()):
                st = self._wheel_active_strings[s]
                should_release = False
                if st.get("pending_off") and now >= st["min_up_t"] - 1e-6:
                    should_release = True
                elif st.get("max_up_t", 0.0) > 0 and now >= st["max_up_t"] - 1e-6:
                    should_release = True
                if should_release:
                    self._release_key(st["key"])
                    self._wheel_last_up[st["key"]] = now
                    for nk in list(st.get("holders") or ()):
                        if self._wheel_note_map.get(nk) == s:
                            self._wheel_note_map.pop(nk, None)
                    self._wheel_active_strings.pop(s, None)

            # 4) guitar_wheel 和弦模式到期松弦
            kept_chords = []
            for st in self._wheel_chord_held:
                if (st.get("pending_off") and now >= st["min_up_t"] - 1e-6) or (
                    st.get("max_up_t", 0.0) > 0 and now >= st["max_up_t"] - 1e-6
                ):
                    self._release_key(st["key"])
                    self._wheel_last_up[st["key"]] = now
                    for nk in list(st.get("holders") or ()):
                        lst = self._wheel_chord_note_map.get(nk)
                        if lst and st in lst:
                            lst.remove(st)
                            if not lst:
                                self._wheel_chord_note_map.pop(nk, None)
                else:
                    kept_chords.append(st)
            self._wheel_chord_held = kept_chords

    # ── 踏板与 Note Off 处理 ─────────────────────────────────────────────
    def _release_pedal_held(self, now):
        for k, st in list(self._g36_active.items()):
            if st.get("pedal_held"):
                st["pedal_held"] = False
                st["pending_off"] = True
                if now >= st["min_up_t"] - 1e-6:
                    self._release_key(k)
                    self._g36_last_up[k] = now
                    for nk in list(st.get("holders") or ()):
                        if self._g36_note_map.get(nk) == k:
                            self._g36_note_map.pop(nk, None)
                    self._g36_active.pop(k, None)
        for s, st in list(self._wheel_active_strings.items()):
            if st.get("pedal_held"):
                st["pedal_held"] = False
                st["pending_off"] = True
                if now >= st["min_up_t"] - 1e-6:
                    self._release_key(st["key"])
                    self._wheel_last_up[st["key"]] = now
                    for nk in list(st.get("holders") or ()):
                        if self._wheel_note_map.get(nk) == s:
                            self._wheel_note_map.pop(nk, None)
                    self._wheel_active_strings.pop(s, None)
        for st in list(self._wheel_chord_held):
            if st.get("pedal_held"):
                st["pedal_held"] = False
                st["pending_off"] = True
        self._flush_expired(now)

    def _handle_cc(self, ts, ch, cc, val, mode):
        if cc in (120, 123):
            self._pedal_down = False
            self.release_all()
            self._emit_event({
                "type": "cc",
                "cc": int(cc),
                "value": int(val),
                "channel": ch,
                "instrument_mode": mode,
                "injected": False,
                "label": "All Off",
                "detail": "CC%d 紧急停音（全部释放）" % int(cc),
                "t": ts,
            })
            return

        if cc == 121:
            self._pedal_down = False
            self._release_pedal_held(time.perf_counter())
            self._emit_event({
                "type": "cc",
                "cc": 121,
                "value": int(val),
                "channel": ch,
                "instrument_mode": mode,
                "injected": False,
                "label": "Reset",
                "detail": "CC121 重置控制器与延音踏板",
                "t": ts,
            })
            return

        if cc != 64:
            return
        if mode == KL.INSTRUMENT_PIANO_36:
            with self._state_lock:
                self._pedal_down = False
            return
        down = int(val) >= 64
        if not self.config.get("sustain_pedal", True):
            if not down:
                with self._state_lock:
                    self._pedal_down = False
                    self._release_pedal_held(time.perf_counter())
            return
        with self._state_lock:
            self._pedal_down = down
        self._emit_event({
            "type": "cc",
            "cc": 64,
            "value": int(val),
            "channel": ch,
            "instrument_mode": mode,
            "injected": False,
            "label": "踏板↓" if down else "踏板↑",
            "detail": "CC64 延音踏板 %s (%d)" % ("踩下" if down else "松开", val),
            "t": ts,
        })
        if not down:
            with self._state_lock:
                self._release_pedal_held(time.perf_counter())

    def _handle_note_off(self, ts, ch, raw_pitch, mode):
        if mode == KL.INSTRUMENT_PIANO_36:
            # 钢琴 36 键无延音，Note Off 仅作 UI 通知（硬件按 pluck_hold 已释放）
            mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, mode)
            self._emit_event({
                "type": "note_off",
                "raw_pitch": int(raw_pitch),
                "pitch": mapped if mapped is not None else int(raw_pitch),
                "channel": ch,
                "instrument_mode": mode,
                "folded": folded,
                "dropped": dropped,
                "injected": False,
                "label": KL.key_label_for_pitch(mapped, mode) if mapped is not None else "",
                "t": ts,
            })
            return

        if mode == KL.INSTRUMENT_GUITAR_36:
            nk = (int(ch), int(raw_pitch))
            now = time.perf_counter()
            ev_payload = None
            with self._state_lock:
                mapped_key = self._g36_note_map.pop(nk, None)
                ev_pitch = None
                ev_label = ""
                ev_folded = False
                if mapped_key is not None:
                    st = self._g36_active.get(mapped_key)
                    if st is not None:
                        ev_pitch = st.get("pitch")
                        ev_label = KL.key_label_for_pitch(ev_pitch, mode) if ev_pitch is not None else mapped_key
                        holders = st.get("holders")
                        if isinstance(holders, set):
                            holders.discard(nk)
                        if not holders:
                            if self.config.get("sustain_pedal", True) and self._pedal_down:
                                st["pedal_held"] = True
                            elif now >= st["min_up_t"] - 1e-6:
                                self._release_key(mapped_key)
                                self._g36_last_up[mapped_key] = now
                                self._g36_active.pop(mapped_key, None)
                            else:
                                st["pending_off"] = True
                    if ev_pitch is None:
                        mapped, ev_folded, _dropped = self._filter_and_map_pitch(raw_pitch, mode)
                        ev_pitch = mapped if mapped is not None else int(raw_pitch)
                        ev_label = KL.key_label_for_pitch(ev_pitch, mode)
                    ev_payload = {
                        "type": "note_off",
                        "raw_pitch": int(raw_pitch),
                        "pitch": ev_pitch,
                        "channel": ch,
                        "instrument_mode": mode,
                        "folded": ev_folded,
                        "dropped": False,
                        "injected": False,
                        "label": ev_label,
                        "t": ts,
                    }
                else:
                    mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, mode)
                    if dropped or mapped is None:
                        ev_payload = {
                            "type": "note_off",
                            "raw_pitch": int(raw_pitch),
                            "pitch": int(raw_pitch),
                            "channel": ch,
                            "instrument_mode": mode,
                            "folded": False,
                            "dropped": True,
                            "injected": False,
                            "label": "",
                            "t": ts,
                        }
                    else:
                        b = self._resolve_guitar36_binding(mapped)
                        if b is None:
                            ev_payload = {
                                "type": "note_off",
                                "raw_pitch": int(raw_pitch),
                                "pitch": mapped,
                                "channel": ch,
                                "instrument_mode": mode,
                                "folded": folded,
                                "dropped": False,
                                "injected": False,
                                "label": "",
                                "t": ts,
                            }
                        else:
                            k = b["key"]
                            st = self._g36_active.get(k)
                            if st is not None and not st.get("holders") and st.get("pitch") == mapped:
                                if self.config.get("sustain_pedal", True) and self._pedal_down:
                                    st["pedal_held"] = True
                                else:
                                    if now >= st["min_up_t"] - 1e-6:
                                        self._release_key(k)
                                        self._g36_last_up[k] = now
                                        self._g36_active.pop(k, None)
                                    else:
                                        st["pending_off"] = True
                            ev_payload = {
                                "type": "note_off",
                                "raw_pitch": int(raw_pitch),
                                "pitch": mapped,
                                "channel": ch,
                                "instrument_mode": mode,
                                "folded": folded,
                                "dropped": False,
                                "injected": False,
                                "label": b["label"],
                                "t": ts,
                            }
            if ev_payload is not None:
                self._emit_event(ev_payload)
            return

        # guitar_wheel
        nk = (int(ch), int(raw_pitch))
        now = time.perf_counter()
        ev_payload = None
        with self._state_lock:
            handled = False

            mapped_str = self._wheel_note_map.pop(nk, None)
            if mapped_str is not None:
                handled = True
                st = self._wheel_active_strings.get(mapped_str)
                if st is not None:
                    holders = st.get("holders")
                    if isinstance(holders, set):
                        holders.discard(nk)
                    if not holders:
                        if self.config.get("sustain_pedal", True) and self._pedal_down:
                            st["pedal_held"] = True
                        elif now >= st["min_up_t"] - 1e-6:
                            self._release_key(st["key"])
                            self._wheel_last_up[st["key"]] = now
                            self._wheel_active_strings.pop(mapped_str, None)
                        else:
                            st["pending_off"] = True

            mapped_chords = self._wheel_chord_note_map.pop(nk, None)
            if mapped_chords:
                handled = True
                for st in list(mapped_chords):
                    holders = st.get("holders")
                    if isinstance(holders, set):
                        holders.discard(nk)
                    if not holders and st in self._wheel_chord_held:
                        if self.config.get("sustain_pedal", True) and self._pedal_down:
                            st["pedal_held"] = True
                        else:
                            st["pending_off"] = True
                self._flush_expired(now)

            if handled:
                ev_payload = {
                    "type": "note_off",
                    "raw_pitch": int(raw_pitch),
                    "pitch": int(raw_pitch),
                    "channel": ch,
                    "instrument_mode": mode,
                    "folded": False,
                    "dropped": False,
                    "injected": False,
                    "label": "",
                    "t": ts,
                }
            else:
                sub = str(self.config.get("wheel_submode") or "fret").lower()
                single_chord = bool(self.config.get("single_key_chord", False))
                custom_rng = (48, 48 + len(GP.ALL_CHORDS) - 1) if (sub == "chord" and single_chord) else None
                mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, mode, custom_range=custom_rng)
                if dropped or mapped is None:
                    ev_payload = {
                        "type": "note_off",
                        "raw_pitch": int(raw_pitch),
                        "pitch": int(raw_pitch),
                        "channel": ch,
                        "instrument_mode": mode,
                        "folded": False,
                        "dropped": True,
                        "injected": False,
                        "label": "",
                        "t": ts,
                    }
                else:
                    if sub == "fret":
                        for s, st in list(self._wheel_active_strings.items()):
                            if not st.get("holders") and st.get("pitch") == mapped:
                                if self.config.get("sustain_pedal", True) and self._pedal_down:
                                    st["pedal_held"] = True
                                elif now >= st["min_up_t"] - 1e-6:
                                    self._release_key(st["key"])
                                    self._wheel_last_up[st["key"]] = now
                                    self._wheel_active_strings.pop(s, None)
                                else:
                                    st["pending_off"] = True
                    else:
                        for st in list(self._wheel_chord_held):
                            if not st.get("holders") and (st.get("raw_pitch") == raw_pitch or st.get("pitch") == mapped):
                                if self.config.get("sustain_pedal", True) and self._pedal_down:
                                    st["pedal_held"] = True
                                else:
                                    st["pending_off"] = True
                        self._flush_expired(now)
                    ev_payload = {
                        "type": "note_off",
                        "raw_pitch": int(raw_pitch),
                        "pitch": mapped,
                        "channel": ch,
                        "instrument_mode": mode,
                        "folded": folded,
                        "dropped": False,
                        "injected": False,
                        "label": "",
                        "t": ts,
                    }
        if ev_payload is not None:
            self._emit_event(ev_payload)

    # ── 模式 1：guitar_36 实现 ───────────────────────────────────────────
    def _resolve_guitar36_binding(self, pitch):
        cmap = self.config.get("custom_key_map") or {}
        override = cmap.get(pitch) if pitch in cmap else cmap.get(str(pitch))
        if override:
            k = str(override)
            return {"key": k, "mod": None, "label": k, "row": 0, "col": 0}
        return KL.pitch_to_binding(pitch, KL.INSTRUMENT_GUITAR_36)

    def _handle_guitar_36_on(self, ts, ch, raw_pitch, vel):
        mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, KL.INSTRUMENT_GUITAR_36)
        if dropped or mapped is None:
            with self._state_lock:
                self._stats["dropped"] += 1
            self._emit_event({
                "type": "note_on",
                "raw_pitch": int(raw_pitch),
                "pitch": int(raw_pitch),
                "velocity": int(vel),
                "channel": ch,
                "instrument_mode": KL.INSTRUMENT_GUITAR_36,
                "folded": False,
                "dropped": True,
                "injected": False,
                "label": "",
                "keys": [],
                "detail": "MIDI %s (%d) 越界忽略" % (pitch_name(raw_pitch), raw_pitch),
                "t": ts,
            })
            return

        b = self._resolve_guitar36_binding(mapped)
        if b is None:
            return
        k = b["key"]
        nk = (int(ch), int(raw_pitch))

        with self._state_lock:
            if self._instrument_mode != KL.INSTRUMENT_GUITAR_36:
                return
            if folded:
                self._stats["folded"] += 1
            pluck_hold = self._timing.key_hold()
            sustain_max = float(self._timing.p5 or 4.0)
            repluck_gap = self._timing.repluck_gap()

            prev_k = self._g36_note_map.pop(nk, None)
            if prev_k is not None and prev_k != k:
                prev_st = self._g36_active.get(prev_k)
                if prev_st is not None:
                    prev_holders = prev_st.get("holders")
                    if isinstance(prev_holders, set):
                        prev_holders.discard(nk)
                    if not prev_holders:
                        self._release_key(prev_k)
                        self._g36_last_up[prev_k] = time.perf_counter()
                        self._g36_active.pop(prev_k, None)

            # 同键先释放并保证 repluck_gap
            if k in self._g36_active:
                old_st = self._g36_active.pop(k, None)
                self._release_key(k)
                self._g36_last_up[k] = time.perf_counter()
                if old_st is not None:
                    for old_nk in list(old_st.get("holders") or ()):
                        if self._g36_note_map.get(old_nk) == k:
                            self._g36_note_map.pop(old_nk, None)

            last_up = self._g36_last_up.get(k)
            wait_sec = ((last_up + repluck_gap) - time.perf_counter()) if last_up is not None else 0.0

        if wait_sec > 0.0002:
            if not self._wait_unblocked(min(wait_sec, 0.05)):
                return

        can_inj = self._can_inject()
        with self._state_lock:
            if (self._running and self._stop_ev.is_set()) or self._instrument_mode != KL.INSTRUMENT_GUITAR_36:
                return
            now = time.perf_counter()
            if can_inj:
                self._press_key(k)
                self._stats["played"] += 1
            else:
                self._stats["blocked"] += 1

            self._g36_active[k] = {
                "pitch": mapped,
                "down_t": now,
                "min_up_t": now + pluck_hold,
                "max_up_t": (now + sustain_max) if sustain_max > 0 else 0.0,
                "pedal_held": False,
                "pending_off": False,
                "holders": {nk},
            }
            self._g36_note_map[nk] = k

        self._emit_event({
            "type": "note_on",
            "raw_pitch": int(raw_pitch),
            "pitch": mapped,
            "velocity": int(vel),
            "channel": ch,
            "instrument_mode": KL.INSTRUMENT_GUITAR_36,
            "folded": folded,
            "dropped": False,
            "injected": can_inj,
            "label": b["label"],
            "keys": [k],
            "detail": "MIDI %s (%d) → %s" % (pitch_name(mapped), mapped, b["label"]),
            "t": ts,
        })

    # ── 模式 2：piano_36 实现（无延音，Shift/Ctrl 严格互斥双批）───────────
    def _handle_piano_36_batch(self, note_ons):
        items_by_pitch = {}
        drop_events = []
        folded_cnt = 0
        for (ts, ch, raw_pitch, vel) in note_ons:
            mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, KL.INSTRUMENT_PIANO_36)
            if dropped or mapped is None:
                drop_events.append({
                    "type": "note_on",
                    "raw_pitch": int(raw_pitch),
                    "pitch": int(raw_pitch),
                    "velocity": int(vel),
                    "channel": ch,
                    "instrument_mode": KL.INSTRUMENT_PIANO_36,
                    "folded": False,
                    "dropped": True,
                    "injected": False,
                    "label": "",
                    "keys": [],
                    "detail": "MIDI %s (%d) 越界忽略" % (pitch_name(raw_pitch), raw_pitch),
                    "t": ts,
                })
                continue
            b = KL.pitch_to_binding(mapped, KL.INSTRUMENT_PIANO_36)
            if b is None:
                continue
            if folded:
                folded_cnt += 1
            if mapped not in items_by_pitch:
                items_by_pitch[mapped] = ({"pitch": mapped, "vel": vel, "raw_pitch": raw_pitch, "ch": ch, "ts": ts, "folded": folded}, b)

        if drop_events or folded_cnt:
            with self._state_lock:
                self._stats["dropped"] += len(drop_events)
                self._stats["folded"] += folded_cnt
            for dev in drop_events:
                self._emit_event(dev)

        if not items_by_pitch:
            return

        items = [items_by_pitch[p] for p in sorted(items_by_pitch)]
        batches = KL._split_piano_frame_batches(items)

        with self._state_lock:
            if self._instrument_mode != KL.INSTRUMENT_PIANO_36:
                return
            min_gap = max(0.001, float(self._timing.p1))
            try:
                mod_lead_ms = float(self.config.get("mod_lead_ms") if self.config.get("mod_lead_ms") is not None else 2.0)
            except Exception:
                mod_lead_ms = 2.0
            mod_lead_ms = max(1.0, min(20.0, mod_lead_ms))
            mod_lead = max(min_gap, mod_lead_ms / 1000.0)
            hold = max(KL.MIN_HOLD, float(self._timing.key_hold()))
            repluck_gap = max(KL.MIN_HOLD, float(self._timing.repluck_gap()))
            async_mode = bool(self._running)

        for b_idx, (batch_mod, batch_items) in enumerate(batches):
            uniq = []
            seen_keys = set()
            for n, b in batch_items:
                if b["key"] in seen_keys:
                    continue
                seen_keys.add(b["key"])
                uniq.append((n, b))

            need_mod_rel_sleep = False
            old_mod_to_rel = None
            if async_mode:
                with self._state_lock:
                    if self._instrument_mode != KL.INSTRUMENT_PIANO_36 or (self._running and self._stop_ev.is_set()):
                        return
                    if self._p36_active_mod != batch_mod:
                        t_rel = time.perf_counter()
                        had_dep = False
                        for ak in list(self._p36_active.keys()):
                            st = self._p36_active.pop(ak, None)
                            if st is not None and st.get("mod") == self._p36_active_mod:
                                had_dep = True
                            self._release_key(ak)
                            self._p36_last_up[ak] = t_rel
                        if self._p36_active_mod is not None:
                            if had_dep:
                                need_mod_rel_sleep = True
                                old_mod_to_rel = self._p36_active_mod
                            else:
                                rel_mod = self._p36_active_mod
                                self._release_key(rel_mod)
                                self._p36_last_mod_up = time.perf_counter()
                                self._p36_last_mod_up_name = rel_mod
                                self._p36_active_mod = None
                                self._p36_mod_up_t = 0.0
                    else:
                        t_rel = time.perf_counter()
                        for _n, b in uniq:
                            bk = b["key"]
                            if bk in self._p36_active:
                                self._release_key(bk)
                                self._p36_last_up[bk] = t_rel
                                self._p36_active.pop(bk, None)

            if need_mod_rel_sleep:
                if not self._wait_unblocked(min_gap):
                    return
                with self._state_lock:
                    if old_mod_to_rel is not None and self._p36_active_mod == old_mod_to_rel:
                        self._release_key(old_mod_to_rel)
                        self._p36_last_mod_up = time.perf_counter()
                        self._p36_last_mod_up_name = old_mod_to_rel
                        self._p36_active_mod = None
                        self._p36_mod_up_t = 0.0

            # 同物理键跨批/跨帧 repluck_gap 保护 + 修饰键释放间隔保护
            with self._state_lock:
                now = time.perf_counter()
                need_wait = 0.0
                for _n, b in uniq:
                    lu = self._p36_last_up.get(b["key"])
                    if lu is not None:
                        need_wait = max(need_wait, (lu + repluck_gap) - now)
                if self._p36_last_mod_up > 0.0:
                    if batch_mod is not None and self._p36_active_mod != batch_mod:
                        need_wait = max(need_wait, (self._p36_last_mod_up + min_gap) - now)
                    elif batch_mod is None:
                        last_m = self._p36_last_mod_up_name
                        affected = (KL.LSHIFT_AFFECTED_COLS if last_m == "LShift"
                                    else (KL.LCTRL_AFFECTED_COLS if last_m == "LCtrl" else None))
                        if affected is None or any(b["col"] in affected for _n, b in uniq):
                            need_wait = max(need_wait, (self._p36_last_mod_up + min_gap) - now)
            if need_wait > 0.0002:
                if not self._wait_unblocked(min(need_wait, 0.06)):
                    return

            can_inj = self._can_inject()

            need_mod_lead_sleep = False
            if can_inj:
                with self._state_lock:
                    if self._instrument_mode != KL.INSTRUMENT_PIANO_36 or (self._running and self._stop_ev.is_set()):
                        return
                    for mk in list(self._held_keys):
                        if mk in _MOD_KEYS and (not async_mode or mk != batch_mod):
                            self._release_key(mk)
                    if batch_mod is not None and (not async_mode or self._p36_active_mod != batch_mod):
                        self._press_key(batch_mod)
                        if async_mode:
                            self._p36_active_mod = batch_mod
                        need_mod_lead_sleep = True

                if need_mod_lead_sleep:
                    if not self._wait_unblocked(mod_lead):
                        return

                with self._state_lock:
                    if self._instrument_mode != KL.INSTRUMENT_PIANO_36 or (self._running and self._stop_ev.is_set()):
                        return
                    t_down = time.perf_counter()
                    up_t = t_down + hold
                    for _n, b in uniq:
                        self._press_key(b["key"])
                        if async_mode:
                            self._p36_active[b["key"]] = {
                                "up_t": up_t,
                                "down_t": t_down,
                                "col": int(b.get("col", 0)),
                                "mod": batch_mod,
                            }
                    if async_mode and batch_mod is not None:
                        self._p36_mod_up_t = max(self._p36_mod_up_t, up_t + mod_lead)
                    self._stats["played"] += len(uniq)
            else:
                with self._state_lock:
                    self._stats["blocked"] += len(uniq)

            for n, b in uniq:
                keys_desc = ([b["mod"], b["key"]] if b["mod"] else [b["key"]])
                self._emit_event({
                    "type": "note_on",
                    "raw_pitch": int(n["raw_pitch"]),
                    "pitch": int(n["pitch"]),
                    "velocity": int(n["vel"]),
                    "channel": int(n["ch"]),
                    "instrument_mode": KL.INSTRUMENT_PIANO_36,
                    "folded": bool(n["folded"]),
                    "dropped": False,
                    "injected": can_inj,
                    "label": b["label"],
                    "keys": keys_desc,
                    "detail": "MIDI %s (%d) → %s [%s]" % (
                        pitch_name(n["pitch"]), n["pitch"], b["label"], "+".join(keys_desc)
                    ),
                    "t": n["ts"],
                })

            if can_inj and (not async_mode or b_idx < len(batches) - 1):
                if not self._wait_unblocked(hold):
                    return
                with self._state_lock:
                    t_up = time.perf_counter()
                    for _n, b in uniq:
                        self._release_key(b["key"])
                        self._p36_last_up[b["key"]] = t_up
                        self._p36_active.pop(b["key"], None)
                if batch_mod is not None:
                    if not self._wait_unblocked(min_gap):
                        return
                    with self._state_lock:
                        self._release_key(batch_mod)
                        self._p36_last_mod_up = time.perf_counter()
                        self._p36_last_mod_up_name = batch_mod
                        self._p36_active_mod = None
                        self._p36_mod_up_t = 0.0

    # ── 模式 3：guitar_wheel 实现 ────────────────────────────────────────
    def _switch_wheel_to(self, target_page, target_sector, pages=5, need_click=True, can_inj=True):
        """按物理时序三定律完成 T/R 翻页与扇区点选（锁外等待）。"""
        with self._state_lock:
            cur_page = int(self._wheel_state["page"])
            cur_sec = self._wheel_state["sector"]
            steps, turn_key = MC.page_delta(cur_page, target_page, pages=pages)
            page_hold = self._timing.page_key_hold()
            page_gap = max(0.005, float(self._timing.p13))
            after_rel = max(0.001, float(self._timing.p14))
            min_gap = max(0.001, float(self._timing.p1))

        if can_inj:
            if steps > 0 and turn_key:
                for step_i in range(steps):
                    if step_i > 0 and not self._wait_unblocked(page_gap):
                        return
                    with self._state_lock:
                        self._press_key(turn_key)
                    if not self._wait_unblocked(page_hold):
                        return
                    with self._state_lock:
                        self._release_key(turn_key)
                if not self._wait_unblocked(after_rel):
                    return
            if need_click and (steps > 0 or target_sector != cur_sec or cur_page == MC.PAGE_ZERO):
                m = self.io.metrics()
                if m:
                    pt = self.io.sector_point(int(target_sector), m)
                    if pt:
                        self.io.move(pt[0], pt[1])
                        if not self._wait_unblocked(0.001):
                            return
                        with self._state_lock:
                            self._press_mouse("left")
                        if not self._wait_unblocked(min_gap):
                            return

        with self._state_lock:
            self._wheel_state["page"] = int(target_page)
            if need_click:
                self._wheel_state["sector"] = int(target_sector)

    def _release_wheel_click_if_held(self):
        with self._state_lock:
            has_left = ("left" in self._held_mouse)
            if not has_left:
                return
            click_hold = self._timing.click_hold(self._wheel_state.get("submode") or "fret")
            wait_s = max(0.001, click_hold - max(0.001, float(self._timing.p1)))
        self._wait_unblocked(wait_s)
        with self._state_lock:
            self._release_mouse("left")

    def _handle_wheel_batch(self, note_ons):
        sub = str(self.config.get("wheel_submode") or "fret").lower()
        single_chord = bool(self.config.get("single_key_chord", False))
        with self._state_lock:
            self._wheel_state["submode"] = sub
        if sub == "chord":
            if single_chord:
                for item in note_ons:
                    self._handle_wheel_single_chord(*item)
            else:
                self._handle_wheel_multi_chord(note_ons)
            return

        # ── 品格直弹子模式 ──
        mapped_items = []
        drop_events = []
        folded_cnt = 0
        for (ts, ch, raw_pitch, vel) in note_ons:
            mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, KL.INSTRUMENT_GUITAR_WHEEL)
            if dropped or mapped is None:
                drop_events.append({
                    "type": "note_on",
                    "raw_pitch": int(raw_pitch),
                    "pitch": int(raw_pitch),
                    "velocity": int(vel),
                    "channel": ch,
                    "instrument_mode": KL.INSTRUMENT_GUITAR_WHEEL,
                    "folded": False,
                    "dropped": True,
                    "injected": False,
                    "label": "",
                    "keys": [],
                    "detail": "MIDI %s (%d) 越界忽略" % (pitch_name(raw_pitch), raw_pitch),
                    "t": ts,
                })
                continue
            if folded:
                folded_cnt += 1
            mapped_items.append((ts, ch, raw_pitch, vel, mapped, folded))

        if drop_events or folded_cnt:
            with self._state_lock:
                self._stats["dropped"] += len(drop_events)
                self._stats["folded"] += folded_cnt
            for dev in drop_events:
                self._emit_event(dev)

        if not mapped_items:
            return

        # 在 0..24 品中选最能覆盖本批音符、且切换成本最低的品格 f
        pitches = sorted({it[4] for it in mapped_items})
        top_p = pitches[-1]
        with self._state_lock:
            cur_page = int(self._wheel_state["page"])
            cur_fret = int(self._wheel_state["fret"])

        best = None
        for f in range(0, MC.MAX_FRET + 1):
            pairs = []
            used_s = set()
            for it in mapped_items:
                s = MC.fret_string(it[4], f)
                if s is not None and s not in used_s:
                    used_s.add(s)
                    pairs.append((it, s))
            if not pairs:
                continue
            pg, sec = modes.locate("fret", f)
            steps, _k = MC.page_delta(cur_page, pg, pages=5)
            has_top = 1 if any(it[4] == top_p for it, _s in pairs) else 0
            same_fret = 1 if f == cur_fret else 0
            same_page = 1 if pg == cur_page else 0
            is_zero = 1 if pg == MC.PAGE_ZERO else 0
            score = (-len(pairs), -has_top, -same_fret, steps, -same_page, -is_zero, f)
            if best is None or score < best[0]:
                best = (score, f, pg, sec, pairs)

        if best is None:
            return
        _sc, target_fret, target_page, target_sec, pairs = best
        need_click = (target_page != MC.PAGE_ZERO)
        can_inj = self._can_inject()

        self._switch_wheel_to(target_page, target_sec, pages=5, need_click=need_click, can_inj=can_inj)
        with self._state_lock:
            if self._instrument_mode != KL.INSTRUMENT_GUITAR_WHEEL or (self._running and self._stop_ev.is_set()):
                return
            self._wheel_state["fret"] = int(target_fret)
            pluck_hold = self._timing.key_hold()
            sustain_max = float(self._timing.p5 or 4.0)
            repluck_gap = self._timing.repluck_gap()

        emit_list = []
        for (ts, ch, raw_pitch, vel, mapped, folded), s in pairs:
            k = str(int(s))
            nk = (int(ch), int(raw_pitch))
            with self._state_lock:
                prev_s = self._wheel_note_map.pop(nk, None)
                if prev_s is not None and prev_s != s:
                    prev_st = self._wheel_active_strings.get(prev_s)
                    if prev_st is not None:
                        prev_holders = prev_st.get("holders")
                        if isinstance(prev_holders, set):
                            prev_holders.discard(nk)
                        if not prev_holders:
                            self._release_key(prev_st["key"])
                            self._wheel_last_up[prev_st["key"]] = time.perf_counter()
                            self._wheel_active_strings.pop(prev_s, None)

                if s in self._wheel_active_strings:
                    old = self._wheel_active_strings.pop(s)
                    self._release_key(old["key"])
                    self._wheel_last_up[old["key"]] = time.perf_counter()
                    for old_nk in list(old.get("holders") or ()):
                        if self._wheel_note_map.get(old_nk) == s:
                            self._wheel_note_map.pop(old_nk, None)

                lu = self._wheel_last_up.get(k)
                wait_sec = ((lu + repluck_gap) - time.perf_counter()) if lu is not None else 0.0

            if wait_sec > 0.0002:
                if not self._wait_unblocked(min(wait_sec, 0.05)):
                    return

            with self._state_lock:
                if self._instrument_mode != KL.INSTRUMENT_GUITAR_WHEEL or (self._running and self._stop_ev.is_set()):
                    return
                now = time.perf_counter()
                if can_inj:
                    self._press_key(k)
                    self._stats["played"] += 1
                else:
                    self._stats["blocked"] += 1

                self._wheel_active_strings[s] = {
                    "pitch": mapped,
                    "key": k,
                    "down_t": now,
                    "min_up_t": now + pluck_hold,
                    "max_up_t": (now + sustain_max) if sustain_max > 0 else 0.0,
                    "pedal_held": False,
                    "pending_off": False,
                    "holders": {nk},
                }
                self._wheel_note_map[nk] = s
            lbl = "%dp·%d弦" % (target_fret, s)
            emit_list.append({
                "type": "note_on",
                "raw_pitch": int(raw_pitch),
                "pitch": mapped,
                "velocity": int(vel),
                "channel": ch,
                "instrument_mode": KL.INSTRUMENT_GUITAR_WHEEL,
                "folded": folded,
                "dropped": False,
                "injected": can_inj,
                "label": lbl,
                "keys": [k],
                "detail": "MIDI %s (%d) → %s [第%d页]" % (pitch_name(mapped), mapped, lbl, target_page + 1),
                "t": ts,
            })

        for ev_p in emit_list:
            self._emit_event(ev_p)
        if can_inj:
            self._release_wheel_click_if_held()
        self._emit_status("wheel", "品格 %d 品（第 %d 页）" % (target_fret, target_page + 1))

    def _trigger_chord(self, chord_name, pitches_info, can_inj, ts):
        loc = modes.locate("chord", chord_name)
        if not loc:
            return
        target_page, target_sec = loc
        self._switch_wheel_to(target_page, target_sec, pages=4, need_click=True, can_inj=can_inj)
        with self._state_lock:
            if self._instrument_mode != KL.INSTRUMENT_GUITAR_WHEEL or (self._running and self._stop_ev.is_set()):
                return
            self._wheel_state["chord"] = chord_name
            strum_mode = bool(self.config.get("chord_strum_mode", False))
            pluck_hold = self._timing.key_hold()
            sustain_max = float(self._timing.p5 or 4.0)
            p8_wait = max(0.001, float(self._timing.p8))
            strum_hold_short = self._timing.strum_hold_short()
            repluck_gap = self._timing.repluck_gap()

        if can_inj:
            self._release_wheel_click_if_held()

        if strum_mode:
            if can_inj:
                m = self.io.metrics()
                if m:
                    pk = self.io.park_point(m)
                    if pk:
                        self.io.move(pk[0], pk[1])
                        if not self._wait_unblocked(p8_wait):
                            return
                with self._state_lock:
                    self._press_mouse("right")
                self._wait_unblocked(strum_hold_short)
                with self._state_lock:
                    self._release_mouse("right")
                    self._stats["played"] += 1
            else:
                with self._state_lock:
                    self._stats["blocked"] += 1
        else:
            safe_strings = list(GP.pluck_safe_strings(chord_name))
            # 单键触发时弹和弦前 3~4 根安全弦；多音触发时按来音数量/最近安全弦按下
            if len(pitches_info) == 1 and pitches_info[0].get("single_chord"):
                chosen_strings = safe_strings[:4]
            else:
                free = list(safe_strings)
                chosen_strings = []
                for it in pitches_info:
                    if not free:
                        break
                    best_s = min(free, key=lambda ss: abs((GP.pluck_pitch(chord_name, ss) or 60) - it["pitch"]))
                    free.remove(best_s)
                    chosen_strings.append(best_s)
                if not chosen_strings and safe_strings:
                    chosen_strings = safe_strings[:3]

            # 触发新和弦前，先释放旧和弦残留按住弦并记录松开时刻
            with self._state_lock:
                if self._wheel_chord_held:
                    t_rel = time.perf_counter()
                    for old_st in self._wheel_chord_held:
                        self._release_key(old_st["key"])
                        self._wheel_last_up[old_st["key"]] = t_rel
                        for old_nk in list(old_st.get("holders") or ()):
                            lst = self._wheel_chord_note_map.get(old_nk)
                            if lst and old_st in lst:
                                lst.remove(old_st)
                                if not lst:
                                    self._wheel_chord_note_map.pop(old_nk, None)
                    self._wheel_chord_held.clear()

            chord_holders = {(int(it["ch"]), int(it["raw_pitch"])) for it in pitches_info}
            for s in chosen_strings:
                k = str(int(s))
                with self._state_lock:
                    lu = self._wheel_last_up.get(k)
                    wait_sec = ((lu + repluck_gap) - time.perf_counter()) if lu is not None else 0.0
                if wait_sec > 0.0002:
                    if not self._wait_unblocked(min(wait_sec, 0.05)):
                        return
                with self._state_lock:
                    if self._instrument_mode != KL.INSTRUMENT_GUITAR_WHEEL or (self._running and self._stop_ev.is_set()):
                        return
                    now = time.perf_counter()
                    if can_inj:
                        self._press_key(k)
                    st = {
                        "key": k,
                        "pitch": pitches_info[0]["pitch"],
                        "raw_pitch": pitches_info[0]["raw_pitch"],
                        "min_up_t": now + pluck_hold,
                        "max_up_t": (now + sustain_max) if sustain_max > 0 else 0.0,
                        "pedal_held": False,
                        "pending_off": False,
                        "holders": set(chord_holders),
                    }
                    self._wheel_chord_held.append(st)
                    for nk in chord_holders:
                        self._wheel_chord_note_map.setdefault(nk, []).append(st)
            with self._state_lock:
                if can_inj:
                    self._stats["played"] += len(chosen_strings)
                else:
                    self._stats["blocked"] += len(chosen_strings)

        for it in pitches_info:
            self._emit_event({
                "type": "note_on",
                "raw_pitch": int(it["raw_pitch"]),
                "pitch": int(it["pitch"]),
                "velocity": int(it["vel"]),
                "channel": int(it["ch"]),
                "instrument_mode": KL.INSTRUMENT_GUITAR_WHEEL,
                "folded": bool(it["folded"]),
                "dropped": False,
                "injected": can_inj,
                "label": chord_name,
                "keys": [chord_name],
                "detail": "和弦 %s（第%d页·第%d格）" % (chord_name, target_page + 1, target_sec + 1),
                "t": ts,
            })
        self._emit_status("wheel", "和弦 %s（第 %d 页 · 第 %d 格）" % (chord_name, target_page + 1, target_sec + 1))

    def _handle_wheel_single_chord(self, ts, ch, raw_pitch, vel):
        chords = list(GP.ALL_CHORDS)
        lo, hi = 48, 48 + len(chords) - 1
        mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, KL.INSTRUMENT_GUITAR_WHEEL, custom_range=(lo, hi))
        if dropped or mapped is None:
            with self._state_lock:
                self._stats["dropped"] += 1
            return
        if folded:
            with self._state_lock:
                self._stats["folded"] += 1
        chord_name = chords[int(mapped) - lo]
        can_inj = self._can_inject()
        self._trigger_chord(
            chord_name,
            [{"raw_pitch": raw_pitch, "pitch": mapped, "vel": vel, "ch": ch, "folded": folded, "single_chord": True}],
            can_inj,
            ts,
        )

    def _handle_wheel_multi_chord(self, note_ons):
        items = []
        dropped_cnt = 0
        folded_cnt = 0
        for (ts, ch, raw_pitch, vel) in note_ons:
            mapped, folded, dropped = self._filter_and_map_pitch(raw_pitch, KL.INSTRUMENT_GUITAR_WHEEL)
            if dropped or mapped is None:
                dropped_cnt += 1
                continue
            if folded:
                folded_cnt += 1
            items.append({"raw_pitch": raw_pitch, "pitch": mapped, "vel": vel, "ch": ch, "folded": folded, "ts": ts})
        if dropped_cnt or folded_cnt:
            with self._state_lock:
                self._stats["dropped"] += dropped_cnt
                self._stats["folded"] += folded_cnt
        if not items:
            return
        pitches = [it["pitch"] for it in items]
        with self._state_lock:
            prev_chord = self._wheel_state.get("chord")
        chord_name, _score, _ok = MA.match_chord(pitches, prev=prev_chord)
        if not chord_name:
            chord_name = GP.ALL_CHORDS[0]
        can_inj = self._can_inject()
        self._trigger_chord(chord_name, items, can_inj, items[0]["ts"])
