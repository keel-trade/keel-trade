"""Submit one run, wait within the shared budget, then read selected facts."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

import time
import uuid
from pathlib import Path

from keel.api_path import seg
from keel.errors import BACKTEST_ALLOWANCE_CLAUSE, KeelError

from . import register
from ._base import OutcomeTool, listed_schema, present_param_schema
from ._screener_commands import _with_refusals, refusal_code
from ._screener_view import SUBJECT, TERMINAL, VIEW, build_result, invalid, read_envelope
from .backtest_run import _poll_budget_s, _quota_envelope_fields


LISTED_DESCRIPTION = (
    "Run a screen — a Keel file with `Outputs(...)` in place of `Execution(...)` — and "
    "return its outputs ranked across its universe at the last closed bar, the coins its "
    "view passes, and one series; a strategy runs through `keel_backtest_run`. Top movers, "
    "carry extremes, who pays while losing: questions like these are screens. `strategy_id` "
    "runs a saved screen; `source` (with an optional `view`) an unsaved one. "
    + BACKTEST_ALLOWANCE_CLAUSE
)
#: The full profile adds the one local-file fact a hosted caller cannot act on.
DESCRIPTION = LISTED_DESCRIPTION + " `source_file` reads the source from a local file."
REQUIRED_ACTION = "backtest.screener.run"


def _handler(args, ctx):
    if sum(bool(args.get(k)) for k in ("strategy_id", "source", "source_file")) != 1:
        invalid("Exactly one strategy_id, source or source_file is required.")
    if args.get("version") is not None and not args.get("strategy_id"):
        invalid("version requires strategy_id.")
    body = {
        wire: args[key]
        for key, wire in [("start_date", "start"), ("end_date", "end")]
        if args.get(key) is not None
    }
    if args.get("version") is not None:
        # The API SubmitRun contract accepts a string version, while the
        # public CLI/MCP argument remains a positive integer.
        body["version"] = str(args["version"])
    if args.get("strategy_id"):
        path = f"/v1/screeners/{seg(args['strategy_id'])}/runs"
    else:
        source = (
            Path(args["source_file"]).read_text() if args.get("source_file") else args["source"]
        )
        if len(source.encode()) > 262144:
            invalid("Source exceeds 262144 bytes.")
        body["source"] = source
        path = "/v1/result-runs"
    client = ctx.get_client()
    # One key per call: keel-api answers a resent submit with the run it
    # already queued (one charge, one run), so a submit whose answer was lost
    # (a timeout, a 5xx) is resent once instead of being reported as unknown.
    headers = {"Idempotency-Key": f"sdk-screener-run-{uuid.uuid4()}"}
    try:
        run = client.post(path, json=body, headers=headers)
    except KeelError as err:
        if refusal_code(err) not in (None, "server_error", "error"):
            raise  # a coded refusal: the run was not queued
        run = client.post(path, json=body, headers=headers)
    rid = run.get("run_id") or run.get("result_run_id")
    if not rid:
        invalid("Run response omitted run_id.")
    # A screen run spends a backtest run (contract 5): the submit carries the
    # same `remaining` / `quota` a backtest submit does, rendered the same way
    # (`quota:` line) — read off the SUBMIT, before polling replaces `run`.
    quota = _quota_envelope_fields(run.get("quota"))
    if run.get("remaining"):
        quota["remaining"] = run["remaining"]
    started = time.monotonic()
    # After the submit, a failed read never fails the call: the run is queued
    # (and charged once), so the caller gets it in flight with its run_id to
    # read later, never an error that invites a second submit.
    try:
        if args.get("wait", True):
            while str(
                run.get("status", "")
            ).upper() not in TERMINAL and time.monotonic() - started < _poll_budget_s(ctx):
                time.sleep(2)
                run = client.get(f"/v1/result-runs/{seg(rid)}")
        if str(run.get("status", "")).upper() in TERMINAL:
            envelope = read_envelope(
                {
                    k: v
                    for k, v in {**args, "run_id": rid}.items()
                    if k in ("run_id", "ranked_by", "order", "view")
                },
                ctx,
            )
        else:
            envelope = _in_flight(run, rid, args)
    except KeelError:
        envelope = _in_flight(run, rid, args)
    result = build_result(envelope, ctx, size=args.get("present") or "receipt")
    result.extra.update(quota)
    return result


def _in_flight(run, rid, args):
    return {
        "run": {**run, "run_id": rid},
        "screener": {"strategy_id": args.get("strategy_id"), "name": run.get("name")},
        "completed": None,
    }


PROPERTIES = {k: v for k, v in SUBJECT.items() if k not in ("run_id", "share_id")}
PROPERTIES.update(
    {
        "source": {
            "type": "string",
            "maxLength": 262144,
            "description": 'Screener DSL source with Globals and Universe, e.g. Globals(target_timeframe="1d", bar_offset="12h"). Grammar: keel_help(topic="screener").',
        },
        "source_file": {"type": "string", "description": "Local screener DSL path."},
        "start_date": {"type": "string", "format": "date", "description": "Inclusive first day."},
        "end_date": {"type": "string", "format": "date", "description": "Exclusive last day."},
        "view": VIEW,
        "wait": {"type": "boolean", "default": True, "description": "Wait up to the poll budget."},
        "present": present_param_schema("`receipt`"),
    }
)
TOOL = register(
    OutcomeTool(
        name="keel_screener_run",
        cli_path=("screener", "run"),
        toolset="backtest",
        description=DESCRIPTION,
        listed_description=LISTED_DESCRIPTION,
        input_schema={"type": "object", "properties": PROPERTIES},
        listed_input_schema=listed_schema(
            "keel_screener_run", {"type": "object", "properties": PROPERTIES}
        ),
        annotations={
            "title": "Run Screen",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_with_refusals(_handler, screen=True),
        required_action=REQUIRED_ACTION,
        rate_tier="backtest",
    )
)
