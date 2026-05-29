# Strategy Best Practices

Guidelines for building robust, production-quality strategies.

## Overfitting Prevention

### Parameter Count

More parameters = more overfitting risk. Rules of thumb:
- **Simple strategy**: 3-6 free parameters
- **Multi-signal blend**: 8-15 free parameters
- **Hierarchical/regime**: 15-25 free parameters

If your strategy has more than 25 free parameters, you are almost certainly
overfitting to historical data.

### In-Sample / Out-of-Sample

Always split your backtest period:
- **In-sample** (first 60-70%): fit and tune parameters
- **Out-of-sample** (last 30-40%): validate — never re-tune after seeing results

A strategy that performs well in-sample but poorly out-of-sample is overfit.

### Cross-Validation Signals

Watch for these overfitting indicators:
- Sharpe ratio drops >50% from in-sample to out-of-sample
- Parameters are at extreme values (hitting min/max bounds)
- Strategy only works on a narrow date range
- Adding parameters improves in-sample but not out-of-sample

## Signal Quality

### Minimum Lookback

Every signal needs sufficient history to be meaningful:
- Trend signals (EWMA, MACD): minimum 2x the longest period
- Mean reversion (RSI, Bollinger): minimum 1.5x the window
- Statistical (Hurst, correlation): minimum 3x the window

Set pipeline warmup accordingly to avoid noisy early bars.

### Autocorrelation

Good trading signals have moderate positive autocorrelation — the forecast
today should be similar to yesterday's. Check by examining signal persistence:
- Too low (< 0.3): signal is noise, trades too frequently
- Good range (0.3-0.8): signal persists but adapts
- Too high (> 0.95): signal barely changes, may be stale

### Signal Diversity

When combining multiple signals, prefer signals with **low correlation**
to each other. Three uncorrelated Sharpe-0.5 signals combine better
than three correlated Sharpe-1.0 signals.

Use `strategy_component_detail` to check signal sub-categories:
- Combine across families: trend + mean_reversion + carry
- Avoid stacking: 3 momentum signals with different windows is still one idea

## Common Pipeline Mistakes

### Missing ForecastCapper

Every strategy should cap forecasts. Without capping, extreme outliers
cause outsized positions:

```yaml
# Always include after ForecastScaler
- ForecastScaler:
    avg_abs_target: 10.0
- ForecastCapper:
    limit: 20.0
```

### Wrong Phase Ordering

Components must follow the 14-phase ordering. Common mistakes:
- SIGNAL before DATA_LOADER (no data to compute signal from)
- PORTFOLIO before RISK (no risk adjustment on positions)
- POSITION_SIZER before PORTFOLIO (sizing before allocation)

Use `dsl_reference('phases')` to check the correct order.
Use `strategy_validate` — it catches ordering violations.

### Missing Normalization

Combining signals on different scales produces garbage forecasts.
Always normalize before combining. See `dsl_reference('normalization')`.

### Unused Slots

Storing a value in a slot but never loading it wastes computation
and confuses readers. The validator catches this as a warning.

## Position Sizing

### Volatility Targeting

The standard approach uses two components to target portfolio-level volatility:

```python
# 1. Compute return volatility per instrument
{"return_vol": [Load("ohlcv_1d"), ReturnVolatility(window="36d"), Store("return_vol")]},

# 2. Convert forecast to vol-targeted weights
VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25),
```

Lower targets = more conservative, higher risk-adjusted returns.
Typical crypto strategies use 0.15-0.30 (15-30% annual vol).

### Leverage Caps

Always cap maximum leverage to prevent blow-ups:
- Conservative: 1x-2x
- Moderate: 2x-5x
- Aggressive: 5x-10x (requires careful risk management)

Never run uncapped leverage in production.

## ForecastScaler / ForecastCapper Usage

### Standard Pipeline

The canonical signal-to-forecast pipeline:

```
Signal → Normalize → ForecastMapper → ForecastScaler → ForecastCapper
```

### ForecastScaler

- Set `avg_abs_target: 10.0` (convention: mean absolute forecast = 10)
- Use `method: MAD` for robustness against outliers
- Use `pool: global` unless assets have fundamentally different signal scales

### ForecastCapper

- Set `limit: 20.0` (convention: forecasts clipped to [-20, +20])
- A forecast of 20 means "maximum confidence long"
- A forecast of -20 means "maximum confidence short"
- The 20/10 ratio means the strongest signal is 2x the average

### When to Adjust

- If your signal is naturally bounded (0-100 like RSI), you may use a
  ForecastMapper to convert the range before scaling
- If combining many signals, the ForecastCombiner applies its own
  diversification multiplier — the combined forecast still targets ±20

## Regime Detection

### When to Add Regime

Add regime detection when:
- Strategy should behave differently in trending vs mean-reverting markets
- You have a clear hypothesis about regime indicators
- Strategy has enough signals (3+) to differentiate regime behavior

### Regime Components

Available regime detectors (check with `strategy_components_search`):
- `FundingLevelRegime`: crypto funding rate levels
- `RealizedVolatilityRegime`: historical volatility regimes
- `FundingDispersionRegime`: funding rate dispersion across assets
- `StressCompositeRegime`: multi-factor stress indicator

### Regime Integration Pattern

```yaml
# Detect regime
- FundingLevelRegime:
    lookback: 20
- Store:
    slot: regime_signal
# Use in blending
- RegimeWeightedBlender:
    regime_slot: regime_signal
    slots: [aggressive_forecast, defensive_forecast]
```

Keep regime detection simple — one or two indicators. Complex regime
models are prone to overfitting.
