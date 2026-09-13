"""Parametric SPICE netlist builder: CTLE + a separate DFE summing stage.

TWO stages, matching the reference architecture. The DFE is NOT merged into the
CTLE loads -- it is its own summing stage fed by two transconductors:

    STAGE 1: CTLE (source-degenerated diff pair)
        VDD                         VDD
         |                           |
        RLn (res_high_po)           RLp
         |                           |
     outn_ctle --+               +-- outp_ctle
         |       |               |       |
        CLn     X1              X2      CLp
         |    (vinp)          (vinn)     |
        GND      |               |      GND
                 sp --[Rs||Cs]-- sn        Rs = poly res, Cs = MIM cap
                 |               |
               Xt1             Xt2         REAL NMOS tail, gate=vbias

    STAGE 2: DFE summing stage (Gm1 + GmF share ONE pair of loads)
        VDD                         VDD
         |                           |
       XRsn (Rsum)                 XRsp
         |                           |
     outn_dfe -----+-------+       outp_dfe -----+-------+
         |         |       |           |         |       |
       XG1p      XGFp      |         XG1n      XGFn      |
     (outp_ctle) (vfbp)    |       (outn_ctle) (vfbn)    |
         |         |       |           |         |       |
        sgm1     sgmf      +-----------+---------+
         |         |
        XT1       XTF                  separate tails, both off vbias

Summing happens in XRsp/XRsn: Gm1 carries the equalised signal, GmF injects the
previous decision, and the two drain currents add in the shared loads. Tap
weight is therefore the RATIO gmF/gm1 -- set independently of the CTLE gain,
which is the whole point of splitting the stages.

The decision path (slicer + 1 UI latch) samples outp_dfe/outn_dfe and is
deliberately IDEAL (XSPICE code models), as in the reference schematic.

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
from dataclasses import dataclass, asdict


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
    # --- DFE summing stage -------------------------------------------------
    # Tap weight is now the ratio gmF/gm1, not an absolute current, so `Itap`
    # and `Wfb` are gone. Both transconductors get their own width and tail
    # current; Rsum is shared (it IS the summing node).
    "Rsum":  (100.0,   2000.0),     # ohm  (summing-node load, each side)
    "Wg1":   (1.0,     100.0),      # um   (Gm1 = main signal transconductor)
    "Ig1":   (100e-6,  4.0e-3),     # A    (Gm1 tail current)
    # TAP WEIGHT AS ONE VARIABLE, not two. A current-steering DFE tap is a
    # SCALED COPY of the main transconductor: both its width and its tail
    # current are tap_pct of the main branch, so it runs at the same current
    # density (same Vgs, same Vdsat, same switching behaviour) and its gm
    # scales LINEARLY with tap_pct:
    #     I_TAP  = tap% * I_MAIN
    #     W_TAP  = tap% * W_MAIN
    # Sizing W and I independently -- which this used to do -- puts the tap
    # devices at a different current density from the pair they are supposed
    # to match, and makes gm go as sqrt(W*I) instead of linearly, so "30% tap"
    # did not mean 30% of the main path at all.
    #
    # Range: a 1-tap DFE optimum is a few percent. Measured on this design the
    # eye peaks near 5% and collapses by 30% -- matching the reference sweep
    # (5% best, 30% worse than no equalisation at all).
    "tap_pct": (1.0,   30.0),       # % of the main Gm1 branch
}
PARAM_NAMES = tuple(PARAM_BOUNDS.keys())

# Which variables are best explored on a logarithmic scale (span >~1 decade).
LOG_PARAMS = {"RL", "Rs", "Cs", "Itail", "Rsum", "Ig1"}

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

# PARALLEL UNITS MUST USE `m=`, NOT `mult=`. Measured, not assumed: in
# sky130_fd_pr__res_high_po_1p41 the `mult` parameter appears ONLY inside the
# Monte-Carlo mismatch expressions (`res_match`, `rend`) and never divides the
# two resistance elements, so `mult=N` leaves the resistance UNCHANGED. With
# MC_MM_SWITCH=0 (the default) it does nothing whatsoever.
#
#   forcing 1 V through w=1.41 l=5:
#     one unit            1407.6 ohm
#     mult=4              1407.6 ohm   <- no effect at all
#     m=4                  351.9 ohm   <- correct, = 1407.6/4
#     4 explicit parallel  351.9 ohm   <- ground truth, matches m=
#
# `m` is ngspice's subcircuit multiplier and replicates the whole subckt. This
# was silently wrong for every R below ~450 ohm (the contact-resistance floor,
# where res_geometry starts returning mult>1): Rs=50 was simulated as 450 ohm,
# 9x too large. The DEFAULTS all resolve to mult=1, which is why every measured
# number recorded so far is self-consistent -- it would have fired the moment
# the optimiser explored the low end of the RL/Rs bounds.

# --- MIM cap value <-> geometry, MEASURED ------------------------------------
# 10x10um -> 206.6 fF, 12.25x12.25um -> 308.2 fF, matching
#   C = CAP_AREA * s^2 + CAP_PERIM * 4s      (s = side in um)
CAP_AREA_F_PER_UM2 = 2.00e-15
CAP_PERIM_F_PER_UM = 0.19e-15
CAP_S_MIN, CAP_S_MAX = 2.0, 60.0

# --- Tail current mirror -----------------------------------------------------
MIRROR_RATIO = 4.0       # Itail = Iref * MIRROR_RATIO
L_TAIL = 0.5             # um; long for output resistance + matching

# --- DFE summing stage -------------------------------------------------------
L_GM = 0.18              # um; length of both Gm1 and GmF devices
L_FB = 0.15              # um; retained: minimum length for a current SWITCH
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


# Widest sky130 nfet model bin: wmin=7e-6, wmax=1.0e-4 -> a single device
# instance may not exceed 100 um of TOTAL width. Measured, because the bin
# table alone is misleading: most bins top out at 7.005 um, but one wide bin
# covers 7..100 um. Beyond that ngspice says "could not find a valid
# modelname" and refuses to run.
#
# `nf` does NOT help -- binning is on total W, not width per finger:
#   W=100 nf=10 (10 um/finger)  ok
#   W=120 nf=12 (10 um/finger)  FAIL
#   W=150 nf=30 ( 5 um/finger)  FAIL
#
# So a wide device must be built from PARALLEL UNIT devices with `m=`, which is
# verified to work on MOSFETs (m=4 of W=10 measures bit-identical to W=40).
# This matters most for the summing-stage tails: wg1_tail = Wtail*Ig1/Itail
# reaches ~16000 um at the corners of the parameter box.
W_MAX_UM = 100.0
W_MIN_UM = 0.42     # sky130 minimum drawn nfet width


def w_mult(w: float) -> tuple[float, int]:
    """Total width -> (width per unit device, number of parallel units).

    ``w_unit * m == w`` exactly, and ``w_unit <= W_MAX_UM``.
    """
    m = max(1, math.ceil(w / W_MAX_UM))
    return (w / m, m)


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
    # --- DFE summing stage ---
    # Rsum=400 matches the reference. Note 400 ohm is BELOW the ~450 ohm
    # contact-resistance floor, so it resolves to two units in parallel
    # (l=2.085, m=2) -- i.e. this default sits squarely in the code path where
    # `mult=` was silently inert. See the RES notes above.
    Rsum: float = 400.0
    Wg1: float = 40.0
    Ig1: float = 1.0e-3
    # 5%: measured optimum on this design, and the reference's best tap too.
    tap_pct: float = 5.0
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
        rsum_l, rsum_m = res_geometry(self.Rsum)
        cs_s = cap_side(self.Cs)
        return {
            "rl_l": rl_l, "rl_mult": rl_m, "rl_actual": res_value(rl_l, rl_m),
            "rs_l": rs_l, "rs_mult": rs_m, "rs_actual": res_value(rs_l, rs_m),
            "rsum_l": rsum_l, "rsum_mult": rsum_m,
            "rsum_actual": res_value(rsum_l, rsum_m),
            "cs_s": cs_s, "cs_actual": cap_value(cs_s),
            "iref": self.Itail / MIRROR_RATIO,
            "w_ref": self.Wtail / MIRROR_RATIO,
            # Both summing-stage tails share vbias with the CTLE tail, so each
            # one's current ratios purely by width against Itail.
            "wg1_tail": max(0.5, self.Wtail * self.Ig1 / self.Itail),
            "wgf_tail": max(0.5, self.Wtail * self.Igf / self.Itail),
            "w_gf": self.Wgf, "i_gf": self.Igf,
        }

    # ---- tap branch, derived from tap_pct (never sized independently) ----
    @property
    def Igf(self) -> float:
        """GmF tail current = tap_pct % of the main Gm1 current."""
        return self.Ig1 * self.tap_pct / 100.0

    @property
    def Wgf(self) -> float:
        """GmF width = tap_pct % of the main Gm1 width.

        Floored at the PDK's minimum drawn width. Below that floor the branch
        is no longer a scaled copy and gm stops tracking tap_pct linearly, so
        the floor is reported in geometry() rather than hidden.
        """
        return max(W_MIN_UM, self.Wg1 * self.tap_pct / 100.0)

    def core_netlist(self) -> str:
        """CTLE stage only: devices and topology, no supply/analysis/.lib.

        Outputs are outp_ctle / outn_ctle. The DFE tap is NOT here any more --
        it lives in its own summing stage (:meth:`summer_netlist`), fed from
        these nodes. Merging the tap onto the CTLE loads (the old topology)
        made tap weight a function of RL and let the tap capacitively load the
        equaliser's output node.
        """
        g = self.geometry()
        w_in, m_in = w_mult(self.W)
        nf_in = _nf(w_in)
        w_r, m_r = w_mult(g["w_ref"])
        nf_r = _nf(w_r)
        return f"""* --- STAGE 1: CTLE, source-degenerated diff pair, real SKY130 devices ---
* input pair
X1 outn_ctle vinp sp 0 {NFET} L={self.L:.4g} W={w_in:.4g} nf={nf_in} m={m_in}
X2 outp_ctle vinn sn 0 {NFET} L={self.L:.4g} W={w_in:.4g} nf={nf_in} m={m_in}
* degeneration: real poly resistor + real MIM cap  (b = body tie to substrate)
XRs sp sn 0 {RES} w={RES_W} l={g['rs_l']:.4g} m={g['rs_mult']}
XCs sp sn {CAP} w={g['cs_s']:.4g} l={g['cs_s']:.4g} mf=1
* loads
XRLp vdd outp_ctle 0 {RES} w={RES_W} l={g['rl_l']:.4g} m={g['rl_mult']}
XRLn vdd outn_ctle 0 {RES} w={RES_W} l={g['rl_l']:.4g} m={g['rl_mult']}
* CL stays ideal: it models the NEXT stage's input capacitance
CLp outp_ctle 0 {self.CL:.6g}
CLn outn_ctle 0 {self.CL:.6g}
* tail: {MIRROR_RATIO:g}:1 NMOS current mirror (Iref sourced from vdd).
* Xt1/Xt2 are the SAME unit device as Xref, replicated m={MIRROR_RATIO:g} times --
* a mirror ratios by multiplicity, not by making one device wider.
Iref vdd vbias DC {g['iref']:.6g}
Xref vbias vbias 0 0 {NFET} L={L_TAIL} W={w_r:.4g} nf={nf_r} m={m_r}
Xt1  sp vbias 0 0 {NFET} L={L_TAIL} W={w_r:.4g} nf={nf_r} m={m_r * MIRROR_RATIO:g}
Xt2  sn vbias 0 0 {NFET} L={L_TAIL} W={w_r:.4g} nf={nf_r} m={m_r * MIRROR_RATIO:g}
"""

    def summer_netlist(self) -> str:
        """STAGE 2: the DFE summing stage -- Gm1 + GmF into shared Rsum loads.

        Gm1 carries the equalised signal from the CTLE; GmF injects the previous
        decision. Both pairs drain into XRsp/XRsn, so their currents add there:
        that shared resistor pair IS the summing node, and the tap weight is the
        ratio gmF/gm1.

        DRAIN CROSSING, and why it is this way round: each device is common
        source, so it inverts its own drain. XG1p's gate is outp_ctle and its
        drain is outn_dfe, so outp_ctle rising pulls outn_dfe down and drives
        (outp_dfe - outn_dfe) POSITIVE -- the stage is non-inverting
        differentially. GmF is crossed identically, so vfbp high also drives the
        summing node positive. The tap must SUBTRACT the previous bit, so the
        INVERTED decision has to reach vfbp -- see dfe_decision_path(). This is
        the same trap as the old merged topology, where feeding dq (not dqb) to
        vfbp made the tap reinforce the post-cursor instead of cancelling it,
        silently. Do not trust this comment: it is measured by
        tests/test_spice.py::test_tap_opposes_the_main_path.
        """
        g = self.geometry()
        w_g1, m_g1 = w_mult(self.Wg1)
        w_gf, m_gf = w_mult(self.Wgf)
        w_t1, m_t1 = w_mult(g["wg1_tail"])
        w_tf, m_tf = w_mult(g["wgf_tail"])
        nf_g1, nf_gf = _nf(w_g1), _nf(w_gf)
        nf_t1, nf_tf = _nf(w_t1), _nf(w_tf)
        return f"""* --- STAGE 2: DFE summing stage (Gm1 + GmF share one load pair) ---
* summing-node loads -- these ARE the summer
XRsp vdd outp_dfe 0 {RES} w={RES_W} l={g['rsum_l']:.4g} m={g['rsum_mult']}
XRsn vdd outn_dfe 0 {RES} w={RES_W} l={g['rsum_l']:.4g} m={g['rsum_mult']}
* Gm1: main signal path, gates driven by the CTLE output (drains crossed)
XG1p outn_dfe outp_ctle sgm1 0 {NFET} L={L_GM} W={w_g1:.4g} nf={nf_g1} m={m_g1}
XG1n outp_dfe outn_ctle sgm1 0 {NFET} L={L_GM} W={w_g1:.4g} nf={nf_g1} m={m_g1}
* GmF: feedback tap, gates driven by the held decision (same crossing as Gm1)
XGFp outn_dfe vfbp sgmf 0 {NFET} L={L_GM} W={w_gf:.4g} nf={nf_gf} m={m_gf}
XGFn outp_dfe vfbn sgmf 0 {NFET} L={L_GM} W={w_gf:.4g} nf={nf_gf} m={m_gf}
* separate tails, both mirrored off the same vbias as the CTLE.
* Names must not collide with the CTLE's Xt1/Xt2 even case-insensitively --
* ngspice folds case, so `XT1` IS `Xt1` and the deck dies with
* "device already exists, bail out".
XTsum1 sgm1 vbias 0 0 {NFET} L={L_TAIL} W={w_t1:.4g} nf={nf_t1} m={m_t1}
XTsumf sgmf vbias 0 0 {NFET} L={L_TAIL} W={w_tf:.4g} nf={nf_tf} m={m_tf}
"""

    def full_netlist(self) -> str:
        """Both stages. This is what actually gets simulated."""
        return self.core_netlist() + self.summer_netlist()

    # ---- area -----------------------------------------------------------
    def area_um2(self) -> dict:
        """Rough active-device area budget in um^2 (no routing/spacing overhead)."""
        g = self.geometry()
        a_in = 2 * self.W * self.L
        a_tail = 2 * self.Wtail * L_TAIL + g["w_ref"] * L_TAIL
        a_sum = (2 * self.Wg1 * L_GM + 2 * self.Wgf * L_GM
                 + (g["wg1_tail"] + g["wgf_tail"]) * L_TAIL)
        a_res = RES_W * (2 * g["rl_l"] * g["rl_mult"]
                         + g["rs_l"] * g["rs_mult"]
                         + 2 * g["rsum_l"] * g["rsum_mult"])
        a_cap = g["cs_s"] ** 2
        total = a_in + a_tail + a_sum + a_res + a_cap
        return {"input_pair": a_in, "tail": a_tail, "summer": a_sum,
                "resistors": a_res, "cap": a_cap,
                "total_um2": total, "total_mm2": total * 1e-6}

    def tap_weight_ratio(self) -> float:
        """Nominal tap weight gmF/gm1 -- which IS tap_pct/100 by construction.

        Because the tap branch is a scaled copy of the main branch (same
        current density), gm scales linearly with the scale factor rather than
        as sqrt(W*I). The realised ratio is still measured from .op, and will
        differ slightly wherever Wgf hits the W_MIN_UM floor.
        """
        return self.tap_pct / 100.0

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
    """Closed-loop 1-tap DFE: clocked comparator -> 1 UI latch -> tap drive.

    Mirrors the reference architecture's two clocked elements:

        outp_dfe,outn_dfe -> adc_bridge -> d_dff (clocked comparator) -> D1
                                           d_dff (1 UI hold)          -> Q1/Q1_b

    Both flops are clocked on the SAME edge, so the second captures what the
    first held before that edge. Net feedback delay is therefore exactly one
    UI -- the bit whose post-cursor the tap must cancel.

    POLARITY: Q1_b (not Q1) drives vfbp. summer_netlist() crosses GmF the same
    way as Gm1, so vfbp high drives (outp_dfe - outn_dfe) positive. Feeding the
    previous decision straight through would therefore REINFORCE its
    post-cursor; the tap must subtract, so the inverted output drives vfbp.
    This exact mistake was made once already in the merged topology and was
    invisible in every metric (0.078 UI vs 0.453 UI once corrected), so it is
    asserted by tests/test_spice.py::test_tap_opposes_the_main_path rather
    than trusted.

    Slicer and latch are deliberately IDEAL XSPICE code models, as they are in
    the reference schematic ("Clocked Comparator (Ideal)", "D Flipflop
    (Ideal)"). No offset, no metastability, no kickback, and clk->Q of 20 ps
    against 50-150 ps for a real StrongARM -- so the eye is optimistic by an
    amount this framework does not quantify.
    """
    f_clk = 1.0 / ui_s
    return f"""* --- DFE decision path (IDEAL, XSPICE) ---
* sampled at the SUMMING NODE, not the CTLE output
Bdiff vdiff 0 V=V(outp_dfe)-V(outn_dfe)
Vclk clk 0 DC 0 PULSE(0 1.8 {0.5*ui_s:.6g} 10p 10p {0.4*ui_s:.6g} {ui_s:.6g})
adc_in  [vdiff] [ddata] adc_sl
adc_clk [clk]   [dclk]  adc_ck
* clocked comparator: decide on this edge
acomp ddata dclk NULL NULL d1 d1b dff_1tap
* 1 UI hold: captures what the comparator held BEFORE this edge
ahold d1 dclk NULL NULL q1 q1b dff_1tap
* inverted decision drives vfbp so the tap SUBTRACTS (see docstring)
adac_p [q1b] [vfbp] dac_fb
adac_n [q1]  [vfbn] dac_fb
.model adc_sl  adc_bridge(in_low=-0.001 in_high=0.001)
.model adc_ck  adc_bridge(in_low=0.7 in_high=1.1)
.model dac_fb  dac_bridge(out_low={VFB_LO:.4g} out_high={VFB_HI:.4g} out_undef={VFB_CM:.4g} t_rise=15p t_fall=15p)
.model dff_1tap d_dff(clk_delay=20p rise_delay=15p fall_delay=15p)
"""


# ---------------------------------------------------------------------------
# Lossy channel (PCIe Gen2 FR4-like), measured and tuned
# ---------------------------------------------------------------------------
# Without a channel the whole spec set is incoherent: there is no post-cursor,
# so the mandated 1-tap DFE has nothing to cancel and its injection is pure
# uncorrelated error (measured: the tap alone caused 6 of 20 PVT eye failures,
# and disabling it fixed 20/20). The peaking spec likewise has no loss to
# compensate. This model gives all three specs something real to act on.
#
# Structure: lumped LC ladder with a frequency-dependent series resistance --
# two staggered R||L stages approximating skin effect. A plain RLGC ladder is
# NOT enough: correctly sized it is a flat attenuator (measured 2.2 dB of tilt
# across 1-5 GHz), and ngspice's LTRA gives -0.16 dB at 2.5 GHz because its `r`
# is frequency-independent. Dispersion is what creates ISI; flat loss does not.
#
# Measured END TO END on the defaults below (chsrc -> vinp, 40 sections/leg,
# INCLUDING the 50 ohm source resistors and 100 ohm differential termination --
# i.e. what this function actually builds):
#     0.1 GHz   -6.20 dB
#     1.0 GHz  -10.33 dB
#     2.5 GHz  -16.77 dB   <- Nyquist
#     5.0 GHz  -22.07 dB
#   equalizable tilt 0.1 -> 2.5 GHz = 10.6 dB, inside the 3-12 dB boost spec.
#
# An earlier version of this comment read -0.93 / -4.95 / -11.10 / -16.56 dB.
# Those came from a PROTOTYPE with no source resistors, before this function
# existed; Rsrc plus Rterm is a 2:1 divider, so the built channel is ~5.5 dB
# lossier across the whole band. The TILT -- the only part the equaliser acts
# on -- was and is correct (10.6 vs 10.2 dB), so the design conclusions drawn
# from it stand. Only the flat offset was misdescribed.
CH_SECTIONS = 40
CH_TD_TOTAL = 1.2e-9      # s, ~7 inch of FR4
CH_Z0 = 50.0              # ohm per leg (100 ohm differential)
CH_RDC = 0.02             # ohm per section, ohmic (flat) loss
CH_SKIN = ((4.0, 0.45e-9), (12.0, 0.06e-9))   # (R, L) staggered R||L stages
CH_RSRC = 50.0            # source series resistance per leg
CH_RTERM = 100.0          # differential termination at the receiver

# Node names. The stimulus drives chsrc_*; the channel output IS vinp/vinn, so
# the equaliser itself is unchanged whether or not a channel is present.
CH_SRC_P, CH_SRC_N = "chsrc_p", "chsrc_n"


def channel_netlist(n_sections: int = CH_SECTIONS, td_total: float = CH_TD_TOTAL,
                    z0: float = CH_Z0, rdc: float = CH_RDC,
                    skin: tuple = CH_SKIN, rsrc: float = CH_RSRC,
                    rterm: float = CH_RTERM) -> str:
    """Differential lossy channel from chsrc_p/chsrc_n to vinp/vinn.

    DC common mode is preserved on purpose: the termination is DIFFERENTIAL
    (between vinp and vinn, not to ground) and the shunt capacitors are open at
    DC, so no DC current flows and the CTLE still sees Vcm at its gates. A
    termination to ground would drag the common mode down and unbias the input
    pair -- one of the three problems HANDOVER 10b flagged.
    """
    td = td_total / n_sections
    L, C = z0 * td, td / z0
    out = [f"* --- lossy channel: {n_sections} sections/leg, "
           f"{td_total*1e12:.0f} ps, skin-effect series R ---",
           f"Rsrc_p {CH_SRC_P} chp_in {rsrc:.6g}",
           f"Rsrc_n {CH_SRC_N} chn_in {rsrc:.6g}"]
    for leg, start, end in (("p", "chp_in", "vinp"), ("n", "chn_in", "vinn")):
        prev = start
        for i in range(n_sections):
            nxt = end if i == n_sections - 1 else f"ch{leg}{i}"
            a = f"{leg}{i}"
            node = f"{a}x"
            out.append(f"Rch{a}dc {prev} {node} {rdc:.6g}")
            for k, (Rk, Lk) in enumerate(skin):
                nx = f"{a}k{k}"
                out.append(f"Rch{a}s{k} {node} {nx} {Rk:.6g}")
                out.append(f"Lch{a}s{k} {node} {nx} {Lk:.6g}")
                node = nx
            out.append(f"Lch{a}m {node} {nxt} {L:.6g}")
            out.append(f"Cch{a} {nxt} 0 {C:.6g}")
            prev = nxt
    out.append(f"Rterm vinp vinn {rterm:.6g}")
    return "\n".join(out) + "\n"


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
