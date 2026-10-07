# Pipeline Phases

The pipeline engine defines 14 step categories organized into 6 ordered groups.
Steps must flow forward through these groups -- backward jumps across group
boundaries are phase ordering violations.

Within a group, categories can appear in any order.

## Phase Groups

```text
Group 0 -- DATA:      DATA_LOADER, DATA_TRANSFORM
Group 1 -- UNIVERSE:  UNIVERSE_FILTER
Group 2 -- SIGNAL:    INDICATOR, SIGNAL_TRANSFORM, SIGNAL_COMPOSER, REGIME_DETECTOR
Group 3 -- FORECAST:  FORECAST_MAPPER, FORECAST_COMPOSER
Group 4 -- POSITION:  POSITION_SIZER, RISK_MANAGER, POSITION_MANAGER
Group 5 -- OUTPUT:    EXECUTOR, REPORTER
```

Special: `SLOT_OP` (Store, Load, StoreValue, Extract) and the Parallel dict
step are exempt from phase ordering and can appear anywhere.

---

## Group 0 -- DATA

### DATA_LOADER

Loads raw market data from external sources into the pipeline.

- **Input:** `None` (pipeline entry point -- ignores incoming current)
- **Output:** `OHLCVDict` or `StreamSeries`
- **Base class:** `DataLoader(DataSource[T])`

```python fragment
PriceDataLoader()                    # serves Globals.target_timeframe on the bar_offset grid
FundingDataLoader()                  # same grain from its 1h native series (agg default: mean)
OpenInterestLoader()                 # same (agg default: last)
PriceDataLoader(timeframe="15min")   # a branch on its own clock — the only override
```

Data loaders are marked `deterministic=False` since they fetch external data.
Caching is handled at the step level via `use_cache` parameters.

### DATA_TRANSFORM

Transforms price/data without changing its fundamental nature. Input and output
remain the same broad type.

- **Input:** `OHLCVDict` (or `StreamSeries`)
- **Output:** `OHLCVDict` (or `StreamSeries`, `SignalSeries`)
- **Base class:** `DataTransform(PriceTransform)`

```python fragment
HeikinAshi()                    # OHLCV -> Heikin-Ashi OHLCV
VolatilityAdjustedPriceSeries() # OHLCV -> vol-adjusted price series
```

The signal re-clockers — `TargetSignalResampler(method=...)` (fine → coarse,
to Globals) and `TargetSignalProjector()` (coarse → fine) — are the same
DATA-group bridge for a computed branch on its own clock.

---

## Group 1 -- UNIVERSE

### UNIVERSE_FILTER

Filters the asset universe based on criteria like volume, liquidity, or
market cap. Reduces the set of instruments flowing through the pipeline.

- **Input:** `OHLCVDict` or `SignalSeries`
- **Output:** `OHLCVDict` or `SignalSeries` (same type, fewer columns)
- **Base class:** `UniverseFilter`

```python fragment
GroupAssetFilter(group="defi")                 # a Universe group, in a branch
AssetSelect(symbols=["BTC", "ETH"])            # an inline basket
TopNAssetSelector(n=10, method="largest")      # a {0,1} selection mask (same columns)
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

```python fragment
EWMA(window=8, min_periods=8)
ROC(period=10)
RSI(period=14)
ReturnVolatility(window="36d")
```

### SIGNAL_TRANSFORM

Transforms signals without changing from signal domain to forecast domain.
Normalization, smoothing, cross-sectional operations.

- **Input:** `SignalSeries` (or subtypes: `NormalizedSignal`, `BinarySignal`, `RankSignal`)
- **Output:** `SignalSeries`, `NormalizedSignal`, `BinarySignal`, `RankSignal`
- **Base class:** `SignalTransform[In, Out]`

```python fragment
CrossSectionalZScore()
EWMATransform(window=7)
NegateTransform()
RollingZScoreTransform(window=60)
ThresholdCross(upper=1.0, lower=-1.0)
```

### SIGNAL_COMPOSER

Joins parallel signal branches back into a single signal. Receives `dict`
from a `Parallel` step and reduces to `SignalSeries`.

- **Input:** `dict` (from Parallel)
- **Output:** `SignalSeries`
- **Base class:** `SignalComposer(Composer[SignalSeries])`

```python fragment
Crossover()                                            # fast - slow
ApplyMask(score_signal="signal", filter_signal="filter")
MaskOr()                                               # any of the boolean branches
```

### REGIME_DETECTOR

Classifies market state (bull/bear/neutral). Output is `RegimeLabel`
(alias for `SignalSeries` with integer labels).

- **Input:** `SignalSeries` or `StreamSeries`
- **Output:** `RegimeLabel` (SignalSeries)
- **Base class:** `RegimeDetector(SignalTransform[SignalSeries, RegimeLabel])`

```python fragment
FundingLevelRegime(lookback=20)
RealizedVolatilityRegime(ohlcv_slot="ohlcv", lookback=20)
MarketTrendRegimeFilter()
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

```python fragment
ForecastScaler(avg_abs_target=10.0, pool="global", method="mean")
ForecastCapper(limit=20.0)
VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d")
```

### FORECAST_COMPOSER

Joins parallel forecast branches into a single blended forecast. Receives
`dict` from Parallel and reduces to `ForecastSeries`.

- **Input:** `dict` (from Parallel)
- **Output:** `ForecastSeries`
- **Base class:** `Composer[ForecastSeries]`

```python fragment
ForecastCombiner(weights={"trend": 0.57, "carry": 0.19, "mean_rev": 0.24})
RegimeWeightedBlender(signal_a_key="trend", signal_b_key="carry", regime_key="regime")
WeightConcatenator()     # weight branches on different assets
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

```python fragment
ForecastWeightNormalizer(target_leverage=1.0)
VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)
EqualWeightSizer(target_leverage=1.0)
```

PositionSizer is a non-generic base class because subclasses accept various
input types. The registry extracts actual types from `run()` method hints.

### RISK_MANAGER

Applies risk constraints to portfolio weights. Caps exposure, enforces
position limits, cost budgets.

- **Input:** `WeightSeries`
- **Output:** `WeightSeries`
- **Base class:** `RiskManager(SignalTransform[WeightSeries, WeightSeries])`

```python fragment
LeverageCap(max_leverage=5.0, max_asset_weight=0.15)
FillNaN(fill_value=0.0)
```

### POSITION_MANAGER

Signal cleaning, inertia, and the position layer. `TradeManager` turns a
stored entry signal into a `Position`; below it, each rule is a reader (the
Position → a series), ordinary components, then an action (`Exit`, `Reduce`,
`ScaleIn`, `AllowEntry`) that changes the Position. `Exposure()` turns the
Position back into signed units held for a sizer; `RiskSizer` takes the
Position directly.

- **Input:** `WeightSeries`, a signal, or a `Position`
- **Output:** `WeightSeries`, or a `Position` (TradeManager and the actions); a reader outputs a series
- **Sizing after it:** `Position -> POSITION_SIZER -> [WeightSeries]`

```python fragment
TradeManager(entries="entries", prices="ohlcv")
InertiaManager(...)
IDMPortfolioAggregator(forecast_slot="forecast_combined", return_vol_slot="return_vol")
```

---

## Group 5 -- OUTPUT

`EXECUTOR` (weights → orders) and `REPORTER` (side-effect observers) close the
ordering, but no strategy authors them: a strategy ends at WeightSeries, and
the platform's execution engine turns the weights into orders.

---

## Phase Ordering Rules

1. Steps must flow forward through groups 0-5
2. Within a group, categories can appear in any order
3. SLOT_OP steps are exempt -- allowed anywhere
4. Nested Pipelines have their own independent phase scope
5. Parallel branches inherit the parent's current phase index
6. Phase violations are `error` in STRICT mode, `warning` in RELAXED mode

Example of a valid ordering:

```text
DATA_LOADER -> DATA_TRANSFORM -> UNIVERSE_FILTER -> INDICATOR ->
SIGNAL_TRANSFORM -> FORECAST_MAPPER -> POSITION_SIZER ->
RISK_MANAGER -> POSITION_MANAGER -> EXECUTOR
```

Example of a violation:

```text
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
