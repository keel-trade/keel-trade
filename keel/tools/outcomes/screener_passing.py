"""The complete stored passing lists, text only and without a card."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from . import register
from ._base import OutcomeTool
from ._screener_view import SUBJECT, build_result, read_envelope


REQUIRED_ACTION = "backtest.screener.read"


def _handler(args, ctx):
    envelope = read_envelope(args, ctx)
    run = envelope.get("run") or {}
    rid = run.get("run_id") or run.get("result_run_id")
    passing = None
    if envelope.get("completed") is not None:
        if args.get("share_id"):
            preview = ctx.get_client().get(
                f"/v1/screener-shares/{seg(args['share_id'])}/passing", run_id=rid
            )
        else:
            preview = ctx.get_client().get(f"/v1/result-runs/{seg(rid)}/preview")
        passing = preview.get("passing")
    return build_result(envelope, ctx, full_passing=passing)


TOOL = register(
    OutcomeTool(
        name="keel_screener_passing",
        cli_path=("screener", "passing"),
        # CLI-only (screeners L13 §2b): the MCP screen view carries the passing
        # lists bounded, with the count of the rest; the whole lists stay a
        # `keel screener passing` read.
        cli_only=True,
        toolset="read-only",
        description="Reads the complete stored passing, entered and left lists for one screen result. The bounded chart and table are available through keel_screener_view; this command returns text and starts no run.",
        input_schema={"type": "object", "properties": SUBJECT},
        annotations={
            "title": "Read passing assets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        required_action=REQUIRED_ACTION,
    )
)
