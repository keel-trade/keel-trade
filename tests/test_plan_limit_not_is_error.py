"""A plan limit is an expected outcome, not a tool failure: no MCP ``isError`` (Q-7927).

Claude's directory listing health badge counts ``isError: true`` results (over 2 % of requests in 30 days reads
"Worth a look", over 5 % "Degraded"; CARD-RESEARCH sources/RESUBMISSION.md). Q-2273 B1 stamped the bit on every error
envelope, and the plan-limit refusals share the error envelope's shape, so a person who spent an allowance counted
against the listing as a broken call. ``_channels.is_plan_limit_envelope`` names the plan-limit refusals and
``_mcp_adapter._flag_error`` leaves the bit off for them; the envelope (code, words, ``retryable: false``, the
"don't retry" instruction) is unchanged.

Per tool, through the REAL handler with keel-api's 403 faked (the client raises what ``translate_http_error`` makes of
the body) and the REAL ``result_for_mode`` of the tool's registration mode:

* backtest run (a quota handoff with ``limit_view``), paper deploy (the slot cap's handoff), screen run (a quota
  ``insufficient_entitlements`` with the quota code in ``example``), research run (a research wall, the quota code in
  ``detail``): ``is_error`` False, and the refusal is the same envelope as before (code, message, retryable false,
  what_was_expected);
* CONTROL, the same tools: a 404 and a scope-missing 403 stay ``is_error`` True.

Seeded red (in this file): ``is_plan_limit_envelope`` answering False (the tree before Q-7927) puts every plan-limit
arm back to ``is_error`` True while the control arms do not move.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext


_bootstrap()


def _quota(unit, label, kind, code, limit, used, reset=1790553600):
    return {
        "title": "Forbidden",
        "status": 403,
        "detail": f"You've used all {limit} {label} included on this plan.",
        "code": code,
        "quota": {
            "kind": kind,
            "unit": unit,
            "label": label,
            "code": code,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "period": "weekly",
            "reset_epoch": reset,
            "plan": "free",
            "plan_name": "Free",
            "higher_plans": [
                {"plan": "trader", "version": "v2", "name": "Trader", "limit": limit * 10},
                {"plan": "pro", "version": "v2", "name": "Pro", "limit": limit * 40},
            ],
        },
    }


BACKTEST_WALL = _quota("backtest_runs", "backtests", "insufficient", "quota_exhausted", 50, 50)
PAPER_WALL = _quota(
    "paper_strategies", "paper strategies", "cap_exceeded", "quota_cap_reached", 1, 1
)
RESEARCH_WALL = {
    "detail": {
        "code": "quota_exhausted",
        "message": "Studies can't run yet. You have used 5 of 5 studies; they reset on Mon 7 Dec.",
        "card_words": "Studies can't run yet. You have used 5 of 5 studies; they reset on Mon 7 Dec.",
        "unit": "research_studies",
        "used": 5,
        "limit": 5,
    }
}

#: tool -> (args, the 403 body the call meets)
WALLS = {
    "keel_backtest_run": ({"strategy_id": "str_x", "end_date": "2026-09-23"}, BACKTEST_WALL),
    "keel_paper_deploy": ({"strategy_id": "str_x"}, PAPER_WALL),
    "keel_screener_run": ({"strategy_id": "str_" + "a" * 26}, BACKTEST_WALL),
    "keel_research_run": ({"study_id": "stu_" + "a" * 26, "max_runs": 10}, RESEARCH_WALL),
}
SCOPE_403 = {"title": "Forbidden", "status": 403, "detail": "Forbidden."}
NOT_FOUND = {"title": "Not Found", "status": 404, "detail": "Not found."}


def _call(tool: str, args: dict, status: int, body: dict, monkeypatch) -> tuple[dict, object]:
    """The tool's MCP result for a keel-api answer of ``status`` + ``body``: the handler's raised refusal, its wire
    envelope, and the registration mode's result (``_mcp_adapter._make_handler`` + ``result_for_mode``)."""
    from keel.tools.outcomes import _mcp_adapter as adapter

    monkeypatch.setattr("keel.tools.outcomes._handoff._is_anon_session", lambda: False)
    monkeypatch.setenv("KEEL_APP_URL", "https://app.usekeel.io")
    exc = translate_http_error(status, json.dumps(body))
    client = MagicMock()
    client.post.side_effect = exc
    client.get.side_effect = exc
    client.patch.side_effect = exc
    client.put.side_effect = exc
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    with pytest.raises(KeelError) as raised:
        get(tool).handler(args, ctx)
    envelope = adapter._wire_error(raised.value.to_envelope())
    result = adapter.result_for_mode(adapter.registration_mode(tool), json.dumps(envelope), tool)
    return envelope, result


@pytest.mark.parametrize("tool", sorted(WALLS))
def test_a_plan_limit_is_not_is_error_and_keeps_its_refusal(tool, monkeypatch):
    from keel.tools.outcomes._channels import is_plan_limit_envelope

    args, body = WALLS[tool]
    envelope, result = _call(tool, args, 403, body, monkeypatch)
    assert is_plan_limit_envelope(envelope), envelope  # non-vacuity: this IS a plan limit
    assert getattr(result, "is_error", False) is False, (tool, envelope)
    # the refusal itself is untouched: the model still reads a refusal it must not retry
    assert envelope["retryable"] is False and envelope["message"] and envelope["code"], envelope
    assert "what_was_expected" in envelope and "suggested_next_action" in envelope


@pytest.mark.parametrize("tool", sorted(WALLS))
@pytest.mark.parametrize("status,body", [(404, NOT_FOUND), (403, SCOPE_403)], ids=["404", "scope"])
def test_control_a_real_error_stays_is_error(tool, status, body, monkeypatch):
    from keel.tools.outcomes._channels import is_plan_limit_envelope

    args, _ = WALLS[tool]
    envelope, result = _call(tool, args, status, body, monkeypatch)
    assert not is_plan_limit_envelope(envelope), envelope
    assert result.is_error is True, (tool, status, envelope)


@pytest.mark.parametrize("tool", sorted(WALLS))
def test_seeded_without_the_carve_out_every_plan_limit_is_is_error(tool, monkeypatch):
    from keel.tools.outcomes import _mcp_adapter as adapter

    monkeypatch.setattr(adapter, "is_plan_limit_envelope", lambda envelope: False)
    args, body = WALLS[tool]
    envelope, result = _call(tool, args, 403, body, monkeypatch)
    assert result.is_error is True, (tool, envelope)
