"""Top-level design orchestrator.

``run_design`` is the single entry the deliverable calls: given target specs it
(1) searches the device sizes with the CEM RL agent on a small set of corners,
(2) verifies the winner across the full PVT grid, and (3) emits the final sized
SPICE schematic, a JSON report, and diagnostic plots.
"""
import dataclasses
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import pdk
from . import specs as sp
from . import netlist as nl
from .env import Objective
from .spice import MeasCond, Simulator
from .agents import cem as cem_agent
from . import analytic


def _sized_header(params: nl.CtleParams, corner: pdk.Corner, what: str,
                  use_channel: bool = False) -> str:
    """Comment block recording BOTH the requested values and the geometry that
    was actually instantiated -- res/cap values are quantised onto real device
    geometry, so the two are not always the same number."""
    g = params.geometry()
    return f"""* ==================================================================
* PCIe Gen2 equalizer (CTLE + 1-tap DFE summer) -- auto-sized by RL (CEM)
* {what}
* Requested (W/L in um, R in ohm, C in F, I in A):
*   W={params.W:.4g}  L={params.L:.4g}  RL={params.RL:.6g}  Rs={params.Rs:.6g}
*   Cs={params.Cs:.6g}  Itail={params.Itail:.6g}  Wtail={params.Wtail:.4g}
*   Rsum={params.Rsum:.6g}  Wg1={params.Wg1:.4g}  Ig1={params.Ig1:.6g}
*   tap_pct={params.tap_pct:.4g}%  -> Wgf={params.Wgf:.4g} Igf={params.Igf:.6g}
*   CL={params.CL:.6g}  Vcm={params.Vcm:.4g}
* Realised geometry:
*   RL -> l={g['rl_l']:.4g} m={g['rl_mult']} ({g['rl_actual']:.6g} ohm)
*   Rs -> l={g['rs_l']:.4g} m={g['rs_mult']} ({g['rs_actual']:.6g} ohm)
*   Cs -> {g['cs_s']:.4g} x {g['cs_s']:.4g} um ({g['cs_actual']:.6g} F)
*   Rsum -> l={g['rsum_l']:.4g} m={g['rsum_mult']} ({g['rsum_actual']:.6g} ohm)
*   Iref={g['iref']:.6g}  Wref={g['w_ref']:.4g}
*   Gm1 tail W={g['wg1_tail']:.4g}   GmF tail W={g['wgf_tail']:.4g}
*   nominal tap weight gmF/gm1 = {params.tap_weight_ratio():.4g}
* corner: {corner.name}
* channel: {'PRESENT (lossy FR4-like, see netlist.channel_netlist)' if use_channel else 'none'}
* =================================================================="""


def _exporter(params: nl.CtleParams, use_channel: bool, meas=None):
    """A Simulator used only to BUILD deck text, never to run anything.

    Routing the exporters through Simulator._assemble is the point: the decks
    used to be rebuilt by hand here, and when the channel was added
    `use_channel` was threaded through Simulator/Objective/env/run_design but
    not through these two functions. The result shipped an eye deck with no
    channel that reported 591.8 mV against the report's 189.0 mV -- 3.1x
    optimistic, exiting 0. Any hand-built third copy would drift the same way.

    `use_trimmed=False` is not optional: a trimmed library lives in
    ~/.cache/eqrl/trimlib and is not portable, so a shipped artifact must
    reference the full PDK library.
    """
    import tempfile
    from .spice import MeasCond, Simulator
    return Simulator(tempfile.mkdtemp(prefix="eqrl_export_"),
                     use_trimmed=False, use_channel=use_channel, meas=meas)


def _export_deck(params: nl.CtleParams, corner: pdk.Corner, path: Path,
                 use_channel: bool = False, meas=None) -> None:
    """Standalone AC deck (DFE loop OPEN) with the sized values."""
    sim = _exporter(params, use_channel, meas)
    control = (".control\n"
               "ac dec 40 1e6 20e9\n"
               "wrdata ctle_ac.dat vdb(outp_ctle,outn_ctle) "
               "vdb(outp_dfe,outn_dfe) vdb(vinp,vinn)\n"
               ".endc\n")
    body = sim._assemble(params, corner, "ac", control, dfe="cm")
    path.write_text(_sized_header(params, corner,
                                  "forward path, DFE loop OPEN",
                                  use_channel) + "\n" + body)


def _export_eye_deck(params: nl.CtleParams, corner: pdk.Corner, path: Path,
                     use_channel: bool = False, n_bits: int = 200,
                     meas=None) -> None:
    """Standalone CLOSED-loop deck, so the reported eye is reproducible.

    The stimulus is built the same way Simulator.tran_eye() builds it, so it
    cannot drift from what was actually simulated either.

    UI and drive level come from `meas`, the same object the simulation used.
    They were separate parameters with their own defaults, which is precisely
    how a deck can be emitted at one drive level while the report quotes
    another.
    """
    from . import dfe as dfe_mod
    from .spice import MeasCond as _MC
    meas = meas or _MC()
    ui, tx_swing_pp_v = meas.ui_s, meas.tx_swing_pp_v
    sim = _exporter(params, use_channel, meas)
    bits = dfe_mod.prbs(n_bits)
    # nrz_pwl takes the SINGLE-ENDED amplitude about Vcm; each leg swings
    # +/-(pp/4) so the differential peak-to-peak is pp. Getting this factor
    # wrong silently doubles the drive -- it did once, and the export test
    # caught it.
    se = tx_swing_pp_v / 4.0
    src = (f"Vinp vinp 0 {dfe_mod.nrz_pwl(bits, ui, se, params.Vcm)}\n"
           f"Vinn vinn 0 "
           f"{dfe_mod.nrz_pwl(bits, ui, se, params.Vcm, invert=True)}\n")
    ts = ui / 50.0
    control = (".control\n"
               f"tran {ts:.6g} {n_bits * ui:.6g} 0 {ts:.6g}\n"
               "wrdata eye.dat v(outp_dfe,outn_dfe) v(vfbp)\n"
               ".endc\n")
    body = sim._assemble(params, corner, "tran", control, dfe="closed",
                         src_override=src, ui=ui)
    path.write_text(_sized_header(params, corner,
                                  f"closed-loop eye, DFE loop CLOSED, "
                                  f"{tx_swing_pp_v*1e3:.0f} mV pp differential drive",
                                  use_channel) + "\n" + body)


def _worst_margins(vres, spec) -> dict:
    """Per-spec fractional margin at the worst corner for that spec."""
    out: dict = {}
    for sc in vres.per_corner.values():
        for k, v in sp.margins(sc["metrics"], spec).items():
            if v != v:
                continue
            if k not in out or v < out[k]:
                out[k] = v
    return out


def _worst_margin(vres, spec):
    mg = _worst_margins(vres, spec)
    if not mg:
        return None
    k = min(mg, key=lambda x: mg[x])
    return [k, mg[k]]


def _json_safe(o):
    """Replace non-finite floats with null: json.dumps emits a bare `NaN`
    token by default, which is not valid JSON and which strict parsers reject."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _plot(report: dict, obj: Objective, best, out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    # convergence
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(1, len(report["curve"]) + 1), report["curve"], "-o", ms=4)
    ax.set_xlabel("CEM iteration"); ax.set_ylabel("best reward")
    ax.set_title("RL (CEM) convergence"); ax.grid(True, alpha=0.3)
    fig.tight_layout(); fig.savefig(out_dir / "convergence.png", dpi=110)
    plt.close(fig)

    # AC response of the winning design (re-read the dumped sweep)
    sim = obj.sim
    acr = sim.ac(best.params, obj.corners[0], tag="final_ac")
    dat = sim.workdir / "final_ac.dat"
    if dat.exists():
        try:
            d = np.loadtxt(dat)
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.semilogx(d[:, 0], d[:, 1])
            ax.axvspan(obj.spec.fpeak_lo, obj.spec.fpeak_hi, alpha=0.12,
                       color="green", label="peak band")
            ax.set_xlabel("Frequency (Hz)")
            ax.set_ylabel("Diff gain (dB)")
            ax.set_title("Final CTLE AC response")
            ax.grid(True, which="both", alpha=0.3); ax.legend()
            fig.tight_layout(); fig.savefig(out_dir / "ctle_ac.png", dpi=110)
            plt.close(fig)
        except Exception:
            pass


def run_design(spec: sp.EqualizerSpec | None = None,
               workdir: str | Path = "/tmp/eqrl_work",
               out_dir: str | Path = "results",
               cem_cfg: cem_agent.CemConfig | None = None,
               search_robust: bool = False,
               pvt_grid: str = "spec",
               do_eye: bool = True,
               use_channel: bool = False,
               analytic_init: bool = True,
               jobs: int | None = None,
               fixed: dict | None = None,
               eval_log: bool = True,
               verbose: bool = True) -> dict:
    spec = spec or sp.EqualizerSpec()
    if not do_eye and spec.require_eye:
        # An AC-only run genuinely has no eye to judge, so scoring one as a
        # failure would be wrong. Every other path must keep require_eye=True:
        # a missing eye when we DID ask for one is a failure, not an excuse.
        spec = dataclasses.replace(spec, require_eye=False)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    # Measurement conditions come from the SPEC, so asking for a different bit
    # rate or drive level changes what is actually simulated rather than only
    # what the report claims.
    meas = MeasCond.from_spec(spec)
    sim = Simulator(workdir, use_channel=use_channel, meas=meas)

    # Search corners: typical only (fast) or a robust subset.
    if search_robust:
        # One definition, in pdk. The R/C axis MUST be in this set: sky130's
        # named process corners move MOSFETs only, and R/C moves f_peak by
        # 29.7% -- more than process does. `ll` is the worst f_peak corner, so
        # a "robust" search without it converges to a design that fails full
        # PVT on exactly that spec.
        search_corners = pdk.robust_search_corners()
    else:
        search_corners = [pdk.typical_corner()]

    # Every (candidate, corner) the search evaluates is appended to disk as it
    # lands. These are expensive labels -- ~4.4 s of ngspice each -- and before
    # this they lived only in Objective._cache and died with the process.
    # Writing is on by default; READING a cache back into a search is not, so
    # the launch counts a run reports stay honest.
    log = None
    if eval_log:
        from .cache import EvalLog
        log = EvalLog(f"cem_{time.strftime('%Y%m%d_%H%M%S')}",
                      extra={"search_robust": search_robust,
                             "do_eye": do_eye, "use_channel": use_channel})
        log.open()

    obj = Objective(spec, sim, corners=search_corners,
                    fixed=fixed or {}, robust="min", do_eye=do_eye, jobs=jobs,
                    use_channel=use_channel, log=log)

    cem_cfg = cem_cfg or cem_agent.CemConfig()
    if analytic_init:
        # Derive the starting point from the small-signal equations, with the
        # device parameters MEASURED at run time and the result calibrated
        # against SPICE in a closed loop. No human in the loop -- this is smart
        # initialisation, not a hand-tuned seed.
        if verbose:
            print("=== analytic initialisation ===")
        try:
            cem_cfg.init_mean = analytic.initial_u(spec, sim, verbose=verbose)
            # tighter first population: starting from a known-good point, not
            # from ignorance
            cem_cfg.init_std = cem_cfg.seeded_std
        except Exception as e:                      # noqa: BLE001
            if verbose:
                print(f"  [analytic] failed ({e}); starting from the box centre")
    if verbose:
        print(f"=== RL sizing: {len(search_corners)} search corner(s), "
              f"agent=CEM ===")
    res = cem_agent.optimize(obj, cem_cfg, verbose=verbose)
    best = res.best
    if best is None:
        raise RuntimeError("optimization produced no valid candidate")

    # --- PVT verification of the winner, in parallel over corners ---
    # pvt_grid(full=<bool>) silently mapped False -> the 9-corner "quick" grid,
    # so the default run "verified across PVT" on 9 corners while reporting a
    # field called pvt_meets_all. Grids are named explicitly now.
    pvt = pdk.pvt_grid(pvt_grid)
    verify = Objective(spec, sim, corners=pvt, fixed=fixed or {},
                       robust="min", do_eye=do_eye, jobs=jobs,
                       use_channel=use_channel)
    vres = verify(best.u)
    pvt_report, worst_pass = {}, {}
    unscoreable = []
    for cname, sc in vres.per_corner.items():
        m = sc["metrics"]
        pvt_report[cname] = {"metrics": m.as_dict(), "pass": sc["pass"],
                             "reward": sc["reward"],
                             "partial": bool(getattr(m, "partial", False)),
                             "note": m.note}
        # A corner that produced no scorecard must not vanish from the
        # aggregate: iterating an empty pass dict records no failures, so the
        # design would read as passing PVT with a corner silently missing.
        if not sc["pass"] or getattr(m, "partial", False):
            unscoreable.append(cname)
        # Keys are derived from the scorecard, never hand-maintained: the two
        # drifted the moment `area` was added and run_design died with a
        # KeyError after spending the entire search budget.
        for k, v in sc["pass"].items():
            worst_pass[k] = worst_pass.get(k, True) and bool(v)

    # --- export artifacts ---
    # Evaluate the winner at the TYPICAL corner explicitly: the search grids
    # never contain it (spec/full use VDD+-5% and 0/125 C only), and the
    # exported decks are written at it, so without this the report has no
    # numbers that correspond to the artifact it ships.
    typ = pdk.typical_corner()
    typ_scored = sp.score(sim.evaluate(best.params, typ, do_eye=do_eye), spec)
    _export_deck(best.params, typ, out_dir / "ctle_final.sp",
                 use_channel=use_channel, meas=meas)
    _export_eye_deck(best.params, typ, out_dir / "ctle_final_eye.sp",
                     use_channel=use_channel, meas=meas)

    report = {
        "spec": asdict(spec),
        "agent": "CEM",
        # Three different costs, previously conflated under "n_simulations":
        "n_candidates": res.n_eval,              # distinct designs simulated
        "n_corner_evals": obj.n_sims,            # candidates x search corners
        "n_ngspice_launches": obj.n_launches,    # the real wall-clock driver
        "n_ngspice_launches_verify": verify.n_launches,
        "search_corners": [c.name for c in search_corners],
        "pvt_grid": pvt_grid,
        "n_pvt_corners": len(pvt),
        "eye_scored": do_eye,
        # The drive level the eye was measured at. Eye height scales directly
        # with it, so the number is uninterpretable without this.
        "tx_swing_pp_v": spec.tx_swing_pp_v,
        "channel": use_channel,
        "analytic_init": analytic_init,
        "curve": res.curve,
        "final_params": best.params.as_dict(),
        "final_geometry": best.params.geometry(),
        # NOT the typical corner: Objective reports the WORST search corner,
        # because that is what the stopping rule must test. Labelling it "typ"
        # made the exported (typical-corner) deck look 25% off when it was
        # exactly right -- tt_ll pushes f_peak up ~33%.
        # Gain-invariant companions to eye height. Eye height can be bought
        # with summing-stage gain (measured: it correlates with that gain at
        # 0.98 and with peaking at only 0.47); openness and improvement cannot.
        "eye_openness_out": typ_scored["metrics"].eye_openness_out,
        "out_cm_v": typ_scored["metrics"].out_cm_v,
        "vdd": typ.vdd,
        # the spec closest to failing, over the whole PVT grid -- reporting the
        # comfortable edge of a two-sided spec flatters
        "binding_spec": _worst_margin(vres, spec),
        "margins_worst": _worst_margins(vres, spec),
        "eye_improvement": typ_scored["metrics"].eye_improvement,
        "worst_search_corner": best.scored_corner,
        "worst_search_corner_metrics": best.scored["metrics"].as_dict(),
        "worst_search_corner_pass": best.scored["pass"],
        "typ_metrics": typ_scored["metrics"].as_dict(),
        "typ_pass": typ_scored["pass"],
        "typ_corner": typ.name,
        "pvt": pvt_report,
        "pvt_all_pass": worst_pass,
        "pvt_unscoreable_corners": unscoreable,
        # A corner that failed to simulate is NOT a pass.
        "pvt_meets_all": (bool(worst_pass) and all(worst_pass.values())
                          and not unscoreable),
    }
    (out_dir / "report.json").write_text(
        json.dumps(_json_safe(report), indent=2, default=str, allow_nan=False))
    _plot(report, obj, best, out_dir)

    if log is not None:
        log.close()
        if verbose:
            print(f"\n  {log.n_written} evaluations kept in "
                  f"results/evals/{log.tag}.jsonl")
    if verbose:
        _print_summary(report)
    return report


def _print_summary(r: dict) -> None:
    m = r["typ_metrics"]; p = r["final_params"]
    print("\n" + "=" * 62)
    print(f"FINAL DESIGN  (metrics at {r['typ_corner']}, the corner the "
          f"exported decks use)")
    print("=" * 62)
    print(f"  W={p['W']:.3g}um L={p['L']:.3g}um RL={p['RL']:.0f}ohm "
          f"Rs={p['Rs']:.0f}ohm Cs={p['Cs']*1e15:.1f}fF It={p['Itail']*1e6:.0f}uA")
    print(f"  DC gain    : {m['dc_gain_db']:.2f} dB")
    print(f"  Peaking    : {m['peaking_db']:.2f} dB @ {m['f_peak']/1e9:.2f} GHz")
    print(f"  Gain@2.5GHz: {m['gain_nyq_db']:.2f} dB")
    print(f"  Power      : {m['power_w']*1e3:.2f} mW")
    print(f"  HD3        : {m['hd3_db']:.1f} dBc")
    print(f"  In. noise  : {m['vn_in_rms']*1e3:.3f} mVrms")
    _ew = m.get("eye_width_ui")
    if _ew is not None and math.isfinite(_ew):
        print(f"  Eye        : {m['eye_height_v']*1e3:.1f} mV x "
              f"{m['eye_width_ui']:.3f} UI   (at {r['tx_swing_pp_v']*1e3:.0f} mV pp drive)")
        _imp = m.get("eye_improvement")
        if _imp is not None and math.isfinite(_imp):
            print(f"  Eye opening: {m['eye_openness_out']:.3f} of swing, "
                  f"{_imp:.2f}x better than at the input")
            print(f"               (>1 = real equalisation; eye HEIGHT alone "
                  f"can be bought with gain)")
    print(f"  Candidates : {r['n_candidates']}  "
          f"(ngspice launches: {r['n_ngspice_launches']} search + "
          f"{r['n_ngspice_launches_verify']} verify)")
    nb = sum(1 for k in r["pvt_all_pass"] if k in sp.BRIEF_SPECS and r["pvt_all_pass"][k])
    tb = sum(1 for k in r["pvt_all_pass"] if k in sp.BRIEF_SPECS)
    print(f"  PVT ({r['pvt_grid']}, {r['n_pvt_corners']} corners) meets all: "
          f"{r['pvt_meets_all']}")
    print(f"    {nb}/{tb} BRIEF specs + "
          f"{len([k for k in r['pvt_all_pass'] if k in sp.INTERNAL_CHECKS])} "
          f"internal bias check (not a customer requirement)")
    if r.get("binding_spec"):
        bn, bm = r["binding_spec"]
        print(f"    tightest margin: {bn} at {bm*100:+.1f}% of its limit")
    if r.get("out_cm_v") == r.get("out_cm_v"):
        print(f"    summing-node CM: {r['out_cm_v']:.3f} V of {r['vdd']:.2f} V "
              f"(mid-supply heuristic wants {r['vdd']/2:.3f} V)")
    if r.get("pvt_unscoreable_corners"):
        print(f"  !! {len(r['pvt_unscoreable_corners'])} corner(s) did not "
              f"fully simulate: {r['pvt_unscoreable_corners'][:4]}")
    for k, v in sorted(r['pvt_all_pass'].items()):
        print(f"     {k:10s} {'ok' if v else 'FAIL'}")
    print("=" * 62)
