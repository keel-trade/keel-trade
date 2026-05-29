"""Tests for keel.cli.agent_mode."""

import os
from unittest.mock import patch

from keel.cli.agent_mode import (
    _STANDARD_ENV_VARS,
    _TOOL_ENV_VARS,
    default_format,
    detected_agent,
    is_agent_mode,
)


# Helper: environment with all agent env vars cleared
_ALL_VARS = list(_TOOL_ENV_VARS) + list(_STANDARD_ENV_VARS) + ["KEEL_AGENT_MODE"]
_CLEAN_ENV = {k: "" for k in _ALL_VARS}


# ── is_agent_mode() ─────────────────────────────────────────────────────────


def test_agent_mode_claude_code():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CLAUDECODE": "1"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_cursor_agent():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CURSOR_AGENT": "1"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_gemini_cli():
    with patch.dict(os.environ, {**_CLEAN_ENV, "GEMINI_CLI": "1"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_cline():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CLINE_ACTIVE": "true"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_codex():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CODEX_SANDBOX": "seatbelt"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_copilot_cli():
    with patch.dict(os.environ, {**_CLEAN_ENV, "COPILOT_CLI": "1"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_goose():
    with patch.dict(os.environ, {**_CLEAN_ENV, "GOOSE_TERMINAL": "1"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_replit():
    with patch.dict(os.environ, {**_CLEAN_ENV, "REPL_ID": "abc123"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_ai_agent_standard():
    """AI_AGENT cross-tool standard (Vercel detect-agent)."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "AI_AGENT": "cursor"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_agent_standard():
    """AGENT cross-tool standard (agents.md proposal)."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "AGENT": "goose"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_explicit_true():
    with patch.dict(os.environ, {"KEEL_AGENT_MODE": "true"}, clear=False):
        assert is_agent_mode() is True


def test_agent_mode_explicit_false_overrides():
    """KEEL_AGENT_MODE=false overrides even when other agent env vars are set."""
    with patch.dict(os.environ, {"KEEL_AGENT_MODE": "false", "CLAUDECODE": "1"}, clear=False):
        assert is_agent_mode() is False


def test_agent_mode_explicit_false_overrides_all():
    """KEEL_AGENT_MODE=false overrides even non-TTY."""
    env = {**_CLEAN_ENV, "KEEL_AGENT_MODE": "false"}
    with patch.dict(os.environ, env, clear=False):
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert is_agent_mode() is False


def test_agent_mode_not_tty():
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        with patch.dict(os.environ, _CLEAN_ENV, clear=False):
            assert is_agent_mode() is True


def test_agent_mode_tty_no_env_vars():
    """When at a TTY with no agent env vars, should return False."""
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch.dict(os.environ, _CLEAN_ENV, clear=False):
            assert is_agent_mode() is False


# ── detected_agent() ─────────────────────────────────────────────────────────


def test_detected_agent_claude_code():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CLAUDECODE": "1"}, clear=False):
        assert detected_agent() == "claude-code"


def test_detected_agent_ai_agent_standard():
    with patch.dict(os.environ, {**_CLEAN_ENV, "AI_AGENT": "my-custom-agent"}, clear=False):
        assert detected_agent() == "my-custom-agent"


def test_detected_agent_agent_standard():
    with patch.dict(os.environ, {**_CLEAN_ENV, "AGENT": "goose"}, clear=False):
        assert detected_agent() == "goose"


def test_detected_agent_none():
    with patch.dict(os.environ, _CLEAN_ENV, clear=False):
        assert detected_agent() is None


def test_detected_agent_prefers_ai_agent_over_tool():
    """AI_AGENT standard takes priority over tool-specific vars."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "AI_AGENT": "cursor", "CLAUDECODE": "1"}, clear=False):
        assert detected_agent() == "cursor"


# ── default_format() ────────────────────────────────────────────────────────


def test_default_format_agent():
    with patch.dict(os.environ, {**_CLEAN_ENV, "CLAUDECODE": "1"}, clear=False):
        assert default_format() == "json"


def test_default_format_human():
    with patch("keel.cli.agent_mode.is_agent_mode", return_value=False):
        assert default_format() == "human"


# ── Coverage of env var sets ─────────────────────────────────────────────────


def test_tool_env_vars_has_expected():
    assert "CLAUDECODE" in _TOOL_ENV_VARS
    assert "CURSOR_AGENT" in _TOOL_ENV_VARS
    assert "GEMINI_CLI" in _TOOL_ENV_VARS
    assert "CLINE_ACTIVE" in _TOOL_ENV_VARS
    assert "CODEX_SANDBOX" in _TOOL_ENV_VARS
    assert "COPILOT_CLI" in _TOOL_ENV_VARS
    assert "REPL_ID" in _TOOL_ENV_VARS
    assert len(_TOOL_ENV_VARS) >= 10


def test_standard_env_vars_has_expected():
    assert "AI_AGENT" in _STANDARD_ENV_VARS
    assert "AGENT" in _STANDARD_ENV_VARS
