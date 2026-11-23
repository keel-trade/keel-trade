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

  and, while the server reports a first-week allowance on the backtest-runs
  block (connect-onboarding spec 01 §1.8), the v3 row's first-week sentence:
  ``Backtests: 38 of 40 used on Free (20 plus your first-week 20); resets to
  20 on Dec 5.`` Compute is silent (PLANS-REVIEW R7): no line until it is
  spent, then its refusal's sentence.

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
    FLOOR_SIDES,
    WEB_APP_SENTENCE,
    assert_plan_copy,
    first_week_bonus,
    fold_first_week,
    join_plan_names,
    limit_unit,
    more_clause,
    more_plan_names,
    own_plan_name,
    project_first_week,
    quota_amount,
    quota_reset_instant,
    render_first_week_clause,
)
from keel.limit_copy import (
    CLOCK_FLOOR_UNIT,
    LABELS,
    LIMIT_ROWS,
    PAPER_CLOCK_FLOOR_UNIT,
    TIER_NO_NEAR_UNITS,
    floor_words,
    render_pieces,
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

#: The research block (spec 02 §L3), only while Research is served.
RESEARCH_KEY = "research"
OPTIONAL_KEYS = OPTIONAL_KEYS | {RESEARCH_KEY}

#: The cap units, one block each.
PAPER_UNIT = "paper_strategies_max"
LIVE_UNIT = "live_strategies_max"
#: The screens caps met through the refresh and publish tools (full profile only), keyed in the ``screens`` block.
SCREEN_CAP_UNITS: dict[str, str] = {
    "live": "screeners_live_max",
    "published": "screeners_published_max",
    "private": "screeners_private_max",
}
SCREEN_UNITS: tuple[str, ...] = tuple(SCREEN_CAP_UNITS.values())
#: The v3 screens units every profile is told (spec 02 §C4.1).
SAVED_UNIT = "screeners_max"
COINS_UNIT = "screen_assets_max"
REFRESH_UNIT = "screener_refresh_min_minutes"
#: Research's units (spec 02 §D).
MAP_UNIT = "research_backtests_max"
REPICK_UNIT = "feature:research_repick"
RESEARCH_TEMPLATE_PREFIX = "feature:research_template_"


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


def _names_block(served: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    """``served`` projected to ``keys`` plus ``more_plans`` (names only), or ``None``."""
    if not isinstance(served, dict):
        return None
    block: dict[str, Any] = {k: served[k] for k in keys if served.get(k) is not None}
    names = more_plan_names(served)
    if names:
        block["more_plans"] = names
    return block


def _floor_block(me: dict) -> dict[str, Any] | None:
    """``{floor, applies?, more_plans, paper, live}`` from the served ``serving_floor``, or ``None``.

    The top-level ``floor`` is what an installed SDK read (the slower failing side on a split plan); ``paper`` and
    ``live`` are each side's own floor and the plans that run that side faster (spec 02 §C4.1, §C4.7: the floor
    split reaches agents)."""
    served = me.get("serving_floor")
    if not isinstance(served, dict):
        served = _plan_status(me).get("serving_floor")
    if not isinstance(served, dict) or not isinstance(served.get("floor"), str):
        return None
    block = _names_block(served, ("floor", "applies")) or {}
    for side in FLOOR_SIDES:
        side_block = _names_block(served.get(side), ("floor",))
        if side_block and isinstance(side_block.get("floor"), str):
            block[side] = side_block
    return block


def _screens_block(me: dict) -> dict[str, Any] | None:
    """``{saved, coins_per_screen, refresh_min_minutes}`` from the served ``plan_status.screens`` (spec 02 §C4.1,
    §L3), on both profiles: the saved-screen cap (counting the screens the Screens list counts, §C4.6), the coins one
    screen may cover, and the fastest a saved screen refreshes, each with the plans that include more (names).
    A unit the plan does not bound is left out: no "unlimited" is ever said (VP D-6)."""
    served = _plan_status(me).get("screens")
    if not isinstance(served, dict):
        return None
    out: dict[str, Any] = {}
    saved = served.get("saved")
    if isinstance(saved, dict) and _count(saved.get("limit")) and not saved.get("unlimited"):
        block: dict[str, Any] = {"unit": SAVED_UNIT, "limit": saved["limit"]}
        if _count(saved.get("used")):
            block["used"] = saved["used"]
            block["remaining"] = max(0, saved["limit"] - saved["used"])
        names = more_plan_names(saved)
        if names:
            block["more_plans"] = names
        out["saved"] = block
    for key in ("coins_per_screen", "refresh_min_minutes"):
        block = _names_block(served.get(key), ("limit",))
        if block and _count(block.get("limit")):
            out[key] = block
    return out or None


def _research_block(me: dict) -> dict[str, Any] | None:
    """``{templates_included, map_max_settings, templates_more?, repick?}`` from the served
    ``plan_status.research`` (spec 02 §C4.1, §L3; absent while Research is off). The studies meter rides ``quota``
    like every meter. The other plans' templates (``templates_more``, the re-pick rows) are the full profile's
    only: the listed profile shows no other plans' features (MC D-12, Q-2080; spec 02 §C3.2), while a limit the
    person reaches still names the plans in its own words (MC D-14)."""
    served = _plan_status(me).get("research")
    if not isinstance(served, dict):
        return None
    out: dict[str, Any] = {}
    included = served.get("templates_included")
    if isinstance(included, list):
        out["templates_included"] = [t for t in included if isinstance(t, str)]
    size = _names_block(served.get("map_max_settings"), ("limit",))
    if size is not None:
        out["map_max_settings"] = {
            "limit": size.get("limit"),
            **({"more_plans": size["more_plans"]} if size.get("more_plans") else {}),
        }
    from . import _toolsets

    if not _toolsets.is_listed_profile():
        more = []
        for entry in served.get("templates_more") or ():
            if isinstance(entry, dict) and isinstance(entry.get("template"), str):
                more.append({"template": entry["template"], "more_plans": more_plan_names(entry)})
        if more:
            out["templates_more"] = more
        repick = served.get("repick")
        if isinstance(repick, dict) and repick.get("included") is False:
            out["repick"] = {"included": False, "more_plans": more_plan_names(repick)}
    return out or None


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


class _Lines:
    """The talking points after the opening: each limit's ``before`` sentence (``keel.limit_copy``, the server's
    rows, G1), with its more clause only where that limit's plans differ from the opening's (doc 07 rule 8: no
    repeated plan words; spec 02 §B: the ladder answer is per gate)."""

    def __init__(self, plan: str | None, opening_names: list[str]):
        self.plan = plan or "this plan"
        self.opening = list(opening_names)
        self.points: list[str] = []

    def add(self, unit: str, names: list[str], slot: str = "before", **facts: Any) -> None:
        pieces = render_pieces(unit, slot, plan_name=self.plan, higher_names=names, **facts)
        if pieces is None:
            return
        tail = pieces.more if names != self.opening else None
        self.points.append(" ".join(p for p in (pieces.head, tail) if p))

    def feature(self, unit: str, names: list[str]) -> None:
        """A feature the plan does not include, inclusion first (the feature wall's head, spec 02 §C3.1, §D):
        ``Signal studies are included on Quant and Pro.`` The names are always said: the opening's "include more"
        is about amounts, never which plan has a feature."""
        pieces = render_pieces(unit, "at", plan_name=self.plan, higher_names=names, kind="feature")
        self.points.append(pieces.head)


def _metered_line(block: dict[str, Any], plan: str | None = None) -> str | None:
    """A meter's line: the v3 ``before`` row (``Backtests: 3 of 20 used on Free; resets Dec 5.``, the first-week
    clause folded in while it has some left) when the server sent its numbers and reset instant;
    ``Backtests: unlimited.`` for an unlimited unit (never a catalog plan's); ``Backtests: 21 of 25 left.`` for an
    older server that sent no reset (Q-1597: no reset is invented). The more clause is the caller's to add."""
    unit = block["unit"]
    if block.get("limit") == "unlimited":
        return f"{_label(unit)}: unlimited."
    limit, remaining = block.get("limit"), block.get("remaining")
    if not _count(limit) or not _count(remaining):
        return None
    used = block.get("used") if _count(block.get("used")) else max(0, limit - remaining)
    when = quota_reset_instant(block)
    if unit in TIER_NO_NEAR_UNITS:
        # A silent unit (compute, PLANS-REVIEW R7): no count, ever. Once spent, its refusal's own sentence ("Backtest
        # time on Free is used up until Dec 5."), which is why a backtest is refused.
        if remaining > 0 or when is None or unit not in LIMIT_ROWS:
            return None
        return render_pieces(
            unit,
            "at",
            plan_name=plan or "this plan",
            higher_names=(),
            used=used,
            limit=limit,
            resets_at=when,
        ).head
    if when is not None and unit in LIMIT_ROWS:
        bonus = first_week_bonus(block)
        pieces = render_pieces(
            unit,
            "before",
            plan_name=plan or "this plan",
            higher_names=(),
            used=used,
            limit=limit,
            resets_at=when,
            first_week=bonus if LIMIT_ROWS[unit].first_week is not None else None,
        )
        return pieces.head if bonus is not None else fold_first_week(pieces.head, block)
    clause = render_first_week_clause(block)
    extras = f" {clause}" if clause else ""
    return f"{_label(unit)}: {quota_amount(unit, remaining)} of {quota_amount(unit, limit)} left{extras}."


def _cap_line(block: dict[str, Any], plan: str | None) -> str | None:
    """``Paper strategies: 1 of 1 running on Free.`` (the v3 ``before`` row) · ``… unlimited.`` · ``… not on
    Free.`` — or ``None`` to render the row."""
    unit = block["unit"]
    if block["limit"] == "unlimited":
        return f"{_label(unit)}: unlimited."
    if block["limit"] == 0:
        return f"{_label(unit)}: not on {plan or 'this plan'}."
    return None


def _opening(plan: str | None, names: list[str]) -> str:
    """``Plan: Free. Trader, Quant and Pro include more; plans are changed in the Keel web app.`` — the names are
    every plan the server says includes more of any limit listed here, in ladder order; none (the top plan) →
    ``Plan: Pro.``"""
    head = f"Plan: {plan or 'unknown'}."
    if not names:
        return head
    verb = "includes" if len(names) == 1 else "include"
    where = WEB_APP_SENTENCE[:1].lower() + WEB_APP_SENTENCE[1:]
    return f"{head} {join_plan_names(names)} {verb} more; {where}"


def _ladder_order(found: list[list[str]], me: dict) -> list[str]:
    """Every name in ``found``, in the catalog's ladder order when the server sent ``offers`` (the paid ladder,
    cheapest first), else in the order first seen."""
    seen: list[str] = []
    for names in found:
        # A stable merge: a name not seen yet goes right after the last name of the SAME list already placed, so
        # every served list keeps its own (ladder) order in the union.
        after = -1
        for name in names:
            if name in seen:
                after = seen.index(name)
            else:
                seen.insert(after + 1, name)
                after += 1
    offers = _plan_status(me).get("offers")
    ladder = (
        [o.get("name") for o in offers if isinstance(o, dict)] if isinstance(offers, list) else []
    )
    rank = {name: i for i, name in enumerate(n for n in ladder if isinstance(n, str))}
    return sorted(seen, key=lambda n: (rank.get(n, len(rank)), seen.index(n)))


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
    screens = _screens_block(me) or {}
    if _screens_served():
        for key, unit in SCREEN_CAP_UNITS.items():
            block = _cap_block(me, unit)
            if block is not None:
                screens[key] = block
    floor = _floor_block(me)
    research = _research_block(me)

    # The opening line names every plan that includes more of something listed below, in ladder order.
    found = [more_plan_names(b) for b in [*blocks, *caps.values(), *screens.values()]]
    if floor:
        found += [floor.get("more_plans") or []]
        found += [(floor.get(side) or {}).get("more_plans") or [] for side in FLOOR_SIDES]
    if research:
        found += [(research.get("map_max_settings") or {}).get("more_plans") or []]
        found += [e["more_plans"] for e in research.get("templates_more") or ()]
    names = _ladder_order(found, me)
    lines = _Lines(plan_name, names)
    for block in blocks:
        line = _metered_line(block, plan_name)
        if line:
            more = more_plan_names(block)
            clause = more_clause("consumable", more) if more != names else None
            lines.points.append(" ".join(p for p in (line, clause) if p))
    for block in (*caps.values(), *(screens[k] for k in SCREEN_CAP_UNITS if k in screens)):
        line = _cap_line(block, plan_name)
        if line is not None:
            lines.points.append(line)
            continue
        lines.add(block["unit"], more_plan_names(block), used=block["used"], limit=block["limit"])
    saved = screens.get("saved")
    if saved is not None:
        lines.add(
            SAVED_UNIT,
            saved.get("more_plans") or [],
            used=saved.get("used", 0),
            limit=saved["limit"],
        )
    coins = screens.get("coins_per_screen")
    if coins is not None:
        # "Trader, Quant and Pro screen every coin." only on the server's word (higher_every_coin, R4).
        every = ((_plan_status(me).get("screens") or {}).get("coins_per_screen") or {}).get(
            "higher_every_coin"
        )
        lines.add(
            COINS_UNIT,
            coins.get("more_plans") or [],
            limit=coins["limit"],
            every_coin=every if isinstance(every, bool) else None,
        )
    refresh = screens.get("refresh_min_minutes")
    if refresh is not None:
        from timeframes import token_for_minutes

        lines.add(
            REFRESH_UNIT, refresh.get("more_plans") or [], floor=token_for_minutes(refresh["limit"])
        )
    if floor is not None:
        sides = [s for s in FLOOR_SIDES if s in floor]
        if sides:
            for side in sides:
                unit = PAPER_CLOCK_FLOOR_UNIT if side == "paper" else CLOCK_FLOOR_UNIT
                lines.add(unit, floor[side].get("more_plans") or [], floor=floor[side]["floor"])
        else:
            lines.points.append(_floor_line(floor, plan_name))
    if research is not None:
        included = research.get("templates_included")
        if included:
            labels = [_TEMPLATE_LABEL(t) for t in included]
            lines.points.append(
                f"Study types on {plan_name or 'this plan'}: {join_plan_names(labels)}."
            )
        size = research.get("map_max_settings")
        if size is not None and _count(size.get("limit")):
            lines.add(MAP_UNIT, size.get("more_plans") or [], limit=size["limit"])
        for entry in research.get("templates_more") or ():
            lines.feature(RESEARCH_TEMPLATE_PREFIX + entry["template"], entry["more_plans"])
        repick = research.get("repick")
        if repick is not None:
            lines.feature(REPICK_UNIT, repick.get("more_plans") or [])
    # The same validator and plan-copy scan as every wall: no price, no destination, no pitch, no expiry urgency.
    # A fact proposes no action, so there is no do-nothing line to require.
    talking_points = validate_talking_points(
        [assert_plan_copy(p) for p in [_opening(plan_name, names), *lines.points]],
        require_do_nothing=False,
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
    if research is not None:
        body[RESEARCH_KEY] = research
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=body)


def _floor_line(block: dict[str, Any], plan: str | None) -> str:
    """An older server's one floor: ``Paper and live on Free run at 1 hour or slower.``"""
    return f"Paper and live on {plan or 'this plan'} run {floor_words(block['floor'])}."


def _TEMPLATE_LABEL(template: str) -> str:  # noqa: N802 — a table lookup, named for the table
    return LABELS.get(RESEARCH_TEMPLATE_PREFIX + template, template.replace("_", " "))


#: One clause per limit kind the caller can meet on the profile (doc 07 §5),
#: no number and no plan name (§3.8, §6.1).
_USAGE_HEAD = (
    "Report the org's own Keel plan, its limits and which plans include more — auth, "
    "identity and visible tools are `keel_account_status`. "
)
_USAGE_TAIL = " — each with its numbers and reset instant. It changes nothing and spends no quota."
#: Counts of things, never "slots" (value-and-pricing spec 02 §C3.6, PLANS-REVIEW R53).
PLAN_USAGE_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, the paper and live strategies running, "
    "the paper and live timeframe, screens and studies" + _USAGE_TAIL
)
PLAN_USAGE_LISTED_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, the paper and live timeframe, and "
    "screens and studies where served" + _USAGE_TAIL
)
PLAN_USAGE_LISTED_PAPER_DESCRIPTION = (
    _USAGE_HEAD + "It covers backtests and compute, the paper strategies running, the "
    "paper and live timeframe, and screens and studies where served" + _USAGE_TAIL
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
