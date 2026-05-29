"""OAuth token persistence + refresh helpers.

The CLI loopback flow (`keel/browser_login.py`) ends by calling
`store_oauth_tokens()`. `KeelClient` (`keel/client.py`) calls
`needs_refresh()` proactively and `attempt_refresh()` reactively on 401.
Refresh hits ``POST /v1/auth/oauth/refresh`` on keel-api, which already
implements OAuth 2.1 §6.1 rotation with lineage-burn reuse detection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from keel.config import KeelConfig, load_config, save_config
from keel.errors import AuthError


# Refresh when the access token has this many seconds (or fewer) of life left.
# Picked to be small enough that almost every call uses a fresh token, but
# large enough that a single in-flight refresh covers any retried request.
DEFAULT_REFRESH_THRESHOLD_SECONDS = 60


def store_oauth_tokens(
    *,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scope: str | None = None,
    client_name: str | None = None,
    api_url: str | None = None,
) -> KeelConfig:
    """Persist tokens from a /v1/auth/oauth/token (or /refresh) response.

    Writes to ``~/.keel/config.yaml`` via save_config. The OAuth access JWT
    is stored in ``api_key`` so KeelClient's existing ``Authorization:
    Bearer`` header path Just Works without branching.

    Returns the updated KeelConfig (also persisted).
    """
    config = load_config()
    config.api_key = access_token
    config.refresh_token = refresh_token
    config.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    if client_name:
        config.client_name = client_name
    if api_url:
        config.api_url = api_url
    save_config(config)
    return config


def clear_oauth_tokens() -> None:
    """Clear all auth fields. Idempotent.

    Wipes ``api_key`` (whether OAuth JWT or legacy long-lived PAT) plus the
    OAuth-specific ``refresh_token``, ``token_expires_at``, ``client_name``.
    Leaves ``api_url`` intact (the user's chosen environment).
    """
    config = load_config()
    config.api_key = None
    config.refresh_token = None
    config.token_expires_at = None
    config.client_name = None
    save_config(config)


def needs_refresh(
    config: KeelConfig,
    *,
    threshold_seconds: int = DEFAULT_REFRESH_THRESHOLD_SECONDS,
) -> bool:
    """True when an OAuth-issued access token is within ``threshold_seconds``
    of expiry (or already expired).

    Returns False if either ``refresh_token`` is None (legacy PAT user — no
    refresh capability) or ``token_expires_at`` is None (we don't know
    when it expires, can't safely pre-refresh).
    """
    if not config.refresh_token or config.token_expires_at is None:
        return False
    expires = config.token_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=threshold_seconds)
    return expires <= deadline


def attempt_refresh(config: KeelConfig, *, timeout: float = 10.0) -> KeelConfig:
    """Refresh the access token. On success persists new tokens and returns
    the updated KeelConfig. On failure clears all OAuth fields and raises
    AuthError.

    Failure scenarios that clear the lineage:
      - Refresh token unknown / expired / already used (401 invalid_grant)
      - Lineage burn — keel-api detected refresh-token reuse and revoked
        the entire lineage server-side. Wipe local state to match.

    Transient failures (network, 5xx) re-raise AuthError as ``retryable``
    without clearing — caller can retry on the next request.
    """
    if not config.refresh_token:
        raise AuthError(
            "No refresh token available. Run `keel auth login` to authenticate.",
            suggestion="keel auth login",
            docs_url="https://app.usekeel.io/settings?tab=api-keys",
        )

    url = config.api_url.rstrip("/") + "/v1/auth/oauth/refresh"
    try:
        response = httpx.post(
            url,
            json={"refresh_token": config.refresh_token},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        # Network / TLS / DNS — leave local creds intact, surface retryable.
        raise AuthError(
            f"Failed to reach token refresh endpoint: {e}",
            retryable=True,
            suggestion="Check your network connection and retry.",
        ) from e

    if response.status_code == 401:
        # Lineage burn or expired token — local state is provably invalid.
        clear_oauth_tokens()
        raise AuthError(
            "Your session has expired. Run `keel auth login` to re-authenticate.",
            suggestion="keel auth login",
            docs_url="https://app.usekeel.io/settings?tab=api-keys",
        )
    if response.status_code >= 500:
        raise AuthError(
            f"Token refresh server error (HTTP {response.status_code}). Try again shortly.",
            retryable=True,
        )
    if response.status_code >= 400:
        clear_oauth_tokens()
        raise AuthError(
            f"Token refresh rejected (HTTP {response.status_code}): {response.text}",
            suggestion="keel auth login",
        )

    body = response.json()
    return store_oauth_tokens(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_in=int(body["expires_in"]),
        scope=body.get("scope"),
        client_name=config.client_name,
        api_url=config.api_url,
    )


__all__ = [
    "DEFAULT_REFRESH_THRESHOLD_SECONDS",
    "attempt_refresh",
    "clear_oauth_tokens",
    "needs_refresh",
    "store_oauth_tokens",
]
