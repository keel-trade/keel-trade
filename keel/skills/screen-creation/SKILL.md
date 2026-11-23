---
name: screen-creation
description: >
  Build a Keel screen from a plain request: split it into the outputs to
  compute and the view that shows them, pick a recipe or search components,
  write the declarations and one Pipeline ending in Store outputs, dry-run,
  run it, and set the view's title, labels and filter. Use when the user asks
  for a screen, a ranking or a list of coins ("coins with negative X", "top
  movers this week", "who is paying for the losing side"), or asks to change
  how a screen is shown. Not for a strategy that holds positions
  (strategy-creation) or for looking up one component (component-discovery).
tools:
  - keel_components_search
  - keel_components_get_many
  - keel_strategy_compose
  - keel_screener_run
  - keel_screener_view
  - keel_screener_view_set
  - keel_screener_list
references:
  - step: 1
    topic: screener
    why: "what a screen is, the view-or-pipeline rule, roles and the unit rules"
  - step: 2
    topic: screen_recipes
    why: "request → Outputs and Pipeline → Columns → view, for every shipped template and preset"
  - step: 4
    topic: dsl_syntax
    why: "declarations, Parallel, Store / Load and own-clock branches"
  - step: 4
    topic: data_loading
    why: "loader and clock shapes: carry, own-clock smoothing, two clocks"
  - step: 6
    topic: view_language
    why: "Columns, Page, Lines / River / Scatter / Table, Rule, TopN and controls"
  - step: 3
    topic: "rule:<CODE>"
    why: "the lesson behind any validation issue code the dry run returns"
---

# Method

A screen is a pipeline file with `Outputs(slots=[...])` in place of
`Execution(...)`. It computes one to three outputs per coin; the view decides
how they are shown. Most of a strategy's method applies; these are the parts
that differ.

## Step 1 — Split the request

Name what to compute (each output) and how to show it (the view). "Coins with
negative X" is ONE stored output, X, plus a view filter `Rule(Raw("x") < 0.0)`
— never a mask output. A threshold, filter, sort, colour or top-N over a
stored output is always the view.

## Step 2 — Recipe or components

A request close to a recipe (`screen_recipes`) starts from it. Otherwise
search each concept with `keel_components_search`, then one
`keel_components_get_many` over every name, as for a strategy.

## Step 3 — Declarations and Pipeline

- `Globals(target_timeframe=...)`: the user's clock, else `1h` for flow data
  and `1d` for price signals. `bar_offset` only when asked.
- `Universe`: coins the user named → `mode="manual"`; otherwise
  `mode="top_volume", top_n=30, market="perp"`, the default for a new screen (say so). A `top_volume`
  universe resolves on the server when the screen runs, saved or not. A wider `top_n` stays at or
  under the plan's coins per screen (`keel_plan_usage` reports `screens.coins_per_screen`; Plan
  limits, below).
- `Outputs(slots=["a", "b"])`: one to three names; each written by
  `Store("a")` at the end of its branch. No sizer, no weights.
- Store raw, readable values by default: `ROC(period=7)` is a % change,
  funding through `Scale(by=8760, unit="rate_per_year")` is % a year. Add a
  cross-sectional step only when the user asks to rank or compare across
  coins. `CrossSectionalZScore` is a rank-normal score
  (z = Φ⁻¹(rank/(N+1)), about ±1.85 at 30 coins), so its `Column` label
  names the value then the scale, e.g.
  `Column("Funding · rank across coins (σ scale)", unit="score")`, never a
  bare indicator name.
- Raw open interest and raw volume are in each coin's own units: rank a
  change, a ratio or a cross-sectional score, never the raw level.

## Step 4 — Dry run

`keel_strategy_compose(dry_run=true, source=...)` until no error remains. Read
each issue's code and suggestion. Read the result's `units` sentences: they
say what a value of 5 means for each output, and the reply quotes them.

## Step 5 — Run

To explore, `keel_screener_run(source=..., view=...)` runs it unsaved. To keep
it, save with `keel_strategy_compose(source=..., name=..., description=...)`
and run `keel_screener_run(strategy_id=...)`. A run with no dates covers the default
window (120 days plus warmup); pass `start_date` / `end_date` only when the user
names a window.

## Step 6 — The view

A saved screen gets a title, a one-sentence description and a label per
output: `keel_screener_view(strategy_id=..., include_view_source=true)` returns
the saved source and revision; `keel_screener_view_set` sends the complete
source back with `Page(title=..., about=...)`, one `Column("Label", unit=...)`
per output (a missing label shows the raw output name), the filter or sort
asked for, and the viewer controls below (Decision points). `dry_run=true`
checks it first.

## Step 7 — Edits

A view word (only, sort, top, label, title, colour) is Step 6: no new version,
no new run. A new series or a clock change is Steps 3–5 as the screen's next
version.

# Decision points

- Ask one question only when the request names no measure at all; a missing
  clock or universe takes the default, stated in the reply.
- Every new screen gives viewers controls: the window, and, when the view
  filters, a threshold control on that value (how many lines are highlighted
  is the viewer's own Show choice in the toolbar, never a `highlight_top_n`
  control),
  `Rule(Raw("x") < Control("below", 0.0, range=(-1, 0.5), label="Below"))`.
  Other values stay literal unless the user asks.
- Publishing a screen happens in the Keel app; `keel_app_link` opens it.

# Plan limits

- The coin limit: a dry run's `plan_findings` (or a refusal) with
  `SCREEN_COINS_OVER_PLAN` carries `remedy.apply` (`narrow_to_plan: true`).
  Save or run with it, without asking first, and say in one line what was
  kept (the result's `plan_narrowed` words) and which plans screen every
  coin (the names the refusal gives). When the user named the coins, name
  the ones left out and offer to swap any back in.
- Don't split the screen into several runs to get past the limit, and don't
  run the coins left out as further screens. Say the limit once.
- At the saved-screen limit, offer running it without saving, or replacing
  one of theirs; never delete one unasked.
- A screen run uses a backtest from the plan's allowance. The backtest wall
  says so, with what keeps working (saved screens keep refreshing without
  using any); nothing else repeats it.

# Output shape

1. The card, where the host draws it; otherwise its text.
2. One sentence: what the screen ranks, over which coins, on which clock.
3. The top values with their units and the as-of bar, read from the result.
4. Never "top picks", "buy" or a forecast.

# When NOT to use

- A strategy that holds positions or is backtested → `strategy-creation`.
- One component's contract → `component-discovery`.
- Repeated tool failures → `recover-from-error`.

# Test prompts

1. "Show me the coins with negative funding."
2. "Make a screen of the top movers this week."
3. "Who's paying for the losing side right now?"
4. "Change it to hourly."
5. "Only show the ones above 5%."
