# Pipeline Phases

The pipeline engine defines 14 step categories organized into 6 ordered groups.
Steps must flow forward through these groups -- backward jumps across group
boundaries are phase ordering violations.

Within a group, categories can appear in any order.

## Phase Groups

```
Group 0 -- DATA:      DATA_LOADER, DATA_TRANSFORM
Group 1 -- UNIVERSE:  UNIVERSE_FILTER
Group 2 -- SIGNAL:    INDICATOR, SIGNAL_TRANSFORM, SIGNAL_COMPOSER, REGIME_DETECTOR
Group 3 -- FORECAST:  FORECAST_MAPPER, FORECAST_COMPOSER
Group 4 -- POSITION:  POSITION_SIZER, RISK_MANAGER, POSITION_MANAGER
Group 5 -- OUTPUT:    EXECUTOR, REPORTER
```

Special: `SLOT_OP` (Store, Load, StoreValue, Extract, Parallel) is exempt from
phase ordering and can appear anywhere.

---

## Group 0 -- DATA

### DATA_LOADER

Loads raw market data from external sources into the pipeline.

- **Input:** `None` (pipeline entry point -- ignores incoming current)
- **Output:** `OHLCVDict` or `StreamSeries`
- **Base class:** `DataLoader(DataSource[T])`

```python
PriceDataLoader(timeframe="15min", use_cache=True)
FundingDataLoader(use_cache=True)
OIDataLoader(use_cache=True)
```

Data loaders are marked `deterministic=False` since they fetch external data.
Caching is handled at the step level via `use_cache` parameters.

### DATA_TRANSFORM

Transforms price/data without changing its fundamental nature. Input and output
remain the same broad type.

- **Input:** `OHLCVDict` (or `StreamSeries`)
- **Output:** `OHLCVDict` (or `StreamSeries`, `SignalSeries`)
- **Base class:** `DataTransform(PriceTransform)`

```python
TimeframeResampler(target_timeframe="1d", source_timeframe="15min")
SignalResampleTransform(target_timeframe="1d", method="mean")
AssetAligner(reference_slot="ohlcv_1d")
```

---

## Group 1 -- UNIVERSE

### UNIVERSE_FILTER

Filters the asset universe based on criteria like volume, liquidity, or
market cap. Reduces the set of instruments flowing through the pipeline.

- **Input:** `OHLCVDict` or `SignalSeries`
- **Output:** `OHLCVDict` or `SignalSeries` (same type, fewer columns)
- **Base class:** `UniverseFilter`

```python
VolumeUniverseReducer(top_n=50, lookback_bars=60, volume_column="volume")
```

Note: UniverseFilter is a non-generic base class. Subclasses declare their
actual input/output types via their `run()` method signatures.

---

## Group 2 -- SIGNAL

### INDICATOR

Computes raw signals from OHLCV data. This is the primary signal generation
step -- technical indicators, statistical measures, etc.

- **Input:** `OHLCVDict`
- **Output:** `SignalSeries`
- **Base class:** `Indicator(SignalTransform[OHLCVDict, SignalSeries])`

```python
EWMA(window=8, min_periods=8)
ROC(period=10)
BreakoutDistance(lookback=160, exclude_current=True)
EWMACrossover(fast=8, slow=32)
```

### SIGNAL_TRANSFORM

Transforms signals without changing from signal domain to forecast domain.
Normalization, smoothing, cross-sectional operations.

- **Input:** `SignalSeries` (or subtypes: `NormalizedSignal`, `BinarySignal`, `RankSignal`)
- **Output:** `SignalSeries`, `NormalizedSignal`, `BinarySignal`, `RankSignal`
- **Base class:** `SignalTransform[In, Out]`

```python
CrossSectionalZScore()
EWMATransform(window=7)
NegateTransform()
VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d")
ThresholdTransform(upper=1.0, lower=-1.0)
```

### SIGNAL_COMPOSER

Joins parallel signal branches back into a single signal. Receives `dict`
from a `Parallel` step and reduces to `SignalSeries`.

- **Input:** `dict` (from Parallel)
- **Output:** `SignalSeries`
- **Base class:** `SignalComposer(Composer[SignalSeries])`

```python
EqualWeightCombiner()    # Average all branches
WeightedCombiner(weights={"trend": 0.6, "mean_rev": 0.4})
```

### REGIME_DETECTOR

Classifies market state (bull/bear/neutral). Output is `RegimeLabel`
(alias for `SignalSeries` with integer labels).

- **Input:** `SignalSeries` or `StreamSeries`
- **Output:** `RegimeLabel` (SignalSeries)
- **Base class:** `RegimeDetector(SignalTransform[SignalSeries, RegimeLabel])`

```python
FundingLevelRegimeDetector(thresholds=[-0.01, 0.01])
RealizedVolRegimeDetector(window="30d", percentile_threshold=0.7)
```

Regime detectors are semantically distinct from signal transforms even though
their type signature overlaps. They require the declared `category` attribute
for correct classification -- the ontology decision tree cannot distinguish
them from SIGNAL_TRANSFORM by types alone.

---

## Group 3 -- FORECAST

### FORECAST_MAPPER

Converts a signal into a standardized forecast in the [-20, +20] range.
This is where raw signals become comparable across different strategies.

- **Input:** `SignalSeries` or `NormalizedSignal`
- **Output:** `ForecastSeries` (Annotated SignalSeries with Bounds(-20, 20))
- **Base class:** `ForecastMapper(ForecastTransform)`

```python
ForecastScaler(avg_abs_target=10.0, pool="global", method="mean")
ForecastCapper(limit=20.0)
```

### FORECAST_COMPOSER

Joins parallel forecast branches into a single blended forecast. Receives
`dict` from Parallel and reduces to `ForecastSeries`.

- **Input:** `dict` (from Parallel)
- **Output:** `ForecastSeries`
- **Base class:** `Composer[ForecastSeries]`

```python
ForecastCombiner(weights={"trend": 0.57, "carry": 0.19, "mean_rev": 0.24})
RegimeWeightedCombiner(regime_slot="regime", weights_map={0: {...}, 1: {...}})
```

Composer key validation: if the composer declares `weights` or `expected_keys`,
the validator checks these match the preceding Parallel's branch names.

---

## Group 4 -- POSITION

### POSITION_SIZER

Converts forecasts (or other inputs) to portfolio weights. This is the
bridge from signal space to portfolio space.

- **Input:** `ForecastSeries`, `SignalSeries`, `OHLCVDict`, or `dict`
- **Output:** `WeightSeries`
- **Base class:** `PositionSizer`

```python
ReturnVolatility(window="36d")
VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)
IDMPortfolioAggregator(forecast_slot="forecast_combined", return_vol_slot="return_vol")
```

PositionSizer is a non-generic base class because subclasses accept various
input types. The registry extracts actual types from `run()` method hints.

### RISK_MANAGER

Applies risk constraints to portfolio weights. Caps exposure, enforces
position limits, cost budgets.

- **Input:** `WeightSeries`
- **Output:** `WeightSeries`
- **Base class:** `RiskManager(SignalTransform[WeightSeries, WeightSeries])`

```python
MaxPositionCap(max_weight=0.15)
GrossLeverageCap(max_leverage=5.0)
```

### POSITION_MANAGER

Signal cleaning, inertia, entry/exit management. Operates on weights but
focuses on trading behavior rather than risk limits.

- **Input:** `WeightSeries`
- **Output:** `WeightSeries`
- **Base class:** `PositionManager(SignalTransform[WeightSeries, WeightSeries])`

```python
PositionInertia(threshold=0.30, mode="relative", rebalance_method="to_edge")
DenseToSparseConverter(tolerance=0.00000001)
```

---

## Group 5 -- OUTPUT

### EXECUTOR

Converts portfolio weights to executable orders.

- **Input:** `WeightSeries`
- **Output:** `OrderSeries`
- **Base class:** `Executor(ExecutionStep)`

```python
SimpleExecutor()
HyperliquidExecutor(signing_service_url="...")
```

### REPORTER

Observes, logs, and audits pipeline state. Side-effect only -- passes data
through unchanged. Can appear after signals, forecasts, or orders.

- **Input:** varies (any type)
- **Output:** same as input (passthrough)
- **Base class:** `Reporter(SignalTransform[Any, Any])`

```python
MetricsReporter()
SignalLogger(slot="forecast_combined")
```

---

## Phase Ordering Rules

1. Steps must flow forward through groups 0-5
2. Within a group, categories can appear in any order
3. SLOT_OP steps are exempt -- allowed anywhere
4. Nested Pipelines have their own independent phase scope
5. Parallel branches inherit the parent's current phase index
6. Phase violations are `error` in STRICT mode, `warning` in RELAXED mode

Example of a valid ordering:
```
DATA_LOADER -> DATA_TRANSFORM -> UNIVERSE_FILTER -> INDICATOR ->
SIGNAL_TRANSFORM -> FORECAST_MAPPER -> POSITION_SIZER ->
RISK_MANAGER -> POSITION_MANAGER -> EXECUTOR
```

Example of a violation:
```
DATA_LOADER -> INDICATOR -> DATA_TRANSFORM  # ERROR: DATA_TRANSFORM (group 0)
                                            # after INDICATOR (group 2)
```

## Validation

Phase ordering is validated by `PipelineValidator.validate_phase_ordering()`.
The validator uses `PHASE_INDEX` (a dict mapping each category to its group
number) for O(1) comparison. The `PhaseOrderMode` enum controls severity:

- `STRICT` (default): backward jumps are errors
- `RELAXED`: backward jumps are warnings

In BACKTEST mode, validation is skipped entirely for performance (unless
`force=True` is passed).
