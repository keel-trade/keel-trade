# Slot System

Slots provide typed, named storage for sharing data between pipeline steps.
They enable cross-branch data sharing and deferred data access without
coupling steps together. In the DSL a slot is named by a string literal —
`Store("ohlcv_1d")`, `Load("ohlcv_1d")`.

## Core Operations

### Store

Save the current pipeline value to a named slot. Passthrough -- the value
continues flowing through the pipeline unchanged.

```python fragment
PriceDataLoader() → Store("ohlcv_1d") → ...   # save the bars for later use; the same data flows on
```

- **Input:** T (current value)
- **Output:** T (same value, passed through)
- **Side effect:** Writes current into the named slot in Context

### Load

Load a value from a named slot into the pipeline flow. Replaces the
current value entirely.

```python fragment
Load("ohlcv_1d") → ROC(period=10)   # replace current with the stored OHLCV, then compute on it
```

- **Input:** any (ignored)
- **Output:** T (value from slot)
- **Side effect:** Reads from the named slot in Context

### StoreValue

Store a literal/constant value into a slot. Current pipeline value passes
through unchanged. Use it only for a genuine runtime slot value that a
downstream component reads by slot name. Strategy configuration is NOT a
slot value: `target_timeframe` and `bar_offset` are declared once in
`Globals(...)` above the Pipeline, and the loaders and declaration-backed
resamplers read them through their declaration refs — never
`StoreValue("target_timeframe", ...)`.

```python fragment
PriceDataLoader() → StoreValue("some_slot", 1.5)   # illustrative: a literal a later step reads by slot name
```

- **Input:** any (passed through unchanged)
- **Output:** same as input
- **Side effect:** Writes the fixed value into the named slot

### Extract

Select a single value from a dict (after Parallel). Not a slot operation
per se, but categorized as SLOT_OP for phase ordering purposes.

```python fragment
{"momentum": [Load("ohlcv_1d"), EWMA(window=8)], "carry": [FundingDataLoader(), NegateTransform()]}
→ Extract("momentum")   # select just the momentum branch result
```

---

## Slot Names and Types

A slot is identified by its NAME: two references to the same name are the
same data, whatever type each declares. The type is for documentation,
validation and introspection — a slot read is checked against the type its
writer produced (`SLOT_TYPE_MISMATCH`), and annotated types carry their
bounds.

Conventional names the components' `*_slot` parameters default to or
document: `"ohlcv"` / `"ohlcv_1d"` (OHLCV bars on the strategy clock),
`"return_vol"` (ReturnVolatility), `"vol"` (the vol sizers), `"entries"`
(`TradeManager(entries=)`), `"forecast_combined"`
(IDMPortfolioAggregator). The clock is not a slot: it is `Globals(...)`.

---

## Slot Parameters

Many components have `*_slot` parameters that reference slots by name. The
component reads from the named slot during execution, in addition to
receiving the pipeline's current value.

```python fragment
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

The component declares which slots it reads, and the pipeline executor
fetches those slot values from the Context and passes them to the component.

---

## Slot Validation

### Static Validation

The validator checks that every Load (and every slot read) has a
corresponding prior Store or StoreValue — `SLOT_NOT_FOUND` /
`SLOT_REF_NOT_FOUND`, with the missing `Store("...")` named in the fix.

### Parallel Branch Isolation

Within a Parallel, each branch gets a **snapshot** of the parent context.
Branches cannot see each other's intermediate writes. After all branches
complete, new slots are merged back into the parent context.

```python fragment
Store("ohlcv_1d") → {
    "branch_a": [Load("ohlcv_1d"), ROC(period=20), Store("momentum")],   # reads the parent snapshot
    "branch_b": [Load("ohlcv_1d"), RSI(period=14), Store("rsi")],        # cannot see "momentum"
}
# after the Parallel: both "momentum" and "rsi" are available
```

### No Slot Overwrites in Parallel

Parallel branches must write to distinct slots. If two branches write the
same slot name, a `SlotOverwriteError` is raised when the pipeline runs —
the validator does not catch it, so give each branch's Store its own name
(and parameterize slot names in a factory that is called from several
branches).

### Self-Cycle Detection

A step that both reads and writes the same slot produces the
`SLOT_SELF_CYCLE` warning.

---

## Common Slot Patterns

### Share Data Between Branches

Store data before Parallel, Load in branches:

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="every_bar")

Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    {
        "momentum": Pipeline([
            Load("ohlcv_1d"),
            EWMA(window=8),
            ForecastScaler(),
        ]),
        "carry": Pipeline([
            FundingDataLoader(),
            NegateTransform(),
            ForecastScaler(),
        ]),
    },
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="shared_ohlcv")
```

### Store for Later Position Sizing

Store intermediate results for use in position sizing:

```python fragment
ForecastCombiner(weights={...}) → Store("forecast_combined")   # read later by IDMPortfolioAggregator
→ VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)
```

### Multi-Timeframe Slot Sharing

A branch on its own clock loads its own grain with an explicit `timeframe=`
(the only override) and comes back to the Globals clock with
`TargetSignalResampler(method=...)` (a FINER branch is coarsened) or
`TargetSignalProjector()` (a COARSER branch is projected) before it is combined:

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="every_bar")

Pipeline([
    PriceDataLoader(),                    # the Globals clock: 1d, closing 00:00 UTC
    Store("ohlcv_1d"),
    {
        "daily": [Load("ohlcv_1d"), ROC(period=14), CrossSectionalZScore(), ForecastScaler()],
        "intraday": [
            PriceDataLoader(timeframe="15min"),   # this branch's own clock
            ROC(period=96),
            TargetSignalResampler(method="mean"),
            CrossSectionalZScore(),
            ForecastScaler(),
        ],
    },
    ForecastCombiner(weights={"daily": 0.5, "intraday": 0.5}),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="two_clocks")
```
