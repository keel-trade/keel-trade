---
name: strategy-creation
description: >
  Author a new Keel strategy from a natural-language thesis: fix path, universe
  and polarity; search every concept; batch-verify component types; draft the
  declarations and one Pipeline; dry-run until clean; save; and, when the user
  wants one, backtest and read the result. Use when the user says "create / build / make me a strategy",
  "new strategy that ...", or describes a trading idea and wants it
  implemented. Not for editing an existing strategy (strategy-fork-and-iterate)
  or for looking up one component (component-discovery).
tools:
  - keel_components_search
  - keel_components_get_many
  - keel_components_get
  - keel_strategy_compose
  - keel_backtest_run
  - keel_backtest_summarize
  - keel_backtest_compare
  - keel_strategy_readiness
  - keel_strategy_checkout
references:
  - step: 1
    topic: strategy_paths
    why: "Path 1–4 chains; Path 2 is the position layer (TradeManager and its rules), its direction and exits (the sizer table is in trading_domain)"
  - step: 1
    topic: entry_exit_patterns
    why: "the position layer: readers, actions, defaults and the canonical stop/target/trail/partial/DCA/gate examples"
  - step: 1
    topic: strategy_patterns
    why: "pattern snippets when the thesis names one: entry/exit, screen-select, pairs, beta hedge, DCA, regime"
  - step: 1
    topic: trading_domain
    why: "the timeframe floor and the per-strategy timeframe table, carry and oscillator polarity, required component pairs, complexity ladder, the entry/exit sizer table"
  - step: 1
    topic: pipeline_system
    why: "the type progression OHLCVDict → SignalSeries → … → WeightSeries"
  - step: 3
    topic: reasoning_principles
    why: "the invariants to check on the mock: terminal WeightSeries, normalize only when needed, each transform once, path matches signal type, a discrete strategy needs a way out, universe before signal"
  - step: 4
    topic: dsl_syntax
    why: "the declarations with one example; the Execution mode for each path; Parallel / Store / Load / variable / factory syntax"
  - step: 4
    topic: composition_mechanics
    why: "Parallel semantics and branch isolation, the two mask systems, slot-reading components, loaders following Globals vs an own-clock branch"
  - step: 4
    topic: universe_selection
    why: "Universe selectors, manual vs criteria modes, resolved semantics, groups — when the user names an asset scope"
  - step: 4
    topic: data_loading
    why: "validated examples of every loader/clock shape — carry, own-clock smoothing, accrual, projection, two price clocks"
  - step: 5
    topic: "rule:<CODE>"
    why: "the lesson behind any validation issue code the dry run returns"
  - step: 5
    topic: mistakes
    why: "the structural mistakes by id — most now carry a validator code with its own fix; the few with none are checked by eye"
  - step: 5
    topic: tool_usage
    why: "pipeline completeness: a backtest refuses a pipeline that does not end in weights, and names the missing step"
---

# Method

The whole construction, in order: read the thesis → name its concepts →
discover the components → mock the typed chain → draft from the skeleton →
dry-run, reading each issue's fix → save → backtest when the user wants one
→ read the result and reason about why.

## Step 1 — Read the thesis; settle three decisions before any tool call

1. **Path.** Continuous forecast (Path 1), discrete entry/exit (Path 2),
   screen-select (Path 3), direct allocation (Path 4). "buy when / sell when /
   entry / exit / crosses" → discrete; "rank / weight / tilt / score /
   forecast" → continuous. Architecture ambiguity → ask ONE question.
2. **Universe — choose the asset set.** Assets the user named →
   `Universe(mode="manual", symbols=[...])`. A scope they described (liquid
   majors, a sector) → a criteria universe: `Universe(mode="top_volume",
top_n=30, market="perp")`, the convention, or `mode="category"`. No hint
   of scope at all → ask which assets, offering the top_volume convention.
   Never write `resolved=`; the save resolves criteria and bakes the list.
   "HL perps" is `market="perp"`. Nothing injects a Universe: without one the
   strategy fails validation (`MISSING_UNIVERSE`).
3. **Polarity.** Trend-following unless the user says fade / overbought /
   oversold / reversal / contrarian / mean reversion.

Clock: `1d` unless the user names one. An unsupported timeframe is offered as
the nearest supported value and changed only with consent — never silently.

Match complexity to specificity: vague → one question, then the simplest
viable version; detailed ("EWMAC 2/8 with FDM and vol sizing") → exactly as
stated, nothing simplified.

## Step 2 — Search every concept

For each building block (data, signal, entry, exit, filter, sizing) and every
concept the user names ("beta hedge", "trailing stop", "vol targeting",
"regime"): `keel_components_search(query=<concept>)`. Search even when you know
the indicator's name — the result gives the canonical name, its output type,
and any adapter it needs (a raw indicator value wants `ThresholdCross` before
an entry slot). A concept with no clean match is said out loud, not
approximated silently; a hand-rolled substitute (a `ConstantForecast(-10)` for
a beta hedge) is a different strategy from the one asked for.

## Step 3 — Mock the graph, then batch-verify

Write the chain as names with the type you expect at each arrow (no DSL yet):

```
PriceDataLoader → OHLCVDict · ROC → SignalSeries · CrossSectionalZScore → NormalizedSignal
· ForecastScaler → ForecastSeries · ForecastCapper → ForecastSeries · ForecastWeightNormalizer → WeightSeries
```

Then ONE `keel_components_get_many(names=[...])` over every name,
standard components (`PriceDataLoader`, `Store`) included. Walk it pair-wise:
output type = next input type? required params covered? does a component
read a slot that needs an earlier `Store`? did search surface a better
candidate you did not mock? A type gap means search for a bridging component
or swap the pick — never force it. Only after the batch fits do you write
DSL: a wrong shape costs one batch call here and a compile round-trip plus a
redraft after.

Multi-signal joins: one directional signal + the other as a filter through
`ApplyMask`; `MaskAnd`/`MaskOr` combine 0/1 masks (entry filters, rule
conditions; they lose direction). Independent branches → a `Parallel` dict, not serial Store/Load;
each branch sizes to WeightSeries before `WeightConcatenator`, and the book's
gross exposure is the sum of the branch targets — split the target across
branches or cap after with `LeverageCap`.

## Step 4 — Draft from the skeleton

The declarations, then exactly one `Pipeline(...)` — the skeleton
`keel_strategy_compose`'s description carries:

```
Globals(target_timeframe='1d')
Universe(mode='top_volume', top_n=30, market='perp')
Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', rebalance_method='to_edge')
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='my_strategy')
```

`Globals(target_timeframe, bar_offset)` is the only place the clock lives.
`bar_offset` is omitted unless the user asks for a different close: omitted,
bars close on the timeframe's own boundary (00:00 UTC for `1d`). An offset
moves every bar and so changes every signal and result — `'12h'` closes the
daily bar at 12:00 UTC, and one daily strategy measured −7.1% with it against
+44.6% without; the reply says so when one is declared. `Universe(...)` is
required. `Execution(rebalance=...)`: `buffered` for Path 1 (continuous weights),
`on_change` for Paths 2 and 3 (binary weights), `every_bar` only for a
constant-weight basket. The pipeline ends in a
normalizer or sizer producing WeightSeries. No imports; component names
bare; keyword arguments only. A bare loader follows Globals with no resampler
after it; an explicit `timeframe=` on a loader is a branch on its own clock
and comes back through `TargetSignalResampler` (finer) or
`TargetSignalProjector` (coarser). A Parallel dict is consumed by a composer
or `Extract`. Add what the user asked for plus the required glue, one
sentence per addition.

## Step 5 — Dry run until clean

The most important step. `keel_strategy_compose(dry_run=True, source=...)`.
Every issue carries a `code`, a `message` and a `suggestion` — the fix. Read
the code first; `keel_help(topic="rule:<CODE>")` explains it. A parse failure
comes back the same way (its own code, the fix and the span), so an import
line or a positional argument costs one round trip, not a guess. Apply the
fix, re-run. A save does not wait for a clean validation — only parse and
compile errors block it, and a backtest run refuses a pipeline that is not
backtest-ready — so clean is the dry run's job: no error-severity issue
outstanding before the save; warnings are read and either fixed or explained
to the user. The few structural mistakes no validator code catches yet are in
the `mistakes` reference; check those by eye. Read the returned `universe`
block (mode, resolved count, preview) before moving on.

## Step 6 — Save

`keel_strategy_compose(source=..., name=<descriptive_snake_case>,
description=<the thesis in the user's words>)`. Where the host draws the
result's card the user already sees it — explain it in your own words;
elsewhere its markdown stands in. When the user asks where the strategy
stands, `keel_strategy_readiness(strategy_id=<id>)` reports its evidence; its
`next_recommended_action` is a field of that report, not an instruction.
Further edits are the `strategy-fork-and-iterate` workflow.

<!-- profile: full -->

On the CLI / local MCP, `keel_strategy_checkout <strategy_id>` puts the file in
the user's editor (project-local when cwd has `.keel/workspace.yaml`); edits
then go edit → `keel_strategy_push`, which keeps the same history.

<!-- /profile -->

## Step 7 — Backtest when the user wants one, then reason about the result

A backtest uses one run of the plan's weekly allowance: run it when the user
asked how the strategy performs, or agrees to a run.
`keel_backtest_run(strategy_id=<id>)` with no dates runs the strategy clock's
default window and cost model; pass a window only when the user named one.
`keel_backtest_summarize` shows one finished run, `keel_backtest_compare`
several variants in one call. Then read it and reason WHY — the mechanism, not the outcome: which exposure produced
the return, whether the costs, the carry or one window explain it, and how it
compares with the BTC hold reference over the same span. Prefer a principled
fix over curve-fitting one bad window; the next change is one small edit
(`strategy-fork-and-iterate`), saved as a new version of this strategy.

# Decision points

- Ask vs proceed: architecture ambiguity (trend vs MR, continuous vs discrete)
  → one question; a parameter or minor choice → proceed with the default and
  say so.
- `EqualWeightAllocator` only on Path 3 (selection is the signal); continuous
  forecasts keep conviction through `ForecastWeightNormalizer` or
  `VolTargetWeightConverter` (+ `LeverageCap`) — the validator warns
  `FORECAST_MAGNITUDE_DISCARDED` otherwise.
- Path 3 is `TopNAssetSelector` → `EqualWeightAllocator` →
  `WeightCadence(duration=…)` → `FillNaN(fill_value=0.0)`: the selection feeds
  the allocator directly, the cadence is the hold and the exit, and `FillNaN`
  keeps the terminal weights dense (`NONDENSE_TERMINAL_WEIGHTS` otherwise) —
  the `screen_select_patterns` topic has the validated example. A
  `TradeManager`'s rules sit between it and the sizer.
- Start simple: normalization, vol standardization and FDM are follow-ups,
  not defaults, for a single-signal build.

# Output shape

1. The result's `view`: where the host draws it as a card the user already
   sees it — explain it in your own words; elsewhere its markdown stands in.
   The source is in the view; `include_source` fetches it on request.
2. One-sentence thesis ("Trend-following on top-30 HL perps with
   regime-gated vol sizing").
3. The 3–5 composition choices and why.
4. `strategy_id`, `hero_url`, and the declarations the strategy carries
   (Universe, clock, Execution).
5. If a backtest ran: what it says and why.

# When NOT to use

- Modifying an existing strategy → `strategy-fork-and-iterate`.
- Looking up one component → `component-discovery`.
- Repeated tool failures → `recover-from-error`.
- Testing something already authored → `backtest-and-analyze`.

# Test prompts

1. "Build me a momentum strategy on the top 20 HL perps with 1h bars."
2. "Create a new strategy that does EWMAC 8/32 trend following with
   vol-targeted sizing and a beta hedge to BTC."
3. "Make a mean-reversion strategy on ETH using RSI overbought/oversold."
