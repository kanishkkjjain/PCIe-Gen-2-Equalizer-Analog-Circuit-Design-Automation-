"""The systematic starting point, as a drop-in for physics.analytic_start_u.

Same derivation we built, generalised to any spec:
  Wg1 = 100*W_MIN            (tap faithful at its lowest setting)
  Rsum = VDD/Ig1             (output CM at mid supply)
  Cout measured by .op       (includes CL, unchanged)
  f_p2 = 2.13 * f_target     (ratio from the calibrated design: 4.0/1.875)
  RL   = 1/(2pi*f_p2*Cout)
  k    = target_peaking + 6.5 dB   (over-ask; f_p2 roll-off eats the rest)
  gm   = k/RL ;  Rs = 2(k-1)/gm    (DC gain pinned at 0 dB)
  f_p1 = 0.80 * f_target ;  Cs = k/(2pi*Rs*f_p1)
  Vov  = (Vcm - 0.10 - Vt)/2       (ICMR budget)
  W, Wtail by gm/ID bisection to that Vov
Cached per spec: the start depends on the spec alone, not on the rollout.
"""
import math
import numpy as np
from eqrl import netlist as nl, pdk
from eqrl.spice import Simulator, _parse_prints

F_P2_RATIO, F_P1_RATIO, OVER_ASK_DB, ICMR_MARGIN = 2.133, 0.800, 6.5, 0.10
_CACHE = {}
_SIM = None
D  = f"@m.x1.m{nl.NFET}"; G1 = f"@m.xg1p.m{nl.NFET}"; T = f"@m.xt1.m{nl.NFET}"


def _sim():
    global _SIM
    if _SIM is None:
        _SIM = Simulator("/tmp/eqrl_newstart", use_channel=False, timeout=300)
    return _SIM


def _op(p, corner, keys):
    s = _sim()
    ctl = ".control\nop\nprint " + " ".join(keys) + "\n.endc\n"
    ok, log = s._run(s._assemble(p, corner, "op", ctl, dfe="cm",
                                 channel=False), "op")
    if not ok:
        return {k: float("nan") for k in keys}
    v = _parse_prints(log)
    return {k: v.get(k, float("nan")) for k in keys}


def _clip(x, name):
    lo, hi = nl.PARAM_BOUNDS[name]
    return float(min(max(x, lo), hi))


def derive(spec, corner=None):
    corner = corner or pdk.typical_corner()
    key = (round(getattr(spec, "peaking_target_db", 7.5), 3),
           round(spec.fpeak_lo, -6), round(spec.fpeak_hi, -6),
           round(corner.vdd, 3), corner.process)
    if key in _CACHE:
        return list(_CACHE[key])

    Wg1 = _clip(100.0 * nl.W_MIN_UM, "Wg1")
    Ig1 = 1.0e-3
    Rsum = _clip(corner.vdd / Ig1, "Rsum")
    base = dict(W=20.0, L=0.18, RL=1200.0, Rs=1500.0, Cs=3e-13,
                Itail=600e-6, Wtail=60.0, Rsum=Rsum, Wg1=Wg1, Ig1=Ig1)
    prov = nl.CtleParams(**base, tap_pct=1.0)
    m = _op(prov, corner, [f"{D}[cdd]", f"{G1}[cgs]", f"{G1}[cgd]", f"{D}[vth]"])
    Cout = (abs(m[f"{D}[cdd]"]) + abs(m[f"{G1}[cgs]"])
            + abs(m[f"{G1}[cgd]"]) + prov.CL)
    Vt = m[f"{D}[vth]"]
    if not (Cout == Cout and Vt == Vt):
        raise RuntimeError("start .op failed")

    f_tgt = 0.5 * (spec.fpeak_lo + spec.fpeak_hi)
    RL = _clip(1.0 / (2 * math.pi * F_P2_RATIO * f_tgt * Cout), "RL")
    pk_db = getattr(spec, "peaking_target_db", None)
    if pk_db is None:
        pk_db = 0.5 * (spec.peaking_min_db + spec.peaking_max_db)
    k = 10 ** ((pk_db + OVER_ASK_DB) / 20.0)
    gm = k / RL
    Rs = _clip(2.0 * (k - 1.0) / gm, "Rs")
    Cs = _clip(k / (2 * math.pi * Rs * F_P1_RATIO * f_tgt), "Cs")
    Vov = max(0.05, (prov.Vcm - ICMR_MARGIN - Vt) / 2.0)
    Itail = _clip(2.0 * (gm * Vov / 2.0), "Itail")

    def bisect(which, lo, hi):
        pre = D if which == "pair" else T
        w = 0.5 * (lo + hi)
        for _ in range(14):
            w = 0.5 * (lo + hi)
            kw = dict(base); kw.update(RL=RL, Rs=Rs, Cs=Cs, Itail=Itail)
            kw["W" if which == "pair" else "Wtail"] = w
            r = _op(nl.CtleParams(**kw, tap_pct=1.0), corner,
                    [f"{pre}[vgs]", f"{pre}[vth]"])
            v = r[f"{pre}[vgs]"] - r[f"{pre}[vth]"]
            if v != v:
                return float("nan")
            if v > Vov: lo = w
            else: hi = w
        return w

    W = bisect("pair", *nl.PARAM_BOUNDS["W"])
    Wtail = bisect("tail", *nl.PARAM_BOUNDS["Wtail"])
    if not (W == W and Wtail == Wtail):
        raise RuntimeError("gm/ID bisection failed")

    phys = dict(W=_clip(W, "W"), L=0.18, RL=RL, Rs=Rs, Cs=Cs,
                Itail=Itail, Wtail=_clip(Wtail, "Wtail"), Rsum=Rsum,
                Wg1=Wg1, Ig1=Ig1, tap_pct=1.55)
    u = nl.norm(phys)
    _CACHE[key] = list(u)
    return list(u)


def start_u(spec, fit, corner_fields=None, tap_pct=None, n_grid=None,
            tail_margin_v=0.15, surrogate=None, corners_cf=None,
            n_calib=3) -> list:
    """Drop-in for `physics.analytic_start_u`, same signature.

    `fit` is accepted and unused: this derivation measures every device value
    with ngspice, which is the whole difference between it and
    `start_physfit`. The argument stays so the two are interchangeable.
    """
    from eqrl.start import corner_from
    return derive(spec, corner_from(corner_fields, corners_cf))


def patch():
    """Select this derivation. Kept so the committed measurement scripts run.

    New code should call `eqrl.start.use("systematic")`. `physics` is still
    rebound because train_policy, collect_data and check_surrogate import the
    function directly inside their own bodies rather than through the seam.
    """
    from eqrl import physics as ph
    from eqrl import start

    start.use("systematic")
    # `deploy_policy` no longer needs rebinding -- it reads the seam, which
    # the line above already set. `physics` still does: train_policy,
    # collect_data and check_surrogate import the function directly inside
    # their own bodies rather than through the seam.
    ph.analytic_start_u = start_u
    return start_u
