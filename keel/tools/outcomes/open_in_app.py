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
    "res_": "result",
    # A running strategy's page (Q-2273 L6): `keel_live_monitor`'s and the
    # listed connection check's hints send a deployment here, and
    # `app_url_for("live", …)` always built its link — only the prefix was
    # missing, so every such hint ended in "unknown prefix".
    "dep_": "live",
    "shr_": "share",
    # A study's page (07 §5.1, 06 P1: r={n}[.{rev}], v, sel).
    "stu_": "study",
    # AUDIT S3 #7: a research project's page (link_routes.json's `project`, /research/projects/{id}); a project
    # read's "View in Keel" links it, and this tool refused its id as an unknown prefix.
    "prj_": "project",
}

#: A real share id — `libs/strategy_registry/share_links.py`
#: `_generate_share_id`: `secrets.token_urlsafe(16)[:21]`, an UNPREFIXED
#: 21-character URL-safe token (Q-2080 / FINDINGS F7). Until 2026-10-01 this
#: tool recognised only `shr_…`, so every share id `keel_share_create` ever
#: returned was refused here as an unknown prefix. Platform ids are
#: `<kind>_<ulid>` and ≥ 30 characters, so the exact length discriminates.
_SHARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{21}")
#: A shared STUDY's id (08 §3.2): 23 URL-safe characters with no prefix. D-341 (1): its link is the public share
#: page ``/share/{id}`` (keel-api's share read serves that as ``links.app``; proven signed out on staging), the same
#: page a strategy share opens; ``/research/{id}`` is the signed-in app route and sends a signed-out reader to sign-in.
#: A3 #4: the share read's "Make it yours" call is ``keel_app_link(id=<share id>)``, and until WL-AG-06 a
#: 23-character id answered "unknown prefix".
_STUDY_SHARE_RE = re.compile(r"[A-Za-z0-9_-]{23}")

# Envelope `target_kind` labels (existing wire contract, unchanged).
TARGET_KINDS: dict[str, str] = {
    "strategy": "strategy_overview",
    "result": "screener_result",
    "backtest": "backtest_results",
    "share": "share_page",
    "live": "live_view",
    # RI-238 (07 §2.2 "open_in_app gains kind study"): `_ID_PREFIX_KINDS` routed
    # `stu_` here while this map had no label, so every study id answered
    # internal_error ("Unexpected error in keel_app_link: 'study'").
    "study": "study_page",
    "project": "project_page",
}


def _research_target(route_kind: str, target_id: str) -> bool:
    """A study, a project, or a shared study's 23-character id: the links that exist only with Research."""
    return route_kind in ("study", "project") or (
        route_kind == "share" and _STUDY_SHARE_RE.fullmatch(target_id) is not None
    )


def kind_for_id(target_id: str) -> str | None:
    """Route kind implied by a known id prefix or a share token's shape, else ``None``."""
    for prefix, kind in _ID_PREFIX_KINDS.items():
        if target_id.startswith(prefix):
            return kind
    if _SHARE_TOKEN_RE.fullmatch(target_id):
        return "share"
    if _STUDY_SHARE_RE.fullmatch(target_id):
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


_PROD_APP_URL = "https://app.usekeel.io"
_PROD_SHARE_ROOT = "https://usekeel.io/share"


def app_base(ctx: ToolContext) -> str:
    """The web app's origin for ``ctx``: ``KEEL_APP_URL`` or a context that
    names one wins; otherwise it follows the configured API's host, so a CLI
    pointed at staging links staging (``staging-api.…`` → ``staging-app.…``,
    ``api.…`` → ``app.…``; ``keel.config.app_origin_for_api``). An API host
    with no app twin keeps the default."""
    import os

    from keel.config import app_origin_for_api, configured_api_url

    explicit = os.environ.get("KEEL_APP_URL")
    named = str(getattr(ctx, "app_url", None) or _PROD_APP_URL)
    if explicit or named.rstrip("/") != _PROD_APP_URL:
        return (explicit or named).rstrip("/")
    # The client the call already used: its config is the API this answer came
    # from. A tool that makes no API call (keel_app_link) builds the client
    # from the same config (no request is sent).
    client = getattr(ctx, "api_client", None)
    if client is None and callable(getattr(ctx, "get_client", None)):
        try:
            client = ctx.get_client()
        except Exception:  # noqa: BLE001 — no configured client: the configured API's URL
            client = None
    api_url = getattr(getattr(client, "_config", None), "api_url", None)
    if not isinstance(api_url, str):
        api_url = configured_api_url()
    return app_origin_for_api(api_url)


def share_root(ctx: ToolContext) -> str:
    """The public share pages' root for ``ctx`` (STG-SURFACES #18): ``KEEL_SHARE_URL_ROOT`` or a context that names
    one wins; with the production app the public root (``usekeel.io/share``); any other app (staging) serves its share
    pages itself, at ``{app}/share`` (keel-app's ``/share/[id]``), where a staging CLI linked prod's root."""
    import os

    explicit = os.environ.get("KEEL_SHARE_URL_ROOT")
    named = str(getattr(ctx, "share_url_root", None) or _PROD_SHARE_ROOT)
    if explicit or named.rstrip("/") != _PROD_SHARE_ROOT:
        return (explicit or named).rstrip("/")
    base = app_base(ctx)
    return _PROD_SHARE_ROOT if base == _PROD_APP_URL else f"{base}/share"


def research_link(
    kind: str,
    target_id: str | None = None,
    *,
    origin: str,
    query: dict[str, object] | None = None,
) -> str:
    """A research link (07 §5.1, RI-251): ``origin`` plus route ``kind`` of ``research_link_routes.json``, the
    wheel's byte copy of ``libs/research/link_routes.json`` (``home``, ``study``, ``new``, ``go``, ``curated``).
    ``{id}`` is ``target_id``; the route's fixed query, then ``query``, ride through :func:`_with_query`."""
    import json
    from importlib.resources import files

    route = json.loads(files("keel.data").joinpath("research_link_routes.json").read_text())[kind]
    path = str(route["path"])
    if "{id}" in path:
        if not target_id:
            raise ValueError(f"A {kind} link needs its id (stu_… or prj_…).")
        path = path.format(id=target_id)
    return _with_query(origin.rstrip("/") + path, {**route["query"], **(query or {})})


def research_link_id(kind: str, url: str | None) -> str | None:
    """The ``{id}`` a served research link of route ``kind`` carries (``research_link_routes.json``, the one owner of
    the route), or ``None`` when ``url`` is not that route's link. A reader of served links, never a writer."""
    import json
    import re
    from importlib.resources import files
    from urllib.parse import urlsplit

    if not isinstance(url, str) or not url:
        return None
    route = json.loads(files("keel.data").joinpath("research_link_routes.json").read_text())[kind]
    head, _, tail = str(route["path"]).partition("{id}")
    found = re.fullmatch(
        re.escape(head) + r"([A-Za-z0-9_-]+)" + re.escape(tail), urlsplit(url).path
    )
    return found.group(1) if found else None


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
    ``live`` (deployment view), ``paper`` (a paper run's page),
    ``share`` (public share page, rooted at :func:`share_root`), or
    ``study`` (a study's page, ``/research/{id}``; 06 P1's ``r``/``v``/``sel``
    ride in ``query``).
    """
    # Screen, strategy and backtest links follow the configured API (J5: a
    # staging CLI linked prod; L13: so did compose's strategy link).
    # `KEEL_APP_URL`, which the hosted server always sets, still wins. Live and
    # paper links keep ctx.app_url: the frozen listed outputs pin them.
    base = app_base(ctx)
    if kind in {"result", "screener", "actions"}:
        import json
        from importlib.resources import files

        routes = json.loads(files("keel.data").joinpath("screener_link_routes.json").read_text())
        route = routes[kind]
        return _with_query(
            base + route["path"].format(id=target_id),
            {**route["query"], **(query or {})},
        )
    if kind == "strategy":
        return _with_query(f"{base}/strategies/{target_id}/edit", query)
    if kind == "backtest":
        return _with_query(f"{base}/backtests/{target_id}", {"tab": "tearsheet", **(query or {})})
    if kind == "live":
        return _with_query(f"{ctx.app_url}/live/{target_id}", query)
    if kind == "paper":
        # A paper run's page (paper-trading-v2 S06 §3.2.3). Built only by the
        # paper tools, which know the run's class; the `dep_` prefix map
        # above keeps resolving to `live`, and the app redirects a paper id
        # there to this page by its venue block (IC-01-9).
        return _with_query(f"{ctx.app_url}/paper/{target_id}", query)
    if kind == "share":
        return _with_query(f"{share_root(ctx)}/{target_id}", query)
    if kind in ("study", "project"):
        return research_link(kind, target_id, origin=base, query=query)
    raise KeelError(
        f"Cannot build an app link for kind {kind!r}.",
        error_code="unknown_link_kind",
        exit_code=2,
        suggestion="Use one of: strategy, backtest, live, share, study, project.",
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
                "running strategy's id (dep_...), a study id (stu_...), a project id (prj_...), or a share id "
                "(the token in a usekeel.io/share/<id> URL, or a shared study's 23 characters)."
            ),
        )

    route_kind = kind_for_id(target_id)
    if route_kind is None:
        raise KeelError(
            f"Cannot build an app link for id {target_id!r} — unknown prefix.",
            error_code="unknown_id_prefix",
            exit_code=2,
            suggestion=(
                "Use a strategy id (str_...), a backtest run id (btr_...), a study id "
                "(stu_...), a project id (prj_...), a running strategy's id (dep_..., from `keel_live_monitor`), or a share "
                "id (the token in a usekeel.io/share/<id> URL, as "
                "`keel_share_create` returns). `keel_strategy_search` and "
                "`keel_backtest_run` return the first two."
            ),
        )
    kind = TARGET_KINDS[route_kind]
    if _research_target(route_kind, target_id):
        from ._toolsets import research_served

        if not research_served():
            # MCP-FINAL batch 3 #6 (FINAL-LIST §D): while this caller is not served Research a study, a project or a
            # shared study has no page to link; the answer is keel-api's paused refusal, word for word (D-387).
            from keel.errors import (
                RESEARCH_PAUSED_CODE,
                RESEARCH_PAUSED_EXPECTED,
                RESEARCH_PAUSED_WORDS,
            )

            raise KeelError(
                RESEARCH_PAUSED_WORDS,
                error_code=RESEARCH_PAUSED_CODE,
                exit_code=3,
                retryable=False,
                suggestion=RESEARCH_PAUSED_EXPECTED,
            )

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


# Lead sentence is R4's policy-vetted language (research/08) and is asserted verbatim by
# tests/test_outcomes_open_in_app.py — keep it exactly; level up in the sentences that follow.
_LEAD = (
    "Returns a link to view and manage this strategy in the Keel web app. A "
    "strategy id (`str_...`) links to the strategy editor, a backtest run id (`btr_...`) "
    "to the tearsheet and charts, a running strategy's id (`dep_...`) to its "
    "page, and a share id (the token in a `usekeel.io/share/<id>` URL) "
)
LISTED_HEAD_DESCRIPTION = (
    _LEAD + "to the public share page. It returns the link as `url`; nothing is opened."
)
#: D-358 (gate E's core-flow arm): the full profile's description is the pre-Research text byte for byte; the
#: Research wording (WL-AG-06: a study id and a shared study's 23-character id) is served only while a request has
#: bound Research as served (``_base.FULL_RESEARCH_COPY``, ``_mcp_adapter.effective_description``). The handler takes
#: a ``stu_`` or a 23-character shared-study id either way (every research answer writes the call for it).
DESCRIPTION = LISTED_HEAD_DESCRIPTION
RESEARCH_DESCRIPTION = (
    _LEAD
    + "to the public share page. A study id (`stu_...`) links to the study, a project id (`prj_...`) to "
    "the project, and a shared study's 23-character id to its snapshot. It returns the link as `url`; nothing is opened."
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
        description=DESCRIPTION,
        # The listed surface is frozen with the listed flag (test_listed_screens: the flag-off head): it keeps the
        # strategy-era copy, and the research copy rides Research only (`_base.LISTED_RESEARCH_COPY`, H14).
        listed_description=LISTED_HEAD_DESCRIPTION,
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
