"""Tests for keel.auth — the auth orchestrators (api-key + browser flow)."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
import respx

from keel.auth import (
    _default_client_name,
    browser_login,
    clear_credentials,
    store_api_key,
    validate_api_key,
)
from keel.browser_login import BrowserLoginResult
from keel.config import KeelConfig, load_config, save_config


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path
    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        yield config_file


# ── _default_client_name ─────────────────────────────────────────────────────


def test_default_client_name_includes_version():
    # Either "Keel CLI/<version>" (when installed) or "Keel CLI" (fallback).
    name = _default_client_name()
    assert name.startswith("Keel CLI")


# ── validate_api_key (existing — regression) ────────────────────────────────


@respx.mock
def test_validate_api_key_calls_me():
    respx.get("https://api.usekeel.io/v1/me").mock(
        return_value=httpx.Response(200, json={"principal_id": "p_X", "org_id": "o_Y"})
    )
    info = validate_api_key("sk_test_X")
    assert info["principal_id"] == "p_X"


# ── store_api_key (existing — regression with new schema) ───────────────────


@respx.mock
def test_store_api_key_writes_config(isolated_config):
    respx.get("https://api.usekeel.io/v1/me").mock(
        return_value=httpx.Response(200, json={"principal_id": "p_X", "org_id": "o_Y"})
    )
    info = store_api_key("sk_test_X")
    config = load_config()
    assert config.api_key == "sk_test_X"
    assert config.refresh_token is None  # legacy PAT path — no refresh state
    assert info["principal_id"] == "p_X"


# ── clear_credentials ────────────────────────────────────────────────────────


def test_clear_credentials_wipes_all_auth_fields(isolated_config):
    from datetime import datetime, timezone

    save_config(KeelConfig(
        api_key="jwt.X",
        api_url="https://staging.example.com",
        refresh_token="krt_X",
        token_expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        client_name="Keel CLI/0.4.0",
    ))
    clear_credentials()
    config = load_config()
    assert config.api_key is None
    assert config.refresh_token is None
    assert config.token_expires_at is None
    assert config.client_name is None
    # api_url preserved
    assert config.api_url == "https://staging.example.com"


# ── browser_login orchestrator ───────────────────────────────────────────────


@respx.mock
def test_browser_login_persists_tokens_and_returns_identity(isolated_config):
    """The orchestrator runs the flow, stores tokens, fetches /v1/me."""
    respx.get("https://api.usekeel.io/v1/me").mock(
        return_value=httpx.Response(200, json={
            "principal_id": "p_X", "org_id": "o_Y", "scopes": ["base"],
        })
    )

    fake_result = BrowserLoginResult(
        access_token="jwt.fresh",
        refresh_token="krt_fresh",
        expires_in=3600,
        scope="strategy.read backtest.read",
        token_type="Bearer",
        api_url="https://api.usekeel.io",
    )

    with patch("keel.browser_login.run", return_value=fake_result) as mock_run:
        info = browser_login(
            api_url="https://api.usekeel.io",
            include_live=False,
            client_name="Keel CLI/0.4.0",
        )

    # 1. browser_login.run was called with our params
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["api_url"] == "https://api.usekeel.io"
    assert kwargs["include_live"] is False
    assert kwargs["client_name"] == "Keel CLI/0.4.0"

    # 2. Tokens persisted
    config = load_config()
    assert config.api_key == "jwt.fresh"
    assert config.refresh_token == "krt_fresh"
    assert config.client_name == "Keel CLI/0.4.0"

    # 3. Identity returned from GET /v1/me
    assert info["principal_id"] == "p_X"


@respx.mock
def test_browser_login_uses_default_client_name_when_omitted(isolated_config):
    respx.get("https://api.usekeel.io/v1/me").mock(
        return_value=httpx.Response(200, json={"principal_id": "p_X"})
    )
    fake_result = BrowserLoginResult(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
        scope="strategy.read",
        token_type="Bearer",
        api_url="https://api.usekeel.io",
    )
    with patch("keel.browser_login.run", return_value=fake_result) as mock_run:
        browser_login(api_url="https://api.usekeel.io")
    kwargs = mock_run.call_args.kwargs
    assert kwargs["client_name"].startswith("Keel CLI")


@respx.mock
def test_browser_login_falls_back_to_config_api_url(isolated_config):
    respx.get("https://staging-api.example.com/v1/me").mock(
        return_value=httpx.Response(200, json={"principal_id": "p_X"})
    )
    # Pre-seed config with a staging URL — browser_login should use it.
    save_config(KeelConfig(api_url="https://staging-api.example.com"))
    fake_result = BrowserLoginResult(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
        scope="strategy.read",
        token_type="Bearer",
        api_url="https://staging-api.example.com",
    )
    with patch("keel.browser_login.run", return_value=fake_result) as mock_run:
        browser_login()  # no api_url arg
    kwargs = mock_run.call_args.kwargs
    assert kwargs["api_url"] == "https://staging-api.example.com"


@respx.mock
def test_browser_login_include_live_propagates(isolated_config):
    respx.get("https://api.usekeel.io/v1/me").mock(
        return_value=httpx.Response(200, json={"principal_id": "p_X"})
    )
    fake_result = BrowserLoginResult(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
        scope="strategy.read runner.*",
        token_type="Bearer",
        api_url="https://api.usekeel.io",
    )
    with patch("keel.browser_login.run", return_value=fake_result) as mock_run:
        browser_login(api_url="https://api.usekeel.io", include_live=True)
    assert mock_run.call_args.kwargs["include_live"] is True


def test_browser_login_propagates_auth_errors(isolated_config):
    """A failed loopback dance surfaces as AuthError — no token persistence."""
    from keel.errors import AuthError

    save_config(KeelConfig(api_url="https://api.usekeel.io"))

    with patch(
        "keel.browser_login.run",
        side_effect=AuthError("Login timed out."),
    ):
        with pytest.raises(AuthError):
            browser_login(api_url="https://api.usekeel.io")

    # Tokens NOT persisted
    config = load_config()
    assert config.api_key is None
    assert config.refresh_token is None
