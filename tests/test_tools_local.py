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


# ─────────────────────────────────────────────────────────────────────────
# universe_resolve — the v16 staging-audit §6.1 regressions
# ─────────────────────────────────────────────────────────────────────────
#
# Two defects, both silent:
#   (a) the request body dropped `lookback` / `volume_quartiles`, so a
#       strategy declaring lookback="90d" resolved on the resolver's
#       `lookback or "7d"` default — a wrong asset list, no error;
#   (b) the source was re-emitted canonically (`spec_to_dsl`), which
#       destroys every comment in the file. Flagship strategy files carry
#       100+-line changelog headers.


COMMENTED_SOURCE = """# ══════════════════════════════════════════════════════════
# flagship v16 (C1, s=1.20) — changelog
#   2026-07-04  seeded max_leverages from the venue snapshot
#   2026-08-01  top_n bumped to the full perp pool
# ══════════════════════════════════════════════════════════
Globals(target_timeframe="1d")  # daily bars
Universe(
    mode="top_volume",
    market="perp",
    top_n=3,
    lookback="90d",  # 90-day volume ranking, NOT the 7d default
    volume_quartiles=["q1", "q2"],
    exclusions=["USDC"],
    inclusions=["HYPE"],
)
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="15min"),  # loader keeps its comment
    ROC(period=8),
])
"""


class _RecordingClient:
    """Stub KeelClient capturing the request body, returning a fixed payload."""

    captured: dict = {}
    extra_response: dict = {}

    def post(self, path: str, json: dict):
        type(self).captured = {"path": path, "body": json}
        return {
            "resolved": ["BTC", "ETH", "SOL"],
            "resolved_at": "2026-08-02T00:00:00+00:00",
            "count": 3,
            **type(self).extra_response,
        }


@pytest.fixture
def recording_client(monkeypatch):
    class _Client(_RecordingClient):
        captured: dict = {}
        extra_response: dict = {}

    monkeypatch.setattr("keel.client.KeelClient", _Client)
    return _Client


class TestUniverseResolveForwardsEverySelector:
    """(a) Every selector the API accepts must reach the request body."""

    def test_lookback_and_quartiles_are_forwarded(self, recording_client):
        from keel.tools.local import universe_resolve

        universe_resolve(source=COMMENTED_SOURCE)

        body = recording_client.captured["body"]
        assert recording_client.captured["path"] == "/v1/universe/resolve"
        # The regression: these two were silently dropped.
        assert body["lookback"] == "90d"
        assert body["volume_quartiles"] == ["q1", "q2"]
        # ...alongside the selectors that were already forwarded.
        assert body["mode"] == "top_volume"
        assert body["market"] == "perp"
        assert body["top_n"] == 3
        assert body["exclusions"] == ["USDC"]
        assert body["inclusions"] == ["HYPE"]

    def test_body_covers_the_whole_server_request_model(self, recording_client):
        """Guard against the next dropped field: every criteria key the
        source declares must appear in the body."""
        from keel.tools.local import universe_resolve

        source = (
            'Universe(mode="top_volume", market="perp", top_n=3, '
            'lookback="30d", volume_quartiles=["q1"], exclusions=["USDC"], '
            'inclusions=["HYPE"], symbols=["BTC"], categories=["l1"], '
            'resolved=["BTC", "DOGE"], resolved_at="2026-01-01T00:00:00+00:00")\n'
            "Pipeline([ROC(period=8)], name='s')\n"
        )
        universe_resolve(source=source)

        body = recording_client.captured["body"]
        assert set(body) == {
            "mode",
            "market",
            "symbols",
            "categories",
            "top_n",
            "exclusions",
            "inclusions",
            "lookback",
            "volume_quartiles",
            "current_resolved",
        }
        # The previously-resolved list rides along so the API can diff.
        assert body["current_resolved"] == ["BTC", "DOGE"]

    def test_absent_selectors_are_omitted(self, recording_client):
        """No fabricated defaults — an undeclared lookback stays undeclared."""
        from keel.tools.local import universe_resolve

        source = 'Universe(mode="top_volume", market="perp", top_n=3)\nPipeline([ROC(period=8)], name=\'s\')\n'
        universe_resolve(source=source)

        body = recording_client.captured["body"]
        assert "lookback" not in body
        assert "volume_quartiles" not in body
        assert "current_resolved" not in body


class TestUniverseResolvePreservesSource:
    """(b) The rewrite is a span edit — everything outside the edited
    `Universe(...)` arguments survives byte-for-byte."""

    def test_comment_header_survives_byte_for_byte(self, recording_client):
        from keel.tools.local import universe_resolve

        result = universe_resolve(source=COMMENTED_SOURCE)
        new_source = result["source"]

        assert result["reformatted"] is False

        # Byte-for-byte: everything before the Universe( call and everything
        # after its closing paren is untouched.
        head_end = COMMENTED_SOURCE.index("Universe(")
        assert new_source[:head_end] == COMMENTED_SOURCE[:head_end]

        tail = COMMENTED_SOURCE[COMMENTED_SOURCE.index("Execution(") :]
        assert new_source.endswith(tail)

        # Every comment line in the original is still present, verbatim.
        for line in COMMENTED_SOURCE.splitlines():
            if line.lstrip().startswith("#") or "#" in line:
                assert line in new_source.splitlines(), line

        # And the resolution actually landed.
        assert "'BTC'" in new_source or '"BTC"' in new_source
        assert "resolved_at" in new_source

    def test_declared_selectors_are_not_rewritten(self, recording_client):
        """The span edit must not touch sibling arguments — `lookback="90d"`
        keeps its spelling, its inline comment, and its position."""
        from keel.tools.local import universe_resolve

        new_source = universe_resolve(source=COMMENTED_SOURCE)["source"]
        assert '    lookback="90d",  # 90-day volume ranking, NOT the 7d default' in new_source
        assert '    volume_quartiles=["q1", "q2"],' in new_source

    def test_result_reparses_with_the_resolved_list(self, recording_client):
        from keel.tools.local import universe_get, universe_resolve

        new_source = universe_resolve(source=COMMENTED_SOURCE)["source"]
        u = universe_get(source=new_source)["universe"]
        assert u["resolved"] == ["BTC", "ETH", "SOL"]
        assert u["resolved_at"] == "2026-08-02T00:00:00+00:00"
        assert u["lookback"] == "90d"

    def test_span_edit_failure_degrades_loudly(self, recording_client, monkeypatch):
        """When the equivalence net fails we still return a correct file —
        but the caller is TOLD the comments are gone."""
        from keel.tools.local import universe_resolve

        from pipeline_engine.dsl import edits

        def _boom(*a, **kw):
            raise edits.EditEquivalenceError("forced")

        monkeypatch.setattr("pipeline_engine.dsl.edits.set_decl_arg", _boom)

        result = universe_resolve(source=COMMENTED_SOURCE)
        assert result["reformatted"] is True
        assert result["resolved"] == ["BTC", "ETH", "SOL"]
        assert "resolved" in result["source"]


class TestUniverseResolveMaxLeverages:
    """`max_leverages` passthrough — baked when the API returns it, never
    fabricated when it does not."""

    def test_max_leverages_is_baked_when_returned(self, recording_client):
        from keel.tools.local import universe_get, universe_resolve

        recording_client.extra_response = {"max_leverages": {"BTC": 40.0, "ETH": 25.0, "SOL": 20.0}}
        result = universe_resolve(source=COMMENTED_SOURCE)

        assert result["max_leverages"] == {"BTC": 40.0, "ETH": 25.0, "SOL": 20.0}
        assert result["reformatted"] is False
        # Committed into the source, next to resolved/resolved_at...
        u = universe_get(source=result["source"])["universe"]
        assert u["max_leverages"] == {"BTC": 40.0, "ETH": 25.0, "SOL": 20.0}
        # ...without disturbing the header.
        head_end = COMMENTED_SOURCE.index("Universe(")
        assert result["source"][:head_end] == COMMENTED_SOURCE[:head_end]

    def test_max_leverages_absent_when_api_omits_it(self, recording_client):
        """Today's ResolveUniverseResponse has no max_leverages. The client
        must not invent one (an empty/None map would trip
        PortfolioMarginCap at run time with a wrong story)."""
        from keel.tools.local import universe_get, universe_resolve

        result = universe_resolve(source=COMMENTED_SOURCE)
        assert "max_leverages" not in result
        # (the word appears in the file's comment header, so assert on the
        # parsed declaration rather than a substring)
        assert "max_leverages" not in universe_get(source=result["source"])["universe"]

    def test_diff_is_passed_through_when_returned(self, recording_client):
        from keel.tools.local import universe_resolve

        recording_client.extra_response = {"diff": {"added": ["SOL"], "removed": ["DOGE"]}}
        result = universe_resolve(source=COMMENTED_SOURCE)
        assert result["diff"] == {"added": ["SOL"], "removed": ["DOGE"]}


class TestUniverseSetPreservesSource:
    """`universe_set` is the step operators run immediately before
    `universe_resolve` — it had the same whole-file canonical re-emission,
    so fixing only resolve would still lose the header. Span edit here
    too (twin of pipeline_engine.mcp.tools.universe_set)."""

    def test_criteria_edit_keeps_every_other_byte(self):
        from keel.tools.local import universe_set

        result = universe_set(
            source=COMMENTED_SOURCE, mode="top_volume", market="perp", top_n=10, lookback="30d"
        )
        new_source = result["source"]

        assert result["reformatted"] is False
        head_end = COMMENTED_SOURCE.index("Universe(")
        assert new_source[:head_end] == COMMENTED_SOURCE[:head_end]
        assert new_source.endswith(COMMENTED_SOURCE[COMMENTED_SOURCE.index("Execution(") :])
        assert "# loader keeps its comment" in new_source

        # The criteria really changed.
        assert result["universe"]["top_n"] == 10
        assert result["universe"]["lookback"] == "30d"

    def test_falls_back_loudly_on_edit_failure(self, monkeypatch):
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import edits

        def _boom(*a, **kw):
            raise edits.EditEquivalenceError("forced")

        monkeypatch.setattr("pipeline_engine.dsl.edits.replace_declaration", _boom)
        result = universe_set(source=COMMENTED_SOURCE, mode="manual", symbols=["BTC"])
        assert result["reformatted"] is True
        assert result["universe"]["mode"] == "manual"


class TestUniverseResolveUnderfill:
    """Lane U: under-filled top_n is written down so the STALE gate can't loop."""

    def _resolve(self, monkeypatch, response):
        from keel.tools.local import universe_resolve

        class _StubClient:
            def post(self, path: str, json: dict):
                return response

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        source = """Universe(mode="top_volume", market="perp", top_n=200)
Pipeline([ROC(period=8)], name='s')
"""
        return universe_resolve(source=source)

    def test_top_n_written_down_on_underfill(self, monkeypatch):
        resolved = [f"S{i}" for i in range(177)]
        result = self._resolve(
            monkeypatch,
            {
                "resolved": resolved,
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 177,
                "requested_top_n": 200,
                "warnings": ["venue can supply 177 of 200 requested assets"],
            },
        )
        assert result["top_n_written_down"] == {
            "requested": 200,
            "achievable": 177,
            "reason": "venue can supply 177 of 200 requested assets",
        }
        assert "top_n=177" in result["source"].replace(" ", "")
        assert result["warnings"] == ["venue can supply 177 of 200 requested assets"]

    def test_exact_fill_leaves_top_n_alone(self, monkeypatch):
        resolved = [f"S{i}" for i in range(200)]
        result = self._resolve(
            monkeypatch,
            {
                "resolved": resolved,
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 200,
                "requested_top_n": 200,
            },
        )
        assert "top_n_written_down" not in result
        assert "top_n=200" in result["source"].replace(" ", "")

    def test_old_server_without_requested_top_n_unchanged(self, monkeypatch):
        result = self._resolve(
            monkeypatch,
            {
                "resolved": ["BTC"],
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 1,
            },
        )
        assert "top_n_written_down" not in result
        assert "top_n=200" in result["source"].replace(" ", "")
