## Composition Mechanics

**Prefer Parallel for independent computations.** When a pipeline has multiple computations that share the same input but don't depend on each other's output, use Parallel branches. This is the most common case: a signal path and a filter/regime/confirmation path both read from the same OHLCV data. Parallel makes the independence explicit and produces cleaner, more readable pipelines. Use Store/Load chains only when computation B genuinely depends on the output of computation A.

**Parallel branches receive `current` automatically.** Each branch starts with the same `current` value that was flowing at the point where Parallel begins. If the data before Parallel is OHLCV (e.g., after TargetTimeframeResampler), both branches already have OHLCV — no Load needed. Only use Store/Load when a branch needs data from a different point in the pipeline (e.g., data stored before some transformation that changed `current`).

**Common Parallel patterns:**
- Signal + filter: `{ "signal": [indicator, transform], "filter": [indicator, threshold] }` → `ApplyMask(score_signal="signal", filter_signal="filter")` — ApplyMask is a SignalComposer that consumes the Parallel dict directly
- Signal + regime: `{ "signal": [indicator, forecast], "regime": [detector, Store('regime')] }` → `Extract("signal")` → `RegimeScale(regime_slot='regime')`
- Multiple signals: `{ "momentum": [ROC, scaler], "carry": [funding, scaler] }` → `ForecastCombiner(weights=...)`
- Boolean mask OR/AND: `{ "a": [filter_a], "b": [filter_b] }` → `MaskOr()` or `MaskAnd()` — combine multiple boolean masks into one

**Composing masks with MaskOr / MaskAnd.** When a condition requires combining multiple threshold filters (e.g., "RSI > 90 OR RSI < 10"), use a nested Parallel inside a branch:

```
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

```
{
    "signal": [KeltnerChannel(...), RollingZScoreTransform(...), NegateTransform()],
    "trend_filter": [ADX(...), BelowThresholdFilter(...)],
    "rsi_confirm": Pipeline([
        RSI(period=3),
        { "high": [AboveThresholdFilter(90)], "low": [BelowThresholdFilter(10)] },
        MaskOr(),
    ]),
}
```

Three independent branches all receive OHLCV as `current`. The signal branch computes a normalized z-score. The trend and RSI branches each produce a boolean mask. After Parallel, apply masks sequentially or combine with MaskAnd before applying to the signal.

**Two mask systems — use the right one:**
- **Universe masks** (1.0/NaN): Produced by `RollingVolumeUniverseMask`. Applied via `ApplyUniverseMask(mask_slot='...')` which reads from a slot. NaN propagates through cross-sectional ops — excluded assets are invisible to z-score, forecast scaler, etc. Use for "which assets are in the tradeable universe."
- **Signal filter masks** (True/False boolean): Produced by threshold filters (`BelowThresholdFilter`, `AboveThresholdFilter`, `TopNAssetSelector`). Applied via `ApplyMask(score_signal, filter_signal)` which consumes a Parallel dict directly. False → 0.0 (asset exists but has no signal). Use for "when to trade specific assets" (trend filter, confirmation gate, etc.). Combine multiple boolean masks with `MaskOr()` or `MaskAnd()`.

Do NOT use `ApplyUniverseMask` with boolean filter masks — it expects 1.0/NaN format. Use `ApplyMask` for boolean filters from Parallel branches.

**Phase ordering resets in nested Pipelines.** A nested Pipeline (including factory calls) can start from DATA phase even if the parent is in FORECAST phase. This enables multi-data-source strategies. Parallel branches do NOT reset phases — they propagate the max phase back to the parent.

**Parallel branch isolation**: Each branch gets a context snapshot. Branch B cannot see slots written by Branch A. Store shared data BEFORE the Parallel, Load it within branches. After ALL branches complete, new slot writes merge back to parent.

**Two branches cannot write the same slot name** — raises SlotOverwriteError. This includes factories with internal Store: if the same factory is called in multiple branches with a hardcoded slot name, it collides. Fix: parameterize slot names in factories.

**Nested Pipelines share parent context** (bidirectional, no snapshot). This differs from Parallel branches. Store inside a nested Pipeline is immediately visible to the parent and vice versa.

**Load replaces the current value** — the previous pipeline value is discarded. Store is a passthrough: writes to context AND passes the value through unchanged.

**Slot-reading components ignore `current`**. Several components read all their data from slots and don't use the pipeline's `current` value at all. Their `current` is a passthrough — whatever flows in passes through, but the component operates on slot data. You do NOT need to Load or manufacture a specific type for their input. Examples:
- `PositionStateMachine(entry_slot, exit_slot)` — reads entry and exit signals from slots
- `ScalingPositionManager(entry_slots, exit_slots)` — reads multiple entry and exit signals from slots
- `TrailingStopExit(entry_slot, ohlcv_slot)` — reads entries and prices from slots
- `MaxDrawdownStopLoss(entry_slot, ohlcv_slot)` — same pattern
- `TakeProfitExit(entry_slot, ohlcv_slot)` — same pattern

The typical pattern: Store data earlier in the pipeline, then slot-reading components access it directly without Load:
```
PriceDataLoader() → TargetTimeframeResampler() → Store('ohlcv') → ... → ThresholdCross() → Store('entries') →
TrailingStopExit(entry_slot='entries', ohlcv_slot='ohlcv') → Store('exits') →
PositionStateMachine(entry_slot='entries', exit_slot='exits') → EqualWeightSizer()
```
No Load needed between Store('entries') and TrailingStopExit or PSM — they read from slots.

**IMPORTANT: Store('ohlcv') goes AFTER TargetTimeframeResampler, not after PriceDataLoader.** All components — including exit conditions (TrailingStopExit, MaxDrawdownStopLoss), ReturnVolatility, and everything else — operate on the target timeframe data. PriceDataLoader outputs raw 15min data; TargetTimeframeResampler converts it to the target timeframe (e.g., 1d). Storing before resampling gives downstream components the wrong timeframe. Parallel branches also receive post-resampler data automatically via `current` — no Store/Load needed for the data already flowing.

**Multiple entries ≠ scaling.** When multiple signals combine into a single entry decision (RSI signal gated by MACD filter), that's ApplyMask → one entry → PSM. When multiple signals independently control position levels (enter 1 unit at z=1.0, another at z=1.5, another at z=2.0), that's multiple Store slots → ScalingPositionManager. The combining pattern (ApplyMask, MaskAnd, MaskOr) produces one signal. The scaling pattern stores each trigger separately and lets ScalingPositionManager count active levels.

