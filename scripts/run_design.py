#!/usr/bin/env python3
"""CLI entry point: size a PCIe CTLE from target specs using the RL framework.

Examples
--------
  python scripts/run_design.py                     # defaults, typical-corner search
  python scripts/run_design.py --robust --iters 12 # robust 3-corner search
  python scripts/run_design.py --peaking 10 --fpeak 2.0e9
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
                    help="search over a 3-corner robust subset")
    ap.add_argument("--full-pvt", action="store_true",
                    help="verify winner over the full PVT cross-product")
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
               search_robust=args.robust, verify_full_pvt=args.full_pvt)


if __name__ == "__main__":
    main()
