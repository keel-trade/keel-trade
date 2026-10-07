"""keel-trade 0.8.0 contract (Q-2500 A, Q-2494's CLI sibling).

- Every request names the client version (``X-Keel-Client:
  keel-trade/<version>``); keel-api treats component pins from a wheel
  WITHOUT it (≤ 0.7.0) as advisory, so the header is what keeps 0.8.0's
  explicit pins the caller's.
- A component lookup the bundled catalogue answers never mints an anonymous
  workspace (each mint spends the network's daily allowance; an ephemeral
  HOME minted one per command), and a 429 is raised, never swallowed.
- Anonymous start refuses loudly when its config cannot persist.
"""

from __future__ import annotations

import httpx
import keel.config as config_mod
import keel.surface as surface_mod
import pytest
import respx
from keel.anon import anonymous_start
from keel.client import CLIENT_HEADER, KeelClient
from keel.config import KeelConfig, save_config
from keel.errors import AuthError, KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

from keel import __version__


API = "https://api.test.io"


@pytest.fixture(autouse=True)
def _cli(monkeypatch):
    _bootstrap()
    monkeypatch.setattr(surface_mod, "_SURFACE", "cli")
    monkeypatch.delenv("KEEL_ANON_AUTO", raising=False)
    monkeypatch.delenv("KEEL_API_KEY", raising=False)


@respx.mock
def test_every_request_names_the_client_version():
    route = respx.get(f"{API}/v1/me").mock(return_value=httpx.Response(200, json={}))
    KeelClient(KeelConfig(api_key="tok", api_url=API)).get("/v1/me")
    assert route.called
    assert route.calls.last.request.headers[CLIENT_HEADER] == f"keel-trade/{__version__}"
    # Non-vacuity: the version is the package's, not a placeholder.
    assert __version__.count(".") == 2


@respx.mock
def test_a_lookup_without_credentials_never_mints():
    """Seeded red on 0.7.0's shape: no credentials + CLI surface minted an
    anonymous org for compose-help, whose answer the bundle gives anyway."""
    save_config(KeelConfig(api_url=API))
    grant = respx.post(f"{API}/v1/auth/anonymous").mock(
        return_value=httpx.Response(201, json={"access_token": "t"})
    )
    detail = respx.get(f"{API}/v1/components/PriceDataLoader").mock(
        return_value=httpx.Response(200, json={"name": "PriceDataLoader"})
    )
    for tool, args in (
        ("keel_components_get", {"name": "PriceDataLoader"}),
        ("keel_components_search", {"category": "data_loader"}),
    ):
        env = OUTCOMES[tool].handler(args, ToolContext()).to_envelope()
        assert env, tool  # the bundle answered
    assert not grant.called
    assert not detail.called


@respx.mock
def test_a_429_on_a_lookup_is_raised_not_swallowed():
    respx.get(f"{API}/v1/components/PriceDataLoader").mock(
        return_value=httpx.Response(429, json={"detail": "slow down"})
    )
    ctx = ToolContext(api_client=KeelClient(KeelConfig(api_key="tok", api_url=API)))
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_components_get"].handler({"name": "PriceDataLoader"}, ctx)
    assert exc.value.error_code == "rate_limited"


@respx.mock
def test_control_with_credentials_the_server_answers():
    route = respx.get(f"{API}/v1/components/PriceDataLoader").mock(
        return_value=httpx.Response(
            200,
            json={"name": "PriceDataLoader", "version": 3, "description": "server record"},
        )
    )
    ctx = ToolContext(api_client=KeelClient(KeelConfig(api_key="tok", api_url=API)))
    OUTCOMES["keel_components_get"].handler({"name": "PriceDataLoader"}, ctx)
    assert route.called


@respx.mock
def test_anonymous_start_refuses_when_the_config_cannot_persist(tmp_path, monkeypatch):
    respx.post(f"{API}/v1/auth/anonymous").mock(
        return_value=httpx.Response(201, json={"access_token": "eyJ.anon", "org_id": "org_a"})
    )
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the config directory should be")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", blocked)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", blocked / "config.yaml")
    with pytest.raises(AuthError) as exc:
        anonymous_start(api_url=API, quiet=True)
    assert "isn't persisting" in str(exc.value)
    assert "KEEL_API_KEY" in str(exc.value)
