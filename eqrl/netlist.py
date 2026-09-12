"""Parametric SPICE netlist builder for the source-degenerated CTLE.

Topology (1-stage CTLE with source degeneration, differential), all devices now
real SKY130 parts -- no ideal R, C or current sources in the signal path:

        VDD                         VDD
         |                           |
        RLn (res_high_po)           RLp
         |                           |
       voutn ---+                 +--- voutp
         |      |                 |      |
        CLn    M1                M2     CLp
         |   (vinp)            (vinn)    |
        GND     |                 |     GND
                sp --[Rs||Cs]-- sn          Rs = poly res, Cs = MIM cap
                |                 |
              Mt1               Mt2         REAL NMOS tail pair, gate=vbias
                |                 |
               GND               GND

The tail is a 4:1 NMOS current mirror (Mref diode-connected off an ideal Iref
reference -- a bandgap legitimately lives off-block). This matters: an ideal
current sink has infinite output resistance and costs zero headroom, which hid
a real failure. Measured at the old Vcm=0.9 the tail sat in *triode*
(Vds=152mV < Vdsat=164mV) and delivered 477uA instead of 600uA -- a 20% current
error. Vcm=1.10 puts Vds=311mV against Vdsat=164mV, i.e. 147mV of margin.

Design variables (continuous; W/L in microns, R in ohm, C in farad, I in A):
    W      input-pair device width
    L      input-pair device length
    RL     load resistor (each side)
    Rs     degeneration resistor (between the two source nodes)
    Cs     degeneration capacitor (in parallel with Rs)
    Itail  per-side tail current (set by Iref x MIRROR_RATIO)
    Wtail  tail device width -- trades headroom (bigger W -> lower Vdsat)
           against area and against Cdb loading the degeneration node

Fixed context (not optimised):
    CL     output load capacitance. Stays IDEAL on purpose: it models the next
           stage's input capacitance, not a component we build.
    Vcm    input common-mode bias
    VDD    supply (set per PVT corner)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from . import pdk

# Ordered design-variable names -> (min, max) bounds. The RL agent operates on a
# normalised [0,1] vector; these bounds map it to physical values.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "W":     (1.0,     100.0),      # um
    "L":     (0.15,    1.0),        # um
    "RL":    (100.0,   5000.0),     # ohm
    "Rs":    (50.0,    10000.0),    # ohm
    "Cs":    (10e-15,  3e-12),      # F
    "Itail": (50e-6,   2.0e-3),     # A (per side)
    "Wtail": (5.0,     200.0),      # um (tail device; headroom vs area)
    "Itap":  (10e-6,   1.0e-3),     # A  (DFE tap current; tap weight = Itap*RL)
    "Wfb":   (1.0,     50.0),       # um (DFE feedback pair width)
}
PARAM_NAMES = tuple(PARAM_BOUNDS.keys())

# Which variables are best explored on a logarithmic scale (span >~1 decade).
LOG_PARAMS = {"RL", "Rs", "Cs", "Itail", "Itap"}

# ---------------------------------------------------------------------------
# Device flavours. NFET is the SINGLE SOURCE OF TRUTH: the ngspice device-probe
# path in spice.py is derived from it, so switching flavours here cannot leave a
# stale hardcoded probe behind. (A stale probe fails *silently*: op() returns
# ok=False, Metrics.saturated stays False, and specs.score() then caps every
# candidate's reward at 0.2 -- the search goes flat with no error.)
#
# Measured @ W=20 L=0.18 nf=2, Vgs=Vds=0.9, tt/27C:
#   sky130_fd_pr__nfet_01v8      Vth=691.4mV  gm=6.289mS
#   sky130_fd_pr__nfet_01v8_lvt  Vth=555.9mV  gm=7.992mS  <- +135mV headroom
NFET = "sky130_fd_pr__nfet_01v8_lvt"
RES = "sky130_fd_pr__res_high_po_1p41"   # ports: r0 r1 b   (b = body/substrate)
CAP = "sky130_fd_pr__cap_mim_m3_1"       # ports: c0 c1

# --- Resistor value <-> geometry, MEASURED not derived -----------------------
# The model's `rsheet` is NOT ohms/square: it scales inversely with width
# (rsheet * W ~ 323 ohm/sq for every variant), i.e. it is already ohms per um of
# length at that width. Forcing 10uA through res_high_po_1p41 gave
#   l=2 -> 781.1 ohm,  l=5 -> 1431.3 ohm,  l=10 -> 2568.2 ohm
# which fits R = R_END + R_PER_UM * l. The offset is contact resistance.
RES_W = 1.41
RES_R_END = 334.3        # ohm, end/contact resistance
RES_R_PER_UM = 223.4     # ohm per um of length
RES_L_MIN, RES_L_MAX = 0.5, 60.0
RES_MULT_MAX = 32

# --- MIM cap value <-> geometry, MEASURED ------------------------------------
# 10x10um -> 206.6 fF, 12.25x12.25um -> 308.2 fF, matching
#   C = CAP_AREA * s^2 + CAP_PERIM * 4s      (s = side in um)
CAP_AREA_F_PER_UM2 = 2.00e-15
CAP_PERIM_F_PER_UM = 0.19e-15
CAP_S_MIN, CAP_S_MAX = 2.0, 60.0

# --- Tail current mirror -----------------------------------------------------
MIRROR_RATIO = 4.0       # Itail = Iref * MIRROR_RATIO
L_TAIL = 0.5             # um; long for output resistance + matching

# --- DFE feedback pair -------------------------------------------------------
L_FB = 0.15              # um; minimum length -- this is a current SWITCH
# Feedback logic levels driving the tap pair. NOT 0/VDD: a 0..1.8 swing has a
# 0.9 V common mode, which would leave the feedback tail's source at only
# 0.9-0.77 = 0.13 V and push it into triode (the same trap the input tail hit).
# 0.8/1.4 keeps CM at 1.10 V -- matching Vcm -- while still giving 600 mV of
# differential drive, far more than enough to fully steer the pair.
VFB_LO, VFB_HI = 0.8, 1.4
VFB_CM = 0.5 * (VFB_LO + VFB_HI)


def res_geometry(R: float) -> tuple[float, int]:
    """Target resistance -> (length_um, mult) for RES.

    A single unit cannot go below ~R_END (the contact resistance), so low
    resistances are built from ``mult`` units in parallel.
    """
    mult = 1
    while mult <= RES_MULT_MAX:
        l = (R * mult - RES_R_END) / RES_R_PER_UM
        if l >= RES_L_MIN:
            return (min(l, RES_L_MAX), mult)
        mult += 1
    return (RES_L_MIN, RES_MULT_MAX)


def res_value(l: float, mult: int) -> float:
    """Inverse of :func:`res_geometry` (geometry -> ohms)."""
    return (RES_R_END + RES_R_PER_UM * l) / mult


def cap_side(C: float) -> float:
    """Target capacitance -> square MIM side length in um."""
    # CAP_AREA*s^2 + 4*CAP_PERIM*s - C = 0
    a, b, c = CAP_AREA_F_PER_UM2, 4.0 * CAP_PERIM_F_PER_UM, -C
    s = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)
    return min(CAP_S_MAX, max(CAP_S_MIN, s))


def cap_value(s: float) -> float:
    """Inverse of :func:`cap_side` (side length -> farads)."""
    return CAP_AREA_F_PER_UM2 * s * s + 4.0 * CAP_PERIM_F_PER_UM * s


def _nf(w: float, per_finger: float = 10.0) -> int:
    """Split a wide device into fingers of roughly ``per_finger`` microns."""
    return max(1, int(round(w / per_finger)))


@dataclass
class CtleParams:
    """Physical design variables plus fixed environment context."""

    W: float = 20.0
    L: float = 0.18
    RL: float = 1200.0
    Rs: float = 1500.0
    Cs: float = 300e-15
    Itail: float = 600e-6
    Wtail: float = 60.0
    Itap: float = 300e-6
    Wfb: float = 20.0
    # fixed context
    CL: float = 40e-15
    # Raised from 0.9: with a REAL NMOS tail, Vcm=0.9 put it in triode
    # (Vds=152mV < Vdsat=164mV, delivering 477uA not 600uA). Vcm=1.10 gives
    # Vds=311mV vs Vdsat=164mV -> 147mV margin. Verified, not assumed.
    Vcm: float = 1.10

    # ---- geometry actually instantiated -------------------------------
    def geometry(self) -> dict:
        rl_l, rl_m = res_geometry(self.RL)
        rs_l, rs_m = res_geometry(self.Rs)
        cs_s = cap_side(self.Cs)
        return {
            "rl_l": rl_l, "rl_mult": rl_m, "rl_actual": res_value(rl_l, rl_m),
            "rs_l": rs_l, "rs_mult": rs_m, "rs_actual": res_value(rs_l, rs_m),
            "cs_s": cs_s, "cs_actual": cap_value(cs_s),
            "iref": self.Itail / MIRROR_RATIO,
            "w_ref": self.Wtail / MIRROR_RATIO,
            # The feedback tail shares vbias, so its current ratios purely by
            # width: Itap/Itail = Wfb_tail/Wtail.
            "wfb_tail": max(0.5, self.Wtail * self.Itap / self.Itail),
        }

    def core_netlist(self) -> str:
        """Return the device/topology section (no supply, analysis, or .lib)."""
        g = self.geometry()
        nf_in = _nf(self.W)
        nf_t = _nf(self.Wtail)
        nf_r = _nf(g["w_ref"])
        nf_fb = _nf(self.Wfb)
        nf_ft = _nf(g["wfb_tail"])
        return f"""* --- CTLE core: source-degenerated diff pair, all-real SKY130 devices ---
* input pair
X1 voutn vinp sp 0 {NFET} L={self.L:.4g} W={self.W:.4g} nf={nf_in} mult=1
X2 voutp vinn sn 0 {NFET} L={self.L:.4g} W={self.W:.4g} nf={nf_in} mult=1
* degeneration: real poly resistor + real MIM cap  (b = body tie to substrate)
XRs sp sn 0 {RES} w={RES_W} l={g['rs_l']:.4g} mult={g['rs_mult']}
XCs sp sn {CAP} w={g['cs_s']:.4g} l={g['cs_s']:.4g} mf=1
* loads: real poly resistors
XRLp vdd voutp 0 {RES} w={RES_W} l={g['rl_l']:.4g} mult={g['rl_mult']}
XRLn vdd voutn 0 {RES} w={RES_W} l={g['rl_l']:.4g} mult={g['rl_mult']}
* CL stays ideal: it models the NEXT stage's input capacitance
CLp voutp 0 {self.CL:.6g}
CLn voutn 0 {self.CL:.6g}
* tail: {MIRROR_RATIO:g}:1 NMOS current mirror (Iref sourced from vdd)
Iref vdd vbias DC {g['iref']:.6g}
Xref vbias vbias 0 0 {NFET} L={L_TAIL} W={g['w_ref']:.4g} nf={nf_r} mult=1
Xt1  sp vbias 0 0 {NFET} L={L_TAIL} W={self.Wtail:.4g} nf={nf_t} mult=1
Xt2  sn vbias 0 0 {NFET} L={L_TAIL} W={self.Wtail:.4g} nf={nf_t} mult=1
* --- DFE 1-tap feedback pair: MERGED current summer ---
* Shares RLp/RLn with the CTLE rather than being a separate gain stage, so the
* two drain currents sum directly in the loads. Fully steered, this moves the
* differential output by Itap*RL -- that product IS the tap weight.
X3 voutn vfbp stap 0 {NFET} L={L_FB} W={self.Wfb:.4g} nf={nf_fb} mult=1
X4 voutp vfbn stap 0 {NFET} L={L_FB} W={self.Wfb:.4g} nf={nf_fb} mult=1
Xtf stap vbias 0 0 {NFET} L={L_TAIL} W={g['wfb_tail']:.4g} nf={nf_ft} mult=1
"""

    # ---- area -----------------------------------------------------------
    def area_um2(self) -> dict:
        """Rough active-device area budget in um^2 (no routing/spacing overhead)."""
        g = self.geometry()
        a_in = 2 * self.W * self.L
        a_tail = 2 * self.Wtail * L_TAIL + g["w_ref"] * L_TAIL
        a_dfe = 2 * self.Wfb * L_FB + g["wfb_tail"] * L_TAIL
        a_res = RES_W * (2 * g["rl_l"] * g["rl_mult"] + g["rs_l"] * g["rs_mult"])
        a_cap = g["cs_s"] ** 2
        total = a_in + a_tail + a_dfe + a_res + a_cap
        return {"input_pair": a_in, "tail": a_tail, "dfe": a_dfe,
                "resistors": a_res, "cap": a_cap,
                "total_um2": total, "total_mm2": total * 1e-6}

    def tap_weight_v(self) -> float:
        """Nominal DFE tap weight in volts (differential), = Itap * RL.

        This is the *structural* value. What it SHOULD be set to depends on the
        channel's first post-cursor, which does not exist yet -- so the tap is a
        free parameter until the channel lands.
        """
        return self.Itap * self.RL

    def as_dict(self) -> dict:
        return asdict(self)


def denorm(x: dict[str, float] | list[float]) -> dict[str, float]:
    """Map a normalised [0,1] vector (or dict) to physical parameter values."""
    if isinstance(x, dict):
        vals = x
    else:
        vals = {n: x[i] for i, n in enumerate(PARAM_NAMES)}
    out: dict[str, float] = {}
    for n in PARAM_NAMES:
        lo, hi = PARAM_BOUNDS[n]
        u = min(1.0, max(0.0, float(vals[n])))
        if n in LOG_PARAMS:
            out[n] = float(math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))))
        else:
            out[n] = lo + u * (hi - lo)
    return out


def norm(phys: dict[str, float]) -> list[float]:
    """Inverse of :func:`denorm` (physical -> normalised [0,1])."""
    out = []
    for n in PARAM_NAMES:
        lo, hi = PARAM_BOUNDS[n]
        v = min(hi, max(lo, float(phys[n])))
        if n in LOG_PARAMS:
            u = (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
        else:
            u = (v - lo) / (hi - lo)
        out.append(u)
    return out


def dfe_static_bias() -> str:
    """Hold the feedback pair balanced at its common mode.

    Used for op/ac/noise/HD3: the tap pair then contributes its DC current and
    its drain capacitance (both real loading effects) but injects no
    differential signal, which is what we want when characterising the forward
    path.
    """
    return (f"Vfbp vfbp 0 DC {VFB_CM:.4g}\n"
            f"Vfbn vfbn 0 DC {VFB_CM:.4g}\n")


def dfe_static_tap(sign: float = +1.0) -> str:
    """Fully steer the tap pair one way -- used to MEASURE the tap weight."""
    hi, lo = (VFB_HI, VFB_LO) if sign > 0 else (VFB_LO, VFB_HI)
    return f"Vfbp vfbp 0 DC {hi:.4g}\nVfbn vfbn 0 DC {lo:.4g}\n"


def dfe_decision_path(ui_s: float = 200e-12) -> str:
    """Closed-loop 1-tap DFE: slicer -> 1 UI latch -> differential tap drive.

    Built from XSPICE code models (verified available in this ngspice build).
    Gotcha: adc_bridge/dac_bridge take BRACKETED vector ports, but d_dff takes
    SCALAR ports (data clk set reset out Nout) -- mixing them up gives
    "Scalar connection expected, [ found".

    The slicer/latch is deliberately behavioural: a transistor-level StrongARM
    closing 200 ps clock-to-Q is a separate project and is not what the RL
    sizes. The analog front end is.
    """
    f_clk = 1.0 / ui_s
    return f"""* --- DFE decision path (behavioural, XSPICE) ---
Bdiff vdiff 0 V=V(voutp)-V(voutn)
Vclk clk 0 DC 0 PULSE(0 1.8 {0.5*ui_s:.6g} 10p 10p {0.4*ui_s:.6g} {ui_s:.6g})
adc_in  [vdiff] [ddata] adc_sl
adc_clk [clk]   [dclk]  adc_ck
aff ddata dclk NULL NULL dq dqb dff_1tap
* POLARITY: dqb (NOT dq) drives vfbp. X3's drain is voutn, so vfbp high pulls
* voutn down and drives (voutp-voutn) POSITIVE. Feeding the previous decision
* dq straight to vfbp therefore REINFORCES that bit -- the opposite of what a
* DFE must do. The tap has to SUBTRACT the post-cursor, so the inverted output
* drives vfbp. Measured at Itap=150uA: dq->vfbp gives 0.078 UI, dqb->vfbp gives
* 0.453 UI.
adac_p [dqb] [vfbp] dac_fb
adac_n [dq]  [vfbn] dac_fb
.model adc_sl  adc_bridge(in_low=-0.001 in_high=0.001)
.model adc_ck  adc_bridge(in_low=0.7 in_high=1.1)
.model dac_fb  dac_bridge(out_low={VFB_LO:.4g} out_high={VFB_HI:.4g} out_undef={VFB_CM:.4g} t_rise=15p t_fall=15p)
.model dff_1tap d_dff(clk_delay=20p rise_delay=15p fall_delay=15p)
"""


def build_source_lines(analysis: str, ac_amp: float = 1.0,
                       tran_amp: float = 0.1, tran_freq: float = 100e6) -> str:
    """Input stimulus depends on the analysis being run."""
    if analysis == "ac":
        # 1 V differential AC (0.5 each, anti-phase) on top of the CM bias.
        return ("Vinp vinp 0 DC {Vcm} AC 0.5 0\n"
                "Vinn vinn 0 DC {Vcm} AC 0.5 180\n")
    if analysis == "tran":
        # Differential sine for harmonic-distortion measurement.
        return (f"Vinp vinp 0 DC {{Vcm}} SIN({{Vcm}} {tran_amp/2:.6g} {tran_freq:.6g})\n"
                f"Vinn vinn 0 DC {{Vcm}} SIN({{Vcm}} {-tran_amp/2:.6g} {tran_freq:.6g})\n")
    if analysis in ("noise", "op"):
        return ("Vinp vinp 0 DC {Vcm} AC 0.5 0\n"
                "Vinn vinn 0 DC {Vcm} AC 0.5 180\n")
    raise ValueError(f"unknown analysis {analysis!r}")
