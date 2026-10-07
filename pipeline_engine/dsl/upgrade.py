"""Position-layer upgrade planner (Q-2448; position-layer spec 04 §2.2).

Nine components are deprecated in place (D-02, D-23): the five per-trade
exits, ``StopDistanceRiskSizer`` and the three position managers. They keep
running byte-identically forever; this module is how an AGENT upgrades a
strategy that uses them, on request, into the position layer's form —
``TradeManager`` with each exit as a named rule ending in an action, then
``Exposure()`` (D-39.1) or ``RiskSizer`` (D-23).

**The planner works on the strategy AS WRITTEN** — unexpanded, factories and
variables intact — because the edits address the user's text that way
(``dsl/edits.py``'s path grammar). Nothing here rewrites a stored strategy:
the agent saves the result as a new version (D-02).

Three steps, three functions:

- :func:`plan_upgrade` finds every **position group** — one deprecated
  manager with every deprecated exit whose output reaches it and every
  deprecated sizer downstream of it, or a deprecated exit/sizer that reaches
  no manager — and classifies each as ``mechanical`` (a ready edit),
  ``assisted`` (a ready edit once scripted questions are answered) or
  ``manual`` (no edit; the ``entry_exit_patterns`` help topic). Every
  deprecated instance belongs to exactly one group (the coverage invariant).
- :func:`render_upgrade` produces the upgraded TEXT through the
  comment-preserving primitives of ``dsl/edits.py``: every byte outside the
  edited spans is unchanged, branch names are kept verbatim (D-22), and the
  multiset of comments is equal. The result is parse-verified.
- :func:`apply_upgrade` is ``render_upgrade`` plus the full 04-R18
  verification: the result validates with ZERO errors under the evolved lock,
  no deprecation issue remains for an upgraded instance, the comment
  multiset is preserved, and every pin of a component still present is kept.
  It never falls back to whole-file re-emission (that would lose comments).

**Recipes** (:data:`RECIPES`) are the one table of what replaces what. A
deprecated class declares ``replacement = recipe_shape("<id>")`` so the
registry's structured replacement and the planner cannot drift.

Arithmetic is today's (D-05, 04-R13): stops, targets and holds are inclusive
(``inclusive=True`` is written explicitly), the trailing stop is the
DIFFERENCE ``GiveBack() − m·ATR > 0`` (exact on a zero-ATR bar), and the time
exit counts bars from the trade's open.

Quick Start:
    >>> from pipeline_engine.dsl.parser import parse_strategy
    >>> from pipeline_engine.dsl.upgrade import plan_upgrade, render_upgrade
    >>> plan = plan_upgrade(parse_strategy(src), lock={})
    >>> [g.mode for g in plan.groups]
    ['mechanical']
    >>> new_src = render_upgrade(src, plan)
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline_engine.base.registry_types import KnownIssue, ReplacementShape
from pipeline_engine.dsl import edits
from pipeline_engine.dsl.parser import DSLParseError, parse_strategy
from pipeline_engine.dsl.spec import (
    ComponentRef,
    FactoryCallSpec,
    ParallelSpec,
    PipelineSpec,
    SlotExtractSpec,
    SlotLoadSpec,
    SlotStoreSpec,
    SlotStoreValueSpec,
    StrategyFile,
    VariableRef,
)
from pipeline_engine.exceptions import StructuredError


#: The recipe family every upgrade payload names (04-R16).
RECIPE_FAMILY = "position-layer/v1"
#: The known issue the five exits and ``StopDistanceRiskSizer`` carry.
Q2448 = "Q-2448"
#: Its summary, fixed by spec 04-R2 (one line, under the 240-character cap).
Q2448_SUMMARY = (
    "Its private copy of the current trade drifts from the position after another exit "
    "or a re-entry, so stops and targets can be measured from a stale price."
)
#: THE ``known_issue`` the six known-issue carriers declare at the flip
#: (T04-49): one object, so the registry, the gate and every message agree.
Q2448_ISSUE = KnownIssue(id=Q2448, summary=Q2448_SUMMARY)
#: The help topic a manual group points at (spec 05 owns its content).
MANUAL_HELP_TOPIC = "entry_exit_patterns"


# ═══════════════════════════════════════════════════════════════════════════
# errors
# ═══════════════════════════════════════════════════════════════════════════


class UpgradeAnswerError(StructuredError):
    """An assisted upgrade's answers are missing or invalid (04-R15).

    ``code`` is ``UPGRADE_ANSWER_MISSING`` or ``UPGRADE_ANSWER_INVALID``;
    ``question`` names the question. There is no default answer.
    """

    tier = "static"
    recoverable = True


class UpgradeVerificationError(StructuredError):
    """An applied upgrade failed a 04-R18 check; the text is never returned.

    ``code`` is ``UPGRADE_VERIFICATION_FAILED``; ``check`` names the check.
    """

    tier = "static"


def _verification_failed(check: str, detail: str) -> UpgradeVerificationError:
    return UpgradeVerificationError(
        code="UPGRADE_VERIFICATION_FAILED",
        remediation=(
            "The upgrade could not be applied safely, so nothing was changed. Upgrade "
            "this strategy by hand (see the entry_exit_patterns help topic) or report it."
        ),
        detail=f"{check}: {detail}",
        check=check,
    )


# ═══════════════════════════════════════════════════════════════════════════
# value rendering + recipe templates
# ═══════════════════════════════════════════════════════════════════════════


def _plain(value: Any) -> str:
    """A value as an agent reads it in one line of prose (``0.05``, ``14``)."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _render(value: Any, kind: str | None) -> str:
    """Render a placeholder value as DSL text (canonical floats, ``"`` strings)."""
    from pipeline_engine.dsl.emitter import _emit_value

    if kind is None:
        return _plain(value)
    if kind == "float":
        return _emit_value(float(value), target_type=float)
    if kind == "neg":
        return _emit_value(-float(value), target_type=float)
    if kind == "int":
        return str(int(value))
    if kind == "str":
        return edits.render_value(str(value), prefer_quote='"')
    raise ValueError(f"unknown placeholder kind {kind!r}")  # pragma: no cover


_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)(?::(float|int|str|neg))?\}")


def fill_template(template: str, values: Mapping[str, Any]) -> str:
    """Substitute ``{name}`` / ``{name:kind}`` placeholders.

    Only identifier placeholders match, so a literal dict brace
    (``{"fast": ...}``) is never mistaken for one. A missing value raises.
    """

    def _sub(m: re.Match) -> str:
        name, kind = m.group(1), m.group(2)
        if name not in values:
            raise KeyError(f"template placeholder {{{name}}} has no value")
        return _render(values[name], kind)

    return _PLACEHOLDER.sub(_sub, template)


def template_placeholders(template: str) -> set[str]:
    return {m.group(1) for m in _PLACEHOLDER.finditer(template)}


@dataclass(frozen=True)
class Recipe:
    """One row of the recipe table (spec 04 §3.3)."""

    id: str
    component: str
    #: The component the long form is built around (``replacement``'s head).
    head: str
    #: One agent-facing line; ``{param}`` placeholders render plainly.
    text: str
    #: The rule (or replacing step) as a DSL step-list fragment template.
    rule: str
    #: Steps inserted directly before the ``TradeManager`` (``None`` = none).
    prelude: str | None
    #: ``mechanical`` when the recipe needs no question, else ``assisted``.
    mode: str
    #: The scripted question an assisted recipe asks (04-R15).
    question: str | None
    #: The rule (branch) name a LINEAR exit gets (uniqued; D-22: free text).
    rule_name: str | None
    #: Whether the deprecated version carries the Q-2448 known issue.
    known_issue: bool
    #: Every component name in ``rule``, ``prelude`` and ``head``.
    uses: tuple[str, ...]
    #: The components ``rule`` applies to TRADE values (bound to the
    #: position), which must be trade-safe at the strategy's pinned version
    #: (spec 03-R39/R41). Market-side steps in a rule (``Load``, ``ATR``,
    #: ``Scale`` on prices) are not listed. The final validation stays the
    #: authority: an op missing here ends in a loud UPGRADE_VERIFICATION_FAILED,
    #: never a silent pass (see :meth:`_Planner._rule_pins`).
    trade_ops: tuple[str, ...] = ()

    def shape(self) -> ReplacementShape:
        """The registry's structured replacement for this recipe (04-R3)."""
        return ReplacementShape(
            head=self.head,
            text=self.text,
            recipe=self.id,
            rule=self.rule,
            prelude=self.prelude,
            uses=self.uses,
        )


_HEAD = "TradeManager"

#: The recipe table (spec 04 §3.3, as amended by D-39.1 ``Exposure()``,
#: D-39.8 ``Scale(by=)`` and D-45's ATR trailing stop). Placeholders are
#: the deprecated instance's parameters (class defaults when unset) plus the
#: derived ``g`` (the group stem), ``prices``, ``stop_dist`` and friends.
RECIPES: dict[str, Recipe] = {
    r.id: r
    for r in (
        Recipe(
            id="q2448.stop",
            component="MaxDrawdownStopLoss",
            head=_HEAD,
            text="A TradeManager rule: TradeReturn() ≤ −{drawdown_threshold} (inclusive) → Exit()",
            rule=(
                "[TradeReturn(), BelowThresholdFilter(threshold={drawdown_threshold:neg}, "
                "inclusive=True), Exit()]"
            ),
            prelude=None,
            mode="mechanical",
            question=None,
            rule_name="stop",
            known_issue=True,
            uses=("TradeManager", "TradeReturn", "BelowThresholdFilter", "Exit"),
            trade_ops=("BelowThresholdFilter",),
        ),
        Recipe(
            id="q2448.target",
            component="TakeProfitExit",
            head=_HEAD,
            text="A TradeManager rule: TradeReturn() ≥ {profit_threshold} (inclusive) → Exit()",
            rule=(
                "[TradeReturn(), AboveThresholdFilter(threshold={profit_threshold:float}, "
                "inclusive=True), Exit()]"
            ),
            prelude=None,
            mode="mechanical",
            question=None,
            rule_name="target",
            known_issue=True,
            uses=("TradeManager", "TradeReturn", "AboveThresholdFilter", "Exit"),
            trade_ops=("AboveThresholdFilter",),
        ),
        Recipe(
            id="q2448.trail",
            component="TrailingStopExit",
            head=_HEAD,
            text=(
                "A TradeManager rule: GiveBack() − {atr_multiplier}×ATR({atr_period}) > 0 → "
                "Exit() (TrailingStop(atr={atr_multiplier}, period={atr_period}))"
            ),
            rule=(
                '[{"fast": [GiveBack()], "slow": [Load({ohlcv_slot:str}), '
                "ATR(period={atr_period:int}), Scale(by={atr_multiplier:float})]}, "
                "Crossover(), AboveThresholdFilter(threshold=0.0), Exit()]"
            ),
            prelude=None,
            mode="mechanical",
            question=None,
            rule_name="trail",
            known_issue=True,
            uses=(
                "TradeManager",
                "GiveBack",
                "Load",
                "ATR",
                "Scale",
                "Crossover",
                "AboveThresholdFilter",
                "Exit",
            ),
            trade_ops=("Crossover", "AboveThresholdFilter"),
        ),
        Recipe(
            id="q2448.time",
            component="TimeBasedExitFilter",
            head=_HEAD,
            text="A TradeManager rule: BarsHeld() ≥ {hold_periods} → Exit()",
            rule=(
                "[BarsHeld(), AboveThresholdFilter(threshold={hold_periods:float}, "
                "inclusive=True), Exit()]"
            ),
            prelude=None,
            mode="mechanical",
            question=None,
            rule_name="max_hold",
            known_issue=True,
            uses=("TradeManager", "BarsHeld", "AboveThresholdFilter", "Exit"),
            trade_ops=("AboveThresholdFilter",),
        ),
        Recipe(
            id="q2448.value_stop",
            component="ValueStopExitFilter",
            head=_HEAD,
            text="A TradeManager rule on DrawdownFromPeak(); the upgrade asks one question",
            rule=(
                "[DrawdownFromPeak(), BelowThresholdFilter(threshold={retracement_pct:neg}), "
                "Exit()]"
            ),
            prelude=None,
            mode="assisted",
            question="Q-VSEF",
            rule_name="value_stop",
            known_issue=True,
            uses=("TradeManager", "DrawdownFromPeak", "BelowThresholdFilter", "Exit"),
            trade_ops=("BelowThresholdFilter",),
        ),
        Recipe(
            id="q2448.manager",
            component="PositionStateMachine",
            head=_HEAD,
            text="TradeManager(entries=…, prices=…) with each exit as its own rule ending in Exit()",
            rule="[TradeManager(entries={entry_slot:str}, prices={prices:str}), Exposure()]",
            prelude=None,
            mode="mechanical",
            question=None,
            rule_name=None,
            known_issue=False,
            uses=("TradeManager", "Exposure"),
        ),
        Recipe(
            id="q2448.tlre",
            component="TradeLevelRiskExit",
            head=_HEAD,
            text="An R-multiple TradeManager: stop at −1R, target at +{reward_multiple}R",
            rule=(
                "[TradeManager(entries={entry_slot:str}, prices={ohlcv_slot:str}), "
                '{"numerator": [TradePnL()], "denominator": [AtEntry(slot={stop_dist:str})]}, '
                'SignalRatio(), {"stop": [BelowThresholdFilter(threshold=-1.0, inclusive=True), '
                'Exit()], "target": [AboveThresholdFilter(threshold={reward_multiple:float}, '
                "inclusive=True), Exit()]}, Exposure()]"
            ),
            prelude=(
                '[{"fast": [Load({ohlcv_slot:str}), ExtractSeries(series_name="close")], '
                '"slow": [Load({level_slot:str})]}, Crossover(), Abs(), Store({stop_dist:str})]'
            ),
            mode="assisted",
            question="Q-TLRE-TEMPLATE",
            rule_name=None,
            known_issue=False,
            uses=(
                "TradeManager",
                "TradePnL",
                "AtEntry",
                "SignalRatio",
                "BelowThresholdFilter",
                "AboveThresholdFilter",
                "Exit",
                "Exposure",
                "Load",
                "ExtractSeries",
                "Crossover",
                "Abs",
                "Store",
            ),
            trade_ops=("SignalRatio", "BelowThresholdFilter", "AboveThresholdFilter"),
        ),
        Recipe(
            id="q2448.scaling",
            component="ScalingPositionManager",
            head=_HEAD,
            text="A TradeManager with ScaleIn(units, times); the upgrade asks one question",
            rule=(
                "[TradeManager(entries={first_entry_slot:str}, prices={prices:str}), "
                '{"add": [Load({first_entry_slot:str}), ScaleIn(units=1.0, times={adds:int})]}, '
                "Exposure()]"
            ),
            prelude=None,
            mode="assisted",
            question="Q-SPM-ADDS",
            rule_name=None,
            known_issue=False,
            uses=("TradeManager", "Load", "ScaleIn", "Exposure"),
        ),
        Recipe(
            id="q2448.risk_sizer",
            component="StopDistanceRiskSizer",
            head="RiskSizer",
            text=(
                "RiskSizer(risk={risk_fraction}, distance=<stop distance at entry>, "
                "max_weight={max_weight}) on the TradeManager's position"
            ),
            rule=(
                "[RiskSizer(risk={risk_fraction:float}, distance={stop_dist:str}, "
                "max_weight={max_weight:float})]"
            ),
            prelude=(
                "[Load({ohlcv_slot:str}), ATR(period={atr_period:int}), "
                "Scale(by={atr_mult:float}), Store({stop_dist:str})]"
            ),
            mode="mechanical",
            question=None,
            rule_name=None,
            known_issue=True,
            uses=("RiskSizer", "Load", "ATR", "Scale", "Store"),
        ),
    )
}

#: ``StopDistanceRiskSizer``'s ``level``-mode distance prelude (|close − L|,
#: in price units — the unit ``RiskSizer.distance`` takes, spec 01-R28).
_LEVEL_DISTANCE_PRELUDE = (
    '[{"fast": [Load({ohlcv_slot:str}), ExtractSeries(series_name="close")], '
    '"slow": [Load({stop_level_slot:str})]}, Crossover(), Abs(), Store({stop_dist:str})]'
)

_BY_COMPONENT: dict[str, Recipe] = {r.component: r for r in RECIPES.values()}

#: The deprecated set (04-R1), by role.
MECHANICAL_EXITS = frozenset(
    {"MaxDrawdownStopLoss", "TakeProfitExit", "TrailingStopExit", "TimeBasedExitFilter"}
)
EXITS = MECHANICAL_EXITS | {"ValueStopExitFilter"}
MANAGERS = frozenset({"PositionStateMachine", "TradeLevelRiskExit", "ScalingPositionManager"})
SIZERS = frozenset({"StopDistanceRiskSizer"})
DEPRECATED = EXITS | MANAGERS | SIZERS
#: The trade ops of a directional market rule (``_directional_wrap``):
#: market value x ``TradeDirection()``, then the sign test.
_DIRECTIONAL_TRADE_OPS = ("SignalProduct", "BelowThresholdFilter")


def recipe_shape(recipe_id: str) -> ReplacementShape:
    """The ``replacement`` a deprecated class declares (04-R3)."""
    return RECIPES[recipe_id].shape()


def recipe_for(component: str) -> Recipe | None:
    return _BY_COMPONENT.get(component)


# ═══════════════════════════════════════════════════════════════════════════
# scripted questions (04-R15)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Choice:
    id: str
    text: str
    #: ``"int"`` / ``"float"`` when the choice takes a value (``within=24``).
    value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "value": self.value}


@dataclass(frozen=True)
class QuestionTemplate:
    id: str
    shape: str
    text: str
    choices: tuple[Choice, ...]
    #: Retired by a ruling: kept so its id is never reused, never asked.
    retired: str | None = None


_KEEP = Choice("keep", "Keep this part on the deprecated components; it runs exactly as today.")

#: The 11 scripted question ids (two retired by rulings, never asked).
#: Every asked question offers ``keep`` (D-02).
QUESTIONS: dict[str, QuestionTemplate] = {
    q.id: q
    for q in (
        QuestionTemplate(
            "Q-U1-INTENT",
            "an exit feeding an entry (census U1)",
            "{component} at {path} feeds the entry slot '{slot}'. A stop or target cannot "
            "open a trade. What should it do?",
            (
                Choice(
                    "anchored",
                    "Measure the move from the entry signal's onset with AnchoredReturn "
                    "(within N bars), in the signal layer.",
                    "int",
                ),
                Choice("scale_in", "Add to the open trade when the move happens (ScaleIn)."),
                Choice("drop", "Remove it from the entry."),
                _KEEP,
            ),
        ),
        QuestionTemplate(
            "Q-U2-LADDER",
            "an exit feeding ScalingPositionManager.exit_slots (census U2)",
            "{component} closes lots of the scaled position '{manager}'. When it fires, close…",
            (
                Choice("all", "…the whole position (Exit())."),
                Choice("fraction", "…this fraction of the initial size (Reduce).", "float"),
                _KEEP,
            ),
        ),
        QuestionTemplate(
            "Q-SPM-ADDS",
            "any ScalingPositionManager",
            "'{manager}' adds up to {max_entries} entries from '{slots}'. Add one unit each time "
            "the entry condition becomes true (once per rising edge)?",
            (Choice("rising_edge", "Yes: ScaleIn once per rising edge (D-17)."), _KEEP),
        ),
        QuestionTemplate(
            "Q-TLRE-TEMPLATE",
            "any TradeLevelRiskExit (census U3)",
            "'{manager}' risks to the level in '{level_slot}' with a {reward_multiple}R target. "
            "Rewrite it as an R-multiple trade: stop at −1R, target at +{reward_multiple}R, each "
            "external exit as its own rule?",
            (Choice("accept", "Yes, rewrite it as an R-multiple TradeManager."), _KEEP),
        ),
        QuestionTemplate(
            "Q-TLRE-ENTRYBLOCK",
            "TradeLevelRiskExit with external_exit_slot",
            "TradeLevelRiskExit refused a new entry on a bar where '{external}' fired. Keep that?",
            (
                Choice("yes", "Yes: an AllowEntry gate refuses entry on those bars."),
                Choice("no", "No: entries ignore it."),
            ),
            retired="X-32: SR-28 (D-39.4) already refuses entry on a market exit's bar",
        ),
        QuestionTemplate(
            "Q-U4-SHARED",
            "one exit read by two managers (census U4)",
            "{component} is used by '{a}' and '{b}'. Copy the rule into each new TradeManager?",
            (Choice("copy", "Yes, copy the rule into each."), _KEEP),
        ),
        QuestionTemplate(
            "Q-VSEF",
            "ValueStopExitFilter",
            "ValueStopExitFilter exits after a {lookback}-bar reversal more than {retracement_pct} "
            "off the recent {window}-bar extreme. The new form measures the retracement from the "
            "trade's best close instead. Use DrawdownFromPeak() < −{retracement_pct}?",
            (Choice("trade_peak", "Yes, measure from the trade's best close."), _KEEP),
        ),
        QuestionTemplate(
            "Q-PRICES",
            "no or ambiguous price slot",
            "Which stored price data does this position trade on?",
            (_KEEP,),  # one choice per top-level loader Store slot is prepended per group
        ),
        QuestionTemplate(
            "Q-CONSUMER",
            "a manager output feeding a non-consumer",
            "'{manager}' feeds '{next}', which does not accept a position. Keep this book on "
            "the deprecated manager?",
            (_KEEP,),
            retired="X-04 / D-49: Exposure() realises any book mechanically",
        ),
        QuestionTemplate(
            "Q-FACTORY",
            "a deprecated step inside a factory or variable body",
            "{component} is inside the reusable block '{name}', used {n} times. Upgrade the "
            "shared block, which changes every use?",
            (_KEEP,),
        ),
        QuestionTemplate(
            "Q-SDRS-NOPOS",
            "StopDistanceRiskSizer with no position upstream",
            "StopDistanceRiskSizer sized trades from the sign of '{input}'. Open a trade when "
            "that sign turns ±1 (ThresholdCross(upper=0, lower=0) into a TradeManager)?",
            (_KEEP,),
        ),
    )
}


@dataclass(frozen=True)
class Question:
    """A rendered question of one group. ``key`` is unique within a plan."""

    id: str
    key: str
    text: str
    choices: tuple[Choice, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "text": self.text,
            "choices": [c.to_dict() for c in self.choices],
        }


def _question(
    qid: str, key: str, values: Mapping[str, Any], extra: Sequence[Choice] = ()
) -> Question:
    tpl = QUESTIONS[qid]
    text = _PLACEHOLDER.sub(lambda m: _plain(values[m.group(1)]), tpl.text)
    return Question(id=qid, key=key, text=text, choices=tuple(extra) + tpl.choices)


@dataclass(frozen=True)
class Answer:
    choice: str
    value: float | None = None


def parse_answers(items: Sequence[str] | Mapping[str, str] | None) -> dict[str, str]:
    """Normalise answers: ``["Q-VSEF=trade_peak", "Q-U1-INTENT@step[3]=anchored:24"]``.

    The CLI's repeatable ``--answers`` and the MCP ``answers`` array use this
    form (04-R21). A mapping passes through.
    """
    if items is None:
        return {}
    if isinstance(items, Mapping):
        return {str(k): str(v) for k, v in items.items()}
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise UpgradeAnswerError(
                code="UPGRADE_ANSWER_INVALID",
                remediation="Write each answer as <question_id>=<choice>[:<value>].",
                detail=f"malformed answer {item!r}",
                question=item,
            )
        key, _, choice = item.partition("=")
        out[key.strip()] = choice.strip()
    return out


def _answer_for(question: Question, answers: Mapping[str, str]) -> Answer:
    raw = answers.get(question.key, answers.get(question.id))
    if raw is None:
        raise UpgradeAnswerError(
            code="UPGRADE_ANSWER_MISSING",
            remediation=(
                f"Answer question {question.key} ({question.text}) with one of: "
                + ", ".join(c.id for c in question.choices)
            ),
            detail=f"no answer for {question.key}",
            question=question.key,
        )
    choice_id, _, value_text = raw.partition(":")
    choice = next((c for c in question.choices if c.id == choice_id.strip()), None)
    if choice is None:
        raise UpgradeAnswerError(
            code="UPGRADE_ANSWER_INVALID",
            remediation=(
                f"Question {question.key} accepts: " + ", ".join(c.id for c in question.choices)
            ),
            detail=f"{raw!r} is not a choice of {question.key}",
            question=question.key,
        )
    if choice.value is None:
        if value_text.strip():
            raise UpgradeAnswerError(
                code="UPGRADE_ANSWER_INVALID",
                remediation=f"Choice {choice.id!r} of {question.key} takes no value.",
                detail=f"unexpected value in {raw!r}",
                question=question.key,
            )
        return Answer(choice.id)
    try:
        value = float(value_text) if choice.value == "float" else float(int(value_text))
    except ValueError:
        raise UpgradeAnswerError(
            code="UPGRADE_ANSWER_INVALID",
            remediation=f"Choice {choice.id!r} of {question.key} needs a {choice.value} value: "
            f"{choice.id}:<value>.",
            detail=f"malformed value in {raw!r}",
            question=question.key,
        ) from None
    return Answer(choice.id, value)


# ═══════════════════════════════════════════════════════════════════════════
# the walk: every step occurrence, its path, and the slot data flow
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class _Occ:
    """One step occurrence at a definition site (edits.py path grammar)."""

    path: str
    node: Any
    container: str  # edits.py container key ("" = the top-level pipeline)
    index: int
    #: "pipeline" or the factory/var name the occurrence is defined in.
    scope: str
    params: dict[str, Any] = field(default_factory=dict)  # effective (bound) params


def _container_and_index(path: str) -> tuple[str, int]:
    cut = path.rfind(".step[")
    if cut >= 0:
        return path[:cut], int(path[cut + 6 : -1])
    return "", int(path[5:-1])


class _Walk:
    """Abstract slot data flow over the strategy as written.

    Values are sets of ORIGINS — paths of deprecated instances whose pulse
    the value carries. Last writer wins for a slot, in execution order.
    Deprecated exits, managers and sizers IGNORE their input (they read
    slots) and originate their own path; data loaders originate nothing.
    """

    def __init__(self, strategy: StrategyFile, lock: Mapping[str, int]):
        self.strategy = strategy
        self.lock = dict(lock or {})
        self.factories = {f.name: f for f in strategy.factories}
        self.variables = {v.name: v for v in strategy.variables}
        self.occ: dict[str, _Occ] = {}
        self.slots: dict[str, frozenset[str]] = {}
        self.writers: dict[str, list[_Occ]] = {}
        #: slot -> [(reader path, param name or "load")]
        self.readers: dict[str, list[tuple[str, str]]] = {}
        #: (consumer path, param, slot, origins of the slot when read)
        self.consumers: list[tuple[str, str, str, frozenset[str]]] = []
        self.inputs: dict[str, frozenset[str]] = {}
        self.order: list[str] = []
        self.var_uses: dict[str, int] = {}
        self.factory_uses: dict[str, int] = {}
        #: The variable/factory bodies being interpreted, innermost last.
        self._active: list[tuple[str, str]] = []
        #: A definition that reaches itself (``"factory sig → sig"``), else
        #: ``None``. The walk does not re-enter it, so it always terminates;
        #: the planner then refuses every group by name (the validator
        #: reports FACTORY_CALL_CYCLE / PIPELINE_VARIABLE_CYCLE).
        self.cycle: str | None = None
        self._enumerate_static()
        self._chain(strategy.pipeline.steps, "", frozenset(), "pipeline", {})

    # ── static enumeration: every occurrence, interpreted or not ──
    def _enumerate_static(self) -> None:
        def visit(steps: list, container: str, scope: str) -> None:
            for i, step in enumerate(steps):
                path = f"{container}.step[{i}]" if container else f"step[{i}]"
                if path not in self.occ:
                    params = dict(getattr(step, "params", None) or getattr(step, "args", {}) or {})
                    self.occ[path] = _Occ(path, step, container, i, scope, params)
                if isinstance(step, ParallelSpec):
                    for name, branch in step.branches.items():
                        visit(branch, f"{path}.branch[{name}]", scope)
                elif isinstance(step, PipelineSpec):
                    visit(step.steps, path, scope)

        visit(self.strategy.pipeline.steps, "", "pipeline")
        for f in self.strategy.factories:
            visit(f.body.steps, f"factory[{f.name}]", f"factory:{f.name}")
        for v in self.strategy.variables:
            if isinstance(v.value, PipelineSpec):
                visit(v.value.steps, f"var[{v.name}]", f"var:{v.name}")

    def _sig(self, name: str):
        from pipeline_engine.base.registry_types import get_latest, get_version

        pinned = self.lock.get(name)
        return get_version(name, pinned) if pinned is not None else get_latest(name)

    def _bound(self, value: Any, binding: Mapping[str, Any]) -> Any:
        if isinstance(value, VariableRef):
            if value.name in binding:
                return binding[value.name]
            var = self.variables.get(value.name)
            if var is not None and not isinstance(var.value, PipelineSpec):
                return var.value
        return value

    def _enter(self, kind: str, name: str) -> bool:
        """Push a body onto the active stack; ``False`` (and the cycle
        recorded) when it is already being interpreted."""
        if (kind, name) in self._active:
            loop = self._active[self._active.index((kind, name)) :]
            if self.cycle is None:
                self.cycle = f"{kind} " + " → ".join([n for _k, n in loop] + [name])
            return False
        self._active.append((kind, name))
        return True

    def _chain(
        self, steps: list, container: str, cur: frozenset[str], scope: str, binding: Mapping
    ) -> frozenset[str]:
        for i, step in enumerate(steps):
            path = f"{container}.step[{i}]" if container else f"step[{i}]"
            cur = self._step(step, path, cur, scope, binding)
        return cur

    def _step(self, step: Any, path: str, cur: frozenset[str], scope: str, binding) -> frozenset:
        occ = self.occ[path]
        if path not in self.order:
            self.order.append(path)
        if isinstance(step, SlotStoreSpec):
            self.slots[step.slot_name] = cur
            self.writers.setdefault(step.slot_name, []).append(occ)
            return cur
        if isinstance(step, SlotStoreValueSpec):
            self.slots[step.slot_name] = frozenset()
            self.writers.setdefault(step.slot_name, []).append(occ)
            return cur
        if isinstance(step, SlotLoadSpec):
            self.readers.setdefault(step.slot_name, []).append((path, "load"))
            return self.slots.get(step.slot_name, frozenset())
        if isinstance(step, SlotExtractSpec):
            return cur
        if isinstance(step, ParallelSpec):
            out: frozenset[str] = frozenset()
            for name, branch in step.branches.items():
                out |= self._chain(branch, f"{path}.branch[{name}]", cur, scope, binding)
            return out
        if isinstance(step, PipelineSpec):
            return self._chain(step.steps, path, cur, scope, binding)
        if isinstance(step, VariableRef):
            var = self.variables.get(step.name)
            self.var_uses[step.name] = self.var_uses.get(step.name, 0) + 1
            if var is not None and isinstance(var.value, PipelineSpec):
                if not self._enter("variable", step.name):
                    return cur
                try:
                    return self._chain(var.value.steps, f"var[{step.name}]", cur, scope, binding)
                finally:
                    self._active.pop()
            return cur
        if isinstance(step, FactoryCallSpec):
            fac = self.factories.get(step.name)
            self.factory_uses[step.name] = self.factory_uses.get(step.name, 0) + 1
            if fac is None:
                return cur
            inner = {p.name: p.default for p in fac.params}
            inner.update({k: self._bound(v, binding) for k, v in step.args.items()})
            if not self._enter("factory", step.name):
                return cur
            try:
                return self._chain(fac.body.steps, f"factory[{step.name}]", cur, scope, inner)
            finally:
                self._active.pop()
        if isinstance(step, ComponentRef):
            self.inputs[path] = cur
            params = {k: self._bound(v, binding) for k, v in step.params.items()}
            occ.params = params
            sig = self._sig(step.name)
            if sig is not None:
                for pname, pinfo in sig.parameters.items():
                    if not pinfo.slot_reference:
                        continue
                    value = params.get(pname, pinfo.default)
                    names = value if isinstance(value, list) else [value]
                    for slot in names:
                        if isinstance(slot, str):
                            self.readers.setdefault(slot, []).append((path, pname))
                            self.consumers.append(
                                (path, pname, slot, self.slots.get(slot, frozenset()))
                            )
            if step.name in DEPRECATED:
                return frozenset({path})
            if sig is not None and sig.category.value == "data_loader":
                return frozenset()
            return cur
        return cur


# ═══════════════════════════════════════════════════════════════════════════
# the plan
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Replaced:
    """One deprecated instance an upgrade replaces (04-R16 ``replaces``)."""

    component: str
    version: int
    path: str
    rule: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "version": self.version,
            "path": self.path,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class LockChange:
    """A pin the upgrade moves so a rule's trade op is trade-safe (L3a-fix).

    The one exception to 04-R18's "every pin is kept": a recipe puts
    ``component`` inside a TradeManager rule, the strategy pins
    ``from_version`` (not certified to run on trade values, spec 03-R39), and
    ``to_version`` is certified. A lock pins a NAME, so the move also moves
    every other use of the component in the strategy; the planner makes it
    only with a proof that those uses compute the same values
    (:data:`_PIN_MOVE_PROOFS`), and ``reason`` names them.
    """

    component: str
    from_version: int
    to_version: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "from": self.from_version,
            "to": self.to_version,
            "reason": self.reason,
        }


@dataclass
class UpgradeGroup:
    """One position group and how it upgrades (04-R9/R10)."""

    mode: str  # "mechanical" | "assisted" | "manual"
    path: str  # the manager's path, else the lone instance's (the issue path)
    manager: str | None
    members: list[str]  # every deprecated instance path in the group
    replaces: list[Replaced]
    questions: list[Question] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    reason: str = ""
    entries: str | None = None
    exit_slot: str | None = None
    prices: str | None = None
    #: The mechanical/assisted rewrite, interpreted by ``render_upgrade``.
    recipe: dict[str, Any] = field(default_factory=dict)
    #: Pins this group's upgrade moves (applied only when the group is).
    lock_changes: list[LockChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "path": self.path,
            "manager": self.manager,
            "recipe": RECIPE_FAMILY,
            "replaces": [r.to_dict() for r in self.replaces],
            "questions": [q.to_dict() for q in self.questions],
            "known_issues": list(self.known_issues),
            "reason": self.reason,
            "lock_changes": [c.to_dict() for c in self.lock_changes],
        }


@dataclass
class UpgradePlan:
    groups: list[UpgradeGroup]
    #: Every deprecated instance path found in the strategy (coverage).
    instances: list[str]

    @property
    def mode(self) -> str:
        """The strategy's mode: its worst group (manual > assisted > mechanical)."""
        modes = {g.mode for g in self.groups}
        for m in ("manual", "assisted", "mechanical"):
            if m in modes:
                return m
        return "none"

    def coverage_ok(self) -> bool:
        """04-R9's invariant: every instance is in exactly one group."""
        seen: list[str] = [m for g in self.groups for m in g.members]
        return sorted(seen) == sorted(self.instances) and len(set(seen)) == len(seen)


def _version_of(name: str, lock: Mapping[str, int]) -> int:
    from pipeline_engine.base.registry_types import get_latest

    if name in lock:
        return int(lock[name])
    sig = get_latest(name)
    return sig.version if sig is not None else 1


def _param(occ: _Occ, name: str) -> Any:
    """The instance's value for ``name``, else the registered class default."""
    if name in occ.params:
        return occ.params[name]
    from pipeline_engine.base.registry_types import get_latest

    sig = get_latest(occ.node.name)
    if sig is not None and name in sig.parameters:
        default = sig.parameters[name].default
        from pipeline_engine.constants import MISSING

        return None if default is MISSING else default
    return None


def _branch_name_of(path: str) -> str | None:
    m = re.findall(r"\.branch\[([^\]]*)\]", path)
    return m[-1] if m else None


def _stem(slot: str) -> str:
    stem = re.sub(r"\W", "", slot)
    return stem or "position"


def _written_slots(walk: _Walk) -> set[str]:
    out = set(walk.writers)
    for occ in walk.occ.values():
        if isinstance(occ.node, (SlotStoreSpec, SlotStoreValueSpec)):
            out.add(occ.node.slot_name)
    return out


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    taken.add(f"{base}_{n}")
    return f"{base}_{n}"


def _is_data_loader(node: Any) -> bool:
    from pipeline_engine.base.registry_types import get_latest

    if not isinstance(node, ComponentRef):
        return False
    sig = get_latest(node.name)
    return sig is not None and sig.category.value == "data_loader"


def _mask_domain(node: Any) -> bool:
    """Is a step's registered output domain within {0, 1}? (scalar-filter omission)"""
    from pipeline_engine.base.registry_types import get_latest

    if not isinstance(node, ComponentRef):
        return False
    sig = get_latest(node.name)
    dom = getattr(sig, "output_domain", None) if sig is not None else None
    if isinstance(dom, dict) and "set" in dom:
        return set(dom["set"]) <= {0.0, 1.0}
    return False


def _deep_steps(step: Any, walk: _Walk, seen: set | None = None):
    """Every step reachable inside ``step`` (branches, nested pipelines,
    referenced variables and factories)."""
    seen = set() if seen is None else seen
    yield step
    if isinstance(step, ParallelSpec):
        for branch in step.branches.values():
            for s in branch:
                yield from _deep_steps(s, walk, seen)
    elif isinstance(step, PipelineSpec):
        for s in step.steps:
            yield from _deep_steps(s, walk, seen)
    elif isinstance(step, VariableRef) and step.name not in seen:
        seen.add(step.name)
        var = walk.variables.get(step.name)
        if var is not None and isinstance(var.value, PipelineSpec):
            for s in var.value.steps:
                yield from _deep_steps(s, walk, seen)
    elif isinstance(step, FactoryCallSpec) and step.name not in seen:
        seen.add(step.name)
        fac = walk.factories.get(step.name)
        if fac is not None:
            for s in fac.body.steps:
                yield from _deep_steps(s, walk, seen)


def _writes_slot(steps: list, walk: _Walk) -> bool:
    return any(
        isinstance(s, (SlotStoreSpec, SlotStoreValueSpec))
        for step in steps
        for s in _deep_steps(step, walk)
    )


def _has_deprecated(steps: list, walk: _Walk) -> bool:
    return any(
        isinstance(s, ComponentRef) and s.name in DEPRECATED
        for step in steps
        for s in _deep_steps(step, walk)
    )


def _reads_input(branch: list) -> bool:
    """A market branch reads the Parallel's input unless its first step is a
    ``Load``, a data loader, or a deprecated exit (which ignore ``current``)."""
    if not branch:
        return True
    first = branch[0]
    if isinstance(first, SlotLoadSpec) or _is_data_loader(first):
        return False
    return not (isinstance(first, ComponentRef) and first.name in EXITS)


def _top_level_prices(walk: _Walk) -> tuple[str | None, list[str], int | None]:
    """The price slot a group with no ``ohlcv_slot`` trades on (04-R11.3).

    Returns ``(slot, candidates, insert_index)``: the slot of the single
    top-level ``Store`` that follows a ``PriceDataLoader`` (through any
    OHLCV-preserving resamplers); or, when that chain stores nothing, the
    index where a ``Store("ohlcv")`` is inserted (``slot`` is ``None``).
    Two or more loaders → ambiguous (``candidates`` lists their stores).
    """
    from pipeline_engine.base.registry_types import get_latest
    from pipeline_engine.validation_shared import type_name

    steps = walk.strategy.pipeline.steps
    chains: list[tuple[str | None, int]] = []
    for i, step in enumerate(steps):
        if not (isinstance(step, ComponentRef) and step.name == "PriceDataLoader"):
            continue
        j = i + 1
        while j < len(steps):
            nxt = steps[j]
            if isinstance(nxt, SlotStoreSpec):
                chains.append((nxt.slot_name, j))
                break
            sig = get_latest(nxt.name) if isinstance(nxt, ComponentRef) else None
            if sig is not None and type_name(sig.output_type) == "OHLCVDict":
                j += 1
                continue
            chains.append((None, j))
            break
        else:
            chains.append((None, j))
    if len(chains) == 1:
        slot, at = chains[0]
        return slot, ([slot] if slot else []), (None if slot else at)
    return None, [s for s, _ in chains if s], None


@dataclass
class _Wiring:
    """How an exit slot is written (04-R10 condition 2), for one manager.

    ``form``: ``parallel`` (exits Parallel → MaskOr/OrCombinator →
    Store(X)), ``linear`` (one exit → Store(X)) or ``market`` (X is a
    market signal; it stays, and one rule reads it).
    """

    slot: str
    form: str
    writer: str
    parallel: str | None = None
    linear: str | None = None
    branches: dict[str, dict[str, Any]] = field(default_factory=dict)
    exits: list[str] = field(default_factory=list)
    vsef: list[str] = field(default_factory=list)
    mask: bool = False  # market form: X's own static domain is within {0, 1}


# ── moving a rule op's pin: proofs that the strategy's other uses keep their values ──


def _describe_step(step: Any) -> str:
    if isinstance(step, SlotLoadSpec):
        return f"Load('{step.slot_name}')"
    if isinstance(step, (ComponentRef, FactoryCallSpec, VariableRef)):
        return f"'{step.name}'"
    return type(step).__name__


def _keeps_input_shape(steps: list, walk: _Walk) -> str | None:
    """``None`` when ``steps``, run on a frame, return a frame with the SAME
    index and columns; else why that is not provable.

    Conservative by construction (a refusal costs a manual upgrade, a wrong
    yes would change a strategy): every step must read the input it is given
    (no ``Load``, data loader, factory or variable, whose bodies may read
    anything), keep its clock (no declared ``clock_transfer``), keep its
    columns (no ``population_scope``, no universe filter) and read no slot.
    A nested Parallel is allowed only when a composer combines it in place.
    """
    for i, step in enumerate(steps):
        if isinstance(step, SlotStoreSpec):
            continue  # Store passes its input through
        if isinstance(step, PipelineSpec):
            why = _keeps_input_shape(step.steps, walk)
            if why:
                return why
            continue
        if isinstance(step, ParallelSpec):
            nxt = steps[i + 1] if i + 1 < len(steps) else None
            sig = walk._sig(nxt.name) if isinstance(nxt, ComponentRef) else None
            if sig is None or sig.category.value != "signal_composer":
                return "holds a Parallel that no composer combines in place"
            for name, branch in step.branches.items():
                why = _keeps_input_shape(branch, walk)
                if why:
                    return f"(inner branch '{name}') {why}"
            continue
        if not isinstance(step, ComponentRef):
            return f"reads {_describe_step(step)}, not the Parallel's input"
        sig = walk._sig(step.name)
        if sig is None:
            return f"uses '{step.name}', which is not registered"
        category = sig.category.value
        if category in ("data_loader", "universe_filter"):
            return f"uses '{step.name}' ({category}), which replaces its input"
        if sig.population_scope is not None:
            return f"uses '{step.name}', which reads across assets"
        if sig.clock_transfer is not None:
            return f"uses '{step.name}', which changes the clock"
        reads_slot = bool(sig.slot_reads) or any(
            pinfo.slot_reference and step.params.get(pname) is not None
            for pname, pinfo in sig.parameters.items()
        )
        if reads_slot:
            return f"uses '{step.name}', which reads a slot"
    return None


def _crossover_v2_keeps(walk: _Walk, occ: _Occ) -> str | None:
    """Crossover v1 -> v2 keeps this use's values, or why that is unproven.

    v2 (``combiners_v2.py``) is v1's ``run()`` behind a same-shape guard:
    identical index AND columns on the two inputs give byte-identical output
    (one computation owner); a mismatch RAISES in v2 where v1 NaN-aligned it.
    So the move is a no-op exactly when both inputs provably share index and
    columns: both are branches of the Parallel directly before the Crossover,
    and each keeps the shape of that Parallel's one input.
    """
    steps = _container_steps(walk.strategy, occ.container)
    prev = steps[occ.index - 1] if occ.index >= 1 else None
    if not isinstance(prev, ParallelSpec):
        return "its inputs do not come from a Parallel directly before it"
    for key in (_param(occ, "fast_key") or "fast", _param(occ, "slow_key") or "slow"):
        if key not in prev.branches:
            return f"its input '{key}' is not a branch of the Parallel before it"
        why = _keeps_input_shape(prev.branches[key], walk)
        if why:
            return f"branch '{key}' {why}"
    return None


#: The pin moves the planner may make, each with its proof (L3a-fix):
#: ``(component, from, to) -> proof(walk, occurrence)``, ``None`` = proven.
#: A move with no entry here is refused whenever the strategy uses the
#: component outside the upgrade's rules.
_PIN_MOVE_PROOFS = {("Crossover", 1, 2): _crossover_v2_keeps}

#: What the proof establishes, in the reason an agent reads.
_PIN_MOVE_GROUNDS = {
    ("Crossover", 1, 2): (
        "v2 is v1's arithmetic behind a same-shape guard, and each of those uses combines two "
        "branches of one input, so it computes the same values"
    ),
}


class _Planner:
    def __init__(self, strategy: StrategyFile, lock: Mapping[str, int]):
        self.walk = _Walk(strategy, lock)
        self.lock = dict(lock or {})
        self.taken = _written_slots(self.walk)

    # ── assignment of instances to groups ──
    def _manager_roles(self, mpath: str) -> dict[str, str]:
        name = self.walk.occ[mpath].node.name
        if name == "PositionStateMachine":
            return {"entry_slot": "entry", "exit_slot": "exit"}
        if name == "TradeLevelRiskExit":
            return {
                "entry_slot": "entry",
                "external_exit_slot": "exit",
                "level_slot": "level",
                "short_level_slot": "level",
            }
        return {"entry_slots": "entry", "exit_slots": "exit"}

    def plan(self) -> UpgradePlan:
        w = self.walk
        instances = [
            p
            for p, o in sorted(w.occ.items(), key=lambda kv: _path_key(kv[0]))
            if isinstance(o.node, ComponentRef) and o.node.name in DEPRECATED
        ]
        if w.cycle is not None:
            # The slot flow through a cyclic definition is undefined: no group
            # can be classified, so every deprecated instance is refused by
            # name (coverage holds: one manual group each).
            why = f"the strategy's {w.cycle} calls itself, so its data flow cannot be followed"
            groups = [
                self._manual(
                    p, w.occ[p].node.name if w.occ[p].node.name in MANAGERS else None, [p], why
                )
                for p in instances
            ]
            return UpgradePlan(groups=groups, instances=instances)
        managers = [p for p in instances if w.occ[p].node.name in MANAGERS]
        exit_targets: dict[str, list[tuple[str, str]]] = {}
        for cpath, pname, _slot, origins in w.consumers:
            if cpath not in w.occ or w.occ[cpath].node.name not in MANAGERS:
                continue
            role = self._manager_roles(cpath).get(pname)
            for origin in origins:
                if origin in w.occ and w.occ[origin].node.name in EXITS and role:
                    exit_targets.setdefault(origin, []).append((cpath, role))

        members: dict[str, list[str]] = {m: [m] for m in managers}
        shared: dict[str, list[str]] = {}
        lone: list[tuple[str, str]] = []  # (instance, why)
        for p in instances:
            name = w.occ[p].node.name
            if name in EXITS:
                targets = exit_targets.get(p, [])
                entry_roles = [t for t in targets if t[1] == "entry"]
                exit_mgrs = list(dict.fromkeys(m for m, r in targets if r == "exit"))
                if entry_roles:
                    lone.append((p, "u1"))
                elif exit_mgrs:
                    first = min(exit_mgrs, key=_path_key)
                    members[first].append(p)
                    if len(exit_mgrs) > 1:
                        shared.setdefault(first, []).append(p)
                else:
                    lone.append((p, "unowned"))
            elif name in SIZERS:
                ups = [o for o in w.inputs.get(p, frozenset()) if o in members]
                if ups:
                    members[min(ups, key=_path_key)].append(p)
                else:
                    lone.append((p, "sdrs"))

        groups: list[UpgradeGroup] = []
        for m in managers:
            groups.append(self._manager_group(m, members[m], shared.get(m, [])))
        for p, why in lone:
            groups.append(self._lone_group(p, why))
        groups.sort(key=lambda g: _path_key(g.path))
        return UpgradePlan(groups=groups, instances=instances)

    def _replaced(self, paths: list[str], rules: Mapping[str, str | None] | None = None):
        out = []
        for p in sorted(paths, key=_path_key):
            name = self.walk.occ[p].node.name
            out.append(
                Replaced(
                    component=name,
                    version=_version_of(name, self.lock),
                    path=p,
                    rule=(rules or {}).get(p, _branch_name_of(p)),
                )
            )
        return out

    def _known(self, paths: list[str]) -> list[str]:
        if any(recipe_for(self.walk.occ[p].node.name).known_issue for p in paths):
            return [Q2448]
        return []

    def _group(self, mode: str, path: str, manager: str | None, paths: list[str], **kw):
        return UpgradeGroup(
            mode=mode,
            path=path,
            manager=manager,
            members=list(paths),
            replaces=kw.pop("replaces", None) or self._replaced(paths),
            known_issues=self._known(paths),
            **kw,
        )

    def _manual(self, path: str, manager: str | None, paths: list[str], reason: str):
        return self._group(
            "manual",
            path,
            manager,
            paths,
            reason=f"{reason} — see the {MANUAL_HELP_TOPIC} help topic",
        )

    def _keep_only(self, path, manager, paths, question: Question, reason: str):
        # Only `keep` is offered until this shape's rewrite is implemented: a
        # choice the renderer cannot apply must never be offered (it would be
        # accepted and silently do nothing).
        question = Question(question.id, question.key, question.text, (_KEEP,))
        return self._group(
            "assisted",
            path,
            manager,
            paths,
            questions=[question],
            reason=reason,
            recipe={"kind": "keep_only"},
        )

    def _scoped(self, paths: list[str]) -> str | None:
        for p in paths:
            scope = self.walk.occ[p].scope
            if scope != "pipeline":
                return scope
        return None

    def _factory_group(self, path: str, manager: str | None, paths: list[str], scope: str):
        kind, _, name = scope.partition(":")
        uses = (self.walk.factory_uses if kind == "factory" else self.walk.var_uses).get(name, 0)
        comp = self.walk.occ[paths[0]].node.name
        q = _question(
            "Q-FACTORY", f"Q-FACTORY@{path}", {"component": comp, "name": name, "n": uses}
        )
        return self._keep_only(path, manager, paths, q, f"inside the reusable block '{name}'")

    def _lone_group(self, path: str, why: str) -> UpgradeGroup:
        occ = self.walk.occ[path]
        comp = occ.node.name
        scope = self._scoped([path])
        if scope is not None:
            return self._factory_group(path, None, [path], scope)
        if why == "u1":
            q = _question(
                "Q-U1-INTENT",
                f"Q-U1-INTENT@{path}",
                {"component": comp, "path": path, "slot": _param(occ, "entry_slot")},
            )
            return self._keep_only(path, None, [path], q, "an exit feeds an entry")
        if why == "sdrs":
            q = _question(
                "Q-SDRS-NOPOS", f"Q-SDRS-NOPOS@{path}", {"input": f"the step before {path}"}
            )
            return self._keep_only(
                path, None, [path], q, "StopDistanceRiskSizer reads a raw signal, not a position"
            )
        return self._manual(path, None, [path], f"{comp}'s pulse reaches no position manager")

    # ── manager groups ──
    def _manager_group(self, mpath: str, paths: list[str], shared: list[str]) -> UpgradeGroup:
        name = self.walk.occ[mpath].node.name
        scope = self._scoped(paths)
        if scope is not None:
            return self._factory_group(mpath, name, paths, scope)
        if shared:
            w = self.walk
            exit_path = shared[0]
            mgrs = sorted(
                {
                    c
                    for c, _p, _s, origins in w.consumers
                    if exit_path in origins and c in w.occ and w.occ[c].node.name in MANAGERS
                },
                key=_path_key,
            )
            q = _question(
                "Q-U4-SHARED",
                f"Q-U4-SHARED@{exit_path}",
                {
                    "component": w.occ[exit_path].node.name,
                    "a": mgrs[0] if mgrs else mpath,
                    "b": mgrs[1] if len(mgrs) > 1 else "?",
                },
            )
            return self._keep_only(
                mpath, name, paths, q, "one exit is read by two position managers"
            )
        if name == "PositionStateMachine":
            return self._psm_group(mpath, paths)
        if name == "TradeLevelRiskExit":
            return self._tlre_group(mpath, paths)
        q = _question(
            "Q-SPM-ADDS",
            f"Q-SPM-ADDS@{mpath}",
            {
                "manager": mpath,
                "max_entries": _param(self.walk.occ[mpath], "max_entries"),
                "slots": _param(self.walk.occ[mpath], "entry_slots"),
            },
        )
        return self._keep_only(mpath, name, paths, q, "ScalingPositionManager's adds need a ruling")

    def _wiring(self, mpath: str, param: str, exits: list[str]) -> _Wiring | str:
        """Analyse how ``mpath``'s ``param`` slot is written; a str is a manual reason."""
        w = self.walk
        x = _param(w.occ[mpath], param)
        writers = w.writers.get(x, [])
        if len(writers) != 1:
            return f"slot '{x}' is written {len(writers)} times, not once"
        wocc = writers[0]
        if not isinstance(wocc.node, SlotStoreSpec):
            return f"slot '{x}' is written by StoreValue, not by an exit"
        if wocc.scope != "pipeline":
            return f"slot '{x}' is written inside a reusable block"
        other = [r for r in w.readers.get(x, []) if r != (mpath, param)]
        if other:
            return f"slot '{x}' is also read by {other[0][0]}"
        container = _container_steps(w.strategy, wocc.container)
        iw = wocc.index
        prev = container[iw - 1] if iw >= 1 else None
        wiring = _Wiring(slot=x, form="market", writer=wocc.path)
        if isinstance(prev, ComponentRef) and prev.name in ("MaskOr", "OrCombinator") and iw >= 2:
            before = container[iw - 2]
            if isinstance(before, ParallelSpec) and _has_deprecated([before], w):
                selection = prev.params.get("signals_list") if prev.name == "OrCombinator" else None
                if selection is not None and set(selection) != set(before.branches):
                    return "OrCombinator selects only some of the exit branches"
                wiring.form = "parallel"
                wiring.parallel = _join(wocc.container, iw - 2)
        elif isinstance(prev, ComponentRef) and prev.name in EXITS:
            wiring.form = "linear"
            wiring.linear = _join(wocc.container, iw - 1)

        if exits and wiring.form == "market":
            return "its exits reach the slot through steps the planner cannot move"
        if wiring.form == "market":
            wiring.mask = iw >= 1 and _mask_domain(prev)
            return wiring
        follower = container[iw + 1] if iw + 1 < len(container) else None
        if not (
            isinstance(follower, SlotLoadSpec)
            or _is_data_loader(follower)
            or _join(wocc.container, iw + 1) == mpath
        ):
            return f"the step after Store('{x}') reads the exit signal"
        if wiring.form == "linear":
            if exits != [wiring.linear]:
                return "some of its exits sit outside the exit chain"
            wiring.exits = [wiring.linear]
            if w.occ[wiring.linear].node.name == "ValueStopExitFilter":
                wiring.vsef.append(wiring.linear)
            return wiring
        par: ParallelSpec = w.occ[wiring.parallel].node
        for bname, bsteps in par.branches.items():
            bpath = f"{wiring.parallel}.branch[{bname}]"
            last = bsteps[-1] if bsteps else None
            if isinstance(last, ComponentRef) and last.name in EXITS:
                if _has_deprecated(bsteps[:-1], w) or _writes_slot(bsteps[:-1], w):
                    return f"branch '{bname}' does more than hold one exit"
                epath = f"{bpath}.step[{len(bsteps) - 1}]"
                wiring.exits.append(epath)
                if last.name == "ValueStopExitFilter":
                    wiring.vsef.append(epath)
                wiring.branches[bname] = {"kind": "exit"}
            else:
                if _has_deprecated(bsteps, w):
                    return f"branch '{bname}' hides a deprecated step"
                if _writes_slot(bsteps, w):
                    return f"branch '{bname}' writes a slot"
                wiring.branches[bname] = {
                    "kind": "market",
                    "reads_input": _reads_input(bsteps),
                    "mask": bool(bsteps) and _mask_domain(bsteps[-1]),
                }
        if sorted(wiring.exits) != sorted(exits):
            return "some of its exits sit outside the exits Parallel"
        return wiring

    def _check_exits(self, exits: list[str], entries: str) -> str | None:
        for p in exits:
            o = self.walk.occ[p]
            if _param(o, "entry_slot") != entries:
                return (
                    f"{o.node.name} measures the trade from '{_param(o, 'entry_slot')}', "
                    f"not the manager's entries '{entries}'"
                )
            if (
                "price_column" in _params_of(o)
                and (_param(o, "price_column") or "close") != "close"
            ):
                return f"{o.node.name} measures a price column other than close"
        return None

    def _resolve_prices(self, exits: list[str], fixed: str | None = None):
        """``(prices, insert_at, candidates)`` — see ``_top_level_prices``."""
        slots = {
            _param(self.walk.occ[p], "ohlcv_slot")
            for p in exits
            if "ohlcv_slot" in _params_of(self.walk.occ[p])
        }
        if fixed is not None:
            slots.add(fixed)
        slots.discard(None)
        if len(slots) == 1:
            return next(iter(slots)), None, []
        if len(slots) > 1:
            return None, None, sorted(slots)
        slot, candidates, insert_at = _top_level_prices(self.walk)
        if slot is not None:
            return slot, None, []
        if insert_at is not None:
            return _unique("ohlcv", self.taken), insert_at, []
        return None, None, candidates

    def _exit_questions(self, mpath: str, prices, candidates, wiring: _Wiring) -> list[Question]:
        qs: list[Question] = []
        if prices is None:
            extra = [
                Choice(f"slot:{c}", f"Trade on the prices stored in '{c}'.") for c in candidates
            ]
            qs.append(_question("Q-PRICES", f"Q-PRICES@{mpath}", {}, extra=extra))
        for vpath in wiring.vsef:
            vo = self.walk.occ[vpath]
            lookback = _param(vo, "lookback") or 1
            qs.append(
                _question(
                    "Q-VSEF",
                    f"Q-VSEF@{vpath}",
                    {
                        "lookback": lookback,
                        "retracement_pct": _param(vo, "retracement_pct"),
                        "window": max(5, 2 * int(lookback)),
                    },
                )
            )
        return qs

    def _wiring_recipe(self, wiring: _Wiring, stem: str) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "form": wiring.form,
            "slot": wiring.slot,
            "parallel": wiring.parallel,
            "linear": wiring.linear,
            "branches": wiring.branches,
            "mask": wiring.mask,
        }
        if wiring.form == "parallel" and any(
            b["kind"] == "market" and b["reads_input"] for b in wiring.branches.values()
        ):
            rec["capture"] = _unique(f"{stem}_input", self.taken)
        if wiring.form == "linear":
            rec["rule_name"] = recipe_for(self.walk.occ[wiring.linear].node.name).rule_name
        if wiring.form == "market":
            rec["rule_name"] = _rule_name_for_slot(wiring.slot)
        return rec

    # ── the pins of a rule's trade ops (T26 finding 2, L3a-fix) ──
    def _rule_pins(self, names) -> tuple[list[LockChange], str | None]:
        """``(lock changes, None)`` or ``([], manual reason)`` for the trade
        ops ``names`` this group's recipes put inside TradeManager rules.

        A pin that is trade-safe (spec 03-R39, judged by
        ``binding.trade_safe_verdict``, the one owner) stays. An uncertified
        pin with a certified newer version moves to it, when
        :data:`_PIN_MOVE_PROOFS` proves every other use of the component in
        the strategy keeps its values; otherwise, or when no version is
        certified, the group is manual: there is no other exact trade-safe
        form of these ops (``Above/BelowThresholdFilter`` compare against a
        constant; the ratio form of the ATR trail is inexact, D-45/X-07).
        """
        from pipeline_engine.base.registry_types import get_all_versions
        from pipeline_engine.binding import decl_from_signature, trade_safe_verdict

        changes: list[LockChange] = []
        for name in sorted(set(names)):
            pinned = self.lock.get(name)
            if pinned is None:
                continue  # evolve_lock pins the certified latest
            versions = get_all_versions(name)
            sig = versions.get(int(pinned))
            if sig is None:
                continue  # an unresolvable pin is refused by verification ("validate")
            decl = decl_from_signature(sig, versions)
            if trade_safe_verdict(decl, {}) is None:
                continue
            head = (
                f"the strategy pins {name} v{pinned}, which is not certified to run on trade values"
            )
            newer = decl.newer_trade_safe
            if newer is None:
                return [], f"{head}, and no version of it is"
            uses = [
                o
                for p, o in sorted(self.walk.occ.items(), key=lambda kv: _path_key(kv[0]))
                if isinstance(o.node, ComponentRef) and o.node.name == name
            ]
            key = (name, int(pinned), newer)
            proof = _PIN_MOVE_PROOFS.get(key)
            for o in uses:
                why = "no proof covers that move" if proof is None else proof(self.walk, o)
                if why:
                    return [], (
                        f"{head}; v{newer} is, but moving the pin would also move the {name} at "
                        f"{o.path}, and {why} (v{newer} could compute it differently)"
                    )
            where = ", ".join(o.path for o in uses)
            reason = f"{name} v{pinned} is not certified to run on trade values and v{newer} is: "
            reason += "this upgrade puts it in a TradeManager rule, so the pin moves"
            if uses:
                reason += f". It also moves the strategy's {name} at {where}; "
                reason += _PIN_MOVE_GROUNDS[key]
            changes.append(LockChange(name, int(pinned), newer, reason))
        return changes, None

    def _trade_ops_of(self, exits: list[str], *extra: str) -> list[str]:
        ops = [op for p in exits for op in recipe_for(self.walk.occ[p].node.name).trade_ops]
        return ops + list(extra)

    def _rules_of(self, wiring: _Wiring) -> dict[str, str | None]:
        rules = {p: _branch_name_of(p) for p in wiring.exits}
        if wiring.linear:
            rules[wiring.linear] = recipe_for(self.walk.occ[wiring.linear].node.name).rule_name
        return rules

    def _psm_group(self, mpath: str, paths: list[str]) -> UpgradeGroup:
        w = self.walk
        m = w.occ[mpath]
        entries = _param(m, "entry_slot")
        x = _param(m, "exit_slot")
        exit_mode = _param(m, "exit_mode") or "scalar"
        exits = [p for p in paths if w.occ[p].node.name in EXITS]
        sizers = [p for p in paths if w.occ[p].node.name in SIZERS]

        def manual(reason: str) -> UpgradeGroup:
            g = self._manual(mpath, "PositionStateMachine", paths, reason)
            g.entries, g.exit_slot = entries, x
            return g

        wiring = self._wiring(mpath, "exit_slot", exits)
        if isinstance(wiring, str):
            return manual(wiring)
        if exit_mode == "directional" and wiring.form != "market":
            return manual("a directional PositionStateMachine with deprecated exits")
        reason = self._check_exits(wiring.exits, entries)
        if reason:
            return manual(reason)
        prices, insert_prices_at, candidates = self._resolve_prices(wiring.exits)

        sizer: dict[str, Any] | None = None
        if sizers:
            if len(sizers) > 1 or sizers[0] != _join(m.container, m.index + 1):
                return manual("StopDistanceRiskSizer is not directly after the manager")
            s_occ = w.occ[sizers[0]]
            sizer = {
                "path": sizers[0],
                "mode": _param(s_occ, "stop_mode") or "atr_multiple",
                "stop_dist": _unique(f"{_stem(x)}_stop_dist", self.taken),
            }
            if _param(s_occ, "ohlcv_slot") not in (None, prices) and prices is not None:
                return manual("StopDistanceRiskSizer sizes on other prices than the exits")

        recipe = {
            "kind": "psm",
            "entries": entries,
            "exit_mode": exit_mode,
            "wiring": self._wiring_recipe(wiring, _stem(x)),
            "sizer": sizer,
            "insert_prices_at": insert_prices_at,
        }
        questions = self._exit_questions(mpath, prices, candidates, wiring)
        rules = self._rules_of(wiring)
        directional = _DIRECTIONAL_TRADE_OPS if exit_mode == "directional" else ()
        lock_changes, why = self._rule_pins(self._trade_ops_of(wiring.exits, *directional))
        if why:
            return manual(why)
        return self._group(
            "assisted" if questions else "mechanical",
            mpath,
            "PositionStateMachine",
            paths,
            replaces=self._replaced(paths, rules),
            questions=questions,
            reason=(
                ""
                if not questions
                else (
                    "a price slot must be chosen"
                    if prices is None
                    else "ValueStopExitFilter has no exact long form"
                )
            ),
            entries=entries,
            exit_slot=x,
            prices=prices,
            recipe=recipe,
            lock_changes=lock_changes,
        )

    def _tlre_group(self, mpath: str, paths: list[str]) -> UpgradeGroup:
        w = self.walk
        m = w.occ[mpath]
        entries = _param(m, "entry_slot")
        if (
            (_param(m, "reference_role") or "stop") != "stop"
            or (_param(m, "offset_mode") or "none") != "none"
            or _param(m, "short_level_slot") is not None
        ):
            return self._manual(
                mpath,
                "TradeLevelRiskExit",
                paths,
                "TradeLevelRiskExit uses a target reference, an offset or a short level",
            )
        exits = [p for p in paths if w.occ[p].node.name in EXITS]
        if [p for p in paths if w.occ[p].node.name in SIZERS]:
            return self._manual(
                mpath,
                "TradeLevelRiskExit",
                paths,
                "StopDistanceRiskSizer after a TradeLevelRiskExit",
            )
        external = _param(m, "external_exit_slot")
        wiring: _Wiring | None = None
        if external is not None:
            got = self._wiring(mpath, "external_exit_slot", exits)
            if isinstance(got, str):
                return self._manual(mpath, "TradeLevelRiskExit", paths, got)
            wiring = got
            reason = self._check_exits(wiring.exits, entries)
            if reason:
                return self._manual(mpath, "TradeLevelRiskExit", paths, reason)
        elif exits:
            return self._manual(mpath, "TradeLevelRiskExit", paths, "exits reach it another way")
        prices = _param(m, "ohlcv_slot")
        stem = _stem(external or _param(m, "level_slot") or "tlre")
        values = {
            "manager": mpath,
            "level_slot": _param(m, "level_slot"),
            "reward_multiple": _param(m, "reward_multiple"),
        }
        questions = [_question("Q-TLRE-TEMPLATE", f"Q-TLRE-TEMPLATE@{mpath}", values)]
        if wiring is not None:
            questions += self._exit_questions(mpath, prices, [], wiring)
        lock_changes, why = self._rule_pins(
            self._trade_ops_of(wiring.exits if wiring else [], *RECIPES["q2448.tlre"].trade_ops)
        )
        if why:
            return self._manual(mpath, "TradeLevelRiskExit", paths, why)
        recipe = {
            "kind": "tlre",
            "entries": entries,
            "level_slot": _param(m, "level_slot"),
            "reward_multiple": _param(m, "reward_multiple"),
            "stop_dist": _unique(f"{stem}_stop_dist", self.taken),
            "wiring": self._wiring_recipe(wiring, stem) if wiring is not None else None,
        }
        return self._group(
            "assisted",
            mpath,
            "TradeLevelRiskExit",
            paths,
            replaces=self._replaced(paths, self._rules_of(wiring) if wiring else None),
            questions=questions,
            reason="TradeLevelRiskExit becomes an R-multiple TradeManager",
            entries=entries,
            exit_slot=external,
            prices=prices,
            recipe=recipe,
            lock_changes=lock_changes,
        )


def _params_of(occ: _Occ) -> dict[str, Any]:
    from pipeline_engine.base.registry_types import get_latest

    sig = get_latest(occ.node.name)
    out = dict(sig.parameters) if sig is not None else {}
    out.update(occ.params)
    return out


def _rule_name_for_slot(slot: str) -> str:
    return slot if slot.isidentifier() else _stem(slot)


def _join(container: str, index: int) -> str:
    return f"{container}.step[{index}]" if container else f"step[{index}]"


def _path_key(path: str) -> tuple:
    """Document-order sort key for an edits.py path (branches by name)."""
    parts: list = []
    for kind, arg in re.findall(r"(step|branch|factory|var)\[([^\]]*)\]", path):
        if kind == "step":
            parts.append((1, int(arg), ""))
        else:
            parts.append((2 if kind == "branch" else 0, 0, f"{kind}:{arg}"))
    prefix = 1 if path.startswith(("factory[", "var[")) else 0
    return (prefix, tuple(parts))


def _container_steps(strategy: StrategyFile, container: str) -> list:
    return edits._resolve_steps_list(strategy, container)


def plan_upgrade(strategy: StrategyFile, lock: Mapping[str, int] | None = None) -> UpgradePlan:
    """Find and classify every position group of ``strategy`` (04-R9/R10).

    ``lock`` is the strategy's component lock (versions for ``replaces``
    and for the slot-parameter lookups); ``{}``/``None`` uses latest.
    """
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()
    return _Planner(strategy, lock or {}).plan()


# ═══════════════════════════════════════════════════════════════════════════
# rendering: plan → upgraded text (comment- and byte-preserving)
# ═══════════════════════════════════════════════════════════════════════════


def _find(source: str, predicate) -> str:
    """The path of the unique top-pipeline-reachable step matching ``predicate``."""
    file = parse_strategy(source)
    hits: list[str] = []

    def visit(steps: list, container: str) -> None:
        for i, step in enumerate(steps):
            path = _join(container, i)
            if predicate(step):
                hits.append(path)
            if isinstance(step, ParallelSpec):
                for name, branch in step.branches.items():
                    visit(branch, f"{path}.branch[{name}]")
            elif isinstance(step, PipelineSpec):
                visit(step.steps, path)

    visit(file.pipeline.steps, "")
    if len(hits) != 1:
        raise _verification_failed("locate", f"expected one matching step, found {len(hits)}")
    return hits[0]


def _is_component(name: str, **params: Any):
    def pred(step: Any) -> bool:
        return (
            isinstance(step, ComponentRef)
            and step.name == name
            and all(step.params.get(k) == v for k, v in params.items())
        )

    return pred


def _is_store(slot: str):
    return lambda step: isinstance(step, SlotStoreSpec) and step.slot_name == slot


def _template_steps(template: str, values: Mapping[str, Any]) -> list[str]:
    """Render a step-list template and split it into one text per step."""
    import ast

    rendered = fill_template(template, values)
    tree = ast.parse(rendered, mode="eval").body
    if not isinstance(tree, ast.List):
        raise ValueError(f"recipe fragment is not a step list: {template!r}")
    return [ast.get_source_segment(rendered, elt) for elt in tree.elts]


def _instance_values(occ_params: Mapping[str, Any], component: str) -> dict[str, Any]:
    from pipeline_engine.base.registry_types import get_latest
    from pipeline_engine.constants import MISSING

    sig = get_latest(component)
    values: dict[str, Any] = {}
    if sig is not None:
        for name, pinfo in sig.parameters.items():
            if pinfo.default is not MISSING:
                values[name] = pinfo.default
    values.update(occ_params)
    return values


def _rule_steps(component: str, params: Mapping[str, Any]) -> list[str]:
    return _template_steps(recipe_for(component).rule, _instance_values(params, component))


def _market_suffix(*, slot_form: bool, mask: bool) -> list[str]:
    """What turns a market value into an ``Exit()`` mask (04-R11.5).

    In the MaskOr/OrCombinator form any NON-ZERO branch value closed the
    trade (``_combine_masks`` / ``OrCombinator`` bool-cast), so a branch whose
    domain is not provably {0, 1} becomes ``|x| > 0``. Read straight from the
    exit slot, the manager fired only on exactly 1.0 of its admitted
    {−1, 0, 1}, so ``x ≥ 1`` is exact there.
    """
    if mask:
        return ["Exit()"]
    if slot_form:
        return ["AboveThresholdFilter(threshold=1.0, inclusive=True)", "Exit()"]
    return ["Abs()", "AboveThresholdFilter(threshold=0.0)", "Exit()"]


def _directional_wrap(inner: str) -> list[str]:
    return [
        '{"left": [' + inner + '], "right": [TradeDirection()]}',
        "SignalProduct()",
        "BelowThresholdFilter(threshold=0.0)",
        "Exit()",
    ]


def _parallel_text(rules: Sequence[tuple[str, list[str]]]) -> str:
    lines = ["{"]
    for name, steps in rules:
        lines.append(f"    {edits.render_value(name, prefer_quote=chr(34))}: [{', '.join(steps)}],")
    lines.append("}")
    return "\n".join(lines)


def _add_comments_above(source: str, step_path: str, comments: list[str]) -> str:
    """Re-emit carried comments as full-line comments above a step (04-R12)."""
    if not comments:
        return source
    idx = edits.build_span_index(source)
    info = idx.steps.get(step_path)
    if info is None or not edits._starts_its_line(source, info.span.start):
        raise _verification_failed("comments", f"cannot place carried comments above {step_path}")
    col = edits._column_of(source, info.span.start)
    at = edits._line_start(source, info.span.start)
    text = "".join(f"{' ' * col}{c}\n" for c in comments)
    new_text = source[:at] + text + source[at:]
    return edits._verify(new_text, idx.file, f"add_comments_above({step_path})")


def _str(value: str) -> str:
    return _render(value, "str")


def _insert_rules(
    source: str, manager, index_after: int, wiring: Mapping, scalar: bool, directional: bool
) -> str:
    """Insert one manager's exit rules at ``index_after`` of its container.

    ``parallel``: the exits Parallel MOVES there byte for byte (branch names
    verbatim, D-22) and each branch is rewritten in place; ``linear``: a
    one-rule Parallel named by the recipe; ``market``: one rule reading the
    exit slot, which stays.
    """
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    form = wiring["form"]
    # The planner's paths may be stale by now (a captured price Store shifts
    # them), so the wiring is re-located from its one writer, Store(X).
    if form in ("parallel", "linear"):
        wpath = _find(source, _is_store(wiring["slot"]))
        wcont, widx = _container_and_index(wpath)
    if form == "parallel":
        source = edits.move_step(source, _join(wcont, widx - 2), mcont, index_after)
        mpath = _find(source, manager)
        mcont, midx = _container_and_index(mpath)
        p_new = _join(mcont, midx + 1)  # the move lands it right after the manager
        par = edits._resolve_step_node(parse_strategy(source), p_new)[2]
        if not isinstance(par, ParallelSpec):
            raise _verification_failed("locate", f"the moved exits Parallel is not at {p_new}")
        for bname, info in wiring["branches"].items():
            bpath = f"{p_new}.branch[{bname}]"
            if info["kind"] == "exit":
                exit_node = par.branches[bname][-1]
                source = edits.replace_step_list(
                    source, bpath, _rule_steps(exit_node.name, dict(exit_node.params))
                )
                continue
            n = len(par.branches[bname])
            for k, step_text in enumerate(_market_suffix(slot_form=False, mask=info["mask"])):
                source = edits.insert_step_text(source, bpath, n + k, step_text)
            if info.get("reads_input") and wiring.get("capture"):
                source = edits.insert_step_text(
                    source, bpath, 0, f"Load({_str(wiring['capture'])})"
                )
        return source
    if form == "linear":
        lin = edits._resolve_step_node(parse_strategy(source), _join(wcont, widx - 1))[2]
        rule = _rule_steps(lin.name, dict(lin.params))
        return edits.insert_step_text(
            source, mcont, index_after, _parallel_text([(wiring["rule_name"], rule)])
        )
    load = f"Load({_str(wiring['slot'])})"
    if directional:
        steps = _directional_wrap(load)
    else:
        steps = [load] + _market_suffix(slot_form=scalar, mask=wiring.get("mask", False))
    return edits.insert_step_text(
        source, mcont, index_after, _parallel_text([(wiring["rule_name"], steps)])
    )


def _remove_wiring(source: str, wiring: Mapping) -> tuple[str, list[str]]:
    """Delete ``Store(X)`` and the combiner / linear exit before it (04-R11.2).

    Returns the comments those deletions took out, for re-emission. With
    ``capture``, ``Store("<X>_input")`` takes the exits Parallel's old place.
    """
    carried: list[str] = []
    if wiring["form"] == "market":
        return source, carried
    wpath = _find(source, _is_store(wiring["slot"]))
    wcont, widx = _container_and_index(wpath)
    source, removed = edits.delete_step_keep_comments(source, wpath)
    carried += removed
    source, removed = edits.delete_step_keep_comments(source, _join(wcont, widx - 1))
    carried += removed
    if wiring["form"] == "parallel" and wiring.get("capture"):
        source = edits.insert_step_text(
            source, wcont, widx - 1, f"Store({_str(wiring['capture'])})"
        )
    return source, carried


def _answers_ok(group: UpgradeGroup, answers: Mapping[str, str]) -> tuple[bool, str | None]:
    """``(proceed, prices)`` from a group's answers; ``keep`` anywhere keeps all."""
    prices = group.prices
    for q in group.questions:
        ans = _answer_for(q, answers)
        if ans.choice == "keep":
            return False, None
        if q.id == "Q-PRICES":
            prices = ans.choice.removeprefix("slot:")
    return True, prices


def _render_psm(source: str, group: UpgradeGroup, answers: Mapping[str, str]) -> str:
    proceed, prices = _answers_ok(group, answers)
    if not proceed:
        return source
    r = group.recipe
    wiring = r["wiring"]
    manager = _is_component(
        "PositionStateMachine", entry_slot=group.entries, exit_slot=group.exit_slot
    )
    if r.get("insert_prices_at") is not None:
        source = edits.insert_step_text(source, "", r["insert_prices_at"], f"Store({_str(prices)})")

    # 1. After the manager: the realiser, or RiskSizer in the old sizer's place (04-R11.6, R14).
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    prelude: list[str] = []
    sizer = r.get("sizer")
    if sizer is not None:
        spath = _join(mcont, midx + 1)
        s_node = edits._resolve_step_node(parse_strategy(source), spath)[2]
        values = _instance_values(dict(s_node.params), "StopDistanceRiskSizer")
        values["stop_dist"] = sizer["stop_dist"]
        values["ohlcv_slot"] = values.get("ohlcv_slot") or prices
        rs = RECIPES["q2448.risk_sizer"]
        tpl = rs.prelude if sizer["mode"] == "atr_multiple" else _LEVEL_DISTANCE_PRELUDE
        prelude = _template_steps(tpl, values)
        source = edits.replace_step_text(source, spath, _template_steps(rs.rule, values)[0])
    else:
        source = edits.insert_step_text(source, mcont, midx + 1, "Exposure()")

    # 2. The rules, directly after the manager (04-R11.4/5).
    source = _insert_rules(
        source,
        manager,
        midx + 1,
        wiring,
        scalar=r["exit_mode"] == "scalar",
        directional=r["exit_mode"] == "directional",
    )
    # 3. The old wiring goes; its comments are carried (04-R11.2, R12).
    source, carried = _remove_wiring(source, wiring)
    # 4. Preludes, then the manager itself (04-R11.3/6).
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    for k, step_text in enumerate(prelude):
        source = edits.insert_step_text(source, mcont, midx + k, step_text)
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    head = fill_template(
        "TradeManager(entries={entries:str}, prices={prices:str})",
        {"entries": group.entries, "prices": prices},
    )
    source = edits.replace_step_text(source, mpath, head)
    return _add_comments_above(source, _join(mcont, midx + 1), carried)


def _render_tlre(source: str, group: UpgradeGroup, answers: Mapping[str, str]) -> str:
    proceed, prices = _answers_ok(group, answers)
    if not proceed:
        return source
    r = group.recipe
    manager = _is_component(
        "TradeLevelRiskExit", entry_slot=group.entries, level_slot=r["level_slot"]
    )
    values = {
        "entry_slot": group.entries,
        "ohlcv_slot": prices,
        "level_slot": r["level_slot"],
        "reward_multiple": r["reward_multiple"],
        "stop_dist": r["stop_dist"],
    }
    tl = RECIPES["q2448.tlre"]
    rule = _template_steps(tl.rule, values)  # TM, {num/den}, SignalRatio, {stop/target}, Exposure
    prelude = _template_steps(tl.prelude, values)

    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    # After the manager, in final order: R readers, ratio, stop/target, external rules, Exposure.
    source = edits.insert_step_text(source, mcont, midx + 1, rule[-1])  # Exposure()
    carried: list[str] = []
    wiring = r.get("wiring")
    if wiring is not None:
        source = _insert_rules(source, manager, midx + 1, wiring, scalar=True, directional=False)
    mpath = _find(source, manager)  # a moved Parallel may have shifted the manager
    mcont, midx = _container_and_index(mpath)
    for step_text in reversed(rule[1:-1]):
        source = edits.insert_step_text(source, mcont, midx + 1, step_text)
    if wiring is not None:
        source, carried = _remove_wiring(source, wiring)
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    for k, step_text in enumerate(prelude):
        source = edits.insert_step_text(source, mcont, midx + k, step_text)
    mpath = _find(source, manager)
    mcont, midx = _container_and_index(mpath)
    source = edits.replace_step_text(source, mpath, rule[0])
    return _add_comments_above(source, _join(mcont, midx + 1), carried)


_RENDERERS = {"psm": _render_psm, "tlre": _render_tlre}


def render_upgrade(
    source: str, plan: UpgradePlan, answers: Mapping[str, str] | Sequence[str] | None = None
) -> str:
    """The upgraded text of ``source`` under ``plan`` (04-R11/R12).

    Mechanical groups need no answers; assisted groups need one per
    question (``keep`` leaves a group untouched); manual groups are left
    as they are. Every primitive is parse-verified; no validation runs here
    (:func:`apply_upgrade` adds it).
    """
    answers = parse_answers(answers)
    for group in plan.groups:
        if group.mode == "manual":
            continue
        kind = group.recipe.get("kind")
        if kind == "keep_only":
            for q in group.questions:
                _answer_for(q, answers)  # only `keep` is offered; still required
            continue
        source = _RENDERERS[kind](source, group, answers)
    return source


# ═══════════════════════════════════════════════════════════════════════════
# verification (04-R18) and the one-call surface
# ═══════════════════════════════════════════════════════════════════════════


def _upgraded_members(plan: UpgradePlan, answers: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for g in plan.groups:
        if g.mode == "manual" or g.recipe.get("kind") == "keep_only":
            continue
        if any(_answer_for(q, answers).choice == "keep" for q in g.questions):
            continue
        out.extend(g.members)
    return out


def _applied_lock_changes(plan: UpgradePlan, answers: Mapping[str, str]) -> list[LockChange]:
    """The pin moves of the groups this upgrade actually rewrites, one per
    component (every group derives the target from the same registry)."""
    out: dict[str, LockChange] = {}
    for g in plan.groups:
        if g.mode == "manual" or g.recipe.get("kind") == "keep_only":
            continue
        if any(_answer_for(q, answers).choice == "keep" for q in g.questions):
            continue
        for c in g.lock_changes:
            out.setdefault(c.component, c)
    return [out[k] for k in sorted(out)]


def _instance_names(strategy: StrategyFile, paths: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in paths:
        name = edits._resolve_step_node(strategy, p)[2].name
        counts[name] = counts.get(name, 0) + 1
    return counts


def verify_upgrade(
    source: str,
    result: str,
    plan: UpgradePlan,
    lock: Mapping[str, int],
    answers: Mapping[str, str] | Sequence[str] | None = None,
) -> dict[str, int]:
    """Run 04-R18's checks on ``result``; return the evolved lock or raise.

    Checks, each named in the error: ``parse``; ``comments`` (multiset
    equal); ``deprecation`` (exactly the deprecated instances of upgraded
    groups are gone, every other one is untouched); ``pins`` (every
    component still present keeps its pin, except the plan's proven
    :class:`LockChange` moves, which land exactly); ``validate`` (zero errors
    under the evolved lock).
    """
    from pipeline_engine.base.lock import evolve_lock, upgrade_lock_entries
    from pipeline_engine.dsl.validator import validate_strategy

    answers = parse_answers(answers)
    try:
        parsed = parse_strategy(result)
    except DSLParseError as exc:
        raise _verification_failed("parse", str(exc)) from exc
    if edits.comment_multiset(result) != edits.comment_multiset(source):
        raise _verification_failed("comments", "the comment multiset changed")
    before = _instance_names(parse_strategy(source), plan.instances)
    upgraded = _instance_names(parse_strategy(source), _upgraded_members(plan, answers))
    expected = {k: before[k] - upgraded.get(k, 0) for k in before if before[k] - upgraded.get(k, 0)}
    after_plan = plan_upgrade(parsed, lock)
    after = _instance_names(parsed, after_plan.instances)
    if after != expected:
        raise _verification_failed(
            "deprecation", f"deprecated instances left {after}, expected {expected}"
        )
    from pipeline_engine.exceptions import LockError

    moves = {c.component: c.to_version for c in _applied_lock_changes(plan, answers)}
    try:
        new_lock = evolve_lock(upgrade_lock_entries(dict(lock or {}), moves), parsed)
    except LockError as exc:
        # A component the result names is not registered: it cannot validate.
        raise _verification_failed("validate", str(exc)) from exc
    for name, version in (lock or {}).items():
        expected = moves.get(name, version)
        if name in new_lock and new_lock[name] != expected:
            raise _verification_failed("pins", f"{name} moved from v{version} to v{new_lock[name]}")
    validation = validate_strategy(parsed, lock=new_lock)
    if validation.errors:
        first = validation.errors[0]
        raise _verification_failed("validate", f"{first.code} at {first.location}: {first.message}")
    return new_lock


def apply_upgrade(
    source: str,
    plan: UpgradePlan,
    answers: Mapping[str, str] | Sequence[str] | None = None,
    *,
    lock: Mapping[str, int] | None = None,
) -> str:
    """Render the upgrade and verify it (04-R18); return the text or raise.

    Raises :class:`UpgradeAnswerError` for a missing/invalid answer and
    :class:`UpgradeVerificationError` (naming the failed check) for anything
    else. Never falls back to whole-file re-emission.
    """
    return _apply(source, plan, answers, lock or {})[0]


def _apply(
    source: str,
    plan: UpgradePlan,
    answers: Mapping[str, str] | Sequence[str] | None,
    lock: Mapping[str, int],
) -> tuple[str, dict[str, int]]:
    """:func:`apply_upgrade`, also returning the verified evolved lock."""
    try:
        result = render_upgrade(source, plan, answers)
    except (UpgradeAnswerError, UpgradeVerificationError):
        raise
    except (edits.EditError, DSLParseError, KeyError, ValueError) as exc:
        raise _verification_failed("edit", f"{type(exc).__name__}: {exc}") from exc
    return result, verify_upgrade(source, result, plan, lock, answers)


def upgrade_strategy_source(
    source: str,
    component_lock: Mapping[str, int] | None = None,
    answers: Mapping[str, str] | Sequence[str] | None = None,
) -> dict[str, Any]:
    """The shared engine behind every upgrade surface (04-R21).

    Returns ``{groups, source, diff, validation, component_lock,
    lock_changes}``. ``source`` is ``None`` while any assisted question lacks
    an answer, or when no group is upgradable; ``diff`` is a unified diff of
    the change. ``lock_changes`` lists the pins the applied upgrade moved
    (:class:`LockChange`, each with its reason); the diff covers the text
    only, so a moved pin is reported there and in ``component_lock``.
    """
    from pipeline_engine.dsl.validator import validate_strategy

    lock = dict(component_lock or {})
    plan = plan_upgrade(parse_strategy(source), lock)
    parsed_answers = parse_answers(answers)
    out: dict[str, Any] = {
        "groups": [g.to_dict() for g in plan.groups],
        "source": None,
        "diff": "",
        "validation": None,
        "component_lock": lock,
        "lock_changes": [],
    }
    pending = [
        q.key
        for g in plan.groups
        if g.mode == "assisted"
        for q in g.questions
        if q.key not in parsed_answers and q.id not in parsed_answers
    ]
    if pending or not any(g.mode != "manual" for g in plan.groups):
        return out
    result, new_lock = _apply(source, plan, parsed_answers, lock)
    validation = validate_strategy(parse_strategy(result), lock=new_lock)
    out.update(
        source=result,
        diff="".join(
            difflib.unified_diff(
                source.splitlines(keepends=True),
                result.splitlines(keepends=True),
                fromfile="before",
                tofile="after",
            )
        ),
        validation={
            "ok": not validation.errors,
            "errors": [i.to_dict() for i in validation.errors],
            "warnings": [i.to_dict() for i in validation.warnings],
        },
        component_lock=new_lock,
        lock_changes=[c.to_dict() for c in _applied_lock_changes(plan, parsed_answers)],
    )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# the validator's POSITION_UPGRADE_AVAILABLE issue (04-R9, R16, R17)
# ═══════════════════════════════════════════════════════════════════════════

#: 04-R16's applicability per mode (``catalog.Applicability`` values).
APPLICABILITY = {
    "mechanical": "machine_applicable",
    "assisted": "has_placeholders",
    "manual": "none",
}


@dataclass(frozen=True)
class UpgradeIssueSpec:
    """Everything the validator needs to emit ONE ``POSITION_UPGRADE_AVAILABLE``.

    The rule is catalogued by spec 03 (L2a, P03-11); the validator's pass 4u
    calls :func:`upgrade_issue_specs` and emits one issue per spec::

        emit(issues, "POSITION_UPGRADE_AVAILABLE", location=spec.location,
             path=spec.path, suggested_edit=spec.suggested_edit,
             applicability_override=spec.applicability, **spec.params)

    ``params`` are exactly the catalog template's placeholders (``manager``,
    ``components``, ``known_issue_note``, ``mode``).
    """

    location: str
    path: tuple[str, ...]
    params: dict[str, str]
    suggested_edit: Any  # validation_shared.SuggestedEdit
    applicability: str


def _keep_assisted_answers(plan: UpgradePlan) -> dict[str, str]:
    """``keep`` for every assisted question: the text a mechanical group's
    ``result_source`` shows upgrades the mechanical groups only."""
    return {q.key: "keep" for g in plan.groups if g.mode == "assisted" for q in g.questions}


def upgrade_issue_specs(
    strategy: StrategyFile,
    lock: Mapping[str, int] | None = None,
    *,
    source: str | None = None,
) -> list[UpgradeIssueSpec]:
    """One :class:`UpgradeIssueSpec` per position group (04-R9, R16, R17).

    Coverage: every deprecated instance is in exactly one group, so in exactly
    one issue (``DEPRECATED_COMPONENT`` still fires per instance). The
    payload is 04-R16's: ``recipe``, ``mode``, ``group``, ``known_issues``,
    ``replaces``, ``questions``, ``ops``, ``lock_changes`` and — only for a
    mechanical group when ``source`` is given — ``result_source``, the
    VERIFIED text (04-R18) of the strategy with every mechanical group
    upgraded and every assisted group kept, with ``component_lock`` beside
    it: the verified evolved lock that text validates under. A verification
    refusal omits both rather than returning unverified text (no silent
    fallback).

    ``lock_changes`` is the pin moves applying THIS offer makes. Beside a
    ``result_source`` that is every move that text needs — the union over
    the mechanical groups it applies, exactly the difference between the
    stored lock and ``component_lock`` — so saving ``result_source`` with
    ``component_lock`` is the whole edit (L3a-fix2: a payload listing only
    its own group's moves under a text that applied a sibling group's move
    was valid under no lock it described). Without a ``result_source`` the
    offer is the group alone, so it is that group's own moves.

    ``ops`` is empty: the planner renders through ``dsl/edits.py``'s
    primitives itself, so the machine-applicable form is ``result_source``
    (recorded deviation from 04-R16's op list).
    """
    from pipeline_engine.validation_shared import SuggestedEdit

    lock = dict(lock or {})
    plan = plan_upgrade(strategy, lock)
    if not plan.groups:
        return []
    result_source: str | None = None
    result_lock: dict[str, int] | None = None
    result_changes: list[dict[str, Any]] = []
    if source is not None and any(g.mode == "mechanical" for g in plan.groups):
        keep = _keep_assisted_answers(plan)
        try:
            result_source, result_lock = _apply(source, plan, keep, lock)
        except (UpgradeVerificationError, UpgradeAnswerError):
            result_source, result_lock = None, None
        else:
            result_changes = [c.to_dict() for c in _applied_lock_changes(plan, keep)]
    specs: list[UpgradeIssueSpec] = []
    for g in plan.groups:
        names: list[str] = []
        if g.manager:
            names.append(g.manager)
        for r in g.replaces:
            if r.component not in names:
                names.append(r.component)
        note = f", known issue {', '.join(g.known_issues)}" if g.known_issues else ""
        payload: dict[str, Any] = {
            "recipe": RECIPE_FAMILY,
            "mode": g.mode,
            "group": {
                "manager": g.path if g.manager else None,
                "entries": g.entries,
                "exit_slot": g.exit_slot,
                "prices": g.prices,
            },
            "known_issues": list(g.known_issues),
            "replaces": [r.to_dict() for r in g.replaces],
            "questions": [q.to_dict() for q in g.questions] if g.mode == "assisted" else [],
            "ops": [],
            "lock_changes": [c.to_dict() for c in g.lock_changes],
        }
        if g.mode == "mechanical" and result_source is not None:
            payload["result_source"] = result_source
            payload["component_lock"] = dict(result_lock or {})
            payload["lock_changes"] = [dict(c) for c in result_changes]
        specs.append(
            UpgradeIssueSpec(
                location=g.path,
                path=(g.path,),
                params={
                    "manager": g.manager or (names[0] if names else ""),
                    "components": ", ".join(names),
                    "known_issue_note": note,
                    "mode": g.mode,
                },
                suggested_edit=SuggestedEdit(kind="upgrade", path=(g.path,), payload=payload),
                applicability=APPLICABILITY[g.mode],
            )
        )
    return specs


__all__ = [
    "MANUAL_HELP_TOPIC",
    "QUESTIONS",
    "Q2448",
    "Q2448_ISSUE",
    "Q2448_SUMMARY",
    "RECIPES",
    "RECIPE_FAMILY",
    "Answer",
    "Choice",
    "LockChange",
    "Question",
    "QuestionTemplate",
    "Recipe",
    "Replaced",
    "UpgradeAnswerError",
    "UpgradeGroup",
    "UpgradePlan",
    "UpgradeIssueSpec",
    "UpgradeVerificationError",
    "APPLICABILITY",
    "apply_upgrade",
    "fill_template",
    "parse_answers",
    "plan_upgrade",
    "recipe_for",
    "recipe_shape",
    "render_upgrade",
    "template_placeholders",
    "upgrade_issue_specs",
    "upgrade_strategy_source",
    "verify_upgrade",
]
