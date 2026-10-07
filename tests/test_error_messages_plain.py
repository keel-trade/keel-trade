"""Error messages are plain language, never raw internals (Q-2273 L3).

The round-5 audit read these as the `message` of listed tools' errors:
`{"detail":[{"type":"less_than_equal","loc":["query","limit"]…` (a FastAPI
422 echoed), `HTTP 418: {"detail":"teapot","request_id":"req_…"}`,
`Request failed after 3 attempts: [Errno 61] Connection refused`,
`Unexpected error in keel_strategy_get: Expecting value: line 1 column 1`,
and a pydantic dump with `input_value=…` and an errors.pydantic.dev URL. The
codes stay as they were; only the words change.

Arms:

* one per source, at the translation layer it lives in (client, errors,
  adapter, backtest config);
* a WIRE sweep: every fault kind × the listed tools that reach keel-api, on
  the hosted listed server under the prod flags, every error message scanned
  for the raw markers — non-vacuous by count (every case must be an error).

Seeds (2026-10-01, each reverted by reversing the edit):
* `_extract_detail`'s list arm removed → the 422 arm and the sweep's 422
  cases red;
* the unmapped-status arm restored to `f"HTTP {status}: {body}"` → the 418
  arm and the sweep's 418 cases red.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import contextmanager

import httpx
import pytest
import respx
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import KeelError, translate_http_error


#: What a raw internal leaks as — none may appear in a message.
RAW = re.compile(
    r'\{"|request_id|req_[A-Za-z0-9]|Errno|Expecting value|input_value|pydantic\.dev'
    r"|\[type=|Traceback|<html",
    re.IGNORECASE,
)

FASTAPI_422 = {
    "detail": [
        {
            "type": "less_than_equal",
            "loc": ["query", "limit"],
            "msg": "Input should be less than or equal to 100",
            "input": "1000",
            "ctx": {"le": 100},
        }
    ]
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("keel.client.time.sleep", lambda _s: None)


def test_a_fastapi_422_list_reads_as_field_and_problem():
    err = translate_http_error(422, json.dumps(FASTAPI_422))
    assert str(err) == "`limit`: Input should be less than or equal to 100."
    assert err.error_code == "validation_failed"


def test_an_unmapped_status_names_no_body_or_request_id():
    err = translate_http_error(418, json.dumps({"detail": "teapot", "request_id": "req_TAINT9"}))
    assert str(err) == "Keel refused the request (HTTP 418): teapot"
    assert err.error_code == "error"
    html = translate_http_error(405, "<html><body>Method Not Allowed</body></html>")
    assert str(html) == "Keel refused the request (HTTP 405)."


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("refused", "Could not connect to Keel."),
        ("timeout", "Keel did not respond in time."),
        ("broken", "The connection to Keel failed before a response arrived."),
    ],
)
def test_a_transport_failure_reads_as_a_sentence(fault, expected):
    def side_effect(request):
        if fault == "refused":
            raise httpx.ConnectError("[Errno 61] Connection refused", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        raise httpx.RemoteProtocolError("peer closed connection", request=request)

    client = KeelClient(config=KeelConfig(api_key="k", api_url="https://api.test.io"))
    try:
        with respx.mock:
            respx.get("https://api.test.io/v1/me").mock(side_effect=side_effect)
            with pytest.raises(KeelError) as exc:
                client.get("/v1/me")
    finally:
        client.close()
    assert str(exc.value) == expected
    assert exc.value.error_code == "error" and exc.value.retryable is True


def test_a_2xx_that_is_not_json_is_a_plain_server_error():
    client = KeelClient(config=KeelConfig(api_key="k", api_url="https://api.test.io"))
    try:
        with respx.mock:
            respx.get("https://api.test.io/v1/me").mock(
                return_value=httpx.Response(200, text="not json")
            )
            with pytest.raises(KeelError) as exc:
                client.get("/v1/me")
    finally:
        client.close()
    assert exc.value.error_code == "server_error"
    assert not RAW.search(str(exc.value)) and "Expecting" not in str(exc.value)


# ── The wire sweep ────────────────────────────────────────────────────────

API = "https://api.plain.test"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
    "KEEL_CARD_META_MOVE",
    "KEEL_NONVIEW_TEXT_ONLY",
)


@contextmanager
def _hosted_listed_prod_flags():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(
        KEEL_SERVER_PROFILE="listed",
        KEEL_EXECUTION_MODE="hosted",
        KEEL_CARD_META_MOVE="1",
        KEEL_NONVIEW_TEXT_ONLY="1",
    )
    token = bind_request_credentials(token="tok_test", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


#: Listed tools whose first keel-api call the fault meets.
API_CALLS = {
    "keel_strategy_get": {"strategy_id": "str_x"},
    "keel_strategy_search": {},
    "keel_strategy_history": {"strategy_id": "str_x"},
    "keel_strategy_notes_read": {"strategy_id": "str_x"},
    "keel_backtest_summarize": {"backtest_id": "btr_x"},
    "keel_library_list": {},
    "keel_plan_usage": {},
}

FAULTS = {
    "422": lambda req: httpx.Response(422, json=FASTAPI_422),
    "418": lambda req: httpx.Response(418, json={"detail": "teapot", "request_id": "req_TAINT9"}),
    "refused": lambda req: (_ for _ in ()).throw(
        httpx.ConnectError("[Errno 61] Connection refused", request=req)
    ),
    "timeout": lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("read timed out", request=req)),
    "nonjson": lambda req: httpx.Response(200, text="not json"),
    "html502": lambda req: httpx.Response(502, text="<html>bad gateway</html>"),
}


def _message(result) -> str:
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("message"), str):
        return structured["message"]
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        text = structured["result"]
    envelope = json.loads(text[text.index("{") :])
    return envelope["message"]


def test_no_listed_error_message_carries_raw_internals():
    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        out = []
        async with Client(create_server()) as client:
            for fault, respond in FAULTS.items():
                for tool, args in API_CALLS.items():
                    with respx.mock(assert_all_called=False) as router:
                        router.route(host="api.plain.test").mock(side_effect=respond)
                        result = await client.call_tool(tool, args, raise_on_error=False)
                    out.append((fault, tool, result))
        return out

    with _hosted_listed_prod_flags():
        results = asyncio.run(run())
    errors = [(f, t, _message(r)) for f, t, r in results if r.is_error]
    # Non-vacuity: every (fault, tool) case really failed and was read.
    assert len(errors) == len(FAULTS) * len(API_CALLS) == len(results)
    leaks = [(f, t, m) for f, t, m in errors if RAW.search(m)]
    assert not leaks, leaks


def test_a_bad_backtest_config_names_fields_not_pydantic_internals():
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_backtest_run"].handler(
            {"strategy_id": "str_x", "config": {"init_cash": -1, "bogus": 1}},
            ToolContext(is_tty=False),
        )
    message = str(exc.value)
    assert exc.value.error_code == "invalid_backtest_config"
    assert "`init_cash`: Input should be greater than or equal to 0" in message
    assert "`bogus`: not a recognised field" in message
    assert not RAW.search(message), message


def test_an_unexpected_exception_is_not_echoed_to_the_caller():
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import _make_handler

    _bootstrap()
    tool = OUTCOMES["keel_help"]
    original = tool.handler

    def boom(args, ctx):
        raise KeyError("close")  # the audit's `KeyError 'close'` shape

    object.__setattr__(tool, "handler", boom)
    try:
        envelope = json.loads(_make_handler(tool, frozenset({"always"}))())
    finally:
        object.__setattr__(tool, "handler", original)
    assert envelope["code"] == "internal_error"
    assert envelope["message"] == "Unexpected error in keel_help; the call did not complete."
