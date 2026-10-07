"""Output formatters for CLI results — json, table, tsv, human.

Human mode has TWO callers and one renderer owner. `format_human` serves
the CLI-only verbs (`keel auth`, `keel context`, `keel skills`,
`keel universe`); `keel.tools.outcomes._cli_adapter._render` serves every
outcome tool. Both route a field through `format_field_line` below, so a
`session_outcomes` map reads the same sentence whichever door it came
through — one computation owner per rendered field (2026-08-06 lesson).

They differ in exactly one respect, preserved deliberately: an
UNRECOGNISED nested value renders inline for `format_human` and indented
for the outcome adapter, which is what each surface already did.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable


def emit(data: Any, fmt: str = "json", columns: list[str] | None = None) -> None:
    """Write formatted data to stdout."""
    text = _format(data, fmt, columns)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def emit_error(error: Any, fmt: str = "json") -> None:
    """Write error to stderr.

    Structured modes (json / table / tsv) emit the spec §13.5 5-field
    envelope when the error is a `KeelError` (or any object exposing
    `to_envelope()`). Human mode prints the message + the next-action
    reason on two lines.
    """
    if hasattr(error, "to_envelope"):
        envelope = error.to_envelope()
    elif hasattr(error, "to_dict"):
        envelope = error.to_dict()
    else:
        envelope = {"code": "error", "message": str(error)}

    if fmt in {"json", "table", "tsv"}:
        text = json.dumps(envelope, indent=2)
    else:
        # human mode: one-line message + next-action reason if present
        lines = [f"Error: {envelope.get('message') or envelope.get('code') or error}"]
        sna = envelope.get("suggested_next_action")
        if isinstance(sna, dict) and sna.get("reason"):
            lines.append(f"  → {sna['reason']}")
        text = "\n".join(lines)
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _format(data: Any, fmt: str, columns: list[str] | None) -> str:
    if fmt == "json":
        return format_json(data)
    elif fmt == "table":
        return format_table(data, columns)
    elif fmt == "tsv":
        return format_tsv(data, columns)
    elif fmt == "human":
        return format_human(data, columns)
    return format_json(data)


def format_json(data: Any) -> str:
    """Format as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def format_table(data: Any, columns: list[str] | None = None) -> str:
    """Format as aligned text table."""
    rows = _to_rows(data)
    if not rows:
        return "(no results)"
    cols = columns or list(rows[0].keys())
    # Compute column widths
    widths = {c: len(c) for c in cols}
    for row in rows:
        for c in cols:
            val = str(row.get(c, ""))
            widths[c] = max(widths[c], len(val))
    # Header
    header = "  ".join(c.upper().ljust(widths[c]) for c in cols)
    lines = [header]
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols)
        lines.append(line)
    return "\n".join(lines)


def format_tsv(data: Any, columns: list[str] | None = None) -> str:
    """Format as tab-separated values (half the tokens of JSON)."""
    rows = _to_rows(data)
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(str(row.get(c, "")) for c in cols))
    return "\n".join(lines)


def format_human(data: Any, columns: list[str] | None = None) -> str:
    """Format for human reading — table for lists, key-value for dicts."""
    if isinstance(data, list):
        if is_execution_rows(data):
            return format_execution_rows(data)
        return format_table(data, columns)
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            lines.append(format_field_line(k, v, data, nested="inline"))
        return "\n".join(lines)
    return str(data)


# ─────────────────────────────────────────────────────────────────────────
# Per-field renderers for the live-execution surface (Q-0913, A8 F-7)
# ─────────────────────────────────────────────────────────────────────────
#
# Before these existed, every structured field on an execution row —
# `session_outcomes`, `account_warnings`, `quality_summary` — reached the
# user as a one-line JSON blob, and `avg_slippage_bps` reached them as a
# bare float divorced from the two fields that say what it MEASURES
# (`avg_slippage_lane`) and over HOW MANY sessions (`slippage_measured_
# sessions`). A bps number with neither is the "near-empty column reads as
# a number" class: -0.64 over 5 of 11 sessions renders identically to
# -0.64 over all of them.
#
# Two rules these renderers obey, and must keep obeying:
#
#   1. SERVER-RENDERED PROSE IS RENDERED, NEVER RE-DERIVED. `AccountWarning
#      .detail` (keel-api `schemas/live.py`: "rendered server-side so every
#      reader — the app banner, the `keel` CLI, an agent over MCP — says
#      the same thing about the same account") is printed verbatim. We
#      never recompute a sentence from `account_value` / `min_notional`.
#      Same for a halt's `detail` when the server supplies one.
#   2. NOTHING IS INVENTED. An absent field renders as absence (EM DASH or
#      an explicit "not reported"), never as 0 and never as a guessed
#      phrase. A vocabulary token (`SKIPPED/BELOW_MIN_TRADE`) is printed as
#      the server wrote it — spec 06a defines that vocabulary as
#      user-meaningful, so glossing it here would be a second, drifting
#      owner of the same words.

EM_DASH = "—"

#: Fields the server does not serve YET — the hook for A8 F-1/F-2, whose
#: server halves belong to the `surfaces` lane on keel-api:
#:
#:   * `session_outcomes: dict[str, int] | None` on
#:     `EnhancedExecutionResponse` — `count(*) GROUP BY status||'/'||
#:     terminal_reason` over `strategy.execution_sessions` for the run.
#:   * `halt: {event_id, reason, detail?} | None` on the same row, for a
#:     run any of whose sessions carries `account_halt_event_id`.
#:
#: The renderers below are live the moment those keys appear in a payload;
#: until then `render_field` returns None for them and nothing is printed
#: (a row that lacks them must not read as "no sessions" — see
#: `tests/test_outcomes_execution_surface.py`).
PENDING_SERVER_FIELDS = ("session_outcomes", "halt")


def _fmt_bps(value: Any) -> str:
    try:
        return f"{float(value):.3f} bps"
    except (TypeError, ValueError):
        return str(value)


def _render_avg_slippage(value: Any, row: dict) -> str:
    """`avg_slippage_bps` never travels without its lane and population.

    The lane (`avg_slippage_lane`) is a contractual sibling on the wire:
    keel-api ships it precisely because renaming the field would break
    older clients, so "which slippage is this" rides beside the number
    (Q-0342). The population (`slippage_measured_sessions`, Q-0111) is
    served with the SAME WHERE clause as the aggregate, so it can never
    disagree with it.
    """
    lane = row.get("avg_slippage_lane")
    measured = row.get("slippage_measured_sessions")

    head = EM_DASH if value is None else _fmt_bps(value)
    parts = [head]
    if lane:
        parts.append(f"lane {lane}")
    if measured is None:
        # An older keel-api pod that does not ship the count. Saying "0"
        # here would invent a population; say what is true.
        parts.append("population not reported")
    else:
        parts.append(f"{measured} session{'' if measured == 1 else 's'} measured")
    return " · ".join(parts)


def _render_session_outcomes(value: Any, row: dict) -> str | None:
    """Why a bar traded what it traded — A8 F-2's per-bar reason.

    Rendered as the total plus the server's own `status/terminal_reason`
    pairs, highest count first. The tokens are printed verbatim: the
    terminal-reason vocabulary is the user-facing vocabulary (spec 06a),
    and a local gloss would be a second owner of it.
    """
    if not isinstance(value, dict) or not value:
        return None
    pairs = sorted(value.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(value.values())
    body = ", ".join(f"{key} {count}" for key, count in pairs)
    return f"{total} session{'' if total == 1 else 's'} {EM_DASH} {body}"


def _render_halt(value: Any, row: dict) -> str | None:
    """A halted bar says so in words, not as an internal session id.

    When the server supplies a rendered `detail` that is what the user
    reads (same contract as `AccountWarning.detail`). Absent one, the
    durable identifiers are named plainly rather than reformatted into a
    sentence this surface would then own alone.
    """
    if not isinstance(value, dict) or not value:
        return None
    detail = value.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail
    event_id = value.get("event_id", EM_DASH)
    reason = value.get("reason", EM_DASH)
    return f"account HALTED {EM_DASH} halt event {event_id}, reason {reason}"


def _render_quality_counts(value: Any, row: dict) -> str | None:
    """Say why this run does or does not carry a single receipt (F-1).

    keel-api serves a `quality_summary` iff the run has exactly ONE
    episode; every post-cutover production run has 11, 15 or 169, so the
    honest CLI answer for a multi-episode run is "receipts are per
    episode, here is how many there are" — not silence, and not one
    episode's receipt passed off as the run's.
    """
    if value is None:
        return None
    sealed = row.get("quality_sealed_receipt_count")
    provisional = row.get("quality_provisional_count")
    parts = [f"{value} episode{'' if value == 1 else 's'}"]
    parts.append(
        f"{sealed} sealed receipt{'' if sealed == 1 else 's'}"
        if sealed is not None
        else "sealed count not reported"
    )
    if provisional is not None:
        parts.append(f"{provisional} provisional")
    line = " · ".join(parts)
    if value == 1:
        return line
    return (
        f"{line} {EM_DASH} receipts are per episode; this run has no single "
        "receipt. Open one with `keel live receipt <deployment_id> "
        "--session-id <session_id>`"
    )


def _render_quality_summary(value: Any, row: dict) -> str | None:
    """The one-episode run's receipt address plus its sealed economics."""
    if not isinstance(value, dict) or not value:
        return None
    parts = []
    state = value.get("state")
    if state:
        parts.append(str(state))
    outcome = value.get("outcome")
    terminal_reason = value.get("terminal_reason")
    if outcome or terminal_reason:
        parts.append(f"{outcome or EM_DASH}/{terminal_reason or EM_DASH}")
    for label, key in (("decision", "decision_bps"), ("arrival", "arrival_bps")):
        raw = value.get(key)
        parts.append(f"{label} {EM_DASH if raw is None else _fmt_bps(raw)}")
    completeness = value.get("completeness")
    if completeness:
        parts.append(str(completeness))
    session_id = value.get("session_id")
    intent_rev = value.get("intent_rev")
    if session_id is not None and intent_rev is not None:
        parts.append(f"receipt at session {session_id} rev {intent_rev}")
    return " · ".join(parts)


def _render_account_warnings(value: Any, row: dict) -> str | None:
    """Print the server's `detail` verbatim, one warning per line."""
    if not isinstance(value, list) or not value:
        return None
    lines = []
    for warning in value:
        if not isinstance(warning, dict):
            continue
        detail = warning.get("detail")
        kind = warning.get("kind", "warning")
        lines.append(f"[{kind}] {detail}" if detail else f"[{kind}] (no detail served)")
    return "\n".join(lines) if lines else None


def _render_order_dispatch(value: Any, row: dict) -> str | None:
    """Q-0626's third state: zero orders because nothing was dispatched YET."""
    if value != "pending":
        return None
    return (
        "pending "
        f"{EM_DASH} the engine has not written this run's sessions yet; the zero "
        "order counts mean 'not dispatched', never 'traded nothing'"
    )


#: key -> renderer. `format_field_line` (and therefore BOTH human
#: surfaces) consults this before any generic fallback.
FIELD_RENDERERS: dict[str, Callable[[Any, dict], str | None]] = {
    "avg_slippage_bps": _render_avg_slippage,
    "session_outcomes": _render_session_outcomes,
    "halt": _render_halt,
    "quality_episode_count": _render_quality_counts,
    "quality_summary": _render_quality_summary,
    "account_warnings": _render_account_warnings,
    "order_dispatch": _render_order_dispatch,
}


def render_field(key: str, value: Any, row: dict | None = None) -> str | None:
    """Render one known field, or return None when no renderer owns it.

    `row` is the sibling context — several renderers are only correct WITH
    it (`avg_slippage_bps` is meaningless without its lane and population).
    """
    renderer = FIELD_RENDERERS.get(key)
    if renderer is None:
        return None
    return renderer(value, row or {})


def format_field_line(
    key: str,
    value: Any,
    context: dict | None = None,
    *,
    nested: str = "inline",
) -> str:
    """One `key: value` line for human output, renderer-first.

    `nested` selects the fallback for a structured value no renderer owns:
    ``"inline"`` (one-line JSON, what `format_human` has always done) or
    ``"indent"`` (2-space JSON, what the outcome CLI adapter has always
    done). Recognised fields ignore it entirely.
    """
    rendered = render_field(key, value, context)
    if rendered is not None:
        if "\n" in rendered:
            body = "\n".join(f"  {line}" for line in rendered.splitlines())
            return f"{key}:\n{body}"
        return f"{key}: {rendered}"
    if key == "data" and is_execution_rows(value):
        return f"{key}:\n{format_execution_rows(value)}"
    if isinstance(value, (list, dict)):
        dumped = (
            json.dumps(value, default=str)
            if nested == "inline"
            else json.dumps(value, indent=2, default=str)
        )
        return f"{key}: {dumped}"
    return f"{key}: {value}"


# ── Execution timeline rows ──────────────────────────────────────────────

#: The two fields every `EnhancedExecutionResponse` row carries and no
#: other list payload in the SDK does. Both are non-Optional on the wire
#: model, so this is a structural test, not a guess.
_EXECUTION_ROW_KEYS = ("attempt_id", "execution_status")

#: Ordered fields rendered under an execution row's header, when present.
_EXECUTION_ROW_FIELDS = (
    "order_dispatch",
    "session_outcomes",
    "halt",
    "error_summary",
    "signal_error_summary",
    "avg_slippage_bps",
    "quality_episode_count",
    "quality_summary",
    "realized_pnl",
)


def is_execution_rows(data: Any) -> bool:
    """True for a non-empty list of live-execution timeline rows."""
    if not isinstance(data, list) or not data:
        return False
    return all(
        isinstance(row, dict) and all(key in row for key in _EXECUTION_ROW_KEYS) for row in data
    )


def format_execution_row(row: dict) -> str:
    """One execution attempt, rendered field-by-field.

    The counts line prints what the server reported and adds no
    interpretation. In particular it never turns `order_count == 0` into
    "no trades needed": a bar whose every leg was refused under the venue
    minimum has exactly those zeros (A8 F-2), and the phrase that tells
    the two apart is `session_outcomes`, one line above.
    """
    when = row.get("activity_at") or row.get("started_at") or EM_DASH
    identity = row.get("execution_run_id") or row.get("attempt_id")
    header = (
        f"{when}  {identity}  {row.get('attempt_kind', EM_DASH)}/"
        f"{row.get('execution_status', EM_DASH)}"
    )
    lines = [header]

    counts = " · ".join(
        f"{label} {row.get(key, EM_DASH)}"
        for label, key in (
            ("orders", "order_count"),
            ("filled", "filled_count"),
            ("rejected", "rejected_count"),
            ("skipped", "skipped_count"),
            ("weights", "weight_count"),
        )
    )
    lines.append(f"  counts: {counts}")

    for key in _EXECUTION_ROW_FIELDS:
        if key not in row:
            continue
        value = row[key]
        if key in FIELD_RENDERERS:
            # A renderer that returns None means "this field says nothing
            # here" (an empty map, a run that is not halted). Print no line
            # rather than a `null` — absence is not a value.
            if render_field(key, value, row) is None:
                continue
        elif value is None:
            continue
        line = format_field_line(key, value, row, nested="inline")
        lines.extend(f"  {part}" for part in line.splitlines())
    return "\n".join(lines)


def format_execution_rows(rows: list) -> str:
    """The execution timeline, newest first as the server ordered it."""
    return "\n".join(format_execution_row(row) for row in rows)


def _to_rows(data: Any) -> list[dict]:
    """Normalize data to list of dicts for tabular formatting."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return [{"value": item} for item in data]
    if isinstance(data, dict):
        return [data]
    return []
