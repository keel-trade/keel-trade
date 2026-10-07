<!-- keywords: risk, IDM, turnover, buffer, portfolio, aggregator, leverage, weight, cap, IDMPortfolioAggregator, LeverageCap -->
<!-- pattern: risk_management -->

# Risk and Position Management

Components that manage risk, reduce turnover, and enforce portfolio constraints.
Add these AFTER the strategy produces valid weights — they are refinements,
not foundations.

## IDMPortfolioAggregator

**What**: Instrument Diversification Multiplier. Scales up positions to capture
the diversification benefit of a multi-asset portfolio.

**When to add**: When you have multiple uncorrelated positions and want full
diversification credit.

**Key params**: target_vol, window, shrinkage (oas recommended), cap (max IDM).

## Buffered execution (turnover)

**What**: Trades only when a position drifts outside a band around its
target — `Execution(rebalance="buffered", buffer_threshold=0.2,
buffer_mode="relative", rebalance_method="to_edge")`. It is a declaration,
not a pipeline step.

**When to add**: When backtest shows high turnover or trading costs matter.

## LeverageCap

**What**: Caps the book's gross leverage (`max_leverage`) and, optionally,
any single asset's weight (`max_asset_weight`).

**When to add**: After any sizer whose book can grow with the position count
(VolTargetWeightConverter, FixedWeightSizer, IDMPortfolioAggregator).

## Full Position Pipeline

```python fragment
Store("forecast_combined")
→ {"return_vol": [Load("ohlcv_1d"), ReturnVolatility(window="36d"), Store("return_vol")]}
→ Load("forecast_combined")
→ VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25)
→ IDMPortfolioAggregator(forecast_slot="forecast_combined", return_vol_slot="return_vol",
                         ohlcv_slot="ohlcv_1d", target_vol=0.25)
→ LeverageCap(max_leverage=5.0)
```

## Common Mistakes

- Adding IDMPortfolioAggregator to a single-signal strategy — diversification
  benefit requires multiple positions. Start simple.
- Setting a buffer threshold too low (< 0.05) — defeats the purpose by
  rebalancing too frequently.
- Forgetting VolTargetWeightConverter — without it, forecasts are not
  converted to vol-targeted portfolio weights. The backtester needs WeightSeries.
