"""Spec 09 CL-8/CL-9 — existing-account confirm + post-claim org context.

Fresh/empty accounts claim silently (Option-C default flip); accounts
that already have work confirm first — explicit decision, TTY prompt, or
a deferred `pending_claim` the next call resolves without OAuth. A
successful claim pins the claimed org via X-Org-Id (CL-9) so the CLI
continues on the work it just claimed.
"""

from __future__ import annotations

import httpx
import respx
from keel.anon import maybe_claim_after_login, resolve_pending_claim
from keel.client import KeelClient
from keel.config import KeelConfig, load_config, save_config


API = "https://api.test.io"


def _prior() -> KeelConfig:
    return KeelConfig(
        api_key="anon_access",
        api_url=API,
        refresh_token="krt_anon",
        anon_org_id="org_anon_1",
    )


def _post_login_config() -> None:
    save_config(
        KeelConfig(
            api_key="real_access", api_url=API, refresh_token="krt_real", anon_org_id="org_anon_1"
        )
    )


def _mock_refresh():
    respx.post(f"{API}/v1/auth/oauth/refresh").mock(
        return_value=httpx.Response(
            200, json={"access_token": "fresh_anon", "refresh_token": "r", "expires_in": 60}
        )
    )


def _mock_lists(*, anon_strategies=1, anon_backtests=2, account_strategies=0):
    """Route the two /v1/strategies readers by bearer: the anon-token count
    read and the real-credential has-work read."""

    def strategies(request):
        if request.headers.get("authorization") == "Bearer fresh_anon":
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


def _mock_claim():
    return respx.post(f"{API}/v1/orgs/claim").mock(
        return_value=httpx.Response(
            200,
            json={
                "org_id": "org_anon_1",
                "plan": "free",
                "already_claimed": False,
                "message": "Workspace claimed — 1 strategy and 2 backtests now belong to your account (plan: free).",
                "strategies_claimed": 1,
                "backtests_claimed": 2,
            },
        )
    )


class TestFreshAccountSilentClaim:
    @respx.mock
    def test_claims_silently_with_option_c_default(self, capsys):
        _post_login_config()
        _mock_refresh()
        _mock_lists(account_strategies=0)
        claim = _mock_claim()

        result = maybe_claim_after_login(_prior())
        assert result["plan"] == "free"
        assert result["strategies_claimed"] == 1
        # Fresh path: no set_default in the body → wire-default Option-C.
        assert b"set_default" not in claim.calls.last.request.content
        # CL-9: the client pins the claimed org for continuation.
        assert load_config().active_org_id == "org_anon_1"
        assert load_config().anon_org_id is None
        assert load_config().pending_claim is None


class TestExistingAccountConfirm:
    @respx.mock
    def test_explicit_yes_attaches_preserving_default(self):
        _post_login_config()
        _mock_refresh()
        _mock_lists(account_strategies=3)
        claim = _mock_claim()

        result = maybe_claim_after_login(_prior(), attach_decision=True)
        assert result["plan"] == "free"
        assert b'"set_default":false' in claim.calls.last.request.content.replace(b" ", b"")
        assert load_config().active_org_id == "org_anon_1"

    @respx.mock
    def test_no_decision_defers_with_pending(self, capsys):
        _post_login_config()
        _mock_refresh()
        _mock_lists(account_strategies=3)
        claim = _mock_claim()

        result = maybe_claim_after_login(_prior())
        assert "pending_claim" in result
        assert claim.calls.call_count == 0, "no claim may fire without the user's decision"
        pending = load_config().pending_claim
        assert pending["anon_org_id"] == "org_anon_1"
        assert pending["counts"] == {"strategies": 1, "backtests": 2}
        assert pending["declined"] is False
        assert load_config().anon_org_id is None, "marker cleared; pending carries the material"

    @respx.mock
    def test_confirm_callback_no_keeps_claimable(self, capsys):
        _post_login_config()
        _mock_refresh()
        _mock_lists(account_strategies=3)
        claim = _mock_claim()

        seen = {}

        def confirm(counts):
            seen["counts"] = counts
            return False

        result = maybe_claim_after_login(_prior(), confirm=confirm)
        assert result["pending_claim_declined"] is True
        assert claim.calls.call_count == 0
        assert seen["counts"] == {"strategies": 1, "backtests": 2}
        assert load_config().pending_claim["declined"] is True
        assert "stays claimable" in capsys.readouterr().err

    @respx.mock
    def test_has_work_check_failure_resolves_toward_confirm(self):
        """Uncertainty must never silently default-flip an existing account."""
        _post_login_config()
        _mock_refresh()

        def strategies(request):
            if request.headers.get("authorization") == "Bearer fresh_anon":
                return httpx.Response(200, json={"data": [{"id": "s0"}]})
            return httpx.Response(500, json={"detail": "boom"})

        respx.get(f"{API}/v1/strategies").mock(side_effect=strategies)
        respx.get(f"{API}/v1/backtests").mock(return_value=httpx.Response(200, json={"data": []}))
        claim = _mock_claim()

        result = maybe_claim_after_login(_prior())
        assert "pending_claim" in result
        assert claim.calls.call_count == 0


class TestResolvePendingClaim:
    def _store_pending(self, declined=False):
        save_config(
            KeelConfig(
                api_key="real_access",
                api_url=API,
                pending_claim={
                    "anon_org_id": "org_anon_1",
                    "refresh_token": "krt_anon",
                    "api_url": API,
                    "counts": {"strategies": 1, "backtests": 2},
                    "org_expires_at": "2026-08-28T00:00:00+00:00",
                    "declined": declined,
                },
            )
        )

    @respx.mock
    def test_attach_true_claims_without_oauth(self, capsys):
        self._store_pending()
        _mock_refresh()
        claim = _mock_claim()

        result = resolve_pending_claim(True)
        assert result["plan"] == "free"
        assert b'"set_default":false' in claim.calls.last.request.content.replace(b" ", b"")
        assert load_config().pending_claim is None
        assert load_config().active_org_id == "org_anon_1"

    def test_attach_false_keeps_claimable(self, capsys):
        self._store_pending()
        result = resolve_pending_claim(False)
        assert result["pending_claim_declined"] is True
        assert load_config().pending_claim["declined"] is True
        assert "claimable until" in capsys.readouterr().err

    @respx.mock
    def test_expired_pending_clears(self, capsys):
        self._store_pending()
        respx.post(f"{API}/v1/auth/oauth/refresh").mock(
            return_value=httpx.Response(401, json={"detail": "expired"})
        )
        result = resolve_pending_claim(True)
        assert result["pending_claim_expired"] is True
        assert load_config().pending_claim is None

    def test_nothing_pending_is_none(self):
        save_config(KeelConfig(api_key="real_access", api_url=API))
        assert resolve_pending_claim(True) is None


class TestOrgContextHeader:
    def test_client_sends_x_org_id_when_set(self):
        save_config(KeelConfig(api_key="k", api_url=API, active_org_id="org_claimed_1"))
        client = KeelClient()
        try:
            assert client._client.headers["X-Org-Id"] == "org_claimed_1"
        finally:
            client.close()

    def test_no_header_without_context(self):
        save_config(KeelConfig(api_key="k", api_url=API))
        client = KeelClient()
        try:
            assert "X-Org-Id" not in client._client.headers
        finally:
            client.close()

    def test_logout_clears_context_and_pending(self):
        from keel.token_store import clear_oauth_tokens

        save_config(
            KeelConfig(
                api_key="k",
                api_url=API,
                active_org_id="org_x",
                pending_claim={"anon_org_id": "o"},
            )
        )
        clear_oauth_tokens()
        cfg = load_config()
        assert cfg.active_org_id is None
        assert cfg.pending_claim is None


class TestPendingRedaction:
    """Review finding (spec 09 §1): the anon refresh token is a bearer
    capability — it must never ride a surfaced payload (tool results,
    status bodies, login summaries land in agent transcripts and logs).
    The stored record keeps the material; every surfaced projection is
    token-free."""

    @respx.mock
    def test_defer_result_carries_no_claim_material(self):
        _post_login_config()
        _mock_refresh()
        _mock_lists(account_strategies=3)
        _mock_claim()

        result = maybe_claim_after_login(_prior())
        surfaced = result["pending_claim"]
        assert "refresh_token" not in surfaced
        assert "api_url" not in surfaced
        assert surfaced["counts"] == {"strategies": 1, "backtests": 2}
        assert surfaced["declined"] is False
        # The stored record still carries the claim material for the retry.
        assert load_config().pending_claim["refresh_token"] == "krt_anon"

    def test_public_projection_is_token_free(self):
        from keel.anon import pending_claim_public

        pending = {
            "anon_org_id": "org_anon_1",
            "refresh_token": "krt_anon",
            "api_url": API,
            "counts": {"strategies": 1, "backtests": 2},
            "org_expires_at": "2026-08-28T00:00:00+00:00",
            "declined": False,
        }
        public = pending_claim_public(pending)
        assert set(public) == {"anon_org_id", "counts", "org_expires_at", "declined"}
        assert pending_claim_public(None) is None
