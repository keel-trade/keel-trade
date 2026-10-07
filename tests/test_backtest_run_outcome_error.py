"""A hosted ``keel_backtest_run`` whose run ENDS badly audits as an error (Q-2293).

agent-funnel-measurement M2 lane D1-3. The call succeeds and returns its normal
envelope either way; what changes is the request outcome slot (Q-1617), the
one channel from a tool to its audit row:

- the run ends ``failed`` or ``timeout`` within the call -> ``is_error: True``;
- it ends ``completed`` -> no error;
- it is still ``queued`` / ``running`` when the poll budget ends -> no error
  (the call did not fail, and neither has the run yet);
- it ends ``cancelled`` -> no error (a cancel is the user's act).

The REAL tool runs through the REAL MCP adapter (``_make_handler``), with only
keel-api faked, so the arms prove the stamp reaches the slot through the same
path a hosted call takes, and that the envelope the agent reads is
byte-identical with and without a slot open (measurement adds nothing).

Proofs (recorded 2026-10-04, each seed reverted by reversing the edit):
seeding ``_RUN_FAILED_STATUSES`` to ``frozenset()`` reds the ``failed`` and
``timeout`` arms; seeding it to include ``"queued"`` and ``"running"`` (the
reversal the brief names: a still-running run audited as an error) reds the
``queued`` and ``running`` arms. ``completed`` and ``cancelled`` stay green
through both, and every arm first asserts the run id was recorded, so a slot
that recorded nothing at all cannot pass as "no error".
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from keel.hosting import close_request_outcome, current_request_outcome, open_request_outcome
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_run as _bt_run_mod  # noqa: F401 -- self-registers
from keel.tools.outcomes._mcp_adapter import _make_handler


RUN = "btr_01k5g8w2y3z4a5b6c7d8e9f0g3"
STRATEGY = "str_01k5g8w2y3z4a5b6c7d8e9f0g1"


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr("keel.tools.outcomes.backtest_run._POLL_MAX_S", 0.05)


def _call(final_status: str, *, open_slot: bool) -> tuple[dict, dict | None]:
    """One hosted-shaped call that polls to ``final_status``; returns the
    envelope the agent reads and the outcome slot the audit row is built from."""
    submitted = {"id": RUN, "status": "queued", "strategy_id": STRATEGY}
    final = {
        "id": RUN,
        "status": final_status,
        "strategy_id": STRATEGY,
        "start_date": "2025-01-01",
        "end_date": "2025-06-30",
        "error_message": "Exceeded 900s limit" if final_status == "timeout" else None,
        "metrics": {"sharpe_ratio": 1.2} if final_status == "completed" else None,
    }

    def fake_get(path, **_kw):
        if path == f"/v1/backtests/{RUN}":
            return final
        if path == f"/v1/backtests/{RUN}/curve":
            return {}
        if path == "/v1/backtests":
            return {"data": [], "pagination": {}}
        if path.startswith("/v1/strategy-work"):
            return {}
        raise AssertionError(f"unexpected GET {path}")

    token = open_request_outcome() if open_slot else None
    try:
        with (
            patch("keel.client.KeelClient.post", return_value=submitted),
            patch("keel.client.KeelClient.get", side_effect=fake_get),
        ):
            handler = _make_handler(OUTCOMES["keel_backtest_run"], frozenset())
            envelope = json.loads(handler(strategy_id=STRATEGY, wait=True, skip_readiness=True))
        slot = dict(current_request_outcome()) if open_slot else None
    finally:
        if token is not None:
            close_request_outcome(token)
    return envelope, slot


@pytest.mark.parametrize(
    ("final_status", "audited_error"),
    [
        ("failed", True),
        ("timeout", True),
        ("completed", False),
        ("queued", False),
        ("running", False),
        ("cancelled", False),
    ],
)
def test_the_runs_own_verdict_reaches_the_audit_slot(final_status, audited_error):
    envelope, slot = _call(final_status, open_slot=True)
    # Non-vacuity: the call went through and the slot recorded its run.
    assert envelope["run_id"] == RUN
    assert slot["backtest_run_id"] == RUN
    assert slot.get("is_error", False) is audited_error


@pytest.mark.parametrize("final_status", ["failed", "timeout", "completed", "running"])
def test_the_agent_reads_the_same_envelope_with_or_without_the_audit(final_status):
    """Nothing analytics-shaped reaches the agent: opening the slot (hosted)
    changes not one byte of what the call returns."""
    with_slot, _ = _call(final_status, open_slot=True)
    without_slot, _ = _call(final_status, open_slot=False)
    # The view's render instant and per-process render counter differ between
    # ANY two calls; they are the only two keys normalised.
    for env in (with_slot, without_slot):
        env["view"].pop("at")
        env["view"].pop("seq")
    assert with_slot == without_slot
    assert "is_error" not in with_slot
    assert "code" not in with_slot, "a run failure is not an error envelope"
