"""Shared paginated-response extractor — one source of truth for the
keel-api pagination shape.

The canonical keel-api response for any list endpoint is
``PaginatedResponse`` from the API:

    {"data": [...], "pagination": {"cursor": ..., "has_more": ...}}

Pre-v0.4 the SDK handlers each rolled their own extractor with
slightly different fallback chains (``items`` / ``events`` /
``accounts`` / ``notes``). None of them included the actual canonical
``data`` key, so every paginated MCP tool silently returned an empty
list against the live API — caught in the v0.4.x prod-readiness smoke.

This helper hardcodes ``data`` first, with the legacy fallbacks kept
as safety nets for any future endpoint that still returns the older
shape. All four list-style outcome handlers (``audit_list_last``,
``accounts_list``, ``strategy_search``, ``strategy_memory_read``)
should funnel through here.
"""

from __future__ import annotations

from typing import Any


def extract_paginated(payload: Any) -> tuple[list[Any], str | None]:
    """Pull ``(items, next_cursor)`` out of a keel-api paginated response.

    Accepts:
      * The canonical shape ``{"data": [...], "pagination": {...}}``
      * Legacy shapes ``{"items": [...], "next_cursor": ...}``,
        ``{"events": [...]}``, ``{"accounts": [...]}``, ``{"notes": [...]}``
      * A bare ``list[...]`` (older endpoints that didn't paginate)
      * Anything else → ``([], None)``

    Args:
        payload: Whatever the ``KeelClient.get`` call returned.

    Returns:
        ``(items, cursor)`` where ``items`` is the parsed list and
        ``cursor`` is the opaque pagination cursor string (or None on
        the last page).
    """
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return [], None

    # Canonical shape — keel-api PaginatedResponse.
    if "data" in payload and isinstance(payload["data"], list):
        pagination = payload.get("pagination") or {}
        cursor = None
        if isinstance(pagination, dict):
            cursor = pagination.get("cursor")
        return payload["data"], cursor

    # Legacy shapes — kept so a future stray endpoint doesn't silently
    # break. Order: try the most specific names first.
    for key in ("items", "events", "accounts", "notes", "results"):
        if key in payload and isinstance(payload[key], list):
            return payload[key], payload.get("next_cursor")

    return [], None


__all__ = ["extract_paginated"]
