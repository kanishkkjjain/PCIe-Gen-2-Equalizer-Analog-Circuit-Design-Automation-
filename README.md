# PCIe Gen2 Equalizer — RL-based Automated Analog Sizing

A Reinforcement-Learning framework that takes **target equalizer specs** and
automatically sizes a SKY130 equalizer, driving **ngspice** in the loop and
emitting the sized netlist + verified specs.

**Use case:** PCIe Gen2 (5 Gb/s, Nyquist 2.5 GHz) — a 1-stage CTLE with source
degeneration feeding a separate **DFE summing stage**, across a lossy FR4-like
channel.

> **Read [`HANDOVER.md`](HANDOVER.md) before changing anything.** §0 quotes the
> problem statement verbatim, §10 documents 21 bugs found and fixed (plus a
> list of this project's own claims that measurement disproved), and §11/§12
> say exactly what is *not* built.
> [`VERIFY.md`](VERIFY.md) shows how to reproduce every claim yourself.

## Result

**All 8 brief specs pass at all 20 of the brief's PVT corners**, with the
channel present and the DFE active. (The scorecard shows 9 — the ninth is an
internal "are the devices in saturation" check, not a customer requirement.)
Found in **189 candidates**:

```
peaking 7.33 dB  vs 3-12       f_peak  1.887 GHz vs 1.25-2.5   eye H 126.8 mV vs 100
hd3    -59.9 dBc  vs <-30      noise   0.655 mV  vs <1.5       eye W 0.484 UI vs 0.4
power    3.41 mW  vs <15       area    0.00033 mm2 vs <0.05
```

**Tightest margin: `f_peak`, +13.2% of its band** — quoted from the
binding edge, not the comfortable one.

**It genuinely equalises**, not merely amplifies: **1.93×
eye-openness improvement** over the equaliser input, collapsing to 0.15 with the
degeneration removed. Eye height alone cannot show this, because summing-stage
gain is a free variable (it correlates with eye height at 0.98, peaking at 0.47).

Eye measured at **200 mV pp** transmitter drive — conservative
against a real Gen2 transmitter's ~800 mV. Converged at iteration
4 of 15; 1552 ngspice launches. Full write-up in
`HANDOVER.md` §16.

## Status

| | |
|---|---|
| Two-stage equalizer (CTLE → Gm1+GmF summing stage), all-real SKY130 devices | ✅ |
| Lossy FR4-like channel, −16.8 dB at Nyquist, 10.6 dB of tilt | ✅ |
| Closed-loop DFE in SPICE (ideal comparator + 1 UI latch) | ✅ |
| **RL loop end-to-end, 9/9 specs at 20/20 corners** | ✅ |
| Automatic analytic starting point (no hand-seeding) | ✅ |
| Test suite (145: 111 fast + 34 against ngspice) | ✅ all passing |
| **Schematic output** (we emit netlists) | ❌ |
| **Sweep-vs-RL baseline** — the brief asks for it | ❌ |
| Runtime "tunable" mechanism · Anaconda env · LLM wrapper | ❌ |

## Why CEM (not deep RL)

The brief stresses *fewest search points / lowest design time*. A step-wise deep
RL policy (PPO/TD3) needs thousands of SPICE calls; every environment step here
*is* a SPICE simulation. The default agent is the **Cross-Entropy Method** —
gradient-free policy search that closed this problem in 189 candidates. A
standard `gymnasium.Env` (`eqrl/env.py::CtleEnv`) is provided so PPO/TD3 can be
dropped in for comparison.

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
tests/          145 total: 111 pure (no PDK, ~5 s) + 34 against real ngspice
scripts/        run_design, build_trimlib, pvt_sweep, smoke_test, plotting,
                baseline (random search at equal budget -- written, unrun)
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
python3 -m pytest tests/ -q                        # 145 tests, ~2 min
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
