"""Plan a study: what would run, what is reused and what it spends; nothing runs (07 §4.1)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# One of 07 §4.1's eight forms, sent as is: keel-api refuses a mixture with
# `usage_error` and every study error with its STUDY_* code (07 §3.4). The
# text leads with the served words (04 §1.7); this module composes no sentence
# about runs or time (RV-E-24).

import json

from keel.api_path import seg

from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import (
    call,
    invalid,
    read_base,
    read_source,
    register_research,
    study_result,
)
from ._research_view import plan_text


REQUIRED_ACTION = "backtest.research.read"
LISTED_DESCRIPTION = (
    "Use when the user asks to check, study, compare or map a strategy or signal they "
    "already have; not for building or editing a strategy (keel_strategy_compose). "
    "Returns what would run, what is reused and the research time it spends; nothing "
    'runs. keel_help(topic="research") has the forms.'
)
DESCRIPTION = LISTED_DESCRIPTION + " `source_file` reads a study document from a local file."

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_plan"]
PROPERTIES = {
    **_SCHEMA["properties"],
    "source_file": {"type": "string", "description": "A local study/1 document path."},
}

#: The plan form's fields (07 §4.1), as keel-api's PlanRequest takes them.
PLAN_FIELDS = (
    "template",
    "subjects",
    "days",
    "split",
    "coins",
    "costs",
    "delay",
    "exposure_multiple",
    "vol_cap",
    "axes",
    "source",
    "study_id",
    "base",
    "add",
    "to",
    "share_id",
    "from_plan",
    "version",
    "resume",
    "retry",
    "cell",
)


def plan_body(args: dict) -> dict:
    """The plan form as the route takes it (``source_file`` read here)."""
    body = {k: args.get(k) for k in PLAN_FIELDS if args.get(k) is not None}
    # D-344 (1): the listed name is exposure_multiple; keel-api's PlanRequest field is `leverage`.
    if "exposure_multiple" in body:
        body["leverage"] = body.pop("exposure_multiple")
    # The again form is resume=true or retry=true; a flag left off (the CLI's False) names no form (J14.6).
    for flag in ("resume", "retry"):
        if body.get(flag) is False:
            del body[flag]
    # The CLI's --costs is text: "none", or the {fee, slippage} object as JSON (MCP sends the object itself). Text
    # that is not a JSON object goes to the route as typed, which refuses it with the field named.
    # The CLI's --cell is the {grid, at, subject?} object as JSON text, the same way (RI-463), and --base the
    # {version, revision, evidence_hash} object an edit was made on (RI-483).
    for key in ("costs", "cell", "base"):
        value = body.get(key)
        if isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                body[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    source = read_source(args)
    if source is not None:
        body["source"] = source
    return body


#: The add form's own fields (07 §4.2): a body of only these is an add, planned by the add-plan route.
ADD_FIELDS = frozenset({"study_id", "add", "to"})


def add_call(ctx, study_id: str, add: list, to: str | None = None) -> dict:
    """07 §4.2's add, planned by ``POST /v1/research/studies/{id}/add-plan`` (04 §1.7): the same recorded plan
    the plans route makes (a ``pln_`` that ``keel_research_run`` runs), plus the per-ref rows, so its served
    ``words.work`` carries what adding costs ("1 run on the same days and coins · Counts as 1 version tried").
    The CLI's ``keel research add`` and this tool's add form both take it, so the two agree (AT-2)."""
    body = {"add": list(add), "to": to}
    return call(ctx, "post", f"/v1/research/studies/{seg(study_id)}/add-plan", body=body)


#: D-341 (3): ``study_id`` with ``version`` and nothing else plans that study's never-run Draft as it is saved.
DRAFT_FIELDS = frozenset({"study_id", "version"})


def draft_body(ctx, study_id: str, version: int) -> dict:
    """D-341 (3): the edit form of a never-run Draft as it is saved, so its run is planned with no new tool and no
    source the caller cannot see (the study read keeps ``source`` out of structuredContent). Read at ``version``, the
    Draft's own ``source`` and ``base`` are sent unchanged, as keel-api's own tests plan a saved never-run study
    (``test_research_agent_contract_postgres._two_plans``: ``{"study_id", "source"}`` → 200); the base pins the
    head, so a Draft edited since is refused rather than planned over. A version that ran is refused here, in words:
    the again form (resume or retry) is what takes a version that ran."""
    read = call(ctx, "get", f"/v1/research/studies/{seg(study_id)}", params={"version": version})
    study = read.get("study") if isinstance(read.get("study"), dict) else {}
    draft = study.get("draft") if isinstance(study.get("draft"), dict) else {}
    source, base = read.get("source"), read_base(read)
    if draft.get("version") != version or study.get("version") != version:
        invalid(
            f"Version {version} of {study_id} is not a saved Draft that has not run. With a version that ran, "
            "send resume=true or retry=true."
        )
    if not isinstance(source, str) or base is None:
        invalid(f"The read of version {version} of {study_id} served no source to plan.")
    return {"study_id": study_id, "source": source, "base": base}


def _handler(args, ctx):
    body = plan_body(args)
    if set(body) == DRAFT_FIELDS:
        body = draft_body(ctx, body["study_id"], int(body["version"]))
    paged = any(args.get(key) is not None for key in ("cursor", "limit"))
    if not paged and body.get("study_id") and body.get("add") and set(body) <= ADD_FIELDS:
        env = add_call(ctx, body["study_id"], body["add"], body.get("to"))
        return study_result(env, plan_text(env), size="receipt", mode="plan")
    for key in ("cursor", "limit"):
        if args.get(key) is not None:
            body[key] = args[key]
    env = call(ctx, "post", "/v1/research/plans", body=body)
    # D-328 D1 (13 §2.1): a plan is one row on the card, so it answers at receipt size.
    return study_result(env, plan_text(env), size="receipt", mode="plan")


TOOL = register_research(
    "research_plan",
    ("research", "plan"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=True,
    title="Plan Study",
    listed_description=LISTED_DESCRIPTION,
    listed_omit=("source_file",),
)
