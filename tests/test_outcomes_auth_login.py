"""Tests for the `keel_auth_login` MCP-only outcome tool.

The handler delegates to ``keel.auth.browser_login`` which does the
network + filesystem work. Here we mock that function and assert:

- The tool is registered in OUTCOMES.
- The tool is mcp_only (no duplicate CLI command).
- The handler passes scope/api_url args through correctly.
- The summary envelope matches the v0.4.1 CLI shape (authenticated,
  principal_id, org_id, plan, tier, next).
- A `runner.*` credential scope flips `tier` from base → live.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from keel.tools.outcomes._base import ToolContext

# Import for side-effect registration.
from keel.tools.outcomes import auth_login as _auth_login_mod  # noqa: F401
from keel.tools.outcomes import OUTCOMES


@pytest.fixture
def ctx():
    return ToolContext(
        is_tty=False,
        app_url="https://app.usekeel.io",
        share_url_root="https://usekeel.io/share",
    )


def _fake_me(scopes=None):
    return {
        "principal": {"id": "prin_abc123"},
        "org": {"id": "org_xyz789", "name": "Test Org", "plan": "trader"},
        "credential_scopes": list(scopes or []),
    }


def test_auth_login_registered():
    assert "keel_auth_login" in OUTCOMES
    tool = OUTCOMES["keel_auth_login"]
    assert tool.toolset == "always"
    assert tool.mcp_only is True
    # No required_action — agents can call this when unauthenticated.
    assert tool.required_action == ""


def test_auth_login_default_scope_returns_base_tier(ctx):
    with patch("keel.auth.browser_login", return_value=_fake_me()) as mock_login:
        tool = OUTCOMES["keel_auth_login"]
        result = tool.handler({}, ctx)

    mock_login.assert_called_once_with(
        api_url=None,
        include_live=False,
        auth_surface="mcp",
    )
    env = result.to_envelope()
    assert env["authenticated"] is True
    assert env["principal_id"] == "prin_abc123"
    assert env["org_id"] == "org_xyz789"
    assert env["org_name"] == "Test Org"
    assert env["plan"] == "trader"
    assert env["tier"] == "base"
    assert env["hero_url"] == "https://app.usekeel.io/settings"
    assert env["share_url"] is None
    # next-hint should drop the user into the discovery flow.
    assert any("keel_status" in line for line in env["next"])
    assert any("prompts/list" in line for line in env["next"])
    assert any("keel_components_search" in line for line in env["next"])
    assert any("keel_components_detail_batch" in line for line in env["next"])


def test_auth_login_with_live_scope_marks_tier_live(ctx):
    with patch(
        "keel.auth.browser_login",
        return_value=_fake_me(scopes=["runner.*"]),
    ) as mock_login:
        tool = OUTCOMES["keel_auth_login"]
        result = tool.handler({"scope": "live"}, ctx)

    mock_login.assert_called_once_with(
        api_url=None,
        include_live=True,
        auth_surface="mcp",
    )
    env = result.to_envelope()
    assert env["tier"] == "live"


def test_auth_login_passes_api_url_through(ctx):
    """Staging users override the default api_url; arg must reach browser_login."""
    with patch("keel.auth.browser_login", return_value=_fake_me()) as mock_login:
        tool = OUTCOMES["keel_auth_login"]
        tool.handler(
            {"api_url": "https://staging-api.example.com"}, ctx
        )

    mock_login.assert_called_once_with(
        api_url="https://staging-api.example.com",
        include_live=False,
        auth_surface="mcp",
    )


def test_auth_login_skipped_by_cli_adapter():
    """mcp_only tools must not register a CLI command — the existing
    hand-rolled `keel auth login` owns the CLI surface."""
    from click.testing import CliRunner
    from keel.cli.main import cli

    runner = CliRunner()
    # `keel auth-login` should NOT exist; only the hand-rolled
    # `keel auth login` subgroup command.
    result = runner.invoke(cli, ["auth-login", "--help"])
    assert result.exit_code != 0
    # And the existing `keel auth` group still has login as a subcommand.
    result = runner.invoke(cli, ["auth", "login", "--help"])
    assert result.exit_code == 0
    assert "browser" in result.output.lower() or "oauth" in result.output.lower()
