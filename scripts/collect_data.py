#!/usr/bin/env python3
"""Collect a labelled dataset of (design, corner) -> measured metrics.

This is the expensive, bounded, resumable step that everything downstream sits
on.  A surrogate model of SPICE needs labels; labels cost ~4.4 s of ngspice
each; so they are gathered ONCE, written to disk the moment they exist, and
never recomputed.

Design of the run, and why each part is the way it is:

**Plan before execute.** The complete sample plan is generated deterministically
from a seed and written to ``results/evals/plan_<tag>.json`` BEFORE any
simulation starts.  Resume is then a set difference -- ``plan x corners`` minus
what the cache already holds -- rather than an attempt to reconstruct what a
dead process was doing.  The analytic seed (5 ngspice launches) is computed
once and stored in the plan, so a resume never recomputes it.

**Three clouds, interleaved.**  Sobol over the whole box teaches the model where
the invalid region is; a cloud around the analytic seed and one around the
shipped design teach it the region a search actually visits, at much higher
resolution.  The three are INTERLEAVED by a seeded shuffle so that any prefix
of the plan is representative: if the first 600 rows were all Sobol, a
surrogate trained early would see almost nothing but broken designs.  A
candidate's corners stay adjacent so early rows form complete designs.

**The parent writes, the workers only compute.**  ``imap_unordered`` hands each
result back as it lands and the parent appends it immediately.  Using
``Objective.evaluate_batch`` here would be a mistake: it returns only when the
whole batch is finished, so a crash at 90 % of a batch loses 90 % of a batch.

**Failures are data.**  A timeout or a parse failure is RECORDED with
``ok=False``, not dropped and not retried in the loop.  The surrogate has to
learn where the cliff is, and a silently dropped failure moves the cliff.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import analytic, netlist as nl, pdk, specs as sp
from eqrl.cache import EvalLog, git_sha, key_of, load, meas_dict
from eqrl.env import _eval_task
from eqrl.netlist import PARAM_NAMES
from eqrl.spice import MeasCond, Simulator, safe_job_count

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "results" / "evals"
N = len(PARAM_NAMES)

_STOP = False


def _on_signal(signum, frame):
    """Stop submitting new work; let what is in flight finish and be written."""
    global _STOP
    if not _STOP:
        _STOP = True
        print(f"\n[signal {signum}] finishing in-flight tasks, then exiting. "
              f"Re-run the same command to resume.", flush=True)


def _ignore_sigint():
    """Worker initialiser.

    Pool workers inherit SIGINT, so Ctrl-C raises KeyboardInterrupt inside
    every worker at once and the pool wedges instead of draining.  Workers
    ignore it; the parent handles it and shuts the pool down in order.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _collect_task(task):
    """Run one (candidate, corner) and delete its dump directory afterwards.

    2 500 designs x 4 corners leaves 10 000 directories of .dat files behind --
    several GB of dumps that nothing reads again, because everything worth
    keeping is in the Metrics object we return.
    """
    workdir = None
    try:
        key, workroot, params, corner, do_eye, timeout, use_ch, meas = task
        workdir = Path(workroot) / f"c{key}" / corner.name
        return _eval_task(task)
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------
def build_plan(tag: str, n_sobol: int, n_seed: int, n_final: int, seed: int,
               spec, sim, do_eye: bool) -> dict:
    """Deterministic sample plan. Written to disk before any simulation."""
    from scipy.stats import qmc

    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    if n_sobol:
        s = qmc.Sobol(d=N, scramble=True, seed=seed).random(n_sobol)
        rows += [{"u": list(map(float, u)), "cloud": "sobol"} for u in s]

    u_seed = None
    if n_seed:
        print("=== analytic seed (computed once, stored in the plan) ===",
              flush=True)
        u_seed = analytic.initial_u(spec, sim, verbose=True)
        c = np.clip(np.asarray(u_seed) + rng.normal(0, 0.25, (n_seed, N)), 0, 1)
        rows += [{"u": list(map(float, u)), "cloud": "seed"} for u in c]

    u_final = None
    rep = ROOT / "results" / "report.json"
    if n_final and rep.exists():
        fp = json.loads(rep.read_text())["final_params"]
        u_final = nl.norm({k: fp[k] for k in PARAM_NAMES})
        c = np.clip(np.asarray(u_final) + rng.normal(0, 0.15, (n_final, N)), 0, 1)
        rows += [{"u": list(map(float, u)), "cloud": "final"} for u in c]
    elif n_final:
        print("  no results/report.json -- skipping the 'final' cloud")

    # Interleave the clouds so ANY PREFIX of the plan is representative. The
    # collection may be stopped at any point, and the surrogate trains on
    # whatever exists; a prefix that is 100 % Sobol is a prefix of almost
    # entirely invalid designs.
    order = rng.permutation(len(rows))
    rows = [rows[i] for i in order]
    for i, r in enumerate(rows):
        r["i"] = i
        r["key"] = list(key_of(r["u"]))

    return {
        "tag": tag,
        "created": time.time(),
        "git_sha": git_sha(),
        "seed": seed,
        "n_sobol": n_sobol, "n_seed": n_seed, "n_final": n_final,
        "do_eye": do_eye,
        "meas": meas_dict(MeasCond.from_spec(spec)),
        "corners": [c.name for c in pdk.robust_search_corners()],
        "u_seed": u_seed,
        "u_final": u_final,
        "rows": rows,
    }


def plan_path(tag: str) -> Path:
    return EVAL_DIR / f"plan_{tag}.json"


def load_or_build_plan(a, spec, sim) -> dict:
    p = plan_path(a.tag)
    if p.exists():
        plan = json.loads(p.read_text())
        print(f"  plan: {len(plan['rows'])} candidates (existing {p.name})")
        return plan
    plan = build_plan(a.tag, a.n_sobol, a.n_seed, a.n_final, a.seed,
                      spec, sim, a.eye)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=1, default=str))
    os.replace(tmp, p)
    print(f"  plan: {len(plan['rows'])} candidates written to {p.name}")
    return plan


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def work_list(plan: dict, done: set, corners: list) -> list:
    """plan x corners - done, in plan order."""
    todo = []
    for row in plan["rows"]:
        k = tuple(row["key"])
        for c in corners:
            if (k, c.name) not in done:
                todo.append((row, c))
    return todo


def print_status(plan: dict, res, corners: list) -> None:
    done = set(res.records)
    total = len(plan["rows"]) * len(corners)
    print(f"\n  cache: {res.summary()}")
    print(f"  done : {len(done)} / {total} "
          f"({100.0 * len(done) / max(1, total):.1f} %)")
    by_cloud: dict = {}
    for row in plan["rows"]:
        k = tuple(row["key"])
        d = sum(1 for c in corners if (k, c.name) in done)
        cl = by_cloud.setdefault(row["cloud"], [0, 0])
        cl[0] += d
        cl[1] += len(corners)
    for cl, (d, t) in sorted(by_cloud.items()):
        print(f"    {cl:8} {d:6} / {t:6}")
    ok = sum(1 for r in res.records.values() if r["metrics"].get("ok"))
    print(f"  valid: {ok} / {len(done)} simulated ok")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="collect")
    ap.add_argument("--n-sobol", type=int, default=1000)
    ap.add_argument("--n-seed", type=int, default=1000)
    ap.add_argument("--n-final", type=int, default=500)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--chunk", type=int, default=64)
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="per-simulation cap. Lower than the search's 300 s on "
                         "purpose: Sobol samples the whole box and will hit "
                         "designs that make ngspice crawl, and a handful of "
                         "those at 300 s each costs hours. A legitimate eye "
                         "run measures ~40 s at worst.")
    ap.add_argument("--no-eye", dest="eye", action="store_false", default=True)
    ap.add_argument("--workdir", default="/tmp/eqrl_collect")
    ap.add_argument("--status", action="store_true", help="report and exit")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--pidfile", default=None,
                    help="write THIS process's pid here. `nohup setsid cmd &` "
                         "makes $! the pid of setsid, which forks and exits at "
                         "once -- so a pidfile written by the shell names a "
                         "process that is already dead, and `kill -INT` on it "
                         "does nothing. The process that must be signalled is "
                         "the one that knows its own pid.")
    a = ap.parse_args()

    if a.pidfile:
        Path(a.pidfile).parent.mkdir(parents=True, exist_ok=True)
        Path(a.pidfile).write_text(f"{os.getpid()}\n")

    spec = sp.EqualizerSpec()
    meas = MeasCond.from_spec(spec)
    corners = pdk.robust_search_corners()
    sim = Simulator(a.workdir, use_channel=True, timeout=a.timeout, meas=meas)

    print(f"collect '{a.tag}': eye={a.eye} corners={len(corners)} "
          f"timeout={a.timeout:g}s sha={git_sha()}")
    plan = load_or_build_plan(a, spec, sim)

    res = load(a.tag, meas=meas)
    if res.n_skipped_bad:
        print(f"  NOTE: skipped {res.n_skipped_bad} unparseable line(s) "
              f"(expected after a kill: at most one per file)")
    print_status(plan, res, corners)
    if a.status:
        return 0

    todo = work_list(plan, set(res.records), corners)
    if not todo:
        print("\n  nothing to do -- collection complete.")
        return 0

    jobs = safe_job_count(a.jobs)
    print(f"\n  {len(todo)} (candidate, corner) pairs to run on {jobs} workers")

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    t0 = time.perf_counter()
    n_done = n_ok = n_fail = 0
    deadline = t0 + a.max_minutes * 60 if a.max_minutes else None

    def tasks():
        for row, c in todo:
            if _STOP or (deadline and time.perf_counter() > deadline):
                return
            if shutil.disk_usage(a.workdir).free < a.min_free_gb * 2**30:
                print("\n  disk below --min-free-gb, stopping gracefully")
                return
            p = nl.denorm(list(row["u"]))
            yield (row["i"], str(sim.workdir), p, c, a.eye, a.timeout,
                   True, meas)

    extra = {"plan": plan_path(a.tag).name, "seed": a.seed,
             "do_eye": a.eye, "timeout": a.timeout}
    by_i = {row["i"]: row for row in plan["rows"]}

    with EvalLog(a.tag, extra=extra) as log, \
            Pool(jobs, initializer=_ignore_sigint) as pool:
        try:
            for i, cname, m, launches in pool.imap_unordered(
                    _collect_task, tasks(), chunksize=1):
                row = by_i[i]
                corner = next(c for c in corners if c.name == cname)
                log.append(tuple(row["key"]), corner, m, meas,
                           params=nl.denorm(list(row["u"])),
                           launches=launches, note=row["cloud"])
                n_done += 1
                n_ok += bool(m.ok)
                n_fail += (not m.ok)
                if n_done % 20 == 0 or n_done == len(todo):
                    el = time.perf_counter() - t0
                    rate = n_done / el
                    left = (len(todo) - n_done) / rate if rate else 0
                    print(f"  {n_done:6}/{len(todo)} ok={n_ok} fail={n_fail} "
                          f"{rate * 60:5.1f}/min  elapsed {el / 60:5.1f}m  "
                          f"eta {left / 60:5.1f}m", flush=True)
        except KeyboardInterrupt:
            _on_signal(signal.SIGINT, None)
        finally:
            pool.terminate() if _STOP else pool.close()
            pool.join()

    el = time.perf_counter() - t0
    print(f"\n  wrote {n_done} records in {el / 60:.1f} min "
          f"({n_ok} ok, {n_fail} failed) -> results/evals/{a.tag}.jsonl")
    if _STOP or n_done < len(todo):
        print(f"  resume: python3 scripts/collect_data.py --tag {a.tag}")
    shutil.rmtree(a.workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
