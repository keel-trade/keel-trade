"""Remove a published screen; followers keep its dated snapshot. Publishing is screener publish."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import SID, register_command, route


REQUIRED_ACTION = "screener.unpublish"
TOOL = register_command(
    "screener_unpublish",
    ("screener", "unpublish"),
    "Remove a published screen; followers keep its dated snapshot. Publishing is screener publish.",
    {"share_id": SID},
    ["share_id"],
    REQUIRED_ACTION,
    route("delete", "/v1/screener-shares/{share_id}"),
    destructive=True,
    toolset="share",
    title="Unpublish Screen",
    rate_tier="mutation",
)
