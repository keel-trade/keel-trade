"""Tests for keel.browser_login — loopback OAuth client."""

from __future__ import annotations

import base64
import hashlib
import threading
import time

import urllib.error
import urllib.request

import httpx
import pytest
import respx

from keel import browser_login as bl
from keel.errors import AuthError


def _drive_loopback(url: str) -> int:
    """Hit a loopback URL using urllib (NOT httpx) so respx never intercepts."""
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


# ── PKCE + state ─────────────────────────────────────────────────────────────


def test_pkce_pair_format():
    verifier, challenge = bl._generate_pkce_pair()
    # base64url, no padding
    assert "=" not in verifier
    assert "=" not in challenge
    assert "+" not in verifier and "/" not in verifier
    assert "+" not in challenge and "/" not in challenge
    # 48 bytes → 64-char b64
    assert len(verifier) >= 60
    # SHA-256 → 32 bytes → 43 chars b64url
    assert len(challenge) == 43


def test_pkce_pair_challenge_matches_verifier():
    verifier, challenge = bl._generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected


def test_pkce_pair_uniqueness():
    samples = {bl._generate_pkce_pair()[0] for _ in range(20)}
    assert len(samples) == 20


def test_state_uniqueness():
    samples = {bl._generate_state() for _ in range(20)}
    assert len(samples) == 20


# ── discover_endpoints ───────────────────────────────────────────────────────


@respx.mock
def test_discover_endpoints_returns_metadata():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(
        return_value=httpx.Response(200, json={
            "issuer": "https://api.usekeel.io",
            "authorization_endpoint": "https://app.usekeel.io/oauth/connect",
            "token_endpoint": "https://api.usekeel.io/v1/auth/oauth/token",
            "scopes_supported": ["base", "live"],
        })
    )
    metadata = bl.discover_endpoints("https://api.usekeel.io")
    assert metadata["authorization_endpoint"] == "https://app.usekeel.io/oauth/connect"
    assert metadata["token_endpoint"] == "https://api.usekeel.io/v1/auth/oauth/token"


@respx.mock
def test_discover_endpoints_404_raises():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(return_value=httpx.Response(404))
    with pytest.raises(AuthError) as exc:
        bl.discover_endpoints("https://api.usekeel.io")
    assert "404" in str(exc.value)


@respx.mock
def test_discover_endpoints_network_error_raises_retryable():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(side_effect=httpx.ConnectError("dns"))
    with pytest.raises(AuthError) as exc:
        bl.discover_endpoints("https://api.usekeel.io")
    assert exc.value.retryable is True


# ── Loopback server + handler ────────────────────────────────────────────────


def _hit_callback(port: int, path: str = "/callback?code=K&state=S") -> int:
    """Send a single GET to the loopback handler. Returns the status."""
    return _drive_loopback(f"http://127.0.0.1:{port}{path}")


def test_bind_loopback_server_picks_free_port():
    server = bl._bind_loopback_server()
    try:
        port = server.server_address[1]
        assert isinstance(port, int)
        assert 1024 <= port <= 65535
    finally:
        server.server_close()


def test_loopback_handler_captures_code_and_state():
    server = bl._bind_loopback_server()
    try:
        port = server.server_address[1]
        # Drive the callback in a thread so the server can serve it.
        def call():
            time.sleep(0.05)
            _hit_callback(port, "/callback?code=abc&state=xyz")

        threading.Thread(target=call, daemon=True).start()
        bl._wait_for_callback(server, timeout_seconds=5)
        assert server.code == "abc"
        assert server.state == "xyz"
        assert server.error is None
    finally:
        server.server_close()


def test_loopback_handler_captures_error():
    server = bl._bind_loopback_server()
    try:
        port = server.server_address[1]
        def call():
            time.sleep(0.05)
            _hit_callback(
                port,
                "/callback?error=access_denied&error_description=user+cancelled",
            )

        threading.Thread(target=call, daemon=True).start()
        bl._wait_for_callback(server, timeout_seconds=5)
        assert server.code is None
        assert "access_denied" in (server.error or "")
    finally:
        server.server_close()


def test_loopback_handler_404s_unknown_path_then_keeps_listening():
    server = bl._bind_loopback_server()
    try:
        port = server.server_address[1]
        results = []

        def call():
            time.sleep(0.05)
            results.append(_hit_callback(port, "/favicon.ico"))
            time.sleep(0.05)
            results.append(_hit_callback(port, "/callback?code=C&state=S"))

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        bl._wait_for_callback(server, timeout_seconds=5)
        # The wait loop returns the moment server.code is set; the drive
        # thread may still be reading the 200 response. Wait for it.
        thread.join(timeout=3.0)
        assert 404 in results
        assert 200 in results
        assert server.code == "C"
    finally:
        server.server_close()


def test_loopback_handler_rejects_missing_code():
    server = bl._bind_loopback_server()
    try:
        port = server.server_address[1]
        def call():
            time.sleep(0.05)
            _hit_callback(port, "/callback?state=xyz")  # no code

        threading.Thread(target=call, daemon=True).start()
        bl._wait_for_callback(server, timeout_seconds=5)
        assert server.code is None
        assert "code" in (server.error or "")
    finally:
        server.server_close()


def test_wait_for_callback_timeout_raises():
    server = bl._bind_loopback_server()
    try:
        with pytest.raises(AuthError) as exc:
            # 1s is fine for the test — nothing connects to it.
            bl._wait_for_callback(server, timeout_seconds=1)
        assert "timed out" in str(exc.value).lower()
    finally:
        server.server_close()


# ── Token exchange ───────────────────────────────────────────────────────────


@respx.mock
def test_exchange_code_happy_path():
    respx.post("https://api.usekeel.io/v1/auth/oauth/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "jwt.X",
            "refresh_token": "krt_X",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "strategy.read",
        })
    )
    body = bl._exchange_code_for_tokens(
        token_endpoint="https://api.usekeel.io/v1/auth/oauth/token",
        code="kac_abc",
        code_verifier="verifier",
        redirect_uri="http://127.0.0.1:1234/callback",
        client_name="Keel CLI/0.4.0",
    )
    assert body["access_token"] == "jwt.X"


@respx.mock
def test_exchange_code_400_raises_authentication_error():
    respx.post("https://api.usekeel.io/v1/auth/oauth/token").mock(
        return_value=httpx.Response(400, json={"detail": "invalid_grant: code unknown"})
    )
    with pytest.raises(AuthError) as exc:
        bl._exchange_code_for_tokens(
            token_endpoint="https://api.usekeel.io/v1/auth/oauth/token",
            code="bogus",
            code_verifier="v",
            redirect_uri="http://127.0.0.1:1/callback",
            client_name="Keel CLI/0.4.0",
        )
    assert "invalid_grant" in str(exc.value)


@respx.mock
def test_exchange_code_500_marks_retryable():
    respx.post("https://api.usekeel.io/v1/auth/oauth/token").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    with pytest.raises(AuthError) as exc:
        bl._exchange_code_for_tokens(
            token_endpoint="https://api.usekeel.io/v1/auth/oauth/token",
            code="K",
            code_verifier="v",
            redirect_uri="http://127.0.0.1:1/callback",
            client_name="Keel CLI/0.4.0",
        )
    assert exc.value.retryable is True


# ── Full run() integration ───────────────────────────────────────────────────


@respx.mock
def test_run_happy_path():
    """Compose discovery + loopback + exchange — the real flow.

    The browser_login.run() function blocks waiting for the callback.
    We mock the well-known + token endpoints via respx, and we drive
    the callback ourselves from a thread by hitting the loopback port
    that run() exposes.

    To get the port mid-flight, we monkey-patch _try_open_browser to
    capture the authorize URL the moment it's known.
    """
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(
        return_value=httpx.Response(200, json={
            "issuer": "https://api.usekeel.io",
            "authorization_endpoint": "https://app.usekeel.io/oauth/connect",
            "token_endpoint": "https://api.usekeel.io/v1/auth/oauth/token",
            "scopes_supported": ["base", "live"],
        })
    )
    respx.post("https://api.usekeel.io/v1/auth/oauth/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "jwt.X",
            "refresh_token": "krt_X",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "strategy.read backtest.read",
        })
    )

    captured: dict = {}

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        captured["url"] = url
        redirect_uri = parse_qs(urlparse(url).query).get("redirect_uri", [""])[0]
        state = parse_qs(urlparse(url).query).get("state", [""])[0]

        def drive():
            time.sleep(0.1)
            _drive_loopback(f"{redirect_uri}?code=kac_test&state={state}")

        threading.Thread(target=drive, daemon=True).start()
        return True

    import keel.browser_login as bl_mod

    bl_mod._try_open_browser = fake_open
    try:
        result = bl.run(
            api_url="https://api.usekeel.io",
            include_live=False,
            client_name="Keel CLI/0.4.0",
            auth_surface="mcp",
            timeout_seconds=10,
        )
    finally:
        import importlib

        importlib.reload(bl_mod)  # restore real _try_open_browser

    assert result.access_token == "jwt.X"
    assert result.refresh_token == "krt_X"
    assert result.expires_in == 3600
    assert result.scope == "strategy.read backtest.read"
    # The authorize URL we constructed should carry the right params.
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["live"] == ["0"]
    assert qs["client_name"] == ["Keel CLI/0.4.0"]
    assert qs["entry"] == ["mcp_auth"]
    assert qs["auth_surface"] == ["mcp"]
    assert qs["utm_source"] == ["keel_mcp"]
    assert qs["utm_medium"] == ["auth"]
    assert qs["utm_campaign"] == ["mcp_auth_signup"]


@respx.mock
def test_run_include_live_sets_live_param():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(
        return_value=httpx.Response(200, json={
            "authorization_endpoint": "https://app.usekeel.io/oauth/connect",
            "token_endpoint": "https://api.usekeel.io/v1/auth/oauth/token",
        })
    )
    respx.post("https://api.usekeel.io/v1/auth/oauth/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "jwt.X",
            "refresh_token": "krt_X",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "strategy.read runner.*",
        })
    )
    captured: dict = {}

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        captured["url"] = url
        rq = parse_qs(urlparse(url).query)

        def drive():
            time.sleep(0.1)
            _drive_loopback(f"{rq['redirect_uri'][0]}?code=K&state={rq['state'][0]}")

        threading.Thread(target=drive, daemon=True).start()
        return True

    import keel.browser_login as bl_mod

    bl_mod._try_open_browser = fake_open
    try:
        result = bl.run(
            api_url="https://api.usekeel.io",
            include_live=True,
            client_name="Keel CLI/0.4.0",
            timeout_seconds=10,
        )
    finally:
        import importlib

        importlib.reload(bl_mod)

    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["live"] == ["1"]
    assert result.scope == "strategy.read runner.*"


@respx.mock
def test_run_state_mismatch_raises():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(
        return_value=httpx.Response(200, json={
            "authorization_endpoint": "https://app.usekeel.io/oauth/connect",
            "token_endpoint": "https://api.usekeel.io/v1/auth/oauth/token",
        })
    )

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        rq = parse_qs(urlparse(url).query)

        def drive():
            time.sleep(0.1)
            # Wrong state — CSRF check should fail
            _drive_loopback(f"{rq['redirect_uri'][0]}?code=K&state=WRONG")

        threading.Thread(target=drive, daemon=True).start()
        return True

    import keel.browser_login as bl_mod

    bl_mod._try_open_browser = fake_open
    try:
        with pytest.raises(AuthError) as exc:
            bl.run(
                api_url="https://api.usekeel.io",
                include_live=False,
                client_name="Keel CLI/0.4.0",
                timeout_seconds=10,
            )
        assert "CSRF" in str(exc.value) or "state" in str(exc.value).lower()
    finally:
        import importlib

        importlib.reload(bl_mod)


@respx.mock
def test_run_user_cancels_raises():
    respx.get(
        "https://api.usekeel.io/v1/auth/oauth/.well-known/oauth-authorization-server"
    ).mock(
        return_value=httpx.Response(200, json={
            "authorization_endpoint": "https://app.usekeel.io/oauth/connect",
            "token_endpoint": "https://api.usekeel.io/v1/auth/oauth/token",
        })
    )

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        rq = parse_qs(urlparse(url).query)

        def drive():
            time.sleep(0.1)
            _drive_loopback(
                f"{rq['redirect_uri'][0]}?error=access_denied"
                f"&error_description=user+cancelled&state={rq['state'][0]}"
            )

        threading.Thread(target=drive, daemon=True).start()
        return True

    import keel.browser_login as bl_mod

    bl_mod._try_open_browser = fake_open
    try:
        with pytest.raises(AuthError) as exc:
            bl.run(
                api_url="https://api.usekeel.io",
                include_live=False,
                client_name="Keel CLI/0.4.0",
                timeout_seconds=10,
            )
        assert "cancelled" in str(exc.value).lower() or "access_denied" in str(exc.value)
    finally:
        import importlib

        importlib.reload(bl_mod)
