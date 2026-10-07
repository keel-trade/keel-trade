## Trading Domain Knowledge

**Carry polarity**: Positive funding = longs pay shorts. Carry strategies SHORT high-funded assets to COLLECT funding. Always use NegateTransform after FundingDataLoader for carry signals. Without it, the strategy pays funding instead of earning it. Standard carry branch: `FundingDataLoader() → NegateTransform → CrossSectionalZScore` (the loader serves the declared clock — the mean of the hourly rates). Exception: FundingLevelRegime (regime detector) handles polarity internally.

**Timeframe selection** — `1d` is the default clock. Longer timeframes have better signal-to-noise, lower transaction costs, and are more robust out-of-sample. Shorter timeframes overfit faster and amplify noise.

**Supported timeframes**: `5min` is the FLOOR — it is supported, and it is the finest clock there is; `1d` is the coarsest. The validator lists the supported values when one is wrong.

An unsupported timeframe (1min, 3min, 10min, 3d, 1w) has a nearest supported value, and moving to it changes the strategy — a 1m thesis on 5m bars is a different strategy. `30min` IS supported, whatever the granularity of the underlying candles.

Supported is not the same as advisable: the default is still `1d`, and the table below stands. `5min` is available for a genuinely intraday thesis the user has asked for, not a default to drift toward — shorter timeframes overfit faster, amplify noise, and multiply transaction costs.

**Session / time-of-day filters are the session family** — `SessionMask(timezone="America/New_York", open="09:30", close="16:00", days="weekdays")` restricts trading to custom hours (IANA zone, DST by the local clock, a bar belongs to the session containing its close); `SessionRangeHigh/Low`, `SessionVWAP`, `SessionCloseExit`, `SessionRelativeVolume` carry the same clock. All are close-triggered ("supported under bar-close semantics"); a 24/7 day anchor is `anchor="day"`. A resampler does not express a session.

| Strategy type            | Default | Acceptable range | Rationale                                                                             |
| ------------------------ | ------- | ---------------- | ------------------------------------------------------------------------------------- |
| Trend / momentum         | 1d      | 8h–1d            | Trends develop over days/weeks. Shorter bars add noise without signal.                |
| Mean reversion           | 1d      | 4h–1d            | Reversions happen faster than trends. Intraday MR (4h–8h) is reasonable if requested. |
| Carry / funding          | 1d      | 1d only          | Funding rates are 8h events; daily aggregation is natural.                            |
| Screen-select / rotation | 1d      | 1d only          | Rotation signals are multi-day by nature.                                             |

Timeframe and trade count move together:

- **More bars, more trades**: `target_timeframe='12h'` doubles the bars a `1d` strategy evaluates, and more bars mean more threshold-crossing opportunities.
- **Fewer bars, less turnover**: moving up to `1d` from a shorter timeframe lowers turnover.
- **Intraday**: `4h` is the usual intraday clock; true intraday (1h–2h) needs strong justification — costs dominate, signal-to-noise drops, and the strategy is more fragile.

**Trend-following as the default style**: Crypto has structural momentum bias — trend strategies are more robust by default. Mean-reversion intent is named explicitly ("mean reversion", "overbought/oversold", "fade", "reversal", "contrarian").

**Oscillator polarity and NegateTransform**: Oscillators (RSI, Stochastic, WilliamsR, CMO) output HIGH = strong momentum / overbought, LOW = weak momentum / oversold. The interpretation depends on strategy type:

- **Trend-following (default)**: Do NOT negate. High RSI = strong momentum → go long. Low RSI = weak momentum → go short. The raw polarity is correct.
- **Mean reversion (explicit only)**: INVERT with NegateTransform so overbought → short (negative forecast), oversold → long (positive forecast). Use more extreme thresholds (±2.0 instead of ±1.5) to avoid trading noise. Without negation, a "mean reversion" RSI strategy actually buys overbought assets.
  Exception: TimeSeriesMeanReversionForecast and WingsTransform handle inversion internally.

**Indicator-specific guidance**:

- **RSI**: Trend → no negate, threshold ±1.5. MR → negate, threshold ±2.0. RSI is a momentum oscillator; in crypto, momentum persistence is strong.
- **MACD**: Inherently a trend indicator (moving average crossover); the `MACD` component emits the histogram (MACD line minus signal line). Almost always used for trend confirmation or filtering, rarely inverted. As a filter: histogram > 0 = bullish, < 0 = bearish — `ThresholdCross(upper=0.5, lower=-0.5)` on the normalized histogram.
- **±1 direction indicators** (SuperTrend, IchimokuCloud) emit +1 / −1 directly. For long/cash, follow with `ThresholdCross(upper=0.5, mode='long_only')` — it stays BinarySignal; `Clip` would not.
- **Funding rates**: Always negate for carry (positive funding = shorts get paid). NegateTransform is required. This is not trend vs MR — it's the carry trade convention.

**Signal diversity > quantity**: Three uncorrelated Sharpe-0.5 signals beat three correlated Sharpe-1.0 signals. Prioritize different families (trend vs carry vs mean reversion) over parameter variants (EWMAC at 5 speeds). Lowest correlation pairs in crypto: trend vs carry, trend vs mean reversion, price-based vs funding-based.

**Required component pairs** (always use together):

- ForecastScaler + ForecastCapper (scale then cap)
- VolTargetWeightConverter + LeverageCap (VolTargetWeightConverter sizes each asset independently, so the book needs a cap after it)
- AnalyticalFDMCombiner is the one-step form of ForecastCombiner + CorrelationEstimator + the deprecated AnalyticalFDM (combine + analytical FDM)

**Stream data alignment**: `FundingDataLoader()` / `OpenInterestLoader()` / `PremiumLoader()` serve the declared target_timeframe themselves, aggregated in-loader by data type — `agg` defaults "mean" for rates (funding), "last" for levels (OI), "mean" for premium; `agg="sum"` for an accrued flow. The Universe selector passes the same resolved universe to all data loaders, so assets already match — AssetAligner is NOT needed in the standard case.

**Multi-data shape alignment**: AssetAligner is only needed when a component explicitly drops assets from the universe (VolumeUniverseReducer, GroupAssetFilter) and a branch that kept them is combined with it — then Store the reduced OHLCV BEFORE the Parallel and use AssetAligner(reference_slot='ohlcv_1d') in the other branches. TopNAssetSelector does NOT change dimensions — it produces a mask, not a reduced universe.

**Complexity ladder** — match pipeline complexity to intent:

- Level 1 (single signal): 4-6 components, 2-4 params. Use ForecastWeightNormalizer for sizing.
- Level 2 (dual signal blend): 8-12 components, 5-8 params. ForecastCombiner → ForecastWeightNormalizer.
- Level 3 (multi-signal + vol targeting): 15-25 components. VolTargetWeightConverter → LeverageCap. For advanced portfolios: add IDM for diversification scaling.
- Level 4 (regime-adaptive portfolio): 25-40 components, 15-20 params
  More than 25 free parameters almost certainly means overfitting. Complexity grows ONE level at a time, not in jumps.

**Regime models**: Prefer single-component detectors (FundingLevelRegime, RealizedVolatilityRegime) over composites. Prefer RegimeWeightedBlender (soft modulation) over RegimeGate (hard on/off). Regime conditioning enhances alpha — it does NOT replace it. Build and test base signal first. Value hierarchy: Alpha > Breadth > Conditioning > Risk Management.

**Hierarchical combination**: Equal weights within a signal family (e.g., EWMAC at 3 speeds) is fine. Across families (trend + carry + MR), set explicit weights — otherwise 5 trend + 1 carry = 83% trend. Use EmpiricalFDM after ForecastCombiner, or AnalyticalFDMCombiner to combine+FDM in one step (preferred for analytical FDM — single weights param, no duplication).

**Position sizing for entry/exit strategies** (after `TradeManager`, or after a stateless threshold):

| Sizer                                                        | Use when                                          | Example                          |
| ------------------------------------------------------------ | ------------------------------------------------- | -------------------------------- |
| `EqualWeightSizer(target_leverage=1.0)`                      | Default. Simple equal weight.                     | Most strategies start here       |
| `EqualWeightSizer(max_weight=0.3)`                           | Cap per-position concentration                    | Prevent 100% in 1 survivor       |
| `EqualWeightSizer(target_leverage=0.5)`                      | Conservative / reduce risk                        | Testing new strategies           |
| `FixedWeightSizer(weight_per_position=0.1)`                  | Fixed allocation per position                     | Known max position count         |
| `VolWeightSizer(vol_slot='vol')`                             | Equal risk contribution                           | Mixed-vol universe (BTC + memes) |
| `RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02)`    | Fixed risk budget per trade                       | Trend strategies, risk-aware     |
| `RiskSizer(risk=0.01, distance='stop_dist', max_weight=0.5)` | Risk a fixed fraction to the stop, sized at entry | R-multiple trades                |

`VolWeightSizer`, `RiskBudgetSizer` and `VolTargetWeightConverter` read a `vol` slot a volatility component writes first: `ReturnVolatility() → Store("vol")` (a Parallel branch or before the signal), then `vol_slot='vol'` (`return_vol_slot='vol'` on the converter).

With `Reduce` or `ScaleIn`, size after `Exposure()` with a sizer that keeps size (`FixedWeightSizer`, or the newest `EqualWeightSizer` v2, `VolWeightSizer` v3, `RiskBudgetSizer` v2); an older pinned version is refused with `SIZER_ERASES_SIZE`.

### Leverage is a risk decision, not a dial

At 10x a 10% adverse move is a total loss, and a backtest Sharpe says
nothing about surviving a liquidation. Two limits bound the leverage a
strategy runs at:

- The mechanical caps are `LeverageCap(max_leverage=...)` at 10 and sizer
  `target_leverage` at 5; a value above them fails validation, so a
  requested figure above a cap is not the figure that runs. The platform
  runs at most 10x gross in backtest and live alike: a bar whose weights
  sum past 10 is scaled down to 10 pro-rata, and the backtest says so.
- The venue's own per-symbol maximum is resolved into the strategy's
  Universe declaration. A universe of low-cap perps often caps far below
  the majors, so the effective ceiling may be well under the requested
  figure for part of the book.
