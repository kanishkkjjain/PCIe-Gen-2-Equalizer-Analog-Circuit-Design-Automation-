"""The MCP server, tested where it can lie.

An LLM wrapper is where a project's careful measurements get rounded up into
claims: the model sees a dict, and whatever the dict implies is what the human
is told. So the tests here are not about the protocol -- they are about the
handful of places this layer could overstate what was measured.

Pure tier: no PDK, no ngspice, no MCP SDK required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eqrl import specs as sp
from eqrl.mcp_server import (build_spec, check_spec, envelope_check,
                             explain_capabilities, _scorecard)


# --------------------------------------------------------------------------
# turning a request into a spec
# --------------------------------------------------------------------------
def test_units_are_converted_not_assumed():
    """"power_max=10" is ambiguous and a model filling it in will guess.

    The arguments name their units, and this pins that they are honoured --
    a spec silently 1000x out is the kind of error that produces a confident,
    wrong answer rather than a visible failure.
    """
    s = build_spec(power_max_mw=10, noise_max_mvrms=1.2,
                   eye_height_min_mv=120, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2)
    assert s.power_max_w == pytest.approx(10e-3)
    assert s.vn_in_max_rms == pytest.approx(1.2e-3)
    assert s.eye_height_min_v == pytest.approx(0.120)
    assert s.fpeak_lo == pytest.approx(1.4e9)
    assert s.fpeak_hi == pytest.approx(2.2e9)


def test_unspecified_fields_keep_the_brief():
    """A caller who only cares about power must not silently relax hd3."""
    base, asked = sp.EqualizerSpec(), build_spec(power_max_mw=8)
    for f in ("hd3_max_db", "vn_in_max_rms", "eye_width_min_ui",
              "area_max_m2", "nyquist"):
        assert getattr(asked, f) == getattr(base, f), f
    assert asked.power_max_w == pytest.approx(8e-3)


@pytest.mark.parametrize("kw", [
    {"fpeak_lo_ghz": 2.5, "fpeak_hi_ghz": 1.5},     # inverted window
    {"fpeak_lo_ghz": 2.0, "fpeak_hi_ghz": 2.0},     # empty window
])
def test_an_impossible_window_is_refused_not_silently_fixed(kw):
    """Quietly swapping the bounds would answer a question nobody asked."""
    with pytest.raises(ValueError):
        build_spec(**kw)


# --------------------------------------------------------------------------
# the envelope: the claim that must never be overstated
# --------------------------------------------------------------------------
def test_a_request_inside_the_trained_range_is_recognised():
    s = build_spec(peaking_db=8, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2,
                   power_max_mw=10)
    assert envelope_check(s)["in_envelope"] is True


def test_a_request_outside_the_trained_range_is_flagged_with_the_reason():
    """The policy answers outside its training range -- it just does not carry
    the measured 56.2 % reliability there. Presenting that answer as a normal
    one is the failure; the server must name which fields are outside."""
    s = build_spec(peaking_db=11.5, eye_height_min_mv=150)
    env = envelope_check(s)
    assert env["in_envelope"] is False
    assert "peaking_target_db" in env["outside"]
    assert "eye_height_min_v" in env["outside"]
    for field, info in env["outside"].items():
        assert "asked" in info and "trained_range" in info, field


def test_out_of_envelope_selects_the_search_and_says_so():
    r = check_spec(peaking_db=11.5)
    assert r["in_trained_envelope"] is False
    assert "CEM" in r["method_that_would_be_used"]
    # and it must not read as "impossible" -- it is not
    assert "impossible" in r["note"]


def test_in_envelope_selects_the_policy_with_the_search_as_fallback():
    r = check_spec(peaking_db=8, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2)
    assert r["in_trained_envelope"] is True
    assert "policy" in r["method_that_would_be_used"]
    assert "fallback" in r["method_that_would_be_used"]


def test_the_known_design_check_states_its_own_limitation():
    """It reads records measured at 4 corners while acceptance needs 20, so it
    is necessary-not-sufficient. A caller told "2 designs meet it" without that
    caveat will report a pass that has not been established."""
    r = check_spec(peaking_db=8, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2)
    known = r["known_designs_meeting_it"]
    assert "note" in known
    assert "not all 20" in known["note"] or "no evaluation cache" in known["note"]


# --------------------------------------------------------------------------
# the scorecard: margins are reported as measured
# --------------------------------------------------------------------------
def test_a_failing_spec_is_reported_as_failing_however_close():
    """A spec missed by 0.1 % is missed. Rounding it to a pass is the single
    most tempting error in this layer."""
    spec = sp.EqualizerSpec()
    pvt = {"margins": {k: 0.5 for k in sp.BRIEF_SPECS}}
    pvt["margins"]["eye_w"] = -0.001
    rows = {r["spec"]: r for r in _scorecard(pvt, spec)}
    assert rows["eye_w"]["pass"] is False
    assert rows["eye_w"]["margin"] == pytest.approx(-0.001)
    assert rows["peaking"]["pass"] is True


def test_an_unmeasured_spec_is_not_a_pass():
    """NaN means "not measured", which is not the same as "met"."""
    spec = sp.EqualizerSpec()
    pvt = {"margins": dict.fromkeys(sp.BRIEF_SPECS, 0.5)}
    pvt["margins"]["hd3"] = float("nan")
    rows = {r["spec"]: r for r in _scorecard(pvt, spec)}
    assert rows["hd3"]["pass"] is False
    assert rows["hd3"]["margin"] is None


def test_the_scorecard_covers_every_condition_not_just_the_brief_ones():
    """A condition missing from the scorecard is one nobody can act on.

    This test used to assert the scorecard showed EXACTLY the eight brief
    specs, which is what the bug looked like from the inside: a design failing
    the internal `bias` check rendered eight passing rows and no sign of the
    failure. The assertion now covers everything `all_pass` depends on.
    """
    spec = sp.EqualizerSpec()
    keys = list(sp.BRIEF_SPECS) + list(sp.INTERNAL_CHECKS)
    rows = _scorecard({"margins": dict.fromkeys(keys, 0.1)}, spec)
    assert {r["spec"] for r in rows} == set(keys)
    for r in rows:
        assert r["required"], f"{r['spec']} has no stated limit"
    assert sum(1 for r in rows if r["internal"]) == len(sp.INTERNAL_CHECKS)


def test_the_stated_limits_track_the_spec_not_the_defaults():
    """The scorecard prints what was ASKED for. Printing the brief's default
    while the user asked for something tighter would be a scorecard against
    the wrong question."""
    spec = build_spec(power_max_mw=7, eye_width_min_ui=0.45)
    keys = list(sp.BRIEF_SPECS) + list(sp.INTERNAL_CHECKS)
    rows = {r["spec"]: r for r in
            _scorecard({"margins": dict.fromkeys(keys, 0.1)}, spec)}
    # the VALUE must be right; its formatting is `_fmt`'s business, and the
    # rule `_fmt` enforces is that a bound never moves when printed
    assert "7" in rows["power"]["required"] and "mW" in rows["power"]["required"]
    assert "0.45" in rows["eye_w"]["required"]


# --------------------------------------------------------------------------
# what the model is told about the tool itself
# --------------------------------------------------------------------------
def test_capabilities_state_the_limits_not_only_the_wins():
    """This text is what the model repeats when asked what the tool can do. If
    it lists only achievements, the model will imply the rest works too."""
    t = explain_capabilities()
    assert "80 %" in t                      # neither method solves everything
    assert "trained on a bounded range" in t or "bounded range" in t
    assert "0.57" in t                      # eye width is not predicted well
    assert "all 20" in t                    # a pass means all 20 corners


def test_the_spawn_guard_refuses_rather_than_consuming_the_machine():
    """Unguarded, a spawned worker re-imports the caller and re-runs the whole
    design -- measured, that reached 45 concurrent ngspice processes and was
    still growing three minutes later, having produced nothing. The existing
    warning in env.py falls back to fork, which deadlocks instead."""
    import eqrl.mcp_server as impl

    class _NoFile:
        pass

    real = sys.modules["__main__"]
    sys.modules["__main__"] = _NoFile()
    try:
        with pytest.raises(RuntimeError) as e:
            impl._require_spawn_safe_caller()
        msg = str(e.value)
        assert "__main__" in msg and "jobs=1" in msg      # names the fix
    finally:
        sys.modules["__main__"] = real
