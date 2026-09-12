#!/usr/bin/env python3
"""Quick sanity checks for the SPICE layer before running the RL loop.

1. .op   -> verify device biasing + the W/L unit convention (Id should scale
   sensibly with W/L; devices should be in saturation).
2. .ac   -> verify a CTLE actually peaks and we parse gain/peaking/fpeak.
3. tran/noise -> verify HD3 and input-referred noise parse to finite numbers.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import pdk
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator

sim = Simulator("/tmp/eqrl_smoke")
c = pdk.typical_corner()
print("PDK lib:", pdk.find_sky130_lib())
print("Corner :", c.name)

p = CtleParams(W=20.0, L=0.18, RL=1200.0, Rs=1500.0, Cs=300e-15, Itail=600e-6)
print("\n--- .op (bias sanity + unit check) ---")
print(sim.op(p, c))

print("\n--- .ac (CTLE shape) ---")
print(sim.ac(p, c))

print("\n--- tran HD3 ---")
print(sim.tran_hd3(p, c))

print("\n--- noise ---")
print(sim.noise(p, c))

print("\n--- full evaluate() ---")
m = sim.evaluate(p, c)
for k, v in m.as_dict().items():
    print(f"  {k:14s}: {v}")
