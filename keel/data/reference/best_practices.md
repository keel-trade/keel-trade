# Strategy Best Practices

Guidelines for building robust strategies.

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

There is no pipeline-level warmup setting. A component's `min_periods` (where
it has one) keeps its output NaN until that many bars exist, and a backtest
computes weights over at least 500 bars of the target timeframe (at least 10
days, at most a year), so a short window starts from history computed before
it; a window already that long carries its own warm-up in its first bars.

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

A component's detail carries its signal sub-category (`sub_category`):

- Combine across families: trend + mean_reversion + carry
- Avoid stacking: 3 momentum signals with different windows is still one idea

## Common Pipeline Mistakes

### Missing ForecastCapper

Every strategy should cap forecasts. Without capping, extreme outliers
cause outsized positions:

```python fragment
ForecastScaler(avg_abs_target=10.0) → ForecastCapper(limit=20.0)   # always cap after scaling
```

### Missing Normalization

Combining signals on different scales produces garbage forecasts.
Always normalize before combining (the `normalization` topic).

## Position Sizing

### Volatility Targeting

The standard approach uses two components to target portfolio-level volatility:

```python fragment
# 1. Compute return volatility per instrument
{"return_vol": [Load("ohlcv_1d"), ReturnVolatility(window="36d"), Store("return_vol")]}
# 2. Convert forecast to vol-targeted weights, then cap the book
→ VolTargetWeightConverter(return_vol_slot="return_vol", pct_target=0.25) → LeverageCap(max_leverage=2.0)
```

Lower targets = more conservative: smaller positions, lower portfolio volatility.
Typical crypto strategies use 0.15-0.30 (15-30% annual vol).

### Leverage Caps

`LeverageCap(max_leverage=...)` bounds the book's gross leverage (the sum of
absolute weights) on every bar, scaling all weights down together when it is
exceeded; an uncapped book carries whatever leverage the sizer produces, up
to the platform's 10x gross limit (backtest and live scale a bar above it
down to 10x pro-rata). `max_leverage` accepts 0.1-10 (default 5), and a
sizer's `target_leverage` accepts at most 5.

- Conservative: 1x-2x
- Moderate: 2x-5x

## ForecastScaler / ForecastCapper Usage

### Standard Pipeline

The canonical signal-to-forecast pipeline:

```text
Signal → Normalize → ForecastMapper → ForecastScaler → ForecastCapper
```

### ForecastScaler

- `avg_abs_target=10.0` (convention: mean absolute forecast = 10)
- `method="MAD"` for robustness against outliers
- `pool="global"` unless assets have fundamentally different signal scales (`pool="by_asset"`)

### ForecastCapper

- `limit=20.0` (convention: forecasts clipped to [-20, +20])
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

Available regime detectors (the component search returns the current set):

- `FundingLevelRegime`: crypto funding rate levels
- `RealizedVolatilityRegime`: historical volatility regimes, emitted ANNUALIZED
  (`1.0` = 100% annualized vol) off the declared `Globals(target_timeframe)`,
  so an absolute threshold such as `index < 1.0` means the same thing on every
  clock
- `FundingDispersionRegime`: funding rate dispersion across assets

### Regime Integration Pattern

The regime detector is a branch beside the signals; the blender reads the
branches by key (a validated example is in the `regime_conditioning` pattern):

```python fragment
{"trend": [...], "carry": [...],
 "regime": [FundingDataLoader(), Store("funding_level_funding_data"), FundingLevelRegime(lookback=20)]}
→ RegimeWeightedBlender(signal_a_key="trend", signal_b_key="carry", regime_key="regime")
```

Keep regime detection simple — one or two indicators. Complex regime
models are prone to overfitting.
