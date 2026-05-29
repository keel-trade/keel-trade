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
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pipeline_engine.base.registry import is_compatible  # noqa: F401 — re-export
from pipeline_engine.base.step import PHASE_GROUPS, StepCategory


logger = logging.getLogger(__name__)


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


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Convert to a JSON-serializable dict."""
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
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
    try:
        from pipeline_engine import types as t

        for name in dir(t):
            if name.startswith("_"):
                continue
            obj = getattr(t, name)
            if get_origin(obj) is Annotated:
                ANNOTATED_SEMANTIC_NAMES[id(obj)] = name
    except ImportError:
        # SDK mode: no types module available.
        # ANNOTATED_SEMANTIC_NAMES stays empty — the validator uses
        # string-based type names from registry.json instead.
        pass


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
        StepCategory.DATA_TRANSFORM: ["OHLCVDict"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries", "OHLCVDict"],
        StepCategory.INDICATOR: ["SignalSeries"],
        # ConstantForecast accepts OHLCVDict directly as an index/basket entry point.
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries", "SignalSeries"],
    },
    # === Phase 1b: STREAM DATA (funding rates, OI, premium) ===
    "StreamSeries": {
        StepCategory.DATA_TRANSFORM: ["StreamSeries", "SignalSeries"],
        StepCategory.SIGNAL_TRANSFORM: ["SignalSeries", "NormalizedSignal", "StreamSeries"],
        StepCategory.REGIME_DETECTOR: ["SignalSeries"],
        StepCategory.INDICATOR: ["SignalSeries"],
    },
    # === Phase 3: SIGNAL (within-branch transitions) ===
    "SignalSeries": {
        StepCategory.DATA_TRANSFORM: ["SignalSeries"],
        StepCategory.SIGNAL_TRANSFORM: [
            "NormalizedSignal",
            "BinarySignal",
            "RankSignal",
            "SignalSeries",
        ],
        StepCategory.REGIME_DETECTOR: ["SignalSeries"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries"],
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
        StepCategory.SIGNAL_COMPOSER: ["SignalSeries"],
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


__all__ = [
    "ErrorCode",
    "TypeFlowEntry",
    "ValidationIssue",
    "ValidationResult",
    "TYPE_TRANSITIONS",
    "ANNOTATED_SEMANTIC_NAMES",
    "PHASE_INDEX",
    "type_to_transition_key",
    "validate_transition_coverage",
    "is_compatible",
    "type_name",
]
