"""`keel_backtest_positions` (Q-1893) — summary first, one call, bounded.

The keel-api route owns the grouping and the bound
(`services/keel-api/tests/test_backtest_trades.py` pins a 2,400-trip run's
summary under budget with a seeded leak). These tests pin the SDK half: ONE
keel-api call per read, the default envelope carries no trip list and stays
small, scopes/cursor/sort reach the API verbatim, carry is signed through the
one reader, and `compare_to` pairs by nearest entry.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import ValidationError
from keel.tools.outcomes import backtest_positions as mod
from keel.tools.outcomes._base import ToolContext


def _trip(asset, day, pnl, side="long", hour=0):
    return {
        "asset": asset,
        "side": side,
        "entry_time": f"2025-01-{day:02d}T{hour:02d}:00:00+00:00",
        "exit_time": f"2025-01-{day + 1:02d}T{hour:02d}:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl,
        "notional": 1000.0,
        "return_pct": pnl / 10,
        "pnl": pnl,
        "fees": 0.5,
        "legs": 1,
    }


def _summary_block(n_assets=40, per_asset=60):
    """The keel-api unscoped shape for a 2,400-trip run (no trip list)."""
    by_asset = [
        {"asset": f"A{i:02d}", "round_trips": per_asset, "net_pnl": 100.0 - i, "win_rate_pct": 50.0}
        for i in range(10)
    ]
    return {
        "job_id": "btr_x",
        "source": "exit_legs",
        "exit_legs": n_assets * per_asset,
        "listed_round_trips": n_assets * per_asset,
        "scope": None,
        "summary": {"round_trips": n_assets * per_asset, "net_pnl": 1234.5, "win_rate_pct": 51.2},
        "by_asset": by_asset,
        "more_assets": {"assets": n_assets - 10, "round_trips": 1800, "net_pnl": 300.0},
        "best": [_trip("A01", d, 50.0 - d) for d in range(1, 6)],
        "worst": [_trip("A02", d, -50.0 + d) for d in range(1, 6)],
        "run_trades": None,
        "run_positions": 2400,
        "trade_model": "position_round_trip",
        "funding_included": True,
        "funding": {"cumulative_funding": 12.5},
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, **params):
        self.calls.append((path, params))
        resp = self.responses[path]
        return resp(params) if callable(resp) else resp


def _ctx(client):
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


def test_default_call_is_one_read_and_a_small_summary_with_a_narrowing_hint():
    client = FakeClient({"/v1/backtests/btr_x/trades": _summary_block()})
    env = mod._handler({"backtest_id": "btr_x"}, _ctx(client)).to_envelope()
    # ONE keel-api call, no scope sent.
    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == "/v1/backtests/btr_x/trades"
    assert not {"asset", "start", "end", "cursor"} & set(params)
    # Non-vacuity: the run behind the summary is large.
    assert env["position_count"] == 2400
    assert "positions" not in env and "trips" not in env
    assert len(env["best"]) == 5 and len(env["worst"]) == 5
    assert "asset='A00'" in env["narrow"]
    # The run's carry, signed through the one reader: paid 12.5 → −12.5.
    assert env["carry"] == -12.5
    # The era stamp is never relayed (trade-metrics spec 01 §6.3), and a
    # recorded count carries no "not recorded" line.
    assert "position_model" not in env and "trade_model" not in env
    assert "position_count_note" not in env
    # The envelope speaks "positions" (founder rename, Q-1893): the API's
    # `round_trips` counts are renamed at every depth, no old key survives.
    assert env["listed_positions"] == 2400
    assert env["summary"]["positions"] == 2400
    assert env["by_asset"][0]["positions"] == 60
    assert env["more_assets"]["positions"] == 1800
    assert "round_trips" not in json.dumps(env)
    assert "position_round_trip" not in json.dumps(env)
    assert env["hero_url"].endswith("/backtests/btr_x?tab=trades")
    assert len(json.dumps(env)) < 6_000


def test_scoped_call_passes_scope_through_and_offers_the_cursor():
    page = {
        "scope": {"asset": "BTC", "start": None, "end": None},
        "summary": {"round_trips": 60},
        "by_asset": [],
        "more_assets": None,
        "sort": "time",
        "trips": [_trip("BTC", d, 1.0) for d in range(1, 26)],
        "cursor": "MjU=",
    }
    client = FakeClient({"/v1/backtests/btr_x/trades": page})
    env = mod._handler(
        {"backtest_id": "btr_x", "asset": "BTC", "sort": "time", "limit": 100}, _ctx(client)
    ).to_envelope()
    _, params = client.calls[0]
    assert params == {"asset": "BTC", "sort": "time", "limit": 100}
    assert len(env["positions"]) == 25 and env["cursor"] == "MjU="
    assert env["summary"] == {"positions": 60}
    assert "cursor" in env["narrow"]

    mod._handler({"backtest_id": "btr_x", "asset": "BTC", "cursor": "MjU="}, _ctx(client))
    assert client.calls[-1][1]["cursor"] == "MjU="


def test_a_limit_past_the_declared_100_is_refused_not_clamped():
    """Q-2270: the registered handler enforces the schema's 1-100; the old
    clamp read 500 as 100 and said nothing."""
    from keel.errors import KeelError
    from keel.tools.outcomes import OUTCOMES

    client = FakeClient({})
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_backtest_positions"].handler(
            {"backtest_id": "btr_x", "asset": "BTC", "limit": 500}, _ctx(client)
        )
    assert exc.value.error_code == "argument_out_of_range"
    assert "1 to 100" in str(exc.value) and "500" in str(exc.value)
    assert client.calls == []


def test_argument_errors_refuse_rather_than_widen():
    client = FakeClient({})
    with pytest.raises(ValidationError):
        mod._handler({"backtest_id": ""}, _ctx(client))
    with pytest.raises(ValidationError):
        mod._handler({"backtest_id": "btr_x", "sort": "size"}, _ctx(client))
    with pytest.raises(ValidationError):  # compare needs one asset
        mod._handler({"backtest_id": "btr_x", "compare_to": "btr_y"}, _ctx(client))
    assert client.calls == []


def test_pairing_is_nearest_same_side_entry_within_the_window():
    # Run A closes at 00:00, run B at 12:00: every entry moves 12 hours.
    a = [_trip("BTC", 1, 10.0), _trip("BTC", 5, -4.0), _trip("BTC", 9, 2.0, side="short")]
    b = [
        _trip("BTC", 1, -6.0, hour=12),
        _trip("BTC", 5, 8.0, hour=12),
        _trip("BTC", 9, 2.0, side="long", hour=12),  # side differs: no pair
    ]
    pairs, only_a, only_b = mod.pair_positions(a, b)
    assert [(p["entry_gap_hours"], p["pnl_diff"]) for p in pairs] == [(12.0, -16.0), (12.0, 12.0)]
    assert [t["side"] for t in only_a] == ["short"] and [t["side"] for t in only_b] == ["long"]
    # Outside the window nothing pairs.
    far = [_trip("BTC", 20, 1.0)]
    assert mod.pair_positions(a[:1], far)[0] == []


def test_compare_to_reads_both_runs_once_and_orders_by_largest_difference():
    def page(trips, net):
        return {"summary": {"net_pnl": net}, "trips": trips, "cursor": None}

    client = FakeClient(
        {
            "/v1/backtests/btr_a/trades": page([_trip("BTC", 1, 10.0), _trip("BTC", 5, -4.0)], 6.0),
            "/v1/backtests/btr_b/trades": page(
                [_trip("BTC", 1, -6.0, hour=12), _trip("BTC", 5, 30.0, hour=12)], 24.0
            ),
        }
    )
    env = mod._handler(
        {"backtest_id": "btr_a", "compare_to": "btr_b", "asset": "BTC"}, _ctx(client)
    ).to_envelope()
    assert [c[0] for c in client.calls] == [
        "/v1/backtests/btr_a/trades",
        "/v1/backtests/btr_b/trades",
    ]
    assert all(c[1]["asset"] == "BTC" and c[1]["sort"] == "time" for c in client.calls)
    assert env["net_pnl_diff"] == 18.0
    assert [p["pnl_diff"] for p in env["pairs"]] == [34.0, -16.0]
    assert "truncated" not in env


def test_registered_on_cli_and_listed_profile():
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    _bootstrap()
    tool = OUTCOMES["keel_backtest_positions"]
    assert tool.cli_path == ("backtest", "positions")
    assert tool.annotations["readOnlyHint"] is True
    assert "keel_backtest_positions" in LISTED_PROFILE_TOOLS
    # The rename left no second registration behind.
    assert "keel_backtest_round_trips" not in OUTCOMES
    assert "keel_backtest_round_trips" not in LISTED_PROFILE_TOOLS


def test_cli_verb_is_positions_and_trades_stays_an_alias():
    from click.testing import CliRunner
    from keel.cli.main import cli

    runner = CliRunner()
    for verb in ("positions", "trades"):
        result = runner.invoke(cli, ["backtest", verb, "--help"])
        assert result.exit_code == 0, (verb, result.output)
        assert "--compare-to" in result.output


# ── the run's position count by era (trade-metrics spec 01 §4.3, §6.2) ──


@pytest.mark.parametrize(
    ("run_trades", "run_positions", "stamp"),
    [
        (2600, None, None),  # Era A: no position count recorded
        (None, 2400, "position_round_trip"),  # Era B
        (2600, 2400, "reducing_order"),  # Era C
        (None, None, "flat_to_flat_v9"),  # unknown stamp: keel-api reads nothing
    ],
    ids=["era-A", "era-B", "era-C", "unknown"],
)
def test_the_position_count_is_the_runs_own_by_era(run_trades, run_positions, stamp):
    block = {
        **_summary_block(),
        "run_trades": run_trades,
        "run_positions": run_positions,
        "trade_model": stamp,
    }
    env = mod._handler(
        {"backtest_id": "btr_x"}, _ctx(FakeClient({"/v1/backtests/btr_x/trades": block}))
    ).to_envelope()
    assert env["position_count"] == run_positions
    if run_positions is None:
        assert env["position_count_note"] == "Positions are not recorded for this run."
    else:
        assert "position_count_note" not in env
    assert env["basis"] == mod._BASIS
    assert "trade_model" not in env and "position_model" not in env
    assert "position_round_trip" not in json.dumps(env)
