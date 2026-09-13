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

A lossy **FR4-like channel** sits in front of it (−11.1 dB at Nyquist). Without
one the specification set is incoherent: a 1-tap DFE with no post-cursor to
cancel can only make the eye worse, which was measured (§10b).

Everything in the signal path is a real PDK device. The slicer and the 1 UI
latch are deliberately **ideal** XSPICE code models — as they are in the
reference design, which labels them "(Ideal)" too.

```bash
cd ~/pcie_eq_rl
python3 -m pytest tests/ -q                              # 108 tests
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
│   ├── test_pure.py         no PDK needed, < 1 s
│   └── test_spice.py        against real ngspice, ~4 min
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

The RL-sized design (§16), at the typical corner, **with the channel present**:

| Metric | Value | Spec | |
|---|---|---|---|
| Peaking | 8.21 dB @ 1.682 GHz | 3–12 dB, 1.25–2.5 GHz | pass |
| DC gain | −4.59 dB | — | (gain @ 2.5 GHz = +3.16 dB) |
| Power | 3.14 mW | < 15 mW | pass |
| HD3 | −76.0 dBc | < −30 dBc | pass |
| Input noise | 0.515 mVrms | < 1.5 mVrms | pass |
| Area | 0.000327 mm² | < 0.05 mm² | pass |
| Eye | 189.0 mV × 0.547 UI | > 100 mV, > 0.4 UI | pass |
| Realised `gmF/gm1` | 0.0097 (`tap_pct` = 1.38 %) | — | see §16 |

`CtleParams()` defaults are a sane starting point in their own right and pass
9/9 at typical with the channel on. The search does not start from them — it
starts from `analytic.py` (§5).

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
| DFE tap OFF | **0/20** | 191 – 263 mV |

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
| **ladder + frequency-dependent series R** | −11.1 dB | **10.2 dB** | ✅ what is built |

Flat loss creates no ISI. **Dispersion is the whole point.**

### The model as built (`netlist.channel_netlist`)

40 sections per leg, 1.2 ns total delay (~7 inch of FR4), `Z0` = 50 Ω per leg,
each section carrying `Rdc` + two staggered `R‖L` skin-effect stages + `L`, with
shunt `C`. 50 Ω series at the source, **100 Ω differential** termination at the
receiver. Measured:

```
0.1 GHz   -0.93 dB        2.5 GHz  -11.10 dB   <- Nyquist, typical Gen2 is -10..-15
1.0 GHz   -4.95 dB        5.0 GHz  -16.56 dB
equalizable tilt 0.1 -> 2.5 GHz = 10.2 dB, inside the brief's 3-12 dB boost window
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

| Item | Notes |
|---|---|
| **`netlists/*.sch` are STALE** | They describe the OLD merged topology. They are a cross-check only — never simulated — but they no longer match `netlist.py`, so the device-for-device diff in VERIFY.md §3 will not agree until they are regenerated. |
| **No schematic export** | The brief asks for "the final schematic"; we emit `.sp` netlists. xschem's `.sch` is a text format, so generating one from `full_netlist()` is tractable and unbuilt. |
| **"Tunable" has no mechanism** | The brief says *tunable 1.25–2.5 GHz* and *variable Rs, Cs*. `Cs` is a design-time constant; a switchable cap bank with a control word is not built, and nothing tests tunability (§0). |
| **No sweep-vs-RL baseline** | The Problem Statement asks for "significantly lower time than sweeping". 189 candidates vs 5¹¹ = 48.8 M for a 5-point grid is suggestive, but no fair baseline (random search at equal budget) has been run. **Biggest remaining gap.** |
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
- **Sweep/random-search baseline** — the headline claim is unmeasured (§11).
- **Transistor-level slicer and latch** — behavioural by design.
- **Candidate batching** — one deck per candidate; ~16× still available (§9).

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
| `pvt_sweep.{json,csv}` | the same design swept by a **different code path** — an independent confirmation |

`pvt_sweep.{json,csv}` was regenerated for the final design with the channel on
and reports **0 of 20 corners failing**, matching `report.json`'s own
verification. The two share `Simulator.evaluate()` but not the search or the
reporting path, so agreement is worth something. Regenerate with:

```bash
python3 scripts/pvt_sweep.py --grid spec --eye --channel --set tap_pct=1.37903 ...
```

(the full `--set` list is in the git log for this file's commit).

No remote is configured. `git remote add origin <url>` when you have one.

## 13. Recipes

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b

# tests: 108 fast (no PDK) + 27 against real ngspice
python3 -m pytest tests/ -q -m "not needs_pdk"
python3 -m pytest tests/ -q

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

1. **Measure RL vs a sweep.** The Problem Statement asks for *"significantly
   lower time than sweeping all MOS, R, C, L parameter space"* and there is no
   measurement. Run random search at an equal evaluation budget (189
   candidates) and report both. This is the headline claim and it is currently
   unsupported. Cheap now: ~10 minutes.
2. **Export a schematic.** The deliverable says *"outputs the final
   schematic"*; we emit netlists, and `netlists/*.sch` are stale (§11). xschem's
   format is text, so generating one from `full_netlist()` is tractable.
3. **Batch candidates into one deck** (§9) — ~16× and the only large speed win
   left, now that parsing is no longer dominant.
4. **Then** the runtime cap bank for "tunable" (§0), a transistor-level slicer,
   and the LLM wrapper. All three are credibility investments rather than
   coverage gaps.

Housekeeping: no conda `environment.yml`; `results/pvt_sweep.*` is stale (§12b).

---

## 15. Test suite

`pytest`, in two tiers, because an ngspice launch costs ~8 s of PDK model
parsing no matter how small the circuit — so anything provable without a
simulator should be.

```bash
python3 -m pytest tests/ -q -m "not needs_pdk"   # 108 tests, < 1 s, no PDK
python3 -m pytest tests/ -q                      # + 15 ngspice tests, ~3 min
```

| tier | file | needs | covers |
|---|---|---|---|
| pure | `tests/test_pure.py` | nothing | value↔geometry round trips, `denorm`/`norm`, corner→`.lib` section names, PRBS, eye metrics, the scorer's NaN and gating behaviour, netlist text invariants, the Gymnasium env |
| spice | `tests/test_spice.py` | PDK + ngspice | device-model conventions (`m=` vs `mult=`, `W` total vs per-finger), every analysis, the tap polarity, the combined deck's equivalence, trimmed-library equivalence, `evaluate()` end to end |

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

**Command** (~25 min on 16 cores):
```bash
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel \
        --pop 48 --iters 15 --seed 0
```

### Result: all 9 specs pass at all 20 of the brief's PVT corners

With the lossy channel in front and the DFE active.

| spec | worst case over 20 corners | limit | |
|---|---|---|---|
| HF peaking | 7.33 dB | 3–12 dB | pass |
| `f_peak` | 1.887 GHz | 1.25–2.5 GHz | pass |
| HD3 | −72.2 dBc | < −30 dBc | pass, 42 dB margin |
| input noise | 0.607 mVrms | < 1.5 mVrms | pass, 2.5× |
| power | 3.41 mW | < 15 mW | pass, 4.4× |
| area | 0.000327 mm² | < 0.05 mm² | pass, 150× |
| eye height | 126.8 mV | > 100 mV | pass, 27% |
| eye width | 0.484 UI | > 0.4 UI | pass, 21% |

0 corners unscoreable. Worst search corner: `ff_1.890V_0C`.

### Sizing found

```
W=24.27um  L=0.2000um  RL=782.9ohm  Rs=2208.7ohm  Cs=257.8fF
Itail=635.0uA  Wtail=97.93um  Rsum=1732.9ohm  Wg1=42.94um  Ig1=407.0uA
tap_pct=1.379%
```

### Cost

| | |
|---|---|
| candidates simulated | **189** |
| (candidate × corner) evaluations | 756 |
| ngspice launches, search | 2268 |
| ngspice launches, 20-corner verify | 60 |
| convergence | `[0.977, 0.988, 0.988, 0.998]`, stopped early at iteration **4 of 15** |

For the brief's "fewer search points" framing: a 5-point grid over 11 variables
is 5¹¹ = **48.8 million** designs. But see §11 — a *fair* baseline (random
search at equal budget) has not been run, so quote the 189 and the caveat
together.

### What made it work — three fixes, each found by a run that failed

The first three attempts all failed, and each failure was diagnostic:

| run | result | what it showed |
|---|---|---|
| unseeded, centre of the box | never found the basin in 15 rounds | 11-D is too big to start blind |
| seeded, no injection | 0.765 at round 1 | `mu` was never evaluated — seeding alone does nothing (§10 bug 19) |
| seeded + injected | **0.986** at round 1, then stalled at 0.998 | the reward saturated at the spec boundary (§10 bug 20) |
| **+ hard-pass bonus** | **crossed at round 4**, 9/9 | `f_peak` 2.52 → 1.89 GHz |

Round 2 of the final run sat at exactly the pathological case — `f_peak`
2.52 GHz, failing by **20 MHz**. Under the old reward that scored 0.9995 and
looked converged.

### The DFE result, stated plainly

**`tap_pct` converged to 1.38 %** — essentially the bottom of its 1–30 % range.
The optimiser kept the mandated 1-tap DFE but turned it down as far as allowed.

That is consistent with everything measured: a manual tap sweep on this design
peaked at 5 % with **+0.8 %** eye height and degraded past 10 %; the reference
design's own sweep has the same shape; and the reference concludes *"the system
may not need to use DFE (or higher-tap DFE) if CTLE can give an enough eye
opening."* At 5 Gb/s with 11 dB of channel loss and 8 dB of CTLE boost, one tap
has little left to cancel. It would matter at 12 Gb/s.

The DFE is present and functioning — not disabled — but it is not earning much,
and that should be said rather than dressed up.

### Caveats on this result

- `ad`/`as`/`pd`/`ps` are 0, so every `f_peak` is optimistic (§11).
- The slicer and latch are ideal with 20 ps clk→Q; the loop-delay budget is
  asserted, not proven.
- Verified on the brief's **20** corners. The R/C axis (60 corners) is a
  project addition and was not re-run for this design.

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
