"""best_of_n.py unchanged, with the SPICE-free PhysFit start. No repo writes."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

if __name__ == "__main__":
    import eqrl.cache as cache
    OUT = Path("/tmp/eqrl_bon_pf/evals"); OUT.mkdir(parents=True, exist_ok=True)
    cache.EVAL_DIR = OUT
    import deploy_policy, eqrl.env_goal
    from eqrl import start_physfit
    start_physfit.patch()
    print(f"start    : PHYSFIT (SPICE-free)\neval log : {OUT}")
    import best_of_n
    sys.argv = ["best_of_n", "--method", "policy", "--k", "8",
                "--n-specs", "10", "--jobs", "8",
                "--workdir", "/tmp/eqrl_bon_pf",
                "--out", str(ROOT / "results" / "best_of_n_policy_physfit.json")]
    raise SystemExit(best_of_n.main())
