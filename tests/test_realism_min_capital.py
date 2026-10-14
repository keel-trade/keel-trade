"""The capital a run needs, on the agent surface (Q-2542, the MCP/CLI half).

The backtest worker stamps `realism.min_capital_for_share` (`25_pct` /
`5_pct` / `0_pct`, USD) on every run since platform v1.185: the capital at
which at most that share of the run's orders would sit under the $10 live
minimum, from the run's own order notionals. `realism_from_metrics` copies
the TWO tolerance rungs (never `0_pct` — "$4.85M" on a $10k fork reads as
the capital needed) and `realism_line` appends them as tolerances, so an
agent answers "how much capital do I need?" from the first run instead of
re-running at other capitals (subject 03 spent 2 of 6 weekly units on that).
Same block and line on the CLI and MCP: one `realism_line` behind both.

SEED (run 2026-10-08): drop the clause (`capital = None` in `realism_line`)
→ 2 red: the line arm and the summarize-surfaces arm; the block arm, the
control (a run without the field is byte-identical) and the 0 %-absent
arm stay green. Reverted by reversing the edit, byte-exact.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._backtest_view import (
    MIN_CAPITAL_RUNGS,
    min_capital_rungs,
    realism_from_metrics,
    realism_line,
)
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


_bootstrap()

# banaitis's Stochastic run (btr_01m4awzh…, $100, 2026-10-07): the worker's
# answer from its 138 real order notionals — three rungs, the 0 % one included.
CAPITAL = {"25_pct": 137.42, "5_pct": 430.46, "0_pct": 854.14}
REALISM = {
    "n_orders": 138,
    "n_below_live_minimum": 39,
    "share_below_live_minimum": 39 / 138,
    "avg_order_notional": 20.94,
    "live_minimum_notional": 10.0,
    "truncated": False,
}
BEFORE_LINE = (
    "39 of 138 orders (28%) were below the $10 live minimum; this backtest fills them "
    "(they are in its returns), live execution would skip them — avg order $21"
)
CLAUSE = "capital for at most 5% of orders under the minimum: $431; at most 25%: $138"


def _summarize(metrics: dict) -> dict:
    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_x":
            return {
                "id": "bt_x",
                "status": "COMPLETED",
                "strategy_id": "str_x",
                "strategy_name": "X",
                "start_date": "2026-07-15",
                "end_date": "2026-10-11",
                "metrics": metrics,
            }
        if path.startswith("/v1/backtests/bt_x/"):
            return {}
        raise KeelError(f"not in this fixture: {path}")

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        return (
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "bt_x"}, ctx).to_envelope()
        )


def test_the_two_tolerance_rungs_ride_the_block_never_the_zero_rung():
    block = realism_from_metrics({"realism": {**REALISM, "min_capital_for_share": CAPITAL}})
    assert block["min_capital_for_share"] == {"25_pct": 137.42, "5_pct": 430.46}
    assert MIN_CAPITAL_RUNGS == ("25_pct", "5_pct")
    # Non-vacuity: the fixture carries the 0 % rung and it is ABSENT from
    # every byte the agent reads.
    assert "0_pct" in CAPITAL and "854" not in json.dumps(block)
    assert "854" not in realism_line(block)


def test_the_line_names_both_rungs_as_tolerances_rounded_up_to_the_dollar():
    block = realism_from_metrics({"realism": {**REALISM, "min_capital_for_share": CAPITAL}})
    line = realism_line(block)
    assert line == f"{BEFORE_LINE} — {CLAUSE}"
    # $430.46 → $431 and $137.42 → $138: "from $X" stays true.
    assert "$431" in line and "$138" in line and "$430" not in line and "$137" not in line


def test_summarize_surfaces_it_on_the_block_and_the_text_line():
    env = _summarize(
        {"sharpe_ratio": 1.0, "realism": {**REALISM, "min_capital_for_share": CAPITAL}}
    )
    assert env["realism"]["min_capital_for_share"] == {"25_pct": 137.42, "5_pct": 430.46}
    (line,) = [x for x in operational_lines(env) if x.startswith("realism: ")]
    assert line == f"realism: {BEFORE_LINE} — {CLAUSE}"


def test_a_run_from_before_the_field_is_unchanged():
    """CONTROL: no key, no clause — the pre-Q-2542 block and line, exactly."""
    block = realism_from_metrics({"realism": REALISM})
    assert block == {
        "n_orders": 138,
        "n_below": 39,
        "share": 39 / 138,
        "avg_order_notional": 20.94,
        "min_notional": 10.0,
        "truncated": False,
    }
    assert realism_line(block) == BEFORE_LINE
    env = _summarize({"sharpe_ratio": 1.0, "realism": REALISM})
    assert "min_capital_for_share" not in env["realism"]
    (line,) = [x for x in operational_lines(env) if x.startswith("realism: ")]
    assert line == f"realism: {BEFORE_LINE}"


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        {},
        {"0_pct": 854.14},
        {"25_pct": 137.42},
        {"25_pct": None, "5_pct": 430.46},
        {"25_pct": 0, "5_pct": 1},
    ],
)
def test_half_an_answer_is_no_answer(stamp):
    """Both rungs or neither — never one tolerance without the other."""
    assert min_capital_rungs(stamp) is None
    block = realism_from_metrics({"realism": {**REALISM, "min_capital_for_share": stamp}})
    assert "min_capital_for_share" not in block
    assert realism_line(block) == BEFORE_LINE


def test_no_sub_minimum_orders_means_no_line_even_with_the_rungs():
    """The line exists to say which orders live execution would skip; a run
    with none has nothing to fix, so the rungs ride the block only."""
    block = realism_from_metrics(
        {"realism": {**REALISM, "n_below_live_minimum": 0, "min_capital_for_share": CAPITAL}}
    )
    assert block["min_capital_for_share"] == {"25_pct": 137.42, "5_pct": 430.46}
    assert realism_line(block) is None


def test_the_schema_declares_the_two_rungs_and_validates_a_real_envelope():
    import jsonschema
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS

    schema = OUTPUT_SCHEMAS["keel_backtest_summarize"]["properties"]["realism"]
    rungs = schema["properties"]["min_capital_for_share"]["properties"]
    assert set(rungs) == {"25_pct", "5_pct"}
    env = _summarize(
        {"sharpe_ratio": 1.0, "realism": {**REALISM, "min_capital_for_share": CAPITAL}}
    )
    jsonschema.validate(env, OUTPUT_SCHEMAS["keel_backtest_summarize"])
