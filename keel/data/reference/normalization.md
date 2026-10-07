# Signal Normalization

Signal normalization ensures different signals are comparable before combining.
Without normalization, a signal ranging -100 to +100 dominates one ranging -1 to +1.

## When to Normalize

Normalize signals **after** generation but **before** combining — each branch
normalizes its own signal, then the composer combines comparable values:

```python fragment
{"momentum":       [ROC(period=20), CrossSectionalZScore(), ForecastScaler()],          # normalize
 "mean_reversion": [RSI(period=14), RollingZScoreTransform(window=60), NegateTransform(), ForecastScaler()]}
→ ForecastCombiner(weights={"momentum": 0.5, "mean_reversion": 0.5})
```

**Normalize before you Store** — Store saves the value as it is at that point;
a normalizer after the Store does not change what a later reader loads.

**Never double-normalize** — applying CrossSectionalZScore then
RollingZScoreTransform on the same signal distorts the distribution. Pick one
method per signal.

## Normalization Methods

### CrossSectionalZScore

Standardizes across assets at each timestamp. Best for:

- Relative value signals (momentum vs peers)
- Signals where cross-asset ranking matters
- Any signal used in a cross-sectional portfolio

```python fragment
CrossSectionalZScore()
```

Output has mean ~0 and std ~1 across assets at each bar. Degenerate with few
assets — under five, prefer RollingZScoreTransform or
`ForecastScaler(pool="by_asset")`.

### RollingZScoreTransform

Standardizes each asset against its own history. Best for:

- Absolute signals (RSI, Bollinger %B)
- Signals where the asset's own distribution matters
- Time-series momentum strategies

```python fragment
RollingZScoreTransform(window=60)    # lookback in bars
```

**Window selection**: Use 2-4x the signal's own lookback.
A 20-period ROC works well with a 60-bar window.

### VolatilityStandardizer

Divides a signal by the asset's rolling volatility (read from an OHLCV slot).
Best for:

- Raw price-based signals before forecast mapping
- Signals with regime-dependent volatility
- When you want to preserve signal direction but stabilize magnitude

```python fragment
VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d", window="36d", returns="pct")
```

**signal_type parameter** (required):

- `price_points`: the signal is in price units (an EWMA crossover, a price
  difference); it is divided by volatility × price
- `percentage`: the signal is already a return or percentage (ROC); it is
  divided by the percentage volatility

### ForecastScaler

Scales forecasts to a target absolute value (default 10). Applied **after**
normalization and forecast mapping, **before** ForecastCapper:

```python fragment
ForecastScaler(avg_abs_target=10.0, method="MAD", pool="global")
```

**method**: `MAD` (median absolute deviation) is more robust to outliers than `mean`.
**pool**: `global` uses all assets to estimate scale; `by_asset` estimates per-asset.

### ForecastCapper

Clips forecast to symmetric bounds. Always use after ForecastScaler:

```python fragment
ForecastCapper(limit=20.0)    # clips to [-20, +20]
```

**Standard pipeline**: Signal → Normalize → ForecastMapper → ForecastScaler → ForecastCapper

## Common Mistakes

### 1. Double normalization

```python fragment
# WRONG: two normalizations on same signal
ROC(period=20) → CrossSectionalZScore() → RollingZScoreTransform(window=60)
```

Fix: pick one. Use CrossSectionalZScore for relative, RollingZScoreTransform for absolute.

### 2. Missing normalization before combining

```python fragment
# WRONG: signals on different scales combined directly
{"momentum": [ROC(period=20)], "mean_reversion": [RSI(period=14)]}
→ ForecastCombiner(weights={"momentum": 0.5, "mean_reversion": 0.5})
```

Fix: normalize (and scale) inside each branch before the combiner.

### 3. Wrong VolatilityStandardizer signal_type

Using `signal_type="price_points"` on a percentage signal (or vice versa)
produces incorrect scaling. Match the parameter to your signal's units.
