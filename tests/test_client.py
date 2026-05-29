"""Tests for keel.client — API client with retries and error translation."""

import pytest
import httpx
import respx

from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import AuthError, KeelError, NotFoundError


@pytest.fixture
def config():
    return KeelConfig(api_key="sk_test_123", api_url="https://api.test.io")


@pytest.fixture
def client(config):
    c = KeelClient(config=config)
    yield c
    c.close()


class TestAuth:
    def test_requires_api_key(self):
        client = KeelClient(config=KeelConfig(api_key=None))
        with pytest.raises(AuthError, match="Not authenticated"):
            client.get("/v1/me")

    @respx.mock
    def test_sends_auth_header(self, client):
        route = respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(200, json={"user": "test"})
        )
        client.get("/v1/me")
        assert route.calls.last.request.headers["authorization"] == "Bearer sk_test_123"


class TestHTTPMethods:
    @respx.mock
    def test_get(self, client):
        respx.get("https://api.test.io/v1/strategies").mock(
            return_value=httpx.Response(200, json={"strategies": []})
        )
        result = client.get("/v1/strategies")
        assert result == {"strategies": []}

    @respx.mock
    def test_get_with_params(self, client):
        route = respx.get("https://api.test.io/v1/strategies").mock(
            return_value=httpx.Response(200, json=[])
        )
        client.get("/v1/strategies", limit=10, search="test")
        req = route.calls.last.request
        assert "limit=10" in str(req.url)

    @respx.mock
    def test_post(self, client):
        respx.post("https://api.test.io/v1/backtests").mock(
            return_value=httpx.Response(200, json={"backtest_id": "bt_123"})
        )
        result = client.post("/v1/backtests", json={"strategy_id": "str_1"})
        assert result["backtest_id"] == "bt_123"

    @respx.mock
    def test_patch(self, client):
        respx.patch("https://api.test.io/v1/strategies/str_1").mock(
            return_value=httpx.Response(200, json={"updated": True})
        )
        result = client.patch("/v1/strategies/str_1", json={"source": "..."})
        assert result["updated"] is True

    @respx.mock
    def test_delete(self, client):
        respx.delete("https://api.test.io/v1/live/dep_1").mock(
            return_value=httpx.Response(200, json={"stopped": True})
        )
        result = client.delete("/v1/live/dep_1")
        assert result["stopped"] is True


class TestErrorTranslation:
    @respx.mock
    def test_404_raises_not_found(self, client):
        respx.get("https://api.test.io/v1/strategies/bad").mock(
            return_value=httpx.Response(404, text="Not found")
        )
        with pytest.raises(NotFoundError):
            client.get("/v1/strategies/bad")

    @respx.mock
    def test_401_raises_auth_error(self, client):
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        with pytest.raises(AuthError):
            client.get("/v1/me")


class TestContextManager:
    @respx.mock
    def test_context_manager(self, config):
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(200, json={"user": "test"})
        )
        with KeelClient(config=config) as client:
            result = client.get("/v1/me")
            assert result["user"] == "test"


# ─────────────────────────────────────────────────────────────────────────────
# Transparent OAuth refresh — proactive (near-expiry) + reactive (on 401)
# ─────────────────────────────────────────────────────────────────────────────


from datetime import datetime, timedelta, timezone


class TestRefreshProactive:
    """Token within the refresh threshold of expiry → refresh fires first."""

    @respx.mock
    def test_proactive_refresh_swaps_access_token(self):
        # Token expires in 10s — under the 60s default threshold.
        config = KeelConfig(
            api_key="jwt.old",
            api_url="https://api.test.io",
            refresh_token="krt_old",
            token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        refresh = respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(200, json={
                "access_token": "jwt.new",
                "refresh_token": "krt_new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "strategy.read",
            })
        )
        me = respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(200, json={"principal_id": "p_X"})
        )
        client = KeelClient(config=config)
        try:
            client.get("/v1/me")
        finally:
            client.close()

        assert refresh.called  # proactive refresh fired
        assert me.calls.last.request.headers["authorization"] == "Bearer jwt.new"

    @respx.mock
    def test_proactive_refresh_skipped_when_far_from_expiry(self):
        config = KeelConfig(
            api_key="jwt.fresh",
            api_url="https://api.test.io",
            refresh_token="krt_fresh",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        refresh = respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(200, json={
                "access_token": "jwt.shouldnt", "refresh_token": "krt_x",
                "token_type": "Bearer", "expires_in": 3600, "scope": "",
            })
        )
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(200, json={"principal_id": "p_X"})
        )
        client = KeelClient(config=config)
        try:
            client.get("/v1/me")
        finally:
            client.close()

        assert not refresh.called

    @respx.mock
    def test_proactive_refresh_skipped_without_refresh_token(self):
        config = KeelConfig(
            api_key="sk_pat",  # legacy PAT user
            api_url="https://api.test.io",
            refresh_token=None,
            token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        refresh = respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(200, json={"principal_id": "p_X"})
        )
        client = KeelClient(config=config)
        try:
            client.get("/v1/me")
        finally:
            client.close()

        assert not refresh.called


class TestRefreshReactive:
    """401 → try refresh → retry request once with new token."""

    @respx.mock
    def test_401_with_refresh_token_retries_after_refresh(self):
        config = KeelConfig(
            api_key="jwt.expired",
            api_url="https://api.test.io",
            refresh_token="krt_X",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        # First /v1/me returns 401; second returns 200.
        responses = [
            httpx.Response(401, text="Unauthorized"),
            httpx.Response(200, json={"principal_id": "p_X"}),
        ]
        me_route = respx.get("https://api.test.io/v1/me").mock(side_effect=responses)
        respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(200, json={
                "access_token": "jwt.fresh",
                "refresh_token": "krt_new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "",
            })
        )
        client = KeelClient(config=config)
        try:
            result = client.get("/v1/me")
        finally:
            client.close()

        assert result["principal_id"] == "p_X"
        assert me_route.call_count == 2
        # Retried request used the new token.
        assert me_route.calls[-1].request.headers["authorization"] == "Bearer jwt.fresh"

    @respx.mock
    def test_401_without_refresh_token_propagates_auth_error(self):
        config = KeelConfig(
            api_key="sk_pat",
            api_url="https://api.test.io",
            refresh_token=None,
        )
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        refresh = respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(200, json={})
        )
        client = KeelClient(config=config)
        try:
            with pytest.raises(AuthError):
                client.get("/v1/me")
        finally:
            client.close()
        # Refresh never even attempted.
        assert not refresh.called

    @respx.mock
    def test_401_with_failed_refresh_raises_auth_error(self):
        config = KeelConfig(
            api_key="jwt.expired",
            api_url="https://api.test.io",
            refresh_token="krt_burned",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        respx.get("https://api.test.io/v1/me").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        respx.post("https://api.test.io/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(401, json={"detail": "invalid_grant"})
        )
        client = KeelClient(config=config)
        try:
            with pytest.raises(AuthError):
                client.get("/v1/me")
        finally:
            client.close()

    @respx.mock
    def test_401_only_retries_once(self):
        """A 401 after refresh does NOT trigger another refresh + retry."""
        config = KeelConfig(
            api_key="jwt.X",
            api_url="https://api.test.io",
            refresh_token="krt_X",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        me_route = respx.get("https://api.test.io/v1/me").mock(
            side_effect=[
                httpx.Response(401, text="Unauthorized"),
                httpx.Response(401, text="Still unauthorized"),
            ]
        )
        refresh_route = respx.post(
            "https://api.test.io/v1/auth/oauth/refresh"
        ).mock(
            return_value=httpx.Response(200, json={
                "access_token": "jwt.fresh",
                "refresh_token": "krt_new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "",
            })
        )
        client = KeelClient(config=config)
        try:
            with pytest.raises(AuthError):
                client.get("/v1/me")
        finally:
            client.close()

        # /v1/me called exactly twice (1 original + 1 retry).
        assert me_route.call_count == 2
        # Refresh called exactly once.
        assert refresh_route.call_count == 1
