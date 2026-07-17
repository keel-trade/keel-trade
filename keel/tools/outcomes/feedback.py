"""`keel_feedback` — file product feedback from agent sessions (spec 02 R4).

POSTs `{goal, kind, severity?, context_ref?, text}` to keel-api's
`/v1/feedback`, which stores a row in `platform.feedback` and emits a
`feedback_submitted` telemetry event SERVER-SIDE (the SDK itself
collects nothing — DP2: no client-side analytics in this package,
ever; this tool only forwards what the agent explicitly writes).

Contract (spec 02 R4): this tool NEVER fails the caller and no flow
may gate on it. Both ends enforce that:

* keel-api returns 200 success-with-`note` for ANY input problem
  (malformed kinds, wrong types, oversized text are normalized there —
  the server is the single normalization point, so this handler passes
  values through verbatim and declares nothing `required`);
* this handler converts every DELIVERY failure (not authenticated,
  4xx/5xx, network/timeout, unparseable response) into a
  success-with-note result — `delivered: false` plus a `note`, never
  an error envelope.

Genuine programming errors (TypeError and friends from a bug in our
code) still propagate to the adapter's `internal_error` envelope like
every sibling tool — never-fails means "feedback delivery must not
become friction", not "swallow bugs silently".
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


_FIELDS = ("goal", "kind", "severity", "context_ref", "text")


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    payload = {k: args[k] for k in _FIELDS if args.get(k) is not None}

    notes: list[str] = []
    if not str(payload.get("text") or "").strip():
        notes.append(
            "no `text` was provided, so the stored record carries no feedback "
            "content — include `text` (plus `goal` and `kind`) next time"
        )

    delivered = False
    feedback_id: str | None = None
    try:
        client = ctx.get_client()
        response = client.post("/v1/feedback", json=payload)
        delivered = True
        if isinstance(response, dict):
            feedback_id = response.get("feedback_id")
            server_note = response.get("note")
            if server_note:
                notes.append(str(server_note))
    except Exception as e:  # noqa: BLE001 — delivery-class failures only; re-raised below if not
        # Never-fails boundary: auth (KeelError/AuthError), HTTP 4xx/5xx
        # and network/timeouts (KeelError via the client's translation +
        # retry layer), unparseable responses (ValueError/JSONDecodeError)
        # and environment problems (OSError) all become success-with-note.
        # Anything else is a programming error — re-raise it so the
        # adapter's internal_error envelope surfaces the bug.
        import httpx

        from keel.errors import KeelError

        if not isinstance(e, (KeelError, httpx.HTTPError, ValueError, OSError)):
            raise
        reason = str(e).strip() or type(e).__name__
        notes.append(
            f"feedback could not be delivered ({reason}); do not retry and do "
            "not block on this — continue with the user's task"
        )

    extra: dict[str, Any] = {
        "status": "ok",
        "delivered": delivered,
        "feedback_id": feedback_id,
    }
    if notes:
        extra["note"] = "; ".join(notes)
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)


FEEDBACK = register(
    OutcomeTool(
        name="keel_feedback",
        # Deliberately the lowest consent bucket (read — same as
        # keel_status/keel_doctor/keel_help): feedback must be fileable
        # by every authenticated caller, so it never sits behind a
        # write-scope grant (spec 02 R4 "no flow may gate on it").
        required_action="audit.read",
        cli_path=("feedback",),
        toolset="always",
        description=(
            "Send product feedback about Keel to the team: friction, praise, "
            "or bug reports from this session. File it at the END of a "
            "session, and any time the same friction repeats — a tool "
            "erroring twice, a confusing result, a missing capability. "
            "Provide `goal` (what you were trying to accomplish), `kind` "
            "(friction | praise | bug), and `text` (the feedback itself); "
            "optionally `severity` and `context_ref` (the id or tool name it "
            "concerns). This tool NEVER fails: delivery problems return "
            "success with a `note`, so it is always safe to call and no "
            "workflow should wait on or gate on it. "
            "Do NOT use for support questions — nothing is returned and no "
            "human replies in-session; for connectivity problems call "
            "`keel_doctor`."
        ),
        input_schema={
            "type": "object",
            # No `required` fields — the never-fails contract extends to
            # arguments: keel-api normalizes whatever arrives and answers
            # success-with-note, so a sparse call must not be rejected
            # client-side either (spec 02 R4).
            "required": [],
            "properties": {
                "text": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "The feedback itself, in your own words. Markdown allowed.",
                },
                "goal": {
                    "type": "string",
                    "description": ("What you were trying to accomplish when the feedback arose."),
                },
                "kind": {
                    "type": "string",
                    "enum": ["friction", "praise", "bug"],
                    "description": "Feedback category: friction | praise | bug.",
                },
                "severity": {
                    "type": "string",
                    "description": "Optional severity: low, medium, or high.",
                },
                "context_ref": {
                    "type": "string",
                    "description": (
                        "Optional reference this feedback concerns — a strategy "
                        "id (str_...), a backtest run id (btr_...), or a tool "
                        "name (keel_backtest_run)."
                    ),
                },
            },
        },
        annotations={
            "title": "Send Feedback",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
