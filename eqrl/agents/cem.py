"""Cross-Entropy Method (CEM) policy search over the normalised parameter space.

CEM is a population-based, gradient-free RL policy-search method.  It maintains a
diagonal Gaussian search distribution N(mu, sigma^2) over the normalised design
vector u in [0,1]^n.  Each iteration it samples a small population, keeps the
top-scoring "elite" fraction, and refits (mu, sigma) to those elites.  Because
every sample is a full candidate design, it reaches near-optimal points in tens
of SPICE evaluations rather than the thousands a step-wise deep-RL policy needs
-- which is exactly the "fewest search points / lowest design time" objective.

The agent is deliberately dependency-light (numpy only) and takes any callable
``objective(u) -> EvalResult`` (see :class:`eqrl.env.Objective`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import specs as sp


@dataclass
class CemConfig:
    pop: int = 16              # candidates per iteration
    iters: int = 10            # iterations
    elite_frac: float = 0.25   # fraction kept as elites
    init_std: float = 0.30     # initial exploration std (normalised units)
    min_std: float = 0.02      # floor to avoid premature collapse
    std_decay: float = 0.92    # multiplicative std shrink per iteration
    init_mean: float = 0.5
    seed: int = 0
    stop_on_pass: bool = True  # halt early once every spec passes


@dataclass
class CemResult:
    best: object                 # EvalResult of the best candidate seen
    best_reward: float
    n_eval: int
    curve: list                  # best-so-far reward per iteration
    mean: np.ndarray             # final search mean (normalised)
    passed: bool


def optimize(objective, cfg: CemConfig | None = None, n_dim: int | None = None,
             verbose: bool = True) -> CemResult:
    cfg = cfg or CemConfig()
    from ..netlist import PARAM_NAMES
    n = n_dim or len(PARAM_NAMES)
    rng = np.random.default_rng(cfg.seed)

    mu = np.full(n, cfg.init_mean)
    std = np.full(n, cfg.init_std)
    n_elite = max(2, int(round(cfg.pop * cfg.elite_frac)))

    best = None
    best_r = -np.inf
    curve: list[float] = []
    n_eval = 0
    passed = False

    for it in range(cfg.iters):
        samples = np.clip(rng.normal(mu, std, size=(cfg.pop, n)), 0.0, 1.0)
        # keep the running best as one guaranteed member (elitism)
        if best is not None:
            samples[0] = np.asarray(best.u)
        results = [objective(s) for s in samples]
        n_eval += cfg.pop
        rewards = np.array([r.reward for r in results])

        order = np.argsort(rewards)[::-1]
        elites = samples[order[:n_elite]]
        mu = elites.mean(axis=0)
        std = np.maximum(elites.std(axis=0), cfg.min_std) * 1.0
        std *= cfg.std_decay

        it_best_idx = int(order[0])
        if rewards[it_best_idx] > best_r:
            best_r = float(rewards[it_best_idx])
            best = results[it_best_idx]
        curve.append(best_r)

        if verbose:
            m = best.scored["metrics"]
            npass = sum(best.scored.get("pass", {}).values())
            ntot = len(best.scored.get("pass", {})) or 1
            print(f"[CEM it {it+1:2d}/{cfg.iters}] best_R={best_r:.3f} "
                  f"peak={m.peaking_db:.1f}dB @ {m.f_peak/1e9:.2f}GHz "
                  f"pow={m.power_w*1e3:.2f}mW pass={npass}/{ntot} "
                  f"(sims={objective.n_sims})")

        if cfg.stop_on_pass and best is not None and sp.all_pass(best.scored):
            passed = True
            if verbose:
                print(f"[CEM] all specs pass at iter {it+1}; stopping early.")
            break

    return CemResult(best=best, best_reward=best_r, n_eval=n_eval, curve=curve,
                     mean=mu, passed=passed or (best is not None and
                                                sp.all_pass(best.scored)))
