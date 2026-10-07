## DSL Syntax (Python-based)

**Not registered components:** `Globals`, `Universe`, `Execution`,
`Pipeline`, `Parallel`, `Store`, `StoreValue`, `Load`, `Extract` are DSL
declarations and syntax, so the component search and component detail do
not list them. Their params are documented below and in
`composition_mechanics.md`.

Strategies use Python syntax, NOT YAML. Example:

```
Globals(target_timeframe='1d')   # the clock (and any offset) live here, and only here

Universe(mode='top_volume', top_n=30, market='perp')   # criteria modes resolve server-side on save

Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', rebalance_method='to_edge')

Pipeline([
    PriceDataLoader(),        # serves 1d bars closing 00:00 UTC — no resampler
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='my_strategy')
```

Key patterns:

- **Slots**: `Store('name')` saves pipeline value, `Load('name')` retrieves it. Slot names are strings.
- **Globals**: `Globals(target_timeframe='1d')` declares the strategy's clock (and any offset) — the only place they live; data loaders follow it, and an explicit `timeframe=` on a loader is a branch on its own clock (composition mechanics). `bar_offset` is omitted unless the user asks for a different close: omitted, bars close on the timeframe's own boundary (00:00 UTC for `'1d'`). An offset moves every bar and so changes every signal and result — `bar_offset='12h'` closes the daily bar at 12:00 UTC, and one daily strategy measured −7.1% with it against +44.6% without.
- **Universe**: `Universe(mode='top_volume', top_n=30, ...)` declares which assets to trade. A manual `symbols=[...]` basket needs no resolving; criteria modes are resolved server-side when the strategy is saved (`resolved=[...]` is filled in for you) — never resolve with pipeline components. The selectors are listed in Universe Selection.
- **Execution**: `Execution(rebalance=...)` controls when the engine trades — the one place the mode rule lives. Choose it by the shape of the weights:
  - `'on_change'` — trade only when a target weight changes. **The mode for binary weights**: entry/exit (Path 2, including scaling entries and level, session and structure exits) and screen-select (Path 3). With `FixedWeightSizer` each position trades once in and once out; `EqualWeightSizer` re-divides the book whenever the number of holdings changes, and every re-division is a trade.
  - `'buffered'` — trade only when a position drifts outside a band around its target. **The mode for continuous weights** (Path 1, forecast-combine, factor tilt): `Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', rebalance_method='to_edge')`. `on_change` places the same orders as `every_bar` here, because the weights move every bar. Params: `buffer_threshold`, `buffer_mode` ('relative' = fraction of target / 'absolute' = fraction of portfolio value / 'reference' = fraction of trailing 30d mean |target weight| — keeps tolerance proportional to typical size instead of the instantaneous target), `rebalance_method` ('to_edge'/'to_center').
  - `'every_bar'` — trade every held position back to its target on every bar. Each small trim or add is a trade: the trade count and turnover inflate, and the win rate bends (up for trend following, down for mean reversion). It suits a constant-weight basket, where it (or `buffered`) corrects drift that `on_change` never touches.
- **Parallel**: a dict step, `{"branch_a": [...], "branch_b": [...]}` — each key names a branch, each value is its step list; there is no Parallel call in the DSL. Follow it with a Composer (ForecastCombiner, Crossover, ApplyMask, WeightConcatenator for weight branches) to merge, or `Extract("branch_name")` to select one branch. Branch semantics: Composition Mechanics.
- **Variables**: `xs_post = Pipeline([...])` then reference `xs_post` in main pipeline.
- **Factories**: `def signal(period): return Pipeline([...])` for parameterized sub-pipelines.
- **A strategy's source is saved whole** — an update carries the COMPLETE source with every step, not a diff.

## Strategy Template

Strategies start with three declarations (Globals, Universe, Execution) then the loader the signal reads — `PriceDataLoader()` for price-based strategies, `FundingDataLoader()` for funding-only ones (do not add a PriceDataLoader the signal never reads); both follow `Globals(target_timeframe=...)` with no resampler step. The prices a backtest trades at and the perp funding it charges are always loaded by the platform at the `Globals` clock, whatever the loaders load — a loader feeds the signal only. The three declarations change only when the timeframe, universe or execution mode changes.
