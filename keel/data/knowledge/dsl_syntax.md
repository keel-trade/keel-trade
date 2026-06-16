## DSL Syntax (Python-based)

**Not registered components — don't call `strategy_components_search` or
`strategy_component_detail` on these:** `Globals`, `Universe`, `Execution`,
`Pipeline`, `Parallel`, `Store`, `StoreValue`, `Load`, `Extract`. Their
params are documented below and in `composition_mechanics.md`.

Strategies use Python syntax, NOT YAML. Example:

```
Globals(target_timeframe='1d')

Universe(mode='top_volume', top_n=30, market='perp', resolved=[...], resolved_at='...')

Execution(rebalance='every_bar')

Pipeline([
    PriceDataLoader(timeframe='15min'),
    TargetTimeframeResampler(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='my_strategy')
```

Key patterns:

- **Slots**: `Store('name')` saves pipeline value, `Load('name')` retrieves it. Slot names are strings.
- **Globals**: `Globals(target_timeframe='1d')` declares pipeline-wide config. Components like TargetTimeframeResampler read from Globals automatically via declaration_refs.
- **Universe**: `Universe(mode='top_volume', top_n=30, ...)` declares which assets to trade. Use `universe_resolve` tool to resolve, not pipeline components.
- **Execution**: `Execution(rebalance='every_bar')` controls when the engine trades. Modes:
  - `'every_bar'` — rebalance every bar. Use for continuous forecast strategies (Path 1) where weights change every bar.
  - `'on_change'` — trade only when weights change. **Use for entry/exit (Path 2) and screen-select (Path 3)** where binary weights only change on signal events. Using `every_bar` on discrete strategies creates unnecessary micro-rebalances.
  - `'buffered'` — trade only when positions drift outside a buffer band. Best upgrade for continuous strategies (Path 1) to reduce turnover. Params: `buffer_threshold` (0.05-0.30, fraction of target), `buffer_mode` ('relative'/'absolute'), `rebalance_method` ('to_edge'/'to_center').
    **Default by path**: Path 1 (continuous) → `'every_bar'`, suggest `'buffered'` as improvement. Path 2 (entry/exit) → always `'on_change'`. Path 3 (screen-select) → `'on_change'`.
- **Parallel**: `{'branch_a': [...], 'branch_b': [...]}` dict for parallel paths. Each branch receives `current` automatically — no Load needed for the value already flowing. Follow with a Composer (ForecastCombiner, Crossover, ApplyMask) to merge, or Extract("branch_name") to select one branch. Use Parallel whenever two computations share the same input but are independent (e.g., signal + filter → ApplyMask).
- **Variables**: `xs_post = Pipeline([...])` then reference `xs_post` in main pipeline.
- **Factories**: `def signal(period): return Pipeline([...])` for parameterized sub-pipelines.
- **update_strategy requires COMPLETE source** — not a diff. Include ALL steps.

## Strategy Template & Iterative Changes

Strategies start with three declarations (Globals, Universe, Execution) then a data pipeline: PriceDataLoader, TargetTimeframeResampler. The target_timeframe (e.g. '1d') determines what timeframe all signals operate on. Never remove or replace these blocks unless the user explicitly asks to change the timeframe or execution mode.

Match scope to the request:

- 'Add an RSI signal' → add RSI after the existing pipeline, keep everything else
- 'Build me a momentum strategy' → build a complete strategy, may restructure
- 'Change timeframe to 4h' → update Globals(target_timeframe='4h'), may adjust parameters

When the request is specific (add, change, remove), be surgical — modify only what was asked. When the request is broad (build, create, design), you have freedom to compose the full pipeline.
