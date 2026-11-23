"""Add a note to a study or project; set aside or bring back a study (07 §4.8, 08 §1.3, §1.4)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# The request has no role: the server derives the author's role and source
# from the credential (08 R07-1). A set_aside note goes through the set-aside
# route and a restored note through restore, so the study's pointer moves in
# the same transaction. A note never feeds a measurement.

from keel.api_path import seg

from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import call, invalid, register_research, study_result


REQUIRED_ACTION = "backtest.research.update"
DESCRIPTION = (
    "Use when the user wants a hypothesis, a decision or a note kept on a study or project; "
    "not for changing what a study shows (keel_research_view_set). Appends one note; "
    "kind set_aside sets the study aside and restored brings it back."
)

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_note"]
PROPERTIES = dict(_SCHEMA["properties"])


def _handler(args, ctx):
    anchor, kind, body = args["anchor"], args["kind"], args["body"]
    if kind in ("set_aside", "restored"):
        if not anchor.startswith("stu_"):
            invalid("Only a study is set aside or brought back.")
        path = f"/v1/research/studies/{seg(anchor)}"
        env = (
            call(ctx, "post", f"{path}/set-aside", body={"reason": body})
            if kind == "set_aside"
            else call(ctx, "post", f"{path}/restore", body={})
        )
    else:
        env = call(
            ctx, "post", "/v1/research/notes", body={"anchor": anchor, "kind": kind, "body": body}
        )
    words = str(env.get("words") or env.get("said") or "")
    # 07 §3.2 rule 9 (A3 #12): a note's receipt ends "View in Keel", the study's page for a study's note, else
    # the Research home (a project's; the note answer serves no link).
    from ._research_commands import research_url
    from .open_in_app import app_base, research_link

    link = (
        research_link("study", anchor, origin=app_base(ctx))
        if anchor.startswith("stu_")
        else research_url(ctx)
    )
    return study_result(
        {**env, "links": env.get("links") or {"app": link}},
        f"{words}\nView in Keel: {link}",
        size="receipt",
    )


TOOL = register_research(
    "research_note",
    ("research", "note"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=False,
    rate_tier="mutation",
    title="Add Study Note",
    # D-344 (3): not destructive. A note is appended; kind set_aside moves a study to Set aside, which is
    # reversible: kind restored brings it back, and nothing is deleted.
)
