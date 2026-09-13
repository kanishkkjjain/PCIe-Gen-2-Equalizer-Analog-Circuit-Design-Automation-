#!/usr/bin/env python3
"""Sanity checks for the SPICE layer before running the RL loop.

Unlike the previous version, this ASSERTS. It used to only print, which meant
it reported success no matter what ngspice returned -- a "sanity check" that
could not fail is not one.

  1. .op   -> devices biased, both probes present, input pair AND real tail in
              saturation, measured supply current near the intended budget.
  2. .ac   -> the CTLE actually peaks, in band, and we parse it.
  3. tran/noise -> HD3 and input-referred noise parse to finite numbers.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import netlist as nl, pdk, specs as sp
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    sim = Simulator("/tmp/eqrl_smoke")
    c = pdk.typical_corner()
    print("PDK lib:", pdk.find_sky130_lib())
    print("Corner :", c.name)
    p = CtleParams()

    print("\n--- .op (bias sanity + unit check) ---")
    op = sim.op(p, c)
    check("op parsed", op.get("ok"), str(op.get("note", ""))[:80])
    if op.get("ok"):
        check("input pair saturated", op["vds_m1"] > op["vdsat_m1"] + 0.02,
              f"vds={op['vds_m1']*1e3:.0f}mV vdsat={op['vdsat_m1']*1e3:.0f}mV")
        check("real tail saturated", op["tail_saturated"],
              f"vds={op['vds_tail']*1e3:.0f}mV vdsat={op['vdsat_tail']*1e3:.0f}mV")
        budget = (2 + 1 / nl.MIRROR_RATIO) * p.Itail + p.Itap
        check("supply current within 20% of budget",
              abs(op["i_supply"] - budget) / budget < 0.20,
              f"{op['i_supply']*1e3:.4f} mA vs {budget*1e3:.4f} mA budget")

    print("\n--- .ac (CTLE shape) ---")
    ac = sim.ac(p, c)
    check("ac parsed", ac.get("ok"))
    if ac.get("ok"):
        check("peaks (peaking > 3 dB)", ac["peaking_db"] > 3.0,
              f"{ac['peaking_db']:.2f} dB")
        check("peak in 1-5 GHz", 1e9 < ac["f_peak"] < 5e9,
              f"{ac['f_peak']/1e9:.3f} GHz")

    print("\n--- tran HD3 ---")
    hd = sim.tran_hd3(p, c)
    check("hd3 parsed", hd.get("ok"))
    if hd.get("ok"):
        check("hd3 finite and negative", math.isfinite(hd["hd3_db"])
              and hd["hd3_db"] < 0, f"{hd['hd3_db']:.1f} dBc")

    print("\n--- noise ---")
    nz = sim.noise(p, c)
    check("noise parsed", nz.get("ok"), str(nz.get("note", ""))[:80])
    if nz.get("ok"):
        check("noise finite and positive",
              math.isfinite(nz["vn_in_rms"]) and nz["vn_in_rms"] > 0,
              f"{nz['vn_in_rms']*1e3:.3f} mVrms")

    print("\n--- full evaluate() ---")
    m = sim.evaluate(p, c)
    check("evaluate ok", m.ok, m.note[:80])
    check("no metric is NaN", all(
        math.isfinite(v) for k, v in m.as_dict().items()
        if isinstance(v, float) and not k.startswith("eye")),
        "")
    scored = sp.score(m, sp.EqualizerSpec())
    print(f"  reward {scored['reward']:.3f}  "
          f"pass {sum(scored['pass'].values())}/{len(scored['pass'])}")
    print(f"  ngspice launches: {sim.n_launches}")

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
