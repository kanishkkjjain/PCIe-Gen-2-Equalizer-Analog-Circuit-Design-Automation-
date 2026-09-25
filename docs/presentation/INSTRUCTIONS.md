# Instructions: build the presentation

You are building a slide deck for a hackathon project presentation. Everything
you need is in **`CONTEXT.md`** (facts and numbers) and **`figures/`** (ten
images). Read `CONTEXT.md` in full before you start.

---

## Before anything else

Call the `Artifact` tool with `action: "quickstart"` and `intent: "slides"`,
and build the deck from the slide type it names. Do not hand-write an HTML
page instead.

---

## What this deck is for

A presenter will stand and talk through it. **The slides are a backdrop, not a
script.** Their job is to give the audience something to look at while the
presenter explains — a diagram, a number, a chart. The explanation lives in the
speaker notes and in the presenter's mouth, not on the slide.

If a slide can be read aloud word for word, it has too many words.

---

## Hard rules

1. **At most ~15 words of body text per slide**, outside of tables and figure
   captions. Headline plus a few short lines. Never a paragraph. Never a
   sentence that runs more than one line.
2. **One idea per slide.** If you are tempted to add "and also…", that is the
   next slide.
3. **Every slide gets a visual** where one exists — a figure from `figures/`, a
   block diagram you draw, a table, or one big number. A slide of bullet points
   with no visual should be rare and deliberate.
4. **Gloss every number whose meaning is not obvious.** Not "0.516 UI" alone,
   but "0.516 UI — the eye stays open for over half the bit period". Section 7
   of `CONTEXT.md` has plain-language glosses for the jargon; use them.
5. **No walls of text in tables either.** At most 10 rows and 3 columns, and
   every cell a few words at most. If the data is bigger, split it across two
   slides or show only the rows that matter.
6. **Use every one of the ten figures.** They are listed with their slide
   assignments in §6 of `CONTEXT.md`.
7. **Never invent a number.** If it is not in `CONTEXT.md`, it does not go on a
   slide. Do not round a figure into a rounder one.

---

## Speaker notes

**Every slide gets notes, 60–120 words.** Write them as what the presenter
would actually say out loud — full sentences, conversational, first person
plural ("what we did here was…"). They carry the explanation the slide
deliberately leaves off.

Good notes do three things: say what the audience is looking at, say why it
matters, and hand off to the next slide. Include the plain-language gloss of
any jargon the slide uses for the first time.

---

## Tone

Plain language throughout, including on the slides. This is a technical
audience, but simple words still land better than complicated ones. Prefer
"how often it works" to "success rate characterisation". Prefer "the model
guesses, the simulator decides" to "surrogate-mediated proposal with
verification-gated acceptance".

Confident and factual. The numbers are good; let them speak without adjectives.
Do not write "impressive", "excellent", "state of the art".

---

## What is NOT in scope

The user was explicit about this. **Include only what the project does and what
works.**

Do not include, do not allude to, do not add a slide for:

- approaches that were tried and abandoned
- bugs found and fixed, or any debugging history
- a "limitations", "challenges faced", "what didn't work" or "future work"
  section
- anything framed as a shortfall

If `CONTEXT.md` does not contain it, it does not belong in the deck. Do not go
looking for balance by inventing a caveats slide.

---

## Structure — 35 slides

| § | section | slides | of which |
|---|---|---|---|
| — | Title | 1 | |
| 1 | The problem | 3 | |
| 2 | The circuit | 6 | 1 divider |
| 3 | System overview | 4 | 1 divider |
| 4 | Component deep dive | 13 | 1 divider |
| 5 | Results | 8 | 1 divider |
| | **total** | **35** | **31 content + 4 dividers** |

A divider slide carries just the section number and name, nothing else. They
are slides 5, 11, 15 and 28 in the map below.

---

## Slide-by-slide map

Columns: **what goes on the slide** / **what the notes must cover**.

### Title

**1. Title.**
Project title, the problem-statement name, team, college, both member names
(§0 of `CONTEXT.md`). No other content.
*Notes:* who you are, one sentence on what the project does.

### §1 — The problem

**2. The brief.**
Three or four short lines pulled from the verbatim statement: automate analog
sizing with RL; use case PCIe equalizer; must beat sweeping the parameter
space; zero human intervention. Not the whole quote — it is too long for a
slide.
*Notes:* read the fuller framing, and say the full statement is in the report.

**3. What "meets spec" means.**
The ten-condition table from §1.2. Mark the two internal checks visually.
*Notes:* walk the eight brief specs quickly, then spend the time on why checks
9 and 10 exist — a circuit can hit every number while sitting in triode, or
open the eye with plain gain without equalising anything.

**4. And it must hold 20 times over.**
A visual of the corner grid: 5 processes × 2 supplies × 2 temperatures = 20.
Draw it as a small grid or as three stacked rows, not as a bulleted list. One
line underneath: *"every condition, at every corner."*
*Notes:* what a PVT corner is in plain terms; why passing at typical is not
passing; that this is what makes each evaluation expensive.

### §2 — The circuit

**5. Divider — "The circuit".**

**6. The signal path.**
**Block diagram A** (spec below). No other content.
*Notes:* the channel eats the fast edges, bits smear into each other, the eye
closes. The CTLE boosts back what was lost; the DFE subtracts the leftover tail
of the bit just decided. Credit the reference design here.

**7. The whole circuit.**
`figures/02a_schematic_full.png`, full width, edge to edge. Caption only.
*Notes:* say this is the real schematic, it opens in xschem, and a test checks
it matches the netlist that actually gets simulated — the picture cannot drift
from the circuit.

**8. Stage 1 — the CTLE.**
`figures/02b_ctle_stage.png`. Three short labels: *Rs ‖ Cs → where the boost
starts*, *RL · Cout → where it rolls off*, *the gap → how much boost*.
*Notes:* the mechanism from §2.2 — at low frequency Cs is open so Rs
degenerates the pair and gain is low; at high frequency Cs shorts Rs out, the
degeneration vanishes, gain rises. That rise is the boost.

**9. Stage 2 — the summing stage and the tap.**
`figures/02c_summing_stage.png`. Two labels: *Gm1 — the signal*, *GmF — a
scaled copy of the last bit, subtracted*.
*Notes:* how one tap works; that `tap_pct` scales the feedback device width and
its current together, so tap strength is one knob rather than two.

**10. The 11 knobs.**
The table from §2.3, trimmed to knob + what it sets (drop the ranges column, or
show ranges only for the four most interesting). Headline: *"this is the entire
output."*
*Notes:* emphasise these are continuous, not a menu; say the ranges are in the
report; note that everything else about the circuit is fixed.

### §3 — System overview

**11. Divider — "How the system works".**

**12. The flow.**
`figures/03a_flow.png`, full width.
*Notes:* walk the five boxes left to right, with the timing: proposing costs no
simulation at all, screening is 16 evaluations, verifying is 20.

**13. The rule.**
One line, very large: **"The model only ever proposes. SPICE decides."**
Nothing else on the slide.
*Notes:* this is the slide to slow down on. `all_pass` is never set from the
model's opinion — it becomes true only after 20 real corner simulations all
passed. This is the difference between a plausible answer and a trustworthy
one.

**14. The parts, and what each is for.**
**Block diagram B** (spec below) — the three bands: derived / learned /
measured.
*Notes:* one sentence per component from the §3.3 table. Point out that trust
increases left to right, and the verdict lives at the far right.

### §4 — Deep dive

**15. Divider — "Inside each part".**
Add **block diagram C** (spec below) on this divider: built once vs. run per
request.
*Notes:* the split matters — training is hours and happens once; a request is
about 40 seconds.

**16. Starting from physics, not from random.**
The 7-step derivation from §4.1, as 7 very short lines (3–5 words each). One
number, large: **0.2 s per spec**.
*Notes:* each step in one sentence; stress that this is derived per
specification, which is what "zero human intervention" requires — nothing is
hand-seeded.

**17. Does the starting point work?**
One big number: **7 of 10 specifications solved by the very first proposal.**
*Notes:* what "first proposal" means and why it matters — every proposal you
skip is 20 SPICE runs you do not pay for.

**18. The dataset.**
Three numbers: **1,500 designs**, **5,993 measurements**, **14,753 SPICE runs
logged**. Plus the three sampling clouds as three short labels.
*Notes:* why sample in three places — spread teaches the model where circuits
fail, clusters around good designs and around the starting points make it
accurate where the search actually walks.

**19. The surrogate: physics × a bounded correction.**
`figures/04a_physics_anchor.png`, full width.
*Notes:* this is the second slide to slow down on. The network never predicts
the circuit from scratch — it predicts a multiplier on the hand calculation,
and that multiplier is capped at ×7.4. Before training it is exactly 1.0, so
the model starts as the hand calculation. It cannot drift away from physics
because it is not free to.

**20. Does the model agree with the simulator?**
`figures/04b_surrogate_agreement.png`.
*Notes:* read the bar chart plainly — gain and current are nearly exact, the
eye is the softest. Then say that is exactly why the eye is measured rather
than predicted, which is the next component.

**21. Can the model fool itself?**
Two numbers side by side: model's prediction **0.99864**, SPICE measured
**0.99769**. Caption: *gap 0.00095*.
*Notes:* explain the trap — a model you optimise against will find its own
blind spots and report a fantasy. So we let a search hunt for the model's
single favourite design and simulated that design for real. It was genuinely
good.

**22. The RL agent.**
Four short lines: goal-conditioned SAC; spec is an input, so one agent serves
any spec; 11 numbers out at once; 20 small steps per episode.
*Notes:* it is a continuous control policy, like driving every joint of a robot
at once — not a classifier picking an answer. And the reward is the *worst* of
the ten margins, never the average, because a design is only as good as its
weakest condition at its worst corner.

**23. Why the agent is deliberately random.**
A small diagram: one starting design → 8 arrows fanning out → 8 candidates.
*Notes:* the policy samples from a distribution, so 8 runs from the same start
give 8 different designs. A deterministic agent would hand back 8 identical
copies and asking for 8 would buy nothing. Seeds are fixed, so it is
reproducible.

**24. How well the agent does.**
Two numbers: **56.2 %** of proposals survive 20-corner verification; **0.26 s**
to produce all 8. One line of gloss under each.
*Notes:* distinguish the two rates clearly — one roll of the dice works about
half the time; eight rolls and keep the best is what gets to 8 of 10.

**25. When the request is outside what it learned.**
Short comparison table: agent 56.2 % / 0.26 s / in-range only; search 32.5 % /
~20 s / anywhere. Bottom line: *both together → 9 of 10.*
*Notes:* why a search has no trained range; that the framework checks each
request field by field and says which method answered; and that the two succeed
on different specifications, which is why running both reaches 9 of 10 where
either alone reaches 8.

**26. Measuring the eye instead of predicting it.**
The before/after table from §4.6: **540 evaluations / ~10 min** → **56 / ~1.5
min**. Plus one line: *two corners recover the true worst case 89.6 % of the
time.*
*Notes:* the eye has a comparator in it and a comparator has no closed form, so
it is measured. Which two corners is a measured result over 280 designs, not a
guess. And the screen is a filter, not a verdict — survivors still face all 20.

**27. Talking to it in plain English.**
The three-tool table from §4.8.
*Notes:* this is the brief's bonus deliverable. Stress that honesty is enforced
in code, not requested in a prompt: every answer names the method, says whether
the request was in range, and a design that misses is returned with what it
missed and never called a pass.

### §5 — Results

**28. Divider — "Results".**

**29. How often it meets a specification.**
`figures/05a_coverage.png`.
*Notes:* 10 unseen specifications, every candidate verified at all 20 corners.
Read the curve: the agent reaches its coverage in far fewer candidates than the
search needs.

**30. How long it takes.**
`figures/05b_time.png`. Beside it, one line: *median 36.9 s.*
*Notes:* what is in those seconds — proposing is free, the screen is 16
simulations, verification is 20.

**31. Beating the sweep.**
The §5.3 table: random search 0.726 / **0 of 95 passing** vs. the framework
0.987 / a passing design. Under it, one line: *a 10-point sweep of 11 knobs is
10¹¹ designs.*
*Notes:* this is the brief's own success criterion, so say so explicitly. Same
budget, same simulator, same corners, same scorer — the only difference is how
candidates are chosen.

**32. A verified design.**
Left: `figures/05c_eye.png`. Right: `figures/05d_ac_response.png`.
Caption: *every condition passing, all 20 corners.*
*Notes:* read the eye — 237 mV against 100 needed, 0.516 UI against 0.4, and
gloss what 0.516 UI means. Then the AC curve: 8.19 dB of boost peaking at
1.89 GHz, inside the shaded required window and just below Nyquist, which is
exactly where the channel's loss bites hardest. Numbers are in §5.5 of
`CONTEXT.md` and are printed on the charts themselves.

**33. Every margin, worst case.**
The margin table from §5.4, trimmed to the 8 brief conditions. Highlight eye
width as the tightest at +13.3 %.
*Notes:* these are worst-case across all 20 corners, not typical. Nothing is
rounded toward passing. The tightest is eye width, and it still clears by 13 %.

**34. Against the brief.**
The checklist table from **§5.7** of `CONTEXT.md`.
*Notes:* go down it. Land on the last two: faster than sweeping, and the bonus
LLM wrapper delivered. Close by mentioning the 327 automated tests (§5.6) —
they check the claims, not just the code: that the schematic matches the
netlist that gets simulated, and that every documented number matches the
artifact it came from.

**35. Closing.**
One line: **"Specs in. Verified schematic out. Under a minute."** Team name
underneath.
*Notes:* thank the audience, invite questions.

> That is 35 slides: 31 content slides plus the 4 dividers, which are numbered
> in the map like any other. Keep all 35.

---

## Block diagrams to draw

Three diagrams have no image. Draw them natively in the deck (shapes and text,
not a generated image) so they stay crisp and editable.

Keep all three in one restrained palette — one accent colour, one warm colour
for the "measured" or "decides" element, grey for everything else. Rounded
rectangles, thin connectors, generous whitespace. No 3-D, no shadows, no
clip-art.

### Diagram A — the signal path (slide 6)

A single left-to-right chain:

```
[TX]→[ lossy channel ]→[ CTLE ]→( + )→[ slicer ]→ data out
                                   ↑                  │
                                   └──[ 1-tap DFE ]←──┘
```

- The summing node is a circle with a **+** in it, not a box.
- The feedback arrow runs from the slicer output, left and back up into the
  summing node. Label it **"the bit just decided, scaled"**.
- Put a small **eye glyph** — two arcs forming a closed lens — immediately
  after the channel, labelled *closed*, and an **open** eye glyph after the
  slicer, labelled *open*. These two glyphs carry the whole story of the slide.
- Label the channel *"loses the fast edges"*.

### Diagram B — the system map (slide 14)

Three horizontal bands, stacked, with a single arrow running down the left edge
labelled **"trust increases"**:

| band | contains | annotation |
|---|---|---|
| **Derived** | physics starting point | "no simulation, 0.2 s" |
| **Learned** | surrogate network → RL agent → fallback search | "fast, approximate, proposes only" |
| **Measured** | 2-corner eye screen → 20-corner verify | "real SPICE, decides" |

- Colour the **Measured** band in the warm accent; the other two in grey and
  the cool accent.
- One caption under the whole thing: **"Only the bottom band is allowed to say
  a design passes."**

### Diagram C — built once vs. run per request (slide 15)

Two rows, one above the other.

**Top row, labelled "Built once — hours":**
```
collect 5,993 measurements → train the surrogate → train the agent
```

**Bottom row, labelled "Per request — ~40 seconds":**
```
derive a start → propose 8 → screen the eye → verify 20 corners
```

Join them with two downward arrows from the top row into the bottom, labelled
**"saved model"** and **"saved agent"**, landing on the "propose 8" box. Make
the visual weight of the bottom row heavier — that is the part that runs when
someone asks for a circuit.

---

## Final check before you finish

- Every one of the ten files in `figures/` appears on some slide.
- No slide has a paragraph on it.
- Every slide has speaker notes of 60–120 words.
- No slide mentions anything that did not work.
- Every number on a slide appears in `CONTEXT.md`.
