## Common Strategy Patterns

These are recognition aids, not templates. Novel strategies may not fit any pattern — that's fine. Use component discovery tools for creative composition.

**Forecast-Combine** — Multiple signals normalized, scaled to forecasts, combined with ForecastCombiner, then sized. Simple flow: ForecastCombiner → ForecastCapper → ForecastWeightNormalizer. Add EmpiricalFDM between combiner and capper for diversification benefit (optional, Level 2+), or use AnalyticalFDMCombiner to combine+FDM in one step. For production vol-targeted sizing, replace ForecastWeightNormalizer with ReturnVolatility + VolTargetWeightConverter chain (Level 3+).

**Screen-Select** — Filter universe by signal, equal-weight survivors, hold for a calendar period: indicator → `TopNAssetSelector` → `EqualWeightAllocator` → `WeightCadence(duration=...)` → `FillNaN(fill_value=0.0)` (Path 3). A hybrid — screen, then entry/exit within the screened set — is in Paths.

**Entry/Exit** — the position layer (Path 2). The most common user request. The bracket, from `TradeManager` down:

```python fragment
Store("entries") → TradeManager(entries="entries", prices="ohlcv")
→ {"stop": [TradeReturn(), BelowThresholdFilter(threshold=-0.05, inclusive=True), Exit()],
   "target": [TradeReturn(), AboveThresholdFilter(threshold=0.10, inclusive=True), Exit()]}
→ Exposure() → EqualWeightSizer()
```

Readers: trade readers `TradeReturn()`, `TradePnL()`, `BarsHeld()`, `DrawdownFromPeak()` / `GiveBack()`, `MaxFavorable()`, `ReturnFromAverage()` / `ReturnSinceLastFill()`, `SoldFraction()` / `AddCount()` (state: read in a later stage), `AtEntry(slot=...)`; between-trade readers `BarsSinceExit()`, `EntryCount(window=...)`, `LossStreak(window=...)`, `RealizedPnL(window=...)`, `LastTradeReturn()`. Thresholds on returns are fractions: −0.05 is a 5% loss.

Actions: `Exit()` closes the trade; `Reduce(fraction=...)` sells a fraction of the initial size; `ScaleIn(units=..., times=...)` adds once per rising edge (`on="every_bar"` for every bar); `AllowEntry()` gates the next entry and ANDs with the signal reset. Short forms expand to the same rules: `StopLoss(pct=0.05)`, `TakeProfit(pct=0.10)`, `TrailingStop(pct=0.08)`, `MaxHold(bars=48)`, `Cooldown(bars=4)`. A market exit is `[Load("x"), Exit()]`.

Mean-reversion entry and exit from one signal:

```python fragment
RSI(period=14) → RollingZScoreTransform(window=60)
→ {"entry": [ThresholdCross(upper=1.5, lower=-1.5), Store("entries")],
   "exit": [SignalReversionExit(exit_threshold=0.5), Store("reverted")]}
→ TradeManager(entries="entries", prices="ohlcv") → {"reverted": [Load("reverted"), Exit()]}
→ Exposure() → EqualWeightSizer()
```

Adds and partials (DCA, pyramid, scale out) are `ScaleIn` / `Reduce` rules; the `entry_exit` pattern has the examples.

Entry with signal + confirmation filter (e.g., RSI signal gated by MACD trend):
Use one branch as the directional signal, the other as a boolean filter. ApplyMask preserves direction from the signal branch.

**Trend-following (default)** — high RSI = strong momentum → long. No NegateTransform. The example is long-only:

```python fragment
{"signal": [RSI(period=14), RollingZScoreTransform(window=60)],
 "filter": [MACD(), RollingZScoreTransform(window=60), ThresholdCross(upper=0.5, lower=-0.5)]}
→ ApplyMask(score_signal="signal", filter_signal="filter") → ThresholdCross(upper=1.0, mode="long_only")
```

For trend, a trail and no fixed target — let winners run. An ATR trail of 3 gives trends room. Equal-weight sizing (`EqualWeightSizer()`) scales naturally with position count.

**Mean reversion** — high RSI = overbought → short. Add NegateTransform, use stricter thresholds. Symmetric (long+short) is fine for MR:

```python fragment
{"signal": [RSI(period=14), RollingZScoreTransform(window=60), NegateTransform()],
 "filter": [MACD(), RollingZScoreTransform(window=60), ThresholdCross(upper=0.5, lower=-0.5)]}
→ ApplyMask(score_signal="signal", filter_signal="filter") → ThresholdCross(upper=2.0, lower=-2.0)
```

For MR, a target makes sense (reversion targets are bounded), with a tighter trail (2 ATR).

**Factor Tilt** — Single signal directly to weights. Simplest pattern. Components: indicator, ForecastWeightNormalizer or EqualWeightAllocator.

**Multi-Signal Hierarchy** — Parallel branches of signals, composed at multiple levels (within-bucket, across-bucket). Uses Parallel + Composers at each level.

**Regime-Conditioned** — Base strategy modulated by regime detection (funding rates, volatility). Uses RegimeWeightedBlender, RegimeGate, or RegimeScale to adjust weights based on market conditions.

**Hold / index basket** — a basket held at constant conviction, no signal: `ConstantForecast(value=10)` → `ForecastWeightNormalizer(target_leverage=1.0)` (the `position_sizing` pattern, Path G).

**Directional Pair/Basket** — `Execution(rebalance='buffered', buffer_threshold=0.10)`. Fixed long/short direction per asset group. Start simple with constant weights, add signal overlays only when requested.

Key components:

- `ConstantForecast(value=10)` — fixed forecast for all assets. Positive = long, negative = short. Simplest way to express fixed-direction legs.
- `ForecastRemap(to_min=2, to_max=20)` — remap signal to always-positive or always-negative range. Use when adding a momentum/signal overlay to modulate size while keeping direction fixed.
- `GroupAssetFilter(group="name")` — filter to Universe group in a branch. Use with `Universe(groups={...})` declarations.
- `AssetSelect(symbols=["HYPE"])` — inline asset filter (reducer, drops columns). Use for quick pairs without Universe declarations.
- `WeightConcatenator()` — merges Parallel branches with **different** asset columns into one DataFrame (unlike ForecastCombiner which averages **same** columns). It takes WeightSeries branches: **each branch sizes to WeightSeries before `WeightConcatenator`, and the book's gross leverage is the sum of the branch targets** — split the target across branches (two legs at 0.375 make a 0.75 book) or cap after with `LeverageCap`.

**Start simple** — constant weights, dollar-balanced:

```python fragment
{"long":  [GroupAssetFilter(group="longs"),  ConstantForecast(value=10),  ForecastWeightNormalizer(target_leverage=0.375)],
 "short": [GroupAssetFilter(group="shorts"), ConstantForecast(value=-10), ForecastWeightNormalizer(target_leverage=0.375)]}
→ WeightConcatenator() → LeverageCap(max_leverage=2)
```

This gives equal dollar weight per asset within each leg, always long one group, always short the other. No signal chain needed. Works for any number of assets per side.

**Add momentum overlay** (when user asks for signal-driven sizing):

```python fragment
{"long":  [GroupAssetFilter(group="longs"),  ROC(period=20), ForecastScaler(), ForecastCapper(limit=20),
           ForecastRemap(to_min=2, to_max=20),   ForecastWeightNormalizer(target_leverage=0.375)],
 "short": [GroupAssetFilter(group="shorts"), ROC(period=20), ForecastScaler(), ForecastCapper(limit=20),
           ForecastRemap(to_min=-20, to_max=-2), ForecastWeightNormalizer(target_leverage=0.375)]}
→ WeightConcatenator()
```

ForecastRemap keeps direction fixed (always positive / always negative) while momentum modulates size within that range. Wider range = more signal influence.

**Beta-hedged pair** (dynamic hedge via rolling beta):
When the user says "beta hedge", "hedge with BTC", or "beta neutral" — use `BetaHedgeAllocator`, NOT a manual short leg. A manual `ConstantForecast(value=-10)` on BTC is a static short, not a beta hedge. BetaHedgeAllocator dynamically sizes the hedge position based on rolling portfolio beta.

```python fragment
PriceDataLoader() → Store("ohlcv")
→ {"long":  [AssetSelect(symbols=["LDO"]), ConstantForecast(value=10),  ForecastWeightNormalizer(target_leverage=0.5)],
   "short": [AssetSelect(symbols=["ZEC"]), ConstantForecast(value=-10), ForecastWeightNormalizer(target_leverage=0.5)]}
→ WeightConcatenator()
→ BetaHedgeAllocator(ohlcv_slot="ohlcv", benchmark="BTC", window=60, hedge_ratio=1.0)
```

The hedge asset (BTC) must be in the Universe but does NOT need its own branch — BetaHedgeAllocator adds it automatically. Store('ohlcv') comes straight after PriceDataLoader() and BEFORE the Parallel block so the allocator reads full-universe OHLCV.

**Alpha + passive hedge** (active longs, constant short):

```python fragment
{"alpha": [GroupAssetFilter(group="picks"), ROC(period=20), ForecastScaler(), ForecastCapper(limit=20),
           ForecastRemap(to_min=2, to_max=20), ForecastWeightNormalizer(target_leverage=0.5)],
 "hedge": [GroupAssetFilter(group="hedge"), ConstantForecast(value=-10), ForecastWeightNormalizer(target_leverage=0.25)]}
→ WeightConcatenator() → LeverageCap(max_leverage=2)
```

Sizing choices — equal dollar exposure per leg, or equal risk contribution:

- `ForecastWeightNormalizer` — dollar-balanced (proportional $ per leg). Default for pair trades; use it when the user says "pair trade", "market neutral", "dollar neutral".
- `VolTargetWeightConverter` — risk-balanced (equal risk per leg): the lower-vol asset gets the larger dollar position (e.g. 2x), which creates net directional exposure. Use it when the user says "risk parity", "equal risk", "vol-adjusted".
