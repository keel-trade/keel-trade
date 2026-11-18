"""Read one block, its stored outputs, versions and source when shown. Search with blocks search."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import NONNEGATIVE_INT, SID, register_command, route


REQUIRED_ACTION = "strategy.read"
TOOL = register_command(
    "blocks_get",
    ("blocks", "get"),
    "Read one block, its stored outputs, versions and source when shown. Search with blocks search.",
    {"block": SID, "version": NONNEGATIVE_INT},
    ["block"],
    REQUIRED_ACTION,
    route("get", "/v1/blocks/{block}"),
    read=True,
    idempotent=True,
    title="Get Output Block",
)
