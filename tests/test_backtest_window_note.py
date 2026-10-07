"""A backtest whose window the platform moved says so (Q-1842).

The R3 probe asked `start_date=2024-01-01`; the run started 2024-07-27 (the
platform's depth floor) and nothing the agent read said so. keel-api has
always put a `WINDOW_CLAMPED_*` warning on the 201 — this tool dropped the
whole `warnings` list. The message below is keel-api's own output for the
probe's request (`services/keel-api/tests/test_backtest_window.py::
TestStartMovedSaysFromWhatToWhat`, pinned there to the character).

Proof it can fail (run 2026-09-23, reverted by reversing the edit): make
`window_adjusted` return None — the three relay arms red, the control
green.

Proof it is not vacuous: the control submits the same run with no warning
and asserts every carrier is ABSENT, so the relay is keyed on the warning
rather than always present.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import view_tool_result


_bootstrap()

MOVED = (
    "Start moved from Jan 1, 2024 to Jul 27, 2024 — Keel's price history "
    "begins Jul 27, 2024, the earliest date any backtest can start."
)

WARNING = {
    "code": "WINDOW_CLAMPED_TO_COVERAGE",
    "message": MOVED,
    "effective_start": "2024-07-27",
    "effective_end": "2026-09-23",
    "requested_start": "2024-01-01",
    "requested_end": "2026-09-23",
    "start_reason": "platform_floor",
}


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_S", 0.05)


def _run(submitted: dict, *, wait: bool) -> dict:
    detail = {
        "id": "bt_probe",
        "status": "COMPLETED",
        "strategy_id": "strat_probe",
        "strategy_name": "BTC beater",
        "start_date": "2024-07-27 00:00:00+00:00",
        "end_date": "2026-09-23 00:00:00+00:00",
        "metrics": {"sharpe_ratio": 0.4, "total_return": -0.9, "total_trades": 40},
    }

    def fake_get(path, **_kw):
        if path == "/v1/backtests/bt_probe":
            return detail
        return {}

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with (
        patch("keel.client.KeelClient.post", return_value=submitted),
        patch("keel.client.KeelClient.get", side_effect=fake_get),
    ):
        return (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "strat_probe",
                    "start_date": "2024-01-01",
                    "end_date": "2026-09-23",
                    "wait": wait,
                },
                ctx,
            )
            .to_envelope()
        )


def _submitted(warnings: list | None) -> dict:
    out = {"id": "bt_probe", "status": "queued", "strategy_id": "strat_probe"}
    if warnings is not None:
        out["warnings"] = warnings
    return out


@pytest.mark.parametrize("wait", [True, False])
def test_the_envelope_carries_requested_and_effective(wait):
    env = _run(_submitted([WARNING]), wait=wait)
    adjusted = env["window_adjusted"]
    assert adjusted["requested_start"] == "2024-01-01"
    assert adjusted["effective_start"] == "2024-07-27"
    assert adjusted["start_reason"] == "platform_floor"
    assert env["window_note"] == MOVED
    assert env["view"]["window_note"] == MOVED


def test_the_text_block_the_model_reads_says_it():
    env = _run(_submitted([WARNING]), wait=True)
    text = view_tool_result(json.dumps(env), "keel_backtest_run").content[0].text
    assert f"window: {MOVED}" in text


def test_other_warnings_are_not_mistaken_for_a_moved_window():
    env = _run(
        _submitted([{"code": "WARMUP_DOMINATES", "message": "Most of the window is warm-up."}]),
        wait=True,
    )
    assert "window_adjusted" not in env and "window_note" not in env


def test_control_an_unmoved_window_says_nothing():
    env = _run(_submitted(None), wait=True)
    assert "window_adjusted" not in env
    assert "window_note" not in env
    assert "window_note" not in env["view"]
    text = view_tool_result(json.dumps(env), "keel_backtest_run").content[0].text
    assert "window:" not in text
