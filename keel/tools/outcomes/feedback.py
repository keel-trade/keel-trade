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
        # An empty note stored an empty record no one can act on (Q-2273).
        # Never-fails (spec 02 R4) covers DELIVERY; a note with nothing in it
        # is a usage error, refused before anything is sent.
        from keel.errors import UsageError

        raise UsageError(
            "Missing `text` — the feedback itself; nothing was sent.",
            suggestion="Re-call with `text` (plus `goal` and `kind` when known).",
        )

    delivered = False
    stored = False
    feedback_id: str | None = None
    try:
        client = ctx.get_client()
        response = client.post("/v1/feedback", json=payload)
        # keel-api answers 200 even when it could not store the row (its
        # never-fails contract): the body then carries `note: "feedback could
        # not be persisted"` and no team member will ever read it. Transport
        # success is not delivery (Q-2268), so `delivered` is the stored fact
        # and `stored` names it.
        if isinstance(response, dict):
            feedback_id = response.get("feedback_id")
            server_note = response.get("note")
            if server_note:
                notes.append(str(server_note))
            stored = not _persist_failed(server_note)
        else:
            stored = True
        delivered = stored
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
        "stored": stored,
        "feedback_id": feedback_id,
    }
    if notes:
        extra["note"] = "; ".join(notes)
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)


#: keel-api's success-with-note for a row it could not write
#: (services/keel-api/src/routers/feedback.py: "feedback could not be
#: persisted"). Matched as a phrase so a longer note still counts.
_PERSIST_FAILED_PHRASE = "could not be persisted"


def _persist_failed(note: Any) -> bool:
    return isinstance(note, str) and _PERSIST_FAILED_PHRASE in note.lower()


FEEDBACK = register(
    OutcomeTool(
        name="keel_feedback",
        # Deliberately the lowest consent bucket (read — same as
        # keel_account_status/keel_connection_check/keel_help): feedback must be fileable
        # by every authenticated caller, so it never sits behind a
        # write-scope grant (spec 02 R4 "no flow may gate on it").
        required_action="audit.read",
        cli_path=("feedback",),
        toolset="always",
        # grounded-in: feedback.py docstring (spec 02 R4 — never-fails
        # contract, no flow gates on it) + system/chat/tool_usage.md:27-29 (a tool
        # erroring twice on the same root cause is exactly the friction to
        # capture here rather than silently working around).
        # No trigger beyond the user's say-so (Q-2080, 2026-10-01): the
        # earlier "can be filed at any point, including after a tool keeps
        # erroring twice" read to OpenAI's scan as autonomous transmission of
        # session details. The description now says when it is for: the user
        # asked, or agreed.
        description=(
            "Send feedback about Keel to the Keel team — friction, praise or a bug report "
            "in `goal`, `kind` and `text` — when the user asks or agrees; a failing "
            "connection is `keel_connection_check`. It sends only these fields (plus "
            "optional `severity` and `context_ref`), nothing else from the conversation; "
            "`text` is required. Returns `delivered` (true only "
            "when Keel stored the note) and, on a problem, a "
            "`note`; no one replies within the session."
        ),
        input_schema={
            "type": "object",
            # `text` is required (Q-2273): a call without it stored an empty
            # record. The never-fails contract (spec 02 R4) still covers
            # DELIVERY — a well-formed note keel-api cannot take is a success
            # with a note — and every other field stays optional.
            "required": ["text"],
            "properties": {
                "text": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "The feedback itself, in the caller's own words. Markdown allowed. "
                        "Up to 5,000 characters are stored; longer text is cut and the "
                        "result's `note` says so."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": ("What was being attempted when the feedback arose."),
                },
                "kind": {
                    "type": "string",
                    "enum": ["friction", "praise", "bug"],
                    "description": "Feedback category: friction | praise | bug.",
                },
                "severity": {
                    "type": "string",
                    # Free text, not an enum (Q-2266): keel-api stores any
                    # string up to 500 characters as written (routers/
                    # feedback.py `_clean_str`), and the never-fails contract
                    # rules out a client-side refusal.
                    "description": (
                        "Optional severity in a word or two (for example low, medium or "
                        "high); stored as written."
                    ),
                },
                "context_ref": {
                    "type": "string",
                    "description": (
                        "Optional reference this feedback concerns — a strategy id "
                        "(`str_...`), a backtest run id (`btr_...`), or a tool name "
                        "(`keel_backtest_run`)."
                    ),
                },
            },
        },
        annotations={
            "title": "Send Feedback",
            "readOnlyHint": False,
            # Irreversible (2026-10-01, Q-2080): a note delivered to the Keel
            # team cannot be recalled, so it is not an append-only write the
            # caller can read back and move on from.
            "destructiveHint": True,
            "idempotentHint": False,
            # The note leaves the caller's workspace and is delivered to the
            # Keel team — an external party from the caller's side. OpenAI's
            # scanner read it as an external system (2026-09-30), correctly;
            # the ground is the delivery, never Keel's own telemetry.
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
