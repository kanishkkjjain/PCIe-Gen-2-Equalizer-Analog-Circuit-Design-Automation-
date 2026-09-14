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
                   cout_c0=40e-15, cout_c1=1.0e-15, cout_c2=1.0e-15,
                   k_pk=1.0, k_f=1.0)

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

        # How optimistic the closed form is, measured rather than assumed --
        # the job analytic.calibrate() does with live SPICE calls, done here
        # from data already collected so a starting design can be derived for a
        # NEW spec without simulating anything.
        #
        # It must be measured with the FITTED gm, not the square-law default:
        # computing it against an unfitted PhysFit folds the gm error into the
        # calibration factor and gave k_pk = 0.81 where the real optimism is
        # ~1.8x.
        self = cls(coef, n_fit=len(rows), quality=quality)
        peaked = [r for r in rows
                  if r["metrics"].get("peaking_db", 0) >= 0.5
                  and r["metrics"].get("f_peak", 0) > 0]
        if len(peaked) >= 20:
            base = analytic_baseline([r["params"] for r in peaked],
                                     [r["corner_fields"] for r in peaked],
                                     self, None)
            with np.errstate(all="ignore"):
                k_pk = float(np.nanmedian(
                    np.asarray(base["peaking_db"])
                    / np.array([r["metrics"]["peaking_db"] for r in peaked])))
                k_f = float(np.nanmedian(
                    np.asarray(base["f_peak"])
                    / np.array([r["metrics"]["f_peak"] for r in peaked])))
            self.coef["k_pk"] = float(np.clip(k_pk, 0.25, 4.0))
            self.coef["k_f"] = float(np.clip(k_f, 0.25, 4.0))
            self.quality["calibration"] = {
                "n": len(peaked), "k_pk": self.coef["k_pk"],
                "k_f": self.coef["k_f"],
                "meaning": "closed form reads this many times high; the start "
                           "targets are pre-divided by it"}
        return self

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
    # the eye, from the pulse response rather than from the AC shape
    "log_h0", "h_pre", "h_post1", "h_post2", "isi_frac", "openness_est",
    "log_eye_est",
    "vdd", "temp_norm", "proc_tt", "proc_ss", "proc_ff", "rc_ll",
)

#: analytic estimates the residual heads are built on, in order
ANALYTIC_TARGETS = ("peaking_db", "f_peak", "dc_gain_db", "power_w")



# ---------------------------------------------------------------------------
# the eye, from the pulse response
# ---------------------------------------------------------------------------
# The eye was the worst-predicted metric by a wide margin -- held-out Spearman
# 0.37 on height and 0.36 on width, against 0.92 for peaking and 0.95 for dc
# gain -- and it carries the HIGHEST weight in the scorer (w_eye = 1.2). A
# surrogate that cannot rank eyes cannot rank designs, and G1 measured exactly
# that: per-metric accuracy rose with more data while the reward correlation
# fell, because the reward is dominated by the metrics the model was worst at.
#
# The fix is to stop asking the network to learn the eye from AC features and
# instead hand it the quantity the eye actually is: the PULSE RESPONSE sampled
# at symbol spacing. Inter-symbol interference is h[-1], h[1], h[2] relative to
# h[0]; the DFE cancels a fraction of h[1]; what is left is the closure. That
# is a computation, not a thing to be learned.

_CHANNEL_CACHE: dict = {}


def channel_H(f):
    """Differential response of the FR4-like channel `netlist.py` builds.

    An ABCD cascade of exactly the ladder in `channel_netlist`: per section, a
    series (Rdc + skin-effect R||L stages + L) and a shunt C, with the source
    resistance in front and half the differential termination as the load.

    The channel does not depend on any design variable, so it is computed once
    per frequency grid and cached.

    Validated against the independently measured response: this returns
    -16.77 dB at the 2.5 GHz Nyquist frequency and 10.68 dB of tilt from DC,
    where direct measurement of the built netlist gives -16.8 dB and 10.6 dB.
    """
    f = np.asarray(f, float)
    key = (f[0], f[-1], len(f))
    hit = _CHANNEL_CACHE.get(key)
    if hit is not None:
        return hit

    s = 2j * np.pi * f
    n = nl.CH_SECTIONS
    td = nl.CH_TD_TOTAL / n
    L, C = nl.CH_Z0 * td, td / nl.CH_Z0

    A = np.ones_like(s)
    B = np.full_like(s, nl.CH_RSRC + 0j)
    Cc = np.zeros_like(s)
    D = np.ones_like(s)

    def mul(a2, b2, c2, d2):
        nonlocal A, B, Cc, D
        A, B, Cc, D = (A * a2 + B * c2, A * b2 + B * d2,
                       Cc * a2 + D * c2, Cc * b2 + D * d2)

    for _ in range(n):
        Z = np.full_like(s, nl.CH_RDC + 0j)
        for Rk, Lk in nl.CH_SKIN:
            Z = Z + (Rk * (s * Lk)) / (Rk + s * Lk)
        Z = Z + s * L
        mul(1.0, Z, 0.0, 1.0)             # series impedance
        mul(1.0, 0.0, s * C, 1.0)         # shunt capacitance

    ZL = nl.CH_RTERM / 2.0
    H = ZL / (A * ZL + B)
    _CHANNEL_CACHE[key] = H
    return H


#: time resolution of the pulse response, as a fraction of one UI
PULSE_OVERSAMPLE = 32
PULSE_NFFT = 2048


def pulse_taps(gm, RL, Rs, Cs, cout, gm_g1, Rsum, ui_s, cl=40e-15):
    """ISI taps (h[-1], h[0], h[1], h[2]) of channel -> CTLE -> summer.

    The cascade is evaluated on a uniform frequency grid, multiplied by the
    spectrum of a single one-UI rectangular symbol, and inverse-transformed.
    The main cursor is the peak of the result; the taps are the samples one and
    two symbol periods either side of it.
    """
    gm, RL, Rs, Cs, cout, gm_g1, Rsum = (
        np.atleast_1d(np.asarray(x, float))
        for x in (gm, RL, Rs, Cs, cout, gm_g1, Rsum))

    dt = ui_s / PULSE_OVERSAMPLE
    f = np.fft.rfftfreq(PULSE_NFFT, dt)
    s = 2j * np.pi * f

    Hch = channel_H(f)[None, :]
    k = (1.0 + gm * Rs / 2.0)[:, None]
    Hctle = ((gm * RL)[:, None] * (1.0 + s * (Rs * Cs)[:, None])
             / ((k + s * (Rs * Cs)[:, None])
                * (1.0 + s * (RL * cout)[:, None])))
    # summing stage: one pole against its own load
    Hsum = (gm_g1 * Rsum)[:, None] / (1.0 + s * (Rsum * cl)[:, None])

    # spectrum of one rectangular UI, sampled on this grid
    Pf = ui_s * np.sinc(f * ui_s) * np.exp(-1j * np.pi * f * ui_s)

    p = np.fft.irfft(Hch * Hctle * Hsum * Pf[None, :], n=PULSE_NFFT, axis=-1)

    i0 = np.argmax(np.abs(p), axis=-1)
    n_ui = PULSE_OVERSAMPLE
    idx = lambda d: np.clip(i0 + d * n_ui, 0, PULSE_NFFT - 1)
    take = lambda j: p[np.arange(len(p)), j]
    return take(idx(-1)), take(idx(0)), take(idx(1)), take(idx(2))


def eye_features(gm, RL, Rs, Cs, cout, gm_g1, Rsum, tap_pct, ui_s,
                 cl=40e-15) -> dict:
    """What the pulse response says about the eye, before any learning.

    `eye_est` is the classic worst-case opening: the main cursor less every
    interfering tap, with the DFE crediting back the fraction of the first
    post-cursor its tap weight cancels. It is not the measured eye -- there is
    no slicer, no latch delay and no noise here -- but it is the right SHAPE,
    which is what the residual has to be a correction to rather than a
    replacement for.
    """
    hm1, h0, h1, h2 = pulse_taps(gm, RL, Rs, Cs, cout, gm_g1, Rsum, ui_s, cl)
    a0 = np.maximum(np.abs(h0), 1e-18)
    tap = np.asarray(tap_pct, float) / 100.0
    resid1 = np.abs(h1 - tap * h0)          # what the DFE leaves behind
    isi = np.abs(hm1) + resid1 + np.abs(h2)
    return {"h0": h0, "h_pre": hm1 / a0, "h_post1": h1 / a0,
            "h_post2": h2 / a0, "isi_frac": isi / a0,
            "eye_est": 2.0 * np.maximum(a0 - isi, 0.0),
            "openness_est": np.maximum(1.0 - isi / a0, 0.0)}


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

    ey = eye_features(gm, P["RL"], P["Rs"], P["Cs"], cout, gmg, P["Rsum"],
                      P["tap_pct"], meas.ui_s, CL)

    feats = np.column_stack([
        lg(gm), lg(cout), lg(boost), lg(fz), lg(fp1), lg(fp2),
        np.clip(fp1 / np.maximum(fp2, 1e-30), 0, 100),
        lg(RsCs / meas.ui_s),
        pk, lg(fpk), dc, gnyq,
        lg(summer_gain), lg(gmg), np.clip(vov_g1, 0, 2.0),
        P["tap_pct"] / 100.0,
        cm_head, lg(power), lg(area),
        lg(np.abs(ey["h0"])),
        np.clip(ey["h_pre"], -5, 5), np.clip(ey["h_post1"], -5, 5),
        np.clip(ey["h_post2"], -5, 5), np.clip(ey["isi_frac"], 0, 10),
        ey["openness_est"], lg(ey["eye_est"]),
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



#: `analytic.solve` searches an (Rs, Cs) grid against a 900-point frequency
#: sweep -- hundreds of milliseconds, which made env.reset() 30x the cost of a
#: step. It depends only on (gm, Cout, target peaking, target f_peak), and gm
#: and Cout come from FIXED_START and so never vary between episodes. The
#: targets are therefore rounded and the answer reused. The bins are coarse on
#: purpose: 0.5 dB and 100 MHz is far below the N(0, 0.1) perturbation applied
#: to the start in normalised units immediately afterwards, so finer bins buy
#: no accuracy and only cost cache misses -- at 0.25 dB / 25 MHz the cache held
#: 9 entries after 400 steps and `analytic._shape` still ran on 2 % of resets,
#: which was 40 % of the whole step budget.
_SOLVE_CACHE: dict = {}
_SOLVE_CACHE_MAX = 4096


def _solve_cached(gm, cout, tgt_pk, tgt_f, n_grid):
    from . import analytic
    key = (round(gm, 9), round(cout, 18), round(tgt_pk / 0.5),
           round(tgt_f / 100e6), n_grid)
    hit = _SOLVE_CACHE.get(key)
    if hit is None:
        hit = analytic.solve(gm, cout, tgt_pk, tgt_f, n=n_grid)
        if len(_SOLVE_CACHE) >= _SOLVE_CACHE_MAX:
            _SOLVE_CACHE.clear()
        _SOLVE_CACHE[key] = hit
    return hit


def analytic_start_u(spec, fit: PhysFit, corner_fields: dict | None = None,
                     tap_pct: float | None = None, n_grid: int = 300) -> list:
    """A hand-calculated starting design for ANY spec, using no SPICE at all.

    `analytic.initial_design` does this properly -- it measures gm and Cout from
    an .op and closes a calibration loop against four AC sweeps. That costs five
    ngspice launches, which is fine once per design run and impossible inside an
    RL loop that resets thousands of times.

    This is the same derivation with the SPICE replaced by fitted estimates:
    gm and Cout from `PhysFit`, and the model's optimism corrected by the k_pk /
    k_f factors measured across the whole collected dataset instead of
    re-measured per design. It aims at the CENTRE of the spec's windows, which
    is what buys PVT margin.

    This is what makes the policy's claim a real one: given a new spec, the
    starting point is derived for THAT spec rather than borrowed from the one
    the project was tuned on.
    """
    from . import analytic
    from .netlist import CtleParams

    cf = corner_fields or {"process": "tt", "vdd": 1.8, "temp": 27.0, "rc": None}
    fixed = dict(analytic.FIXED_START)
    tap = analytic.TAP_PCT_START if tap_pct is None else tap_pct

    gm = float(np.atleast_1d(fit.gm_m1(fixed["W"], fixed["L"],
                                       fixed["Itail"], cf["temp"]))[0])
    cout = float(np.atleast_1d(fit.cout(fixed["W"], fixed["L"],
                                        fixed["Wg1"]))[0])

    tgt_pk = 0.5 * (spec.peaking_min_db + spec.peaking_max_db)
    tgt_f = 0.5 * (spec.fpeak_lo + spec.fpeak_hi)
    k_pk = max(0.25, fit.coef.get("k_pk", 1.0))
    k_f = max(0.25, fit.coef.get("k_f", 1.0))

    RL, Rs, Cs = _solve_cached(gm, cout, tgt_pk * k_pk, tgt_f * k_f, n_grid)
    rsum = analytic._rsum_for_midsupply(
        _CornerLike(cf["vdd"]), fixed["Ig1"])
    p = CtleParams(**fixed, tap_pct=tap, RL=RL, Rs=Rs, Cs=Cs, Rsum=rsum)
    return nl.norm({k: getattr(p, k) for k in PARAM_NAMES})


class _CornerLike:
    """Just enough of pdk.Corner for `_rsum_for_midsupply`, without a PDK."""

    def __init__(self, vdd: float):
        self.vdd = vdd


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
