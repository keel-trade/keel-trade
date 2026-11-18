"""A null win rate says why, and only when it is null (R-009).

A user's agent read a run with trades and `win_rate: null`, and a strategy
with `latest_backtest_metrics: null`, and could not tell why either was
empty. The engine now records `closed_trades` / `open_trades` (their sum is
`total_trades`; the win rate reads the closed half), and the views carry one
short line when a number is absent for a reason the result can state:

* a run with trades, none closed → `NO_CLOSED_TRADE_LINE` (`view.count_basis`,
  drawn by the card and the evidence markdown);
* a run with no trades → `NO_TRADES_LINE`;
* a strategy view with no evidence → `NO_EVIDENCE_LINES[backtest_state]`.

A run whose win rate is a number carries none of them (the control arms).
SEED (run 2026-10-28, reverted by reversing the edit): make `win_rate_note`
return `NO_CLOSED_TRADE_LINE` unconditionally after the zero-trades check —
5 red: the control `test_a_closed_trade_carries_no_note`, the measured half
of the older-run arm, and three `test_render_cadence` rows whose runs carry
a win rate (the nine-metrics markdown, eras A and C). The no-closed and
no-trades arms stay green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from keel.tools.outcomes._backtest_view import (
    COUNT_BASIS,
    NO_CLOSED_TRADE_LINE,
    NO_TRADES_LINE,
    build_backtest_view,
    view_metrics,
    win_rate_note,
)
from keel.tools.outcomes._strategy_view import NO_EVIDENCE_LINES, build_view
from keel.tools.outcomes.backtest_summarize import _extract_summary_metrics


_HEAD = {"sharpe_ratio": 0.4, "total_return": 12.0, "max_drawdown": 9.0}


def _current(**metrics) -> dict:
    return {**_HEAD, "trade_model": "reducing_order", **metrics}


def _detail(metrics: dict) -> dict:
    return {
        "id": "btr_r009",
        "status": "COMPLETED",
        "strategy_name": "Held",
        "sequence_number": 1,
        "start_date": "2025-01-01",
        "end_date": "2025-07-01",
        "metrics": metrics,
    }


_HELD = _current(total_trades=4, closed_trades=0, open_trades=4, win_rate=None, positions=4)
_CLOSED = _current(total_trades=4, closed_trades=1, open_trades=3, win_rate=100.0, positions=4)


def test_the_counts_ride_view_metrics_and_summary_metrics():
    shown = view_metrics(_HELD)
    assert (shown["trades"], shown["closed_trades"], shown["open_trades"]) == (4, 0, 4)
    assert "win_rate_pct" not in shown
    summary = _extract_summary_metrics(_HELD)
    assert (summary["closed_trades"], summary["open_trades"]) == (0, 4)


@pytest.mark.parametrize("size", ["receipt", "evidence"])
def test_trades_with_none_closed_say_why_win_rate_is_absent(size):
    view = build_backtest_view(_detail(_HELD), size=size)
    assert view["count_basis"] == NO_CLOSED_TRADE_LINE
    if size == "evidence":
        assert NO_CLOSED_TRADE_LINE in view["markdown"]


def test_a_closed_trade_carries_no_note():
    # Control: same run shape, one trade closed, a win rate measured.
    assert win_rate_note(view_metrics(_CLOSED)) is None
    view = build_backtest_view(_detail(_CLOSED), size="evidence")
    assert "count_basis" not in view
    assert NO_CLOSED_TRADE_LINE not in view["markdown"]
    assert NO_TRADES_LINE not in view["markdown"]


def test_no_trades_says_so():
    metrics = _current(total_trades=0, closed_trades=0, open_trades=0, positions=0)
    view = build_backtest_view(_detail(metrics), size="evidence")
    assert view["count_basis"] == NO_TRADES_LINE
    assert NO_CLOSED_TRADE_LINE not in view["markdown"]


def test_an_older_run_without_the_split_reads_its_null_win_rate_the_same_way():
    # Era A (no stamp, no closed_trades): a null win rate over closed trades
    # with trades recorded has no other cause. Its era line stays first.
    metrics = {**_HEAD, "total_trades": 7, "win_rate": None}
    view = build_backtest_view(_detail(metrics), size="evidence")
    assert view["count_basis"] == f"{COUNT_BASIS['A']} {NO_CLOSED_TRADE_LINE}"
    measured = build_backtest_view(_detail({**metrics, "win_rate": 42.0}), size="evidence")
    assert measured["count_basis"] == COUNT_BASIS["A"]


def test_a_run_whose_counts_are_unrecorded_says_nothing():
    # Unknown era: no trade number is read, so there is nothing to explain.
    metrics = {**_HEAD, "total_trades": 9, "win_rate": None, "trade_model": "flat_to_flat_v9"}
    assert win_rate_note(view_metrics(metrics)) is None


# ── The strategy view ────────────────────────────────────────────────

_GRAPH = json.loads(
    (
        Path(__file__).resolve().parent / "fixtures/cards/hrp_plus_breakout_w10.graph.json"
    ).read_text()
)


@pytest.mark.parametrize("state", sorted(NO_EVIDENCE_LINES))
def test_a_strategy_without_evidence_says_which_kind(state):
    view = build_view(_GRAPH, {"name": "s", "backtest_state": state}, size_override="structure")
    assert view["evidence"] is None
    assert view["evidence_note"] == NO_EVIDENCE_LINES[state]
    assert NO_EVIDENCE_LINES[state] in view["markdown"]


def test_a_strategy_with_evidence_or_a_backtested_state_carries_no_note():
    meta = {
        "name": "s",
        "backtest_state": "backtested",
        "latest_backtest_metrics": _CLOSED,
        "latest_backtest_sequence": 1,
    }
    with_evidence = build_view(_GRAPH, meta, size_override="structure")
    assert with_evidence["evidence"] is not None
    assert "evidence_note" not in with_evidence
    bare = build_view(_GRAPH, {"name": "s", "backtest_state": "backtested"})
    assert "evidence_note" not in bare
    for line in NO_EVIDENCE_LINES.values():
        assert line not in with_evidence["markdown"]
        assert line not in bare["markdown"]
