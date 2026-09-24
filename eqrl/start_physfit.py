"""The systematic derivation, SPICE-free -- every device estimate from PhysFit.

`start_systematic.py` spends ~29 ngspice .op runs per spec measuring Cout, Vt
and bisecting W/Wtail. Measured cost: proposing went from 1.2 s to 19.8 s per
spec, a 19x regression, and at that price the start CANNOT be used inside RL
training where the environment resets thousands of times.

This is the same derivation with every .op replaced by the fitted model:

    Cout   <- fit.cout(W, L, Wg1)
    gm     <- fit.gm_m1(W, L, Itail, temp),  inverted for W
    Wtail  <- fit.tail_margin(...) bisected to the 0.15 V headroom target
    Vt     <- measured ONCE, module-wide (it barely moves with device size)

It also uses PhysFit's own fitted calibration instead of a hand-tuned over-ask:
`k_pk = 1.677` and `k_f = 1.428` say how many times high the closed form reads,
so the targets are pre-multiplied by them. For a 7.5 dB target that asks for
12.6 dB -- close to the 14 dB found empirically, which is a good sign both
routes measure the same effect.

NOTE ON WHAT THIS DOES NOT FIX. The measured weakness of the systematic start
is not device accuracy -- PhysFit's gm_m1 has 24 % median relative error and
still produces a better start than exact .op values did. It is that the start
aims at the TYPICAL corner while margin is scored at the WORST, and that it
never closes a loop against the surrogate that does the scoring. Both are
deliberately left alone here so this experiment measures the speed change and
nothing else.
"""
import math
import os

import numpy as np
from eqrl import netlist as nl, pdk

F_P2_RATIO, F_P1_RATIO, ICMR_MARGIN, TAIL_MARGIN_V = 2.133, 0.800, 0.10, 0.15
_CACHE, _VT = {}, {}

#: Where `_vt` runs its single .op. A module default rather than a hardcoded
#: /tmp path so a server can keep its scratch under its own work root.
WORK_DIR = os.environ.get("EQRL_START_WORK", "/tmp/eqrl_vt_once")

#: Vt when the .op could not run. Measured on tt/1.8V/27C; it barely moves with
#: device size, which is why one measurement serves every spec in the first
#: place. Using it is not a failure -- but it IS a guess, and a design derived
#: from a guessed threshold must not be indistinguishable from one derived from
#: a measured one, so every corner that fell back is recorded here.
VT_FALLBACK = 0.6061
_VT_FELL_BACK: set = set()


def vt_fallback_corners() -> tuple:
    """Corners whose Vt was guessed rather than measured, for reporting."""
    return tuple(sorted(_VT_FELL_BACK))


def _vt(corner, fit, work_dir=None):
    """Vt, measured once per corner and reused. The only ngspice this file runs."""
    key = (corner.process, round(corner.vdd, 3), round(corner.temp, 1))
    if key in _VT:
        return _VT[key]
    from eqrl.spice import Simulator, _parse_prints
    from eqrl import analytic
    s = Simulator(str(work_dir or WORK_DIR), use_channel=False, timeout=300)
    p = nl.CtleParams(**analytic.FIXED_START, tap_pct=1.0)
    d = f"@m.x1.m{nl.NFET}"
    v = None
    try:
        ok, log = s._run(s._assemble(p, corner, "op",
                                     f".control\nop\nprint {d}[vth]\n.endc\n",
                                     dfe="cm", channel=False), "vt")
        if ok:
            v = _parse_prints(log).get(f"{d}[vth]")
    except Exception:                       # ngspice missing, PDK missing, ...
        v = None                            # a start must still be derivable
    if v is None:
        _VT_FELL_BACK.add(key)
        v = VT_FALLBACK
    _VT[key] = float(v)
    return _VT[key]


def _clip(x, name):
    lo, hi = nl.PARAM_BOUNDS[name]
    return float(min(max(float(x), lo), hi))


def _invert(f, target, lo, hi, n=40):
    """Smallest x in [lo,hi] with f(x) >= target (f monotone increasing)."""
    if float(np.atleast_1d(f(hi))[0]) < target:
        return hi
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if float(np.atleast_1d(f(mid))[0]) < target:
            lo = mid
        else:
            hi = mid
    return hi


def derive(spec, fit, corner=None, work_dir=None):
    corner = corner or pdk.typical_corner()
    key = (round(getattr(spec, "peaking_target_db", 7.5), 3),
           round(spec.fpeak_lo, -6), round(spec.fpeak_hi, -6),
           corner.process, round(corner.vdd, 3), round(corner.temp, 1))
    if key in _CACHE:
        return list(_CACHE[key])

    Wg1 = _clip(100.0 * nl.W_MIN_UM, "Wg1")
    Ig1 = 1.0e-3
    Rsum = _clip(corner.vdd / Ig1, "Rsum")
    L = 0.18
    Vt = _vt(corner, fit, work_dir)
    Vov = max(0.05, (1.1 - ICMR_MARGIN - Vt) / 2.0)

    # Cout: the CTLE drain term is ~0.1 fF against 68 fF total, so a
    # provisional W costs nothing and breaks the W <-> Cout loop outright.
    Cout = float(np.atleast_1d(fit.cout(20.0, L, Wg1))[0])

    k_pk = max(1.0, fit.coef.get("k_pk", 1.0))
    k_f = max(1.0, fit.coef.get("k_f", 1.0))
    f_tgt = 0.5 * (spec.fpeak_lo + spec.fpeak_hi)

    RL = _clip(1.0 / (2 * math.pi * F_P2_RATIO * f_tgt * k_f * Cout), "RL")
    pk_db = getattr(spec, "peaking_target_db", None)
    if pk_db is None:
        pk_db = 0.5 * (spec.peaking_min_db + spec.peaking_max_db)
    k = 10 ** ((pk_db * k_pk) / 20.0)          # PhysFit's own over-ask factor
    gm = k / RL
    Rs = _clip(2.0 * (k - 1.0) / gm, "Rs")
    Cs = _clip(k / (2 * math.pi * Rs * F_P1_RATIO * f_tgt), "Cs")
    Itail_gm = 2.0 * (gm * Vov / 2.0)          # what the peaking target wants

    # HEADROOM IS A HARD CONSTRAINT, gm IS A WINDOW. The gm/ID chain asks for
    # more tail current than the tail can carry and still stay clear of triode:
    # measured, Itail_gm leaves +0.0745 V of margin even at the widest tail,
    # against the 0.15 V the shipped start insists on. That is WORSE than the
    # +0.082 V which put 56 % of collected starts out of saturation, so an
    # RL perturbation from here lands in triode where the reward has no
    # gradient. So cap the current at what the tail can actually sustain, the
    # way `physics.analytic_start_u` does, and let peaking land where it lands
    # -- it has a 3-12 dB window to land in, the bias point has none.
    Wtail = _clip(nl.PARAM_BOUNDS["Wtail"][1], "Wtail")
    lo_i, hi_i = nl.PARAM_BOUNDS["Itail"]
    grid = np.linspace(lo_i, min(hi_i, Itail_gm), 96)
    W_prov = _clip(_invert(lambda w: fit.gm_m1(w, L, Itail_gm, corner.temp),
                           gm, *nl.PARAM_BOUNDS["W"]), "W")
    marg = np.atleast_1d(fit.tail_margin(W_prov, L, grid, Wtail))
    ok = np.flatnonzero(np.asarray(marg, float) >= TAIL_MARGIN_V)
    Itail = _clip(float(grid[ok[-1]]) if len(ok) else float(grid[0]), "Itail")

    W = _clip(_invert(lambda w: fit.gm_m1(w, L, Itail, corner.temp),
                      gm, *nl.PARAM_BOUNDS["W"]), "W")
    Wtail = _clip(_invert(lambda wt: fit.tail_margin(W, L, Itail, wt),
                          TAIL_MARGIN_V, *nl.PARAM_BOUNDS["Wtail"]), "Wtail")

    # tap_pct IS A CONSTANT HERE, DELIBERATELY. Step 13 of the systematic
    # derivation reads the post-cursor off a single-pulse transient, and a
    # transient is SPICE -- which is the one thing this file exists to avoid.
    # 1.55 is what that measurement returned for the brief's own spec, and the
    # search moves it from there. `docs/systematic-starting-point.md` step 13
    # describes the SPICE route; this is the SPICE-free stand-in for it.
    u = nl.norm(dict(W=W, L=L, RL=RL, Rs=Rs, Cs=Cs, Itail=Itail,
                     Wtail=Wtail, Rsum=Rsum, Wg1=Wg1, Ig1=Ig1, tap_pct=1.55))
    _CACHE[key] = list(u)
    return list(u)


def start_u(spec, fit, corner_fields=None, tap_pct=None, n_grid=None,
            tail_margin_v=0.15, surrogate=None, corners_cf=None, n_calib=3,
            work_dir=None) -> list:
    """Drop-in for `physics.analytic_start_u`, same signature.

    The extra arguments the physics version takes are accepted and ignored:
    `n_grid` and `n_calib` size a grid search and a surrogate calibration loop
    that this derivation does not run, and `tail_margin_v` is fixed at
    TAIL_MARGIN_V because the headroom cap is the whole point of the Itail
    step. They stay in the signature so the two are interchangeable at every
    call site without the caller knowing which one it got.
    """
    from eqrl.start import corner_from
    return derive(spec, fit, corner_from(corner_fields, corners_cf),
                  work_dir)


def patch():
    """Swap this in for physics.analytic_start_u at every binding.

    Kept so the committed experiment scripts reproduce unchanged. New code
    should go through `eqrl.start.use("physfit")` instead -- this function
    cannot reach a module that has not been imported yet, which is exactly the
    case for `deploy_policy` under the MCP server.
    """
    from eqrl import physics as ph
    from eqrl import start

    start.use("physfit")
    # `deploy_policy` no longer needs rebinding -- it reads the seam, which
    # the line above already set. `physics` still does: train_policy,
    # collect_data and check_surrogate import the function directly inside
    # their own bodies rather than through the seam.
    ph.analytic_start_u = start_u
    return start_u
