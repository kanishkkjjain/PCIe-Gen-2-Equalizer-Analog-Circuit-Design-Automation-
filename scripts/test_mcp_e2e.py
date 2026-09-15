#!/usr/bin/env python3
"""End-to-end test of the MCP design flow against real ngspice.

WHY THIS IS A COMMITTED SCRIPT AND NOT A HEREDOC. The cautionary tale in this
project is `results/best_of_n.json`: a headline number measured by an
uncommitted heredoc, impossible to reproduce afterwards, and eventually
retracted because nobody could tell what it had actually measured. Once was
enough. This writes a dated artifact and can be re-run.

WHY THESE SPECS. The ten seed-999 deployment specs have a KNOWN expected
outcome: `results/best_of_n_policy.json` records that the policy solves eight
of them and fails specs 5 and 8. So one run measures the success path and
drives both failure branches naturally, with a per-spec expectation rather than
"whatever happens". A spec that disagrees with the shipped artifact is a
finding about the server, not noise -- which is the difference between a test
and a demo.

Three out-of-envelope specs exercise the CEM route, which had never run at all,
and one spec is repeated to check determinism.

  python3 scripts/test_mcp_e2e.py --jobs 10
  python3 scripts/test_mcp_e2e.py --quick        # 3 specs, for a smoke check

Exits non-zero if any assertion fails, so "it ran" and "it passed" cannot be
confused for one another.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = ROOT / "results" / "mcp_e2e.json"

#: Deliberately outside the policy's trained goal space. Each must route to the
#: CEM search and name the fields that were outside.
OUT_OF_ENVELOPE = [
    {"peaking_db": 11.5},                       # above the trained peaking band
    {"eye_height_min_mv": 150},                 # above the trained eye height
    {"power_max_mw": 2.0},                      # below the trained power floor
]


def expected_outcomes() -> dict[int, bool]:
    """What the shipped artifact says the policy achieves, per spec."""
    f = ROOT / "results" / "best_of_n_policy.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text())
    return {r["spec"]: bool(r["first_success_rank"]) for r in d["specs"]}


def spec_kwargs(spec) -> dict:
    """An EqualizerSpec back into the MCP tool's human-facing arguments."""
    return {
        "peaking_db": spec.peaking_target_db,
        "fpeak_lo_ghz": spec.fpeak_lo / 1e9,
        "fpeak_hi_ghz": spec.fpeak_hi / 1e9,
        "hd3_max_db": spec.hd3_max_db,
        "noise_max_mvrms": spec.vn_in_max_rms * 1e3,
        "power_max_mw": spec.power_max_w * 1e3,
        "eye_height_min_mv": spec.eye_height_min_v * 1e3,
        "eye_width_min_ui": spec.eye_width_min_ui,
    }


def check_plots_are_real(r: dict) -> list[str]:
    """A returned path that points at nothing is worse than no path at all.

    The caller shows the human an image; if the file is empty or not a PNG they
    see a broken box and have no idea whether the design is wrong or the plot
    is. Checked as bytes, not as "the key exists".
    """
    bad = []
    for key in ("eye_png", "ac_png"):
        p = r.get(key)
        if not p:
            bad.append(f"{key} missing")
            continue
        f = Path(p)
        if not f.exists():
            bad.append(f"{key} path does not exist")
        elif f.stat().st_size < 5000:
            bad.append(f"{key} is only {f.stat().st_size} bytes")
        elif f.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            bad.append(f"{key} is not a PNG")
    return bad


def check_no_unverified_claim(r: dict) -> list[str]:
    """The rule the whole wrapper exists to keep.

    A pass must have been established at all 20 corners. A failure must still
    hand back a design and say what it missed -- "no design" with no artifact
    helps nobody, and a failure with an empty `failed_specs` is a claim with
    nothing behind it.
    """
    bad = []
    if not r.get("ok"):
        return bad                       # the no-design path, checked elsewhere
    if r["all_pass"]:
        if "20" not in str(r.get("specs_passed", "")):
            bad.append(f"all_pass True but corners not stated: "
                       f"{r.get('specs_passed')!r}")
        if r.get("failed_specs"):
            bad.append(f"all_pass True while reporting failures: "
                       f"{r['failed_specs']}")
    else:
        if not r.get("sized_parameters"):
            bad.append("failed without returning a design")
        if not r.get("failed_specs"):
            bad.append("failed without naming which specs failed")
    return bad


def run_one(label, kwargs, jobs) -> dict:
    from eqrl.mcp_server import design_equalizer
    t0 = time.perf_counter()
    try:
        r = design_equalizer(jobs=jobs, **kwargs)
        err = None
    except Exception as e:                                # noqa: BLE001
        r, err = {}, f"{type(e).__name__}: {e}"
    wall = time.perf_counter() - t0
    row = {"label": label, "request": kwargs, "wall_s": round(wall, 1),
           "error": err}
    if err:
        return row
    row.update({
        "ok": r.get("ok"), "all_pass": r.get("all_pass"),
        "method": r.get("method"), "in_envelope": r.get("in_envelope"),
        "outside": sorted(r.get("outside_trained_range") or {}),
        "reported_wall_s": r.get("wall_s"),
        "attempts": r.get("attempts"),
        "binding": r.get("binding_spec"),
        "failed_specs": r.get("failed_specs"),
        "artifact_dir": r.get("artifact_dir"),
        "eye_png": r.get("eye_png"), "ac_png": r.get("ac_png"),
        "netlist_path": r.get("netlist_path"),
        "sized_parameters": r.get("sized_parameters"),
        "problems": check_no_unverified_claim(r) + (
            check_plots_are_real(r) if r.get("ok") else []),
    })
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--quick", action="store_true",
                    help="3 specs only, for a smoke check")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    from eqrl.env_goal import GoalSpace
    from deploy_policy import DEPLOY_SEED, deploy_specs

    expect = expected_outcomes()
    specs = deploy_specs(GoalSpace.load(), 10, DEPLOY_SEED)
    if a.quick:
        specs = specs[:3]

    rows, t_start = [], time.perf_counter()

    print(f"=== {len(specs)} deployment specs (expected outcome known) ===",
          flush=True)
    for i, s in enumerate(specs):
        row = run_one(f"spec{i}", spec_kwargs(s), a.jobs)
        row["expected_pass"] = expect.get(i)
        row["matches_artifact"] = (row.get("all_pass") == expect.get(i)
                                   if expect.get(i) is not None else None)
        rows.append(row)
        print(f"  spec{i}: {'PASS' if row.get('all_pass') else 'fail'}"
              f"  expected {'PASS' if expect.get(i) else 'fail'}"
              f"  {row['wall_s']:5.1f} s  method={row.get('method')}"
              f"  {'' if row.get('matches_artifact', True) else '  <-- DISAGREES'}"
              f"{'  PROBLEMS: ' + str(row['problems']) if row.get('problems') else ''}",
              flush=True)

    if not a.quick:
        print("\n=== out-of-envelope (must route to CEM) ===", flush=True)
        for j, kw in enumerate(OUT_OF_ENVELOPE):
            row = run_one(f"ooe{j}", kw, a.jobs)
            ok_route = (row.get("in_envelope") is False
                        and "cem" in str(row.get("method", "")).lower()
                        and bool(row.get("outside")))
            row["routing_correct"] = ok_route
            rows.append(row)
            print(f"  ooe{j} {kw}: method={row.get('method')} "
                  f"in_envelope={row.get('in_envelope')} "
                  f"outside={row.get('outside')} "
                  f"{row['wall_s']:5.1f} s  "
                  f"{'ROUTING OK' if ok_route else 'ROUTING WRONG'}",
                  flush=True)

        print("\n=== determinism + artifact isolation ===", flush=True)
        kw = spec_kwargs(specs[0])
        r1 = run_one("repeat_a", kw, a.jobs)
        kw2 = spec_kwargs(specs[2])
        r2 = run_one("other_spec", kw2, a.jobs)
        r3 = run_one("repeat_b", kw, a.jobs)
        rows += [r1, r2, r3]

        same = r1.get("sized_parameters") == r3.get("sized_parameters")
        # the first call's artifacts must survive two later calls
        isolated = bool(r1.get("eye_png") and Path(r1["eye_png"]).exists()
                        and r1.get("artifact_dir") != r2.get("artifact_dir")
                        and r1.get("artifact_dir") != r3.get("artifact_dir"))
        print(f"  determinism (same request twice -> same design): "
              f"{'PASS' if same else 'FAIL'}")
        print(f"  artifact isolation (3 calls, distinct dirs, first survives): "
              f"{'PASS' if isolated else 'FAIL'}")
    else:
        same = isolated = None

    # ---- metrics ---------------------------------------------------------
    dep = [r for r in rows if r["label"].startswith("spec")]
    walls = [r["wall_s"] for r in dep if not r.get("error")]
    passed = sum(1 for r in dep if r.get("all_pass"))
    agree = [r for r in dep if r.get("matches_artifact") is False]
    problems = [(r["label"], r["problems"]) for r in rows if r.get("problems")]
    ooe = [r for r in rows if r["label"].startswith("ooe")]
    bad_route = [r["label"] for r in ooe if not r.get("routing_correct")]

    print("\n" + "=" * 68)
    print(f"SPECS      {passed}/{len(dep)} passed 20-corner verification"
          + (f"   (artifact expects {sum(1 for v in expect.values() if v)})"
             if expect else ""))
    if walls:
        print(f"TIMING     median {st.median(walls):.1f} s   "
              f"min {min(walls):.1f}   max {max(walls):.1f}   "
              f"p90 {sorted(walls)[int(0.9*len(walls))-1]:.1f}")
    pol = sum(1 for r in dep if "policy" in str(r.get("method", "")))
    print(f"ROUTING    {pol} via the policy, {len(dep)-pol} via CEM "
          f"(deployment specs)")
    print(f"           out-of-envelope routed correctly: "
          f"{len(ooe)-len(bad_route)}/{len(ooe)}")
    print(f"ROBUSTNESS determinism        "
          f"{'PASS' if same else ('n/a' if same is None else 'FAIL')}")
    print(f"           artifact isolation "
          f"{'PASS' if isolated else ('n/a' if isolated is None else 'FAIL')}")
    print(f"           no unverified claim, plots decode: "
          f"{'PASS' if not problems else 'FAIL'}")
    for lbl, ps in problems:
        print(f"             {lbl}: {ps}")
    if agree:
        print(f"DISAGREES WITH THE SHIPPED ARTIFACT: "
              f"{[r['label'] for r in agree]}")
    print(f"TOTAL      {(time.perf_counter()-t_start)/60:.1f} min")
    print("=" * 68)

    Path(a.out).write_text(json.dumps(
        {"created": time.time(), "jobs": a.jobs, "quick": a.quick,
         "n_specs": len(dep), "n_passed": passed,
         "determinism": same, "artifact_isolation": isolated,
         "routing_failures": bad_route, "problems": problems,
         "disagrees": [r["label"] for r in agree], "rows": rows},
        indent=2, default=str))
    print(f"wrote {a.out}")

    failed = bool(problems or bad_route or agree
                  or (same is False) or (isolated is False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
