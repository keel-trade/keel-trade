<!-- topic: screener_view -->
<!-- topic: screen_view -->

# View language

Generated from `results.views.registry` and `results.views.generate`; edit those, never this file.

View source is a closed declarative language, stored separately from the pipeline.
Every top-level line is exactly one of: `Columns(...)`, `Page(...)`, a view (`Lines(...)`, `River(...)`, `Scatter(...)`, `Table(...)`), or a layout (`Section(...)`, `Tiles(...)`).
There is no `Control` line, no assignment, no import. A view edit always sends the COMPLETE source:
read the stored source first and keep every line the author did not ask to change.
Play configures speed and loop; it never starts playback. Section and Tiles preserve layout order.
Read with control overrides to explore; write with expected_revision to save.

## Declarations

- `Columns(<output>=Column("label", unit=, about=), ...)`: declared once; one entry per output or general series the page shows, keyed by its name. The label is the one positional argument; `unit=` is a unit token; labels and units come only from here.
- `Page(window=, start=, at=, pins=, play=, title=, about=, default=, switch=)`: declared once, keywords only. `about=` is the screen's description: one plain sentence for traders, under 280 characters, saying what the screen shows; it is the public page's description and the published link's note. `window=Last(n)` counts bars on the screen's own clock, never days: on an hourly screen `Last(720)` is 30 days, on a daily one `Last(30)` is. `default=` names the view shown first; `switch=[view ids]` lists the views a viewer may switch to (default: every view).
- `play=Play(bars_per_second=, loop=)` only as `Page(play=...)`.
- A view: `Kind("id", <channel>=..., <option>=...)`; the id is its one positional argument.

## Values: default controls, literals elsewhere

Write a literal for every value that is not one of the viewer controls below (what belongs in the view and what in the pipeline: the `screener` reference, "View or pipeline"):

```python view-fragment
filter=Rule(Raw("oi_chg7") > 5.0)
```

Every new screen ships viewer controls (the default view declares them; a screen written by chat or an agent declares them too): "Rows shown" on a River, `filter=TopN(Control("rows", 20, range=(5, 50), label="Rows shown"), by=...)`; "Days shown" on `Page(window=Last(Control("days", ...)))`; and a threshold control on the value a filter reads. The days control's values are bars, because `Last` counts bars: each option is a number of days times the screen's bars per day (24 on an hourly screen, 1 on a daily one), and the Keel app shows each as days. On an hourly screen: `Last(Control("days", 2160, options=[720, 1440, 2160, 4320], label="Days shown"))`, 30 to 180 days opening on 90; on a daily screen the same control is `Control("days", 90, options=[30, 60, 90, 180], label="Days shown")`. Any other value stays literal unless the author asks to let viewers change it. Never put a control on `highlight_top_n`: how many lines a Lines view highlights is the viewer's own Show choice in the toolbar (Top 1, 5, 10, 20 or All); write a number only to change where it opens (default 10). A top-N filter's count, `TopN(Control("n", 10, range=(1, 50)), by=...)`, is set by that same Show choice, never drawn as a slider. Two more viewer choices, only when the author asks: Colour by, `colour=Rank(Control("colour_by", "x", options=["x", "y"], label="Colour by"))`, and coins outside the filter, `others=Control("others", "grey", options=["grey", "hidden"], label="Coins outside the filter")`; every option must be drawable, as the default is. Signature: `Control("name", default, range=, step=, label=, options=)`. `range=(lo, hi)` is a pair; there is no `min=` or `max=`. `options=[...]` makes a choice control.

A control is declared by its first full form, written INLINE where its value goes. Every later use references it by name only, `Control("name")`:

```python view-fragment
filter=Rule(Raw("oi_chg7") > Control("oi_above", 5.0, range=(-40, 130), label="OI change above"))
```

Thresholds, control defaults and ranges are written in the output's stored unit.

## Rule

`Rule(cond, ...)` takes 1–3 conditions and passes a coin when all hold. A condition is one comparison (`<`, `<=`, `>`, `>=`, `==`, `!=`) between a transform and a literal or control:

```python view-fragment
Rule(Raw("oi_chg7") > 5.0, Raw("funding7") > 0.05)
```

## A fixed view (no controls)

```python view
Columns(oi_chg7=Column("7-day OI change", unit="percent"))
Page(title="OI build-up",
     about="Coins whose open interest grew more than 5% over the last week.",
     window=Last(90), default="Build-up")
Lines("Build-up", plot=Raw("oi_chg7"), filter=Rule(Raw("oi_chg7") > 5.0))
```

## A complete view source

```python view
Columns(
    oi_chg7=Column("7-day OI change", unit="percent", about="Change in open interest over the last 7 days"),
    funding7=Column("Funding, 7-day average, a year", unit="rate_per_year"),
    price=Column("Price", unit="price"),
)

Page(title="OI build-up with high funding",
     about="Coins whose open interest grew over the last week while funding stayed high.",
     window=Last(Control("days", 2160, options=[720, 1440, 2160, 4320], label="Days shown")),
     default="Build-up")

Lines("Build-up",
      plot=Raw("oi_chg7"),
      filter=Rule(Raw("oi_chg7") > Control("oi_above", 5.0, range=(-40, 130), step=1, label="OI change above"),
                  Raw("funding7") > Control("funding_above", 0.05, range=(0, 0.5), step=0.01, label="Funding above, a year")),
      colour=Pct("funding7"))

River("When it started",
      plot=Pct("oi_chg7"),
      filter=TopN(Control("n", 20, range=(1, 50), label="How many"), by=Raw("oi_chg7")))

Table("Passing now",
      columns=[Raw("oi_chg7"), Raw("funding7"), Raw("price")],
      filter=Rule(Raw("oi_chg7") > Control("oi_above"), Raw("funding7") > Control("funding_above")),
      sort=Raw("oi_chg7"))
```

`oi_above` is `5.0` because `oi_chg7` is `percent`; `funding_above` is `0.05` because `funding7` is `rate_per_year`. `days` opens on `2160` because this screen is hourly: 90 days of 24 bars. The Table references both controls by name and shows `price`, a general series, as context.

## Kinds

### Lines

Channels: plot, filter, colour, series, x.
Required: plot.
Options: relative, anchor, y_scale, highlight_top_n, highlight_by, others, colour_range, colour_scale.

```python view
Lines("Trend", plot=Raw("signal"))
```

### River

Channels: plot, filter, order, series, x.
Required: plot.
Options: order, clamp_quantile.

```python view
River("Ranks", plot=Pct("signal"))
```

### Scatter

Channels: x, y, filter, colour.
Required: x, y.
Options: trail_bars, quadrant_x, quadrant_y, labels, quadrants.

```python view
Scatter("Compare", x=Raw("signal"), y=Raw("other"))
```

### Table

Channels: columns, sort, filter, colour.
Required: columns.
Options: sort_dir, sparkline_bars.

```python view
Table("Values", columns=[Raw("signal")], sort=Raw("signal"))
```

## Calls

- `Raw`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Rank`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Pct`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Rebased`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Cum`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Drawdown`: transform; 1–1 positional arguments; keywords: across, along, start.
- `Change`: transform; 1–1 positional arguments; keywords: n, since, across, along.
- `Rolling`: transform; 1–3 positional arguments; keywords: fn, n, across, along.
- `Diff`: transform; 2–2 positional arguments; keywords: none.
- `Mean`: transform; 1–1 positional arguments; keywords: across.
- `Median`: transform; 1–1 positional arguments; keywords: across.
- `Pctl`: transform; 1–2 positional arguments; keywords: p, across.
- `Corr`: transform; 1–2 positional arguments; keywords: across.
- `TimePart`: transform; 1–2 positional arguments; keywords: part, across.
- `TopN`: filter; 1–1 positional arguments; keywords: by, at.
- `BottomN`: filter; 1–1 positional arguments; keywords: by, at.
- `Ends`: filter; 1–1 positional arguments; keywords: by, at.
- `Rule`: filter; 1–3 positional arguments; keywords: none.
- `Pinned`: filter; 0–0 positional arguments; keywords: none.
- `All`: filter; 0–0 positional arguments; keywords: none.
- `Start`: helper; 0–0 positional arguments; keywords: none.
- `Latest`: helper; 0–0 positional arguments; keywords: none.
- `Last`: helper; 1–1 positional arguments; keywords: none.
- `At`: helper; 1–1 positional arguments; keywords: none.
- `Selected`: helper; 0–1 positional arguments; keywords: none.
- `Neighbours`: helper; 1–1 positional arguments; keywords: radius.
- `Is`: helper; 2–2 positional arguments; keywords: none.
- `HLine`: helper; 1–1 positional arguments; keywords: none.
- `VLine`: helper; 1–1 positional arguments; keywords: none.
- `Ring`: helper; 1–1 positional arguments; keywords: none.
- `Best`: helper; 0–1 positional arguments; keywords: by.
- `Box`: helper; 1–1 positional arguments; keywords: none.
- `Shade`: helper; 0–0 positional arguments; keywords: where.
- `Interval`: helper; 1–1 positional arguments; keywords: none.
- `Layers`: helper; 1–12 positional arguments; keywords: none.
- `Section`: helper; 1–13 positional arguments; keywords: none.
- `Tiles`: helper; 1–12 positional arguments; keywords: none.
- `Stat`: helper; 1–1 positional arguments; keywords: label, unit.
- `Line`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Bar`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Area`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Point`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Rect`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Text`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.
- `Band`: mark; 0–0 positional arguments; keywords: x, y, colour, size, text, lo, hi, opacity, where.

Limits: 1–12 views, up to 8 pins, N from 1 to 50, up to 3 Rule conditions.
Rebased/log require positive levels. Change is a ratio for positive levels and a difference for signed rates.
Rank and Pct use average ranks across the whole universe. Missing values never pass a rule.
Page(window=Last(n)) counts bars on the screen's clock (hours on an hourly screen). All labels and unit tokens come from Columns.
Lines colour: any stored output, through Raw, Pct or Rank. Raw colours by value over the 2nd–98th percentile of the drawn values unless colour_range=(lo, hi) gives the range in the output's stored units; colour_scale="sequential" or "diverging" (default auto: diverging only when the values straddle zero).
