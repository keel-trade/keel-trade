"""Set a published screen's one-sentence description (the share's note)."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from ._screener_commands import S, lines_result, register_command
from ._screener_view import invalid


REQUIRED_ACTION = "sharing.update"
NOTE_MAX = 280


def _handler(args, ctx):
    note = (args.get("note") or "").strip()
    if not note or len(note) > NOTE_MAX:
        invalid(f"note must be one sentence of 1–{NOTE_MAX} characters.")
    path = f"/v1/screener-shares/{seg(str(args['share_id']))}"
    client = ctx.get_client()
    current = client.get(path)
    share = client.patch(
        path, json={"expected_revision": current["presentation_revision"], "note": note}
    )
    lines = [f"Description set: {share.get('note') or note}", share["url"]]
    return lines_result(share, lines, share_url=share.get("url"))


TOOL = register_command(
    "screener_describe",
    ("screener", "describe"),
    (
        "Set a published screen's description: one plain sentence (under 280 characters) "
        "saying what the screen shows, shown on its public page."
    ),
    {
        "share_id": {**S, "x-cli-positional": True, "description": "Published screen link id."},
        "note": {**S, "maxLength": NOTE_MAX, "description": "The description sentence."},
    },
    ["share_id", "note"],
    REQUIRED_ACTION,
    _handler,
    idempotent=True,
    toolset="share",
    title="Describe Published Screen",
    cli_only=True,
)
