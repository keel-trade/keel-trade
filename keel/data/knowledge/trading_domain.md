## Trading Domain Knowledge

**Carry polarity**: Positive funding = longs pay shorts. Carry strategies SHORT high-funded assets to COLLECT funding. Always use NegateTransform after FundingDataLoader for carry signals. Without it, the strategy pays funding instead of earning it. Standard carry branch: `FundingDataLoader → TargetSignalResampler(method="mean") → NegateTransform → CrossSectionalZScore`. Exception: FundingLevelRegime (regime detector) handles polarity internally.

**Timeframe selection** — default to `1d` unless the user specifies otherwise. Longer timeframes have better signal-to-noise, lower transaction costs, and are more robust out-of-sample. Shorter timeframes overfit faster and amplify noise.

**Supported timeframes** (hard constraint — the platform ONLY supports these values for `target_timeframe`):
`15min`, `30min`, `1h`, `2h`, `3h`, `4h`, `6h`, `8h`, `12h`, `1d`

If the user asks for an unsupported timeframe (e.g. 1min, 3min, 5min, 3d, 1w), explain that it's not supported and use the nearest supported value instead. For example: "5-minute bars aren't supported — the minimum is 15min. I'll use 15min instead."

| Strategy type | Default | Acceptable range | Rationale |
|---|---|---|---|
| Trend / momentum | 1d | 8h–1d | Trends develop over days/weeks. Shorter bars add noise without signal. |
| Mean reversion | 1d | 4h–1d | Reversions happen faster than trends. Intraday MR (4h–8h) is reasonable if requested. |
| Carry / funding | 1d | 1d only | Funding rates are 8h events; daily aggregation is natural. |
| Screen-select / rotation | 1d | 1d only | Rotation signals are multi-day by nature. |

When to suggest a different timeframe (as a follow-up, not the initial build):
- **Too few trades** → suggest trying 12h or 8h before lowering thresholds. More bars = more threshold-crossing opportunities. Say: "You could try `target_timeframe='12h'` for more signal granularity — this doubles the number of bars the strategy evaluates."
- **Too many trades / high turnover** → suggest moving up to 1d if on a shorter timeframe.
- **User says "intraday"** → use 4h (not 1h). True intraday (1h–2h) needs strong justification — costs dominate, signal-to-noise drops, and the strategy is more fragile.
- **Never go below 4h** unless the user explicitly asks and understands the tradeoffs.
- **If user doesn't specify** → always use 1d. Don't proactively suggest shorter timeframes in initial builds.

**Default to trend-following**: When the user doesn't specify trend vs mean reversion, build a trend-following strategy. Crypto has structural momentum bias — trend strategies are more robust by default. Mean reversion should only be used when explicitly requested ("mean reversion", "overbought/oversold", "fade", "reversal", "contrarian").

**Oscillator polarity and NegateTransform**: Oscillators (RSI, Stochastic, WilliamsR, CMO) output HIGH = strong momentum / overbought, LOW = weak momentum / oversold. The interpretation depends on strategy type:
- **Trend-following (default)**: Do NOT negate. High RSI = strong momentum → go long. Low RSI = weak momentum → go short. The raw polarity is correct.
- **Mean reversion (explicit only)**: INVERT with NegateTransform so overbought → short (negative forecast), oversold → long (positive forecast). Use more extreme thresholds (±2.0 instead of ±1.5) to avoid trading noise. Without negation, a "mean reversion" RSI strategy actually buys overbought assets.
Exception: TimeSeriesMeanReversionForecast and WingsTransform handle inversion internally.

**Indicator-specific guidance**:
- **RSI**: Trend → no negate, threshold ±1.5. MR → negate, threshold ±2.0. RSI is a momentum oscillator; in crypto, momentum persistence is strong.
- **MACD**: Inherently a trend indicator (moving average crossover). Almost always used for trend confirmation or filtering, rarely inverted. As a filter: MACD histogram > 0 = bullish, < 0 = bearish. Use ThresholdCross(0.5, -0.5) on normalized MACD histogram.
- **Funding rates**: Always negate for carry (positive funding = shorts get paid). NegateTransform is required. This is not trend vs MR — it's the carry trade convention.

**Signal diversity > quantity**: Three uncorrelated Sharpe-0.5 signals beat three correlated Sharpe-1.0 signals. Prioritize different families (trend vs carry vs mean reversion) over parameter variants (EWMAC at 5 speeds). Lowest correlation pairs in crypto: trend vs carry, trend vs mean reversion, price-based vs funding-based.

**Required component pairs** (always use together):
- ForecastScaler + ForecastCapper (scale then cap)
- TopNAssetSelector + SelectionToSignalConverter (select then manage exits)
- VolatilityAdjustedPriceSeries + BreakoutDistance (vol-adjust then breakout)
- AnalyticalFDMCombiner (combine + analytical FDM in one step, replaces ForecastCombiner + CorrelationEstimator + AnalyticalFDM)
- Store + Load (matching slot names)

**Stream data alignment**: Funding/OI data (1h) must be resampled to the target timeframe before mixing with price data. Standard pattern: `FundingDataLoader → TargetSignalResampler(method="mean")`. TargetSignalResampler reads target_timeframe from Globals and handles resampling + alignment automatically. Choose method by data type: "mean" for rates (funding), "last" for levels (OI), "sum" for flows. The Universe selector passes the same resolved universe to all data loaders, so assets already match — AssetAligner is NOT needed in the standard case. Old pattern (EWMATransform → SignalResampleTransform → AssetAligner) is unnecessary with Universe selection.

**Multi-data shape alignment**: When Parallel branches use different data sources (price vs funding vs OI), use TargetSignalResampler in each branch to align timeframes. Assets already match because the Universe selector passes the same resolved list to all data loaders. AssetAligner is only needed when a component explicitly drops assets from the universe (VolumeUniverseReducer, GroupAssetFilter). In that case, Store the reduced OHLCV BEFORE the Parallel and use AssetAligner(reference_slot='ohlcv_1d') in other branches. Note: TopNAssetSelector does NOT change dimensions — it produces a mask, not a reduced universe.

**Complexity ladder** — match pipeline complexity to intent:
- Level 1 (single signal): 4-6 components, 2-4 params. Use ForecastWeightNormalizer for sizing.
- Level 2 (dual signal blend): 8-12 components, 5-8 params. ForecastCombiner → ForecastWeightNormalizer.
- Level 3 (multi-signal + vol targeting): 15-25 components. VolTargetWeightConverter → LeverageCap. For advanced portfolios: add IDM for diversification scaling.
- Level 4 (regime-adaptive portfolio): 25-40 components, 15-20 params
More than 25 free parameters almost certainly means overfitting. Suggest complexity ONE level at a time, not jumps.

**Regime models**: Prefer single-component detectors (FundingLevelRegime, RealizedVolatilityRegime) over composites. Prefer RegimeWeightedBlender (soft modulation) over RegimeGate (hard on/off). Regime conditioning enhances alpha — it does NOT replace it. Build and test base signal first. Value hierarchy: Alpha > Breadth > Conditioning > Risk Management.

**Hierarchical combination**: Equal weights within a signal family (e.g., EWMAC at 3 speeds) is fine. Across families (trend + carry + MR), set explicit weights — otherwise 5 trend + 1 carry = 83% trend. Use EmpiricalFDM after ForecastCombiner, or AnalyticalFDMCombiner to combine+FDM in one step (preferred for analytical FDM — single weights param, no duplication).

**Post-backtest reasoning — never overfit to results.** When a backtest shows losses in a specific period, DO NOT make changes designed to avoid that specific period. That is curve-fitting. Instead:

1. **Diagnose the mechanism, not the outcome.** "Shorts lost money in Nov 2024" is an outcome. "Mean reversion shorts during a parabolic breakout with no trend filter" is a mechanism. Fix mechanisms, not outcomes.
2. **Changes must have principled reasons independent of the backtest.** "Go long-only because the Nov 2024 wipeout was short-side" = overfitting. "Go long-only because crypto has a structural long bias and mean reversion shorts in trending markets are inherently risky" = principled. The change should make sense even without seeing the backtest.
3. **Removing trades to avoid losses is a red flag.** A strategy that takes fewer trades is more fragile and more likely to be overfit. If a strategy loses money in some periods, the first question is whether the signal was wrong or the risk management was missing — not whether to eliminate that side entirely.
4. **Prefer adding robustness over removing exposure.** Instead of "remove shorts," consider: add a trend filter, add position sizing, add a regime gate, reduce leverage. These address the mechanism (unfiltered mean reversion in strong trends) without eliminating half the strategy.
5. **Always state the principled reason.** When suggesting a post-backtest change, lead with the trading principle, not the backtest result. "Mean reversion strategies benefit from a trend filter because reversals fail in strong trends" — not "Adding this filter because the strategy lost money here."

**Match the suggestion to where the strategy is on its maturation arc.** Refinement on a non-thesis sands the edges of a strategy that has no edge. When the user is non-specific, locate the strategy on the arc (scoping / building / searching / iterating / refining / validating) and pick a move whose question matches the phase. See `strategy_phases.md` for the full arc and per-phase moves. User-directed moves always override phase.

**Position sizing for entry/exit strategies** (after PSM):

| Sizer | Use when | Example |
|-------|----------|---------|
| `EqualWeightSizer(target_leverage=1.0)` | Default. Simple equal weight. | Most strategies start here |
| `EqualWeightSizer(max_weight=0.3)` | Cap per-position concentration | Prevent 100% in 1 survivor |
| `EqualWeightSizer(target_leverage=0.5)` | Conservative / reduce risk | Testing new strategies |
| `FixedWeightSizer(weight_per_position=0.1)` | Fixed allocation per position | Known max position count |
| `VolWeightSizer(vol_slot='vol')` | Equal risk contribution | Mixed-vol universe (BTC + memes) |
| `RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02)` | Fixed risk budget per trade | Trend strategies, risk-aware |

Default: `EqualWeightSizer()`. Suggest `VolWeightSizer` as improvement for mixed-vol universes.
`EqualWeightSizer` and `VolWeightSizer` support `max_weight` to cap per-position concentration (excess → cash).
`VolWeightSizer` and `RiskBudgetSizer` require `ReturnVolatility() → Store('vol')` upstream.

For scaling strategies (ScalingPositionManager), `FixedWeightSizer` is the natural default — weight = level × weight_per_position. `EqualWeightSizer` also works: it counts total levels across assets for proportional weighting. Always pair with `LeverageCap` since gross leverage grows with position count and level.

**Start simple, suggest improvements.** Default to the minimal viable pipeline for the user's intent. Normalization (CrossSectionalZScore), volatility standardization (VolatilityStandardizer), and forecast diversification (FDM) are valuable but should be suggested as follow-up improvements after the base pipeline works — not included by default in every initial build. A single-signal strategy that produces correct WeightSeries is more useful than a complex one with normalization issues.

