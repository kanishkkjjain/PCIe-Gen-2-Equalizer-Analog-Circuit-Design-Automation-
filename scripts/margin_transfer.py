#!/usr/bin/env python3
"""Does the surrogate's PREDICTED margin survive contact with real SPICE?

Gate G6-0, and the cheapest question in the project: it decides whether
checkpoint 6 is worth building before any of it is built. No simulation, no
training -- it reads the evaluations that already exist.

THE PREMISE BEING TESTED. Checkpoint 5's proposals passed real SPICE only 18 %
of the time, and the diagnosis was that the search stopped at the pass boundary
-- min-margin ~ 0 in the model -- which is exactly where model error flips the
verdict. The fix is to make the policy buy margin PAST zero. That is only worth
doing if predicted margin carries real information, and checkpoint 5 also
measured Spearman(predicted REWARD, real reward) = -0.33, which is a reason to
check rather than assume.

WHY NOT USE results/best_of_n.json. It holds 100 proposals with predicted and
real outcomes, which looks like the obvious source. It cannot be used: the
mapping from its spec entries to the seed-999 spec stream is not recoverable.
Every CEM-optimised proposal scores ~0.999 whatever spec it is graded against
(the reward saturates at the boundary -- the very defect HARD_PASS_WEIGHT was
added for), so no amount of matching identifies which spec produced an entry.
An analysis built on a guessed mapping would be a number with nothing behind
it. Measured: the best-matching spec beat the runner-up by a mean absolute
0.0003 in reward, i.e. noise.

WHAT THIS USES INSTEAD. Every record in the durable cache carries REAL measured
metrics, so the real margin vector is computable for ANY spec, not just the one
it was collected under. The surrogate gives the predicted vector for the same
design and spec. Take held-out designs only -- the same 25 % split by design
key the model was validated on -- cross them with goals drawn from the
calibrated goal space, and the result is hundreds of thousands of (predicted,
real) margin pairs at zero simulation cost.

  python3 scripts/margin_transfer.py
  python3 scripts/margin_transfer.py --n-goals 600 --out results/margin_transfer.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import cache, pdk, specs as sp
from eqrl.cache import git_sha, write_atomic
from eqrl.env_goal import GoalSpace
from eqrl.spice import Metrics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "margin_transfer.json"

#: G6-0 passes if the real pass rate among designs at or above this predicted
#: margin beats the rate at predicted margin >= 0 by at least this many points.
BUFFER_THRESHOLD = 0.08
MIN_GAIN_POINTS = 15.0
#: Edges for the BIN-WISE curve. The cumulative curve can only ever look
#: monotone-ish; it is the bin-wise one that shows the turn, and the turn is
#: the reason `env_goal` caps its reward rather than rewarding margin without
#: limit. See MARGIN_CAP there.
BIN_EDGES = (0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, float("inf"))


def _metrics(md: dict) -> Metrics:
    return Metrics(**{k: v for k, v in md.items()
                      if k in Metrics.__dataclass_fields__})


def _agg_min_margin(mets, spec, tail_margin_v) -> tuple:
    """Worst-over-corners margin vector, collapsed to its binding entry.

    Aggregated over EVERY corner rather than read off the worst-reward one:
    the corner with the lowest reward need not be the corner that fails a
    given spec, and the deployment gate is passing everywhere.
    """
    out: dict = {}
    for m in mets:
        for k, v in sp.margin_vector(m, spec, tail_margin_v=tail_margin_v).items():
            if v < out.get(k, float("inf")):
                out[k] = v
    k = min(out, key=out.get)
    return out[k], k


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="collect,goalstart,goalstart2")
    ap.add_argument("--val-frac", type=float, default=0.25)
    ap.add_argument("--split-seed", type=int, default=0)
    ap.add_argument("--n-goals", type=int, default=600)
    ap.add_argument("--goal-seed", type=int, default=0)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    from scipy.stats import spearmanr
    from eqrl.surrogate import Surrogate, load_latest, split_by_candidate

    sur = Surrogate.load(a.surrogate) if a.surrogate else load_latest()
    recs = cache.load([t.strip() for t in a.tag.split(",") if t.strip()])
    print(recs.summary())

    # The SAME split the model was validated on, reproduced from its config --
    # scoring the surrogate on designs it was fitted to would answer a
    # different and much easier question.
    _, va, _ = split_by_candidate(list(recs.records.values()),
                                  a.val_frac, a.split_seed)

    corners = pdk.robust_search_corners()
    order = [c.name for c in corners]
    cfs = {c.name: {"process": c.process, "vdd": c.vdd, "temp": c.temp,
                    "rc": c.rc} for c in corners}

    by_design: dict = {}
    for r in va:
        md = r.get("metrics") or {}
        if md.get("ok", True) and r["corner"] in cfs:
            by_design.setdefault(tuple(r["key"]), {})[r["corner"]] = r
    full = {k: v for k, v in by_design.items() if len(v) == len(corners)}
    print(f"{len(va)} held-out rows -> {len(full)} designs measured at all "
          f"{len(corners)} robust corners")
    if len(full) < 100:
        print("too few complete held-out designs to conclude anything")
        return 1

    space = GoalSpace.load()
    rng = np.random.default_rng(a.goal_seed)
    goals = [space.sample(rng) for _ in range(a.n_goals)]

    from eqrl.env_goal import OBS_METRICS, OBS_SCALE

    t0 = time.perf_counter()
    pred, real, unc = [], [], []
    pred_bind, real_bind = [], []
    for per in full.values():
        real_ms = [_metrics(per[n]["metrics"]) for n in order]
        p = per[order[0]]["params"]
        raw = sur.predict_raw([p] * len(order), [cfs[n] for n in order])
        pred_ms = sur.predict_metrics([p] * len(order), [cfs[n] for n in order],
                                      raw=raw)
        # the same normalised ensemble disagreement the environment observes
        u = float(np.mean([np.mean(np.abs(raw["_std"][k]) / OBS_SCALE.get(k, 1.0))
                           for k in OBS_METRICS if k in raw["_std"]]))
        # `bias` for the PREDICTION comes from the fitted device model, which
        # is what a policy has at proposal time; for the REAL side it comes
        # from the measured saturation flag, which is ground truth.
        tm = float(np.atleast_1d(sur.fit.tail_margin(
            p["W"], p["L"], p["Itail"], p["Wtail"]))[0])
        for spec in goals:
            pv, pk = _agg_min_margin(pred_ms, spec, tm)
            rv, rk = _agg_min_margin(real_ms, spec, None)
            pred.append(pv); real.append(rv); unc.append(u)
            pred_bind.append(pk); real_bind.append(rk)

    pred = np.asarray(pred); real = np.asarray(real)
    real_pass = real >= 0.0
    rho = float(spearmanr(np.clip(pred, -5, 5), np.clip(real, -5, 5)).statistic)

    print(f"\nn = {len(pred):,} (design, goal) pairs in "
          f"{time.perf_counter() - t0:.1f} s")
    print(f"spearman(predicted m_min, real m_min) = {rho:+.3f}")
    print(f"real all_pass rate over all pairs     = {real_pass.mean():.2%}")

    print("\n  predicted m_min threshold -> real pass rate above it")
    curve = []
    for thr in (0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30):
        g = pred >= thr
        rate = float(real_pass[g].mean()) if g.sum() else float("nan")
        curve.append({"threshold": thr, "n": int(g.sum()), "real_pass": rate})
        note = "" if g.sum() >= 50 else "   (thin)"
        print(f"    >= {thr:+.2f}   n={int(g.sum()):6d}   "
              f"real pass {rate:6.1%}{note}")

    # BIN-WISE, which is the curve that decides the reward cap. More margin is
    # not monotonically better: past some point a high predicted margin means
    # the model is confidently wrong, which is model exploitation showing up
    # even in data no optimiser selected.
    print("\n  predicted m_min BIN -> real pass rate inside it")
    bins = []
    for lo, hi in zip(BIN_EDGES[:-1], BIN_EDGES[1:]):
        g = (pred >= lo) & (pred < hi)
        if not g.sum():
            continue
        rate = float(real_pass[g].mean())
        bins.append({"lo": lo, "hi": hi if hi != float("inf") else None,
                     "n": int(g.sum()), "real_pass": rate})
        note = "" if g.sum() >= 50 else "   (thin)"
        hs = "  inf" if hi == float("inf") else f"{hi:+.2f}"
        print(f"    [{lo:+.2f}, {hs})  n={int(g.sum()):5d}   "
              f"real pass {rate:6.1%}{note}")

    solid = [b for b in bins if b["n"] >= 50]
    best = max(solid, key=lambda b: b["real_pass"]) if solid else None
    if best is not None:
        hi = best["hi"] if best["hi"] is not None else best["lo"] + 0.05
        cap = round((best["lo"] + hi) / 2, 3)
        print(f"\n  best well-supported bin [{best['lo']:+.2f}, {hi:+.2f}) at "
              f"{best['real_pass']:.1%} -> recommended MARGIN_CAP = {cap:+.3f}")
    else:
        cap = None
        print("\n  no bin with n >= 50; cannot recommend a cap")

    # ---- how wrong is the margin, as a function of the model's own doubt? --
    #
    # This sets the uncertainty penalty, and it has to be the ABSOLUTE error.
    # The SIGNED optimism is not monotone -- it rises to +0.139 and then turns
    # negative in the top uncertainty quintile -- so fitting a line to it gives
    # a NEGATIVE slope, i.e. a reward for being uncertain. The absolute error
    # is monotone and nearly linear, and it is the honest quantity anyway: a
    # margin the model cannot predict in either direction is not margin the
    # policy should be paid for.
    pen = None
    u = np.asarray(unc)
    keep = (np.abs(pred) < 5) & (np.abs(real) < 5)
    if keep.sum() > 1000:
        e = np.abs(pred[keep] - real[keep])
        A = np.vstack([u[keep], np.ones(keep.sum())]).T
        slope, icept = np.linalg.lstsq(A, e, rcond=None)[0]
        pen = float(slope)
        print("\n  margin error vs the model's own disagreement")
        qs = np.quantile(u[keep], [0, .2, .4, .6, .8, 1.0])
        for i in range(5):
            g = (u[keep] >= qs[i]) & (u[keep] <= qs[i + 1])
            print(f"    unc [{qs[i]:.3f},{qs[i+1]:.3f}]  n={int(g.sum()):6d}  "
                  f"|error| {e[g].mean():.4f}  signed "
                  f"{(pred[keep] - real[keep])[g].mean():+.4f}")
        print(f"\n    |margin error| ~ {slope:.2f} * unc + {icept:.4f}")
        print(f"    -> recommended uncertainty penalty = {slope:.2f}, which "
              f"makes the\n       reward a lower bound on the margin rather "
              f"than the model's hope")

    base = next(c for c in curve if c["threshold"] == 0.0)["real_pass"]
    buf = next(c for c in curve if c["threshold"] == BUFFER_THRESHOLD)
    gain = (buf["real_pass"] - base) * 100
    ok = gain >= MIN_GAIN_POINTS

    print(f"\n  buying margin from 0 to {BUFFER_THRESHOLD:+.2f} moves the real "
          f"pass rate {base:.1%} -> {buf['real_pass']:.1%}  ({gain:+.1f} points)")
    print(f"  G6-0 needs >= {MIN_GAIN_POINTS:+.0f}:  "
          f"{'PASS' if ok else 'FAIL'}")

    from collections import Counter
    miss = Counter(k for k, rp in zip(real_bind, real_pass) if not rp)
    print("\n  binding REAL margin over all pairs: "
          + ", ".join(f"{k} {v}" for k, v in miss.most_common(5)))

    write_atomic(Path(a.out), json.dumps({
        "created": time.time(), "git_sha": git_sha(),
        "surrogate": str(getattr(sur, "path", a.surrogate or "LATEST")),
        "tag": a.tag, "val_frac": a.val_frac, "split_seed": a.split_seed,
        "n_goals": a.n_goals, "goal_seed": a.goal_seed,
        "n_designs": len(full), "n_pairs": int(len(pred)),
        "spearman_pred_real_mmin": rho,
        "real_pass_rate_all": float(real_pass.mean()),
        "threshold_curve": curve,
        "bins": bins,
        "recommended_margin_cap": cap,
        "recommended_uncertainty_penalty": pen,
        "buffer_threshold": BUFFER_THRESHOLD,
        "gain_points": gain, "G6_0_pass": bool(ok),
        "binding_real": dict(miss.most_common()),
        "note": ("Held-out designs crossed with sampled goals. Real margins "
                 "come from measured metrics, so they are ground truth; the "
                 "designs are NOT optimiser-selected, so this is an upper "
                 "bound on what a search that exploits the model achieves."),
    }, indent=2))
    print(f"\n  wrote {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
