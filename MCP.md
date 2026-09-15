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
| `design_equalizer` | ~45 s | Propose, screen the eye in SPICE, verify at all 20 PVT corners. Returns sizes, scorecard, plots, netlist. |
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

## What it will not do

These are enforced in `eqrl/mcp_server.py`, not asked for in a prompt:

- **`all_pass` is only ever true after all 20 PVT corners were simulated.** The
  learned model's opinion is an intermediate step, never a reported result.
- **A failure still returns the best design found**, with the specs it missed
  and by how much. "Nothing found" with no artifact helps no one.
- **The method and the envelope check are always reported**, so an answer can
  never look more authoritative than it is.
- **Margins are printed as measured.** Nothing is rounded toward passing.

Roughly 20 % of specifications are not solved by either method — see the README.
When that happens the server says so rather than returning a design that does
not meet the request.

## Why ~45 seconds

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
