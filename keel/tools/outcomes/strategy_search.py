"""`keel_strategy_search` — enumerate strategies (remote + optional local).

Replaces the legacy `strategy_list` + `strategy_find_local` primitives.
Filterable by `query`, `tag`, `owner`, `share_id`; when no filters are
supplied and the call is interactive (CLI/TTY), also surfaces
locally-checked-out workspaces.

Do NOT use to fetch a strategy's full source — call `keel_strategy_get`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _local_workspaces() -> list[dict[str, Any]]:
    """Best-effort list of local checked-out strategies."""
    try:
        from keel.workspace import list_workspaces

        items = list_workspaces() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for ws in items:
        out.append(
            {
                "strategy_id": ws.strategy_id,
                "name": getattr(ws, "name", None),
                "owner": "local",
                "hero_url": None,
                "updated_at": getattr(ws, "checked_out_at", None),
                "source": "local",
            }
        )
    return out


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    query: str | None = args.get("query")
    tag: str | None = args.get("tag")
    owner: str | None = args.get("owner")
    share_id: str | None = args.get("share_id")
    limit: int = int(args.get("limit", 20) or 20)
    cursor: str | None = args.get("cursor")

    # Build remote query. The keel-api list endpoint (today) accepts
    # cursor/limit/sort/search/status. `tag`, `owner`, `share_id` are
    # client-side filters applied to the response below until the API
    # gains support (Phase 2C).
    params: dict[str, Any] = {"limit": limit}
    if query:
        params["search"] = query
    if cursor:
        params["cursor"] = cursor

    client = ctx.get_client()
    try:
        payload = client.get("/v1/strategies", **params)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to list strategies: {e}",
            suggestion="Run `keel doctor` to diagnose.",
        )

    from ._pagination import extract_paginated

    items_raw, next_cursor = extract_paginated(payload)

    results: list[dict[str, Any]] = []
    for it in items_raw:
        sid = it.get("strategy_id") or it.get("id")
        owner_val = it.get("owner") or it.get("org_id")
        # Client-side filter for fields the API doesn't yet accept.
        if tag and tag not in (it.get("tags") or []):
            continue
        if owner and owner_val != owner:
            continue
        if share_id and share_id != it.get("share_id"):
            continue
        results.append(
            {
                "strategy_id": sid,
                "name": it.get("name"),
                "owner": owner_val,
                "hero_url": f"{ctx.app_url}/strategies/{sid}" if sid else None,
                "updated_at": it.get("updated_at"),
            }
        )

    # Merge local workspaces only on CLI when no remote filters specified.
    no_filters = not any((query, tag, owner, share_id))
    if no_filters and ctx.is_tty:
        seen_ids = {r["strategy_id"] for r in results}
        for local in _local_workspaces():
            if local["strategy_id"] not in seen_ids:
                results.append(local)

    extra: dict[str, Any] = {"results": results}
    if next_cursor:
        extra["next_cursor"] = next_cursor

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/strategies",
        share_url=None,
        extra=extra,
    )


STRATEGY_SEARCH = register(
    OutcomeTool(
        name="keel_strategy_search",
        required_action="strategy.read",
        cli_path=("strategy", "search"),
        toolset="read-only",
        description=(
            "Search and list strategies in the current org. Optional filters: "
            "`query` (name substring), `tag`, `owner`, `share_id`. "
            "On CLI (TTY) calls with no filters, also includes locally "
            "checked-out workspaces. "
            "Do NOT use to fetch a strategy's full source or version history — "
            "call `keel_strategy_get`. "
            "Do NOT use to look up component metadata — call `keel_components_search`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name substring filter."},
                "tag": {"type": "string", "description": "Filter by strategy tag."},
                "owner": {"type": "string", "description": "Filter by owner principal/org."},
                "share_id": {"type": "string", "description": "Filter by share-link id."},
                "limit": {"type": "integer", "default": 20, "description": "Max results."},
                "cursor": {"type": "string", "description": "Pagination cursor from prior call."},
            },
            "required": [],
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
