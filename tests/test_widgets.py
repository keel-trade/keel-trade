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
import pathlib
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.widgets import (
    CARD_KINDS,
    CARD_TOOLS,
    HOST_FONT_ORIGIN,
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
    # Q-1769: the timeout is conditional on no call being in flight; the
    # miss itself (and its fallback row) lives in `showMiss`.
    assert 'if (!envelopeDelivered && !callInFlight) showMiss("lost");' in html
    assert "function showMiss(reason)" in html
    assert "renderLinkRow(null);" in html


# ─── Card ↔ tool wiring ─────────────────────────────────────────────────


def test_card_tools_exist_in_catalog():
    unknown = set(CARD_TOOLS) - set(OUTCOMES)
    assert not unknown, f"CARD_TOOLS references unknown tools: {sorted(unknown)}"
    assert set(CARD_TOOLS.values()) == set(CARD_KINDS)


def test_every_strategy_shaped_tool_carries_the_strategy_card():
    """mcp-strategy-view V-4 / Q-1619: `keel_strategy_get` was 2 % of a
    measured user's calls while compose alone was 28 %, so the card sat
    on the one tool nobody called. Compose, fork, diff and library_fork
    return the same `view` block and render through the same card."""
    strategy_tools = {t for t, k in CARD_TOOLS.items() if k == "strategy"}
    assert strategy_tools == {
        "keel_strategy_get",
        "keel_strategy_compose",
        "keel_strategy_fork",
        "keel_strategy_diff",
        "keel_library_fork",
    }
    for tool_name in strategy_tools:
        assert tool_ui_meta(tool_name)["ui"]["resourceUri"] == card_resource_uri("strategy"), (
            tool_name
        )


def test_one_resource_per_kind_however_many_tools_share_it():
    """Five tools, one strategy card: the resource list must not gain a
    duplicate registration per owning tool."""
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    uris = [str(r.uri) for r in resources if str(r.uri).startswith("ui://")]
    assert len(uris) == len(set(uris)) == 2 * len(CARD_KINDS)


def test_tool_ui_meta_carries_both_dialects(monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    for tool_name, kind in CARD_TOOLS.items():
        meta = tool_ui_meta(tool_name)
        assert meta is not None
        assert meta["ui"]["resourceUri"] == card_resource_uri(kind)
        assert meta["openai/outputTemplate"] == openai_card_resource_uri(kind)
    assert tool_ui_meta("keel_account_status") is None


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
    assert "ui" not in metas["keel_account_status"]


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
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)
    for kind in CARD_KINDS:
        assert ui[card_resource_uri(kind)].mime_type == "text/html;profile=mcp-app"
        assert ui[openai_card_resource_uri(kind)].mime_type == "text/html+skybridge"


def test_dialect_twins_serve_identical_html():
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)

    def _text(resource) -> str:
        return asyncio.run(resource.read()).contents[0].content

    for kind in CARD_KINDS:
        a = _text(ui[card_resource_uri(kind)])
        b = _text(ui[openai_card_resource_uri(kind)])
        assert a == b, f"{kind}: dialect twins drifted"


def test_csp_allows_only_the_host_font_and_cards_are_borderless():
    """Q-1505 + Q-1631 (DELIBERATE CHANGE, 2026-09-20): a card fetches
    nothing and frames nothing, so `connectDomains` stays empty — but it
    is NOT resource-free. The adapter injects the host's own `@font-face`
    block verbatim, and Claude serves Anthropic Sans from
    `https://assets.claude.ai`; with an empty `resourceDomains` the font
    file is blocked by the iframe CSP and the card silently paints in a
    fallback face while the conversation around it does not. The origin
    is therefore declared by name on the MCP Apps resource. The ChatGPT
    twin declares NO resource origin (O-1, 2026-09-25): only Claude's
    hostContext injects that font, ChatGPT requires system fonts, and its
    review checks the declared CSP against behaviour. Both stay
    borderless — Claude web draws no border by default and the card is
    transparent."""
    _, _, resources = _server_with_env(
        KEEL_SERVER_PROFILE="full",
        KEEL_TOOLSETS=ALL_TOOLSETS_ENV,
        KEEL_APP_URL="https://staging-app.tailf4d598.ts.net",
    )
    ui = _ui_resources(resources)
    meta = ui[card_resource_uri("backtest")].meta
    # SEED: `resourceDomains: []` in `_csp_meta()` (the pre-Q-1631 value)
    # reds this and test_every_card_kind_declares_the_font_origin.
    assert meta["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [HOST_FONT_ORIGIN],
    }
    assert HOST_FONT_ORIGIN == "https://assets.claude.ai"
    assert "frameDomains" not in meta["ui"]["csp"]
    assert meta["ui"]["prefersBorder"] is False
    openai_meta = ui[openai_card_resource_uri("backtest")].meta
    widget_csp = openai_meta["openai/widgetCSP"]
    assert widget_csp["connect_domains"] == []
    assert widget_csp["resource_domains"] == []
    assert "https://staging-app.tailf4d598.ts.net" in widget_csp["redirect_domains"]
    assert openai_meta["openai/widgetPrefersBorder"] is False


def test_every_card_kind_declares_the_font_origin():
    """Not vacuous: every kind carries it on the MCP Apps resource — a
    single kind passing would hide the rest as blocked fonts — and no
    kind carries it on the ChatGPT twin (system fonts there)."""
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)
    seen = 0
    for kind in CARD_KINDS:
        assert ui[card_resource_uri(kind)].meta["ui"]["csp"]["resourceDomains"] == [
            HOST_FONT_ORIGIN
        ], kind
        assert (
            ui[openai_card_resource_uri(kind)].meta["openai/widgetCSP"]["resource_domains"] == []
        ), kind
        seen += 1
    assert seen == len(CARD_KINDS) >= 5


# ─── ChatGPT submission metadata (O-1) ─────────────────────────────────
#
# OpenAI's plugin reference: `_meta.ui.domain` / `openai/widgetDomain` is
# the "Dedicated origin for hosted components (required when submitting a
# plugin with UI; must be unique per plugin)", and review "checks the
# declared policy against the UI behavior". Claude's `ui.domain` is a
# different, strictly validated format (`{hash}.claudemcpcontent.com`; any
# other value replaces the card with an error), so the domain rides the
# ChatGPT twin only and the Claude resource is pinned byte-for-byte below.

_KEEL_OWNED = re.compile(r"^https://([a-z0-9-]+\.)*usekeel\.io$")

#: The MCP Apps (Claude) resource `_meta`, exactly as served under the prod
#: defaults. The control arm: an O-1 twin change must leave it untouched.
_CLAUDE_RESOURCE_META = {
    "ui": {
        "csp": {"connectDomains": [], "resourceDomains": ["https://assets.claude.ai"]},
        "prefersBorder": False,
    }
}


def _twin_domains(meta: dict) -> list[str]:
    csp = meta["openai/widgetCSP"]
    return [
        *csp["connect_domains"],
        *csp["resource_domains"],
        *csp.get("frame_domains", []),
        *csp["redirect_domains"],
        meta["openai/widgetDomain"],
    ]


def test_the_chatgpt_twin_declares_a_keel_widget_domain():
    """Every twin names the Keel app origin as its dedicated widget origin
    (prod default), and the domain follows `KEEL_APP_URL` per environment."""
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)
    seen = 0
    for kind in CARD_KINDS:
        meta = ui[openai_card_resource_uri(kind)].meta
        assert meta["openai/widgetDomain"] == "https://app.usekeel.io", kind
        seen += 1
    assert seen == len(CARD_KINDS) >= 5

    _, _, staged = _server_with_env(
        KEEL_SERVER_PROFILE="full",
        KEEL_TOOLSETS=ALL_TOOLSETS_ENV,
        KEEL_APP_URL="https://staging-app.tailf4d598.ts.net/app",
    )
    staged_meta = _ui_resources(staged)[openai_card_resource_uri("backtest")].meta
    assert staged_meta["openai/widgetDomain"] == "https://staging-app.tailf4d598.ts.net"


def test_the_chatgpt_twin_declares_only_keel_production_origins():
    """Under the prod defaults every origin the twin declares (CSP lists +
    widget domain) is a Keel-owned `usekeel.io` origin — no Claude-owned
    font host, no dev/staging tailnet host, no third party."""
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)
    checked = 0
    for kind in CARD_KINDS:
        domains = _twin_domains(ui[openai_card_resource_uri(kind)].meta)
        assert domains, kind  # not vacuous: redirect + widget domain at least
        for origin in domains:
            assert _KEEL_OWNED.match(origin), f"{kind}: non-Keel origin {origin!r} on the twin"
            assert "claude" not in origin and "tailf4d598" not in origin, origin
            checked += 1
    assert checked >= 2 * len(CARD_KINDS)


def test_the_claude_resource_metadata_is_unchanged():
    """Control arm: the MCP Apps resource keeps its exact `_meta` — the
    host-font CSP Claude needs, no `ui.domain` (Claude would refuse a
    non-hash value), and no ChatGPT-only key."""
    _, _, resources = _server_with_env(KEEL_SERVER_PROFILE="full", KEEL_TOOLSETS=ALL_TOOLSETS_ENV)
    ui = _ui_resources(resources)
    for kind in CARD_KINDS:
        meta = ui[card_resource_uri(kind)].meta
        assert meta == _CLAUDE_RESOURCE_META, kind
        assert "domain" not in meta["ui"], kind


def test_the_host_background_token_is_not_mapped():
    """Q-1631, transparency half: the adapter maps the host's palette onto
    the card's tokens, but mapping `--bg` to the host's OPAQUE primary
    background cancelled the transparent-body rule the theming contract
    requires (and double-paints on a tinted slot). Every other documented
    token stays mapped."""
    html = build_card_html("backtest")
    # SEED: put `"--bg": ["--color-background-primary"],` back into
    # TOKEN_MAP — reds this and test_the_body_stays_transparent.
    token_map = html.split("var TOKEN_MAP = {", 1)[1].split("};", 1)[0]
    assert '"--bg"' not in token_map
    for token in ('"--fg"', '"--muted"', '"--line"', '"--tile"', '"--font"'):
        assert token in token_map, token
    assert "background: var(--bg)" in html and "--bg: transparent" in html


# ─── Q-1505: sizing, theming, no frames — the shared-adapter contract ───


@pytest.mark.parametrize("kind", CARD_KINDS)
def test_cards_frame_nothing_and_size_themselves(kind):
    """The served HTML: no nested iframe (the host blocks it and its
    failure was invisible), the size-changed notification + the Apps
    SDK intrinsic-height call are wired, and the document declares both
    color schemes so the host's theme applies before any script runs."""
    html = build_card_html(kind)
    assert "<iframe" not in html
    assert "mountEmbed" not in html
    assert "embed_url" not in html
    assert '"ui/notifications/size-changed"' in html
    assert "ResizeObserver" in html
    assert "notifyIntrinsicHeight" in html
    assert 'name="color-scheme" content="light dark"' in html
    assert "100vh" not in html
    # Host style variables map onto the card tokens (Claude ships them).
    assert "--color-text-primary" in html
    assert "hostContext" in html


def test_backtest_card_draws_the_curve_in_card():
    html = build_card_html("backtest")
    assert "equity-line" in html
    assert "createElementNS" in html
    assert "env.curve" in html
    # The rows the founder cut (Q-1505) are gone; period is formatted.
    assert '["Run", env.run_id]' not in html
    assert '["Engine", env.engine]' not in html
    assert "fmtPeriod" in html


def test_other_cards_follow_the_backtest_card_contract():
    """Q-1505 follow-up: the strategy card carries no source scroll box,
    the live card draws its equity sparkline in-card, the preflight card
    renders no account/intent ids — and none of the three frames or
    fetches anything (the render test proves the rendered DOM; this pins
    the served bundles)."""
    strategy = build_card_html("strategy")
    assert 'H.el("pre"' not in strategy and "pre.source" not in strategy.split("</style>")[1]
    # mcp-strategy-view M2: the flat arrow chip chain is GONE — the card now
    # draws the editor's block list, so `componentChain` is replaced by the
    # block/parallel renderer and the branch packer (PLAN §4.4). Branches,
    # params and pipeline shape were lost in the chain (Q-1619).
    assert "componentChain(" not in strategy
    assert "function parallel(" in strategy and "function parRow(" in strategy
    assert "function columnsFor(" in strategy and "function flowOf(" in strategy
    live = build_card_html("live")
    assert "equity-line" in live and "drawSpark(" in live
    preflight = build_card_html("preflight")
    assert "account_id" not in preflight.split("</style>")[1].split("KeelHost = KeelHost")[1]
    for html in (strategy, live, preflight):
        assert "<iframe" not in html and "fetch(" not in html


def test_card_composition_placeholders_are_the_five_the_render_test_uses():
    """services/keel-app/src/lib/__tests__/mcp-card.unit.test.ts composes
    the card from the same assets with the same five placeholders (it
    cannot import Python). Pin the contract so the two builds cannot
    drift: the template names exactly these, and the built HTML has none
    left."""
    from keel.widgets import _asset

    template = _asset("card.html.tmpl")
    for token in ("{{KIND}}", "{{TITLE}}", "{{CSS}}", "{{ADAPTER_JS}}", "{{CARD_JS}}"):
        assert token in template, token
    assert set(re.findall(r"\{\{[A-Z_]+\}\}", template)) == {
        "{{KIND}}",
        "{{TITLE}}",
        "{{CSS}}",
        "{{ADAPTER_JS}}",
        "{{CARD_JS}}",
    }
    for kind in CARD_KINDS:
        assert "{{" not in build_card_html(kind)


def test_listed_excluded_cards_is_exactly_preflight():
    """Deliberate-edit guard: widening the widget surface on the listed
    profile must show up in this file's diff."""
    assert LISTED_EXCLUDED_CARDS == {"preflight"}


# ─── Simulated widget failure → fallback URL line (spec 06 AC) ──────────


def test_tool_text_payload_carries_fallback_url_line():
    """The AC's simulated-failure path, server half: a host that never
    renders the widget still shows the tool's TEXT payload, so the
    plain URL line must be present in the raw text of a real
    tools/call result.

    Since BUILD §2.8 that text block is the view's MARKDOWN plus the
    operational fields, not the JSON envelope — a reader whose host
    dropped the card gets a readable result rather than a wall of
    braces. The envelope moved to ``structuredContent``, which is where
    the card's adapter already reads it from (``parseToolResult``), so
    both halves are asserted here: the text a person sees and the
    envelope a card renders.
    """
    server, _, _ = _server_with_env(KEEL_SERVER_PROFILE="full")
    tool = asyncio.run(server.get_tool("keel_backtest_summarize"))
    detail = {
        "id": "btr_fallback",
        "status": "completed",
        "strategy_id": "str_1",
        "metrics": {"sharpe": 2.1},
    }
    with patch("keel.client.KeelClient.get", side_effect=[detail, {}, {}]):
        result = asyncio.run(tool.run({"backtest_id": "btr_fallback"}))
    text = result.content[0].text
    assert "View in Keel: https://" in text
    # The link line closes the view's markdown, after whatever the result
    # had to say; only catalogue lines (`label: …`, here the run's `costs:`
    # since Q-2270) follow it.
    lines = text.strip().splitlines()
    link = next(i for i, line in enumerate(lines) if line.startswith("View in Keel: https://"))
    trailing = [line for line in lines[link + 1 :] if line]
    assert all(line.split(":", 1)[0].isidentifier() for line in trailing), trailing
    assert result.structured_content, "the tool result carries no structured content"
    # What the card reads: `_meta["keel/card"]` merged over the structured
    # envelope (host-adapter.js) — the same dict with the probe flag off,
    # and `render` rides the card meta with `KEEL_CARD_META_MOVE` on.
    from keel.tools.outcomes._channels import CARD_META_KEY, _merge

    envelope = json.loads(json.dumps(result.structured_content))
    _merge(envelope, (result.meta or {}).get(CARD_META_KEY) or {})
    assert envelope["url_line"].startswith("View in Keel: https://")
    assert envelope["render"]["card"] == "backtest"
    assert envelope["render"]["fallback_url"] == envelope["hero_url"]
    # And the text block is NOT the envelope: that is the change §2.8
    # made, and a regression would be invisible to the assertions above.
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


# ─── The card system: source-level pins (M3/M4, Q-1627 … Q-1634) ───────
#
# These pin the BYTES. The rules they cannot reach — a sign glyph in
# front of every coloured number, a remainder under every cap, an honest
# body when no result arrives — are about the rendered DOM and are proven
# by the browser guard at the bottom of this file.


@pytest.mark.parametrize("kind", ["backtest", "live", "preflight"])
def test_the_duplicated_formatters_are_gone(kind):
    """Q-1629 / Q-1634: one formatter, in the adapter. The two
    byte-identical `money()` copies (card-live.js, card-preflight.js) and
    the per-card sign/colour decisions they carried are deleted; every
    card reads `KeelHost.fmt`."""
    from keel.widgets import _asset

    card = _asset(f"card-{kind}.js")
    # SEED: paste the old `function money(v, digits)` back into
    # card-live.js — reds this parametrisation for kind="live".
    assert "function money(" not in card, f"card-{kind}.js still defines its own money()"
    assert "H.fmt" in card
    adapter = _asset("host-adapter.js")
    assert "var fmt = {" in adapter and "fmt: fmt," in adapter
    # The sign is derived from the rendered body, so colour cannot appear
    # without it — the rule, in the one place that implements it.
    assert "function signOf(" in adapter and "function signedDisplay(" in adapter


@pytest.mark.parametrize("kind", ["backtest", "live", "preflight"])
def test_no_card_stringifies_an_object_into_prose(kind):
    """Q-1630: `JSON.stringify` was the fallback for a warning with no
    message and for an object table cell — it printed wire JSON at the
    user. A value with no human form is COUNTED, not stringified."""
    from keel.widgets import _asset

    # SEED: restore `|| JSON.stringify(uw[0])` in card-preflight.js —
    # reds this parametrisation for kind="preflight".
    assert "JSON.stringify" not in _asset(f"card-{kind}.js")


def test_the_live_card_never_labels_a_wire_key():
    """Q-1630: the generic view printed up to 8 wire field names with
    their underscores swapped for spaces. Every rendered column now comes
    from the COLS table, which is labels, so an unlabelled field cannot
    reach the DOM."""
    from keel.widgets import _asset

    card = _asset("card-live.js")
    # SEED: restore the generic wire-key kv fallback in card-live.js —
    # reds this on the `k.replace` line.
    assert "k.replace(/_/g" not in card
    assert "c.replace(/_/g" not in card
    assert "var COLS = {" in card
    # The table helper is the adapter's, so the remainder rule is shared.
    assert "H.table(" in card and "function table(rows, cols, labels, cap)" not in card


def test_the_template_ships_a_skeleton_not_the_word_loading():
    """Q-1627: the body held a literal "Loading…" that the 4 s fallback
    never cleared. It ships a shape-stable skeleton instead, and the
    adapter replaces it with an honest line when no result arrives."""
    from keel.widgets import _asset

    template = _asset("card.html.tmpl")
    # SEED: put `<p class="muted">Loading&hellip;</p>` back in
    # card.html.tmpl — reds this line.
    assert "Loading" not in template and "&hellip;" not in template
    assert 'class="skeleton"' in template and 'aria-busy="true"' in template
    adapter = _asset("host-adapter.js")
    assert "No result reached this card." in adapter
    assert "function clearSkeleton(" in adapter
    # And the latch is gone: a second tool result re-renders.
    assert "if (envelopeDelivered || envelope == null) return;" not in adapter


def test_fullscreen_is_declared_and_consumed():
    """Q-1632: the display-mode plumbing was inert — nothing declared
    `availableDisplayModes`, and card.css read neither `[data-display-mode]`
    nor `--host-max-height`."""
    from keel.widgets import _asset

    adapter = _asset("host-adapter.js")
    # SEED: `appCapabilities: {}` in the handshake — reds this and
    # test_the_handshake_declares_both_display_modes.
    assert 'var APP_DISPLAY_MODES = ["inline", "fullscreen"];' in adapter
    assert "appCapabilities: { availableDisplayModes: APP_DISPLAY_MODES }" in adapter
    assert 'requestDisplayMode: "ui/request-display-mode"' in adapter
    assert "get availableDisplayModes()" in adapter
    css = _asset("card.css")
    assert ':root[data-display-mode="fullscreen"]' in css
    # Fullscreen is the ONE mode a card may scroll in: inline keeps the
    # no-nested-scroll rule, so `overflow-y: auto` appears only under a
    # fullscreen selector.
    blocks = [b for b in css.split("}") if "overflow-y: auto" in b]
    assert blocks, "fullscreen must allow vertical scroll"
    for block in blocks:
        assert 'data-display-mode="fullscreen"' in block, block


def test_the_action_row_is_a_real_target_with_a_focus_ring():
    """Q-1633: the one action was a 13 px inline link and card.css had no
    focus rule at all."""
    from keel.widgets import _asset

    css = _asset("card.css")
    # SEED: drop the `.button-link` block from card.css — reds this and
    # test_the_action_is_a_real_tap_target (measured height falls to ~18 px).
    assert ".button-link" in css
    assert "min-height: 36px" in css and "min-height: 44px" in css
    assert ":focus-visible" in css
    # A drawdown is neutral ink even in the chart readout (Q-1629).
    assert ".chart .readout .dd {\n  color: var(--muted);\n}" in css
    backtest = _asset("card-backtest.js")
    assert 'readout.setAttribute("aria-live", "polite")' in backtest
    assert 'tabindex: "0"' in backtest and 'hit.addEventListener("keydown"' in backtest
    assert 'th.setAttribute("scope", "col")' in _asset("host-adapter.js")


# ─── M3/M4 guard: the RENDERED cards (Q-1627 … Q-1634) ─────────────────
#
# Everything the card system promises is a property of the rendered DOM:
# a sign glyph in front of every coloured number, an em dash that KEEPS
# its tile, a named remainder under every cap, no id in visible text, a
# 36/44 px action, an honest body when nothing arrives, Expand only
# where the host lists fullscreen. The assertions below read
# measurements taken from real Chromium by
# ``tests/fixtures/cards/c_card_check.mjs``, which drives each card
# through the same ``ui/initialize`` → ``tool-result`` sequence a host
# uses.
#
# Skips (never silently passes) when node or Playwright is absent — the
# browser comes from ``services/keel-app/node_modules``, which a bare
# Python checkout does not have. Same contract as
# ``test_widgets_strategy.py``.
#
# Proof it can fail — each seed reverted by reversing the edit:
#
# * SEED 1 (Q-1629) ``host-adapter.js`` ``signedDisplay``:
#   ``tone: sign === "+" ? "pos" : …`` → ``tone: n > 0 ? "pos" : n < 0 ?
#   "neg" : ""`` (colour from the VALUE, not the rendered sign) plus
#   dropping the ``+`` — reds ``test_colour_never_appears_without_a_sign``.
# * SEED 2 (Q-1634) ``host-adapter.js`` ``tiles()``: skip an entry whose
#   display is ``missing`` — reds ``test_a_missing_number_keeps_its_tile``.
# * SEED 3 (Q-1630) ``card-backtest.js``: ``name: env.strategy_name ||
#   env.strategy_id`` — reds ``test_no_id_reaches_visible_text``.
# * SEED 4 (Q-1628) ``host-adapter.js`` ``remainderLine`` → ``return
#   null`` — reds ``test_every_cap_names_its_remainder``.
# * SEED 5 (Q-1627) ``host-adapter.js`` timeout branch → leave the body
#   alone — reds ``test_a_result_that_never_arrives_says_so``.
# * SEED 6 (Q-1632) ``renderLinkRow``: render Expand unconditionally —
#   reds ``test_expand_renders_only_where_the_host_offers_fullscreen``.
# * SEED 7 (Q-1685) ``card-backtest.js``: ``var span = H.fmtDate(
#   period.start_date, false) + " – " + H.fmtDate(period.end_date, true)``
#   (the hand-roll that shipped) — reds ONLY
#   ``test_the_receipt_keeps_both_years_when_a_window_spans_them``, on the
#   exact string the founder read in claude.ai ("Aug 15 – Sep 21, 2026"),
#   while all 50 other tests — the same-year control arms included — stay
#   green.
# * SEED 8 (Q-1685) ``card-backtest.js``: re-append the deleted fullscreen
#   facts table (Window / Cost model / Curve) — reds
#   ``test_expand_renders_only_where_the_host_offers_fullscreen`` on the
#   DUPLICATION itself ("the window is stated twice"), not merely on a
#   missing string.
#
# Proof it is not vacuous: every expected count below is derived from the
# FIXTURE JSON (the metrics the envelope carries, the rows it carries,
# the ids it carries), quantities no seed in a renderer can move, and
# ``test_the_scan_was_not_vacuous`` refuses a run that visited fewer than
# four kinds or rendered fewer than twelve tiles. The year-span arm reads
# its expected years off the envelope the harness rendered and asserts
# they DIFFER before asserting the render, so it cannot pass against a
# same-year window.

CARDS_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "cards"
CARD_CHECK = CARDS_FIXTURES / "c_card_check.mjs"
_PLAYWRIGHT = (
    pathlib.Path(__file__).resolve().parents[4]
    / "services"
    / "keel-app"
    / "node_modules"
    / "playwright"
)

#: Groups of metric keys the backtest card turns into one tile each, in
#: order. Read against the fixture below so the expected tile count is a
#: fact about the ENVELOPE, not a number typed into this file. The fixture
#: is a pre-`view.tiles` envelope: the count (`total_trades`) has no tile on
#: that path, because its label ("Trades", Q-1906) is served only — the
#: card's static HTML may not carry the word (test_policy_scan).
_BACKTEST_TILE_KEYS = (
    ("sharpe", "sharpe_ratio"),
    ("total_return_pct", "total_return"),
    ("max_drawdown_pct", "max_drawdown"),
    ("win_rate_pct", "win_rate"),
    ("turnover",),
    ("sortino", "sortino_ratio"),
    ("calmar", "calmar_ratio"),
    ("profit_factor",),
)

_ID_TOKEN_RE = re.compile(r"\b(?:str|btr|dep|cmt|shr|int)_[A-Za-z0-9]+\b")


def _fixture(name: str) -> dict:
    return json.loads((CARDS_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered_cards(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the card system's rules are DOM rules")
    if not _PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {_PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(CARD_CHECK),
            "--cards",
            str(cards),
            "--fixtures",
            str(CARDS_FIXTURES),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=pathlib.Path(__file__).resolve().parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the card check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


_TILE_KINDS = ("backtest", "live", "preflight")


def test_the_scan_was_not_vacuous(rendered_cards: dict) -> None:
    """EVERY kind visited, twelve-plus tiles drawn — and every count the
    other tests use is read from the fixtures, not from the render.

    The kind list is read from ``CARD_KINDS``, not typed here, so a kind
    added to the bundle without an arm in the check script reds this
    rather than quietly escaping every rule below (which is what
    ``compare`` did on the day it was registered)."""
    kinds = [k for k in CARD_KINDS if k in rendered_cards]
    assert kinds == list(CARD_KINDS), f"the scan visited {kinds}"

    # Tiles, counted from the fixtures.
    metrics = _fixture("c_backtest.envelope.json")["summary_metrics"]
    missing_groups = [g for g in _BACKTEST_TILE_KEYS if not any(k in metrics for k in g)]
    assert not missing_groups, f"the fixture lost its metrics: {missing_groups}"
    expected_backtest = len(_BACKTEST_TILE_KEYS) + (1 if "funding_attribution" in metrics else 0)
    preview = _fixture("c_preflight.envelope.json")["preview"]
    expected_preflight = 4 + sum(k in preview for k in ("est_slippage", "est_fees"))
    expected_live = 6  # Net P&L · Today · Max DD · Positions + Return · Sharpe

    drawn = {kind: len(rendered_cards[kind]["afterMore"]["tiles"]) for kind in _TILE_KINDS}
    assert drawn == {
        "backtest": expected_backtest,
        "live": expected_live,
        "preflight": expected_preflight,
    }
    assert sum(drawn.values()) >= 12

    # Ids: the fixtures really do carry them, so their absence below is
    # a property of the RENDERER, not of thin fixtures.
    for name in ("c_backtest.envelope.json", "c_live_overview.envelope.json"):
        raw = (CARDS_FIXTURES / name).read_text(encoding="utf-8")
        assert _ID_TOKEN_RE.search(raw), f"{name} carries no id to hide"


def test_no_id_reaches_visible_text(rendered_cards: dict) -> None:
    """Q-1630: no `str_` / `btr_` / `dep_` token in anything a person
    reads — including the headline, which used to fall back to one."""
    for kind in CARD_KINDS:
        text = rendered_cards[kind]["text"]
        hits = _ID_TOKEN_RE.findall(text)
        assert not hits, f"{kind}: ids in visible text: {hits}"
    # The headline of a nameless run is a word, not an id.
    assert rendered_cards["backtestSparse"]["headName"] == "Untitled"
    assert _fixture("c_backtest_sparse.envelope.json")["strategy_id"].startswith("str_")


def test_no_id_reaches_visible_text_from_server_prose(rendered_cards: dict) -> None:
    """Q-1712: the id rule binds sentences the SERVER wrote, too.

    Every earlier arm of the rule reads prose the CARD composes, where
    keeping ids out is a matter of not putting them in. A refusal message,
    a worker note and the card note are composed by the server, drawn verbatim,
    and staging's `not_found` reads
    "Backtest btr_00000000000000000000000000 not found." — which the card
    printed, at 720 px and 390 px, light and dark.

    Both halves are asserted, because redaction that ate the sentence
    would satisfy the first one alone:

    * the id is gone from the drawn text, and
    * every sentence it was embedded in is still there.

    SEED: in host-adapter.js `prose`, `return original;` unconditionally —
    `errorProse` and `backtestProse` both red on the id half.
    SEED: `return "";` unconditionally — both red on the sentence half.
    SEED (2026-09-23): card-backtest.js reads `env.nudge` (the field L4
    retired) instead of `env.render.note` — the "is ready to deploy."
    sentence reds; the other sentences stay green.
    """
    expected = rendered_cards["errorProse"]["expected"]
    # Not vacuous: the token the harness injected really is one this
    # regex calls an id. A renderer seed cannot move this — it reads the
    # harness's own literal, never the render.
    assert _ID_TOKEN_RE.fullmatch(expected["id"]), expected["id"]
    assert len(expected["sentences"]) == 4

    for arm in ("errorProse", "backtestProse"):
        text = rendered_cards[arm]["text"]
        assert text.strip(), f"{arm} rendered nothing"
        hits = _ID_TOKEN_RE.findall(text)
        assert not hits, f"{arm}: server prose put ids in visible text: {hits}"

    # …and the words around the id survived, in the arm that drew them.
    assert expected["sentences"][0] in rendered_cards["errorProse"]["text"]
    for sentence in expected["sentences"][1:]:
        assert sentence in rendered_cards["backtestProse"]["text"], sentence

    # The error envelope draws the refusal and nothing else — no tiles
    # over a card with no result (BUILD §4.1).
    assert rendered_cards["errorProse"]["tiles"] == []

    # The redaction itself, from the live `KeelHost.prose` in a rendered
    # frame. `innerText` collapses runs of spaces, so the DOM arms above
    # cannot see the tidy that turns `Backtest  not found.` back into
    # prose; these pairs can.
    # SEED: drop the `[ \t]{2,}` collapse from `prose` — only this arm
    # reds, and it names the doubled space.
    unit = {row["input"]: row["output"] for row in expected["unit"]}
    assert len(unit) == 6, unit
    poison = expected["id"]
    assert unit[f"Backtest {poison} not found."] == "Backtest not found."
    assert unit[f"run {poison} ({poison}) failed"] == "run failed"
    assert unit[f"deleted {poison} , then stopped"] == "deleted, then stopped"
    # A message that was NOTHING but an id has nothing left to say; the
    # caller falls back to something the envelope carries, never to an
    # invented sentence.
    assert unit[poison] == ""
    # Text with no id is returned untouched — byte-identical, so the
    # redaction cannot quietly reflow every note on the card.
    assert unit["nothing to redact here"] == "nothing to redact here"
    assert unit[None] == ""


def test_colour_never_appears_without_a_sign(rendered_cards: dict) -> None:
    """Q-1629, WCAG 1.4.1: every green/red number leads with + or −, and
    drawdowns are never coloured."""
    signs = ("+", "−")
    coloured = 0
    for key, m in rendered_cards.items():
        for probe in (m, m.get("afterMore"), m.get("afterExpand")):
            if not probe:
                continue
            for tile in probe["tiles"]:
                if tile["tone"]:
                    coloured += 1
                    assert tile["value"].startswith(signs), (
                        f"{key}: {tile['label']} is {tile['tone']} but reads {tile['value']!r}"
                    )
                if tile["label"] in ("Max drawdown", "Expected DD"):
                    assert tile["tone"] == "", f"{key}: a drawdown must be neutral"
            for cell in probe["colouredCells"]:
                coloured += 1
                assert cell.startswith(signs), f"{key}: coloured cell {cell!r}"
    assert coloured >= 20, f"only {coloured} coloured values — nothing was proven"


def test_a_missing_number_keeps_its_tile(rendered_cards: dict) -> None:
    """Q-1634: a null metric renders an em dash; the grid never reshapes
    with the data."""
    sparse = rendered_cards["backtestSparse"]
    labels = [t["label"] for t in sparse["tiles"]]
    # Q-1781: the app's first four, in its order and the server's words.
    assert labels == ["Return", "Max drawdown", "Sharpe", "Win rate"]
    dashed = {t["label"] for t in sparse["tiles"] if t["missing"]}
    # Read from the fixture: exactly the metrics it does NOT carry.
    metrics = _fixture("c_backtest_sparse.envelope.json")["summary_metrics"]
    assert "sharpe" not in metrics and "max_drawdown_pct" not in metrics
    assert "win_rate_pct" not in metrics and "win_rate" not in metrics
    assert dashed == {"Sharpe", "Max drawdown", "Win rate"}
    for tile in sparse["tiles"]:
        if tile["missing"]:
            assert tile["value"] == "—"
            assert tile["tone"] == ""
    # Same rule on a live card: the fixture carries no Sharpe.
    live = rendered_cards["live"]["afterMore"]
    assert "sharpe" not in _fixture("c_live_overview.envelope.json")["stats"]
    sharpe = [t for t in live["tiles"] if t["label"] == "Sharpe"]
    assert sharpe and sharpe[0]["value"] == "—"


def test_every_cap_names_its_remainder(rendered_cards: dict) -> None:
    """Q-1628 / Q-1634: a capped table says how many rows it dropped —
    the portfolio view used to drop them silently."""
    cap = 12
    cases = {
        "positionsWide": ("c_live_positions.envelope.json", "perp_positions"),
        "portfolioWide": ("c_live_portfolio.envelope.json", "deployments"),
    }
    for case, (fixture, key) in cases.items():
        total = len(_fixture(fixture)["data"][key])
        assert total > cap, f"{fixture} must overflow the cap to prove anything"
        m = rendered_cards[case]
        assert m["tableRows"] == cap
        assert any(n.startswith(f"+{total - cap} more rows") for n in m["notes"]), (
            f"{case}: no remainder named in {m['notes']}"
        )


def test_a_narrow_table_becomes_a_list_not_a_clipped_grid(rendered_cards: dict) -> None:
    """Q-1628: under 480 px a 6-column grid loses its right-hand columns
    to `overflow: hidden` with nothing said. It becomes a per-row list of
    the three columns that matter, and names the columns it left out."""
    wide = rendered_cards["positionsWide"]
    narrow = rendered_cards["positionsNarrow"]
    assert len(wide["ths"]) == 6 and wide["rowItems"] == 0
    assert narrow["ths"] == [] and narrow["rowItems"] == 12
    assert all(th["scope"] == "col" for th in wide["ths"])
    assert any("more columns" in n for n in narrow["notes"]), narrow["notes"]
    for m in (wide, narrow):
        assert m["overflowX"] == 0, "nothing may be wider than the card"
        assert not m["internalScroll"]


def test_a_result_that_never_arrives_says_so(rendered_cards: dict) -> None:
    """Q-1627: the skeleton is a promise; when it is not kept the body
    says so instead of holding a spinner-by-text forever."""
    m = rendered_cards["noResult"]
    assert m["skeletons"] == 0
    assert m["ariaBusy"] is None
    assert m["bodyText"] == "No result reached this card."
    assert "Loading" not in m["text"]
    assert m["tiles"] == []


def test_a_second_tool_result_re_renders(rendered_cards: dict) -> None:
    """Q-1634: `deliverEnvelope` latched, so a live card could never
    update. The second result is what the card shows."""
    second = rendered_cards["second"]
    assert second["headName"] == "second_result_strategy"
    assert "v3" in second["headChips"]
    # Exactly one card, not two stacked renders.
    assert len(second["tiles"]) == 4


def test_expand_renders_only_where_the_host_offers_fullscreen(
    rendered_cards: dict,
) -> None:
    """Q-1632: declared is not offered. With `["inline"]` there is no
    Expand; with fullscreen listed there is one, it sends
    `ui/request-display-mode`, and the card lays out for the new mode."""
    inline_only = rendered_cards["inlineOnly"]
    assert inline_only["expand"] is False
    assert len(inline_only["actions"]) == 1
    assert inline_only["headName"], "the inline arm must have rendered a card"

    full = rendered_cards["withFullscreen"]
    assert full["expand"] is True
    assert full["headName"], "the fullscreen arm must have rendered a card"
    sent = [s for s in full["sent"] if s["method"] == "ui/request-display-mode"]
    assert sent and sent[0]["params"]["mode"] == "fullscreen"

    after = full["afterExpand"]
    assert after["displayMode"] == "fullscreen"
    assert after["expand"] is False, "no Expand once expanded"
    # Fullscreen carries the whole metric set inline — that is what it is
    # for — so there is nothing left behind a "+N more". The expected
    # count is the fixture's, not the render's.
    metrics = _fixture("c_backtest.envelope.json")["summary_metrics"]
    expected = len(_BACKTEST_TILE_KEYS) + (1 if "funding_attribution" in metrics else 0)
    assert len(after["tiles"]) == expected
    assert len(full["tiles"]) == 4, "inline still shows four"
    assert after["moreButton"] == []
    # Q-1685 / review F-6: fullscreen's payload is the whole tile set and
    # a larger chart — NOT a facts table restating the receipt. The three
    # rows that used to sit under the chart were a duplicate of the
    # receipt's window (and, before the fix, a CONTRADICTION of it), a
    # duplicate of its cost model, and the downsampler's own point count.
    # The window is stated exactly once, in the receipt, in both modes.
    assert after["receipt"] == full["receipt"], "the receipt changed with the mode"
    assert len(after["receipt"]) == 1, after["receipt"]
    window = after["receipt"][0].split(" · ")[0]
    assert re.search(r"\d{4}$", window), f"not a rendered range: {window!r}"
    assert after["text"].count(window) == 1, f"the window is stated twice: {window!r}"
    assert "Cost model" not in after["text"]
    assert "extremes preserved" not in after["text"]


def test_the_receipt_keeps_both_years_when_a_window_spans_them(
    rendered_cards: dict,
) -> None:
    """Q-1685: the receipt hand-rolled its range as ``fmtDate(start,
    false) + " – " + fmtDate(end, true)`` — the start year dropped
    unconditionally — so a 2024-08-15 → 2026-09-21 run rendered
    "Aug 15 – Sep 21, 2026": five weeks, printed under two years of
    tiles. The receipt is the only place inline says what the numbers
    belong to."""
    span = rendered_cards["backtestYearSpan"]
    start_year = span["period"]["start_date"][:4]
    end_year = span["period"]["end_date"][:4]
    # Not vacuous: the arm really does span years, read off the envelope
    # the harness rendered rather than a literal typed here.
    assert start_year != end_year, f"the arm does not span years: {span['period']}"

    assert len(span["receipt"]) == 1, span["receipt"]
    receipt = span["receipt"][0]
    assert start_year in receipt and end_year in receipt, receipt

    # The header carries the window's LENGTH; the receipt carries the
    # window. Neither repeats the other — which is the reason the range
    # was hand-rolled in the first place (fmtPeriod appends the length).
    when = span["headWhen"]
    assert when, "the header lost the window's length"
    assert span["text"].count(when) == 1, f"{when!r} is stated twice"

    # Control: a same-year window still prints its year ONCE, so the fix
    # reacts to the SPAN rather than always printing both years.
    control_period = _fixture("c_backtest.envelope.json")["period"]
    assert control_period["start_date"][:4] == control_period["end_date"][:4]
    control = rendered_cards["backtest"]["receipt"][0]
    assert control.count(control_period["end_date"][:4]) == 1, control


def test_the_handshake_declares_both_display_modes(rendered_cards: dict) -> None:
    """Q-1632: no host offers fullscreen to an app that never asked."""
    init = [s for s in rendered_cards["backtest"]["sent"] if s["method"] == "ui/initialize"]
    assert init, "no handshake"
    assert init[0]["params"]["appCapabilities"] == {
        "availableDisplayModes": ["inline", "fullscreen"]
    }


def test_the_action_is_a_real_tap_target(rendered_cards: dict) -> None:
    """Q-1633: 36 px inline, 44 px under 480 px — measured, not declared."""
    for kind in _TILE_KINDS:
        actions = rendered_cards[kind]["actions"]
        assert actions, f"{kind}: no action rendered"
        assert len(actions) <= 2, f"{kind}: more than two actions"
        for a in actions:
            assert a["h"] >= 36, f"{kind}: {a}"
    for a in rendered_cards["positionsNarrow"]["actions"]:
        assert a["h"] >= 44, f"narrow target too small: {a}"


def test_the_body_stays_transparent(rendered_cards: dict) -> None:
    """Q-1631: the host paints the slot; mapping `--bg` to the host's
    opaque background double-painted it."""
    for kind in CARD_KINDS:
        bg = rendered_cards[kind]["bodyBackground"]
        assert bg in ("rgba(0, 0, 0, 0)", "transparent"), f"{kind}: {bg}"


HISTORICAL_NOTE = "Run on historical data · not indicative of future results"


def _rgb(css: str) -> tuple[int, int, int]:
    """`rgb(1, 2, 3)` or `#010203` → (1, 2, 3)."""
    css = css.strip()
    if css.startswith("#"):
        v = css[1:]
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.match(r"rgba?\(\s*(\d+)[, ]+(\d+)[, ]+(\d+)", css)
    assert m, f"not a colour: {css!r}"
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def test_backtest_performance_carries_the_historical_line(rendered_cards: dict) -> None:
    """S-6: every view that draws backtest performance — the backtest
    card's result and the comparison card, wide and phone-width, dark and
    light — ends with one muted line saying the numbers are from a run on
    historical data. The live card (real positions) and the strategy card
    (no performance) never draw it, and neither does a closed one-line
    receipt, which stays one line.

    Proof it can fail: delete the ``body.appendChild(H.historicalNote())``
    line from card-compare.js — the compare arms red; delete the
    ``if (completed) body.appendChild(H.historicalNote());`` line from
    card-backtest.js — the backtest arms red; both while the live and
    strategy control arms stay green. Not vacuous: every arm asserted
    absent must have rendered a card (text present), and the receipt arm
    must really be the one-line receipt its fixture declares."""
    shown = ("backtest", "backtestLight", "compare", "compareNarrow", "withFullscreen")
    for arm in shown:
        notes = rendered_cards[arm]["histNote"]
        assert [n["text"] for n in notes] == [HISTORICAL_NOTE], f"{arm}: {notes}"
        n = notes[0]
        # Text, at the bottom of the body, just above the action row —
        # never an action itself, and never an extra action.
        assert n["lastInBody"], f"{arm}: the line is not the body's last element"
        assert not n["interactive"], f"{arm}: the line sits inside an action"
        assert len(rendered_cards[arm]["actions"]) <= 2, arm
        # The theme's muted ink (AA on every host ground per
        # test_card_contrast.py), at the smallest body step.
        assert n["muted"], f"{arm}: --muted did not resolve"
        assert _rgb(n["color"]) == _rgb(n["muted"]), f"{arm}: {n['color']} != {n['muted']}"
        assert n["fontSize"] == "11px", f"{arm}: {n['fontSize']}"
    # Both themes really were measured: dark and light resolve different inks.
    dark_ink = rendered_cards["backtest"]["histNote"][0]["muted"]
    light_ink = rendered_cards["backtestLight"]["histNote"][0]["muted"]
    assert dark_ink != light_ink, (dark_ink, light_ink)
    # Phone width keeps it inside the frame — no sideways scroll.
    assert rendered_cards["compareNarrow"]["overflowX"] <= 0
    assert not rendered_cards["compareNarrow"]["internalScroll"]

    # Never where the numbers are not from a backtest, or on a receipt.
    for arm in ("live", "strategy", "positionsWide", "portfolioWide", "backtestReceipt"):
        m = rendered_cards[arm]
        assert m["text"].strip(), f"{arm} rendered nothing — the absence proves nothing"
        assert m["histNote"] == [], f"{arm}: {m['histNote']}"
        assert HISTORICAL_NOTE not in m["text"], arm
    receipt_fixture = _fixture("c_bt_receipt_completed.envelope.json")
    assert receipt_fixture["view"]["size"] == "receipt"
    assert rendered_cards["backtestReceipt"]["tiles"] == []
    assert rendered_cards["backtestReceipt"]["height"] < 64


def test_card_titles_are_user_words_not_internal_names():
    """Q-1883: a strategy card with nothing to draw showed its title,
    "Strategy Glass Box" — the internal name of the card's design — to the
    user. The title is what every waiting and terminal state shows before a
    result names the object, so it is plain user-facing words, and every
    served card carries exactly its kind's title."""
    # SEED: set CARD_TITLES["strategy"] back to "Strategy Glass Box" — the
    # internal-word assertion reds on the strategy card.
    from keel.widgets import CARD_KINDS, CARD_TITLES, build_card_html

    internal = ("glass box", "preflight", "result", "view")
    assert set(CARD_TITLES) == set(CARD_KINDS) and len(CARD_KINDS) == 5
    for kind in CARD_KINDS:
        title = CARD_TITLES[kind]
        assert not any(w in title.lower() for w in internal), (kind, title)
        html = build_card_html(kind)
        assert f"<title>{title}</title>" in html, kind
        assert f'<span id="card-title" class="card-title">{title}</span>' in html, kind
