"""Read one study, a shared snapshot, one chart's rows, a subject's addresses, a project, or the user's studies
(07 §4.4, §4.5, §4.12; D-189 the project read)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# Every number is keel-api's: the text quotes the served values (07 §3.2 rule
# 8's one formatter lives in _research_view), and the structured content is
# the served JSON bounded by CHANNEL_MAP["study"] (07 §3.3).

import json

from keel.api_path import seg

from ._base import present_choice, present_param_schema
from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import (
    CARD_FULL,
    call,
    invalid,
    read_base,
    register_research,
    study_result,
)
from ._research_view import LIST_ROWS_MAX, _day, data_text, next_call, project_text, study_text


REQUIRED_ACTION = "backtest.research.read"
#: 07 §8.5's budget is 230 characters (D-344 (4) trimmed it to pay for run's allowance clause; the list and library
#: forms are said by the `study_id` and `library` parameter descriptions).
LISTED_DESCRIPTION = (
    "Use when the user asks about a study's results; not for one backtest "
    "(keel_backtest_summarize). Returns each measurement against the user's bars with its "
    "sample, or the progress; with view, that chart's rows by cursor."
)
DESCRIPTION = LISTED_DESCRIPTION

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_get"]
PROPERTIES = dict(_SCHEMA["properties"])
PROPERTIES["present"] = present_param_schema("`view`")


def _study_path(args: dict) -> str:
    return f"/v1/research/studies/{seg(args['study_id'])}"


def read_study(args: dict, ctx) -> dict:
    """GET the study (or the share) the arguments name."""
    if args.get("share_id"):
        return call(ctx, "get", f"/v1/research/shares/{seg(args['share_id'])}", params=CARD_FULL)
    params = {"version": args.get("version"), "revision": args.get("revision"), **CARD_FULL}
    return call(ctx, "get", _study_path(args), params=params)


def _is_view(row: dict, view) -> bool:
    """A study read's view row is the one ``view`` names, by its index or its exact title. The CLI passes
    ``--view 0`` as text, so an index is matched as its number too (STG-SURFACES #3: the chart's rows were titled
    "0 (version 1)" instead of "Your bars")."""
    index = row.get("index")
    return (index is not None and str(index) == str(view)) or row.get("title") == view


def _share_view_index(share: dict, view) -> int:
    """A shared chart by its index or exact title, as the share read lists it (the public route takes the index)."""
    for row in share.get("views") or []:
        if (
            row.get("index") == view
            or row.get("title") == view
            or str(row.get("index")) == str(view)
        ):
            return int(row["index"])
    invalid(f"The share has no chart {view!r}: read the share to list its charts.")
    raise AssertionError  # pragma: no cover  (invalid raises)


def _share_data(args: dict, ctx, *, limit: int):
    """A share's rows (A3 #4, 07 §3.5): a chart's rows through the public share-view data route
    (``GET /public/study-shares/{id}/views/{n}/data``, 08 §3.8: the snapshot only, its facts stored at share time),
    or ``output="bars"``, the snapshot's bars as the share read serves them (every row; written 25 at a time, the
    cursor the next row's place). A snapshot is read whole: ``where``, ``sort`` and ``measures`` are not taken."""
    share_id = args["share_id"]
    taken = [k for k in ("study_id", "where", "sort", "measures", "version") if args.get(k)]
    if taken:
        invalid(
            "A share's rows are read whole by view or output; send it without "
            + ", ".join(taken)
            + "."
        )
    share = read_study({"share_id": share_id}, ctx)
    link = (share.get("links") or {}).get("app")
    if args.get("output") is not None:
        if args["output"] != "bars":
            invalid('A share serves its charts by view, and its bars as output="bars".')
        return _share_bars(share, args, ctx, limit=limit, link=link)
    index = _share_view_index(share, args.get("view"))
    params = {"limit": limit, "cursor": args.get("cursor")}
    page = call(
        ctx,
        "get",
        f"/public/study-shares/{seg(share_id)}/views/{seg(str(index))}/data",
        params=params,
    )
    title = next(
        (str(r.get("title")) for r in share.get("views") or [] if r.get("index") == index),
        str(index),
    )
    as_run = (share.get("study") or {}).get("as_run_on")
    title = f"{title}, as shared (run {_day(as_run)})" if as_run else f"{title}, as shared"
    version = (share.get("study") or {}).get("version")
    text = data_text(
        page, title=title, version=version, call_args={"share_id": share_id, "view": index}
    )
    text = f"{text}\nView in Keel: {link}" if link else text
    return study_result(
        {**page, "links": share.get("links") or {}}, text, size="view", mode="chart"
    )


def _share_bars(share: dict, args: dict, ctx, *, limit: int, link: str | None):
    from ._research_view import _locked_steps, bar_row

    checks = share.get("checks") or {}
    rows = list(checks.get("rows") or [])
    cursor = args.get("cursor")
    if cursor is not None and not str(cursor).isdigit():
        invalid("That cursor is not a share bars cursor: read the share's bars again without one.")
    start = int(cursor or 0)
    page = rows[start : start + limit]
    study = share.get("study") or {}
    lines = [
        f"{study.get('title', '')}: bars {start + 1}–{start + len(page)} of {len(rows)}"
        f" (met: {int(checks.get('met', 0))} of {int(checks.get('count', len(rows)))}, as shared)"
        if page
        else f"{study.get('title', '')}: no bars from {start + 1} of {len(rows)}"
    ]
    locked = _locked_steps(share)
    lines.extend(bar_row(r, locked) for r in page)
    if start + len(page) < len(rows):
        more = {"share_id": args["share_id"], "output": "bars", "cursor": str(start + len(page))}
        lines.append("More: " + next_call("keel_research_get", more))
    if link:
        lines.append(f"View in Keel: {link}")
    return study_result(
        {**share, "links": share.get("links") or {}}, "\n".join(lines), size="view", mode="chart"
    )


def _data(args: dict, ctx, *, limit: int = LIST_ROWS_MAX):
    """One chart's rows (07 §4.5): the study read names the version and the title. ``limit``: MCP pages at most 25
    rows; the CLI's ``keel research data`` passes the API's default, 100 (07 §3.5, R-22)."""
    if args.get("share_id"):
        return _share_data(args, ctx, limit=args.get("limit") or limit)
    if not args.get("study_id"):
        invalid(
            "A chart's rows are read from a study or a share: send study_id or share_id with view or output."
        )
    study = read_study({k: args.get(k) for k in ("study_id", "version")}, ctx)
    version = args.get("version") or (study.get("study") or {}).get("version")
    index = next(
        (row.get("index") for row in study.get("views") or [] if _is_view(row, args.get("view"))),
        None,
    )
    params = {
        "view": args.get("view"),
        "output": args.get("output"),
        "measures": args.get("measures"),
        "where": json.dumps(args["where"]) if args.get("where") is not None else None,
        "sort": json.dumps(args["sort"]) if args.get("sort") is not None else None,
        "limit": args.get("limit") or limit,
        "cursor": args.get("cursor"),
        # 13 §3.3 (WL-AG-09): a chart read asks for its chart at card size (the chart card, `card_data.key`).
        "card_view": index,
        # D9 (SERVE 63cc77014e): each row's measures in the one served string, written in the table's cells.
        "words": "true",
    }
    page = call(
        ctx,
        "get",
        f"{_study_path(args)}/versions/{seg(str(int(version)))}/data",
        params=params,
    )
    title = str(args.get("output") or args.get("view"))
    for row in study.get("views") or []:
        if _is_view(row, args.get("view")):
            title = row.get("title") or title
    call_args = {
        k: args.get(k)
        for k in ("study_id", "version", "view", "output", "measures", "where", "sort")
        if args.get(k) is not None
    }
    text = data_text(page, title=title, version=version, call_args=call_args)
    link = (study.get("links") or {}).get("app")
    text = f"{text}\nView in Keel: {link}" if link else text
    env = {**page, "links": study.get("links") or {}}
    return study_result(env, text, size="view", mode="chart")


#: MCP-FINAL r2: the address read's ``by_reason`` counts in words, never the keys ("3 inert, 15 settable, 2 wiring"),
#: each as its rows' served why-not words say it (research.words: "has no effect while …", "wires the strategy
#: together; it is not a setting"). A reason with no words here raises: a new served key needs its words first.
ADDRESS_REASON_WORDS: dict[str, str] = {
    "settable": "you can set",
    "bound": "bound to another setting",
    "wiring": "that wire the strategy together",
    "infra": "that Keel sets",
    "follows_declaration": "that follow a declaration",
    "dead": "that nothing reads",
    "inert": "with no effect as the strategy stands",
    "unsupported": "a study cannot vary",
}


def _addresses(args: dict, ctx):
    """A subject's address list (07 §4.12): STEP-A's rows, verbatim."""
    params = {
        "subject": args["subject"],
        "include": args.get("include"),
        "limit": args.get("limit") or LIST_ROWS_MAX,
        "cursor": args.get("cursor"),
    }
    page = call(ctx, "get", "/v1/research/addresses", params=params)
    rows = list(page.get("data") or [])
    by_reason = page.get("by_reason") or {}
    total = int(page.get("rows_total") or len(rows))
    lines = [
        f"Addresses of {args['subject']}: {len(rows)} shown of {total}"
        + (
            " ("
            + ", ".join(f"{n} {ADDRESS_REASON_WORDS[k]}" for k, n in sorted(by_reason.items()) if n)
            + ")"
            if by_reason
            else ""
        )
    ]
    for row in rows:
        # AG-18 (07 §4.12, over keel-api's address read): why a row cannot be set reads its served words
        # (`why_not_words`), the code only when no words are served; `bound_to` is a list of addresses.
        said = row.get("why_not_words") or row.get("why_not")
        why = f" — {said}" if said else ""
        bound_to = row.get("bound_to")
        if isinstance(bound_to, list):
            bound_to = ", ".join(str(b) for b in bound_to)
        bound = f" (bound to {bound_to})" if bound_to else ""
        # MCP-FINAL #9: a declaration's address (execution.*, globals.*) belongs to no component, so it names none
        # (it read "· None vNone"); a step's names its component and version
        made = f" · {row.get('component')} v{row.get('version')}" if row.get("component") else ""
        lines.append(f"- {row.get('address')} = {json.dumps(row.get('value'))}{made}{bound}{why}")
    pagination = page.get("pagination") or {}
    if pagination.get("has_more") and pagination.get("cursor"):
        more = {k: args.get(k) for k in ("subject", "include") if args.get(k) is not None}
        lines.append(
            "More: " + next_call("keel_research_get", {**more, "cursor": pagination["cursor"]})
        )
    lines.append(f"View in Keel: {_subject_url(args['subject'], ctx)}")
    return study_result(page, "\n".join(lines), size="receipt", mode="addresses")


def _subject_url(subject: str, ctx) -> str:
    """07 §3.2 rule 9 (A3 #12): an address read ends "View in Keel" at the subject's editor when the subject is a
    strategy version (``Strategy("str_…@n")``), else at the Research home."""
    import re

    from ._research_commands import research_url
    from .open_in_app import app_url_for

    found = re.search(r'"(str_[A-Za-z0-9]+)@\d+"', subject)
    return app_url_for("strategy", found.group(1), ctx) if found else research_url(ctx)


#: AUDIT S3 #5: a list header's per-state counts, in the words the study card's state line uses for each state
#: (``research.card_summary.state_words``: Planned, Running, Stopping, Stopped, Didn't finish, Done, "Done · 2 didn't
#: finish"), never the enum key ("completed partial", "cancel requested"). Keyed by every state keel-api serves (the
#: test holds this to ``StudyState``); an unknown key raises rather than printing a key.
STATE_COUNT_WORDS: dict[str, str] = {
    "planned": "planned",
    "running": "running",
    "cancel_requested": "stopping",
    "cancelled": "stopped",
    "failed": "didn't finish",
    "completed": "done",
    "completed_partial": "done with runs that didn't finish",
}


def _by_state_order(states: dict) -> list[tuple[str, int]]:
    """The served ``by_state`` counts in the order a study moves through them."""
    order = list(STATE_COUNT_WORDS)
    unknown = [k for k in states if k not in STATE_COUNT_WORDS]
    if unknown:
        raise ValueError(f"no list words for study state(s) {unknown!r}")
    return sorted(((k, int(n)) for k, n in states.items()), key=lambda kv: order.index(kv[0]))


def _list(args: dict, ctx, *, most: int | None = None):
    """The user's studies (07 §4.4 with no id): GET /v1/research/studies.

    ``most`` (the CLI's list, R-22: 100 on the CLI, 25 on MCP; STG-SURFACES #28): read on, page by page (the route
    serves at most :data:`LIST_ROWS_MAX` a page), until that many studies are read or there are no more; the text
    then names the rest's cursor as one read does."""
    want = most if most is not None else (args.get("limit") or LIST_ROWS_MAX)
    params = {
        "limit": min(want, LIST_ROWS_MAX),
        "cursor": args.get("cursor"),
        **CARD_FULL,
    }
    page = call(ctx, "get", "/v1/research/studies", params=params)
    rows = list(page.get("data") or [])
    while most is not None and len(rows) < want:
        more = page.get("pagination") or {}
        if not (more.get("has_more") and more.get("cursor")):
            break
        params = {**params, "limit": min(want - len(rows), LIST_ROWS_MAX), "cursor": more["cursor"]}
        page = call(ctx, "get", "/v1/research/studies", params=params)
        rows += list(page.get("data") or [])
    states = page.get("by_state") or {}
    lines = [
        f"Studies: {len(rows)} shown of {int(page.get('studies_total') or 0)}"
        + (
            " ("
            + ", ".join(f"{n:,} {STATE_COUNT_WORDS[k]}" for k, n in _by_state_order(states) if n)
            + ")"
            if any(states.values())
            else ""
        )
    ]
    for row in rows:
        label = (row.get("kind_label") or {}).get("short", "Study")
        head = f" — {row['headline']}" if row.get("headline") else ""
        lines.append(
            f"- {row.get('study_id')} · {label} · {row.get('title')} · {row.get('state_words')}{head}"
        )
    pagination = page.get("pagination") or {}
    if pagination.get("has_more") and pagination.get("cursor"):
        lines.append("More: " + next_call("keel_research_get", {"cursor": pagination["cursor"]}))
    if not rows and not int(page.get("studies_total") or 0) and not args.get("cursor"):
        # STG-SURFACES #25: an org with no study is told how to start one (a Check of one of its strategies).
        # AUDIT S3 #12: the list of the reader's strategies comes first, so the placeholder is one it can fill
        lines.append(
            "Start one: "
            + next_call("keel_strategy_search", {})
            + " lists your strategies; "
            + next_call(
                "keel_research_plan", {"template": "check", "subjects": ['Strategy("str_…@3")']}
            )
            + " checks one, with its id and version in place of str_…@3."
        )
    from ._research_commands import research_url

    lines.append(f"View in Keel: {research_url(ctx)}")
    env = {**page, "studies": rows, "links": {"app": research_url(ctx)}}
    env.pop("data", None)
    return study_result(env, "\n".join(lines), size="view", mode="list")


def _project(args: dict, ctx):
    """D-189: a project read (D-177's GET /v1/research/projects/{prj}), here because a read is read-scoped (07 §5):
    a read-only token reads a project; ``keel_research_project`` keeps only its writes."""
    others = [k for k in ("study_id", "share_id", "subject", "view", "output") if args.get(k)]
    if others:
        invalid("project_id reads a project; send it without " + ", ".join(others) + ".")
    params = {"limit": args["limit"]} if args.get("limit") else None
    env = call(ctx, "get", f"/v1/research/projects/{seg(args['project_id'])}", params=params)
    link = project_url(env.get("project_id") or args["project_id"], ctx)
    # D-328 D8 (13 §2.1): a project read is a one-row receipt that links the project's page.
    return study_result(
        {**env, "links": {"app": link}},
        project_text(env, app_url=link),
        size="receipt",
        mode="project",
    )


def project_url(project_id: str, ctx) -> str:
    """The project's app page, ``link_routes.json``'s ``project`` route (SERVE's, 13 §3.6)."""
    from .open_in_app import app_base, research_link

    return research_link("project", project_id, origin=app_base(ctx))


#: R-24 (WL-AG-08): each library kind's route kind and the subject call a study names an item by (07 §4.10,
#: 01 §2.4): a signal is an `Output`, a market state a regime `Output`, a strategy or benchmark a `Strategy`.
_LIBRARY = {
    "signals": ("signals", "signal"),
    "market_states": ("market_states", "market state"),
    "strategies": ("strategies", "strategy"),
    "benchmarks": ("strategies", "benchmark"),
}


def _library_call(item: dict) -> str:
    """The item's subject call: keel-api's served ``writes`` when it serves one (a signal's ``Output(…)``), else a
    strategy's ``Strategy("keel/…@n")`` (01 §2.4's form; the route serves no ``writes`` for strategies)."""
    if isinstance(item.get("writes"), str) and item["writes"]:
        return item["writes"]
    ref = json.dumps(item.get("ref"))
    if item.get("output"):
        role = f", role={json.dumps(item['role'])}" if item.get("role") else ""
        return f"Output({ref}, output={json.dumps(item['output'])}{role})"
    return f"Strategy({ref})"


#: D-415 D5: the user's own signals: keel-api's strategies list, ``kind=signal`` (the one owner of "this pipeline is a
#: signal", ``routers/strategies._SIGNAL_HEAD_SQL``), one page.
MINE_LISTED = 100
_SIGNAL_ROLES = frozenset({"signal", "filter", "event", "regime"})


def _mine(args: dict, ctx):
    """D-415 D5 (SIGNALS-FIRST): ``library="mine"``, the user's own signals in the library list's shape: each by its
    name and version with the exact call a study names it by, ``Output("str_…@n", output="…")``, its output read from
    the user's blocks at that version (``GET /v1/blocks?scope=mine``; the strategies list serves no output names). A
    signal whose output could not be read says so, never a guessed call. ``keel signals list`` is this read."""
    listing = call(
        ctx,
        "get",
        "/v1/strategies",
        params={"kind": "signal", "limit": MINE_LISTED, "sort": "updated_desc"},
    )
    rows = [
        r for r in listing.get("data") or [] if isinstance(r, dict) and r.get("kind") == "signal"
    ]
    outputs: dict[str, str] = {}
    if rows:
        blocks = call(ctx, "get", "/v1/blocks", params={"scope": "mine", "limit": 50})
        for item in blocks.get("items") or []:
            if isinstance(item, dict) and item.get("role") in _SIGNAL_ROLES:
                outputs.setdefault(str(item.get("ref")), str(item.get("output")))
    items = []
    for r in rows:
        sid, version = r.get("id") or r.get("strategy_id"), r.get("current_sequence")
        ref = f"{sid}@{version}" if isinstance(sid, str) and isinstance(version, int) else None
        output = outputs.get(ref or "")
        items.append(
            {
                "label": r.get("name"),
                "ref": ref,
                "version": version,
                "output": output,
                "writes": f"Output({json.dumps(ref)}, output={json.dumps(output)})"
                if ref and output
                else None,
                "made_from_study_id": r.get("made_from_study_id"),
            }
        )
    shown = items[:LIST_ROWS_MAX]
    lines = [f"Your signals: {len(shown)} shown of {len(items)}"]
    for item in shown:
        said = item["writes"] or (
            "not saved yet" if item["ref"] is None else "its output could not be read just now"
        )
        lines.append(f"- {item['label']} · v{item['version']} · {said}")
    first = next((i for i in shown if i["writes"]), None)
    if first:
        example = next_call(
            "keel_research_plan", {"template": "signal", "subjects": [first["writes"]]}
        )
        lines.append(f"Study one: {example}")
    elif not items:
        lines.append(
            "None yet. Make one from a signal comparison's mix (keel_research_make_signal), "
            'or list Keel\'s with library="signals".'
        )
    from ._research_commands import research_url

    link = research_url(ctx)
    lines.append(f"View in Keel: {link}")
    out = {"data": items, "total": len(items), "links": {"app": link}, "card_line": lines[0]}
    return study_result(out, "\n".join(lines), size="receipt", mode="library")


def _library(args: dict, ctx):
    """R-24 (A3 #1, 07 §4.10): Keel's library, ``GET /v1/research/library?kind=…``, so an agent on the hosted
    connector (where keel_blocks_search is not listed) finds the pinned refs a study names. Each item is written
    with its exact subject call and the plan that studies it; a one-row receipt card (13 §2.1). ``"mine"`` lists the
    user's own signals the same way (``_mine``, D-415 D5)."""
    others = [
        k
        for k in ("study_id", "share_id", "subject", "project_id", "view", "output")
        if args.get(k)
    ]
    if others:
        invalid("library lists Keel's library; send it without " + ", ".join(others) + ".")
    if args["library"] == "mine":
        return _mine(args, ctx)
    kind, noun = _LIBRARY[args["library"]]
    env = call(ctx, "get", "/v1/research/library", params={"kind": kind})
    items = [i for i in env.get("data") or [] if isinstance(i, dict)]
    if args["library"] == "benchmarks":
        items = [i for i in items if i.get("kind") == "benchmark"]
    elif args["library"] == "strategies":
        items = [i for i in items if i.get("kind") != "benchmark"]
    shown = items[:LIST_ROWS_MAX]
    lines = [
        f"Keel's library, {args['library'].replace('_', ' ')}: {len(shown)} shown of {len(items)}"
    ]
    for item in shown:
        lines.append(f"- {item.get('label')} · {item.get('universe')} · {_library_call(item)}")
    if shown and noun in ("benchmark", "market state"):
        # A benchmark or a market state is added to a study (07 §4.12.4), never studied alone.
        call_words = _library_call(shown[0])
        lines.append(
            f"Add one to a study: keel_research_plan(study_id=<its id>, add=['{call_words}'])"
        )
    elif shown:
        template = "signal" if noun == "signal" else "check"
        example = next_call(
            "keel_research_plan", {"template": template, "subjects": [_library_call(shown[0])]}
        )
        lines.append(f"Study one: {example}")
    from ._research_commands import research_url

    link = research_url(ctx)
    lines.append(f"View in Keel: {link}")
    # CARD-RENDER receipt-library (13 §2.1, D8): the one-row receipt names what it read in the text's own first line
    # (never "Untitled"); the library route serves no card line of its own
    out = {**env, "data": items, "total": len(items), "links": {"app": link}, "card_line": lines[0]}
    return study_result(out, "\n".join(lines), size="receipt", mode="library")


def _source(args: dict, ctx):
    """D-353: ``include="source"`` with ``study_id``: the study read with its study/1 document and the read's
    ``base`` written after it in the TEXT answer only, so an agent on any host can read it, change it and plan the
    change (``keel_research_plan(study_id, source, base)``). structuredContent never carries the source
    (``structured_study`` drops it: up to 131,072 bytes); a share read serves no source (its author's stays theirs)."""
    if args.get("share_id") or not args.get("study_id"):
        invalid(
            'include="source" reads one of your studies: send it with study_id (a shared study serves no source).'
        )
    if args.get("subject") or args.get("view") is not None or args.get("output"):
        invalid('include="source" is read with study_id (and version) alone.')
    env = read_study({k: args.get(k) for k in ("study_id", "version", "revision")}, ctx)
    base = read_base(env)
    if base is not None:
        env = {**env, "base": base}
    source = env.get("source")
    text = study_text(env)
    if isinstance(source, str) and source:
        study = env.get("study") or {}
        edit = next_call(
            "keel_research_plan",
            {
                "study_id": study.get("study_id") or args["study_id"],
                "source": "<the changed document>",
                "base": base,
            },
        )
        text += (
            f"\n\nSource of version {study.get('version')} ({len(source):,} characters):\n```\n{source.rstrip()}\n```\n"
            f"Plan a change: {edit}, with the whole changed document as source and this base."
        )
    else:
        text += "\n\nThis read served no source."
    return study_result(env, text, size=present_choice(args) or "view", mode="study")


def _handler(args, ctx):
    if args.get("library"):
        return _library(args, ctx)
    if args.get("project_id"):
        return _project(args, ctx)
    if args.get("include") == "source":
        return _source(args, ctx)
    if args.get("subject"):
        if args.get("study_id") or args.get("share_id"):
            invalid("subject reads a subject's addresses; send it without a study or share.")
        return _addresses(args, ctx)
    if args.get("view") is not None or args.get("output"):
        return _data(args, ctx)
    if not args.get("study_id") and not args.get("share_id"):
        return _list(args, ctx)
    if args.get("study_id") and args.get("share_id"):
        invalid("Send study_id or share_id, not both.")
    env = read_study(args, ctx)
    # RI-483 (D-301): the read an edit of this source is made on, beside the source (keel_research_plan's edit form
    # sends it; a stale or missing base is refused rather than planned over a Draft).
    base = read_base(env)
    if base is not None:
        env = {**env, "base": base}
    if never_ran(env):
        # 13 §5's Planned row (D-381 (2)): a saved Draft that never ran has nothing to draw as a study; its card is
        # the plan receipt with the Draft's served words, never an empty study frame
        return study_result(env, study_text(env), size="receipt", mode="plan")
    return study_result(env, study_text(env), size=present_choice(args) or "view", mode="study")


def never_ran(env: dict) -> bool:
    """A study read whose version is a Draft no run has touched (``study.state`` ``planned``, 13 §5): no run set, no
    card summary, nothing measured."""
    study = env.get("study") if isinstance(env.get("study"), dict) else {}
    return study.get("state") == "planned" and not env.get("card_summary")


TOOL = register_research(
    "research_get",
    ("research", "get"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=True,
    title="Read Study",
    listed_description=LISTED_DESCRIPTION,
)
