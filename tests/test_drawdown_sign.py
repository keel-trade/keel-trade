"""One drawdown sign on every model-visible metric block (Q-1805).

A live ChatGPT session read `summary_metrics.max_drawdown: 60.88` beside a
card and a text block that both said `−60.9%`. The worker writes
`max_drawdown` as an unsigned magnitude; `view.metrics` re-signed it, but
`summary_metrics` and compare's `performance` rows copied it through. The
convention is now the view's everywhere a model reads a curated block: a
percent, negative or zero, named `max_drawdown_pct`. `metrics_raw*` stay
the worker's sealed metrics verbatim.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._backtest_view import signed_drawdown
from keel.tools.outcomes._base import ToolContext


def _detail(index: int = 0, **metrics) -> dict:
    return {
        "id": f"btr_dd{index}",
        "status": "COMPLETED",
        "strategy_id": "str_dd",
        "strategy_name": "Drawdown sign",
        "sequence_number": index + 1,
        "commit_id": f"c_dd{index}",
        "engine": "native",
        "start_date": "2024-08-15",
        "end_date": "2026-09-22",
        "completed_at": "2026-09-22T11:04:00Z",
        # The worker's spelling and sign, as staging returns it (60.88).
        "metrics": {
            "sharpe_ratio": 0.8,
            "total_return": 96.8,
            "max_drawdown": 60.88 - index,
            "total_trades": 1448,
            **metrics,
        },
    }


class _Client:
    def __init__(self, details: list[dict]):
        self.details = {d["id"]: d for d in details}

    def get(self, path, **kwargs):
        for run_id, detail in self.details.items():
            if path == f"/v1/backtests/{run_id}":
                return detail
        # Curve, results, source, commit message, strategy-work: absent is
        # a legal answer for each, and none of them carries a metric.
        return {}

    def post(self, path, **kwargs):
        detail = next(iter(self.details.values()))
        return {**detail, "status": "queued"}


def _call(tool: str, args: dict, details: list[dict]) -> dict:
    _bootstrap()
    ctx = ToolContext(api_client=_Client(details), is_tty=False, app_url="https://app.usekeel.io")
    return OUTCOMES[tool].handler(args, ctx).to_envelope()


def _summarize(**metrics) -> dict:
    return _call("keel_backtest_summarize", {"backtest_id": "btr_dd0"}, [_detail(0, **metrics)])


def test_summarize_states_the_drawdown_once_with_the_views_sign():
    env = _summarize()
    summary = env["summary_metrics"]
    assert summary["max_drawdown_pct"] == pytest.approx(-60.88)
    assert "max_drawdown" not in summary
    # The same number, the same sign, as the view the card and text render.
    assert summary["max_drawdown_pct"] == env["view"]["metrics"]["max_drawdown_pct"]
    # The worker's sealed metrics stay verbatim — that block is the raw one.
    assert env["metrics_raw"]["max_drawdown"] == pytest.approx(60.88)
    # Non-vacuity the seed cannot move: the other headline metrics rode along.
    assert summary["sharpe_ratio"] == pytest.approx(0.8)


def test_run_wait_states_the_drawdown_once_with_the_views_sign():
    env = _call(
        "keel_backtest_run",
        {"strategy_id": "str_dd", "commit_id": "c_dd0", "wait": True, "skip_readiness": True},
        [_detail(0)],
    )
    assert env["summary_metrics"]["max_drawdown_pct"] == pytest.approx(-60.88)
    assert "max_drawdown" not in env["summary_metrics"]
    assert env["summary_metrics"]["max_drawdown_pct"] == env["view"]["metrics"]["max_drawdown_pct"]


def test_compare_rows_use_the_signed_name_and_their_delta_reads_the_right_way():
    env = _call(
        "keel_backtest_compare",
        {"backtest_ids": ["btr_dd0", "btr_dd1"]},
        [_detail(0), _detail(1)],
    )
    row = env["performance"]["max_drawdown_pct"]
    assert row["a"] == pytest.approx(-60.88) and row["b"] == pytest.approx(-59.88)
    # b's drawdown is one point SHALLOWER: a positive delta is an improvement.
    assert row["delta"] == pytest.approx(1.0)
    assert "max_drawdown" not in env["performance"]
    assert env["metrics_raw_a"]["max_drawdown"] == pytest.approx(60.88)


def test_compare_by_run_columns_use_the_signed_name():
    details = [_detail(i) for i in range(3)]
    env = _call("keel_backtest_compare", {"backtest_ids": [d["id"] for d in details]}, details)
    column = env["performance_by_run"]["max_drawdown_pct"]
    assert column == pytest.approx([-60.88, -59.88, -58.88])
    assert "max_drawdown" not in env["performance_by_run"]


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        ({"max_drawdown": 43.4}, -43.4),  # the worker's magnitude
        ({"max_drawdown_pct": -43.4}, -43.4),  # already signed: unchanged
        ({"max_drawdown_pct": 43.4}, -43.4),  # a positive _pct is re-signed
        ({"max_drawdown": 43.4, "max_drawdown_pct": -12.0}, -12.0),  # _pct wins
        ({"max_drawdown": 0.0}, 0.0),
    ],
)
def test_signed_drawdown_states_one_key_at_or_below_zero(metrics, expected):
    out = signed_drawdown({"sharpe": 1.0, **metrics})
    assert out["max_drawdown_pct"] == pytest.approx(expected)
    assert "max_drawdown" not in out
    assert out["sharpe"] == 1.0


def test_signed_drawdown_leaves_a_block_without_a_drawdown_alone():
    assert signed_drawdown({"sharpe": 1.0}) == {"sharpe": 1.0}
