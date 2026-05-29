"""Configuration management — ~/.keel/config.yaml + env vars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


CONFIG_DIR = Path.home() / ".keel"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


@dataclass
class KeelConfig:
    api_key: str | None = None
    api_url: str = "https://api.usekeel.io"
    # OAuth flow state — all optional, backwards-compatible with v0.3.x configs.
    # When refresh_token is set, KeelClient performs transparent refresh.
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    client_name: str | None = None


def _parse_expires_at(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def load_config() -> KeelConfig:
    """Load config with env var precedence: KEEL_API_KEY > config file."""
    import os

    config = KeelConfig()

    # Load from file
    if CONFIG_FILE.exists():
        try:
            data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
            config.api_key = data.get("api_key", config.api_key)
            config.api_url = data.get("api_url", config.api_url)
            config.refresh_token = data.get("refresh_token", config.refresh_token)
            config.token_expires_at = _parse_expires_at(data.get("token_expires_at"))
            config.client_name = data.get("client_name", config.client_name)
        except Exception:
            pass  # Corrupt config — use defaults

    # Env vars override
    env_key = os.environ.get("KEEL_API_KEY")
    if env_key:
        config.api_key = env_key
    env_url = os.environ.get("KEEL_API_URL")
    if env_url:
        config.api_url = env_url

    return config


def save_config(config: KeelConfig) -> None:
    """Write config to ~/.keel/config.yaml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config.api_key:
        data["api_key"] = config.api_key
    if config.api_url != "https://api.usekeel.io":
        data["api_url"] = config.api_url
    if config.refresh_token:
        data["refresh_token"] = config.refresh_token
    if config.token_expires_at:
        expires = config.token_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        data["token_expires_at"] = expires.isoformat()
    if config.client_name:
        data["client_name"] = config.client_name
    CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False))
