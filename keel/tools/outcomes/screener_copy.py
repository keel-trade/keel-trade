"""Copy a source-visible shared screen into a new draft. The original remains unchanged."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import SID, S, register_command, route


REQUIRED_ACTION = "strategy.create"
TOOL = register_command(
    "screener_copy",
    ("screener", "copy"),
    "Copy a source-visible shared screen into a new draft. The original remains unchanged.",
    {"share_id": SID, "name": S},
    ["share_id"],
    REQUIRED_ACTION,
    route("post", "/public/screener-shares/{share_id}/fork"),
    title="Copy Shared Screen",
    cli_only=True,
)
