"""`keel_strategy_search` — enumerate strategies (remote + optional local).

Replaces the legacy `strategy_list` + `strategy_find_local` primitives.
Filterable by `query`; when no filter is supplied and the call is
interactive (CLI/TTY), also surfaces locally-checked-out workspaces.

`tag`, `owner` and `share_id` left on 2026-10-01 (Q-2080 / FINDINGS F8):
they were client-side filters over fields keel-api's `StrategyResponse`
never carries (`tags`, `share_id`, `owner`), so any value matched nothing
and returned an empty page that read as "no such strategy".

Do NOT use to fetch a strategy's full source — call `keel_strategy_get`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._surface_hints import tool_ref
from .open_in_app import app_url_for


def _local_workspaces() -> list[dict[str, Any]]:
    """Best-effort list of local checked-out strategies."""
    try:
        from keel.workspace import list_workspaces

        items = list_workspaces() or []
    except Exception:  # noqa: BLE001 — workspace listing unavailable → empty list
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
    limit: int = int(args.get("limit", 20) or 20)
    cursor: str | None = args.get("cursor")

    # Build remote query. The keel-api list endpoint accepts
    # cursor/limit/sort/search/status.
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
            suggestion=f"{tool_ref('keel_connection_check')} checks the connection to Keel.",
        )

    from ._pagination import extract_paginated

    items_raw, next_cursor = extract_paginated(payload)

    from ._toolsets import is_listed_profile

    # `owner` is the owning org's id: every result is the caller's own org,
    # so on the LISTED profile it is an internal identifier that says
    # nothing (Q-2268); the CLI keeps it.
    with_owner = not is_listed_profile()
    results: list[dict[str, Any]] = []
    for it in items_raw:
        sid = it.get("strategy_id") or it.get("id")
        row: dict[str, Any] = {"strategy_id": sid, "name": it.get("name")}
        if with_owner:
            row["owner"] = it.get("owner") or it.get("org_id")
        row["hero_url"] = app_url_for("strategy", sid, ctx) if sid else None
        row["updated_at"] = it.get("updated_at")
        results.append(row)

    # Merge local workspaces only on CLI when no remote filters specified.
    # Hosted servers have no caller filesystem — explicit no-op there
    # (is_tty is already False on MCP, this makes the invariant local).
    from keel.hosting import is_hosted

    if not query and ctx.is_tty and not is_hosted():
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
        # grounded-in: context-architecture-design Part F (existing strategy:
        # search → get → fork to iterate on a copy); system/chat/collaboration.md §4
        # (iterate on an existing strategy, don't rewrite); system/chat/tool_usage.md:8
        # (state analysis — inspect the current pipeline before changing it).
        description=(
            "Search and list the org's strategies — the entry point for an EXISTING "
            "strategy, before `keel_strategy_get`. It matches `query` (name substring) "
            "and pages with `limit` and `cursor`; each result carries the strategy id, "
            "name and last-updated time. On CLI (TTY) calls with no query, locally "
            "checked-out workspaces are included. The Keel Library's published entries "
            "are `keel_library_list`; components are `keel_components_search`."
        ),
        # Listed-profile copy (agent-surface-cleanup spec 01 §2.5, R-4): the same
        # text minus one surface fact — the hosted server has no local workspaces to include.
        listed_description=(
            "Search and list the org's strategies — the entry point for an EXISTING "
            "strategy, before `keel_strategy_get`. It matches `query` (name substring) "
            "and pages with `limit` and `cursor`; each result carries the strategy id, "
            "name and last-updated time. The Keel Library's published entries are "
            "`keel_library_list`; components are `keel_components_search`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name substring filter."},
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum results (1-100, default 20).",
                },
                "cursor": {"type": "string", "description": "Pagination cursor from prior call."},
            },
            "required": [],
        },
        annotations={
            "title": "Search Strategies",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
