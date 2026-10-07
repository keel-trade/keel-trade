"""`keel_strategy_history` — show commit history for a strategy.

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
from ._surface_hints import strategy_ids_hint, usage_hint
from ._toolsets import local_tools_registered
from .open_in_app import app_url_for


def _next_lines(has_commits: bool) -> list[str]:
    """The `next` facts — naming only tools this server registers.

    Spec 05 §4 item 7: `keel_strategy_push` / `keel_strategy_checkout` are
    local-only, so a hosted or listed result does not name them (the old
    lines also advertised a checkout form marked "NOT YET IMPLEMENTED"). The
    facts are indicative: what each tool does, not an instruction to call it.
    """
    local = local_tools_registered()
    if not has_commits:
        lines = ["No commits yet — the strategy may have just been created."]
        if local:
            lines.append("`keel_strategy_push -m 'msg'` pushes a first version from a checkout.")
        return lines
    lines = [
        "`keel://strategy/{id}/source` and `keel_strategy_diff` show the source at a commit.",
        "`keel_strategy_restore strategy_id=<id> ref=<sequence_or_commit_id>` makes a "
        "historical commit the new HEAD.",
    ]
    if local:
        lines.append(
            "`keel_strategy_checkout <id>` then brings the restored HEAD into a local workspace."
        )
    return lines


def version_entry(v: dict[str, Any], *, listed: bool) -> dict[str, Any]:
    """One version row as this surface returns it.

    Surface attribution (spec 08 R5): which surface/client made the commit.
    The LISTED row is :data:`_listed_projection.LISTED_VERSION_FIELDS` — the
    rendered `modified_via` sentence without the raw client / auth-surface
    provenance or the source hash (Q-2268: internal plumbing on a research
    connector); the CLI and local server keep all three, which the sync
    contract reads. `keel_strategy_get include_versions=true` returns the
    same row on listed.
    """
    from keel.workspace import format_modified_via

    entry: dict[str, Any] = {
        "sequence_number": v.get("sequence_number"),
        "commit_id": v.get("commit_id"),
        "parent_id": v.get("parent_id"),
        "source_hash": (v.get("source_hash") or "")[:12],
        "message": v.get("message"),
        "created_at": v.get("created_at"),
        "tags": v.get("tags") or [],
        "client_name": v.get("client_name"),
        "auth_surface": v.get("auth_surface"),
        # None for commits predating the attribution migration.
        "modified_via": format_modified_via(
            v.get("client_name"), v.get("auth_surface"), v.get("created_at")
        ),
    }
    if listed:
        from ._listed_projection import LISTED_VERSION_FIELDS, pick

        return pick(entry, LISTED_VERSION_FIELDS)
    return entry


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=usage_hint(
                "Pass a strategy id (e.g. `keel strategy log str_abc123`). ",
                "Pass `strategy_id` (`str_...`). ",
            )
            + strategy_ids_hint(),
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

    client = ctx.get_client()
    try:
        result = client.get(f"/v1/strategies/{strategy_id}/versions", limit=limit)
    except KeelError:
        raise
    except Exception as e:
        raise KeelError(
            f"Failed to fetch version history for {strategy_id}: {e}",
            suggestion="Run `keel_connection_check` to diagnose auth / API.",
        ) from e

    # Endpoint returns a bare list[VersionResponse] today but the
    # canonical shape is {data: [...], pagination: ...}; shared helper
    # handles both transparently.
    from keel.workspace import _normalize_paginated_versions

    from ._toolsets import is_listed_profile

    listed = is_listed_profile()
    entries: list[dict[str, Any]] = [
        version_entry(v, listed=listed) for v in _normalize_paginated_versions(result)
    ]

    return OutcomeResult(
        run_id=strategy_id,
        # `?tab=history` was never read by any page (see open_in_app._with_query).
        hero_url=app_url_for("strategy", strategy_id, ctx),
        share_url=None,
        extra={
            "strategy_id": strategy_id,
            "commits": entries,
            "count": len(entries),
            "head_sequence": entries[0]["sequence_number"] if entries else None,
            "next": _next_lines(bool(entries)),
        },
    )


STRATEGY_LOG = register(
    OutcomeTool(
        name="keel_strategy_history",
        required_action="strategy.read",
        cli_path=("strategy", "log"),
        toolset="read-only",
        # grounded-in: sync-contract (spec 08) — server HEAD is the canonical
        # source of truth; the log is the 'git log' over that timeline;
        # system/chat/collaboration.md §4 (find the ref to diff/restore against when
        # iterating).
        description=(
            "Show a strategy's commit history — each version's commit id, the ref "
            "`keel_backtest_run` pins and `keel_strategy_diff` compares. Each entry "
            "carries the sequence number, commit id, parent, source hash, message, "
            "timestamp and tags, newest first; `limit` defaults to 50 (server maximum "
            "200). It is the server's canonical timeline, where server HEAD is the source "
            "of truth, and each entry also names the client that made it (`modified_via`). "
            "One commit's source is `keel_strategy_get` with `version` and "
            "`include_source=true`."
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
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum commits to return (1-200).",
                },
            },
        },
        annotations={
            "title": "Get Strategy History",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        # Listed-profile copy (spec 01 R3): policy-clean, routes only to
        # tools on the listed surface. keel_strategy_diff is now listed
        # (D1 2026-07-19), so the structural-compare route points there.
        listed_description=(
            "Show a strategy's commit history — each version's commit id, the ref "
            "`keel_backtest_run` pins and `keel_strategy_diff` compares. Each entry "
            "carries the sequence number, commit id, parent, message, "
            "timestamp and tags, newest first; `limit` defaults to 50 (server maximum "
            "200). One commit's source is `keel_strategy_get` with `version` and "
            "`include_source=true`."
        ),
    )
)
