"""Tests for the `keel_auth_logout` MCP-only outcome tool.

The handler delegates to ``keel.auth.clear_credentials`` which wipes
the OAuth fields in ~/.keel/config.yaml. Here we assert:

- The tool is registered in OUTCOMES and is mcp_only.
- The handler calls clear_credentials exactly once.
- The envelope reports logged_out + authenticated=false + a next-hint
  that points at keel_auth_login (so the agent knows how to switch
  accounts without another round-trip).
- The tool is idempotent (safe to call when already logged out).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from keel.tools.outcomes._base import ToolContext

# Import for side-effect registration.
from keel.tools.outcomes import auth_logout as _auth_logout_mod  # noqa: F401
from keel.tools.outcomes import OUTCOMES


@pytest.fixture
def ctx():
    return ToolContext(
        is_tty=False,
        app_url="https://app.usekeel.io",
        share_url_root="https://usekeel.io/share",
    )


def test_keel_auth_logout_is_registered():
    assert "keel_auth_logout" in OUTCOMES
    tool = OUTCOMES["keel_auth_logout"]
    assert tool.mcp_only is True
    assert tool.toolset == "always"
    assert tool.required_action == ""


def test_keel_auth_logout_clears_credentials_and_returns_envelope(ctx):
    with patch("keel.auth.clear_credentials") as mock_clear:
        env = OUTCOMES["keel_auth_logout"].handler({}, ctx).to_envelope()

    mock_clear.assert_called_once_with()
    assert env["logged_out"] is True
    assert env["authenticated"] is False
    assert "next" in env
    assert any("keel_auth_login" in line for line in env["next"])


def test_keel_auth_logout_is_idempotent(ctx):
    """Calling logout twice in a row must not error — clear_credentials
    is itself idempotent (clears whatever's stored, no-op on empty)."""
    with patch("keel.auth.clear_credentials") as mock_clear:
        OUTCOMES["keel_auth_logout"].handler({}, ctx)
        OUTCOMES["keel_auth_logout"].handler({}, ctx)
    assert mock_clear.call_count == 2
