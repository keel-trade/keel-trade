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
    given = args.get("run_set_id")
    if isinstance(given, str) and given.startswith("stu_") and not args.get("study_id"):
        # FINAL-JOURNEYS S3-11 (FIX-13): `keel research cancel stu_… --yes` sent the study id as a run set and keel-api
        # refused it by its pattern, with a hint about strategy source. A study id given there names that study:
        # its latest version's run (or --version's) stops, as --study-id does.
        args = {**args, "study_id": given, "run_set_id": None}
    if args.get("run_set_id"):
        if args.get("study_id"):
            invalid("Send run_set_id, or study_id with version, not both.")
        if not str(args["run_set_id"]).startswith("rus_"):
            invalid("Send a study id (stu_…) or its run's id (rus_…).")
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


def _left_to_run(env: dict) -> bool:
    """MCP-FINAL: a resume is offered only for a run set the cancel stopped with runs left (state ``cancelled`` or
    ``cancel_requested``, fewer done than its children); a cancel of a COMPLETED run set stops nothing and has nothing
    to resume, so "Resume later" there named a plan that resumes nothing."""
    if env.get("state") not in ("cancelled", "cancel_requested"):
        return False
    progress = env.get("progress") if isinstance(env.get("progress"), dict) else {}
    children, done = progress.get("children"), progress.get("done")
    if isinstance(children, int) and isinstance(done, int):
        return done < children
    return True


def _handler(args, ctx):
    rus = _run_set_of(args, ctx)
    env = call(ctx, "post", f"/v1/research/run-sets/{seg(rus)}/cancel", body={})
    # MCP-FINAL S3 (POLISH): the served sentence says the stop once ("Stopped. 0 settings done; nothing is charged
    # for the rest."); the tile's words ("Stopped at 0 of 12 settings") only when no sentence is served
    said = str(env.get("words") or "")
    lines = [said or str((env.get("progress") or {}).get("words") or "")]
    study_id = args.get("study_id") or _study_of(env)
    if study_id and _left_to_run(env):
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
