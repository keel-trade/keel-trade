<!-- keywords: top, rank, select, rotation, TopNAssetSelector, SelectionToSignalConverter, PositionManager, screen, filter, universe -->
<!-- pattern: screen_select -->

# Screen-Select-Allocate

Select the top N assets by a ranking signal, hold for a period, then rebalance.
Best for rotation strategies (momentum rotation, value rotation).

## Component Sequence

1. **PriceDataLoader** - Load OHLCV data
2. **Indicator** (ROC, RSI, etc.) - Compute ranking signal
3. **TopNAssetSelector** (n=10, ascending=False) - Select top N assets
4. **SelectionToSignalConverter** (hold_periods=7) - Convert selection to held positions with exit timing
5. **EqualWeightAllocator** - Equal weight selected assets → WeightSeries

## Why Each Step Matters

- **TopNAssetSelector**: Ranks all assets and selects the top N. Without it,
  all assets would be included.
- **SelectionToSignalConverter**: Adds hold period tracking and manages exits.
  Without it, assets are re-selected every bar (excessive turnover) and there's
  no exit mechanism.

## Hold Period Choices

- **Short** (3-7 days): Higher turnover, faster reaction, more trading costs
- **Medium** (7-14 days): Balanced turnover and reaction speed
- **Long** (14-30 days): Lower turnover, smoother returns, slower adaptation

## Minimal Example

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    TopNAssetSelector(n=10, ascending=False),
    SelectionToSignalConverter(hold_periods=7),
    EqualWeightAllocator(),
], name="top_n_rotation")
```

## Common Mistakes

- **M-11**: TopNAssetSelector without SelectionToSignalConverter — selects assets
  but never manages exits. Always follow with SelectionToSignalConverter(hold_periods=N).
- Using VolTargetWeightConverter after TopNAssetSelector — selections produce
  BinarySignal, not ForecastSeries. Use EqualWeightAllocator instead.
- Setting ascending=True when you want the highest-ranked assets (ascending=True
  selects the LOWEST values).
