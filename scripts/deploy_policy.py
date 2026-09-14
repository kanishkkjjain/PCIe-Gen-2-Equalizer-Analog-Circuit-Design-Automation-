#!/usr/bin/env python3
"""Run the trained policy against REAL ngspice on unseen specs. Gate G3.

Everything up to here happened inside a learned model of the circuit, so none
of it is evidence about the circuit. This is where the claim is settled: the
policy is handed specs drawn with a seed disjoint from training and evaluation,
its observations come from actual SPICE simulations at the four robust corners,
and whatever it lands on is verified across all 20 of the brief's PVT corners.

The comparison that matters is CEM AT AN EQUAL BUDGET. "Our policy beats a
search we gave a tenth of the simulations to" is not a result. Each method gets
the same number of SPICE evaluations on the same spec, and the table says who
did better with them -- including when that is CEM.

Durability, because each spec costs minutes of simulation:

* `results/policy_vs_cem.json` is rewritten after EVERY spec completes, so an
  interrupted run keeps everything it finished
* re-running skips specs already present; `--redo` forces them
* every SPICE call is logged to the durable cache under `deploy_<spec>`, so
  even a spec interrupted halfway contributes its evaluations to the dataset

  python3 scripts/deploy_policy.py --n-specs 6
  python3 scripts/deploy_policy.py --n-specs 6 --budget 20
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import pdk, specs as sp
from eqrl.cache import EvalLog, git_sha, write_atomic
from eqrl.env_goal import GoalSpace, check_meas_compatible
from eqrl.netlist import PARAM_NAMES
from eqrl.physics import analytic_start_u
from eqrl.spice import MeasCond, Simulator
from eqrl.surrogate import load_latest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "policy_vs_cem.json"
DEPLOY_SEED = 999

_STOP = False


def _on_signal(signum, frame):
    global _STOP
    _STOP = True
    print(f"\n[signal {signum}] finishing the current spec, then exiting.",
          flush=True)


def deploy_specs(space: GoalSpace, n: int, seed: int = DEPLOY_SEED) -> list:
    """Specs the policy has never been trained or evaluated on.

    Drawn from the SAME calibrated space as training, with a seed disjoint from
    both the training stream and the seed-777 evaluation set. Drawing them from
    a different space would make the comparison meaningless in the other
    direction -- easy specs would flatter the policy.
    """
    rng = np.random.default_rng(seed)
    return [space.sample(rng) for _ in range(n)]


def _worst(obj, res):
    return min(sc["reward"] for sc in res.per_corner.values())


def run_policy(model, sur, obj, spec, budget: int, max_steps: int) -> dict:
    """Greedy policy rollout with observations from real SPICE."""
    from eqrl.env_goal import GoalCtleEnv
    # A surrogate-backed env is used ONLY to build observations in exactly the
    # layout the policy was trained on; every reward and metric below comes
    # from SPICE, never from it.
    shim = GoalCtleEnv(sur, GoalSpace(), seed=0, max_steps=max_steps,
                       uncertainty_penalty=0.0)
    shim._spec = spec
    shim._t = 0

    u = np.asarray(analytic_start_u(spec, sur.fit), float)
    best = {"reward": -np.inf, "u": u.copy(), "res": None}
    used = 0
    trace = []
    for t in range(min(budget, max_steps)):
        res = obj(u)
        used += 1
        r = _worst(obj, res)
        if r > best["reward"]:
            best = {"reward": r, "u": u.copy(), "res": res}
        trace.append({"step": t, "reward": float(r),
                      "u": [float(x) for x in u]})
        if obj.all_pass_everywhere(res):
            break
        shim._u = u
        obs = shim._obs(res.scored, res.scored["metrics"])
        action, _ = model.predict(obs, deterministic=True)
        u = np.clip(u + shim.step_scale * np.clip(action, -1, 1), 0.0, 1.0)
    return {"sims": used, "best_reward": float(best["reward"]),
            "u": [float(x) for x in best["u"]], "res": best["res"],
            "trace": trace}


def run_cem(obj, spec, budget: int, sur, seed: int = 0) -> dict:
    """CEM at the SAME simulation budget, from the SAME analytic start.

    `agents.cem.optimize` is used as-is rather than reimplemented: the point of
    the comparison is the shipped optimiser, and a second copy of it here would
    be a second place for its behaviour to drift.

    `seeded_std`, not `init_std`, because the mean is an analytic design --
    which is what design.py does when it seeds, and scattering across the whole
    box from a good start would handicap CEM for no reason.
    """
    from eqrl.agents.cem import CemConfig, optimize

    u0 = np.asarray(analytic_start_u(spec, sur.fit), float)
    pop = max(4, budget // 4)
    iters = max(1, budget // pop)
    cfg = CemConfig(pop=pop, iters=iters, init_mean=u0,
                    init_std=CemConfig.seeded_std, seed=seed,
                    stop_on_pass=True)
    r = optimize(obj, cfg, verbose=False)
    return {"sims": r.n_eval, "best_reward": float(r.best_reward),
            "u": [float(x) for x in r.best.u], "res": r.best,
            "curve": [float(x) for x in r.curve]}


def verify(u, spec, sim, jobs, tag) -> dict:
    """All 20 of the brief's PVT corners on one design."""
    from eqrl.design import _worst_margins
    from eqrl.env import Objective
    v = Objective(spec, sim, corners=pdk.pvt_grid("spec"), robust="min",
                  do_eye=True, jobs=jobs, use_channel=True)
    res = v(list(u))
    agg: dict = {}
    for sc in res.per_corner.values():
        for k, val in sc["pass"].items():
            agg[k] = agg.get(k, True) and bool(val)
    mg = _worst_margins(res, spec)
    binding = min(mg, key=lambda k: mg[k]) if mg else "none"
    return {"all_pass_everywhere": v.all_pass_everywhere(res),
            "brief_specs_passed": sum(1 for k in sp.BRIEF_SPECS if agg.get(k)),
            "failed": [k for k in sorted(agg) if not agg[k]],
            "binding": [binding, float(mg.get(binding, float("nan")))],
            "margins": {k: float(x) for k, x in mg.items()},
            "n_corners": len(v.corners), "launches": v.n_launches}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-specs", type=int, default=6)
    ap.add_argument("--budget", type=int, default=20,
                    help="SPICE evaluations each method gets, per spec")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--workdir", default="/tmp/eqrl_deploy")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--no-verify", dest="verify", action="store_false",
                    default=True)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--pidfile", default=None)
    a = ap.parse_args()

    if a.pidfile:
        Path(a.pidfile).parent.mkdir(parents=True, exist_ok=True)
        Path(a.pidfile).write_text(f"{os.getpid()}\n")

    from stable_baselines3 import PPO
    from eqrl.env import Objective
    from eqrl.surrogate import Surrogate

    sur = Surrogate.load(a.surrogate) if a.surrogate else load_latest()
    pol = a.policy
    if pol is None:
        latest = ROOT / "models" / "policy" / "LATEST"
        if not latest.exists():
            print("no trained policy -- run scripts/train_policy.py first")
            return 1
        pol = latest.read_text().strip()
    p = Path(pol)
    if not p.is_absolute():
        p = ROOT / p
    model = PPO.load(p)
    print(f"policy: {p.name}")

    space = GoalSpace.load()
    specs = deploy_specs(space, a.n_specs)

    done = {}
    if OUT.exists() and not a.redo:
        try:
            done = {r["spec_id"]: r
                    for r in json.loads(OUT.read_text()).get("specs", [])}
            print(f"  {len(done)} spec(s) already done, skipping them")
        except Exception:
            done = {}

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    rows = list(done.values())
    for i, spec in enumerate(specs):
        sid = f"spec{i}"
        if sid in done or _STOP:
            continue
        check_meas_compatible(spec, sur)
        meas = MeasCond.from_spec(spec)
        sim = Simulator(f"{a.workdir}/{sid}", use_channel=True,
                        timeout=a.timeout, meas=meas)
        corners = pdk.robust_search_corners()

        print(f"\n=== {sid}: peaking {spec.peaking_min_db:.1f}-"
              f"{spec.peaking_max_db:.1f} dB, f_peak "
              f"{spec.fpeak_lo/1e9:.2f}-{spec.fpeak_hi/1e9:.2f} GHz, "
              f"hd3 < {spec.hd3_max_db:.1f}, eye_h > "
              f"{spec.eye_height_min_v*1e3:.0f} mV ===", flush=True)

        row = {"spec_id": sid, "spec": {k: getattr(spec, k) for k in
                                        ("peaking_min_db", "peaking_max_db",
                                         "peaking_target_db", "fpeak_lo",
                                         "fpeak_hi", "hd3_max_db",
                                         "power_max_w", "eye_height_min_v",
                                         "eye_width_min_ui")}}

        for method in ("policy", "cem"):
            t0 = time.perf_counter()
            with EvalLog(f"deploy_{sid}_{method}",
                         extra={"spec_id": sid, "method": method}) as log:
                obj = Objective(spec, sim, corners=corners, robust="min",
                                do_eye=True, jobs=a.jobs, use_channel=True)
                if method == "policy":
                    out = run_policy(model, sur, obj, spec, a.budget,
                                     a.max_steps)
                else:
                    out = run_cem(obj, spec, a.budget, sur)
                for r in obj.history:
                    for cname, sc in r.per_corner.items():
                        c = next(c for c in corners if c.name == cname)
                        log.append(r.u, c, sc["metrics"], meas,
                                   params=r.params.as_dict(), note=method)
            wall = time.perf_counter() - t0
            solved = bool(out["res"] is not None
                          and obj.all_pass_everywhere(out["res"]))
            row[method] = {"sims": out["sims"], "solved_search_corners": solved,
                           "best_reward": out["best_reward"], "u": out["u"],
                           "wall_s": wall, "launches": obj.n_launches}
            print(f"  {method:6}: {out['sims']:3} sims, reward "
                  f"{out['best_reward']:.4f}, "
                  f"{'PASS' if solved else 'fail'} at search corners "
                  f"[{wall/60:.1f}m]", flush=True)

            if a.verify and out["res"] is not None:
                row[method]["pvt"] = verify(out["u"], spec, sim, a.jobs, sid)
                v = row[method]["pvt"]
                print(f"          20-corner: "
                      f"{v['brief_specs_passed']}/8 specs, "
                      f"{'PASS' if v['all_pass_everywhere'] else 'fail'}, "
                      f"binding {v['binding'][0]} {v['binding'][1]:+.1%}",
                      flush=True)

        rows = [r for r in rows if r["spec_id"] != sid] + [row]
        rows.sort(key=lambda r: r["spec_id"])
        _write(rows, a)

    _write(rows, a)
    _summarise(rows, a)
    return 0


def _write(rows, a) -> None:
    """Rewritten after every spec, so an interrupted run keeps its work."""
    payload = {"created": time.time(), "git_sha": git_sha(),
               "deploy_seed": DEPLOY_SEED, "budget": a.budget,
               "n_specs": a.n_specs, "specs": rows,
               "note": ("Policy and CEM get the SAME number of SPICE "
                        "evaluations on the SAME spec, both started from the "
                        "same analytic design. Specs are drawn with a seed "
                        "disjoint from training and evaluation.")}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=float))
    os.replace(tmp, OUT)


def _summarise(rows, a) -> None:
    if not rows:
        print("\nno specs completed")
        return
    print(f"\n{'=' * 74}\nG3  policy vs CEM at an equal budget of {a.budget} "
          f"SPICE evaluations\n{'=' * 74}")
    print(f"  {'spec':7} {'policy':>22} {'CEM':>22}")
    print(f"  {'':7} {'reward  sims  20-corner':>22} "
          f"{'reward  sims  20-corner':>22}")
    wins = {"policy": 0, "cem": 0, "tie": 0}
    for r in rows:
        cells = []
        for m in ("policy", "cem"):
            d = r.get(m, {})
            pv = d.get("pvt", {})
            tag = ("PASS" if pv.get("all_pass_everywhere")
                   else f"{pv.get('brief_specs_passed', '-')}/8")
            cells.append(f"{d.get('best_reward', float('nan')):6.4f} "
                         f"{d.get('sims', 0):5} {tag:>10}")
        pr = r.get("policy", {}).get("best_reward", -np.inf)
        cr = r.get("cem", {}).get("best_reward", -np.inf)
        wins["policy" if pr > cr else "cem" if cr > pr else "tie"] += 1
        print(f"  {r['spec_id']:7} {cells[0]:>22} {cells[1]:>22}")
    n = len(rows)
    pol_solved = sum(1 for r in rows
                     if r.get("policy", {}).get("pvt", {}).get("all_pass_everywhere"))
    cem_solved = sum(1 for r in rows
                     if r.get("cem", {}).get("pvt", {}).get("all_pass_everywhere"))
    print(f"\n  solved at all 20 PVT corners: policy {pol_solved}/{n}, "
          f"CEM {cem_solved}/{n}")
    print(f"  higher reward:                policy {wins['policy']}, "
          f"CEM {wins['cem']}, tie {wins['tie']}")
    print(f"\n  wrote {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
