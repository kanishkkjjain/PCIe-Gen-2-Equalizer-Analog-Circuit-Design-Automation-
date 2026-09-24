"""The starting-point seam: which derivation runs, and that everyone agrees.

`eqrl/start.py` exists to fix one specific bug. `start_physfit.patch()`
rebinds module globals, and it reaches `deploy_policy` through
`sys.modules.get(...)` -- a module the MCP server imports *inside*
`design_equalizer`, after any import-time patch has already run. The patch
therefore lands on the policy path and misses the CEM path, and the two
searches start from different designs.

That is silent. Nothing raises, nothing logs, and the result still looks like a
verified design. So the test that matters here is not "does physfit produce a
start" -- it is "do BOTH proposers resolve to the same one".

Pure tier: no PDK, no ngspice, no MCP SDK.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eqrl import start


@pytest.fixture(autouse=True)
def _restore_default():
    """No test may leak its choice into the next one."""
    yield
    start.reset()


# --------------------------------------------------------------------------
# selecting a derivation
# --------------------------------------------------------------------------
def test_the_default_is_the_derivation_the_policy_was_trained_against():
    """Training, data collection and every committed measurement used the
    physics derivation. If the default ever flips, they all quietly change
    meaning without a single line of their own being edited."""
    assert start.current() == "physics"


def test_switching_takes_effect_and_reports_itself():
    assert start.use("physfit") == "physfit"
    assert start.current() == "physfit"
    assert start.reset() == "physics"


def test_an_unknown_name_raises_rather_than_falling_back():
    """A silent fallback here means a run REPORTS one derivation and USES
    another, which is worse than not starting at all."""
    with pytest.raises(ValueError, match="unknown starting point"):
        start.use("phsyfit")            # the typo a human actually makes
    assert start.current() == "physics"


# --------------------------------------------------------------------------
# the bug the seam exists to prevent
# --------------------------------------------------------------------------
def test_both_proposers_resolve_to_the_same_derivation():
    """The policy path reaches the start through `env_goal`; the CEM path
    reaches it through `deploy_policy.start_for`. They MUST agree -- CEM and
    the policy beginning from different designs makes the two methods
    incomparable, and nothing in the result would show it.
    """
    import eqrl.env_goal as eg
    import deploy_policy as dp

    for name in ("physics", "physfit"):
        start.use(name)
        assert eg._start is dp._start, "the two paths hold different seams"
        assert eg._start.current() == dp._start.current() == name


def test_neither_proposer_captured_the_function_at_import_time():
    """Late binding is the whole design. If either module had done
    `from .physics import analytic_start_u`, the name would be frozen at import
    and a later `use()` would be ignored by that module alone.
    """
    import eqrl.env_goal as eg
    import deploy_policy as dp

    for mod in (eg, dp):
        assert not hasattr(mod, "analytic_start_u"), (
            f"{mod.__name__} still binds the function directly, so a later "
            f"start.use() cannot reach it")


@pytest.mark.parametrize("mod,name", [("start_physfit", "physfit"),
                                      ("start_systematic", "systematic")])
def test_patch_and_the_seam_do_not_disagree(mod, name):
    """`patch()` is kept so the committed measurement scripts reproduce.

    It used to rebind `env_goal.analytic_start_u`, a name that no longer
    exists. Left alone, `scripts/measure_start_fast.py --method systematic`
    would have gone on running with the DEFAULT start and reported the result
    under the systematic label -- the same silent mislabelling the seam was
    built to stop, reintroduced by the fix for it.
    """
    import importlib
    importlib.import_module(f"eqrl.{mod}").patch()
    assert start.current() == name


def test_every_named_implementation_can_actually_be_loaded():
    """A name in IMPLEMENTATIONS with no working factory behind it fails at
    the first design request, not at startup."""
    for name in start.IMPLEMENTATIONS:
        start.use(name)
        assert callable(start._FACTORIES[name]())


# --------------------------------------------------------------------------
# what the physfit derivation returns
# --------------------------------------------------------------------------
def _specs():
    """The brief, plus both edges of the trained envelope."""
    from eqrl.env_goal import GoalSpace
    from eqrl.mcp_server import build_spec

    out = [build_spec()]
    gs = GoalSpace.load()
    for lo_hi in (0, 1):
        out.append(build_spec(
            peaking_db=gs.peaking_target_db[lo_hi],
            fpeak_lo_ghz=gs.fpeak_lo[lo_hi] / 1e9,
            fpeak_hi_ghz=gs.fpeak_hi[lo_hi] / 1e9,
            hd3_max_db=gs.hd3_max_db[lo_hi],
            power_max_mw=gs.power_max_w[lo_hi] * 1e3,
            eye_height_min_mv=gs.eye_height_min_v[lo_hi] * 1e3,
            eye_width_min_ui=gs.eye_width_min_ui[lo_hi]))
    return out


@pytest.fixture(scope="module")
def fit():
    """The shipped fit when it is committed, the default coefficients when it
    is not -- `derive` reads k_pk / k_f through `.get` with defaults, so both
    are legitimate inputs and the test should pass against either."""
    from eqrl.physics import PhysFit
    d = ROOT / (ROOT / "models/surrogate/LATEST").read_text().strip()
    f = d / "physfit.json"
    return PhysFit.load(f) if f.exists() else PhysFit()


@pytest.mark.parametrize("i", range(3))
def test_the_start_is_a_legal_design_across_the_envelope(fit, i):
    """Out-of-bounds normalised values are clipped downstream, so an illegal
    start does not crash -- it silently becomes a DIFFERENT design than the one
    derived. Every value has to land inside [0, 1] here.
    """
    from eqrl import start_physfit
    from eqrl.netlist import PARAM_NAMES

    u = start_physfit.start_u(_specs()[i], fit)
    assert len(u) == len(PARAM_NAMES) == 11
    assert all(0.0 <= v <= 1.0 for v in u), dict(zip(PARAM_NAMES, u))


def test_the_same_spec_gives_the_same_start(fit):
    """The cache returns a copy, not the stored list. A caller that mutated a
    returned start would otherwise poison every later call for that spec."""
    from eqrl import start_physfit
    s = _specs()[0]
    a = start_physfit.start_u(s, fit)
    a[0] = 0.5 if a[0] != 0.5 else 0.25     # mutate the caller's copy
    b = start_physfit.start_u(s, fit)
    assert b != a, "the cache handed out its own list"
    assert b == start_physfit.start_u(s, fit)


def test_it_is_a_drop_in_for_the_physics_version(fit):
    """Interchangeable at every call site means accepting the same arguments.
    A TypeError here would surface only at deploy time, inside a proposer."""
    import inspect
    from eqrl import start_physfit
    from eqrl.physics import analytic_start_u

    theirs = set(inspect.signature(analytic_start_u).parameters)
    ours = set(inspect.signature(start_physfit.start_u).parameters)
    assert theirs <= ours, f"missing: {sorted(theirs - ours)}"


# --------------------------------------------------------------------------
# what the server tells the caller
# --------------------------------------------------------------------------
def test_the_server_defaults_to_physfit_and_can_be_put_back():
    import eqrl.mcp_server as m
    assert m.START_IMPL in start.IMPLEMENTATIONS
    start.use(m.START_IMPL)
    assert start.current() == m.START_IMPL


def test_a_guessed_threshold_voltage_is_reported_not_hidden():
    """`_vt` falls back to a stored constant when the .op cannot run. That is a
    reasonable number, but a design derived from it must not read identically
    to one derived from a measured threshold."""
    import eqrl.mcp_server as m
    from eqrl import start_physfit

    start.use("physfit")
    saved = set(start_physfit._VT_FELL_BACK)
    try:
        start_physfit._VT_FELL_BACK.clear()
        assert m._start_point_note() == "physfit"
        start_physfit._VT_FELL_BACK.add(("ss", 1.71, 0.0))
        note = m._start_point_note()
        assert "physfit" in note and "Vt not measured" in note
        assert str(start_physfit.VT_FALLBACK) in note
    finally:
        start_physfit._VT_FELL_BACK.clear()
        start_physfit._VT_FELL_BACK.update(saved)


def test_a_typo_in_the_env_var_stops_the_server_at_startup():
    """A bad EQRL_START must not produce a server that registers its tools,
    looks healthy, and then dies on the first design request 45 s in."""
    import subprocess
    env = dict(os.environ, EQRL_START="phsyfit")
    r = subprocess.run(
        [sys.executable, "-c", "import eqrl.mcp_server"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "is not a starting point" in r.stderr
