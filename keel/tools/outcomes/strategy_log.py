"""`keel_strategy_log` — show commit history for a strategy.

The "git log" of the sync model. Wraps `GET /v1/strategies/<id>/versions`.
Lists commits in reverse-chronological order (newest first) with
sequence number, parent, source hash, message, timestamp, and any
tags.

Use to:

  * See what's changed since you last checked out
  * Find a specific commit to checkout / restore / diff against
  * Audit who/what produced each version (commit messages)
  * Track agent + user edits over time

Companion verbs: `keel_strategy_restore` (server-side restore of a
historical commit as new HEAD), `keel_strategy_diff` (compare two
commits), `keel_strategy_checkout <id>@<ref>` (pull a specific
version into the local workspace).
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
                "Pass a strategy id (e.g. `keel strategy log str_abc123`). "
                "Find ids via `keel_strategy_search` or "
                "`keel_strategy_workspaces` for locally-checked-out ones."
            ),
        )

    limit = args.get("limit") or 50
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise KeelError(
            f"Invalid `limit`: {args.get('limit')!r} — expected an integer.",
            error_code="invalid_argument",
            exit_code=2,
            suggestion="Pass `limit` as an integer between 1 and 200 (default: 50).",
        )
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    client = ctx.get_client()
    try:
        result = client.get(f"/v1/strategies/{strategy_id}/versions", limit=limit)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fetch version history for {strategy_id}: {e}",
            suggestion="Run `keel_doctor` to diagnose auth / API.",
        ) from e

    # Endpoint returns a bare list[VersionResponse] today but the
    # canonical shape is {data: [...], pagination: ...}; shared helper
    # handles both transparently.
    from keel.workspace import _normalize_paginated_versions

    entries: list[dict[str, Any]] = [
        {
            "sequence_number": v.get("sequence_number"),
            "commit_id": v.get("commit_id"),
            "parent_id": v.get("parent_id"),
            "source_hash": (v.get("source_hash") or "")[:12],
            "message": v.get("message"),
            "created_at": v.get("created_at"),
            "tags": v.get("tags") or [],
        }
        for v in _normalize_paginated_versions(result)
    ]

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}?tab=history",
        share_url=None,
        extra={
            "strategy_id": strategy_id,
            "commits": entries,
            "count": len(entries),
            "head_sequence": entries[0]["sequence_number"] if entries else None,
            "next": (
                [
                    "No commits yet — strategy may have been just created.",
                    "Push your first version via `keel_strategy_push -m 'msg'`.",
                ]
                if not entries
                else [
                    "To see the source at a specific commit: read `keel://strategy/{id}/source` or use `keel_strategy_diff`.",
                    "To restore a historical commit as new HEAD: `keel_strategy_restore strategy_id=<id> ref=<sequence_or_commit_id>`.",
                    "To checkout a historical version locally: `keel_strategy_checkout <id>@<sequence_or_commit_id>` (NOT YET IMPLEMENTED — use restore + checkout for now).",
                ]
            ),
        },
    )


STRATEGY_LOG = register(
    OutcomeTool(
        name="keel_strategy_log",
        required_action="strategy.read",
        cli_path=("strategy", "log"),
        toolset="read-only",
        description=(
            "Show commit history for a strategy — sequence number, commit "
            "id, parent, source hash, message, timestamp, and tags. The "
            "'git log' of the sync model. Reverse-chronological (newest "
            "first). Use to audit changes, find commits to restore/diff, "
            "or see what's moved since you last checked out. "
            "Do NOT use to fetch source for one commit — that's a future "
            "`keel://strategy/{id}/versions/{ref}/source` resource. Do NOT "
            "use to see what changed STRUCTURALLY between two versions — "
            "call `keel_strategy_diff` for that. "
            "Default `limit=50`; max 200 (server-enforced)."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id whose history to show.",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max commits to return. Clamped to 1..200.",
                },
            },
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
