"""TK-345 (RF-80.4): deploy, resume and restart envelopes carry the route's ``run`` block.

With ``run.on_lifecycle_change`` on, keel-api writes a deploy, resume or paper
restart's run request in the state change's transaction (TK-092), so the book
trades at once, and the route answers ``run: {request_id, status} | null``
(spec 02 §2.5). The envelope lifts it to the top level as ``run``. Off, the
route sends no ``run`` key and the envelope is exactly today's: no ``run`` key.

The client is real; only the network is mocked (respx), so the route's JSON
passes through ``KeelClient`` exactly as in production.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import KeelError


API = "https://api.test.io"
RUN = {"request_id": "evc_01", "status": "requested"}


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    from keel.tools.outcomes import _bootstrap

    _bootstrap()


@pytest.fixture
def real_client():
    c = KeelClient(config=KeelConfig(api_key="sk_test_run_block", api_url=API))
    yield c
    c.close()


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr("keel.permissions.assert_armed_for_account", lambda *a, **k: None)


def _ctx(client):
    from keel.tools.outcomes._base import ToolContext

    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


def _row(**extra):
    return {"deployment_id": "dep_1", "status": "LIVE", **extra}


def _live_resume(client):
    from keel.tools.outcomes import get

    return (
        get("keel_live_control")
        .handler({"deployment_id": "dep_1", "action": "resume"}, _ctx(client))
        .to_envelope()
    )


def _paper(client, action):
    from keel.tools.outcomes import get

    return (
        get("keel_paper_control")
        .handler({"deployment_id": "dep_1", "action": action}, _ctx(client))
        .to_envelope()
    )


@respx.mock
def test_live_resume_lifts_the_run_block(armed, real_client):
    route = respx.post(f"{API}/v1/deployments/dep_1/resume").mock(
        return_value=httpx.Response(200, json=_row(run=RUN))
    )
    env = _live_resume(real_client)
    assert route.call_count == 1
    assert env["run"] == RUN


@respx.mock
def test_live_resume_without_the_switch_is_todays_envelope(armed, real_client):
    respx.post(f"{API}/v1/deployments/dep_1/resume").mock(
        return_value=httpx.Response(200, json=_row())
    )
    env = _live_resume(real_client)
    assert env["action"] == "resume"  # non-vacuous: the envelope was built
    assert "run" not in env


@respx.mock
def test_a_null_run_block_is_relayed_as_null(armed, real_client):
    respx.post(f"{API}/v1/deployments/dep_1/resume").mock(
        return_value=httpx.Response(200, json=_row(run=None))
    )
    assert _live_resume(real_client)["run"] is None


@respx.mock
def test_a_malformed_run_block_raises(armed, real_client):
    respx.post(f"{API}/v1/deployments/dep_1/resume").mock(
        return_value=httpx.Response(200, json=_row(run={"request_id": 7}))
    )
    with pytest.raises(KeelError) as caught:
        _live_resume(real_client)
    assert caught.value.error_code == "unexpected_response"


@respx.mock
def test_live_pause_never_carries_a_run_block(armed, real_client):
    from keel.tools.outcomes import get

    respx.post(f"{API}/v1/deployments/dep_1/pause").mock(
        return_value=httpx.Response(200, json=_row(status="PAUSED", run=RUN))
    )
    env = (
        get("keel_live_control")
        .handler({"deployment_id": "dep_1", "action": "pause"}, _ctx(real_client))
        .to_envelope()
    )
    assert "run" not in env


@respx.mock
def test_paper_resume_lifts_the_run_block(paper_sdk_released, real_client):
    respx.post(f"{API}/v1/paper/dep_1/resume").mock(
        return_value=httpx.Response(200, json=_row(run=RUN))
    )
    assert _paper(real_client, "resume")["run"] == RUN


@respx.mock
def test_paper_resume_without_the_switch_is_todays_envelope(paper_sdk_released, real_client):
    respx.post(f"{API}/v1/paper/dep_1/resume").mock(return_value=httpx.Response(200, json=_row()))
    env = _paper(real_client, "resume")
    assert env["action"] == "resume"
    assert "run" not in env


@respx.mock
def test_paper_restart_lifts_the_new_runs_block(paper_sdk_released, real_client):
    respx.post(f"{API}/v1/paper/dep_1/restart").mock(
        return_value=httpx.Response(200, json={**_row(run=RUN), "deployment_id": "dep_2"})
    )
    env = _paper(real_client, "restart")
    assert env["deployment_id"] == "dep_2"
    assert env["run"] == RUN


def _direct_deploy(tmp_path, monkeypatch, deployed: dict):
    """The in-terminal deploy (D28 escape hatch): preview, then the POST."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from keel.permissions import write_arm
    from keel.tools.outcomes import get

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    write_arm(account_id="acct_1")
    client = MagicMock()
    client.post.side_effect = [
        {"strategy_name": "S", "derived_schedule": "0 0 * * *", "weights": []},
        {"handoff_url": "https://app.usekeel.io/deploy?intent=t", "intent_token": "t"},
        deployed,
    ]
    tool = get("keel_live_deploy")
    args = {"strategy_id": "strat_abc", "account_id": "acct_1", "direct": True}
    token = tool.handler({**args, "preview": True}, _ctx(client)).to_envelope()[
        "confirmation_token"
    ]
    env = tool.handler(
        {**args, "preview": False, "confirmation_token": token}, _ctx(client)
    ).to_envelope()
    assert client.post.call_args.args[0] == "/v1/deployments"  # non-vacuous: it deployed
    return env


def test_direct_live_deploy_lifts_the_run_block(tmp_path, monkeypatch):
    env = _direct_deploy(tmp_path, monkeypatch, _row(run=RUN))
    assert env["run_id"] == "dep_1"
    assert env["run"] == RUN


def test_direct_live_deploy_without_the_switch_is_todays_envelope(tmp_path, monkeypatch):
    env = _direct_deploy(tmp_path, monkeypatch, _row())
    assert env["run_id"] == "dep_1"
    assert "run" not in env
