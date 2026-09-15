"""The MCP layer itself -- schemas, transport, error handling.

`test_mcp.py` tests the design logic by calling the Python functions. That
leaves the whole protocol surface untested: what a host actually sees is the
tool SCHEMAS, and whether a bad argument comes back as a tool error or kills
the session. A server can be perfectly correct underneath and still be unusable
because its schema says a required argument is optional, or because one bad
call takes the connection down.

These talk to the server the way a host does -- over stdio, with the SDK's own
client.

Skipped entirely if the MCP SDK is not installed: it is an optional extra
(`requirements-mcp.txt`), and the rest of the framework does not need it.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("mcp", reason="the MCP server is an optional extra")

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                      # noqa: E402

# NOTE ON NAMING. The MCP *wire* format is camelCase (`inputSchema`,
# `isError`), but the 2.x Python objects expose snake_case (`input_schema`,
# `is_error`) -- and pydantic raises AttributeError on the camelCase spelling
# rather than returning None. Tests written against the wire names fail with
# "object has no attribute", which reads like a broken server rather than a
# broken test. Use the Python names here.

SERVER = ROOT / "scripts" / "run_mcp_server.py"


def _params() -> StdioServerParameters:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT))
    return StdioServerParameters(command=sys.executable,
                                 args=[str(SERVER)], env=env)


async def _with_session(fn, timeout: float = 120.0):
    """Start the server over stdio, hand `fn` a live session, shut it down."""
    async def run():
        async with stdio_client(_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)
    return await asyncio.wait_for(run(), timeout=timeout)


def _run(fn, timeout: float = 120.0):
    return asyncio.run(_with_session(fn, timeout))


# --------------------------------------------------------------------------
# what a host sees
# --------------------------------------------------------------------------
def test_the_server_starts_and_lists_its_three_tools():
    """A server that starts but exposes nothing looks identical to a working
    one from the outside -- the host simply never offers the tools."""
    async def go(s):
        return await s.list_tools()

    tools = _run(go)
    names = {t.name for t in tools.tools}
    assert names == {"design_equalizer", "check_spec", "explain_capabilities"}, names


def test_every_tool_describes_itself_and_its_arguments():
    """The description and schema are the whole basis on which a model decides
    to call a tool, and with what. A missing unit or an argument absent from
    the schema means the model guesses."""
    async def go(s):
        return await s.list_tools()

    for t in _run(go).tools:
        assert t.description and len(t.description) > 40, t.name
        props = (t.input_schema or {}).get("properties", {})
        if t.name == "explain_capabilities":
            continue
        for arg in ("peaking_db", "fpeak_lo_ghz", "power_max_mw"):
            assert arg in props, f"{t.name} schema is missing {arg}"
        # every argument is optional: a caller who only cares about power
        # must not be forced to restate the whole brief
        assert not (t.input_schema or {}).get("required"), \
            f"{t.name} demands arguments that should default to the brief"


def test_the_units_are_in_the_argument_names_the_model_sees():
    """"power_max=10" is ambiguous; "power_max_mw" is not. The model only ever
    sees the schema, so the units have to be in it."""
    async def go(s):
        return await s.list_tools()

    props = {t.name: set((t.input_schema or {}).get("properties", {}))
             for t in _run(go).tools}
    for name in ("design_equalizer", "check_spec"):
        for arg in props[name]:
            assert any(arg.endswith(u) for u in
                       ("_db", "_ghz", "_mw", "_mvrms", "_mv", "_ui")), \
                f"{name}.{arg} does not name its unit"


# --------------------------------------------------------------------------
# calling them for real
# --------------------------------------------------------------------------
def test_check_spec_answers_over_the_wire():
    async def go(s):
        return await s.call_tool("check_spec", {"peaking_db": 8,
                                                "fpeak_lo_ghz": 1.4,
                                                "fpeak_hi_ghz": 2.2})
    res = _run(go)
    assert not res.is_error, res.content
    blob = str(res.content) + str(getattr(res, "structured_content", ""))
    assert "trained_envelope" in blob or "in_trained_envelope" in blob


def test_capabilities_carry_the_limits_over_the_wire():
    """This text is what a model repeats when asked what the tool can do. If
    the limits do not survive transport, the model implies everything works."""
    async def go(s):
        return await s.call_tool("explain_capabilities", {})
    res = _run(go)
    blob = str(res.content)
    assert "80 %" in blob or "80%" in blob     # neither method solves all specs
    assert "0.57" in blob                      # eye width is not predicted well


def test_a_bad_argument_is_a_tool_error_not_a_dead_session():
    """An inverted f_peak window is a user mistake, and it must come back as an
    error the model can read and correct -- not an exception that takes the
    stdio connection down and leaves the host with a tool that "stopped
    working"."""
    async def go(s):
        bad = await s.call_tool("check_spec", {"fpeak_lo_ghz": 2.5,
                                               "fpeak_hi_ghz": 1.5})
        # the session must still be usable afterwards
        good = await s.call_tool("explain_capabilities", {})
        return bad, good

    bad, good = _run(go)
    assert bad.is_error, "an impossible f_peak window was accepted"
    assert "f_peak" in str(bad.content).lower() or "window" in str(bad.content).lower()
    assert not good.is_error, "the session died after one bad argument"


@pytest.mark.needs_pdk
def test_design_returns_text_and_images_over_the_wire():
    """The proof is the pictures. They have to survive the protocol, not just
    exist on disk -- a host renders what comes back in the content blocks."""
    async def go(s):
        return await s.call_tool("design_equalizer",
                                 {"peaking_db": 8, "fpeak_lo_ghz": 1.4,
                                  "fpeak_hi_ghz": 2.2, "power_max_mw": 10})
    res = _run(go, timeout=600.0)
    assert not res.is_error, res.content
    kinds = [getattr(c, "type", None) for c in res.content]
    assert "text" in kinds, kinds
    assert kinds.count("image") >= 2, f"expected eye + AC images, got {kinds}"
    text = next(c.text for c in res.content if getattr(c, "type", None) == "text")
    assert "PVT corners" in text and "sized devices" in text


# --------------------------------------------------------------------------
# resources and prompts -- the two primitives that were advertised and empty
# --------------------------------------------------------------------------
def test_no_capability_is_advertised_without_anything_behind_it():
    """The defect that prompted all of this.

    The server declared `prompts` and `resources` in its capabilities and
    provided ZERO of each. A client that asked got an empty list -- so the
    feature silently did not exist, while the handshake said it did. That is
    worse than not advertising: nothing errors, and nobody looks again.
    """
    async def go(s):
        init = await s.initialize()
        return (init.capabilities, await s.list_tools(),
                await s.list_resources(), await s.list_prompts())

    caps, tools, resources, prompts = _run(go)
    if caps.tools is not None:
        assert tools.tools, "tools capability advertised, none provided"
    if caps.resources is not None:
        assert resources.resources, "resources advertised, none provided"
    if caps.prompts is not None:
        assert prompts.prompts, "prompts advertised, none provided"


def test_the_resources_a_model_can_read_are_present_and_readable():
    """`explain_capabilities` is a tool the model must think to call. The same
    content as a RESOURCE can be pulled in as context without a round trip,
    which is the difference between limits the model happens to ask for and
    limits it has in front of it."""
    async def go(s):
        listed = await s.list_resources()
        caps = await s.read_resource("eqrl://capabilities")
        rng = await s.read_resource("eqrl://trained-range")
        return listed, caps, rng

    listed, caps, rng = _run(go)
    uris = {str(r.uri) for r in listed.resources}
    assert {"eqrl://capabilities", "eqrl://trained-range",
            "eqrl://guide"} <= uris, uris

    # the limits must survive being served as a resource, not just as a tool
    text = caps.contents[0].text
    assert "80 %" in text or "80%" in text
    assert "0.57" in text

    # and the trained range must be real numbers a model can compare against
    import json
    r = json.loads(rng.contents[0].text)
    assert r["peaking_db"] == [5.0, 10.0], r["peaking_db"]
    assert "not impossible" in r["note"], "the note must not read as a hard no"


def test_the_prompts_are_usable_workflows_not_placeholders():
    """In Claude Code these become /mcp__pcie-equalizer__<name>. A prompt that
    lists but expands to nothing useful is a dead menu entry."""
    async def go(s):
        listed = await s.list_prompts()
        got = await s.get_prompt("design_equalizer",
                                 {"peaking_db": "7", "power_max_mw": "8"})
        limits = await s.get_prompt("explain_limits", {})
        return listed, got, limits

    listed, got, limits = _run(go)
    names = {p.name for p in listed.prompts}
    assert names == {"design_equalizer", "check_feasibility",
                     "explain_limits"}, names
    # no `_prompt` suffix leaking into what the user types
    assert not any(n.endswith("_prompt") for n in names), names

    body = got.messages[0].content.text
    assert "7 dB" in body and "8 mW" in body, "arguments did not reach the text"
    assert "scorecard" in body, "the design prompt does not ask for the proof"

    lim = limits.messages[0].content.text
    assert "CANNOT" in lim or "cannot" in lim
    assert "eqrl://capabilities" in lim, "does not point at the limits resource"


def test_the_instructions_carry_the_guarantees_the_model_relies_on():
    """`instructions` is the server's one channel into the model's reading
    path. For a server whose whole point is not overstating results, it has to
    state what `all_pass` means and that "nothing found" is a real answer --
    otherwise the model fills both gaps with optimism."""
    async def go(s):
        return (await s.initialize()).instructions

    t = _run(go) or ""
    assert len(t) > 1500, f"instructions are only {len(t)} chars"
    assert "all 20 corners" in t
    assert "failed_specs" in t
    assert "20 %" in t or "20%" in t          # some specs have no answer
    assert "check_spec" in t                  # what to call first
    assert "eqrl://capabilities" in t         # where the limits live
