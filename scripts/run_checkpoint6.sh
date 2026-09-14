#!/usr/bin/env bash
# The checkpoint-6 pipeline: the margin objective, end to end.
#
# Ordered so the cheapest gate that can kill the whole idea runs FIRST. G6-0
# needs no simulation and no training -- if the surrogate's predicted margin
# carries no information about what survives real SPICE, then no reward built
# on it can help, and the honest finding is that model error is the floor for
# every method built on this model. There is no point training for two hours
# to discover that.
#
# The surrogate is FROZEN at models/surrogate/LATEST throughout. Retraining it
# changes the policy's target and makes the before/after comparison meaningless.
#
#   bash scripts/run_checkpoint6.sh            # full pipeline
#   bash scripts/run_checkpoint6.sh compare    # just the two best-of-N runs
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PDK_ROOT:?set PDK_ROOT to the volare sky130 path}"
STAGE="${1:-all}"
K="${K:-8}"
STEPS="${STEPS:-120000}"
mkdir -p logs

step() { echo; echo "=============== $* ==============="; }

if [[ "$STAGE" == "all" || "$STAGE" == "transfer" ]]; then
  step "1/4 does predicted margin survive real SPICE? (gate G6-0, ~30 s)"
  # Exits non-zero if the gate fails, and `set -e` then stops the pipeline --
  # which is the intended behaviour, not an accident.
  python3 -u scripts/margin_transfer.py
fi

if [[ "$STAGE" == "all" || "$STAGE" == "train" ]]; then
  step "2/4 train SAC against the margin objective (gate G6-1)"
  # SAC, not PPO, and the reason is proposal DIVERSITY: deployment samples K
  # designs and verifies all of them, and PPO's near-deterministic head would
  # return K copies of one design.
  #
  # python3 -u because redirected stdout is block buffered -- without it the
  # log sits at 0 bytes for the first ten minutes and a healthy run is
  # indistinguishable from a hung one.
  #
  # ~23 env steps/s (a 5-member ensemble over 4 corners per step), so 120k
  # steps is about two hours. Checkpoints every 10k, so there is always a
  # usable policy even if it is stopped early.
  python3 -u scripts/train_policy.py --algo sac --steps "$STEPS" --envs 4 \
          --eval-every 10000 --ckpt-every 10000 --n-eval 25 \
          --pidfile logs/sac.pid
fi

if [[ "$STAGE" == "all" || "$STAGE" == "compare" ]]; then
  step "3/4 re-measure the CEM bar through the committed script"
  # The checkpoint-5 number cannot be used as the baseline: its spec entries
  # cannot be mapped back to the seed-999 stream, because every CEM-optimised
  # proposal scores ~0.999 against any spec. See HANDOVER 19.3. So the bar is
  # re-measured here by the same code that measures the policy.
  python3 -u scripts/best_of_n.py --method surrogate_cem --k "$K" \
          --jobs 8 --pidfile logs/bon_cem.pid

  step "4/4 the policy as a proposer (gates G6-2, G6-3, G6-4)"
  python3 -u scripts/best_of_n.py --method policy --k "$K" \
          --jobs 8 --pidfile logs/bon_policy.pid
fi

step "gates"
python3 -u scripts/status.py
