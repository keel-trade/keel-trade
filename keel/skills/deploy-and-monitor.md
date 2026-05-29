---
name: deploy-and-monitor
description: |
  Deploy a validated strategy to live Hyperliquid trading and surface its
  first hours/days of execution. DESTRUCTIVE — moves real capital. The
  workflow requires a backtest baseline, an account choice, and an explicit
  user confirmation before the deploy fires.
trigger: |
  Use when the user says "deploy", "go live", "send to Hyperliquid",
  "start trading this", or names a strategy_id and asks to put it on an
  account. Do NOT use to look at existing deployments (use
  `portfolio-review`) or to backtest (use `backtest-and-analyze`).
knowledge:
  - reasoning_principles
  - mistakes
  - trading_domain
  - tool_usage
tools:
  - keel_accounts_list
  - keel_live_deploy
  - keel_live_monitor
---

# Workflow

## Step 1: Pre-flight — backtest and validation must already be green

Before touching `keel_live_deploy`:

- Confirm the strategy has at least one recent backtest with acceptable metrics. If not, hand off to `backtest-and-analyze` first.
- Confirm the strategy has gone through `overfit-check` if Sharpe was suspiciously high. Real capital amplifies overfit losses.
- Confirm the strategy uses `Execution(rebalance="buffered", buffer_threshold=0.05, min_trade_size=0.01)` or equivalent. Live deployment without buffering bleeds to fees (mistake catalog #9).

If any of these fails, refuse to proceed and explain why.

## Step 2: Choose an account

Call `keel_accounts_list`. Filter for accounts with sufficient equity and matching exchange (Hyperliquid for HL perps). If multiple, ask the user which one. If only one matches, confirm with the user before proceeding — never auto-select.

## Step 3: Stage the deploy (preview, no commit)

Call `keel_live_deploy(strategy_id=<id>, account_id=<account_id>, preview=True)`. This returns:

- The compiled lockfile (versioned components + params)
- The first-bar target weights (what positions will open immediately)
- A risk preview (max gross leverage, max position size, expected turnover)
- A short-lived `confirmation_token` that is bound to this strategy, account, and schedule

Print this preview *outside the tool-use accordion* — in plain prose to the user. They must see it before confirming.

## Step 4: Get explicit confirmation

Ask the user, in plain language:

> "Ready to deploy `<strategy_name>` to `<account_label>`? This will open <N> positions for ~$<estimated notional> at the next bar. Confirm with 'yes' or specify changes."

Do NOT proceed without an unambiguous "yes" (or equivalent). If the user hesitates, surface the risk concern they're implying — don't push.

## Step 5: Fire the deploy

Call `keel_live_deploy(strategy_id=<id>, account_id=<account_id>, preview=False, confirmation_token=<token_from_preview>)`. The token must be the one returned by Step 3 and must match the same strategy, account, and schedule. The MCP host will also surface this as a destructive operation and request its own confirmation — that's expected and not redundant. Belt-and-braces. Actual deploy also requires live OAuth scope and local account arming; if arming fails, ask the user to run `keel arm live set --account <account_id>` on the same machine and then retry after they confirm.

Capture the returned `deployment_id`.

## Step 6: First monitoring snapshot

Call `keel_live_monitor(deployment_id=<id>, view="overview")` immediately
after, then use the targeted read-only views for the claims you make:

- `view="positions"` for current exchange positions and account value.
- `view="executions", limit=5` for recent worker status and errors.
- `view="orders", limit=20` for initial order/fill state.

Read the returned `freshness` block before summarizing. Positions are
on-demand Hyperliquid snapshots; executions, orders, trades, stats, equity,
P&L, and portfolio views are Keel backend records and can lag the live dashboard
stream. Return the `hero_url` for the deployment dashboard so the user can watch
live.

# Common mistakes

- **Deploying without buffered rebalancing** (catalog #9). The first week's fees will eat the edge.
- **Deploying without an account_id** (catalog #5). The API will 422. Always call `keel_accounts_list` first.
- **Auto-selecting an account when multiple match.** Live deploys are not a place to be helpful-by-default — be explicit.
- **Skipping the preview / confirmation.** This is the single most important safety step in the entire SDK. Never silently fire `keel_live_deploy`.
- **Cheering on a successful deploy.** Don't celebrate — show the first monitoring snapshot and let the user judge.

# Expected output shape

**Pre-deploy preview:**
1. Strategy + account being targeted.
2. Compiled component versions (so user sees the lockfile).
3. First-bar target positions (table: asset × target_weight × notional).
4. Risk preview (max leverage, turnover estimate).
5. Plain-language confirmation request.

**Post-deploy (only after confirmation):**
1. `deployment_id` + `hero_url`.
2. Monitoring snapshot with view/source freshness called out.
3. Current positions if fetched from `view="positions"`.
4. Recent orders/executions if available; if none are recorded yet, say so.
5. Any execution errors.
6. Suggested next step: open the dashboard or hand off to `portfolio-review`.

# When NOT to use this skill

- **Monitoring an already-deployed strategy** → use `portfolio-review`. This skill is for the *moment of deploy*.
- **Backtesting** → use `backtest-and-analyze`. Deploy only after backtest + (if needed) overfit-check.
- **Stopping or modifying a live strategy** → out of scope; use the `keel_live_control` tool directly (with the destructive guard).
- **The user is just curious about deployment** → answer their question with prose. Don't fire the workflow unless they say "deploy".

# Test prompts

1. "Deploy str_K9p2Lz to my main HL account."
2. "Go live with the funding carry strategy."
3. "I'm ready to start trading this — push it to Hyperliquid."
