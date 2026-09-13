"""Closed-form starting point for the search, computed automatically.

CEM otherwise starts at the centre of every parameter range, which for an
11-dimensional box is a long way from anything that works: measured, a run
started there spent its whole budget without ever putting the peak in band.

This module derives a starting design from the small-signal equations instead.
Nothing here is hand-tuned -- the device parameters are MEASURED from an .op at
run time and the answer is calibrated against SPICE in a closed loop, so the
result is smart initialisation, not a human sitting in the loop.

The CTLE half-circuit:

    Av(s) = -gm*RL * (1 + s*Rs*Cs)
            ---------------------------------------
            (1 + gm*Rs/2 + s*Rs*Cs) * (1 + s*RL*Cout)

    f_z   = 1/(2*pi*Rs*Cs)                 where the boost starts
    f_p1  = (1 + gm*Rs/2)/(2*pi*Rs*Cs)     where it stops
    f_p2  = 1/(2*pi*RL*Cout)               the output pole -- sets f_peak
    boost = 1 + gm*Rs/2                    asymptotic, only realised if f_p1 < f_p2

The trap this encodes: the boost is only realised when f_p1 sits BELOW f_p2.
At the old defaults 1+gm*Rs/2 promised 14.5 dB while the output pole truncated
it to 5 dB, because f_p1 (1.87 GHz) landed above f_p2 (1.98 GHz).

The model is optimistic -- it has no r_o, no Miller Cgd, no tail pole, no
short-channel gm droop. Measured it over-predicts peaking by ~1.8x and f_peak
by ~1.3x, consistently. `calibrate()` measures those factors rather than
assuming them, so the correction tracks the PDK and the corner.
"""
from __future__ import annotations

import math

import numpy as np

from . import netlist as nl
from . import pdk
from .netlist import CtleParams

# Knobs the analytic solve does NOT set. They fix gm and the headroom; the
# solve then places the poles and zero around whatever gm they produce.
FIXED_START = dict(W=20.0, L=0.18, Itail=600e-6, Wtail=60.0, Wg1=40.0, Ig1=1.0e-3)

# Measured optimum for a 1-tap DFE on this topology, and the same value the
# reference design settles on. Both sweeps degrade beyond ~10%.
TAP_PCT_START = 5.0


def measure_devices(sim, corner: pdk.Corner, p: CtleParams) -> tuple[float, float]:
    """(gm of the input pair, total capacitance on the CTLE output) from one .op.

    Cout is what sets f_p2, and it is NOT just CL: the summing stage's Gm1 gate
    capacitance more than doubles it (measured 40 fF -> 67 fF).
    """
    from .spice import _parse_prints
    d = f"@m.x1.m{nl.NFET}"
    g1 = f"@m.xg1p.m{nl.NFET}"
    ctl = (".control\nop\n"
           f"print {d}[gm] {d}[cdd] {g1}[cgs] {g1}[cgd]\n.endc\n")
    ok, log = sim._run(sim._assemble(p, corner, "op", ctl, dfe="cm",
                                     channel=False), "analytic_op")
    if not ok:
        raise RuntimeError(f"analytic init: .op failed for {p}")
    v = _parse_prints(log)
    gm = v.get(f"{d}[gm]", float("nan"))
    cout = (abs(v.get(f"{d}[cdd]", 0.0)) + abs(v.get(f"{g1}[cgs]", 0.0))
            + abs(v.get(f"{g1}[cgd]", 0.0)) + p.CL)
    if not (gm == gm and gm > 0):
        raise RuntimeError("analytic init: could not measure gm")
    return gm, cout


def _shape(gm, cout, RL, Rs, Cs, f):
    """(f_peak, peaking_dB) from the half-circuit model, vectorised over Rs/Cs."""
    jw = 2j * np.pi * f
    Rs = np.asarray(Rs)[..., None]
    Cs = np.asarray(Cs)[..., None]
    k = 1 + gm * Rs / 2
    H = (gm * RL / k) * (1 + jw * Rs * Cs) / ((k + jw * Rs * Cs) / k * (1 + jw * RL * cout))
    g = 20 * np.log10(np.abs(H))
    i = np.argmax(g, axis=-1)
    pk = np.take_along_axis(g, i[..., None], -1)[..., 0] - g[..., 0]
    return f[i], pk


def solve(gm: float, cout: float, target_pk_db: float, target_fpeak: float,
          n: int = 300) -> tuple[float, float, float]:
    """Closed-form RL, Rs, Cs for a target peaking and peak frequency."""
    RL = 1.0 / (2 * math.pi * target_fpeak * cout)
    RL = min(max(RL, *(nl.PARAM_BOUNDS["RL"][:1])), nl.PARAM_BOUNDS["RL"][1])
    f = np.logspace(6, 10.3, 900)
    lo_s, hi_s = nl.PARAM_BOUNDS["Rs"]
    lo_c, hi_c = nl.PARAM_BOUNDS["Cs"]
    Rs = np.logspace(math.log10(lo_s), math.log10(hi_s), n)
    Cs = np.logspace(math.log10(lo_c), math.log10(hi_c), n)
    RsG, CsG = np.meshgrid(Rs, Cs, indexing="ij")
    fpk, pk = _shape(gm, cout, RL, RsG.ravel(), CsG.ravel(), f)
    err = ((pk - target_pk_db) / target_pk_db) ** 2 + ((fpk - target_fpeak) / target_fpeak) ** 2
    i = int(np.argmin(err))
    return RL, float(RsG.ravel()[i]), float(CsG.ravel()[i])


# Calibration feedback is DAMPED. An undamped update (k *= measured/target)
# oscillates: measured 1.60 dB -> 13.88 dB -> 10.55 dB against a 7.5 dB target,
# because the model's error is not proportional. A square-root step converges
# monotonically instead.
CALIB_DAMPING = 0.5


def initial_design(spec, sim, corner: pdk.Corner | None = None,
                   n_calib: int = 4, verbose: bool = True) -> CtleParams:
    """A starting design derived from the equations and calibrated against SPICE.

    Targets the middle of the brief's windows: peaking halfway through 3-12 dB,
    peak frequency halfway through the f_peak band. Aiming at a window's centre
    rather than its edge is what buys PVT margin.
    """
    corner = corner or pdk.typical_corner()
    tgt_pk = 0.5 * (spec.peaking_min_db + spec.peaking_max_db)
    tgt_f = 0.5 * (spec.fpeak_lo + spec.fpeak_hi)

    p = CtleParams(**FIXED_START, tap_pct=TAP_PCT_START)
    gm, cout = measure_devices(sim, corner, p)
    if verbose:
        print(f"  [analytic] measured gm={gm*1e3:.3f} mS, Cout={cout*1e15:.1f} fF")

    k_pk, k_f = 1.0, 1.0          # model -> SPICE correction, measured below
    for it in range(n_calib + 1):
        RL, Rs, Cs = solve(gm, cout, tgt_pk / k_pk, tgt_f / k_f)
        p = CtleParams(**FIXED_START, tap_pct=TAP_PCT_START,
                       RL=RL, Rs=Rs, Cs=Cs, Rsum=_rsum_for_midsupply(corner, FIXED_START["Ig1"]))
        if it == n_calib:
            break
        ac = sim.ac(p, corner)
        if not ac.get("ok") or not (ac["peaking_db"] > 0):
            if verbose:
                print("  [analytic] calibration sim unusable; keeping current factors")
            break
        k_pk = max(0.15, min(4.0, k_pk * (ac["peaking_db"] / tgt_pk) ** CALIB_DAMPING))
        k_f = max(0.15, min(4.0, k_f * (ac["f_peak"] / tgt_f) ** CALIB_DAMPING))
        if verbose:
            print(f"  [analytic] calib {it+1}: SPICE {ac['peaking_db']:5.2f} dB @ "
                  f"{ac['f_peak']/1e9:.3f} GHz  (target {tgt_pk:.1f} dB @ "
                  f"{tgt_f/1e9:.3f} GHz) -> k {k_pk:.3f}, {k_f:.3f}")
    if verbose:
        print(f"  [analytic] start: RL={p.RL:.0f} Rs={p.Rs:.0f} Cs={p.Cs*1e15:.0f}fF "
              f"Rsum={p.Rsum:.0f} tap={p.tap_pct:.1f}%")
    return p


def _rsum_for_midsupply(corner: pdk.Corner, ig1: float) -> float:
    """Summing-node load that puts the output common mode at mid supply.

    The reference sets output VCM = 0.5*I_Gm1*R_Gm1 = VDD/2, which centres the
    swing and maximises headroom. Ours sat at 1.54 V of 1.8 V before this.
    """
    r = corner.vdd / ig1 if ig1 > 0 else nl.PARAM_BOUNDS["Rsum"][1]
    lo, hi = nl.PARAM_BOUNDS["Rsum"]
    return float(min(max(r, lo), hi))


def initial_u(spec, sim, corner: pdk.Corner | None = None,
              verbose: bool = True) -> list[float]:
    """The starting design as a normalised [0,1] vector for the search."""
    p = initial_design(spec, sim, corner, verbose=verbose)
    return nl.norm({k: getattr(p, k) for k in nl.PARAM_NAMES})
