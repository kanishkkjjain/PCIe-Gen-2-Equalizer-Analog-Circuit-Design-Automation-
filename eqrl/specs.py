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
from dataclasses import dataclass

from .spice import Metrics


@dataclass
class EqualizerSpec:
    """PCIe Gen2 (5 Gb/s) equalizer targets. Frequencies in Hz, power in W."""

    nyquist: float = 2.5e9
    # Transmitter drive, differential peak-to-peak, at the CHANNEL input.
    # The brief states an eye requirement but no drive level, so one had to be
    # chosen -- and an eye number is meaningless without it, since eye height
    # scales directly with it. 200 mV pp is CONSERVATIVE: a real PCIe Gen2
    # transmitter drives ~800 mV pp, which would give a larger eye. Recorded in
    # report.json so the number can be checked rather than guessed at.
    tx_swing_pp_v: float = 0.200
    # HF peaking boost window and the band the peak should fall in
    peaking_min_db: float = 3.0
    peaking_max_db: float = 12.0
    peaking_target_db: float = 8.0
    fpeak_lo: float = 1.25e9
    fpeak_hi: float = 2.5e9
    # Gain at Nyquist. NOT IN THE BRIEF -- it was invented, and while it was
    # weighted at 0.6 it silently vetoed otherwise-valid designs. It is kept as
    # a REPORTED metric (it is genuinely informative) but is no longer scored
    # or pass/fail'd by default. Set w_gain > 0 to opt back in.
    gain_2g5_min_db: float = 0.0
    # hard constraints
    hd3_max_db: float = -30.0
    vn_in_max_rms: float = 1.5e-3
    power_max_w: float = 15e-3
    area_max_m2: float = 0.05e-6   # 0.05 mm^2
    # When True (the default), a MISSING eye result is a FAILURE rather than a
    # spec that quietly disappears. Set False only for deliberately AC-only
    # evaluation (--no-eye), where there is no eye to judge.
    require_eye: bool = True
    # eye (closed-loop DFE sim). Names say WHAT is bounded and in WHICH unit:
    # these two used to be crossed (`eye_h_min_ui` held the width limit) and the
    # scorer crossed them back, which worked only for as long as nobody set them
    # by name.
    eye_width_min_ui: float = 0.4
    eye_height_min_v: float = 0.100

    # reward weights
    w_peaking: float = 1.0
    w_fpeak: float = 1.0
    w_gain: float = 0.0      # see gain_2g5_min_db: not a brief requirement
    w_power: float = 0.5
    w_hd3: float = 0.6
    w_noise: float = 0.6
    w_bias: float = 1.0
    w_area: float = 0.5
    w_eye: float = 1.2      # the actual system-level goal, weighted highest


# How much of the reward comes from HARD pass/fail rather than the soft
# scores. Large enough that crossing a spec boundary beats any soft gain,
# small enough that the soft scores still steer while far from the target.
HARD_PASS_WEIGHT = 0.10


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
    if spec.w_gain > 0:
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
    # A missing eye used to remove eye_h/eye_w from the scorecard entirely, so
    # all_pass() returned True over the remaining seven specs -- a design could
    # be declared compliant by deleting the two hardest requirements. The eye
    # sim genuinely can fail (convergence, timeout, an OOM-killed worker), so
    # this was reachable. Now: absent eye => scored 0 and marked FAILED, unless
    # the caller explicitly said it was not measuring one.
    has_eye = (m.eye_height_v == m.eye_height_v) and (m.eye_width_ui == m.eye_width_ui)
    score_eye = has_eye or spec.require_eye
    if score_eye:
        # _above() already maps NaN -> 0.0, so a missing eye scores zero.
        s["eye_h"] = _above(m.eye_height_v, spec.eye_height_min_v, soft=0.05)
        s["eye_w"] = _above(m.eye_width_ui, spec.eye_width_min_ui, soft=0.15)
    # `saturated` now requires the REAL tail device to be in saturation too, not
    # just the input pair -- an ideal current sink could never fail this.
    s["bias"] = 1.0 if m.saturated else 0.15

    w = spec
    weighted = (
        w.w_peaking * s["peaking"] + w.w_fpeak * s["fpeak"] +
        w.w_power * s["power"] + w.w_hd3 * s["hd3"] +
        w.w_noise * s["noise"] + w.w_bias * s["bias"] + w.w_area * s["area"]
    )
    wsum = (w.w_peaking + w.w_fpeak + w.w_power + w.w_hd3 +
            w.w_noise + w.w_bias + w.w_area)
    if w.w_gain > 0:
        weighted += w.w_gain * s["gain2g5"]
        wsum += w.w_gain
    # NOTE: wsum grows when the eye is present, so an eye-scored reward and a
    # non-eye-scored one are NOT on the same scale. Any search must therefore
    # evaluate every candidate the same way -- see env.Objective(do_eye=...),
    # which applies one setting to the whole population.
    if score_eye:
        weighted += w.w_eye * (s["eye_h"] + s["eye_w"])
        wsum += 2.0 * w.w_eye
    passd = {
        "peaking": spec.peaking_min_db <= m.peaking_db <= spec.peaking_max_db,
        "fpeak": spec.fpeak_lo <= m.f_peak <= spec.fpeak_hi,
        "power": m.power_w <= spec.power_max_w,
        "hd3": (m.hd3_db == m.hd3_db) and m.hd3_db <= spec.hd3_max_db,
        "noise": (m.vn_in_rms == m.vn_in_rms) and m.vn_in_rms <= spec.vn_in_max_rms,
        "area": (area_mm2 == area_mm2) and area_mm2 <= spec.area_max_m2 * 1e6,
        "bias": bool(m.saturated),
    }
    if spec.w_gain > 0:
        passd["gain2g5"] = m.gain_2g5_db >= spec.gain_2g5_min_db
    if score_eye:
        # `NaN >= x` is False, so a missing eye fails here rather than vanishing
        passd["eye_h"] = bool(m.eye_height_v >= spec.eye_height_min_v)
        passd["eye_w"] = bool(m.eye_width_ui >= spec.eye_width_min_ui)
    reward = weighted / wsum

    # HARD-PASS BONUS. The soft scores above give the search a gradient when it
    # is far from a spec, but they SATURATE at the boundary: a design 10 MHz
    # over the 2.5 GHz f_peak limit scores 0.99972 against 1.00000 for one that
    # passes, and an eye 1 mV short scores 0.99960. That difference is far
    # below the noise of a population sample, so the optimiser cannot prefer a
    # PASSING design over a marginally failing one -- measured, a run stalled
    # at reward 0.998 for three iterations with one spec still failing and no
    # gradient to fix it.
    #
    # Mixing in the fraction of HARD specs met puts a real step at each
    # boundary (1/n_specs * HARD_PASS_WEIGHT, ~1.1% for 9 specs) instead of
    # ~0.03%, so crossing the line is worth more than any soft improvement.
    n_pass = sum(1 for v in passd.values() if v)
    reward = ((1.0 - HARD_PASS_WEIGHT) * reward
              + HARD_PASS_WEIGHT * n_pass / max(1, len(passd)))
    # gate: if bias invalid, cap reward low (invalid operating point)
    if not m.saturated:
        reward = min(reward, 0.2)

    return {"reward": float(reward), "scores": s, "pass": passd, "metrics": m}


def margins(m: Metrics, spec: EqualizerSpec) -> dict:
    """Fractional margin to the NEAREST limit for each spec.

    For a two-sided spec this is the distance to whichever edge is closer --
    which is the one that actually binds. Reporting the comfortable edge
    flatters: the final design's worst f_peak is 1.887 GHz, quoted against the
    2.5 GHz ceiling (0.613 GHz of room) when the binding edge is the 1.25 GHz
    floor, and the worst corner sits 1.415 GHz -- only 0.165 GHz above it,
    3.7x tighter. Negative means the spec is violated.
    """
    def two_sided(x, lo, hi):
        if x != x:
            return float("nan")
        return min(x - lo, hi - x) / (hi - lo)

    def upper(x, lim, ref):
        return (lim - x) / abs(ref) if x == x else float("nan")

    def lower(x, lim, ref):
        return (x - lim) / abs(ref) if x == x else float("nan")

    out = {
        "peaking": two_sided(m.peaking_db, spec.peaking_min_db, spec.peaking_max_db),
        "fpeak": two_sided(m.f_peak, spec.fpeak_lo, spec.fpeak_hi),
        "hd3": upper(m.hd3_db, spec.hd3_max_db, spec.hd3_max_db),
        "noise": upper(m.vn_in_rms, spec.vn_in_max_rms, spec.vn_in_max_rms),
        "power": upper(m.power_w, spec.power_max_w, spec.power_max_w),
        "area": upper(m.area_mm2, spec.area_max_m2 * 1e6, spec.area_max_m2 * 1e6),
        "eye_h": lower(m.eye_height_v, spec.eye_height_min_v, spec.eye_height_min_v),
        "eye_w": lower(m.eye_width_ui, spec.eye_width_min_ui, spec.eye_width_min_ui),
    }
    return out


def binding_spec(m: Metrics, spec: EqualizerSpec) -> tuple:
    """(name, fractional margin) of the spec closest to failing."""
    mg = {k: v for k, v in margins(m, spec).items() if v == v}
    if not mg:
        return ("none", float("nan"))
    k = min(mg, key=lambda x: mg[x])
    return (k, mg[k])


# Specs that come from the brief. `bias` and `summer_saturated` are INTERNAL
# sanity checks -- "are the transistors actually switched on" -- not customer
# requirements, so "9 specs pass" is really 8 brief specs plus one self-check.
BRIEF_SPECS = ("peaking", "fpeak", "hd3", "noise", "power", "area",
               "eye_h", "eye_w")
INTERNAL_CHECKS = ("bias",)


def all_pass(scored: dict) -> bool:
    return bool(scored.get("pass")) and all(scored["pass"].values())
