# PCIe Gen2 Equalizer — RL Sizing Framework · Handover

**Project root:** `~/pcie_eq_rl` (WSL native ext4 — *not* `/mnt/c`, see §2)
**Last updated:** 2026-09-13
**Status:** Two-stage equalizer (CTLE → DFE summing stage) with a lossy channel,
sized end-to-end by the RL loop. **All 9 specs pass at all 20 of the brief's PVT
corners** in 189 candidates (§16). LLM wrapper and schematic export **not built**
(§0b).

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
| **fewer search points than a sweep** | ⚠️ **unmeasured.** 189 candidates vs 5¹¹ = 48.8 M for a 5-point grid, but no baseline (e.g. random search at equal budget) has been run. **This is the biggest remaining gap — the Problem Statement asks for it explicitly.** |
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
python3 -m pytest tests/ -q                              # 145 tests
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
│   ├── test_pure.py         111 tests, no PDK needed, ~5 s
│   └── test_spice.py        34 tests against real ngspice
├── scripts/
│   ├── run_design.py        CLI entry point
│   ├── build_trimlib.py     build + VALIDATE trimmed model libraries
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
intervention". Measured: box centre scores 0.522 (5/9 specs), this scores 0.999
(9/9).

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
| `Metrics.eye_improvement` | openness at the slicer ÷ openness at the equaliser input. **>1 means real equalisation**; measured 1.93×, and 0.15 with the degeneration removed |
| `Metrics.eye_height_in_v` / `eye_height_ctle_v` | the eye before the equaliser and after stage 1, so a gain-only solution is visible |
| `Metrics.out_cm_v` | summing-node common mode, so drift from the mid-supply heuristic is measured rather than assumed (§4) |
| `specs.margins()` / `binding_spec()` | fractional margin to the **nearest** limit, and the spec closest to failing. Two-sided specs must be quoted from the binding edge |
| `specs.BRIEF_SPECS` / `INTERNAL_CHECKS` | 8 customer requirements vs the 1 internal saturation check |
| `Simulator.tran_hd3(channel=False)` / `.noise(channel=False)` | both default to channel-free: linearity and input-referred noise are properties of the EQUALISER (§10 bugs 22–23) |

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
| Peaking | 8.21 dB @ 1.682 GHz | 3–12 dB, 1.25–2.5 GHz | pass |
| DC gain | -4.59 dB | — | gain @ 2.5 GHz = +3.16 dB |
| Power | 3.14 mW | < 15 mW | pass — **excludes the ideal comparator and latch** |
| HD3 | -63.4 dBc | < −30 dBc | pass, measured at **full drive** (§10 bug 1b) |
| Input noise | 0.545 mVrms | < 1.5 mVrms | pass, **both stages** counted (§10 bug 1a) |
| Area | 0.000327 mm² | < 0.05 mm² | pass |
| Eye | 189.0 mV × 0.547 UI | > 100 mV, > 0.4 UI | at **200 mV pp** drive |
| **Eye openness** | **0.349 of swing, 1.93× better than at the input** | > 1 | see below |
| Realised `gmF/gm1` | 0.0097 (`tap_pct` = 1.38 %) | — | §16 |
| Summing-node CM | 1.450 V of 1.80 V | — | mid-supply heuristic wants 0.900 V (§4) |

### Eye openness is the number that shows equalisation

Eye **height** can be bought with summing-stage gain — measured across the 20
corners it correlates with that gain at **0.982** and with peaking at only
**0.468**, because `Rsum`/`Wg1`/`Ig1` are free variables. **Openness** (opening
as a fraction of the swing at the same node) is invariant under scaling, so
`eye_improvement` = openness at the slicer ÷ openness at the equaliser input
measures equalisation rather than amplification.

Measured: **1.93×**, and it collapses to **0.15** when the
degeneration capacitor is removed. Doubling the summing-stage gain moves eye
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

### Corrections to earlier claims in this file