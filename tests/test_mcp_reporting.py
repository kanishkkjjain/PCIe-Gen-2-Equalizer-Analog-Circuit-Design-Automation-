"""The reporting layer, tested where it can misstate a verified result.

An independent tester probed the server and found ten ways the answer could say
something untrue while nothing errored. The engineering underneath was sound --
the 20-corner verification really happens -- but the layer that DESCRIBES the
result could contradict it, and a silent misstatement is worse than a crash.

Every test here was written BEFORE its fix and seen to fail. That order is not
ceremony: this project already shipped a test that passed while the behaviour
it pinned was broken, so a green test that could never have caught its bug is
treated as no test at all.

Pure tier: no PDK, no simulator.
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
from eqrl import mcp_server as impl


def _pvt(failing=(), n_corners=20):
    """A fake 20-corner verification result, failing exactly `failing`."""
    keys = list(sp.BRIEF_SPECS) + list(sp.INTERNAL_CHECKS)
    margins = {k: (-0.01 if k in failing else 0.5) for k in keys}
    return {"all_pass_everywhere": not failing,
            "brief_specs_passed": sum(1 for k in sp.BRIEF_SPECS
                                      if k not in failing),
            "failed": sorted(failing),
            "binding": (sorted(failing)[0] if failing else "hd3",
                        -0.01 if failing else 0.04),
            "margins": margins, "n_corners": n_corners, "launches": 40}


# --------------------------------------------------------------------------
# finding 2 -- three counts over three different sets
# --------------------------------------------------------------------------
def test_a_design_failing_only_an_internal_check_is_not_shown_as_passing():
    """The reported contradiction, reproduced.

    `all_pass` spans ten conditions, `brief_specs_passed` counts eight, and the
    scorecard rendered eight rows -- so a design failing `bias` displayed as
    "DOES NOT PASS -- 8/8 at all 20 PVT corners" with eight rows all reading
    pass. The header said one thing, the body another, and a reader who skims
    sees 8/8 and eight green rows.
    """
    spec = sp.EqualizerSpec()
    rows = impl._scorecard(_pvt(failing=("bias",)), spec)

    shown = {r["spec"] for r in rows}
    assert "bias" in shown, (
        "the scorecard hides the condition that failed: "
        f"it shows {sorted(shown)}")
    assert len(rows) == len(sp.BRIEF_SPECS) + len(sp.INTERNAL_CHECKS)
    bias = next(r for r in rows if r["spec"] == "bias")
    assert bias["pass"] is False
    assert any(r.get("internal") for r in rows), (
        "internal checks are not distinguished from brief specs")


def test_every_condition_all_pass_depends_on_appears_in_the_scorecard():
    """Whatever `all_pass` is computed over must be visible. A condition that
    can fail the design but never appears is one nobody can act on."""
    spec = sp.EqualizerSpec()
    rows = impl._scorecard(_pvt(), spec)
    shown = {r["spec"] for r in rows}
    for k in list(sp.BRIEF_SPECS) + list(sp.INTERNAL_CHECKS):
        assert k in shown, f"{k} decides all_pass but is never displayed"


def test_the_headline_states_both_counts_so_they_cannot_be_confused():
    """"8/8" is true of the brief specs and misleading about the design."""
    spec = sp.EqualizerSpec()
    line = impl._specs_passed_line(_pvt(failing=("bias",)), spec)
    assert "8/8" in line and "10" in line, line
    assert "internal" in line.lower(), line


# --------------------------------------------------------------------------
# finding 3 -- requirements must never be rounded
# --------------------------------------------------------------------------
def test_a_requirement_window_is_never_rounded_in_any_rendering():
    """The AC plot said "need 6-10" where the real window was 5.5 - 10.5, while
    the scorecard beside it said 5.5 - 10.5. A design at 5.7 dB passes and the
    picture says it needed 6 -- and pictures are what people trust."""
    spec = impl.build_spec(peaking_db=8)          # -> 5.5 .. 10.5
    assert spec.peaking_min_db == pytest.approx(5.5)
    assert spec.peaking_max_db == pytest.approx(10.5)

    title = impl._peaking_requirement_text(spec)
    assert "5.5" in title and "10.5" in title, title
    assert "6-10" not in title.replace(" ", ""), (
        f"the peaking window was rounded to a different window: {title}")


def test_a_tiny_requirement_does_not_render_as_zero():
    """`:.2f` turned a 0.001 mVrms request into "< 0.00 mVrms" -- a stated
    requirement of zero, which no design can meet and none was asked to."""
    spec = impl.build_spec(noise_max_mvrms=0.001)
    rows = {r["spec"]: r for r in impl._scorecard(_pvt(), spec)}
    txt = rows["noise"]["required"]
    assert "0.00 " not in txt and not txt.endswith("0.00"), txt
    assert "0.001" in txt, txt


# --------------------------------------------------------------------------
# finding 4 -- hidden defaults
# --------------------------------------------------------------------------
def test_the_response_says_which_limits_were_asked_for_and_which_defaulted():
    """Eight optional parameters silently fill from the brief, so a request for
    "8 dB under 10 mW" carries six constraints nobody chose. The tester proved
    it black-box: one argument gave byte-identical output to none."""
    asked = impl.check_spec(power_max_mw=10)
    empty = impl.check_spec()
    assert asked != empty, (
        "a request with a constraint is indistinguishable from one without")

    applied = asked["applied_spec"]
    keys = {r["limit"] for r in applied}
    for k in ("peaking", "fpeak", "hd3", "noise", "power", "area",
              "eye_h", "eye_w"):
        assert k in keys, f"{k} is applied but not disclosed"
    power = next(r for r in applied if r["limit"] == "power")
    assert power["source"] == "asked", power
    assert next(r for r in applied if r["limit"] == "hd3")["source"] == "default"


def test_applied_spec_reports_in_the_units_the_caller_used():
    """The request says power_max_mw; echoing power_max_w invites the model to
    relay 0.0001 against a limit the user typed as 10."""
    applied = impl.check_spec(power_max_mw=10)["applied_spec"]
    power = next(r for r in applied if r["limit"] == "power")
    assert "mW" in power["value"], power
    assert "10" in power["value"], power


# --------------------------------------------------------------------------
# finding 5 -- input validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kw,why", [
    ({"power_max_mw": -5}, "negative power"),
    ({"eye_height_min_mv": -50}, "negative eye height"),
    ({"eye_width_min_ui": 1.5}, "an eye wider than the unit interval"),
    ({"noise_max_mvrms": -3}, "negative noise"),
    ({"hd3_max_db": 30}, "the hd3 sign slip, which makes the ask look easier"),
])
def test_physically_impossible_inputs_are_refused(kw, why):
    """All five were accepted and sent to a real simulation. The hd3 one is the
    worst: +30 instead of -30 reported MORE matching designs than no constraint
    at all, so the typo made the request look easier than asking for nothing."""
    with pytest.raises(ValueError):
        impl.build_spec(**kw)


def test_the_peaking_error_names_the_legal_band():
    """"peaking window is empty" describes a window the caller never passed --
    they passed a scalar. The message has to say what IS allowed."""
    with pytest.raises(ValueError) as e:
        impl.build_spec(peaking_db=99)
    msg = str(e.value)
    assert "peaking" in msg.lower()
    assert "0.5" in msg and "14.5" in msg, msg


# --------------------------------------------------------------------------
# finding 6 -- noise and area were never checked at all
# --------------------------------------------------------------------------
def test_an_unachievable_noise_limit_is_not_called_in_range():
    """1 nV rms came back `in_trained_envelope: true` and went to the policy.
    The check covers seven fields and noise is not one of them -- so the
    mechanism whose whole job is catching unreasonable requests skipped the
    parameter that made this one unreasonable.

    There is no TRAINED range for noise, so this is checked against what has
    ever been measured: never below 0.159 mVrms in 5 993 records.
    """
    r = impl.check_spec(noise_max_mvrms=0.001)
    assert r["in_trained_envelope"] is not True, (
        "a physically unachievable noise limit was reported as in range")
    blob = str(r)
    assert "noise" in blob.lower()


def test_noise_and_area_are_disclosed_as_outside_the_policys_scope():
    """Reporting them as "checked" would be a second untruth. They are outside
    the goal space entirely, and the response should say so."""
    r = impl.check_spec(noise_max_mvrms=1.0)
    assert "unchecked_by_envelope" in r or "achievable" in str(r).lower()


# --------------------------------------------------------------------------
# finding 7 -- a time budget that can never look like a pass
# --------------------------------------------------------------------------
def test_a_budget_result_is_marked_and_never_reports_a_pass():
    """Measured 421 s against a documented "about 45 s". A run that stops early
    must return what it has, clearly incomplete -- never a pass it did not
    establish at 20 corners."""
    out = impl._budget_result(best=None, attempts=[], elapsed=1.0, budget=1.0,
                              env={"in_envelope": True, "outside": {}})
    assert out["budget_exceeded"] is True
    assert out["all_pass"] is False


# --------------------------------------------------------------------------
# the dress rehearsal's finding -- a FAIL that never happened
# --------------------------------------------------------------------------
def _real_pvt(failed=()):
    """What `deploy_policy.verify` ACTUALLY returns, which is not what `_pvt`
    above pretends.

    `margins` comes from `design._worst_margins`, which calls `sp.margins` --
    the EIGHT brief specs. `bias` and `equalises` are not in it. The pass state
    for all ten lives in `failed`, aggregated from each corner's `score()["pass"]`
    dict, and that is also what `all_pass_everywhere` is computed from.

    The first fixture in this file invented margins for all ten, so every test
    above agreed with the renderer about a dict the simulator never produces.
    """
    return {"all_pass_everywhere": not failed,
            "brief_specs_passed": sum(1 for k in sp.BRIEF_SPECS
                                      if k not in failed),
            "failed": sorted(failed),
            "binding": ["hd3", 0.04],
            "margins": {k: 0.5 for k in sp.BRIEF_SPECS},   # eight, not ten
            "n_corners": 20, "launches": 40}


def test_an_internal_check_that_passed_is_not_rendered_as_a_failure():
    """Caught by driving one real design through the MCP client.

    The design passed -- `all_pass` true, `failed` empty, 20 corners -- and the
    scorecard printed:

        bias       devices biased in saturation    n/a  FAIL
        equalises  eye openness improved ...       n/a  FAIL

    beside "8/10 including internal checks". Nothing errored; the answer was
    simply wrong about two of its own ten rows, in the direction that makes a
    working circuit look broken. The cause is that a missing margin was read as
    a failed margin, and `_worst_margins` never records these two.
    """
    rows = {r["spec"]: r for r in impl._scorecard(_real_pvt(), sp.EqualizerSpec())}
    for k in sp.INTERNAL_CHECKS:
        assert rows[k]["pass"] is True, (
            f"{k} passed at all 20 corners but the scorecard says it failed")
        assert rows[k]["margin"] is None, (
            f"{k} has no recorded margin; inventing one would be worse")


def test_the_counts_agree_with_all_pass_on_a_real_verification():
    """The header said 8/10 while `all_pass` said True. Whatever else is true,
    those two cannot disagree: they describe the same ten conditions."""
    spec = sp.EqualizerSpec()
    pvt = _real_pvt()
    line = impl._specs_passed_line(pvt, spec)
    assert "10/10" in line, line
    rows = impl._scorecard(pvt, spec)
    assert all(r["pass"] for r in rows) is pvt["all_pass_everywhere"]


def test_a_real_internal_failure_still_shows_as_a_failure():
    """The fix must not become "absent means pass" -- that is the original bug
    with the sign flipped. When `failed` names it, the row says FAIL."""
    rows = {r["spec"]: r
            for r in impl._scorecard(_real_pvt(failed=("bias",)),
                                     sp.EqualizerSpec())}
    assert rows["bias"]["pass"] is False
    assert rows["equalises"]["pass"] is True
    assert "9/10" in impl._specs_passed_line(_real_pvt(failed=("bias",)),
                                             sp.EqualizerSpec())


def test_a_budget_result_renders_instead_of_crashing_the_tool_call():
    """`_budget_result` returns `ok: True` with a best-effort design, but none
    of `specs_passed`, `method` or `eye_measured` -- all of which `_render`
    indexes directly. So the one path that exists to degrade gracefully raised
    KeyError and took the whole tool call down, which is the opposite of
    degrading gracefully.

    The default budget is 150 s and a normal run takes about 50, so this only
    fires on a hard specification -- exactly the one a demo gets asked for.
    """
    import run_mcp_server as srv

    pvt = _real_pvt(failed=("eye_w",))
    out = impl._budget_result(
        best={"u": [0.5] * 11, "pvt": pvt}, attempts=[{"method": "policy"}],
        elapsed=151.0, budget=150.0,
        env={"in_envelope": True, "outside": {}})
    out["scorecard"] = impl._scorecard(pvt, sp.EqualizerSpec())
    out["specs_passed"] = impl._specs_passed_line(pvt, sp.EqualizerSpec())
    out["sized_parameters"] = {"W": 28.0, "L": 0.15}
    out["failed_specs"] = pvt["failed"]

    text = srv._render(out)                 # must not raise
    assert "DOES NOT PASS" in text, text
    assert "PASSES --" not in text, text
    assert "eye_w" in text
