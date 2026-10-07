"""A cross-strategy compare says what is the same, and its columns differ
(Q-1887, ChatGPT R4 #6).

ChatGPT compared two copies of "HYPE Long/Cash MACD Trend" whose only
difference was `bar_offset` 12h → none. The compare said "Different
strategies — structure and universe may differ." although its own spec diff
showed identical pipelines and universes, and both columns carried the same
name.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* `_cross_strategy_sentence` returns its fallback unconditionally — the
  same-pipeline test reds; the no-source control stays green;
* `_distinct_labels` returns `labels` unchanged — both label tests red.
"""

from __future__ import annotations

from unittest.mock import MagicMock


NAME = "HYPE Long/Cash MACD Trend"


def _source(offset: str | None) -> str:
    clock = (
        f'Globals(target_timeframe="1d", bar_offset="{offset}")'
        if offset
        else ('Globals(target_timeframe="1d")')
    )
    return (
        f"{clock}\n"
        'Universe(mode="manual", symbols=["HYPE"])\n'
        'Execution(rebalance="every_bar")\n'
        "Pipeline([\n"
        '    PriceDataLoader(timeframe="1d"),\n'
        "    ROC(period=8),\n"
        "])\n"
    )


def _detail(run_id: str, sid: str, seq: int, queued: str) -> dict:
    return {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": sid,
        "strategy_name": NAME,
        "sequence_number": seq,
        "commit_id": f"c_{sid}",
        "start_date": "2024-12-05",
        "end_date": "2026-09-23",
        "queued_at": queued,
        "engine": "native",
        "metrics": {"sharpe_ratio": 0.5, "total_trades": 29},
    }


def _compare(seq_a: int, seq_b: int, *, sources: bool = True) -> dict:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    details = {
        "btr_a": _detail("btr_a", "str_a", seq_a, "2026-09-23T09:00:00Z"),
        "btr_b": _detail("btr_b", "str_b", seq_b, "2026-09-23T10:00:00Z"),
    }
    srcs = {"str_a": _source("12h"), "str_b": _source(None)}
    client = MagicMock()

    def get(path, **_kw):
        parts = path.split("/")
        if path.startswith("/v1/backtests/") and len(parts) == 4:
            return details[parts[3]]
        if path.endswith("/source"):
            return {"source": srcs[parts[3]]} if sources else {}
        return {}

    client.get.side_effect = get
    ctx = ToolContext(api_client=client, is_tty=False)
    return (
        OUTCOMES["keel_backtest_compare"]
        .handler({"backtest_ids": ["btr_a", "btr_b"]}, ctx)
        .to_envelope()
    )


def test_the_warning_says_what_the_spec_diff_found() -> None:
    env = _compare(4, 1)
    warnings = env["comparability_warnings"]
    assert "Different strategies with the same pipeline and universe." in warnings, warnings
    assert not any("may differ" in w for w in warnings)
    clock = next(w for w in warnings if w.startswith("Clocks differ"))
    assert "closes its daily bar at 12:00 UTC" in clock and "at 00:00 UTC" in clock, clock


def test_without_sources_the_hedge_stands() -> None:
    """CONTROL: no diff, no claim — the old sentence, never a guess."""
    warnings = _compare(4, 1, sources=False)["comparability_warnings"]
    assert "Different strategies — structure and universe may differ." in warnings


def test_same_named_columns_gain_their_version() -> None:
    labels = [run["label"] for run in _compare(4, 1)["view"]["runs"]]
    assert labels == [f"{NAME} · v4", f"{NAME} · v1"]


def test_same_named_same_version_columns_gain_their_order() -> None:
    labels = [run["label"] for run in _compare(1, 1)["view"]["runs"]]
    assert labels == [f"{NAME} · v1 (earlier)", f"{NAME} · v1 (later)"]
