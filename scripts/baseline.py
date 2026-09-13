#!/usr/bin/env python3
"""Measure CEM against a random-search baseline at an EQUAL evaluation budget.

The Problem Statement asks for a flow that "sizes devices using fewer search
points (lowest design time)" and that "should take significantly lower time than
sweeping all MOS, R, C, L parameter space". That claim had never been measured.

Two comparisons, because they answer different questions:

1. **Exhaustive sweep** -- arithmetic, not simulated. A grid over 11 variables
   at k points per variable is k**11 designs. At 5 points that is 48.8 million,
   which at the measured cost per evaluation is millennia. This is the brief's
   literal framing and it is not a close contest.

2. **Random search at the SAME budget** -- this is the comparison a reviewer
   will actually ask for, and the only one that can embarrass the method. Draw
   the same number of candidates uniformly from the same box, score them the
   same way, and report the best. If CEM cannot beat that, the search is not
   doing anything.

  python3 scripts/baseline.py --budget 189
  python3 scripts/baseline.py --budget 189 --channel --eye
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import netlist as nl, pdk, specs as sp
from eqrl.env import Objective
from eqrl.spice import MeasCond, Simulator


def _human_time(seconds: float) -> str:
    """Pick a unit a reader can feel. Formatting everything in years printed
    "0 years" for the 3-point grid, which reads as free rather than as 9 days."""
    for div, unit in ((1, "s"), (60, "min"), (3600, "hours"), (86400, "days"),
                      (86400 * 365.25, "years")):
        if seconds / div < 1000:
            return f"{seconds/div:,.1f} {unit}"
    return f"{seconds/(86400*365.25):,.0f} years"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=189,
                    help="candidates to draw; match the CEM run being compared")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--channel", action="store_true")
    ap.add_argument("--eye", action="store_true", default=True)
    ap.add_argument("--no-eye", dest="eye", action="store_false")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--workdir", default="/tmp/eqrl_baseline")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1]
                                         / "results" / "baseline.json"))
    a = ap.parse_args()

    spec = sp.EqualizerSpec()
    if not a.eye:
        import dataclasses
        spec = dataclasses.replace(spec, require_eye=False)
    sim = Simulator(a.workdir, use_channel=a.channel,
                    meas=MeasCond.from_spec(spec))
    corners = [pdk.Corner("tt", pdk.VDD_NOM, 27.0),
               pdk.Corner("ss", pdk.VDD_CORNERS["lo"], 125.0),
               pdk.Corner("ff", pdk.VDD_CORNERS["hi"], 0.0),
               pdk.Corner("tt", pdk.VDD_NOM, 27.0, rc="ll")]
    obj = Objective(spec, sim, corners=corners, robust="min",
                    do_eye=a.eye, jobs=a.jobs, use_channel=a.channel)

    n = len(nl.PARAM_NAMES)
    rng = np.random.default_rng(a.seed)
    us = [list(rng.random(n)) for _ in range(a.budget)]

    print(f"random search: {a.budget} candidates x {len(corners)} corners, "
          f"channel={'on' if a.channel else 'off'}")
    t0 = time.perf_counter()
    results = obj.evaluate_batch(us)
    wall = time.perf_counter() - t0

    best = max(results, key=lambda r: r.reward)
    # all_pass_everywhere, not all_pass(r.scored): the latter tests only the
    # WORST-REWARD corner, and a candidate can pass there while failing a spec
    # at another. This is the headline statistic of the whole comparison, so it
    # has to be the strict one -- cem._passes_everywhere already uses it.
    n_pass = sum(1 for r in results if obj.all_pass_everywhere(r))
    print(f"\n  best reward      : {best.reward:.4f}")
    print(f"  candidates that pass every spec at every search corner: "
          f"{n_pass}/{a.budget}")
    print(f"  ngspice launches : {obj.n_launches}")
    print(f"  wall clock       : {wall/60:.1f} min")

    # the brief's literal framing, as arithmetic
    per_eval = wall / max(1, a.budget)
    print("\n  exhaustive grid over the same 11 variables:")
    for k in (3, 5, 10):
        total = k ** n
        print(f"    {k:2d} points/variable = {total:,} designs "
              f"= {_human_time(total * per_eval)} at this cost")

    rep = {"method": "random", "budget": a.budget, "seed": a.seed,
           "channel": a.channel, "eye": a.eye,
           "best_reward": best.reward, "n_all_pass": n_pass,
           "n_ngspice_launches": obj.n_launches, "wall_s": wall,
           "best_params": best.params.as_dict(),
           "rewards": sorted((r.reward for r in results), reverse=True)[:20]}
    Path(a.out).write_text(json.dumps(rep, indent=2, default=str))
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
