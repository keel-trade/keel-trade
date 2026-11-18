"""Read the view catalog or one kind with channels, options and examples. Write source with screener view-set."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import S, register_command, route


REQUIRED_ACTION = "strategy.read"
TOOL = register_command(
    "view_describe",
    ("view", "describe"),
    "Read the view catalog or one kind with channels, options and examples. Write source with screener view-set.",
    {"kind": {**S, "x-cli-positional": True}},
    [],
    REQUIRED_ACTION,
    route("get", "/v1/views/catalog"),
    read=True,
    idempotent=True,
    title="Describe View Kinds",
    cli_only=True,
)
