# PCIe Gen2 Equalizer — RL-based Automated Analog Sizing

A Reinforcement-Learning framework that takes **target equalizer specs** and
automatically sizes a SKY130 equalizer, driving **ngspice** in the loop and
emitting the sized schematic + verified specs.

**Use case:** PCIe Gen2 (5 Gb/s, Nyquist 2.5 GHz) — 1-stage CTLE with source
degeneration (variable `Rs`, `Cs`) merged with a current-summing **1-tap DFE**.

> **Read [`HANDOVER.md`](HANDOVER.md) before changing anything** — it documents
> every measured number, 12 bugs found and fixed, and what is *not* built.
> [`VERIFY.md`](VERIFY.md) shows how to reproduce every claim yourself.

## Status

| | |
|---|---|
| CTLE + summer + 1-tap DFE, all-real SKY130 devices | ✅ built, netlist-verified two ways |
| Closed-loop DFE running in SPICE (XSPICE slicer + 1 UI latch) | ✅ |
| Full PVT: 60 corners × every metric incl. eye | ✅ |
| **RL loop end-to-end** | ❌ **never run** — this is the actual deliverable |
| Lossy channel | ❌ investigated, not built (not required by the brief) |
| Transistor-level slicer/latch | ❌ behavioural code models |
| LLM wrapper (bonus) | ❌ |

**Current design (`Itap`=50 µA), worst case over all 60 corners:**
```
f_peak  2.991 GHz vs 2.5   <- THE ONLY VIOLATION (18/60 corners)
eye H   170.8 mV  vs 100   ok      eye W  0.688 UI vs 0.4  ok
noise   0.493 mV  vs 1.5   ok      power  2.52 mW  vs 15   ok
area, bias, gain, hd3, peaking     ok at 60/60
```
9 of 10 specs pass everywhere. `f_peak` is the single binding constraint — one
dimension for the optimiser to close.

⚠️ The `CtleParams` **default** `Itap`=300 µA fails the eye at **60/60** corners.
Runs starting from defaults start from a known-bad point.

## Why CEM (not deep RL)

The brief stresses *fewest search points / lowest design time*. A step-wise
deep-RL policy (PPO/TD3) needs thousands of SPICE calls. The default agent is
the **Cross-Entropy Method** — gradient-free policy search reaching near-optimal
sizings in tens of simulations. A standard `gymnasium.Env`
(`eqrl/env.py::CtleEnv`) is provided so PPO/TD3 can be dropped in for comparison.

## Layout

```
eqrl/
  pdk.py        SKY130 discovery + PVT corner grids (quick/spec/full)
  netlist.py    parametric netlist, device<->geometry maps, DFE decision path
  spice.py      ngspice driver: ac / op / tap_weight / hd3 / noise / eye
  dfe.py        PRBS, eye extraction, peak-distortion analysis
  specs.py      target specs + smooth spec-satisfaction reward
  env.py        Objective (for CEM) + Gymnasium CtleEnv (for deep RL)
  agents/cem.py Cross-Entropy-Method policy search
  design.py     orchestrator: specs in -> sized .sp + report.json + plots
netlists/
  eq_tb.sch     full equalizer, DFE loop OPEN   (op/ac/noise)
  eq_dfe_tb.sch full equalizer, DFE loop CLOSED (eye/transient)
  ctle_tb.sch   older CTLE-only schematic (superseded)
scripts/
  pvt_sweep.py  parallel full-PVT sweep of every spec, with timing
  run_design.py CLI
  smoke_test.py SPICE-layer sanity checks
  plot_wave.py  plot any wrdata dump to PNG
  plot_eye.py   render the closed-loop eye diagram
```

## Design variables (9)

`W, L` (input pair) · `RL` · `Rs, Cs` (degeneration — **Rs sets the boost, Cs
sets the zero**) · `Itail`, `Wtail` (tail mirror) · `Itap`, `Wfb` (DFE tap).
Bounds in `netlist.py::PARAM_BOUNDS`.

## Specs scored

Peaking 3–12 dB · peak in 1.25–2.5 GHz · HD3 < −30 dB · input noise < 1.5 mVrms ·
power < 15 mW · area < 0.05 mm² · eye > 0.4 UI and > 100 mV · bias validity
(input pair **and** real tail device in saturation).

## Version control

Git initialised; identity set **repo-local** (`kanishk` /
`f20230748@pilani.bits-pilani.ac.in`) so nothing global was touched. SPICE dumps
and plots are gitignored; the 60-corner PVT results in `results/` are tracked as
evidence. No remote configured. See `HANDOVER.md` §12b.

## Setup

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b
```
ngspice-42 (with XSPICE + KLU), xschem 3.4.4, Python 3.12 with numpy / scipy /
matplotlib / gymnasium. SKY130 via volare. Full details in `HANDOVER.md` §2.

## Run

```bash
python3 scripts/smoke_test.py                                    # SPICE layer sanity
python3 scripts/pvt_sweep.py --grid spec --eye --set Itap=50e-6  # 20-corner PVT
python3 scripts/plot_eye.py --itap 50e-6 -o /tmp/eye.png         # eye diagram
xschem netlists/eq_dfe_tb.sch                                    # press f, then Shift+S
```

## Three things that will bite you

1. **`.spiceinit` is required for correctness, not speed.** Copy
   `$PDK_ROOT/sky130A/libs.tech/ngspice/spinit` into any directory you simulate
   from, or xschem-generated netlists fail with `Formula() error`.
2. **99.9 % of runtime is PDK model parsing**, not solving — a full 12-device AC
   analysis costs the same as parsing the library with a single resistor
   (7.60 s vs 7.61 s). **No speed optimisation is switched on**; ~147× is
   available. See `HANDOVER.md` §9.
3. **"5 corners" is a miscount.** The brief's `TT,SS,FF,SF,FS; VDD±5%; 0–125 °C`
   is 5 process × V × T = **20** corners, and sky130 keeps R/C on a *separate*
   axis the named corners do not touch. Testing only the listed corners
   understates the worst case by 19 %.
