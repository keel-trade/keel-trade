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


# ── Canonical app-URL routing — ONE source of truth (spec 06 R4) ─────
# `keel_open_in_app` (MCP), `keel app open` (CLI), and `keel open`
# (browser-launching CLI verb) all resolve URLs HERE. Never duplicate
# this routing elsewhere.

# Known id prefixes → route kind.
_ID_PREFIX_KINDS: dict[str, str] = {
    "str_": "strategy",
    "btr_": "backtest",
    "shr_": "share",
}

# Envelope `target_kind` labels (existing wire contract, unchanged).
TARGET_KINDS: dict[str, str] = {
    "strategy": "strategy_overview",
    "backtest": "backtest_results",
    "share": "share_page",
    "live": "live_view",
}


def kind_for_id(target_id: str) -> str | None:
    """Route kind implied by a known id prefix, else ``None``."""
    for prefix, kind in _ID_PREFIX_KINDS.items():
        if target_id.startswith(prefix):
            return kind
    return None


def app_url_for(kind: str, target_id: str, ctx: ToolContext) -> str:
    """The canonical web-app URL for one target.

    ``kind`` is one of ``strategy`` (overview = Deploy-button landing),
    ``backtest`` (tearsheet tab), ``live`` (deployment view), or
    ``share`` (public share page, rooted at ``ctx.share_url_root``).
    """
    if kind == "strategy":
        return f"{ctx.app_url}/strategies/{target_id}"
    if kind == "backtest":
        return f"{ctx.app_url}/backtests/{target_id}?tab=tearsheet"
    if kind == "live":
        return f"{ctx.app_url}/live/{target_id}"
    if kind == "share":
        return f"{ctx.share_url_root}/{target_id}"
    raise KeelError(
        f"Cannot build an app link for kind {kind!r}.",
        error_code="unknown_link_kind",
        exit_code=2,
        suggestion="Use one of: strategy, backtest, live, share.",
    )


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

    route_kind = kind_for_id(target_id)
    if route_kind is None:
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
    kind = TARGET_KINDS[route_kind]

    # Spec 09 CL-2 (review finding): while anonymous, strategy/backtest/
    # live URLs reference anon-org resources the user's future account
    # will not own — the link dead-ends at sign-in with a wrong-account
    # 404. Public share pages are org-independent and stay linkable.
    if route_kind != "share":
        from ._handoff import _is_anon_session

        if _is_anon_session():
            raise KeelError(
                "This workspace is anonymous — the web app cannot show its "
                "resources until it is claimed into an account.",
                error_code="anon_no_app_link",
                exit_code=2,
                suggestion=(
                    "Sign in first with `keel_auth_login` — your strategies "
                    "and backtests come with you automatically — then retry "
                    "this link."
                ),
            )

    url = app_url_for(route_kind, target_id, ctx)

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
        # grounded-in: open_in_app.py docstring (spec 01 R4 — on the listed
        # profile this is the ONLY bridge from the agent surface into the web
        # app; navigation not action, readOnlyHint:true) + app_url_for kinds
        # (strategy overview / backtest tearsheet / share page).
        description=(
            # Lead sentence is R4's policy-vetted language (research/08) and
            # is asserted verbatim by tests/test_outcomes_open_in_app.py —
            # keep it exactly; level up in the sentences that follow.
            "Returns a link to view and manage this strategy in the Keel web "
            "app. This is the bridge from the agent surface to the interactive "
            "product: give it a strategy id (`str_...`) for the strategy "
            "overview page, a backtest run id (`btr_...`) for the full "
            "interactive tearsheet and charts, or a share id (`shr_...`) for "
            "the public share page. Reach for it whenever the user wants to "
            "see the visual result or continue with the strategy in the app "
            "beyond what these tools do; it is read-only navigation — it "
            "builds the canonical URL and changes nothing. Present the "
            "returned `url` to the user as a clickable link. "
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
