"""Shared validation constants, types, and pure functions.

Used by both the DSL validator (Phase 1) and the runtime PipelineValidator.
The DSL validator walks ComponentRef specs + registry lookups; the runtime
validator walks live step instances + get_type_hints. This module provides
the shared pieces: type transition graph, error codes, validation result types.

Quick Start:
    >>> from pipeline_engine.validation_shared import (
    ...     TYPE_TRANSITIONS, ErrorCode, ValidationResult,
    ... )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pipeline_engine.base.registry import is_compatible  # noqa: F401 — re-export
from pipeline_engine.base.step import PHASE_GROUPS, StepCategory


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RESAMPLER CONFIG RULES (source_tf, target_tf, bar_offset)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Single source of truth for resampling constraints. Used by:
#   - TimeframeResampler / TargetTimeframeResampler at runtime
#   - DSL validator pass 9 (catches errors before backtest submission)
#   - TypeScript validator (mirrored in pass9-declarations.ts)
#
# Same error messages across all three so the agent sees identical feedback
# regardless of which layer rejected the config.


TIMEFRAME_MINUTES: dict[str, int] = {
    "15min": 15,
    "30min": 30,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


def timeframe_to_minutes(tf: str) -> int:
    """Convert a canonical timeframe string to minutes. Raises on unknown."""
    if tf not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {sorted(TIMEFRAME_MINUTES)}")
    return TIMEFRAME_MINUTES[tf]


#: Inverse alphabet: minutes → canonical token (10 entries; bijective).
MINUTES_TO_TOKEN: dict[int, str] = {m: tf for tf, m in TIMEFRAME_MINUTES.items()}


def render_clock(clock: tuple[int, int]) -> str:
    """Canonical clock rendering — ONE helper drives both engines' message
    text (dsl-mtf-clocks spec 02 §4.2).

    The canonical token, plus ``@`` + offset-literal iff the phase is
    non-zero: ``(240, 0) → "4h"``; ``(1440, 720) → "1d@12h"``. Non-token
    offsets — any positive minute count ``parse_bar_offset_minutes`` admits —
    render canonically as ``@{n}min`` (R02-MIN-12): ``(60, 45) → "1h@45min"``.
    The TS mirror (M4e) reads the same tokens from generated data
    (``validation_tables.json``'s TIMEFRAME_MINUTES); no engine ever does
    arithmetic on these display strings.
    """
    p, o = clock
    base = MINUTES_TO_TOKEN.get(p, f"{p}min")
    if o == 0:
        return base
    tok = MINUTES_TO_TOKEN.get(o)
    return f"{base}@{tok}" if tok is not None else f"{base}@{o}min"


_BAR_OFFSET_UNIT_MINUTES: dict[str, int] = {
    "min": 1,
    "h": 60,
    "d": 24 * 60,
    "w": 7 * 24 * 60,
}

# Unified bar-offset grammar (core-engine-audit spec 02 §4.4, 2026-07-10):
# strict — case-sensitive, no whitespace — matching the TS editor validator's
# regex in pass9-declarations.ts exactly. Python used to accept '12H ' /
# ' 15min' (IGNORECASE + \s*) while TS rejected them: a live, unfixtured
# grammar divergence. Strictness is the safer unification direction and no
# stored strategy depends on the lenient forms (emitters always write
# canonical lowercase). Fixture bar_offset_grammar_reject pins the '12H '
# case in both languages.
_BAR_OFFSET_RE = re.compile(r"^(\d+)(min|h|d|w)$")


def parse_bar_offset_minutes(bar_offset: str) -> int:
    """Parse bar_offset to whole minutes. Raises ValueError on invalid.

    Strict grammar ('15min', '12h', '1d', '90min' — lowercase unit, no
    whitespace; unified with the TS editor validator, spec 02 §4.4) and
    strict about value (positive, whole minutes only — sub-minute offsets
    are nonsense on a 15min-source platform).
    """
    if not isinstance(bar_offset, str):
        raise ValueError(
            f"bar_offset ({bar_offset!r}) is not a valid duration. "
            f"Use a value like '15min', '30min', '1h', '12h'."
        )
    match = _BAR_OFFSET_RE.match(bar_offset)
    if not match:
        raise ValueError(
            f"bar_offset ({bar_offset!r}) is not a valid duration. "
            f"Use a value like '15min', '30min', '1h', '12h'."
        )
    n = int(match.group(1))
    unit = match.group(2)
    if n <= 0:
        raise ValueError(f"bar_offset ({bar_offset!r}) must be positive.")
    return n * _BAR_OFFSET_UNIT_MINUTES[unit]


def validate_resample_config(
    source_tf: str,
    target_tf: str,
    bar_offset: str | None,
    source_offset_minutes: int = 0,
) -> None:
    """Validate a (source_tf, target_tf, bar_offset) triple. Raises ValueError.

    The phase rows are the **general Theorem-1 form** of
    dsl-multi-timeframe-clocks spec 01 §5.2, landed at the
    ``clock-transform-rebase`` PROMOTED flip (spec 03 §3.5 / §10 row 21,
    resolution R-7): ``o_in`` — the phase of the INPUT clock — is a parameter,
    never assumed 0.

    Rules (``p_in``/``p_out`` = source/target minutes, ``o_in`` =
    ``source_offset_minutes``, ``off`` = the requested offset):

      - ``p_in > p_out``                      → upsampling not supported
      - ``p_in == p_out ∧ off ≠ o_in``        → no valid offset range at same TF
      - ``off ≢ o_in (mod p_in)``             → must be a multiple of source bar size
      - ``off ≥ p_out``                       → must be strictly less than target
                                                (whole-period offsets are silent
                                                no-ops because pandas wraps
                                                mod-period)

    ``source_offset_minutes=0`` (the default) reproduces the historical
    per-hop rule exactly: an input read straight off a loader sits on the
    epoch grid at phase 0. The parameter matters for the **chained-offset**
    shape — ``(15,0) → (60,30) → (240,30)`` under
    ``Globals(bar_offset="30min")`` — which the old ``off % p_in`` form
    rejected at the second hop and the general form legalizes, because the
    target's boundaries never cut a source bar. That legalization is an
    INTENDED verdict change, enumerated in the D5 fire inventory and the D6
    expectations ledger (``prior_semantics: per_hop_reject``); it is the one
    place where validation and runtime BOTH loosen, together, so the two can
    never disagree on a chained shape.
    """
    source_mins = timeframe_to_minutes(source_tf)
    target_mins = timeframe_to_minutes(target_tf)

    if source_mins > target_mins:
        raise ValueError(
            f"Cannot resample {source_tf} → {target_tf}: upsampling not supported "
            f"(source must be ≤ target)."
        )

    if bar_offset is None:
        return

    offset_mins = parse_bar_offset_minutes(bar_offset)
    o_in = source_offset_minutes % source_mins

    if source_mins == target_mins:
        if offset_mins == o_in:
            # κ_out == κ_in: the offset is already the input's own phase, so
            # the "transform" is the identity — a no-op lint, not an error
            # (spec 01 §5.2's RESAMPLER_NOOP row owns it at the write-time
            # surface; the runtime short-circuits it).
            return
        raise ValueError(
            f"bar_offset ({bar_offset}) has no valid value when target_timeframe "
            f"equals the data loader's timeframe ({source_tf}). Remove bar_offset, "
            f"or set a larger target_timeframe."
        )

    if (offset_mins - o_in) % source_mins != 0:
        raise ValueError(
            f"bar_offset ({bar_offset}) must be a multiple of the data loader's "
            f"timeframe ({source_tf})."
        )

    if offset_mins >= target_mins:
        raise ValueError(
            f"bar_offset ({bar_offset}) must be strictly less than target_timeframe "
            f"({target_tf}); whole-period offsets are silent no-ops (pandas wraps "
            f"mod-period). For 'act N bars delayed' tests, use IndexShift_Nbars instead."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR CODES
# ═══════════════════════════════════════════════════════════════════════════════


class ErrorCode:
    """Validation error codes used by the runtime PipelineValidator."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    PHASE_ORDER_VIOLATION = "PHASE_ORDER_VIOLATION"
    SLOT_NOT_FOUND = "SLOT_NOT_FOUND"
    TYPE_HINTS_UNAVAILABLE = "TYPE_HINTS_UNAVAILABLE"
    VALIDATION_DEPTH_EXCEEDED = "VALIDATION_DEPTH_EXCEEDED"
    TRANSITION_INVALID = "TRANSITION_INVALID"
    TRANSITION_OUTPUT_MISMATCH = "TRANSITION_OUTPUT_MISMATCH"
    DICT_INPUT_EXPECTED = "DICT_INPUT_EXPECTED"
    DICT_NOT_CONSUMED = "DICT_NOT_CONSUMED"
    COMPOSER_MISSING_KEYS = "COMPOSER_MISSING_KEYS"
    SLOT_SELF_CYCLE = "SLOT_SELF_CYCLE"
    EXTRACT_MISSING_KEY = "EXTRACT_MISSING_KEY"


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION TYPES
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# TYPED-ISSUE ENVELOPE SUB-SCHEMAS (dsl-type-system spec 05 §3.2)
#
# JSON-native, stable shapes. Each carries a ``to_dict()`` producing exactly the
# spec 05 §3.2 wire form; the TS mirror (spec 05 §3.6) and the shared
# ``dsl/fixtures/envelope/full_issue.json`` fixture pin these shapes across both
# languages.
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DomainRef:
    """A value-domain lattice element in wire form (spec 05 §3.2).

    ``kind == "top"`` (⊤, no constraint); ``"set"`` (a literal value set, with
    the optional spec 02 ``domain_aliases`` name — Binary/Flag/Mask); or
    ``"interval"`` (a ``[low, high]`` bound).
    """

    kind: Literal["top", "set", "interval"]
    values: tuple[float, ...] | None = None
    low: float | None = None
    high: float | None = None
    alias: str | None = None

    @staticmethod
    def top() -> "DomainRef":
        return DomainRef("top")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "set":
            return {
                "kind": "set",
                "values": [float(v) for v in (self.values or ())],
                "alias": self.alias,
            }
        if self.kind == "interval":
            return {
                "kind": "interval",
                "low": float(self.low),  # type: ignore[arg-type]
                "high": float(self.high),  # type: ignore[arg-type]
                "alias": self.alias,
            }
        return {"kind": "top"}


@dataclass(frozen=True)
class ClockRef:
    """A clock in wire form (dsl-mtf-clocks spec 01 §10; spec 02 §4.1).

    ``timeframe`` carries the canonical TOKEN (e.g. ``"4h"`` — never minutes
    alone) because the alphabet is generated data and both engines manipulate
    tokens; ``period_minutes``/``phase_minutes`` ride along so no engine does
    arithmetic on display strings; ``origin`` is the path of the step that
    last set this clock, in the existing envelope path addressing (§3.1 #7 —
    no new addressing scheme).
    """

    timeframe: str
    period_minutes: int
    phase_minutes: int
    origin: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "period_minutes": self.period_minutes,
            "phase_minutes": self.phase_minutes,
            "origin": list(self.origin),
        }


@dataclass(frozen=True)
class TypeRef:
    """A synthesized or demanded type ``(declared, base, domain, tier, clock)`` (§3.2).

    ``declared`` is the name as shown (refined names verbatim, e.g.
    ``"BinarySignal"``); ``base`` is the checked base (for demands, the
    exp-unfolded parent); ``tier`` is the refinement demand strength
    (``hard`` set / ``soft`` interval / ``none`` ⊤); ``clock`` is the fourth
    member, exactly parallel to ``domain`` (dsl-mtf-clocks spec 01 §10 —
    ``None`` when the value is clock-less). ``to_dict()`` always emits the
    ``clock`` key, matching the envelope's always-present-keys discipline
    (spec 02 §4.1).
    """

    declared: str
    base: str
    domain: DomainRef
    tier: Literal["hard", "soft", "none"] = "none"
    clock: ClockRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "base": self.base,
            "domain": self.domain.to_dict(),
            "tier": self.tier,
            "clock": self.clock.to_dict() if self.clock is not None else None,
        }


@dataclass(frozen=True)
class Span:
    """A source-text span in the strategy declaration (§3.2)."""

    line: int
    col: int
    end_line: int
    end_col: int

    def to_dict(self) -> dict[str, int]:
        return {
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


@dataclass(frozen=True)
class ProvenanceHop:
    """One hop of a domain/slot blame chain (origin → transfer → store → load)."""

    kind: Literal["origin", "transfer", "store", "load", "projection"]
    step: str
    path: tuple[str, ...] = ()
    type: TypeRef | None = None
    slot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step": self.step,
            "path": list(self.path),
            "type": self.type.to_dict() if self.type is not None else None,
            "slot": self.slot,
        }


@dataclass(frozen=True)
class ValidOption:
    """A machine-readable candidate, type-fit ranked (§3.2, R6-REQ4)."""

    kind: Literal["component", "value", "key", "slot", "version"]
    value: str | float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "detail": self.detail}


@dataclass(frozen=True)
class SuggestedEdit:
    """A concrete edit payload (§3.2, R6-REQ5) — closed op vocabulary."""

    kind: Literal["set_param", "replace_component", "insert_step", "remove_step", "rename"]
    path: tuple[str, ...]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": list(self.path), "payload": dict(self.payload)}


@dataclass(frozen=True)
class StagingNote:
    """Severity honesty for a staged code (§3.1 #16): ``{stage, terminal_severity}``."""

    stage: str
    terminal_severity: str

    def to_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "terminal_severity": self.terminal_severity}


@dataclass
class ValidationIssue:
    """A single validation finding — the 16-field typed envelope (spec 05 §3.1).

    Five preserved fields (``severity``/``code``/``message``/``location``/
    ``suggestion``) keep their names, types, and meanings so every existing
    consumer is untouched; eleven new fields are all defaulted so the ~60
    non-type-shaped emission sites change zero call sites. ``to_dict()``
    serializes all 16 keys, always present (spec 05 §3.4 stable-shape).
    """

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str
    suggestion: str | None = None
    # ── Envelope extension (dsl-type-system spec 05 §3.1) — 11 new fields ──
    tier: Literal["static", "compile", "runtime", "gate"] = "static"
    path: tuple[str, ...] = ()
    span: Span | None = None
    expected: TypeRef | None = None
    actual: TypeRef | None = None
    provenance: tuple[ProvenanceHop, ...] = ()
    valid_options: tuple[ValidOption, ...] = ()
    suggested_edit: SuggestedEdit | None = None
    applicability: str = "none"
    recoverable: bool = True
    staging: StagingNote | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize all 16 envelope fields, always present (spec 05 §3.4)."""
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
            "tier": self.tier,
            "path": list(self.path),
            "span": self.span.to_dict() if self.span is not None else None,
            "expected": self.expected.to_dict() if self.expected is not None else None,
            "actual": self.actual.to_dict() if self.actual is not None else None,
            "provenance": [h.to_dict() for h in self.provenance],
            "valid_options": [o.to_dict() for o in self.valid_options],
            "suggested_edit": (
                self.suggested_edit.to_dict() if self.suggested_edit is not None else None
            ),
            "applicability": self.applicability,
            "recoverable": self.recoverable,
            "staging": self.staging.to_dict() if self.staging is not None else None,
        }


@dataclass
class TypeFlowEntry:
    """One step's type flow record for pipeline summary."""

    step: str  # e.g. "EWMACrossover(fast=8, slow=32)"
    input_type: str  # e.g. "PriceFrame"
    output_type: str  # e.g. "SignalSeries"
    category: str  # StepCategory value

    def to_dict(self) -> dict[str, str]:
        """Convert to a JSON-serializable dict."""
        return {
            "step": self.step,
            "input_type": self.input_type,
            "output_type": self.output_type,
            "category": self.category,
        }


@dataclass
class ValidationResult:
    """Aggregated validation result with structured issues."""

    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    info: list[ValidationIssue] = field(default_factory=list)
    type_flow: list[TypeFlowEntry] = field(default_factory=list)
    slot_types: dict[str, type] = field(default_factory=dict)
    pipeline_summary: str = ""

    def all_issues(self) -> list[ValidationIssue]:
        """Errors, then warnings, then info — flat list for serialization.

        Use when emitting issues to API responses, MCP tool output, or any
        consumer that surfaces issues to the user. Including info ensures
        non-blocking advisory codes (LOCK_DRIFT, future signals) reach the
        renderer instead of being silently dropped.
        """
        return self.errors + self.warnings + self.info

    def explain(self) -> str:
        """Produce readable multi-line output of all issues."""
        lines: list[str] = []

        if self.valid:
            lines.append("Pipeline validation passed.")
        else:
            lines.append("Pipeline validation FAILED.")

        for label, issues in [
            ("ERRORS", self.errors),
            ("WARNINGS", self.warnings),
            ("INFO", self.info),
        ]:
            if issues:
                lines.append(f"\n{label}:")
                for issue in issues:
                    lines.append(f"  [{issue.code}] {issue.message} (at {issue.location})")
                    if issue.suggestion:
                        lines.append(f"    -> {issue.suggestion}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE INDEX
# ═══════════════════════════════════════════════════════════════════════════════

# Group-based index for O(1) phase ordering lookups.
# Categories within the same group share an index, so intra-group
# reordering is allowed; only cross-group backward jumps are violations.
PHASE_INDEX: dict[StepCategory, int] = {}
for _group_idx, _group_cats in enumerate(PHASE_GROUPS):
    for _cat in _group_cats:
        PHASE_INDEX[_cat] = _group_idx


#: The sanctioned universe-mask APPLIER components (rolling-universe P2 (c)).
#: ApplyUniverseMask's registered category (signal_transform) describes its
#: value effect, not its placement: Contract C (projects/rolling-universe/
#: 01-contracts.md §1) fixes its position at the TS→XS boundary of each path,
#: which is phase-VARIABLE by design (signals are computed on the full pool,
#: the mask lands wherever the first cross-sectional op needs it). Keyed by
#: name because no registration metadata isolates "mask applier" today — its
#: population_scope is per_column (it pools nothing). If a second applier
#: ever ships, prefer promoting this to registration metadata over growing
#: the list (flagged in the P2 (c) report).
UNIVERSE_MASK_APPLIERS: frozenset[str] = frozenset({"ApplyUniverseMask"})


def is_universe_mask_phase_exempt(
    name: str,
    category: "StepCategory | None",
    population_scope: dict | None,
) -> bool:
    """True for universe-mask machinery that is PHASE-TRANSPARENT (P2 (c)).

    The phase ladder models the single-path refinement DATA → UNIVERSE →
    SIGNAL → FORECAST → POSITION → OUTPUT. Universe-mask machinery breaks the
    single-path premise on purpose:

    - **Dynamic mask emitters** — ``universe_filter``-category components
      declaring ``population_universe`` scope (RollingUniverseMask,
      RollingVolumeUniverseMask, TopNAssetSelector, VolumeUniverseReducer /
      ...Any) — rank by data that needs indicators computed first, so the
      canonical rolling pattern legitimately places them after INDICATOR
      steps on a fresh ``Load(...)`` path. ``population_fixed`` universe
      filters (AssetSelect, GroupAssetFilter) have no data dependency and
      KEEP their phase position — the discriminator deliberately excludes
      them.
    - **Mask appliers** (:data:`UNIVERSE_MASK_APPLIERS`) land at the TS→XS
      boundary of each path — after SIGNAL or FORECAST steps by design.

    Their real placement law is Contract C — "the mask precedes the first
    population op on every path" — enforced by the XS_BEFORE_UNIVERSE_MASK
    taint walk, a path-sensitive rule the linear phase index cannot express.
    Exempt steps are treated exactly like SLOT_OP: never checked, never
    advancing the phase cursor. Shared by the write-time pass 7
    (``dsl/validator.py``) and the runtime Layer C validator
    (``pipeline/validator.py``) so the two surfaces cannot disagree.
    """
    if name in UNIVERSE_MASK_APPLIERS:
        return True
    return (
        category is StepCategory.UNIVERSE_FILTER
        and (population_scope or {}).get("kind") == "population_universe"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE TRANSITION GRAPH
# ═══════════════════════════════════════════════════════════════════════════════

# Reverse lookup for Annotated types: maps the Annotated type object to its
# semantic string name. Plain aliases (RawSignal = SignalSeries) are identity-
# equal at runtime, so they merge into their base type's row. Annotated types
# are distinguishable via get_origin() and get their own rows.
ANNOTATED_SEMANTIC_NAMES: dict[int, str] = {}


def _init_annotated_semantic_names() -> None:
    """Auto-discover Annotated types from pipeline_engine.types."""
    from pipeline_engine import types as t

    for name in dir(t):
        if name.startswith("_"):
            continue
        obj = getattr(t, name)
        if get_origin(obj) is Annotated:
            ANNOTATED_SEMANTIC_NAMES[id(obj)] = name


_init_annotated_semantic_names()


# String-keyed type transition graph (Decision D20).
# Keys are runtime-reconciled type names:
# - Plain aliases (RawSignal = SignalSeries) use base type name ("SignalSeries")
# - Annotated types use semantic names ("NormalizedSignal", "BinarySignal", etc.)
# - Special types: "None" for pipeline entry, "dict" for post-Parallel
TYPE_TRANSITIONS: dict[str, dict[StepCategory, list[str]]] = {
    # === Pipeline entry ===
    "None": {
        StepCategory.DATA_LOADER: ["OHLCVDict", "StreamSeries"],
    },
    # === Phase 1: DATA ===
    "OHLCVDict": {
        # Most data transforms preserve OHLCV shape, while
        # VolatilityAdjustedPriceSeries intentionally reduces close prices to
        # a signal frame for BreakoutDistance.
        StepCategory.DATA_TRANSFORM: ["OHLCVDict", "SignalSeries"],
        # ExtractSeries is a signal transform because it starts a signal branch,
        # but its truthful input is the upstream OHLCV dictionary.
        StepCategory.SIGNAL_TRANSFORM: ["SignalSeries"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries", "OHLCVDict", "BinarySignal"],
        # Indicators may emit a BinarySignal subtype (e.g. SuperTrend/Ichimoku
        # regime signals), not just a generic SignalSeries.
        StepCategory.INDICATOR: ["SignalSeries", "BinarySignal"],
        # ConstantForecast accepts OHLCVDict directly as an index/basket entry point.
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries", "SignalSeries"],
    },
    # === Phase 1b: STREAM DATA (funding rates, OI, premium) ===
    "StreamSeries": {
        StepCategory.DATA_TRANSFORM: ["StreamSeries", "SignalSeries"],
        StepCategory.SIGNAL_TRANSFORM: ["SignalSeries", "NormalizedSignal", "StreamSeries"],
        # Regime detectors return a market-wide ``GlobalSeries`` (1-D
        # ``pd.Series``) rather than the legacy SignalSeries (DataFrame).
        # All 7 registered RegimeDetectors honor this — verified 2026-06-11.
        StepCategory.REGIME_DETECTOR: ["GlobalSeries"],
        StepCategory.INDICATOR: ["SignalSeries"],
    },
    # === Phase 3: SIGNAL (within-branch transitions) ===
    "SignalSeries": {
        StepCategory.DATA_TRANSFORM: ["SignalSeries"],
        # BreakoutDistance consumes the signal frame emitted by
        # VolatilityAdjustedPriceSeries while retaining indicator semantics.
        StepCategory.INDICATOR: ["SignalSeries"],
        StepCategory.SIGNAL_TRANSFORM: [
            "NormalizedSignal",
            "BinarySignal",
            "RankSignal",
            "SignalSeries",
        ],
        # See the "StreamSeries" → REGIME_DETECTOR note above — regime
        # detectors collapse cross-sectional input into a market-wide
        # 1-D series.
        StepCategory.REGIME_DETECTOR: ["GlobalSeries"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries", "BinarySignal"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries"],
        StepCategory.REPORTER: ["SignalSeries"],
    },
    "NormalizedSignal": {
        StepCategory.SIGNAL_TRANSFORM: [
            "NormalizedSignal",
            "BinarySignal",
            "RankSignal",
            "SignalSeries",
        ],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
    },
    "BinarySignal": {
        StepCategory.SIGNAL_TRANSFORM: ["BinarySignal"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries"],
    },
    "RankSignal": {
        StepCategory.SIGNAL_TRANSFORM: ["NormalizedSignal", "RankSignal", "BinarySignal"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
    },
    # === After Parallel — current is dict[str, Any] (branch results) ===
    "dict": {
        # Signal composers may emit a BinarySignal subtype (e.g. And/Or/Mask
        # combiners), not just a generic SignalSeries.
        StepCategory.SIGNAL_COMPOSER: ["SignalSeries", "BinarySignal"],
        StepCategory.FORECAST_COMPOSER: ["ForecastSeries", "WeightSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries"],
    },
    # === Phase 4: FORECAST ===
    "ForecastSeries": {
        StepCategory.SIGNAL_TRANSFORM: ["ForecastSeries", "SignalSeries"],
        StepCategory.FORECAST_COMPOSER: ["ForecastSeries"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.REPORTER: ["ForecastSeries"],
    },
    # === Phase 5: PORTFOLIO ===
    "WeightSeries": {
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.RISK_MANAGER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["WeightSeries"],
        StepCategory.EXECUTOR: ["OrderSeries"],
    },
    # === Phase 6-7: EXECUTION & REPORTING ===
    "OrderSeries": {
        StepCategory.REPORTER: ["OrderSeries"],
    },
    # === Market-wide single time series (regime labels, factor exposures) ===
    # Used as the canonical output type for RegimeDetector (via the
    # RegimeLabel alias). A GlobalSeries is consumed by slot reads on
    # RegimeLeverageScaler / MarketLeverageScaler / MarketRiskScaler /
    # RegimeGate, so the chain transitions are intentionally small —
    # most regime detectors end a branch and Store the result for
    # downstream slot reads. Reporters can still emit them for
    # visualization.
    "GlobalSeries": {
        StepCategory.REPORTER: ["GlobalSeries"],
    },
}


def validate_transition_coverage() -> None:
    """Verify TYPE_TRANSITIONS covers all pipeline categories (ARCH-003).

    Raises RuntimeError if any StepCategory is missing from all transition
    paths. Called at module initialization.
    """
    covered_categories: set[StepCategory] = set()
    for transitions in TYPE_TRANSITIONS.values():
        for cat in transitions:
            covered_categories.add(cat)

    all_categories = set(StepCategory) - {StepCategory.SLOT_OP}
    uncovered = all_categories - covered_categories
    if uncovered:
        raise RuntimeError(
            f"TYPE_TRANSITIONS does not cover categories: "
            f"{', '.join(c.value for c in sorted(uncovered, key=lambda c: c.value))}. "
            f"All non-SLOT_OP categories must appear in at least one transition path."
        )


validate_transition_coverage()


def type_to_transition_key(t: type) -> str | None:
    """Map a runtime type to its string key in TYPE_TRANSITIONS.

    Resolution order:
    1. Annotated types -> ANNOTATED_SEMANTIC_NAMES reverse lookup
    2. type(None) -> "None"
    3. dict -> "dict"
    4. NewType -> __qualname__ (e.g., "PriceFrame", "SignalSeries")
    5. Regular type -> __name__

    Returns None if the type has no entry in TYPE_TRANSITIONS.
    """
    # 1. Annotated types: use semantic name
    if get_origin(t) is Annotated:
        name = ANNOTATED_SEMANTIC_NAMES.get(id(t))
        if name and name in TYPE_TRANSITIONS:
            return name
        # Unknown Annotated: unwrap and try base
        args = get_args(t)
        if args:
            return type_to_transition_key(args[0])
        return None

    # 2. None type
    if t is type(None):
        return "None"

    # 3. dict type
    if t is dict:
        return "dict"

    # 4. NewType (has __qualname__ like "PriceFrame")
    if hasattr(t, "__supertype__"):
        name = getattr(t, "__qualname__", str(t))
        if name in TYPE_TRANSITIONS:
            return name
        return None

    # 5. Regular class
    name = getattr(t, "__name__", str(t))
    if name in TYPE_TRANSITIONS:
        return name
    return None


def type_name(t: type) -> str:
    """Human-readable type name for error messages."""
    if t is type(None):
        return "None"
    if t is Any:
        return "Any"
    # Annotated types: use semantic name if available
    if get_origin(t) is Annotated:
        name = ANNOTATED_SEMANTIC_NAMES.get(id(t))
        if name:
            return name
    # Union types: render as "A | B | C" rather than a bare "Union"
    origin = get_origin(t)
    if origin is Union or (origin is not None and origin.__class__.__name__ == "UnionType"):
        args = get_args(t)
        return " | ".join(type_name(a) for a in args)
    if hasattr(t, "__name__"):
        return t.__name__
    # NewType objects have __qualname__ or we can use str
    if hasattr(t, "__qualname__"):
        return t.__qualname__
    return str(t)


def _param_target_types(pinfo) -> tuple[type, ...]:
    """Unwrap Optional/Union on a parameter type, returning the non-None members.

    For `float | None` → `(float,)`. For `int | float` → `(int, float)`. For a
    bare `int` → `(int,)`. Used by both the validator (acceptance check) and
    the emitter (canonical numeric rendering).
    """
    import types as _types

    t = pinfo.type_
    origin = get_origin(t)
    if origin is Union or isinstance(t, _types.UnionType):
        return tuple(a for a in get_args(t) if a is not type(None))
    return (t,)


def param_display_type(pinfo) -> str:
    """Frontend/agent-facing type label.

    Single source of truth for the parameter "type" string shown in tool
    results, validator errors, and frontend registry metadata. Strips
    Optional (Union[T, None] → T) so the surface vocabulary stays small:
    "int", "float", "str", "bool", "enum", or a component type name.
    Optional vs required is communicated via the separate ``required``
    field, not the type string.
    """
    t = pinfo.type_
    if get_origin(t) is Literal:
        return "enum"
    targets = _param_target_types(pinfo)
    if len(targets) == 1:
        t = targets[0]
    if get_origin(t) is Literal:
        # Optional[Literal[...]] — a None-sentinel enum param (e.g.
        # SignalResampler v2's target_timeframe): still "enum"; None marks
        # omission and rides the separate `required`/`default` fields.
        return "enum"
    return getattr(t, "__name__", str(t))


def param_accepts_numeric(pinfo, value: object) -> bool:
    """Whether ``value`` is numerically acceptable for ``pinfo``.

    Mirrors Python's runtime laxness: an ``int`` satisfies any param whose
    declared type set includes ``float``. Booleans are excluded (they're an
    ``int`` subclass but rarely intended as numeric). Returns False when the
    param doesn't accept any numeric type.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    targets = _param_target_types(pinfo)
    return float in targets or int in targets


__all__ = [
    "ErrorCode",
    "TypeFlowEntry",
    "ValidationIssue",
    "ValidationResult",
    "DomainRef",
    "ClockRef",
    "TypeRef",
    "Span",
    "ProvenanceHop",
    "ValidOption",
    "SuggestedEdit",
    "StagingNote",
    "TYPE_TRANSITIONS",
    "ANNOTATED_SEMANTIC_NAMES",
    "PHASE_INDEX",
    "type_to_transition_key",
    "validate_transition_coverage",
    "is_compatible",
    "type_name",
    "param_display_type",
    "param_accepts_numeric",
    "_param_target_types",
    "TIMEFRAME_MINUTES",
    "MINUTES_TO_TOKEN",
    "render_clock",
    "timeframe_to_minutes",
    "parse_bar_offset_minutes",
    "validate_resample_config",
]
