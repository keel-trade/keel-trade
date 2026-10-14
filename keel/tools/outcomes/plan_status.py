"""`keel_plan_usage` — the caller's own plan, usage and reset (spec 04 R2, D-12).

Read-only wrapper over ``GET /v1/me``. It returns EXACTLY:

* ``plan`` — the org's plan id (``free``, ``starter`` …);
* ``period`` — the metering window the backtest units share (``weekly``),
  or ``None`` when the server named none or the units disagree;
* ``quota`` — one block per metered backtest unit, projected to
  ``{unit, limit, used, remaining, resets_at}`` (the same projection
  ``keel_backtest_run``'s ``quota`` carries);
* ``talking_points`` — ONE neutral sentence::

      Current plan: Free. This week: 46 of 50 backtest runs and 1,380 of
      1,500 compute seconds remaining; resets Mon 29 Sep 00:00 UTC.

  and, only while the server reports a first-week allowance on the
  ``backtest_runs`` block (connect-onboarding spec 01 §1.8/§1.9), a second
  one — the only first-week wording on any agent surface::

      154 of 200 first-week backtests left; they end Tue 13 Oct 15:02 UTC.

* ``paper`` — present only where the caller is served the paper tools
  (S10 §5, PT2-90): on the listed profile while ``listed_paper_enabled()``
  is true for the caller's host (per request, FDR-10), and on the full
  profile once the paper SDK is released. One block,
  ``{unit: "paper_strategies_max", limit, used, remaining}``, read from the
  ``/v1/me`` entitlement balance the paper wall's own check computes
  (``platform_auth.get_all_balances``: the effective cap with bonus grants,
  and the live count of DEPLOYING/LIVE/PAUSED paper runs), and one more
  neutral sentence::

      Paper strategies: 1 of 1 in use. It limits how many run at once and
      does not reset on a clock.

  A concurrent cap has no period and no reset. Absent everywhere else, so a
  ChatGPT session's output stays HEAD's bytes.

That is the Lovable ``get_workspace`` shape: the caller's own state and
nothing else (mcp-conversion D-12, 04 §4.3, founder ruling 2026-09-28).

**Removed by D-12, and why.** ``upgrade_options`` (the other plans, their
limits and — off the listed profile — their prices), ``builder_fee_bps``,
``manage_url`` / ``hero_url`` (the billing tab, whose plan buttons start
Stripe Checkout), ``live_slots`` (live capacity is not a research-surface
fact), and the "plan changes are an account action …" and "doing nothing is
also fine" points. OpenAI rejected Keel v1.0.0 for "commerce for disallowed
offerings" while this tool listed the other plans (Q-2080). keel-api still
serves every one of those numbers (D-10: the API carries numbers); this
tool simply never projects them — an allow-list, so a future server field
cannot leak onto a surface whose policy was not reviewed for it.

Entitlements are org-level: after a human changes the plan, the SAME token
immediately reads the new limits here — no re-auth (asserted at the API
layer in keel-api's tests/test_plan_status.py).
"""

from __future__ import annotations

from typing import Any

from keel.errors import (
    QUOTA_CAP_NO_RESET,
    assert_neutral_wall_text,
    format_reset_instant,
    plan_display_name,
    project_first_week,
    quota_reset_instant,
    render_first_week_sentence,
)

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._handoff import validate_talking_points


# INT_MAX sentinel platform_auth uses for unlimited grants (config.UNLIMITED).
_UNLIMITED_SENTINEL = 2147483647

#: The metered research units this tool reports, in talking-point order, with
#: the label the sentence reads. plans.yaml unit names — the vocabulary the
#: served `quotas` blocks, the /v1/me balances and `keel_backtest_run`'s
#: `quota` all speak. `live_strategies_max` (a cap) and `ai_messages` (the
#: in-app chat's allowance) are deliberately absent (D-12).
_UNITS: tuple[tuple[str, str], ...] = (
    ("backtest_runs", "backtest runs"),
    ("backtest_compute_seconds", "compute seconds"),
)

#: The per-unit projection — identical to `backtest_run.QUOTA_BLOCK_KEYS`.
#: `first_week` is present only while a first-week allowance is active
#: (spec 01 §1.8) and is itself projected to `errors.FIRST_WEEK_KEYS`.
_BLOCK_KEYS: tuple[str, ...] = ("unit", "limit", "used", "remaining", "resets_at", "first_week")

#: The top-level key set, exactly (pinned by test_outcomes_plan_status).
RESULT_KEYS: frozenset[str] = frozenset({"plan", "period", "quota", "talking_points"})
#: The one key the paper surface adds, only where paper is served (S10 §5).
PAPER_KEY = "paper"
#: The paper slot unit (plans.yaml), a concurrent cap.
PAPER_UNIT = "paper_strategies_max"

_PERIOD_PHRASES: dict[str, str] = {
    "daily": "Today",
    "weekly": "This week",
    "monthly": "This month",
}


def _served_blocks(me: dict) -> dict[str, dict[str, Any]]:
    """Per-unit quota blocks, keyed by unit, from whichever place the server
    sent them — both read verbatim, neither invented:

    1. ``plan_status.quotas`` — the served ``QuotaView`` wire blocks (limit,
       used, remaining, period, reset), the one computation owner;
    2. the ``/v1/me`` entitlement balances, for a keel-api older than that
       block: ``granted`` → limit, ``spent + reserved`` → used (the
       ``QuotaView`` definition), ``available`` → remaining.

    An unlimited unit reads ``"unlimited"`` for its limit and remaining —
    never the INT_MAX sentinel.
    """
    out: dict[str, dict[str, Any]] = {}
    wanted = {unit for unit, _ in _UNITS}
    ps = me.get("plan_status")
    served = ps.get("quotas") if isinstance(ps, dict) else None
    if isinstance(served, list) and served:
        for block in served:
            if not isinstance(block, dict) or block.get("unit") not in wanted:
                continue
            if block.get("unlimited"):
                out[block["unit"]] = {
                    "unit": block["unit"],
                    "limit": "unlimited",
                    "remaining": "unlimited",
                }
                continue
            out[block["unit"]] = dict(block)
        return out
    for bal in me.get("entitlements") or []:
        if not isinstance(bal, dict) or bal.get("unit") not in wanted:
            continue
        granted = bal.get("granted")
        if not isinstance(granted, int):
            continue
        unit = bal["unit"]
        if granted >= _UNLIMITED_SENTINEL:
            out[unit] = {"unit": unit, "limit": "unlimited", "remaining": "unlimited"}
            continue
        block: dict[str, Any] = {"unit": unit, "limit": granted}
        spent, reserved = bal.get("spent"), bal.get("reserved")
        if isinstance(spent, int):
            block["used"] = spent + (reserved if isinstance(reserved, int) else 0)
        if isinstance(bal.get("available"), int):
            block["remaining"] = bal["available"]
        for key in ("period", "resets_at", "first_week"):
            if bal.get(key) is not None:
                block[key] = bal[key]
        out[unit] = block
    return out


def _paper_served() -> bool:
    """Whether this caller is served the paper tools, read per call (S10 §5).

    Listed: the host-aware listed flag, so a ChatGPT session with the flag on
    for Claude only gets HEAD's bytes. Full: the held-SDK constant.
    """
    from . import _toolsets

    if _toolsets.is_listed_profile():
        return _toolsets.listed_paper_enabled()
    return _toolsets.PAPER_SDK_RELEASED


def _paper_block(me: dict) -> dict[str, Any] | None:
    """The paper slot block from the served cap balance, or ``None``.

    The balance's ``available`` is the cap itself for a cap unit, so the
    remaining count is ``limit - cap_current`` (the same definition as
    ``platform_auth.quota.quota_view_from_cap``); nothing is invented.
    """
    for bal in me.get("entitlements") or []:
        if not isinstance(bal, dict) or bal.get("unit") != PAPER_UNIT:
            continue
        granted, current = bal.get("granted"), bal.get("cap_current")
        if not isinstance(granted, int) or not isinstance(current, int):
            return None
        if granted >= _UNLIMITED_SENTINEL:
            return {
                "unit": PAPER_UNIT,
                "limit": "unlimited",
                "used": current,
                "remaining": "unlimited",
            }
        return {
            "unit": PAPER_UNIT,
            "limit": granted,
            "used": current,
            "remaining": max(0, granted - current),
        }
    return None


def _paper_point(block: dict[str, Any]) -> str:
    """The neutral paper sentence: the caller's own count, the cap rule, stop."""
    if block["limit"] == "unlimited":
        return "Paper strategies: unlimited."
    return (
        f"Paper strategies: {_amount(block['used'])} of {_amount(block['limit'])} in use. "
        f"{QUOTA_CAP_NO_RESET}."
    )


def _project(block: dict[str, Any]) -> dict[str, Any]:
    """One served block projected to :data:`_BLOCK_KEYS`."""
    out = {k: block[k] for k in _BLOCK_KEYS if k in block and k != "first_week"}
    first_week = project_first_week(block.get("first_week"))
    if first_week:
        out["first_week"] = first_week
    return out


def _shared_period(blocks: list[dict[str, Any]]) -> str | None:
    """The one metering window the units share, or ``None``."""
    periods = {b.get("period") for b in blocks if b.get("limit") != "unlimited"}
    periods.discard(None)
    return periods.pop() if len(periods) == 1 else None


def _amount(n: Any) -> str:
    return f"{n:,}" if isinstance(n, int) and not isinstance(n, bool) else str(n)


def _talking_point(plan: Any, period: str | None, blocks: list[dict[str, Any]]) -> str:
    """The ONE neutral sentence (D-12 §4.3, exact shape)::

        Current plan: Free. This week: 46 of 50 backtest runs and 1,380 of
        1,500 compute seconds remaining; resets Mon 29 Sep 00:00 UTC.

    Numbers verbatim from the server; a unit it did not send is omitted,
    and the reset clause appears only when it sent an instant (Q-1597: a
    client-side guess at a period boundary is a lie).
    """
    labels = dict(_UNITS)
    facts: list[str] = []
    for block in blocks:
        label = labels[block["unit"]]
        if block.get("limit") == "unlimited":
            facts.append(f"unlimited {label}")
        elif isinstance(block.get("remaining"), int) and isinstance(block.get("limit"), int):
            facts.append(f"{_amount(block['remaining'])} of {_amount(block['limit'])} {label}")
    name = plan_display_name(plan) or "unknown"
    sentence = f"Current plan: {name}."
    if facts:
        window = _PERIOD_PHRASES.get(str(period or ""), "This period")
        sentence += f" {window}: {' and '.join(facts)} remaining"
        instants = []
        for block in blocks:
            when = quota_reset_instant(block)
            if when is not None and when not in instants:
                instants.append(when)
        if instants:
            sentence += "; resets " + ", ".join(format_reset_instant(w) for w in instants)
        sentence += "."
    return sentence


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    me = ctx.get_client().get("/v1/me")
    if not isinstance(me, dict):
        me = {}
    ps = me.get("plan_status")
    plan = ps.get("plan") if isinstance(ps, dict) and ps.get("plan") else None
    if plan is None:
        org = me.get("org") if isinstance(me.get("org"), dict) else {}
        plan = org.get("plan")

    served = _served_blocks(me)
    blocks = [served[unit] for unit, _ in _UNITS if unit in served]
    period = _shared_period(blocks)
    points = [_talking_point(plan, period, blocks)]
    # The first-week sentence (spec 01 §1.9): always here when the server
    # reports the allowance on the backtest-runs block — the compute unit's
    # block carries one too, but the sentence counts backtests.
    if "backtest_runs" in served:
        first_week = render_first_week_sentence(served["backtest_runs"].get("first_week"))
        if first_week:
            points.append(first_week)
    paper = _paper_block(me) if _paper_served() else None
    if paper is not None:
        points.append(_paper_point(paper))
    # The same validator and D-12 scan as every wall: no other plan, no
    # price, no destination, no expiry urgency. A neutral fact proposes no
    # action, so there is no do-nothing line to require.
    talking_points = validate_talking_points(
        [assert_neutral_wall_text(p, free_plan=False) for p in points],
        require_do_nothing=False,
    )

    body: dict[str, Any] = {
        "plan": plan,
        "period": period,
        "quota": [_project(b) for b in blocks],
        "talking_points": talking_points,
    }
    if paper is not None:
        body[PAPER_KEY] = paper
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=body)


PLAN_USAGE_DESCRIPTION = (
    "Report the org's own Keel plan and its backtest usage this period — auth, "
    "identity and visible tools are `keel_account_status`. It returns the plan name, the "
    "period, and for backtest runs and backtest compute seconds the limit, the "
    "used and remaining counts, and the reset instant. It changes nothing and "
    "spends no quota."
)
PLAN_USAGE_LISTED_PAPER_DESCRIPTION = (
    "Report the org's own Keel plan and its backtest usage this period — auth, "
    "identity and visible tools are `keel_account_status`. It returns the plan name, the "
    "period, and for backtest runs and backtest compute seconds the limit, the "
    "used and remaining counts, and the reset instant, plus the paper strategies "
    "limit (simulated runs at once, no reset) with its used and remaining counts. It "
    "changes nothing and spends no quota."
)


PLAN_STATUS = register(
    OutcomeTool(
        name="keel_plan_usage",
        # Lowest consent bucket (read — same as keel_account_status/keel_connection_check):
        # plan visibility must never sit behind a write-scope grant.
        required_action="audit.read",
        cli_path=("plan", "status"),
        toolset="read-only",
        # grounded-in: plan_status.py _handler (the caller's own plan, the
        # served per-unit quota blocks and reset instants — numbers only);
        # mcp-conversion 04 §4.3 / D-12 (the Lovable get_workspace shape:
        # the caller's own state and nothing else).
        description=PLAN_USAGE_DESCRIPTION,
        # The listed twin while paper is served to this host (S10 §5, PT2-90):
        # the description states the paper block the output then carries.
        listed_paper_description=PLAN_USAGE_LISTED_PAPER_DESCRIPTION,
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "title": "Get Plan Usage",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
