"""`keel_app_link` — a link into the Keel web app (spec 01 R4).

Renamed from `keel_open_in_app` on 2026-10-01 (Q-2080): "open" promised a
navigation the tool never performs — it returns a URL. The old name is a
callable alias (`_toolsets.TOOL_ALIASES`).

Pure URL construction, no API call: given a strategy id, backtest run
id, or share id, return the canonical web-app URL. On the LISTED server
profile this is the only bridge from the agent surface into the app —
the returned editor page is where the user manages the strategy
onward under their own steam (research/08: navigation, not action;
`readOnlyHint: true`; policy-vetted description).

URL bases come from server config: ``ToolContext.app_url`` (overridden
by the ``KEEL_APP_URL`` env on hosted deployments — staging points at
the staging app) and ``ToolContext.share_url_root``
(``KEEL_SHARE_URL_ROOT``). Prod defaults live on ToolContext.
"""

from __future__ import annotations

import re

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# ── Canonical app-URL routing — ONE source of truth (spec 06 R4) ─────
# `keel_app_link` (MCP), `keel app open` (CLI), and `keel open`
# (browser-launching CLI verb) all resolve URLs HERE. Never duplicate
# this routing elsewhere.

# Known id prefixes → route kind. `shr_` is the spelling this tool once
# documented; no share link has ever carried it (see `_SHARE_TOKEN_RE`), and
# it stays only so a caller that typed it still lands on the share page.
_ID_PREFIX_KINDS: dict[str, str] = {
    "str_": "strategy",
    "btr_": "backtest",
    # A running strategy's page (Q-2273 L6): `keel_live_monitor`'s and the
    # listed connection check's hints send a deployment here, and
    # `app_url_for("live", …)` always built its link — only the prefix was
    # missing, so every such hint ended in "unknown prefix".
    "dep_": "live",
    "shr_": "share",
}

#: A real share id — `libs/strategy_registry/share_links.py`
#: `_generate_share_id`: `secrets.token_urlsafe(16)[:21]`, an UNPREFIXED
#: 21-character URL-safe token (Q-2080 / FINDINGS F7). Until 2026-10-01 this
#: tool recognised only `shr_…`, so every share id `keel_share_create` ever
#: returned was refused here as an unknown prefix. Platform ids are
#: `<kind>_<ulid>` and ≥ 30 characters, so the exact length discriminates.
_SHARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{21}")

# Envelope `target_kind` labels (existing wire contract, unchanged).
TARGET_KINDS: dict[str, str] = {
    "strategy": "strategy_overview",
    "backtest": "backtest_results",
    "share": "share_page",
    "live": "live_view",
}


def kind_for_id(target_id: str) -> str | None:
    """Route kind implied by a known id prefix or a share token's shape, else ``None``."""
    for prefix, kind in _ID_PREFIX_KINDS.items():
        if target_id.startswith(prefix):
            return kind
    if _SHARE_TOKEN_RE.fullmatch(target_id):
        return "share"
    return None


def _with_query(base: str, query: dict[str, object] | None) -> str:
    """Append a query string, dropping empties.

    Parameterised links are built HERE so every app URL has one author.
    Two tools used to append their own and shipped parameters no page has
    ever read (`?tab=history`, `?compare=a..b`) — a link that works and a
    parameter that does nothing look identical from the tool's side.
    """
    items = [(k, v) for k, v in (query or {}).items() if v not in (None, "")]
    if not items:
        return base
    from urllib.parse import urlencode

    return base + "?" + urlencode([(k, str(v)) for k, v in items])


def app_url_for(
    kind: str,
    target_id: str,
    ctx: ToolContext,
    *,
    query: dict[str, object] | None = None,
) -> str:
    """The canonical web-app URL for one target.

    ``kind`` is one of ``strategy`` (the EDITOR — V-6, ratified
    2026-09-19: every strategy link lands where the work happens, and
    ``/strategies/{id}`` redirects there), ``backtest`` (tearsheet tab),
    ``live`` (deployment view), or ``share`` (public share page, rooted
    at ``ctx.share_url_root``).
    """
    if kind == "strategy":
        return _with_query(f"{ctx.app_url}/strategies/{target_id}/edit", query)
    if kind == "backtest":
        return _with_query(
            f"{ctx.app_url}/backtests/{target_id}", {"tab": "tearsheet", **(query or {})}
        )
    if kind == "live":
        return _with_query(f"{ctx.app_url}/live/{target_id}", query)
    if kind == "share":
        return _with_query(f"{ctx.share_url_root}/{target_id}", query)
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
                "Pass a strategy id (str_...), a backtest run id (btr_...), a "
                "running strategy's id (dep_...), or a share id (the token in a "
                "usekeel.io/share/<id> URL)."
            ),
        )

    route_kind = kind_for_id(target_id)
    if route_kind is None:
        raise KeelError(
            f"Cannot build an app link for id {target_id!r} — unknown prefix.",
            error_code="unknown_id_prefix",
            exit_code=2,
            suggestion=(
                "Use a strategy id (str_...), a backtest run id (btr_...), a "
                "running strategy's id (dep_..., from `keel_live_monitor`), or a share "
                "id (the token in a usekeel.io/share/<id> URL, as "
                "`keel_share_create` returns). `keel_strategy_search` and "
                "`keel_backtest_run` return the first two."
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
        name="keel_app_link",
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
            "Returns a link to view and manage this strategy in the Keel web app. A "
            "strategy id (`str_...`) links to the strategy editor, a backtest run id (`btr_...`) "
            "to the tearsheet and charts, a running strategy's id (`dep_...`) to its "
            "page, and a share id (the token in a `usekeel.io/share/<id>` URL) "
            "to the public share page. It returns the link as `url`; nothing is opened."
        ),
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Strategy id (`str_...`), backtest run id (`btr_...`), running "
                        "strategy id (`dep_...`), or share id (the token in a `usekeel.io/share/<id>` URL)."
                    ),
                },
            },
        },
        annotations={
            "title": "Get App Link",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
