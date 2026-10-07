"""``keel_components_get`` returns ``expands_to`` for a registered factory (P03-47, 03-R51/R62).

The long form, as DSL text, of the factory's documented example on a 1h clock
under ``TradeManager(prices='ohlcv')`` — one snapshot per factory. An ordinary
component gains no position keys (the control).
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import components_help as H


_SNAPSHOTS = {
    "StopLoss": "TradeReturn(), BelowThresholdFilter(threshold=-0.05, inclusive=True), Exit()",
    "TakeProfit": (
        "TradeReturn(), AboveThresholdFilter(threshold=0.1, inclusive=True), Reduce(fraction=0.5)"
    ),
    "TrailingStop": "DrawdownFromPeak(), BelowThresholdFilter(threshold=-0.08), Exit()",
    "MaxHold": "BarsHeld(), AboveThresholdFilter(threshold=72.0, inclusive=True), Exit()",
    "Cooldown": "BarsSinceExit(), AboveThresholdFilter(threshold=3), AllowEntry()",
}


def _detail(name: str) -> dict:
    """The bundled record of ``name``; falls back to the live registry's
    signature when the bundled registry.json predates the factories (the
    lane's final regen, P03-51, bakes them in)."""
    from keel.data.registry import get_component_detail

    try:
        detail = get_component_detail(name)
        if detail.get("factory_expansion"):
            return detail
    except KeyError:
        pass
    pe = pytest.importorskip("pipeline_engine.registry_loader")
    pe.ensure_registry_loaded()
    from pipeline_engine.base.registry_types import get_latest

    sig = get_latest(name)
    if sig is None or sig.factory_expansion is None:
        pytest.skip(f"{name} is not registered in this environment")
    return {
        "name": name,
        "description": sig.description,
        "binding": sig.binding,
        "factory_expansion": sig.factory_expansion,
        "parameters": [],
    }


@pytest.mark.parametrize("name", sorted(_SNAPSHOTS))
def test_expands_to_snapshot(name):
    shaped = H._shape_detail_base(_detail(name))
    assert shaped["expands_to"] == _SNAPSHOTS[name]
    assert shaped["binding"] == {"role": "factory"}
    assert "1h clock" in shaped["expands_to_note"]


def test_control_an_ordinary_component_has_no_position_keys():
    shaped = H._shape_detail_base({"name": "RSI", "description": "RSI.", "parameters": []})
    for key in ("binding", "trade_safe", "preserves_size", "factory_expansion", "expands_to"):
        assert key not in shaped
