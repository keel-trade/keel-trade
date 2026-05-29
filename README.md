<p align="center">
  <a href="https://usekeel.io/keel-mcp">
    <img src="https://usekeel.io/og/keel-mcp.png" alt="keel-trade — Build, backtest, and automate Hyperliquid trading strategies with your agent" width="100%">
  </a>
</p>

<h1 align="center">keel-trade</h1>

<p align="center">
  <strong>The Keel CLI and stdio MCP server.</strong><br>
  Build, backtest, and automate <a href="https://hyperliquid.xyz">Hyperliquid</a> trading strategies — with your agent in the loop for <em>creation</em> and a deterministic engine in the loop for <em>execution</em>.
</p>

<p align="center">
  <a href="https://pypi.org/project/keel-trade/"><img src="https://img.shields.io/pypi/v/keel-trade.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="https://usekeel.io/keel-mcp"><img src="https://img.shields.io/badge/product-keel--mcp-635BFF.svg" alt="Product page"></a>
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
- A **deterministic backtest engine** with real Hyperliquid funding + price + slippage, walk-forward, and Monte Carlo
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

## Install

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

For Cursor, Windsurf, and generic MCP clients, see [usekeel.io/keel-mcp#install](https://usekeel.io/keel-mcp#install) or the [agent setup guide](https://usekeel.io/docs/sdk/agent-setup).

## First conversation with your agent

After install, sign in once via the agent (no terminal commands needed):

> **You:** *"Connect to Keel."*
>
> **Agent:** *Calls `keel_auth_login`. Browser opens to app.usekeel.io, you click Allow, tokens land in `~/.keel/config.yaml`. Authenticated for 30 days with transparent refresh.*

Then describe what you want:

> **You:** *"Find me momentum signals for Hyperliquid top-30 perps and compose a backtest from 2024-08-15 to today."*
>
> **Agent:** *Calls `keel_components_search` → `keel_components_detail_batch` → `keel_strategy_compose` → `keel_backtest_run`. Returns a share URL with the full tearsheet (equity curve, Sharpe, max drawdown, per-asset attribution).*

Concrete example: [this share URL](https://app.usekeel.io/share/gDXjURKqWPs8CZ4eXdqAI?ref=H0O2KN) is a funding-carry backtest produced through exactly this flow — Sharpe 2.17 over 2024-08-15 → 2026-04-30 on real Hyperliquid data.

## What the MCP exposes

The default toolset spans status, auth, components, strategy lifecycle, backtest, audit, accounts, sharing, and read-only live monitoring. **Live-write tools** (`keel_live_deploy`, `keel_live_control`) require an explicit opt-in toolset plus a local arming step — agents can't deploy your account without you authorizing it twice.

Full per-tool reference: [usekeel.io/docs/sdk/tool-reference](https://usekeel.io/docs/sdk/tool-reference).

## CLI usage

Every MCP outcome tool has a CLI mirror. Useful for terminals, SSH sessions, CI, scripts, or agents that prefer subprocess calls:

```bash
# Auth + status
keel auth login
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

| Task | Surface |
|---|---|
| **Backtest a Hyperliquid strategy** — real fees, funding, slippage, ~220 perps | [usekeel.io/hyperliquid-backtest](https://usekeel.io/hyperliquid-backtest) |
| **Screen HL perps** — momentum, funding, volume, breakout, regime | [usekeel.io/lab](https://usekeel.io/lab) |
| **Use AI to build strategies** — typed composition, not freeform code | [usekeel.io/ai-trading-strategy-builder](https://usekeel.io/ai-trading-strategy-builder) |
| **Backtest portfolios** across the HL universe | [usekeel.io/crypto-portfolio-backtesting](https://usekeel.io/crypto-portfolio-backtesting) |
| **Robustness diagnostics** — walk-forward, Monte Carlo, deflated Sharpe, PBO | [usekeel.io/hyperliquid](https://usekeel.io/hyperliquid) |
| **Deploy a strategy live** on Hyperliquid (non-custodial) | [usekeel.io/strategy-os](https://usekeel.io/strategy-os) |
| **Compare strategies + venues** | [usekeel.io/compare](https://usekeel.io/compare) |
| **Browse documented trading strategies** | [usekeel.io/strategies](https://usekeel.io/strategies) |

## Documentation

- **Product page**: [usekeel.io/keel-mcp](https://usekeel.io/keel-mcp)
- **Getting started**: [usekeel.io/docs/getting-started](https://usekeel.io/docs/getting-started)
- **Agent setup (per host)**: [usekeel.io/docs/sdk/agent-setup](https://usekeel.io/docs/sdk/agent-setup)
- **CLI reference**: [usekeel.io/docs/sdk/cli-reference](https://usekeel.io/docs/sdk/cli-reference)
- **MCP tool reference**: [usekeel.io/docs/sdk/tool-reference](https://usekeel.io/docs/sdk/tool-reference)
- **REST API reference**: [usekeel.io/docs/api-reference](https://usekeel.io/docs/api-reference)
- **Agent instructions** (canonical, machine-readable): [`AGENTS.md`](AGENTS.md)

## Status

Alpha. The CLI and MCP surface are stable and ship to PyPI on a regular cadence; the underlying engine and component library are actively developed.

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
