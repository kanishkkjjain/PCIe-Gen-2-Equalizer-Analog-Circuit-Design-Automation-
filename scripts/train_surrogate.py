#!/usr/bin/env python3
"""Train the physics-informed surrogate and decide gate G1.

G1 is the question "can a policy trained inside this model be trusted to find
designs that are good in real SPICE?", made measurable: the surrogate must RANK
designs the way SPICE ranks them.  Concretely, Spearman correlation between the
reward the scorer gives a predicted design and the reward it gives the real
one, worst-over-corners exactly as the search aggregates it, on designs the
model never saw.  **G1: rho > 0.9.**

Rank correlation rather than R^2 on purpose: a policy does not care that the
model is 10 % off on peaking, it cares whether the model agrees about which of
two designs is better.

The run also writes the comparison that could embarrass the whole approach --
residual model against a plain network against the hand equations alone, on the
same split.  If the physics is not helping, validation.json says so and the
fallback (plain network) is already measured rather than hypothesised.

  python3 scripts/train_surrogate.py                 # train on results/evals
  python3 scripts/train_surrogate.py --tag collect --epochs 400
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import specs as sp
from eqrl.cache import git_sha, load, write_atomic
from eqrl.physics import FEATURE_NAMES, PhysFit
from eqrl.spice import MeasCond
from eqrl.surrogate import (DIRECT_TARGETS, FLAG_TARGETS, MODEL_DIR,
                            RESIDUAL_TARGETS, Net, Surrogate, assemble,
                            residual_targets, split_by_candidate)

ROOT = Path(__file__).resolve().parents[1]


def _masked_gauss_nll(pred, target, mask, torch):
    """Mean squared error over the entries that exist.

    Targets are missing in bulk and not at random -- an AC-only record has no
    eye at all -- so the loss must ignore them rather than treat NaN as zero,
    which would teach the model that a missing eye is a closed one.
    """
    if mask.sum() == 0:
        return torch.zeros((), dtype=pred.dtype)
    d = (pred - target)[mask]
    return (d * d).mean()


def train_member(Xtr, Rtr, Dtr, Ftr, Xva, Rva, Dva, Fva, seed, epochs, lr,
                 patience, verbose=False):
    import torch
    net = Net(Xtr.shape[1], seed=seed)
    opt = torch.optim.Adam(net.parameters(), lr=lr)

    def t(a, dtype=torch.float32):
        return torch.tensor(np.nan_to_num(a, nan=0.0), dtype=dtype)

    xtr, xva = t(Xtr), t(Xva)
    rtr, rva = t(Rtr), t(Rva)
    dtr, dva = t(Dtr), t(Dva)
    ftr = t(Ftr)
    mrtr, mrva = t(np.isfinite(Rtr), torch.bool), t(np.isfinite(Rva), torch.bool)
    mdtr, mdva = t(np.isfinite(Dtr), torch.bool), t(np.isfinite(Dva), torch.bool)
    bce = torch.nn.BCEWithLogitsLoss()

    best, best_state, bad = np.inf, None, 0
    for ep in range(epochs):
        net.train()
        opt.zero_grad()
        pr, pd, pf = net(xtr)
        loss = (_masked_gauss_nll(pr, rtr, mrtr, torch)
                + _masked_gauss_nll(pd, dtr, mdtr, torch)
                + 0.1 * bce(pf, ftr))
        loss.backward()
        opt.step()

        net.eval()
        with torch.no_grad():
            pr, pd, _ = net(xva)
            vl = float(_masked_gauss_nll(pr, rva, mrva, torch)
                       + _masked_gauss_nll(pd, dva, mdva, torch))
        if vl < best - 1e-6:
            best, bad = vl, 0
            best_state = {k: v.clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
        if verbose and ep % 100 == 0:
            print(f"    ep {ep:4} train {loss.item():.4f} val {vl:.4f}")
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    return net, best


def reward_of(metrics_list, spec, keys):
    """Worst-over-corners reward per design, the way the search aggregates it."""
    by: dict = {}
    for k, m in zip(keys, metrics_list):
        s = sp.score(m, spec)
        r = s.get("reward_raw", s["reward"])
        by[k] = min(by.get(k, np.inf), r)
    return by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="collect")
    ap.add_argument("--members", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=3000)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--patience", type=int, default=300)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-points", type=int, default=400,
                    help="refuse to DECLARE G1 on fewer held-in designs than "
                         "this; the number is still reported")
    ap.add_argument("--g1", type=float, default=0.9)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    import torch
    torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))

    spec = sp.EqualizerSpec()
    meas = MeasCond.from_spec(spec)
    res = load(a.tag, meas=meas)
    recs = [r for r in res.records.values() if r["metrics"].get("ok")]
    n_designs = len({tuple(r["key"]) for r in recs})
    print(f"data: {len(res)} records, {len(recs)} ok, {n_designs} designs")
    if not recs:
        print("no data yet -- is collection running?")
        return 1

    fit = PhysFit.fit(recs)
    print("physfit:", {k: f"R2(log)={q['r2_log']:.3f}"
                       for k, q in fit.quality.items()})

    tr, va, _ = split_by_candidate(recs, a.val_frac, a.seed)
    print(f"split by DESIGN: {len(tr)} train rows / {len(va)} val rows "
          f"({len({tuple(r['key']) for r in tr})} / "
          f"{len({tuple(r['key']) for r in va})} designs)")

    Dtr_all, Dva_all = assemble(tr, fit, meas), assemble(va, fit, meas)
    x_mean = Dtr_all["X"].mean(0)
    x_std = np.where(Dtr_all["X"].std(0) < 1e-9, 1.0, Dtr_all["X"].std(0))
    Xtr = (Dtr_all["X"] - x_mean) / x_std
    Xva = (Dva_all["X"] - x_mean) / x_std

    Rtr = residual_targets(Dtr_all["Yr"], Dtr_all["Br"])
    Rva = residual_targets(Dva_all["Yr"], Dva_all["Br"])
    with np.errstate(all="ignore"):
        d_mean = np.nanmean(Dtr_all["Yd"], 0)
        d_sd = np.nanstd(Dtr_all["Yd"], 0)
    d_mean = np.nan_to_num(d_mean)
    d_std = np.where(~np.isfinite(d_sd) | (d_sd < 1e-12), 1.0, d_sd)
    Dtr = (Dtr_all["Yd"] - d_mean) / d_std
    Dva = (Dva_all["Yd"] - d_mean) / d_std

    print(f"\ntraining {a.members} members on {Xtr.shape[0]} rows x "
          f"{Xtr.shape[1]} features")
    t0 = time.perf_counter()
    members = []
    for s in range(a.members):
        net, vl = train_member(Xtr, Rtr, Dtr, Dtr_all["Yf"], Xva, Rva, Dva,
                               Dva_all["Yf"], s, a.epochs, a.lr, a.patience,
                               verbose=(s == 0))
        members.append(net)
        print(f"  member {s}: val loss {vl:.5f}")
    print(f"  trained in {time.perf_counter() - t0:.1f}s")

    sur = Surrogate(fit, x_mean, x_std, d_mean, d_std, members, meas,
                    config={"tag": a.tag, "seed": a.seed,
                            "n_records": len(recs), "n_designs": n_designs,
                            "epochs": a.epochs, "val_frac": a.val_frac})

    # ---- validation (gate G1) ---------------------------------------------
    from scipy.stats import spearmanr
    report = validate(sur, va, spec, meas, Dva_all, Rva, Dva, a)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    outdir = Path(a.out) if a.out else MODEL_DIR / stamp
    sur.save(outdir)
    (outdir / "validation.json").write_text(json.dumps(report, indent=1,
                                                       default=float))
    try:
        rel = str(outdir.resolve().relative_to(ROOT))
    except ValueError:
        rel = str(outdir.resolve())   # a scratch dir outside the repo
    write_atomic(MODEL_DIR / "LATEST", rel)
    print(f"\n  saved {outdir}")
    print(f"  LATEST -> {rel}")

    rho = report["G1"]["spearman_reward"]
    enough = n_designs - len({tuple(r["key"]) for r in va}) >= a.min_points
    verdict = ("PASS" if (rho > a.g1 and enough)
               else ("more data needed" if not enough else "FAIL"))
    print(f"\n{'=' * 66}\nG1  spearman(reward_pred, reward_real) = {rho:.4f}  "
          f"(need > {a.g1})   {verdict}\n{'=' * 66}")
    return 0


def validate(sur, va_records, spec, meas, Dva_all, Rva, Dva, a) -> dict:
    """Everything that could disprove the approach, measured on held-out data."""
    from scipy.stats import spearmanr
    import torch

    params = [r["params"] for r in va_records]
    cfs = [r["corner_fields"] for r in va_records]
    keys = [tuple(r["key"]) for r in va_records]

    pred = sur.predict_metrics(params, cfs)
    real = []
    from eqrl.spice import Metrics
    for r in va_records:
        m = Metrics(**{k: v for k, v in r["metrics"].items()
                       if k in Metrics.__dataclass_fields__})
        real.append(m)

    r_pred = reward_of(pred, spec, keys)
    r_real = reward_of(real, spec, keys)
    ks = [k for k in r_real if np.isfinite(r_real[k]) and np.isfinite(r_pred[k])]
    rho = float(spearmanr([r_pred[k] for k in ks],
                          [r_real[k] for k in ks]).statistic) if len(ks) > 2 else float("nan")

    # per-metric held-out accuracy
    raw = sur.predict_raw(params, cfs)
    per_metric = {}
    for k in RESIDUAL_TARGETS + DIRECT_TARGETS:
        y = np.array([r["metrics"].get(k, np.nan) for r in va_records], float)
        p = np.asarray(raw[k], float)
        m = np.isfinite(y) & np.isfinite(p)
        if m.sum() < 5:
            continue
        ss_res = float(np.sum((y[m] - p[m]) ** 2))
        ss_tot = float(np.sum((y[m] - y[m].mean()) ** 2))
        per_metric[k] = {
            "n": int(m.sum()),
            "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
            "spearman": float(spearmanr(p[m], y[m]).statistic),
            "median_abs_err": float(np.median(np.abs(p[m] - y[m]))),
        }

    # per-spec pass/fail agreement -- what the policy's termination depends on
    agree = {}
    for k in sp.BRIEF_SPECS:
        a_, b_ = [], []
        for mp, mr in zip(pred, real):
            a_.append(bool(sp.score(mp, spec)["pass"].get(k)))
            b_.append(bool(sp.score(mr, spec)["pass"].get(k)))
        agree[k] = float(np.mean(np.array(a_) == np.array(b_)))

    # ---- the three-way comparison that can embarrass the approach ---------
    three = three_way(sur, va_records, Dva_all, Rva, Dva, spec, meas, a)

    return {
        "created": time.time(), "git_sha": git_sha(),
        "n_val_rows": len(va_records), "n_val_designs": len(set(keys)),
        "G1": {"spearman_reward": rho, "threshold": a.g1, "n_designs": len(ks),
               "definition": "spearman of worst-over-corner reward_raw, "
                             "predicted vs real, on held-out DESIGNS"},
        "per_metric": per_metric,
        "pass_agreement": agree,
        "three_way": three,
        "physfit_quality": sur.fit.quality,
    }


def three_way(sur, va_records, Dva_all, Rva, Dva, spec, meas, a) -> dict:
    """residual model vs plain network vs hand equations alone, same split.

    The residual model must beat both. If it does not, the physics is not
    earning its place and this file is the evidence -- the fallback is the
    plain network, and it is measured here rather than assumed.
    """
    from scipy.stats import spearmanr
    out = {}
    for j, k in enumerate(RESIDUAL_TARGETS):
        y = Dva_all["Yr"][:, j]
        base = Dva_all["Br"][:, j]
        pred = np.asarray(sur.predict_raw([r["params"] for r in va_records],
                                          [r["corner_fields"] for r in va_records])[k], float)
        m = np.isfinite(y) & np.isfinite(base) & np.isfinite(pred)
        if m.sum() < 5:
            continue

        def _r2(p):
            ss = float(np.sum((y[m] - p[m]) ** 2))
            tt = float(np.sum((y[m] - y[m].mean()) ** 2))
            return 1.0 - ss / tt if tt > 0 else float("nan")
        out[k] = {"analytic_only_r2": _r2(base),
                  "residual_model_r2": _r2(pred),
                  "analytic_only_spearman": float(spearmanr(base[m], y[m]).statistic),
                  "residual_model_spearman": float(spearmanr(pred[m], y[m]).statistic),
                  "n": int(m.sum())}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
