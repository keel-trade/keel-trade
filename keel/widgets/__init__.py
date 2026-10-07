"""Keel widget bundle — the 4-card MCP Apps surface (spec 06 R2).

One widget codebase, two host dialects:

* **MCP Apps** (``io.modelcontextprotocol/ui``) — the primary target.
  Each card is a self-contained ``ui://keel/cards/{kind}.html`` resource
  (mime ``text/html;profile=mcp-app``); the owning tool advertises it
  via ``_meta.ui.resourceUri``. Hosts (claude.ai, Claude Desktop,
  Cursor, VS Code) render it in a sandboxed iframe.
* **ChatGPT** — ChatGPT converged on the MCP Apps standard (2026-02):
  the SAME ``ui://keel/cards/{kind}.html`` resource + ``_meta.ui.resourceUri``
  is its primary path too. A byte-identical twin is still registered at
  ``ui://keel/cards/{kind}.openai.html`` (mime ``text/html+skybridge``)
  and advertised via ``_meta["openai/outputTemplate"]`` — the legacy
  Apps SDK aliases, kept because no sunset is published. The bundled
  ``host-adapter.js`` serves both bridges from one HTML: it detects the
  ``window.openai`` globals (``toolOutput`` / ``theme`` / ``displayMode``
  / ``maxHeight`` / ``notifyIntrinsicHeight``) and maps ``callTool`` ↔
  ``tools/call``, ``openExternal`` ↔ ``ui/open-link``,
  ``sendFollowUpMessage`` ↔ the MCP Apps message method — the cards
  themselves are dialect-blind.

Shared-adapter contract (Q-1505; every card inherits it, so a card
lane never re-implements these):

* **Sizing.** The document is exactly as tall as its content: the
  adapter sets the root element's height explicitly and sends
  ``ui/notifications/size-changed`` (or ``notifyIntrinsicHeight``) from
  a ResizeObserver. No fixed heights, no ``100vh``, no internal scroll.
* **Theme.** ``hostContext.theme`` → ``[data-theme]``;
  ``hostContext.styles.variables`` → the card tokens (``--bg --fg
  --muted --line --accent --good --bad --tile --font``), with card.css's
  palette + ``prefers-color-scheme`` as the fallback; the host's
  ``styles.css.fonts`` block is injected verbatim. ``prefersBorder`` is
  false (Claude web is borderless by default; mobile draws its own).
* **No nested frames, no fetches.** Cards draw from the tool envelope
  only (the backtest chart from ``curve``). Claude restricts
  ``frameDomains`` pending security review, so the former embed iframe
  never rendered; the CSP below therefore declares no frame or connect
  origins at all.

The four cards (D2.2 render-only rule: they render fields the base
tools ALREADY return — no surface-specific business logic):

=============  ========================  =====================================
card kind      owning tool               renders
=============  ========================  =====================================
``backtest``   keel_backtest_summarize   header + metric tiles + in-card SVG equity/DD chart
``strategy``   keel_strategy_get         name · version · Live badge header, latest-backtest
                                         tiles, glass box (universe / clock / execution) from
                                         the server-derived ``metadata.graph`` (the tool asks
                                         ``include=graph``), the pipeline as a component chain
``live``       keel_live_monitor         per ``view``: overview = P&L tiles (``stats``) + an
                                         in-card equity sparkline (``curve``) + hygiene
                                         warnings; positions / portfolio / stats / equity /
                                         pnl = tiles + a capped table; other views = a capped
                                         table with a per-view column whitelist
``preflight``  keel_live_deploy          name + schedule header, sizing / expected-DD /
                                         est. cost / asset-count tiles, confirm-by row
=============  ========================  =====================================

Card review decisions (Q-1505 follow-up, 2026-09-17), applied to all three:

* No internal ids (strategy / run / account / intent tokens) and no raw
  ISO timestamps — names, versions and formatted dates instead.
* No source dump: the strategy card's ``<pre>`` scroll box violated the
  no-nested-scrolling host rule; the glass box replaces it and the link
  row opens the editor.
* Every user-visible label passes the listed-surface string rules
  (``tests/test_policy_scan.py`` scans the served card HTML): wire field
  names the scanner would read as prose are assembled at runtime in
  ``card-live.js`` and labelled with listing-safe words (``Carry`` for
  the carry attribution, ``Fills`` for the fill count).

The card system (mcp-strategy-view V-15, Q-1627 … Q-1634) — one grammar
across all four cards, carried by ``host-adapter.js``:

* **Header**: ``Keel`` · name (mono, ellipsis, ``Untitled`` when
  missing — never an id) · ``v{n}`` · state chip · clock chip. Dates
  only as "since Aug 20" / "8 months", never a raw stamp.
* **Tiles**: four inline, the rest behind "+N more" in place; a missing
  value renders an em dash and KEEPS its tile.
* **Numbers**: one formatter (``KeelHost.fmt``), the twin of the app's
  ``pnl()``. A sign glyph always accompanies colour; only signed money
  and returns are coloured; drawdowns, ratios and rates are neutral;
  one minus glyph (U+2212).
* **Remainders**: every capped table/list names what it dropped, and
  under 480 px a wide table becomes a per-row list of its three most
  important columns rather than a grid the frame clips.
* **States**: a shape-stable skeleton per card kind, an honest line when
  no result arrives, and a second tool result re-renders (no latch).
* **Actions**: at most two — the link row and, only where the host lists
  fullscreen, Expand. Both are ≥ 36 px (44 px under 480 px) with a
  visible focus ring.

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
    "CARD_TITLES",
    "CARD_TOOLS",
    "HOST_FONT_ORIGIN",
    "LISTED_EXCLUDED_CARDS",
    "MAX_INVOCATION_STRING_CHARS",
    "MAX_WIDGET_DESCRIPTION_CHARS",
    "TOOL_INVOCATION_STRINGS",
    "WIDGET_DESCRIPTIONS",
    "build_card_html",
    "card_kind_for_tool",
    "card_resource_uri",
    "openai_card_resource_uri",
    "tool_ui_meta",
    "register_card_resources",
]


_ASSETS = Path(__file__).parent / "assets"

# Card kind → owning outcome tool (inverted below). Render-only: the
# card consumes that tool's response envelope verbatim.
# Several tools share one card kind: the strategy card renders the same
# `view` block whether it arrived from a read, a compose, a fork, a diff
# or a library fork (mcp-strategy-view V-4, Q-1619) — 2 % of a measured
# user's calls were `strategy_get`, while compose alone was 28 %.
#
# The render-cadence build (RENDER-CADENCE-BUILD §2.5, Q-1688) added
# ``keel_backtest_run`` and ``keel_backtest_watch``: a submitted or
# watched run IS a backtest result — it renders as the card's `receipt`
# size (one row that opens in place), the same card `summarize` draws in
# full. A step in a set is a receipt; the run being discussed is the
# evidence block.
CARD_TOOLS: dict[str, str] = {
    "keel_backtest_summarize": "backtest",
    "keel_backtest_run": "backtest",
    "keel_backtest_watch": "backtest",
    "keel_strategy_get": "strategy",
    "keel_strategy_compose": "strategy",
    "keel_strategy_fork": "strategy",
    "keel_strategy_diff": "strategy",
    "keel_library_fork": "strategy",
    "keel_backtest_compare": "compare",
    "keel_live_monitor": "live",
    "keel_live_deploy": "preflight",
}

CARD_KINDS: tuple[str, ...] = ("backtest", "strategy", "live", "preflight", "compare")

# Cards that must NEVER exist on the directory-listed profile (spec 06
# R2 AC). keel_live_deploy is already outside LISTED_PROFILE_TOOLS, so
# this is defense-in-depth — a future allow-list edit can't quietly
# bring the card along.
LISTED_EXCLUDED_CARDS: frozenset[str] = frozenset({"preflight"})

#: The card's title before a result names it — the head of the waiting and
#: terminal states, the frame's `<title>` and `aria-label`. User-facing
#: words only: "Strategy Glass Box" was the internal name of the card's
#: design and reached users on every strategy card that had no result to
#: draw (Q-1883). A rendered card replaces it with the object's own name.
CARD_TITLES: dict[str, str] = {
    "backtest": "Backtest",
    "strategy": "Strategy",
    "live": "Live strategy",
    "preflight": "Deploy check",
    "compare": "Comparison",
}
_CARD_TITLES = CARD_TITLES

# ── ChatGPT tool-row copy (BUILD §2.6) ────────────────────────────────
#
# ``openai/toolInvocation/invoking`` is what the tool row reads WHILE the
# call is in flight; ``invoked`` is what it reads after. Both are
# DESCRIPTOR-level — one string per tool, published in ``tools/list``,
# with no way to vary per call — so every ``invoked`` is
# OUTCOME-NEUTRAL. ``keel_backtest_run`` returns on ``wait=false``, on a
# polling timeout and on a failed run; ``keel_strategy_compose`` returns
# on a dry run that saved nothing. "Backtest finished" / "Strategy
# saved" would be a lie on those arms, and the row sits directly above
# the card whose own receipt names the outcome.
#
# ≤ 64 characters each, and policy-clean: these strings ride the listed
# profile's ``_meta`` and ``tests/test_policy_scan.py`` scans them.
TOOL_INVOCATION_STRINGS: dict[str, tuple[str, str]] = {
    "keel_backtest_run": ("Running backtest…", "Backtest returned"),
    "keel_backtest_watch": ("Checking the run…", "Watch returned"),
    "keel_backtest_summarize": ("Reading the result…", "Result returned"),
    "keel_backtest_compare": ("Comparing runs…", "Comparison returned"),
    "keel_strategy_compose": ("Composing…", "Compose returned"),
    "keel_strategy_get": ("Reading the strategy…", "Strategy returned"),
    "keel_strategy_fork": ("Forking…", "Fork returned"),
    "keel_strategy_diff": ("Comparing versions…", "Diff returned"),
    "keel_library_fork": ("Forking from the library…", "Fork returned"),
    "keel_live_monitor": ("Reading live state…", "Live view returned"),
}

#: The cap the hosts' tool row can show before it elides.
MAX_INVOCATION_STRING_CHARS = 64

# ── What the model is told about the card (spec 02 §2.6) ───────────────
#
# ChatGPT's own mechanism for "the user can already see this": a
# DESCRIPTOR-level `openai/widgetDescription` per card-backed tool. It
# describes what the card shows — a fact, word-rule clean, ≤ 200 chars —
# never how to reply (the one sentence about explaining rather than
# repeating lives in the instructions head, spec 01 §2.2). claude.ai
# injects its own note; nothing is added there. `preflight` is full-profile
# only and is scanned under that profile's rules.
WIDGET_DESCRIPTIONS: dict[str, str] = {
    "backtest": (
        "A card the user sees: the run's headline metrics, its window, the equity and "
        "drawdown curve, and a link to the tearsheet."
    ),
    "compare": (
        "A card the user sees: the runs side by side, deltas against the first, overlaid "
        "curves, and what differs between them."
    ),
    "strategy": (
        "A card the user sees: the strategy's block structure, what changed, its latest "
        "evidence, and a link to the editor."
    ),
    "live": "A card the user sees: the live strategy's current state and how fresh it is.",
    "preflight": (
        "A card the user sees: the deployment preview — the account, the sizing and the "
        "checks — before anything is confirmed."
    ),
}

#: The cap spec 02 §2.6 sets on each description.
MAX_WIDGET_DESCRIPTION_CHARS = 200

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
    the per-card renderer differs. No external scripts/styles, no
    frames, no fetches — a card is a pure function of its tool envelope.
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


#: The one origin a card loads a resource from ON CLAUDE: Claude serves
#: the host font (Anthropic Sans) from here, and the host injects its own
#: ``@font-face`` block into every card. Declared on the MCP Apps
#: resource only — the ChatGPT twin loads nothing remote (system fonts). With an empty allow-list the
#: font file request is blocked by the iframe CSP and the card silently
#: paints in a fallback face while the conversation around it does not
#: (Q-1631 — invisible locally, since a local harness has no host).
HOST_FONT_ORIGIN = "https://assets.claude.ai"


def _csp_meta() -> dict:
    """MCP Apps CSP declaration (``_meta.ui.csp``) for card resources.

    Narrowest policy that still works: a card makes no fetch and frames
    nothing (``connectDomains`` stays empty), and the ONE remote
    resource it loads is the host's own font, which the transparent-
    theming contract requires us to allow by name.
    """
    return {
        "connectDomains": [],
        "resourceDomains": [HOST_FONT_ORIGIN],
    }


def _openai_widget_csp() -> dict:
    """Apps SDK CSP twin (``_meta["openai/widgetCSP"]``).

    ``redirect_domains`` is the one field the standard ``_meta.ui.csp``
    cannot express: openExternal destinations that skip ChatGPT's
    safe-link modal (research/05 §4 implication #5) — the app and the
    share origin, i.e. where the link row points.

    ``resource_domains`` is EMPTY, unlike the MCP Apps twin: the one
    remote resource a card loads is Claude's host font, which only
    Claude's ``hostContext.styles.css.fonts`` ever injects. On ChatGPT
    the card paints in card.css's system stack — "Don't use custom
    fonts, even in full screen modes" (ChatGPT UI guidelines) — and
    OpenAI's review "checks the declared policy against the UI
    behavior", so a Claude-owned origin the card never loads there is a
    declaration that does not match behaviour.
    """
    return {
        "connect_domains": [],
        "resource_domains": [],
        "redirect_domains": sorted({_app_origin(), _share_origin()}),
    }


def _openai_widget_domain() -> str:
    """The ChatGPT widget's dedicated origin (``_meta["openai/widgetDomain"]``).

    OpenAI's plugin reference: ``_meta.ui.domain`` is the "Dedicated
    origin for hosted components (required when submitting a plugin with
    UI; must be unique per plugin)", and ``openai/widgetDomain`` is its
    "OpenAI-specific compatibility alias ... in ChatGPT". The value is an
    origin the developer supplies; ChatGPT sandboxes the card under a
    subdomain derived from it (``<domain>.web-sandbox.oaiusercontent.com``)
    and points the fullscreen "Open in <app>" button at it — so it is the
    Keel web app's origin, the same place the link row opens, and it
    follows ``KEEL_APP_URL`` per environment like every other link.

    Set on the ChatGPT twin ONLY, and never as ``ui.domain`` on the MCP
    Apps resource: ``ui.domain``'s format is host-dependent, and Claude
    requires exactly ``{sha256(connector URL)[:32]}.claudemcpcontent.com``
    — any other value shows "Invalid ui.domain format" / "ui.domain
    mismatch" INSTEAD of rendering the card (claude.com/docs/connectors/
    building/mcp-apps/troubleshooting).
    """
    return _app_origin()


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
    meta: dict = {
        # MCP Apps wire key (research/05 §1: `_meta.ui.csp` shorthand).
        "ui": {"resourceUri": card_resource_uri(kind)},
        # ChatGPT Apps SDK twin.
        "openai/outputTemplate": openai_card_resource_uri(kind),
    }
    invocation = TOOL_INVOCATION_STRINGS.get(tool_name)
    if invocation is not None:
        meta["openai/toolInvocation/invoking"] = invocation[0]
        meta["openai/toolInvocation/invoked"] = invocation[1]
    description = WIDGET_DESCRIPTIONS.get(kind)
    if description is not None:
        meta["openai/widgetDescription"] = description
    return meta


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
        if kind in kinds:
            continue  # several tools, one card kind — one resource
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
                # prefersBorder false: Claude web is borderless by default
                # and the card is transparent; mobile hosts draw their own.
                meta={"ui": {"csp": _csp_meta(), "prefersBorder": False}},
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
                    "openai/widgetDomain": _openai_widget_domain(),
                    "openai/widgetPrefersBorder": False,
                },
            )
        )
        registered.append(openai_card_resource_uri(kind))
    return registered
