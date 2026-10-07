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
- **RealizedVolatilityRegime** (lookback=20): Detects vol regime (high/low).
  Emits ANNUALIZED vol, so its thresholds are absolute: `1.0` means 100%
  annualized vol. From v2 it annualizes off the declared
  `Globals(target_timeframe)`, so a threshold picked on one clock keeps its
  meaning on another (v1 hardcoded `sqrt(365)` and read 9.80x low at 15min).
  It reads the bars in its slot, so those must be on the declared clock — to
  measure vol from FINER bars, use `RealizedVolatility` instead.
- **MarketTrendRegimeFilter** (fast_period, slow_period): market-wide trend state.
- The slot-reading detectors (`RealizedVolatilityRegime`,
  `AveragePairwiseCorrelationRegime`, `CrossSectionalDispersionRegime`) read
  OHLCV from a slot and take a SignalSeries as their flow input: in their
  branch, `ExtractSeries(series_name="close")` goes first.

Prefer single-component detectors over composites for simplicity and robustness.

## Pattern Structure

```python fragment
{
    "signal_a": [...],    # First signal branch (e.g., trend)
    "signal_b": [...],    # Second signal branch (e.g., carry)
    "regime": [...],      # Regime detector branch
}
→ RegimeWeightedBlender(signal_a_key="signal_a", signal_b_key="signal_b", regime_key="regime")
```

**One clock at the blend.** Every branch must arrive on the timeframe
`Globals(target_timeframe=...)` declares before it is combined — a blender or
combiner rejects inputs on different clocks (`CLOCK_MISMATCH`). The loaders
already serve the declared clock; only a branch pinned to its own
`timeframe=` needs bringing back — a finer one coarsened with
`TargetSignalResampler(method=...)`, a coarser computed regime projected down
with `TargetSignalProjector()`.

## Minimal Example

```python
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL", "AVAX", "LINK"])
Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge")

Pipeline([
    PriceDataLoader(),
    {
        "trend": [
            ROC(period=20),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
            ForecastCapper(limit=20.0),
        ],
        "carry": [
            FundingDataLoader(),
            NegateTransform(),
            CrossSectionalZScore(),
            ForecastScaler(avg_abs_target=10.0),
            ForecastCapper(limit=20.0),
        ],
        "regime": [
            FundingDataLoader(),
            Store("funding_level_funding_data"),
            FundingLevelRegime(lookback=20),
        ],
    },
    RegimeWeightedBlender(signal_a_key="trend", signal_b_key="carry", regime_key="regime"),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="regime_conditioned")
```

## Common Mistakes

- Adding regime detection before having a working base signal.
  Value hierarchy: Alpha > Breadth > Conditioning.
- Using RegimeGate (hard on/off) when RegimeWeightedBlender (soft) would be
  more stable — regime transitions are noisy.
- Forgetting `Store("funding_level_funding_data")` before FundingLevelRegime —
  the detector reads funding data from this specific slot.
