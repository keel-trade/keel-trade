"""Save a study's title, bars and views as a presentation revision; nothing runs (07 §4.6)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# A changed evidence part is STUDY_EVIDENCE_CHANGED (01 §6.2): the fix is a
# plan of a new version. An edit answers with what the study now shows (the
# Q-3639 lesson), led by the served "Saved revision n" line.

from keel.api_path import seg

from ._base import present_choice, present_param_schema
from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import (
    call,
    invalid,
    lead_line,
    read_source,
    register_research,
    study_result,
)
from ._research_view import study_text


REQUIRED_ACTION = "backtest.research.update"
DESCRIPTION = (
    "Use when the user asks to change a study's title, bars or views; not for what it "
    "measures (a new version through keel_research_plan). Saves a presentation revision with "
    "the revision it was read at; nothing runs. `set` renames the study or changes its question "
    "alone; `revert_to` restores an earlier revision; `dry_run` checks without saving; "
    "`source_file` reads the source from a local file."
)

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_view_set"]
PROPERTIES = {
    **_SCHEMA["properties"],
    "source_file": {"type": "string", "description": "A local study/1 document path."},
}
PROPERTIES["present"] = present_param_schema("`view`")


def _handler(args, ctx):
    source = read_source(args)
    text_set = args.get("set")
    if isinstance(text_set, str):  # the CLI's --set as JSON text
        import json

        try:
            text_set = json.loads(text_set)
        except ValueError:
            invalid('--set is JSON, e.g. \'{"title": "Momentum, costs at 3x"}\'.')
    given = [
        k
        for k, v in (("source", source), ("set", text_set), ("revert_to", args.get("revert_to")))
        if v is not None
    ]
    if len(given) != 1:
        invalid(
            "Send one of source (the whole document, or its Study, Bars and Views), set (the title or question) "
            "or revert_to."
        )
    body = {
        "source": source,
        # D-191 (A3 4.6): the title or question alone; the server writes it into the head's document.
        "set": text_set,
        "expected_revision": args["expected_revision"],
        "revert_to": args.get("revert_to"),
        "dry_run": bool(ctx.dry_run or args.get("dry_run")),
        "label": args.get("label"),
    }
    env = call(ctx, "put", f"/v1/research/studies/{seg(args['study_id'])}/presentation", body=body)
    text = lead_line(study_text(env), env.get("saved"))
    return study_result(env, text, size=present_choice(args) or "view")


TOOL = register_research(
    "research_view_set",
    ("research", "view-set"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=False,
    rate_tier="mutation",
    title="Edit Study View",
)
