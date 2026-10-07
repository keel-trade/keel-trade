"""`keel_live_receipt` — the sealed execution receipt for ONE episode.

Q-0913 / audit A8 F-7: nothing on the CLI or MCP surface ever called
`GET /v1/deployments/{id}/executions/{session_id}/revisions/{intent_rev}/receipt`
(keel-api `routers/execution_analytics.py::get_execution_receipt`), so the
entire receipt product — benchmark shortfalls, fee components, markouts,
fills, the child timeline — was unreachable to an agent.

WHY THE ADDRESS IS (session_id, intent_rev) AND NOT A RUN ID
------------------------------------------------------------
A receipt is per EPISODE, not per run. Audit A8 F-1 measured every
post-cutover production run at 11, 15 or 169 episodes and none at 1, and
keel-api serves a run-level `quality_summary` *iff* the run has exactly
one episode — so no run in production names a single receipt, and a CLI
verb shaped `receipt <deployment> <run>` could not be answered truthfully
for any of them. The addressable identity is the one the timeline already
ships: `quality_summary.session_id` + `quality_summary.intent_rev` on a
`keel_live_monitor view='executions'` row.

`version` selects an older immutable version of a corrected receipt; omit
it for the latest.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .open_in_app import app_url_for


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    session_id = (args.get("session_id") or "").strip()
    if not deployment_id or not session_id:
        raise ValidationError(
            "`keel_live_receipt` needs both `deployment_id` and `session_id`.",
            suggestion=(
                "Call `keel_live_monitor` with view='executions' first: each row's "
                "`quality_summary` carries the `session_id` and `intent_rev` that "
                "address its receipt. A run with more than one episode has one "
                "receipt PER episode and no single run-level receipt."
            ),
        )

    intent_rev = int(args.get("intent_rev", 1))
    params: dict[str, Any] = {}
    version = args.get("version")
    if version is not None:
        params["version"] = int(version)

    path = f"/v1/deployments/{deployment_id}/executions/{session_id}/revisions/{intent_rev}/receipt"
    client = ctx.get_client()
    try:
        receipt = client.get(path, **params) if params else client.get(path)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fetch execution receipt: {e}",
            suggestion=(
                "Verify deployment_id / session_id / intent_rev against a "
                "`keel_live_monitor` view='executions' row. A receipt exists "
                "only once the episode has sealed."
            ),
        )

    return OutcomeResult(
        run_id=None,
        hero_url=app_url_for("live", deployment_id, ctx, query={"tab": "executions"}),
        share_url=None,
        extra={
            "deployment_id": deployment_id,
            "session_id": session_id,
            "intent_rev": intent_rev,
            "receipt": receipt,
        },
    )


LIVE_RECEIPT = register(
    OutcomeTool(
        name="keel_live_receipt",
        required_action="execution_analytics.read",
        cli_path=("live", "receipt"),
        toolset="live-read",
        # grounded-in: live_receipt.py module docstring (why the address is
        # per-episode, from audit A8 F-1's measured episode counts) +
        # keel-api routers/execution_analytics.py::get_execution_receipt
        # (the path, the optional `version` query param, the 404 shape) +
        # schemas/live.py::ExecutionQualitySummary (session_id / intent_rev
        # are exposed on the timeline precisely to make this reachable).
        description=(
            "Fetch the sealed execution receipt for ONE episode of a live "
            "deployment — the per-symbol record of what an execution actually "
            "cost: filled and residual quantity, decision- and arrival-benchmark "
            "shortfall in basis points, fee components, completeness and its "
            "missing reasons, and the terminal reason. "
            "ADDRESS: a receipt belongs to an episode, not to a run. Get "
            "`session_id` and `intent_rev` from a `keel_live_monitor` "
            "view='executions' row's `quality_summary`; a run with several "
            "episodes has one receipt per episode and no single run receipt. "
            "Pass `version` only to read an older version of a corrected receipt. "
            "Do NOT use for a whole deployment's cost picture — call "
            "`keel_live_quality`. "
            "Do NOT use to change anything — this endpoint is read-only."
        ),
        input_schema={
            "type": "object",
            "required": ["deployment_id", "session_id"],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Deployment the episode belongs to.",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Execution session (episode) id — `quality_summary."
                        "session_id` on a `keel_live_monitor` executions row."
                    ),
                },
                "intent_rev": {
                    "type": "integer",
                    "default": 1,
                    "description": (
                        "Intent revision of that episode — `quality_summary."
                        "intent_rev`. Defaults to 1."
                    ),
                },
                "version": {
                    "type": "integer",
                    "description": (
                        "Immutable receipt version. Omit for the latest; set it "
                        "only to read a superseded version of a corrected receipt."
                    ),
                },
            },
        },
        annotations={
            "title": "Read Execution Receipt",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
