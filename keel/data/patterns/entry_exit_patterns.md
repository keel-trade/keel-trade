<!-- keywords: entry, exit, threshold, binary, RSI, overbought, oversold, buy, sell, state, PositionStateMachine, ThresholdCross, mean reversion, breakout, zero cross -->
<!-- pattern: entry_exit -->

# Discrete Entry/Exit

Threshold-based buy/sell signals with explicit position state management.
Best for mean-reversion (buy oversold, sell overbought) and breakout strategies.

**Always use `Execution(rebalance='on_change')`** for entry/exit strategies. Binary weights only change on entry/exit events — `every_bar` causes unnecessary micro-rebalances every bar even when no signal fired.

## Two Approaches

### Stateless (simpler, start here)
ThresholdCross re-evaluates every bar — no position memory. Position is active only while signal stays beyond threshold. Good for first iteration.

### Stateful (separate entry/exit signals)
Build entry and exit signals independently, combine with PositionStateMachine. Enables different entry and exit thresholds (e.g., enter at z=±2, exit when z crosses 0). Use when the user specifies distinct entry and exit conditions.

## Component Sequence — Stateless

1. **PriceDataLoader** + **TargetTimeframeResampler** - Load and resample data
2. **Indicator** (KeltnerChannel, RSI, etc.) - Compute signal
3. **Normalization** (optional) - RollingZScoreTransform, CrossSectionalZScore
4. **NegateTransform** (if needed) - Flip polarity for mean reversion
5. **ThresholdCross** (upper=2, lower=-2) - Generate positions → BinarySignal
6. **EqualWeightSizer** - Allocate → WeightSeries

## Component Sequence — Stateful

1. **Data pipeline** - PriceDataLoader, TargetTimeframeResampler
2. **Signal computation** (Parallel if filter/confirm needed):
   - Signal branch: Indicator → Normalize → NegateTransform (if MR)
   - Filter branch: ADX/other → ThresholdFilter (independent, in Parallel)
3. **ApplyMask** - Merge signal + filter from Parallel dict
4. **Entry signal**: ThresholdCross(upper=2, lower=-2) → Store('entries')
5. **Exit signal**: ThresholdCross(upper=0, lower=0) → Store('exits')
   - Or: TimeBasedExitFilter(entry_slot='entries', hold_periods=5) → Store('exits')
6. **PositionStateMachine**(entry_slot='entries', exit_slot='exits', exit_mode='directional')
7. **EqualWeightSizer** → WeightSeries

## Directional Exits with PositionStateMachine

`exit_mode='directional'`: exit fires when exit signal **opposes** current position (exit × state < 0).

Use ThresholdCross(upper=0, lower=0) as exit signal:
- Signal > 0 → exit_signal = +1 → exits shorts (state=-1), holds longs (state=+1)
- Signal < 0 → exit_signal = -1 → exits longs (state=+1), holds shorts (state=-1)

This enables "enter at extreme, exit at mean" — the most common mean-reversion pattern.

`exit_mode='scalar'` (default): exit fires when exit_signal == 1.0, direction-blind. Use with TimeBasedExitFilter or ValueStopExitFilter.

## Stateful Example — Enter at ±2, Exit at Zero Cross, with Filter + Confirm

Decomposition for: "KeltnerChannel z-score, enter at ±2, exit at z=0, skip strong trends, confirm with RSI(3) extremes"

| Block | Intent | Component(s) |
|-------|--------|--------------|
| Signal | (close-EMA20)/ATR20 | KeltnerChannel(20,1) |
| Normalize | 60-bar z-score | RollingZScoreTransform(60), NegateTransform |
| Entry | z > 2 short, z < -2 long | ThresholdCross(2, -2) |
| Exit | z crosses 0 | ThresholdCross(0, 0) + directional PSM |
| Filter | skip strong trends | ADX(14) → BelowThresholdFilter(25) |
| Confirm | RSI(3) > 90 or < 10 | RSI → AboveThreshold(90) + BelowThreshold(10) → MaskOr |

Bar-by-bar trace (negated z-score, directional exit):
```
negated_z  entry        exit         PSM state
+2.5       +1 (long)    +1 (z>0)     entry wins  → LONG
+1.5       0            +1 (z>0)     same sign   → HOLD
-0.3       0            -1 (z<0)     opposes +1  → EXIT  ← z crossed 0
-2.5       -1 (short)   -1 (z<0)     entry wins  → SHORT
-1.5       0            -1 (z<0)     same sign   → HOLD
+0.3       0            +1 (z>0)     opposes -1  → EXIT  ← z crossed 0
```

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(timeframe='15min'),
    TargetTimeframeResampler(),

    # ── signal + filter + confirm (all independent → parallel) ──
    {
        "signal": [
            KeltnerChannel(period=20, multiplier=1),
            RollingZScoreTransform(window=60),
            NegateTransform(),
        ],
        "trend_filter": [
            ADX(period=14),
            BelowThresholdFilter(threshold=25, inclusive=True),
        ],
        "rsi_confirm": Pipeline([
            RSI(period=3),
            {
                "overbought": [AboveThresholdFilter(threshold=90)],
                "oversold":   [BelowThresholdFilter(threshold=10)],
            },
            MaskOr(),
        ]),
    },

    # ── combine filters, then apply to signal ──
    #   trend_filter AND rsi_confirm → single mask, then mask the signal
    # (For simplicity, apply sequentially or combine with nested MaskAnd)

    # ── entry + exit (both consume masked z-score → parallel) ──
    {
        "entry": [ThresholdCross(upper=2, lower=-2), Store('entries')],
        "exit":  [ThresholdCross(upper=0, lower=0), Store('exits')],
    },

    # ── position management + sizing ──
    PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='directional'),
    EqualWeightSizer(),
], name="keltner_mean_reversion")
```

Key composition patterns shown:
- **Nested Parallel**: RSI branch contains its own inner Parallel (overbought/oversold) → MaskOr
- **MaskOr**: composes two boolean filters (RSI > 90 OR RSI < 10) into a single mask
- **Three independent branches**: signal, trend filter, RSI confirm all receive OHLCV as `current`
- **Entry/exit in Parallel**: both consume the same masked z-score, Store to separate slots
- **Directional PSM**: exit_mode='directional' holds until exit signal opposes position

## Exit Condition Components

Six exit components are available. All output `1.0` (exit) / `0.0` (hold) and use `PSM(exit_mode='scalar')`.

| Component | What it does | Needs entry_slot? | Needs ohlcv_slot? |
|-----------|-------------|-------------------|-------------------|
| `SignalReversionExit(exit_threshold)` | Exit when abs(signal) <= threshold | No | No |
| `TrailingStopExit(entry_slot, ohlcv_slot, atr_multiplier, atr_period)` | ATR trailing stop, per-trade | Yes | Yes |
| `MaxDrawdownStopLoss(entry_slot, ohlcv_slot, drawdown_threshold)` | Fixed % stop loss from entry | Yes | Yes |
| `TakeProfitExit(entry_slot, ohlcv_slot, profit_threshold)` | Fixed % take profit from entry | Yes | Yes |
| `TimeBasedExitFilter(entry_slot, hold_periods)` | Exit after N bars | Yes | No |
| `ValueStopExitFilter(entry_slot, direction)` | Exit when momentum reverses | Yes | No |

Slot-reading components ignore `current` — they read entry signals and/or OHLCV from pipeline slots. No `Load()` needed before them.

### Pattern: Signal Reversion Exit

Replaces the confusing `ThresholdCross(0,0)` + `PSM(exit_mode='directional')` pattern. Entry fires when signal is extreme, exit fires when it reverts to neutral.

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(),
    TargetTimeframeResampler(),
    RSI(period=14),
    RollingZScoreTransform(window=100),
    NegateTransform(),
    {
        "entry": [ThresholdCross(upper=1.5, lower=-1.5), Store('entries')],
        "exit":  [SignalReversionExit(exit_threshold=0.5), Store('exits')],
    },
    PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar'),
    EqualWeightSizer(),
])
```

The gap between entry threshold (1.5) and exit threshold (0.5) creates hysteresis that prevents whipsaw.

### Pattern: Trailing Stop

Per-trade ATR trailing stop. Resets on each new entry. Handles long and short via entry signal direction.

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(),
    TargetTimeframeResampler(),
    Store('ohlcv'),
    RSI(period=14),
    RollingZScoreTransform(window=100),
    NegateTransform(),
    ThresholdCross(upper=1.5, lower=-1.5),
    Store('entries'),
    TrailingStopExit(entry_slot='entries', ohlcv_slot='ohlcv', atr_multiplier=2.5, atr_period=14),
    Store('exits'),
    PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar'),
    EqualWeightSizer(),
])
```

### Pattern: Stop Loss + Take Profit (combined)

Use Parallel + MaskOr to combine multiple exit conditions — whichever fires first closes the position.

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(),
    TargetTimeframeResampler(),
    Store('ohlcv'),
    MACD(fast_period=12, slow_period=26, signal_period=9),
    RollingZScoreTransform(window=100),
    ThresholdCross(upper=0.5, lower=-0.5),
    Store('entries'),
    {
        "stop_loss":   [MaxDrawdownStopLoss(entry_slot='entries', ohlcv_slot='ohlcv', drawdown_threshold=0.05)],
        "take_profit": [TakeProfitExit(entry_slot='entries', ohlcv_slot='ohlcv', profit_threshold=0.10)],
    },
    MaskOr(),
    Store('exits'),
    PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar'),
    EqualWeightSizer(),
])
```

### Pattern: Trailing Stop + Time-based (whichever fires first)

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(),
    TargetTimeframeResampler(),
    Store('ohlcv'),
    # ... entry signal chain ...
    Store('entries'),
    {
        "trailing": [TrailingStopExit(entry_slot='entries', ohlcv_slot='ohlcv', atr_multiplier=2.5)],
        "time":     [TimeBasedExitFilter(entry_slot='entries', hold_periods=20)],
    },
    MaskOr(),
    Store('exits'),
    PositionStateMachine(entry_slot='entries', exit_slot='exits', exit_mode='scalar'),
    EqualWeightSizer(),
])
```

## Scaling Entries (Pyramiding / DCA / Laddered)

`ScalingPositionManager` outputs integer position levels {-N, ..., 0, ..., +N} instead of binary {-1, 0, +1}. Use for DCA, pyramiding, or laddered entries where you want to scale into positions over time.

Downstream sizers (FixedWeightSizer, EqualWeightSizer) work without changes — `positions * weight_per_position` naturally scales with level.

### Two Entry Modes

**Single slot** (DCA / pyramiding): Each 0→non-zero transition increments level.
```python
ScalingPositionManager(entry_slots='entries', exit_slots='exits', max_entries=3)
```

**Multiple slots** (laddered entries): Each slot independently controls one level.
```python
ScalingPositionManager(entry_slots=['entry_1', 'entry_2', 'entry_3'], exit_slots='exits')
```

### Exit Modes

- `exit_mode='all'` (default): Any exit closes entire position (level → 0)
- `exit_mode='one_level'`: Each exit decrements by 1 (partial exits)

### Pattern: Laddered RSI Entries

Enter more aggressively as conditions worsen, exit when signal reverts.

```python
Globals(target_timeframe='1d')
Pipeline([
    PriceDataLoader(),
    TargetTimeframeResampler(),
    RSI(period=14),
    RollingZScoreTransform(window=100),
    NegateTransform(),
    {
        "entry_1": [ThresholdCross(upper=1.5, lower=-1.5, mode='long_only'), Store('entry_1')],
        "entry_2": [ThresholdCross(upper=2.0, lower=-2.0, mode='long_only'), Store('entry_2')],
        "entry_3": [ThresholdCross(upper=2.5, lower=-2.5, mode='long_only'), Store('entry_3')],
        "exit":    [SignalReversionExit(exit_threshold=0.5), Store('exits')],
    },
    ScalingPositionManager(entry_slots=['entry_1', 'entry_2', 'entry_3'], exit_slots='exits'),
    FixedWeightSizer(weight_per_position=0.10),
], name="laddered_rsi")
```

Level 1 → 10%, level 2 → 20%, level 3 → 30%. Exit reverts to 0%.

### Pattern: Partial Exits (Scale Out)

```python
Pipeline([
    # ... entry signal chain ...
    {
        "entry_1": [..., Store('entry_1')],
        "entry_2": [..., Store('entry_2')],
        "entry_3": [..., Store('entry_3')],
        "exit_tp1":   [TakeProfitExit(entry_slot='entry_1', ohlcv_slot='ohlcv', profit_threshold=0.05), Store('exit_tp1')],
        "exit_tp2":   [TakeProfitExit(entry_slot='entry_1', ohlcv_slot='ohlcv', profit_threshold=0.10), Store('exit_tp2')],
        "exit_trail": [TrailingStopExit(entry_slot='entry_1', ohlcv_slot='ohlcv', atr_multiplier=2.5), Store('exit_trail')],
    },
    ScalingPositionManager(
        entry_slots=['entry_1', 'entry_2', 'entry_3'],
        exit_slots=['exit_tp1', 'exit_tp2', 'exit_trail'],
        exit_mode='one_level',
    ),
    FixedWeightSizer(weight_per_position=0.10),
])
```

Level 3 (30%). TP1 → level 2 (20%). TP2 → level 1 (10%). Trail → flat.

## Common Mistakes

- **M-03**: Normalizing binary signals with CrossSectionalZScore — meaningless
  on {-1, 0, +1} values. Binary signals skip normalization entirely.
- **Missing exit logic**: ThresholdCross alone is stateless. If the user specifies
  separate entry/exit conditions, use PositionStateMachine.
- **Wrong polarity for mean reversion**: High indicator value = overbought. For mean
  reversion, use NegateTransform BEFORE ThresholdCross so overbought → short.
- **Using ApplyUniverseMask for signal filters**: Threshold filters produce True/False
  (boolean). Use ApplyMask (SignalComposer), not ApplyUniverseMask (expects 1.0/NaN).
- **Loading inside Parallel when unnecessary**: Parallel branches receive `current`
  automatically. Only Load when you need data from a different pipeline point.
