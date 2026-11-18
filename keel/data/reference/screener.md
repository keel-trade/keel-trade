<!-- topic: screens -->
<!-- topic: screen -->

# Screens: stored signals with a separate view

A screen computes signals for people to inspect. It has `Outputs`, not
`Execution`; a strategy has `Execution`, not `Outputs`. Declare `Outputs`
before variables, factories and `Pipeline`. Store every named output, and
keep all output clocks aligned with `Globals`. Declare one to three outputs.
A new screen's default universe is the top 30 perps by volume,
`Universe(mode="top_volume", top_n=30, market="perp")`; a `top_volume`
universe resolves on the server when the screen runs. To cover every
active Hyperliquid perp, write `top_n=180`, the width of Keel's own launch
screens: `top_n` is a cap, so every active perp fits under it, and a much
larger number only adds coins with no recent data.
Price, volume, funding, open interest and BTC can also be view context; do
not duplicate general context merely to draw it.

The view (labels, filters, sorts, colours, viewer controls) is its own
language: the `view_language` topic is its complete syntax, and the
`screen_recipes` topic is worked screens with their views.

```python
Globals(target_timeframe="1h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["funding_7d", "ret_1d"])
Pipeline([
    {
        "funding": [
            FundingDataLoader(),
            RollingMean(window="7d"),
            Scale(by=8760, unit="rate_per_year"),
            Store("funding_7d"),
        ],
        "returns": [PriceDataLoader(), ROC(period=24), Store("ret_1d")],
    },
])
```

## View or pipeline

This is the one rule for every screen edit. A threshold, filter, sort,
colour, top-N, title, description or label over a stored output belongs in
the view, not the pipeline: a view edit saves a new view revision and
starts no run. "Coins with negative funding" is the view rule
`filter=Rule(Raw("funding_7d") < 0.0)`: a view edit, no new output and no
run. Store a mask (`BelowThresholdFilter(threshold=0.0), Store("negative")`)
only when another screen or strategy will `Use` it as a filter or universe.
A new series to compute, or a different clock or universe, is a pipeline
edit: the screen's next version.

Every new screen's view gives it a `Page(title=..., about=...)`, one
`Column("Label", unit=...)` per output (an output with no label is shown by
its raw name) and a viewer control: a threshold control on the value a
filter reads. How many lines a Lines view highlights is the viewer's own Show
choice in the toolbar (Top 1, 5, 10, 20 or All), never a `highlight_top_n`
control.

`RollingMean` averages a signal over an exact trailing duration (`7d` is
168 hourly or seven daily bars). It leaves warmup and incomplete windows
unknown. `SMA` is an OHLCV indicator; it is not a funding smoother. No
trade-window safety certification is declared for `RollingMean`.

## Roles and units

Numeric per-coin outputs infer `signal`; a yes/no mask infers `filter`; a
market-wide series infers `regime`. Declare `universe` or `event` explicitly
on a compatible mask, for example:

```python fragment
Outputs(slots=["eligible"], roles={"eligible": "universe"})
```

The validator checks role against type, shape, domain and cross-sectional
sensitivity. A role is intent, not a conversion. `Outputs` holds names and
optional roles, never labels, units, chart kinds or ranking instructions.
The view source supplies labels and presentation, including the binding
that selects the ranked output.

Read the validation result's output descriptors and unit sentences before
writing a threshold:

- `ROC` and `SignalROC` use percent × 100: 5% is **5.0**.
- Trade returns and drawdowns are fractions: 5% is **0.05**.
- Funding `agg="mean"` (including the omitted default) or `"last"` is a
  decimal per hour. `Scale(by=8760, unit="rate_per_year")` declares a yearly
  decimal; 5% a year is then **0.05**.
- Funding `agg="sum"` accrues a varying bar duration and stays `unknown` in
  v1. Scaling unknown input does not establish a known unit. Without
  `unit=`, Scale's output is unknown even when its input was known.
- A view's `Columns(unit=...)` must agree with a known output unit.
  Unknown output units may be declared by the view and are labelled as such.

Store raw, readable values by default (`ROC(period=7)` is a % change; funding
scaled to `rate_per_year`). A cross-sectional step is for an explicit ask to
rank or compare across coins: `CrossSectionalZScore` is a rank-normal score
(z = Φ⁻¹(rank/(N+1)), about ±1.85 at 30 coins); its `Column` label
names the value then the scale, e.g.
`Column("Funding · rank across coins (σ scale)", unit="score")`, never a bare
indicator name.

Raw open interest is in each coin's own units, not dollars. Its descriptor
stays unknown, and the OI presets and fourth launch template remain gated
while the cross-asset OI unit-law fix is unresolved. Do not rank raw OI or
raw base-coin volume across coins. A difference alone still has those units.

## More than one clock

A branch may read its own timeframe, then align to the output grid. The
screen below uses an offset daily grid and explicitly resamples the hourly
funding branch before storing its output. The `bar_offset="12h"` is here only
to show an offset grid: it moves every daily close, which moves every bar and
so changes every signal and result. Omit `bar_offset` unless the user asks for
a different close:

```python
Globals(target_timeframe="1d", bar_offset="12h")
Universe(mode="top_volume", top_n=30, market="perp")
Outputs(slots=["return_7d", "funding_7d"])
Pipeline([
    {
        "returns": [PriceDataLoader(), ROC(period=7), Store("return_7d")],
        "funding": [
            FundingDataLoader(timeframe="1h"),
            RollingMean(window="7d"),
            Scale(by=8760, unit="rate_per_year"),
            TargetSignalResampler(method="last"),
            Store("funding_7d"),
        ],
    },
])
```

Use a projector rather than a resampler when holding a completed coarser
series onto a finer grid. Clock alignment must be explicit and causal; the
validator does not silently resample an output.

## Reusing a screen

`Use("str_<id>@<version>", output="eligible", as_="filter")` links a
versioned output to a pipeline. Use the stored version number, not a moving
HEAD. The server checks access, the output's role and clocks, and stores a
self-contained expansion with its source/version/author evidence. The
consumer's own universe, timeframe and lookback determine execution; a Use
is not a read of the screen's latest chart or a promise of identical numbers
under a different universe. Review `USE_PIN_DIFFERS` evidence and any
universe-sensitive warning. Detach preserves the expansion's behavior and
removes the link; removing a source does not erase a consumer's stored copy.

A screen is never backtested, deployed or evaluated as a strategy directly.
Use the supported actions (test as a strategy, use as filter/universe/signal,
or pin a list into a sized book) to preview a normal strategy, then save it
atomically. Auto-refresh is an owner action distinct from Publish; private
screens can refresh.

## Tools

Save with `keel_strategy_compose` (a file with `Outputs` is a screen); run
with `keel_screener_run` (`source` runs one unsaved); read with
`keel_screener_view`, which also returns the saved view source and its
revision; change the view with `keel_screener_view_set`; list with
`keel_screener_list`. A saved version has no values until it runs, so the
order is save, run, then read. A view edit starts from the stored view:
`keel_screener_view(include_view_source=true)` returns its source and
revision, and `keel_screener_view_set` takes that revision as
`expected_revision` (never a guessed number). Publishing happens in the Keel
app. The `keel` CLI's `keel screener` commands are the same tools.
Screens need keel-trade 0.10.0 or later.
