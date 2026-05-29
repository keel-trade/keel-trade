---
name: backtest-and-analyze
description: |
  Run a backtest on an existing strategy and produce a structured analysis:
  return metrics, regime decomposition, key periods, and an authenticated
  URL. Persists a short summary to strategy memory for future sessions.
trigger: |
  Use when the user asks to "run a backtest", "test this strategy",
  "see how it performs", "what's the Sharpe", or names a strategy_id and
  asks anything about performance. Do NOT use for forward-testing,
  live deploys, or robustness checks — those have their own skills.
knowledge:
  - reasoning_principles
  - mistakes
  - trading_domain
  - tool_usage
tools:
  - keel_status
  - keel_strategy_search
  - keel_backtest_run
  - keel_backtest_watch
  - keel_backtest_summarize
  - keel_strategy_status
  - keel_strategy_push
  - keel_strategy_log
  - keel_strategy_memory_read
  - keel_strategy_memory_write
---

# Workflow

## Step 1: Confirm inputs and read prior context

- If the user gave a `strategy_id`, use it. Otherwise ask, or call `keel_strategy_search(limit=5)` and confirm which strategy to test.
- Default date range: 2024-08-15 → today's UTC date unless the user specified a range.
- Call `keel_strategy_memory_read(strategy_id=<id>)` — prior runs and notes may already answer the user's question, or hint at what to focus on.
- **Check entitlements upfront** if this is a parameter sweep, optimization, or anything else that will run >1 backtest. Call `keel_status` and read `entitlements.summary` — the `backtest_runs` unit has weekly remaining quota (free=30, starter=150, trader+pro unlimited). If the user's `remaining` is small relative to what they want, surface that BEFORE kicking off the sweep: *"You have N backtest runs remaining this week on the {plan} plan. Running M iterations will exhaust your quota — upgrade at {entitlements.upgrade_url} or scope the sweep to K runs."* No surprise mid-sweep 403s.

## Step 2: Run the backtest

Call `keel_backtest_run(strategy_id=<id>, start_date=<YYYY-MM-DD>)` unless the user specified an explicit end date; `end_date` is optional and defaults to today's UTC date. The response includes `run_id`, `hero_url`, and usually `summary_metrics`. If the run is still active or metrics are absent, call `keel_backtest_watch(backtest_id=<run_id>)` instead of making an ad hoc polling loop. Once terminal, use `keel_backtest_summarize(backtest_id=<run_id>)` only if you still need the dedicated summary/result URL payload.

If the strategy is checked out and you've been editing locally, `keel_backtest_run` raises `local_ahead` rather than silently testing the OLD server version. Two clean fixes: (a) `keel_strategy_push --message "..."` then re-run, or (b) re-run with `auto_push=True` to push + backtest in one step. Pin a historical version explicitly via `commit_id=...` if that's actually what you want (find via `keel_strategy_log`).

## Step 3: Analyze the result

Surface the metrics that matter:

- **Sharpe**, **Total Return**, **Max Drawdown**, **Calmar**, **Sortino**, **hit rate**
- **Date range** (always print it — comparisons across ranges are meaningless, mistake M-30 catalog #6)
- **Top-3 / bottom-3 contributors** (which assets drove the result)
- **Worst month / best month** (regime sensitivity)

If `Sharpe > 3.0` or `Total Return > 500%` for the period, this is suspicious. Prompt the user to run `overfit-check` before drawing conclusions.

## Step 4: Reason about the result

Three bullets of plain-English interpretation:

- What does the result say about the *thesis*? Did the strategy work as designed, or did it happen to work for a different reason?
- Where did it underperform? Is that consistent with the trading_domain priors (e.g., trend strategies bleed in chop)?
- What's the natural next iteration?

## Step 5: Persist a summary

Call `keel_strategy_memory_write(strategy_id=<id>, note=<one paragraph>)`. Future sessions will see this. Keep it tight — Sharpe, Return, Max DD, date range, one-line interpretation.

End your reply with the `hero_url` on its own line. The user clicks once.

# Common mistakes

- **Comparing Sharpe across different date ranges without saying so** (catalog #6). Always print the range.
- **Running a backtest before the strategy validates** (catalog #4). If the strategy was just edited, the platform may reject it. Validate first.
- **Returning a full weights table.** Use the `hero_url` — the tearsheet renders better than any markdown table.
- **Cheering on a `Sharpe > 5`** without flagging it for overfit. High Sharpe on long ranges in crypto is almost always lookback bias.
- **Ignoring per-asset attribution.** A 3.0 Sharpe driven entirely by one asset is a different beast from a 3.0 Sharpe spread across 30. Always show contributors.
- **Burning through a weekly quota mid-conversation.** Free plan is 30 backtest_runs/wk; if the user is iterating fast or running a sweep, that runs out quickly. Always `keel_status` → check `entitlements.summary` BEFORE kicking off >1 run. On 403 with code `insufficient_entitlements`: read `example.unit` / `example.limit` / `example.current` from the error envelope and tell the user the SPECIFIC unit they hit + direct them to `example.billing_url`. Do NOT retry the call (re-auth doesn't increase quota — that's a billing upgrade flow).

# Expected output shape

1. One-sentence summary ("HRP baseline, Sharpe 3.13, Total Return 717%, MaxDD -12%, 2024-08-15 → 2026-02-27").
2. Metrics table (compact).
3. Top-3 / bottom-3 contributors.
4. Three bullets of "what this means".
5. The `hero_url` on its own line.
6. (Optional) Next-action suggestion (overfit-check if Sharpe > 3, fork-and-iterate if there's an obvious improvement).

# When NOT to use this skill

- **Robustness / out-of-sample checks** → use `overfit-check`. Single-backtest analysis can't tell you if the result is real.
- **Live trading** → use `deploy-and-monitor`. Backtest passing is necessary but not sufficient.
- **Creating or editing the strategy** → use `strategy-creation` or `strategy-fork-and-iterate` first.
- **Comparing two strategies side by side** → run two backtests via this skill, then summarize. There's no dedicated comparison skill (yet).

# Test prompts

1. "Run a backtest on str_K9p2Lz from 2024-08 to today."
2. "What's the Sharpe of my funding carry strategy?"
3. "Test the strategy I just created."
