"""The goal-conditioned environment.

These are the checks that decide whether a training run is worth starting: an
env that emits a non-finite observation, samples goals outside its own space,
or lets the surrogate be used at the wrong measurement conditions will train
happily and produce a policy that means nothing.
"""
from __future__ import annotations

import dataclasses
import inspect

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

    # `info` carries the RAW disagreement while the reward prices the CLIPPED
    # one, and that asymmetry is deliberate: the raw number is the diagnostic
    # that showed the policy sitting at 0.263 while the calibrated start sits
    # at 0.018, and clipping it in the log would have hidden exactly that. The
    # relationship between the two is asserted here so the two never drift.
    from eqrl.env_goal import UNCERTAINTY_CLIP
    charged = min(i0["uncertainty"], UNCERTAINTY_CLIP)
    assert r1 == pytest.approx(r0 - charged, abs=1e-6)


def test_goal_space_round_trips_through_json(tmp_path):
    s = GoalSpace(hd3_max_db=(-33.0, -27.0), feasible_frac=0.62)
    p = tmp_path / "gs.json"
    s.save(p)
    back = GoalSpace.load(p)
    assert back.hd3_max_db == (-33.0, -27.0)
    assert back.feasible_frac == pytest.approx(0.62)


# --------------------------------------------------------------------------
# the checkpoint-6 objective, asserted as behaviour
# --------------------------------------------------------------------------
def test_margin_mode_never_terminates_early(surrogate):
    """Ending the episode at `all_pass` is what trained the fragility in.

    The surrogate's pass boundary is min-margin ~ 0, which is exactly where
    model error flips the verdict at verification -- measured, proposals that
    stopped there passed real SPICE 18 % of the time against 64-67 % for ones
    carrying MARGIN_CAP of buffer. So in margin mode the episode always runs
    its full budget and the policy keeps being paid for margin it does not
    strictly need.
    """
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, max_steps=6,
                      reward_mode="margin")
    env.reset(seed=1)
    for ep in range(20):
        for _ in range(env.max_steps):
            _, _, term, trunc, _ = env.step(env.action_space.sample())
            assert not term, "margin mode terminated early"
            if trunc:
                break
        assert trunc, "episode did not truncate at max_steps"
        env.reset()


def test_the_reward_is_flat_once_the_margin_cap_is_reached(surrogate):
    """Above the cap more predicted margin is not better -- measured, the real
    pass rate FALLS from 66.7 % to 40 % past it, because a very large predicted
    margin mostly means the model is confidently wrong. A reward that kept
    paying would send the policy there."""
    from eqrl.env_goal import MARGIN_CAP, MEAN_MARGIN_WEIGHT
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, reward_mode="margin")
    env.reset(seed=0)
    env._best = MARGIN_CAP
    flat = {k: MARGIN_CAP for k in ("a", "b", "c")}
    lo, _ = env._reward(0.0, MARGIN_CAP, flat, 0.0)
    env._best = MARGIN_CAP
    hi, _ = env._reward(0.0, MARGIN_CAP * 10,
                        {k: MARGIN_CAP * 10 for k in flat}, 0.0)
    assert lo == pytest.approx(hi), "reward still rising above MARGIN_CAP"
    assert lo == pytest.approx(MARGIN_CAP + MEAN_MARGIN_WEIGHT * MARGIN_CAP)


def test_no_trade_is_profitable_in_margin_mode(surrogate):
    """Nine conditions improved, one broken, must LOWER the score.

    This is the defect checkpoint 5's reward had, stated as the property that
    forbids it rather than as a description of the fix.
    """
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, reward_mode="margin")
    env.reset(seed=0)
    before = {f"s{i}": 0.01 for i in range(10)}
    after = dict(before)
    for i in range(9):
        after[f"s{i}"] = 0.30           # nine conditions much better
    after["s9"] = -0.001                # one just barely broken

    env._best = -np.inf
    r_before, _ = env._reward(0.0, min(before.values()), before, 0.0)
    env._best = -np.inf
    r_after, _ = env._reward(0.0, min(after.values()), after, 0.0)
    assert r_after < r_before, "the trade paid, which is the whole defect"


def test_no_random_trade_can_buy_its_way_out_of_failing(surrogate):
    """The property, not one fixture of it.

    A single hand-built example proves only that one case was thought of. This
    draws 3,000 random margin vectors, improves every condition but one, and
    breaks that one -- and demands the reward fall EVERY time. The first
    version of this reward passed the fixture above and failed here.
    """
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, reward_mode="margin")
    env.reset(seed=0)
    rng = np.random.default_rng(0)
    for _ in range(3000):
        keys = [f"s{i}" for i in range(10)]
        before = {k: float(rng.uniform(0.0, 0.3)) for k in keys}
        victim = keys[int(rng.integers(10))]
        after = {k: float(rng.uniform(before[k], 1.0)) for k in keys}
        after[victim] = float(-rng.uniform(1e-6, 0.5))
        env._best = -np.inf
        rb, _ = env._reward(0.0, min(before.values()), before, 0.0)
        env._best = -np.inf
        ra, _ = env._reward(0.0, min(after.values()), after, 0.0)
        assert ra < rb, (f"breaking {victim} paid: {ra:.6f} >= {rb:.6f}\n"
                         f"  before={before}\n  after={after}")


def test_margin_mode_has_no_terminal_bonus_to_earn(surrogate):
    """The bonus and the early stop are one mechanism, and both are gone.

    Paying 2.0 for reaching the surrogate's pass boundary is what taught the
    old policy to stop exactly where model error decides the verdict. Keeping
    the bonus while removing the stop would keep the incentive.
    """
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, max_steps=5,
                      reward_mode="margin")
    env.reset(seed=7)
    for _ in range(60):
        _, r, term, trunc, info = env.step(env.action_space.sample())
        assert not term
        # a margin-mode reward is bounded by cap + bonus + shaping; 2.0 is far
        # outside it, so a stray terminal bonus would show up here
        assert r < 1.0, f"reward {r} looks like a terminal bonus leaked in"
        if trunc:
            env.reset()


def test_score_mode_still_reproduces_checkpoint_five(surrogate):
    """The old objective stays runnable, or checkpoint 5's result becomes a
    number with nothing behind it."""
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, max_steps=4,
                      reward_mode="score")
    _, info = env.reset(seed=2)
    assert "reward" in info
    obs, r, term, trunc, info = env.step(env.action_space.sample())
    # in score mode the reward is the scorer's, bounded in [0, 1] before shaping
    assert -1.0 <= info["reward"] <= 1.0

    # and it must still carry checkpoint 5's terminal bonus. Refactoring the
    # reward into a helper dropped it once, because the helper was called
    # before `terminated` was known -- a silent change to the objective the
    # old result is quoted against.
    import eqrl.env_goal as eg
    src = inspect.getsource(eg.GoalCtleEnv.step)
    assert "2.0" in src and "score" in src, (
        "score mode no longer pays the terminal bonus it is defined by")


def test_the_reward_and_the_observation_use_the_same_tail_margin(surrogate):
    """Two computations of one number drift. `bias` in the margin vector and
    the tail-margin slot in the observation must come from one place."""
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, reward_mode="margin")
    obs, _ = env.reset(seed=5)
    _, _, _, _, agg = env._evaluate(env._u)
    slot = len(env._u) + 8 + len(sp.BRIEF_SPECS) + len(GOAL_FIELDS)
    expected = env._tail_margin(env._u) / sp.TAIL_MARGIN_SCALE_V
    assert float(obs[slot]) == pytest.approx(np.clip(expected, -5, 5), abs=1e-5)
    assert agg["bias"] == pytest.approx(expected, abs=1e-5)


def test_the_uncertainty_penalty_is_inside_the_improvement_baseline(surrogate):
    """Shaping must be computed on the PENALISED score, not the raw margin.

    Otherwise the policy collects `0.5 * improve` for margin it gained by
    walking somewhere the model is only guessing -- a loophole with exactly the
    shape of the one this reward exists to close. Measured at 10k steps with
    the old weak penalty: the policy travelled from uncertainty 0.018 at the
    calibrated start to 0.263, and gained +0.0022 of margin for it.
    """
    from eqrl.env_goal import MARGIN_CAP
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=0, reward_mode="margin",
                      uncertainty_penalty=5.0)
    env.reset(seed=0)
    mg = {"a": 0.0, "b": 0.0}

    # same margin, more doubt -> strictly worse, and NOT a new best
    env._best = -np.inf
    confident, imp_c = env._reward(0.0, 0.0, mg, 0.0)
    env._best = confident
    doubtful, imp_d = env._reward(0.0, 0.0, mg, 0.05)
    assert doubtful < confident
    assert imp_d < 0, "a move into uncertainty registered as an improvement"

    # a small margin gain must NOT pay for a large jump in uncertainty
    env._best = -np.inf
    here, _ = env._reward(0.0, 0.00, mg, 0.018)
    env._best = here
    there, imp = env._reward(0.0, 0.02, {"a": 0.02, "b": 0.02}, 0.263)
    assert there < here, "wandering into the unknown paid"
    assert imp < 0


def test_the_reward_is_bounded_below_as_well_as_above(surrogate):
    """An unbounded reward diverged the critic and destroyed two training runs.

    Margins are fractional distances to a limit, so they have no lower bound at
    all. Measured over 1 500 random steps before this was fixed: `m_min`
    reached -425 and one step's reward -516, so a 20-step return could reach
    -10 000. |Q| went from 14.3 at 10k steps to 2 836 at 20k, and the actor,
    following a diverged critic, saturated 91.5 % of its actions and moved
    ANTI-parallel to the solution (cosine -0.22).

    Capping the reward above is not enough and was what the first version did.
    """
    from eqrl.env_goal import (MARGIN_CAP, MARGIN_FLOOR, UNCERTAINTY_CLIP,
                               MEAN_MARGIN_WEIGHT)
    env = GoalCtleEnv(surrogate, GoalSpace(), seed=2, max_steps=10,
                      reward_mode="margin", uncertainty_penalty=5.27)
    lo = MARGIN_FLOOR - 5.27 * UNCERTAINTY_CLIP
    hi = MARGIN_CAP * (1 + MEAN_MARGIN_WEIGHT) + 0.5 * (MARGIN_CAP - lo)

    env.reset(seed=2)
    worst = np.inf
    for _ in range(600):
        _, r, _, trunc, _ = env.step(env.action_space.sample())
        worst = min(worst, r)
        assert lo - 1e-6 <= r <= hi + 1e-6, f"reward {r} outside [{lo}, {hi}]"
        if trunc:
            env.reset()
    assert np.isfinite(worst)

    # and the two clips are doing the work, not luck: an absurd margin and an
    # absurd uncertainty must both come back bounded
    env._best = -np.inf
    r_huge, _ = env._reward(0.0, -425.0, {"a": -425.0}, 17.2)
    assert r_huge >= lo - 1e-6, f"a -425 margin produced {r_huge}"
    assert r_huge == pytest.approx(MARGIN_FLOOR - 5.27 * UNCERTAINTY_CLIP)
