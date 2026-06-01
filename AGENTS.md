<!-- mcp-name: io.github.keel-trade/keel-trade -->

# Keel Agent SDK

Keel is an agent-native research environment for Hyperliquid systematic
trading. The Python package provides one `keel` CLI and one stdio MCP command.
Both surfaces bind to the same outcome-tool registry.

This file is the package README and agent instruction entry point. Keep it
short, current, and operational. Do not add command inventories unless they are
checked against the outcome registry.

## Install

```bash
pipx install keel-trade
claude mcp add keel -- keel mcp serve
```

`uv tool install keel-trade` also works. Agent hosts launch the MCP command as
a stdio child process:

```bash
keel mcp serve
```

## First Contact

When an agent starts with Keel:

1. Call `keel_status`.
2. If `authenticated` is false, call `keel_auth_login`.
3. Read `workflow_routes` from `keel_status` before choosing lower-level tools.
4. For strategy creation or substantial edits, load the `strategy-creation`
   prompt before composing source.
5. Search components with `keel_components_search`.
6. Fetch exact component schemas with `keel_components_detail_batch`.
7. Compose or validate DSL with `keel_strategy_compose`.
8. Run evidence with `keel_backtest_run`.
9. If the run is still active, watch it with `keel_backtest_watch`.
10. Summarize evidence with `keel_backtest_summarize`.
11. Share only when the user explicitly asks, via `keel_share_create`.

Do not invent Keel commands. If unsure, call `keel_help`, inspect `tools/list`,
or run `keel --help`.

## Authentication

Preferred agent path:

```text
keel_status -> keel_auth_login -> retry original tool
```

`keel_auth_login` opens the OAuth browser flow, captures the loopback redirect,
and stores credentials in `~/.keel/config.yaml`. The CLI uses the same state:

```bash
keel auth login
```

Headless environments can use:

```bash
keel auth login --key <token>
```

Live-trading consent is requested explicitly:

```text
keel_auth_login(scope="live")
```

or:

```bash
keel auth login --scope live
```

## Recommended Strategy Workflow

Use prompts for workflow, resources for context, tools for actions, and URLs for
human handoff.

```text
strategy-creation prompt
keel_components_search(keyword="momentum")
keel_components_detail_batch(names=["ROC", "ForecastScaler", "..."])
keel_strategy_compose(source_file="strategy.py", dry_run=true)
keel_strategy_compose(source_file="strategy.py", name="momentum-baseline")
keel_backtest_run(strategy_id="str_...", start_date="2025-01-01")
keel_backtest_watch(backtest_id="bt_...")
keel_backtest_summarize(backtest_id="bt_...")
```

CLI equivalents:

```bash
keel components search momentum --format json
keel components describe-batch ROC ForecastScaler --format json
keel strategy compose --source-file strategy.py --dry-run --format json
keel strategy compose --source-file strategy.py --name momentum-baseline --format json
keel backtest run str_abc123 --start-date 2025-01-01 --format json
keel backtest watch bt_abc123 --format json
keel backtest summarize bt_abc123 --format json
```

For checked-out local strategy work:

```bash
keel project init
keel strategy checkout str_abc123
keel strategy status str_abc123
keel strategy push str_abc123 -m "describe the change"
keel backtest run str_abc123 --auto-push --start-date 2025-01-01
keel strategy log str_abc123
keel strategy restore str_abc123 --ref 3
```

Backtests run server-side strategy versions. If local edits are ahead of the
server, push first or use `--auto-push` intentionally.
`end_date` / `--end-date` is optional and defaults to today's UTC date.

## Current MCP Tools

MCP tools are callable by canonical `keel_*` names. `KEEL_TOOLSETS` filters the
MCP surface only; the CLI command tree is generated independently.
For the exact generated schema/annotation/CLI mapping, see
`packages/keel-trade/docs/tool-reference.md`.

Always loaded:

- `keel_status`
- `keel_auth_login`
- `keel_doctor`
- `keel_help`

Read-only:

- `keel_accounts_list`
- `keel_audit_list_last`
- `keel_components_search`
- `keel_components_compose_help` (single known component schema/detail)
- `keel_components_detail_batch` (several component schemas before composing)
- `keel_strategy_get`
- `keel_strategy_search`
- `keel_strategy_diff`
- `keel_strategy_log`
- `keel_strategy_memory_read`
- `keel_strategy_workspaces`

Research and backtest:

- `keel_strategy_compose`
- `keel_strategy_checkout`
- `keel_strategy_delete`
- `keel_strategy_discard`
- `keel_strategy_fork`
- `keel_strategy_memory_write`
- `keel_strategy_pull`
- `keel_strategy_push`
- `keel_strategy_restore`
- `keel_strategy_status`
- `keel_backtest_run`
- `keel_backtest_watch`
- `keel_backtest_summarize`

Sharing:

- `keel_share_create`

Live read, loaded by default:

- `keel_live_monitor`

Live write, loaded only when `KEEL_TOOLSETS` includes `live-write`:

- `keel_live_deploy`
- `keel_live_control`

Default MCP toolsets are `always,read-only,backtest,share,live-read`. Opt into
live write tools only when the user is explicitly working on live deployment or
control:

```bash
export KEEL_TOOLSETS=read-only,backtest,share,live-read,live-write
```

`KEEL_TOOLSETS=live` remains a deprecated compatibility alias for
`live-read,live-write`; new configs should use the explicit split.

## MCP Prompts

These prompts are bundled as MCP prompts under the `keel-skill` tag and are also
used as workflow guidance in other Keel agent contexts:

- `strategy-creation`
- `strategy-fork-and-iterate`
- `backtest-and-analyze`
- `overfit-check`
- `deploy-and-monitor`
- `portfolio-review`
- `component-discovery`
- `recover-from-error`

Use `strategy-creation` before a first `keel_strategy_compose` call in a session.
Use `backtest-and-analyze` before interpreting backtest results. Use
`recover-from-error` when a tool repeatedly fails.

## MCP Resources

Use resources for read-only context that should not be recomputed through tool
calls:

- `keel://components/catalog`
- `keel://components/{name}/schema`
- `keel://strategy/{strategy_id}/source`
- `keel://strategy/{strategy_id}/lockfile`
- `keel://backtest/{backtest_id}/results`
- `keel://backtest/latest`
- `keel://strategy/{strategy_id}/backtest/latest`
- `keel://dsl/reference/{topic}`
- `keel://knowledge/{section}`
- `keel://context/user`
- `keel://context/project`
- `keel://context/strategy/{strategy_id}`

## Live Trading Safety

Live trading is opt-in and should be treated as a release gate, not a normal
research action.

Required posture:

1. Do not deploy unless the user explicitly asks.
2. Load `deploy-and-monitor`.
3. Check auth, scopes, accounts, and entitlements with `keel_status` and
   `keel_accounts_list`.
4. Run `keel_live_deploy` with preview enabled first.
5. Show the preview to the user and ask for explicit confirmation.
6. Actual deploy requires the preview `confirmation_token`, live OAuth scope,
   and local account arming.
7. Monitor immediately with `keel_live_monitor`.

`keel_live_monitor` returns a `freshness` block. Read it before summarizing
live data: `view="positions"` is an on-demand Hyperliquid snapshot, while
portfolio/history views come from Keel backend records and can lag the live
dashboard stream.

CLI arming is local-machine authorization:

```bash
keel arm live set --account acct_...
```

Local arming is not a sizing or risk cap. It means this machine is allowed to
perform live actions for that account until expiry.

Preview returns planning information plus a short-lived local
`confirmation_token`. Actual deploy must pass that token with the same
strategy, account, and schedule; host confirmation, live OAuth scope, and local
arming still apply.

## CLI Contract

All outcome commands support:

```bash
--format json|table|tsv|human
```

Agents should prefer `--format json`. Human progress should not be parsed from
stdout; parse stable JSON fields such as `hero_url`, `resource_uri`,
`share_url`, `summary_metrics`, `backtest_id`, and `strategy_id`.

Agent mode is enabled by `KEEL_AGENT_MODE=true`, common agent environment
variables, or non-TTY stdout. `KEEL_AGENT_MODE=false` forces human defaults and
is the script/CI opt-out.

Destructive CLI commands require `--yes` in agent mode. Agents should add it
only after the user has confirmed the action. If omitted, the CLI returns a
structured `cli_confirmation_required` error explaining what to ask the user.

## Current CLI Shape

Outcome-backed CLI commands use the same nouns as MCP tools:

```bash
keel status
keel doctor
keel help <topic>
keel components search <keyword>
keel components compose-help <name>
keel components describe-batch <name1> <name2>
keel strategy search
keel strategy get <strategy_id>
keel strategy compose
keel strategy checkout <strategy_id>
keel strategy status <strategy_id>
keel strategy push <strategy_id> -m "message"
keel strategy pull <strategy_id>
keel strategy log <strategy_id>
keel strategy restore <strategy_id> --ref <ref>
keel backtest run <strategy_id> --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]
keel backtest watch <backtest_id>
keel backtest summarize <backtest_id>
keel share create <target_id> --yes
keel accounts list
keel live deploy <strategy_id> --account-id <account_id>
keel live deploy <strategy_id> --account-id <account_id> \
  --no-preview --confirmation-token <token> --yes
keel live monitor <deployment_id>
keel live control <deployment_id> --action pause|resume|stop|trigger --yes
keel audit list-last
```

CLI-only operational commands include:

```bash
keel auth login
keel auth status
keel auth whoami
keel auth logout
keel mcp serve
keel project init
keel context show --layer user|project
keel arm live set --account <account_id>
keel arm status
keel arm disarm
```

## What Not To Do

- Do not use the removed meta-tool pattern from older docs. Current MCP tools
  are direct `keel_*` tools.
- Do not use old commands that are absent from `keel --help` and the current CLI
  shape above.
- Do not claim backtest results without a `keel_backtest_run` result or
  `keel_backtest_summarize` evidence.
- Do not create public share links unless the user asks.
- Do not live deploy, pause, resume, stop, or trigger without explicit user
  confirmation.
