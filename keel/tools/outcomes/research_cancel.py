"""Stop a study's run: what has not started is cancelled and never billed (07 §4.8, 04 §4.1)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# The child in progress finishes and is kept and billed. Resuming is a plan
# of the Again form with resume (07 §4.1), then a run.

from keel.api_path import seg

from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import call, invalid, register_research, study_result
from ._research_view import next_call


REQUIRED_ACTION = "backtest.research.cancel"
DESCRIPTION = (
    "Use when the user asks to stop a study that is running; not for deleting a study. "
    "Cancels what has not started; a run in progress finishes and is kept. Takes the "
    "run_set_id, or study_id with version."
)

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_cancel"]
PROPERTIES = dict(_SCHEMA["properties"])


def _run_set_of(args, ctx) -> str:
    if args.get("run_set_id"):
        if args.get("study_id"):
            invalid("Send run_set_id, or study_id with version, not both.")
        return args["run_set_id"]
    if not args.get("study_id"):
        invalid("Send run_set_id, or study_id with version.")
    study = call(
        ctx,
        "get",
        f"/v1/research/studies/{seg(args['study_id'])}",
        params={"version": args.get("version")},
    )
    rus = (study.get("study") or {}).get("run_set_id")
    if not rus:
        invalid("That version has no run to stop.")
    return rus


def _study_of(env: dict) -> str | None:
    """The run set's study: ``study_id`` as the answer serves it (SERVE 63cc77014e), else the one its link names
    (``CancelResponse.links.app`` is the study's page, ``research_backend.study_link``), read through the study
    route of ``research_link_routes.json`` (``open_in_app.research_link_id``), never a hand-written path."""
    from .open_in_app import research_link_id

    if isinstance(env.get("study_id"), str):
        return env["study_id"]
    found = research_link_id("study", (env.get("links") or {}).get("app"))
    return found if found and found.startswith("stu_") else None


def _handler(args, ctx):
    rus = _run_set_of(args, ctx)
    env = call(ctx, "post", f"/v1/research/run-sets/{seg(rus)}/cancel", body={})
    lines = [str(env.get("words") or ""), str((env.get("progress") or {}).get("words") or "")]
    study_id = args.get("study_id") or _study_of(env)
    if study_id:
        # A3 #12: the Again form takes study_id with resume (07 §4.1); a call without it is a usage error, and a
        # cancel by run_set_id once wrote exactly that. The run set's study is read from the served link
        # (``/research/{study_id}``), the one place the cancel answer names it.
        lines.append(
            "Resume later: "
            + next_call("keel_research_plan", {"study_id": study_id, "resume": True})
        )
    link = (env.get("links") or {}).get("app")
    if link:
        lines.append(f"View in Keel: {link}")
    return study_result(env, "\n".join(x for x in lines if x), size="receipt")


TOOL = register_research(
    "research_cancel",
    ("research", "cancel"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=False,
    rate_tier="mutation",
    title="Stop Study Run",
    # D-344 (3), CONFORM-MCP F5: a cancel moves the run set to cancelled in place (resume is a new submission), as
    # keel_paper_control's stop is destructive; the CLI asks to confirm.
    destructive=True,
)
