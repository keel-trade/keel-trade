"""Every plan gate's agent sentence, before, near and at (value-and-pricing spec 02 §C2, §D; G1).

The SDK twin of ``libs/platform_auth/limit_copy.py``'s renderer: the same rows, the same templates, the same
grammar (mcp-conversion D-14, doc 07 §4-§5). The SDK's own venv has no ``platform_auth`` (sdk-test.yml runs without
``PYTHONPATH=libs``), so the templates are restated here and pinned, wall for wall, to the table platform_auth
generates from plans.yaml (``libs/platform_auth/fixtures/limit_copy_walls.json``): every catalog plan x every row x
slot renders byte-identically through :func:`render_limit` (``tests/test_limit_copy.py``). A template that drifts
from the server's reds there.

Names only, interpolated from served data: no plan name, other plans' numbers, price, link or pitch word lives in a
template. The caller's own numbers, the reset DATE ("resets Dec 5", spec 02 §C3.5: never "this month"), the free way
forward as a fact, then the plans that include more and where plans change. The agent's instruction is the row's
``suggestion``, never part of the message (§C3.2).

The clause is keyed by the limit's kind, and by its unit where the unit reads differently (PLANS-REVIEW R3, R4): a
cap on what RUNS (paper and live strategies) says ``Pro runs more.``, every other cap ``includes more``; the coins per
screen says ``Trader, Quant and Pro screen every coin.`` when the server says every plan named covers every coin. A
served name equal to the caller's own plan name (a legacy Trader, whose higher plans include the current Trader) reads
"the current Trader plan" (R2). The SDK has no catalog, so "every coin" is never inferred: it is a served fact
(``higher_every_coin``), and without it the clause reads ``include more``, which is true either way.

The near rule is :func:`tier_for` (R17), pinned to the ``tiers`` table of ``limit_copy_cases.json``.
"""

from __future__ import annotations

import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone


#: The limit kinds both renderers key on (doc 07 §5; ``setting`` from spec 02 §C2: a size bound per action, the
#: coins per screen and the settings map size; it reads like a consumable, has no near notice and never resets).
KINDS: tuple[str, ...] = ("consumable", "cap", "floor", "feature", "setting")

#: kind -> (verb after one name, verb after several).
VERBS: dict[str, tuple[str, str]] = {
    "consumable": ("includes more", "include more"),
    "cap": ("includes more", "include more"),
    "floor": ("runs faster", "run faster"),
    "feature": ("includes it", "include it"),
    "setting": ("includes more", "include more"),
}

#: The caps on what runs: their clause is ``runs more`` / ``run more`` (PLANS-REVIEW R3). Every other cap is on what
#: is kept and ``includes more``.
RUN_MORE_UNITS: frozenset[str] = frozenset({"paper_strategies_max", "live_strategies_max"})
_RUN_VERBS: tuple[str, str] = ("runs more", "run more")

#: The coins per screen, and its clause when every plan named covers every coin.
COINS_UNIT = "screen_assets_max"
_EVERY_COIN_VERBS: tuple[str, str] = ("screens every coin", "screen every coin")

#: A coins-per-screen cap at or above this is "every coin" (D-6's finite "every coin": the most coins a Universe may
#: name; platform_auth's ``config.EVERY_COIN_LIMIT``, pinned equal by ``tests/test_limit_copy.py``).
EVERY_COIN_LIMIT = 500


def is_every_coin(limit: int | None) -> bool:
    """True when a coins-per-screen cap covers every coin (:data:`EVERY_COIN_LIMIT`)."""
    return isinstance(limit, int) and not isinstance(limit, bool) and limit >= EVERY_COIN_LIMIT


#: Where plans are changed, in plain words: never a URL (D-12, D-14 rule 4).
PLANS_CHANGED = "Plans are changed in the Keel web app."

#: The paper floor (plans v3 split it from the live one, brief §3 C) and the live/refusal floor unit.
PAPER_CLOCK_FLOOR_UNIT = "paper_clock_floor"
CLOCK_FLOOR_UNIT = "paper_live_clock_floor"
FLOOR_UNITS: frozenset[str] = frozenset({PAPER_CLOCK_FLOOR_UNIT, CLOCK_FLOOR_UNIT})


def join_names(names: Sequence[str]) -> str:
    """``Pro`` · ``Trader and Pro`` · ``Trader, Quant and Pro``."""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def clause_verbs(
    kind: str, unit: str | None = None, *, every_coin: bool = False
) -> tuple[str, str]:
    """``(after one name, after several)`` for ``kind``, and by ``unit``: a cap on what runs (:data:`RUN_MORE_UNITS`)
    runs more; the coins per screen, when ``every_coin`` (every plan named covers every coin), screens every coin.
    An unknown ``kind`` raises."""
    if kind not in VERBS:
        raise ValueError(f"unknown limit kind {kind!r}; expected one of {KINDS}")
    if kind == "cap" and unit in RUN_MORE_UNITS:
        return _RUN_VERBS
    if kind == "setting" and unit == COINS_UNIT and every_coin:
        return _EVERY_COIN_VERBS
    return VERBS[kind]


def _clean(names: Sequence[str]) -> list[str]:
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]


def plan_names_words(names: Sequence[str], own: str | None = None) -> str | None:
    """``"Trader, Quant and Pro"``: the served names, joined, with a name equal to ``own`` (the caller's own plan's
    name) as "the current {own} plan" (PLANS-REVIEW R2). ``None`` for no names. Lower case: the caller capitalises a
    sentence that starts with it."""
    clean = _clean(names)
    if not clean:
        return None
    return join_names([f"the current {n} plan" if own and n == own else n for n in clean])


def more_clause(
    names: Sequence[str],
    kind: str = "consumable",
    *,
    unit: str | None = None,
    own: str | None = None,
    every_coin: bool | None = None,
) -> str | None:
    """``"Trader and Pro include more."`` for the served names of the plans that beat the caller on one limit;
    ``None`` when there are none (the top plan). ``unit`` picks a cap's or the coins' verb (:func:`clause_verbs`);
    ``every_coin`` is the served fact that every plan named covers every coin (absent: not known, so "include
    more"); ``own`` is the caller's own plan name (:func:`plan_names_words`). An unknown ``kind`` raises."""
    one, many = clause_verbs(kind, unit, every_coin=bool(every_coin))
    joined = plan_names_words(names, own)
    if joined is None:
        return None
    return f"{joined[:1].upper()}{joined[1:]} {one if len(_clean(names)) == 1 else many}."


def wall_more(
    names: Sequence[str],
    kind: str = "consumable",
    *,
    unit: str | None = None,
    own: str | None = None,
    every_coin: bool | None = None,
) -> str | None:
    """The clause at a wall: :func:`more_clause` plus :data:`PLANS_CHANGED`, or ``None`` when no plan includes
    more (the sentence never stands alone)."""
    clause = more_clause(names, kind, unit=unit, own=own, every_coin=every_coin)
    return None if clause is None else f"{clause} {PLANS_CHANGED}"


# ── The near rule (PLANS-REVIEW R17; platform_auth.quota.tier_for) ─────────────

#: The share-used tiers, highest first: 90% ``critical``, 75% ``warn``, 50% ``notice`` (a bare count no surface words
#: as a nudge).
TIER_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("critical", 0.9),
    ("warn", 0.75),
    ("notice", 0.5),
)
#: The percentage tiers apply only to a meter of at least this many ("near" is a moment, not a state).
TIER_SMALL_LIMIT = 10
#: A smaller meter's one near notice: at its last unit...
TIER_LAST_LEFT_DEFAULT = 1
#: ...except AI messages, which keep spec 02 §C5.7's line in the reply that leaves 2.
TIER_LAST_LEFT: dict[str, int] = {"ai_messages": 2}
#: Meters never WORDED as a count (spec 02 §C6, R7), named only in their own refusal: a words property only (the
#: server's Q-8111). Their tier is computed like every other meter's (:func:`tier_for`); whether a surface may say it
#: is :func:`worded_tier`. ``platform_auth.quota.SILENT_UNITS``.
SILENT_UNITS: frozenset[str] = frozenset(
    {"backtest_compute_seconds", "research_compute_seconds", "screen_refresh_seconds"}
)
#: The name the SDK exported before Q-8111; the same set, read as "never worded".
TIER_NO_NEAR_UNITS = SILENT_UNITS


def is_silent(unit: str) -> bool:
    """True when no surface words ``unit``'s count (:data:`SILENT_UNITS`)."""
    return unit in SILENT_UNITS


def tier_last_left(unit: str) -> int:
    """How many are left at a small meter's one near notice (:data:`TIER_LAST_LEFT`)."""
    return TIER_LAST_LEFT.get(unit, TIER_LAST_LEFT_DEFAULT)


def tier_for(unit: str, *, limit: int | None, used: int | None) -> str:
    """THE tier rule, the SDK twin of ``platform_auth.quota.tier_for``, "what tier is it", silent meters included:
    ``ok`` | ``notice`` | ``warn`` | ``critical`` | ``exhausted``. Nothing granted or unlimited: ``ok``; nothing
    left: ``exhausted``; a meter under :data:`TIER_SMALL_LIMIT`: ``critical`` at :func:`tier_last_left` or fewer
    left, else ``ok``; otherwise by share used. Whether a surface may SAY it is :func:`worded_tier`."""
    if not limit:
        return "ok"
    used = used or 0
    remaining = max(0, limit - used)
    if remaining <= 0:
        return "exhausted"
    if limit < TIER_SMALL_LIMIT:
        return "critical" if remaining <= tier_last_left(unit) else "ok"
    for name, threshold in TIER_THRESHOLDS:
        if used / limit >= threshold:
            return name
    return "ok"


def worded(unit: str, tier: str) -> str:
    """The tier a surface may WORD, from a tier already computed: a silent unit's reads ``ok`` unless it is
    ``exhausted`` (its own refusal); any other unit's is itself (``platform_auth.quota.worded``)."""
    return "ok" if is_silent(unit) and tier != "exhausted" else tier


def worded_tier(unit: str, *, limit: int | None, used: int | None) -> str:
    """ "Should we say it": :func:`tier_for` through :func:`worded` (``platform_auth.quota.worded_tier``)."""
    return worded(unit, tier_for(unit, limit=limit, used=used))


def tier_rule() -> dict:
    """:func:`tier_for`'s constants as data, in the shape ``platform_auth.quota.tier_rule`` ships."""
    return {
        "thresholds": {name: threshold for name, threshold in TIER_THRESHOLDS},
        "small_limit": TIER_SMALL_LIMIT,
        "last_left_default": TIER_LAST_LEFT_DEFAULT,
        "last_left": dict(sorted(TIER_LAST_LEFT.items())),
        "silent_units": sorted(SILENT_UNITS),
    }


def reset_words(resets_at: datetime) -> str:
    """``"Dec 5"``: the reset's UTC date (spec 02 §C3.5). The exact instant stays in the structured ``resets_at``
    field; the sentence carries the date only."""
    if resets_at.tzinfo is None:
        raise ValueError("resets_at must be timezone-aware")
    day = resets_at.astimezone(timezone.utc)
    return f"{day:%b} {day.day}"


def floor_words(floor: str) -> str:
    """``"daily"`` for a one-day floor, else ``"at 1 hour or slower"``."""
    from timeframes import label

    return "daily" if floor == "1d" else f"at {label(floor)} or slower"


def coins_words(limit: int) -> str:
    """A coin cap in words: ``"every coin"`` at or above :data:`EVERY_COIN_LIMIT` (R4: the catalogs' words), else
    ``"up to 30"``."""
    return "every coin" if is_every_coin(limit) else f"up to {limit:,}"


@dataclass(frozen=True)
class FeatureName:
    """How a feature wall names what the plan lacks (spec 02 §C3.1, §D: lead with inclusion, "Signal studies are
    included on Quant and Pro."): ``subject`` and whether it takes a plural verb."""

    subject: str
    plural: bool = True

    def included_on(self, names_words: str) -> str:
        return f"{self.subject} {'are' if self.plural else 'is'} included on {names_words}."

    def not_on(self, plan: str) -> str:
        return f"{self.subject} {'aren' if self.plural else 'isn'}'t on {plan}."


@dataclass(frozen=True)
class LimitRow:
    """One unit's agent copy. ``before`` is its ``keel_plan_usage`` line, ``near`` a successful result's notice at
    the quota tiers (meters only; ``None`` where spec 02 §D says "—"), ``at`` the refusal's first sentence and
    ``free_way`` the fact that follows it when a free way forward exists. ``paused`` (spec 02 §C4.3a) is the
    sentence of a saved thing the limit has paused rather than refused (a saved screen over the coin cap: its
    refresh), rendered like ``at`` with the plans and where they change. ``first_week`` holds a meter's slot
    templates while a first-week bonus is in its count (R16: only the period's allowance comes back at the reset,
    never the bonus). ``feature`` names a feature row's subject; its ``at`` then leads with inclusion and the
    names."""

    unit: str
    before: str | None
    near: str | None
    at: str
    free_way: str | None = None
    suggestion: str = "Plan limit; don't retry before the reset. Tell the user the numbers and which plans include more."
    paused: str | None = None
    first_week: dict[str, str] | None = None
    feature: FeatureName | None = None


_CAP_SUGGESTION = (
    "Plan limit. Tell the user the numbers and which plans include more; ask before stopping "
    "or replacing anything of theirs."
)
_FLOOR_SUGGESTION = (
    "Plan limit. Tell the user the timeframe the plan runs and which plans run faster; offer "
    "the admitted timeframe, and never suggest live trading to get around a paper limit."
)
_FEATURE_SUGGESTION = (
    "Plan limit. Tell the user what the plan includes and which plans include this; offer "
    "what the plan does run."
)
_TEMPLATE_SUGGESTION = (
    "Plan limit. Tell the user which plans include this study type; offer a Check on their "
    "plan, and Keel's example study of this type."
)
#: Spec 02 §C4.4: the never-split rule, read at the moment it matters.
_COINS_SUGGESTION = (
    "Plan limit. Tell the user the coin limit and which plans include more; use the top "
    "{limit} or a {limit}-coin pick. Don't split the screen into several runs to get past it."
)

_METER_BEFORE = "{Label}: {used} of {limit} used on {plan}; resets {reset}."
_METER_NEAR = "{left} of {limit} {label} left; resets {reset}."
_METER_AT = "{Label}: {used} of {limit} used on {plan}; resets {reset}."
_BONUS = "({plan_limit} plus your first-week {first_week})"
_METER_FIRST_WEEK: dict[str, str] = {
    "before": "{Label}: {used} of {limit} used on {plan} "
    + _BONUS
    + "; resets to {plan_limit} on {reset}.",
    "near": "{left} of {limit} {label} left " + _BONUS + "; resets to {plan_limit} on {reset}.",
    "at": "{Label}: {used} of {limit} used on {plan} "
    + _BONUS
    + "; {plan_limit} come back on {reset}.",
}
_CAP_BEFORE = "{Label}: {used} of {limit} on {plan}."
_CAP_AT = "{Label}: {used} of {limit} on {plan}."
_FEATURE_AT = "{Label} isn't on {plan}."
#: A study type's included alternative (spec 02 §C4.8): the Check, on every plan.
_CHECK_INSTEAD = "{plan} runs the Check, which covers costs, delay and chance."

#: The founder's screens fact, at the backtest wall and nowhere else (PLANS-REVIEW R6): screen runs spend backtests,
#: refreshes don't.
_BACKTESTS_FREE_WAY = (
    "Screen runs use the same backtests. Your results, paper runs and saved screens keep working, and saved screens "
    "keep refreshing without using any."
)
#: Research's features by the catalog's names (R5), with each one's free way and instruction.
_RESEARCH_FEATURES: tuple[tuple[str, FeatureName, str | None, str], ...] = (
    ("feature:research", FeatureName("Studies"), None, _FEATURE_SUGGESTION),
    ("feature:research_template_check", FeatureName("Checks"), None, _TEMPLATE_SUGGESTION),
    (
        "feature:research_template_signal",
        FeatureName("Signal studies"),
        _CHECK_INSTEAD,
        _TEMPLATE_SUGGESTION,
    ),
    (
        "feature:research_template_compare",
        FeatureName("Compare", plural=False),
        _CHECK_INSTEAD,
        _TEMPLATE_SUGGESTION,
    ),
    (
        "feature:research_template_entries",
        FeatureName("Entries and exits studies"),
        _CHECK_INSTEAD,
        _TEMPLATE_SUGGESTION,
    ),
    (
        "feature:research_template_map",
        FeatureName("Settings maps"),
        _CHECK_INSTEAD,
        _TEMPLATE_SUGGESTION,
    ),
    (
        "feature:research_repick",
        FeatureName("Nearby settings and re-pick"),
        "The rest of the Check runs on {plan}.",
        _FEATURE_SUGGESTION,
    ),
    (
        "feature:research_agent",
        FeatureName("Research with your agent", plural=False),
        None,
        _FEATURE_SUGGESTION,
    ),
    (
        "feature:research_copy",
        FeatureName("Making a study yours", plural=False),
        None,
        _FEATURE_SUGGESTION,
    ),
)

#: Every unit an agent (MCP, CLI and SDK, or the in-app chat) can be refused on, and both floors: the same keys as
#: platform_auth's ``LIMIT_ROWS`` (G1, ``tests/test_limit_copy.py`` derives that set from plans.yaml and the libs
#: exemption table).
LIMIT_ROWS: dict[str, LimitRow] = {
    row.unit: row
    for row in (
        LimitRow(
            "backtest_runs",
            _METER_BEFORE,
            _METER_NEAR,
            _METER_AT,
            _BACKTESTS_FREE_WAY,
            first_week=_METER_FIRST_WEEK,
        ),
        LimitRow(
            "backtest_compute_seconds",
            None,  # a silent unit (spec 02 §C6; R7): never a count, only its refusal
            None,
            "Backtest time on {plan} is used up until {reset}.",
            "Results and paper runs keep working.",
        ),
        LimitRow(
            "ai_messages",
            _METER_BEFORE,
            # §C5.7: the reply that leaves 2 ends with this one line.
            "You have {left} {label} left until {reset}. Your own AI agent connected to Keel can keep building "
            "with no message limit.",
            _METER_AT,
            "Your own AI agent connected to Keel keeps building with no message limit.",
            first_week={
                "before": _METER_FIRST_WEEK["before"],
                "near": "You have {left} {label} left until {reset}. Your own AI agent connected to Keel can keep "
                "building with no message limit.",
                "at": _METER_FIRST_WEEK["at"],
            },
        ),
        LimitRow(
            "research_studies",
            _METER_BEFORE,
            _METER_NEAR,
            _METER_AT,
            "A planned study keeps its plan and runs once the studies reset.",
        ),
        LimitRow(
            "research_compute_seconds",
            None,  # a silent ceiling (D-64): named only in its own refusal
            None,
            "{Label} on {plan} is used up until {reset}.",
            "A planned study keeps its plan and runs then.",
        ),
        LimitRow(
            "paper_strategies_max",
            "{Label}: {used} of {limit} running on {plan}.",
            None,
            "{Label}: {used} of {limit} running on {plan}.",
            "Stopping one frees it, and a newer version of a running strategy is an update, not a new one.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "live_strategies_max",
            "{Label}: {used} of {limit} running on {plan}.",
            None,
            "{Label}: {used} of {limit} running on {plan}.",
            "Stopping one frees it; live strategies are managed in the Keel web app.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "screeners_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Delete or archive one, or run it without saving.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "screeners_published_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Unpublishing one frees it.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "screeners_live_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Turning one's refresh off frees it.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "screeners_private_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Turning one's refresh off frees it.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "screen_assets_max",
            "Coins per screen: {coins} on {plan}.",
            None,
            "{plan} screens cover up to {limit} coins; this one covers {resolved}.",
            None,
            _COINS_SUGGESTION,
            paused="This screen covers {resolved} coins; {plan} screens cover up to {limit}. Its refresh is paused.",
        ),
        LimitRow(
            "screener_refresh_min_minutes",
            "Saved screens on {plan} refresh {floor}.",
            None,
            "Saved screens on {plan} refresh {floor}.",
            "Running it yourself works at any time.",
            _FLOOR_SUGGESTION,
        ),
        LimitRow(
            PAPER_CLOCK_FLOOR_UNIT,
            "Paper on {plan} runs {floor}.",
            None,
            "Paper on {plan} runs {floor}; this strategy is {requested}.",
            "Change `target_timeframe` to {admitted} to deploy it; the backtest stays as it is.",
            _FLOOR_SUGGESTION,
        ),
        LimitRow(
            CLOCK_FLOOR_UNIT,
            "Live on {plan} runs {floor}.",
            None,
            "Live on {plan} runs {floor}; this strategy is {requested}.",
            "Change `target_timeframe` to {admitted} to deploy it; the backtest stays as it is.",
            _FLOOR_SUGGESTION,
        ),
        LimitRow(
            "research_backtests_max",
            "Settings maps on {plan} cover up to {limit} settings.",
            None,
            "Settings maps on {plan} cover up to {limit} settings; this one has {settings}.",
            "Narrowing an axis brings it within {limit}.",
            _FEATURE_SUGGESTION,
        ),
        *(
            LimitRow(unit, None, None, _FEATURE_AT, free_way, suggestion, feature=name)
            for unit, name, free_way, suggestion in _RESEARCH_FEATURES
        ),
        LimitRow(
            "research_shares_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Revoking a share frees it.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "research_saved_studies_max",
            _CAP_BEFORE,
            None,
            _CAP_AT,
            "Deleting a study frees it.",
            _CAP_SUGGESTION,
        ),
        LimitRow(
            "research_evidence_bytes_max",
            None,
            None,
            "{Label} on {plan} are full.",
            "Deleting a study frees its results.",
            _CAP_SUGGESTION,
        ),
    )
}

#: Each row's noun when the server sent no label: ``platform_auth.quota.UNIT_LABELS`` (a test reads that source and
#: pins every entry equal), and the floors' own words.
LABELS: dict[str, str] = {
    "backtest_runs": "backtests",
    "backtest_compute_seconds": "backtest compute time",
    "ai_messages": "AI messages",
    "research_studies": "studies",
    "research_compute_seconds": "research time",
    "paper_strategies_max": "paper strategies",
    "live_strategies_max": "live strategies",
    "screeners_max": "saved screens",
    "screeners_published_max": "public screens",
    "screeners_live_max": "auto-refreshing screens",
    "screeners_private_max": "private auto-refreshing screens",
    "screen_assets_max": "coins per screen",
    "screener_refresh_min_minutes": "screen refresh",
    "research_backtests_max": "settings in a map",
    "feature:research": "running studies",
    "feature:research_template_check": "Check a strategy",
    "feature:research_template_signal": "Study a signal",
    "feature:research_template_compare": "Compare",
    "feature:research_template_entries": "Entries and exits",
    "feature:research_template_map": "Map a strategy's settings",
    "feature:research_repick": "Re-picking settings",
    "feature:research_agent": "research with your agent",
    "feature:research_copy": "making a study yours",
    "research_shares_max": "shared studies",
    "research_saved_studies_max": "saved studies",
    "research_evidence_bytes_max": "stored study results",
    PAPER_CLOCK_FLOOR_UNIT: "paper timeframe",
    CLOCK_FLOOR_UNIT: "live timeframe",
}

#: The slots a row renders.
SLOTS: tuple[str, ...] = ("before", "near", "at", "paused")


def kind_of_row(unit: str) -> str:
    """The more clause's kind for a row (the libs ``_kind_of_row`` over ``unit_kind``)."""
    if unit in FLOOR_UNITS or unit == "screener_refresh_min_minutes":
        return "floor"
    if unit in ("screen_assets_max", "research_backtests_max"):
        return "setting"
    if unit.startswith("feature:"):
        return "feature"
    if unit.endswith("_max"):
        return "cap"
    return "consumable"


def row_kind(unit: str, limit: int | None = None) -> str:
    """The kind a row renders as (platform_auth's ``row_kind``): the row's kind, and a cap of 0 (a plan that grants
    none, v1/v2 Free's private screens; R30) as a feature."""
    kind = kind_of_row(unit)
    if kind == "cap" and limit == 0:
        return "feature"
    return kind


def _count(n: int | None) -> str:
    return "" if n is None else f"{n:,}"


def _format(template: str, fields: dict, unit: str, slot: str) -> str:
    wanted = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    hole = sorted(name for name in wanted if fields.get(name, "") == "")
    if hole:
        raise ValueError(f"{unit} {slot}: no value for {hole}")
    return template.format(**fields)


@dataclass(frozen=True)
class Pieces:
    """A rendered sentence in its parts: ``head`` (what was reached, or the limit), ``free_way`` (the fact that
    follows it at a wall) and ``more`` (the more clause, plus "Plans are changed …" at a wall). Joined with one
    space they are :func:`render_limit`'s text; the wall's talking points and card keep them apart."""

    head: str
    free_way: str | None
    more: str | None

    def text(self) -> str:
        return " ".join(p for p in (self.head, self.free_way, self.more) if p)


def render_limit(unit: str, slot: str, **facts) -> str | None:
    """One unit's agent sentence for ``slot`` (``before``, ``near`` or ``at``), identical to platform_auth's
    ``render_limit`` (the facts are :func:`render_pieces`'s)."""
    pieces = render_pieces(unit, slot, **facts)
    return None if pieces is None else pieces.text()


def render_pieces(
    unit: str,
    slot: str,
    *,
    plan_name: str,
    higher_names: Sequence[str],
    label: str | None = None,
    used: int | None = None,
    limit: int | None = None,
    resets_at: datetime | None = None,
    requested: str | None = None,
    floor: str | None = None,
    admitted: str | None = None,
    resolved: int | None = None,
    settings: int | None = None,
    first_week: int | None = None,
    every_coin: bool | None = None,
    kind: str | None = None,
) -> Pieces | None:
    """One unit's agent sentence for ``slot``, in its :class:`Pieces`.

    ``at`` is the four-part refusal (spec 02 §C2): what was reached with the user's number, the free way forward
    when there is one, the plans that include more and "Plans are changed in the Keel web app.". ``before`` and
    ``near`` end with the more clause alone. ``None`` for a slot the row has no sentence for (a cap's near, a silent
    unit's before and near). The top plan gets no clause (``higher_names`` empty). A template field it needs and did
    not get raises: no sentence renders with a hole in it. ``label`` is the served label (the row's :data:`LABELS`
    noun when absent).

    A feature (and a cap of 0, ``limit=0``, or ``kind="feature"``: a plan that grants none) leads with inclusion,
    "Signal studies are included on Quant and Pro.", then the included alternative and where plans change.
    ``first_week`` is the first-week bonus inside ``limit`` (a meter's count while the grant is active), so the
    sentence says only the period's own allowance comes back. ``every_coin`` is the served fact that every plan named
    covers every coin. A coins-per-screen refusal on an every-coin cap raises: no screen can be over it.
    """
    if slot not in SLOTS:
        raise ValueError(f"unknown slot {slot!r}; expected one of {SLOTS}")
    row = LIMIT_ROWS.get(unit)
    if row is None:
        raise ValueError(f"no LIMIT_ROWS row for {unit!r}")
    clause_kind = kind or row_kind(unit, limit)
    if unit == COINS_UNIT and slot != "before" and is_every_coin(limit):
        raise ValueError(
            f"{COINS_UNIT} {slot}: a cap of {limit} covers every coin, so no screen is over it"
        )
    noun = label or LABELS.get(unit, "")
    if clause_kind == "feature":
        return _feature_pieces(
            row, slot, plan_name=plan_name, names=_clean(higher_names), noun=noun
        )
    template = getattr(row, slot)
    if first_week is not None:
        if row.first_week is None:
            raise ValueError(f"{unit} has no first-week sentence")
        if limit is None or not 0 < first_week < limit:
            raise ValueError(f"{unit}: first_week={first_week} must sit inside limit={limit}")
        template = row.first_week.get(slot, template)
    if template is None:
        return None
    plan_limit = None if first_week is None or limit is None else limit - first_week
    fields = {
        "label": noun,
        "Label": noun[:1].upper() + noun[1:],
        "plan": plan_name,
        "used": _count(used),
        "limit": _count(limit),
        "left": "" if used is None or limit is None else f"{max(0, limit - used):,}",
        "reset": "" if resets_at is None else reset_words(resets_at),
        "requested": requested or "",
        "floor": "" if floor is None else floor_words(floor),
        "admitted": admitted or "",
        "resolved": _count(resolved),
        "settings": _count(settings),
        "coins": "" if limit is None else coins_words(limit),
        "first_week": _count(first_week),
        "plan_limit": _count(plan_limit),
    }
    head = _format(template, fields, unit, slot)
    free_way = None
    names = list(higher_names)
    if slot in ("at", "paused"):
        if slot == "at" and row.free_way is not None:
            free_way = _format(row.free_way, fields, unit, slot)
        more = wall_more(names, clause_kind, unit=unit, own=plan_name, every_coin=every_coin)
    else:
        more = more_clause(names, clause_kind, unit=unit, own=plan_name, every_coin=every_coin)
    return Pieces(head, free_way, more)


def _feature_pieces(
    row: LimitRow, slot: str, *, plan_name: str, names: list[str], noun: str
) -> Pieces | None:
    """A feature's wall (spec 02 §C3.1, §D): only ``at``; inclusion first, then the included alternative (a study
    type's Check) and where plans change. A cap of 0 reads as its plural label and has no free way (nothing to
    free on a plan that grants none)."""
    if slot != "at":
        return None
    name = row.feature or FeatureName(noun[:1].upper() + noun[1:])
    joined = plan_names_words(names, plan_name)
    head = name.included_on(joined) if joined else name.not_on(plan_name)
    free_way = None
    if row.feature is not None and row.free_way is not None:
        free_way = _format(row.free_way, {"plan": plan_name}, row.unit, slot)
    return Pieces(head, free_way, PLANS_CHANGED if joined else None)


def feature_absent(unit: str, plan_name: str, *, label: str | None = None) -> str:
    """What happened at a feature wall, as a card headline: ``Signal studies aren't on Free.`` (the wall itself leads
    with inclusion)."""
    row = LIMIT_ROWS[unit]
    noun = label or LABELS.get(unit, "")
    return (row.feature or FeatureName(noun[:1].upper() + noun[1:])).not_on(plan_name)


def render_suggestion(unit: str, *, limit: int | None = None) -> str:
    """The row's instruction for the agent (never in the wall message, §C3.2). The coin cap's names its limit, so an
    every-coin one (never refused) raises; a cap of 0 reads as a feature's."""
    row = LIMIT_ROWS[unit]
    if unit == COINS_UNIT and is_every_coin(limit):
        raise ValueError(f"{COINS_UNIT}: a cap of {limit} covers every coin; no suggestion")
    if row_kind(unit, limit) == "feature" and row.feature is None:
        return _FEATURE_SUGGESTION
    return row.suggestion.format(limit=_count(limit))


__all__ = [
    "COINS_UNIT",
    "CLOCK_FLOOR_UNIT",
    "EVERY_COIN_LIMIT",
    "FeatureName",
    "RUN_MORE_UNITS",
    "TIER_LAST_LEFT",
    "SILENT_UNITS",
    "TIER_NO_NEAR_UNITS",
    "TIER_SMALL_LIMIT",
    "TIER_THRESHOLDS",
    "clause_verbs",
    "coins_words",
    "feature_absent",
    "is_every_coin",
    "plan_names_words",
    "row_kind",
    "is_silent",
    "tier_for",
    "tier_last_left",
    "tier_rule",
    "worded",
    "worded_tier",
    "FLOOR_UNITS",
    "KINDS",
    "LABELS",
    "LIMIT_ROWS",
    "LimitRow",
    "PAPER_CLOCK_FLOOR_UNIT",
    "PLANS_CHANGED",
    "Pieces",
    "SLOTS",
    "VERBS",
    "floor_words",
    "join_names",
    "kind_of_row",
    "more_clause",
    "render_limit",
    "render_pieces",
    "render_suggestion",
    "reset_words",
    "wall_more",
]
