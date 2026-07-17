"""`keel_strategy_memory_read` / `keel_strategy_memory_write` — notes on a strategy.

Both tools live in one module because they're tightly coupled — read and
write of the same underlying `platform.strategy_memory` table.

The API endpoints `GET/POST /v1/strategies/{id}/memory` are shipped as
part of Phase 2E. 404s now mean the strategy itself isn't visible to the
caller (cross-org or missing), not "endpoint pending" — surface them
as `NotFoundError` so the agent gets a precise error.

Do NOT use these to mutate strategy source — call `keel_strategy_compose`.
"""

from __future__ import annotations

import time
from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# ─── READ ────────────────────────────────────────────────────────────────


def _read_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id: str = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass `strategy_id=str_abc`. Find ids via "
                "`keel_strategy_search` or `keel_strategy_workspaces` for "
                "locally-checked-out ones."
            ),
        )
    limit: int = int(args.get("limit", 10) or 10)

    client = ctx.get_client()
    notes: list[Any] = []
    last_updated: str | None = None
    summary: str | None = None
    try:
        payload = client.get(f"/v1/strategies/{strategy_id}/memory", limit=limit)
        # The /memory endpoint isn't strictly PaginatedResponse-shaped
        # (it carries last_updated + summary alongside the list) but the
        # list itself follows the same `data` convention against the
        # live API. Use the shared helper for the list and pluck the
        # metadata fields manually.
        from ._pagination import extract_paginated

        notes, _ = extract_paginated(payload)
        if isinstance(payload, dict):
            last_updated = payload.get("last_updated")
            summary = payload.get("summary")
    except NotFoundError:
        # Strategy not visible to caller (missing or cross-org). Re-raise
        # so the agent sees a precise error rather than an empty list.
        raise
    except KeelError:
        # Surface auth/entitlement errors
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to read memory for {strategy_id}: {e}",
            suggestion=(
                "Run `keel_doctor` to check API health. If the strategy "
                "exists but has no memory yet, the response will be empty "
                "rather than an error — verify the id is correct."
            ),
        )

    extra: dict[str, Any] = {
        "strategy_id": strategy_id,
        "notes": notes,
    }
    if last_updated is not None:
        extra["last_updated"] = last_updated
    if summary is not None:
        extra["summary"] = summary

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
        share_url=None,
        extra=extra,
    )


STRATEGY_MEMORY_READ = register(
    OutcomeTool(
        name="keel_strategy_memory_read",
        required_action="strategy.read",
        cli_path=("strategy", "memory-read"),
        toolset="read-only",
        description=(
            "Read agent/user notes attached to a strategy. Returns the most "
            "recent `limit` notes (default 10), newest first. "
            "Do NOT use to fetch strategy source or metadata — call "
            "`keel_strategy_get`. "
            "Do NOT use to write notes — call `keel_strategy_memory_write`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id whose memory to fetch.",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max notes to return (newest first).",
                },
            },
        },
        annotations={
            "title": "Read Strategy Memory",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_read_handler,
    )
)


# ─── WRITE ───────────────────────────────────────────────────────────────


def _write_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id: str = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=(
                "Pass `strategy_id=str_abc`. Find ids via "
                "`keel_strategy_search` or `keel_strategy_workspaces`."
            ),
        )
    note: str = (args.get("note") or "").strip()
    if not note:
        raise KeelError(
            "Missing required `note` (must be non-empty).",
            error_code="missing_note",
            exit_code=2,
            suggestion=(
                "Pass `note='<text>'` — typically a single paragraph of "
                'context (e.g. "baseline Sharpe 3.13 over 2024-08 → '
                '2026-02; main risk is concentration in HYPE"). Markdown is '
                "allowed."
            ),
        )
    role: str = args.get("role") or "agent"
    if role not in ("agent", "user"):
        raise KeelError(
            f"Invalid role {role!r}; must be 'agent' or 'user'.",
            error_code="invalid_role",
            exit_code=2,
            suggestion=(
                "Pass `role='agent'` for AI-authored notes (default) or "
                "`role='user'` for human-authored ones."
            ),
        )

    client = ctx.get_client()
    try:
        result = client.post(
            f"/v1/strategies/{strategy_id}/memory",
            json={"note": note, "role": role},
        )
    except NotFoundError:
        # Strategy not visible (missing or cross-org) — re-raise.
        raise
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to write memory for {strategy_id}: {e}",
            suggestion=(
                "Verify the strategy id exists (`keel_strategy_get "
                f"{strategy_id}`). If the API rejected the payload, the "
                "note may be too long — keep it under a few KB."
            ),
        )

    memory_id = None
    ts: Any = None
    if isinstance(result, dict):
        memory_id = result.get("memory_id") or result.get("note_id") or result.get("id")
        ts = result.get("created_at") or result.get("ts")
    if ts is None:
        ts = int(time.time())

    extra: dict[str, Any] = {
        "strategy_id": strategy_id,
        "memory_id": memory_id,
        "created_at": ts,
    }
    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
        share_url=None,
        extra=extra,
    )


STRATEGY_MEMORY_WRITE = register(
    OutcomeTool(
        name="keel_strategy_memory_write",
        required_action="strategy.update",
        cli_path=("strategy", "memory-write"),
        toolset="backtest",
        description=(
            "Append an agent/user note to a strategy's memory. Defaults to "
            "role='agent'; pass role='user' for human-authored notes. "
            "Do NOT use to mutate strategy source — call `keel_strategy_compose`. "
            "Do NOT use to read existing notes — call `keel_strategy_memory_read`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id", "note"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id to attach the note to.",
                },
                "note": {
                    "type": "string",
                    "description": "The note body (markdown allowed).",
                },
                "role": {
                    "type": "string",
                    "enum": ["agent", "user"],
                    "default": "agent",
                    "description": "Who authored the note.",
                },
            },
        },
        annotations={
            "title": "Write Strategy Memory",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_write_handler,
    )
)
