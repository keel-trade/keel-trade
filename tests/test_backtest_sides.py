"""Which way a run's positions went (R-003, OB-levels P3c).

The worker stores `positions_long` / `positions_short` (summing to
`positions`), `flips` and `pnl_long` / `pnl_short` on every run since R-003.
On the agent surface they are:

* `sides` — a first-class field on `keel_backtest_run` /
  `keel_backtest_summarize` (the counts, the P&Ls and `line`), and a `sides:`
  text-block line;
* `view.sides_line` for the card, and the keys in `view.metrics` and
  `summary_metrics`;
* the SAME sentence the app's run page shows (keel-app `SideLine.tsx`).

A run recorded before the counts carries none of it: absent, never 0.

SEED (run 2026-10-28): make `sides_block` swap the two counts (read
`positions_short` as long) → the long-only, short-only and summarize arms
red; the absent-counts arms, the unknown-era arm and the money-format arm
stay green (the control). Reverted by reversing the edit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_summarize as _bt_sum_mod  # noqa: F401 (registers the tool)
from keel.tools.outcomes._backtest_view import build_backtest_view, sides_block
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


#: A short-only run: 12 positions, all short, no flips.
SHORT_ONLY = {
    "trade_model": "reducing_order",
    "total_trades": 12,
    "positions": 12,
    "positions_long": 0,
    "positions_short": 12,
    "flips": 0,
    "pnl_long": 0.0,
    "pnl_short": 412.1,
}
SHORT_LINE = "12 positions: 0 long, 12 short · 0 flips · long P&L $0.00, short P&L +$412.10"

#: A long-only run with one position: the singulars.
LONG_ONE = {
    "trade_model": "reducing_order",
    "positions": 1,
    "positions_long": 1,
    "positions_short": 0,
    "flips": 1,
    "pnl_long": -1234.5,
    "pnl_short": 0.0,
}
LONG_LINE = "1 position: 1 long, 0 short · 1 flip · long P&L −$1,234.50, short P&L $0.00"


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


def _detail(metrics):
    return {
        "id": "bt_x",
        "status": "COMPLETED",
        "strategy_id": "str_x",
        "strategy_name": "X",
        "start_date": "2026-09-01",
        "end_date": "2026-09-26",
        "metrics": metrics,
    }


def _summarize(ctx, metrics):
    def fake_get(path, **kw):
        if path == "/v1/backtests/bt_x":
            return _detail(metrics)
        if path in ("/v1/backtests/bt_x/results", "/v1/backtests/bt_x/curve"):
            return {}
        raise KeelError(f"not in this fixture: {path}")

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        return (
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_x"}, ctx).to_envelope()
        )


class TestSidesBlock:
    def test_short_only(self):
        block = sides_block(SHORT_ONLY)
        assert block == {
            "positions_long": 0,
            "positions_short": 12,
            "flips": 0,
            "pnl_long": 0.0,
            "pnl_short": 412.1,
            "line": SHORT_LINE,
        }

    def test_long_only_singulars_and_a_loss(self):
        assert sides_block(LONG_ONE)["line"] == LONG_LINE

    def test_flipping_run(self):
        block = sides_block(
            {
                "trade_model": "reducing_order",
                "positions_long": 2,
                "positions_short": 1,
                "flips": 2,
                "pnl_long": 110.0,
                "pnl_short": 40.0,
            }
        )
        assert block["line"] == (
            "3 positions: 2 long, 1 short · 2 flips · long P&L +$110.00, short P&L +$40.00"
        )

    def test_a_run_before_the_counts_has_no_block(self):
        # Legacy payloads: absent is "not recorded", never a 0 line.
        assert sides_block({"trade_model": "reducing_order", "positions": 12}) is None
        assert sides_block({"total_trades": 12}) is None
        assert sides_block(None) is None

    def test_counts_without_pnl_say_no_pnl(self):
        metrics = {k: v for k, v in SHORT_ONLY.items() if not k.startswith("pnl_")}
        block = sides_block(metrics)
        assert block["line"] == "12 positions: 0 long, 12 short · 0 flips"
        assert "pnl_long" not in block

    def test_an_unknown_era_carries_none(self):
        assert sides_block({**SHORT_ONLY, "trade_model": "something_new"}) is None

    def test_an_amount_that_rounds_to_zero_is_unsigned(self):
        line = sides_block({**SHORT_ONLY, "pnl_long": -0.004})["line"]
        assert "long P&L $0.00," in line


class TestSidesOnTheSurface:
    def test_view_carries_the_line_and_the_metrics(self):
        view = build_backtest_view(_detail(SHORT_ONLY), size="evidence")
        assert view["sides_line"] == SHORT_LINE
        for key in ("positions_long", "positions_short", "flips", "pnl_long", "pnl_short"):
            assert view["metrics"][key] == SHORT_ONLY[key], key

    def test_view_of_a_legacy_run_has_no_line(self):
        view = build_backtest_view(_detail({"trade_model": "reducing_order", "positions": 3}))
        assert "sides_line" not in view
        assert "positions_long" not in view["metrics"]

    def test_summarize_surfaces_sides(self, ctx):
        env = _summarize(ctx, SHORT_ONLY)
        assert env["sides"]["line"] == SHORT_LINE
        assert env["sides"]["positions_short"] == 12
        assert env["summary_metrics"]["positions_short"] == 12
        assert env["summary_metrics"]["pnl_short"] == 412.1
        lines = [line for line in operational_lines(env) if line.startswith("sides: ")]
        assert lines == [f"sides: {SHORT_LINE}"]

    def test_summarize_of_a_legacy_run_has_no_sides(self, ctx):
        env = _summarize(ctx, {"trade_model": "reducing_order", "positions": 3})
        assert "sides" not in env
        assert not [line for line in operational_lines(env) if line.startswith("sides: ")]
        # Non-vacuity: the run was read and summarised.
        assert env["summary_metrics"]["positions"] == 3

    def test_the_card_draws_the_line(self):
        from pathlib import Path

        from keel.tools.outcomes import backtest_summarize as _mod

        card = (
            Path(_mod.__file__).resolve().parents[2] / "widgets/assets/card-backtest.js"
        ).read_text()
        assert "v.sides_line" in card
