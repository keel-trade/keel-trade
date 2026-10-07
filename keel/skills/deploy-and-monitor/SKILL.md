---
name: deploy-and-monitor
description: >
  Take a validated, backtested strategy live on Hyperliquid and read its first
  hours: check readiness blockers, hand the user to the web deploy flow (the
  human picks the account, reviews sizing, accepts risk), observe the handoff
  completing, then read the first monitoring views. Use when the user says
  "deploy", "go live", "start trading this", "put it on my account". Not for
  surveying running deployments (portfolio-review) or for backtesting.
tools:
  - keel_strategy_readiness
  - keel_accounts_list
  - keel_live_deploy
  - keel_live_monitor
references:
  - step: 4
    topic: capability_boundaries
    why: "how execution actually works, bar-close semantics, per-series freshness before making live claims"
  - step: 4
    topic: trading_domain
    why: "leverage is a risk decision: the caps and what to say once"
---

# Method

## Step 1 — Pre-flight

`keel_strategy_readiness(strategy_id=<id>)`: a non-empty `live_readiness_blockers`
(no baseline, no diagnosis, no readiness review) is a stop — name the missing
evidence and hand off (`backtest-and-analyze`, `overfit-check` when the
baseline looked too good). Check the `Execution(...)` matches the path:
continuous strategies go live `buffered` (every-bar rebalancing bleeds to
fees); entry/exit and screen-select strategies are `on_change`. There is no
paper mode; the bounded way to watch a strategy behave is a minimal-capital
deployment scaled up later.

## Step 2 — Accounts

`keel_accounts_list` tells you what is connected, so you can say whether the
user needs to connect a Hyperliquid account first. The wallet needs a first
deposit on Hyperliquid (at least 5 USDC) before Keel can be authorized; after
that, it can be funded whenever the user likes, before or after deploying. The
choice of account is made by the user inside the deploy flow — never pick one
for them.

## Step 3 — Hand off

`keel_live_deploy(strategy_id=<id>)`. It returns `code=handoff_required` with an
`action_url` (the web deploy flow: select or connect the account, review the
server-computed sizing, accept the risk, go live) and a `resume` block. Give
the user the `action_url` in plain prose, outside any tool accordion, and say
what they will decide there. This tool places no orders.

<!-- profile: full -->

`direct=true` is an operator path (inert unless `KEEL_ALLOW_DIRECT_DEPLOY=1`)
and not the normal way to go live; do not reach for it because the handoff
feels slow.

<!-- /profile -->

## Step 4 — Observe the handoff completing

When the user says they have finished (or asks), poll
`keel_live_deploy(intent_token=<resume.token>, preview=true)` →
`handoff_state` `pending` / `completed` / `expired`. `expired` → mint a new
handoff. `completed` → the `deployment_id`.

## Step 5 — First monitoring read

`keel_live_monitor(deployment_id=<id>, view="overview")`, then only the views your
claims need: `positions` (on-demand exchange snapshot), `executions`
(`limit=5`, worker status and errors), `orders` (`limit=20`). Read the
`freshness` block first — backend-recorded views can lag the live dashboard.
Return the deployment `hero_url` so the user can watch live.

# Decision points

- Any hesitation from the user is a risk concern to surface, not a prompt to
  push.
- A high-leverage request: one sentence on what it costs, which cap binds,
  then build — never talk them out of it twice.
- Updating an already-live deployment to a newer version is
  `keel_live_update`, not a second deploy.

# Output shape

Before the handoff: strategy and readiness verdict; `Execution` mode
confirmed; the `action_url` with what the user decides there.

After `completed`: `deployment_id` + `hero_url`; the overview with freshness
called out; positions if fetched; orders/executions if any (say so if none
yet); errors, if any; next step (open the dashboard, or `portfolio-review`
later).

# When NOT to use

- Monitoring an existing deployment → `portfolio-review`.
- Stopping, pausing, or rebalancing a live deployment → `keel_live_control`
  (host-confirmed), not this skill.
- Updating a live deployment's version → `keel_live_update`.
- Curiosity about deployment → answer in prose; fire nothing.

# Test prompts

1. "Deploy str_K9p2Lz to my main HL account."
2. "Go live with the funding carry strategy."
3. "I'm ready to start trading this — push it to Hyperliquid."
