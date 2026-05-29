<!-- keywords: momentum, trend, forecast, scaling, capping, continuous, signal, ewmac, roc -->
<!-- pattern: forecast_pipeline -->

# Continuous Forecast Pipeline

The most common path from data to weights. Use when signal values carry conviction
(stronger signal = larger position). Best for trend-following and momentum strategies.

## Component Sequence

1. **PriceDataLoader** - Load OHLCV data
2. **Indicator** (ROC, EWMACrossover, EWMA+Crossover) - Generate raw signal
3. **VolatilityStandardizer** - Remove volatility scaling from signal (optional but recommended)
4. **CrossSectionalZScore** - Normalize across assets so signals are comparable
5. **ForecastScaler** (avg_abs_target=10.0) - Scale to standard forecast range
6. **ForecastCapper** (limit=20.0) - Clip extreme values
7. **ForecastWeightNormalizer** (target_leverage=1.0) - Convert forecast to portfolio weights

For production vol-targeted sizing (Level 3+), replace step 7 with:
ReturnVolatility → VolTargetWeightConverter (requires return_vol slot).

## Why Each Step Matters

- **Normalize before scaling**: Different indicators produce different scales. Without
  CrossSectionalZScore, ForecastScaler can't find a stable scaling factor.
- **Scale then cap**: ForecastScaler targets avg |forecast| = 10. ForecastCapper clips
  to [-20, +20]. Always use both together.
- **ForecastWeightNormalizer preserves signal**: Unlike EqualWeightAllocator, it translates
  forecast magnitude into weight proportion — stronger signals get larger positions.
  Unlike VolTargetWeightConverter, it requires no slots (zero setup).

## Minimal Example

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="simple_momentum")
```

## Common Mistakes

- **M-01**: Using EqualWeightAllocator after ForecastSeries discards conviction
  magnitude. Use ForecastWeightNormalizer (simple) or VolTargetWeightConverter (production).
  EqualWeightAllocator is correct for TopN/filter scenarios (Path 3).
- Skipping CrossSectionalZScore when combining multiple signals — signals at
  different scales will dominate unpredictably.
- Double normalization (VolatilityStandardizer AND CrossSectionalZScore AND
  RollingZScore) — pick one normalization method.
