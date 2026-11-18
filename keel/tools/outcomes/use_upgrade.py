"""Preview or move one pinned Use to a chosen version. The diff precedes an explicit save."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import DRY, SID, S, register_command, route


REQUIRED_ACTION = "strategy.update"
TOOL = register_command(
    "use_upgrade",
    ("use", "upgrade"),
    "Preview or move one pinned Use to a chosen version. The diff precedes an explicit save.",
    {"strategy_id": SID, "at": S, "to_version": {"type": "integer", "minimum": 1}, "dry_run": DRY},
    ["strategy_id", "at", "to_version"],
    REQUIRED_ACTION,
    route("post", "/v1/strategies/{strategy_id}/uses/upgrade"),
    title="Move Use to Version",
    rate_tier="mutation",
)
