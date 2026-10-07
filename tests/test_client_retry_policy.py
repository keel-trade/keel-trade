"""`KeelClient` retries only what is safe to resend (Q-2273 L1).

keel-api takes no client idempotency key, so a write whose response was lost
may already have been applied: the round-5 audit's compose call met a 503,
was retried, and made two strategies. The policy:

* a GET/HEAD is retried on a 5xx, a timeout or a transport error;
* a write (POST/PATCH/PUT/DELETE) is retried ONLY when the connection failed
  before anything was sent, and otherwise fails with `retryable: false` and a
  message that says it may have been applied.

Arms: each write method × {5xx, read timeout, broken connection} sends ONE
request and raises unconfirmed; CONTROLS — a GET on the same faults is
retried to success, and a write whose connection was refused before sending
is retried to success; a wire arm drives `keel_strategy_compose` on the
hosted listed server through a 503 and counts one POST.

Seed (2026-10-01, reverted by reversing the edit): `_IDEMPOTENT_METHODS`
gaining "POST" → every POST arm and the wire arm red (two POSTs, a success),
the GET and connect-refused controls green.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager

import httpx
import pytest
import respx
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import KeelError


API = "https://api.test.io"


SLEPT: list[float] = []


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    SLEPT.clear()
    monkeypatch.setattr("keel.client.time.sleep", SLEPT.append)


@pytest.fixture
def client():
    c = KeelClient(config=KeelConfig(api_key="sk_test_123", api_url=API))
    yield c
    c.close()


def _flaky(fault):
    """First call faults, every later one succeeds."""
    calls = {"n": 0}

    def side_effect(request):
        calls["n"] += 1
        if calls["n"] == 1:
            if fault == "503":
                return httpx.Response(503, json={"detail": "upstream"})
            if fault == "timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            if fault == "broken":
                raise httpx.RemoteProtocolError("peer closed", request=request)
            if fault == "refused":
                raise httpx.ConnectError("[Errno 61] Connection refused", request=request)
        return httpx.Response(200, json={"ok": True})

    return side_effect, calls


WRITES = [
    ("POST", lambda c: c.post("/v1/strategies", json={"name": "n"})),
    ("PATCH", lambda c: c.patch("/v1/strategies/str_1", json={"source": "s"})),
    ("PUT", lambda c: c.put("/v1/strategies/str_1", json={"source": "s"})),
    ("DELETE", lambda c: c.delete("/v1/strategies/str_1")),
]


@pytest.mark.parametrize("fault", ["503", "timeout", "broken"])
@pytest.mark.parametrize(("method", "call"), WRITES, ids=[m for m, _ in WRITES])
def test_a_write_that_reached_the_server_is_sent_once_and_not_retryable(
    client, method, call, fault
):
    side_effect, calls = _flaky(fault)
    with respx.mock:
        respx.route(method=method, host="api.test.io").mock(side_effect=side_effect)
        with pytest.raises(KeelError) as exc:
            call(client)
    assert calls["n"] == 1, "a write that may have been applied is never resent"
    env = exc.value.to_envelope()
    assert env["retryable"] is False
    assert "may still have been applied" in env["message"]
    assert "read the result back" in env["what_was_expected"].lower()


@pytest.mark.parametrize("fault", ["503", "timeout", "broken"])
def test_control_a_read_is_retried_on_the_same_faults(client, fault):
    side_effect, calls = _flaky(fault)
    with respx.mock:
        respx.get(f"{API}/v1/strategies").mock(side_effect=side_effect)
        assert client.get("/v1/strategies") == {"ok": True}
    assert calls["n"] == 2


@pytest.mark.parametrize(("method", "call"), WRITES, ids=[m for m, _ in WRITES])
def test_control_a_write_refused_before_sending_is_retried(client, method, call):
    side_effect, calls = _flaky("refused")
    with respx.mock:
        respx.route(method=method, host="api.test.io").mock(side_effect=side_effect)
        assert call(client) == {"ok": True}
    assert calls["n"] == 2


def test_a_read_that_keeps_failing_stays_retryable(client):
    with respx.mock:
        route = respx.get(f"{API}/v1/strategies").mock(
            return_value=httpx.Response(503, json={"detail": "x"})
        )
        with pytest.raises(KeelError) as exc:
            client.get("/v1/strategies")
    assert route.call_count == 3
    assert exc.value.retryable is True and exc.value.error_code == "server_error"


# ── The wire: one compose call, one strategy ─────────────────────────────


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    keys = ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_API_KEY", "KEEL_API_URL")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok_test", api_url="https://api.wire.test")
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_a_compose_save_that_meets_a_503_posts_once_over_the_wire():
    from keel.tools.outcomes import _bootstrap

    _bootstrap()
    source = (
        'Globals(target_timeframe="1d")\nUniverse(assets=["BTC"])\n'
        "Execution()\nPipeline([ROC(period=20)])\n"
    )
    posts: list[str] = []

    def side_effect(request):
        if request.method == "POST" and request.url.path == "/v1/strategies":
            posts.append(request.url.path)
            if len(posts) == 1:
                return httpx.Response(503, json={"detail": "upstream"})
            return httpx.Response(201, json={"strategy_id": "str_dup", "name": "dup"})
        return httpx.Response(404, json={"detail": "not faked"})

    async def call():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as mcp:
            return await mcp.call_tool(
                "keel_strategy_compose",
                {"source": source, "name": "dup"},
                raise_on_error=False,
            )

    with _hosted_listed(), respx.mock(assert_all_called=False) as router:
        router.route(host="api.wire.test").mock(side_effect=side_effect)
        result = asyncio.run(call())
    assert len(posts) == 1, posts
    assert result.is_error
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    assert '"retryable": false' in text and "may still have been applied" in text


# ── 429 (Q-2273 L2) ──────────────────────────────────────────────────────
#
# Seed (2026-10-01, reverted by reversing the edit): `_RETRY_AFTER_CAP_S`
# raised to 1e9 → the long-wait arm red (an hour slept inside the call), the
# HTTP-date, last-attempt and write arms green.


def _http_date(seconds_from_now: float) -> str:
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    when = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return format_datetime(when, usegmt=True)


def test_a_429_with_an_http_date_is_waited_out_and_retried(client):
    with respx.mock:
        route = respx.get(f"{API}/v1/me").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": _http_date(3)}, json={}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        assert client.get("/v1/me") == {"ok": True}
    assert route.call_count == 2
    assert len(SLEPT) == 1 and 0 < SLEPT[0] <= 3


def test_a_429_asking_for_a_long_wait_returns_it_instead_of_blocking(client):
    with respx.mock:
        route = respx.get(f"{API}/v1/me").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "3600"}, json={})
        )
        with pytest.raises(KeelError) as exc:
            client.get("/v1/me")
    assert route.call_count == 1 and SLEPT == []
    env = exc.value.to_envelope()
    assert env["code"] == "rate_limited" and env["retryable"] is True
    assert env["detail"] == {"retry_after_s": 3600} and "3600 seconds" in env["message"]


def test_a_429_on_the_last_attempt_is_reported_not_slept_on(client):
    with respx.mock:
        route = respx.get(f"{API}/v1/me").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"}, json={})
        )
        with pytest.raises(KeelError) as exc:
            client.get("/v1/me")
    assert route.call_count == 3 and SLEPT == [1.0, 1.0]
    assert exc.value.error_code == "rate_limited" and "None" not in str(exc.value)


def test_control_a_429_on_a_write_is_resent_because_it_was_never_processed(client):
    with respx.mock:
        route = respx.post(f"{API}/v1/strategies").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}, json={}),
                httpx.Response(201, json={"strategy_id": "str_1"}),
            ]
        )
        assert client.post("/v1/strategies", json={}) == {"strategy_id": "str_1"}
    assert route.call_count == 2 and SLEPT == [2.0]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("0", 0.0), ("", None), ("soon", None), ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0)],
)
def test_retry_after_reads_both_forms(header, expected):
    from keel.errors import retry_after_seconds

    assert retry_after_seconds(header) == expected
