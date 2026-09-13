#!/usr/bin/env python3
"""Full-PVT sweep of every spec, with per-run timing.

Answers "Meet across PVT" for ALL metrics, not just the AC ones. Runs corners in
parallel across cores.

IMPORTANT: every worker gets its OWN Simulator workdir. The analyses write fixed
filenames (ac.dat, tran.dat, eye.dat), so sharing a directory across processes
silently corrupts results -- one worker reads another's dump.

  python scripts/pvt_sweep.py                      # spec grid (20 corners)
  python scripts/pvt_sweep.py --grid full --eye    # 60 corners + closed-loop eye
  python scripts/pvt_sweep.py --grid quick         # 9-corner smoke test

The "spec" grid is exactly what the brief asks for. The "full" grid adds the R/C
axis, which sky130 does NOT fold into its named process corners -- and which
moves f_peak more than the process corners do. Quote both.
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import pdk, specs as sp
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator

WORKROOT = Path("/tmp/eqrl_pvtsweep")


def _json_safe(o):
    """json.dumps writes a bare `NaN` token by default, which is not valid JSON
    and which strict parsers reject. Non-finite -> null."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _one(args) -> dict:
    """Evaluate one corner in its own workdir; time each analysis separately.

    This delegates to Simulator.evaluate() rather than re-assembling Metrics by
    hand. The hand-rolled copy that used to live here had already drifted: it
    lacked evaluate()'s analytic power fallback, so a failed op() left power_w
    as NaN and the corner was reported as a POWER SPEC FAILURE rather than a
    failed measurement.
    """
    idx, corner, params, do_eye, use_ch = args
    wd = WORKROOT / f"w{idx:03d}_{corner.lib_section}"
    sim = Simulator(wd, timeout=900, use_channel=use_ch)
    p = CtleParams(**params)

    # Per-analysis timing still needs the individual calls, so time them and
    # let evaluate() reuse the cached dumps for the assembly.
    t = {}
    t0 = time.perf_counter(); sim.ac(p, corner);       t["ac"] = time.perf_counter() - t0
    t0 = time.perf_counter(); sim.op(p, corner);       t["op"] = time.perf_counter() - t0
    t0 = time.perf_counter(); sim.tran_hd3(p, corner); t["hd3"] = time.perf_counter() - t0
    t0 = time.perf_counter(); sim.noise(p, corner);    t["noise"] = time.perf_counter() - t0
    t["eye"] = 0.0
    if do_eye:
        t0 = time.perf_counter(); sim.tran_eye(p, corner); t["eye"] = time.perf_counter() - t0

    m = sim.evaluate(p, corner, do_eye=do_eye)
    scored = sp.score(m, sp.EqualizerSpec())
    return {"corner": corner.name, "lib": corner.lib_section,
            "vdd": corner.vdd, "temp": corner.temp,
            "metrics": m.as_dict(), "pass": scored["pass"],
            "reward": scored["reward"], "time_s": t,
            "time_total_s": sum(t.values()),
            "n_launches": sim.n_launches}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", choices=("quick", "spec", "full"), default="spec",
                    help="quick=9 (smoke), spec=20 (the brief's literal "
                         "cross-product), full=60 (+ the R/C axis)")
    ap.add_argument("--full", action="store_true",
                    help="deprecated alias for --grid full")
    ap.add_argument("--eye", action="store_true", help="include closed-loop eye")
    ap.add_argument("--channel", action="store_true",
                    help="put the lossy channel in front of the equaliser. "
                         "Needed to reproduce run_design's conditions -- "
                         "without it the DFE has no post-cursor to cancel.")
    ap.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 4))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "results"))
    ap.add_argument("--set", action="append", default=[],
                    metavar="K=V", help="override a CtleParams field, e.g. --set Igf=50e-6")
    a = ap.parse_args()

    corners = pdk.pvt_grid("full" if a.full else a.grid)
    params = {k: float(v) for k, v in (x.split("=", 1) for x in a.set)}
    if params:
        print("  param overrides:", params)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    grid = "full" if a.full else a.grid
    print(f"PVT sweep: grid={grid}, {len(corners)} corners, {a.jobs} parallel "
          f"jobs, eye={'on' if a.eye else 'off'}, "
          f"channel={'on' if a.channel else 'off'}")
    if grid == "spec":
        print("  NOTE: spec grid varies MOSFETs only. sky130 keeps R/C on a "
              "separate axis;\n        use --grid full for the real worst case.")

    t_wall = time.perf_counter()
    with Pool(a.jobs) as pool:
        rows = pool.map(_one, [(i, c, params, a.eye, a.channel)
                               for i, c in enumerate(corners)])
    wall = time.perf_counter() - t_wall

    # ---- per-analysis timing summary ----
    keys = ["ac", "op", "hd3", "noise"] + (["eye"] if a.eye else [])
    print("\n--- timing per analysis (seconds) ---")
    print(f"  {'analysis':10s} {'mean':>7s} {'min':>7s} {'max':>7s} {'total':>9s}")
    for k in keys:
        v = [r["time_s"][k] for r in rows]
        print(f"  {k:10s} {sum(v)/len(v):7.2f} {min(v):7.2f} {max(v):7.2f} {sum(v):9.1f}")
    cpu = sum(r["time_total_s"] for r in rows)
    print(f"  {'PER CORNER':10s} {cpu/len(rows):7.2f}")
    print(f"\n  total CPU time : {cpu:8.1f} s")
    print(f"  wall-clock     : {wall:8.1f} s   (speedup {cpu/wall:.1f}x on {a.jobs} jobs)")

    # ---- pass/fail ----
    speckeys = sorted({k for r in rows for k in r["pass"]})
    print("\n--- pass/fail by corner ---")
    hdr = "  " + f"{'corner':26s}" + "".join(f"{k[:7]:>9s}" for k in speckeys)
    print(hdr)
    nfail = 0
    for r in rows:
        bad = [k for k, v in r["pass"].items() if not v]
        if bad:
            nfail += 1
        cells = "".join(f"{('ok' if r['pass'].get(k) else 'FAIL'):>9s}"
                        for k in speckeys)
        print(f"  {r['corner']:26s}{cells}")
    print(f"\n  corners with >=1 failure: {nfail} / {len(rows)}")
    for k in speckeys:
        f = sum(1 for r in rows if not r["pass"].get(k))
        if f:
            print(f"    {k:10s} fails at {f:3d} / {len(rows)} corners")

    (out / "pvt_sweep.json").write_text(
        json.dumps(_json_safe(rows), indent=2, default=str, allow_nan=False))
    with open(out / "pvt_sweep.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["corner", "lib", "vdd", "temp", "reward", "time_total_s"]
                   + speckeys
                   + ["f_peak", "peaking_db", "power_w", "hd3_db",
                      "vn_in_rms", "eye_height_v", "eye_width_ui"])
        for r in rows:
            m = r["metrics"]
            w.writerow([r["corner"], r["lib"], r["vdd"], r["temp"],
                        round(r["reward"], 4), round(r["time_total_s"], 2)]
                       + [r["pass"].get(k) for k in speckeys]
                       + [m["f_peak"], m["peaking_db"], m["power_w"], m["hd3_db"],
                          m["vn_in_rms"], m["eye_height_v"], m["eye_width_ui"]])
    print(f"\n  wrote {out/'pvt_sweep.json'} and {out/'pvt_sweep.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
