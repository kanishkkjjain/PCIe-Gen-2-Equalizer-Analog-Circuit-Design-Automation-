"""ngspice batch driver + result parsers.

We drive ngspice as a subprocess in batch mode (``ngspice -b deck.sp``) with a
``.control`` block that runs the requested analysis and dumps data with
``wrdata``/``print``.  Parsing the plain-text dumps with numpy is far more
robust across ngspice builds than the libngspice Python bindings, and it makes
PVT-corner sweeps trivially parallelisable.

The public entry point is :meth:`Simulator.evaluate`, which returns a
:class:`Metrics` snapshot (AC shape, power, distortion, noise, bias sanity) for
one parameter set at one PVT corner.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import netlist as nl
from . import pdk
from . import trimlib
from .netlist import CtleParams

# Where validated trimmed libraries live. Outside the repo, and persistent --
# if it is wiped, validated_lib() returns None and everything falls back to the
# full library, which is slow but correct.
# Measured on this design with the FULL library: ~322 MiB resident per ngspice
# process, almost all of it parsed model data. A trimmed library holds roughly
# a sixth of the model text, so the estimate drops with it.
NGSPICE_RSS_MIB_FULL = 350
NGSPICE_RSS_MIB_TRIMMED = 120


def available_mib() -> int:
    """MemAvailable in MiB, or a large number if it cannot be read."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 1 << 20


def safe_job_count(requested: int | None = None, trimmed: bool = False,
                   reserve_mib: int = 1024) -> int:
    """Parallel workers that fit in RAM as well as in cores.

    16 ngspice processes at ~322 MiB each is 5.0 GiB, which on a 7.6 GiB box
    leaves too little headroom -- measured, it OOM-killed unrelated background
    tasks during a run. An OOM-killed worker is a RESOURCE failure that the
    scorer would otherwise see as a bad design, so it is worth avoiding rather
    than merely detecting.

    ``reserve_mib`` is left for the OS, the parent process and anything else
    the user is running.
    """
    cores = os.cpu_count() or 4
    want = requested if requested else min(16, cores)
    per = NGSPICE_RSS_MIB_TRIMMED if trimmed else NGSPICE_RSS_MIB_FULL
    budget = max(0, available_mib() - reserve_mib)
    by_mem = max(1, budget // per)
    return max(1, min(want, cores, by_mem))


DEFAULT_TRIM_DIR = Path(
    os.environ.get("EQRL_TRIMLIB", Path.home() / ".cache" / "eqrl" / "trimlib"))


@dataclass
class Metrics:
    """Measured performance of one CTLE candidate at one corner."""

    ok: bool = False                 # simulation converged and parsed
    dc_gain_db: float = float("nan")  # differential gain at 1 MHz
    peak_gain_db: float = float("nan")
    peaking_db: float = float("nan")  # peak - dc
    f_peak: float = float("nan")      # Hz of peak gain
    gain_nyq_db: float = float("nan")  # gain at Nyquist (2.5 GHz)
    bw_3db: float = float("nan")      # -3dB freq relative to peak (Hz)
    power_w: float = float("nan")
    hd3_db: float = float("nan")      # 3rd-harmonic distortion (dBc), at the slicer
    hd3_ctle_db: float = float("nan")  # same tone after stage 1 only -- diagnostic
    vn_in_rms: float = float("nan")   # integrated input-referred noise (Vrms)
    # bias sanity
    id_m1: float = float("nan")
    gm_m1: float = float("nan")
    vds_m1: float = float("nan")
    vdsat_m1: float = float("nan")
    # real tail device -- with an ideal current sink these could not exist, and
    # their absence hid a 20% current error (tail sitting in triode at Vcm=0.9)
    id_tail: float = float("nan")
    vds_tail: float = float("nan")
    vdsat_tail: float = float("nan")
    tail_saturated: bool = False
    i_supply: float = float("nan")    # measured, not assumed
    area_mm2: float = float("nan")
    # closed-loop DFE eye (only populated when evaluate(do_eye=True))
    eye_height_v: float = float("nan")
    eye_width_ui: float = float("nan")
    # Gain-invariant companions to eye_height_v. Eye height alone can be bought
    # with gain; openness (opening / swing at the same node) cannot.
    eye_height_in_v: float = float("nan")      # at the equaliser input
    eye_height_ctle_v: float = float("nan")    # after stage 1
    eye_openness_in: float = float("nan")
    eye_openness_ctle: float = float("nan")
    eye_openness_out: float = float("nan")     # at the slicer
    eye_improvement: float = float("nan")      # openness_out / openness_in
    tap_ratio: float = float("nan")     # realised gmF/gm1, measured from .op
    gm_g1: float = float("nan")
    gm_gf: float = float("nan")
    summer_saturated: bool = False
    out_cm_v: float = float("nan")   # summing-node common mode (see op())
    # Was a channel in front? The "does it equalise" check is only meaningful
    # when there is ISI to remove: drive the equaliser from an ideal PWL source
    # and eye openness can only DROP, because the source is already perfect.
    # Recording it here keeps that condition with the measurement instead of
    # leaving the scorer to guess.
    channel_present: bool = False
    saturated: bool = False
    partial: bool = False            # some analyses failed; metrics are NaN
    note: str = ""

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class MeasCond:
    """The conditions every measurement is taken under.

    These lived as literals inside the deck builders, which made
    ``EqualizerSpec.nyquist`` decorative: the eye and the DFE latch ran at a
    hardcoded 200 ps UI no matter what the spec asked for, so a request for
    8 Gb/s came back as a confident 5 Gb/s answer with no error. Gathering them
    here gives one place to thread from the spec, and `from_spec` is the only
    mapping.

    Defaults are the brief's values, so a Simulator built without a spec (the
    tests, `trimlib.validate`, `plot_eye`) behaves exactly as before.

    Duck-typed on purpose: `specs.py` imports `Metrics` from this module, so
    importing `EqualizerSpec` here would be circular.
    """

    ui_s: float = 200e-12           # 1/(2*nyquist); 2.5 GHz -> 200 ps
    tx_swing_pp_v: float = 0.200    # differential pp at the channel input
    hd3_freq: float = 100e6
    hd3_amp_pp_v: float = 0.100     # differential pp at the equaliser input
    noise_f_lo: float = 10e6
    noise_f_hi: float = 5e9

    @property
    def nyquist(self) -> float:
        return 1.0 / (2.0 * self.ui_s)

    @classmethod
    def from_spec(cls, spec) -> "MeasCond":
        return cls(ui_s=spec.ui_s, tx_swing_pp_v=spec.tx_swing_pp_v,
                   hd3_freq=spec.hd3_freq, hd3_amp_pp_v=spec.hd3_amp_pp_v,
                   noise_f_lo=spec.noise_f_lo, noise_f_hi=spec.noise_f_hi)


class Simulator:
    def __init__(self, workdir: str | Path, ngspice_bin: str = "ngspice",
                 timeout: float = 300.0, use_trimmed: bool = True,
                 trim_dir: str | Path | None = None,
                 use_channel: bool = False,
                 meas: "MeasCond | None" = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.ngspice = shutil.which(ngspice_bin) or ngspice_bin
        # 300 s, not 60: a closed-loop eye is ~10 s standalone but ~2x that
        # under 16-way contention, and a TIMEOUT is indistinguishable from a
        # bad design to the scorer -- it would be silently ranked as a failed
        # candidate and steer the search away from a region for no reason.
        self.timeout = timeout
        self._lib = str(pdk.find_sky130_lib())
        # S3: the honest cost metric. One evaluate() launches ngspice 4-5x, so
        # counting "evaluations" understates SPICE cost by that factor.
        self.n_launches = 0
        self.last_error: str | None = None
        # Trimmed libraries are opt-OUT, but they are only ever used for a
        # section that has been PROVEN identical to the full library (see
        # trimlib.validate). An unvalidated or stale section silently and
        # correctly falls back to the full library -- slow, never wrong.
        self.trim_dir = Path(trim_dir) if trim_dir else DEFAULT_TRIM_DIR
        self.use_trimmed = use_trimmed
        self._lib_cache: dict[str, str] = {}
        self.trimmed_sections_used: set[str] = set()
        # A lossy channel in front of the equaliser. Off by default so every
        # number recorded before this existed stays reproducible.
        self.use_channel = use_channel
        # Measurement conditions, threaded from the spec by design.py and
        # env._eval_task. This used to be a bare `self.tx_swing_pp_v = 0.200`
        # attribute that NOTHING ever assigned from the spec, so setting
        # spec.tx_swing_pp_v gave metrics simulated at 200 mV, a report claiming
        # the requested value, and an exported deck built at the requested
        # value -- the D1 defect (artifact disagrees with report) through a
        # different door. A constructor argument cannot be forgotten the way a
        # post-construction assignment was.
        self.meas = meas or MeasCond()

    # ---- deck assembly -------------------------------------------------
    def lib_for(self, section: str) -> str:
        """Model library to use for one corner section.

        Returns the trimmed library only if it exists AND was validated against
        this exact PDK; otherwise the full one. Never raises -- falling back is
        always safe, so a missing or stale trim degrades speed, not accuracy.
        """
        if section in self._lib_cache:
            return self._lib_cache[section]
        chosen = self._lib
        if self.use_trimmed:
            try:
                t = trimlib.validated_lib(section, self.trim_dir)
                if t is not None:
                    chosen = str(t)
                    self.trimmed_sections_used.add(section)
            except Exception:
                chosen = self._lib          # any doubt -> full library
        self._lib_cache[section] = chosen
        return chosen

    def _header(self, corner: pdk.Corner) -> str:
        return (f"* auto-generated CTLE deck  corner={corner.name}\n"
                f'.lib "{self.lib_for(corner.lib_section)}" {corner.lib_section}\n'
                f".temp {corner.temp:.4g}\n"
                f".param Vcm={{Vcm}}\n")

    def _assemble(self, p: CtleParams, corner: pdk.Corner, analysis: str,
                  control: str, tran_amp: float = 0.1,
                  tran_freq: float = 100e6, dfe: str | None = None,
                  src_override: str | None = None,
                  ui: float = 200e-12, channel: bool | None = None) -> str:
        """Assemble a deck.

        ``dfe`` selects how the tap pair's gates are driven:
          None / "cm"  -> held at common mode (forward-path characterisation)
          "pos"/"neg"  -> fully steered one way (to measure the tap weight)
          "closed"     -> the XSPICE slicer->latch->tap feedback loop
        Without one of these, vfbp/vfbn float and every analysis fails.

        ``ui`` sets the DFE latch clock. It MUST come from the caller: it was
        previously left at dfe_decision_path's 200 ps default while the PRBS
        followed the requested UI, so any bit rate but 5 Gb/s silently sampled
        the eye at the wrong rate.
        """
        use_ch = self.use_channel if channel is None else channel
        src = (src_override if src_override is not None
               else nl.build_source_lines(analysis, tran_amp=tran_amp,
                                          tran_freq=tran_freq))
        ch_opts = ""
        if use_ch:
            # The ladder adds 80 inductors and ngspice's default abstol (1e-12 A)
            # is too tight for it: transients die with "timestep too small,
            # trouble with node ...#branch". Measured, abstol=1e-10 fixes it.
            # NOT switching to method=gear, which would damp the ladder's
            # ringing and understate the very ISI we are trying to measure.
            ch_opts = ".options abstol=1e-10 vntol=1e-6\n"
            # Re-target the stimulus at the channel input. The channel's output
            # IS vinp/vinn, so the equaliser netlist is byte-identical with or
            # without a channel -- only what drives its gates changes.
            src = (src.replace(" vinp ", f" {nl.CH_SRC_P} ")
                      .replace(" vinn ", f" {nl.CH_SRC_N} "))
            src += nl.channel_netlist()
        if dfe in (None, "cm"):
            src += nl.dfe_static_bias()
        elif dfe == "pos":
            src += nl.dfe_static_tap(+1.0)
        elif dfe == "neg":
            src += nl.dfe_static_tap(-1.0)
        elif dfe == "closed":
            src += nl.dfe_decision_path(ui_s=ui)
        else:
            raise ValueError(f"unknown dfe mode {dfe!r}")
        # substitute Vcm literal (kept as a param for readability)
        deck = [
            self._header(corner),
            ch_opts,
            f".param Vcm={p.Vcm:.4g}",
            f"Vdd vdd 0 DC {corner.vdd:.4g}",
            src.replace("{Vcm}", f"{p.Vcm:.4g}"),
            p.full_netlist(),
            control,
            ".end\n",
        ]
        return "\n".join(deck)

    # ngspice reports most fatal conditions on stdout and STILL EXITS 0, so a
    # returncode check alone is not enough. Every pattern here has been seen in
    # this project; the tag is what ends up in Metrics.note so a failure is
    # traceable to a cause instead of appearing as a bad design.
    FATAL_PATTERNS: tuple[tuple[str, str], ...] = (
        ("could not find a valid modelname", "model-bin-miss"),
        ("device already exists", "duplicate-instance"),
        ("unknown subckt", "missing-subckt"),
        ("could not find a valid subcircuit", "missing-subckt"),
        ("simulation interrupted", "sim-interrupted"),
        ("singular matrix", "singular-matrix"),
        ("iteration limit reached", "no-convergence"),
        ("too many iterations without convergence", "no-convergence"),
        ("timestep too small", "timestep-too-small"),
        ("mif-error", "xspice-error"),
        ("error on line", "deck-syntax"),
        ("fatal error", "fatal"),
        ("is not a valid instance name", "bad-instance-name"),
        ("no such vector", "missing-vector"),
        ("out of memory", "oom"),
    )
    # Benign notes that contain scary words; never treat these as failures.
    BENIGN = (
        "no compatibility mode selected",
        'no ".plot", ".print", or ".fourier" lines',
    )

    @classmethod
    def scan_log(cls, log: str) -> str | None:
        """Return a short tag for the first fatal condition in an ngspice log.

        ngspice frequently prints a fatal error and exits 0, so this is the only
        reliable failure signal. Callers must treat a non-None result as a hard
        failure -- silently returning NaN metrics makes a broken deck look like
        a bad design, which steers the optimiser for no reason.
        """
        low = log.lower()
        for benign in cls.BENIGN:
            low = low.replace(benign, "")
        for pat, tag in cls.FATAL_PATTERNS:
            if pat in low:
                return tag
        return None

    def _run(self, deck: str, tag: str) -> tuple[bool, str]:
        deck_path = self.workdir / f"{tag}.sp"
        deck_path.write_text(deck)
        self.n_launches += 1
        try:
            r = subprocess.run(
                [self.ngspice, "-b", str(deck_path)],
                cwd=self.workdir, capture_output=True, text=True,
                timeout=self.timeout,
            )
            log = r.stdout + "\n" + r.stderr
            err = self.scan_log(log)
            if err is None and r.returncode != 0:
                # Negative codes are signals. -9 is SIGKILL, which on this box
                # overwhelmingly means the OOM killer: 16 parallel ngspice
                # processes on 7.6 GiB is enough to trigger it. That is a
                # RESOURCE failure, not a bad design, and the distinction
                # matters -- scoring it as a design would teach the optimiser
                # to avoid a perfectly good region of the space.
                err = {-9: "killed-oom", -11: "segfault"}.get(
                    r.returncode, f"exit-{r.returncode}")
            if err is not None:
                self.last_error = err
                return False, f"FATAL[{err}] deck={tag}\n" + log
            return True, log
        except subprocess.TimeoutExpired:
            self.last_error = "timeout"
            return False, f"FATAL[timeout] deck={tag} after {self.timeout}s"
        except FileNotFoundError:
            self.last_error = "ngspice-not-found"
            return False, "FATAL[ngspice-not-found]"

    @staticmethod
    def _read_wrdata(path: Path) -> np.ndarray | None:
        if not path.exists():
            return None
        try:
            data = np.loadtxt(path)
        except Exception:
            return None
        if data.ndim == 1:
            data = data.reshape(1, -1)
        return data if data.size else None

    # ---- individual analyses ------------------------------------------
    def ac(self, p: CtleParams, corner: pdk.Corner, tag: str = "ac") -> dict:
        out = self.workdir / f"{tag}.dat"
        if out.exists():
            out.unlink()
        control = (
            ".control\n"
            "ac dec 40 1e6 20e9\n"
            # column 1 = CTLE stage (peaking/f_peak are specified there),
            # column 2 = the summing node, which is what the slicer sees
            f'wrdata {out.name} vdb(outp_ctle,outn_ctle) vdb(outp_dfe,outn_dfe) vdb(vinp,vinn)\n'
            ".endc\n"
        )
        ok, log = self._run(self._assemble(p, corner, "ac", control), tag)
        if not ok:
            return {"ok": False, "log": log, "note": _fatal_tag(log)}
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log, "note": "ac produced no data"}
        f, gdb = d[:, 0], d[:, 1]
        # wrdata repeats the x column per expression: cols are
        # f, ctle, f, summing-node, f, equaliser-input
        gdb_sum = d[:, 3] if d.shape[1] >= 4 else None
        # Reference both to the EQUALISER's input. With a channel in front, the
        # raw traces include its loss, and "peaking" would then describe the
        # channel+equaliser cascade -- but the brief's 3-12 dB boost is a
        # property of the equaliser. Without a channel v(vinp,vinn) is the 1 V
        # AC drive (0 dB) and this subtraction changes nothing.
        if d.shape[1] >= 6:
            gin = d[:, 5]
            gdb = gdb - gin
            if gdb_sum is not None:
                gdb_sum = gdb_sum - gin
        # dc gain ~ lowest frequency point
        dc = float(gdb[0])
        ipk = int(np.argmax(gdb))
        peak = float(gdb[ipk])
        fpk = float(f[ipk])
        g25 = float(np.interp(self.meas.nyquist, f, gdb))
        # 3dB bandwidth relative to peak (first crossing below peak-3 after peak)
        bw = float("nan")
        target = peak - 3.0
        for i in range(ipk, len(f)):
            if gdb[i] <= target:
                bw = float(f[i]); break
        out_d = {"ok": True, "dc_gain_db": dc, "peak_gain_db": peak,
                 "peaking_db": peak - dc, "f_peak": fpk, "gain_nyq_db": g25,
                 "bw_3db": bw}
        if gdb_sum is not None:
            out_d["sum_dc_gain_db"] = float(gdb_sum[0])
            out_d["sum_peak_gain_db"] = float(np.max(gdb_sum))
            out_d["sum_gain_nyq_db"] = float(np.interp(self.meas.nyquist, f, gdb_sum))
        return out_d

    def op(self, p: CtleParams, corner: pdk.Corner, tag: str = "op") -> dict:
        # Derived from netlist.NFET, never hardcoded: the sky130 subckt names its
        # internal MOS "m<subckt-name>", so the probe path must track the device
        # flavour. A mismatch here fails silently (no rows parsed -> ok=False ->
        # saturated=False -> reward capped at 0.2 for every candidate).
        dev = f"@m.x1.m{nl.NFET}"      # CTLE input pair
        tail = f"@m.xt1.m{nl.NFET}"    # real tail device -- headroom now matters
        # Summing stage: the tap weight is gmF/gm1, so both must be measured.
        gm1 = f"@m.xg1p.m{nl.NFET}"
        gmf = f"@m.xgfp.m{nl.NFET}"
        control = (
            ".control\n"
            "op\n"
            f"print {dev}[id] {dev}[gm] {dev}[vds] {dev}[vdsat] {dev}[vgs]\n"
            f"print {tail}[id] {tail}[vds] {tail}[vdsat]\n"
            f"print {gm1}[gm] {gm1}[id] {gm1}[vds] {gm1}[vdsat]\n"
            f"print {gmf}[gm] {gmf}[id] {gmf}[vds] {gmf}[vdsat]\n"
            "print v(outp_dfe) v(outn_dfe)\n"
            "print i(Vdd)\n"          # true total supply current
            ".endc\n"
        )
        ok, log = self._run(self._assemble(p, corner, "op", control), tag)
        if not ok:
            return {"ok": False, "log": log, "note": _fatal_tag(log)}
        v = _parse_prints(log)
        need = [f"{dev}[{k}]" for k in ("id", "gm", "vds", "vdsat", "vgs")]
        # The TAIL probe is required too. Leaving it optional reproduced exactly
        # the failure this file already fixed for the input pair: a stale probe
        # path yields ok=True with saturated=False, specs.score() then caps
        # every candidate at reward 0.2, and the search goes flat with no error.
        need += [f"{tail}[{k}]" for k in ("id", "vds", "vdsat")]
        if not all(k in v for k in need):
            missing = [k for k in need if k not in v]
            return {"ok": False, "log": log,
                    "note": f"op probe missing {missing}"}
        idv, gm, vds, vdsat, vgs = (v[f"{dev}[{k}]"] for k in
                                    ("id", "gm", "vds", "vdsat", "vgs"))
        t_id = v[f"{tail}[id]"]
        t_vds = v[f"{tail}[vds]"]
        t_vdsat = v[f"{tail}[vdsat]"]
        # ngspice reports source current flowing INTO the + terminal, so the
        # supply draw is the magnitude.
        isup = abs(v.get("i(vdd)", float("nan")))
        tail_sat = (t_vds == t_vds) and (t_vds > t_vdsat + 0.02)
        g1 = v.get(f"{gm1}[gm]", float("nan"))
        gf = v.get(f"{gmf}[gm]", float("nan"))
        # REALISED tap weight. CtleParams.tap_weight_ratio() is a square-law
        # estimate; these devices are nowhere near square law, so this measured
        # value is the one to quote.
        tap_ratio = (gf / g1) if (g1 == g1 and g1 > 0) else float("nan")

        def _sat(pre):
            vds = v.get(f"{pre}[vds]", float("nan"))
            vdsat = v.get(f"{pre}[vdsat]", float("nan"))
            return (vds == vds) and (vds > vdsat + 0.02)
        summer_sat = _sat(gm1) and _sat(gmf)
        # Output common mode at the summing node. analytic.py starts the search
        # from the reference's rule (0.5*Ig1*Rsum = VDD/2) but Rsum and Ig1 are
        # both free variables, so the optimiser can and does move away from it.
        # Measured rather than assumed, and reported so the drift is visible.
        cm = 0.5 * (v.get("v(outp_dfe)", float("nan"))
                    + v.get("v(outn_dfe)", float("nan")))
        return {"ok": True, "out_cm_v": cm, "id_m1": idv, "gm_m1": gm, "vds_m1": vds,
                "vdsat_m1": vdsat, "vgs_m1": vgs,
                "id_tail": t_id, "vds_tail": t_vds, "vdsat_tail": t_vdsat,
                "tail_saturated": tail_sat, "i_supply": isup,
                "gm_g1": g1, "gm_gf": gf, "tap_ratio": tap_ratio,
                "summer_saturated": summer_sat,
                # every stage must bias correctly, not just the CTLE
                "saturated": ((vds > vdsat + 0.02) and (idv > 1e-9)
                              and tail_sat and summer_sat)}

    def tap_weight(self, p: CtleParams, corner: pdk.Corner) -> dict:
        """Measure the realised tap injection at the SUMMING node.

        Steer the GmF pair fully each way and take the difference at
        outp_dfe/outn_dfe. Expect |dV| ~ Igf * Rsum * 2 when fully switched;
        much less means GmF is not fully steering on the 0.8/1.4 V drive.

        Also returns the injection as a FRACTION of the main path's full swing
        (Ig1 * Rsum), which is the number that actually matters -- a 1-tap DFE
        must cancel a post-cursor that is some fraction of the cursor.
        """
        vals = {}
        for sign, tag in ((+1.0, "pos"), (-1.0, "neg")):
            control = ".control\nop\nprint v(outp_dfe) v(outn_dfe)\n.endc\n"
            mode = "pos" if sign > 0 else "neg"
            ok, log = self._run(
                self._assemble(p, corner, "op", control, dfe=mode),
                f"tap_{tag}")
            v = _parse_prints(log)
            if "v(outp_dfe)" not in v or "v(outn_dfe)" not in v:
                return {"ok": False, "log": log}
            vals[tag] = v["v(outp_dfe)"] - v["v(outn_dfe)"]
        dv = abs(vals["pos"] - vals["neg"])
        ideal = 2.0 * p.Igf * p.Rsum
        main = p.Ig1 * p.Rsum
        return {"ok": True, "tap_dv": dv, "tap_ideal": ideal,
                "tap_efficiency": (dv / ideal) if ideal > 0 else float("nan"),
                "tap_frac_of_main": (0.5 * dv / main) if main > 0 else float("nan"),
                "v_pos": vals["pos"], "v_neg": vals["neg"]}

    def tran_hd3(self, p: CtleParams, corner: pdk.Corner,
                 amp: float | None = None, freq: float | None = None,
                 tag: str = "tran", channel: bool | None = False) -> dict:
        """3rd-harmonic distortion, measured AT THE SLICER.

        channel=False by default, for the same reason noise() is: the tone was
        being injected AHEAD of the channel, which eats 6.2 dB at 100 MHz, so
        the circuit was driven at ~49% of the intended amplitude and reported a
        flattering number -- measured -76.0 dBc through the channel against
        -63.4 dBc at full drive. Linearity is a property of the equaliser, so
        it is measured on the equaliser.

        MEASURED AT `outp_dfe`, NOT `outp_ctle`. This is the same defect the
        noise measurement had: the equaliser is two stages and stopping at
        stage 1 leaves the summing stage out of a number that claims to
        describe the whole thing. It bites harder here than it did for noise,
        because `summer_netlist()`'s Gm1 is an UNDEGENERATED pair -- the CTLE
        has Rs||Cs linearising it and the summer has nothing -- so the omitted
        stage is the more distorting one, and it is the node the slicer
        actually decides on.

        `hd3_ctle_db` is still returned, as a diagnostic: the stage-by-stage
        split is what makes the number explainable, the same way
        eye_openness_ctle sits beside eye_openness_out.
        """
        amp = self.meas.hd3_amp_pp_v if amp is None else amp
        freq = self.meas.hd3_freq if freq is None else freq
        out = self.workdir / f"{tag}.dat"
        if out.exists():
            out.unlink()
        ncyc = 20
        tstop = ncyc / freq
        tstep = 1.0 / (freq * 512)   # 512 pts/cycle
        control = (
            ".control\n"
            f"tran {tstep:.6g} {tstop:.6g} 0 {tstep:.6g}\n"
            f'wrdata {out.name} v(outp_dfe,outn_dfe) v(outp_ctle,outn_ctle)\n'
            ".endc\n"
        )
        ok, log = self._run(
            self._assemble(p, corner, "tran", control, tran_amp=amp,
                           tran_freq=freq, channel=channel), tag)
        if not ok:
            return {"ok": False, "log": log, "note": _fatal_tag(log)}
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log, "note": "hd3 produced no data"}
        t, v = d[:, 0], d[:, 1]
        res = {"ok": True, "hd3_db": _hd3_from_wave(t, v, freq)}
        # wrdata repeats the x column per expression: cols are t, dfe, t, ctle
        if d.shape[1] >= 4:
            res["hd3_ctle_db"] = _hd3_from_wave(d[:, 2], d[:, 3], freq)
        return res

    def tran_eye(self, p: CtleParams, corner: pdk.Corner, n_bits: int = 200,
                 ui: float = 200e-12, amp: float = 0.10,   # DIFFERENTIAL amplitude: pp = 2*amp
                 tstep: float | None = None, tag: str = "eye") -> dict:
        """Closed-loop PRBS transient through the full equalizer + DFE loop.

        This is the expensive path (a few hundred UI at ps resolution), used to
        VERIFY a design and to validate the cheap pulse-response model -- not
        once per RL candidate.
        """
        from . import dfe as dfe_mod
        out = self.workdir / f"{tag}.dat"
        if out.exists():
            out.unlink()
        bits = dfe_mod.prbs(n_bits)
        src = (f"Vinp vinp 0 {dfe_mod.nrz_pwl(bits, ui, amp/2, p.Vcm)}\n"
               f"Vinn vinn 0 {dfe_mod.nrz_pwl(bits, ui, amp/2, p.Vcm, invert=True)}\n")
        tstop = n_bits * ui
        ts = tstep if tstep is not None else ui / 50.0
        control = (
            ".control\n"
            f"tran {ts:.6g} {tstop:.6g} 0 {ts:.6g}\n"
            # also capture the equaliser INPUT and the CTLE output, so a
            # gain-only solution can be told apart from real equalisation
            f"wrdata {out.name} v(outp_dfe,outn_dfe) v(vfbp) "
            f"v(outp_ctle,outn_ctle) v(vinp,vinn)\n"
            ".endc\n"
        )
        ok, log = self._run(
            self._assemble(p, corner, "tran", control, dfe="closed",
                           src_override=src, ui=ui), tag)
        if not ok:
            return {"ok": False, "log": log, "note": _fatal_tag(log)}
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log, "note": "eye produced no data"}
        t, v = d[:, 0], d[:, 1]
        vfb = d[:, 3] if d.shape[1] >= 4 else None
        eye = dfe_mod.eye_from_waveform(t, v, ui)
        res = {"ok": True, "eye": eye, "t": t, "v": v, "vfb": vfb,
               "n_bits": n_bits}

        # --- is the eye open because we EQUALISED, or because we AMPLIFIED? ---
        # Eye height alone cannot tell: the summing stage's gain (Rsum, Wg1,
        # Ig1) is a free optimiser variable, and measured across 20 corners the
        # eye height correlates with that gain at 0.98 and with peaking at only
        # 0.47. "Openness" -- the eye opening as a FRACTION of the swing at the
        # same node -- is gain-invariant: scaling a waveform leaves it
        # unchanged. So openness_out > openness_in means real equalisation.
        skip = t >= t[0] + 20 * ui

        def _openness(col):
            if d.shape[1] <= col:
                return float("nan"), float("nan")
            w = d[skip, col]
            e = dfe_mod.eye_from_waveform(t[skip], w, ui, skip_ui=0)
            pp = float(w.max() - w.min())
            return e.height_v, (e.height_v / pp if pp > 0 else float("nan"))

        h_ctle, o_ctle = _openness(5)
        h_in, o_in = _openness(7)
        pp_out = float(v[skip].max() - v[skip].min())
        o_out = eye.height_v / pp_out if pp_out > 0 else float("nan")
        res.update(eye_height_ctle_v=h_ctle, eye_height_in_v=h_in,
                   eye_openness_in=o_in, eye_openness_ctle=o_ctle,
                   eye_openness_out=o_out,
                   eye_improvement=(o_out / o_in) if o_in and o_in == o_in
                   and o_in > 0 else float("nan"))
        return res

    def noise(self, p: CtleParams, corner: pdk.Corner, tag: str = "noise",
              channel: bool | None = False) -> dict:
        """Integrated input-referred noise.

        Measured at the SUMMING NODE (outp_dfe/outn_dfe), not the CTLE output.
        The equaliser is two stages and the brief asks for the noise of the
        equaliser; stopping at stage 1 left the summing stage's contribution
        uncounted. That is not a small omission -- the CTLE runs at BELOW-unity
        gain (-4.59 dB at the final design), so noise added after it refers
        back to the input AMPLIFIED, not attenuated.

        ngspice's own `inoise_total` is unreliable here: at 5 of 9 PVT corners it
        returns `-nan` even though the SPECTRUM it computed is perfectly clean
        (all 54 points finite and positive). Its integration routine is the
        broken part, not the noise analysis. So we dump the spectrum and
        integrate it in numpy: rms = sqrt(integral of S(f)^2 df).
        `inoise_total` is still parsed and reported as a cross-check when finite.
        """
        out = self.workdir / f"{tag}_spec.dat"
        if out.exists():
            out.unlink()
        control = (
            ".control\n"
            f"noise v(outp_dfe,outn_dfe) Vinp dec 20 {self.meas.noise_f_lo:.6g} "
            f"{self.meas.noise_f_hi:.6g}\n"
            "setplot noise1\n"
            f"wrdata {out.name} inoise_spectrum\n"
            "setplot noise2\n"
            "print inoise_total\n"
            ".endc\n"
        )
        # channel=False by default ON PURPOSE. ngspice refers noise back to a
        # SOURCE, so with a channel in front the result is referred to the
        # transmitter and the channel's loss inflates it -- a design that meets
        # the spec fails it spuriously. The brief's "input-referred noise" is a
        # property of the EQUALISER, whose input is the channel's output, so
        # the equaliser is measured on its own. Same reasoning as referencing
        # peaking to v(vinp,vinn).
        ok, log = self._run(self._assemble(p, corner, "noise", control,
                                           channel=channel), tag)
        if not ok:
            return {"ok": False, "log": log, "note": _fatal_tag(log)}
        d = self._read_wrdata(out)
        if d is not None and d.shape[1] >= 2:
            f, sd = d[:, 0], d[:, 1]
            good = np.isfinite(f) & np.isfinite(sd) & (sd >= 0)
            if good.sum() >= 2:
                f, sd = f[good], sd[good]
                rms = float(np.sqrt(np.trapezoid(sd ** 2, f)))
                ng = _parse_scalar(log, "inoise_total")
                return {"ok": True, "vn_in_rms": rms,
                        "ngspice_inoise_total": ng,
                        "n_spectrum_pts": int(good.sum())}
        val = _parse_scalar(log, "inoise_total")
        # NaN must be caught explicitly: `val < 0` is False for NaN, so a bare
        # sign check lets ngspice's "-nan" through as a successful result and
        # silently poisons the metric. Degenerate sizings (e.g. a 20um feedback
        # pair carrying 10uA -> deep subthreshold) really do make the BSIM noise
        # integration return -nan, and the RL agent will propose such points.
        if val is None or val != val or val < 0:
            return {"ok": False, "log": log,
                    "note": f"noise analysis returned {val!r}"}
        # ngspice reports inoise_total as the INTEGRATED RMS input-referred
        # noise in volts already -- do NOT sqrt it. (Verified: raw 2.915e-4 for
        # the default CTLE = 0.29 mVrms, matching the hand calc sqrt(4kT*gamma/gm)
        # * sqrt(BW) ~ 0.2-0.5 mVrms; sqrt()ing it gave a bogus 17 mVrms.)
        # CAVEAT: still referred to Vinp alone, which carries only half the
        # differential drive (AC 0.5), so this may be off by a factor ~2 for a
        # true differential input-referred figure. Worth confirming against a
        # hand-computed onoise/|Adiff| before quoting it as final.
        return {"ok": True, "vn_in_rms": val}

    def _evaluate_combined(self, p: CtleParams, corner: pdk.Corner,
                           do_eye: bool = False) -> Metrics:
        m = Metrics()
        failed: list[str] = []
        # The combined deck runs op/ac/tran/noise CHANNEL-FREE. None of them
        # needs it:
        #   op    - no DC current flows into MOSFET gates, so the bias is
        #           bit-identical with and without the ladder (measured, 0.00e+00)
        #   ac    - referenced to v(vinp,vinn), so channel-invariant by design
        #   hd3   - deliberately measured at full drive (bug 1b)
        #   noise - deliberately referred to the equaliser input (bug 17)
        # Only the eye needs the channel. Running the combined deck with it and
        # then re-running hd3 and noise without it cost FOUR launches per
        # evaluation instead of two, and the ladder's 80 inductors make every
        # one of those transients genuinely slower to solve.
        r = self._combined(p, corner, channel=False)
        if not r.get("launch_ok"):
            # The whole deck died. This is NOT a bad design -- record the cause
            # so it cannot be mistaken for one by the scorer or a reader.
            m.note = f"combined-deck-failed[{r.get('fatal', '?')}]"
            return m

        acr = r["ac"]
        if not acr.get("ok"):
            m.note = f"ac-failed[{acr.get('note', '?')}]"
            return m
        for k in ("dc_gain_db", "peak_gain_db", "peaking_db", "f_peak",
                  "gain_nyq_db", "bw_3db"):
            setattr(m, k, acr[k])

        opr = r["op"]
        if opr.get("ok"):
            for k in ("id_m1", "gm_m1", "vds_m1", "vdsat_m1", "id_tail",
                      "vds_tail", "vdsat_tail", "tail_saturated", "i_supply",
                      "saturated", "gm_g1", "gm_gf", "summer_saturated",
                      "out_cm_v"):
                setattr(m, k, opr.get(k, float("nan")))
            m.tap_ratio = opr["tap_ratio"]
        else:
            failed.append(f"op[{opr.get('note', '?')}]")

        if m.i_supply == m.i_supply:
            m.power_w = corner.vdd * m.i_supply
        else:
            m.power_w = corner.vdd * ((2.0 + 1.0 / nl.MIRROR_RATIO) * p.Itail
                                      + p.Ig1 + p.Igf)
        m.area_mm2 = p.area_um2()["total_mm2"]
        m.channel_present = bool(self.use_channel)

        hd = r["hd3"]          # already channel-free: the deck was built that way
        if hd.get("ok"):
            m.hd3_db = hd["hd3_db"]
            m.hd3_ctle_db = hd.get("hd3_ctle_db", float("nan"))
        else:
            failed.append(f"hd3[{hd.get('note', '?')}]")
        nz = r["noise"]        # likewise channel-free
        if nz.get("ok"):
            m.vn_in_rms = nz["vn_in_rms"]
        else:
            failed.append(f"noise[{nz.get('note', '?')}]")

        if do_eye:
            # tran_eye's `amp` is the DIFFERENTIAL AMPLITUDE, so the
            # peak-to-peak swing is 2*amp. Passing tx_swing_pp_v
            # straight in doubled the drive to 400 mV pp while the
            # exported deck still used 200 -- caught by the export test.
            ey = self.tran_eye(p, corner, amp=self.meas.tx_swing_pp_v / 2,
                               ui=self.meas.ui_s)
            if ey.get("ok"):
                m.eye_height_v = ey["eye"].height_v
                m.eye_width_ui = ey["eye"].width_ui
                for k in ("eye_height_in_v", "eye_height_ctle_v",
                          "eye_openness_in", "eye_openness_ctle",
                          "eye_openness_out", "eye_improvement"):
                    setattr(m, k, ey.get(k, float("nan")))
            else:
                failed.append(f"eye[{ey.get('note', '?')}]")
        return _finish(m, failed)

    # ---- combined deck: one launch instead of four ---------------------
    def _combined(self, p: CtleParams, corner: pdk.Corner,
                  hd3_amp: float | None = None, hd3_freq: float | None = None,
                  tag: str = "combo", channel: bool | None = None) -> dict:
        """Run op + ac + tran(HD3) + noise from ONE deck, one ngspice launch.

        These four analyses differ only in the ANALYSIS card -- they share the
        same netlist and the same DFE bias (tap held at common mode). Running
        them separately re-parses 1.24 MB of models four times over for nothing:
        measured, the parse is ~99.9% of each launch.

        One source serves all of them: a SPICE source may carry DC, AC and a
        transient function simultaneously, and each analysis picks the part it
        needs.

        Every analysis is checked INDIVIDUALLY afterwards. A combined deck has
        one genuine hazard -- an early analysis failing takes the rest with it
        -- so "the file exists and parsed" is verified per analysis rather than
        inferred from the launch succeeding.
        """
        hd3_amp = self.meas.hd3_amp_pp_v if hd3_amp is None else hd3_amp
        hd3_freq = self.meas.hd3_freq if hd3_freq is None else hd3_freq
        dev = f"@m.x1.m{nl.NFET}"
        tail = f"@m.xt1.m{nl.NFET}"
        gm1 = f"@m.xg1p.m{nl.NFET}"
        gmf = f"@m.xgfp.m{nl.NFET}"
        f_ac = self.workdir / f"{tag}_ac.dat"
        f_tr = self.workdir / f"{tag}_tran.dat"
        f_nz = self.workdir / f"{tag}_noise.dat"
        for f in (f_ac, f_tr, f_nz):
            if f.exists():
                f.unlink()

        ncyc, tstop = 20, 20 / hd3_freq
        tstep = 1.0 / (hd3_freq * 512)
        src = (f"Vinp vinp 0 DC {{Vcm}} AC 0.5 0 "
               f"SIN({{Vcm}} {hd3_amp/2:.6g} {hd3_freq:.6g})\n"
               f"Vinn vinn 0 DC {{Vcm}} AC 0.5 180 "
               f"SIN({{Vcm}} {-hd3_amp/2:.6g} {hd3_freq:.6g})\n")
        control = (
            ".control\n"
            "op\n"
            f"print {dev}[id] {dev}[gm] {dev}[vds] {dev}[vdsat] {dev}[vgs]\n"
            f"print {tail}[id] {tail}[vds] {tail}[vdsat]\n"
            f"print {gm1}[gm] {gm1}[id] {gm1}[vds] {gm1}[vdsat]\n"
            f"print {gmf}[gm] {gmf}[id] {gmf}[vds] {gmf}[vdsat]\n"
            "print v(outp_dfe) v(outn_dfe)\n"
            "print i(Vdd)\n"
            "ac dec 40 1e6 20e9\n"
            f"wrdata {f_ac.name} vdb(outp_ctle,outn_ctle) vdb(outp_dfe,outn_dfe) vdb(vinp,vinn)\n"
            f"tran {tstep:.6g} {tstop:.6g} 0 {tstep:.6g}\n"
            f"wrdata {f_tr.name} v(outp_dfe,outn_dfe) v(outp_ctle,outn_ctle)\n"
            # noise LAST: setplot switches the active plot and would strand the
            # wrdata calls above.
            f"noise v(outp_dfe,outn_dfe) Vinp dec 20 {self.meas.noise_f_lo:.6g} "
            f"{self.meas.noise_f_hi:.6g}\n"
            "setplot noise1\n"
            f"wrdata {f_nz.name} inoise_spectrum\n"
            "setplot noise2\n"
            "print inoise_total\n"
            ".endc\n"
        )
        ok, log = self._run(
            self._assemble(p, corner, "ac", control, src_override=src,
                           channel=channel), tag)
        res: dict = {"launch_ok": ok, "log": log}
        if not ok:
            res["fatal"] = _fatal_tag(log)
            return res

        # --- op ---
        v = _parse_prints(log)
        need = [f"{dev}[{k}]" for k in ("id", "gm", "vds", "vdsat", "vgs")]
        need += [f"{tail}[{k}]" for k in ("id", "vds", "vdsat")]
        missing = [k for k in need if k not in v]
        res["op"] = ({"ok": False, "note": f"op probe missing {missing}"}
                     if missing else self._op_from_prints(v, dev, tail, gm1, gmf))
        # --- ac ---
        res["ac"] = self._ac_from_file(f_ac)
        # --- hd3 ---
        d = self._read_wrdata(f_tr)
        if d is None or d.shape[1] < 2:
            res["hd3"] = {"ok": False, "note": "hd3 produced no data"}
        else:
            res["hd3"] = {"ok": True,
                          "hd3_db": _hd3_from_wave(d[:, 0], d[:, 1], hd3_freq)}
            if d.shape[1] >= 4:     # t, dfe, t, ctle
                res["hd3"]["hd3_ctle_db"] = _hd3_from_wave(d[:, 2], d[:, 3],
                                                           hd3_freq)
        # --- noise ---
        res["noise"] = self._noise_from_file(f_nz, log)
        return res

    def _op_from_prints(self, v, dev, tail, gm1, gmf) -> dict:
        idv, gm, vds, vdsat, vgs = (v[f"{dev}[{k}]"] for k in
                                    ("id", "gm", "vds", "vdsat", "vgs"))
        t_id, t_vds, t_vdsat = (v[f"{tail}[{k}]"] for k in ("id", "vds", "vdsat"))
        isup = abs(v.get("i(vdd)", float("nan")))
        tail_sat = (t_vds == t_vds) and (t_vds > t_vdsat + 0.02)
        g1 = v.get(f"{gm1}[gm]", float("nan"))
        gf = v.get(f"{gmf}[gm]", float("nan"))
        tap_ratio = (gf / g1) if (g1 == g1 and g1 > 0) else float("nan")

        def _sat(pre):
            a = v.get(f"{pre}[vds]", float("nan"))
            b = v.get(f"{pre}[vdsat]", float("nan"))
            return (a == a) and (a > b + 0.02)
        summer_sat = _sat(gm1) and _sat(gmf)
        # Output common mode at the summing node. analytic.py starts the search
        # from the reference's rule (0.5*Ig1*Rsum = VDD/2) but Rsum and Ig1 are
        # both free variables, so the optimiser can and does move away from it.
        # Measured rather than assumed, and reported so the drift is visible.
        cm = 0.5 * (v.get("v(outp_dfe)", float("nan"))
                    + v.get("v(outn_dfe)", float("nan")))
        return {"ok": True, "out_cm_v": cm, "id_m1": idv, "gm_m1": gm, "vds_m1": vds,
                "vdsat_m1": vdsat, "vgs_m1": vgs, "id_tail": t_id,
                "vds_tail": t_vds, "vdsat_tail": t_vdsat,
                "tail_saturated": tail_sat, "i_supply": isup,
                "gm_g1": g1, "gm_gf": gf, "tap_ratio": tap_ratio,
                "summer_saturated": summer_sat,
                "saturated": ((vds > vdsat + 0.02) and (idv > 1e-9)
                              and tail_sat and summer_sat)}

    def _ac_from_file(self, path: Path) -> dict:
        d = self._read_wrdata(path)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "note": "ac produced no data"}
        f, gdb = d[:, 0], d[:, 1]
        gdb_sum = d[:, 3] if d.shape[1] >= 4 else None
        # Same referencing as ac(): with a channel in front the raw traces
        # include its loss, and "peaking" would describe the cascade rather
        # than the equaliser. This helper is the one the COMBINED deck uses,
        # and it was missed when ac() was fixed -- so evaluate() silently
        # reported cascade peaking while ac() reported equaliser peaking.
        if d.shape[1] >= 6:
            gin = d[:, 5]
            gdb = gdb - gin
            if gdb_sum is not None:
                gdb_sum = gdb_sum - gin
        dc = float(gdb[0])
        ipk = int(np.argmax(gdb))
        peak, fpk = float(gdb[ipk]), float(f[ipk])
        bw = float("nan")
        for i in range(ipk, len(f)):
            if gdb[i] <= peak - 3.0:
                bw = float(f[i]); break
        out = {"ok": True, "dc_gain_db": dc, "peak_gain_db": peak,
               "peaking_db": peak - dc, "f_peak": fpk,
               "gain_nyq_db": float(np.interp(self.meas.nyquist, f, gdb)),
               "bw_3db": bw}
        if gdb_sum is not None:
            out["sum_dc_gain_db"] = float(gdb_sum[0])
            out["sum_peak_gain_db"] = float(np.max(gdb_sum))
            out["sum_gain_nyq_db"] = float(np.interp(self.meas.nyquist, f, gdb_sum))
        return out

    def _noise_from_file(self, path: Path, log: str) -> dict:
        d = self._read_wrdata(path)
        if d is not None and d.shape[1] >= 2:
            f, sd = d[:, 0], d[:, 1]
            good = np.isfinite(f) & np.isfinite(sd) & (sd >= 0)
            if good.sum() >= 2:
                rms = float(np.sqrt(np.trapezoid(sd[good] ** 2, f[good])))
                return {"ok": True, "vn_in_rms": rms,
                        "ngspice_inoise_total": _parse_scalar(log, "inoise_total"),
                        "n_spectrum_pts": int(good.sum())}
        return {"ok": False, "note": "noise produced no usable spectrum"}

    # ---- top-level -----------------------------------------------------
    def evaluate(self, p: CtleParams, corner: pdk.Corner,
                 do_hd3: bool = True, do_noise: bool = True,
                 do_eye: bool = False, combined: bool = True) -> Metrics:
        """Measure one design at one corner.

        ``combined`` runs op/ac/hd3/noise from a single deck (one ngspice
        launch instead of four). Set it False to take the slow path -- the
        per-analysis methods are kept because they are what the tests pin, and
        because the combined deck is worth being able to bisect against.
        """
        m = Metrics()
        failed: list[str] = []
        if combined and do_hd3 and do_noise:
            return self._evaluate_combined(p, corner, do_eye=do_eye)
        acr = self.ac(p, corner)
        if not acr.get("ok"):
            m.note = f"ac-failed[{acr.get('note', '?')}]"
            return m
        for k in ("dc_gain_db", "peak_gain_db", "peaking_db", "f_peak",
                  "gain_nyq_db", "bw_3db"):
            setattr(m, k, acr[k])
        opr = self.op(p, corner)
        if not opr.get("ok"):
            failed.append(f"op[{opr.get('note', '?')}]")
        if opr.get("ok"):
            m.id_m1 = opr["id_m1"]; m.gm_m1 = opr["gm_m1"]
            m.vds_m1 = opr["vds_m1"]; m.vdsat_m1 = opr["vdsat_m1"]
            m.id_tail = opr["id_tail"]; m.vds_tail = opr["vds_tail"]
            m.vdsat_tail = opr["vdsat_tail"]
            m.tail_saturated = opr["tail_saturated"]
            m.i_supply = opr["i_supply"]
            m.saturated = opr["saturated"]
            m.gm_g1 = opr["gm_g1"]; m.gm_gf = opr["gm_gf"]
            m.summer_saturated = opr["summer_saturated"]
            m.out_cm_v = opr.get("out_cm_v", float("nan"))
        # Power from the MEASURED supply current. The old analytic
        # `vdd * 2 * Itail` was wrong twice over: it ignored the mirror
        # reference branch, and the mirror does not actually deliver Itail
        # (Vds mismatch against the diode-connected reference costs ~8%).
        if m.i_supply == m.i_supply:
            m.power_w = corner.vdd * m.i_supply
        else:
            # The summing stage draws Ig1 + Igf off the same supply; omitting
            # them understated the fallback badly now that it is a real stage.
            m.power_w = corner.vdd * ((2.0 + 1.0 / nl.MIRROR_RATIO) * p.Itail
                                      + p.Ig1 + p.Igf)
        m.area_mm2 = p.area_um2()["total_mm2"]
        m.channel_present = bool(self.use_channel)
        if do_hd3:
            tr = self.tran_hd3(p, corner)
            if tr.get("ok"):
                m.hd3_db = tr["hd3_db"]
                m.hd3_ctle_db = tr.get("hd3_ctle_db", float("nan"))
            else:
                failed.append(f"hd3[{tr.get('note', '?')}]")
        if do_noise:
            nz = self.noise(p, corner)
            if nz.get("ok"):
                m.vn_in_rms = nz["vn_in_rms"]
            else:
                failed.append(f"noise[{nz.get('note', '?')}]")
        m.tap_ratio = opr.get("tap_ratio", float("nan")) if opr.get("ok") else float("nan")
        if do_eye:
            # Closed-loop PRBS through the real DFE loop. Measured cost is only
            # ~1.3 s of solve on top of the PDK parse, so with a trimmed library
            # this is affordable per-candidate and the cheap pulse-response
            # surrogate may not be needed at all.
            # tran_eye's `amp` is the DIFFERENTIAL AMPLITUDE, so the
            # peak-to-peak swing is 2*amp. Passing tx_swing_pp_v
            # straight in doubled the drive to 400 mV pp while the
            # exported deck still used 200 -- caught by the export test.
            ey = self.tran_eye(p, corner, amp=self.meas.tx_swing_pp_v / 2,
                               ui=self.meas.ui_s)
            if ey.get("ok"):
                m.eye_height_v = ey["eye"].height_v
                m.eye_width_ui = ey["eye"].width_ui
                for k in ("eye_height_in_v", "eye_height_ctle_v",
                          "eye_openness_in", "eye_openness_ctle",
                          "eye_openness_out", "eye_improvement"):
                    setattr(m, k, ey.get(k, float("nan")))
            else:
                failed.append(f"eye[{ey.get('note', '?')}]")
        # `ok` means "this result is SCOREABLE" (the AC parsed), not
        # "everything worked" -- specs.score() already maps each missing metric
        # to 0.0 through its NaN guards, which keeps a graded reward instead of
        # a cliff. Clearing ok here would make score() return an EMPTY pass
        # dict, and any caller aggregating `for k, v in sc["pass"].items()`
        # would then record no failures at all for a corner that did not
        # simulate. `partial` + `note` carry that visibility instead.
        m.ok = True
        m.partial = bool(failed)
        if failed:
            m.note = ("analyses failed: " + ",".join(failed)
                      + ("; " + m.note if m.note else ""))
        return m


def _finish(m: Metrics, failed: list[str]) -> Metrics:
    """Common tail: ok means SCOREABLE, partial records what did not run."""
    m.ok = True
    m.partial = bool(failed)
    if failed:
        m.note = ("analyses failed: " + ",".join(failed)
                  + ("; " + m.note if m.note else ""))
    return m


def _fatal_tag(log: str) -> str:
    """Pull the FATAL[...] tag out of a log for reporting."""
    i = log.find("FATAL[")
    if i < 0:
        return "unknown-failure"
    return log[i + 6: log.find("]", i)]


# ---- parsing helpers ---------------------------------------------------
def _parse_prints(log: str) -> dict[str, float]:
    """All ``name = value`` lines from ngspice, keyed by the FULL lowercased lhs.

    Keying on the full expression matters: probing two devices means several
    lines end in the same ``[vds]``/``[id]`` suffix, and a suffix matcher would
    silently keep only the last one.
    """
    out: dict[str, float] = {}
    for line in log.splitlines():
        if "=" not in line:
            continue
        lhs, _, rhs = line.partition("=")
        lhs = lhs.strip().lower()
        rhs = rhs.strip().split()[0] if rhs.strip() else ""
        try:
            out[lhs] = float(rhs)
        except ValueError:
            continue
    return out


def _parse_scalar(log: str, name: str) -> float | None:
    for line in log.splitlines():
        s = line.strip().lower()
        if s.startswith(name.lower()) and "=" in s:
            try:
                return float(s.split("=")[1].strip().split()[0])
            except (ValueError, IndexError):
                continue
    return None


def _hd3_from_wave(t: np.ndarray, v: np.ndarray, f0: float) -> float:
    """Return HD3 in dBc from a transient differential waveform.

    Uses the last whole number of cycles, windowed FFT, ratio of the 3rd
    harmonic bin to the fundamental bin.
    """
    # resample onto a uniform grid (ngspice tran steps can be non-uniform)
    n = 4096
    tu = np.linspace(t[0], t[-1], n)
    vu = np.interp(tu, t, v)
    vu = vu - np.mean(vu)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(vu * win))
    dt = tu[1] - tu[0]
    freqs = np.fft.rfftfreq(n, dt)
    def bin_amp(fc):
        k = int(np.argmin(np.abs(freqs - fc)))
        lo, hi = max(0, k - 2), min(len(spec), k + 3)
        return float(np.max(spec[lo:hi]))
    a1 = bin_amp(f0)
    a3 = bin_amp(3 * f0)
    if a1 <= 0:
        return float("nan")
    return 20.0 * math.log10(max(a3, 1e-18) / a1)
