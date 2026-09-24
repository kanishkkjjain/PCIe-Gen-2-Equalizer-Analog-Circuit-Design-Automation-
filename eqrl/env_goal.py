"""Goal-conditioned sizing environment, backed by the learned surrogate.

The difference from `env.CtleEnv` is the whole point of this piece of work.
`CtleEnv` is tied to ONE spec: the reward function is built from a fixed
`EqualizerSpec`, so a policy trained in it learns "reach these numbers" and has
nothing to say about any other target.  Here the target is part of the
observation, so the policy learns the *mapping* from (where I am, where I was
asked to go) to (what to change) -- and can then be handed a spec it has never
seen.

That is also what makes it genuinely RL rather than a search.  CEM re-runs its
whole loop for every new spec and keeps nothing; a goal-conditioned policy is
trained once and amortised over every spec afterwards.

Each step costs one surrogate forward pass over 4 corners, microseconds rather
than the ~18 s of SPICE that the same step costs in `CtleEnv`, which is the
only reason a policy can be trained at all inside a day.

Two guards matter more than anything else here:

* **The surrogate is only valid at the MeasCond it was trained at.**  A goal
  that moves the data rate, the transmitter swing or the HD3 tone changes what
  the measurements mean, and the model would answer confidently in the wrong
  units.  This is the `tx_swing_pp_v` bug class, so it is asserted, not assumed.
* **A model-based policy will exploit its model.**  Wherever the surrogate is
  over-optimistic, PPO will find it and live there.  The ensemble's own
  disagreement is used as a penalty, so the policy is pushed towards regions the
  model actually knows.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import pdk
from . import specs as sp
from .netlist import PARAM_NAMES
from . import start as _start        # late-bound: see eqrl/start.py
from .spice import MeasCond

ROOT = Path(__file__).resolve().parents[1]
GOAL_SPACE_PATH = ROOT / "results" / "goal_space.json"

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAVE_GYM = True
except Exception:                                       # pragma: no cover
    _HAVE_GYM = False

N_U = len(PARAM_NAMES)
#: metrics fed to the policy, in order
OBS_METRICS = ("peaking_db", "f_peak", "hd3_db", "vn_in_rms", "power_w",
               "eye_height_v", "eye_width_ui", "eye_improvement")
#: rough scales to standardise them; fixed rather than learned so a saved
#: policy keeps meaning the same thing
OBS_SCALE = {"peaking_db": 10.0, "f_peak": 2.5e9, "hd3_db": 40.0,
             "vn_in_rms": 1.5e-3, "power_w": 15e-3, "eye_height_v": 0.15,
             "eye_width_ui": 1.0, "eye_improvement": 2.0}
#: the goal fields the policy sees
GOAL_FIELDS = ("peaking_target_db", "fpeak_lo", "fpeak_hi", "hd3_max_db",
               "power_max_w", "eye_height_min_v")
GOAL_SCALE = {"peaking_target_db": 12.0, "fpeak_lo": 2.5e9, "fpeak_hi": 2.5e9,
              "hd3_max_db": 40.0, "power_max_w": 15e-3,
              "eye_height_min_v": 0.15}

#: +1 for the tail bias margin. The scorer clips reward to 0.2 for a design
#: that is not biased in saturation, and measured from bias-aware starts a
#: random policy still spends 24.9 % of its STEPS on the wrong side of that
#: cliff (p10 of the margin is -0.064 V). Without this the cliff is invisible
#: in the observation: the policy sees the reward collapse but nothing that
#: predicts it, so it can only learn to avoid whole regions rather than to
#: steer along the edge. It is cheap physics the agent should not have to
#: rediscover.
OBS_DIM = N_U + len(OBS_METRICS) + len(sp.BRIEF_SPECS) + len(GOAL_FIELDS) + 2

#: Where the reward stops paying for more predicted margin.
#:
#: MEASURED, not chosen -- `scripts/margin_transfer.py`, 223,800 (design, goal)
#: pairs over held-out designs. The real pass rate as a function of the
#: surrogate's predicted min-margin is NOT monotone:
#:
#:     [+0.00,+0.02) 32.0%   [+0.08,+0.10) 64.2%
#:     [+0.02,+0.05) 24.7%   [+0.10,+0.15) 66.7%   <- best supported bin
#:     [+0.05,+0.08) 48.9%   [+0.15,+0.20) 46.2%   (n=26)
#:                           [+0.20,  inf) 40.0%   (n=10)
#:
#: It peaks around 0.08-0.15 and then FALLS. That turn is the same mechanism
#: this project keeps meeting: a very large predicted margin mostly means the
#: model is confidently wrong, and here it shows up in data no optimiser
#: selected at all. An uncapped reward would push the policy straight into the
#: falling half -- checkpoint 5's failure wearing a different reward -- because
#: 20 steps of 0.04 is plenty of authority to get there. Flat above the cap,
#: so there is nothing to gain by going further.
MARGIN_CAP = 0.125
#: Weight on the MEAN margin. Deliberately far too small to make a trade
#: profitable: the whole point of scoring on the minimum is that improving nine
#: conditions cannot pay for breaking the tenth. It exists only so that two
#: designs with the same binding margin are not exactly tied.
MEAN_MARGIN_WEIGHT = 0.05
#: Where the reward stops punishing a worse design, and why it must exist.
#:
#: Capping the reward ABOVE without bounding it BELOW destroyed the first two
#: SAC runs. Margins are fractional distances to a limit and have no lower
#: bound at all: measured over 1 500 random steps, `m_min` reached **-425** and
#: one step's reward reached **-516**, so a 20-step return could reach -10 000.
#: The critic cannot fit a target spanning four orders of magnitude and did not
#: try to -- |Q| went 14.3 at 10k steps to **2 836** at 20k, and a diverged
#: critic hands the actor meaningless gradients. The policy then did exactly
#: what a broken Q tells it to: 91.5 % of its actions saturated at |a| = 1, it
#: travelled L2 1.60 from the start where a real search travels 0.38, and its
#: direction was ANTI-correlated with the solution (cosine -0.22).
#:
#: -1.0 matches `specs.MARGIN_NAN_FLOOR`: a design missing a limit by more than
#: 100 % is hopeless, and so is one missing by 42 500 %. Nothing the policy can
#: act on is lost. It starts at a calibrated design (mean m_min -0.22), so the
#: flat region is the "ran away" region -- precisely where a flat, maximally
#: bad reward is the message to send. The individual margins stay in the
#: observation, so direction information is not clipped, only the reward is.
MARGIN_FLOOR = -1.0
#: Same argument for the uncertainty term. Held-out designs span unc 0.015 to
#: 0.219; a random policy reached **17.2**, which would alone contribute -90 to
#: a single step's reward. Past this the model is not more wrong, it is simply
#: useless, and the reward says so at a bounded price.
UNCERTAINTY_CLIP = 0.25
#: What one unit of ensemble disagreement costs, in margin units.
#:
#: MEASURED, like MARGIN_CAP -- `scripts/margin_transfer.py` regresses the
#: surrogate's ABSOLUTE margin error on its own normalised disagreement over
#: 223,800 held-out pairs and gets
#:
#:     |predicted m_min - real m_min| ~ 5.27 * unc + 0.027
#:
#: so `m_min - 5.27 * unc` is a lower bound on the margin rather than the
#: model's hope for it. It has to be the ABSOLUTE error: the SIGNED optimism
#: rises to +0.155 and then turns NEGATIVE in the top uncertainty quintile, so
#: a line fitted to it has a negative slope and would pay the policy to be
#: uncertain.
#:
#: The number looks enormous beside checkpoint 5's 0.1 because the UNITS
#: changed. That reward was a score in [0, 1]; this one is a margin, and the
#: penalty has to be in the same currency.
#:
#: It is not academic. Measured at 10k SAC steps with the old 0.1: the
#: calibrated start sits at unc 0.018, and the policy's rollouts averaged
#: 0.263 -- fifteen times deeper into ground the ensemble disagrees about --
#: for a margin gain of +0.0022. The penalty was not paying for the trip.
UNCERTAINTY_PENALTY = 5.27


@dataclass
class GoalSpace:
    """The distribution of specs the policy is trained to satisfy.

    **Calibrated from data, not chosen.**  A range that asks for things this
    topology cannot do makes the success gate impossible by construction, and
    hours would then be spent tuning PPO for a problem with no solution.  The
    shipped design reaches hd3 = -31.2 dBc and eye height 131 mV worst-case, so
    a naive `hd3_max` down to -40 dB or `eye_height_min` up to 150 mV would be
    asking for designs that do not exist.

    `scripts/calibrate_goals.py` measures, against the collected designs, what
    fraction of sampled goals anything at all satisfies, and writes the ranges
    it settles on to `results/goal_space.json`.  Training, evaluation and
    deployment all load that one file, so their goals are comparable.
    """

    peaking_target_db: tuple = (5.0, 10.0)
    peaking_band_db: float = 2.5          # +- around the target
    fpeak_lo: tuple = (1.1e9, 1.6e9)
    fpeak_hi: tuple = (2.0e9, 2.8e9)
    hd3_max_db: tuple = (-34.0, -26.0)
    power_max_w: tuple = (3e-3, 15e-3)
    eye_height_min_v: tuple = (0.06, 0.13)
    eye_width_min_ui: tuple = (0.35, 0.50)
    feasible_frac: float | None = None    # measured by calibrate_goals.py
    note: str = ""

    def sample(self, rng) -> sp.EqualizerSpec:
        """One spec drawn from the space, as a real `EqualizerSpec`."""
        def u(rng_, lohi):
            return float(rng_.uniform(*lohi))

        tgt = u(rng, self.peaking_target_db)
        return dataclasses.replace(
            sp.EqualizerSpec(),
            peaking_target_db=tgt,
            peaking_min_db=max(1.0, tgt - self.peaking_band_db),
            peaking_max_db=tgt + self.peaking_band_db,
            fpeak_lo=u(rng, self.fpeak_lo),
            fpeak_hi=u(rng, self.fpeak_hi),
            hd3_max_db=u(rng, self.hd3_max_db),
            power_max_w=u(rng, self.power_max_w),
            eye_height_min_v=u(rng, self.eye_height_min_v),
            eye_width_min_ui=u(rng, self.eye_width_min_ui),
        )

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GoalSpace":
        known = {f.name for f in dataclasses.fields(cls)}
        out = {}
        for k, v in d.items():
            if k not in known:
                continue
            out[k] = tuple(v) if isinstance(v, list) else v
        return cls(**out)

    def save(self, path=GOAL_SPACE_PATH) -> None:
        from .cache import write_atomic
        write_atomic(Path(path), json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path=GOAL_SPACE_PATH) -> "GoalSpace":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls.from_dict(json.loads(p.read_text()))


def check_meas_compatible(spec, surrogate) -> None:
    """A goal must not change what the measurements MEAN.

    The surrogate learned metrics at one set of measurement conditions. A goal
    that moved the data rate, the transmitter swing or the HD3 tone would make
    every prediction an answer to a different question, delivered with full
    confidence -- which is exactly how tx_swing_pp_v came to be reported at a
    value the simulation never used. Cheap to check, so it is checked.
    """
    want = MeasCond.from_spec(spec)
    have = surrogate.meas
    for f in ("ui_s", "tx_swing_pp_v", "hd3_freq", "hd3_amp_pp_v",
              "noise_f_lo", "noise_f_hi"):
        a, b = getattr(want, f), getattr(have, f)
        if abs(float(a) - float(b)) > 1e-12 * max(1.0, abs(float(b))):
            raise ValueError(
                f"goal changes the measurement condition {f}: {a} != {b} "
                f"(the surrogate was trained at {b}). The surrogate cannot be "
                f"used for this goal; re-collect at the new condition.")


if _HAVE_GYM:

    class GoalCtleEnv(gym.Env):
        """Nudge the design; be rewarded against a spec that changes every episode."""

        metadata = {"render_modes": []}

        def __init__(self, surrogate, goal_space: GoalSpace | None = None,
                     corners=None, max_steps: int = 20,
                     step_scale: float = 0.04, seed: int | None = None,
                     uncertainty_penalty: float = UNCERTAINTY_PENALTY,
                     start_mix=(0.5, 0.3, 0.2), u_seed=None,
                     start_noise: float = 0.02,
                     reward_mode: str = "margin"):
            super().__init__()
            if reward_mode not in ("margin", "score"):
                raise ValueError(f"reward_mode: {reward_mode!r}")
            # "score" reproduces checkpoint 5 exactly, so its result stays
            # re-runnable after the objective changed under it. "margin" is
            # what checkpoint 6 trains against; see `_reward`.
            self.reward_mode = reward_mode
            self.sur = surrogate
            self.space = goal_space or GoalSpace.load()
            self.corners = corners or pdk.robust_search_corners()
            self.cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp,
                         "rc": c.rc} for c in self.corners]
            self.max_steps = max_steps
            # 0.04, not 0.1. The distance from the calibrated start to a
            # solution is a median of 0.331 on the largest knob (max observed
            # 0.428), so 20 steps of 0.1 gives the policy 2.0 per knob -- six
            # times the authority it needs, and it used it: measured, 30.9 % of
            # actions saturated, tap_pct at |a| = 1.000 on EVERY step and L at
            # 0.927, which walks the channel length across most of its range
            # and takes f_peak out of band with it. At 0.04 the reachable
            # travel is 0.8, still more than twice the median requirement, but
            # a saturated action is now a reasonable move rather than a lunge
            # at a bound.
            # 0.04, not 0.1. The distance from the calibrated start to a
            # solution is a median of 0.331 on the largest knob (max observed
            # 0.428), so 20 steps of 0.1 handed the policy 2.0 per knob -- six
            # times the authority it needs, and it used all of it: measured,
            # 30.9 % of actions saturated, tap_pct at |a| = 1.000 on EVERY step
            # and L at 0.927, which walks the channel length across most of its
            # range and drags f_peak out of band. At 0.04 the reachable travel
            # is 0.8, still more than twice the median requirement, but a
            # saturated action is a reasonable move rather than a lunge at a
            # bound.
            self.step_scale = step_scale
            self.lam = uncertainty_penalty
            # (analytic start, uniform, fixed seed). The analytic share is what
            # makes training match deployment: at deploy a new spec gets its own
            # hand-calculated start, so the policy must be used to seeing one.
            self.start_mix = np.asarray(start_mix, float) / np.sum(start_mix)
            self.u_seed = (np.asarray(u_seed, float) if u_seed is not None
                           else np.full(N_U, 0.5))
            # Noise added to a derived start. It is exploration, but it is also
            # destructive: the analytic start is CALIBRATED so that f_peak sits
            # inside the goal's band at all four corners, and N(0, 0.1) undoes
            # that -- measured, the start is in band on 18 of 20 goals but only
            # 9 of 20 survive the perturbation. Deployment applies no noise at
            # all, so the evaluation env uses 0 and training uses a smaller
            # value than it did.
            self.start_noise = float(start_noise)

            self.action_space = spaces.Box(-1.0, 1.0, (N_U,), np.float32)
            self.observation_space = spaces.Box(-5.0, 5.0, (OBS_DIM,),
                                                np.float32)
            self._rng = np.random.default_rng(seed)
            self._u = None
            self._t = 0
            self._spec = None
            self._best = -np.inf

        # -- the surrogate call -------------------------------------------
        def _evaluate(self, u):
            """Worst-over-corners scoring of one design, all corners at once."""
            from . import netlist as nl
            p = nl.denorm(list(np.clip(u, 0.0, 1.0)))
            params = [p] * len(self.corners)
            raw = self.sur.predict_raw(params, self.cfs)
            mets = self.sur.predict_metrics(params, self.cfs, raw=raw)
            scored = [sp.score(m, self._spec) for m in mets]
            i = int(np.argmin([s["reward"] for s in scored]))
            # normalised ensemble disagreement, averaged over the metrics that
            # are scored -- the model's own statement of where it is guessing
            unc = float(np.mean([
                np.mean(np.abs(raw["_std"][k]) / (OBS_SCALE.get(k, 1.0)))
                for k in OBS_METRICS if k in raw["_std"]]))

            # Pass/fail and margins are aggregated over EVERY corner, not read
            # off the worst-reward one. They are different questions: the
            # corner with the lowest reward need not be the corner that fails a
            # given spec, and `all_pass` on one corner is a strictly weaker
            # condition than passing everywhere.
            #
            # This mattered twice. Termination was being decided on a single
            # corner while deployment requires all four -- so G2 was being
            # measured against an easier target than G3, which would have shown
            # up as an unexplained drop between them. And the margins the
            # policy observes were one corner's, so it could not see a spec it
            # was failing elsewhere: exactly the information needed to hold
            # f_peak inside its band across the corner spread.
            agg_pass = {}
            for sc in scored:
                for k, v in sc["pass"].items():
                    agg_pass[k] = agg_pass.get(k, True) and bool(v)
            #
            # TEN margins, not the eight brief ones. `bias` and `equalises` are
            # conditions the scorer GATES on (reward clipped to 0.2 and 0.3),
            # so a reward built on the other eight lets the policy trade those
            # two away for free -- the same loophole in a smaller room.
            tm = self._tail_margin(u)
            agg_margin = {}
            for m in mets:
                for k, v in sp.margin_vector(m, self._spec,
                                             tail_margin_v=tm).items():
                    prev = agg_margin.get(k)
                    if prev is None or (v == v and v < prev):
                        agg_margin[k] = v
            return scored[i], mets[i], unc, agg_pass, agg_margin

        def _tail_margin(self, u) -> float:
            """Volts of tail-device saturation headroom for a design.

            Pulled out of `_obs` so the observation and the reward are the same
            number rather than two computations of it. It comes from the fitted
            device model (`PhysFit`), not from the surrogate's `saturated`
            flag, which is why `score`'s bias gate and this margin can disagree
            -- measured, the fit gets the sign right on 89.3 % of records. That
            is the intended trade: a continuous signal the policy can steer by,
            in place of a boolean it can only fall off.
            """
            from . import netlist as _nl
            d = _nl.denorm(list(np.clip(u, 0.0, 1.0)))
            return float(np.atleast_1d(self.sur.fit.tail_margin(
                d["W"], d["L"], d["Itail"], d["Wtail"]))[0])

        def _obs(self, scored, metrics, agg_margin=None) -> np.ndarray:
            mg = agg_margin if agg_margin is not None else sp.margins(
                metrics, self._spec)
            feats = [float(getattr(metrics, k, np.nan)) / OBS_SCALE[k]
                     for k in OBS_METRICS]
            marg = [float(mg.get(k, np.nan)) for k in sp.BRIEF_SPECS]
            goal = [float(getattr(self._spec, k)) / GOAL_SCALE[k]
                    for k in GOAL_FIELDS]
            # The tail margin already appears in `mg["bias"]`, normalised. It
            # is kept as its own slot as well so OBS_DIM does not change and
            # saved policies keep meaning the same thing; `equalises` reaches
            # the policy through `eye_improvement` in OBS_METRICS.
            tail_margin = self._tail_margin(self._u)
            v = (list(self._u) + feats + marg + goal
                 + [tail_margin / sp.TAIL_MARGIN_SCALE_V,
                    (self.max_steps - self._t) / self.max_steps])
            v = [0.0 if (x != x) else float(x) for x in v]
            return np.clip(np.asarray(v, np.float32),
                           self.observation_space.low,
                           self.observation_space.high)

        # -- gym API -------------------------------------------------------
        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if seed is not None:
                self._rng = np.random.default_rng(seed)
            self._spec = ((options or {}).get("spec")
                          or self.space.sample(self._rng))
            check_meas_compatible(self._spec, self.sur)

            kind = self._rng.choice(3, p=self.start_mix)
            if kind == 0:
                # n=48 rather than analytic.solve's default 300. The default
                # searches a 300x300 (Rs, Cs) grid against 900 frequencies --
                # ~1.5 s, which made reset 30x more expensive than a step and
                # would have dominated the whole training run. The start is
                # only a starting point and is perturbed by noise immediately
                # afterwards, so grid resolution buys nothing here.
                # calibrated against the surrogate over all four corners --
                # open loop the start missed the f_peak band at 9 of 10 goals
                u0 = np.asarray(_start.analytic_start_u(
                    self._spec, self.sur.fit,
                    surrogate=self.sur, corners_cf=self.cfs), float)
                u0 = u0 + self._rng.normal(0, self.start_noise, N_U)
            elif kind == 1:
                u0 = self._rng.uniform(0.0, 1.0, N_U)
            else:
                u0 = self.u_seed + self._rng.normal(0, self.start_noise, N_U)
            self._u = np.clip(u0, 0.0, 1.0)
            self._t = 0

            scored, mets, unc, agg_pass, agg_margin = self._evaluate(self._u)
            m_min = min(agg_margin.values())
            # seeded with the START's score, so `improve` pays for progress
            # from where the episode actually began rather than from -inf.
            # In margin mode it must be the PENALISED value, matching what
            # `_reward` compares against.
            self._best = (scored["reward"] if self.reward_mode == "score"
                          else float(np.clip(m_min, MARGIN_FLOOR, MARGIN_CAP))
                          - self.lam * min(float(unc), UNCERTAINTY_CLIP))
            # `pass` is reported at reset as well as at step. The starting
            # design is derived, not random, and it sometimes already meets the
            # spec -- a caller that only inspects step results scores those as
            # failures.
            return self._obs(scored, mets, agg_margin), {
                "reward": scored["reward"], "m_min": m_min,
                "uncertainty": unc, "pass": agg_pass, "u": self._u.copy()}

        def step(self, action):
            a = np.clip(np.asarray(action, np.float32), -1, 1)
            self._u = np.clip(self._u + self.step_scale * a, 0.0, 1.0)
            self._t += 1

            scored, mets, unc, agg_pass, agg_margin = self._evaluate(self._u)
            r = scored["reward"]
            m_min = min(agg_margin.values())
            # terminated FIRST: score mode's reward includes a terminal bonus,
            # so computing the reward before knowing whether the episode ended
            # silently drops it. It did, and the test that claims score mode
            # reproduces checkpoint 5 is the reason that was caught.
            terminated, truncated = self._episode_end(agg_pass)
            base, improve = self._reward(r, m_min, agg_margin, unc)

            reward = base + 0.5 * max(0.0, improve)
            if self.reward_mode == "score" and terminated:
                reward += 2.0
            return self._obs(scored, mets, agg_margin), float(reward), \
                terminated, truncated, {
                    "reward": r, "m_min": m_min, "pass": agg_pass,
                    "uncertainty": unc, "u": self._u.copy()}

        # -- the objective -------------------------------------------------
        def _reward(self, r, m_min, agg_margin, unc):
            """`(step reward, improvement over the best so far)`.

            In `margin` mode the score is the MINIMUM of the ten margins, and
            that is the whole fix. `specs.score` mixes a weighted soft score
            with `HARD_PASS_WEIGHT * n_pass / n`, but passing is CONJUNCTIVE --
            so a policy can raise that reward by improving five conditions
            while driving a sixth negative, and be paid for it. Measured over
            checkpoint 5, it did: 0 of 20 held-out specs solved while training
            reward climbed. A minimum has no such trade. The only way to raise
            it is to improve whatever is currently worst.

            Not a softmin. softmin(x) = -tau*log sum exp(-x_i/tau) tends to the
            MEAN for any tau large enough to give the non-binding conditions a
            gradient -- which is the count objective again, loophole included.
            The gradient the policy needs is not in the reward: all ten margins
            are already in the observation, so it can see which one binds.
            """
            if self.reward_mode == "score":
                improve = r - self._best
                self._best = max(self._best, r)
                return r - self.lam * unc, improve
            capped = float(np.clip(m_min, MARGIN_FLOOR, MARGIN_CAP))
            unc = min(float(unc), UNCERTAINTY_CLIP)
            # THE MEAN BONUS IS PAID ONLY TO DESIGNS THAT ALREADY PASS, and is
            # computed over margins clipped into [0, MARGIN_CAP].
            #
            # Both restrictions are load-bearing, and a property test found
            # that out the hard way: with a plain mean the trade this reward
            # exists to forbid was still profitable. Nine conditions at 0.30
            # and one at -0.001 scores 0.0125, against 0.0105 for ten at 0.01
            # -- the mean swings 0.27 while the minimum gives up 0.011, so
            # 0.05 of it is more than enough to buy the violation. That is the
            # softmin failure mode wearing a different hat.
            #
            # Gating on feasibility removes it completely: breaking any
            # condition makes the minimum negative, which deletes the bonus
            # entirely rather than merely taxing it. Among designs that DO
            # pass, the bonus is bounded by MEAN_MARGIN_WEIGHT * MARGIN_CAP
            # (0.00625), so the worst it can do is shift the binding margin by
            # that much -- and it can never push it below zero.
            # The penalty is inside BOTH the reward and the improvement
            # baseline. Tracking `improve` on the raw margin instead leaves a
            # loophole with the same shape as the one this reward exists to
            # close: the policy could collect shaping reward for margin it
            # gained by walking somewhere the model is guessing.
            r = capped - self.lam * unc
            improve = r - self._best
            self._best = max(self._best, r)
            if capped >= 0.0:
                finite = [v for v in agg_margin.values() if np.isfinite(v)]
                if finite:
                    mean = float(np.mean(np.clip(finite, 0.0, MARGIN_CAP)))
                    r += MEAN_MARGIN_WEIGHT * mean
            return r, improve

        def _episode_end(self, agg_pass):
            """`(terminated, truncated)`.

            In `margin` mode the episode NEVER terminates early. Stopping the
            moment the surrogate says `all_pass` is what trained checkpoint 5's
            fragility in: it teaches the policy to halt at min-margin ~ 0,
            which is exactly where model error flips the verdict at
            verification. That is the 18 % per-proposal pass rate. Running the
            full budget lets the policy keep buying margin up to MARGIN_CAP,
            where the measured real pass rate is 64-67 % instead.
            """
            if self.reward_mode == "score":
                # every corner, matching deploy's all_pass_everywhere
                return (bool(agg_pass) and all(agg_pass.values()),
                        self._t >= self.max_steps)
            return False, self._t >= self.max_steps
