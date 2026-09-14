#!/usr/bin/env python3
"""How much does the analytic seed give, and how much does the RL search add?

The project could not answer this. The run log records the POPULATION best per
iteration, never the seed's own score, and report.json stores only the winner.
The figure that looked like an answer -- HANDOVER 5's "box centre scores 0.522
(5/9), analytic scores 0.999 (9/9)" -- was stale three ways: typical corner
only, the old merged topology, and the OLD SCORER (before HD3 moved to the
slicer, before the `equalises` check, before the soft widths became relative).

It matters more than it used to. The binding spec is now HD3, and analytic.py is
a small-signal AC solve with no model of distortion, noise, eye or power: it
computes 4 of the 11 knobs (RL, Rs, Cs, Rsum) and pins the other 7 to constants.
The constraint that decides the design is one the equations cannot see.

Three design points, scored through the SAME Objective, the same corners and the
same scorer the search itself used, so the numbers are directly comparable to
report.json:

    blind start    centre of the normalised box -- what you get with no theory
    analytic seed  eqrl/analytic.py, recomputed live rather than hardcoded
    after CEM      results/report.json::final_params -- the shipped design

  python3 scripts/seed_benchmark.py
  python3 scripts/seed_benchmark.py --no-eye      # AC-only, much faster

NOTE ON WHAT THIS DOES NOT MEASURE. It compares the seed against the WHOLE
search. It does not isolate the CEM operator from the seed -- that needs a
SEEDED random search at equal budget, and the committed baseline.py run is
unseeded. See HANDOVER 11.
"""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import analytic, netlist as nl, pdk, specs as sp
from eqrl.design import _worst_margins
from eqrl.env import Objective
from eqrl.netlist import PARAM_NAMES
from eqrl.spice import MeasCond, Simulator

ROOT = Path(__file__).resolve().parents[1]


def _search_corners() -> list:
    """The same 4-corner robust set design.run_design(search_robust=True) uses.

    Kept identical on purpose: a reward measured over a different corner set is
    not comparable to the one report.json quotes.
    """
    return [
        pdk.Corner("tt", pdk.VDD_NOM, 27.0),
        pdk.Corner("ss", pdk.VDD_CORNERS["lo"], 125.0),
        pdk.Corner("ff", pdk.VDD_CORNERS["hi"], 0.0),
        pdk.Corner("tt", pdk.VDD_NOM, 27.0, rc="ll"),
    ]


def _row(name: str, res, spec: sp.EqualizerSpec, obj: Objective) -> dict:
    """One design point's scorecard over whatever grid `obj` was built with.

    Margins are the worst PER SPEC across every corner -- `design._worst_margins`,
    the same function report.json uses. Taking them all from the single
    worst-REWARD corner instead (the obvious thing) gives a different answer:
    measured, it named `fpeak +6.9%` as binding where report.json says
    `hd3 +4.1%`, because no one corner is worst at everything. Reusing the
    function rather than reimplementing it is what keeps this comparable to the
    report -- which is the whole point of the exercise.
    """
    m = res.scored["metrics"]
    # aggregate pass/fail across ALL corners, not just the worst-reward one
    agg: dict = {}
    for sc in res.per_corner.values():
        for k, v in sc["pass"].items():
            agg[k] = agg.get(k, True) and bool(v)
    mg = _worst_margins(res, spec)
    binding = min(mg, key=lambda k: mg[k]) if mg else "none"
    return {
        "name": name,
        "reward_worst_corner": res.reward,
        "reward_ungated": min(sc.get("reward_raw", sc["reward"])
                              for sc in res.per_corner.values()),
        "worst_corner": res.scored_corner,
        "all_pass_everywhere": obj.all_pass_everywhere(res),
        "brief_specs_passed": sum(1 for k in sp.BRIEF_SPECS if agg.get(k)),
        "brief_specs_total": len(sp.BRIEF_SPECS),
        "failed": [k for k in sorted(agg) if not agg[k]],
        "binding": [binding, mg.get(binding, float("nan"))],
        "margins": mg,
        "metrics": m.as_dict(),
        "params": res.params.as_dict(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eye", dest="eye", action="store_true", default=True)
    ap.add_argument("--no-eye", dest="eye", action="store_false",
                    help="AC-only: much faster, but the highest-weighted spec "
                         "is then not measured at all")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--workdir", default="/tmp/eqrl_seedbench")
    ap.add_argument("--out", default=str(ROOT / "results" / "seed_vs_search.json"))
    a = ap.parse_args()

    spec = sp.EqualizerSpec()
    if not a.eye:
        spec = dataclasses.replace(spec, require_eye=False)
    # meas MUST come from the spec, or this silently measures at default
    # conditions while claiming to reproduce report.json (see HANDOVER bug 28).
    sim = Simulator(a.workdir, use_channel=True, timeout=900,
                    meas=MeasCond.from_spec(spec))

    # --- the three design points -------------------------------------------
    u_blind = [0.5] * len(PARAM_NAMES)

    print("=== analytic initialisation (recomputed, not hardcoded) ===")
    t0 = time.perf_counter()
    u_seed = analytic.initial_u(spec, sim, verbose=True)
    t_seed = time.perf_counter() - t0

    rep_path = ROOT / "results" / "report.json"
    if not rep_path.exists():
        print("no results/report.json -- run scripts/run_design.py first")
        return 1
    final = json.loads(rep_path.read_text())["final_params"]
    u_final = nl.norm({k: final[k] for k in PARAM_NAMES})

    points = [("blind start (box centre)", u_blind),
              ("analytic seed", u_seed),
              ("after CEM", u_final)]

    # --- score them the way the SEARCH did, then the way VERIFY did ---------
    search = Objective(spec, sim, corners=_search_corners(), robust="min",
                       do_eye=a.eye, jobs=a.jobs, use_channel=True)
    verify = Objective(spec, sim, corners=pdk.pvt_grid("spec"), robust="min",
                       do_eye=a.eye, jobs=a.jobs, use_channel=True)

    print(f"\n=== scoring 3 points x {len(search.corners)} search corners ===")
    t0 = time.perf_counter()
    s_res = search.evaluate_batch([u for _, u in points])
    print(f"=== scoring 3 points x {len(verify.corners)} PVT corners ===")
    v_res = verify.evaluate_batch([u for _, u in points])
    wall = time.perf_counter() - t0 + t_seed

    rows = []
    for (name, _), sr, vr in zip(points, s_res, v_res):
        rows.append({**_row(name, sr, spec, search),
                     "pvt": _row(name, vr, spec, verify)})

    # --- print --------------------------------------------------------------
    print("\n" + "=" * 78)
    print("SEED vs SEARCH   (reward = worst of the 4 search corners, as ranked)")
    print("=" * 78)
    print(f"  {'design point':26} {'reward':>8} {'ungated':>8} {'brief':>7} "
          f"{'20-corner':>10} {'binding':>17}")
    for r in rows:
        p = r["pvt"]
        print(f"  {r['name']:26} {r['reward_worst_corner']:8.4f} "
              f"{r['reward_ungated']:8.4f} "
              f"{p['brief_specs_passed']}/{p['brief_specs_total']:<5} "
              f"{'PASS' if p['all_pass_everywhere'] else 'fail':>10} "
              f"{p['binding'][0]:>10} {p['binding'][1]:+6.1%}")
    print("\n  `reward` is CLIPPED by the validity gates, so two invalid designs")
    print("  can tie at 0.30 however different they are; `ungated` is the raw")
    print("  weighted score and is what separates them.")
    print()
    for r in rows:
        p = r["pvt"]
        if p["failed"]:
            print(f"  {r['name']}: fails {', '.join(p['failed'])} over 20 corners")
    seed_r = rows[1]["reward_ungated"]; final_r = rows[2]["reward_ungated"]
    print(f"\n  ungated reward, analytic seed -> after CEM : {seed_r:.4f} -> "
          f"{final_r:.4f} ({(final_r - seed_r):+.4f})")
    print(f"  brief specs over 20 PVT corners    : "
          f"{rows[1]['pvt']['brief_specs_passed']}/8 -> "
          f"{rows[2]['pvt']['brief_specs_passed']}/8")
    print(f"\n  wall clock {wall/60:.2f} min, {sim.n_launches} ngspice launches "
          f"(+{search.n_launches + verify.n_launches} in workers)")
    print("\n  NOT measured here: the CEM operator in isolation. This compares the")
    print("  seed against the WHOLE search; separating them needs a SEEDED random")
    print("  search at equal budget (baseline.py's run is unseeded).")

    out = {
        "eye_scored": a.eye,
        "channel": True,
        "search_corners": [c.name for c in search.corners],
        "pvt_grid": "spec",
        "n_pvt_corners": len(verify.corners),
        "wall_s": wall,
        "n_ngspice_launches": search.n_launches + verify.n_launches + sim.n_launches,
        "rows": rows,
        "caveat": ("Compares the analytic seed against the whole search. Does "
                   "not isolate the CEM operator from the seed -- that needs a "
                   "seeded random search at equal budget."),
    }
    Path(a.out).write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
