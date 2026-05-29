"""Test that JSON-loaded registry produces identical validation results."""

from __future__ import annotations

import pytest


VALID_SOURCE = """
# name: test_strategy
Globals(target_timeframe="1d")
Universe(mode="top_volume", top_n=30, market="perp")
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastWeightNormalizer(),
])
"""


class TestValidatorParity:
    def test_bundled_registry_loads(self):
        from keel.data.registry import load_registry

        data = load_registry()
        assert len(data["components"]) > 100

    def test_validate_with_bundled_registry(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source=VALID_SOURCE)
        assert result["valid"] is True
        assert len(result["type_flow"]) >= 5

    def test_validate_catches_errors(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source="Pipeline([NonexistentComponent()])")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_type_flow_accuracy(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source=VALID_SOURCE)
        flow = result["type_flow"]

        # Verify the expected type chain
        # PriceDataLoader → OHLCVDict
        assert flow[0]["output_type"] == "OHLCVDict"
        # ROC (after resampler) → SignalSeries
        roc_step = [f for f in flow if "ROC" in f["step"]]
        assert len(roc_step) > 0
        assert roc_step[0]["output_type"] == "SignalSeries"
        # ForecastWeightNormalizer → WeightSeries (final)
        assert flow[-1]["output_type"] == "WeightSeries"
