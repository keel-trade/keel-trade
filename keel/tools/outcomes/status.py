"""`keel_status` — auth/cache/registry summary.

Per spec §4 #1: "Am I authed? Which account? Cache fresh? Catalog
version vs live?"

Do NOT use to enumerate strategies (`keel_strategy_search`).
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _workflow_routes(*, live_read_loaded: bool, live_write_loaded: bool) -> list[dict[str, Any]]:
    """High-signal routes for first-contact agents.

    Keep this compact: `keel_status` is usually the first call, so it
    should steer tool choice without becoming another manual.
    """
    routes: list[dict[str, Any]] = [
        {
            "name": "first_session",
            "when": "new Keel session, unknown auth state, or unfamiliar project",
            "prompt": None,
            "tools": ["keel_status", "keel_auth_login", "keel_help"],
            "next": [
                "Call keel_status first.",
                "If authenticated=false, call keel_auth_login.",
                "For strategy work, load the strategy-creation prompt before composing.",
            ],
        },
        {
            "name": "research_strategy",
            "when": "create or materially edit a strategy, then produce evidence",
            "prompt": "strategy-creation",
            "tools": [
                "keel_components_search",
                "keel_components_detail_batch",
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
            "tools": ["keel_doctor", "keel_help", "keel_audit_list_last"],
            "next": [
                "Read the structured error envelope before trying another tool.",
                "Use keel_doctor for environment/auth issues.",
            ],
        },
    ]

    routes.append(
        {
            "name": "live_monitoring",
            "when": (
                "user asks about existing live deployments, portfolio state, or live positions"
            ),
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
    if live_write_loaded:
        live_route["tools"].append("keel_live_control")
    routes.append(live_route)
    return routes


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.config import load_config

    config = load_config()
    body: dict[str, Any] = {
        "authenticated": bool(config.api_key),
        "api_url": config.api_url,
    }

    # Always read live env — CLI ctx has empty toolsets (CLI exposes
    # every command); MCP ctx mirrors env. Read env directly so both
    # paths return the truth.
    from . import all_tools
    from ._mcp_adapter import loaded_tool_names
    from ._toolsets import load_toolsets

    active = load_toolsets()
    body["toolsets_loaded"] = sorted(active)
    body["tools_visible"] = loaded_tool_names({t.name: t for t in all_tools()})
    body["live_monitoring_allowed"] = "live-read" in active
    body["live_trading_allowed"] = "live-write" in active
    body["workflow_routes"] = _workflow_routes(
        live_read_loaded="live-read" in active,
        live_write_loaded="live-write" in active,
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
                "principal_id": principal.get("id"),
                "org_id": org.get("id"),
                "org_name": org.get("name"),
                "plan": org.get("plan"),
                "tier": "live" if "runner.*" in scopes else "base",
            }
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
                body["next"] = [
                    "keel_auth_login   # tokens rejected by /v1/me — re-authenticate",
                ]

        # Best-effort entitlements probe — gives agents a window into
        # plan-limit usage BEFORE they run a big sweep. If a unit is
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
                "live_strategies_max": [
                    "MCP tool calls (keel_live_deploy)",
                    "CLI (keel live deploy)",
                    "web app live deploys",
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
                # Mark unlimited explicitly so agents don't show
                # "2147483647 remaining" to the user.
                if granted == 2147483647:
                    entry["unlimited"] = True
                if unit in UNIT_NOTES:
                    entry["note"] = UNIT_NOTES[unit]
                summary.append(entry)
            body["entitlements"] = {
                "summary": summary,
                "upgrade_url": "https://app.usekeel.io/settings?tab=billing",
            }
        except Exception as e:  # noqa: BLE001
            # Don't block status on this — surface the failure as a hint
            # so the agent knows entitlements aren't visible right now.
            body["entitlements_error"] = str(e)
    else:
        body["next"] = [
            "keel_auth_login   # not authenticated — run this to sign in via browser",
            "(or `keel auth login` from a terminal if your agent can't open browsers)",
        ]

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/settings",
        share_url=None,
        extra=body,
    )


STATUS = register(
    OutcomeTool(
        name="keel_status",
        required_action="audit.read",
        cli_path=("status",),
        toolset="always",
        description=(
            "Report Keel CLI/MCP status: auth state, API URL, active toolsets, "
            "and the list of MCP tools visible under the current `KEEL_TOOLSETS`. "
            "Use as the first call when wiring up a new agent. "
            "Do NOT use to enumerate strategies — call `keel_strategy_search`. "
            "Do NOT use to diagnose errors — call `keel_doctor`."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
