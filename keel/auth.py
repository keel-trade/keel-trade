"""Authentication helpers — login, logout, identity.

Two login paths:

* ``store_api_key`` — long-lived PAT pasted by the user (`keel auth login
  --key <token>`). Backwards-compatible with v0.3.x.
* ``browser_login`` — interactive OAuth 2.1 + PKCE loopback flow (the
  default on `keel auth login`).

Both paths end at ``GET /v1/me`` to confirm the credential works before
returning identity to the caller.
"""

from __future__ import annotations

from keel.client import KeelClient
from keel.config import KeelConfig, load_config, save_config


def _default_client_name() -> str:
    """Return ``Keel CLI/<version>``. Falls back if metadata is missing."""
    try:
        from importlib.metadata import version

        return f"Keel CLI/{version('keel-trade')}"
    except Exception:  # noqa: BLE001
        return "Keel CLI"


def validate_api_key(api_key: str, api_url: str | None = None) -> dict:
    """Validate an API key by calling GET /v1/me. Returns principal info."""
    config = KeelConfig(api_key=api_key)
    if api_url:
        config.api_url = api_url
    client = KeelClient(config=config)
    try:
        return client.get("/v1/me")
    finally:
        client.close()


def store_api_key(api_key: str, api_url: str | None = None) -> dict:
    """Validate and store an API key. Returns principal info."""
    info = validate_api_key(api_key, api_url)
    config = load_config()
    config.api_key = api_key
    if api_url:
        config.api_url = api_url
    save_config(config)
    return info


def clear_credentials() -> None:
    """Remove stored credentials (both legacy PAT and OAuth state)."""
    from keel.token_store import clear_oauth_tokens

    # clear_oauth_tokens wipes api_key + refresh_token + token_expires_at
    # + client_name; leaves api_url. That's exactly logout behavior.
    clear_oauth_tokens()


def browser_login(
    api_url: str | None = None,
    *,
    include_live: bool = False,
    client_name: str | None = None,
    auth_surface: str | None = None,
    timeout_seconds: int = 300,
    open_browser: bool = True,
) -> dict:
    """Drive the OAuth loopback flow + persist tokens + return identity.

    Steps:
      1. Resolve api_url (arg → current config → default).
      2. Run the loopback PKCE flow via ``browser_login.run``.
      3. Persist tokens via ``token_store.store_oauth_tokens``.
      4. Validate with ``GET /v1/me`` and return the principal info.

    Returns the principal info as ``GET /v1/me`` returns it.
    """
    from keel import browser_login as bl
    from keel.token_store import store_oauth_tokens

    resolved_url = api_url or load_config().api_url
    resolved_name = client_name or _default_client_name()

    result = bl.run(
        api_url=resolved_url,
        include_live=include_live,
        client_name=resolved_name,
        auth_surface=auth_surface,
        timeout_seconds=timeout_seconds,
        open_browser=open_browser,
    )
    store_oauth_tokens(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        scope=result.scope,
        client_name=resolved_name,
        api_url=resolved_url,
    )
    return get_identity()


def get_identity() -> dict:
    """Get current identity via GET /v1/me."""
    client = KeelClient()
    try:
        return client.get("/v1/me")
    finally:
        client.close()


def get_status() -> dict:
    """Get identity + entitlements via GET /v1/me and GET /v1/entitlements."""
    client = KeelClient()
    try:
        me = client.get("/v1/me")
        entitlements = client.get("/v1/entitlements")
        return {**me, "entitlements": entitlements}
    finally:
        client.close()
