"""Good-result nudge tests — spec 03 R3(a) (agent-first-build M3.3).

The one-line deploy suggestion appears EXACTLY when the run's durable
``metrics.good_result`` marker (spec 02) is set, with the
surface-appropriate link: deploy-intent deep link on the full profile,
overview URL + navigation-only language on the listed profile.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._nudge import good_result_nudge


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


GOOD_METRICS = {
    "sharpe_ratio": 1.72,
    "total_return": 39.4,
    "max_drawdown": -14.3,
    "good_result": {
        "sharpe": 1.72,
        "sharpe_threshold": 1.5,
        "range_days": 364.0,
        "min_range_days": 182.0,
    },
}
SUB_THRESHOLD_METRICS = {
    "sharpe_ratio": 0.9,
    "total_return": 5.0,
    "max_drawdown": -22.0,
    # No good_result marker — the worker writes it only when the gate fires.
}

MINT_RESPONSE = {
    "handoff_url": "https://app.usekeel.io/deploy?intent=tokN",
    "intent_token": "tokN",
    "expires_at": "2026-07-17T01:00:00+00:00",
    "suggested_config": {"sizing_usd": 400, "sizing_basis": {"max_drawdown_pct": 14.3}},
}

# Research/08 token families that must NEVER appear in listed-profile output.
LISTED_BANNED_TOKENS = (
    "deploy",
    "fund",
    "trade",
    "trading",
    "go live",
    "live",
    "upgrade",
    "wallet",
    "leverage",
)


def _detail(status: str, metrics: dict | None) -> dict:
    return {
        "id": "btr_1",
        "strategy_id": "strat_n",
        "strategy_name": "Nudge Test",
        "status": status,
        "start_date": "2025-07-01",
        "end_date": "2026-06-30",
        "metrics": metrics,
    }


# ─── keel_backtest_run (wait path) ───────────────────────────────────────


def test_backtest_run_nudges_exactly_when_good_result_true():
    client = MagicMock()
    client.post.side_effect = [
        {"id": "btr_1", "status": "queued"},  # POST /v1/backtests
        MINT_RESPONSE,  # POST /v1/live/deploy-intents (nudge link)
    ]
    client.get.return_value = _detail("completed", GOOD_METRICS)

    env = (
        get("keel_backtest_run")
        .handler({"strategy_id": "strat_n", "no_ownership_hint": True}, _ctx(client))
        .to_envelope()
    )

    assert "nudge" in env
    nudge = env["nudge"]
    assert nudge.count("\n") == 0, "the nudge is exactly one line"
    # Honest numbers: Sharpe never cited without its drawdown; range named.
    assert "Sharpe 1.72" in nudge
    assert "max drawdown -14.3%" in nudge
    assert "364 days" in nudge
    # Full profile → the deploy-intent deep link.
    assert "https://app.usekeel.io/deploy?intent=tokN" in nudge
    # The do-nothing alternative is present, no return promises.
    assert "do nothing" in nudge.lower()
    assert " earn " not in f" {nudge.lower()} "
    assert client.post.call_args_list[1] == call(
        "/v1/live/deploy-intents", json={"strategy_id": "strat_n"}
    )


def test_backtest_run_no_nudge_without_good_result():
    client = MagicMock()
    client.post.return_value = {"id": "btr_1", "status": "queued"}
    client.get.return_value = _detail("completed", SUB_THRESHOLD_METRICS)

    env = (
        get("keel_backtest_run")
        .handler({"strategy_id": "strat_n", "no_ownership_hint": True}, _ctx(client))
        .to_envelope()
    )

    assert "nudge" not in env
    # No good result → no deploy-intent mint either.
    assert all(c.args[0] != "/v1/live/deploy-intents" for c in client.post.call_args_list)


# ─── keel_backtest_summarize ─────────────────────────────────────────────


def test_summarize_nudges_when_good_result_true():
    client = MagicMock()
    client.get.side_effect = [
        _detail("completed", GOOD_METRICS),  # GET /v1/backtests/{id}
        {"presigned_url": "https://s3/x", "expires_in": 3600},  # /results
    ]
    client.post.return_value = MINT_RESPONSE

    env = (
        get("keel_backtest_summarize").handler({"backtest_id": "btr_1"}, _ctx(client)).to_envelope()
    )

    assert "nudge" in env
    assert "https://app.usekeel.io/deploy?intent=tokN" in env["nudge"]
    assert "max drawdown -14.3%" in env["nudge"]


def test_summarize_no_nudge_without_good_result():
    client = MagicMock()
    client.get.side_effect = [
        _detail("completed", SUB_THRESHOLD_METRICS),
        {"presigned_url": "https://s3/x", "expires_in": 3600},
    ]

    env = (
        get("keel_backtest_summarize").handler({"backtest_id": "btr_1"}, _ctx(client)).to_envelope()
    )

    assert "nudge" not in env
    client.post.assert_not_called()


# ─── Listed-profile surface (research/08) ────────────────────────────────


def test_listed_profile_nudge_is_navigation_only(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    client = MagicMock()

    line = good_result_nudge(
        _detail("completed", GOOD_METRICS), strategy_id="strat_n", ctx=_ctx(client)
    )

    assert line is not None
    assert "view this strategy in the Keel app" in line
    assert "https://app.usekeel.io/strategies/strat_n" in line
    lowered = line.lower()
    for token in LISTED_BANNED_TOKENS:
        assert token not in lowered, f"listed nudge must not say {token!r}"
    # Listed NEVER mints deploy-intent links.
    client.post.assert_not_called()
    # Numbers still honest: drawdown named next to Sharpe.
    assert "Sharpe 1.72" in line
    assert "max drawdown -14.3%" in line


# ─── Fallbacks + honesty edge cases ──────────────────────────────────────


def test_full_profile_falls_back_to_overview_when_mint_fails():
    from keel.errors import KeelError

    client = MagicMock()
    client.post.side_effect = KeelError("intents unavailable")

    line = good_result_nudge(
        _detail("completed", GOOD_METRICS), strategy_id="strat_n", ctx=_ctx(client)
    )

    assert line is not None
    assert "https://app.usekeel.io/strategies/strat_n" in line
    assert "intent=" not in line


def test_no_numbers_cited_when_drawdown_missing():
    metrics = {
        "sharpe_ratio": 2.0,
        "good_result": {"sharpe": 2.0, "range_days": 300.0},
        # no max_drawdown key
    }
    client = MagicMock()
    client.post.return_value = MINT_RESPONSE

    line = good_result_nudge(_detail("completed", metrics), strategy_id="strat_n", ctx=_ctx(client))

    assert line is not None
    # Honesty rule: a Sharpe is never cited without its drawdown.
    assert "Sharpe" not in line
    assert "%" not in line


def test_no_nudge_for_malformed_or_missing_inputs():
    client = MagicMock()
    ctx = _ctx(client)
    assert good_result_nudge(None, strategy_id="s", ctx=ctx) is None
    assert good_result_nudge({}, strategy_id="s", ctx=ctx) is None
    assert good_result_nudge({"metrics": None}, strategy_id="s", ctx=ctx) is None
    assert good_result_nudge(_detail("completed", GOOD_METRICS), strategy_id=None, ctx=ctx) is None
    assert good_result_nudge({"metrics": {"good_result": False}}, strategy_id="s", ctx=ctx) is None
