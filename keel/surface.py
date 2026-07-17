"""Calling-surface self-identification (spec 08 R5).

keel-api classifies the calling surface for commit attribution and
telemetry (`derive_surface`): an explicit ``x-keel-surface`` header wins
over auth-method heuristics. The SDK knows exactly which surface it is
— the CLI entrypoint, the local MCP server, the hosted MCP server, or a
bare SDK import — so it says so instead of leaving the server to guess
from token claims (which mislabel e.g. CLI use of a browser-minted
token).

Values must be members of keel-api's SURFACES set:
``cli | local-mcp | hosted-mcp | sdk | web | chat``.
"""

from __future__ import annotations


_SURFACE: str | None = None

_KNOWN_SURFACES = frozenset({"cli", "local-mcp", "hosted-mcp", "sdk", "web", "chat"})


def set_surface(surface: str) -> None:
    """Declare the calling surface. Called once by entrypoints (CLI main,
    `keel mcp serve`). Unknown values raise — never send a header the
    server would silently discard."""
    if surface not in _KNOWN_SURFACES:
        raise ValueError(f"Unknown surface {surface!r}; expected one of {sorted(_KNOWN_SURFACES)}")
    global _SURFACE
    _SURFACE = surface


def current_surface() -> str:
    """The surface to advertise on API requests.

    Hosted execution mode always wins (the pod IS the hosted MCP server
    regardless of which entrypoint imported us); otherwise whatever the
    entrypoint declared; otherwise plain `sdk` (direct library use).
    """
    try:
        from keel.hosting import is_hosted

        if is_hosted():
            return "hosted-mcp"
    except Exception:  # noqa: BLE001 — surface detection must never break a request
        pass
    return _SURFACE or "sdk"
