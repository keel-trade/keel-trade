"""`keel_account_status` as a view tool with a profile-derived capability block
(spec 02 §2.5, guard G6).

`capabilities` is derived from the tools the ACTIVE profile loads — never
from `KEEL_TOOLSETS`, which the listed profile ignores. On listed,
`workflow_routes` and `toolsets_loaded` are absent (R-22); on full, routes
are `{name, when, tools}` with every tool loaded. `consumed_by` for the live
slot unit is free of the listed surface's banned verbs.

SEED (run 2026-09-23, reverted by reversing the edit): derive `monitor` from
`load_toolsets()` (`"read-write" if "live-write" in active else …`) —
`test_listed_capabilities_ignore_the_toolsets_env` reds (listed with
`KEEL_TOOLSETS=…,live-write` would claim read-write) while the full-profile
CONTROL stays green.
"""

from __future__ import annotations

import json
import re

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._status_view import build_status_view, quota_sentences
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


#: The listed surface's word rule for payload strings (spec 04 §2.5 / R-31).
FORBIDDEN = re.compile(r"\bdeploy(s|ed|ing|ment)?\b", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _registry():
    _bootstrap()


def _status(monkeypatch, *, profile: str, toolsets: str | None, entitlements=None) -> dict:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    if toolsets is None:
        monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    else:
        monkeypatch.setenv("KEEL_TOOLSETS", toolsets)
    monkeypatch.setenv("KEEL_API_KEY", "k")
    monkeypatch.setattr(
        "keel.auth.get_identity",
        lambda: {"org": {"id": "org_1", "name": "Acme", "plan": "free"}, "principal": {"id": "p"}},
    )
    monkeypatch.setattr(
        "keel.client.KeelClient.get",
        lambda self, path, **kw: entitlements or {"balances": []},
    )
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    return OUTCOMES["keel_account_status"].handler({}, ctx).to_envelope()


def test_listed_capabilities_ignore_the_toolsets_env(monkeypatch) -> None:
    body = _status(
        monkeypatch, profile="listed", toolsets="read-only,backtest,share,live-read,live-write"
    )
    assert body["capabilities"] == {
        "research": True,
        "backtest": True,
        "monitor": "read-only",
        "live_actions": "web app",
    }
    assert body["profile"] == "listed"
    assert "workflow_routes" not in body and "toolsets_loaded" not in body
    assert body["live_trading_allowed"] is False
    # Non-vacuity: the listed surface is what was loaded.
    assert set(body["tools_visible"]) == LISTED_PROFILE_TOOLS
    # Q-1960: a fact about this connector's tools, never a session permission.
    # 04 §5.3 (D-12): the same no-trading boundary the instructions state.
    assert (
        "This connector: research and backtests; it cannot place orders, move funds or "
        "connect wallets; strategies that are running are managed in the Keel web app."
        in body["view"]["markdown"]
    )
    assert "read-only" not in body["view"]["markdown"]


def test_full_profile_with_live_write_acts_here(monkeypatch) -> None:
    """CONTROL: the full profile with live-write loaded really is read-write."""
    body = _status(
        monkeypatch, profile="full", toolsets="read-only,backtest,share,live-read,live-write"
    )
    assert body["capabilities"]["live_actions"] == "here"
    assert body["capabilities"]["monitor"] == "read-write"
    names = set(body["tools_visible"])
    for route in body["workflow_routes"]:
        assert set(route) == {"name", "when", "tools"}
        assert set(route["tools"]) <= names
    (live,) = [r for r in body["workflow_routes"] if r["name"] == "live_trading"]
    assert live["when"] == "live actions on a running strategy"


def test_the_status_view_template(monkeypatch) -> None:
    entitlements = {
        "balances": [
            {
                "unit": "backtest_runs",
                "granted": 50,
                "spent": 44,
                "available": 6,
                "reserved": 1,
                "period": "weekly",
                "resets_at": "2026-09-28T00:00:00Z",
            },
            {
                "unit": "backtest_compute_seconds",
                "granted": 3600,
                "spent": 600,
                "available": 3000,
                "period": "weekly",
                "resets_at": "2026-09-28T00:00:00Z",
            },
            {"unit": "live_strategies_max", "granted": 2, "spent": 0, "available": 2},
        ]
    }
    body = _status(monkeypatch, profile="listed", toolsets=None, entitlements=entitlements)
    lines = body["view"]["markdown"].strip().splitlines()
    assert lines[0] == "**Keel** · signed in as Acme · free plan"
    # Two units sharing a period and a reset are ONE sentence; `reserved` rides.
    assert "6 of 50 backtest runs (1 reserved by runs in flight) and 3,000 of 3,600" in lines[1]
    assert lines[1].endswith(
        "Quota is per org and shared across the web app, the CLI and other chats."
    )
    # The strategies list, never `/settings` — it opens on the billing tab
    # (D-12 §4.4: no agent surface links a plan destination).
    assert lines[-1] == "View in Keel: https://app.usekeel.io/strategies"
    assert "upgrade_url" not in body["entitlements"]
    consumed = [
        e["consumed_by"]
        for e in body["entitlements"]["summary"]
        if e["unit"] == "live_strategies_max"
    ][0]
    assert consumed == [
        "local MCP server with the live tools loaded",
        "CLI",
        "web app live strategies",
    ]
    assert not FORBIDDEN.search(json.dumps(body["entitlements"]))


def test_next_is_one_string(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    body = OUTCOMES["keel_account_status"].handler({}, ToolContext(is_tty=False)).to_envelope()
    assert body["next"] == (
        "Not signed in — the connector's own sign-in (reconnect it in the client) starts a session."
    )


def test_a_unit_the_server_did_not_send_is_omitted() -> None:
    """Q-1590: never an invented number."""
    assert quota_sentences([]) is None
    assert quota_sentences([{"unit": "backtest_runs", "granted": None}]) is None
    view = build_status_view(
        {"authenticated": True, "capabilities": {}}, profile="listed", url=None
    )
    assert view["markdown"] == "**Keel** · signed in\n"


def test_a_failed_probe_is_a_line_of_the_text_block() -> None:
    """claude.ai reads only the text block: a failed identity or quota probe
    must be IN the markdown, not only a `structuredContent` field.

    SEED (run 2026-09-23, reverted by reversing the edit): delete the
    `identity_error` append in `build_status_view` — this reds on the
    identity arm while the clean CONTROL below stays green.
    """
    failed = build_status_view(
        {
            "authenticated": True,
            "capabilities": {},
            "identity_error": "connection refused",
            "entitlements_error": "503 from the entitlements read",
        },
        profile="listed",
        url=None,
    )["markdown"].splitlines()
    assert failed == [
        "**Keel** · signed in",
        "Identity check failed: connection refused",
        "Quota could not be read: 503 from the entitlements read",
    ]
    # CONTROL: no error fields ⇒ no error lines.
    clean = build_status_view(
        {"authenticated": True, "capabilities": {}}, profile="listed", url=None
    )
    assert "failed" not in clean["markdown"] and "could not" not in clean["markdown"]


def test_status_is_a_text_view_with_no_card_under_the_meta_move(monkeypatch) -> None:
    """R4 S2 (ChatGPT drew no card for `keel_account_status`): by spec 02 §2.5 the
    status tool is a view with NO card — `CARD_TOOLS` does not name it, so
    no `openai/outputTemplate` is published and ChatGPT shows its bare
    "Called tool" row. What must hold with `KEEL_CARD_META_MOVE=1` (staging)
    is the view: the text block IS the status markdown, and an old
    connector's frozen `{"result": string}` shape is kept."""
    from keel.tools.outcomes._mcp_adapter import registration_mode, result_for_mode
    from keel.widgets import tool_ui_meta

    monkeypatch.setenv("KEEL_CARD_META_MOVE", "1")
    monkeypatch.delenv("KEEL_NONVIEW_TEXT_ONLY", raising=False)
    body = _status(monkeypatch, profile="listed", toolsets=None)
    assert body["view"]["kind"] == "status"

    assert tool_ui_meta("keel_account_status") is None
    mode = registration_mode("keel_account_status")
    assert mode == "status_wrapped"
    result = result_for_mode(mode, json.dumps(body, default=str), "keel_account_status")
    text = result.content[0].text
    assert text.lstrip().startswith("**Keel** · signed in as Acme · free plan"), text
    assert set(result.structured_content) == {"result"}
    assert json.loads(result.structured_content["result"])["view"]["kind"] == "status"


def test_mcp_status_drops_the_live_permission_booleans(monkeypatch, tmp_path) -> None:
    """Q-1964: `live_monitoring_allowed` / `live_trading_allowed` stay in the
    CLI envelope and never reach an MCP client. On ChatGPT the model reads
    structuredContent, and a field NAMED "live trading allowed: false" came
    back as "this session is read-only for live trading".

    SEED (run 2026-09-26, reverted by reversing the edit): remove both names
    from `_STATUS.drop` in `_channels.py`. This test reds on the MCP
    assertion, while the CONTROL (the CLI envelope still carries both) and
    the non-vacuity arm (`capabilities` served) stay green.
    """
    import asyncio

    from keel.mcp.server import create_server

    # CONTROL: the CLI envelope keeps both booleans.
    envelope = _status(monkeypatch, profile="listed", toolsets=None)
    assert envelope["live_trading_allowed"] is False
    assert envelope["live_monitoring_allowed"] is True

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    result = asyncio.run(create_server().call_tool("keel_account_status", {}))
    structured = result.structured_content or {}
    wrapped = structured.get("result")
    payload = json.loads(wrapped) if isinstance(wrapped, str) else structured
    # Non-vacuous: this is the served status payload, capabilities included.
    assert payload["capabilities"]["monitor"] == "read-only"
    served = json.dumps(payload) + "".join(getattr(c, "text", "") for c in result.content)
    assert "live_trading_allowed" not in served
    assert "live_monitoring_allowed" not in served
