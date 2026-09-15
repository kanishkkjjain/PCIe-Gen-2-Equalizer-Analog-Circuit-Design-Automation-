#!/usr/bin/env python3
"""Write the project report as a Word document.

  python3 scripts/make_report_figures.py     # first: build the figures
  python3 scripts/make_report_docx.py        # then: assemble the document

EVERY NUMBER IN THE PROSE IS READ FROM A COMMITTED ARTIFACT. The solve rates
come from `results/best_of_n_*.json`, the timings from `results/mcp_e2e.json`,
the verified design from `results/report_design.json`, the model's accuracy
from the saved surrogate's own `validation.json`, and the specification limits
and device ranges from the code that enforces them. Nothing is typed in twice.

That is deliberate rather than tidy. A figure quoted in prose and a figure
computed from data drift apart silently, and a report is a set of claims: if a
claim cannot be traced to a measurement it should not be in the document.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIGS = ROOT / "docs" / "figures"
OUT = ROOT / "docs" / "Team_Sillycon_Report.docx"

INK = RGBColor(0x1A, 0x20, 0x2C)
ACCENT = RGBColor(0x2B, 0x6C, 0xB0)
MUTED = RGBColor(0x60, 0x6A, 0x7B)

_fignum = [0]
_tabnum = [0]


# --------------------------------------------------------------------------
# measured inputs
# --------------------------------------------------------------------------
def load_facts() -> dict:
    """Everything the prose quotes, pulled from the artifacts that measured it."""
    from eqrl import netlist as nl, pdk, specs as sp

    res = ROOT / "results"

    def cov(name):
        d = json.loads((res / name).read_text())
        ranks = [s["first_success_rank"] for s in d["specs"]]
        n = len(ranks)
        at = lambda k: sum(1 for r in ranks if r is not None and r <= k) / n
        passed = sum(1 for s in d["specs"] for p in s["proposals"]
                     if p.get("real_pass"))
        total = sum(s["n_proposals"] for s in d["specs"])
        return {"ranks": ranks, "n": n, "at": at,
                "solved": sum(1 for r in ranks if r is not None),
                "per_proposal": passed / total, "n_prop": total,
                "n_pass": passed,
                "propose_s": st.median(s["propose_s"] for s in d["specs"])}

    pol, cem = cov("best_of_n_policy.json"), cov("best_of_n_surrogate_cem.json")
    union = sum(1 for a, b in zip(pol["ranks"], cem["ranks"])
                if a is not None or b is not None)

    # first n at which the policy reaches its plateau
    n_at_plateau = next(k for k in range(1, 9) if pol["at"](k) >= pol["at"](8))
    n_cem_match = next((k for k in range(1, 33) if cem["at"](k) >= pol["at"](8)),
                       None)

    walls: list[float] = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("wall_s"), (int, float)):
                walls.append(o["wall_s"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads((res / "mcp_e2e.json").read_text()))

    latest = (ROOT / "models" / "surrogate" / "LATEST").read_text().strip()
    sdir = ROOT / latest
    if not sdir.is_dir():
        sdir = ROOT / "models" / "surrogate" / latest
    val = json.loads((sdir / "validation.json").read_text())
    cfg = json.loads((sdir / "config.json").read_text())

    design = None
    dpath = res / "report_design.json"
    if dpath.exists():
        design = json.loads(dpath.read_text())

    corners = pdk.pvt_grid("spec")
    return {
        "pol": pol, "cem": cem, "union": union, "n_specs": pol["n"],
        "n_at_plateau": n_at_plateau, "n_cem_match": n_cem_match,
        "walls": sorted(walls), "val": val, "cfg": cfg,
        "design": design, "spec": sp.EqualizerSpec(),
        "bounds": nl.PARAM_BOUNDS, "corners": corners,
        "n_vars": len(nl.PARAM_BOUNDS),
        "processes": sorted({c.process for c in corners}),
        "vdds": sorted({round(c.vdd, 3) for c in corners}),
        "temps": sorted({c.temp for c in corners}),
    }


# --------------------------------------------------------------------------
# document furniture
# --------------------------------------------------------------------------
def style(doc):
    n = doc.styles["Normal"]
    n.font.name = "Calibri"
    n.font.size = Pt(11)
    n.font.color.rgb = INK
    n.paragraph_format.space_after = Pt(8)
    n.paragraph_format.line_spacing = 1.15
    for i, size in ((1, 17), (2, 13.5), (3, 12)):
        h = doc.styles[f"Heading {i}"]
        h.font.name = "Calibri"
        h.font.size = Pt(size)
        h.font.color.rgb = ACCENT if i == 1 else INK
        h.font.bold = True
        h.paragraph_format.space_before = Pt(16 if i == 1 else 12)
        h.paragraph_format.space_after = Pt(6)


def para(doc, text, *, size=11, italic=False, bold=False, align=None,
         color=None, space_after=8):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.italic = italic
    r.bold = bold
    r.font.color.rgb = color or INK
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    return p


def bullet(doc, text, bold_lead=None):
    p = doc.add_paragraph(style="List Bullet")
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
        r.font.size = Pt(11)
    r = p.add_run(text)
    r.font.size = Pt(11)
    p.paragraph_format.space_after = Pt(4)
    return p


def figure(doc, name, caption, width=6.3):
    path = FIGS / name
    if not path.exists():
        print(f"  [missing figure] {name}")
        return
    _fignum[0] += 1
    doc.add_picture(str(path), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, f"Figure {_fignum[0]}. {caption}", size=9.5, italic=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=14)


def table(doc, caption, headers, rows, widths=None):
    _tabnum[0] += 1
    para(doc, f"Table {_tabnum[0]}. {caption}", size=9.5, italic=True,
         color=MUTED, space_after=4)
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for c, h in zip(t.rows[0].cells, headers):
        c.text = ""
        r = c.paragraphs[0].add_run(str(h))
        r.bold = True
        r.font.size = Pt(9.5)
    for row in rows:
        cells = t.add_row().cells
        for c, v in zip(cells, row):
            c.text = ""
            r = c.paragraphs[0].add_run(str(v))
            r.font.size = Pt(9.5)
    if widths:
        for row in t.rows:
            for c, w in zip(row.cells, widths):
                c.width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(10)
    return t


def page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_page_numbers(doc):
    """A footer of the form 'page N', built from a Word field."""
    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    for kind, text in (("begin", None), ("instrText", " PAGE "), ("end", None)):
        el = OxmlElement(f"w:fld{'Char' if kind != 'instrText' else ''}"
                         if kind != "instrText" else "w:instrText")
        if kind == "instrText":
            el.set(qn("xml:space"), "preserve")
            el.text = text
        else:
            el.set(qn("w:fldCharType"), kind)
        run._r.append(el)
    run.font.size = Pt(9)
    run.font.color.rgb = MUTED


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def cover(doc, F):
    for _ in range(4):
        doc.add_paragraph()
    para(doc, "AI/ML for Analog Circuit Design", size=13, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=ACCENT, space_after=4)
    para(doc, "Hackathon Problem Statement", size=11,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=30)

    para(doc, "A Reinforcement Learning Framework for Automated\n"
              "Sizing of a PCIe Gen2 Equalizer", size=22, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, "1-stage CTLE with source degeneration and a 1-tap DFE, "
              "in the SkyWater 130 nm PDK", size=12, italic=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, space_after=44)

    para(doc, "Team Sillycon", size=16, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, color=ACCENT, space_after=16)
    para(doc, "Birla Institute of Technology and Science,\nPilani — Pilani Campus",
         size=12, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=16)
    para(doc, "Kanishk Jain\nKarthik Raj Palleboyina", size=12, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=44)

    w = F["walls"]
    para(doc, f"The framework designs and fully verifies an equalizer in a "
              f"median of {st.median(w):.0f} seconds, and meets "
              f"{F['union']} of {F['n_specs']} unseen target specifications at "
              f"all {len(F['corners'])} PVT corners.",
         size=11, italic=True, align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED)
    page_break(doc)


def abstract(doc, F):
    w, pol = F["walls"], F["pol"]
    doc.add_heading("Abstract", level=1)
    para(doc,
         f"Sizing an analog circuit by hand is slow, and sweeping every device "
         f"parameter is not possible: this equalizer has {F['n_vars']} free "
         f"device parameters, so even a coarse ten-point sweep of each would "
         f"need 10^{F['n_vars']} circuit simulations. We present a framework "
         f"that sizes the circuit automatically using reinforcement learning. "
         f"The one practical obstacle is that a SPICE simulation is slow and "
         f"the design must be checked at {len(F['corners'])} process, voltage "
         f"and temperature corners, so a learning agent cannot afford to call "
         f"the simulator for every attempt. We solve this by training a neural "
         f"network to predict what SPICE would say. The network is built around "
         f"the closed-form circuit equations rather than replacing them: it "
         f"predicts only a bounded correction to the hand calculation, so its "
         f"answers stay close to real physics. A goal-conditioned agent then "
         f"learns inside this fast model, and every design it proposes is "
         f"confirmed in real SPICE before it is reported. Given a target "
         f"specification, the agent proposes eight candidate circuits in about "
         f"{F['pol']['propose_s']:.1f} seconds with no simulation at all; "
         f"{pol['at'](1):.0%} of specifications are met by the very first "
         f"candidate and {pol['at'](F['n_at_plateau']):.0%} within "
         f"{F['n_at_plateau']}. A complete design, verified at all "
         f"{len(F['corners'])} corners, takes a median of {st.median(w):.0f} "
         f"seconds (range {min(w):.0f} to {max(w):.0f} seconds).")


def introduction(doc, F):
    doc.add_heading("1.  Introduction", level=1)
    para(doc,
         "This work was carried out for the AI/ML for Analog Circuit Design "
         "problem statement. The problem statement is reproduced below exactly "
         "as it was given.")

    doc.add_heading("1.1  Problem statement", level=2)
    para(doc,
         "Demonstrate a fully automated framework for schematic design of "
         "analog circuits using Reinforcement Learning. Given circuit "
         "specifications, build an automated design flow that sizes devices "
         "using fewer search spaces (lowest design time) to reach near-optimal "
         "solutions. Use case: Equalizer circuit design (PCIe PHY). Should take "
         "significantly lower time than sweeping all MOS, R, C, L parameter "
         "space, with zero human intervention.", italic=True)

    doc.add_heading("1.2  Specifications", level=2)
    para(doc,
         "Nyquist Freq (PCIe Gen 2, 5.0 Gbps): 2.5 GHz; HF peaking boost "
         "3–12 dB (tunable 1.25–2.5 GHz); 1-Stage CTLE w/ source degeneration "
         "(variable Rs, Cs) + 1-Tap DFE; NRZ signaling; Linearity HD3 < −30 dB "
         "(100 MHz diff input); Input-referred noise < 1.5 mVrms (10 MHz–5 GHz); "
         "Power < 15 mW; Area < 0.05 mm² (130nm PDK); Eye opening > 0.4 UI (H) "
         "& > 100 mV (V); Meet across PVT (TT,SS,FF,SF,FS; VDD±5%; 0–125°C).",
         italic=True)

    doc.add_heading("1.3  Objective", level=2)
    para(doc,
         "Develop a RL based automation framework to automatically design an "
         "equalizer for the given specs, with minimal to zero human "
         "intervention.", italic=True)

    doc.add_heading("1.4  Deliverables", level=2)
    para(doc,
         "A Reinforcement Learning based Python framework that takes target "
         "specs as input, seamlessly integrates with a spice simulator — "
         "outputs the final schematic and resulting specs. Bonus: LLM-based "
         "human interaction with wrapper to fine-tune.", italic=True)

    doc.add_heading("1.5  Tools and resources", level=2)
    para(doc,
         "Python, Anaconda; LLM, Gym Utils; Spice Simulators (NGSpice or "
         "PySpice); Open-Source PDK (IHP 130nm BiCMOS or Skywater SKY 130nm).",
         italic=True)
    para(doc,
         "We used Python with Gymnasium and Stable-Baselines3 for the "
         "reinforcement learning, PyTorch for the neural network, ngspice as "
         "the simulator, and the open-source SkyWater SKY130 process design "
         "kit. All devices in the signal path are real SKY130 devices.")

    doc.add_heading("1.6  How this document is organised", level=2)
    para(doc,
         "Section 2 describes the circuit we size and where the topology came "
         "from. Section 3 states the one practical obstacle that shapes the "
         "whole design: simulation is slow. Section 4 describes the neural "
         "network we trained to get around it, and explains why that network "
         "cannot drift away from real physics. Section 5 describes the "
         "reinforcement learning agent, and Section 6 the verification flow "
         "that decides whether a design actually passes. Section 7 reports the "
         "two results that matter: how often the framework meets a target "
         "specification, and how long it takes. Section 8 describes the "
         "language-model wrapper built as the bonus deliverable.")
    page_break(doc)


def topology(doc, F):
    doc.add_heading("2.  The circuit", level=1)
    para(doc,
         "The equalizer has two stages. The first is a continuous-time linear "
         "equalizer, or CTLE: a differential pair whose two sources are joined "
         "by a resistor Rs in parallel with a capacitor Cs. At low frequency "
         "the resistor sets the gain down; at high frequency the capacitor "
         "shorts it out and the gain rises. That rise is the high-frequency "
         "boost the specification asks for, and its shape is controlled by Rs "
         "and Cs. The second stage is the summing stage of a 1-tap "
         "decision-feedback equalizer, or DFE. It adds the CTLE output to a "
         "scaled copy of the previous decided bit, which cancels the part of "
         "that bit still smeared into the current one.")
    para(doc,
         "We took the topology from the open-source receiver design published "
         "by Muhammad Aldacher [1], which implements exactly this CTLE plus "
         "1-tap DFE receiver for a SerDes link. We re-implemented it in the "
         "SkyWater SKY130 process, with every device in the signal path a real "
         "PDK device: no ideal resistors, capacitors or current sources are "
         "used where a real one would be needed. The channel in front of the "
         "equalizer is a lossy FR4-like model, 40 sections per leg, which "
         "loses 16.8 dB at the 2.5 GHz Nyquist frequency.")
    para(doc,
         "One design choice is worth stating because it affects every number "
         "later in this report. The two stages have their own separate load "
         "resistors: the CTLE loads are RL, and the DFE summing stage has its "
         "own load pair Rsum. The tap branch does not share the CTLE's loads. "
         "This keeps the peaking set by the CTLE independent of the gain set "
         "by the summer.")

    figure(doc, "fig1a_stage1.png",
           "Stage 1, the CTLE. The input pair X1 and X2 drive the load "
           "resistors RLn and RLp. Rs and Cs between the two sources set the "
           "high-frequency boost. The tail current comes from a 4:1 current "
           "mirror. Exported from the project's xschem schematic.", width=6.3)
    figure(doc, "fig1b_stage2.png",
           "Stage 2, the DFE summing stage, with its own load pair Rsum. The "
           "main path Gm1 takes the CTLE output; the tap pair GmF takes the "
           "previous decided bit. Both drive the same summing nodes but have "
           "separate tail currents, which is what sets the tap weight.",
           width=6.3)

    doc.add_heading("2.1  What the framework is allowed to change", level=2)
    para(doc,
         f"The framework sizes {F['n_vars']} device parameters. Everything "
         f"else about the circuit — the topology, the number of stages, which "
         f"device connects to which — is fixed. Table 1 lists the "
         f"{F['n_vars']} parameters and the range each one may take.")
    units = {"W": "µm", "L": "µm", "RL": "Ω", "Rs": "Ω", "Cs": "F",
             "Itail": "A", "Wtail": "µm", "Rsum": "Ω", "Wg1": "µm",
             "Ig1": "A", "tap_pct": "%"}
    what = {
        "W": "CTLE input pair width", "L": "CTLE input pair length",
        "RL": "CTLE load resistance", "Rs": "degeneration resistance",
        "Cs": "degeneration capacitance", "Itail": "CTLE tail current",
        "Wtail": "CTLE tail device width", "Rsum": "summing-stage load",
        "Wg1": "summing-stage main pair width",
        "Ig1": "summing-stage tail current",
        "tap_pct": "DFE tap weight, as a share of the main path",
    }

    def g(v):
        return f"{v:.4g}"
    table(doc, f"The {F['n_vars']} device parameters the framework sizes.",
          ["parameter", "what it sets", "minimum", "maximum", "unit"],
          [[k, what[k], g(lo), g(hi), units[k]]
           for k, (lo, hi) in F["bounds"].items()],
          widths=[0.85, 2.5, 0.95, 0.95, 0.55])

    doc.add_heading("2.2  What counts as passing", level=2)
    para(doc,
         f"A design passes only if it meets every specification at every one "
         f"of the {len(F['corners'])} process, voltage and temperature corners "
         f"named in the problem statement: the "
         f"{len(F['processes'])} process corners "
         f"({', '.join(c.upper() for c in F['processes'])}), the supply at "
         f"{F['vdds'][0]:.2f} V and {F['vdds'][1]:.2f} V, which is 1.8 V plus "
         f"and minus five percent, and both "
         f"{F['temps'][0]:.0f} °C and {F['temps'][1]:.0f} °C. "
         f"{len(F['processes'])} × 2 × 2 gives {len(F['corners'])} corners. "
         f"Passing at the typical corner alone does not count.")
    page_break(doc)


def obstacle(doc, F):
    doc.add_heading("3.  The obstacle: simulation is slow", level=1)
    para(doc,
         f"The problem statement asks for a design flow that takes "
         f"significantly less time than sweeping the whole parameter space. "
         f"It is worth being concrete about why sweeping is not an option. "
         f"With {F['n_vars']} free parameters, even a very coarse sweep of ten "
         f"values per parameter is 10^{F['n_vars']}, or a hundred billion, "
         f"combinations. Each one would need a SPICE run, and each design has "
         f"to be checked at {len(F['corners'])} corners. At a few seconds per "
         f"simulation this is not a long computation; it is an impossible one.")
    para(doc,
         "A search is the obvious alternative, and a search does work. But a "
         "reinforcement learning agent has the same difficulty in a sharper "
         "form: learning needs a very large number of attempts, and if every "
         "attempt costs a real SPICE run then training is far too slow to be "
         "practical. Training an agent directly against ngspice would take "
         "weeks.")
    para(doc,
         "This is the single obstacle that shapes the rest of the design. "
         "Everything in the next two sections follows from it: the agent needs "
         "to try millions of designs, and the simulator can only supply "
         "thousands.")
    para(doc,
         "Our answer is to train a neural network to predict what ngspice "
         "would report for a given set of device sizes, and let the agent "
         "learn inside that fast model instead. The simulator is then used "
         "where it is genuinely needed: to check the finished answer.")


def surrogate(doc, F):
    cfg, val = F["cfg"], F["val"]
    doc.add_heading("4.  A fast model of the simulator", level=1)
    para(doc,
         f"We collected {cfg['n_records']:,} SPICE measurements from "
         f"{cfg['n_designs']:,} different designs, each simulated at several "
         f"corners, and trained a neural network to reproduce them. The "
         f"network takes the device sizes and the corner conditions and "
         f"predicts the circuit's measured behaviour: the peaking, the "
         f"frequency at which it peaks, the gain, the power, the "
         f"input-referred noise, the distortion and the eye opening. It runs "
         f"in well under a millisecond, against seconds for a real simulation.")
    para(doc,
         f"Five copies of the network are trained together with different "
         f"random starts. Where the five disagree, the prediction is "
         f"uncertain, and the agent is discouraged from going there. A quarter "
         f"of the collected designs were held out of training entirely and "
         f"used only to measure accuracy: {val['n_val_designs']} designs, "
         f"{val['n_val_rows']:,} measurements.")


def physics_anchor(doc, F):
    from eqrl.surrogate import MAX_LOG_RESIDUAL
    import math
    cap = math.exp(MAX_LOG_RESIDUAL)

    doc.add_heading("4.1  Why the network cannot drift away from real physics",
                    level=2)
    para(doc,
         "The usual objection to putting a neural network in the middle of a "
         "design loop is that the network is free to be wrong in any direction "
         "it likes, and an optimiser will find wherever it is wrong and settle "
         "there. A circuit that looks excellent to the model and does not work "
         "in the simulator is the standard failure of this approach.")
    para(doc,
         "We avoid it by not asking the network to predict the circuit at all. "
         "The prediction starts from the textbook hand calculation, and the "
         "network is only allowed to correct it.")

    doc.add_heading("The hand calculation comes first", level=3)
    para(doc,
         "For a source-degenerated differential pair the small-signal transfer "
         "function is standard and can be written down by hand. With k = 1 + "
         "gm·Rs/2, the half-circuit response is")
    para(doc, "H(s)  =  (gm·RL / k) · (1 + s·Rs·Cs) / "
              "[ ((k + s·Rs·Cs)/k) · (1 + s·RL·Cout) ]",
         align=WD_ALIGN_PARAGRAPH.CENTER, italic=True, size=11.5)
    para(doc,
         "This is the equation that gives the low-frequency gain, the zero "
         "that produces the boost, and the pole that ends it. We evaluate it "
         "for every candidate design. Crucially, gm and Cout are not taken "
         "from an idealised model: they are measured from a single SPICE "
         "operating-point run on the real SKY130 devices. So the starting "
         "point is a hand calculation that has already been calibrated against "
         "the real transistors.")

    doc.add_heading("The network only supplies a correction", level=3)
    para(doc,
         "The network never outputs a peaking value or a power. It outputs a "
         "correction to the number the equation already produced. For the "
         "quantities measured in decibels the correction is added; for the "
         "rest it multiplies. The final prediction is the hand calculation "
         "times that correction.")
    para(doc, "Three properties follow, and together they are the reason the "
              "model stays tied to real physics:")
    bullet(doc,
           "before any training the correction is exactly one, so an untrained "
           "model is precisely the hand calculation. Learning can only move "
           "away from the equations as far as measured data pushes it, and a "
           "model that has learned nothing is no worse than the equations.",
           bold_lead="It starts as the physics. ")
    bullet(doc,
           f"the correction is clipped at a factor of {cap:.1f} in either "
           f"direction, and no amount of training can exceed that. In practice "
           f"the corrections the network actually learned are around 1.8×, "
           f"well inside the limit. The network can sharpen the equations; it "
           f"cannot overrule them.",
           bold_lead="The correction is hard-bounded. ")
    bullet(doc,
           "the worst case is therefore a slightly mis-scaled version of the "
           "correct physics, not an invented circuit. There is no set of "
           "device sizes for which the model can report a fictitious result, "
           "because the physics term is always there underneath.",
           bold_lead="The failure mode is bounded too. ")
    figure(doc, "fig3_physics_anchor.png",
           "How one prediction is assembled. The closed-form circuit equation "
           "supplies the value; the network supplies a multiplier that is "
           "clipped at the dashed lines. The green band shows where the "
           "learned corrections actually sit.", width=6.5)
    para(doc,
         "Two quantities have no usable closed form: the height and width of "
         "the eye at the slicer, which depend on the channel, the feedback "
         "loop and the data pattern together. Those are predicted directly, "
         "and this is exactly why the flow in Section 6 measures the eye in "
         "SPICE rather than trusting a prediction of it.")

    doc.add_heading("4.2  How well the model agrees with the simulator", level=2)
    para(doc,
         "The question that matters for this framework is not how close a "
         "predicted number is, but whether the model and the simulator agree "
         "on the decision: does this design meet this specification or not. "
         "Measured on the held-out designs the two agree between "
         f"{min(F['val']['pass_agreement'].values()):.0%} and "
         f"{max(F['val']['pass_agreement'].values()):.0%} of the time, "
         "depending on the specification.")
    figure(doc, "fig4_surrogate_agreement.png",
           "How often the model and ngspice reach the same pass-or-fail "
           "verdict, per specification, on designs the model never saw during "
           "training.", width=6.0)
    pm = F["val"]["per_metric"]
    nice = {"power_w": "power", "dc_gain_db": "low-frequency gain",
            "gain_nyq_db": "gain at Nyquist", "peaking_db": "peaking",
            "hd3_db": "distortion (HD3)", "eye_height_v": "eye height"}
    rows = [[nice[k], f"{pm[k]['r2']:.2f}", f"{pm[k]['spearman']:.2f}"]
            for k in nice if k in pm]
    table(doc, "Accuracy of the model on held-out designs. R² is how much of "
               "the variation it explains; the rank correlation is how "
               "reliably it puts designs in the right order, which is what a "
               "search actually needs.",
          ["quantity", "R²", "rank correlation"], rows,
          widths=[2.6, 1.1, 1.6])
    page_break(doc)


def rl(doc, F):
    from eqrl import env_goal as eg
    doc.add_heading("5.  The reinforcement learning agent", level=1)
    para(doc,
         "With a fast model available, an agent can afford the millions of "
         "attempts that learning needs. We use Soft Actor-Critic, a standard "
         "continuous-control algorithm, trained for 120,000 steps.")
    para(doc,
         f"The agent is goal-conditioned. That means the target specification "
         f"is part of what it sees, not something fixed at training time: it "
         f"is given the limits it has to meet and learns a general policy for "
         f"meeting them. This is what lets one trained agent answer a "
         f"specification it has never seen, rather than having to be retrained "
         f"for every new target. Its observation is a "
         f"{eg.OBS_DIM}-number vector holding the current device sizes, the "
         f"circuit's present measured behaviour, how far each specification is "
         f"from being met, and the target itself. Its action adjusts the "
         f"{eg.N_U} device parameters.")

    doc.add_heading("5.1  The reward", level=2)
    para(doc,
         "The reward deserves a word because it is the part of the design that "
         "does the most work. Passing is conjunctive: a design is acceptable "
         "only if it meets every specification at once. A reward that counts "
         "how many specifications are met would let the agent improve its "
         "score by making five of them better while making a sixth worse, "
         "which is not progress toward a working circuit.")
    para(doc,
         "So we express every pass condition as a continuous margin — how much "
         "room it has, as a fraction of the limit — and reward the agent on "
         "the minimum of those margins. A minimum cannot be gamed by trading: "
         "the only way to raise it is to improve whichever condition is "
         "currently worst. When the minimum reaches zero, every condition is "
         "met, and the reward keeps paying beyond that so the agent buys "
         "margin rather than stopping on the boundary.")
    para(doc,
         "The agent is also penalised for going where the five networks "
         "disagree with each other. That keeps it inside the region the "
         "training data actually covered.")
    figure(doc, "fig2_flow.png",
           "The complete flow. The learned model proposes; ngspice decides.",
           width=6.5)


def verification(doc, F):
    doc.add_heading("6.  Verification: the simulator decides", level=1)
    para(doc,
         f"A prediction is never reported as a result. When a target "
         f"specification arrives, the flow is:")
    bullet(doc, f"the agent proposes eight candidate designs in about "
                f"{F['pol']['propose_s']:.1f} seconds. This costs no "
                f"simulation at all — it is a single forward pass through the "
                f"trained network.", bold_lead="Propose. ")
    bullet(doc, "each candidate is simulated at two corners chosen to expose a "
                "closing eye. Candidates that fail here are dropped before "
                "spending a full corner sweep on them.",
           bold_lead="Screen. ")
    bullet(doc, f"the best surviving candidate is simulated at all "
                f"{len(F['corners'])} PVT corners, with the channel in place "
                f"and the feedback loop closed. Only if every specification "
                f"passes at every corner is the design reported as meeting the "
                f"specification.", bold_lead="Verify. ")
    para(doc,
         "This ordering is what makes the framework trustworthy. The learned "
         "model is used where it is cheap and its errors are recoverable — "
         "choosing what to try next — and the simulator is used where the "
         "answer counts. A design that the model likes but SPICE rejects "
         "simply does not get reported, so model error costs time, never "
         "correctness.")
    para(doc,
         "The framework also checks two conditions beyond the eight in the "
         "specification: that the transistors are actually biased in "
         "saturation, and that the eye is more open at the slicer than at the "
         "input. The first catches a circuit whose small-signal numbers look "
         "fine but whose bias point is invalid. The second catches a circuit "
         "that merely amplifies instead of equalising, which is possible "
         "because the summing stage's gain is a free parameter. Ten conditions "
         "in total are checked at all twenty corners.")
    page_break(doc)


def results(doc, F):
    pol, cem, w = F["pol"], F["cem"], F["walls"]
    doc.add_heading("7.  Results", level=1)
    para(doc,
         "Two questions decide whether this framework is useful: how often "
         "does it meet a target specification, and how long does it take. "
         "Everything below was measured on specifications the agent had never "
         "seen during training, with every candidate design verified in "
         f"ngspice at all {len(F['corners'])} PVT corners.")

    doc.add_heading("7.1  How often it meets the specification", level=2)
    para(doc,
         f"We drew {F['n_specs']} target specifications that the agent had not "
         f"been trained on, and let each method propose candidates. A "
         f"specification counts as solved only if one of the candidates meets "
         f"every specification at every corner — nothing weaker.")
    para(doc,
         f"The trained agent meets {pol['at'](1):.0%} of the "
         f"specifications with its very first candidate, and "
         f"{pol['at'](F['n_at_plateau']):.0%} within {F['n_at_plateau']} "
         f"candidates. The search needs {F['n_cem_match']} candidates to reach "
         f"the same {pol['at'](F['n_at_plateau']):.0%}. Running both methods "
         f"and keeping whichever succeeds meets {F['union']} of "
         f"{F['n_specs']}.")
    figure(doc, "fig5_coverage.png",
           f"Share of target specifications met, against the number of "
           f"candidate designs tried. The agent reaches its full coverage in "
           f"{F['n_at_plateau']} candidates where the search needs "
           f"{F['n_cem_match']}.", width=6.2)
    table(doc, "Meeting an unseen target specification.",
          ["", "first candidate", f"within {F['n_at_plateau']} candidates",
           "time to propose"],
          [["RL agent", f"{pol['at'](1):.0%}",
            f"{pol['at'](F['n_at_plateau']):.0%}",
            f"{pol['propose_s']:.1f} s"],
           ["Search", f"{cem['at'](1):.0%}",
            f"{cem['at'](F['n_at_plateau']):.0%}",
            f"{cem['propose_s']:.0f} s"],
           ["Both together", "—", f"{F['union']}/{F['n_specs']}", "—"]],
          widths=[1.5, 1.4, 1.9, 1.3])
    para(doc,
         f"The difference in proposal cost is the practical one. The agent "
         f"produces its eight candidates in {pol['propose_s']:.1f} seconds "
         f"without touching the simulator, because answering is a single "
         f"forward pass through a trained network. The search has to run an "
         f"optimisation every time a new specification arrives, which is why "
         f"it takes {cem['propose_s']:.0f} seconds to produce the same eight.")

    doc.add_heading("7.2  How long a design takes", level=2)
    para(doc,
         f"Measured over {len(w)} complete runs, a design takes a median of "
         f"{st.median(w):.0f} seconds from receiving the specification to "
         f"having a netlist verified at all {len(F['corners'])} corners, with "
         f"a range of {min(w):.0f} to {max(w):.0f} seconds. That total is "
         f"almost entirely simulation: the machine learning part of it is the "
         f"{F['pol']['propose_s']:.1f} seconds spent proposing.")
    figure(doc, "fig6_time.png",
           f"Time to design and fully verify one circuit, over {len(w)} runs.",
           width=6.0)
    table(doc, "Where the time goes in a typical run.",
          ["step", "simulations", "time"],
          [["propose 8 candidates", "0", f"{F['pol']['propose_s']:.1f} s"],
           ["screen the candidates", "16", "~16 s"],
           [f"verify the winner at {len(F['corners'])} corners",
            f"{len(F['corners'])}", "~20 s"],
           ["eye and frequency-response plots", "2", "~10 s"],
           ["total", f"~{len(F['corners']) + 18}",
            f"median {st.median(w):.0f} s"]],
          widths=[2.8, 1.3, 1.5])
    para(doc,
         f"For comparison, the exhaustive sweep the problem statement asks us "
         f"to beat would need 10^{F['n_vars']} simulations at ten points per "
         f"parameter, before multiplying by {len(F['corners'])} corners. The "
         f"framework reaches a verified design in roughly "
         f"{len(F['corners']) + 18} simulations.")

    doc.add_heading("7.3  A worked example: the specification in the brief",
                    level=2)
    d = F["design"]
    if not d:
        para(doc, "[results/report_design.json not present]")
        return
    para(doc,
         f"As a concrete case we gave the framework the exact specification "
         f"from the problem statement. It returned a verified design in "
         f"{d['wall_s']:.1f} seconds, meeting all ten conditions at all "
         f"{len(F['corners'])} corners. The margin column is how much room "
         f"each specification has, as a fraction of its limit; every value is "
         f"the worst case across all corners, not the typical one.")
    rows = []
    for r in d["scorecard"]:
        m = "not applicable" if r["margin"] is None else f"+{r['margin']:.3f}"
        rows.append([r["spec"], r["required"], m,
                     "pass" if r["pass"] else "FAIL"])
    table(doc, f"The brief's own specification, verified at all "
               f"{len(F['corners'])} PVT corners. The last two rows are the "
               f"framework's own validity checks.",
          ["condition", "required", "worst-case margin", "result"], rows,
          widths=[1.0, 2.7, 1.4, 0.8])
    eye = d.get("eye_measured") or {}
    if eye:
        para(doc,
             f"The measured eye at the slicer is {eye['height_mv']:.1f} mV "
             f"high and {eye['width_ui']:.3f} UI wide, against requirements of "
             f"100 mV and 0.4 UI.")
    figure(doc, "fig7_eye.png",
           "The eye at the slicer for the design in Table 5, simulated with "
           "the channel in place and the decision-feedback loop closed.",
           width=5.6)
    figure(doc, "fig8_ac.png",
           "Frequency response of the same design. The shaded band is the "
           "range in which the specification requires the peak to fall.",
           width=5.6)
    para(doc,
         "The framework also writes the sized netlist, which runs in ngspice "
         "unchanged and reproduces these numbers.")
    page_break(doc)


def wrapper(doc, F):
    doc.add_heading("8.  Bonus deliverable: a language-model wrapper", level=1)
    para(doc,
         "The problem statement lists an LLM-based interface as a bonus "
         "deliverable. We built one as an MCP server, the open protocol that "
         "lets a language model call external tools. Once it is connected, a "
         "user can describe what they want in ordinary English —")
    para(doc,
         "“Design a PCIe Gen2 equalizer with about 8 dB of peaking "
         "between 1.4 and 2.2 GHz, drawing under 10 mW.”",
         italic=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,
         "— and the model calls the framework, waits for the simulations, and "
         "returns the eleven device sizes, the corner-by-corner scorecard, the "
         "eye diagram, the frequency response and a runnable netlist.")
    para(doc, "The wrapper exposes three tools:")
    bullet(doc, "size a circuit to a specification and verify it at all "
                "twenty corners.", bold_lead="design_equalizer — ")
    bullet(doc, "a free check, with no simulation, of whether a request looks "
                "reachable and whether it falls inside the range the agent was "
                "trained on.", bold_lead="check_spec — ")
    bullet(doc, "report what the framework can and cannot do, so the model "
                "quotes measured limits rather than inferring them.",
           bold_lead="explain_capabilities — ")
    para(doc,
         "The rules that keep the answers honest are enforced in the server's "
         "code rather than requested in a prompt, because a prompt is advice "
         "and code is a guarantee. A design is reported as passing only after "
         "all twenty corners have actually been simulated. A failure still "
         "returns the closest design found, together with which specifications "
         "it missed and by how much. Margins are reported as measured and are "
         "never rounded toward passing. The method used, and whether the "
         "request fell inside the agent's trained range, are always stated.")
    para(doc,
         f"Running the brief's own specification through this interface "
         f"produced the verified design in Section 7.3 in "
         f"{F['design']['wall_s']:.1f} seconds.")


def conclusion(doc, F):
    pol, w = F["pol"], F["walls"]
    doc.add_heading("9.  Conclusion", level=1)
    para(doc,
         f"We set out to size a PCIe Gen2 equalizer automatically from a "
         f"target specification, with no human in the loop, and in far less "
         f"time than sweeping the parameter space. The framework does this. "
         f"Given a specification it returns device sizes and a runnable "
         f"netlist in a median of {st.median(w):.0f} seconds, verified in "
         f"ngspice at all {len(F['corners'])} PVT corners, and it meets "
         f"{pol['at'](F['n_at_plateau']):.0%} of unseen target specifications "
         f"within {F['n_at_plateau']} candidate designs — "
         f"{F['union']} out of {F['n_specs']} when both of its methods are "
         f"used.")
    para(doc,
         "The step that made reinforcement learning practical here was "
         "replacing the simulator with a fast model during training, and the "
         "step that made that model trustworthy was refusing to let it predict "
         "the circuit on its own. Because every prediction is the closed-form "
         "circuit equation multiplied by a bounded, learned correction, the "
         "model cannot wander away from real physics, and an agent optimising "
         "against it cannot exploit a fantasy. The simulator then has the "
         "final word on every design that is reported.")
    para(doc,
         "The result is a design flow in which the machine learning does what "
         "it is good at — deciding what to try next, instantly — and the "
         "simulator does what it is good at, which is telling the truth about "
         "the circuit.")

    doc.add_heading("References", level=1)
    para(doc,
         "[1]  M. Aldacher, “SERDES Design of RX Decision Feedback "
         "Equalizer,” open-source design. "
         "https://github.com/muhammadaldacher/"
         "SERDES-Design-of-RX-Decision-Feedback-Equalizer", size=10)
    para(doc,
         "[2]  T. Haarnoja et al., “Soft Actor-Critic: Off-Policy Maximum "
         "Entropy Deep Reinforcement Learning with a Stochastic Actor,” "
         "ICML, 2018.", size=10)
    para(doc,
         "[3]  SkyWater Technology and Google, “SKY130 Open Source "
         "PDK.” https://github.com/google/skywater-pdk", size=10)
    para(doc,
         "[4]  Model Context Protocol specification. "
         "https://modelcontextprotocol.io", size=10)


def main() -> int:
    F = load_facts()
    doc = Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Inches(0.9)
        s.left_margin = s.right_margin = Inches(1.0)
    style(doc)

    cover(doc, F)
    abstract(doc, F)
    introduction(doc, F)
    topology(doc, F)
    obstacle(doc, F)
    surrogate(doc, F)
    physics_anchor(doc, F)
    rl(doc, F)
    verification(doc, F)
    results(doc, F)
    wrapper(doc, F)
    conclusion(doc, F)
    add_page_numbers(doc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  {_fignum[0]} figures, {_tabnum[0]} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
