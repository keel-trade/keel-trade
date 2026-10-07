"""Smoke tests for the live outcome tools (deploy/monitor/control).

Each test mocks `KeelClient.get` / `post` / `delete` so we exercise the
handler dispatch and URL plumbing without touching the network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
import yaml
from keel.errors import KeelError, ValidationError
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import HandoffRequired


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


# ─── keel_live_deploy ───────────────────────────────────────────────────


def _preview_store(tmp_path: Path) -> Path:
    return tmp_path / ".keel" / "live-previews.yaml"


def test_live_deploy_preview_returns_preview_data(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.return_value = {
        "strategy_name": "Hype-Carry",
        "derived_schedule": "0 */4 * * *",
        "weights": [{"symbol": "HYPE", "weight": 1.0}],
        "est_slippage": 3.2,
        "est_fees": 1.1,
    }
    tool = get("keel_live_deploy")

    result = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    )

    # Preview runs first; the deploy-intent mint (spec 03 R2) follows.
    assert client.post.call_args_list == [
        call("/v1/deployments/preview", json={"strategy_id": "strat_abc"}),
        call("/v1/deployments/deploy-intents", json={"strategy_id": "strat_abc"}),
    ]
    env = result.to_envelope()
    # The mocked mint response carries no handoff_url → the preview
    # envelope omits the deep-link fields rather than inventing them.
    assert "handoff_url" not in env
    # Preview mode → no run_id / hero_url, but preview body present.
    assert env["share_url"] is None
    assert env.get("run_id") is None
    assert env.get("hero_url") is None
    assert "preview" in env
    assert env["preview"]["derived_schedule"] == "0 */4 * * *"
    assert env["preview"]["est_slippage"] == 3.2
    assert "confirmation_token" in env
    assert env["next_action"]["args"]["preview"] is False
    assert env["next_action"]["args"]["confirmation_token"] == env["confirmation_token"]
    assert env["confirmation_expires_at"].endswith("Z")

    store = yaml.safe_load(_preview_store(tmp_path).read_text(encoding="utf-8"))
    record = store["previews"][env["confirmation_token"]]
    assert record["strategy_id"] == "strat_abc"
    assert record["account_id"] == "acct_1"
    assert record["schedule"] is None
    assert record["preview"]["derived_schedule"] == "0 */4 * * *"


def test_live_deploy_actual_returns_deployment_id(tmp_path, monkeypatch):
    # Live deploy is gated by the local arming layer (spec §7); arm
    # the test account before calling preview=False.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = [
        {
            "strategy_name": "Hype-Carry",
            "derived_schedule": "0 0 * * *",
            "weights": [{"symbol": "HYPE", "weight": 1.0}],
            "est_slippage": 3.2,
            "est_fees": 1.1,
        },
        {
            "handoff_url": "https://app.usekeel.io/deploy?intent=tok123",
            "intent_token": "tok123",
            "expires_at": "2026-07-17T01:00:00+00:00",
            "suggested_config": {"sizing_usd": 500, "sizing_basis": {"max_drawdown_pct": 12.0}},
        },
        {"deployment_id": "dep_42", "status": "deploying"},
    ]
    tool = get("keel_live_deploy")

    preview_result = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": True,
            "direct": True,
            "schedule": "0 0 * * *",
        },
        _ctx(client),
    )
    preview_env = preview_result.to_envelope()
    token = preview_env["confirmation_token"]
    # Spec 03 R2: preview additionally returns the signed handoff deep link.
    assert preview_env["handoff_url"] == "https://app.usekeel.io/deploy?intent=tok123"
    assert preview_env["deploy_intent"]["intent_token"] == "tok123"
    assert preview_env["deploy_intent"]["suggested_config"]["sizing_usd"] == 500

    result = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": False,
            "direct": True,
            "schedule": "0 0 * * *",
            "confirmation_token": token,
        },
        _ctx(client),
    )

    assert client.post.call_args_list == [
        call("/v1/deployments/preview", json={"strategy_id": "strat_abc"}),
        call("/v1/deployments/deploy-intents", json={"strategy_id": "strat_abc"}),
        call(
            "/v1/deployments",
            json={
                "strategy_id": "strat_abc",
                "account_id": "acct_1",
                "schedule": "0 0 * * *",
            },
        ),
    ]
    env = result.to_envelope()
    assert env["run_id"] == "dep_42"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_42"
    assert env["share_url"] is None

    store = yaml.safe_load(_preview_store(tmp_path).read_text(encoding="utf-8"))
    assert token not in store.get("previews", {})


def test_live_deploy_actual_requires_confirmation_token():
    client = MagicMock()
    tool = get("keel_live_deploy")

    with pytest.raises(KeelError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_abc",
                "account_id": "acct_1",
                "preview": False,
                "direct": True,
            },
            _ctx(client),
        )

    assert exc.value.error_code == "missing_confirmation_token"
    client.post.assert_not_called()


def test_live_deploy_confirmation_token_must_match_request(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.return_value = {
        "strategy_name": "Hype-Carry",
        "derived_schedule": "0 */4 * * *",
    }
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    with pytest.raises(KeelError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_abc",
                "account_id": "acct_2",
                "preview": False,
                "direct": True,
                "confirmation_token": token,
            },
            _ctx(client),
        )

    assert exc.value.error_code == "confirmation_token_mismatch"
    # Preview post + deploy-intent mint only — the mismatch stops the deploy.
    assert client.post.call_args_list[0] == call(
        "/v1/deployments/preview", json={"strategy_id": "strat_abc"}
    )
    assert all(c.args[0] != "/v1/deployments" for c in client.post.call_args_list)


def test_live_deploy_confirmation_token_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.return_value = {
        "strategy_name": "Hype-Carry",
        "derived_schedule": "0 */4 * * *",
    }
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    path = _preview_store(tmp_path)
    store = yaml.safe_load(path.read_text(encoding="utf-8"))
    store["previews"][token]["expires_at"] = "2000-01-01T00:00:00Z"
    path.write_text(yaml.safe_dump(store, sort_keys=True), encoding="utf-8")

    with pytest.raises(KeelError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_abc",
                "account_id": "acct_1",
                "preview": False,
                "direct": True,
                "confirmation_token": token,
            },
            _ctx(client),
        )

    assert exc.value.error_code == "confirmation_token_expired"
    assert client.post.call_args_list[0] == call(
        "/v1/deployments/preview", json={"strategy_id": "strat_abc"}
    )
    assert all(c.args[0] != "/v1/deployments" for c in client.post.call_args_list)


# ─── keel_live_deploy write-through guard (spec 08 R2) ───────────────────


def _ws_meta(strategy_id: str, base_hash: str):
    from keel.workspace import WorkspaceMeta

    return WorkspaceMeta(
        strategy_id=strategy_id,
        name="G",
        source_hash=base_hash,
        checked_out_at="2026-07-16T00:00:00Z",
        current_sequence=1,
    )


def test_live_deploy_preview_local_ahead_default_pushes_first(tmp_path, monkeypatch):
    """Preview on a locally-ahead strategy write-throughs by default."""
    from unittest.mock import patch

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.return_value = {"derived_schedule": "0 0 * * *"}
    pushed = {
        "strategy_id": "strat_g",
        "status": "pushed",
        "source_hash": "NEW",
        "sequence": 5,
        "commit_id": "cmt_dep",
    }

    with (
        patch("keel.workspace.get_workspace", return_value=_ws_meta("strat_g", "BASE")),
        patch("keel.workspace.read_local_source", return_value="edited"),
        patch("keel.workspace._compute_hash", return_value="NEW"),
        patch("keel.workspace.push", return_value=pushed) as push_mock,
    ):
        env = (
            get("keel_live_deploy")
            .handler(
                {"strategy_id": "strat_g", "account_id": "acct_1", "preview": True, "direct": True},
                _ctx(client),
            )
            .to_envelope()
        )

    push_mock.assert_called_once()
    assert push_mock.call_args.kwargs["message"] == "Auto-push before live deploy"
    # Preview still ran (against the freshly pushed HEAD)
    assert client.post.call_args_list[0] == call(
        "/v1/deployments/preview", json={"strategy_id": "strat_g"}
    )
    assert "cmt_dep" in env["sync_note"]
    assert "auto-pushed" in env["sync_note"].lower()


def test_live_deploy_preview_local_ahead_opt_out_raises(tmp_path, monkeypatch):
    """auto_push=False → preview refuses with `local_ahead` (no push, no preview)."""
    from unittest.mock import patch

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()

    with (
        patch("keel.workspace.get_workspace", return_value=_ws_meta("strat_g", "BASE")),
        patch("keel.workspace.read_local_source", return_value="edited"),
        patch("keel.workspace._compute_hash", return_value="NEW"),
        patch("keel.workspace.push") as push_mock,
    ):
        with pytest.raises(KeelError) as exc:
            get("keel_live_deploy").handler(
                {
                    "strategy_id": "strat_g",
                    "account_id": "acct_1",
                    "preview": True,
                    "direct": True,
                    "auto_push": False,
                },
                _ctx(client),
            )

    push_mock.assert_not_called()
    client.post.assert_not_called()
    assert exc.value.error_code == "local_ahead"


def test_live_deploy_preview_conflict_stops_never_forces(tmp_path, monkeypatch):
    """Local ahead AND server moved → sync_conflict; preview never runs."""
    from unittest.mock import patch

    from keel.errors import ConflictError

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()

    with (
        patch("keel.workspace.get_workspace", return_value=_ws_meta("strat_g", "BASE")),
        patch("keel.workspace.read_local_source", return_value="edited"),
        patch("keel.workspace._compute_hash", return_value="LOCAL"),
        patch(
            "keel.workspace.push",
            side_effect=ConflictError("Source hash mismatch"),
        ) as push_mock,
    ):
        with pytest.raises(KeelError) as exc:
            get("keel_live_deploy").handler(
                {"strategy_id": "strat_g", "account_id": "acct_1", "preview": True, "direct": True},
                _ctx(client),
            )

    push_mock.assert_called_once()
    assert push_mock.call_args.kwargs.get("force") is not True
    client.post.assert_not_called()
    assert exc.value.error_code == "sync_conflict"
    payload = exc.value.input or {}
    assert payload["base_hash"] == "BASE"
    assert payload["local_hash"] == "LOCAL"


def test_live_deploy_confirm_stops_if_local_moved_after_preview(tmp_path, monkeypatch):
    """Local edits between preview and confirm → stop; never deploy unpreviewed code."""
    from unittest.mock import patch

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.return_value = {"derived_schedule": "0 0 * * *"}
    tool = get("keel_live_deploy")

    # Clean at preview time (no workspace at all).
    token = tool.handler(
        {"strategy_id": "strat_g", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]
    client.post.reset_mock()

    # Local copy appears + is ahead by confirm time.
    with (
        patch("keel.workspace.get_workspace", return_value=_ws_meta("strat_g", "BASE")),
        patch("keel.workspace.read_local_source", return_value="edited"),
        patch("keel.workspace._compute_hash", return_value="NEW"),
        patch("keel.workspace.push") as push_mock,
    ):
        with pytest.raises(KeelError) as exc:
            tool.handler(
                {
                    "strategy_id": "strat_g",
                    "account_id": "acct_1",
                    "preview": False,
                    "direct": True,
                    "confirmation_token": token,
                },
                _ctx(client),
            )

    # Confirm phase NEVER pushes (that would deploy unpreviewed code) and
    # never reaches POST /v1/deployments.
    push_mock.assert_not_called()
    client.post.assert_not_called()
    assert exc.value.error_code == "local_ahead"
    assert "preview" in (exc.value.suggestion or "").lower()


def test_live_deploy_guard_noops_hosted(tmp_path, monkeypatch):
    """Hosted mode: no caller filesystem — the guard must not touch keel.workspace.

    The write-through guard used to be REACHED on a hosted surface and had to
    no-op there. Since the hosted direct-deploy hardening (2db4a7b6,
    2026-07-21) `direct=true` is refused BEFORE the guard runs, so the same
    property now holds one layer earlier and strictly more strongly: hosted
    never touches the caller filesystem AND never POSTs at all. The refusal
    itself is owned by test_hosted_mode.py::
    test_hosted_refuses_in_terminal_direct_deploy; what this test still pins is
    that nothing on the hosted path reaches `keel.workspace`.
    """
    from unittest.mock import patch

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    client = MagicMock()
    client.post.return_value = {"derived_schedule": "0 0 * * *"}

    with patch("keel.workspace.get_workspace") as gw, pytest.raises(KeelError) as exc:
        get("keel_live_deploy").handler(
            {"strategy_id": "strat_g", "account_id": "acct_1", "preview": True, "direct": True},
            _ctx(client),
        )

    assert exc.value.error_code == "direct_deploy_disabled"
    gw.assert_not_called()
    client.post.assert_not_called()


# ─── keel_live_deploy DEFAULT = web handoff (D7, 2026-07-19) ─────────────


def test_live_deploy_default_returns_web_handoff_not_a_deploy():
    """Default (no `direct`): CLI/local go-live is a browser handoff — the
    agent never enumerates accounts or POSTs /v1/deployments for the live action."""
    client = MagicMock()
    client.post.return_value = {
        "handoff_url": "https://app.usekeel.io/deploy?intent=tokZ",
        "intent_token": "tokZ",
        "expires_at": "2026-07-19T01:00:00+00:00",
        "suggested_config": {"sizing_usd": 500, "sizing_basis": {"max_drawdown_pct": 10.0}},
    }
    tool = get("keel_live_deploy")

    with pytest.raises(HandoffRequired) as exc:
        # account_id supplied, but the default path ignores it and hands off.
        tool.handler({"strategy_id": "strat_abc", "account_id": "acct_1"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["code"] == "handoff_required"
    assert env["blocked_action"] == "live_deploy"
    assert env["action_url"] == "https://app.usekeel.io/deploy?intent=tokZ"
    assert env["resume"]["token"] == "tokZ"
    assert env["resume"]["verify_call"]["tool"] == "keel_live_deploy"
    assert env["cost"]["suggested_sizing_usd"] == 500
    # ONLY the deploy-intent mint happened — never POST /v1/deployments, never the
    # /v1/deployments/preview call, and NO account enumeration.
    client.post.assert_called_once_with(
        "/v1/deployments/deploy-intents", json={"strategy_id": "strat_abc"}
    )
    client.get.assert_not_called()


def test_live_deploy_default_no_account_does_not_enumerate_accounts():
    """No account_id, default path: still a web handoff — the org account
    list is never fetched (no keel_accounts_list, no /v1/accounts)."""
    client = MagicMock()
    client.post.return_value = {
        "handoff_url": "https://app.usekeel.io/deploy?intent=tokQ",
        "intent_token": "tokQ",
        "suggested_config": {"sizing_usd": None, "sizing_basis": None},
    }
    tool = get("keel_live_deploy")

    with pytest.raises(HandoffRequired) as exc:
        tool.handler({"strategy_id": "strat_abc"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["blocked_action"] == "live_deploy"
    assert env["action_url"] == "https://app.usekeel.io/deploy?intent=tokQ"
    client.get.assert_not_called()


def test_live_deploy_default_mint_unavailable_falls_back_to_deploy_url():
    """Mint yields no link (older API / missing scope) → the owned
    /deploy/{id} entry path is the fallback; resume points at monitoring."""
    client = MagicMock()
    client.post.return_value = {}  # no handoff_url → mint returns None
    tool = get("keel_live_deploy")

    with pytest.raises(HandoffRequired) as exc:
        tool.handler({"strategy_id": "strat_abc"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["action_url"] == "https://app.usekeel.io/deploy/strat_abc"
    assert "token" not in env["resume"]
    assert env["resume"]["verify_call"]["tool"] == "keel_live_monitor"


def test_live_deploy_intent_poll_works_regardless_of_direct():
    """The handoff-resume status poll (intent_token + preview) runs on the
    default surface — it's how the agent observes handoff completion."""
    client = MagicMock()
    client.post.return_value = {
        "status": "completed",
        "strategy_id": "strat_abc",
        "deployment_id": "dep_77",
        "deployment_status": "LIVE",
    }
    tool = get("keel_live_deploy")

    result = tool.handler({"strategy_id": "strat_abc", "intent_token": "tokP"}, _ctx(client))

    env = result.to_envelope()
    assert env["handoff_state"]["status"] == "completed"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_77"
    client.post.assert_called_once_with(
        "/v1/deployments/deploy-intents/status", json={"intent_token": "tokP"}
    )


# ─── keel_live_deploy `direct` is a hidden, env-gated escape hatch ───────
# (D28 2026-07-20, sdk-v0.7.0)


def test_live_deploy_description_does_not_advertise_direct():
    """The user-facing tool description must guide callers ONLY to the web
    handoff — it must not mention or invite the `direct` escape hatch."""
    tool = get("keel_live_deploy")
    assert "direct" not in tool.description.lower()
    # ...and it DOES route going-live to the web app handoff.
    assert "action_url" in tool.description
    assert "WEB APP" in tool.description


def test_live_deploy_direct_without_env_opt_in_is_disabled(monkeypatch):
    """`direct=true` alone (env gate NOT set) must NOT reach the in-terminal
    deploy: it raises `direct_deploy_disabled` and never POSTs /v1/deployments —
    so a model that merely guesses the param can't place real orders."""
    monkeypatch.delenv("KEEL_ALLOW_DIRECT_DEPLOY", raising=False)
    client = MagicMock()
    tool = get("keel_live_deploy")

    with pytest.raises(KeelError) as exc:
        tool.handler(
            {"strategy_id": "strat_abc", "account_id": "acct_1", "direct": True},
            _ctx(client),
        )

    assert exc.value.error_code == "direct_deploy_disabled"
    # The remediation points back to the web handoff + names the env gate.
    assert "KEEL_ALLOW_DIRECT_DEPLOY" in (exc.value.suggestion or "")
    # No live/preview call happened — the gate stops before any network I/O.
    client.post.assert_not_called()
    client.get.assert_not_called()


def test_live_deploy_direct_with_env_opt_in_reaches_direct_path(tmp_path, monkeypatch):
    """`direct=true` WITH KEEL_ALLOW_DIRECT_DEPLOY set still reaches the
    legacy in-terminal preview path (headless/advanced use stays
    functional)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KEEL_ALLOW_DIRECT_DEPLOY", "1")
    client = MagicMock()
    client.post.return_value = {
        "derived_schedule": "0 */4 * * *",
        "weights": [{"symbol": "HYPE", "weight": 1.0}],
        "est_slippage": 3.2,
        "est_fees": 1.1,
    }
    tool = get("keel_live_deploy")

    result = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    )

    # Reached the direct preview path: POST /v1/deployments/preview happened and a
    # local confirmation_token was minted.
    assert (
        call("/v1/deployments/preview", json={"strategy_id": "strat_abc"})
        in client.post.call_args_list
    )
    env = result.to_envelope()
    assert env["confirmation_token"]


def test_live_deploy_default_web_handoff_unaffected_by_env_opt_in():
    """Even with the escape hatch armed, the DEFAULT (no `direct`) call is
    still a web handoff — the env gate never changes the default path."""
    # conftest's _arm_direct_deploy already sets KEEL_ALLOW_DIRECT_DEPLOY=1.
    client = MagicMock()
    client.post.return_value = {
        "handoff_url": "https://app.usekeel.io/deploy?intent=tokZ",
        "intent_token": "tokZ",
        "suggested_config": {"sizing_usd": 500, "sizing_basis": None},
    }
    tool = get("keel_live_deploy")

    with pytest.raises(HandoffRequired) as exc:
        tool.handler({"strategy_id": "strat_abc", "account_id": "acct_1"}, _ctx(client))

    env = exc.value.to_envelope()
    assert env["code"] == "handoff_required"
    assert env["action_url"] == "https://app.usekeel.io/deploy?intent=tokZ"
    # Default path never POSTs /v1/deployments and never enumerates accounts.
    client.post.assert_called_once_with(
        "/v1/deployments/deploy-intents", json={"strategy_id": "strat_abc"}
    )
    client.get.assert_not_called()


# ─── keel_live_monitor ──────────────────────────────────────────────────


def test_live_monitor_overview_returns_metadata():
    client = MagicMock()
    client.get.return_value = {
        "deployment_id": "dep_42",
        "strategy_id": "strat_abc",
        "status": "active",
    }
    tool = get("keel_live_monitor")

    result = tool.handler({"deployment_id": "dep_42"}, _ctx(client))

    # The overview row first; the card's stats + equity enrichment ride
    # behind it (Q-1505) and never replace it.
    assert client.get.call_args_list[0] == call("/v1/deployments/dep_42")
    assert [c.args[0] for c in client.get.call_args_list] == [
        "/v1/deployments/dep_42",
        "/v1/deployments/dep_42/stats",
        "/v1/deployments/dep_42/equity",
    ]
    env = result.to_envelope()
    assert env["run_id"] == "dep_42"
    assert env["view"] == "overview"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_42?tab=overview"
    assert env["freshness"]["source"] == "keel_backend_records"
    assert env["freshness"]["mode"] == "recorded_state"
    assert env["freshness"]["realtime"] is False
    assert env["data"]["status"] == "active"


def test_live_monitor_overview_carries_stats_and_compact_curve():
    """The live card draws from the envelope alone (Q-1505): overview
    ships the stats slice verbatim and the equity series compacted to
    [[timestamp, equity, twr_pct], ...] — values copied, never
    recomputed, first and last points always kept."""
    from keel.tools.outcomes.live_monitor import _CURVE_MAX_POINTS

    n = 1000
    points = [
        {
            "timestamp": f"2026-06-{1 + (i % 28):02d}T00:00:00Z",
            "equity": 1000.0 + i,
            "twr_pct": i / 10,
        }
        for i in range(n)
    ]

    def _get(path, **params):
        if path.endswith("/stats"):
            return {"net_pnl": 123.4, "sharpe": 1.2}
        if path.endswith("/equity"):
            return {"points": points, "baseline_value": 1000.0, "current_value": 1999.0}
        return {"deployment_id": "dep_42", "name": "mom", "status": "active"}

    client = MagicMock()
    client.get.side_effect = _get
    env = get("keel_live_monitor").handler({"deployment_id": "dep_42"}, _ctx(client)).to_envelope()

    assert env["stats"] == {"net_pnl": 123.4, "sharpe": 1.2}
    curve = env["curve"]
    assert len(curve["points"]) == _CURVE_MAX_POINTS
    assert curve["points"][0] == ["2026-06-01T00:00:00Z", 1000.0, 0.0]
    assert curve["points"][-1] == [points[-1]["timestamp"], 1999.0, 99.9]
    assert curve["source_points"] == n
    assert curve["baseline_value"] == 1000.0
    assert curve["current_value"] == 1999.0
    # Strictly increasing equity in, strictly increasing out (no reordering).
    eqs = [p[1] for p in curve["points"]]
    assert eqs == sorted(eqs)


def test_live_monitor_overview_enrichment_is_best_effort():
    """A failing stats/equity read omits the block; the overview still
    answers (the enrichment is a render nicety, never a gate)."""

    def _get(path, **params):
        if path.endswith("/stats") or path.endswith("/equity"):
            raise KeelError("boom", error_code="upstream")
        return {"deployment_id": "dep_42", "status": "active"}

    client = MagicMock()
    client.get.side_effect = _get
    env = get("keel_live_monitor").handler({"deployment_id": "dep_42"}, _ctx(client)).to_envelope()
    assert env["data"]["status"] == "active"
    assert "stats" not in env
    assert "curve" not in env


def test_live_monitor_non_overview_views_do_not_enrich():
    client = MagicMock()
    client.get.return_value = {"points": []}
    get("keel_live_monitor").handler({"deployment_id": "dep_42", "view": "equity"}, _ctx(client))
    client.get.assert_called_once_with("/v1/deployments/dep_42/equity")


def test_live_monitor_positions_view():
    client = MagicMock()
    client.get.return_value = {"account_value": 1000.0, "perp_positions": []}
    tool = get("keel_live_monitor")

    result = tool.handler({"deployment_id": "dep_42", "view": "positions"}, _ctx(client))

    client.get.assert_called_once_with("/v1/deployments/dep_42/positions")
    env = result.to_envelope()
    assert env["view"] == "positions"
    assert env["hero_url"].endswith("?tab=positions")
    assert env["freshness"]["source"] == "hyperliquid_exchange"
    assert env["freshness"]["mode"] == "on_demand_exchange_query"


def test_live_monitor_executions_returns_executing_snapshot_without_polling():
    """EXECUTING is data, not a failure or a reason to wait for terminal state."""
    client = MagicMock()
    client.get.return_value = [
        {
            "execution_run_id": "exec_engine_123",
            "execution_status": "EXECUTING",
            "started_at": "2026-07-21T12:00:00Z",
            "ended_at": None,
        }
    ]
    tool = get("keel_live_monitor")

    result = tool.handler(
        {"deployment_id": "dep_42", "view": "executions", "limit": 25},
        _ctx(client),
    )

    client.get.assert_called_once_with("/v1/deployments/dep_42/executions", limit=25)
    env = result.to_envelope()
    assert env["view"] == "executions"
    assert env["data"][0]["execution_status"] == "EXECUTING"
    assert env["data"][0]["ended_at"] is None


def test_live_monitor_trades_view_with_filters():
    client = MagicMock()
    client.get.return_value = {"items": [], "next_cursor": None}
    tool = get("keel_live_monitor")

    result = tool.handler(
        {
            "deployment_id": "dep_42",
            "view": "trades",
            "limit": 25,
            "symbol": "HYPE",
            "side": "BUY",
            "sort_by": "notional",
            "sort_dir": "desc",
        },
        _ctx(client),
    )

    client.get.assert_called_once_with(
        "/v1/deployments/dep_42/trades",
        limit=25,
        symbol="HYPE",
        side="BUY",
        sort_by="notional",
        sort_dir="desc",
    )
    env = result.to_envelope()
    assert env["view"] == "trades"


def test_live_monitor_portfolio_ignores_deployment_id():
    client = MagicMock()
    client.get.return_value = {"deployments": []}
    tool = get("keel_live_monitor")

    result = tool.handler({"deployment_id": "all"}, _ctx(client))

    client.get.assert_called_once_with("/v1/deployments/portfolio/summary")
    env = result.to_envelope()
    assert env["view"] == "portfolio"
    assert env.get("run_id") is None
    assert env["freshness"]["source"] == "keel_snapshot_store"
    assert "lag" in env["freshness"]["note"]


def test_live_monitor_bare_call_returns_portfolio():
    """Called with no args, the tool returns the portfolio summary —
    matches the agent's intuition for 'how are my live deployments
    doing?'. v0.5.4-fix: schema previously required `deployment_id`,
    forcing the agent to ask 'which one?' even though the handler
    already supported the bare case."""
    client = MagicMock()
    client.get.return_value = {"deployments": []}
    tool = get("keel_live_monitor")

    result = tool.handler({}, _ctx(client))

    client.get.assert_called_once_with("/v1/deployments/portfolio/summary")
    env = result.to_envelope()
    assert env["view"] == "portfolio"


def test_live_monitor_schema_does_not_require_deployment_id():
    """Schema mirrors the handler's bare-call support."""
    tool = get("keel_live_monitor")
    assert "deployment_id" not in tool.input_schema["required"]


# ─── keel_live_control ──────────────────────────────────────────────────


def _arm_for_test(tmp_path, monkeypatch, account_id: str = "acct_x") -> None:
    """Arm live trading in a tmp HOME so live_control's arming gate passes."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id=account_id)


def test_live_control_pause_calls_correct_endpoint(tmp_path, monkeypatch):
    _arm_for_test(tmp_path, monkeypatch)
    client = MagicMock()
    client.post.return_value = {"status": "paused"}
    tool = get("keel_live_control")

    result = tool.handler({"deployment_id": "dep_42", "action": "pause"}, _ctx(client))

    client.post.assert_called_once_with("/v1/deployments/dep_42/pause")
    env = result.to_envelope()
    assert env["run_id"] == "dep_42"
    assert env["action"] == "pause"
    assert env["new_state"] == "paused"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_42"


def test_live_control_stop_uses_delete(tmp_path, monkeypatch):
    _arm_for_test(tmp_path, monkeypatch)
    client = MagicMock()
    client.delete.return_value = None
    tool = get("keel_live_control")

    tool.handler({"deployment_id": "dep_42", "action": "stop"}, _ctx(client))

    client.delete.assert_called_once_with("/v1/deployments/dep_42")
    client.post.assert_not_called()


def test_live_control_invalid_action_raises_ValidationError():
    client = MagicMock()
    tool = get("keel_live_control")

    with pytest.raises(ValidationError):
        tool.handler({"deployment_id": "dep_42", "action": "obliterate"}, _ctx(client))

    client.post.assert_not_called()
    client.delete.assert_not_called()


# ─── Registration sanity ────────────────────────────────────────────────


def test_live_tools_registered_with_correct_toolset_and_hints():
    deploy = get("keel_live_deploy")
    monitor = get("keel_live_monitor")
    control = get("keel_live_control")

    assert monitor.toolset == "live-read"
    for tool in (deploy, control):
        assert tool.toolset == "live-write"

    # Destructive tools
    assert deploy.annotations["destructiveHint"] is True
    assert deploy.confirm_in_cli is True
    assert "confirmation_token" in deploy.input_schema["properties"]
    assert control.annotations["destructiveHint"] is True
    assert control.confirm_in_cli is True

    # Read-only tool
    assert monitor.annotations["readOnlyHint"] is True
    assert monitor.annotations["destructiveHint"] is False

    # The CLI-only write tools keep the canonical "Do NOT" guard clause;
    # the listed monitor states its neighbours as facts (Q-1804 — an
    # imperative routing clause reads as injection on ChatGPT).
    for tool in (deploy, control):
        assert "Do NOT use" in tool.description
    for text in (monitor.description, monitor.listed_description):
        assert "Do NOT" not in text
    assert "`keel_live_control`" in monitor.description
    assert "`keel_app_link`" in monitor.listed_description


# ─── quota visibility pass-through (spec 04 R5) ──────────────────────────


def test_live_deploy_passes_through_remaining_when_present(tmp_path, monkeypatch):
    """A sub-20% `remaining` block on the deploy response surfaces top-level."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *"},
        {},  # deploy-intent mint response (no handoff_url → omitted)
        {
            "deployment_id": "dep_low",
            "status": "LIVE",
            "remaining": {"live_slots": 0},
        },
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]
    env = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": False,
            "direct": True,
            "confirmation_token": token,
        },
        _ctx(client),
    ).to_envelope()
    assert env["remaining"] == {"live_slots": 0}
    assert env["deployment"]["remaining"] == {"live_slots": 0}


def test_live_deploy_omits_remaining_when_absent(tmp_path, monkeypatch):
    """No `remaining` on the deploy response → no top-level envelope field."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from keel.permissions import write_arm

    write_arm(account_id="acct_1")

    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *"},
        {},  # deploy-intent mint response (no handoff_url → omitted)
        {"deployment_id": "dep_ok", "status": "LIVE"},
    ]
    tool = get("keel_live_deploy")
    token = tool.handler(
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True, "direct": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]
    env = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": False,
            "direct": True,
            "confirmation_token": token,
        },
        _ctx(client),
    ).to_envelope()
    assert "remaining" not in env
