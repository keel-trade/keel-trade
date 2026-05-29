"""Smoke tests for the accounts + sharing + audit auxiliary outcome tools.

Covers:
- `keel_accounts_list` (list + detail modes)
- `keel_share_create` (strategy / backtest / explicit override)
- `keel_audit_list_last` (read-only event list)

Each test mocks `KeelClient` via `unittest.mock.patch` so no network
traffic happens.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from keel.tools.outcomes._base import ToolContext

# Import for side-effect registration — same pattern as test_outcomes_backtest.
from keel.tools.outcomes import accounts as _accounts_mod  # noqa: F401
from keel.tools.outcomes import share_create as _share_mod  # noqa: F401
from keel.tools.outcomes import audit as _audit_mod  # noqa: F401
from keel.tools.outcomes import OUTCOMES


# ─── shared fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ctx():
    return ToolContext(
        is_tty=False,
        app_url="https://app.usekeel.io",
        share_url_root="https://usekeel.io/share",
    )


# ─── keel_accounts_list ──────────────────────────────────────────────────


def test_accounts_list_returns_list(ctx):
    """No account_id -> list mode -> envelope.accounts populated."""
    api_payload = {
        "items": [
            {
                "account_id": "acc_abc",
                "wallet_address": "0xabc",
                "label": "main",
                "status": "active",
                "account_mode": "unified",
            },
            {
                "account_id": "acc_def",
                "wallet_address": "0xdef",
                "label": None,
                "status": "pending",
                "account_mode": "cross",
            },
        ],
        "next_cursor": "cur_next",
    }

    with patch("keel.client.KeelClient.get", return_value=api_payload) as mock_get:
        tool = OUTCOMES["keel_accounts_list"]
        result = tool.handler({}, ctx)

    mock_get.assert_called_once()
    # Path is the first positional; limit/cursor go through as kwargs.
    assert mock_get.call_args.args[0] == "/v1/accounts"

    env = result.to_envelope()
    assert env["hero_url"] == "https://app.usekeel.io/accounts"
    assert env["share_url"] is None
    assert env["accounts"][0]["account_id"] == "acc_abc"
    assert env["accounts"][1]["account_id"] == "acc_def"
    assert env["next_cursor"] == "cur_next"


def test_accounts_list_with_id_returns_detail(ctx):
    """account_id arg -> detail mode -> envelope.account populated."""
    api_payload = {
        "account_id": "acc_abc",
        "wallet_address": "0xabc",
        "label": "main",
        "status": "active",
        "account_mode": "unified",
        "agent_address": "0xagent",
        "expires_at": "2027-01-01T00:00:00Z",
    }

    with patch("keel.client.KeelClient.get", return_value=api_payload) as mock_get:
        tool = OUTCOMES["keel_accounts_list"]
        result = tool.handler({"account_id": "acc_abc"}, ctx)

    mock_get.assert_called_once_with("/v1/accounts/acc_abc")

    env = result.to_envelope()
    assert env["hero_url"] == "https://app.usekeel.io/accounts/acc_abc"
    assert env["share_url"] is None
    assert env["account"]["account_id"] == "acc_abc"
    assert env["account"]["agent_address"] == "0xagent"


# ─── keel_share_create ───────────────────────────────────────────────────


def test_share_create_strategy_returns_share_url(ctx):
    """str_* prefix -> POST /v1/strategies/{id}/share-links + share_url populated."""
    create_payload = {
        "share_id": "shr_abc123",
        "share_type": "strategy",
        "include_source": False,
        "permission": "view",
        "expires_at": None,
    }
    me_payload = {"referral_code": "zach42"}

    def fake_get(path, **_):
        if path == "/v1/me":
            return me_payload
        raise AssertionError(f"unexpected GET {path}")

    with patch("keel.client.KeelClient.post", return_value=create_payload) as mock_post, \
            patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_share_create"]
        result = tool.handler({"target_id": "str_xyz"}, ctx)

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "/v1/strategies/str_xyz/share-links"
    body = mock_post.call_args.kwargs["json"]
    assert body["permission"] == "view"
    assert body["include_source"] is False
    # We auto-pin latest backtest so the share card has metrics.
    assert body["pin_latest_backtest"] is True

    env = result.to_envelope()
    assert env["run_id"] == "shr_abc123"
    assert env["hero_url"] == "https://app.usekeel.io/share-links"
    # share_url IS populated for this tool — the one exception.
    assert env["share_url"] == "https://usekeel.io/share/shr_abc123?ref=zach42"
    assert env["share_id"] == "shr_abc123"
    assert env["share_type"] == "strategy"
    assert env["permission"] == "view"
    assert env["include_source"] is False


def test_share_create_backtest_target_type_inferred(ctx):
    """btr_* prefix -> POST /v1/backtests/{id}/share-link."""
    create_payload = {
        "share_id": "shr_btr456",
        "share_type": "backtest",
        "include_source": True,
        "permission": "view",
        "expires_at": None,
    }

    def fake_get(path, **_):
        # No referral code -> share_url shouldn't carry ?ref=
        return {}

    with patch("keel.client.KeelClient.post", return_value=create_payload) as mock_post, \
            patch("keel.client.KeelClient.get", side_effect=fake_get):
        tool = OUTCOMES["keel_share_create"]
        result = tool.handler(
            {"target_id": "btr_run789", "include_source": True}, ctx
        )

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "/v1/backtests/btr_run789/share-link"
    body = mock_post.call_args.kwargs["json"]
    assert body["include_source"] is True

    env = result.to_envelope()
    # No referral code -> bare share URL.
    assert env["share_url"] == "https://usekeel.io/share/shr_btr456"
    assert env["share_type"] == "backtest"
    assert env["include_source"] is True


def test_share_create_target_type_override(ctx):
    """target_type='backtest' overrides a non-standard prefix."""
    create_payload = {
        "share_id": "shr_zzz",
        "share_type": "backtest",
        "include_source": False,
        "permission": "view",
    }

    with patch("keel.client.KeelClient.post", return_value=create_payload) as mock_post, \
            patch("keel.client.KeelClient.get", return_value={}):
        tool = OUTCOMES["keel_share_create"]
        result = tool.handler(
            {"target_id": "custom_id_99", "target_type": "backtest"}, ctx
        )

    # Explicit override should route to /v1/backtests/{id}/share-link
    assert mock_post.call_args.args[0] == "/v1/backtests/custom_id_99/share-link"

    env = result.to_envelope()
    assert env["share_url"].startswith("https://usekeel.io/share/shr_zzz")


# ─── keel_audit_list_last ────────────────────────────────────────────────


def test_audit_list_last_returns_events(ctx):
    """GET /v1/audit -> normalized events list."""
    api_payload = {
        "items": [
            {
                "id": "evt_001",
                "org_id": "org_x",
                "actor_principal_id": "prn_y",
                "action": "backtest.run",
                "decision": "ALLOW",
                "metadata": {
                    "args": {"strategy_id": "str_z", "wait": False},
                    "result_ref": "btr_run001",
                },
                "created_at": "2026-05-18T12:34:56Z",
            },
            {
                "id": "evt_002",
                "org_id": "org_x",
                "actor_principal_id": "prn_y",
                "action": "strategy.update",
                "decision": "DENY",
                "metadata": {},
                "created_at": "2026-05-18T12:35:00Z",
            },
        ],
        "next_cursor": None,
    }

    with patch("keel.client.KeelClient.get", return_value=api_payload) as mock_get:
        tool = OUTCOMES["keel_audit_list_last"]
        result = tool.handler({"n": 5}, ctx)

    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == "/v1/audit"
    assert mock_get.call_args.kwargs == {"limit": 5}

    env = result.to_envelope()
    assert env["hero_url"] == "https://app.usekeel.io/audit"
    assert env["share_url"] is None
    assert len(env["events"]) == 2
    e0 = env["events"][0]
    assert e0["event_id"] == "evt_001"
    assert e0["tool"] == "backtest.run"
    assert e0["decision"] == "permit"
    assert e0["ts"] == "2026-05-18T12:34:56Z"
    assert e0["args"] == {"strategy_id": "str_z", "wait": False}
    assert e0["result_ref"] == "btr_run001"
    assert e0["metadata_complete"] is True

    # Empty API metadata is a real production shape. Keep the event useful,
    # but do not imply args/result_ref are known or replay-safe.
    e1 = env["events"][1]
    assert e1["decision"] == "deny"
    assert e1["args"] == {}
    assert e1["result_ref"] is None
    assert e1["metadata_complete"] is False
    assert "best-effort" in env["metadata_note"]


def test_audit_replay_is_not_registered():
    """Replay-safe retry is a future design, not a shipped stub."""
    assert "keel_audit_replay" not in OUTCOMES
