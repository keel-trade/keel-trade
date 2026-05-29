"""Tests for keel.cli.commands.auth — the `keel auth` Click group."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from keel.cli.main import cli
from keel.errors import AuthError


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path):
    """Patch config + clear env, defaulting to NON-agent mode.

    `CliRunner` captures stdout so the agent-mode fallback (``not
    sys.stdout.isatty()``) would otherwise force agent-mode on. The
    ``KEEL_AGENT_MODE=false`` override pins us to the interactive path.
    Tests that want agent mode override this env var explicitly.
    """
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path
    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {"KEEL_AGENT_MODE": "false"}, clear=True):
        yield config_file


# ── --key path (existing behavior — regression) ─────────────────────────────


# Fixture matching the production /v1/me response shape — nested
# `principal` and `org` dicts plus `credential_scopes` array. Earlier
# tests used a flat shape that drifted from the actual API.
_ME_FIXTURE = {
    "principal": {"id": "prn_X", "type": "user", "display_name": "Test User"},
    "org": {"id": "org_Y", "name": "Test Org", "plan": "trader"},
    "credential_scopes": ["strategy.read", "backtest.read"],
}


def test_login_with_key_flag(runner, isolated_env):
    with patch("keel.auth.store_api_key", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, ["auth", "login", "--key", "sk_abc123"])
    assert result.exit_code == 0
    mock.assert_called_once_with("sk_abc123", api_url=None)
    assert "prn_X" in result.output
    assert "trader" in result.output
    # Concise summary, NOT the full /v1/me dump.
    assert "credential_scopes" not in result.output
    assert "next" in result.output


def test_login_with_key_and_api_url(runner, isolated_env):
    with patch("keel.auth.store_api_key", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, [
            "auth", "login",
            "--key", "sk_abc",
            "--api-url", "https://staging-api.example.com",
        ])
    assert result.exit_code == 0
    mock.assert_called_once_with("sk_abc", api_url="https://staging-api.example.com")


def test_login_with_invalid_key_surfaces_auth_error(runner, isolated_env):
    with patch(
        "keel.auth.store_api_key",
        side_effect=AuthError("Authentication required"),
    ):
        result = runner.invoke(cli, ["auth", "login", "--key", "bad"])
    assert result.exit_code == 4  # AuthError exit code


# ── Browser flow (new default) ──────────────────────────────────────────────


def test_login_no_flags_invokes_browser_flow(runner, isolated_env):
    with patch("keel.auth.browser_login", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, ["auth", "login"])
    assert result.exit_code == 0
    mock.assert_called_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["include_live"] is False
    assert kwargs["api_url"] is None
    # Concise login summary surfaces principal_id, plan, tier, next hint
    assert "prn_X" in result.output
    assert "trader" in result.output
    assert "base" in result.output  # tier (no runner.* in fixture)


def test_login_summary_marks_live_tier(runner, isolated_env):
    fixture_with_live = {
        **_ME_FIXTURE,
        "credential_scopes": ["strategy.read", "runner.*"],
    }
    with patch("keel.auth.browser_login", return_value=fixture_with_live):
        result = runner.invoke(cli, ["auth", "login", "--scope", "live"])
    assert result.exit_code == 0
    assert "live" in result.output  # tier reflects runner.* presence


def test_login_scope_live_propagates(runner, isolated_env):
    with patch("keel.auth.browser_login", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, ["auth", "login", "--scope", "live"])
    assert result.exit_code == 0
    assert mock.call_args.kwargs["include_live"] is True


def test_login_browser_flow_with_api_url(runner, isolated_env):
    with patch("keel.auth.browser_login", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, [
            "auth", "login",
            "--api-url", "https://staging-api.example.com",
        ])
    assert result.exit_code == 0
    assert mock.call_args.kwargs["api_url"] == "https://staging-api.example.com"


def test_login_browser_flow_timeout_exits_with_auth_error(runner, isolated_env):
    with patch(
        "keel.auth.browser_login",
        side_effect=AuthError(
            "Login timed out.",
            suggestion="keel auth login --key <token>",
        ),
    ):
        result = runner.invoke(cli, ["auth", "login"])
    assert result.exit_code == 4


def test_login_invalid_scope_rejected_at_click_level(runner, isolated_env):
    result = runner.invoke(cli, ["auth", "login", "--scope", "admin"])
    assert result.exit_code != 0
    assert "admin" in result.output.lower() or "invalid" in result.output.lower()


# ── Agent mode (stdin paste — regression) ────────────────────────────────────


def test_login_agent_mode_reads_stdin(runner, isolated_env):
    """In agent mode, no --key, stdin has a key → PAT path used."""
    with patch.dict(os.environ, {"KEEL_AGENT_MODE": "true"}, clear=False), \
         patch("keel.auth.store_api_key", return_value=_ME_FIXTURE) as mock, \
         patch("keel.auth.browser_login") as browser_mock:
        result = runner.invoke(cli, ["auth", "login"], input="sk_from_stdin\n")
    assert result.exit_code == 0
    mock.assert_called_once_with("sk_from_stdin", api_url=None)
    # Browser flow must NOT have fired in agent mode.
    browser_mock.assert_not_called()


def test_login_agent_mode_empty_stdin_exits_usage_error(runner, isolated_env):
    """Agent mode + no --key + empty stdin → usage_error, no browser."""
    with patch.dict(os.environ, {"KEEL_AGENT_MODE": "true"}, clear=False), \
         patch("keel.auth.browser_login") as browser_mock:
        result = runner.invoke(cli, ["auth", "login"], input="")
    assert result.exit_code == 2  # usage_error
    browser_mock.assert_not_called()


def test_login_agent_mode_with_explicit_key_bypasses_stdin(runner, isolated_env):
    with patch.dict(os.environ, {"KEEL_AGENT_MODE": "true"}, clear=False), \
         patch("keel.auth.store_api_key", return_value=_ME_FIXTURE) as mock:
        result = runner.invoke(cli, ["auth", "login", "--key", "sk_explicit"])
    assert result.exit_code == 0
    mock.assert_called_once_with("sk_explicit", api_url=None)


# ── Logout (clears OAuth state too) ──────────────────────────────────────────


def test_logout_clears_oauth_state(runner, isolated_env):
    from datetime import datetime, timezone

    from keel.config import KeelConfig, load_config, save_config

    # Pre-seed an OAuth session in the isolated config.
    save_config(KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        client_name="Keel CLI/0.4.0",
    ))
    result = runner.invoke(cli, ["auth", "logout"])
    assert result.exit_code == 0
    config = load_config()
    assert config.api_key is None
    assert config.refresh_token is None
    assert config.token_expires_at is None
    assert config.client_name is None
