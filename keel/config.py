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
    # Anonymous claim-later tier (spec 05 R4): set when the stored tokens
    # came from POST /v1/auth/anonymous. `keel auth login` reads it to
    # offer/execute the claim; cleared on claim or logout.
    anon_org_id: str | None = None
    # Spec 09 CL-3c: the anon org's GC instant (grant response
    # `org_expires_at`) + the last time the <48h expiry notice printed.
    anon_org_expires_at: datetime | None = None
    anon_expiry_notice_at: datetime | None = None
    # Spec 09 CL-9: org context for post-claim continuation. When set,
    # KeelClient sends `X-Org-Id` so requests act on the claimed org even
    # though the OAuth tokens were org-bound before the claim. Set only by
    # a successful claim; cleared on logout and at each new login. NOT an
    # org switcher — no user-facing command writes it.
    active_org_id: str | None = None
    # Spec 09 CL-8: deferred existing-account claim material (anon org id,
    # anon refresh token, api_url, counts, org expiry, declined flag).
    # Same file/permission class that held the anon credentials pre-login.
    pending_claim: dict | None = None


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
    """Load config with env var precedence: KEEL_API_KEY > config file.

    Hosted mode (``KEEL_EXECUTION_MODE=hosted``): the ONLY credential
    source is the per-request binding made by the hosting process
    (`keel.hosting.bind_request_credentials`). The config file and env
    vars are never consulted — a hosted request without a binding raises
    an instructive auth error rather than silently acting as the pod.
    """
    import os

    from keel.hosting import current_request_credentials, hosted_auth_error, is_hosted

    bound = current_request_credentials()
    if bound is not None:
        return KeelConfig(api_key=bound.token, api_url=bound.api_url)
    if is_hosted():
        raise hosted_auth_error()

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
            config.anon_org_id = data.get("anon_org_id", config.anon_org_id)
            config.anon_org_expires_at = _parse_expires_at(data.get("anon_org_expires_at"))
            config.anon_expiry_notice_at = _parse_expires_at(data.get("anon_expiry_notice_at"))
            config.active_org_id = data.get("active_org_id", config.active_org_id)
            raw_pending = data.get("pending_claim")
            config.pending_claim = raw_pending if isinstance(raw_pending, dict) else None
        except Exception:  # noqa: BLE001, S110 — corrupt/absent config falls back to defaults
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
    if config.anon_org_id:
        data["anon_org_id"] = config.anon_org_id
    for dt_field in ("anon_org_expires_at", "anon_expiry_notice_at"):
        value = getattr(config, dt_field)
        if value:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            data[dt_field] = value.isoformat()
    if config.active_org_id:
        data["active_org_id"] = config.active_org_id
    if config.pending_claim:
        data["pending_claim"] = config.pending_claim
    CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False))
