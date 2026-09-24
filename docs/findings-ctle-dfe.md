# What the CTLE and the DFE tap are actually doing

Measured 2026-09-24 on the `experiments` branch. Nothing here changed the
shipped code; it is all characterisation of what `master` already does.

**Conclusion first: the framework is operating at close to the right point.**
The CTLE boost it aims for is the optimum, the tap polarity is correct, and the
one genuine defect — a tap lower bound sitting above the optimum — is worth
about 2 % on one spec and does not change how many specifications get solved.

---

## 1. The CTLE boost is already optimal

Sweeping the realised boost with everything else held fixed, at the nominal
corner, tap at 1 %:

| Realised boost | Eye width | Eye height |
|---|---|---|
| 4.88 dB | 0.488 UI | 125 mV |
| **7.40 dB** | **0.508 UI** | **202 mV** |
| 8.62 dB | 0.477 UI | 198 mV |
| 11.35 dB | 0.391 UI | 172 mV |

Optimum ≈ 7.4 dB. The framework's start aims at the centre of the 3–12 dB
window, which is 7.5 dB. **No change needed.**

An earlier analysis of the collected records suggested the optimum was 2–4 dB.
That was **confounded** — it compared designs whose widths, currents and load
resistors all differed. The controlled sweep above overturns it. Correlations
across a heterogeneous design set are not a substitute for holding things fixed.

## 2. The post-cursor is positive, so the tap polarity is right

`physics.analytic_start_u` sets the tap from a modelled `h[1]/h[0]` and records
that it comes out **negative** (−0.002 to −0.162), concluding the ideal tap
cannot be represented and clipping to the floor.

Measured directly with a single isolated pulse, it is **positive**:

| Boost | after channel | after CTLE | at summing node | h2/h0 |
|---|---|---|---|---|
| 4.88 dB | +0.372 | +0.137 | +0.127 | −0.004 |
| 7.40 dB | +0.372 | +0.083 | **+0.090** | −0.100 |
| 8.62 dB | +0.372 | +0.053 | +0.068 | −0.136 |
| 11.35 dB | +0.372 | −0.011 | −0.002 | **−0.204** |

Method validated: with the channel disabled the same measurement returns
`h1/h0 = +0.0000` exactly.

So the analytic pulse model has the **sign wrong**. It does not currently cause
harm, because both the model's answer and the correct answer point at "use the
smallest tap available" — but the rule is unreliable and should not be trusted
if the channel changes.

Do **not** flip the tap polarity. `tests/test_spice.py::test_tap_opposes_the_main_path`
is correct, and the measurement agrees with it.

## 3. The post-cursor depends mostly on *when* you sample

At the summing node, 7.40 dB:

| Sample point | h0 | h1/h0 |
|---|---|---|
| peak − 0.2 UI | 318 mV | +0.280 |
| peak | 348 mV | +0.090 |
| peak + 0.2 UI | 321 mV | +0.024 |
| peak + 0.4 UI | 252 mV | +0.007 |

Sampling 0.2 UI late keeps 92 % of the amplitude and cuts the post-cursor from
9 % to 2.4 %. The eye measurement picks whichever phase opens the eye widest,
which lands in that region — so the ISI the DFE has to cancel is ~2 %, not 9 %.

Any rule that sizes the tap from the post-cursor **at the pulse peak** will
over-tap by roughly 4×.

## 4. `tap_pct` understates the real correction by about 5×

The parameter is nominally `gmF/gm1`. What reaches the summing node is
`Igf × Rsum` against `h0`:

| `tap_pct` | Igf nominal | GmF tail W | Igf **actual** | Effective correction |
|---|---|---|---|---|
| 0.00 % | 0.00 µA | 0.500 µm *floored* | **5.00 µA** | **2.6 %** |
| 0.25 % | 2.50 µA | 0.500 µm *floored* | 5.00 µA | 2.6 % |
| 0.50 % | 5.00 µA | 0.500 µm *floored* | 5.00 µA | 2.6 % |
| 0.75 % | 7.50 µA | 0.750 µm | 7.50 µA | 3.9 % |
| 1.00 % | 10.00 µA | 1.000 µm | 10.00 µA | 5.2 % |
| 2.00 % | 20.00 µA | 2.000 µm | 20.00 µA | 10.3 % |

Two consequences:

- **`tap_pct = 0` is not zero.** The GmF tail device floors at 0.5 µm minimum
  width, so it always carries 5 µA. Anything at or below 0.5 % is the *same
  circuit*, delivering 2.6 % correction.

  **CORRECTION (measured later).** That holds for `Wtail = 60 µm`, which is the
  `FIXED_START` constant these sweeps used — it is **not** what the real runtime
  start uses. With `START_WTAIL_UM = 150 µm` the tail width does not floor
  (0.625 µm at tap 0.25 %), so the tap current keeps scaling down and the
  circuits are *not* identical. Only `Wgf` floors in the real flow. The "+2.3 %
  from lowering the bound" figure below inherits the same caveat and was never
  re-measured under the real start.
- The useful range of the parameter is squeezed between 0 and ~1 %, so it is a
  poor knob for a search to hold.

## 5. The DFE helps, but only just — and the bound stops it reaching the optimum

Same design, same boost, only the tap changed:

| Effective tap | Eye width | Eye height |
|---|---|---|
| 0 % (tap pair held at common mode) | 0.5117 UI | 204.7 mV |
| **2.6 % (the floor)** | **0.5156 UI** | **209.1 mV** |
| 5.2 % (`tap_pct` = 1 %, the current lower bound) | 0.5078 UI | 201.9 mV |
| 10.3 % (`tap_pct` = 2 %) | 0.4883 UI | 184.2 mV |

The optimum is at the floor. The DFE contributes **+2.1 % eye height** over
being truly off, and every larger tap is worse.

`nl.PARAM_BOUNDS["tap_pct"]` is `(1.0, 30.0)`. **The lower bound sits above the
optimum**, so the framework is forced to over-tap by about 2×.

Caveat on the width figures: 0.5156 vs 0.5117 is 0.0039 UI, exactly one step of
the 256-phase measurement grid, and boost calibration varies by a similar amount
run to run. The **height** difference is the trustworthy one.

## 6. Why fixing it is not worth shipping

Lowering the bound to 0 gains roughly **+2.3 % eye width, +4 % eye height**. It
does **not** change how many specifications get solved, because the two the
policy fails are not close:

| | Required eye width | Best proposal reached | Shortfall |
|---|---|---|---|
| spec5 | 0.4772 UI | 0.4375 UI | needs **+9.1 %** |
| spec8 | 0.4792 UI | 0.4531 UI | needs **+5.8 %** |

Both were rejected at the 2-corner eye screen; neither reached full
verification. A 2.3 % gain does not reach either.

The change would also make every measured headline number stale (per-proposal
rate, coverage, timing) since they were all measured with the current bounds.
That cost is not justified by margin on specs that already pass.

**Recorded as known and deliberately not fixed.**

## 7. The structural limit

Watch `h2/h0` in §2: as the CTLE boosts harder it cancels the first post-cursor
— reaching zero at 11.35 dB — but the **second** post-cursor grows, from −0.004
to −0.204.

A 1-tap DFE can only cancel the first. So:

- too little boost → h1 too large for the tap to finish
- too much boost → h1 gone, but h2 has grown and nothing can touch it
- the optimum is where these balance: **≈ 7.4 dB, which is where the framework
  already sits**

Eye width being the binding spec is therefore a **topology** limit, not a tuning
one. Within CTLE + 1-tap DFE, the two unsolved specs look genuinely out of
reach. A second tap is the change that would attack h2 directly.

## 8. What these measurements cannot see

The eye here — and in the shipped framework — is measured on a transient with:

- **no noise.** A CTLE boosting 7.4 dB boosts noise with the signal; a DFE
  cancels ISI *without* amplifying noise. That is the main reason DFEs beat
  CTLEs in practice, and it is invisible here. Input-referred noise is scored
  as a separate spec, but noise never closes the eye in this measurement.
- **no jitter.** Eye *width* in a real link is largely eaten by jitter, and the
  "sample 0.2 UI late" trick in §3 has far less room when the sampling instant
  wanders.
- **an ideal comparator and flip-flop.** `netlist.dfe_decision_path` says so:
  clk→Q of 20 ps against 50–150 ps for a real StrongARM.
- **a 200-bit pattern**, which does not reach worst-case run lengths.

So §5's "the DFE barely helps" is a statement about *this measurement*, not
about the architecture. On silicon the DFE would earn more of its keep, mostly
by letting the CTLE run softer and save noise.

Closing that gap means injecting `trnoise` into the eye deck and re-running §5 —
roughly an afternoon as a one-off study. Making it the framework's real
measurement is a much larger job: the eye becomes statistical, the specs become
BER-referenced, and the surrogate and policy both need retraining.

---

## Summary

| Finding | Action |
|---|---|
| CTLE optimum ≈ 7.4 dB; framework aims 7.5 dB | none needed |
| Post-cursor positive; polarity correct | none needed |
| Analytic pulse model has the sign wrong | unreliable, untrusted |
| `tap_pct` understates correction ~5× | documented here |
| Tap lower bound (1 %) above the optimum | known, not fixed: +2 %, no coverage change |
| h2 grows with boost, 1 tap cannot cancel it | structural; needs a 2nd tap |
| Eye measured without noise or jitter | tier-1 study if ever needed |
