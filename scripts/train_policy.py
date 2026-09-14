#!/usr/bin/env python3
"""Train the goal-conditioned policy inside the surrogate. Gate G2.

PPO over `env_goal.GoalCtleEnv`: every episode draws a spec from the calibrated
goal space, derives its own hand-calculated starting design for THAT spec, and
the policy has 20 moves to bring it inside every requirement at all four robust
corners.

**G2 is measured on goals the training sampler never draws.**  A fixed set is
generated from seed 777 -- disjoint from the training stream and from
deployment's seed 999 -- and success is "every brief spec passes at every
corner within the step budget".  **G2: >= 80 %.**  Reporting success on the
training distribution would measure memorisation of a sampler, which is not the
claim being made.

Everything is resumable, because a training run that cannot survive a closed
terminal is a training run that will be done twice:

* checkpoints every `--ckpt-every` steps to ``models/policy/ckpt_<steps>.zip``
* ``models/policy/LATEST`` as an atomically-replaced text pointer
* ``--resume`` continues from LATEST, step count included
* ``--max-minutes`` stops gracefully with a final checkpoint
* every evaluation is appended to ``results/policy_train_eval.json`` as it
  happens, so the G2 curve survives a crash at any point

  python3 scripts/train_policy.py --steps 1000000
  python3 scripts/train_policy.py --resume --steps 2000000
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eqrl import specs as sp
from eqrl.cache import git_sha, write_atomic
from eqrl.env_goal import GoalCtleEnv, GoalSpace
from eqrl.surrogate import load_latest

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "models" / "policy"
EVAL_LOG = ROOT / "results" / "policy_train_eval.json"

#: seeds kept apart on purpose. Training draws from an unbounded stream; these
#: two sets are fixed and disjoint from it and from each other.
EVAL_SEED = 777
DEPLOY_SEED = 999

_STOP = False


def _on_signal(signum, frame):
    global _STOP
    if not _STOP:
        _STOP = True
        print(f"\n[signal {signum}] finishing this rollout, checkpointing, "
              f"then exiting. Resume with --resume.", flush=True)


def held_out_specs(space: GoalSpace, n: int, seed: int = EVAL_SEED) -> list:
    rng = np.random.default_rng(seed)
    return [space.sample(rng) for _ in range(n)]


def feasible_ceiling(specs, tag: str = "collect") -> dict:
    """How many of these specs is anything at all known to satisfy?

    G2 as a fraction of the evaluation set is misleading on its own. The goal
    space is calibrated so that roughly half of sampled specs have a
    demonstrated solution among the collected designs; a policy solving 45 % of
    them may be solving nearly everything solvable. Measuring against a ceiling
    that is not 100 % is the difference between "the policy is weak" and "the
    goal space is the constraint", and those call for opposite responses.

    This is a LOWER bound on what is achievable -- the policy can reach designs
    nobody simulated -- so a success rate above it is not a contradiction.
    """
    try:
        from eqrl.cache import load
        from eqrl.spice import MeasCond
        import importlib.util
        cg = ROOT / "scripts" / "calibrate_goals.py"
        spec_ = importlib.util.spec_from_file_location("calibrate_goals", cg)
        mod = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(mod)

        recs = list(load(tag, meas=MeasCond.from_spec(sp.EqualizerSpec())
                         ).records.values())
        designs = [m for m in mod.worst_by_design(recs) if m.saturated]
        if not designs:
            return {}
        n_ok = 0
        for g in specs:
            if any(all(sp.score(m, g)["pass"].get(k) for k in sp.BRIEF_SPECS)
                   for m in designs):
                n_ok += 1
        return {"n_specs": len(specs), "n_demonstrably_feasible": n_ok,
                "fraction": n_ok / max(1, len(specs)),
                "n_saturated_designs": len(designs)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def rollout(policy, env, spec, max_steps: int) -> dict:
    """One greedy episode against one spec. Returns what G2 is computed from."""
    obs, info = env.reset(options={"spec": spec})
    best_r, best_u, solved, steps = info["reward"], env._u.copy(), False, 0
    unc = [info.get("uncertainty", 0.0)]
    for t in range(max_steps):
        action, _ = policy.predict(obs, deterministic=True)
        obs, _, term, trunc, info = env.step(action)
        steps = t + 1
        unc.append(info.get("uncertainty", 0.0))
        if info["reward"] > best_r:
            best_r, best_u = info["reward"], info["u"].copy()
        if term:
            solved = True
            break
        if trunc:
            break
    return {"solved": bool(solved), "steps": steps, "best_reward": float(best_r),
            "u": [float(x) for x in best_u],
            "mean_uncertainty": float(np.mean(unc))}


def evaluate(policy, env, specs, max_steps: int) -> dict:
    rs = [rollout(policy, env, s, max_steps) for s in specs]
    solved = [r for r in rs if r["solved"]]
    return {"n": len(rs), "success_rate": len(solved) / max(1, len(rs)),
            "mean_steps_to_solve": (float(np.mean([r["steps"] for r in solved]))
                                    if solved else None),
            "mean_best_reward": float(np.mean([r["best_reward"] for r in rs])),
            "mean_uncertainty": float(np.mean([r["mean_uncertainty"] for r in rs]))}


def append_eval(record: dict) -> None:
    """Append one evaluation durably; the G2 curve must survive a crash."""
    hist = []
    if EVAL_LOG.exists():
        try:
            hist = json.loads(EVAL_LOG.read_text()).get("evals", [])
        except Exception:
            hist = []
    hist.append(record)
    tmp = EVAL_LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps({"git_sha": git_sha(), "evals": hist},
                              indent=1, default=float))
    os.replace(tmp, EVAL_LOG)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=1_000_000)
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--n-steps", type=int, default=256)
    ap.add_argument("--batch-size", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--ent-coef", type=float, default=0.005)
    ap.add_argument("--max-episode-steps", type=int, default=20)
    ap.add_argument("--uncertainty-penalty", type=float, default=0.1)
    ap.add_argument("--start-noise", type=float, default=0.02,
                    help="noise on a derived start during TRAINING. The "
                         "evaluation env always uses 0, matching deployment.")
    ap.add_argument("--ckpt-every", type=int, default=50_000)
    ap.add_argument("--eval-every", type=int, default=50_000)
    ap.add_argument("--n-eval", type=int, default=25)
    ap.add_argument("--g2", type=float, default=0.80)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--pidfile", default=None)
    a = ap.parse_args()

    if a.pidfile:
        Path(a.pidfile).parent.mkdir(parents=True, exist_ok=True)
        Path(a.pidfile).write_text(f"{os.getpid()}\n")

    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    from eqrl.surrogate import Surrogate
    sur = Surrogate.load(a.surrogate) if a.surrogate else load_latest()
    space = GoalSpace.load()
    print(f"surrogate: {a.surrogate or 'LATEST'}  "
          f"goal space feasible_frac={space.feasible_frac}")
    if space.feasible_frac is not None and space.feasible_frac < 0.3:
        print("  WARNING: most sampled goals have no demonstrated solution. "
              "G2 may be unreachable by construction -- re-run "
              "scripts/calibrate_goals.py with more collected data.")

    u_seed = None
    plan = ROOT / "results" / "evals" / "plan_collect.json"
    if plan.exists():
        u_seed = json.loads(plan.read_text()).get("u_seed")

    def make(i):
        def _f():
            return GoalCtleEnv(sur, space, seed=a.seed + i,
                               max_steps=a.max_episode_steps,
                               uncertainty_penalty=a.uncertainty_penalty,
                               u_seed=u_seed, start_noise=a.start_noise)
        return _f

    # DummyVecEnv, not SubprocVecEnv: each step is a small CPU forward pass, so
    # subprocess overhead exceeds the work, and while data collection is running
    # 16 extra processes would simply fight the ngspice workers for cores.
    venv = DummyVecEnv([make(i) for i in range(a.envs)])
    # G2 is measured from the SAME starting point deploy uses: the
    # hand-calculated design for that spec. Training keeps the mixture (uniform
    # and seed starts are exploration), but scoring the policy from starts it
    # never meets in deployment would give any G2 -> G3 drop a cause that has
    # nothing to do with the method.
    # start_noise=0: deployment starts from the calibrated design exactly, so
    # G2 must be measured from there too. Perturbing it would score the policy
    # from starts it never meets in deployment AND would undo the f_peak
    # calibration the start exists to provide.
    eval_env = GoalCtleEnv(sur, space, seed=12345,
                           max_steps=a.max_episode_steps,
                           uncertainty_penalty=0.0, u_seed=u_seed,
                           start_mix=(1.0, 0.0, 0.0), start_noise=0.0)
    specs = held_out_specs(space, a.n_eval)
    ceiling = feasible_ceiling(specs)
    if ceiling.get("n_specs"):
        print(f"  of the {ceiling['n_specs']} held-out specs, "
              f"{ceiling['n_demonstrably_feasible']} "
              f"({ceiling['fraction']:.0%}) are satisfied by at least one of "
              f"{ceiling['n_saturated_designs']} collected designs -- that is "
              f"the demonstrated floor on what is reachable, not a hard cap")

    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    done_steps = 0
    latest = POLICY_DIR / "LATEST"
    if a.resume and latest.exists():
        p = Path(latest.read_text().strip())
        if not p.is_absolute():
            p = ROOT / p
        model = PPO.load(p, env=venv)
        done_steps = int(model.num_timesteps)
        print(f"resumed {p.name} at {done_steps} steps")
    else:
        model = PPO("MlpPolicy", venv, n_steps=a.n_steps,
                    batch_size=a.batch_size, learning_rate=a.lr,
                    ent_coef=a.ent_coef, seed=a.seed, verbose=0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    t0 = time.perf_counter()
    deadline = t0 + a.max_minutes * 60 if a.max_minutes else None
    chunk = max(a.n_steps * a.envs, min(a.eval_every, a.ckpt_every))
    best_rate = -1.0

    print(f"\ntraining to {a.steps} steps ({a.envs} envs, chunk {chunk})")
    while done_steps < a.steps and not _STOP:
        if deadline and time.perf_counter() > deadline:
            print("  --max-minutes reached, stopping gracefully")
            break
        model.learn(total_timesteps=min(chunk, a.steps - done_steps),
                    reset_num_timesteps=False, progress_bar=False)
        done_steps = int(model.num_timesteps)

        ck = POLICY_DIR / f"ckpt_{done_steps}.zip"
        model.save(ck)
        write_atomic(latest, str(ck.relative_to(ROOT)))

        ev = evaluate(model, eval_env, specs, a.max_episode_steps)
        rec = {"steps": done_steps, "wall_s": time.perf_counter() - t0,
               "eval_seed": EVAL_SEED, "feasible_ceiling": ceiling, **ev}
        if ceiling.get("n_demonstrably_feasible"):
            rec["success_vs_feasible"] = (
                ev["success_rate"] * ev["n"]
                / ceiling["n_demonstrably_feasible"])
        append_eval(rec)
        best_rate = max(best_rate, ev["success_rate"])
        vs_feas = rec.get("success_vs_feasible")
        print(f"  {done_steps:>8} steps  success {ev['success_rate']:.1%}"
              + (f" ({vs_feas:.0%} of demonstrably feasible)"
                 if vs_feas is not None else "")
              + f"  reward {ev['mean_best_reward']:.3f}  "
              f"steps {ev['mean_steps_to_solve']}  "
              f"unc {ev['mean_uncertainty']:.3f}  "
              f"[{(time.perf_counter() - t0) / 60:.1f}m]", flush=True)

        # keep only the most recent few checkpoints; they are ~200 kB each but
        # there is no reason to accumulate hundreds
        cks = sorted(POLICY_DIR.glob("ckpt_*.zip"),
                     key=lambda p: int(p.stem.split("_")[1]))
        for old in cks[:-3]:
            old.unlink(missing_ok=True)

    ck = POLICY_DIR / f"ckpt_{done_steps}.zip"
    model.save(ck)
    write_atomic(latest, str(ck.relative_to(ROOT)))
    print(f"\n  {done_steps} steps in {(time.perf_counter() - t0) / 60:.1f} min")
    print(f"  LATEST -> {ck.relative_to(ROOT)}")
    print(f"\n{'=' * 66}\nG2  best held-out success rate = {best_rate:.1%}  "
          f"(need >= {a.g2:.0%})   "
          f"{'PASS' if best_rate >= a.g2 else 'below gate'}\n{'=' * 66}")
    if _STOP or done_steps < a.steps:
        print(f"  resume: python3 scripts/train_policy.py --resume "
              f"--steps {a.steps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
