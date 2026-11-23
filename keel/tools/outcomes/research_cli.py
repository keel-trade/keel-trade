"""Research's CLI-only verbs (07 §2.2): each a `cli_only` OutcomeTool, with no MCP registration.

An agent on MCP reaches the same outcome through the eight research tools
(add is plan's add form, both through add-plan; watch, data and list are get; resume and retry are a
plan of the Again form then run) or the app (`keel_app_link`). Every verb
calls the API, which answers RESEARCH_PAUSED while research.enabled is off.
"""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").

import time
import uuid

from keel.api_path import seg

from ._research_commands import (
    CARD_FULL,
    call,
    invalid,
    read_source,
    register_research,
    study_result,
)
from ._research_view import plan_text, project_text, study_text
from .research_get import PROPERTIES as GET_PROPERTIES
from .research_plan import PROPERTIES as PLAN_PROPERTIES
from .research_plan import add_call, plan_body
from .research_run import TERMINAL, _poll_budget_s, state_of


READ = "backtest.research.read"
UPDATE = "backtest.research.update"
RUN = "backtest.research.run"

_STUDY_ID = {
    "type": "string",
    "pattern": "^stu_",
    "x-cli-positional": True,
    "description": "The study (stu_…).",
}
_RUN_SET_ID = {
    "type": "string",
    "pattern": "^rus_",
    "x-cli-positional": True,
    "description": "The run (rus_…), from the study's read.",
}
_SHARE_ID = {
    "type": "string",
    "minLength": 23,
    "maxLength": 23,
    "x-cli-positional": True,
    "description": "The shared study's id (23 characters).",
}
_VERSION = {"type": "integer", "minimum": 1, "description": "The study version."}


def _intent_key(verb: str) -> dict[str, str]:
    """RI-504: run-draft, resume and retry start costed work, which keel-api refuses 422 idempotency_key_required
    without an ``Idempotency-Key`` (it used to answer 500). One key per call, as ``research_run.submit`` sends."""
    return {"Idempotency-Key": f"sdk-research-{verb}-{uuid.uuid4()}"}


def _study_answer(env: dict) -> object:
    return study_result(env, study_text(env), size="view")


def _receipt(env: dict, *keys: str, ctx=None, study_id: str | None = None) -> object:
    """A write's receipt: its served words, then "View in Keel" (07 §3.2 rule 9, A3 #12): the served link, else the
    study's page when the write names a study, else the Research home (set-aside, restore, archive and delete answer
    with no link)."""
    words = next((str(env[k]) for k in keys if env.get(k)), "")
    if not words and env.get("deleted") is True:
        # StudyDeletedResponse serves its words (SERVE 63cc77014e: "Deleted; its 2 shared links stopped working.");
        # an answer without them (an older server) reads its served fields, labelled, never a blank line.
        # AUDIT-WORDS M33: as the served words say it, never "shares revoked: N"
        n = int(env.get("shares_revoked") or 0)
        words = (
            f"Deleted; its {n} shared link{'s' if n != 1 else ''} stopped working."
            if n
            else "Deleted."
        )
    link = (env.get("links") or {}).get("app") or (
        _app_link(ctx, study_id) if ctx is not None else None
    )
    text = "\n".join(x for x in (words, f"View in Keel: {link}" if link else "") if x)
    return study_result(env, text, size="receipt")


def _app_link(ctx, study_id: str | None) -> str:
    from ._research_commands import research_url
    from .open_in_app import app_base, research_link

    if study_id:
        return research_link("study", study_id, origin=app_base(ctx))
    return research_url(ctx)


# ── add, save, run-draft ──────────────────────────────────────────────────────


def _add(args, ctx):
    env = add_call(ctx, args["study_id"], args["add"], args.get("to"))
    return study_result(env, plan_text(env), size="receipt", mode="plan")


def _save(args, ctx):
    return _study_answer(call(ctx, "post", "/v1/research/saves", body={"plan_id": args["plan_id"]}))


def _run_draft(args, ctx):
    sid, version = args["study_id"], str(int(args["version"]))
    path = f"/v1/research/studies/{seg(sid)}/versions/{seg(version)}/run"
    return _study_answer(
        call(ctx, "post", path, body={}, params=CARD_FULL, headers=_intent_key("run-draft"))
    )


# ── watch, data, list ─────────────────────────────────────────────────────────


def _watch(args, ctx):
    path = f"/v1/research/studies/{seg(args['study_id'])}"
    params = {"version": args.get("version"), **CARD_FULL}
    env = call(ctx, "get", path, params=params)
    started = time.monotonic()
    while state_of(env) not in TERMINAL and time.monotonic() - started < _poll_budget_s(ctx):
        time.sleep(2)
        env = call(ctx, "get", path, params=params)
    return _study_answer(env)


#: 07 §3.5 (R-22): the API and CLI page 1–500 rows, default 100; MCP pages at most 25.
CLI_DATA_ROWS = 100


def _data(args, ctx):
    from .research_get import _data as read_rows

    if args.get("view") is None and not args.get("output"):
        invalid("Send view (an index or title) or output.")
    return read_rows(args, ctx, limit=CLI_DATA_ROWS)


#: R-22 (WL-AG-07, STG-SURFACES #28): the CLI lists 100 studies by default (MCP 25); the route serves 25 a page, so
#: the CLI reads on (``research_get._list``'s ``most``).
CLI_LIST_ROWS = 100


def _list(args, ctx):
    from .research_get import _list as read_list

    return read_list(
        {"cursor": args.get("cursor")}, ctx, most=int(args.get("limit") or CLI_LIST_ROWS)
    )


# ── resume, retry, steps ──────────────────────────────────────────────────────


def _resubmit(mode):
    def handler(args, ctx):
        path = f"/v1/research/run-sets/{seg(args['run_set_id'])}/{seg(mode)}"
        return _study_answer(call(ctx, "post", path, body={}, headers=_intent_key(mode)))

    return handler


def _steps(args, ctx):
    env = call(ctx, "get", "/v1/research/steps", params={"name": args.get("name")})
    # AUDIT S3 #8 (07 §3.2 rule 11): no internals: the catalog's id ("keel.research.steps/1#43") is not the
    # reader's; the count is
    lines = [f"Steps ({env.get('steps_total')})"]
    for step in env.get("steps") or []:
        # AUDIT-WORDS M29: the step by its label, its name only as the handle a study document takes
        label, name = step.get("label") or step.get("name"), step.get("name")
        handle = f" ({name})" if name and name != label else ""
        about = step.get("about")
        lines.append(f"- {label}{handle}: {about}" if about else f"- {label}{handle}")
    grammar = env.get("grammar")
    if isinstance(grammar, dict):
        # K15 (01 §4.9, RI-092): the listing's grammar is in the answer's `grammar`; its words are the help topic's.
        # AUDIT-WORDS M30: no field name; the call that reads the grammar (the CLI prints its command)
        lines.append(
            f"The study document's grammar ({len(grammar.get('references') or [])} reference forms and "
            f"{len(grammar.get('rules') or {})} rules with their fixes): "
            'keel_help(topic="research_steps") reads it.'
        )
    lines.append(f"View in Keel: {_app_link(ctx, None)}")
    return study_result(env, "\n".join(lines), size="view")


# ── copy, projects, set aside, restore, archive, delete, shares ──────────────


def _copy(args, ctx):
    env = call(
        ctx,
        "post",
        f"/v1/research/shares/{seg(args['share_id'])}/copies",
        body={"name": args.get("name")},
    )
    return _receipt(env, "words", ctx=ctx)


#: 08's project item kinds (R07-11) by the ref's own prefix; any other ref names its kind with --ref-kind.
_REF_KINDS = {"stu_": "study", "str_": "strategy", "dep_": "paper_run"}


def _item_body(args) -> dict:
    """``{ref_kind, ref}`` as ``POST``/``DELETE /v1/research/projects/{prj}/items`` take them (RI-178): the kind is
    the one sent, else the one the ref's prefix names; an output or a screen sends --ref-kind."""
    ref = args.get("ref")
    if not ref:
        invalid("Send --ref.")
    kind = args.get("ref_kind") or next(
        (k for prefix, k in _REF_KINDS.items() if str(ref).startswith(prefix)), None
    )
    if kind is None:
        # AUDIT-WORDS M31: the flag's values as values
        invalid("Send --ref-kind for this ref: study, strategy, output, screen or paper_run.")
    return {"ref_kind": kind, "ref": ref}


def _project(args, ctx):
    action = args["action"]
    if action == "create":
        if not args.get("name"):
            invalid("A new project needs --name.")
        env = call(ctx, "post", "/v1/research/projects", body={"name": args["name"]})
    else:
        if not args.get("project_id"):
            invalid("Send --project-id.")
        path = f"/v1/research/projects/{seg(args['project_id'])}"
        if action == "rename":
            env = call(ctx, "patch", path, body={"name": args.get("name")})
        elif action == "archive":
            env = call(ctx, "patch", path, body={"archived": True})
        elif action == "add":
            env = call(ctx, "post", f"{path}/items", body=_item_body(args))
        else:
            env = call(ctx, "delete", f"{path}/items", body=_item_body(args))
    if "project_id" in env and "items" in env:
        # A project write answers with the project as it now reads (ProjectResponse, which serves no words): its
        # text is the project read's, ids included (WL-AG-02).
        from .research_get import project_url

        link = project_url(env["project_id"], ctx)
        return study_result(
            {**env, "links": {"app": link}},
            project_text(env, app_url=link),
            size="receipt",
            mode="project",
        )
    return _receipt(env, "words", "said", ctx=ctx)


def _set_aside(args, ctx):
    path = f"/v1/research/studies/{seg(args['study_id'])}/set-aside"
    env = call(ctx, "post", path, body={"reason": args["reason"]})
    return _receipt(env, "said", "words", ctx=ctx, study_id=args["study_id"])


def _restore(args, ctx):
    path = f"/v1/research/studies/{seg(args['study_id'])}/restore"
    return _receipt(
        call(ctx, "post", path, body={}), "said", "words", ctx=ctx, study_id=args["study_id"]
    )


def _archive(args, ctx):
    path = f"/v1/research/studies/{seg(args['study_id'])}/archive"
    env = call(ctx, "post", path, body={"archived": not args.get("undo")})
    return _receipt(env, "said", "words", ctx=ctx, study_id=args["study_id"])


def _delete(args, ctx):
    path = f"/v1/research/studies/{seg(args['study_id'])}"
    params = {"revoke_shares": True} if args.get("revoke_shares") else None
    # A deleted study has no page: its receipt links the Research home.
    return _receipt(call(ctx, "delete", path, params=params), "said", "words", ctx=ctx)


def _shares(args, ctx):
    env = call(ctx, "get", f"/v1/research/studies/{seg(args['study_id'])}/shares")
    rows = env.get("data") or env.get("items") or []
    # AUDIT-WORDS M32: in words, the study named by its page link below
    lines = [f"Shared links of this study: {len(rows)}"]
    for row in rows:
        lines.append(f"- {row.get('share_id')} · {row.get('url') or ''}".rstrip(" ·"))
    lines.append(f"View in Keel: {_app_link(ctx, args['study_id'])}")
    return study_result(env, "\n".join(lines), size="view")


#: 07 §2.2's CLI-only rows: (name, path, handler, properties, required, action, read).
CLI_VERBS = (
    (
        "research_add",
        ("research", "add"),
        _add,
        {
            "study_id": _STUDY_ID,
            "add": PLAN_PROPERTIES["add"],
            "to": PLAN_PROPERTIES["to"],
        },
        ["study_id", "add"],
        READ,
        True,
    ),
    (
        "research_save",
        ("research", "save"),
        _save,
        {
            "plan_id": {
                "type": "string",
                "pattern": "^pln_",
                "x-cli-positional": True,
                "description": "The plan.",
            }
        },
        ["plan_id"],
        UPDATE,
        False,
    ),
    (
        "research_run_draft",
        ("research", "run-draft"),
        _run_draft,
        {"study_id": _STUDY_ID, "version": _VERSION},
        ["study_id", "version"],
        RUN,
        False,
    ),
    (
        "research_watch",
        ("research", "watch"),
        _watch,
        {"study_id": _STUDY_ID, "version": _VERSION},
        ["study_id"],
        READ,
        True,
    ),
    (
        "research_data",
        ("research", "data"),
        _data,
        {
            k: (
                GET_PROPERTIES[k]
                if k != "limit"
                else {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Rows a page (1 to 500, default 100).",
                }
            )
            for k in (
                "study_id",
                "version",
                "view",
                "output",
                "measures",
                "where",
                "sort",
                "limit",
                "cursor",
            )
        },
        ["study_id"],
        READ,
        True,
    ),
    (
        "research_list",
        ("research", "list"),
        _list,
        {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": CLI_LIST_ROWS,
                "description": f"Studies to show (1 to {CLI_LIST_ROWS}; default {CLI_LIST_ROWS}).",
            },
            "cursor": {"type": "string", "description": "The cursor the previous page named."},
        },
        [],
        READ,
        True,
    ),
    (
        "research_resume",
        ("research", "resume"),
        _resubmit("resume"),
        {"run_set_id": _RUN_SET_ID},
        ["run_set_id"],
        RUN,
        False,
    ),
    (
        "research_retry",
        ("research", "retry"),
        _resubmit("retry"),
        {"run_set_id": _RUN_SET_ID},
        ["run_set_id"],
        RUN,
        False,
    ),
    (
        "research_steps",
        ("research", "steps"),
        _steps,
        {
            "name": {
                "type": "string",
                "description": "One step in full, by the name a study document uses (e.g. CostStress).",
            }
        },
        [],
        READ,
        True,
    ),
    (
        "research_copy",
        ("research", "copy"),
        _copy,
        {
            "share_id": _SHARE_ID,
            "name": {"type": "string", "maxLength": 120, "description": "Your copy's name."},
        },
        ["share_id"],
        UPDATE,
        False,
    ),
    (
        "research_project",
        ("research", "project"),
        _project,
        {
            "action": {
                "enum": ["create", "rename", "archive", "add", "remove"],
                "x-cli-positional": True,
                "description": "What to do with the project.",
            },
            "project_id": {
                "type": "string",
                "pattern": "^prj_",
                "description": "The project (prj_…).",
            },
            "name": {
                "type": "string",
                "maxLength": 120,
                "description": "The project's name (create, rename).",
            },
            "ref": {
                "type": "string",
                "description": "The item to add or remove: a stu_, str_ or dep_ id, or a ref.",
            },
            "ref_kind": {
                "enum": ["study", "strategy", "output", "screen", "paper_run"],
                "description": "The item's kind, when its id's prefix does not say it.",
            },
        },
        ["action"],
        UPDATE,
        False,
    ),
    (
        "research_set_aside",
        ("research", "set-aside"),
        _set_aside,
        {
            "study_id": _STUDY_ID,
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 8192,
                "description": "Why, in your words.",
            },
        },
        ["study_id", "reason"],
        UPDATE,
        False,
    ),
    (
        "research_restore",
        ("research", "restore"),
        _restore,
        {"study_id": _STUDY_ID},
        ["study_id"],
        UPDATE,
        False,
    ),
    (
        "research_archive",
        ("research", "archive"),
        _archive,
        {
            "study_id": _STUDY_ID,
            "undo": {
                "type": "boolean",
                "default": False,
                "description": "Bring an archived study back.",
            },
        },
        ["study_id"],
        UPDATE,
        False,
    ),
    (
        "research_delete",
        ("research", "delete"),
        _delete,
        {
            "study_id": _STUDY_ID,
            "revoke_shares": {
                "type": "boolean",
                "default": False,
                "description": "Also revoke its shares.",
            },
        },
        ["study_id"],
        UPDATE,
        False,
    ),
    (
        "research_shares",
        ("research", "shares"),
        _shares,
        {"study_id": _STUDY_ID},
        ["study_id"],
        "sharing.read",
        True,
    ),
)

#: WL-AG-15 (A3 #6, 07 §3.2 rule 11): `keel research …` help is written for the person at the terminal: one line
#: per verb (no spec citation; the MCP descriptions are written for an agent, "Use when the user asks …", and were
#: cut at their first sentence), and one example per plan form. Each verb's flags carry their own lines (the
#: research input schemas' descriptions, `research_cli`'s CLI-only properties).
RESEARCH_CLI_HELP: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = {
    ("research", "plan"): (
        "Plan a study: what would run, what is reused, the time and the versions tried. Nothing runs.",
        (
            "Check a strategy:   keel research plan --template check --subjects 'Strategy(\"str_…@3\")'",
            "Study a signal:     keel research plan --template signal "
            '--subjects \'Output("keel/momentum_7d@1", output="momentum_7d")\'',
            "Map its settings:   keel research plan --template map --subjects 'Strategy(\"str_…@3\")' "
            '--axes \'SettingAxis("step[2].period", [10, 20, 30], name="lookback")\'',
            "From a document:    keel research plan --source-file study.study",
            "Edit a study:       keel research plan stu_… --source-file study.study --base '{…}'",
            "Add a subject:      keel research plan stu_… --add 'Strategy(\"keel/btc_hold@1\")'",
            "Run a shared study: keel research plan --share-id <23-character id> --subjects 'Strategy(\"str_…@3\")'",
            "Plan again:         keel research plan --from-plan pln_…",
            "Resume a stop:      keel research plan stu_… --resume",
        ),
    ),
    ("research", "run"): (
        "Run a plan: save the study and run what is new, then wait briefly for it.",
        ("keel research run pln_…",),
    ),
    ("research", "get"): (
        "Read a study, a shared study, a chart's rows, a subject's addresses, a project or Keel's library.",
        (
            "keel research get stu_…",
            "keel research get --library signals",
            "keel research get --subject 'Strategy(\"str_…@3\")'",
        ),
    ),
    ("research", "view-set"): ("Change a study's title, bars or charts. Reruns nothing.", ()),
    ("research", "brief"): (
        "What you have studied: projects, studies, notes and versions tried.",
        (),
    ),
    ("research", "note"): ("Keep a hypothesis, a decision or a note on a study or a project.", ()),
    ("research", "cancel"): (
        "Stop a running study. What has started finishes and is kept.",
        ("keel research cancel stu_…", "keel research cancel stu_… --version 2"),
    ),
    ("research", "make-signal"): ("Keep a study's mix of signals as a new signal.", ()),
    ("research", "add"): (
        "Plan adding a subject or a reference to a study.",
        ("keel research add stu_… --add 'Strategy(\"keel/btc_hold@1\")'",),
    ),
    ("research", "save"): (
        "Save a plan's study without running it.",
        ("keel research save pln_…",),
    ),
    ("research", "run-draft"): (
        "Run a study's saved Draft.",
        ("keel research run-draft stu_… --version 2",),
    ),
    ("research", "watch"): ("Wait for a running study and show it when it is done.", ()),
    ("research", "data"): (
        "A chart's rows, 100 at a time.",
        ("keel research data stu_… --view 1",),
    ),
    ("research", "list"): ("Your studies, newest first.", ()),
    ("research", "resume"): ("Resume a stopped run.", ("keel research resume rus_…",)),
    ("research", "retry"): (
        "Retry the runs that didn't finish on our side (free).",
        ("keel research retry rus_…",),
    ),
    ("research", "steps"): (
        "The steps a study can run; --name shows one in full.",
        ("keel research steps", "keel research steps --name CostStress"),
    ),
    ("research", "copy"): (
        "Make a shared study yours, with nothing run.",
        ("keel research copy <23-character share id> --name 'My copy'",),
    ),
    ("research", "project"): (
        "Create, rename or archive a project, or add and remove its items.",
        (
            "keel research project create --name 'Funding ideas'",
            "keel research project add --project-id prj_… --ref stu_…",
        ),
    ),
    ("research", "set-aside"): ("Set a study aside, with your reason.", ()),
    ("research", "restore"): ("Bring back a study you set aside.", ()),
    ("research", "archive"): ("Archive a study (--undo brings it back).", ()),
    ("research", "delete"): (
        "Delete a study: its versions, runs and notes. A study with active shares is kept unless --revoke-shares.",
        ("keel research delete stu_…", "keel research delete stu_… --revoke-shares"),
    ),
    ("research", "shares"): ("A study's shares.", ()),
}

TOOLS = [
    register_research(
        name,
        path,
        RESEARCH_CLI_HELP[path][0],
        props,
        required,
        action,
        handler,
        read=read,
        title=" ".join(path).title(),
        cli_only=True,
        destructive=name == "research_delete",
    )
    for name, path, handler, props, required, action, read in CLI_VERBS
]

# `read_source` is the plan forms' local-file twin; the CLI's plan and run take it.
__all__ = ["CLI_VERBS", "RESEARCH_CLI_HELP", "TOOLS", "plan_body", "read_source"]
