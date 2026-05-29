# Slot System

Slots provide typed, named storage for sharing data between pipeline steps.
They enable cross-branch data sharing, config propagation, and deferred
data access without coupling steps together.

## Core Operations

### Store

Save the current pipeline value to a named slot. Passthrough -- the value
continues flowing through the pipeline unchanged.

```python
from pipeline_engine.slot_ops import Store
from pipeline_engine.slots import OHLCV_1D

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TimeframeResampler(target_timeframe="1d"),
    Store(OHLCV_1D),       # Save resampled data for later use
    # ... pipeline continues with same data
])
```

- **Input:** T (current value)
- **Output:** T (same value, passed through)
- **Side effect:** Writes current into the named slot in Context

### Load

Load a value from a named slot into the pipeline flow. Replaces the
current value entirely.

```python
from pipeline_engine.slot_ops import Load
from pipeline_engine.slots import OHLCV_1D

Pipeline([
    Load(OHLCV_1D),        # Replace current with stored OHLCV data
    ROC(period=10),         # Compute indicator on loaded data
])
```

- **Input:** any (ignored)
- **Output:** T (value from slot)
- **Side effect:** Reads from the named slot in Context

### StoreValue

Store a literal/constant value into a slot. Current pipeline value passes
through unchanged. Use for config parameters that must be consistent across
multiple components.

```python
from pipeline_engine.slot_ops import StoreValue
from pipeline_engine.slots import BAR_OFFSET, TARGET_TIMEFRAME

Pipeline([
    StoreValue(BAR_OFFSET, "12h"),
    StoreValue(TARGET_TIMEFRAME, "1d"),
    PriceDataLoader(timeframe="15min"),
    # ... components can read BAR_OFFSET and TARGET_TIMEFRAME slots
])
```

- **Input:** any (passed through unchanged)
- **Output:** same as input
- **Side effect:** Writes the fixed value into the named slot

### Extract

Select a single value from a dict (after Parallel). Not a slot operation
per se, but categorized as SLOT_OP for phase ordering purposes.

```python
from pipeline_engine.slot_ops import Extract

Pipeline([
    {
        "momentum": [Load(OHLCV_1D), EWMA(window=8)],
        "carry":    [Load(FUNDING_RATES), EWMATransform(window=24)],
    },
    Extract("momentum"),   # Select just the momentum branch result
])
```

---

## Slot Handles

Slots are created via `Slot.create(name, type)`:

```python
from pipeline_engine.slots import Slot
from pipeline_engine.types import OHLCVDict, ForecastSeries, WeightSeries

# Create typed slot handles
OHLCV_1D = Slot.create("ohlcv_1d", OHLCVDict)
FORECAST_COMBINED = Slot.create("forecast_combined", ForecastSeries)
FINAL_WEIGHTS = Slot.create("final_weights", WeightSeries)
```

Key properties:
- **Identity by name:** Two slots with the same name refer to the same data,
  regardless of declared type
- **value_type:** For documentation, validation, and introspection -- not identity
- **Bounds:** Annotated types carry bounds constraints accessible via `slot.get_bounds()`

### Pre-Defined Domain Slots

The engine provides common slots out of the box:

```python
from pipeline_engine.slots import (
    OHLCV_1D,           # Slot[OHLCVDict] -- daily OHLCV data
    OHLCV_1H,           # Slot[OHLCVDict] -- hourly OHLCV data
    FUNDING_RATES,      # Slot[StreamSeries] -- funding rate data
    FORECAST_MOMENTUM,  # Slot[ForecastSeries] -- momentum forecast
    FORECAST_CARRY,     # Slot[ForecastSeries] -- carry forecast
    COMBINED_FORECAST,  # Slot[ForecastSeries] -- blended forecast
    MARKET_BETA,        # Slot[SignalSeries] -- market beta
    FINAL_WEIGHTS,      # Slot[WeightSeries] -- final portfolio weights
    TARGET_WEIGHTS,     # Slot[WeightSeries] -- target weights
    BAR_OFFSET,         # Slot[TemporalOffset] -- bar alignment offset
    TARGET_TIMEFRAME,   # Slot[str] -- target timeframe string
)
```

### String-Based Slot References

In DSL code and some component parameters, slots are referenced by name
string rather than Slot object. The DSL resolver creates Slot handles from
string names during pipeline construction:

```python
# DSL-style (string names)
Pipeline([
    Store("ohlcv_1d"),
    Load("ohlcv_1d"),
    StoreValue("bar_offset", "12h"),
])
```

---

## Slot Parameters

Many components have `*_slot` parameters that reference slots by name. The
component reads from the named slot during execution, in addition to
receiving the pipeline's current value.

```python
# Component reads current (SignalSeries) + slot data
VolatilityStandardizer(
    signal_type="price_points",
    ohlcv_slot="ohlcv_1d",     # Reads OHLCV data from this slot
    window="36d",
)

# Component reads current (ForecastSeries) + return vol from slot
VolTargetWeightConverter(
    return_vol_slot="return_vol",     # Reads return volatility
    pct_target=0.25,
)

# Component reads current + return vol + OHLCV from slots
IDMPortfolioAggregator(
    forecast_slot="forecast_combined",
    return_vol_slot="return_vol",
    ohlcv_slot="ohlcv_1d",
)
```

The component declares which slots it reads via its `reads` property, and
the pipeline executor fetches slot values from Context and passes them as
`**kwargs` to the `run()` method.

---

## Slot Validation

### Static Validation

The `PipelineValidator.validate_slot_availability()` pass checks that every
Load (and every slot read) has a corresponding prior Store or StoreValue:

```python
# VALID: Store before Load
Pipeline([
    PriceDataLoader(),
    Store(OHLCV_1D),
    # ... other steps ...
    Load(OHLCV_1D),           # OK: slot was written above
])

# INVALID: Load without Store
Pipeline([
    Load(OHLCV_1D),           # ERROR: SLOT_NOT_FOUND -- no prior Store
])
```

### Parallel Branch Isolation

Within a Parallel, each branch gets a **snapshot** of the parent context.
Branches cannot see each other's intermediate writes. After all branches
complete, new slots are merged back into the parent context.

```python
Pipeline([
    Store(OHLCV_1D),
    {
        "branch_a": [
            Load(OHLCV_1D),       # OK: reads from parent snapshot
            Store(FORECAST_MOMENTUM),
        ],
        "branch_b": [
            Load(OHLCV_1D),       # OK: reads from parent snapshot
            # Load(FORECAST_MOMENTUM),  # ERROR at runtime: branch_a's
            #                           # writes are not visible here
            Store(FORECAST_CARRY),
        ],
    },
    # After Parallel: both FORECAST_MOMENTUM and FORECAST_CARRY are available
])
```

### No Slot Overwrites in Parallel

Parallel branches must write to distinct slots. If two branches write the
same slot name, a `SlotOverwriteError` is raised at construction time:

```python
# INVALID: both branches write the same slot
{
    "a": [Store(OHLCV_1D)],
    "b": [Store(OHLCV_1D)],   # SlotOverwriteError!
}
```

### Self-Cycle Detection

A step that both reads and writes the same slot produces a warning:

```python
# WARNING: SLOT_SELF_CYCLE
class MyStep:
    reads = (MY_SLOT,)
    writes = (MY_SLOT,)
```

---

## Common Slot Patterns

### Share Data Between Branches

Store data before Parallel, Load in branches:

```python
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TimeframeResampler(target_timeframe="1d"),
    Store(OHLCV_1D),
    {
        "momentum": Pipeline([
            Load(OHLCV_1D),
            EWMA(window=8),
            ForecastScaler(),
        ]),
        "carry": Pipeline([
            FundingDataLoader(),
            TargetSignalResampler(method="mean"),
            ForecastScaler(),
        ]),
    },
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
])
```

### Config Propagation

Use StoreValue at pipeline start for shared configuration:

```python
Pipeline([
    StoreValue(BAR_OFFSET, "12h"),
    StoreValue(TARGET_TIMEFRAME, "1d"),
    PriceDataLoader(timeframe="15min"),
    # ... all downstream components can read these config values
])
```

### Store for Later Position Sizing

Store intermediate results for use in position sizing:

```python
Pipeline([
    # ... signal generation ...
    Store(COMBINED_FORECAST),    # Save for VolTargetWeightConverter / IDMPortfolioAggregator
    # ... position sizing pipeline reads forecast_combined slot ...
    VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
])
```

### Multi-Timeframe Slot Sharing

Store OHLCV data at different timeframes for mixed-frequency strategies:

```python
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TimeframeResampler(target_timeframe="1d"),
    Store(OHLCV_1D),
    TimeframeResampler(target_timeframe="1h"),
    Store(OHLCV_1H),
    # ... branches can Load either timeframe
])
```
