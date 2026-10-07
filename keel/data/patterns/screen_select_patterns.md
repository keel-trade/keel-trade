<!-- keywords: top, rank, select, rotation, TopNAssetSelector, SelectionToSignalConverter, screen, filter, universe -->
<!-- pattern: screen_select -->

# Screen-Select-Allocate

Select the top N assets by a ranking signal, hold for a period, then rebalance.
Best for rotation strategies (momentum rotation, value rotation).

## Component Sequence

1. **PriceDataLoader** - Load OHLCV on the Globals clock (no resampler step)
2. **Indicator** (ROC, RSI, etc.) - Compute ranking signal
3. **TopNAssetSelector** (n=10, method="largest") - Select top N assets (a {0,1} mask)
4. **EqualWeightAllocator** - Equal weight selected assets → WeightSeries
5. **WeightCadence** (duration="7d") - Hold the targets for the calendar period
6. **FillNaN** (fill_value=0.0) - Densify the terminal weights (a masked-out asset is an explicit 0.0)

`SelectionToSignalConverter(hold_periods=N)` is the bar-count hold; its
declared input is a {0,1} mask on the weight base, so today it is either
type-refused before the allocator or entry-less after it. The calendar hold
above is the working hold until the converter's input typing is fixed.

## Why Each Step Matters

- **TopNAssetSelector**: Ranks all assets and selects the top N. Without it,
  all assets would be included.
- **WeightCadence**: Samples the intended weights at the calendar boundary and
  holds them. Without a hold, assets are re-selected every bar (excessive
  turnover) and there's no exit mechanism.

## Dollar-liquidity floor and calendar cadence

- **"Skip anything under $10M/24h"** is a THRESHOLD with a variable-size
  universe, never a top-N: `Universe(min_trailing_dollar_volume=10_000_000)`
  at resolution plus the causal per-bar mask
  `DollarVolumeLoader() -> Store("dollar_volume")`, then
  `RollingDollarVolumeMask(dollar_volume_slot="dollar_volume", window="24h", min_notional=10_000_000) -> Store("liq")`
  applied with `ApplyUniverseMask(mask_slot="liq")` BEFORE any rank or
  z-score (gate-before-stats). Both read dollar volume: traded notional
  (sum of trade price × size) — the same number at every clock. Before
  2025-03-23 the Universe floor has no data (it reads 1m trade bars only),
  while `DollarVolumeLoader` splices in the 15m candle `volume × close`
  there. `min_trailing_notional_proxy` and `RollingNotionalProxyMask` (the
  candle proxy) are deprecated.
- **"Rebalanced weekly" / "hold the targets for a week"** is
  `WeightCadence(duration="7d", anchor="MONDAY", timezone="UTC")` on the
  FINAL weights: intended weights sampled at the calendar boundary and held;
  NaN before the first full boundary; no off-schedule final sample. It does
  not guarantee a holding period after partial fills (execution-layer).
- A universe-mask strategy must end DENSE: add `FillNaN(fill_value=0.0)` after
  the cadence (the validator's `NONDENSE_TERMINAL_WEIGHTS` names why).
- A manual basket needs no `resolved=`: the save bakes it from `symbols`
  (minus exclusions, plus inclusions), and the runtime reads the same list.

```python fragment
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=[...], groups={"l1": [...], "defi": [...]}, min_trailing_dollar_volume=10_000_000)
Pipeline([
    DollarVolumeLoader(), Store("dollar_volume"),
    PriceDataLoader(), Store("ohlcv"),
    RollingDollarVolumeMask(dollar_volume_slot="dollar_volume", window="24h", min_notional=10_000_000, rebalance_freq="1d"), Store("liq"),
    {"l1": [Load("ohlcv"), GroupAssetFilter(group="l1"), ROC(period=60), ApplyUniverseMask(mask_slot="liq"),
            TopNAssetSelector(n=2, method="largest"), EqualWeightAllocator()],
     "defi": [...]},
    WeightConcatenator(), LeverageCap(max_leverage=1.0),
    WeightCadence(duration="7d", anchor="MONDAY", timezone="UTC"), FillNaN(fill_value=0.0),
])
```

## Hold Period Choices

- **Short** (3-7 days): Higher turnover, faster reaction, more trading costs
- **Medium** (7-14 days): Balanced turnover and reaction speed
- **Long** (14-30 days): Lower turnover, smoother returns, slower adaptation

## Minimal Example

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE", "XRP", "ADA", "SUI", "NEAR", "APT", "HYPE"])
Execution(rebalance="on_change")
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    TopNAssetSelector(n=10, method="largest"),
    EqualWeightAllocator(),
    WeightCadence(duration="7d", anchor="MONDAY", timezone="UTC"),
    FillNaN(fill_value=0.0),
], name="top_n_rotation")
```

## Common Mistakes

- **M-11**: TopNAssetSelector without a hold — selects assets but never manages
  exits. When you want a slower rebalance than the clock, follow the allocator
  with a hold (`WeightCadence(duration=...)`).
- Using VolTargetWeightConverter after TopNAssetSelector — selections produce
  BinarySignal, not ForecastSeries. Use EqualWeightAllocator instead.
- Setting `method="smallest"` when you want the highest-ranked assets
  (`"smallest"` selects the LOWEST values).
