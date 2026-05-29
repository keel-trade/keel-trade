"""`keel_strategy_checkout` — pull a platform strategy into a local workspace.

Per `projects/agent-v2/01-current-state-audit.md`: the lightweight-git
sync verbs (checkout / push / pull / status / workspaces / discard)
form the canonical model for cross-surface collaboration (web app ↔
local editor ↔ MCP agent). This wrapper promotes the existing
`keel.workspace.checkout()` library function to a first-class MCP
outcome + CLI verb.

Workflow:

  $ keel strategy checkout str_abc123
  → fetches HEAD source from /v1/strategies/<id>
  → writes strategy.py + .keel-meta.json to the workspace dir
  → prints the local file path

Companion verbs: `keel_strategy_push` (commit local changes back),
`keel_strategy_pull` (re-fetch HEAD), `keel_strategy_status`
(ahead/behind/diverged), `keel_strategy_workspaces` (list all checked
out), `keel_strategy_discard` (remove local without touching server).

Do NOT use to CREATE a new strategy — call `keel_strategy_compose`
with no `strategy_id`. Do NOT use to download backtest results or
share artifacts — call the relevant backtest / share outcome.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass a strategy id (e.g. `keel strategy checkout str_abc123` or "
                "`keel_strategy_checkout(strategy_id='str_abc123')`). Find ids "
                "via `keel_strategy_search`."
            ),
        )

    target_dir = args.get("dir") or None

    from keel.workspace import checkout

    try:
        result = checkout(strategy_id, target_dir=target_dir)
    except ValueError as e:
        raise KeelError(
            str(e),
            error_code="checkout_failed",
            exit_code=1,
            suggestion=(
                "Verify the strategy id via `keel_strategy_search` or "
                "`keel_strategy_get`. If the source is empty server-side, "
                "the strategy needs `keel_strategy_compose` to push its "
                "first version."
            ),
        ) from e

    mode = result.get("mode")
    next_steps = [
        f"Open `{result.get('file')}` in your editor to make changes.",
        "After editing: `keel_strategy_status` to see local vs server diff.",
        "When ready to commit: `keel_strategy_push -m 'msg'`.",
    ]
    if mode == "home" and result.get("hint"):
        # Surface the project-init hint as the first next-step so the
        # agent sees it before the generic edit-flow steps.
        next_steps.insert(0, result["hint"])

    body: dict[str, Any] = {
        "strategy_id": result["strategy_id"],
        "name": result.get("name"),
        "workspace": result.get("workspace"),
        "file": result.get("file"),
        "source_hash": result.get("source_hash"),
        "sequence": result.get("sequence"),
        "status": result.get("status", "checked_out"),
        "mode": mode,
        "next": next_steps,
    }
    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
        share_url=None,
        extra=body,
    )


STRATEGY_CHECKOUT = register(
    OutcomeTool(
        name="keel_strategy_checkout",
        required_action="strategy.read",
        cli_path=("strategy", "checkout"),
        toolset="backtest",
        description=(
            "Pull a platform strategy into a local workspace so you can edit, "
            "validate, and version-control it. Writes `strategy.py` + "
            "`.keel-meta.json` to the workspace dir (defaults to "
            "`~/.keel/workspace/<id>/`; project-local when cwd has "
            "`.keel/workspace.yaml`). Subsequent edits are local until "
            "`keel_strategy_push` commits them back. "
            "Use this BEFORE iterating on an existing strategy — even small "
            "edits should go through checkout → edit → push, NOT raw "
            "`keel_strategy_compose` calls that bypass version history. "
            "Do NOT use to CREATE a new strategy — call `keel_strategy_compose`. "
            "Do NOT use for backtest artifacts — those are read-only resources."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Platform strategy id (e.g. `str_01knsm...`). Discover "
                        "via `keel_strategy_search`."
                    ),
                },
                "dir": {
                    "type": "string",
                    "description": (
                        "Override target dir. Defaults to `<project>/strategies/<id>/` "
                        "when cwd has `.keel/workspace.yaml`, else `~/.keel/"
                        "workspace/<id>/`. Pass an absolute path to land the "
                        "checkout somewhere specific."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": False,  # writes to local filesystem
            "destructiveHint": False,
            "idempotentHint": False,  # re-running overwrites local if hash matches HEAD
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
