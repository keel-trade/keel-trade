"""The per-run compare hint is short and window-scoped (Q-1878, R4 probe).

The R4 agent: "Every backtest result repeated a growing
keel_backtest_compare(...) suggestion listing all prior run IDs, with a
'their end dates differ' warning even when that was intentional for the
half-period runs." The hint now names at most the latest four runs of THIS
run's window; a deliberate sub-window run is not offered as a variant.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* `_same_window` returns True unconditionally — the two split tests red;
  the cap test and the unknown-window control stay green;
* `MAX_HINT_IDS = 8` — the cap test reds; the split tests stay green.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from keel.tools.outcomes._backtest_view import compare_next


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def get(self, path, **_kw):
        assert path == "/v1/backtests"
        return {"data": self.rows, "pagination": {}}


FULL = ("2024-07-27", "2026-09-23")
FIRST_HALF = ("2024-07-27", "2025-08-25")
SECOND_HALF = ("2025-08-25", "2026-09-23")


def _row(run_id: str, minutes_ago: int, window) -> dict:
    return {
        "id": run_id,
        "status": "COMPLETED",
        "completed_at": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
        "start_date": window[0] if window else None,
        "end_date": window[1] if window else None,
    }


def _ids(line: str) -> list[str]:
    inside = line.split("backtest_ids=[", 1)[1].split("]", 1)[0]
    return [part.strip().strip('"') for part in inside.split(",")]


def test_the_hint_names_at_most_the_latest_four() -> None:
    rows = [_row(f"btr_{i}", 50 - i, FULL) for i in range(7)]
    line = compare_next(
        _Client(rows),
        strategy_id="str_a",
        backtest_id="btr_6",
        start_date=FULL[0],
        end_date=FULL[1],
    )
    assert line.startswith("7 completed runs on this strategy in the last hour; the latest 4: "), (
        line
    )
    assert _ids(line) == ["btr_3", "btr_4", "btr_5", "btr_6"]


def test_a_split_run_is_not_offered_as_a_variant_of_a_full_run() -> None:
    rows = [
        _row("btr_full_a", 40, FULL),
        _row("btr_h1", 30, FIRST_HALF),
        _row("btr_h2", 20, SECOND_HALF),
        _row("btr_full_b", 5, FULL),
    ]
    line = compare_next(
        _Client(rows),
        strategy_id="str_a",
        backtest_id="btr_full_b",
        start_date=FULL[0],
        end_date=FULL[1],
    )
    assert _ids(line) == ["btr_full_a", "btr_full_b"]
    assert line.startswith("2 completed runs over this run's window in the last hour: ")
    assert "end dates differ" not in line


def test_a_split_run_is_compared_with_its_own_window_only() -> None:
    rows = [
        _row("btr_full", 40, FULL),
        _row("btr_h1", 30, FIRST_HALF),
        _row("btr_h2_v2", 20, SECOND_HALF),
        _row("btr_h2", 5, SECOND_HALF),
    ]
    line = compare_next(
        _Client(rows),
        strategy_id="str_a",
        backtest_id="btr_h2",
        start_date=SECOND_HALF[0],
        end_date=SECOND_HALF[1],
    )
    assert _ids(line) == ["btr_h2_v2", "btr_h2"]
    assert "end dates differ" not in line


def test_runs_without_windows_are_not_separated() -> None:
    """CONTROL: an unknown window is never a guessed difference."""
    rows = [_row("btr_x", 30, None), _row("btr_y", 5, None)]
    line = compare_next(_Client(rows), strategy_id="str_a", backtest_id="btr_y")
    assert _ids(line) == ["btr_x", "btr_y"]
    assert line.startswith("2 completed runs on this strategy in the last hour: ")
