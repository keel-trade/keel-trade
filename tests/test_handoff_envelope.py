"""Handoff-envelope tests — spec 03 R1 (agent-first-build M3.1).

Schema-validates the shared human-gate response structure from every
wall-hitting tool. The three spec acceptance cases are covered with the
REAL 403 translation path (`translate_http_error`) feeding the adopting
handlers:

  1. quota-exceeded backtest        (`keel_backtest_run`)
  2. live_deploy without live scope (`keel_live_deploy`)
  3. deploy without linked account  (`keel_live_deploy`)

plus the plan-cap wall on `keel_strategy_compose`, the scope wall on
`keel_live_control`, the preview `handoff_url` (spec 03 R2 client half),
and the policy gate: the LISTED profile never mints deploy-intent links.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from keel.errors import EntitlementError, KeelError, translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import (
    HandoffRequired,
    mint_deploy_intent,
)


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


# ─── The envelope schema (spec 03 R1) ────────────────────────────────────
#
# Declared as data; `_assert_valid_handoff_envelope` validates an actual
# envelope against it (types + required keys + value constraints). This
# is the "schema-validated" AC — self-contained, no jsonschema dep.

HANDOFF_ENVELOPE_SCHEMA = {
    "required": {
        "blocked_action": str,
        "reason": str,
        "required_actor": str,
        "action_url": str,
        "talking_points": list,
        "resume": dict,
        # Standard §13.5 error-envelope fields the R1 keys ride on:
        "code": str,
        "message": str,
        "what_was_expected": str,
        "example": dict,
        "suggested_next_action": dict,
    },
    "optional": {
        "limit_details": dict,
        "cost": dict,
    },
}


def _assert_valid_handoff_envelope(env: dict) -> None:
    for key, typ in HANDOFF_ENVELOPE_SCHEMA["required"].items():
        assert key in env, f"handoff envelope missing required key {key!r}"
        assert isinstance(env[key], typ), f"{key!r} must be {typ.__name__}, got {type(env[key])}"
    for key, typ in HANDOFF_ENVELOPE_SCHEMA["optional"].items():
        if key in env and env[key] is not None:
            assert isinstance(env[key], typ), f"{key!r} must be {typ.__name__}"

    assert env["code"] == "handoff_required"
    assert env["required_actor"] == "human"
    assert env["action_url"].startswith("https://"), "action_url must be an absolute owned URL"

    # Talking points: non-empty strings, honest (no return-promising
    # language), and ALWAYS include the do-nothing alternative.
    tps = env["talking_points"]
    assert tps and all(isinstance(tp, str) and tp.strip() for tp in tps)
    joined = " ".join(tps).lower()
    assert "do nothing" in joined or "doing nothing" in joined, (
        "talking_points must include the do-nothing alternative"
    )
    assert " earn " not in f" {joined} ", "no return-promising language"
    assert "guaranteed" not in joined

    # Resume: a pollable token and/or a concrete verify call.
    resume = env["resume"]
    assert resume.get("token") or resume.get("verify_call")
    if resume.get("verify_call"):
        assert isinstance(resume["verify_call"], dict)
        assert resume["verify_call"].get("tool")

    # limit_details / cost carry only API-derived numbers — if present,
    # every numeric field must be a real number (never a placeholder str).
    for numeric_block in ("limit_details", "cost"):
        block = env.get(numeric_block)
        if isinstance(block, dict):
            for k, v in block.items():
                if k in {"limit", "current", "need", "suggested_sizing_usd"} and v is not None:
                    assert isinstance(v, (int, float)), f"{numeric_block}.{k} must be numeric"


# Real keel-api RFC 7807 bodies, exercised through the REAL translation
# path (`translate_http_error`) so the tests pin the whole chain.
QUOTA_403_BODY = (
    '{"type": "about:blank", "title": "Forbidden", "status": 403, '
    '"detail": "Insufficient entitlements", '
    '"reasons": ["entitlement:insufficient:backtest_runs:limit=30:current=30"]}'
)
LIVE_CAP_403_BODY = (
    '{"type": "about:blank", "title": "Forbidden", "status": 403, '
    '"detail": "Insufficient entitlements", '
    '"reasons": ["entitlement:cap_exceeded:live_strategies_max:limit=1:current=1"]}'
)
SCOPE_403_BODY = (
    '{"type": "about:blank", "title": "Forbidden", "status": 403, '
    '"detail": "Forbidden", "reasons": ["credential:scope_denied:runner.create"]}'
)


# ─── AC case 1: quota-exceeded backtest ──────────────────────────────────


def test_backtest_run_quota_wall_returns_handoff_envelope():
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, QUOTA_403_BODY)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler(
            {"strategy_id": "strat_q", "start_date": "2024-08-15", "end_date": "2026-07-01"},
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "backtest_run"
    # Exact numbers from the API's entitlement reason — never invented.
    assert env["limit_details"]["unit"] == "backtest_runs"
    assert env["limit_details"]["limit"] == 30
    assert env["limit_details"]["current"] == 30
    assert "30 of 30" in " ".join(env["talking_points"])
    assert env["action_url"] == "https://app.usekeel.io/settings?tab=billing"
    assert env["resume"]["verify_call"]["tool"] == "keel_backtest_run"
    assert env["resume"]["verify_call"]["args"]["strategy_id"] == "strat_q"


def test_backtest_run_scope_403_is_not_a_handoff():
    """A scope-shaped 403 on backtest re-raises unchanged (agent-recoverable)."""
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SCOPE_403_BODY)

    with pytest.raises(EntitlementError) as exc:
        get("keel_backtest_run").handler({"strategy_id": "strat_q"}, _ctx(client))
    assert not isinstance(exc.value, HandoffRequired)


# ─── AC case 2: live_deploy without live scope ───────────────────────────


def test_live_deploy_scope_wall_returns_handoff_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SCOPE_403_BODY)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_s", "account_id": "acct_1", "preview": True},
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_deploy"
    # Human path: act directly in the app (web session ≠ agent token scope).
    assert env["action_url"] == "https://app.usekeel.io/strategies/strat_s"
    # Agent path after consent: re-login with the live scope, then retry.
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert verify["args"] == {"scope": "live"}
    assert verify["then_retry"]["tool"] == "keel_live_deploy"
    # No numbers cited on the scope wall → no limit_details / cost blocks.
    assert "limit_details" not in env


def test_live_deploy_quota_cap_returns_billing_handoff(tmp_path, monkeypatch):
    """live_strategies_max cap on the deploy path → billing handoff w/ exact numbers."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, LIVE_CAP_403_BODY)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_s", "account_id": "acct_1", "preview": True},
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_deploy"
    assert env["limit_details"]["unit"] == "live_strategies_max"
    assert env["limit_details"]["limit"] == 1
    assert env["limit_details"]["current"] == 1


# ─── AC case 3: deploy without linked account ────────────────────────────


def test_live_deploy_no_account_id_with_empty_org_returns_handoff():
    """No account_id + org verifiably has zero accounts → the linking wall."""
    client = MagicMock()
    client.get.return_value = {"data": [], "pagination": {"cursor": None, "has_more": False}}
    client.post.return_value = {
        "handoff_url": "https://app.usekeel.io/deploy?intent=tok9",
        "intent_token": "tok9",
        "expires_at": "2026-07-17T01:00:00+00:00",
        "suggested_config": {
            "sizing_usd": 400,
            "sizing_basis": {
                "rule": "drawdown_conservative_v1",
                "max_drawdown_pct": 12.5,
                "worst_case_loss_usd": 50,
            },
        },
    }

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler({"strategy_id": "strat_u"}, _ctx(client))

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_deploy"
    # The minted deploy-intent deep link is the action_url; its token is
    # the resume token, and the verify_call is the executable poll (R6):
    # keel_live_deploy with the intent_token reads handoff_state.
    assert env["action_url"] == "https://app.usekeel.io/deploy?intent=tok9"
    assert env["resume"]["token"] == "tok9"
    assert env["resume"]["verify_call"]["tool"] == "keel_live_deploy"
    assert env["resume"]["verify_call"]["args"] == {
        "strategy_id": "strat_u",
        "intent_token": "tok9",
    }
    # cost = the server-computed sizing numbers, passed through verbatim.
    assert env["cost"]["suggested_sizing_usd"] == 400
    assert env["cost"]["sizing_basis"]["max_drawdown_pct"] == 12.5
    client.post.assert_called_once_with("/v1/live/deploy-intents", json={"strategy_id": "strat_u"})


def test_live_deploy_account_id_is_handler_enforced_not_schema_required():
    """Contract pin: `account_id` must NOT be schema-required.

    The MCP adapter pre-flights schema-required args and would return a
    generic usage_error BEFORE the handler runs — making the
    unlinked-account handoff unreachable from any MCP surface (the exact
    dead-end spec 03 R4b forbids). The handler enforces account presence
    itself: no accounts → handoff envelope; accounts exist → usage error
    naming keel_accounts_list.
    """
    tool = get("keel_live_deploy")
    assert tool.input_schema["required"] == ["strategy_id"]
    assert "account_id" in tool.input_schema["properties"]


def test_live_deploy_no_account_id_but_accounts_exist_keeps_usage_error():
    """Accounts exist → it's an agent usage error, NOT a human wall."""
    client = MagicMock()
    client.get.return_value = {"data": [{"account_id": "acct_1"}], "pagination": {}}

    with pytest.raises(KeelError) as exc:
        get("keel_live_deploy").handler({"strategy_id": "strat_u"}, _ctx(client))
    assert exc.value.error_code == "missing_account_id"
    assert not isinstance(exc.value, HandoffRequired)


def test_live_deploy_account_not_found_at_deploy_returns_handoff(tmp_path, monkeypatch):
    """API 404 'account not found' on the actual deploy → linking wall."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_gone")

    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *"},  # preview
        {},  # deploy-intent mint during preview (no handoff_url)
        translate_http_error(404, '{"detail": "account not found: acct_gone"}'),  # POST /v1/live
        {},  # deploy-intent mint inside the handoff builder (no handoff_url)
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_u", "account_id": "acct_gone", "preview": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    with pytest.raises(HandoffRequired) as exc:
        tool.handler(
            {
                "strategy_id": "strat_u",
                "account_id": "acct_gone",
                "preview": False,
                "confirmation_token": token,
            },
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_deploy"
    # Mint yielded no link → owned standalone-flow entry path fallback.
    assert env["action_url"] == "https://app.usekeel.io/deploy/strat_u"
    assert env["resume"]["verify_call"]["tool"] == "keel_accounts_list"


def test_live_deploy_strategy_not_found_stays_plain_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.errors import NotFoundError
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *"},
        {},
        translate_http_error(404, '{"detail": "strategy not found: strat_x"}'),
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_x", "account_id": "acct_1", "preview": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]
    with pytest.raises(NotFoundError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_x",
                "account_id": "acct_1",
                "preview": False,
                "confirmation_token": token,
            },
            _ctx(client),
        )
    assert not isinstance(exc.value, HandoffRequired)


# ─── Plan-cap wall on strategy_compose ───────────────────────────────────


def test_strategy_compose_plan_cap_returns_handoff_envelope():
    client = MagicMock()
    body = (
        '{"detail": "Insufficient entitlements", '
        '"reasons": ["entitlement:feature_not_available:feature:custom_components"]}'
    )
    client.post.side_effect = translate_http_error(403, body)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_strategy_compose").handler(
            {"source": "Globals()", "name": "capped"}, _ctx(client)
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "strategy_compose"
    assert env["limit_details"]["unit"] == "feature:custom_components"
    assert env["resume"]["verify_call"]["tool"] == "keel_strategy_compose"


# ─── Scope wall on live_control ──────────────────────────────────────────


def test_live_control_scope_wall_returns_handoff_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SCOPE_403_BODY)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_control").handler(
            {"deployment_id": "dep_1", "action": "pause"}, _ctx(client)
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_control"
    assert env["action_url"] == "https://app.usekeel.io/live/dep_1"
    assert env["resume"]["verify_call"]["tool"] == "keel_auth_login"
    assert env["resume"]["verify_call"]["then_retry"]["args"]["action"] == "pause"


# ─── Listed-profile policy gate (research/08) ────────────────────────────


def test_mint_deploy_intent_never_mints_on_listed_profile(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    client = MagicMock()
    assert mint_deploy_intent(_ctx(client), "strat_l") is None
    client.post.assert_not_called()


def test_listed_profile_preview_carries_no_handoff_url(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    client = MagicMock()
    client.post.return_value = {"derived_schedule": "0 0 * * *"}

    env = (
        get("keel_live_deploy")
        .handler(
            {"strategy_id": "strat_l", "account_id": "acct_1", "preview": True},
            _ctx(client),
        )
        .to_envelope()
    )
    assert "handoff_url" not in env
    assert "deploy_intent" not in env
    # Only the preview POST happened — no deploy-intents call at all.
    assert client.post.call_args_list == [call("/v1/live/preview", json={"strategy_id": "strat_l"})]


def test_listed_profile_unlinked_wall_falls_back_to_owned_url(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    client = MagicMock()
    client.get.return_value = {"data": [], "pagination": {}}

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler({"strategy_id": "strat_l"}, _ctx(client))

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["action_url"] == "https://app.usekeel.io/deploy/strat_l"
    assert "token" not in env["resume"]
    client.post.assert_not_called()


# ─── Constructor invariants (honesty rules are structural) ───────────────


def _minimal_kwargs(**overrides):
    kwargs = dict(
        blocked_action="backtest_run",
        reason="r",
        action_url="https://app.usekeel.io/x",
        talking_points=["Point.", "Doing nothing is also fine."],
        resume={"verify_call": {"tool": "keel_status", "args": {}}},
    )
    kwargs.update(overrides)
    return kwargs


def test_handoff_requires_do_nothing_talking_point():
    with pytest.raises(ValueError, match="do-nothing"):
        HandoffRequired("m", **_minimal_kwargs(talking_points=["Upgrade to continue."]))


def test_handoff_rejects_return_promising_language():
    with pytest.raises(ValueError, match="forbidden"):
        HandoffRequired(
            "m",
            **_minimal_kwargs(
                talking_points=[
                    "You could earn 12% by deploying.",
                    "Doing nothing is also fine.",
                ]
            ),
        )


def test_handoff_requires_resume():
    with pytest.raises(ValueError, match="resume"):
        HandoffRequired("m", **_minimal_kwargs(resume={}))


def test_handoff_requires_action_url():
    with pytest.raises(ValueError, match="action_url"):
        HandoffRequired("m", **_minimal_kwargs(action_url=""))


def test_minimal_handoff_envelope_is_schema_valid():
    env = HandoffRequired("m", **_minimal_kwargs()).to_envelope()
    _assert_valid_handoff_envelope(env)


# ─── Wire shape through the MCP adapter ──────────────────────────────────


def test_handoff_envelope_survives_mcp_error_serialization():
    """The MCP adapter serializes KeelError via to_envelope() — the R1
    keys must ride top-level on the wire, not require unwrapping."""
    import json

    err = HandoffRequired(
        "Plan limit hit",
        **_minimal_kwargs(limit_details={"unit": "backtest_runs", "limit": 30, "current": 30}),
    )
    wire = json.loads(json.dumps(err.to_envelope(), default=str))
    _assert_valid_handoff_envelope(wire)
    assert wire["limit_details"]["limit"] == 30
