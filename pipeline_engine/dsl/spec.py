"""DSL spec data structures — the contract between parser, validator, and resolver.

All dataclasses in this module represent the parsed AST of a .strategy file.
They are pure data containers with no methods or validation logic.

Quick Start:
    >>> from pipeline_engine.dsl.spec import StrategyFile, ComponentRef
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline_engine.constants import MISSING  # noqa: F401 — re-export


@dataclass
class SourceLocation:
    """Source position for error messages.

    The context field uses a grammar:
    "step[N]", "branch[name].step[N]", "var[name]", "store[name]",
    "load[name]", "factory[name]", "factory_call[name]", "pipeline"
    """

    line: int
    col: int
    context: str


@dataclass
class VariableRef:
    """Reference to a previously defined variable."""

    name: str
    location: SourceLocation


@dataclass
class ComponentRef:
    """A reference to a component by name with parameters."""

    name: str
    params: dict[str, Any | VariableRef]
    location: SourceLocation


@dataclass
class SlotStoreSpec:
    """Store current pipeline data to a named slot (DSL syntax, not a component)."""

    slot_name: str
    location: SourceLocation


@dataclass
class SlotStoreValueSpec:
    """Store a fixed literal value into a named slot (DSL syntax)."""

    slot_name: str
    value: Any
    location: SourceLocation


@dataclass
class SlotLoadSpec:
    """Load data from a named slot into pipeline flow (DSL syntax, not a component)."""

    slot_name: str
    location: SourceLocation


@dataclass
class SlotExtractSpec:
    """Extract a single value from a dict (after Parallel). DSL syntax: Extract('key')."""

    key: str
    location: SourceLocation


@dataclass
class ParallelSpec:
    """Parallel branch specification (dict literal in DSL)."""

    branches: dict[str, list[StepSpec]]
    location: SourceLocation


@dataclass
class PipelineSpec:
    """Parsed pipeline specification."""

    steps: list[StepSpec]
    name: str | None
    location: SourceLocation


@dataclass
class FactoryParam:
    """A parameter in a factory definition."""

    name: str
    default: Any = field(default=MISSING)
    annotation: str | None = None


@dataclass
class FactoryDef:
    """A parameterized sub-pipeline template."""

    name: str
    params: list[FactoryParam]
    body: PipelineSpec
    location: SourceLocation


@dataclass
class FactoryCallSpec:
    """A call to a DSL-defined factory."""

    name: str
    args: dict[str, Any | VariableRef]
    location: SourceLocation


@dataclass
class VariableAssignment:
    """A variable definition: name = Pipeline(...) or name = literal."""

    name: str
    value: PipelineSpec | Any
    location: SourceLocation


@dataclass
class GlobalsSpec:
    """Top-level Globals declaration — pipeline-wide configuration."""

    target_timeframe: str | None = None
    bar_offset: str | None = None
    location: SourceLocation | None = None


@dataclass
class UniverseSpec:
    """Top-level Universe declaration — asset selection criteria + committed list."""

    mode: str = "manual"
    market: str = "perp"
    symbols: list[str] | None = None
    categories: list[str] | None = None
    top_n: int | None = None
    exclusions: list[str] | None = None
    inclusions: list[str] | None = None
    lookback: str | None = None
    volume_quartiles: list[str] | None = None
    resolved: list[str] | None = None
    resolved_at: str | None = None
    groups: dict[str, list[str]] | None = None
    location: SourceLocation | None = None


@dataclass
class ExecutionSpec:
    """Top-level Execution declaration — how target weights translate into trades."""

    rebalance: str = "every_bar"
    on_change_tolerance: float = 1e-8
    buffer_threshold: float | None = None
    buffer_mode: str = "relative"
    rebalance_method: str = "to_center"
    min_trade_size: float = 0.0
    location: SourceLocation | None = None


# ── Canonical metadata for Execution params ──────────────────────────────
# Single source of truth. Emitters, validators, UI, and consumers all
# derive their behavior from this registry. To add a param: add the field
# to ExecutionSpec above, then add an entry here. Everything else adapts.

EXECUTION_PARAM_META: dict[str, dict] = {
    "rebalance": {
        "type": "select",
        "default": "every_bar",
        "options": ["every_bar", "on_change", "buffered"],
        "always_emit": True,
        "description": "When the engine trades. every_bar: every bar. on_change: only when weights change. buffered: only when positions drift outside a buffer band.",
    },
    "on_change_tolerance": {
        "type": "number",
        "default": 1e-8,
        "modes": ["on_change"],
        "description": "Weight changes smaller than this are ignored. Default 1e-8 filters floating-point noise.",
    },
    "buffer_threshold": {
        "type": "number",
        "default": None,
        "modes": ["buffered"],
        "required_for": ["buffered"],
        "description": "Width of the no-trade buffer band. Fraction of target (relative) or portfolio value (absolute). Typical: 0.05-0.30.",
    },
    "buffer_mode": {
        "type": "select",
        "default": "relative",
        "options": ["relative", "absolute"],
        "modes": ["buffered"],
        "description": "How the buffer band is computed. relative: fraction of target position size. absolute: fraction of portfolio value.",
    },
    "rebalance_method": {
        "type": "select",
        "default": "to_center",
        "options": ["to_edge", "to_center"],
        "modes": ["buffered"],
        "description": "When position breaches the buffer: to_edge trades minimum to nearest band edge (20-40% less turnover). to_center trades all the way to target.",
    },
    "min_trade_size": {
        "type": "number",
        "default": 0.0,
        "description": "Minimum trade size as portfolio weight fraction. Trades smaller than this are skipped. 0 = disabled.",
    },
}

# Derived constants (use these instead of hardcoding param names)
EXECUTION_PARAM_NAMES: set[str] = set(EXECUTION_PARAM_META.keys())
EXECUTION_VALID_REBALANCE: set[str] = set(EXECUTION_PARAM_META["rebalance"]["options"])
EXECUTION_VALID_BUFFER_MODE: set[str] = set(EXECUTION_PARAM_META["buffer_mode"]["options"])
EXECUTION_VALID_REBALANCE_METHOD: set[str] = set(
    EXECUTION_PARAM_META["rebalance_method"]["options"]
)


@dataclass
class StrategyFile:
    """Top-level parsed strategy file."""

    metadata: dict[str, str]
    factories: list[FactoryDef]
    variables: list[VariableAssignment]
    pipeline: PipelineSpec
    globals_: GlobalsSpec | None = None
    universe: UniverseSpec | None = None
    execution: ExecutionSpec | None = None


# Union type for steps in a pipeline
StepSpec = (
    ComponentRef
    | ParallelSpec
    | PipelineSpec
    | VariableRef
    | SlotStoreSpec
    | SlotStoreValueSpec
    | SlotLoadSpec
    | SlotExtractSpec
    | FactoryCallSpec
)


__all__ = [
    "MISSING",
    "ComponentRef",
    "ExecutionSpec",
    "FactoryCallSpec",
    "FactoryDef",
    "FactoryParam",
    "GlobalsSpec",
    "ParallelSpec",
    "PipelineSpec",
    "SlotLoadSpec",
    "SlotExtractSpec",
    "SlotStoreSpec",
    "SlotStoreValueSpec",
    "SourceLocation",
    "StepSpec",
    "StrategyFile",
    "UniverseSpec",
    "VariableAssignment",
    "VariableRef",
]
