"""The compare TEXT carries the cost profile and realism (Q-1876, R4 probe).

`keel_backtest_compare`'s description promises "each run's cost profile
(resizes, turnover, fees)"; its text block — all the
model reads on claude.ai — drew only the four headline rows, and never the
small-order share the R4 comparison turned on. The diff row also printed
`top_n 10` beside a universe chip that says "Top 10 by volume".

Driven through the REAL handler (details → view → markdown).

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* `_comparison_markdown` loops `_TABLE_ROWS` only (drop `+ _COST_TABLE_ROWS`)
  — `test_the_table_arm_carries_costs_and_realism` reds; the line arm, the
  no-realism control and the diff-label test stay green;
* `backtest_compare._handler` stops copying realism (delete
  `run["realism"] = realism`) — the realism-row assertions red on both arms;
  the control (a set with no realism has no row) stays green;
* `_declaration_part` without its universe branch — the `Top 10 by volume`
  test reds, its execution control stays green.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _detail(run_id: str, seq: int, *, share: float | None, avg: float, trips: int) -> dict:
    metrics = {
        "sharpe_ratio": 1.2 + seq / 10,
        "total_return_pct": 30.0 + seq,
        "max_drawdown": 15.0,
        "win_rate_pct": 45.0,
        "total_trades": trips,
        "turnover": 11.4 + seq,
        # A current run (trade-metrics spec 01 §3): turnover is recorded.
        "trade_model": "reducing_order",
        "total_fees_paid": 40.0 + seq,
    }
    if share is not None:
        metrics["realism"] = {
            "n_orders": 1000,
            "n_below_live_minimum": int(share * 1000),
            "share_below_live_minimum": share,
            "avg_order_notional": avg,
            "live_minimum_notional": 10.0,
            "truncated": False,
        }
    return {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": "str_a",
        "strategy_name": "S",
        "sequence_number": seq,
        "commit_id": f"c{seq}",
        "start_date": "2024-07-27",
        "end_date": "2026-09-23",
        "metrics": metrics,
    }


def _compare(n: int, *, realism: bool = True) -> dict:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    shares = [0.12, 0.05, 0.09, 0.04, 0.10]
    details = {
        f"btr_{i}": _detail(
            f"btr_{i}",
            i + 1,
            share=shares[i] if realism else None,
            avg=100.0 + 20 * i,
            trips=500 + i,
        )
        for i in range(n)
    }
    client = MagicMock()

    def get(path, **_kw):
        if path.startswith("/v1/backtests/") and path.count("/") == 3:
            return details[path.rsplit("/", 1)[1]]
        if path == "/v1/strategies/str_a":
            return {"current_sequence": n}
        return {}

    client.get.side_effect = get
    ctx = ToolContext(api_client=client, is_tty=False)
    return (
        OUTCOMES["keel_backtest_compare"]
        .handler({"backtest_ids": list(details)}, ctx)
        .to_envelope()
    )


def test_the_table_arm_carries_costs_and_realism() -> None:
    env = _compare(2)
    md = env["view"]["markdown"]
    # Non-vacuity: the table arm, with its four headline rows.
    assert "| --- | ---: | ---: |" in md and "| Sharpe |" in md
    assert "| Trades | 500 | 501 |" in md
    assert "| Turnover (× capital) | 12.4x | 13.4x |" in md
    assert "| Fees paid | $41 | $42 |" in md
    assert "| Orders under $10 | 12% · avg $100 | 5% · avg $120 |" in md
    # Copied into structuredContent too (view.runs is a structured row).
    assert env["view"]["runs"][1]["realism"]["share"] == 0.05


def test_the_line_arm_carries_costs_and_realism() -> None:
    env = _compare(5)
    md = env["view"]["markdown"]
    assert "| --- |" not in md  # beyond four runs: one line per run
    lines = [line for line in md.splitlines() if " — Return " in line]
    assert len(lines) == 5
    assert "Trades 504" in lines[4] and "Fees paid $45" in lines[4]
    assert "Orders under $10 10% · avg $180" in lines[4]


def test_a_set_without_realism_has_no_realism_row() -> None:
    """CONTROL: nothing recorded ⇒ no row (never a guessed minimum)."""
    md = _compare(2, realism=False)["view"]["markdown"]
    assert "| Sharpe |" in md  # non-vacuity: the table was drawn
    assert "Orders under" not in md


@pytest.mark.parametrize(
    ("section", "key", "pair", "mode", "expected"),
    [
        ("universe", "top_n", {"a": 30, "b": 10}, "top_volume", "Top 10 by volume"),
        ("universe", "top_n", {"a": 30, "b": 10}, "category", "Top 10"),
        # CONTROL: an execution key keeps the vetted vocabulary.
        ("execution", "buffer_threshold", {"a": 0.2, "b": 0.05}, None, "buffer 0.05"),
    ],
)
def test_the_diff_row_speaks_the_chips_words(section, key, pair, mode, expected) -> None:
    from keel.tools.outcomes.backtest_compare import _run_diff

    spec_diff = {"declarations": {section: {key: pair}}}
    assert _run_diff(spec_diff, same_window=True, universe_mode=mode) == expected
