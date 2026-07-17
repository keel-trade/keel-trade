"""`keel_plan_status` (spec 04 R2/R3) — plan facts + per-surface link policy.

The contract under test:

* full MCP round-trip (create_server → call_tool → keel-api mock):
  remaining quotas for a mid-consumption org come back EXACTLY as the
  server computed them — the SDK invents no numbers;
* per-surface manage_url/checkout policy: included on the full profile
  (CLI, local MCP, unlisted hosted endpoint) and on a listed
  registration declared KEEL_LISTED_CLIENT=claude; OMITTED on a listed
  registration declared chatgpt AND on a listed registration with no
  declared client (fail-safe default — research/08 indirect-upsell
  clause);
* on suppressed surfaces the ENTIRE serialized output passes a
  banned-phrase scan (no "upgrade now"-class marketing, no billing/
  checkout URLs) and `talking_points` carry facts validated by the
  SAME honesty rules as the spec 03 handoff envelope;
* older keel-api without the plan_status block → degraded output from
  the fields /v1/me does carry, with an explicit note — never
  client-side reconstructions of prices/limits.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
import respx
from httpx import Response
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    listed_client,
    manage_links_allowed,
)


_bootstrap()

TOOL = OUTCOMES["keel_plan_status"]

API = "https://api.test.keel"

# The server-computed plan_status block for a mid-consumption free org
# (25 of 30 runs spent, 2400 of 3000 compute seconds spent, 1 of 1 live
# slot used) — numbers as platform_auth.pricing.plan_status_summary
# produces them from plans.yaml + the enforced fee schedule.
PLAN_STATUS_BLOCK = {
    "plan": "free",
    "builder_fee_bps": 5,
    "limits": {
        "backtest_runs": 30,
        "compute_seconds": 3000,
        "live_slots": 1,
        "ai_messages": 15,
    },
    "remaining": {"backtest_runs": 5, "compute_seconds": 600, "live_slots": 0},
    "upgrade_options": [
        {
            "plan": "starter",
            "price": {"usd_per_month": 29, "usd_per_month_billed_annually": 23},
            "what_changes": {
                "backtest_runs": {"from": 30, "to": 150},
                "compute_seconds": {"from": 3000, "to": 10000},
                "live_slots": {"from": 1, "to": 3},
                "ai_messages": {"from": 15, "to": 50},
                "builder_fee_bps": {"from": 5, "to": 3},
            },
        },
        {
            "plan": "trader",
            "price": {"usd_per_month": 79, "usd_per_month_billed_annually": 63},
            "what_changes": {
                "backtest_runs": {"from": 30, "to": "unlimited"},
            },
        },
    ],
    "manage_url": "https://app.usekeel.io/settings?tab=billing",
}


def _me_response(plan_status: dict | None = PLAN_STATUS_BLOCK) -> dict:
    body = {
        "principal": {"id": "prn_1", "type": "user"},
        "org": {"id": "org_1", "name": "Test Org", "plan": "free", "status": "active"},
        "entitlements": [
            {
                "unit": "backtest_runs",
                "type": "consumable",
                "granted": 30,
                "spent": 25,
                "reserved": 0,
                "available": 5,
            },
            {
                "unit": "backtest_compute_seconds",
                "type": "consumable",
                "granted": 3000,
                "spent": 2400,
                "reserved": 0,
                "available": 600,
            },
            {
                "unit": "live_strategies_max",
                "type": "cap",
                "granted": 1,
                "enabled": False,
                "cap_current": 1,
                "spent": 0,
                "reserved": 0,
                "available": 1,
            },
        ],
        "credential_scopes": None,
    }
    if plan_status is not None:
        body["plan_status"] = plan_status
    return body


@pytest.fixture()
def _api_env(monkeypatch):
    """Authenticated SDK config pointed at the mock API; full profile."""
    monkeypatch.setenv("KEEL_API_KEY", "test-key")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)
    monkeypatch.setattr("keel.client.time.sleep", lambda *_: None)


def _call_mcp() -> dict:
    """Full MCP round-trip: real FastMCP server, tools/call, JSON body."""
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        result = await server.call_tool("keel_plan_status", {})
        return json.loads(result.content[0].text)

    return asyncio.run(go())


# "upgrade now"-class marketing phrases (spec 04 AC) — scanned against the
# WHOLE serialized envelope. `upgrade_options` (an identifier) never
# matches: every pattern requires a following word, not an underscore.
BANNED_PHRASES_RE = re.compile(
    r"upgrade (now|today|your|to \w)"
    r"|\bunlock\b"
    r"|\bsubscribe now\b"
    r"|\bbuy now\b"
    r"|\bact now\b"
    r"|\blimited[- ]time\b"
    r"|\bdon'?t miss\b"
    r"|\bbest value\b"
    r"|\bsupercharge\b"
    r"|\bpowerful\b"
    r"|\bpremium\b"
    r"|\bgenerous\b",
    re.IGNORECASE,
)


# ─── Quota correctness (mock) — spec 04 AC #2, client half ──────────────


@respx.mock
def test_mid_consumption_numbers_pass_through_exactly(_api_env):
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    env = _call_mcp()
    assert env["plan"] == "free"
    assert env["remaining"] == {
        "backtest_runs": 5,
        "compute_seconds": 600,
        "live_slots": 0,
    }
    assert env["limits"]["backtest_runs"] == 30
    assert env["builder_fee_bps"] == 5
    options = {o["plan"]: o for o in env["upgrade_options"]}
    assert options["starter"]["price"]["usd_per_month"] == 29
    assert options["starter"]["what_changes"]["builder_fee_bps"] == {
        "from": 5,
        "to": 3,
    }
    assert options["trader"]["what_changes"]["backtest_runs"]["to"] == "unlimited"


@respx.mock
def test_full_profile_includes_manage_url_and_existing_checkout_pointer(_api_env):
    """spec 04 R3: the upgrade path is the EXISTING POST
    /v1/billing/checkout — the tool points at it, adds no billing logic."""
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    env = _call_mcp()
    assert env["manage_url"] == "https://app.usekeel.io/settings?tab=billing"
    assert env["hero_url"] == env["manage_url"]
    assert env["checkout"]["endpoint"] == "POST /v1/billing/checkout"
    assert "talking_points" not in env  # suppressed-surface field only


@respx.mock
def test_output_is_numbers_only_on_every_surface(_api_env, monkeypatch):
    """No marketing phrases even where manage_url is ALLOWED."""
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    for setup in ("full", "listed-claude"):
        if setup == "listed-claude":
            monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
            monkeypatch.setenv("KEEL_LISTED_CLIENT", "claude")
        blob = json.dumps(_call_mcp())
        hits = BANNED_PHRASES_RE.findall(blob)
        assert not hits, f"{setup}: marketing phrases leaked: {hits}"


# ─── ChatGPT-profile suppression (spec 04 AC #3) ────────────────────────


@pytest.mark.parametrize(
    "client_env",
    ["chatgpt", None],
    ids=["declared-chatgpt", "undeclared-fail-safe-default"],
)
@respx.mock
def test_listed_suppression_omits_links_and_scans_clean(_api_env, monkeypatch, client_env):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    if client_env:
        monkeypatch.setenv("KEEL_LISTED_CLIENT", client_env)
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))

    env = _call_mcp()

    # The field is OMITTED, not nulled (spec 04 R2).
    assert "manage_url" not in env
    assert "checkout" not in env
    assert "hero_url" not in env

    # Facts still flow: numbers, limits, upgrade_options-as-data.
    assert env["remaining"]["backtest_runs"] == 5
    assert env["upgrade_options"][0]["plan"] == "starter"

    # String-level scan of the ENTIRE output: no "upgrade now"-class
    # phrases, no billing/checkout URL smuggled through any field.
    blob = json.dumps(env)
    hits = BANNED_PHRASES_RE.findall(blob)
    assert not hits, f"marketing phrases on suppressed surface: {hits}"
    for token in ("settings?tab=billing", "stripe", "checkout", "/billing/"):
        assert token not in blob.lower(), f"billing link token {token!r} leaked"

    # talking_points: facts + human-only + do-nothing (the spec 03
    # honesty rules — shared validator, not a second shape).
    points = env["talking_points"]
    assert any("5 of 30 backtest runs remaining" in p for p in points)
    assert any("performed by a human" in p for p in points)
    assert any("Doing nothing is also fine" in p for p in points)
    from keel.tools.outcomes._handoff import validate_talking_points

    assert validate_talking_points(points) == points


@respx.mock
def test_listed_claude_keeps_manage_url(_api_env, monkeypatch):
    """Anthropic's policy supports owned-domain link-outs (research/08):
    the Claude-listed registration keeps manage_url."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_CLIENT", "claude")
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response()))
    env = _call_mcp()
    assert env["manage_url"] == "https://app.usekeel.io/settings?tab=billing"
    assert "talking_points" not in env


def test_listed_client_env_validation(monkeypatch):
    """chatgpt/claude/unset are the only accepted values — a typo raises
    instead of silently picking a policy branch."""
    monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)
    assert listed_client() is None
    monkeypatch.setenv("KEEL_LISTED_CLIENT", " ChatGPT ")
    assert listed_client() == "chatgpt"
    monkeypatch.setenv("KEEL_LISTED_CLIENT", "claude")
    assert listed_client() == "claude"
    monkeypatch.setenv("KEEL_LISTED_CLIENT", "openai")
    with pytest.raises(ValueError, match="KEEL_LISTED_CLIENT"):
        listed_client()


def test_manage_links_policy_matrix(monkeypatch):
    """full → always allowed (KEEL_LISTED_CLIENT irrelevant); listed →
    allowed only for claude; unset/chatgpt → suppressed."""
    cases = [
        ("full", None, True),
        ("full", "chatgpt", True),  # brand env is irrelevant off the listed profile
        ("listed", "claude", True),
        ("listed", "chatgpt", False),
        ("listed", None, False),  # fail-safe default for a new directory
    ]
    for profile, client_env, expected in cases:
        monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
        if client_env:
            monkeypatch.setenv("KEEL_LISTED_CLIENT", client_env)
        else:
            monkeypatch.delenv("KEEL_LISTED_CLIENT", raising=False)
        assert manage_links_allowed() is expected, (profile, client_env)


# ─── Older-API degraded path — no invented numbers ──────────────────────


@respx.mock
def test_older_api_without_plan_status_degrades_honestly(_api_env):
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=_me_response(plan_status=None)))
    env = _call_mcp()
    assert env["plan"] == "free"
    # remaining derives from the entitlement balances /v1/me DID send.
    assert env["remaining"] == {
        "backtest_runs": 5,
        "compute_seconds": 600,
        "live_slots": 0,
    }
    # Prices/limits/fees are server-sourced — absent server support they
    # are OMITTED, never reconstructed client-side.
    assert "builder_fee_bps" not in env
    assert "limits" not in env
    assert "upgrade_options" not in env
    assert "pricing.md" in env["note"]


@respx.mock
def test_older_api_unlimited_sentinel_is_labelled(_api_env):
    me = _me_response(plan_status=None)
    me["entitlements"][0]["granted"] = 2147483647
    me["entitlements"][0]["available"] = 2147483647
    respx.get(f"{API}/v1/me").mock(return_value=Response(200, json=me))
    env = _call_mcp()
    assert env["remaining"]["backtest_runs"] == "unlimited"


# ─── Registration / gating ──────────────────────────────────────────────


def test_read_only_toolset_listed_inclusion_and_read_bucket(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names

    assert TOOL.toolset == "read-only"
    assert TOOL.local_only is False
    assert TOOL.required_action == "audit.read"
    assert TOOL.annotations["readOnlyHint"] is True
    assert "keel_plan_status" in LISTED_PROFILE_TOOLS

    # Present on the default toolsets, both local and hosted, both profiles.
    for mode in (None, "hosted"):
        if mode:
            monkeypatch.setenv("KEEL_EXECUTION_MODE", mode)
        else:
            monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
        for profile in ("full", "listed"):
            monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
            monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
            assert "keel_plan_status" in loaded_tool_names(OUTCOMES), (mode, profile)


def test_cli_command_registers():
    """`keel plan status` is the CLI face of the same outcome."""
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    assert "plan" in root.commands
    assert "status" in root.commands["plan"].commands


def test_schema_takes_no_arguments():
    assert TOOL.input_schema["required"] == []
    assert TOOL.input_schema["properties"] == {}
