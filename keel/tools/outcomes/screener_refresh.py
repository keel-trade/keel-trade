"""Manage the server's refresh registration, never a local timer."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from ._screener_commands import SID, B, lines_result, register_command


REQUIRED_ACTION = "screener.auto_refresh"


def _handler(args, ctx):
    client = ctx.get_client()
    sid = args["strategy_id"]
    path = f"/v1/screeners/{seg(sid)}/refresh"
    if args.get("off") or args.get("on") is False:
        result = client.delete(path)
    else:
        shared = client.get("/v1/screener-shares", strategy_id=sid).get("items", [])
        if shared:
            commit_id = shared[0].get("commit_id")
        else:
            screen = client.get(f"/v1/strategies/{seg(sid)}")
            commit_id = screen.get("head_commit_id")
        if not commit_id:
            from ._screener_view import invalid

            invalid("Screen has no published or latest commit to refresh.")
        result = client.post(path, json={"commit_id": commit_id})
    return lines_result(result or {}, refresh_lines(result or {}))


def refresh_lines(result):
    """§D1.7: whether the screen now refreshes, and how often."""
    schedule = result.get("schedule") or {}
    if not result.get("enabled"):
        return ["Auto-refresh is off."]
    every = cadence_words(schedule.get("cadence_minutes"))
    return [f"Auto-refresh is on: it recomputes {every}, after each closed bar."]


def cadence_words(minutes):
    """``60`` → ``hourly``, ``240`` → ``every 4 hours`` (copy.cadence_words' twin)."""
    if not minutes:
        return "on its bar clock"
    for size, one, many in ((1440, "daily", "days"), (60, "hourly", "hours")):
        if minutes % size == 0:
            n = minutes // size
            return one if n == 1 else f"every {n} {many}"
    return "every minute" if minutes == 1 else f"every {minutes} minutes"


TOOL = register_command(
    "screener_refresh",
    ("screener", "refresh"),
    "Turn screen auto-refresh on or off. Plans limit how many screens refresh and how "
    "often; see `keel_plan_usage`.",
    {"strategy_id": SID, "on": {**B, "default": True}, "off": B},
    ["strategy_id"],
    REQUIRED_ACTION,
    _handler,
    idempotent=True,
    title="Set Screen Auto-Refresh",
    rate_tier="mutation",
)
