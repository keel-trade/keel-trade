<!-- keywords: risk, IDM, inertia, portfolio, aggregator, leverage, weight, converter, sparse, dense, PositionInertia, IDMPortfolioAggregator -->
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

## PositionInertia

**What**: Reduces unnecessary trading by only rebalancing when positions drift
beyond a threshold.

**When to add**: When backtest shows high turnover or trading costs matter.

**Key params**: threshold (0.1-0.3), mode ("relative"), rebalance_method ("to_edge").

## DenseToSparseConverter

**What**: Strips zero-weight entries from WeightSeries for efficient backtest
execution.

**When to add**: Always, as the last step before backtest. Reduces computation
by only tracking non-zero positions.

## Full Position Pipeline

```python
def position_pipeline():
    return Pipeline([
        {"return_vol": [
            Load("ohlcv_1d"),
            ReturnVolatility(window="36d"),
            Store("return_vol"),
        ]},
        VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
        IDMPortfolioAggregator(
            forecast_slot="forecast_combined", return_vol_slot="return_vol",
            ohlcv_slot="ohlcv_1d", target_vol=0.25,
        ),
        PositionInertia(threshold=0.30, mode="relative"),
        LeverageCap(max_leverage=5.0),
        DenseToSparseConverter(tolerance=0.00000001),
    ])
```

## Common Mistakes

- Adding IDMPortfolioAggregator to a single-signal strategy — diversification
  benefit requires multiple positions. Start simple.
- Setting PositionInertia threshold too low (< 0.05) — defeats the purpose
  by rebalancing too frequently.
- Forgetting VolTargetWeightConverter — without it, forecasts are not
  converted to vol-targeted portfolio weights. The backtester needs WeightSeries.
