# PCIe Gen2 Equalizer — RL-based Automated Analog Sizing

A Reinforcement-Learning framework that takes **target equalizer specs** and
automatically sizes a SKY130 equalizer, driving **ngspice** in the loop and
emitting the sized netlist + verified specs.

**Use case:** PCIe Gen2 (5 Gb/s, Nyquist 2.5 GHz) — a 1-stage CTLE with source
degeneration feeding a separate **DFE summing stage**, across a lossy FR4-like
channel.

> **Read [`HANDOVER.md`](HANDOVER.md) before changing anything.** §0 quotes the
> problem statement verbatim, §10 documents 31 bugs found and fixed (plus a
> list of this project's own claims that measurement disproved), and §11/§12
> say exactly what is *not* built.
> [`VERIFY.md`](VERIFY.md) shows how to reproduce every claim yourself.

## Result

**All 8 brief specs pass at all 20 of the brief's PVT corners**, with the
channel present and the DFE active. (The scorecard shows 10 — the other two are
internal checks: devices in saturation, and "does it actually equalise".)
Found in **95 candidates**, worst case over all 20 corners:

```
peaking 5.89 dB  vs 3-12       f_peak  1.781 GHz vs 1.25-2.5   eye H 131.1 mV vs 100
hd3    -31.2 dBc  vs <-30      noise   0.617 mV  vs <1.5       eye W 0.563 UI vs 0.4
power    2.89 mW  vs <15       area    0.00031 mm2 vs <0.05
```

**Tightest margin: `hd3`, +4.1% — only 1.24 dB.** That is the honest number:
HD3 is measured at the SLICER, across both stages. Measured after stage 1 only
— as this project did until it was caught — the same design reads −55.2 dBc and
appears to have 25 dB of margin it does not have. The summing stage's Gm1 is an
undegenerated pair, so it is the more distorting half and the one the slicer
sees. See `HANDOVER.md` §10 bug 27.

**It genuinely equalises**, not merely amplifies: **1.97×
eye-openness improvement** over the equaliser input (1.53× worst-case).
Eye height alone cannot show this, because summing-stage gain is a free
variable (it correlates with eye height at 0.98, peaking at 0.47), so openness
— opening as a fraction of the swing at the same node — is scored instead.

**What the RL adds over the hand calculation:** the analytic seed alone reaches
**5 of 8** brief specs (it fails eye height, eye width and HD3 across PVT);
after CEM it is **8 of 8**. The equations solve 4 of the 11 knobs from the AC
response and model no distortion, noise, eye or power — so the spec that now
binds is one they cannot see. `results/seed_vs_search.json`.

Eye measured at **200 mV pp** transmitter drive — conservative
against a real Gen2 transmitter's ~800 mV. Converged at iteration
2 of 15; 800 ngspice launches. Full write-up in
`HANDOVER.md` §16.

## Checkpoint 6 — the objective was the bug

Checkpoint 5 ended with a working surrogate and a policy that solved **0 of 20**
held-out specs. The natural reading is "RL does not work here". The narrower
claim tested in checkpoint 6 is that the *reward* was wrong, not the method.

**The reward was a count, and passing is conjunctive.** `specs.score` mixes a
weighted soft score with `HARD_PASS_WEIGHT x n_pass / n`. A policy can raise
that by improving five conditions while driving a sixth negative — and be paid
for it. `specs.margin_vector` replaces it with the ten pass conditions as
continuous margins, scored on their **minimum**, which has the property

```
all_pass(score(m, spec))   <==>   min(margin_vector(m, spec)) >= 0
```

so the only way to raise the score is to fix whatever is currently worst.
Checked over all 5 993 collected records (171 of them passing): **0
disagreements**.

**Stopping at the pass boundary trained the fragility in.** The episode used to
end the moment the *surrogate* said all_pass — exactly where model error decides
the verdict. Now it never terminates early, and the reward keeps paying for
margin beyond zero.

### How much margin is worth buying (measured before training)

`scripts/margin_transfer.py` crosses 373 held-out designs with 600 sampled
goals — 223 800 pairs, no simulation — and compares predicted min-margin with
the real one from measured metrics. Spearman **+0.821**. But the relationship is
**not monotone**:

```
predicted margin   real pass rate
[+0.00, +0.02)         32.0 %
[+0.02, +0.05)         24.7 %
[+0.05, +0.08)         48.9 %
[+0.08, +0.10)         64.2 %
[+0.10, +0.15)         66.7 %   <- best supported
[+0.15, +0.20)         46.2 %
[+0.20,   inf)         40.0 %
```

It peaks and then **falls**: a very large predicted margin mostly means the
model is confidently wrong. So the reward is *capped* at 0.125 rather than
unbounded — the constant comes from this table, and `scripts/status.py` warns
if the two drift apart.

### The result, and the one number it is reported as

**It takes n = more than 32 proposals to have a 90 % chance of solving a spec.**
Both methods plateau at 80 %, and they get there very differently:

| | specs solved | per proposal | proposal cost |
|---|---|---|---|
| **SAC policy** | **80 % at n = 7** | **35.2 %** | 1 s (forward pass) |
| CEM + surrogate | 80 % at n = 20 | 17.8 % | 42 s (a search, every time) |

Ten held-out specs, one committed script, every proposal verified at all 20 PVT
corners. "Solved" means one of the n proposals meets **every** brief spec at
**every** corner — nothing weaker.

**They fail on different specs, and that is the useful part:**

```
CEM unsolved      spec5, spec7
policy unsolved   spec5, spec8
union             9 of 10 = 90 %
```

Running both and keeping whichever passes hits the 90 % target today, with no
new code. Only spec5 defeats both, and it may be infeasible — the best design
among 5 993 collected records misses it by **eye_h −0.1 %**.

### What is still wrong

**Both methods optimise corners they are not graded on.** Proposal uses 4
search corners; acceptance uses a 20-corner grid. Two of the four are not in
the grid at all, and **18 of the 20 that decide pass/fail are never seen** —
not by the search, not by the policy, not by the surrogate in training. Two
methods with quite different search behaviour plateauing at exactly 80 % is
what that looks like. HANDOVER 19.11; untested, and the prediction is recorded
before the test.

**Proposal diversity failed its gate** (0.148 against 0.25, fixed in advance).
The policy's eight proposals are near-copies, so best-of-n is doing less work
than its name implies.

## Status

| | |
|---|---|
| Two-stage equalizer (CTLE → Gm1+GmF summing stage), all-real SKY130 devices | ✅ |
| Lossy FR4-like channel, −16.8 dB at Nyquist, 10.6 dB of tilt | ✅ |
| Closed-loop DFE in SPICE (ideal comparator + 1 UI latch) | ✅ |
| **RL loop end-to-end, 8/8 brief specs at 20/20 corners** | ✅ |
| Automatic analytic starting point (no hand-seeding) | ✅ |
| Test suite (236: 199 fast + 37 against ngspice) | ✅ all passing |
| **Schematic output** (we emit netlists) | ❌ |
| **Sweep-vs-RL baseline** — CEM 0.987 (passes) vs random 0.726 (0/95) | ✅ |
| **Seed-vs-search measured** — analytic 5/8 specs → after CEM 8/8 | ✅ |
| **Durable, resumable evaluation cache** — 5 993 records, 0 lost to 5 interruptions | ✅ |
| **Physics-informed surrogate** — G1 rank correlation 0.88, optimism gap median +0.018 (n=14) | ✅ |
| **Surrogate-assisted CEM** — 8/8 specs at 20/20 corners, binding margin +10.1% | ✅ |
| **Goal-conditioned RL** — PPO, checkpoint 5 | ❌ 0/20 specs; the reward was a count (HANDOVER 18.18) |
| **Goal-conditioned RL** — SAC on a min-margin reward, checkpoint 6 | ✅ **80% of specs at n=7 proposals vs CEM's n=20**; 35.2% per proposal vs 17.8% |
| **Proposal diversity** (gate G6-4) | ❌ 0.148 against a 0.25 target fixed in advance |
| Runtime "tunable" mechanism · Anaconda env · LLM wrapper | ❌ |

## Three methods, and why there are three

**Seeded CEM** (checkpoints 1–4) is the default agent and the baseline. The
brief stresses *fewest search points / lowest design time*, and gradient-free
policy search closed this problem in 95 candidates. But it optimises **one
spec**: give it a different target and it starts over, keeping nothing — 7
minutes and ~800 ngspice launches, every time.

**A goal-conditioned policy** (checkpoint 5) is trained once and applied to
specs it has never seen. The spec is part of the observation, so the policy
learns the mapping from *(where the design is, what is being asked)* to *(what
to change)* rather than the answer to one question.

The obstacle is arithmetic: PPO needs ~1M environment steps, and every step of
`CtleEnv` is ~18 s of SPICE across four corners — over two hundred days. So the
policy is trained inside a **learned model of the circuit** and only the
finished policy meets ngspice (the standard construction: MBTD3 DAC 2024,
AnaCraft TCAD 2025). Four gated stages:

```
collect_data.py    2 524 designs x 4 corners, resumable, crash-drilled
train_surrogate.py physics-informed surrogate        gate G1  rank correlation
train_policy.py    goal-conditioned PPO inside it    gate G2  held-out success
deploy_policy.py   real ngspice on unseen specs      gate G3  vs CEM, equal budget
```

The surrogate is **grey-box**: metrics with a closed form are predicted as a
correction to it, with the residual head zero-initialised so the model *starts*
as the hand calculation and training can only improve on it. The eye is
computed from the **pulse response** (channel → CTLE → summer, inverse
transformed, sampled at symbol spacing) rather than learned from AC features —
which took eye-width rank correlation from 0.36 to 0.69.

### What actually works: propose against the model, verify in SPICE

The surrogate is not an oracle and must not be used as one. Measured over
**10 specs x 10 proposals, every proposal simulated for real** (100 SPICE
verifications):

```
check the top 1 proposal  ->  20% of specs solved      4 SPICE evaluations
check the top 4           ->  50%                     16
check the top 8           ->  90%   <-                32
check the top 10          ->  90%   (plateau)         40
```

> **RETRACTED BY CHECKPOINT 6 — this measured a weaker test than the one the
> project ships against.** `32 = 8 proposals x 4 corners`: the quick *search*
> corners, not the 20-corner PVT grid that decides acceptance, and the
> artifact records no 20-corner check at all. Re-measured at 20 corners by a
> committed script (`scripts/best_of_n.py`), the same method reaches **80 % of
> specs and needs n = 20 to get there**, not 90 % at n = 8. The per-proposal
> rate held up almost exactly (18 % then, 17.8 % now); it was the coverage
> claim that did not. See HANDOVER 19.9. The numbers below are kept because
> they are what the run measured, not because they are the answer.

**Per new spec, end to end, at the 20-corner acceptance grid:**

| | SPICE evaluations | wall clock |
|---|---|---|
| seeded CEM (checkpoints 1-4) | 380 | ~7 min |
| CEM + surrogate, n=20 for 80 % | 400 | ~14 min |
| **SAC policy, n=7 for 80 %** | **140** | **~2.5 min** |
| **both, taking whichever passes -> 90 %** | **540** | **~10 min** |

The policy proposes 7 designs in **one second** -- a forward pass, no
simulator. CEM spends 42 s of search for its 20, and pays that again for every
new spec; the policy's training is spent once.

**Do not trust the model's ranking.** Spearman between predicted and real
reward across those 100 proposals is **-0.33** -- *negative*. The design the
surrogate rates highest is, if anything, less likely to survive, because the
highest predicted rewards are exactly where the model is most over-optimistic.
Verify all eight in parallel and take whichever passes; that is also the
fastest option, so the honest workflow and the fast one coincide. Ensemble
disagreement separates pass from fail only weakly (0.0316 vs 0.0373) and is not
a usable filter.

A single surrogate-chosen design was verified across all 20 PVT corners and
**passes 8/8 brief specs**, beating the shipped checkpoint-3 design on six of
eight margins: binding margin **+10.1 %** against +4.1 %, hd3 -36.2 dBc against
-31.2, eye height 220.2 mV against 131.1. `results/surrogate_optimum_pvt.json`.

### The price, stated plainly

The surrogate cost **11 986 ngspice launches** to build, once. Break-even
against the shipped flow is **~17 specs**.

That cost is not specific to this method. A policy cannot be trained against
SPICE -- 1M steps x 18 s is over two hundred days -- so the surrogate is a
**prerequisite** for the RL approach too. Both paths pay the same 11 986
launches; only one of them then works.

## Status

| | |
|---|---|
| Two-stage equalizer (CTLE → Gm1+GmF summing stage), all-real SKY130 devices | ✅ |
| Lossy FR4-like channel, −16.8 dB at Nyquist, 10.6 dB of tilt | ✅ |
| Closed-loop DFE in SPICE (ideal comparator + 1 UI latch) | ✅ |
| **RL loop end-to-end, 8/8 brief specs at 20/20 corners** | ✅ |
| Automatic analytic starting point (no hand-seeding) | ✅ |
| Test suite (236: 199 fast + 37 against ngspice) | ✅ all passing |
| **Schematic output** (we emit netlists) | ❌ |
| **Sweep-vs-RL baseline** — CEM 0.987 (passes) vs random 0.726 (0/95) | ✅ |
| **Seed-vs-search measured** — analytic 5/8 specs → after CEM 8/8 | ✅ |
| **Durable, resumable evaluation cache** — 5 993 records, 0 lost to 5 interruptions | ✅ |
| **Physics-informed surrogate** — G1 rank correlation 0.88, optimism gap median +0.018 (n=14) | ✅ |
| **Surrogate-assisted CEM** — 8/8 specs at 20/20 corners, binding margin +10.1% | ✅ |
| **Goal-conditioned RL** — PPO, checkpoint 5 | ❌ 0/20 specs; the reward was a count (HANDOVER 18.18) |
| **Goal-conditioned RL** — SAC on a min-margin reward, checkpoint 6 | ✅ **80% of specs at n=7 proposals vs CEM's n=20**; 35.2% per proposal vs 17.8% |
| **Proposal diversity** (gate G6-4) | ❌ 0.148 against a 0.25 target fixed in advance |
| Runtime "tunable" mechanism · Anaconda env · LLM wrapper | ❌ |

## Three methods, and why there are three

**Seeded CEM** (checkpoints 1–4) is the default agent and the baseline. The
brief stresses *fewest search points / lowest design time*, and gradient-free
policy search closed this problem in 95 candidates. But it optimises **one
spec**: give it a different target and it starts over, keeping nothing — 7
minutes and ~800 ngspice launches, every time.

**A goal-conditioned policy** (checkpoint 5) is trained once and applied to
specs it has never seen. The spec is part of the observation, so the policy
learns the mapping from *(where the design is, what is being asked)* to *(what
to change)* rather than the answer to one question.

The obstacle is arithmetic: PPO needs ~1M environment steps, and every step of
`CtleEnv` is ~18 s of SPICE across four corners — over two hundred days. So the
policy is trained inside a **learned model of the circuit** and only the
finished policy meets ngspice (the standard construction: MBTD3 DAC 2024,
AnaCraft TCAD 2025). Four gated stages:

```
collect_data.py    2 524 designs x 4 corners, resumable, crash-drilled
train_surrogate.py physics-informed surrogate        gate G1  rank correlation
train_policy.py    goal-conditioned PPO inside it    gate G2  held-out success
deploy_policy.py   real ngspice on unseen specs      gate G3  vs CEM, equal budget
```

The surrogate is **grey-box**: metrics with a closed form are predicted as a
correction to it, with the residual head zero-initialised so the model *starts*
as the hand calculation and training can only improve on it. The eye is
computed from the **pulse response** (channel → CTLE → summer, inverse
transformed, sampled at symbol spacing) rather than learned from AC features —
which took eye-width rank correlation from 0.36 to 0.69.

### The surrogate is good enough to design with

`scripts/check_surrogate.py` exists to look for the failure this class of
method is famous for — a model that over-rates its own favourite design. It
optimises *inside* the surrogate and then simulates the winner for real.

The first run of it looked reassuring and was **n = 1**:

```
predicted reward 0.9986   real (ngspice) 0.9977   optimism gap +0.0009
```

Repeated over 14 optimiser-chosen designs, it is not reassuring at all. The
model claims all 14 pass; SPICE agrees about **4**. Optimism gap median
**+0.018**, mean +0.040, worst **+0.173** — the +0.0009 above was one of the
lucky draws. This is why the shipped workflow verifies every proposal rather
than trusting the model's pick (HANDOVER 18.19).

and the design it turned up **passes all 8 brief specs at all 20 PVT corners**,
with more margin than the shipped one on six of eight:

```
binding spec   hd3 +10.1%   vs the shipped design's +4.1%
hd3  -36.2 dBc vs -31.2     eye H 220.2 mV vs 131.1
power 2.48 mW  vs 2.89      eye improvement 2.38x vs 1.97x
```

**Stated honestly, this is not "a better design in 1.9 seconds".** The
surrogate cost **11 986 ngspice launches** to train, once. After that, a new
spec costs 2 000 surrogate evaluations (1.9 s, no simulator) plus ~44 launches
to verify, against 760 launches for the shipped flow — so break-even is around
**17 specs**. It is a one-off investment that pays back only when the tool is
used repeatedly, which is the case the brief describes.

## Layout

```
eqrl/
  pdk.py        SKY130 discovery + PVT corner grids (quick/spec/full)
  netlist.py    CTLE, DFE summer, channel, decision path, geometry maps
  spice.py      ngspice driver: combined deck, fatal-error scan, trimmed libs
  dfe.py        PRBS, NRZ PWL, eye extraction
  specs.py      target specs + reward (soft scores + hard-pass bonus)
  analytic.py   closed-form starting point, calibrated against SPICE
  trimlib.py    trimmed per-corner model libraries + validation
  env.py        Objective (parallel, cached) + Gymnasium CtleEnv
  agents/cem.py Cross-Entropy-Method policy search
  design.py     orchestrator: specs in -> sized decks + report.json
  cache.py      durable append-only JSONL log of every SPICE evaluation
  physics.py    closed forms usable without SPICE: channel ABCD, pulse
                response, noise, power, gm/Cout fits, analytic start
  surrogate.py  grey-box residual surrogate (physics + learned correction)
  env_goal.py   goal-conditioned env: the spec is an input, not a constant
tests/          236 total: 199 pure (no PDK, ~32 s) + 37 against real ngspice
scripts/        run_design, build_trimlib, pvt_sweep, smoke_test, plotting,
                baseline (random search at an equal candidate budget)
netlists/       xschem schematics -- CROSS-CHECK ONLY, currently STALE
```

## Design variables (11)

`W, L` (CTLE pair) · `RL` · `Rs, Cs` (degeneration — **Rs sets the boost, Cs the
zero**) · `Itail, Wtail` · `Rsum` (summing load) · `Wg1, Ig1` (main
transconductor) · `tap_pct` (DFE tap weight, scaled as a whole branch).
Bounds in `netlist.py::PARAM_BOUNDS`.

## Setup

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b
pip install -r requirements.txt
```
ngspice-42 (XSPICE + KLU), xschem 3.4.4, Python 3.12. SKY130 via volare.

## Run

```bash
python3 -m pytest tests/ -q                        # 236 tests, ~2 min
python3 scripts/build_trimlib.py --grid spec       # 22.4x faster evaluations
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel
```

## Four things that will bite you

1. **`mult=` is inert on sky130 devices — use `m=`.** On the poly resistor
   `mult` appears only in the Monte-Carlo mismatch terms: `mult=4` measures
   **1407.6 Ω**, identical to one unit, while `m=4` gives the correct 351.9 Ω.
   This silently made every resistance below ~450 Ω up to 9× too large.
2. **ngspice folds instance-name case.** `XT1` and `Xt1` are the same device.
3. **A single nfet may not exceed 100 µm total width**, and `nf` does not help —
   binning is on total W.
4. **"5 corners" is a miscount.** The brief's `TT,SS,FF,SF,FS; VDD±5%; 0–125 °C`
   is 5 × 2 × 2 = **20** corners, and sky130 keeps R/C on a *separate* axis the
   named corners never touch.

## Version control

Git initialised; identity set **repo-local**. SPICE dumps and plots are
gitignored; `results/` is tracked as evidence. `report.json` is the RL run's
output; `pvt_sweep.{json,csv}` is the same design swept by a different
**driver** and agrees on all 20 corners. That is not an *independent*
confirmation — both go through `Simulator.evaluate()`, so it checks the
reporting path, not the physics. See `HANDOVER.md` §12b.
