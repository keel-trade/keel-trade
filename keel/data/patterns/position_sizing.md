<!-- keywords: position, sizing, volatility, weight, allocator, VolTargetWeightConverter, ReturnVolatility, EqualWeightAllocator, ForecastWeightNormalizer, risk -->
<!-- pattern: position_sizing -->

# Position Sizing Paths

Convert signals or forecasts into portfolio weights (WeightSeries). The choice
depends on your signal type, desired complexity, and whether you need vol targeting.

## Path A: ForecastWeightNormalizer (for simple forecast strategies)

Best for: Level 1-2 strategies where you want forecast-proportional weights
without vol targeting infrastructure.

```text
ForecastSeries → ForecastWeightNormalizer(target_leverage=1.0) → WeightSeries
```

No slots required. Normalizes forecasts so abs(weights) sum to target_leverage.
Stronger forecasts get proportionally larger positions.

## Path B: ReturnVolatility + VolTargetWeightConverter (for vol-targeted strategies)

Full Carver-style position sizing with vol targeting and diversification.

```python fragment
{"return_vol": [Load("ohlcv_1d"), ReturnVolatility(window="36d"), Store("return_vol")]}
→ Load("forecast_combined")
→ VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)
→ IDMPortfolioAggregator(forecast_slot="forecast_combined", return_vol_slot="return_vol", ohlcv_slot="ohlcv_1d")
→ LeverageCap(max_leverage=5.0)
```

Requires slots: `return_vol` (from ReturnVolatility) and the stored combined
forecast (`Store("forecast_combined")` after the combiner). Use when: multiple
signals, want diversification benefit, a portfolio volatility target.

## Path C: EqualWeightAllocator (for screen-select strategies)

Best for: Screen-select strategies where all selected assets get
equal allocation.

```text
TopNAssetSelector mask → EqualWeightAllocator → WeightCadence(duration="7d") → FillNaN(fill_value=0.0) → WeightSeries
```

## Path D: EqualWeightAllocator (for factor tilts)

Best for: Direct signal-to-weight without forecast scaling. All selected
assets get the same allocation.

```text
SignalSeries → EqualWeightAllocator → WeightSeries
```

## Path E: Entry/Exit Sizers (for discrete entry/exit strategies)

Best for: TradeManager or stateless ThresholdCross. These sizers convert a
BinarySignal (+1/-1/0), or a TradeManager's `Exposure()`, to WeightSeries.
`RiskSizer` takes the Position directly, without `Exposure()`.

```text
BinarySignal → EqualWeightSizer(target_leverage=1.0) → WeightSeries
BinarySignal → FixedWeightSizer(weight_per_position=0.1) → WeightSeries
BinarySignal → VolWeightSizer(vol_slot='vol') → WeightSeries
BinarySignal → RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02) → WeightSeries
```

| Sizer              | Behavior                                                                                                                             | Requires vol slot?             | Supports max_weight?                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------ | ------------------------------------------------------------------------- |
| `EqualWeightSizer` | Splits target_leverage evenly across active positions                                                                                | No                             | Yes                                                                       |
| `FixedWeightSizer` | Fixed weight per position, stacks with count                                                                                         | No                             | No (weight is already fixed)                                              |
| `VolWeightSizer`   | Inverse-vol weighting, equal risk per position                                                                                       | Yes                            | Yes                                                                       |
| `RiskBudgetSizer`  | Fixed vol budget per position                                                                                                        | Yes                            | No (use LeverageCap)                                                      |
| `RiskSizer`        | Risk-per-trade: `risk / stop distance` sized ONCE at the trade's entry bar (the `distance` slot, in price units), held for the trade | No (reads the `distance` slot) | Yes (`max_weight` — load-bearing: a 1-tick stop must not become leverage) |

`max_weight` caps the absolute weight of any single position. Excess goes to
cash (gross leverage decreases), not redistributed. Use when position count
varies and you want to prevent concentration in few survivors.

`RiskSizer` is the R-culture rule ("risk 1% per trade"): risk ÷ the stop
distance sampled at the trade's entry bar, held for the trade; it never
re-sizes into a shrinking stop. `distance` is a stored slot in price units
(close − the stop level, or a multiple of ATR), the same slot an R-multiple
rule divides `TradePnL()` by through `AtEntry(slot=...)`. `max_weight` is
load-bearing. A missing or zero distance at entry refuses that entry.
`RiskBudgetSizer(risk_per_position=...)` sizes to a VOLATILITY budget — a
different rule from "1% risk".

## Path F: Sizing with adds and partials

Best for: DCA, pyramids and scale-outs (`ScaleIn`, `Reduce` below a TradeManager).
`Exposure()` is the units held, signed, so a sizer that keeps size scales with it:
`FixedWeightSizer` (weight = units held × `weight_per_position`), or the newest
`EqualWeightSizer`, `VolWeightSizer` and `RiskBudgetSizer`. An older pinned version that
erases size is refused with `SIZER_ERASES_SIZE`.

```text
Exposure → FixedWeightSizer(weight_per_position=0.1) → LeverageCap(max_leverage=...) → WeightSeries
```

1 unit → 0.1, 2 units → 0.2, 3 units → 0.3. `max_units` (derived from the `ScaleIn`
rules) bounds it.

## Path G: Hold / index basket (no signal)

A basket held at constant conviction — the "hold BTC" or index-basket
strategy — needs no signal component. `ConstantForecast` emits one
forecast value for every loaded asset; the normalizer sizes it.

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC"])
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(),
    ConstantForecast(value=10),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="hold_btc")
```

Equal weights across a basket: `EqualWeightSizer(target_leverage=1.0)` after
`ConstantForecast`, or `ForecastWeightNormalizer` as above (equal forecasts
normalize to equal weights). A selection sizer after a constant forecast
is clean by design — a constant forecast carries no conviction to discard,
which is what `FORECAST_MAGNITUDE_DISCARDED` checks. A `FixedWeightSizer`
here is uncapped — the book is `weight × N`, which `FIXED_WEIGHT_UNCAPPED`
names.

## When to Use Each

| Signal Type                       | Sizing Method                                                            | Why                                          |
| --------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------- |
| ForecastSeries (simple)           | ForecastWeightNormalizer                                                 | Preserves conviction, zero setup             |
| ForecastSeries (vol-targeted)     | ReturnVolatility + VolTargetWeightConverter                              | Adds vol targeting, IDM, leverage caps       |
| Combined forecasts (simple)       | ForecastCombiner → ForecastCapper → ForecastWeightNormalizer             | Quick multi-signal path                      |
| Combined forecasts (vol-targeted) | ForecastCombiner → FDM → ForecastCapper → VolTargetWeightConverter chain | Full vol-targeted sizing                     |
| BinarySignal (entry/exit)         | EqualWeightSizer                                                         | Default for TradeManager-based strategies    |
| BinarySignal (mixed-vol)          | VolWeightSizer                                                           | Equal risk contribution across positions     |
| Selections (screen-select)        | EqualWeightAllocator → WeightCadence → FillNaN                           | Equal allocation to selected assets          |
| TopN + conviction weighting       | ApplyMask(signal, TopN mask) → ForecastWeightNormalizer                  | Weight selected assets by signal strength    |
| Raw signal (factor tilt)          | EqualWeightAllocator                                                     | Equal allocation to all assets               |
| Hold / index basket (no signal)   | ConstantForecast → ForecastWeightNormalizer                              | Constant conviction, the normalizer sizes it |

## Common Mistakes

- Sizing a continuous forecast with an equal-weight sizer discards its
  conviction (the validator warns, `FORECAST_MAGNITUDE_DISCARDED`);
  EqualWeightAllocator IS correct for TopN/filter scenarios where selection is
  the signal.
- Pair and basket legs merged by WeightConcatenator: each leg ends in its own
  sizer, and the book's gross leverage is the sum of the leg targets.
