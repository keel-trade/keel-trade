"""Deprecation-aware search on the SDK / MCP / CLI surface (Q-2448, spec 04-R28).

``keel_components_search`` and ``keel.data.registry.search_components`` hide a
deprecated component by default through the ONE shared predicate
(``pipeline_engine.component_ranking.visible``) on every path — bundled,
``after`` / ``before``, and the ``GET /v1/components`` API path, which returns
every component and is filtered client-side. ``include_deprecated=True`` shows
them with ``status`` and ``replacement_text``; a lookup by name always answers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.data import registry as bundled
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client=None) -> ToolContext:
    return ToolContext(api_client=client or MagicMock(), app_url="https://app.usekeel.io")


def _deprecated() -> list[str]:
    data = bundled._ensure_loaded()
    return sorted(c["name"] for c in data["components"] if c.get("status") == "deprecated")


def _search(args: dict, client=None) -> list[dict]:
    if client is None:
        client = MagicMock()
        client.get.side_effect = RuntimeError("offline")  # forces the bundled path
    return get("keel_components_search").handler(args, _ctx(client)).to_envelope()["results"]


def test_bundled_search_hides_every_deprecated_component_by_default():
    names = _deprecated()
    assert len(names) >= 1, names  # non-vacuous: RollingNotionalProxyMask et al.
    shown = {c["name"] for c in bundled.search_components(top_k=10_000)}
    assert not set(names) & shown
    for name in names:  # the query path too
        assert name not in {c["name"] for c in bundled.search_components(query=name, top_k=50)}


def test_include_deprecated_shows_them_with_status_and_replacement():
    rows = {c["name"]: c for c in bundled.search_components(top_k=10_000, include_deprecated=True)}
    for name in _deprecated():
        assert rows[name]["status"] == "deprecated"
        assert "replacement_text" in rows[name]
    assert rows["RollingNotionalProxyMask"]["replacement_text"] == "Use RollingDollarVolumeMask"
    assert "status" not in rows["SMA"]  # control: an active row is unchanged


def test_the_tool_hides_on_the_bundled_path_and_shows_on_request():
    target = "RollingNotionalProxyMask"
    assert target not in {r["name"] for r in _search({"keyword": target})}
    shown = {r["name"]: r for r in _search({"keyword": target, "include_deprecated": True})}
    assert shown[target]["status"] == "deprecated"


def test_the_after_path_applies_the_predicate(monkeypatch):
    legacy = {"name": "LegacyThing", "category": "signal_transform", "status": "deprecated"}
    active = {"name": "ActiveThing", "category": "signal_transform", "status": "active"}
    monkeypatch.setattr(bundled, "get_components_after", lambda name: [legacy, active])
    assert [r["name"] for r in _search({"after": "SMA"})] == ["ActiveThing"]
    assert {r["name"] for r in _search({"after": "SMA", "include_deprecated": True})} == {
        "LegacyThing",
        "ActiveThing",
    }


def test_the_api_path_filters_client_side():
    client = MagicMock()
    client.get.return_value = [
        {"name": "RollingNotionalProxyMask", "category": "universe_filter", "status": "deprecated"},
        {"name": "SMA", "category": "indicator", "status": "active"},
    ]
    assert [r["name"] for r in _search({"category": "indicator"}, client)] == ["SMA"]
    both = _search({"category": "indicator", "include_deprecated": True}, client)
    assert {r["name"] for r in both} == {"RollingNotionalProxyMask", "SMA"}


def test_the_schema_offers_the_flag_defaulting_off():
    prop = get("keel_components_search").input_schema["properties"]["include_deprecated"]
    assert prop["type"] == "boolean" and prop["default"] is False


def test_a_lookup_by_name_still_answers():
    detail = bundled.get_component_detail("RollingNotionalProxyMask")
    assert detail["name"] == "RollingNotionalProxyMask"
