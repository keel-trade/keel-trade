<!-- keywords: screen, screener, recipe, ranking, rank, top movers, outputs, view, columns -->
<!-- pattern: screen_recipes -->

# Screen recipes

Generated from `libs/screeners` (the launch templates and presets) by
`screeners.recipes`; edit those, never this file.

Each recipe is a request, the screen that answers it, and its view. Run one
unsaved with `keel_screener_run(source=<screen>, view=<view>)`; to keep it,
save the screen with `keel_strategy_compose`, then send the view with
`keel_screener_view_set`. Change the clock, the universe or a lookback to fit
the request; keep each output's `Store` and its `Columns` entry. Every value is
written in the unit its `Column` declares (the `screener` reference, "Roles and
units").

## Patterns the recipes share

- **Raw values first.** Store what a trader reads: `ROC(period=7)` is a
  % change, funding through `Scale(by=8760, unit="rate_per_year")` is % a year.
- **Rank across coins only when asked.** `CrossSectionalZScore()` is a
  rank-normal score (z = Φ⁻¹(rank/(N+1)), about ±1.85 at 30 coins) and
  `RankTransform(pct=True)` a 0–1 rank. A z-score output's `Column` label names
  the value then the scale, e.g.
  `Column("Funding · rank across coins (σ scale)", unit="score")`, never a
  bare indicator name. Rank a change, a ratio or a
  score, never a raw open-interest or volume level (each coin's own units).
- **Universe.** New screens start on `Universe(mode="top_volume", top_n=30,
  market="perp")`, as every recipe below does (the default, not a plan fact).
  `top_n` goes up to the plan's coins per screen (`keel_plan_usage` reports
  `screens.coins_per_screen`); on a plan that screens every coin,
  every active perp is `top_n=180`, the launch screens' width (`top_n` is
  a cap: a larger number only adds coins with no recent data). Coins the user names are
  `Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")`. A
  `top_volume` universe resolves when the screen runs.
- **"Coins where X is below / above N"** is one stored output, X, and a view
  filter: `filter=Rule(Raw("x") < 0.0)`. It is never a second, mask output.
- **Context in the view.** `price` is a general series the view can show
  (declare it in `Columns`) without storing it as an output.
- **Viewer controls.** A screen built for someone gives viewers a threshold on
  the value its filter reads. How many lines are highlighted is the viewer's
  own Show choice in the toolbar (Top 1, 5, 10, 20 or All), never a control.

```python view
Lines("funding", plot=Raw("funding_7d"), filter=Rule(Raw("funding_7d") < Control("below", 0.0, range=(-1, 0.5), step=0.01, label="Funding below, a year")))
```

## Top movers this week

Request: Top and bottom coins by seven-day return. The view says: Top and bottom coins by seven-day return, with relative volume and one-day return for context.

Screen:

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["ret_7d", "rel_vol", "ret_1d"])
Pipeline([
    PriceDataLoader(), Store("prices"), ROC(period=168), Store("ret_7d"), Load("prices"), RelativeVolume(lookback=168), CrossSectionalZScore(), Store("rel_vol"), Load("prices"), ROC(period=24), Store("ret_1d")
])
```

View:

```python view
Columns(ret_7d=Column("7-day return", unit="percent"), rel_vol=Column("Relative volume · rank across coins (σ scale)", unit="score"), ret_1d=Column("1-day return", unit="percent"), price=Column("Price", unit="price"))
Page(title="Top movers this week", about="Top and bottom coins by seven-day return, with relative volume and one-day return for context.", at=Latest(), default="movers")
Lines("movers", plot=Raw("ret_7d"), filter=Ends(Control("n", 5, range=(1, 50), label="How many at each end"), by=Raw("ret_7d")), others="grey")
Table("coins", columns=[Raw("ret_7d"), Raw("rel_vol"), Raw("ret_1d"), Raw("price")], sort=Raw("ret_7d"), filter=Ends(Control("n"), by=Raw("ret_7d")))
```

## Funding extremes

Request: Top and bottom coins by annualized seven-day average funding. The view says: Top and bottom coins by seven-day average funding, expressed as an annual rate.

Screen:

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["funding_7d", "funding_now", "funding_z"])
Pipeline([
    FundingDataLoader(), RollingMean(window="7d"), Scale(by=8760, unit="rate_per_year"), Store("funding_7d"), FundingDataLoader(), Scale(by=8760, unit="rate_per_year"), Store("funding_now"), FundingDataLoader(), RollingMean(window="7d"), CrossSectionalZScore(), Store("funding_z")
])
```

View:

```python view
Columns(funding_7d=Column("7-day average funding", unit="rate_per_year"), funding_now=Column("Current funding", unit="rate_per_year"), funding_z=Column("Funding · rank across coins (σ scale)", unit="score"))
Page(title="Funding extremes", about="Top and bottom coins by seven-day average funding, expressed as an annual rate.", at=Latest(), default="funding")
Lines("funding", plot=Raw("funding_7d"), filter=Ends(Control("n", 5, range=(1, 50), label="How many at each end"), by=Raw("funding_7d")), others="grey")
Table("coins", columns=[Raw("funding_7d"), Raw("funding_now"), Raw("funding_z")], sort=Raw("funding_7d"), filter=Ends(Control("n"), by=Raw("funding_7d")))
```

## Shorts paying longs

Request: Negative average funding, with rebased prices for context. The view says: Coins with negative seven-day average funding: shorts pay longs. Prices are rebased to compare their paths.

Screen:

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["funding_7d", "negative", "ret_7d"])
Pipeline([
    FundingDataLoader(), RollingMean(window="7d"), Scale(by=8760, unit="rate_per_year"), Store("funding_7d"), BelowThresholdFilter(threshold=0.0), Store("negative"), PriceDataLoader(), ROC(period=168), Store("ret_7d")
])
```

View:

```python view
Columns(funding_7d=Column("7-day average funding", unit="rate_per_year"), negative=Column("Shorts pay longs", unit="flag"), ret_7d=Column("7-day return", unit="percent"), price=Column("Price", unit="price"))
Page(title="Shorts paying longs", about="Coins with negative seven-day average funding: shorts pay longs. Prices are rebased to compare their paths.", at=Latest(), default="prices")
Lines("prices", plot=Rebased("price"), filter=Rule(Raw("funding_7d") < Control("funding_below", 0.0, range=(-0.5, 0.5), label="7-day funding below")), others="grey")
Table("coins", columns=[Raw("funding_7d"), Raw("negative"), Raw("ret_7d"), Raw("price")], sort=Raw("funding_7d"), filter=Rule(Raw("funding_7d") < Control("funding_below")), sort_dir="asc")
```

## Who's paying for the losing side

Request: Every perp by seven-day return against annualized funding: who pays while losing. The view says: Top left: longs paying funding while price falls. Bottom right: shorts paying funding while price rises.

Screen:

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["ret_7d", "funding_7d", "pain"])
Pipeline([
    PriceDataLoader(), ROC(period=168), Store("ret_7d"), FundingDataLoader(), RollingMean(window="7d"), Scale(by=8760, unit="rate_per_year"), Store("funding_7d"), {"paid": [Load("funding_7d")], "lost": [Load("ret_7d"), NegateTransform()]}, SignalProduct(left_key="paid", right_key="lost"), Store("pain")
])
```

View:

```python view
Columns(ret_7d=Column("7-day return", unit="percent"), funding_7d=Column("7-day average funding", unit="rate_per_year"), pain=Column("Paying while losing", unit="score"))
Page(title="Who's paying for the losing side", about="Top left: longs paying funding while price falls. Bottom right: shorts paying funding while price rises.", window=Last(720), play=Play(bars_per_second=12), at=Latest(), default="quadrants")
Scatter("quadrants", x=Raw("ret_7d"), y=Raw("funding_7d"), filter=TopN(Control("n", 10, range=(1, 50), label="How many"), by=Raw("pain")), quadrant_x=0, quadrant_y=0, labels="selection", quadrants=("Longs paying, price falling", "Longs paying, price rising", "Shorts paying, price falling", "Shorts paying, price rising"))
Table("pain", columns=[Raw("pain"), Raw("ret_7d"), Raw("funding_7d")], sort=Raw("pain"))
Lines("top-pain", plot=Raw("pain"), filter=TopN(Control("n"), by=Raw("pain")))
```

## Funding right now vs its 7-day average

Request: Funding right now next to its 7-day average, across the top coins by volume. The view says: Each coin's funding right now beside its 7-day average, both as an annual rate.

Two loader shapes, one per output. "Right now" is `FundingDataLoader(agg="last")`, the rate at each bar's close (on an hourly screen the latest hourly rate); the 7-day average is `RollingMean(window="7d")` over the hourly rates (168 of them). Both are scaled to a yearly rate, so a filter on either is written as a yearly decimal (0.2 = 20% a year).

Screen:

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["funding_now", "funding_7d"])
Pipeline([
    {
        "now": [FundingDataLoader(agg="last"), Scale(by=8760, unit="rate_per_year"), Store("funding_now")],
        "average": [FundingDataLoader(), RollingMean(window="7d"), Scale(by=8760, unit="rate_per_year"), Store("funding_7d")],
    },
])
```

View:

```python view
Columns(funding_now=Column("Current funding", unit="rate_per_year"), funding_7d=Column("7-day average funding", unit="rate_per_year"))
Page(title="Funding now vs its 7-day average", about="Each coin's funding right now beside its 7-day average, both as an annual rate.", at=Latest(), window=Last(Control("days", 2160, options=[720, 1440, 2160, 4320], label="Days shown")), default="now")
Lines("now", plot=Raw("funding_now"), filter=Ends(Control("n", 5, range=(1, 50), label="How many at each end"), by=Raw("funding_now")), others="grey")
Table("coins", columns=[Raw("funding_now"), Raw("funding_7d")], sort=Raw("funding_now"), filter=Ends(Control("n"), by=Raw("funding_now")))
```

## One indicator, ranked

These presets rank across coins (the score is the last step); for a raw value, drop the score step and label the output in its own unit. Each preset is one output on `Globals(target_timeframe="1d")` and `Universe(mode="top_volume", top_n=30, market="perp")`, written as `Outputs(slots=["<slug>"])` and `Pipeline([PriceDataLoader(), <steps>, Store("<slug>")])` (the funding preset loads `FundingDataLoader()` instead).

| Preset | Slug | Column label | Steps after the loader |
| --- | --- | --- | --- |
| Rate of change | `roc` | Rate of change · rank across coins (σ scale) | `{"numerator": [ROC(period=4)], "denominator": [ReturnVolatility(window="36d")]}, SignalRatio(), CrossSectionalZScore()` |
| MACD | `macd` | MACD · rank across coins (σ scale) | `{"numerator": [MACD(fast_period=12, slow_period=26, signal_period=9)], "denominator": [ReturnVolatility(window="36d")]}, SignalRatio(), CrossSectionalZScore()` |
| MACD slope | `macd_slope` | MACD slope · rank across coins (σ scale) | `{"numerator": [MACD(fast_period=12, slow_period=26, signal_period=9), SignalROC(period=3)], "denominator": [ReturnVolatility(window="36d")]}, SignalRatio(), CrossSectionalZScore()` |
| Relative strength | `rsi` | Relative strength · rank across coins (σ scale) | `{"numerator": [RSI(period=14)], "denominator": [ReturnVolatility(window="36d")]}, SignalRatio(), CrossSectionalZScore()` |
| Daily return | `ret` | Daily return · rank across coins (σ scale) | `{"numerator": [ExtractSeries(series_name="close"), SignalROC(period=1)], "denominator": [ReturnVolatility(window="36d")]}, SignalRatio(), CrossSectionalZScore()` |
| Relative volume | `rel_vol` | Relative volume · rank across coins (σ scale) | `RelativeVolume(lookback=20), CrossSectionalZScore()` |
| Breakout distance | `breakout` | Breakout distance · rank across coins (σ scale) | `VolatilityAdjustedPriceSeries(vol_span=32), BreakoutDistance(lookback=20), CrossSectionalZScore()` |
| Normalized true range | `natr` | Normalized true range · rank across coins (σ scale) | `NATR(period=14), CrossSectionalZScore()` |
| Mean reversion distance | `mean_rev` | Mean reversion distance · rank across coins (σ scale) | `MeanReversionDistance(sma_period=20, atr_period=14), CrossSectionalZScore()` |
| Volatility squeeze | `squeeze` | Volatility squeeze · rank across coins (σ scale) | `VolatilitySqueeze(short_period=7, long_period=21), CrossSectionalZScore()` |
| Trend fit | `trend_r2` | Trend fit · rank across coins (σ scale) | `LinearRegressionR2(window=20), CrossSectionalZScore()` |
| Funding rate | `funding` | Funding rate · rank across coins (σ scale) | `CrossSectionalZScore()` |
| Composite rank change | `rank_change` | Composite rank change | `{"roc": [ROC(period=4), RankTransform(pct=True)], "macd_slope": [MACD(fast_period=12, slow_period=26, signal_period=9), SignalROC(period=3), RankTransform(pct=True)], "rel_vol": [RelativeVolume(lookback=20), RankTransform(pct=True)]}, EqualForecastCombiner(), RankTransform(pct=True), Difference(period=1)` |
