<!-- keywords: session, opening range, ORB, NY open, flat by EOD, session VWAP, time of day, SMC, order block, fair value gap, FVG, break of structure, BOS, CHoCH, liquidity sweep, swing, premium discount, 1R, R multiple -->
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

| Component                                      | Output                                                                                                        | Key axes                                                                                                                  |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `SwingPivot(side, detector, width)`            | last CONFIRMED swing level (price), ffilled                                                                   | `detector` fractal \| leg_flip; `width` 1/5/50                                                                            |
| `BreakOfStructure`                             | signed pulse {-1,0,+1} at a close/wick break of the last swing                                                | `trigger` close \| wick                                                                                                   |
| `ChangeOfCharacter`                            | the reversal subset of the breaks                                                                             | `which_pivot` last_swing \| bos_origin                                                                                    |
| `LiquiditySweep`                               | signed pulse at the RECLAIM bar of a stop hunt                                                                | `target` swing \| equal_cluster \| period_extreme; `reclaim_bars`                                                         |
| `PremiumDiscount`                              | range COORDINATE: 0 = range low, 1 = range high, running past both ends (~33% of readings outside [0,1])      | `range_anchor` swing_pair \| trailing_extremes (bounded); "in discount" = one-sided `BelowThresholdFilter(threshold=0.5)` |
| `FairValueGap` / `OrderBlock` / `BreakerBlock` | signed ATR distance to the nearest ACTIVE zone (≤ 0 = inside), or `output="edge"` = the zone's far-edge PRICE | `fill` / `mitigation` touch \| midpoint \| full \| close_through; `candle_select`; `confirm`; `expiry_bars`               |
| `Displacement`                                 | signed impulse-candle pulse                                                                                   | `body_atr_mult`, `require_fvg`                                                                                            |

The bar that REACHES a zone reports it (the zone dies from the next bar
under `touch`), so `FairValueGap() -> BelowThresholdFilter(threshold=0.0,
inclusive=True)` is the "enter when price is in the gap" mask on the bar a
bar-close trader enters. `output="edge"` feeds an `AtEntry(slot=...)` structure stop and
`RiskSizer(distance=...)`: store `close − edge` as the stop distance.

`PremiumDiscount` is a coordinate, not a bounded fraction: under the default
`swing_pair` anchor the dealing range is a PAIR OF LEVELS, so a close beyond
one reads past 0 or 1 (the expansion state — the same axis ICT's -0.27 /
-0.62 extension targets sit on). Gate it with the ONE-SIDED
`BelowThresholdFilter(threshold=0.5)` / `AboveThresholdFilter(threshold=0.5)`; a two-sided
`RangeSelector(low_threshold=0.0, high_threshold=0.5)` written for "in discount" drops exactly the
deepest-discount bars (a measured 36% shortfall). Use
`range_anchor="trailing_extremes"` when you want a reading bounded to [0, 1].

```python
Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])
Execution(rebalance="on_change")   # every level/zone trade is entry/exit: never re-true a held position
Pipeline([
    PriceDataLoader(), Store("ohlcv"),
    OrderBlock(direction="bullish", mitigation="close_through", output="edge"), Store("ob_edge"),
    Load("ohlcv"),
    {"fast": [ExtractSeries(series_name="close")], "slow": [Load("ob_edge")]},
    Crossover(), Store("stop_dist"),   # the stop distance in price units: close − the zone edge
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

Keel never claims these primitives work: no controlled test of SMC
constructs has been published, and a user-declared configuration carries
no evidence bar.
