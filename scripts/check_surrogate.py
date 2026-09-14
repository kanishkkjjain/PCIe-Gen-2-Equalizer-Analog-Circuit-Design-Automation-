#!/usr/bin/env python3
"""Find where the surrogate is over-optimistic, before PPO does it for us.

A policy trained inside a model does not find good designs -- it finds designs
the MODEL thinks are good, and those are two different sets wherever the model
is wrong in the optimistic direction. This is the canonical failure of
model-based RL, and it is silent: training curves look excellent right up to
the point where the result is simulated.

So the hole is looked for deliberately and cheaply. CEM is run against the
surrogate alone -- hundreds of evaluations, no SPICE, seconds -- and whatever
it converges on is then simulated for real at the four robust corners. Twenty
seconds of ngspice answers the question that matters:

    predicted reward 0.98, real reward 0.40  -> the model has a hole, and the
                                                policy will live in it
    predicted 0.95, real 0.90                -> usable

It also reports where the surrogate's own ensemble disagrees at that point. If
the optimum sits where the members disagree far more than they do on the
training data, the uncertainty penalty in the env is the lever to raise, and
this says so before a training run is spent finding out.

  python3 scripts/check_surrogate.py
  python3 scripts/check_surrogate.py --evals 800
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import netlist as nl, pdk, specs as sp
from eqrl.cache import git_sha, load
from eqrl.physics import analytic_start_u
from eqrl.spice import MeasCond, Simulator

ROOT = Path(__file__).resolve().parents[1]


def surrogate_reward(sur, u, spec, cfs) -> tuple:
    """Worst-over-corners predicted reward, and the ensemble's disagreement."""
    p = nl.denorm(list(np.clip(u, 0, 1)))
    params = [p] * len(cfs)
    raw = sur.predict_raw(params, cfs)
    mets = sur.predict_metrics(params, cfs, raw=raw)
    scored = [sp.score(m, spec) for m in mets]
    r = min(s["reward"] for s in scored)
    return float(r), _disagreement(raw), scored


def _disagreement(raw) -> float:
    """Ensemble spread, normalised per metric.

    Averaging the raw spreads is meaningless: f_peak is in Hz, so it swamps
    everything else and the number comes out as 5e7 regardless of what the
    model actually knows. Each metric is divided by the same scale the policy's
    observation uses, so this is comparable to the env's own penalty term.
    """
    from eqrl.env_goal import OBS_METRICS, OBS_SCALE
    vals = []
    for k in OBS_METRICS:
        v = raw["_std"].get(k)
        if v is not None and np.isfinite(v).all():
            vals.append(np.mean(np.abs(v)) / OBS_SCALE.get(k, 1.0))
    return float(np.mean(vals)) if vals else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals", type=int, default=600)
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--workdir", default="/tmp/eqrl_surcheck")
    ap.add_argument("--out", default=str(ROOT / "results" / "surrogate_check.json"))
    a = ap.parse_args()

    from eqrl.agents.cem import CemConfig, optimize
    from eqrl.surrogate import Surrogate, load_latest

    sur = Surrogate.load(a.surrogate) if a.surrogate else load_latest()
    spec = sp.EqualizerSpec()
    meas = MeasCond.from_spec(spec)
    corners = pdk.robust_search_corners()
    cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp, "rc": c.rc}
           for c in corners]

    # --- how much do the members disagree on the TRAINING data? -------------
    recs = [r for r in load("collect", meas=meas).records.values()
            if r["metrics"].get("ok")]
    base_unc = float("nan")
    if recs:
        sample = recs[:400]
        raw = sur.predict_raw([r["params"] for r in sample],
                              [r["corner_fields"] for r in sample])
        base_unc = _disagreement(raw)
    print(f"typical ensemble disagreement on collected designs: {base_unc:.4g}")

    # --- optimise INSIDE the surrogate --------------------------------------
    class _SurObjective:
        """Duck-types just enough of env.Objective for agents.cem.optimize."""

        def __init__(self):
            self.n = 0
            self.best = None

        def evaluate_batch(self, us):
            out = []
            for u in us:
                r, unc, scored = surrogate_reward(sur, u, spec, cfs)
                self.n += 1
                out.append(_Res(tuple(u), r, scored))
            return out

        def all_pass_everywhere(self, res):
            return all(sp.all_pass(s) for s in res.per_corner.values())

    class _Res:
        def __init__(self, u, r, scored):
            self.u = u
            self.reward = r
            self.scored = scored[0]
            self.per_corner = {c.name: s for c, s in zip(corners, scored)}
            self.scored_corner = corners[0].name

    u0 = np.asarray(analytic_start_u(spec, sur.fit), float)
    obj = _SurObjective()
    iters = max(1, a.evals // a.pop)
    t0 = time.perf_counter()
    res = optimize(obj, CemConfig(pop=a.pop, iters=iters, init_mean=u0,
                                  init_std=0.25, seed=0, stop_on_pass=False),
                   verbose=False)
    u_star = np.asarray(res.best.u, float)
    pred_r, pred_unc, pred_scored = surrogate_reward(sur, u_star, spec, cfs)
    print(f"CEM inside the surrogate: {obj.n} evaluations in "
          f"{time.perf_counter() - t0:.1f}s -> predicted reward {pred_r:.4f}")
    print(f"  ensemble disagreement there: {pred_unc:.4g} "
          f"({pred_unc / base_unc:.2f}x typical)" if base_unc == base_unc else "")

    at_edge = [PN for PN, x in zip(nl.PARAM_NAMES, u_star)
               if x < 0.02 or x > 0.98]
    if at_edge:
        print(f"  parameters pinned at a box edge: {', '.join(at_edge)}")

    # --- simulate that design for real --------------------------------------
    print("\nsimulating the surrogate's optimum for real ...")
    from eqrl.env import Objective
    sim = Simulator(a.workdir, use_channel=True, timeout=300, meas=meas)
    real = Objective(spec, sim, corners=corners, robust="min", do_eye=True,
                     jobs=a.jobs, use_channel=True)
    rres = real(list(u_star))
    real_r = min(s.get("reward_raw", s["reward"])
                 for s in rres.per_corner.values())
    real_gated = rres.reward

    gap = pred_r - real_gated
    print(f"\n{'=' * 68}")
    print(f"  predicted reward (surrogate) : {pred_r:.4f}")
    print(f"  real reward (ngspice)        : {real_gated:.4f}  "
          f"(ungated {real_r:.4f})")
    print(f"  optimism gap                 : {gap:+.4f}")
    print(f"  real design passes everywhere: "
          f"{real.all_pass_everywhere(rres)}")
    print("=" * 68)
    if gap > 0.25:
        print("  The model has a hole at its own optimum. A policy trained in "
              "it will find the same place.\n  Raise "
              "--uncertainty-penalty in train_policy.py, or collect around "
              "this design.")
    elif gap > 0.10:
        print("  Mild optimism -- usable, but expect deployed rewards below "
              "predicted ones.")
    else:
        print("  The surrogate's optimum survives real simulation.")

    out = {"created": time.time(), "git_sha": git_sha(),
           "surrogate": str(a.surrogate or "LATEST"),
           "n_surrogate_evals": obj.n,
           "predicted_reward": pred_r, "real_reward": float(real_gated),
           "real_reward_ungated": float(real_r), "optimism_gap": float(gap),
           "uncertainty_at_optimum": pred_unc,
           "uncertainty_typical": base_unc,
           "params_at_box_edge": at_edge,
           "u": [float(x) for x in u_star],
           "real_all_pass": bool(real.all_pass_everywhere(rres)),
           "real_metrics": rres.scored["metrics"].as_dict(),
           "note": ("CEM optimised against the surrogate alone, then that "
                    "design simulated for real. The gap is how much the model "
                    "over-rates its own favourite design -- which is where a "
                    "policy trained inside it will go.")}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
