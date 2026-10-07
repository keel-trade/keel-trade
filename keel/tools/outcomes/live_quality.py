"""`keel_live_quality` — the deployment's precomputed execution-quality rollup.

Q-0913 / audit A8 F-7: `GET /v1/deployments/{id}/execution-quality`
(keel-api `routers/execution_analytics.py::get_execution_quality` ->
`services/execution_analytics.py::fetch_precomputed_quality`) had no
caller on the CLI or MCP surface, so the only cost picture an agent could
offer was `keel_live_monitor view='stats'`.

The rollup is a PROJECTION OF SEALED RECEIPTS, never a recomputation
(2026-08-06 "one computation owner" lesson): whatever it reports was
sealed by the observer first. It is optionally sliced by date window and
by execution style — `style` / `style_version` exist because a deployment
can execute market on one bar and maker on the next, and pooling the two
would hide which one the numbers describe.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .open_in_app import app_url_for


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    if not deployment_id:
        raise ValidationError(
            "`keel_live_quality` needs a `deployment_id`.",
            suggestion="List yours with `keel_deployments_list`.",
        )

    params: dict[str, Any] = {}
    for key in ("start_date", "end_date", "style", "style_version"):
        value = args.get(key)
        if value not in (None, ""):
            params[key] = value

    client = ctx.get_client()
    path = f"/v1/deployments/{deployment_id}/execution-quality"
    try:
        quality = client.get(path, **params) if params else client.get(path)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fetch execution quality for {deployment_id}: {e}",
            suggestion=(
                "Check the deployment id with `keel_deployments_list`. "
                "`start_date` must be strictly earlier than the exclusive "
                "`end_date`."
            ),
        )

    return OutcomeResult(
        run_id=deployment_id,
        hero_url=app_url_for("live", deployment_id, ctx, query={"tab": "analysis"}),
        share_url=None,
        extra={"deployment_id": deployment_id, "execution_quality": quality},
    )


LIVE_QUALITY = register(
    OutcomeTool(
        name="keel_live_quality",
        required_action="execution_analytics.read",
        cli_path=("live", "quality"),
        toolset="live-read",
        # grounded-in: live_quality.py module docstring (the rollup projects
        # sealed receipts and never recomputes them; style slicing exists so
        # maker and market bars are not pooled) + keel-api
        # routers/execution_analytics.py::get_execution_quality (the four
        # optional query params and the start<end 422).
        description=(
            "Read a live deployment's precomputed execution-quality rollup: the "
            "aggregated cost of its executions, projected from sealed receipts "
            "rather than recomputed. Optionally narrow to a date window "
            "(`start_date` inclusive, `end_date` exclusive) or to one execution "
            "style (`style`, `style_version`) so market and maker bars are not "
            "pooled into one number. "
            "Use this for 'what has execution cost me on this deployment'; use "
            "`keel_live_receipt` for one episode's own evidence. "
            "Do NOT use for realized strategy P&L — call `keel_live_monitor` "
            "with view='pnl' or view='stats'. "
            "Do NOT use to change anything — this endpoint is read-only."
        ),
        input_schema={
            "type": "object",
            "required": ["deployment_id"],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Deployment to summarize.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Inclusive lower bound, ISO date (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": (
                        "Exclusive upper bound, ISO date (YYYY-MM-DD). Must be "
                        "strictly later than start_date."
                    ),
                },
                "style": {
                    "type": "string",
                    "description": (
                        "Restrict to one execution style (e.g. the market or the "
                        "maker lane) instead of pooling both."
                    ),
                },
                "style_version": {
                    "type": "string",
                    "description": "Restrict to one version of that execution style.",
                },
            },
        },
        annotations={
            "title": "Read Execution Quality Rollup",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
