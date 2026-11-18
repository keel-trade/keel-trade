"""Read and format the API's selected facts; never rank or open preview files."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime

from keel.api_path import seg
from keel.errors import KeelError

from ._base import OutcomeResult
from ._channels import card_meta_move
from .open_in_app import app_url_for


SUBJECT = {
    "strategy_id": {
        "type": "string",
        "pattern": "^str_[0-9a-z]{26}$",
        "x-cli-positional": True,
        "description": "A saved screen.",
    },
    "version": {
        "type": "integer",
        "minimum": 1,
        "description": "A saved version, only with strategy_id.",
    },
    "run_id": {"type": "string", "pattern": "^res_[0-9a-z]{26}$", "description": "One result run."},
    "share_id": {"type": "string", "description": "A shared or followed screen."},
    "ranked_by": {
        "type": "string",
        "description": "Output to rank; defaults to the first line view, then first continuous per-asset output, then any per-asset output.",
    },
    "order": {"type": "string", "enum": ["desc", "asc"], "description": "Highest or lowest first."},
}
VIEW = {
    "anyOf": [
        {"type": "string"},
        {"type": "object", "additionalProperties": {"type": ["number", "string", "boolean"]}},
    ],
    "description": "View source or declared control values, read without saving.",
}
TERMINAL = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "EXPIRED"}


def invalid(message):
    raise KeelError(message, error_code="invalid_arguments", exit_code=2)


def read_envelope(args, ctx):
    selected = [key for key in ("strategy_id", "run_id", "share_id") if args.get(key)]
    if len(selected) > 1 or (args.get("version") is not None and not args.get("strategy_id")):
        invalid("Choose at most one strategy_id, run_id or share_id; version requires strategy_id.")
    if args.get("run_id"):
        path = f"/v1/result-runs/{seg(args['run_id'])}/envelope"
    elif args.get("strategy_id"):
        path = f"/v1/screeners/{seg(args['strategy_id'])}/envelope"
    elif args.get("share_id"):
        path = f"/v1/screener-shares/{seg(args['share_id'])}/envelope"
    else:
        path = "/v1/result-runs/latest/envelope"
    params = {
        key: args[key]
        for key in ("version", "ranked_by", "order", "view")
        if args.get(key) is not None
    }
    if isinstance(params.get("view"), dict):
        params["view"] = json.dumps(params["view"], separators=(",", ":"))
    params["shape"] = "card" if card_meta_move() else "fallback"
    result = ctx.get_client().get(path, **params)
    if not isinstance(result, dict) or result.get("schema") != "keel.screener.envelope/1":
        raise KeelError(
            "The result envelope has an unsupported schema.", error_code="invalid_response"
        )
    return result


def date_words(value):
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        stamp = datetime.fromtimestamp(value / 1000, UTC)
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.strftime("%b %d %H:%M UTC").replace(" 0", " ")


def text(value, limit=80):
    value = str(value or "").replace("\n", " ").replace("|", "\\|")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def number(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "—"
        return format(value, ".4g").replace("-", "−")
    return text(value, 32)


MINUS = "\u2212"
#: Storage scale to percent points (keel-app `render/format.ts` PERCENT_SCALE):
#: percent is stored ×100, fractions and rates as decimals.
PERCENT_SCALE = {"percent": 1, "fraction": 100, "rate_per_hour": 100, "rate_per_year": 100}
#: The unit words a table header carries, so each cell stays a bare figure.
#: An unknown unit carries none: its values read unitless (founder ruling
#: 2026-11-04: no "unknown" unit label anywhere).
HEADER_UNIT_WORDS = {
    "rate_per_year": "% a year",
    "rate_per_hour": "% an hour",
}


def _fixed(value, decimals, signed=False):
    rounded = 0.0 if float(format(abs(value), f".{decimals}f")) == 0 else value
    sign = MINUS if rounded < 0 else "+" if signed and rounded > 0 else ""
    return sign + format(abs(rounded), f".{decimals}f")


def _price(value):
    """S1 `ssPrice`: grouped integers from 1000, two decimals from 1, else 3 significant."""
    a = abs(value)
    sign = MINUS if value < 0 else ""
    if a >= 1000:
        return sign + f"{round(a):,}"
    if a >= 1:
        return _fixed(value, 2)
    return "0" if a == 0 else sign + format(a, ".3g")


def _ordinal(n):
    tail = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{tail}"


def screen_value(value, unit=None):
    """One table cell as keel-app's `formatScreenValue` writes it (raw transform):
    percents and rates signed with one decimal, S1's price rule for levels."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return number(value)
    if not math.isfinite(value):
        return "—"
    if unit in PERCENT_SCALE:
        return _fixed(value * PERCENT_SCALE[unit], 1, signed=True) + "%"
    if unit in ("x", "multiple"):
        return _fixed(value, 2) + "×"
    if unit == "rank":
        return _ordinal(round(value * 100))
    if unit == "flag":
        return (MINUS if value < 0 else "") + str(abs(round(value)))
    if unit == "count" and float(value).is_integer():
        return (MINUS if value < 0 else "") + f"{abs(int(value)):,}"
    if unit == "score":
        return _fixed(value, 2)
    return _price(value)


#: The rows a screen's text block lists (Q-3634): the rows its rendered view
#: shows — every coin its filter passes, or the head of the ranking when it
#: has none — up to this many. claude.ai's model reads nothing but this text,
#: so these are the rows an agent can quote; the count of the rest is said.
TEXT_ROWS = 25
#: A receipt names the first few of them on its one line.
RECEIPT_ROWS = 5

#: Words that already state a column's unit ("Funding now, a year"), so its
#: header does not say it twice ("Funding now, a year (% a year)"), Q-3634.
_UNIT_STATED = {
    "rate_per_year": re.compile(
        r"\b(a|per)\s+year\b|\bannual(ly|ised|ized)?\b|/\s*y(ea)?r\b|\bAP[RY]\b", re.I
    ),
    "rate_per_hour": re.compile(r"\b(an|per)\s+hour\b|\bhourly\b|/\s*h(ou)?r\b", re.I),
}


def cell(value):
    """A table cell's text in full: never cut (Q-3634), only kept on one row."""
    return str(value if value is not None else "").replace("\n", " ").replace("|", "\\|")


def unit_stated(label, unit, words=None):
    """Whether ``label`` already says its unit, so a header or an outputs row
    does not say it twice ("Funding now, a year (% a year)", Q-3634): the
    rate phrasings for the two rates, a ``%`` for a percent, else the unit's
    own ``words`` in the label."""
    if not label:
        return False
    if unit in _UNIT_STATED:
        return bool(_UNIT_STATED[unit].search(label))
    if words and "%" in words:
        return "%" in label
    return bool(words) and words.lower() in label.lower()


def column_label(item, name):
    """A column header: the view's label in full, with the unit said once."""
    label = cell((item or {}).get("label") or name)
    unit = (item or {}).get("unit")
    words = HEADER_UNIT_WORDS.get(unit)
    if words and not unit_stated(label, unit):
        label += f" ({words})"
    return label


def coins(n):
    return f"{n} coin" + ("" if n == 1 else "s")


def range_words(span, unit, rendered):
    """``Across 179 coins: −140.6% (MINA) to +60.9% (XMR) a year; 44 below
    zero`` — the ranked output over EVERY coin with a value, said whenever
    the rows the text lists are fewer than those coins (Q-3638, Q-3639).

    The text lists only the rendered rows (a top 15 of positive funding),
    and a model that reads nothing else told the user no coin paid negative
    funding while MINA sat at −140.6% a year. ``span`` is keel-api's
    ``completed.range`` (CONTRACT field 1); absent or malformed, nothing is
    said — never a range computed from the rows in hand, which would be the
    shown subset's range wearing the universe's words. ``below_zero`` null
    means the output's values cannot be negative, so that clause is left out.
    """
    if not isinstance(span, dict):
        return None
    count = _count(span.get("count"))
    low, high = span.get("low"), span.get("high")
    if not count or rendered >= count or not isinstance(low, dict) or not isinstance(high, dict):
        return None
    values = (low.get("value"), high.get("value"))
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    ):
        return None
    words = HEADER_UNIT_WORDS.get(unit)
    tail = " " + words.removeprefix("% ") if words else ""
    line = (
        f"Across {coins(count)}: {screen_value(values[0], unit)} ({cell(low.get('asset'))})"
        f" to {screen_value(values[1], unit)} ({cell(high.get('asset'))}){tail}"
    )
    below = _count(span.get("below_zero"))
    if below is not None:
        line += f"; {below if below else 'none'} below zero"
    return line


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def passing_facts(passing, total):
    """What a view's filter passed, or None for a view with no filter.

    keel-api serves ``passing`` null for a view with no filter (contract 2);
    an envelope from before that says every coin passes and nothing moved,
    which reads the same way: a ranking, not a pass list.
    """
    if not isinstance(passing, dict):
        return None
    counts = passing.get("counts") or {}
    now = passing.get("now") or {}
    count = _count(passing.get("count"))
    count = count if count is not None else _count(counts.get("passing"))
    count = count if count is not None else len(now.get("assets") or [])
    of = _count(passing.get("total")) or total
    entered = _count(counts.get("entered"))
    entered = len(passing.get("entered") or []) if entered is None else entered
    left = _count(counts.get("left"))
    left = len(passing.get("left") or []) if left is None else left
    if of and count >= of and not entered and not left:
        return None
    tie = passing.get("tie") if isinstance(passing.get("tie"), dict) else None
    return {
        "count": count,
        "of": of,
        "now": now.get("assets") or [],
        "entered": (passing.get("entered") or [], entered),
        "left": (passing.get("left") or [], left),
        "previous_ms": (passing.get("previous") or {}).get("bar_ms"),
        "tie": tie if tie and (_count(tie.get("count")) or 0) > 1 else None,
    }


def _names(values, count, full):
    values = values or []
    shown = values if full else values[:5]
    rest = (len(values) if count is None else count) - len(shown)
    return ", ".join(text(v, 20) for v in shown) + (f" and {rest} more" if rest > 0 else "")


def moved_line(facts, *, full=False):
    """``Since <bar>: entered …; left ….`` — only the sides that moved, and
    nothing when neither did (keel-api leaves tie reshuffles out, contract 2)."""
    parts = []
    for word, key in (("entered", "entered"), ("left", "left")):
        values, count = facts[key]
        if count:
            parts.append(f"{word} {_names(values, count, full)}")
    if not parts:
        return None
    return f"Since {date_words(facts['previous_ms'])}: " + "; ".join(parts) + "."


def passing_lines(passing, total, *, full=False):
    """The pass count and what moved, with every name (keel_screener_passing)."""
    facts = passing_facts(passing, total)
    if facts is None:
        return []
    lines = [
        f"{facts['count']} of {facts['of']} pass now: {_names(facts['now'], facts['count'], full)}"
    ]
    moved = moved_line(facts, full=full)
    return lines + ([moved] if moved else [])


#: Earlier view revisions the `view source:` line names (the rest stay in
#: structuredContent's `view_source.revisions`).
TEXT_REVISIONS = 5


def view_source_text(block):
    """The text block's `view source:` line: the saved view's revision and its
    source as a fenced block (Q-3634).

    claude.ai hands its model nothing but the text block, so a source that rode
    only in structuredContent never reached the agent that asked for it, and it
    guessed `expected_revision=0`. Revision 0 is Keel's default view, before
    anything is saved.
    """
    if not isinstance(block, dict) or not isinstance(block.get("source"), str):
        return None
    source = block["source"].rstrip("\n")
    revision = _count(block.get("revision")) or 0
    count = source.count("\n") + 1
    head = f"view revision {revision}" + (
        " (Keel's default view; nothing saved yet)" if not block.get("persisted") else ""
    )
    head += f", {count} line" + ("" if count == 1 else "s")
    earlier = [
        r
        for r in block.get("revisions") or []
        if isinstance(r, dict) and _count(r.get("revision")) not in (None, revision)
    ][:TEXT_REVISIONS]
    if earlier:
        head += "; earlier revisions " + "; ".join(
            f"{r['revision']} ("
            + ", ".join(
                part
                for part in (
                    date_words(r["saved_at"]) if r.get("saved_at") else None,
                    cell(r["label"]) if r.get("label") else None,
                )
                if part
            )
            + ")"
            for r in earlier
        ).replace(" ()", "")
    return f"{head}\n```python\n{source}\n```"


def build_result(envelope, ctx, *, size="view", full_passing=None):
    screen = envelope.get("screener") or {}
    run = envelope.get("run") or {}
    completed = envelope.get("completed")
    share = envelope.get("share") or {}
    rid = run.get("run_id") or run.get("result_run_id")
    sid = screen.get("strategy_id") or screen.get("id")
    url = share.get("url") or (
        app_url_for("screener", sid, ctx, query={"srun": rid})
        if sid
        else app_url_for("result", rid, ctx)
    )
    name = cell(screen.get("name") or "Untitled screen")
    version = screen.get("version") or screen.get("sequence")
    revision = _count(screen.get("view_revision"))
    status = run.get("status", "QUEUED")
    view = {
        "kind": "screener",
        "size": size,
        "name": name,
        "version": version,
        "status": status,
        "object": sid or rid,
        "at": run.get("finished_at"),
        "seq": run.get("sequence", 0),
    }
    extra = {"view": view, "screener": screen, "run": run, "in_flight": envelope.get("in_flight")}
    link = f"View in Keel: {url}"
    # "screen v1 · view rev 2": which saved view the rows were drawn with, so
    # an edit can be confirmed (Q-3634); a screen on Keel's default view has none.
    title = (f" v{version}" if version else "") + (f" · view rev {revision}" if revision else "")
    if completed is None:
        words = run.get("error_words") or run.get("status_words") or str(status).lower()
        # The card reads the same words (it draws `run.status_words`), so a run
        # in flight says it is running on the card too, not only in the text.
        extra["run"] = {**run, "status_words": words}
        # The card's served words (`copy.card`) when the envelope carries
        # them; the saved view's revision rides `screener.view_revision`.
        if isinstance(envelope.get("copy"), dict):
            extra["copy"] = envelope["copy"]
        view["markdown"] = (
            f"Screen · {name}"
            + title
            + f" · {words}"
            # The run id, so a host that reads only text can read it when done.
            + (f" · run {rid}" if rid else "")
            + f"\n{link}"
        )
        return OutcomeResult(run_id=rid, hero_url=url, extra=extra)
    copy = completed.get("copy") or {}
    manifest = completed.get("manifest") or {}
    universe = manifest.get("universe") or {}
    total = universe.get("total", universe.get("count", 0))
    universe_label = universe.get("label") or f"{total} asset" + ("" if total == 1 else "s")
    latest = completed.get("latest") or {}
    columns = latest.get("columns", [])
    rows = latest.get("rows", [])
    ranked = completed.get("ranked_by")
    labels = completed.get("labels") or []
    output_names = [o.get("name") for o in manifest.get("outputs", [])]
    label_map = {name: item for name, item in zip(output_names, labels)}

    def label(name):
        return column_label(label_map.get(name), name)

    def plain(name):
        return cell((label_map.get(name) or {}).get("label") or name)

    def unit_of(name):
        return (label_map.get(name) or {}).get("unit")

    facts = passing_facts(
        full_passing if full_passing is not None else completed.get("passing"), total
    )
    # The coins the view shows, in rank order: keel-api filters the rows by
    # the view's filter, and the hero's list names exactly the passing coins.
    allowed = set((completed.get("hero") or {}).get("passing") or [])
    if facts is not None and allowed and columns[:1] == ["asset"]:
        rows = [row for row in rows if row and row[0] in allowed]
    coverage = completed.get("coverage") or {}
    with_value, of_coverage = _count(coverage.get("with_value")), _count(coverage.get("of"))
    if facts is not None:
        listed = facts["count"]
    elif columns[:1] == ["asset"]:
        # The coins with a value are the ones ranked; never more than the screen has.
        listed = with_value if with_value is not None and with_value <= total else total
    else:
        listed = len(rows)
    shown_rows = rows[: min(listed, TEXT_ROWS)] if listed else rows[:TEXT_ROWS]
    order_words = "lowest first" if completed.get("order") == "asc" else "highest first"
    ranked_words = f", ranked by {plain(ranked)} ({order_words})" if ranked else ""
    tie_words = None
    if facts is not None:
        summary = f"{facts['count']} of {coins(facts['of'])} pass now{ranked_words}."
        tie = facts["tie"]
        if tie:
            # A cutoff inside a tie (contract 2): which of the tied coins made
            # the list is not a market fact, so the text says they tie.
            tie_words = (
                f"{tie['count']} coins tie at {screen_value(tie.get('value'), unit_of(ranked))}"
                " at the cutoff."
            )
    elif ranked and columns[:1] == ["asset"]:
        summary = f"{coins(listed)} ranked by {plain(ranked)} ({order_words})."
    else:
        summary = None

    def row_cells(row, index):
        return [
            cell(row[i] if i < len(row) else None)
            if columns[i] in ("asset", "output")
            else screen_value(row[i] if i < len(row) else None, unit_of(columns[i]))
            for i in index
        ]

    # The view's own columns when it declares them (a Table), else the
    # ranking first: the columns the rendered view draws.
    view_columns = ((completed.get("graphs") or {}).get("default_channels") or {}).get("columns")
    width = len(rows[0]) if rows else len(columns)

    def carried(wanted):
        return [
            columns.index(c)
            for c in dict.fromkeys(wanted)
            if c in columns and c not in ("asset", "output") and columns.index(c) < width
        ][:3]

    value_index = (carried(view_columns) if view_columns else []) or carried([ranked, *columns])
    index = [i for i in (columns.index(c) for c in ("asset", "output") if c in columns)][:1]
    index += value_index
    head = f"**{name}** · screen{title}" + "".join(
        f" · {part}"
        for part in (
            copy.get("clock"),
            f"as of {date_words(completed.get('as_of_ms'))}",
            copy.get("freshness"),
        )
        if part
    )
    lines = [head]
    if full_passing is not None:
        lines += passing_lines(full_passing, total, full=True)
    else:
        if summary:
            lines.append(summary + (f" {tie_words}" if tie_words else ""))
        moved = moved_line(facts) if facts is not None else None
        if moved:
            lines.append(moved)
    span = completed.get("range") if isinstance(completed.get("range"), dict) else None
    # The rows this text lists — the table at view size, the first few on a
    # receipt's one line; fewer than the coins ranked and the range is said.
    listed_here = (
        0 if not index else len(shown_rows) if size == "view" else len(shown_rows[:RECEIPT_ROWS])
    )
    span_words = range_words(span, unit_of((span or {}).get("output") or ranked), listed_here)
    if span_words and size == "view":
        lines.append(span_words + ".")
    if size == "view" and shown_rows and index:
        lines += [
            "",
            "| # | "
            + " | ".join(
                {"asset": "Asset", "output": "Output"}.get(columns[i]) or label(columns[i])
                for i in index
            )
            + " |",
            "| --- | " + " | ".join("---" for _ in index) + " |",
        ]
        lines += [
            f"| {n + 1} | " + " | ".join(row_cells(row, index)) + " |"
            for n, row in enumerate(shown_rows)
        ]
        hidden = listed - len(shown_rows) if listed else 0
        if hidden > 0:
            lines.append(
                f"{hidden} more pass — see Keel."
                if facts is not None
                else f"{hidden} more ranked — see Keel."
            )
    as_of = copy.get("as_of") if isinstance(copy.get("as_of"), dict) else {}
    notes = [as_of.get("words") or copy.get("inputs_through")]
    # "N of A assets have a value" only when fewer than all do (contract 7).
    if not (with_value is not None and of_coverage is not None and with_value >= of_coverage):
        notes.append(copy.get("coverage"))
    notes += [copy.get("gaps"), copy.get("rebased")]
    lines += [note for note in notes if isinstance(note, str) and note.strip()]
    lines.append(link)
    view.update(
        {
            "as_of": completed.get("as_of_ms"),
            "freshness": completed.get("freshness"),
            "clock": copy.get("clock"),
            "window": completed.get("window"),
            "markdown": "\n".join(lines),
        }
    )
    extra.update(
        {
            key: completed.get(key)
            for key in ("headline", "latest_top", "labels", "passing", "actions", "copy")
        }
    )
    if span is not None:
        # The ranked output's range over every coin with a value, for the
        # screen card and for any host whose model reads structuredContent.
        extra["range"] = span
    extra["manifest"] = {
        "clock": manifest.get("clock"),
        "universe": {
            "label": universe_label,
            "total": total,
            "with_value": with_value,
        },
        "outputs": [
            {
                key: value
                for key, value in {
                    "name": o.get("name"),
                    "shape": o.get("shape") or o.get("descriptor", {}).get("shape"),
                    "value_type": o.get("value_type") or o.get("descriptor", {}).get("value_type"),
                    "bounds": o.get("descriptor", {}).get("domain"),
                    "discrete": o.get("descriptor", {}).get("discrete"),
                }.items()
            }
            for o in manifest.get("outputs", [])
        ],
        "run": {
            "status": status,
            "as_of": completed.get("as_of_ms"),
            "window": completed.get("window"),
        },
        "ranked_by": ranked,
        "order": completed.get("order"),
        "disclosures": completed.get("disclosures", [])[:4],
        "disclosures_more": max(0, len(completed.get("disclosures", [])) - 4),
    }
    # Every output's label in full, beside its unit (contract 6: no cut).
    extra["labels"] = [
        {"label": str(item.get("label") or ""), "unit": str(item.get("unit") or "")}
        for item in labels
    ]
    extra["actions"] = [{"label": "Open in Keel", "url": url}]
    if sid and ranked:
        extra["actions"].append(
            {
                "label": "Test as a strategy",
                "url": app_url_for("actions", sid, ctx, query={"automate": ranked}),
            }
        )
    extra.update({key: completed.get(key) for key in ("hero", "graphs")} if size == "view" else {})
    if size == "view" and envelope.get("shape") == "card":
        extra["latest"] = completed.get("latest")
    if size == "receipt":
        # The first few coins with their ranked value: what a run answers,
        # on the one line a receipt has.
        first = value_index[:1]
        top = ", ".join(
            " ".join(row_cells(row, index[:1] + first))
            for row in shown_rows[:RECEIPT_ROWS]
            if row and index
        )
        parts = [
            f"Screen · {name}{title}",
            copy.get("clock"),
            summary.rstrip(".") if summary else None,
            top or None,
            span_words,
            f"as of {date_words(completed.get('as_of_ms'))}",
        ]
        view["markdown"] = " · ".join(p for p in parts if p) + f"\n{link}"
    return OutcomeResult(run_id=rid, hero_url=url, share_url=share.get("url"), extra=extra)
