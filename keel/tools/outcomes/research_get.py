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
        (
            row.get("index")
            for row in study.get("views") or []
            if row.get("index") == args.get("view") or row.get("title") == args.get("view")
        ),
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
        if row.get("index") == args.get("view") or row.get("title") == args.get("view"):
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
            + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(by_reason.items()))
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
        lines.append(
            f"- {row.get('address')} = {json.dumps(row.get('value'))}"
            f" · {row.get('component')} v{row.get('version')}{bound}{why}"
        )
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


def _list(args: dict, ctx):
    """The user's studies (07 §4.4 with no id): GET /v1/research/studies."""
    params = {
        "limit": args.get("limit") or LIST_ROWS_MAX,
        "cursor": args.get("cursor"),
        **CARD_FULL,
    }
    page = call(ctx, "get", "/v1/research/studies", params=params)
    rows = list(page.get("data") or [])
    states = page.get("by_state") or {}
    lines = [
        f"Studies: {len(rows)} shown of {int(page.get('studies_total') or 0)}"
        + (
            " (" + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(states.items())) + ")"
            if states
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
    from .open_in_app import research_link

    return research_link(
        "project", project_id, origin=str(getattr(ctx, "app_url", "https://app.usekeel.io"))
    )


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


def _library(args: dict, ctx):
    """R-24 (A3 #1, 07 §4.10): Keel's library, ``GET /v1/research/library?kind=…``, so an agent on the hosted
    connector (where keel_blocks_search is not listed) finds the pinned refs a study names. Each item is written
    with its exact subject call and the plan that studies it; a one-row receipt card (13 §2.1)."""
    others = [
        k
        for k in ("study_id", "share_id", "subject", "project_id", "view", "output")
        if args.get(k)
    ]
    if others:
        invalid("library lists Keel's library; send it without " + ", ".join(others) + ".")
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
    out = {**env, "data": items, "total": len(items), "links": {"app": link}}
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
    return study_result(env, study_text(env), size=present_choice(args) or "view", mode="study")


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
