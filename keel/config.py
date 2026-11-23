"""Configuration management — ~/.keel/config.yaml + env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


CONFIG_DIR = (
    Path(os.environ["KEEL_CONFIG_DIR"]).expanduser()
    if os.environ.get("KEEL_CONFIG_DIR")
    else Path.home() / ".keel"
)
CONFIG_FILE = CONFIG_DIR / "config.yaml"

#: Where the active credential came from (Q-2579) — never persisted.
#: ``env``: ``KEEL_API_KEY`` is the WHOLE credential for this process: no
#: stored refresh token, org context or claim state rides with it, and
#: nothing it does writes the stored login.
#: ``stored``: the login in ``~/.keel/config.yaml`` (``keel auth login`` or
#: the anonymous workspace), refreshed as it always was.
#: ``hosted``: the per-request credential a hosted server bound.
CREDENTIAL_SOURCE_ENV = "env"
CREDENTIAL_SOURCE_STORED = "stored"
CREDENTIAL_SOURCE_HOSTED = "hosted"
DEFAULT_API_URL = "https://api.usekeel.io"
#: The web app's origin for the default API (``app_origin_for_api``).
DEFAULT_APP_URL = "https://app.usekeel.io"


def app_origin_for_api(api_url: str | None) -> str:
    """The web app's origin twin of an API URL: ``api.…`` → ``app.…``, ``staging-api.…`` → ``staging-app.…``
    (STG-SURFACES #18, DATA: a CLI pointed at staging linked prod). An API host with no app twin, a non-https URL or
    none at all reads the default app."""
    from urllib.parse import urlsplit

    if not isinstance(api_url, str) or not api_url:
        return DEFAULT_APP_URL
    api = urlsplit(api_url)
    first, _, rest = (api.hostname or "").partition(".")
    if not rest or api.scheme != "https":
        return DEFAULT_APP_URL
    if first == "api":
        return f"https://app.{rest}"
    if first.endswith("-api"):
        return f"https://{first[: -len('-api')]}-app.{rest}"
    return DEFAULT_APP_URL


def configured_api_url() -> str:
    """The API this process talks to (:func:`load_config`'s), read without failing: a hosted process with no bound
    request, or an unreadable config, reads ``KEEL_API_URL`` else the stored login's URL."""
    try:
        return load_config().api_url
    except Exception:  # noqa: BLE001 - no credential bound here: the URL alone is what is asked
        return stored_login_api_url()


def configured_app_origin() -> str:
    """The web app's origin every link this process prints starts with: ``KEEL_APP_URL`` (a hosted server always
    sets it), else the configured API's app twin (:func:`app_origin_for_api`)."""
    explicit = os.environ.get("KEEL_APP_URL")
    if explicit:
        return explicit.rstrip("/")
    return app_origin_for_api(configured_api_url())


def api_keys_url() -> str:
    """Where a person makes an API key, on the app this process links (:func:`configured_app_origin`)."""
    return f"{configured_app_origin()}/settings?tab=api-keys"


def configured_api_is_default() -> bool:
    """Whether this process talks to Keel's production API (the only one ``status.usekeel.io`` reports on)."""
    return configured_api_url().rstrip("/") == DEFAULT_API_URL


@dataclass
class KeelConfig:
    api_key: str | None = None
    api_url: str = DEFAULT_API_URL
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
    # Q-2579: which credential this config carries (CREDENTIAL_SOURCE_*).
    # Set by the loaders, never written to the file.
    credential_source: str | None = None


def credential_source_label(config: KeelConfig) -> str | None:
    """The user-facing name of the active credential source, or None when
    there is no credential: ``env KEEL_API_KEY`` / ``stored login``."""
    if not config.api_key:
        return None
    return {
        CREDENTIAL_SOURCE_ENV: "env KEEL_API_KEY",
        CREDENTIAL_SOURCE_STORED: "stored login",
        CREDENTIAL_SOURCE_HOSTED: "hosted connector",
    }.get(config.credential_source or "", "explicit")


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


def load_file_config() -> KeelConfig:
    """The stored login exactly as ``~/.keel/config.yaml`` holds it.

    No environment variable is applied. Every writer of the file
    read-modify-writes through THIS, never through :func:`load_config`, so
    a process running on ``KEEL_API_KEY`` / ``KEEL_API_URL`` can never copy
    its environment into the stored login another process uses (Q-2579).
    """
    config = KeelConfig()
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
    if config.api_key:
        config.credential_source = CREDENTIAL_SOURCE_STORED
    return config


def stored_login_api_url() -> str:
    """The API a stored-login action (login, claim) targets when no
    ``--api-url`` is given: ``KEEL_API_URL``, else the file's, else prod."""
    import os

    return os.environ.get("KEEL_API_URL") or load_file_config().api_url


def load_config() -> KeelConfig:
    """The credential this process acts as.

    Precedence (Q-2579): ``KEEL_API_KEY`` set → it is the whole credential,
    against ``KEEL_API_URL`` (else the default API). The stored login's
    refresh token, org context (``X-Org-Id``), anonymous/claim state and
    ``api_url`` are NOT read, so they can neither refresh nor redirect the
    key, and a 401 on the key fails as a rejected key. No key → the stored
    login in ``~/.keel/config.yaml``, with ``KEEL_API_URL`` overriding its
    URL, refreshed as before. Callers that PERSIST use
    :func:`load_file_config` instead.

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
        return KeelConfig(
            api_key=bound.token,
            api_url=bound.api_url,
            credential_source=CREDENTIAL_SOURCE_HOSTED,
        )
    if is_hosted():
        raise hosted_auth_error()

    env_key = os.environ.get("KEEL_API_KEY")
    env_url = os.environ.get("KEEL_API_URL")
    if env_key:
        return KeelConfig(
            api_key=env_key,
            api_url=env_url or DEFAULT_API_URL,
            credential_source=CREDENTIAL_SOURCE_ENV,
        )

    config = load_file_config()
    if env_url:
        config.api_url = env_url
    return config


def save_config(config: KeelConfig) -> None:
    """Write config to ~/.keel/config.yaml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config.api_key:
        data["api_key"] = config.api_key
    if config.api_url != DEFAULT_API_URL:
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
