"""Read an existing screen result through the shared API envelope."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg
from keel.errors import KeelError

from . import register
from ._base import OutcomeTool, present_choice, present_param_schema
from ._screener_commands import NOT_RUN_CODES, _with_refusals, refusal_code
from ._screener_view import SUBJECT, VIEW, build_result, invalid, read_envelope


DESCRIPTION = (
    "Show a screen's latest completed run without starting one: outputs ranked at the last "
    "closed bar, the coins its view passes, those that entered or left, and one series; a "
    "backtest is `keel_backtest_summarize`. Pass `strategy_id`, `run_id` or a published "
    "`share_id`; with none, your newest run. `view` previews control values without saving; "
    "`include_view_source=true` adds the saved view source and revision for "
    "`keel_screener_view_set`."
)

REQUIRED_ACTION = "backtest.screener.read"


def view_source(client, sid):
    """The saved view source, its revision and the recent revisions.

    Read from GET /views, which needs no run: a screen that has never run
    still has a view (Keel's default, revision 0) to edit (contract 4).
    """
    stored = client.get(f"/v1/screeners/{seg(str(sid))}/views")
    history = client.get(f"/v1/screeners/{seg(str(sid))}/views/revisions")
    return {
        "strategy_id": sid,
        "source": stored.get("source"),
        "revision": stored.get("revision", 0),
        "persisted": stored.get("persisted", False),
        # Earlier revisions `keel_screener_view_set(revert_to=…)` restores:
        # numbers and dates only (who saved each is not the agent's to read).
        "revisions": [
            {
                "revision": r.get("revision"),
                "saved_at": r.get("updated_at"),
                "label": r.get("label"),
                "origin": r.get("origin"),
            }
            for r in (history if isinstance(history, list) else [])[:10]
        ],
    }


def _handler(args, ctx):
    size = "receipt" if present_choice(args) == "receipt" else "view"
    try:
        result = build_result(read_envelope(args, ctx), ctx, size=size)
    except KeelError as err:
        # A screen that has not run yet still has a view to edit: the
        # refusal carries its source and revision when they were asked for.
        if (
            args.get("include_view_source")
            and args.get("strategy_id")
            and refusal_code(err) in NOT_RUN_CODES
        ):
            detail = err.detail if isinstance(getattr(err, "detail", None), dict) else {}
            try:
                source = view_source(ctx.get_client(), args["strategy_id"])
            except KeelError:
                source = None  # the refusal itself is the answer; never mask it
            if source is not None:
                err.detail = {**detail, "code": refusal_code(err), "view_source": source}
        raise
    if args.get("include_view_source"):
        sid = args.get("strategy_id") or (result.extra.get("screener") or {}).get("strategy_id")
        if not sid:
            invalid(
                "include_view_source needs a saved screen: pass its strategy_id "
                "(a run of an unsaved source has no saved view)."
            )
        result.extra["view_source"] = view_source(ctx.get_client(), sid)
    return result


INCLUDE_VIEW_SOURCE = {
    "type": "boolean",
    "default": False,
    "description": (
        "Also return the saved view source and its revision (a saved screen only), "
        "to edit with keel_screener_view_set."
    ),
}
PROPERTIES = {
    **SUBJECT,
    "view": VIEW,
    "include_view_source": INCLUDE_VIEW_SOURCE,
    "present": present_param_schema("`view`"),
}


TOOL = register(
    OutcomeTool(
        name="keel_screener_view",
        cli_path=("screener", "view"),
        toolset="read-only",
        description=DESCRIPTION,
        input_schema={"type": "object", "properties": PROPERTIES},
        annotations={
            "title": "View Screen",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_with_refusals(_handler, screen=True),
        required_action=REQUIRED_ACTION,
    )
)
