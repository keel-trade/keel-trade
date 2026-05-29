## Paths From Data to Weights

**Normalization applies to ALL paths** — not just continuous. Discrete strategies commonly normalize signals before applying thresholds (e.g., RollingZScoreTransform → ThresholdCross at ±2). CrossSectionalZScore is useful when combining multiple signals on different scales, regardless of path.

**Path 1 — Continuous Forecast** (`Execution(rebalance='every_bar')`, suggest `'buffered'` for production):
Data → Indicator → Normalize → ForecastScaler → ForecastCapper → ForecastWeightNormalizer → WeightSeries. Signal values carry conviction — stronger signals get larger positions. When combining multiple signals, add CrossSectionalZScore before ForecastScaler to put them on comparable scales. For single signals, ForecastScaler handles scaling directly. ForecastWeightNormalizer is the default sizer — it normalizes total leverage to target_leverage (default 1.0).

**Vol-targeted sizing** (upgrade): Replace ForecastWeightNormalizer with `VolTargetWeightConverter → LeverageCap(max_leverage=1.0)`. This gives lower-vol assets larger positions (equal risk contribution) while keeping total leverage capped. VolTargetWeightConverter sizes per-asset independently, so always follow with LeverageCap. The pattern using a Parallel branch:
```
{ 'signal': [ROC, ForecastScaler, ForecastCapper], 'vol': [ReturnVolatility, Store('vol')] }
→ Extract('signal') → VolTargetWeightConverter(return_vol_slot='vol') → LeverageCap(max_leverage=1.0)
```

**Path 2 — Discrete Entry/Exit** (most requested, `Execution(rebalance='on_change')`):
Decisions are in/out, not continuous. Always use `on_change` — binary weights only change on entry/exit events, so `every_bar` causes unnecessary micro-rebalances. Build entry and exit signals separately, combine with PositionStateMachine. Works for trend-following, breakout, mean-reversion, and event-driven strategies. Default to trend-following when the user doesn't specify.

Simple (stateless): Data → Indicator → Normalize → ThresholdCross → EqualWeightSizer(). Re-evaluates every bar — no position memory. Good for starting, but exits at entry threshold, not at a separate exit level.

Stateful (entry + exit): Build entry and exit signals independently (often in Parallel), Store each to slots, combine with PositionStateMachine → EqualWeightSizer(). Enables different entry/exit thresholds (e.g., enter at z=±2, exit when signal reverts).

Entry signal: Normalize → ThresholdCross(upper=2, lower=-2) → Store('entries')

Exit mechanisms (choose one or combine with Parallel + MaskOr for OR logic):
- Signal reversion: SignalReversionExit(exit_threshold=0.5) → exits when |signal| returns to neutral zone. Use in Parallel with entry from same normalized signal. Works with exit_mode='scalar'.
- Trailing stop: TrailingStopExit(entry_slot, ohlcv_slot, atr_multiplier=2.0) → ATR-based, resets per trade, long+short aware
- Stop loss: MaxDrawdownStopLoss(entry_slot, ohlcv_slot, drawdown_threshold=0.05) → fixed % loss from entry
- Take profit: TakeProfitExit(entry_slot, ohlcv_slot, profit_threshold=0.10) → fixed % gain from entry
- Time-based: TimeBasedExitFilter(entry_slot, hold_periods=N) → exits after N bars
- Momentum stop: ValueStopExitFilter(entry_slot, direction='auto') → exits when momentum reverses

All exit components output 1.0 for exit and use exit_mode='scalar' with PSM. Slot-reading components (TrailingStopExit, MaxDrawdownStopLoss, TakeProfitExit) read entry signals and OHLCV from slots — no Load() needed before them.

Position state: PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar') → holds until exit fires

Multiple signals may feed into entry (e.g., 2-3 indicators combined or gated) and different signals or conditions into exit (e.g., trailing stop OR time-based via Parallel + MaskOr).

Direction: Default to **long-only** for trend-following entry/exit strategies (`ThresholdCross(mode='long_only')`). Crypto has structural long bias and long-only is simpler to reason about. Suggest adding shorts as a follow-up improvement. For mean reversion, symmetric (both sides) is fine. For trend, prefer trailing stop only (no TakeProfitExit) — let winners run.

**Position sizing for Path 2** (after PSM or ThresholdCross):
- Default: `EqualWeightSizer()` — splits target_leverage evenly across active positions. Add `max_weight=0.3` to cap per-position concentration when position count varies.
- Mixed-vol universe (e.g., BTC + altcoins + memes): suggest `VolWeightSizer(vol_slot='vol')` for equal risk contribution. Requires `ReturnVolatility() → Store('vol')` upstream. Also supports `max_weight`.
- Risk-aware trend: `RiskBudgetSizer(vol_slot='vol', risk_per_position=0.02)` — fixed vol budget per trade.
- Fixed allocation: `FixedWeightSizer(weight_per_position=0.1)` — 10% per position regardless of count. Add `LeverageCap` for safety.
- `BinaryToWeight` is deprecated — use the sizers above.

Filters and confirmations are independent of the signal → compute in Parallel branches, merge with ApplyMask before the entry threshold.

**Scaling entries (DCA / pyramiding / laddered)**: A variant of Path 2 where positions accumulate across multiple independent triggers instead of being binary in/out. Use ScalingPositionManager instead of PositionStateMachine. It outputs integer position levels {0, 1, 2, 3} instead of binary {-1, 0, +1}.

Important: most multi-signal strategies are NOT scaling strategies. The distinction:
- "Enter when RSI AND MACD agree" → ONE combined entry signal (ApplyMask or MaskAnd) → PSM. The user wants a single gated entry, not accumulation.
- "Enter at RSI 30, add more at 20, add more at 10" → THREE independent entry levels → ScalingPositionManager. The user wants to accumulate position as conviction deepens.
- "Exit on stop loss OR take profit" → ONE combined exit signal (MaskOr) → PSM. This is OR-logic combining, not partial exits.
- "Take profit at +5%, take more at +10%, trail the rest" → THREE independent exit levels → ScalingPositionManager(exit_mode='one_level'). The user wants to scale out.

Default to PositionStateMachine. Only use ScalingPositionManager when the user explicitly describes accumulation, scaling, or multiple independent position levels.

Trigger words for scaling: 'DCA', 'dollar cost average', 'pyramid', 'add to position', 'scale in', 'scale out', 'ladder', 'laddered entries', 'partial exit', 'accumulate', 'buy more if it drops further', 'multiple entry levels', 'average down', 'average in'.

Two entry modes:
- Multi-slot (laddered): `ScalingPositionManager(entry_slots=['e1', 'e2', 'e3'], exit_slots='exits')` — each slot independently controls one level. Use when each level has its own trigger condition.
- Single-slot (DCA/pyramiding): `ScalingPositionManager(entry_slots='entries', exit_slots='exits', max_entries=3)` — each re-trigger of the entry signal adds a level.

Exit modes: `exit_mode='all'` (default, full exit) or `exit_mode='one_level'` (partial exits, decrements by 1).

Sizing: `FixedWeightSizer(weight_per_position=0.10)` is the natural default — weight = level × weight_per_position. Level 3 at 0.10 → 0.30 weight. Always pair with `LeverageCap` since gross leverage grows with levels. Use `Execution(rebalance='on_change')`.

**Path 3 — Screen-Select-Allocate** (`Execution(rebalance='on_change')`):
Weights change only when selections rotate — `on_change` avoids redundant trades.
Data → Indicator → TopNAssetSelector → SelectionToSignalConverter(hold_periods) → EqualWeightAllocator → WeightSeries. Selects top N assets by rank, equal-weights them. Assets rotate in and out based on ranking changes and hold periods. Best for rotation strategies.

**Hybrid: Screen → Entry/Exit**: Narrow the universe with TopNAssetSelector or cross-sectional ranking, then apply entry/exit logic on the selected assets. Use TopNAssetSelector to produce a mask → ApplyMask on the signal, then ThresholdCross for entry/exit within the screened set.

**Path 4 — Direct Allocation**:
Data → Indicator → EqualWeightAllocator or RiskParityAllocator → WeightSeries. Skips the forecast stage entirely. Simple but effective for factor tilts.

