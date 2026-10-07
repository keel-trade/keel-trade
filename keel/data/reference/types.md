# Pipeline Type System

The pipeline engine uses semantic NewType wrappers around pandas DataFrames to
provide type-safe data flow. Types carry meaning -- `SignalSeries` is not
interchangeable with `WeightSeries` even though both are DataFrames at runtime.

## Base Domain Types

### None

Pipeline entry point. Data loaders accept `None` as input (they ignore
incoming current and fetch fresh data).

```text
PriceDataLoader: None -> OHLCVDict        # a loader starts a pipeline or a branch
```

### OHLCVDict

```text
OHLCVDict = NewType("OHLCVDict", dict)
```

Per-symbol OHLCV data. Keys are symbol strings, values are OHLCV DataFrames
with columns: open, high, low, close, volume. Index: datetime.

This is the native form returned by `PriceDataLoader` and preserved through
DATA_TRANSFORM and UNIVERSE_FILTER phases.

### StreamSeries

```text
StreamSeries = NewType("StreamSeries", SignalSeries)
```

Raw stream market data (funding rates, open interest, premium). Asset columns
by timestamp rows. Subtype of `SignalSeries` -- compatible with steps that
accept `SignalSeries` input.

### SignalSeries

```text
SignalSeries = NewType("SignalSeries", pd.DataFrame)
```

Raw indicator output. Any numeric values. Columns are typically asset names,
index is datetime. This is the workhorse type for the SIGNAL phase group.

### ForecastSeries

```text
ForecastSeries = Annotated[SignalSeries, Bounds(-20, 20)]
```

Standardized forecast. Expected range [-20, +20]. Uses PEP 593 `Annotated`
with `Bounds` constraint. Transparent subtype of `SignalSeries` --
`is_compatible(ForecastSeries, SignalSeries)` returns True.

### WeightSeries

```text
WeightSeries = NewType("WeightSeries", pd.DataFrame)
```

Portfolio weights. Columns are asset names, values represent target allocation.
Typically sum to 1.0 or a target leverage value.

### OrderSeries

```text
OrderSeries = NewType("OrderSeries", pd.DataFrame)
```

Order instructions. Contains columns for symbol, side, quantity, etc.
The final output of a complete pipeline.

### dict

Not a domain type per se -- this is the output of a `Parallel` step. Branch
results are collected into `dict[str, Any]` keyed by branch name. Must be
consumed by a Composer, Extract, or Load step.

---

## Semantic Type Aliases

These are zero-cost aliases that carry intent. Some use `Annotated` with
value constraints.

### NormalizedSignal

```text
NormalizedSignal = Annotated[SignalSeries, Bounds(-1, 1)]
```

Signal normalized to [-1, 1] range. Output of cross-sectional normalization
steps like `CrossSectionalZScore`.

### BinarySignal

```text
BinarySignal = Annotated[SignalSeries, DiscreteValues(frozenset({-1.0, 0.0, 1.0}))]
```

Signal with only -1, 0, +1 values (short/flat/long). Output of threshold
transforms.

### Position

A `TradeManager`'s trades: when each one opened, what it holds, and what its
rules did. It flows only below `TradeManager`. A reader (`TradeReturn()`,
`BarsHeld()`, `AtEntry(slot=...)`, ...) turns it into an ordinary series, so
filters, combiners and ratios work on that series unchanged. Only actions
(`Exit()`, `Reduce()`, `ScaleIn()`, `AllowEntry()`) and declared consumers
(`Exposure()`, `RiskSizer`) take a Position. A continuous path never carries
one.

### RankSignal

```text
RankSignal = Annotated[SignalSeries, Bounds(0, 1)]
```

Cross-sectional rank normalized to [0, 1]. Output of ranking transforms.

### RegimeLabel

```text
RegimeLabel = SignalSeries
```

Regime classification output. Integer labels (e.g., 0=bear, 1=neutral,
2=bull). Plain alias for `SignalSeries` -- distinguished by the component's
declared category, not by type.

### Other Aliases

```text
RawSignal = SignalSeries        # Unprocessed indicator output
Forecast = ForecastSeries       # Alias for ForecastSeries
CappedForecast = ForecastSeries # Standard forecast range [-20, 20]
TargetWeights = WeightSeries    # Target portfolio weights
```

---

## Scope-Aware Types

These distinguish per-instrument data from market-wide data:

```text
InstrumentFrame = NewType("InstrumentFrame", pd.DataFrame)
# Shape: (T x N) -- N instruments, T timestamps

GlobalSeries = NewType("GlobalSeries", pd.Series)
# Shape: (T,) -- one scalar per timestamp
```

---

## Bounded Type Constraints

Types use PEP 593 `Annotated` with constraint metadata:

```text
from pipeline_engine.types import Bounds, DiscreteValues, Ge, Le

# Bounds: min/max range
ForecastSeries = Annotated[SignalSeries, Bounds(-20, 20)]

# DiscreteValues: allowed set
BinarySignal = Annotated[SignalSeries, DiscreteValues(frozenset({-1.0, 0.0, 1.0}))]

# Ge/Le: one-sided constraints
Ge(0.0)   # Greater than or equal
Le(1.0)   # Less than or equal
```

Accessing constraints at runtime:

```text
from typing import get_args, get_origin, Annotated

if get_origin(type_hint) is Annotated:
    base_type, *metadata = get_args(type_hint)
    for m in metadata:
        if isinstance(m, Bounds):
            print(f"Range: [{m.min_val}, {m.max_val}]")
```

---

## Type Transition Graph

The `TYPE_TRANSITIONS` dict defines which step categories are valid
successors for each output type. This is the semantic validation layer
on top of structural `is_compatible()` type checking.

### Entry

```text
None -> DATA_LOADER -> [OHLCVDict, StreamSeries]
```

### From OHLCVDict

```text
OHLCVDict -> DATA_TRANSFORM   -> [OHLCVDict]
OHLCVDict -> UNIVERSE_FILTER  -> [SignalSeries, OHLCVDict]
OHLCVDict -> INDICATOR        -> [SignalSeries]
OHLCVDict -> POSITION_SIZER   -> [WeightSeries, SignalSeries]
```

### From StreamSeries

```text
StreamSeries -> DATA_TRANSFORM    -> [StreamSeries, SignalSeries]
StreamSeries -> SIGNAL_TRANSFORM  -> [SignalSeries, NormalizedSignal, StreamSeries, BinarySignal, RankSignal]
StreamSeries -> REGIME_DETECTOR   -> [SignalSeries]
StreamSeries -> INDICATOR         -> [SignalSeries]
```

### From SignalSeries

```text
SignalSeries -> DATA_TRANSFORM    -> [SignalSeries]
SignalSeries -> SIGNAL_TRANSFORM  -> [NormalizedSignal, BinarySignal, RankSignal, SignalSeries]
SignalSeries -> REGIME_DETECTOR   -> [SignalSeries]
SignalSeries -> FORECAST_MAPPER   -> [ForecastSeries]
SignalSeries -> UNIVERSE_FILTER   -> [SignalSeries]
SignalSeries -> POSITION_SIZER    -> [WeightSeries]
SignalSeries -> REPORTER          -> [SignalSeries]
```

### From NormalizedSignal

```text
NormalizedSignal -> SIGNAL_TRANSFORM -> [NormalizedSignal, BinarySignal, RankSignal]
NormalizedSignal -> FORECAST_MAPPER  -> [ForecastSeries]
```

### From BinarySignal

```text
BinarySignal -> SIGNAL_TRANSFORM -> [BinarySignal]
BinarySignal -> FORECAST_MAPPER  -> [ForecastSeries]
BinarySignal -> POSITION_SIZER   -> [WeightSeries]
```

### From RankSignal

```text
RankSignal -> SIGNAL_TRANSFORM -> [NormalizedSignal, RankSignal]
RankSignal -> FORECAST_MAPPER  -> [ForecastSeries]
```

### From dict (after Parallel)

```text
dict -> SIGNAL_COMPOSER   -> [SignalSeries]
dict -> FORECAST_COMPOSER -> [ForecastSeries]
dict -> POSITION_SIZER    -> [WeightSeries]
```

### From ForecastSeries

```text
ForecastSeries -> SIGNAL_TRANSFORM  -> [ForecastSeries, SignalSeries]
ForecastSeries -> FORECAST_COMPOSER -> [ForecastSeries]
ForecastSeries -> FORECAST_MAPPER   -> [ForecastSeries]
ForecastSeries -> POSITION_SIZER    -> [WeightSeries]
ForecastSeries -> REPORTER          -> [ForecastSeries]
```

### From WeightSeries

```text
WeightSeries -> POSITION_SIZER   -> [WeightSeries]
WeightSeries -> RISK_MANAGER     -> [WeightSeries]
WeightSeries -> POSITION_MANAGER -> [WeightSeries]
WeightSeries -> EXECUTOR         -> [OrderSeries]
```

### From Position

```text
Position -> POSITION_MANAGER -> [Position, SignalSeries, BinarySignal]
Position -> POSITION_SIZER   -> [WeightSeries]
```

### From OrderSeries

```text
OrderSeries -> REPORTER -> [OrderSeries]
```

---

## Type Compatibility

The engine uses `is_compatible(source_type, target_type)` for structural
type checking. Key behaviors:

- Identity: `is_compatible(X, X)` is always True
- NewType subtypes: `is_compatible(StreamSeries, SignalSeries)` is True
  (StreamSeries is a NewType of SignalSeries)
- Annotated types: `is_compatible(ForecastSeries, SignalSeries)` is True
  (ForecastSeries is Annotated[SignalSeries, ...])
- Incompatible NewTypes: `is_compatible(WeightSeries, SignalSeries)` is False
- None acceptance: `type(None)` only matches `type(None)` or `Any`
- Any: `is_compatible(Any, X)` and `is_compatible(X, Any)` are both True

The type system has two validation layers:

1. **Structural** (`is_compatible`): Does the data physically fit?
2. **Semantic** (`TYPE_TRANSITIONS`): Does the transition make domain sense?

Both are checked during pipeline validation. Structural mismatches produce
errors; semantic mismatches produce warnings.
