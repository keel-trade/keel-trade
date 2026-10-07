<!-- keywords: entry, exit, stop loss, stop, take profit, target, trailing stop, trail, breakeven, partial, scale out, scale in, dca, safety order, pyramid, ladder, max hold, time exit, cooldown, re-entry, daily limit, R multiple, risk per trade, TradeManager, position, trade, binary, threshold, RSI, mean reversion, breakout -->
<!-- pattern: entry_exit -->

# Entries, exits and the position layer

## The model

Four layers, in one direction: signals (market only; entry and exit signals are built separately) → positions (TradeManager, the only layer that remembers what the strategy did) → sizing (per asset) → portfolio. Continuous forecasts skip the position layer.

Position layer: TradeManager turns entry signals into a Position; each rule is a reader, then ordinary components, then an action (Exit, Reduce, ScaleIn, AllowEntry).

- Store the entry signal (−1/0/1), then `TradeManager(entries='entries', prices='ohlcv')`.
- A rule is one branch of the dict step below it. The branch name is the rule's name and its exit reason; names never change what a rule does. "Stop OR target" is two branches.
- A trade value comes from a reader; a market value enters a rule with `Load('slot')` and keeps its full-history meaning.
- A rule that reads what another rule did (`SoldFraction()`, `AddCount()`) goes in a later stage: a second dict step.
- **Use `Execution(rebalance='on_change')`**: weights change only on entries, exits, partials and adds.

## Readers

Trade readers (unit in brackets):

- `TradeReturn()`: return from the entry close, positive when winning (fraction).
- `TradePnL()`: profit per unit from the entry close (price).
- `BarsHeld()`: bars since entry, 0 on the entry bar.
- `PeakPrice()` / `GiveBack()`: best close since entry / distance back from it (price).
- `DrawdownFromPeak()`: fraction given back from the best close (≤ 0).
- `MaxFavorable()` / `MaxAdverse()`: best / worst return so far (fraction).
- `EntryPrice()` / `AveragePrice()`: entry close / average after adds (price).
- `ReturnFromAverage()` / `ReturnSinceLastFill()`: return from the average / from the last fill (fraction).
- `UnitsHeld()` / `SoldFraction()` / `AddCount()`: units held / initial size sold / adds made.
- `TradeDirection()` / `IsLong()` / `IsShort()`: +1 or −1 / 1 while long / 1 while short.
- `AtEntry(slot='x')`: a market value sampled at entry, held for the trade.

Between-trade readers, read only in a branch ending in `AllowEntry()`:

- `BarsSinceExit()` / `LastTradeReturn()`: bars since the last exit / the previous trade's return.
- `EntryCount(window='1d')` / `LossStreak(window='1d')`: entries / consecutive losses in the window.
- `RealizedPnL(window='1d')`: realized return of partial sells and closes in the window (fraction).
- `EntriesThisSignal()` / `SignalReset()`: entries since the signal turned on / 1 when reset.

Return readers are fractions: −0.05 is a 5% loss; `-5` is −500%. Windows are the UTC day or week by default (`anchor='rolling'` trails) and need `Globals(target_timeframe=)`. `SinceEntry(agg='max')` is a running max, min, sum or mean since entry (the best R so far).

## Actions and short forms

- `Exit()` closes the whole trade on the first bar its 0/1 mask is 1.
- `Reduce(fraction=0.5)` sells a fraction of the INITIAL size, once per rule per trade.
- `ScaleIn(units=1.0, times=4)` adds each time its mask turns on, up to `times`; `on='every_bar'` adds on every bar it is on.
- `AllowEntry()` gates new entries: a trade opens only where every gate is 1 and the entry signal has reset.
- `Exposure()` turns the Position into signed units held. It sits between the rules and every sizer except `RiskSizer`, which takes the Position.

Short forms expand to the same rules: `StopLoss(pct=0.05)`, `TakeProfit(pct=0.10)` (`fraction=` makes it a `Reduce`), `TrailingStop(pct=0.08)` or `TrailingStop(atr=2.5, period=14)`, `MaxHold(bars=48)`, `Cooldown(bars=4)`; `MaxHold` and `Cooldown` also take `window='2d'`.

## Defaults

- After an exit the next trade waits for the entry signal to reset; the exit bar's own value counts. Gates AND with that wait; `reentry='any_bar'` drops it.
- Rules start the bar after entry.
- State readers report the start of the bar and see only stages above (and earlier steps of their own branch).
- On one bar: full exit, then partials, then adds; an add is skipped on a partial's bar.
- An opposite entry flips the trade, as its own event.
- A market exit's bar refuses a new entry.
- `max_units` is derived from the `ScaleIn` rules, and the validator states it.
- A missing `AtEntry` value (or `RiskSizer` distance) refuses that entry.
- On a bar with no price nothing happens.
- `Cooldown(bars=n)` re-enters from bar n + 1; a gate on a flip bar sees one row.
- A transformed exposure (a `Clip`, a combiner) is sized as a signal.

## The bracket and the market exit

```python
# canonical: bracket
Globals(target_timeframe='4h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    RSI(period=14),
    BelowThresholdFilter(threshold=30.0),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'stop':   [TradeReturn(), BelowThresholdFilter(threshold=-0.05, inclusive=True), Exit()],
        'target': [TradeReturn(), AboveThresholdFilter(threshold=0.10, inclusive=True), Exit()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

Short form: `{'stop': [StopLoss(pct=0.05)], 'target': [TakeProfit(pct=0.10)]}`. `inclusive=True` fires at or beyond the level. A market exit reads a stored mask:

```python
# canonical: market_exit
Globals(target_timeframe='4h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    RSI(period=14),
    Store('rsi'),
    BelowThresholdFilter(threshold=30.0),
    Store('entries'),
    Load('rsi'),
    AboveThresholdFilter(threshold=50.0),
    Store('rsi_recovered'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'rsi_recovered': [Load('rsi_recovered'), Exit()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

## Stateless first iteration

`ThresholdCross → EqualWeightSizer` holds a position only while the signal is beyond its threshold, with no memory. Move to `TradeManager` when the user names a stop, target, separate exit, partial, add or re-entry rule.

## More rules

**ATR trail and a trend exit.** The trail compares give-back with 2.5 ATR by difference: a ratio never fires on a zero-ATR bar. A % trail is `[DrawdownFromPeak(), BelowThresholdFilter(threshold=-0.08), Exit()]`.

```python
# canonical: atr_trail
Globals(target_timeframe='4h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    ATR(period=14),
    Scale(by=2.5),
    Store('trail_dist'),
    Load('ohlcv'),
    {'fast': [EWMA(window=20)], 'slow': [EWMA(window=80)]},
    Crossover(),
    Store('trend'),
    AboveThresholdFilter(threshold=0.0),
    Store('entries'),
    Load('trend'),
    BelowThresholdFilter(threshold=0.0),
    Store('trend_down'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'trail':      [{'fast': [GiveBack()], 'slow': [Load('trail_dist')]}, Crossover(), AboveThresholdFilter(threshold=0.0), Exit()],
        'trend_down': [Load('trend_down'), Exit()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

**Scale out, then breakeven.** Half off at +5%; after that, exit at entry. `SoldFraction()` sits in the stage below the sell. `Reduce(fraction=1.0)` sells one initial unit.

```python
# canonical: scale_out_breakeven
Globals(target_timeframe='4h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    RSI(period=14),
    Store('rsi'),
    BelowThresholdFilter(threshold=40.0),
    Store('entries'),
    Load('rsi'),
    AboveThresholdFilter(threshold=63.0, inclusive=True),
    Store('rsi_hot'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'stop':   [TradeReturn(), BelowThresholdFilter(threshold=-0.05, inclusive=True), Exit()],
        'tp1':    [TradeReturn(), AboveThresholdFilter(threshold=0.05, inclusive=True), Reduce(fraction=0.5)],
        'rsi_63': [Load('rsi_hot'), Exit()],
    },
    {
        'breakeven': [
            {
                'after_tp1': [SoldFraction(), AboveThresholdFilter(threshold=0.0)],
                'at_entry':  [TradeReturn(), BelowThresholdFilter(threshold=0.0, inclusive=True)],
            },
            MaskAnd(),
            Exit(),
        ],
    },
    Exposure(),
    FixedWeightSizer(weight_per_position=0.2),
    LeverageCap(max_leverage=1.0),
])
```

**R-multiple ORB trade, 1% risk.** R = `TradePnL()` ÷ the stop distance frozen by `AtEntry`. `stop_dist` is in price units and also sizes the trade. The bell's bar refuses a new entry.

```python
# canonical: orb_r_trade
Globals(target_timeframe='15min')
Universe(mode='manual', symbols=['ETH'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    SessionRangeLow(range_duration='30min', timezone='America/New_York', open='09:30', close='16:00'),
    Scale(by=0.999),
    Store('stop_level'),
    Load('ohlcv'),
    SessionCloseExit(bars_before_close=0, timezone='America/New_York', open='09:30', close='16:00'),
    Store('bell'),
    Load('ohlcv'),
    {'fast': [ExtractSeries(series_name='close')], 'slow': [Load('stop_level')]},
    Crossover(),
    Store('stop_dist'),
    Load('ohlcv'),
    {
        'fast': [ExtractSeries(series_name='close')],
        'slow': [SessionRangeHigh(range_duration='30min', timezone='America/New_York', open='09:30', close='16:00')],
    },
    Crossover(),
    ThresholdCross(upper=0.0, mode='long_only'),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv'),
    {'numerator': [TradePnL()], 'denominator': [AtEntry(slot='stop_dist')]},
    SignalRatio(),
    {
        'stop':   [BelowThresholdFilter(threshold=-1.0, inclusive=True), Exit()],
        'target': [AboveThresholdFilter(threshold=1.0, inclusive=True), Exit()],
        'bell':   [Load('bell'), Exit()],
    },
    RiskSizer(risk=0.01, distance='stop_dist', max_weight=1.0),
])
```

**Breakeven after +2R.** `SinceEntry(agg='max')` is the best R so far.

```python
# canonical: breakeven_after_2r
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    ATR(period=14),
    Scale(by=2.0),
    Store('one_r'),
    Load('ohlcv'),
    {'fast': [EWMA(window=24)], 'slow': [EWMA(window=96)]},
    Crossover(),
    Store('trend'),
    AboveThresholdFilter(threshold=0.0),
    Store('entries'),
    Load('trend'),
    BelowThresholdFilter(threshold=0.0),
    Store('trend_down'),
    TradeManager(entries='entries', prices='ohlcv'),
    {'numerator': [TradePnL()], 'denominator': [AtEntry(slot='one_r')]},
    SignalRatio(),
    {
        'stop':       [BelowThresholdFilter(threshold=-1.0, inclusive=True), Exit()],
        'breakeven':  [
            {
                'was_2r':   [SinceEntry(agg='max'), AboveThresholdFilter(threshold=2.0, inclusive=True)],
                'at_entry': [BelowThresholdFilter(threshold=0.0, inclusive=True)],
            },
            MaskAnd(),
            Exit(),
        ],
        'trend_down': [Load('trend_down'), Exit()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

**Time and stale exits.** Short form `MaxHold(bars=48)`. `BarsHeld()` counts from this trade's entry, not the signal (M-35).

```python
# canonical: time_and_stale
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    {'fast': [EWMA(window=24)], 'slow': [EWMA(window=96)]},
    Crossover(),
    AboveThresholdFilter(threshold=0.0),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'max_hold': [BarsHeld(), AboveThresholdFilter(threshold=48, inclusive=True), Exit()],
        'stale':    [
            {
                'old': [BarsHeld(), AboveThresholdFilter(threshold=10, inclusive=True)],
                'red': [TradeReturn(), BelowThresholdFilter(threshold=0.0)],
            },
            MaskAnd(),
            Exit(),
        ],
        'trail':    [DrawdownFromPeak(), BelowThresholdFilter(threshold=-0.05), Exit()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

**DCA from the last fill.** `max_units` = 1 + 4 = 5, so at most 5 × 0.04 per asset. Adds on `TradeReturn()` need the price to recover before each re-add; `ReturnSinceLastFill()` re-arms at each fill. A TWAP is `ScaleIn(units=1.0, times=4, on='every_bar')`.

```python
# canonical: dca
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    RSI(period=14),
    BelowThresholdFilter(threshold=35.0),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'safety': [ReturnSinceLastFill(), BelowThresholdFilter(threshold=-0.02, inclusive=True), ScaleIn(units=1.0, times=4)],
        'take':   [ReturnFromAverage(), AboveThresholdFilter(threshold=0.02, inclusive=True), Exit()],
        'stop':   [TradeReturn(), BelowThresholdFilter(threshold=-0.15, inclusive=True), Exit()],
    },
    Exposure(),
    FixedWeightSizer(weight_per_position=0.04),
    LeverageCap(max_leverage=1.0),
])
```

**Staged entries, then a trail once full.** `AddCount()` sits below the adds. A pyramid adds on `ReturnSinceLastFill()` ≥ 0.05.

```python
# canonical: staged_and_pyramid
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    ATR(period=14),
    Store('atr'),
    Load('ohlcv'),
    {'fast': [EWMA(window=24)], 'slow': [EWMA(window=96)]},
    Crossover(),
    Store('trend'),
    AboveThresholdFilter(threshold=0.0),
    Store('entries'),
    Load('trend'),
    BelowThresholdFilter(threshold=0.0),
    Store('trend_down'),
    TradeManager(entries='entries', prices='ohlcv'),
    {
        'second_third': [
            {
                'pullback': [{'numerator': [TradePnL()], 'denominator': [AtEntry(slot='atr')]}, SignalRatio(), BelowThresholdFilter(threshold=-0.5, inclusive=True)],
                'aged':     [BarsHeld(), AboveThresholdFilter(threshold=12, inclusive=True)],
            },
            MaskOr(),
            ScaleIn(units=1.0, times=1),
        ],
        'last_third': [
            {
                'pullback': [{'numerator': [TradePnL()], 'denominator': [AtEntry(slot='atr')]}, SignalRatio(), BelowThresholdFilter(threshold=-1.0, inclusive=True)],
                'aged':     [BarsHeld(), AboveThresholdFilter(threshold=24, inclusive=True)],
            },
            MaskOr(),
            ScaleIn(units=1.0, times=1),
        ],
        'trend_down': [Load('trend_down'), Exit()],
    },
    {
        'trail_when_full': [
            {
                'full':      [AddCount(), AboveThresholdFilter(threshold=2.0, inclusive=True)],
                'gave_back': [DrawdownFromPeak(), BelowThresholdFilter(threshold=-0.06)],
            },
            MaskAnd(),
            Exit(),
        ],
    },
    Exposure(),
    FixedWeightSizer(weight_per_position=0.1),
    LeverageCap(max_leverage=1.0),
])
```

**Re-entry and daily gates.** `reentry='any_bar'` lets a still-on signal re-enter after the cooldown. Two losses in a row: `[LossStreak(window='1d'), BelowThresholdFilter(threshold=2.0), AllowEntry()]`.

```python
# canonical: reentry_gates
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    {'fast': [EWMA(window=24)], 'slow': [EWMA(window=96)]},
    Crossover(),
    AboveThresholdFilter(threshold=0.0),
    Store('entries'),
    TradeManager(entries='entries', prices='ohlcv', reentry='any_bar'),
    {
        'stop':       [TradeReturn(), BelowThresholdFilter(threshold=-0.03, inclusive=True), Exit()],
        'target':     [TradeReturn(), AboveThresholdFilter(threshold=0.06, inclusive=True), Exit()],
        'cooldown':   [BarsSinceExit(), AboveThresholdFilter(threshold=4, inclusive=True), AllowEntry()],
        'two_a_day':  [EntryCount(window='1d'), BelowThresholdFilter(threshold=2.0), AllowEntry()],
        'daily_loss': [RealizedPnL(window='1d'), AboveThresholdFilter(threshold=-0.03), AllowEntry()],
    },
    Exposure(),
    EqualWeightSizer(),
])
```

**Several books.** Each book is its own `TradeManager`; `Exposure()` makes it a signal for the combiner. A trade value never crosses books.

```python
# canonical: multi_book
Globals(target_timeframe='1h')
Universe(mode='manual', symbols=['BTC', 'ETH', 'SOL'])
Execution(rebalance='on_change')
Pipeline([
    PriceDataLoader(),
    Store('ohlcv'),
    RSI(period=2),
    Store('rsi2'),
    BelowThresholdFilter(threshold=10.0),
    Store('entry_fast'),
    Load('rsi2'),
    AboveThresholdFilter(threshold=70.0),
    Store('exit_fast'),
    Load('ohlcv'),
    RSI(period=14),
    Store('rsi14'),
    BelowThresholdFilter(threshold=30.0),
    Store('entry_slow'),
    Load('rsi14'),
    AboveThresholdFilter(threshold=50.0),
    Store('exit_slow'),
    {
        'fast_book': [TradeManager(entries='entry_fast', prices='ohlcv'), {'revert': [Load('exit_fast'), Exit()]}, Exposure()],
        'slow_book': [TradeManager(entries='entry_slow', prices='ohlcv'), {'revert': [Load('exit_slow'), Exit()]}, Exposure()],
    },
    ForecastCombiner(),
    ForecastWeightNormalizer(target_leverage=1.0),
])
```

## Not expressible

- Cross-asset "max N open positions", or a loss limit across the whole book: not expressible yet, a later portfolio-layer project (`capability_boundaries`). Per asset: `EntryCount(window=)`, `RealizedPnL(window='1d')`.
- Intrabar or resting fills: rules decide at the bar close.
- A minimum hold on a continuous book; per-lot accounting (use sleeves of `TradeManager` + `Exposure()`).

> **Live and backtest positions.** Live recomputes a strategy over its last 365 days at every bar. A position that
> depends on its own past trades (a stop, a target or a re-entry gate) matches the backtest whenever the strategy has
> been flat, with its entry off, at some point in that window. If a trade stays open longer than the window, or trades
> re-enter back to back without a flat bar, live can differ from the backtest until the strategy is next flat. A
> backtest reports this as `LIVE_WINDOW_SHORTER_THAN_TRADES`. A market exit (for example `Load('regime_off') → Exit()`)
> or a time limit (`MaxHold`) keeps trades inside the window.

## Common Mistakes

- **M-35: Measuring a trade from its signal.** Stops, targets, trails and time exits are rules on the Position, never values computed from the stored entry signal.
- **M-03: Normalizing binary signals**: meaningless on {-1, 0, +1}.
- Refused by the validator: a reader above `TradeManager`; a branch below `TradeManager` that starts with a filter instead of a reader or `Load`; a state reader in its writer's stage; a gate without `AllowEntry()`; `-5` for −5%; a ±1 signal into `Exit()`.
- **Mean-reversion polarity**: NegateTransform before ThresholdCross so overbought → short.
