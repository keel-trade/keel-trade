"""`version` on `keel_backtest_run` pins what runs (spec 03 §2.1, guard 3).

A pinned run — `commit_id` OR `version` — never auto-pushes a checkout; the
integer an agent naturally passes goes out as its decimal string (keel-api's
request models are strict and would 422 a JSON integer); the
`WINDOW_INVERTED` recovery call and the plan-wall resume both carry it, so
following either reruns the SAME version.

SEED (run 2026-09-23, reverted by reversing the edit): drop the `version`
arm from `backtest_run._is_pinned` (`return bool(args.get("commit_id"))`) —
`test_a_version_pin_never_auto_pushes` reds (the fake guard records a call)
while the unpinned CONTROL keeps recording exactly one.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_run as bt
from keel.tools.outcomes._base import ToolContext


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


SUBMITTED = {"id": "btr_pin", "status": "queued", "strategy_id": "str_pin"}


def _run(ctx, args, *, guard_calls: list, post=None):
    def fake_guard(*_a, **_kw):
        guard_calls.append(1)
        return None

    post_mock = post or (lambda *_a, **_kw: SUBMITTED)
    with (
        patch("keel.tools.outcomes._sync_guard.write_through_guard", side_effect=fake_guard),
        patch("keel.client.KeelClient.post", side_effect=post_mock) as posted,
    ):
        env = OUTCOMES["keel_backtest_run"].handler(
            {"strategy_id": "str_pin", "wait": False, **args}, ctx
        )
    return env.to_envelope(), posted


def test_the_unpinned_control_reaches_the_guard(ctx):
    """CONTROL (non-vacuity): with no pin the fake guard IS called — so the
    pinned arms' zero is the pin, not a guard that never runs."""
    calls: list = []
    _run(ctx, {}, guard_calls=calls)
    assert calls == [1]


def test_a_version_pin_never_auto_pushes(ctx):
    calls: list = []
    _, posted = _run(ctx, {"version": 3}, guard_calls=calls)
    assert calls == []
    body = posted.call_args.kwargs["json"]
    # An int goes out as its decimal string (StrictRequest would 422 a JSON int).
    assert body["version"] == "3"
    assert "commit_id" not in body


def test_a_tag_or_commit_id_ref_is_sent_as_given(ctx):
    calls: list = []
    _, posted = _run(ctx, {"version": "champion"}, guard_calls=calls)
    assert posted.call_args.kwargs["json"]["version"] == "champion"
    assert calls == []


def test_the_retry_args_carry_the_version():
    args = {"strategy_id": "str_pin", "version": 3, "start_date": "2025-01-01", "_internal": 1}
    out = bt._retry_args(args)
    assert out["version"] == 3
    assert "_internal" not in out
    # Q-2012 / Q-2029: an end_date the caller did not pass is never added.
    assert "end_date" not in out


def test_the_inverted_window_recovery_keeps_the_version(ctx):
    refusal = translate_http_error(
        422,
        json.dumps(
            {
                "detail": {
                    "code": "WINDOW_INVERTED",
                    "message": "Cannot backtest — start after end.",
                    "requested_start": "2025-06-01",
                    "requested_end": "2025-01-01",
                }
            }
        ),
    )

    def post(*_a, **_kw):
        raise refusal

    with pytest.raises(KeelError) as exc:
        _run(
            ctx,
            {"version": 3, "start_date": "2025-06-01", "end_date": "2025-01-01"},
            guard_calls=[],
            post=post,
        )
    args = exc.value.to_envelope()["suggested_next_action"]["args"]
    assert args["version"] == 3
    assert args["start_date"] == "2025-01-01"


def test_version_not_found_names_the_log(ctx):
    refusal = translate_http_error(
        409,
        json.dumps(
            {
                "type": "https://api.usekeel.io/errors/conflict",
                "title": "Conflict",
                "status": 409,
                "detail": "Version '9' is not a version of strategy str_pin.",
                "code": "VERSION_NOT_FOUND",
            }
        ),
    )

    def post(*_a, **_kw):
        raise refusal

    with (
        patch("keel.client.KeelClient.get", return_value={"current_sequence": 7}),
        pytest.raises(KeelError) as exc,
    ):
        _run(ctx, {"version": 9}, guard_calls=[], post=post)
    env = exc.value.to_envelope()
    assert env["suggested_next_action"]["tool"] == "keel_strategy_history"
    assert env["suggested_next_action"]["args"] == {"strategy_id": "str_pin"}
    assert env["what_was_expected"].startswith("a sequence number (1…7), a tag, HEAD")


def test_the_mcp_signature_accepts_an_integer_version():
    """The synthesized FastMCP signature must not be a strict `str`, or the
    integer an agent sends is refused before the handler runs."""
    from keel.tools.outcomes._mcp_adapter import _json_type_to_py

    assert _json_type_to_py(bt.INPUT_SCHEMA["properties"]["version"]) == "int | str"
