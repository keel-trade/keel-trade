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
    """Validate and store an API key. Returns principal info.

    If the previous credentials were an anonymous grant (spec 05 R3/R4),
    the anonymous workspace is auto-claimed into the key's account —
    same behavior as the browser login path.
    """
    prior_config = load_config()
    info = validate_api_key(api_key, api_url)
    config = load_config()
    config.api_key = api_key
    # A PAT has no refresh capability — leaving a stale (anon/OAuth)
    # refresh token behind would trigger proactive-refresh 401s that
    # clear_oauth_tokens() the PAT away.
    config.refresh_token = None
    config.token_expires_at = None
    # Spec 09 CL-9: fresh credentials start with a clean org context.
    config.active_org_id = None
    if api_url:
        config.api_url = api_url
    save_config(config)

    from keel.anon import maybe_claim_after_login

    claim_result = maybe_claim_after_login(prior_config)
    if claim_result is not None:
        if "pending_claim" in claim_result:
            info = {**info, "pending_claim": claim_result["pending_claim"]}
        else:
            info = {**info, "claimed_anon_org": claim_result}
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
    attach_decision: bool | None = None,
    confirm_attach=None,
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

    # Snapshot BEFORE login: if the stored credentials are an anonymous
    # grant (spec 05 R3/R4), the post-OAuth step claims that workspace
    # into the new account (auto-claim). store_oauth_tokens overwrites
    # the anon tokens, so the proof material must be captured now.
    prior_config = load_config()

    resolved_url = api_url or prior_config.api_url
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

    # Auto-claim the anonymous workspace under the new account. Best-effort:
    # a failed/expired claim degrades to a notice, never a failed login.
    # Spec 09 CL-8: a fresh/empty account claims silently; an account that
    # already has work confirms first — `attach_decision` (explicit) or
    # `confirm_attach` (TTY prompt) decide, else the claim defers and the
    # result carries `pending_claim` for the caller to surface.
    from keel.anon import maybe_claim_after_login

    claim_result = maybe_claim_after_login(
        prior_config, attach_decision=attach_decision, confirm=confirm_attach
    )

    identity = get_identity()
    if claim_result is not None:
        if "pending_claim" in claim_result:
            identity = {**identity, "pending_claim": claim_result["pending_claim"]}
        else:
            identity = {**identity, "claimed_anon_org": claim_result}
    return identity


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
