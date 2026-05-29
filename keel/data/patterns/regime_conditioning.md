<!-- keywords: regime, condition, blender, gate, funding, volatility, adaptive, market, RegimeWeightedBlender, RegimeGate -->
<!-- pattern: regime_conditioning -->

# Regime Conditioning

Adjust strategy behavior based on detected market regime. Regime conditioning
ENHANCES alpha — it does NOT replace it. Always build and test the base signal
first, then add regime conditioning as an improvement.

## Two Approaches

**Soft modulation (RegimeWeightedBlender)**: Smoothly adjusts the weight between
two signals based on regime. Preferred — more stable, fewer regime whipsaws.

**Hard switching (RegimeGate)**: Fully enables/disables a signal based on regime.
Use only when a signal truly fails in certain regimes (rare).

## Available Regime Detectors

- **FundingLevelRegime** (lookback=20): Detects funding rate regime (high/low/neutral).
  Handles polarity internally — no NegateTransform needed.
- **RealizedVolatilityRegime** (window=30): Detects vol regime (high/low).
- **ADXRegime** (period=14, threshold=25): Detects trending vs ranging.

Prefer single-component detectors over composites for simplicity and robustness.

## Pattern Structure

```
{
    "signal_a": [...],    # First signal branch (e.g., trend)
    "signal_b": [...],    # Second signal branch (e.g., carry)
    "regime": [...],      # Regime detector branch
}
→ RegimeWeightedBlender(signal_a_key, signal_b_key, regime_key, ...)
```

## Minimal Example

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    {
        "trend": Pipeline([
            Load("ohlcv_1d"),
            ROC(period=20),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
            ForecastCapper(limit=20.0),
        ]),
        "carry": Pipeline([
            FundingDataLoader(use_cache=True),
            TargetSignalResampler(method="mean"),
            NegateTransform(),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
            ForecastCapper(limit=20.0),
        ]),
        "regime": Pipeline([
            FundingDataLoader(use_cache=True),
            Store("funding_level_funding_data"),
            FundingLevelRegime(lookback=20),
        ]),
    },
    RegimeWeightedBlender(
        signal_a_key="trend", signal_b_key="carry", regime_key="regime",
    ),
    ForecastCapper(limit=20.0),
], name="regime_conditioned")
```

## Common Mistakes

- Adding regime detection before having a working base signal.
  Value hierarchy: Alpha > Breadth > Conditioning.
- Using RegimeGate (hard on/off) when RegimeWeightedBlender (soft) would be
  more stable — regime transitions are noisy.
- Forgetting `Store("funding_level_funding_data")` before FundingLevelRegime —
  the detector reads funding data from this specific slot.
