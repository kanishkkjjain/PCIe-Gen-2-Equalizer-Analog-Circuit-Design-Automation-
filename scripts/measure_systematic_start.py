"""best_of_n.py unchanged, with (a) the systematic start and (b) NO repo writes."""
import sys
from pathlib import Path
ROOT = Path("/home/kanishk/pcie_eq_rl")
SCR = ("/tmp/claude-1000/-home-kanishk-pcie-eq-rl/"
       "3e6e4ce8-a896-44db-b245-2013c898f73b/scratchpad")
sys.path[:0] = [str(ROOT), str(ROOT/"scripts"), SCR]

if __name__ == "__main__":
    # 1. keep every write out of the repository
    import eqrl.cache as cache
    OUT = Path("/tmp/eqrl_bon_new/evals"); OUT.mkdir(parents=True, exist_ok=True)
    cache.EVAL_DIR = OUT
    import eqrl.cache
    eqrl.cache.EVAL_DIR = OUT

    # 2. swap in the systematic starting point, nothing else
    import deploy_policy, eqrl.env_goal
    import newstart
    newstart.patch()

    print(f"start      : SYSTEMATIC (patched)")
    print(f"eval log   : {cache.EVAL_DIR}   (outside the repo)")
    import best_of_n
    sys.argv = ["best_of_n", "--method", "policy", "--k", "8",
                "--n-specs", "10", "--jobs", "8",
                "--workdir", "/tmp/eqrl_bon_new",
                "--out", f"{SCR}/bestofn_newstart.json"]
    raise SystemExit(best_of_n.main())
