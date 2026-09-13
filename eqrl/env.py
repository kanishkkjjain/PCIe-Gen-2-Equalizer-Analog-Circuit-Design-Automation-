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

import hashlib
import os
from dataclasses import dataclass
from multiprocessing import Pool
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
from .spice import MeasCond, Simulator, safe_job_count


def _eval_task(task):
    """Evaluate one (candidate, corner) pair in a worker process.

    Module-level and self-contained so it pickles. Each worker builds its OWN
    Simulator in its OWN directory: the analyses write fixed filenames
    (ac.dat, tran.dat, eye.dat), so sharing a workdir across processes means one
    worker parses another's dump. scripts/pvt_sweep.py hit this first and works
    around it the same way.
    """
    key, workroot, params, corner, do_eye, timeout, use_ch, meas = task
    # Per (candidate, CORNER) -- not per candidate. The corners of one candidate
    # run concurrently, so keying the directory on the candidate alone puts four
    # workers in one directory all writing ac.dat / tran.dat / eye.dat, and they
    # overwrite each other's dumps. Verified: a candidate directory held exactly
    # one ac.dat shared by four corner tasks.
    # `meas` rides in the task tuple because the worker builds its own
    # Simulator: anything not carried here silently reverts to the defaults,
    # which is how tx_swing_pp_v came to be recorded in the report at a value
    # the simulation never used.
    sim = Simulator(Path(workroot) / f"c{key}" / corner.name, timeout=timeout,
                    use_channel=use_ch, meas=meas)
    m = sim.evaluate(CtleParams(**params), corner, do_eye=do_eye)
    return key, corner.name, m, sim.n_launches


@dataclass
class EvalResult:
    u: tuple                    # normalised parameter vector
    reward: float
    scored: dict                # scorer output at `scored_corner` (the WORST)
    params: CtleParams
    per_corner: dict            # corner.name -> scored
    scored_corner: str = ""     # which corner `scored` describes -- it is the
                                # worst one, NOT the typical one, and callers
                                # that label it must say so


class Objective:
    """Evaluate a normalised parameter vector -> reward, with optional PVT robustness."""

    def __init__(self, spec: sp.EqualizerSpec, sim: Simulator,
                 corners: list[pdk.Corner] | None = None,
                 fixed: dict | None = None, robust: str = "min",
                 do_eye: bool = False, jobs: int | None = None,
                 use_channel: bool = False):
        self.spec = spec
        self.sim = sim
        self.corners = corners or [pdk.typical_corner()]
        self.fixed = fixed or {}          # non-optimised context (CL, Vcm)
        self.robust = robust              # 'min' -> worst corner, 'mean'
        # do_eye applies to the WHOLE population or none of it. specs.score()
        # normalises by the weights it actually used, so an eye-scored reward
        # and a non-eye-scored one are on different scales and must never be
        # compared against each other inside one search.
        self.do_eye = do_eye
        self.use_channel = use_channel
        # Capped by RAM as well as cores: an OOM-killed worker looks like a
        # failed candidate to the scorer, which would steer the search for a
        # reason that has nothing to do with the circuit.
        self.jobs = safe_job_count(jobs)
        self._cache: dict[tuple, EvalResult] = {}
        self.n_sims = 0                   # (candidate, corner) evaluations
        self.n_launches = 0               # actual ngspice invocations
        self.history: list[EvalResult] = []

    def params_from_u(self, u) -> CtleParams:
        phys = nl.denorm(list(u))
        return CtleParams(**phys, **self.fixed)

    @staticmethod
    def _key(u) -> tuple:
        return tuple(round(float(x), 5) for x in u)

    def __call__(self, u) -> EvalResult:
        return self.evaluate_batch([u])[0]

    def evaluate_batch(self, us: list) -> list[EvalResult]:
        """Evaluate a whole population, running (candidate x corner) in parallel.

        The search was previously serial: ~30 s per candidate x 160 candidates
        is ~80 minutes on a 16-core box, while scripts/pvt_sweep.py was already
        getting a 15x speedup from the same pattern.
        """
        keys = [self._key(u) for u in us]
        todo = [k for k in dict.fromkeys(keys) if k not in self._cache]
        if todo:
            params = {k: self.params_from_u(k) for k in todo}
            root = self.sim.workdir
            meas = MeasCond.from_spec(self.spec)
            tasks = [(self._digest(k), str(root), params[k].as_dict(), c,
                      self.do_eye, self.sim.timeout, self.use_channel, meas)
                     for k in todo for c in self.corners]
            by_key = {self._digest(k): k for k in todo}
            results: dict[tuple, dict] = {k: {} for k in todo}
            if self.jobs > 1 and len(tasks) > 1:
                with Pool(min(self.jobs, len(tasks))) as pool:
                    out = pool.map(_eval_task, tasks)
            else:
                out = [_eval_task(t) for t in tasks]
            for dig, cname, m, launches in out:
                results[by_key[dig]][cname] = sp.score(m, self.spec)
                self.n_sims += 1
                self.n_launches += launches
            for k in todo:
                per_corner = results[k]
                rewards = [per_corner[c.name]["reward"] for c in self.corners]
                agg = (min(rewards) if self.robust == "min"
                       else float(np.mean(rewards)))
                # Report the WORST corner, not the first. Reporting the typical
                # corner let the stopping rule declare success on a design that
                # failed elsewhere in the search set.
                worst = min(self.corners,
                            key=lambda c: per_corner[c.name]["reward"])
                res = EvalResult(u=k, reward=float(agg),
                                 scored=per_corner[worst.name],
                                 params=params[k], per_corner=per_corner,
                                 scored_corner=worst.name)
                self._cache[k] = res
                self.history.append(res)
        return [self._cache[k] for k in keys]

    @staticmethod
    def _digest(key: tuple) -> str:
        """Stable short id for a candidate, used to name its private workdir."""
        return hashlib.sha1(repr(key).encode()).hexdigest()[:12]

    def all_pass_everywhere(self, res: EvalResult) -> bool:
        """True only when EVERY search corner passes every spec."""
        return all(sp.all_pass(sc) for sc in res.per_corner.values())


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
            # obs: n params + [dc_gain, peaking, fpeak, gain_nyq, power, reward]
            # Observations are CLIPPED into this box below: f_peak/2.5e9 alone
            # exceeds 5 for any candidate peaking above 12.5 GHz, which
            # gymnasium's env_checker rejects outright.
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
                (m.gain_nyq_db or 0) / 40.0,
                (m.power_w or 0) / self.obj.spec.power_max_w,
                res.reward,
            ]
            feats = [0.0 if (x != x) else float(x) for x in feats]
            obs = np.asarray(list(self._u) + feats, dtype=np.float32)
            return np.clip(obs, self.observation_space.low,
                           self.observation_space.high)

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
