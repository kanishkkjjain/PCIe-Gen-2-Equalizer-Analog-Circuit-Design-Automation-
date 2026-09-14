#!/usr/bin/env python3
"""Choose the spec distribution the policy is trained on, from evidence.

Picking goal ranges by eye is a trap that costs hours and is invisible when you
fall into it.  If a large share of sampled specs ask for something this
topology cannot deliver, then "the policy solves 80 % of held-out specs" is
impossible by construction -- and the natural response, tuning PPO harder, is
wasted effort against a problem with no solution.  The shipped design reaches
hd3 = -31.2 dBc and eye height 131 mV at worst case, so a goal space reaching
to -40 dBc or 150 mV would be mostly wishes.

So the ranges are measured instead.  Against the collected designs -- each
scored worst-over-corners, exactly as the search ranks them -- this samples
goals and asks how many real, already-simulated designs satisfy each one.  A
goal that nothing satisfies is not a hard goal, it is an impossible one.

The space is then shrunk towards its centre until enough sampled goals are
demonstrably reachable, and written to `results/goal_space.json`.  Training,
the held-out evaluation and deployment all load that same file, so their
numbers are comparable -- which they would not be if each drew its own ranges.

  python3 scripts/calibrate_goals.py
  python3 scripts/calibrate_goals.py --target-frac 0.7 --n-goals 600
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import specs as sp
from eqrl.cache import git_sha, load
from eqrl.env_goal import GOAL_SPACE_PATH, GoalSpace
from eqrl.spice import MeasCond, Metrics

ROOT = Path(__file__).resolve().parents[1]

#: the tuple-valued fields that get shrunk
RANGE_FIELDS = ("peaking_target_db", "fpeak_lo", "fpeak_hi", "hd3_max_db",
                "power_max_w", "eye_height_min_v", "eye_width_min_ui")


def worst_by_design(records) -> list:
    """One Metrics per design: the worst corner for each metric separately.

    Per-metric worst rather than the worst corner overall, because no single
    corner is worst at everything -- the same reason `design._worst_margins`
    exists. A design "satisfies" a goal only if it does so everywhere.
    """
    by: dict = {}
    for r in records:
        if not r["metrics"].get("ok"):
            continue
        by.setdefault(tuple(r["key"]), []).append(r["metrics"])

    worse_is_bigger = {"hd3_db", "vn_in_rms", "power_w", "area_mm2", "f_peak"}
    out = []
    for k, ms in by.items():
        if len(ms) < 2:
            continue
        agg = {}
        for f in Metrics.__dataclass_fields__:
            vals = [m.get(f) for m in ms]
            nums = [v for v in vals if isinstance(v, (int, float))
                    and v == v]
            if not nums:
                agg[f] = ms[0].get(f)
                continue
            agg[f] = max(nums) if f in worse_is_bigger else min(nums)
        agg["ok"] = all(m.get("ok") for m in ms)
        agg["saturated"] = all(m.get("saturated") for m in ms)
        agg["channel_present"] = True
        out.append(Metrics(**{kk: vv for kk, vv in agg.items()
                             if kk in Metrics.__dataclass_fields__}))
    return out


def feasibility(space: GoalSpace, designs, n_goals: int, seed: int,
                min_designs: int) -> dict:
    """What fraction of sampled goals at least `min_designs` designs satisfy.

    `designs` must already be restricted to designs biased in SATURATION at
    every corner. A goal "satisfied" by a design that is not actually operating
    as an amplifier is not evidence that the goal is reachable -- the metrics
    that pass on paper there do not mean what they say.
    """
    rng = np.random.default_rng(seed)
    counts = []
    for _ in range(n_goals):
        g = space.sample(rng)
        n = 0
        for m in designs:
            s = sp.score(m, g)
            if all(s["pass"].get(k) for k in sp.BRIEF_SPECS):
                n += 1
                if n >= min_designs:
                    break
        counts.append(n)
    counts = np.asarray(counts)
    return {"frac_feasible": float(np.mean(counts >= min_designs)),
            "frac_any": float(np.mean(counts > 0)),
            "median_satisfying": float(np.median(counts)),
            "n_goals": n_goals, "min_designs": min_designs}


def shrink(space: GoalSpace, factor: float) -> GoalSpace:
    """Pull every range towards its own centre by `factor`."""
    d = space.to_dict()
    for f in RANGE_FIELDS:
        lo, hi = d[f]
        mid = 0.5 * (lo + hi)
        d[f] = (mid + (lo - mid) * factor, mid + (hi - mid) * factor)
    return GoalSpace.from_dict(d)


def relax(space: GoalSpace, step: float) -> GoalSpace:
    """Move the HARD edge of each one-sided range inwards (easier goals).

    Which edge is the hard one is a property of the spec, not of the number: a
    bigger `eye_height_min` is harder, a more negative `hd3_max` is harder. The
    hard edge is moved towards the easy one and CLAMPED before it crosses --
    without the clamp `hd3_max_db` walked from (-34, -26) past its own upper
    bound and numpy refused to sample the inverted range.
    """
    d = space.to_dict()

    def ease(field, hard_is_low: bool):
        lo, hi = d[field]
        span = abs(hi - lo)
        if hard_is_low:                       # raise the floor towards hi
            lo = min(lo + step * span, hi - 1e-9 * max(1.0, abs(hi)))
        else:                                 # lower the ceiling towards lo
            hi = max(hi - step * span, lo + 1e-9 * max(1.0, abs(lo)))
        d[field] = (lo, hi)

    ease("hd3_max_db", hard_is_low=True)        # -34 is harder than -26
    ease("eye_height_min_v", hard_is_low=False)  # 0.13 is harder than 0.06
    ease("eye_width_min_ui", hard_is_low=False)
    ease("power_max_w", hard_is_low=True)        # 3 mW is harder than 15 mW
    return GoalSpace.from_dict(d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="collect",
                    help="collection tag, or several comma-separated. The goal-start cloud lives under its own tag, and a surrogate that has not seen it is wrong exactly where the policy starts.")
    ap.add_argument("--n-goals", type=int, default=400)
    ap.add_argument("--min-designs", type=int, default=1,
                    help="how many collected designs must satisfy a goal for "
                         "it to count as demonstrably reachable. 1, not 3: "
                         "the collection is dominated by Sobol samples over "
                         "the whole box, so demanding three independent "
                         "solutions per goal was unreachable at every range "
                         "tried (0 %% at every round while 61-77 %% of goals "
                         "had one). One real solution is the honest bar for "
                         "'this is not impossible'.")
    ap.add_argument("--target-frac", type=float, default=0.6)
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--out", default=str(GOAL_SPACE_PATH))
    a = ap.parse_args()

    meas = MeasCond.from_spec(sp.EqualizerSpec())
    tags = [t.strip() for t in a.tag.split(",") if t.strip()]
    recs = list(load(tags, meas=meas).records.values())
    all_designs = worst_by_design(recs)
    designs = [m for m in all_designs if m.saturated]
    print(f"{len(recs)} records -> {len(all_designs)} designs with >=2 corners, "
          f"{len(designs)} of them saturated at every corner")
    if len(designs) < 50:
        print("too few designs to calibrate against; let collection run further")
        return 1

    n_pass = sum(1 for m in designs
                 if all(sp.score(m, sp.EqualizerSpec())["pass"].get(k)
                        for k in sp.BRIEF_SPECS))
    print(f"  {n_pass} of them already meet the ORIGINAL brief at every corner")

    space = GoalSpace()
    history = []
    for rnd in range(a.max_rounds):
        f = feasibility(space, designs, a.n_goals, a.seed + rnd, a.min_designs)
        history.append({"round": rnd, **f, "space": space.to_dict()})
        print(f"  round {rnd}: {f['frac_feasible']:.1%} of goals have "
              f">={a.min_designs} satisfying designs "
              f"(any: {f['frac_any']:.1%}, median {f['median_satisfying']:.0f})")
        if f["frac_feasible"] >= a.target_frac:
            break
        # first try making the goals easier, then narrow the spread
        space = relax(space, 0.10) if rnd % 2 == 0 else shrink(space, 0.85)

    final = feasibility(space, designs, a.n_goals, a.seed + 99, a.min_designs)
    space = dataclasses.replace(
        space, feasible_frac=final["frac_feasible"],
        note=(f"calibrated {time.strftime('%Y-%m-%d')} against {len(designs)} "
              f"collected designs; {final['frac_feasible']:.0%} of sampled "
              f"goals are satisfied by >= {a.min_designs} of them. Ranges are "
              f"evidence about what this topology can do, not preferences."))
    space.save(a.out)

    print(f"\n  final feasibility {final['frac_feasible']:.1%} "
          f"(any: {final['frac_any']:.1%})")
    print(f"  wrote {a.out}")
    for f in RANGE_FIELDS:
        lo, hi = getattr(space, f)
        print(f"    {f:20} {lo:12.4g} .. {hi:12.4g}")
    Path(ROOT / "results" / "goal_space_calibration.json").write_text(
        json.dumps({"created": time.time(), "git_sha": git_sha(),
                    "n_designs": len(designs), "history": history,
                    "final": final}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
