"""Run a study's plan: save under RL-29, run what is new, wait briefly (07 §4.3)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# One Idempotency-Key per call, resent once on an uncoded error (the
# `screener_run.py` rule): keel-api answers a resent submit with the run set it
# already made, so a submit whose answer was lost is never charged twice. After
# the submit a failed read never fails the call (a charged run is never
# reported as an error that invites a second run).

import time
import uuid

from keel.api_path import seg
from keel.errors import RESEARCH_ALLOWANCE_CLAUSE, KeelError

from ._base import present_choice, present_param_schema
from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import (
    CARD_FULL,
    call,
    invalid,
    line_before_link,
    refusal_code,
    register_research,
    study_result,
)
from ._research_view import study_text
from .research_plan import plan_body


REQUIRED_ACTION = "backtest.research.run"
LISTED_DESCRIPTION = (
    "Use when the user has seen a study's plan and wants it run; not for one backtest "
    "(keel_backtest_run). Runs the plan_id from keel_research_plan; returns the study or its "
    "progress. " + RESEARCH_ALLOWANCE_CLAUSE
)
DESCRIPTION = LISTED_DESCRIPTION + " `source_file` reads a study document from a local file."

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_run"]
PROPERTIES = {
    **_SCHEMA["properties"],
    "source_file": {"type": "string", "description": "A local study/1 document path."},
}
PROPERTIES["present"] = present_param_schema(
    "`receipt`: one short card that shows the run's progress, then its result"
)

#: 00 §A.3's run-set states from which nothing more runs.
TERMINAL = frozenset({"completed", "completed_partial", "cancelled", "failed"})


def _poll_budget_s(ctx) -> float:
    from .backtest_run import _poll_budget_s as budget

    return budget(ctx)


def submit(ctx, body: dict) -> dict:
    """POST the run once, resent once on an uncoded error under the same key."""
    headers = {"Idempotency-Key": f"sdk-research-run-{uuid.uuid4()}"}
    # SERVE WL-SV-21: a done run's answer serves card_data when the card asks (``?card=full``), as a study read does.
    try:
        return call(ctx, "post", "/v1/research/runs", body=body, params=CARD_FULL, headers=headers)
    except KeelError as err:
        if refusal_code(err) not in (None, "server_error", "error"):
            raise  # a coded refusal: nothing ran
        return call(ctx, "post", "/v1/research/runs", body=body, params=CARD_FULL, headers=headers)


def state_of(env: dict) -> str:
    return str((env.get("study") or {}).get("state") or "")


def wait_for(ctx, env: dict, *, wait: bool) -> dict:
    """Poll the study within the shared budget; a failed read keeps what we had.

    JOURNEYS J6 (SDK-FIX): a run that is done at its answer (every run reused) still answers with the study read's
    result. keel-api's run answer serves no headline or bars (it reads no outputs), so a Map whose 28 runs were all
    reused answered "28 of 28 runs done" and nothing else: the one final read below runs for it too."""
    study_id = (env.get("study") or {}).get("study_id")
    version = (env.get("study") or {}).get("version")
    if not wait or not study_id:
        return env
    if state_of(env) in TERMINAL and (env.get("headline") or env.get("checks")):
        return env
    started = time.monotonic()
    path = f"/v1/research/studies/{seg(study_id)}"

    def read(params: dict) -> dict:
        # The submit's quota stays the call's answer about what it spent.
        got = call(ctx, "get", path, params=params)
        return {**got, **{k: env[k] for k in ("quota", "quota_notice") if k in env}}

    try:
        # AUDIT S3 #9: the polls read the state only; keel-api builds `card_data` (the chart, the check words) only on
        # the one read the answer draws, the last. A ?card=full poll every 2 s rebuilt it ~45 times a call.
        while state_of(env) not in TERMINAL and time.monotonic() - started < _poll_budget_s(ctx):
            time.sleep(2)
            env = read({"version": version})
        env = read({"version": version, **CARD_FULL})
    except KeelError:
        pass
    return env


def _handler(args, ctx):
    form = plan_body(args)
    if args.get("plan_id"):
        if form or args.get("max_runs") is not None:
            invalid("Send plan_id alone, or a plan form with max_runs.")
        body = {"plan_id": args["plan_id"]}
    else:
        if not form:
            invalid("Send plan_id, or a plan form with max_runs.")
        if args.get("max_runs") is None:
            # POLISH (MCP-FINAL S3): the refusal's next call is the valid one, the plan of this same form (its plan_id
            # then runs with no max_runs), never ``{"tool": null}``
            from keel.errors import UsageError

            err = UsageError("A direct run states max_runs (the most runs it may start).")
            err.recovery_tool = "keel_research_plan"
            err.recovery_tool_args = {
                k: v for k, v in args.items() if k not in ("max_runs", "wait", "present")
            }
            err.suggestion = "Plan it first, then run its plan_id; or send max_runs:"
            raise err
        body = {"plan": form, "max_runs": args["max_runs"]}
    env = wait_for(ctx, submit(ctx, body), wait=args.get("wait", True))
    # D-328 D5 (13 §2.1, §2.6): a run answers as the fixed-height run card, its progress or its compact result;
    # the full study card comes from keel_research_get (or the card's Expand), never by itself.
    # D-344 (9), CONFORM-MCP F12: only the enum's two sizes can be asked for (present_choice). A run's compact
    # form is the run card (D-328 D5), so `receipt` and the default both draw it; `view` asks for the full study.
    size = "view" if present_choice(args) == "view" else "progress"
    text = line_before_link(study_text(env), env.get("quota_notice"))
    return study_result(env, text, size=size, mode="run")


TOOL = register_research(
    "research_run",
    ("research", "run"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=False,
    rate_tier="backtest",
    title="Run Study",
    listed_description=LISTED_DESCRIPTION,
    listed_omit=("source_file",),
)
