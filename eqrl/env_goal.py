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
from .physics import analytic_start_u
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

OBS_DIM = N_U + len(OBS_METRICS) + len(sp.BRIEF_SPECS) + len(GOAL_FIELDS) + 1


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
                     step_scale: float = 0.1, seed: int | None = None,
                     uncertainty_penalty: float = 0.1,
                     start_mix=(0.5, 0.3, 0.2), u_seed=None):
            super().__init__()
            self.sur = surrogate
            self.space = goal_space or GoalSpace.load()
            self.corners = corners or pdk.robust_search_corners()
            self.cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp,
                         "rc": c.rc} for c in self.corners]
            self.max_steps = max_steps
            self.step_scale = step_scale
            self.lam = uncertainty_penalty
            # (analytic start, uniform, fixed seed). The analytic share is what
            # makes training match deployment: at deploy a new spec gets its own
            # hand-calculated start, so the policy must be used to seeing one.
            self.start_mix = np.asarray(start_mix, float) / np.sum(start_mix)
            self.u_seed = (np.asarray(u_seed, float) if u_seed is not None
                           else np.full(N_U, 0.5))

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
            return scored[i], mets[i], unc

        def _obs(self, scored, metrics) -> np.ndarray:
            mg = sp.margins(metrics, self._spec)
            feats = [float(getattr(metrics, k, np.nan)) / OBS_SCALE[k]
                     for k in OBS_METRICS]
            marg = [float(mg.get(k, np.nan)) for k in sp.BRIEF_SPECS]
            goal = [float(getattr(self._spec, k)) / GOAL_SCALE[k]
                    for k in GOAL_FIELDS]
            v = (list(self._u) + feats + marg + goal
                 + [(self.max_steps - self._t) / self.max_steps])
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
                u0 = np.asarray(analytic_start_u(self._spec, self.sur.fit,
                                                 n_grid=48), float)
                u0 = u0 + self._rng.normal(0, 0.1, N_U)
            elif kind == 1:
                u0 = self._rng.uniform(0.0, 1.0, N_U)
            else:
                u0 = self.u_seed + self._rng.normal(0, 0.1, N_U)
            self._u = np.clip(u0, 0.0, 1.0)
            self._t = 0

            scored, mets, unc = self._evaluate(self._u)
            self._best = scored["reward"]
            return self._obs(scored, mets), {"reward": scored["reward"],
                                             "uncertainty": unc}

        def step(self, action):
            a = np.clip(np.asarray(action, np.float32), -1, 1)
            self._u = np.clip(self._u + self.step_scale * a, 0.0, 1.0)
            self._t += 1

            scored, mets, unc = self._evaluate(self._u)
            r = scored["reward"]
            improve = r - self._best
            self._best = max(self._best, r)
            terminated = bool(sp.all_pass(scored))
            truncated = self._t >= self.max_steps

            # Same shaping as CtleEnv so the two are comparable, minus a
            # penalty for standing where the model disagrees with itself.
            reward = (r + 0.5 * max(0.0, improve)
                      + (2.0 if terminated else 0.0)
                      - self.lam * unc)
            return self._obs(scored, mets), float(reward), terminated, \
                truncated, {"reward": r, "pass": scored.get("pass"),
                            "uncertainty": unc, "u": self._u.copy()}
