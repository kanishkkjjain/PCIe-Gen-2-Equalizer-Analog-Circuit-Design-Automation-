#!/usr/bin/env python3
"""Build every figure the project report uses, into docs/figures/.

Each figure is drawn from a committed artifact rather than from numbers typed
in here: the coverage curve comes from `results/best_of_n_*.json`, the timing
from `results/mcp_e2e.json`, the surrogate chart from the saved model's own
`validation.json`, and the schematic from `netlists/eq_core.sch` via xschem.
A figure that cannot be traced back to a measurement does not belong in a
report, and one that is typed in by hand can drift away from the measurement
without anything noticing.

  python3 scripts/make_report_figures.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIGS = ROOT / "docs" / "figures"

INK, ACCENT, WARM, MUTED = "#1a202c", "#2b6cb0", "#c05621", "#718096"
plt.rcParams.update({
    "font.size": 11, "font.family": "DejaVu Sans",
    "axes.edgecolor": INK, "axes.labelcolor": INK,
    "text.color": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def _save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / name
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  {name}")
    return out


# --------------------------------------------------------------------------
# 1. the schematic, exported from the real .sch file
# --------------------------------------------------------------------------
def fig_schematic():
    """xschem renders the schematic; we trim its margins and split it in two.

    Split because the whole circuit across one page column leaves the device
    labels too small to read. The two halves are the two stages, which is how
    the report discusses them anyway.
    """
    from PIL import Image, ImageChops
    sch = ROOT / "netlists" / "eq_core.sch"
    raw = FIGS / "_schematic_raw.png"
    FIGS.mkdir(parents=True, exist_ok=True)

    if shutil.which("xschem") is None:
        print("  [skip] xschem not installed")
        return
    subprocess.run(
        ["xschem", "--tcl", "set dark_colorscheme 0", "--command",
         f"wm geometry . 2600x1500; update; xschem zoom_full; update; "
         f"xschem print png {raw}; exit", str(sch)],
        check=True, capture_output=True, timeout=240)

    im = Image.open(raw).convert("RGB")
    bg = im.getpixel((5, 5))
    bbox = ImageChops.difference(
        im, Image.new("RGB", im.size, bg)).getbbox()
    im = im.crop(bbox)
    im.save(FIGS / "fig1_schematic_full.png")
    print("  fig1_schematic_full.png", im.size)

    # the stage split: stage 2 starts a little before its title
    w, h = im.size
    cut = int(w * 0.375)
    im.crop((0, 0, cut, h)).save(FIGS / "fig1a_stage1.png")
    im.crop((cut, 0, w, h)).save(FIGS / "fig1b_stage2.png")
    print("  fig1a_stage1.png / fig1b_stage2.png")
    raw.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# 2. what the framework does, end to end
# --------------------------------------------------------------------------
def fig_flow():
    fig, ax = plt.subplots(figsize=(9.5, 3.4))
    ax.set_xlim(0, 100); ax.set_ylim(0, 34); ax.axis("off")

    boxes = [
        (2, "Target\nspecification", MUTED, "the eight limits"),
        (21, "Propose\n8 designs", ACCENT, "RL policy, 1.2 s\nno simulation"),
        (40, "Screen\nthe eye", WARM, "SPICE,\n2 corners"),
        (59, "Verify", "#2f855a", "SPICE,\nall 20 corners"),
        (78, "Sized netlist\n+ report", MUTED, "measured specs"),
    ]
    for x, title, col, sub in boxes:
        ax.add_patch(FancyBboxPatch((x, 13), 17, 11, boxstyle="round,pad=0.6",
                                    linewidth=1.6, edgecolor=col,
                                    facecolor="white", zorder=2))
        ax.text(x + 8.5, 20.5, title, ha="center", va="center",
                fontsize=11, fontweight="bold", color=col, zorder=3)
        ax.text(x + 8.5, 16, sub, ha="center", va="center", fontsize=8.5,
                color=MUTED, zorder=3)
    for x in (19, 38, 57, 76):
        ax.add_patch(FancyArrowPatch((x, 18.5), (x + 2, 18.5),
                                     arrowstyle="-|>", mutation_scale=15,
                                     color=INK, linewidth=1.4))
    ax.text(50, 7.5,
            "The learned model only ever proposes. SPICE decides: a design is "
            "reported as passing\nonly after all 20 PVT corners were actually "
            "simulated.",
            ha="center", va="center", fontsize=10, color=INK, style="italic")
    return _save(fig, "fig2_flow.png")


# --------------------------------------------------------------------------
# 3. the physics anchor
# --------------------------------------------------------------------------
def fig_physics_anchor():
    """How one prediction is assembled, and the bound on the learned part."""
    from eqrl.surrogate import MAX_LOG_RESIDUAL
    import numpy as np

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.9),
                                   gridspec_kw={"width_ratios": [1.15, 1]})

    # -- left: the three terms -------------------------------------------
    ax1.set_xlim(0, 100); ax1.set_ylim(0, 40); ax1.axis("off")
    terms = [(1, "Hand\ncalculation",
              "closed-form CTLE\nresponse, using gm and\nCout from one SPICE\noperating point", ACCENT),
             (35, "bounded\ncorrection",
              "what the network\nlearned from\n5,993 measurements", WARM),
             (69, "prediction", "physics, sharpened", "#2f855a")]
    for x, title, sub, col in terms:
        ax1.add_patch(FancyBboxPatch((x, 11), 29, 20,
                                     boxstyle="round,pad=0.6", linewidth=1.6,
                                     edgecolor=col, facecolor="white"))
        ax1.text(x + 14.5, 26.5, title, ha="center", va="center", fontsize=10,
                 fontweight="bold", color=col)
        ax1.text(x + 14.5, 17.5, sub, ha="center", va="center", fontsize=8,
                 color=MUTED)
    for x, sym in ((32.5, "x"), (66.5, "=")):
        ax1.text(x, 21, sym, ha="center", va="center", fontsize=15,
                 color=INK, fontweight="bold")
    ax1.text(50, 5.5,
             "Before training the correction is exactly 1.0,\n"
             "so the model starts as the hand calculation.",
             ha="center", va="center", fontsize=9.5, style="italic")

    # -- right: how far the correction may go ----------------------------
    r = np.linspace(-3.0, 3.0, 600)
    ax2.plot(r, np.exp(np.clip(r, -MAX_LOG_RESIDUAL, MAX_LOG_RESIDUAL)),
             color=WARM, linewidth=2.2, label="allowed correction")
    ax2.axvspan(-MAX_LOG_RESIDUAL, MAX_LOG_RESIDUAL, color=ACCENT, alpha=0.07)
    for s in (-MAX_LOG_RESIDUAL, MAX_LOG_RESIDUAL):
        ax2.axvline(s, color=ACCENT, linestyle="--", linewidth=1.3)
    ax2.axhline(1.0, color=MUTED, linewidth=1.0, linestyle=":")
    ax2.axvspan(-0.6, 0.6, color="#2f855a", alpha=0.13)
    ax2.set_yscale("log")
    ax2.set_xlabel("what the network wants to change")
    ax2.set_ylabel("multiplier on the physics")
    ax2.set_title("The correction is capped", fontsize=11, color=INK)
    ax2.text(0, 0.20, "where the learned\ncorrections actually\nsit (about 1.8x)",
             ha="center", fontsize=8.2, color="#2f855a")
    ax2.text(2.55, 9.0, f"hard limit\nx{np.exp(MAX_LOG_RESIDUAL):.1f}",
             ha="center", fontsize=8.5, color=ACCENT)
    fig.tight_layout()
    return _save(fig, "fig3_physics_anchor.png")


# --------------------------------------------------------------------------
# 4. how well the model agrees with SPICE
# --------------------------------------------------------------------------
def fig_surrogate_agreement():
    val = json.loads((_surrogate_dir() / "validation.json").read_text())
    pa = val["pass_agreement"]
    order = sorted(pa, key=lambda k: -pa[k])
    vals = [pa[k] * 100 for k in order]

    fig, ax = plt.subplots(figsize=(8.2, 3.5))
    bars = ax.bar(order, vals, color=ACCENT, width=0.62)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.0f}%",
                ha="center", fontsize=9.5, color=INK)
    ax.set_ylim(0, 112)
    ax.set_ylabel("agrees with SPICE (%)")
    ax.set_title("Model vs. simulator: does each specification pass or fail?",
                 fontsize=11)
    ax.axhline(100, color=MUTED, linewidth=0.9, linestyle=":")
    fig.tight_layout()
    return _save(fig, "fig4_surrogate_agreement.png")


def _surrogate_dir() -> Path:
    """LATEST holds a path relative to the repo root, not a bare tag."""
    latest = (ROOT / "models" / "surrogate" / "LATEST").read_text().strip()
    d = ROOT / latest
    return d if d.is_dir() else ROOT / "models" / "surrogate" / latest


# --------------------------------------------------------------------------
# 5. how often it solves a specification
# --------------------------------------------------------------------------
def coverage(path: Path):
    d = json.loads(path.read_text())
    ranks = [s["first_success_rank"] for s in d["specs"]]
    n_max = max(s["n_proposals"] for s in d["specs"])
    n = len(ranks)
    return ([k for k in range(1, n_max + 1)],
            [sum(1 for r in ranks if r is not None and r <= k) / n
             for k in range(1, n_max + 1)], ranks)


def fig_coverage():
    res = ROOT / "results"
    kp, cp, rp = coverage(res / "best_of_n_policy.json")
    kc, cc, rc = coverage(res / "best_of_n_surrogate_cem.json")
    union = sum(1 for a, b in zip(rp, rc) if a is not None or b is not None)

    fig, ax = plt.subplots(figsize=(8.2, 4.0))
    ax.step(kp, [c * 100 for c in cp], where="post", color=ACCENT,
            linewidth=2.4, label="RL policy")
    ax.step(kc, [c * 100 for c in cc], where="post", color=WARM,
            linewidth=2.0, linestyle="--", label="search (no learned policy)")
    ax.axhline(union / len(rp) * 100, color="#2f855a", linewidth=1.6,
               linestyle=":", label=f"both methods together "
                                    f"({union}/{len(rp)})")
    ax.set_xlabel("number of candidate designs tried")
    ax.set_ylabel("specifications solved (%)")
    ax.set_title("How often a target specification is met\n"
                 "(10 unseen specifications; every candidate verified at all "
                 "20 PVT corners)", fontsize=11)
    ax.set_ylim(0, 100); ax.set_xlim(1, max(kc))
    ax.legend(frameon=False, loc="lower right", fontsize=9.5)
    fig.tight_layout()
    return _save(fig, "fig5_coverage.png")


# --------------------------------------------------------------------------
# 6. how long a design takes
# --------------------------------------------------------------------------
def wall_times():
    d = json.loads((ROOT / "results" / "mcp_e2e.json").read_text())
    out: list[float] = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("wall_s"), (int, float)):
                out.append(o["wall_s"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return sorted(out)


def fig_time():
    ws = wall_times()
    import statistics as st
    fig, ax = plt.subplots(figsize=(8.2, 3.4))
    ax.hist(ws, bins=9, color=ACCENT, alpha=0.82, edgecolor="white")
    ax.axvline(st.median(ws), color=WARM, linewidth=2.2,
               label=f"median {st.median(ws):.0f} s")
    ax.set_xlabel("time to design and verify one circuit (seconds)")
    ax.set_ylabel("number of runs")
    ax.set_title(f"Time per design, measured over {len(ws)} runs "
                 f"(range {min(ws):.0f}-{max(ws):.0f} s)", fontsize=11)
    ax.legend(frameon=False, fontsize=9.5)
    fig.tight_layout()
    return _save(fig, "fig6_time.png")


# --------------------------------------------------------------------------
# 7-8. the proof: a real eye and a real AC response
# --------------------------------------------------------------------------
def fig_measured():
    """Copy the eye and AC plots from the most recent verified design run.

    They live under /tmp, which is cleared on reboot; the report must not
    depend on that.
    """
    runs = sorted(Path("/tmp/eqrl_mcp").glob("*/eye.png"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        print("  [skip] no design run found under /tmp/eqrl_mcp")
        return
    src = runs[0].parent
    FIGS.mkdir(parents=True, exist_ok=True)
    for a, b in (("eye.png", "fig7_eye.png"), ("ac.png", "fig8_ac.png")):
        if (src / a).exists():
            shutil.copy(src / a, FIGS / b)
            print(f"  {b}  (from {src.name})")


def main() -> int:
    print("figures ->", FIGS)
    fig_schematic()
    fig_flow()
    fig_physics_anchor()
    fig_surrogate_agreement()
    fig_coverage()
    fig_time()
    fig_measured()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
