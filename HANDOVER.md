# PCIe Gen2 Equalizer — RL Sizing Framework · Handover

**Project root:** `~/pcie_eq_rl` (WSL native ext4 — *not* `/mnt/c`, see §2)
**Last updated:** 2026-09-13
**Status:** Two-stage equalizer (CTLE → DFE summing stage) with a lossy channel,
sized end-to-end by the RL loop. **All 8 brief specs pass at all 20 of the
brief's PVT corners** in 95 candidates (§16) — the scorecard shows 10, the other
two being internal checks (devices in saturation; does it actually equalise)
rather than customer requirements. LLM wrapper and schematic export **not
built** (§0b).

---

## 0. The problem statement, verbatim

Recorded here because it is the contract, and because a great deal of effort was
wasted in one session arguing from memory about what it required. Quote it, do
not paraphrase it.

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
> schematic and resulting specs
> Bonus: LLM-based human interaction with wrapper to fine-tune
>
> **Tools & Resources**
>
> Python, Anaconda
> LLM, Gym Utils
> Spice Simulators (NGSpice or PySpice)
> Open-Source PDK (IHP 130nm BiCMOS or Skywater SKY 130nm)

### Every scored spec traces to that text

`eqrl/specs.py::EqualizerSpec` was checked against it line by line:

| brief | `specs.py` |
|---|---|
| peaking 3–12 dB | `peaking_min_db=3.0`, `peaking_max_db=12.0` |
| tunable 1.25–2.5 GHz | `fpeak_lo=1.25e9`, `fpeak_hi=2.5e9` — see the caveat below |
| HD3 < −30 dB @ 100 MHz | `hd3_max_db=-30.0`, measured at 100 MHz |
| noise < 1.5 mVrms, 10 MHz–5 GHz | `vn_in_max_rms=1.5e-3`, integrated over exactly that band |
| power < 15 mW | `power_max_w=15e-3` |
| area < 0.05 mm² | `area_max_m2=0.05e-6` |
| eye > 0.4 UI (H) | `eye_width_min_ui=0.4` |
| eye > 100 mV (V) | `eye_height_min_v=0.100` |
| PVT TT/SS/FF/SF/FS × VDD±5% × 0–125 °C | `pdk.pvt_grid("spec")` = **20 corners** |

**`gain_2g5_min_db` is the one invented spec.** It is not in the brief. It is
kept as a *reported* metric but its weight is `w_gain = 0`, so it no longer
vetoes designs. It previously did, silently.

**Two readings of the brief that are NOT satisfied**, recorded so they are not
mistaken for done:
- *"tunable 1.25–2.5 GHz"* and *"variable Rs, Cs"* imply a **runtime** tuning
  mechanism — a switchable cap bank and a control word. What is scored is that
  one fixed design's peak lands inside the band. There is no tuning mechanism
  and no test for one.
- The HD3 test amplitude (100 mV differential) is an assumption; the brief
  states the frequency but not the amplitude.

---

## 0b. Deliverables vs the brief — honest scorecard

| asked for | state |
|---|---|
| RL framework in Python | ✅ CEM (`agents/cem.py`) + `gymnasium.Env` (`env.py`) |
| takes target specs as input | ✅ `EqualizerSpec`, CLI flags |
| integrates with a SPICE simulator | ✅ ngspice, 22.4× faster than the naive path (§9) |
| outputs the resulting specs | ✅ `results/report.json`, per corner |
| **outputs the final schematic** | ⚠️ **netlists, not a schematic.** `results/ctle_final*.sp` run and reproduce the report; `netlists/*.sch` are hand-drawn and now describe the OLD topology |
| SKY130 PDK | ✅ |
| Gym Utils | ⚠️ `CtleEnv` exists and passes `check_env`, but no deep-RL agent has been run through it |
| **fewer search points than a sweep** | ✅ **measured** (§11). 95 candidates to a passing design; random search at the same budget reached 0.726 with **0 of 95** passing. A 5-point grid over 11 variables is 5¹¹ = 48.8 M designs = **6.8 years** at the measured per-candidate cost. |
| zero human intervention | ✅ the starting point is derived automatically (`analytic.py`), not hand-seeded |
| Anaconda | ❌ `requirements.txt` only, no `environment.yml` |
| LLM wrapper (bonus) | ❌ |

---

## 1. TL;DR — what exists and what it does

A Python framework that takes target equalizer specs, generates a **SKY130**
netlist itself, drives **ngspice**, measures the result, scores it, and searches
the device sizes with a Cross-Entropy-Method agent.

The analog design is **two stages**, matching the reference architecture (§4):

1. a **1-stage source-degenerated CTLE** (`Rs‖Cs` between the source nodes), and
2. a separate **DFE summing stage** — two transconductors `Gm1` (main path) and
   `GmF` (feedback tap) draining into one shared pair of `Rsum` loads. That
   shared pair *is* the summer.

A lossy **FR4-like channel** sits in front of it (−16.8 dB at Nyquist,
10.6 dB of equalizable tilt). Without
one the specification set is incoherent: a 1-tap DFE with no post-cursor to
cancel can only make the eye worse, which was measured (§10b).

Everything in the signal path is a real PDK device. The slicer and the 1 UI
latch are deliberately **ideal** XSPICE code models — as they are in the
reference design, which labels them "(Ideal)" too.

```bash
cd ~/pcie_eq_rl
python3 -m pytest tests/ -q                              # 282 tests
python3 scripts/smoke_test.py                            # SPICE layer sanity
python3 scripts/build_trimlib.py --grid spec             # 5.7x less model text
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel
```

---

## 2. Environment

| Component | Version / location |
|---|---|
| OS | WSL2 (Ubuntu) on Windows, kernel 6.6.87.2 |
| Python | 3.12.3, `/usr/bin/python3` |
| Python deps | numpy 2.5.2, scipy 1.18.1, matplotlib 3.11.1, gymnasium 1.3.0 — installed to user site with `pip install --user --break-system-packages` (PEP-668 override) |
| **ngspice** | **42**, `/usr/bin/ngspice`, compiled with KLU + **XSPICE** |
| **xschem** | **3.4.4**, `/usr/bin/xschem` |
| **SKY130 PDK** | volare 0.20.6 → `~/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b/` |
| PDK model lib | `<PDK>/sky130A/libs.tech/ngspice/sky130.lib.spice` |
| Cores | 16 |
| GUI | WSLg active (`DISPLAY=:0`), so ngspice `plot` and xschem open real windows |

**Required env var** (put in `~/.bashrc`):
```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b
```

**Why the project is on `~` and not `/mnt/c`** — measured: ngspice runs
**7.28 s** on native ext4 vs **9.93 s** on the Windows mount (~36% slower), and
200 small-file writes take 0.00 s vs 0.28 s. Reach it from Windows via
`\\wsl$\Ubuntu\home\kanishk\pcie_eq_rl` or VS Code Remote-WSL.

**Anaconda is NOT installed.** The hackathon brief recommends it; the only conda
on the machine is a **Windows** miniconda at `/mnt/c/Users/1998k/miniconda3`
(PE32+ binaries) which cannot drive Linux ngspice. Installing Linux Miniconda +
shipping an `environment.yml` remains an open conformance item.

---

## 3. File hierarchy

```
~/pcie_eq_rl/
├── HANDOVER.md              this file
├── README.md                short project intro
├── VERIFY.md                how to reproduce every claim yourself
├── requirements.txt         pinned Python deps
├── pytest.ini
├── eqrl/                    the framework package
│   ├── pdk.py               SKY130 discovery + PVT corner model
│   ├── netlist.py           parametric netlist: CTLE, DFE summer, channel,
│   │                        decision path, value<->geometry maps
│   ├── spice.py             ngspice driver, combined deck, fatal-error scan,
│   │                        trimmed-lib selection, memory-aware job capping
│   ├── dfe.py               PRBS, NRZ PWL, eye extraction
│   ├── specs.py             target specs + reward (soft scores + hard-pass bonus)
│   ├── analytic.py          closed-form starting point, calibrated vs SPICE
│   ├── trimlib.py           trimmed per-corner model libraries + validation
│   ├── env.py               Objective (parallel, cached) + Gymnasium CtleEnv
│   ├── design.py            orchestrator: specs in -> sized decks + report
│   └── agents/cem.py        Cross-Entropy-Method policy search
├── tests/
│   ├── conftest.py          the `needs_pdk` marker
│   ├── test_pure.py         124 tests, no PDK needed, ~5 s
│   └── test_spice.py        37 tests against real ngspice
├── scripts/
│   ├── run_design.py        CLI entry point
│   ├── build_trimlib.py     build + VALIDATE trimmed model libraries
│   ├── baseline.py          random search at an equal budget (§11, run)
│   ├── seed_benchmark.py    blind vs analytic seed vs after-CEM (§5)
│   ├── pvt_sweep.py         parallel PVT sweep of every spec, with timing
│   ├── smoke_test.py        SPICE-layer sanity checks (asserts)
│   ├── plot_eye.py          render the closed-loop eye diagram
│   └── plot_wave.py         plot any wrdata dump to PNG
├── netlists/                xschem schematics -- CROSS-CHECK ONLY, never
│   │                        simulated by the framework, and currently STALE:
│   │                        they show the old merged topology (§11)
│   ├── eq_tb.sch            DFE loop open (op/ac/noise)
│   ├── eq_dfe_tb.sch        DFE loop closed (eye/transient)
│   └── ctle_tb.sch          older CTLE-only version
├── configs/                 (empty, unused -- nothing reads it)
└── results/                 report.json + the sized decks (§12b)
```

Scratch dirs used at runtime (not in the repo): `/tmp/eqrl_*`. Validated
trimmed libraries live outside the repo in `~/.cache/eqrl/trimlib`.

---

## 4. Circuit architecture

**TWO stages.** The DFE is not merged into the CTLE's loads -- it is its own
summing stage. This matches the reference architecture (§17) and matters: in the
merged version the tap loaded the CTLE's output node and the tap weight was
welded to `RL`.

```
   channel            STAGE 1: CTLE                STAGE 2: DFE summing stage
  (FR4-like)     source-degenerated pair        Gm1 + GmF share ONE load pair

 chsrc_p --[Rsrc]--+                VDD                       VDD
   ladder,         |                 |                         |
   40 sections,    |               RLp/RLn                  XRsp/XRsn (Rsum)
   skin-effect R   |                 |                         |
                 vinp/vinn --> X1/X2 +--> outp_ctle --+     outp_dfe/outn_dfe
 [Rterm 100 ohm]                 |     outn_ctle      |         |     |
                                 sp--[Rs||Cs]--sn     +--> XG1p/XG1n  |
                                 |            |       (main signal)   |
                               Xt1          Xt2                       |
                            (4:1 mirror off vbias)   XGFp/XGFn -------+
                                                     (feedback tap)
                                                        |
                          vfbp/vfbn <-- dac <-- d_dff <-- d_dff <-- adc_bridge
                                        (1 UI hold)  (clocked comparator)
```

- **The summer is `XRsp`/`XRsn`.** `Gm1` and `GmF` drain into the SAME two
  resistors, so their currents add there by Ohm's law -- no extra devices, no
  extra power, no extra delay. There is no separate summing amplifier and there
  should not be one.
- **Tap weight is the ratio `gmF/gm1`**, set independently of the CTLE gain.
  That is the whole reason for splitting the stages.
- **`Gm1` design rule** (from the reference): flat response well past Nyquist,
  gain need not be high, and the output common mode is set by
  `0.5 * Ig1 * Rsum`. Prioritise a high dominant pole over gain.
  ⚠️ This is an **initialisation heuristic, not a constraint**. `analytic.py`
  starts the search from it, but `Rsum` and `Ig1` are both free variables and
  the optimiser moves away: the previous final design sat at **1.442 V of
  1.8 V**, not the 0.900 V the rule implies. Nothing re-enforces it, and the
  resulting headroom is asymmetric. `op()` now measures `out_cm_v` and
  `report.json` records it, so the drift is visible rather than assumed away.
- **Decision path**: `adc_bridge` -> `d_dff` (clocked comparator) -> `d_dff`
  (1 UI hold) -> `dac_bridge`. Two flops on the same edge give exactly one UI of
  feedback delay: the second captures what the first held before the edge.
- **Channel**: lumped ladder, 40 sections per leg, with a frequency-dependent
  series resistance (two staggered `R||L` stages approximating skin effect). A
  plain RLGC ladder does NOT work -- correctly sized it is a flat attenuator
  with 2.2 dB of tilt, and flat loss creates no ISI (§10b).
- **Feedback logic levels are 0.8/1.4 V, not 0/1.8 V.** A rail-to-rail swing has
  a 0.9 V common mode, which would push the tap tail into triode. 0.8/1.4 keeps
  the CM at 1.10 V with 600 mV of differential drive.
- **`CL` stays ideal on purpose** -- it models the next stage's input
  capacitance. Note the summing stage's `Gm1` gate capacitance more than
  doubles the total on that node (40 fF -> 66.9 fF measured), which is why
  splitting the stages moved `f_peak`.

### The schematics are a cross-check, and they are STALE

`netlists/*.sch` are **never simulated by the framework**. Every number in this
file comes from decks that `eqrl/netlist.py` generates and writes to `/tmp`. The
schematics exist so an independent netlister can be diffed against the Python
one (VERIFY.md §3) -- two descriptions that must agree.

**They currently describe the OLD merged topology** (tap draining onto the CTLE
loads, `voutp`/`voutn` node names) and have not been regenerated for the
two-stage design. Do not use them to judge the present circuit. See §11.

---

## 5. Module reference

### `eqrl/pdk.py` — PDK discovery and PVT corners
| Symbol | Purpose |
|---|---|
| `find_sky130_lib()` | locates `sky130.lib.spice`; honours `SKY130_LIB` / `PDK_ROOT` |
| `PROCESS_CORNERS` | `("tt","ss","ff","sf","fs")` — these vary **MOSFETs only** |
| `RC_CORNERS` | `(None,"ll","hh","hl","lh")` — **separate axis**, varies R and C |
| `Corner(process, vdd, temp, rc=None)` | one PVT point |
| `Corner.lib_section` | maps to the `.lib` section name |
| `pvt_grid(mode)` | `"quick"`=9, `"spec"`=20, `"full"`=60 (`full=bool` kept as alias) |

**Corner grids — "5 corners" is a miscount.** The brief's
*"TT,SS,FF,SF,FS; VDD±5%; 0–125 °C"* is **5 process × V × T = 20 corners**, not 5.

| mode | corners | use |
|---|---|---|
| `quick` | 9 | development smoke test |
| `spec` | 20 | the brief's literal cross-product, R/C typical |
| `full` | 60 | + the R/C axis — the real worst case |

Measured historically (on the old merged topology): worst `f_peak` was
**2.517 GHz on `spec`** but
**2.991 GHz on `full`** — reporting only the spec grid **understates the worst
case by 19 %** and makes a real failure look like a rounding error.
Failing corners: 4/20 vs 18/60.

This is a sky130 quirk, not pedantry: in most PDKs "SS" means slow transistors
*and* slow R/C, but `.lib ss` and `.lib tt` here include the **identical**
`res_typical__cap_typical` models. R/C is a separate axis that physically exists
and moves `f_peak` by 29.7 % — more than the process corners do. Quote both
numbers in any write-up.

**Critical**: the `.lib` section naming is irregular. `tt` + `ll` is the section
**`ll`**, *not* `tt_ll`; but `ss` + `hh` is `ss_hh`. `lib_section` handles it.

### `eqrl/netlist.py` — netlist generation
| Symbol | Purpose |
|---|---|
| `PARAM_BOUNDS` | the **11** design variables and their ranges (§7) |
| `NFET` / `RES` / `CAP` | device model names — **`NFET` is the single source of truth** |
| `W_MAX_UM` / `w_mult()` | a single nfet may not exceed **100 µm total width**; wider devices split into parallel units with `m=` |
| `res_geometry(R)` / `cap_side(C)` | measured value→geometry maps |
| `CtleParams` | the design point. `Wgf`/`Igf` are **derived properties** of `tap_pct`, not independent fields |
| `.core_netlist()` | STAGE 1, the CTLE alone |
| `.summer_netlist()` | STAGE 2, Gm1 + GmF into shared `Rsum` |
| `.full_netlist()` | both — this is what actually gets simulated |
| `channel_netlist()` | the lossy FR4-like channel (§10b) |
| `dfe_decision_path(ui_s)` | comparator → 1 UI hold → tap drive |
| `dfe_static_bias()` / `dfe_static_tap(±1)` | tap at CM / fully steered |
| `denorm()` / `norm()` | [0,1]ⁿ ↔ physical values |

### `eqrl/spice.py` — ngspice driver
`Simulator(workdir, timeout=300, use_trimmed=True, use_channel=False)`

| Method | Returns |
|---|---|
| `.ac()` | CTLE and summing-node response, **referenced to `v(vinp,vinn)`** so peaking describes the equaliser, not the channel+equaliser cascade |
| `.op()` | input pair, tail, **and both Gm stages**: `gm_g1`, `gm_gf`, realised `tap_ratio`, measured `i(Vdd)` |
| `.tap_weight()` | realised tap injection at the summing node |
| `.tran_hd3()` | HD3 in dBc from a 100 MHz sine |
| `.noise()` | input-referred noise, **channel-free by default** (§10 bug 17) |
| `.tran_eye()` | closed-loop PRBS through the real DFE loop |
| `.evaluate(..., combined=True)` | everything; op/ac/tran/noise from **one** deck (§9) |
| `Simulator.scan_log()` | 15 fatal ngspice signatures — ngspice prints errors and still exits 0 |
| `safe_job_count()` | parallelism capped by **MemAvailable**, not just core count |

### `eqrl/analytic.py` — the automatic starting point
Derives `RL`, `Rs`, `Cs` from the half-circuit equations rather than starting the
search at the centre of an 11-dimensional box. `gm` and `Cout` are **measured**
from an `.op` at run time and the result is **calibrated against SPICE in a
closed loop** (damped — undamped it oscillates, §10 bug 18). That makes it smart
initialisation, not a hand-tuned seed, which matters for the brief's "zero human
intervention".

**What it is worth, measured** (`scripts/seed_benchmark.py`,
`results/seed_vs_search.json` — all three points scored through the same
`Objective`, corners and scorer the search used):

| design point | ungated reward | brief specs, 20 corners | binding |
|---|---|---|---|
| blind start (box centre) | 0.4631 | **3 / 8** | `fpeak` −99.9% |
| **analytic seed** | **0.9050** | **5 / 8** | `eye_h` −44.8% |
| after CEM | 0.9873 | **8 / 8**, PASS | `hd3` +4.1% |

So the equations do most of the *distance* (0.46 → 0.91) and none of the
*compliance*: the seed still fails **eye height, eye width and HD3** across the
20 corners, and trips the `equalises` check. That is exactly the shape you would
predict from what `analytic.py` models — it solves 4 of the 11 knobs from the
small-signal AC equations and has **no model of distortion, noise, eye or
power**, so the constraint that now binds (HD3, at −26.3 dBc against a −30 dBc
limit) is one it cannot see. The search's job is everything the equations are
blind to.

Read the gated `reward` column with care: both non-compliant rows clip to
exactly 0.3000 at the `equalises` gate, so only `reward_raw` separates them.

### `eqrl/trimlib.py` — trimmed model libraries
Writes a per-corner `.lib` holding only the devices this design instantiates
(169 files / 7.12 MB → 93 files / 1.24 MB) and **refuses to use one until it is
proven identical to the full library** over 5 design points at 1e-9 relative
tolerance. An unvalidated or stale section silently falls back to the full
library: slow, never wrong.

### New in checkpoint 2 — the measurement API

| Symbol | Purpose |
|---|---|
| `EqualizerSpec.require_eye` | **True by default**: a missing eye result FAILS rather than deleting `eye_h`/`eye_w` from the scorecard (§10 bug 24). `--no-eye` sets it False for deliberately AC-only runs |
| `EqualizerSpec.tx_swing_pp_v` | transmitter drive, differential pk-pk, at the channel input. 200 mV pp — **conservative** against a real Gen2 transmitter's ~800 mV. An eye number is meaningless without it |
| `Metrics.eye_openness_out` | eye opening as a **fraction of the swing at the same node** — invariant under gain |
| `Metrics.eye_improvement` | openness at the slicer ÷ openness at the equaliser input. **>1 means real equalisation**; measured 1.97× (1.53× worst corner), and 0.15 with the degeneration removed. SCORED as an internal check since bug 31 |
| `Metrics.eye_height_in_v` / `eye_height_ctle_v` | the eye before the equaliser and after stage 1, so a gain-only solution is visible |
| `Metrics.out_cm_v` | summing-node common mode, so drift from the mid-supply heuristic is measured rather than assumed (§4) |
| `specs.margins()` / `binding_spec()` | fractional margin to the **nearest** limit, and the spec closest to failing. Two-sided specs must be quoted from the binding edge |
| `specs.BRIEF_SPECS` / `INTERNAL_CHECKS` | 8 customer requirements vs the 2 internal checks (`bias`, `equalises`) |
| `Simulator.tran_hd3(channel=False)` / `.noise(channel=False)` | both default to channel-free: linearity and input-referred noise are properties of the EQUALISER (§10 bugs 22–23) |

### New in this round — measurement conditions and the two-stage HD3

| Symbol | Purpose |
|---|---|
| `spice.MeasCond` | **every** condition a measurement is taken under: `ui_s`, `tx_swing_pp_v`, `hd3_freq`, `hd3_amp_pp_v`, `noise_f_lo/hi`. Built from the spec by `MeasCond.from_spec()` — one mapping, one place. Duck-typed because `specs.py` imports `Metrics` from `spice.py`, so importing the spec here would be circular |
| `EqualizerSpec.ui_s` | `1/(2*nyquist)`. NRZ Nyquist is half the baud rate, so the brief's 2.5 GHz is exactly 200 ps — the value that used to be hardcoded (§10 bug 29) |
| `Metrics.hd3_db` / `hd3_ctle_db` | HD3 **at the slicer** (scored) and after stage 1 only (diagnostic). The second is what the project used to quote; it flatters by ~25 dB (§10 bug 27) |
| `Metrics.channel_present` | was there ISI to remove? The `equalises` check is meaningless without a channel — from an ideal PWL source, openness can only drop — so the condition travels with the measurement (§10 bug 31) |
| `EqualizerSpec.eye_improvement_min` | threshold for the `equalises` internal check; 1.0 = "no better than its own input" |
| `specs.SOFT_FRAC` / `HD3_SOFT_DB` | reward barrier widths as a fraction of each limit, so a different spec keeps its gradient. Chosen to reproduce the old absolute values exactly at the default spec (§10 bug 30) |

### `eqrl/dfe.py` — DFE + eye
`prbs()`, `nrz_pwl()`, `Eye`, `eye_from_waveform()` (closed-loop),
`eye_from_pulse_response()` (cheap peak-distortion path, **written but not yet
wired into the RL loop**).

### `eqrl/specs.py` — targets and reward
`EqualizerSpec` holds all targets. `score(m, spec)` returns
`{"reward", "scores", "pass", "metrics"}`. Soft helpers `_plateau`, `_target`,
`_below`, `_above` all return **0.0 for NaN**, so a failed analysis scores as a
failure rather than silently passing.

### `eqrl/env.py` / `eqrl/agents/cem.py` / `eqrl/design.py`
`Objective` = callable `u → EvalResult` with caching + optional worst-case-over-
corners. `CtleEnv` = standard `gymnasium.Env` so PPO/TD3 can be swapped in.
`cem.optimize()` = population-based policy search. `design.run_design()` =
orchestrator (specs in → sized netlist + report + plots).

---

### `scripts/pvt_sweep.py` — parallel PVT sweep with timing
```bash
python3 scripts/pvt_sweep.py --grid spec --eye --set tap_pct=5.0
python3 scripts/pvt_sweep.py --grid full --eye --jobs 16
```
`--grid quick|spec|full` (9/20/60), `--eye` adds the closed-loop DFE run,
`--set K=V` overrides any `CtleParams` field, `--jobs N` sets parallelism.
Writes `pvt_sweep.{json,csv}` and prints per-analysis timing.

**Every worker gets its own workdir.** The analyses write fixed filenames
(`ac.dat`, `tran.dat`, `eye.dat`); sharing a directory across processes would
silently corrupt results — one worker reading another's dump.

## 6. Measured device characterisations

**These were measured, not derived. The textbook formulas were wrong.**

### Resistor `sky130_fd_pr__res_high_po_1p41` — ports `r0 r1 b`
The model's `rsheet` is **not** ohms/square: it scales inversely with width
(`rsheet × W ≈ 323 Ω/sq` for every variant), i.e. it is already **ohms per µm of
length**. Forcing 10 µA:

| l (µm) | 2 | 5 | 10 |
|---|---|---|---|
| R (Ω) | 781.1 | 1431.3 | 2568.2 |

→ **R = 334.3 + 223.4·l**. The offset is contact resistance, so **R below
~450 Ω is unreachable** with one unit — `res_geometry()` uses parallel `mult`.
The `b` (body) terminal is a third pin ideal resistors don't have; tie to GND.
These models carry **voltage coefficients**, so they are mildly nonlinear.

### MIM cap `sky130_fd_pr__cap_mim_m3_1` — ports `c0 c1`
10×10 µm → 206.6 fF; 12.25×12.25 µm → 308.2 fF
→ **C = 2.0·s² + 0.76·s (fF, s in µm)**.

### `W` is TOTAL width, not per-finger (measured)
`_nf()` splits a device into ~10 µm fingers and passes `W` unchanged. That is
correct: `W=40 nf=4` and four parallel `W=10 nf=1` devices draw **bit-identical
1661.613 µA**. Had `W` been per-finger, every width in the design would have
been silently multiplied by its finger count. Pinned by
`tests/test_spice.py::test_nfet_w_is_total_width`.

A consequence worth noting: because `W` is total, the old mirror (`Wref`=15/nf=2
against `Wtail`=60/nf=6) had the correct 4:1 ratio to first order, and the
finger-width difference (7.5 vs 10 µm) is only a second-order narrow-width
effect — measured at **0.23 %** of supply current. The mirror is now written as
identical unit devices replicated with `m=4`, which is the layout intent; it is
not a numerical fix.

### Device flavour — LVT chosen
At W=20 L=0.18 nf=2, Vgs=Vds=0.9 V:

| | `nfet_01v8` | `nfet_01v8_lvt` |
|---|---|---|
| Vth | 691.4 mV | **555.9 mV** |
| gm | 6.289 mS | 7.992 mS |

At *matched current* (600 µA, which is our actual case) the gm gain is only
**+8%**, not +27% — the +27% was at fixed Vgs. **The real win is headroom**: the
source node sits at 128 mV instead of 24 mV.

---

## 7. Design variables (11)

| Param | Range | Role |
|---|---|---|
| `W` | 1–100 µm | CTLE input-pair width |
| `L` | 0.15–1.0 µm | CTLE input-pair length |
| `RL` | 100–5000 Ω (log) | CTLE load — with `Cout` this sets `f_peak` |
| `Rs` | 50–10000 Ω (log) | degeneration R — sets the boost `1+gm·Rs/2` |
| `Cs` | 10 fF–3 pF (log) | degeneration C — sets the zero `1/(2π·Rs·Cs)` |
| `Itail` | 50 µA–2 mA (log) | CTLE tail current per side |
| `Wtail` | 5–200 µm | CTLE tail device width |
| `Rsum` | 100–2000 Ω (log) | summing-node load; also sets the output CM |
| `Wg1` | 1–100 µm | Gm1 (main path) width |
| `Ig1` | 100 µA–4 mA (log) | Gm1 tail current |
| `tap_pct` | 1–30 % | **DFE tap weight** |

Fixed: `CL = 40 fF`, `Vcm = 1.10 V`, `L_TAIL = 0.5 µm`, `L_GM = 0.18 µm`,
`MIRROR_RATIO = 4`.

### `tap_pct` is ONE variable, not two — and that is not cosmetic

A current-steering DFE tap is a **scaled copy** of the main transconductor:

```
I_TAP = tap% · I_MAIN        W_TAP = tap% · W_MAIN
```

Both scaled together, so the tap runs at the **same current density** (same
Vgs, same Vdsat, same switching behaviour) and its `gm` scales **linearly** with
`tap_pct`. This is how the reference design does it (§17).

Sizing `W` and `I` independently — which this framework used to do — puts the
tap devices at a different operating point from the pair they are meant to
match and makes `gm` go as `sqrt(W·I)`. A "30% tap" was not 30% of anything.
`Wgf` and `Igf` are now derived properties, floored at the PDK's minimum drawn
width.

**`Rs·Cs` is a second-order trap.** It sets the CTLE's settling time, and that
decides *where the post-cursor lands*. Measured on this design:

| `Rs·Cs` | settling | peaking | dominant post-cursor |
|---|---|---|---|
| 637 ps | 3.2 UI | 9.64 dB | **h[2]** — a 1-tap DFE cannot reach it |
| 450 ps | 2.2 UI | 6.62 dB | **h[1]** ✓ |
| 60 ps | 0.3 UI | 0 dB | h[1], but no boost at all |

Optimising the AC equations alone lands in the first row: good peaking, and a
tail the DFE cannot cancel. Only the eye metric sees this, which is an argument
for keeping the eye in the inner loop.

**Headroom is a real constraint**:
`VDD ≥ Rsum·(Ig1+Igf)/2 + Vds(Gm) + Vds(tail)`, and the same on the CTLE side.

---

## 8. Current design state

The RL-sized design (§16) at the typical corner, **with the channel present**.
Every number below is read from `results/report.json`, not recalled:

| Metric | Value | Spec | |
|---|---|---|---|
| Peaking | 6.74 dB @ 1.588 GHz | 3–12 dB, 1.25–2.5 GHz | pass |
| DC gain | -1.81 dB | — | gain @ Nyquist = +4.25 dB |
| Power | 2.67 mW | < 15 mW | pass — **excludes the ideal comparator and latch** |
| HD3 | **-32.9 dBc** | < −30 dBc | pass, at the **slicer**, both stages (§10 bug 27) |
| HD3 after stage 1 only | -57.9 dBc | — | diagnostic; this is what used to be quoted |
| Input noise | 0.502 mVrms | < 1.5 mVrms | pass, **both stages** counted (§10 bug 1a) |
| Area | 0.000308 mm² | < 0.05 mm² | pass |
| Eye | 202.1 mV × 0.609 UI | > 100 mV, > 0.4 UI | at **200 mV pp** drive |
| **Eye openness** | **0.355 of swing, 1.97× better than at the input** | > 1 | see below |
| Realised `gmF/gm1` | 0.0076 (`tap_pct` = 1.07 %) | — | §16 |
| Summing-node CM | 1.489 V of 1.80 V | — | mid-supply heuristic wants 0.900 V (§4) |

### Eye openness is the number that shows equalisation

Eye **height** can be bought with summing-stage gain — measured across the 20
corners it correlates with that gain at **0.982** and with peaking at only
**0.468**, because `Rsum`/`Wg1`/`Ig1` are free variables. It is a scored
internal check now, not merely a reported one (§10 bug 31). **Openness** (opening
as a fraction of the swing at the same node) is invariant under scaling, so
`eye_improvement` = openness at the slicer ÷ openness at the equaliser input
measures equalisation rather than amplification.

Measured: **1.97×** (1.53× at the worst corner), and it collapses to **0.15**
when the degeneration capacitor is removed. Doubling the summing-stage gain moves eye
height by tens of mV and `eye_improvement` barely at all. So this design really
does equalise.

---

## 9. Performance facts (drive the architecture)

| Measurement | Value |
|---|---|
| ngspice run, **no** PDK | **4 ms** |
| ngspice run, `.lib tt` | **~7.1–7.7 s** → 99.95% is PDK model parsing |
| Trimmed `.lib` (nfet + R/C + `all.spice`) | **0.54 s — 13.2×, bit-identical results** |
| 4 candidates batched in one deck | 7.27 s vs 30.6 s unbatched (**4.2×**) |
| Closed-loop 200 UI eye | 8.4 s total, of which **only ~1.3 s is solve** |

**Why parsing is slow** — a known open-pdks issue
([#163](https://github.com/RTimothyEdwards/open_pdks/issues/163)): sky130 MOSFETs
are "micro-binned", and `.lib tt` pulls ~30 files covering the *entire* PDK
(pfet, 3.3/5/20 V, ESD, bipolars, RF). Others report *minutes*.

**The trimmed-lib fix**: build a `.lib` containing only `nfet_01v8` + R/C +
`all.spice`. The blocker was **not** `mc_mm_switch` (a red herring) — it was
`all.spice`, which carries **`.option scale=1.0u`**, the microns→metres
conversion for the BSIM4 bins (min-L bin is 1.5e-07). **Not yet integrated into
the framework** — see §11.

### Speed optimisations: BUILT and MEASURED (2026-09-13)

Two of the three are now implemented and validated. Measured single-process,
no contention, one full `evaluate()` at the typical corner:

| | s/eval | gain |
|---|---|---|
| baseline: full `.lib`, 4 separate ngspice launches | **29.55** | 1x |
| combined deck (op+ac+tran+noise in ONE launch) | **7.88** | **3.75x** |
| + trimmed `.lib` | **1.32** | **22.4x** |

They multiply: the trim shrinks each parse, the combine removes three of the
four parses.

**Launches per evaluation: 2.** One channel-free deck carrying op/ac/tran/noise,
one closed-loop eye with the channel. It was briefly **4** — each correctness fix
added a channel-free re-run — until it was noticed that the combined deck never
needed the channel in the first place (§10 bug 25). That alone took a full search
from 2268 launches to 1512.

**Combined deck** (`Simulator._combined`): op/ac/tran/noise share a netlist and
a DFE bias, so they differ only in the analysis card. One source carries DC, AC
and SIN simultaneously and each analysis takes the part it needs. Verified
**bit-identical** to the separate path -- 29 metrics x 3 design points, worst
relative difference 0.00e+00. `evaluate(combined=False)` keeps the old path for
bisecting.

**Trimmed `.lib`** (`eqrl/trimlib.py`, `scripts/build_trimlib.py`): the `tt`
section pulls **169 files / 7.12 MB** covering pfet, 3.3/5/20 V, ESD, bipolars
and RF. Trimmed to **93 files / 1.24 MB**. Nothing is used until `validate()`
proves it identical to the full library across 5 design points at 1e-9
relative tolerance; an unvalidated or stale section silently falls back to the
full library, so the failure mode is slow, never wrong.

**What went wrong the first time, and the lesson.** All 7 sections were
REJECTED on the first attempt: the dependency analysis kept files matching
`.subckt <device>` and therefore dropped
`sky130_fd_pr__nfet_01v8_lvt__mismatch.corner.spice`, which defines only
*parameters*. ngspice then said `Undefined parameter [...__toxe_slope] / Cannot
compute substitute` and the deck died. A missing MODEL is loud; a missing
PARAMETER is loud here too -- but a missing `r+c` include would NOT be, since
it merely changes `rm3`/`rcvia3` that the MIM cap's parasitics read. That
asymmetry is why validation compares measured metrics instead of trusting the
analysis. The rule now matches parameter definitions as well, ignoring
`.include` lines (otherwise an aggregator matches every device name and the
trim achieves nothing -- a guard that also fired during development).

Still not built: batching several candidates into one deck (~16x more).

### Historical note: no speed optimisations were in use

The full 60-corner sweep finishing in 5.3 minutes is **entirely** 16-way
parallelism. Every individual simulation is as slow as it has ever been.

The measurement that makes this stark — single process, no contention:

| | time |
|---|---|
| PDK parse with a **single 1 kΩ resistor** | 7.61 s |
| Full 12-device equalizer, 173-point AC sweep | **7.60 s** |
| **difference = the actual circuit solve** | **−0.01 s** |

Solving the entire equalizer is **free** — below the measurement noise floor.
Per corner we launch ngspice **5 times** (ac, op, hd3, noise, eye) = 38.1 s of
parsing, of which **30.4 s is re-parsing identical models**. Only the eye differs
(10.66 s), so ~3 s of it is real transient work.

| optimisation | status | gain |
|---|---|---|
| trimmed `.lib` | validated bit-identical — lives only in a **code comment** | **14×** |
| one launch per corner instead of 5 | designed, not built | **5×** |
| batch candidates into one deck | designed, not built | ~16× for the RL loop |
| parallel over corners | ✅ **the only one built** | 15× |

```
measured now                    79.6 s/corner
trimmed lib alone (5 launches)   2.7 s/corner
trimmed lib + single launch      0.54 s/corner    -> ~147x still on the table
```

**Parallelism does not compose the way the others do.** 16-way contention makes
each run ~2× slower (7.6 s → ~15 s), so you get 15× throughput, not 16×, and
that ceiling is fixed by core count. The other two fixes are multiplicative on
top and would make contention stop mattering. The full PVT sweep would go from
**5.3 minutes to roughly 2 seconds**.

**Consequence for the RL loop**: at 0.54 s/candidate the closed-loop eye is
affordable in the inner loop and the planned cheap pulse-response surrogate is
**unnecessary**. Do not build it before re-measuring.

---

## 10. Bugs caught and fixed

### Latent bugs that would have silently corrupted the RL loop

0. **`mult=` does nothing on a sky130 resistor — parallel units were never
   built.** `res_geometry()` returns `(length, mult)` and drops to parallel
   units for any R below the ~450 Ω contact-resistance floor, and the netlist
   emitted that as `mult=N`. But in `sky130_fd_pr__res_high_po_1p41` the `mult`
   parameter appears **only inside the Monte-Carlo mismatch expressions**
   (`res_match`, `rend`) and never divides either resistance element — with
   `MC_MM_SWITCH=0` it does nothing at all. Measured, forcing 1 V through
   `w=1.41 l=5`:

   | | R |
   |---|---|
   | one unit | 1407.6 Ω |
   | `mult=4` | **1407.6 Ω — no effect whatsoever** |
   | `m=4` | 351.9 Ω |
   | 4 explicit parallel instances | 351.9 Ω |

   `m` is ngspice's *subcircuit* multiplier and is the one that works. So
   `Rs=50 Ω` was being simulated as **450 Ω, 9× too large**, and `RL=100 Ω` as
   500 Ω. Every default resolves to `mult=1`, which is why every number
   recorded in this file is self-consistent and why the committed PVT sweep is
   unaffected — **it would have fired the moment the optimiser explored the low
   end of the `RL`/`Rs` bounds, which it is explicitly allowed to do.** Fixed:
   the netlist now emits `m=`. Regression test:
   `tests/test_spice.py::test_resistor_parallel_units_need_m_not_mult`.

   Related, and worth knowing: `mult=` on a sky130 **MOSFET** is also inert
   (the subckt declares it but never passes it to the internal BSIM4 device),
   and `mf=` on the MIM cap likewise only feeds the mismatch term. None of
   those were load-bearing — the MOSFET `mult=1` was a no-op either way — but
   do not reach for any of them expecting a multiplier.

1. **`wrdata` filenames were quoted.** `spice.py` emitted `wrdata "ac.dat"`;
   ngspice does not strip quotes, so it created a file literally named
   `"ac.dat"` while Python looked for `ac.dat`. **`ac()` and `tran_hd3()` had
   never worked.** Only found because AC had previously been validated through
   hand-written scratch decks that used `print` instead.
2. **`inoise_total` was being square-rooted.** ngspice already reports it as
   integrated RMS volts. The extra `sqrt` turned 2.915e-4 into **17 mVrms**, an
   apparent 11× spec violation that was pure arithmetic. Correct: **0.29 mVrms**,
   matching `√(4kTγ/gm)·√BW`.
3. **NaN slipped through the noise guard.** `val < 0` is False for NaN, so
   ngspice's `-nan` returned `ok=True` and poisoned the metric. Degenerate
   sizings (a 20 µm tap pair at 10 µA → deep subthreshold) genuinely produce it,
   and the agent *will* propose such points.
4. **DFE feedback polarity was inverted** (old merged topology; the same
   trap was re-measured, not re-reasoned, for the two-stage stage — see
   `tests/test_spice.py::test_tap_opposes_the_main_path`). `dq` drove `vfbp`, but M3's drain is
   `voutn`, so `vfbp` high drives the output *positive* — feeding the
   previous decision straight through therefore **reinforced** the post-cursor
   instead of cancelling it. The inverted output must drive `vfbp`. Measured
   on the old merged topology at a 150 µA tap:
   0.078 UI before, **0.453 UI** after. Found only because a reader asked why
   the schematic "is just a diff pair sharing loads".
   **Retracted claim**: I had written that the monotonic eye collapse vs tap
   size "confirms correct feedback polarity". It does not — with no channel
   *either* polarity adds an uncorrelated ±w term and closes the eye, so that
   test cannot distinguish them.
5. **Eye-width metric was circular.** Splitting traces by sign *at each phase*
   separates the groups by construction → width was always **1.000 UI** and
   could never report a closed eye. Fixed: classify once by the decided bit at
   the widest-separation phase, hold that label fixed across all phases.

6. **ngspice's own noise integration is broken here.** `inoise_total` returned
   `-nan` at **5 of 9** PVT corners — while the SPECTRUM it computed at those
   same corners was perfectly clean (all 54 points finite and positive). Its
   *integration* routine is the broken part, not the noise analysis. Every one
   of those "noise failures" was an artifact, not a spec violation.
   Fixed by dumping `inoise_spectrum` (`setplot noise1`) and integrating in
   numpy: `rms = sqrt(∫S(f)²df)`. Validated where ngspice does work:
   **mine 0.509 vs ngspice 0.502 mVrms, 1.4 % agreement**. Noise now passes
   **60/60** corners. `ngspice_inoise_total` is still reported as a cross-check.

### Caught pre-emptively (would have failed silently)
7. **Hardcoded device probe.** `spice.py` had
   `dev = "@m.x1.msky130_fd_pr__nfet_01v8"`. Switching to LVT changes the
   internal name; a stale probe → `op()` returns `ok=False` → `saturated=False`
   → `specs.score()` caps **every** candidate's reward at 0.2, flat search, no
   error. Now derived from `nl.NFET`, the single source of truth.
8. **Parser suffix collision.** `_parse_print_row` matched on `[vds]`, so
   probing two devices would silently keep only the last. Replaced by
   `_parse_prints`, keyed on the **full** expression.

### SPICE / tooling gotchas
9. **XSPICE instance names must start with `a`.** `dac_p`/`dac_n` were parsed as
   *digital* devices → "model type mismatch / Unable to find definition".
   Renamed `adac_p`/`adac_n`. Also: `adc_bridge`/`dac_bridge` take **bracketed
   vector** ports, but `d_dff` takes **scalar** ports
   (`data clk set reset out Nout`).
10. **Reference current source direction.** `Iref vbias 0 DC 150u` *drains* the
   node (`v(vbias) = −0.79 V`, zero current). Must source from the supply:
   `Iref vdd vbias DC 150u`.
11. **xschemrc must be `source`d, not copied.** The PDK file does
   `append XSCHEM_LIBRARY_PATH :[file dirname [info script]]`, so a copy points
   at `~/.xschem` instead of the PDK and **no sky130 symbols are found**. It
   also sets `XSCHEM_START_WINDOW` to an *absolute* path (line ~799) which
   xschem re-prefixes → doubled-path error dialog. `~/.xschem/xschemrc` now
   sources the PDK file in place and blanks `XSCHEM_START_WINDOW`.
   Backup of the bad copy: `~/.xschem/xschemrc.copied-backup`.
12. **`.spiceinit` is required for correctness, not speed.** It buys only ~3% on
    runtime, but *without* it xschem-generated netlists fail outright
    (`Formula() error`, "No compatibility mode selected"). Copy
    `$PDK_ROOT/sky130A/libs.tech/ngspice/spinit` to `.spiceinit` in any
    directory you simulate from.

### Found while rebuilding to the two-stage topology (2026-09-13)

13. **ngspice folds instance-name case.** The new summing-stage tail `XT1`
    collided with the CTLE's `Xt1` — the same instance as far as ngspice is
    concerned — and the deck died with `device already exists, bail out`.
    Renamed `XTsum1`/`XTsumf`. Pinned by a test asserting no two instance names
    collide case-insensitively.

14. **A single nfet may not exceed 100 µm of TOTAL width**, and `nf` does not
    help. The widest model bin is 7–100 µm; beyond it ngspice refuses with
    `could not find a valid modelname`. Measured:
    `W=100 nf=10` ok, `W=120 nf=12` FAIL, `W=150 nf=30` (5 µm/finger) FAIL — so
    binning is on total W, not width per finger. The summing-stage tails scale
    as `Wtail·Ig1/Itail` and reach ~16000 µm at the box corners, so wide devices
    are now split into parallel units with `m=`. This was latent in the ORIGINAL
    code too: `Wtail` is bounded at 200 µm and the old `Xt1 W=Wtail` would have
    failed above 100.

15. **A DFE tap must be scaled as a whole branch.** `I_TAP = tap%·I_MAIN` AND
    `W_TAP = tap%·W_MAIN` together, so the tap keeps the main branch's current
    density and `gm` scales linearly. Sizing `W` and `I` independently makes
    `gm` go as `sqrt(W·I)` and the tap devices sit at a different Vdsat — a
    "33.7% tap" was not 33.7% of anything. From the reference design (§17).

16. **`_ac_from_file()` never received the AC-referencing fix that `ac()` got.**
    Two parsing paths, one patched. With a channel present `evaluate()` — which
    uses the combined deck — silently reported the **cascade's** peaking
    (2.15 dB) while `ac()` reported the **equaliser's** (9.64 dB). Nothing
    errored. Both paths now reference `v(vinp,vinn)`, with a test asserting they
    agree.

17. **Noise was referred to the transmitter through the channel.** ngspice
    refers noise to a *source*, so with the channel in front the channel's loss
    inflated the input-referred figure and a passing design failed the spec
    spuriously. The brief's "input-referred noise" is a property of the
    **equaliser**, whose input is the channel's output, so `noise()` now
    measures the equaliser alone by default.

18. **The analytic calibration oscillates if undamped.** Feeding back
    `k *= measured/target` gave 1.60 → 13.88 → 10.55 dB against a 7.5 dB target,
    because the model's error is not proportional. A square-root step converges
    monotonically: 1.60 → 8.55 → 7.88 → 7.62.

19. **CEM never evaluated its own starting mean.** Samples are drawn from
    `N(mu, std)`, which never yields `mu` exactly — so an analytically derived
    starting point was only ever a *centre for the scatter* and was never
    scored. Injecting it as the first population member took round 1 from
    **0.765 to 0.986**. Without this, "seeding" does nothing.

20. **The reward saturated at every spec boundary — the search could not prefer
    a passing design.** The soft scores exist to give gradient far from a spec,
    but at the boundary they flatten: `f_peak` 10 MHz over the 2.5 GHz limit
    scored **0.99972** against **1.00000** for one that passes; an eye 1 mV
    short scored 0.99960. That is far below the noise of a 48-sample population,
    so a marginally-failing design was indistinguishable from a passing one. A
    whole run stalled at reward 0.998 for three iterations with a spec still
    failing and no gradient to fix it. Fixed with a **hard-pass bonus**:
    `HARD_PASS_WEIGHT = 0.10` of the reward now comes from the fraction of hard
    specs met, making a boundary crossing worth ~1.1 % instead of 0.03 % — a
    **40× larger step**. The very next run crossed at iteration 4
    (`f_peak` 2.52 → 1.89 GHz).

21. **`abstol=1e-12` is too tight for the channel ladder** — 80 inductors kill
    the transient with "timestep too small". `abstol=1e-10` fixes it (§10b).

### Found by external review, and fixed (checkpoint 2)

These three produced **wrong reported numbers**, not wrong verdicts. Every one
has the same shape: an instrument that was correct for an earlier version of the
circuit and was never moved when the circuit changed.

22. **Noise was measured on only half the equaliser.**
    `noise v(outp_ctle,outn_ctle)` stopped at the CTLE output, so the summing
    stage's contribution was never counted. The line was written when there WAS
    only one stage. Worse than a plain omission: the CTLE runs at **below-unity
    gain** (−4.59 dB), so noise added after it refers back to the input
    **amplified**, not attenuated. Now measured at `outp_dfe/outn_dfe`.
    Effect: 0.515 → **0.545 mVrms** typ, 0.607 → **0.655** worst-case. Still a
    2.3× margin, so the verdict held — the advertised margin did not.

23. **HD3 was measured at half drive.** The test tone was injected ahead of the
    channel, which eats 6.2 dB at 100 MHz, so the circuit was exercised at ~49 %
    amplitude — and weak signals distort less. Linearity is a property of the
    equaliser, so it is now measured channel-free, exactly as `noise()` already
    was. Effect: **−76.0 → −63.4 dBc**, a 12.6 dB flattery removed.
    *Not* a tolerance artefact: verified identical at ngspice's default
    `abstol`, at 1e-10, and at 1e-14.

24. **A missing eye deleted two specs instead of failing.** When the eye sim
    produced no result, `eye_h`/`eye_w` simply vanished from the pass dict and
    `all_pass()` returned **True over the remaining seven** — a design could be
    declared compliant by dropping the two hardest requirements. The eye sim
    genuinely can fail (convergence, timeout, an OOM-killed worker), so this was
    reachable; it just never fired in a recorded run. A NaN eye now FAILS, with
    an explicit `EqualizerSpec.require_eye=False` opt-out that `--no-eye` sets
    for deliberately AC-only evaluation.

25. **My own defect fixes caused a 2× speed regression.** Each fix added an
    ngspice launch: the channel-free noise re-run, then the channel-free HD3
    re-run, taking an evaluation from 2 launches to 4. The combined deck never
    needed the channel at all — `op` is **bit-identical** with and without it
    (measured 0.00e+00, since no DC current flows into MOSFET gates), `ac` is
    referenced to `v(vinp,vinn)` so it is channel-invariant, and HD3/noise are
    now deliberately channel-free. Only the eye needs it. Back to **2 launches**:
    2268 → **1512** for a bit-identical search path. Found only because someone
    asked why the run was slow.

26. **A fourth stale `Itap` reference** (`p.Itap` in `scripts/smoke_test.py`).
    The audit that found the first three checked `CtleParams(...)` **constructor
    kwargs** but not **attribute access**, so this one survived it. It surfaced
    only when the documented commands were executed for real rather than
    `--help`'d. The audit now covers attribute access, kwargs and string keys.

### Found by a second review round (2026-09-13, after checkpoint 2)

27. **HD3 was still measured on ONE STAGE OF TWO — the same defect as bug 22,
    surviving in a second place.** Bug 22 moved the *noise* probe from
    `outp_ctle` to the summing node, with the reasoning written into
    `spice.py`: the equaliser is two stages and the brief asks for a property
    of the equaliser. Nobody applied that reasoning to HD3, which kept probing
    `v(outp_ctle,outn_ctle)`.

    It bites **harder** here than it did for noise. `summer_netlist()`'s Gm1 is
    an **undegenerated** differential pair — the CTLE has `Rs‖Cs` linearising
    it, the summing stage has nothing — so the omitted stage is the *more*
    distorting one, and it is the node the slicer actually decides on.

    Predicted from square law before measuring (`Id=204 µA`, `gm=3.92 mS` →
    `Vov≈104 mV`, 59 mV on Gm1's gates, `HD3≈(1/32)(Vid/Vov)²` → −39.9 dBc),
    then measured:

    | | HD3 at typ |
    |---|---|
    | `outp_ctle` (stage 1 only — what shipped) | **−63.4 dBc** |
    | `outp_dfe` (at the slicer — correct) | **−38.7 dBc** |

    So the advertised margin was **33.4 dB and is really 8.7 dB**. Still passes
    −30 dBc, but the number that was quoted described the more linear half of
    the circuit. `hd3_ctle_db` is still reported alongside, because the
    stage-by-stage split is what makes the result explainable — the same way
    `eye_openness_ctle` sits beside `eye_openness_out`.

28. **`tx_swing_pp_v` was recorded but never used.** `Simulator.__init__` set
    `self.tx_swing_pp_v = 0.200` and **nothing anywhere assigned it from the
    spec** — while `design.py` passed `spec.tx_swing_pp_v` to the deck exporter
    and wrote it into `report.json`. Setting it to 800 mV would have given
    metrics simulated at 200 mV, a report claiming 800 mV, and an exported deck
    built at 800 mV: **defect D1 exactly** (artifact disagrees with report),
    through a different door, and invisible only because the default happened
    to equal the constant.

    Root cause is the pattern, not the value: a post-construction attribute
    assignment has no call site to update. It is a constructor argument now
    (`MeasCond`), carried through `env._eval_task`'s task tuple because the
    workers build their own `Simulator` in another process — anything not in
    that tuple silently reverts to defaults there.
    `tests/test_pure.py::test_worker_task_tuple_matches_what_the_worker_unpacks`
    compares both ends of the tuple by AST so the two cannot drift.

29. **`spec.nyquist` was read by NOTHING.** `tran_eye`'s `ui=200e-12` default
    was never overridden by `_evaluate_combined`, and the noise band
    (`10e6..5e9`), HD3 tone (`100e6`) and Nyquist probe (`2.5e9`) were literals
    in `spice.py`. The framework was hard-wired to 5 Gb/s: a spec asking for
    8 Gb/s got a confident 5 Gb/s answer with **no error at all**. All six
    conditions now live in `MeasCond`, built from the spec by one mapping.

30. **The reward's soft barriers did not scale with the spec.** `soft=3e-3` for
    power means that asking for power < 2 mW gives a barrier 1.5× wider than
    the whole limit — the score is flat exactly where it has to steer, and the
    search runs blind until the hard-pass bonus fires. They are fractions of
    each limit now (`SOFT_FRAC`), chosen to reproduce the previous absolute
    values **exactly** at the default spec, so no published number moves. HD3
    deliberately keeps an absolute 6 dB width: dB is already a ratio scale, so
    "20% of −30 dB" is not a meaningful quantity.

31. **`eye_improvement` was reported but never scored** — and my first attempt
    at scoring it was wrong in an instructive way. Checkpoint 2 added the
    metric that distinguishes equalising from amplifying, but nothing in
    `score()` consulted it, so the gaming path was documented and disproven for
    one sizing while staying wide open for any re-run. It is an internal check
    now, alongside `bias` (deliberately **not** a brief spec —
    `gain_nyq_min_db` is this project's cautionary tale about inventing one).

    The instructive part: gating on `eye_improvement >= 1` unconditionally
    **failed the default design**, reward 0.98 → the 0.3 cap. With no channel
    the stimulus is an ideal PWL source, so the input eye is already perfect
    and openness can only drop — improvement < 1 is then the correct physical
    answer, not a defect. The gate is conditional on `channel_present`, which
    `Metrics` now records, so the condition travels with the measurement
    instead of being left for the scorer to assume.

### Corrections to earlier claims in this file

- **"The mirror's unequal finger widths are a matching defect."** Retracted.
  Measured, `W=40 nf=4` and `m=4` of `W=10 nf=1` are **bit-identical**
  (1661.613 µA). The effect on supply current is 0.23 %. The mirror was
  rewritten as identical unit devices anyway, but as layout intent, not a fix.
- **"The eye fails because the gain is insufficient."** Wrong. There is
  **1017 mV pp** at the slicer while the eye opening is 112 mV. Amplitude was
  never the problem — the DFE tap was (§10b).
- **"h[1] is only 0.7 % of the cursor, so the DFE has nothing to cancel."**
  Measured at the **wrong node**. That was after the CTLE had already removed
  the post-cursor. At the channel output — where the reference measures it — it
  is **37.6 %**.
- **`sf` and `ss` ship DIFFERENT nfet models.** They include different
  `.pm3.spice` files, so `sf` is not "`ss` with a different pfet" even in an
  all-NMOS circuit. This is why `sf`@125 °C behaved unlike `ss`@125 °C.

### Predictions that were wrong (corrected by measurement)
- **`rsheet` is not Ω/square** — it's Ω per µm of length. The formula gave
  1.52 µm where the true answer is 3.88 µm.
- **Adding the DFE pair raised `f_peak`** (2.376 → 2.517 GHz), not lowered it. I
  only accounted for added drain capacitance; the pair's **output resistance
  appears in parallel** at the same node, lowering the effective load and
  pushing the output pole up. That dominates.
- **A 100 Ω load does NOT collapse `peaking`** — I wrote a sanity check claiming
  it would. Peaking actually *rises* (9.03 → 11.55 dB); it is the **peak gain**
  that collapses (8.91 → 4.76 dB). A small `RL` pushes the output pole up so more
  of the degeneration boost is realised even as absolute gain falls.
- **"The monotonic eye collapse confirms correct feedback polarity"** — invalid
  reasoning, and it was masking bug 4. With no channel *either* polarity adds an
  uncorrelated ±w term and closes the eye, so that test cannot distinguish them.
- **"A channel is essential"** — stated repeatedly as fact; it is judgment. The
  brief does not require one and no spec depends on it (§10b).
- **HD3 and noise both degraded** with real devices, as expected but by more
  than guessed: HD3 −81.6 → **−67.0 dBc** (resistor voltage coefficients), noise
  0.292 → **0.390 mVrms** (an ideal tail contributes *no* noise; a real one does).

---

## 10b. Channel — BUILT, and the spec set is incoherent without it

Earlier revisions of this file argued the channel was optional because the brief
never mentions one. That was correct about the brief and **wrong about the
engineering**, and it is now measured.

### Why it is not optional

With no channel there is no post-cursor, so the mandated 1-tap DFE has nothing
to cancel and its injection is pure uncorrelated error. Measured on the brief's
20 PVT corners, no channel, tap active vs tap held at common mode:

| | eye failures | eye height range |
|---|---|---|
| DFE tap ON | **6/20** | 19.7 – 151 mV |
| DFE tap OFF | **0/20** | 201 – 263 mV |

The tap cost roughly half the eye height at every corner. Every "eye failure"
was caused by the DFE itself. The peaking spec is equally hollow with no loss
to compensate, and the optimiser correctly responded by abandoning equalisation
altogether — it was optimising an ill-posed problem, not misbehaving.

### What works, and what does not

| approach | loss @ 2.5 GHz | tilt 1–5 GHz | |
|---|---|---|---|
| ngspice `LTRA` | −0.16 dB | none | its `r` is frequency-independent |
| naive RLGC ladder | −302 dB | — | badly parameterised, just a big RC |
| correctly-sized ladder | −15.3 dB | 2.2 dB | **flat attenuator — no dispersion, no ISI** |
| **ladder + frequency-dependent series R** | −16.8 dB | **10.6 dB** | ✅ what is built |

Flat loss creates no ISI. **Dispersion is the whole point.**

### The model as built (`netlist.channel_netlist`)

40 sections per leg, 1.2 ns total delay (~7 inch of FR4), `Z0` = 50 Ω per leg,
each section carrying `Rdc` + two staggered `R‖L` skin-effect stages + `L`, with
shunt `C`. 50 Ω series at the source, **100 Ω differential** termination at the
receiver. Measured:

```
0.1 GHz   -6.20 dB        2.5 GHz  -16.77 dB   <- Nyquist
1.0 GHz  -10.33 dB        5.0 GHz  -22.07 dB
equalizable tilt 0.1 -> 2.5 GHz = 10.6 dB, inside the brief's 3-12 dB boost window
```

Pulse response at the **channel output** (the reference's measurement point):
pre-1 9.4 %, cursor 232 mV, **post-1 37.6 %**, post-2 15.5 % — against the
reference's 48.8 % post-cursor at 12 Gb/s. Comparable, and the difference is
the data rate: the same channel produces far more ISI per UI at 83 ps than at
200 ps.

### Three problems it raises, and how each is solved

- **Common mode.** The termination is **differential** (`vinp`–`vinn`, not to
  ground) and the shunt caps are open at DC, so no DC current flows and the CTLE
  still sees `Vcm` at its gates. A termination to ground would unbias the input
  pair.
- **Convergence.** 80 inductors break ngspice's default `abstol` (1e-12):
  transients die with *"timestep too small, trouble with node ...#branch"*.
  `abstol=1e-10` fixes it. Deliberately **not** `method=gear`, which would damp
  the ladder's ringing and understate the ISI being measured.
- **Cost.** 563 extra elements. The eye transient is now the dominant runtime
  (1.67 s of a 2.99 s evaluation) and it is real solve time, so the trimmed
  library does not help it.

### What it did NOT fix

Even with the channel, a correctly-scaled tap, and a swept tap weight, the DFE's
best contribution on this design was **+0.8 % eye height at 5 %**, degrading
beyond 10 %. The optimiser then drove `tap_pct` to 1.38 %. That agrees with the
reference's own sweep shape and with its closing remark: *"The system may not
need to use DFE (or higher-tap DFE) if CTLE can give an enough eye opening."*
At 5 Gb/s with 11 dB of loss and 8 dB of CTLE boost, one tap has little left to
do. See §16.

---

## 11. Known issues / open items

### Defects found in review (2026-09-13) — both since FIXED in checkpoint 2

These were found by a reviewer reading the shipped artifacts, after this file
already claimed they had been verified. **Both are now fixed** (§10 bugs 22–26
are the same review round); the descriptions are kept because the failure mode
is more instructive than the patch.

**D1 — FIXED. `results/ctle_final_eye.sp` had no channel and did not reproduce
the report.** Now verified to match to **0.00 %** (it was 213 % wrong).

`design.py::_export_eye_deck()` writes the PRBS sources straight onto
`vinp`/`vinn`. When the framework runs an eye with the channel enabled,
`spice.py::_assemble()` instead retargets those sources to
`chsrc_p`/`chsrc_n`, appends `nl.channel_netlist()`, **and** adds
`.options abstol=1e-10 vntol=1e-6`. The exporter does none of it, and the run
that produced `report.json` had `channel: true`.

```
results/ctle_final_eye.sp   591.8 mV x 0.750 UI
report.json typ_metrics     189.0 mV x 0.547 UI      -> 3.1x optimistic
```

The failure direction is the bad one: the deck **exits 0**, logs nothing worse
than a benign `m=xx` warning, and hands a reviewer a *better* eye than this
project claims. A fix must add the channel **and** the `abstol` option — with
80 inductors and the default 1e-12, the deck would die with "timestep too
small".

`ctle_final.sp` (AC) is missing the channel too, but `ac()` divides out
`vdb(vinp,vinn)`, so peaking and `f_peak` are **channel-invariant by
construction** — the AC deck agrees with the report to 0.000 % and therefore
cannot expose this. **VERIFY.md §14 cross-checks only that deck**, i.e. the one
whose metric cannot fail. That is why the defect shipped.

Root cause: when the channel was added, `use_channel` was threaded through
`Simulator`, `Objective`, `env._eval_task`, `run_design` and the CLI — but not
the two exporters, which take `(params, corner, path)` and cannot know. Same
family as bugs 16 and 17: **two code paths building the same thing, one
patched.** The fix should route the exporters through `Simulator._assemble`
(forcing `use_trimmed=False`, since a trimmed library is not portable) rather
than adding a third copy of the deck-building logic.

**D2 — FIXED. `Igf` was no longer settable; three call sites still passed it
and three tests failed.** A **fourth** site was found later (§10 bug 26).

`Igf`/`Wgf` became read-only properties of `tap_pct` (§7). Three sites were
missed:

| site | symptom |
|---|---|
| `scripts/plot_eye.py:25` | `TypeError: CtleParams.__init__() got an unexpected keyword argument 'Igf'` |
| `tests/test_spice.py:240-241` | same TypeError — `test_an_oversized_tap_closes_the_eye` never runs |

and two further tests encode **stale assertion bounds** from when the default
tap was ~34 % rather than today's 5 %:

| test | asserts | actual | why |
|---|---|---|---|
| `test_tap_weight_is_realised` | `tap_frac_of_main > 0.05` | 0.0397 | a 5 % tap injects ~4 % of the main path — correct |
| `test_measured_tap_ratio_is_sane` | `0.1 < tap_ratio` | 0.0462 | `gmF/gm1` ≈ 0.046 for a 5 % tap — correct |

**All three failures are test staleness, not circuit defects.** The measured
values are exactly what a 5 % tap should produce.

`plot_eye.py`'s flag should be `--tap-pct`. Note also that its
`if a.igf else {}` treats `0` as "not supplied", so `--tap-pct 0` would be
silently ignored; it wants `is not None`.

**VERIFY.md §5 documents the broken invocation twice** — those two commands
were written into the docs *after* the breakage and have never worked.

### Why these shipped, and the pattern worth fixing

The same fact living in several places, updated in some and not others:

| defect | what drifted |
|---|---|
| D1 | `_assemble` gained `use_channel`; the exporters did not |
| D2 | `tap_pct` threaded through 3 files; 3 call sites missed |
| pvt_sweep status (fixed) | regenerated; 1 of 3 notes updated |
| test counts (fixed) | changed; 2 of 3 docs updated |

And the verification then sampled the copy that could not fail: `--help` instead
of a real invocation, the AC deck instead of the eye deck, the fast tier instead
of the full suite. **Check the path that can fail, or the check is decorative.**


| Item | Notes |
|---|---|
| **`netlists/*.sch` are STALE** | They describe the OLD merged topology. They are a cross-check only — never simulated — but they no longer match `netlist.py`, so the device-for-device diff in VERIFY.md §3 will not agree until they are regenerated. |
| **Sweep-vs-RL baseline — MEASURED** | Random search at an equal **95-candidate** budget, same 4 corners, same scorer, same channel and eye: **best reward 0.726, 0 of 95 passing**, against CEM's **0.987 and a design that passes every spec at every corner**. `results/baseline.json`. The exhaustive side stays arithmetic: at the measured 4.42 s per candidate a 5-point grid over 11 variables (5¹¹ = 48.8 M designs) is **6.8 years**, a 3-point grid 217.7 hours. Caveat worth stating: CEM also had the analytic seed, so this compares two methods as a whole, not the search operator alone — the unseeded CEM run (§16) is the third data point. |
| **No schematic export** | The brief asks for "the final schematic"; we emit `.sp` netlists. xschem's `.sch` is a text format, so generating one from `full_netlist()` is tractable and unbuilt. |
| **"Tunable" has no mechanism** | The brief says *tunable 1.25–2.5 GHz* and *variable Rs, Cs*. `Cs` is a design-time constant; a switchable cap bank with a control word is not built, and nothing tests tunability (§0). |
| **`ad`/`as`/`pd`/`ps` pinned to 0** | Junction capacitance is zero in the netlist, so output capacitance is understated and every `f_peak` is optimistic. Measured `Cdd` = 0.14 fF, which is the tell. |
| **Slicer/latch are ideal** | XSPICE code models with 20 ps clk→Q against 50–150 ps for a real StrongARM, no offset, no metastability, no kickback. The <200 ps loop-delay budget is asserted, not proven. The reference labels its own comparator and flop "(Ideal)" too. |
| **No CDR** | The sampling clock phase is fixed, not recovered. Swept across 8 phases it changed the eye by under 2 %, so it is not currently binding — but a real link needs one. |
| **`f_peak` can be reported at the sweep edge** | A response still rising at 20 GHz reports `f_peak = 20 GHz`, which is "no peak in range", not a peak. The scorer handles it (plateau → 0) but the number misleads in logs. |
| **Noise referral is approximate** | Referred to `Vinp` alone, which carries half the differential drive; may be off by ~2×. |
| **HD3 amplitude is an assumption** | 100 mV differential; the brief states the frequency but not the amplitude. |
| **No conda `environment.yml`** | `requirements.txt` exists; Anaconda is named in the brief's Tools. |

---

## 12. What is NOT built

- **LLM natural-language wrapper** — the bonus deliverable.
- **Schematic output** — netlists only (§11).
- **Runtime tunability** — no cap bank, no control word (§0).
- **Transistor-level slicer and latch** — behavioural by design.
- **Candidate batching** — one deck per candidate; ~16× still available (§9).
- **A bit rate other than 5 Gb/s is now WIRED but untested.** `spec.nyquist`
  really does drive the UI, the latch clock and the Nyquist probe (§10 bug 29),
  and a pure test pins the arithmetic — but no design has actually been sized at
  another rate, and the channel model is fixed FR4 regardless. Treat a non-5 Gb/s
  run as unvalidated, not as unsupported.

---

## 12b. Version control

Initialised 2026-09-12. Initial commit `177fb74` — 24 files, 7,322 insertions.

**Identity is set repo-LOCAL, not global**, so nothing machine-wide was changed:
```
user.name  = kanishk                                  <- from the Linux account
user.email = f20230748@pilani.bits-pilani.ac.in
```
Change with `git config --local user.name "..."` if that attribution is wrong.

**`.gitignore` covers** `__pycache__`, `*.dat`/`*.raw`/`*.out` (SPICE dumps),
`.spiceinit`, generated `*.png`/`*.svg`/`*.pdf`, and editor dirs.

**`results/` is tracked deliberately** as evidence:

| file | what it is |
|---|---|
| `report.json` | the RL run's full output — sizing, resolved geometry, cost, per-corner PVT. **The current result (§16).** |
| `ctle_final.sp` | the sized equaliser, DFE loop open — runs standalone |
| `ctle_final_eye.sp` | the sized equaliser, loop closed |
| `pvt_sweep.{json,csv}` | the same design swept by a different **driver** |

`pvt_sweep.{json,csv}` was regenerated for the final design with the channel on
and reports **0 of 20 corners failing**, matching `report.json`.

⚠️ **This is NOT an independent confirmation, and earlier revisions of this file
called it one.** Both go through `Simulator.evaluate()`; only the driver above
it differs. Two files agreeing to every digit is what happens when they run the
same code. It confirms the *reporting and aggregation* paths, nothing about the
physics. Regenerate with:

```bash
python3 scripts/pvt_sweep.py --grid spec --eye --channel --set tap_pct=1.37903 ...
```

(the full `--set` list is in the git log for this file's commit).

No remote is configured. `git remote add origin <url>` when you have one.

## 13. Recipes

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b

# tests: 282 total -- 244 fast (no PDK) + 38 against real ngspice.
python3 -m pytest tests/ -q -m "not needs_pdk"   # 131, ~5 s
python3 -m pytest tests/ -q                      # all 282, ~2 min

# SPICE layer sanity (asserts, so it can fail)
python3 scripts/smoke_test.py

# build + VALIDATE trimmed model libraries (22.4x faster evaluations)
python3 scripts/build_trimlib.py --grid spec
python3 scripts/build_trimlib.py --check          # what is validated

# THE DELIVERABLE: specs in -> sized design out
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel
python3 scripts/run_design.py --no-analytic-init  # unseeded, for comparison

# what actually gets simulated, for any design point
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;print(CtleParams().full_netlist())"
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import channel_netlist;print(channel_netlist())"

# PVT sweep of one design point (--set overrides any CtleParams field)
python3 scripts/pvt_sweep.py --grid spec --eye --set tap_pct=5.0

# eye diagram
python3 scripts/plot_eye.py -o /tmp/eye.png

# schematics (CROSS-CHECK ONLY, and currently stale -- see 11)
xschem netlists/eq_tb.sch

# version control
git log --oneline
```

---

## 14. Recommended next step

Ranked by what the brief actually grades:

1. **Buy back HD3 margin.** Now that HD3 is measured where the slicer decides
   (§10 bug 27) the design passes by **1.24 dB**, and that is the binding spec.
   The lever is the undegenerated Gm1: degenerate it, or trade its gain down
   and let the CTLE carry more. The eye-height spec pays for it, so this is a
   real design trade rather than a free fix — which is exactly why it is worth
   doing deliberately rather than leaving the optimiser to sit on the boundary.
2. **Size at a rate other than 5 Gb/s.** The plumbing exists and is tested
   (§10 bug 29) but has never been exercised end to end, and the channel model
   is fixed FR4 regardless of the rate. Either validate it or say so.
3. **Export a schematic.** The deliverable says *"outputs the final
   schematic"*; we emit netlists, and `netlists/*.sch` are stale (§11). xschem's
   format is text, so generating one from `full_netlist()` is tractable.
4. **Batch candidates into one deck** (§9) — ~16× and the only large speed win
   left, now that parsing is no longer dominant.
5. **Then** the runtime cap bank for "tunable" (§0), a transistor-level slicer,
   and the LLM wrapper. All three are credibility investments rather than
   coverage gaps.

Housekeeping: no conda `environment.yml`. (`results/pvt_sweep.*` is **not**
stale — it was regenerated for the final design and corroborates `report.json`
exactly; see §12b.)

---

## 15. Test suite

`pytest`, in two tiers, because an ngspice launch costs ~8 s of PDK model
parsing no matter how small the circuit — so anything provable without a
simulator should be.

```bash
python3 -m pytest tests/ -q -m "not needs_pdk"   # 244 tests, ~32 s, no PDK
python3 -m pytest tests/ -q                      # all 282, ~2 min
```

| tier | file | needs | covers |
|---|---|---|---|
| pure | `tests/test_pure.py` (124) | nothing | value↔geometry round trips, `denorm`/`norm`, corner→`.lib` section names, PRBS, eye metrics, the scorer's NaN and gating behaviour, netlist text invariants, the Gymnasium env |
| spice | `tests/test_spice.py` (37) | PDK + ngspice | device-model conventions (`m=` vs `mult=`, `W` total vs per-finger), every analysis, the tap polarity, the combined deck's equivalence, trimmed-library equivalence, `evaluate()` end to end |

`tests/conftest.py` defines the `needs_pdk` marker and skips that tier when
`find_sky130_lib()` raises, so the fast tier runs on a machine with no PDK.

**Tests that exist because the bug actually happened** — these are the ones to
keep if anything is ever trimmed:

| test | guards |
|---|---|
| `test_resistor_parallel_units_need_m_not_mult` | §10 bug 0 — `mult=` is inert, R below ~450 Ω was up to 9× too large |
| `test_nfet_w_is_total_width` | the W/finger convention every current in the design depends on |
| `test_eye_from_waveform_reports_a_closed_eye_as_closed` | §10 bug 5 — the circular width metric that returned 1.000 UI for any input |
| `test_eye_width_counts_only_a_contiguous_opening` | width counted disjoint openings as one eye |
| `test_dfe_feedback_polarity_is_inverted` | §10 bug 4 — `dq` vs `dqb` driving `vfbp` |
| `test_a_stale_device_probe_fails_loudly` | §10 bug 7 — a stale probe capped every candidate's reward at 0.2, silently |
| `test_pass_keys_are_stable_and_complete` | the `KeyError: 'area'` that killed `run_design()` after the whole search |
| `test_dfe_clock_period_follows_the_requested_ui` | the DFE clock pinned at 200 ps while the PRBS followed the requested UI |
| `test_partial_results_stay_scoreable_and_flagged` | a corner that fails to simulate must not read as a passing corner |
| `test_each_candidate_corner_pair_gets_its_own_workdir` | parallel workers sharing `ac.dat`/`eye.dat` |
| `test_nan_metrics_never_score_as_a_pass` | §10 bug 3 — NaN slipping through a `< 0` guard |
| `test_instance_names_are_unique_case_insensitively` | §10 bug 13 — `XT1` IS `Xt1` to ngspice |
| `test_no_instance_exceeds_the_width_limit_anywhere_in_the_box` | §10 bug 14 — the 100 µm model-bin ceiling |
| `test_tap_branch_is_a_scaled_copy_of_the_main_branch` | §10 bug 15 — gm must scale linearly with `tap_pct` |
| `test_peaking_is_the_equalisers_not_the_cascades` | §10 bug 16 — two AC paths, one patched |
| `test_reward_has_a_real_step_at_each_spec_boundary` | §10 bug 20 — the saturation that stalled a whole run |
| `test_first_population_contains_the_starting_mean` | §10 bug 19 — seeding without injection does nothing |
| `test_summer_stage_is_a_real_summing_node` | both transconductors must share one load pair |
| `test_tap_opposes_the_main_path` | the polarity trap, re-opened by the topology change |
| `test_combined_deck_is_bit_identical_to_separate_analyses` | one launch instead of four must change no number |
| `test_resource_failures_are_distinguished_from_design_failures` | an OOM-killed worker is not a bad design |

---

## 16. The RL loop, run end to end

**Command** (~11 min on 16 cores):
```bash
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel \
        --pop 48 --iters 15 --seed 0
```

### Result: all 8 brief specs pass at all 20 of the brief's PVT corners

With the lossy channel in front and the DFE active. **0 of 20 corners fail
anything.**

| spec | worst case over 20 corners | limit | |
|---|---|---|---|
| HF peaking | 5.89 dB | 3–12 dB | pass |
| f_peak | 1.781 GHz | 1.25–2.5 GHz | pass, +6.9% |
| **HD3** | **−31.2 dBc** | < −30 dBc | **binding, +4.1% (1.24 dB)** |
| input noise | 0.617 mVrms | < 1.5 mVrms | pass |
| power | 2.89 mW | < 15 mW | pass |
| area | 0.000308 mm² | < 0.05 mm² | pass |
| eye height | 131.1 mV | > 100 mV | pass |
| eye width | 0.563 UI | > 0.4 UI | pass |

The scorecard shows **10** entries; the other two (`bias`, `equalises`) are
internal checks, **not customer requirements**. Quote 8.

**Quote the margin from the BINDING edge.** `report.json` records
`binding_spec` and `margins_worst` so this cannot drift.

**The binding spec changed when HD3 was measured correctly** (§10 bug 27). With
the probe on the CTLE output the same design reads **−55.2 dBc** worst-case and
`f_peak` looks binding at +13.2%; with the probe where the slicer decides, HD3
is **−31.2 dBc** and binding at **+4.1%**. Nothing about the circuit changed —
only which half of it was being measured.

**1.24 dB is genuinely tight**, and it should be read as such: it is inside the
range that model uncertainty, the missing junction capacitance (§11) and a real
(non-ideal) slicer could plausibly move. The design passes as measured; it is
not a design with comfortable distortion margin. Buying margin back means
degenerating Gm1 or dropping its gain, and the eye-height spec pays for it.

### Sizing found

```
W=30.26um  L=0.2426um  RL=983.1ohm  Rs=1890.8ohm  Cs=253.3fF
Itail=539.0uA  Wtail=84.47um  Rsum=1823.0ohm
Wg1=33.44um  Ig1=345.0uA  tap_pct=1.072%
```

Compared with the sizing found before HD3 was measured at the slicer, the search
backed the summing stage off: `Ig1` 407 → 345 µA and `Wg1` 42.9 → 33.4 µm, with
more of the gain moved into the CTLE (`RL` 783 → 983 Ω, DC gain −4.59 → −1.81 dB).
That is the expected response to a distortion constraint that finally sees the
undegenerated stage — and a useful side effect, since summing-stage gain is
exactly what eye height could otherwise be bought with.

### Cost

| | |
|---|---|
| candidates simulated | **95** |
| (candidate × corner) evaluations | 380 |
| ngspice launches, search | 760 |
| ngspice launches, 20-corner verify | 40 |
| convergence | `[0.9778, 0.9873]`, stopped early at iteration **2 of 15** |

Two launches per evaluation: one channel-free deck carrying op/ac/tran/noise,
one closed-loop eye with the channel. An earlier version ran four — the combined
deck was needlessly carrying the channel and HD3/noise were then re-run without
it. See §9.

⚠️ `stop_on_pass` halts at the **first design that passes**, which is
first-feasible, not the *"near-optimal"* the brief asks for. That caveat stands.

The sweep comparison the Problem Statement asks for **has now been run** (§11):
random search at the same 95-candidate budget, through the same `Objective`,
same corners, same scorer, reached **0.726 and 0 of 95 passing** against CEM's
**0.987 and a passing design**. So the search is doing real work, not riding the
analytic seed alone — though it is fair to say the seed is part of the method,
and the unseeded CEM run below is the third data point.

### What made it work — three fixes, each found by a run that failed

| run | result | what it showed |
|---|---|---|
| unseeded, box centre | never found the basin in 15 rounds | 11-D is too big to start blind |
| seeded, no injection | 0.765 at round 1 | `mu` was never evaluated (§10 bug 19) |
| seeded + injected | 0.986, then stalled at 0.998 | the reward saturated at the boundary (§10 bug 20) |
| **+ hard-pass bonus** | **crossed at round 4** | `f_peak` 2.52 → 1.89 GHz |

### The DFE result, stated plainly

**`tap_pct` converged to 1.38 %** — near the bottom of its 1–30 %
range. The optimiser kept the mandated 1-tap DFE and turned it down as far as
allowed. A manual sweep peaked at 5 % with **+0.8 %** eye height and degraded
past 10 %; the reference design's own sweep has the same shape and concludes
*"the system may not need to use DFE ... if CTLE can give an enough eye
opening."* At 5 Gb/s with this channel and 8 dB of CTLE boost, one tap has
little left to cancel. It would matter at 12 Gb/s.

### Caveats

- **`ad`/`as`/`pd`/`ps` are 0**, so junction capacitance is missing and every
  `f_peak` is optimistic — and `f_peak` is the **binding** spec, so the tightest
  margin sits on the least trustworthy number.
- The slicer and latch are ideal with 20 ps clk→Q; the loop-delay budget is
  asserted, not proven. Power excludes them entirely.
- The eye is measured at **200 mV pp** drive — conservative
  against a real Gen2 transmitter's ~800 mV, so it understates rather than
  flatters.
- Verified on the brief's **20** corners; the 60-corner R/C grid is a project
  addition and was not re-run for this design.

---

## 17. The reference design this architecture follows

`github.com/muhammadaldacher/SERDES-Design-of-RX-Decision-Feedback-Equalizer`
— a 1-tap DFE for 12 Gb/s NRZ over 14-inch FR4. Three things were taken from
its report and corrected long-standing errors here:

1. **Tap scaling**: `I_TAP = tap%·I_MAIN` **and** `W_TAP = tap%·W_MAIN`
   together (§7).
2. **Measure the pulse response at the CHANNEL OUTPUT**, not after the CTLE.
   Its numbers: pre-1 46 %, cursor 483 mV, **post-1 48.8 %**, post-2 23.9 %.
3. **Output CM at mid-supply**: `0.5·I_Gm1·R_Gm1 = VDD/2`.

Its measured tap sweep — eye improving to ~10 % then collapsing by 30 % — is the
same shape reproduced here, and its stated mechanism matches: *"the DFE improves
the high-frequency components of the data at the expense of the low-frequency
components."*

## 18. Checkpoint 5 — model-based, goal-conditioned RL

### 18.0 What this adds, and why CEM was not enough

Checkpoints 1-4 optimise **one spec**. CEM is handed an `EqualizerSpec`, runs
~95 candidates against ngspice, and returns a design. Give it a different spec
and it starts over, keeping nothing. Measured in §16, that is 7 minutes and
~800 ngspice launches *per spec*, every time.

It is also, as §11 admits, hard to distinguish from "vary knobs by percentages
and see what sticks" on a 2-iteration run.

So checkpoint 5 adds a method that is RL in the sense the term normally means:
a **policy**, trained once, that maps *(where the design is now, what the spec
asks for)* to *(what to change)*, and is then applied to specs it has never
seen. The cost of the training is paid once and amortised over every future
spec. Seeded CEM stays exactly as it is and becomes the baseline it is compared
against.

The obstacle is arithmetic. A PPO policy needs on the order of a million
environment steps. Every step of `env.CtleEnv` is a SPICE evaluation at ~4.4 s
across four corners — about 18 s of simulation per step. A million of those is
over two hundred days. So the policy is trained inside a **learned model of the
circuit**, and only the finished policy is let near ngspice. This is the
standard construction (MBTD3, DAC 2024; AnaCraft, TCAD 2025), and it splits the
work into four gated stages:

| stage | what it produces | gate |
|---|---|---|
| collect (`collect_data.py`) | labelled (design, corner) → metrics | resumable, crash-drilled |
| surrogate (`train_surrogate.py`) | a SPICE stand-in, physics-informed | **G1** rank correlation |
| policy (`train_policy.py`) | goal-conditioned PPO, trained in the surrogate | **G2** held-out success |
| deploy (`deploy_policy.py`) | real ngspice on unseen specs, vs CEM at equal budget | **G3** the comparison table |

### 18.1 Nothing is allowed to live only in memory

Before any of it, a defect had to be fixed that this project had been paying
for silently: `Objective._cache` is an in-memory dict, so **every labelled
evaluation died with the process**. Roughly 1 650 of them — about two hours of
ngspice — no longer exist. A surrogate is worth exactly the labels it is
trained on, so that had to stop being true first.

`eqrl/cache.py` is an append-only JSONL log. JSONL rather than a database or a
pickle for one reason: it is the only format where a process killed mid-write
damages *at most the final line*, and every record before it is still readable.
One `write()` of a complete line, then `flush()`, then `os.fsync()`, per
record — at 4.4 s per simulation the fsync costs nothing, and it is what makes
a power cut cost one record instead of one buffer. Only the parent process ever
opens the file; sixteen workers appending concurrently would interleave partial
lines.

Records carry the `MeasCond` they were measured under, and `load()` refuses
records taken under different conditions unless told otherwise. That is the
`tx_swing_pp_v` bug class (§10 bug 28) caught at read time rather than trusted.

**A bug found by testing rather than by reasoning.** A torn final line has no
newline, so a naive append *concatenates onto the fragment* and both records
become one unparseable line — the resumed run would silently have lost its first
new result as well as the interrupted one. Measured before the fix: four
records plus one append loaded back as four, not five. `EvalLog.open()` now
terminates a partial line first, so a kill costs exactly one record.
`tests/test_cache.py` pins it.

**The crash drill was run before any of this was trusted.** Collection was
started, the parent `kill -9`ed with fourteen simulations in flight, and
restarted: 25 records survived, 0 unparseable, and the resumed run reported
25/10096 done and continued without recomputing the analytic seed.

One operational trap worth recording: `nohup setsid cmd &` makes `$!` the pid
of `setsid`, which forks and exits immediately, so a pidfile written by the
shell names a process that is already dead and `kill -INT` on it does nothing.
Every long-running script now writes its own pidfile.

### 18.2 The surrogate is grey-box, and the data says why

The hand equations in `analytic.py` track SPICE **well in order and badly in
value**. Measured on this project's own collected data:

| quantity | Spearman vs SPICE | median predicted / actual |
|---|---|---|
| peaking | +0.88 | 1.77x |
| dc gain | +0.89 | 1.08x |
| power | +0.81 | 0.81x → 1.08x after the fix below |

A consistent multiplicative bias is the cheapest thing in the world for a
residual model to remove. So metrics with a usable closed form are predicted as
a **correction to it**:

```
y = f_analytic(u, corner) * exp(g_theta(features))     (ratio scales)
y = f_analytic(u, corner) +     g_theta(features)      (dB scales)
```

with `g_theta`'s final layer **zero-initialised** — before training the model
*is* the hand calculation, and training can only improve on it. A test pins
that. `area_mm2` is computed exactly and never predicted; predicting an exactly
computable quantity only adds error.

**Three things the data disproved.**

1. **Supply power is not `vdd * (Itail + Ig1_branch)`.** `netlist.py` builds
   *separate per-side tails*, `Xt1` and `Xt2`, each mirrored 4:1 off one
   diode-connected reference — so the pair draws `2*Itail` and the reference
   another `Itail/4`. The obvious formula is wrong by more than 2x and read as
   correct until it was checked against `i_supply`. With the right structure
   and *measured* `id_tail` it reproduces `i_supply` at a median ratio of
   **1.000** when the tail is saturated; the spread that remains is delivery —
   a tail in triode sources ~87 % of the requested current (n=66) — and is left
   to the learned residual.

2. **gm of the summing stage does not fit from its own geometry** (R² 0.17 in
   log space, 77 % median error). It is physical: Gm1's gate is driven by the
   CTLE output, whose common mode is `vdd − Itail/2·RL`, so how hard it is on
   depends on the *first* stage's bias. Feeding that Vgs estimate in more than
   doubles the fit. Without it the network was being handed a feature that was
   mostly noise.

3. **The calibration factors must be measured against the fitted gm.** Computed
   against the square-law default they came out at 0.81x — the wrong direction
   entirely — because the gm error folds into them. Against the fitted gm they
   are 1.40x on peaking and 1.21x on f_peak, consistent with the optimism §10
   documents and with the reference design's own 12.68 dB predicted against
   10 dB simulated.

### 18.3 The eye had to be computed, not learned

The eye was by far the worst-predicted metric — held-out Spearman **0.37** on
height and **0.36** on width, against 0.92 for peaking and 0.95 for dc gain —
and it carries the highest weight in the scorer (`w_eye = 1.2`).

G1 showed the consequence exactly: as data arrived, per-metric accuracy **rose**
(peaking R² 0.73 → 0.88, f_peak −1.76 → −0.09) while the reward correlation
**fell**, 0.51 → 0.37. Predicting eight metrics well and two badly is not a
surrogate, because the reward is dominated by the ones it is worst at.

The mistake was asking for the eye from AC features. An eye *is* the pulse
response sampled at symbol spacing: ISI is h[−1], h[1], h[2] relative to h[0],
the DFE cancels a fraction of h[1], and what is left is the closure. That is a
computation.

`physics.channel_H` is an ABCD cascade of exactly the ladder
`netlist.channel_netlist` builds. It does not depend on any design variable, so
it is computed once per frequency grid and cached. **Validated, not assumed:**
it returns −16.77 dB at Nyquist with 10.68 dB of tilt, where direct measurement
of the built netlist gives −16.8 and 10.6. The cascade channel → CTLE → summer
is multiplied by the spectrum of one rectangular UI and inverse-transformed.

Measured effect, same split:

| | before | after |
|---|---|---|
| `eye_width_ui` ρ | 0.359 | **0.691** |
| `eye_improvement` ρ | 0.393 | **0.675** |
| `eye_height_v` ρ | 0.370 | **0.550** |
| `vn_in_rms` ρ | 0.297 | 0.587 |
| G1 | 0.368 | **0.561** |

**A finding worth keeping.** These features correlate with the eye *only on
designs actually biased in saturation* (ρ +0.43), and essentially not at all
across the whole collection (ρ −0.00). That is physically right — a
small-signal pulse model means nothing for a circuit not operating as a linear
amplifier — and `log_summer_gain` even *reverses sign* between the two regimes.
The regime is something the model must represent, which is part of why the
saturation classifier shares the trunk.

### 18.4 Two metrics that had to be moved, and one dropped

**Noise** was a direct head and behaved like one: held-out Spearman swung
between 0.59 and 0.23 across two runs on the same data. It has a closed form.
Getting the *structure* right mattered more than the constant — the input pair
alone ranks designs at ρ +0.25, adding the load resistors' thermal noise
referred through the stage gain gives +0.28, and adding the summing stage
referred through the CTLE's gain gives **+0.57**. The residual can absorb a
constant factor (it reads 0.44x low); it cannot absorb a missing dependence.
Noise pass-agreement went 0.60 → 0.88.

**`tap_ratio` was dropped as a target.** It spans 0.006 to 1.5e5 in the
collected data, so after linear standardisation a handful of outliers owned the
direct heads' loss entirely: including it drove its own held-out R² to −6.1 and
dragged the other direct heads down with it. It is diagnostic and never scored.

**`f_peak` is masked where there is no peak.** A flat design reports f_peak at
the sweep start (1 MHz), making the residual target an outlier an order of
magnitude larger than any real correction. Masked rows get no gradient, so the
head's output there is arbitrary — and un-clipped, `exp()` turned that into an
f_peak of 4e13 Hz and a held-out R² of **−6859**. Residuals are now clipped to
a factor of 7.4 either way. A prediction may be wrong, but it must stay in the
range where "wrong" still means something.

### 18.5 Splitting, and what G1 actually measures

The split is **by design, never by row**. The four corners of one design are
four nearly identical rows; splitting by row puts some in train and some in
validation, and the held-out score then measures memorisation.

G1 was initially Spearman over *every* held-out design, which is the wrong
denominator. About half the collected designs are not biased in saturation, and
there the pulse model means nothing — so that number is largely the model
ranking broken designs against other broken designs, which no policy ever has
to do. `validation.json` now also reports Spearman over designs saturated at
**every** corner, and top-20 %-precision: of the designs the model puts in its
own top fifth, how many are really there. That is what a search actually
consumes from a model.

**The thresholds, the stability requirement and the fallback were written into
the plan before the run that decides them**, so they could not be chosen after
seeing the numbers.

### 18.6 Looking for the blind spot on purpose

A policy trained inside a model finds designs the *model* thinks are good. It
is the canonical failure of model-based RL and it is silent.

`scripts/check_surrogate.py` goes looking: CEM runs against the surrogate alone
(400 evaluations, about a second, no SPICE), and whatever it converges on is
simulated for real at the four robust corners. At 340 designs the answer is
reassuring — predicted reward **0.9982**, real **0.9676**, an optimism gap of
**+0.031**, and the ensemble disagrees *ten times less* at that point than on
typical collected designs, so the optimum sits inside the region the model
knows rather than in a hole.

The env additionally prices that disagreement into the reward
(`uncertainty_penalty`), so the policy is pushed towards regions the model
actually knows.

### 18.7 Speed, because it decided the design

`GoalCtleEnv.step` started at 223 ms. A million steps would have been 62 hours.

| ms/step | what was wrong |
|---|---|
| 223 | starting point |
| 4.9 | `_evaluate` called `predict_raw` twice — once for the ensemble spread, once inside `predict_metrics`; and `.eval()` ran on every member on every forward pass |
| 3.9 | `analytic.solve` searches a 300×300 (Rs, Cs) grid against a 900-point sweep. It depends only on (gm, Cout, target peaking, target f_peak) — and gm/Cout come from `FIXED_START` and never vary — so it is memoised on coarsely binned targets (0.5 dB, 100 MHz, far below the noise added to the start immediately afterwards) |

**57x in total**, and that is under contention with fourteen ngspice workers.
At 0.25 dB / 25 MHz bins the cache held nine entries after 400 steps and the
solve was still 40 % of the step budget — the bin width was the whole fix.

### 18.8 Why the first policy run stalled, and what the data said

The first PPO run flattened at **5 % held-out success** and stayed there from
51k steps to 205k. Mean best reward climbed the whole time (0.733 → 0.767 →
0.836 → 0.884 → 0.883) and the number of specs solved never moved off exactly
one out of twenty. Two separate causes, both found by measuring rather than by
tuning hyper-parameters.

**1. The policy started every episode on a plateau that does not exist.**

Per-goal analytic starts were simulated for real and compared against what the
surrogate said about the same designs:

| goal | surrogate | real SPICE |
|---|---|---|
| 0 | not saturated → reward **0.2000** | saturated → reward **0.5486** |
| 1 | not saturated → reward **0.2000** | saturated → reward **0.6796** |

`specs.score` clips reward to 0.2 for a design that is not biased in
saturation. The surrogate believed that of ~70 % of analytic starts (and
thought 82 % failed `equalises`), so most episodes opened on a flat 0.2 with no
gradient at all — from points the simulator scores between 0.55 and 0.68.

The cause is distribution shift, and it was avoidable in hindsight. The `seed`
cloud is drawn around the analytic start for the **default** spec. A
goal-conditioned policy starts from a **different** analytic design for every
spec it is handed, and nothing in the training data covered those points. The
sampling plan had been written against where the *previous* method operated,
not where the new one does.

An earlier hypothesis for the same symptom — that low-`f_peak` goals drive `RL`
up until `Itail·RL` collapses the CTLE output common mode — was checked and is
**wrong**: across twelve sampled goals the output CM sits at 0.87–1.06 V
against a floor near 0.70 V. The designs were fine; the model was not. Worth
recording because it is the more obvious explanation and it is false.

Fix: `collect_data.py --n-goal-starts` samples goals from the calibrated space,
derives each one's analytic start with no SPICE (PhysFit plus the fitted
calibration factors), perturbs it and simulates all four corners. The surrogate
is then trained on both tags.

**2. The policy was beginning to exploit the model.**

Ensemble disagreement at the evaluation points rose monotonically —
0.234 → 0.232 → 0.192 → 0.320 → 0.356 — while reward plateaued. That is the
canonical model-based RL failure: the agent finds where the model is
optimistic and lives there. The `uncertainty_penalty` exists for exactly this
and is raised on the final run, with `check_surrogate.py --evals 2000` used to
probe harder than the 400-evaluation pilot did.

**The general lesson, which is the one worth keeping:** a surrogate is valid
only where it has data, and *where it has data* is decided by the sampling
plan, not by the model or its validation score. G1 was computed on held-out
designs drawn from the same three clouds, so it could not see this at all — the
model was accurate on its own distribution and wrong on the policy's. Held-out
accuracy measured on the training distribution says nothing about a region that
distribution never visits.

### 18.9 Fixing the start: bias the circuit before shaping its response

The diagnosis in §18.8 was half right, and the half that was wrong is the more
interesting one.

Adding a per-cloud breakdown to the surrogate's validation (the metric that
would have caught the original failure, added after it) showed that only
**42 %** of per-goal analytic starts are saturated *in SPICE*. The plateau the
policy sat on was therefore largely **real**, not a model artifact — the
two-sample check in §18.8 had drawn two lucky designs. Both things were true at
once: the surrogate was miscalibrated there *and* the starts were genuinely bad.

Diagnosing from 110 collected goal-start records, **63 % of the unsaturated
ones fail because the tail is in triode** — `vds_tail` 0.168 V against
`vdsat_tail` 0.177 V, missing by 9 mV — where the saturated ones sit at 0.337 V
against 0.168 V.

**Two fitted relations, not intuition.** From 3 095 records:

```
vdsat_tail ~ Itail^+0.36 · Wtail^-0.42      R²(log) 0.909
vds_tail   ~ Itail^-0.44 · (W/L)^+0.51      R²(log) 0.740
```

Together they predict the *sign* of the tail's saturation margin correctly on
**89.3 %** of records. `PhysFit.tail_margin()` is those two, and it costs no
SPICE.

The second is the one that is easy to get backwards. The tail node sits at
`Vcm − Vgs_M1`, so asking for **more** tail current raises `Vgs` and pushes that
node **down** into the tail device's own `Vdsat`. More current makes the bias
*worse*. It shows in the data: designs biased in saturation carry 0.50 mA where
the unsaturated ones carry 0.66 mA.

`analytic_start_u` now widens the tail (lower `Vdsat` at no cost to `gm`) and
takes the **largest** current that still clears a 0.15 V margin — headroom
first, then as much `gm` as that allows.

| | before | after |
|---|---|---|
| predicted tail margin | +0.082 V | **+0.154 V** |
| starts surviving the env's N(0, 0.1) | ~44 % | **92 %** |
| verified in SPICE at all 4 corners | — | **4 / 4 saturated** |

with real rewards 0.4812, 0.5603, 0.7634, 0.8174 instead of the 0.2 the scorer
clips an unbiased design to.

**A misreading worth recording.** Binning the data first suggested a *narrower*
tail was better (70 % saturated at `Wtail` 5–50 against 61 % at 50–90). That was
confounded — the cloud perturbs all eleven knobs at once — and the isolated
regression gives `Wtail` a clean negative exponent on `vdsat_tail`. The
regression was the right instrument; the binning was not.

### 18.10 The cliff is shown to the policy, not smoothed away

Fixing the start was necessary and not sufficient. Measured from bias-aware
starts with a random policy: 6.7 % of *starts* have negative tail margin, but
**24.9 % of steps** do, with the 10th percentile at −0.064 V. A policy that
raises `Itail` for gain — the natural move for a peaking-limited goal — walks
straight off the 0.2 cliff.

Softening the gate inside the environment would make the landscape easier to
learn, but it would also have the policy optimise something the real scorer does
not, and that mismatch surfaces at deployment as an unexplained G2 → G3 drop.
So the **tail margin became an observation** instead: the policy can see the
cliff approaching rather than only feeling the reward collapse afterwards, and
the objective is untouched. `OBS_DIM` 34 → 35.

### 18.11 What the targeted collection bought

Collecting ~650 designs around the *bias-aware* starts, retrained on the same
25 % split:

| cloud | ρ before | ρ after | saturated real / predicted |
|---|---|---|---|
| goal_start | 0.519 | **0.823** | 0.42 / 0.34 → **0.63 / 0.63** |

The classifier is calibrated where the policy operates; it had been
under-predicting saturation there by eight points, which is what manufactured
the false plateau.

G1 over saturated held-out designs: **0.854** on 135 designs, against a gate of
0.9 fixed before the run. It does not pass. The gate stays where it was set —
the pre-commitment exists precisely so that a number seen afterwards cannot move
it. What the pre-commitment *does* govern is the fallback, whose trigger was
`spearman_saturated < 0.8 AND eye agreement < 0.75`; neither holds (0.854, and
eye 0.90 / 0.82), so the policy trains against the full reward including the eye
rather than taking the easier path.

### 18.12 Hypotheses that measurement killed

Kept because the discarded ones are as informative as the surviving one, and
each was plausible enough to have been acted on.

| hypothesis | why it was plausible | what the measurement said |
|---|---|---|
| The analytic start collapses the CTLE output common mode for low-`f_peak` goals | `RL` is solved from the `f_peak` target, and `Itail·RL` is a real headroom constraint | **False.** Output CM sits at 0.87–1.06 V across twelve sampled goals, against a floor near 0.70 V |
| The surrogate is simply wrong about saturation at the starts | Two starts simulated saturated where it predicted otherwise | **Half true.** Only 42 % of starts are genuinely saturated; the model was miscalibrated *and* the starts were bad |
| A narrower tail improves bias margin | Binned data showed 70 % vs 61 % | **False.** Confounded by eleven knobs moving together; the isolated fit gives `Wtail^-0.42` |
| The policy cannot reach a solution in 20 steps of 0.1 | "Give it more steps" is the obvious fix for non-convergence | **False.** The seeded CEM's whole journey is 0.288 on its largest knob and 0.399 in L2 — the budget is 7× that, and continuous actions give finer resolution than 0.1 |

### 18.13 An unplanned result: surrogate-assisted CEM

`check_surrogate.py` exists to look for the model's blind spot, not to design
anything. It runs CEM *inside* the surrogate and then simulates whatever it
converges on, so that a model which over-rates its own favourite design is
caught before a policy is trained in it.

At 1 500 designs it caught nothing, and that is the finding:

```
predicted reward (surrogate) : 0.9986
real reward (ngspice)        : 0.9977
optimism gap                 : +0.0009
ensemble disagreement there  : 0.033 against 3.707 typical
```

The model predicted its own optimum's reward to nine parts in ten thousand. At
340 designs the same probe gave +0.031 and a design that did not pass; the
goal-start data closed both.

**The design it found passes 8/8 brief specs at all 20 PVT corners**, and it is
better than the shipped checkpoint-3 design on six of eight margins:

| spec | shipped (CEM) | surrogate-assisted |
|---|---|---|
| **hd3 (binding)** | **+4.1 %** | **+10.1 %** |
| eye_h | +31.1 % | +59.9 % |
| fpeak | +6.9 % | +13.2 % |
| peaking | +32.1 % | +41.7 % |
| noise | +58.8 % | +70.6 % |
| power | +80.7 % | +82.0 % |
| eye_w | +40.6 % | +17.2 % |
| area | +99.4 % | +98.8 % |

The binding constraint has 2.5× the margin it had. In absolute terms: hd3
−36.2 dBc against −31.2, eye height 220.2 mV against 131.1, power 2.48 mW
against 2.89, eye openness improvement 2.38× against 1.97×.

**The cost, accounted honestly.** This is not "a better design in 1.9 seconds".
The surrogate cost **11 986 ngspice launches** to train, once. After that:

| | per new spec |
|---|---|
| seeded CEM (shipped) | 760 ngspice launches, ~7 min |
| surrogate-assisted CEM | 2 000 surrogate evaluations (1.9 s, no simulator) + ~44 launches to verify |

Break-even is at roughly **17 specs**. Below that the shipped flow is cheaper;
above it the surrogate is, and the margin grows without bound. That is the
amortisation argument for the whole approach, and it is the honest way to state
it — a one-off investment that only pays back when the tool is used repeatedly,
which is exactly the case the brief describes.

**What this is and is not.** It is a result for the *surrogate*, not for the
goal-conditioned policy. Surrogate-assisted CEM still runs a full search per
spec; it is simply running it against a model that costs microseconds instead
of a simulator that costs seconds. The policy's claim — one training run,
then any spec without search — is separate and is decided by G2 and G3.

So the project now has **three** methods to compare, not two:

1. **seeded CEM** — the shipped baseline, one search per spec against ngspice
2. **surrogate-assisted CEM** — one search per spec against the learned model
3. **goal-conditioned policy** — no search at all, trained once

One caveat recorded rather than buried: `L` sits on a box edge in this design.
A knob pinned at its bound means the optimiser wanted to go further and could
not, so the bound — not the circuit — is setting that dimension.

### 18.14 One condition was blocking everything

The retrained policy — better surrogate, bias-aware starts, the cliff visible in
the observation — still solved 5 % of held-out specs. Reward climbed to 0.88
and stopped, exactly as before.

Rather than tune, the question asked was *which* condition fails. Running the
trained checkpoint over the 20 held-out specs and recording the best step of
each episode:

```
conditions passed at best step, out of 10:
   9/10 : 5 specs      which condition blocks it:
   8/10 : 12 specs       fpeak    fails on 19/20 specs
   7/10 : 3 specs        peaking  fails on  8/20
                         power    fails on  7/20
                         eye_w    fails on  4/20
```

The policy was meeting eight or nine conditions on every spec and finishing
none. **`fpeak` alone accounted for it.**

**Why the start could not hit the band.** `analytic_start_u` solves for the
band centre and corrects the closed form by the fitted `k_f`. Two things make
that the wrong target:

1. The **environment scores with the surrogate**, which applies a *learned
   residual on top of the same closed form*. The equations-plus-`k_f` answer
   and the surrogate's answer are not the same number, so aiming with one and
   being graded by the other misses.
2. The target was the band centre **at the nominal corner**, while the slow
   corner sits about 20 % lower in `f_peak`. A design centred at `tt` puts `ss`
   under the floor — and `all_pass` is evaluated at every corner.

Measured open-loop: the start landed inside the band at all four corners on
**2 of 20** sampled goals. For spec 0 — band 1.29–2.48 GHz, target 1.89 — the
start delivered 1.59 GHz at `tt` and 1.33 GHz at `ss`.

**The fix** closes the calibration loop against the *surrogate*, the same way
`analytic.initial_design` closes it against SPICE and for the same reason,
except that here an iteration costs microseconds rather than five ngspice
launches. It centres the **corner spread** in the band rather than the nominal
value, and damps the update (`**0.7`) for the reason `CALIB_DAMPING` exists: an
undamped ratio update oscillates when the model's error is not proportional.

| | open loop | calibrated |
|---|---|---|
| f_peak in band at all 4 corners | 2/20 | **18/20** |
| mean conditions passed | 7.83 | 7.80 |

**Making it affordable.** A fresh calibrated start is several grid solves at
~175 ms each, which put `reset` at 706 ms against a 4 ms step — 1M steps would
have taken ten hours. Two changes, both measured:

* calibrated starts cached on coarsely binned goal targets. At 0.5 dB / 50 MHz
  the cache held 21 entries after 800 steps and missed almost every time; at
  1 dB / 150 MHz it warms in a few thousand episodes, and the binning error is
  far smaller than the `N(0, 0.1)` the start is perturbed by immediately after.
* intermediate iterates solve on a 40-point grid instead of 96. The solve cost
  goes as `n_grid²` and those iterates are stepping stones, not the answer.

1M steps: **10 h → 1.9 h**.

The 358k-step run in flight was discarded rather than resumed. It was
optimising against a start that could not satisfy the spec it was handed, so
its weights encode a different problem.

### 18.15 The environment was undoing its own start calibration

With `fpeak` targeted correctly the policy still solved nothing, and the
per-condition breakdown at the *start* rather than at the end explained why.

`GoalCtleEnv` perturbed every derived start by `N(0, 0.1)`. The calibrated start
puts f_peak in band at all four corners on 18 of 20 goals; the perturbation put
9 of those back out:

| start_noise | conditions at start | f_peak fails |
|---|---|---|
| **0.00** | **7.30/10** | **1/20** |
| 0.05 | 6.25/10 | 6/20 |
| 0.10 | 6.00/10 | 11/20 |

Deployment applies no noise, so the evaluation env now uses 0 — G2 had been
measured from starts the policy never meets in deployment *and* with the
calibration destroyed. Training dropped to 0.02; the diversity noise was there
to provide already comes from the uniform and seed start branches, half of all
episodes between them.

**This also explains the shape of the earlier failure.** From the perturbed
start the policy passed 6.10/10 and worked up to 8.25/10 — driving `eye_h`,
`equalises` and `bias` to zero failures — while `fpeak` went from 11 failures to
19. It was not ignoring f_peak. It was **trading it away**, because from a start
that had already lost the band, regaining it costs more than the other
conditions are worth. Start in band and there is nothing to trade.

### 18.16 The DFE tap was fighting the pulse response

Even from a clean calibrated start, `equalises` failed at **20 of 20** — and
that check asks only that the circuit make the eye no *worse* than it found it.
The scorer clips reward to 0.3 when it fails, so every episode still opened on
a plateau.

The tap exists to cancel the first post-cursor, so it should be `h[1]/h[0]`,
which `physics.py` already computes. Measured across sampled goals that ratio is
**negative**, −0.002 to −0.162, while `tap_pct` is bounded to `[1, 30]` and can
only be positive. **A positive tap against a negative post-cursor adds ISI
instead of removing it.** The data says so directly: at `TAP_PCT_START = 5` the
starts with the largest `|h[1]|` had eye improvement 0.685 and those with the
smallest had 1.035.

The tap is now taken as close to `h[1]/h[0]` as the bounds allow — for a
negative post-cursor, the floor.

| | before | after |
|---|---|---|
| conditions passed at start | 7.30/10 | **8.40/10** |
| median `tap_pct` | 5.00 % | **1.22 %** |
| median `eye_improvement` | < 1 | **1.279** |
| `equalises` failures | 20/20 | **7/20** |

**The corroboration is the part worth keeping.** The shipped checkpoint-3
design, found by 95 candidates of search against ngspice, sits at
`tap_pct = 1.07 %`. The pulse-response model arrives at **1.22 % with no
simulation at all** — an independent check on the physics in §18.3, from a
direction it was not built for.

`TAP_PCT_START = 5.0` carried a comment calling it "the measured optimum for a
1-tap DFE on this topology, and the same value the reference design settles
on". It was — *before this channel existed*. It has been quietly wrong since the
channel was added, and nothing flagged it because 5 % is a perfectly reasonable
tap for a different post-cursor sign.

What remains at the start is `eye_w` (11/20) and `eye_h` (9/20), which is the
right place for the remaining work to sit: the closed-form model has no eye,
and opening one against a channel is exactly what the learned policy is for.

### 18.17 The instruments were wrong, not just the policy

After the start was fixed three separate ways the policy still reported 0 %
success, which was too absolute to be plausible. Checking *what is reachable*
rather than accepting the number found that two of the measurements were
themselves wrong, both in the direction that made the policy look worse.

**The ceiling was a statement about the sampling plan, not the problem.** G2 had
been reported against "13 of 20 demonstrably feasible", a figure computed by
asking how many held-out specs an *already-collected design* happens to satisfy.
That measures which designs were collected. Running a real CEM search against
the surrogate — 3 000 evaluations, no simulator, a few seconds per spec — solves
**20 of 20**:

```
spec0  SOLVED  best reward 0.9976      spec10 SOLVED  best reward 0.9823
spec1  SOLVED  best reward 0.9936      ...
...                                    spec19 SOLVED  best reward 0.9874
```

So the goal space was never the constraint, the 80 % gate is not impossible by
construction, and any shortfall is the policy's own. The ceiling is now computed
by search at startup.

**The evaluation never looked at the start.** The starting design is *derived*,
not random, and on some specs it already satisfies everything — measured, the
calibrated start solves **3 of 10** outright, which the CEM search confirms by
moving *zero distance* from it. `rollout()` only inspected step results, so on
those specs the policy took its first action, stepped off a solution it was
already sitting on, and was scored unsolved.

They are now credited — and reported **separately**, as `solved_at_start`. A
spec the start already meets is a result for the analytic start and the
surrogate calibration, not for the policy; folding the two together would claim
work the policy did not do.

**Reachability was checked at the same time and is fine.** Distance from the
calibrated start to the searched solution, over ten specs:

```
median L2 0.485,  median largest-knob move 0.331
policy budget    20 steps x 0.1 = 2.0 per knob
specs needing a knob move > 1.0:  0 / 10
```

**The lesson.** Five training runs were discarded across §18.8–18.17, each after
a measured defect: a false plateau, an f_peak targeting error, start noise
undoing the calibration, a DFE tap of the wrong sign, and finally two broken
instruments. The last is the uncomfortable one — for several hours conclusions
were being drawn from a ceiling that measured the sampling plan and a rollout
that could not see its own starting state. What exposed it was not a better
idea but a refusal to accept 0 %: a number that absolute is usually an
instrument, not a result.

### 18.18 G2 and G3: the policy did not work, and why

**G2 — policy trained inside the surrogate. FAILED.**

Over 1 000 000 steps and 20 evaluations, the policy solved **0 of 20** held-out
specs. The reported 20 % success is entirely the *derived starting design*;
`solved_by_policy` is 0 at every single evaluation. That split was added to the
metric **before** the result was known, precisely so this could not hide behind
a headline number.

```
    51 200 steps   success 20%   at_start 4   by_policy 0   reward 0.941
   256 000 steps   success 20%   at_start 4   by_policy 0   reward 0.932
   460 800 steps   success 20%   at_start 4   by_policy 0   reward 0.950
   870 400 steps   success 20%   at_start 4   by_policy 0   reward 0.976
 1 001 472 steps   success 20%   at_start 4   by_policy 0   reward 0.952
```

Gate was 80 %. The ceiling is **20/20** — a real search against the same
surrogate solves every one of these specs — so this is not a feasibility
excuse. It learned to raise the dense reward (0.941 → 0.976) and never closed
the conjunction.

**Why.** There are 10 conditions and `all_pass` needs every one. Measured on the
trained policy: it drives `equalises` 8→0, `eye_h` 10→0, `eye_w` 12→1,
`bias` 1→0 — and takes `fpeak` from 5 failures to **20**. It is not ignoring
f_peak; it is **trading it away**, because the reward counts *how many*
conditions are met and not *which*. Losing one to gain five is a win on that
scoreboard and a total loss on the objective.

That is a reward-design problem, not a tuning problem, and it is the honest
finding: a count-based reward on an all-or-nothing conjunction gives an agent a
profitable move that never reaches the goal.

**G3 — deployment against real ngspice, six unseen specs, four methods.**

| method | solved at 20/20 PVT corners | SPICE evaluations |
|---|---|---|
| RL policy | 2/6 | 63 |
| CEM at equal budget (20) | 2/6 | 66 |
| **surrogate-assisted CEM** | **4/6** | **6** |
| CEM to convergence | 3/6 | 282 |

The policy's 2/6 is **not the policy**: both took 1 simulation, meaning the
starting design already passed and the policy changed nothing. Its genuine
contribution is 0/6, consistent with G2.

One small real result for it: on spec2 and spec3 the policy scores 0.87 where
equal-budget CEM scores **0.30** — the invalid-design floor. With only 20
simulations CEM wanders into designs that do not even bias correctly, while the
policy stays in sensible territory. It is better-behaved at tiny budgets. It
just never finishes.

### 18.19 The claim that did not survive its own audit

`check_surrogate.py` reported predicted 0.9986 against real 0.9977 — a gap of
+0.0009 — and that was written up as evidence the surrogate could be trusted to
pick designs. **It was n = 1.**

Run properly — 14 independent searches inside the surrogate, every winner
simulated for real:

```
model said "this passes":   14 / 14
SPICE agreed:                4 / 14
gap: median +0.018   mean +0.040   worst +0.173
```

The original +0.0009 is row 2 of that table: one of the four lucky draws. The
surrogate **is** systematically optimistic when a search optimises against it,
which is textbook model exploitation — every design the search rejects is one
the model rated lower, so the design it returns is selected for the model being
wrong in the flattering direction. An accurate model plus an optimiser gives a
biased answer; the optimiser is an error-finder.

What survives: the specific design is real and was verified at all 20 corners.
What does not: "the surrogate reliably picks good designs."

The fix is not a better model but a different workflow — propose several,
verify all of them (§18.20). Recorded because the overclaim was caught by
review, not by the author.

### 18.20 Propose-and-verify, measured

10 specs x 10 proposals, all 100 verified in real SPICE at the four robust
corners. Proposals ranked by the model's own predicted reward:

```
check top 1  ->  20%      check top 6  ->  60%
check top 2  ->  30%      check top 7  ->  80%
check top 4  ->  50%      check top 8  ->  90%   <- N = 8
check top 5  ->  60%      check top 10 ->  90%   (plateau)
```

Per-proposal pass rate is **18 %**. Eight proposals reach **90 % of specs** at
32 SPICE evaluations and ~23 s end to end, against 380 evaluations and ~7 min
for seeded CEM.

Three things that must travel with that number:

1. **The model's ranking is anti-correlated with reality**: Spearman(predicted,
   real) = **−0.33** over the 100 proposals, and first-success ranks were
   scattered `1, 1, 2, 3, 4, 5, 7, 7, 8`. Verify all eight in parallel; do not
   walk the model's ordering.
2. **It plateaus at 90 %, it does not climb.** One spec failed all ten
   proposals — the searches share a blind spot, so more proposals do not help.
   Those specs need the seeded-CEM fallback.
3. **Ensemble disagreement is too weak to filter on**: 0.0316 on designs that
   passed against 0.0373 on those that failed.

The 90 % is from ten specs. The true value could plausibly sit anywhere from
~75 % to ~95 %; thirty specs would tighten it and the run is affordable.

### 18.21 What this project concludes

| method | works? | cost per new spec |
|---|---|---|
| seeded CEM (checkpoints 1-4) | yes | 380 evaluations, ~7 min |
| **surrogate propose-and-verify** | **yes, 90 % of specs** | **32 evaluations, ~23 s** |
| goal-conditioned RL policy | **no** | — |

The RL approach failed and the infrastructure built to support it succeeded.
Both conclusions come from the same measurements, and reporting only the second
would be the dishonest version of this work.

The training cost is not an argument against the surrogate. A policy cannot be
trained against SPICE, so the surrogate is a prerequisite for the RL path as
well: both paths pay the same 11 986 launches, and only one of them then
delivers anything.

---

## 19. Checkpoint 6 — the objective was the bug

Checkpoint 5 shipped a surrogate that works (G1 rho = 0.884) and a policy that
does not (G2 = 0 of 20 held-out specs). §18.21 concluded that the search was
doing the work and the policy was not. This checkpoint tests a narrower claim:
that the failure was the OBJECTIVE the policy was trained against, not the fact
that it was a policy.

Two defects were diagnosed, both in the reward, neither in the algorithm.

**The reward was a count with a loophole.** `specs.score` mixes a weighted soft
score with `HARD_PASS_WEIGHT * n_pass / n`. Passing is CONJUNCTIVE -- every
condition must hold -- so an optimiser can raise that reward by improving five
conditions while driving a sixth negative, and be paid for it. That is not a
hypothesis about what the policy did; `tests/test_margin_vector.py` constructs
the trade and shows the old reward rising as the design gets worse.

**Terminating at the pass boundary trained the fragility in.** The episode
ended the moment the SURROGATE said `all_pass`, so the policy learned to stop
at min-margin ~ 0 -- which is exactly where model error decides the verdict.

### 19.1 The fix: one number with no trade in it

`specs.margin_vector(m, spec, tail_margin_v)` returns all ten pass conditions
as continuous margins, with the property

    all_pass(score(m, spec))  <==>  min(margin_vector(m, spec)) >= 0

so the only way to raise the minimum is to improve whatever is currently worst.
Checked, not asserted: over every record in the evaluation cache at this
checkpoint -- 5,993 real SPICE measurements, 171 of which pass -- the two
functions disagree on **0**. The test scans whatever the cache holds, so it
gets stricter as more is collected rather than going stale.

Ten, not eight. `margins()` covers the brief specs; `bias` and `equalises` are
INTERNAL checks that `score` GATES on (reward clipped to 0.2 and 0.3), and a
reward built on the other eight lets the policy trade those two away for free
-- the same loophole in a smaller room. `bias` comes in as `PhysFit.tail_margin`
in volts over `TAIL_MARGIN_SCALE_V`, which is a continuous signal the policy can
steer by in place of a boolean it can only fall off.

The equivalence is exact in the form the tests pin -- `bias` taken from
`score`'s own `saturated` flag. Substituting the fitted tail margin, as the
environment does, makes it approximate: the fit gets the sign right on 89.3 %
of records. That is the intended trade, and the rule it implies is simple:
anything GATING on pass/fail uses `all_pass`, anything OPTIMISING uses the
minimum.

**Not a softmin.** softmin(x) = -tau*log sum exp(-x_i/tau) tends to the MEAN for
any tau large enough to give the non-binding conditions a gradient -- which is
the count objective again, loophole included. The gradient is not needed in the
reward: all ten margins are already in the observation, so the policy can see
which one binds.

### 19.2 The mean-bonus reintroduced the loophole, and a test caught it

The first version of the reward was `min(margins) + 0.05 * mean(margins)`, with
the 0.05 described in the source as "deliberately too small to make a trade
profitable". It was not. A property test over 3,000 random margin vectors found
the counterexample immediately: nine conditions at 0.30 and one at -0.001
scores 0.0125, against 0.0105 for ten conditions at 0.01. The mean swings 0.27
while the minimum gives up 0.011, so 5 % of it is more than enough to buy the
violation.

This is worth recording because the comment was confident and wrong, and the
fixture-style test passed. What caught it was the randomised property test. The
fix makes the bonus conditional rather than small: it is paid only to designs
that already pass, over margins clipped into `[0, MARGIN_CAP]`. Breaking any
condition makes the minimum negative, which deletes the bonus entirely instead
of merely taxing it. Among designs that do pass, the bonus is bounded by
`MEAN_MARGIN_WEIGHT * MARGIN_CAP` = 0.00625 and can never push the minimum
below zero.

### 19.3 More margin is not monotonically better (G6-0)

The premise -- push predicted margin past zero and the design survives real
SPICE -- was tested BEFORE any training, because if it is false then no reward
built on the surrogate's margin can help and the honest answer is that model
error is the floor for every method built on this model.

`results/best_of_n.json` looked like the obvious source and could not be used:
its spec entries cannot be mapped back to the seed-999 stream. Two independent
attempts, both failed:

*By reward.* Every CEM-optimised proposal scores ~0.999 whatever spec it is
graded against -- the reward saturates at the boundary, which is the very
defect `HARD_PASS_WEIGHT` was added to paper over -- so the best-matching spec
beat the runner-up by a mean absolute **0.0003**, i.e. by noise.

*By margin.* Margin does not saturate, so this should have worked. Scoring each
entry's proposals against all ten specs and taking the argmax of the mean
predicted min-margin gives a mapping that claims **5 distinct specs for 10
entries** -- entries 0, 3, 4 and 7 all name spec 8 -- and is not the identity.
If each entry had been optimised against a distinct one of these ten specs,
that could not happen.

**The `predicted` column of `results/best_of_n.json` is not reproducible and
should not be quoted, and the artifact cannot serve as the baseline.** Its
`real_pass` column stands only in the weak sense that SPICE measured
*something*.

That matters more than bookkeeping, because re-measuring the same method
through the committed script gives a **much better** answer than the 18 %
per-proposal the artifact reports (see 19.5). The simplest explanation
consistent with both observations is that the historical run verified
proposals against a different spec from the one they were optimised for --
which would depress the pass rate and destroy the mapping in one stroke. That
is inference, not proof: the run cannot be reconstructed, which is exactly
why the measurement is now a committed script.

`scripts/margin_transfer.py` answers it without that file. Every cached record
carries REAL measured metrics, so the real margin vector is computable for ANY
spec; the surrogate gives the predicted one for the same design and spec. Take
held-out designs only -- the same 25 % split by design key the model was
validated on -- and cross them with goals from the calibrated space:

    223,800 (design, goal) pairs, 373 held-out designs
    spearman(predicted m_min, real m_min) = +0.821

Predicted margin tracks the real thing strongly. Note the contrast with
checkpoint 5's Spearman(predicted REWARD, real reward) = -0.33: the margin is
the well-behaved quantity and the clipped, bonus-mixed reward is not.

But the relationship is NOT monotone:

    predicted m_min bin       real pass rate
    [+0.00, +0.02)   n=100        32.0 %
    [+0.02, +0.05)   n=166        24.7 %
    [+0.05, +0.08)   n= 88        48.9 %
    [+0.08, +0.10)   n= 53        64.2 %
    [+0.10, +0.15)   n= 84        66.7 %   <- best supported bin
    [+0.15, +0.20)   n= 26        46.2 %
    [+0.20,   inf)   n= 10        40.0 %

It peaks around 0.08-0.15 and then FALLS. That turn is the same mechanism this
project keeps meeting: a very large predicted margin mostly means the model is
confidently wrong -- and here it appears in data no optimiser selected at all.

So the plan's "reward unbounded above, so the policy keeps buying margin" was
wrong, and would have walked the policy straight into the falling half; 20
steps of 0.04 is ample authority to reach 0.20. The reward is CAPPED at
`MARGIN_CAP = 0.125`, the midpoint of the best-supported bin, and is flat above
it. The constant is derived from this measurement, not chosen.

**G6-0: buying margin from 0 to +0.08 moves the real pass rate 42.1 % ->
61.3 %, +19.1 points against a +15 gate. PASS.**

Two caveats that belong next to that number and not in a footnote: these
designs were measured at the 4 robust corners, while deployment verifies at 20;
and they were NOT selected by an optimiser hunting for model error. 42 % is
therefore an upper bound on what a search that exploits the model achieves.
G6-0 says the buffer CAN work. Only G6-3 says whether it did.

### 19.4 The uncertainty penalty was in the wrong units

The first SAC run was launched with `--uncertainty-penalty` at its default of
0.1, and the first evaluation said plainly that it was not working:

    10 000 steps   success 24 % (6 of 25, ALL 6 solved at the start)
                   mean best m_min -0.2191   gain over start +0.0022
                   mean ensemble disagreement 0.263

Measured separately, the calibrated start sits at disagreement **0.018**. So
the policy was travelling fifteen times deeper into ground the ensemble does
not agree about, and coming back with **+0.002** of margin for the trip. That
is model exploitation, and it is the failure this project has met at every
turn.

It is also a failure that was already written down. HANDOVER 18 / the
checkpoint-5 plan recorded the same symptom during the PPO pilot --
disagreement climbing 0.19 -> 0.32 -> 0.36 while reward plateaued -- and the
recorded remedy was 0.3. The default was used instead. The measurement above
cost seven minutes of training; not reading the note cost the same again.

**The penalty is now derived rather than chosen**, the same way `MARGIN_CAP`
was. `scripts/margin_transfer.py` regresses the surrogate's margin error on its
own disagreement, over the same 223,800 held-out pairs:

    unc [0.015,0.030]  n=36 6xx   |error| 0.1840   signed +0.0730
    unc [0.030,0.040]           |error| 0.2098   signed +0.1379
    unc [0.040,0.057]           |error| 0.2714   signed +0.1554
    unc [0.057,0.080]           |error| 0.3526   signed +0.1271
    unc [0.080,0.219]           |error| 0.6149   signed -0.0193

    |predicted m_min - real m_min|  ~  5.27 * unc + 0.027

so `m_min - 5.27 * unc` is a **lower bound** on the margin rather than the
model's hope for it. `UNCERTAINTY_PENALTY = 5.27`.

Two things about that number are worth keeping.

**It must be the ABSOLUTE error.** The signed optimism in the right-hand column
is not monotone -- it rises to +0.155 and then turns negative in the top
quintile -- so a line fitted to it has a NEGATIVE slope and would pay the
policy to be uncertain. The absolute error is monotone and nearly linear, and
it is the honest quantity anyway: a margin the model cannot predict in either
direction is not margin anyone should be paid for.

**5.27 is not large; the units changed.** Checkpoint 5's reward was a score in
[0, 1] and 0.1 was sensible in it. This reward is a margin, capped at 0.125,
and a penalty has to be in the same currency as the thing it is subtracted
from. Comparing the two numbers directly is the mistake, not the number.

While fixing this, a second loophole of the same shape turned up in the
shaping term: `improve` was computed on the RAW margin while the reward
carried the penalty, so the policy could collect `0.5 * improve` for margin it
gained by walking somewhere the model was guessing. The penalty is now inside
both, and `tests/test_env_goal.py` pins it.

### 19.5 The reward was bounded above and not below, and it broke the critic

Two SAC runs failed before anyone looked at the critic. The evaluations said
the policy was doing nothing:

    10 000 steps   gain over start +0.0384   unc 0.193   0 specs solved by policy
    20 000 steps   gain over start +0.0056   unc 0.123   0 specs solved by policy

The obvious reading -- "it has learned to stand still" -- was wrong, and the
measurement that showed it was wrong is worth keeping, because the two natural
definitions of "how far did it go" disagree completely:

    travel to the BEST design it saw     median 0.000
    travel to the FINAL design           median 1.60
    a real search, from the same start    median 0.342

The policy was not frozen. It was **running away**: 1.6 in normalised design
space, four times further than a search needs to go, and everything it passed
on the way was worse than where it started -- so the best design it ever saw
remained the starting point, and travel-to-best read 0. Measuring only the
best-so-far hides a policy that is sprinting in the wrong direction.

Two more numbers finished the diagnosis:

    fraction of actions saturated at |a| = 1        91.5 %
    cosine(policy direction, search direction)     -0.22   (median -0.29)

Ninety-one per cent of actions slammed to a bound, in a direction ANTI-parallel
to the solution. That is not a policy that has learned the wrong thing; it is a
policy following a broken value function. And the value function was broken:

    |Q| at 10 000 steps      14.3
    |Q| at 20 000 steps   2 836.0        (a 20-step return should be about -6)

**The cause was in the reward, and it was mine.** `MARGIN_CAP` bounds the
reward ABOVE -- the measured pass-rate curve turns over, so paying for more
margin past 0.125 is paying for the model to be confidently wrong. Nothing
bounded it BELOW. Margins are fractional distances to a limit and have no floor
at all. Measured over 1 500 random steps:

    raw m_min       worst  -425.3
    step reward     worst  -515.8      median -1.26
    uncertainty     worst    17.2      (held-out designs span 0.015 - 0.219)

A 20-step return could therefore reach -10 000. No critic fits a target
spanning four orders of magnitude; it diverged, and the actor did what a
diverged critic told it to.

The fix is `MARGIN_FLOOR = -1.0` and `UNCERTAINTY_CLIP = 0.25`, giving a reward
in [-2.32, +0.13] and a return in [-46, +2.6]. Both bounds are chosen the same
way as the cap -- from what the numbers mean, not from tuning:

* -1.0 matches `specs.MARGIN_NAN_FLOOR`. A design missing a limit by more than
  100 % is hopeless, and so is one missing by 42 500 %. The policy starts from
  a calibrated design at mean m_min -0.22, so the flat region is the region it
  only reaches by running away -- exactly where a flat, maximally bad reward is
  the right message. Direction information is not lost: all ten margins stay in
  the observation, only the scalar reward is clipped.
* 0.25 is just past the top of the held-out uncertainty range. Beyond it the
  model is not more wrong, it is simply useless.

**Reward clipping is ordinary practice** -- DQN clips to [-1, 1] for this exact
reason. What is not ordinary is capping one end and leaving the other open,
which is what happens when the cap is added for a *different* reason (model
optimism) and no one asks what the other end does.

`tests/test_env_goal.py` now asserts the bound over 600 random steps and checks
that a -425 margin at uncertainty 17.2 still comes back at the floor.

### 19.6 What the uncertainty penalty is allowed to forbid

`UNCERTAINTY_PENALTY = 5.27` looks severe enough to freeze a policy in place,
so it was checked against the designs that actually work rather than argued
about:

    calibrated start                       unc 0.018   penalty 0.095
    CEM proposals that PASSED 20-corner    unc 0.029   penalty 0.153  (max 0.039)
    CEM proposals that failed              unc 0.032   penalty 0.167
    where the runaway policy went          unc 0.193   penalty 1.02

Moving from the start to where the working designs live costs **0.058** in
penalty against up to **0.345** of available margin. The penalty permits the
region that contains solutions and forbids only the region the policy was
running into. It is not what stopped the policy.

Note also that uncertainty does **not** separate pass from fail among proposals
(0.0290 against 0.0317). It is a distance-from-data signal, not a quality
signal, and checkpoint 5 measured the same thing (0.0316 against 0.0373). It is
useful for deciding where to *stand*, useless for deciding which proposal to
*ship*.

### 19.7 One metric, fixed in advance

This checkpoint reported the CEM baseline four different ways while the run was
still going -- per-proposal pass rate at four specs, then at six, then the
fraction of specs solved -- and the two quantities moved in OPPOSITE directions
against checkpoint 5:

    per-proposal pass rate      18 %  ->  35 %   (better)
    specs solved at K = 8       90 %  ->  67 %   (worse)

Quoting whichever one was to hand made the same run look like good news and
then bad news within an hour. That is a reporting failure, not a measurement
one, and it is the reason the headline is now a single fixed sentence emitted
by `scripts/best_of_n.py::_summarise`:

    it takes n = ? to have a 90 % chance of solving the spec

"Solving" means one of the n proposals meets every brief spec at all 20 PVT
corners. Per-proposal pass rate and proposal diversity are still recorded --
diversity is a validity gate, since n near-identical proposals are worth one --
but they are diagnostics, not the answer.

**A partial run is not an answer either.** The number at four specs was
n = 2; at six it was already not reached within eight. Intermediate values of a
running measurement should not be reported as results at all.

### 19.8 Three specs needed more than eight proposals, and they are not
infeasible

At n = 8, three of the ten specs had nothing that passed. Before spending
simulation on more proposals, the obvious alternative was checked: are those
specs simply impossible for this topology? Against the 1 497 collected designs
that carry real SPICE measurements at all four robust corners:

    spec5   1 real design satisfies it       spec7   4 real designs
    spec8   1 real design                    (spec1, spec6: none found)

So specs 5, 7 and 8 have demonstrated solutions and the search had not yet
found them -- a sampling question, not a feasibility one. `best_of_n.py`
grew `--extend --only-unsolved`, which continues the seed sequence and adds
proposals to unsolved specs while keeping every verification already paid for
(each is ~20 SPICE evaluations, and they dominate the cost). Specs already
solved are skipped: more proposals cannot lower a `first_success_rank` that is
already set.

Note the caveat on the feasibility check: it is measured at the 4 robust
corners, while deployment verifies at 20. It shows a spec is not hopeless; it
does not promise the design survives the full grid.

### 19.9 The surrogate-CEM baseline, measured

**It takes n = more than 32 proposals to have a 90 % chance of solving a spec.
The method does not reach 90 % at all: it plateaus at 80 %.**

    n =  1   ->  50 % of specs solved
    n =  2   ->  60 %
    n =  5   ->  70 %
    n = 20   ->  80 %
    n = 32   ->  80 %   (no further gain)

Ten seed-999 specs, proposals from CEM inside the frozen surrogate, every
proposal verified at all 20 PVT corners, `results/best_of_n_surrogate_cem.json`.
"Solved" means one of the n proposals meets every brief spec at every corner.

Per spec, the rank at which it was first solved:

    spec0  n=1    spec3  n=1    spec6  n=1    spec9  n=5
    spec1  n=1    spec4  n=1    spec7  never (0 of 32)
    spec2  n=2    spec5  never (0 of 32)      spec8  n=20

**The plateau is a SEARCH failure, not a feasibility limit** -- at least half of
it. Before accepting 80 % as a ceiling, the best design in the collected data
was found for each unsolved spec and verified at all 20 corners:

    spec7   best collected design PASSES 8/8 at 20 corners, binding fpeak +4.5 %
    spec5   best collected design FAILS by eye_h -0.1 %

So a design that satisfies spec7 everywhere **already exists in this project's
own data**, and 32 CEM proposals did not find it. spec5 is genuinely marginal
and cannot be called feasible or infeasible from what has been collected.

The honest reading: the reachable target is 9 of 10, and the shipped method is
leaving one of them on the table. Nothing here says 80 % is the best this
circuit can do.

**On comparing this to checkpoint 5's "8 proposals reach 90 %":** don't. That
artifact is not reproducible and its spec labels cannot be recovered (19.3).
The two runs disagree in opposite directions on the two available metrics --
per-proposal pass rate went UP (18 % -> 35 %) while specs solved went DOWN
(90 % -> 70 % at n=8) -- which is precisely why only one metric is reported now
and why it is fixed before the run rather than chosen after it.

### 19.10 The policy, measured against the same bar

**It takes n = more than 32 proposals to have a 90 % chance of solving a spec.
The policy plateaus at 80 %, the same coverage as CEM, and reaches it at n = 7
where CEM needs n = 20.**

    n = 1   ->  50 % of specs        n = 5   ->  70 %
    n = 2   ->  60 %                 n = 7   ->  80 %
    n = 3   ->  70 %                 n = 32  ->  80 %  (no further gain)

Head to head, identical script, identical seed-999 specs, identical 32-proposal
budget on unsolved specs, every proposal verified at all 20 PVT corners:

    method        specs solved      per proposal   diversity
    SAC policy    80 % at n = 7        35.2 %        0.148
    CEM           80 % at n = 20       17.8 %        0.324

The policy reaches the same coverage for about a third of the simulation,
because each proposal is twice as likely to survive verification. Proposal cost
is not close: 7 designs in **1 second** from a forward pass, against 42 seconds
of search for CEM's 20 -- and CEM pays that again for every new spec while the
policy's training is spent once.

**Gate results, as pre-committed:**

    G6-0  margin transfers to SPICE    +19.1 pts, rho +0.821     PASS
    G6-1  in-surrogate margin          +0.198, gain +0.386       PASS
    G6-2  n-for-90 % beats CEM         neither reaches 90 %      PARTIAL
                                       (80 % at n=7 vs n=20)
    G6-3  per-proposal pass rate       35.2 % vs 17.8 %          PASS
    G6-4  proposal diversity           0.148 vs 0.25 target      **FAIL**

**G6-4 failed and is reported as failed.** SAC's entropy did not produce the
spread that best-of-n assumes; the eight proposals are near-copies. The policy
survives it only because each individual proposal is so much better. The fix is
identified and not yet done: each rollout visits 20 designs, so eight rollouts
produce 160 candidates of which only 8 are proposed -- selecting for spread
subject to a margin threshold costs nothing extra.

**The two methods fail on DIFFERENT specs**, which is the most useful result
here:

    CEM unsolved     spec5, spec7
    policy unsolved  spec5, spec8
    union solved     9 of 10 = 90 %

The policy solves spec7, which CEM never found in 32 proposals and for which a
passing design demonstrably exists in the collected data. CEM solves spec8,
which the policy never found. Running both and keeping whichever passes reaches
the 90 % target today, with no new code.

Only spec5 defeats both, and it may be infeasible: the best design among 5 993
collected records misses it by eye_h **-0.1 %**.

### 19.11 Both methods are optimising corners they are not graded on

Found while writing this up, and it reframes the 80 % plateau.

The proposal objective uses `pdk.robust_search_corners()`; acceptance uses
`pdk.pvt_grid("spec")`. They are not the same set, and barely overlap:

    tt_1.800V_27C        optimised for, NOT in the acceptance grid
    tt_ll_1.800V_27C     optimised for, NOT in the acceptance grid
    ss_1.710V_125C       optimised for, in the grid
    ff_1.890V_0C         optimised for, in the grid

Half the corners being optimised are never graded, and **18 of the 20 corners
that decide pass/fail are never seen** -- not by the search, not by the policy,
and not by the surrogate in training, which was fitted on the four robust
corners only.

Every method in this project is therefore solving a measurably different
problem from the one it is scored on. That is the most plausible single cause
of two methods with quite different search behaviour plateauing at exactly the
same 80 %, and neither checkpoint 5 nor checkpoint 6 tested it.

It is also cheap to act on, because the fix is already paid for: the best-of-n
runs wrote **5 600 new records covering all 20 corners**, nearly doubling the
5 993-record training set with precisely the near-feasible, full-grid data the
surrogate has never had. The order is: measure whether failing proposals pass
on the two shared corners; if they do, retrain the surrogate on the full grid
and propose against the acceptance corners.

Recorded before doing it, so the prediction is on the record rather than
fitted afterwards.

---

## 20. Checkpoint 7 — eye width is not the surrogate's to predict

Checkpoint 6 left both methods plateaued at 80 % of specs, and the cause was
not ambiguous: **eye width is the binding failure on 81-92 % of every proposal
that fails 20-corner verification** (policy 92 %, CEM 81 %). It is also the
metric the surrogate is worst at -- held-out R² 0.57 and pass/fail agreement
82 %, against 0.90/0.97 for peaking and power.

The obvious move was to predict it better. Three attempts, **all measured
before any training, all failed**, and the whole exercise cost about twenty
minutes because the cheap test came first.

### 20.1 The phase-sweep feature (+0.001)

The diagnosis was structural and looked airtight. `dfe.measure_eye` defines eye
width as *the fraction of the symbol period over which the eye is open* -- it
evaluates the opening at 64 sampling phases and takes the longest contiguous
positive run. Every eye feature the surrogate had was sampled at ONE phase, the
cursor: `pulse_taps` takes `i0 + d·n_ui`, and `openness_est`/`eye_est` are built
from those. The model was being asked to predict a phase sweep from features
containing no phase information at all -- which is exactly why eye HEIGHT, which
does have a single-phase scaffold, reaches 0.80 while width sits at 0.57.

So the sweep was built (`PULSE_OVERSAMPLE` is already 32 samples per UI, so the
phases needed no interpolation) and checked against 5 993 measured records:

    eye_width_est alone                          R²  0.001   spearman 0.118
    added to the 7 existing eye features        +0.001

Nothing. The reason is visible in the distributions: the hand model says the
eye is open across **78 %** of the symbol where SPICE measures **33 %**. The
linear pulse cascade has no slicer, no latch and no feedback loop in it, and
those are what close a real eye.

**A note on the kill-switch that caught this.** The plan's stated bar was "the
new feature must beat the trained model's R² 0.57 on its own, with no network".
That bar was badly calibrated -- a single raw feature beating a 5-member
ensemble over 32 features would be remarkable, and almost nothing would pass it.
The bar that matters is the second line above: does it add anything to what is
already there. It gave the same verdict, so the conclusion stands, but the
lesson is that a kill-switch has to test the right quantity or it will
eventually kill something good.

### 20.2 Two standard fixes for a bimodal target, also nothing

Measured eye width is strongly bimodal -- a mode of closed eyes near 0.15, a
second of open eyes near 0.82, and only 23 % of designs in the 0.35-0.65 band
between them. That is a classic reason regression underperforms, so both
standard remedies were tried:

    predict width directly (linear, all 32 features)   R²  0.295
    predict logit(width), invert                       R²  0.292
    classify open/closed                               76.7 % vs 65.8 % base

The trained network already reaches 0.565, above all of these. It is extracting
close to what these features contain, and the features do not contain the eye.

### 20.3 The conclusion, and why it is not a consolation prize

Eye width is **not** physically out of reach -- 47 % of every design ever
simulated clears the easiest value the goal space asks for. It is simply not
predictable from hand physics. Every other spec has a closed form the network
corrects; the eye has a comparator in it, and there is no equation for a
comparator.

So it is measured instead, and two further measurements make that cheap.

**The worst corner for eye width is one nothing looks at.** Over 280 designs
measured at the full 20-corner grid:

    sf_1.710V_0C   worst 47.5 %     screen 1 corner  -> true 20-corner min 66.8 %
    sf_1.890V_0C   worst 22.9 %     screen 2 corners -> 89.6 %, mean err +0.008 UI
    ff_1.890V_0C   worst 13.2 %     screen 3 corners -> 91.4 %  (not worth the cost)

**And the surrogate cannot represent that corner.** The acceptance grid spans
five processes (tt, ss, ff, sf, fs); `physics.FEATURE_NAMES` carries
`proc_tt`, `proc_ss`, `proc_ff` and nothing else, so `sf` and `fs` both encode
as all-zeros -- indistinguishable from each other and from nothing. The corner
that decides most eye-width failures is one the model has no word for, and was
never trained on.

`scripts/best_of_n.py` now screens each proposal at those two `sf` corners and
sends only the survivors to the full 20-corner verify:

    checkpoint 6   27 proposals x 20 corners      540 evaluations   ~10 min
    checkpoint 7   screen 8 x 2, verify ~2 x 20    56 evaluations   ~1.5 min

The screen is a FILTER, not a verdict: at 89.6 % it will sometimes pass a design
the full grid rejects, which is what the full grid is for. Every such design is
recorded (`screen_missed`) so the screen's own error rate is measured rather
than assumed.

### 20.4 A silent data-loading bug, found on the way

`cache.load` matched exact filenames only, so `--tag collect,bon` looked for
`bon.jsonl`, did not find it, **skipped it without a word**, and would have
trained on half the requested data while reporting success. The checkpoint-6
runs write one file per spec (`bon_policy_spec0` ... `bon_surrogate_cem_spec9`),
so the retraining step planned for this checkpoint would have silently used
**zero** of those 5 600 new records.

Tags now also match prefixes, and a tag matching no file raises with the list of
what is available. Silence is the wrong answer to "give me data that does not
exist".
