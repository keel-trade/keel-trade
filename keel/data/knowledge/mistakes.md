## Key Mistakes to Avoid

**M-01: Equal weights on continuous forecasts.** ForecastSeries values carry conviction. Using EqualWeightAllocator after continuous forecasts discards signal magnitude. Use ForecastWeightNormalizer (simple) or VolTargetWeightConverter (production) instead. Note: EqualWeightAllocator IS correct for Path 3 (TopN/filter → equal-weight selected assets) where selection is the signal.

**M-09: Pipeline without WeightSeries output.** The backtester needs WeightSeries. A pipeline ending at SignalSeries or ForecastSeries cannot be tested. Always check the output type.

**M-10: Missing data pipeline.** Every pipeline needs data. Globals(target_timeframe='1d'), PriceDataLoader, TargetTimeframeResampler are the standard opening. Use Globals for config, not StoreValue.

**M-11: TopN without exit logic.** TopNAssetSelector selects entries but doesn't manage exits. Always follow with SelectionToSignalConverter (hold_periods) to manage hold duration and exit timing.

**M-12: Unconsumed Parallel.** After a Parallel block (dict output), you MUST add a Composer, Extract, or Load. A pipeline ending at dict is incomplete.

**M-16: Over-indexing on one pattern.** Not every strategy needs hierarchical multi-signal forecast-combine. A simple factor tilt or entry/exit strategy may be exactly what the user wants.

**M-03: Normalizing binary signals.** CrossSectionalZScore on {-1,0,1} is meaningless. Binary signals follow Path 2 (entry/exit), not Path 1 (continuous forecast).

**M-18: Parallel branch shape mismatch.** If any branch drops assets from the universe (VolumeUniverseReducer, GroupAssetFilter), ALL other branches must use AssetAligner(reference_slot='ohlcv_1d') to match. Store the reduced OHLCV BEFORE the Parallel block.

**M-19: Serial Store/Load chain when Parallel should be used.** When two computations share the same input but are otherwise independent (e.g., a filter and a signal), use Parallel branches — not a serial Store → compute A → Store result → Load → compute B → apply A to B chain. The serial pattern obscures data flow and prevents the engine from expressing independence. Rule of thumb: if you Store a value only to Load it one step later, the two paths should be Parallel branches instead.

Bad (serial anti-pattern):
```
Store('ohlcv') → ADX → BelowThreshold → Store('mask') → Load('ohlcv') → Keltner → ZScore → ApplyUniverseMask(mask_slot='mask')
```

Good (parallel — branches receive current automatically, no Load needed):
```
{ "signal": [Keltner, ZScore], "filter": [ADX, BelowThreshold] } → ApplyMask("signal", "filter")
```

Note: ApplyMask is a SignalComposer that consumes the Parallel dict directly — no Store, Load, or Extract needed. This applies to any case where a filter or confirmation signal is independent of the main signal path. Always ask: "does computation B depend on the output of computation A?" If not, they belong in Parallel.

**M-20: Using ApplyUniverseMask with boolean filter masks.** ApplyUniverseMask expects 1.0/NaN masks (from RollingVolumeUniverseMask). Boolean masks from threshold filters (True/False) give 0.0 (not NaN) when multiplied, which has different semantics. Use ApplyMask (SignalComposer) for boolean filter masks from Parallel branches. Use ApplyUniverseMask only with 1.0/NaN universe masks.

**M-21: Re-normalizing or re-thresholding already-binary signals.** MaskAnd, MaskOr, and exit components (TrailingStopExit, TakeProfitExit, etc.) already output 0.0/1.0 binary values. Do NOT add MinMaxNormalize or ThresholdCross after them — this re-processes an already-binary signal and often squashes it to all zeros (producing 0 trades). Store the output directly: `MaskAnd() → Store('entries')`, `MaskOr() → Store('exits')`.

**M-22: Position sizer before PositionStateMachine.** PSM outputs BinarySignal (position state: +1/-1/0). Position sizers (EqualWeightSizer, VolWeightSizer, etc.) convert BinarySignal to WeightSeries. The correct order is always: `PositionStateMachine → EqualWeightSizer()`. Putting the sizer first converts entry signals to weights BEFORE PSM can manage position state, making PSM receive WeightSeries instead of BinarySignal from slots.

**M-23: Using MaskAnd/MaskOr to combine directional entry signals.** MaskAnd/MaskOr are boolean mask combiners — they convert inputs to True/False, losing +1/-1 direction. If you MaskAnd two ThresholdCross outputs (+1/-1/0), the result is True/False with no direction — you can't distinguish long from short. Instead, use one signal as the directional source and the other as a filter via ApplyMask: `{ "signal": [RSI, Normalize], "filter": [MACD, ThresholdCross] } → ApplyMask(score_signal='signal', filter_signal='filter') → ThresholdCross`. MaskAnd/MaskOr are correct for combining exit conditions (which are non-directional 0/1 scalars) and boolean filter masks.

**M-25: Using FixedWeightSizer with weight too high.** `FixedWeightSizer(weight_per_position=1.0)` means 100% allocation per position — with 5 active positions that's 5x leverage. Use `EqualWeightSizer()` (which divides target_leverage by position count) for safe default sizing. Only use `FixedWeightSizer` when you know the maximum position count and want explicit per-position allocation. Always pair with `LeverageCap` when using `FixedWeightSizer`.

**M-24: Defaulting to mean reversion when user doesn't specify.** When the user says "RSI strategy" or "RSI/MACD conditional," default to trend-following (no NegateTransform). Crypto has structural momentum bias — trend strategies are more robust. Only add NegateTransform when the user explicitly asks for mean reversion ("overbought/oversold", "fade", "reversal", "contrarian"). Without NegateTransform, high RSI = strong momentum → long. With NegateTransform, high RSI = overbought → short. Getting the polarity wrong means the strategy systematically trades against the trend.

**M-26: Using ScalingPositionManager when user wants combined signal logic.** "Enter when RSI AND MACD both signal" is a single gated entry — use ApplyMask → ThresholdCross → PSM. ScalingPositionManager is for independent position accumulation ("enter at RSI 30, add more at RSI 20, add more at RSI 10"). The test: does each signal add a separate position level, or do they jointly gate a single entry? Joint gating → PSM. Independent levels → ScalingPositionManager. When in doubt, default to PSM — it covers the majority of multi-signal strategies.

**M-27: Pair strategy sizing — dollar-balanced vs risk-balanced.** For directional pair strategies, `VolTargetWeightConverter` produces risk-balanced weights where the lower-vol asset gets a larger dollar position (e.g., 2x). This creates net directional exposure, not a balanced pair. `ForecastWeightNormalizer` produces dollar-balanced weights where each leg gets proportional dollar allocation. Neither is inherently better — clarify with the user: "Do you want equal dollar exposure per leg, or equal risk contribution?" Default to explaining both options. Use `ForecastWeightNormalizer` when user says "pair trade", "market neutral", "dollar neutral". Use `VolTargetWeightConverter` when user says "risk parity", "equal risk", "vol-adjusted".

**M-28: Pattern-matching instead of searching for domain-specific concepts.** When the user mentions a specific trading concept ("beta hedge", "vol targeting", "risk parity", "trailing stop"), ALWAYS call `strategy_components_search` with the concept as a query BEFORE selecting components. Pattern docs are guides, not exhaustive — there are components for concepts not listed in patterns (e.g., BetaHedgeAllocator for beta hedging). A manual `ConstantForecast(-10)` on BTC is a static short, not a beta hedge — the user asked for a specific mechanism. Searching first would have surfaced the correct component. Think about what the user's words mean in trading, not just which pattern template matches the keywords.

**M-29: Per-branch normalization in Parallel before WeightConcatenator.** Do NOT put `ForecastWeightNormalizer` inside each Parallel branch when branches will be merged by `WeightConcatenator`. Per-branch normalization makes each branch sum to `target_leverage` independently, so after concatenation the total leverage is `N * target_leverage` (where N = number of branches). It also destroys the relative sizing between branches — a branch with one asset and a branch with five assets both get leverage=1, making the single asset 5x overweight. Normalize AFTER concatenation so all branches size relative to each other.

