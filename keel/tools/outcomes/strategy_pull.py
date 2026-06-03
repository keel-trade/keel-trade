"""`keel_strategy_pull` — re-fetch server HEAD into the local workspace.

The "git pull" of the sync model. Refreshes the local strategy.py
with whatever's currently at server HEAD. Refuses if local has
uncommitted changes (diverged state) — caller must push or discard
first. Pass `force=True` to override and overwrite local.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id") or None
    force = bool(args.get("force", False))

    from keel.workspace import pull, pull_force

    try:
        if force and strategy_id:
            result = pull_force(strategy_id)
        else:
            result = pull(strategy_id=strategy_id)
    except ValueError as e:
        raise KeelError(
            f"Couldn't pull strategy{f' {strategy_id}' if strategy_id else ''}: {e}",
            error_code="pull_failed",
            exit_code=2,
            suggestion=(
                "If local has uncommitted changes (status `diverged` or `ahead`): "
                "run `keel_strategy_push -m 'msg'` first OR `keel_strategy_pull "
                "force=True` to overwrite local (LOSES local edits). "
                "Use `keel_strategy_status` to see which case you're in."
            ),
        ) from e

    resolved_id = result.get("strategy_id") or strategy_id
    status = result.get("status")
    # Lib's pull() returns four shapes — translate each to a clear hint.
    if status == "pulled" or status == "force_pulled":
        next_hints = [
            "Local working copy is now at server HEAD.",
            "Open the file in your editor to see changes.",
            "Use `keel_strategy_log` to see what changed since your last checkout.",
        ]
    elif status == "current":
        next_hints = [
            "Already at server HEAD — nothing to pull.",
        ]
    elif status == "local_changes":
        # Remote is unchanged; local has unpushed edits. Pull is a no-op
        # but DON'T say "now at server HEAD" — that's misleading.
        next_hints = [
            "Remote hasn't moved, but you have unpushed local edits.",
            "Push them when ready: `keel_strategy_push -m '<msg>'`.",
            "Or to discard local edits: `keel_strategy_pull force=True` (LOSES local work).",
        ]
    elif status == "conflict":
        # Both moved — explicit resolution required.
        next_hints = [
            "Local AND server both moved — divergent history.",
            "Resolve by: (a) `keel_strategy_push force=True` to overwrite server, "
            "(b) `keel_strategy_pull force=True` to overwrite local (LOSES local edits), "
            "or (c) `keel_strategy_discard` + re-checkout to start fresh.",
        ]
    else:
        next_hints = [f"Pull result status={status!r}."]

    body: dict[str, Any] = {
        "strategy_id": resolved_id,
        "status": status,
        "source_hash": result.get("source_hash"),
        "server_sequence": result.get("sequence"),
        "local_hash": result.get("local_hash"),
        "server_hash": result.get("remote_hash"),  # match status outcome
        "next": next_hints,
    }
    return OutcomeResult(
        run_id=resolved_id,
        hero_url=f"{ctx.app_url}/strategies/{resolved_id}"
        if resolved_id
        else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_PULL = register(
    OutcomeTool(
        name="keel_strategy_pull",
        required_action="strategy.read",
        cli_path=("strategy", "pull"),
        toolset="backtest",
        description=(
            "Re-fetch the server HEAD into the local working copy. The 'git "
            "pull' of the sync model. Refuses if local has uncommitted "
            "changes (diverged state) so you don't lose work — push first "
            "or pass `force=True` to overwrite local. Use when you suspect "
            "someone (a teammate, the web editor, a fork) updated the strategy "
            "while you were working locally. Check `keel_strategy_status` "
            "first to see if a pull is needed."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy to pull. Auto-detected from workspace if omitted.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Overwrite local changes with server HEAD. LOSES "
                        "local edits — only use after explicit confirmation."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": False,  # writes to local filesystem
            "destructiveHint": False,
            "idempotentHint": True,  # pulling twice gives the same result
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
