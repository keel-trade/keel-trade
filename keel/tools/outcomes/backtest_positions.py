"""`keel_backtest_positions` — a completed run's positions, bounded (Q-1893).

Each item is ONE position, from entry to exit. Founder rename 2026-09-23:
the tool shipped as `keel_backtest_round_trips`; "round trips" did not read
to users, so the tool, its title and its envelope say "positions"
(`positions`, `position_count`, `listed_positions`). The keel-api route and
its block keep their internal names (`trips`, `round_trips`,
`listed_round_trips`); :func:`_positions_vocab` and :data:`_ENVELOPE_KEYS`
are the one place that maps them. `position_count` is the run's own
position count (keel-api `run_positions`, trade-metrics spec 01 §4.3):
null on a run that did not record one, with `position_count_note` saying
so. It includes positions still open at the window end; this tool lists
closed positions only, so the listed count can be lower. The run's era
stamp is never relayed (spec 01 §6.3).

Founder decision 2026-09-23: agents need a trade-level read to explain WHY
two runs differ (the same MACD strategy closed at 12:00 vs 00:00 UTC:
−7.1% vs +44.6% over the same 29 round trips), "but suggest calling it per
coin or for a subset time period … some have thousands of trades, we don't
want the result to return all".

So the default call is a SUMMARY (counts, win rate, P&L by asset, the best
and worst positions) and says how to narrow; an `asset` and/or `start`/`end`
scope returns one PAGE of positions (default 25, max 100) with a cursor.

**One keel-api call per read.** `GET /v1/backtests/{id}/trades`
(keel-api `utils/backtest_trades.py`, the
ONE computation owner) reads the stored `results.json` once and returns the
bounded block; this module copies it and adds the run's signed carry, read
through `_backtest_view._carry` (the one reading of the worker's funding
sign). `compare_to` is the only two-call path: the same scoped read on a
second run, aligned here by nearest entry time.

The MCP name says "positions", not "trades": `trade` is a forbidden stem
in listed-profile tool names (tests/test_policy_scan.py). The CLI verb is
`keel backtest positions`; `keel backtest trades` (the verb this shipped
under) stays as an alias — the CLI is not the listed surface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from keel.errors import KeelError, NotFoundError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._surface_hints import tool_ref
from .open_in_app import app_url_for


DEFAULT_LIMIT = 25
MAX_LIMIT = 100
#: `compare_to` pairs a position with the other run's nearest same-side entry
#: within this many hours (a bar-offset change moves entries by hours, not
#: days; a pairing wider than this is two different decisions).
PAIR_WINDOW_HOURS = 72.0

_BASIS = (
    "Positions are built from the run's stored closed trades: P&L is price moves "
    "net of fees, realized exits only. Carry is not recorded per position — "
    "`carry` is the run total. A position still open at the window end is not "
    "listed, so `position_count` can be above the number listed."
)

#: Beside a null `position_count` (an Era A run, spec 01 §6.2).
_NOT_RECORDED = "Positions are not recorded for this run."


def _narrow_hint(env: dict) -> str:
    assets = [r.get("asset") for r in env.get("by_asset") or [] if isinstance(r, dict)]
    example = assets[0] if assets else "BTC"
    return (
        f"Per-asset positions: asset='{example}'. A window: start/end "
        "(YYYY-MM-DD, on entry time). Pages hold up to 100 rows; `cursor` "
        "continues one. compare_to=<backtest_id> with an asset lines two runs' "
        "positions up by entry time."
    )


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fetch(client, backtest_id: str, params: dict) -> dict:
    try:
        return client.get(f"/v1/backtests/{backtest_id}/trades", **params)
    except NotFoundError:
        raise NotFoundError(
            f"Backtest {backtest_id} not found.",
            suggestion=(
                f"Verify the backtest_id — it is the id {tool_ref('keel_backtest_run')} returned."
            ),
        )


def _carry(block: dict) -> Any:
    from ._backtest_view import _carry as carry_of

    return carry_of(
        {"funding": block.get("funding"), "funding_included": block.get("funding_included")}
    )


#: keel-api block key → this tool's envelope key (the one vocabulary map).
_ENVELOPE_KEYS = {
    "trips": "positions",
    "run_positions": "position_count",
    "listed_round_trips": "listed_positions",
}


def _positions_vocab(value: Any) -> Any:
    """A summary / by-asset row with its `round_trips` count named `positions`."""
    if isinstance(value, list):
        return [_positions_vocab(v) for v in value]
    if isinstance(value, dict) and "round_trips" in value:
        return {("positions" if k == "round_trips" else k): v for k, v in value.items()}
    return value


def _slim(trip: dict) -> dict:
    return {
        k: trip.get(k)
        for k in ("side", "entry_time", "exit_time", "entry_price", "exit_price", "pnl")
    } | {"return_pct": trip.get("return_pct")}


def pair_positions(a: list[dict], b: list[dict], *, window_hours: float = PAIR_WINDOW_HOURS):
    """Greedy nearest-entry pairing of two runs' positions for ONE asset.

    Each position pairs at most once, same side only, within ``window_hours``.
    Returns ``(pairs, only_a, only_b)``; a pair carries both positions, the
    entry gap in hours and ``pnl_diff = b.pnl - a.pnl``.
    """
    candidates = []
    for i, ta in enumerate(a):
        ea = _parse(ta.get("entry_time"))
        if ea is None:
            continue
        for j, tb in enumerate(b):
            eb = _parse(tb.get("entry_time"))
            if eb is None or tb.get("side") != ta.get("side"):
                continue
            gap = (eb - ea).total_seconds() / 3600.0
            if abs(gap) <= window_hours:
                candidates.append((abs(gap), i, j, gap))
    candidates.sort()
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for _, i, j, gap in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pa, pb = a[i].get("pnl") or 0.0, b[j].get("pnl") or 0.0
        pairs.append(
            {
                "a": _slim(a[i]),
                "b": _slim(b[j]),
                "entry_gap_hours": round(gap, 2),
                "pnl_diff": round(pb - pa, 2),
            }
        )
    only_a = [_slim(t) for k, t in enumerate(a) if k not in used_a]
    only_b = [_slim(t) for k, t in enumerate(b) if k not in used_b]
    return pairs, only_a, only_b


def _compare(client, backtest_id: str, other_id: str, params: dict, limit: int) -> dict:
    """Both runs' scoped positions (one page of up to 100 each), aligned."""
    scoped = {**params, "limit": MAX_LIMIT, "sort": "time"}
    scoped.pop("cursor", None)
    blk_a = _fetch(client, backtest_id, scoped)
    blk_b = _fetch(client, other_id, scoped)
    trips_a, trips_b = blk_a.get("trips") or [], blk_b.get("trips") or []
    pairs, only_a, only_b = pair_positions(trips_a, trips_b)
    pairs.sort(key=lambda p: (-abs(p["pnl_diff"]), p["a"]["entry_time"] or ""))
    net_a = (blk_a.get("summary") or {}).get("net_pnl")
    net_b = (blk_b.get("summary") or {}).get("net_pnl")
    out: dict[str, Any] = {
        "compare_to": other_id,
        "a": {"backtest_id": backtest_id, "summary": _positions_vocab(blk_a.get("summary"))},
        "b": {"backtest_id": other_id, "summary": _positions_vocab(blk_b.get("summary"))},
        "net_pnl_diff": (
            round(net_b - net_a, 2)
            if isinstance(net_a, (int, float)) and isinstance(net_b, (int, float))
            else None
        ),
        "pairs": pairs[:limit],
        "pairs_total": len(pairs),
        "only_in_a": only_a[:limit],
        "only_in_b": only_b[:limit],
        "pairing": (
            f"Nearest same-side entry within {PAIR_WINDOW_HOURS:g} hours; pnl_diff is "
            "b minus a; pairs ordered by the largest difference first."
        ),
    }
    if blk_a.get("cursor") or blk_b.get("cursor"):
        out["truncated"] = (
            "More than 100 positions in scope on at least one run: the first 100 "
            "by entry time are compared. A narrower start/end covers the rest."
        )
    return out


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    backtest_id = (args.get("backtest_id") or "").strip()
    if not backtest_id:
        raise ValidationError(
            "`keel_backtest_positions` needs a `backtest_id`.",
            suggestion="Pass the backtest_id that `keel_backtest_run` returned.",
        )
    asset = (args.get("asset") or "").strip() or None
    start = (args.get("start") or "").strip() or None
    end = (args.get("end") or "").strip() or None
    cursor = (args.get("cursor") or "").strip() or None
    compare_to = (args.get("compare_to") or "").strip() or None
    sort = args.get("sort") or "pnl"
    if sort not in ("pnl", "time"):
        raise ValidationError("`sort` is 'pnl' or 'time'.")
    try:
        limit = int(args.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise ValidationError("`limit` is a whole number from 1 to 100.")
    if compare_to and not asset:
        raise ValidationError(
            "`compare_to` lines two runs up for ONE asset, so it needs `asset`.",
            suggestion=(
                "Call without compare_to first: the summary's by_asset table names "
                "the assets that moved each run."
            ),
        )

    params: dict[str, Any] = {"limit": limit, "sort": sort}
    for key, value in (("asset", asset), ("start", start), ("end", end), ("cursor", cursor)):
        if value is not None:
            params[key] = value

    client = ctx.get_client()
    hero_url = app_url_for("backtest", backtest_id, ctx, query={"tab": "trades"})

    if compare_to:
        extra = _compare(client, backtest_id, compare_to, params, limit)
        extra["backtest_id"] = backtest_id
        extra["scope"] = {"asset": asset, "start": start, "end": end}
        extra["basis"] = _BASIS
        return OutcomeResult(run_id=backtest_id, hero_url=hero_url, share_url=None, extra=extra)

    block = _fetch(client, backtest_id, params)
    if not isinstance(block, dict):
        raise KeelError(f"Unexpected positions response for {backtest_id}.")

    extra: dict[str, Any] = {"backtest_id": backtest_id}
    for key in (
        "scope",
        "summary",
        "by_asset",
        "more_assets",
        "best",
        "worst",
        "sort",
        "trips",
        "cursor",
        "assets_available",
        "listed_round_trips",
    ):
        if key in block:
            extra[_ENVELOPE_KEYS.get(key, key)] = _positions_vocab(block[key])
    # The run's own position count (keel-api `run_positions`, spec 01 §4.3),
    # null when the run did not record one — said, never guessed.
    extra["position_count"] = block.get("run_positions")
    if extra["position_count"] is None:
        extra["position_count_note"] = _NOT_RECORDED
    extra["carry"] = _carry(block)
    extra["basis"] = _BASIS
    if block.get("scope") is None:
        extra["narrow"] = _narrow_hint(block)
    elif block.get("cursor"):
        extra["narrow"] = "More positions in scope: pass `cursor` for the next page."
    return OutcomeResult(run_id=backtest_id, hero_url=hero_url, share_url=None, extra=extra)


BACKTEST_POSITIONS = register(
    OutcomeTool(
        name="keel_backtest_positions",
        required_action="backtest.read",
        cli_path=("backtest", "positions"),
        toolset="backtest",
        # grounded-in: keel-api utils/backtest_trades.py (the grouping of
        # stored exit legs into positions, the summary/page bound) and the
        # founder decision quoted in the module docstring (Q-1893).
        description=(
            "The positions of ONE completed backtest, entry to exit — what drove "
            "its result. Default: a summary (count, win rate, P&L by asset, five "
            "best, five worst). `asset` and/or `start`/`end`: one page of "
            "positions (entry/exit time and price, side, entry value, net P&L, "
            "fees) and a `cursor`. `compare_to` pairs two runs' positions for one "
            "asset."
        ),
        input_schema={
            "type": "object",
            "required": ["backtest_id"],
            "properties": {
                "backtest_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "A completed run's backtest_id.",
                },
                "asset": {
                    "type": "string",
                    "description": "One asset's positions (e.g. 'BTC').",
                },
                "start": {
                    "type": "string",
                    "description": "Window start on entry time, YYYY-MM-DD (inclusive).",
                },
                "end": {
                    "type": "string",
                    "description": "Window end on entry time, YYYY-MM-DD (exclusive).",
                },
                "limit": {
                    "type": "integer",
                    "default": DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": "Maximum rows per page within a scope (1-100).",
                },
                "cursor": {
                    "type": "string",
                    "description": "The `cursor` from a previous page.",
                },
                "sort": {
                    "type": "string",
                    "enum": ["pnl", "time"],
                    "default": "pnl",
                    "description": (
                        "Row order within a scope: `pnl` = largest absolute P&L "
                        "first, `time` = entry time."
                    ),
                },
                "compare_to": {
                    "type": "string",
                    "description": (
                        "A second backtest_id; with `asset`, pairs the two runs' "
                        "positions by nearest entry time."
                    ),
                },
            },
        },
        annotations={
            "title": "Get Backtest Positions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
