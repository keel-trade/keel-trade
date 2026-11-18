"""Preview or replace one Use with its renamed steps, recording its source. This does not alter that source."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import DRY, SID, S, register_command, route


REQUIRED_ACTION = "strategy.update"
TOOL = register_command(
    "use_detach",
    ("use", "detach"),
    "Preview or replace one Use with its renamed steps, recording its source. This does not alter that source.",
    {"strategy_id": SID, "at": S, "dry_run": DRY},
    ["strategy_id", "at"],
    REQUIRED_ACTION,
    route("post", "/v1/strategies/{strategy_id}/uses/detach"),
    title="Detach Use",
    rate_tier="mutation",
)
