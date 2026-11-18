"""Save a study's mix as a new signal: the pipeline it measured, byte for byte; nothing runs (07 §4.11)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# A strategy Blend is refused (RESEARCH_BLEND_NOT_A_STRATEGY): it stays a
# labelled what-if (RL-10). The new signal's ref is in the answer, so the
# agent can add it to the comparison.

from keel.api_path import seg

from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import call, register_research, study_result
from ._research_view import next_call


REQUIRED_ACTION = "strategy.create"
DESCRIPTION = (
    "Use when the user wants a study's mix of signals kept as one new signal; not for "
    "combining strategies (a Blend stays a what-if). Saves the measured mix pipeline as a "
    "new signal, v1, with its provenance; nothing runs."
)

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_make_signal"]
PROPERTIES = dict(_SCHEMA["properties"])


def _handler(args, ctx):
    body = {k: args[k] for k in ("version", "subject", "name")}
    env = call(ctx, "post", f"/v1/research/studies/{seg(args['study_id'])}/make-signal", body=body)
    lines = [str(env.get("words") or "")]
    ref = env.get("ref")
    if ref:
        lines.append(
            "Add it to the comparison: "
            + next_call("keel_research_plan", {"study_id": args["study_id"], "add": [ref]})
        )
    # 07 §3.2 rule 9 (A3 #12): the receipt ends "View in Keel", the study's page (the make-signal answer serves no
    # link).
    from .open_in_app import research_link

    link = research_link(
        "study", args["study_id"], origin=str(getattr(ctx, "app_url", "https://app.usekeel.io"))
    )
    lines.append(f"View in Keel: {link}")
    return study_result(
        {**env, "links": env.get("links") or {"app": link}},
        "\n".join(x for x in lines if x),
        size="receipt",
    )


TOOL = register_research(
    "research_make_signal",
    ("research", "make-signal"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=False,
    rate_tier="mutation",
    title="Make Signal From Mix",
)
