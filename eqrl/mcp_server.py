"""MCP server: design a PCIe Gen2 equalizer by asking for one.

The brief's bonus deliverable -- "LLM-based human interaction with wrapper" --
exposing the framework so a person can say "8 dB of peaking around 1.8 GHz,
under 10 mW" and get back sized devices, a 20-corner scorecard, an eye diagram,
the AC response, and a netlist they can run themselves.

WHY IT USES BOTH METHODS. The SAC policy was trained on a BOUNDED goal space
(`results/goal_space.json`), and inside it the policy is the better instrument
by a wide margin -- 56.2 % of its proposals survive 20-corner verification
against CEM's 32.5 %, and it proposes in 1.2 s against 41 s. Outside that space
it is extrapolating. CEM is a search rather than a learned map, so it has no
trained range and works anywhere in the design box. The two also fail on
DIFFERENT specs (union 9 of 10, against 8 for either alone), so the fallback is
worth having rather than merely safe.

THE HONESTY RULES, WHICH ARE THE POINT OF THIS FILE. An LLM wrapper is exactly
where a project's careful measurements get rounded up into claims, so the rules
live in code here, not in a prompt:

  * a pass is NEVER reported unless all 20 PVT corners were actually simulated.
    The surrogate's opinion is an intermediate, never a result.
  * a failure still returns the best design found, naming the specs it missed
    and by how much. "Nothing found", with no artifact, helps nobody.
  * the method that produced the design, and whether the request was inside the
    policy's trained envelope, are always reported.
  * margins are printed as measured. Nothing is rounded toward passing.

`tests/test_mcp.py` pins these as behaviour.
"""
from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eqrl import netlist as nl, pdk, specs as sp

#: Root for per-call artifact directories. Returned as paths as well as inline
#: images, so the result is still usable from a host that cannot render images.
#:
#: Every call gets its OWN subdirectory under this, and that is not tidiness.
#: The server is long-lived: a single fixed output path means the second design
#: of a session overwrites the first, so the path the first answer handed back
#: then shows a DIFFERENT circuit's eye. Nothing errors, nothing warns, and a
#: human comparing two designs is looking at one of them twice.
OUT_ROOT = Path("/tmp/eqrl_mcp")


def _call_dir(spec: sp.EqualizerSpec) -> Path:
    """A fresh directory for one call's plots and netlists.

    Named by time and by a hash of the spec, so the directory says what it
    holds and two different requests can never collide.
    """
    import hashlib
    key = hashlib.sha256(
        repr(dataclasses.astuple(spec)).encode()).hexdigest()[:8]
    d = OUT_ROOT / f"{time.strftime('%H%M%S')}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d

#: How many designs to propose before screening. Eight is what the shipped
#: measurement used, and the coverage curve is flat past seven.
N_PROPOSALS = 8


# --------------------------------------------------------------------------
# turning a human request into a spec
# --------------------------------------------------------------------------
def build_spec(peaking_db: float | None = None,
               fpeak_lo_ghz: float | None = None,
               fpeak_hi_ghz: float | None = None,
               hd3_max_db: float | None = None,
               noise_max_mvrms: float | None = None,
               power_max_mw: float | None = None,
               eye_height_min_mv: float | None = None,
               eye_width_min_ui: float | None = None) -> sp.EqualizerSpec:
    """Human-friendly units in, `EqualizerSpec` out.

    Everything is optional and defaults to the brief, so a caller who only
    cares about power can say so and leave the rest alone. Units are named in
    the argument (GHz, mW, mV) because "power_max=10" is ambiguous and an LLM
    filling it in will guess.
    """
    kw: dict = {}
    if peaking_db is not None:
        # a target with the brief's +/- window around it, clipped to the brief
        kw["peaking_target_db"] = float(peaking_db)
        kw["peaking_min_db"] = max(3.0, float(peaking_db) - 2.5)
        kw["peaking_max_db"] = min(12.0, float(peaking_db) + 2.5)
    if fpeak_lo_ghz is not None:
        kw["fpeak_lo"] = float(fpeak_lo_ghz) * 1e9
    if fpeak_hi_ghz is not None:
        kw["fpeak_hi"] = float(fpeak_hi_ghz) * 1e9
    if hd3_max_db is not None:
        kw["hd3_max_db"] = float(hd3_max_db)
    if noise_max_mvrms is not None:
        kw["vn_in_max_rms"] = float(noise_max_mvrms) * 1e-3
    if power_max_mw is not None:
        kw["power_max_w"] = float(power_max_mw) * 1e-3
    if eye_height_min_mv is not None:
        kw["eye_height_min_v"] = float(eye_height_min_mv) * 1e-3
    if eye_width_min_ui is not None:
        kw["eye_width_min_ui"] = float(eye_width_min_ui)

    spec = dataclasses.replace(sp.EqualizerSpec(), **kw)
    if spec.fpeak_lo >= spec.fpeak_hi:
        raise ValueError(
            f"f_peak window is empty: lo {spec.fpeak_lo/1e9:.2f} GHz is not "
            f"below hi {spec.fpeak_hi/1e9:.2f} GHz")
    if spec.peaking_min_db >= spec.peaking_max_db:
        raise ValueError("peaking window is empty")
    return spec


def envelope_check(spec: sp.EqualizerSpec) -> dict:
    """Is this request inside the range the policy was actually trained on?

    Asking a learned policy for something outside its training distribution is
    not an error -- it will answer -- but the answer carries none of the
    measured 56.2 % reliability, and presenting it as though it did is the
    failure this function exists to prevent.
    """
    from eqrl.env_goal import GoalSpace
    g = GoalSpace.load()
    checks = {
        "peaking_target_db": (spec.peaking_target_db, g.peaking_target_db),
        "fpeak_lo": (spec.fpeak_lo, g.fpeak_lo),
        "fpeak_hi": (spec.fpeak_hi, g.fpeak_hi),
        "hd3_max_db": (spec.hd3_max_db, g.hd3_max_db),
        "power_max_w": (spec.power_max_w, g.power_max_w),
        "eye_height_min_v": (spec.eye_height_min_v, g.eye_height_min_v),
        "eye_width_min_ui": (spec.eye_width_min_ui, g.eye_width_min_ui),
    }
    outside = {k: {"asked": v, "trained_range": list(rng)}
               for k, (v, rng) in checks.items() if not rng[0] <= v <= rng[1]}
    return {"in_envelope": not outside, "outside": outside}


def known_designs_meeting(spec: sp.EqualizerSpec, limit: int = 3) -> dict:
    """Do any of the measured designs already satisfy this, at 4 corners?

    Reads the durable evaluation cache -- real SPICE measurements, not the
    surrogate. It is a NECESSARY-not-sufficient signal: those records are at the
    4 robust corners while acceptance needs all 20, so a design that clears this
    may still fail verification. It is reported that way.
    """
    from eqrl import cache
    from eqrl.spice import Metrics
    try:
        recs = cache.load(["collect", "goalstart", "goalstart2"])
    except FileNotFoundError:
        return {"checked": 0, "n_meeting": 0,
                "note": "no evaluation cache shipped with this copy"}
    corners = {c.name for c in pdk.robust_search_corners()}
    by: dict = {}
    for r in recs.records.values():
        md = r.get("metrics") or {}
        if md.get("ok", True) and r["corner"] in corners:
            by.setdefault(tuple(r["key"]), {})[r["corner"]] = md
    full = {k: v for k, v in by.items() if len(v) == len(corners)}
    good = []
    for key, per in full.items():
        worst = None
        for md in per.values():
            m = Metrics(**{k: v for k, v in md.items()
                           if k in Metrics.__dataclass_fields__})
            v = sp.min_margin(m, spec)
            worst = v if worst is None else min(worst, v)
        if worst is not None and worst >= 0:
            good.append((worst, key))
    good.sort(reverse=True)
    return {"checked": len(full), "n_meeting": len(good),
            "best_margin_4corner": (round(good[0][0], 4) if good else None),
            "note": ("measured at the 4 robust corners, not all 20 -- "
                     "necessary but not sufficient")}


# --------------------------------------------------------------------------
# the design flow
# --------------------------------------------------------------------------
def _require_spawn_safe_caller() -> None:
    """Fail fast if this is called from somewhere spawn will re-execute.

    The worker pool uses `spawn` (torch is loaded, and forking after torch
    deadlocks), and a spawned child RE-IMPORTS the caller's main module. If the
    caller runs `design_equalizer(...)` at module level with no
    `if __name__ == "__main__":` guard, every worker re-runs the entire design,
    recursively, and the run never finishes -- it just consumes the machine.

    Measured while building this: an unguarded caller reached 45 concurrent
    ngspice processes and was still spawning more three minutes later, having
    produced nothing. `env._pool_context` already warns about the related
    `python -c` case, but it then falls back to fork, which is the deadlock.
    Neither failure says what to do, and both look like "it is just slow".

    So this raises instead, naming the fix.
    """
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if path:
        return
    raise RuntimeError(
        "design_equalizer() must be called from a module with a __file__ "
        "(a .py script or the MCP server), not from `python -c` or a bare "
        "REPL: the SPICE worker pool uses spawn, which re-imports the caller's "
        "main module.\n\n"
        "  put the call in a .py file, inside:\n"
        "      if __name__ == \"__main__\":\n"
        "          design_equalizer(...)\n\n"
        "  or pass jobs=1 to run without a worker pool (much slower).")



def _scorecard(pvt: dict, spec: sp.EqualizerSpec) -> list[dict]:
    """Every brief spec against its limit at the WORST of the 20 corners.

    Margins are reported exactly as measured. Nothing is rounded toward
    passing -- a spec that misses by 0.1 % says so.
    """
    limits = {
        "peaking": f"{spec.peaking_min_db:.1f} - {spec.peaking_max_db:.1f} dB",
        "fpeak": f"{spec.fpeak_lo/1e9:.2f} - {spec.fpeak_hi/1e9:.2f} GHz",
        "hd3": f"< {spec.hd3_max_db:.1f} dBc",
        "noise": f"< {spec.vn_in_max_rms*1e3:.2f} mVrms",
        "power": f"< {spec.power_max_w*1e3:.1f} mW",
        "area": f"< {spec.area_max_m2*1e6:.3f} mm2",
        "eye_h": f"> {spec.eye_height_min_v*1e3:.0f} mV",
        "eye_w": f"> {spec.eye_width_min_ui:.2f} UI",
    }
    out = []
    for k in sp.BRIEF_SPECS:
        mg = pvt["margins"].get(k)
        out.append({"spec": k, "required": limits.get(k, ""),
                    "margin": (None if mg is None or mg != mg else round(mg, 4)),
                    "pass": bool(mg is not None and mg == mg and mg >= 0)})
    return out


def design_equalizer(peaking_db: float | None = None,
                     fpeak_lo_ghz: float | None = None,
                     fpeak_hi_ghz: float | None = None,
                     hd3_max_db: float | None = None,
                     noise_max_mvrms: float | None = None,
                     power_max_mw: float | None = None,
                     eye_height_min_mv: float | None = None,
                     eye_width_min_ui: float | None = None,
                     jobs: int | None = None,
                     make_plots: bool = True) -> dict:
    """Size an equalizer for this specification and prove it in SPICE.

    Propose -> screen the eye in SPICE -> verify at all 20 PVT corners, stopping
    at the first design that passes. Roughly 45 s: proposing costs no simulation
    at all, the screen is 16 evaluations, and verification is 20 more. The
    measurement harness that produced this project's headline numbers verifies
    every proposal to build a coverage curve -- designing ONE circuit does not.
    """
    from eqrl.spice import MeasCond, Simulator
    from eqrl.surrogate import load_latest
    from best_of_n import (SCREEN_CORNERS, propose_policy,
                           propose_surrogate_cem, screen_eye)
    from deploy_policy import verify

    _require_spawn_safe_caller()
    t0 = time.perf_counter()
    spec = build_spec(peaking_db, fpeak_lo_ghz, fpeak_hi_ghz, hd3_max_db,
                      noise_max_mvrms, power_max_mw, eye_height_min_mv,
                      eye_width_min_ui)
    env = envelope_check(spec)
    out_dir = _call_dir(spec)

    sur = load_latest()
    corners = pdk.robust_search_corners()
    cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp, "rc": c.rc}
           for c in corners]
    sim = Simulator(str(out_dir / "work"), use_channel=True, timeout=600,
                    meas=MeasCond.from_spec(spec))

    # The policy only where it was trained; a search everywhere else.
    order = ["policy", "surrogate_cem"] if env["in_envelope"] else ["surrogate_cem"]
    attempts, winner, winning_method = [], None, None

    for method in order:
        if method == "policy":
            from stable_baselines3 import SAC
            ck = ROOT / Path((ROOT / "models/policy/LATEST").read_text().strip())
            props = propose_policy(SAC.load(ck), spec, sur, cfs,
                                   N_PROPOSALS, 20)
        else:
            props = propose_surrogate_cem(spec, sur, cfs, N_PROPOSALS, 2000, 40)

        scr = screen_eye(props, spec, sim, jobs, list(SCREEN_CORNERS))
        survivors = scr["kept"]
        # best predicted margin first, so the likeliest design is verified first
        for pr in survivors:
            p = nl.denorm(list(np.clip(pr["u"], 0, 1)))
            mets = sur.predict_metrics([p] * len(cfs), cfs)
            pr["_pred"] = min(sp.min_margin(m, spec) for m in mets)
        survivors.sort(key=lambda x: -x["_pred"])

        verified = []
        for pr in survivors:
            pvt = verify(pr["u"], spec, sim, jobs, "mcp")
            verified.append({"u": pr["u"], "pvt": pvt})
            if pvt["all_pass_everywhere"]:
                winner, winning_method = verified[-1], method
                break
        attempts.append({"method": method, "proposed": len(props),
                         "survived_eye_screen": scr["n_kept"],
                         "verified_at_20_corners": len(verified)})
        if winner:
            break
        # nothing passed: keep the closest for the report rather than nothing
        if verified and winner is None:
            best = max(verified, key=lambda v: min(
                (x for x in v["pvt"]["margins"].values() if x == x),
                default=-9e9))
            if winner is None:
                winner, winning_method = best, method + " (did not pass)"

    if winner is None:
        return {"ok": False, "all_pass": False,
                "reason": "every proposal was rejected by the SPICE eye screen "
                          "before verification -- the requested eye width is "
                          "not reachable from any design proposed",
                "in_envelope": env["in_envelope"], "outside": env["outside"],
                "attempts": attempts,
                "wall_s": round(time.perf_counter() - t0, 1)}

    params = nl.denorm(list(np.clip(winner["u"], 0, 1)))
    pvt = winner["pvt"]
    result = {
        "ok": True,
        "artifact_dir": str(out_dir),
        # THE claim, and it is only ever True when 20 corners were simulated.
        "all_pass": bool(pvt["all_pass_everywhere"]),
        "method": winning_method,
        "in_envelope": env["in_envelope"],
        "outside_trained_range": env["outside"],
        "sized_parameters": {k: float(params[k]) for k in nl.PARAM_NAMES},
        "scorecard": _scorecard(pvt, spec),
        "specs_passed": f"{pvt['brief_specs_passed']}/8 at all "
                        f"{pvt['n_corners']} PVT corners",
        "binding_spec": pvt["binding"],
        "failed_specs": pvt["failed"],
        "attempts": attempts,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    if make_plots:
        result.update(_artifacts(params, spec, sim, winning_method, out_dir))
    return result


def _artifacts(params: dict, spec: sp.EqualizerSpec, sim,
               method: str = "", out_dir: Path | None = None) -> dict:
    """Eye diagram, AC response and the sized netlist, for the winning design.

    These are the proof. A scorecard is a set of numbers the caller has to take
    on trust; an open eye is a picture of the circuit working, and the netlist
    lets them re-run the whole thing themselves.
    """
    out_dir = out_dir or OUT_ROOT
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from eqrl.design import _export_deck, _export_eye_deck
    from eqrl.spice import MeasCond

    p = nl.CtleParams(**params)
    typ = pdk.typical_corner()
    meas = MeasCond.from_spec(spec)
    out: dict = {}

    # --- the netlist: the brief's literal deliverable ---------------------
    try:
        _export_deck(p, typ, out_dir / "design.sp", use_channel=True, meas=meas)
        _export_eye_deck(p, typ, out_dir / "design_eye.sp", use_channel=True,
                         meas=meas)
        # `design._export_deck` hardcodes "auto-sized by RL (CEM)" in the
        # header. When the SAC policy produced this design that line is simply
        # wrong, and a netlist is the artifact most likely to be read on its
        # own, away from any surrounding explanation. The exporter is a
        # byte-identical copy and is not edited; its OUTPUT is corrected here.
        for f in ("design.sp", "design_eye.sp"):
            path = out_dir / f
            if path.exists() and method:
                txt = path.read_text().replace(
                    "auto-sized by RL (CEM)", f"auto-sized by RL ({method})", 1)
                path.write_text(txt)
        out["netlist_path"] = str(out_dir / "design.sp")
        out["netlist"] = (out_dir / "design.sp").read_text()
    except Exception as e:                       # a plot failing must not lose
        out["netlist_error"] = f"{type(e).__name__}: {e}"   # the design itself

    # --- eye diagram from the closed-loop DFE transient -------------------
    try:
        r = sim.tran_eye(p, typ, n_bits=200, ui=meas.ui_s,
                         amp=meas.tx_swing_pp_v / 2)
        if r.get("ok"):
            t, v, e = r["t"], r["v"], r["eye"]
            m = t >= 20 * meas.ui_s
            t, v = t[m], v[m]
            n_ui = int((t[-1] - t[0]) // meas.ui_s)
            ph = np.linspace(-0.5, 0.5, 200)
            fig, ax = plt.subplots(figsize=(7, 4.2))
            for k in range(n_ui - 1):
                tk = t[0] + (k + 0.5 + ph) * meas.ui_s
                ax.plot(ph, np.interp(tk, t, v) * 1e3, color="#2b6cb0",
                        lw=0.5, alpha=0.25)
            ax.axhline(0, color="k", lw=0.8, alpha=0.5)
            ax.set_xlabel("phase (UI)")
            ax.set_ylabel("differential output (mV)")
            ax.set_title(
                f"Eye at the slicer, {1/meas.ui_s/1e9:.0f} Gb/s\n"
                f"height {e.height_v*1e3:.1f} mV "
                f"(need > {spec.eye_height_min_v*1e3:.0f})   "
                f"width {e.width_ui:.3f} UI "
                f"(need > {spec.eye_width_min_ui:.2f})")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(out_dir / "eye.png", dpi=120)
            plt.close(fig)
            out["eye_png"] = str(out_dir / "eye.png")
            out["eye_measured"] = {"height_mv": round(e.height_v * 1e3, 1),
                                   "width_ui": round(e.width_ui, 4)}
    except Exception as e:
        out["eye_error"] = f"{type(e).__name__}: {e}"

    # --- AC response: the peaking, and where it peaks ---------------------
    try:
        acr = sim.ac(p, typ)
        dat = Path(sim.workdir) / "ac.dat"
        if acr.get("ok") and dat.exists():
            d = np.loadtxt(dat)
            f, ctle = d[:, 0], d[:, 1]
            fig, ax = plt.subplots(figsize=(7, 4.2))
            ax.semilogx(f, ctle, lw=1.8, color="#2b6cb0", label="CTLE output")
            ax.axvspan(spec.fpeak_lo, spec.fpeak_hi, color="#48bb78",
                       alpha=0.15, label="required f_peak window")
            ax.axvline(spec.nyquist, color="k", ls="--", lw=0.9,
                       label=f"Nyquist {spec.nyquist/1e9:.1f} GHz")
            ax.set_xlabel("frequency (Hz)")
            ax.set_ylabel("gain (dB)")
            ax.set_title(f"CTLE response   peaking "
                         f"{acr.get('peaking_db', float('nan')):.2f} dB "
                         f"(need {spec.peaking_min_db:.0f}-"
                         f"{spec.peaking_max_db:.0f}), f_peak "
                         f"{acr.get('f_peak', float('nan'))/1e9:.3f} GHz")
            ax.grid(alpha=0.3, which="both")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(out_dir / "ac.png", dpi=120)
            plt.close(fig)
            out["ac_png"] = str(out_dir / "ac.png")
    except Exception as e:
        out["ac_error"] = f"{type(e).__name__}: {e}"

    return out


CAPABILITIES = """\
PCIe Gen2 equalizer sizing -- what this framework does, measured.

WHAT IT IS
  A 1-stage CTLE with source degeneration feeding a 1-tap DFE summing stage,
  in SKY130, driven through a lossy FR4-like channel. 11 device variables are
  sized automatically. Verification is ngspice at the brief's 20 PVT corners.

WHAT IT ACHIEVES
  The shipped reference design passes all 8 brief specs at all 20 PVT corners,
  found in 95 candidates. Tightest margin hd3 at +4.1 %.

  Two methods propose designs:
    SAC policy   56.2 % of proposals survive 20-corner verification;
                 8 designs proposed in 1.2 s with no simulation.
    CEM search   32.5 % per proposal, 41 s per 8, but no trained range limit.

WHAT IT CANNOT DO -- state these plainly if asked
  * Neither method solves every specification. Both plateau at 80 % of a
    held-out set of 10, and more proposals do not help.
  * The policy was trained on a bounded range of specs. Outside it the policy
    is extrapolating and its measured reliability does not apply; the server
    falls back to the search and says so.
  * Eye WIDTH is not predicted, it is measured in SPICE. The learned model
    reaches R2 0.57 on it against 0.90 for peaking and 0.97 for power, and
    three attempts to improve that were measured and failed.
  * A design is only ever reported as passing if all 20 corners were simulated.
"""


def explain_capabilities() -> str:
    """What this framework does, what it measured, and what it cannot do."""
    return CAPABILITIES


def check_spec(peaking_db: float | None = None,
               fpeak_lo_ghz: float | None = None,
               fpeak_hi_ghz: float | None = None,
               hd3_max_db: float | None = None,
               noise_max_mvrms: float | None = None,
               power_max_mw: float | None = None,
               eye_height_min_mv: float | None = None,
               eye_width_min_ui: float | None = None) -> dict:
    """Is this specification reachable? No simulation, under a second."""
    spec = build_spec(peaking_db, fpeak_lo_ghz, fpeak_hi_ghz, hd3_max_db,
                      noise_max_mvrms, power_max_mw, eye_height_min_mv,
                      eye_width_min_ui)
    env = envelope_check(spec)
    known = known_designs_meeting(spec)
    return {
        "in_trained_envelope": env["in_envelope"],
        "outside_trained_range": env["outside"],
        "known_designs_meeting_it": known,
        "method_that_would_be_used": ("SAC policy, then CEM as fallback"
                                      if env["in_envelope"]
                                      else "CEM search (request is outside the "
                                           "policy's trained range)"),
        "note": ("in_trained_envelope False does not mean impossible -- it "
                 "means the policy's measured 56.2 % reliability does not "
                 "apply and a search is used instead"),
    }
