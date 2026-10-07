"""Search surfaces carry the position layer (spec 03-R63/R64, P03-45/46, Q-2448).

``sub_category`` is a filter on ``keel_components_search`` (case-insensitive
exact match, the bundled helper's semantics on both paths), and every result
says what a position component is: ``position_role`` and ``trade_safe``,
keep-by-omission so an ordinary component's entry is unchanged.
"""

from __future__ import annotations

import pytest
from keel.data import registry as R
from keel.tools.outcomes import components_search as S


_FAKE = {
    "components": [
        {
            "name": "TradeReturn",
            "category": "position_manager",
            "sub_category": "trade_reader",
            "input_type": "Position",
            "output_type": "SignalSeries",
            "binding": {"role": "reader", "scope": "trade", "scale": "fraction"},
        },
        {
            "name": "StopLoss",
            "category": "position_manager",
            "sub_category": "trade_factory",
            "input_type": "Position",
            "output_type": "Position",
            "binding": {"role": "factory"},
        },
        {
            "name": "Lag",
            "category": "signal_transform",
            "sub_category": "conversion",
            "input_type": "SignalSeries",
            "output_type": "SignalSeries",
            "trade_safe": {"warmup": {"param": "periods"}, "scale": "keep"},
        },
        {
            "name": "RSI",
            "category": "indicator",
            "sub_category": "momentum",
            "input_type": "OHLCVDict",
            "output_type": "SignalSeries",
        },
    ]
}


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    monkeypatch.setattr(R, "_ensure_loaded", lambda: _FAKE)


def test_position_search_fields_by_omission():
    by = {c["name"]: R.position_search_fields(c) for c in _FAKE["components"]}
    assert by["TradeReturn"] == {"position_role": "reader"}
    assert by["StopLoss"] == {"position_role": "factory"}
    assert by["Lag"] == {"trade_safe": True}
    assert by["RSI"] == {}  # control: an ordinary component is unchanged


def test_sub_category_filter_is_case_insensitive_exact():
    out = R.search_components(sub_category="TRADE_FACTORY", top_k=10)
    assert [e["name"] for e in out] == ["StopLoss"]
    assert out[0]["position_role"] == "factory"
    assert R.search_components(sub_category="trade", top_k=10) == []  # exact, not prefix


def test_the_outcome_tool_exposes_the_filter():
    props = S.COMPONENTS_SEARCH.input_schema["properties"]
    assert props["sub_category"]["type"] == "string"
    out = S._search_bundled({"sub_category": "trade_reader"})
    assert [e["name"] for e in out] == ["TradeReturn"]
    assert out[0]["position_role"] == "reader"


def test_the_api_path_defers_a_sub_category_filter():
    assert S._search_via_api(None, {"sub_category": "trade_reader"}) is None
