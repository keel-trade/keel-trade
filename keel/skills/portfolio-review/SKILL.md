---
name: portfolio-review
description: >
  Summarise the strategies running on the user's account, read-only:
  positions, recent fills, drawdown against backtest expectations, and
  anything errored or stale. It changes nothing; starting or stopping a
  strategy happens in the Keel web app. Use when the user asks "how are my
  strategies doing", "what's running", "portfolio status", or asks about
  several running strategies at once. Not for backtesting a candidate
  (backtest-and-analyze).
tools:
  - keel_live_monitor
  - keel_app_link
references:
  - step: 2
    topic: capability_boundaries
    why: "per-series freshness and bar-close semantics — what a recorded view can and cannot state"
  - step: 3
    topic: trading_domain
    why: "leverage caps; why Sharpe over a short sample is noise"
---

# Method

Every call here reads; none changes a running strategy.

## Step 1 — Enumerate

`keel_live_monitor(view="portfolio")` with no `deployment_id` — the summary
across every running strategy (and account, unless the user named one). Read
the `freshness` block first: the portfolio view is recorded backend state and
can lag the web dashboard.

## Step 2 — Read each strategy the user cares about

- **Positions** — `keel_live_monitor(deployment_id=<id>, view="positions")`,
  an on-demand exchange snapshot of what one strategy holds now. For several
  strategies, say when you only have the recorded summary.
- **Recent fills** — the `trades` view for one strategy, with `limit=20`.
- **Drawdown** — `view="equity"` or `view="stats"` for one strategy; set its
  realized drawdown beside the drawdown its backtest showed.
- **Totals** — account value, realized and unrealized P&L (both, always),
  funding, fees, running and total strategy counts, from the portfolio view's
  fields. Do not invent day / week / MTD returns from the summary; fetch a
  specific view only if asked.

## Step 3 — Flag what needs attention, first

- Errored orders or executions in the last 24 h
  (`view="executions"`, `limit=5`).
- Realized drawdown beyond the backtest's drawdown.
- No rebalance in more than 2× the target interval (stale).
- Net leverage above the user's stated risk preference (ask if unknown).

<!-- profile: full -->

On a local install the stated preference may already be in
`keel://context/user` (`~/.keel/context.md`), where the host browses
resources.

<!-- /profile -->

Errors before metrics — never inside a metrics wall.

## Step 4 — Report the data

Show the data and name what it shows; recommend nothing unprompted. A
question about starting, stopping or changing a strategy is answered with the
evidence; the change itself happens in the Keel web app, and
`keel_app_link` returns the page.

# Decision points

- A running strategy's Sharpe against its backtest Sharpe needs a sample:
  under ~60 trading days it is noise — say so instead of comparing.
- "My main account" means filter to it; do not roll everything up.
- Unrealized is not realized; print both, the difference is real.

# Output shape

1. One-sentence summary of what is running ("4 strategies running on
   1 account; one has an errored order in the last 24 h").
2. Totals from the portfolio view's fields.
3. Per-strategy table: name × realized P&L × age × status.
4. Flagged items, if any.
5. Top-3 positions only if fetched from `positions`; otherwise say not fetched.
6. `hero_url` for the portfolio dashboard.

# When NOT to use

- Starting, stopping or changing a running strategy → the user does that in
  the Keel web app (`keel_app_link`).
- One strategy in depth → `keel_live_monitor(deployment_id,
view="overview")` and its read-only views; `recover-from-error` if the tools
  themselves keep failing.
- A candidate replacement → `backtest-and-analyze`.

# Test prompts

1. "How are my strategies doing?"
2. "Give me a status across everything that's running."
3. "What's running right now, and has anything errored?"
