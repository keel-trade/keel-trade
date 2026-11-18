"""Preview a pinned output expansion in a strategy without writing. Apply source through strategy compose."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import SID, S, register_command, route


REQUIRED_ACTION = "strategy.read"
TOOL = register_command(
    "use_preview",
    ("use", "preview"),
    "Preview a pinned output expansion in a strategy without writing. Apply source through strategy compose.",
    {"strategy_id": SID, "ref": S, "output": S, "as_": S, "at": S},
    ["strategy_id", "ref", "output"],
    REQUIRED_ACTION,
    route("post", "/v1/strategies/{strategy_id}/uses/preview"),
    read=True,
    idempotent=True,
    title="Preview Use",
)
