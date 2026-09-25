#!/usr/bin/env python3
"""The channel's pulse response, for the presentation.

One isolated bit is driven into the committed channel model and the received
waveform is plotted against it. This is the picture behind the word "ISI": the
transmitted pulse is a clean rectangle one Unit Interval wide, and what arrives
is a smeared hump that is still sitting there a full bit later, on top of
whatever the next bit is doing.

The channel here is `netlist.channel_netlist()` -- the same lossy ladder every
other measurement in this project runs through -- so the post-cursor this plots
is the one the DFE tap is sized against. It is passive R/L/C, so this needs
ngspice but no PDK.

  python3 scripts/make_channel_figures.py

Writes docs/presentation/figures/06a_pulse_spreading.png.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eqrl import dfe, netlist as nl                  # noqa: E402

OUT = ROOT / "docs" / "presentation" / "figures" / "06a_pulse_spreading.png"

UI = 200e-12          # 5 Gb/s
AMP = 0.1             # single-ended, so 200 mV differential -- the project's
VCM = 0.0             # drive level. Common mode is irrelevant to a passive net.
N_LEAD = 3            # bit slots before the pulse
N_TRAIL = 16          # and after: the channel delays by 1.2 ns (6 UI)
                      # on top of the tail itself, so this has to be
                      # generous or the post-cursors fall off the end

INK, ACCENT, WARM, MUTED = "#1a202c", "#2b6cb0", "#c05621", "#718096"
plt.rcParams.update({
    "font.size": 11, "font.family": "DejaVu Sans",
    "axes.edgecolor": INK, "axes.labelcolor": INK,
    "text.color": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def run() -> np.ndarray:
    """Drive one bit through the channel; return (t, tx_diff, rx_diff)."""
    bits = np.array([0] * N_LEAD + [1] + [0] * N_TRAIL, dtype=int)
    tstop = len(bits) * UI

    deck = [
        "* channel pulse response -- passive ladder, no device models needed",
        f"Vp {nl.CH_SRC_P} 0 {dfe.nrz_pwl(bits, UI, AMP, VCM)}",
        f"Vn {nl.CH_SRC_N} 0 {dfe.nrz_pwl(bits, UI, AMP, VCM, invert=True)}",
        nl.channel_netlist(),
        ".control",
        f"tran {UI / 200:.6g} {tstop:.6g} 0",
        "wrdata pulse.dat v(chsrc_p,chsrc_n) v(vinp,vinn)",
        ".endc",
        ".end",
    ]

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "pulse.sp").write_text("\n".join(deck) + "\n")
        r = subprocess.run(["ngspice", "-b", "pulse.sp"], cwd=d,
                           capture_output=True, text=True, timeout=300)
        dat = d / "pulse.dat"
        if not dat.exists():
            raise RuntimeError(f"ngspice produced no data:\n{r.stdout[-2000:]}"
                               f"\n{r.stderr[-2000:]}")
        raw = np.loadtxt(dat)
    # wrdata repeats the x column per expression: t, tx, t, rx
    return raw[:, 0], raw[:, 1], raw[:, 3]


def main() -> int:
    t, tx, rx = run()

    # REFERENCE BOTH TO THE IDLE LEVEL. This is NRZ, so a run of zeros sits at
    # -amp differentially, not at 0 V. Taken raw, the largest |sample| is the
    # idle level itself and the "cursor" lands at t=0 with h1/h0 = 1.000 --
    # which is what this first did. The pulse response is the DEVIATION from
    # idle, so subtract the settled level before measuring anything.
    idle = slice(0, int(np.searchsorted(t, 2 * UI)))
    tx = tx - float(np.mean(tx[idle]))
    rx = rx - float(np.mean(rx[idle]))

    # The cursor is the received peak; the post-cursors are one and two UI
    # later, sampled at that same phase.
    i_pk = int(np.argmax(np.abs(rx)))
    t_pk, h0 = t[i_pk], rx[i_pk]
    h1 = float(np.interp(t_pk + UI, t, rx))
    h2 = float(np.interp(t_pk + 2 * UI, t, rx))
    print(f"cursor h0 = {h0*1e3:.1f} mV at {t_pk*1e12:.0f} ps")
    print(f"post-cursor h1/h0 = {h1/h0:+.3f}")
    print(f"post-cursor h2/h0 = {h2/h0:+.3f}")

    # DE-SKEW. The channel delays by 1.2 ns -- six whole bit slots -- so plotted
    # raw, the transmitted pulse and its response sit at opposite ends of a
    # mostly empty frame. Shifting the received trace back by that delay puts
    # them on the same axis, which is the comparison the slide is making. The
    # delay is a constant; removing it changes no shape and no ratio.
    t_tx_centre = (N_LEAD + 0.5) * UI
    skew = t_pk - t_tx_centre
    ps = 1e12
    trx = (t - skew)

    fig, ax = plt.subplots(figsize=(9.2, 4.3))

    # bit-slot boundaries around the sampling phase
    for k in range(-2, 6):
        ax.axvline((t_tx_centre + k * UI) * ps, color=MUTED, lw=0.6, ls=":",
                   alpha=0.55, zorder=0)

    ax.plot(t * ps, tx * 1e3, color=MUTED, lw=1.8, ls="--",
            label="transmitted: one bit, 200 ps wide")
    ax.plot(trx * ps, rx * 1e3, color=ACCENT, lw=2.6,
            label="received after 7 inch of FR4")

    ax.axhline(0, color=INK, lw=0.8)
    for kk, (vv, lab, col) in enumerate((
            (h0, "cursor", "#2f855a"), (h1, "+1 UI", WARM),
            (h2, "+2 UI", WARM))):
        tt = t_tx_centre + kk * UI
        ax.plot([tt * ps], [vv * 1e3], "o", ms=8, color=col, zorder=5)
        ax.annotate(lab, (tt * ps, vv * 1e3), textcoords="offset points",
                    xytext=(7, 9), fontsize=10, color=col, fontweight="bold")

    ax.annotate(
        f"{h1/h0*100:.0f} % of this bit is still here\none bit later -- on top of"
        f"\nwhatever the next bit sends",
        xy=((t_tx_centre + UI) * ps, h1 * 1e3),
        xytext=((t_tx_centre + 1.9 * UI) * ps, h0 * 1e3 * 0.80),
        fontsize=10.5, color=WARM,
        arrowprops=dict(arrowstyle="->", color=WARM, lw=1.5))

    ax.set_xlabel("time (ps)   —   channel delay removed so the two line up")
    ax.set_ylabel("differential pulse response (mV)")
    ax.set_title("One bit through the channel: it does not stay in its slot",
                 fontsize=12.5)
    ax.legend(frameon=False, fontsize=10, loc="upper right")
    ax.set_xlim((t_tx_centre - 1.6 * UI) * ps, (t_tx_centre + 4.4 * UI) * ps)
    fig.tight_layout()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
