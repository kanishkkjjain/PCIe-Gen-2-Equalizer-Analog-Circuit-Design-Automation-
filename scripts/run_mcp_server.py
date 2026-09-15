#!/usr/bin/env python3
"""Run the equalizer design MCP server over stdio.

  pip install -r requirements-mcp.txt
  export PDK_ROOT=/path/to/sky130
  python3 scripts/run_mcp_server.py

Point an MCP host at this command; see MCP.md for the config snippet.

  python3 scripts/run_mcp_server.py --self-test

checks that the tools register and that the spec/envelope logic answers, with
no host and no simulator -- which is the failure worth catching early, because
a server that starts but exposes nothing looks identical to a working one from
the outside.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


def build_server():
    """Register the tools. Imported by the self-test as well as by main."""
    try:
        from mcp.server.mcpserver import Image, MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ModuleNotFoundError as e:                       # pragma: no cover
        raise SystemExit(
            "the MCP SDK is not installed.  pip install -r requirements-mcp.txt"
        ) from e

    from eqrl import mcp_server as impl

    server = MCPServer(
        name="pcie-equalizer",
        instructions=(
            "Sizes a PCIe Gen2 equalizer (CTLE + 1-tap DFE, SKY130) to a "
            "requested specification and verifies it in ngspice across the 20 "
            "PVT corners of the brief.\n\n"
            "Call check_spec first when the user's request looks unusual -- it "
            "costs no simulation and says whether the request is inside the "
            "range the RL policy was trained on.\n\n"
            "design_equalizer takes about 45 seconds because it really does run "
            "SPICE. Report its `all_pass` field as given: it is True only when "
            "all 20 corners were simulated and passed. If it is False, the "
            "design returned is the closest one found -- say so, and say which "
            "specs it missed and by how much. Do not describe a design as "
            "meeting a specification unless all_pass is True."
        ),
    )

    @server.tool(
        title="Design an equalizer",
        description="Size a PCIe Gen2 equalizer to a specification and verify "
                    "it across all 20 PVT corners in ngspice (~45 s). Returns "
                    "the 11 sized device values, a per-spec scorecard, an eye "
                    "diagram, the AC response, and a runnable SPICE netlist.")
    def design_equalizer(
        peaking_db: float | None = None,
        fpeak_lo_ghz: float | None = None,
        fpeak_hi_ghz: float | None = None,
        hd3_max_db: float | None = None,
        noise_max_mvrms: float | None = None,
        power_max_mw: float | None = None,
        eye_height_min_mv: float | None = None,
        eye_width_min_ui: float | None = None,
    ) -> list:
        try:
            r = impl.design_equalizer(peaking_db, fpeak_lo_ghz, fpeak_hi_ghz,
                                      hd3_max_db, noise_max_mvrms,
                                      power_max_mw, eye_height_min_mv,
                                      eye_width_min_ui)
        except ValueError as e:
            raise ToolError(str(e)) from e
        # Images are returned ALONGSIDE the text, and their paths are in the
        # text too: a host that cannot render images still gets a usable answer.
        out: list = [_render(r)]
        for key in ("eye_png", "ac_png"):
            p = r.get(key)
            if p and Path(p).exists():
                out.append(Image(path=p))
        return out

    @server.tool(
        title="Check whether a specification is reachable",
        description="No simulation, under a second. Says whether the request "
                    "is inside the range the RL policy was trained on, and "
                    "whether any already-measured design satisfies it.")
    def check_spec(
        peaking_db: float | None = None,
        fpeak_lo_ghz: float | None = None,
        fpeak_hi_ghz: float | None = None,
        hd3_max_db: float | None = None,
        noise_max_mvrms: float | None = None,
        power_max_mw: float | None = None,
        eye_height_min_mv: float | None = None,
        eye_width_min_ui: float | None = None,
    ) -> dict:
        # A contradictory request -- an inverted f_peak window, an empty
        # peaking band -- is the CALLER's mistake, and the caller is a model
        # that can fix it if it is told what was wrong. Letting the ValueError
        # escape makes the SDK report `UnexpectedToolError: Error executing
        # tool check_spec`, which is crash-shaped and says nothing: the model
        # cannot tell a bad argument from a broken server, and retries the same
        # call. ToolError carries the message through instead.
        try:
            return impl.check_spec(peaking_db, fpeak_lo_ghz, fpeak_hi_ghz,
                                   hd3_max_db, noise_max_mvrms, power_max_mw,
                                   eye_height_min_mv, eye_width_min_ui)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @server.tool(
        title="What this framework can and cannot do",
        description="The measured capabilities and the measured limits. Use "
                    "this rather than inferring what the tool can do.")
    def explain_capabilities() -> str:
        return impl.explain_capabilities()

    return server


def _render(r: dict) -> str:
    """The result as text a model can read without inventing anything."""
    if not r.get("ok"):
        return (f"NO DESIGN FOUND.\n{r.get('reason','')}\n"
                f"in trained envelope: {r.get('in_envelope')}\n"
                f"attempts: {r.get('attempts')}\n"
                f"({r.get('wall_s')} s)")
    L = [f"{'PASSES' if r['all_pass'] else 'DOES NOT PASS'} "
         f"-- {r['specs_passed']}",
         f"method: {r['method']}   "
         f"in trained envelope: {r['in_envelope']}"]
    if r.get("outside_trained_range"):
        L.append(f"OUTSIDE the policy's trained range: "
                 f"{sorted(r['outside_trained_range'])} -- a search was used "
                 f"and the policy's measured reliability does not apply")
    if not r["all_pass"]:
        L.append(f"failed specs: {r['failed_specs']} "
                 f"-- this is the closest design found, not a solution")
    L.append("")
    L.append(f"{'spec':9}{'required':26}{'margin':>10}  result")
    for row in r["scorecard"]:
        mg = "n/a" if row["margin"] is None else f"{row['margin']:+.4f}"
        L.append(f"{row['spec']:9}{row['required']:26}{mg:>10}  "
                 f"{'pass' if row['pass'] else 'FAIL'}")
    L.append("")
    L.append("sized devices:")
    for k, v in r["sized_parameters"].items():
        L.append(f"   {k:8} {v:.6g}")
    if r.get("eye_measured"):
        L.append(f"\nmeasured eye: {r['eye_measured']}")
    for k, lbl in (("eye_png", "eye diagram"), ("ac_png", "AC response"),
                   ("netlist_path", "SPICE netlist")):
        if r.get(k):
            L.append(f"{lbl}: {r[k]}")
    L.append(f"\n({r['wall_s']} s)")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    server = build_server()
    if a.self_test:
        import asyncio
        tools = asyncio.run(server.list_tools())
        print(f"{len(tools)} tools registered:")
        for t in tools:
            print(f"   {t.name}")
        from eqrl import mcp_server as impl
        r = impl.check_spec(peaking_db=8, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2)
        print(f"\ncheck_spec responds: in_envelope="
              f"{r['in_trained_envelope']}, "
              f"{r['known_designs_meeting_it']['n_meeting']} known designs meet it")
        assert len(tools) == 3, f"expected 3 tools, got {len(tools)}"
        print("\nself-test OK")
        return 0
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
