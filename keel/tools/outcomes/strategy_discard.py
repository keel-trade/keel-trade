"""`keel_strategy_discard` — remove a local workspace (server-side strategy unchanged).

Like `git remote rm` for a checked-out strategy. Deletes the local
working copy + sync metadata. Does NOT delete the server-side
strategy — use `keel_strategy_delete` for that.

Local-only operation; no API call. Useful for cleaning up workspaces
after you're done iterating, or when divergence needs a fresh start
("re-checkout from HEAD instead of trying to merge").
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id") or None

    from keel.workspace import discard

    try:
        result = discard(strategy_id=strategy_id)
    except ValueError as e:
        raise KeelError(
            f"Couldn't discard workspace{f' for {strategy_id}' if strategy_id else ''}: {e}",
            error_code="discard_failed",
            exit_code=2,
            suggestion=(
                "If no workspace exists, there's nothing to discard. List "
                "checked-out workspaces via `keel_strategy_workspaces`."
            ),
        ) from e

    resolved_id = result.get("strategy_id") or strategy_id
    body: dict[str, Any] = {
        "strategy_id": resolved_id,
        "status": result.get("status", "discarded"),
        "removed_workspace": result.get("workspace"),
        "next": [
            "Local workspace removed. Server-side strategy is unchanged — "
            "still accessible via `keel_strategy_get` or `keel_strategy_checkout`.",
            "To DELETE the server-side strategy too: `keel_strategy_delete`.",
        ],
    }
    return OutcomeResult(
        run_id=resolved_id,
        hero_url=f"{ctx.app_url}/strategies/{resolved_id}"
        if resolved_id
        else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_DISCARD = register(
    OutcomeTool(
        name="keel_strategy_discard",
        required_action="strategy.read",
        cli_path=("strategy", "discard"),
        toolset="backtest",
        local_only=True,  # deletes local workspace files
        # grounded-in: strategy_discard.py docstring (local-only removal,
        # server strategy untouched); spec 08 sync contract (reset a diverged
        # workspace by re-checking out fresh, not manual merge).
        description=(
            "Remove a local workspace — the checked-out `strategy.py` + "
            "`.keel-meta.json`. Local-only: the server-side strategy is "
            "untouched and still available via `keel_strategy_get` or "
            "`keel_strategy_checkout`. Use it to clean up after you're done "
            "iterating, or to reset a diverged workspace by re-checking out "
            "fresh (a clean re-checkout beats a manual merge). "
            "Do NOT use to delete a strategy on the platform — call "
            "`keel_strategy_delete` (destructive + irreversible). This tool "
            "only touches local filesystem state."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy workspace to discard. Auto-detected if omitted.",
                },
            },
        },
        annotations={
            "title": "Discard Local Workspace",
            "readOnlyHint": False,  # deletes local files
            "destructiveHint": True,  # deletes local working copy
            "idempotentHint": True,
            "openWorldHint": False,
        },
        confirm_in_cli=True,  # require --yes since it deletes local work
        handler=_handler,
    )
)
