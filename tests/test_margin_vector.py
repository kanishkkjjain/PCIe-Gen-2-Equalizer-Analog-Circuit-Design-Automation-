"""The objective checkpoint 6 is built on, checked as a PROPERTY.

Checkpoint 5 shipped a test that asserted a literal call string and passed
happily while the behaviour it was supposed to pin was wrong. So nothing here
inspects how the reward is written; everything here constructs a situation and
asserts what the number must do in it.

The claim being defended is one line:

    all_pass(score(m, spec))  <==>  min(margin_vector(m, spec)) >= 0

and its consequence: there is no trade. Improving any nine conditions while
breaking the tenth must LOWER the score, because that is exactly the move the
old reward paid for and the reason the old policy solved nothing.

Pure tier -- no PDK, no ngspice. The dataset checks read the committed cache.
"""
from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from eqrl import specs as sp
from eqrl.spice import Metrics

ROOT = Path(__file__).resolve().parents[1]


def _m(**kw) -> Metrics:
    """A metrics object that comfortably passes the default spec."""
    base = dict(ok=True, peaking_db=7.0, f_peak=1.8e9, dc_gain_db=0.0, gain_nyq_db=3.0,
                hd3_db=-35.0, vn_in_rms=0.8e-3, power_w=6e-3, area_mm2=0.001,
                eye_height_v=0.15, eye_width_ui=0.6, eye_improvement=1.5,
                saturated=True, channel_present=True)
    base.update(kw)
    return Metrics(**{k: v for k, v in base.items()
                      if k in Metrics.__dataclass_fields__})


# --------------------------------------------------------------------------
# 1. the equivalence, on constructed cases and then on every real record
# --------------------------------------------------------------------------
def test_passing_design_has_non_negative_min_margin():
    spec = sp.EqualizerSpec()
    m = _m()
    assert sp.all_pass(sp.score(m, spec))
    assert sp.min_margin(m, spec) >= 0


@pytest.mark.parametrize("broken", [
    {"peaking_db": 0.5},            # below the band
    {"peaking_db": 30.0},           # above it
    {"f_peak": 0.5e9},
    {"f_peak": 4.0e9},
    {"hd3_db": -5.0},
    {"vn_in_rms": 50e-3},
    {"power_w": 1.0},
    {"eye_height_v": 0.001},
    {"eye_width_ui": 0.01},
    {"saturated": False},           # INTERNAL: the bias gate
    {"eye_improvement": 0.5},       # INTERNAL: the equalises gate
])
def test_every_single_violation_drives_the_minimum_negative(broken):
    """Including the two internal gates.

    These are the ones that matter most. `margins()` does not cover them, so a
    reward built on `margins()` alone lets the policy trade away saturation
    and equalisation for nothing -- and those are precisely the two conditions
    `score` clips the reward to 0.2 and 0.3 for.
    """
    spec = sp.EqualizerSpec()
    m = _m(**broken)
    assert not sp.all_pass(sp.score(m, spec)), "the fixture no longer breaks it"
    assert sp.min_margin(m, spec) < 0, broken


def test_equivalence_holds_on_every_collected_record():
    """Every real SPICE measurement in the cache, not constructed ones.

    Constructed cases only prove the branches the author thought of. This runs
    the two functions over everything the project has ever measured and
    demands they never disagree.
    """
    from eqrl import cache
    recs = cache.load(["collect", "goalstart", "goalstart2"])
    if len(recs) < 500:
        pytest.skip("evaluation cache not present")
    spec = sp.EqualizerSpec()
    n = agree = 0
    for r in recs.records.values():
        md = r.get("metrics") or {}
        if not md.get("ok", True):
            continue
        m = Metrics(**{k: v for k, v in md.items()
                       if k in Metrics.__dataclass_fields__})
        n += 1
        agree += (sp.all_pass(sp.score(m, spec))
                  == (sp.min_margin(m, spec) >= 0))
    assert n > 500
    assert agree == n, f"{n - agree} of {n} records disagree"


def test_a_failed_simulation_cannot_pass():
    """`score` short-circuits `ok=False` to an empty pass dict, which
    `all_pass` reads as False. An empty margin vector would make `min()`
    raise; a full one would invent margins for numbers that do not exist."""
    spec = sp.EqualizerSpec()
    m = _m(ok=False)
    assert not sp.all_pass(sp.score(m, spec))
    assert sp.min_margin(m, spec) < 0


def test_nan_is_a_failure_not_an_absence():
    """A missing eye is a design that has not been shown to work.

    Returning NaN here would let `min()` skip it silently, which is how a
    design once got declared compliant by deleting the two hardest specs.
    """
    spec = sp.EqualizerSpec()
    v = sp.margin_vector(_m(eye_height_v=float("nan")), spec)
    assert all(math.isfinite(x) or x == float("inf") for x in v.values())
    assert sp.min_margin(_m(eye_height_v=float("nan")), spec) < 0


def test_key_set_tracks_the_scorer_not_merely_overlaps_it():
    """A condition `score` checks but the vector omits is one the optimiser
    may violate for free; one the vector adds can veto a passing design."""
    spec = sp.EqualizerSpec()
    m = _m()
    scored_keys = set(sp.score(m, spec)["pass"])
    assert set(sp.margin_vector(m, spec)) == scored_keys

    # and when the caller opts out of the eye, both sides drop it together
    no_eye = dataclasses.replace(spec, require_eye=False)
    m2 = _m(eye_height_v=float("nan"), eye_width_ui=float("nan"))
    assert set(sp.margin_vector(m2, no_eye)) == set(sp.score(m2, no_eye)["pass"])

    # gain_nyq is opt-in via w_gain and must appear on both sides together --
    # it is this project's cautionary tale about inventing a requirement that
    # then silently vetoes designs, so it must not veto silently here either
    with_gain = dataclasses.replace(spec, w_gain=1.0)
    assert set(sp.margin_vector(m, with_gain)) == set(
        sp.score(m, with_gain)["pass"])
    weak = _m(gain_nyq_db=-50.0)
    assert not sp.all_pass(sp.score(weak, with_gain))
    assert sp.min_margin(weak, with_gain) < 0


# --------------------------------------------------------------------------
# 2. the thing the old reward got wrong: there must be no profitable trade
# --------------------------------------------------------------------------
def test_the_old_reward_pays_for_a_trade_and_the_new_one_does_not():
    """The defect, demonstrated rather than described.

    `score` mixes in `HARD_PASS_WEIGHT * n_pass / n`. Take a design failing
    several conditions, fix most of them, and break one that was passing: the
    old reward goes UP, because it counts. The minimum goes DOWN, because it
    cannot.
    """
    spec = sp.EqualizerSpec()
    before = _m(peaking_db=3.0, hd3_db=-20.0, vn_in_rms=2.0e-3,
                power_w=13e-3, eye_width_ui=0.30)
    after = _m(peaking_db=7.0, hd3_db=-35.0, vn_in_rms=0.8e-3,
               power_w=6e-3, eye_width_ui=0.60,
               eye_height_v=0.001)              # the one thing sacrificed

    assert sp.score(after, spec)["reward"] > sp.score(before, spec)["reward"], \
        "fixture no longer reproduces the loophole"
    assert sp.min_margin(after, spec) < sp.min_margin(before, spec), \
        "the minimum paid for a trade, which is the whole defect"


def test_only_the_binding_condition_can_raise_the_minimum():
    spec = sp.EqualizerSpec()
    base = _m(eye_height_v=0.062)          # eye_h binds, just barely
    v = sp.margin_vector(base, spec)
    assert min(v, key=v.get) == "eye_h"
    # improving anything else changes nothing
    assert sp.min_margin(_m(eye_height_v=0.062, power_w=2e-3), spec) == \
        pytest.approx(sp.min_margin(base, spec))
    # improving the binding one does
    assert sp.min_margin(_m(eye_height_v=0.12), spec) > sp.min_margin(base, spec)
