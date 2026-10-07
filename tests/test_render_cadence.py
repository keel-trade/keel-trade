"""Render-cadence contracts — Lane 1 (O + A) of the mcp-strategy-view build.

`projects/fable/mcp-strategy-view/RENDER-CADENCE-BUILD.md` §2 is the
contract of record; §6 tasks O.1–O.4 and A.1–A.8 are the rows these
guards cover. Every guard here carries a `# SEED:` comment naming the
one-line edit that reds it and a non-vacuity assertion reading a
quantity that seed cannot move.

Ledger: Q-1688 (backtest visuals + comparison), Q-1689 (dry-run
cadence), Q-1690 (copy).
"""

from __future__ import annotations

import json

import pytest
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


# ── O.3 — `keel_backtest_compare` on the listed profile ───────────────
#
# Guard: `test_policy_scan.py::test_listed_surface_is_exactly_the_allow_list`
# (derives from LISTED_PROFILE_TOOLS, so it moves with this) plus the
# policy scan over the new listed copy. The rows below are the
# non-vacuity half the derived test cannot give: the exact size of the
# set, and the substitutions that make the copy listable at all.


def test_the_listed_profile_carries_the_comparison_tool():
    """Decision #19: without `keel_backtest_compare` on the listed
    profile there is NO comparison tool on claude.ai or ChatGPT, and the
    set-of-runs rendering the cadence build exists for has nowhere to
    land.

    # SEED: drop "keel_backtest_compare" from LISTED_PROFILE_TOOLS in
    # keel/tools/outcomes/_toolsets.py — this and the size row go red.
    """
    assert "keel_backtest_compare" in LISTED_PROFILE_TOOLS
    # Non-vacuity: the size is stated, so a rename or an accidental
    # widening of the allow-list cannot pass by swapping one name for
    # another. 26 before this build, 27 after, 28 with restore (spec 03 §2.4),
    # 29 with keel_backtest_positions (Q-1893).
    assert len(LISTED_PROFILE_TOOLS) == 29, sorted(LISTED_PROFILE_TOOLS)
    # And the four backtest tools are all there — the comparison tool is
    # useless on a profile that cannot produce the runs it compares.
    assert {
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_backtest_summarize",
        "keel_backtest_compare",
    } <= LISTED_PROFILE_TOOLS


def test_the_listed_comparison_copy_drops_the_two_forbidden_words():
    """The directory string rules reject "trades" and "funding" outright;
    the comparison copy says "carry" and names no count word (the count's
    label is "Trades", Q-1906, which the listed rules reject) — the
    `keel_backtest_summarize` precedent. Since agent-surface-cleanup spec
    01 §2.5 (R-4) the base text is the same on every profile, so there is
    no listed override: the ONE description is written in those terms.

    # SEED: put "trades" back into `description` in
    # keel/tools/outcomes/backtest_compare.py — this row and
    # test_policy_scan.py's verb scan both go red.
    """
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    tool = OUTCOMES["keel_backtest_compare"]
    assert tool.listed_description is None, "an override with no surface difference (R-4)"
    lowered = tool.description.lower()
    for banned in ("trades", "trade", "funding", "fund", "deploy", "wallet"):
        assert banned not in lowered, f"comparison copy carries {banned!r}"
    assert "carry" in lowered and "fills" not in lowered  # Q-1746: not "fills"
    assert "round trip" not in lowered  # Q-1906: never the old count word


@pytest.mark.parametrize("field", ["description", "listed_description"])
def test_the_comparison_tool_routes_only_to_listed_tools(field):
    """A listed description that names a non-listed tool fails the
    directory scan; both strings must stay inside the allow-list."""
    import re

    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    tool = OUTCOMES["keel_backtest_compare"]
    # No override means the listed profile serves the shared text (R-4).
    text = getattr(tool, field) or tool.description
    refs = set(re.findall(r"\bkeel_[a-z0-9_]+\b", text))
    stray = {r for r in refs if r in OUTCOMES and r not in LISTED_PROFILE_TOOLS}
    assert not stray, f"{field} routes to non-listed tools: {sorted(stray)}"
    # Non-vacuity: it routes somewhere at all.
    assert refs, f"{field} names no tool — the routing scan would be vacuous"


# ── O.1 — VIEW_TOOLS, `kind`, `size_override` ─────────────────────────


def test_every_backtest_shaped_tool_can_carry_a_view():
    """The four backtest tools joined VIEW_TOOLS: their text block is the
    receipt / evidence / comparison markdown, not the JSON envelope.

    # SEED: drop "keel_backtest_run" from VIEW_TOOLS in
    # keel/tools/outcomes/_strategy_view.py — this row goes red, and so
    # does test_no_view_tool_infers_a_string_output_schema below.
    """
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS

    assert {
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_backtest_summarize",
        "keel_backtest_compare",
    } <= VIEW_TOOLS
    # Non-vacuity the seed cannot move: the six strategy-shaped tools
    # that were already there are still there, and the set is exactly
    # ten — a rename cannot pass by swapping one member for another.
    assert {
        "keel_strategy_get",
        "keel_strategy_compose",
        "keel_strategy_fork",
        "keel_strategy_diff",
        "keel_library_get",
        "keel_library_fork",
    } <= VIEW_TOOLS
    # Agent-surface-cleanup spec 02 §2.2/§2.5: restore (strategy kind, R-15)
    # and keel_account_status (the status kind, no card) — twelve in all.
    assert {"keel_strategy_restore", "keel_account_status"} <= VIEW_TOOLS
    assert len(VIEW_TOOLS) == 12, sorted(VIEW_TOOLS)


def test_no_view_tool_infers_a_string_output_schema():
    """A view-carrying tool returns a `ToolResult`, so an inferred
    `{"result": "<string>"}` output schema would tell a strict client to
    expect the wrong shape. The function infers NONE; the schema a view
    tool publishes is the declared envelope schema (Q-1788,
    `_output_schemas.OUTPUT_SCHEMAS`, validated in test_output_schemas)."""
    from fastmcp.tools.function_tool import FunctionTool
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import _make_param_synthesized_handler
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS
    from keel.tools.outcomes._strategy_view import VIEW_TOOLS

    _bootstrap()
    checked = 0
    for name, tool in sorted(OUTCOMES.items()):
        fn = _make_param_synthesized_handler(tool, frozenset({tool.toolset}))
        schema = FunctionTool.from_function(fn, name=name).output_schema
        if name == "keel_account_status":
            # Until the non-view probe arm flips, status KEEPS the inferred
            # `{"result": string}` wrapper old connectors hold (R-25).
            assert schema is not None and schema.get("x-fastmcp-wrap-result")
            checked += 1
        elif name in VIEW_TOOLS:
            assert schema is None, f"{name} must not infer an output schema"
            assert name in OUTPUT_SCHEMAS, f"{name} declares no envelope schema"
            checked += 1
        else:
            assert schema is not None, f"{name} lost its output schema"
    # Non-vacuity: every VIEW_TOOLS member that is registered was
    # actually reached (a typo'd name would silently check nothing).
    assert checked == len(VIEW_TOOLS & set(OUTCOMES)) >= 8


def _adx_fixture() -> dict:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent
    return json.loads((root / "fixtures/cards/strategy_adx.envelope.json").read_text())


def test_every_strategy_view_declares_its_kind():
    """Renderers dispatch on `view.kind`; a missing one reads as
    strategy, which is why the backtest and comparison views must
    declare theirs and this one must too.

    # SEED: delete `"kind": KIND_STRATEGY,` from build_view's view dict.
    """
    from keel.tools.outcomes._strategy_view import build_view

    envelope = _adx_fixture()
    view = build_view(envelope["metadata"]["graph"], envelope["metadata"])
    assert view["kind"] == "strategy"
    # Non-vacuity: the view is a real one, built from a real graph.
    assert view["structure"]["blocks"], "the fixture graph has no blocks"


def test_size_override_beats_the_event_and_a_receipt_never_marks():
    """`present` resolves to `size_override`: the caller says how much to
    show, `choose_size` says what the event deserves, the override wins.

    # SEED: delete the `if size_override:` block in build_view.
    """
    from keel.tools.outcomes._strategy_view import build_view

    envelope = _adx_fixture()
    graph, meta = envelope["metadata"]["graph"], envelope["metadata"]

    default = build_view(graph, meta)
    forced = build_view(graph, meta, size_override="receipt")
    assert forced["size"] == "receipt"
    assert "marks" not in forced
    # Non-vacuity the seed cannot move: without the override the SAME
    # fixture picks a different size, so the override is doing work.
    assert default["size"] == "structure" and default["size"] != forced["size"]
    # And the markdown follows the size: a receipt is one line plus the
    # link line, never the block tree.
    assert len(forced["markdown"].strip().splitlines()) <= 2
    assert len(default["markdown"].strip().splitlines()) > 2


# ── O.2 — card tools, titles, the invoking table, result `_meta` ───────


def test_every_backtest_shaped_tool_carries_the_backtest_card():
    """A submitted or watched run IS a backtest result — the same card,
    at its receipt size.

    # SEED: map "keel_backtest_run" to "strategy" in CARD_TOOLS
    # (keel/widgets/__init__.py) — this row goes red.
    """
    from keel.widgets import CARD_KINDS, CARD_TOOLS, card_kind_for_tool

    for name in ("keel_backtest_run", "keel_backtest_watch", "keel_backtest_summarize"):
        assert card_kind_for_tool(name) == "backtest", name
    # Non-vacuity the seed cannot move: every card tool names a real
    # tool and a declared kind, and the table is the size we think.
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    assert not set(CARD_TOOLS) - set(OUTCOMES)
    assert set(CARD_TOOLS.values()) == set(CARD_KINDS)
    assert card_kind_for_tool("keel_backtest_compare") == "compare"
    assert len(CARD_TOOLS) == 11, sorted(CARD_TOOLS)


def test_the_invoking_strings_are_outcome_neutral_and_fit_the_row():
    """Descriptor-level strings cannot vary per call: `run` returns on
    `wait=false`, on a timeout and on a failure, and `compose` returns on
    a dry run that saved nothing — so "finished" / "saved" would be a lie
    on those arms. The card's own receipt names the outcome.

    # SEED: change "Backtest returned" to "Backtest finished" in
    # TOOL_INVOCATION_STRINGS — the outcome-word row goes red.
    """
    from keel.widgets import MAX_INVOCATION_STRING_CHARS, TOOL_INVOCATION_STRINGS

    outcome_words = (
        "finished",
        "complete",
        "completed",
        "saved",
        "succeeded",
        "failed",
        "done",
    )
    for name, (invoking, invoked) in TOOL_INVOCATION_STRINGS.items():
        assert invoking and invoked, name
        assert len(invoking) <= MAX_INVOCATION_STRING_CHARS, name
        assert len(invoked) <= MAX_INVOCATION_STRING_CHARS, name
        for word in outcome_words:
            assert word not in invoked.lower(), f"{name}: {invoked!r} claims an outcome"
    # Non-vacuity the seed cannot move: the table covers the tools the
    # contract names, all ten of them.
    assert len(TOOL_INVOCATION_STRINGS) == 10, sorted(TOOL_INVOCATION_STRINGS)
    assert "keel_backtest_run" in TOOL_INVOCATION_STRINGS


def test_result_meta_names_the_same_resource_as_the_descriptor(monkeypatch):
    """`_meta.ui.resourceUri` on the RESULT must name the same card the
    tool's descriptor advertises — two spellings of one resource is how
    a host ends up rendering nothing.

    # SEED: drop the `meta=` argument from the ToolResult in
    # keel/tools/outcomes/_mcp_adapter.py::view_tool_result.
    """
    import json

    from keel.tools.outcomes._mcp_adapter import view_tool_result
    from keel.widgets import CARD_TOOLS, tool_ui_meta

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    envelope = {"run_id": "str_x", "view": {"kind": "strategy", "markdown": "**X** · v1 · valid"}}
    checked = 0
    for name in sorted(CARD_TOOLS):
        result = view_tool_result(json.dumps(envelope), name)
        descriptor = tool_ui_meta(name)
        assert result.meta["ui"]["resourceUri"] == descriptor["ui"]["resourceUri"], name
        checked += 1
    assert checked == len(CARD_TOOLS) >= 11
    # Control arm: a tool with no card carries no result `_meta` at all,
    # so the key is evidence rather than decoration.
    assert view_tool_result(json.dumps(envelope), "keel_account_status").meta is None


# ── O.4 — the text block carries the operational fields (§2.8) ─────────


def _seeded(**extra):
    import json

    return json.dumps(
        {"run_id": "str_x", "view": {"kind": "strategy", "markdown": "**X** · v1 · valid"}, **extra}
    )


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        (
            "next",
            "No backtest of this strategy has been run yet.",
            "next: No backtest of this strategy",
        ),
        # The sample-size fact, formerly a `next` (spec 02 §2.4 #13).
        ("few_fills_note", "few trades (3) — a sparse signal", "sample: few trades (3)"),
        ("library_facts", "verified run Jul 27, 2024 – Sep 22, 2026", "facts: verified run"),
        ("quota_notice", "3 of 50 runs left this month.", "quota: 3 of 50 runs left"),
        ("error_message", "worker died", "error: worker died"),
        ("info", "Submitted; not waiting.", "info: Submitted; not waiting."),
        (
            "validation",
            {"errors": [{"code": "MISSING_UNIVERSE", "message": "no Universe"}], "warnings": []},
            'validation: 1 error — MISSING_UNIVERSE: no Universe (keel_help topic="rule:MISSING_UNIVERSE")',
        ),
    ],
)
def test_text_block_carries_every_operational_field(key, value, expected):
    """Seed each key in turn and assert it reaches `content`. Without
    this the promotion of the text block to `view.markdown` would shrink
    what the model is guaranteed to read to one line.

    # SEED: delete one row from `_OPERATIONAL_FIELDS` in
    # keel/tools/outcomes/_mcp_adapter.py — that parametrisation reds.
    """
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    result = view_tool_result(_seeded(**{key: value}))
    text = result.content[0].text
    assert expected in text, text
    # Non-vacuity the seed cannot move: the markdown is still first and
    # a blank line still separates it from the operational block.
    assert text.startswith("**X** · v1 · valid")
    assert "\n\n" in text


def test_the_operational_block_keeps_the_contract_order():
    """`next`, `facts`, `quota`, `error`, `info`, `validation`, `sample` —
    the spec 02 §2.4 order the model reads them in, one per line. The
    retired `nudge` field is never rendered."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    result = view_tool_result(
        _seeded(
            few_fills_note="s",
            info="i",
            error_message="e",
            quota_notice="q",
            library_facts="f",
            nudge="n",
            next="x",
            validation={"errors": [], "warnings": [{"code": "W", "message": "m"}]},
        )
    )
    body = result.content[0].text.split("\n\n", 1)[1].splitlines()
    assert [line.split(":", 1)[0] for line in body] == [
        "next",
        "facts",
        "quota",
        "error",
        "info",
        "validation",
        "sample",
    ]


def test_warnings_reach_the_text_block_with_their_explain_topic():
    """A warning never blocks a save, so the text block is the only place
    it can reach the model on a host that drops structuredContent — and
    `rule:<CODE>` is what turns the label into something lookupable."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    result = view_tool_result(
        _seeded(
            validation={
                "errors": [],
                "warnings": [
                    {"code": "UNRESOLVED_UNIVERSE", "message": "resolve on save"},
                    {"code": "MASK_DROPS_DIRECTION", "message": "MaskAnd drops sign"},
                ],
            }
        )
    )
    text = result.content[0].text
    assert "validation: 2 warnings" in text
    assert 'keel_help topic="rule:UNRESOLVED_UNIVERSE"' in text
    assert 'keel_help topic="rule:MASK_DROPS_DIRECTION"' in text
    # Non-vacuity: a clean validation block adds no line at all, so the
    # presence of the line is evidence rather than a constant.
    clean = view_tool_result(_seeded(validation={"ok": True, "errors": [], "warnings": []}))
    assert "validation" not in clean.content[0].text


def test_error_envelopes_keep_their_whole_json_text_block():
    """Control arm (§2.8): an error envelope is NOT a view envelope. Its
    text block is the exact JSON string it had, byte for byte — the only
    channel the model reads on claude.ai — and the envelope ALSO rides as
    structuredContent, the only channel a ChatGPT card reads (Q-1785). A
    string that is not a JSON object stays a bare string."""
    import json

    from keel.tools.outcomes._mcp_adapter import view_tool_result

    for payload in (
        '{"code": "not_found", "message": "gone"}',
        '{"run_id": "str_x", "next": "…"}',
        json.dumps({"view": {"markdown": "   "}, "next": "…"}),
    ):
        for name in (None, "keel_strategy_get"):
            result = view_tool_result(payload, name)
            assert [block.text for block in result.content] == [payload]
            assert result.structured_content == json.loads(payload)
    # Not an envelope: a text-only result, never dressed up as one (and a
    # bare string could not satisfy the declared output schema, Q-1788).
    for raw in ("not json", "[1, 2]"):
        result = view_tool_result(raw, "keel_strategy_get")
        assert [block.text for block in result.content] == [raw]
        assert result.structured_content is None


# ── A.1 — `_backtest_view.build_backtest_view` (§2.1) ─────────────────

_DETAIL = {
    "id": "btr_9f3",
    "status": "COMPLETED",
    "strategy_id": "str_mom",
    "strategy_name": "Simple Momentum (ROC 20)",
    "sequence_number": 3,
    "commit_id": "c_1a2b3c4d",
    "engine": "native",
    "start_date": "2024-08-15",
    "end_date": "2026-09-22",
    "completed_at": "2026-09-22T11:04:00Z",
    "metrics": {
        "sharpe": 0.67,
        "total_return_pct": 60.5,
        # The worker's OTHER spelling, and positive — the view owns the
        # sign, so this must still render −43.4%.
        "max_drawdown": 43.4,
        "total_trades": 1761,
        "win_rate_pct": 26.0,
        "turnover": 220.0,
        # A current run (trade-metrics spec 01 §3): turnover is recorded.
        "trade_model": "reducing_order",
        "sortino_ratio": 1.06,
        "profit_factor": 1.08,
        "total_fees_paid": 1091.0,
        "funding_attribution": -13.3,
    },
}

_URL = "https://app.usekeel.io/backtests/btr_9f3?tab=tearsheet"


def _backtest_view(size="receipt", **overrides):
    from keel.tools.outcomes._backtest_view import build_backtest_view

    detail = {**_DETAIL, **overrides}
    return build_backtest_view(detail, size=size, url=_URL)


def test_backtest_receipt_markdown_is_one_line_plus_link():
    """A step in a set is ONE line. The whole cadence rests on it: the
    founder's turn 3 drew ten full cards for ten runs.

    # SEED: in _receipt_markdown, replace `lines = [" · ".join(bits)]`
    # with `lines = [bits[0], " · ".join(bits[1:])]` — this row reds.
    """
    view = _backtest_view()
    lines = view["markdown"].strip().splitlines()
    assert len(lines) == 2, lines
    assert lines[0] == (
        "Backtest **Simple Momentum (ROC 20)** v3 · Return +60.5% · Max drawdown −43.4% · "
        "Sharpe 0.67 · Win rate 26.0% · Aug 15, 2024 – Sep 21, 2026"
    )
    assert lines[1] == f"View in Keel: {_URL}"
    # Non-vacuity the seed cannot move: the line is a real rendering of
    # real metrics, not an empty string that trivially counts as one.
    assert len(lines[0]) > 60 and view["metrics"]["trades"] == 1761


def test_every_number_keeps_its_precision_on_every_surface():
    """Q-1710: `_plain` stripped trailing zeros, so a Sharpe of 0.60 read
    `0.6` on the receipt, in the evidence block and in the comparison
    column beside `0.82`. The card's `fmt.ratio` is a bare `toFixed(dp)`
    and always was, so the two surfaces disagreed about the same run."""
    # SEED: give `_fixed` back the `.rstrip("0").rstrip(".")` that
    # `_plain` carried — every row below reds, which IS what staging
    # shipped.
    flat = {
        "sharpe": 0.6,
        "total_return_pct": 46.2,
        "max_drawdown_pct": -55.2,
        "total_trades": 1468,
        "win_rate_pct": 26.0,
        "turnover": 12.0,
        "trade_model": "reducing_order",
        "sortino": 1.0,
        "calmar": 2.0,
        "profit_factor": 1.1,
    }
    receipt = _backtest_view(metrics=flat)
    assert "Sharpe 0.60 ·" in receipt["markdown"], receipt["markdown"]
    evidence = _backtest_view("evidence", metrics=flat)
    line = evidence["markdown"].strip().splitlines()[2]
    assert line == (
        "Trades 1,468 · Turnover (× capital) 12.0x · Sortino 1.00 · Calmar 2.00 · "
        "Profit factor 1.10"
    )
    # Not vacuous: the values really are the ones a strip would shorten —
    # each has a zero or a one-digit tail that `0.6`/`1`/`2` would eat.
    assert flat["sharpe"] == 0.6 and flat["sortino"] == 1.0 and flat["calmar"] == 2.0
    # And a value that needs no padding is untouched.
    assert "Sharpe 0.67 ·" in _backtest_view()["markdown"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("QUEUED", "still running when checked"),
        ("running", "still running when checked"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_every_non_success_receipt_names_its_state_and_stays_one_line(status, expected):
    """A snapshot says it is a snapshot: no second result ever updates a
    line already in the transcript, so `still running` alone would read
    as a live status."""
    view = _backtest_view(status=status, error_message="worker exploded\nsecond line")
    lines = view["markdown"].strip().splitlines()
    assert len(lines) == 2, lines
    assert expected in lines[0]
    # A queued/running receipt never quotes numbers it does not have.
    if expected.startswith("still"):
        assert "Sharpe" not in lines[0] and "fills" not in lines[0]
    if status == "failed":
        # The first line of the error, and only the first.
        assert "worker exploded" in lines[0] and "second line" not in lines[0]


def test_evidence_markdown_lists_the_nine_metrics_and_the_net_of_line():
    """The evidence block is the run being discussed: nine numbers, the
    window, its span, and what the numbers are net of.

    # SEED: delete the `net_of` line from _evidence_markdown — this row
    # reds while the receipt rows stay green.
    """
    from keel.tools.outcomes._backtest_view import EVIDENCE_METRIC_KEYS, NET_OF

    view = _backtest_view("evidence")
    text = view["markdown"]
    lines = text.strip().splitlines()
    assert lines[0] == (
        "**Simple Momentum (ROC 20)** · v3 · completed · Aug 15, 2024 – Sep 21, 2026 · 2.1 years"
    )
    # Q-1746: the app's first four, in its order and with its labels; the
    # rest on the "more" line, labelled as the app's metrics table.
    assert lines[1] == "Return +60.5% · Max drawdown −43.4% · Sharpe 0.67 · Win rate 26.0%"
    assert lines[2] == (
        "Trades 1,761 · Turnover (× capital) 220.0x · Sortino 1.06 · "
        "Profit factor 1.08 · Fees paid $1,091"
    )
    assert lines[3] == "net of fees, slippage and carry"
    assert lines[4] == f"View in Keel: {_URL}"
    # Non-vacuity, read from the FIXTURE rather than the output: the
    # layout is asserted against the declared keys, nine of which this
    # fixture carries (no calmar; no positions view, so none of the five
    # keys only that view fills — trade-metrics spec 01 §6.2). Q-1991 retired
    # the incl-warm-up tile, so 15 keys are declared.
    assert len(EVIDENCE_METRIC_KEYS) == 15
    present = [k for k in EVIDENCE_METRIC_KEYS if k in view["metrics"]]
    assert len(present) == 9 and "calmar" not in present
    assert list(NET_OF) == ["fees", "slippage", "carry"]


# ── Q-1715 — the Carry tile, and the claim under it ──────────────────

#: The funding block the worker really writes (`metrics["funding"] =
#: portfolio.funding_stats()`), from the staging capture of
#: `keel_backtest_summarize` on 2026-09-22.
_WIRE_FUNDING = {
    "funding_boost_pct": -23.71330202910646,
    "cumulative_funding": 1772.7569706000593,
    "funding_return_pct": -17.727569706000594,
    "price_only_return_pct": 120.51062990955863,
}


def test_carry_is_read_from_the_funding_block_the_worker_actually_writes():
    """Q-1715: `view.metrics` sourced carry from `funding_attribution`,
    a key NOTHING in the platform has ever written — so the card's Carry
    tile never drew from a real envelope while the receipt line beneath
    it said the numbers were net of carry."""
    # SEED: in `_backtest_view._carry`, return `None` instead of reading
    # `funding.cumulative_funding` — the wire arm reds while the
    # fixture arm (which supplies `funding_attribution`) stays green,
    # which IS the shipped split.
    wire = {k: v for k, v in _DETAIL["metrics"].items() if k != "funding_attribution"}
    wire = {**wire, "funding": dict(_WIRE_FUNDING), "funding_included": True}
    view = _backtest_view(metrics=wire)
    # Signed P&L in account currency: negative = funding PAID. The
    # worker writes `cumulative_funding` as a cost magnitude, proven
    # against the stored library runs where
    # `funding_return_pct == -cumulative_funding / 100` in both
    # directions.
    assert view["metrics"]["carry"] == -_WIRE_FUNDING["cumulative_funding"]
    assert view["metrics"]["carry"] < 0
    # NOT the two percents, which are different quantities: the app's
    # "Funding Carry" in points of return, and funding as a percent of
    # initial capital.
    assert view["metrics"]["carry"] != _WIRE_FUNDING["funding_boost_pct"]
    assert view["metrics"]["carry"] != _WIRE_FUNDING["funding_return_pct"]
    # Not vacuous, read from the INPUT: this envelope carries no
    # `funding_attribution` at all — exactly like the wire — so the
    # value above can only have come from the funding block.
    assert "funding_attribution" not in wire and "carry" not in wire
    assert wire["funding"]["cumulative_funding"] > 0
    # And an explicit key still wins, so nothing that already worked
    # stops working.
    assert _backtest_view()["metrics"]["carry"] == -13.3


def test_a_run_that_did_not_model_funding_does_not_claim_to_be_net_of_carry():
    """The receipt line is a PROMISE — "net of fees, slippage and carry"
    printed under the tiles. `net_of` was a constant, so a run the
    worker marked `funding_included: false` made the claim anyway."""
    # SEED: in `build_backtest_view`, put `list(NET_OF)` back in place of
    # `net_of_for(detail.get("metrics"))` — both rows below red.
    from keel.tools.outcomes._backtest_view import NET_OF, NET_OF_WITHOUT_CARRY

    excluded = {**_DETAIL["metrics"], "funding": dict(_WIRE_FUNDING), "funding_included": False}
    view = _backtest_view("evidence", metrics=excluded)
    assert view["net_of"] == list(NET_OF_WITHOUT_CARRY)
    # The prose and the field are ONE claim.
    assert "net of fees and slippage" in view["markdown"]
    assert "carry" not in view["markdown"]
    # Nothing to show, so no tile either — a Carry figure beside "not
    # net of carry" is the same contradiction the other way round.
    assert "carry" not in view["metrics"]
    # Control arm, identical but for the flag: the claim stands and the
    # number is there, so the verdict reacts to the FLAG and not to the
    # shape of the metrics dict.
    included = {**excluded, "funding_included": True}
    kept = _backtest_view("evidence", metrics=included)
    assert kept["net_of"] == list(NET_OF)
    assert "net of fees, slippage and carry" in kept["markdown"]
    assert kept["metrics"]["carry"] is not None
    # An ABSENT flag leaves the standing claim: it predates the key, and
    # withdrawing a true statement from every older envelope would be
    # the same error in the other direction.
    older = {k: v for k, v in included.items() if k != "funding_included"}
    assert _backtest_view("evidence", metrics=older)["net_of"] == list(NET_OF)


def test_metrics_is_the_one_owner_of_key_names_and_signs():
    """`trades` = total_trades/num_trades, `carry` = funding_attribution,
    and max drawdown is always negative-or-zero however the worker wrote
    it. Every consumer reads `view.metrics`, never `summary_metrics`.

    # SEED: delete the `if drawdown is not None and drawdown > 0` block
    # in view_metrics — the drawdown rows red.
    """
    view = _backtest_view()
    metrics = view["metrics"]
    assert metrics["max_drawdown_pct"] == -43.4
    assert "Max drawdown −43.4%" in view["markdown"]
    assert metrics["trades"] == 1761
    # The deprecated `fills` alias is gone: the cards read `view.tiles` (Q-1787).
    assert "fills" not in metrics
    assert metrics["carry"] == -13.3
    assert metrics["fees_paid"] == 1091.0
    assert metrics["sortino"] == 1.06
    # Non-vacuity the seed cannot move: the fixture really does use the
    # OTHER spelling, positive — otherwise the sign rule is untested.
    assert _DETAIL["metrics"]["max_drawdown"] == 43.4
    assert "max_drawdown_pct" not in _DETAIL["metrics"]


def test_the_headline_sharpe_is_the_one_the_app_headlines():
    """Q-1746: one run, one Sharpe on every surface. Q-1991 (founder ruling
    2026-10-04): the headline is the full-window `sharpe_ratio` on every run
    — the warm-up-trimmed `sharpe_ratio_active` is no longer headlined, and
    no tile or note says "warm-up", because the stored trim count cannot
    tell warm-up from a decision to stay flat. The app's tearsheet, cards
    and the chat payload follow the same rule.

    # SEED (run 2026-10-04): restore the `warmup > 0` → active swap in
    # view_metrics — the 60-bar arm reds (0.52 headlined) while the 0-bar
    # and no-active arms stay green.
    """
    base = {**_DETAIL["metrics"], "sharpe": 0.51}
    for metrics in (
        {**base, "sharpe_ratio_active": 0.52, "warmup_bars": 60},
        {**base, "sharpe_ratio_active": 0.52, "warmup_bars": 0},
        {**base, "warmup_bars": 60},
    ):
        view = _backtest_view(metrics=metrics)
        assert view["metrics"]["sharpe"] == 0.51
        assert "sharpe_incl_warmup" not in view["metrics"]
        (sharpe_tile,) = [t for t in view["tiles"] if t["key"] == "sharpe"]
        assert sharpe_tile["display"] == "0.51" and "note" not in sharpe_tile
        tiles = view["tiles"] + view["more_tiles"]
        assert not any("warm" in (t.get("label") or "").lower() for t in tiles)


def test_the_tiles_are_the_apps_first_four_and_the_count_is_trades():
    """Q-1746 + founder ruling 2026-09-22: `view.tiles` are the app's first
    four stat cards (Return · Max drawdown · Sharpe · Win rate) with its
    labels; everything else is `view.more_tiles`. The count the card
    labelled "Fills" is the engine's round-trip decision count — the app's
    Trades tile, not its fills table (1,808 vs 5,960 on the reported run).

    # SEED: relabel ("trades", "Trades") → "Fills" in MORE_TILES —
    # the label row reds.
    """
    view = _backtest_view("evidence")
    assert [(t["key"], t["label"]) for t in view["tiles"]] == [
        ("total_return_pct", "Return"),
        ("max_drawdown_pct", "Max drawdown"),
        ("sharpe", "Sharpe"),
        ("win_rate_pct", "Win rate"),
    ]
    more = {t["key"]: t for t in view["more_tiles"]}
    assert more["trades"]["label"] == "Trades"  # Q-1906
    assert more["trades"]["value"] == _DETAIL["metrics"]["total_trades"]
    # 2026-09-25: the model reads the count under `trades` (it said "round
    # trips" off the old key), and the card formats it by the served kind —
    # the card HTML is listed copy and names no count key.
    assert more["trades"]["format"] == "count"
    assert "round_trips" not in view["metrics"]
    # Nothing the view draws calls the count "fills".
    assert "fills" not in view["markdown"].lower()
    assert "fills" not in _backtest_view()["markdown"].lower()
    # Non-vacuous: the fixture carries the count under the worker's key.
    assert _DETAIL["metrics"]["total_trades"] == 1761


def test_missing_metric_is_a_dash_not_a_crash():
    """A missing metric is ABSENT from `metrics` and an em dash in the
    markdown. A zero would be a lie; a crash would lose the run."""
    view = _backtest_view("evidence", metrics={"sharpe": 1.2})
    assert view["metrics"] == {"sharpe": 1.2}
    assert "Max drawdown —" in view["markdown"]
    # A headline tile keeps its place with a dash; a "more" metric the run
    # does not carry is not listed at all.
    assert "Trades" not in view["markdown"]
    assert [t["display"] for t in view["tiles"]] == ["—", "—", "1.20", "—"]
    # A run with no metrics dict at all still renders.
    bare = _backtest_view("evidence", metrics=None)
    assert bare["metrics"] == {}
    assert bare["markdown"].strip().splitlines()[1] == (
        "Return — · Max drawdown — · Sharpe — · Win rate —"
    )


def test_no_id_engine_or_stamp_in_header_or_markdown():
    """V-2: registry nouns and formatted numbers. A reader sees
    "Simple Momentum (ROC 20) v3", never `btr_…`, `native`, a commit
    hash or an ISO stamp — the fixture carries all four so the sweep is
    not vacuous."""
    for size in ("receipt", "evidence"):
        # The PROSE, not the link line: a URL legitimately contains the
        # run id, which is exactly why the rule is scoped this way.
        text = "\n".join(
            line
            for line in _backtest_view(size)["markdown"].splitlines()
            if not line.startswith("View in Keel:")
        )
        assert "btr_" not in text and "str_" not in text
        assert "c_1a2b3c4d" not in text
        assert "native" not in text
        assert "T11:04:00Z" not in text and "2026-09-22T" not in text
    # Non-vacuity: the DETAIL really does carry each of them.
    assert _DETAIL["id"].startswith("btr_")
    assert _DETAIL["engine"] == "native" and _DETAIL["commit_id"].startswith("c_")
    assert _DETAIL["completed_at"].endswith("Z")


def test_the_view_never_carries_the_curve():
    """One copy per envelope, at the top level: two copies is ~14 KB per
    run, and `curve` is accepted only so a caller cannot conclude the
    view forgot it."""
    view = _backtest_view(curve={"points": [["2024-08-15T00:00:00Z", 1000.0, 0.0]]})
    assert "curve" not in view


# ── Q-1709 — `view.window` is a DATE pair, at one owner ───────────────


def test_the_view_window_is_a_plain_date_whatever_the_api_said():
    """BUILD §2.1/§2.2 specify `{"start": "2024-08-15", ...}`. keel-api
    answers `start_date` as `"2024-07-27 00:00:00+00:00"` — a
    space-separated midnight datetime that is not ISO-8601, so
    `date.fromisoformat` raises on it and every renderer written from
    the spec is left guessing."""
    # SEED: in `_backtest_view.window_block`, return
    # `{"start": start, "end": end}` unchanged — this reds, which IS
    # what staging shipped on all four backtest-shaped tools.
    from datetime import date

    raw = _backtest_view(
        start_date="2024-07-27 00:00:00+00:00",
        end_date="2026-09-22 00:00:00+00:00",
    )
    assert raw["window"] == {
        "start": "2024-07-27",
        "end": "2026-09-22",
        # Additive (spec 03 §2.5): `end` stays exclusive; `last_bar` is the
        # last covered day and `days` the covered-day count.
        "last_bar": "2026-09-21",
        "days": 787,
    }
    # The shape the contract promises is the shape a reader can parse.
    assert date.fromisoformat(raw["window"]["start"]) == date(2024, 7, 27)
    # Not vacuous, twice over: the datetime really IS unparseable as a
    # date (so the normalisation is doing work), and the markdown built
    # from the same pair was already right — this was confined to the
    # structured field and the fix must not move the prose.
    with pytest.raises(ValueError):
        date.fromisoformat("2024-07-27 00:00:00+00:00")
    assert "Jul 27, 2024 – Sep 21, 2026" in raw["markdown"]  # the last covered day
    # A date-only input is untouched, and a window that is not a date at
    # all is absent rather than passed through in a forbidden shape.
    assert _backtest_view()["window"] == {
        "start": "2024-08-15",
        "end": "2026-09-22",
        "last_bar": "2026-09-21",
        "days": 768,
    }
    assert _backtest_view(start_date="soon", end_date=None)["window"] == {
        "start": None,
        "end": None,
    }


def test_the_strategy_evidence_window_reads_the_same_owner():
    """The strategy card's evidence block carries the same pair. It used
    `str(value)[:10]`, which agreed with `window_block` on every stamp
    keel-api happens to send and would emit ten characters of ANYTHING
    else onto a field the contract says is a date."""
    # SEED: in `_strategy_view.evidence_from_metadata`, restore
    # `{"start": str(start)[:10] if start else None, ...}` — the
    # garbage-input row below reds while the datetime row stays green,
    # which is exactly why one owner and not two.
    from keel.tools.outcomes._strategy_view import evidence_from_metadata

    def window_for(start, end):
        return evidence_from_metadata(
            {
                "latest_backtest_sequence": 3,
                "latest_backtest_start_date": start,
                "latest_backtest_end_date": end,
                "latest_backtest_metrics": {"sharpe_ratio": 0.67},
            }
        )

    dated = window_for("2024-07-27 00:00:00+00:00", "2026-09-22 00:00:00+00:00")
    assert dated["window"] == {
        "start": "2024-07-27",
        "end": "2026-09-22",
        "last_bar": "2026-09-21",
        "days": 787,
    }
    # A value that is not a date is ABSENT, never ten characters of it.
    assert window_for("not a date at all", "2026-09-22")["window"] == {
        "start": None,
        "end": "2026-09-22",
    }
    # Not vacuous: the evidence block really was built (it carries the
    # metric beside the window), so an empty dict cannot pass either row.
    assert dated["sharpe"] == 0.67


# ── A.8 — the supersession election key on every view (§2.9) ──────────


def test_every_view_carries_a_server_minted_election_key():
    """`object` / `at` / `seq`: what the widgets announce on the
    `keel-cards` channel so an older card of the SAME object collapses
    to its receipt when a younger one mounts.

    # SEED: in `_base.election_key`, take `at` from an argument the
    # caller supplies (a client stamp) instead of `datetime.now(UTC)` —
    # the clock row reds.
    """
    from datetime import UTC, datetime

    from keel.tools.outcomes._strategy_view import build_view

    before = datetime.now(UTC)
    envelope = _adx_fixture()
    strategy = build_view(
        envelope["metadata"]["graph"],
        {**envelope["metadata"], "strategy_id": "str_adx"},
    )
    backtest = _backtest_view()
    after = datetime.now(UTC)

    assert strategy["object"] == "str_adx"
    assert backtest["object"] == "btr_9f3"
    # Server clock: the stamp sits inside the window this test spanned.
    # `at` is truncated to milliseconds, so the lower bound is too —
    # otherwise the comparison fails on sub-millisecond truncation
    # rather than on where the stamp came from.
    floor = before.replace(microsecond=(before.microsecond // 1000) * 1000)
    for view in (strategy, backtest):
        stamped = datetime.fromisoformat(view["at"].replace("Z", "+00:00"))
        assert floor <= stamped <= after, view["at"]
    # Monotonic within the process, so two views minted in the same
    # millisecond still order.
    assert backtest["seq"] > strategy["seq"]
    later = _backtest_view()
    assert later["seq"] > backtest["seq"]
    # Non-vacuity the seed cannot move: both view kinds were built, and
    # a view with no object omits the key rather than inventing one.
    assert strategy["kind"] == "strategy" and backtest["kind"] == "backtest"
    anonymous = build_view(envelope["metadata"]["graph"], envelope["metadata"])
    assert "object" not in anonymous and "at" in anonymous


# ── A.2 / A.3 / A.6 — run, watch, summarize ───────────────────────────


class _Recorder:
    """A path-keyed fake client that COUNTS what each arm actually read.

    The curve guard is a claim about round trips, not about a key in an
    envelope, so the test has to see the GETs.
    """

    def __init__(self, detail, *, curve=None, runs=None, results=None):
        self.detail = detail
        self.curve = curve
        self.runs = runs if runs is not None else {"data": [], "pagination": {}}
        self.results = results or {}
        self.paths: list[str] = []

    def get(self, path, **kwargs):
        self.paths.append(path)
        if path.endswith("/curve"):
            if self.curve is None:
                return {}
            return self.curve
        if path.endswith("/results"):
            return self.results
        if path == "/v1/backtests":
            return self.runs
        if path.startswith("/v1/backtests/"):
            return self.detail
        if path.startswith("/v1/strategy-work"):
            return {}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, **kwargs):
        self.paths.append(f"POST {path}")
        # A submission answers `queued` whatever the eventual detail
        # says — the recorder must not leak the terminal status back
        # into the submit response.
        return {**self.detail, "id": self.detail["id"], "status": "queued"}

    def count(self, suffix):
        return sum(1 for p in self.paths if p.endswith(suffix))


_CURVE = {
    "points": [
        {"t": "2024-08-15T00:00:00Z", "equity": 1000.0, "drawdown_pct": 0.0},
        {"t": "2026-09-22T00:00:00Z", "equity": 1605.0, "drawdown_pct": -2.0},
    ],
    "start": "2024-08-15T00:00:00Z",
    "end": "2026-09-22T00:00:00Z",
    "source_points": 54000,
}


def _run(args=None, *, detail=None, **recorder_kwargs):
    from unittest.mock import patch

    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _Recorder(detail or _DETAIL, **recorder_kwargs)
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    call = {
        "strategy_id": "str_mom",
        "commit_id": "c_1a2b3c4d",
        "wait": True,
        "skip_readiness": True,
        **(args or {}),
    }
    with (
        patch("keel.tools.outcomes.backtest_run._POLL_INTERVAL_S", 0.0),
        # A non-terminal snapshot would otherwise spin for the whole
        # 90 s budget: the timed-out arm is a real arm of this guard.
        patch("keel.tools.outcomes.backtest_run._POLL_MAX_S", 0.05),
    ):
        env = OUTCOMES["keel_backtest_run"].handler(call, ctx).to_envelope()
    return env, client


def test_run_wait_success_carries_receipt_view_and_curve():
    """A finished run renders itself: the card at its receipt size, plus
    the ONE copy of the curve the card draws.

    # SEED: delete the `curve = fetch_curve(client, backtest_id)` block
    # in keel/tools/outcomes/backtest_run.py — this row reds.
    """
    env, client = _run(curve=_CURVE)
    assert env["view"]["kind"] == "backtest"
    assert env["view"]["size"] == "receipt"
    assert env["view"]["markdown"].startswith("Backtest **Simple Momentum (ROC 20)** v3 · Return")
    assert env["curve"]["points"][0] == ["2024-08-15T00:00:00Z", 1000.0, 0.0]
    # Exactly ONE copy per envelope, and it is at the top level.
    assert "curve" not in env["view"]
    # Non-vacuity the seed cannot move: exactly one curve GET happened.
    assert client.count("/curve") == 1
    assert env["render"]["card"] == "backtest"


def test_run_present_view_yields_evidence():
    """`present="view"` is the caller saying "this is the run we are
    about to discuss" — the evidence block, not the receipt."""
    env, _ = _run({"present": "view"}, curve=_CURVE)
    assert env["view"]["size"] == "evidence"
    assert "net of fees, slippage and carry" in env["view"]["markdown"]
    # Control arm: the same call without `present` is a receipt, so the
    # argument is doing the work rather than the fixture.
    default, _ = _run(curve=_CURVE)
    assert default["view"]["size"] == "receipt"
    # `present` never changes what is FETCHED — only what is rendered.
    assert default["curve"] == env["curve"]


def test_run_queued_carries_a_running_receipt_without_numbers():
    """`wait=false` still renders: one line naming the strategy, the
    window, and the fact that it was not finished when checked."""
    env, client = _run({"wait": False})
    line = env["view"]["markdown"].splitlines()[0]
    assert "still running when checked" in line
    assert "Sharpe" not in line
    # Non-vacuity: no curve was fetched for a run that has not finished.
    assert client.count("/curve") == 0


@pytest.mark.parametrize("status", ["FAILED", "cancelled"])
def test_run_failed_and_cancelled_receipts_carry_the_state(status):
    env, client = _run(
        detail={**_DETAIL, "status": status, "error_message": "worker exploded"},
    )
    assert status.lower() in env["view"]["markdown"]
    assert client.count("/curve") == 0


def _watch(args=None, *, detail=None, **recorder_kwargs):
    from unittest.mock import patch

    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _Recorder(detail or _DETAIL, **recorder_kwargs)
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    call = {
        "backtest_id": "btr_9f3",
        "interval_s": 1,
        "timeout_s": 0,
        "skip_readiness": True,
        **(args or {}),
    }
    with patch("keel.tools.outcomes.backtest_watch.time.sleep", lambda _s: None):
        env = OUTCOMES["keel_backtest_watch"].handler(call, ctx).to_envelope()
    return env, client


def test_no_curve_fetch_on_failed_or_timed_out_runs():
    """Control arm for the curve guard: exactly one GET on success, zero
    on every arm that produced no numbers. A fetch that happened
    unconditionally would look identical in the envelope.

    # SEED: in keel/tools/outcomes/backtest_watch.py replace
    # `if is_success(view_detail):` with `if True:` — the watch zeros
    # red while the success arms stay green. (`backtest_run` returns
    # before its fetch on every non-success arm, so its zeros are
    # structural; the watch computes the same verdict on ONE path,
    # which is what makes this seedable.)
    """
    success, success_client = _run(curve=_CURVE)
    failed, failed_client = _run(detail={**_DETAIL, "status": "FAILED"})
    queued, queued_client = _run(detail={**_DETAIL, "status": "RUNNING"})
    assert success_client.count("/curve") == 1
    assert failed_client.count("/curve") == 0
    assert queued_client.count("/curve") == 0

    watched, watched_client = _watch(curve=_CURVE)
    watch_failed, watch_failed_client = _watch(detail={**_DETAIL, "status": "FAILED"})
    watch_running, watch_running_client = _watch(detail={**_DETAIL, "status": "RUNNING"})
    assert watched_client.count("/curve") == 1
    assert watch_failed_client.count("/curve") == 0
    assert watch_running_client.count("/curve") == 0
    assert watched["curve"]["points"][0] == ["2024-08-15T00:00:00Z", 1000.0, 0.0]
    assert watch_running["timed_out"] is True

    # Non-vacuity: every arm actually produced an envelope with a view,
    # so the zeros are about the fetch and not about a crash.
    for env in (success, failed, queued, watched, watch_failed, watch_running):
        assert env["view"]["kind"] == "backtest"


# ── Q-1716 — the hygiene notes reach every backtest surface ──────────

#: What the worker really writes when a universe is NOT clean — the
#: executor's `lifetime_mask.notes` / `span_mask.notes` ride
#: `metrics["warnings"]`, and `derive_non_result` writes
#: `metrics["non_result"]` (services/backtest-worker/src/metrics.py).
#: Both are conditional, so a clean run carries NEITHER — which is why
#: their absence from a captured envelope is not evidence the path is
#: dead.
_HYGIENE = {
    "warnings": [
        {
            "code": "SYMBOL_DELISTED",
            "message": "2 of 30 assets were delisted inside the window — "
            "held to the delisting, then dropped.",
        }
    ],
    "non_result": {"message": "Warm-up consumed the whole window — no bar to decide on."},
}


def test_the_hygiene_notes_reach_run_and_watch_not_only_summarize():
    """Q-1716: `_notes_block` was built by `keel_backtest_summarize`
    alone, while `keel_backtest_run` and `keel_backtest_watch` render
    the SAME card — which reads `env.notes` — so a run with delisted
    symbols said nothing about them on two of its three surfaces."""
    # SEED: in `backtest_run._handler`, delete the `notes = notes_block(...)`
    # block — the run arm reds while the summarize arm stays green,
    # which IS the shipped split.
    noisy = {**_DETAIL, "metrics": {**_DETAIL["metrics"], **_HYGIENE}}
    env, _ = _run(detail=noisy)
    assert env["notes"]["assets"][0]["code"] == "SYMBOL_DELISTED"
    assert env["notes"]["non_result"]["message"].startswith("Warm-up consumed")

    watch, _ = _watch(detail=noisy)
    assert watch["notes"]["assets"][0]["code"] == "SYMBOL_DELISTED"

    # Not vacuous, and the half that says this is NOT a server defect:
    # the SAME tools on a run with nothing to note carry no `notes` key
    # at all, because the worker writes both fields conditionally. The
    # four staging captures were clean runs, not evidence of a dead
    # path.
    clean, _ = _run()
    assert "notes" not in clean
    assert "warnings" not in _DETAIL["metrics"] and "non_result" not in _DETAIL["metrics"]


def test_watch_renders_the_run_at_its_receipt_size_and_present_widens_it():
    """A watch that finds a finished run IS that run's result — the same
    card `summarize` draws, at the size a step deserves."""
    default, _ = _watch(curve=_CURVE)
    assert default["view"]["size"] == "receipt"
    assert len(default["view"]["markdown"].strip().splitlines()) == 2
    widened, _ = _watch({"present": "view"}, curve=_CURVE)
    assert widened["view"]["size"] == "evidence"
    # Control arm: `present` changed the rendering and nothing else.
    assert widened["curve"] == default["curve"]


def test_summarize_view_is_evidence_and_markdown_matches_the_metrics():
    """`summarize` is the rendering for the run being DISCUSSED, so its
    view is the evidence block — it takes no `present`.

    # SEED: pass size="receipt" in backtest_summarize's
    # build_backtest_view call — this row reds.
    """
    from unittest.mock import patch

    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _Recorder(_DETAIL, curve=_CURVE)
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    with patch.dict("os.environ", {}, clear=False):
        env = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_9f3"}, ctx)
    envelope = env.to_envelope()
    view = envelope["view"]
    assert view["size"] == "evidence"
    assert "Return +60.5% · Max drawdown −43.4% · Sharpe 0.67 · Win rate 26.0%" in view["markdown"]
    # Non-vacuity, read from the fixture: eight of the nine evidence
    # metrics are present, so the layout is rendering real values.
    from keel.tools.outcomes._backtest_view import EVIDENCE_METRIC_KEYS

    assert len([k for k in EVIDENCE_METRIC_KEYS if k in view["metrics"]]) == 9
    assert "present" not in OUTCOMES["keel_backtest_summarize"].input_schema["properties"]


def test_present_copy_describes_its_own_tool_and_classifies_no_other_call():
    """Q-1748: the shared `present` copy ended "dry runs and single runs are
    receipts; a create, a fork or a save is a view" — sorting OTHER tools'
    calls into categories. ChatGPT's approval gate read that as tool
    documentation prescribing "how the classifier should treat strategy
    forking and related tool usage" and flagged keel_strategy_fork as a
    Suspicious Instruction. Each tool's copy now names only its own default.

    # SEED: append " A create, a fork or a save is a view." to
    # PRESENT_DESCRIPTION in _base.py — every taker reds here.
    """
    import re

    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    other_calls = re.compile(r"\b(fork|create|single run|dry runs)\b", re.IGNORECASE)
    checked = 0
    for name, tool in OUTCOMES.items():
        prop = tool.input_schema.get("properties", {}).get("present")
        if prop is None:
            continue
        shared, _, own_default = prop["description"].partition(" Omitted: ")
        assert own_default, f"{name}: `present` copy states no default of its own"
        assert not other_calls.search(shared), f"{name}: `present` copy classifies other calls"
        checked += 1
    assert checked == 4  # non-vacuous: the four takers were read


def test_the_user_phrasings_that_pick_a_size_are_in_the_copy_the_host_reads():
    """Q-1745: on claude.ai (which drops server instructions) "show me the
    result at full size" and "just the one-line version" never reached
    `present`. The mapping lives where that host reads it: the parameter
    description on every tool that takes `present` — and ONLY there since
    agent-surface-cleanup spec 01 §2.5 made rendering cadence a forbidden
    class in tool descriptions (the `present` parameter and the server's
    defaults carry it; `present` was unused while the cadence sat in
    prose, 00-context §6)."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    takers = [n for n, t in OUTCOMES.items() if "present" in t.input_schema.get("properties", {})]
    # Non-vacuity: the four tools BUILD §2.4 names, read from the registry.
    assert sorted(takers) == [
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_strategy_compose",
        "keel_strategy_fork",
    ]
    for name in takers:
        desc = OUTCOMES[name].input_schema["properties"]["present"]["description"]
        assert "in full or enlarged" in desc and "`view`" in desc, name
        assert "one-line" in desc and "`receipt`" in desc, name
        assert "Omitted: `" in desc, name
    summarize = OUTCOMES["keel_backtest_summarize"]
    for text in (summarize.description, OUTCOMES["keel_strategy_get"].description):
        assert "full size" not in text
        assert "one-line form" not in text


def test_next_names_compare_only_when_another_run_completed_this_hour():
    """Both arms. The compare hint exists to turn N single-run cards
    into one comparison; firing it on a first backtest would be noise.

    # SEED: invert the `if len(ids) < 2` condition in compare_next —
    # the silent arm reds.
    """
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    stale = (datetime.now(UTC) - timedelta(hours=9)).isoformat().replace("+00:00", "Z")
    sibling = {
        "id": "btr_older",
        "status": "COMPLETED",
        "completed_at": recent,
    }

    loud, _ = _run(runs={"data": [sibling], "pagination": {}})
    assert loud["next"].startswith("2 completed runs on this strategy in the last hour: ")
    # OLDEST first: compare's deltas are each run minus the FIRST id.
    assert loud["next"].index('"btr_older"') < loud["next"].index('"btr_9f3"')
    assert "keel_backtest_compare(backtest_ids=[" in loud["next"]

    # Negative arm 1: the only sibling is older than the window.
    quiet, _ = _run(runs={"data": [{**sibling, "completed_at": stale}], "pagination": {}})
    assert "next" not in quiet
    # Negative arm 2: no siblings at all — a first backtest.
    alone, _ = _run()
    assert "next" not in alone
    # Negative arm 3: a sibling that never completed is not a run to
    # compare against.
    running, _ = _run(
        runs={"data": [{**sibling, "status": "RUNNING"}], "pagination": {}},
    )
    assert "next" not in running


def test_few_fills_is_a_data_line_beside_the_compare_hint():
    """W5 P2: a run with three fills has not measured a strategy. Since
    spec 02 §2.4 that is a FACT about the sample — its own data line
    (`few_fills_note`, `sample:`) — so it no longer displaces the one
    advice line: both reach the model."""
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    sparse = {**_DETAIL, "metrics": {**_DETAIL["metrics"], "total_trades": 3}}
    env, _ = _run(
        detail=sparse,
        runs={"data": [{"id": "btr_older", "status": "COMPLETED", "completed_at": recent}]},
    )
    assert env["few_fills_note"].startswith("few trades (3) —")
    # The fact only (spec 05 R-L3): `strategy_phases` is in-app journey
    # posture, not served over MCP, so the line points at no topic.
    # SEED (run 2026-09-28): restore `; see keel_help(topic="strategy_
    # phases")` in `few_fills_next` — this reds; reverted by reversing it.
    assert env["few_fills_note"] == (
        "few trades (3) — a sparse signal or a window shorter than the warmup"
    )
    assert "keel_help" not in env["few_fills_note"]
    assert env["next"].startswith("2 completed runs on this strategy")
    # Control arm: the same call at the threshold carries no sample line.
    dense = {**_DETAIL, "metrics": {**_DETAIL["metrics"], "total_trades": 20}}
    other, _ = _run(
        detail=dense,
        runs={"data": [{"id": "btr_older", "status": "COMPLETED", "completed_at": recent}]},
    )
    assert "few_fills_note" not in other
    assert other["next"].startswith("2 completed runs on this strategy")


def test_a_held_position_is_not_called_a_sparse_signal():
    """Q-1844 (R3 probe): a buy-and-hold baseline read "few round trips (1)
    — a sparse signal…". One round trip that never CLOSED is a held
    position: the engine reports no win rate over zero closed trips, which
    is the fact the line now keys on.

    SEED (2026-09-23, reverted): delete the held-position branch in
    `few_fills_next` — the hold arm reds; the sparse control (3 round trips
    with a win rate) keeps its line either way."""
    from keel.tools.outcomes._backtest_view import few_fills_next

    held = {"trades": 1, "sharpe": 0.4}  # no win_rate_pct: nothing closed
    assert few_fills_next(held) is None
    held_basket = {"trades": 5}  # a constant-weight basket of 5, all open
    assert few_fills_next(held_basket) is None
    sparse = {"trades": 3, "win_rate_pct": 33.3}  # control: trades that closed
    assert few_fills_next(sparse).startswith("few trades (3) —")
    assert few_fills_next({"trades": 0}).startswith("few trades (0) —")


def test_the_next_line_reaches_the_model_through_the_text_block():
    """End to end: the §2.7 line and the §2.8 appender are one contract —
    a `next` the host never shows the model is not a hint."""
    import json
    from datetime import UTC, datetime, timedelta

    from keel.tools.outcomes._mcp_adapter import view_tool_result

    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    env, _ = _run(
        runs={"data": [{"id": "btr_older", "status": "COMPLETED", "completed_at": recent}]},
    )
    text = view_tool_result(json.dumps(env), "keel_backtest_run").content[0].text
    preface, _, body = text.partition("\n\n")
    assert preface.startswith("card: ")  # Q-1749 — a card-backed tool
    assert body.startswith("Backtest **Simple Momentum (ROC 20)** v3")
    assert "\nnext: 2 completed runs on this strategy in the last hour" in text


def test_a_card_backed_text_block_says_the_card_already_shows_it():
    """Q-1749: on ChatGPT a model restated the pipeline as a code block and
    pasted the tearsheet URLs the card already carried (claude.ai injects
    its own "the user can already see the result" note; ChatGPT does not).
    Every card-backed result's text block opens with CARD_SHOWN_LINE; a view
    tool with no card does not; and the line DESCRIBES what the user sees —
    it never addresses the host or its classifier (the Q-1748 class).

    # SEED: guard the preface with `if kind is None` in view_tool_result —
    # the card arm reds and the no-card control stays green.
    """
    import json

    from keel.tools.outcomes._mcp_adapter import CARD_SHOWN_LINE, view_tool_result
    from keel.widgets import CARD_TOOLS

    from tests.test_policy_scan import HOST_ADDRESSED_RE

    env = json.dumps({"view": {"markdown": "Strategy **X** v2\nView in Keel: https://x"}})
    carded = view_tool_result(env, "keel_strategy_compose").content[0].text
    assert carded.startswith(CARD_SHOWN_LINE + "\n\n")
    assert carded.rstrip().endswith("View in Keel: https://x")  # link line stays last
    # Control arm: a view tool with no card keeps its markdown verbatim.
    assert "keel_library_get" not in CARD_TOOLS
    plain = view_tool_result(env, "keel_library_get").content[0].text
    assert plain == "Strategy **X** v2\nView in Keel: https://x"
    assert not HOST_ADDRESSED_RE.search(CARD_SHOWN_LINE)


def test_the_card_line_names_what_the_card_carries():
    """Q-1896 (R4): agents pasted "Backtest ID: btr_…" and full app URLs under
    a card that carried them. The preface names the card's name, ids and
    links, and says what the ids and links in the text are for."""
    from keel.tools.outcomes._mcp_adapter import CARD_SHOWN_LINE

    for fact in ("name, links and ids", "call it by name", "hosts without cards"):
        assert fact in CARD_SHOWN_LINE, fact


def test_a_dry_run_is_prefaced_as_a_draft_check_not_as_what_the_user_sees():
    """Q-1896, the Q-1848 follow-up: a dry run's card is one quiet row, so the
    preface that says "what follows is shown to the user" was false for it and
    invited relaying parser internals. A dry run (either named signal) opens
    with DRAFT_CHECK_LINE; a save keeps CARD_SHOWN_LINE.

    # SEED (run 2026-09-23): make `_mcp_adapter._is_dry_run` return False —
    # both dry-run arms red while the save control arm stays green. Revert by
    # reversing the edit.
    """
    import json

    from keel.tools.outcomes._mcp_adapter import (
        CARD_SHOWN_LINE,
        DRAFT_CHECK_LINE,
        view_tool_result,
    )

    md = "Strategy **X** · Draft check\nvalidation: PARSE_ERROR at line 3"
    by_flag = json.dumps({"dry_run": True, "view": {"markdown": md}})
    by_status = json.dumps({"view": {"markdown": md, "status": "PREVIEW"}})
    saved = json.dumps({"view": {"markdown": "Strategy **X** v2", "status": "saved"}})

    for env in (by_flag, by_status):
        text = view_tool_result(env, "keel_strategy_compose").content[0].text
        assert text.startswith(DRAFT_CHECK_LINE + "\n\n"), text[:80]
        assert CARD_SHOWN_LINE not in text
        assert text.endswith(md)  # the detail still reaches the model
    # Control arm: a save is prefaced as what the user sees.
    text = view_tool_result(saved, "keel_strategy_compose").content[0].text
    assert text.startswith(CARD_SHOWN_LINE + "\n\n")
    assert DRAFT_CHECK_LINE != CARD_SHOWN_LINE


# ── A.5 — the dry-run receipt, `present` on compose / fork (§2.3) ─────


class _ComposeClient:
    """A compose-shaped fake: parse, versions, HEAD source, the run list."""

    def __init__(self, *, graph, versions=None, runs=None, head_source=None, patched=None):
        self.graph = graph
        self.versions = versions if versions is not None else []
        self.runs = runs if runs is not None else {"data": [], "pagination": {}}
        self.head_source = head_source
        self.patched = patched
        self.paths: list[str] = []

    def get(self, path, **kwargs):
        self.paths.append(path)
        if path.endswith("/versions"):
            return self.versions
        if path.endswith("/versions/HEAD/source"):
            return {"source": self.head_source, "sequence_number": 3}
        if path == "/v1/backtests":
            return self.runs
        if path.startswith("/v1/strategies/"):
            return {
                "strategy_id": "str_abc",
                "name": "demo",
                "current_sequence": 4,
                "status": "DRAFT",
                "graph": self.graph,
            }
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, **kwargs):
        self.paths.append(f"POST {path}")
        if path == "/v1/strategies/parse":
            return {"graph": self.graph, "valid": True}
        return {"strategy_id": "str_abc", "current_sequence": 4}

    def patch(self, path, **kwargs):
        self.paths.append(f"PATCH {path}")
        if self.patched is not None:
            return self.patched
        return {"strategy_id": "str_abc", "current_sequence": 4}


def _graph():
    return _adx_fixture()["metadata"]["graph"]


def _compose(args, monkeypatch, **client_kwargs):
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )
    monkeypatch.setattr("keel.hosting.is_hosted", lambda: True, raising=False)
    client = _ComposeClient(graph=_graph(), **client_kwargs)
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    env = OUTCOMES["keel_strategy_compose"].handler(args, ctx).to_envelope()
    return env, client


def test_dry_run_view_is_a_receipt_with_preview_line(monkeypatch):
    """Decision #3: a dry run is a STEP. Before this it drew the whole
    five-block card — the "Untitled" card the founder saw on the first
    compose of every session.

    # SEED: in strategy_compose._handler, pass size_override=None to
    # _dry_run_view on the dry-run arm — this row reds while the
    # control arm below stays green.
    """
    env, _ = _compose({"source": "Globals()", "dry_run": True}, monkeypatch)
    view = env["view"]
    assert view["size"] == "receipt"
    assert view["status"] == "PREVIEW"
    assert view["markdown"].strip() == "Preview · 8 blocks · valid"
    # Non-vacuity the seed cannot move: the view carries the whole
    # structure regardless of size, so the card can open in place
    # without a second call.
    assert len(view["structure"]["blocks"]) == 5
    assert view["kind"] == "strategy"


_NAMED_SOURCE = """\
Globals(target_timeframe="1d", bar_offset="12h")
Universe(mode="top_volume", top_n=30, market="perp")
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="sv_roc20")
"""


def test_a_dry_run_calls_the_strategy_what_the_source_calls_it(monkeypatch):
    """Q-1717: `view.name` was null on every dry run, so the preview
    receipt — the first card a user ever sees — introduced their
    strategy as `Untitled` seconds after they named it in the source
    they pasted. The emitter's graph carries no name and a dry run has
    no stored row, so nothing else in the envelope could supply one."""
    # SEED: in `strategy_compose._dry_run_view`, drop `_pipeline_name(source)`
    # from the `resolved_name` chain — the source-named row below reds
    # while the explicit-`name` row stays green, which IS the shipped
    # defect (the captures that showed it passed no `name`).
    env, _ = _compose({"source": _NAMED_SOURCE, "dry_run": True}, monkeypatch)
    assert env["view"]["name"] == "sv_roc20"
    # An explicit `name` argument outranks the source's — it is what the
    # caller asked the strategy be CALLED.
    named, _ = _compose(
        {"source": _NAMED_SOURCE, "name": "Simple Momentum (ROC 20)", "dry_run": True},
        monkeypatch,
    )
    assert named["view"]["name"] == "Simple Momentum (ROC 20)"
    # Not vacuous, read from the SOURCE rather than the render: the name
    # really is in the pipeline declaration and really is not in the
    # graph the server's parse returns, so it can only have come from
    # the new lookup.
    assert 'name="sv_roc20"' in _NAMED_SOURCE
    assert _graph().get("name") is None
    # A source that names nothing stays honest rather than inventing one.
    assert (
        _compose({"source": "Globals()", "dry_run": True}, monkeypatch)[0]["view"]["name"] is None
    )


def test_dry_run_present_view_is_the_structure(monkeypatch):
    """`present="view"` is the caller saying "this is the thing we are
    about to discuss" — the whole block tree, as before."""
    env, _ = _compose({"source": "Globals()", "dry_run": True, "present": "view"}, monkeypatch)
    assert env["view"]["size"] == "structure"
    assert len(env["view"]["markdown"].strip().splitlines()) > 5


@pytest.mark.parametrize(
    ("validation", "expected"),
    [
        ({"ok": True, "warnings": [], "errors": []}, "Preview · 8 blocks · valid"),
        (
            {"ok": True, "warnings": [{"message": "w"}], "errors": []},
            "Preview · 8 blocks · 1 warning",
        ),
        (
            {"ok": False, "warnings": [], "errors": [{"message": "a"}, {"message": "b"}]},
            "Preview · 2 errors",
        ),
    ],
)
def test_the_preview_receipt_says_exactly_one_validity_word(validation, expected, monkeypatch):
    """Never both `valid` and a warning count; with errors the block
    count is dropped — the errors are the news (BUILD §2.3)."""
    monkeypatch.setattr(
        "keel.tools.outcomes.strategy_compose._try_local_validate",
        lambda source, **_kw: {**validation, "lock": None},
    )
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile", lambda **kw: {"compiled": True}, raising=False
    )
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _ComposeClient(graph=_graph())
    ctx = ToolContext(api_client=client, is_tty=False)
    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": "Globals()", "dry_run": True}, ctx)
        .to_envelope()
    )
    assert env["view"]["markdown"].strip() == expected


def test_save_size_is_unchanged_by_present_absent(monkeypatch):
    """Control arm: without `present`, a save's size is still whatever
    the EVENT deserves — `present` adds a lever, it does not move the
    default."""
    env, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        head_source=None,
    )
    default_size = env["view"]["size"]
    forced, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc", "present": "receipt"},
        monkeypatch,
        head_source=None,
    )
    assert forced["view"]["size"] == "receipt"
    assert default_size == "structure" and default_size != forced["view"]["size"]
    # The receipt is re-rendered from the SAME graph the view carries —
    # no second read, and the structure survives for the in-place open.
    assert forced["view"]["structure"] == env["view"]["structure"]
    assert len(forced["view"]["markdown"].strip().splitlines()) <= 2


def test_fork_present_receipt_shrinks_the_view(monkeypatch):
    """A fork is a thing the user now HAS, so it renders whole by
    default; `present="receipt"` is for forking eight library entries to
    compare them."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _ComposeClient(graph=_graph())
    ctx = ToolContext(api_client=client, is_tty=False)
    whole = OUTCOMES["keel_strategy_fork"].handler({"source": "str_parent"}, ctx).to_envelope()
    receipt = (
        OUTCOMES["keel_strategy_fork"]
        .handler({"source": "str_parent", "present": "receipt"}, ctx)
        .to_envelope()
    )
    assert whole["view"]["size"] == "structure"
    assert receipt["view"]["size"] == "receipt"
    assert len(receipt["view"]["markdown"].strip().splitlines()) <= 2


# ── A.6 — the compose `next` lines (§2.7 rows 2 and 3) ────────────────


def test_next_names_dry_run_only_on_rapid_saves(monkeypatch):
    """Both arms. Every save is a version the user sees; a burst turns
    one edit into five rows in their history.

    # SEED: raise RAPID_SAVE_WINDOW_S to 0 in
    # keel/tools/outcomes/strategy_compose.py — the loud arm reds.
    """
    from datetime import UTC, datetime, timedelta

    just_now = (datetime.now(UTC) - timedelta(seconds=18)).isoformat().replace("+00:00", "Z")
    long_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")

    loud, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": just_now}],
    )
    assert loud["next"].startswith("Saved v4 1")
    assert "after v3" in loud["next"]
    # Spec 02 §2.4 #1(c): a fact and a call, never how to iterate.
    assert loud["next"].endswith(
        "each save is a version in the user's history; dry_run=true previews without saving."
    )
    assert "save once per user-visible step" not in loud["next"]

    # The negative arm asserts the key is ABSENT, not merely different.
    quiet, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": long_ago}],
        runs={"data": [{"id": "btr_1", "status": "COMPLETED"}], "pagination": {}},
    )
    assert "next" not in quiet
    # Non-vacuity the seed cannot move: both arms really did save, and
    # both reached the same version — so the difference between them is
    # the stamp, not the call.
    assert quiet["version"] == 4 and loud["version"] == 4


def test_an_unchanged_save_says_so_as_a_fact(monkeypatch):
    """Q-2102 (cohort wave 2026-09-29, `duplicate-backtest-submit`): a save
    keel-api answered `unchanged: true` wrote no version. The envelope says
    so as `unchanged: true` plus one data line, and nothing else — no advice
    about backtesting (founder: "dont say dont run or anything"). The
    rapid-save line is dropped: there was no save to be rapid.

    SEED (run 2026-09-29): read `result.get("unchanged") is True` as False in
    strategy_compose._handler → this arm reds; the changed-save control arm
    below stays green.
    """
    import json
    from datetime import UTC, datetime, timedelta

    from keel.tools.outcomes._mcp_adapter import view_tool_result

    just_now = (datetime.now(UTC) - timedelta(seconds=18)).isoformat().replace("+00:00", "Z")
    completed = {"data": [{"id": "btr_1", "status": "COMPLETED"}], "pagination": {}}
    same, client = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": just_now}],
        runs=completed,
        patched={"strategy_id": "str_abc", "current_sequence": 3, "unchanged": True},
    )
    assert same["unchanged"] is True
    assert "next" not in same  # no "Saved v3 18s after v3"
    text = view_tool_result(json.dumps(same), "keel_strategy_compose").content[0].text
    line = "unchanged: No changes — source matches the current version (v3)."
    assert line in text.splitlines(), text
    assert "backtest" not in line.lower() and "run" not in line.lower()

    # Control: a save that wrote v4 carries no key and no line, and keeps
    # its rapid-save line.
    changed, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": just_now}],
        runs=completed,
    )
    assert "unchanged" not in changed
    assert changed["next"].startswith("Saved v4")
    changed_text = view_tool_result(json.dumps(changed), "keel_strategy_compose").content[0].text
    assert "unchanged:" not in changed_text
    # Non-vacuity the seed cannot move: both arms reached the PATCH.
    assert "PATCH /v1/strategies/str_abc" in client.paths
    assert (same["version"], changed["version"]) == (3, 4)


def test_the_version_stamp_is_read_before_the_patch(monkeypatch):
    """The HEAD-source read compose already does returns no timestamp, so
    the rapid-save line has its own read — and it must happen while the
    head is still the PREVIOUS version."""
    from datetime import UTC, datetime, timedelta

    just_now = (datetime.now(UTC) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    _, client = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": just_now}],
    )
    versions_at = client.paths.index("/v1/strategies/str_abc/versions")
    patch_at = client.paths.index("PATCH /v1/strategies/str_abc")
    assert versions_at < patch_at, client.paths


def test_next_states_there_is_no_backtest_only_while_there_is_none(monkeypatch):
    """W5 P1, in the indicative (spec 05 R-L4 / D-13 L3): the line states
    that no completed run exists and stops — it never spells out the
    quota-spending call, and never a date.

    SEED (run 2026-09-28): restore the old return (`No backtest yet —
    keel_backtest_run(strategy_id=…) runs the platform's default window …`)
    — the equality and the `keel_backtest_run` assertion red; the two
    control arms below stay green. Reverted by reversing the edit."""
    fresh, _ = _compose({"source": "Globals()", "name": "demo"}, monkeypatch)
    assert fresh["next"] == "No backtest of this strategy has been run yet."
    assert "keel_backtest_run" not in fresh["next"]
    assert "2024-08-15" not in fresh["next"]

    already, _ = _compose(
        {"source": "Globals()", "name": "demo"},
        monkeypatch,
        runs={"data": [{"id": "btr_1", "status": "COMPLETED"}], "pagination": {}},
    )
    assert "next" not in already
    # A run that never completed is not a backtest the user has.
    running, _ = _compose(
        {"source": "Globals()", "name": "demo"},
        monkeypatch,
        runs={"data": [{"id": "btr_1", "status": "RUNNING"}], "pagination": {}},
    )
    assert "next" in running


def test_the_rapid_save_line_wins_over_the_first_backtest_line(monkeypatch):
    """Only one `next` rides an envelope. A burst of saves is the more
    urgent thing to say; "no backtest yet" is still true next time."""
    from datetime import UTC, datetime, timedelta

    just_now = (datetime.now(UTC) - timedelta(seconds=9)).isoformat().replace("+00:00", "Z")
    env, _ = _compose(
        {"source": "Globals()", "strategy_id": "str_abc"},
        monkeypatch,
        versions=[{"sequence_number": 3, "created_at": just_now}],
    )
    assert env["next"].startswith("Saved v4")
    assert "No backtest of this strategy" not in env["next"]


# ── A.4 — compare 2–8, the comparison view (§2.2) ─────────────────────

_CMP_SRC = """\
Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH"])
Execution(rebalance="buffered", buffer_threshold={threshold})
Pipeline([
    PriceDataLoader(timeframe="1d"),
    ROC(period=8),
])
"""

#: The founder's turn 3 shape: one declaration varying across the set.
_THRESHOLDS = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70)


def _cmp_detail(index, *, strategy_id="str_mom", start="2024-08-15", end="2026-09-22"):
    return {
        "id": f"btr_{index}",
        "status": "COMPLETED",
        "strategy_id": strategy_id,
        "strategy_name": f"Momentum {strategy_id[-3:]}",
        "sequence_number": index + 1,
        "commit_id": f"c_{index}",
        "engine": "native",
        "start_date": start,
        "end_date": end,
        "metrics": {
            "sharpe_ratio": 0.60 + index * 0.03,
            "total_return": 50.0 + index * 5,
            "max_drawdown": 45.0 - index,
            "total_trades": 1761,
            "total_fees_paid": 1217.0 - index * 50,
            "turnover": 12.0,
            "trade_model": "reducing_order",
            "funding_included": True,
        },
    }


class _CompareClient:
    def __init__(self, details, *, sources=None, curve=None, messages=None):
        self.details = {d["id"]: d for d in details}
        self.sources = sources or {}
        self.messages = messages or {}
        self.curve = curve
        self.paths: list[str] = []

    def get(self, path, **kwargs):
        self.paths.append(path)
        if path.endswith("/curve"):
            # HONOUR the requested resolution (Q-1708): a fake that
            # returns a fixed-size curve whatever `points=` says makes
            # every budget assertion over it blind to the one lever that
            # sets the size. `points=N` buckets ⇒ N + 1 samples.
            if callable(self.curve):
                return self.curve(kwargs.get("points"))
            return self.curve or {}
        for run_id, detail in self.details.items():
            if path == f"/v1/backtests/{run_id}":
                return detail
        for (sid, cid), src in self.sources.items():
            if path == f"/v1/strategies/{sid}/versions/{cid}/source":
                return {"source": src}
        # The version's commit message — the run label's fallback (Q-1792).
        for detail in self.details.values():
            if path == f"/v1/strategies/{detail['strategy_id']}/versions/{detail['commit_id']}":
                return {"message": self.messages.get(detail["commit_id"])}
        raise AssertionError(f"unexpected GET {path}")


def _compare(details, *, sources=None, curve=None, messages=None):
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _CompareClient(details, sources=sources, curve=curve, messages=messages)
    ctx = ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")
    env = (
        OUTCOMES["keel_backtest_compare"]
        .handler({"backtest_ids": [d["id"] for d in details]}, ctx)
        .to_envelope()
    )
    return env, client


def _compare_varying(n):
    details, sources = _varying_set(n)
    return _compare(details, sources=sources)


def _varying_set(n):
    details = [_cmp_detail(i) for i in range(n)]
    sources = {
        (d["strategy_id"], d["commit_id"]): _CMP_SRC.format(threshold=_THRESHOLDS[i])
        for i, d in enumerate(details)
    }
    return details, sources


def test_compare_labels_from_the_one_declaration_that_varies_across_the_set():
    """Set-wide, not pairwise. A pairwise "exactly one declaration
    differs" rule cannot label the BASELINE — it has nothing to differ
    from — and it fails the founder's own v1→v2, which changed two keys.

    # SEED: in _run_label, return `stem` unconditionally — this row reds
    # while the two-key row below stays green.
    """
    details, sources = _varying_set(4)
    env, _ = _compare(details, sources=sources)
    labels = [run["label"] for run in env["view"]["runs"]]
    assert labels == ["v1 · buffer 0.05", "v2 · buffer 0.1", "v3 · buffer 0.2", "v4 · buffer 0.3"]
    # The BASELINE is labelled too — the half a pairwise rule cannot do.
    assert labels[0].endswith("0.05")
    # Non-vacuity the seed cannot move: four DISTINCT labels over four
    # runs, and no run id anywhere in them.
    assert len(set(labels)) == 4
    assert not any("btr_" in label for label in labels)


def test_two_varying_declarations_label_bare():
    """The founder's v1→v2 changed `rebalance` AND `buffer_threshold`;
    naming one of them would be a claim the set does not support."""
    details = [_cmp_detail(0), _cmp_detail(1)]
    sources = {
        ("str_mom", "c_0"): _CMP_SRC.format(threshold=0.05),
        ("str_mom", "c_1"): (
            'Globals(target_timeframe="4h")\n'
            'Universe(mode="manual", symbols=["BTC", "ETH"])\n'
            'Execution(rebalance="every_bar")\n'
            "Pipeline([\n    PriceDataLoader(timeframe='1d'),\n    ROC(period=8),\n])\n"
        ),
    }
    env, _ = _compare(details, sources=sources)
    assert [run["label"] for run in env["view"]["runs"]] == ["v1", "v2"]


def test_compare_across_strategies_has_no_hero_and_names_strategies():
    """Q-1686's rule: runs that span strategies have no single target, so
    the envelope says so rather than inventing one."""
    details = [_cmp_detail(0, strategy_id="str_aaa"), _cmp_detail(1, strategy_id="str_bbb")]
    env, _ = _compare(details)
    view = env["view"]
    assert view["name"] == "2 strategies"
    assert [run["label"] for run in view["runs"]] == ["Momentum aaa", "Momentum bbb"]
    assert env.get("hero_url") is None and "hero_url" not in env
    assert "url_line" not in view
    assert "Different strategies — structure and universe may differ." in view["warnings"]
    assert "strategy_id" not in env
    # Control arm: one shared strategy DOES get the editor as its one
    # destination, so the absence above is the envelope's answer.
    shared, _ = _compare([_cmp_detail(0), _cmp_detail(1)])
    assert shared["hero_url"] == "https://app.usekeel.io/strategies/str_mom/edit"
    assert shared["strategy_id"] == "str_mom"
    assert shared["view"]["url_line"].endswith("/strategies/str_mom/edit")


def test_compare_eight_is_the_cap_nine_is_usage_error():
    from keel.errors import KeelError
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    details, sources = _varying_set(8)
    env, _ = _compare(details, sources=sources)
    assert len(env["view"]["runs"]) == 8
    # Nine is refused before any read.
    client = _CompareClient([])
    ctx = ToolContext(api_client=client, is_tty=False)
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_backtest_compare"].handler({"backtest_ids": ["a"] * 9}, ctx)
    assert exc.value.exit_code == 2
    assert client.paths == [], "a refused call must read nothing"


def test_two_id_pair_keys_are_byte_identical_to_before():
    """The §2.2 compatibility golden: this EXACT key set keeps its
    shape. New keys are added beside them; none of these changes.

    # SEED: rename `run_a` to `runs[0]` in the two-id arm — this row
    # reds, which is the whole point: every existing caller, fixture and
    # golden reads these names.
    """
    details = [_cmp_detail(0), _cmp_detail(1)]
    sources = {
        ("str_mom", "c_0"): _CMP_SRC.format(threshold=0.05),
        ("str_mom", "c_1"): _CMP_SRC.format(threshold=0.10),
    }
    env, _ = _compare(details, sources=sources)
    pair_keys = {
        "run_a",
        "run_b",
        "performance",
        "cost_profile",
        "comparability_warnings",
        "metrics_raw_a",
        "metrics_raw_b",
        "summary_text",
        "spec_diff",
    }
    assert pair_keys <= set(env), sorted(pair_keys - set(env))
    assert env["run_a"]["backtest_id"] == "btr_0"
    assert env["performance"]["sharpe_ratio"]["a"] == 0.60
    assert env["performance"]["sharpe_ratio"]["b"] == pytest.approx(0.63)
    assert env["performance"]["sharpe_ratio"]["delta"] == pytest.approx(0.03)
    assert env["metrics_raw_b"] == details[1]["metrics"]
    # Non-vacuity the seed cannot move: the pair keys are ENUMERATED (a
    # subset check over an empty set would pass on anything).
    assert len(pair_keys) == 9
    # For > 2 ids the A/B-SHAPED keys are OMITTED rather than
    # reinterpreted — one key never carries two shapes. The two
    # shape-neutral members of the pair set (`comparability_warnings`,
    # `summary_text`) are lists/strings at any arity and stay.
    ab_shaped = pair_keys - {"comparability_warnings", "summary_text"}
    many, _ = _compare_varying(3)
    assert not (ab_shaped & set(many)), sorted(ab_shaped & set(many))
    assert {"comparability_warnings", "summary_text"} <= set(many)
    assert isinstance(many["performance_by_run"]["sharpe_ratio"], list)
    # `spec_diffs` is the N-shape on BOTH arms: baseline-vs-each by index.
    assert env["spec_diffs"][0] is None and len(env["spec_diffs"]) == 2
    assert len(many["spec_diffs"]) == 3


def test_deltas_are_each_run_minus_the_baseline():
    """`deltas[i]` answers "what did run i change against the first id" —
    the question a reader comparing an iteration is asking.

    # SEED: flip the subtraction in comparison_deltas — this row reds.
    """
    details, sources = _varying_set(3)
    env, _ = _compare(details, sources=sources)
    view = env["view"]
    assert view["baseline"] == 0
    assert view["deltas"][0] is None
    assert view["deltas"][1]["sharpe"] == pytest.approx(0.03)
    assert view["deltas"][2]["sharpe"] == pytest.approx(0.06)
    # Fees FELL across the set, so the sign is evidence rather than a
    # constant — a flipped subtraction cannot pass this.
    assert view["deltas"][2]["fees_paid"] == pytest.approx(-100.0)
    # Non-vacuity: the baseline's own metrics are non-empty.
    assert view["runs"][0]["metrics"]["sharpe"] == 0.60


def test_every_run_says_what_it_differs_from_the_baseline_in():
    """Q-1714: `card-compare.js` reads `view.runs[i].diff`, the wire
    never carried it, and the row whose whole job is to say what varied
    read `—` in every column on every real result. The information was
    one level up in `spec_diffs[i]`, in a shape the card does not read."""
    # SEED: in `backtest_compare._handler`, delete the `if index:` block
    # that sets `run["diff"]` — every row below reds, which IS the
    # shipped defect.
    details, sources = _varying_set(4)
    env, _ = _compare(details, sources=sources)
    runs = env["view"]["runs"]
    # The baseline differs from nothing and says nothing; every other
    # run names the declaration that moved, in the SAME vocabulary its
    # own label uses.
    assert "diff" not in runs[0]
    assert [r["diff"] for r in runs[1:]] == ["buffer 0.1", "buffer 0.2", "buffer 0.3"]
    assert all(r["diff"] in r["label"] or r["diff"].split()[0] in r["label"] for r in runs[1:])
    # Not vacuous, read from the SOURCES rather than the render: the
    # four runs really do differ in exactly this declaration, and the
    # envelope really does carry the spec diff the cell is projected
    # from — so an empty cell could only be the projection.
    assert len({src.strip() for src in sources.values()}) == 4
    assert env["spec_diffs"][1]["declarations"]["execution"]["buffer_threshold"] == {
        "a": 0.05,
        "b": 0.1,
    }


def test_a_run_whose_spec_matches_the_baseline_says_so_rather_than_nothing():
    """An em dash is indistinguishable from "the envelope carried no
    diff" — which is exactly how the broken row read as "these runs are
    identical" (Q-1714). A run that IS identical says the word."""
    # SEED: in `_run_diff`, return `""` in place of `DIFF_IDENTICAL` —
    # the identical row reds while the window row stays green.
    same = _CMP_SRC.format(threshold=0.05)
    details = [_cmp_detail(0), _cmp_detail(1)]
    env, _ = _compare(
        details,
        sources={(d["strategy_id"], d["commit_id"]): same for d in details},
    )
    assert env["view"]["runs"][1]["diff"] == "identical"
    # A period difference is a difference even when the spec matches.
    shifted = [_cmp_detail(0), _cmp_detail(1, start="2025-01-01")]
    env2, _ = _compare(
        shifted,
        sources={(d["strategy_id"], d["commit_id"]): same for d in shifted},
    )
    assert env2["view"]["runs"][1]["diff"] == "window"
    # Not vacuous: both arms really compared two runs off ONE source, so
    # the two verdicts differ for the window and nothing else.
    assert env["spec_diffs"][1]["declarations"] == {}
    assert env2["spec_diffs"][1]["declarations"] == {}
    assert details[0]["start_date"] == shifted[0]["start_date"]


def test_the_comparison_markdown_is_a_table_to_four_and_lines_beyond():
    """Q-1710: without the GFM delimiter row the block is not a table at
    all — six pipe-filled paragraphs — and `_plain`'s trailing-zero strip
    put `0.6` in a column beside `0.82`."""
    # SEED: delete the `lines.append("| --- | " + ...)` line in
    # `_comparison_markdown` — the delimiter row below reds, which IS
    # what staging shipped.
    details, sources = _varying_set(4)
    env, _ = _compare(details, sources=sources)
    lines = env["view"]["markdown"].strip().splitlines()
    assert lines[0].startswith("**Momentum mom** · 4 runs · Aug 15, 2024 – Sep 21, 2026")
    assert (
        lines[1] == "| | v1 · buffer 0.05 | v2 · buffer 0.1 | v3 · buffer 0.2 | v4 · buffer 0.3 |"
    )
    # One delimiter cell per HEADER cell (the empty row-label column
    # included, else the column count disagrees and GFM draws nothing),
    # run columns right-aligned so a reader compares down them.
    assert lines[2] == "| --- | ---: | ---: | ---: | ---: |"
    assert lines[2].count("|") == lines[1].count("|") == 6
    # Q-1746 / Q-1787: the card's rows are the backtest view's four
    # headline tiles, then its "more" tiles, in the app's order and with its
    # labels — served as `view.rows`; the markdown table draws the four.
    assert [r["label"] for r in env["view"]["rows"]] == [
        "Return",
        "Max drawdown",
        "Sharpe",
        "Win rate",
        "Trades",
        "Turnover (× capital)",
        "Sortino",
        "Calmar",
        "Profit factor",
        "Fees paid",
    ]
    trades_row = next(r for r in env["view"]["rows"] if r["label"] == "Trades")
    assert trades_row["key"] == "trades" and trades_row["format"] == "count"
    assert [line.split(" |")[0] for line in lines[3:7]] == [
        "| Return",
        "| Max drawdown",
        "| Sharpe",
        "| Win rate",
    ]
    # Every cell of a numeric row at the SAME precision: `0.60`, not `0.6`.
    assert lines[5].startswith("| Sharpe | 0.60 | 0.63 |")
    assert any(line.startswith("Runs: v1 · buffer 0.05 https://") for line in lines)
    # Not vacuous: four runs really rendered, so the column count above
    # is counting real columns.
    assert len(env["view"]["runs"]) == 4

    wide, _ = _compare_varying(8)
    wide_lines = wide["view"]["markdown"].strip().splitlines()
    assert not any(line.startswith("| ") for line in wide_lines)
    assert sum(1 for line in wide_lines if " — Return " in line) == 8
    # No run id in the drawn text (the link lines are links).
    prose = [line for line in wide_lines if not line.startswith("View in Keel:")]
    assert not any("btr_" in line.split(" · https")[0] for line in prose)


def test_eight_run_envelope_is_under_budget():
    """§2.2: an eight-run envelope stays under 60 KB with its curves.

    The heavy arm of this — staging-shaped points, the whole envelope,
    and a seed that reproduces the 76 KB measurement — is
    `test_outcomes_backtest_compare.test_an_eight_run_compare_envelope_stays_inside_the_host_budget`
    (Q-1708). This row is the wiring half: the resolution really is spent
    per ENVELOPE, so the curve the server is asked for shrinks at eight
    ids. It asserted `== 120` at eight runs against a fake that ignored
    `points=` entirely, which is how it agreed with a per-RUN budget.
    """
    import json

    from keel.tools.outcomes.backtest_compare import compare_curve_points

    def curve(points):
        # `points=N` buckets ⇒ N + 1 samples: both endpoints inclusive.
        return {
            "points": [
                {
                    "t": f"2024-08-{(i % 28) + 1:02d}T00:00:00Z",
                    "equity": 1000.0 + i,
                    "drawdown_pct": -1.0,
                }
                for i in range(points + 1)
            ],
            "start": "2024-08-15T00:00:00Z",
            "end": "2026-09-22T00:00:00Z",
            "source_points": 54000,
        }

    details, sources = _varying_set(8)
    env, client = _compare(details, sources=sources, curve=curve)
    payload = json.dumps(env, default=str)
    assert len(payload.encode()) < 60_000, len(payload.encode())
    assert len(env["view"]["runs"]) == 8
    # The run curves share the budget (spec 03 §2.2); no `holds` is asked,
    # so no hold line is counted (Q-2441 — the `+ 1` here was a phantom).
    expected = compare_curve_points(8) + 1
    assert all(len(run["curve"]["points"]) == expected for run in env["view"]["runs"])
    assert client.paths.count("/v1/backtests/btr_0/curve") == 1
    # Not vacuous, and the row that makes the budget per-ENVELOPE rather
    # than per-run: two ids keep the full single-run resolution, so this
    # is measuring a resolution that actually moves with the set size.
    pair_details, pair_sources = _varying_set(2)
    two, _ = _compare(pair_details, sources=pair_sources, curve=curve)
    assert expected < compare_curve_points(2) + 1
    assert all(
        len(run["curve"]["points"]) == compare_curve_points(2) + 1 for run in two["view"]["runs"]
    )


# ── The six tools' W1 copy, landed with the code (Q-1690) ─────────────


def test_the_cadence_sentences_left_the_descriptions():
    """The render cadence's W1 copy (BUILD §3.1) put a rendering sentence
    in each tool's description. agent-surface-cleanup spec 01 §2.5 made
    that a FORBIDDEN class: the server's defaults and the `present`
    parameter's own description carry the cadence, and the descriptions
    state what each tool does and returns. The facts stay.

    # SEED: re-add "The result renders as a one-line receipt." to
    # keel/tools/outcomes/backtest_run.py's description — this reds.
    """
    import re

    from keel.tools.outcomes import OUTCOMES, _bootstrap

    from pipeline_engine.reference.system import assemble

    _bootstrap()

    def text(name):
        return OUTCOMES[name].description

    cadence = next(p for n, p, _ in assemble.DESCRIPTION_CLASSES if n == "rendering-cadence")
    for name in (
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_backtest_summarize",
        "keel_backtest_compare",
        "keel_strategy_compose",
        "keel_strategy_get",
    ):
        assert not re.search(cadence, text(name), re.IGNORECASE), name
    assert "Show ONE completed backtest run in full" in text("keel_backtest_summarize")
    assert "Compare 2–8 completed backtests" in text("keel_backtest_compare")
    compose = text("keel_strategy_compose")
    assert "`dry_run=true` validates and compiles without saving" in compose
    assert "adds a new version to the strategy's history" in compose
    assert "never needs repeating" not in compose

    # The conduct the descriptions used to carry is GONE from all six —
    # it lives in the operating core and the skills now.
    for name in (
        "keel_backtest_run",
        "keel_backtest_watch",
        "keel_backtest_summarize",
        "keel_backtest_compare",
        "keel_strategy_compose",
        "keel_strategy_fork",
    ):
        for banned in ("BE PROACTIVE", "Do NOT ask", "FIRST-TIME", "just run it"):
            assert banned not in text(name), f"{name} still says {banned!r}"
        # Non-vacuity on a quantity no wording change can move: every one
        # still names a neighbour — as a neutral fact since Q-1804, never as
        # an imperative `Do NOT use … — call`.
        assert "`keel_" in text(name) and "Do NOT" not in text(name), name


def test_both_compose_strings_fit_claude_codes_two_kilobyte_cut():
    """2 KB is BYTES: W1's compose is 1,998 chars and 2,004 of them. The
    tail past the cut is dropped SILENTLY, which is why this is measured
    rather than eyeballed."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    compose = OUTCOMES["keel_strategy_compose"]
    assert len(compose.description.encode()) <= 2048
    assert len(compose.listed_description.encode()) <= 2048
    # The listed string differs by the one surface fact a hosted caller
    # cannot act on (R-4): the local checkout write-back.
    assert "workspace_sync" in compose.description
    assert "workspace_sync" not in compose.listed_description
    # Armed on this tree (G-B): the validator owns these facts now, so the
    # sentences left BOTH strings (spec 01 §2.5 paragraph 4, R-1). The
    # branch-sizing sentence (review 06 M-1's corrected fact) left too once
    # NORMALIZER_BEFORE_CONCAT reached WARNING — `rule:` owner in assemble.py.
    for text in (compose.description, compose.listed_description):
        assert "`FixedWeightSizer` needs a `LeverageCap`" not in text
        assert "not `EqualWeightAllocator`" not in text
        assert "Each branch sizes to WeightSeries before `WeightConcatenator`" not in text
    # Non-vacuity: the DSL skeleton — the reason the string is near the
    # cut at all — is in BOTH.
    for text in (compose.description, compose.listed_description):
        assert "Declarations, then one Pipeline:" in text
        assert "ForecastWeightNormalizer(target_leverage=1.0)" in text
        # Q-1947 (2026-09-25): ChatGPT's approval gate flagged compose for
        # "specifying validation/help methods", so the skill pointer left the
        # description; the server instructions' head and keel_help carry it
        # (test_listed_skill_routing pins both).
        assert 'topic="skill:' not in text
        assert "keel_help" not in text


# ── A.7 — the CLI human renderer (§3.4) ───────────────────────────────


def _cli_render(envelope: dict) -> str:
    import click
    from click.testing import CliRunner
    from keel.tools.outcomes import _cli_adapter
    from keel.tools.outcomes._base import OutcomeResult

    result = OutcomeResult(
        run_id=envelope.get("run_id"),
        hero_url=envelope.get("hero_url"),
        extra={k: v for k, v in envelope.items() if k not in ("run_id", "hero_url")},
    )

    @click.command()
    def show():
        _cli_adapter._render(result, "human")

    return CliRunner().invoke(show, []).output


def test_cli_human_format_prints_backtest_markdown_first_and_skips_the_curve():
    """A terminal draws no chart, so `curve` is 240 rows of numbers right
    after a one-line receipt. Skipped the way `view` is.

    # SEED: delete the `if k == "curve"` skip in
    # keel/tools/outcomes/_cli_adapter.py::_render — this row reds.
    """
    env, _ = _run(curve=_CURVE)
    out = _cli_render(env)
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines[0].startswith("Backtest **Simple Momentum (ROC 20)** v3 · Return")
    assert "2024-08-15T00:00:00Z" not in out
    assert "curve" not in out
    # Non-vacuity the seed cannot move: the envelope really carries a
    # curve with points, and the rest of the envelope still prints.
    assert env["curve"]["points"]
    assert "status" in out and out.strip()


def test_cli_human_format_prints_the_comparison_markdown_first():
    env, _ = _compare_varying(4)
    out = _cli_render(env)
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines[0].startswith("**Momentum mom** · 4 runs")
    assert lines[1].startswith("| | v1 · buffer 0.05 |")
    # The per-run curves stay out of the terminal too.
    assert "source_points" not in out


def test_the_claude_code_hint_no_longer_asks_for_a_second_metrics_table():
    """BUILD §3.4: the view IS the table; a hint asking for another one
    is the same numbers twice."""
    from keel.tools.outcomes._render import SURFACE_HINTS

    hint = SURFACE_HINTS["claude_code"]
    assert "`view.markdown` is the complete rendering" in hint
    assert "metrics table" not in hint
    # Non-vacuity: the plain link line is still named on every
    # surface — the fact this rewrite must not have dropped.
    assert all("link line" in text for text in SURFACE_HINTS.values())


def test_run_and_watch_carry_the_render_block_the_way_summarize_does():
    env, _ = _run(curve=_CURVE)
    watched, _ = _watch(curve=_CURVE)
    for envelope in (env, watched):
        assert envelope["render"]["card"] == "backtest"
        assert envelope["render"]["card_resource_uri"] == "ui://keel/cards/backtest.html"
        assert envelope["render"]["fallback_url"] == envelope["hero_url"]


# ── Measurement eras (trade-metrics spec 01 §4, §6.2 — Q-2122) ─────────
#
# SEED (run 2026-09-30): delete the Era B entry of
# `_backtest_view._ERA_KEYS` (the read model's B branch) → every Era B arm
# below goes red (its count, win rate and cost rows read nothing), while
# the A, C and unknown arms stay green. Reverted by reversing the edit.

_ERA_HEAD = {"sharpe": 0.67, "total_return_pct": 60.5, "max_drawdown_pct": -20.1}
ERA_METRICS = {
    "A": {**_ERA_HEAD, "total_trades": 1761, "win_rate_pct": 26.0, "profit_factor": 1.08},
    "B": {
        **_ERA_HEAD,
        "total_trades": 53,
        "win_rate": 40.0,
        "profit_factor": 1.6,
        "rebalance_legs": 2437,
        "turnover": 65.3,
        "trade_model": "position_round_trip",
    },
    "C": {
        **_ERA_HEAD,
        "total_trades": 1198,
        "win_rate": 84.8,
        "profit_factor": 1.9,
        "positions": 53,
        "position_win_rate": 40.0,
        "resizes": 2437,
        "turnover": 65.3,
        "avg_holding_duration": "12 days 04:00:00",
        "trade_model": "reducing_order",
    },
    "unknown": {
        **_ERA_HEAD,
        "total_trades": 99,
        "win_rate": 50.0,
        "trade_model": "flat_to_flat_v9",
    },
}

#: (headline line, more line or None, basis line or None) — the §6.2 labels.
ERA_EVIDENCE = {
    "A": (
        "Return +60.5% · Max drawdown −20.1% · Sharpe 0.67 · Win rate 26.0%",
        "Trades 1,761 · Profit factor 1.08",
        "Positions are not recorded for this run.",
    ),
    "B": (
        "Return +60.5% · Max drawdown −20.1% · Sharpe 0.67 · Position win rate 40.0%",
        "Positions 53 · Resizes 2,437 · Turnover (× capital) 65.3x · "
        "Profit factor (per position) 1.60",
        "This run counted positions, not trades; its trade count is not recorded.",
    ),
    "C": (
        "Return +60.5% · Max drawdown −20.1% · Sharpe 0.67 · Win rate 84.8%",
        "Trades 1,198 · Positions 53 · Position win rate 40.0% · Resizes 2,437 · "
        "Turnover (× capital) 65.3x · Avg holding time 12.2 days · Profit factor 1.90",
        None,
    ),
    "unknown": ("Return +60.5% · Max drawdown −20.1% · Sharpe 0.67 · Win rate —", None, None),
}


def _era_detail(era: str) -> dict:
    return {**_DETAIL, "metrics": ERA_METRICS[era]}


def build_backtest_view(*args, **kwargs):
    from keel.tools.outcomes._backtest_view import build_backtest_view as build

    return build(*args, **kwargs)


@pytest.mark.parametrize("era", ["A", "B", "C", "unknown"])
def test_each_era_is_labelled_by_what_it_recorded(era):
    view = build_backtest_view(_era_detail(era), size="evidence", url=_URL)
    lines = view["markdown"].strip().splitlines()
    headline, more, basis = ERA_EVIDENCE[era]
    expected = [headline] + [x for x in (more, basis) if x is not None]
    assert lines[1 : 1 + len(expected)] == expected
    assert view.get("count_basis") == basis
    # The receipt says the same fourth label.
    receipt = build_backtest_view(_era_detail(era), size="receipt", url=_URL)["markdown"]
    assert headline.split(" · ")[3] in receipt
    # "—" means not recorded, never 0; the stamp is never rendered.
    assert "position_round_trip" not in json.dumps(view)
    assert "reducing_order" not in view["markdown"]


def test_an_unknown_era_shows_no_trade_number_and_says_so(caplog):
    with caplog.at_level("WARNING", logger="keel.tools.outcomes._backtest_view"):
        view = build_backtest_view(_era_detail("unknown"), size="evidence", url=_URL)
    assert not {"trades", "win_rate_pct", "positions", "position_win_rate_pct"} & set(
        view["metrics"]
    )
    assert any("flat_to_flat_v9" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("era", "note"),
    [
        ("A", "few trades (3)"),
        ("B", "few positions (3)"),
        ("C", "few positions (3)"),
        ("unknown", None),
    ],
)
def test_the_sample_note_keys_on_positions_when_recorded(era, note):
    from keel.tools.outcomes._backtest_view import few_fills_next, view_metrics

    small = {
        "A": {"total_trades": 3, "win_rate_pct": 33.3},
        "B": {"total_trades": 3, "win_rate": 33.3, "trade_model": "position_round_trip"},
        # 40 trades but 3 positions: the sample is the positions.
        "C": {
            "total_trades": 40,
            "win_rate": 60.0,
            "positions": 3,
            "position_win_rate": 33.3,
            "trade_model": "reducing_order",
        },
        "unknown": {"total_trades": 3, "win_rate": 33.3, "trade_model": "flat_to_flat_v9"},
    }[era]
    got = few_fills_next(view_metrics(small))
    assert (got.split(" — ")[0] if got else None) == note


@pytest.mark.parametrize(
    ("era", "count", "label"),
    [
        ("A", 1761, "trades"),
        ("B", 53, "positions"),
        ("C", 1198, "trades"),
        ("unknown", None, "trades"),
    ],
)
def test_the_strategy_evidence_names_its_count_by_era(era, count, label):
    from keel.tools.outcomes._strategy_view import _evidence, _evidence_line

    evidence = _evidence(ERA_METRICS[era], version=3, start="2024-08-15", end="2026-09-22")
    assert (evidence["total_trades"], evidence["count_label"]) == (count, label)
    shown = f"{count:,}" if count is not None else "—"
    assert f"{shown} {label}" in _evidence_line(evidence)


def test_the_era_fixtures_are_non_vacuous():
    # Four eras, and each arm's expected layout is its own.
    assert set(ERA_METRICS) == set(ERA_EVIDENCE) == {"A", "B", "C", "unknown"}
    assert len({v[0] for v in ERA_EVIDENCE.values()}) == 4
    assert ERA_METRICS["B"]["trade_model"] == "position_round_trip"
    assert ERA_METRICS["C"]["trade_model"] == "reducing_order"
    assert "trade_model" not in ERA_METRICS["A"]
