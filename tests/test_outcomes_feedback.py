"""`keel_feedback` (spec 02 R4) — the never-fails feedback outcome tool.

The contract under test:

* full MCP round-trip (create_server → call_tool → keel-api mock) for
  the happy path, malformed input, server-500, and network-down — ALL
  of them return a success envelope (`status: ok`), never an error
  envelope; delivery failures carry `delivered: false` + a `note`;
* toolset `always` → present on every profile (full/listed × local/
  hosted) regardless of `KEEL_TOOLSETS`;
* programming errors are NOT silently swallowed (adapter internal_error
  path still fires for genuine bugs);
* the local-MCP surface self-identifies with `x-keel-surface:
  local-mcp` on the wire (spec 08 R5 carry-forward from M2.5 — the
  keel-api middleware derives `surface=local-mcp` from it).
"""

from __future__ import annotations

import asyncio
import json

import pytest
import respx
from httpx import Response
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()

TOOL = OUTCOMES["keel_feedback"]

API = "https://api.test.keel"


@pytest.fixture()
def _api_env(monkeypatch):
    """Authenticated SDK config pointed at the mock API; no retry sleeps."""
    monkeypatch.setenv("KEEL_API_KEY", "test-key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setattr("keel.client.time.sleep", lambda *_: None)


def _call_mcp(args: dict) -> dict:
    """Full MCP round-trip: real FastMCP server, tools/call, JSON body."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        result = await server.call_tool("keel_feedback", args)
        return json.loads(result.content[0].text)

    return asyncio.run(go())


# ─── MCP round-trip: never fails (spec 02 R4) ───────────────────────────


@respx.mock
def test_round_trip_happy_path(_api_env):
    route = respx.post(f"{API}/v1/feedback").mock(
        return_value=Response(200, json={"status": "ok", "feedback_id": "fbk_01TEST"})
    )
    env = _call_mcp(
        {
            "goal": "compose a momentum strategy",
            "kind": "friction",
            "severity": "low",
            "context_ref": "keel_backtest_run",
            "text": "watch output was confusing",
        }
    )
    assert env["status"] == "ok"
    assert env["delivered"] is True
    assert env["feedback_id"] == "fbk_01TEST"
    assert "code" not in env  # never an error envelope

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "goal": "compose a momentum strategy",
        "kind": "friction",
        "severity": "low",
        "context_ref": "keel_backtest_run",
        "text": "watch output was confusing",
    }


@respx.mock
def test_round_trip_malformed_input_is_passed_through_and_still_succeeds(_api_env):
    """The server is the single normalization point: an off-enum kind is
    sent verbatim; keel-api answers 200 + note (M2.5 contract) and the
    tool surfaces that note in a SUCCESS envelope."""
    respx.post(f"{API}/v1/feedback").mock(
        return_value=Response(
            200,
            json={
                "status": "ok",
                "feedback_id": "fbk_01NOTE",
                "note": "kind 'rant' is not one of friction, praise, bug; stored as-is",
            },
        )
    )
    env = _call_mcp({"kind": "rant", "text": "everything"})
    assert env["status"] == "ok"
    assert env["delivered"] is True
    assert env["feedback_id"] == "fbk_01NOTE"
    assert "kind 'rant'" in env["note"]
    assert "code" not in env


@respx.mock
def test_round_trip_server_500_returns_success_with_note(_api_env):
    respx.post(f"{API}/v1/feedback").mock(return_value=Response(500, text="boom"))
    env = _call_mcp({"kind": "bug", "text": "api down?"})
    assert env["status"] == "ok"
    assert env["delivered"] is False
    assert env["feedback_id"] is None
    assert "could not be delivered" in env["note"]
    assert "do not block" in env["note"]
    assert "code" not in env


@respx.mock
def test_round_trip_network_down_returns_success_with_note(_api_env):
    import httpx

    respx.post(f"{API}/v1/feedback").mock(side_effect=httpx.ConnectError("no route"))
    env = _call_mcp({"kind": "friction", "text": "offline"})
    assert env["status"] == "ok"
    assert env["delivered"] is False
    assert "could not be delivered" in env["note"]
    assert "code" not in env


def test_unauthenticated_returns_success_with_note(monkeypatch):
    """No credentials at all — AuthError becomes success-with-note, not
    a login demand (nothing may gate on feedback)."""
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    env = _call_mcp({"kind": "praise", "text": "nice"})
    assert env["status"] == "ok"
    assert env["delivered"] is False
    assert "could not be delivered" in env["note"]
    assert "code" not in env


@respx.mock
def test_empty_call_still_succeeds_with_guidance_note(_api_env):
    """A contentless call is delivered (the empty record itself is
    friction signal) and the note nudges toward providing `text`."""
    respx.post(f"{API}/v1/feedback").mock(
        return_value=Response(200, json={"status": "ok", "feedback_id": "fbk_01EMPTY"})
    )
    env = _call_mcp({})
    assert env["status"] == "ok"
    assert env["delivered"] is True
    assert "no `text` was provided" in env["note"]


def test_programming_errors_are_not_swallowed(_api_env):
    """Never-fails covers DELIVERY, not bugs: a TypeError inside the
    handler must surface through the adapter's internal_error envelope
    (sibling pattern), not masquerade as delivered feedback."""

    def _broken_get_client(self):
        raise TypeError("bug in our code")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ToolContext, "get_client", _broken_get_client)
        env = _call_mcp({"kind": "bug", "text": "x"})
    assert env.get("code") == "internal_error"
    assert "bug in our code" in env["message"]


# ─── Registration surface: toolset `always`, every profile ──────────────


def test_tool_is_always_loaded_on_every_profile(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    assert TOOL.toolset == "always"
    assert TOOL.local_only is False
    assert "keel_feedback" in LISTED_PROFILE_TOOLS

    for mode in (None, "hosted"):
        if mode:
            monkeypatch.setenv("KEEL_EXECUTION_MODE", mode)
        else:
            monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
        for profile in ("full", "listed"):
            monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
            # Even the narrowest toolset env keeps `always` tools loaded.
            monkeypatch.setenv("KEEL_TOOLSETS", "read-only")
            assert "keel_feedback" in loaded_tool_names(OUTCOMES), (mode, profile)


def test_read_bucket_action_so_no_scope_can_gate_it():
    """audit.read = the lowest consent bucket (same as keel_status /
    keel_doctor / keel_help): every authenticated caller can file
    feedback — a write-scope grant must never gate it (spec 02 R4)."""
    assert TOOL.required_action == "audit.read"


def test_cli_command_registers():
    """`keel feedback` is the CLI face of the same outcome."""
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    assert "feedback" in root.commands


def test_schema_has_no_required_fields():
    """Never-fails extends to arguments: nothing is `required`, so the
    adapter's usage_error pre-flight can never reject a sparse call."""
    assert TOOL.input_schema["required"] == []
    assert set(TOOL.input_schema["properties"]) == {
        "goal",
        "kind",
        "severity",
        "context_ref",
        "text",
    }
    assert TOOL.input_schema["properties"]["kind"]["enum"] == ["friction", "praise", "bug"]


# ─── Surface self-identification header (spec 08 R5 carry-forward) ──────


@respx.mock
def test_local_mcp_surface_header_on_the_feedback_request(_api_env, monkeypatch):
    """The local MCP entrypoint (`keel mcp serve`) declares `local-mcp`;
    the actual POST /v1/feedback must carry `x-keel-surface: local-mcp`
    so keel-api's derive_surface classifies the row/event correctly.
    Self-identification metadata only — the SDK collects nothing (DP2)."""
    import keel.surface as surface_mod

    monkeypatch.setattr(surface_mod, "_SURFACE", None)
    surface_mod.set_surface("local-mcp")  # what mcp_cmd.serve() does
    route = respx.post(f"{API}/v1/feedback").mock(
        return_value=Response(200, json={"status": "ok", "feedback_id": "fbk_01HDR"})
    )
    env = _call_mcp({"kind": "praise", "text": "header check"})
    assert env["delivered"] is True
    assert route.calls.last.request.headers["x-keel-surface"] == "local-mcp"


def test_cli_entrypoint_is_not_relabelled_local_mcp(monkeypatch):
    """Only `keel mcp serve` sets local-mcp; the CLI entrypoint stays
    `cli` (mcp_cmd.serve overrides AFTER cli main set it, never the
    reverse)."""
    import keel.surface as surface_mod

    monkeypatch.setattr(surface_mod, "_SURFACE", None)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    surface_mod.set_surface("cli")  # what cli main() does
    assert surface_mod.current_surface() == "cli"
    surface_mod.set_surface("local-mcp")  # what mcp_cmd.serve() then does
    assert surface_mod.current_surface() == "local-mcp"
