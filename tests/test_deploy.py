"""The deployment harness and the seeds that keep its comparison honest.

Pure tests. What they guard is not arithmetic but the integrity of the
claim: if the specs the policy is deployed on overlap the ones it trained or
was evaluated on, then G3 measures memorisation and says nothing, and nothing
in the output would look wrong.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch", reason="deploy harness imports the surrogate")

from eqrl import specs as sp
from eqrl.env_goal import GoalSpace

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dep():
    return _load("deploy_policy")


@pytest.fixture(scope="module")
def trn():
    return _load("train_policy")


def _fingerprint(s) -> tuple:
    return (round(s.peaking_target_db, 9), round(s.fpeak_lo, 3),
            round(s.fpeak_hi, 3), round(s.hd3_max_db, 9),
            round(s.power_max_w, 12), round(s.eye_height_min_v, 9),
            round(s.eye_width_min_ui, 9))


def test_deploy_specs_are_deterministic(dep):
    """A rerun must resume onto the SAME specs, or the table mixes problems."""
    space = GoalSpace()
    a = dep.deploy_specs(space, 6)
    b = dep.deploy_specs(space, 6)
    assert [_fingerprint(x) for x in a] == [_fingerprint(x) for x in b]


def test_deployment_specs_are_disjoint_from_evaluation_specs(dep, trn):
    """G2 is measured on seed 777, G3 on seed 999.

    If these overlapped, "the policy solves specs it has never seen" would be
    false while every number in the output still looked reasonable.
    """
    space = GoalSpace()
    ev = {_fingerprint(s) for s in trn.held_out_specs(space, 200)}
    dp = {_fingerprint(s) for s in dep.deploy_specs(space, 200)}
    assert trn.EVAL_SEED != dep.DEPLOY_SEED
    assert ev & dp == set(), "evaluation and deployment specs overlap"


def test_deployed_specs_are_real_equalizer_specs(dep):
    """They are fed to the same scorer as everything else, so they have to be
    coherent specs, not just numbers in the right ranges."""
    for s in dep.deploy_specs(GoalSpace(), 25):
        assert isinstance(s, sp.EqualizerSpec)
        assert s.peaking_min_db < s.peaking_target_db < s.peaking_max_db
        assert s.fpeak_lo < s.fpeak_hi
        assert s.hd3_max_db < 0
        assert s.power_max_w > 0 and s.eye_height_min_v > 0


def test_both_methods_are_compared_at_the_same_budget(dep):
    """'Our policy beats a search given a tenth of the simulations' is not a
    result. The budget is one number used by both."""
    import inspect
    src = inspect.getsource(dep.main)
    assert "run_policy(model, sur, obj, spec, a.budget" in src
    assert "run_cem(obj, spec, a.budget, sur)" in src
    # and CEM is the shipped optimiser, not a local reimplementation
    assert "from eqrl.agents.cem import CemConfig, optimize" in \
        inspect.getsource(dep.run_cem)


def test_every_method_starts_from_the_same_design(dep):
    """Handing one method a worse starting point would decide the comparison by
    construction rather than by method.

    It also has to be the CALIBRATED start, the one the environment uses.
    Training happened from starts calibrated against the surrogate over all four
    corners; an open-loop start at deployment would put a difference between G2
    and G3 that has nothing to do with the method -- and it is not a small one,
    since open loop the start lands in the f_peak band at 2 of 20 goals against
    18 of 20 calibrated.
    """
    import inspect
    for fn in (dep.run_policy, dep.run_cem, dep.run_cem_converged,
               dep.run_surrogate_cem):
        src = inspect.getsource(fn)
        assert "start_for(spec, sur" in src, (
            f"{fn.__name__} does not use the shared start")
        assert "analytic_start_u(spec, sur.fit)" not in src, (
            f"{fn.__name__} still builds an open-loop start")
    # and the shared helper must actually close the loop
    shared = inspect.getsource(dep.start_for)
    assert "surrogate=sur" in shared and "corners_cf=cfs" in shared
