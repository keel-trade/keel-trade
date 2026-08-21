"""Widget bundle tests (spec 06 R2) — cards, dialect twins, gating,
and the non-negotiable URL fallback.

The deploy-preflight LISTED-absence proof lives in the policy scan
(tests/test_policy_scan.py — the hard gate); this file covers the
bundle machinery and the full-profile side of the matrix.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.widgets import (
    CARD_KINDS,
    CARD_TOOLS,
    LISTED_EXCLUDED_CARDS,
    build_card_html,
    card_resource_uri,
    openai_card_resource_uri,
    tool_ui_meta,
)


_bootstrap()

ALL_TOOLSETS_ENV = "read-only,backtest,share,live-read,live-write"

_PROFILE_KEYS = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS", "KEEL_APP_URL")


def _server_with_env(**env):
    """Build a real FastMCP server under a controlled env, restoring after."""
    from keel.mcp.server import create_server

    saved = {k: os.environ.get(k) for k in _PROFILE_KEYS}
    for k in _PROFILE_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        server = create_server()
        tools = asyncio.run(server.list_tools())
        resources = asyncio.run(server.list_resources())
        return server, tools, resources
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ui_resources(resources) -> dict[str, object]:
    return {str(r.uri): r for r in resources if str(r.uri).startswith("ui://")}


# ─── Bundle build ───────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", CARD_KINDS)
def test_card_html_builds_self_contained(kind):
    html = build_card_html(kind)
    # One bundle: shared adapter + the per-kind renderer, all inline.
    assert "window.KeelHost" in html
    assert f'data-card="{kind}"' in html
    assert 'id="link-row"' in html  # the fallback link row element
    # No external scripts/styles — self-contained is the contract.
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=", html)


def test_unknown_card_kind_raises():
    with pytest.raises(ValueError, match="Unknown card kind"):
        build_card_html("equity")


def test_adapter_maps_openai_dialect():
    """The thin window.openai adapter (spec 06 R2): callTool ↔
    tools/call, openExternal ↔ ui/open-link, sendFollowUpMessage ↔ the
    message method — all present in every card's inline adapter."""
    html = build_card_html("backtest")
    for needle in (
        "window.openai.callTool",
        'callTool: "tools/call"',
        "window.openai.openExternal",
        'openLink: "ui/open-link"',
        "window.openai.sendFollowUpMessage",
        'initialize: "ui/initialize"',
    ):
        assert needle in html, f"adapter mapping missing: {needle}"


def test_adapter_renders_fallback_without_host_data():
    """SIMULATED widget failure, HTML half: when no host ever delivers
    tool output (the research/05 §1 handshake bug), the adapter's
    timeout branch renders the fallback link row anyway."""
    html = build_card_html("backtest")
    assert "if (!envelopeDelivered) renderLinkRow(null);" in html


# ─── Card ↔ tool wiring ─────────────────────────────────────────────────


def test_card_tools_exist_in_catalog():
    unknown = set(CARD_TOOLS) - set(OUTCOMES)
    assert not unknown, f"CARD_TOOLS references unknown tools: {sorted(unknown)}"
    assert set(CARD_TOOLS.values()) == set(CARD_KINDS)


def test_tool_ui_meta_carries_both_dialects(monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    for tool_name, kind in CARD_TOOLS.items():
        meta = tool_ui_meta(tool_name)
        assert meta is not None
        assert meta["ui"]["resourceUri"] == card_resource_uri(kind)
        assert meta["openai/outputTemplate"] == openai_card_resource_uri(kind)
    assert tool_ui_meta("keel_status") is None


def test_tool_ui_meta_listed_excludes_preflight(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert tool_ui_meta("keel_live_deploy") is None
    # Non-excluded cards keep their meta on the listed profile.
    assert tool_ui_meta("keel_backtest_summarize") is not None


# ─── Registration matrix (real servers) ─────────────────────────────────


def test_full_profile_all_toolsets_registers_all_cards():
    _, tools, resources = _server_with_env(
        KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV
    )
    uris = set(_ui_resources(resources))
    expected = {card_resource_uri(k) for k in CARD_KINDS} | {
        openai_card_resource_uri(k) for k in CARD_KINDS
    }
    assert uris == expected

    metas = {t.name: (t.to_mcp_tool().meta or {}) for t in tools}
    for tool_name, kind in CARD_TOOLS.items():
        assert metas[tool_name].get("ui", {}).get("resourceUri") == card_resource_uri(kind)
        assert metas[tool_name].get("openai/outputTemplate") == openai_card_resource_uri(kind)
    # Non-card tools advertise no widget.
    assert "ui" not in metas["keel_status"]


def test_full_profile_default_toolsets_omits_preflight():
    """No live-write toolset → keel_live_deploy isn't registered, so
    the preflight card resource must not exist either (a card never
    exists without its tool — truthful surface)."""
    _, tools, resources = _server_with_env(KEEL_SERVER_PROFILE="full")
    uris = set(_ui_resources(resources))
    assert card_resource_uri("preflight") not in uris
    assert openai_card_resource_uri("preflight") not in uris
    assert card_resource_uri("backtest") in uris
    assert "keel_live_deploy" not in {t.name for t in tools}


def test_card_mime_types_per_dialect():
    _, _, resources = _server_with_env(
        KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV
    )
    ui = _ui_resources(resources)
    for kind in CARD_KINDS:
        assert ui[card_resource_uri(kind)].mime_type == "text/html;profile=mcp-app"
        assert ui[openai_card_resource_uri(kind)].mime_type == "text/html+skybridge"


def test_dialect_twins_serve_identical_html():
    _, _, resources = _server_with_env(
        KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV
    )
    ui = _ui_resources(resources)

    def _text(resource) -> str:
        return asyncio.run(resource.read()).contents[0].content

    for kind in CARD_KINDS:
        a = _text(ui[card_resource_uri(kind)])
        b = _text(ui[openai_card_resource_uri(kind)])
        assert a == b, f"{kind}: dialect twins drifted"


def test_csp_declares_app_origin_from_env():
    """Cards fetch/frame ONLY the Keel app origin (spec 06 R2: embed
    endpoints declared in the widget CSP), and the origin follows
    KEEL_APP_URL — staging deployments declare the staging app."""
    _, _, resources = _server_with_env(
        KEEL_SERVER_PROFILE="full",
        KEEL_TOOLSETS=ALL_TOOLSETS_ENV,
        KEEL_APP_URL="https://staging-app.tailf4d598.ts.net",
    )
    ui = _ui_resources(resources)
    meta = ui[card_resource_uri("backtest")].meta
    csp = meta["ui"]["csp"]
    assert csp["connectDomains"] == ["https://staging-app.tailf4d598.ts.net"]
    assert csp["frameDomains"] == ["https://staging-app.tailf4d598.ts.net"]
    openai_meta = ui[openai_card_resource_uri("backtest")].meta
    assert openai_meta["openai/widgetCSP"]["connect_domains"] == [
        "https://staging-app.tailf4d598.ts.net"
    ]


def test_listed_excluded_cards_is_exactly_preflight():
    """Deliberate-edit guard: widening the widget surface on the listed
    profile must show up in this file's diff."""
    assert LISTED_EXCLUDED_CARDS == {"preflight"}


# ─── Simulated widget failure → fallback URL line (spec 06 AC) ──────────


def test_tool_text_payload_carries_fallback_url_line():
    """The AC's simulated-failure path, server half: a host that never
    renders the widget still shows the tool's TEXT payload, so the
    plain URL line must be present in the raw text of a real
    tools/call result."""
    server, _, _ = _server_with_env(KEEL_SERVER_PROFILE="full")
    tool = asyncio.run(server.get_tool("keel_backtest_summarize"))
    detail = {
        "id": "btr_fallback",
        "status": "completed",
        "strategy_id": "str_1",
        "metrics": {"sharpe": 2.1},
    }
    with patch("keel.client.KeelClient.get", side_effect=[detail, {}]):
        result = asyncio.run(tool.run({"backtest_id": "btr_fallback"}))
    text = result.content[0].text
    assert "View in Keel: https://" in text
    envelope = json.loads(text)
    assert envelope["url_line"].startswith("View in Keel: https://")
    assert envelope["render"]["card"] == "backtest"
    assert envelope["render"]["fallback_url"] == envelope["hero_url"]
