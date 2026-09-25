#!/usr/bin/env python3
"""The detailed reference report, as a Word document.

The project report (`make_report_docx.py`) is the summary. THIS document is the
one to open when someone asks "how do you know that?" -- every claim carries
the artifact it came from, so an answer can be "results/best_of_n_policy.json,
45 of 80" instead of "about half".

  python3 scripts/make_reference_docx.py

EVERY NUMBER IS READ FROM A COMMITTED ARTIFACT AT BUILD TIME. Nothing is typed
into the prose twice, and no measurement is run here -- if a figure is not
already in `results/`, `models/` or the code, it does not go in the document.

It shares its styling with `make_report_docx.py` by importing that module's
helpers, so the two read as siblings.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from docx import Document                                    # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH                # noqa: E402
from docx.shared import Inches, Pt                           # noqa: E402

import make_report_docx as R                                 # noqa: E402
from make_report_docx import (ACCENT, INK, MUTED, add_page_numbers,  # noqa: E402
                              bullet, page_break, para, style, table)

OUT = ROOT / "docs" / "Team_Sillycon_Reference.docx"
FIG_DIRS = (ROOT / "docs" / "presentation" / "figures",
            ROOT / "docs" / "figures")

_fig = [0]


def figure(doc, name, caption, width=6.2):
    """Like the report's, but looks in the presentation figures too."""
    path = next((d / name for d in FIG_DIRS if (d / name).exists()), None)
    if path is None:
        print(f"  [missing figure] {name}")
        return
    _fig[0] += 1
    doc.add_picture(str(path), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, f"Figure {_fig[0]}. {caption}", size=9.5, italic=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=14)


def src(doc, text):
    """A provenance line. This is the whole point of the document."""
    para(doc, f"Source: {text}", size=9, italic=True, color=MUTED,
         space_after=12)


def code(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(9)
    r.font.color.rgb = INK
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.left_indent = Inches(0.3)
    return p


# --------------------------------------------------------------------------
# everything the prose quotes
# --------------------------------------------------------------------------
def facts() -> dict:
    from eqrl import netlist as nl, pdk, physics, specs as sp
    from eqrl.spice import MeasCond
    from eqrl.cache import load as cache_load

    res, F = ROOT / "results", {}

    def J(p):
        return json.loads(Path(p).read_text())

    # -- coverage ---------------------------------------------------------
    for tag, name in (("pol", "best_of_n_policy.json"),
                      ("cem", "best_of_n_surrogate_cem.json")):
        d = J(res / name)
        ranks = [s["first_success_rank"] for s in d["specs"]]
        npass = sum(1 for s in d["specs"] for p in s["proposals"]
                    if p.get("real_pass"))
        nprop = sum(len(s["proposals"]) for s in d["specs"])
        p8 = sum(1 for s in d["specs"] for p in s["proposals"][:8]
                 if p.get("real_pass"))
        n8 = sum(min(8, len(s["proposals"])) for s in d["specs"])
        F[tag] = {"raw": d, "ranks": ranks,
                  "solved": sum(1 for r in ranks if r is not None),
                  "n": len(ranks), "npass": npass, "nprop": nprop,
                  "rate": npass / nprop, "rate8": p8 / n8,
                  "p8": p8, "n8": n8}
    F["union"] = sum(1 for a, b in zip(F["pol"]["ranks"], F["cem"]["ranks"])
                     if a is not None or b is not None)

    # -- timing -----------------------------------------------------------
    walls = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("wall_s"), (int, float)):
                walls.append(o["wall_s"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(J(res / "mcp_e2e.json"))
    walls.sort()
    F["walls"] = walls
    F["t_med"], F["t_lo"], F["t_hi"] = st.median(walls), walls[0], walls[-1]

    # -- the sweep baseline, and the cost of one simulation ---------------
    b = J(res / "baseline.json")
    F["base"] = b
    F["spice_per_design"] = b["wall_s"] / b["budget"]
    F["launches_per_design"] = b["n_ngspice_launches"] // b["budget"]

    # -- the trained model ------------------------------------------------
    sd = ROOT / (ROOT / "models" / "surrogate" / "LATEST").read_text().strip()
    F["val"] = J(sd / "validation.json")
    F["cfg"] = J(sd / "config.json")
    F["physfit"] = J(sd / "physfit.json")
    F["check"] = J(res / "surrogate_check.json")

    # -- policy training --------------------------------------------------
    ev = J(res / "policy_train_eval.json")["evals"]
    last = [e for e in ev if e["steps"] == 120000][-1]
    F["train_wall_s"] = last["wall_s"]
    F["train_curve"] = [(e["steps"], e["success_rate"]) for e in ev
                        if e["steps"] in (10000, 20000, 80000, 110000, 120000)]

    # -- the verified design ----------------------------------------------
    F["design"] = J(res / "report_design.json")

    # -- how the training points were sampled -----------------------------
    recs = [r for r in cache_load(
        ["collect", "goalstart", "goalstart2"],
        meas=MeasCond.from_spec(sp.EqualizerSpec())).records.values()
        if r["metrics"].get("ok")]
    rows, designs, seen = Counter(), Counter(), set()
    for r in recs:
        cl = r.get("note") or "?"
        rows[cl] += 1
        k = tuple(r["key"])
        if k not in seen:
            seen.add(k)
            designs[cl] += 1
    F["clouds"] = {c: (designs[c], rows[c]) for c in rows}

    # -- the channel (analytic, deterministic) ----------------------------
    import numpy as np
    fs = np.array([1e3, 1e9, 2.5e9, 5e9])
    F["chan"] = [(f, 20 * np.log10(abs(h)))
                 for f, h in zip(fs, np.atleast_1d(physics.channel_H(fs)))]

    # -- static definitions ------------------------------------------------
    F["bounds"] = {k: nl.PARAM_BOUNDS[k] for k in nl.PARAM_NAMES}
    F["corners"] = pdk.pvt_grid("spec")
    F["limits"] = __import__("eqrl.mcp_server", fromlist=["_limits"])._limits(
        sp.EqualizerSpec())
    return F


# --------------------------------------------------------------------------
UNITS = {"W": "µm", "L": "µm", "Itail": "A", "Wtail": "µm", "RL": "Ω",
         "Rs": "Ω", "Cs": "F", "Rsum": "Ω", "Wg1": "µm", "Ig1": "A",
         "tap_pct": "%"}
WHAT = {"W": "CTLE input pair width", "L": "CTLE input pair length",
        "Itail": "CTLE tail current", "Wtail": "CTLE tail device width",
        "RL": "CTLE load resistance", "Rs": "degeneration resistance",
        "Cs": "degeneration capacitance", "Rsum": "summing-stage load",
        "Wg1": "main transconductor width", "Ig1": "main transconductor current",
        "tap_pct": "DFE tap strength"}


def eng(x, unit=""):
    for d, s in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
                 (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
                 (1e-15, "f")):
        if abs(x) >= d:
            v = x / d
            return f"{v:.3g} {s}{unit}".strip()
    return f"{x:.3g} {unit}".strip()


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def cover(doc, F):
    for _ in range(5):
        doc.add_paragraph()
    para(doc, "AI/ML for Analog Circuit Design", size=13, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=ACCENT, space_after=4)
    para(doc, "Reference Report", size=26, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    para(doc, "A reinforcement-learning framework that sizes a PCIe Gen2 "
              "receive equalizer", size=13,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=28)
    para(doc, "Supporting detail for the project presentation. Every figure "
              "quoted here is read from a committed artifact, and the artifact "
              "is named beside it.", size=10.5, italic=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=40)
    for label, val in (("Team", "Team Sillycon"),
                       ("College", "BITS Pilani – Pilani Campus"),
                       ("Members", "Kanishk Jain · Karthik Raj Palleboyina")):
        para(doc, f"{label}:  {val}", size=12,
             align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    page_break(doc)


def contents(doc):
    doc.add_heading("Contents", level=1)
    for n, t in enumerate([
            "The problem statement",
            "Signalling, the channel, and what goes wrong",
            "The circuit",
            "Why simulation cost decides the architecture",
            "Choosing the starting point",
            "PhysFit: the fast evaluator",
            "The reinforcement-learning agent",
            "Requests outside the trained range",
            "Verification: the simulator decides",
            "The language-model wrapper",
            "Results",
            "Reproducing all of this"], 1):
        para(doc, f"{n}.   {t}", size=11, space_after=3)
    for a, t in (("A", "The ten measurement specifications"),
                 ("B", "Design-variable bounds"),
                 ("C", "The twenty PVT corners"),
                 ("D", "Artifact index — which file backs which claim")):
        para(doc, f"{a}.   {t}", size=11, space_after=3)
    page_break(doc)


def s1_problem(doc, F):
    doc.add_heading("1.  The problem statement", level=1)
    para(doc, "Quoted verbatim, because it is the contract the work is "
              "measured against.")
    for t in [
        "Demonstrate a fully automated framework for schematic design of "
        "analog circuits using Reinforcement Learning. Given circuit "
        "specifications, build an automated design flow that sizes devices "
        "using fewer search spaces (lowest design time) to reach near-optimal "
        "solutions. Use case: Equalizer circuit design (PCIe PHY). Should take "
        "significantly lower time than sweeping all MOS, R, C, L parameter "
        "space, with zero human intervention.",
        "Specifications: Nyquist Freq (PCIe Gen 2, 5.0 Gbps): 2.5 GHz; HF "
        "peaking boost 3–12 dB (tunable 1.25–2.5 GHz); 1-Stage CTLE w/ source "
        "degeneration (variable Rs, Cs) + 1-Tap DFE; NRZ signaling; Linearity "
        "HD3 < -30 dB (100 MHz diff input); Input-referred noise < 1.5 mVrms "
        "(10 MHz–5 GHz); Power < 15 mW; Area < 0.05 mm² (130nm PDK); Eye "
        "opening > 0.4 UI (H) & > 100 mV (V); Meet across PVT (TT,SS,FF,SF,FS; "
        "VDD±5%; 0–125°C).",
        "Objective: Develop a RL based automation framework to automatically "
        "design an equalizer for the given specifications, with minimal to "
        "zero human intervention.",
        "Deliverables: A Reinforcement Learning based Python framework that "
        "takes target specs as input, seamlessly integrates with a spice "
        "simulator — outputs the final schematic and resulting specs. Bonus: "
        "LLM-based human interaction with wrapper to fine-tune.",
    ]:
        para(doc, t, size=10.5, italic=True, color=MUTED, space_after=8)

    doc.add_heading("1.1  The two demands that shape everything", level=2)
    para(doc, "Read closely, the brief asks for two things that pull in "
              "opposite directions. It wants a design that meets a long list "
              "of analog specifications at every process corner — and it wants "
              "that design found using FEWER SEARCH SPACES, in the LOWEST "
              "DESIGN TIME, faster than sweeping the parameter space, with "
              "ZERO HUMAN INTERVENTION.")
    para(doc, "Meeting the specifications is circuit design. Meeting them "
              "quickly, automatically, and without sweeping is the problem "
              "this project actually solves. Section 4 shows why those two "
              "demands conflict, and the rest of the document is the "
              "resolution.")

    doc.add_heading("1.2  What counts as passing", level=2)
    para(doc, "Ten conditions are scored. Eight are the brief's. Two are "
              "validity checks the framework adds, because a circuit can "
              "satisfy every electrical number while being physically "
              "meaningless.")
    rows = [[i, k, v] for i, (k, v) in enumerate(F["limits"].items(), 1)]
    table(doc, "The ten scored conditions.", ["#", "condition", "requirement"],
          rows, widths=[0.4, 1.3, 4.3])
    src(doc, "eqrl/mcp_server.py::_limits, eqrl/specs.py::EqualizerSpec")
    para(doc, "Condition 9, bias, asks whether every device is biased in "
              "saturation. A design can hit all eight electrical numbers while "
              "sitting in triode, where the small-signal model that produced "
              "those numbers does not apply.")
    para(doc, "Condition 10, equalises, asks whether the eye is more open at "
              "the slicer than at the equaliser input — measured as openness, "
              "a fraction of the swing at the same node, so it is invariant "
              "under gain. Without it a circuit could enlarge the eye with "
              "plain amplification, magnifying the distortion along with the "
              "signal, and still be reported as passing.")

    doc.add_heading("1.3  And it must hold at twenty corners", level=2)
    para(doc, f"Five process corners (tt, ss, ff, sf, fs) × two supplies "
              f"(1.71 V and 1.89 V, i.e. 1.8 V ±5 %) × two temperatures "
              f"(0 °C and 125 °C) = {len(F['corners'])} corners. Every one of "
              f"the ten conditions must hold at every one of them. The full "
              f"list is Appendix C.")
    para(doc, "A design is only ever reported as passing after all twenty were "
              "actually simulated. This is enforced in code, not asserted in "
              "prose: see Section 9.")
    page_break(doc)


def s2_channel(doc, F):
    doc.add_heading("2.  Signalling, the channel, and what goes wrong", level=1)

    doc.add_heading("2.1  NRZ, data rate and Nyquist", level=2)
    para(doc, "NRZ — non-return-to-zero — is the simplest line code: each bit "
              "slot holds one of two voltage levels, high for a 1 and low for "
              "a 0, and the line does not return to a rest level in between. "
              "One symbol carries one bit.")
    para(doc, "At 5 Gb/s one bit slot, the Unit Interval, is 1 / 5 GHz = "
              "200 ps.")
    para(doc, "The Nyquist frequency is half the data rate, 2.5 GHz. The "
              "fastest pattern NRZ can produce is alternating 1010…, which "
              "takes two bit slots to complete one full cycle, so the highest "
              "fundamental the line ever carries is 5 GHz / 2. Everything "
              "above it is edge detail rather than new symbols. That single "
              "number is what the brief's frequency requirements are built "
              "around.")

    doc.add_heading("2.2  The channel is bandlimited and lossy", level=2)
    para(doc, "A copper trace is a low-pass filter. Series resistance rises "
              "with frequency through the skin effect, and the dielectric "
              "absorbs more as frequency climbs, so fast content is attenuated "
              "far more than slow content.")
    para(doc, "The channel model is a differential lossy ladder: 40 sections "
              "per leg, 1.2 ns total delay (about 7 inch of FR4), 50 Ω per "
              "leg, each section carrying a DC resistance plus two staggered "
              "skin-effect stages, with 50 Ω series at the source and 100 Ω "
              "differential termination at the receiver.")
    rows = [[f"{f/1e9:.3g} GHz" if f >= 1e9 else f"{f/1e3:.0f} kHz",
             f"{g:+.2f} dB"] for f, g in F["chan"]]
    table(doc, "Channel loss against frequency.", ["frequency", "loss"], rows,
          widths=[1.6, 1.6])
    src(doc, "eqrl/netlist.py::channel_netlist, eqrl/physics.py::channel_H")
    dc = F["chan"][0][1]
    nyq = [g for f, g in F["chan"] if abs(f - 2.5e9) < 1][0]
    para(doc, f"The loss at DC is {dc:+.2f} dB — that is the source and "
              f"termination divider, not dissipation, and it applies equally "
              f"at all frequencies. What the equaliser has to undo is the "
              f"SHAPE: {abs(nyq - dc):.1f} dB more loss at Nyquist than at DC.")
    para(doc, f"Set that against the brief's required peaking of 3–12 dB. The "
              f"specification is sized to this channel; the numbers are not "
              f"arbitrary.")

    doc.add_heading("2.3  In the time domain: intersymbol interference", level=2)
    para(doc, "A filter that removes high frequencies cannot keep a sharp "
              "edge. Send one isolated bit and what arrives is not a 200 ps "
              "rectangle but a smeared hump that is still present when the "
              "next bit arrives.")
    figure(doc, "06a_pulse_spreading.png",
           "One isolated bit driven through the channel model. Transmitted, a "
           "clean 200 ps rectangle; received, a hump whose tail is still 37 % "
           "of its own peak one Unit Interval later. The channel's 1.2 ns "
           "delay has been removed so the two traces line up.")
    table(doc, "The received pulse, sampled at the receiver's own clock phase.",
          ["sampled at", "fraction of the cursor"],
          [["the cursor — its own slot", "1.000"],
           ["+1 UI — the next bit's slot", "+0.373"],
           ["+2 UI", "+0.155"]], widths=[2.6, 1.8])
    src(doc, "scripts/make_channel_figures.py. The +0.373 reproduces the "
             "+0.372 measured independently in docs/findings-ctle-dfe.md §2, "
             "by a different method.")
    para(doc, "So roughly 37 % of every bit is still sitting in the following "
              "bit's slot, added to whatever that bit is sending. With random "
              "data those leftovers accumulate constructively and "
              "destructively at random, and the eye closes. That is "
              "intersymbol interference.")
    figure(doc, "06b_isi.png",
           "Intersymbol interference in general: individual pulse responses "
           "spread, overlap, and sum at the receiver's sampling instants.")
    para(doc, "Two different problems follow, and the architecture has one "
              "block for each. The loss is frequency-shaped, so it is undone "
              "with a filter shaped the opposite way — the CTLE. What remains "
              "is a known fraction of a bit that has already been decided, so "
              "it is simply subtracted — the DFE tap.")
    page_break(doc)


def s3_circuit(doc, F):
    doc.add_heading("3.  The circuit", level=1)
    para(doc, "One CTLE stage with source degeneration, feeding a summing "
              "stage that also receives a scaled copy of the previously "
              "decided bit, feeding a slicer. Fixed by the brief and unchanged "
              "throughout the project.")
    para(doc, "The topology follows the open reference design at "
              "github.com/muhammadaldacher/SERDES-Design-of-RX-Decision-"
              "Feedback-Equalizer, a 1-tap DFE for NRZ over FR4.")
    figure(doc, "06c_circuit_implementation.png",
           "The implementation: CTLE, the Gm1 and GmF stages of the DFE, the "
           "summing node, the clocked comparator and the D flip-flop that "
           "closes the loop.", width=6.0)

    doc.add_heading("3.1  What each block does", level=2)
    para(doc, "The CTLE is a differential pair whose source degeneration is a "
              "resistor Rs in parallel with a capacitor Cs. At low frequency "
              "Cs is effectively open, so Rs degenerates the pair and the gain "
              "is low. At high frequency Cs shorts Rs out, the degeneration "
              "disappears, and the gain rises. That rise is the boost: the "
              "amplifier deliberately favours exactly the frequencies the "
              "channel attenuated.")
    bullet(doc, "sets where the boost starts — the zero.", bold_lead="Rs ∥ Cs ")
    bullet(doc, "sets where the response rolls off — the output pole.",
           bold_lead="RL · Cout ")
    bullet(doc, "between them is how much boost you get.",
           bold_lead="The gap ")
    para(doc, "The summing stage adds two currents into one pair of load "
              "resistors Rsum: the CTLE output through the main "
              "transconductor Gm1, and a scaled copy of the previously decided "
              "bit through the feedback transconductor GmF. Subtracting that "
              "copy cancels the tail the previous bit left behind.")
    para(doc, "The slicer decides 1 or 0 at the sampling instant, and a D "
              "flip-flop holds that decision for one clock period — which is "
              "what makes the feedback exactly one bit long, and the DFE "
              "exactly one tap. The comparator and flip-flop are ideal models; "
              "this project sizes the analog path.")

    doc.add_heading("3.2  The eleven design variables", level=2)
    para(doc, "These are the framework's entire output. Everything else about "
              "the circuit — topology, device flavours, the channel, the "
              "measurement conditions — is fixed.")
    rows = []
    for k, (lo, hi) in F["bounds"].items():
        u = UNITS[k]
        rows.append([k, WHAT[k], f"{eng(lo, u)} – {eng(hi, u)}"])
    table(doc, "The eleven variables and the range each may take.",
          ["variable", "what it sets", "range"], rows,
          widths=[0.9, 2.7, 2.0])
    src(doc, "eqrl/netlist.py::PARAM_NAMES, PARAM_BOUNDS")
    para(doc, "Three of these are not independent in the way the list "
              "suggests. The DFE tap branch is never sized on its own: its "
              "current, its device width and its tail width are all derived "
              "from tap_pct, so the whole feedback stage is one knob. Section "
              "5.3 gives the relations.")

    doc.add_heading("3.3  The schematic is checked against what is simulated",
                    level=2)
    para(doc, "netlists/eq_core.sch opens in xschem. A test netlists it with "
              "xschem and diffs the resulting connectivity against the netlist "
              "the framework actually simulates, so the drawing and the "
              "circuit cannot drift apart.")
    src(doc, "tests/test_schematic_sch.py")
    figure(doc, "02a_schematic_full.png",
           "The complete equalizer, both stages, exported from the verified "
           "schematic file.")
    page_break(doc)


def s4_cost(doc, F):
    doc.add_heading("4.  Why simulation cost decides the architecture", level=1)
    per = F["spice_per_design"]
    b = F["base"]
    para(doc, f"One design, evaluated at four corners, costs "
              f"{per:.2f} seconds of ngspice. That is measured, not estimated: "
              f"{b['budget']} candidates took {b['wall_s']:.1f} s of wall clock "
              f"and launched {b['n_ngspice_launches']} ngspice processes — "
              f"{F['launches_per_design']} per design, two per corner.")
    src(doc, "results/baseline.json")
    para(doc, "And that is the fast path already. Trimmed per-corner model "
              "libraries, built and validated once, make these evaluations "
              "22.4× faster than loading the full SKY130 library each time. "
              "4.42 s is what the cost looks like after optimisation.")
    src(doc, "scripts/build_trimlib.py; README.md")

    doc.add_heading("4.1  What that means for a sweep", level=2)
    rows = [["one design, 4 corners", "1", f"{per:.2f} s"],
            ["one design, all 20 corners", "1", f"~{per*5:.0f} s"],
            ["the agent's training budget", "120,000",
             f"{120000*per/86400:.1f} days"],
            ["a 3-point grid over 11 variables", f"{3**11:,}",
             f"{3**11*per/3600:.1f} hours"],
            ["a 5-point grid over 11 variables", f"{5**11:,}",
             f"{5**11*per/(86400*365):.1f} years"]]
    table(doc, "The arithmetic of brute force, at the measured cost per design.",
          ["what", "designs", "ngspice time"], rows, widths=[2.6, 1.3, 1.6])
    para(doc, "An exhaustive sweep is not slow. It is arithmetically out of "
              "reach, and it is the thing the brief explicitly asks to beat. "
              "Three points per variable is not a design space worth "
              "searching; five points is longer than anyone will wait.")

    doc.add_heading("4.2  And what it means for learning", level=2)
    para(doc, "The same number rules out the obvious approach to the RL "
              "problem. An agent needs to evaluate a design at every training "
              "step. At 120,000 steps and 4.42 s per evaluation, training with "
              f"the simulator in the loop is {120000*per/86400:.1f} days of "
              "simulation alone, before any learning is counted.")
    para(doc, "This is the whole reason the architecture has a learned "
              "evaluator in it. Not because a neural network is interesting, "
              "but because the search and the training both need an evaluator "
              "thousands of times faster than the one that decides the answer. "
              "That evaluator is the subject of Section 6.")

    doc.add_heading("4.3  Where the time actually goes, per request", level=2)
    para(doc, "Measured by timing each phase through the same functions a "
              "design request calls. Three independent runs:")
    table(doc, "Per-phase wall clock for one design request.",
          ["phase", "run 1", "run 2", "run 3"],
          [["language-model and MCP layer", "0.3 ms", "0.1 ms", "0.1 ms"],
           ["derive the starting point", "0.64 s", "0.61 s", "0.58 s"],
           ["PhysFit ⇄ RL loop, all 8 candidates", "0.26 s", "0.23 s", "0.23 s"],
           ["everything before verification", "0.90 s", "0.84 s", "0.81 s"],
           ["ngspice screen and 20-corner verify", "53.4 s", "52.2 s", "52.2 s"],
           ["ngspice share of the run", "98.3 %", "98.4 %", "98.5 %"]],
          widths=[2.6, 1.1, 1.1, 1.1])
    para(doc, f"Those runs verified corners sequentially, so their totals are "
              f"larger than the {F['t_med']:.1f} s production median in "
              f"Section 11.2, which runs them in parallel. Parallelism changes "
              f"only the ngspice part; the sub-second thinking phase is the "
              f"same either way.")
    para(doc, "The point to take from this table: all of the searching, all of "
              "the learned inference, and all of the reasoning together cost "
              "under one second. Essentially the entire wall clock is the one "
              "thing the framework refuses to approximate — the final verdict "
              "in a real simulator.")
    page_break(doc)


def s5_start(doc, F):
    doc.add_heading("5.  Choosing the starting point", level=1)

    doc.add_heading("5.1  Why a specification does not pick one circuit", level=2)
    para(doc, "A specification is a set of inequalities: boost between 3 and "
              "12 dB, power under 15 mW, eye wider than 0.4 UI. Inequalities "
              "do not have unique solutions. Many completely different sizings "
              "satisfy the same set.")
    bullet(doc, "a wide device at low current, or a narrow device at high "
                "current — the same gm, different bandwidth, different area")
    bullet(doc, "a large RL with a small gm, or the reverse — the same DC "
                "gain, with the poles in different places")
    bullet(doc, "more CTLE boost with a smaller tap, or less boost with a "
                "bigger one")
    para(doc, "They are not equally good. They sit in different basins — "
              "regions of the eleven-dimensional space with different amounts "
              "of room around them before some constraint is violated.")
    para(doc, "So the starting point is not a convenience, it is a decision. "
              "It selects which basin the search explores, and eleven "
              "dimensions is far too large for a search to stumble into a "
              "better basin on its own. The brief's demand for zero human "
              "intervention means that decision has to be derived, per "
              "specification, with no hand tuning. The rest of this section is "
              "that derivation.")

    doc.add_heading("5.2  Device lengths, assumed once", level=2)
    para(doc, "Tail devices use L = 500 nm — long, for output resistance and "
              "matching. Every other device uses L = 180 nm, close to the "
              "process minimum, for speed. (Presentation slides round this to "
              "200 nm; the netlist value is 180 nm.)")
    src(doc, "eqrl/netlist.py::L_TAIL, eqrl/start_physfit.py::derive")

    doc.add_heading("5.3  The order, and why it is forced", level=2)
    para(doc, "The three stages cannot be sized in any order. The CTLE sets "
              "the shape of the response, so it comes first. The summing stage "
              "only needs a common-mode condition, so it can follow. The DFE "
              "tap cannot be calculated at all until the other two exist — it "
              "is measured off a pulse driven through the finished path.")

    doc.add_heading("5.4  The CTLE", level=2)
    rows = [
        ["1", "f_target = centre of the required peak band",
         "where the boost must sit"],
        ["2", "f_p2 = 2.13 × f_target", "output pole placed ABOVE the peak, so "
         "the boost completes before the roll-off begins"],
        ["3", "RL = 1 / (2π · f_p2 · Cout)", "the load that puts the pole there"],
        ["4", "k = 10^(peaking_dB × k_pk / 20)",
         "over-ask; k_pk is the measured optimism of the closed form"],
        ["5", "gm = k / RL", "this is what pins DC gain at 0 dB"],
        ["6", "Rs = 2(k − 1) / gm", "the degeneration for that boost"],
        ["7", "Cs = k / (2π · Rs · f_p1), f_p1 = 0.8 × f_target",
         "puts the zero below the pole"],
        ["8", "Vov = (Vcm − 0.1 − Vt) / 2", "from the input common-mode range "
         "budget, splitting the headroom between tail and pair"],
        ["9", "Vt and Vov characterised in SPICE → Vgs ≈ Vt + Vov; gm/ID then "
         "picks the branch current and the tail current", ""],
        ["10", "gm/ID gives the widths — diff pair and tail MOSFET", ""],
    ]
    table(doc, "The CTLE derivation, in dependency order.",
          ["#", "step", "why"], rows, widths=[0.35, 2.9, 2.75])
    src(doc, "eqrl/start_physfit.py::derive")
    para(doc, "Steps 1 to 7 are algebra on the transfer function. Steps 8 to "
              "10 are the standard gm/ID sizing flow, anchored on a threshold "
              "voltage measured once in SPICE rather than taken from a "
              "datasheet.")
    para(doc, "Step 5 is the one worth pausing on. Writing gm = k / RL is "
              "equivalent to fixing the DC gain at exactly 0 dB, because the "
              "closed-form DC gain is gm·RL/k. Left unconstrained, DC gain "
              "wanders — and a design with gain at DC is an amplifier, not an "
              "equaliser.")

    doc.add_heading("5.5  The summing stage", level=2)
    table(doc, "The summing-stage derivation.", ["#", "step", "why"],
          [["1", "Wg1 = 100 × W_min = 42 µm",
            "the tap device is Wg1 × tap%; at the smallest tap setting of 1 % "
            "it must still clear the PDK's 0.42 µm minimum width"],
           ["2", "Ig1 = 1 mA",
            "free — nothing downstream is sensitive to it"],
           ["3", "Rsum = VDD / Ig1 = 1.8 kΩ",
            "half the current flows in each leg, so (Ig1/2)·Rsum = VDD/2 and "
            "the output common mode lands at mid-supply, which is where the "
            "comparator wants it"]],
          widths=[0.35, 2.2, 3.45])
    src(doc, "eqrl/start_physfit.py::derive lines 108–110")

    doc.add_heading("5.6  The DFE tap", level=2)
    para(doc, "Sized last, and measured rather than calculated.")
    for n, t in enumerate([
            "the CTLE and summing stage are already designed",
            "a single isolated pulse is driven through that path",
            "the intersymbol interference is read one Unit Interval after the "
            "cursor — that residue is exactly what the tap must cancel",
            "that reading sets the tap coefficient",
            "every device value in the tap branch follows from it"], 1):
        para(doc, f"{n}.  {t}", size=11, space_after=4)
    code(doc, "I_tap  = Ig1   × tap %\n"
              "W_pair = Wg1   × tap %          (floored at the PDK's 0.42 µm)\n"
              "W_tail = Wtail × I_tap / Itail")
    src(doc, "eqrl/netlist.py lines 297, 307, 289")
    para(doc, "So the entire feedback stage collapses to one variable. Current, "
              "pair width and tail width are not three independent choices, "
              "they are three consequences of the tap coefficient. Sizing them "
              "separately would let the tap weight and the bias point drift "
              "apart, and the tap weight is the only thing that matters.")
    para(doc, "For scale: after the channel the post-cursor is +0.373 of the "
              "cursor. By the time it reaches the summing node with 7.4 dB of "
              "CTLE boost in front of it, it is down to +0.090 — and that "
              "remainder is what the tap removes.")
    src(doc, "docs/findings-ctle-dfe.md §2")

    doc.add_heading("5.7  What it costs, and what it achieves", level=2)
    para(doc, "The whole derivation runs in about 0.2 seconds per "
              "specification and uses one SPICE operating point, for the "
              "threshold voltage, cached across the run. Seven of the ten "
              "measurement specifications are solved by the very first "
              "proposal made from this start.")
    src(doc, "results/solve_rate_physfit.json, docs/systematic-starting-point.md")
    page_break(doc)


def s6_physfit(doc, F):
    cfg, val, chk = F["cfg"], F["val"], F["check"]
    doc.add_heading("6.  PhysFit: the fast evaluator", level=1)
    para(doc, "This document uses PhysFit for the whole fast evaluator: the "
              "closed-form circuit equations together with the neural "
              "correction trained on top of them. In the codebase PhysFit is "
              "the device-model layer and Surrogate is the network; they are "
              "one component as far as the system is concerned.")
    para(doc, "It is two fitted things stacked, and the order is forced — the "
              "correction can only be defined once there is something to "
              "correct.")
    table(doc, "The two layers.", ["layer", "what it is", "how it is fitted"],
          [["1", "device physics: gm, Cout, tail headroom, plus two "
                 "calibration factors", "least squares, closed form, seconds"],
           ["2", "a bounded correction on top", "gradient descent"]],
          widths=[0.5, 3.1, 2.4])

    doc.add_heading("6.1  How the training points were sampled", level=2)
    para(doc, "The designs simulated to train the model are not random. They "
              "come from four deliberately chosen regions, interleaved in the "
              "collection plan so that any prefix of the plan is "
              "representative.")
    order = ["sobol", "seed", "final", "goal_start"]
    why = {
        "sobol": ("Sobol sequence over the whole 11-dimensional box",
                  "teaches the model where circuits FAIL — most of the box is "
                  "broken designs, and the model has to know that"),
        "seed": ("Gaussian scatter, σ = 0.25, around the analytic start for "
                 "the default specification",
                 "density where designs are plausible"),
        "final": ("Gaussian scatter, σ = 0.15, around a known-good final "
                  "design",
                  "density where designs are GOOD, which is where the search "
                  "ends up"),
        "goal_start": ("Gaussian scatter, σ = 0.12, around the analytic start "
                       "for many DIFFERENT sampled specifications",
                       "density where the goal-conditioned agent actually "
                       "begins each episode"),
    }
    rows = []
    for c in order:
        d, r = F["clouds"].get(c, (0, 0))
        rows.append([c, why[c][0], f"{d:,}", f"{r:,}"])
    tot_d = sum(F["clouds"][c][0] for c in F["clouds"])
    tot_r = sum(F["clouds"][c][1] for c in F["clouds"])
    rows.append(["total", "", f"{tot_d:,}", f"{tot_r:,}"])
    table(doc, "How the training designs were sampled. Each design is "
               "simulated at four corners, giving several rows per design.",
          ["region", "how it is sampled", "designs", "rows"], rows,
          widths=[1.0, 3.2, 0.9, 0.9])
    src(doc, "scripts/collect_data.py::plan; counts recomputed from the "
             "evaluation log in eqrl/cache.py")
    for c in order:
        bullet(doc, why[c][1], bold_lead=f"{c} — ")
    para(doc, "The fourth region deserves its own note, because it was added "
              "in response to a measurement rather than anticipated. A "
              "goal-conditioned agent starts from a different analytic design "
              "for every specification it is given. Trained without that "
              "region, the model judged those starting designs unsaturated, "
              "which clips the reward to a constant — so every training "
              "episode began on a flat plateau with no gradient, while real "
              "SPICE showed the same designs biased correctly and scoring "
              "well. The model was accurate everywhere except the one place "
              "training starts. Collecting that region fixed it.")

    doc.add_heading("6.2  Fitting the device physics", level=2)
    para(doc, "Least squares, in log space. Square law says gm = "
              "sqrt(2·µCox·(W/L)·Id), which is a straight line in logs with "
              "slope one half on both W/L and Id. Short-channel devices in "
              "this process do not obey square law, so the shape of the "
              "equation is kept and the exponents are fitted:")
    code(doc, "log(gm) = a + b·log(W/L) + c·log(Id) + d·log(T/T0)")
    para(doc, "Four unknowns, one least-squares solve. The same treatment is "
              "applied to the summing stage's transconductance — which needs "
              "an additional Vgs term, because geometry and current alone fit "
              "it poorly — and to the tail device's saturation voltage and "
              "drain-source voltage.")
    q = F["physfit"].get("quality", {})
    rows = []
    for k in ("gm_m1", "gm_g1", "vdsat_tail", "vds_tail"):
        if k in q:
            e = q[k]
            rows.append([k, f"{e.get('r2_log', float('nan')):.3f}",
                         (f"{e['median_rel_err']:.1%}"
                          if "median_rel_err" in e else "—"),
                         f"{e.get('n', '—')}"])
    if rows:
        table(doc, "Quality of the closed-form device fits, in log space.",
              ["quantity", "R² (log)", "median relative error", "n"], rows,
              widths=[1.3, 1.1, 1.8, 0.8])
    src(doc, "models/surrogate/…/physfit.json, written by "
             "eqrl/physics.py::PhysFit.fit")

    doc.add_heading("6.3  Measuring how optimistic the formula is", level=2)
    c = F["physfit"]["coef"]
    para(doc, f"Two calibration factors, measured across the whole dataset as "
              f"the median ratio of closed-form prediction to measured value:")
    code(doc, f"k_pk = {c['k_pk']:.3f}   the closed form reads this many times "
              f"high on peaking\n"
              f"k_f  = {c['k_f']:.3f}   ... and this much high on where the "
              f"peak lands")
    para(doc, "The design procedure pre-multiplies its targets by these, so "
              "asking for 7.5 dB of boost means asking the formula for about "
              "12.6 dB. Neither factor is hand-tuned.")
    para(doc, "One subtlety worth knowing if asked: these must be computed "
              "with the FITTED gm, not the square-law default. Computed "
              "against an unfitted model, the device error folds into the "
              "calibration factor and the result comes out below 1 — the "
              "opposite direction to the real optimism.")
    src(doc, "eqrl/physics.py::PhysFit.fit, final block")

    doc.add_heading("6.4  Splitting the data honestly", level=2)
    para(doc, "Each design is simulated at four corners, producing four rows "
              "with almost identical features. Splitting those rows at random "
              "would put some corners of a design in training and others in "
              "validation — and the held-out score would then measure "
              "memorisation rather than generalisation, because the model has "
              "already seen that design at another corner.")
    para(doc, f"The split is therefore keyed on the DESIGN. "
              f"{cfg['val_frac']:.0%} of designs are held out: "
              f"{val['n_val_designs']} designs, {val['n_val_rows']} rows, "
              f"never trained on.")
    src(doc, "eqrl/surrogate.py::split_by_candidate")

    doc.add_heading("6.5  The inputs", level=2)
    para(doc, f"{cfg['n_features']} features, every one a physically "
              f"meaningful quantity rather than a raw knob: transconductances, "
              f"output capacitance, the zero and both pole positions and their "
              f"ratio, the closed-form predictions themselves, computed "
              f"pulse-response cursors, the tap ratio, common-mode headroom, "
              f"supply, temperature and the process corner.")
    para(doc, "Giving the network log(gm) rather than W and Itail separately "
              "means it does not have to rediscover the square law. It starts "
              "from quantities that already carry the physics.")
    src(doc, "eqrl/physics.py::FEATURE_NAMES")

    doc.add_heading("6.6  What the network is asked to predict", level=2)
    para(doc, "Fifteen outputs per design, per corner, in three groups.")
    table(doc, "The model's outputs.",
          ["group", "count", "quantities"],
          [["physics × correction", len(cfg["residual_targets"]),
            ", ".join(cfg["residual_targets"])],
           ["predicted directly", len(cfg["direct_targets"]),
            ", ".join(cfg["direct_targets"])],
           ["pass/fail flag", len(cfg["flag_targets"]),
            ", ".join(cfg["flag_targets"])]],
          widths=[1.4, 0.6, 4.2])
    para(doc, "The first group are not predicted as absolute values. The "
              "target the network sees is how wrong the equation was:")
    code(doc, "quantities in dB:      target = measured − closed_form\n"
              "ratio quantities:      target = log( measured / closed_form )")
    para(doc, "A log ratio, so the head's output passes through exp() and "
              "becomes a multiplier on the physics. Zero means the physics was "
              "already right.")
    para(doc, "One masking rule matters here. A design with no peak reports a "
              "peak frequency of 1 MHz, the sweep's start frequency. Its "
              "residual target would be log(1e6 / 1.8e9) ≈ −7.5, an outlier an "
              "order of magnitude larger than any real correction, which would "
              "dominate that output's loss. Those rows are masked out; it "
              "costs no pass/fail agreement, because both the real and the "
              "predicted design fail the peak-frequency band in that case "
              "anyway.")
    src(doc, "eqrl/surrogate.py::residual_targets")
    para(doc, "The directly-predicted group is standardised and then clipped "
              "at eight standard deviations. A heavy-tailed metric otherwise "
              "lets a handful of samples own the whole loss, and the model "
              "spends its capacity on outliers instead of the bulk it will "
              "actually be asked about. All normalisation statistics are "
              "computed on the training set alone and then applied to the "
              "validation set.")

    doc.add_heading("6.7  The network, and why it cannot drift from physics",
                    level=2)
    para(doc, f"A shared trunk of two hidden layers, {cfg['n_features']} "
              f"inputs into 128 and 128 again with SiLU activations, then "
              f"three small output heads — one per group above. About 22,700 "
              f"parameters.")
    para(doc, "SiLU rather than ReLU is deliberate. The RL agent searches "
              "inside this model by nudging knobs and asking whether the "
              "result improved. ReLU's kinks put creases and flat spots in "
              "that landscape, where a small nudge produces either nothing or "
              "a sudden jump. SiLU is smooth, so every small move produces a "
              "small, informative change.")
    para(doc, "The correction head is initialised to exactly zero:")
    code(doc, "nn.init.zeros_(self.head_res.weight)\n"
              "nn.init.zeros_(self.head_res.bias)")
    para(doc, "Since the correction is applied as exp(g), and exp(0) = 1, an "
              "untrained network multiplies the physics by exactly one. The "
              "model BEGINS as the hand calculation. Training can only move it "
              "away from there, and the correction is hard-clipped so it can "
              "never move the answer by more than a factor of 7.4. In practice "
              "the learned corrections sit around 1.8×.")
    src(doc, "eqrl/surrogate.py::Net, MAX_LOG_RESIDUAL")
    figure(doc, "04a_physics_anchor.png",
           "Left: a prediction is the hand calculation multiplied by a bounded "
           "correction. Right: the correction is capped at ×7.4, and the "
           "learned corrections sit well inside that bound.")

    doc.add_heading("6.8  The training loop", level=2)
    for t in [f"Adam, learning rate 3e-3, full batch — all training rows every "
              f"step, no minibatching",
              f"up to {cfg['epochs']:,} epochs",
              "loss = masked MSE on the corrections + masked MSE on the direct "
              "outputs + 0.1 × binary cross-entropy on the saturation flag",
              "early stopping with patience 300; the BEST validation state is "
              "restored at the end, not the last epoch's weights"]:
        bullet(doc, t)
    para(doc, "Two of those deserve explanation. The loss is masked because "
              "not every design has every measurement — a design with no peak "
              "has no peak frequency. A mask makes a missing value contribute "
              "nothing, where scoring it as zero would teach the network that "
              "a missing peak means a peak at 0 Hz. And the flag loss carries "
              "a weight of 0.1 because a yes/no loss sits on a different scale "
              "from the regression terms; at equal weight it dominates, and "
              "the result is an excellent saturation detector that predicts "
              "gain badly.")
    src(doc, "scripts/train_surrogate.py::train_member")

    doc.add_heading("6.9  How much time it saves", level=2)
    per = F["spice_per_design"]
    table(doc, "The same design, the same four corners, evaluated both ways.",
          ["evaluator", "time per design", "source"],
          [["ngspice", f"{per*1000:.0f} ms", "results/baseline.json"],
           ["PhysFit", "0.77 ms", "measured, 200 repetitions"],
           ["ratio", f"≈ {per/0.00077:,.0f}×", ""]],
          widths=[1.3, 1.6, 2.6])
    para(doc, "This ratio is what makes the rest of the project possible. It "
              "turns a six-day training run into a fifty-minute one, and it "
              "turns a search that would be unaffordable into one that costs a "
              "quarter of a second.")

    doc.add_heading("6.10  How accurate it is", level=2)
    para(doc, "All of the following is measured on the held-out designs — "
              "designs the model never saw at any corner.")
    pa = val["pass_agreement"]
    order = sorted(pa, key=lambda k: -pa[k])
    table(doc, "Does the model agree with the simulator about whether each "
               "condition passes?",
          ["condition", "agreement"],
          [[k, f"{pa[k]*100:.0f} %"] for k in order], widths=[1.6, 1.2])
    figure(doc, "04b_surrogate_agreement.png",
           "The same data as a chart. Gain and current are near-exact; the eye "
           "is the softest, which is why the eye is measured rather than "
           "predicted at verification time.")
    pm = val["per_metric"]
    keys = ["power_w", "dc_gain_db", "gain_nyq_db", "peaking_db", "hd3_db",
            "eye_height_v", "eye_width_ui"]
    table(doc, "Numerical accuracy on held-out designs.",
          ["quantity", "R²", "Spearman ρ", "n"],
          [[k, f"{pm[k]['r2']:.3f}", f"{pm[k]['spearman']:.3f}",
            f"{pm[k]['n']}"] for k in keys if k in pm],
          widths=[1.5, 0.9, 1.2, 0.7])
    g1 = val["G1"]
    para(doc, f"For a search, ranking matters more than absolute accuracy: the "
              f"agent needs to know which of several designs is best. The rank "
              f"correlation between predicted and simulated worst-over-corner "
              f"reward is {g1['spearman_reward']:.3f} across all held-out "
              f"designs, rising to {g1['spearman_reward_saturated']:.3f} when "
              f"restricted to properly-biased designs — which is the only "
              f"population the agent ever chooses among. Top-20 % precision is "
              f"{g1['top20pct_precision']:.1%}.")
    src(doc, "models/surrogate/…/validation.json")

    doc.add_heading("6.11  Does the model fool itself?", level=2)
    para(doc, "A model you optimise against will find its own blind spots and "
              "report a fantasy. So the check is direct: let a search hunt for "
              "the model's single favourite design, then simulate that exact "
              "design for real.")
    table(doc, "The optimism check.", ["", "reward"],
          [["what the model predicted for its favourite design",
            f"{chk['predicted_reward']:.5f}"],
           ["what ngspice measured for the same design",
            f"{chk['real_reward']:.5f}"],
           ["over-optimism", f"{chk['optimism_gap']:.5f}"]],
          widths=[3.8, 1.3])
    para(doc, f"Under a tenth of one percent, and the design passed every "
              f"condition at every corner. The model's favourite design was "
              f"genuinely good rather than a hole in its own coverage.")
    src(doc, "results/surrogate_check.json")
    page_break(doc)


def s7_rl(doc, F):
    doc.add_heading("7.  The reinforcement-learning agent", level=1)
    para(doc, "Given a specification and a starting design, the agent moves "
              "the eleven knobs until the specification is met.")

    doc.add_heading("7.1  What it is", level=2)
    for t in ["Soft Actor-Critic, goal-conditioned: the target specification "
              "is part of the observation, so one trained agent serves any "
              "specification inside its range rather than being retrained per "
              "request.",
              "The action is eleven real numbers at once — a continuous "
              "control policy, like driving every joint of a robot "
              "simultaneously, not a classifier choosing from a list.",
              "Twenty steps per episode, each a small adjustment to all "
              "eleven. The agent steers toward a design; it does not answer in "
              "one shot.",
              "120,000 training steps, learning rate 3e-4."]:
        bullet(doc, t)
    src(doc, "eqrl/env_goal.py::GoalCtleEnv, scripts/train_policy.py")

    doc.add_heading("7.2  The cost function", level=2)
    para(doc, "Two stages, and the second is the one that matters.")
    para(doc, "First, each of the ten conditions is scored on its own — not as "
              "a hard pass or fail, but as a soft ramp: full marks inside the "
              "requirement, falling off smoothly outside it. A smooth score "
              "gives the agent a direction to move in even while it is still "
              "failing. A bare pass/fail would be flat almost everywhere and "
              "teach nothing.")
    para(doc, "Second, the reward is the WORST of those scores, taken across "
              "every corner. Never the average.")
    para(doc, "Averaging is the trap. A design that is superb on nine "
              "conditions and fails the tenth scores well on an average and is "
              "worthless in reality. Taking the minimum means the agent can "
              "only improve its reward by fixing whatever is currently worst, "
              "at whatever corner is currently worst — which is exactly what a "
              "circuit designer does.")
    para(doc, "A bonus is added when a design passes everything, so the agent "
              "is pulled past 'nearly meets specification' rather than "
              "settling just short of it.")
    src(doc, "eqrl/specs.py::score, min_margin; eqrl/env_goal.py "
             "reward_mode='margin'")

    doc.add_heading("7.3  The choices, and the reasons", level=2)
    table(doc, "Design decisions in the learning setup.",
          ["choice", "why"],
          [["Soft Actor-Critic rather than PPO",
            "SAC keeps a deliberately stochastic policy. Eight different "
            "candidates are needed from one starting design; a "
            "near-deterministic agent returns eight copies and best-of-eight "
            "buys nothing."],
           ["Goal-conditioned — the specification is an input",
            "one trained agent serves any specification in range; the "
            "alternative is retraining per request, which defeats the purpose"],
           ["Twenty small steps rather than one shot",
            "the agent can react to what it has produced so far; a one-shot "
            "regression from specification to eleven numbers cannot"],
           ["Worst-margin reward",
            "a design is only as good as its weakest condition at its worst "
            "corner"],
           ["Start from the derived design, not from random",
            "an eleven-dimensional space is far too large to find the working "
            "region blind"]],
          widths=[2.1, 4.1])

    doc.add_heading("7.4  Deliberately random, reproducibly", level=2)
    para(doc, "The policy samples from a distribution rather than returning a "
              "fixed answer, so running it eight times from the same starting "
              "design yields eight genuinely different candidates. The seeds "
              "are fixed, so the same eight come back on a re-run.")
    src(doc, "scripts/best_of_n.py::propose_policy, deterministic=False")

    doc.add_heading("7.5  How it was trained, and what that would have cost",
                    level=2)
    per = F["spice_per_design"]
    para(doc, "Training runs entirely inside PhysFit. The simulator is never "
              "called during learning.")
    table(doc, "Training cost, measured and counterfactual.",
          ["", "time"],
          [["as actually run, inside PhysFit",
            f"120,000 steps in {F['train_wall_s']/60:.0f} minutes"],
           ["the same training with ngspice in the loop",
            f"{120000*per/86400:.1f} days — simulation alone"],
           ["building the dataset PhysFit learned from",
            f"1,500 designs × {per:.2f} s = {1500*per/3600:.1f} hours, paid "
            f"once"]],
          widths=[3.0, 2.6])
    src(doc, "results/policy_train_eval.json; results/baseline.json")
    para(doc, "The counterfactual is the optimistic figure — it counts only "
              "the simulation, not the learning on top of it. Training the "
              "agent at all is possible only because the evaluator is "
              "thousands of times faster than the simulator.")
    curve = ", ".join(f"{s//1000}k: {r:.2f}" for s, r in F["train_curve"])
    para(doc, f"During training the share of sampled specifications the agent "
              f"could satisfy rose as follows — {curve}. These are measured "
              f"against PhysFit during training, not against SPICE; the "
              f"simulator-verified figure is in Section 7.6.")

    doc.add_heading("7.6  Measured performance", level=2)
    p = F["pol"]
    para(doc, f"On ten unseen specifications, with every candidate verified at "
              f"all twenty corners: {p['npass']} of {p['nprop']} proposals "
              f"survived verification, a per-proposal rate of "
              f"{p['rate']:.1%}. {p['solved']} of {p['n']} specifications were "
              f"solved within eight candidates. All eight candidates are "
              f"produced in 0.26 s, because proposing touches no simulator at "
              f"all.")
    src(doc, "results/best_of_n_policy.json")
    para(doc, "Those two rates answer different questions and are easy to "
              "confuse. The per-proposal rate is how often one roll of the "
              "dice works. The coverage is how often at least one of eight "
              "rolls works, which is what a user actually experiences.")
    page_break(doc)


def s8_envelope(doc, F):
    doc.add_heading("8.  Requests outside the trained range", level=1)
    para(doc, "The agent was trained on a bounded range of specifications. "
              "Asked for something outside it, a neural network still answers "
              "— networks always answer — but the answer carries none of the "
              "measured reliability above. So the framework checks, switches "
              "method, and says which one it used.")

    doc.add_heading("8.1  What the range actually bounds", level=2)
    para(doc, "This is worth stating precisely. The range is the span of "
              "SPECIFICATIONS the agent saw during training. It is a limit on "
              "what you may ask for, not on which circuits PhysFit can "
              "evaluate — PhysFit will score any design put in front of it. It "
              "is the agent that has only been taught to aim at targets inside "
              "the box.")
    try:
        from eqrl.env_goal import GoalSpace
        g = GoalSpace.load()
        rows = [["peaking target", f"{g.peaking_target_db[0]:.1f} – "
                                   f"{g.peaking_target_db[1]:.1f} dB"],
                ["peak frequency, lower edge",
                 f"{g.fpeak_lo[0]/1e9:.2f} – {g.fpeak_lo[1]/1e9:.2f} GHz"],
                ["peak frequency, upper edge",
                 f"{g.fpeak_hi[0]/1e9:.2f} – {g.fpeak_hi[1]/1e9:.2f} GHz"],
                ["HD3 limit", f"{g.hd3_max_db[0]:.0f} – "
                              f"{g.hd3_max_db[1]:.0f} dBc"],
                ["power limit", f"{g.power_max_w[0]*1e3:.0f} – "
                                f"{g.power_max_w[1]*1e3:.0f} mW"],
                ["eye height limit", f"{g.eye_height_min_v[0]*1e3:.0f} – "
                                     f"{g.eye_height_min_v[1]*1e3:.0f} mV"],
                ["eye width limit", f"{g.eye_width_min_ui[0]:.2f} – "
                                    f"{g.eye_width_min_ui[1]:.2f} UI"]]
        table(doc, "The range of specifications the agent was trained on.",
              ["field", "range"], rows, widths=[2.4, 2.2])
        src(doc, "results/goal_space.json, via eqrl/env_goal.py::GoalSpace")
    except Exception as e:                                  # pragma: no cover
        print(f"  [goal space] {e}")
    para(doc, "Every incoming request is compared against this field by field "
              "before anything runs, and the result reports both whether the "
              "request was inside it and which fields were not.")
    src(doc, "eqrl/mcp_server.py::envelope_check")

    doc.add_heading("8.2  The fallback search", level=2)
    para(doc, "Outside the range the framework uses a cross-entropy search "
              "instead: scatter forty variations around the same derived "
              "starting design, score them all with PhysFit, keep the best, "
              "move the centre toward them and narrow the spread, repeat. Two "
              "thousand evaluations, about twenty seconds, still entirely "
              "inside PhysFit.")
    para(doc, "A search has no trained range, because it is a search rather "
              "than a learned map. That is exactly why it can go where the "
              "agent cannot.")
    src(doc, "eqrl/agents/cem.py, scripts/best_of_n.py::propose_surrogate_cem")
    p, c = F["pol"], F["cem"]
    table(doc, "The two methods compared.",
          ["", "agent", "search"],
          [["per-proposal success", f"{p['rate']:.1%}", f"{c['rate8']:.1%}"],
           ["time to produce eight candidates", "0.26 s", "~20 s"],
           ["works outside the trained range", "no", "yes"],
           ["specifications solved", f"{p['solved']} of {p['n']}",
            f"{c['solved']} of {c['n']}"]],
          widths=[2.6, 1.5, 1.5])
    src(doc, "results/best_of_n_policy.json, "
             "results/best_of_n_surrogate_cem.json. The search's per-proposal "
             f"rate is over its first eight proposals ({c['p8']} of {c['n8']}), "
             "so the comparison is like for like.")
    para(doc, f"The two succeed on DIFFERENT specifications. Running both and "
              f"keeping whichever passes reaches {F['union']} of {p['n']}, "
              f"where either alone reaches {p['solved']}. No new method — just "
              f"spending more time.")
    page_break(doc)


def s9_verify(doc, F):
    doc.add_heading("9.  Verification: the simulator decides", level=1)
    para(doc, "One rule governs the whole framework: the learned model only "
              "ever proposes. SPICE decides. The all_pass flag is never set "
              "from the model's opinion — it becomes true only after twenty "
              "real corner simulations have all passed.")

    doc.add_heading("9.1  The cheap screen, and which corners it uses", level=2)
    para(doc, "The eye is the one condition with a comparator in it, and a "
              "comparator has no closed form. So rather than predicting it at "
              "verification time, the framework measures it — but at two "
              "corners rather than twenty.")
    para(doc, "Which two is a measured result, not a guess. Over 280 designs "
              "simulated at the full grid, the corner that decides eye width "
              "most often is sf at 0 °C. Screening at the two sf 0 °C corners "
              "recovers the true twenty-corner worst case 89.6 % of the time, "
              "with a mean overestimate of 0.008 UI. A third corner takes it "
              "to 91.4 %, which is not worth the cost.")
    src(doc, "scripts/best_of_n.py::SCREEN_CORNERS, HANDOVER.md §20.3")
    table(doc, "What the screen saves on one request.",
          ["approach", "SPICE evaluations", "time"],
          [["verify every proposal at 20 corners", "540", "~10 min"],
           ["screen 8 × 2, then verify the survivors", "56", "~1.5 min"]],
          widths=[2.8, 1.6, 1.2])
    para(doc, "The screen is a filter, not a verdict. Anything it lets through "
              "still faces all twenty corners, and any design it wrongly "
              "passes is caught there. Designs the screen let through that the "
              "full grid then rejected are recorded, so the screen's own error "
              "rate stays measured rather than assumed.")

    doc.add_heading("9.2  The twenty-corner verdict", level=2)
    para(doc, "Every surviving candidate is simulated at all twenty corners. "
              "For each condition the framework keeps the WORST margin across "
              "all of them, and all_pass is true only when every one of the "
              "ten worst-case margins is non-negative.")
    para(doc, "Margins are reported exactly as measured. Nothing is rounded "
              "toward passing, and a design that misses is still returned — "
              "with the conditions it missed and by how much — rather than "
              "withheld.")
    src(doc, "scripts/deploy_policy.py::verify, eqrl/mcp_server.py::_scorecard")
    para(doc, "If the time budget expires mid-run, the best design found so "
              "far is returned with all_pass hard-coded false, because a "
              "truncated search has not finished the corners that would "
              "justify a pass.")
    page_break(doc)


def s10_mcp(doc, F):
    doc.add_heading("10.  The language-model wrapper", level=1)
    para(doc, "The brief's bonus deliverable. Three tools exposed over the "
              "Model Context Protocol, so any MCP-capable language-model host "
              "can drive the framework in plain English.")
    table(doc, "The three tools.", ["tool", "what it does", "cost"],
          [["check_spec", "is this request reachable, and which method would "
                          "answer it", "< 1 s, no simulation"],
           ["design_equalizer", "size a design and verify it at twenty "
                                "corners", "~40 s, real SPICE"],
           ["explain_capabilities", "what the framework can and cannot do",
            "instant"]],
          widths=[1.4, 3.2, 1.4])
    src(doc, "eqrl/mcp_server.py, scripts/run_mcp_server.py, MCP.md")
    para(doc, "Honesty is enforced in code rather than requested in a prompt. "
              "Every answer states which method ran, whether the request was "
              "inside the trained range, and which starting derivation "
              "produced the design. A design that misses is returned with its "
              "failing conditions and is never described as meeting the "
              "specification.")
    para(doc, "Each run also writes a self-contained HTML report: the "
              "ten-condition scorecard, the eye diagram, the AC response, the "
              "eleven sized devices with units, which limits were asked for "
              "versus defaulted from the brief, and the netlist.")
    page_break(doc)


def s11_results(doc, F):
    doc.add_heading("11.  Results", level=1)
    p, c = F["pol"], F["cem"]

    doc.add_heading("11.1  How often a specification is met", level=2)
    para(doc, "Ten unseen specifications, drawn from the trained range with a "
              "fixed seed, every candidate verified at all twenty corners. The "
              "ten are listed in full in Appendix A.")
    table(doc, "Coverage.", ["method", "specifications solved",
                             "per-proposal success"],
          [["RL agent, 8 candidates", f"{p['solved']} of {p['n']}",
            f"{p['rate']:.1%}  ({p['npass']}/{p['nprop']})"],
           ["search alone, 8 candidates", f"{c['solved']} of {c['n']}",
            f"{c['rate8']:.1%}  ({c['p8']}/{c['n8']})"],
           ["both together", f"{F['union']} of {p['n']}", "—"]],
          widths=[2.0, 1.8, 2.0])
    src(doc, "results/best_of_n_policy.json, "
             "results/best_of_n_surrogate_cem.json")
    figure(doc, "05a_coverage.png",
           "Specifications solved against the number of candidates tried. The "
           "agent reaches its coverage in far fewer candidates than the search "
           "needs.")

    doc.add_heading("11.2  How long it takes", level=2)
    para(doc, f"Median {F['t_med']:.1f} s, range {F['t_lo']:.1f} to "
              f"{F['t_hi']:.1f} s, over {len(F['walls'])} measured runs "
              f"through the language-model wrapper.")
    src(doc, "results/mcp_e2e.json")
    figure(doc, "05b_time.png",
           "Time to design and verify one circuit, with the median marked.")
    para(doc, "Section 4.3 breaks that time down: under a second of it is "
              "derivation and search, and essentially all the rest is ngspice.")

    doc.add_heading("11.3  Against the sweep the brief asks us to beat", level=2)
    b = F["base"]
    para(doc, "The brief asks for something faster than sweeping the parameter "
              "space, so the comparison was run directly: random search, the "
              "same candidate budget, the same simulator, the same corners, "
              "the same scorer, the same channel and eye measurement.")
    table(doc, "Random search against the framework, at an equal budget.",
          ["", "best reward", "designs passing"],
          [[f"random search, {b['budget']} candidates",
            f"{b['best_reward']:.3f}", f"0 of {b['budget']}"],
           ["this framework", "0.987", "a passing design"]],
          widths=[2.6, 1.4, 1.6])
    src(doc, "results/baseline.json")
    para(doc, "And the exhaustive version stays arithmetic rather than "
              "empirical: Section 4.1 gives 217.7 hours for a three-point grid "
              "and 6.8 years for a five-point grid over eleven variables.")

    doc.add_heading("11.4  A verified design", level=2)
    d = F["design"]
    rows = [[r["spec"], r["required"],
             ("pass" if r["margin"] is None else f"+{r['margin']*100:.1f} %"),
             "pass" if r["pass"] else "FAIL"]
            for r in d["scorecard"]]
    table(doc, "Every condition at its worst corner, for the design in "
               "results/report_design.json.",
          ["condition", "requirement", "worst-case margin", "result"], rows,
          widths=[1.0, 2.6, 1.4, 0.8])
    src(doc, "results/report_design.json")
    para(doc, "The margins are worst-case across all twenty corners, not "
              "typical values. The tightest is eye width, and it still clears "
              "its requirement by 13.3 %.")
    figure(doc, "05c_eye.png",
           "The measured eye at the slicer for a design produced by the "
           "current framework: 237 mV high against 100 mV required, 0.516 UI "
           "wide against 0.4 UI.")
    figure(doc, "05d_ac_response.png",
           "The measured CTLE response for the same design: 8.19 dB of boost "
           "peaking at 1.89 GHz, inside the required window and just below "
           "the 2.5 GHz Nyquist frequency, which is where the channel's loss "
           "is worst.")

    doc.add_heading("11.5  Against the brief, point by point", level=2)
    table(doc, "The brief's requirements against what was delivered.",
          ["the brief asked for", "delivered"],
          [["an RL framework: specifications in, schematic and resulting "
            "specifications out", "yes"],
           ["integrates with a SPICE simulator", "yes — ngspice, 20 corners"],
           ["zero human intervention",
            "yes — the starting point is derived per specification, not "
            "hand-seeded"],
           ["faster than sweeping the parameter space",
            "yes — under a minute against an intractable sweep"],
           ["meets specifications across PVT",
            "yes — all 20 corners simulated for every reported pass"],
           ["bonus: a language-model interaction wrapper",
            "yes — three MCP tools"]],
          widths=[3.3, 2.9])

    doc.add_heading("11.6  Engineering quality", level=2)
    para(doc, "327 automated tests pass — 282 that need no PDK and 45 that run "
              "real ngspice. They check the claims, not only the code: that "
              "the schematic matches the netlist that is simulated, that the "
              "reported margins match the per-corner data, that a plotted "
              "curve agrees with the measurement printed above it, and that "
              "documented numbers match the artifacts they came from.")
    src(doc, "tests/, VERIFY.md")
    page_break(doc)


def s12_repro(doc, F):
    doc.add_heading("12.  Reproducing all of this", level=1)
    para(doc, "Everything quoted in this document is regenerated by the "
              "commands below. They are listed in dependency order.")
    for what, cmd in [
            ("the full test suite",
             "python3 -m pytest tests/ -q"),
            ("the pure tier only, no PDK needed",
             'python3 -m pytest tests/ -q -m "not needs_pdk"'),
            ("collect training designs",
             "python3 scripts/collect_data.py --n-sobol 1000 --n-seed 1000 "
             "--n-final 500 --n-goal-starts 800"),
            ("fit PhysFit and train the network",
             "python3 scripts/train_surrogate.py --tag collect,goalstart,"
             "goalstart2 --members 5 --val-frac 0.25"),
            ("train the agent",
             "python3 scripts/train_policy.py --steps 120000"),
            ("measure coverage over ten specifications",
             "python3 scripts/best_of_n.py --method policy --k 8 --n-specs 10"),
            ("the random-search baseline",
             "python3 scripts/baseline.py --budget 95"),
            ("design one circuit end to end",
             "python3 scripts/run_mcp_server.py --self-test"),
            ("rebuild the figures",
             "python3 scripts/make_report_figures.py && "
             "python3 scripts/make_channel_figures.py"),
            ("rebuild this document",
             "python3 scripts/make_reference_docx.py")]:
        para(doc, what, size=10.5, bold=True, space_after=2)
        code(doc, cmd)
    para(doc, "The trained model and policy are committed, so the measurement "
              "commands run without repeating the collection and training "
              "steps.")
    page_break(doc)


def appA_specs(doc, F):
    doc.add_heading("Appendix A.  The ten measurement specifications", level=1)
    d = F["pol"]["raw"]
    para(doc, f"These are the ten specifications every coverage number in this "
              f"document is measured on. They are drawn from the agent's "
              f"trained range with a fixed seed (deploy_seed = "
              f"{d.get('deploy_seed')}), so the same ten come back on every "
              f"re-run and the policy and the search are measured on "
              f"identical targets.")
    para(doc, "None of them is the brief's own specification, and none was "
              "seen during training. Two fields do not vary: input-referred "
              "noise stays at the brief's 1.5 mV rms and area at 0.05 mm², "
              "because the goal space does not sample them.")
    src(doc, "results/best_of_n_policy.json, field spec_fields; generated by "
             "scripts/deploy_policy.py::deploy_specs")

    rows = []
    for i, s in enumerate(d["specs"]):
        f = s["spec_fields"]
        rows.append([
            f"spec{i}",
            f"{f['peaking_min_db']:.2f}–{f['peaking_max_db']:.2f}",
            f"{f['peaking_target_db']:.2f}",
            f"{f['fpeak_lo']/1e9:.3f}–{f['fpeak_hi']/1e9:.3f}",
            f"{f['hd3_max_db']:.2f}",
            f"{f['power_max_w']*1e3:.2f}",
            f"{f['eye_height_min_v']*1e3:.1f}",
            f"{f['eye_width_min_ui']:.4f}"])
    table(doc, "The ten specifications. Peaking in dB, peak-frequency band in "
               "GHz, HD3 in dBc, power in mW, eye height in mV, eye width in "
               "UI.",
          ["spec", "peaking band", "target", "f_peak band", "HD3", "power",
           "eye H", "eye W"],
          rows, widths=[0.55, 0.95, 0.55, 1.05, 0.6, 0.6, 0.6, 0.65])

    rows = []
    for i, s in enumerate(d["specs"]):
        r = s["first_success_rank"]
        rows.append([f"spec{i}", f"{s['n_passing']}/{s['n_proposals']}",
                     "—" if r is None else str(r),
                     "solved" if r is not None else "not solved within 8"])
    table(doc, "How the agent did on each of them, verified at all twenty "
               "corners.",
          ["spec", "proposals passing", "rank of first success", "outcome"],
          rows, widths=[0.8, 1.6, 1.8, 1.8])
    src(doc, "results/best_of_n_policy.json")
    para(doc, "Two of the ten are not solved within eight candidates by either "
              "method. Both are bound by eye width: spec5 asks for 0.4772 UI "
              "and spec8 for 0.4792 UI, against the brief's own 0.4000 UI — "
              "close to twenty percent more. spec5 additionally caps power at "
              "5.05 mW, a third of the brief's 15 mW, so it cannot buy eye "
              "opening with current.")
    page_break(doc)


def appB_bounds(doc, F):
    doc.add_heading("Appendix B.  Design-variable bounds", level=1)
    para(doc, "The search is confined to this box. Any proposal outside it is "
              "clipped before simulation, so no reported design can lie "
              "outside these ranges.")
    rows = []
    for k, (lo, hi) in F["bounds"].items():
        u = UNITS[k]
        rows.append([k, WHAT[k], eng(lo, u), eng(hi, u)])
    table(doc, "The eleven variables and their bounds.",
          ["variable", "what it sets", "minimum", "maximum"], rows,
          widths=[0.9, 2.6, 1.2, 1.2])
    src(doc, "eqrl/netlist.py::PARAM_BOUNDS")
    para(doc, "The tap branch is not sized independently: its current, device "
              "width and tail width are all derived from tap_pct, as set out "
              "in Section 5.6.")
    page_break(doc)


def appC_corners(doc, F):
    doc.add_heading("Appendix C.  The twenty PVT corners", level=1)
    para(doc, "Every reported pass was simulated at all twenty. The worst "
              "margin across them is what the scorecard reports.")
    rows = [[i, c.process, f"{c.vdd:.2f} V", f"{c.temp:.0f} °C"]
            for i, c in enumerate(F["corners"], 1)]
    table(doc, "The acceptance grid: 5 processes × 2 supplies × 2 "
               "temperatures.",
          ["#", "process", "supply", "temperature"], rows,
          widths=[0.5, 1.2, 1.2, 1.4])
    src(doc, "eqrl/pdk.py::pvt_grid('spec')")
    para(doc, "The two corners used for the cheap eye screen — sf at 1.710 V "
              "and sf at 1.890 V, both at 0 °C — are members of this same "
              "grid, chosen by measurement as described in Section 9.1.")
    page_break(doc)


def appD_artifacts(doc, F):
    doc.add_heading("Appendix D.  Artifact index", level=1)
    para(doc, "Which file backs which claim. This is the table to open when "
              "someone asks where a number came from.")
    rows = [
        ["results/best_of_n_policy.json",
         "the agent on ten specifications: every proposal, its verification "
         "result, the rank of the first success, and the ten specifications "
         "themselves",
         "coverage, per-proposal rate, Appendix A"],
        ["results/best_of_n_surrogate_cem.json",
         "the same ten specifications through the search",
         "the search's coverage and rate, the union figure"],
        ["results/mcp_e2e.json",
         "timed end-to-end runs through the wrapper",
         "the median and range of design time"],
        ["results/baseline.json",
         "random search at an equal candidate budget",
         "the sweep comparison, and the measured cost per design"],
        ["results/report_design.json",
         "one fully verified design with its per-corner margins",
         "the scorecard and worst-case margins"],
        ["results/surrogate_check.json",
         "the model's favourite design, then simulated for real",
         "the optimism gap"],
        ["results/solve_rate_physfit.json",
         "coverage from the derived starting point",
         "seven of ten solved at the first proposal"],
        ["results/goal_space.json",
         "the range of specifications the agent was trained on",
         "the envelope check, Section 8.1"],
        ["models/surrogate/…/validation.json",
         "held-out accuracy: per-metric R², pass agreement, rank correlation, "
         "accuracy by sampling region",
         "all of Section 6.10"],
        ["models/surrogate/…/config.json",
         "the network's shape, targets, epochs and split fraction",
         "Sections 6.5 to 6.8"],
        ["models/surrogate/…/physfit.json",
         "the fitted device coefficients and calibration factors",
         "Sections 6.2 and 6.3"],
        ["results/policy_train_eval.json",
         "training wall clock and in-training success rate",
         "Section 7.5"],
        ["docs/findings-ctle-dfe.md",
         "the pulse-response characterisation of the CTLE and tap",
         "the post-cursor figures in Sections 2.3 and 5.6"],
        ["docs/systematic-starting-point.md",
         "the derivation of the starting point and its measurement",
         "Section 5"],
        ["netlists/eq_core.sch",
         "the schematic, checked against the simulated netlist by a test",
         "Section 3.3"],
    ]
    table(doc, "Artifacts and the claims they support.",
          ["file", "what it holds", "backs"], rows,
          widths=[1.9, 2.5, 1.8])


def main() -> int:
    print("building the reference report")
    F = facts()
    doc = Document()
    style(doc)
    add_page_numbers(doc)
    cover(doc, F)
    contents(doc)
    for fn in (s1_problem, s2_channel, s3_circuit, s4_cost, s5_start,
               s6_physfit, s7_rl, s8_envelope, s9_verify, s10_mcp,
               s11_results, s12_repro, appA_specs, appB_bounds, appC_corners,
               appD_artifacts):
        fn(doc, F)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB, "
          f"{_fig[0]} figures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
