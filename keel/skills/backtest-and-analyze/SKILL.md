---
name: backtest-and-analyze
description: >
  Run a backtest on an existing strategy and read it: confirm the strategy, run
  on the strategy's default window unless the user named one, watch to
  terminal, present the run's view, and interpret the mechanism behind the
  result. Use when the user says "run a backtest", "test this", "how does it
  perform", "what's the Sharpe", or names a strategy and asks about
  performance. Not for robustness checks (overfit-check) or for editing the
  strategy.
tools:
  - keel_strategy_search
  - keel_strategy_notes_read
  - keel_backtest_run
  - keel_backtest_watch
  - keel_backtest_summarize
  - keel_backtest_compare
  - keel_strategy_history
  - keel_strategy_notes_add
  - keel_strategy_status
  - keel_strategy_push
references:
  - step: 2
    topic: tool_usage
    why: "backtest mechanics: one continuous simulation, half-open window, warm-up; never N short runs as a substitute for post-processing"
  - step: 2
    topic: platform_operations
    why: "the backtest window rule (the default window per timeframe, warm-up, the data floor) and per-asset data coverage"
  - step: 2
    topic: backtest_costs
    why: "backtest cost assumptions: the default fee and slippage model every run applies"
  - step: 4
    topic: trading_domain
    why: "timeframe facts (too few trades on a short bar), trend vs mean-reversion behaviour, the complexity ladder"
  - step: 4
    topic: mistakes
    why: "structural mistakes behind a zero-trade or wrong-direction result"
---

# Method

## Step 1 — Confirm inputs and read prior context

- A `strategy_id` from the user, else `keel_strategy_search(limit=5)` and
  confirm which one.
- `keel_strategy_notes_read(strategy_id=<id>)` — prior runs and notes may already
  answer the question or say what to look at.
- Window: omit `start_date` / `end_date` unless the user named a period; the
  tool applies the strategy clock's default window and the receipt names the
  window it used.
- More than one run planned (a grid, several windows)? Each run uses one
  backtest of the plan's weekly allowance; say how many runs the batch needs
  before starting, and let the user scope it.

## Step 2 — Run and wait

`keel_backtest_run(strategy_id=<id>, ...)`. The receipt carries `run_id`,
`hero_url`, and usually `summary_metrics`. If the run is still active, or
metrics are absent, `keel_backtest_watch(backtest_id=<run_id>)` — not an ad hoc
polling loop. Once terminal, show the run with `keel_backtest_summarize` if it
is the run you present; for several runs, `keel_backtest_compare` once with
every id. Backtests run the platform's current version; pin a historical one
with `commit_id=...` (from `keel_strategy_history`) only when that is what the user
wants. A run may not finish in the turn: say it started and how to read it;
never promise to report back on your own.

<!-- profile: full -->

A checked-out strategy with local edits makes `keel_backtest_run` refuse with
`local_ahead` rather than silently test the old server version: push first, or
re-run with `auto_push=True`.

<!-- /profile -->

## Step 3 — Read the result

The `view` is the metrics table — where the host draws it the user already
sees it; elsewhere its markdown stands in. Then, in prose: the date range
(always — comparisons across ranges are meaningless), the top-3 / bottom-3
contributors (a Sharpe carried by one asset is a different result from one
spread across thirty), best and worst month. `Sharpe > 3.0` or `Total Return >
500%` on a long window is unusual for a real edge; say so plainly —
`overfit-check` is the robustness check for a result like that.

## Step 4 — Reason about it

Three bullets: did the thesis work as designed or for another reason; where
did it underperform and is that consistent with the strategy type (trend
bleeds in chop); what would a principled next iteration change — a mechanism
fix with a reason independent of the backtest, never a change designed to
dodge one bad period. Diagnose the mechanism, not the outcome: "mean-reversion
shorts in a parabolic breakout with no trend filter" is a mechanism; "shorts
lost money in Nov 2024" is an outcome. Report the result first, then the
caveats.

## Step 5 — Save a note when asked

When the user asks to save or remember the result,
`keel_strategy_notes_add(strategy_id=<id>, note=<one paragraph>)`: Sharpe,
return, max DD, window, one-line interpretation. End the reply with the
`hero_url` on its own line.

# Decision points

- One run → summarize; several → compare once. Never both for the same run.
- Plan-limit refusals (`quota_exhausted`, `quota_cap_reached`,
  `plan_feature_unavailable`) carry the numbers: report the limit and the
  reset. The call is not retried; re-authenticating adds no capacity.
- Simulation framing (daily reset, withdrawals, "add $X a week") is not a
  reason for N short backtests: run one over the full window and decompose
  from trades and equity.

# Output shape

1. One sentence: strategy, Sharpe, return, max DD, window.
2. The run's `view` — explained, not repeated, where the host draws it.
3. Top-3 / bottom-3 contributors.
4. Three bullets of "what this means".
5. `hero_url` on its own line.
6. Where a follow-up is natural, one line naming it — a robustness check
   (`overfit-check`) for an unusually strong result, a mechanism fix
   (`strategy-fork-and-iterate`) for a clear weakness — for the user to pick.

# When NOT to use

- Robustness / out-of-sample → `overfit-check`.
- Comparing runs → `keel_backtest_compare`.
- Creating or editing the strategy first → `strategy-creation` /
  `strategy-fork-and-iterate`.

# Test prompts

1. "Run a backtest on str_K9p2Lz from <start> to today."
2. "What's the Sharpe of my funding carry strategy?"
3. "Test the strategy I just created."
