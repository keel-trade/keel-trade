"""Loopback browser-OAuth client for ``keel auth login``.

Implements the RFC 8252 native-app loopback redirect pattern against
Keel's OAuth 2.1 + PKCE endpoints. End-to-end:

  1. Discover authorize/token endpoints via
     ``{api_url}/v1/auth/oauth/.well-known/oauth-authorization-server``.
  2. Generate PKCE pair (S256) + CSRF state.
  3. Bind ``127.0.0.1:0`` — the kernel picks a free port.
  4. Open the user's browser to the authorize endpoint with the
     loopback ``redirect_uri``. If the browser fails to open, print the
     URL to stderr — the user can paste it manually.
  5. Wait for the browser to hit the loopback ``/callback`` with
     ``?code=…&state=…``. Reject state mismatches; reject ``?error=…``
     (user cancelled).
  6. Exchange the code at the token endpoint. Return the token payload.

The CLI integration (``keel auth login``) persists the result via
``token_store.store_oauth_tokens``.

This module does NOT touch the config file or print success messages —
that's the orchestrator's job. Keeps the module unit-testable without
filesystem patching.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import sys
import time
import webbrowser
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from keel.errors import AuthError


logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes from browser-open to /callback


@dataclass
class BrowserLoginResult:
    """The /v1/auth/oauth/token response, plus the api_url it came from."""

    access_token: str
    refresh_token: str
    expires_in: int
    scope: str | None
    token_type: str
    api_url: str


# ── PKCE + state generation ──────────────────────────────────────────────────


def _generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). S256, base64url, no padding.

    Matches the ``_pkce_pair`` helper used by the server-side tests at
    `the API auth-OAuth test suite`.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def _generate_state() -> str:
    return secrets.token_urlsafe(32)


# ── Discovery ────────────────────────────────────────────────────────────────


def discover_endpoints(api_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch the RFC 8414 metadata from the configured Keel API.

    Returns the parsed JSON which includes ``authorization_endpoint`` and
    ``token_endpoint``. Lets the CLI work against any Keel environment
    (prod, staging, self-hosted) by following the api_url's pointer to
    its own app + token endpoints, rather than hardcoding URL shapes.
    """
    well_known = api_url.rstrip("/") + "/v1/auth/oauth/.well-known/oauth-authorization-server"
    try:
        response = httpx.get(well_known, timeout=timeout)
    except httpx.HTTPError as e:
        raise AuthError(
            f"Could not reach {well_known}: {e}",
            suggestion="Check your --api-url / KEEL_API_URL or your network.",
            retryable=True,
        ) from e
    if response.status_code != 200:
        raise AuthError(
            f"OAuth metadata endpoint returned HTTP {response.status_code}.",
            suggestion="Verify --api-url points at a Keel API that supports OAuth.",
        )
    return response.json()


# ── Loopback HTTP server ─────────────────────────────────────────────────────


_SUCCESS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Keel - signed in</title>
  <style>
    html, body { height: 100%; margin: 0; }
    body {
      display: flex; align-items: center; justify-content: center;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b0d10; color: #e6e6e6;
    }
    .card { text-align: center; max-width: 28rem; padding: 2rem; }
    .card h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
    .card p { margin: 0; color: #8a8f96; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Signed in to Keel</h1>
    <p>You can close this tab and return to your terminal.</p>
  </div>
</body>
</html>
"""

_ERROR_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Keel - sign-in error</title>
  <style>
    html, body {{ height: 100%; margin: 0; }}
    body {{
      display: flex; align-items: center; justify-content: center;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b0d10; color: #e6e6e6;
    }}
    .card {{ text-align: center; max-width: 28rem; padding: 2rem; }}
    .card h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; color: #ff6b6b; }}
    .card p {{ margin: 0; color: #8a8f96; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Sign-in error</h1>
    <p>{message}</p>
  </div>
</body>
</html>
"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler — captures the /callback query and shuts down."""

    server: "_CallbackServer"  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802 — stdlib name
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        params = parse_qs(parsed.query)
        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if error:
            error_desc = params.get("error_description", [""])[0]
            msg = f"{error}: {error_desc}" if error_desc else error
            self.server.error = msg
            self._respond_html(400, _ERROR_HTML_TEMPLATE.format(message=msg))
            return

        if not code:
            self.server.error = "callback missing 'code' parameter"
            self._respond_html(
                400,
                _ERROR_HTML_TEMPLATE.format(message="Missing authorization code"),
            )
            return

        # State mismatch: hold the captured value but mark mismatch -- the
        # caller decides how to surface. We still respond cleanly so the
        # browser doesn't hang.
        self.server.code = code
        self.server.state = state
        self._respond_html(200, _SUCCESS_HTML)

    def _respond_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default stderr request log — we have our own logger.
        logger.debug("loopback %s - - %s", self.address_string(), format % args)


class _CallbackServer(http.server.HTTPServer):
    """HTTPServer with a place to stash the captured callback data."""

    code: str | None = None
    state: str | None = None
    error: str | None = None


def _bind_loopback_server() -> _CallbackServer:
    """Bind ``127.0.0.1:0`` and return the server. Caller picks the port
    from ``server.server_address[1]`` after binding.
    """
    try:
        server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    except OSError as e:
        raise AuthError(
            f"Could not bind a loopback port: {e}",
            suggestion="Check your firewall or run `keel auth login --key <token>` instead.",
        ) from e
    return server


def _wait_for_callback(server: _CallbackServer, timeout_seconds: int) -> None:
    """Service requests until the callback fires or the timeout elapses.

    Non-/callback paths (e.g. browser fetching /favicon.ico) get 404 and
    we keep listening. Returns when ``server.code`` or ``server.error``
    is set, or raises AuthError on timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        server.timeout = max(1.0, min(remaining, 30.0))
        # handle_request blocks for up to server.timeout seconds.
        server.handle_request()
        if server.code is not None or server.error is not None:
            return
    raise AuthError(
        "Login timed out. To skip the browser, run "
        "`keel auth login --key <token>` with a key from "
        "https://app.usekeel.io/settings?tab=api-keys.",
        suggestion="keel auth login --key <token>",
        docs_url="https://app.usekeel.io/settings?tab=api-keys",
    )


# ── Browser open ─────────────────────────────────────────────────────────────


def _try_open_browser(url: str) -> bool:
    """Return True if a browser opened. Print the URL to stderr if not.

    ``webbrowser.open`` returns ``True`` even when no browser exists on
    some platforms (it just spawns a non-existent process), so we also
    print the URL to stderr unconditionally for the user's benefit.
    """
    print(
        "\nOpening your browser to complete sign-in. If it doesn't open, paste:",
        file=sys.stderr,
    )
    print(f"  {url}\n", file=sys.stderr)
    try:
        return webbrowser.open(url, new=2)
    except Exception as e:  # noqa: BLE001
        logger.debug("webbrowser.open failed: %s", e)
        return False


# ── Token exchange ───────────────────────────────────────────────────────────


def _exchange_code_for_tokens(
    *,
    token_endpoint: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_name: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "client_name": client_name,
    }
    try:
        response = httpx.post(token_endpoint, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise AuthError(
            f"Could not reach token endpoint: {e}",
            retryable=True,
        ) from e
    if response.status_code == 200:
        return response.json()
    # 4xx / 5xx — surface the server detail.
    try:
        detail = response.json().get("detail") or response.text
    except Exception:  # noqa: BLE001
        detail = response.text
    if response.status_code in (400, 401):
        raise AuthError(f"Token exchange failed: {detail}")
    raise AuthError(
        f"Token exchange failed (HTTP {response.status_code}): {detail}",
        retryable=response.status_code >= 500,
    )


# ── Public entry point ───────────────────────────────────────────────────────


def run(
    *,
    api_url: str,
    include_live: bool = False,
    client_name: str,
    auth_surface: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> BrowserLoginResult:
    """Drive the full loopback OAuth dance. Returns tokens on success.

    Arguments:
        api_url: Keel API base URL (e.g. ``https://api.usekeel.io``).
            We discover the authorize + token endpoints from its
            well-known metadata.
        include_live: True → request the ``live`` tier (checkbox
            pre-checked on the consent page). False → ``base`` tier only.
        client_name: User-facing label persisted in the credential row,
            e.g. ``"Keel CLI/0.4.0"``.
        auth_surface: Optional first-touch attribution marker for product
            analytics. ``"mcp"`` tags account creation from ``keel_auth_login``.
        timeout_seconds: Maximum wall-clock time waiting for the
            browser callback. Default 5 minutes.
        open_browser: If False, only prints the URL to stderr — used in
            tests. The loopback server still binds and waits.

    Raises:
        AuthError on any failure (timeout, callback error, token
        exchange failure, server unreachable). Never returns None.
    """
    metadata = discover_endpoints(api_url)
    auth_endpoint = metadata["authorization_endpoint"]
    token_endpoint = metadata["token_endpoint"]

    verifier, challenge = _generate_pkce_pair()
    state = _generate_state()

    server = _bind_loopback_server()
    try:
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"

        authorize_params = {
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "client_name": client_name,
            "live": "1" if include_live else "0",
        }
        if auth_surface == "mcp":
            authorize_params.update(
                {
                    "entry": "mcp_auth",
                    "auth_surface": "mcp",
                    "utm_source": "keel_mcp",
                    "utm_medium": "auth",
                    "utm_campaign": "mcp_auth_signup",
                }
            )

        authorize_url = auth_endpoint + "?" + urlencode(authorize_params)

        logger.debug("loopback listening on %s", redirect_uri)

        if open_browser:
            _try_open_browser(authorize_url)
        else:
            print(f"\nOpen this URL to sign in:\n  {authorize_url}\n", file=sys.stderr)

        _wait_for_callback(server, timeout_seconds)

        if server.error:
            raise AuthError(f"Sign-in cancelled or failed: {server.error}")
        if server.code is None:
            # Belt-and-braces — _wait_for_callback should have raised.
            raise AuthError("Sign-in completed without a code (this is a bug).")
        if server.state != state:
            raise AuthError(
                "CSRF check failed: state parameter mismatch on the OAuth "
                "callback. Possible browser misbehavior — please retry."
            )

        body = _exchange_code_for_tokens(
            token_endpoint=token_endpoint,
            code=server.code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            client_name=client_name,
        )
    finally:
        # Ensure the socket is freed regardless of which branch we exit on.
        try:
            server.server_close()
        except Exception as e:  # noqa: BLE001
            logger.debug("server_close failed: %s", e)

    return BrowserLoginResult(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_in=int(body["expires_in"]),
        scope=body.get("scope"),
        token_type=body.get("token_type", "Bearer"),
        api_url=api_url,
    )


__all__ = [
    "BrowserLoginResult",
    "DEFAULT_TIMEOUT_SECONDS",
    "discover_endpoints",
    "run",
]
