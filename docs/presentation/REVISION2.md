# Revision 2: build out the circuit section

Second revision. **§2 is the only section that changes.** Everything else in
the deck stays exactly as it is.

`CONTEXT.md` has been updated with the material: new **§2.0 – §2.8**. Read
those before you start. Three new images are in `figures/`.

---

## Keep exactly as they are

Typography, colour, layout, the corner section labels, divider treatment,
speaker notes, and all the rules from the earlier instructions: ~15 words of
body text per slide, one idea per slide, a visual on every slide, every number
glossed, never invent a number.

---

## What §2 is for now

The old §2 showed the circuit. The new §2 makes an argument that ends in a
design procedure:

> 1. NRZ at 5 Gb/s means everything that matters happens below **2.5 GHz**.
> 2. The channel throws away **10.7 dB** by the time it gets there.
> 3. In the time domain that means **37 % of every bit lands in the next bit's
>    slot** — the eye closes.
> 4. Two different problems, two blocks: shaped loss → **CTLE**; a known
>    leftover of an already-decided bit → **DFE tap**.
> 5. Here is the circuit that does it.
> 6. The spec does not pick one such circuit — **many sizings satisfy it**.
> 7. So here is exactly how we pick the starting one.

Step 6 → 7 is the hinge. It is what makes the rest of the deck (the RL search)
necessary rather than decorative.

---

## §2 — 14 slides

Old §2 was 6 slides (the divider, the signal path, the whole schematic, the two
stages, the 11 knobs). All of those survive; nine slides are added around them.

**S1. Divider — "The circuit".** Unchanged.

**S2. NRZ, and why 2.5 GHz.**
Three short definitions, from `CONTEXT.md` §2.0. Show the arithmetic visually:
one bit = 200 ps; the fastest pattern `1010` takes two bits per cycle;
therefore Nyquist = half the data rate = **2.5 GHz**. A tiny waveform sketch of
`1 0 1 0` with the cycle bracketed across two bit slots does this better than
a sentence.
*Notes:* define NRZ in one line — two levels, one bit per symbol, no return to
rest. Then the point of Nyquist: above it there are no new symbols, only edge
detail. That single number is what the whole specification is built around.

**S3. The channel is a low-pass filter.**
The loss table from §2.1 (DC, 1 GHz, 2.5 GHz, 5 GHz). Then the number that
matters, large: **10.7 dB of loss between DC and Nyquist**, next to
**the brief asks for 3–12 dB of boost**.
*Notes:* why a copper trace attenuates — skin effect raises series resistance
with frequency, the dielectric absorbs more with frequency. Then land the
juxtaposition: the required peaking is not an arbitrary spec, it is the size of
this channel's hole.

**S4. One bit does not stay in its slot.**
`figures/06a_pulse_spreading.png`, full width. Nothing else.
*Notes:* this is our own channel model, one isolated bit. Transmitted, a clean
200 ps rectangle. Received, a smeared hump — and at the moment the receiver
samples the *next* bit, **37 %** of this one is still sitting there. Mention
that the channel delay has been removed from the plot so the two line up.

**S5. Intersymbol interference.**
`figures/06b_isi.png`. Keep the slide text to the term itself.
*Notes:* walk the figure — individual pulse responses spread, overlap, and add
at the receiver's sampling instants. With random data the leftovers add
constructively and destructively at random, which is what closes the eye.
Then set up the split: part of this is frequency-shaped loss, part is a known
amount of an already-decided bit. Two problems, two blocks.

**S6. The solution, at block level.**
**Diagram A** (spec below). No other content.
*Notes:* one sentence per block. The CTLE undoes the shaped loss; the DFE
subtracts the leftover of the bit we have already decided. Credit the reference
design here: `github.com/muhammadaldacher/SERDES-Design-of-RX-Decision-Feedback-Equalizer`.

**S7. The same thing, opened up.**
**Diagram B** (spec below).
*Notes:* the summing node is where the two paths meet — CTLE output through
`Gm1`, the scaled previous bit through `GmF`. The comparator decides, the flip
flop holds that decision for one clock, and that held bit is what gets scaled
and subtracted. The loop is one bit long, which is why it is a **1-tap** DFE.

**S8. The circuit that implements it.**
`figures/06c_circuit_implementation.png`, full width.
*Notes:* point out the correspondence with the previous slide — the same five
labelled parts, now as transistors. Note that the comparator and flip-flop are
ideal models: this project sizes the analog path.

**S9. The whole circuit.** `figures/02a_schematic_full.png`. Unchanged.

**S10. Stage 1 — the CTLE.** `figures/02b_ctle_stage.png`. Unchanged.

**S11. Stage 2 — the summing stage and the tap.**
`figures/02c_summing_stage.png`. Unchanged.

**S12. The 11 knobs.** Unchanged (CONTEXT §2.5).

**S13. The specification does not pick one circuit.**
From §2.7. Show it as a short list of trade-offs that all meet the same spec:
wide-and-slow vs narrow-and-fast; big `RL` with small `gm` or the reverse; more
boost with a smaller tap or less boost with a bigger one. One line at the
bottom: **"Many sizings pass. They are not equally good."**
*Notes:* a spec is a set of inequalities, and inequalities do not have unique
solutions. The sizings sit in different basins with different amounts of room
before a constraint breaks. So the starting point is not a convenience — it
decides which basin the search ever sees, and 11 dimensions is far too large
for the search to find a better basin by itself. That is what the next slide
is for.

**S14. How we choose the starting point.**
**Diagram C** (spec below). This is a dense, full-page slide and that is fine —
it is the payoff of the section.
*Notes:* say the order out loud, because it is the thing to take away: CTLE
first because it sets the shape, Summer second because it only needs the
common-mode condition, DFE last because **it cannot be calculated at all until
the other two exist** — the tap is measured off a pulse through the finished
path. Mention that this whole derivation runs per specification, with no hand
tuning, which is what "zero human intervention" requires.

---

## Drawing specs

Same restrained palette as the rest of the deck. Rounded rectangles, thin
connectors, one accent colour, no 3-D, no shadows.

### Diagram A — block-level solution (S6)

One row, left to right:

```
[ Input ] → [ Channel ] → [ CTLE ] → [ DFE ] → [ Data output ]
```

Label the Channel *"loses the fast edges"*, the CTLE *"boosts them back"*, the
DFE *"subtracts the leftover"*. Nothing else.

### Diagram B — the same, in detail (S7)

Open the CTLE and DFE boxes out:

```
                                      ┌──────── Summing Node ────────┐
 in ──►[ Channel ]──►[ CTLE ]──► Gm1 ─►( + )──►[ Clocked      ]──► D1 ──► data
                                         ▲     [ Comparator   ]        │
                                         │                             │
                                    GmF ─┘◄──── Q ◄──[ D Flipflop ]◄───┘
                                                          ▲
                                                       RX Clk
```

Requirements:

1. The summing node is a **circle with a +**, not a box.
2. `Gm1` and `GmF` are the two inputs to that circle — `Gm1` carrying the
   signal from the CTLE, `GmF` carrying the fed-back bit.
3. The feedback path runs **from the flip-flop output back to `GmF`**, and is
   the visually obvious loop on the slide. Label it *"the bit we just
   decided, scaled by the tap"*.
4. `RX Clk` feeds both the comparator and the flip-flop.
5. **Use exactly these labels** — `Gm1`, `GmF`, *Summing Node*, *Clocked
   Comparator*, *D Flipflop*, *RX Clk* — because the next slide shows the real
   schematic annotated with the same words, and the two slides should read as
   the same circuit.

### Diagram C — the design procedure (S14)

This is the one to get right. Full page.

**Header band, spanning the full width:**

> Starting assumptions —  **tail devices L = 500 nm**  ·  **everything else
> L = 200 nm**

**Three blocks on one horizontal line, directly beneath the header**, evenly
spaced so the page reads as three columns **without any column rules,
backgrounds or dividers being drawn**:

```
      CTLE                    Summer                    DFE
```

**Under each block, an arrow down into a vertical flowchart.** Use small
rounded boxes connected by short downward arrows. Keep each step to one line
where possible; the formulas may be a second line in monospace.

**CTLE column** — ten steps, from `CONTEXT.md` §2.8:

```
1  f_target = centre of the required peak band
2  put the output pole above it:  f_p2 = 2.13 × f_target
3  RL = 1 / (2π · f_p2 · Cout)
4  over-ask:  k = 10^(peaking_dB × 1.677 / 20)
5  gm = k / RL          ← pins DC gain at 0 dB
6  Rs = 2(k − 1) / gm
7  Cs = k / (2π · Rs · f_p1),   f_p1 = 0.8 × f_target
8  Vov = (Vcm − 0.1 − Vt) / 2   from the ICMR budget
9  Vt and Vov characterised in SPICE → Vgs ≈ Vt + Vov
   then gm/ID picks the branch current and the tail current
10 gm/ID gives the widths — diff pair and tail MOSFET
```

Mark step 5 with a small callout: **"this is what fixes DC gain at 0 dB"**.

**Summer column** — three steps:

```
1  Wg1 = 100 × W_min = 42 µm
   so the tap device still clears the 0.42 µm PDK floor at tap = 1 %
2  Ig1 = 1 mA
   free — nothing downstream is sensitive to it
3  Rsum = VDD / Ig1 = 1.8 kΩ
   half the current per leg → output common mode at VDD/2
```

**DFE column** — the measured one:

```
1  CTLE and Summer are already designed
2  feed a single pulse through that path
3  read the ISI one UI after the cursor
4  → that sets the tap coefficient
5  every device value follows from it:
       I_tap  = Ig1   × tap %
       W_pair = Wg1   × tap %
       W_tail = Wtail × I_tap / Itail
```

End the DFE column with a highlighted box:
**"three device values — one variable"**

Give that box the warm accent so the eye lands on it; it is the single most
important statement on the page.

*Speaker-note reminder for this slide:* the netlist uses **180 nm**, not 200.
200 nm on the slide is a round number for discussion, and the schematic image
on slide 9 reads `0.18` — so if anyone asks, the answer is "180, near-minimum
length". The 500 nm tail is exact.

---

## One change outside §2

§4's starting-point slides (old 16–17) **stay, but shrink to the result only** —
7 of 10 specifications solved at the first proposal, 0.2 s per specification.
Drop the mechanism from them; it now lives in §2 slide 14 and should not be
told twice at length. A single sentence in the notes referring back is enough.

Nothing else outside §2 changes.

---

## Figures

Three new files, all in `figures/`:

| file | slide |
|---|---|
| `06a_pulse_spreading.png` | S4 |
| `06b_isi.png` | S5 |
| `06c_circuit_implementation.png` | S8 |

The existing `02a`, `02b`, `02c` keep their slides. `03a_flow.png` remains
retired, as in the previous revision.

---

## Still not in scope

**Only what the project does and what works.** No slide about approaches that
were tried and dropped, no bugs, no "limitations" or "future work", nothing
framed as a shortfall. If `CONTEXT.md` does not contain it, it does not go in
the deck.
