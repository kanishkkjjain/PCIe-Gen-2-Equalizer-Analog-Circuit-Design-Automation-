#!/usr/bin/env python3
"""Propose K designs per spec, verify every one in SPICE, report K-for-90 %.

This is the measurement that decides checkpoint 6, and it is written down
because the equivalent number for checkpoint 5 was produced by an uncommitted
heredoc. `results/best_of_n.json` exists with nothing in the repository able to
reproduce it -- the same class of problem as a figure in the docs with no
artifact behind it, which `tests/test_docs.py` exists to catch.

THE WORKFLOW BEING MEASURED. Proposing is nearly free (no simulator) and
verifying is affordable, so the useful question is not "does one proposal pass"
but "how many proposals before one of them does". Checkpoint 5 answered 8 for
90 % of specs, at 18 % per proposal.

Both methods run through THIS code on the SAME specs, because a comparison
against a historical number measured by different code is not a comparison:

  surrogate_cem   CEM inside the surrogate, K seeds -> K proposals. The
                  shipped baseline, re-measured so the bar is reproducible.
  policy          K stochastic rollouts of the trained SAC policy from the one
                  calibrated start. Each rollout contributes its BEST
                  min-margin design, not its last.

Everything is verified at all 20 PVT corners. Results are written per spec as
they complete, so an interrupted run keeps what it finished and re-running
skips it.

  python3 scripts/best_of_n.py --method surrogate_cem --k 8
  python3 scripts/best_of_n.py --method policy --k 8
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eqrl import netlist as nl, pdk, specs as sp
from eqrl.cache import EvalLog, git_sha, write_atomic
from eqrl.env_goal import GoalSpace, check_meas_compatible
from margin_transfer import _agg_min_margin
from eqrl.spice import MeasCond, Simulator

ROOT = Path(__file__).resolve().parents[1]
_STOP = False


def _on_signal(signum, frame):
    global _STOP
    _STOP = True
    print(f"\n[signal {signum}] finishing this spec, then exiting.", flush=True)


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------
def propose_surrogate_cem(spec, sur, cfs, k, evals, pop, seed0=0) -> list:
    """K independent CEM searches inside the surrogate, K different seeds."""
    from eqrl.agents.cem import CemConfig, optimize
    from deploy_policy import start_for

    u0 = start_for(spec, sur, pdk.robust_search_corners())

    class _Res:
        def __init__(self, u, r):
            self.u, self.reward = tuple(u), r
            self.per_corner, self.scored_corner = {}, ""

    class _Obj:
        def __init__(self):
            self.n = 0

        def evaluate_batch(self, us):
            out = []
            for u in us:
                p = nl.denorm(list(np.clip(u, 0, 1)))
                mets = sur.predict_metrics([p] * len(cfs), cfs)
                self.n += 1
                out.append(_Res(u, min(sp.score(m, spec)["reward"]
                                       for m in mets)))
            return out

        def all_pass_everywhere(self, res):
            return False

    props = []
    for seed in range(seed0, seed0 + k):
        o = _Obj()
        r = optimize(o, CemConfig(pop=pop, iters=max(1, evals // pop),
                                  init_mean=u0, init_std=0.25, seed=seed,
                                  stop_on_pass=False), verbose=False)
        props.append({"source": seed, "u": [float(x) for x in r.best.u]})
    return props


def propose_policy(model, spec, sur, cfs, k, max_steps, seed0=0) -> list:
    """K stochastic rollouts from the one calibrated start.

    `deterministic=False` is the point. The diversity has to come from the
    policy's own entropy -- it is why SAC was chosen over PPO -- because every
    rollout begins at the SAME calibrated design. A near-deterministic head
    would return K copies of one proposal and best-of-K would buy nothing,
    which is why proposal diversity is a gate here and not an afterthought.

    Each rollout contributes the design with the best min-margin it SAW, not
    the one it happened to end on: the episode deliberately runs its full
    budget rather than stopping at a solution, so the last step is not
    generally the best one.
    """
    from eqrl.env_goal import GoalCtleEnv
    from train_policy import rollout

    env = GoalCtleEnv(sur, GoalSpace.load(), seed=12345, max_steps=max_steps,
                      uncertainty_penalty=0.0, start_mix=(1.0, 0.0, 0.0),
                      start_noise=0.0, reward_mode="margin")
    import torch
    props = []
    for seed in range(seed0, seed0 + k):
        # The action noise comes from TORCH's generator, not the env's, so
        # seeding `env._rng` alone makes the proposals differ by accident
        # rather than reproducibly -- they would not come back the same on a
        # re-run, and a resumed run would mix two streams. Both are seeded.
        env._rng = np.random.default_rng(seed)
        torch.manual_seed(seed)
        r = rollout(model, env, spec, max_steps, deterministic=False)
        props.append({"source": seed, "u": r["u"],
                      "rollout_m_min": r["best_m_min"],
                      "start_m_min": r["start_m_min"],
                      "solved_at_start": r["solved_at_start"]})
    return props


def diversity(props) -> float:
    """Mean pairwise L2 between proposals, in normalised design space."""
    us = [np.asarray(p["u"], float) for p in props]
    if len(us) < 2:
        return 0.0
    return float(np.mean([np.linalg.norm(a - b)
                          for a, b in itertools.combinations(us, 2)]))


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=("surrogate_cem", "policy"),
                    required=True)
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--n-specs", type=int, default=10)
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--cem-evals", type=int, default=2000)
    ap.add_argument("--cem-pop", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--workdir", default="/tmp/eqrl_bestofn")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--extend", action="store_true",
                    help="ADD proposals to specs already in the output file, "
                         "continuing the seed sequence, instead of skipping "
                         "them. This is how K-for-90 %% is pushed past the "
                         "first K without paying again for verifications that "
                         "have already been done -- each one is ~20 SPICE "
                         "evaluations and they are the expensive part.")
    ap.add_argument("--only-unsolved", action="store_true",
                    help="with --extend, only touch specs that no proposal has "
                         "solved yet. The solved ones already have their "
                         "first_success_rank and more proposals cannot lower "
                         "it, so simulating them again buys nothing.")
    ap.add_argument("--pidfile", default=None)
    a = ap.parse_args()

    if a.pidfile:
        Path(a.pidfile).parent.mkdir(parents=True, exist_ok=True)
        Path(a.pidfile).write_text(f"{os.getpid()}\n")

    out_path = Path(a.out or ROOT / "results" / f"best_of_n_{a.method}.json")

    from eqrl.surrogate import Surrogate, load_latest
    from deploy_policy import DEPLOY_SEED, deploy_specs, verify

    sur = Surrogate.load(a.surrogate) if a.surrogate else load_latest()
    model = None
    if a.method == "policy":
        from stable_baselines3 import SAC
        pol = a.policy
        if pol is None:
            latest = ROOT / "models" / "policy" / "LATEST"
            if not latest.exists():
                print("no trained policy -- run scripts/train_policy.py first")
                return 1
            pol = latest.read_text().strip()
        p = Path(pol)
        model = SAC.load(p if p.is_absolute() else ROOT / p)
        print(f"policy: {Path(pol).name}")

    space = GoalSpace.load()
    specs = deploy_specs(space, a.n_specs, DEPLOY_SEED)
    corners = pdk.robust_search_corners()
    cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp, "rc": c.rc}
           for c in corners]

    done = {}
    if out_path.exists() and not a.redo:
        try:
            done = {r["spec"]: r
                    for r in json.loads(out_path.read_text()).get("specs", [])}
            print(f"  {len(done)} spec(s) already done, skipping them")
        except Exception:
            done = {}

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    rows = list(done.values())
    for i, spec in enumerate(specs):
        if _STOP:
            continue
        prior = done.get(i)
        if prior is not None and not a.extend:
            continue
        if prior is not None and a.only_unsolved and prior["first_success_rank"]:
            continue
        check_meas_compatible(spec, sur)
        sim = Simulator(f"{a.workdir}/spec{i}", use_channel=True,
                        timeout=a.timeout, meas=MeasCond.from_spec(spec))

        print(f"\n=== spec{i}: peaking {spec.peaking_min_db:.1f}-"
              f"{spec.peaking_max_db:.1f} dB, f_peak "
              f"{spec.fpeak_lo/1e9:.2f}-{spec.fpeak_hi/1e9:.2f} GHz, "
              f"hd3 < {spec.hd3_max_db:.1f}, eye_h > "
              f"{spec.eye_height_min_v*1e3:.0f} mV ===", flush=True)

        # Continue the seed sequence rather than restarting it, so an
        # extension adds genuinely new proposals instead of re-deriving the
        # ones already verified.
        kept = list(prior["proposals"]) if prior is not None else []
        seed0 = len(kept)
        t0 = time.perf_counter()
        if a.method == "policy":
            props = propose_policy(model, spec, sur, cfs, a.k, a.max_steps,
                                   seed0=seed0)
        else:
            props = propose_surrogate_cem(spec, sur, cfs, a.k,
                                          a.cem_evals, a.cem_pop, seed0=seed0)
        propose_s = time.perf_counter() - t0
        if kept:
            print(f"  extending: {len(kept)} proposals already verified, "
                  f"adding {len(props)} more (seeds {seed0}..{seed0+len(props)-1})",
                  flush=True)
        div = diversity(props)
        print(f"  {len(props)} proposals in {propose_s:.1f} s, "
              f"pairwise L2 {div:.3f}", flush=True)

        # what the MODEL claims, kept beside what SPICE measures. Checkpoint 5
        # dropped this column and made its own evidence uncheckable.
        for pr in props:
            p = nl.denorm(list(np.clip(pr["u"], 0, 1)))
            mets = sur.predict_metrics([p] * len(cfs), cfs)
            tm = float(np.atleast_1d(sur.fit.tail_margin(
                p["W"], p["L"], p["Itail"], p["Wtail"]))[0])
            mv, binding = _agg_min_margin(mets, spec, tm)
            pr["predicted_m_min"] = float(mv)
            pr["predicted_binding"] = binding
            pr["predicted_reward"] = float(min(sp.score(m, spec)["reward"]
                                               for m in mets))

        # The log goes INTO the verifying Objective, so every (candidate,
        # corner) lands on disk the moment it is simulated. A best-of-8 run
        # over 10 specs is 1,600 twenty-corner evaluations of near-feasible
        # designs -- the region the surrogate is weakest in and has least data
        # for. Draining them afterwards, or not keeping them at all, throws
        # away more simulation than the whole original collection run.
        with EvalLog(f"bon_{a.method}_spec{i}",
                     extra={"spec_id": f"spec{i}", "method": a.method}) as log:
            for j, pr in enumerate(props):
                pr["pvt"] = verify(pr["u"], spec, sim, a.jobs, f"spec{i}",
                                   log=log)
                pr["real_pass"] = bool(pr["pvt"]["all_pass_everywhere"])
                print(f"    [{j + 1}/{len(props)}] pred m_min "
                      f"{pr['predicted_m_min']:+.4f} ({pr['predicted_binding']})"
                      f"  -> 20-corner "
                      f"{pr['pvt']['brief_specs_passed']}/8 "
                      f"{'PASS' if pr['real_pass'] else 'fail'}"
                      f"  binding {pr['pvt']['binding'][0]}"
                      f" {pr['pvt']['binding'][1]:+.1%}", flush=True)

        props = kept + props
        div = diversity(props)
        ranks = [j for j, pr in enumerate(props) if pr["real_pass"]]
        row = {"spec": i, "n_proposals": len(props), "diversity_l2": div,
               "propose_s": propose_s,
               "first_success_rank": (ranks[0] + 1) if ranks else None,
               "n_passing": len(ranks),
               "spec_fields": {k: getattr(spec, k) for k in
                               ("peaking_min_db", "peaking_max_db",
                                "peaking_target_db", "fpeak_lo", "fpeak_hi",
                                "hd3_max_db", "power_max_w",
                                "eye_height_min_v", "eye_width_min_ui")},
               "proposals": props}
        rows = [r for r in rows if r["spec"] != i] + [row]
        rows.sort(key=lambda r: r["spec"])
        _write(rows, out_path, a)
        print(f"  spec{i}: {len(ranks)}/{len(props)} pass, first at rank "
              f"{row['first_success_rank']}", flush=True)

    _write(rows, out_path, a)
    _summarise(rows, a)
    return 0


def _write(rows, out_path, a) -> None:
    write_atomic(out_path, json.dumps(
        {"created": time.time(), "git_sha": git_sha(), "method": a.method,
         "k": max((r["n_proposals"] for r in rows), default=a.k),
         "deploy_seed": 999, "m_specs": len(rows),
         "n_per_spec": max((r["n_proposals"] for r in rows), default=a.k),
         "specs": rows}, indent=2, default=float))


def _summarise(rows, a) -> None:
    if not rows:
        return
    # the real number of proposals per spec, which after --extend is not --k
    k = max(r["n_proposals"] for r in rows)
    print(f"\n{'=' * 70}\nbest-of-N, method = {a.method}, {len(rows)} specs\n"
          f"{'=' * 70}")
    print("   K   specs solved by K proposals")
    curve = []
    for n in range(1, k + 1):
        hit = sum(1 for r in rows
                  if r["first_success_rank"] and r["first_success_rank"] <= n)
        frac = hit / len(rows)
        curve.append({"k": n, "solved": hit, "frac": frac})
        print(f"  {n:2}   {hit}/{len(rows)}  {frac:6.0%}"
              + ("   <- 90 %" if frac >= 0.9 else ""))
    k90 = next((c["k"] for c in curve if c["frac"] >= 0.9), None)
    # THE headline, in the one form it is reported in. It is the number of
    # proposals you must verify to have a 90 % chance that one of them meets
    # the spec at all 20 PVT corners -- nothing else. Reporting per-proposal
    # pass rate INSTEAD of this is how two runs end up being compared on two
    # different metrics that move in opposite directions: measured here, the
    # per-proposal rate went UP against checkpoint 5 (18 % -> 35 %) while the
    # fraction of specs solved went DOWN (90 % -> 67 % at K=8). One metric,
    # fixed in advance, or the comparison means nothing.
    print(f"\n  ==> it takes n = {k90 if k90 else f'more than {k}'} to have a "
          f"90 % chance of solving the spec")

    per = (sum(r["n_passing"] for r in rows)
           / max(1, sum(r["n_proposals"] for r in rows)))
    div = float(np.mean([r["diversity_l2"] for r in rows]))
    print(f"\n  G6-2  K for 90 % of specs        = "
          f"{k90 if k90 else f'> {k}'}   (checkpoint 5 CEM: 8)")
    print(f"  G6-3  per-proposal real pass rate = {per:.1%}   "
          f"(checkpoint 5: 18 %, need > 30 %)")
    print(f"  G6-4  mean proposal diversity     = {div:.3f}   (need > 0.25)")
    at_start = sum(1 for r in rows for p in r["proposals"]
                   if p.get("solved_at_start"))
    if at_start:
        print(f"\n  note: {at_start} proposals were already solved at the "
              f"calibrated START, which is the starting design's credit, "
              f"not the policy's")


if __name__ == "__main__":
    raise SystemExit(main())
