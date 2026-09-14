"""The goal-conditioned environment.

These are the checks that decide whether a training run is worth starting: an
env that emits a non-finite observation, samples goals outside its own space,
or lets the surrogate be used at the wrong measurement conditions will train
happily and produce a policy that means nothing.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from eqrl import specs as sp

torch = pytest.importorskip("torch", reason="the surrogate env needs torch")
gym = pytest.importorskip("gymnasium")

from eqrl.env_goal import (GOAL_FIELDS, OBS_DIM, GoalCtleEnv, GoalSpace,
                           check_meas_compatible)
from eqrl.physics import PhysFit
from eqrl.spice import MeasCond
from eqrl.surrogate import DIRECT_TARGETS, RESIDUAL_TARGETS, Net, Surrogate
from eqrl.netlist import PARAM_NAMES
from eqrl.physics import FEATURE_NAMES


@pytest.fixture(scope="module")
def surrogate():
    """An untrained surrogate: the residual heads still equal the physics."""
    n_d = len(DIRECT_TARGETS)
    return Surrogate(PhysFit(), np.zeros(len(FEATURE_NAMES)),
                     np.ones(len(FEATURE_NAMES)), np.zeros(n_d), np.ones(n_d),
                     members=[Net(len(FEATURE_NAMES), seed=s) for s in (0, 1)],
                     meas=MeasCond.from_spec(sp.EqualizerSpec()))


@pytest.fixture
def env(surrogate):
    return GoalCtleEnv(surrogate, GoalSpace(), seed=0, max_steps=8)


def test_it_is_a_valid_gymnasium_env(env):
    from gymnasium.utils.env_checker import check_env
    check_env(env, skip_render_check=True)


def test_observations_stay_finite_and_inside_the_box(env):
    """NaN reaches this env by every route -- a missing eye, a degenerate AC
    solve, a division by a zero margin. One of them in an observation poisons
    the policy gradient silently."""
    obs, _ = env.reset(seed=3)
    assert obs.shape == (OBS_DIM,)
    for i in range(300):
        obs, r, term, trunc, info = env.step(env.action_space.sample())
        assert np.isfinite(obs).all(), f"non-finite observation at step {i}"
        assert env.observation_space.contains(obs)
        assert np.isfinite(r), f"non-finite reward at step {i}"
        if term or trunc:
            obs, _ = env.reset()


def test_sampled_goals_land_inside_the_declared_space():
    space = GoalSpace()
    rng = np.random.default_rng(0)
    for _ in range(200):
        g = space.sample(rng)
        assert space.peaking_target_db[0] <= g.peaking_target_db <= space.peaking_target_db[1]
        assert space.hd3_max_db[0] <= g.hd3_max_db <= space.hd3_max_db[1]
        assert space.eye_height_min_v[0] <= g.eye_height_min_v <= space.eye_height_min_v[1]
        # the peaking window must stay a window
        assert g.peaking_min_db < g.peaking_target_db < g.peaking_max_db
        assert g.fpeak_lo < g.fpeak_hi


def test_a_goal_that_changes_the_measurement_condition_is_refused(surrogate):
    """The surrogate learned metrics at ONE MeasCond.

    A goal that moved the data rate or the transmitter swing would make every
    prediction an answer to a different question, delivered with full
    confidence -- the tx_swing_pp_v bug class. It must fail loudly.
    """
    ok = sp.EqualizerSpec()
    check_meas_compatible(ok, surrogate)          # the trained condition

    for field, value in (("tx_swing_pp_v", 0.800), ("nyquist", 4.0e9),
                         ("hd3_freq", 250e6)):
        bad = dataclasses.replace(ok, **{field: value})
        with pytest.raises(ValueError, match="measurement condition"):
            check_meas_compatible(bad, surrogate)


def test_termination_only_happens_when_every_spec_passes(env):
    obs, _ = env.reset(seed=11)
    for _ in range(400):
        obs, r, term, trunc, info = env.step(env.action_space.sample())
        if term:
            assert info["pass"] is not None
            assert all(info["pass"].get(k) for k in sp.BRIEF_SPECS), (
                "terminated while a brief spec was failing")
        if term or trunc:
            env.reset()


def test_the_goal_is_actually_in_the_observation(env):
    """If the goal were not observable the policy could not be conditioned on
    it, and 'goal-conditioned' would be a claim with nothing behind it."""
    o1, _ = env.reset(seed=5)
    spec_a = env._spec
    o2, _ = env.reset(seed=5)
    assert np.allclose(o1, o2), "reset is not reproducible for a fixed seed"

    # same design, different goal -> different observation
    hard = dataclasses.replace(spec_a, hd3_max_db=spec_a.hd3_max_db - 6.0,
                               eye_height_min_v=spec_a.eye_height_min_v + 0.03)
    o3, _ = env.reset(seed=5, options={"spec": hard})
    assert not np.allclose(o1, o3), "the goal does not reach the observation"


def test_uncertainty_is_reported_and_penalised(surrogate):
    """A model-based policy will exploit its model wherever it is optimistic.

    The ensemble's own disagreement is the only signal available for that, so
    it must be both reported and priced into the reward.
    """
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, uncertainty_penalty=0.0)
    env.reset(seed=2)
    _, r0, _, _, i0 = env.step(np.zeros(len(PARAM_NAMES), np.float32))
    assert "uncertainty" in i0 and np.isfinite(i0["uncertainty"])

    penal = GoalCtleEnv(surrogate, GoalSpace(), seed=0, uncertainty_penalty=1.0)
    penal.reset(seed=2)
    _, r1, _, _, i1 = penal.step(np.zeros(len(PARAM_NAMES), np.float32))
    assert i1["uncertainty"] == pytest.approx(i0["uncertainty"])
    assert r1 == pytest.approx(r0 - i0["uncertainty"], abs=1e-6)


def test_goal_space_round_trips_through_json(tmp_path):
    s = GoalSpace(hd3_max_db=(-33.0, -27.0), feasible_frac=0.62)
    p = tmp_path / "gs.json"
    s.save(p)
    back = GoalSpace.load(p)
    assert back.hd3_max_db == (-33.0, -27.0)
    assert back.feasible_frac == pytest.approx(0.62)
