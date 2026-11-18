"""Persist only declared follower control values, never view source."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from ._screener_commands import SID, B, lines_result, register_command
from ._screener_view import invalid


REQUIRED_ACTION = "screener.follow"


def _handler(args, ctx):
    path = f"/v1/screen-follows/{seg(args['share_id'])}/view"
    client = ctx.get_client()
    if args.get("reset"):
        result = client.delete(path)
        return lines_result(result or {}, ["Your view: the author's view."])
    if not isinstance(args.get("controls"), dict):
        invalid("controls is required unless reset is true.")
    result = client.put(path, json={"controls": args["controls"]})
    controls = (result or {}).get("controls") or args["controls"]
    words = ", ".join(f"{name} = {value}" for name, value in controls.items())
    return lines_result(result or {}, [f"Your view: {words}."])


TOOL = register_command(
    "screener_follow_view_set",
    ("screener", "follow-view-set"),
    "Set your own declared control values on a followed screen, or reset to the author view.",
    {
        "share_id": SID,
        "controls": {
            "type": "object",
            "additionalProperties": {"type": ["number", "string", "boolean"]},
        },
        "reset": B,
    },
    ["share_id"],
    REQUIRED_ACTION,
    _handler,
    idempotent=True,
    toolset="share",
    title="Set Followed Screen Controls",
    cli_only=True,
)
