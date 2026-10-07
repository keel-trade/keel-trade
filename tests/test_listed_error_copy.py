"""Listed tools' error and suggestion text names only listed tools (Q-2273 L4).

Q-2268 gated the listed result BODIES and swept them by driving happy paths,
so error paths were never read: the round-5 audit found `keel backtest
compare …`, `keel components compose-help RSI`, `keel strategy log …`,
`keel ownership status …` and `keel_strategy_workspaces` in listed errors,
and the compose dry run's unknown-component suggestion named a chat tool and
a CLI verb. A listed host has neither: every such name sends the model to a
call it cannot make.

The sweep drives every listed tool over the REAL FastMCP wire on the hosted
listed server (prod flags) through a matrix of error-provoking calls — no
arguments, each required string empty and blank, each free-text string junk,
each enum value bogus, and every keel-api fault (400 / 401 / 403 / 404 /
409 / 422 / 429 / 500 / a coded 409 / transport) on a minimal valid call —
plus the compose dry run of an unknown component, and scans EVERY string of
every result (text blocks and structured content) for:

* a CLI form — ``keel <verb>`` in lower case;
* a tool name the listed server does not register — any ``keel_*`` token
  outside `LISTED_PROFILE_TOOLS`, and chat-api's ``strategy_components_*``.

Non-vacuity: every listed tool is driven, the matrix yields > 400 results of
which > 250 are errors, and the scanner itself is shown to fire on a planted
string (the unit arm below).

Seed (2026-10-01, reverted by reversing the edit): compare's arity
suggestion restored to "Usage: `keel backtest compare <backtest_id> …`" →
the sweep red on `keel_backtest_compare [array:backtest_ids=[]]: keel
backtest compare`, the scanner and non-vacuity arms green.
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
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, TOOL_ALIASES


_bootstrap()

API = "https://api.copy.test"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
    "KEEL_CARD_META_MOVE",
    "KEEL_NONVIEW_TEXT_ONLY",
)

#: `keel <verb>` — the CLI's shape. Lower-case `keel` followed by a verb; the
#: product name in prose is capitalised ("the Keel web app").
CLI_FORM = re.compile(r"(?<![\w/.@-])keel(?: [a-z][a-z-]+\b)+")
TOOL_TOKEN = re.compile(r"\b(?:keel_[a-z][a-z0-9_]*|strategy_components_[a-z_]+)\b")
#: `keel_*` tokens that are not tool names.
NOT_TOOLS = frozenset({"keel_trade"})

#: A minimal valid call per listed tool (the fault arms run on these).
MIN_ARGS: dict[str, dict] = {
    "keel_account_status": {},
    "keel_app_link": {"id": "str_q2273"},
    "keel_backtest_compare": {"backtest_ids": ["btr_a", "btr_b"]},
    "keel_backtest_positions": {"backtest_id": "btr_a"},
    "keel_backtest_run": {"strategy_id": "str_x", "wait": False},
    "keel_backtest_summarize": {"backtest_id": "btr_a"},
    "keel_backtest_watch": {"backtest_id": "btr_a", "timeout_s": 0},
    "keel_components_get": {"name": "ROC"},
    "keel_components_get_many": {"names": ["ROC", "NoSuchComponentQ2273"]},
    "keel_components_search": {"query": "momentum"},
    "keel_connection_check": {},
    "keel_feedback": {"text": "q2273"},
    "keel_help": {"topic": "dsl"},
    "keel_library_fork": {"slug": "ma-crossover-crypto"},
    "keel_library_get": {"slug": "ma-crossover-crypto"},
    "keel_library_list": {},
    "keel_live_monitor": {"deployment_id": "dep_1"},
    "keel_plan_usage": {},
    "keel_share_create": {"target_id": "str_x"},
    "keel_strategy_compose": {
        "source": 'Globals(target_timeframe="1d")\nUniverse(assets=["BTC"])\n'
        "Execution()\nPipeline([ROC(period=20)])\n",
        "name": "q2273",
    },
    "keel_strategy_diff": {"strategy_id": "str_x", "ref_a": "2", "ref_b": "3"},
    "keel_strategy_fork": {"source": "str_x"},
    "keel_strategy_get": {"strategy_id": "str_x"},
    "keel_strategy_history": {"strategy_id": "str_x"},
    "keel_strategy_notes_add": {"strategy_id": "str_x", "note": "q2273"},
    "keel_strategy_notes_read": {"strategy_id": "str_x"},
    "keel_strategy_readiness": {"strategy_id": "str_x"},
    "keel_strategy_restore": {"strategy_id": "str_x", "ref": "2"},
    "keel_strategy_search": {},
}

_QUOTA_403 = {
    "title": "Forbidden",
    "status": 403,
    "detail": "Backtest limit reached for this period.",
    "code": "quota_exhausted",
    "quota": {
        "unit": "backtest_runs",
        "label": "backtest runs",
        "limit": 5,
        "used": 5,
        "remaining": 0,
        "resets_at": "2026-11-01T00:00:00Z",
    },
}

FAULTS: dict[str, object] = {
    "400": (400, {"detail": "bad input"}),
    "401": (401, {"detail": "expired"}),
    "403": (403, {"detail": "You do not have access to this strategy"}),
    "403-quota": (403, _QUOTA_403),
    "404": (404, {"detail": "Not found"}),
    "409": (409, {"detail": "Conflict — resource changed"}),
    "409-coded": (409, {"title": "Conflict", "code": "PARENT_VERSION_NOT_HEAD", "detail": "x"}),
    "422": (422, {"detail": [{"loc": ["body", "name"], "msg": "field required"}]}),
    "429": (429, {"detail": "slow down"}),
    "500": (500, {"detail": "boom"}),
    "connect": "CONNECT",
}


def _calls() -> list[tuple[str, str, dict, object]]:
    """(case, tool, args, fault|None) — the whole matrix."""
    from keel.tools.outcomes._mcp_adapter import effective_input_schema

    out: list[tuple[str, str, dict, object]] = []
    for name in sorted(LISTED_PROFILE_TOOLS):
        schema = effective_input_schema(OUTCOMES[name])
        props = schema.get("properties", {})
        base = MIN_ARGS[name]
        out.append(("empty", name, {}, None))
        for key, spec in props.items():
            if spec.get("type") == "array":
                out.append((f"array:{key}=[]", name, {**base, key: []}, None))
                continue
            if spec.get("type") != "string":
                continue
            if "enum" in spec:
                out.append((f"enum:{key}", name, {**base, key: "not_a_value_q2273"}, None))
                continue
            for value in ("", "   ", "q2273-junk/../x"):
                out.append((f"str:{key}={value!r}", name, {**base, key: value}, None))
        for fault_name, fault in FAULTS.items():
            out.append((f"fault:{fault_name}", name, base, fault))
    out.append(
        (
            "dry-run-unknown",
            "keel_strategy_compose",
            {"source": "Pipeline([NoSuchComponentQ2273(period=3)])", "dry_run": True},
            "COMPILE_OK",
        )
    )
    return out


@contextmanager
def _hosted_listed():
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


def _responder(fault):
    def respond(request):
        if fault == "CONNECT":
            raise httpx.ConnectError("refused", request=request)
        if fault == "COMPILE_OK":
            # keel-api's compile/parse answers, so the dry run reaches the
            # local validator's issue list (where the suggestion lives).
            if request.url.path.endswith("/compile"):
                return httpx.Response(200, json={"compiled": True})
            return httpx.Response(200, json={"graph": {"nodes": []}})
        if fault is None:
            return httpx.Response(404, json={"detail": "Not found"})
        status, body = fault
        return httpx.Response(status, json=body)

    return respond


def _strings(result) -> str:
    texts = [getattr(c, "text", "") or "" for c in result.content]
    texts.append(json.dumps(result.structured_content or {}, default=str))
    return "\n".join(texts)


def findings(text: str) -> list[str]:
    """Every CLI form and unlisted tool name in ``text``."""
    hits = [m.group(0) for m in CLI_FORM.finditer(text)]
    hits += [
        t for t in TOOL_TOKEN.findall(text) if t not in LISTED_PROFILE_TOOLS and t not in NOT_TOOLS
    ]
    return hits


def test_the_scanner_fires_on_each_form():
    """Non-vacuity of the scanner: each form it exists to catch is caught,
    and the listed names and prose it must pass are passed."""
    assert findings("Usage: `keel backtest compare <a> <b>`") == ["keel backtest compare"]
    assert findings("find ids via `keel_strategy_workspaces`") == ["keel_strategy_workspaces"]
    assert findings("Use `strategy_components_search` (chat)") == ["strategy_components_search"]
    assert set(TOOL_ALIASES) - set(LISTED_PROFILE_TOOLS), "old names are not listed"
    old = next(iter(TOOL_ALIASES))
    assert findings(f"call {old}") == [old]
    assert findings("Open it in the Keel web app; `keel_strategy_search` lists ids.") == []


@pytest.fixture(scope="module")
def sweep():
    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        out = []
        async with Client(create_server()) as client:
            for case, name, args, fault in _calls():
                with respx.mock(assert_all_called=False) as router:
                    router.route(host="api.copy.test").mock(side_effect=_responder(fault))
                    result = await client.call_tool(name, args, raise_on_error=False)
                out.append((case, name, result))
        return out

    import keel.client

    original_sleep = keel.client.time.sleep
    keel.client.time.sleep = lambda _s: None
    try:
        with _hosted_listed():
            return asyncio.run(run())
    finally:
        keel.client.time.sleep = original_sleep


def test_the_sweep_drives_every_listed_tool_through_its_error_paths(sweep):
    assert {name for _, name, _ in sweep} == set(LISTED_PROFILE_TOOLS)
    errors = [r for _, _, r in sweep if r.is_error]
    assert len(sweep) > 400 and len(errors) > 250, (len(sweep), len(errors))


def test_no_listed_result_names_the_cli_or_an_unlisted_tool(sweep):
    leaks = sorted(
        {(name, case, hit) for case, name, result in sweep for hit in findings(_strings(result))}
    )
    assert not leaks, "\n".join(f"{n} [{c}]: {h}" for n, c, h in leaks)
