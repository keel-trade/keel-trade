"""`keel_ownership_status` - first-session ownership projection."""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._ownership import fetch_ownership_projection, ownership_envelope_fields


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass a strategy id, e.g. `keel ownership status str_...`.",
        )

    projection = fetch_ownership_projection(ctx, strategy_id)
    resource_uri = f"keel://ownership/strategy/{strategy_id}"
    body: dict[str, Any] = {
        "strategy_id": strategy_id,
        "resource_uri": resource_uri,
        "projection_available": projection is not None,
    }
    if projection:
        body["projection"] = projection
        body.update(ownership_envelope_fields(projection))
    else:
        body.update(
            {
                "ownership_status": "not_started",
                "next_recommended_action": {
                    "kind": "write_strategy_brief",
                    "reason": "No first-session ownership projection is available yet.",
                },
                "missing_evidence": [
                    "strategy_brief",
                    "baseline_evidence",
                    "failure_modes",
                ],
                "live_readiness_blockers": [
                    "no_baseline",
                    "no_diagnosis",
                    "no_ownership_decision",
                    "no_readiness_review",
                ],
            }
        )

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
        share_url=None,
        resource_uri=resource_uri,
        extra=body,
    )


OWNERSHIP_STATUS = register(
    OutcomeTool(
        name="keel_ownership_status",
        required_action="strategy.read",
        cli_path=("ownership", "status"),
        toolset="read-only",
        description=(
            "Fetch the first-session strategy ownership projection for one strategy. "
            "Returns next_recommended_action, missing_evidence, and "
            "live_readiness_blockers for agent guidance. Do NOT use this to run "
            "a backtest or mutate strategy source; call `keel_backtest_run` or "
            "`keel_strategy_compose` for those actions."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id (e.g. `str_abc123`).",
                },
            },
        },
        annotations={
            "title": "Strategy Ownership Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
