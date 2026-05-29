# Signal Normalization

Signal normalization ensures different signals are comparable before combining.
Without normalization, a signal ranging -100 to +100 dominates one ranging -1 to +1.

## When to Normalize

Normalize signals **after** generation but **before** combining:

```yaml
# Correct: normalize each signal, then combine
steps:
  - ROC:
      period: 20
  - CrossSectionalZScore: {}     # normalize
  - Store:
      slot: momentum
  - RSI:
      period: 14
  - RollingZScore:               # normalize
      window: 60
  - Store:
      slot: mean_reversion
  - ForecastCombiner:
      slots: [momentum, mean_reversion]
```

**Never normalize after Store** — Store saves the raw value; normalize before storing.

**Never double-normalize** — applying CrossSectionalZScore then RollingZScore
on the same signal distorts the distribution. Pick one method per signal.

## Normalization Methods

### CrossSectionalZScore

Standardizes across assets at each timestamp. Best for:
- Relative value signals (momentum vs peers)
- Signals where cross-asset ranking matters
- Any signal used in a cross-sectional portfolio

```yaml
- CrossSectionalZScore: {}
```

No parameters. Output has mean ~0 and std ~1 across assets at each bar.

### RollingZScore

Standardizes each asset against its own history. Best for:
- Absolute signals (RSI, Bollinger %B)
- Signals where the asset's own distribution matters
- Time-series momentum strategies

```yaml
- RollingZScore:
    window: 60    # lookback in bars
```

**Window selection**: Use 2-4x the signal's own lookback.
A 20-period ROC works well with a 60-bar RollingZScore window.

### VolatilityStandardizer

Divides signal by its rolling volatility estimate. Best for:
- Raw price-based signals before forecast mapping
- Signals with regime-dependent volatility
- When you want to preserve signal direction but stabilize magnitude

```yaml
- VolatilityStandardizer:
    window: 60
    signal_type: returns    # "returns" or "levels"
    returns: pct            # "pct" or "log" (only when signal_type=returns)
```

**signal_type parameter**:
- `returns` (default): Signal represents returns; divides by return volatility
- `levels`: Signal represents price levels; applies return-based vol scaling

### ForecastScaler

Scales forecasts to a target absolute value (default 10). Applied **after**
normalization and forecast mapping, **before** ForecastCapper:

```yaml
- ForecastScaler:
    avg_abs_target: 10.0    # target mean absolute forecast
    method: MAD             # "MAD" or "mean"
    pool: global            # "global" or "by_asset"
```

**method**: MAD (median absolute deviation) is more robust to outliers.
**pool**: `global` uses all assets to estimate scale; `by_asset` estimates per-asset.

### ForecastCapper

Clips forecast to symmetric bounds. Always use after ForecastScaler:

```yaml
- ForecastCapper:
    limit: 20.0    # clips to [-20, +20]
```

**Standard pipeline**: Signal → Normalize → ForecastMapper → ForecastScaler → ForecastCapper

## Common Mistakes

### 1. Double normalization

```yaml
# WRONG: two normalizations on same signal
- ROC:
    period: 20
- CrossSectionalZScore: {}
- RollingZScore:
    window: 60
```

Fix: pick one. Use CrossSectionalZScore for relative, RollingZScore for absolute.

### 2. Normalizing after Store

```yaml
# WRONG: Store saves un-normalized value
- ROC:
    period: 20
- Store:
    slot: momentum
- CrossSectionalZScore: {}    # this normalizes nothing useful
```

Fix: normalize before Store.

### 3. Missing normalization before combining

```yaml
# WRONG: signals on different scales combined directly
- ROC:
    period: 20
- Store:
    slot: momentum
- RSI:
    period: 14
- Store:
    slot: mean_reversion
- ForecastCombiner:
    slots: [momentum, mean_reversion]
```

Fix: add normalization before each Store.

### 4. Wrong VolatilityStandardizer signal_type

Using `signal_type: levels` on a returns-based signal (or vice versa)
produces incorrect scaling. Match the parameter to your signal's nature.
