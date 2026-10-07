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
from .open_in_app import app_url_for


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

    if status == "conflict":
        # Both moved — a TRUE conflict. Stop with the spec-08 R4 envelope
        # (three-way context + recovery options). Never resolved silently.
        from keel.workspace import build_conflict_envelope

        raise build_conflict_envelope(
            resolved_id,
            base_hash=result.get("base_hash"),
            local_hash=result.get("local_hash"),
            server_hash=result.get("remote_hash"),
            action="pull",
        )

    # Lib's pull() returns the remaining shapes — translate each to a
    # clear hint.
    if status == "pulled" or status == "force_pulled":
        next_hints = [
            "Local working copy is now at server HEAD.",
            "Open the file in your editor to see changes.",
            "Use `keel_strategy_history` to see what changed since your last checkout.",
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
        hero_url=app_url_for("strategy", resolved_id, ctx)
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
        local_only=True,  # writes the local workspace working copy
        # grounded-in: sync-contract (spec 08 R3/R4) — a stale/behind
        # workspace has exactly one instruction (pull); diverged is refused
        # unless forced; strategy_pull.py docstring.
        description=(
            "Re-fetch the server HEAD into the local working copy — the 'git "
            "pull' of the sync model. Refuses when local has uncommitted "
            "changes (diverged) so you don't silently lose work; push first, "
            "or pass `force=True` to overwrite local (LOSES local edits). Use "
            "it when someone else — a teammate, the web editor, a fork, a "
            "restore — may have moved the strategy while you were working "
            "locally; check `keel_strategy_status` first to see whether a "
            "pull is even needed. "
            "Do NOT use to send local edits UP — call `keel_strategy_push`. "
            "Do NOT use to inspect what changed — call `keel_strategy_history`."
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
            "title": "Pull Strategy Updates",
            "readOnlyHint": False,  # writes to local filesystem
            "destructiveHint": False,
            "idempotentHint": True,  # pulling twice gives the same result
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
