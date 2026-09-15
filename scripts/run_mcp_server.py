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
from typing import Annotated

from pydantic import Field

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


# PARAMETER SCHEMAS. Sixteen slots previously shipped with no description,
# no units and no bounds, so the model saw auto-generated titles like "Eye
# Height Min Mv" and had to guess. Worse, nothing rejected `power_max_mw=-5`
# or `hd3_max_db=+30` -- the latter being the classic sign slip, which
# reported MORE matching designs than no constraint at all, so the mistake
# made the request look easier.
#
# With bounds in the schema the HOST refuses these before they reach the
# model, which is both faster and impossible to talk past.
Peaking = Annotated[float | None, Field(
    default=None, gt=0.5, lt=14.5,
    description="Target high-frequency peaking boost in dB. Widened by "
                "+/-2.5 dB and clipped to the brief's 3-12 dB band. "
                "Default 8.")]
FpeakLo = Annotated[float | None, Field(
    default=None, gt=0.1, lt=10.0,
    description="Lower edge of the band the peak must fall in, in GHz. "
                "Must be below fpeak_hi_ghz. Default 1.25.")]
FpeakHi = Annotated[float | None, Field(
    default=None, gt=0.1, lt=10.0,
    description="Upper edge of the band the peak must fall in, in GHz. "
                "Default 2.5.")]
Hd3 = Annotated[float | None, Field(
    default=None, lt=0.0, gt=-80.0,
    description="Maximum third-harmonic distortion in dBc, measured at "
                "the slicer. NEGATIVE -- it is a suppression. Default -30.")]
Noise = Annotated[float | None, Field(
    default=None, gt=0.0, le=100.0,
    description="Maximum input-referred noise in mV rms over 10 MHz-5 GHz. "
                "Nothing below 0.159 has ever been measured. Default 1.5.")]
Power = Annotated[float | None, Field(
    default=None, gt=0.0, le=1000.0,
    description="Maximum supply power in mW. Default 15.")]
EyeH = Annotated[float | None, Field(
    default=None, gt=0.0, le=2000.0,
    description="Minimum eye opening in mV at the slicer, with the channel "
                "and DFE active. Default 100.")]
EyeW = Annotated[float | None, Field(
    default=None, gt=0.0, lt=1.0,
    description="Minimum eye opening as a fraction of the unit interval. "
                "Must be below 1 -- an eye cannot be wider than the symbol "
                "it lives in. Default 0.4.")]



def build_server():
    """Register the tools. Imported by the self-test as well as by main."""
    try:
        from mcp.server.mcpserver import Image, MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import CallToolResult, TextContent
    except ModuleNotFoundError as e:                       # pragma: no cover
        raise SystemExit(
            "the MCP SDK is not installed.  pip install -r requirements-mcp.txt"
        ) from e

    from eqrl import mcp_server as impl


    server = MCPServer(
        name="pcie-equalizer",
        instructions=(
            "Sizes a PCIe Gen2 equalizer (1-stage CTLE with source "
            "degeneration + 1-tap DFE summing stage, SKY130) to a requested "
            "specification, and verifies it in ngspice across the 20 PVT "
            "corners of the PCIe brief.\n"
            "\n"
            "WHAT THE RESULT MEANS\n"
            "  `all_pass` is True ONLY after all 20 corners were actually "
            "simulated and every brief spec passed at every one of them. It is "
            "never set from the learned model's opinion. You can rely on that: "
            "if it is True the design is verified, and if it is False the "
            "design has not been shown to meet the spec, however close it "
            "looks.\n"
            "  A False result still returns a usable design plus "
            "`failed_specs` and the margins. Give the user the design and say "
            "plainly which specs it missed and by how much -- do not withhold "
            "it, and do not describe it as meeting the specification.\n"
            "  Margins are printed as measured. Do not round one toward "
            "passing.\n"
            "\n"
            "WHICH METHOD ANSWERS\n"
            "  Two are available. A trained RL policy is used when the request "
            "falls inside the range it was trained on (56.2 % of its proposals "
            "survive 20-corner verification, and it proposes in ~1 s); a CEM "
            "search is used otherwise, because a search has no trained range. "
            "The result names which one ran in `method`, and "
            "`in_envelope`/`outside_trained_range` say whether the request was "
            "inside the trained range. Pass that on -- an out-of-range answer "
            "is still a real verified design, it just did not come from the "
            "policy.\n"
            "\n"
            "WHEN NOTHING IS FOUND\n"
            "  About 20 % of specifications are solved by neither method. "
            "`ok: False` is a real outcome, not a malfunction: retrying the "
            "same request will not help. Say the request could not be met, "
            "and suggest relaxing whichever spec `check_spec` or the last "
            "`failed_specs` points at.\n"
            "\n"
            "COST, AND WHAT TO CALL FIRST\n"
            "  design_equalizer really runs SPICE and takes about 45 seconds. "
            "check_spec costs nothing and takes under a second -- call it "
            "first whenever a request looks unusual or ambitious, and tell the "
            "user what it says before spending the 45 seconds.\n"
            "  Every design writes a self-contained HTML report -- scorecard, "
            "eye diagram, AC response, sized devices and netlist in one page. "
            "Its path comes back as `report_path`. TELL THE USER TO OPEN IT: "
            "image content is collapsed or unrendered in most hosts, so the "
            "report is how they actually see the proof.\n"
            "  The resource eqrl://capabilities lists what this framework "
            "does AND what it measurably cannot do; read it rather than "
            "inferring limits."
        ),
    )

    @server.tool(
        title="Design an equalizer",
        description="Size a PCIe Gen2 equalizer to a specification and verify "
                    "it across all 20 PVT corners in ngspice (~45 s). Returns "
                    "the 11 sized device values, a per-spec scorecard, an eye "
                    "diagram, the AC response, and a runnable SPICE netlist.")
    def design_equalizer(
        peaking_db: Peaking = None,
        fpeak_lo_ghz: FpeakLo = None,
        fpeak_hi_ghz: FpeakHi = None,
        hd3_max_db: Hd3 = None,
        noise_max_mvrms: Noise = None,
        power_max_mw: Power = None,
        eye_height_min_mv: EyeH = None,
        eye_width_min_ui: EyeW = None,
    ):
        try:
            r = impl.design_equalizer(peaking_db, fpeak_lo_ghz, fpeak_hi_ghz,
                                      hd3_max_db, noise_max_mvrms,
                                      power_max_mw, eye_height_min_mv,
                                      eye_width_min_ui)
        except ValueError as e:
            raise ToolError(str(e)) from e

        # TEXT + IMAGES + STRUCTURED DATA, in one result.
        #
        # This used to return prose and pictures only -- while the server's own
        # instructions told the model to "report its `all_pass` field as
        # given". There was no such field on the wire: the dict below was built
        # and then thrown away, and the model had to parse a table and hope.
        # Returning a CallToolResult directly is passed through by the SDK
        # untouched, so the guarantees the instructions describe now actually
        # exist as data a caller can check.
        content = [TextContent(type="text", text=_render(r))]
        for key in ("eye_png", "ac_png"):
            path = r.get(key)
            if path and Path(path).exists():
                content.append(Image(path=path).to_image_content())
        # paths are not JSON-serialisable surprises; everything here is plain
        structured = {k: v for k, v in r.items() if k != "netlist"}
        return CallToolResult(content=content, structured_content=structured)

    @server.tool(
        title="Check whether a specification is reachable",
        description="No simulation, under a second. Says whether the request "
                    "is inside the range the RL policy was trained on, and "
                    "whether any already-measured design satisfies it.")
    def check_spec(
        peaking_db: Peaking = None,
        fpeak_lo_ghz: FpeakLo = None,
        fpeak_hi_ghz: FpeakHi = None,
        hd3_max_db: Hd3 = None,
        noise_max_mvrms: Noise = None,
        power_max_mw: Power = None,
        eye_height_min_mv: EyeH = None,
        eye_width_min_ui: EyeW = None,
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

    # ---- resources: what the model can READ, without a round trip --------
    # The server was advertising a `resources` capability while providing
    # none, so a client that asked got an empty list -- a promise with nothing
    # behind it. Each of these points at text that already existed.
    @server.resource("eqrl://capabilities", name="capabilities",
                     title="What this framework can and cannot do",
                     mime_type="text/plain",
                     description="Measured capabilities AND measured limits. "
                                 "Read this rather than inferring what the "
                                 "tools can do.")
    def _capabilities() -> str:
        return impl.explain_capabilities()

    @server.resource("eqrl://trained-range", name="trained_range",
                     title="The range the RL policy was trained on",
                     mime_type="application/json",
                     description="Specs inside this range are answered by the "
                                 "policy; outside it the server falls back to "
                                 "a search and says so.")
    def _trained_range() -> dict:
        return impl.trained_range()

    @server.resource("eqrl://guide", name="guide",
                     title="Usage guide",
                     mime_type="text/markdown",
                     description="How to connect and use this server, with "
                                 "worked examples and the honesty rules.")
    def _guide() -> str:
        f = ROOT / "MCP.md"
        return f.read_text(encoding="utf-8") if f.exists() else impl.CAPABILITIES

    # ---- prompts: one-keystroke entry points for the PERSON ---------------
    # Prompts are user-controlled, not model-facing -- in Claude Code they
    # appear as /mcp__pcie-equalizer__<name>. They exist so the common
    # workflows, including asking for the limits, are one keystroke rather
    # than something the user has to think to phrase.
    @server.prompt(name="design_equalizer",
                   title="Design an equalizer",
                   description="Size and verify an equalizer for a spec")
    def design_equalizer_prompt(peaking_db: str = "8",
                                fpeak_lo_ghz: str = "1.4",
                                fpeak_hi_ghz: str = "2.2",
                                power_max_mw: str = "10") -> str:
        return (f"Design a PCIe Gen2 equalizer with about {peaking_db} dB of "
                f"peaking, peaking between {fpeak_lo_ghz} and {fpeak_hi_ghz} "
                f"GHz, drawing under {power_max_mw} mW.\n\n"
                "Use design_equalizer. Then show me the 20-corner scorecard, "
                "the eye diagram and the AC response, and tell me which method "
                "produced the design and whether the request was inside the "
                "policy's trained range. If it did not pass, say which specs "
                "it missed and by how much.")

    @server.prompt(name="check_feasibility",
                   title="Is this spec reachable?",
                   description="Cheap feasibility check, no simulation")
    def check_feasibility_prompt(peaking_db: str = "8",
                                 eye_height_min_mv: str = "100",
                                 power_max_mw: str = "10") -> str:
        return (f"Before running any simulation, check whether a PCIe Gen2 "
                f"equalizer with {peaking_db} dB peaking, an eye at least "
                f"{eye_height_min_mv} mV and under {power_max_mw} mW is "
                "reachable. Use check_spec. Tell me whether it is inside the "
                "RL policy's trained range, how many already-measured designs "
                "satisfy it, and whether it is worth spending the 45 seconds "
                "on a full design run.")

    @server.prompt(name="explain_limits",
                   title="What are the limits?",
                   description="What this framework measurably cannot do")
    def explain_limits_prompt() -> str:
        return ("Read the eqrl://capabilities resource and tell me plainly "
                "what this equalizer design framework CANNOT do -- the specs "
                "it fails to solve, how often, and which measurements are "
                "unreliable. I want the limits, not the achievements.")

    return server


def _render(r: dict) -> str:
    """The result as text a model can read without inventing anything."""
    if not r.get("ok"):
        return (f"NO DESIGN FOUND.\n{r.get('reason','')}\n"
                f"in trained envelope: {r.get('in_envelope')}\n"
                f"attempts: {r.get('attempts')}\n"
                f"({r.get('wall_s')} s)")
    # `.get` throughout, not indexing. The budget path returns `ok: True` with
    # a best-effort design and NONE of `specs_passed`, `method` or
    # `sized_parameters` -- so the one branch written to degrade gracefully
    # raised KeyError here and killed the tool call outright.
    L = [f"{'PASSES' if r['all_pass'] else 'DOES NOT PASS'} "
         f"-- {r.get('specs_passed', 'no verification completed')}",
         f"method: {r.get('method') or r.get('method_attempted')}   "
         f"in trained envelope: {r.get('in_envelope')}"]
    if r.get("budget_exceeded"):
        L.append(f"TIME BUDGET EXCEEDED -- {r.get('reason', '')}")
    if r.get("outside_trained_range"):
        L.append(f"OUTSIDE the policy's trained range: "
                 f"{sorted(r['outside_trained_range'])} -- a search was used "
                 f"and the policy's measured reliability does not apply")
    if not r["all_pass"] and r.get("failed_specs"):
        L.append(f"failed specs: {r['failed_specs']} "
                 f"-- this is the closest design found, not a solution")
    L.append("")
    # Columns sized from the data. Fixed widths of 9 and 26 were set by the
    # eight brief specs and then overrun by the two internal checks, whose
    # names and descriptions are longer: "equalises" exactly filled its column
    # and collided with the next one, printing "equaliseseye openness ...".
    rows = r.get("scorecard") or []
    if rows:
        wk = max(len("spec"), *(len(x["spec"]) for x in rows)) + 2
        wr = max(len("required"), *(len(x["required"]) for x in rows)) + 2
        L.append(f"{'spec':{wk}}{'required':{wr}}{'margin':>10}  result")
        for row in rows:
            mg = "n/a" if row["margin"] is None else f"{row['margin']:+.4f}"
            L.append(f"{row['spec']:{wk}}{row['required']:{wr}}{mg:>10}  "
                     f"{'pass' if row['pass'] else 'FAIL'}")
        L.append("")
    if r.get("sized_parameters"):
        L.append("sized devices:")
        for k, v in r["sized_parameters"].items():
            L.append(f"   {k:8} {v:.6g}")
    if r.get("eye_measured"):
        L.append(f"\nmeasured eye: {r['eye_measured']}")
    # the report first: it is the one thing worth opening, and it contains the
    # other three
    if r.get("report_path"):
        L.append(f"\nFULL REPORT (open this): {r['report_path']}")
        L.append("   scorecard, eye diagram, AC response, sized devices and "
                 "the netlist, in one page")
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
        async def _enumerate():
            return (await server.list_tools(), await server.list_resources(),
                    await server.list_prompts())
        tools, resources, prompts = asyncio.run(_enumerate())
        print(f"{len(tools)} tools:")
        for t in tools:
            print(f"   {t.name}")
        print(f"{len(resources)} resources:")
        for r in resources:
            print(f"   {r.uri}")
        print(f"{len(prompts)} prompts (slash commands in the host):")
        for pr in prompts:
            print(f"   {pr.name}")
        from eqrl import mcp_server as impl
        r = impl.check_spec(peaking_db=8, fpeak_lo_ghz=1.4, fpeak_hi_ghz=2.2)
        print(f"\ncheck_spec responds: in_envelope="
              f"{r['in_trained_envelope']}, "
              f"{r['known_designs_meeting_it']['n_meeting']} known designs meet it")
        # A capability advertised with nothing behind it is worse than one
        # not advertised: the client asks, gets an empty list, and the feature
        # silently does not exist. This server shipped exactly that for a
        # while -- prompts and resources were declared and both were empty.
        assert len(tools) == 3, f"expected 3 tools, got {len(tools)}"
        assert len(resources) == 3, f"expected 3 resources, got {len(resources)}"
        assert len(prompts) == 3, f"expected 3 prompts, got {len(prompts)}"
        print("\nself-test OK")
        return 0
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
