"""Tests for local tool implementations."""

from __future__ import annotations

import os

import pytest
import respx


@pytest.fixture
def mock_api():
    """respx mock of the Keel API: the lock tools must make NO call to it."""
    os.environ["KEEL_API_KEY"] = "test-key"
    os.environ["KEEL_API_URL"] = "https://api.test.usekeel.io"
    with respx.mock(base_url="https://api.test.usekeel.io") as mock:
        yield mock
    os.environ.pop("KEEL_API_KEY", None)
    os.environ.pop("KEEL_API_URL", None)


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

    # RollingUniverseMask v2 made `dollar_volume_slot` required; this source is
    # the v1 interface. PriceDataLoader v1 -> latest changes no interface.
    PINNED_SOURCE = """
Globals(target_timeframe="1d")
Universe(mode="top_volume", top_n=2, resolved=["BTC", "ETH"], resolved_at="2026-07-01T00:00:00Z")
Pipeline([
    PriceDataLoader(),
    Store("ohlcv"),
    RollingUniverseMask(top_n=2),
    Store("universe_mask"),
    Load("ohlcv"),
    EWMA(window=20),
    ApplyUniverseMask(mask_slot="universe_mask"),
    EqualWeightSizer(target_leverage=1.0),
    FillNaN(fill_value=0.0),
])
"""

    def _pinned_lock(self) -> dict[str, int]:
        from keel.tools.local import strategy_lock_generate

        lock = strategy_lock_generate(source=self.PINNED_SOURCE)["component_lock"]
        # Non-vacuity: the bundle's latest is above both pins.
        assert lock["RollingUniverseMask"] > 1 and lock["PriceDataLoader"] > 1
        return {**lock, "RollingUniverseMask": 1, "PriceDataLoader": 1}

    def test_lock_status_decides_breaking_offline_with_the_one_owner(self, mock_api):
        """Drift entries come from the vendored lock_upgrade (the owner keel-api's
        /lock/check runs), with no API call: an unchanged interface is not
        breaking; a v2 that adds a required slot is, and says what is missing."""
        from keel.tools.local import strategy_lock_status

        result = strategy_lock_status(source=self.PINNED_SOURCE, component_lock=self._pinned_lock())
        assert not mock_api.calls
        assert result["status"] == "drift"
        by = {d["component"]: d for d in result["drift"]}
        assert by["PriceDataLoader"]["breaking"] is False
        assert by["PriceDataLoader"]["issues_at_target"] == []
        rum = by["RollingUniverseMask"]
        assert rum["breaking"] is True
        assert [i["code"] for i in rum["issues_at_target"]] == ["MISSING_PARAM"]
        assert rum["interface"]["params_added"] == [
            {"name": "dollar_volume_slot", "required": True, "slot_type": "DollarVolumeSeries"}
        ]

    def test_lock_upgrade_bumps_offline_and_validates_at_the_new_pin(self, mock_api):
        from keel.tools.local import strategy_lock_upgrade

        lock = self._pinned_lock()
        ok = strategy_lock_upgrade(
            source=self.PINNED_SOURCE, component_lock=lock, components=["PriceDataLoader"]
        )
        bad = strategy_lock_upgrade(
            source=self.PINNED_SOURCE, component_lock=lock, components=["RollingUniverseMask"]
        )
        assert not mock_api.calls
        assert ok["upgraded"] == ["PriceDataLoader"] and ok["valid"] is True
        assert ok["component_lock"]["RollingUniverseMask"] == 1  # only the requested pin
        assert bad["upgraded"] == ["RollingUniverseMask"] and bad["valid"] is False
        assert "MISSING_PARAM" in {i["code"] for i in bad["issues"]}
        assert [c["component"] for c in bad["changes"]] == ["RollingUniverseMask"]

    def test_lock_tools_without_a_lock_return_a_fresh_one(self):
        from keel.tools.local import strategy_lock_status, strategy_lock_upgrade

        status = strategy_lock_status(source=self.PINNED_SOURCE)
        up = strategy_lock_upgrade(source=self.PINNED_SOURCE)
        assert status["status"] == "current" and status["drift"] == []
        assert up["upgraded"] == [] and up["valid"] is None
        assert status["component_lock"] == up["component_lock"]


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
            resolve=False,
            source=self.SOURCE_WITH_UNIVERSE,
            mode="top_volume",
            market="perp",
            top_n=20,
        )
        assert "source" in result
        assert result["universe"]["mode"] == "top_volume"

    #: A resolved universe carrying everything `universe set` takes no
    #: argument for — the shape every real strategy has once it can run.
    RESOLVED_WITH_STATE = (
        'Universe(mode="top_volume", market="perp", top_n=30, '
        "min_trailing_notional_proxy=10000000.0, "
        'resolved=["BTC", "ETH"], resolved_at="2026-09-01T00:00:00+00:00", '
        "groups={'core': ['BTC']}, max_leverages={'BTC': 40.0, 'ETH': 25.0})\n"
        'Pipeline([ROC(period=8)], name="s")\n'
    )

    def test_universe_set_keeps_resolved_state_groups_leverages_and_the_floor(self):
        """U-18 (SDK twin): rebuilding the spec from the call's arguments
        deleted every declaration the signature has no argument for. The worst
        of them is `resolved` — `keel universe set` on a resolved strategy
        silently UN-resolved it, and the next `deploy` / `backtest_submit`
        refused the strategy with UNRESOLVED_UNIVERSE, naming nothing the user
        had done. A call that leaves the criteria as declared keeps all of it
        (Q-2435: only a CRITERIA change drops the resolution — next test)."""
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        result = universe_set(
            resolve=False, source=self.RESOLVED_WITH_STATE, mode="top_volume", top_n=30
        )
        universe = parse_strategy(result["source"]).universe

        assert universe.top_n == 30
        assert universe.resolved == ["BTC", "ETH"]
        assert universe.resolved_at == "2026-09-01T00:00:00+00:00"
        assert universe.groups == {"core": ["BTC"]}
        assert universe.max_leverages == {"BTC": 40.0, "ETH": 25.0}
        # The fixture declares the floor under the deprecated alias (the
        # alias arm, DV6b): carried with the same value and written back
        # under the current name — universe_set is a writer.
        assert universe.min_trailing_dollar_volume == 10000000.0
        assert universe.min_trailing_notional_proxy is None

    @pytest.mark.parametrize(
        ("kwargs", "field"),
        [
            ({"mode": "category", "categories": ["defi"]}, "categories"),
            ({"mode": "top_volume", "top_n": 30, "lookback": "90d"}, "lookback"),
            ({"mode": "top_volume", "top_n": 30, "exclusions": ["BTC"]}, "exclusions"),
            ({"mode": "top_volume", "top_n": 30, "inclusions": ["MORPHO"]}, "inclusions"),
            ({"mode": "top_volume", "top_n": 30, "volume_quartiles": ["q4"]}, "volume_quartiles"),
            ({"mode": "manual", "symbols": ["MORPHO"]}, "symbols"),
            ({"mode": "top_volume", "top_n": 50}, "top_n"),
        ],
    )
    def test_universe_set_no_resolve_drops_a_resolution_the_criteria_did_not_produce(
        self, kwargs, field
    ):
        """Q-2435 (SDK twin, `resolve=False` / `keel universe set
        --no-resolve`): a criteria edit writes no `resolved` list, so the
        docstring's promise holds — the next save resolves the NEW criteria
        server-side instead of storing the old list under the new label. The
        floor and the hand-authored groups are carried. Red on the pre-fix
        shared constructor (the carry)."""
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        before = parse_strategy(self.RESOLVED_WITH_STATE).universe
        assert before.resolved == ["BTC", "ETH"]  # non-vacuity: a resolved fixture
        result = universe_set(resolve=False, source=self.RESOLVED_WITH_STATE, **kwargs)
        universe = parse_strategy(result["source"]).universe

        assert getattr(universe, field) != getattr(before, field)  # the edit landed
        assert universe.resolved is None
        assert universe.resolved_at is None
        assert universe.max_leverages is None
        assert universe.groups == {"core": ["BTC"]}
        assert universe.min_trailing_dollar_volume == 10000000.0

    def test_universe_set_replaces_the_floor_when_the_caller_passes_one(self):
        """Carrying is not pinning: an explicit value still wins."""
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        result = universe_set(
            resolve=False,
            source=self.RESOLVED_WITH_STATE,
            mode="top_volume",
            top_n=30,
            min_trailing_dollar_volume=5e6,
        )
        universe = parse_strategy(result["source"]).universe
        assert universe.min_trailing_dollar_volume == 5e6
        assert universe.min_trailing_notional_proxy is None

    def test_universe_set_carries_a_current_name_floor(self):
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        source = self.RESOLVED_WITH_STATE.replace(
            "min_trailing_notional_proxy", "min_trailing_dollar_volume"
        )
        universe = parse_strategy(
            universe_set(resolve=False, source=source, mode="top_volume", top_n=50)["source"]
        ).universe
        assert universe.min_trailing_dollar_volume == 10000000.0
        assert universe.min_trailing_notional_proxy is None

    def test_universe_set_on_a_strategy_with_no_universe_carries_nothing(self):
        """Control: with nothing declared there is nothing to carry, and the
        criteria still land."""
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        result = universe_set(
            resolve=False,
            source='Pipeline([ROC(period=8)], name="s")\n',
            mode="top_volume",
            top_n=10,
        )
        universe = parse_strategy(result["source"]).universe
        assert universe.top_n == 10
        assert universe.resolved is None and universe.max_leverages is None
        assert universe.min_trailing_notional_proxy is None
        assert universe.min_trailing_dollar_volume is None

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
            resolve=False,
            source=COMMENTED_SOURCE,
            mode="top_volume",
            market="perp",
            top_n=10,
            lookback="30d",
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
        result = universe_set(
            resolve=False, source=COMMENTED_SOURCE, mode="manual", symbols=["BTC"]
        )
        assert result["reformatted"] is True
        assert result["universe"]["mode"] == "manual"


class TestUniverseResolveUnderfill:
    """The `top_n` write-down fires on UNDER-SUPPLY, and on nothing else.

    The write-down exists so a permanently unachievable `top_n` cannot fail the
    STALE_UNIVERSE gate forever (cohort Lane U). Measured against the FINAL
    list — what this did until 2026-09-16 (evaluator T3 finding F1) — it also
    fired on a list shortened by the user's own exclusions or by a declared
    band, and both RATCHET: `top_n=30, exclusions=["BTC"]` walked 30 → 29 → 28
    per call, and `top_n=50` under a q1 band was rewritten to the band size.
    That is the silent rewrite of a declared intent D-11 rejected.

    The decision is `top_n_under_supplied` — the one predicate keel-api and the
    rich twin use — reading `supply_before_filters`: the ranked pool the VENUE
    supplied, measured BEFORE bands, floor, exclusions and inclusions.
    """

    SOURCE = """Universe(mode="top_volume", market="perp", top_n=200)
Pipeline([ROC(period=8)], name='s')
"""

    def _resolve(self, monkeypatch, response, source=None):
        from keel.tools.local import universe_resolve

        class _StubClient:
            def post(self, path: str, json: dict):
                return response

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        return universe_resolve(source=source or self.SOURCE)

    # ── arm 1: the venue really cannot supply top_n ──────────────────────────

    def test_top_n_written_down_on_under_supply(self, monkeypatch):
        resolved = [f"S{i}" for i in range(177)]
        result = self._resolve(
            monkeypatch,
            {
                "resolved": resolved,
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 177,
                "requested_top_n": 200,
                "supply_before_filters": 177,
                "warnings": ["venue can supply 177 of 200 requested assets"],
            },
        )
        assert result["top_n_written_down"] == {
            "requested": 200,
            "achievable": 177,
            "reason": "venue can supply 177 of 200 requested assets",
        }
        assert "top_n=177" in result["source"].replace(" ", "")
        # The server's own under-supply sentence passes through untouched; the
        # SDK does not add a third phrasing of the same fact (the CLI renders
        # `top_n_written_down` beside it).
        assert result["warnings"] == ["venue can supply 177 of 200 requested assets"]

    def test_the_write_down_is_idempotent_on_a_second_call(self, monkeypatch):
        """It must CONVERGE: re-resolving the written-down source sees the same
        177-name pool, which now equals top_n, so nothing moves."""
        resolved = [f"S{i}" for i in range(177)]
        response = {
            "resolved": resolved,
            "resolved_at": "2026-08-20T00:00:00+00:00",
            "count": 177,
            "supply_before_filters": 177,
        }
        first = self._resolve(monkeypatch, response)
        second = self._resolve(monkeypatch, response, source=first["source"])

        assert "top_n=177" in second["source"].replace(" ", "")
        assert "top_n_written_down" not in second

    # ── arm 2: the shortfall is the user's own exclusions ────────────────────

    def test_an_exclusion_shortfall_does_not_ratchet_top_n(self, monkeypatch):
        """F1's measured ratchet: the venue supplied all 30 and an exclusion
        removed one. Two calls, and top_n must still be 30 — measured against
        the final list it would read 29, then 28."""
        source = """Universe(mode="top_volume", market="perp", top_n=30, exclusions=["BTC"])
Pipeline([ROC(period=8)], name='s')
"""
        response = {
            "resolved": [f"S{i}" for i in range(29)],
            "resolved_at": "2026-08-20T00:00:00+00:00",
            "count": 29,
            "requested_top_n": 30,
            "supply_before_filters": 30,
        }
        first = self._resolve(monkeypatch, response, source=source)
        second = self._resolve(monkeypatch, response, source=first["source"])

        assert "top_n=30" in second["source"].replace(" ", "")
        assert "top_n_written_down" not in first and "top_n_written_down" not in second

    # ── arm 3: the shortfall is a declared filter — top_n is a CAP (D-11) ────

    def test_a_quartile_under_fill_is_not_written_down(self, monkeypatch):
        """`top_n=50` over a q1 band that can only supply 44. top_n is a cap
        here, not a target; rewriting it to 44 would replace the user's cap
        with today's band size. The server's cap sentence is passed through."""
        source = """Universe(mode="top_volume", market="perp", top_n=50, volume_quartiles=["q1"])
Pipeline([ROC(period=8)], name='s')
"""
        result = self._resolve(
            monkeypatch,
            {
                "resolved": [f"S{i}" for i in range(44)],
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 44,
                "requested_top_n": 50,
                "supply_before_filters": 178,
                "pool_size": 178,
                "band_size": 44,
                "warnings": [
                    "44 assets selected against top_n=50 — top_n is a cap under a "
                    "volume-quartile filter, not a target."
                ],
            },
            source=source,
        )
        assert "top_n=50" in result["source"].replace(" ", "")
        assert "top_n_written_down" not in result
        assert any("cap under a volume-quartile filter" in w for w in result["warnings"])
        # The evidence rides along so an agent can explain the 44 (D-12).
        assert result["pool_size"] == 178 and result["band_size"] == 44

    # ── controls ────────────────────────────────────────────────────────────

    def test_exact_fill_leaves_top_n_alone(self, monkeypatch):
        result = self._resolve(
            monkeypatch,
            {
                "resolved": [f"S{i}" for i in range(200)],
                "resolved_at": "2026-08-20T00:00:00+00:00",
                "count": 200,
                "requested_top_n": 200,
                "supply_before_filters": 200,
            },
        )
        assert "top_n_written_down" not in result
        assert "top_n=200" in result["source"].replace(" ", "")

    def test_an_older_server_without_the_pool_size_never_guesses(self, monkeypatch):
        """No `supply_before_filters` means the shortfall's CAUSE is unknown,
        and a write-down rewrites the user's declaration — so it says what it
        cannot tell instead of guessing."""
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
        assert any("does not report `supply_before_filters`" in w for w in result["warnings"])


class TestUniverseResolveSnapshotLabel:
    def test_as_of_and_snapshot_note_pass_through_when_present(self, monkeypatch):
        """Q-0983: the server's ONE-SNAPSHOT label reaches the caller verbatim;
        an older server that omits it leaves the keys absent, never fabricated."""
        from keel.tools.local import universe_resolve

        payload = {
            "resolved": ["BTC"],
            "resolved_at": "2025-06-01T12:00:00+00:00",
            "as_of": "2025-06-01T12:00:00+00:00",
            "snapshot_note": "One snapshot as of 2025-06-01T12:00:00+00:00: ...",
            "count": 1,
        }

        class _StubClient:
            def __init__(self):
                pass

            def post(self, path: str, json: dict):
                return dict(payload)

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        source = 'Universe(mode="manual", symbols=["BTC"])\nPipeline([ROC(period=8)], name="s")\n'
        result = universe_resolve(source=source)
        assert result["as_of"] == payload["as_of"]
        assert result["snapshot_note"] == payload["snapshot_note"]

        del payload["as_of"], payload["snapshot_note"]
        result = universe_resolve(source=source)
        assert "as_of" not in result and "snapshot_note" not in result


class TestUniverseResolveNotionalFloor:
    @staticmethod
    def _stub(monkeypatch) -> dict:
        captured: dict = {}

        class _StubClient:
            def __init__(self):
                pass

            def post(self, path: str, json: dict):
                captured["body"] = json
                return {"resolved": ["BTC"], "resolved_at": "2026-06-03T12:00:00+00:00", "count": 1}

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        return captured

    @pytest.mark.parametrize(
        # DV6b: the current name, and the deprecated alias (the alias arm) —
        # the same floor, sent under the current name either way.
        "field",
        ["min_trailing_dollar_volume", "min_trailing_notional_proxy"],
    )
    def test_declared_floor_rides_the_request_body(self, monkeypatch, field):
        """Spec 04 §1: the floor is forwarded when declared and absent
        otherwise (the server default is 'no floor', so absence is exact);
        universe_get reports it under the name it was declared with."""
        from keel.tools.local import universe_get, universe_resolve

        captured = self._stub(monkeypatch)
        with_floor = (
            f'Universe(mode="top_volume", market="perp", top_n=20, {field}=10000000)\n'
            "Pipeline([ROC(period=8)], name='s')\n"
        )
        universe_resolve(source=with_floor)
        assert captured["body"]["min_trailing_dollar_volume"] == 10000000
        assert "min_trailing_notional_proxy" not in captured["body"]
        assert universe_get(source=with_floor)["universe"][field] == 10000000
        universe_resolve(
            source='Universe(mode="manual", symbols=["BTC"])\nPipeline([ROC(period=8)], name=\'s\')\n'
        )
        assert "min_trailing_dollar_volume" not in captured["body"]
        assert "min_trailing_notional_proxy" not in captured["body"]

    def test_both_floor_names_are_sent_as_declared(self, monkeypatch):
        """Ambiguous (DV6b): forwarded as declared so the API refuses it —
        this tool never picks a winner."""
        from keel.tools.local import universe_resolve

        captured = self._stub(monkeypatch)
        universe_resolve(
            source=(
                'Universe(mode="top_volume", top_n=20, min_trailing_dollar_volume=1e7, '
                "min_trailing_notional_proxy=2e7)\nPipeline([ROC(period=8)], name='s')\n"
            )
        )
        assert (
            captured["body"]["min_trailing_dollar_volume"],
            captured["body"]["min_trailing_notional_proxy"],
        ) == (1e7, 2e7)


class TestUniverseSetResolvesInTheSameCall:
    """Q-2283 (spec 03 U2): `universe_set` resolves by default — the agent or
    CLI user gets the baked list and the server's note without a second call."""

    NOTE = (
        "Resolved 2 of 3 symbols: BTC, ETH. Dropped xyz:AAPL — listed on Hyperliquid "
        "(HIP-3); Keel does not support HIP-3 markets yet — support is coming soon."
    )
    SOURCE = 'Universe(mode="top_volume", top_n=5)\nPipeline([ROC(period=8)], name="s")\n'

    def test_default_resolves_and_carries_the_note(self, monkeypatch):
        from keel.tools.local import universe_set

        from pipeline_engine.dsl import parse_strategy

        posted: list[dict] = []
        note = self.NOTE

        class _StubClient:
            def post(self, path: str, json: dict):
                posted.append({"path": path, "body": json})
                return {
                    "resolved": ["BTC", "ETH"],
                    "resolved_at": "2026-10-03T00:00:00+00:00",
                    "count": 2,
                    "resolution_note": note,
                    "dropped": ["xyz:AAPL"],
                }

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        result = universe_set(source=self.SOURCE, mode="manual", symbols=["BTC", "ETH", "xyz:AAPL"])

        assert len(posted) == 1 and posted[0]["path"] == "/v1/universe/resolve"
        assert posted[0]["body"]["symbols"] == ["BTC", "ETH", "xyz:AAPL"]
        u = parse_strategy(result["source"]).universe
        assert u.symbols == ["BTC", "ETH", "xyz:AAPL"]  # the user's list is kept
        assert u.resolved == ["BTC", "ETH"]
        assert result["universe"]["resolved"] == ["BTC", "ETH"]
        assert result["resolution_note"] == note
        assert result["dropped"] == ["xyz:AAPL"]

    def test_nothing_tradeable_is_this_calls_refusal(self, monkeypatch):
        from keel.errors import ValidationError
        from keel.tools.local import universe_set

        class _StubClient:
            def post(self, path: str, json: dict):
                raise ValidationError(
                    "None of the 1 requested symbol can be traded on Keel …",
                    error_code="UNIVERSE_NOTHING_TRADEABLE",
                )

        monkeypatch.setattr("keel.client.KeelClient", _StubClient)
        with pytest.raises(ValidationError) as exc:
            universe_set(source=self.SOURCE, mode="manual", symbols=["xyz:AAPL"])
        assert exc.value.error_code == "UNIVERSE_NOTHING_TRADEABLE"

    def test_CONTROL_resolve_false_stays_offline(self, monkeypatch):
        from keel.tools.local import universe_set

        class _NoNetwork:
            def __init__(self):
                raise AssertionError("resolve=False must not touch the API")

        monkeypatch.setattr("keel.client.KeelClient", _NoNetwork)
        result = universe_set(source=self.SOURCE, mode="manual", symbols=["BTC"], resolve=False)
        assert "resolution_note" not in result
        assert result["universe"]["symbols"] == ["BTC"]
