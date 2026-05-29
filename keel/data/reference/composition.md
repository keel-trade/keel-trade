# Composition Patterns

The pipeline DSL supports several composition patterns for building complex
strategies from simple building blocks: parallel branches, factories,
variable references, and nesting.

## Sequential Pipeline

The simplest pattern. Steps execute in order, each receiving the output of
the previous step as its `current` input.

```python
from pipeline_engine.pipeline.execution import Pipeline

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TimeframeResampler(target_timeframe="1d"),
    VolumeUniverseReducer(top_n=50),
    Store(OHLCV_1D),
    EWMA(window=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
], name="simple_strategy")
```

---

## Parallel Branches

Split the pipeline into named branches that execute independently, then
rejoin via a Composer step.

### Dict Syntax (Preferred)

```python
Pipeline([
    Store(OHLCV_1D),
    {
        "momentum": [
            Load(OHLCV_1D),
            EWMA(window=8),
            ForecastScaler(),
        ],
        "carry": [
            FundingDataLoader(),
            TargetSignalResampler(method="mean"),
            ForecastScaler(),
        ],
    },
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
])
```

Dicts in the step list are auto-wrapped as `Parallel` by
`Pipeline._normalize_steps()`.

### Explicit Parallel Class

```python
from pipeline_engine.pipeline.execution import Parallel

Pipeline([
    Store(OHLCV_1D),
    Parallel(
        momentum=[Load(OHLCV_1D), EWMA(window=8), ForecastScaler()],
        carry=[FundingDataLoader(), EWMATransform(window=24), ForecastScaler()],
    ),
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
])
```

### Parallel Behavior

- Each branch receives the **same** `current` value from before the Parallel
- Each branch gets a **snapshot** of the parent Context (sibling isolation)
- Branches execute **sequentially** (not concurrently) -- "parallel" refers
  to data-flow topology
- Results are collected into `dict[str, Any]` keyed by branch name
- After all branches complete, new slots are merged back into parent Context
- Slot write conflicts across branches raise `SlotOverwriteError`

### Consuming Parallel Results

After a Parallel, `current` is a `dict`. Three ways to consume it:

```python
# 1. Composer -- reduce dict to single value
ForecastCombiner(weights={"a": 0.5, "b": 0.5})  # dict -> ForecastSeries

# 2. Extract -- select one branch
Extract("momentum")  # dict -> whatever that branch produced

# 3. Load -- ignore the dict, load from a slot instead
Load(SOME_SLOT)      # dict is discarded, slot value becomes current
```

---

## Factories (Parameterized Sub-Pipelines)

Define reusable pipeline templates as functions. Call with different
arguments to create multiple instances.

```python
def ewmac_signal(fast, slow):
    """EWMA crossover signal with configurable windows."""
    return Pipeline([
        Load(OHLCV_1D),
        {
            "fast": [EWMA(window=fast, min_periods=fast)],
            "slow": [EWMA(window=slow, min_periods=slow)],
        },
        Crossover(),
        VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d"),
        xs_post,
    ])

def roc_signal(period, smooth=7):
    """Rate of change signal with smoothing."""
    return Pipeline([
        Load(OHLCV_1D),
        ROC(period=period),
        EWMATransform(window=smooth),
        VolatilityStandardizer(signal_type="percentage", ohlcv_slot="ohlcv_1d"),
        xs_post,
    ])
```

Use in the main pipeline:

```python
Pipeline([
    # ... data loading ...
    {
        "ewmac_8_32":   ewmac_signal(fast=8, slow=32),
        "ewmac_16_64":  ewmac_signal(fast=16, slow=64),
        "roc_10":       roc_signal(period=10),
        "roc_20":       roc_signal(period=20, smooth=14),
    },
    ForecastCombiner(weights={...}),
])
```

Each factory call returns a new Pipeline instance with its own steps.
Factory functions are the primary mechanism for creating signal families --
same structure, different parameters.

Note: when a Pipeline is used as a branch in a Parallel, it is kept as-is
(not flattened). It executes as a nested pipeline with its own step loop.

---

## Variable Pipelines (Shared Sub-Pipelines)

Assign a pipeline to a variable for reuse across multiple branches.

```python
# Define shared post-processing steps
xs_post = Pipeline([
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0, pool="global", method="mean"),
    ForecastCapper(limit=20.0),
], name="XSPostProcess")

# Use in multiple signal factories
def ewmac_signal(fast, slow):
    return Pipeline([
        Load(OHLCV_1D),
        EWMA(window=fast),
        xs_post,       # Shared post-processing
    ])

def roc_signal(period):
    return Pipeline([
        Load(OHLCV_1D),
        ROC(period=period),
        xs_post,       # Same post-processing
    ])
```

The variable pipeline is embedded directly in each containing pipeline's
step list. It executes as a nested Pipeline, inheriting the parent's
Context and mode.

---

## Nesting

Pipelines can contain Pipelines, and Parallels can contain Pipelines.
This enables hierarchical strategy structures.

### Nested Pipeline in Steps

```python
signal_pipeline = Pipeline([
    Load(OHLCV_1D),
    EWMA(window=8),
    ForecastScaler(),
], name="signal")

position_pipeline = Pipeline([
    VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
], name="position")

Pipeline([
    PriceDataLoader(),
    Store(OHLCV_1D),
    signal_pipeline,     # Nested: inherits parent context
    position_pipeline,   # Nested: sees slots from signal_pipeline
], name="main")
```

### Hierarchical Parallel Nesting

Parallel branches can contain further Parallel structures:

```python
Pipeline([
    Store(OHLCV_1D),
    {
        "trend": Pipeline([
            {
                "ewmac_bucket": Pipeline([
                    {
                        "ewmac_8_32":  ewmac_signal(8, 32),
                        "ewmac_16_64": ewmac_signal(16, 64),
                    },
                    ForecastCombiner(weights={"ewmac_8_32": 0.5, "ewmac_16_64": 0.5}),
                ]),
                "breakout_bucket": Pipeline([
                    {
                        "break_40":  breakout_signal(40),
                        "break_160": breakout_signal(160),
                    },
                    ForecastCombiner(weights={"break_40": 0.5, "break_160": 0.5}),
                ]),
            },
            ForecastCombiner(weights={"ewmac_bucket": 0.6, "breakout_bucket": 0.4}),
        ]),
        "carry": carry_signal(),
    },
    ForecastCombiner(weights={"trend": 0.75, "carry": 0.25}),
])
```

### Nesting Rules

- Each nested Pipeline inherits the parent's Context (slots are shared)
- Nested Pipelines have their **own phase scope** (phase ordering resets)
- Parallel branch isolation applies at each nesting level
- Maximum validation depth is 10 levels (configurable)

---

## Pipeline Composition API

### .then() -- Sequential Composition

Compose two pipelines end-to-end with type checking:

```python
signal_pipeline = Pipeline([...])    # Output: ForecastSeries
position_pipeline = Pipeline([...])  # Input: ForecastSeries

full = signal_pipeline.then(position_pipeline)
# TypeError if signal output is incompatible with position input
```

### .with_params() -- Parameter Variants

Create a new pipeline with modified step parameters:

```python
base = Pipeline([EWMA(window=8), ForecastScaler(avg_abs_target=10.0)])
variant = base.with_params(**{"steps[0].window": 16})
# base is unchanged, variant has EWMA(window=16)
```

### .inject_parameters() -- Location-Based Injection

For optimization, inject parameters using location-based keys:

```python
params = pipeline.discover_all_parameters()
# Returns {"0:EWMA:window": {...}, "1:ForecastScaler:avg_abs_target": {...}, ...}

new_pipeline = pipeline.inject_parameters(**{"0:EWMA:window": 16})
```

### .compile() / .fingerprint() -- Serialization

```python
spec = pipeline.compile()          # Canonical JSON dict
fp = pipeline.fingerprint()        # SHA-256 hex string
restored = Pipeline.from_compiled(spec)  # Round-trip reconstruction
```

---

## Execution Modes

Pipelines accept a `mode` parameter that controls validation, caching,
and hook behavior:

```python
from pipeline_engine.modes import PerformanceMode

Pipeline([...], mode=PerformanceMode.BACKTEST)      # Skip validation
Pipeline([...], mode=PerformanceMode.DEVELOPMENT)    # Cached validation
Pipeline([...], mode=PerformanceMode.PRODUCTION)     # Validate once
Pipeline([...], mode=PerformanceMode.DEBUG)           # Validate every call
```

Nested Pipelines inherit the parent's mode.

---

## Complete Example

A realistic strategy combining all patterns:

```python
# Shared post-processing
xs_post = Pipeline([
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0, pool="global"),
    ForecastCapper(limit=20.0),
], name="XSPostProcess")

# Signal factories
def ewmac(fast, slow):
    return Pipeline([
        Load("ohlcv_1d"),
        {
            "fast": [EWMA(window=fast, min_periods=fast)],
            "slow": [EWMA(window=slow, min_periods=slow)],
        },
        Crossover(),
        VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d"),
        xs_post,
    ])

def carry():
    return Pipeline([
        FundingDataLoader(use_cache=True),
        TargetSignalResampler(method="mean"),
        NegateTransform(),
        VolatilityStandardizer(signal_type="percentage", ohlcv_slot="ohlcv_1d"),
        xs_post,
    ], name="Carry")

# Position sizing sub-pipeline
def position_pipeline():
    return Pipeline([
        {"return_vol": [
            Load("ohlcv_1d"),
            ReturnVolatility(window="36d"),
            Store("return_vol"),
        ]},
        VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
        IDMPortfolioAggregator(
            forecast_slot="forecast_combined",
            return_vol_slot="return_vol",
            ohlcv_slot="ohlcv_1d",
        ),
        PositionInertia(threshold=0.30),
        LeverageCap(max_leverage=5.0),
    ], name="PositionPipeline")

# Main pipeline
Pipeline([
    StoreValue("bar_offset", "12h"),
    PriceDataLoader(timeframe="15min", use_cache=True),
    TimeframeResampler(target_timeframe="1d", source_timeframe="15min"),
    VolumeUniverseReducer(top_n=50, lookback_bars=60),
    Store("ohlcv_1d"),
    {
        "trend": Pipeline([
            {
                "ewmac_8_32": ewmac(8, 32),
                "ewmac_16_64": ewmac(16, 64),
            },
            ForecastCombiner(weights={"ewmac_8_32": 0.5, "ewmac_16_64": 0.5}),
        ]),
        "carry": carry(),
    },
    ForecastCombiner(weights={"trend": 0.75, "carry": 0.25}),
    Store("forecast_combined"),
    position_pipeline(),
], name="trend_carry_portfolio")
```
