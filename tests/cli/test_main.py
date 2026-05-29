"""Smoke tests for the top-level CLI surface in 0.3.0.

The outcome-tool inventory in `keel.tools.outcomes` defines the
canonical surface; both CLI groups (`keel strategy ...`, `keel
backtest ...`, etc.) and MCP tools (`keel_strategy_*`, etc.) bind to
those handlers.
"""

from __future__ import annotations

from click.testing import CliRunner

from keel.cli.main import cli


runner = CliRunner()


# ─── Top-level help ─────────────────────────────────────────────────────


def test_top_level_help_lists_outcome_groups():
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    out = result.output
    # Always-loaded outcomes
    assert "status" in out
    assert "doctor" in out
    assert "help" in out
    # Family groups
    assert "strategy" in out
    assert "backtest" in out
    assert "live" in out
    assert "accounts" in out
    assert "components" in out
    assert "share" in out
    assert "audit" in out
    # CLI-only escape hatches
    assert "auth" in out
    assert "mcp" in out
    assert "universe" in out


def test_top_level_help_shows_global_options():
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--format" in result.output
    assert "--dry-run" in result.output
    assert "--version" in result.output


def test_version():
    from importlib.metadata import version as _pkg_version

    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert _pkg_version("keel-trade") in result.output


# ─── Outcome groups — each shows its outcome commands ────────────────────


def test_strategy_group_has_outcome_subcommands():
    result = runner.invoke(cli, ["strategy", "--help"])
    assert result.exit_code == 0
    for sub in ("compose", "get", "search", "fork", "diff", "delete"):
        assert sub in result.output


def test_backtest_group_has_run_and_summarize():
    result = runner.invoke(cli, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "summarize" in result.output
    assert "watch" in result.output


def test_live_group_has_deploy_monitor_control():
    result = runner.invoke(cli, ["live", "--help"])
    assert result.exit_code == 0
    assert "deploy" in result.output
    assert "monitor" in result.output
    assert "control" in result.output


def test_components_group_has_search_and_compose_help():
    result = runner.invoke(cli, ["components", "--help"])
    assert result.exit_code == 0
    assert "search" in result.output
    assert "compose-help" in result.output


def test_share_group_has_create():
    result = runner.invoke(cli, ["share", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output


def test_audit_group_has_list_last_only():
    result = runner.invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0
    assert "list-last" in result.output
    assert "replay" not in result.output


def test_accounts_group_has_list():
    result = runner.invoke(cli, ["accounts", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output


def test_mcp_serve_is_stdio_only():
    result = runner.invoke(cli, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.output
    assert "--port" not in result.output
    assert "stdio" in result.output


# ─── Format default — agent vs human ─────────────────────────────────────


def test_agent_mode_defaults_to_json(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    # Agent mode means JSON output by default
    assert result.output.strip().startswith("{")


# ─── Unknown subcommand surfaces a clean error ───────────────────────────


def test_unknown_top_level_command():
    result = runner.invoke(cli, ["does-not-exist"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_unknown_subcommand():
    result = runner.invoke(cli, ["strategy", "does-not-exist"])
    assert result.exit_code != 0
    assert "No such command" in result.output
