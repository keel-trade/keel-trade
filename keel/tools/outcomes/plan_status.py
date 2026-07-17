"""`keel_plan_status` — plan, limits, and remaining quota as numbers (spec 04 R2).

Read-only wrapper over ``GET /v1/me``: surfaces the server-computed
``plan_status`` block — ``{plan, limits, remaining: {backtest_runs,
compute_seconds, live_slots}, builder_fee_bps, upgrade_options}`` — whose
every figure traces to the platform's enforcement sources
(``libs/platform_auth/plans.yaml``, the enforced builder-fee schedule,
and the ``/pricing.md`` price map). Numbers only; no marketing language
in any output, on any surface.

Per-surface policy (spec 04 R2/R3, research/08):

* ``manage_url`` (the billing page, where plan changes happen via the
  EXISTING Stripe checkout — no new billing logic) plus the ``checkout``
  pointer to ``POST /v1/billing/checkout`` are included on the CLI, the
  local MCP, the unlisted hosted endpoint, and a listed registration
  declared ``KEEL_LISTED_CLIENT=claude``.
* On a listed registration declared ``chatgpt`` — or with NO declared
  client (fail-safe default) — both are OMITTED and ``talking_points``
  carry facts only, validated by the same honesty rules as the spec 03
  handoff envelope (``_handoff.validate_talking_points`` — one
  validator, no second shape).

Entitlements are org-level: after a human changes the plan, the SAME
token immediately reads the new limits here — no re-auth (asserted at
the API layer in keel-api's tests/test_plan_status.py).
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._handoff import validate_talking_points
from ._toolsets import manage_links_allowed


# INT_MAX sentinel platform_auth uses for unlimited grants (config.UNLIMITED).
_UNLIMITED_SENTINEL = 2147483647

# Entitlement unit → spec 04 R2 key, for the degraded (older-API) path only.
_FALLBACK_UNIT_KEYS = {
    "backtest_runs": "backtest_runs",
    "backtest_compute_seconds": "compute_seconds",
    "live_strategies_max": "live_slots",
}

_BILLING_PATH = "/settings?tab=billing"


def _fallback_remaining(entitlements: list[Any]) -> dict[str, int | str]:
    """Remaining counters from the /v1/me entitlement balances, for
    servers that predate the plan_status block. Same vocabulary, same
    balance math (consumable → available; cap → granted − cap_current);
    nothing here invents a number the server didn't send."""
    remaining: dict[str, int | str] = {}
    for bal in entitlements:
        if not isinstance(bal, dict):
            continue
        key = _FALLBACK_UNIT_KEYS.get(bal.get("unit"))
        if key is None:
            continue
        granted = bal.get("granted")
        if granted is None:
            continue
        if granted >= _UNLIMITED_SENTINEL:
            remaining[key] = "unlimited"
        elif bal.get("type") == "cap":
            remaining[key] = max(0, granted - (bal.get("cap_current") or 0))
        else:
            remaining[key] = bal.get("available", 0)
    return remaining


def _facts_talking_points(plan: Any, limits: dict | None, remaining: dict | None) -> list[str]:
    """Facts-only talking points for upsell-suppressed surfaces.

    Numbers come verbatim from the API response; the lines name the
    human-only nature of plan changes and the do-nothing alternative,
    and are validated by the SAME honesty rules as the spec 03 handoff
    envelope (no second shape, per the M3.1 adoption note)."""
    facts: list[str] = []
    for key, label in (
        ("backtest_runs", "backtest runs"),
        ("compute_seconds", "backtest compute seconds"),
        ("live_slots", "live strategy slots"),
    ):
        if remaining and key in remaining:
            limit = (limits or {}).get(key)
            if limit is not None:
                facts.append(f"{remaining[key]} of {limit} {label} remaining")
            else:
                facts.append(f"{remaining[key]} {label} remaining")
    usage_line = f"Current plan: {plan}."
    if facts:
        usage_line += " This period: " + "; ".join(facts) + "."
    points = [
        usage_line,
        (
            "Plan changes are a billing action performed by a human in the "
            "Keel account settings; they cannot be made from this chat."
        ),
        (
            "Doing nothing is also fine — the current plan keeps working "
            "and existing strategies, backtests, and results stay available."
        ),
    ]
    return validate_talking_points(points)


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    me = ctx.get_client().get("/v1/me")
    if not isinstance(me, dict):
        me = {}
    org = me.get("org") or {}
    ps = me.get("plan_status")
    allowed = manage_links_allowed()

    body: dict[str, Any] = {}
    if isinstance(ps, dict):
        # Explicit field projection on every surface: a future server
        # field can never leak onto a surface whose policy wasn't
        # reviewed for it (the suppression is an allow-list, not a
        # strip-list).
        body["plan"] = ps.get("plan")
        body["builder_fee_bps"] = ps.get("builder_fee_bps")
        body["limits"] = ps.get("limits")
        body["remaining"] = ps.get("remaining")
        body["upgrade_options"] = [
            {k: option.get(k) for k in ("plan", "price", "what_changes")}
            for option in (ps.get("upgrade_options") or [])
            if isinstance(option, dict)
        ]
        manage_url = ps.get("manage_url") or f"{ctx.app_url}{_BILLING_PATH}"
    else:
        # Older keel-api without plan_status: report what /v1/me does
        # carry (plan + entitlement balances). Prices, limits tables, and
        # fee schedule are server-sourced numbers — absent server support
        # they are OMITTED, never reconstructed client-side.
        body["plan"] = org.get("plan")
        body["remaining"] = _fallback_remaining(me.get("entitlements") or [])
        body["note"] = (
            "this keel-api version does not provide plan pricing fields; "
            "limits, builder fee, and other-plan details are unavailable "
            "here — see the platform's pricing.md"
        )
        manage_url = f"{ctx.app_url}{_BILLING_PATH}"

    if allowed:
        body["manage_url"] = manage_url
        # spec 04 R3: plan changes ride the EXISTING checkout endpoint —
        # this is a pointer to it, not new billing logic. Card entry and
        # payment happen on the hosted Stripe page, never through an agent.
        body["checkout"] = {
            "endpoint": "POST /v1/billing/checkout",
            "body": {"plan": "<plan>", "billing_cycle": "monthly | annual"},
            "returns": (
                "checkout_url — a hosted Stripe checkout page on Keel's "
                "domain; a human completes payment there"
            ),
            "note": (
                "orgs with an active subscription change plans via "
                "POST /v1/billing/upgrade instead; entitlements apply to "
                "the org immediately, so the same token sees the new "
                "limits without re-authentication"
            ),
        }
    else:
        body["talking_points"] = _facts_talking_points(
            body.get("plan"), body.get("limits"), body.get("remaining")
        )

    return OutcomeResult(
        run_id=None,
        hero_url=manage_url if allowed else None,
        share_url=None,
        extra=body,
    )


PLAN_STATUS = register(
    OutcomeTool(
        name="keel_plan_status",
        # Lowest consent bucket (read — same as keel_status/keel_doctor):
        # plan visibility must never sit behind a write-scope grant.
        required_action="audit.read",
        cli_path=("plan", "status"),
        toolset="read-only",
        description=(
            "Report the org's current Keel plan as enforced numbers: plan "
            "name, per-plan limits, remaining quota this period (backtest "
            "runs, compute seconds, live strategy slots), the builder fee "
            "in bps, and `upgrade_options` — the other available plans "
            "with USD prices and exact limit differences, returned as "
            "data, not a recommendation. Read-only: calling it never "
            "changes the plan and never spends quota. Check it before a "
            "large backtest sweep to stay within the remaining allowance. "
            "Do NOT use to check auth state or visible tools — call "
            "`keel_status`."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "title": "Plan Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
