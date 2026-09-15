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

#: What the collected measurements show is physically reachable, so a request
#: below either floor can be refused with evidence rather than a shrug.
#:
#: MEASURED over the 5 993 shipped records. These two are NOT in the policy's
#: goal space -- it never varied them -- so `envelope_check` cannot speak for
#: them, and for a long time nothing did: a request for 1 nV rms came back
#: "in_trained_envelope: true" and was routed to the policy. The mechanism
#: whose whole purpose is catching unreasonable requests skipped the parameter
#: that made that one unreasonable.
ACHIEVABLE = {
    "noise_mvrms_min": 0.159,      # p1 0.264, median 0.853, over 5993 records
    "area_mm2_min": 0.000053,      # median 0.000435
    "n_records": 5993,
}

#: The peaking band the netlist can express. `build_spec` widens a target by
#: +/-2.5 dB and clips to the brief's 3-12, so a target outside this produces
#: an empty window -- which used to be reported as "peaking window is empty",
#: describing a window the caller never passed (they passed a scalar).
PEAKING_TARGET_MIN, PEAKING_TARGET_MAX = 0.5, 14.5

#: Units for the eleven sized devices. The netlist header labels them; the tool
#: output shipped them bare, so "W 28.3553" could be micrometres, metres or
#: anything else. A number a reader cannot act on is barely a number.
PARAM_UNITS = {
    "W": "um", "L": "um", "RL": "ohm", "Rs": "ohm", "Cs": "F",
    "Itail": "A", "Wtail": "um", "Rsum": "ohm", "Wg1": "um",
    "Ig1": "A", "tap_pct": "%",
}

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
    _reject_impossible(peaking_db, hd3_max_db, noise_max_mvrms, power_max_mw,
                       eye_height_min_mv, eye_width_min_ui)
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
        # Name the legal band. "window is empty" describes a window the caller
        # never passed -- the argument is a scalar target -- and leaves them
        # with nothing to correct towards.
        raise ValueError(
            f"peaking_db={peaking_db} produces an empty band once widened by "
            f"+/-2.5 dB and clipped to the brief's 3-12 dB. Ask for a target "
            f"between {PEAKING_TARGET_MIN} and {PEAKING_TARGET_MAX} dB.")
    return spec


def _reject_impossible(peaking_db, hd3_max_db, noise_max_mvrms, power_max_mw,
                       eye_height_min_mv, eye_width_min_ui) -> None:
    """Refuse requests no circuit could satisfy, before spending minutes on one.

    Every one of these was accepted and sent to a real simulation. The hd3 case
    is the one that matters most: `hd3_max_db=+30` instead of -30 is the
    classic sign slip, and it reported MORE matching designs than asking for
    nothing at all -- so the typo made the request look EASIER, which is the
    direction a mistake must never fail in.
    """
    bad = []
    if power_max_mw is not None and power_max_mw <= 0:
        bad.append(f"power_max_mw={power_max_mw}: a power budget must be "
                   f"positive")
    if noise_max_mvrms is not None and noise_max_mvrms <= 0:
        bad.append(f"noise_max_mvrms={noise_max_mvrms}: a noise limit must be "
                   f"positive")
    if eye_height_min_mv is not None and eye_height_min_mv <= 0:
        bad.append(f"eye_height_min_mv={eye_height_min_mv}: an eye opening "
                   f"must be positive")
    if eye_width_min_ui is not None and not 0 < eye_width_min_ui < 1:
        bad.append(f"eye_width_min_ui={eye_width_min_ui}: an eye cannot be "
                   f"wider than the unit interval it lives in (0 < w < 1)")
    if hd3_max_db is not None and hd3_max_db >= 0:
        bad.append(f"hd3_max_db={hd3_max_db}: third-harmonic distortion is a "
                   f"suppression and is negative in dBc -- did you mean "
                   f"{-abs(hd3_max_db)}?")
    if peaking_db is not None and not (PEAKING_TARGET_MIN < peaking_db
                                       < PEAKING_TARGET_MAX):
        bad.append(f"peaking_db={peaking_db}: outside the reachable band "
                   f"{PEAKING_TARGET_MIN}-{PEAKING_TARGET_MAX} dB")
    if bad:
        raise ValueError("; ".join(bad))


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



def _fmt(x: float, unit: str = "", sig: int = 3) -> str:
    """A number that never moves when it is printed.

    One helper for the scorecard AND the plot titles, because they used
    different format strings and therefore disagreed: `:.0f` rendered a real
    5.5 - 10.5 dB window as "need 6-10" in the plot while the table beside it
    said 5.5 - 10.5, and `:.2f` rendered a 0.001 mVrms request as "< 0.00" -- a
    stated requirement of zero.

    A requirement printed loosely is a requirement misstated, and the plot is
    the part a human trusts most.
    """
    if x != x:
        return "n/a"
    a = abs(x)
    if a == 0:
        t = "0"
    elif a >= 1e5:
        t = f"{x:.{sig}g}"
    else:
        # Decimals from the MAGNITUDE, so small numbers keep their figures.
        # Deriving them from the integer part instead -- which the first
        # version did -- gave 0.001 two decimals and printed "0.00", the very
        # bug this helper exists to prevent.
        import math
        dec = max(0, sig - 1 - math.floor(math.log10(a)))
        t = f"{x:.{min(dec, 12)}f}".rstrip("0").rstrip(".") or "0"
    return f"{t} {unit}".strip()


def _peaking_requirement_text(spec: sp.EqualizerSpec) -> str:
    """The peaking window, rendered once, used by the scorecard and the plot."""
    return (f"{_fmt(spec.peaking_min_db)} - {_fmt(spec.peaking_max_db)} dB")


def _limits(spec: sp.EqualizerSpec) -> dict:
    """Every scored limit, as text, in the units the caller uses."""
    return {
        "peaking": _peaking_requirement_text(spec),
        "fpeak": f"{_fmt(spec.fpeak_lo/1e9)} - {_fmt(spec.fpeak_hi/1e9)} GHz",
        "hd3": f"< {_fmt(spec.hd3_max_db)} dBc",
        "noise": f"< {_fmt(spec.vn_in_max_rms*1e3)} mVrms",
        "power": f"< {_fmt(spec.power_max_w*1e3)} mW",
        "area": f"< {_fmt(spec.area_max_m2*1e6)} mm2",
        "eye_h": f"> {_fmt(spec.eye_height_min_v*1e3)} mV",
        "eye_w": f"> {_fmt(spec.eye_width_min_ui)} UI",
        "bias": "devices biased in saturation",
        "equalises": "eye openness improved vs the equaliser input",
    }


def applied_spec(spec: sp.EqualizerSpec, asked: dict) -> list[dict]:
    """Every limit actually in force, and whether the caller chose it.

    All eight parameters are optional and silently fill from the brief, so a
    request for "8 dB under 10 mW" carries six constraints nobody picked --
    invisible to the caller and unreportable by the model. Proven black-box
    before anyone read the source: `check_spec(power_max_mw=15)` returned
    output byte-identical to `check_spec()` with no arguments at all.

    Units are the REQUEST's. Echoing `power_max_w: 0.01` at someone who typed
    `power_max_mw=10` invites exactly the misreport it looks like.
    """
    limits = _limits(spec)
    arg_of = {"peaking": "peaking_db", "fpeak": "fpeak_lo_ghz/fpeak_hi_ghz",
              "hd3": "hd3_max_db", "noise": "noise_max_mvrms",
              "power": "power_max_mw", "area": None,
              "eye_h": "eye_height_min_mv", "eye_w": "eye_width_min_ui",
              "bias": None, "equalises": None}
    given = {k for k, v in asked.items() if v is not None}
    out = []
    for k, text in limits.items():
        a = arg_of.get(k)
        chosen = bool(a and any(part in given for part in a.split("/")))
        out.append({"limit": k, "value": text,
                    "source": "asked" if chosen else "default",
                    "parameter": a or "not settable"})
    return out


def achievable_check(spec: sp.EqualizerSpec) -> dict:
    """Noise and area against what has ever actually been measured.

    `envelope_check` cannot speak for these: the policy never varied them, so
    there is no trained range to compare against. For a long time nothing
    checked them at all, and a request for 1 nV rms came back
    `in_trained_envelope: true` and was routed to the policy -- the honesty
    mechanism skipping the one parameter that made the request impossible.

    This is a DIFFERENT kind of check and is reported as such: not "outside the
    range the policy learned" but "below anything this circuit has ever
    achieved, across N measurements".
    """
    out = {}
    n = ACHIEVABLE["n_records"]
    if spec.vn_in_max_rms * 1e3 < ACHIEVABLE["noise_mvrms_min"]:
        out["noise"] = (
            f"asked for < {_fmt(spec.vn_in_max_rms*1e3)} mVrms; the lowest "
            f"input-referred noise measured in {n} records is "
            f"{ACHIEVABLE['noise_mvrms_min']} mVrms")
    if spec.area_max_m2 * 1e6 < ACHIEVABLE["area_mm2_min"]:
        out["area"] = (
            f"asked for < {_fmt(spec.area_max_m2*1e6)} mm2; the smallest "
            f"measured in {n} records is {ACHIEVABLE['area_mm2_min']} mm2")
    return {"unreachable": out,
            "unchecked_by_envelope": ["noise", "area"],
            "note": ("noise and area are outside the policy's goal space, so "
                     "the trained-range check says nothing about them. They "
                     "are compared against measured achievability instead.")}


def _budget_result(best, attempts, elapsed, budget, env) -> dict:
    """What to return when the time budget runs out.

    A run that stops early must hand back what it has, unmistakably marked --
    and must NEVER report a pass, because a pass means twenty corners were
    simulated and this one did not finish. `all_pass` is hard-coded False here
    rather than read from anywhere.
    """
    out = {
        "ok": best is not None,
        "all_pass": False,
        "budget_exceeded": True,
        "budget_s": budget,
        "wall_s": round(elapsed, 1),
        "in_envelope": env["in_envelope"],
        "outside_trained_range": env["outside"],
        "method_attempted": [a["method"] for a in attempts],
        "attempts": attempts,
        "reason": (f"stopped after {elapsed:.0f} s against a {budget:.0f} s "
                   f"budget. This design was NOT verified at all 20 corners; "
                   f"re-run without a budget, or relax the specification."),
    }
    return out


def _scorecard(pvt: dict, spec: sp.EqualizerSpec) -> list[dict]:
    """Every condition `all_pass` depends on -- all TEN, not the brief's eight.

    The bug this shape fixes: `all_pass_everywhere` spans ten conditions,
    `brief_specs_passed` counts eight, and this rendered eight rows. A design
    failing only `bias` displayed as "DOES NOT PASS -- 8/8 at all 20 PVT
    corners" with eight rows all reading pass. The header contradicted the
    body, and the condition that actually failed was nowhere on screen.

    `bias` and `equalises` are INTERNAL validity checks rather than brief
    specs -- is the circuit biased at all, and is it equalising rather than
    merely amplifying -- so they are flagged, not hidden.
    """
    limits = _limits(spec)
    # WHICH FIELD DECIDES A ROW. `margins` comes from `design._worst_margins`,
    # which calls `sp.margins` -- the EIGHT brief specs. It has no entry for
    # `bias` or `equalises` at all. The pass state for all ten lives in
    # `failed`, aggregated from each corner's `score()["pass"]` dict, and that
    # is the same thing `all_pass_everywhere` is computed from.
    #
    # Reading a MISSING margin as a FAILED margin is what a real run did: a
    # design that passed everywhere printed `bias ... n/a FAIL` beside
    # "8/10 including internal checks" under an all_pass of True. So `failed`
    # decides the verdict whenever the verification supplied it, and the margin
    # is display only. Where it was not supplied -- a caller handing in a bare
    # margins dict -- the sign of the margin still decides, and an absent or
    # NaN margin is still not a pass.
    failed = pvt.get("failed")
    out = []
    for k in list(sp.BRIEF_SPECS) + list(sp.INTERNAL_CHECKS):
        mg = pvt["margins"].get(k)
        measured = mg is not None and mg == mg
        ok = (k not in failed) if failed is not None else (measured and mg >= 0)
        out.append({"spec": k, "required": limits.get(k, ""),
                    "internal": k in sp.INTERNAL_CHECKS,
                    "margin": (round(mg, 4) if measured else None),
                    "pass": bool(ok)})
    return out


def _specs_passed_line(pvt: dict, spec: sp.EqualizerSpec) -> str:
    """Both counts, together, so neither can be read as the other.

    "8/8" is true of the brief specs and misleading about the design: it was
    printed beside a DOES NOT PASS header for a design failing `bias`.
    """
    rows = _scorecard(pvt, spec)
    brief = [r for r in rows if not r["internal"]]
    nb = sum(1 for r in brief if r["pass"])
    na = sum(1 for r in rows if r["pass"])
    return (f"{nb}/{len(brief)} brief specs, {na}/{len(rows)} including "
            f"internal checks, at all {pvt.get('n_corners', 20)} PVT corners")


def design_equalizer(peaking_db: float | None = None,
                     fpeak_lo_ghz: float | None = None,
                     fpeak_hi_ghz: float | None = None,
                     hd3_max_db: float | None = None,
                     noise_max_mvrms: float | None = None,
                     power_max_mw: float | None = None,
                     eye_height_min_mv: float | None = None,
                     eye_width_min_ui: float | None = None,
                     jobs: int | None = None,
                     make_plots: bool = True,
                     budget_s: float = 150.0) -> dict:
    """Size an equalizer for this specification and prove it in SPICE.

    Propose -> screen the eye in SPICE -> verify at all 20 PVT corners, stopping
    at the first design that passes.

    TIMING, corrected. A SOLVABLE in-envelope request takes about 40 s: median
    37 s over ten measured specs, because proposing costs no simulation, the
    screen is 16 evaluations and verification is 20 more. An UNSOLVABLE one
    takes far longer -- both methods run, every screen survivor is verified,
    and a tester measured **421 s**. The "about 45 s" this docstring used to
    claim was the median of the solvable distribution reported as a universal,
    which is a mistake this project has now made three times.

    `budget_s` bounds it. On expiry the best design found so far is returned
    with `budget_exceeded: True` and `all_pass: False` -- never a pass, because
    a pass means twenty corners were simulated and a truncated run did not
    finish them.
    """
    from eqrl.spice import MeasCond, Simulator
    from eqrl.surrogate import load_latest
    from best_of_n import (SCREEN_CORNERS, propose_policy,
                           propose_surrogate_cem, screen_eye)
    from deploy_policy import verify

    _require_spawn_safe_caller()
    t0 = time.perf_counter()
    asked = {"peaking_db": peaking_db, "fpeak_lo_ghz": fpeak_lo_ghz,
             "fpeak_hi_ghz": fpeak_hi_ghz, "hd3_max_db": hd3_max_db,
             "noise_max_mvrms": noise_max_mvrms, "power_max_mw": power_max_mw,
             "eye_height_min_mv": eye_height_min_mv,
             "eye_width_min_ui": eye_width_min_ui}
    spec = build_spec(**asked)
    env = envelope_check(spec)
    reach = achievable_check(spec)
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
            if time.perf_counter() - t0 > budget_s:
                # Checked BEFORE each 20-corner verification, which is the only
                # place minutes accumulate. Stopping mid-verification would
                # leave a design checked at some corners and not others -- and
                # a partial corner sweep must never be mistaken for a sweep.
                best = max(verified, key=lambda v: min(
                    (x for x in v["pvt"]["margins"].values() if x == x),
                    default=-9e9)) if verified else None
                out = _budget_result(best, attempts + [{
                    "method": method, "proposed": len(props),
                    "survived_eye_screen": scr["n_kept"],
                    "verified_at_20_corners": len(verified)}],
                    time.perf_counter() - t0, budget_s, env)
                if best is not None:
                    bp = nl.denorm(list(np.clip(best["u"], 0, 1)))
                    out["sized_parameters"] = {k: float(bp[k])
                                               for k in nl.PARAM_NAMES}
                    out["scorecard"] = _scorecard(best["pvt"], spec)
                    out["failed_specs"] = best["pvt"]["failed"]
                    # This design WAS verified at all 20 corners -- the budget
                    # stopped the next attempt, not this verification -- so the
                    # counts are real. `all_pass` stays False regardless: the
                    # SEARCH did not finish, and the header must not read as
                    # though it had.
                    out["specs_passed"] = _specs_passed_line(best["pvt"], spec)
                    out["method"] = method
                out["applied_spec"] = applied_spec(spec, asked)
                return out
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
                # SAME key as the success path below. These were once
                # "outside" here and "outside_trained_range" there, so a caller
                # reading one got silence from the other -- and the silence
                # reads as "nothing was outside the trained range", which is
                # the opposite of the truth on exactly the requests where it
                # matters most.
                "in_envelope": env["in_envelope"],
                "outside_trained_range": env["outside"],
                # which method was tried, even though none of them won: the
                # top-level `method` is None when nothing is found, and a
                # caller must still be able to see the request was routed to
                # the search rather than the policy.
                "method_attempted": [a["method"] for a in attempts],
                "applied_spec": applied_spec(spec, asked),
                "unreachable": reach["unreachable"],
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
        "specs_passed": _specs_passed_line(pvt, spec),
        "applied_spec": applied_spec(spec, asked),
        "unreachable": reach["unreachable"],
        "unchecked_by_envelope": reach["unchecked_by_envelope"],
        "binding_spec": pvt["binding"],
        "failed_specs": pvt["failed"],
        "attempts": attempts,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    if make_plots:
        result.update(_artifacts(params, spec, sim, winning_method, out_dir))
        # After the plots and netlist, so it can embed them. One page the
        # caller opens instead of depending on the host to render images --
        # which Claude Desktop collapses and terminals mostly cannot do.
        try:
            result["report_path"] = str(_report_html(result, spec, out_dir))
        except Exception as e:                    # a report must never cost
            result["report_error"] = f"{type(e).__name__}: {e}"   # the design
    return result


def _report_html(result: dict, spec: sp.EqualizerSpec, out_dir: Path) -> Path:
    """One self-contained page with everything the design run produced.

    MCP image content is collapsed behind an expander in Claude Desktop and
    terminals mostly do not render images at all, so a caller shown "here are
    your diagrams" often sees nothing. Rather than depend on the host, the run
    writes a page: open one file, see the whole result.

    Self-contained on purpose -- the PNGs are embedded as data URIs, so moving
    or mailing the file cannot turn the proof into two broken-image boxes.

    It deliberately shows the things that are easy to leave out: all TEN
    conditions rather than the brief's eight, and which limits the caller
    actually chose versus which defaulted in behind them.
    """
    import base64
    import html

    def img(path_key: str, title: str) -> str:
        p = result.get(path_key)
        if not p or not Path(p).exists():
            return ""
        b = base64.b64encode(Path(p).read_bytes()).decode()
        return (f'<figure><img alt="{html.escape(title)}" '
                f'src="data:image/png;base64,{b}">'
                f'<figcaption>{html.escape(title)}</figcaption></figure>')

    ok = bool(result.get("all_pass"))

    def _margin(r) -> str:
        """The margin cell. A helper because nesting a format spec inside an
        f-string inside a generator is write-only code."""
        return "n/a" if r["margin"] is None else f"{r['margin']:+.4f}"

    rows = "".join(
        f'<tr class="{"pass" if r["pass"] else "fail"}">'
        f'<td>{html.escape(r["spec"])}'
        f'{" <span class=tag>internal</span>" if r.get("internal") else ""}</td>'
        f'<td>{html.escape(str(r["required"]))}</td>'
        f'<td class=num>{_margin(r)}</td>'
        f'<td>{"pass" if r["pass"] else "FAIL"}</td></tr>'
        for r in result.get("scorecard", []))

    applied = "".join(
        f'<tr><td>{html.escape(a["limit"])}</td>'
        f'<td>{html.escape(a["value"])}</td>'
        f'<td class="{a["source"]}">{a["source"]}</td></tr>'
        for a in result.get("applied_spec", []))

    devices = "".join(
        f'<tr><td>{html.escape(k)}</td><td class=num>{v:.6g}</td>'
        f'<td>{PARAM_UNITS.get(k, "")}</td></tr>'
        for k, v in (result.get("sized_parameters") or {}).items())

    env = ("inside the policy's trained range" if result.get("in_envelope")
           else "OUTSIDE the trained range -- a search was used, and the "
                "policy's measured reliability does not apply")

    page = f"""<!doctype html><meta charset=utf-8>
<title>PCIe Gen2 equalizer -- design report</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;margin:0;padding:2rem;
      max-width:1100px;margin-inline:auto;color:#1a202c;background:#fff}}
 h1{{font-size:1.5rem;margin:0 0 .25rem}}
 .verdict{{display:inline-block;padding:.35rem .8rem;border-radius:6px;
          font-weight:600;margin:.5rem 0 1rem}}
 .yes{{background:#c6f6d5;color:#22543d}} .no{{background:#fed7d7;color:#742a2a}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}}
 th,td{{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #e2e8f0}}
 th{{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:#4a5568}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 tr.fail td{{background:#fff5f5}}
 .tag{{font-size:.7rem;background:#e2e8f0;padding:.05rem .35rem;border-radius:3px}}
 .asked{{color:#2b6cb0;font-weight:600}} .default{{color:#718096}}
 figure{{margin:0}} img{{width:100%;border:1px solid #e2e8f0;border-radius:6px}}
 figcaption{{font-size:.85rem;color:#4a5568;margin-top:.3rem}}
 .plots{{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin:1rem 0 2rem}}
 pre{{background:#f7fafc;border:1px solid #e2e8f0;border-radius:6px;padding:1rem;
     overflow:auto;max-height:22rem;font-size:12px}}
 .meta{{color:#4a5568;font-size:.9rem}}
 @media(max-width:800px){{.plots{{grid-template-columns:1fr}}}}
</style>
<h1>PCIe Gen2 equalizer &mdash; design report</h1>
<div class="verdict {'yes' if ok else 'no'}">
  {'PASSES' if ok else 'DOES NOT PASS'} &mdash; {html.escape(str(result.get('specs_passed', '')))}
</div>
<p class=meta>Designed by <b>{html.escape(str(result.get('method', '?')))}</b>,
   {html.escape(env)}. Wall clock {result.get('wall_s', '?')} s.</p>

<h2>Scorecard</h2>
<p class=meta>Every condition <code>all_pass</code> depends on. The two marked
   <span class=tag>internal</span> are validity checks rather than brief specs:
   <code>bias</code> asks whether the devices are biased in saturation, and
   <code>equalises</code> whether the eye is more open at the slicer than at the
   input &mdash; a design can enlarge the eye with gain alone without equalising
   anything.</p>
<table><tr><th>condition</th><th>required</th><th>margin</th><th></th></tr>
{rows}</table>

<h2>Proof</h2>
<div class=plots>{img('eye_png', 'Eye diagram at the slicer, closed-loop DFE')}
{img('ac_png', 'CTLE response, with the required f_peak window shaded')}</div>

<h2>Sized devices</h2>
<table><tr><th>parameter</th><th>value</th><th>unit</th></tr>{devices}</table>

<h2>The specification actually applied</h2>
<p class=meta>Unspecified limits fall back to the PCIe brief. They are listed
   here because a constraint nobody chose still shapes the answer.</p>
<table><tr><th>limit</th><th>value</th><th>source</th></tr>{applied}</table>

<h2>Netlist</h2>
<pre>{html.escape(result.get('netlist', '(not generated)'))}</pre>
"""
    out = out_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


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
                f"height {_fmt(e.height_v*1e3)} mV "
                f"(need > {_fmt(spec.eye_height_min_v*1e3)})   "
                f"width {_fmt(e.width_ui)} UI "
                f"(need > {_fmt(spec.eye_width_min_ui)})")
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
            # The SAME requirement text the scorecard prints. These were two
            # different format strings and therefore disagreed: the plot said
            # "need 6-10" where the table said 5.5 - 10.5.
            ax.set_title(f"CTLE response   peaking "
                         f"{_fmt(acr.get('peaking_db', float('nan')))} dB "
                         f"(need {_peaking_requirement_text(spec)}), f_peak "
                         f"{_fmt(acr.get('f_peak', float('nan'))/1e9)} GHz")
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

  A design is judged on TEN conditions, not eight. The eight brief specs
  (peaking, f_peak, hd3, noise, power, area, eye height, eye width) plus two
  INTERNAL validity checks that decide whether the result means anything:
    bias       are the devices biased in saturation? An unbiased circuit can
               post good-looking small-signal numbers that mean nothing.
    equalises  is the eye more OPEN at the slicer than at the input? The
               summing stage's gain is a free variable, so a design can make
               the eye bigger without equalising anything. Openness -- opening
               as a fraction of the swing at the same node -- is invariant
               under that gain, and is what gets scored.
  `all_pass` covers all ten. The scorecard shows all ten. A count of "8/8"
  refers only to the brief specs and is reported alongside the ten, never
  instead of it.

WHAT IT ACHIEVES
  The shipped reference design passes all 8 brief specs at all 20 PVT corners,
  found in 95 candidates. Tightest margin hd3 at +4.1 %.

  Two methods propose designs. Both figures below are over n = 10 held-out
  specs -- about +/-15 % at one sigma, so treat them as "roughly twice" rather
  than as three significant figures:
    SAC policy   56.2 % of proposals survive 20-corner verification;
                 8 designs proposed in 1.2 s with no simulation.
    CEM search   32.5 % per proposal, 41 s per 8, but no trained range limit.

HOW LONG A DESIGN TAKES
  A solvable, in-envelope request: about 40 s (median 37 s over ten measured
  specs). An UNSOLVABLE one is far slower -- both methods run and every screen
  survivor is verified at 20 corners -- and has been measured at 421 s. Use
  check_spec first if a request looks ambitious; it costs nothing. A run that
  hits its time budget returns `budget_exceeded: True` and never a pass.

WHAT IT CANNOT DO -- state these plainly if asked
  * Neither method solves every specification. Both plateau at 80 % of a
    held-out set of 10 (so 8 of 10 -- and at n = 10 that is +/-15 % at one
    sigma). More proposals do not help: both were extended to 32.
    A separate figure, feasible_frac 0.504, measures something DIFFERENT --
    the share of randomly SAMPLED goals that any collected design satisfies --
    and is not in conflict with the 80 %.
  * The policy was trained on a bounded range of specs. Outside it the policy
    is extrapolating and its measured reliability does not apply; the server
    falls back to the search and says so.
  * Eye WIDTH is not predicted, it is measured in SPICE. The learned model
    reaches R2 0.57 on it against 0.90 for peaking and 0.97 for power, and
    three attempts to improve that were measured and failed.
  * A design is only ever reported as passing if all 20 corners were simulated.
"""


def trained_range() -> dict:
    """The goal space the SAC policy was trained on, in human units.

    Served as a RESOURCE as well as being checked inside every call, so a model
    can reason about whether a request is reachable before spending 45 seconds
    finding out. `envelope_check` is the authority; this is the same numbers in
    a form worth reading.
    """
    from eqrl.env_goal import GoalSpace
    g = GoalSpace.load()
    return {
        "note": ("The RL policy learned this range. A request outside it is "
                 "not impossible -- the server falls back to the CEM search -- "
                 "but the policy's measured 56.2 % per-proposal reliability "
                 "does not apply there."),
        "peaking_db": list(g.peaking_target_db),
        "fpeak_lo_ghz": [x / 1e9 for x in g.fpeak_lo],
        "fpeak_hi_ghz": [x / 1e9 for x in g.fpeak_hi],
        "hd3_max_db": list(g.hd3_max_db),
        "power_max_mw": [x * 1e3 for x in g.power_max_w],
        "eye_height_min_mv": [x * 1e3 for x in g.eye_height_min_v],
        "eye_width_min_ui": list(g.eye_width_min_ui),
        "fraction_of_sampled_goals_with_a_known_solution": g.feasible_frac,
    }


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
    asked = {"peaking_db": peaking_db, "fpeak_lo_ghz": fpeak_lo_ghz,
             "fpeak_hi_ghz": fpeak_hi_ghz, "hd3_max_db": hd3_max_db,
             "noise_max_mvrms": noise_max_mvrms, "power_max_mw": power_max_mw,
             "eye_height_min_mv": eye_height_min_mv,
             "eye_width_min_ui": eye_width_min_ui}
    spec = build_spec(**asked)
    env = envelope_check(spec)
    reach = achievable_check(spec)
    known = known_designs_meeting(spec)
    # A request the policy's range accepts but physics does not is NOT "in
    # range" in any sense a caller cares about.
    in_env = bool(env["in_envelope"]) and not reach["unreachable"]
    return {
        "in_trained_envelope": in_env,
        "applied_spec": applied_spec(spec, asked),
        "unreachable": reach["unreachable"],
        "unchecked_by_envelope": reach["unchecked_by_envelope"],
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
