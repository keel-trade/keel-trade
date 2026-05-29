<!-- keywords: position, sizing, volatility, weight, allocator, VolTargetWeightConverter, ReturnVolatility, EqualWeightAllocator, ForecastWeightNormalizer, risk -->
<!-- pattern: position_sizing -->

# Position Sizing Paths

Convert signals or forecasts into portfolio weights (WeightSeries). The choice
depends on your signal type, desired complexity, and whether you need vol targeting.

## Path A: ForecastWeightNormalizer (for simple forecast strategies)

Best for: Level 1-2 strategies where you want forecast-proportional weights
without vol targeting infrastructure.

```
ForecastSeries → ForecastWeightNormalizer(target_leverage=1.0) → WeightSeries
```

No slots required. Normalizes forecasts so abs(weights) sum to target_leverage.
Stronger forecasts get proportionally larger positions.

## Path B: ReturnVolatility + VolTargetWeightConverter (for production strategies)

Full Carver-style position sizing with vol targeting and diversification.

```
{"return_vol": [Load("ohlcv_1d"), ReturnVolatility(window="36d"), Store("return_vol")]},
VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
IDMPortfolioAggregator(forecast_slot="forecast_combined", return_vol_slot="return_vol", ohlcv_slot="ohlcv_1d"),
LeverageCap(max_leverage=5.0),
```

Requires slot: `return_vol` (from ReturnVolatility). Use when: multiple signals,
want diversification benefit, production deployment.

## Path C: EqualWeightAllocator (for screen-select strategies)

Best for: Screen-select strategies where all selected assets get
equal allocation.

```
SelectionSignal → EqualWeightAllocator → WeightSeries
```

## Path D: EqualWeightAllocator (for factor tilts)

Best for: Direct signal-to-weight without forecast scaling. All selected
assets get the same allocation.

```
SignalSeries → EqualWeightAllocator → WeightSeries
```

## Path E: Entry/Exit Sizers (for discrete entry/exit strategies)

Best for: Strategies using PositionStateMachine or stateless ThresholdCross.
These sizers convert BinarySignal (+1/-1/0) to WeightSeries.

```
BinarySignal → EqualWeightSizer(target_leverage=1.0) → WeightSeries
BinarySignal → FixedWeightSizer(weight_per_position=0.1) → WeightSeries
BinarySignal → VolWeightSizer(vol_slot='vol') → WeightSeries
BinarySignal → RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02) → WeightSeries
```

| Sizer | Behavior | Requires vol slot? | Supports max_weight? |
|-------|----------|--------------------|---------------------|
| `EqualWeightSizer` | Splits target_leverage evenly across active positions | No | Yes |
| `FixedWeightSizer` | Fixed weight per position, stacks with count | No | No (weight is already fixed) |
| `VolWeightSizer` | Inverse-vol weighting, equal risk per position | Yes | Yes |
| `RiskBudgetSizer` | Fixed vol budget per position | Yes | No (use LeverageCap) |

`max_weight` caps the absolute weight of any single position. Excess goes to
cash (gross leverage decreases), not redistributed. Use when position count
varies and you want to prevent concentration in few survivors.

Vol-based sizers (`VolWeightSizer`, `RiskBudgetSizer`) require
`ReturnVolatility() → Store('vol')` upstream. `BinaryToWeight` is deprecated
— use `EqualWeightSizer` instead.

## Path F: Entry/Exit Sizers with ScalingPositionManager (for multi-level positions)

Best for: DCA, pyramiding, laddered entries — strategies that scale into positions over time.
ScalingPositionManager outputs integer levels {-N, ..., 0, ..., +N}. Sizers handle this
without code changes — `positions * weight_per_position` naturally scales.

```
PositionLevel → FixedWeightSizer(weight_per_position=0.1) → WeightSeries
```
Level 1 → 0.1, Level 2 → 0.2, Level 3 → 0.3. EqualWeightSizer also works —
it counts total levels across assets for proportional weighting.

## When to Use Each

| Signal Type | Sizing Method | Why |
|------------|--------------|-----|
| ForecastSeries (simple) | ForecastWeightNormalizer | Preserves conviction, zero setup |
| ForecastSeries (production) | ReturnVolatility + VolTargetWeightConverter | Adds vol targeting, IDM, leverage caps |
| Combined forecasts (simple) | ForecastCombiner → ForecastCapper → ForecastWeightNormalizer | Quick multi-signal path |
| Combined forecasts (production) | ForecastCombiner → FDM → ForecastCapper → VolTargetWeightConverter chain | Full production sizing |
| BinarySignal (entry/exit) | EqualWeightSizer | Default for PSM-based strategies |
| BinarySignal (mixed-vol) | VolWeightSizer | Equal risk contribution across positions |
| Selections (screen-select) | EqualWeightAllocator | Equal allocation to selected assets |
| TopN + conviction weighting | TopN → ForecastWeightNormalizer | Weight selected assets by signal strength |
| Raw signal (factor tilt) | EqualWeightAllocator | Equal allocation to all assets |

## Common Mistakes

- **M-01**: EqualWeightAllocator after continuous ForecastSeries — destroys signal
  information. Use ForecastWeightNormalizer instead. EqualWeightAllocator IS correct
  for TopN/filter scenarios where selection is the signal.
- Missing `return_vol` slot when using VolTargetWeightConverter — must compute
  return volatility first via ReturnVolatility (usually in a Parallel block with Store).
- Skipping DenseToSparseConverter — produces weights for all assets including zeros,
  which wastes backtest computation.
