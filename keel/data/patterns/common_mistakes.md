<!-- keywords: mistake, error, bug, wrong, fix, pitfall, gotcha, warning, anti-pattern -->
<!-- pattern: common_mistakes -->

# Common Mistakes

Top mistakes ranked by frequency. Check these when debugging a strategy.

## M-01: Equal Weights on Continuous Forecasts

**Wrong**: `ForecastSeries → EqualWeightAllocator`
**Right**: `ForecastSeries → ForecastWeightNormalizer` (simple) or `ForecastSeries → VolTargetWeightConverter` (production)

ForecastSeries values carry conviction (higher magnitude = stronger signal).
EqualWeightAllocator discards this information. Use ForecastWeightNormalizer
to preserve signal magnitude while normalizing to a target leverage.

**Exception**: EqualWeightAllocator IS correct for Path 3 (TopN/filter → equal-weight
selected assets) where the selection is the signal, not the magnitude.

## M-03: Normalizing Binary Signals

**Wrong**: `ThresholdCross → CrossSectionalZScore → ForecastScaler`
**Right**: `ThresholdCross → SelectionToSignalConverter → EqualWeightAllocator`

Binary signals ({-1, 0, +1}) are already discrete decisions. Cross-sectional
z-scoring is meaningless. Binary signals follow Path 2 (entry/exit), not
Path 1 (continuous forecast).

## M-09: Pipeline Without WeightSeries Output

The backtester needs WeightSeries. A pipeline ending at SignalSeries or
ForecastSeries cannot be tested. Always check the output type before running
a backtest. Use `pipeline_stage` tool to verify. The simplest fix: add
`ForecastWeightNormalizer(target_leverage=1.0)` as the terminal step.

## M-10: Missing Data Pipeline

Every pipeline needs data. The standard opening is:
`Globals(target_timeframe="1d")` above the Pipeline, then `PriceDataLoader → Store("ohlcv_1d")`.
Don't skip this even for simple strategies.

## M-11: TopN Without Exit Logic

**Wrong**: `ROC → TopNAssetSelector → EqualWeightAllocator`
**Right**: `ROC → TopNAssetSelector → SelectionToSignalConverter(hold_periods=7) → EqualWeightAllocator`

TopNAssetSelector selects entries but doesn't manage exits. Without
SelectionToSignalConverter, positions have no exit mechanism.

## M-12: Unconsumed Parallel

After a Parallel block (produces dict), you MUST add a Composer, Extract,
or Load. A pipeline ending at dict is incomplete and cannot be backtested.

## M-16: Over-Indexing on One Pattern

Not every strategy needs hierarchical multi-signal forecast-combine. A simple
factor tilt (4 components) or entry/exit strategy may be exactly right.
Match complexity to user intent.

## M-17: Missing ReturnVolatility Before VolTargetWeightConverter

**Wrong**: `ForecastCapper → VolTargetWeightConverter` (no return volatility computed)
**Right**: `ReturnVolatility(window="36d") → Store("return_vol")` then `VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)`

VolTargetWeightConverter needs per-instrument return volatility from a `return_vol`
slot. Compute it first via ReturnVolatility and store it in a parallel branch.

## M-18: Parallel Branch Shape Mismatch (Advanced)

This is rare with Universe selector — all data loaders receive the same
resolved universe, so assets match by default. Only applies when a pipeline
component explicitly drops assets from the DataFrame.

**Wrong**: Price branch uses VolumeUniverseReducer (30→20 assets), funding
branch doesn't → ForecastCombiner gets mismatched shapes.

**Right**: Store reduced OHLCV before Parallel, use AssetAligner in
secondary branches:

```
VolumeUniverseReducer() → Store("ohlcv_1d") →
Parallel({
    "momentum": [ROC(...), ...],
    "carry": [FundingDataLoader(), TargetSignalResampler(method="mean"), AssetAligner(reference_slot="ohlcv_1d"), ...],
})
```

Note: TopNAssetSelector does NOT change dimensions — it produces a mask,
not a reduced universe.

## M-19: TargetTimeframeResampler after a signal step

**Wrong**: `RSI → NegateTransform → TargetTimeframeResampler`
**Right**: `TargetTimeframeResampler → RSI → NegateTransform`

TargetTimeframeResampler expects OHLCV data, not a signal. Place it
immediately after the data loader, before any indicator or transform.
Validator emits `TYPE_MISMATCH` with this code.

## M-20: bar_offset at same source/target timeframe

**Wrong**: `Globals(target_timeframe='15min', bar_offset='15min')` with a 15min loader.
**Right**: Remove `bar_offset` — it has no valid value when target matches source.

`bar_offset` shifts bin anchors when aggregating up (e.g. 15min → 1d at
12:00 UTC). At same source/target it would silently mislabel bars.
Validator emits `BAR_OFFSET_AT_SAME_TF`.

## M-21: bar_offset not a multiple of source timeframe

**Wrong**: `Globals(bar_offset='5min')` with a 15min loader.
**Right**: Use a multiple of the source timeframe (e.g. `'15min'`, `'30min'`, `'12h'`).

A non-multiple offset pulls partial source bars into the wrong aggregation
bin. Validator emits `BAR_OFFSET_NOT_MULTIPLE`.

## M-22: Unnecessary TargetTimeframeResampler at same timeframe

**Wrong (works but noisy)**: `Globals(target_timeframe='15min') + PriceDataLoader(timeframe='15min') + TargetTimeframeResampler()`
**Right (cleaner)**: Drop both `Globals(target_timeframe=...)` and the resampler step.

The runtime short-circuits TargetTimeframeResampler when target equals
source, so this is safe — just visually noisy. Validator emits
`RESAMPLER_NOOP` as a warning. Keep the Globals+Resampler pair only if
you want the timeframe-knob for later iteration; otherwise omit both.

## Polarity Mistakes

- **Carry**: Always NegateTransform after FundingDataLoader — positive funding
  means longs pay shorts, so carry strategies SHORT high-funded assets.
- **Mean reversion**: NegateTransform after RSI/oscillators — RSI high =
  overbought, but mean reversion wants to BUY oversold (low RSI → positive forecast).
