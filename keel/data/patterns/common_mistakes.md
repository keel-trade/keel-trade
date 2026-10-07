<!-- keywords: mistake, error, bug, wrong, fix, pitfall, gotcha, warning, anti-pattern -->
<!-- pattern: common_mistakes -->

# Common Mistakes

Structural mistakes — a clock or bar offset the loaders cannot serve, a
resampler after a loader that already follows Globals, branches on different
clocks at a combiner, a missing Store, a type that does not chain, equal
weights on a continuous forecast, an uncapped FixedWeightSizer — are reported
by the validator with a coded message whose suggestion is the fix; the
`rule:<CODE>` help topic explains any code. A pipeline that does not end in
WeightSeries, including one that ends at an unconsumed Parallel, is refused
by the backtest before it runs. The mistakes below are the ones the validator cannot judge.

## M-03: Normalizing Binary Signals

**Wrong**: `ThresholdCross → CrossSectionalZScore → ForecastScaler`
**Right**: `ThresholdCross → EqualWeightSizer` (or `ThresholdCross → Store("entries") → TradeManager(...) → sizer`)

Binary signals ({-1, 0, +1}) are already discrete decisions. Cross-sectional
z-scoring is meaningless. Binary signals follow Path 2 (entry/exit), not
Path 1 (continuous forecast).

## M-11: Top-N Without a Hold

**Wrong**: `ROC → TopNAssetSelector → EqualWeightAllocator` (re-selected every bar)
**Right**: `ROC → TopNAssetSelector → EqualWeightAllocator → WeightCadence(duration="7d") → FillNaN(fill_value=0.0)`

TopNAssetSelector selects assets but doesn't manage exits; WeightCadence holds
the targets for the calendar period (the `screen_select_patterns` topic).

## M-16: Over-Indexing on One Pattern

Not every strategy needs hierarchical multi-signal forecast-combine. A simple
factor tilt (4 components) or entry/exit strategy may be exactly right.
Match complexity to user intent.

## M-18: Parallel Branch Shape Mismatch (Advanced)

This is rare with Universe selector — all data loaders receive the same
resolved universe, so assets match by default. Only applies when a pipeline
component explicitly drops assets from the DataFrame.

**Wrong**: Price branch uses VolumeUniverseReducer (30→20 assets), funding
branch doesn't → ForecastCombiner gets mismatched shapes.

**Right**: Store reduced OHLCV before Parallel, use AssetAligner in
secondary branches:

```python fragment
VolumeUniverseReducer(...) → Store("ohlcv_1d") → {
    "momentum": [ROC(period=20), ...],
    "carry": [FundingDataLoader(), AssetAligner(reference_slot="ohlcv_1d"), ...],
}
```

Note: TopNAssetSelector does NOT change dimensions — it produces a mask,
not a reduced universe.

## M-34: "Same length, different timestamps" at a combiner — a span, not a clock

**Symptom**: `SignalProduct` / `SignalRatio` / `Crossover` refuse with
`branch indices differ: 'left' has N rows, 'right' has N rows (same length,
different timestamps)` although both branches ARE on the declared clock and
the validator is clean (no `CLOCK_MISMATCH`).

**What it is**: the two branches carry the same `(period, offset)` but a
different **span** — different first/last labels. The guard says so
(`This is a SPAN difference`) and names each side's clock, first/last and the
first divergent label. Every series on one clock inside one run must carry
the run window's completed-bar grid `(start, end]`; a producer that does not
is a platform defect, which re-projecting or re-resampling the branch does
not fix (that is the fix for a CLOCK difference, and the guard names those
by name: `TargetSignalProjector()` coarse → fine, `TargetSignalResampler()`
fine → coarse, `Globals(bar_offset=...)` for a phase split).

## M-35: Measuring a Trade From Its Signal

A stop, target, trail or time exit is measured from this trade's entry, so it
is a rule on the Position. A value computed from the stored entry signal is
anchored to the signal, which stays on after the trade closes or re-enters.

**Wrong** (fires 48 bars after the signal, so on a level entry a re-entered
trade is closed early):

```python fragment
Load("entries") → Lag(periods=48) → Store("old_signal")
→ TradeManager(entries="entries", prices="ohlcv")
→ {"max_hold": [Load("old_signal"), Exit()]}
```

**Right** (counts from this trade's entry):

```python fragment
TradeManager(entries="entries", prices="ohlcv")
→ {"max_hold": [BarsHeld(), AboveThresholdFilter(threshold=48, inclusive=True), Exit()]}
```

The validator accepts both; the difference is what the number means.

## Polarity Mistakes

- **Carry**: Always NegateTransform after FundingDataLoader — positive funding
  means longs pay shorts, so carry strategies SHORT high-funded assets.
- **Mean reversion**: NegateTransform after RSI/oscillators — RSI high =
  overbought, but mean reversion wants to BUY oversold (low RSI → positive forecast).
