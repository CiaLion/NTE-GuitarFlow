# -*- coding: utf-8 -*-
"""keyboard_layout.py -- 《异键演奏》v1.4 36 键（吉他钢琴键 / 钢琴 36 键）唯一真相源。

包含：
1. 三乐器模式常量与物理音域定义；
2. guitar_36 (52..87, E3..D#6) 与 piano_36 (60..95, C4..B6) 精确键位表；
3. 八度折叠、音域覆盖率与智能移调分析；
4. 多轨范围筛选（all / melody / accomp）；
5. guitar_36（支持长按延音）与 piano_36（无延音、Shift/Ctrl 严格互斥双批调度）编译器。
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mido

import midi_adapter
import player_config
from midi_loader import read_flat_notes

# ══════════════════════════════════════════════════════════════════════════
#  1. 共享常量（唯一真相源）
# ══════════════════════════════════════════════════════════════════════════
INSTRUMENT_GUITAR_WHEEL = "guitar_wheel"   # MIDI 40..88
INSTRUMENT_GUITAR_36 = "guitar_36"         # MIDI 52..87，有延音
INSTRUMENT_PIANO_36 = "piano_36"           # MIDI 60..95，无延音，Shift/Ctrl 互斥

INSTRUMENT_MODES = (
    INSTRUMENT_GUITAR_WHEEL,
    INSTRUMENT_GUITAR_36,
    INSTRUMENT_PIANO_36,
)

INSTRUMENT_RANGES = {
    INSTRUMENT_GUITAR_WHEEL: (40, 88),
    INSTRUMENT_GUITAR_36: (52, 87),
    INSTRUMENT_PIANO_36: (60, 95),
}

INSTRUMENT_CN = {
    INSTRUMENT_GUITAR_WHEEL: "吉他 · 轮盘（品格/和弦）",
    INSTRUMENT_GUITAR_36: "吉他 · 钢琴键（36 键）",
    INSTRUMENT_PIANO_36: "钢琴 · 36 键",
}

FRAME_GAP = 0.015
MIN_HOLD = 0.005

# ══════════════════════════════════════════════════════════════════════════
#  2. 36 键物理键位表
# ══════════════════════════════════════════════════════════════════════════
# guitar_36 (52 E3 .. 87 D#6，无修饰键；row 0=低音, 1=中音, 2=高音)
GUITAR_36_ROWS = (
    ('A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', ';', "'", 'Enter'),
    ('Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', '[', ']'),
    ('1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='),
)

# piano_36 (60 C4 .. 95 B6，最低音左下角 C4；row 0=低音, 1=中音, 2=高音)
PIANO_36_ROWS = (
    ('Z', 'X', 'C', 'V', 'B', 'N', 'M'),
    ('A', 'S', 'D', 'F', 'G', 'H', 'J'),
    ('Q', 'W', 'E', 'R', 'T', 'Y', 'U'),
)

# 八度内半音偏移 pc (0..11) -> (col_idx, mod)
PIANO_36_PC_MAP = {
    0: (0, None),
    1: (0, 'LShift'),
    2: (1, None),
    3: (2, 'LCtrl'),
    4: (2, None),
    5: (3, None),
    6: (3, 'LShift'),
    7: (4, None),
    8: (4, 'LShift'),
    9: (5, None),
    10: (6, 'LCtrl'),
    11: (6, None),
}

# LShift 仅影响列 {0,3,4}（QRT/AFG/ZVB）；LCtrl 仅影响列 {2,6}（EU/DJ/CM）
LSHIFT_AFFECTED_COLS = frozenset({0, 3, 4})
LCTRL_AFFECTED_COLS = frozenset({2, 6})

_MOD_PREFIX = {
    None: "",
    "LShift": "⇧",
    "LCtrl": "⌃",
}

_DRUM_NAME_RE = re.compile(
    r"\b(drum|drums|percussion|cymbals?)\b|\bdrum\s*kit\b|\btama\b",
    re.IGNORECASE,
)

_CONTRACT_TRACK_NAMES = frozenset({
    "SOLO_MASTER",
    "DUET_P1_LEAD",
    "DUET_P2_RHYTHM",
    "DUET_P2_ARP",
})


# ══════════════════════════════════════════════════════════════════════════
#  3. 公开映射与音域分析 API
# ══════════════════════════════════════════════════════════════════════════
def pitch_to_binding(pitch, instrument_mode):
    """把 MIDI 音高映射为 36 键物理键位描述；越界或轮盘模式返回 None。"""
    try:
        p = int(round(float(pitch)))
    except (TypeError, ValueError):
        return None
    if instrument_mode == INSTRUMENT_GUITAR_36:
        lo, hi = INSTRUMENT_RANGES[INSTRUMENT_GUITAR_36]
        if p < lo or p > hi:
            return None
        off = p - lo
        row = off // 12
        col = off % 12
        key = GUITAR_36_ROWS[row][col]
        return {
            "key": key,
            "mod": None,
            "label": key,
            "row": row,
            "col": col,
        }
    if instrument_mode == INSTRUMENT_PIANO_36:
        lo, hi = INSTRUMENT_RANGES[INSTRUMENT_PIANO_36]
        if p < lo or p > hi:
            return None
        off = p - lo
        row = off // 12
        pc = off % 12
        col, mod = PIANO_36_PC_MAP[pc]
        key = PIANO_36_ROWS[row][col]
        return {
            "key": key,
            "mod": mod,
            "label": _MOD_PREFIX.get(mod, "") + key,
            "row": row,
            "col": col,
        }
    return None


def key_label_for_pitch(pitch, instrument_mode):
    """返回 MIDI 音高对应的简洁键帽标签（如 'A', 'Z', '⇧Z', '⌃C'）。"""
    b = pitch_to_binding(pitch, instrument_mode)
    return b["label"] if b else ""


def fold_pitch(pitch, low, high):
    """把任意 MIDI 音高按八度折叠进 [low, high]。"""
    p = int(round(float(pitch)))
    lo, hi = int(low), int(high)
    if lo > hi:
        lo, hi = hi, lo
    while p < lo:
        p += 12
    while p > hi:
        p -= 12
    if p < lo:
        p = lo
    elif p > hi:
        p = hi
    return p


def _extract_pitches(notes):
    out = []
    for n in notes or []:
        if isinstance(n, dict):
            if "pitch" in n:
                out.append(int(round(float(n["pitch"]))))
        elif hasattr(n, "pitch"):
            out.append(int(round(float(n.pitch))))
        else:
            try:
                out.append(int(round(float(n))))
            except (TypeError, ValueError):
                continue
    return out


def analyze_range_and_transpose(notes, instrument_mode):
    """统计音符在目标乐器音域的覆盖率，并搜索 -12..+12 内最佳半音移调。"""
    lo, hi = INSTRUMENT_RANGES.get(instrument_mode, (40, 88))
    pitches = _extract_pitches(notes)
    total = len(pitches)
    if total == 0:
        return {
            "in_range": 0,
            "total": 0,
            "coverage": 0.0,
            "best_shift": 0,
            "best_coverage": 0.0,
            "low": lo,
            "high": hi,
        }
    in_range = sum(1 for p in pitches if lo <= p <= hi)
    coverage = round(in_range / float(total), 4)

    best_key = None
    best_shift = 0
    best_count = in_range
    for sh in range(-12, 13):
        cnt = sum(1 for p in pitches if lo <= (p + sh) <= hi)
        key = (-cnt, abs(sh), 0 if sh == 0 else (1 if sh > 0 else 2), sh)
        if best_key is None or key < best_key:
            best_key = key
            best_shift = sh
            best_count = cnt

    return {
        "in_range": in_range,
        "total": total,
        "coverage": coverage,
        "best_shift": best_shift,
        "best_coverage": round(best_count / float(total), 4),
        "low": lo,
        "high": hi,
    }


# ══════════════════════════════════════════════════════════════════════════
#  4. 音轨筛选（all / melody / accomp）
# ══════════════════════════════════════════════════════════════════════════
def _read_track_names(path):
    mid = mido.MidiFile(path)
    names = []
    for tr in mid.tracks:
        got = ""
        for msg in tr:
            if msg.type == "track_name":
                got = msg.name or ""
                break
        names.append(got)
    return names


def select_notes_by_scope(path, track_scope="all"):
    """按 track_scope ('all' | 'melody' | 'accomp') 从 MIDI 读取并筛选非鼓音符。"""
    scope = str(track_scope or "all").strip().lower()
    if scope not in ("all", "melody", "accomp"):
        scope = "all"

    names = _read_track_names(path)
    flat_notes, info = read_flat_notes(path, drop_drums=True)
    by_track = {}
    for n in flat_notes:
        by_track.setdefault(n.track_idx, []).append({
            "t": round(float(n.start), 6),
            "dur": round(max(0.02, float(n.length)), 6),
            "pitch": int(n.pitch),
            "vel": int(n.velocity),
            "track": int(n.track_idx),
        })

    valid_tracks = []
    for idx in sorted(by_track):
        tname = names[idx] if idx < len(names) else ""
        if _DRUM_NAME_RE.search(tname or ""):
            continue
        if by_track[idx]:
            valid_tracks.append(idx)

    # 若同时存在原始轨和派生契约轨，排除派生契约轨以免重复叠加
    raw_tracks = [
        i for i in valid_tracks
        if (names[i] if i < len(names) else "").strip().upper() not in _CONTRACT_TRACK_NAMES
    ]
    pool = raw_tracks if raw_tracks else valid_tracks

    if not pool:
        return [], {"scope": scope, "tracks": [], "track_names": names, "info": info}

    mel_tracks = []
    acc_tracks = []
    other_tracks = []
    if not raw_tracks:
        # 仅含契约轨时，显式按契约名称识别，避免 SOLO_MASTER 与 DUET 轨重复叠奏或混入伴奏
        by_cname = {
            (names[i] if i < len(names) else "").strip().upper(): i
            for i in pool
        }
        if "DUET_P1_LEAD" in by_cname:
            mel_tracks.append(by_cname["DUET_P1_LEAD"])
        if "DUET_P2_RHYTHM" in by_cname:
            acc_tracks.append(by_cname["DUET_P2_RHYTHM"])
        elif "DUET_P2_ARP" in by_cname:
            acc_tracks.append(by_cname["DUET_P2_ARP"])
        if scope == "all":
            if "SOLO_MASTER" in by_cname:
                chosen = [by_cname["SOLO_MASTER"]]
            elif mel_tracks or acc_tracks:
                chosen = mel_tracks + acc_tracks
            else:
                chosen = list(pool)
        elif scope == "melody":
            if mel_tracks:
                chosen = list(mel_tracks)
            elif "SOLO_MASTER" in by_cname:
                chosen = [by_cname["SOLO_MASTER"]]
            else:
                chosen = [pool[0]]
        else:  # accomp
            chosen = list(acc_tracks)
    else:
        for idx in pool:
            tname = names[idx] if idx < len(names) else ""
            if midi_adapter.is_chords_track(tname):
                acc_tracks.append(idx)
            elif midi_adapter.is_melody_track(tname):
                mel_tracks.append(idx)
            else:
                other_tracks.append(idx)

        chosen = []
        if scope == "all":
            chosen = list(pool)
        elif scope == "melody":
            if mel_tracks:
                chosen = list(mel_tracks)
            elif len(pool) == 1:
                chosen = list(pool)
            elif other_tracks:
                # 无明确旋律轨名时，优先挑平均音高较高且音符密度适中的轨
                def _mel_score(i):
                    ns = by_track[i]
                    avg_p = sum(x["pitch"] for x in ns) / float(len(ns))
                    return (-round(avg_p, 2), -len(ns), i)
                chosen = [min(other_tracks, key=_mel_score)]
            else:
                chosen = [pool[0]]
        else:  # accomp
            if acc_tracks:
                chosen = list(acc_tracks)
            elif len(pool) > 1:
                mel_set = set(mel_tracks)
                if not mel_set and other_tracks:
                    def _mel_score(i):
                        ns = by_track[i]
                        avg_p = sum(x["pitch"] for x in ns) / float(len(ns))
                        return (-round(avg_p, 2), -len(ns), i)
                    mel_set = {min(other_tracks, key=_mel_score)}
                chosen = [i for i in pool if i not in mel_set]
            else:
                chosen = []

    merged = []
    for idx in chosen:
        merged.extend(dict(x) for x in by_track.get(idx, ()))
    merged.sort(key=lambda x: (x["t"], x["pitch"]))

    # 同起点（<=15ms）同音高去重，保留更长时值
    dedup = []
    frames = _cluster_frames(merged, gap=FRAME_GAP)
    for fr in frames:
        dedup.extend(fr["notes"])
    dedup.sort(key=lambda x: (x["t"], x["pitch"]))

    return dedup, {
        "scope": scope,
        "tracks": chosen,
        "track_names": [names[i] if i < len(names) else ("Track %d" % i) for i in chosen],
        "info": info,
    }


# ══════════════════════════════════════════════════════════════════════════
#  5. Timing 工具与分帧
# ══════════════════════════════════════════════════════════════════════════
class _TimingFallback(object):
    def __init__(self, values=None):
        src = dict(player_config.load_timing())
        if values:
            src.update(values)
        self.p1 = float(src.get("min_action_interval", 0.001))
        self.p4 = float(src.get("pluck_hold", 0.034))
        self.p5 = float(src.get("sustain_max", 4.0))
        self.p19 = float(src.get("sustain_min", 0.30))
        self.p20 = float(src.get("repluck_gap", 0.034))
        self.p21 = float(src.get("start_delay", 3.0))
        self._src = src

    def key_hold(self):
        return max(self.p4, MIN_HOLD)

    def repluck_gap(self):
        return max(self.p20, MIN_HOLD)

    def as_dict(self):
        return dict(self._src)


def _resolve_timing(timing):
    if timing is not None:
        return timing
    try:
        from midi_compiler import Timing
        return Timing()
    except Exception:
        return _TimingFallback()


def _op_order(op):
    return {"key_up": 0, "mouse_up": 0, "move_park": 1, "move_sector": 1,
            "key_down": 2, "mouse_down": 2}.get(op, 3)


def _hw(t, op, **kw):
    ev = {"t": round(float(t), 6), "op": op}
    ev.update(kw)
    return ev


def _cluster_frames(notes, gap=FRAME_GAP):
    """按起始差 <= gap 聚成帧；同帧同音高保留时长更长的一颗。"""
    if not notes:
        return []
    order = sorted(notes, key=lambda n: (float(n["t"]), -(float(n.get("vel") or 0))))
    raw_frames = []
    for n in order:
        nt = float(n["t"])
        if raw_frames and (nt - raw_frames[-1]["t"]) <= gap + 1e-9:
            raw_frames[-1]["notes"].append(n)
        else:
            raw_frames.append({"t": nt, "notes": [n]})
    frames = []
    for f in raw_frames:
        seen = {}
        for n in f["notes"]:
            p = int(round(float(n["pitch"])))
            prev = seen.get(p)
            if prev is None or float(n.get("dur") or 0.0) > float(prev.get("dur") or 0.0):
                item = dict(n)
                item["pitch"] = p
                item["t"] = float(item["t"])
                item["dur"] = float(item.get("dur") or 0.25)
                seen[p] = item
        frames.append({"t": float(f["t"]), "notes": [seen[p] for p in sorted(seen)]})
    return frames


def _normalize_input_notes(notes):
    out = []
    for n in notes or []:
        try:
            out.append({
                "t": round(max(0.0, float(n["t"])), 6),
                "dur": round(max(0.001, float(n.get("dur") or 0.25)), 6),
                "pitch": int(round(float(n["pitch"]))),
                "vel": int(n.get("vel") or 80),
                "track": int(n.get("track") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: (x["t"], x["pitch"]))
    return out


# ══════════════════════════════════════════════════════════════════════════
#  6. 编译器 A：guitar_36（有延音，36 键直按，任意多音并发）
# ══════════════════════════════════════════════════════════════════════════
def _guitar_36_hold(dur, t):
    d = float(dur or 0.0)
    if t.p5 > 0 and d >= t.p19:
        return round(max(MIN_HOLD, min(d, t.p5)), 4)
    return round(max(MIN_HOLD, t.key_hold()), 4)


def compile_guitar_36(notes, timing=None, log=None, start_offset=None, detail=False):
    """编译 guitar_36 (52..87) 音符列表为硬件时间轴 dict。"""
    t = _resolve_timing(timing)
    log = log or (lambda _m: None)
    offset = float(t.p21) if start_offset is None else float(start_offset)

    clean = _normalize_input_notes(notes)
    frames = _cluster_frames(clean, gap=FRAME_GAP)
    repluck_gap = max(MIN_HOLD, float(t.repluck_gap()))

    hw = []
    analysis_notes = []
    late_shifts = []
    late_times_raw = []
    late_detail = []

    # key -> {"down": float, "up_ev": dict}
    last_key_state = {}
    plucks = 0
    sustains = 0
    dropped = 0
    last_sound = 0.0

    for fr in frames:
        t_frame = float(fr["t"])
        for n in fr["notes"]:
            pitch = int(n["pitch"])
            binding = pitch_to_binding(pitch, INSTRUMENT_GUITAR_36)
            if binding is None:
                dropped += 1
                analysis_notes.append({
                    "t": round(t_frame, 6),
                    "pitch": pitch,
                    "dropped": True,
                    "late": 0.0,
                    "key": None,
                    "mod": None,
                    "label": "",
                    "mode": INSTRUMENT_GUITAR_36,
                })
                continue

            key = binding["key"]
            hold = _guitar_36_hold(n.get("dur"), t)
            t_on = t_frame
            shift = 0.0

            prev = last_key_state.get(key)
            if prev is not None:
                prev_down = prev["down"]
                prev_up_ev = prev["up_ev"]
                # 同键重按必须满足：prev_up >= prev_down + MIN_HOLD 且 t_on - prev_up >= repluck_gap
                min_next = max(prev_down + repluck_gap, prev_down + MIN_HOLD + repluck_gap)
                if t_on < min_next - 1e-9:
                    shift = min_next - t_on
                    t_on = min_next
                # 若前一次按键的松开时刻侵入了本次按下前的 repluck_gap，则提前松开前键
                limit_up = t_on - repluck_gap
                if float(prev_up_ev["t"]) > limit_up + 1e-9:
                    new_up = max(prev_down + MIN_HOLD, limit_up)
                    prev_up_ev["t"] = round(new_up, 6)
                    if t_on < new_up + repluck_gap - 1e-9:
                        extra_shift = (new_up + repluck_gap) - t_on
                        shift += extra_shift
                        t_on += extra_shift

            if shift > 1e-9:
                late_shifts.append(shift)
                late_times_raw.append(round(t_frame, 6))
                if detail:
                    late_detail.append({
                        "t": round(t_frame, 6),
                        "pitch": pitch,
                        "shift": round(shift, 6),
                        "why": "repluck",
                    })

            t_up = t_on + hold
            down_ev = _hw(t_on, "key_down", key=key, sound=True, pitch=pitch)
            up_ev = _hw(t_up, "key_up", key=key)
            hw.append(down_ev)
            hw.append(up_ev)
            last_key_state[key] = {"down": t_on, "up_ev": up_ev}

            plucks += 1
            if hold > max(MIN_HOLD, t.key_hold()) + 1e-6:
                sustains += 1
            last_sound = max(last_sound, t_on)

            analysis_notes.append({
                "t": round(float(n["t"]), 6),
                "pitch": pitch,
                "dropped": False,
                "late": round(shift, 6),
                "key": key,
                "mod": None,
                "label": binding["label"],
                "mode": INSTRUMENT_GUITAR_36,
            })

    for e in hw:
        e["t"] = round(float(e["t"]) + offset, 6)
    if hw and hw[0]["t"] < 0:
        sh = -hw[0]["t"]
        for e in hw:
            e["t"] = round(e["t"] + sh, 6)
    hw.sort(key=lambda e: (e["t"], _op_order(e["op"])))

    duration = round((hw[-1]["t"] if hw else offset), 3) if hw else 0.0
    late_cnt = len(late_shifts)
    max_late = round(max(late_shifts), 4) if late_shifts else 0.0

    stats = {
        "frames": len(frames),
        "notes": sum(len(f["notes"]) for f in frames),
        "voices": plucks,
        "dropped": dropped,
        "sustains": sustains,
        "events": len(hw),
        "hw_events": len(hw),
        "plucks": plucks,
        "strums": 0,
        "turns": 0,
        "clicks": 0,
        "switches": 0,
        "batches_split": 0,
        "late": late_cnt,
        "max_late": max_late,
        "duration": duration,
        "late_times": sorted(set(late_times_raw)),
    }
    if detail:
        stats["late_detail"] = late_detail

    hw_stats = {
        "events": len(hw),
        "hw_events": len(hw),
        "turns": 0,
        "clicks": 0,
        "plucks": plucks,
        "strums": 0,
        "switches": 0,
        "batches_split": 0,
        "late": late_cnt,
        "max_late": max_late,
        "duration": duration,
        "late_times": sorted(set(late_times_raw)),
    }
    analysis = {
        "kind": INSTRUMENT_GUITAR_36,
        "instrument_mode": INSTRUMENT_GUITAR_36,
        "notes": analysis_notes,
        "dropped": dropped,
        "late": sum(1 for a in analysis_notes if (a.get("late") or 0.0) > 0.002),
    }
    range_info = analyze_range_and_transpose(clean, INSTRUMENT_GUITAR_36)

    return {
        "version": 1,
        "role": INSTRUMENT_GUITAR_36,
        "role_cn": INSTRUMENT_CN[INSTRUMENT_GUITAR_36],
        "kind": INSTRUMENT_GUITAR_36,
        "instrument_mode": INSTRUMENT_GUITAR_36,
        "source": "",
        "start": {
            "page": 1,
            "sector": 1,
            "mode_cn": INSTRUMENT_CN[INSTRUMENT_GUITAR_36],
            "wheel": "无需轮盘",
        },
        "stats": stats,
        "compile_stats": dict(stats),
        "hw_stats": hw_stats,
        "analysis": analysis,
        "range_info": range_info,
        "timing": t.as_dict() if hasattr(t, "as_dict") else {},
        "events": hw,
    }


# ══════════════════════════════════════════════════════════════════════════
#  7. 编译器 B：piano_36（无延音，Shift/Ctrl 严格互斥，同帧最多 2 批）
# ══════════════════════════════════════════════════════════════════════════
def _can_join_batch(binding, batch_mod):
    """判定一个音符绑定是否能安全并入指定修饰键批（绝不串音）。"""
    mod = binding["mod"]
    col = binding["col"]
    if batch_mod is None:
        return mod is None
    if batch_mod == "LShift":
        return mod == "LShift" or (mod is None and col not in LSHIFT_AFFECTED_COLS)
    if batch_mod == "LCtrl":
        return mod == "LCtrl" or (mod is None and col not in LCTRL_AFFECTED_COLS)
    return False


def _split_piano_frame_batches(items):
    """把同一帧内合法音符（(note, binding) 列表）拆成 1 或 2 批。

    利用物理性质：
    - LShift 批兼容 {LShift, None@col∉{0,3,4}}
    - LCtrl  批兼容 {LCtrl,  None@col∉{2,6}}
    两者并集覆盖全部 36 个音，因此任何同帧和弦最多只需 2 批。
    """
    if not items:
        return []

    has_shift = any(b["mod"] == "LShift" for _n, b in items)
    has_ctrl = any(b["mod"] == "LCtrl" for _n, b in items)
    has_n_shift = any(b["mod"] is None and b["col"] in LSHIFT_AFFECTED_COLS for _n, b in items)
    has_n_ctrl = any(b["mod"] is None and b["col"] in LCTRL_AFFECTED_COLS for _n, b in items)

    # 1 批直通判定
    if not has_shift and not has_ctrl:
        return [(None, items)]
    if not has_ctrl and not has_n_shift:
        return [("LShift", items)]
    if not has_shift and not has_n_ctrl:
        return [("LCtrl", items)]

    # 必须拆 2 批：最高音所在批优先作为批 A（准点）
    top_pitch = max(int(n["pitch"]) for n, _b in items)

    if has_shift and not has_ctrl:
        # 批 S = LShift 兼容集；批 N = 剩余受 LShift 影响的自然音（None 批）
        b_shift = [(n, b) for n, b in items if _can_join_batch(b, "LShift")]
        b_none = [(n, b) for n, b in items if not _can_join_batch(b, "LShift")]
        top_in_none = any(int(n["pitch"]) == top_pitch for n, _b in b_none)
        if top_in_none and b_none:
            return [(None, b_none), ("LShift", b_shift)]
        return [("LShift", b_shift), (None, b_none)]

    if has_ctrl and not has_shift:
        # 批 C = LCtrl 兼容集；批 N = 剩余受 LCtrl 影响的自然音（None 批）
        b_ctrl = [(n, b) for n, b in items if _can_join_batch(b, "LCtrl")]
        b_none = [(n, b) for n, b in items if not _can_join_batch(b, "LCtrl")]
        top_in_none = any(int(n["pitch"]) == top_pitch for n, _b in b_none)
        if top_in_none and b_none:
            return [(None, b_none), ("LCtrl", b_ctrl)]
        return [("LCtrl", b_ctrl), (None, b_none)]

    # 同时存在 LShift 音与 LCtrl 音：分为 LShift 批与 LCtrl 批
    # 注意自由列 {1, 5} 两者都兼容，优先归入第一批（准点 batch A）
    top_is_ctrl = any(int(n["pitch"]) == top_pitch and b["mod"] == "LCtrl" for n, b in items)
    top_is_n_shift = any(
        int(n["pitch"]) == top_pitch and b["mod"] is None and b["col"] in LSHIFT_AFFECTED_COLS
        for n, b in items
    )
    if top_is_ctrl or top_is_n_shift:
        first_mod, second_mod = "LCtrl", "LShift"
    else:
        first_mod, second_mod = "LShift", "LCtrl"

    batch_a = []
    batch_b = []
    for n, b in items:
        if _can_join_batch(b, first_mod):
            batch_a.append((n, b))
        else:
            batch_b.append((n, b))

    # 若某批实际不含对应修饰键音符，则降级为 None 批
    def _effective_mod(mod, grp):
        if mod is None:
            return None
        if any(b["mod"] == mod for _n, b in grp):
            return mod
        return None

    out = []
    if batch_a:
        out.append((_effective_mod(first_mod, batch_a), batch_a))
    if batch_b:
        out.append((_effective_mod(second_mod, batch_b), batch_b))
    return out


def compile_piano_36(notes, timing=None, log=None, start_offset=None, detail=False):
    """编译 piano_36 (60..95) 音符列表：无延音，Shift/Ctrl 严格互斥。"""
    t = _resolve_timing(timing)
    log = log or (lambda _m: None)
    offset = float(t.p21) if start_offset is None else float(start_offset)

    clean = _normalize_input_notes(notes)
    frames = _cluster_frames(clean, gap=FRAME_GAP)

    min_gap = max(0.001, float(t.p1))
    hold = round(max(MIN_HOLD, float(t.key_hold())), 4)
    repluck_gap = max(MIN_HOLD, float(t.repluck_gap()))

    hw = []
    analysis_notes = []
    late_shifts = []
    late_times_raw = []
    late_detail = []

    last_key_down = {}
    last_key_up = {}
    last_mod_up = None
    last_mod_up_by_name = {"LShift": None, "LCtrl": None}
    last_batch_t_on = None
    # 记录无修饰批最后一个键释放的时间，防止后续修饰键提前按下产生串音
    last_plain_up_by_col = {}
    last_any_key_up = None

    plucks = 0
    switches = 0
    batches_split = 0
    dropped = 0
    last_sound = 0.0

    for fr in frames:
        t_frame = float(fr["t"])
        playable = []
        for n in fr["notes"]:
            pitch = int(n["pitch"])
            binding = pitch_to_binding(pitch, INSTRUMENT_PIANO_36)
            if binding is None:
                dropped += 1
                analysis_notes.append({
                    "t": round(float(n["t"]), 6),
                    "pitch": pitch,
                    "dropped": True,
                    "late": 0.0,
                    "key": None,
                    "mod": None,
                    "label": "",
                    "mode": INSTRUMENT_PIANO_36,
                })
            else:
                playable.append((n, binding))

        if not playable:
            continue

        batches = _split_piano_frame_batches(playable)
        if len(batches) > 1:
            batches_split += (len(batches) - 1)

        for b_idx, (batch_mod, batch_items) in enumerate(batches):
            # 同一物理键在同一批内不可重复（正常由不同列/不同音域自然满足，此处防御去重）
            uniq_items = []
            seen_keys = set()
            for n, b in batch_items:
                if b["key"] in seen_keys:
                    continue
                seen_keys.add(b["key"])
                uniq_items.append((n, b))

            t_target = t_frame
            t_on = t_target
            if last_batch_t_on is not None:
                t_on = max(t_on, last_batch_t_on)

            # 1) 同物理键重按约束：距离上次按下/松开至少间隔 repluck_gap
            for _n, b in uniq_items:
                k = b["key"]
                if k in last_key_down:
                    t_on = max(t_on, last_key_down[k] + repluck_gap)
                if k in last_key_up:
                    t_on = max(t_on, last_key_up[k] + repluck_gap)

            # 2) 修饰键包络互斥与前后动作安全间隔约束
            if batch_mod is not None:
                # 批前 min_gap 按下修饰键 -> t_mod_down = t_on - min_gap
                min_mod_down = -1e9
                if last_mod_up is not None:
                    min_mod_down = max(min_mod_down, last_mod_up + min_gap)
                if last_any_key_up is not None:
                    min_mod_down = max(min_mod_down, last_any_key_up + min_gap)
                affected_cols = (LSHIFT_AFFECTED_COLS if batch_mod == "LShift"
                                 else LCTRL_AFFECTED_COLS)
                for col in affected_cols:
                    if col in last_plain_up_by_col:
                        min_mod_down = max(min_mod_down, last_plain_up_by_col[col] + min_gap)
                t_on = max(t_on, min_mod_down + min_gap)
            else:
                shift_up = last_mod_up_by_name.get("LShift")
                if shift_up is not None and any(
                    b["col"] in LSHIFT_AFFECTED_COLS for _n, b in uniq_items
                ):
                    t_on = max(t_on, shift_up + min_gap)
                ctrl_up = last_mod_up_by_name.get("LCtrl")
                if ctrl_up is not None and any(
                    b["col"] in LCTRL_AFFECTED_COLS for _n, b in uniq_items
                ):
                    t_on = max(t_on, ctrl_up + min_gap)

            # 仅批 A 计入跨帧 late（同帧拆出的批 B 属于必然物理分批，计入 batches_split）
            shift = max(0.0, t_on - t_target)
            note_late = shift if b_idx == 0 else 0.0
            if b_idx == 0 and shift > 1e-9:
                late_shifts.append(shift)
                late_times_raw.append(round(t_target, 6))
                if detail:
                    for n, _b in uniq_items:
                        late_detail.append({
                            "t": round(t_target, 6),
                            "pitch": int(n["pitch"]),
                            "shift": round(shift, 6),
                            "why": "batch_guard",
                        })

            if batch_mod is not None:
                hw.append(_hw(t_on - min_gap, "key_down", key=batch_mod))
                switches += 1

            t_keys_up = t_on + hold
            for n, b in uniq_items:
                k = b["key"]
                p = int(n["pitch"])
                hw.append(_hw(t_on, "key_down", key=k, sound=True, pitch=p))
                hw.append(_hw(t_keys_up, "key_up", key=k))
                last_key_down[k] = t_on
                last_key_up[k] = t_keys_up
                if batch_mod is None:
                    last_plain_up_by_col[b["col"]] = max(
                        last_plain_up_by_col.get(b["col"], -1e9), t_keys_up
                    )
                plucks += 1
                analysis_notes.append({
                    "t": round(float(n["t"]), 6),
                    "pitch": p,
                    "dropped": False,
                    "late": round(note_late, 6),
                    "key": k,
                    "mod": b["mod"],
                    "label": b["label"],
                    "mode": INSTRUMENT_PIANO_36,
                })

            last_any_key_up = max(last_any_key_up or t_keys_up, t_keys_up)
            last_batch_t_on = t_on

            if batch_mod is not None:
                t_mod_up = t_keys_up + min_gap
                hw.append(_hw(t_mod_up, "key_up", key=batch_mod))
                last_mod_up = max(last_mod_up or t_mod_up, t_mod_up)
                prev_m = last_mod_up_by_name.get(batch_mod)
                last_mod_up_by_name[batch_mod] = max(prev_m, t_mod_up) if prev_m is not None else t_mod_up

            last_sound = max(last_sound, t_on)

    for e in hw:
        e["t"] = round(float(e["t"]) + offset, 6)
    if hw and hw[0]["t"] < 0:
        sh = -hw[0]["t"]
        for e in hw:
            e["t"] = round(e["t"] + sh, 6)
    hw.sort(key=lambda e: (e["t"], _op_order(e["op"])))

    duration = round((hw[-1]["t"] if hw else offset), 3) if hw else 0.0
    late_cnt = len(late_shifts)
    max_late = round(max(late_shifts), 4) if late_shifts else 0.0

    stats = {
        "frames": len(frames),
        "notes": sum(len(f["notes"]) for f in frames),
        "voices": plucks,
        "dropped": dropped,
        "sustains": 0,
        "events": len(hw),
        "hw_events": len(hw),
        "plucks": plucks,
        "strums": 0,
        "turns": 0,
        "clicks": 0,
        "switches": switches,
        "batches_split": batches_split,
        "late": late_cnt,
        "max_late": max_late,
        "duration": duration,
        "late_times": sorted(set(late_times_raw)),
    }
    if detail:
        stats["late_detail"] = late_detail

    hw_stats = {
        "events": len(hw),
        "hw_events": len(hw),
        "turns": 0,
        "clicks": 0,
        "plucks": plucks,
        "strums": 0,
        "switches": switches,
        "batches_split": batches_split,
        "late": late_cnt,
        "max_late": max_late,
        "duration": duration,
        "late_times": sorted(set(late_times_raw)),
    }
    analysis = {
        "kind": INSTRUMENT_PIANO_36,
        "instrument_mode": INSTRUMENT_PIANO_36,
        "notes": analysis_notes,
        "dropped": dropped,
        "late": sum(1 for a in analysis_notes if (a.get("late") or 0.0) > 0.002),
    }
    range_info = analyze_range_and_transpose(clean, INSTRUMENT_PIANO_36)

    return {
        "version": 1,
        "role": INSTRUMENT_PIANO_36,
        "role_cn": INSTRUMENT_CN[INSTRUMENT_PIANO_36],
        "kind": INSTRUMENT_PIANO_36,
        "instrument_mode": INSTRUMENT_PIANO_36,
        "source": "",
        "start": {
            "page": 1,
            "sector": 1,
            "mode_cn": INSTRUMENT_CN[INSTRUMENT_PIANO_36],
            "wheel": "无需轮盘",
        },
        "stats": stats,
        "compile_stats": dict(stats),
        "hw_stats": hw_stats,
        "analysis": analysis,
        "range_info": range_info,
        "timing": t.as_dict() if hasattr(t, "as_dict") else {},
        "events": hw,
    }


# ══════════════════════════════════════════════════════════════════════════
#  8. 顶层入口：compile_keyboard_song
# ══════════════════════════════════════════════════════════════════════════
def _empty_keyboard_timeline(path, instrument_mode, t, track_scope, transpose, range_info):
    return {
        "version": 1,
        "role": instrument_mode,
        "role_cn": INSTRUMENT_CN.get(instrument_mode, instrument_mode),
        "kind": instrument_mode,
        "instrument_mode": instrument_mode,
        "track_scope": track_scope,
        "transpose": int(transpose or 0),
        "source": os.path.abspath(path) if path else "",
        "start": {
            "page": 1,
            "sector": 1,
            "mode_cn": INSTRUMENT_CN.get(instrument_mode, instrument_mode),
            "wheel": "无需轮盘",
        },
        "stats": {
            "frames": 0, "notes": 0, "voices": 0, "events": 0,
            "hw_events": 0, "plucks": 0, "strums": 0, "turns": 0,
            "clicks": 0, "switches": 0, "batches_split": 0,
            "dropped": 0, "folded": 0, "sustains": 0,
            "late": 0, "max_late": 0.0, "duration": 0.0,
            "late_times": [], "no_track_notes": True,
        },
        "compile_stats": {},
        "hw_stats": {
            "events": 0, "hw_events": 0, "turns": 0, "clicks": 0,
            "plucks": 0, "strums": 0, "switches": 0, "batches_split": 0,
            "late": 0, "max_late": 0.0, "duration": 0.0, "late_times": [],
        },
        "analysis": {
            "kind": instrument_mode,
            "instrument_mode": instrument_mode,
            "notes": [],
            "dropped": 0,
            "late": 0,
        },
        "range_info": range_info,
        "timing": t.as_dict() if hasattr(t, "as_dict") else {},
        "events": [],
        "notes": [],
    }


def compile_keyboard_song(path, instrument_mode, timing=None, track_scope="all",
                          transpose=0, custom_notes=None, start_offset=None,
                          log=None, detail=False,
                          arrange_mode="raw", enable_auto_accomp=True,
                          enable_arpeggio=True, enable_chord_rhythm=True,
                          strum_roll=False, arp_pattern="ascending",
                          solo_arpeggio=None, p2_arpeggio=None):
    """编译一首 MIDI（或卷帘 custom_notes）为 36 键时间轴 dict。"""
    if instrument_mode not in (INSTRUMENT_GUITAR_36, INSTRUMENT_PIANO_36):
        raise ValueError("未知 36 键乐器模式: %r" % (instrument_mode,))

    t = _resolve_timing(timing)
    log = log or (lambda _m: None)
    tr = int(transpose or 0)
    lo, hi = INSTRUMENT_RANGES[instrument_mode]
    arr_mode = str(arrange_mode or "raw").strip().lower()
    arr_info = None

    if custom_notes is not None:
        raw_notes = _normalize_input_notes(custom_notes)
        sel_info = {"scope": track_scope, "tracks": [], "track_names": ["自定义画布"]}
        # 卷帘画布上的音符已是最终编辑音高，不二次移调、不自动折叠，越界标红框
        range_raw = analyze_range_and_transpose(raw_notes, instrument_mode)
        play_notes = raw_notes
        folded_count = 0
        range_info = dict(range_raw)
        range_info["transpose"] = tr
        range_info["folded"] = 0
        range_info["raw_coverage"] = range_raw["coverage"]
    elif arr_mode != "raw":
        if not path:
            raise ValueError("compile_keyboard_song 需要提供 MIDI 路径或 custom_notes")
        import kb_arranger as KA
        # 非 raw 智能编配模式自动按角色拆分旋律/伴奏，统一 track_scope 契约标记为 "auto"
        track_scope = "auto"
        play_notes, arr_info = KA.arrange_keyboard_notes(
            path, instrument_mode,
            arrange_mode=arr_mode,
            track_scope=track_scope,
            transpose=tr,
            enable_auto_accomp=bool(enable_auto_accomp),
            enable_arpeggio=bool(enable_arpeggio),
            enable_chord_rhythm=bool(enable_chord_rhythm),
            strum_roll=bool(strum_roll),
            arp_pattern=str(arp_pattern or "ascending"),
            solo_arpeggio=solo_arpeggio,
            p2_arpeggio=p2_arpeggio,
        )
        sel_info = {"scope": track_scope, "tracks": [], "track_names": arr_info.get("tracks") or []}
        raw_pitches = arr_info.pop("raw_pitches", None)
        if raw_pitches is None:
            raw_pitches, _ = select_notes_by_scope(path, track_scope="all")
        raw_stat = analyze_range_and_transpose(raw_pitches, instrument_mode)
        arr_stat = analyze_range_and_transpose(play_notes, instrument_mode)
        range_info = dict(arr_stat)
        range_info["best_shift"] = raw_stat["best_shift"]
        range_info["best_coverage"] = raw_stat["best_coverage"]
        range_info["raw_coverage"] = raw_stat["coverage"]
        range_info["transpose"] = tr
        folded_count = int(arr_info.get("folded") or 0)
        range_info["folded"] = folded_count
    else:
        if not path:
            raise ValueError("compile_keyboard_song 需要提供 MIDI 路径或 custom_notes")
        raw_notes, sel_info = select_notes_by_scope(path, track_scope=track_scope)
        range_raw = analyze_range_and_transpose(raw_notes, instrument_mode)

        shifted = []
        for n in raw_notes:
            d = dict(n)
            d["pitch"] = int(n["pitch"]) + tr
            shifted.append(d)

        range_shifted = analyze_range_and_transpose(shifted, instrument_mode)
        range_info = dict(range_shifted)
        range_info["best_shift"] = range_raw["best_shift"]
        range_info["best_coverage"] = range_raw["best_coverage"]
        range_info["raw_coverage"] = range_raw["coverage"]
        range_info["transpose"] = tr

        folded_count = 0
        play_notes = []
        for d in shifted:
            p = int(d["pitch"])
            fp = fold_pitch(p, lo, hi)
            if fp != p:
                folded_count += 1
            d["pitch"] = fp
            play_notes.append(d)
        range_info["folded"] = folded_count

    if not play_notes:
        tl = _empty_keyboard_timeline(path, instrument_mode, t, track_scope, tr, range_info)
        tl["arrange_mode"] = arr_mode
        if arr_info is not None:
            tl["arrange_info"] = arr_info
        log("%s：筛选范围「%s」下没有可用音符" % (INSTRUMENT_CN[instrument_mode], track_scope))
        return tl

    if instrument_mode == INSTRUMENT_GUITAR_36:
        tl = compile_guitar_36(play_notes, timing=t, log=log,
                               start_offset=start_offset, detail=detail)
    else:
        tl = compile_piano_36(play_notes, timing=t, log=log,
                              start_offset=start_offset, detail=detail)

    tl["source"] = os.path.abspath(path) if path else ""
    tl["track_scope"] = str(track_scope or "all")
    tl["arrange_mode"] = arr_mode
    if arr_info is not None:
        tl["arrange_info"] = arr_info
    tl["transpose"] = tr
    tl["range_info"] = range_info
    tl["notes"] = play_notes
    tl["stats"]["folded"] = folded_count
    tl["compile_stats"]["folded"] = folded_count
    tl["adapter"] = {
        "track_scope": str(track_scope or "all"),
        "arrange_mode": arr_mode,
        "tracks": sel_info.get("track_names") or [],
    }
    if custom_notes is not None:
        tl["custom"] = True

    st = tl["stats"]
    mode_tag = "范围=%s" % tl["track_scope"] if arr_mode == "raw" else "编配=%s" % arr_mode
    log("%s（%s，移调=%+d）：%d 音 / %d 帧，覆盖率 %.1f%%，折叠 %d，硬件动作 %d，拆批 %d，late=%d"
        % (INSTRUMENT_CN[instrument_mode], mode_tag, tr,
           st.get("notes", 0), st.get("frames", 0),
           range_info["coverage"] * 100.0, folded_count,
           st.get("hw_events", 0), st.get("batches_split", 0), st.get("late", 0)))
    return tl
