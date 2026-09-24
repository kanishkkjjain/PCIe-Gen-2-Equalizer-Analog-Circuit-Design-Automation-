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

## The SPICE-free version (`eqrl/start_physfit.py`)

The cost above is removed by taking every device estimate from `PhysFit`
instead of ngspice. Same derivation, one `.op` for `Vt` cached module-wide, and
`PhysFit`'s own fitted calibration (`k_pk = 1.677`, `k_f = 1.428`) in place of
the hand-tuned over-ask -- for a 7.5 dB target that asks 12.6 dB against the
14 dB found empirically, so both routes measure the same effect.

Building it turned up the likely root cause of why the SPICE version started so
much worse. The gm/ID chain demanded `Itail = 1694 uA`, which leaves only
**+0.0745 V** of tail headroom even at the widest tail -- against the 0.15 V the
shipped start insists on, and *worse* than the +0.082 V the project measured as
putting 56 % of starts out of saturation. An RL perturbation from there lands in
triode, where the reward clips and the policy has no gradient. Capping `Itail`
at what the tail can sustain (1088 uA) restores +0.150 V exactly. **Headroom is
a hard constraint; peaking has a 3-12 dB window to land in.**

| | start cost | solved |
|---|---|---|
| checkpoint 7 (shipped) | 1.2 s | 8/10 |
| systematic (SPICE `.op`) | 19.8 s | 8/10 |
| **physfit (SPICE-free)** | **0.2 s** | **8/10** |

Measured by `scripts/measure_start_fast.py`, which stops at the first proposal
passing all 20 corners rather than verifying all eight -- 27 verifications and
7.8 minutes against ~80 and 25. It reports coverage and rank but **not** the
per-proposal rate, which needs every proposal checked. Raw:
`results/solve_rate_physfit.json`.

Seven of ten solved at rank 1. At 0.2 s per reset this is cheap enough to run
**inside** RL training, which the SPICE version at 19.8 s categorically was not.

## What the MCP server now uses

The PhysFit derivation is the server's default start, selected through
`eqrl/start.py` rather than by monkeypatching. Two things made a seam necessary
rather than a nicety:

- `patch()` reaches `deploy_policy` through `sys.modules.get(...)`, and the MCP
  server imports that module **inside** `design_equalizer`. An import-time
  patch would therefore land on the policy path and miss the CEM fallback,
  leaving the two searches starting from different designs -- which
  `deploy_policy.start_for` exists specifically to prevent.
- `patch()` also rebound `env_goal.analytic_start_u`. Once `env_goal` went
  through the seam that name stopped existing, so a patch that still set it
  would have silently done nothing while the run reported the new start.

The seam is resolved per call, so there is no window in which two callers
disagree. `EQRL_START=physics` restores the derivation the saved policy was
trained against, and every result reports which one ran in `start_point`.

The `tap_pct = 1.55` in `start_physfit.derive` is a constant **by design** and
not an oversight: step 13 of the table above reads the post-cursor off a
single-pulse transient, and a transient is SPICE, which is the one thing that
file exists to avoid. 1.55 is what that measurement returned for the brief's
own spec, and the search moves it from there.

Measured through the MCP tool with the PhysFit start in force, three requests
end to end:

| request | result | method | wall |
|---|---|---|---|
| the brief's own spec | **passes**, 10/10 at all 20 corners | policy | 50.5 s |
| all seven settable limits given | **passes**, 10/10 at all 20 corners | policy | 44.9 s |
| 12 dB peaking, outside the trained range | routed to CEM, reported `all_pass: false` on `equalises` | CEM | 55.9 s |

Both passing runs sit inside the committed 28-102 s range, so the start change
costs no time at the server. The third is the case that matters most for
honesty: an out-of-range request still got a real 20-corner verification and
was **not** reported as a pass.

## The two specifications nothing solves

`spec5` and `spec8` are unsolved by the shipped start, the SPICE derivation and
the PhysFit derivation alike. Both fail on **eye width**, and both are rejected
at the 2-corner screen without ever reaching full verification.

```
                        spec5              spec8
peaking          5.64 - 10.64 dB    5.54 - 10.54 dB
f_peak          1.455 - 2.766 GHz  1.546 - 2.680 GHz
hd3                  < -31.51 dBc       < -32.59 dBc
noise                < 1.500 mVrms      < 1.500 mVrms
power                < 5.052 mW         < 11.614 mW
area                 < 0.050 mm2        < 0.050 mm2
eye height           > 103.7 mV         > 77.5 mV
eye width            > 0.4772 UI        > 0.4792 UI   <-- binding
best reached           0.4375 UI          0.4531 UI
short by                    9.1 %              5.7 %
```

For scale, the brief's own specification asks for **0.4000 UI**. These two ask
for ~0.478, close to 20 % more, and `spec5` additionally caps power at 5.05 mW
-- a third of the brief's 15 mW -- so it cannot buy the eye with current.

Nothing about a starting point closes a 5.7-9.1 % eye-width gap. This is the
`h2` limit from `findings-ctle-dfe.md`: one DFE tap cannot cancel the second
post-cursor, and the CTLE trades the first against it.

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
