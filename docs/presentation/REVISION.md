# Revision: restructure the deck

You built this deck already. **The visual design is right — keep it.** What
changes is the order of the argument and where the emphasis falls.

Read the updated **`CONTEXT.md`** before you start. It has a new **§7, "Speed,
and why it decides the architecture"**, which did not exist when you built the
first version. That section is the spine of the new deck.

---

## Keep exactly as they are

- Typography, colour palette, layout system, spacing.
- The section-label convention in the corner (`01 · THE PROBLEM`,
  `04 · INSIDE EACH PART — THE RL AGENT`), updated to the new section names.
- The divider-slide treatment.
- Speaker notes on every slide, 60–120 words, in the same voice.
- The images in `figures/` — nine of the ten carry over unchanged. See
  **Figures** below for the one exception and why.
- The rules from the original instructions: ~15 words of body text per slide,
  one idea per slide, a visual on every slide, every number glossed.

---

## The argument the new deck makes

The old deck explained the system bottom-up: here is a component, here is
another component. The new one makes **one argument**, and every section is a
step in it:

> 1. The brief's real problem is **search time and search space**.
> 2. ngspice costs **4.42 s per design** — far too slow to search with, and
>    hopeless to train inside.
> 3. So we built **PhysFit**, a stand-in that is **~5,700× faster**.
> 4. We trained an RL agent inside PhysFit — **50 minutes** instead of
>    **6.1 days**.
> 5. And we spend real simulation only on the final verdict.

If a slide does not serve that argument, it does not need to be there.

---

## New structure — 34 slides

| § | section | slides | what changes |
|---|---|---|---|
| — | Title | 1 | unchanged |
| 1 | The problem | 5 | **reframed** around search cost, **+1 new slide** |
| 2 | The circuit | 6 | **completely unchanged** |
| 3 | System overview | 3 | **new block diagram**, replaces the old three |
| 4 | Inside the three components | 12 | re-homed and expanded |
| 5 | Results | 6 | reports **8 / 10** only |
| 6 | Outside the trained range | 4 | **new section**, ends on **9 / 10** |

---

## Old → new: what happens to all 35 existing slides

| old | title | action |
|---|---|---|
| 1 | Title | **keep** |
| 2 | The brief | **edit** — reframe on search time / search space (new 2) |
| 3 | Ten conditions | **keep** (new 3) |
| 4 | 20 corners | **edit** — add what 20 corners *cost* (new 4) |
| — | — | **NEW slide 5**: what one ngspice evaluation costs |
| 5–10 | The circuit, all six | **keep, untouched** (new 6–11) |
| 11 | Divider "How the system works" | **keep** (new 12) |
| 12 | One request, five steps | **replace** with the new block diagram (new 13) |
| 13 | "The model only ever proposes" | **keep** (new 14) |
| 14 | The parts / trust increases | **drop** — the new diagram covers it |
| 15 | Divider + built-once/per-request | **keep as divider** (new 15) |
| 16 | Starting from physics | **keep** (new 16) |
| 17 | Does the starting point work? | **keep** (new 17) |
| 18 | The dataset | **move** into the PhysFit block (new 19) |
| 19 | Physics × bounded correction | **move** into PhysFit (new 18) |
| 20 | Model vs simulator agreement | **move** into PhysFit (new 22) |
| 21 | Can the model fool itself? | **move** into PhysFit (new 23) |
| 22 | The RL agent | **keep** (new 24) |
| 23 | Deliberately random | **keep** (new 26) |
| 24 | How well the agent does | **keep** (new 27) |
| 25 | Outside what it learned | **move** to §6 and expand (new 32) |
| 26 | The eye screen | **move** to §5 (new 30) |
| 27 | The LLM wrapper | **move** to §3, fold into the diagram slide's notes, and keep one slim slide at the end of §4 (new 28) |
| 28 | Divider "Results" | **keep** (new 29) |
| 29 | How often it meets a spec | **edit** — show the agent's 8/10 only (new 31 → see §5) |
| 30 | How long it takes | **keep** |
| 31 | Beating the sweep | **keep** |
| 32 | A verified design | **keep** |
| 33 | Every margin, worst case | **keep** |
| 34 | Against the brief | **keep** |
| 35 | Closing | **keep, last** |

Nothing is thrown away except old slide 14, whose content the new block diagram
absorbs.

---

## §1 — The problem (5 slides)

The reframing is the point. The old §1 said "here are the specs". The new one
says "**the specs are the easy part; finding them in an 11-dimensional space
without waiting years is the hard part**".

**Slide 2 — The brief.**
Same slide, new emphasis. Lead with the brief's own words: **"fewer search
spaces"**, **"lowest design time"**, **"zero human intervention"**. Make
*search time* visually the biggest thing on the slide.
*Notes:* the brief is not only "design an equalizer" — it is "design it
without searching the whole space, and do it fast". That framing decides
everything that follows.

**Slide 3 — What "meets spec" means.**
Unchanged. The ten-condition table.

**Slide 4 — And it must hold 20 times over.**
Keep the corner grid, but add the cost: **one design = 20 corners = ~22 s of
ngspice.**
*Notes:* as before on what a corner is — then land that every single candidate
design carries this price tag.

**Slide 5 — NEW: What a search actually costs.**
The centrepiece of §1. From **§7.1** of `CONTEXT.md`:

| | ngspice time |
|---|---|
| one design, 4 corners | **4.42 s** |
| a 3-point grid over 11 knobs | **217 hours** |
| a 5-point grid over 11 knobs | **6.8 years** |

Underneath, one line, large and unresolved:
**"We need to evaluate circuits thousands of times faster."**
*Notes:* stress that 4.42 s is already the *optimised* number — trimmed model
libraries made it 22.4× faster than the naive path. Then the arithmetic: an
exhaustive sweep is not slow, it is impossible. End by posing the question but
**not answering it** — the answer arrives in §4 as PhysFit. Do not name PhysFit
on this slide.

---

## §2 — The circuit (6 slides)

**No changes at all.** Old slides 5–10 carry over exactly.

---

## §3 — System overview (3 slides)

**Slide 12 — Divider, "How the system works".** Unchanged.

**Slide 13 — NEW: the system, and where the time goes.**
This replaces old slides 12 and 14. It is the most important new slide in the
deck. Full drawing spec below. Nothing on the slide but the diagram and its
timings.
*Notes:* walk the chain left to right in one sentence per block. Then land the
point: everything except the final verification costs **under a second**;
about **97 %** of the wall clock is ngspice. We did not make simulation fast —
we made sure we almost never have to do it.

**Slide 14 — "The model only ever proposes. SPICE decides."**
Unchanged from the old deck. It reads even better directly after the timing
diagram.

### Drawing spec — the system block diagram

Left-to-right chain, five stages, with a time under every one.

```
          ┌──────────────────┐                    ┌────────────────────────┐
 spec ──► │  LLM             │ ──► Start point ──►│   ┌─────────┐          │──► ngspice
          │  ┌────────────┐  │     back-of-the-   │   │ PhysFit │ ◄──┐     │    verification
          │  │ MCP server │  │     envelope calc  │   └─────────┘    │     │    2-corner screen
          │  └────────────┘  │     + a little     │        │         │     │    + all 20 corners
          └──────────────────┘     SPICE          │        ▼         │     │
                                                  │   ┌──────────┐   │     │
                                                  │   │ RL agent │ ──┘     │
                                                  │   └──────────┘         │
                                                  └────────────────────────┘
          < 1 ms                  0.6 s                  0.25 s                 ~36 s
```

Requirements:

1. **The LLM block is an outer box that visually contains the MCP server box.**
   Not two boxes side by side — one inside the other.
2. **PhysFit and the RL agent sit inside one outer box**, joined by a **loop**:
   two arrows forming a cycle between them. Label the outer box
   **"the search loop"** and the cycle **"propose → score → adjust, ×160"**.
3. **A time label under every block**, in the accent colour, using the numbers
   above (from **§7.2** of `CONTEXT.md`).
4. **Underneath everything, a single horizontal stacked bar** representing the
   total ~37 s, with each stage's share drawn **to scale**. The ngspice segment
   will occupy ~97 % of the bar and the first three stages will be a sliver at
   the left — **that visual disproportion is the entire message**, so do not
   "fix" it by making the small segments legible. Label the sliver
   **"0.9 s — everything else"** with a leader line, and the long segment
   **"~36 s — ngspice verification"**.
5. Caption under the bar: **"≈ 37 s total."**

Colour the ngspice stage in the warm accent; the rest in grey and the cool
accent, matching the palette already used.

---

## §4 — Inside the three components (12 slides)

**Three components only**, in this order. Use the component name in the corner
label, as the old deck did.

**Slide 15 — Divider, "Inside the three parts".**
Keep the built-once / per-request diagram from the old divider.

### Component 1 — the starting point (slides 16–17)

**16. Starting from physics, not from random.** Unchanged.
**17. Does the starting point work?** Unchanged (7 of 10 at the first proposal).

### Component 2 — PhysFit (slides 18–23)

This is the answer to the question slide 5 asked. **Name it here for the
first time**, and say what it is before saying how good it is.

**18. PhysFit = physics × a bounded correction.**
`figures/04a_physics_anchor.png`, full width. (Old slide 19.)
*Notes:* the network never predicts the circuit from scratch — it predicts a
multiplier on the hand calculation, capped at ×7.4, and starts at exactly 1.0.
It cannot drift away from physics because it is not free to.

**19. What it learned from.**
The dataset numbers (old slide 18): **1,500 designs**, **5,993 measurements**,
the three sampling clouds. Add: **1 h 50 min of ngspice, paid once.**
*Notes:* why sample in three places. Then: this is the only large simulation
bill in the whole project, and it is paid once.

**20. NEW: What PhysFit predicts.**
The output table from **§4.3** of `CONTEXT.md`: **5** physics-×-correction
outputs, **9** direct, **1** flag. Plus the shape: 32 → 128 → 128 → 3 heads,
**×5 networks**.
*Notes:* it predicts all ten scored conditions plus the device quantities the
reward needs. Five independent networks — where they disagree, we know the
prediction is uncertain.

**21. NEW: How much time it saves.**
The headline of the whole deck. Two numbers, very large, side by side:
**ngspice 4,420 ms** / **PhysFit 0.77 ms**, and beneath them **≈ 5,700×**.
*Notes:* same design, same four corners, measured both ways. This single ratio
is what makes everything else in the project possible — the search, and above
all the training.

**22. Does PhysFit agree with the simulator?**
`figures/04b_surrogate_agreement.png`. (Old slide 20.)
*Notes:* unchanged.

**23. Can it fool itself?**
The 0.99864 / 0.99769 / gap 0.00095 slide. (Old slide 21.)
*Notes:* unchanged.

### Component 3 — the RL agent (slides 24–28)

**24. The RL agent.** (Old slide 22, unchanged.)

**25. NEW: What it is rewarded for.**
The cost function, from **§4.4** of `CONTEXT.md`. Two steps, shown as two short
lines: *score every condition softly* → *take the **worst** one, across every
corner*. Then the one-line reason, which deserves space:
**"Average the scores and a design that fails one condition still looks good."**
*Notes:* explain the soft ramp — a bare pass/fail is flat and teaches nothing,
a ramp gives a direction to move in. Then the minimum: the agent can only
improve its reward by fixing whatever is currently worst, which is what a
designer does. Mention the bonus for passing everything, which stops it
settling just short.

**26. Why the agent is deliberately random.** (Old slide 23, unchanged.)

**27. NEW: How it was trained — and what that would have cost.**
Two rows, the contrast doing all the work:

| inside PhysFit | **50 minutes** |
|---|---|
| with ngspice in the loop | **6.1 days** |

Plus one line: *120,000 steps.*
*Notes:* this is the slide that closes the argument opened on slide 5.
Training simply is not possible with a simulator in the loop — and 6.1 days is
the optimistic figure, counting only simulation and not the learning on top.
Mention that the agent's in-training success rose 0.88 → 1.0 between 10k and
110k steps, and be precise that this was measured against PhysFit, not SPICE.

**28. How well the agent does.**
Old slide 24, with the corrected number: **56.2 %** of proposals survive
20-corner verification, **0.26 s** to produce all 8.
*Notes:* one roll of the dice works about half the time; eight rolls and keep
the best is what reaches 8 of 10. Also fold in the LLM wrapper here in one
sentence — it is the brief's bonus deliverable, three MCP tools, and every
answer states which method ran and whether the request was in range.

---

## §5 — Results (6 slides)

**Report the agent's own coverage: 8 of 10.** The 9 of 10 belongs to §6.

**29. Divider, "Results".** Unchanged.

**30. Measuring the eye instead of predicting it.**
Old slide 26, moved here — it belongs with the verification story.
*Notes:* unchanged.

**31. How often it meets a specification.**
`figures/05a_coverage.png`. **Show the RL agent's curve reaching 8 of 10.**
If the figure also plots the search and the 9/10 line, note in the speaker
notes that the second curve is explained in the next section — or crop the
message to the agent's line and save the rest for slide 33.

**32. How long it takes.** `figures/05b_time.png`. Unchanged.

**33. Beating the sweep.** Unchanged.

**34. A verified design.** The eye and AC figures. Unchanged.

**35. Every margin, worst case.** Unchanged.

---

## §6 — When the request is outside the trained range (4 slides)

**36. Divider, "Outside the trained range".**

**37. What the agent was taught to aim at.**
The trained range as a small table: peaking 5–10 dB, peak frequency
1.1–1.6 / 2.0–2.8 GHz, HD3 −34 to −26 dBc, power 3–15 mW, eye height
60–130 mV, eye width 0.35–0.50 UI.
*Notes:* **be precise here** — this is a limit on *what you can ask for*, not
on which circuits PhysFit can evaluate. PhysFit will score any design. It is
the agent that has only been taught to aim at targets inside this box. Every
request is checked against it field by field, before anything runs.

**38. Outside it, we switch method and say so.**
The comparison table: agent **56.2 %** / 0.26 s / in-range only; search
**32.5 %** / ~20 s / anywhere.
*Notes:* a neural network always answers — that is the danger. Outside its
range the answer carries none of its measured reliability, so the framework
switches to a cross-entropy search, which has no trained range because it is a
search rather than a learned map. It still runs entirely inside PhysFit:
2,000 evaluations in ~20 s. And the result always names which method answered.

**39. And the two together do better than either.**
The payoff. Three numbers: agent **8/10**, search **8/10**, together
**9 of 10**.
*Notes:* they succeed on *different* specifications, so running both reaches
9 of 10 where either alone reaches 8. No new code — just spending more time.

**40. Against the brief + closing.**
Keep old slides 34 and 35 as the last two.

---

## Figures

The original instructions said "use every one of the ten figures". That rule
now has **one deliberate exception**.

| figure | where it goes now |
|---|---|
| `02a_schematic_full.png` | unchanged, §2 |
| `02b_ctle_stage.png` | unchanged, §2 |
| `02c_summing_stage.png` | unchanged, §2 |
| `03a_flow.png` | **retired** — see below |
| `04a_physics_anchor.png` | slide 18, inside PhysFit |
| `04b_surrogate_agreement.png` | slide 22, inside PhysFit |
| `05a_coverage.png` | slide 31, §5 |
| `05b_time.png` | slide 32, §5 |
| `05c_eye.png` | slide 34, unchanged |
| `05d_ac_response.png` | slide 34, unchanged |

**`03a_flow.png` is retired on purpose.** It shows the same five stages as the
new system block diagram but without the timings, the LLM/MCP block or the
proportional bar. Keeping both would put two versions of one diagram in the
same deck, and the weaker one first. Drop it; do not try to find it a home.

---

## Numbering note

The slide numbers above run past 34 because dividers are numbered too. Build
what the map describes; the exact total will land between 34 and 40 depending
on how you count dividers. Do not pad.

---

## Still not in scope

Unchanged from the original instructions. **Only what the project does and what
works.** No slide about approaches that were tried and dropped, no bugs, no
"limitations" or "future work", nothing framed as a shortfall. If `CONTEXT.md`
does not contain it, it does not go in the deck.
