"""`keel_open_in_app` — navigation link into the Keel web app (spec 01 R4).

Pure URL construction, no API call: given a strategy id, backtest run
id, or share id, return the canonical web-app URL. On the LISTED server
profile this is the only bridge from the agent surface into the app —
the returned overview page is where the user manages the strategy
onward under their own steam (research/08: navigation, not action;
`readOnlyHint: true`; policy-vetted description).

URL bases come from server config: ``ToolContext.app_url`` (overridden
by the ``KEEL_APP_URL`` env on hosted deployments — staging points at
the staging app) and ``ToolContext.share_url_root``
(``KEEL_SHARE_URL_ROOT``). Prod defaults live on ToolContext.
"""

from __future__ import annotations

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    target_id = (args.get("id") or "").strip()
    if not target_id:
        raise KeelError(
            "Missing required `id`.",
            error_code="missing_id",
            exit_code=2,
            suggestion=(
                "Pass a strategy id (str_...), a backtest run id (btr_...), "
                "or a share id (shr_...)."
            ),
        )

    if target_id.startswith("str_"):
        kind = "strategy_overview"
        url = f"{ctx.app_url}/strategies/{target_id}"
    elif target_id.startswith("btr_"):
        kind = "backtest_results"
        url = f"{ctx.app_url}/backtests/{target_id}?tab=tearsheet"
    elif target_id.startswith("shr_"):
        kind = "share_page"
        url = f"{ctx.share_url_root}/{target_id}"
    else:
        raise KeelError(
            f"Cannot build an app link for id {target_id!r} — unknown prefix.",
            error_code="unknown_id_prefix",
            exit_code=2,
            suggestion=(
                "Use a strategy id (str_...), a backtest run id (btr_...), "
                "or a share id (shr_...). Find ids via `keel_strategy_search` "
                "or `keel_backtest_run`."
            ),
        )

    return OutcomeResult(
        run_id=None,
        hero_url=url,
        share_url=None,
        extra={"url": url, "target_kind": kind, "id": target_id},
    )


OPEN_IN_APP = register(
    OutcomeTool(
        name="keel_open_in_app",
        required_action="strategy.read",
        # CLI: `keel app open <id>` prints the canonical URL (hero_url).
        # spec 06's future `keel open` (which launches the browser) can
        # wrap this without a rename.
        cli_path=("app", "open"),
        toolset="read-only",
        description=(
            "Returns a link to view and manage this strategy in the Keel "
            "web app. Accepts a strategy id (`str_...`) — links to the "
            "strategy overview page; a backtest run id (`btr_...`) — links "
            "to the backtest results page; or a share id (`shr_...`) — "
            "links to the public share page. Read-only navigation: builds "
            "the canonical URL and changes nothing. Present the returned "
            "`url` to the user as a clickable link. "
            "Do NOT use to fetch strategy data or metrics — call "
            "`keel_strategy_get` or `keel_backtest_summarize` for those."
        ),
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Strategy id (str_...), backtest run id (btr_...), or share id (shr_...)."
                    ),
                },
            },
        },
        annotations={
            "title": "Open in Keel App",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
