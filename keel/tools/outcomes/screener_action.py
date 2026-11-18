"""Preview or apply one output action into an ordinary strategy. A dry run creates no commit."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from ._screener_commands import DRY, SID, S, register_command, route


REQUIRED_ACTION = "strategy.create"
TOOL = register_command(
    "screener_action",
    ("screener", "action"),
    "Preview or apply one output action into an ordinary strategy. A dry run creates no commit.",
    {
        "strategy_id": SID,
        "action": {
            "type": "string",
            "enum": [
                "test_as_strategy",
                "use_as_filter",
                "use_as_universe",
                "use_as_signal",
                "pin_list_to_book",
            ],
        },
        "output": S,
        "target_strategy_id": S,
        "expected_target_commit_id": S,
        "ref": S,
        "sizing": {"type": "string", "enum": ["forecast"]},
        "name": S,
        "names": {"type": "array", "items": S},
        "dry_run": DRY,
    },
    ["strategy_id", "action", "output"],
    REQUIRED_ACTION,
    route("post", "/v1/strategies/{strategy_id}/automate"),
    title="Turn Screen Output into Strategy",
    rate_tier="mutation",
)
