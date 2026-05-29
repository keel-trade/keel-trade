---
name: portfolio-review
description: |
  Survey all live deployments across the user's accounts: aggregate P&L,
  per-strategy attribution, current exposures, and anything that's
  drifted or errored. Read-only — never modifies a deployment.
trigger: |
  Use when the user asks "how am I doing", "portfolio status", "across
  my deployments", "live P&L", "what's running right now", or anything
  that surveys multiple deployments. Do NOT use to deploy a strategy
  (`deploy-and-monitor`) or to control a single deployment (out of scope).
knowledge:
  - reasoning_principles
  - trading_domain
tools:
  - keel_live_monitor
  - keel_accounts_list
---

# Workflow

## Step 1: Enumerate deployments

Call `keel_live_monitor(deployment_id="all", view="portfolio")` to get the aggregate view across all of the user's live deployments. If they have multiple accounts, this rolls up across accounts unless the user asked about a single account.

Read the returned `freshness` block before summarizing. The portfolio view is a
Keel backend summary from deployment records, trades, funding, and stored
account snapshots; it can lag the web dashboard live stream. If the user asks
for current exposure or current account value for one deployment, call
`keel_live_monitor(deployment_id=<id>, view="positions")` for an on-demand
Hyperliquid snapshot.

If the portfolio view isn't available on the current SDK version, fall back to `keel_accounts_list` + per-deployment `keel_live_monitor(deployment_id=<id>)` and aggregate client-side.

## Step 2: Reason about the picture

Three views the user usually wants:

- **Aggregate.** Total account value, realized P&L, unrealized P&L, funding, fees, active deployment count, and total deployment count. Do not invent day/week/MTD returns from the portfolio summary; fetch more specific live views only if the user asks.
- **Per-strategy attribution.** Which deployments contributed most/least? A single laggard can mask a strong portfolio.
- **Exposure right now.** For a single deployment, use `view="positions"` for current exchange positions. For multiple deployments, say when you only have the stored portfolio summary instead of per-account live-service stream data.

## Step 3: Flag anything anomalous

- A deployment with errored orders in the last 24h.
- A deployment with realized drawdown beyond its backtest-implied 95th percentile MaxDD.
- A deployment that hasn't rebalanced in > 2× its target rebalance interval (stuck).
- Net leverage > the user's stated risk preference (from `keel://context/user`).

Surface these explicitly — don't bury them in a metrics table.

## Step 4: Don't make decisions for the user

This skill is *survey only*. Even if a deployment looks bad, do NOT recommend stopping it without the user asking. Show the data, identify the signal, let them decide. If they ask "should I stop it?", that's a separate conversation that may route into `recover-from-error` or direct CLI use of `keel_live_control`.

# Common mistakes

- **Comparing live Sharpe to backtest Sharpe over a < 1-month sample.** Sharpe at low sample size is noise. Wait for at least 60 trading days before drawing conclusions (trading_domain).
- **Aggregating across accounts when the user asked about one.** If they said "my main account", filter — don't roll everything up.
- **Hiding errored deployments in a wall of metrics.** Surface errors first, metrics second.
- **Recommending action.** This skill reports. The user decides.
- **Treating unrealized P&L as realized.** Always print both — the difference is real and visible.

# Expected output shape

1. One-sentence headline ("Portfolio +12.3% since first deploy, 4 active deployments across 1 account").
2. Aggregate metrics table with fields returned by the portfolio view.
3. Per-deployment table (strategy_name x realized P&L x age x status).
4. Flagged anomalies (if any).
5. Top-3 current positions only if fetched from `view="positions"`; otherwise say they were not fetched.
6. `hero_url` for the portfolio dashboard.

# When NOT to use this skill

- **Deploying a new strategy** → use `deploy-and-monitor`.
- **Stopping or modifying a deployment** → out of scope. Direct CLI:
  `keel live control <deployment_id> --action stop --yes` only after explicit
  user confirmation.
- **Investigating a single failing deployment in depth** → use `recover-from-error` if the user is stuck, or inspect it with `keel_live_monitor(deployment_id=<id>, view="overview")` and the relevant read-only views.
- **Backtesting a candidate replacement** → use `backtest-and-analyze`.

# Test prompts

1. "How's my portfolio doing?"
2. "Give me a status across all my live deployments."
3. "What's running right now and how much am I up?"
