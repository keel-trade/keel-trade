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

from keel.errors import KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._pagination import extract_paginated


#: A mixed result's row labels by class (paper-trading-v2 S06 §3.5.1, PT2-16).
#: Plain one-word data labels, not prose: the tool is not listed, and the
#: paper strings module's rows are sentences (its probes need 12 characters).
_ROW_LABELS: dict[str, str] = {"real": "Live", "practice": "Paper"}


def _row_label(row: Any) -> str | None:
    """A mixed result's row label, read from ``venue.capital_class`` and from
    nothing else: never the status, the id or ``venue.code`` (null for a row
    with no account). A row with no venue block gets no label."""
    venue = row.get("venue") if isinstance(row, dict) else None
    capital_class = venue.get("capital_class") if isinstance(venue, dict) else None
    return _ROW_LABELS.get(capital_class) if isinstance(capital_class, str) else None


def _practice_status(row: Any) -> Any:
    """A practice row with its ``status`` in keel-app's words (Deploying /
    Paper / Paused / Stopped), never the raw ``LIVE`` (QA-W2). Keyed on
    ``venue.capital_class`` like the row label; a real row is relayed as sent."""
    venue = row.get("venue") if isinstance(row, dict) else None
    if not isinstance(venue, dict) or venue.get("capital_class") != "practice":
        return row
    if "status" not in row:
        return row
    from ._paper_status import paper_status_word

    word = paper_status_word(
        row.get("status"), health=row.get("health"), pause_reason=row.get("pause_reason")
    )
    return {**row, "status": word}


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
    # paper-trading-v2 S06 §3.5.1: the class filter is the held paper form of
    # this tool (§3.11 item 1, PT2-28), read at call time; while the paper
    # SDK is held the tool is HEAD's and sends no class.
    from . import _toolsets

    capital_class = args.get("capital_class") or None
    if _toolsets.PAPER_SDK_RELEASED:
        # The route reads an absent class as `real` (PT2-28), so a caller that
        # names none gets `all`: this tool is the id lookup for everything the
        # org runs, and each row of that mixed result is labelled below.
        capital_class = capital_class or "all"
        if capital_class not in CAPITAL_CLASSES:
            raise ValidationError(
                f"Unknown capital_class {capital_class!r}; it is one of "
                f"{', '.join(CAPITAL_CLASSES)}. Nothing was read.",
                suggestion=f"Pass capital_class as one of: {', '.join(CAPITAL_CLASSES)}.",
                input={"capital_class": capital_class},
            )
        params["capital_class"] = capital_class

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
    if _toolsets.PAPER_SDK_RELEASED:
        items = [_practice_status(row) for row in items]
    if _toolsets.PAPER_SDK_RELEASED and capital_class == "all":
        # A mixed result labels each row Live or Paper; a one-class request
        # already names its class, so its rows are relayed unlabelled.
        labelled = []
        for row in items:
            label = _row_label(row)
            labelled.append({**row, "label": label} if label else row)
        items = labelled
    extra: dict[str, Any] = {"deployments": items, "deployment_count": len(items)}
    if next_cursor:
        extra["next_cursor"] = next_cursor

    paper_only = _toolsets.PAPER_SDK_RELEASED and capital_class == "practice"
    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/paper" if paper_only else f"{ctx.app_url}/live",
        share_url=None,
        extra=extra,
    )


#: The route's class filter values (S01 IC-01-9).
CAPITAL_CLASSES: tuple[str, ...] = ("real", "practice", "all")

_INPUT_SCHEMA: dict[str, Any] = {
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
}

#: The held paper form of the schema (§3.5.1): the class filter.
_PAPER_INPUT_SCHEMA: dict[str, Any] = {
    **_INPUT_SCHEMA,
    "properties": {
        **_INPUT_SCHEMA["properties"],
        "capital_class": {
            "type": "string",
            "enum": list(CAPITAL_CLASSES),
            "description": (
                "Which runs to list: 'real' (live deployments), 'practice' (paper "
                "runs, simulated) or 'all'."
            ),
        },
    },
}


_DESCRIPTION = (
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
)

#: The held paper form of the description (S06 §3.5.1): the parameter, the
#: `all` default with its Live/Paper labels, that paper runs are simulated,
#: and `keel_paper_control` beside `keel_live_control` in the state pointer.
_PAPER_DESCRIPTION = (
    "List every deployment in your org with its id, strategy, status, "
    "schedule, account_id, deployed version, realized P&L, and open "
    "position count. By default it lists both live deployments and paper "
    "runs, each row labelled `Live` or `Paper` from its `venue`; paper runs "
    "are simulated. Pass `capital_class` ('real', 'practice' or 'all') to "
    "list one class, and `include_stopped=true` to include stopped and "
    "archived ones; results are cursor-paginated. "
    "This is the id-lookup step: take `deployment_id` from here into "
    "`keel_live_monitor`, `keel_live_receipt`, or `keel_live_quality`, "
    "and `account_id` into `keel_accounts_safety`. "
    "Do NOT use for aggregate performance across deployments — call "
    "`keel_live_monitor` with view='portfolio'. "
    "Do NOT use to change a deployment's state — call "
    "`keel_live_control` for a Live row, or `keel_paper_control` for a "
    "Paper row."
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
        description=_DESCRIPTION,
        # The held paper form (§3.5.1; §3.11 item 1).
        paper_description=_PAPER_DESCRIPTION,
        input_schema=_INPUT_SCHEMA,
        paper_input_schema=_PAPER_INPUT_SCHEMA,
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
