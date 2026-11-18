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
6. a platform failure reads "couldn't run on Keel's side", never a failed check;
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


def _sample(sample: Mapping[str, Any] | None) -> str:
    if not sample:
        return "sample not stated"
    parts = []
    for key, one, many in (
        ("days", "day", "days"),
        ("effective_days", "effective day", "effective days"),
        ("coins", "coin", "coins"),
        ("trades", "trade", "trades"),
        ("shared_days", "shared day", "shared days"),
        ("books", "book", "books"),
    ):
        n = sample.get(key)
        if n is not None:
            parts.append(
                f"{n:,} {one if n == 1 else many}" if isinstance(n, int) else f"{n} {many}"
            )
    return ", ".join(parts) or "sample not stated"


def _interval(item: Mapping[str, Any]) -> str:
    iv = item.get("interval")
    if iv:
        unit = item.get("unit")
        return f"interval {_value(iv.get('lo'), unit)} to {_value(iv.get('hi'), unit)}"
    reason = item.get("withheld_reason")
    return f"interval withheld: {reason}" if reason else "no interval"


def _met(row: Mapping[str, Any]) -> str:
    met = row.get("met")
    if met is None:
        return "not measured"
    return "met" if met else "not met"


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
        f" · {coins.get('count', '?'):,} coins: {chosen}"
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


def _first_line(env: Mapping[str, Any]) -> str:
    study = env.get("study") or {}
    card = env.get("card_summary") or {}
    kind = (study.get("kind_label") or {}).get("name", "Study")
    state = card.get("state_words") or study.get("state", "")
    if study.get("share_id"):
        line = (
            f"Snapshot · {kind} · {study.get('title', '')} · as run {_day(study.get('as_run_on'))}"
        )
        by = _shared_by(env)
        return f"{line} · shared by {by}" if by else line
    line = f"{kind} · {study.get('title', '')} · version {study.get('version')} · {state}"
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
    maker = (env.get("card_summary") or {}).get("maker")
    return str(maker) if maker and maker != "keel" else None


def _method_line(env: Mapping[str, Any]) -> str | None:
    steps = env.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    seen: list[str] = []
    for s in steps:
        words = f"{s.get('step')} v{s.get('version')}"
        if words not in seen:
            seen.append(words)
    return "Measured with " + ", ".join(seen) + "."


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


def bar_row(r: Mapping[str, Any], locked: set[str]) -> str:
    """One bar as its measurement line (rule 3)."""
    if r.get("met") is None and str(r.get("ref") or "").split(".", 1)[0] in locked:
        # RI-243 (07 §4.4, D-201): a bar over a step the version ran without, on a plan without it, has no
        # number and is out of the served count: it says so, never "not measured".
        return f"- {r.get('label')}: {NOT_INCLUDED}"
    if isinstance(r.get("value_words"), str) and r["value_words"]:
        # D-328 D9 (13 §3.2, WL-AG-12): the row's numbers in keel-api's served words (research.words), the same
        # strings the card and the app show, so a reply never shows a second rendering of a number the user sees.
        interval = r.get("interval_words")
        if not interval:
            reason = r.get("withheld_reason")
            interval = f"interval withheld: {reason}" if reason else "no interval"
        sample = r.get("sample_words") or _sample(r.get("sample"))
        bar = r.get("bar_words") or f"your bar {r.get('op')} {r.get('bar')}"
        return f"- {r.get('label')}: {r['value_words']}, {interval}; {sample}; {bar}; {_met(r)}"
    # A row served without words (a snapshot frozen before WL-SV-04) reads through the one formatter, as before.
    return (
        f"- {r.get('label')}: {_value(r.get('value'), r.get('unit'))}, {_interval(r)};"
        f" {_sample(r.get('sample'))}; your bar {r.get('op')} {_value(r.get('bar'), r.get('unit'))};"
        f" {_met(r)}"
    )


def _bars_lines(env: Mapping[str, Any], study_id: str | None) -> list[str]:
    checks = env.get("checks")
    if not checks:
        return []
    rows = list(checks.get("rows") or [])
    count, met = int(checks.get("count", len(rows))), int(checks.get("met", 0))
    lines = [f"Your bars ({count}; met: {met}):"]
    shown = rows[:LIST_ROWS_MAX]
    locked = _locked_steps(env)
    lines.extend(bar_row(r, locked) for r in shown)
    rest = count - len(shown)
    if rest > 0:
        rest_met = met - sum(1 for r in shown if r.get("met") is True)
        call = next_call("keel_research_get", {**_target(env), "output": "bars"})
        lines.append(
            Keep(f"and {rest} more bar{'s' if rest != 1 else ''}: {rest_met} met — {call}")
        )
    return lines


def _headline_lines(env: Mapping[str, Any]) -> list[str]:
    """A study with no bars writes its served headline measures (01 §3.3, at most four), each a measurement line
    (rule 3: value, unit, interval or its withheld reason, sample). With bars, the bar lines carry the numbers."""
    if env.get("checks"):
        return []
    measures = list(((env.get("headline") or {}).get("measures") or [])[:4])
    if not measures:
        return []
    out = ["In short:"]
    for m in measures:
        if isinstance(m.get("value_words"), str) and m["value_words"]:
            # D9 (SERVE 63cc77014e): a headline measure's served words, as bar_row reads a check row's.
            interval = m.get("interval_words")
            if not interval:
                reason = m.get("withheld_reason")
                interval = f"interval withheld: {reason}" if reason else "no interval"
            sample = m.get("sample_words") or _sample(m.get("sample"))
            out.append(f"- {m.get('label')}: {m['value_words']}, {interval}; {sample}")
            continue
        out.append(
            f"- {m.get('label')}: {_value(m.get('value'), m.get('unit'))}, {_interval(m)};"
            f" {_sample(m.get('sample'))}"
        )
    return out


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
    if vt:
        n = vt.get("count")
        said = "unknown" if n is None else f"{n:,}"
        # A version that counted no family serves ``family`` empty and ``count`` null: "versions tried: unknown",
        # never "versions tried for : unknown".
        # AGENT F1 (SERVE 63cc77014e): the family by its served name, never the ledger id ("fam:str_…").
        name = vt.get("family_name")
        family = f" for {name}" if name else ""
        line += f" · versions tried{family}: {said}"
    return line


def _views_line(env: Mapping[str, Any], study_id: str | None) -> str | None:
    views = env.get("views")
    if not isinstance(views, list) or not views:
        return None
    titles = [str(v.get("title")) for v in views[:LIST_ROWS_MAX]]
    more = f" and {len(views) - LIST_ROWS_MAX} more" if len(views) > LIST_ROWS_MAX else ""
    call = next_call("keel_research_get", {**_target(env), "view": 0})
    return Keep(
        f"Charts ({len(views)}): "
        + " · ".join(titles)
        + more
        + f" — {call} reads the numbers behind any of them."
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
        line = f"- {n.get('kind')} ({who}, {_day(n.get('date'))}): {_quote(n.get('body'))}"
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


def _share_next_lines(env: Mapping[str, Any]) -> list[str]:
    """07 §4.4 (A3 #4, AT-29): a share read offers both of 08 §3.8.1's actions. "Run on yours" is the plan's From
    share form (listed); "Make it yours", a copy with nothing run, is the CLI's ``keel research copy`` and the app's,
    so the generated call is ``keel_app_link`` to the snapshot, with the CLI's command beside it."""
    share_id = (env.get("study") or {}).get("share_id")
    if not share_id:
        return []
    run = next_call("keel_research_plan", {"share_id": share_id})
    make = next_call("keel_app_link", {"id": share_id})
    return [
        Keep(f"Run on yours: {run}, with subjects=[…] to run it on your own"),
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
    lines.append(_method_line(env) or "")
    lines.extend(_headline_lines(env))
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
        lines[0] = f"{lines[0]} · {said}"
        if study_id:
            lines.append(
                Keep(
                    next_call("keel_research_get", {"study_id": study_id})
                    + " reads it as it fills in."
                )
            )
    elif progress:
        lines.append(said)
    if not study.get("share_id"):
        # A share read serves the snapshot, not this reader's runs: its counts read "0 runs" for a study that ran
        # 24 (the card's footer carries the snapshot's own runs words).
        lines.append(_runs_line(env) or "")
    lines.append(_views_line(env, study_id) or "")
    lines.extend(_notes_lines(env))
    lines.extend(_linked_to_lines(study))
    for fact in env.get("stale") or []:
        lines.append(_stale_line(fact))
    draft = study.get("draft")
    if draft:
        line = f"Version {draft.get('version')} is a saved Draft that has not run yet."
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
        elif (
            study_id and draft.get("version") == study.get("version") and draft.get("can_run", True)
        ):
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
    for key in ("estimate", "also_changes"):
        if words.get(key):
            lines.append(str(words[key]))
    if words.get("allowance"):
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
        for s in steps[:40]:
            if not s.get("runnable", True):
                parts.append(f"{s.get('label')}: {NOT_INCLUDED}")
            elif s.get("to_run"):
                parts.append(f"{s.get('label')} {s.get('to_run')}")
            else:
                parts.append(f"{s.get('label')} reused")
        lines.append(f"Steps ({len(steps)}): " + " · ".join(parts))
    issues = [
        i
        for i in env.get("issues") or []
        if not (i.get("code") == "STUDY_PLAN_STEP_NOT_INCLUDED" and i.get("path") in said_locked)
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
        cell_words = (
            " (" + ", ".join(f"{n:,} {k.replace('_', ' ')}" for k, n in cells.items() if n) + ")"
        )
    lines.append(
        f"Rows 1–{len(rows)} of {total:,}{cell_words}"
        if rows
        else f"No rows yet of {total:,}{cell_words}"
    )
    worded = facts.get("footing_cells") or {}
    if rows:
        # A row's served words and link are read with its cells, never columns of their own.
        cols = [c for c in rows[0].keys() if c not in ("words", "link")]
        axes = {str(a.get("name")): a for a in facts.get("axes") or [] if isinstance(a, Mapping)}
        bases = {
            str(m.get("name")): str(m["basis"])
            for m in facts.get("measures") or []
            if isinstance(m, Mapping) and m.get("basis")
        }
        # WL-AG-12: a measure column reads its served label beside its key ("Growth of 1 (growth)"), as an axis does.
        labels = {
            str(m.get("name")): str(m["label"])
            for m in facts.get("measures") or []
            if isinstance(m, Mapping) and m.get("label")
        }
        lines.append("| " + " | ".join(_header(c, axes, bases, labels) for c in cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows:
            lines.append("| " + " | ".join(_served_cell(r, c, worded) for c in cols) + " |")
    for name, rng in (facts.get("ranges") or {}).items():
        low, high = rng.get("low") or {}, rng.get("high") or {}
        line = f"Across all {total:,} rows: {name} {low.get('value')} to {high.get('value')}"
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


def _header(
    column: str,
    axes: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
) -> str:
    """RI-361: a column's header as the agent reads it. An axis column reads its served label (``facts.axes[].label``,
    research.words.axis_words: "Result", "Days late"), the words the app draws, with the key a ``where`` takes beside
    it ("Result (result)"); a label that is the key already ("lookback") reads once. D-315 (RI-568): a money measure
    reads its served basis beside its key ("gross_return (before fees and funding)"), as the app's CSV header does.
    Any other column, and an axis with no served label, reads its key as served."""
    if bases and bases.get(column):
        return f"{column} ({bases[column]})"
    label = str((axes.get(column) or {}).get("label") or (labels or {}).get(column) or "").strip()
    if not label or label == column:
        return column
    return f"{label} ({column})"


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
    value = row.get(column, "")
    return "—" if value is None else str(value)


def _view_fact_lines(facts: Mapping[str, Any]) -> list[str]:
    """RI-135: what the view draws beyond its rows, in the served words (keel-api ``page_facts.view_page``):
    the Distribution's marker, the marks (05 §4, V18), where the interval is withheld with its reason (05 V14;
    07 §3.2 rule 3: a value without its interval says why), and a SameFooting table's difference sentences
    (``facts.footing_words``, RI-354). Nothing is worded here but the joins."""
    out: list[str] = [str(w) for w in facts.get("footing_words") or []]
    marker = (facts.get("bins") or {}).get("marker")
    if isinstance(marker, Mapping) and marker.get("value") is not None:
        label = str(marker.get("label") or "").strip()
        out.append(
            f"Marker: {label} at {marker['value']}" if label else f"Marker at {marker['value']}"
        )
    marks = []
    for mark in facts.get("marks") or []:
        if not isinstance(mark, Mapping):
            continue
        label = str(mark.get("label") or "").strip()
        at = mark.get("at")
        line = "a line across" if mark.get("kind") == "hline" else "a line up"
        marks.append(f"{label} at {at}" if label else f"{line} at {at}")
    if marks:
        out.append("Marks: " + "; ".join(marks))
    reasons = facts.get("withheld_reason") or {}
    by_reason: dict[str, list[str]] = {}
    for x in facts.get("withheld") or []:
        key = _x_text(x)
        reason = str(reasons.get(key) or "").strip()
        by_reason.setdefault(reason, []).append(key)
    for reason, xs in by_reason.items():
        where = ", ".join(xs)
        out.append(
            f"Interval withheld at {where}: {reason}" if reason else f"Interval withheld at {where}"
        )
    return out


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
