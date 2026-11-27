# -*- coding: utf-8 -*-
"""player/kb_arranger.py -- 《异键演奏》36 键智能织体编排器（吉他 36 键 / 钢琴 36 键通用）。

将原本只存在于吉他轮盘模式的角色拆分与伴奏生成能力移植到 36 键：
  - raw              : 原谱直出（按轨范围过滤，原样演奏）
  - solo             : 1P 独奏（主旋律 + 智能低音根五伴奏 + 句间和弦内音琶音点缀）
  - duet_lead        : 双人合奏 · 1P 主音（提纯主旋律高音线，留出低中音空间）
  - duet_rhythm      : 双人合奏 · 2P 伴奏（柱式 / 节拍律动 / 错峰扫弦滚奏）
  - duet_rhythm_fret : 双人合奏 · 2P 伴奏（分解琶音 / 根音五度交替）
"""
from __future__ import annotations

import bisect
import math

import keyboard_layout as KL
import midi_adapter as MA
from midi_loader import read_flat_notes

KB_ARRANGE_RAW = "raw"
KB_ARRANGE_SOLO = "solo"
KB_ARRANGE_LEAD = "duet_lead"
KB_ARRANGE_CHORD = "duet_rhythm"
KB_ARRANGE_ARP = "duet_rhythm_fret"

KB_ARRANGE_MODES = (
    KB_ARRANGE_RAW,
    KB_ARRANGE_SOLO,
    KB_ARRANGE_LEAD,
    KB_ARRANGE_CHORD,
    KB_ARRANGE_ARP,
)

KB_ARRANGE_LABELS = {
    KB_ARRANGE_RAW: "原谱直出（按音轨）",
    KB_ARRANGE_SOLO: "1P 独奏（旋律+伴奏）",
    KB_ARRANGE_LEAD: "1P 主音（纯旋律）",
    KB_ARRANGE_CHORD: "2P 伴奏（柱式/律动）",
    KB_ARRANGE_ARP: "2P 伴奏（琶音/根音）",
}

KB_ARP_PATTERNS = (
    ("ascending", "从低到高"),
    ("pingpong", "往返折返"),
    ("bass_chord", "根音交替"),
)

# 和弦模板（相对根音半音）与偏好权重
_CHORD_TEMPLATES = (
    ("maj", (0, 4, 7), 0.00),
    ("min", (0, 3, 7), 0.02),
    ("7", (0, 4, 7, 10), 0.14),
    ("maj7", (0, 4, 7, 11), 0.18),
    ("m7", (0, 3, 7, 10), 0.16),
    ("sus4", (0, 5, 7), 0.24),
    ("dim", (0, 3, 6), 0.35),
)

_FRAME_GAP = 0.015
_SLICE_GAP = 0.030
_PIANO_SHARP_PCS = {1, 6, 8}   # Shift# 修饰组（C#/F#/G# 对应 #1/#4/#5）
_PIANO_FLAT_PCS = {3, 10}      # Ctrl b 修饰组（D#/A# 对应 b3/b7）


def _accomp_bounds(instrument_mode: str) -> tuple[int, int]:
    """返回目标 36 键的伴奏舒适区（保留高音呼吸感）。"""
    if instrument_mode == KL.INSTRUMENT_GUITAR_36:
        return (52, 76)   # E3 ~ E5
    return (60, 83)       # C4 ~ B5


def _fold_to(pitch: int, lo: int, hi: int) -> int:
    p = int(pitch)
    while p < lo:
        p += 12
    while p > hi:
        p -= 12
    return max(lo, min(hi, p))


def _guess_chord_root_and_pcs(pitches: set[int]) -> tuple[int, list[int]]:
    """从切片音高集合提取和弦根音 pitch_class 与构成音 pitch_class 列表（根音优先）。"""
    if not pitches:
        return 0, [0, 4, 7]
    sorted_p = sorted(int(p) for p in pitches)
    lowest_pc = sorted_p[0] % 12
    obs_pcs = {p % 12 for p in sorted_p}
    if len(obs_pcs) == 1:
        # 单音兜底补全大三（低音区做根-五-八度稳妥声部）
        return lowest_pc, [lowest_pc, (lowest_pc + 7) % 12, lowest_pc]

    best_score = None
    best_root = lowest_pc
    best_tpl = (0, 4, 7)
    for root in range(12):
        for _name, intervals, bias in _CHORD_TEMPLATES:
            tpl_pcs = {(root + iv) % 12 for iv in intervals}
            extra = len(obs_pcs - tpl_pcs)
            miss = len(tpl_pcs - obs_pcs)
            bass_bonus = -0.35 if root == lowest_pc else 0.0
            sc = extra * 1.5 + miss * 0.45 + bias + bass_bonus
            key = (round(sc, 6), 0 if root == lowest_pc else 1, root)
            if best_score is None or key < best_score:
                best_score = key
                best_root = root
                best_tpl = intervals

    ordered_pcs = []
    for iv in best_tpl:
        pc = (best_root + iv) % 12
        if pc not in ordered_pcs:
            ordered_pcs.append(pc)
    return best_root, ordered_pcs


def _voice_chord(
    root_pc: int,
    ordered_pcs: list[int],
    lo: int,
    hi: int,
    prev_center: float | None,
    instrument_mode: str,
    max_voices: int = 4,
) -> list[int]:
    """为和弦挑选贴近 prev_center 且声部紧凑的 MIDI 音高排列。"""
    # 低音根音落在舒适区下半部
    bass_lo = lo
    bass_hi = min(hi, lo + 14)
    bass_cands = [p for p in range(bass_lo, bass_hi + 1) if p % 12 == root_pc]
    if not bass_cands:
        bass_cands = [_fold_to(root_pc + 60, bass_lo, bass_hi)]

    best_voicing = None
    best_cost = None
    for bass in bass_cands:
        notes = [bass]
        for pc in ordered_pcs[1:]:
            cand = bass + ((pc - bass) % 12)
            if cand == bass:
                cand += 12
            if cand > hi:
                cand -= 12
            if lo <= cand <= hi and cand not in notes:
                notes.append(cand)
        # 若不足 3 音且有空间，补根音高八度
        if len(notes) < max_voices:
            oct_up = bass + 12
            if lo <= oct_up <= hi and oct_up not in notes:
                notes.append(oct_up)
        notes = sorted(notes)[:max_voices]
        center = sum(notes) / float(len(notes))
        span = (notes[-1] - notes[0]) if len(notes) > 1 else 0
        jump = abs(center - prev_center) if prev_center is not None else abs(center - (lo + 9))
        mod_penalty = 0.0
        if instrument_mode == KL.INSTRUMENT_PIANO_36:
            pcs_here = {p % 12 for p in notes}
            if (pcs_here & _PIANO_SHARP_PCS) and (pcs_here & _PIANO_FLAT_PCS):
                mod_penalty = 3.5
        cost = jump * 1.0 + max(0, span - 16) * 0.4 + mod_penalty
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_voicing = notes

    if not best_voicing:
        return [_fold_to(root_pc + 60, lo, hi)]
    return best_voicing


def _cluster_by_gap(notes: list[dict], gap: float) -> list[list[dict]]:
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (float(n["t"]), int(n["pitch"])))
    groups = [[ordered[0]]]
    for n in ordered[1:]:
        if float(n["t"]) - float(groups[-1][0]["t"]) <= gap + 1e-9:
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def _split_tracks(path: str, transpose: int) -> tuple[list[dict], list[dict], list[dict], float]:
    """从 MIDI 提取转调后的 (all_notes, mel_notes, acc_notes, beat_sec)。"""
    names = KL._read_track_names(path)
    flat_notes, info = read_flat_notes(path, drop_drums=True)
    tempo_map = list((info or {}).get("tempo_map") or ())
    fallback_bpm = float((info or {}).get("bpm") or 120.0)

    by_track: dict[int, list[dict]] = {}
    for n in flat_notes:
        by_track.setdefault(int(n.track_idx), []).append({
            "t": round(float(n.start), 6),
            "dur": round(max(0.03, float(n.length)), 6),
            "pitch": int(n.pitch) + int(transpose or 0),
            "vel": int(n.velocity),
            "track": int(n.track_idx),
        })

    valid = []
    for idx in sorted(by_track):
        tname = names[idx] if idx < len(names) else ""
        if KL._DRUM_NAME_RE.search(tname or ""):
            continue
        if by_track[idx]:
            valid.append(idx)

    raw_tracks = [
        i for i in valid
        if (names[i] if i < len(names) else "").strip().upper() not in KL._CONTRACT_TRACK_NAMES
    ]
    pool = raw_tracks if raw_tracks else valid
    if not pool:
        return [], [], [], 0.50

    mel_idxs, acc_idxs, other_idxs = [], [], []
    if not raw_tracks:
        # 仅含契约轨时，按契约语义严格归类，绝不把 SOLO_MASTER 混入伴奏
        by_cname = {
            (names[i] if i < len(names) else "").strip().upper(): i
            for i in pool
        }
        if "DUET_P1_LEAD" in by_cname:
            mel_idxs.append(by_cname["DUET_P1_LEAD"])
        if "DUET_P2_RHYTHM" in by_cname:
            acc_idxs.append(by_cname["DUET_P2_RHYTHM"])
        elif "DUET_P2_ARP" in by_cname:
            acc_idxs.append(by_cname["DUET_P2_ARP"])
        if not mel_idxs and "SOLO_MASTER" in by_cname:
            # 仅存在 SOLO_MASTER（或无 DUET_P1_LEAD）时作单轨兜底拆高低声部
            pool = [by_cname["SOLO_MASTER"]]
            if not acc_idxs:
                other_idxs = [by_cname["SOLO_MASTER"]]
        elif mel_idxs and "SOLO_MASTER" in by_cname:
            # 已存在 DUET_P1_LEAD 时剔除 SOLO_MASTER，避免重复叠奏
            pool = [i for i in pool if i != by_cname["SOLO_MASTER"]]
    else:
        for idx in pool:
            tname = names[idx] if idx < len(names) else ""
            if MA.is_chords_track(tname):
                acc_idxs.append(idx)
            elif MA.is_melody_track(tname):
                mel_idxs.append(idx)
            else:
                other_idxs.append(idx)

    if not mel_idxs and len(pool) > 1:
        cands = other_idxs or pool
        best_mel = min(
            cands,
            key=lambda i: (
                -round(sum(x["pitch"] for x in by_track[i]) / float(len(by_track[i])), 2),
                -len(by_track[i]),
                i,
            ),
        )
        mel_idxs = [best_mel]
        for i in pool:
            if i != best_mel and i not in acc_idxs:
                acc_idxs.append(i)
    elif not acc_idxs and len(pool) > 1:
        mel_set = set(mel_idxs)
        acc_idxs = [i for i in pool if i not in mel_set]

    all_notes: list[dict] = []
    for i in pool:
        all_notes.extend(dict(x) for x in by_track[i])
    all_notes.sort(key=lambda x: (x["t"], x["pitch"]))

    if len(pool) == 1 and not acc_idxs:
        # 单轨曲目自动做高/低声部切分，确保角色拆分对任意单轨 MIDI 均有效
        single = sorted(by_track[pool[0]], key=lambda x: (x["t"], x["pitch"]))
        mel_notes: list[dict] = []
        acc_notes: list[dict] = []
        for grp in _cluster_by_gap(single, _FRAME_GAP):
            top = grp[-1]
            mel_notes.append(dict(top, is_melody=True))
            for low in grp[:-1]:
                if top["pitch"] - low["pitch"] >= 3:
                    acc_notes.append(dict(low, is_melody=False))
        if not acc_notes:
            # 纯单音旋律时，以每两拍左右聚合成兜底和声切片来源
            acc_notes = [dict(x, is_melody=False) for x in mel_notes]
    else:
        mel_notes = []
        for i in (mel_idxs or [pool[0]]):
            for grp in _cluster_by_gap(by_track[i], _FRAME_GAP):
                mel_notes.append(dict(grp[-1], is_melody=True))
        acc_notes = []
        for i in acc_idxs:
            acc_notes.extend(dict(x, is_melody=False) for x in by_track[i])
        if not acc_notes:
            acc_notes = [dict(x, is_melody=False) for x in mel_notes]

    mel_notes.sort(key=lambda x: (x["t"], x["pitch"]))
    acc_notes.sort(key=lambda x: (x["t"], x["pitch"]))

    bpm = fallback_bpm
    if tempo_map:
        bpm = float(tempo_map[0][1] or fallback_bpm)
    beat_sec = 60.0 / bpm if bpm > 1e-3 else 0.50
    beat_sec = max(0.28, min(0.85, beat_sec))
    return all_notes, mel_notes, acc_notes, beat_sec


def _build_acc_slices(acc_notes: list[dict], beat_sec: float) -> list[dict]:
    """从伴奏音符构造和弦切片；若都是稀疏单音则按 2 拍窗口聚类成切片。"""
    if not acc_notes:
        return []
    ordered = sorted(acc_notes, key=lambda x: (x["t"], x["pitch"]))
    slices: list[dict] = []
    for n in ordered:
        t0 = float(n["t"])
        end = t0 + float(n.get("dur") or 0.25)
        if slices and (t0 - slices[-1]["t"]) <= _SLICE_GAP + 1e-9:
            slices[-1]["pitches"].add(int(n["pitch"]))
            slices[-1]["end"] = max(slices[-1]["end"], end)
            slices[-1]["vel"].append(int(n.get("vel") or 80))
        else:
            slices.append({
                "t": t0,
                "end": end,
                "pitches": {int(n["pitch"])},
                "vel": [int(n.get("vel") or 80)],
            })

    multi_cnt = sum(1 for s in slices if len(s["pitches"]) >= 2)
    if slices and multi_cnt < max(1, len(slices) // 4):
        # 绝大多数切片都是单音（典型如分解单音/单旋律兜底），按 ~2 拍分组提纯和声
        win = max(0.60, beat_sec * 2.0)
        grouped: list[dict] = []
        for s in slices:
            if grouped and (s["t"] - grouped[-1]["t"]) < win - 1e-4:
                grouped[-1]["pitches"] |= s["pitches"]
                grouped[-1]["end"] = max(grouped[-1]["end"], s["end"])
                grouped[-1]["vel"].extend(s["vel"])
            else:
                grouped.append({
                    "t": s["t"],
                    "end": s["end"],
                    "pitches": set(s["pitches"]),
                    "vel": list(s["vel"]),
                })
        slices = grouped
    return slices


def _arp_sequence(voicing: list[int], pattern: str, steps_cnt: int) -> list[int]:
    """按指定琶音形态展开步骤音高序列。"""
    if not voicing:
        return []
    seq = sorted(voicing)
    if pattern == "pingpong" and len(seq) >= 3:
        cycle = seq + seq[-2:0:-1]
    elif pattern == "bass_chord" and len(seq) >= 2:
        bass = seq[0]
        uppers = seq[1:]
        cycle = []
        for idx in range(max(4, steps_cnt)):
            if idx % 2 == 0:
                cycle.append(bass)
            else:
                cycle.append(uppers[(idx // 2) % len(uppers)])
        return cycle[:steps_cnt]
    else:
        cycle = seq
    out = []
    for i in range(steps_cnt):
        out.append(cycle[i % len(cycle)])
    return out


def _dedup_notes(notes: list[dict]) -> list[dict]:
    """按 (t, pitch) 排序，同 15ms 帧同音高保留先出现的/高优先级音。"""
    ordered = sorted(notes, key=lambda n: (float(n["t"]), 0 if n.get("is_melody") else 1, int(n["pitch"])))
    out: list[dict] = []
    for grp in _cluster_by_gap(ordered, _FRAME_GAP):
        seen_p: dict[int, dict] = {}
        for n in grp:
            p = int(n["pitch"])
            if p not in seen_p:
                seen_p[p] = dict(n)
            else:
                if float(n.get("dur", 0.0)) > float(seen_p[p].get("dur", 0.0)):
                    seen_p[p]["dur"] = n["dur"]
        out.extend(sorted(seen_p.values(), key=lambda x: x["pitch"]))
    return out


def arrange_keyboard_notes(
    path: str,
    instrument_mode: str,
    arrange_mode: str = KB_ARRANGE_RAW,
    track_scope: str = "all",
    transpose: int = 0,
    enable_auto_accomp: bool = True,
    enable_arpeggio: bool = True,
    enable_chord_rhythm: bool = True,
    strum_roll: bool = False,
    arp_pattern: str = "ascending",
    solo_arpeggio: bool | None = None,
    p2_arpeggio: bool | None = None,
) -> tuple[list[dict], dict]:
    """为 36 键模式生成编排后的音符列表与统计信息（1P 独奏与 2P 伴奏开关彻底隔离）。"""
    mode = str(arrange_mode or KB_ARRANGE_RAW).strip().lower()
    if mode not in KB_ARRANGE_MODES:
        mode = KB_ARRANGE_RAW
    use_solo_arp = bool(enable_arpeggio) if solo_arpeggio is None else bool(solo_arpeggio)
    use_p2_arp = bool(enable_arpeggio) if p2_arpeggio is None else bool(p2_arpeggio)
    lo, hi = KL.INSTRUMENT_RANGES[instrument_mode]
    acc_lo, acc_hi = _accomp_bounds(instrument_mode)
    tr = int(transpose or 0)
    arp_pat = str(arp_pattern or "ascending").strip().lower()
    if arp_pat not in ("ascending", "pingpong", "bass_chord"):
        arp_pat = "ascending"

    if mode == KB_ARRANGE_RAW:
        raw_notes, sel_info = KL.select_notes_by_scope(path, track_scope=track_scope)
        folded = 0
        out = []
        for n in raw_notes:
            p_raw = int(n["pitch"]) + tr
            fp = KL.fold_pitch(p_raw, lo, hi)
            if fp != p_raw:
                folded += 1
            d = dict(n)
            d["pitch"] = fp
            out.append(d)
        return out, {
            "arrange_mode": KB_ARRANGE_RAW,
            "melody_count": len(out),
            "accomp_count": 0,
            "slice_count": 0,
            "strum_roll": False,
            "arp_pattern": arp_pat,
            "folded": folded,
            "tracks": sel_info.get("track_names") or [],
        }

    _all, mel_notes, acc_notes, beat_sec = _split_tracks(path, tr)
    raw_pitches_unshifted = [int(n["pitch"]) - tr for n in _all]
    if not mel_notes and not acc_notes:
        return [], {
            "arrange_mode": mode,
            "melody_count": 0,
            "accomp_count": 0,
            "slice_count": 0,
            "strum_roll": bool(strum_roll),
            "arp_pattern": arp_pat,
            "folded": 0,
            "tracks": [],
            "raw_pitches": raw_pitches_unshifted,
        }

    # 主音路径（duet_lead）
    folded_mel = []
    mel_folded_cnt = 0
    for n in mel_notes:
        p = int(n["pitch"])
        fp = KL.fold_pitch(p, lo, hi)
        if fp != p:
            mel_folded_cnt += 1
        d = dict(n)
        d["pitch"] = fp
        d["is_melody"] = True
        folded_mel.append(d)

    if mode == KB_ARRANGE_LEAD:
        deduped = _dedup_notes(folded_mel)
        return deduped, {
            "arrange_mode": mode,
            "melody_count": len(deduped),
            "accomp_count": 0,
            "slice_count": 0,
            "strum_roll": False,
            "arp_pattern": arp_pat,
            "folded": mel_folded_cnt,
            "tracks": ["1P 主音旋律"],
            "raw_pitches": raw_pitches_unshifted,
        }

    slices = _build_acc_slices(acc_notes, beat_sec)
    voicings: list[tuple[dict, list[int], int]] = []
    prev_center = None
    for s in slices:
        root_pc, pcs = _guess_chord_root_and_pcs(s["pitches"])
        max_v = 3 if instrument_mode == KL.INSTRUMENT_PIANO_36 else 4
        vc = _voice_chord(
            root_pc, pcs, acc_lo, acc_hi,
            prev_center=prev_center,
            instrument_mode=instrument_mode,
            max_voices=max_v,
        )
        prev_center = sum(vc) / float(len(vc))
        voicings.append((s, vc, root_pc))

    # ── 2P 柱式 / 律动 ──────────────────────────────────────────────────
    if mode == KB_ARRANGE_CHORD:
        chord_out: list[dict] = []
        strum_step = 0.018 if strum_roll else 0.0
        beat_idx = 0
        for idx, (s, vc, _rpc) in enumerate(voicings):
            nxt = voicings[idx + 1][0] if idx + 1 < len(voicings) else None
            avg_vel = int(round(sum(s["vel"]) / float(len(s["vel"])))) if s["vel"] else 74
            vel = max(55, min(92, avg_vel))
            D, n_beats, step_b = MA._slice_grid(s, nxt, beat_sec)
            if not enable_chord_rhythm:
                triggers = [(float(s["t"]), max(0.25, min(float(s["end"]) - float(s["t"]), step_b * 2.0)))]
            else:
                stride = step_b
                if stride < 0.22:
                    stride *= 2.0
                triggers = []
                cur = float(s["t"])
                limit = float(s["t"]) + max(0.15, D) - 0.05
                while cur <= limit + 1e-6:
                    triggers.append((round(cur, 6), max(0.16, stride * 0.86)))
                    cur += stride
                if not triggers:
                    triggers = [(float(s["t"]), max(0.22, D * 0.85))]

            for t_hit, dur_hit in triggers:
                order_vc = list(vc)
                if strum_roll and (beat_idx % 2 == 1) and len(order_vc) > 2:
                    # 奇偶拍上下扫交替更有吉他真实扫弦感
                    order_vc = list(reversed(order_vc))
                for k, p in enumerate(order_vc):
                    chord_out.append({
                        "t": round(t_hit + k * strum_step, 6),
                        "dur": round(dur_hit, 6),
                        "pitch": int(p),
                        "vel": max(48, vel - k * 3),
                        "is_melody": False,
                    })
                beat_idx += 1

        deduped = _dedup_notes(chord_out) if not strum_roll else sorted(
            chord_out, key=lambda n: (n["t"], n["pitch"])
        )
        return deduped, {
            "arrange_mode": mode,
            "melody_count": 0,
            "accomp_count": len(deduped),
            "slice_count": len(voicings),
            "strum_roll": bool(strum_roll),
            "arp_pattern": arp_pat,
            "folded": 0,
            "tracks": ["2P 柱式律动" + ("(错峰扫弦)" if strum_roll else "")],
            "raw_pitches": raw_pitches_unshifted,
        }

    # ── 2P 琶音 / 根音 ──────────────────────────────────────────────────
    if mode == KB_ARRANGE_ARP:
        arp_out: list[dict] = []
        for idx, (s, vc, _rpc) in enumerate(voicings):
            nxt = voicings[idx + 1][0] if idx + 1 < len(voicings) else None
            D, _nb, step_b = MA._slice_grid(s, nxt, beat_sec)
            D = max(0.20, D)
            vel = 72
            if not use_p2_arp:
                # 关闭琶音时：稳健弹奏根音 + 五度/八度低音支柱
                picks = vc[:2] if len(vc) >= 2 else vc[:1]
                for p in picks:
                    arp_out.append({
                        "t": round(float(s["t"]), 6),
                        "dur": round(min(D * 0.9, max(0.24, step_b * 1.4)), 6),
                        "pitch": int(p),
                        "vel": vel,
                        "is_melody": False,
                    })
                if D >= step_b * 1.9:
                    arp_out.append({
                        "t": round(float(s["t"]) + step_b, 6),
                        "dur": round(min(step_b * 0.85, 0.45), 6),
                        "pitch": int(vc[0]),
                        "vel": vel - 8,
                        "is_melody": False,
                    })
            else:
                sub_step = max(0.14, step_b * 0.5)
                cnt = max(1, int(math.floor((D - 0.04) / sub_step)) + 1)
                cnt = min(16, cnt)
                seq = _arp_sequence(vc, arp_pat, cnt)
                for k, p in enumerate(seq):
                    tk = round(float(s["t"]) + k * sub_step, 6)
                    if nxt is not None and tk >= float(nxt["t"]) - 0.04:
                        break
                    arp_out.append({
                        "t": tk,
                        "dur": round(max(0.12, sub_step * 0.92), 6),
                        "pitch": int(p),
                        "vel": vel if k == 0 else max(54, vel - 6),
                        "is_melody": False,
                    })

        deduped = _dedup_notes(arp_out)
        return deduped, {
            "arrange_mode": mode,
            "melody_count": 0,
            "accomp_count": len(deduped),
            "slice_count": len(voicings),
            "strum_roll": False,
            "arp_pattern": arp_pat,
            "folded": 0,
            "tracks": ["2P 琶音/根音"],
            "raw_pitches": raw_pitches_unshifted,
        }

    # ── 1P 独奏（主旋律 + 智能伴奏） ────────────────────────────────────
    mel_clean = _dedup_notes(folded_mel)
    if not enable_auto_accomp:
        return mel_clean, {
            "arrange_mode": mode,
            "melody_count": len(mel_clean),
            "accomp_count": 0,
            "slice_count": len(voicings),
            "strum_roll": False,
            "arp_pattern": arp_pat,
            "folded": mel_folded_cnt,
            "tracks": ["1P 独奏(关闭伴奏)"],
            "raw_pitches": raw_pitches_unshifted,
        }

    mel_times = [float(n["t"]) for n in mel_clean]
    solo_acc: list[dict] = []

    def _mel_pitch_near(t_sec: float) -> int | None:
        pos = bisect.bisect_left(mel_times, t_sec - 0.04)
        best = None
        for j in range(max(0, pos - 1), min(len(mel_clean), pos + 2)):
            if abs(float(mel_clean[j]["t"]) - t_sec) <= 0.14:
                p = int(mel_clean[j]["pitch"])
                if best is None or p < best:
                    best = p
        return best

    last_bass_t = -99.0
    bass_min_gap = max(0.32, beat_sec * 0.95)
    for idx, (s, vc, _rpc) in enumerate(voicings):
        t0 = float(s["t"])
        nxt_s_t = float(voicings[idx + 1][0]["t"]) if idx + 1 < len(voicings) else (t0 + beat_sec * 2.0)

        # 1) 强拍低音支撑（优先位于同期旋律下方 >= 3 半音；若旋律贴近音域下界退无可退，温和取高 4+ 半音和弦内音兜底）
        if t0 - last_bass_t >= bass_min_gap - 1e-6:
            mel_here = _mel_pitch_near(t0)
            bass_p = int(vc[0])
            while mel_here is not None and bass_p > mel_here - 3 and bass_p - 12 >= lo:
                bass_p -= 12
            if mel_here is None or bass_p <= mel_here - 3:
                solo_acc.append({
                    "t": round(t0, 6),
                    "dur": round(min(beat_sec * 1.4, max(0.24, nxt_s_t - t0 - 0.04)), 6),
                    "pitch": bass_p,
                    "vel": 68,
                    "is_melody": False,
                })
                last_bass_t = t0
            else:
                # 旋律落在最低 3 个半音 (如 piano_36 C4..D4)：在 [lo, hi] 内挑不二度碰撞的和弦柔和内音
                fallback_cands = [
                    p for p in vc
                    if lo <= p <= hi and abs(p - mel_here) >= 4
                ]
                if not fallback_cands:
                    oct_cand = bass_p + 12
                    if lo <= oct_cand <= hi and abs(oct_cand - mel_here) >= 4:
                        fallback_cands = [oct_cand]
                if fallback_cands:
                    solo_acc.append({
                        "t": round(t0, 6),
                        "dur": round(min(beat_sec * 1.1, max(0.20, nxt_s_t - t0 - 0.04)), 6),
                        "pitch": int(fallback_cands[0]),
                        "vel": 56,
                        "is_melody": False,
                    })
                    last_bass_t = t0

        # 2) 若开启 1P 句间和弦琶音，在旋律长呼吸空隙处温和填入柔和上行分解内音（不受 2P 开关及形态干扰）
        if use_solo_arp:
            pos = bisect.bisect_right(mel_times, t0 + 0.08)
            next_mel_t = mel_times[pos] if pos < len(mel_times) else nxt_s_t
            gap_end = min(next_mel_t - 0.11, nxt_s_t - 0.04)
            step = max(0.15, beat_sec * 0.5)
            fill_start = t0 + step
            if gap_end - fill_start >= step * 0.8:
                slots = min(4, int(math.floor((gap_end - fill_start) / step)) + 1)
                fills = _arp_sequence(vc[1:] or vc, "ascending", slots)
                for k, fp in enumerate(fills):
                    ft = round(fill_start + k * step, 6)
                    solo_acc.append({
                        "t": ft,
                        "dur": round(min(0.28, step * 0.85), 6),
                        "pitch": int(fp),
                        "vel": 58,
                        "is_melody": False,
                    })

    combined = _dedup_notes(mel_clean + solo_acc)
    return combined, {
        "arrange_mode": mode,
        "melody_count": len(mel_clean),
        "accomp_count": max(0, len(combined) - len(mel_clean)),
        "slice_count": len(voicings),
        "strum_roll": False,
        "solo_arpeggio": use_solo_arp,
        "arp_pattern": "ascending",
        "folded": mel_folded_cnt,
        "tracks": ["1P 独奏(旋律+智能伴奏)"],
        "raw_pitches": raw_pitches_unshifted,
    }
