"""`keel_live_update` — the SDK/MCP producer of the UPDATE intent.

Deploy-wizard-v2 spec 04 §4: the agent can mint and share an update
handoff URL; it cannot apply the update and cannot put configuration into
the intent. The tool mirrors `keel_live_deploy`'s handoff contract with
the update-intent endpoints:

  * default call mints ``POST /v1/deployments/update-intents`` with a body
    of EXACTLY ``{"deployment_id"}`` and raises the shared handoff
    envelope (``code=handoff_required``) carrying ``action_url`` (the
    minted `handoff_url`), a pollable ``resume.token`` + its
    ``expires_at``, and an executable ``resume.verify_call``;
  * calling with ``intent_token`` is a pure status poll of
    ``POST /v1/deployments/update-intents/status`` returning
    ``handoff_state`` (pending | completed | expired) — completed carries
    the applied version pointer (spec 04 §2/§6 idempotent-reuse);
  * mint refusals (409 non-live, 404) PROPAGATE with the server's own
    remediation — the link is the outcome, never masked by a fallback;
  * anonymous sessions resolve to the sign-in wall (spec 09 CL-1).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.config import KeelConfig, save_config
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import HandoffRequired


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


INTENT_TOKEN = "eyJ.fake-update-intent.token"

MINT_RESPONSE = {
    "intent_id": "uin_1",
    "intent_token": INTENT_TOKEN,
    "handoff_url": f"https://app.usekeel.io/deploy?intent={INTENT_TOKEN}",
    "expires_at": "2026-08-30T13:00:00+00:00",
    "status": "pending",
    "deployment_id": "dep_1",
    "strategy_id": "strat_1",
}


def _assert_executable(call: dict) -> None:
    """The verify_call must work if the agent follows it verbatim: the tool
    exists, required args present, no arg outside the schema."""
    tool = get(call["tool"])
    args = call.get("args", {})
    properties = set(tool.input_schema.get("properties", {}))
    required = set(tool.input_schema.get("required", []))
    assert set(args) <= properties, f"args {set(args) - properties} not in schema"
    assert required <= set(args), f"missing required args {required - set(args)}"


# ─── The mint → handoff envelope ─────────────────────────────────────────


def test_mint_raises_the_handoff_envelope_with_url_token_and_expiry():
    client = MagicMock()
    client.post.return_value = dict(MINT_RESPONSE)

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["code"] == "handoff_required"
    assert env["blocked_action"] == "live_update"
    assert env["required_actor"] == "human"
    assert env["action_url"] == MINT_RESPONSE["handoff_url"]
    # Same shape as the deploy mint: the pollable token + the link's expiry.
    assert env["resume"]["token"] == INTENT_TOKEN
    assert env["resume"]["expires_at"] == "2026-08-30T13:00:00+00:00"
    # The verify_call is the poll, executable exactly as written (R6).
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_live_update"
    assert verify["args"] == {"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}
    _assert_executable(verify)
    # Honesty rules: the do-nothing alternative is present.
    assert any("nothing" in tp.lower() for tp in env["talking_points"])


def test_mint_body_is_exactly_deployment_id_and_nothing_else():
    """The spec 04 §4 invariant, pinned at the wire: no config rides the
    mint. One POST, body == {"deployment_id"} — the server side enforces
    extra="forbid"; this proves the producer never tries."""
    client = MagicMock()
    client.post.return_value = dict(MINT_RESPONSE)

    with pytest.raises(HandoffRequired):
        get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))

    client.post.assert_called_once_with(
        "/v1/deployments/update-intents", json={"deployment_id": "dep_1"}
    )
    client.get.assert_not_called()


def test_schema_offers_no_config_shaped_params():
    """Structural half of "cannot put config into the intent": the tool's
    schema carries only the deployment id and the resume token — there is
    no field an agent could smuggle sizing/schedule/execution config into."""
    tool = get("keel_live_update")
    assert set(tool.input_schema["properties"]) == {"deployment_id", "intent_token"}
    assert tool.input_schema["required"] == ["deployment_id"]


def test_mint_refusal_propagates_server_remediation():
    """409 (deployment not LIVE/PAUSED) must NOT be masked by a fallback
    URL — the server's remediation names the actual answer."""
    client = MagicMock()
    detail = (
        '{"detail": "Cannot mint an update link for a deployment in status '
        "'STOPPED'. Must be LIVE or PAUSED.\"}"
    )
    client.post.side_effect = translate_http_error(409, detail)

    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))

    assert not isinstance(exc.value, HandoffRequired)
    assert "Must be LIVE or PAUSED" in str(exc.value)


def test_mint_unknown_shape_raises_instead_of_guessing():
    client = MagicMock()
    client.post.return_value = {"status": "pending"}  # no handoff_url/token

    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))

    assert exc.value.error_code == "update_intent_mint_unexpected"


def test_missing_deployment_id_is_a_usage_error():
    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler({}, _ctx(MagicMock()))
    assert exc.value.error_code == "missing_deployment_id"


# ─── The status poll (resume leg) ────────────────────────────────────────


def test_poll_pending_returns_handoff_state_and_repoll_action():
    client = MagicMock()
    client.post.return_value = {
        "status": "pending",
        "intent_id": "uin_1",
        "deployment_id": "dep_1",
        "strategy_id": "strat_1",
        "expires_at": "2026-08-30T13:00:00+00:00",
    }

    env = (
        get("keel_live_update")
        .handler({"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client))
        .to_envelope()
    )

    state = env["handoff_state"]
    assert state["status"] == "pending"
    assert state["deployment_id"] == "dep_1"
    assert env["next_action"]["tool"] == "keel_live_update"
    assert env["next_action"]["args"] == {
        "deployment_id": "dep_1",
        "intent_token": INTENT_TOKEN,
    }
    _assert_executable(env["next_action"])


def test_poll_completed_carries_applied_version_and_monitor_action():
    """Spec 04 §6: a fulfilled link reopened shows completed status + the
    applied version pointer — no error, no duplicate update."""
    client = MagicMock()
    client.post.return_value = {
        "status": "completed",
        "intent_id": "uin_1",
        "deployment_id": "dep_1",
        "strategy_id": "strat_1",
        "expires_at": "2026-08-30T13:00:00+00:00",
        "deployment_status": "LIVE",
        "updated_version_string": "v7",
    }

    env = (
        get("keel_live_update")
        .handler({"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client))
        .to_envelope()
    )

    state = env["handoff_state"]
    assert state["status"] == "completed"
    assert state["deployment_status"] == "LIVE"
    assert state["updated_version_string"] == "v7"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_1"
    assert env["next_action"]["tool"] == "keel_live_monitor"
    assert env["next_action"]["args"] == {"deployment_id": "dep_1"}


def test_poll_expired_carries_server_remediation_and_fresh_mint_action():
    client = MagicMock()
    remediation = "This update link has expired. Ask your agent for a fresh link."
    client.post.return_value = {
        "status": "expired",
        "intent_id": "uin_1",
        "deployment_id": "dep_1",
        "expires_at": "2026-08-30T12:00:00+00:00",
        "remediation": remediation,
    }

    env = (
        get("keel_live_update")
        .handler({"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client))
        .to_envelope()
    )

    assert env["handoff_state"]["status"] == "expired"
    assert env["handoff_state"]["remediation"] == remediation  # server text, verbatim
    assert env["next_action"]["tool"] == "keel_live_update"
    assert env["next_action"]["args"] == {"deployment_id": "dep_1"}


def test_poll_is_a_pure_status_read():
    client = MagicMock()
    client.post.return_value = {"status": "pending", "deployment_id": "dep_1"}

    get("keel_live_update").handler(
        {"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client)
    )

    client.post.assert_called_once_with(
        "/v1/deployments/update-intents/status", json={"intent_token": INTENT_TOKEN}
    )
    client.get.assert_not_called()


def test_poll_unknown_status_shape_raises_instead_of_guessing():
    client = MagicMock()
    client.post.return_value = {"status": "sideways"}

    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler(
            {"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client)
        )
    assert exc.value.error_code == "update_intent_status_unexpected"
    assert "keel_connection_check" in (exc.value.suggestion or "")


def test_poll_wrong_org_names_the_actual_fix_not_scope_relogin():
    client = MagicMock()
    body = (
        '{"detail": "This update link belongs to a different Keel account. '
        "Sign in with the account your agent is connected to (the one that "
        'owns this deployment), then open the link again."}'
    )
    client.post.side_effect = translate_http_error(403, body)

    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler(
            {"deployment_id": "dep_1", "intent_token": INTENT_TOKEN}, _ctx(client)
        )

    assert exc.value.error_code == "update_intent_wrong_org"
    assert "different Keel account" in str(exc.value)
    assert "live scope" not in (exc.value.suggestion or "")


def test_poll_tampered_token_instructs_fresh_mint():
    client = MagicMock()
    body = (
        '{"detail": "The update link failed verification. This link is not '
        'valid - it may have been truncated or altered."}'
    )
    client.post.side_effect = translate_http_error(400, body)

    with pytest.raises(KeelError) as exc:
        get("keel_live_update").handler(
            {"deployment_id": "dep_1", "intent_token": "tampered.token"}, _ctx(client)
        )

    assert exc.value.error_code == "update_intent_invalid"
    assert "failed verification" in str(exc.value)
    assert "keel_live_update" in (exc.value.suggestion or "")


# ─── Anonymous sessions (spec 09 CL-1) ───────────────────────────────────


def test_anon_session_resolves_to_signin_before_any_mint():
    save_config(
        KeelConfig(
            api_key="anon_access",
            api_url="https://api.test.io",
            refresh_token="krt_anon",
            anon_org_id="org_anon_1",
        )
    )
    client = MagicMock()
    client.post.side_effect = AssertionError(
        "no API call may be made while building an anonymous wall (CL-2)"
    )

    with pytest.raises(HandoffRequired) as exc:
        get("keel_live_update").handler({"deployment_id": "dep_1"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["blocked_action"] == "live_update"
    assert "action_url" not in env, "anon walls carry no URL (CL-1)"
    verify = env["resume"]["verify_call"]
    assert verify["tool"] == "keel_auth_login"
    assert verify["then_retry"]["tool"] == "keel_live_update"
    assert verify["then_retry"]["args"] == {"deployment_id": "dep_1"}
