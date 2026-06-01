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
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True},
        _ctx(client),
    )

    client.post.assert_called_once_with(
        "/v1/live/preview", json={"strategy_id": "strat_abc"}
    )
    env = result.to_envelope()
    # Preview mode → no run_id / hero_url, but preview body present.
    assert env["share_url"] is None
    assert env.get("run_id") is None
    assert env.get("hero_url") is None
    assert "preview" in env
    assert env["preview"]["derived_schedule"] == "0 */4 * * *"
    assert env["preview"]["est_slippage"] == 3.2
    assert "confirmation_token" in env
    assert env["next_action"]["args"]["preview"] is False
    assert (
        env["next_action"]["args"]["confirmation_token"] == env["confirmation_token"]
    )
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
        {"deployment_id": "dep_42", "status": "deploying"},
    ]
    tool = get("keel_live_deploy")

    preview_result = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": True,
            "schedule": "0 0 * * *",
        },
        _ctx(client),
    )
    token = preview_result.to_envelope()["confirmation_token"]

    result = tool.handler(
        {
            "strategy_id": "strat_abc",
            "account_id": "acct_1",
            "preview": False,
            "schedule": "0 0 * * *",
            "confirmation_token": token,
        },
        _ctx(client),
    )

    assert client.post.call_args_list == [
        call("/v1/live/preview", json={"strategy_id": "strat_abc"}),
        call(
            "/v1/live",
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
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True},
        _ctx(client),
    ).to_envelope()["confirmation_token"]

    with pytest.raises(KeelError) as exc:
        tool.handler(
            {
                "strategy_id": "strat_abc",
                "account_id": "acct_2",
                "preview": False,
                "confirmation_token": token,
            },
            _ctx(client),
        )

    assert exc.value.error_code == "confirmation_token_mismatch"
    client.post.assert_called_once_with(
        "/v1/live/preview", json={"strategy_id": "strat_abc"}
    )


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
        {"strategy_id": "strat_abc", "account_id": "acct_1", "preview": True},
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
                "confirmation_token": token,
            },
            _ctx(client),
        )

    assert exc.value.error_code == "confirmation_token_expired"
    client.post.assert_called_once_with(
        "/v1/live/preview", json={"strategy_id": "strat_abc"}
    )


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

    client.get.assert_called_once_with("/v1/live/dep_42")
    env = result.to_envelope()
    assert env["run_id"] == "dep_42"
    assert env["view"] == "overview"
    assert env["hero_url"] == "https://app.usekeel.io/live/dep_42?tab=overview"
    assert env["freshness"]["source"] == "keel_backend_records"
    assert env["freshness"]["mode"] == "recorded_state"
    assert env["freshness"]["realtime"] is False
    assert env["data"]["status"] == "active"


def test_live_monitor_positions_view():
    client = MagicMock()
    client.get.return_value = {"account_value": 1000.0, "perp_positions": []}
    tool = get("keel_live_monitor")

    result = tool.handler(
        {"deployment_id": "dep_42", "view": "positions"}, _ctx(client)
    )

    client.get.assert_called_once_with("/v1/live/dep_42/positions")
    env = result.to_envelope()
    assert env["view"] == "positions"
    assert env["hero_url"].endswith("?tab=positions")
    assert env["freshness"]["source"] == "hyperliquid_exchange"
    assert env["freshness"]["mode"] == "on_demand_exchange_query"


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
        "/v1/live/dep_42/trades",
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

    client.get.assert_called_once_with("/v1/live/portfolio/summary")
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

    client.get.assert_called_once_with("/v1/live/portfolio/summary")
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

    result = tool.handler(
        {"deployment_id": "dep_42", "action": "pause"}, _ctx(client)
    )

    client.post.assert_called_once_with("/v1/live/dep_42/pause")
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

    client.delete.assert_called_once_with("/v1/live/dep_42")
    client.post.assert_not_called()


def test_live_control_invalid_action_raises_ValidationError():
    client = MagicMock()
    tool = get("keel_live_control")

    with pytest.raises(ValidationError):
        tool.handler(
            {"deployment_id": "dep_42", "action": "obliterate"}, _ctx(client)
        )

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

    # Descriptions include the canonical "Do NOT" guard clause.
    for tool in (deploy, monitor, control):
        assert "Do NOT use" in tool.description
