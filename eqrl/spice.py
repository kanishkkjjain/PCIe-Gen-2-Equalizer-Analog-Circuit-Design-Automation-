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
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import netlist as nl
from . import pdk
from .netlist import CtleParams


@dataclass
class Metrics:
    """Measured performance of one CTLE candidate at one corner."""

    ok: bool = False                 # simulation converged and parsed
    dc_gain_db: float = float("nan")  # differential gain at 1 MHz
    peak_gain_db: float = float("nan")
    peaking_db: float = float("nan")  # peak - dc
    f_peak: float = float("nan")      # Hz of peak gain
    gain_2g5_db: float = float("nan")  # gain at Nyquist (2.5 GHz)
    bw_3db: float = float("nan")      # -3dB freq relative to peak (Hz)
    power_w: float = float("nan")
    hd3_db: float = float("nan")      # 3rd-harmonic distortion (dBc)
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
    tap_weight_v: float = float("nan")
    saturated: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class Simulator:
    def __init__(self, workdir: str | Path, ngspice_bin: str = "ngspice",
                 timeout: float = 60.0):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.ngspice = shutil.which(ngspice_bin) or ngspice_bin
        self.timeout = timeout
        self._lib = str(pdk.find_sky130_lib())

    # ---- deck assembly -------------------------------------------------
    def _header(self, corner: pdk.Corner) -> str:
        return (f"* auto-generated CTLE deck  corner={corner.name}\n"
                f'.lib "{self._lib}" {corner.lib_section}\n'
                f".temp {corner.temp:.4g}\n"
                f".param Vcm={{Vcm}}\n")

    def _assemble(self, p: CtleParams, corner: pdk.Corner, analysis: str,
                  control: str, tran_amp: float = 0.1,
                  tran_freq: float = 100e6, dfe: str | None = None,
                  src_override: str | None = None) -> str:
        """Assemble a deck.

        ``dfe`` selects how the tap pair's gates are driven:
          None / "cm"  -> held at common mode (forward-path characterisation)
          "pos"/"neg"  -> fully steered one way (to measure the tap weight)
          "closed"     -> the XSPICE slicer->latch->tap feedback loop
        Without one of these, vfbp/vfbn float and every analysis fails.
        """
        src = (src_override if src_override is not None
               else nl.build_source_lines(analysis, tran_amp=tran_amp,
                                          tran_freq=tran_freq))
        if dfe in (None, "cm"):
            src += nl.dfe_static_bias()
        elif dfe == "pos":
            src += nl.dfe_static_tap(+1.0)
        elif dfe == "neg":
            src += nl.dfe_static_tap(-1.0)
        elif dfe == "closed":
            src += nl.dfe_decision_path()
        else:
            raise ValueError(f"unknown dfe mode {dfe!r}")
        # substitute Vcm literal (kept as a param for readability)
        deck = [
            self._header(corner),
            f".param Vcm={p.Vcm:.4g}",
            f"Vdd vdd 0 DC {corner.vdd:.4g}",
            src.replace("{Vcm}", f"{p.Vcm:.4g}"),
            p.core_netlist(),
            control,
            ".end\n",
        ]
        return "\n".join(deck)

    def _run(self, deck: str, tag: str) -> tuple[bool, str]:
        deck_path = self.workdir / f"{tag}.sp"
        deck_path.write_text(deck)
        try:
            r = subprocess.run(
                [self.ngspice, "-b", str(deck_path)],
                cwd=self.workdir, capture_output=True, text=True,
                timeout=self.timeout,
            )
            return True, (r.stdout + "\n" + r.stderr)
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"
        except FileNotFoundError:
            return False, "ngspice-not-found"

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
            f'wrdata {out.name} vdb(voutp,voutn)\n'
            ".endc\n"
        )
        ok, log = self._run(self._assemble(p, corner, "ac", control), tag)
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log}
        f, gdb = d[:, 0], d[:, 1]
        # dc gain ~ lowest frequency point
        dc = float(gdb[0])
        ipk = int(np.argmax(gdb))
        peak = float(gdb[ipk])
        fpk = float(f[ipk])
        g25 = float(np.interp(2.5e9, f, gdb))
        # 3dB bandwidth relative to peak (first crossing below peak-3 after peak)
        bw = float("nan")
        target = peak - 3.0
        for i in range(ipk, len(f)):
            if gdb[i] <= target:
                bw = float(f[i]); break
        return {"ok": True, "dc_gain_db": dc, "peak_gain_db": peak,
                "peaking_db": peak - dc, "f_peak": fpk, "gain_2g5_db": g25,
                "bw_3db": bw}

    def op(self, p: CtleParams, corner: pdk.Corner, tag: str = "op") -> dict:
        # Derived from netlist.NFET, never hardcoded: the sky130 subckt names its
        # internal MOS "m<subckt-name>", so the probe path must track the device
        # flavour. A mismatch here fails silently (no rows parsed -> ok=False ->
        # saturated=False -> reward capped at 0.2 for every candidate).
        dev = f"@m.x1.m{nl.NFET}"      # input pair
        tail = f"@m.xt1.m{nl.NFET}"    # real tail device -- headroom now matters
        control = (
            ".control\n"
            "op\n"
            f"print {dev}[id] {dev}[gm] {dev}[vds] {dev}[vdsat] {dev}[vgs]\n"
            f"print {tail}[id] {tail}[vds] {tail}[vdsat]\n"
            "print i(Vdd)\n"          # true total supply current
            ".endc\n"
        )
        ok, log = self._run(self._assemble(p, corner, "op", control), tag)
        v = _parse_prints(log)
        need = [f"{dev}[{k}]" for k in ("id", "gm", "vds", "vdsat", "vgs")]
        if not all(k in v for k in need):
            return {"ok": False, "log": log}
        idv, gm, vds, vdsat, vgs = (v[k] for k in need)
        t_id = v.get(f"{tail}[id]", float("nan"))
        t_vds = v.get(f"{tail}[vds]", float("nan"))
        t_vdsat = v.get(f"{tail}[vdsat]", float("nan"))
        # ngspice reports source current flowing INTO the + terminal, so the
        # supply draw is the magnitude.
        isup = abs(v.get("i(vdd)", float("nan")))
        tail_sat = (t_vds == t_vds) and (t_vds > t_vdsat + 0.02)
        return {"ok": True, "id_m1": idv, "gm_m1": gm, "vds_m1": vds,
                "vdsat_m1": vdsat, "vgs_m1": vgs,
                "id_tail": t_id, "vds_tail": t_vds, "vdsat_tail": t_vdsat,
                "tail_saturated": tail_sat, "i_supply": isup,
                "saturated": (vds > vdsat + 0.02) and (idv > 1e-9) and tail_sat}

    def tap_weight(self, p: CtleParams, corner: pdk.Corner) -> dict:
        """Measure the realised DFE tap weight: steer the pair both ways, diff.

        Expect |dV| ~ 2*Itap*RL. A much smaller number means the pair is not
        fully switching (Wfb too small, or the feedback swing too weak).
        """
        vals = {}
        for sign, tag in ((+1.0, "pos"), (-1.0, "neg")):
            control = (".control\nop\nprint v(voutp) v(voutn)\n.endc\n")
            mode = "pos" if sign > 0 else "neg"
            ok, log = self._run(
                self._assemble(p, corner, "op", control, dfe=mode),
                f"tap_{tag}")
            v = _parse_prints(log)
            if "v(voutp)" not in v or "v(voutn)" not in v:
                return {"ok": False, "log": log}
            vals[tag] = v["v(voutp)"] - v["v(voutn)"]
        dv = abs(vals["pos"] - vals["neg"])
        ideal = 2.0 * p.Itap * p.RL
        return {"ok": True, "tap_dv": dv, "tap_ideal": ideal,
                "tap_efficiency": (dv / ideal) if ideal > 0 else float("nan")}

    def tran_hd3(self, p: CtleParams, corner: pdk.Corner,
                 amp: float = 0.1, freq: float = 100e6, tag: str = "tran") -> dict:
        out = self.workdir / f"{tag}.dat"
        if out.exists():
            out.unlink()
        ncyc = 20
        tstop = ncyc / freq
        tstep = 1.0 / (freq * 512)   # 512 pts/cycle
        control = (
            ".control\n"
            f"tran {tstep:.6g} {tstop:.6g} 0 {tstep:.6g}\n"
            f'wrdata {out.name} v(voutp,voutn)\n'
            ".endc\n"
        )
        ok, log = self._run(
            self._assemble(p, corner, "tran", control, tran_amp=amp,
                           tran_freq=freq), tag)
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log}
        t, v = d[:, 0], d[:, 1]
        return {"ok": True, "hd3_db": _hd3_from_wave(t, v, freq)}

    def tran_eye(self, p: CtleParams, corner: pdk.Corner, n_bits: int = 200,
                 ui: float = 200e-12, amp: float = 0.10,
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
            f"wrdata {out.name} v(voutp,voutn) v(vfbp)\n"
            ".endc\n"
        )
        ok, log = self._run(
            self._assemble(p, corner, "tran", control, dfe="closed",
                           src_override=src), tag)
        d = self._read_wrdata(out)
        if d is None or d.shape[1] < 2:
            return {"ok": False, "log": log}
        t, v = d[:, 0], d[:, 1]
        vfb = d[:, 3] if d.shape[1] >= 4 else None
        eye = dfe_mod.eye_from_waveform(t, v, ui)
        return {"ok": True, "eye": eye, "t": t, "v": v, "vfb": vfb,
                "n_bits": n_bits}

    def noise(self, p: CtleParams, corner: pdk.Corner, tag: str = "noise") -> dict:
        """Integrated input-referred noise.

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
            "noise v(voutp,voutn) Vinp dec 20 10e6 5e9\n"
            "setplot noise1\n"
            f"wrdata {out.name} inoise_spectrum\n"
            "setplot noise2\n"
            "print inoise_total\n"
            ".endc\n"
        )
        ok, log = self._run(self._assemble(p, corner, "noise", control), tag)
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

    # ---- top-level -----------------------------------------------------
    def evaluate(self, p: CtleParams, corner: pdk.Corner,
                 do_hd3: bool = True, do_noise: bool = True,
                 do_eye: bool = False) -> Metrics:
        m = Metrics()
        acr = self.ac(p, corner)
        if not acr.get("ok"):
            m.note = "ac-failed: " + acr.get("log", "")[-200:]
            return m
        for k in ("dc_gain_db", "peak_gain_db", "peaking_db", "f_peak",
                  "gain_2g5_db", "bw_3db"):
            setattr(m, k, acr[k])
        opr = self.op(p, corner)
        if opr.get("ok"):
            m.id_m1 = opr["id_m1"]; m.gm_m1 = opr["gm_m1"]
            m.vds_m1 = opr["vds_m1"]; m.vdsat_m1 = opr["vdsat_m1"]
            m.id_tail = opr["id_tail"]; m.vds_tail = opr["vds_tail"]
            m.vdsat_tail = opr["vdsat_tail"]
            m.tail_saturated = opr["tail_saturated"]
            m.i_supply = opr["i_supply"]
            m.saturated = opr["saturated"]
        # Power from the MEASURED supply current. The old analytic
        # `vdd * 2 * Itail` was wrong twice over: it ignored the mirror
        # reference branch, and the mirror does not actually deliver Itail
        # (Vds mismatch against the diode-connected reference costs ~8%).
        if m.i_supply == m.i_supply:
            m.power_w = corner.vdd * m.i_supply
        else:
            m.power_w = corner.vdd * (2.0 + 1.0 / nl.MIRROR_RATIO) * p.Itail
        m.area_mm2 = p.area_um2()["total_mm2"]
        if do_hd3:
            tr = self.tran_hd3(p, corner)
            if tr.get("ok"):
                m.hd3_db = tr["hd3_db"]
        if do_noise:
            nz = self.noise(p, corner)
            if nz.get("ok"):
                m.vn_in_rms = nz["vn_in_rms"]
        m.tap_weight_v = p.tap_weight_v()
        if do_eye:
            # Closed-loop PRBS through the real DFE loop. Measured cost is only
            # ~1.3 s of solve on top of the PDK parse, so with a trimmed library
            # this is affordable per-candidate and the cheap pulse-response
            # surrogate may not be needed at all.
            ey = self.tran_eye(p, corner)
            if ey.get("ok"):
                m.eye_height_v = ey["eye"].height_v
                m.eye_width_ui = ey["eye"].width_ui
        m.ok = True
        return m


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


def _parse_print_row(log: str, keys: list[str]) -> list[float] | None:
    """Parse ngspice ``print a b c`` output lines of the form ``name = value``.

    ngspice prints one ``<expr> = <value>`` per line for scalar prints.
    """
    found: dict[str, float] = {}
    for line in log.splitlines():
        if "=" not in line:
            continue
        lhs, _, rhs = line.partition("=")
        lhs = lhs.strip().lower()
        rhs = rhs.strip().split()[0] if rhs.strip() else ""
        for k in keys:
            if lhs.endswith(f"[{k}]") or lhs.endswith(k):
                try:
                    found[k] = float(rhs)
                except ValueError:
                    pass
    if all(k in found for k in keys):
        return [found[k] for k in keys]
    return None


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
