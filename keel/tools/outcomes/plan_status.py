"""`keel_plan_usage` — the caller's plan, its limits, and which plans include more
(spec 04 R2; mcp-conversion D-14, doc 07 §5).

Read-only wrapper over ``GET /v1/me``. It returns:

* ``plan`` / ``plan_name`` — the org's plan id and its served display name
  (the id capitalised for a server that sent no name);
* ``period`` — the metering window the backtest units share, or ``None``;
* ``quota`` — one block per metered unit (backtest runs, compute and, while
  research is served, studies), projected to ``{unit, limit, used,
  remaining, resets_at, more_plans}`` (the same projection
  ``keel_backtest_run``'s ``quota`` carries: ``more_plans`` is the served
  NAMES of the plans that include more, never their numbers);
* ``paper`` / ``live`` / ``screens`` — the caps the caller can meet on this
  surface, present only where those tools are served;
* ``serving_floor`` — ``{floor, more_plans}``, the fastest timeframe paper
  and live strategies run at on this plan;
* ``talking_points`` — one opening line, then one short line per limit::

      Plan: Free. Trader and Pro include more; plans are changed in the Keel web app.
      Backtests: 21 of 25 left; resets Sat 5 Dec.
      Backtest compute time: 690 s of 750 s left; resets Sat 5 Dec.
      Paper strategies: 1 of 1 running.
      Paper and live timeframe: 1h or slower.

  and, while the server reports a first-week allowance with some left on the
  backtest-runs block (connect-onboarding spec 01 §1.8), its clause folded
  into the backtests line: ``Backtests: 75 of 75 left (50 first-week extras
  until Mon 23 Nov); resets Wed 16 Dec.``

**Never** another plan's limits or price, ``upgrade_options``,
``builder_fee_bps`` or a billing/pricing URL (D-12, which D-14 keeps): an
allow-list, so a future server field cannot reach a surface whose policy was
not reviewed for it.

Entitlements are org-level: after a human changes the plan, the SAME token
immediately reads the new limits here — no re-auth (asserted at the API
layer in keel-api's tests/test_plan_status.py).
"""

from __future__ import annotations

from typing import Any

from keel.errors import (
    CAP,
    WEB_APP_SENTENCE,
    assert_plan_copy,
    format_reset_date,
    join_plan_names,
    limit_unit,
    more_plan_names,
    own_plan_name,
    project_first_week,
    quota_amount,
    quota_reset_instant,
    render_first_week_clause,
)

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._handoff import validate_talking_points


# INT_MAX sentinel platform_auth uses for unlimited grants (config.UNLIMITED).
_UNLIMITED_SENTINEL = 2147483647

#: The metered units this tool reports, in talking-point order: plans.yaml
#: unit names — the vocabulary the served `quotas` blocks, the /v1/me
#: balances and `keel_backtest_run`'s `quota` all speak. `research_studies`
#: is served only while research.enabled is on (absent from /v1/me otherwise,
#: so absent here); research_compute_seconds is a silent ceiling (D-64).
_UNITS: tuple[str, ...] = ("backtest_runs", "backtest_compute_seconds", "research_studies")

#: The per-unit projection — identical to `backtest_run.QUOTA_BLOCK_KEYS`.
_BLOCK_KEYS: tuple[str, ...] = (
    "unit",
    "limit",
    "used",
    "remaining",
    "resets_at",
    "first_week",
    "more_plans",
)

#: The top-level keys every call returns, exactly (pinned by
#: test_outcomes_plan_status). The optional ones below appear only where
#: the caller can meet that limit on this surface.
RESULT_KEYS: frozenset[str] = frozenset({"plan", "plan_name", "period", "quota", "talking_points"})
#: The paper slots, only where the paper tools are served (S10 §5).
PAPER_KEY = "paper"
#: The live slots, only where the live tools are (never the listed profile).
LIVE_KEY = "live"
#: The screens caps, only where the screen tools are served (L13).
SCREENS_KEY = "screens"
#: The paper/live timeframe floor (doc 07 §5): `{floor, more_plans}`.
FLOOR_KEY = "serving_floor"
OPTIONAL_KEYS: frozenset[str] = frozenset({PAPER_KEY, LIVE_KEY, SCREENS_KEY, FLOOR_KEY})

#: The cap units, one block each.
PAPER_UNIT = "paper_strategies_max"
LIVE_UNIT = "live_strategies_max"
SCREEN_UNITS: tuple[str, ...] = (
    "screeners_live_max",
    "screeners_published_max",
    "screeners_private_max",
)


def _plan_status(me: dict) -> dict:
    ps = me.get("plan_status")
    return ps if isinstance(ps, dict) else {}


def _served_blocks(me: dict) -> dict[str, dict[str, Any]]:
    """Per-unit quota blocks, keyed by unit, from whichever place the server
    sent them — both read verbatim, neither invented:

    1. ``plan_status.quotas`` — the served ``QuotaView`` wire blocks (limit,
       used, remaining, period, reset, higher_plans), the one computation
       owner;
    2. the ``/v1/me`` entitlement balances, for a keel-api older than that
       block: ``granted`` → limit, ``spent + reserved`` → used (the
       ``QuotaView`` definition), ``available`` → remaining.

    An unlimited unit reads ``"unlimited"`` for its limit and remaining —
    never the INT_MAX sentinel.
    """
    out: dict[str, dict[str, Any]] = {}
    wanted = set(_UNITS)
    served = _plan_status(me).get("quotas")
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
        for key in ("period", "resets_at", "first_week", "higher_plans"):
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


def _live_served() -> bool:
    """Whether the live-deploy tool is on this server (the opt-in live-write
    toolset; never the listed profile, spec 01 R3)."""
    from . import _toolsets

    return _toolsets.is_tool_loaded(
        "live-write", _toolsets.load_toolsets(), name="keel_live_deploy"
    )


def _screens_served() -> bool:
    """The screens caps (auto-refresh, public, private) are met through the
    refresh and publish tools, which are full-profile only."""
    from . import _toolsets

    return _toolsets.screens_served() and not _toolsets.is_listed_profile()


def _cap_block(me: dict, unit: str) -> dict[str, Any] | None:
    """One cap's block from the served balance, or ``None``.

    The balance's ``available`` is the cap itself for a cap unit, so the
    remaining count is ``limit - cap_current`` (the same definition as
    ``platform_auth.quota.quota_view_from_cap``); nothing is invented. A cap
    the plan does not grant at all is ``limit: 0``.
    """
    for bal in me.get("entitlements") or []:
        if not isinstance(bal, dict) or bal.get("unit") != unit:
            continue
        granted = bal.get("granted", bal.get("limit"))
        current = bal.get("cap_current", bal.get("used"))
        if not isinstance(granted, int) or not isinstance(current, int):
            return None
        block: dict[str, Any] = {"unit": unit, "used": current}
        if granted >= _UNLIMITED_SENTINEL:
            block.update(limit="unlimited", remaining="unlimited")
        else:
            block.update(limit=granted, remaining=max(0, granted - current))
        names = more_plan_names(bal)
        if names:
            block["more_plans"] = names
        return block
    return None


def _floor_block(me: dict) -> dict[str, Any] | None:
    """``{floor, more_plans}`` from the served ``serving_floor``, or ``None``."""
    served = me.get("serving_floor")
    if not isinstance(served, dict):
        served = _plan_status(me).get("serving_floor")
    if not isinstance(served, dict) or not isinstance(served.get("floor"), str):
        return None
    block: dict[str, Any] = {"floor": served["floor"]}
    names = more_plan_names(served)
    if names:
        block["more_plans"] = names
    return block


def _project(block: dict[str, Any]) -> dict[str, Any]:
    """One served block projected to :data:`_BLOCK_KEYS`."""
    derived = ("first_week", "more_plans")
    out = {k: block[k] for k in _BLOCK_KEYS if k in block and k not in derived}
    first_week = project_first_week(block.get("first_week"))
    if first_week:
        out["first_week"] = first_week
    names = more_plan_names(block)
    if names:
        out["more_plans"] = names
    return out


def _shared_period(blocks: list[dict[str, Any]]) -> str | None:
    """The one metering window the units share, or ``None``."""
    periods = {b.get("period") for b in blocks if b.get("limit") != "unlimited"}
    periods.discard(None)
    return periods.pop() if len(periods) == 1 else None


def _label(unit: str) -> str:
    row = limit_unit(unit)
    label = row.label if row is not None else unit.replace("_", " ")
    return f"{label[:1].upper()}{label[1:]}"


def _count(n: Any) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def _metered_line(block: dict[str, Any]) -> str | None:
    """``Backtests: 21 of 25 left; resets Sat 5 Dec.`` — numbers verbatim; a
    reset only when the server sent an instant (Q-1597). While the first
    week has some left, its clause is folded in: ``Backtests: 75 of 75 left
    (50 first-week extras until Mon 23 Nov); resets Wed 16 Dec.``"""
    unit = block["unit"]
    if block.get("limit") == "unlimited":
        return f"{_label(unit)}: unlimited."
    limit, remaining = block.get("limit"), block.get("remaining")
    if not _count(limit) or not _count(remaining):
        return None
    when = quota_reset_instant(block)
    reset = f"; resets {format_reset_date(when)}" if when else ""
    clause = render_first_week_clause(block)
    extras = f" {clause}" if clause else ""
    return (
        f"{_label(unit)}: {quota_amount(unit, remaining)} of "
        f"{quota_amount(unit, limit)} left{extras}{reset}."
    )


def _cap_line(block: dict[str, Any], plan: str | None) -> str:
    """``Paper strategies: 1 of 1 running.`` · ``… unlimited.`` · ``… not on Free.``"""
    unit = block["unit"]
    if block["limit"] == "unlimited":
        return f"{_label(unit)}: unlimited."
    if block["limit"] == 0:
        return f"{_label(unit)}: not on {plan or 'this plan'}."
    row = limit_unit(unit)
    held = row.held if row is not None and row.kind == CAP else "in use"
    return f"{_label(unit)}: {block['used']:,} of {block['limit']:,} {held}."


def _floor_line(block: dict[str, Any]) -> str:
    return f"{_label('paper_live_clock_floor')}: {block['floor']} or slower."


def _opening(plan: str | None, names: list[str]) -> str:
    """``Plan: Free. Trader and Pro include more; plans are changed in the
    Keel web app.`` — the names are every plan the server says includes more
    of any limit listed here; none (Pro) → ``Plan: Pro.``"""
    head = f"Plan: {plan or 'unknown'}."
    if not names:
        return head
    verb = "includes" if len(names) == 1 else "include"
    where = WEB_APP_SENTENCE[:1].lower() + WEB_APP_SENTENCE[1:]
    return f"{head} {join_plan_names(names)} {verb} more; {where}"


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    me = ctx.get_client().get("/v1/me")
    if not isinstance(me, dict):
        me = {}
    ps = _plan_status(me)
    org = me.get("org") if isinstance(me.get("org"), dict) else {}
    plan = ps.get("plan") or org.get("plan")
    plan_name = own_plan_name(
        {"plan_name": me.get("plan_name") or ps.get("plan_name"), "plan": plan}
    )

    served = _served_blocks(me)
    blocks = [served[unit] for unit in _UNITS if unit in served]
    caps: dict[str, Any] = {}
    paper = _cap_block(me, PAPER_UNIT) if _paper_served() else None
    if paper is not None:
        caps[PAPER_KEY] = paper
    live = _cap_block(me, LIVE_UNIT) if _live_served() else None
    if live is not None:
        caps[LIVE_KEY] = live
    screens = (
        [b for unit in SCREEN_UNITS if (b := _cap_block(me, unit)) is not None]
        if _screens_served()
        else []
    )
    floor = _floor_block(me)

    # The opening line names every plan that includes more of something
    # listed below, in the order the server listed them.
    names: list[str] = []
    for block in [*blocks, *caps.values(), *screens, *([floor] if floor else [])]:
        for name in more_plan_names(block):
            if name not in names:
                names.append(name)
    points = [_opening(plan_name, names)]
    for block in blocks:
        line = _metered_line(block)
        if line:
            points.append(line)
    points += [_cap_line(b, plan_name) for b in (*caps.values(), *screens)]
    if floor is not None:
        points.append(_floor_line(floor))
    # The same validator and plan-copy scan as every wall: no price, no
    # destination, no pitch, no expiry urgency. A fact proposes no action,
    # so there is no do-nothing line to require.
    talking_points = validate_talking_points(
        [assert_plan_copy(p) for p in points], require_do_nothing=False
    )

    body: dict[str, Any] = {
        "plan": plan,
        "plan_name": plan_name,
        "period": _shared_period(blocks),
        "quota": [_project(b) for b in blocks],
        "talking_points": talking_points,
        **caps,
    }
    if screens:
        body[SCREENS_KEY] = screens
    if floor is not None:
        body[FLOOR_KEY] = floor
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=body)


#: One clause per limit kind the caller can meet on the profile (doc 07 §5),
#: no number and no plan name (§3.8, §6.1).
_USAGE_HEAD = (
    "Report the org's own Keel plan, its limits and which plans include more — auth, "
    "identity and visible tools are `keel_account_status`. "
)
_USAGE_TAIL = " — each with its numbers and reset instant. It changes nothing and spends no quota."
PLAN_USAGE_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, paper and live slots, the paper and "
    "live timeframe, screens and studies" + _USAGE_TAIL
)
PLAN_USAGE_LISTED_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, the paper and live timeframe, and "
    "screens and studies where served" + _USAGE_TAIL
)
PLAN_USAGE_LISTED_PAPER_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, paper slots, the paper and live "
    "timeframe, and screens and studies where served" + _USAGE_TAIL
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
        # served per-unit blocks, reset instants and the served names of the
        # plans that include more); mcp-conversion doc 07 §5 / D-14.
        description=PLAN_USAGE_DESCRIPTION,
        listed_description=PLAN_USAGE_LISTED_DESCRIPTION,
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
