"""Anonymous claim-later tier — CLI instant start + auto-claim (spec 05 R3/R4).

Test-first coverage for the SDK half of M7.2/M7.1:
- config marker round-trip + logout clearing;
- `anonymous_start` grant call, notice, kill-switch/ratelimit degradation;
- instant start in `KeelClient._require_auth` (CLI surface only, never
  with existing credentials, env-disableable);
- auto-claim on `keel auth login` (browser + --key paths) including the
  expired-workspace degradation.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import keel.surface as surface_mod
import pytest
import respx
from keel.anon import (
    anon_auto_enabled,
    anonymous_start,
    is_anon,
    maybe_claim_after_login,
)
from keel.client import KeelClient
from keel.config import KeelConfig, load_config, save_config
from keel.errors import AuthError


API = "https://api.test.io"

GRANT_RESPONSE = {
    "access_token": "eyJanon.access.token",
    "refresh_token": "krt_anon_refresh_token_x",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "strategy.read strategy.create",
    "anonymous": True,
    "org_id": "org_anon_1",
    "principal_id": "prn_anon_1",
    "plan": "anon",
    "org_expires_at": "2026-07-24T00:00:00+00:00",
    "quota": {"backtest_runs": 15, "backtest_compute_seconds": 600, "strategies_max": 3},
    "notice": "Running anonymously — 15 backtests and 3 strategies included.",
    "claim": {"endpoint": "/v1/orgs/claim", "how": "sign in then POST"},
}


@pytest.fixture(autouse=True)
def _reset_surface(monkeypatch):
    monkeypatch.setattr(surface_mod, "_SURFACE", None)
    monkeypatch.delenv("KEEL_ANON_AUTO", raising=False)


@pytest.fixture
def cli_surface(monkeypatch):
    monkeypatch.setattr(surface_mod, "_SURFACE", "cli")


class TestConfigMarker:
    def test_round_trip(self):
        save_config(KeelConfig(api_key="tok", anon_org_id="org_anon_1"))
        loaded = load_config()
        assert loaded.anon_org_id == "org_anon_1"
        assert is_anon(loaded) is True

    def test_logout_clears_marker(self):
        from keel.token_store import clear_oauth_tokens

        save_config(KeelConfig(api_key="tok", anon_org_id="org_anon_1"))
        clear_oauth_tokens()
        assert load_config().anon_org_id is None

    def test_is_anon_false_without_key(self):
        save_config(KeelConfig(api_key=None, anon_org_id="org_anon_1"))
        assert is_anon() is False


class TestAnonAutoEnabled:
    def test_cli_surface_enabled(self, cli_surface):
        assert anon_auto_enabled() is True

    def test_sdk_surface_disabled(self):
        assert anon_auto_enabled() is False

    def test_env_opt_out_wins(self, cli_surface, monkeypatch):
        monkeypatch.setenv("KEEL_ANON_AUTO", "0")
        assert anon_auto_enabled() is False

    def test_env_opt_in_for_sdk(self, monkeypatch):
        monkeypatch.setenv("KEEL_ANON_AUTO", "1")
        assert anon_auto_enabled() is True


class TestAnonymousStart:
    @respx.mock
    def test_grant_persists_tokens_and_marker(self, capsys):
        respx.post(f"{API}/v1/auth/anonymous").mock(
            return_value=httpx.Response(201, json=GRANT_RESPONSE)
        )
        save_config(KeelConfig(api_url=API))
        config = anonymous_start()
        assert config.api_key == GRANT_RESPONSE["access_token"]
        assert config.refresh_token == GRANT_RESPONSE["refresh_token"]
        assert config.anon_org_id == "org_anon_1"
        # Persisted, not just in memory.
        assert load_config().anon_org_id == "org_anon_1"
        # One-line notice on stderr (spec R4), stdout untouched.
        captured = capsys.readouterr()
        assert "Running anonymously" in captured.err
        assert captured.out == ""

    @respx.mock
    def test_kill_switch_503_degrades_instructively(self):
        respx.post(f"{API}/v1/auth/anonymous").mock(
            return_value=httpx.Response(
                503,
                json={
                    "detail": {
                        "error": "anon_tier_disabled",
                        "message": "Anonymous access is not enabled on this environment.",
                    }
                },
            )
        )
        save_config(KeelConfig(api_url=API))
        with pytest.raises(AuthError, match="not enabled"):
            anonymous_start()
        assert load_config().api_key is None  # nothing stored

    def test_refuses_when_credentials_exist(self):
        save_config(KeelConfig(api_key="existing", api_url=API))
        with pytest.raises(AuthError, match="already exist"):
            anonymous_start()


class TestInstantStart:
    @respx.mock
    def test_first_command_auto_starts_anonymously(self, cli_surface, capsys):
        respx.post(f"{API}/v1/auth/anonymous").mock(
            return_value=httpx.Response(201, json=GRANT_RESPONSE)
        )
        target = respx.get(f"{API}/v1/strategies").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        save_config(KeelConfig(api_url=API))
        client = KeelClient()
        try:
            client.get("/v1/strategies")
        finally:
            client.close()
        auth = target.calls.last.request.headers["authorization"]
        assert auth == f"Bearer {GRANT_RESPONSE['access_token']}"
        assert "Running anonymously" in capsys.readouterr().err

    @respx.mock
    def test_never_auto_anon_when_credentials_exist(self, cli_surface):
        grant = respx.post(f"{API}/v1/auth/anonymous")
        respx.get(f"{API}/v1/strategies").mock(return_value=httpx.Response(200, json={"data": []}))
        client = KeelClient(config=KeelConfig(api_key="real_key", api_url=API))
        try:
            client.get("/v1/strategies")
        finally:
            client.close()
        assert not grant.called

    def test_disabled_by_env_falls_back_to_auth_error(self, cli_surface, monkeypatch):
        monkeypatch.setenv("KEEL_ANON_AUTO", "0")
        save_config(KeelConfig(api_url=API))
        client = KeelClient()
        try:
            with pytest.raises(AuthError, match="Not authenticated"):
                client.get("/v1/strategies")
        finally:
            client.close()

    def test_non_cli_surface_keeps_plain_auth_error(self):
        save_config(KeelConfig(api_url=API))
        client = KeelClient()
        try:
            with pytest.raises(AuthError, match="keel_auth_login"):
                client.get("/v1/strategies")
        finally:
            client.close()

    @respx.mock
    def test_grant_failure_surfaces_both_messages(self, cli_surface):
        respx.post(f"{API}/v1/auth/anonymous").mock(
            return_value=httpx.Response(
                429,
                json={"detail": {"error": "anon_rate_limited", "message": "limit 3"}},
            )
        )
        save_config(KeelConfig(api_url=API))
        client = KeelClient()
        try:
            with pytest.raises(AuthError, match="anonymous start was\n?.*unavailable"):
                client.get("/v1/strategies")
        finally:
            client.close()


def _mock_cl8_lists(*, account_strategies=0, anon_strategies=1, anon_backtests=0):
    """Spec 09 CL-8: maybe_claim_after_login now reads the anon workspace
    counts (anon bearer) and the account's has-work signal (real bearer)
    before claiming. Empty account → the original silent-claim path."""

    def strategies(request):
        if request.headers.get("authorization") == "Bearer fresh_anon_access" or (
            request.headers.get("authorization") == "Bearer fresh_anon"
        ):
            return httpx.Response(
                200, json={"data": [{"id": f"s{i}"} for i in range(anon_strategies)]}
            )
        return httpx.Response(
            200, json={"data": [{"id": f"a{i}"} for i in range(account_strategies)]}
        )

    respx.get(f"{API}/v1/strategies").mock(side_effect=strategies)
    respx.get(f"{API}/v1/backtests").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": f"b{i}"} for i in range(anon_backtests)]}
        )
    )


class TestAutoClaim:
    @respx.mock
    def test_claim_after_login_reowns_and_clears_marker(self, capsys):
        prior = KeelConfig(
            api_key="anon_access",
            api_url=API,
            refresh_token="krt_anon_refresh",
            anon_org_id="org_anon_1",
        )
        # Post-login state: real tokens, marker still present.
        save_config(
            KeelConfig(
                api_key="real_access",
                api_url=API,
                refresh_token="krt_real",
                anon_org_id="org_anon_1",
            )
        )
        respx.post(f"{API}/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "fresh_anon_access",
                    "refresh_token": "krt_rotated",
                    "expires_in": 3600,
                },
            )
        )
        claim = respx.post(f"{API}/v1/orgs/claim").mock(
            return_value=httpx.Response(
                200,
                json={
                    "org_id": "org_anon_1",
                    "plan": "free",
                    "already_claimed": False,
                    "message": "Workspace claimed.",
                },
            )
        )
        _mock_cl8_lists()
        result = maybe_claim_after_login(prior)
        assert result["plan"] == "free"
        # The claim used the NEW credentials + the FRESH anon token.
        req = claim.calls.last.request
        assert req.headers["authorization"] == "Bearer real_access"
        assert b"fresh_anon_access" in req.content
        assert load_config().anon_org_id is None
        assert "claimed" in capsys.readouterr().err.lower()

    @respx.mock
    def test_expired_anon_org_degrades_to_notice(self, capsys):
        prior = KeelConfig(
            api_key="anon_access",
            api_url=API,
            refresh_token="krt_anon_refresh",
            anon_org_id="org_anon_1",
        )
        save_config(KeelConfig(api_key="real_access", api_url=API, anon_org_id="org_anon_1"))
        respx.post(f"{API}/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(401, json={"detail": "expired"})
        )
        assert maybe_claim_after_login(prior) is None
        assert load_config().anon_org_id is None
        assert "could not be claimed" in capsys.readouterr().err

    def test_no_marker_is_a_noop(self):
        assert maybe_claim_after_login(KeelConfig(api_key="x", api_url=API)) is None

    @respx.mock
    def test_browser_login_wires_auto_claim(self):
        """keel auth login end-to-end: OAuth result stored, claim invoked,
        identity enriched with the claim outcome."""
        from types import SimpleNamespace

        import keel.auth as auth_mod

        save_config(
            KeelConfig(
                api_key="anon_access",
                api_url=API,
                refresh_token="krt_anon_refresh",
                anon_org_id="org_anon_1",
            )
        )
        fake_result = SimpleNamespace(
            access_token="real_access",
            refresh_token="krt_real",
            expires_in=3600,
            scope="strategy.read",
        )
        respx.post(f"{API}/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(
                200, json={"access_token": "fresh_anon", "refresh_token": "r", "expires_in": 60}
            )
        )
        respx.post(f"{API}/v1/orgs/claim").mock(
            return_value=httpx.Response(
                200,
                json={
                    "org_id": "org_anon_1",
                    "plan": "free",
                    "already_claimed": False,
                    "message": "claimed",
                },
            )
        )
        respx.get(f"{API}/v1/me").mock(
            return_value=httpx.Response(
                200, json={"principal": {"id": "prn_real"}, "org": {"plan": "free"}}
            )
        )
        _mock_cl8_lists()
        with patch.object(auth_mod, "_default_client_name", return_value="Keel CLI/test"):
            with patch("keel.browser_login.run", return_value=fake_result):
                identity = auth_mod.browser_login(api_url=API)
        assert identity["claimed_anon_org"]["plan"] == "free"
        cfg = load_config()
        assert cfg.api_key == "real_access"
        assert cfg.anon_org_id is None
