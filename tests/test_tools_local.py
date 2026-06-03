"""Tests for local tool implementations."""

from __future__ import annotations

import pytest


# Valid pipeline using actual component names from registry
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

INVALID_SOURCE = """
Pipeline([
    NonexistentComponent(),
])
"""


class TestComponentTools:
    def test_components_search(self):
        from keel.tools.local import strategy_components_search

        results = strategy_components_search(keyword="ROC")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_component_detail(self):
        from keel.tools.local import strategy_component_detail

        result = strategy_component_detail("ROC")
        assert result["name"] == "ROC"

    def test_components_dump(self):
        from keel.tools.local import strategy_components_dump

        results = strategy_components_dump()
        assert len(results) > 100  # We have ~160 components

    def test_dsl_reference(self):
        from keel.tools.local import dsl_reference

        result = dsl_reference()
        assert "topics" in result


class TestStrategyTools:
    def test_validate_valid(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source=VALID_SOURCE)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_invalid(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source=INVALID_SOURCE)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_parse_error(self):
        from keel.tools.local import strategy_validate

        result = strategy_validate(source="not valid python at all }{")
        assert result["valid"] is False

    def test_explain(self):
        from keel.tools.local import strategy_explain

        result = strategy_explain(source=VALID_SOURCE)
        assert result["valid"] is True
        assert result["step_count"] >= 5
        assert len(result["steps"]) >= 5
        assert result["steps"][0]["type"] == "component"
        assert "summary" in result

    def test_diff(self):
        from keel.tools.local import strategy_diff

        source_b = VALID_SOURCE.replace("period=8", "period=16")
        result = strategy_diff(source_a=VALID_SOURCE, source_b=source_b)
        assert isinstance(result, dict)

    def test_pipeline_stage(self):
        from keel.tools.local import pipeline_stage

        result = pipeline_stage(source=VALID_SOURCE)
        assert "stage" in result
        assert result["backtest_ready"] is True

    def test_examples(self):
        from keel.tools.local import strategy_examples

        results = strategy_examples()
        # May be list or dict with "examples" key
        assert isinstance(results, (list, dict))

    def test_composition_patterns(self):
        from keel.tools.local import composition_patterns

        results = composition_patterns(query="momentum")
        assert isinstance(results, dict)
        assert "patterns" in results
        assert "query" in results
        assert results["query"] == "momentum"


class TestLockTools:
    def test_lock_generate(self):
        from keel.tools.local import strategy_lock_generate

        result = strategy_lock_generate(source=VALID_SOURCE)
        assert "component_lock" in result
        lock = result["component_lock"]
        assert "ROC" in lock
        assert isinstance(lock["ROC"], int)

    def test_lock_status_current(self):
        from keel.tools.local import strategy_lock_generate, strategy_lock_status

        lock_result = strategy_lock_generate(source=VALID_SOURCE)
        lock = lock_result["component_lock"]
        status = strategy_lock_status(source=VALID_SOURCE, component_lock=lock)
        assert status["status"] == "current"

    def test_lock_status_no_lock(self):
        from keel.tools.local import strategy_lock_status

        result = strategy_lock_status(source=VALID_SOURCE)
        assert result["status"] == "unknown"

    def test_lock_upgrade(self):
        from keel.tools.local import strategy_lock_upgrade

        result = strategy_lock_upgrade(source=VALID_SOURCE)
        assert "component_lock" in result
        assert "upgraded" in result


class TestUniverseTools:
    SOURCE_WITH_UNIVERSE = """
# name: test
Globals(target_timeframe="1d")
Universe(mode="manual", market="perp", symbols=["BTC", "ETH"])
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastWeightNormalizer(),
])
"""

    def test_universe_get(self):
        from keel.tools.local import universe_get

        result = universe_get(source=self.SOURCE_WITH_UNIVERSE)
        assert result["universe"] is not None
        assert result["universe"]["mode"] == "manual"

    def test_universe_set(self):
        from keel.tools.local import universe_set

        result = universe_set(
            source=self.SOURCE_WITH_UNIVERSE,
            mode="top_volume",
            market="perp",
            top_n=20,
        )
        assert "source" in result
        assert result["universe"]["mode"] == "top_volume"

    def test_universe_resolve_bakes_resolved_into_source(self, monkeypatch):
        """universe_resolve reads criteria from source, calls API, bakes the
        returned `resolved`/`resolved_at` back into the source. No criteria
        args — the source is the source of truth."""
        from keel.tools.local import universe_resolve

        # Stub KeelClient.post so the test is offline + deterministic.
        captured: dict = {}

        class _StubClient:
            def __init__(self):
                pass

            def post(self, path: str, json: dict):
                captured["path"] = path
                captured["body"] = json
                return {
                    "resolved": ["BTC", "ETH", "SOL", "AVAX", "ARB"],
                    "resolved_at": "2026-06-03T12:00:00+00:00",
                    "count": 5,
                }

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)

        # Source with top_volume criteria but NO resolved list (Alain's case).
        unresolved = """Universe(mode="top_volume", market="perp", top_n=5)
Pipeline([ROC(period=8)], name='s')
"""
        result = universe_resolve(source=unresolved)

        # Output shape contract
        assert set(result.keys()) >= {"source", "resolved", "resolved_at", "count"}
        assert result["count"] == 5
        assert result["resolved"] == ["BTC", "ETH", "SOL", "AVAX", "ARB"]

        # Resolved baked back into the source DSL string
        assert "BTC" in result["source"]
        assert "resolved_at" in result["source"]

        # API was called with criteria read from source — not from kwargs
        assert captured["path"] == "/v1/universe/resolve"
        assert captured["body"]["mode"] == "top_volume"
        assert captured["body"]["top_n"] == 5
        assert captured["body"]["market"] == "perp"
        # Empty criteria fields not included
        assert "symbols" not in captured["body"]
        assert "categories" not in captured["body"]

    def test_universe_resolve_raises_when_no_universe(self):
        """A strategy without Universe(...) declaration → ValueError, not silent failure."""
        from keel.tools.local import universe_resolve

        # Need a parseable strategy with NO Universe declaration. Keep it minimal.
        source_without_universe = """Pipeline([ROC(period=8)], name='s')
"""
        try:
            universe_resolve(source=source_without_universe)
        except ValueError as e:
            assert "Universe" in str(e)
        else:
            raise AssertionError("expected ValueError for source without Universe")

    def test_universe_resolve_manual_mode_passes_symbols(self, monkeypatch):
        """Manual-mode universe with `symbols` set → API receives symbols list."""
        from keel.tools.local import universe_resolve

        captured: dict = {}

        class _StubClient:
            def post(self, path: str, json: dict):
                captured["body"] = json
                return {
                    "resolved": json["symbols"],
                    "resolved_at": "2026-06-03T12:00:00+00:00",
                    "count": len(json["symbols"]),
                }

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        manual_source = """Universe(mode="manual", market="perp", symbols=["BTC", "ETH"])
Pipeline([ROC(period=8)], name='s')
"""
        result = universe_resolve(source=manual_source)
        assert captured["body"]["mode"] == "manual"
        assert captured["body"]["symbols"] == ["BTC", "ETH"]
        assert result["resolved"] == ["BTC", "ETH"]
