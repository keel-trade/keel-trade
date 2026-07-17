"""Domain types for the pipeline engine.

Provides semantic NewType wrappers around pd.DataFrame for type-safe pipeline
data flow and bounded type annotations using PEP 593.

Quick Start:
    >>> from pipeline_engine.types import PriceFrame, SignalSeries
    >>> import pandas as pd
    >>> df = pd.DataFrame({"close": [1.0, 2.0]})
    >>> pf = PriceFrame(df)  # Transparent at runtime, typed for mypy

Scope types distinguish instrument-level (T×N DataFrame) from global
(T-length Series) data, preventing silent shape mismatches:

    >>> from pipeline_engine.types import InstrumentFrame, GlobalSeries
    >>> signal = InstrumentFrame(df)   # per-asset values
    >>> regime = GlobalSeries(series)  # single market-wide value
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, NewType


# SDK-bundled stub — full file imports pandas; SDK strips it to keep
# the wheel lightweight. NewType chain is preserved (used by validator);
# the underlying pd.DataFrame/pd.Series typing is collapsed to `object`,
# which is fine since the SDK never executes pipeline_engine.runtime code.
class _PdStub:
    DataFrame = object
    Series = object


pd = _PdStub()


# ═══════════════════════════════════════════════════════════════════════════════
# BOUNDED TYPE CONSTRAINTS (must precede base types that use them)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Ge:
    """Greater than or equal constraint for Annotated types."""

    value: float


@dataclass(frozen=True)
class Le:
    """Less than or equal constraint for Annotated types."""

    value: float


@dataclass(frozen=True)
class Bounds:
    """Combined min/max bounds constraint for Annotated types.

    Used for rich typing where value constraints are part of the type.
    This is the Pythonic pattern (PEP 593) -- works with Pydantic, beartype,
    and custom validators.

    Example::

        ForecastSeries = Annotated[SignalSeries, Bounds(-20, 20)]
        BoundedWeight = Annotated[WeightSeries, Bounds(0, 1)]

    Accessing constraints at runtime::

        from typing import get_args, get_origin
        if get_origin(type_hint) is Annotated:
            base_type, *metadata = get_args(type_hint)
            for m in metadata:
                if isinstance(m, Bounds):
                    print(f"Range: [{m.min_val}, {m.max_val}]")
    """

    min_val: float
    max_val: float


@dataclass(frozen=True)
class DiscreteValues:
    """Allowed discrete values constraint for Annotated types.

    Used for signals that must contain only specific values.
    Validated in DEBUG mode via Context._validate_type().
    """

    values: frozenset[float]


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DOMAIN TYPES
# These provide semantic meaning to DataFrames flowing through the pipeline.
# ═══════════════════════════════════════════════════════════════════════════════

OHLCVDict = NewType("OHLCVDict", dict)
"""Per-symbol OHLCV data. Keys: symbol strings, values: OHLCV DataFrames.
This is the native form returned by PriceDataLoader and preserved through
DATA_TRANSFORM and UNIVERSE_FILTER phases."""

PriceFrame = NewType("PriceFrame", pd.DataFrame)
"""OHLCV price data. Columns: open, high, low, close, volume. Index: datetime.
Also used as slot value type for stored OHLCV data references."""

SignalSeries = NewType("SignalSeries", pd.DataFrame)
"""Raw indicator output. Any numeric values. Index: datetime."""

StreamSeries = NewType("StreamSeries", SignalSeries)
"""Raw stream market data (funding rates, open interest, premium).
Asset columns x timestamp rows. Subtype of SignalSeries — compatible
with steps that accept SignalSeries input."""

ForecastSeries = Annotated[SignalSeries, Bounds(-20, 20)]
"""Standardized forecast. Expected range: [-20, 20]. Index: datetime.
Transparent subtype of SignalSeries — is_compatible(ForecastSeries, SignalSeries) is True."""

WeightSeries = NewType("WeightSeries", pd.DataFrame)
"""Portfolio weights. Sum to 1.0 (or target leverage). Index: datetime."""

OrderSeries = NewType("OrderSeries", pd.DataFrame)
"""Order instructions. Columns: symbol, side, quantity, etc. Index: datetime."""

FundingFrame = NewType("FundingFrame", pd.DataFrame)
"""Funding rate data. Used by carry-based strategies. Index: datetime."""

SentimentFrame = NewType("SentimentFrame", pd.DataFrame)
"""Sentiment indicator data. Index: datetime."""

TemporalOffset = NewType("TemporalOffset", str)
"""Pandas offset string for bar alignment (e.g. "12h", "19h"). Used in config slots."""


# ═══════════════════════════════════════════════════════════════════════════════
# SCOPE-AWARE TYPES
# These distinguish instrument-level data (T×N DataFrame) from global/universe-
# level data (T-length Series). The type itself carries scope information,
# so Slot.value_type is sufficient for runtime dispatch.
# ═══════════════════════════════════════════════════════════════════════════════

InstrumentFrame = NewType("InstrumentFrame", pd.DataFrame)
"""Per-instrument values. Columns = instruments, Index = timestamps.
Shape: (T × N) where N = number of instruments."""

GlobalSeries = NewType("GlobalSeries", pd.Series)
"""Market-wide single value per timestamp. Index = timestamps.
Shape: (T,) — one scalar per time step."""


# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC TYPE ALIASES
# Zero-cost aliases that carry intent. Tightened with Annotated constraints
# where value ranges are well-defined.
# ═══════════════════════════════════════════════════════════════════════════════

RawSignal = SignalSeries
"""Unprocessed indicator output. Any numeric values."""

NormalizedSignal = Annotated[SignalSeries, Bounds(-1, 1)]
"""Signal normalized to [-1, 1] range."""

BinarySignal = Annotated[SignalSeries, DiscreteValues(frozenset({-1.0, 0.0, 1.0}))]
"""Signal with only -1, 0, +1 values (short/flat/long)."""

RankSignal = Annotated[SignalSeries, Bounds(0, 1)]
"""Cross-sectional rank normalized to [0, 1]."""

PositionLevel = SignalSeries
"""Integer position levels {-N, ..., -1, 0, 1, ..., N}. Sign = direction, magnitude = level count.
Output of ScalingPositionManager for pyramiding / DCA / laddered entries."""

RegimeLabel = GlobalSeries
"""Regime classification output as a 1-D market-wide time series.

Values typically in {+1.0 bullish, 0.0 neutral, -1.0 bearish} but may be
any float per the producing detector. Index: datetime. Single series —
applies to all assets uniformly (consumers like RegimeLeverageScaler and
RegimeGate broadcast across asset columns at the application site).

Previously aliased to SignalSeries (DataFrame), but every registered
RegimeDetector subclass actually returns ``pd.Series`` (verified across
all 7 implementations on 2026-06-11). The annotation drift forced
``RegimeGate`` to false-reject Series inputs and gave the validator a
warped picture of the data flow — repointing to GlobalSeries restores
honesty across the type-flow, slot-compatibility, and consumer-broadcast
boundaries."""

Forecast = ForecastSeries
"""Standardized forecast (alias for ForecastSeries)."""

CappedForecast = ForecastSeries
"""Standard forecast range [-20, 20]. Identical to ForecastSeries."""

TargetWeights = WeightSeries
"""Target portfolio weights (alias for WeightSeries)."""


__all__ = [
    # Bounded type building blocks
    "Ge",
    "Le",
    "Bounds",
    "DiscreteValues",
    # Base domain types
    "OHLCVDict",
    "PriceFrame",
    "SignalSeries",
    "ForecastSeries",
    "WeightSeries",
    "OrderSeries",
    "StreamSeries",
    "FundingFrame",
    "SentimentFrame",
    "TemporalOffset",
    # Scope-aware types
    "InstrumentFrame",
    "GlobalSeries",
    # Semantic type aliases
    "RawSignal",
    "NormalizedSignal",
    "BinarySignal",
    "RankSignal",
    "PositionLevel",
    "RegimeLabel",
    "Forecast",
    "CappedForecast",
    "TargetWeights",
]
