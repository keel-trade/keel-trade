"""`keel_live_monitor`'s overview says when the next run is and how the last one
ended (doc 14 §5 R5, §11), in plain words.

`next_run` is read off keel-api's `next_run_at` (computed server-side from the
evaluation grid, so Claude and the live page cannot disagree); `last_run` off
the executions view, newest row (`limit=1`). Both are top-level blocks beside
the row, so the listed projection (an allow-list of row fields) keeps them.

Seeds (each reversed by the exact edit): drop the `next_run` assignment in
`_handler` ⇒ the handler arm reds; make `_last_run_block` read the newest row
with no `limit` ⇒ the request arm reds.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.config import KeelConfig
from keel.errors import KeelError
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes.live_monitor import _last_run_text, _next_run_block


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


def _ctx(client) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io")


def _client(row: dict, executions):
    def _get(path, **params):
        if path.endswith("/executions"):
            if isinstance(executions, Exception):
                raise executions
            return executions
        if path.endswith("/stats") or path.endswith("/equity"):
            return {}
        return row

    client = MagicMock()
    client.get.side_effect = _get
    client._config = KeelConfig(api_key="test", api_url="https://monitor-unselected.test")
    return client


# ── next_run ─────────────────────────────────────────────────────────────


def test_next_run_words_the_servers_instant():
    block = _next_run_block({"status": "LIVE", "next_run_at": "2026-11-04T12:00:00Z"})
    assert block == {"at": "2026-11-04T12:00:00Z", "text": "Next run: 4 Nov 2026 at 12:00 UTC."}


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("PAUSED", "No run is scheduled while it is paused."),
        ("STOPPED", "No run is scheduled while it is stopped."),
        ("DEPLOYING", "No run is scheduled while it is still starting."),
        ("LIVE", "The next run time is not available for this schedule."),
    ],
)
def test_a_null_next_run_says_why(status, text):
    assert _next_run_block({"status": status, "next_run_at": None}) == {"at": None, "text": text}


def test_an_older_keel_api_without_the_field_reports_nothing():
    """Absent is "not reported", never "not scheduled"."""
    assert _next_run_block({"status": "LIVE"}) is None


# ── last_run ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "text"),
    [
        (
            {"execution_status": "completed", "order_count": 2, "filled_count": 2},
            "The last run placed 2 orders: 2 filled.",
        ),
        (
            {
                "execution_status": "completed",
                "order_count": 3,
                "filled_count": 1,
                "rejected_count": 1,
                "skipped_count": 1,
            },
            "The last run placed 3 orders: 1 filled, 1 rejected, 1 skipped.",
        ),
        (
            {"execution_status": "completed", "order_count": 0},
            "The last run placed no orders: the positions already matched its signal.",
        ),
        (
            {"execution_status": "failed", "error_summary": "Your account holds $0."},
            "The last run's trades did not go through: Your account holds $0.",
        ),
        (
            {"signal_status": "failed", "execution_status": "skipped"},
            "The last run could not work out its signal.",
        ),
        ({"execution_status": "executing"}, "A run is in progress now."),
        (
            {"execution_status": "COMPLETED", "order_count": 1, "filled_count": 1},
            "The last run placed 1 order: 1 filled.",
        ),
    ],
)
def test_last_run_sentences(row, text):
    assert _last_run_text(row) == text


def test_the_overview_carries_both_blocks_and_reads_one_execution():
    row = {
        "deployment_id": "dep_42",
        "status": "LIVE",
        "next_run_at": "2026-11-04T12:00:00Z",
    }
    newest = {
        "execution_status": "completed",
        "order_count": 1,
        "filled_count": 1,
        "signal_as_of_time": "2026-11-03T12:00:00Z",
    }
    client = _client(row, [newest])
    env = get("keel_live_monitor").handler({"deployment_id": "dep_42"}, _ctx(client)).to_envelope()

    assert env["next_run"] == {
        "at": "2026-11-04T12:00:00Z",
        "text": "Next run: 4 Nov 2026 at 12:00 UTC.",
    }
    assert env["last_run"] == {
        "at": "2026-11-03T12:00:00Z",
        "outcome": "completed",
        "text": "The last run placed 1 order: 1 filled. That run used the 3 Nov 2026 at "
        "12:00 UTC close.",
    }
    executions = [c for c in client.get.call_args_list if c.args[0].endswith("/executions")]
    assert len(executions) == 1
    assert executions[0].kwargs["limit"] == 1
    assert executions[0].kwargs["headers"] == {"X-Keel-Agent-Telemetry": "1"}
    # The row itself is relayed as sent: the blocks are beside it, not in it.
    assert env["data"]["next_run_at"] == "2026-11-04T12:00:00Z"


def test_a_deployment_with_no_run_yet_says_so():
    client = _client({"deployment_id": "dep_42", "status": "LIVE", "next_run_at": None}, [])
    env = get("keel_live_monitor").handler({"deployment_id": "dep_42"}, _ctx(client)).to_envelope()
    assert env["last_run"] == {"at": None, "outcome": None, "text": "It has not run yet."}


def test_a_failed_executions_read_omits_last_run_only():
    """Best-effort, like the stats and curve: the overview still answers."""
    client = _client(
        {"deployment_id": "dep_42", "status": "LIVE", "next_run_at": "2026-11-04T12:00:00Z"},
        KeelError("boom", error_code="upstream"),
    )
    env = get("keel_live_monitor").handler({"deployment_id": "dep_42"}, _ctx(client)).to_envelope()
    assert "last_run" not in env
    assert env["next_run"]["at"] == "2026-11-04T12:00:00Z"


def test_other_views_do_not_read_runs():
    client = _client({"deployment_id": "dep_42", "status": "LIVE"}, [])
    env = (
        get("keel_live_monitor")
        .handler({"deployment_id": "dep_42", "view": "positions"}, _ctx(client))
        .to_envelope()
    )
    assert "next_run" not in env and "last_run" not in env
    assert not [c for c in client.get.call_args_list if c.args[0].endswith("/executions")]
