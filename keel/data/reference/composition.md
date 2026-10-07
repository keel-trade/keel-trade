# Composition Patterns

The pipeline DSL supports several composition patterns for building complex
strategies from simple building blocks: parallel branches, factories,
variable references, and nesting.

## Sequential Pipeline

The simplest pattern. Steps execute in order, each receiving the output of
the previous step as its `current` input.

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="every_bar")

Pipeline([
    PriceDataLoader(),          # serves the Globals clock — no resampler step
    EWMA(window=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="simple_strategy")
```

---

## Parallel Branches

Split the pipeline into named branches that execute independently, then
rejoin via a Composer step.

### Parallel branches — a dict step

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="every_bar")

Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    {
        "momentum": [
            Load("ohlcv_1d"),
            EWMA(window=8),
            ForecastScaler(),
        ],
        "carry": [
            FundingDataLoader(),
            NegateTransform(),
            ForecastScaler(),
        ],
    },
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="momentum_carry")
```

In the DSL a Parallel is not a call: it is a dict literal in the step list —
each key names a branch, each value is that branch's step list. Branches
receive the same input, run in isolation, and the dict they produce is
consumed by the next step. The Python pipeline API also has a `Parallel`
class that takes branches as keyword arguments; that form is the Python
API's, and the DSL parser rejects it (`PARALLEL_BRANCH_SHAPE`).

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

```python fragment
# 1. Composer -- reduce dict to single value
ForecastCombiner(weights={"a": 0.5, "b": 0.5})  # dict -> ForecastSeries

# 2. Extract -- select one branch
Extract("momentum")  # dict -> whatever that branch produced

# 3. Load -- ignore the dict, load from a slot instead
Load("some_slot")    # dict is discarded, slot value becomes current
```

---

## Factories and Variable Pipelines

A **factory** is a function whose body is one `return Pipeline([...])`; call
it with keyword arguments to create one instance per parameter set. A
**variable** holds a Pipeline for reuse — it is embedded directly in each
step list that names it and runs as a nested Pipeline, inheriting the
parent's Context. Variables and factories are defined above the main
`Pipeline(...)`, and a factory may use a variable defined before it.

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL", "AVAX", "LINK"])
Execution(rebalance="every_bar")

# Shared post-processing
xs_post = Pipeline([
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0, pool="global", method="mean"),
    ForecastCapper(limit=20.0),
], name="XSPostProcess")

def ewmac_signal(fast, slow):
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

def roc_signal(period):
    return Pipeline([
        Load("ohlcv_1d"),
        ROC(period=period),
        VolatilityStandardizer(signal_type="percentage", ohlcv_slot="ohlcv_1d"),
        xs_post,
    ])

Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    {
        "ewmac_8_32":  ewmac_signal(fast=8, slow=32),
        "ewmac_16_64": ewmac_signal(fast=16, slow=64),
        "roc_20":      roc_signal(period=20),
    },
    ForecastCombiner(weights={"ewmac_8_32": 0.35, "ewmac_16_64": 0.35, "roc_20": 0.3}),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="signal_family")
```

Each factory call returns a new Pipeline instance with its own steps —
factories are the primary mechanism for signal families: same structure,
different parameters. When a Pipeline is a branch of a Parallel it is kept
as-is (not flattened) and executes as a nested pipeline with its own step
loop.

---

## Nesting

Pipelines can contain Pipelines, and Parallels can contain Pipelines. This
enables hierarchical strategy structures — a branch whose value is a
Pipeline can itself hold a Parallel and its composer:

```python fragment
{
    "trend": Pipeline([
        {
            "ewmac_8_32":  ewmac_signal(fast=8, slow=32),
            "ewmac_16_64": ewmac_signal(fast=16, slow=64),
        },
        ForecastCombiner(weights={"ewmac_8_32": 0.5, "ewmac_16_64": 0.5}),
    ]),
    "carry": carry(),
}
→ ForecastCombiner(weights={"trend": 0.75, "carry": 0.25})
```

### Nesting Rules

- Each nested Pipeline inherits the parent's Context (slots are shared)
- Nested Pipelines have their **own phase scope** (phase ordering resets)
- Parallel branch isolation applies at each nesting level
- Maximum validation depth is 10 levels (configurable)

---

## Complete Example

A realistic strategy combining all patterns:

```python
Globals(target_timeframe="1d")   # the clock (and any offset) live here
Universe(mode="manual", symbols=["BTC", "ETH", "SOL", "AVAX", "LINK"])
Execution(rebalance="every_bar")

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
        FundingDataLoader(),
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
        LeverageCap(max_leverage=5.0),
    ], name="PositionPipeline")

# Main pipeline
Pipeline([
    PriceDataLoader(),      # serves 1d bars (00:00 UTC close) — nothing to resample
    Store("ohlcv_1d"),
    {
        "trend": Pipeline([
            {
                "ewmac_8_32": ewmac(fast=8, slow=32),
                "ewmac_16_64": ewmac(fast=16, slow=64),
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
