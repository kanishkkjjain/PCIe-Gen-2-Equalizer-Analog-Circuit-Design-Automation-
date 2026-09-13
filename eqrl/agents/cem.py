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

from dataclasses import dataclass

import numpy as np

from .. import specs as sp


@dataclass
class CemConfig:
    pop: int = 16              # candidates per iteration
    iters: int = 10            # iterations
    elite_frac: float = 0.25   # fraction kept as elites
    init_std: float = 0.30     # initial exploration std (normalised units)
    # When the starting mean comes from the analytic solve it is already a good
    # design, so the first population should explore AROUND it rather than
    # scatter across the whole box. design.py applies this when it seeds.
    seeded_std: float = 0.12
    min_std: float = 0.02      # floor to avoid premature collapse
    std_decay: float = 0.92    # multiplicative std shrink per iteration
    init_mean: float | object = 0.5   # scalar, or a per-knob vector
    seed: int = 0
    stop_on_pass: bool = True  # halt early once every spec passes


@dataclass
class CemResult:
    best: object                 # EvalResult of the best candidate seen
    best_reward: float
    n_eval: int                  # distinct candidates actually simulated
    n_launches: int              # ngspice invocations (the real cost)
    curve: list                  # best-so-far reward per iteration
    mean: np.ndarray             # final search mean (normalised)
    passed: bool


def optimize(objective, cfg: CemConfig | None = None, n_dim: int | None = None,
             verbose: bool = True) -> CemResult:
    cfg = cfg or CemConfig()
    from ..netlist import PARAM_NAMES
    n = n_dim or len(PARAM_NAMES)
    rng = np.random.default_rng(cfg.seed)

    # A vector init_mean is an analytic starting point (see eqrl/analytic.py).
    # Starting at the centre of an 11-dimensional box is a long way from
    # anything that works: measured, the centre scores 0.522 (5/9 specs) while
    # the analytic start scores 0.999 (9/9).
    mu = (np.asarray(cfg.init_mean, dtype=float) if np.ndim(cfg.init_mean)
          else np.full(n, float(cfg.init_mean)))
    if mu.shape != (n,):
        raise ValueError(f"init_mean must be scalar or length {n}, got {mu.shape}")
    mu = np.clip(mu, 0.0, 1.0)
    std = np.full(n, cfg.init_std)
    n_elite = max(2, int(round(cfg.pop * cfg.elite_frac)))

    best = None
    best_r = -np.inf
    curve: list[float] = []
    n_eval = 0
    passed = False

    for it in range(cfg.iters):
        samples = np.clip(rng.normal(mu, std, size=(cfg.pop, n)), 0.0, 1.0)
        if best is not None:
            # elitism: the running best is always re-evaluated (a cache hit)
            samples[0] = np.asarray(best.u)
        elif it == 0:
            # FIRST iteration: evaluate the starting mean itself. Sampling from
            # N(mu, std) never produces mu exactly, so an analytically derived
            # starting point would otherwise never be scored -- and it is the
            # one design we already know is good. Injecting it makes it the
            # elite to beat, so the search can only improve on it.
            samples[0] = mu.copy()
        before = len(getattr(objective, "_cache", ()))
        # one batch per iteration so the whole population runs in parallel
        if hasattr(objective, "evaluate_batch"):
            results = objective.evaluate_batch(list(samples))
        else:
            results = [objective(s) for s in samples]
        # Count only candidates actually simulated. The elitist re-injection is
        # a cache hit and costs nothing; counting it inflated the headline
        # "evaluations used" figure by one per iteration.
        n_eval += max(0, len(getattr(objective, "_cache", ())) - before)
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
                  f"(cand={n_eval} ngspice={getattr(objective,'n_launches',0)})")

        if cfg.stop_on_pass and best is not None and _passes_everywhere(objective, best):
            passed = True
            if verbose:
                print(f"[CEM] all specs pass at iter {it+1}; stopping early.")
            break

    return CemResult(best=best, best_reward=best_r, n_eval=n_eval,
                     n_launches=getattr(objective, "n_launches", 0),
                     curve=curve, mean=mu,
                     passed=passed or (best is not None
                                       and _passes_everywhere(objective, best)))


def _passes_everywhere(objective, res) -> bool:
    """Stop only when every SEARCH CORNER passes, not just the reported one.

    `res.scored` is one corner's scorecard. Testing it alone meant a robust
    multi-corner search would stop the moment that single corner passed, even
    with the others failing -- which is the entire point of searching them.
    """
    checker = getattr(objective, "all_pass_everywhere", None)
    if checker is not None:
        return checker(res)
    return sp.all_pass(res.scored)
