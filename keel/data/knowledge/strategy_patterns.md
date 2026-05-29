## Common Strategy Patterns

These are recognition aids, not templates. Novel strategies may not fit any pattern — that's fine. Use component discovery tools for creative composition.

**Forecast-Combine** — `Execution(rebalance='every_bar')` default, suggest `'buffered'` for production to reduce turnover. Multiple signals normalized, scaled to forecasts, combined with ForecastCombiner, then sized. Simple flow: ForecastCombiner → ForecastCapper → ForecastWeightNormalizer. Add EmpiricalFDM between combiner and capper for diversification benefit (optional, Level 2+), or use AnalyticalFDMCombiner to combine+FDM in one step. For production vol-targeted sizing, replace ForecastWeightNormalizer with ReturnVolatility + VolTargetWeightConverter chain (Level 3+).

**Screen-Select** — `Execution(rebalance='on_change')` — weights change only when selections rotate. Filter universe by signal, equal-weight survivors. Components: indicator, TopNAssetSelector, SelectionToSignalConverter (hold_periods), EqualWeightAllocator. Assets rotate in/out based on ranking and hold periods.

**Screen → Entry/Exit (hybrid)** — Narrow universe with cross-sectional ranking or TopN, then apply entry/exit logic on selected assets. TopNAssetSelector produces a mask → ApplyMask gates the signal → ThresholdCross for entry/exit within the screened set.

**Entry/Exit** — Threshold-based buy/sell signals with position state management. The most common user request. Always use `Execution(rebalance='on_change')` — weights only change on entry/exit, so rebalancing every bar creates unnecessary micro-trades. Key architecture:

Entry: indicator → normalize → ThresholdCross → Store('entries')
Exit: one or more exit conditions → Store('exits')
Position: PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar') → EqualWeightSizer()

Exit components (all output 1.0=exit, 0.0=hold, use exit_mode='scalar'):
- `SignalReversionExit(exit_threshold)` — exit when |signal| returns to neutral zone. Stateless. Place in Parallel branch with entry from same normalized signal.
- `TrailingStopExit(entry_slot, ohlcv_slot, atr_multiplier)` — ATR trailing stop, resets per trade, long+short aware. Reads from slots.
- `MaxDrawdownStopLoss(entry_slot, ohlcv_slot, drawdown_threshold)` — fixed % stop loss from entry. Reads from slots.
- `TakeProfitExit(entry_slot, ohlcv_slot, profit_threshold)` — fixed % take profit from entry. Reads from slots.
- `TimeBasedExitFilter(entry_slot, hold_periods)` — exit after N bars. Reads entry_slot.
- `ValueStopExitFilter(entry_slot, ohlcv_slot, direction='auto')` — exit when momentum reverses. Auto-detects long/short direction.

Combining exits: Parallel + MaskOr → whichever fires first:
```
{ "stop": [MaxDrawdownStopLoss(...)], "tp": [TakeProfitExit(...)] }, MaskOr(), Store('exits')
```

Entry with signal + confirmation filter (e.g., RSI signal gated by MACD trend):
Use one branch as the directional signal, the other as a boolean filter. ApplyMask preserves direction from the signal branch.

**Trend-following (default)** — high RSI = strong momentum → long. No NegateTransform. Start long-only, suggest adding shorts later:
```
{ "signal": [RSI, Normalize], "filter": [MACD, Normalize, ThresholdCross] }, ApplyMask(score_signal='signal', filter_signal='filter'), ThresholdCross(upper=1.0, lower=-1.0, mode='long_only')
```
For trend, prefer trailing stop only (no TakeProfitExit) — let winners run. Use wider ATR multiplier (3.0) to give trends room. Equal-weight sizing (`EqualWeightSizer()`) scales naturally with position count.

**Mean reversion** — high RSI = overbought → short. Add NegateTransform, use stricter thresholds. Symmetric (long+short) is fine for MR:
```
{ "signal": [RSI, Normalize, NegateTransform], "filter": [MACD, Normalize, ThresholdCross] }, ApplyMask(score_signal='signal', filter_signal='filter'), ThresholdCross(upper=2.0, lower=-2.0)
```
For MR, TakeProfitExit makes sense (reversion targets are bounded). Tighter trailing stop (2.0 ATR).

When the user doesn't specify trend vs mean reversion, **default to trend-following**. Crypto has structural momentum bias. Mean reversion should be explicit ("mean reversion", "overbought/oversold", "fade", "reversal").

Do NOT use MaskAnd to combine two ThresholdCross outputs — MaskAnd is boolean (loses +1/-1 direction). Use ApplyMask when one branch provides direction and the other gates it.

Entry + exit from same signal (branch in Parallel):
```
RSI → Normalize → { "entry": [ThresholdCross(1.5,-1.5), Store('entries')], "exit": [SignalReversionExit(0.5), Store('exits')] }
```

Slot-reading components (PSM, TrailingStopExit, MaxDrawdownStopLoss, TakeProfitExit) ignore `current` — they read from slots. No Load needed before them. Position sizer goes AFTER PSM, not before.

**Position sizing for entry/exit strategies** (after PSM):
- `EqualWeightSizer()` — default. Splits target_leverage evenly across active positions. 5 longs at 1.0x → 0.2 each.
- `EqualWeightSizer(target_leverage=1.0, max_weight=0.3)` — equal weight with 30% per-position cap. Prevents concentration when positions drop out (1 survivor gets 30%, not 100%). Excess goes to cash.
- `EqualWeightSizer(target_leverage=0.5)` — conservative half-leverage.
- `FixedWeightSizer(weight_per_position=0.1)` — 10% per position, stacks with count. Add `LeverageCap` for hard limit.
- `VolWeightSizer(vol_slot='vol')` — inverse-vol sizing, equal risk per position. Requires `ReturnVolatility() → Store('vol')` upstream. Supports `max_weight` cap (same as EqualWeightSizer).
- `RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02)` — fixed vol budget per position. Requires `ReturnVolatility() → Store('vol')` upstream. Good for trend strategies with risk awareness.
- `BinaryToWeight` is deprecated — use EqualWeightSizer or FixedWeightSizer instead.

**Scaling Entry/Exit (DCA, pyramiding, laddered)** — `Execution(rebalance='on_change')` — multi-level variant of Entry/Exit. Positions scale through levels (1, 2, 3) as conditions deepen or re-trigger. Uses ScalingPositionManager instead of PositionStateMachine.

**When to use PSM vs ScalingPositionManager:**

| User says | Pattern | Component |
|-----------|---------|-----------|
| "Buy when RSI AND MACD agree" | Combined entry signal | ApplyMask → ThresholdCross → PSM |
| "Exit on stop OR timeout" | Combined exit signal | MaskOr → PSM |
| "Enter at RSI 30, add at 20, add at 10" | Independent entry levels | ScalingPositionManager |
| "Scale out: TP at 5%, TP at 10%, trail rest" | Independent exit levels | ScalingPositionManager(exit_mode='one_level') |
| "Buy more if it drops further" | Accumulation | ScalingPositionManager |
| "MACD confirms RSI entry" | Gated single entry | ApplyMask → PSM |

Laddered entries (different thresholds per level):
```
RSI → Normalize → {
    "e1": [ThresholdCross(upper=1.0, ..., mode='long_only'), Store('e1')],
    "e2": [ThresholdCross(upper=1.5, ..., mode='long_only'), Store('e2')],
    "e3": [ThresholdCross(upper=2.0, ..., mode='long_only'), Store('e3')],
    "exit": [SignalReversionExit(0.5), Store('exits')],
}
ScalingPositionManager(entry_slots=['e1', 'e2', 'e3'], exit_slots='exits')
FixedWeightSizer(weight_per_position=0.10)
```
Level 1 = 10%, level 2 = 20%, level 3 = 30% per asset. Each threshold independently activates/deactivates as the z-score crosses it.

Partial exits (scale out):
```
ScalingPositionManager(
    entry_slots=['e1', 'e2', 'e3'],
    exit_slots=['exit_tp1', 'exit_tp2', 'exit_trail'],
    exit_mode='one_level',
)
```
Each exit fire removes one level. TP at +5% → level 2. TP at +10% → level 1. Trail fires → flat.

**Factor Tilt** — `Execution(rebalance='every_bar')`, suggest `'buffered'` for production. Single signal directly to weights. Simplest pattern. Components: indicator, ForecastWeightNormalizer or EqualWeightAllocator.

**Multi-Signal Hierarchy** — Parallel branches of signals, composed at multiple levels (within-bucket, across-bucket). Uses Parallel + Composers at each level.

**Regime-Conditioned** — Base strategy modulated by regime detection (funding rates, volatility). Uses RegimeWeightedBlender, RegimeGate, or RegimeScale to adjust weights based on market conditions.

**Directional Pair/Basket** — `Execution(rebalance='buffered', buffer_threshold=0.10)`. Fixed long/short direction per asset group. Start simple with constant weights, add signal overlays only when requested.

Key components:
- `ConstantForecast(value=10)` — fixed forecast for all assets. Positive = long, negative = short. Simplest way to express fixed-direction legs.
- `ForecastRemap(to_min=2, to_max=20)` — remap signal to always-positive or always-negative range. Use when adding a momentum/signal overlay to modulate size while keeping direction fixed.
- `GroupAssetFilter(group="name")` — filter to Universe group in a branch. Use with `Universe(groups={...})` declarations.
- `AssetSelect(["HYPE"])` — inline asset filter (reducer, drops columns). Use for quick pairs without Universe declarations.
- `WeightConcatenator()` — merges Parallel branches with **different** asset columns into one DataFrame (unlike ForecastCombiner which averages **same** columns).

**Start simple** — constant weights, dollar-balanced:
```
{ "long":  [GroupAssetFilter(group="longs"),  ConstantForecast(value=10)],
  "short": [GroupAssetFilter(group="shorts"), ConstantForecast(value=-10)] },
WeightConcatenator(),
ForecastWeightNormalizer(target_leverage=0.75),
LeverageCap(max_leverage=2),
```

This gives equal dollar weight per asset, always long one group, always short the other. No signal chain needed. Works for any number of assets per side.

**Add momentum overlay** (when user asks for signal-driven sizing):
```
{ "long":  [GroupAssetFilter(group="longs"),  ROC(20), ForecastScaler(), ForecastCapper(20), ForecastRemap(to_min=2, to_max=20)],
  "short": [GroupAssetFilter(group="shorts"), ROC(20), ForecastScaler(), ForecastCapper(20), ForecastRemap(to_min=-20, to_max=-2)] },
WeightConcatenator(),
ForecastWeightNormalizer(target_leverage=0.75),
```

ForecastRemap keeps direction fixed (always positive / always negative) while momentum modulates size within that range. Wider range = more signal influence.

**Beta-hedged pair** (dynamic hedge via rolling beta):
When the user says "beta hedge", "hedge with BTC", or "beta neutral" — use `BetaHedgeAllocator`, NOT a manual short leg. A manual `ConstantForecast(-10)` on BTC is a static short, not a beta hedge. BetaHedgeAllocator dynamically sizes the hedge position based on rolling portfolio beta.
```
Store('ohlcv'),
{ "long":  [AssetSelect(["LDO"]),  ConstantForecast(value=10)],
  "short": [AssetSelect(["ZEC"]),  ConstantForecast(value=-10)] },
WeightConcatenator(),
ForecastWeightNormalizer(target_leverage=1),
BetaHedgeAllocator(ohlcv_slot='ohlcv', benchmark='BTC', window=60, hedge_ratio=1.0),
```

The hedge asset (BTC) must be in the PriceDataLoader symbols but does NOT need its own branch — BetaHedgeAllocator adds it automatically. Store('ohlcv') must come AFTER TargetTimeframeResampler and BEFORE the Parallel block so the allocator can read full-universe OHLCV data. Do NOT put ForecastWeightNormalizer inside each branch — normalize AFTER WeightConcatenator so the branches size relative to each other.

**Alpha + passive hedge** (active longs, constant short):
```
{ "alpha": [GroupAssetFilter(group="picks"), ROC(20), ForecastScaler(), ForecastCapper(20), ForecastRemap(to_min=2, to_max=20)],
  "hedge": [GroupAssetFilter(group="hedge"), ConstantForecast(value=-10)] },
WeightConcatenator(), ForecastWeightNormalizer(target_leverage=0.75), LeverageCap(max_leverage=2),
```

Sizing choices — clarify with user:
- `ForecastWeightNormalizer` — dollar-balanced (equal $ per leg). Default for pair trades.
- `VolTargetWeightConverter` — risk-balanced (equal risk per leg). Low-vol asset gets larger position, creates net directional exposure. Use when user asks for risk parity or vol targeting.

