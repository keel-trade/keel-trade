"""The one text and envelope builder for Research's agent surfaces (spec 07 §3.2, §3.3, §3.5).

Every research tool's text block is built here from keel-api's served JSON
(the route contract, ``services/keel-api/src/schemas/research.py``): the CLI
prints it, MCP hosts read it as ``content``. Nothing here measures, rounds a
measurement into a claim, or writes a sentence keel-api serves: plan, queue,
limit and refusal words are ``words.py``'s (RL-53) and arrive in the payload;
state words and the footing come from ``card_summary`` (07 §3.7).

The rules (07 §3.2, each tested):

1. the first line is ``{kind label} · {title} · version {n} · {state words}``;
2. the footing line names the days, the split, the coins and how they were
   chosen, the costs and the data;
3. every measurement line carries value, unit, interval (or its withheld
   reason) and sample; the method versions are said once;
4. lists name their population, write at most 25 rows, and name the call that
   reads the rest;
5. what was not measured is said;
6. a platform failure reads "didn't finish on our side", never a failed check;
7. no verdict words (``VERDICT_WORDS``; AT-7 scans this module's copy);
8. numbers are formatted by the one formatter, the vendored
   ``pipeline_engine.units.format_unit_value`` (an unknown unit raises);
9. the last line is ``View in Keel: {url}``;
10. the text is at most 8,000 characters at the largest legal shape.

Every ``next`` call is generated from the registry the caller is served
(:func:`next_call`): a tool the surface does not serve becomes
``keel_app_link`` to the page that does it (07 §3.4, AT-14).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


__all__ = [
    "Keep",
    "LIST_ROWS_MAX",
    "TEXT_MAX_CHARS",
    "VERDICT_WORDS",
    "bar_row",
    "data_text",
    "next_call",
    "plan_text",
    "project_text",
    "structured_study",
    "study_text",
]

TEXT_MAX_CHARS = 8_000
LIST_ROWS_MAX = 25
STRUCTURED_CHECK_ROWS = 5
#: 07 §4.4: a step the reader's plan does not include, on its own line (a plan's step, a bar over a step the version
#: ran without), with no number. The full sentence, ``words.agent_locked_step``'s, is served in ``locked[].words``.
NOT_INCLUDED = "not included in this account's plan"

#: 07 §3.2 rule 7: copy never judges. AT-7 scans this module's literals for them.
VERDICT_WORDS = (
    "robust",
    "overfit",
    "holds up",
    "fragile",
    "mixed",
    "strong",
    "weak",
    "good",
    "bad",
)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ── Small formatters ──────────────────────────────────────────────────────────


def _day(value: str | None) -> str:
    """``2025-03-23`` → ``Mar 23, 2025``: 06 §12.7's served day words, month first (keel-api's ``words.served_day``,
    7794649523), so the text never mixes "23 Mar 2025" with a served "picked Mar 23, 2025". The year is always
    written: an agent's text is read without the reader's calendar."""
    if not value:
        return "?"
    d = date.fromisoformat(str(value)[:10])
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _value(value: Any, unit: str | None) -> str:
    """A served measurement in its unit, through the one formatter (rule 8)."""
    if value is None:
        return "not measured"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if not unit:
        return format(value, ".12g")
    from pipeline_engine.units import RESEARCH_UNIT_KEYS, format_unit_value

    return format_unit_value(value, unit, keys=RESEARCH_UNIT_KEYS)


def _sample(item: Mapping[str, Any]) -> str:
    """A number's sample as served (D9, STG-SURFACES #2): keel-api's ``sample_words`` (``research.words``: a bar's
    sample line, a measure's counts or span), the string the card and the app show, never rebuilt from ``sample`` here.
    AUDIT-WORDS M7: empty when none is served (the footing line states the study's days), never "sample not stated"."""
    said = item.get("sample_words")
    return str(said) if isinstance(said, str) and said else ""


#: AUDIT-WORDS M6: a served reason's own lead word, dropped where the line already says it ("no interval (…)",
#: "not measured: …"), so a line never reads "interval withheld: Withheld: …".
_REASON_LEADS = ("withheld:", "no interval:", "not measured:")


def _why(reason: Any) -> str:
    """A served reason as a clause (:func:`_clause`) without its lead word (:data:`_REASON_LEADS`)."""
    said = _clause(reason)
    for lead in _REASON_LEADS:
        if said.lower().startswith(lead):
            said = said[len(lead) :].strip()
            break
    return said


def _no_interval(reason: Any) -> str:
    """M6: a number without an interval, and why when a reason is served: "no interval (the values share their
    data)"."""
    why = _why(reason)
    return f"no interval ({why})" if why else "no interval"


def _clause(reason: Any) -> str:
    """A served sentence used as a clause inside a line: its closing full stop dropped, so the line never reads
    ".;" (MCP-FINAL #8). The words are otherwise as served."""
    return str(reason).strip().rstrip(".").strip() if isinstance(reason, str) else ""


def _interval(item: Mapping[str, Any]) -> str:
    iv = item.get("interval")
    if iv:
        unit = item.get("unit")
        return f"interval {_value(iv.get('lo'), unit)} to {_value(iv.get('hi'), unit)}"
    return _no_interval(item.get("withheld_reason"))


def _met(row: Mapping[str, Any]) -> str:
    """A bar's verdict; empty with no verdict (AUDIT-WORDS M5: a measured number with no bar set read "No bar set; not
    measured"). A bar with no number says why in its own line (:func:`bar_row`)."""
    met = row.get("met")
    if met is None:
        return ""
    return "met" if met else "not met"


def _join(*parts: str) -> str:
    """A measurement line's clauses, the empty ones left out (M5, M7)."""
    return "; ".join(p for p in parts if p)


def _quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


#: RI-253: what a listed caller is pointed to when an unserved call names no study (07 §3.2 rule 9: every research
#: answer's last line is "View in Keel: {url}").
VIEW_IN_KEEL = "the View in Keel link below"


def next_call(tool: str, args: Mapping[str, Any], *, app_url: str | None = None) -> str:
    """A generated next call for THIS caller's surface (07 §3.4, AT-14).

    On the listed profile a tool the listing does not serve is replaced by ``keel_app_link`` to the study's page:
    ``keel_app_link(id="stu_…")`` (RI-238 links a study id to ``/research/{id}``). The tool takes an ``id`` and has no
    ``url`` argument, so the ``keel_app_link(url=…)`` this once rendered was refused as an unexpected argument
    (RI-253; ``_research_commands`` took the same fix for refusals, RI-237). A call that names no study has no link
    call to make: it points to the answer's own "View in Keel" line (``app_url`` is that link; it never becomes an
    argument).
    """
    from ._toolsets import is_listed_profile, listed_profile_tools

    if is_listed_profile() and tool not in listed_profile_tools():
        study_id = args.get("study_id")
        if isinstance(study_id, str) and study_id.startswith("stu_"):
            return f"keel_app_link(id={_quote(study_id)})"
        return VIEW_IN_KEEL
    rendered = ", ".join(f"{k}={_quote(v)}" for k, v in args.items() if v is not None)
    return f"{tool}({rendered})"


class Keep(str):
    """A line the cap never drops (WL-AG-04, A3 #8): one that acts (``Run it: …``, a next call, the call that reads
    the rest of a list) or that a reader cannot do without. The first line and the ``View in Keel`` line are kept
    too, by position."""


MORE_IN_THE_APP = "(more in the app)"


def _cap(lines: Sequence[str], last: str) -> str:
    """Join, keeping the first line, every :class:`Keep` line and the last line, and hold rule 10 by construction:
    body detail is dropped from the end first, whole lines only (never a cut sentence), and "(more in the app)"
    stands where the first dropped line stood. Until A3 #8 the cap popped from the end, and a plan's last body line
    is its "Run it" line: a 40-step plan lost the one line that acts."""
    body = [line for line in lines if line]
    dropped: set[int] = set()

    def render() -> str:
        out: list[str] = []
        for i, line in enumerate(body):
            if i not in dropped:
                out.append(line)
            elif i == min(dropped):
                out.append(MORE_IN_THE_APP)
        return "\n".join([*out, last])

    text = render()
    for i in range(len(body) - 1, 0, -1):
        if len(text) <= TEXT_MAX_CHARS:
            break
        if not isinstance(body[i], Keep):
            dropped.add(i)
            text = render()
    return text


def _once(messages: Sequence[str], paths: Sequence[str | None]) -> list[str]:
    """WL-AG-04: a served warning repeated across steps prints once with its count, in first-seen order ("…
    (on 40 steps)"); a plan of 40 steps served ``STUDY_PLAN_STORED_EXPIRING`` 40 times, line by line."""
    seen: dict[str, list[str | None]] = {}
    for message, path in zip(messages, paths, strict=True):
        if message:
            seen.setdefault(message, []).append(path)
    out = []
    for message, where in seen.items():
        if len(where) == 1:
            out.append(message)
        elif all(str(p or "").startswith("/steps/") for p in where):
            out.append(f"{message} (on {len(where)} steps)")
        else:
            out.append(f"{message} ({len(where)} times)")
    return out


# ── The footing (rule 2) ──────────────────────────────────────────────────────


def count_words(n: int, one: str, many: str | None = None) -> str:
    """``research.words.count_words``' rule: "1 coin", "30 coins" (STG-SURFACES #1: the footing read "1 coins")."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def footing_words(footing: Mapping[str, Any] | None) -> str | None:
    if not footing:
        return None
    days = footing.get("days") or {}
    line = f"Days {_day(days.get('start'))} to {_day(days.get('end'))}"
    if days.get("start_moved_from"):
        line += f" (moved from {_day(days['start_moved_from'])})"
    if footing.get("split"):
        line += f", split {_day(footing['split'])}"
    coins = footing.get("coins") or {}
    chosen = str(coins.get("chosen") or "")
    chosen = chosen[:1].lower() + chosen[1:]
    line += (
        f" · {count_words(coins['count'], 'coin')}: {chosen}"
        if isinstance(coins.get("count"), int)
        else ""
    )
    costs = footing.get("costs")
    if costs == "none" or not costs:
        line += " · no costs"
    else:
        # RI-507: fee PLUS slippage a fill, as the card's served footing line says it (research.card_summary
        # .cost_bp_a_fill, D-113); the fee alone read 4.5 where the card reads 9
        fill = float(costs.get("fee", 0)) + float(costs.get("slippage", 0))
        line += f" · costs {format(fill * 10_000, '.4g')} bp a fill"
    if footing.get("delay"):
        line += f" · trading {footing['delay']} bar{'s' if footing['delay'] != 1 else ''} late"
    if footing.get("leverage") not in (None, 1, 1.0):
        line += f" · exposure multiple {format(footing['leverage'], 'g')}"
    line += f" · data as of {_day(days.get('end'))}"
    return line


# ── A study (07 §4.4) ─────────────────────────────────────────────────────────


#: AUDIT-WORDS M1: a state in the card's words (``research.card_summary.state_words``) for a read that serves no
#: card; the raw state ("planned") is never printed.
STATE_WORDS = {
    "planned": "Planned",
    "queued": "Queued",
    "running": "Running",
    "cancel_requested": "Stopping",
    "cancelled": "Stopped",
    "failed": "Didn't finish",
    "completed": "Done",
    "completed_partial": "Done",
}


def _state_words(env: Mapping[str, Any]) -> str:
    card = env.get("card_summary") or {}
    said = card.get("state_words")
    if isinstance(said, str) and said:
        return said
    return STATE_WORDS.get(str((env.get("study") or {}).get("state") or ""), "")


def _first_line(env: Mapping[str, Any]) -> str:
    study = env.get("study") or {}
    kind = (study.get("kind_label") or {}).get("name", "Study")
    state = _state_words(env)
    if study.get("share_id"):
        # AUDIT-WORDS M64: the app's noun for a share, "Shared study"
        line = f"Shared study · {kind} · {study.get('title', '')} · as run {_day(study.get('as_run_on'))}"
        by = _shared_by(env)
        return f"{line} · shared by {by}" if by else line
    line = f"{kind} · {study.get('title', '')} · version {study.get('version')}"
    line += f" · {state}" if state else ""
    linked = study.get("linked_from")
    if linked:
        # D-176 (RI-178): the served words ("Linked from a study that was deleted" once the source is gone, when
        # study_id is null); the id and version stay for an agent's next call while the source exists.
        said = linked.get("words")
        if linked.get("study_id"):
            line += f" · from {linked['study_id']} v{linked.get('version')}"
            if said:
                line += f" ({said})"
        elif said:
            line += f" · {said}"
    return line


def _shared_by(env: Mapping[str, Any]) -> str | None:
    """07 §4.4: a share's maker, "Keel" exactly when the share is Keel's own (``by_keel``, RI-235: no display name
    makes a share Keel's), else the sharer's name while the share shows it (``card_summary.maker``, D-199); null when
    the share shows no name."""
    if env.get("by_keel") is True:
        return "Keel"
    card = env.get("card_summary") or {}
    # AUDIT-WORDS M22: the served maker words ("by your agent"), never the raw maker ("your_agent")
    said = card.get("maker_words")
    if isinstance(said, str) and said.startswith("by "):
        return said[3:] or None
    maker = card.get("maker")
    if not maker or maker == "keel":
        return None
    return _MAKER.get(str(maker), str(maker))


_MAKER = {"you": "you", "your_agent": "your agent"}


#: ``research.words._KEEP_CAPITAL``: the words a label keeps capitalised inside a sentence.
_KEEP_CAPITAL = frozenset({"Sharpe", "CAGR", "BTC", "IC", "Keel"})


def _method_line(env: Mapping[str, Any]) -> str | None:
    """D-401 (STG-SURFACES #8): the served plain-words sentence (``words.method``, ``research.words.method_line``: "Measured
    with the backtest, costs from none to six times and …"), so the text and the app say one thing; a server before it
    serves none, and the text names every step by its served label. Never a class name or a version."""
    said = (env.get("words") or {}).get("method")
    if isinstance(said, str) and said.strip():
        return said.strip()
    steps = env.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    # D-401: steps by their served labels in words, never "Backtest v1"
    seen: list[str] = []
    for s in steps:
        label = str(s.get("label") or "").strip()
        first = label.split(" ", 1)[0]
        # research.words' sentence rule: a name or an acronym keeps its capital ("Sharpe discounted for …")
        said = label if first in _KEEP_CAPITAL or first.isupper() else label[:1].lower() + label[1:]
        if said and said not in seen:
            seen.append(said)
    return ("Measured with " + ", ".join(seen) + ".") if seen else None


def _locked_steps(env: Mapping[str, Any]) -> set[str]:
    """The step ids whose served run state is ``locked`` for this reader (04 §2.4, D-198): a step the reader's plan
    cannot run and the read version holds no evidence of (``research.run_state.step_run_states``).

    RI-267: a read that serves no run state for a step (a share read serves no steps) reads each bar row's own
    ``locked`` (RI-249: the version ran without that step, the snapshot's frozen flag), so a shared Free Check's
    re-picking bar reads as the study read's does. Where the step's run state is served it wins: a Pro reader of a
    version that ran without re-picking reads ``needs_run`` (RI-223), never locked, though the row says ``locked``."""
    out: set[str] = set()
    stated: set[str] = set()
    for step in env.get("steps") or []:
        state = step.get("run_state") if isinstance(step, Mapping) else None
        if isinstance(state, Mapping):
            stated.add(str(step.get("id")))
            if state.get("state") == "locked":
                out.add(str(step.get("id")))
    checks = env.get("checks")
    for row in (checks.get("rows") or []) if isinstance(checks, Mapping) else []:
        step = str(row.get("ref") or "").split(".", 1)[0] if isinstance(row, Mapping) else ""
        if step and row.get("locked") is True and step not in stated:
            out.add(step)
    return out


def _target(env: Mapping[str, Any]) -> dict[str, str]:
    """The id a next call reads this answer by: ``study_id``, or a share read's ``share_id`` (A3 #4: the "more
    bars" call of a share once named no id at all)."""
    study = env.get("study") or {}
    if study.get("study_id"):
        return {"study_id": str(study["study_id"])}
    if study.get("share_id"):
        return {"share_id": str(study["share_id"])}
    return {}


#: A check row with no bar (a seed's or Keel's Checks) on a snapshot frozen before D-407 still says this; the text
#: never prints it (D-407 (2): no "No bar set" on a row).
NO_BAR_SET = "No bar set"

#: research.words.NO_BARS_WORDS (D-407 (2)): the one line a Check with no bars set says under its measured checks, the
#: same words the app, the card and chat say: the owner's, and a share's or showcase's reader's.
NO_BARS_WORDS = {
    "owner": "Set a bar on any check to see whether it meets it",
    "copy": "Make it yours to set your own bars",
}


def _lock_more(env: Mapping[str, Any]) -> dict[str, str]:
    """Research D-422 (value-and-pricing spec 02 §C3.1, §C3.2): the plans that include each locked step, as served
    (``locked[].more``, "Trader, Quant and Pro"), by step id; empty where none is served (the app, the top plan, the
    listed profile, which strips the rows)."""
    return {
        str(lk.get("step")): str(lk["more"])
        for lk in env.get("locked") or []
        if isinstance(lk, Mapping) and lk.get("step") and lk.get("more")
    }


def bar_row(
    r: Mapping[str, Any],
    locked: set[str],
    *,
    no_bars: bool = False,
    more: Mapping[str, str] | None = None,
) -> str:
    """One bar as its measurement line (rule 3). ``no_bars``: the list says no bar is set (:func:`_bars_lines`), so
    each row is its number alone, never "No bar set" eight times. ``more``: :func:`_lock_more`'s plans by step id, so
    a locked row reads "included on Trader, Quant and Pro" (D-422)."""
    step = str(r.get("ref") or "").split(".", 1)[0]
    if r.get("met") is None and step in locked:
        # RI-243 (07 §4.4, D-201): a bar over a step the version ran without, on a plan without it, has no
        # number and is out of the served count: it says so, never "not measured". D-422: and the plans that
        # include it, as served.
        names = (more or {}).get(step)
        return f"- {r.get('label')}: " + (f"included on {names}" if names else NOT_INCLUDED)
    if isinstance(r.get("value_words"), str) and r["value_words"]:
        # D-328 D9 (13 §3.2, WL-AG-12): the row's numbers in keel-api's served words (research.words), the same
        # strings the card and the app show, so a reply never shows a second rendering of a number the user sees.
        bar = r.get("bar_words") or (
            f"your bar {r.get('op')} {r.get('bar')}" if r.get("op") else NO_BAR_SET
        )
        if no_bars and bar == NO_BAR_SET:
            bar = ""
        if r.get("met") is None and r.get("value") is None:
            # MCP-FINAL #8: a bar with no number says why once, in its served reason, and names a sample only when
            # one is served: it read "not measured, interval withheld: Not measured: … only 18 are active.; sample
            # not stated; your bar ≥ 75th; not measured"
            why = _why(r.get("withheld_reason"))
            return f"- {r.get('label')}: " + _join(
                f"not measured: {why}" if why else "not measured", _sample(r), bar
            )
        interval = r.get("interval_words") or _no_interval(r.get("withheld_reason"))
        return f"- {r.get('label')}: {r['value_words']}, " + _join(
            interval, _sample(r), bar, _met(r)
        )
    # A row served without words (a snapshot frozen before WL-SV-04) reads through the one formatter, as before.
    bar = (
        f"your bar {r.get('op')} {_value(r.get('bar'), r.get('unit'))}"
        if r.get("op")
        else ("" if no_bars else NO_BAR_SET)
    )
    return f"- {r.get('label')}: {_value(r.get('value'), r.get('unit'))}, " + _join(
        _interval(r), _sample(r), bar, _met(r)
    )


def _as_drawn(env: Mapping[str, Any], item: Mapping[str, Any]) -> Mapping[str, Any]:
    """MCP-FINAL batch 4 (D9, one served string per number): a check row or headline measure with the strings its
    tile draws. keel-api serves a number's words twice, on its row and on the card's tile (``card_summary.numbers``),
    and they disagreed (the tile "0.45 chance the edge is real with one version known" · "365 days", the row "0.45
    chance the edge is real" · "Nov 17, 2025 to Nov 17, 2026"). The text says what the card draws.

    A tile is the item's own only when it is unambiguously the SAME number (SDK-FIX, MCP-FINAL p2 S1): the same
    ``ref`` and ``measure`` and the same served ``value``, and, where several tiles still qualify, the same ``label``.
    ``(ref, measure)`` alone names a measure, not a number: a Compare serves one ``("base.summary", "sharpe")`` tile
    per result, and keying on it gave every result the first tile's words (stu_01m7xm5081: "0.76" three times for
    0.76, −0.16, −0.089). An item with no value, or no single matching tile, keeps its own served words, never
    another number's."""
    card = env.get("card_summary") if isinstance(env.get("card_summary"), Mapping) else {}
    value = item.get("value")
    if item.get("ref") is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return item
    same = [
        tile
        for tile in card.get("numbers") or []
        if isinstance(tile, Mapping)
        and (tile.get("ref"), tile.get("measure")) == (item.get("ref"), item.get("measure"))
        and isinstance(tile.get("value"), (int, float))
        and not isinstance(tile.get("value"), bool)
        and math.isclose(float(tile["value"]), float(value), rel_tol=1e-12, abs_tol=0.0)
    ]
    if len(same) > 1:
        same = [tile for tile in same if tile.get("label") == item.get("label")]
    if len(same) != 1:
        return item
    drawn = {
        k: same[0][k]
        for k in ("value_words", "sample_words")
        if isinstance(same[0].get(k), str) and same[0][k]
    }
    return {**item, **drawn} if "value_words" in item or "value_words" in drawn else item


def _bars_lines(env: Mapping[str, Any], study_id: str | None) -> list[str]:
    checks = env.get("checks")
    if not checks:
        return []
    rows = list(checks.get("rows") or [])
    count, met = int(checks.get("count", len(rows))), int(checks.get("met") or 0)
    locked = _locked_steps(env)
    more = _lock_more(env)
    if count == 0 and rows and all(r.get("op") is None for r in rows):
        # AUDIT-WORDS M5, D-407 (2): a Check with no bar set (a seed's, Keel's showcase) says its numbers and the one
        # line the app says, never "Your bars (0; met: 0)" over rows that each read "No bar set; not measured"
        shared = bool((env.get("study") or {}).get("share_id"))
        lines = [f"{'Checks' if shared else 'Your checks'} ({len(rows)}; no bars set yet):"]
        lines.extend(
            bar_row(_as_drawn(env, r), locked, no_bars=True, more=more)
            for r in rows[:LIST_ROWS_MAX]
        )
        if len(rows) > LIST_ROWS_MAX:
            call = next_call("keel_research_get", {**_target(env), "output": "bars"})
            lines.append(Keep(f"and {len(rows) - LIST_ROWS_MAX} more: {call}"))
        if shared:
            lines.append(Keep(NO_BARS_WORDS["copy"]))
        else:
            call = next_call("keel_research_view_set", _target(env))
            lines.append(Keep(f"{NO_BARS_WORDS['owner']}: {call}"))
        return lines
    # AUDIT-WORDS M63: one noun for the list, "checks"; "bar" is only the threshold
    lines = [f"Your checks ({count}; {met} met):"]
    shown = rows[:LIST_ROWS_MAX]
    lines.extend(bar_row(_as_drawn(env, r), locked, more=more) for r in shown)
    rest = count - len(shown)
    if rest > 0:
        rest_met = met - sum(1 for r in shown if r.get("met") is True)
        call = next_call("keel_research_get", {**_target(env), "output": "bars"})
        lines.append(
            Keep(f"and {rest} more check{'s' if rest != 1 else ''}, {rest_met} met: {call}")
        )
    return lines


def _headline_lines(env: Mapping[str, Any]) -> list[str]:
    """A study with no bars writes its served headline measures (01 §3.3, at most four), each a measurement line
    (rule 3: value, unit, interval or its withheld reason, sample).

    SDK-FIX (JOURNEYS J5/J6 S2): a study with bars first says the strategy as it stands, its served
    ``headline.stands`` (return, Sharpe, max drawdown over the window), so the bars read against it; a server that
    serves no ``stands`` gives the headline measures no bar row already states. The bar lines carry the rest."""
    headline = env.get("headline") or {}
    if env.get("checks"):
        barred = {
            (r.get("ref"), r.get("measure"))
            for r in (env["checks"].get("rows") or [])
            if isinstance(r, Mapping)
        }
        measures = list(headline.get("stands") or []) or [
            m
            for m in (headline.get("measures") or [])[:4]
            if isinstance(m, Mapping) and (m.get("ref"), m.get("measure")) not in barred
        ]
    else:
        measures = list((headline.get("measures") or [])[:4])
    if not measures:
        return []
    out = ["In short:"]
    for m in (_as_drawn(env, x) for x in measures):
        if isinstance(m.get("value_words"), str) and m["value_words"]:
            # D9 (SERVE 63cc77014e): a headline measure's served words, as bar_row reads a check row's.
            interval = m.get("interval_words") or _no_interval(m.get("withheld_reason"))
            # MCP-FINAL p2 (a Map share): a measure frozen with no sample says none, never "sample not stated" (the
            # footing line above states the study's days)
            said = m.get("sample_words")
            tail = f"; {said}" if isinstance(said, str) and said else ""
            out.append(f"- {m.get('label')}: {m['value_words']}, {interval}{tail}")
            continue
        out.append(
            f"- {m.get('label')}: {_value(m.get('value'), m.get('unit'))}, "
            + _join(_interval(m), _sample(m))
        )
    return out


def _best_settings_line(env: Mapping[str, Any]) -> str | None:
    """SDK-FIX (JOURNEYS J5/J6 S2): a done Map says which settings came out best, in the card's served top rows
    (``card_summary.top5`` of kind ``best_settings``: each setting's label and value words), so its read answers
    "which settings hold up" beside its nearby numbers: "Best settings (of 25): hold 3 · lookback 28 at 1.37; hold 7
    · lookback 10 at 1.22; …", by the measure the map colours. None for any other kind, or before any setting ran."""
    card = env.get("card_summary") if isinstance(env.get("card_summary"), Mapping) else {}
    top = card.get("top5") if isinstance(card.get("top5"), Mapping) else {}
    if top.get("kind") != "best_settings":
        return None
    rows = [
        r
        for r in top.get("rows") or []
        if isinstance(r, Mapping) and r.get("label") and isinstance(r.get("value_words"), str)
    ]
    if not rows:
        return None
    total = top.get("total")
    of = f" (of {total:,})" if isinstance(total, int) and not isinstance(total, bool) else ""
    # AUDIT-WORDS M21: the measure by its served name when the card serves one ("…: Sharpe 1.38")
    named = top.get("measure_label")
    said = "; ".join(
        f"{r['label']}: {named} {r['value_words']}"
        if named
        else f"{r['label']} at {r['value_words']}"
        for r in rows
    )
    return f"Best settings{of}: {said}"


def _runs_line(env: Mapping[str, Any]) -> str | None:
    """``Runs: {counts.runs.words} · versions tried …``. The counts' words are
    keel-api's (``research.words.runs_words``, the one owner of count words:
    "9 runs, 0 reused, none failed"; a platform failure reads "couldn't run on
    Keel's side", rule 6), printed as served."""
    counts = env.get("counts") or {}
    runs = counts.get("runs") or {}
    said = runs.get("words") if isinstance(runs, Mapping) else None
    if not said:
        return None
    line = f"Runs: {said}"
    vt = counts.get("versions_tried") or {}
    if vt and vt.get("count") is not None:
        # AUDIT-WORDS M61: a count not known yet is left out, never "versions tried: unknown"
        said = f"{vt['count']:,}"
        # A version that counted no family serves ``family`` empty and ``count`` null: "versions tried: unknown",
        # never "versions tried for : unknown".
        # AGENT F1 (SERVE 63cc77014e): the family by its served name, never the ledger id ("fam:str_…").
        name = vt.get("family_name")
        family = f" for {name}" if name else ""
        line += f" · versions tried{family}: {said}"
        # EN-13 / D-382: Keel's share of the count and the family's later tries, each in keel-api's own words
        # (research.words.keel_tries_words / tried_more_since_words), printed as served; never folded into the count.
        extra = [
            w
            for w in (vt.get("keel_words"), vt.get("more_since_words"))
            if isinstance(w, str) and w
        ]
        if extra:
            line += f" ({', '.join(extra)})"
    return line


def _views_line(env: Mapping[str, Any], study_id: str | None) -> str | None:
    views = env.get("views")
    if not isinstance(views, list) or not views:
        return None
    titles = [str(v.get("title")) for v in views[:LIST_ROWS_MAX]]
    more = f" and {len(views) - LIST_ROWS_MAX} more" if len(views) > LIST_ROWS_MAX else ""
    call = next_call("keel_research_get", {**_target(env), "view": 0})
    # AUDIT-WORDS M23: no em dash in prose
    return Keep(
        f"Charts ({len(views)}): "
        + " · ".join(titles)
        + more
        + f". {call} reads the numbers behind any of them."
    )


#: 07 §4.4: a stale fact's offer is written as its call only while the call is short enough to read; the edit form's
#: ``source`` is the whole document (up to 131,072 bytes), so a longer offer points to the app's Update instead.
OFFER_CALL_MAX = 4_000


def _stale_line(fact: Mapping[str, Any]) -> str:
    """07 §3.1 (A3 #12): a stale fact's served words, then its offer's call ("an offer names the next call"):
    ``keel_research_plan`` with the offer's ``plan`` (the edit-form request keel-api built, its ``base`` included).
    An offer with no plan (a deleted subject), or one too long to write, says where the update is made."""
    words = str(fact.get("words", "") if isinstance(fact, Mapping) else "")
    offer = fact.get("offer") if isinstance(fact, Mapping) else None
    plan = offer.get("plan") if isinstance(offer, Mapping) else None
    if not isinstance(plan, Mapping):
        return words
    call = next_call("keel_research_plan", plan)
    if len(call) <= OFFER_CALL_MAX:
        return Keep(f"{words} Plan it: {call}")
    study_id = plan.get("study_id")
    where = next_call("keel_app_link", {"id": study_id}) if study_id else VIEW_IN_KEEL
    return Keep(f"{words} Plan it in the app: {where}")


_ROLE = {"agent": "your agent", "user": "you"}
#: AUDIT-WORDS M12: a note's kind as the app names it (``NoteKind``: keel-api schemas/research.py), never its key.
NOTE_KIND_WORDS = {
    "hypothesis": "Hypothesis",
    "note": "Note",
    "decision": "Decision",
    "set_aside": "Set aside",
    "restored": "Brought back",
    "agent_session": "Agent session",
}


def note_kind_words(kind: Any) -> str:
    """M12: a note kind in words. A kind added after this table reads as a sentence ("New kind"), never its key;
    ``test_research_view`` holds the table to keel-api's ``NoteKind``."""
    said = NOTE_KIND_WORDS.get(str(kind))
    if said:
        return said
    text = str(kind or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _notes_lines(env: Mapping[str, Any]) -> list[str]:
    """07 §4.4 (A3 #12): the latest notes the read serves (at most 5, newest first, each body at most 200
    characters as served), with the whole count."""
    notes = [n for n in env.get("notes") or [] if isinstance(n, Mapping)]
    if not notes:
        return []
    total = int(env.get("notes_total") or len(notes))
    out = [f"Notes ({len(notes)} shown of {total}, newest first):"]
    for n in notes:
        who = _ROLE.get(str(n.get("author_role")), str(n.get("author_role") or ""))
        line = f"- {note_kind_words(n.get('kind'))} ({who}, {_day(n.get('date'))}): {_quote(n.get('body'))}"
        length, body = n.get("length"), str(n.get("body") or "")
        if n.get("truncated") and isinstance(length, int):
            # SERVE 3fbd0571b9: a cut body says so with its whole length, as the brief's does (a cut is never read as
            # the whole note).
            line += f" (the first {len(body.rstrip('…')):,} of {length:,} characters)"
        out.append(line)
    return out


def _linked_to_lines(study: Mapping[str, Any]) -> list[str]:
    """D-176 (A3 #12): the newer studies linked from this one, in their served words, each with its id for the next
    read; the served "And N more linked studies" when the list is not whole."""
    out = []
    for row in study.get("linked_to") or []:
        if isinstance(row, Mapping) and row.get("study_id"):
            out.append(f"{row.get('words')} ({row['study_id']} v{row.get('version')})")
    if study.get("linked_to_more"):
        out.append(str(study["linked_to_more"]))
    return out


def _draft_run_call(env: Mapping[str, Any], study_id: str) -> str:
    """D-341 (3): a never-run Draft's next call, with no new tool: plan the Draft, then run that plan. With the
    Draft's live plan served (``draft.plan_id``, REQUEST to SERVE), re-plan it by ``from_plan``; else the plan tool's
    draft form, ``study_id`` with ``version`` alone (``research_plan.draft_body`` sends the Draft's own source and
    base), so the call is short whatever the Draft's size and needs no source the caller cannot see."""
    draft = (env.get("study") or {}).get("draft") or {}
    # D-344 (5), R-L4: stated in the indicative, never as an instruction to spend.
    then = "plans its run; keel_research_run runs that plan."
    if isinstance(draft.get("plan_id"), str) and draft["plan_id"]:
        return f"{next_call('keel_research_plan', {'from_plan': draft['plan_id']})} {then}"
    return f"{next_call('keel_research_plan', {'study_id': study_id, 'version': draft.get('version')})} {then}"


#: A strategy version in the form a plan's ``subjects`` take, for a call the reader fills in.
SUBJECT_PLACEHOLDER = 'Strategy("str_…@3")'
#: AUDIT S3 #6: a signal (a pipeline output) in the same form, for a Signal or signal Compare share's "Run on yours".
SIGNAL_PLACEHOLDER = 'Output("str_…@1", output="…")'


def _share_run_subjects(env: Mapping[str, Any]) -> tuple[list[str], str]:
    """AUDIT S3 #6: the subjects "Run on yours" fills in, by the share's own subjects and kind, and what they are: a
    Signal share's subject is a signal, so its call names an ``Output(…)``, never a strategy; a Compare runs two to
    eight, so its call names two (the share's own subject kind) and says so. A derived row (a mix the share made) is
    the study's own and never a subject to fill."""
    kinds = [
        str(s.get("kind"))
        for s in env.get("subjects") or []
        if isinstance(s, Mapping) and s.get("kind") in ("strategy", "output")
    ]
    signal = (kinds[0] if kinds else None) == "output" or (
        not kinds and ((env.get("study") or {}).get("kind_label") or {}).get("key") == "signal"
    )
    one, noun, many = (
        (SIGNAL_PLACEHOLDER, "signal", "signals")
        if signal
        else (SUBJECT_PLACEHOLDER, "strategy", "strategies")
    )
    if ((env.get("study") or {}).get("kind_label") or {}).get("key") == "compare":
        return [one, one], f"your own {many} (two to eight)"
    return [one], f"your own {noun}"


def _share_next_lines(env: Mapping[str, Any]) -> list[str]:
    """07 §4.4 (A3 #4, AT-29): a share read offers both of 08 §3.8.1's actions. "Run on yours" is the plan's From
    share form (listed); "Make it yours", a copy with nothing run, is the CLI's ``keel research copy`` and the app's,
    so the generated call is ``keel_app_link`` to the snapshot, with the CLI's command beside it."""
    share_id = (env.get("study") or {}).get("share_id")
    if not share_id:
        return []
    # STG-SURFACES #6 (S3): the subjects ride in the call itself, so the CLI writes one command
    # (``keel research plan --share-id … --subjects '…'``), never a CLI command beside an MCP argument
    subjects, yours = _share_run_subjects(env)
    run = next_call("keel_research_plan", {"share_id": share_id, "subjects": subjects})
    make = next_call("keel_app_link", {"id": share_id})
    return [
        Keep(f"Run on yours: {run} runs it on {yours}"),
        Keep(
            f"Make it yours, a copy with nothing run: {make} (the CLI: keel research copy {share_id})"
        ),
    ]


def study_text(env: Mapping[str, Any]) -> str:
    """A study read's text block (07 §3.2, §4.4)."""
    study = env.get("study") or {}
    study_id = study.get("study_id")
    headline = env.get("headline") or {}
    lines: list[str] = [_first_line(env)]
    # D-125: the served study count leads, as on a plan (words.py's agent_study_status; null on the app).
    allowance = (env.get("words") or {}).get("allowance")
    if allowance:
        lines.append(str(allowance))
    if headline.get("sentence"):
        lines.append(str(headline["sentence"]))
    lines.append(footing_words(env.get("footing")) or "")
    lines.extend(_headline_lines(env))
    lines.append(_best_settings_line(env) or "")
    # 06 §4.10, RL-10: a what-if step (a strategy blend) says so, in its served words.
    for step in env.get("steps") or []:
        if isinstance(step, Mapping) and step.get("what_if"):
            lines.append(f"{step.get('label')}: {step['what_if']}")
    lines.extend(_bars_lines(env, study_id))
    # 07 §3.2 rule 6 (D-130): a step that ended on its content gives its served reason, word for word.
    for step in env.get("steps") or []:
        outcome = step.get("outcome") if isinstance(step, Mapping) else None
        if (
            isinstance(outcome, Mapping)
            and outcome.get("kind") == "content"
            and outcome.get("reason")
        ):
            lines.append(f"{step.get('label')}: {outcome['reason']}")
    not_measured = env.get("not_measured") or []
    if not_measured:
        lines.append("Not measured: " + "; ".join(str(x) for x in not_measured) + ".")
    progress = env.get("progress")
    said = _progress_said(progress) if isinstance(progress, Mapping) else ""
    if progress and study.get("state") in ("running", "cancel_requested", "queued"):
        # 07 §3.3 (in flight): the served progress line leads, then the call that reads it as it fills in.
        # AUDIT-WORDS M2: the first line already says the tally ("Running · 9 of 26 runs"), so only the time and
        # queue words follow, never "Running · 9 of 26 runs · Running · 9 of 26 runs done"
        if _state_words(env) and _state_words(env) != STATE_WORDS.get(str(study.get("state"))):
            said = " · ".join(str(progress[k]) for k in ("time_words", "queue") if progress.get(k))
        lines[0] = f"{lines[0]} · {said}" if said else lines[0]
        if study_id:
            lines.append(
                Keep(
                    next_call("keel_research_get", {"study_id": study_id})
                    + " reads it as it fills in."
                )
            )
    elif progress:
        lines.append(_settled_said(env, lines[0], said))
    if not study.get("share_id"):
        # A share read serves the snapshot, not this reader's runs: its counts read "0 runs" for a study that ran
        # 24 (the card's footer carries the snapshot's own runs words).
        lines.append(_runs_line(env) or "")
    lines.append(_views_line(env, study_id) or "")
    # JOURNEYS J5/J6 S3: the step versions are a details line, after the numbers and charts, never between the
    # footing and the result ("Measured with Backtest v1, CostStress v1, …" led every read)
    lines.append(_method_line(env) or "")
    lines.extend(_notes_lines(env))
    lines.extend(_linked_to_lines(study))
    for fact in env.get("stale") or []:
        lines.append(_stale_line(fact))
    draft = study.get("draft")
    if draft:
        line = f"Version {draft.get('version')} is a saved Draft that has not run yet."
        # AUDIT S3 #4 (D-329, one served string per fact): the Draft's served run words ("24 runs; the rest reused")
        # and, when it cannot run as it stands, its served reason (research.words' newer_data_draft), with no
        # run call (its Run would refuse stale; the stale line carries the offer)
        if isinstance(draft.get("words"), str) and draft["words"].strip():
            line = f"{line[:-1]}: {draft['words'].strip().rstrip('.')}."
        cannot = draft.get("can_run") is False
        if cannot and isinstance(draft.get("reason"), str) and draft["reason"].strip():
            line = f"{line} {draft['reason'].strip()}"
        if (
            study_id
            and draft.get("version") is not None
            and draft.get("version") != study.get("version")
        ):
            # RI-483: this read is an older version than the Draft head; an edit is made on the Draft's own read.
            line += " Edit it on its own read: " + next_call(
                "keel_research_get", {"study_id": study_id, "version": draft.get("version")}
            )
            line = Keep(line)
        elif study_id and draft.get("version") == study.get("version") and not cannot:
            line = Keep(f"{line} {_draft_run_call(env, study_id)}")
        lines.append(line)
    lines.extend(_share_next_lines(env))
    return _cap(lines, f"View in Keel: {(env.get('links') or {}).get('app', '')}")


# ── A plan (07 §4.1) ──────────────────────────────────────────────────────────


def _subject_name(env: Mapping[str, Any], key: str) -> str:
    for subject in env.get("subjects") or []:
        if isinstance(subject, Mapping) and subject.get("key") == key:
            return str(subject.get("name") or subject.get("label") or subject.get("ref") or key)
    return key


def _added_lead(env: Mapping[str, Any]) -> str | None:
    """07 §4.2 (A3 #12): the add form's answer leads with what is added and at which version: "Adds Momentum
    rotation v4 to Momentum rotation v3 · version 2". ``added[].ref`` is the subject id each ref was added as, named
    by the plan's own ``subjects`` (06 §12 rule 2: the name, never the typed ref). Each added subject's cost and each
    ref the study could not take are already in the served work line, so they are not said again."""
    added = [a for a in env.get("added") or [] if isinstance(a, Mapping)]
    if not added:
        return None
    names = ", ".join(_subject_name(env, str(a.get("ref"))) for a in added)
    title = (env.get("study") or {}).get("title", "")
    return f"Adds {names} to {title} · {env.get('writes', '')}"


def _progress_said(progress: Mapping[str, Any]) -> str:
    """D-359 (5) (b), D-349: the run's progress in its served strings, never recomposed. The receipt (``progress.receipt``,
    the one line the app's receipt and chip print) when served, plus the progress words when the receipt does not
    already carry them ("Stopped at 12 of 24 runs" beside "Stopped. 12 runs done; nothing is charged for the rest.");
    with no receipt, the words, then the time words or the queue words."""
    receipt = str(progress.get("receipt") or "")
    words = str(progress.get("words") or "")
    if receipt:
        return receipt if not words or words in receipt else f"{words} · {receipt}"
    rest = [str(progress[k]) for k in ("time_words", "queue") if progress.get(k)]
    return " · ".join(x for x in [words, *rest] if x)


def _settled_said(env: Mapping[str, Any], first: str, said: str) -> str:
    """MCP-FINAL S3 / SERVER-5 (POLISH): a read whose run ended short says the stop once. The first line already
    carries the tally ("Stopped at 15 of 48 settings"); below it the served reason, ``words.stopped`` ("Keel's engine
    was updated during this run, so the rest of it didn't run. Resume sets it up again on the updated engine."),
    which the text never printed, else the progress line only when the first line does not carry it already."""
    progress = env.get("progress") or {}
    reason = (env.get("words") or {}).get("stopped")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    tally = str(progress.get("words") or "")
    # AUDIT-WORDS M3 is declined: the surface parity tree holds every served progress string to the agent byte for
    # byte ("Done · 22 of 24 runs finished", "Replaced by a newer run"), so the receipt stays below the first line
    return "" if tally and tally in first else said


def plan_text(env: Mapping[str, Any]) -> str:
    """A plan's text block: the served words lead; nothing about runs or time is composed."""
    study = env.get("study") or {}
    kind = (study.get("kind_label") or {}).get("name", "Study")
    words = env.get("words") or {}
    lines = [
        _added_lead(env) or f"Plan · {kind} · {study.get('title', '')} · {env.get('writes', '')}",
        str(words.get("work", "")),
    ]
    # D-359 (5) (b), D-329: every served plan string is printed as served, once: the estimate's honesty note and an
    # edit's "Also changes …" (the surface parity tree's plan_ready and refine rows).
    # SERVER-2: and the served engine note ("Keel's engine changed since version 1: 15 runs"), which said why runs
    # were not reused and was never printed.
    # AUDIT-WORDS M11: and an edit's served "linked" words (changing the dates starts a new linked study)
    for key in ("engine", "estimate", "also_changes", "linked"):
        if words.get(key):
            lines.append(str(words[key]))
    # SDK-FIX (JOURNEYS J6 w14): a plan Run refuses prints its served wall once, below; a map over the bound read
    # its size five times (each locked step's words on the Steps line, the issue, then the wall).
    walled = not env.get("run_allowed") and bool(words.get("limit"))
    studies_wall = walled and any(
        i.get("code") == "STUDY_PLAN_STUDIES_LIMIT" for i in env.get("issues") or []
    )
    if words.get("allowance") and not studies_wall:
        # AUDIT-WORDS M10: behind the studies wall the wall states the count ("You have used 3 of 3 studies"); said
        # once. Behind any other wall the count stays (D-395: usage at the limit wall).
        lines.append(str(words["allowance"]))
    if words.get("queue"):
        lines.append(str(words["queue"]))
    lines.append(footing_words(env.get("footing")) or "")
    steps = env.get("steps") or []
    # RI-263: a step the Steps line names as not included is said there once, never again as its issue line.
    said_locked = {
        f"/steps/{s.get('id')}" for s in steps[:40] if not s.get("runnable", True) and s.get("id")
    }
    if steps:
        parts = []
        # SDK-FIX (J5): each lock clause once for every step it locks, at the first one's place, by clause
        groups: dict[str, tuple[int, list[str]]] = {}
        for s in steps[:40]:
            if not s.get("runnable", True):
                # SERVER-2: a locked step reads its served words (``steps[].locked.words``: they carry the size, "the
                # map it reads has 36 settings; the most this account's plan can run is 25"); with none served, the
                # bare reason.
                lock = s.get("locked") if isinstance(s.get("locked"), Mapping) else {}
                said = _clause(lock.get("words"))
                # D-422 (value-and-pricing spec 02 §C3.1): an agent row names the plans that include it ("…: included
                # on Trader, Quant and Pro", ``locked.more`` the names). When the plan serves its runs-without line
                # (below), that line names them once, so the Steps line keeps the plain clause.
                names = str(lock.get("more") or "")
                named = f": included on {names}" if names else ""
                if named and said.endswith(named) and words.get("runs_without"):
                    said = said[: -len(named)] + f": {NOT_INCLUDED}"
                    named = ""
                tail = named or f": {NOT_INCLUDED}"
                # AUDIT-WORDS M10: a lock on the plan's backtest bound is sized whether or not its counts are served
                sized = (
                    lock.get("used") is not None
                    or lock.get("reads") is not None
                    or lock.get("unit") == "research_backtests_max"
                )
                if sized and walled:
                    parts.append(f"{s.get('label')}: too many settings (below)")
                elif sized or (said and not said.endswith(tail)):
                    parts.append(said or f"{s.get('label')}{tail}")
                else:
                    # "Every setting, Nearby settings: not included in this account's plan"
                    if tail not in groups:
                        parts.append("")
                        groups[tail] = (len(parts) - 1, [])
                    # SERVER-2: the label its served words name ("Re-picking settings: not included …")
                    groups[tail][1].append(
                        said[: -len(tail)] if said.endswith(tail) else str(s.get("label"))
                    )
            elif s.get("to_run"):
                # AUDIT-WORDS M9: "The backtest: 1 run", never "The backtest 1"
                n = s.get("to_run")
                ran = count_words(n, "run") if isinstance(n, int) and not isinstance(n, bool) else n
                parts.append(f"{s.get('label')}: {ran}")
            else:
                parts.append(f"{s.get('label')}: reused")
        for tail, (at, labels) in groups.items():
            parts[at] = ", ".join(labels) + tail
        lines.append(f"Steps ({len(steps)}): " + " · ".join(parts))
    if words.get("runs_without"):
        # Value-and-pricing spec 02 §C4.8 / §D (RB D-198, D-422): what this plan runs without and the plans that
        # include it, as served, once
        lines.append(str(words["runs_without"]))
    issues = [
        i
        for i in env.get("issues") or []
        if not (i.get("code") == "STUDY_PLAN_STEP_NOT_INCLUDED" and i.get("path") in said_locked)
        # M10: the wall below states the limit once (a map's size, the studies count)
        and not (
            walled
            and i.get("code") in ("STUDY_PLAN_TOO_MANY_BACKTESTS", "STUDY_PLAN_STUDIES_LIMIT")
        )
    ]
    lines.extend(
        _once([str(i.get("message", "")) for i in issues], [i.get("path") for i in issues])
    )
    if env.get("run_allowed"):
        # D-344 (5), R-L4 (CONFORM-MCP F7): a result never instructs a quota-spending call; it states the call as a
        # fact. The count is the served allowance line above, byte for byte (D-329, the surface parity tree: a
        # re-cased "Running it used 1 of 3 …" was a second rendering of the one served string).
        lines.append(
            Keep(
                f"{next_call('keel_research_run', {'plan_id': env.get('plan_id')})} runs this plan."
            )
        )
    elif words.get("limit"):
        # The served neutral wall (07 §3.4) replaces the Run line; it already carries every locked step's words.
        lines.append(Keep(words["limit"]))
    else:
        # RI-278: with no served wall, a locked step's own words are said only when the Steps line has not already
        # named it as not included (RI-263's rule: said there once); a locked step past the line's 40 keeps them.
        for item in steps:
            locked = item.get("locked")
            if locked and locked.get("words") and f"/steps/{item.get('id')}" not in said_locked:
                lines.append(str(locked["words"]))
    return _cap(lines, f"View in Keel: {(env.get('links') or {}).get('app', '')}")


# ── A project (08 §1.2, D-177) ────────────────────────────────────────────────


def project_text(env: Mapping[str, Any], *, app_url: str = "") -> str:
    """D-177's project read: its name, its question, and each item by kind, name and id (served words for an item
    that is no longer available), then the call that opens a study and "View in Keel". ``keel_research_get`` with
    ``project_id`` (D-189: a read is read-scoped). A3 #3: RI-593 cut structuredContent ``items`` to a count because
    the text names each one, and the text dropped the ids, so on MCP no item could be opened."""
    lines = [f"Project · {env.get('name') or ''} · {env.get('project_id') or ''}"]
    if env.get("question"):
        lines.append(str(env["question"]))
    items = [i for i in env.get("items") or [] if isinstance(i, Mapping)]
    total = int(env.get("items_total", len(items)))
    shown_items = items[:LIST_ROWS_MAX]
    lines.append(f"Items: {len(shown_items)} shown of {total}")
    for item in shown_items:
        shown = item.get("name") if item.get("available", True) else item.get("words")
        lines.append(f"- {item.get('ref_kind')} · {shown or item.get('ref')} · {item.get('ref')}")
    if total > len(shown_items):
        lines.append(Keep(f"and {total - len(shown_items)} more items: {VIEW_IN_KEEL} lists them"))
    study = next(
        (
            i.get("ref")
            for i in items
            if i.get("ref_kind") == "study" and i.get("available", True) and i.get("ref")
        ),
        None,
    )
    if study:
        lines.append(Keep("Open a study: " + next_call("keel_research_get", {"study_id": study})))
    return _cap(lines, f"View in Keel: {app_url}")


# ── One chart's rows (07 §3.5, §4.5) ──────────────────────────────────────────


def data_text(
    page: Mapping[str, Any], *, title: str, version: int | None, call_args: Mapping[str, Any]
) -> str:
    """Every row the page returns, the served facts, and the call that reads on. A SameFooting table's rows read in
    its served words (:func:`_served_cell`, D-252) and its difference sentences follow them (RI-354); an axis column's
    header reads its served label beside the key a ``where`` takes (:func:`_header`, RI-361)."""
    rows = list(page.get("data") or [])
    facts = page.get("facts") or {}
    total = int(facts.get("rows_total", len(rows)))
    # A share read serves no version (the snapshot's own): its chart is named alone.
    lines = [f"{title} (version {version})" if version is not None else title]
    cells = dict(facts.get("cells") or {})
    filling = page.get("filling") or {}
    if filling.get("words"):
        # D-158 (05 §6): a map filling in says how many settings are in; the rest are the not-run (hatched) rows.
        lines.append(str(filling["words"]))
        if filling.get("state") == "filling":
            cells.pop("not_run", None)
    cell_words = ""
    if cells:
        # AUDIT-WORDS M17: each count in words ("384 measured, 2 not run"), never the state key
        cell_words = (
            " (" + ", ".join(f"{n:,} {cell_state_words(k)}" for k, n in cells.items() if n) + ")"
        )
    lines.append(
        f"Rows 1–{len(rows)} of {total:,}{cell_words}"
        if rows
        # M18: "No rows yet (775 in all)", never "No rows yet of 775"
        else f"No rows yet ({total:,} in all){cell_words}"
    )
    worded = facts.get("footing_cells") or {}
    labels = {
        str(m.get("name")): str(m["label"])
        for m in facts.get("measures") or []
        if isinstance(m, Mapping) and m.get("label")
    }
    if rows and _is_checks_page(rows, facts):
        # STG-SURFACES #3 (WL-AG-12, D9): a Checks view's rows are the study read's check rows; each reads as the
        # study read's bar line (:func:`bar_row`, every number in its served words), never the row's internal
        # columns (bar_index, withheld_reason, stricter, looser), as the app's checks table draws them.
        locked = {str(r.get("ref") or "").split(".", 1)[0] for r in rows if r.get("locked")}
        lines.extend(bar_row(r, locked) for r in rows)
        rows = []
    if rows:
        lines.extend(_table_lines(rows, facts, worded))
    for name, rng in (facts.get("ranges") or {}).items():
        low, high = rng.get("low") or {}, rng.get("high") or {}
        # STG-SURFACES #3: each end in its served words (page_facts._ranges), the measure by its served label.
        # MCP-FINAL batch 3 #4: the label only ("Sharpe", never "Sharpe (sharpe)"); the table's header row above names
        # the key a `where` takes, once.
        label = labels.get(str(name)) or column_words(str(name), {}, labels)
        ends = (f"{_end_words(end)}{_end_at(end, facts)}" for end in (low, high))
        line = f"Across all {total:,} rows: {label} " + " to ".join(ends)
        if rng.get("below_zero") is not None:
            line += f"; {rng['below_zero']} below zero"
        lines.append(line)
    lines.extend(_view_fact_lines(facts))
    pagination = page.get("pagination") or {}
    if pagination.get("has_more") and pagination.get("cursor"):
        lines.append(
            "More: " + next_call("keel_research_get", {**call_args, "cursor": pagination["cursor"]})
        )
    return "\n".join(line for line in lines if line)


#: AUDIT-WORDS M14/M17: a row's or a map cell's run state in words, the one set of four every surface says
#: (``research.words.CELL_WORDS``, keel-app ``lib/research/evidence-words.ts``): measured, not run, didn't finish on
#: our side, couldn't be measured; never "computed" or "not_run".
CELL_STATE_WORDS = {
    "computed": "measured",
    "not_run": "not run",
    "cancelled": "not run",
    "running": "running",
    "failed_platform": "didn't finish on our side",
    "failed_content": "couldn't be measured",
    "not_valid": "not a valid setting",
}


def cell_state_words(state: Any) -> str:
    """A cell state in words (:data:`CELL_STATE_WORDS`); a state added after the table reads as a phrase."""
    return CELL_STATE_WORDS.get(str(state)) or str(state or "").replace("_", " ")


#: AUDIT-WORDS M13: the columns a data row carries for the engine or a link, never a reader's column: ``cell`` (the
#: setting phrase the axis columns already say), ``b`` (the bootstrap's draw count), ``grain`` (the time axis's
#: step, already in its day or month words), ``bar_index``. ``status`` reads as a "State" column only when a row is
#: not done (:func:`_table_lines`); the ``*_lo`` / ``*_hi`` ends fold into their measure and ``*_withheld`` tokens
#: are left out (the interval lines below the table say why an interval is missing).
ROW_BOOKKEEPING = frozenset({"cell", "b", "grain", "bar_index", "is_selected_cell"})

#: M13: a column keel-api serves no label for, by what it holds (the bookkeeping columns every step's rows share).
#: A served axis or measure label always wins (:func:`column_words`).
COLUMN_WORDS = {
    "result": "Result",
    "status": "State",
    "days": "Days",
    "effective_days": "Effective days",
    "shared_days": "Shared days",
    "skill_days": "Days with both skills",
    "flat_days": "Flat days",
    "too_few_coins": "Days with too few coins",
    "coins": "Coins",
    "coins_min": "Fewest coins",
    "coins_max": "Most coins",
    "coins_median": "Median coins",
    "coverage": "Coverage",
    "first_day": "First day",
    "last_day": "Last day",
    "trades": "Trades",
    "price": "Price",
    "fees": "Fees",
    "slippage": "Slippage",
    "funding": "Funding",
    "wiped_out_on": "Wiped out on",
    "window": "Window",
}

#: M13 (AUDIT-NUMBERS extras): a split table's window values, as the split's own words say them.
WINDOW_WORDS = {"whole": "Whole window", "before": "Before the split", "after": "After the split"}
_SPLIT_SIDES = (("_before", "before the split"), ("_after", "after the split"))


def column_words(
    column: str,
    axes: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, str],
) -> str:
    """M13: a column's name for the reader: the served axis or measure label; a measure's side of the split by its
    measure's label ("Rank IC before the split" for ``ic_before``); else :data:`COLUMN_WORDS`; else the key as a
    phrase ("Days before" for ``days_before``), never snake case."""
    label = str((axes.get(column) or {}).get("label") or labels.get(column) or "").strip()
    if label:
        return label
    for suffix, words in _SPLIT_SIDES:
        if column.endswith(suffix):
            base = column[: -len(suffix)]
            named = str(
                (axes.get(base) or {}).get("label")
                or labels.get(base)
                or COLUMN_WORDS.get(base)
                or ""
            )
            if named:
                return f"{named} {words}"
    if column in COLUMN_WORDS:
        return COLUMN_WORDS[column]
    text = column.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _interval_base(column: str, present: set[str]) -> str | None:
    """The measure a ``<measure>_lo`` / ``<measure>_hi`` column is an end of, when that measure is on the page."""
    for suffix in ("_lo", "_hi"):
        if column.endswith(suffix) and column[: -len(suffix)] in present:
            return column[: -len(suffix)]
    return None


def _number_words(row: Mapping[str, Any], column: str) -> str | None:
    """A number cell's served words (``row.words``; the SDK asks every page worded), else the value rounded for
    reading (:func:`_plain_text`); None for a value nothing measured."""
    said = row.get("words")
    if isinstance(said, Mapping) and isinstance(said.get(column), str) and said[column]:
        return None if said[column] == NOT_MEASURED_CELL else str(said[column])
    value = row.get(column)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _plain_text(value)


def _table_lines(
    rows: Sequence[Mapping[str, Any]], facts: Mapping[str, Any], worded: Mapping[str, Any]
) -> list[str]:
    """AUDIT-WORDS M13/M14 (one formatter for every data table, MCP and CLI): the reader's columns only, each named
    in words (:func:`column_words`) and each cell in its served words. A measure's interval ends fold into its cell
    ("1.36 (−0.048 to 2.62)"); ``*_withheld`` tokens, bookkeeping columns and a single-valued Result are left out;
    a row's run state is a "State" column in words only when some row is not done; a column every row leaves
    unmeasured is named once below the table, and a "Wiped out on" no row reached reads "No row was wiped out."."""
    # A row's served words and link are read with its cells, never columns of their own. STG-SURFACES #3: a
    # column no row serves a plain value in (a nested record) is internal, never drawn, as the app's table.
    cols = [
        c
        for c in rows[0].keys()
        if c not in ("words", "link")
        and any(not isinstance(r.get(c), (Mapping, list, tuple)) for r in rows)
    ]
    axes = {str(a.get("name")): a for a in facts.get("axes") or [] if isinstance(a, Mapping)}
    measures = [m for m in facts.get("measures") or [] if isinstance(m, Mapping)]
    labels = {str(m.get("name")): str(m["label"]) for m in measures if m.get("label")}
    bases = {str(m.get("name")): str(m["basis"]) for m in measures if m.get("basis")}
    present = set(cols)
    folded = {c: b for c in cols if (b := _interval_base(c, present))}
    hidden = (
        set(folded) | {c for c in cols if c.endswith("_withheld")} | (ROW_BOOKKEEPING & present)
    )
    if "status" in present and all(r.get("status") in (None, "computed") for r in rows):
        hidden.add("status")
    if (
        "result" in present
        and "result" not in axes
        and len({str(r.get("result")) for r in rows}) == 1
    ):
        # one result on the page: the chart's title names it
        hidden.add("result")
    cols = [c for c in cols if c not in hidden]
    ends = {b: (f"{b}_lo", f"{b}_hi") for b in set(folded.values())}

    def cell(r: Mapping[str, Any], c: str) -> str:
        if c == "status":
            return cell_state_words(r.get("status"))
        if c == "window" and str(r.get(c)) in WINDOW_WORDS:
            return WINDOW_WORDS[str(r.get(c))]
        if c == "wiped_out_on" and r.get(c) is None:
            return "never"
        said = _served_cell(r, c, worded)
        if c in ends and said != NOT_MEASURED_CELL:
            lo, hi = (_number_words(r, e) for e in ends[c])
            if lo is not None and hi is not None:
                said = f"{said} ({lo} to {hi})"
        return said

    out: list[str] = []
    # MCP-FINAL S3 (POLISH): a column no row on the page measured ("not measured" in every cell, a done map's
    # Turnover) is left out of the table and named once below it
    unmeasured = [
        c
        for c in cols
        if c != "wiped_out_on"
        and all(_served_cell(r, c, worded) == NOT_MEASURED_CELL for r in rows)
    ]
    never_wiped = "wiped_out_on" in cols and all(r.get("wiped_out_on") is None for r in rows)
    cols = [c for c in cols if c not in unmeasured and not (never_wiped and c == "wiped_out_on")]
    heads = [_header(c, axes, bases, labels) for c in cols]
    # AUDIT-NUMBERS S2-k: a Distribution's bin ends read as the binned measure's range ("Rank IC from",
    # "Rank IC to"), never the keys bin_lo / bin_hi; their cells are worded by keel-api in that measure's unit
    binned = str((facts.get("bins") or {}).get("label") or "").strip()
    if binned:
        bin_ends = {"bin_lo": f"{binned} from", "bin_hi": f"{binned} to"}
        heads = [bin_ends.get(c, h) for c, h in zip(cols, heads, strict=True)]
    out.append("| " + " | ".join(heads) + " |")
    out.append("|" + "---|" * len(cols))
    for r in rows:
        out.append("| " + " | ".join(cell(r, c) for c in cols) + " |")
    if unmeasured:
        out.append(
            "Not measured on these rows: "
            + ", ".join(_header(c, axes, bases, labels) for c in unmeasured)
            + "."
        )
    if never_wiped:
        out.append("No row was wiped out.")
    keyed = [f"{h} = {c}" for h, c in zip(heads, cols, strict=True) if h != c]
    if keyed:
        # MCP-FINAL S3 (POLISH): the headers read as words; the keys `where`, `sort` and `measures` take are
        # said once, under the table ("Return (ret)" in every header read as noise)
        out.append("Keys for where, sort and measures: " + ", ".join(keyed) + ".")
    return out


def _is_checks_page(rows: Sequence[Mapping[str, Any]], facts: Mapping[str, Any]) -> bool:
    """A Checks view's page (``research.page_facts.checks_read``): its one axis is ``check`` and its rows are the study
    read's check rows (each serves ``bar_index`` and its ``value_words``)."""
    axes = [a.get("name") for a in facts.get("axes") or [] if isinstance(a, Mapping)]
    return axes == ["check"] and all("bar_index" in r and "value_words" in r for r in rows)


def _end_at(end: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    """AUDIT S3 #11 (07 §3.5, §4.5): where a range end is, from its served ``at`` (``page_facts._ranges``: each
    axis's value at that row), each axis by its served label: " (Day 2025-03-28)", " (Hold 28, Lookback 12)". Empty
    for an end served with no ``at``."""
    at = end.get("at")
    if not isinstance(at, Mapping) or not at:
        return ""
    axes = {str(a.get("name")): a for a in facts.get("axes") or [] if isinstance(a, Mapping)}
    # AUDIT-NUMBERS S2-k / M16: a bin end by the binned measure, as the table's header says it ("Sharpe from −0.37")
    binned = str((facts.get("bins") or {}).get("label") or "").strip()
    named = {"bin_lo": f"{binned} from", "bin_hi": f"{binned} to"} if binned else {}
    return (
        " ("
        + ", ".join(
            f"{named.get(str(k)) or column_words(str(k), axes, {})} {_axis_value_words(axes.get(str(k)), v)}"
            for k, v in at.items()
        )
        + ")"
    )


def _axis_value_words(axis: Mapping[str, Any] | None, value: Any) -> str:
    """AUDIT-WORDS M15/M16: one axis value as the reader sees it: the axis's served value words (a variant arm, a
    month: ``facts.axes[].value_words``), a day in words, a fractional number at 3 significant figures ("Bin −0.0244",
    never "Bin -0.024359925788497216")."""
    said = (axis or {}).get("value_words")
    if isinstance(said, Mapping) and isinstance(said.get(_x_text(value)), str):
        return str(said[_x_text(value)])
    if isinstance(value, float) and not value.is_integer() and math.isfinite(value):
        return format(value, ".3g").replace("-", "\u2212")
    return _plain_text(value)


def _end_words(end: Mapping[str, Any]) -> str:
    """A range end as served: its ``words`` (``page_facts._ranges``); a share frozen before them reads its value."""
    said = end.get("words")
    return str(said) if isinstance(said, str) and said else _plain_text(end.get("value"))


def _plain_text(value: Any) -> str:
    """A cell no words are served for, as text, never Python's repr: "—" for null, a whole float as its whole number
    (an axis's 10.0 is "10"), a record as compact JSON. MCP-FINAL S3 (POLISH): a day in words ("Nov 19, 2025", as the
    marks say it), never ISO ("2025-11-19T00:00:00"); a time of day that is not midnight keeps it ("14:00 UTC")."""
    if value is None:
        return "\u2014"
    if isinstance(value, str):
        said = _day_words(value)
        if said is not None:
            return said
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float) and math.isfinite(value):
        return _float_words(value)
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def _float_words(value: float) -> str:
    """PRODUCT-READY-RUBRIC §1 (no unrounded floats): a number served with no words and no unit, rounded for reading
    ("0.0244", "1.17", "63,422"), with a true minus sign; a measure in its unit reads through :func:`_value`."""
    a = abs(value)
    text = f"{value:,.0f}" if a >= 1000 else format(value, ".3g")
    return text.replace("-", "\u2212")


#: The served words of a cell nothing measured (research.words ``NOT_MEASURED_WORDS``).
NOT_MEASURED_CELL = "not measured"


def _header(
    column: str,
    axes: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
) -> str:
    """RI-361: a column's header as the agent reads it. An axis or measure column reads its served label
    (``facts.axes[].label``, research.words.axis_words: "Result", "Days late"; ``facts.measures[].label``), the words
    the app draws. MCP-FINAL S3 (POLISH): the label alone ("Return", never "Return (ret)"); :func:`data_text` says
    each key a ``where`` takes once, under the table. D-315 (RI-568): a money measure reads its served basis
    ("Return before fees and funding", or "gross_return (before fees and funding)" with no label), as the app's CSV
    header does. A column with no served label reads its key as served."""
    label = column_words(column, axes, labels or {})
    basis = (bases or {}).get(column)
    if basis:
        return label if basis in label else f"{label} ({basis})"
    return label


_ISO_DAY = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]00:?00)?$"
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day_words(text: str) -> str | None:
    """An ISO day or a UTC midnight as "Nov 19, 2025"; a UTC time of day as "Nov 19, 2025 14:00 UTC"; None for any
    other string (an id, a name), which reads as served."""
    m = _ISO_DAY.match(text)
    if m is None:
        return None
    year, month, day, hour, minute, second = m.groups()
    if not 1 <= int(month) <= 12:
        return None
    said = f"{_MONTHS[int(month) - 1]} {int(day)}, {year}"
    if hour is not None and (int(hour), int(minute), int(second or 0)) != (0, 0, 0):
        said += f" {hour}:{minute} UTC"
    return said


def _served_cell(row: Mapping[str, Any], column: str, worded: Mapping[str, Any]) -> str:
    """D-252 (RI-354): one cell as the agent reads it. A SameFooting table's rows keep its stored field keys and raw
    values (``fee``, ``0.00045``); ``facts.footing_cells`` serves what they read as, the words the app draws ("Fee",
    "0.045%", "not recorded"), "—" for a value it does not word, never the key. Any other cell as served; a null one
    (a withheld value, ``ret`` on a trades page) reads "—", never Python's "None"."""
    axis, measure = worded.get("axis"), worded.get("measure")
    if worded and column == axis:
        return str((worded.get("labels") or {}).get(str(row.get(axis)), "—"))
    if worded and column == measure:
        by_field = (worded.get("values") or {}).get(str(row.get(axis))) or {}
        return str(by_field.get(str(row.get(measure)), "—"))
    said = row.get("words")
    if isinstance(said, Mapping) and isinstance(said.get(column), str) and said[column]:
        # D9 (SERVE 63cc77014e, ``?words=true``): the measure as every surface quotes it ("1.95", "−0.9%").
        return str(said[column])
    return _plain_text(row.get(column, ""))


def _view_fact_lines(facts: Mapping[str, Any]) -> list[str]:
    """RI-135: what the view draws beyond its rows, in the served words (keel-api ``page_facts.view_page``):
    the Distribution's marker, the marks (05 §4, V18), where the interval is withheld with its reason (05 V14;
    07 §3.2 rule 3: a value without its interval says why), and a SameFooting table's difference sentences
    (``facts.footing_words``, RI-354). Nothing is worded here but the joins."""
    out: list[str] = [str(w) for w in facts.get("footing_words") or []]
    marker = (facts.get("bins") or {}).get("marker")
    if isinstance(marker, Mapping) and marker.get("value") is not None:
        label = str(marker.get("label") or "").strip()
        said = marker.get("words")
        at = said if isinstance(said, str) and said else _plain_text(marker["value"])
        out.append(f"Marker: {label} at {at}" if label else f"Marker at {at}")
    marks = []
    for mark in facts.get("marks") or []:
        if not isinstance(mark, Mapping):
            continue
        label = str(mark.get("label") or "").strip()
        at = mark.get("at")
        line = "a line across" if mark.get("kind") == "hline" else "a line up"
        # STG-SURFACES #3: where the mark sits in its served words (page_facts.worded_marks)
        said = mark.get("at_words")
        at = said if isinstance(said, str) and said else _plain_text(at)
        marks.append(f"{label} at {at}" if label else f"{line} at {at}")
    if marks:
        out.append("Marks: " + "; ".join(marks))
    reasons = facts.get("withheld_reason") or {}
    by_reason: dict[str, list[str]] = {}
    # AUDIT-WORDS M15: where in words ("Nov 2026", "20 days ahead"), never the raw x ("2026-11-01T00:00:00", "20")
    x_axis = _x_axis(facts)
    for x in facts.get("withheld") or []:
        reason = _why(reasons.get(_x_text(x)))
        by_reason.setdefault(reason, []).append(_x_words(x_axis, x))
    for reason, xs in by_reason.items():
        where = ", ".join(xs)
        out.append(f"No interval at {where}: {reason}." if reason else f"No interval at {where}.")
    return out


def _x_axis(facts: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The axis a view's ``withheld`` x values are on: its time axis, else its one ordered axis; None when that is
    not plain (the words then read the value alone)."""
    axes = [
        a for a in facts.get("axes") or [] if isinstance(a, Mapping) and a.get("name") != "result"
    ]
    timed = [a for a in axes if a.get("kind") == "time" or a.get("name") == "time"]
    if timed:
        return timed[0]
    ordered = [a for a in axes if a.get("role") == "ordered"]
    return ordered[0] if len(ordered) == 1 else (axes[0] if len(axes) == 1 else None)


def _x_words(axis: Mapping[str, Any] | None, x: Any) -> str:
    """One withheld x in words: a day or month as :func:`_axis_value_words` says it; a number with its axis
    ("days ahead 20")."""
    said = _axis_value_words(axis, x)
    if axis is None or not isinstance(x, (int, float)) or isinstance(x, bool):
        return said
    name = str(axis.get("label") or "").strip()
    return f"{name[:1].lower() + name[1:]} {said}" if name else said


def _x_text(value: Any) -> str:
    """An x as the server keys ``withheld_reason`` (research.page_facts ``_js_text``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


# ── The structured home (07 §3.3's channel table) ────────────────────────────


def _card_fact(fact: Any) -> Any:
    """A stale fact for the card: its offer without ``plan.source``."""
    if not isinstance(fact, Mapping) or not isinstance(fact.get("offer"), Mapping):
        return fact
    offer = dict(fact["offer"])
    if isinstance(offer.get("plan"), Mapping):
        offer["plan"] = {k: v for k, v in offer["plan"].items() if k != "source"}
    return {**fact, "offer": offer}


def structured_study(env: Mapping[str, Any]) -> dict[str, Any]:
    """``structuredContent`` for a study read on MCP: bounded by construction (RL-58).

    ``headline`` and ``view.markdown`` are dropped (card_summary and the text
    carry them); ``checks.rows`` keeps at most 5 (the user's bars first, as
    served); steps, views and notes become counts.
    """
    out = {k: v for k, v in env.items() if k not in ("headline",)}
    checks = env.get("checks")
    if isinstance(checks, Mapping):
        out["checks"] = {
            "count": checks.get("count"),
            "met": checks.get("met"),
            "rows": list(checks.get("rows") or [])[:STRUCTURED_CHECK_ROWS],
        }
    steps = env.get("steps")
    if isinstance(steps, list):
        by_status: dict[str, int] = {}
        for s in steps:
            by_status[str(s.get("status"))] = by_status.get(str(s.get("status")), 0) + 1
        out["steps"] = {"total": len(steps), "by_status": by_status}
    views = env.get("views")
    if isinstance(views, list):
        out["views"] = {"total": len(views)}
    subjects = env.get("subjects")
    if isinstance(subjects, list):
        # 06 §5's pinned subjects: the app's; MCP carries the count (the text
        # names them), so 50 subjects stay inside the budget.
        out["subjects"] = {"total": len(subjects)}
    items = env.get("items")
    if isinstance(items, list):
        # RI-593: a project read's items (up to 100) are counts too; the text
        # names each one (project_text), and the API keeps the list.
        out["items"] = {"total": len(items)}
    # The stored form is the editor's (06 §4.9), up to 131,072 bytes: never in
    # structuredContent. A plan keeps its own `source` (it is not reduced).
    out.pop("source", None)
    stale = env.get("stale")
    if isinstance(stale, list):
        # A stale fact's offer names the newer ref and the plan's other fields;
        # its `plan.source` is the whole document re-pinned (06 §4.8), which the
        # study read serves and the card does not carry (RL-58; FIX-7's J1.6
        # card was 8,617 bytes, 3.4 KB of it this source).
        out["stale"] = [_card_fact(f) for f in stale]
    if "notes" in env or "notes_total" in env:
        out["notes"] = {"notes_total": int(env.get("notes_total") or 0)}
        out.pop("notes_total", None)
    view = dict(env.get("view") or {})
    view.pop("markdown", None)
    if view:
        out["view"] = view
    return out
