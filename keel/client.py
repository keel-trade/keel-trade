"""Sync HTTP client for the Keel API.

Handles authentication, retries with exponential backoff, and error translation.

Retries are idempotency-aware (Q-2273 L1): keel-api takes no client
idempotency key, so a write whose response was lost may already have been
applied, and resending it creates a second strategy, backtest or share link.
A GET/HEAD is retried on a 5xx, a timeout or a transport error; a write is
retried ONLY when the connection failed before anything was sent
(`httpx.ConnectError` / `ConnectTimeout` / `PoolTimeout`). A write that fails
after sending raises with ``retryable=False`` and says it may have been
applied (`errors.write_unconfirmed`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from keel.config import KeelConfig, load_config
from keel.errors import (
    AuthError,
    KeelError,
    rate_limited_error,
    retry_after_seconds,
    translate_http_error,
    transport_error,
    write_unconfirmed,
)


logger = logging.getLogger(__name__)

#: The header naming the client package + version (Q-2500).
CLIENT_HEADER = "X-Keel-Client"


def client_header_value() -> str:
    """``keel-trade/<version>`` — the value of :data:`CLIENT_HEADER`."""
    from keel import __version__

    return f"keel-trade/{__version__}"


_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # 1s, 2s, 4s
#: Methods safe to resend after a lost response (RFC 9110 §9.2.2 —
#: keel-api's PUT/DELETE are not relied on as idempotent here).
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD"})
#: Transport failures that happen BEFORE the request is sent — the one
#: failure a write may be resent after.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
#: The longest a 429's Retry-After is waited out INSIDE one call; a longer
#: wait is returned to the caller as `retry_after_s` instead of blocking it.
_RETRY_AFTER_CAP_S = 5.0


class KeelClient:
    """Sync httpx wrapper with auth, retries, and error translation."""

    def __init__(self, config: KeelConfig | None = None) -> None:
        self._config = config or load_config()
        self._client = httpx.Client(
            base_url=self._config.api_url,
            timeout=_DEFAULT_TIMEOUT,
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        # Surface self-identification (spec 08 R5): tell keel-api which
        # surface is calling so commit attribution / telemetry classify
        # correctly regardless of which client minted the token.
        from keel.surface import current_surface

        headers["x-keel-surface"] = current_surface()
        # Q-2500: the wheel names its own version. keel-api treats component
        # pins from a wheel WITHOUT this header (≤ 0.7.0, whose bundled lock
        # predates the server's catalogue) as advisory; with it, the pins are
        # the caller's (Q-2270). Also attributes every write to a version.
        headers[CLIENT_HEADER] = client_header_value()
        # Spec 09 CL-9: post-claim org context. OAuth tokens are org-bound
        # at mint (pre-claim), so after a claim the client targets the
        # claimed org explicitly via the API's existing X-Org-Id mechanism
        # (membership was granted by the re-own). Only ever set by a
        # successful claim; never in hosted mode (bound configs carry none).
        if self._config.active_org_id:
            headers["X-Org-Id"] = self._config.active_org_id
        return headers

    @property
    def has_credentials(self) -> bool:
        """Whether this client already holds credentials (never mints)."""
        return bool(self._config.api_key)

    def _require_auth(self) -> None:
        if not self._config.api_key:
            # CLI instant start (spec 05 R4): with NO stored credentials,
            # the first command needing auth mints an anonymous workspace
            # and proceeds — never when credentials exist (this branch only
            # runs when api_key is absent), never in hosted mode (hosted
            # config resolution raised long before this point).
            from keel.anon import anon_auto_enabled, anonymous_start

            if anon_auto_enabled():
                try:
                    updated = anonymous_start(api_url=self._config.api_url)
                except AuthError as anon_err:
                    raise AuthError(
                        "Not authenticated, and anonymous start was "
                        f"unavailable: {anon_err} "
                        "Run `keel auth login` (browser) or `keel auth login "
                        "--key <token>` for CI/SSH/Codespaces.",
                        suggestion="Run `keel auth login`.",
                        docs_url="https://app.usekeel.io/settings?tab=api-keys",
                    ) from anon_err
                self._config = updated
                self._client.headers["Authorization"] = f"Bearer {updated.api_key}"
                return
            raise AuthError(
                "Not authenticated. "
                "From an MCP agent: call the `keel_auth_login` tool — it opens "
                "a browser and persists tokens automatically. "
                "From a terminal: run `keel auth login` (browser) or "
                "`keel auth login --key <token>` for CI/SSH/Codespaces (token "
                "from https://app.usekeel.io/settings?tab=api-keys).",
                suggestion=(
                    "Call MCP tool `keel_auth_login` (or run `keel auth login` in a terminal)."
                ),
                docs_url="https://app.usekeel.io/settings?tab=api-keys",
            )

    def get(self, path: str, **params: Any) -> Any:
        """GET request with retries and error handling."""
        self._require_auth()
        return self._request("GET", path, params=params or None)

    def get_public(self, path: str, **params: Any) -> Any:
        """GET a public endpoint that does not require authentication.

        Used for share-resolve / share-graph endpoints under `/s/...`. Skips
        the auth precheck; if the user happens to be logged in the auth
        header still goes through but the public endpoints ignore it.
        """
        return self._request("GET", path, params=params or None)

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        """POST request with retries and error handling."""
        self._require_auth()
        return self._request("POST", path, json=json, params=params or None)

    def patch(self, path: str, json: dict | None = None) -> Any:
        """PATCH request with retries and error handling."""
        self._require_auth()
        return self._request("PATCH", path, json=json)

    def put(self, path: str, json: dict | None = None) -> Any:
        """PUT request with retries and error handling."""
        self._require_auth()
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        """DELETE request with retries and error handling."""
        self._require_auth()
        return self._request("DELETE", path)

    def _maybe_refresh_proactively(self) -> None:
        """Refresh the access token if it expires within ~60s.

        Only acts when ``refresh_token`` is set (OAuth flow). Legacy PAT
        users with just ``api_key`` see no behavior change. Refresh
        failures bubble up as AuthError; transient (5xx / network)
        failures are silently absorbed here — the in-flight request will
        try anyway and hit 401 if the access token is truly dead.
        """
        from keel.token_store import attempt_refresh, needs_refresh

        if not needs_refresh(self._config):
            return
        try:
            updated = attempt_refresh(self._config)
        except AuthError as e:
            if e.retryable:
                logger.debug("Proactive refresh transient failure: %s", e)
                return
            raise
        self._config = updated
        self._client.headers["Authorization"] = f"Bearer {updated.api_key}"

    def _attempt_reactive_refresh(self) -> bool:
        """Refresh-on-401. Returns True on success (caller should retry).

        Raises AuthError on hard refresh failure (lineage burn / invalid
        grant). Local OAuth fields are cleared by attempt_refresh on hard
        failure. Returns False if there's no refresh_token to use.
        """
        from keel.token_store import attempt_refresh

        if not self._config.refresh_token:
            return False
        updated = attempt_refresh(self._config)
        self._config = updated
        self._client.headers["Authorization"] = f"Bearer {updated.api_key}"
        return True

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute request with retry logic + transparent OAuth refresh.

        Refresh fires in two places when an OAuth refresh_token is set:
          1. Proactively before sending if the access token is within
             the refresh threshold of expiry.
          2. Reactively on 401, exactly once per request.
        """
        self._maybe_refresh_proactively()

        idempotent = method.upper() in _IDEMPOTENT_METHODS
        refresh_already_attempted = False
        for attempt in range(_MAX_RETRIES):
            last = attempt == _MAX_RETRIES - 1
            delay = _BACKOFF_BASE * (2**attempt)
            try:
                response = self._client.request(method, path, **kwargs)
            except _NOT_SENT as e:
                # Nothing reached the server: safe to resend on any method.
                if not last:
                    logger.warning("Connection failed, retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                raise transport_error(e, sent=False) from e
            except httpx.HTTPError as e:
                # Sent (or possibly sent) and no response read: a write may
                # have been applied, so only a read is resent.
                if idempotent and not last:
                    logger.warning(
                        "Request failed (%s), retrying in %.1fs", type(e).__name__, delay
                    )
                    time.sleep(delay)
                    continue
                if idempotent:
                    raise transport_error(e, sent=True) from e
                raise write_unconfirmed(method, transport_error(e, sent=True)) from e
            # Log rate limit info at verbose level
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                logger.debug("Rate limit remaining: %s", remaining)
            if (
                response.status_code == 401
                and not refresh_already_attempted
                and self._config.refresh_token
            ):
                refresh_already_attempted = True
                try:
                    if self._attempt_reactive_refresh():
                        logger.debug("Refreshed access token after 401; retrying request.")
                        continue
                except AuthError:
                    # Refresh failed hard (lineage burn / invalid grant) —
                    # OAuth fields cleared; fall through to normal 401.
                    pass
            if response.status_code == 429:
                # Rate limited (Q-2273 L2): wait what the server asks — either
                # Retry-After form — but never past `_RETRY_AFTER_CAP_S` inside
                # a call and never after the last attempt; otherwise hand the
                # wait back to the caller in the `rate_limited` envelope. A 429
                # is refused before it is processed, so a write may be resent.
                wait = retry_after_seconds(response.headers.get("Retry-After"))
                if last or (wait is not None and wait > _RETRY_AFTER_CAP_S):
                    raise rate_limited_error(wait)
                wait = delay if wait is None else wait
                logger.warning("Rate limited, retrying after %.1fs", wait)
                time.sleep(wait)
                continue
            if response.status_code >= 500:
                if idempotent and not last:
                    logger.warning(
                        "Server error %d, retrying in %.1fs", response.status_code, delay
                    )
                    time.sleep(delay)
                    continue
                err = translate_http_error(response.status_code, response.text)
                raise err if idempotent else write_unconfirmed(method, err)
            if response.status_code >= 400:
                raise translate_http_error(response.status_code, response.text)
            try:
                return response.json()
            except ValueError:
                # A 2xx whose body is not JSON (a proxy page, a truncated
                # body): the parser's "Expecting value: line 1 column 1" is
                # not a message (Q-2273 L3). A write answered 2xx was most
                # likely applied, so it is never presented as safe to resend.
                err = KeelError(
                    f"Keel answered (HTTP {response.status_code}) with a response "
                    "that could not be read.",
                    error_code="server_error",
                    retryable=True,
                    suggestion="Retry in a few seconds. If it keeps failing, Keel may be unavailable.",
                )
                raise err if idempotent else write_unconfirmed(method, err) from None
        raise KeelError(f"Request failed after {_MAX_RETRIES} attempts.")

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> KeelClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
