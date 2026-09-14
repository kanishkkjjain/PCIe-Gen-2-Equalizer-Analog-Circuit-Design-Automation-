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

from eqrl import pdk, specs as sp
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


def feasible_ceiling(specs, sur, corners_cf, evals: int = 3000) -> dict:
    """How many of these specs is anything at all able to satisfy?

    G2 as a bare fraction is meaningless without knowing what is reachable, and
    the first version of this asked the wrong question: it counted how many
    specs an ALREADY-COLLECTED design happens to satisfy, and answered 13 of 20.
    That is a statement about the sampling plan, not about the problem. Running
    a real search against the surrogate answers 20 of 20 -- every spec in the
    evaluation set is solvable, so the policy's shortfall is the policy's, and
    the 80 % gate is not impossible by construction.

    Costs a few seconds per spec against the model and no simulator at all, so
    it is computed once at startup.
    """
    from eqrl import netlist as nl
    from eqrl.agents.cem import CemConfig, optimize
    from eqrl.physics import analytic_start_u

    class _R:
        def __init__(self, u, r, sc, names):
            self.u = tuple(u)
            self.reward = r
            self.scored = sc[0]
            self.per_corner = dict(zip(names, sc))
            self.scored_corner = names[0]

    names = [c["process"] + str(c["vdd"]) + str(c["temp"]) + str(c["rc"])
             for c in corners_cf]

    class _O:
        def __init__(self, g):
            self.g = g

        def evaluate_batch(self, us):
            out = []
            for u in us:
                p = nl.denorm(list(np.clip(u, 0, 1)))
                mets = sur.predict_metrics([p] * len(corners_cf), corners_cf)
                sc = [sp.score(m, self.g) for m in mets]
                out.append(_R(u, min(x["reward"] for x in sc), sc, names))
            return out

        def all_pass_everywhere(self, res):
            return all(sp.all_pass(x) for x in res.per_corner.values())

    pop = 40
    n_ok = 0
    try:
        for g in specs:
            u0 = analytic_start_u(g, sur.fit, surrogate=sur,
                                  corners_cf=corners_cf)
            o = _O(g)
            r = optimize(o, CemConfig(pop=pop, iters=max(1, evals // pop),
                                      init_mean=np.asarray(u0, float),
                                      init_std=0.25, seed=0, stop_on_pass=True),
                         verbose=False)
            n_ok += bool(o.all_pass_everywhere(r.best))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"n_specs": len(specs), "n_demonstrably_feasible": n_ok,
            "fraction": n_ok / max(1, len(specs)),
            "method": f"CEM, {evals} evaluations against the surrogate"}


def rollout(policy, env, spec, max_steps: int,
            deterministic: bool = True) -> dict:
    """One episode against one spec. Returns what the gate is computed from.

    "Solved" means `m_min >= 0` -- the minimum of the ten-margin vector, which
    is equivalent to `all_pass` by construction (`specs.margin_vector`). It is
    read from the margin vector rather than from the `pass` dict ON PURPOSE:
    the two can disagree by ~10 %, because the vector's `bias` entry comes from
    the fitted device model while the scorer's comes from the surrogate's
    saturated flag. If the training gate used one and the proposer used the
    other, the difference would show up later as a gate-to-deploy drop that has
    nothing to do with the method. One definition, used everywhere.
    """
    obs, info = env.reset(options={"spec": spec})
    start_m = info.get("m_min", float("nan"))
    best_r, best_u, steps = info["reward"], env._u.copy(), 0
    best_m, best_m_u = start_m, env._u.copy()
    unc = [info.get("uncertainty", 0.0)]
    solved_at_start = bool(start_m == start_m and start_m >= 0.0)

    for t in range(max_steps):
        action, _ = policy.predict(obs, deterministic=deterministic)
        obs, _, term, trunc, info = env.step(action)
        steps = t + 1
        unc.append(info.get("uncertainty", 0.0))
        if info["reward"] > best_r:
            best_r, best_u = info["reward"], info["u"].copy()
        m = info.get("m_min", float("nan"))
        if m == m and m > best_m:
            best_m, best_m_u = m, info["u"].copy()
        # In margin mode `term` is always False by design -- the episode runs
        # its full budget so the policy keeps buying margin past the pass
        # boundary, which is where model error lives.
        if term or trunc:
            break

    return {"solved": bool(best_m == best_m and best_m >= 0.0),
            "steps": steps, "best_reward": float(best_r),
            "start_m_min": float(start_m), "best_m_min": float(best_m),
            "gain_m_min": float(best_m - start_m),
            "u": [float(x) for x in best_m_u],
            "mean_uncertainty": float(np.mean(unc)),
            "solved_at_start": solved_at_start}


def evaluate(policy, env, specs, max_steps: int) -> dict:
    """G6-1: can the policy clear the margin cap inside its own model?

    Reported as the DISTRIBUTION of best min-margin, not just a success count.
    A success rate alone cannot distinguish a policy that scrapes over zero --
    which is the checkpoint-5 failure, and passes real SPICE 42 % of the time
    -- from one that carries the buffer the reward is asking for, which passes
    61-67 %. The gain over the start is reported for the same reason: it is
    what the reward actually optimises, and it is non-zero even on specs where
    nothing passes.
    """
    from eqrl.env_goal import MARGIN_CAP
    rs = [rollout(policy, env, s, max_steps) for s in specs]
    solved = [r for r in rs if r["solved"]]
    at_start = sum(1 for r in rs if r["solved_at_start"])
    bm = np.array([r["best_m_min"] for r in rs], float)
    return {"n": len(rs), "success_rate": len(solved) / max(1, len(rs)),
            "solved_at_start": at_start,
            "solved_by_policy": len(solved) - at_start,
            "mean_best_m_min": float(np.nanmean(bm)),
            "median_best_m_min": float(np.nanmedian(bm)),
            "frac_at_cap": float(np.mean(bm >= MARGIN_CAP)),
            "mean_gain_m_min": float(np.nanmean(
                [r["gain_m_min"] for r in rs])),
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
    ap.add_argument("--algo", choices=("sac", "ppo"), default="sac",
                    help="SAC is the default, and the reason is PROPOSAL "
                         "DIVERSITY rather than general robustness. Deployment "
                         "samples K designs from the policy and verifies all "
                         "of them; PPO's Gaussian head collapses to nearly "
                         "deterministic, so K samples would be K copies of one "
                         "design and best-of-K would buy nothing. SAC's "
                         "entropy term is what keeps them different.")
    ap.add_argument("--reward-mode", choices=("margin", "score"),
                    default="margin")
    ap.add_argument("--steps", type=int, default=120_000,
                    help="MEASURED: the environment runs at ~23 steps/s -- a "
                         "5-member ensemble over 4 corners is 20 forward "
                         "passes per step -- so 120k is about two hours and "
                         "500k would be six. SAC is off-policy and far more "
                         "sample-efficient than the 1M PPO steps checkpoint 5 "
                         "used, so the smaller budget is not a compromise.")
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--n-steps", type=int, default=256)
    ap.add_argument("--batch-size", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--ent-coef", type=float, default=0.005)
    ap.add_argument("--max-episode-steps", type=int, default=20)
    ap.add_argument("--uncertainty-penalty", type=float, default=None,
                    help="default: env_goal.UNCERTAINTY_PENALTY, which is "
                         "measured by scripts/margin_transfer.py rather than "
                         "chosen. See the note on that constant.")
    ap.add_argument("--start-noise", type=float, default=0.02,
                    help="noise on a derived start during TRAINING. The "
                         "evaluation env always uses 0, matching deployment.")
    ap.add_argument("--ckpt-every", type=int, default=50_000)
    ap.add_argument("--eval-every", type=int, default=50_000)
    ap.add_argument("--n-eval", type=int, default=25)
    ap.add_argument("--g2", type=float, default=0.80)
    ap.add_argument("--g6-1-gain", type=float, default=0.05,
                    help="G6-1: mean improvement in min-margin from the "
                         "calibrated start that the policy must show")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--surrogate", default=None)
    ap.add_argument("--pidfile", default=None)
    a = ap.parse_args()

    if a.pidfile:
        Path(a.pidfile).parent.mkdir(parents=True, exist_ok=True)
        Path(a.pidfile).write_text(f"{os.getpid()}\n")

    from stable_baselines3 import PPO, SAC
    from stable_baselines3.common.vec_env import DummyVecEnv
    ALGO = {"sac": SAC, "ppo": PPO}[a.algo]

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

    from eqrl.env_goal import UNCERTAINTY_PENALTY
    lam = (UNCERTAINTY_PENALTY if a.uncertainty_penalty is None
           else a.uncertainty_penalty)
    print(f"  uncertainty penalty {lam:.2f} "
          f"{'(measured)' if a.uncertainty_penalty is None else '(override)'}")

    def make(i):
        def _f():
            return GoalCtleEnv(sur, space, seed=a.seed + i,
                               max_steps=a.max_episode_steps,
                               uncertainty_penalty=lam,
                               u_seed=u_seed, start_noise=a.start_noise,
                               reward_mode=a.reward_mode)
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
                           start_mix=(1.0, 0.0, 0.0), start_noise=0.0,
                           reward_mode=a.reward_mode)
    specs = held_out_specs(space, a.n_eval)
    eval_cfs = [{"process": c.process, "vdd": c.vdd, "temp": c.temp,
                 "rc": c.rc} for c in pdk.robust_search_corners()]
    ceiling = feasible_ceiling(specs, sur, eval_cfs)
    if ceiling.get("n_specs"):
        print(f"  ceiling: a real search against the surrogate solves "
              f"{ceiling['n_demonstrably_feasible']}/{ceiling['n_specs']} "
              f"({ceiling['fraction']:.0%}) of the held-out specs, so that is "
              f"what the policy is being measured against")

    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    done_steps = 0
    latest = POLICY_DIR / "LATEST"
    if a.resume and latest.exists():
        p = Path(latest.read_text().strip())
        if not p.is_absolute():
            p = ROOT / p
        model = ALGO.load(p, env=venv)
        done_steps = int(model.num_timesteps)
        print(f"resumed {p.name} at {done_steps} steps")
    elif a.algo == "sac":
        # ent_coef="auto": the entropy weight is tuned against a target
        # entropy rather than guessed. Fixing it is how a continuous-control
        # policy quietly collapses to deterministic, which would take the
        # proposal diversity with it.
        model = SAC("MlpPolicy", venv, batch_size=a.batch_size,
                    learning_rate=a.lr, ent_coef="auto", seed=a.seed,
                    verbose=0)
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
    best_margin = -np.inf

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
        # `algo` and `reward_mode` are recorded on every row because this log
        # is APPENDED to across checkpoints, and checkpoint 5's PPO rows were
        # produced under a different objective entirely. Without them the file
        # reads as one success-rate curve when it is two, and the older rows
        # carry none of the margin fields at all.
        rec = {"steps": done_steps, "wall_s": time.perf_counter() - t0,
               "eval_seed": EVAL_SEED, "algo": a.algo,
               "reward_mode": a.reward_mode, "feasible_ceiling": ceiling, **ev}
        if ceiling.get("n_demonstrably_feasible"):
            rec["success_vs_feasible"] = (
                ev["success_rate"] * ev["n"]
                / ceiling["n_demonstrably_feasible"])
        append_eval(rec)
        best_rate = max(best_rate, ev["success_rate"])
        best_margin = max(best_margin, ev["mean_best_m_min"])
        vs_feas = rec.get("success_vs_feasible")
        print(f"  {done_steps:>8} steps  success {ev['success_rate']:.1%}"
              + (f" ({vs_feas:.0%} of solvable)" if vs_feas is not None else "")
              + (f" [{ev.get('solved_at_start', 0)} at start]"
                 if ev.get("solved_at_start") else "")
              + f"  m_min {ev['mean_best_m_min']:+.4f} "
              f"(+{ev['mean_gain_m_min']:.4f} vs start, "
              f"{ev['frac_at_cap']:.0%} at cap)  "
              f"unc {ev['mean_uncertainty']:.3f}  "
              f"[{(time.perf_counter() - t0) / 60:.1f}m]", flush=True)

        # Keep the most recent few checkpoints; there is no reason to
        # accumulate hundreds. Ordered by MTIME, not by the step number in the
        # filename -- and that distinction is not cosmetic. A fresh run starts
        # at a low step count, so with a previous run's checkpoints still in
        # the directory, "keep the three highest-numbered" keeps the OLD run's
        # and deletes the new one it has just pointed LATEST at. Measured: with
        # checkpoint 5's PPO files present (921600, 972800, 1001472), saving
        # ckpt_25000 would have deleted it immediately and left LATEST naming a
        # file that no longer existed.
        cks = sorted(POLICY_DIR.glob("ckpt_*.zip"),
                     key=lambda f: f.stat().st_mtime)
        keep = {ck.resolve()}
        for old in cks[:-3]:
            if old.resolve() not in keep:
                old.unlink(missing_ok=True)

    ck = POLICY_DIR / f"ckpt_{done_steps}.zip"
    model.save(ck)
    write_atomic(latest, str(ck.relative_to(ROOT)))
    print(f"\n  {done_steps} steps in {(time.perf_counter() - t0) / 60:.1f} min")
    print(f"  LATEST -> {ck.relative_to(ROOT)}")
    # G6-1, in margin mode. The gate is the MARGIN, not the success rate.
    # A policy that scrapes over zero satisfies a success count and then fails
    # verification 58 % of the time; the buffer is the thing being trained for,
    # so the buffer is the thing that is gated. The success rate is still
    # printed, as context rather than as the criterion.
    from eqrl.env_goal import MARGIN_CAP
    hist = json.loads(EVAL_LOG.read_text())["evals"] if EVAL_LOG.exists() else []
    last = hist[-1] if hist else {}
    print(f"\n{'=' * 70}")
    if a.reward_mode == "margin":
        gain = last.get("mean_gain_m_min", float("nan"))
        ok = best_margin > 0.0 and gain >= a.g6_1_gain
        print(f"G6-1  mean best min-margin over held-out specs = "
              f"{best_margin:+.4f}   (need > 0)")
        print(f"      mean gain over the calibrated start      = "
              f"{gain:+.4f}   (need >= {a.g6_1_gain:+.2f})")
        print(f"      fraction reaching MARGIN_CAP={MARGIN_CAP:.3f}        = "
              f"{last.get('frac_at_cap', float('nan')):.0%}")
        print(f"      held-out success rate (context)          = "
              f"{best_rate:.1%}")
        print(f"      -> {'PASS' if ok else 'BELOW GATE'}")
    else:
        print(f"G2  best held-out success rate = {best_rate:.1%}  "
              f"(need >= {a.g2:.0%})   "
              f"{'PASS' if best_rate >= a.g2 else 'below gate'}")
    print("=" * 70)
    if _STOP or done_steps < a.steps:
        print(f"  resume: python3 scripts/train_policy.py --resume "
              f"--steps {a.steps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
