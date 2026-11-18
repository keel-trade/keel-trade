<!-- keywords: session, opening range, ORB, NY open, flat by EOD, session VWAP, time of day, SMC, order block, fair value gap, FVG, break of structure, BOS, CHoCH, liquidity sweep, swing, premium discount, 1R, R multiple, supply zone, demand zone, proximal, distal, midpoint, mean threshold, short -->
<!-- pattern: session_and_structure -->

# Session and price-structure (SMC) patterns

Two families, one execution model: every entry and exit here is a
close-triggered weight change (bar-close semantics), and every strategy built from them is an ENTRY/EXIT strategy: declare
`Execution(rebalance="on_change")` (the engine then trades the entry and
the exit only; under the default `every_bar` it re-trues every held
position to `weight × equity / price` on every bar — dust orders that a
live account would place for real). Pair with `FixedWeightSizer` or
`RiskSizer` when the units of a held trade must not change;
`EqualWeightSizer` re-weights held positions as the active count changes.
Keel places no resting orders and models no intrabar touch, so a level exit
is a bar-close exit, not a bracket order.

## Session family (one shared clock)

All session components take `timezone` (IANA, DST by the local clock),
`open`/`close` ("HH:MM" local), `days` ("weekdays" | "all") and `anchor`
("session" | "day" — a 00:00-anchored day for 24/7 crypto). A bar belongs to
the session containing its CLOSE label (Keel's native `close − 1ms` labels
are read as the close they name).

| Component                                                      | Output                                                                                                       | Use                                                                      |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `SessionMask`                                                  | 1/0 in-session flag                                                                                          | gate entries to a window                                                 |
| `SessionRangeHigh` / `SessionRangeLow(range_duration="30min")` | the opening-range level, NaN until the window's LAST bar closes, then frozen for the session (never partial) | ORB entries; the stop level under the range                              |
| `SessionVWAP`                                                  | session- or day-anchored VWAP (candle PROXY: typical price × volume)                                         | "price vs VWAP", `(close − VWAP)/ATR` via `SignalRatio`                  |
| `SessionCloseExit(bars_before_close)`                          | pulse on the session's final bar                                                                             | flat by EOD: `[Load('bell'), Exit()]` (its bar also refuses a new entry) |
| `SessionRelativeVolume(lookback_sessions)`                     | volume ÷ same-time-of-day average over prior sessions                                                        | session-seasonal RVOL                                                    |

The NY-open ORB composes as in `entry_exit_patterns.md`
("R-multiple ORB trade"). `CumulativeSum(reset="session",
session_mask_slot="session")` resets a running sum at each session open.

## SMC structure family (interpretation as configuration)

Every contested clause is a parameter with sourced options and a documented
default, so the school a strategy follows is set by its configuration, and
an unset clause runs the documented default. All confirm with lag (no
repaint) and are bounded by `expiry_bars` (a Keel convention).

| Component                                                     | Output                                                                                                                                                                                                                                           | Key axes                                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `SwingPivot(side, detector, width)`                           | last CONFIRMED swing level (price), ffilled                                                                                                                                                                                                      | `detector` fractal \| leg_flip; `width` 1/5/50                                                                            |
| `BreakOfStructure`                                            | signed pulse {-1,0,+1} at a close/wick break of the last swing                                                                                                                                                                                   | `trigger` close \| wick                                                                                                   |
| `ChangeOfCharacter`                                           | the reversal subset of the breaks                                                                                                                                                                                                                | `which_pivot` last_swing \| bos_origin                                                                                    |
| `LiquiditySweep`                                              | signed pulse at the RECLAIM bar of a stop hunt                                                                                                                                                                                                   | `target` swing \| equal_cluster \| period_extreme; `reclaim_bars`                                                         |
| `PremiumDiscount`                                             | range COORDINATE: 0 = range low, 1 = range high, running past both ends (~33% of readings outside [0,1])                                                                                                                                         | `range_anchor` swing_pair \| trailing_extremes (bounded); "in discount" = one-sided `BelowThresholdFilter(threshold=0.5)` |
| `FairValueGap` / `OrderBlock` / `BreakerBlock`                | signed ATR distance to the nearest ACTIVE zone (≤ 0 = inside); `output="level", at=` = the PRICE of a line (`proximal`, `midpoint`, `distal` = `output="edge"`, `top`, `bottom`, or a 0–1 fraction); `output="position"` = 0 proximal → 1 distal | `fill` / `mitigation` touch \| midpoint \| full \| close_through; `candle_select`; `confirm`; `expiry_bars`               |
| `OrderBlockEntry` / `FairValueGapEntry` / `BreakerBlockEntry` | entry signal: +1 at a bullish zone, −1 at a bearish one, on the bar price reaches the `at` line                                                                                                                                                  | the zone's axes, plus `at` proximal \| midpoint \| distal \| fraction; `reach` close \| wick                              |
| `SweepLevel`                                                  | the price of the last sweep's wick, held until the next sweep (the stop beyond a stop hunt)                                                                                                                                                      | `LiquiditySweep`'s axes; `direction` both \| bullish \| bearish                                                           |
| `Displacement`                                                | signed impulse-candle pulse                                                                                                                                                                                                                      | `body_atr_mult`, `require_fvg`                                                                                            |

The bar that REACHES a zone reports it (the zone dies from the next bar
under `touch`). A threshold on the distance
(`FairValueGap(direction="bullish") -> BelowThresholdFilter(threshold=0.0, inclusive=True)`)
is a 0/1 mask, so it enters LONG whatever the zone's side; `FairValueGapEntry`
is the entry itself, +1 at a bullish gap and −1 at a bearish one. `output="level"`
feeds `LevelStop` / `LevelTakeProfit` / `RiskRewardTarget` and
`RiskSizer(distance=...)`: store `close − level` (`SignalDifference`) as the
stop distance. A `direction="both"` zone's level is the nearest zone of either
side, so a two-sided strategy stores one level per side and scopes each rule
with `side="long"` / `side="short"`.

`PremiumDiscount` is a coordinate, not a bounded fraction: under the default
`swing_pair` anchor the dealing range is a PAIR OF LEVELS, so a close beyond
one reads past 0 or 1 (the expansion state — the same axis ICT's -0.27 /
-0.62 extension targets sit on). Gate it with the ONE-SIDED
`BelowThresholdFilter(threshold=0.5)` / `AboveThresholdFilter(threshold=0.5)`; a two-sided
`RangeSelector(low_threshold=0.0, high_threshold=0.5)` written for "in discount" drops exactly the
deepest-discount bars (a measured 36% shortfall). Use
`range_anchor="trailing_extremes"` when you want a reading bounded to [0, 1].

The stop and target below have a short form: `LevelStop(level="ob_edge")` and
`RiskRewardTarget(stop_level="ob_edge", r=2.0)` (`entry_exit_patterns`, the structure_stop_2r recipe).

```python
Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="on_change")   # every level/zone trade is entry/exit: never re-true a held position
Pipeline([
    PriceDataLoader(), Store("ohlcv"),
    OrderBlock(direction="bullish", mitigation="close_through", output="level", at="distal"), Store("ob_edge"),
    Load("ohlcv"),
    {"left": [ExtractSeries(series_name="close")], "right": [Load("ob_edge")]},
    SignalDifference(), Store("stop_dist"),   # the stop distance in price units: close − the zone edge
    Load("ohlcv"),
    {"zone": [OrderBlock(direction="bullish", mitigation="close_through"), BelowThresholdFilter(threshold=0.0, inclusive=True)],
     "flow": [TakerFlowLoader(side="net"), CumulativeSum(), SignalROC(period=3), AboveThresholdFilter(threshold=0.0)]},
    MaskAnd(), Store("entries"),
    TradeManager(entries="entries", prices="ohlcv"),
    {"numerator": [TradePnL()], "denominator": [AtEntry(slot="stop_dist")]},
    SignalRatio(),
    {"stop": [BelowThresholdFilter(threshold=-1.0, inclusive=True), Exit()],
     "target": [AboveThresholdFilter(threshold=2.0, inclusive=True), Exit()]},
    RiskSizer(risk=0.01, distance="stop_dist", max_weight=1.0),
])
```

The short side needs no flip: the entry component carries it. Short the 50% of a
bearish block, stop above the block plus half an ATR, target the last swing low:

```python
Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="on_change")
Pipeline([
    PriceDataLoader(), Store("ohlcv"),
    OrderBlock(direction="bearish", mitigation="close_through", output="level", at="top"), Store("ob_top"),
    Load("ohlcv"), SwingPivot(side="low"), Store("swing_low"),
    Load("ohlcv"), ATR(period=14), Scale(by=0.5), Store("half_atr"),
    Load("ohlcv"), OrderBlockEntry(direction="bearish", mitigation="close_through", at="midpoint"), Store("entries"),
    TradeManager(entries="entries", prices="ohlcv"),
    {"stop": [LevelStop(level="ob_top", buffer="half_atr")],
     "target": [LevelTakeProfit(level="swing_low")]},
    Exposure(), FixedWeightSizer(weight_per_position=0.1), LeverageCap(max_leverage=1.0),
])
```

Keel never claims these primitives work: no controlled test of SMC
constructs has been published, and a user-declared configuration carries
no evidence bar.
