"""Closed-form circuit knowledge, usable without SPICE.

`analytic.py` already implements these equations, but it MEASURES gm and Cout
from an ``.op`` before using them.  A surrogate model has to run thousands of
times per second inside an RL loop, so it cannot call ngspice at all -- it needs
the same equations driven by gm and Cout ESTIMATED from geometry.

That estimate is fitted from the collected data rather than assumed, because
sky130 at these sizes is nowhere near square-law.  What the fit provides is a
smooth, monotone, physically-shaped function of (W, L, I) that the neural
network can then correct, instead of the network having to discover
``gm ~ sqrt(W/L * I)`` from scratch.

Why bother, rather than letting a plain network learn everything: measured on
this circuit, the hand equations over-predict peaking by about 2x and f_peak by
about 1.5x, but they do so CONSISTENTLY -- a two-parameter calibration brings
them to 1.6 % and 5 %. A consistent bias is exactly what a residual model
removes cheaply, and it means the model starts out already knowing the shape of
the response surface rather than learning it from a few thousand points.

The reference design (Aldacher, SERDES RX DFE, CTLE report p.45) uses these
same equations and reports the same optimism: 12.68 dB predicted against 10 dB
simulated.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import netlist as nl
from .netlist import L_GM, L_TAIL, MIRROR_RATIO, PARAM_NAMES

BOLTZ = 1.380649e-23
Q = 1.602176634e-19
T0_K = 273.15


# ---------------------------------------------------------------------------
# device-level estimates, fitted from data
# ---------------------------------------------------------------------------
class PhysFit:
    """Geometry -> (gm, Cout) estimates, fitted from collected .op data.

    Square law says ``gm = sqrt(2 mu Cox (W/L) Id)``, i.e. a straight line in
    log space with slope 1/2 on both ``W/L`` and ``Id``.  Short-channel sky130
    devices do not obey that, so the exponents are FITTED and the square-law
    values are only the starting point.  Temperature enters through mobility
    (mu ~ T^-1.5), which is fitted too.
    """

    #: square-law starting point, replaced by `fit`
    DEFAULT = dict(gm_a=-5.0, gm_wl=0.5, gm_id=0.5, gm_t=0.0,
                   gmg_a=-5.0, gmg_wl=0.5, gmg_id=0.5, gmg_t=0.0, gmg_v=0.0,
                   cout_c0=40e-15, cout_c1=1.0e-15, cout_c2=1.0e-15)

    def __init__(self, coef: dict | None = None, n_fit: int = 0,
                 quality: dict | None = None):
        self.coef = dict(self.DEFAULT)
        if coef:
            self.coef.update(coef)
        self.n_fit = n_fit
        self.quality = quality or {}

    # -- the estimates ----------------------------------------------------
    def gm_m1(self, W, L, Itail, temp_c):
        """Transconductance of one CTLE input device (carries Itail/2)."""
        c = self.coef
        wl = np.maximum(np.asarray(W, float) / np.asarray(L, float), 1e-9)
        idd = np.maximum(np.asarray(Itail, float) / 2.0, 1e-12)
        tk = (np.asarray(temp_c, float) + T0_K) / (27.0 + T0_K)
        return np.exp(c["gm_a"] + c["gm_wl"] * np.log(wl)
                      + c["gm_id"] * np.log(idd) + c["gm_t"] * np.log(tk))

    def gm_g1(self, Wg1, Ig1, temp_c, vgs=None):
        """Transconductance of the summing stage's main input device.

        Geometry and current alone fit this BADLY (R^2 0.17 in log space,
        77 % median error, measured). The reason is physical: Gm1's gate is
        driven by the CTLE output, whose common mode is
        ``vdd - Itail/2 * RL`` -- so how hard this device is on depends on the
        FIRST stage's bias point, not only its own. Feeding that Vgs estimate
        in takes the fit to a usable place; without it the network would be
        handed a feature that is mostly noise.
        """
        c = self.coef
        wl = np.maximum(np.asarray(Wg1, float) / L_GM, 1e-9)
        idd = np.maximum(np.asarray(Ig1, float) / 2.0, 1e-12)
        tk = (np.asarray(temp_c, float) + T0_K) / (27.0 + T0_K)
        out = (c["gmg_a"] + c["gmg_wl"] * np.log(wl)
               + c["gmg_id"] * np.log(idd) + c["gmg_t"] * np.log(tk))
        if vgs is not None:
            out = out + c["gmg_v"] * np.asarray(vgs, float)
        return np.exp(out)

    @staticmethod
    def vgs_g1(vdd, Itail, RL):
        """CTLE output common mode = the gate bias the summing stage sees."""
        return np.asarray(vdd, float) - np.asarray(Itail, float) / 2.0 * np.asarray(RL, float)

    def cout(self, W, L, Wg1, CL=40e-15):
        """Total capacitance on the CTLE output node.

        Not just CL: the summing stage's Gm1 gate capacitance more than doubles
        it (measured 40 fF -> 67 fF), which is why f_p2 lands where it does.
        """
        c = self.coef
        return (CL + c["cout_c1"] * np.asarray(W, float) * np.asarray(L, float)
                + c["cout_c2"] * np.asarray(Wg1, float) * L_GM)

    # -- fitting ----------------------------------------------------------
    @classmethod
    def fit(cls, records) -> "PhysFit":
        """Least squares in log space against measured .op quantities."""
        rows = [r for r in records
                if r["metrics"].get("ok") and r["metrics"].get("gm_m1", 0) > 0]
        if len(rows) < 20:
            return cls()

        def _fit_gm(gm_key, wl_of, id_of, extra_of=None):
            A, y = [], []
            for r in rows:
                g = r["metrics"].get(gm_key)
                if not (g and g > 0 and g == g):
                    continue
                p, cf = r["params"], r["corner_fields"]
                t = cf["temp"]
                wl, idd = wl_of(p), id_of(p)
                if wl <= 0 or idd <= 0:
                    continue
                row = [1.0, math.log(wl), math.log(idd),
                       math.log((t + T0_K) / (27.0 + T0_K))]
                if extra_of is not None:
                    row.append(float(extra_of(p, cf)))
                A.append(row)
                y.append(math.log(g))
            if len(y) < 20:
                return None, {}
            A, y = np.asarray(A), np.asarray(y)
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
            pred = A @ beta
            ss = 1.0 - np.var(y - pred) / max(np.var(y), 1e-30)
            return beta, {"n": len(y), "r2_log": float(ss),
                          "median_rel_err": float(np.median(
                              np.abs(np.exp(pred) - np.exp(y)) / np.exp(y)))}

        coef, quality = {}, {}
        b, q = _fit_gm("gm_m1", lambda p: p["W"] / p["L"],
                       lambda p: p["Itail"] / 2.0)
        if b is not None:
            coef.update(gm_a=b[0], gm_wl=b[1], gm_id=b[2], gm_t=b[3])
            quality["gm_m1"] = q
        b, q = _fit_gm("gm_g1", lambda p: p["Wg1"] / L_GM,
                       lambda p: p["Ig1"] / 2.0,
                       lambda p, cf: cls.vgs_g1(cf["vdd"], p["Itail"], p["RL"]))
        if b is not None:
            coef.update(gmg_a=b[0], gmg_wl=b[1], gmg_id=b[2], gmg_t=b[3],
                        gmg_v=b[4])
            quality["gm_g1"] = q
        return cls(coef, n_fit=len(rows), quality=quality)

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"coef": {k: float(v) for k, v in self.coef.items()},
                "n_fit": self.n_fit, "quality": self.quality}

    @classmethod
    def from_dict(cls, d: dict) -> "PhysFit":
        return cls(d.get("coef"), d.get("n_fit", 0), d.get("quality"))

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path) -> "PhysFit":
        return cls.from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# the small-signal shape, analytically
# ---------------------------------------------------------------------------
#: frequency grid the analytic peak search runs on. Bounded on purpose: an
#: unbounded argmax can return the grid edge for a degenerate design, and
#: log(f_peak) of a 20 GHz edge value is a feature the network has to unlearn.
F_GRID = np.logspace(6, 10.3, 160)          # 1 MHz .. 20 GHz
F_PEAK_LO, F_PEAK_HI = 1e8, 1.5e10


def ctle_shape(gm, RL, Rs, Cs, cout):
    """(f_peak, peaking_dB, dc_gain_dB, gain_at_nyquist_dB) per design.

    Vectorised over the leading axis.  Same half-circuit transfer function
    `analytic.py` documents::

        Av(s) = -gm RL (1 + s Rs Cs) / ((1 + gm Rs/2 + s Rs Cs)(1 + s RL Cout))
    """
    gm, RL, Rs, Cs, cout = (np.atleast_1d(np.asarray(x, float))
                            for x in (gm, RL, Rs, Cs, cout))
    f = F_GRID[None, :]
    jw = 2j * np.pi * f
    k = 1.0 + gm[:, None] * Rs[:, None] / 2.0
    num = (gm[:, None] * RL[:, None]) * (1.0 + jw * Rs[:, None] * Cs[:, None])
    den = ((k + jw * Rs[:, None] * Cs[:, None])
           * (1.0 + jw * RL[:, None] * cout[:, None]))
    g = 20.0 * np.log10(np.abs(num / den) + 1e-30)

    i = np.argmax(g, axis=-1)
    fpk = F_GRID[i]
    dc = g[:, 0]
    pk = g[np.arange(len(g)), i] - dc
    return np.clip(fpk, F_PEAK_LO, F_PEAK_HI), pk, dc, g


def gain_at(g_curve, f_hz):
    """Interpolate a dB curve produced by `ctle_shape` at one frequency.

    Returns one value per design, shape (n,).
    """
    return np.array([float(np.interp(np.log10(f_hz), np.log10(F_GRID), row))
                     for row in np.atleast_2d(g_curve)])


# ---------------------------------------------------------------------------
# the feature vector
# ---------------------------------------------------------------------------
#: names in order; exported so tests and the docs can refer to them
FEATURE_NAMES = (
    "log_gm", "log_cout", "log_boost", "log_fz", "log_fp1", "log_fp2",
    "fp1_over_fp2", "log_RsCs_in_UI",
    "pk_analytic_db", "fpeak_analytic_log", "dc_gain_analytic_db",
    "gain_nyq_analytic_db",
    "log_summer_gain", "log_gm_g1", "vov_g1", "tap_ratio",
    "cm_headroom_v", "log_power_analytic", "log_area",
    "vdd", "temp_norm", "proc_tt", "proc_ss", "proc_ff", "rc_ll",
)

#: analytic estimates the residual heads are built on, in order
ANALYTIC_TARGETS = ("peaking_db", "f_peak", "dc_gain_db", "power_w")


def supply_power(vdd, Itail, Ig1, tap_pct):
    """Supply power from the branches the netlist actually instantiates.

    Not ``vdd * (Itail + Ig1_branch)``, which is what this looked like it
    should be and is wrong by more than a factor of two. `netlist.py` builds
    the CTLE with SEPARATE per-side tails -- Xt1 and Xt2, each mirrored 4:1 off
    the same diode-connected reference -- so the pair draws ``2 * Itail``, and
    the reference branch draws another ``Itail / MIRROR_RATIO``.

    Verified against collected data rather than read off the schematic: using
    the MEASURED id_tail in this structure reproduces i_supply with a median
    ratio of 1.000 when the tail is in saturation. The spread that remains is
    tail DELIVERY -- a tail that falls into triode sources about 87 % of the
    requested current (measured, n=66) -- which depends on Wtail, Itail, RL and
    vdd and is left to the learned residual, being exactly the kind of thing a
    closed form does not capture.
    """
    return (np.asarray(vdd, float)
            * (2.0 * np.asarray(Itail, float)
               + np.asarray(Itail, float) / MIRROR_RATIO
               + np.asarray(Ig1, float)
               * (1.0 + np.asarray(tap_pct, float) / 100.0)))


def physics_features(params, corner_fields, fit: PhysFit, meas) -> np.ndarray:
    """Physically meaningful, SPICE-free description of (design, corner).

    `params` may be one dict or a list of dicts.  Returns (n, len(FEATURE_NAMES)).

    Every quantity here is something a designer would compute by hand before
    simulating.  Giving them to the network directly means it does not have to
    rediscover, from a few thousand samples, that the boost is only realised
    when f_p1 sits below f_p2 -- the single trap that dominates this topology.
    """
    if isinstance(params, dict):
        params = [params]
    if isinstance(corner_fields, dict):
        corner_fields = [corner_fields] * len(params)

    P = {k: np.array([p[k] for p in params], float) for k in PARAM_NAMES}
    CL = np.array([p.get("CL", 40e-15) for p in params], float)
    Vcm = np.array([p.get("Vcm", 1.10) for p in params], float)
    vdd = np.array([c["vdd"] for c in corner_fields], float)
    temp = np.array([c["temp"] for c in corner_fields], float)
    proc = [c["process"] for c in corner_fields]
    rc = [c.get("rc") for c in corner_fields]

    gm = fit.gm_m1(P["W"], P["L"], P["Itail"], temp)
    gmg = fit.gm_g1(P["Wg1"], P["Ig1"], temp,
                    vgs=fit.vgs_g1(vdd, P["Itail"], P["RL"]))
    cout = fit.cout(P["W"], P["L"], P["Wg1"], CL)

    boost = 1.0 + gm * P["Rs"] / 2.0
    RsCs = P["Rs"] * P["Cs"]
    fz = 1.0 / (2 * np.pi * RsCs)
    fp1 = boost / (2 * np.pi * RsCs)
    fp2 = 1.0 / (2 * np.pi * P["RL"] * cout)

    fpk, pk, dc, curve = ctle_shape(gm, P["RL"], P["Rs"], P["Cs"], cout)
    gnyq = gain_at(curve, meas.nyquist)

    # summing stage
    summer_gain = gmg * P["Rsum"]
    vov_g1 = 2.0 * (P["Ig1"] / 2.0) / np.maximum(gmg, 1e-12)
    cm_head = vdd - 0.5 * P["Ig1"] * P["Rsum"]

    power = supply_power(vdd, P["Itail"], P["Ig1"], P["tap_pct"])
    area = (P["W"] * P["L"] + P["Wtail"] * L_TAIL
            + P["Wg1"] * L_GM * (1.0 + P["tap_pct"] / 100.0)) * 1e-6

    def lg(x, floor=1e-30):
        return np.log(np.maximum(np.asarray(x, float), floor))

    feats = np.column_stack([
        lg(gm), lg(cout), lg(boost), lg(fz), lg(fp1), lg(fp2),
        np.clip(fp1 / np.maximum(fp2, 1e-30), 0, 100),
        lg(RsCs / meas.ui_s),
        pk, lg(fpk), dc, gnyq,
        lg(summer_gain), lg(gmg), np.clip(vov_g1, 0, 2.0),
        P["tap_pct"] / 100.0,
        cm_head, lg(power), lg(area),
        vdd, (temp - 27.0) / 100.0,
        np.array([p == "tt" for p in proc], float),
        np.array([p == "ss" for p in proc], float),
        np.array([p == "ff" for p in proc], float),
        np.array([r == "ll" for r in rc], float),
    ])
    return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)


def analytic_baseline(params, corner_fields, fit: PhysFit, meas) -> dict:
    """The hand-calculated prediction the residual heads correct.

    Returned in the SAME units as `Metrics`, so a residual of zero means "the
    model agrees with the hand calculation exactly".
    """
    if isinstance(params, dict):
        params = [params]
    if isinstance(corner_fields, dict):
        corner_fields = [corner_fields] * len(params)
    P = {k: np.array([p[k] for p in params], float) for k in PARAM_NAMES}
    CL = np.array([p.get("CL", 40e-15) for p in params], float)
    vdd = np.array([c["vdd"] for c in corner_fields], float)
    temp = np.array([c["temp"] for c in corner_fields], float)

    gm = fit.gm_m1(P["W"], P["L"], P["Itail"], temp)
    cout = fit.cout(P["W"], P["L"], P["Wg1"], CL)
    fpk, pk, dc, _ = ctle_shape(gm, P["RL"], P["Rs"], P["Cs"], cout)
    power = supply_power(vdd, P["Itail"], P["Ig1"], P["tap_pct"])
    return {"peaking_db": pk, "f_peak": fpk, "dc_gain_db": dc,
            "power_w": power}


def area_mm2(params) -> np.ndarray:
    """Exact, never predicted -- a deterministic function of geometry.

    Uses the same `CtleParams.area_um2()` the simulator reports, so a surrogate
    Metrics carries the true area rather than a modelled one. Predicting a
    quantity that is exactly computable would add error for nothing.
    """
    if isinstance(params, dict):
        params = [params]
    out = []
    for p in params:
        cp = nl.CtleParams(**{k: p[k] for k in PARAM_NAMES})
        out.append(float(cp.area_um2()["total_mm2"]))
    return np.array(out)
