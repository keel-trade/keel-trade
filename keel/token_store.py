"""OAuth token persistence + refresh helpers.

The CLI loopback flow (`keel/browser_login.py`) ends by calling
`store_oauth_tokens()`. `KeelClient` (`keel/client.py`) calls
`needs_refresh()` proactively and `attempt_refresh()` reactively on 401.
Refresh hits ``POST /v1/auth/oauth/refresh`` on keel-api, which already
implements OAuth 2.1 §6.1 rotation with lineage-burn reuse detection.

One stored login, many clients (Q-2602). A CLI and a key-less local MCP
server on one machine both act as the login in ``~/.keel/config.yaml``, and
each ``KeelClient`` decides from the snapshot it loaded. keel-api rotates a
refresh token exactly once and refuses a second presentation (401; after
60 s it also burns the lineage, Q-2532). So a refresh here:

- runs under an advisory lock beside the config file, so two processes on
  one machine never present the same token at once;
- re-reads the file once it holds the lock, and adopts a login another
  process already refreshed (or a new login) instead of refreshing a token
  that is no longer the current one;
- on a refusal, clears the stored login only when the file still holds the
  very refresh token that was refused. A newer login on disk is adopted,
  never deleted.

Keyed processes (``KEEL_API_KEY``, Q-2579) and hosted mode carry no refresh
token and never reach this path.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import httpx

import keel.config as _config_module
from keel.config import KeelConfig, load_file_config, save_config
from keel.errors import AuthError


logger = logging.getLogger(__name__)

try:  # POSIX
    import fcntl as _fcntl

    _msvcrt = None
except ImportError:  # pragma: no cover — Windows
    _fcntl = None  # type: ignore[assignment]
    import msvcrt as _msvcrt  # type: ignore[no-redef]

# How long a refresh waits for another process's refresh of the same login.
# Longer than one refresh's HTTP timeout, so a waiter outlasts the holder.
_LOCK_WAIT_MARGIN_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.05


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
    config = load_file_config()  # Q-2579: the file, never the env view
    config.api_key = access_token
    config.refresh_token = refresh_token
    config.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    if client_name:
        config.client_name = client_name
    if api_url:
        config.api_url = api_url
    # Spec 09 CL-9: a fresh login starts with a clean org context — the
    # claim step re-sets it when it re-owns a workspace.
    config.active_org_id = None
    save_config(config)
    return config


def clear_oauth_tokens() -> None:
    """Clear all auth fields. Idempotent.

    Wipes ``api_key`` (whether OAuth JWT or legacy long-lived PAT) plus the
    OAuth-specific ``refresh_token``, ``token_expires_at``, ``client_name``.
    Leaves ``api_url`` intact (the user's chosen environment).
    """
    config = load_file_config()  # Q-2579: the file, never the env view
    config.api_key = None
    config.refresh_token = None
    config.token_expires_at = None
    config.client_name = None
    config.anon_org_id = None
    # Spec 09: logout clears the whole claim-flow state — expiry-notice
    # stamps, org context, and any deferred claim material.
    config.anon_org_expires_at = None
    config.anon_expiry_notice_at = None
    config.active_org_id = None
    config.pending_claim = None
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


def _lock_path():
    # Resolved at call time: the config location is a module attribute.
    cfg = _config_module.CONFIG_FILE
    return cfg.with_name(cfg.name + ".lock")


def _try_lock(fd: int) -> bool:
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        else:  # pragma: no cover — Windows
            _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
    else:  # pragma: no cover — Windows
        os.lseek(fd, 0, os.SEEK_SET)
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def stored_login_lock(wait_seconds: float) -> Iterator[bool]:
    """Hold the machine-wide advisory lock on the stored login (Q-2602).

    Yields whether the lock was acquired. A holder that outlives
    ``wait_seconds`` (a hung process) does not wedge this one: the refresh
    proceeds unserialised, still re-reading the file first and still never
    clearing a login newer than the token it sent."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + wait_seconds
        while not (acquired := _try_lock(fd)):
            if time.monotonic() >= deadline:
                logger.warning(
                    "Another keel process held the login lock for %.0fs; refreshing without it.",
                    wait_seconds,
                )
                break
            time.sleep(_LOCK_POLL_SECONDS)
        yield acquired
    finally:
        if acquired:
            _unlock(fd)
        os.close(fd)


def _session_expired() -> AuthError:
    return AuthError(
        "Your session has expired. Run `keel auth login` to re-authenticate.",
        suggestion="keel auth login",
        docs_url=_api_keys_url(),
    )


def _newer_stored_login(config: KeelConfig) -> KeelConfig | None:
    """The stored login, if the file no longer holds ``config``'s refresh
    token: another process refreshed it, or the user logged in again.

    Returns ``config`` with the file's token fields (its ``api_url`` and
    other process-level fields are kept), None when the file still holds
    the same token, or no login at all (a config the caller built itself,
    or a login another process cleared: refreshing ours then answers for
    itself)."""
    stored = load_file_config()
    if stored.refresh_token == config.refresh_token or not stored.api_key:
        return None
    return dataclasses.replace(
        config,
        api_key=stored.api_key,
        refresh_token=stored.refresh_token,
        token_expires_at=stored.token_expires_at,
        client_name=stored.client_name,
    )


def _refused(config: KeelConfig, error: AuthError) -> KeelConfig:
    """A refresh of ``config.refresh_token`` was refused. Clear the stored
    login only if it is still the one refused; a newer one is adopted."""
    newer = _newer_stored_login(config)
    if newer is not None:
        logger.debug("Refresh refused, but the stored login is newer; using it.")
        return newer
    clear_oauth_tokens()
    raise error


def attempt_refresh(config: KeelConfig, *, timeout: float = 10.0) -> KeelConfig:
    """Refresh the access token, serialised across processes (Q-2602).

    Under :func:`stored_login_lock`, re-reads the stored login: if another
    process already refreshed it (or the user logged in again), that login
    is returned and no refresh is sent, unless it is itself due. Otherwise
    the refresh is sent, persisted and returned.

    A refusal (401 or other 4xx: unknown / expired / already-used token,
    lineage burn) clears the stored login ONLY when the file still holds
    the refresh token that was refused, then raises AuthError; a newer
    stored login is returned instead. Transient failures (network, 5xx)
    raise AuthError as ``retryable`` without clearing.
    """
    if not config.refresh_token:
        raise AuthError(
            "No refresh token available. Run `keel auth login` to authenticate.",
            suggestion="keel auth login",
            docs_url=_api_keys_url(),
        )
    with stored_login_lock(timeout + _LOCK_WAIT_MARGIN_SECONDS):
        newer = _newer_stored_login(config)
        if newer is not None:
            if not needs_refresh(newer):
                return newer
            config = newer
        return _send_refresh(config, timeout=timeout)


def _send_refresh(config: KeelConfig, *, timeout: float) -> KeelConfig:
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
        # Lineage burn, expired token, or a token another process already
        # rotated — invalid only if the file still holds it.
        return _refused(config, _session_expired())
    if response.status_code >= 500:
        raise AuthError(
            f"Token refresh server error (HTTP {response.status_code}). Try again shortly.",
            retryable=True,
        )
    if response.status_code >= 400:
        return _refused(
            config,
            AuthError(
                f"Token refresh rejected (HTTP {response.status_code}): {response.text}",
                suggestion="keel auth login",
            ),
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
    "stored_login_lock",
]


def _api_keys_url() -> str:
    """The API-keys page on the app this process links (``keel.config.api_keys_url``)."""
    from keel.config import api_keys_url

    return api_keys_url()
