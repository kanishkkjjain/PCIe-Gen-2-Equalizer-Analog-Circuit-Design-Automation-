#!/usr/bin/env python3
"""One screen: where every long-running piece of this project stands.

Long runs here are detached and measured in hours, so the question "is it still
going, and is it going to finish in time" needs an answer that does not involve
reading a log by eye.  Everything below is read from the artifacts themselves,
never from memory of what was launched.

  python3 scripts/status.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _alive(pid: int) -> bool:
    try:
        return subprocess.run(["kill", "-0", str(pid)],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def _age(t: float) -> str:
    d = time.time() - t
    if d < 90:
        return f"{d:.0f}s ago"
    if d < 5400:
        return f"{d / 60:.0f}m ago"
    return f"{d / 3600:.1f}h ago"


def section(name: str) -> None:
    print(f"\n\033[1m{name}\033[0m" if sys.stdout.isatty() else f"\n{name}")
    print("-" * 66)


def processes() -> None:
    section("running")
    pids = sorted((ROOT / "logs").glob("*.pid")) if (ROOT / "logs").exists() else []
    if not pids:
        print("  no pidfiles in logs/")
    for f in pids:
        try:
            pid = int(f.read_text().strip())
        except Exception:
            continue
        state = "ALIVE" if _alive(pid) else "not running"
        log = f.with_suffix(".log")
        tail = ""
        if log.exists():
            lines = [ln for ln in log.read_text(errors="replace").splitlines()
                     if ln.strip()]
            tail = lines[-1][:80] if lines else ""
        print(f"  {f.stem:12} pid {pid:<8} {state:12} {tail}")


def collection() -> None:
    section("data collection")
    from eqrl import specs as sp
    from eqrl.cache import load
    from eqrl.spice import MeasCond
    meas = MeasCond.from_spec(sp.EqualizerSpec())

    ev = ROOT / "results" / "evals"
    plans = sorted(ev.glob("plan_*.json")) if ev.exists() else []
    if not plans:
        print("  no collection plan yet")
        return
    for pf in plans:
        plan = json.loads(pf.read_text())
        tag = plan["tag"]
        res = load(tag, meas=meas)
        total = len(plan["rows"]) * len(plan["corners"])
        n = len(res.records)
        ok = sum(1 for r in res.records.values() if r["metrics"].get("ok"))
        designs = len({k[0] for k in res.records})
        print(f"  {tag}: {n}/{total} records ({100 * n / max(1, total):5.1f} %), "
              f"{ok} ok, {designs} designs")
        if res.n_skipped_bad or res.n_skipped_meas:
            print(f"    skipped: {res.n_skipped_bad} unparseable, "
                  f"{res.n_skipped_meas} meas-mismatch")
        ts = sorted(r["t"] for r in res.records.values())
        if len(ts) > 30:
            recent = ts[-30:]
            rate = 30 / max(1e-9, recent[-1] - recent[0]) * 60
            left = (total - n) / rate if rate > 0 else float("inf")
            print(f"    {rate:.1f} rec/min, last {_age(ts[-1])}, "
                  f"eta {left / 60:.1f} h")


def _latest(kind: str):
    p = ROOT / "models" / kind / "LATEST"
    if not p.exists():
        return None
    d = Path(p.read_text().strip())
    return d if d.is_absolute() else ROOT / d


def surrogate() -> None:
    section("surrogate (gate G1: spearman > 0.9)")
    d = _latest("surrogate")
    if d is None or not d.exists():
        print("  not trained yet")
        return
    print(f"  LATEST {d.name}")
    vf = d / "validation.json"
    if not vf.exists():
        print("  no validation.json")
        return
    v = json.loads(vf.read_text())
    g1 = v["G1"]["spearman_reward"]
    print(f"  G1 spearman = {g1:.4f}   "
          f"{'PASS' if g1 > v['G1']['threshold'] else 'below gate'}   "
          f"({v['n_val_designs']} held-out designs)")
    sat = v.get("saturated_classifier", {})
    if sat:
        print(f"  saturated classifier accuracy {sat.get('accuracy', 0):.1%}")
    tw = v.get("three_way", {})
    if tw:
        print("  held-out R2, hand equations -> residual model:")
        for k, x in tw.items():
            print(f"    {k:12} {x['analytic_only_r2']:+7.3f} -> "
                  f"{x['residual_model_r2']:+7.3f}")


def policy() -> None:
    section("policy (gate G2: held-out success >= 80 %)")
    p = ROOT / "results" / "policy_train_eval.json"
    if not p.exists():
        print("  not trained yet")
        return
    evals = json.loads(p.read_text()).get("evals", [])
    if not evals:
        print("  no evaluations recorded")
        return
    best = max(e["success_rate"] for e in evals)
    last = evals[-1]
    print(f"  {len(evals)} evals, latest at {last['steps']} steps: "
          f"success {last['success_rate']:.1%}, "
          f"mean best reward {last['mean_best_reward']:.3f}")
    print(f"  best so far {best:.1%}   "
          f"{'PASS' if best >= 0.8 else 'below gate'}")
    d = _latest("policy")
    if d:
        print(f"  LATEST {d.name}")


def deploy() -> None:
    section("deployment vs CEM (gate G3)")
    p = ROOT / "results" / "policy_vs_cem.json"
    if not p.exists():
        print("  not run yet")
        return
    d = json.loads(p.read_text())
    rows = d.get("specs", [])
    print(f"  {len(rows)}/{d.get('n_specs', '?')} specs done, "
          f"budget {d.get('budget')} sims each")
    for r in rows:
        cells = []
        for m in ("policy", "cem"):
            x = r.get(m, {})
            pv = x.get("pvt", {})
            tag = ("PASS" if pv.get("all_pass_everywhere")
                   else f"{pv.get('brief_specs_passed', '-')}/8")
            cells.append(f"{m} {x.get('best_reward', float('nan')):.4f} {tag}")
        print(f"    {r['spec_id']:7} {cells[0]:28} {cells[1]}")


def disk() -> None:
    section("disk")
    for path in ("/tmp", str(ROOT)):
        try:
            u = shutil.disk_usage(path)
            print(f"  {path:28} {u.free / 2**30:6.1f} GiB free "
                  f"of {u.total / 2**30:.0f}")
        except OSError:
            pass
    ev = ROOT / "results" / "evals"
    if ev.exists():
        mb = sum(f.stat().st_size for f in ev.glob("*")) / 2**20
        print(f"  results/evals                {mb:6.1f} MiB")


def main() -> int:
    print(f"eqrl status  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for fn in (processes, collection, surrogate, policy, deploy, disk):
        try:
            fn()
        except Exception as e:                      # never let status crash
            print(f"  [{fn.__name__} unavailable: {type(e).__name__}: {e}]")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
