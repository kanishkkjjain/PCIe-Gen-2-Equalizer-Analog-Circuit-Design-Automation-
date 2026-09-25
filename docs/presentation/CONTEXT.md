# Project context for the presentation

Everything the deck draws on. Every number here was read out of a committed
artifact, and the artifact is named beside it. Nothing is estimated.

Read this together with `INSTRUCTIONS.md`, which says how to turn it into
slides. The images live in `figures/` next to this file.

---

## 0. Title-slide facts

| | |
|---|---|
| Problem statement | **AI/ML for Analog Circuit Design** |
| Project | A reinforcement-learning framework that sizes a PCIe Gen2 receive equalizer |
| Team | **Team Sillycon** |
| College | **BITS Pilani – Pilani Campus** |
| Members | **Kanishk Jain**, **Karthik Raj Palleboyina** |

---

## 1. The problem statement

### 1.1 The brief, verbatim

> **Problem Statement**
>
> Demonstrate a fully automated framework for schematic design of analog
> circuits using Reinforcement Learning. Given circuit specifications, build an
> automated design flow that sizes devices using fewer search spaces (lowest
> design time) to reach near-optimal solutions. Use case: Equalizer circuit
> design (PCIe PHY). Should take significantly lower time than sweeping all MOS,
> R, C, L parameter space, with zero human intervention.
>
> **Specifications:** Nyquist Freq (PCIe Gen 2, 5.0 Gbps): 2.5 GHz; HF peaking
> boost 3–12 dB (tunable 1.25–2.5 GHz); 1-Stage CTLE w/ source degeneration
> (variable Rs, Cs) + 1-Tap DFE; NRZ signaling; Linearity HD3 < -30 dB (100 MHz
> diff input); Input-referred noise < 1.5 mVrms (10 MHz–5 GHz); Power < 15 mW;
> Area < 0.05 mm² (130nm PDK); Eye opening > 0.4 UI (H) & > 100 mV (V); Meet
> across PVT (TT,SS,FF,SF,FS; VDD±5%; 0–125°C).
>
> **Objective**
>
> Develop a RL based automation framework to automatically design an equalizer
> for the given specifications, with minimal to zero human intervention.
>
> **Deliverables**
>
> A Reinforcement Learning based Python framework that takes target specs as
> input, seamlessly integrates with a spice simulator — outputs the final
> schematic and resulting specs.
> Bonus: LLM-based human interaction with wrapper to fine-tune.

### 1.2 What that means as numbers

Ten conditions are scored. Eight are the brief's; two are validity checks the
framework adds so a design cannot pass by cheating.

| # | condition | requirement |
|---|---|---|
| 1 | HF peaking | 3 – 12 dB |
| 2 | peak frequency | 1.25 – 2.5 GHz |
| 3 | HD3 linearity | < −30 dBc |
| 4 | input-referred noise | < 1.5 mV rms |
| 5 | power | < 15 mW |
| 6 | area | < 0.05 mm² |
| 7 | eye height | > 100 mV |
| 8 | eye width | > 0.4 UI |
| 9 | *bias* (internal) | every device biased in saturation |
| 10 | *equalises* (internal) | eye more open at the slicer than at the input |

Source: `eqrl/mcp_server.py::_limits`, `eqrl/specs.py::EqualizerSpec`.

**Why 9 and 10 exist, in one line each.** A circuit can hit every electrical
number while sitting in triode, where the small-signal model that produced
those numbers does not apply. And a circuit can enlarge the eye with plain gain
without equalising anything — amplifying the distortion along with the signal.
Both are caught explicitly.

### 1.3 Every condition must hold at 20 corners

5 process corners (**tt, ss, ff, sf, fs**) × 2 supplies (**1.71 V, 1.89 V**,
i.e. 1.8 V ±5 %) × 2 temperatures (**0 °C, 125 °C**) = **20 corners**.

A design is reported as passing **only** after all 20 were actually simulated.

### 1.4 Why this is hard

- **11 continuous knobs.** Not a menu of choices — a continuous 11-dimensional
  space, where most combinations produce a circuit that does not bias correctly
  at all.
- **The conditions fight each other.** More boost costs power and linearity.
  More current opens the eye and burns the power budget. Wider devices help gm
  and hurt bandwidth.
- **Simulation is the bottleneck.** One full evaluation is 20 SPICE runs. A
  parameter sweep that visited even 10 points per knob would be 10¹¹ designs.
- **Zero human intervention is required.** Nothing may be hand-tuned per spec.

### 1.5 Technology

SKY130 open-source PDK, ngspice, Python. Signalling is NRZ at 5 Gb/s, so one
bit is 200 ps and Nyquist is 2.5 GHz.

---

## 2. The circuit

### 2.1 Architecture

Fixed by the brief and not changed anywhere in the project:

```
TX  →  lossy channel  →  CTLE  →  summing stage  →  slicer
                                       ↑
                                  1-tap DFE feedback
```

The topology follows the open reference design
`github.com/muhammadaldacher/SERDES-Design-of-RX-Decision-Feedback-Equalizer`
(a 1-tap DFE for NRZ over FR4). **Credit it on the slide.**

### 2.2 What each block is for, in plain language

**The channel** loses high frequencies. Fast edges arrive smeared, so each bit
bleeds into the ones after it. That smearing is what closes the eye.

**The CTLE** is a differential pair whose source degeneration is `Rs` in
parallel with `Cs`. At low frequency `Cs` is an open circuit, so `Rs`
degenerates the pair and the gain is low. At high frequency `Cs` shorts `Rs`
out, the degeneration disappears and the gain rises. That rise is the *boost* —
the amplifier deliberately favours exactly the frequencies the channel killed.

- `Rs ‖ Cs` sets **where** the boost starts (the zero).
- `RL · Cout` sets **where** the response rolls off (the output pole).
- The gap between them is **how much** boost you get.

**The summing stage** adds two currents into one pair of load resistors
`Rsum`: the CTLE output through the main transconductor `Gm1`, and a scaled
copy of the previous decided bit through the feedback transconductor `GmF`.
Subtracting that copy cancels the tail the previous bit left behind.

- `tap_pct` scales the feedback path — **both** its device width and its
  current — so the tap strength is one knob, not two.

**The slicer** decides 1 or 0 at the sampling instant. Its decision is what
feeds back through the tap.

### 2.3 The 11 design variables

These are the framework's entire output. Everything else about the circuit is
fixed.

| knob | what it sets | range |
|---|---|---|
| `W` | CTLE input pair width (µm) | 1 – 100 |
| `L` | CTLE input pair length (µm) | 0.15 – 1.0 |
| `Itail` | CTLE tail current (A) | 50 µ – 2 m |
| `Wtail` | CTLE tail device width (µm) | 5 – 200 |
| `RL` | CTLE load resistance (Ω) | 100 – 5 000 |
| `Rs` | degeneration resistance (Ω) | 50 – 10 000 |
| `Cs` | degeneration capacitance (F) | 10 f – 3 p |
| `Rsum` | summing-stage load (Ω) | 100 – 2 000 |
| `Wg1` | main transconductor width (µm) | 1 – 100 |
| `Ig1` | main transconductor current (A) | 100 µ – 4 m |
| `tap_pct` | DFE tap strength (%) | 1 – 30 |

Source: `eqrl/netlist.py::PARAM_BOUNDS`.

### 2.4 The schematic is real, and checked

`netlists/eq_core.sch` opens in xschem, and a test netlists it with xschem and
diffs the connectivity against the netlist the framework actually simulates.
The picture and the circuit cannot drift apart.

---

## 3. System-level overview

### 3.1 The flow, per request

```
target spec → derive a starting design → propose 8 candidates
            → screen the eye in SPICE at 2 corners
            → verify survivors in SPICE at all 20 corners
            → sized netlist + report
```

### 3.2 The rule everything obeys

> **The learned model only ever proposes. SPICE decides.**

`all_pass` is never set from the model's opinion. It becomes true only after
20 real corner simulations all passed. This is what makes the output
trustworthy rather than merely plausible.

### 3.3 What each component is for

| component | job |
|---|---|
| **Physics layer** | Turn a spec into a sensible starting design with no simulation |
| **Dataset** | Teach the model what SPICE actually does to this circuit |
| **Surrogate network** | Predict all ten conditions in ~1 ms instead of ~2 s |
| **RL policy** | Walk from the starting design to one that meets the spec |
| **Fallback search** | Answer requests outside the policy's trained range |
| **Eye screen** | Kill hopeless candidates cheaply, before the expensive check |
| **20-corner verify** | Issue the verdict. The only thing allowed to say "passes" |
| **LLM wrapper** | Take the request in plain English and report honestly |

### 3.4 Built once vs. run per request

- **Built once** (hours, offline): collect 5,993 measurements → train the
  surrogate → train the policy. Produces two saved files.
- **Run per request** (~40 seconds): derive → propose → screen → verify.

---

## 4. Deep dive, component by component

### 4.1 The physics layer and the starting design

**Job:** given any spec, produce a good starting design without running SPICE.

Not a stored constant — it is *derived* for each spec:

1. Pick the target peak frequency at the centre of the requested window.
2. Place the output pole above it so the boost completes before roll-off.
3. Get `RL` from that pole and the estimated output capacitance.
4. Get the required `gm` from the boost target, and `Rs` from `gm`.
5. Get `Cs` from `Rs` and where the zero must sit.
6. Size the devices for that `gm`, then cap the tail current at what the tail
   device can carry and stay out of triode.
7. Set `Rsum` so the output common mode sits at mid-supply.

The closed form reads optimistically, by a factor measured once across the
whole dataset: **`k_pk = 1.677`** on peaking and **`k_f = 1.428`** on
frequency. The targets are pre-multiplied by those, so the derivation asks for
what it needs in order to get what it wants.

**Metric — does it work?** **0.2 s per specification**, and **7 of 10 specs are
solved by the very first proposal** it produces.
Source: `results/solve_rate_physfit.json`, `eqrl/start_physfit.py`.

### 4.2 The dataset PhysFit learns from

**Job:** measure enough real circuits that a model can learn the gap between
hand calculation and SPICE.

- **1,500 designs**, each simulated at 4 corners → **5,993 measurement rows**.
- Cost, paid once: 1,500 × 4.42 s = **1 h 50 min** of ngspice.
- **14,753 SPICE evaluations** logged over the project in total.
- Sampled from three places, deliberately: spread over the whole space (so the
  model learns where circuits *fail*), clustered around known-good designs, and
  clustered around the derived starting points (so the model is accurate
  exactly where the policy begins).
- Every row records all ten conditions plus the bias state.

Source: `models/surrogate/.../config.json`, `eqrl/cache.py`.

### 4.3 PhysFit — the fast evaluator

**Job:** say what ngspice would say, fast enough to be called hundreds of
thousands of times.

> **Naming.** This deck uses **PhysFit** for the whole fast evaluator: the
> closed-form circuit equations *together with* the neural correction trained
> on top of them. (In the codebase `PhysFit` is the device-model layer and
> `Surrogate` the network; they are one block as far as the system is
> concerned.)

#### What it is

```
PhysFit prediction  =  hand calculation  ×  bounded learned correction
```

**It is not a black box.** The network never predicts the circuit from
scratch — it predicts a *multiplier* on the closed-form answer, and that
multiplier is **capped at ×7.4**. Before training the correction is exactly
1.0, so the model *starts* as the hand calculation and can only be nudged from
there. In practice the learned corrections sit around **×1.8**. This is why it
cannot drift away from physics: it is not free to.

#### What it predicts

**15 outputs per design, per corner.**

| kind | count | what |
|---|---|---|
| physics × correction | **5** | peaking, peak frequency, DC gain, power, input-referred noise |
| predicted directly | **9** | Nyquist gain, HD3, eye height, eye width, eye improvement, input eye openness, gm of the CTLE pair, gm of the summer, output common mode |
| pass/fail flag | **1** | is every device biased in saturation |

#### How it is built and trained

| | |
|---|---|
| inputs | **32 features**, all physically meaningful — gm, Cout, pole and zero positions, computed pulse-response cursors, supply, temperature, process |
| shape | 32 → **128 → 128** (SiLU) → three output heads |
| ensemble | **5 networks trained independently**; where they disagree, the prediction is uncertain, and that is usable information |
| training | 3,000 epochs, **25 % of the data held out** and never trained on |

Source: `eqrl/surrogate.py`, `models/surrogate/.../config.json`.

#### Metric 1 — how much time it saves

This is the number the whole architecture rests on. **Same design, same four
corners, measured both ways:**

| | per design | |
|---|---|---|
| ngspice | **4,420 ms** | `results/baseline.json` |
| PhysFit | **0.77 ms** | measured, 200 repetitions |
| | **≈ 5,700× faster** | |

#### Metric 2 — how good is it?

*Does it agree with SPICE about pass/fail?* On 375 held-out designs:

| power | area | noise | eye height | peaking | HD3 | peak freq | eye width |
|---|---|---|---|---|---|---|---|
| 100 % | 100 % | 94 % | 91 % | 89 % | 89 % | 83 % | 82 % |

*How close are the numbers?* R² on held-out designs — power 0.965, DC gain
0.956, Nyquist gain 0.937, peaking 0.903, HD3 0.816, eye height 0.800.

*Does it rank designs correctly?* That is what actually matters for search.
Rank correlation **0.805**, rising to **0.884** on properly-biased designs —
the only population the policy ever chooses among.

*Does it fool itself?* A model you optimise against will find its own blind
spots. So: let a search hunt for the model's single favourite design, then
simulate that design for real. The model over-rated it by **0.00095** — under a
tenth of one percent.
Source: `models/surrogate/.../validation.json`, `results/surrogate_check.json`.

### 4.4 The RL policy

**Job:** given a spec and a starting design, move the 11 knobs until the spec
is met.

- **Goal-conditioned Soft Actor-Critic.** The target spec is part of the input,
  so one trained agent serves any spec in range — it is not retrained per
  request.
- **Output is 11 numbers at once**, not one. A continuous control policy, like
  a robot driving every joint simultaneously.
- **20 steps per episode**, each a small adjustment to all 11 knobs. The agent
  steers; it does not answer in one shot.
- 120,000 training steps, learning rate 3e-4.

#### The cost function

Two stages, and the second is the important one.

1. **Each condition is scored on its own**, not as a hard pass/fail but as a
   soft ramp: full marks inside the requirement, falling off smoothly outside
   it. A smooth score gives the agent a direction to move in even while it is
   still failing — a bare pass/fail would be flat everywhere and teach nothing.
2. **The reward is the WORST of those scores, across every corner.** Never the
   average.

> Averaging is the trap. A design that is superb on nine conditions and fails
> the tenth would score well on an average and is worthless in reality. Taking
> the minimum means the agent can only improve its reward by fixing whatever is
> currently worst — which is exactly what a circuit designer does.

A **bonus is added when a design passes everything**, so the agent is pulled
past "nearly meets spec" rather than settling just short of it.

#### The choices, and why

| choice | why |
|---|---|
| **SAC**, not PPO | SAC keeps a deliberately random policy. We need 8 *different* candidates from one start; a near-deterministic agent returns 8 copies and best-of-8 buys nothing. |
| **Goal-conditioned** — the spec is an input | One trained agent serves any specification in range. The alternative is retraining per request, which defeats the purpose. |
| **20 small steps**, not one shot | The agent steers toward a design. One-shot regression from spec to 11 numbers has no way to react to what it has produced so far. |
| **Worst-margin reward** | See above — a design is only as good as its weakest condition at its worst corner. |
| **Start from the physics design**, not random | An 11-dimensional space is far too large to find the working region blind. |

**Deliberately random.** The policy samples from a distribution rather than
returning a fixed answer, so running it 8 times from the same start gives 8
genuinely different designs. The seeds are fixed, so the 8 are reproducible.

#### How it was trained — and what that would have cost in ngspice

Training runs **entirely inside PhysFit**. The simulator is never called.

| | |
|---|---|
| as actually run | **120,000 steps in 50 minutes** |
| if ngspice were in the loop | 120,000 × 4.42 s = **6.1 days** of simulation alone |

*And 6.1 days is the optimistic figure — it counts only the simulation, not the
learning on top of it.* Training the agent at all is only possible because the
evaluator is ~5,700× faster than the simulator.

During training, the share of specifications the agent could satisfy rose
**0.88 → 0.96 → 1.0** between 10k and 110k steps. (Measured against PhysFit, in
training — the real-SPICE number is the 56.2 % below.)
Source: `results/policy_train_eval.json`.

#### Metrics

**56.2 % of proposals (45 of 80) survive full 20-corner verification**; **8 of
10 unseen specifications solved**; **0.26 s to produce all 8 proposals**,
because proposing touches no simulator at all — it runs entirely inside
PhysFit.
Source: `results/best_of_n_policy.json`; the timing measured directly (§7.2).

### 4.5 The fallback search

**Job:** answer requests that fall outside the range the agent was trained on.

> **Say this precisely.** The range being checked is the span of
> *specifications* the RL agent saw during training — peaking 5–10 dB, peak
> frequency 1.1–1.6 / 2.0–2.8 GHz, HD3 −34 to −26 dBc, power 3–15 mW, eye
> height 60–130 mV, eye width 0.35–0.50 UI. It is a limit on **what you can
> ask for**, not on which circuits PhysFit can evaluate. PhysFit will happily
> evaluate a design for any request; it is the *agent* that has only been
> taught to aim at targets inside that box.

Every incoming request is checked field by field against that range. Outside
it, the policy would still answer — networks always answer — but the answer
would carry none of its measured reliability. So the framework switches method
and says it did.

The fallback is a cross-entropy search: scatter 40 variations around the
starting design, score them all with the surrogate, keep the best, move the
centre toward them, repeat — 2,000 evaluations, still entirely inside the
model, ~20 s. A search has no trained range, which is exactly why it can go
where the policy cannot.

**Metric:** **32.5 % per proposal** over its first 8 (against the policy's
56.2 %), **8 of 10** solved. And because the two methods succeed on
*different* specifications, running both gives **9 of 10**.
Source: `results/best_of_n_surrogate_cem.json`.

### 4.6 The eye screen

**Job:** throw out hopeless candidates before paying for the full corner sweep.

The eye is the one condition with a comparator in it, and a comparator has no
closed form. So instead of predicting it, the framework **measures** it — but
at two corners rather than twenty.

Which two is a measured result, not a guess. Over 280 designs simulated at the
full grid, the corner that decides eye width most often is `sf` at 0 °C.
Screening the two `sf` 0 °C corners recovers the true 20-corner worst case
**89.6 %** of the time.

**Metric — what it saved:**

| | evaluations | time |
|---|---|---|
| verify every proposal at 20 corners | **540** | ~10 min |
| screen 8 × 2, then verify ~2 × 20 | **56** | ~1.5 min |

It is a filter, not a verdict. Anything it lets through still faces all 20
corners.
Source: `HANDOVER.md` §20.3, `scripts/best_of_n.py`.

### 4.7 The 20-corner verification

**Job:** issue the verdict, and be the only thing allowed to.

Every surviving candidate is simulated at all 20 corners. For each condition
the framework keeps the **worst** margin across all of them. `all_pass` is true
only when every one of the ten worst-case margins is non-negative.

Margins are reported exactly as measured. Nothing is rounded toward passing.

### 4.8 The LLM wrapper (the brief's bonus deliverable)

**Job:** let a person ask for a circuit in plain English and get an honest
answer.

Three tools, exposed over MCP so any LLM host can drive the framework:

| tool | what it does | cost |
|---|---|---|
| `check_spec` | Is this request reachable? | < 1 s, no simulation |
| `design_equalizer` | Size it and verify at 20 corners | ~40 s, real SPICE |
| `explain_capabilities` | What the framework can and cannot do | instant |

Honesty is enforced in code, not asked for in a prompt. Every answer states
which method ran, whether the request was inside the trained range, and which
starting derivation produced the design. A design that misses is still returned
— with the specs it missed and by how much — and is never described as passing.

Each run also writes a self-contained HTML report: scorecard, eye diagram, AC
response, the 11 sized devices, and the netlist.

---

## 5. Results

### 5.1 How often it meets a specification

On **10 unseen specifications**, with every candidate verified at all 20
corners:

| | solved |
|---|---|
| RL policy, 8 candidates | **8 / 10** |
| Search alone | 8 / 10 |
| **Both methods together** | **9 / 10** |

The policy reaches its coverage in far fewer candidates than the search needs.
Source: `results/best_of_n_policy.json`, `results/best_of_n_surrogate_cem.json`.

### 5.2 How long it takes

**Median 36.9 s**, range **28.2 – 101.7 s**, over 16 measured runs.
Source: `results/mcp_e2e.json`.

Three recent end-to-end runs through the LLM wrapper: **50.5 s**, **44.9 s**
and **44.1 s**, each passing all ten conditions at all 20 corners.

### 5.3 Against the sweep the brief asks us to beat

The brief says the framework should take *"significantly lower time than
sweeping all MOS, R, C, L parameter space."* So the comparison was run:
random search, same candidate budget, same simulator, same corners, same
scorer.

| | best reward | designs that passed |
|---|---|---|
| random search, 95 candidates | 0.726 | **0 of 95** |
| this framework | **0.987** | **a passing design** |

An exhaustive sweep at even 10 points per knob would be 10¹¹ designs; at ~2 s
each that is longer than the age of the universe. The framework answers in
under a minute.

### 5.4 A verified design

Every one of the ten conditions passing at all 20 PVT corners. Worst-case
margin per condition, as a fraction of the requirement:

| condition | worst-case margin |
|---|---|
| area | +99.2 % |
| power | +71.8 % |
| eye height | +59.1 % |
| peaking | +41.8 % |
| HD3 | +39.9 % |
| noise | +33.2 % |
| peak frequency | +19.9 % |
| **eye width** | **+13.3 %** ← tightest |
| bias | pass |
| equalises | pass |

Source: `results/report_design.json`.

### 5.5 The design in the figures

A **separate** run, at the same specification, with the current code — this is
the one that produced the eye and AC images in `figures/`, so these are the
numbers printed on those two charts. (The margin table above is from the
committed artifact and is a different run; both passed all 20 corners.)

| | measured | required |
|---|---|---|
| eye height | **237 mV** | > 100 mV |
| eye width | **0.516 UI** | > 0.4 UI |
| peaking | **8.19 dB** | 3 – 12 dB |
| peak frequency | **1.89 GHz** | 1.25 – 2.5 GHz |
| time to design and verify | **44.1 s** | — |

*0.516 UI means the eye stays open for more than half the bit period — the
sampling instant has room to be wrong and still read the bit correctly.*

*8.19 dB of boost, peaking at 1.89 GHz — just below the 2.5 GHz Nyquist
frequency, which is exactly where the channel's loss bites hardest.*

### 5.6 Engineering quality

**327 automated tests passing** — 282 that need no PDK, 45 that run real
ngspice. They check the claims, not just the code: that the schematic matches
the simulated netlist, that the reported margins match the per-corner data,
that documented numbers match the artifacts they came from.

### 5.7 Against the brief, point by point

| the brief asked for | delivered |
|---|---|
| RL framework, specs in → schematic + specs out | ✅ |
| Integrates with a SPICE simulator | ✅ ngspice, 20 corners |
| Zero human intervention | ✅ the starting point is derived, not hand-seeded |
| Faster than sweeping the parameter space | ✅ under a minute vs. intractable |
| Meets specs across PVT | ✅ all 20 corners simulated for every pass |
| *Bonus:* LLM interaction wrapper | ✅ 3 MCP tools |

---

## 6. Figure inventory

All in `figures/` beside this file. **Use every one of them.**

| file | what it shows | goes on |
|---|---|---|
| `02a_schematic_full.png` | The complete equalizer schematic, both stages, exported from the real xschem file | §2, full-bleed |
| `02b_ctle_stage.png` | Stage 1 only — the CTLE differential pair with `Rs ‖ Cs` degeneration | §2, CTLE slide |
| `02c_summing_stage.png` | Stage 2 only — the summing stage with `Gm1` main path and `GmF` tap | §2, DFE slide |
| `03a_flow.png` | The five-box flow: spec → propose → screen → verify → report, with the caption that SPICE decides | §3, flow slide |
| `04a_physics_anchor.png` | Left: prediction = hand calculation × bounded correction. Right: the correction capped at ×7.4 | §4, surrogate slide |
| `04b_surrogate_agreement.png` | Bar chart, how often the model agrees with SPICE about each condition passing | §4, surrogate metric slide |
| `05a_coverage.png` | Step curve: specifications solved vs. candidates tried, policy vs. search, with the 9/10 line | §5, coverage slide |
| `05b_time.png` | Histogram of time per design over 16 runs, median marked | §5, timing slide |
| `05c_eye.png` | The measured eye diagram at the slicer — 237 mV / 0.516 UI, both limits in the title | §5, eye slide |
| `05d_ac_response.png` | The measured CTLE response — 8.19 dB of boost peaking at 1.89 GHz, inside the shaded required window, with Nyquist marked | §5, AC slide |

**Note on `02a`:** it is very wide and dense. Use it once, full width, as the
"here is the whole circuit" moment — do not try to make its device labels
readable. `02b` and `02c` are the readable halves.

---

## 7. Speed, and why it decides the architecture

This section is the backbone of the revised deck. §1 uses it to state the
problem; §3 uses it to explain the system; §4 uses it to justify PhysFit.

### 7.1 What one ngspice evaluation costs

**4.42 seconds** for one design at 4 corners.
Source: `results/baseline.json` — 95 candidates, 760 ngspice launches, 420.3 s
wall. That is 8 launches per design, two per corner.

And that is the **fast** path already: trimmed per-corner model libraries make
these evaluations **22.4× faster** than loading the full SKY130 library each
time. 4.42 s is what the cost looks like *after* optimisation.

Multiply it out:

| | designs | ngspice time |
|---|---|---|
| one design, 4 corners | 1 | 4.42 s |
| one design, all 20 corners | 1 | ~22 s |
| the RL agent's training budget | 120,000 | **6.1 days** |
| a 3-point grid over 11 knobs | 177,147 | **217.7 hours** |
| a 5-point grid over 11 knobs | 48,800,000 | **6.8 years** |

*The brief asks for "fewer search spaces" and "lowest design time". These are
the numbers that make that the central problem rather than a nice-to-have.*

### 7.2 The per-phase budget of one request

Measured directly, timing each phase through the same functions the framework
calls. Three independent runs:

| phase | run 1 | run 2 | run 3 |
|---|---|---|---|
| LLM + MCP layer (parse, envelope check) | 0.3 ms | 0.1 ms | 0.1 ms |
| Start point (calculations + one SPICE operating point) | 0.64 s | 0.61 s | 0.58 s |
| PhysFit ⇄ RL loop — all 8 candidates | 0.26 s | 0.23 s | 0.23 s |
| **everything before verification** | **0.90 s** | **0.84 s** | **0.81 s** |
| ngspice eye screen + 20-corner verification | 53.4 s | 52.2 s | 52.2 s |
| **ngspice share of the run** | **98.3 %** | **98.4 %** | **98.5 %** |

> These runs verified sequentially, so their totals are larger than the
> **36.9 s production median** (§5.2), which runs the corners in parallel.
> Parallelism changes only the ngspice part — the 0.9 s before it is the same
> either way. Against the production median: **~0.9 s of thinking, ~36 s of
> simulating, about 97 % of the run in ngspice.**

**This is the sentence the system-overview slide has to land:** the searching,
the learning and the reasoning together cost under a second. Essentially all
the wall-clock time is the one thing we refuse to approximate — the final
verdict in a real simulator.

### 7.3 Training the agent: why PhysFit is not optional

| | |
|---|---|
| training as actually run, inside PhysFit | **120,000 steps in 50 minutes** |
| the same training with ngspice in the loop | **6.1 days** — simulation only |
| building the dataset PhysFit learned from | 1 h 50 min, **paid once** |

Source: `results/policy_train_eval.json`, and 120,000 × 4.42 s.

---

## 8. Vocabulary, for writing the speaker notes

Short plain-language glosses. Use them when a term first appears.

- **Eye diagram** — every bit of a long random sequence drawn on top of every
  other. The open gap in the middle is the margin the receiver has to work
  with. A closed eye means bit errors.
- **UI (unit interval)** — one bit period. At 5 Gb/s, 200 ps. "0.4 UI" means
  the eye must stay open for at least 40 % of a bit.
- **PVT corner** — a manufacturing-and-environment combination: fast or slow
  silicon, supply high or low, hot or cold. The chip must work at all of them.
- **CTLE** — continuous-time linear equalizer. An amplifier shaped to boost
  exactly the frequencies the cable lost.
- **DFE tap** — subtracts a scaled copy of the bit just decided, cancelling the
  smear it left behind.
- **gm (transconductance)** — how much output current a transistor gives per
  volt of input. The basic currency of analog gain.
- **Saturation** — the operating region where a transistor behaves as an
  amplifier. Outside it the design's equations stop being true.
- **Surrogate** — a fast stand-in for the simulator.
- **HD3** — third-harmonic distortion. How much the amplifier invents signal
  that was not there. Lower (more negative) is better.
