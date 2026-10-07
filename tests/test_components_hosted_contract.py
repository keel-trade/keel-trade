"""The hosted (API-backed) component tools honour their documented inputs (Q-2270).

On the hosted listed server `keel_components_search` with no text filter
reads keel-api's `GET /v1/components`, which takes `category` only. Before
Q-2270 that path forwarded `limit` as a query param the route never declared
and returned the whole catalogue (224 entries) under a result saying
`limit: 20`.

Driven over the real FastMCP wire on the hosted listed server with
`KeelClient.get` faked to answer what keel-api serves.

The API serves a component's `clock_transfer` only per version
(`versions[<n>].clock_transfer`); the tools read the top-level key the
bundled registry carries, so every hosted component read as `keep` — no
`clock_direction` on a resampler, no `clock` block on `keel_components_get`.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* delete `results = results[:limit]` from `components_search._handler` — the
  three API limit arms red (60 entries come back); the bundled-path control
  stays green.
* make `registry.clock_transfer_of` read the top-level key only — the two
  hosted clock arms red; the keep-component control stays green.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import _bootstrap


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)

#: What `GET /v1/components` serves, reduced to the fields the tool reads.
_CATALOGUE = [
    {
        "name": f"Comp{i:03d}",
        "category": "indicator" if i % 2 else "data_transform",
        "description": "a component",
        "input_type": "SignalSeries",
        "output_type": "SignalSeries",
    }
    for i in range(60)
]


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok_test", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _call(tool: str, args: dict, answer) -> tuple[list, dict]:
    calls: list = []

    def get(self, path, **params):
        calls.append((path, params))
        return answer(path, params)

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    with _hosted_listed(), mock.patch("keel.client.KeelClient.get", get):
        result = asyncio.run(run())
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    body = json.loads(text)
    if isinstance(body.get("result"), str):
        body = json.loads(body["result"])
    return calls, body


def _catalogue(path, params):
    rows = _CATALOGUE
    if params.get("category"):
        rows = [r for r in rows if r["category"] == params["category"]]
    return rows


@pytest.mark.parametrize(
    ("args", "expected"),
    [({}, 20), ({"limit": 7}, 7), ({"category": "indicator", "limit": 5}, 5)],
)
def test_search_on_the_api_path_returns_at_most_limit(args, expected):
    calls, body = _call("keel_components_search", args, _catalogue)
    assert [c[0] for c in calls] == ["/v1/components"]  # the API path ran
    assert "limit" not in calls[0][1]  # the route takes no limit
    assert len(body["results"]) == expected
    assert body["limit"] == expected and body["total"] == expected
    if "category" in args:
        assert {r["category"] for r in body["results"]} == {"indicator"}


def test_search_on_the_bundled_path_returns_at_most_limit():
    """Control: a keyword search never reaches the API; the bundled search
    stops at `limit` itself."""
    calls, body = _call("keel_components_search", {"keyword": "a", "limit": 3}, _catalogue)
    assert calls == []
    assert len(body["results"]) == 3 and body["limit"] == 3


def _api_record(name: str) -> dict:
    """`name` as keel-api serves it: the bundled record with `clock_transfer`
    moved into `versions[<latest>]` and no top-level `clock_transfer` or
    `clock_direction` (routers/components.py `_signature_to_response`)."""
    import copy

    from keel.data.registry import get_component_detail

    record = copy.deepcopy(get_component_detail(name))
    transfer = record.pop("clock_transfer", None)
    record.pop("clock_direction", None)
    latest = str(record.get("latest") or record.get("version"))
    record.setdefault("versions", {}).setdefault(latest, {})["clock_transfer"] = transfer
    for entry in record["versions"].values():
        entry.pop("clock_direction", None)
    return record


def _offline(path, params):
    from keel.errors import KeelError

    raise KeelError("offline")


def test_hosted_component_detail_carries_the_same_clock_block_as_bundled():
    _, bundled = _call("keel_components_get", {"name": "TargetTimeframeResampler"}, _offline)
    assert bundled["clock"]["direction"] == "resample"  # non-vacuous
    calls, hosted = _call(
        "keel_components_get",
        {"name": "TargetTimeframeResampler"},
        lambda path, params: _api_record("TargetTimeframeResampler"),
    )
    assert [c[0] for c in calls] == ["/v1/components/TargetTimeframeResampler"]
    assert "clock_transfer" not in _api_record("TargetTimeframeResampler")
    assert hosted.get("clock") == bundled["clock"]


def test_hosted_search_names_a_resamplers_clock_direction():
    names = ("TargetTimeframeResampler", "TimeframeResampler", "PriceDataLoader")
    calls, body = _call(
        "keel_components_search",
        {"category": "data_transform", "limit": 50},
        lambda path, params: [_api_record(n) for n in names],
    )
    assert [c[0] for c in calls] == ["/v1/components"]
    directions = {r["name"]: r.get("clock_direction") for r in body["results"]}
    assert directions["TargetTimeframeResampler"] == "resample"
    assert directions["TimeframeResampler"] == "resample"


def test_a_keep_component_has_no_clock_on_either_path():
    """Control: a component that leaves the clock alone carries no block."""
    _, hosted = _call("keel_components_get", {"name": "ROC"}, lambda p, q: _api_record("ROC"))
    _, bundled = _call("keel_components_get", {"name": "ROC"}, _offline)
    assert "clock" not in hosted and "clock" not in bundled
