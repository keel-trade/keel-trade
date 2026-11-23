"""Copy a source-visible shared screen into a new draft. The original remains unchanged."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._plan_fit import NARROW_PARAM, narrowed_fields
from ._screener_commands import SID, S, register_command, route


REQUIRED_ACTION = "strategy.create"
_FORK = route("post", "/public/screener-shares/{share_id}/fork")


def _copy(args, ctx):
    """The fork route's answer, plus what the coin cap kept when the copy was narrowed (spec 02 §C4.3a: the
    copy's first commit is the narrowed source and its run is submitted; ``run_refused`` when backtests are out)."""
    result = _FORK(args, ctx)
    result.extra.update(narrowed_fields(result.extra))
    return result


TOOL = register_command(
    "screener_copy",
    ("screener", "copy"),
    "Copy a source-visible shared screen into a new draft. The original remains unchanged.",
    {"share_id": SID, "name": S, "narrow_to_plan": NARROW_PARAM},
    ["share_id"],
    REQUIRED_ACTION,
    _copy,
    title="Copy Shared Screen",
    cli_only=True,
)
