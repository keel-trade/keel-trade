"""`keel_strategy_status` — compare local vs server HEAD.

The "git status" of the sync model. Four possible states:

  * `clean`     — local hash matches server HEAD; nothing to push or pull
  * `ahead`     — local has commits server doesn't (push to share)
  * `behind`    — server has commits local doesn't (pull to catch up)
  * `diverged`  — both moved independently (push + force, OR pull --force, OR manual resolution)

Call this BEFORE every push or pull to know what state you're in.
Especially important after coming back from the web editor (someone
else might have made changes).
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id") or None

    # Default include_recent=True so agents see the last 5 commits with
    # every status check — they need "what just happened" context to
    # decide whether to undo, push, or proceed. Cheap (1 extra GET) and
    # the agent can opt out for hot polling via `include_recent=False`.
    include_recent = args.get("include_recent", True)
    try:
        recent_n = int(args.get("recent_commits", 5))
    except (TypeError, ValueError):
        recent_n = 5
    recent_n = max(0, min(recent_n, 20))
    if not include_recent:
        recent_n = 0

    from keel.workspace import status

    try:
        result = status(strategy_id=strategy_id, recent_commits=recent_n)
    except ValueError as e:
        raise KeelError(
            f"Couldn't read workspace status{f' for {strategy_id}' if strategy_id else ''}: {e}",
            error_code="status_failed",
            exit_code=2,
            suggestion=(
                "If no workspace exists for the strategy, run "
                "`keel_strategy_checkout <strategy_id>` first. List "
                "checked-out workspaces via `keel_strategy_workspaces`."
            ),
        ) from e

    resolved_id = result.get("strategy_id") or strategy_id
    # Lib returns `state` not `status`, and uses "current"/"conflict";
    # the agent-facing vocabulary is "clean"/"diverged" — translate.
    raw_state = result.get("state", "unknown")
    state = {
        "current": "clean",
        "conflict": "diverged",
    }.get(raw_state, raw_state)

    next_hints: list[str] = []
    if state == "clean":
        next_hints = [
            "Local matches server HEAD — no sync action needed.",
            "Run `keel_backtest_run` to backtest the current version.",
        ]
    elif state == "ahead":
        next_hints = [
            "Local has uncommitted changes. Commit + push via "
            "`keel_strategy_push -m 'msg'` BEFORE running a backtest — "
            "backtests run against server HEAD, not local.",
        ]
    elif state == "behind":
        next_hints = [
            "Server has newer commits. Pull via `keel_strategy_pull` to "
            "catch up. Use `keel_strategy_log` to see what changed.",
        ]
    elif state == "diverged":
        next_hints = [
            "Local AND server both moved since checkout — divergent history.",
            "Resolve by: (a) `keel_strategy_push force=True` to overwrite server "
            "with local (loses server changes), OR (b) `keel_strategy_pull "
            "force=True` to overwrite local with server (loses your local "
            "changes), OR (c) `keel_strategy_discard` and re-checkout, OR "
            "(d) compare with `keel_strategy_diff` and manually merge.",
        ]

    body: dict[str, Any] = {
        "strategy_id": resolved_id,
        "status": state,
        "local_hash": result.get("local_hash"),
        "server_hash": result.get("remote_hash"),
        "server_sequence": result.get("sequence"),
        "name": result.get("name"),
        "workspace": result.get("workspace"),
        "file": result.get("file"),
        "next": next_hints,
    }
    if result.get("recent_commits") is not None:
        body["recent_commits"] = result["recent_commits"]
    return OutcomeResult(
        run_id=resolved_id,
        hero_url=f"{ctx.app_url}/strategies/{resolved_id}"
        if resolved_id
        else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_STATUS = register(
    OutcomeTool(
        name="keel_strategy_status",
        required_action="strategy.read",
        cli_path=("strategy", "status"),
        toolset="backtest",
        description=(
            "Compare a local workspace's strategy.py against the server's "
            "current HEAD. The 'git status' of the sync model. Returns one "
            "of: `clean` (in sync), `ahead` (local has uncommitted changes — "
            "push first), `behind` (server moved, pull to catch up), "
            "`diverged` (both moved — needs explicit resolution). "
            "By default also returns the last 5 commits in `recent_commits` "
            "so the agent has 'what just happened' context alongside sync "
            "state (set `include_recent=False` for hot polling loops). "
            "Call this BEFORE `keel_backtest_run` — backtests use server "
            "HEAD, so unpushed local changes won't be tested. Also call "
            "after coming back from the web editor (someone else may have "
            "edited). Auto-detects the strategy from the current workspace "
            "if `strategy_id` is omitted. "
            "Do NOT use to list ALL workspaces — call `keel_strategy_workspaces`. "
            "Do NOT use to inspect full history — call `keel_strategy_log`."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy to check. Auto-detected from workspace if omitted.",
                },
                "include_recent": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Include the most recent commits in the response "
                        "(default: last 5). Lets the agent see 'what just "
                        "happened' alongside sync state. Set False for hot "
                        "polling loops where one extra GET is too expensive."
                    ),
                },
                "recent_commits": {
                    "type": "integer",
                    "default": 5,
                    "description": (
                        "How many recent commits to include when "
                        "`include_recent=True`. Clamped to 0..20."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
