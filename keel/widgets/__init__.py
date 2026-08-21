"""Keel widget bundle — the 4-card MCP Apps surface (spec 06 R2).

One widget codebase, two host dialects:

* **MCP Apps** (``io.modelcontextprotocol/ui``) — the primary target.
  Each card is a self-contained ``ui://keel/cards/{kind}.html`` resource
  (mime ``text/html;profile=mcp-app``); the owning tool advertises it
  via ``_meta.ui.resourceUri``. Hosts (claude.ai, Claude Desktop,
  Cursor, VS Code) render it in a sandboxed iframe.
* **ChatGPT Apps SDK** — a byte-identical HTML twin registered at
  ``ui://keel/cards/{kind}.openai.html`` (mime ``text/html+skybridge``)
  and advertised via ``_meta["openai/outputTemplate"]``. The bundled
  ``host-adapter.js`` detects ``window.openai`` and maps
  ``callTool`` ↔ ``tools/call``, ``openExternal`` ↔ ``ui/open-link``,
  ``sendFollowUpMessage`` ↔ the MCP Apps message method — the cards
  themselves are dialect-blind.

The four cards (D2.2 render-only rule: they render fields the base
tools ALREADY return — no surface-specific business logic):

=============  ========================  =====================================
card kind      owning tool               renders
=============  ========================  =====================================
``backtest``   keel_backtest_summarize   headline metrics + equity/DD embed
``strategy``   keel_strategy_get         glass-box summary (source, meta)
``live``       keel_live_monitor         positions / P&L / freshness
``preflight``  keel_live_deploy          server-computed sizing + est. costs
=============  ========================  =====================================

Profile gating (spec 06 R2 acceptance): the ``preflight`` card exists
ONLY on the unlisted/full profile. ``keel_live_deploy`` is already
absent from ``LISTED_PROFILE_TOOLS``; :data:`LISTED_EXCLUDED_CARDS`
excludes the card resource itself as defense-in-depth, and
``tests/test_policy_scan.py`` proves the absence against a real listed
server (tools' ``_meta`` + the resource list + the served card HTML).

Every card renders the plain ``url_line`` / ``hero_url`` fallback link
row unconditionally — MCP Apps rendering has open reliability bugs
(research/05 §1), so the link line is the contract, the widget is the
enhancement.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


__all__ = [
    "CARD_KINDS",
    "CARD_TOOLS",
    "LISTED_EXCLUDED_CARDS",
    "build_card_html",
    "card_resource_uri",
    "openai_card_resource_uri",
    "tool_ui_meta",
    "register_card_resources",
]


_ASSETS = Path(__file__).parent / "assets"

# Card kind → owning outcome tool (inverted below). Render-only: the
# card consumes that tool's response envelope verbatim.
CARD_TOOLS: dict[str, str] = {
    "keel_backtest_summarize": "backtest",
    "keel_strategy_get": "strategy",
    "keel_live_monitor": "live",
    "keel_live_deploy": "preflight",
}

CARD_KINDS: tuple[str, ...] = ("backtest", "strategy", "live", "preflight")

# Cards that must NEVER exist on the directory-listed profile (spec 06
# R2 AC). keel_live_deploy is already outside LISTED_PROFILE_TOOLS, so
# this is defense-in-depth — a future allow-list edit can't quietly
# bring the card along.
LISTED_EXCLUDED_CARDS: frozenset[str] = frozenset({"preflight"})

_CARD_TITLES: dict[str, str] = {
    "backtest": "Backtest Result",
    "strategy": "Strategy Glass Box",
    "live": "Live View",
    "preflight": "Deploy Preflight",
}

# MCP Apps wire mime (what FastMCP's UI_MIME_TYPE resolves to) and the
# ChatGPT Apps SDK widget mime.
MCP_APPS_MIME = "text/html;profile=mcp-app"
OPENAI_WIDGET_MIME = "text/html+skybridge"


def card_resource_uri(kind: str) -> str:
    """MCP Apps resource URI for one card."""
    return f"ui://keel/cards/{kind}.html"


def openai_card_resource_uri(kind: str) -> str:
    """Apps SDK (ChatGPT) resource URI for the same card."""
    return f"ui://keel/cards/{kind}.openai.html"


def _app_origin() -> str:
    """Origin of the Keel web app the cards embed/fetch/link into.

    Same env the tool adapters consult (``KEEL_APP_URL``, hard-set by
    hosted deployments) with the prod default — one URL source of truth.
    """
    base = os.environ.get("KEEL_APP_URL", "https://app.usekeel.io")
    parts = urlsplit(base)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return base.rstrip("/")


def _share_origin() -> str:
    base = os.environ.get("KEEL_SHARE_URL_ROOT", "https://usekeel.io/share")
    parts = urlsplit(base)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return base.rstrip("/")


@lru_cache(maxsize=None)
def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def build_card_html(kind: str) -> str:
    """Compose one card's self-contained HTML from the shared bundle.

    One codebase: every card inlines the same ``host-adapter.js`` (host
    detection + the ``window.openai`` mapping) and ``card.css``; only
    the per-card renderer differs. No external scripts/styles — the
    only remote reference a card ever makes is the R1 embed iframe,
    declared in the CSP below.
    """
    if kind not in CARD_KINDS:
        raise ValueError(f"Unknown card kind {kind!r}. Valid: {', '.join(CARD_KINDS)}")
    template = _asset("card.html.tmpl")
    return (
        template.replace("{{KIND}}", kind)
        .replace("{{TITLE}}", _CARD_TITLES[kind])
        .replace("{{CSS}}", _asset("card.css"))
        .replace("{{ADAPTER_JS}}", _asset("host-adapter.js"))
        .replace("{{CARD_JS}}", _asset(f"card-{kind}.js"))
    )


def _csp_meta() -> dict:
    """MCP Apps CSP declaration (``_meta.ui.csp``) for card resources.

    The card only ever talks to the Keel app origin: the R1 embed routes
    load in a nested iframe (frame-src) and any card fetch goes to the
    same origin (connect-src).
    """
    app = _app_origin()
    return {
        "connectDomains": [app],
        "resourceDomains": [app],
        "frameDomains": [app],
    }


def _openai_widget_csp() -> dict:
    """Apps SDK CSP twin (``_meta["openai/widgetCSP"]``)."""
    app = _app_origin()
    return {
        "connect_domains": [app],
        "resource_domains": [app],
        # openExternal destinations that skip the safe-link modal and
        # get a redirectUrl round-trip (research/05 §4 implication #5).
        "redirect_domains": sorted({app, _share_origin()}),
    }


def card_kind_for_tool(tool_name: str) -> str | None:
    return CARD_TOOLS.get(tool_name)


def tool_ui_meta(tool_name: str) -> dict | None:
    """The ``_meta`` block a card-backed tool publishes in tools/list.

    Returns ``None`` for tools without a card, and for card kinds
    excluded from the active profile (listed → no preflight; the owning
    tool isn't registered there anyway — this is the second lock).
    """
    from keel.tools.outcomes._toolsets import is_listed_profile

    kind = CARD_TOOLS.get(tool_name)
    if kind is None:
        return None
    if is_listed_profile() and kind in LISTED_EXCLUDED_CARDS:
        return None
    return {
        # MCP Apps wire key (research/05 §1: `_meta.ui.csp` shorthand).
        "ui": {"resourceUri": card_resource_uri(kind)},
        # ChatGPT Apps SDK twin.
        "openai/outputTemplate": openai_card_resource_uri(kind),
    }


def active_card_kinds() -> list[str]:
    """Card kinds whose owning tool is registered on the active surface.

    Mirrors ``register_all``'s own gating (profile + toolsets +
    local_only) so a card resource never exists without its tool — a
    truthful surface, and the reason the preflight card is absent on
    any deployment that doesn't load ``live-write``.
    """
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._toolsets import (
        is_listed_profile,
        is_tool_loaded,
        load_toolsets,
    )

    toolsets = load_toolsets()
    kinds: list[str] = []
    for tool_name, kind in CARD_TOOLS.items():
        tool = OUTCOMES.get(tool_name)
        if tool is None:
            continue
        if not is_tool_loaded(tool.toolset, toolsets, local_only=tool.local_only, name=tool.name):
            continue
        if is_listed_profile() and kind in LISTED_EXCLUDED_CARDS:
            continue
        kinds.append(kind)
    return kinds


def register_card_resources(mcp_server) -> list[str]:
    """Register the card UI resources on a FastMCP server.

    Two registrations per active card (MCP Apps + Apps SDK twin), same
    HTML text. Returns the registered URIs (used by tests and the
    startup inventory log).
    """
    from fastmcp.resources import TextResource

    registered: list[str] = []
    for kind in active_card_kinds():
        html = build_card_html(kind)
        title = _CARD_TITLES[kind]
        mcp_server.add_resource(
            TextResource(
                uri=card_resource_uri(kind),
                name=f"keel-card-{kind}",
                title=f"{title} card",
                description=f"Keel {title} card (MCP Apps widget).",
                text=html,
                mime_type=MCP_APPS_MIME,
                meta={"ui": {"csp": _csp_meta(), "prefersBorder": True}},
            )
        )
        registered.append(card_resource_uri(kind))
        mcp_server.add_resource(
            TextResource(
                uri=openai_card_resource_uri(kind),
                name=f"keel-card-{kind}-openai",
                title=f"{title} card (Apps SDK)",
                description=f"Keel {title} card (ChatGPT Apps SDK widget).",
                text=html,
                mime_type=OPENAI_WIDGET_MIME,
                meta={
                    "openai/widgetCSP": _openai_widget_csp(),
                    "openai/widgetPrefersBorder": True,
                },
            )
        )
        registered.append(openai_card_resource_uri(kind))
    return registered
