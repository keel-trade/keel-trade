"""`keel_strategy_notes_read` / `keel_strategy_notes_add` — notes on a strategy.

Renamed from `keel_strategy_memory_read` / `keel_strategy_memory_write` on
2026-10-01 (Q-2080): "memory" named the table, not what a user keeps; the
old names are callable aliases (`_toolsets.TOOL_ALIASES`).

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
from ._base import OutcomeResult, OutcomeTool, ToolContext, listed_schema
from ._surface_hints import strategy_ids_hint
from .open_in_app import app_url_for


#: Fields a served note NEVER carries (Q-2080 / FINDINGS F5): the id of the
#: conversation another surface wrote the note from identifies that
#: conversation, not the note, and recalling the note needs none of it.
_NOTE_PRIVATE_FIELDS = ("source_conversation_id",)


def served_note(note: Any) -> Any:
    """One note as this surface returns it — the row minus its private fields.

    On the LISTED profile the row is the allow-list
    (`_listed_projection.LISTED_NOTE_FIELDS`, Q-2268): a field keel-api adds
    later stays off the directory connector until it is named there.
    """
    if not isinstance(note, dict):
        return note
    from ._toolsets import is_listed_profile

    if is_listed_profile():
        from ._listed_projection import LISTED_NOTE_FIELDS, pick

        return pick(note, LISTED_NOTE_FIELDS)
    return {k: v for k, v in note.items() if k not in _NOTE_PRIVATE_FIELDS}


# ─── READ ────────────────────────────────────────────────────────────────


def _read_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id: str = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass `strategy_id=str_abc`. " + strategy_ids_hint(),
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
        notes = [served_note(n) for n in notes]
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
            f"Failed to read the notes for {strategy_id}: {e}",
            suggestion=(
                "`keel_connection_check` reports API health. A strategy with no notes "
                "yet answers with an empty list rather than an error — verify the id."
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
        hero_url=app_url_for("strategy", strategy_id, ctx),
        share_url=None,
        extra=extra,
    )


STRATEGY_MEMORY_READ = register(
    OutcomeTool(
        name="keel_strategy_notes_read",
        required_action="strategy.read",
        cli_path=("strategy", "memory-read"),
        toolset="read-only",
        # grounded-in: system/chat/collaboration.md §5 (Explain the Why — the
        # reasoning behind choices) + §4 (iterate on context you already
        # established); system/chat/tool_usage.md:17-18 (the principled
        # reasoning worth persisting).
        description=(
            "Read the notes saved on one of the caller's strategies — the durable context "
            "that outlives one session; adding one is `keel_strategy_notes_add`. The "
            "result carries `notes`: the most recent `limit` "
            "notes (default 10), newest first, each with its text, author and time — why "
            "prior changes were made, baseline metrics, known risks."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id whose notes to read.",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum notes to return, newest first (1-100).",
                },
            },
        },
        annotations={
            "title": "Read Strategy Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
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
            suggestion="Pass `strategy_id=str_abc`. " + strategy_ids_hint(),
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
        hero_url=app_url_for("strategy", strategy_id, ctx),
        share_url=None,
        extra=extra,
    )


#: The shared schema; the listed twin omits `role` (`_base.LISTED_SCHEMA_OMISSIONS`):
#: a connector's note is the agent's, and a dropped `role` lands as the default.
NOTES_ADD_INPUT_SCHEMA: dict[str, Any] = {
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
            "minLength": 1,
            "maxLength": 8192,
            "description": (
                "The note body (markdown allowed): one short paragraph about this strategy."
            ),
        },
        "role": {
            "type": "string",
            "enum": ["agent", "user"],
            "default": "agent",
            "description": "Who authored the note.",
        },
    },
}


STRATEGY_MEMORY_WRITE = register(
    OutcomeTool(
        name="keel_strategy_notes_add",
        required_action="strategy.update",
        cli_path=("strategy", "memory-write"),
        toolset="backtest",
        # grounded-in: system/chat/collaboration.md §5 (Explain the Why — record the
        # reasoning behind a choice) + §4 (the reasoning behind each
        # iteration is the durable artifact).
        description=(
            "Add a note to a strategy's saved notes, kept with the strategy in the user's "
            "Keel account and returned by `keel_strategy_notes_read` in later sessions. "
            "Each call appends one note; none is edited or removed. A note is a brief, "
            "strategy-specific record (a rationale, a baseline, a risk), not a transcript "
            "of the conversation. "
            "`role` defaults to `agent`; `user` marks a human-authored note. Changing the "
            "source is `keel_strategy_compose`."
        ),
        # Listed copy (R-4): the same text minus the `role` sentence — the listed
        # schema omits `role` (every note from a connector is the agent's).
        listed_description=(
            "Add a note to a strategy's saved notes, kept with the strategy in the user's "
            "Keel account and returned by `keel_strategy_notes_read` in later sessions. "
            "Each call appends one note; none is edited or removed. A note is a brief, "
            "strategy-specific record (a rationale, a baseline, a risk), not a transcript "
            "of the conversation. Changing the source is "
            "`keel_strategy_compose`."
        ),
        input_schema=NOTES_ADD_INPUT_SCHEMA,
        listed_input_schema=listed_schema("keel_strategy_notes_add", NOTES_ADD_INPUT_SCHEMA),
        annotations={
            "title": "Add Strategy Note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_write_handler,
    )
)
