#!/usr/bin/env python3
"""Build and VALIDATE trimmed SKY130 model libraries.

99.9% of every ngspice launch is spent parsing the PDK, not solving the circuit
(HANDOVER section 9). The `tt` section pulls 169 files / 7.12 MB covering pfet,
3.3/5/20 V, ESD, bipolars and RF -- none of which this design instantiates.
Trimming to just nfet_01v8_lvt + R/C + all.spice cuts that to 93 files /
1.24 MB.

Nothing is used until it is PROVEN identical to the full library. Each section
is validated by simulating several design points through both libraries and
comparing every metric at 1e-9 relative tolerance. A section that disagrees --
by any amount -- is not written to the manifest, so Simulator falls back to the
full library for it.

  python3 scripts/build_trimlib.py                 # sections for the full grid
  python3 scripts/build_trimlib.py --grid spec     # just the brief's corners
  python3 scripts/build_trimlib.py --section tt    # one section
  python3 scripts/build_trimlib.py --check         # report status, build nothing
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import pdk, trimlib
from eqrl.spice import DEFAULT_TRIM_DIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", choices=("quick", "spec", "full"), default="full",
                    help="build the sections needed by this PVT grid")
    ap.add_argument("--section", action="append", default=[],
                    help="build one specific .lib section (repeatable)")
    ap.add_argument("--out", default=str(DEFAULT_TRIM_DIR))
    ap.add_argument("--check", action="store_true",
                    help="report which sections are validated; build nothing")
    a = ap.parse_args()

    out = Path(a.out)
    sections = a.section or sorted({c.lib_section for c in pdk.pvt_grid(a.grid)})

    if a.check:
        print(f"trim dir: {out}")
        man = trimlib.load_manifest(out)
        print(f"manifest pdk fingerprint: {man.get('pdk_fingerprint', '(none)')}")
        for sec in sections:
            p = trimlib.validated_lib(sec, out)
            print(f"  {sec:8s} {'VALIDATED ' + str(p.name) if p else 'not validated -> full library'}")
        return 0

    print(f"Building trimmed libraries for {len(sections)} section(s) -> {out}")
    print("Each one is validated against the full library before it is trusted.\n")
    ok, failed = [], []
    t0 = time.perf_counter()
    for sec in sections:
        try:
            path = trimlib.build(sec, out)
            print(f"[{sec}] built {path.name}")
            rep = trimlib.validate(sec, out)
            print(f"[{sec}] VALIDATED (worst relative difference "
                  f"{rep['worst_rel_diff']:.2e} over {len(rep['points'])} points)\n")
            ok.append(sec)
        except trimlib.TrimmedLibError as e:
            print(f"[{sec}] REJECTED: {e}\n")
            failed.append((sec, str(e)))
        except Exception as e:                      # noqa: BLE001
            print(f"[{sec}] REJECTED (unexpected {type(e).__name__}): {e}\n")
            failed.append((sec, f"{type(e).__name__}: {e}"))

    dt = time.perf_counter() - t0
    print("=" * 62)
    print(f"validated {len(ok)}/{len(sections)} sections in {dt:.0f} s")
    if failed:
        print("\nREJECTED sections (these will use the full library, slowly "
              "but correctly):")
        for sec, why in failed:
            print(f"  {sec}: {why[:160]}")
    print("\nSimulator picks these up automatically; anything not listed above "
          "falls back to the full library.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
