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
