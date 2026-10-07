"""`keel_live_monitor` — unified read-only observability for live deployments.

Per spec §4 #13: one tool, one positional `deployment_id`, one `view`
filter that selects which slice to fetch. Collapses the previous
~13 read-only `live_*` CLI commands behind a single `view=` enum.

Do NOT use to mutate state — call `keel_live_control` instead.
Do NOT use to deploy a new strategy — call `keel_live_deploy`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, ValidationError

from . import register
from ._base import (
    LISTED_ENUM_RENAMES,
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    listed_enum,
)
from .open_in_app import app_url_for


# view → (method, path-template, supports-trade-filters)
# path-template uses "{id}" placeholder; "portfolio" ignores deployment_id.
_VIEWS: dict[str, tuple[str, str]] = {
    "overview": ("GET", "/v1/deployments/{id}"),
    "positions": ("GET", "/v1/deployments/{id}/positions"),
    "equity": ("GET", "/v1/deployments/{id}/equity"),
    "pnl": ("GET", "/v1/deployments/{id}/daily-pnl"),
    "stats": ("GET", "/v1/deployments/{id}/stats"),
    "weights": ("GET", "/v1/deployments/{id}/weights"),
    "weights-history": ("GET", "/v1/deployments/{id}/weights/history"),
    "executions": ("GET", "/v1/deployments/{id}/executions"),
    "orders": ("GET", "/v1/deployments/{id}/orders"),
    "trades": ("GET", "/v1/deployments/{id}/trades"),
    "funding": ("GET", "/v1/deployments/{id}/funding"),
    "portfolio": ("GET", "/v1/deployments/portfolio/summary"),
}

# Views that take an optional `limit` query param, and keel-api's bound on
# it (`routers/live.py`: `Query(50, ge=1, le=200)` on orders / trades /
# executions, `Query(500, ge=1, le=5000)` on weights/history). The schema
# declares the widest (1-5000); a view's own cap is checked in the handler,
# so a value past it is refused with the view named instead of 422ing.
_PAGINATED_VIEWS: dict[str, int] = {
    "orders": 200,
    "trades": 200,
    "executions": 200,
    "weights-history": 5000,
}
_LIMIT_MAX = max(_PAGINATED_VIEWS.values())

#: The listed schema's spellings of three views (`_base.LISTED_ENUM_RENAMES`),
#: accepted on every profile and mapped back to the API's view names here.
_LISTED_VIEW_ALIASES: dict[str, str] = {
    listed: shared for shared, listed in LISTED_ENUM_RENAMES[("keel_live_monitor", "view")].items()
}


def canonical_view(view: str) -> str:
    """The API's view name for either spelling (`trade_history` → `trades`)."""
    return _LISTED_VIEW_ALIASES.get(view, view)


# Trade-specific filters (only forwarded when view="trades").
_TRADE_FILTERS = ("symbol", "side", "start_time", "sort_by", "sort_dir", "cursor")

#: The trades endpoint's closed sort vocabularies (`routers/live.py`
#: `list_trades`, Q-2270): declared as enums and refused here when unknown,
#: because the schema enum is not enforced by the adapter and an older
#: keel-api fell back to time-descending without a word.
_TRADE_SORTS: dict[str, tuple[str, ...]] = {
    "sort_by": ("trade_time", "notional", "closed_pnl"),
    "sort_dir": ("asc", "desc"),
}

# Executions-specific filters (only forwarded when view="executions").
#
# Q-0913 / audit A8 F-7: `expand_orders` has existed on
# `GET /v1/deployments/{id}/executions` since Wave A and NOTHING on this
# surface ever sent it, so an agent reading the timeline saw four order
# counts and no way to reach the orders behind them. `execution_run_id`
# narrows the same endpoint to one run, which is how the app fetches a
# single expanded row without re-pulling the whole list.
_EXECUTION_FILTERS = ("execution_run_id",)
_EXECUTION_FLAGS = ("expand_orders",)


_FRESHNESS: dict[str, dict[str, Any]] = {
    "positions": {
        "source": "hyperliquid_exchange",
        "mode": "on_demand_exchange_query",
        "realtime": False,
        "note": (
            "Fetched from Hyperliquid through keel-api at request time: a live read of "
            "the linked exchange account, so it can include holdings beyond this one "
            "running strategy. This is the freshest SDK live view, but it is a snapshot, "
            "not a stream."
        ),
    },
    "portfolio": {
        "source": "keel_snapshot_store",
        "mode": "latest_recorded_snapshot",
        "realtime": False,
        "note": (
            "Aggregated from Keel deployment records, trades, funding, and stored "
            "account snapshots. It can lag the web dashboard live-service stream."
        ),
    },
}

_RECORDED_STATE_NOTE = (
    "Read from Keel backend records. It updates when runners record evaluations, "
    "orders, trades, funding, or account snapshots; it is not a real-time tail."
)


def _freshness_for(view: str) -> dict[str, Any]:
    if view in _FRESHNESS:
        return dict(_FRESHNESS[view])
    return {
        "source": "keel_backend_records",
        "mode": "recorded_state",
        "realtime": False,
        "note": _RECORDED_STATE_NOTE,
    }


# Live-card curve budget (Q-1505): the same cap the backtest card uses.
_CURVE_MAX_POINTS = 240


def _compact_curve(equity: Any) -> dict[str, Any] | None:
    """Distil ``GET /v1/deployments/{id}/equity`` into the card's series.

    ``[[timestamp, equity, twr_pct], ...]`` — at most ``_CURVE_MAX_POINTS``
    points, first and last always kept, evenly strided between. Values
    are copied, never recomputed (the flow-immune ``twr_pct`` is the
    series the app's % mode renders; ``None`` stays ``None``).
    """
    if not isinstance(equity, dict):
        return None
    raw = equity.get("points")
    if not isinstance(raw, list):
        return None
    pts: list[list[Any]] = []
    for pt in raw:
        if not isinstance(pt, dict):
            continue
        ts = pt.get("timestamp")
        eq = pt.get("equity")
        if not isinstance(ts, str) or not isinstance(eq, (int, float)) or isinstance(eq, bool):
            continue
        twr = pt.get("twr_pct")
        pts.append(
            [
                ts,
                round(float(eq), 2),
                round(float(twr), 3) if isinstance(twr, (int, float)) else None,
            ]
        )
    if not pts:
        return None
    if len(pts) > _CURVE_MAX_POINTS:
        step = (len(pts) - 1) / (_CURVE_MAX_POINTS - 1)
        pts = [pts[round(i * step)] for i in range(_CURVE_MAX_POINTS - 1)] + [pts[-1]]
    out: dict[str, Any] = {"points": pts, "source_points": len(raw)}
    for key in ("baseline_value", "current_value"):
        val = equity.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = val
    return out


def _bounded_equity(data: Any) -> Any:
    """``view=equity`` with its curve capped at ``_CURVE_MAX_POINTS`` (Q-2273 L7).

    The whole curve (5,000 points measured: 78 KB of text) was handed to a
    model that can read a summary of it; the overview has always shown a
    capped curve. Points are kept as keel-api sent them — first and last
    always, evenly strided between — and ``downsampled`` says so, with the
    count served, so nothing reads as the full series. The CLI keeps the
    whole curve: its reader is a terminal or a script, not a context window.
    """
    from keel.surface import current_surface

    if current_surface() == "cli" or not isinstance(data, dict):
        return data
    points = data.get("points")
    if not isinstance(points, list) or len(points) <= _CURVE_MAX_POINTS:
        return data
    step = (len(points) - 1) / (_CURVE_MAX_POINTS - 1)
    kept = [points[round(i * step)] for i in range(_CURVE_MAX_POINTS - 1)] + [points[-1]]
    return {
        **data,
        "points": kept,
        "downsampled": {
            "points_served": len(points),
            "points_shown": len(kept),
            "how": "evenly spaced, first and last kept; the full curve is in the Keel web app",
        },
    }


def _overview_enrichment(client: Any, deployment_id: str) -> dict[str, Any]:
    """Best-effort ``stats`` + compact ``curve`` for the overview view."""
    out: dict[str, Any] = {}
    try:
        stats = client.get(f"/v1/deployments/{deployment_id}/stats")
        if isinstance(stats, dict):
            out["stats"] = stats
    except KeelError:
        pass
    try:
        curve = _compact_curve(client.get(f"/v1/deployments/{deployment_id}/equity"))
        if curve:
            out["curve"] = curve
    except KeelError:
        pass
    return out


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    # Omitted `view`: `portfolio` with no deployment, `overview` with one —
    # the one rule the schema states (Q-2080; the schema declared a
    # default of `overview` while the description promised the portfolio).
    requested_view = (args.get("view") or "").strip()
    view = canonical_view(requested_view or "overview")

    if view not in _VIEWS:
        raise ValidationError(
            f"Unknown view {view!r}. Valid views: {sorted(_VIEWS)}",
            suggestion=(
                f"Pass `view` as one of: {', '.join(sorted(_VIEWS))}. Default "
                "is 'overview' (summary metrics). Use 'positions' for current "
                "exposures, 'trades' for recent fills, 'portfolio' for the "
                "cross-deployment view."
            ),
        )

    # The portfolio summary answers `view="portfolio"`, or a call that names
    # neither a deployment nor a view. An EXPLICIT per-deployment view with no
    # deployment is refused (Q-2270): it used to fall into the portfolio
    # branch and come back as `view: portfolio`, the view nobody asked for.
    if view == "portfolio" or (deployment_id in ("", "all") and not requested_view):
        method, path = _VIEWS["portfolio"]
        effective_view = "portfolio"
        effective_id = "all"
    else:
        if deployment_id in ("", "all"):
            raise KeelError(
                f"view={requested_view!r} reads one deployment, and no `deployment_id` "
                "was given; nothing was read.",
                error_code="missing_deployment_id",
                exit_code=2,
                suggestion=(
                    "Pass `deployment_id` for that view, or view='portfolio' (or no "
                    "arguments) for the summary across every deployment."
                ),
            )
        method, path_template = _VIEWS[view]
        path = path_template.replace("{id}", deployment_id)
        effective_view = view
        effective_id = deployment_id

    # Build query params.
    params: dict[str, Any] = {}
    limit = args.get("limit")
    if limit is not None and effective_view in _PAGINATED_VIEWS:
        cap = _PAGINATED_VIEWS[effective_view]
        if int(limit) > cap:
            shown = listed_enum("keel_live_monitor", "view", [effective_view])[0]
            raise ValidationError(
                f"`limit` must be from 1 to {cap} for the {shown} view (got {int(limit)}).",
                error_code="argument_out_of_range",
                suggestion=f"Re-call with `limit` at most {cap}; nothing was read.",
                input={"limit": limit},
            )
        params["limit"] = int(limit)
    if effective_view == "trades":
        for key, allowed in _TRADE_SORTS.items():
            val = args.get(key)
            if val not in (None, "") and val not in allowed:
                raise ValidationError(
                    f"Unknown `{key}` {val!r}; it is one of {', '.join(allowed)}. "
                    "Nothing was read.",
                    error_code="invalid_argument",
                    suggestion=f"Pass `{key}` as one of: {', '.join(allowed)}.",
                    input={key: val},
                )
        for key in _TRADE_FILTERS:
            val = args.get(key)
            if val is not None and val != "":
                params[key] = val
    if effective_view == "executions":
        for key in _EXECUTION_FILTERS:
            val = args.get(key)
            if val is not None and val != "":
                params[key] = val
        for key in _EXECUTION_FLAGS:
            # Only send the flag when it is asked for — the endpoint's
            # default is False and an explicit `false` would change the
            # request signature for no behavioural gain.
            if args.get(key):
                params[key] = True

    client = ctx.get_client()
    data = client.get(path, **params) if params else client.get(path)

    # Overview enrichment for the live card (Q-1505, D2.2 render-only):
    # the deployment row alone carries no performance numbers, so the
    # default view also fetches the stats slice and a compact equity
    # series — both are existing `view`s of this same tool, read
    # best-effort (a KeelError on either simply omits that block; the
    # overview itself already succeeded).
    overview_extra: dict[str, Any] = {}
    if effective_view == "overview":
        overview_extra = _overview_enrichment(client, effective_id)
    if effective_view == "equity":
        data = _bounded_equity(data)

    from ._toolsets import is_listed_profile

    if is_listed_profile():
        # The directory connector's result (Q-2268): a deployment row is the
        # allow-list `_listed_projection.LISTED_DEPLOYMENT_FIELDS` (no org,
        # exchange-account or config id, storage key or source hash), and
        # every other payload loses `LISTED_INTERNAL_KEYS` at any depth.
        # The CLI and local server keep keel-api's rows whole.
        from ._listed_projection import listed_live_data, scrub

        data = listed_live_data(effective_view, data)
        if "stats" in overview_extra:
            overview_extra["stats"] = scrub(overview_extra["stats"])

    hero_url = (
        app_url_for("live", effective_id, ctx, query={"tab": effective_view})
        if effective_id != "all"
        else f"{ctx.app_url}/live?tab=portfolio"
    )

    # Card + per-surface render hints (spec 06 R2/R3) — render-only.
    from ._render import card_render_block

    return OutcomeResult(
        run_id=effective_id if effective_id != "all" else None,
        hero_url=hero_url,
        share_url=None,
        extra={
            "view": effective_view,
            "freshness": _freshness_for(effective_view),
            "data": data,
            **overview_extra,
            "render": card_render_block(
                "live",
                fallback_url=hero_url,
                ctx=ctx,
                embed_id=effective_id if effective_id != "all" else None,
            ),
        },
    )


LIVE_MONITOR = register(
    OutcomeTool(
        name="keel_live_monitor",
        required_action="runner.read",
        cli_path=("live", "monitor"),
        toolset="live-read",
        # grounded-in: live_monitor.py module docstring (spec §4 #13 — one
        # read-only tool, `view` enum replacing ~13 live_* reads) +
        # _FRESHNESS (positions = on-demand exchange snapshot vs recorded
        # backend state) + live_control.py D7 note (going live is a web
        # handoff, this tool only observes).
        description=(
            "Read-only observability for live deployments: overview, positions, equity, "
            "P&L, stats, weights, weights-history, executions, orders, trades, funding "
            "events, or portfolio summary — one `view` enum in place of ~13 separate live_* "
            "read endpoints. It only observes and changes no deployment; going live with a "
            "new strategy is a web-app step (`keel_live_deploy` hands off to it). With no "
            "arguments it returns the portfolio summary across all deployments; "
            "`deployment_id` narrows to one. The returned `freshness` qualifies the data: "
            "`positions` is a live read of the linked Hyperliquid account at request time "
            "(a snapshot that can include holdings beyond one deployment), while "
            "portfolio/history views are recorded backend state that can lag the web "
            "dashboard's live-service stream — not a real-time tail. Executions view: "
            "`expand_orders=true` returns "
            "the child orders behind each attempt's counts, and `execution_run_id` narrows "
            "to one bar. A row's `quality_summary` carries the `session_id` and "
            "`intent_rev` that address that episode's receipt via `keel_live_receipt`; a "
            "run with several episodes has one receipt per episode and no run-level "
            "receipt, which is what its `quality_episode_count` reports. Changing deployment "
            "state is `keel_live_control`; deploying a new strategy is `keel_live_deploy`."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": (
                        "Deployment to inspect. Optional; omitted (or 'all') with "
                        "view='portfolio' (the default in that case) fetches the portfolio "
                        "summary across every deployment."
                    ),
                    "x-cli-positional": True,
                },
                "view": {
                    "type": "string",
                    "enum": sorted(_VIEWS.keys()),
                    "description": (
                        "Which slice to fetch. Omitted: 'portfolio' with no deployment_id, "
                        "'overview' (the deployment's metadata, stats and equity curve) with "
                        "one; 'portfolio' ignores deployment_id. Every other view needs "
                        "deployment_id and is refused without one."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _LIMIT_MAX,
                    "description": (
                        "Maximum rows per page for paginated views: up to 200 for orders, "
                        "trades and executions, 5000 for weights-history. Ignored for "
                        "other views."
                    ),
                },
                "symbol": {
                    "type": "string",
                    "description": "Trades view: filter to one instrument symbol.",
                },
                "side": {
                    "type": "string",
                    "description": "Trades view: filter by trade side (BUY/SELL).",
                },
                "start_time": {
                    "type": "string",
                    "description": "Trades view: ISO-8601 lower bound on trade_time.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": list(_TRADE_SORTS["sort_by"]),
                    "description": "Trades view: sort column (default trade_time).",
                },
                "sort_dir": {
                    "type": "string",
                    "enum": list(_TRADE_SORTS["sort_dir"]),
                    "description": "Trades view: 'asc' or 'desc' (default desc).",
                },
                "cursor": {
                    "type": "string",
                    "description": "Trades view: pagination cursor.",
                },
                "expand_orders": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Executions view: also return the child orders behind each "
                        "attempt's counts (symbol, side, state, price, order_type), instead "
                        "of the counts alone."
                    ),
                },
                "execution_run_id": {
                    "type": "string",
                    "description": (
                        "Executions view: return only this execution run, still scoped to "
                        "the deployment. Paired with expand_orders it drills into one bar."
                    ),
                },
            },
        },
        annotations={
            "title": "Monitor Live Deployments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            # The `positions` view is a live read of the caller's linked
            # Hyperliquid account — an exchange Keel does not control — so
            # the tool reaches an independently controlled external system
            # (OpenAI's scan, 2026-10-01, Q-2080). Read-only, and bounded
            # to the caller's own account: the hint says where it reads.
            "openWorldHint": True,
        },
        handler=_handler,
        # ── Listed-profile copy (spec 01 R3, research/08 string rules) ──
        # Same behavior, same args, same views — policy-vetted wording
        # only (no deploy/fund/trade verbs, no routing to tools absent
        # from the listed surface). Enum values are API data, not copy.
        listed_title="Monitor Running Strategies",
        listed_description=(
            "Read-only monitoring of the strategies running on the caller's account; "
            "acting on one happens in the Keel web app (`keel_app_link` returns the "
            "link). `view` picks a slice (overview, positions, equity, pnl, stats, "
            "weights, portfolio, …); with no arguments it returns the portfolio "
            "summary, and `deployment_id` narrows to one. `freshness` qualifies the data: "
            "`positions` is a live read of the caller's linked Hyperliquid account, a "
            "snapshot that can include holdings beyond one running strategy; the other "
            "views are recorded backend state."
        ),
        listed_input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": (
                        "Running strategy to inspect. Optional; omitted (or 'all') with "
                        "view='portfolio' (the default in that case) fetches the portfolio "
                        "summary across every running strategy."
                    ),
                    "x-cli-positional": True,
                },
                "view": {
                    "type": "string",
                    # The listed spellings (`_base.LISTED_ENUM_RENAMES`); the
                    # handler accepts both.
                    "enum": listed_enum("keel_live_monitor", "view", sorted(_VIEWS.keys())),
                    "description": (
                        "Which slice to fetch. Omitted: 'portfolio' with no deployment_id, "
                        "'overview' (one running strategy's metadata, stats and equity "
                        "curve) with one; 'portfolio' ignores deployment_id. Every other view needs "
                        "deployment_id and is refused without one."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _LIMIT_MAX,
                    "description": (
                        "Maximum rows per page: up to 200 for order_history, trade_history "
                        "and executions, 5000 for weights-history. Ignored for other views."
                    ),
                },
                "symbol": {
                    "type": "string",
                    "description": "trade_history view only: filter to one instrument symbol.",
                },
                "side": {
                    "type": "string",
                    "description": "trade_history view only: filter by side.",
                },
                "start_time": {
                    "type": "string",
                    "description": "trade_history view only: ISO-8601 lower bound on its time.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": list(_TRADE_SORTS["sort_by"]),
                    "description": "trade_history view only: sort column (default trade_time).",
                },
                "sort_dir": {
                    "type": "string",
                    "enum": list(_TRADE_SORTS["sort_dir"]),
                    "description": "trade_history view only: 'asc' or 'desc' (default desc).",
                },
                "cursor": {
                    "type": "string",
                    "description": "trade_history view only: pagination cursor.",
                },
                "expand_orders": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Executions view: also return the child records behind each "
                        "attempt's counts, instead of the counts alone."
                    ),
                },
                "execution_run_id": {
                    "type": "string",
                    "description": (
                        "Executions view: return only this execution run, still scoped to "
                        "the running strategy."
                    ),
                },
            },
        },
    )
)
