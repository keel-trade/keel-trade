"""Golden-query regression for component-search ranking.

Contract of record: dsl-multi-timeframe-clocks
`specs/02-validator-surface.md` §9 (the mechanism in §9.1, the four-part
fix in §9.2, these assertions in §9.3).

**The bug this pins shut.** Scoring is token overlap, name ×3 + category
×2 + description ×1. Tokenization used to be
``re.findall(r"[a-z0-9]+", name.lower())`` — lowercasing first erases the
case boundaries, so ``TimeframeResampler`` collapsed to the single token
``"timeframeresampler"`` which can never equal a query word. The ×3 name
weight was therefore DEAD for every camelCase component name. Measured on
2026-07-25: all five re-clocking components scored exactly 2 on
``"multi timeframe"`` (description-only), the sort was stable, so the tie
fell to the bundled list's insertion order — alphabetical — and the
platform recommended ``SignalResampler``, the variant with no globals
wiring, over the declaration-backed one. Founder ruling K15 is the exact
opposite preference: prefer the ``Target*`` variants in most places.

Assertions here are RELATIVE ORDER, never absolute rank — absolute ranks
churn with every registry regen and would make this a maintenance tax
instead of a guard.
"""

from __future__ import annotations

import json

import pytest
from keel.data.registry import (
    CLOCK_DIRECTIONS,
    _load_json,
    clock_direction_for_op,
    clock_direction_of,
    rank_components,
    score_component,
    search_components,
    tokenize_name,
    tokenize_text,
)


# The projector pair (spec 01 §8.2) — the coarse → fine operators.
PAIR = ("SignalProjector", "TargetSignalProjector")

# The two legacy project-direction components constrained at v2 and
# tombstoned (spec 01 §8.4). Their ``status="deprecated"`` flags are
# DEFERRED to the Gate-2 commit (spec 02 §8.2, R-4) so the always-on
# DEPRECATED_COMPONENT warning does not fire on the unmeasured legacy
# population pre-Gate-2.
LEGACY = ("SignalTimeframeConverter", "AlignToFrequency")

# The declaration-backed (globals-wired) variants — K15's "in most places".
DECLARATION_BACKED = (
    "TargetSignalProjector",
    "TargetSignalResampler",
    "TargetTimeframeResampler",
)


def _live_components() -> list[dict]:
    """A deep copy of the bundled registry's component list."""
    return json.loads(json.dumps(_load_json("registry.json")["components"]))


def _gate2_components() -> list[dict]:
    """The live registry projected forward to the Gate-2 flag state.

    When the deferred ``status="deprecated"`` flags actually land this
    projection becomes the identity, so every assertion below converges
    onto the live registry with ZERO edits to this file.
    """
    comps = _live_components()
    for comp in comps:
        if comp["name"] in LEGACY:
            comp["status"] = "deprecated"
    return comps


def _order(components: list[dict], query: str) -> list[str]:
    return [c["name"] for c in rank_components(components, query)]


def _rank_of(names: list[str], name: str) -> int:
    assert name in names, f"{name} scored 0 on this query — expected a hit"
    return names.index(name)


# ─── §9.3 golden queries, live registry ──────────────────────────────────


def test_multi_timeframe_ranks_declaration_backed_above_its_explicit_sibling():
    """§9.3 bullet 1 — the K15 preference, live, today.

    This is THE measured bug: before the fix all five re-clocking
    components tied at 2 and `SignalResampler` won on alphabetical
    accident.
    """
    names = _order(_live_components(), "multi timeframe")

    # The declaration-backed variants outrank their explicit siblings.
    assert _rank_of(names, "TargetSignalResampler") < _rank_of(names, "SignalResampler")
    assert _rank_of(names, "TargetTimeframeResampler") < _rank_of(names, "TimeframeResampler")
    assert _rank_of(names, "TargetSignalProjector") < _rank_of(names, "SignalProjector")

    # And the footgun no longer leads.
    assert names[0] != "SignalResampler"


def test_multi_timeframe_surfaces_the_pair_and_the_globals_wired_variants_in_top_six():
    """§9.3 bullet 1 — discoverability of the new pair."""
    top6 = set(_order(_live_components(), "multi timeframe")[:6])
    assert "TargetSignalProjector" in top6
    assert {"TargetSignalResampler", "TargetTimeframeResampler"} <= top6


def test_resample_signal_puts_the_globals_wired_resampler_first():
    """§9.3 bullet 3 — `TargetSignalResampler` above `SignalResampler`."""
    names = _order(_live_components(), "resample signal")
    assert names[0] == "TargetSignalResampler"
    assert _rank_of(names, "TargetSignalResampler") < _rank_of(names, "SignalResampler")


def test_projection_query_ranks_the_pair_above_every_non_legacy_alternative():
    """§9.3 bullet 2, in its pre-Gate-2 form.

    The spec states this bullet as "top result is one of the pair". That
    holds the moment the deferred deprecated flags land (see
    `test_gate2_...` below, which asserts exactly that). Pre-Gate-2 the
    superseded `SignalTimeframeConverter` shell is still `status="active"`
    and its v2 docstring — "Superseded: project coarse signals to a finer
    timeframe with SignalProjector or TargetSignalProjector" — scores it
    top on this very query. So the live assertion is the strongest one
    that is true today: the pair beats everything that is not one of the
    two deferred-deprecated legacy shells.
    """
    names = _order(_live_components(), "project signal to execution timeframe")
    best_pair = min(_rank_of(names, p) for p in PAIR)
    for name in names[:best_pair]:
        assert name in LEGACY, (
            f"{name} outranks the projector pair on a projection query and is "
            f"not one of the deferred-deprecated legacy shells {LEGACY}"
        )
    # Within the pair, the globals-wired one leads.
    assert _rank_of(names, "TargetSignalProjector") < _rank_of(names, "SignalProjector")


# ─── §9.3 golden queries under the Gate-2 deprecated flags ───────────────


def test_gate2_status_partition_sinks_the_legacy_shells_below_every_active_alternative():
    """§9.3 bullet 1's status-partition clause (arms at Gate-2, R-4)."""
    comps = _gate2_components()
    active = {c["name"] for c in comps if c["status"] == "active"}
    for query in ("multi timeframe", "project signal to execution timeframe"):
        names = _order(comps, query)
        for legacy in LEGACY:
            if legacy not in names:
                continue
            below = set(names[names.index(legacy) :])
            assert not (below & active - {legacy}), (
                f"{legacy!r} must sink below every active alternative on "
                f"{query!r}; found active components ranked after it: "
                f"{sorted(below & active - {legacy})}"
            )


def test_gate2_projection_query_top_result_is_one_of_the_pair():
    """§9.3 bullet 2 verbatim, under the Gate-2 flag state."""
    names = _order(_gate2_components(), "project signal to execution timeframe")
    assert names[0] in PAIR
    # K15: the globals-wired one is the default suggested fix.
    assert names[0] == "TargetSignalProjector"


def test_gate2_multi_timeframe_puts_signal_resampler_below_the_projectors():
    """§9.3 bullet 1's "below both projectors" clause."""
    names = _order(_gate2_components(), "multi timeframe")
    for projector in PAIR:
        assert _rank_of(names, projector) < _rank_of(names, "SignalResampler")


# ─── the ordering chain, unit-level ──────────────────────────────────────


def test_camelcase_names_tokenize_on_case_boundaries():
    """§9.2 item 1 — the ×3 name weight is alive.

    The legacy tokenizer produced the single token
    "timeframeresampler", which no query word could ever equal.
    """
    assert tokenize_name("TargetTimeframeResampler") == {
        "target",
        "timeframe",
        "resampler",
    }
    # Free text is prose, not identifiers — it keeps the plain tokenizer.
    assert tokenize_text("signal_transform") == {"signal", "transform"}


def test_acronym_names_survive_tokenization():
    """SPEC DEVIATION, deliberate and measured.

    §9.2 item 1 writes the regex as ``[A-Z][a-z0-9]*|[a-z0-9]+``. That
    shatters acronyms — ``RSI`` → {"r","s","i"} — so the ×3 name weight
    the fix EXISTS to revive would go dead for the ~20 acronym-named
    components instead. Measured under the literal regex: "rsi" returned
    CCI / CMO / EWMATransform with RSI nowhere in the top 3, "roc"
    returned AboveThresholdFilter / ApplyMask / AssetSelect (pure
    alphabetical noise) with ROC absent, "macd" put MACD third behind
    Aroon. A leading ``[A-Z]+(?![a-z])`` alternative keeps capital runs
    intact and serves the fix's stated intent.
    """
    assert tokenize_name("RSI") == {"rsi"}
    assert tokenize_name("MACDHistogram") == {"macd", "histogram"}
    assert tokenize_name("SignalROC") == {"signal", "roc"}
    assert tokenize_name("AnalyticalFDMCombiner") == {"analytical", "fdm", "combiner"}

    for acronym in ("RSI", "MACD", "ROC", "ATR", "OBV"):
        top = [c["name"] for c in search_components(query=acronym.lower(), top_k=3)]
        assert top[0] == acronym, f"{acronym} must lead its own query, got {top}"


def test_name_weight_actually_fires_for_a_camelcase_component():
    comp = {"name": "TargetTimeframeResampler", "category": "", "description": ""}
    assert score_component(comp, tokenize_text("timeframe")) == 3.0


def test_declaration_backed_wins_the_tie_at_equal_score():
    """§9.2 item 2 — the third key of the chain."""
    explicit = {"name": "AaaWidget", "category": "", "description": "clock", "status": "active"}
    backed = {
        "name": "ZzzWidget",
        "category": "",
        "description": "clock",
        "status": "active",
        "declaration_refs": {"target_timeframe": "globals.target_timeframe"},
    }
    ordered = [c["name"] for c in rank_components([explicit, backed], "clock")]
    assert ordered == ["ZzzWidget", "AaaWidget"], (
        "declaration-backed must outrank its explicit sibling on equal "
        "score — otherwise the tie falls to alphabetical accident, which "
        "is the whole bug"
    )


def test_status_partition_beats_score():
    """§9.2 item 2 — a deprecated component sinks regardless of score."""
    deprecated = {
        "name": "Aaa",
        "category": "clock",
        "description": "clock clock clock",
        "status": "deprecated",
    }
    active = {"name": "Zzz", "category": "", "description": "clock", "status": "active"}
    ordered = [c["name"] for c in rank_components([deprecated, active], "clock")]
    assert ordered == ["Zzz", "Aaa"]


def test_zero_score_components_are_dropped():
    comps = [{"name": "Aaa", "category": "", "description": "", "status": "active"}]
    assert rank_components(comps, "nothingmatchesthis") == []


def test_ranking_is_total_and_deterministic():
    """No pair of results may be order-ambiguous — the name key is last
    precisely so the chain is a total order."""
    comps = _live_components()
    first = _order(comps, "signal")
    second = _order(list(reversed(comps)), "signal")
    assert first == second


# ─── one scorer, not two (§9.2 item 3) ───────────────────────────────────


def test_after_before_overlay_uses_the_shared_scorer():
    """The `keel_components_search` after/before overlay carried a VERBATIM
    copy of the scoring block. Two copies of a ranking rule is two
    rankings; this asserts the overlay now delegates."""
    import inspect

    from keel.tools.outcomes import components_search

    source = inspect.getsource(components_search)
    assert "rank_components(results, query)" in source
    # The duplicated block's fingerprints must be gone.
    assert 'findall(r"[a-z0-9]+"' not in source
    assert "* 3.0" not in source


def test_overlay_and_bundled_search_agree_on_order():
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    tool = OUTCOMES["keel_components_search"]
    env = tool.handler({"query": "resample signal", "limit": 5}, ToolContext(api_client=None))
    overlay_names = [r["name"] for r in env.to_envelope()["results"]]
    bundled_names = [c["name"] for c in search_components(query="resample signal", top_k=5)]
    assert overlay_names == bundled_names


# ─── non-clock control set (§9.3 bullet 4) ───────────────────────────────

#: Six non-clock queries guarding the blast radius of the tokenization
#: change.
_NON_CLOCK_CONTROL_QUERIES = (
    "momentum crossover",
    "risk parity",
    "beta hedge",
    "bollinger bands",
    "regime detector",
    "moving average",
)

#: The subset of the control set where the ×3 name weight contributes
#: nothing to the leaders, so the top-3 SET is provably unchanged by
#: fix #1. See the module note on the other three.
_ORDER_STABLE_CONTROL_QUERIES = (
    "bollinger bands",
    "regime detector",
    "moving average",
)


def _legacy_rank(components: list[dict], query: str) -> list[str]:
    """The pre-fix scorer, reproduced exactly, for A/B comparison."""
    import re

    q = set(re.findall(r"[a-z0-9]+", query.lower()))

    def plain(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    scored = []
    for comp in components:
        score = (
            len(q & plain(comp.get("name", ""))) * 3.0
            + len(q & plain(comp.get("category", ""))) * 2.0
            + len(q & plain(comp.get("description", ""))) * 1.0
        )
        if score > 0:
            scored.append((score, comp))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c["name"] for _, c in scored]


@pytest.mark.parametrize("query", _NON_CLOCK_CONTROL_QUERIES)
def test_control_queries_never_lose_a_result(query):
    """Recall is monotone under the tokenization change.

    CamelCase splitting can only ADD name tokens, so a component that
    scored > 0 before must still score > 0. Measured across 20 probe
    queries at implementation time: 0 lost results, 5 queries gained
    some. This is the honest blast-radius guard — see the next test for
    why "top-3 unchanged" cannot be asserted for the whole set.
    """
    comps = _live_components()
    assert set(_legacy_rank(comps, query)) <= set(_order(comps, query))


@pytest.mark.parametrize("query", _ORDER_STABLE_CONTROL_QUERIES)
def test_control_queries_top_three_unchanged(query):
    """§9.3 bullet 4 for the queries where it is a true statement.

    SPEC DEVIATION, recorded deliberately. §9.3 asks for a 6-query
    non-clock control set "asserting their top-3 sets are unchanged by
    the tokenization change". Measured over 20 representative non-clock
    queries, 14 DID change — because the ×3 name weight was dead and the
    old top-3 was alphabetical noise. "risk parity" used to return
    AdverseVolCap / FillNaN / LeverageCap and now returns
    RiskParityAllocator / MarketRiskScaler / RiskBudgetSizer; "beta
    hedge" gained BetaEstimator and RollingBeta. Pinning "unchanged"
    across the board would pin the bug. So the set is split: all six
    control queries assert monotone recall (above), and the three whose
    leaders never depended on a name match assert the literal top-3-set
    invariance.
    """
    comps = _live_components()
    assert set(_legacy_rank(comps, query)[:3]) == set(_order(comps, query)[:3])


# ─── clock_direction filter (§9.2 item 4) ────────────────────────────────


def test_clock_direction_vocabulary():
    assert CLOCK_DIRECTIONS == ("keep", "synth", "resample", "project")
    assert clock_direction_for_op(None) == "keep"
    assert clock_direction_for_op("coarsen") == "resample"
    assert clock_direction_for_op("project") == "project"
    assert clock_direction_for_op("synth") == "synth"


def test_unknown_clock_op_raises_rather_than_defaulting_to_keep():
    """A new transfer op must be classified explicitly. Silently calling
    it `keep` would hide a clock-changing component from the filter."""
    with pytest.raises(ValueError, match="Unknown clock_transfer op"):
        clock_direction_for_op("interpolate")


def test_clock_direction_filter_returns_exactly_the_transform_set():
    project = {c["name"] for c in search_components(clock_direction="project", top_k=50)}
    resample = {c["name"] for c in search_components(clock_direction="resample", top_k=50)}
    synth = {c["name"] for c in search_components(clock_direction="synth", top_k=50)}

    assert set(PAIR) <= project
    assert set(LEGACY) <= project
    assert resample == {
        "RealizedVolatility",
        "SignalResampler",
        "TargetSignalResampler",
        "TimeframeResampler",
        "TargetTimeframeResampler",
    }
    assert synth == {"PriceDataLoader", "FundingDataLoader"}
    assert not (project & resample)


def test_clock_direction_filter_composes_with_query():
    names = [
        c["name"]
        for c in search_components(clock_direction="project", query="multi timeframe", top_k=10)
    ]
    assert names, "the project-direction components must be reachable by query"
    assert set(names) <= set(PAIR) | set(LEGACY)


def test_unknown_clock_direction_is_rejected():
    with pytest.raises(ValueError, match="Unknown clock_direction"):
        search_components(clock_direction="downsample")


def test_search_entries_expose_clock_direction_only_where_it_distinguishes():
    entries = {c["name"]: c for c in search_components(query="multi timeframe", top_k=10)}
    assert entries["TargetSignalProjector"]["clock_direction"] == "project"
    assert entries["TargetTimeframeResampler"]["clock_direction"] == "resample"
    plain = search_components(keyword="RSI", top_k=1)
    assert "clock_direction" not in plain[0]


def test_emitted_clock_direction_is_fresh():
    """`clock_direction` is DERIVED at regen from `clock_transfer`; a
    hand-edited value is rejected here (the `flow_config` precedent).

    Regenerate with:
        PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py
    """
    for comp in _load_json("registry.json")["components"]:
        assert "clock_direction" in comp, f"{comp['name']} missing clock_direction"
        assert comp["clock_direction"] == clock_direction_of(comp), (
            f"{comp['name']}: emitted clock_direction "
            f"{comp['clock_direction']!r} does not match its clock_transfer"
        )
        for ver, spec in comp.get("versions", {}).items():
            assert spec["clock_direction"] == clock_direction_of(spec), (
                f"{comp['name']} v{ver}: stale clock_direction"
            )


def test_clock_transfer_is_present_for_every_non_keep_component():
    non_keep = [
        c for c in _load_json("registry.json")["components"] if c["clock_direction"] != "keep"
    ]
    assert len(non_keep) == 11, "spec 01 §8.1 — the transform set has 11 members"
    for comp in non_keep:
        assert comp["clock_transfer"]["op"] in ("synth", "coarsen", "project")
        assert comp["clock_transfer"]["src"]


# ─── the agent-facing surface (§9.2 items 4 and 6) ───────────────────────


def test_search_tool_schema_exposes_the_clock_direction_filter():
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    tool = OUTCOMES["keel_components_search"]
    prop = tool.input_schema["properties"]["clock_direction"]
    assert prop["enum"] == list(CLOCK_DIRECTIONS)
    # R7 §5.5 — the one distinguishing sentence, not prose.
    assert "resample raw data fine" in tool.description
    assert "project signals coarse" in tool.description
    assert "`clock_direction`" in tool.description


def test_compose_help_renders_the_clock_block_from_the_registration_surface():
    """§9.2 item 6 — sourced from `clock_transfer`, never regex-mined."""
    from keel.data.registry import get_component_detail
    from keel.tools.outcomes.components_help import _shape_detail

    block = _shape_detail(get_component_detail("TargetSignalProjector"))["clock"]
    assert block["direction"] == "project"
    assert "declaration-backed" in block["clock_source"]
    assert "globals.target_timeframe" in block["clock_source"]
    assert "coarse → fine" in block["legality"]
    # R7-I13 — lookbacks are bars; the clock never rescales them.
    assert "BARS" in block["never"]

    explicit = _shape_detail(get_component_detail("SignalProjector"))["clock"]
    assert "explicit" in explicit["clock_source"]

    coarsen = _shape_detail(get_component_detail("TimeframeResampler"))["clock"]
    assert coarsen["direction"] == "resample"
    assert "fine → coarse" in coarsen["legality"]
    assert "bar_offset" in coarsen["bar_offset"]


def test_compose_help_omits_the_clock_block_for_clock_less_components():
    from keel.data.registry import get_component_detail
    from keel.tools.outcomes.components_help import _shape_detail

    assert "clock" not in _shape_detail(get_component_detail("RSI"))
