"""The capital guide on the agent surface (design doc 14 §5/§11, the SDK half).

The backtest worker stamps ``metrics.capital_guide`` (computed only by
``backtest_framework.capital_guide``): Suggested / Closest / Minimum in USD.
``capital_guide_block`` copies it with one plain sentence, the summarize
envelope carries it as ``capital_guide`` and the MCP text as ``capital:``,
and the realism line stops quoting the retired order-count rungs whenever
the guide is there (founder 2026-11-03: the measure is position drift).

SEEDS (run 2026-11-03, reversed by reversing the exact edit):
* ``realism_line`` ignores the guide (``has_guide = False``) → 2 red (the
  rungs-retired arm, the summarize arm); the no-guide control (old rungs kept)
  and the listed arm stay green.
* the envelope drops the block (``guide = None``) → the summarize and schema
  arms red; the block arms stay green.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._backtest_view import (
    capital_guide_block,
    realism_from_metrics,
    realism_line,
)
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import operational_lines


_bootstrap()

GUIDE = {
    "version": 1,
    "basis": "backtest",
    "n_held": 2,
    "typical_weight": 0.5,
    "universe_size": 2,
    "suggested_usd": 200,
    "closest_usd": 400,
    "minimum_usd": 100,
    "steps": {"minimum": 0.2, "suggested": 0.1, "closest": 0.05},
}
LINE = "Suggested $200 to trade it as backtested; $400 tracks it most closely; it runs from $100"
REALISM = {
    "n_orders": 138,
    "n_below_live_minimum": 39,
    "share_below_live_minimum": 39 / 138,
    "avg_order_notional": 20.94,
    "live_minimum_notional": 10.0,
    "truncated": False,
    "min_capital_for_share": {"25_pct": 137.42, "5_pct": 430.46, "0_pct": 854.14},
}
RUNGS = "capital for at most 5% of orders under the minimum: $431; at most 25%: $138"


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


def test_the_block_copies_the_amounts_and_says_them_plainly():
    block = capital_guide_block({"capital_guide": GUIDE})
    assert block == {
        "basis": "backtest",
        "suggested_usd": 200,
        "closest_usd": 400,
        "minimum_usd": 100,
        "n_held": 2,
        "typical_weight": 0.5,
        "universe_size": 2,
        "line": LINE,
    }


def test_the_assets_basis_says_why():
    guide = {**GUIDE, "basis": "assets", "typical_weight": None, "universe_size": 8}
    guide.update(suggested_usd=400, closest_usd=1000, minimum_usd=200)
    block = capital_guide_block({"capital_guide": guide})
    assert block["typical_weight"] is None
    assert block["line"] == (
        "Suggested $400 to trade it as backtested; $1,000 tracks it most closely; "
        "it runs from $200 (sized by its 8 assets: this backtest held no positions)"
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {**GUIDE, "basis": "other"},
        {**GUIDE, "suggested_usd": None},
        {**GUIDE, "closest_usd": 0},
        {**GUIDE, "minimum_usd": True},
    ],
)
def test_no_guide_or_half_a_guide_is_no_block(raw):
    assert capital_guide_block({"capital_guide": raw}) is None


def test_the_retired_rungs_give_way_to_the_guide():
    realism = realism_from_metrics({"realism": REALISM})
    block = capital_guide_block({"capital_guide": GUIDE})
    line = realism_line(realism, block)
    assert RUNGS not in line and "$431" not in line
    assert line.startswith("39 of 138 orders (28%) were below the $10 live minimum")


def test_without_a_guide_the_old_rungs_stay():
    """CONTROL: a run from before the stamp reads exactly as before."""
    realism = realism_from_metrics({"realism": REALISM})
    assert realism_line(realism).endswith(f" — {RUNGS}")
    assert realism["min_capital_for_share"] == {"25_pct": 137.42, "5_pct": 430.46}


def test_summarize_carries_the_block_and_the_capital_line():
    env = _summarize({"sharpe_ratio": 1.0, "realism": REALISM, "capital_guide": GUIDE})
    assert env["capital_guide"]["suggested_usd"] == 200
    lines = operational_lines(env)
    (capital,) = [x for x in lines if x.startswith("capital: ")]
    assert capital == f"capital: {LINE}"
    (realism,) = [x for x in lines if x.startswith("realism: ")]
    assert "$431" not in realism
    # the old fields stay readable on the block
    assert env["realism"]["min_capital_for_share"] == {"25_pct": 137.42, "5_pct": 430.46}


def test_a_run_without_the_stamp_has_no_capital_line():
    env = _summarize({"sharpe_ratio": 1.0, "realism": REALISM})
    assert "capital_guide" not in env
    assert not [x for x in operational_lines(env) if x.startswith("capital: ")]


def test_the_schema_declares_the_block_and_validates_a_real_envelope():
    import jsonschema
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS

    schema = OUTPUT_SCHEMAS["keel_backtest_summarize"]
    props = schema["properties"]["capital_guide"]["properties"]
    assert {"suggested_usd", "closest_usd", "minimum_usd", "line"} <= set(props)
    env = _summarize({"sharpe_ratio": 1.0, "capital_guide": GUIDE})
    assert "capital_guide" in env  # non-vacuity: the block is there to validate
    jsonschema.validate(env, schema)


def test_the_listed_profile_carries_no_capital_guide(monkeypatch):
    """The listed connector is for research and backtests: no funding
    amounts, and its realism line keeps the order-count rungs it always had."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    env = _summarize({"sharpe_ratio": 1.0, "realism": REALISM, "capital_guide": GUIDE})
    assert "capital_guide" not in env
    lines = operational_lines(env)
    assert not [x for x in lines if x.startswith("capital: ")]
    (realism,) = [x for x in lines if x.startswith("realism: ")]
    assert realism.endswith(RUNGS)
