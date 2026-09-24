"""Does the framework SOLVE each spec? Stops at the first proposal that passes.

best_of_n.py verifies every proposal at 20 corners to build a coverage curve,
which costs ~80 verifications for 10 specs. If the question is only "is this
spec solved", the first pass ends it -- roughly 15 verifications instead.

Reports coverage (solved/10) and rank of first success. Not the per-proposal
rate: that needs every proposal checked, which is the expensive thing.
"""
import sys, time, json, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", choices=("shipped", "systematic", "physfit"),
                    default="physfit")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--n-specs", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    import eqrl.cache as cache
    OUT = Path(f"/tmp/eqrl_fast_{a.start}/evals"); OUT.mkdir(parents=True, exist_ok=True)
    cache.EVAL_DIR = OUT
    import deploy_policy, eqrl.env_goal
    if a.start == "physfit":
        from eqrl import start_physfit as m; m.patch()
    elif a.start == "systematic":
        from eqrl import start_systematic as m; m.patch()

    import numpy as np
    from eqrl import pdk, surrogate as sg
    from eqrl.spice import Simulator
    from eqrl.env_goal import GoalSpace
    import best_of_n as bon
    # the SAME spec generator and seed best_of_n uses, or the comparison is
    # against a different set of specifications and means nothing
    from deploy_policy import DEPLOY_SEED, deploy_specs, verify
    from stable_baselines3 import SAC

    sur = sg.load_latest()
    latest = (ROOT / "models/policy/LATEST").read_text().strip()
    ck = ROOT / latest if (ROOT / latest).exists() else Path(latest)
    model = SAC.load(str(ck), device="cpu")
    sim = Simulator(f"/tmp/eqrl_fast_{a.start}", use_channel=True)
    space = GoalSpace.load()
    specs = deploy_specs(space, a.n_specs, DEPLOY_SEED)
    corners = pdk.robust_search_corners()
    cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp, "rc": c.rc}
           for c in corners]

    print(f"start: {a.start.upper()}   stop-at-first-pass\n")
    rows, t0 = [], time.perf_counter()
    for i, spec in enumerate(specs):
        tp = time.perf_counter()
        props = bon.propose_policy(model, spec, sur, cfs, a.k, 20, seed0=i * 100)
        prop_s = time.perf_counter() - tp
        rank, nver = None, 0
        for j, p in enumerate(props):
            nver += 1
            pvt = verify(p["u"], spec, sim, a.jobs, f"fast_{a.start}_s{i}")
            if pvt["all_pass_everywhere"]:
                rank = j + 1
                break
        rows.append({"spec": i, "rank": rank, "verifications": nver,
                     "propose_s": round(prop_s, 2)})
        print(f"  spec{i}: {'SOLVED at rank '+str(rank) if rank else 'UNSOLVED'}"
              f"   ({nver} verifications, propose {prop_s:.1f}s)", flush=True)

    solved = sum(1 for r in rows if r["rank"])
    tot = sum(r["verifications"] for r in rows)
    el = time.perf_counter() - t0
    print(f"\n  solved {solved}/{a.n_specs}   {tot} verifications   {el/60:.1f} min")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"start": a.start, "solved": solved, "n_specs": a.n_specs,
             "verifications": tot, "wall_s": round(el, 1), "specs": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
