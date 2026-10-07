"""`keel_strategy_readiness` — what evidence a strategy has, and what it still lacks.

Renamed from `keel_ownership_status` on 2026-10-01 (Q-2080): "ownership"
read as legal or access ownership to OpenAI's name scan, while the tool
reports research readiness — the evidence a strategy carries (a brief, a
baseline backtest, named failure modes) and the one next step. The old name
is a callable alias (`_toolsets.TOOL_ALIASES`).
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._ownership import (
    fetch_projection,
    ownership_envelope_fields,
    projection_unavailable_fields,
)
from ._surface_hints import strategy_ids_hint, usage_hint
from .open_in_app import app_url_for


#: What the LISTED result keeps of keel-api's strategy-work projection
#: (Q-2268): the stage, the one next step, the missing evidence and the
#: latest run — never the session id, UI state, workflow state or raw
#: artifact payloads, which are the in-app chat's plumbing (the full profile
#: keeps the whole projection for the CLI and the `keel://ownership`
#: resource).
LISTED_PROJECTION_FIELDS: tuple[str, ...] = (
    "strategy_id",
    "current_stage",
    "overall_status",
    "next_recommended_action",
    "missing_evidence",
    "latest_backtest",
    "updated_at",
)
LISTED_LATEST_BACKTEST_FIELDS: tuple[str, ...] = (
    "backtest_id",
    "status",
    "sequence_number",
    "date_range",
    "metrics",
    "completed_at",
)


def listed_projection(projection: dict[str, Any]) -> dict[str, Any]:
    """The allow-listed projection a listed result carries."""
    out = {k: projection[k] for k in LISTED_PROJECTION_FIELDS if k in projection}
    latest = out.get("latest_backtest")
    if isinstance(latest, dict):
        out["latest_backtest"] = {
            k: latest[k] for k in LISTED_LATEST_BACKTEST_FIELDS if k in latest
        }
    return out


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=usage_hint(
                "Pass a strategy id, e.g. `keel ownership status str_...`. ",
                "Pass `strategy_id` (`str_...`). ",
            )
            + strategy_ids_hint(),
        )

    fetch = fetch_projection(ctx, strategy_id)
    resource_uri = f"keel://ownership/strategy/{strategy_id}"
    body: dict[str, Any] = {
        "strategy_id": strategy_id,
        "resource_uri": resource_uri,
        "projection_available": fetch.projection is not None,
    }
    if fetch.projection is not None:
        # Includes the never-touched-strategy case: keel-api answers 200 with
        # a real projection whose session_id is null (spec 20 §2.4 N1), so
        # "not started" arrives as computed evidence, never as a guess.
        from ._toolsets import is_listed_profile

        body["projection"] = (
            listed_projection(fetch.projection) if is_listed_profile() else fetch.projection
        )
        body.update(ownership_envelope_fields(fetch.projection))
    else:
        # Honest unavailability with its reason. Deliberately carries NO
        # ownership_status / missing_evidence / live_readiness_blockers: this
        # process read nothing, so it asserts nothing (Q-0500).
        body.update(projection_unavailable_fields(fetch))

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=app_url_for("strategy", strategy_id, ctx),
        share_url=None,
        resource_uri=resource_uri,
        extra=body,
    )


OWNERSHIP_STATUS = register(
    OutcomeTool(
        name="keel_strategy_readiness",
        required_action="strategy.read",
        cli_path=("ownership", "status"),
        toolset="read-only",
        # grounded-in: ownership_status.py _handler fields
        # (next_recommended_action / missing_evidence) and keel-api's
        # strategy-work projection (spec 20). Plain words only (Q-2080): the
        # in-app "maturation arc" vocabulary is the chat's, not this surface's.
        description=(
            "Report what evidence a saved strategy has and still lacks — a brief, a "
            "baseline backtest, named failure modes — and the one next step; a backtest is "
            "`keel_backtest_run`. Returns "
            "`next_recommended_action`, `missing_evidence`, a "
            "`projection` (stage, latest run id and metrics), and `projection_available` "
            "with `unavailable_reason` when the strategy is not visible to the caller."
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
            "title": "Get Strategy Readiness",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
