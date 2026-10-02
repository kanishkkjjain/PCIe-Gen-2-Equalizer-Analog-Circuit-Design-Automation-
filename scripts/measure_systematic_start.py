"""best_of_n.py unchanged, with (a) the systematic start and (b) NO repo writes.

The companion to `measure_physfit_start.py`: same measurement, same ten specs,
the only difference being which starting-point derivation is in force. The
systematic one measures its device values with ngspice `.op` runs; PhysFit
takes them from the fitted model. See `docs/systematic-starting-point.md`.

  python3 scripts/measure_systematic_start.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

OUT_DIR = Path("/tmp/eqrl_bon_sys")

if __name__ == "__main__":
    # 1. keep every write out of the repository
    import eqrl.cache as cache
    evals = OUT_DIR / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    cache.EVAL_DIR = evals

    # 2. swap in the systematic starting point, nothing else
    import deploy_policy, eqrl.env_goal          # noqa: F401  (import order)
    from eqrl import start_systematic
    start_systematic.patch()

    from eqrl import start
    print(f"start      : {start.current()}")
    print(f"eval log   : {cache.EVAL_DIR}   (outside the repo)")

    import best_of_n
    sys.argv = ["best_of_n", "--method", "policy", "--k", "8",
                "--n-specs", "10", "--jobs", "8",
                "--workdir", str(OUT_DIR),
                "--out", str(OUT_DIR / "bestofn_systematic.json")]
    raise SystemExit(best_of_n.main())
