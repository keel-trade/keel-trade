"""Per-surface render hints for card-backed tool responses (spec 06 R3).

The four card tools (backtest result, strategy glass-box, live view,
deploy preflight) attach one ``render`` block to their envelope. It is
HINTS, not behavior: the same envelope ships to every surface, and the
block tells the consuming agent how each surface should present it —
derived entirely from fields the tool already returns (D2.2 render-only
rule; no surface detection, no per-surface business logic server-side).

The per-surface rules are the research/05 capability matrix, revised
2026-09-17 against the current host docs (Q-1505):

* claude.ai / Claude Desktop — MCP Apps card (metrics + in-card chart)
  plus the plain link line; bare image content blocks collapse behind
  an expander there.
* Claude Code — ``view.markdown`` + link line + a ``keel open`` pointer
  (terminal renders no widgets or images).
* Cursor — an inline chart image is acceptable; link line always.
* ChatGPT — the same MCP Apps card inline (ChatGPT has converged on the
  MCP Apps standard); fullscreen on request only; no PiP assumptions on
  mobile; link line always.

**No embed iframe.** Until Q-1505 the block carried ``embed_url`` — a
signed ``/embed/{kind}/{id}`` route the card mounted in a NESTED iframe.
Claude's MCP Apps policy restricts ``frameDomains`` (third-party iframes)
pending security review, so the frame never rendered for anyone and its
failure was invisible. The cards now draw from the envelope itself (the
backtest card from ``curve``), the embed routes stay for the app and
share pages, and this module mints nothing — a render hint must never
cost a network round trip or create a capability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ._base import ToolContext


# Static per-surface presentation hints (identical for every card —
# they describe SURFACES, not tools).
SURFACE_HINTS: dict[str, str] = {
    # Descriptive voice (Q-1804): each hint states what the surface shows,
    # never what the model must do — structuredContent is model-visible on
    # ChatGPT, and imperative copy there reads as injected instructions.
    "claude": (
        "The card renders automatically when the host supports MCP Apps: "
        "one header line, four headline numbers with the rest one tap "
        "away, a receipt line, and an in-card chart drawn from the "
        "envelope. It carries an Expand action only where the host "
        "offers fullscreen. The plain link line is the fallback wherever "
        "the card does not render; bare image blocks collapse behind an "
        "expander on this host."
    ),
    "claude_code": (
        # The old text said "show a compact metrics table" — the one
        # thing the cadence contract forbids, because the view already
        # IS the table and a second one is the same numbers twice.
        "No widget renders here: `view.markdown` is the complete "
        "rendering (a receipt is one line), with the plain link line; "
        "`keel open <kind> <id>` opens it in the browser."
    ),
    "cursor": (
        "The same card renders inline (MCP Apps), without fullscreen; an "
        "inline chart image is acceptable too; the plain link line "
        "accompanies it."
    ),
    "chatgpt": (
        "The same card renders inline (MCP Apps). The card declares "
        "fullscreen and draws its own Expand action when ChatGPT lists "
        "that mode, so fullscreen is the user's choice; no PiP on "
        "mobile; the plain link line accompanies it."
    ),
}


def card_render_block(
    kind: str,
    *,
    fallback_url: str | None,
    ctx: "ToolContext",
    embed_id: str | None = None,
    note: str | None = None,
) -> dict:
    """Build the ``render`` envelope block for one card-backed response.

    ``fallback_url`` is the plain app/share URL the surface shows when
    (not if) widget rendering fails — research/05 §1. ``note`` is a
    server-composed sentence the card draws under its result (today the
    full profile's good-result `deploy` line, which the model reads as its
    `deploy:` text line); the card reads it here, under a neutral name, so
    the listed card's HTML carries no field named for an action the listed
    surface never offers (policy scan). ``embed_id`` is
    accepted for call-site compatibility and unused: the block carries
    no embed route since Q-1505 (see the module docstring). ``ctx`` is
    likewise kept on the signature so a future per-environment hint can
    read it without touching every caller.
    """
    del ctx, embed_id  # render-only: no environment reads, no minting
    from keel.widgets import card_resource_uri

    from ._toolsets import is_listed_profile

    block = {
        "card": kind,
        "card_resource_uri": card_resource_uri(kind),
        "fallback_url": fallback_url,
    }
    # The per-surface hints describe OTHER clients (Claude Code's terminal,
    # `keel open`, Cursor). The listed profile is the directory connector, a
    # single host whose card is the rendering, so it carries none of them
    # (Q-2268: no CLI or other-client copy on the listed surface); no card
    # reads the block.
    if not is_listed_profile():
        block["surface_hints"] = SURFACE_HINTS
    if note:
        block["note"] = note
    return block
