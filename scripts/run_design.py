#!/usr/bin/env python3
"""CLI entry point: size a PCIe CTLE from target specs using the RL framework.

Examples
--------
  python scripts/run_design.py                          # typical-corner search
  python scripts/run_design.py --robust --pvt-grid full # robust, verify 60
  python scripts/run_design.py --peaking 10 --no-eye    # fast AC-only search
"""
import argparse
import sys
from pathlib import Path

# make the package importable when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import specs as sp
from eqrl.agents.cem import CemConfig
from eqrl.design import run_design


def main():
    ap = argparse.ArgumentParser(description="RL CTLE auto-sizing")
    ap.add_argument("--peaking", type=float, default=8.0,
                    help="target HF peaking (dB)")
    ap.add_argument("--fpeak-lo", type=float, default=1.25e9)
    ap.add_argument("--fpeak-hi", type=float, default=2.5e9)
    ap.add_argument("--power-max", type=float, default=15e-3)
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--iters", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--robust", action="store_true",
                    help="search over a 4-corner robust subset (incl. tt_ll, "
                         "the worst f_peak corner)")
    ap.add_argument("--pvt-grid", choices=("quick", "spec", "full"),
                    default="spec",
                    help="grid used to VERIFY the winner: quick=9 (smoke), "
                         "spec=20 (the brief's cross-product), full=60 "
                         "(+ the R/C axis, the real worst case)")
    ap.add_argument("--eye", dest="eye", action="store_true", default=True,
                    help="score the closed-loop DFE eye (default on)")
    ap.add_argument("--no-eye", dest="eye", action="store_false",
                    help="skip the eye: ~5x faster, but the highest-weighted "
                         "spec is then invisible to the search")
    ap.add_argument("--channel", action="store_true",
                    help="put a lossy FR4-like channel in front of the "
                         "equaliser (-11 dB at Nyquist). Without it there is "
                         "no post-cursor and the mandated 1-tap DFE has "
                         "nothing to cancel.")
    ap.add_argument("--no-analytic-init", dest="analytic_init",
                    action="store_false", default=True,
                    help="start the search from the centre of the parameter "
                         "box instead of the analytic solution (slower; kept "
                         "so the two can be compared)")
    ap.add_argument("--jobs", type=int, default=None,
                    help="parallel ngspice workers (default: min(16, ncpu))")
    ap.add_argument("--workdir", default="/tmp/eqrl_work")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "results"))
    args = ap.parse_args()

    spec = sp.EqualizerSpec(
        peaking_target_db=args.peaking,
        fpeak_lo=args.fpeak_lo, fpeak_hi=args.fpeak_hi,
        power_max_w=args.power_max,
    )
    cfg = CemConfig(pop=args.pop, iters=args.iters, seed=args.seed)
    run_design(spec, workdir=args.workdir, out_dir=args.out, cem_cfg=cfg,
               search_robust=args.robust, pvt_grid=args.pvt_grid,
               do_eye=args.eye, jobs=args.jobs, use_channel=args.channel,
               analytic_init=args.analytic_init)


if __name__ == "__main__":
    main()
