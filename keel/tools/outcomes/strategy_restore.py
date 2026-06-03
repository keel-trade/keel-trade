"""`keel_strategy_restore` — restore a historical commit as new HEAD.

Wraps `POST /v1/strategies/<id>/versions/restore`. Server-side
operation: takes an old commit ref (sequence number, commit_id, or
tag), reads the source from that commit, creates a NEW commit on
HEAD with that source. So history is preserved (you can see the
restore as a new entry in `keel_strategy_log`).

The "git revert" of the sync model — except it creates a forward
commit rather than a reverse-diff commit. Use this when an agent or
user made a change that should be undone, OR when a backtest of an
older version showed better results and you want to go back.

After restoring, the local workspace (if checked out) will be
'behind' — pull to catch up.
"""

from __future__ import annotations

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    ref = (args.get("ref") or "").strip()

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass the strategy id explicitly (e.g. "
                "`keel_strategy_restore strategy_id=str_abc ref=3`). Find "
                "ids via `keel_strategy_search` or "
                "`keel_strategy_workspaces`."
            ),
        )
    if not ref:
        raise KeelError(
            "Missing required `ref` — the commit to restore.",
            error_code="missing_ref",
            exit_code=2,
            suggestion=(
                "Pass a sequence number, commit_id, or tag (e.g. "
                "`keel_strategy_restore strategy_id=str_abc ref=3` or "
                "`ref=cmt_xyz` or `ref=v1.0`). Use `keel_strategy_log` "
                "to find the ref you want."
            ),
        )

    message = args.get("message") or f"Restore version {ref}"

    client = ctx.get_client()
    try:
        result = client.post(
            f"/v1/strategies/{strategy_id}/versions/restore",
            json={"ref": ref, "message": message},
        )
    except NotFoundError:
        raise
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to restore strategy {strategy_id}@{ref}: {e}",
            suggestion="Verify the ref exists via `keel_strategy_log`.",
        ) from e

    new_sequence = result.get("current_sequence") or result.get("sequence")

    # API returns StrategyResponse (no commit_id). Helper does the
    # single follow-up GET on /versions?limit=1 — same pattern as push.
    new_commit_id: str | None = result.get("commit_id")
    if not new_commit_id:
        from keel.workspace import _fetch_latest_commit_id

        new_commit_id = _fetch_latest_commit_id(client, strategy_id)
    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
        share_url=None,
        extra={
            "strategy_id": strategy_id,
            "restored_from_ref": ref,
            "new_sequence": new_sequence,
            "new_commit_id": new_commit_id,
            "message": message,
            "next": [
                f"Restored. New HEAD is sequence={new_sequence}, with source from {ref}.",
                "Local workspace (if checked out) is now 'behind' — run "
                "`keel_strategy_pull` to sync.",
                "Backtest the restored version: `keel_backtest_run`.",
            ],
        },
    )


STRATEGY_RESTORE = register(
    OutcomeTool(
        name="keel_strategy_restore",
        required_action="strategy.update",
        cli_path=("strategy", "restore"),
        toolset="backtest",
        description=(
            "Restore a historical commit as the new HEAD. Server-side "
            "operation: reads source from the named ref (sequence number, "
            "commit_id, or tag), creates a new commit on HEAD with that "
            "source. History is preserved — the restore shows up as a new "
            "commit in `keel_strategy_log`. The 'git revert' (forward-revert) "
            "of the sync model. "
            "Use when an edit should be undone, OR when an older version "
            "backtested better and you want to go back. Use "
            "`keel_strategy_log` first to find the ref. After restore, "
            "local workspace (if checked out) is 'behind' — run "
            "`keel_strategy_pull` to catch up. "
            "Do NOT use for forking into a new strategy — call "
            "`keel_strategy_fork`. Do NOT use to discard local edits — "
            "that's `keel_strategy_pull force=True` or `keel_strategy_discard`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id", "ref"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy to restore.",
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Which commit to restore. Accepts: sequence number "
                        "(e.g. `3`), commit_id (`cmt_xyz`), or tag (`v1.0`). "
                        "Find via `keel_strategy_log`."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Commit message for the new HEAD. Defaults to 'Restore version <ref>'."
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
