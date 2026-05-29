<!-- keywords: combine, blend, multi-signal, ensemble, ForecastCombiner, FDM, diversification, weights, parallel -->
<!-- pattern: combining_signals -->

# Multi-Signal Blending

Combine multiple signals into a single forecast, then size to weights.
Each signal must be independently normalized and scaled before combining.

## Component Sequence

1. **Parallel branches** - Each branch produces a ForecastSeries
2. **ForecastCombiner** (weights=...) - Weighted average of forecasts
3. **EmpiricalFDM** or **AnalyticalFDM** - Forecast diversification multiplier (optional, Level 2+)
4. **ForecastCapper** (limit=20.0) - Re-cap after combining
5. **ForecastWeightNormalizer** - Convert combined forecast to portfolio weights

For simple strategies (Level 1-2), skip step 3 — go straight from
ForecastCombiner to ForecastCapper to ForecastWeightNormalizer.

## Within-Family vs Across-Family

**Within a family** (e.g., EWMAC at 3 speeds): equal weights are fine — signals
share the same thesis and correlate highly.

**Across families** (trend + carry + mean reversion): set explicit weights.
5 trend signals + 1 carry signal with equal weights = 83% trend exposure.
Better: `{"trend": 0.5, "carry": 0.5}` at the family level.

## Hierarchical Combination

For 3+ signal families, combine in two levels:
1. **Level 1**: Combine within each family (e.g., 3 EWMAC speeds → 1 trend forecast)
2. **Level 2**: Combine family-level forecasts (trend + carry + MR → final forecast)

Use nested Parallel + ForecastCombiner at each level.

## FDM (Forecast Diversification Multiplier)

When combining correlated forecasts, the combined forecast has lower volatility
than individual forecasts. FDM scales up to compensate:
- **EmpiricalFDM**: Estimates from data. Simpler, works with 2+ signals.
  Place after ForecastCombiner as a separate sequential step.
- **AnalyticalFDMCombiner**: Combines forecasts AND applies analytical FDM
  (1/sqrt(w'Rw)) in one step. Preferred for explicit correlation-based FDM.
  Replaces the old ForecastCombiner + CorrelationEstimator + AnalyticalFDM pattern.

FDM is optional for simple strategies. Add it when you want to preserve
forecast scale after combination (Level 2+).

### Analytical FDM Example

```python
{
    "ewmac_2_8": [ewmac(2, 8)],
    "ewmac_4_16": [ewmac(4, 16)],
    "ewmac_8_32": [ewmac(8, 32)],
},
AnalyticalFDMCombiner(
    weights={"ewmac_2_8": 0.33, "ewmac_4_16": 0.34, "ewmac_8_32": 0.33},
    correlation_window="90d",
),
ForecastCapper(limit=20.0),
```

### Empirical FDM Example

```python
ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
EmpiricalFDM(window="90d"),
ForecastCapper(limit=20.0),
```

## Minimal Example

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(),
    {
        "momentum": [
            ROC(period=20),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
        ],
        "mean_reversion": [
            RSI(period=14),
            NegateTransform(),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
        ],
    },
    ForecastCombiner(weights={"momentum": 0.6, "mean_reversion": 0.4}),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="dual_signal")
```

## Common Mistakes

- Combining signals at different stages (one ForecastSeries, one SignalSeries).
  Normalize and scale ALL branches before combining.
- Forgetting ForecastCapper after ForecastCombiner — FDM can push values beyond 20.
- Equal weights across uncorrelated families when one family has many more signals.
