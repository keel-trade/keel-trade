"""`keel_strategy_delete` — hard-delete a strategy.

Replaces: `strategy_archive`, `strategy_discard`.

Calls `DELETE /v1/strategies/{id}` — destructive, non-idempotent.
Workspace artifacts on disk are NOT touched; clean those up locally
if needed.

Do NOT use to stop a live deployment — call `keel_live_control` with
action=stop.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id: str = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass the strategy id explicitly (e.g. `keel_strategy_delete "
                "strategy_id=str_abc`). Find ids via `keel_strategy_search`. "
                "If you just want to drop the LOCAL workspace and leave the "
                "server strategy intact, use `keel_strategy_discard` instead."
            ),
        )

    client = ctx.get_client()
    try:
        client.delete(f"/v1/strategies/{strategy_id}")
    except NotFoundError:
        raise NotFoundError(
            f"Strategy not found: {strategy_id}",
            suggestion="Run `keel strategy search` to list available strategies.",
        )
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to delete strategy {strategy_id}: {e}",
            suggestion=(
                "If the strategy has an active live deployment or queued "
                "backtest, the server rejects delete. Stop the deployment "
                "(`keel_live_stop`) and wait for in-flight backtests, then "
                "retry. Otherwise run `keel_doctor`."
            ),
        )

    extra: dict[str, Any] = {"strategy_id": strategy_id, "deleted": True}

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies",
        share_url=None,
        extra=extra,
    )


STRATEGY_DELETE = register(
    OutcomeTool(
        name="keel_strategy_delete",
        required_action="strategy.delete",
        cli_path=("strategy", "delete"),
        toolset="backtest",
        description=(
            "Hard-delete a strategy on the platform. Non-idempotent — once "
            "deleted, the strategy_id cannot be reused and version history "
            "is gone. Local workspace files are NOT removed; clean those up "
            "separately. "
            "Do NOT use to stop a live deployment — call `keel_live_control` "
            "with action=stop. "
            "Do NOT use to remove a local workspace — use the workspace tools."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id to delete.",
                },
            },
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
