"""Gymnasium environment + a direct objective for the CTLE sizing problem.

Two views of the same problem are provided:

* :class:`CtleEnv` — a standard ``gymnasium.Env`` where the agent nudges the
  normalised device-parameter vector each step and is rewarded by the spec
  scorer.  This is the drop-in interface for deep-RL agents (PPO/TD3 via
  stable-baselines3) if one wants to use them.

* :class:`Objective` — a plain callable ``u -> result`` that evaluates a full
  normalised parameter vector (optionally as the worst case over a set of PVT
  corners).  The sample-efficient Cross-Entropy-Method agent optimises this
  directly, which is what actually meets the "fewest simulations" goal.

Both share one :class:`~eqrl.spice.Simulator` and one evaluation cache so
repeated candidates never re-simulate.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAVE_GYM = True
except Exception:  # gymnasium optional for the CEM path
    _HAVE_GYM = False

from . import netlist as nl
from . import pdk
from . import specs as sp
from .netlist import CtleParams, PARAM_NAMES
from .spice import Simulator, Metrics


@dataclass
class EvalResult:
    u: tuple                    # normalised parameter vector
    reward: float
    scored: dict                # scorer output at the reporting corner
    params: CtleParams
    per_corner: dict            # corner.name -> scored


class Objective:
    """Evaluate a normalised parameter vector -> reward, with optional PVT robustness."""

    def __init__(self, spec: sp.EqualizerSpec, sim: Simulator,
                 corners: list[pdk.Corner] | None = None,
                 fixed: dict | None = None, robust: str = "min"):
        self.spec = spec
        self.sim = sim
        self.corners = corners or [pdk.typical_corner()]
        self.fixed = fixed or {}          # non-optimised context (CL, Vcm)
        self.robust = robust              # 'min' -> worst corner, 'mean'
        self._cache: dict[tuple, EvalResult] = {}
        self.n_sims = 0
        self.history: list[EvalResult] = []

    def params_from_u(self, u) -> CtleParams:
        phys = nl.denorm(list(u))
        return CtleParams(**phys, **self.fixed)

    def __call__(self, u) -> EvalResult:
        key = tuple(round(float(x), 5) for x in u)
        if key in self._cache:
            return self._cache[key]
        p = self.params_from_u(u)
        per_corner: dict[str, dict] = {}
        rewards = []
        for c in self.corners:
            m = self.sim.evaluate(p, c)
            self.n_sims += 1
            sc = sp.score(m, self.spec)
            per_corner[c.name] = sc
            rewards.append(sc["reward"])
        agg = min(rewards) if self.robust == "min" else float(np.mean(rewards))
        # report against the first (typical) corner for readability
        report = per_corner[self.corners[0].name]
        res = EvalResult(u=key, reward=float(agg), scored=report,
                         params=p, per_corner=per_corner)
        self._cache[key] = res
        self.history.append(res)
        return res


if _HAVE_GYM:

    class CtleEnv(gym.Env):
        """Incremental-edit env for deep-RL agents.

        Observation = normalised params (n) + a few normalised measured specs.
        Action      = continuous nudge in [-1,1]^n scaled by ``step_scale``.
        Reward      = spec score at the current point (dense).
        """

        metadata = {"render_modes": []}

        def __init__(self, objective: Objective, max_steps: int = 24,
                     step_scale: float = 0.15, seed: int | None = None):
            super().__init__()
            self.obj = objective
            self.n = len(PARAM_NAMES)
            self.max_steps = max_steps
            self.step_scale = step_scale
            self.action_space = spaces.Box(-1.0, 1.0, (self.n,), np.float32)
            # obs: n params + [dc_gain, peaking, fpeak, gain2g5, power, reward]
            self.observation_space = spaces.Box(-5.0, 5.0, (self.n + 6,), np.float32)
            self._u = None
            self._t = 0
            self._rng = np.random.default_rng(seed)
            self._best = -np.inf

        def _obs(self, res: EvalResult) -> np.ndarray:
            m = res.scored["metrics"]
            feats = [
                (m.dc_gain_db or 0) / 40.0,
                (m.peaking_db or 0) / 12.0,
                (m.f_peak or 0) / 2.5e9,
                (m.gain_2g5_db or 0) / 40.0,
                (m.power_w or 0) / self.obj.spec.power_max_w,
                res.reward,
            ]
            feats = [0.0 if (x != x) else x for x in feats]
            return np.asarray(list(self._u) + feats, dtype=np.float32)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if seed is not None:
                self._rng = np.random.default_rng(seed)
            self._u = self._rng.uniform(0.3, 0.7, self.n)
            self._t = 0
            res = self.obj(self._u)
            self._best = res.reward
            return self._obs(res), {"reward": res.reward}

        def step(self, action):
            action = np.clip(np.asarray(action, np.float32), -1, 1)
            self._u = np.clip(self._u + self.step_scale * action, 0.0, 1.0)
            self._t += 1
            res = self.obj(self._u)
            r = res.reward
            improve = r - self._best
            self._best = max(self._best, r)
            terminated = sp.all_pass(res.scored)
            truncated = self._t >= self.max_steps
            # reward shaping: level + improvement bonus, big bonus on full pass
            reward = r + 0.5 * max(0.0, improve) + (2.0 if terminated else 0.0)
            return self._obs(res), float(reward), terminated, truncated, {
                "reward": r, "pass": res.scored.get("pass"), "params": res.params}
