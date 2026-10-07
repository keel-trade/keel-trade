"""Declared input bounds are real and enforced (Q-2270).

The bar from Anthropic's directory review: every documented input is honoured
exactly or refused with a clear message — never silently capped — and the
schema declares the real limits. Before Q-2270 the MCP adapter validated
argument TYPES only, so a declared `maximum` was a promise nothing kept
(`limit=500` on `keel_backtest_positions` reached the handler and was clamped
to 100), and an undeclared keel-api bound came back as a raw 422.

What this file holds, on the hosted listed server:

* every listed parameter keel-api bounds declares that bound, exactly
  (`API_BOUNDS`, each row citing the keel-api line it mirrors);
* EVERY declared numeric / length / item bound on EVERY listed tool refuses a
  value just past it, before any keel-api call, with the parameter named;
* the edge value itself goes through unchanged over the real FastMCP wire;
* every registered handler is wrapped (the enforcement cannot be bypassed by
  registering a tool).

Seeds (run 2026-10-01, each reverted by reversing the edit):
* drop the `wrap_handler` call from `OutcomeTool.__post_init__` — 6 red: the
  sweep, the three wire refusals, the restore refusal and the wrapped-handler
  census; the API-bound table and the live-monitor per-view cap (the
  handler's own check) stay green — the control arms;
* delete `"maximum": 100` from `keel_strategy_search.limit` — 2 red: the
  API-bound table and that tool's wire refusal; every other arm green.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._declared_bounds import bound_violation


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)

#: `(tool, parameter) → {bound: value}` — keel-api's own bound on what the
#: listed tool forwards. The schema must declare exactly this.
API_BOUNDS: dict[tuple[str, str], dict[str, int]] = {
    # routers/strategies.py list_strategies: Query(20, ge=1, le=100)
    ("keel_strategy_search", "limit"): {"minimum": 1, "maximum": 100},
    # routers/strategies.py list_versions: Query(50, ge=1, le=200)
    ("keel_strategy_history", "limit"): {"minimum": 1, "maximum": 200},
    # routers/strategies.py list_strategy_memory: Query(10, ge=1, le=100)
    ("keel_strategy_notes_read", "limit"): {"minimum": 1, "maximum": 100},
    # schemas/strategy_memory.py StrategyMemoryWriteRequest.note
    ("keel_strategy_notes_add", "note"): {"minLength": 1, "maxLength": 8192},
    # schemas/strategies.py RestoreRequest.message
    ("keel_strategy_restore", "message"): {"maxLength": 200},
    # schemas/strategies.py CreateStrategyRequest.name / UpdateStrategyRequest
    ("keel_strategy_compose", "name"): {"minLength": 1, "maxLength": 255},
    # schemas/strategies.py UpdateStrategyRequest.parent_version
    ("keel_strategy_compose", "parent_version"): {"minLength": 1, "maxLength": 200},
    # schemas/sharing.py ForkStrategyRequest.name
    ("keel_strategy_fork", "name"): {"minLength": 1, "maxLength": 255},
    # schemas/library.py LibraryForkRequest.name
    ("keel_library_fork", "name"): {"minLength": 1, "maxLength": 255},
    # schemas/backtests.py SubmitBacktestRequest.version
    ("keel_backtest_run", "version"): {"minLength": 1, "maxLength": 200},
    # routers/backtests.py list trades: Query(ge=1, le=_TRADES_MAX_LIMIT=100)
    ("keel_backtest_positions", "limit"): {"minimum": 1, "maximum": 100},
    # routers/backtests.py get_backtest_slice: capital Query(gt=0)
    ("keel_backtest_summarize", "capital"): {"exclusiveMinimum": 0},
    # routers/live.py: orders/trades/executions le=200, weights/history le=5000
    # (the schema declares the widest; the per-view cap is the handler's)
    ("keel_live_monitor", "limit"): {"minimum": 1, "maximum": 5000},
}

_BOUND_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
)


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
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


def _listed_tools() -> dict[str, dict]:
    """`{tool name: published input schema}` of the hosted listed server."""
    from keel.mcp.server import create_server

    with _hosted_listed():
        tools = asyncio.run(create_server().list_tools())
    return {t.name: t.parameters for t in tools}


def _past(spec: dict):
    """A value just outside the FIRST bound `spec` declares."""
    if "maximum" in spec:
        return spec["maximum"] + 1
    if "minimum" in spec:
        return spec["minimum"] - 1
    if "exclusiveMinimum" in spec:
        return spec["exclusiveMinimum"]
    if "exclusiveMaximum" in spec:
        return spec["exclusiveMaximum"]
    if "maxLength" in spec:
        return "x" * (spec["maxLength"] + 1)
    if "minLength" in spec:
        return "" if spec["minLength"] == 1 else "x" * (spec["minLength"] - 1)
    if "maxItems" in spec:
        return ["BTC"] * (spec["maxItems"] + 1)
    if "minItems" in spec:
        return []
    raise AssertionError(spec)


def _bounded(schemas: dict[str, dict]) -> list[tuple[str, str, dict]]:
    return [
        (tool, name, spec)
        for tool, schema in sorted(schemas.items())
        for name, spec in sorted((schema.get("properties") or {}).items())
        if any(k in spec for k in _BOUND_KEYS)
    ]


def test_every_api_bounded_listed_parameter_declares_exactly_that_bound():
    schemas = _listed_tools()
    assert len(API_BOUNDS) >= 13  # non-vacuous: the table is the sweep's result
    for (tool, param), bound in API_BOUNDS.items():
        spec = schemas[tool]["properties"][param]
        declared = {k: spec[k] for k in _BOUND_KEYS if k in spec}
        assert declared == bound, f"{tool}.{param}: declares {declared}, keel-api bounds {bound}"


def test_every_declared_bound_on_every_listed_tool_refuses_before_any_api_call():
    schemas = _listed_tools()
    bounded = _bounded(schemas)
    # Non-vacuous: the listed surface carries bounds on many tools, and the
    # API table's rows are among them.
    assert len({tool for tool, _, _ in bounded}) >= 10, bounded
    assert set(API_BOUNDS) <= {(tool, name) for tool, name, _ in bounded}

    calls: list = []

    def record(self, path, *a, **kw):
        calls.append(path)
        return {}

    refused = 0
    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.get", record),
        mock.patch("keel.client.KeelClient.post", record),
        mock.patch("keel.client.KeelClient.patch", record),
    ):
        from keel.errors import KeelError
        from keel.tools.outcomes._base import ToolContext

        for tool, name, spec in bounded:
            value = _past(spec)
            assert bound_violation(name, spec, value) is not None, (tool, name)
            with pytest.raises(KeelError) as exc:
                OUTCOMES[tool].handler({name: value}, ToolContext())
            assert exc.value.error_code == "argument_out_of_range", (tool, name, exc.value)
            assert f"`{name}`" in str(exc.value)
            refused += 1
    assert refused == len(bounded)
    assert calls == []  # nothing reached keel-api


def _wire(tool: str, args: dict, *, get=None, post=None) -> tuple[list, dict]:
    calls: list = []

    def fget(self, path, **params):
        calls.append(("GET", path, params))
        return get(path, params) if get else {}

    def fpost(self, path, json=None, **_):
        calls.append(("POST", path, json))
        return post(path, json) if post else {}

    async def call():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.get", fget),
        mock.patch("keel.client.KeelClient.post", fpost),
    ):
        result = asyncio.run(call())
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    try:
        body = json.loads(text)
    except ValueError:
        body = {"_text": text}
    return calls, body


@pytest.mark.parametrize(
    ("tool", "base", "param", "edge", "past"),
    [
        ("keel_strategy_search", {}, "limit", 100, 150),
        ("keel_strategy_notes_read", {"strategy_id": "str_a"}, "limit", 100, 101),
        ("keel_strategy_history", {"strategy_id": "str_a"}, "limit", 200, 201),
    ],
)
def test_the_edge_goes_through_and_one_past_it_is_refused_over_the_wire(
    tool, base, param, edge, past
):
    calls, body = _wire(tool, {**base, param: past})
    assert calls == [], calls
    assert body.get("code") == "argument_out_of_range", body
    assert str(past) in body.get("message", "")

    calls, body = _wire(tool, {**base, param: edge}, get=lambda p, q: [])
    assert calls, body  # control: the edge value reached keel-api …
    assert calls[0][2][param] == edge  # … exactly as asked


def test_live_monitor_refuses_a_limit_past_the_views_own_cap():
    """Orders, trades and executions stop at 200; weights-history at 5000."""
    calls, body = _wire(
        "keel_live_monitor", {"deployment_id": "dep_1", "view": "trade_history", "limit": 300}
    )
    assert calls == [] and body.get("code") == "argument_out_of_range", body
    assert "trade_history" in body["message"] and "200" in body["message"]

    calls, body = _wire(
        "keel_live_monitor",
        {"deployment_id": "dep_1", "view": "weights-history", "limit": 300},
        get=lambda p, q: {"snapshots": []},
    )
    assert [(c[1], c[2]) for c in calls] == [
        ("/v1/deployments/dep_1/weights/history", {"limit": 300})
    ]


def test_restore_message_past_200_characters_is_refused_before_anything_is_written():
    calls, body = _wire(
        "keel_strategy_restore", {"strategy_id": "str_a", "ref": "3", "message": "m" * 201}
    )
    # A view tool's error leads with its message line (the text block), so
    # the code is read from the whole text.
    assert calls == [] and "argument_out_of_range" in json.dumps(body), body


def test_every_registered_handler_enforces_its_declared_bounds():
    assert len(OUTCOMES) >= 40  # non-vacuous: the whole registry
    unwrapped = [
        n for n, t in OUTCOMES.items() if not getattr(t.handler, "__keel_bounds_enforced__", False)
    ]
    assert unwrapped == []
