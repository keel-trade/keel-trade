"""Follow/unfollow a shared screen, or follow its disclosed community copy."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from ._screener_commands import SID, B, lines_result, register_command


REQUIRED_ACTION = "screener.follow"


def _handler(args, ctx):
    client = ctx.get_client()
    share = args["share_id"]
    if args.get("community"):
        result = client.post(f"/v1/screen-follows/{seg(share)}/community", json={})
    elif args.get("on", True):
        result = client.post("/v1/screen-follows", json={"share_id": share})
    else:
        result = client.delete(f"/v1/screen-follows/{seg(share)}")
        return lines_result(result or {}, [f"No longer following {share}."])
    followed = (result or {}).get("share_id") or share
    rows = (client.get("/v1/screen-follows") or {}).get("items", [])
    row = next((r for r in rows if r.get("share_id") == followed), None)
    return lines_result(result or {}, follow_lines(row, community=bool(args.get("community"))))


def follow_lines(row, *, community=False):
    """§D1.7: the followed title, its status and the link."""
    if row is None:
        return ["Following."]
    lead = "Following the community copy" if community else "Following"
    return [
        f"{lead}: {row['title']} · {row['status']}",
        f"View in Keel: {row['url']}",
    ]


TOOL = register_command(
    "screener_follow",
    ("screener", "follow"),
    "Follow a shared screen or stop following it. A removed source may offer a community copy.",
    {"share_id": SID, "on": {**B, "default": True}, "community": B},
    ["share_id"],
    REQUIRED_ACTION,
    _handler,
    idempotent=True,
    toolset="share",
    title="Follow Screen",
    rate_tier="mutation",
)
