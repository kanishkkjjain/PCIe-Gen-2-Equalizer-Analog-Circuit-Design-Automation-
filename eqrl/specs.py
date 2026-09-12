"""Target specifications and the reward / spec-satisfaction scorer.

The scorer turns a :class:`~eqrl.spice.Metrics` snapshot into

  * a scalar reward in ~[0, 1] (smooth, so the RL search always has signal),
  * a per-spec dict of soft scores, and
  * a per-spec dict of hard pass/fail booleans.

Design intent: hard constraints (power, HD3, noise, bias validity) use one-sided
soft barriers that saturate to 1.0 once met with margin; shape targets (peaking
level and peak frequency) use tent/plateau functions that reward landing inside
the requested band.  A candidate that fails to bias correctly is heavily
penalised so the agent learns to stay in the valid region first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .spice import Metrics


@dataclass
class EqualizerSpec:
    """PCIe Gen2 (5 Gb/s) equalizer targets. Frequencies in Hz, power in W."""

    nyquist: float = 2.5e9
    # HF peaking boost window and the band the peak should fall in
    peaking_min_db: float = 3.0
    peaking_max_db: float = 12.0
    peaking_target_db: float = 8.0
    fpeak_lo: float = 1.25e9
    fpeak_hi: float = 2.5e9
    # gain at Nyquist should be healthy (relative to dc); >= this many dB
    gain_2g5_min_db: float = 0.0
    # hard constraints
    hd3_max_db: float = -30.0
    vn_in_max_rms: float = 1.5e-3
    power_max_w: float = 15e-3
    area_max_m2: float = 0.05e-6   # 0.05 mm^2
    # eye (evaluated by the system-level sim, not the CTLE AC scorer)
    eye_h_min_ui: float = 0.4
    eye_v_min_v: float = 0.100

    # reward weights
    w_peaking: float = 1.0
    w_fpeak: float = 1.0
    w_gain: float = 0.6
    w_power: float = 0.5
    w_hd3: float = 0.6
    w_noise: float = 0.6
    w_bias: float = 1.0
    w_area: float = 0.5
    w_eye: float = 1.2      # the actual system-level goal, weighted highest


def _plateau(x: float, lo: float, hi: float, soft: float) -> float:
    """1.0 inside [lo,hi], decaying by ``soft`` outside. NaN -> 0."""
    if x != x:  # NaN
        return 0.0
    if lo <= x <= hi:
        return 1.0
    d = (lo - x) if x < lo else (x - hi)
    return math.exp(-(d / soft) ** 2)


def _target(x: float, tgt: float, soft: float) -> float:
    if x != x:
        return 0.0
    return math.exp(-((x - tgt) / soft) ** 2)


def _below(x: float, limit: float, soft: float) -> float:
    """One-sided: 1.0 when x <= limit, decaying above it (for max constraints)."""
    if x != x:
        return 0.0
    if x <= limit:
        return 1.0
    return math.exp(-((x - limit) / soft) ** 2)


def _above(x: float, limit: float, soft: float) -> float:
    if x != x:
        return 0.0
    if x >= limit:
        return 1.0
    return math.exp(-((limit - x) / soft) ** 2)


def score(m: Metrics, spec: EqualizerSpec) -> dict:
    """Return {'reward': float, 'scores': {...}, 'pass': {...}, 'metrics': m}."""
    if not m.ok:
        return {"reward": -1.0, "scores": {}, "pass": {}, "metrics": m,
                "note": m.note or "sim-failed"}

    s: dict[str, float] = {}
    # --- shape targets ---
    s["peaking"] = _plateau(m.peaking_db, spec.peaking_min_db,
                            spec.peaking_max_db, soft=3.0)
    # extra pull toward the nominal target so it doesn't sit at the edge
    s["peaking"] = 0.5 * s["peaking"] + 0.5 * _target(m.peaking_db,
                                                       spec.peaking_target_db, 4.0)
    s["fpeak"] = _plateau(m.f_peak, spec.fpeak_lo, spec.fpeak_hi, soft=0.6e9)
    s["gain2g5"] = _above(m.gain_2g5_db, spec.gain_2g5_min_db, soft=4.0)
    # --- hard constraints ---
    s["power"] = _below(m.power_w, spec.power_max_w, soft=3e-3)
    s["hd3"] = _below(m.hd3_db, spec.hd3_max_db, soft=6.0)
    s["noise"] = _below(m.vn_in_rms, spec.vn_in_max_rms, soft=0.5e-3)
    # Area only became measurable once ideal R/C were replaced by PDK devices.
    area_mm2 = m.area_mm2 if m.area_mm2 == m.area_mm2 else float("nan")
    s["area"] = _below(area_mm2, spec.area_max_m2 * 1e6, soft=0.02)
    # Eye: only scored when the closed-loop DFE sim was actually run. Scoring a
    # NaN as 0 would otherwise make every AC-only evaluation look like a failure.
    has_eye = (m.eye_height_v == m.eye_height_v) and (m.eye_width_ui == m.eye_width_ui)
    if has_eye:
        s["eye_h"] = _above(m.eye_height_v, spec.eye_v_min_v, soft=0.05)
        s["eye_w"] = _above(m.eye_width_ui, spec.eye_h_min_ui, soft=0.15)
    # `saturated` now requires the REAL tail device to be in saturation too, not
    # just the input pair -- an ideal current sink could never fail this.
    s["bias"] = 1.0 if m.saturated else 0.15

    w = spec
    weighted = (
        w.w_peaking * s["peaking"] + w.w_fpeak * s["fpeak"] +
        w.w_gain * s["gain2g5"] + w.w_power * s["power"] +
        w.w_hd3 * s["hd3"] + w.w_noise * s["noise"] + w.w_bias * s["bias"] +
        w.w_area * s["area"]
    )
    wsum = (w.w_peaking + w.w_fpeak + w.w_gain + w.w_power + w.w_hd3 +
            w.w_noise + w.w_bias + w.w_area)
    if has_eye:
        weighted += w.w_eye * (s["eye_h"] + s["eye_w"])
        wsum += 2.0 * w.w_eye
    reward = weighted / wsum
    # gate: if bias invalid, cap reward low (invalid operating point)
    if not m.saturated:
        reward = min(reward, 0.2)

    passd = {
        "peaking": spec.peaking_min_db <= m.peaking_db <= spec.peaking_max_db,
        "fpeak": spec.fpeak_lo <= m.f_peak <= spec.fpeak_hi,
        "gain2g5": m.gain_2g5_db >= spec.gain_2g5_min_db,
        "power": m.power_w <= spec.power_max_w,
        "hd3": (m.hd3_db == m.hd3_db) and m.hd3_db <= spec.hd3_max_db,
        "noise": (m.vn_in_rms == m.vn_in_rms) and m.vn_in_rms <= spec.vn_in_max_rms,
        "area": (area_mm2 == area_mm2) and area_mm2 <= spec.area_max_m2 * 1e6,
        "bias": bool(m.saturated),
    }
    if has_eye:
        passd["eye_h"] = m.eye_height_v >= spec.eye_v_min_v
        passd["eye_w"] = m.eye_width_ui >= spec.eye_h_min_ui
    return {"reward": float(reward), "scores": s, "pass": passd, "metrics": m}


def all_pass(scored: dict) -> bool:
    return bool(scored.get("pass")) and all(scored["pass"].values())
