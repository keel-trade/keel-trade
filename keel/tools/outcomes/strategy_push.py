"""`keel_strategy_push` — commit local working copy back to the platform.

The "git push" of the lightweight strategy sync model. Reads the
local `strategy.py`, validates it (same as `keel_strategy_compose
dry_run=True`), then PATCHes the platform via
`/v1/strategies/<id>` to create a new commit (new HEAD).

Conflict detection: by default sends `expected_source_hash` so the
server rejects with 409 if the server-side HEAD has moved since the
local checkout. Pass `force=True` to override (use sparingly — it
overwrites any concurrent edits).
"""

from __future__ import annotations

from typing import Any

from keel.errors import ConflictError, KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id") or None
    message = args.get("message") or None
    force = bool(args.get("force", False))

    from keel.workspace import push

    try:
        result = push(strategy_id=strategy_id, message=message, force=force)
    except ConflictError:
        raise  # propagate the structured 409
    except ValueError as e:
        # Common case: strategy not checked out, or no strategy_id and not in workspace
        raise KeelError(
            f"Can't push — no local workspace found{f' for {strategy_id}' if strategy_id else ''}: {e}",
            error_code="not_in_workspace",
            exit_code=2,
            suggestion=(
                "Run `keel_strategy_checkout <strategy_id>` first, OR cd into "
                "the workspace directory before pushing. List checked-out "
                "workspaces via `keel_strategy_workspaces`."
            ),
        ) from e

    resolved_id = result.get("strategy_id") or strategy_id
    body: dict[str, Any] = {
        "strategy_id": resolved_id,
        "status": result.get("status"),
        "source_hash": result.get("source_hash"),
        "sequence": result.get("sequence"),
        "commit_id": result.get("commit_id"),
        "message": message,
    }
    if result.get("status") == "no_changes":
        body["next"] = [
            "No local changes detected — nothing to push.",
            "If you expected changes, check `keel_strategy_status` for diff details.",
        ]
    else:
        commit_id = result.get("commit_id")
        seq = result.get("sequence")
        commit_hint = f" (commit_id={commit_id})" if commit_id else ""
        body["next"] = [
            f"Pushed sequence={seq}{commit_hint} — now the new HEAD.",
            "Run `keel_backtest_run` with `--wait` to backtest the new version.",
            "Or `keel_strategy_log` to see the full commit history.",
        ]
    return OutcomeResult(
        run_id=resolved_id,
        hero_url=f"{ctx.app_url}/strategies/{resolved_id}" if resolved_id else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_PUSH = register(
    OutcomeTool(
        name="keel_strategy_push",
        required_action="strategy.update",
        cli_path=("strategy", "push"),
        toolset="backtest",
        description=(
            "Commit local strategy.py changes back to the platform as a new "
            "version. The 'git push' of the sync model. Reads the local "
            "working copy, validates, sends to the API, creates a new commit. "
            "Conflict-safe by default (uses `expected_source_hash` against "
            "what the server had at last checkout/pull). Pass `force=True` "
            "to override conflict detection (overwrites concurrent edits — "
            "use sparingly). If `strategy_id` is omitted, auto-detects from "
            "the current workspace directory. "
            "Use AFTER editing strategy.py locally, BEFORE running a backtest — "
            "backtest runs against server HEAD, so unpushed local changes "
            "won't be tested. Include a commit `message` so the version "
            "history is readable (`keel_strategy_log` shows messages)."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Strategy to push. If omitted, auto-detects from the "
                        "workspace (set via `keel_strategy_checkout` or "
                        "by being in the workspace directory)."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Commit message. Highly recommended — shows in "
                        "`keel_strategy_log` and the web app version history. "
                        "Like a git commit message."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Skip conflict detection. Overwrites server HEAD even "
                        "if it moved since checkout. Use only when you've "
                        "verified there's no concurrent work."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,  # creates new commit, doesn't delete
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
