"""Every listed tool's failure sets MCP `isError` (Q-2273 B1).

A Keel tool RETURNS its failures as the spec §13.5 envelope (Q-1731) — it
never raises, because FastMCP's raise path renders only `str(exc)` and drops
the `structuredContent` a refusal card draws from (Q-1785). Until Q-2273 the
returned envelope also left `isError: false`, so a host could not tell a 404
from a success without parsing prose (0 of 96 error results in the round-5
audit set it).

Arms, each over the REAL FastMCP wire on the hosted listed server, under the
prod flags (`KEEL_CARD_META_MOVE=1`, `KEEL_NONVIEW_TEXT_ONLY=1`) and with
both flags off — together every registration mode (`view`, `live`, `text`,
`legacy`, `status_wrapped`) is driven:

* HANDLER errors: every listed tool provoked into a KeelError its handler
  raises (an empty required id, an unknown topic, a keel-api 404) →
  `isError: true`, the envelope's `code` unchanged in the result;
* VALIDATION errors: every listed tool called with an argument it does not
  declare (FastMCP's own argument validation, wrapped by
  `_wrap_fastmcp_validation_errors`) → `isError: true`, `usage_error`;
* CONTROL: successes on the same server, same modes → `isError: false`;
* NON-VACUITY: the handler arm covers every listed tool but the one whose
  contract is never to fail, by name.

Seed (run 2026-10-01, reverted by reversing the edit): `_flag_error`
returning `result` unchanged → every HANDLER and VALIDATION arm red in both
flag states, the CONTROL arm green.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.errors import NotFoundError
from keel.tools.outcomes import _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
    "KEEL_CARD_META_MOVE",
    "KEEL_NONVIEW_TEXT_ONLY",
)

#: One call per listed tool that its HANDLER refuses. Ids are empty (the
#: tool's own `missing_*` refusal) wherever the tool takes one; the rest are
#: refused by the faked keel-api's 404 or by the input itself.
HANDLER_ERRORS: dict[str, dict] = {
    "keel_app_link": {"id": "hello world"},
    "keel_backtest_compare": {"backtest_ids": []},
    "keel_backtest_positions": {"backtest_id": ""},
    "keel_backtest_run": {"strategy_id": ""},
    "keel_backtest_summarize": {"backtest_id": ""},
    "keel_backtest_watch": {"backtest_id": ""},
    "keel_components_get": {"name": ""},
    "keel_components_get_many": {"names": []},
    "keel_components_search": {"clock_direction": "sideways"},
    "keel_connection_check": {},
    "keel_feedback": {"text": "   "},
    "keel_help": {"topic": "no_such_topic_q2273"},
    "keel_library_fork": {"slug": ""},
    "keel_library_get": {"slug": ""},
    "keel_library_list": {},
    "keel_live_monitor": {"deployment_id": "dep_q2273"},
    "keel_plan_usage": {},
    "keel_share_create": {"target_id": ""},
    "keel_strategy_compose": {"source": ""},
    "keel_strategy_diff": {"strategy_id": "str_x", "ref_a": "", "ref_b": ""},
    "keel_strategy_fork": {"source": ""},
    "keel_strategy_get": {"strategy_id": ""},
    "keel_strategy_history": {"strategy_id": ""},
    "keel_strategy_notes_add": {"strategy_id": "str_x", "note": ""},
    "keel_strategy_notes_read": {"strategy_id": ""},
    "keel_strategy_readiness": {"strategy_id": ""},
    "keel_strategy_restore": {"strategy_id": "", "ref": "2"},
    "keel_strategy_search": {},
}

#: Listed tools with no handler failure to provoke, and why.
NEVER_FAILS: dict[str, str] = {
    # Spec 02 §2.5: the status card degrades ("Identity check failed: …")
    # rather than refusing — it is the first call of a session.
    "keel_account_status": "degrades to a status card, never an error",
}

#: Successes on the same server — the control arm.
SUCCESSES: dict[str, dict] = {
    "keel_help": {},
    "keel_app_link": {"id": "str_q2273"},
    "keel_components_search": {"query": "momentum"},
}


@contextmanager
def _hosted_listed(flags_on: bool):
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    if flags_on:
        os.environ.update(KEEL_CARD_META_MOVE="1", KEEL_NONVIEW_TEXT_ONLY="1")
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


def _not_found(self, method, path, **_kw):
    raise NotFoundError(f"Nothing at {path}.")


def _call_all(calls: list[tuple[str, dict]], flags_on: bool) -> list[tuple[str, object]]:
    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        out = []
        async with Client(create_server()) as client:
            for name, args in calls:
                out.append((name, await client.call_tool(name, args, raise_on_error=False)))
        return out

    with _hosted_listed(flags_on), mock.patch("keel.client.KeelClient._request", _not_found):
        return asyncio.run(run())


def _envelope(result) -> dict:
    """The §13.5 envelope a failed result carries: `structuredContent` (a view
    tool), the `{"result": text}` wrapper, or the JSON at the end of the text."""
    structured = result.structured_content
    if isinstance(structured, dict) and "code" in structured:
        return structured
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return json.loads(structured["result"])
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text[text.index("{") :] if not text.lstrip().startswith("{") else text)


def test_the_handler_table_covers_every_listed_tool():
    """Non-vacuity: no listed tool escapes the arm unnamed."""
    assert set(HANDLER_ERRORS) | set(NEVER_FAILS) == set(LISTED_PROFILE_TOOLS)
    assert len(HANDLER_ERRORS) == len(LISTED_PROFILE_TOOLS) - len(NEVER_FAILS) > 20


@pytest.mark.parametrize("flags_on", [True, False], ids=["prod-flags", "flags-off"])
def test_every_handler_error_sets_is_error(flags_on):
    results = _call_all(sorted(HANDLER_ERRORS.items()), flags_on)
    wrong = []
    for name, result in results:
        env = _envelope(result)
        if not (result.is_error and isinstance(env.get("code"), str)):
            wrong.append((name, result.is_error, env.get("code")))
    assert not wrong, wrong
    assert len(results) == len(HANDLER_ERRORS)


@pytest.mark.parametrize("flags_on", [True, False], ids=["prod-flags", "flags-off"])
def test_every_argument_validation_error_sets_is_error(flags_on):
    calls = [(name, {"not_an_argument_q2273": 1}) for name in sorted(LISTED_PROFILE_TOOLS)]
    results = _call_all(calls, flags_on)
    wrong = [
        (name, result.is_error, _envelope(result).get("code"))
        for name, result in results
        if not (result.is_error and _envelope(result).get("code") == "usage_error")
    ]
    assert not wrong, wrong
    assert len(results) == len(LISTED_PROFILE_TOOLS)


@pytest.mark.parametrize("flags_on", [True, False], ids=["prod-flags", "flags-off"])
def test_a_success_does_not_set_is_error(flags_on):
    results = _call_all(sorted(SUCCESSES.items()), flags_on)
    assert [(n, r.is_error) for n, r in results] == [(n, False) for n in sorted(SUCCESSES)]
