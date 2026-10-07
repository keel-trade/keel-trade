"""The renamed tools' deprecated names (Q-2080, 2026-10-01).

Ten listed tools were renamed for OpenAI's plugin tool scan (`_toolsets
.TOOL_ALIASES`). The contract, proved both ways here on BOTH servers the one
codebase runs — the hosted listed registration and a local stdio server:

* `tools/list` advertises ONLY the new names (an old spelling in the catalog
  would be a tool the scan reads and the host freezes);
* `tools/call` with an old name still runs the renamed tool, through the
  wire handler (`fastmcp.Client` over the in-memory transport) and the
  in-process `call_tool`, with the middleware seeing the NEW name;
* a renamed PARAMETER's old name (`no_ownership_hint` → `skip_readiness`) is
  accepted and rewritten before validation;
* control arms: a name that was never a tool is still not found, and a
  call under the new name is unchanged.

Proof it can fail (run 2026-10-01): comment out the `call_tool` override in
`keel.mcp.server.KeelMCP` — `test_old_names_still_call_the_renamed_tool_*`
red naming every alias; delete one `PARAM_ALIASES` row —
`test_old_parameter_name_is_accepted` reds.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    PARAM_ALIASES,
    TOOL_ALIASES,
    aliases_of,
    canonical_tool_name,
)


_bootstrap()

_PROFILE_ENV = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS", "KEEL_LISTED_CLIENT")


@contextmanager
def _env(**values: str):
    saved = {k: os.environ.get(k) for k in _PROFILE_ENV}
    for k in _PROFILE_ENV:
        os.environ.pop(k, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _hosted_listed():
    return _env(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")


def _local_full():
    return _env(KEEL_TOOLSETS="read-only,backtest,share,live-read,live-write")


# ── The table itself ────────────────────────────────────────────────────


def test_alias_table_maps_only_old_names_onto_registered_tools():
    """Every alias key is NOT a registered tool; every value is one and is on
    the listed surface (the rename exists for that surface)."""
    assert TOOL_ALIASES, "no aliases declared — nothing to prove"
    for old, new in TOOL_ALIASES.items():
        assert old not in OUTCOMES, f"{old} is still registered — it was not renamed"
        assert new in OUTCOMES, f"{new} is not a registered tool"
        assert new in LISTED_PROFILE_TOOLS, f"{new} is not listed"
        assert old != new
    # Non-vacuity: the ten renames of 2026-10-01.
    assert len(TOOL_ALIASES) == 10
    assert canonical_tool_name("keel_status") == "keel_account_status"
    assert canonical_tool_name("keel_account_status") == "keel_account_status"
    assert canonical_tool_name("keel_not_a_tool") == "keel_not_a_tool"
    assert aliases_of("keel_account_status") == frozenset({"keel_status"})
    assert aliases_of("keel_backtest_run") == frozenset()


def test_param_alias_table_names_real_parameters():
    for tool_name, renames in PARAM_ALIASES.items():
        props = OUTCOMES[tool_name].input_schema["properties"]
        for old, new in renames.items():
            assert old not in props, (tool_name, old)
            assert new in props, (tool_name, new)
    assert sum(len(v) for v in PARAM_ALIASES.values()) >= 3


# ── tools/list: new names only, on both servers ─────────────────────────


@pytest.mark.parametrize("profile", ["hosted-listed", "local-full"])
def test_tools_list_advertises_no_old_name(profile):
    from keel.mcp.server import create_server

    with _hosted_listed() if profile == "hosted-listed" else _local_full():
        names = {t.name for t in asyncio.run(create_server().list_tools())}
    leaked = names & set(TOOL_ALIASES)
    assert not leaked, f"{profile}: tools/list advertises a deprecated name: {sorted(leaked)}"
    # Non-vacuity: every renamed tool IS listed under its new name.
    assert set(TOOL_ALIASES.values()) <= names, sorted(set(TOOL_ALIASES.values()) - names)


# ── tools/call: old names still run the renamed tool ────────────────────


def _message_of(result) -> str:
    """The envelope text a call returns, whatever channel it rides."""
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        text += "\n" + structured["result"]
    return text


def _probe_args(canonical: str) -> dict:
    """Arguments that make the tool answer WITHOUT the network: an argument
    the schema does not know, so the adapter's own usage envelope names the
    tool that ran. The envelope is the proof the canonical handler was
    reached — it is built from that tool's schema and name."""
    return {"zz_not_a_param": 1}


@pytest.mark.parametrize("old_name", sorted(TOOL_ALIASES))
def test_old_names_still_call_the_renamed_tool_in_process(old_name):
    from keel.mcp.server import create_server

    canonical = TOOL_ALIASES[old_name]
    with _hosted_listed():
        server = create_server()
        result = asyncio.run(server.call_tool(old_name, _probe_args(canonical)))
    text = _message_of(result)
    assert f"Invalid arguments to {canonical}" in text, text[:300]
    assert old_name not in text, f"the envelope names the deprecated spelling: {text[:300]}"


@pytest.mark.parametrize("old_name", sorted(TOOL_ALIASES))
def test_old_names_still_call_the_renamed_tool_over_the_wire(old_name):
    """Through the MCP protocol (fastmcp's in-memory client), on the local
    stdio server's profile — the second server the alias layer must cover."""
    from fastmcp import Client
    from keel.mcp.server import create_server

    canonical = TOOL_ALIASES[old_name]

    async def go():
        async with Client(create_server()) as client:
            listed = {t.name for t in await client.list_tools()}
            result = await client.call_tool(old_name, _probe_args(canonical), raise_on_error=False)
            return listed, result

    with _local_full():
        listed, result = asyncio.run(go())
    assert old_name not in listed and canonical in listed
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    if isinstance(result.structured_content, dict):
        text += "\n" + json.dumps(result.structured_content)
    assert f"Invalid arguments to {canonical}" in text, text[:300]


def test_a_name_that_was_never_a_tool_is_still_not_found():
    """Control arm: the alias layer resolves only the declared spellings."""
    from fastmcp import Client
    from keel.mcp.server import create_server

    async def go():
        async with Client(create_server()) as client:
            return await client.call_tool("keel_not_a_tool", {}, raise_on_error=False)

    with _local_full():
        result = asyncio.run(go())
    assert result.is_error
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    assert "keel_not_a_tool" in text


def test_the_middleware_sees_the_canonical_name():
    """The hosted gate, audit and metrics key on the name the middleware
    reads: an alias call reaches the chain already canonicalised."""
    from fastmcp.server.middleware import Middleware
    from keel.mcp.server import create_server

    seen: list[str] = []

    class Spy(Middleware):
        async def on_call_tool(self, context, call_next):
            seen.append(context.message.name)
            return await call_next(context)

    with _hosted_listed():
        server = create_server()
        server.add_middleware(Spy())
        asyncio.run(server.call_tool("keel_doctor", {"zz": 1}))
        asyncio.run(server.call_tool("keel_connection_check", {"zz": 1}))
    assert seen == ["keel_connection_check", "keel_connection_check"]


# ── a renamed parameter ────────────────────────────────────────────────


def test_old_parameter_name_is_accepted_and_rewritten():
    from keel.tools.outcomes._base import OutcomeResult, OutcomeTool
    from keel.tools.outcomes._mcp_adapter import _make_handler

    captured: list[dict] = []

    def fake(args, ctx):
        captured.append(dict(args))
        return OutcomeResult()

    base = OUTCOMES["keel_strategy_get"]
    tool = OutcomeTool(
        name=base.name,
        cli_path=base.cli_path,
        toolset=base.toolset,
        description=base.description,
        input_schema=base.input_schema,
        annotations=base.annotations,
        handler=fake,
    )
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KEEL_EXECUTION_MODE", None)
        handler = _make_handler(tool, frozenset())
        old = json.loads(handler(strategy_id="str_x", no_ownership_hint=True))
        new = json.loads(handler(strategy_id="str_x", skip_readiness=True))
        both = json.loads(
            handler(strategy_id="str_x", no_ownership_hint=True, skip_readiness=False)
        )
    assert "code" not in old and "code" not in new and "code" not in both
    assert captured[0] == {"strategy_id": "str_x", "skip_readiness": True}
    assert captured[1] == {"strategy_id": "str_x", "skip_readiness": True}
    # The OLD name wins when a call carries both (Q-2267): through FastMCP the
    # new name is ALWAYS present at its schema default, so the old name is
    # the only one a frozen catalog's caller actually sent.
    assert captured[2] == {"strategy_id": "str_x", "skip_readiness": True}


def test_old_parameter_name_is_absent_from_every_published_schema():
    from keel.mcp.server import create_server

    with _hosted_listed():
        tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    with _local_full():
        tools.update({t.name: t for t in asyncio.run(create_server().list_tools())})
    for tool_name, renames in PARAM_ALIASES.items():
        props = set((tools[tool_name].parameters or {}).get("properties", {}))
        assert not (props & set(renames)), (tool_name, sorted(props & set(renames)))
        assert set(renames.values()) <= props, (tool_name, sorted(set(renames.values()) - props))
