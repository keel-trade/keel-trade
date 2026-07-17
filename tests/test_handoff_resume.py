"""Round-trip resumption + errors-instruct tests — spec 03 R6/R7 (M3.7).

R6: the handoff envelope's ``resume`` must be actionable by an agent
without the user returning to the tab:

  * ``resume.verify_call`` is executable EXACTLY as written — the named
    tool exists on the surface that emitted the envelope and the args
    satisfy its schema (pinned for every builder, both execution modes).
  * ``keel_live_deploy`` with ``intent_token`` (preview phase) is a pure
    status poll of ``POST /v1/live/deploy-intents/status`` returning
    ``handoff_state`` (pending | completed | expired).
  * ``keel_live_deploy`` with ``intent_token`` + ``preview=False``
    forwards the token to ``POST /v1/live`` (server-side
    ``handoff_completed`` attribution; telemetry-only).

R7: every deploy-intent failure path surfaced through the SDK names the
next action — expired, tampered/invalid, wrong org — with remediation
that is correct for an AGENT (e.g. the wrong-org 403 must never suggest
the live-scope re-login, which cannot fix it).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import (
    live_scope_handoff,
    maybe_quota_handoff,
    unlinked_account_handoff,
)


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


INTENT_TOKEN = "eyJ.fake-intent.token"


# ─── R6: the intent-status poll (keel_live_deploy + intent_token) ────────


def test_poll_pending_returns_handoff_state_and_repoll_action():
    client = MagicMock()
    client.post.return_value = {
        "status": "pending",
        "intent_id": "din_1",
        "strategy_id": "strat_p",
        "expires_at": "2026-07-17T01:00:00+00:00",
        "suggested_config": {"sizing_usd": 500},
    }

    env = (
        get("keel_live_deploy")
        .handler({"strategy_id": "strat_p", "intent_token": INTENT_TOKEN}, _ctx(client))
        .to_envelope()
    )

    state = env["handoff_state"]
    assert state["status"] == "pending"
    assert state["intent_id"] == "din_1"
    assert state["strategy_id"] == "strat_p"
    assert state["expires_at"] == "2026-07-17T01:00:00+00:00"
    # Next action: re-poll this same call.
    assert env["next_action"]["tool"] == "keel_live_deploy"
    assert env["next_action"]["args"] == {
        "strategy_id": "strat_p",
        "intent_token": INTENT_TOKEN,
    }


def test_poll_completed_returns_deployment_and_monitor_action():
    client = MagicMock()
    client.post.return_value = {
        "status": "completed",
        "intent_id": "din_1",
        "strategy_id": "strat_c",
        "expires_at": "2026-07-17T01:00:00+00:00",
        "deployment_id": "dep_42",
        "deployment_status": "LIVE",
    }

    result = get("keel_live_deploy").handler(
        {"strategy_id": "strat_c", "intent_token": INTENT_TOKEN}, _ctx(client)
    )
    env = result.to_envelope()

    state = env["handoff_state"]
    assert state["status"] == "completed"
    assert state["deployment_id"] == "dep_42"
    assert state["deployment_status"] == "LIVE"
    # The agent lands on the live deployment without any browser return.
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_42"
    assert env["next_action"]["tool"] == "keel_live_monitor"
    assert env["next_action"]["args"] == {"deployment_id": "dep_42"}


def test_poll_expired_carries_server_remediation_and_fresh_link_action():
    client = MagicMock()
    remediation = (
        "This deploy link has expired (links live for up to 1 hour). "
        "Ask your agent for a fresh link, or open the strategy in the Keel "
        "app and deploy from there."
    )
    client.post.return_value = {
        "status": "expired",
        "intent_id": "din_1",
        "strategy_id": "strat_e",
        "expires_at": "2026-07-17T00:00:00+00:00",
        "remediation": remediation,
    }

    env = (
        get("keel_live_deploy")
        .handler({"strategy_id": "strat_e", "intent_token": INTENT_TOKEN}, _ctx(client))
        .to_envelope()
    )

    state = env["handoff_state"]
    assert state["status"] == "expired"
    assert state["remediation"] == remediation  # server text, verbatim (R7)
    # Next action mints a fresh link.
    assert env["next_action"]["tool"] == "keel_live_deploy"
    assert env["next_action"]["args"] == {"strategy_id": "strat_e", "preview": True}


def test_poll_is_a_pure_status_read():
    """The poll never previews, never mints, never touches accounts —
    exactly one POST to the status endpoint."""
    client = MagicMock()
    client.post.return_value = {"status": "pending", "strategy_id": "strat_p"}

    get("keel_live_deploy").handler(
        {"strategy_id": "strat_p", "intent_token": INTENT_TOKEN}, _ctx(client)
    )

    client.post.assert_called_once_with(
        "/v1/live/deploy-intents/status", json={"intent_token": INTENT_TOKEN}
    )
    client.get.assert_not_called()


def test_poll_unknown_status_shape_raises_instead_of_guessing():
    client = MagicMock()
    client.post.return_value = {"status": "sideways"}

    with pytest.raises(KeelError) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_x", "intent_token": INTENT_TOKEN}, _ctx(client)
        )
    assert exc.value.error_code == "deploy_intent_status_unexpected"
    assert "keel_doctor" in (exc.value.suggestion or "")


# ─── R7: poll failure paths instruct the agent correctly ─────────────────


def test_poll_tampered_token_instructs_fresh_mint():
    """400 (tampered/truncated/not-a-deploy-link) → remediation names the
    fresh-mint action; the server's own remediation text survives."""
    client = MagicMock()
    body = (
        '{"detail": "The deploy link failed verification. This link is not '
        "valid — it may have been truncated or altered. Copy the full link "
        'from your agent, or ask it to mint a fresh one."}'
    )
    client.post.side_effect = translate_http_error(400, body)

    with pytest.raises(KeelError) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_t", "intent_token": "tampered.token"}, _ctx(client)
        )

    assert exc.value.error_code == "deploy_intent_invalid"
    assert "failed verification" in str(exc.value)  # server detail, not a JSON dump
    assert "preview=True" in exc.value.suggestion
    assert "keel_live_deploy" in exc.value.suggestion


def test_poll_wrong_org_names_the_actual_fix_not_scope_relogin():
    """403 on the status endpoint = the token belongs to another org. The
    class-default 're-login with live scope' remediation would mislead."""
    client = MagicMock()
    body = (
        '{"detail": "This deploy link belongs to a different Keel account. '
        "Sign in with the account your agent is connected to (the one that "
        'owns this strategy), then open the link again."}'
    )
    client.post.side_effect = translate_http_error(403, body)

    with pytest.raises(KeelError) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_w", "intent_token": INTENT_TOKEN}, _ctx(client)
        )

    assert exc.value.error_code == "deploy_intent_wrong_org"
    assert "different Keel account" in str(exc.value)
    assert "live scope" not in (exc.value.suggestion or "")
    assert "Mint a fresh link" in exc.value.suggestion


def test_http_400_translation_extracts_detail():
    """R7 groundwork: a 400's message is the server's remediation text,
    never an `HTTP 400: {json}` dump."""
    err = translate_http_error(400, '{"detail": "This is not a deploy link token."}')
    assert str(err) == "This is not a deploy link token."
    assert err.error_code == "usage_error"


# ─── R6: deploy forwards the intent token (handoff_completed leg) ────────


def test_actual_deploy_forwards_intent_token(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *"},  # preview
        {},  # deploy-intent mint during preview (no handoff_url)
        {"deployment_id": "dep_9"},  # POST /v1/live
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_d", "account_id": "acct_1", "preview": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    result = tool.handler(
        {
            "strategy_id": "strat_d",
            "account_id": "acct_1",
            "preview": False,
            "confirmation_token": token,
            "intent_token": INTENT_TOKEN,
        },
        _ctx(client),
    )

    live_call = client.post.call_args_list[-1]
    assert live_call.args[0] == "/v1/live"
    assert live_call.kwargs["json"]["intent_token"] == INTENT_TOKEN
    assert result.to_envelope()["run_id"] == "dep_9"


# ─── R6: every builder's verify_call is executable exactly as written ────


def _assert_executable(call: dict, *, hosted: bool = False) -> None:
    """The instruction must work if the agent follows the string: the tool
    exists in the registry (and on the emitting surface), required args are
    present, and no arg falls outside the tool's schema."""
    assert isinstance(call, dict) and call.get("tool"), f"not a verify_call: {call!r}"
    tool = get(call["tool"])
    assert tool is not None, f"verify_call names unknown tool {call['tool']!r}"
    if hosted:
        assert not tool.local_only, (
            f"verify_call names {call['tool']!r}, which is local_only and not "
            "registered on hosted servers — not executable as written"
        )
    args = call.get("args", {})
    schema = tool.input_schema
    properties = set(schema.get("properties", {}))
    required = set(schema.get("required", []))
    assert set(args) <= properties, f"args {set(args) - properties} not in {call['tool']} schema"
    assert required <= set(args), f"missing required args {required - set(args)}"
    if call.get("then_retry"):
        _assert_executable(call["then_retry"], hosted=hosted)


def _quota_envelope():
    err = translate_http_error(
        403,
        '{"detail": "Insufficient entitlements", '
        '"reasons": ["entitlement:insufficient:backtest_runs:limit=30:current=30"]}',
    )
    return maybe_quota_handoff(
        err,
        blocked_action="backtest_run",
        retry_call={"tool": "keel_backtest_run", "args": {"strategy_id": "strat_q"}},
    )


def _scope_envelope():
    err = translate_http_error(
        403,
        '{"detail": "Forbidden", "reasons": ["credential:scope_denied:runner.create"]}',
    )
    return live_scope_handoff(
        err,
        blocked_action="live_deploy",
        action_url="https://app.usekeel.io/strategies/strat_s",
        retry_call={
            "tool": "keel_live_deploy",
            "args": {"strategy_id": "strat_s", "preview": True},
        },
    )


def _unlinked_envelope(with_intent: bool):
    client = MagicMock()
    client.post.return_value = (
        {
            "handoff_url": "https://app.usekeel.io/deploy?intent=tokX",
            "intent_token": "tokX",
            "suggested_config": {"sizing_usd": None, "sizing_basis": None},
        }
        if with_intent
        else {}
    )
    return unlinked_account_handoff(
        blocked_action="live_deploy", strategy_id="strat_u", ctx=_ctx(client)
    )


@pytest.mark.parametrize(
    "build",
    [
        _quota_envelope,
        _scope_envelope,
        lambda: _unlinked_envelope(True),
        lambda: _unlinked_envelope(False),
    ],
    ids=["quota", "scope-local", "unlinked-with-intent", "unlinked-no-intent"],
)
def test_verify_call_is_executable_exactly_as_written(build):
    envelope = build().to_envelope()
    _assert_executable(envelope["resume"]["verify_call"])


def test_unlinked_with_intent_verify_call_is_the_poll():
    env = _unlinked_envelope(True).to_envelope()
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_live_deploy"
    assert verify["args"] == {"strategy_id": "strat_u", "intent_token": "tokX"}
    assert env["resume"]["token"] == "tokX"


def test_unlinked_without_intent_falls_back_to_accounts_list():
    env = _unlinked_envelope(False).to_envelope()
    assert env["resume"]["verify_call"]["tool"] == "keel_accounts_list"
    assert "token" not in env["resume"]


# ─── R6: hosted surfaces get a verify_call that exists there ─────────────


def test_scope_wall_hosted_verify_call_is_executable_on_hosted(monkeypatch):
    """keel_auth_login is local_only — a hosted envelope must not name it.
    On hosted, re-auth is the client's OAuth flow; the verify_call is the
    retry itself with the client re-auth named in its reason."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")

    env = _scope_envelope().to_envelope()
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_live_deploy"
    assert verify["args"] == {"strategy_id": "strat_s", "preview": True}
    assert "re-authenticate" in verify["reason"].lower()
    _assert_executable(verify, hosted=True)
    # The talking points explain the client re-auth, not the local tool.
    joined = " ".join(env["talking_points"])
    assert "keel_auth_login" not in joined
    assert "re-authenticate" in joined.lower()


def test_scope_wall_local_verify_call_stays_auth_login(monkeypatch):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "local")
    env = _scope_envelope().to_envelope()
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert verify["args"] == {"scope": "live"}
    assert verify["then_retry"]["tool"] == "keel_live_deploy"
    _assert_executable(verify)
