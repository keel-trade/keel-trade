"""LBF: the screen tools' failure modes (fix batch 2026-11-03).

* ``keel_screener_run`` sends ONE Idempotency-Key per call and resends the
  submit once on an unknown outcome (a 5xx or a lost answer) with the SAME
  key; keel-api answers the resend with the run it already queued, so the
  call never double-charges. A failed read after the submit returns the run
  in flight with its run_id instead of an error inviting a second submit.
  Seeded red (the key minted per attempt): the two posts carry different keys.
* A CODED 5xx keeps keel-api's code, words and ``retryable`` verdict; an
  uncoded 5xx is unchanged (control).
* The screen refusals added to the table name their next step.
"""

import json

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()
SID = "str_" + "a" * 26
RID = "res_" + "a" * 26


class Flaky:
    """A submit whose first answer is lost; reads that fail after it."""

    def __init__(self, *, first, reads_fail=True):
        self.posts = []
        self.first = first
        self.reads_fail = reads_fail

    def post(self, path, json=None, headers=None, **params):
        self.posts.append((path, json, dict(headers or {})))
        if len(self.posts) == 1 and self.first is not None:
            raise self.first
        return {"run_id": RID, "status": "QUEUED"}

    def get(self, path, **params):
        if self.reads_fail:
            raise translate_http_error(502, "Bad gateway")
        return {"run_id": RID, "status": "QUEUED"}


def _run(client, **args):
    return OUTCOMES["keel_screener_run"].handler(
        {"strategy_id": SID, **args}, ToolContext(api_client=client)
    )


def test_a_lost_submit_is_resent_once_with_the_same_key(monkeypatch):
    monkeypatch.setattr("keel.tools.outcomes.screener_run.time.sleep", lambda s: None)
    monkeypatch.setattr("keel.tools.outcomes.screener_run._poll_budget_s", lambda ctx: 1)
    client = Flaky(first=translate_http_error(500, "Internal Server Error"))
    result = _run(client)
    assert len(client.posts) == 2  # non-vacuous: the resend happened
    keys = {post[2]["Idempotency-Key"] for post in client.posts}
    assert len(keys) == 1 and next(iter(keys)).startswith("sdk-screener-run-")
    # The poll failed after the submit: the run is reported in flight.
    assert result.extra["run"]["run_id"] == RID
    assert result.extra["view"]["status"] == "QUEUED"


def test_two_calls_are_two_runs():
    a, b = Flaky(first=None, reads_fail=False), Flaky(first=None, reads_fail=False)
    _run(a, wait=False)
    _run(b, wait=False)
    assert a.posts[0][2]["Idempotency-Key"] != b.posts[0][2]["Idempotency-Key"]


def test_a_coded_refusal_is_never_resent():
    body = json.dumps({"detail": {"code": "SCREENER_RUN_TOO_LARGE", "detail": "Too large."}})
    client = Flaky(first=translate_http_error(422, body))
    with pytest.raises(KeelError):
        _run(client)
    assert len(client.posts) == 1


def test_a_coded_5xx_keeps_its_code_words_and_retry_verdict():
    body = json.dumps(
        {
            "detail": {
                "code": "platform:refresh_capacity_exhausted",
                "detail": "Keel can't take more auto-refreshing screens right now.",
                "next": "Keep the screen without auto-refresh, or turn another one off.",
                "retryable": False,
            }
        }
    )
    err = translate_http_error(503, body)
    assert err.error_code == "platform:refresh_capacity_exhausted"
    assert str(err) == "Keel can't take more auto-refreshing screens right now."
    assert err.retryable is False and getattr(err, "refused", False) is True
    plain = translate_http_error(503, "Service Unavailable")  # control: uncoded
    assert plain.error_code == "server_error" and plain.retryable is True
    assert not getattr(plain, "refused", False)


def _refusing(status, detail):
    body = json.dumps({"detail": detail})

    class Refusing:
        def get(self, path, **params):
            return {"head_commit_id": "c", "active_share": None, "items": []}

        def post(self, path, json=None, **params):
            raise translate_http_error(status, body)

        put = post

    return Refusing()


@pytest.mark.parametrize(
    "tool,args,status,detail,next_tool,retryable",
    [
        (
            "keel_screener_refresh",
            {"strategy_id": SID},
            403,
            {
                "code": "SCREENER_REFRESH_BELOW_FLOOR",
                "detail": "Your plan auto-refreshes screens at most every 1h.",
                "floor_minutes": 60,
                "timeframe": "15min",
            },
            None,
            False,
        ),
        (
            "keel_screener_view_set",
            {"strategy_id": SID, "view": "x", "expected_revision": 2},
            409,
            {
                "code": "SCREENER_VIEWS_CONFLICT",
                "detail": "The views were changed somewhere else at the same time.",
                "current_revision": 3,
            },
            "keel_screener_view",
            False,
        ),
    ],
)
def test_screen_refusals_name_their_next_step(tool, args, status, detail, next_tool, retryable):
    with pytest.raises(KeelError) as raised:
        OUTCOMES[tool].handler(args, ToolContext(api_client=_refusing(status, detail)))
    envelope = raised.value.to_envelope()
    assert envelope["code"] == detail["code"]
    assert envelope["message"] == detail["detail"]
    assert envelope["what_was_expected"]
    assert raised.value.retryable is retryable
    action = envelope.get("suggested_next_action") or {}
    assert action.get("tool") == next_tool


def test_a_coded_503_on_a_write_is_a_refusal_not_an_unknown_outcome(monkeypatch):
    """A coded 5xx says nothing was applied: the client reports keel-api's
    refusal (code, words, its retry verdict), never "may still have been
    applied". Control: an uncoded 503 on the same write stays unconfirmed.
    Seeded red (``refused`` ignored in the client): the coded arm's message
    gains the write-unconfirmed sentence."""
    import httpx
    import respx
    from keel.client import KeelClient
    from keel.config import KeelConfig

    monkeypatch.setattr("keel.client.time.sleep", lambda s: None)
    coded = {
        "detail": {
            "code": "platform:refresh_capacity_exhausted",
            "detail": "Keel can't take more auto-refreshing screens right now.",
            "retryable": False,
        }
    }
    client = KeelClient(config=KeelConfig(api_key="sk_test_123", api_url="https://api.test.io"))
    try:
        for body, refused in ((coded, True), ({"detail": "upstream"}, False)):
            with respx.mock:
                respx.post("https://api.test.io/v1/screeners/s/refresh").mock(
                    return_value=httpx.Response(503, json=body)
                )
                with pytest.raises(KeelError) as raised:
                    client.post("/v1/screeners/s/refresh", json={"commit_id": "c"})
            message = raised.value.to_envelope()["message"]
            assert ("may still have been applied" in message) is (not refused)
            if refused:
                assert raised.value.error_code == "platform:refresh_capacity_exhausted"
    finally:
        client.close()
