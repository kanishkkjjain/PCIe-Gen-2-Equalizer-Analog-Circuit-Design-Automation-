#!/usr/bin/env python3
"""Plot an ngspice ``wrdata`` dump to a PNG (and optionally open it in Windows).

``wrdata`` writes plain whitespace-separated columns: an x column (frequency for
.ac, time for .tran) followed by one column per expression. This script reads
that, guesses whether it is a frequency- or time-domain sweep, and renders it.

Examples
--------
  python scripts/plot_wave.py /tmp/eqrl_lvt/ac.dat
  python scripts/plot_wave.py /tmp/eqrl_lvt/tran.dat -o /tmp/tran.png --open
  python scripts/plot_wave.py ac.dat --kind ac --title "CTLE, Rs=3k"
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Bands worth showing on a CTLE plot (from the PCIe Gen2 spec).
NYQUIST = 2.5e9
PEAK_BAND = (1.25e9, 2.5e9)


def guess_kind(x: np.ndarray) -> str:
    """'ac' if the x column looks like a decade frequency sweep, else 'tran'."""
    xs = x[x > 0]
    if xs.size and xs.min() > 1.0 and (xs.max() / xs.min()) > 50:
        return "ac"
    return "tran"


def main() -> int:
    ap = argparse.ArgumentParser(description="plot an ngspice wrdata dump")
    ap.add_argument("datfile")
    ap.add_argument("-o", "--out", help="output PNG (default: alongside input)")
    ap.add_argument("--kind", choices=["ac", "tran", "auto"], default="auto")
    ap.add_argument("--title")
    ap.add_argument("--open", action="store_true",
                    help="open the PNG in Windows via explorer.exe (WSL)")
    args = ap.parse_args()

    path = Path(args.datfile)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1

    d = np.loadtxt(path)
    if d.ndim == 1:
        d = d.reshape(1, -1)
    if d.shape[1] < 2:
        print(f"error: expected >=2 columns, got {d.shape[1]}", file=sys.stderr)
        return 1

    x, ys = d[:, 0], d[:, 1:]
    kind = guess_kind(x) if args.kind == "auto" else args.kind

    fig, ax = plt.subplots(figsize=(7, 4.2))
    for i in range(ys.shape[1]):
        label = f"col{i+1}" if ys.shape[1] > 1 else None
        (ax.semilogx if kind == "ac" else ax.plot)(x, ys[:, i], lw=1.4,
                                                   label=label)

    if kind == "ac":
        ax.axvspan(*PEAK_BAND, alpha=0.12, color="green",
                   label=f"peak band {PEAK_BAND[0]/1e9:g}-{PEAK_BAND[1]/1e9:g} GHz")
        ax.axvline(NYQUIST, ls="--", lw=1, color="crimson", alpha=0.7,
                   label="Nyquist 2.5 GHz")
        ax.set_xlabel("frequency (Hz)")
        ax.set_ylabel("differential gain (dB)")
        # annotate the headline numbers so the plot is self-describing
        g = ys[:, 0]
        ipk = int(np.argmax(g))
        ax.plot(x[ipk], g[ipk], "o", ms=6, color="crimson")
        ax.annotate(f"peak {g[ipk]:.2f} dB @ {x[ipk]/1e9:.2f} GHz\n"
                    f"peaking {g[ipk]-g[0]:.2f} dB",
                    xy=(x[ipk], g[ipk]), xytext=(8, -28),
                    textcoords="offset points", fontsize=9)
    else:
        ax.set_xlabel("time (s)")
        ax.set_ylabel("voltage (V)")

    ax.grid(True, which="both", alpha=0.3)
    if ys.shape[1] > 1 or kind == "ac":
        ax.legend(fontsize=8, loc="best")
    ax.set_title(args.title or f"{path.name}  ({kind})")
    fig.tight_layout()

    out = Path(args.out) if args.out else path.with_suffix(".png")
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"wrote {out}")

    if args.open:
        try:
            win = subprocess.run(["wslpath", "-w", str(out)],
                                 capture_output=True, text=True,
                                 check=True).stdout.strip()
            subprocess.run(["explorer.exe", win], check=False)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("(could not open via explorer.exe - not WSL?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
