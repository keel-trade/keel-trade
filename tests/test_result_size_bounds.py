"""Listed results stay inside what a host shows (Q-2273 L7).

The round-5 audit measured `keel_components_get_many` with 30 names at
109 KB of text (over Claude's 25k-token tool-result limit), one sequential
GET per name and no dedupe (50 x `ROC` = 50 reads), and `keel_live_monitor
view=equity` handing over a 5,000-point curve (78 KB).

Arms (hosted listed server, prod flags, real FastMCP wire):

* get_many refuses more than 10 distinct names before any read;
* duplicates are read once and returned once;
* the TEN LARGEST contracts in the bundled catalog come back under 60k
  characters, the overflow named and left out (non-vacuous: the arm asserts
  at least one was left out and at least one returned whole);
* view=equity with 5,000 points returns 240, first and last kept, and says
  it was downsampled; the CLI surface keeps the whole curve (control).

Seed (2026-10-01, reverted by reversing the edit): `TEXT_BUDGET` raised to
10**9 → the largest-ten arm red (~108k characters, nothing left out).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.errors import NotFoundError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()

_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_CARD_META_MOVE",
    "KEEL_NONVIEW_TEXT_ONLY",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


@contextmanager
def _prod_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(
        KEEL_SERVER_PROFILE="listed",
        KEEL_EXECUTION_MODE="hosted",
        KEEL_CARD_META_MOVE="1",
        KEEL_NONVIEW_TEXT_ONLY="1",
    )
    token = bind_request_credentials(token="tok", api_url="https://api.size.test")
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _wire(calls, request):
    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        out = []
        async with Client(create_server()) as client:
            for name, args in calls:
                r = await client.call_tool(name, args, raise_on_error=False)
                out.append((r, "".join(getattr(c, "text", "") for c in r.content)))
        return out

    with _prod_listed(), mock.patch("keel.client.KeelClient._request", request):
        return asyncio.run(run())


def _catalog_404(reads):
    def request(self, method, path, **_kw):
        reads.append(path)
        raise NotFoundError("not served; the bundled catalog answers")

    return request


def _names() -> list[str]:
    from keel.data.registry import _ensure_loaded

    return [c["name"] for c in _ensure_loaded()["components"]]


def test_more_than_ten_names_is_refused_before_any_read():
    reads: list = []
    [(result, text)] = _wire(
        [("keel_components_get_many", {"names": _names()[:11]})], _catalog_404(reads)
    )
    # The declared `maxItems` bound (round 4's one front door) refuses first.
    assert result.is_error and "argument_out_of_range" in text and "at most 10" in text
    assert reads == []


def test_duplicate_names_are_read_once():
    reads: list = []
    [(result, text)] = _wire(
        # Within the declared 10-item bound; duplicates are still read once.
        [("keel_components_get_many", {"names": ["ROC"] * 10})],
        _catalog_404(reads),
    )
    assert not result.is_error
    assert reads == ["/v1/components/ROC"]
    assert text.startswith("1 found, 0 missing of 1 requested")


def test_the_ten_largest_contracts_stay_within_the_budget():
    sizes = {}
    for name in _names():
        env = OUTCOMES["keel_components_get"].handler({"name": name}, ToolContext()).to_envelope()
        sizes[name] = len(json.dumps(env, default=str))
    largest = sorted(sizes, key=sizes.get, reverse=True)[:10]
    assert sum(sizes[n] for n in largest) > 100_000  # non-vacuous: the ten overflow
    [(result, text)] = _wire([("keel_components_get_many", {"names": largest})], _catalog_404([]))
    assert not result.is_error
    assert len(text) < 60_000, len(text)
    assert "left out for size" in text.splitlines()[0]
    assert f"### {largest[0]} " in text  # the first is always returned whole


def _points(n):
    return [
        {"timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z", "equity": 1000.0 + i}
        for i in range(n)
    ]


def test_an_equity_curve_is_downsampled_and_says_so():
    def request(self, method, path, **_kw):
        return {"points": _points(5000), "baseline_value": 1000.0}

    [(result, text)] = _wire(
        [("keel_live_monitor", {"deployment_id": "dep_1", "view": "equity"})], request
    )
    assert not result.is_error
    data = json.loads(text)["data"]
    assert len(data["points"]) == 240
    assert data["points"][0]["equity"] == 1000.0 and data["points"][-1]["equity"] == 5999.0
    assert data["downsampled"]["points_served"] == 5000
    assert len(text) < 30_000


@pytest.mark.usefixtures("monkeypatch")
def test_control_the_cli_keeps_the_whole_curve(monkeypatch):
    import keel.surface

    monkeypatch.setattr(keel.surface, "_SURFACE", "cli")
    client = mock.MagicMock()
    client.get.return_value = {"points": _points(5000)}
    result = OUTCOMES["keel_live_monitor"].handler(
        {"deployment_id": "dep_1", "view": "equity"},
        ToolContext(api_client=client, is_tty=False),
    )
    assert len(result.extra["data"]["points"]) == 5000
    assert "downsampled" not in result.extra["data"]
