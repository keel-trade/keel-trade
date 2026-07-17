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
from ._base import OutcomeResult, OutcomeTool, ToolContext


# view → (method, path-template, supports-trade-filters)
# path-template uses "{id}" placeholder; "portfolio" ignores deployment_id.
_VIEWS: dict[str, tuple[str, str]] = {
    "overview": ("GET", "/v1/live/{id}"),
    "positions": ("GET", "/v1/live/{id}/positions"),
    "equity": ("GET", "/v1/live/{id}/equity"),
    "pnl": ("GET", "/v1/live/{id}/daily-pnl"),
    "stats": ("GET", "/v1/live/{id}/stats"),
    "weights": ("GET", "/v1/live/{id}/weights"),
    "weights-history": ("GET", "/v1/live/{id}/weights/history"),
    "executions": ("GET", "/v1/live/{id}/executions"),
    "orders": ("GET", "/v1/live/{id}/orders"),
    "trades": ("GET", "/v1/live/{id}/trades"),
    "funding": ("GET", "/v1/live/{id}/funding"),
    "portfolio": ("GET", "/v1/live/portfolio/summary"),
}

# Views that take an optional `limit` query param.
_PAGINATED_VIEWS = {"orders", "trades", "executions", "weights-history"}

# Trade-specific filters (only forwarded when view="trades").
_TRADE_FILTERS = ("symbol", "side", "start_time", "sort_by", "sort_dir", "cursor")


_FRESHNESS: dict[str, dict[str, Any]] = {
    "positions": {
        "source": "hyperliquid_exchange",
        "mode": "on_demand_exchange_query",
        "realtime": False,
        "note": (
            "Fetched from Hyperliquid through keel-api at request time. This is "
            "the freshest SDK live view, but it is a snapshot, not a stream."
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


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    view = (args.get("view") or "overview").strip()

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

    # Special-case: empty / "all" / view="portfolio" → portfolio summary.
    if view == "portfolio" or deployment_id in ("", "all"):
        method, path = _VIEWS["portfolio"]
        effective_view = "portfolio"
        effective_id = "all"
    else:
        if not deployment_id:
            raise KeelError(
                "Missing required `deployment_id` argument.",
                error_code="missing_deployment_id",
                exit_code=2,
                suggestion=(
                    "Pass deployment_id positional, or use deployment_id='all' / "
                    "view='portfolio' for portfolio summary."
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
        params["limit"] = int(limit)
    if effective_view == "trades":
        for key in _TRADE_FILTERS:
            val = args.get(key)
            if val is not None and val != "":
                params[key] = val

    client = ctx.get_client()
    data = client.get(path, **params) if params else client.get(path)

    hero_url = (
        f"{ctx.app_url}/live/{effective_id}?tab={effective_view}"
        if effective_id != "all"
        else f"{ctx.app_url}/live?tab=portfolio"
    )

    return OutcomeResult(
        run_id=effective_id if effective_id != "all" else None,
        hero_url=hero_url,
        share_url=None,
        extra={
            "view": effective_view,
            "freshness": _freshness_for(effective_view),
            "data": data,
        },
    )


LIVE_MONITOR = register(
    OutcomeTool(
        name="keel_live_monitor",
        required_action="runner.read",
        cli_path=("live", "monitor"),
        toolset="live-read",
        description=(
            "Read live deployment state: overview, positions, equity, P&L, stats, "
            "weights, weights-history, executions, orders, trades, funding events, "
            "or portfolio summary. Selects the slice via the `view` enum so one tool "
            "replaces ~13 separate live_* read endpoints. "
            "DEFAULTS: when the user asks 'how are my live deployments doing' or "
            "similar without naming one, just call with no args — returns the "
            "portfolio summary across all deployments. Pass `deployment_id` to "
            "drill into a single deployment. "
            "Returns `freshness` metadata so agents can distinguish on-demand "
            "exchange snapshots from recorded backend state; this tool is not a "
            "real-time live-service stream. "
            "Do NOT use to mutate state — call `keel_live_control` instead. "
            "Do NOT use to deploy a new strategy — call `keel_live_deploy`."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": (
                        "Deployment to inspect. Optional; omit (or pass 'all') "
                        "with view='portfolio' (the default in this case) to fetch "
                        "the portfolio summary across every deployment."
                    ),
                    "x-cli-positional": True,
                },
                "view": {
                    "type": "string",
                    "enum": sorted(_VIEWS.keys()),
                    "default": "overview",
                    "description": (
                        "Which slice to fetch. 'overview' returns the deployment "
                        "metadata; 'portfolio' ignores deployment_id."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Page size for paginated views (orders, trades, executions, "
                        "weights-history). Ignored for other views."
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
                    "description": "Trades view: sort column (notional, closed_pnl).",
                },
                "sort_dir": {
                    "type": "string",
                    "description": "Trades view: 'asc' or 'desc'.",
                },
                "cursor": {
                    "type": "string",
                    "description": "Trades view: pagination cursor.",
                },
            },
        },
        annotations={
            "title": "Monitor Live Deployments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
        # ── Listed-profile copy (spec 01 R3, research/08 string rules) ──
        # Same behavior, same args, same views — policy-vetted wording
        # only (no deploy/fund/trade verbs, no routing to tools absent
        # from the listed surface). Enum values are API data, not copy.
        listed_title="Monitor Running Strategies",
        listed_description=(
            "Read-only monitoring for strategies currently running on your "
            "account. Choose a slice with the `view` parameter (overview, "
            "positions, equity, pnl, stats, weights, portfolio, and more — "
            "see the enum). "
            "DEFAULTS: when the user asks how their running strategies are "
            "doing without naming one, just call with no args — returns the "
            "portfolio summary across all of them. Pass `deployment_id` to "
            "drill into a single one. "
            "Returns `freshness` metadata so agents can distinguish "
            "on-demand exchange snapshots from recorded backend state; this "
            "tool is not a real-time stream. "
            "Do NOT use to change the state of a running strategy — this "
            "tool only reads. Manage strategies in the Keel web app "
            "(`keel_open_in_app` returns the link)."
        ),
        listed_input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": (
                        "Running strategy to inspect. Optional; omit (or pass "
                        "'all') with view='portfolio' (the default in this "
                        "case) to fetch the portfolio summary across every "
                        "running strategy."
                    ),
                    "x-cli-positional": True,
                },
                "view": {
                    "type": "string",
                    "enum": sorted(_VIEWS.keys()),
                    "default": "overview",
                    "description": (
                        "Which slice to fetch. 'overview' returns metadata for "
                        "one running strategy; 'portfolio' ignores "
                        "deployment_id."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum rows per page for paginated history views. "
                        "Ignored for other views."
                    ),
                },
                "symbol": {
                    "type": "string",
                    "description": "History views: filter to one instrument symbol.",
                },
                "side": {
                    "type": "string",
                    "description": "History views: filter by execution side.",
                },
                "start_time": {
                    "type": "string",
                    "description": "History views: ISO-8601 lower bound on event time.",
                },
                "sort_by": {
                    "type": "string",
                    "description": "History views: sort column.",
                },
                "sort_dir": {
                    "type": "string",
                    "description": "History views: 'asc' or 'desc'.",
                },
                "cursor": {
                    "type": "string",
                    "description": "History views: pagination cursor.",
                },
            },
        },
    )
)
