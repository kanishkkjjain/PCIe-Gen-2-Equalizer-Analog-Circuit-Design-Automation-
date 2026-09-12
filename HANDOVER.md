# PCIe Gen2 Equalizer — RL Sizing Framework · Handover

**Project root:** `~/pcie_eq_rl` (WSL native ext4 — *not* `/mnt/c`, see §2)
**Last updated:** 2026-09-12
**Status:** CTLE + summer + 1-tap DFE built and verified in SPICE.
Channel model, RL loop end-to-end, and LLM wrapper **not yet built**.

---

## 1. TL;DR — what exists and what it does

A Python framework that takes target equalizer specs, drives **ngspice** with a
**SKY130** netlist it generates itself, measures the result, and scores it. The
RL agent (Cross-Entropy Method) is written and wired but **has never been run
end-to-end**.

The analog design is a **1-stage source-degenerated CTLE** merged with a
**current-summing 1-tap DFE**, built entirely from real PDK devices (no ideal
R, C, or current sources anywhere in the signal path).

```bash
cd ~/pcie_eq_rl
python3 scripts/smoke_test.py                     # SPICE layer sanity
python3 scripts/plot_eye.py --itap 10e-6 -o /tmp/eye.png
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
print(Simulator('/tmp/w').evaluate(CtleParams(), pdk.typical_corner(), do_eye=True))"
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
├── README.md                short project intro (69 lines)
├── eqrl/                    the framework package
│   ├── __init__.py          package docstring only (14)
│   ├── pdk.py               PDK discovery + PVT corner model (136)
│   ├── netlist.py           parametric SPICE netlist generation (344)
│   ├── spice.py             ngspice driver + all analyses + parsers (452)
│   ├── dfe.py               PRBS, eye extraction, peak-distortion (168)
│   ├── specs.py             target specs + reward scorer (156)
│   ├── env.py               Gymnasium env + Objective wrapper (153)
│   ├── design.py            top-level orchestrator (175)
│   └── agents/
│       ├── __init__.py      (1)
│       └── cem.py           Cross-Entropy Method policy search (101)
├── VERIFY.md                how to reproduce every claim yourself
├── netlists/
│   ├── eq_tb.sch            FULL equalizer, DFE loop OPEN (op/ac/noise)
│   ├── eq_dfe_tb.sch        FULL equalizer, DFE loop CLOSED (eye/transient)
│   └── ctle_tb.sch          older CTLE-only schematic (superseded)
├── scripts/
│   ├── run_design.py        CLI entry point (51)
│   ├── smoke_test.py        SPICE-layer sanity checks (38)
│   ├── plot_wave.py         plot any wrdata dump to PNG (112)
│   ├── plot_eye.py          render the closed-loop eye diagram (45)
│   └── pvt_sweep.py         parallel full-PVT sweep of every spec, with timing
├── configs/                 (empty — reserved)
└── results/                 (empty — run_design.py writes here)
```

Scratch/simulation dirs used at runtime (not in the repo): `/tmp/eqrl_*`.

---

## 4. Circuit architecture

```
          VDD
      RLn ─┴─ RLp                 shared load resistors (real poly)
   voutn ──┴── voutp              summing nodes
     │ │       │ │
     │ └─ X3  X4 ┘ │              DFE tap pair, tail Xtf  (current switch)
     └─ X1     X2 ┘               CTLE input pair, LVT
        └ sp ─Rs‖Cs─ sn ┘         degeneration: real poly R + real MIM cap
          │         │
        Xt1       Xt2             real NMOS tail mirror (gate = vbias)
```

- **Merged current summer**: the DFE tap pair shares RLp/RLn with the CTLE
  instead of being a separate gain stage (what real PCIe RXs do; saves power
  and area). Fully steered, the tap moves the output by `Itap · RL` — that
  product *is* the tap weight.
- **Tail**: 4:1 NMOS current mirror (`MIRROR_RATIO`), `Iref` sourced from VDD
  into a diode-connected `Xref`. An ideal reference current is a legitimate
  abstraction (a bandgap lives off-block); the *mirror devices* are real.
- **Decision path**: behavioural via XSPICE — `adc_bridge` (slicer) →
  `d_dff` (1 UI latch) → two `dac_bridge` (differential tap drive). A
  transistor-level StrongARM closing 200 ps clock-to-Q is a separate project
  and is not what the RL sizes.
- **`CL` stays ideal on purpose** — it models the *next* stage's input
  capacitance, not a component we build.

**Feedback logic levels are 0.8 / 1.4 V, not 0 / 1.8 V.** A rail-to-rail swing
has a 0.9 V common mode, which would leave the tap tail's source at
0.9 − 0.77 = 0.13 V and push it into triode — the same trap the input tail hit
(§7). 0.8/1.4 keeps CM at 1.10 V while still giving 600 mV of differential
drive, far more than enough to fully steer the pair.

---

### The two schematics — they are not interchangeable

| file | DFE loop | tap gates driven by | use for |
|---|---|---|---|
| `eq_tb.sch` | **open** | static 1.1 V DC sources | `op`, `ac`, `noise` — forward-path characterisation |
| `eq_dfe_tb.sch` | **closed** | XSPICE slicer → 1 UI latch → DAC | eye / transient |

Both contain the same analog core (12 real devices). The **slicer and latch are
behavioural code models, not transistors** — in `eq_dfe_tb.sch` they live in a
labelled `DFE_LOOP` text block, because xschem has no stock symbols for
`adc_bridge`/`d_dff`/`dac_bridge`. `Mtf`'s width sets `Itap`
(`wfb_tail = Wtail·Itap/Itail`): W=30 → 300 µA, W=5 → 50 µA, W=1 → 10 µA.

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

Measured (Itap=50 µA design): worst `f_peak` is **2.517 GHz on `spec`** but
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
| `PARAM_BOUNDS` | the 9 design variables and their ranges |
| `LOG_PARAMS` | which are searched on a log scale |
| `NFET` / `RES` / `CAP` | device model names — **`NFET` is the single source of truth** |
| `res_geometry(R) -> (l, mult)` | resistance → geometry (measured mapping) |
| `cap_side(C) -> s` | capacitance → MIM square side |
| `CtleParams` | the design point (dataclass) |
| `.core_netlist()` | emits the device section |
| `.geometry()` | resolved geometry actually instantiated |
| `.area_um2()` | area breakdown |
| `.tap_weight_v()` | `Itap · RL` |
| `dfe_static_bias()` | hold tap pair at CM (forward-path characterisation) |
| `dfe_static_tap(±1)` | fully steer the tap (to *measure* tap weight) |
| `dfe_decision_path()` | the XSPICE closed feedback loop |
| `denorm()` / `norm()` | [0,1]ⁿ ↔ physical values |

### `eqrl/spice.py` — ngspice driver
`Simulator(workdir, ngspice_bin="ngspice", timeout=60)`

| Method | Returns |
|---|---|
| `.ac()` | dc/peak gain, peaking, `f_peak`, gain@2.5 GHz, BW |
| `.op()` | input-pair *and* tail bias, saturation flags, **measured** `i(Vdd)` |
| `.tap_weight()` | realised tap `ΔV` vs ideal, switching efficiency |
| `.tran_hd3()` | HD3 in dBc from a 100 MHz sine |
| `.tran_eye()` | **closed-loop** PRBS through the real DFE loop → `Eye` |
| `.noise()` | integrated input-referred noise |
| `.evaluate(do_hd3, do_noise, do_eye)` | everything → `Metrics` |

`_assemble(..., dfe=)` selects the tap drive: `None`/`"cm"`, `"pos"`, `"neg"`,
`"closed"`. **Omitting it floats `vfbp`/`vfbn` and every analysis fails.**

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
python3 scripts/pvt_sweep.py --grid spec --eye --set Itap=50e-6
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

## 7. Design variables (9 dims)

| Param | Range | Role |
|---|---|---|
| `W` | 1–100 µm | input pair width |
| `L` | 0.15–1.0 µm | input pair length (raises `ro`, costs gm) |
| `RL` | 100–5000 Ω | load resistor (log) |
| `Rs` | 50–10000 Ω | degeneration R (log) — sets the zero |
| `Cs` | 10 fF–3 pF | degeneration C (log) — sets the zero |
| `Itail` | 50 µA–2 mA | per-side tail current (log) |
| `Wtail` | 5–200 µm | tail device width — **headroom vs area** |
| `Itap` | 10 µA–1 mA | DFE tap current (log); tap weight = `Itap·RL` |
| `Wfb` | 1–50 µm | tap pair width (must fully switch) |

Fixed: `CL = 40 fF`, `Vcm = 1.10 V`, `L_TAIL = 0.5 µm`, `L_FB = 0.15 µm`,
`MIRROR_RATIO = 4`.

**`Vcm = 1.10 V` is not arbitrary.** At the original 0.9 V the real tail sat in
**triode** (`Vds` 152 mV < `Vdsat` 164 mV) and delivered **477 µA instead of
600 µA** — a 20% current error that the ideal current sink had completely
hidden. At 1.10 V: `Vds` 311 mV vs `Vdsat` 164 mV, 147 mV of margin.

**Headroom is now a real constraint**:
`VDD(1.8) ≥ RL·(Itail + Itap/2) + Vds(input pair) + Vds(tail)`

---

## 8. Current design state (defaults, tt corner)

| Metric | Value | Spec | |
|---|---|---|---|
| Peaking | 9.03 dB | 3–12 dB | pass |
| **f_peak** | **2.517 GHz** | ≤2.5 GHz | **FAIL even at typical** |
| Power | 2.77 mW (measured `i(Vdd)` = 1.5375 mA) | <15 mW | pass |
| HD3 | −66.3 dBc | <−30 dBc | pass |
| Noise | 0.502 mVrms | <1.5 mVrms | pass |
| Area | 259.4 µm² = 0.000259 mm² | <0.05 mm² | pass (0.52%) |
| DC gain | −0.12 dB | — | (peak gain 8.91 dB) |

*(CTLE-only, before the DFE pair was added, these were 2.26 mW / 0.390 mVrms /
238 µm² — don't mix the two sets.)*
| Tap weight | 643.8 mV measured / 720 mV ideal | — | 89.4% efficiency |

### PVT — full results (60 corners, every metric, closed-loop eye)

Run with `scripts/pvt_sweep.py`. **Two grids, and the difference matters:**

| grid | corners | failing | worst `f_peak` |
|---|---|---|---|
| `spec` (the brief's literal cross-product) | 20 | 4 | **2.517 GHz** (0.7 % over) |
| `full` (+ R/C axis) | 60 | 18 | **2.991 GHz** (20 % over) |

Reporting only the spec grid **understates the worst case by 19 %**. See §5
(`pdk.py`) for why sky130 makes this a trap.

**The tap current dominates everything else.** Same design, one parameter:

| spec | `Itap`=300 µA (default) | **`Itap`=50 µA** |
|---|---|---|
| eye width | **60/60 FAIL** | **0/60** |
| eye height | 43/60 FAIL | **0/60** |
| f_peak | 26/60 FAIL | 18/60 FAIL |
| area, bias, gain, hd3, noise, peaking, power | 0/60 | 0/60 |
| **corners with ≥1 failure** | **60/60** | **18/60** |

Worst-case margins at `Itap`=50 µA across all 60 corners:
```
f_peak  2.991 GHz vs 2.5   <- THE ONLY VIOLATION
eye H   170.8 mV  vs 100   ok      eye W  0.688 UI vs 0.4  ok
noise   0.493 mV  vs 1.5   ok      power  2.52 mW  vs 15   ok
```

**Conclusion: this topology meets 9 of 10 specs at every corner.** `f_peak` is
the single binding constraint — one dimension for the optimiser, needing
2.99 → 2.5 GHz. That is a well-posed problem.

### Peaking IS tunable across the required band (measured)
The brief asks for "3–12 dB **tunable 1.25–2.5 GHz**". Rs sets the boost
(`1+gm·Rs/2`); Cs sets the zero (`1/(Rs·Cs)`). So **Cs is the tuning knob** —
with Rs fixed at 1500 Ω:

| Cs | f_peak | peaking | in spec |
|---|---|---|---|
| 500 fF | 2.00 GHz | 10.34 dB | yes |
| 800 fF | 1.59 GHz | 11.20 dB | yes |
| 1200 fF | 1.26 GHz | 11.73 dB | yes |

Scaling Rs **and** Cs together does NOT work — it moves the peak but drives the
boost out of the 3–12 dB window (14.9 dB at 1.68 GHz, 2.95 dB at 3.17 GHz).

*Caveat:* this demonstrates different **designs** peak at different frequencies.
A literal reading of "tunable" implies a **runtime** mechanism — a switchable
MIM-cap bank with NMOS switches and a control word. That is **not built**, and
the switch on-resistance would sit in series with Cs and shift the zero.

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

### NO speed optimisations are currently in use

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
4. **DFE feedback polarity was inverted.** `dq` drove `vfbp`, but M3's drain is
   `voutn`, so `vfbp` high drives `(voutp-voutn)` *positive* — feeding the
   previous decision straight through therefore **reinforced** the post-cursor
   instead of cancelling it. `dqb` must drive `vfbp`. Measured at Itap=150 µA:
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

## 10b. Channel — investigated, NOT built, and NOT required by the brief

**Re-read the brief: it never mentions a channel.** No insertion loss, no trace
or via model, no S-parameters, and no statement of *where* the eye is measured.
Every spec line is measurable on the equalizer standalone, and all of them are.
**A channel unlocks no new spec.** Earlier notes in this file claiming the
channel is essential were engineering judgment stated as fact — corrected here.

The real argument for one: the brief **mandates a 1-Tap DFE**, and with no
channel the optimal tap is ≈0 (measured — see the Itap table in §8). A DFE the
optimiser drives to zero is hard to defend.

### What was measured about building one
| approach | result |
|---|---|
| ngspice `LTRA` (`O` element) | **−0.16 dB** at 2.5 GHz — its `r` is frequency-independent, so a matched line gives negligible loss |
| naive lumped RLGC ladder | **−302 dB** — badly parameterised, just a big RC low-pass |
| correctly-sized ladder (40 sections, R=4.32 Ω, L=1.25 nH, C=0.5 pF) | −15.3 dB at 2.5 GHz but only **2.2 dB of tilt** across 1–5 GHz — a flat attenuator, useless: no dispersion to equalise |
| **ladder + frequency-dependent series R** (two staggered `R∥L` stages, approximating skin effect) | **−1.75 / −13.18 / −25.27 / −33.08 dB** at 0.1 / 1 / 2.5 / 5 GHz — **23.5 dB of tilt**, a realistic shape |

The last one works. Note −25 dB at Nyquist is *aggressive* for Gen2 (typical
−10 to −15 dB); it is tunable by scaling the resistances.

### Three things that would need solving
- **Common mode.** The CTLE needs `Vcm = 1.10 V` at its gates. The ladder passes
  DC, but source/termination resistors would drag the CM to ground — terminate
  to `Vcm` or AC-couple with a bias network.
- **Cost.** 40 sections × 6 elements × 2 legs ≈ **480 extra elements**. Must be
  measured before it goes anywhere near the RL inner loop.
- **Accuracy vs speed.** Fewer sections is faster, but the ladder's own LC
  cutoff then pollutes the band.

### Recommendation
Build it as an **optional stimulus, off by default**. Spec-sheet numbers stay
measured standalone as the brief literally asks; a with-channel demo then shows
the DFE earning its place. Both stories available, and the channel never has to
sit in the inner loop.

## 11. Known issues / open items

| Item | Notes |
|---|---|
| **Trimmed `.lib` not integrated** | Validated bit-identical (13.2× faster) but still only proven at one operating point on `tt`. Needs several W/L points and a file per corner before adoption. Failure mode is **loud** (`could not find a valid modelname`), never silently wrong. |
| **`ad`/`as`/`pd`/`ps` pinned to 0** | Junction/diffusion parasitics are zero in both the Python netlist and the schematic, so output capacitance is **understated** and `f_peak` is optimistic. xschem wanted to emit `expr('...')` forms that ngspice would not evaluate. |
| **`Itap` lower bound is numerically fragile** | At 10 µA the noise analysis returns `-nan`. The scorer treats it as a failure (correct) but the boundary is *numerical*, not physical. |
| **`Itap` default is badly wrong** | The `CtleParams` default of 300 µA fails the eye at **60/60** corners; 50 µA passes at **0/60**. The default has not been changed — the optimiser should choose it, but be aware any run starting from defaults starts from a known-bad point. |
| **No speed optimisation is switched on** | ~147× available (§9). The trimmed `.lib` is validated but lives only in a code comment. |
| **Slicer/latch are behavioural** | `adc_bridge`/`d_dff`/`dac_bridge`, not transistors. Their assumed 20 ps clock-to-Q is optimistic vs a real StrongARM's 50–150 ps, so the loop-delay budget (<200 ps UI) is **not** proven. Eye numbers are optimistic by an unquantified amount. |
| **"Tunable" has no mechanism** | Cs is a design-time constant; a runtime switchable cap bank is not built (§8). |
| **`gain_2g5_min_db` is invented** | It is in `EqualizerSpec` but **not in the brief**. Delete it or flag it — it should not be mistaken for a requirement. |
| **Noise referral is approximate** | Referred to `Vinp` alone, which carries only half the differential drive — may be off by ~2×. |
| **HD3 test amplitude is an assumption** | Hardcoded `tran_amp = 0.1` (100 mV diff). The spec doesn't state an amplitude; the 37 dB margin partly reflects a gentle test. |
| **Eye plot crossings not phase-centred** | Cosmetic only; the metric picks the best phase independently. |
| ~~`ctle_tb.sch` is CTLE-only~~ | **Resolved**: `netlists/eq_tb.sch` is the full equalizer, netlist-verified against `core_netlist()` device-for-device, and simulates to within ~1% of the framework. `ctle_tb.sch` retained for reference. |
| **No `environment.yml` / conda** | Hackathon conformance gap (§2). |
| **No git repo** | `git init` has not been run. |

---

## 12. What is NOT built

- **Lossy channel model** — the big one. Without it we are equalising a flat
  wire, the first post-cursor is ≈0, and the DFE tap has nothing to cancel.
  Every peaking/eye number is aimed at a target that doesn't physically exist.
- **RL loop end-to-end** — `cem.optimize()` and `CtleEnv` exist and import, but
  **have never been run** against the simulator.
- **`eye_from_pulse_response` not wired in** — written, unused (and possibly
  unnecessary, see §9).
- ~~Full 60-corner PVT~~ — **done**. Both `spec` (20) and `full` (60) grids run
  with every metric including the closed-loop eye. See §8.
- **LLM natural-language wrapper** — the bonus deliverable.
- **Baseline comparison** — the headline "fewer evaluations than a sweep" claim
  has no supporting measurement yet.

**Also worth knowing:** the specs barely bind on the CTLE alone (7× power
margin, 37 dB HD3 margin, 3× noise margin). Only `f_peak`, area-vs-cap-size, and
the eye genuinely trade. A search over an almost-entirely-feasible space would
prove nothing, so the constraints need to actually bite before any benchmark is
quoted. Real passives + the DFE pair have already pushed `f_peak` into failure,
which is progress in the right direction.

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

**`results/pvt_sweep.{json,csv}` ARE tracked** — deliberately. They are the
60-corner PVT evidence (104 KB) and are worth keeping under version control even
though they are generated. Delete them from tracking if the repo is ever pushed
somewhere size-sensitive.

**Not tracked and not backed up:** everything in `/tmp/eqrl_*` (the actual SPICE
decks each run produces) and `~/.xschem/simulations/`. Those are regenerated by
`smoke_test.py` / `pvt_sweep.py` / xschem `Shift+N`, so losing them is cheap —
but be aware `/tmp` is wiped on reboot.

No remote is configured. `git remote add origin <url>` when you have one.

## 13. Recipes

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b

# sanity-check the SPICE layer
python3 scripts/smoke_test.py

# evaluate the default design at one corner
python3 -c "import sys;sys.path.insert(0,'.');from eqrl import pdk,specs as sp;\
from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
m=Simulator('/tmp/w').evaluate(CtleParams(),pdk.typical_corner(),do_eye=True);\
print(sp.score(m,sp.EqualizerSpec())['pass'])"

# eye diagram
python3 scripts/plot_eye.py --itap 10e-6 -o /tmp/eye.png

# plot any wrdata dump (auto-detects AC vs transient)
python3 scripts/plot_wave.py /tmp/w/ac.dat --open

# version control
git log --oneline
git status --short

# full PVT sweep (parallel, with timing)
python3 scripts/pvt_sweep.py --grid spec --eye --set Itap=50e-6   # 20 corners
python3 scripts/pvt_sweep.py --grid full --eye                    # 60 corners

# schematics  (press f to zoom-to-fit; the DFE tap pair sits far right)
xschem netlists/eq_tb.sch            # loop OPEN  - op/ac/noise
xschem netlists/eq_dfe_tb.sch        # loop CLOSED - Shift+S runs the real DFE

# netlist the schematic headlessly and diff against the Python netlist
xschem -x -r -s --command 'xschem netlist; exit' netlists/ctle_tb.sch </dev/null
diff <(sed -n '/subckt/,/ends/p' ~/.xschem/simulations/ctle_tb.spice) \
     <(python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;print(CtleParams().core_netlist())")

# interactive SPICE with waveform window (needs .spiceinit in cwd)
cp $PDK_ROOT/sky130A/libs.tech/ngspice/spinit .spiceinit
ngspice /tmp/w/ac.sp        # then: plot vdb(voutp,voutn) xlog
```

**xschem CLI flags that matter**: `-x` = no GUI (`-b` is `--detach`, which
suppresses *all* output and looks like a silent hang), `-r` = no readline
(needed when redirecting stdio), `-s` = SPICE netlist, `-o`/`-N` = output
path/name. Netlists default to `~/.xschem/simulations/`.

---

## 14. Recommended next step

Ranked by value per effort:

1. **Run the RL loop.** It has never executed, and it is *the* deliverable. The
   problem is now well-posed: 9 of 10 specs pass at all 60 corners and `f_peak`
   is the single binding constraint (2.99 → 2.5 GHz needed).
2. **Switch on the trimmed `.lib`** (§9). ~147× is available; validate across
   several W/L points and generate one file per corner first. Failure mode is
   loud (`could not find a valid modelname`), never silently wrong.
3. **Fix the `Itap` default**, or at least document that runs starting from
   `CtleParams()` defaults start from a known-bad point (60/60 eye failures).
4. **Then** decide on the channel (§10b) and the transistor-level slicer — both
   are credibility investments, not coverage gaps.

Housekeeping still outstanding: no `environment.yml` and no Linux conda install
(the Anaconda conformance gap, §2). Git is now initialised (§12b).
