"""Per-surface render hints for card-backed tool responses (spec 06 R3).

The four card tools (backtest result, strategy glass-box, live view,
deploy preflight) attach one ``render`` block to their envelope. It is
HINTS, not behavior: the same envelope ships to every surface, and the
block tells the consuming agent how each surface should present it —
derived entirely from fields the tool already returns (D2.2 render-only
rule; no surface detection, no per-surface business logic server-side).

The per-surface rules are the research/05 capability matrix verbatim:

* claude.ai / Claude Desktop — MCP Apps widget + the plain link line;
  never bare image content blocks (they collapse behind an expander).
* Claude Code — metrics table + link line + a ``keel open`` pointer
  (terminal renders no widgets or images).
* Cursor — an inline chart image is acceptable; link line always.
* ChatGPT — Apps SDK card inline; fullscreen on request only; no PiP
  assumptions on mobile; link line always.

``embed_url`` is the R1 embed route for the card's inner chart iframe:
``{KEEL_APP_URL}/embed/{kind}/{id}?token=<embed-jwt>``. The token is a
short-lived signed capability minted via ``POST /v1/embeds`` (M5.1;
same ``sharing.create`` bucket as share links). Minting is best-effort
BY DESIGN: any delivery failure (unauthenticated, 4xx/5xx, network)
falls back to the tokenless URL — the card JS already hides the frame
on load failure, so the tool response itself must never fail or slow
into an error because a render-only nicety couldn't be signed.
Programming errors still propagate (lessons.md: never-fails is not
swallow-bugs). This module remains the ONE place embed URLs are built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ._base import ToolContext


# Static per-surface presentation hints (identical for every card —
# they describe SURFACES, not tools).
SURFACE_HINTS: dict[str, str] = {
    "claude": (
        "Widget card renders automatically when supported; ALWAYS also "
        "show the plain link line — never rely on the widget alone and "
        "never emit bare image blocks."
    ),
    "claude_code": (
        "Show a compact metrics table plus the plain link line; offer "
        "`keel open <kind> <id>` to view it in the browser."
    ),
    "cursor": ("An inline chart image is acceptable; always include the plain link line."),
    "chatgpt": (
        "Card renders inline via the Apps SDK; fullscreen only on user "
        "request; no PiP on mobile; include the plain link line."
    ),
}

# Card kinds with an R1 embed route (spec 06 R1). The preflight card is
# data-only — it has no embed route (and therefore never mints).
_EMBED_KINDS: frozenset[str] = frozenset({"backtest", "strategy", "live"})


def _mint_embed_token(kind: str, embed_id: str, ctx: "ToolContext") -> str | None:
    """Best-effort mint of a signed embed token (``POST /v1/embeds``).

    Returns the token string, or ``None`` when minting is unavailable or
    fails for any DELIVERY reason. Deliberate boundaries:

    * Mints only on the client the handler already authenticated with
      (``ctx.api_client``); never constructs a fresh client just to sign
      a render nicety, and never mints in ``dry_run`` (the mint is a
      mutation — it creates a live capability).
    * ``KeelError`` (auth, 4xx/5xx, retry-exhausted network) → ``None``;
      the caller falls back to the tokenless URL. Programming errors
      (TypeError & co) propagate — same boundary as keel_feedback.
    """
    if ctx.api_client is None or ctx.dry_run:
        return None
    from keel.errors import KeelError

    try:
        resp = ctx.api_client.post(
            "/v1/embeds",
            json={"resource_type": kind, "resource_id": embed_id},
        )
    except KeelError:
        return None
    token = resp.get("embed_token") if isinstance(resp, dict) else None
    return token if isinstance(token, str) and token else None


def card_render_block(
    kind: str,
    *,
    fallback_url: str | None,
    ctx: "ToolContext",
    embed_id: str | None = None,
) -> dict:
    """Build the ``render`` envelope block for one card-backed response.

    ``fallback_url`` is the plain app/share URL the surface shows when
    (not if) widget rendering fails — research/05 §1. ``embed_id`` keys
    the R1 embed route when the kind has one; a signed token is minted
    exactly once per emitted block (never for preflight, never without
    an embed id) and attached as ``?token=``. Mint failure degrades to
    the tokenless URL — never to a tool error.
    """
    from keel.widgets import card_resource_uri

    block: dict = {
        "card": kind,
        "card_resource_uri": card_resource_uri(kind),
        "fallback_url": fallback_url,
        "surface_hints": SURFACE_HINTS,
    }
    if embed_id and kind in _EMBED_KINDS:
        embed_url = f"{ctx.app_url}/embed/{kind}/{embed_id}"
        token = _mint_embed_token(kind, embed_id, ctx)
        if token:
            embed_url = f"{embed_url}?token={token}"
        block["embed_url"] = embed_url
    return block
