"""`keel_deployments_list` — enumerate the org's live deployments.

Q-0913 / audit A8 F-7: before this existed the ONLY cross-deployment read
on the CLI/MCP surface was `keel_live_monitor view=portfolio`, which
serves `PortfolioSummaryResponse` — active deployments only, no cursor, no
`account_id`, no deployed version. An agent asked "which deployments do I
have?" could not answer for a stopped one, and could not get the
`account_id` it needs for `keel_accounts_safety`.

This is the thin twin of `GET /v1/deployments` (keel-api
`routers/live.py::list_deployments`): the same `PaginatedResponse` shape
every list endpoint uses, `include_stopped` to widen past
`ACTIVE_STATUSES`, and `DeploymentResponse` rows carrying
`account_id` / `deployed_commit_id` / `deployed_version_string` /
`total_pnl` / `position_count`.

Read-only. Deploying, pausing, and stopping are elsewhere by design.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._pagination import extract_paginated


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    params: dict[str, Any] = {}
    limit = args.get("limit")
    if limit is not None:
        params["limit"] = int(limit)
    cursor = args.get("cursor")
    if cursor:
        params["cursor"] = cursor
    if args.get("include_stopped"):
        params["include_stopped"] = True

    client = ctx.get_client()
    try:
        result = client.get("/v1/deployments", **params)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to list deployments: {e}",
            suggestion="Run `keel_connection_check` to check auth / API reachability.",
        )

    items, next_cursor = extract_paginated(result)
    extra: dict[str, Any] = {"deployments": items, "deployment_count": len(items)}
    if next_cursor:
        extra["next_cursor"] = next_cursor

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/live",
        share_url=None,
        extra=extra,
    )


DEPLOYMENTS_LIST = register(
    OutcomeTool(
        name="keel_deployments_list",
        required_action="runner.read",
        cli_path=("deployments", "list"),
        toolset="live-read",
        # grounded-in: deployments.py module docstring (Q-0913 / A8 F-7 —
        # the portfolio summary is active-only and carries no account_id)
        # + keel-api routers/live.py::list_deployments (the
        # `include_stopped` filter over ACTIVE_STATUSES and the
        # DeploymentResponse projection this returns verbatim).
        description=(
            "List every deployment in your org with its id, strategy, status, "
            "schedule, account_id, deployed version, realized P&L, and open "
            "position count. Pass `include_stopped=true` to include stopped "
            "and archived ones; results are cursor-paginated. "
            "This is the id-lookup step: take `deployment_id` from here into "
            "`keel_live_monitor`, `keel_live_receipt`, or `keel_live_quality`, "
            "and `account_id` into `keel_accounts_safety`. "
            "Do NOT use for aggregate performance across deployments — call "
            "`keel_live_monitor` with view='portfolio'. "
            "Do NOT use to change a deployment's state — call "
            "`keel_live_control`."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "include_stopped": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include stopped/archived deployments as well as the "
                        "active ones (default: active only)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max deployments per page (default 20, server cap 100).",
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor returned by a previous call.",
                },
            },
        },
        annotations={
            "title": "List Deployments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
