"""Tests for keel.token_store — OAuth persistence + refresh."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx
import yaml

from keel.config import KeelConfig, load_config, save_config
from keel.errors import AuthError
from keel.token_store import (
    DEFAULT_REFRESH_THRESHOLD_SECONDS,
    attempt_refresh,
    clear_oauth_tokens,
    needs_refresh,
    store_oauth_tokens,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config(tmp_path):
    """Patch config.CONFIG_FILE/DIR to tmp_path and clear env vars."""
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path
    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        yield config_file


# ── store_oauth_tokens ───────────────────────────────────────────────────────


def test_store_oauth_tokens_persists_all_fields(isolated_config):
    before = datetime.now(timezone.utc)
    config = store_oauth_tokens(
        access_token="jwt.access",
        refresh_token="krt_refresh",
        expires_in=3600,
        scope="strategy.read backtest.read",
        client_name="Keel CLI/0.4.0",
    )
    after = datetime.now(timezone.utc)

    assert config.api_key == "jwt.access"
    assert config.refresh_token == "krt_refresh"
    assert config.client_name == "Keel CLI/0.4.0"
    assert config.token_expires_at is not None
    # Expiry should be ~3600s from now, within a small slack window.
    delta = config.token_expires_at - before
    assert timedelta(seconds=3590) <= delta <= timedelta(seconds=3610)
    assert config.token_expires_at >= before + timedelta(seconds=3590)
    assert config.token_expires_at <= after + timedelta(seconds=3610)


def test_store_oauth_tokens_writes_to_disk(isolated_config):
    store_oauth_tokens(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
        client_name="Keel CLI/0.4.0",
    )
    data = yaml.safe_load(isolated_config.read_text())
    assert data["api_key"] == "jwt.X"
    assert data["refresh_token"] == "krt_X"
    assert data["client_name"] == "Keel CLI/0.4.0"
    assert "token_expires_at" in data


def test_store_oauth_tokens_with_api_url_override(isolated_config):
    config = store_oauth_tokens(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
        api_url="https://staging-api.example.com",
    )
    assert config.api_url == "https://staging-api.example.com"


def test_store_oauth_tokens_returns_updated_config(isolated_config):
    config = store_oauth_tokens(
        access_token="jwt.X",
        refresh_token="krt_X",
        expires_in=3600,
    )
    reloaded = load_config()
    assert reloaded.api_key == config.api_key
    assert reloaded.refresh_token == config.refresh_token


# ── clear_oauth_tokens ───────────────────────────────────────────────────────


def test_clear_oauth_tokens_wipes_all_auth_fields(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.X",
        api_url="https://staging-api.example.com",
        refresh_token="krt_X",
        token_expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        client_name="Keel CLI/0.4.0",
    ))
    clear_oauth_tokens()
    config = load_config()
    assert config.api_key is None
    assert config.refresh_token is None
    assert config.token_expires_at is None
    assert config.client_name is None
    # api_url preserved
    assert config.api_url == "https://staging-api.example.com"


def test_clear_oauth_tokens_is_idempotent(isolated_config):
    clear_oauth_tokens()  # nothing to clear
    clear_oauth_tokens()  # still nothing
    config = load_config()
    assert config.api_key is None


# ── needs_refresh ────────────────────────────────────────────────────────────


def test_needs_refresh_false_without_refresh_token():
    config = KeelConfig(
        api_key="jwt.X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # expired
    )
    assert needs_refresh(config) is False


def test_needs_refresh_false_without_expires_at():
    config = KeelConfig(api_key="jwt.X", refresh_token="krt_X")
    assert needs_refresh(config) is False


def test_needs_refresh_true_when_already_expired():
    config = KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    assert needs_refresh(config) is True


def test_needs_refresh_true_within_threshold():
    config = KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    assert needs_refresh(config) is True


def test_needs_refresh_false_outside_threshold():
    config = KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    assert needs_refresh(config) is False


def test_needs_refresh_respects_custom_threshold():
    config = KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert needs_refresh(config, threshold_seconds=60) is False
    assert needs_refresh(config, threshold_seconds=600) is True


def test_needs_refresh_handles_naive_datetime():
    config = KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=30),
    )
    # Should normalize naive to UTC and still detect upcoming expiry.
    assert needs_refresh(config) is True


def test_default_threshold_is_60_seconds():
    assert DEFAULT_REFRESH_THRESHOLD_SECONDS == 60


# ── attempt_refresh ──────────────────────────────────────────────────────────


@respx.mock
def test_attempt_refresh_happy_path(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.old",
        api_url="https://api.usekeel.io",
        refresh_token="krt_old",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        client_name="Keel CLI/0.4.0",
    ))
    respx.post("https://api.usekeel.io/v1/auth/oauth/refresh").mock(
        return_value=httpx.Response(200, json={
            "access_token": "jwt.new",
            "refresh_token": "krt_new",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "strategy.read backtest.read",
        })
    )
    config = attempt_refresh(load_config())
    assert config.api_key == "jwt.new"
    assert config.refresh_token == "krt_new"
    # client_name preserved across refresh
    assert config.client_name == "Keel CLI/0.4.0"
    # On disk too
    persisted = load_config()
    assert persisted.api_key == "jwt.new"


@respx.mock
def test_attempt_refresh_401_clears_oauth_fields(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.old",
        refresh_token="krt_burned",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        client_name="Keel CLI/0.4.0",
    ))
    respx.post("https://api.usekeel.io/v1/auth/oauth/refresh").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid refresh token"})
    )
    with pytest.raises(AuthError) as exc:
        attempt_refresh(load_config())
    assert "expired" in str(exc.value).lower() or "log in" in str(exc.value).lower()
    assert exc.value.suggestion == "keel auth login"
    # Local state wiped
    config = load_config()
    assert config.api_key is None
    assert config.refresh_token is None


@respx.mock
def test_attempt_refresh_500_leaves_config_intact(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    ))
    respx.post("https://api.usekeel.io/v1/auth/oauth/refresh").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    with pytest.raises(AuthError) as exc:
        attempt_refresh(load_config())
    assert exc.value.retryable is True
    # Config still intact
    config = load_config()
    assert config.api_key == "jwt.X"
    assert config.refresh_token == "krt_X"


@respx.mock
def test_attempt_refresh_network_error_leaves_config_intact(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    ))
    respx.post("https://api.usekeel.io/v1/auth/oauth/refresh").mock(
        side_effect=httpx.ConnectError("DNS resolution failed")
    )
    with pytest.raises(AuthError) as exc:
        attempt_refresh(load_config())
    assert exc.value.retryable is True
    config = load_config()
    assert config.api_key == "jwt.X"
    assert config.refresh_token == "krt_X"


def test_attempt_refresh_without_refresh_token_raises(isolated_config):
    save_config(KeelConfig(api_key="jwt.X"))  # legacy PAT user, no refresh
    with pytest.raises(AuthError) as exc:
        attempt_refresh(load_config())
    assert exc.value.suggestion == "keel auth login"


@respx.mock
def test_attempt_refresh_400_clears_oauth_fields(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.X",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    ))
    respx.post("https://api.usekeel.io/v1/auth/oauth/refresh").mock(
        return_value=httpx.Response(400, json={"detail": "malformed request"})
    )
    with pytest.raises(AuthError):
        attempt_refresh(load_config())
    config = load_config()
    assert config.api_key is None


@respx.mock
def test_attempt_refresh_uses_configured_api_url(isolated_config):
    save_config(KeelConfig(
        api_key="jwt.X",
        api_url="https://staging-api.example.com",
        refresh_token="krt_X",
        token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    ))
    route = respx.post(
        "https://staging-api.example.com/v1/auth/oauth/refresh"
    ).mock(
        return_value=httpx.Response(200, json={
            "access_token": "jwt.new",
            "refresh_token": "krt_new",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "strategy.read",
        })
    )
    attempt_refresh(load_config())
    assert route.called
