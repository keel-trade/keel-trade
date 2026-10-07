## Composition Mechanics

**Prefer Parallel for independent computations.** When a pipeline has multiple computations that share the same input but don't depend on each other's output, use Parallel branches. This is the most common case: a signal path and a filter/regime/confirmation path both read from the same OHLCV data. Parallel makes the independence explicit and produces cleaner, more readable pipelines. Use Store/Load chains only when computation B genuinely depends on the output of computation A.

**Parallel branches receive `current` automatically.** Each branch starts with the same `current` value that was flowing at the point where Parallel begins. If the data before Parallel is OHLCV (e.g., straight after PriceDataLoader()), both branches already have OHLCV — no Load needed. Only use Store/Load when a branch needs data from a different point in the pipeline (e.g., data stored before some transformation that changed `current`).

**Common Parallel patterns:**

- Signal + filter: `{"signal": [indicator, transform], "filter": [indicator, threshold]}` → `ApplyMask(score_signal="signal", filter_signal="filter")` — ApplyMask is a SignalComposer that consumes the Parallel dict directly. Filters and confirmations are independent of the signal, so this is where they go.
- Signal + regime: `{"signal": [indicator, forecast], "regime": [detector]}` → `RegimeScale(signal_key="signal", index_key="regime")` (or `RegimeGate(..., condition=...)`) — the regime composers read both branches from the dict by key
- Multiple signals: `{"momentum": [ROC, scaler], "carry": [funding, scaler]}` → `ForecastCombiner(weights=...)`
- Weight legs on different assets: `{"long": [..., sizer], "short": [..., sizer]}` → `WeightConcatenator()` — each leg ends in WeightSeries
- Boolean mask OR/AND: `{"a": [filter_a], "b": [filter_b]}` → `MaskOr()` or `MaskAnd()` — combine multiple boolean masks into one

**Composing masks with MaskOr / MaskAnd.** When a condition requires combining multiple threshold filters (e.g., "RSI > 90 OR RSI < 10"), use a nested Parallel inside a branch:

```python fragment
"rsi_extreme": Pipeline([
    RSI(period=3),
    {
        "overbought": [AboveThresholdFilter(threshold=90)],
        "oversold":   [BelowThresholdFilter(threshold=10)],
    },
    MaskOr(),
])
```

The nested Pipeline wraps a Parallel that splits the RSI signal into two threshold checks, then MaskOr combines them into a single boolean mask. This pattern works for any "condition A OR condition B" filter. Use MaskAnd when ALL conditions must be met (e.g., low trend AND high volume).

**Full composition example — signal + trend filter + RSI confirm:**

```python fragment
{
    "signal": [KeltnerChannel(...), RollingZScoreTransform(...), NegateTransform()],
    "trend_filter": [ADX(...), BelowThresholdFilter(...)],
    "rsi_confirm": Pipeline([
        RSI(period=3),
        {"high": [AboveThresholdFilter(threshold=90)], "low": [BelowThresholdFilter(threshold=10)]},
        MaskOr(),
    ]),
}
```

Three independent branches all receive OHLCV as `current`. The signal branch computes a normalized z-score. The trend and RSI branches each produce a boolean mask. After Parallel, apply masks sequentially or combine with MaskAnd before applying to the signal.

**Two mask systems — use the right one:**

- **Universe masks** (1.0/NaN): Produced by `RollingVolumeUniverseMask` or `RollingDollarVolumeMask`, which read dollar volume from a `dollar_volume_slot` wired from `DollarVolumeLoader() -> Store(...)`. Applied via `ApplyUniverseMask(mask_slot='...')` which reads from a slot. NaN propagates through cross-sectional ops — excluded assets are invisible to z-score, forecast scaler, etc. Use for "which assets are in the tradeable universe."
- **Signal filter masks** (True/False boolean): Produced by threshold filters (`BelowThresholdFilter`, `AboveThresholdFilter`, `TopNAssetSelector`). Applied via `ApplyMask(score_signal, filter_signal)` which consumes a Parallel dict directly. False → 0.0 (asset exists but has no signal). Use for "when to trade specific assets" (trend filter, confirmation gate, etc.). Combine multiple boolean masks with `MaskOr()` or `MaskAnd()` — never `ApplyUniverseMask`, which expects the 1.0/NaN format.

**Phase ordering resets in nested Pipelines.** A nested Pipeline (including factory calls) can start from DATA phase even if the parent is in FORECAST phase. This enables multi-data-source strategies. Parallel branches do NOT reset phases — they propagate the max phase back to the parent.

**Parallel branch isolation**: Each branch gets a context snapshot — Branch B cannot see slots written by Branch A — so store shared data BEFORE the Parallel. After ALL branches complete, new slot writes merge back to parent.

**Two branches cannot write the same slot name** — raises SlotOverwriteError. This includes factories with internal Store: if the same factory is called in multiple branches with a hardcoded slot name, it collides. Fix: parameterize slot names in factories.

**Nested Pipelines share parent context** (bidirectional, no snapshot). This differs from Parallel branches. Store inside a nested Pipeline is immediately visible to the parent and vice versa.

**Load replaces the current value** — the previous pipeline value is discarded. Store is a passthrough: writes to context AND passes the value through unchanged.

**A rule reads its Position from the branch it sits in.** `TradeManager(entries=, prices=)` reads its two slots, so nothing is loaded before it. Below it, every Parallel branch receives the Position: a reader turns it into a series, ordinary components work on that series unchanged, and an action applies the result:

```python fragment
PriceDataLoader() → Store("ohlcv") → ... → ThresholdCross(...) → Store("entries") →
TradeManager(entries="entries", prices="ohlcv") →
{"stop": [TradeReturn(), BelowThresholdFilter(threshold=-0.05, inclusive=True), Exit()]} →
Exposure() → EqualWeightSizer()
```

A market value enters a rule with `Load('slot')` and keeps its full-history meaning. A rolling or cumulative step after a reader is measured from the entry (its warm-up starts at entry).

**Store('ohlcv') goes straight after PriceDataLoader().** The loader already serves `Globals(target_timeframe)` on the `bar_offset` grid, so every slot reader operates on the target timeframe from the first step.

**Time: one clock, set in `Globals`.** `Globals(target_timeframe=..., bar_offset=...)` is the strategy's clock, and every data loader follows it: `PriceDataLoader()`, `FundingDataLoader()` and the others serve that clock, on the `bar_offset` grid, themselves — so no resampler goes after a loader (`RESAMPLER_NOOP`). Only a branch that needs another clock gets `timeframe=` on its loader, and it comes back with `TargetSignalProjector()` if it is coarser or `TargetSignalResampler(method=...)` if it is finer. The full time rules, with validated examples, are in the `data_loading` pattern.

**Multiple entries ≠ scaling.** Multiple entry signals combined (ApplyMask, MaskAnd) are one entry. A second unit on a later condition is a `ScaleIn` rule.

### Dependency graph → DSL

**Then organize blocks into a dependency graph before writing DSL.** Don't jump straight to code — first figure out what depends on what:

1. **Draw dependencies**: for each building block, ask "what does this need as input?"
   - RSI needs OHLCV. MACD needs OHLCV. → Both are independent, both start from same data.
   - ThresholdCross needs normalized signal. → Depends on RSI output.
   - TradeManager needs `entries` and the price data stored. → Depends on both Store points.
   - A rule that reads `SoldFraction()` depends on the stage that sells. → It sits in a later stage (stage order = dependency).

2. **Group independent blocks into Parallel branches**: blocks that share the same input and don't depend on each other's output go in Parallel.
   - RSI + MACD both need OHLCV → `{ "rsi": [RSI, ...], "macd": [MACD, ...] }`
   - A stop and a trail both read the Position → two branches below `TradeManager`, one per rule
   - Entry + market exit from same signal → `{ "entry": [ThresholdCross, Store('entries')], "exit": [SignalReversionExit, Store('reverted')] }`

3. **Identify Store points**: only Store data that will be read later by slot-reading components. Each Store is a "checkpoint" that downstream components reference by name.
   - `Store('ohlcv')` — needed by TradeManager (`prices=`). Placed straight after `PriceDataLoader()`, which already serves the target timeframe.
   - `Store('entries')` — needed by TradeManager (`entries=`)
   - a market exit mask (e.g. `Store('reverted')`) — read by a rule as `[Load('reverted'), Exit()]`

4. **Chain the groups**: data flows through the dependency graph top-down:

   ```
   PriceDataLoader → Store('ohlcv')
     → {rsi branch, macd branch} → combine → ThresholdCross → Store('entries')
       → TradeManager(entries, prices) → {stop, trail}
         → Exposure() → EqualWeightSizer
   ```

5. **Write DSL from the chain**: each level of the graph becomes pipeline steps. Parallel groups become `{}` blocks. Store points become `Store('name')`. TradeManager reads its slots directly — no Load needed before it (Composition Mechanics).
