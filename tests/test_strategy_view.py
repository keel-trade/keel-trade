"""The derived strategy `view` (PLAN §4.2) — the M0 guard.

Three things are proved here:

1. **The corpus guard.** Over every checked-in library graph AND the
   founder's HRP fixture, `build_view` yields a header and a markdown
   block, and the markdown names every component in the graph exactly
   once per occurrence — branches, nested sub-pipelines and factory
   bodies included. The expectation is computed by walkers written IN
   THIS FILE, never by importing the module under test: a seeded defect
   in `_strategy_view`'s own walk must move one side of the comparison
   and not both (2026-08-25 lesson).
2. **The size cases.** The event picks the size, the model never does.
3. **The card contract.** `build_view` reproduces the checked-in card
   fixtures' `view` blocks field for field — those fixtures are what
   `card-strategy.js`'s `normalizeView()` consumes, so a drift here is
   a drift in what a user sees.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from keel.tools.outcomes._strategy_view import build_view, choose_size


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
LIBRARY_ENTRIES = sorted((REPO_ROOT / "libs/strategy_library/data/entries").glob("*/graph.json"))
HRP_GRAPH = SDK_ROOT / "tests/fixtures/cards/hrp_plus_breakout_w10.graph.json"
HRP_NAME = "hrp_plus_breakout_w10"
CARD_FIXTURES = SDK_ROOT / "tests/fixtures/cards"


# ─── Independent walkers (deliberately NOT the module's) ───────────────
#
# These read the raw JSON the way a person would. They exist so the
# guard's expectation and the guard's subject are computed by different
# code: seeding a defect in `_strategy_view`'s walk reds the assertion
# instead of silently moving the target with it.


def _blocks(graph: dict) -> list:
    for key in ("blocks", "steps"):
        if isinstance(graph.get(key), list):
            return graph[key]
    return []


def _components(blocks: list) -> list[str]:
    """Every component occurrence, in reading order."""
    out: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "component":
            out.append(str(block.get("component") or block.get("name")))
        elif kind == "parallel":
            for branch in (block.get("branches") or {}).values():
                out.extend(_components(branch))
        elif kind == "pipeline":
            out.extend(_components(block.get("steps") or []))
    return out


def _all_components(graph: dict) -> list[str]:
    out = _components(_blocks(graph))
    for factory in graph.get("factories") or []:
        if isinstance(factory, dict):
            out.extend(_components(factory.get("body") or factory.get("steps") or []))
    return out


def _has_parallel(blocks: list) -> bool:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "parallel":
            return True
        for branch in (block.get("branches") or {}).values():
            if _has_parallel(branch):
                return True
        if _has_parallel(block.get("steps") or []):
            return True
    return False


def _named(markdown: str, component: str) -> int:
    """Occurrences of a component NAME in the markdown, word-bounded."""
    count = 0
    start = 0
    while True:
        index = markdown.find(component, start)
        if index < 0:
            return count
        before = markdown[index - 1] if index else " "
        after_index = index + len(component)
        after = markdown[after_index] if after_index < len(markdown) else " "
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            count += 1
        start = index + len(component)


def _corpus() -> list[tuple[str, dict]]:
    graphs = [(path.parent.name, json.loads(path.read_text())) for path in LIBRARY_ENTRIES]
    graphs.append((HRP_NAME, json.loads(HRP_GRAPH.read_text())))
    return graphs


CORPUS = _corpus()


# ─── Non-vacuity: what the corpus actually holds ───────────────────────
#
# Both quantities are read from the FIXTURES with the walkers above, so
# no seed inside `_strategy_view` can move them. A guard that passed
# over an empty corpus, or over one with no branches to walk, would be
# proving nothing.


def test_corpus_is_large_enough_to_prove_anything():
    assert len(CORPUS) > 15, f"corpus collapsed to {len(CORPUS)} graphs"
    assert all(_blocks(graph) for _, graph in CORPUS), "a corpus graph has no blocks"


def test_corpus_contains_branches_and_a_deep_one():
    with_parallel = [name for name, graph in CORPUS if _has_parallel(_blocks(graph))]
    assert with_parallel, "no corpus graph has a parallel — the branch walk is untested"
    assert len(with_parallel) >= 10, f"only {len(with_parallel)} branching graphs"
    hrp = dict(CORPUS)[HRP_NAME]
    assert len(_all_components(hrp)) > 40, "the HRP extreme is no longer extreme"


# ─── The M0 guard ──────────────────────────────────────────────────────
#
# SEED (proof it can fail): in `keel/tools/outcomes/_strategy_view.py`,
# in `_render_list`, change the branch chain's
#     `for child in children`
# to
#     `for child in children[:1]`
# — the branch walk then names only each branch's first block. This test
# reds on every graph whose branches carry more than one block; the
# control arm below (a flat three-block graph) stays green, which is
# what proves the verdict reacts to the branch walk and not to the
# builder always failing.


@pytest.mark.parametrize(("name", "graph"), CORPUS, ids=[name for name, _ in CORPUS])
def test_view_names_every_component_exactly_once_per_occurrence(name, graph):
    view = build_view(graph, {"name": graph.get("name") or name}, full=True)
    assert view is not None, f"{name}: no view built"
    assert view["header"] is not None
    assert isinstance(view["markdown"], str) and view["markdown"].strip()

    expected = Counter(_all_components(graph))
    assert expected, f"{name}: no components to name"
    markdown = view["markdown"]
    for component, occurrences in expected.items():
        assert _named(markdown, component) == occurrences, (
            f"{name}: {component} named {_named(markdown, component)}x, "
            f"occurs {occurrences}x in the graph"
        )


def test_control_arm_flat_graph_is_unaffected_by_the_branch_walk():
    """Control: a graph with no branches at all.

    Green under the seed above and without it — so a red in the guard
    means the BRANCH walk moved, not that the builder broke.
    """
    graph = {
        "blocks": [
            {"id": "b1", "type": "component", "component": "PriceDataLoader", "params": {}},
            {"id": "b2", "type": "component", "component": "ROC", "params": {"period": 20}},
            {"id": "b3", "type": "component", "component": "EqualWeightSizer", "params": {}},
        ]
    }
    view = build_view(graph, {"name": "flat"}, full=True)
    for component in ("PriceDataLoader", "ROC", "EqualWeightSizer"):
        assert _named(view["markdown"], component) == 1


def test_markdown_ends_with_the_link_line_and_names_its_remainder():
    """Nothing is hidden without a count, and every rendering ends with
    the same link (PLAN §4.6 / §4.8)."""
    graph = json.loads(HRP_GRAPH.read_text())
    view = build_view(
        graph,
        {"name": "hrp", "current_sequence": 2},
        mobile=True,
        url="https://app.usekeel.io/strategies/str_hrp/edit",
    )
    lines = view["markdown"].rstrip().splitlines()
    assert lines[-1] == "View in Keel: https://app.usekeel.io/strategies/str_hrp/edit"
    assert view["url_line"] == lines[-1]
    # The budget truncated this one; the remainder is named, not dropped.
    assert any(line.startswith("… ") and line.endswith("more blocks") for line in lines)
    # And the parallel that did not fit says how much it stands for.
    assert any("Parallel · 6 branches · 45 blocks" in line for line in lines)


# ─── The four sizes (PLAN §4.3, V-13) ──────────────────────────────────


def _change(*, added=0, removed=0, changed=0, total=8):
    return {
        "added": [{"id": f"a{i}", "component": "X", "path": []} for i in range(added)],
        "removed": [{"id": f"r{i}", "component": "X", "path": []} for i in range(removed)],
        "changed": [
            {"id": f"c{i}", "component": "X", "path": [], "params": {"p": {"old": 1, "new": 2}}}
            for i in range(changed)
        ],
        "reordered": [],
        "touched": added + removed + changed,
        "total": total,
    }


def test_size_no_change_is_the_structure():
    assert choose_size(None, 8) == ("structure", False)
    assert choose_size(_change(total=8), 8) == ("structure", False)


def test_size_one_blocks_params_is_a_receipt():
    assert choose_size(_change(changed=1, total=8), 8) == ("receipt", False)


def test_size_a_few_hunks_is_the_change():
    assert choose_size(_change(changed=1, added=1, total=8), 8) == ("change", False)


def test_size_beyond_eight_hunks_or_half_the_blocks_is_the_marked_structure():
    # more than eight hunks, on a big pipeline
    assert choose_size(_change(changed=9, total=40), 40) == ("structure", True)
    # more than half the blocks, on a small one
    assert choose_size(_change(changed=2, added=3, total=8), 8) == ("structure", True)


# ─── Declarations (Q-1707) ─────────────────────────────────────────────


def _decl(**keys):
    """A declaration diff block as `_declarations.diff_declarations` emits it."""
    return {"execution": {k: {"a": v[0], "b": v[1]} for k, v in keys.items()}}


def test_size_one_declaration_key_is_a_receipt_like_one_param():
    """Q-1707: a lone `Execution(buffer_threshold=…)` edit is the same SIZE
    of event as a lone `ROC(period=…)` edit — before this, a change with no
    block hunk scored `touched == 0` and fell all the way back to the
    structure, saying nothing had happened."""
    # SEED: in `choose_size`, drop `+ count_declarations(...)` from the
    # `touched` fallback — this reads `structure` again, which IS the
    # shipped defect.
    change = {
        "added": [],
        "removed": [],
        "changed": [],
        "reordered": [],
        "declarations": _decl(buffer_threshold=(0.1, 0.05)),
        "total": 8,
    }
    assert choose_size(change, 8) == ("receipt", False)
    # Not vacuous: the SAME block with its declarations removed is the
    # no-change case, so the verdict above reacts to the declarations and
    # not to the shape of the dict.
    assert choose_size({**change, "declarations": {}}, 8) == ("structure", False)


def test_size_a_declaration_beside_a_block_is_the_change():
    """The mixed save: two things moved, so it is not a one-line receipt."""
    # SEED: as above — `touched` collapses to 1 and this reads `receipt`.
    change = {**_change(changed=1, total=8), "declarations": _decl(bar_offset=("9h", "21h"))}
    change.pop("touched")
    assert choose_size(change, 8) == ("change", False)


def test_marks_are_block_arithmetic_only():
    """A declaration has no block to mark, so it never pushes the size into
    the marked full form — which would draw the whole pipeline and mark
    nothing in it."""
    # SEED: in `choose_size`, change the marks arm to read `touched`
    # instead of `blocks_touched` — six declaration keys over a 4-block
    # pipeline then reads `("structure", True)`.
    many = {
        "added": [],
        "removed": [],
        "changed": [],
        "reordered": [],
        "declarations": {
            "execution": {
                "rebalance": {"a": "buffered", "b": "every_bar"},
                "buffer_threshold": {"a": 0.1, "b": None},
                "buffer_mode": {"a": "relative", "b": None},
                "rebalance_method": {"a": "to_edge", "b": "to_center"},
            },
            "globals": {"bar_offset": {"a": "9h", "b": "21h"}},
            "universe": {"top_n": {"a": 30, "b": 20}},
        },
        "total": 4,
    }
    assert choose_size(many, 4) == ("change", False)
    # Not vacuous: the same COUNT of block hunks on the same pipeline is
    # the marked structure, so the arm is live and only the declarations
    # are exempt from it.
    assert choose_size(_change(changed=6, total=4), 4) == ("structure", True)


def test_the_markdown_names_the_declaration_and_counts_blocks_honestly():
    """`view.markdown` is what a text host relays verbatim, so the one
    place a declaration edit must appear is here."""
    # SEED: in `_change_markdown`, delete the `declaration_rows(...)` loop
    # — the `~ Execution` line disappears while the block line stays.
    envelope = json.loads((CARD_FIXTURES / "strategy_adx.envelope.json").read_text())
    change = {**_change(changed=1, total=5), "declarations": _decl(buffer_threshold=(0.1, 0.05))}
    change["touched"] = 2
    view = build_view(envelope["metadata"]["graph"], envelope["metadata"], change=change)
    lines = view["markdown"].splitlines()
    assert "~ Execution · buffer   0.1 → 0.05" in lines, lines
    # The block tally stays BLOCK arithmetic: one block moved out of five,
    # and the declaration is not one of them.
    assert "  4 blocks unchanged" in lines, lines
    # Not vacuous: the same view without declarations renders the block
    # hunk and the same tally, so the assertions above read the new loop.
    plain = build_view(
        envelope["metadata"]["graph"], envelope["metadata"], change=_change(changed=1, total=5)
    )
    assert not any("Execution" in line for line in plain["markdown"].splitlines()[2:])
    assert "  4 blocks unchanged" in plain["markdown"].splitlines()


def test_size_rides_the_view_and_marks_only_when_earned():
    graph = json.loads((CARD_FIXTURES / "strategy_adx.envelope.json").read_text())
    metadata = graph["metadata"]
    big = _change(changed=3, added=3, total=8)
    view = build_view(metadata["graph"], metadata, change=big)
    assert view["size"] == "structure"
    assert view["marks"] is True
    small = _change(changed=1, total=8)
    assert "marks" not in build_view(metadata["graph"], metadata, change=small)


# ─── The card contract ─────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", ["strategy_adx", "strategy_hrp", "strategy_change"])
def test_build_view_reproduces_the_card_fixture_field_for_field(fixture):
    """The fixtures are what `card-strategy.js` consumes.

    `status_text` is excluded and only it: the three fixtures carry
    hand-authored card copy ("Draft · from Nebula") that two identical
    metadata blocks cannot both derive, so it is asserted separately
    below against the rule the card itself uses.
    """
    envelope = json.loads((CARD_FIXTURES / f"{fixture}.envelope.json").read_text())
    expected = envelope["view"]
    built = build_view(
        envelope["metadata"]["graph"],
        envelope["metadata"],
        change=expected.get("change"),
        evidence=expected.get("evidence"),
        url=envelope["hero_url"],
    )
    assert built is not None
    drifted = {
        key: (value, built.get(key))
        for key, value in expected.items()
        if key != "status_text" and built.get(key) != value
    }
    assert not drifted, f"{fixture}: {sorted(drifted)}"
    assert isinstance(built["markdown"], str) and built["markdown"].strip()


def test_status_text_follows_the_cards_own_rule():
    graph = json.loads((CARD_FIXTURES / "strategy_adx.envelope.json").read_text())["metadata"]
    view = build_view(graph["graph"], {**graph, "updated_at": "2026-09-20T11:04:00Z"})
    assert view["status_text"] == "Draft · updated Sep 20"
    # No timestamp ⇒ the state alone; never a raw stamp, never a guess.
    assert build_view(graph["graph"], graph)["status_text"] == "Draft"


def test_the_header_carries_no_ids_and_no_dates():
    """V-2 / the receipt rule: the header is registry nouns and counts."""
    envelope = json.loads((CARD_FIXTURES / "strategy_hrp.envelope.json").read_text())
    header = build_view(envelope["metadata"]["graph"], envelope["metadata"])["header"]
    text = json.dumps(header)
    assert "str_" not in text and "btr_" not in text
    assert "resolved_at" not in text
    assert "2026-" not in text and "2024-" not in text


def test_no_graph_no_view():
    """A source the server could not parse has no view to show — and
    says so by absence rather than by an empty shell."""
    assert build_view(None, {"name": "x"}) is None
    assert build_view({}, {"name": "x"}) is None
    assert build_view({"blocks": []}, {"name": "x"}) is None


# ─── The text surfaces (PLAN §4.6 / M1) ────────────────────────────────


def _view_envelope():
    envelope = json.loads((CARD_FIXTURES / "strategy_adx.envelope.json").read_text())
    view = build_view(
        envelope["metadata"]["graph"],
        envelope["metadata"],
        url=envelope["hero_url"],
    )
    return {"run_id": "str_adx", "hero_url": envelope["hero_url"], "view": view}


def test_mcp_text_block_is_the_markdown_and_the_envelope_stays_reachable():
    """A text host reads ONE text block. It was the whole JSON envelope
    (00 §2.2); it is now the rendering, with the envelope still machine
    -reachable as structuredContent — which is also where the card's
    host adapter looks."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    envelope = _view_envelope()
    result = view_tool_result(json.dumps(envelope))
    # The markdown, then the one operational line a strategy view always
    # derives: its execution as DSL a model can copy (Q-1846).
    assert [block.text for block in result.content] == [
        envelope["view"]["markdown"].rstrip()
        + "\n\nexecution: Execution(rebalance='buffered', buffer_threshold=0.2, "
        "buffer_mode='relative', rebalance_method='to_edge')"
    ]
    assert result.structured_content["view"]["size"] == "structure"
    assert result.structured_content["hero_url"] == envelope["hero_url"]


@pytest.mark.parametrize(
    ("execution", "expected"),
    [
        # The R3 card read "Buffered 0.2" for exactly this sparse graph dict.
        (
            {"rebalance": "buffered", "buffer_threshold": 0.2},
            "Execution(rebalance='buffered', buffer_threshold=0.2, "
            "buffer_mode='relative', rebalance_method='to_center')",
        ),
        # A template's to_edge reads differently from the default to_center.
        (
            {"rebalance": "buffered", "buffer_threshold": 0.1, "rebalance_method": "to_edge"},
            "Execution(rebalance='buffered', buffer_threshold=0.1, "
            "buffer_mode='relative', rebalance_method='to_edge')",
        ),
        # Control: a mode's irrelevant params never appear; a mode-free
        # param only when it differs from its default.
        ({"rebalance": "every_bar", "buffer_threshold": 0.2}, "Execution(rebalance='every_bar')"),
        (
            {"rebalance": "every_bar", "min_trade_size": 0.01},
            "Execution(rebalance='every_bar', min_trade_size=0.01)",
        ),
    ],
)
def test_execution_dsl_names_every_param_in_effect(execution, expected):
    """Q-1846: an agent read "Buffered 0.2" and wrote `Execution(buffer=0.2)`.

    SEED (2026-09-23, reverted): drop the mode-default back-fill in
    `execution_dsl` (`value = default`) — both buffered arms red (no
    buffer_mode / rebalance_method), the every_bar controls stay green."""
    from keel.tools.outcomes._strategy_view import execution_dsl

    assert execution_dsl(execution) == expected


def test_execution_dsl_round_trips_through_the_parser():
    """The line is valid DSL: the parser accepts it and reads back the same
    declaration — the point is that an agent can paste it."""
    from keel.tools.outcomes._strategy_view import execution_dsl

    from pipeline_engine.dsl import parse_strategy

    line = execution_dsl({"rebalance": "buffered", "buffer_threshold": 0.2})
    src = (
        "Globals(target_timeframe='1d')\n"
        "Universe(mode='manual', symbols=['BTC'])\n"
        f"{line}\n"
        "Pipeline([PriceDataLoader(), ROC(period=20), "
        "ForecastWeightNormalizer(target_leverage=1.0)], name='x')\n"
    )
    parsed = parse_strategy(src).execution
    assert parsed.rebalance == "buffered" and parsed.buffer_threshold == 0.2
    assert parsed.buffer_mode == "relative" and parsed.rebalance_method == "to_center"


def test_mcp_text_block_is_untouched_without_a_view():
    """Control arm: every other envelope — errors included — keeps the
    exact JSON string it had before as its text block, byte for byte
    (Q-1785 adds the envelope as structuredContent beside it)."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    # A view with no markdown is not a view worth promoting.
    empty = json.dumps({"view": {"markdown": "   "}})
    for payload in ('{"error": {"code": "not_found"}}', '{"run_id": "str_x"}', empty):
        result = view_tool_result(payload)
        assert [block.text for block in result.content] == [payload]
        assert result.structured_content == json.loads(payload)
    text_only = view_tool_result("not json")
    assert [b.text for b in text_only.content] == ["not json"]
    assert text_only.structured_content is None


def test_view_tools_infer_no_output_schema():
    """A view-carrying tool returns a ToolResult, so an inferred
    `{"result": "<string>"}` output schema would tell a strict client to
    expect the wrong shape; the published schema is the declared one
    (Q-1788, test_output_schemas)."""
    from fastmcp.tools.function_tool import FunctionTool
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import _make_param_synthesized_handler
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS

    _bootstrap()
    for name in ("keel_strategy_get", "keel_backtest_summarize"):
        tool = OUTCOMES[name]
        fn = _make_param_synthesized_handler(tool, frozenset({tool.toolset}))
        schema = FunctionTool.from_function(fn, name=name).output_schema
        if name in VIEW_TOOLS:
            assert schema is None, f"{name} must not infer an output schema"
        else:
            assert schema is not None, f"{name} lost its output schema"


def test_cli_human_format_prints_the_markdown_first():
    import click
    from click.testing import CliRunner
    from keel.tools.outcomes import _cli_adapter
    from keel.tools.outcomes._base import OutcomeResult

    envelope = _view_envelope()
    result = OutcomeResult(
        run_id="str_adx",
        hero_url=envelope["hero_url"],
        extra={"view": envelope["view"], "metadata": {"name": "adx_trend_crypto"}},
    )

    @click.command()
    def human():
        _cli_adapter._render(result, "human")

    @click.command()
    def as_json():
        _cli_adapter._render(result, "json")

    out = CliRunner().invoke(human).output
    assert out.startswith("**adx_trend_crypto**")
    # The block list, not the nested graph object printed as JSON.
    assert '"blocks"' not in out
    # The link is still the last clickable line.
    assert out.rstrip().endswith(envelope["hero_url"])

    parsed = json.loads(CliRunner().invoke(as_json).output)
    assert parsed["view"]["structure"]["blocks"], "--format json keeps the whole view"


# ── The Code tab's source (Q-1687) ──────────────────────────────────────────
#
# Founder, on a fullscreen card: "code desnt show on code tab". It never had —
# `renderFullscreen` drew a hardcoded stub and the view carried nothing to
# show. The view now carries the DSL, capped, and `card-strategy.js`'s
# `normalizeView()` projects it through.


def _flat_graph() -> dict:
    return {
        "blocks": [
            {"id": "b1", "type": "component", "component": "PriceDataLoader", "params": {}},
            {"id": "b2", "type": "component", "component": "ROC", "params": {"period": 20}},
        ]
    }


def test_the_view_carries_the_source_the_code_tab_renders():
    from keel.tools.outcomes._strategy_view import build_view as bv

    src = "Globals(target_timeframe='1d')\nPipeline(\n  PriceDataLoader(),\n)"
    view = bv(_flat_graph(), {"name": "flat"}, source=src)
    assert view["source"] == {"text": src, "lines": 4, "truncated": False}


def test_a_view_built_without_a_source_carries_no_source_key():
    """Control: the field appears because a source was passed, not always."""
    view = build_view(_flat_graph(), {"name": "flat"})
    assert "source" not in view
    for empty in (None, "", "   \n  ", 17, {"not": "a string"}):
        assert "source" not in build_view(_flat_graph(), {"name": "flat"}, source=empty)


def test_the_source_does_not_change_the_markdown_every_text_host_relays():
    """The source is for the card; the prose an agent reads is untouched."""
    graph = _flat_graph()
    without = build_view(graph, {"name": "flat"}, url="https://app.usekeel.io/x")
    with_src = build_view(
        graph, {"name": "flat"}, url="https://app.usekeel.io/x", source="Pipeline()"
    )
    assert with_src["markdown"] == without["markdown"]


def test_a_long_source_is_capped_on_a_line_boundary_and_says_what_is_missing():
    from keel.tools.outcomes._strategy_view import MAX_SOURCE_CHARS, source_block

    text = "\n".join(f"step_{i} = ROC(period={i})" for i in range(4000))
    assert len(text) > MAX_SOURCE_CHARS, "the fixture must actually exceed the cap"

    block = source_block(text)
    assert block["truncated"] is True
    assert len(block["text"]) <= MAX_SOURCE_CHARS
    # Whole lines only — the tab never ends mid-token.
    assert not block["text"].endswith("\n")
    assert block["text"].splitlines()[-1] in text.splitlines()
    # And it says how much is missing, rather than passing a part off as whole.
    assert block["lines"] == 4000
    assert 0 < block["shown_lines"] < block["lines"]


def test_a_source_at_the_cap_is_not_marked_truncated():
    """The boundary itself, so `truncated` tracks the cap and not the size."""
    from keel.tools.outcomes._strategy_view import MAX_SOURCE_CHARS, source_block

    exact = "x" * MAX_SOURCE_CHARS
    assert source_block(exact)["truncated"] is False
    assert source_block(exact + "y")["truncated"] is True


@pytest.mark.parametrize(
    ("symbols", "expected"),
    [
        (["HYPE"], "Manual basket · 1 asset · HL perps"),
        (["BTC", "ETH"], "Manual basket · 2 assets · HL perps"),
        ([], "Manual basket · HL perps"),
    ],
)
def test_manual_basket_chip_counts_in_english(symbols, expected):
    """R4: a one-asset basket read "Manual basket · 1 assets"."""
    from keel.tools.outcomes._strategy_view import universe_block

    block = universe_block({"mode": "manual", "symbols": symbols, "market": "perp"})
    assert block is not None
    assert block["label"] == expected
    assert block["total"] == len(symbols)
