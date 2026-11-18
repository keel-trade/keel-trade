"""Read owned, followed or curated screens without scheduling work."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

from keel.api_path import seg

from ._base import listed_schema
from ._screener_commands import B, lines_result, register_command
from .screener_refresh import cadence_words


REQUIRED_ACTION = "strategy.read"


def _handler(args, ctx):
    client = ctx.get_client()
    scope = args.get("scope") or (
        "following" if args.get("following") else "keel" if args.get("keel") else None
    )
    result = {}
    lines = []
    if scope in (None, "mine"):
        result["mine"] = client.get("/v1/strategies", kind="screener")
        rows = _rows(result["mine"])
        lines += ["Your screens:"] + [mine_line(client, row) for row in rows] if rows else []
    if scope in (None, "following"):
        result["following"] = client.get("/v1/screen-follows")
        rows = _rows(result["following"])
        lines += ["Following:"] + [following_line(row) for row in rows] if rows else []
    if scope == "keel":
        result["keel"] = _without_thumbs(client.get_public("/public/screeners"))
        rows = _rows(result["keel"])
        lines += ["Keel's screens:"] + [keel_line(row) for row in rows] if rows else []
    return lines_result(result, lines or ["No screens."])


#: Card-only fields of a curated row: the thumbnail series (12 coins x 60
#: values per screen) and its image. The list draws no card, so they reach
#: no one; on a text host they were most of the result (Q-3634).
CARD_ONLY = frozenset({"thumb", "card_url"})


def _without_thumbs(page):
    if isinstance(page, dict):
        return {k: _without_thumbs(v) if k == "items" else v for k, v in page.items()}
    if isinstance(page, list):
        return [
            {k: v for k, v in row.items() if k not in CARD_ONLY} if isinstance(row, dict) else row
            for row in page
        ]
    return page


def _rows(page):
    if isinstance(page, list):
        return page
    if isinstance(page, dict):
        for key in ("items", "data", "screens"):
            if isinstance(page.get(key), list):
                return page[key]
    return []


def mine_line(client, row):
    """§D1.7: ``{name} v{n} · {visibility} · {auto-refresh state} · {share url | —}``."""
    sid = row.get("strategy_id") or row.get("id")
    shares = (client.get("/v1/screener-shares", strategy_id=sid) or {}).get("items", [])
    share = shares[0] if shares else None
    refresh = client.get(f"/v1/screeners/{seg(sid)}/refresh") or {}
    schedule = refresh.get("schedule") or {}
    state = (
        "auto-refresh " + cadence_words(schedule.get("cadence_minutes"))
        if refresh.get("enabled")
        else "auto-refresh off"
    )
    visibility = ((share or {}).get("disclosure") or {}).get("visibility") or "private"
    version = row.get("current_sequence")
    name = row.get("name") + (f" v{version}" if version else "")
    about = f" · {row['description']}" if row.get("description") else ""
    return (
        f"{name}{about} · {visibility.capitalize()} · {state} · "
        f"{(share or {}).get('url') or '—'} · {sid}"
    )


def following_line(row):
    """§D1.7 Following rows: status, its date, the notice and a community copy."""
    parts = [row.get("title") or row.get("share_id"), row.get("status")]
    if row.get("status_at"):
        parts.append(str(row["status_at"])[:10])
    line = " · ".join(str(p) for p in parts if p)
    if row.get("notice"):
        line += f" · {row['notice']}"
    if row.get("community_share_id"):
        line += f" · community copy: {row['community_share_id']}"
    line += f" · {row.get('url') or '—'}"
    return line + (f" · {row['share_id']}" if row.get("title") and row.get("share_id") else "")


def keel_line(row):
    """§D1.7 keel rows: ``{title} · {sentence} · {clock_words} · {headline} · {url}``."""
    headline = row.get("headline")
    if isinstance(headline, dict):
        headline = (
            f"{headline.get('passing')} of {headline.get('of')} pass"
            if headline.get("passing") is not None
            else None
        )
    parts = [
        row.get("title"),
        row.get("sentence"),
        row.get("clock_words"),
        headline,
        row.get("url"),
        row.get("share_id"),
    ]
    return " · ".join(str(p) for p in parts if p)


DESCRIPTION = (
    "List screens: your own and the ones you follow (the default), or one `scope`: `mine`, "
    "`following` or Keel's curated `keel`. Each row gives the name, version or status, "
    "auto-refresh, link, and the id `keel_screener_view` takes. It starts no run."
)
SCOPE = {
    "type": "string",
    "enum": ["mine", "following", "keel"],
    "description": "One group only; omitted, your own and followed screens.",
}
TOOL = register_command(
    "screener_list",
    ("screener", "list"),
    DESCRIPTION,
    {"scope": SCOPE, "following": B, "keel": B},
    [],
    REQUIRED_ACTION,
    _handler,
    read=True,
    idempotent=True,
    title="List Screens",
    listed_input_schema=listed_schema(
        "keel_screener_list",
        {"type": "object", "properties": {"scope": SCOPE, "following": B, "keel": B}},
    ),
)
