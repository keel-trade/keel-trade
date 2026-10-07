"""keel_backtest_summarize — the notes block (Q-1583 step 4)."""

from __future__ import annotations


class TestNotesBlock:
    """Q-1583 step 4: the run's non-result label and per-asset notes are lifted
    to a first-class `notes` block, copied verbatim from the worker's metrics."""

    def test_non_result_and_asset_notes_are_lifted_verbatim(self):
        from keel.tools.outcomes.backtest_summarize import _notes_block

        metrics = {
            "total_return": 0.0,
            "non_result": {"kind": "no_position_opened", "message": "No position was opened…"},
            "warnings": [
                {
                    "code": "SYMBOL_ABSENT",
                    "message": "IP: no market data in this window — not traded",
                },
                {
                    "code": "DATA_COPY_LAG",
                    "message": "VINE: Keel's copy of this series is shorter…",
                },
                "PRICE_MARKS_INJECTED: a plain-string legacy note is not an asset note",
            ],
        }
        notes = _notes_block(metrics)
        assert notes["non_result"] == metrics["non_result"]
        assert [n["code"] for n in notes["assets"]] == ["SYMBOL_ABSENT", "DATA_COPY_LAG"]
        assert notes["assets"][0] is metrics["warnings"][0], "verbatim, not re-derived"

    def test_a_run_with_nothing_to_say_has_no_notes_block(self):
        """CONTROL + non-vacuity: numbers alone ⇒ None (readers test presence)."""
        from keel.tools.outcomes.backtest_summarize import _notes_block

        assert _notes_block({"sharpe_ratio": 1.2, "warnings": []}) is None
        assert _notes_block({"non_result": {"kind": "x"}}) is None, "a label needs its message"
