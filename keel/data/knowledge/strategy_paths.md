## Paths From Data to Weights

**Normalization applies to ALL paths** — not just continuous. Discrete strategies commonly normalize signals before applying thresholds (e.g., RollingZScoreTransform → ThresholdCross at ±2). CrossSectionalZScore is useful when combining multiple signals on different scales, regardless of path.

**Path 1 — Continuous Forecast**:
Data → Indicator → Normalize → ForecastScaler → ForecastCapper → ForecastWeightNormalizer → WeightSeries. Signal values carry conviction — stronger signals get larger positions. When combining multiple signals, add CrossSectionalZScore before ForecastScaler to put them on comparable scales. For single signals, ForecastScaler handles scaling directly. ForecastWeightNormalizer is the default sizer — it normalizes total leverage to target_leverage (default 1.0).

**Vol-targeted sizing** (upgrade): Replace ForecastWeightNormalizer with `VolTargetWeightConverter → LeverageCap(max_leverage=1.0)`. This gives lower-vol assets larger positions (equal risk contribution) while keeping total leverage capped. The pattern using a Parallel branch:

```python fragment
{"signal": [ROC(period=20), ForecastScaler(), ForecastCapper()], "vol": [ReturnVolatility(), Store("vol")]}
→ Extract("signal") → VolTargetWeightConverter(return_vol_slot="vol") → LeverageCap(max_leverage=1.0)
```

**Path 2 — Discrete Entry/Exit** (most requested):
Decisions are in/out, not continuous. Works for trend-following, breakout, mean-reversion, and event-driven strategies.

Four layers, in one direction: signals (market only; entry and exit signals are built separately) → positions (TradeManager, the only layer that remembers what the strategy did) → sizing (per asset) → portfolio. Continuous forecasts skip the position layer.

Position layer: TradeManager turns entry signals into a Position; each rule is a reader, then ordinary components, then an action (Exit, Reduce, ScaleIn, AllowEntry).

Simple (stateless): Data → Indicator → Normalize → ThresholdCross → EqualWeightSizer(). Re-evaluates every bar — no position memory. Good for starting, but exits at entry threshold, not at a separate exit level.

With rules: entries (−1/0/1) → `Store('entries')` → `TradeManager(entries='entries', prices='ohlcv')` → one Parallel branch per rule, named as the user names it (the name is the exit reason) → `Exposure()` → sizer. `RiskSizer` takes the Position directly, without `Exposure()`.

Choosing the action:

- A full exit (stop, target, trail, time, a market signal) → `Exit()`. A market exit is `[Load('x'), Exit()]`.
- Take part off → `Reduce(fraction=)`, a fraction of the initial size.
- Add (DCA, pyramid, ladder) → `ScaleIn(units=, times=)`.
- When the next trade may start (cooldown, trades per day, pause after losses, daily loss) → `AllowEntry()`.
- "Stop OR target" is two branches. "RSI AND MACD agree" is one entry signal (ApplyMask or MaskAnd), not an add.
- A rule reading what another rule did (`SoldFraction()`, `AddCount()`) goes in a later stage.
- After an exit the next trade waits for the entry signal to reset; `AllowEntry` gates AND with that wait; `reentry='any_bar'` drops the reset.

The readers, actions and examples are catalogued once, in Strategy Patterns (Entry/Exit); the deep doc is the `entry_exit_patterns` topic.

**Exit semantics**: bar-close weight mechanics, not resting orders — see Capability Boundaries ("Bar-close consent"). Pine-style SL/TP and R-multiples are exits of this kind.

Direction: long-only (`ThresholdCross(mode='long_only')`) is the usual form for trend-following entry/exit strategies. Crypto has structural long bias and long-only is simpler to reason about. For mean reversion, symmetric (both sides) is fine.

**Sizing for Path 2** (after TradeManager or ThresholdCross): the sizer table is in Trading Domain ("Position sizing for entry/exit strategies") — `EqualWeightSizer()` is the default.

Trigger words for adds and partials (`ScaleIn` / `Reduce`): 'DCA', 'dollar cost average', 'pyramid', 'add to position', 'scale in', 'scale out', 'ladder', 'laddered entries', 'partial exit', 'accumulate', 'buy more if it drops further', 'multiple entry levels', 'average down', 'average in'. Use them only when the user describes adds or partials.

**Path 3 — Screen-Select-Allocate**:
Data → Indicator → `TopNAssetSelector(n=…)` → `EqualWeightAllocator()` → `WeightCadence(duration='7d')` → `FillNaN(fill_value=0.0)` → WeightSeries. Selects the top N assets by rank, equal-weights them, and holds the targets for the calendar period (the cadence is the hold and the exit); FillNaN makes a masked-out asset an explicit 0.0. Best for rotation strategies — the `screen_select_patterns` topic has the validated example.

**Hybrid: Screen → Entry/Exit**: Narrow the universe with TopNAssetSelector or cross-sectional ranking, then apply entry/exit logic on the selected assets. Use TopNAssetSelector to produce a mask → ApplyMask on the signal, then ThresholdCross for entry/exit within the screened set.

**Path 4 — Direct Allocation**:
Data → Indicator → EqualWeightAllocator or RiskParityAllocator → WeightSeries. Skips the forecast stage entirely. Simple but effective for factor tilts.
