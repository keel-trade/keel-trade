<!-- keywords: data, loader, price, funding, predicted funding, open interest, premium, basis, PriceDataLoader, FundingDataLoader, PredictedFundingLoader, OpenInterestLoader, PremiumLoader, Globals, target_timeframe, bar_offset, served grain, agg, resample, project, align, timeframe, multi-timeframe -->
<!-- pattern: data_loading -->

# Data Sources and Alignment

Every pipeline starts with a data loader, and every loader follows the clock
the strategy declares. This file is the one place the loader/clock rules and
the multi-timeframe override patterns are taught in full; the other pattern
files reference it.

**Declare the data your signal reads — simulation data is the platform's
job.** A `PriceDataLoader` is required only when the signal itself consumes
prices. The prices every backtest trades at, and the funding a perp backtest
charges, are loaded by the platform for every run — whatever the branches
load — and always follow the `Globals` clock: bars of `target_timeframe` on the
`bar_offset` grid, from the market the data loaders name. So a strategy may
load any clocks its signal needs (15min and 1h for a 4h strategy; 1h and 4h
for a 1d one) without loading the strategy clock anywhere, and a
funding-only pipeline needs no price loader at all. A data loader's output
feeds the signal and nothing else; the result's `data_info.marks` records what
the simulation traded at (`source: "platform"`, timeframe, bar offset,
market), `funding_source: "platform"` the funding it charged, and
`funding_included: true` says funding is in the result.

## Time: one clock, and how to change, follow and re-clock it

**Control.** `Globals(target_timeframe=..., bar_offset=...)` is the strategy's
execution clock and the only place it is set; `target_timeframe` is one of
`5min 15min 30min 1h 2h 3h 4h 6h 8h 12h 1d`. Bars are labelled by their close:
without `bar_offset` they close on the timeframe's own boundary (00:00 UTC for
`1d`), and `bar_offset="12h"` moves every `1d` close to 12:00 UTC. The offset
must be shorter than the timeframe (`BAR_OFFSET_TOO_LARGE`) and a multiple of
each loader's source grain — 15min for price, 1h for the funding,
open-interest and premium loaders, 5min for the flow loaders
(`BAR_OFFSET_NOT_MULTIPLE`). A deployed strategy is evaluated at each bar close
on that grid, so changing either value changes when it trades as well as every
signal. Change the clock by editing the `Globals(...)` line. Lookbacks count
bars, not time: `ROC(period=14)` is 14 days at `1d` and 14 hours at `1h` —
restate windows when the timeframe changes.

**Follow.** Every data loader follows Globals: with no `timeframe=` it serves
the Globals clock on the `bar_offset` grid, already aggregated —
`PriceDataLoader`, `FundingDataLoader`, `OpenInterestLoader`, `PremiumLoader`,
`PredictedFundingLoader`, and the flow loaders (`DollarVolumeLoader`,
`TakerFlowLoader`, `TwapVolumeLoader`, `VWAPLoader`, `WhalePrintLoader`).
`PriceDataLoader` builds OHLCV for any clock from 5min to 1d itself; the stream
loaders aggregate with their own `agg` (funding `mean`; open interest, premium
and predicted funding `last`). No resampler goes after a loader.

**Override.** `timeframe=` on a loader is the only way to put a branch on
another clock. It is that branch's clock, not a default: the branch must come
back to the Globals clock before it meets another branch (`CLOCK_MISMATCH`) or
ends the pipeline (`TERMINAL_CLOCK_MISMATCH`).

**Floors.** `FundingDataLoader` is hourly data: under a clock finer than `1h`
it is refused (`LOADER_FINER_THAN_NATIVE`). Load it with `timeframe="1h"` and
end the branch with `TargetSignalProjector()`. Every other loader serves every
clock from 5min up.

**Re-clocking — two directions, one component pair each.**

- Coarse → fine: `TargetSignalProjector()` onto the Globals clock,
  `SignalProjector(target_timeframe=...)` onto an explicit intermediate clock.
  Each fine bar carries the last COMPLETED coarse bar — no look-ahead, no
  method; NaN (hold) before the first completed coarse bar.
- Fine → coarse: `TargetSignalResampler(method=...)` onto the Globals clock,
  `SignalResampler(target_timeframe=..., method=...)` onto an explicit
  intermediate clock. Only completed bars are aggregated; `mean` for rates,
  `last` for levels, `sum` for flows.
- The target must tile the source evenly (`CLOCK_NOT_HARMONIC`), and each pair
  goes one way only (`UPSAMPLE_NOT_SUPPORTED`, `PROJECT_WRONG_DIRECTION`).
- They are needed only where you put a branch on its own clock. A re-clocking
  step whose input already sits on its target clock — any resampler right
  after a loader — is a no-op (`RESAMPLER_NOOP`): leave it out.
- `TargetTimeframeResampler()` is not needed in new strategies: load each clock
  you want with its own `PriceDataLoader`. After a 15min load it is a second
  aggregation whose open/high/low differ from the loader's own roll on bars
  that begin without a trade.

One clock, nothing re-clocked (`1d` closing 12:00 UTC):

```python
Globals(target_timeframe="1d", bar_offset="12h")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    {
        "momentum": [PriceDataLoader(), ROC(period=14), CrossSectionalZScore()],
        "carry": [FundingDataLoader(), NegateTransform(), CrossSectionalZScore()],
    },
    ForecastCombiner(weights={"momentum": 0.5, "carry": 0.5}),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="one_clock")
```

A `15min` strategy with hourly funding, below funding's floor:

```python
Globals(target_timeframe="15min")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    {
        "momentum": [PriceDataLoader(), ROC(period=16), CrossSectionalZScore()],
        "carry": [
            FundingDataLoader(timeframe="1h"),
            NegateTransform(),
            CrossSectionalZScore(),
            TargetSignalProjector(),
        ],
    },
    ForecastCombiner(weights={"momentum": 0.5, "carry": 0.5}),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="hourly_funding_on_15min")
```

Three clocks — a `1d` regime projected down and a `15min` momentum branch
resampled up, meeting on `1h`:

```python
Globals(target_timeframe="1h")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    {
        "regime": [
            PriceDataLoader(timeframe="1d"),
            MarketTrendRegimeFilter(fast_period=5, slow_period=17),
            TargetSignalProjector(),
        ],
        "momentum": [
            PriceDataLoader(timeframe="15min"),
            ROC(period=16),
            TargetSignalResampler(method="mean"),
            ForecastScaler(avg_abs_target=10.0),
        ],
    },
    RegimeGate(signal_key="momentum", index_key="regime", condition="> 0"),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="three_clocks")
```

## Data Sources

| Loader                 | Served Timeframe                                                                                                                                                                                                                             | Output Type        | Content                                                                                                                                                                                            |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PriceDataLoader        | omit `timeframe` → serves `Globals.target_timeframe` on the `bar_offset` grid (rolled in-loader from the 1m grid; frozen 15m capture data serves only the span below a coin's first 1m bar); `timeframe=` only for a branch on its own clock | OHLCVDict          | OHLCV candles                                                                                                                                                                                      |
| FundingDataLoader      | omit → serves `Globals.target_timeframe` from the 1h native series, `agg` default `mean` (`sum`, `last`); floor 1h — settled funding is hourly and stays hourly                                                                              | StreamSeries       | Settled funding rates (the rate actually charged)                                                                                                                                                  |
| PredictedFundingLoader | omit → served grain from the 1m ctx series, `agg` default `last` (`mean`, `first`, `median`, `sum`); floor 5min                                                                                                                              | StreamSeries       | PREDICTED next-settlement funding rate — the last forecast the venue published inside the bar, never the settled rate                                                                              |
| OpenInterestLoader     | omit → served grain from the 1m ctx series, `agg` default `last` (`mean`, `first`, `median`, `sum`); floor 5min                                                                                                                              | StreamSeries       | Open interest (BASE-COIN units)                                                                                                                                                                    |
| PremiumLoader          | omit → served grain from the 1m ctx series, `agg` default `last` (`mean`, `first`, `median`, `sum`); floor 5min                                                                                                                              | StreamSeries       | Perp-vs-oracle premium (basis)                                                                                                                                                                     |
| TakerFlowLoader        | `timeframe` — omit to serve Globals.target_timeframe                                                                                                                                                                                         | StreamSeries       | Aggressor volume/count by side (net = CVD input)                                                                                                                                                   |
| WhalePrintLoader       | `timeframe` — omit to serve Globals.target_timeframe                                                                                                                                                                                         | StreamSeries       | Large-print volume by notional bucket × side                                                                                                                                                       |
| VWAPLoader             | `timeframe` — omit to serve Globals.target_timeframe                                                                                                                                                                                         | StreamSeries       | Exact per-bar trade VWAP                                                                                                                                                                           |
| DollarVolumeLoader     | `timeframe` — omit to serve Globals.target_timeframe                                                                                                                                                                                         | DollarVolumeSeries | Dollar volume: traded notional Σ price × size (USDC); before 2025-03-23 the 15m candle volume × close, spliced and stamped in provenance. Wire it into the volume components' `dollar_volume_slot` |
| TwapVolumeLoader       | `timeframe` — omit to serve Globals.target_timeframe                                                                                                                                                                                         | StreamSeries       | Venue-TWAP child-fill volume                                                                                                                                                                       |

The flow loaders read the 1m trade grid and are backtest-grade until the
live trades capture is enabled in an environment; a LIVE deployment on a
backtest-grade series is refused at deploy time. The same gate now covers the
ctx family: a live deployment of a strategy running `OpenInterestLoader`,
`PremiumLoader` or `PredictedFundingLoader` at version 2 is refused
(`BACKTEST_GRADE_SERIES`) until the live ctx minute writer is enabled in that
environment. Backtests are always allowed.

**The base grain and the served floor are two different numbers.** Price
bars, the order-flow series and the ctx family (open interest, premium,
predicted funding) are all STORED at ONE MINUTE. What a strategy may ASK for
is coarser and declared: `5min` is the finest grain the ctx loaders serve —
the finest token the platform's clock alphabet admits — because `1m` names
the partition a loader READS and is not a declarable clock. So
`Globals(target_timeframe="5min")` + `OpenInterestLoader()` serves twelve
bars an hour, and nothing finer is expressible. Settled funding is the one
exception in the other direction: `FundingDataLoader` is stored hourly, its
floor is its native `1h`, and it stays hourly. Every price read is the 1m
grid — there is no price-source switch. The venue's 15m capture is retired;
its frozen history serves only the span below a coin's first 1m bar.

**Provisional rows are served and labelled, never refused.** A serving read
asks for the `observed` cohort, so the newest minutes — the ones the archive
has not sealed yet — are in the frame rather than clipped a day back, and the
run's provenance says so: `provisional_from` carries, per coin, the first bar
that came from above the seal watermark (an empty mapping means nothing
provisional was served). The price loader on the grid, the flow loaders and
the ctx loaders all publish it, beside `seal_state: "observed"`.

**Data floors, as facts read from the system** (these two dates are pinned to
the platform's own constants by `reference/era_facts_test.py` — if the
archive moves, the test moves them): the 1m archive's first instant is
2025-03-22 (10:48 UTC), so the first full day every clock can serve a
complete bar from — the floor the flow loaders, the store gate and the
submit-time clamp all read — is **2025-03-23**. The retired 15m capture's
frozen history (captured until 2026-09-24) reaches back to 2024-06-25 for BTC,
ETH and SOL (later listings from their own first bar).

**Ask for the window you want; assets are skipped while unavailable.** A
backtest is a universe over a window. An asset that is not yet listed,
delisted before the end, listed after the floor, or missing venue bars across
a recorded gap is simply not traded while unavailable — NaN, not a refusal.
The result carries one quiet note per such asset (`SYMBOL_ABSENT`,
`SYMBOL_DATA_ENDED`, `SYMBOL_DELISTED`, `SYMBOL_UNAVAILABLE`,
`DATA_COPY_LAG`) and a `non_result` label when warm-up consumed the whole
window or no position was ever opened. The only refusal left is a window in
which none of the universe was trading at all. Do NOT narrow the universe or
the window to dodge a data edge; a window that starts before the floor
simply begins at the floor (the submit response says so).

**A delisted coin's data ends where its trading did.** Its death is the
venue's last traded minute, verified against Keel's own bars: price, flow,
funding, open interest and context rows all end at the bar containing it (that
bar is real; nothing after it is carried). A position held into it is exited at
that bar's close, and the run says so once — `SYMBOL_DELISTED` with the date
("TON stopped trading 2026-06-15 09:22 UTC (delisted on Hyperliquid) — position
exited at that bar's close") plus `symbol`, `delisted_at`, `verified` and
`exit_bar` fields. A universe resolved as of a past date keeps a coin that was
still listed then, and drops one already dead.

Open interest values are BASE-COIN units, not dollars — ranking assets
cross-sectionally on raw OI is invalid; use per-asset changes (Difference,
SignalROC) or per-asset normalization first.

Predicted funding is NOT settled funding. `PredictedFundingLoader` serves the
venue's forecast for the next settlement — the last forecast published inside
the bar, which is causal (a bar only ever carries observations inside it) but
is not the same statistic at every grain: on an hourly branch it is the
forecast as the hour closed, and on a 5min branch it is twelve refinements an
hour. `FundingDataLoader` serves the rate actually charged, hourly. The
executor's funding P&L always uses the settled series. Both series' depth is
reported by the run itself (the consumed-eras intersection rule) — read it
from the result rather than assuming the price window.

## Standard Data Opening — the loader follows Globals

`Globals(target_timeframe=..., bar_offset=...)` above the Pipeline is the ONLY
place the strategy's clock and its offset live. `PriceDataLoader()` reads both:
it serves `target_timeframe` bars, rolled up in-loader from the 1m grid —
the retired 15m capture's frozen history only below a coin's first 1m bar —
ON the `bar_offset` grid when one is declared. There
is no resampler step in the standard opening, and `Store("ohlcv_1d")` goes straight after the loader —
every downstream component already sees the target timeframe.

A `1d` strategy without `bar_offset` has bars that close at 00:00 UTC, as in
every example below. An
offset moves every bar — `bar_offset="12h"` closes the daily bar at 12:00
UTC — and so changes every signal and result; the loader rolls up on the
offset grid when one is declared.

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    {
        "fast": [EWMA(window=8, min_periods=8)],
        "slow": [EWMA(window=32, min_periods=32)],
    },
    Crossover(),
    VolatilityStandardizer(signal_type="price_points", ohlcv_slot="ohlcv_1d", window="36d", returns="pct"),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="one_clock_offset")
```

What the loader does, by served grain vs the 15m grain the clock rules
reason at (the source moved to the 1m grid; the clock rules did NOT move with
it — `PriceDataLoader`'s `clock_transfer["grain"]` is still `15min`, so every
`bar_offset` verdict on an already-valid graph is unchanged):

| Served grain (from Globals) | `bar_offset` declared | The loader                                                                     |
| --------------------------- | --------------------- | ------------------------------------------------------------------------------ |
| coarser (1h … 1d)           | no                    | rolls the source up to the grain                                               |
| coarser                     | yes                   | rolls up ON the offset grid — the offset is consumed here, nothing else needed |
| equal (15min)               | no                    | serves 15min — rolled from the 1m grid, or frozen 15m history below it         |
| equal (15min)               | yes                   | refused: `BAR_OFFSET_AT_SAME_TF` (remove `bar_offset`, or raise the target)    |

Without an offset it is the same shape: `Globals(target_timeframe="1h")` +
`PriceDataLoader()` serves hourly bars. The loader has no default timeframe
(it neither "returns 15min" nor "defaults to 15min"): it follows Globals or
an explicit literal.

What the validator says about this opening (all at write time, before any
I/O):

- **No `Globals(target_timeframe=...)` and no `timeframe=` literal** →
  `LOADER_TIMEFRAME_UNBOUND`, an error: the loader has nothing to serve.
  Declare Globals (the fix for the strategy's clock) — or pass `timeframe=`
  only if this loader is a branch on its own clock.
- **`PriceDataLoader()` followed by `TargetTimeframeResampler()`** →
  `RESAMPLER_NOOP` (warning): the loader already serves `1d`, the step
  would produce exactly that clock. Remove the resampler; keep Globals — it
  declares the execution clock.
- **`PriceDataLoader(timeframe="15min")` under a coarser Globals with nothing
  coarsening it** → `TERMINAL_CLOCK_MISMATCH` (weights on 15min, execution
  declared on 1d; plus `UNUSED_GLOBAL` for `bar_offset` when one is declared). Drop the literal
  so the loader follows Globals.
- **`Globals(target_timeframe="15min", bar_offset="5min")` + `PriceDataLoader()`**
  → `BAR_OFFSET_AT_SAME_TF`; \*\*`Globals(target_timeframe="2h", bar_offset="30min")`
  - `FundingDataLoader()`\*\* → `BAR_OFFSET_NOT_MULTIPLE` at the funding step
    (the hourly series does not tile a 30min grid; `bar_offset="1h"` does).

## Multi-timeframe: an explicit `timeframe=` is a branch on its OWN clock

The only override. A loader with an explicit `timeframe=` serves that grain
regardless of Globals, and the branch must be brought back to the declared
clock before it is combined or terminates:

- branch FINER than the global (a 15min price branch under a 1d global) → end
  it with `TargetSignalResampler(method=...)` (aggregation: `mean` for rates,
  `last` for levels, `sum` for flows);
- branch COARSER than the global (1h funding under a 15min global) → end it
  with `TargetSignalProjector()` (holds the last COMPLETED coarse bar on the
  fine grid; no method).

`TargetTimeframeResampler()` (OHLCV in, OHLCV out) is not needed in new
strategies: give each clock its own `PriceDataLoader`. After an explicit 15min
load it is a second aggregation whose open/high/low differ from the loader's
own roll on bars that begin without a trade.

Two price branches on different grains, combined at the terminal 1d clock:

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    {
        "intraday": [
            PriceDataLoader(timeframe="15min"),
            ROC(period=96),
            TargetSignalResampler(method="mean"),
            CrossSectionalZScore(),
        ],
        "daily": [
            PriceDataLoader(),
            ROC(period=14),
            CrossSectionalZScore(),
        ],
    },
    ForecastCombiner(weights={"intraday": 0.5, "daily": 0.5}),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="two_price_clocks")
```

A combiner, blender or gate whose inputs arrive on different clocks is
refused with `CLOCK_MISMATCH`, and the message names the step to add.

## Stream Data: funding, open interest, premium follow Globals too

`FundingDataLoader()`, `OpenInterestLoader()`, `PremiumLoader()` and
`PredictedFundingLoader()` serve `Globals.target_timeframe` from their native
series — the aggregation happens IN the loader, on the `bar_offset` grid when
one is declared, with a per-series default `agg`. Settled funding is the
hourly one; the other three read the venue's per-minute state:

| Loader                   | native | `agg` default | why                                                                                            | also via `agg=`                                  |
| ------------------------ | ------ | ------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `FundingDataLoader`      | 1h     | `mean`        | the rate LEVEL per hour, comparable across grains                                              | `sum` = accrued over the bar; `last` = the close |
| `OpenInterestLoader`     | 1m     | `last`        | a point-in-time level; the bar's close state is what trades                                    | `mean`, `first`, `median`, `sum`                 |
| `PremiumLoader`          | 1m     | `last`        | an instantaneous sample; the bar carries its last minute — the reading the hourly snapshot was | `mean`, `first`, `median`, `sum`                 |
| `PredictedFundingLoader` | 1m     | `last`        | a forecast the venue refines through the hour; the bar carries the last one inside it          | `mean`, `first`, `median`, `sum`                 |

`PremiumLoader`'s default moved `mean` → `last` with the move to the minute
series: the old hourly row was a point sample, so the bar's LAST minute is
what reproduces it, while a mean over sixty minutes is a different estimator.
Ask for it by name (`agg="mean"`) when you want it — it is now a genuine
within-bar average rather than an average of hourly samples.

| Served grain       | `FundingDataLoader` (native 1h)                                                                                             | `OpenInterestLoader` / `PremiumLoader` / `PredictedFundingLoader` (native 1m)                                                |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 5min, 15min, 30min | **refused** — `LOADER_FINER_THAN_NATIVE`, fix: `timeframe="1h"` on the loader + `TargetSignalProjector()` ending the branch | served: the minutes inside each bar are rolled with `agg`                                                                    |
| 1h                 | serves the hourly series as-is                                                                                              | served: the bar carries its LAST observed minute — the same venue state as the old hourly snapshot, up to one minute earlier |
| coarser (2h … 1d)  | rolls up with `agg` (closed-right, labelled by close, trailing partial bar dropped) on the offset grid                      | the same roll, from the minutes                                                                                              |

`5min` is the floor for the three ctx loaders and it is the finest token the
alphabet has, so `LOADER_FINER_THAN_NATIVE` can no longer fire for them at
all — `FundingDataLoader` is its only remaining subject. The OFFSET rule did
not move with the source: it still reasons at `1h` for all four
(`clock_transfer["grain"]`), so `Globals(target_timeframe="2h", bar_offset="30min")`
is `BAR_OFFSET_NOT_MULTIPLE` at any of them, exactly as before.

The five override patterns, each as it validates and runs:

**1. 1d global, funding at 1d — the common carry strategy.** Served 1d, mean
of the hourly rates per UTC day; no resampler step.

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    FundingDataLoader(),
    NegateTransform(),
    EWMATransform(window=10),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="funding_carry_daily")
```

**2. 1d global, 1h funding with your own smoothing.** The explicit
`timeframe="1h"` is the override; smooth on the hourly grid; the resampler
puts the smoothed series on the 1d clock (and consumes an offset when one is declared).

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    FundingDataLoader(timeframe="1h"),
    EWMATransform(window=24),
    TargetSignalResampler(method="last"),
    NegateTransform(),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="funding_hourly_smoothed_daily")
```

**3. 1d global, accrued funding per day.** `agg="sum"` — units change with
the grain (a day's accrual, not an hourly rate).

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    FundingDataLoader(agg="sum"),
    NegateTransform(),
    EWMATransform(window=10),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="funding_accrued_daily")
```

**4. 15min global, funding.** The only legal shape under a global finer than
funding's native hour: pin the loader to `"1h"` and project. A bare
`FundingDataLoader()` here is refused (`LOADER_FINER_THAN_NATIVE`) and the
validator's fix arm writes exactly these two edits.

```python
Globals(target_timeframe="15min")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    FundingDataLoader(timeframe="1h"),
    TargetSignalProjector(),
    NegateTransform(),
    EWMATransform(window=40),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="funding_on_15min")
```

**5. 15min price for one thing, 1d price for another** — the two-branch
price example above (`two_price_clocks`).

Open interest, premium and predicted funding are the same shape as pattern 1
with the ctx family's default `agg` (`last`) — and, unlike funding, they can
also be served bare under a 5min / 15min / 30min global:

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    PriceDataLoader(),
    OpenInterestLoader(),
    RollingZScoreTransform(window=20),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="open_interest_daily")
```

**What did the loader do to my series?** Read it from the backtest result,
not from prose: `data_info.loaders` carries one block per loader instance
with `timeframe` (the grain SERVED), `native_grain` (the finest partition
read: `1m` for price when the grid served any of the window and `15min`
only when the whole window lay below the grid, in the frozen capture history,
`1h` for settled funding, `1m` for the ctx family — open interest,
premium, predicted funding — and `1m` for flow), `agg` (the aggregation
applied, `null` only when served == native, so a ctx loader always names
one), `bar_offset` (the declared token),
`bar_offset_applied` (the token this loader CONSUMED, or `null`) and
`timeframe_literal` (the explicit `timeframe=` if one was written). Pattern 1
above reads `FundingDataLoader: timeframe "1d", native_grain "1h", agg
"mean"` (under `bar_offset="12h"` it would also read `bar_offset_applied
"12h"`, the offset consumed in-loader); pattern 2 reads `timeframe "1h", agg
null, bar_offset_applied null` — nothing rolled in-loader, the resampler did it.

## Funding-Only (Price-Free) Pipelines

A strategy whose signal reads only stream data is a first-class shape — no
`PriceDataLoader` needed; the loader still follows Globals:

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    FundingDataLoader(),
    NegateTransform(),
    EWMATransform(window=10),
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="funding_only_daily")
```

The platform loads the simulation prices at the strategy clock, as for every
backtest (see the note at the top; the validator notes the price-free signal
as `PRICE_MARKS_AUTO`, an info), and funding flows into the P&L as usual
(`funding_included: true`).

AssetAligner is NOT needed in the standard case — the Universe selector
passes the same resolved universe to all data loaders, so assets already
match. `SignalResampler(target_timeframe=..., method=...)` /
`SignalProjector(target_timeframe=...)` are the explicit-target forms for a
branch whose clock is neither Globals nor a loader's grain; prefer the
declaration-backed `TargetSignalResampler` / `TargetSignalProjector`.

## Universe Reduction Alignment (Advanced)

When a pipeline component drops assets from the DataFrame (VolumeUniverseReducer,
GroupAssetFilter), secondary data branches must align to the reduced set.
This is rare with Universe selector — it's only needed when something in-pipeline
explicitly reduces assets. Store the reduced OHLCV BEFORE the Parallel:

```python
Globals(target_timeframe="1d")

Universe(mode="manual", symbols=["BTC", "ETH", "SOL"])

Pipeline([
    DollarVolumeLoader(), Store("dollar_volume"),  # The reducer ranks this dollar volume
    PriceDataLoader(),
    VolumeUniverseReducer(dollar_volume_slot="dollar_volume", top_n=2),  # Drops assets — triggers alignment need
    Store("ohlcv_1d"),                     # Reduced universe stored here
    {
        "momentum": [ROC(period=20), CrossSectionalZScore()],
        "carry": [
            FundingDataLoader(),
            AssetAligner(reference_slot="ohlcv_1d"),  # Align to reduced universe
            NegateTransform(),
            CrossSectionalZScore(),
        ],
    },
    ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="reduced_universe_alignment")
```

Without AssetAligner in this case, ForecastCombiner fails with shape mismatch
because the price branch has fewer assets than the funding branch.

## Common Mistakes

- **M-10**: Missing data pipeline entirely — every pipeline needs at least
  one data loader (the loader your SIGNAL reads). Declare `PriceDataLoader`
  only when the signal consumes prices; a funding-only pipeline's loader is
  `FundingDataLoader`, and simulation marks are supplied by the platform.
  A pipeline with zero data loaders cannot run.
- **M-18**: Parallel branches with different asset counts — if any branch
  drops assets (VolumeUniverseReducer, GroupAssetFilter), all other
  branches must use AssetAligner to match.
- Pinning a stream loader to `timeframe="1h"` under a 1d global and combining
  it without `TargetSignalResampler(method=...)` — `CLOCK_MISMATCH`; a bare
  `FundingDataLoader()` needs no step at all.
- Adding `TargetTimeframeResampler()` / `TargetSignalResampler()` after a bare
  loader — `RESAMPLER_NOOP`; the loader already serves the declared clock.
- Forgetting `Store("ohlcv_1d")` — many downstream components read from this
  slot (VolatilityStandardizer, AssetAligner, etc.).
