"""Every SDK tool that calls a keel-api 204 route succeeds on the real wire shape (Q-3133).

keel-api answers its stop and delete routes ``204 No Content``
(``paper.py`` ``DELETE /v1/paper/{id}``, ``live.py`` ``DELETE /v1/deployments/{id}``,
``strategies.py`` ``DELETE /v1/strategies/{id}``). The tool tests elsewhere
drive a fake transport that answers those routes with a JSON dict, and the
client test used to mock ``200 {"stopped": true}``, so no test ever sent the
real :class:`KeelClient` a 204, and every stop through the SDK, the CLI and the
hosted connector reported "could not be read" after the server had applied it.

These arms keep the client real and mock only the network (respx), so the
answer passes through ``KeelClient._request`` exactly as in production.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from keel.client import KeelClient
from keel.config import KeelConfig

API = "https://api.test.io"


@pytest.fixture
def real_client():
    c = KeelClient(config=KeelConfig(api_key="sk_test_204", api_url=API))
    yield c
    c.close()


def _ctx(client):
    from keel.tools.outcomes._base import ToolContext

    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


@respx.mock
def test_paper_stop_succeeds_on_204(paper_sdk_released, real_client):
    from keel.tools.outcomes import get

    route = respx.delete(f"{API}/v1/paper/dep_p").mock(return_value=httpx.Response(204))
    env = (
        get("keel_paper_control")
        .handler({"deployment_id": "dep_p", "action": "stop"}, _ctx(real_client))
        .to_envelope()
    )
    assert route.call_count == 1  # non-vacuous: the real client sent it
    assert env["action"] == "stop"
    assert env["deployment_id"] == "dep_p"
    assert env["new_state"]  # the documented outcome, since the route sends no row


@respx.mock
def test_live_stop_succeeds_on_204(monkeypatch, real_client):
    from keel.tools.outcomes import get

    monkeypatch.setattr("keel.permissions.assert_armed_for_account", lambda *a, **k: None)
    route = respx.delete(f"{API}/v1/deployments/dep_l").mock(return_value=httpx.Response(204))
    env = (
        get("keel_live_control")
        .handler({"deployment_id": "dep_l", "action": "stop"}, _ctx(real_client))
        .to_envelope()
    )
    assert route.call_count == 1
    assert env["action"] == "stop"
    assert env["new_state"]


@respx.mock
def test_strategy_delete_succeeds_on_204(real_client):
    from keel.tools.outcomes import get

    route = respx.delete(f"{API}/v1/strategies/str_x").mock(return_value=httpx.Response(204))
    env = (
        get("keel_strategy_delete")
        .handler({"strategy_id": "str_x"}, _ctx(real_client))
        .to_envelope()
    )
    assert route.call_count == 1
    assert env["deleted"] is True
