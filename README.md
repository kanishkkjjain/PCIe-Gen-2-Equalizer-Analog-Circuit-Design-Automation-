# PCIe Gen2 Equalizer — automated analog sizing with Reinforcement Learning

Give it a target specification. It sizes a SKY130 receive equalizer, verifies
the result in **ngspice at 20 PVT corners**, and hands back the sized netlist
and the measured specs. **About 40 seconds**, no human in the loop.

**Use case:** PCIe Gen2 — 5 Gb/s NRZ, Nyquist 2.5 GHz. A 1-stage CTLE with
source degeneration feeding a DFE summing stage, across a lossy FR4-like
channel.

```
spec ──► derive a start ──► propose 8 designs ──► screen the eye ──► verify ──► sized netlist
         physics, 0.2 s     RL agent, 0.26 s      SPICE, 2 corners   SPICE, 20 corners
```

> **The learned model only ever proposes. SPICE decides.**
> `all_pass` is never set from the model's opinion — it becomes true only after
> all 20 corners were actually simulated and every condition passed.

---

## Result

**Coverage** — ten specifications drawn at random from the agent's trained
range, none seen during training, every candidate verified at all 20 corners:

| method | specifications solved | per proposal |
|---|---|---|
| RL agent, 8 candidates | **8 of 10** | **56.2 %** (45/80) |
| search alone, 8 candidates | 8 of 10 | 32.5 % |
| both together | **9 of 10** | — |

**Time** — median **36.9 s**, range 28.2–101.7 s over 16 measured runs.
About 97 % of that is ngspice; everything else takes under a second.

**Against a sweep**, which is what the brief asks us to beat — random search at
the same candidate budget, same simulator, same corners, same scorer:

| | best reward | designs passing |
|---|---|---|
| random search, 95 candidates | 0.726 | **0 of 95** |
| this framework | 0.987 | a passing design |

At the measured **4.42 s per design**, an exhaustive 5-point grid over 11
variables is 48.8 M designs — **6.8 years**. A 3-point grid is 217 hours.

**A verified design.** All 8 brief specs pass at all 20 PVT corners, with the
channel present and the DFE loop closed. (The scorecard shows 10 — the other
two are internal checks: devices in saturation, and *does it actually
equalise*.) Worst case over every corner:

```
peaking 5.887 dB   vs 3-12        f_peak 1.781 GHz vs 1.25-2.5    eye H 131.1 mV  vs >100
hd3    -31.24 dBc  vs <-30        noise  0.6173 mV vs <1.5        eye W 0.5625 UI vs >0.4
power   2.894 mW   vs <15         area   0.0003085 mm2 vs <0.05
```

**Tightest margin: `hd3`, only 1.24 dB.** That is the honest number — HD3 is
measured at the **slicer**, across both stages. Measured after stage 1 only, the
same design reads −55.2 dBc and appears to have 25 dB of margin it does not
have. The summing stage's Gm1 is an undegenerated pair, so it is the more
distorting half and the one the slicer sees.

**It equalises rather than merely amplifies** — 1.97× eye-openness improvement
over the equaliser input, 1.53× worst case. Eye height alone cannot show this,
because summing-stage gain is a free variable; openness is invariant under it.

Eye measured at **200 mV pp** transmitter drive — conservative against a real
Gen2 transmitter's ~800 mV, so it understates rather than flatters.

## Status

| | |
|---|---|
| Tests | **327** — 282 with no PDK (~40 s), 45 against real ngspice |
| Verification | all 20 brief corners simulated for every reported pass |
| Trained policy | committed (`models/policy/ckpt_120000.zip`) |
| Fast evaluator | committed (`models/surrogate/`) |
| LLM wrapper | 3 MCP tools, see [`MCP.md`](MCP.md) |

---

## How it works

Four components, built once and then reused for every request.

**1 · The starting point** is *derived*, per specification, with no hand
tuning — pole placement, then gm/ID sizing off a threshold voltage measured
once in SPICE. 0.2 s. Seven of ten specifications are solved by the very first
proposal made from it.

**2 · PhysFit** is a fast stand-in for the simulator: the closed-form circuit
equations **multiplied by a bounded learned correction**. The network never
predicts a circuit from scratch, and the correction is capped at ×7.4 — before
training it is exactly 1.0, so the model *starts* as the hand calculation and
cannot drift away from physics.

```
ngspice   4,420 ms per design            PhysFit   0.77 ms       ≈ 5,700× faster
```

That ratio is what makes everything else affordable. Training the agent took
**50 minutes**; with ngspice in the loop it would have been **6.1 days** of
simulation alone.

**3 · The RL agent** is a goal-conditioned Soft Actor-Critic. The specification
is part of its input, so one trained agent serves any spec in range. It emits
all 11 device values at once and refines them over 20 small steps. Its reward
is the **worst** of the ten conditions at the worst corner — never the average,
because a design is only as good as its weakest spec. It is deliberately
stochastic, so eight rollouts from one start give eight genuinely different
candidates.

**4 · Verification** decides. Candidates are screened cheaply at two corners
(measured to recover the true 20-corner worst case 89.6 % of the time), and
only survivors pay for the full grid. Margins are reported as measured; nothing
is rounded toward passing.

Requests outside the agent's trained range are routed to a cross-entropy search
instead, and the answer says so.

## Quickstart

```bash
pip install -r requirements.txt           # add --break-system-packages on Debian/Ubuntu
export PDK_ROOT=/path/to/sky130

python3 -m pytest tests/ -q -m "not needs_pdk"   # 282 tests, ~40 s, no PDK needed
python3 -m pytest tests/ -q                      # all 327, ~4 min, needs ngspice + PDK
```

Design one circuit end to end:

```bash
python3 scripts/run_design.py
```

Or drive it from a language model — the brief's bonus deliverable:

```bash
pip install -r requirements-mcp.txt
python3 scripts/run_mcp_server.py        # --self-test to check it registers
```

Three tools: `check_spec` (instant, no simulation), `design_equalizer`
(~40 s, real SPICE), `explain_capabilities`. Every answer states which method
ran and whether the request was inside the trained range. See [`MCP.md`](MCP.md).

**Not installable with pip:** ngspice 42 (needs XSPICE and KLU), xschem 3.4.4,
and the SKY130 PDK via volare. [`HANDOVER.md`](HANDOVER.md) §2 has the versions.

## Layout

```
eqrl/
  netlist.py    CTLE, DFE summer, channel, decision path, geometry
  spice.py      ngspice driver: combined deck, fatal-error scan, trimmed libs
  physics.py    closed forms + the fitted device model (PhysFit)
  surrogate.py  the grey-box network: physics x a bounded correction
  env_goal.py   goal-conditioned RL environment — the spec is an input
  start.py      which starting-point derivation is in force
  specs.py      the ten scored conditions and the reward
  mcp_server.py the language-model wrapper
scripts/        training, measurement, design, figures, the MCP server
tests/          327 tests; they check the claims, not only the code
netlists/       eq_core.sch is current and checked against the simulated netlist
results/        every measurement this project's documents quote
models/         the trained policy and the fast evaluator
docs/           the reports, the figures, and the findings
```

## Going deeper

| document | what is in it |
|---|---|
| [`docs/Team_Sillycon_Reference.docx`](docs/Team_Sillycon_Reference.docx) | the detailed reference report — every claim with the artifact that backs it, the ten measurement specs, the design procedure |
| [`MCP.md`](MCP.md) | the language-model wrapper: setup, tools, what it refuses to do |
| [`VERIFY.md`](VERIFY.md) | how to reproduce every claim here yourself |
| [`HANDOVER.md`](HANDOVER.md) | the full engineering record — the problem statement verbatim, every bug found and fixed, and an explicit list of what is *not* built |
| [`docs/findings-ctle-dfe.md`](docs/findings-ctle-dfe.md) | what the CTLE and the DFE tap are actually doing, measured |
| [`docs/systematic-starting-point.md`](docs/systematic-starting-point.md) | the starting-point derivation and its measurement |

## Design variables

Eleven, and they are the framework's entire output. Everything else about the
circuit is fixed.

```
CTLE     W  L  Itail  Wtail  RL  Rs  Cs
summer   Rsum  Wg1  Ig1
DFE      tap_pct          (the tap's current, width and tail all derive from it)
```

Bounds are in `eqrl/netlist.py::PARAM_BOUNDS`.

## Credit

The topology follows the open reference design
[muhammadaldacher/SERDES-Design-of-RX-Decision-Feedback-Equalizer](https://github.com/muhammadaldacher/SERDES-Design-of-RX-Decision-Feedback-Equalizer)
— a 1-tap DFE for NRZ over FR4. Three things were taken from its report: the
tap scaling convention, measuring the pulse response at the channel output, and
putting the summing stage's output common mode at mid-supply.

Built for the **AI/ML for Analog Circuit Design** problem statement by
**Team Sillycon** — Kanishk Jain and Karthik Raj Palleboyina, BITS Pilani,
Pilani Campus.
