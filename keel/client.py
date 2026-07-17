"""Sync HTTP client for the Keel API.

Handles authentication, retries with exponential backoff, and error translation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from keel.config import KeelConfig, load_config
from keel.errors import AuthError, KeelError, translate_http_error


logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # 1s, 2s, 4s


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
        return headers

    def _require_auth(self) -> None:
        if not self._config.api_key:
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

        last_error: Exception | None = None
        refresh_already_attempted = False
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.request(method, path, **kwargs)
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
                    # Rate limited — retry after backoff
                    retry_after = float(
                        response.headers.get("Retry-After", _BACKOFF_BASE * (2**attempt))
                    )
                    logger.warning("Rate limited, retrying after %.1fs", retry_after)
                    time.sleep(retry_after)
                    continue
                if response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                    # Server error — retry with backoff
                    delay = _BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "Server error %d, retrying in %.1fs", response.status_code, delay
                    )
                    time.sleep(delay)
                    continue
                if response.status_code >= 400:
                    raise translate_http_error(response.status_code, response.text)
                return response.json()
            except KeelError:
                raise
            except httpx.TimeoutException as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_BASE * (2**attempt)
                    logger.warning("Request timeout, retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
            except httpx.HTTPError as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_BASE * (2**attempt)
                    logger.warning("HTTP error: %s, retrying in %.1fs", e, delay)
                    time.sleep(delay)
                    continue
        raise KeelError(f"Request failed after {_MAX_RETRIES} attempts: {last_error}")

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> KeelClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
