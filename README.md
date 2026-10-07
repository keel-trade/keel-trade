<p align="center">
  <a href="https://usekeel.io/keel-mcp">
    <img src="https://usekeel.io/og/keel-mcp.png" alt="keel-trade — Build, backtest, and automate Hyperliquid trading strategies with your agent" width="100%">
  </a>
</p>

<h1 align="center">keel-trade</h1>

<p align="center">
  <strong>The Keel CLI and stdio MCP server.</strong><br>
  Build and backtest <a href="https://hyperliquid.xyz">Hyperliquid</a> perpetual-futures strategies with your AI assistant — typed strategy composition and deterministic backtests on real Hyperliquid market history. Running a strategy with real capital happens in the <a href="https://app.usekeel.io">Keel web app</a>.
</p>

<p align="center">
  <a href="https://pypi.org/project/keel-trade/"><img src="https://img.shields.io/pypi/v/keel-trade.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="https://usekeel.io/keel-mcp"><img src="https://img.shields.io/badge/product-keel--mcp-635BFF.svg" alt="Product page"></a>
  <a href="https://glama.ai/mcp/servers/keel-trade/keel-trade"><img src="https://glama.ai/mcp/servers/keel-trade/keel-trade/badges/score.svg" alt="keel-trade MCP server"></a>
</p>

<p align="center">
  <a href="https://usekeel.io">Website</a> ·
  <a href="https://usekeel.io/keel-mcp">Product page</a> ·
  <a href="https://usekeel.io/docs">Docs</a> ·
  <a href="https://app.usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI?ref=H0O2KN">Sample backtest</a> ·
  <a href="https://github.com/keel-trade/keel-trade/discussions">Discussions</a>
</p>

---

## What is Keel?

[Keel](https://usekeel.io) is a quantitative crypto trading platform built around Hyperliquid — strategy development, backtesting, live execution, and portfolio management on the venue with the deepest on-chain perpetual order book. The full platform includes:

- A **web app** for composing strategies, running backtests, and deploying live ([app.usekeel.io](https://app.usekeel.io))
- A **deterministic backtest engine** with real Hyperliquid funding, price and slippage
- **Bit-for-bit live execution** — the same compiled strategy artifact runs in backtest and on Hyperliquid
- A **strategy library** of documented, forkable trading strategies
- A **screener + calculator suite** at [usekeel.io/lab](https://usekeel.io/lab) (funding leaderboard, momentum, overfit-check, walk-forward visualizer, more)
- This package — `keel-trade` — the **agent-native research surface**

This repository is the public mirror of the `keel-trade` Python package: a single `pipx install` gives you both a CLI and a stdio MCP server, so the same tools work from a terminal or from any MCP-capable agent.

## Why agents create strategies, not trade them

Most agent-trading projects put an LLM in the execution loop. That makes systems slow, inconsistent, and hard to audit. Keel does the opposite:

```
You ──── compose ────► Strategy graph ──── compile ────► Deterministic artifact
                              ▲                                    │
                              │                                    ▼
                          Agent edits                          Backtest engine
                          via MCP tools                        (real HL data)
                                                                   │
                                                                   ▼
                                                              Live execution
                                                              (same artifact)
```

Three properties drive the design:

1. **Bit-for-bit parity between backtest and live.** Same compiled artifact, same engine, same data path. There is no second implementation that can drift.
2. **Typed composition over freeform code.** Strategies are graphs of versioned components. Compile errors catch bugs at author time instead of in production.
3. **Agents compose, the deterministic engine executes.** Claude / Cursor / Codex help you build the strategy. They are not in the trade loop.

## Quick start — the hosted MCP, nothing to install

Paste one URL into the client you already use. You sign in through your
client's own OAuth flow; no API key to copy, no local install.

<!-- agent-surface:begin -->
<!-- GENERATED from shared/agent-surface.json + LISTED_PROFILE_TOOLS — edit there, then run
     python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write -->
Hosted endpoint URL: `https://mcp.usekeel.io/mcp` — one endpoint, a 29-tool research/backtest/read surface, nothing to install. Sign in or sign up on the Keel page that opens (no account needed beforehand); authentication is your MCP client's OAuth flow, never a tool call.

- **Claude**: Customize → Connectors → Add custom connector. Paste https://mcp.usekeel.io/mcp and choose Connect.
- **ChatGPT**: Settings → Security and login → turn on Developer mode (a paid ChatGPT plan; on Business/Enterprise an admin must allow it). Open chatgpt.com/plugins, choose +, give the connector a name and a description, and enter https://mcp.usekeel.io/mcp as the MCP server URL (the /mcp path included).
- **Claude Code**: `claude mcp add --transport http keel https://mcp.usekeel.io/mcp`. Inside Claude Code run /mcp and choose Authenticate.
- **Cursor**: Add the server to .cursor/mcp.json (project) or ~/.cursor/mcp.json (global): `{"mcpServers": {"keel": {"url": "https://mcp.usekeel.io/mcp"}}}`. Cursor asks you to sign in on first use.
- **Codex**: `codex mcp add keel --url https://mcp.usekeel.io/mcp` then `codex mcp login keel`. Codex opens your browser for the sign-in.
- **Windsurf**: Add the server to Windsurf's mcp_config.json: `{"mcpServers": {"keel": {"serverUrl": "https://mcp.usekeel.io/mcp"}}}`. Windsurf asks you to sign in on first use.
- **VS Code**: Add the server to .vscode/mcp.json, or open the install link: `{"servers": {"keel": {"type": "http", "url": "https://mcp.usekeel.io/mcp"}}}`. VS Code asks you to sign in on first use.
- **Other**: Use your client's own add remote / HTTP MCP server flow with https://mcp.usekeel.io/mcp. The first request answers 401 with OAuth 2.1 metadata, so a compliant client discovers the sign-in step by itself.

The agent builds, tests and reads. When you want to run a strategy on your account, it hands it to the Keel app and you take it from there.

Setup guide: https://usekeel.io/docs/agents/setup · Per-client runbook: https://usekeel.io/agents
<!-- agent-surface:end -->

**No Keel account yet?** [Create one free](https://app.usekeel.io/sign-up?from=agent-readme) —
then connect the endpoint above and ask your agent to build something.

## Choose your surface

Keel is one product with several places to use it. The hosted MCP above is
the default; this package is the CLI + local MCP path.

<!-- surface-routing:begin -->
<!-- GENERATED from shared/surface-routing.json — edit there, then run
     python packages/keel-trade/keel-sdk/scripts/check_surface_routing.py --write -->
| You are… | Default path (shown first) | Also works |
| --- | --- | --- |
| Using Claude/ChatGPT on web or phone | Hosted endpoint — paste the URL (directory one-click coming) | CLI + local MCP |
| Working in Claude Code / Cursor / terminal | Hosted endpoint — one command, or paste the URL | CLI + local MCP |
| Running a strategy from the Keel app | Keel web app (connect an account, review sizing, start it there) | reads on every surface |
| Building your own agent/scripts | SDK + API key | CLI |
| Just browsing/running strategies | Web app + library | hosted endpoint |
<!-- surface-routing:end -->

- **CLI + local MCP** — this package ([install below](#install)).
- **SDK + API key** — the [REST API](https://usekeel.io/docs/api-reference)
  for building your own agents and scripts.

Per-surface zero-to-first-backtest runbook: [usekeel.io/agents](https://usekeel.io/agents).

## Install

### Claude Desktop — one-click (MCPB)

Download `keel-trade-<version>.mcpb` from the [latest release](https://github.com/keel-trade/keel-trade/releases/latest) and drag onto Claude Desktop. Cross-platform single bundle — works on macOS, Windows, and Linux.

The MCPB bundle requires system Python 3.11+ (same prerequisite as the terminal install path below). First launch takes ~10-30 seconds while the bundle pip-installs runtime deps to `~/.keel/mcpb-lib/py3.X/`; subsequent launches are instant.

The bundle runs this package's local MCP server: your assistant composes and validates strategies, backtests them on real Hyperliquid history, browses the strategy library, compares runs, and shares results. Sign in once with `keel_auth_login`. By default it cannot place orders or move funds — running a strategy with real capital happens in the Keel web app, and the local tools that act on a live account stay off unless you enable them and arm them on your machine ([details below](#what-the-mcp-exposes)). Prefer nothing to install? Claude Desktop also works with the hosted connector in the [quick start](#quick-start--the-hosted-mcp-nothing-to-install).

### Terminal — pipx / uv (Claude Code, Codex, Cursor, Windsurf, etc.)

```bash
pipx install keel-trade
```

`uv tool install keel-trade` also works. Python 3.11+.

Then register the stdio MCP command with your agent host:

```bash
# Claude Code
claude mcp add keel -- keel mcp serve

# Codex
codex mcp add keel -- keel mcp serve
```

For Cursor, Windsurf, and generic MCP clients, see [usekeel.io/keel-mcp#install](https://usekeel.io/keel-mcp#install) or the [agent setup guide](https://usekeel.io/docs/agents/setup).

## First conversation with your agent

After install, sign in once via the agent (no terminal commands needed):

> **You:** _"Connect to Keel."_
>
> **Agent:** _Calls `keel_auth_login`. Browser opens to app.usekeel.io, you click Allow, tokens land in `~/.keel/config.yaml`. Authenticated for 30 days with transparent refresh._

Then describe what you want:

> **You:** _"Find me momentum signals for Hyperliquid top-30 perps and compose a backtest from 2024-08-15 to today."_
>
> **Agent:** _Calls `keel_components_search` → `keel_components_get_many` → `keel_strategy_compose` → `keel_backtest_run`. Returns a share URL with the full tearsheet (equity curve, Sharpe, max drawdown, per-asset attribution)._

Concrete example: [this share URL](https://app.usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI?ref=H0O2KN) is a funding-carry backtest produced through exactly this flow — Sharpe 2.17 over 2024-08-15 → 2026-04-30 on real Hyperliquid data.

## What the MCP exposes

The default local toolsets (`always`, `read-only`, `backtest`, `share`, `live-read`) span status, auth, components, strategy lifecycle, backtest, audit, accounts, sharing, and read-only live monitoring.

**Live-write tools** (`keel_live_deploy`, `keel_live_control`, `keel_live_update`) are off unless you opt in with `KEEL_TOOLSETS`. Even then, going live is a hand-off: `keel_live_deploy` places no orders and returns a link into the Keel web app, where you choose the account, review sizing, and start the strategy. Acting on a live account from your machine (`keel_live_control`, or the operator-only direct deploy) also requires local arming (`keel arm live set`). The hosted connector at `mcp.usekeel.io` has no live-write tools at all.

Full per-tool reference: [usekeel.io/docs/sdk/tool-reference](https://usekeel.io/docs/sdk/tool-reference).

## CLI usage

Every MCP outcome tool has a CLI mirror. Useful for terminals, SSH sessions, CI, scripts, or agents that prefer subprocess calls:

```bash
# Auth + status
keel auth login                      # opens a browser
keel auth login --key "$KEEL_API_KEY"  # CI / SSH / no browser
keel status

# Search components, compose, backtest
keel components search "momentum"
keel strategy compose --source-file my-strategy.py --dry-run
keel backtest run str_abc123 --start-date 2024-08-15 --wait

# Inspect a strategy
keel strategy get str_abc123
keel strategy log str_abc123
```

Full CLI reference: [usekeel.io/docs/sdk/cli-reference](https://usekeel.io/docs/sdk/cli-reference).

## What you can do with Keel

| Task                                                                           | Surface                                                                                    |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| **Backtest a Hyperliquid strategy** — real fees, funding and slippage across the HL perp universe, delisted names included | [usekeel.io/hyperliquid-backtest](https://usekeel.io/hyperliquid-backtest)                 |
| **Screen HL perps** — momentum, funding, volume, breakout, regime              | [usekeel.io/lab](https://usekeel.io/lab)                                                   |
| **Use AI to build strategies** — typed composition, not freeform code          | [usekeel.io/ai-trading-strategy-builder](https://usekeel.io/ai-trading-strategy-builder)   |
| **Backtest portfolios** across the HL universe                                 | [usekeel.io/crypto-portfolio-backtesting](https://usekeel.io/crypto-portfolio-backtesting) |
| **Robustness calculators** — walk-forward visualizer, Monte Carlo, deflated Sharpe, overfit check | [usekeel.io/lab](https://usekeel.io/lab)                                                   |
| **Run a strategy live** from the Keel web app, on your own Hyperliquid account (non-custodial) | [usekeel.io/strategy-os](https://usekeel.io/strategy-os)                                   |
| **Compare strategies + venues**                                                | [usekeel.io/compare](https://usekeel.io/compare)                                           |
| **Browse documented trading strategies**                                       | [usekeel.io/strategies](https://usekeel.io/strategies)                                     |

## Documentation

- **Product page**: [usekeel.io/keel-mcp](https://usekeel.io/keel-mcp)
- **Getting started**: [usekeel.io/docs/getting-started](https://usekeel.io/docs/getting-started)
- **Agent setup (per host)**: [usekeel.io/docs/agents/setup](https://usekeel.io/docs/agents/setup)
- **CLI reference**: [usekeel.io/docs/sdk/cli-reference](https://usekeel.io/docs/sdk/cli-reference)
- **MCP tool reference**: [usekeel.io/docs/sdk/tool-reference](https://usekeel.io/docs/sdk/tool-reference)
- **REST API reference**: [usekeel.io/docs/api-reference](https://usekeel.io/docs/api-reference)
- **Agent instructions** (canonical, machine-readable): [`AGENTS.md`](AGENTS.md)

## Status

Alpha. The CLI and MCP surface are stable and ship to PyPI on a regular cadence; the underlying engine and component library are actively developed.

Keel measures the strategies you design. It is not investment advice, and backtest results do not predict future returns.

## How to contribute / report a bug

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Short version:

- **Bug report** → open an issue using the bug template
- **Feature request, question, or pattern share** → use [Discussions](https://github.com/keel-trade/keel-trade/discussions)
- **Security issue** → email `team@usekeel.io` (do not open a public issue)
- **Patches** → PRs are welcome; we maintain in a private monorepo so PRs may take longer to land — see CONTRIBUTING for the porting process

## Related

- [Keel on Hyperliquid — the platform](https://usekeel.io)
- [What is MCP?](https://usekeel.io/learn/what-is-mcp)
- [AI agents on Hyperliquid — the definitive guide](https://usekeel.io/agent/hyperliquid)
- [Why we don't put LLMs in the trade loop](https://usekeel.io/learn/agentic-trading)

## License

MIT. See [`LICENSE`](LICENSE).

---

<p align="center">
  Built by <a href="https://usekeel.io/about">the Keel Research Team</a> · <a href="https://x.com/usekeelio">@usekeelio</a>
</p>
