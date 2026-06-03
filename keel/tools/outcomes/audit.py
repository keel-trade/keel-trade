"""Audit tools — `keel_audit_list_last`.

Per spec §4 auxiliary table + §13.7:

- `keel_audit_list_last` (read-only): the agent reads its own recent
  tool calls (the N most recent), useful for self-debugging and for the
  `recover-from-error` skill.

Backing endpoint: `GET /v1/audit?limit={n}` returns a paginated list of
audit events (see the API audit router). Items are
shaped:

    {
      "id": "<event_id>",           # serialization_alias on event_id
      "org_id": "...",
      "actor_principal_id": "...",
      "action": "...",              # e.g. "backtest.run", "strategy.update"
      "decision": "ALLOW" | "DENY" or "permit" | "deny",
      "metadata": {...} | None,
      "created_at": "...iso..."
    }

We normalize to the spec §13.7 envelope:
    events: [{ts, tool, decision, args, result_ref, metadata_complete}]
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# ─── keel_audit_list_last ────────────────────────────────────────────────


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw audit row from /v1/audit into the spec envelope shape.

    The API uses `id` (serialization_alias for event_id), `action` for
    the tool/action name, `metadata` for the call args / result pointer,
    and `created_at` for timestamp. Spec §13.7 expects a compact
    `{ts, tool, args, result_ref}` shape; Keel also adds normalized
    `decision` and `metadata_complete` so agents can tell when sparse
    audit metadata was returned.
    """
    metadata = raw.get("metadata") or {}
    metadata_complete = bool(metadata)
    # Best-effort split: metadata may carry call args under "args" /
    # "input" and a result pointer under "result_ref" / "result_id".
    args = metadata.get("args") or metadata.get("input") or {}
    result_ref = (
        metadata.get("result_ref")
        or metadata.get("result_id")
        or metadata.get("backtest_run_id")
        or metadata.get("strategy_id")
        or None
    )
    return {
        "event_id": raw.get("id") or raw.get("event_id"),
        "ts": raw.get("created_at"),
        "tool": raw.get("action"),
        "decision": _normalize_decision(raw.get("decision")),
        "args": args,
        "result_ref": result_ref,
        "metadata_complete": metadata_complete,
    }


def _normalize_decision(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized == "allow":
        return "permit"
    if normalized == "deny":
        return "deny"
    return normalized or None


def _list_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    n = args.get("n") or 20
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise KeelError(
            f"Invalid `n` value: {args.get('n')!r}; expected an integer.",
            error_code="invalid_argument",
            exit_code=2,
            suggestion="Pass `n` as an integer between 1 and 100 (default: 20).",
        )
    if n < 1:
        n = 1
    if n > 100:
        # /v1/audit clamps at 100 server-side; mirror that here.
        n = 100

    client = ctx.get_client()
    try:
        result = client.get("/v1/audit", limit=n)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fetch audit events: {e}",
            suggestion="Run `keel_doctor` to diagnose auth / API.",
        )

    # API returns the canonical {data: [...], pagination: {cursor, has_more}}
    # — see the API canonical PaginatedResponse.
    from ._pagination import extract_paginated

    raw_items, next_cursor = extract_paginated(result)
    events = [_normalize_event(item) for item in raw_items if isinstance(item, dict)]

    extra: dict[str, Any] = {"events": events}
    if next_cursor:
        extra["next_cursor"] = next_cursor
    extra["metadata_note"] = (
        "`args` and `result_ref` are best-effort fields. They may be empty "
        "until the API audit metadata includes sanitized call inputs and "
        "result pointers."
    )

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/audit",
        share_url=None,
        extra=extra,
    )


AUDIT_LIST_LAST = register(
    OutcomeTool(
        name="keel_audit_list_last",
        required_action="audit.read",
        cli_path=("audit", "list-last"),
        toolset="read-only",
        description=(
            "Read the most recent N audit events for the current org. "
            "Use this to inspect what the agent (or user) just did — "
            "tool/action name, decision (permit/deny), and best-effort "
            "args/result_ref metadata when the API recorded it. "
            "Do NOT assume `args` or `result_ref` is complete; empty values "
            "mean the audit event did not include replay-safe metadata. "
            "Do NOT use to mutate state — call the relevant outcome "
            "tool directly. "
            "Do NOT use to fetch full backtest results — read "
            "`keel://backtest/<id>/results` after locating the run id "
            "in the audit event's `result_ref`."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "n": {
                    "type": "integer",
                    "default": 20,
                    "description": ("Max events to return. Server clamps to 1..100."),
                },
            },
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_list_handler,
    )
)
