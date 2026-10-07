"""`keel_account_status` — who is signed in, the quota, what this server carries.

Renamed from `keel_status` on 2026-10-01 (Q-2080): "status" alone read as
service health or strategy state to OpenAI's name scan. The old name is a
callable alias (`_toolsets.TOOL_ALIASES`).

Per spec §4 #1: "Am I authed? Which account? Cache fresh? Catalog
version vs live?"

Do NOT use to enumerate strategies (`keel_strategy_search`).
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._status_view import CAPABILITY_PHRASE


#: The live-trading route's guidance on a HOSTED server without live-write
#: (Q-1744). A hosted user cannot set an environment variable on our server,
#: so "opt in via KEEL_TOOLSETS" names a remedy that does not exist for them;
#: live actions happen in the web app, reached through `keel_app_link`
#: (the same routing as `_surface_hints.LISTED_LIVE_HINT`).
HOSTED_LIVE_ACTIONS_NEXT = (
    "Live actions on a strategy (deploy, pause, resume, stop) are done in the "
    "Keel web app — `keel_app_link` returns the link. This connector does not "
    "carry them, and nothing the user sets here changes that."
)


def _workflow_routes(
    *, live_read_loaded: bool, live_write_loaded: bool, hosted: bool = False
) -> list[dict[str, Any]]:
    """High-signal routes for first-contact agents.

    Keep this compact: `keel_account_status` is usually the first call, so it
    should steer tool choice without becoming another manual.

    ``hosted`` changes only the live-trading route's advice when live-write
    is not loaded: the env-var opt-in is a local-install lever (Q-1744).
    """
    routes: list[dict[str, Any]] = [
        {
            "name": "first_session",
            "when": "new Keel session, unknown auth state, or unfamiliar project",
            "prompt": None,
            "tools": ["keel_account_status", "keel_auth_login", "keel_help"],
            "next": [
                "Call keel_account_status first.",
                "If authenticated=false, call keel_auth_login.",
                "For strategy work, load the strategy-creation prompt before composing.",
                "To start from a verified strategy instead of composing from scratch, see keel_library_list.",
            ],
        },
        {
            "name": "research_strategy",
            "when": "create or materially edit a strategy, then produce evidence",
            "prompt": "strategy-creation",
            "tools": [
                "keel_components_search",
                "keel_components_get_many",
                "keel_strategy_compose",
                "keel_backtest_run",
                "keel_backtest_summarize",
            ],
            "next": [
                "Decompose the thesis into component roles.",
                "Search candidates, then batch-fetch full component schemas.",
                "Dry-run compose before saving; backtest only after compose succeeds.",
            ],
        },
        {
            "name": "existing_strategy_iteration",
            "when": "user names an existing strategy or wants local file edits",
            "prompt": "strategy-fork-and-iterate",
            "tools": [
                "keel_strategy_search",
                "keel_strategy_get",
                "keel_strategy_checkout",
                "keel_strategy_status",
                "keel_strategy_push",
                "keel_backtest_run",
            ],
            "next": [
                "Search or fetch the strategy first.",
                "Use checkout/status/push for local edits; backtest server HEAD.",
            ],
        },
        {
            "name": "debug_recovery",
            "when": "a tool fails, validation loops, auth breaks, or outputs look stale",
            "prompt": "recover-from-error",
            "tools": ["keel_connection_check", "keel_help", "keel_audit_list_last"],
            "next": [
                "Read the structured error envelope before trying another tool.",
                "Use keel_connection_check for environment/auth issues.",
            ],
        },
    ]

    routes.append(
        {
            "name": "live_monitoring",
            "when": ("user asks about running live strategies, portfolio state, or live positions"),
            "prompt": "portfolio-review",
            "tools": ["keel_accounts_list", "keel_live_monitor"],
            "available": live_read_loaded,
            "next": [
                "Use keel_live_monitor(deployment_id='all', view='portfolio') for the aggregate view.",
                (
                    "Use keel_live_monitor(deployment_id=<id>, view='positions') "
                    "for an on-demand Hyperliquid account snapshot."
                ),
                (
                    "Read keel_live_monitor.freshness before interpreting live data; "
                    "positions are exchange snapshots, portfolio/history views are "
                    "recorded backend state."
                ),
            ],
        }
    )

    live_route = {
        "name": "live_trading",
        "when": ("user explicitly asks to deploy, pause, resume, stop, or trigger live capital"),
        "prompt": "deploy-and-monitor",
        "tools": ["keel_accounts_list", "keel_live_deploy", "keel_live_monitor"],
        "available": live_write_loaded,
        "read_available": live_read_loaded,
        "next": [
            (
                "Opt into live write tools with "
                "KEEL_TOOLSETS=read-only,backtest,share,live-read,live-write."
            ),
            (
                "Preview first; actual deploy requires confirmation_token, "
                "--yes in agent-mode CLI, live OAuth scope, and local arming."
            ),
            (
                "Read keel_live_monitor.freshness before interpreting live data; "
                "positions are exchange snapshots, portfolio/history views are "
                "recorded backend state."
            ),
            "Use portfolio-review for existing deployment summaries.",
        ],
    }
    if hosted and not live_write_loaded:
        live_route["tools"] = ["keel_app_link", "keel_live_monitor"]
        live_route["next"] = [
            HOSTED_LIVE_ACTIONS_NEXT,
            (
                "Read keel_live_monitor.freshness before interpreting live data; "
                "positions are exchange snapshots, portfolio/history views are "
                "recorded backend state."
            ),
        ]
    if live_write_loaded:
        live_route["tools"].append("keel_live_control")
    routes.append(live_route)
    return routes


#: The live route's `when`, in the base register (spec 02 §2.5).
LIVE_ROUTE_WHEN = "live actions on a running strategy"


def route_facts(routes: list[dict[str, Any]], loaded: Any) -> list[dict[str, Any]]:
    """`{name, when, tools}` for every route whose every tool is loaded.

    The routes' `next` / `prompt` prose was method (the chat layer's), and
    naming a tool the server does not load is a false fact about it."""
    names = set(loaded or [])
    out = []
    for route in routes:
        tools = [t for t in route.get("tools") or [] if isinstance(t, str)]
        if not tools or not set(tools) <= names:
            continue
        when = LIVE_ROUTE_WHEN if route.get("name") == "live_trading" else route.get("when")
        out.append({"name": route.get("name"), "when": when, "tools": tools})
    return out


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.config import load_config
    from keel.hosting import is_hosted

    config = load_config()
    hosted = is_hosted()
    body: dict[str, Any] = {"authenticated": bool(config.api_key)}
    if hosted:
        # On the hosted server `api_url` is the pod's IN-CLUSTER base
        # (`http://keel-api.<ns>.svc.cluster.local:8080`) — plumbing no user
        # can reach or act on (Q-1744). The user's address is the web app.
        body["app_url"] = ctx.app_url
    else:
        body["api_url"] = config.api_url

    # Always read live env — CLI ctx has empty toolsets (CLI exposes
    # every command); MCP ctx mirrors env. Read env directly so both
    # paths return the truth.
    from . import all_tools
    from ._mcp_adapter import loaded_tool_names
    from ._status_view import capabilities_for
    from ._toolsets import load_toolsets, server_profile

    profile = server_profile()
    active = load_toolsets()
    loaded = loaded_tool_names({t.name: t for t in all_tools()})
    # ONE source for what this server can do (spec 02 §2.5): the tools the
    # ACTIVE profile loads. The listed profile ignores `KEEL_TOOLSETS`, so
    # deriving from it said "live trading allowed" on a connector serving no
    # live-write tool.
    capabilities = capabilities_for(loaded)
    body["profile"] = profile
    body["capabilities"] = capabilities
    body["tools_visible"] = loaded  # envelope-only (the model has tools/list)
    body["live_monitoring_allowed"] = capabilities["monitor"] != "none"
    body["live_trading_allowed"] = capabilities["monitor"] == "read-write"
    if profile == "full":
        # Full profile only (R-22): the routes as `{name, when, tools}`,
        # filtered to routes whose every tool is loaded — no method prose.
        body["toolsets_loaded"] = sorted(active)
        body["workflow_routes"] = route_facts(
            _workflow_routes(
                live_read_loaded="live-read" in active,
                live_write_loaded="live-write" in active,
                hosted=hosted,
            ),
            loaded,
        )

    # Best-effort live identity probe — handlers fail soft on auth.
    if config.api_key:
        try:
            from keel.auth import get_identity

            me = get_identity()
            # /v1/me returns nested {principal: {id}, org: {id, name, plan}, credential_scopes: [...]}
            # — same shape `_login_summary` reads. Flat-key reads would
            # silently return None for every field (caught in the v0.4.x smoke).
            principal = me.get("principal") or {}
            org = me.get("org") or {}
            scopes = me.get("credential_scopes") or []
            body["identity"] = {
                "org_name": org.get("name"),
                "plan": org.get("plan"),
                "tier": "live" if "runner.*" in scopes else "base",
            }
            if profile == "listed":
                # The listed result names the account, never its internal
                # ids (Q-2268): an email-free display name when the server
                # serves one, else nothing beyond the org name and plan.
                label = principal.get("display_name")
                if isinstance(label, str) and label.strip() and "@" not in label:
                    body["identity"]["display_name"] = label.strip()
            else:
                # The CLI and local server keep the ids for support.
                body["identity"] = {
                    "principal_id": principal.get("id"),
                    "org_id": org.get("id"),
                    **body["identity"],
                }

            # Anonymous claim-later state (spec 05 R4): anon quota +
            # expiry + claim instructions, numbers straight from the
            # server (plan_status.remaining — never invented locally).
            if org.get("plan") == "anon" or config.anon_org_id:
                plan_status = me.get("plan_status") or {}
                metadata = org.get("metadata") or {}
                anon_meta = metadata.get("anon") or {}
                body["anonymous"] = {
                    "active": True,
                    "org_expires_at": anon_meta.get("expires_at"),
                    "remaining": plan_status.get("remaining"),
                    "limits": plan_status.get("limits"),
                    "claim": (
                        "Run `keel auth login` to keep this work — the "
                        "workspace (all strategies and backtests) transfers "
                        "to your account automatically and stops expiring."
                    ),
                }
            # Spec 09 CL-8: a deferred claim awaiting the user's decision.
            if config.pending_claim and not config.pending_claim.get("declined"):
                from keel.anon import pending_claim_public

                # Projection only — the stored record carries the anon
                # refresh token, which never rides a tool result.
                body["pending_claim"] = pending_claim_public(config.pending_claim)
        except Exception as e:  # noqa: BLE001
            # Only suggest re-auth on an actual 401. Network blips, 5xx,
            # parse failures etc. should NOT contradict `authenticated:
            # true` with a misleading "session likely expired" hint —
            # users would re-login unnecessarily and the agent reads the
            # contradiction back as uncertainty.
            from keel.errors import AuthError

            body["identity_error"] = str(e)
            if isinstance(e, AuthError):
                body["authenticated"] = False

        # Best-effort entitlements probe — gives agents a window into
        # plan-limit usage BEFORE they run a batch of backtests. If a unit is
        # exhausted or close to it, the agent can warn the user and
        # surface the billing-upgrade URL proactively instead of waiting
        # for the next call to 403. Failure is soft — handlers must not
        # block status on an entitlements outage.
        try:
            from keel.client import KeelClient

            client = KeelClient()
            try:
                ent = client.get("/v1/entitlements")
            finally:
                client.close()

            balances = ent.get("balances") or [] if isinstance(ent, dict) else []
            # Surface the high-signal consumable units agents care about
            # most. keel-api EntitlementBalance fields: `granted` /
            # `spent` / `reserved` / `available` (NOT consumed/remaining
            # — those don't exist on the API response).
            #
            # Each entry is annotated with `consumed_by` — the surface(s)
            # that actually charge this unit, mirroring
            # `libs/platform_auth/actions.py:COSTED_ACTIONS`. Agents need
            # this to answer "will doing X burn my Y quota?" correctly —
            # e.g. `ai_messages` is exclusively spent by the in-app chat
            # at app.usekeel.io/chat, NEVER by MCP/CLI/SDK tool calls.
            # The agent reads `consumed_by` inline instead of guessing
            # from unit names.
            CONSUMED_BY: dict[str, list[str]] = {
                "backtest_runs": [
                    "MCP tool calls (keel_backtest_run)",
                    "CLI (keel backtest run)",
                    "web app backtest UI",
                ],
                "backtest_compute_seconds": [
                    "MCP tool calls (keel_backtest_run)",
                    "CLI (keel backtest run)",
                    "web app backtest UI",
                ],
                "ai_messages": ["in-app chat at app.usekeel.io/chat ONLY"],
                # Spec 02 §2.5: the unit names its consumers without the
                # listed surface's banned verbs (the old strings named
                # `deploy`/`deploys`).
                "live_strategies_max": [
                    "local MCP server with the live tools loaded",
                    "CLI",
                    "web app live strategies",
                ],
                "eval_runs": ["agent evaluation runs (internal/admin)"],
            }
            UNIT_NOTES: dict[str, str] = {
                "ai_messages": (
                    "NOT consumed by MCP/CLI/SDK tool calls. Conversation "
                    "tokens used by an agent host driving Keel via MCP are "
                    "billed by that host's LLM provider, NOT by Keel."
                ),
            }
            summary: list[dict] = []
            for b in balances:
                unit = b.get("unit")
                if unit not in CONSUMED_BY:
                    continue
                granted = b.get("granted")
                entry: dict = {
                    "unit": unit,
                    "granted": granted,
                    "spent": b.get("spent"),
                    "available": b.get("available"),
                    "consumed_by": CONSUMED_BY[unit],
                }
                # WHEN it refills, straight from the server (M0.2/M1.3).
                # "4 backtests left" is not actionable without it — the
                # agent cannot tell a six-day lockout from a six-hour one.
                # Projected explicitly and ONLY when the server sent it: a
                # keel-api that does not serve the period carries no reset,
                # and inventing one here would be the null-number sentence
                # Q-1590 shipped, one layer down.
                for key in ("period", "resets_at", "seconds_to_reset", "reserved"):
                    if b.get(key) is not None:
                        entry[key] = b[key]
                # Mark unlimited explicitly so agents don't show
                # "2147483647 remaining" to the user.
                if granted == 2147483647:
                    entry["unlimited"] = True
                if unit in UNIT_NOTES:
                    entry["note"] = UNIT_NOTES[unit]
                summary.append(entry)
            # The caller's own units only — no plan destination (D-12: the
            # `upgrade_url` this carried was the billing tab, whose plan
            # buttons start Stripe Checkout; Q-2080).
            body["entitlements"] = {"summary": summary}
        except Exception as e:  # noqa: BLE001
            # Don't block status on this — surface the failure as a hint
            # so the agent knows entitlements aren't visible right now.
            body["entitlements_error"] = str(e)

    # ONE `next` string, the first condition that holds (spec 02 §2.5); a
    # unit exhausted is the quota line's to say, not a separate `next`.
    from ._status_view import build_status_view, status_next

    next_line = status_next(body, hosted=hosted, profile=profile)
    if next_line:
        body["next"] = next_line

    # The strategies list — not `/settings`, which opens on the billing tab
    # (D-12 §4.4: no agent surface links a plan destination).
    hero_url = f"{ctx.app_url}/strategies"
    body["view"] = build_status_view(body, profile=profile, url=hero_url)
    return OutcomeResult(
        run_id=None,
        hero_url=hero_url,
        share_url=None,
        extra=body,
    )


STATUS = register(
    OutcomeTool(
        name="keel_account_status",
        required_action="audit.read",
        cli_path=("status",),
        toolset="always",
        # grounded-in: status.py _handler (workflow_routes first_session +
        # entitlements/quota probe + surface_hints); system/chat/tool_usage.md:31-39
        # ("Don't Falsely Claim a Tool is Missing" — orient to the surface
        # you actually have before reaching for a lower-level tool).
        description=(
            "Report who is signed in to Keel, on which plan and with how much quota left — "
            "plan usage per unit is `keel_plan_usage`, a failing connection "
            "`keel_connection_check`. It carries the account and plan, the remaining quota "
            "(backtest runs, compute seconds, live strategy slots) with the reset instant, "
            "and what this connector can do: the toolsets visible under `KEEL_TOOLSETS` "
            "and the MCP tools they load."
        ),
        # Listed-profile copy (Q-1744): the connector has no API URL a user
        # can act on and no `KEEL_TOOLSETS` a user can set, so the listed
        # string describes what the result carries HERE. The capability
        # clause is the status view's line-3 phrase, byte for byte (Q-1960):
        # a fact about this connector's tools, never a session permission —
        # "one read-only call" beside "live monitoring read-only" became
        # "the session is read-only for live trading" in a ChatGPT refusal.
        # The first sentence stays shared with `description` (R-4). No
        # "first of a session" (Q-2080, 2026-10-01): OpenAI's scan read it as
        # a trigger beyond the user's request; the instructions say when to
        # call it, the description says what it returns.
        listed_description=(
            "Report who is signed in to Keel, on which plan and with how much quota left — "
            "plan usage per unit is `keel_plan_usage`, a failing connection "
            "`keel_connection_check`. It carries the account and plan, the remaining quota "
            "(backtest runs, compute seconds) with the reset instant, and what this "
            f"connector can do: {CAPABILITY_PHRASE}."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "title": "Get Account Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
