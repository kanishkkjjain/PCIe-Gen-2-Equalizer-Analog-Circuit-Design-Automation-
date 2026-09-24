# Connecting the MCP server

The brief's bonus deliverable: talk to the design framework in plain language.

> *"I need about 8 dB of peaking, peaking somewhere between 1.4 and 2.2 GHz,
> under 10 mW."*

and get back sized devices, a 20-corner scorecard, an eye diagram, the AC
response, and a netlist you can run yourself.

## Install

```bash
pip install -r requirements.txt
pip install -r requirements-mcp.txt        # the MCP SDK, optional extra
export PDK_ROOT=/path/to/sky130
python3 scripts/run_mcp_server.py --self-test
```

The self-test registers the tools and answers a spec query without a host and
without a simulator. A server that starts but exposes no tools looks identical
to a working one from the outside, which is what this catches.

## Point a host at it

Claude Desktop — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pcie-equalizer": {
      "command": "python3",
      "args": ["/absolute/path/to/scripts/run_mcp_server.py"],
      "env": { "PDK_ROOT": "/absolute/path/to/sky130" }
    }
  }
}
```

Claude Code:

```bash
claude mcp add pcie-equalizer -e PDK_ROOT=/path/to/sky130 \
  -- python3 /absolute/path/to/scripts/run_mcp_server.py
```

Both need **absolute** paths: the host launches the command from its own working
directory, not from this one.

## The three tools

| tool | time | what it does |
|---|---|---|
| `check_spec` | < 1 s | Is the request inside the range the RL policy was trained on, and does any already-measured design satisfy it? No simulation. |
| `design_equalizer` | median 37 s ([details](#how-long-it-takes)) | Propose, screen the eye in SPICE, verify at all 20 PVT corners. Returns sizes, scorecard, plots, netlist, and a self-contained HTML report. |
| `explain_capabilities` | instant | The measured capabilities **and the measured limits**, so the model quotes them rather than inferring. |

## Which method answers

| | used when | measured |
|---|---|---|
| **SAC policy** | the request is inside its trained range | 56.2 % of proposals survive 20-corner verification; 8 proposed in 1.2 s |
| **CEM search** | outside that range, or the policy's proposals all fail the eye screen | 32.5 % per proposal, 41 s per 8 — but no trained range at all |

The policy was trained on a bounded goal space: peaking 5–10 dB, f_peak window
within 1.1–1.6 / 2.0–2.8 GHz, hd3 −34…−26 dBc, power 3–15 mW, eye 60–130 mV and
0.35–0.50 UI. Ask for something outside it and the server uses the search
instead **and says so** — an extrapolating policy still answers, it just does not
carry the measured reliability, and presenting it as though it did is the
failure this design avoids.

## Ten conditions, not eight

A design is judged on the eight brief specs **plus two internal validity
checks**, and `all_pass` covers all ten:

| check | what it asks |
|---|---|
| `bias` | are the devices biased in saturation? An unbiased circuit can post good-looking small-signal numbers that mean nothing. |
| `equalises` | is the eye more *open* at the slicer than at the input? The summing stage's gain is a free variable, so a design can enlarge the eye without equalising anything. |

The scorecard shows all ten, the internal two marked as such. A count of "8/8"
refers only to the brief specs and is always reported next to the ten — never
instead of it. That mattered: for a while a design failing only `bias` rendered
as *"DOES NOT PASS — 8/8 at all 20 PVT corners"* with eight passing rows and no
sign of the condition that failed.

The opposite error followed it, and is worth recording because the test suite
did not catch it. The 20-corner verification records *margins* for the eight
brief specs only -- `bias` and `equalises` have no entry -- and the renderer
read a missing margin as a failed one. So a design that passed everywhere
printed `bias ... n/a FAIL` beside `all_pass: True`. The fixtures agreed with
the renderer about a dict the simulator never returns, so every test passed.
It was found by driving one real design through an MCP client, which is now
what the shape in `tests/test_mcp_reporting.py` is modelled on. The verdict for
every row comes from the same per-corner pass state `all_pass` is computed
from; the margin is displayed when it exists and is never invented.

## The report page

Every `design_equalizer` call writes a self-contained `report.html` and returns
its path as `report_path`. One file, no external references: the eye diagram
and AC response are embedded as `data:` URIs, alongside the ten-row scorecard,
the eleven sized devices with their units, which limits were **asked for**
versus **defaulted from the brief**, the method and envelope check, and the
netlist.

It exists because MCP image content is collapsed behind an expander in Claude
Desktop and most terminals cannot render images at all, so "here are your
diagrams" frequently shows the caller nothing. The page does not depend on the
host. If writing it fails the design is still returned, with `report_error`
set -- a report must never cost a result.

## Where the design starts from

Both searches begin at a hand-derived design rather than at random, and the
server derives it with **PhysFit** -- the closed-form derivation with every
device estimate taken from the fitted model instead of ngspice. Measured, that
is the same coverage as the derivation the policy was trained against, **8 of
10 specifications**, with 7 of 10 solved at the first proposal, and it costs
~0.2 s per spec against ~1.2 s (`results/solve_rate_physfit.json`,
`docs/systematic-starting-point.md`).

Every result says which derivation produced it, in `start_point`. If the single
`.op` that measures the threshold voltage could not run, `start_point` says so
and names the constant that was used instead -- a design derived from a guessed
threshold must not read identically to one derived from a measured one.

```
"EQRL_START": "physics"     # back to the derivation the policy was trained on
```

is accepted in the same `env` block as `PDK_ROOT`. The choice is applied once,
before either proposer runs, through the seam in `eqrl/start.py` rather than by
patching module globals -- the policy path and the CEM fallback **must** begin
from the same design, and a monkeypatch cannot reach `deploy_policy`, which the
server imports only once a request arrives.

## What it will not do

These are enforced in `eqrl/mcp_server.py`, not asked for in a prompt:

- **`all_pass` is only ever true after all 20 PVT corners were simulated.** The
  learned model's opinion is an intermediate step, never a reported result.
- **A failure still returns the best design found**, with the specs it missed
  and by how much. "Nothing found" with no artifact helps no one.
- **The method and the envelope check are always reported**, so an answer can
  never look more authoritative than it is.
- **Margins are printed as measured.** Nothing is rounded toward passing.
- **The starting derivation is reported**, including when its one SPICE
  measurement fell back to a stored constant.

Roughly 20 % of specifications are not solved by either method — see the README.
When that happens the server says so rather than returning a design that does
not meet the request.

## How long it takes

| request | measured |
|---|---|
| solvable, inside the trained range | **median 37 s, range 28 - 102 s** over the 16 calls in `results/mcp_e2e.json`; two later runs of the README example took 48 s and 51 s |
| unsolvable | **up to ~420 s** — both methods run and every screen survivor is verified at 20 corners |

The median is not a bound. `budget_s` is checked *between* proposals, so one
slow 20-corner verification runs past it to completion.

`design_equalizer` takes a `budget_s` (default 150). On expiry it returns the
best design found so far with `budget_exceeded: True` and `all_pass: False` —
never a pass, because a pass means twenty corners were simulated and a
truncated run did not finish them.

Call `check_spec` first when a request looks ambitious; it costs nothing.

## Why a solvable one is ~40 seconds

```
propose 8 designs             1.2 s   no simulator at all
eye-screen 8 at 2 corners    16.2 s   16 SPICE evaluations
verify the best, 20 corners  ~20   s   20 SPICE evaluations
eye + AC plots               ~10   s   two more simulations
```

Only one design is verified at the full corner grid — the first one that clears
the eye screen, which in 7 of 8 solved cases is the first one tried. The
measurement harness behind this project's headline numbers verifies *every*
proposal to build a coverage curve; designing one circuit does not.
