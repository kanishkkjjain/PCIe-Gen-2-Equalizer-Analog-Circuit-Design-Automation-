# A starting point derived from physics, not from constants

Measured 2026-09-24 on the `experiments` branch.

**Result first: it does not solve more specifications.** Coverage is 8 of 10
either way, and the two specs the shipped framework cannot solve are still
unsolved. What it changes is *how* the starting design is arrived at — every
value now traces to a measurement or a stated constraint, where four of the
eleven previously began at hand-picked numbers.

---

## Why bother

`analytic.FIXED_START` hands the search four constants with no derivation
behind them:

```python
W = 20.0 um    L = 0.18 um    Wg1 = 40.0 um    Ig1 = 1.0 mA
```

Everything else in the start is derived — `Itail` from a tail-headroom rule,
`RL` from pole placement, `Rs`/`Cs` from a grid search against the closed-form
response, `Rsum` from mid-supply, `tap_pct` from a modelled post-cursor. But
those four are just numbers, and the brief asks for **zero human intervention**.

Two further defects motivated the rework, both measured:

- **DC gain is unconstrained.** Across 1 290 properly-biased designs it spans
  −14.3 to +8.8 dB, and **39 % sit above 0 dB** — amplifying everything rather
  than boosting the high end. The framework has a whole `equalises` check to
  catch this after the fact.
- **The pole ordering is not enforced.** In **32 %** of designs `f_p1 > f_p2`:
  the degeneration pole lands above the output pole, so the boost never
  completes before the roll-off starts. The solver compensates by inflating
  `Rs` — in one measured case to 2.8x the textbook value.

## The derivation

Order is forced by the dependencies. Nothing is iterated.

| Step | Value | Justification |
|---|---|---|
| 1 | `Wg1 = 100 x W_MIN = 42 um` | `Wgf = Wg1 x tap/100` must clear the PDK floor (0.42 um) at the lowest tap setting, 1 %. The shipped 40 um is *just* under, which is why the tap width floors while its current does not. |
| 2 | `Ig1 = 1.0 mA` | Free. Nothing downstream is sensitive to it. |
| 3 | `Rsum = VDD / Ig1` | Output common mode at mid supply. |
| 4 | `Cout` from one `.op` | `cdd(X1) + cgs(Gm1) + cgd(Gm1) + CL`. Measured, not estimated — Gm1's gate capacitance is the dominant term after `CL`. |
| 5 | `RL = 1/(2*pi*f_p2*Cout)` | Places the output pole at `f_p2 = 2.13 x f_target`, deliberately **above** the peak so the boost completes. |
| 6 | `k = target_peaking + 6.5 dB` | Over-ask. The `f_p2` roll-off eats the difference; calibrated against SPICE (14 dB asked -> 7.54 dB realised). |
| 7 | `gm = k / RL` | Follows from pinning **DC gain at 0 dB**: `DC = gm*RL/k = 1`. |
| 8 | `Rs = 2(k-1)/gm` | The same `k`, now as degeneration: `k = 1 + gm*Rs/2`. |
| 9 | `Cs = k/(2*pi*Rs*f_p1)` | `f_p1 = 0.80 x f_target`, below `f_p2` by construction. |
| 10 | `Vov = (Vcm - 0.10 - Vt)/2` | ICMR budget: `ICMR- = Vov_tail + Vt + Vov_pair`, equal Vov both. `Vt` measured by `.op`. |
| 11 | `Id = gm*Vov/2`, `Itail = 2*Id` | gm/ID. |
| 12 | `W`, `Wtail` | Bisected until the measured Vov hits target, at `L = 0.18` and `L = 0.5`. |
| 13 | `tap_pct` from a pulse | Single isolated bit, post-cursor read at the sampling phase that maximises `h0 - abs(h1) - abs(h2)`. |

### The worked example

For the brief's own specification:

```
W    48.20 um      RL   581.5 ohm     Rsum  1800 ohm
L     0.18 um      Rs   930.9 ohm     Wg1     42.0 um
Itail 1698 uA      Cs   571.2 fF      Ig1   1.00 mA
Wtail 136.18 um                       tap    1.551 %
```

All ten conditions pass at the typical corner, `all_pass = True`:

```
peaking     7.54 dB    3-12 dB          DC gain  -0.13 dB  (pinned 0)
f_peak      1.999 GHz  1.25-2.5 GHz     hd3      -37.0 dBc  < -30
noise       0.320 mV   < 1.5            power     7.93 mW   < 15
eye height  530.1 mV   > 100            eye width 0.5312 UI > 0.4
```

`Wgf = 42 x 0.01551 = 0.651 um`, clear of the 0.42 um floor — which is what
step 1 was for.

## What it measures

`scripts/measure_systematic_start.py` runs the project's own
`best_of_n.py --method policy --k 8 --n-specs 10` with **only** the starting
point swapped. Same surrogate, same `ckpt_120000` policy, same `PARAM_BOUNDS`,
same netlist, same screen-then-verify flow. Directly comparable to the
committed `results/best_of_n_policy.json`.

| spec | checkpoint 7 | systematic start | |
|---|---|---|---|
| 0 | 7/8 r1 | **8/8** r1 | better |
| 1 | 1/8 r3 | 1/8 **r2** | same |
| 2 | 8/8 r1 | 8/8 r1 | same |
| 3 | 8/8 r1 | 8/8 r1 | same |
| 4 | 8/8 r1 | 8/8 r1 | same |
| **5** | **0/8 unsolved** | **0/8 unsolved** | same |
| 6 | 7/8 r1 | 6/8 r1 | worse |
| 7 | 2/8 **r7** | **6/8 r2** | better |
| **8** | **0/8 unsolved** | **0/8 unsolved** | same |
| 9 | 4/8 r2 | 2/8 **r1** | worse |

```
solved         8/10   ->   8/10      no change
per proposal   56.2 % ->   58.8 %    +2.6 points
```

Raw result: `results/best_of_n_policy_systematic.json`.

## Reading the result honestly

**Coverage is unchanged.** `spec5` and `spec8` remain unsolved. That is
consistent with what was already measured: they need **+9.1 %** and **+5.8 %**
eye width respectively, and both were rejected at the 2-corner eye screen. A
better starting design does not produce that much eye width. They are blocked by
the topology — one DFE tap cannot cancel the second post-cursor — not by where
the search begins.

**The per-proposal gain is inside the noise.** 56.2 % to 58.8 % is two extra
proposals out of eighty. At n = 10 specs the one-sigma spread is about ±15 %,
and two specs improved while two got worse. This is not evidence of an
improvement.

**The one real gain is rank.** `spec7` moved from first success at rank 7 to
rank 2, `spec9` from rank 2 to rank 1. Fewer 20-corner verifications to reach an
answer is genuine saved compute, but it is a cost saving, not a capability.

**So: better engineering, same capability.** The search was already finding its
way from the weaker start. The 8/10 plateau was never a starting-point problem.

## Known cost, and how to remove it

The derivation spends about **30 `.op` runs per specification** — measuring
`Cout` and `Vt`, then bisecting `W` and `Wtail`. The shipped
`physics.analytic_start_u` does the equivalent with **zero** SPICE, using
`PhysFit.cout` and `PhysFit.gm_m1`.

That matters beyond the ~5 minutes it adds here: **a SPICE-free start is the
only kind usable inside RL training**, where the environment resets thousands of
times. Only `Vt` is missing from `PhysFit`, and it barely moves with device
size, so one measurement would serve every spec. Until that is done this
derivation is a deployment-time tool, not a training-time one.

## Files

- `eqrl/start_systematic.py` — the derivation, and `patch()` to swap it in
- `scripts/measure_systematic_start.py` — the measurement runner
- `results/best_of_n_policy_systematic.json` — the raw result
- `docs/findings-ctle-dfe.md` — the CTLE/DFE characterisation this grew out of
