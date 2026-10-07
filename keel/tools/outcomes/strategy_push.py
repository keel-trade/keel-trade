"""`keel_strategy_push` — commit local working copy back to the platform.

The "git push" of the lightweight strategy sync model. Reads the
local `strategy.py` and PATCHes the platform via
`/v1/strategies/<id>` to create a new commit (new HEAD). keel-api
validates the saved source; the result carries that verdict as
`validation` (or `{"unavailable": true}` — position-layer spec 04-R22).
Validation never blocks the push, as with compose.

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
from .open_in_app import app_url_for


def push_validation(result: dict) -> dict[str, Any]:
    """The push envelope's validation facts (position-layer spec 04-R22).

    ``validation`` is ``{ok, errors, warnings}`` from keel-api's verdict on
    the saved source, or ``{"unavailable": true}`` when the response carried
    none — never omitted silently, because this tool says it validates.
    ``validation_line`` is the same text line every MCP view result carries
    (errors, then the position-layer upgrade, then other warnings). Push stays
    non-blocking: the version is saved either way, like compose.
    """
    from ._mcp_adapter import _validation_line
    from ._strategy_view import deprecations_block

    raw = result.get("validation")
    out: dict[str, Any] = {}
    if isinstance(raw, dict):
        errors = list(raw.get("errors") or [])
        warnings = list(raw.get("warnings") or [])
        ok = raw.get("ok")
        if ok is None:
            ok = raw.get("valid")
        if ok is None:
            ok = not errors
        validation = {"ok": bool(ok), "errors": errors, "warnings": warnings}
        out["validation"] = validation
        out["validation_line"] = _validation_line(validation) or "validation: clean"
    else:
        out["validation"] = {"unavailable": True}
        out["validation_line"] = (
            "validation: unavailable — the server returned no verdict for this push; "
            "keel_strategy_compose with dry_run=true validates the same source."
        )
    deprecations = deprecations_block(result.get("deprecations"))
    if deprecations:
        from ._known_issue import deprecations_line

        out["deprecations"] = deprecations
        out["upgrade"] = deprecations_line(deprecations)
    return out


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
    if result.get("status") != "no_changes":
        body.update(push_validation(result))
        if result.get("lock_changes"):
            # The pushed text was the position-layer upgrade of HEAD and it
            # moved these pins; they were saved with it (Q-2448).
            body["lock_changes"] = result["lock_changes"]
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
            "Or `keel_strategy_history` to see the full commit history.",
        ]
    return OutcomeResult(
        run_id=resolved_id,
        hero_url=app_url_for("strategy", resolved_id, ctx)
        if resolved_id
        else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_PUSH = register(
    OutcomeTool(
        name="keel_strategy_push",
        required_action="strategy.update",
        cli_path=("strategy", "push"),
        toolset="backtest",
        local_only=True,  # reads the local workspace working copy
        # grounded-in: sync-contract (spec 08) — server HEAD is the source of
        # truth, pushing is how local edits become runnable; conflict-safe by
        # expected_source_hash; system/chat/collaboration.md §6 (validate before, backtest
        # runs against server HEAD).
        description=(
            "Commit local strategy.py changes back to the platform as a new "
            "version — the 'git push' of the sync model. Server HEAD is the "
            "source of truth, and pushing is how local edits become runnable: "
            "it reads the local working copy, validates it, and creates a new "
            "commit (new HEAD). Conflict-safe by default (sends "
            "`expected_source_hash` against what the server had at last "
            "checkout/pull); pass `force=True` only when you've verified no "
            "concurrent work — it overwrites the server HEAD. Auto-detects "
            "`strategy_id` from the current workspace when omitted. Push AFTER "
            "editing strategy.py, BEFORE running a backtest — backtests run "
            "against server HEAD, so unpushed local changes won't be tested; "
            "include a commit `message` so `keel_strategy_history` stays readable. "
            "Do NOT use to CREATE a new strategy from scratch — call "
            "`keel_strategy_compose`. Do NOT use to publish a strategy "
            "publicly — call `keel_share_create`."
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
                        "`keel_strategy_history` and the web app version history. "
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
            "title": "Push Local Strategy Changes",
            "readOnlyHint": False,
            "destructiveHint": False,  # creates new commit, doesn't delete
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
