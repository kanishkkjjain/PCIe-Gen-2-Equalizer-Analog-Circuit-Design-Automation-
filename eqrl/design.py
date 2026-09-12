"""Top-level design orchestrator.

``run_design`` is the single entry the deliverable calls: given target specs it
(1) searches the device sizes with the CEM RL agent on a small set of corners,
(2) verifies the winner across the full PVT grid, and (3) emits the final sized
SPICE schematic, a JSON report, and diagnostic plots.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import pdk
from . import specs as sp
from . import netlist as nl
from .env import Objective
from .spice import Simulator
from .agents import cem as cem_agent


def _export_deck(params: nl.CtleParams, corner: pdk.Corner, path: Path) -> None:
    """Write a standalone, human-openable CTLE AC deck with the sized values."""
    lib = pdk.find_sky130_lib()
    deck = f"""* ==================================================================
* PCIe Gen2 CTLE  --  auto-sized by RL (CEM) framework
* Sized device parameters (W/L in um, R in ohm, C in F, I in A):
*   W={params.W:.4g}  L={params.L:.4g}  RL={params.RL:.6g}
*   Rs={params.Rs:.6g}  Cs={params.Cs:.6g}  Itail={params.Itail:.6g}
*   CL={params.CL:.6g}  Vcm={params.Vcm:.4g}
* ==================================================================
.lib "{lib}" {corner.lib_section}
.temp {corner.temp:.4g}
.param Vcm={params.Vcm:.4g}

Vdd vdd 0 DC {corner.vdd:.4g}
Vinp vinp 0 DC {{Vcm}} AC 0.5 0
Vinn vinn 0 DC {{Vcm}} AC 0.5 180

{params.core_netlist()}
.control
ac dec 40 1e6 20e9
* differential gain (dB) vs frequency
plot vdb(voutp,voutn)
wrdata ctle_ac.dat vdb(voutp,voutn)
.endc
.end
"""
    path.write_text(deck)


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
            ax.axvspan(1.25e9, 2.5e9, alpha=0.12, color="green",
                       label="peak band")
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
               verify_full_pvt: bool = True,
               fixed: dict | None = None,
               verbose: bool = True) -> dict:
    spec = spec or sp.EqualizerSpec()
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    sim = Simulator(workdir)

    # search corners: typical only (fast) or a 3-corner robust subset
    if search_robust:
        search_corners = [pdk.Corner("tt", pdk.VDD_NOM, 27.0),
                          pdk.Corner("ss", pdk.VDD_CORNERS["lo"], 125.0),
                          pdk.Corner("ff", pdk.VDD_CORNERS["hi"], 0.0)]
    else:
        search_corners = [pdk.typical_corner()]

    obj = Objective(spec, sim, corners=search_corners,
                    fixed=fixed or {}, robust="min")

    if verbose:
        print(f"=== RL sizing: {len(search_corners)} search corner(s), "
              f"agent=CEM ===")
    res = cem_agent.optimize(obj, cem_cfg, verbose=verbose)
    best = res.best
    if best is None:
        raise RuntimeError("optimization produced no valid candidate")

    # --- full PVT verification of the winner ---
    pvt = pdk.pvt_grid(full=verify_full_pvt)
    pvt_report = {}
    worst_pass = {k: True for k in
                  ["peaking", "fpeak", "gain2g5", "power", "hd3", "noise", "bias"]}
    for c in pvt:
        m = sim.evaluate(best.params, c)
        sc = sp.score(m, spec)
        pvt_report[c.name] = {"metrics": m.as_dict(), "pass": sc["pass"],
                              "reward": sc["reward"]}
        for k, v in sc["pass"].items():
            worst_pass[k] = worst_pass[k] and bool(v)

    # --- export artifacts ---
    typ = pdk.typical_corner()
    _export_deck(best.params, typ, out_dir / "ctle_final.sp")

    report = {
        "spec": asdict(spec),
        "agent": "CEM",
        "n_simulations": obj.n_sims,
        "n_evaluations": res.n_eval,
        "search_corners": [c.name for c in search_corners],
        "curve": res.curve,
        "final_params": best.params.as_dict(),
        "typ_metrics": best.scored["metrics"].as_dict(),
        "typ_pass": best.scored["pass"],
        "pvt": pvt_report,
        "pvt_all_pass": worst_pass,
        "pvt_meets_all": all(worst_pass.values()),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    _plot(report, obj, best, out_dir)

    if verbose:
        _print_summary(report)
    return report


def _print_summary(r: dict) -> None:
    m = r["typ_metrics"]; p = r["final_params"]
    print("\n" + "=" * 62)
    print("FINAL DESIGN (typical corner)")
    print("=" * 62)
    print(f"  W={p['W']:.3g}um L={p['L']:.3g}um RL={p['RL']:.0f}ohm "
          f"Rs={p['Rs']:.0f}ohm Cs={p['Cs']*1e15:.1f}fF It={p['Itail']*1e6:.0f}uA")
    print(f"  DC gain    : {m['dc_gain_db']:.2f} dB")
    print(f"  Peaking    : {m['peaking_db']:.2f} dB @ {m['f_peak']/1e9:.2f} GHz")
    print(f"  Gain@2.5GHz: {m['gain_2g5_db']:.2f} dB")
    print(f"  Power      : {m['power_w']*1e3:.2f} mW")
    print(f"  HD3        : {m['hd3_db']:.1f} dBc")
    print(f"  In. noise  : {m['vn_in_rms']*1e3:.3f} mVrms")
    print(f"  Sims used  : {r['n_simulations']}")
    print(f"  PVT meets all specs: {r['pvt_meets_all']}  {r['pvt_all_pass']}")
    print("=" * 62)
