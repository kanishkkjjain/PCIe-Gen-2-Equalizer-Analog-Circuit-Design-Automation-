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
    gain_nyq_min_db: float = 0.0
    # hard constraints
    hd3_max_db: float = -30.0
    vn_in_max_rms: float = 1.5e-3
    power_max_w: float = 15e-3
    area_max_m2: float = 0.05e-6   # 0.05 mm^2
    # MEASUREMENT CONDITIONS. These used to be literals inside spice.py, which
    # made `nyquist` above decorative: the eye ran at a hardcoded 200 ps UI
    # whatever the spec said, so asking for 8 Gb/s returned a confident 5 Gb/s
    # answer. They are spec fields now so a different spec is actually honoured.
    hd3_freq: float = 100e6        # brief: "HD3 < -30 dB (100 MHz diff input)"
    hd3_amp_pp_v: float = 0.100    # differential pp at the equaliser input
    noise_f_lo: float = 10e6       # brief: noise integrated 10 MHz - 5 GHz
    noise_f_hi: float = 5e9
    # An equaliser whose eye openness at the slicer is no better than at its own
    # input is amplifying, not equalising. This is an INTERNAL validity check of
    # the same kind as `bias`, deliberately NOT a brief spec -- gain_nyq_min_db
    # is the cautionary tale for inventing one of those.
    eye_improvement_min: float = 1.0
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
    w_gain: float = 0.0      # see gain_nyq_min_db: not a brief requirement
    w_power: float = 0.5
    w_hd3: float = 0.6
    w_noise: float = 0.6
    w_bias: float = 1.0
    w_area: float = 0.5
    w_eye: float = 1.2      # the actual system-level goal, weighted highest

    @property
    def ui_s(self) -> float:
        """Unit interval in seconds.

        NRZ signalling puts the Nyquist frequency at half the baud rate, so
        UI = 1/(2*nyquist). The brief's 2.5 GHz gives exactly 200 ps, which is
        the value that used to be hardcoded in `spice.py::tran_eye`.
        """
        return 1.0 / (2.0 * self.nyquist)


# How much of the reward comes from HARD pass/fail rather than the soft
# scores. Large enough that crossing a spec boundary beats any soft gain,
# small enough that the soft scores still steer while far from the target.
#: Volts of tail-device saturation headroom that count as one unit of margin.
#: Matches the scale `env_goal` already divides by in its observation, so the
#: number the policy SEES and the number it is PAID for are the same quantity.
TAIL_MARGIN_SCALE_V = 0.2
#: What an unmeasurable margin is worth. A NaN eye is a design that has not
#: been shown to work; scoring it as 0 (neutral) or dropping it (invisible to
#: `min`) would both let it pass.
MARGIN_NAN_FLOOR = -1.0

HARD_PASS_WEIGHT = 0.10


# Soft-barrier widths, as a FRACTION of each limit (or of each band's width).
#
# These were absolute constants, which silently broke any spec but the default
# one: with `soft=3e-3` for power, asking for power < 2 mW gives a barrier 1.5x
# wider than the entire limit, so the score is flat exactly where it has to
# steer and the search runs blind until the hard-pass bonus fires.
#
# The fractions below are chosen to reproduce the previous ABSOLUTE values
# exactly at the default spec, so this change is provably a no-op for every
# number already published -- see tests/test_pure.py::
# test_relative_soft_widths_reproduce_the_absolute_ones.
#
#   power 0.20*15e-3 = 3e-3     noise (1/3)*1.5e-3 = 0.5e-3
#   area  0.40*0.05  = 0.02     eye_h 0.50*0.100   = 0.05
#   eye_w 0.375*0.4  = 0.15     fpeak 0.48*1.25e9  = 0.6e9
#   peaking (1/3)*9 = 3.0       peaking-target (4/9)*9 = 4.0
SOFT_FRAC = {
    "power": 0.20,
    "noise": 1.0 / 3.0,
    "area": 0.40,
    "eye_h": 0.50,
    "eye_w": 0.375,
    "fpeak": 0.48,          # of (fpeak_hi - fpeak_lo)
    "peaking": 1.0 / 3.0,   # of (peaking_max_db - peaking_min_db)
    "peaking_target": 4.0 / 9.0,
    "gain": 4.0 / 9.0,      # of the peaking band, same scale as peaking
}

# HD3 keeps an ABSOLUTE width on purpose. It is already a ratio expressed in dB,
# so "20% of -30 dB" is not a meaningful quantity -- a decibel barrier is the
# scale-free one here, and 6 dB is what it has always been.
HD3_SOFT_DB = 6.0


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

    # Soft-barrier widths scale with the spec being asked for (see SOFT_FRAC).
    pk_band = spec.peaking_max_db - spec.peaking_min_db
    fp_band = spec.fpeak_hi - spec.fpeak_lo

    s: dict[str, float] = {}
    # --- shape targets ---
    s["peaking"] = _plateau(m.peaking_db, spec.peaking_min_db,
                            spec.peaking_max_db,
                            soft=SOFT_FRAC["peaking"] * pk_band)
    # extra pull toward the nominal target so it doesn't sit at the edge
    s["peaking"] = 0.5 * s["peaking"] + 0.5 * _target(
        m.peaking_db, spec.peaking_target_db,
        SOFT_FRAC["peaking_target"] * pk_band)
    s["fpeak"] = _plateau(m.f_peak, spec.fpeak_lo, spec.fpeak_hi,
                          soft=SOFT_FRAC["fpeak"] * fp_band)
    if spec.w_gain > 0:
        s["gain_nyq"] = _above(m.gain_nyq_db, spec.gain_nyq_min_db,
                               soft=SOFT_FRAC["gain"] * pk_band)
    # --- hard constraints ---
    s["power"] = _below(m.power_w, spec.power_max_w,
                        soft=SOFT_FRAC["power"] * spec.power_max_w)
    s["hd3"] = _below(m.hd3_db, spec.hd3_max_db, soft=HD3_SOFT_DB)
    s["noise"] = _below(m.vn_in_rms, spec.vn_in_max_rms,
                        soft=SOFT_FRAC["noise"] * spec.vn_in_max_rms)
    # Area only became measurable once ideal R/C were replaced by PDK devices.
    area_mm2 = m.area_mm2 if m.area_mm2 == m.area_mm2 else float("nan")
    s["area"] = _below(area_mm2, spec.area_max_m2 * 1e6,
                       soft=SOFT_FRAC["area"] * spec.area_max_m2 * 1e6)
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
        s["eye_h"] = _above(m.eye_height_v, spec.eye_height_min_v,
                            soft=SOFT_FRAC["eye_h"] * spec.eye_height_min_v)
        s["eye_w"] = _above(m.eye_width_ui, spec.eye_width_min_ui,
                            soft=SOFT_FRAC["eye_w"] * spec.eye_width_min_ui)
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
        weighted += w.w_gain * s["gain_nyq"]
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
        passd["gain_nyq"] = m.gain_nyq_db >= spec.gain_nyq_min_db
    if score_eye:
        # `NaN >= x` is False, so a missing eye fails here rather than vanishing
        passd["eye_h"] = bool(m.eye_height_v >= spec.eye_height_min_v)
        passd["eye_w"] = bool(m.eye_width_ui >= spec.eye_width_min_ui)
    reward = weighted / wsum

    # EQUALISING, or just AMPLIFYING? Eye HEIGHT cannot tell the two apart: the
    # summing stage's gain (Rsum, Wg1, Ig1) is a free optimiser variable, and
    # across 20 corners eye height correlates with that gain at 0.98 and with
    # peaking at only 0.47. Openness -- opening as a fraction of the swing at
    # the same node -- is invariant under that scaling, so improvement < 1 means
    # the circuit made the eye no better than it found it.
    #
    # This is an INTERNAL check, like `bias`, not a brief spec: it is a validity
    # condition for calling the thing an equaliser at all. It is deliberately
    # not added to BRIEF_SPECS -- gain_nyq_min_db is this project's cautionary
    # tale about inventing a requirement that then silently vetoes designs.
    # Only asked when there is ISI to remove. Driven from an ideal PWL source
    # with no channel in front, the input eye is already perfect and openness
    # can only DROP -- improvement < 1 is then the correct physical answer, not
    # a defect, and gating on it would fail every channel-free evaluation
    # (measured: it took the default design's reward from 0.98 to the 0.3 cap).
    equalises = True
    if m.channel_present and m.eye_improvement == m.eye_improvement:
        equalises = m.eye_improvement >= spec.eye_improvement_min
    passd["equalises"] = bool(equalises)

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
    # The gates below CLIP the reward, so two designs that both trip a gate
    # score identically however different they are -- measured, the box centre
    # and the analytic seed both came out at exactly 0.3000, which makes the
    # reward useless for comparing them. `reward_raw` is the ungated value,
    # reported for diagnostics only. THE SEARCH MUST KEEP USING `reward`: the
    # clipping is what stops it exploring invalid regions.
    reward_raw = reward
    # gate: if bias invalid, cap reward low (invalid operating point)
    if not m.saturated:
        reward = min(reward, 0.2)
    # gate: an "equaliser" that does not equalise is not a candidate, however
    # large its eye. Same shape as the bias gate above.
    if not equalises:
        reward = min(reward, 0.3)

    return {"reward": float(reward), "reward_raw": float(reward_raw),
            "scores": s, "pass": passd, "metrics": m}


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


def margin_vector(m: Metrics, spec: EqualizerSpec,
                  tail_margin_v: float | None = None) -> dict:
    """Every pass condition as a continuous margin, so that

        all_pass(score(m, spec))  <==>  min(margin_vector(...)) >= 0

    This is the whole point of the function.  `score` mixes a weighted soft
    score with `HARD_PASS_WEIGHT * n_pass / n`, and `all_pass` is CONJUNCTIVE
    -- so an optimiser can raise that reward by improving five conditions
    while driving a sixth negative, and be paid for it.  Measured over
    checkpoint 5: the PPO policy did exactly that, solving 0 of 20 held-out
    specs while its reward climbed.  A minimum has no such trade: the only way
    to raise it is to improve the condition that is currently worst.

    Ten entries.  The eight brief specs come from `margins()` unchanged.  The
    two INTERNAL checks that `score` gates on must be here too, or the policy
    simply trades THEM away instead:

      bias        tail-device saturation headroom, in volts, normalised by the
                  0.2 V scale `env_goal` already uses in its observation.  It
                  is not derivable from `Metrics` (it needs the fitted device
                  model), so the caller passes it in -- which also keeps this
                  module free of any PDK or physics import.  When it is not
                  supplied, `m.saturated` is used as a coarse +/-1 stand-in.
      equalises   `eye_improvement - eye_improvement_min`, relative.  Only
                  meaningful with a channel in front (see `score`); without
                  one it is not a condition at all and is reported as +inf so
                  it can never bind.

    NaN propagates as a FAILURE, not as an absence: a missing eye measurement
    is a design that has not been shown to work, and returning NaN here would
    let `min()` silently skip it.

    ONE CAVEAT ON THE EQUIVALENCE ABOVE, stated here rather than in a footnote
    because it is the only place the property is not exact. It holds precisely
    when `tail_margin_v` is omitted, since `bias` is then `score`'s own
    `m.saturated` flag -- and that is the form the tests pin, over every record
    in the cache. Pass a fitted `tail_margin_v` and the two can disagree on
    `bias`, because the fit gets the sign right on 89.3 % of records rather
    than all of them. That is a deliberate trade and not a defect: a policy
    needs a continuous signal it can steer by, and a boolean only tells it that
    it has already fallen off. Anything GATING on pass/fail should use
    `all_pass`; anything OPTIMISING should use the minimum.
    """
    # A failed simulation has no margins, and `score` short-circuits it to
    # reward -1.0 with an EMPTY pass dict -- which `all_pass` reads as False
    # (`bool({})`). The vector has to agree, and an empty dict here would make
    # `min()` raise instead.
    if not m.ok:
        return {"ok": MARGIN_NAN_FLOOR}

    out = dict(margins(m, spec))

    # The KEY SET must track `score`'s, not merely overlap with it, or the
    # equivalence above is false in both directions: an entry `score` does not
    # check can veto a passing design, and a condition `score` does check but
    # this omits is one the optimiser is free to violate for nothing.
    if not ((m.eye_height_v == m.eye_height_v)
            and (m.eye_width_ui == m.eye_width_ui)) and not spec.require_eye:
        out.pop("eye_h", None)
        out.pop("eye_w", None)
    if spec.w_gain > 0:
        out["gain_nyq"] = ((m.gain_nyq_db - spec.gain_nyq_min_db)
                           / max(abs(spec.gain_nyq_min_db), 1.0))

    if tail_margin_v is None:
        out["bias"] = 1.0 if m.saturated else -1.0
    else:
        out["bias"] = float(tail_margin_v) / TAIL_MARGIN_SCALE_V

    if m.channel_present and m.eye_improvement == m.eye_improvement:
        ref = abs(spec.eye_improvement_min) or 1.0
        out["equalises"] = (m.eye_improvement - spec.eye_improvement_min) / ref
    else:
        # no channel -> nothing to equalise -> the check does not apply
        out["equalises"] = float("inf")

    # NaN means "not measured", which is not the same as "passed"
    return {k: (v if v == v else MARGIN_NAN_FLOOR) for k, v in out.items()}


def min_margin(m: Metrics, spec: EqualizerSpec,
               tail_margin_v: float | None = None) -> float:
    """The binding margin: >= 0 exactly when every condition is met."""
    return min(margin_vector(m, spec, tail_margin_v).values())


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
INTERNAL_CHECKS = ("bias", "equalises")


def all_pass(scored: dict) -> bool:
    return bool(scored.get("pass")) and all(scored["pass"].values())
