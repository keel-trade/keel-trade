"""Spec 09 CL-1/CL-2/CL-3 — anonymous walls resolve to sign-in.

While the session runs on an anonymous grant, every human-required wall
must claim-before-handoff: `resume.verify_call` is `keel_auth_login`,
no `action_url`, and NEVER a deploy/billing/app URL that references
anon-org resources (the future account will not own them). Plus the
CL-3c <48h expiry notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from keel.anon import maybe_print_expiry_notice
from keel.config import KeelConfig, load_config, save_config
from keel.errors import EntitlementError
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import (
    ANON_SIGNIN_TALKING_POINT,
    HandoffRequired,
    deploy_web_handoff,
    live_scope_handoff,
    maybe_quota_handoff,
    mint_deploy_intent,
    unlinked_account_handoff,
)


API = "https://api.test.io"


def _anon_config() -> None:
    save_config(
        KeelConfig(
            api_key="anon_access",
            api_url=API,
            refresh_token="krt_anon",
            anon_org_id="org_anon_1",
        )
    )


def _signed_in_config() -> None:
    save_config(KeelConfig(api_key="real_access", api_url=API))


def _ctx() -> ToolContext:
    client = MagicMock()
    client.post.side_effect = AssertionError(
        "no API call may be made while building an anonymous wall (CL-2)"
    )
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


def _quota_error() -> EntitlementError:
    e = EntitlementError(
        "Plan limit: backtest_runs",
        input={
            "unit": "backtest_runs",
            "unit_label": "backtest runs",
            "limit": 15,
            "current": 15,
            "billing_url": "https://app.usekeel.io/settings?tab=billing",
        },
    )
    e.recovery_tool = None
    return e


def _assert_anon_shape(h: HandoffRequired, *, blocked_action: str) -> None:
    envelope = h.to_envelope()
    assert envelope["blocked_action"] == blocked_action
    assert "action_url" not in envelope, "anon walls carry no URL (CL-1)"
    verify = envelope["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert "then_retry" in verify
    assert any(ANON_SIGNIN_TALKING_POINT in tp for tp in envelope["talking_points"])
    flat = repr(envelope)
    assert "/deploy/" not in flat, "no deploy URLs while anon (CL-2)"
    assert "billing" not in flat, "no billing URLs while anon (CL-1)"


class TestAnonWalls:
    def test_deploy_wall_resolves_to_signin(self):
        _anon_config()
        h = deploy_web_handoff(strategy_id="str_1", ctx=_ctx())
        _assert_anon_shape(h, blocked_action="live_deploy")
        assert h.resume["verify_call"]["then_retry"]["args"] == {"strategy_id": "str_1"}

    def test_unlinked_account_wall_resolves_to_signin(self):
        _anon_config()
        h = unlinked_account_handoff(blocked_action="live_deploy", strategy_id="str_1", ctx=_ctx())
        _assert_anon_shape(h, blocked_action="live_deploy")

    def test_scope_wall_resolves_to_signin(self):
        _anon_config()
        h = live_scope_handoff(
            EntitlementError("scope missing"),
            blocked_action="live_control",
            action_url="https://app.usekeel.io/live/dep_1",
            retry_call={"tool": "keel_live_control", "args": {"deployment_id": "dep_1"}},
        )
        _assert_anon_shape(h, blocked_action="live_control")

    def test_quota_wall_uses_signup_framing_with_server_numbers(self):
        _anon_config()
        h = maybe_quota_handoff(
            _quota_error(),
            blocked_action="backtest_run",
            retry_call={"tool": "keel_backtest_run", "args": {"strategy_id": "str_1"}},
        )
        assert h is not None
        _assert_anon_shape(h, blocked_action="backtest_run")
        # Server numbers still ride along — never invented, never dropped.
        assert h.limit_details["limit"] == 15
        assert h.limit_details["current"] == 15

    def test_mint_deploy_intent_short_circuits_while_anon(self):
        _anon_config()
        ctx = _ctx()
        assert mint_deploy_intent(ctx, "str_1") is None
        ctx.api_client.post.assert_not_called()

    def test_good_result_nudge_points_at_signin(self):
        from keel.tools.outcomes._nudge import good_result_nudge

        _anon_config()
        line = good_result_nudge(
            {"metrics": {"good_result": {"sharpe": 1.2}}},
            strategy_id="str_1",
            ctx=_ctx(),
        )
        assert line is not None
        assert "keel auth login" in line
        assert "/strategies/" not in line and "/deploy/" not in line


class TestSignedInControls:
    """Controls — green in both states: non-anon walls keep their URLs."""

    def test_deploy_wall_keeps_url_when_signed_in(self):
        _signed_in_config()
        ctx = ToolContext(api_client=MagicMock(), app_url="https://app.usekeel.io")
        ctx.api_client.post.return_value = {}  # mint returns no handoff_url → owned fallback
        h = deploy_web_handoff(strategy_id="str_1", ctx=ctx)
        assert h.to_envelope()["action_url"] == "https://app.usekeel.io/deploy/str_1"

    def test_quota_wall_keeps_billing_when_signed_in(self):
        _signed_in_config()
        h = maybe_quota_handoff(
            _quota_error(),
            blocked_action="backtest_run",
            retry_call={"tool": "keel_backtest_run", "args": {}},
        )
        assert "billing" in h.to_envelope()["action_url"]


class TestHandoffActionUrlContract:
    def test_none_action_url_is_legal_and_omitted(self):
        h = HandoffRequired(
            "m",
            blocked_action="x",
            reason="r",
            action_url=None,
            talking_points=["a", "doing nothing is fine"],
            resume={"verify_call": {"tool": "keel_auth_login", "args": {}}},
        )
        assert "action_url" not in h.to_envelope()

    def test_empty_action_url_still_raises(self):
        with pytest.raises(ValueError):
            HandoffRequired(
                "m",
                blocked_action="x",
                reason="r",
                action_url="",
                talking_points=["a", "doing nothing is fine"],
                resume={"verify_call": {"tool": "t", "args": {}}},
            )


class TestExpiryNotice:
    def _anon_with_expiry(self, hours_left: float) -> None:
        save_config(
            KeelConfig(
                api_key="anon_access",
                api_url=API,
                anon_org_id="org_anon_1",
                anon_org_expires_at=datetime.now(timezone.utc) + timedelta(hours=hours_left),
            )
        )

    def test_notice_inside_window(self, capsys):
        self._anon_with_expiry(20)
        assert maybe_print_expiry_notice() is True
        err = capsys.readouterr().err
        assert "expires in" in err and "keel auth login" in err
        # Throttle: a second call within the hour stays silent.
        assert maybe_print_expiry_notice() is False
        assert load_config().anon_expiry_notice_at is not None

    def test_reprints_after_an_hour(self, capsys):
        self._anon_with_expiry(20)
        assert maybe_print_expiry_notice() is True
        capsys.readouterr()
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        assert maybe_print_expiry_notice(now=later) is True

    def test_silent_outside_window(self, capsys):
        self._anon_with_expiry(72)
        assert maybe_print_expiry_notice() is False
        assert capsys.readouterr().err == ""

    def test_silent_when_already_expired(self):
        self._anon_with_expiry(-1)
        assert maybe_print_expiry_notice() is False

    def test_silent_without_stored_expiry(self):
        _anon_config()  # pre-spec-09 config shape: marker, no expiry
        assert maybe_print_expiry_notice() is False

    def test_silent_when_signed_in(self):
        _signed_in_config()
        assert maybe_print_expiry_notice() is False
