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

import json
import re
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
        "limit_view": dict,
    },
}


def _assert_valid_handoff_envelope(env: dict, *, plan_wall: bool = False) -> None:
    """The R1 shape. ``plan_wall`` is the D-12 plan-limit wall: it carries
    NO `action_url` (no plan destination on an agent surface) and states its
    facts without a do-nothing line (it proposes no action). Every other
    wall carries an owned https URL and names the do-nothing alternative."""
    for key, typ in HANDOFF_ENVELOPE_SCHEMA["required"].items():
        assert key in env, f"handoff envelope missing required key {key!r}"
        assert isinstance(env[key], typ), f"{key!r} must be {typ.__name__}, got {type(env[key])}"
    for key, typ in HANDOFF_ENVELOPE_SCHEMA["optional"].items():
        if key in env and env[key] is not None:
            assert isinstance(env[key], typ), f"{key!r} must be {typ.__name__}"

    assert env["code"] == "handoff_required"
    assert env["required_actor"] == "human"
    if plan_wall:
        assert "action_url" not in env, "a plan-limit wall carries no destination (D-12)"
        assert env.get("docs_url") is None
        assert env.get("limit_details", {}).get("unit"), "a plan wall carries its numbers"
    else:
        assert isinstance(env.get("action_url"), str)
        assert env["action_url"].startswith("https://"), "action_url must be an absolute owned URL"

    # Talking points: non-empty strings, honest (no return-promising
    # language); an action wall includes the do-nothing alternative.
    tps = env["talking_points"]
    assert tps and all(isinstance(tp, str) and tp.strip() for tp in tps)
    joined = " ".join(tps).lower()
    if not plan_wall:
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
    _assert_valid_handoff_envelope(env, plan_wall=True)
    assert env["blocked_action"] == "backtest_run"
    # Exact numbers from the API's entitlement reason — never invented.
    assert env["limit_details"]["unit"] == "backtest_runs"
    assert env["limit_details"]["limit"] == 30
    assert env["limit_details"]["current"] == 30
    assert "30 of 30" in " ".join(env["talking_points"])
    assert "action_url" not in env  # D-12: no plan destination
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
            {"strategy_id": "strat_s", "account_id": "acct_1", "preview": True, "direct": True},
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env)
    assert env["blocked_action"] == "live_deploy"
    # Human path: act directly in the app (web session ≠ agent token scope).
    assert env["action_url"] == "https://app.usekeel.io/strategies/strat_s/edit"
    # Agent path after consent: re-login with the live scope, then retry.
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert verify["args"] == {"scope": "live"}
    assert verify["then_retry"]["tool"] == "keel_live_deploy"
    # No numbers cited on the scope wall → no limit_details / cost blocks.
    assert "limit_details" not in env


def test_live_deploy_quota_cap_returns_plan_limit_handoff(tmp_path, monkeypatch):
    """live_strategies_max cap on the deploy path → the neutral plan-limit
    handoff with exact numbers and no destination (D-12)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, LIVE_CAP_403_BODY)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_s", "account_id": "acct_1", "preview": True, "direct": True},
            _ctx(client),
        )

    env = exc.value.to_envelope()
    _assert_valid_handoff_envelope(env, plan_wall=True)
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
        get("keel_live_deploy").handler({"strategy_id": "strat_u", "direct": True}, _ctx(client))

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
    client.post.assert_called_once_with(
        "/v1/deployments/deploy-intents", json={"strategy_id": "strat_u"}
    )


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
        get("keel_live_deploy").handler({"strategy_id": "strat_u", "direct": True}, _ctx(client))
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
        translate_http_error(
            404, '{"detail": "account not found: acct_gone"}'
        ),  # POST /v1/deployments
        {},  # deploy-intent mint inside the handoff builder (no handoff_url)
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_u", "account_id": "acct_gone", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    with pytest.raises(HandoffRequired) as exc:
        tool.handler(
            {
                "strategy_id": "strat_u",
                "account_id": "acct_gone",
                "preview": False,
                "direct": True,
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
        {"strategy_id": "strat_x", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]
    with pytest.raises(NotFoundError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_x",
                "account_id": "acct_1",
                "preview": False,
                "direct": True,
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
    _assert_valid_handoff_envelope(env, plan_wall=True)
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
            {"strategy_id": "strat_l", "account_id": "acct_1", "preview": True, "direct": True},
            _ctx(client),
        )
        .to_envelope()
    )
    assert "handoff_url" not in env
    assert "deploy_intent" not in env
    # Only the preview POST happened — no deploy-intents call at all.
    assert client.post.call_args_list == [
        call("/v1/deployments/preview", json={"strategy_id": "strat_l"})
    ]


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
        resume={"verify_call": {"tool": "keel_account_status", "args": {}}},
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


# ─── The wall's SENTENCE, and the link policy (mcp-conversion M0.5/M3) ──
#
# These drive the same chain the product does — a real keel-api 403 body
# → `translate_http_error` → `maybe_quota_handoff` → the serialized
# envelope — and assert on the RENDERED talking point, because every
# layer below it was already "correct" while the user-visible sentence
# was false (Q-1590).


def _wall_envelope(body: str, *, strategy_id: str = "strat_q") -> dict:
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, body)
    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler(
            {"strategy_id": strategy_id, "start_date": "2024-08-15", "end_date": "2026-07-01"},
            _ctx(client),
        )
    return exc.value.to_envelope()


# The current producer's body for an org that SPENT its grant: symmetric
# keys, the machine code, the parsed block, the reset instant.
SPENT_GRANT_403 = (
    '{"title": "Forbidden", "status": 403, "detail": "Insufficient entitlements", '
    '"code": "quota_exhausted", '
    '"reasons": ["entitlement:insufficient:backtest_runs:limit=50:used=50:remaining=0'
    ':period=weekly:reset_epoch=1790035200"], '
    '"quota": {"kind": "insufficient", "unit": "backtest_runs", "label": "backtests", '
    '"code": "quota_exhausted", "limit": 50, "used": 50, "remaining": 0, '
    '"period": "weekly", "reset_epoch": 1790035200}}'
)
# Same unit, same tool, same everything — EXCEPT the plan never granted
# it. This is the control arm: the only kind of wall whose sentence may
# say the plan does not include the unit.
NO_GRANT_403 = (
    '{"title": "Forbidden", "status": 403, "detail": "Insufficient entitlements", '
    '"code": "plan_feature_unavailable", '
    '"reasons": ["entitlement:no_grants:backtest_runs"], '
    '"quota": {"kind": "no_grants", "unit": "backtest_runs", "label": "backtests", '
    '"code": "plan_feature_unavailable"}}'
)
# The pre-contract producer, whose vocabulary drift IS Q-1590: kind
# `insufficient` with neither `limit=` nor `current=` for the consumer to
# read. The old renderer fell through to the no-grants sentence here.
LEGACY_NEED_HAVE_403 = (
    '{"title": "Forbidden", "status": 403, "detail": "Insufficient entitlements", '
    '"reasons": ["entitlement:insufficient:backtest_runs:need=1:have=0"]}'
)


def test_spent_grant_wall_states_the_truth_not_the_false_no_grants_sentence():
    """Q-1590 regression, asserted on the RENDERED talking point.

    A user who has used 50 of 50 must be told exactly that, with the
    reset instant. The sentence the old code produced —
    "The current plan doesn't include backtest runs (needs 1)" — must be
    impossible for any kind but `no_grants`.
    """
    env = _wall_envelope(SPENT_GRANT_403)
    _assert_valid_handoff_envelope(env, plan_wall=True)
    points = " ".join(env["talking_points"])
    assert "Weekly backtests used — 50 of 50 on your plan." in points
    assert "They reset Tue 22 Sep 00:00 UTC." in points
    assert "does not include" not in points and "doesn't include" not in points
    # Not vacuous: the numbers are the SERVED ones and the kind is the
    # spent-grant kind — neither moves if the renderer is wrong.
    assert env["limit_details"]["kind"] == "insufficient"
    assert (env["limit_details"]["limit"], env["limit_details"]["used"]) == (50, 0 + 50)
    assert env["limit_details"]["remaining"] == 0


def test_no_grant_wall_is_the_only_one_that_says_the_plan_lacks_the_unit():
    """Control arm for the test above: identical unit and tool, one field
    different (`kind`), and the sentence flips — which is what proves the
    verdict reacts to the KIND rather than to whatever parsed."""
    env = _wall_envelope(NO_GRANT_403)
    _assert_valid_handoff_envelope(env, plan_wall=True)
    points = " ".join(env["talking_points"])
    assert "Your plan does not include backtests." in points
    assert "used —" not in points


def test_legacy_producer_vocabulary_no_longer_renders_the_false_sentence():
    """The exact Q-1590 wire: `kind: insufficient` with a vocabulary this
    client cannot read. It degrades to a sentence that claims only what
    it was given — never to "your plan doesn't include it"."""
    env = _wall_envelope(LEGACY_NEED_HAVE_403)
    points = " ".join(env["talking_points"])
    assert "Backtest runs — plan limit reached on your plan." in points
    assert "does not include" not in points and "doesn't include" not in points


def test_wall_envelope_carries_no_destination_on_any_host(monkeypatch):
    """D-12 (Q-2080), scanned over the WHOLE serialized envelope: a
    plan-limit wall carries no URL of any kind — no `action_url`, no
    `docs_url`, no billing link in `limit_details`, no link on the card —
    on the production host AND a staging one (the environment used to
    choose which billing page the wall linked; now there is none to
    choose).

    SEED (run 2026-09-28): in `maybe_quota_handoff`, pass the billing URL
    as `action_url` again — this test reds (and the exact-copy test);
    `test_resume_reruns_exactly_the_blocked_call` stays green (control).
    Reverted by reversing the edit."""
    import json

    for host in ("https://app.usekeel.io", "https://staging-app.tailf4d598.ts.net"):
        monkeypatch.setenv("KEEL_APP_URL", host)
        for body in (SPENT_GRANT_403, SPENT_WITH_PATHS_403, COMPUTE_EDGE_403):
            env = _wall_envelope(body)
            blob = json.dumps(env).lower()
            assert "action_url" not in env and env.get("docs_url") is None
            assert "http" not in blob, "no URL of any kind on a plan-limit wall"
            for token in (
                "/settings",
                "tab=billing",
                "/checkout",
                "stripe",
                "/pricing",
                "billing_url",
                "see plans",
                "higher_plans",
            ):
                assert token not in blob, f"{token!r} leaked into a wall envelope"
    # Non-vacuity: the walls scanned are real walls with real numbers.
    assert env["limit_details"]["limit"] == 1500


def test_talking_points_validator_rejects_a_pitch():
    """D-11 restraint half, widened by D-12, in the ONE validator both
    surfaces use.

    Seeded arms: a price, a capacity promise, a checkout pointer, and —
    since D-12 — the D-11 revision's own "Higher plans include more …"
    sentence, a "See plans" pointer and the billing tab. Control arm: the
    same shape with none of them passes unchanged.
    """
    from keel.tools.outcomes._handoff import validate_talking_points

    honest = [
        "You've used 50 of 50 backtests included this week.",
        "Changing the plan is a human step in the Keel account settings.",
        "Doing nothing is also fine — everything you've built stays.",
    ]
    assert validate_talking_points(honest) == honest

    for pitch in (
        "Starter is $29/month and includes 150 backtests a week.",
        "The next tier adds capacity right away.",
        "Open the checkout page to continue.",
        "29 USD per month unlocks more runs.",
        "Act now — hurry, this is a limited-time offer.",
        "Higher plans include more backtests: Starter 500 a week, Trader unlimited.",
        'See plans in Keel — the card\'s "See plans in Keel" link.',
        "Plans are listed at https://app.usekeel.io/settings?tab=billing&from=agent.",
    ):
        with pytest.raises(ValueError, match="forbidden upsell language"):
            validate_talking_points([pitch, *honest[1:]])


# ─── The neutral wall (D-12, 04 §4.1) and its calm card (Q-1806) ──────────
#
# The 2026-09-23 ChatGPT observation (Q-1806): at 50/50 the card rendered,
# in red, a directive to the MODEL. The 2026-09-28 rejection (Q-2080): the
# wall named the other tiers and drew a "See plans in Keel" button into the
# billing tab. The wall now states the caller's own limit and reset; on the
# free plan, that paid plans include more; and what does not use the
# allowance — as text, with no link.

PATHS = [
    {"plan": "starter", "limit": 500, "period": "weekly"},
    {"plan": "trader", "unlimited": True},
]
SPENT_WITH_PATHS_403 = (
    '{"title": "Forbidden", "status": 403, "detail": "Insufficient entitlements", '
    '"code": "quota_exhausted", '
    '"quota": {"kind": "insufficient", "unit": "backtest_runs", "label": "backtests", '
    '"code": "quota_exhausted", "limit": 50, "used": 50, "remaining": 0, '
    '"period": "weekly", "reset_epoch": 1790035200, "plan": "free", '
    '"higher_plans": ' + json.dumps(PATHS) + "}}"
)
PAID_WITH_PATHS_403 = SPENT_WITH_PATHS_403.replace('"plan": "free"', '"plan": "starter"').replace(
    '"limit": 50, "used": 50', '"limit": 500, "used": 500'
)
COMPUTE_EDGE_403 = (
    '{"title": "Forbidden", "status": 403, "detail": "x", "code": "quota_exhausted", '
    '"quota": {"kind": "insufficient", "unit": "backtest_compute_seconds", '
    '"label": "backtest compute time", "code": "quota_exhausted", "limit": 1500, '
    '"used": 1490, "remaining": 10, "period": "weekly", "reset_epoch": 1790035200, '
    '"plan": "free", "higher_plans": [{"plan": "starter", "limit": 15000, '
    '"period": "weekly"}, {"plan": "pro", "unlimited": true}]}}'
)

FREE_WALL = [
    "Weekly backtests used — 50 of 50 on the Free plan. They reset Tue 22 Sep 00:00 UTC.",
    "Plans are changed in the Keel web app.",
    "Composing, validating and reading existing results don't use this allowance.",
]
PAID_WALL = [
    "Weekly backtests used — 500 of 500 on your plan. They reset Tue 22 Sep 00:00 UTC.",
    "Composing, validating and reading existing results don't use this allowance.",
]

#: Words that only make sense to the MODEL. None may reach text a person
#: reads on the card (`message` and every string in `limit_view`).
_MODEL_DIRECTED = re.compile(
    r"`|\bexample\b|\bdocs_url\b|\blimit_details\b|\baction_url\b"
    r"|\bdo not retry\b|\bdon't retry\b|\breport the\b|\brelay\b",
    re.IGNORECASE,
)


def _card_texts(env: dict) -> list[str]:
    texts = [env["message"]]
    for value in (env.get("limit_view") or {}).values():
        assert isinstance(value, str), f"every limit_view field is text, got {value!r}"
        texts.append(value)
    return texts


def _model_directed_in_card(env: dict) -> list[str]:
    return [t for t in _card_texts(env) if _MODEL_DIRECTED.search(t)]


def test_card_text_carries_no_model_directed_words():
    env = _wall_envelope(SPENT_WITH_PATHS_403)
    _assert_valid_handoff_envelope(env, plan_wall=True)
    assert len(_card_texts(env)) >= 5, "non-vacuity: message + headline/reset/plans/note"
    assert _model_directed_in_card(env) == []


def test_model_directed_guard_can_fail(monkeypatch):
    """Seeded arm: the exact pre-Q-1806 message suffix, put back into the
    message renderer, must trip the guard. Control: the unseeded wall is
    clean (test above) — the guard reacts to the words, not to the wall."""
    import keel.errors as errors

    real = errors._quota_message
    monkeypatch.setattr(
        errors,
        "_quota_message",
        lambda info: (
            real(info) + " Report the numbers in `example`, name the reset if one is given, "
            "include `docs_url`, and do not retry."
        ),
    )
    env = _wall_envelope(SPENT_WITH_PATHS_403)
    assert _model_directed_in_card(env) == [env["message"]]


def test_free_wall_is_the_exact_d12_copy():
    """04 §4.1 free plan, quote-level, on every carrier: the talking points
    ARE the three lines, the message is them joined, and the card draws the
    same sentences (headline, reset, plans, note) with no link.

    SEEDS (run 2026-09-28, each reverted by reversing the edit): add
    `limit_view["link"] = "See plans in Keel"` after the card loop in
    `maybe_quota_handoff` — the D-12 scan raises and this test and the
    paid-wall test red (a non-string link dict is refused by the scan
    too); pass the billing URL as `action_url` — this test and the
    no-destination test red. The key set is pinned here as well, so a
    link that slipped past the scan still reds."""
    env = _wall_envelope(SPENT_WITH_PATHS_403)
    _assert_valid_handoff_envelope(env, plan_wall=True)
    assert env["talking_points"] == FREE_WALL
    assert env["message"] == " ".join(FREE_WALL)
    view = env["limit_view"]
    assert set(view) == {"headline", "reset", "plans", "note"}, view
    assert view == {
        "headline": "Weekly backtests used — 50 of 50 on the Free plan",
        "reset": "They reset Tue 22 Sep 00:00 UTC.",
        "plans": FREE_WALL[1],
        "note": FREE_WALL[2],
    }
    # ONE authoritative home for the numbers: limit_details. The empty
    # `example` is never pointed at; the caller's own plan rides along.
    assert env["example"] == {}
    assert env["limit_details"]["plan"] == "free"
    assert "higher_plans" not in env["limit_details"]
    assert "`example`" not in json.dumps(env)
    assert env["reason"] == "A plan limit on backtests stopped this call."
    assert env["resume"]["verify_call"]["reason"] == (
        "The same call, unchanged; it succeeds after the reset."
    )


def test_paid_wall_carries_no_plan_sentence():
    """04 §4.1 any paid plan (Q2): limit, reset and the allowance note —
    no plan mention. Control arm for the test above: the same wall, one
    field different (`plan`), and the plan sentence disappears."""
    env = _wall_envelope(PAID_WITH_PATHS_403)
    _assert_valid_handoff_envelope(env, plan_wall=True)
    assert env["talking_points"] == PAID_WALL
    assert env["message"] == " ".join(PAID_WALL)
    assert set(env["limit_view"]) == {"headline", "reset", "note"}
    assert "Paid plans" not in json.dumps(env)


def test_the_served_higher_plans_never_render():
    """keel-api still serves `higher_plans` (D-10); the SDK renders none of
    it (D-12). Seed the served number: nothing on the envelope moves, which
    proves no sentence reads it. Non-vacuity: the seed changed the body."""
    seeded = SPENT_WITH_PATHS_403.replace('"limit": 500', '"limit": 777')
    assert seeded != SPENT_WITH_PATHS_403
    env = _wall_envelope(seeded)
    blob = json.dumps(env)
    for token in ("777", "500", "Starter", "Trader", "starter", "trader", "unlimited"):
        assert token not in blob, token
    assert env == _wall_envelope(SPENT_WITH_PATHS_403)


def test_older_server_without_a_plan_gets_the_paid_shape():
    """A server that named no plan gets no plan sentence — the free-plan
    sentence is never guessed onto a wall."""
    env = _wall_envelope(SPENT_GRANT_403)
    assert "plans" not in env["limit_view"]
    points = " ".join(env["talking_points"])
    assert "Paid plans" not in points and "Free" not in points


def test_compute_edge_wall_does_not_claim_all_used():
    env = _wall_envelope(COMPUTE_EDGE_403)
    points = env["talking_points"]
    assert points[0] == (
        "Weekly backtest compute time — 10 s of 1,500 s left on the Free plan, "
        "not enough to start this. They reset Tue 22 Sep 00:00 UTC."
    )
    assert points[1] == "Plans are changed in the Keel web app."
    joined = " ".join(points)
    assert "used 1,490" not in joined and "used all" not in joined.lower()
    assert env["limit_view"]["headline"] == (
        "Weekly backtest compute time — 10 s of 1,500 s left on the Free plan, not enough "
        "to start this"
    )
    assert "15,000" not in json.dumps(env) and "Pro" not in joined


def test_every_wall_text_field_passes_the_d12_scan(monkeypatch):
    """The scan covers message, reason, suggestion, talking points and the
    card — not only talking points. Seeded arm: the wall's plan sentence
    renderer returns the D-11 revision's tier line; building the wall
    raises (a programming error, never a string quietly shipped). Control:
    the unseeded free wall builds (tests above)."""
    import keel.errors as errors

    monkeypatch.setattr(
        errors,
        "quota_plans_line",
        lambda info: "Higher plans include more backtests: Starter 500 a week, Trader unlimited.",
    )
    with pytest.raises(ValueError, match="forbidden plan/upsell language"):
        _wall_envelope(SPENT_WITH_PATHS_403)


def test_resume_reruns_exactly_the_blocked_call():
    """Q-1807 correctness: the blocked call pinned v3 (`commit_id`), a
    window, a config and a presentation. Followed after the reset, the
    resume must rerun THAT — the old one carried strategy_id + dates only,
    so it would have backtested HEAD."""
    blocked = {
        "strategy_id": "strat_q",
        "commit_id": "cmt_v3",
        "start_date": "2025-01-01",
        "end_date": "2026-07-01",
        "config": {"init_cash": 25000, "fees": 0.0005},
        "present": "view",
    }
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SPENT_WITH_PATHS_403)
    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler(dict(blocked), _ctx(client))
    verify = exc.value.to_envelope()["resume"]["verify_call"]
    assert verify["tool"] == "keel_backtest_run"
    assert len(blocked) >= 5, "non-vacuity: every semantic arg kind is exercised"
    assert verify["args"] == blocked
    # What was SENT is what the resume replays (the server saw commit v3).
    sent = client.post.call_args.kwargs["json"]
    assert sent["commit_id"] == verify["args"]["commit_id"]


def test_resume_names_no_date_the_caller_omitted():
    """Q-2012 / Q-2029: a call made without dates resumes without dates.
    The resume used to carry the end_date the blocked call resolved to (the
    day of the wall) — a date the user never named, stale by the reset.
    Non-vacuity: the blocked call itself DID send an end_date, so the
    resolved date existed to leak."""
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SPENT_WITH_PATHS_403)
    with pytest.raises(HandoffRequired) as exc:
        get("keel_backtest_run").handler({"strategy_id": "strat_q"}, _ctx(client))
    args = exc.value.to_envelope()["resume"]["verify_call"]["args"]
    assert client.post.call_args.kwargs["json"]["end_date"], "the request resolved one"
    assert args == {"strategy_id": "strat_q"}
    assert "end_date" not in args and "start_date" not in args
