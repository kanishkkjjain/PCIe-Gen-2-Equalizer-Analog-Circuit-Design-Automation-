#!/usr/bin/env python3
"""Render an eye diagram from the closed-loop DFE transient.

  python scripts/plot_eye.py --itap 10e-6 -o /tmp/eye.png
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from eqrl import pdk
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator

ap = argparse.ArgumentParser()
ap.add_argument("--tap-pct", type=float, default=None, dest="tap_pct",
                help="DFE tap weight as %% of the main Gm1 branch (1-30). "
                     "Igf/Wgf are derived from this and are not settable.")
ap.add_argument("--bits", type=int, default=200)
ap.add_argument("--ui", type=float, default=200e-12)
ap.add_argument("-o", "--out", default="/tmp/eye.png")
a = ap.parse_args()

sim = Simulator("/tmp/eqrl_eye", timeout=600)
# `is not None`, not truthiness: --tap-pct 0 must be honoured,
# not silently ignored as 'not supplied'.
p = CtleParams(**({'tap_pct': a.tap_pct} if a.tap_pct is not None else {}))
r = sim.tran_eye(p, pdk.typical_corner(), n_bits=a.bits, ui=a.ui)
if not r.get("ok"):
    print("sim failed:", r.get("log", "")[-400:]); raise SystemExit(1)
t, v, e = r["t"], r["v"], r["eye"]

skip = 20 * a.ui
m = t >= skip
t, v = t[m], v[m]
n_ui = int((t[-1] - t[0]) // a.ui)
ph = np.linspace(-0.5, 0.5, 200)
fig, ax = plt.subplots(figsize=(7, 4.2))
for k in range(n_ui - 1):
    tk = t[0] + (k + 0.5 + ph) * a.ui
    ax.plot(ph, np.interp(tk, t, v) * 1e3, color="#2b6cb0", lw=0.5, alpha=0.25)
ax.axhline(0, color="k", lw=0.8, alpha=0.5)
ax.set_xlabel("phase (UI)"); ax.set_ylabel("differential output (mV)")
ax.set_title(f"Eye at the summing node, 5 Gb/s | tap={p.tap_pct:.2f}% "
             f"(Igf={p.Igf*1e6:.1f}uA, nominal gmF/gm1 = {p.tap_weight_ratio():.3f})\n"
             f"height {e.height_v*1e3:.1f} mV (>100)  width {e.width_ui:.3f} UI (>0.4)")
ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(a.out, dpi=120)
print(f"wrote {a.out}  height={e.height_v*1e3:.1f}mV width={e.width_ui:.3f}UI")
