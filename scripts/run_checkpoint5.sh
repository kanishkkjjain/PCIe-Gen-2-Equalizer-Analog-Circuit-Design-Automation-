#!/usr/bin/env bash
# The checkpoint-5 pipeline, end to end, in the order the gates depend on.
#
# Every stage is resumable, so re-running this after an interruption continues
# rather than restarts. Each stage writes a numbered artifact and the gate it
# decides; scripts/status.py reads all of them.
#
#   bash scripts/run_checkpoint5.sh            # full pipeline
#   bash scripts/run_checkpoint5.sh surrogate  # from the surrogate onwards
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PDK_ROOT:?set PDK_ROOT to the volare sky130 path}"
TAGS="${TAGS:-collect,goalstart,goalstart2}"
STAGE="${1:-all}"
mkdir -p logs

step() { echo; echo "=============== $* ==============="; }

if [[ "$STAGE" == "all" || "$STAGE" == "surrogate" ]]; then
  step "1/5 surrogate (gate G1)"
  # default output location on purpose: models/surrogate/LATEST must point
  # inside the repo, or anything run without --surrogate picks up a stale
  # scratch model
  python3 -u scripts/train_surrogate.py --tag "$TAGS" --members 5 --val-frac 0.25

  step "2/5 look for the model's blind spot"
  # 2000 evaluations, not 400: PPO searches harder than a small CEM run, so
  # the hole has to be looked for harder
  python3 -u scripts/check_surrogate.py --tag "$TAGS" --evals 2000 --jobs 4
fi

if [[ "$STAGE" == "all" || "$STAGE" == "surrogate" || "$STAGE" == "policy" ]]; then
  step "3/5 policy (gate G2)"
  # fresh, not --resume: the surrogate changed underneath, so resumed weights
  # were fitted against a different model.
  # uncertainty-penalty 0.3, not 0.1: ensemble disagreement climbed
  # 0.19 -> 0.36 across the pilot run while reward plateaued, which is the
  # policy drifting into regions the model does not know.
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 -u scripts/train_policy.py \
      --steps 1000000 --envs 8 --uncertainty-penalty 0.3 \
      --eval-every 50000 --ckpt-every 50000 --n-eval 20 \
      --pidfile logs/policy.pid
fi

if [[ "$STAGE" == "all" || "$STAGE" == "policy" || "$STAGE" == "deploy" ]]; then
  step "4/5 deploy against real ngspice (gate G3)"
  # --cem-converged adds the budget run_design actually gives CEM, so the
  # table is not only the equal-budget row that flatters the policy
  python3 -u scripts/deploy_policy.py --n-specs 6 --budget 20 \
      --cem-converged --pidfile logs/deploy.pid
fi

step "5/5 gates"
python3 scripts/status.py
