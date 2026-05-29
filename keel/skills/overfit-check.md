---
name: overfit-check
description: |
  Probe whether a backtest result is robust or curve-fit. Runs the
  strategy across multiple out-of-sample windows, perturbs key
  parameters, and reports stability. Auto-triggers when a single
  backtest shows Sharpe > 3.0 or Total Return > 500%.
trigger: |
  Use when the user asks "is this real?", "is this overfit?", "is this
  robust?", "out-of-sample", "walk-forward", or auto-trigger after any
  single-window backtest produces Sharpe > 3.0. Do NOT use as a substitute
  for the initial single-window backtest (use `backtest-and-analyze` first).
knowledge:
  - reasoning_principles
  - mistakes
  - trading_domain
tools:
  - keel_backtest_run
  - keel_backtest_watch
  - keel_backtest_summarize
---

# Workflow

## Step 1: Confirm the baseline

The user is here because a single backtest looked too good — or they're being disciplined and want to verify. Either way, confirm:

- The `strategy_id` and the baseline `run_id` (or run a baseline if absent).
- The original date range (e.g., 2024-08-15 → 2026-02-27).

Print baseline Sharpe / Return / MaxDD before starting the probe.

## Step 2: Out-of-sample window probe

Run 3-5 backtests on disjoint windows. Default split if user didn't specify:

- **W1 (in-sample):** First 50% of original range
- **W2 (out-of-sample):** Last 50% of original range
- **W3 (held-out tail):** Most recent 3 months
- **W4 (volatility regime A):** A high-vol stretch (e.g., a crash month)
- **W5 (volatility regime B):** A low-vol stretch (e.g., a chop month)

For each: call `keel_backtest_run(strategy_id=<id>, start_date=Wi.start, end_date=Wi.end)`. If a run is still active, call `keel_backtest_watch(backtest_id=<run_id>)` rather than writing your own polling loop. Collect Sharpe, Return, MaxDD.

## Step 3: Parameter perturbation (optional, if user wants depth)

Pick the 1-2 most sensitive parameters (lookback windows, thresholds, leverage cap). Perturb each by ±20% and ±50%. Run a backtest at each perturbation. If Sharpe collapses with small perturbation, the strategy is curve-fit.

This is heavy — only run if the user asked for depth, and warn them about the wall-clock cost (5 windows × 5 perturbations = 25 backtests).

## Step 4: Reason about stability

Three diagnostics:

- **Sharpe spread across windows.** If max(Sharpe) - min(Sharpe) > 2.0 and any window is negative, the strategy is fragile. Real edges degrade smoothly, not catastrophically.
- **Sign of returns.** Negative returns in any disjoint window are a yellow flag. Two or more negative windows are a red flag.
- **Parameter sensitivity.** Real edges are robust to ±20% perturbations. If a 20% lookback shift kills the Sharpe, you found a coincidence.

## Step 5: Verdict

Pick one:

- **ROBUST** — Sharpe stable across windows, returns positive across regimes, params not razor-thin. The original result is probably real.
- **MIXED** — Some windows weak, but the thesis holds in the majority. Worth running forward with smaller size.
- **OVERFIT** — Variance across windows too high, or negative in OOS, or razor-thin params. Do not deploy.

State your verdict in one sentence. Then list which windows / perturbations drove it.

# Common mistakes

- **Treating a single-window OOS as proof** (trading_domain). One disjoint window is necessary but not sufficient. You need regime variety.
- **Cherry-picking the OOS range** to coincide with a known good period. Disjoint != cherry-picked.
- **Ignoring negative returns in any window.** "Average Sharpe across 5 windows is 2.0" hides a -3.0 window.
- **Running perturbations without thinking about which params matter.** A leverage cap perturbation is informative; a `random_seed` perturbation is noise.
- **Forgetting to print the windows.** The user needs to see the date ranges to interpret the spread.

# Expected output shape

1. One-sentence verdict (ROBUST / MIXED / OVERFIT).
2. Table: window × Sharpe × Return × MaxDD × date range.
3. (If run) Parameter sensitivity table.
4. Two-bullet interpretation.
5. Recommendation: "deploy small forward", "iterate further", or "discard".
6. Links to the windows' `hero_url`s (one per row).

# When NOT to use this skill

- **First-pass backtest** → use `backtest-and-analyze`. Don't burn 5 backtests if a single one tells you the strategy is broken.
- **Live deployment** → use `deploy-and-monitor`. A ROBUST verdict is necessary but not sufficient — you still need forward observation.
- **Strategy authoring / editing** → use the creation or fork skills. This skill diagnoses, it doesn't author.

# Test prompts

1. "Is this 3.5 Sharpe real, or am I overfitting?"
2. "Run an out-of-sample check on str_K9p2Lz."
3. "Walk-forward this strategy across 2025."
