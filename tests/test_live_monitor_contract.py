"""`keel_live_monitor` answers the view it was asked for, or refuses (Q-2270).

Before Q-2270 an explicit per-deployment view with no `deployment_id`
(`view="positions"`) fell into the portfolio branch and came back as
`view: portfolio`; the `missing_deployment_id` refusal below it was
unreachable. Driven over the real FastMCP wire on the hosted listed server,
`KeelClient.get` faked.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* restore the old branch condition (`view == "portfolio" or deployment_id in
  ("", "all")`) — the refusal arms red (the portfolio is read instead); the
  no-argument and explicit-portfolio controls stay green.
* delete the `_TRADE_SORTS` refusal loop from `_handler` — the two unknown
  sort arms red (the value is forwarded); the known-value arm stays green.

Trade-history `sort_by` / `sort_dir` are closed vocabularies (keel-api's
`list_trades` sorted an unknown value by time, descending, without a word):
declared as enums and refused here when unknown.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from keel.tools.outcomes import _bootstrap


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
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


def _monitor(args: dict) -> tuple[list, str]:
    calls: list = []

    def get(self, path, **params):
        calls.append((path, params))
        return {"items": [], "data": [], "points": []}

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool("keel_live_monitor", args, raise_on_error=False)

    with _hosted_listed(), mock.patch("keel.client.KeelClient.get", get):
        result = asyncio.run(run())
    return calls, "\n".join(getattr(c, "text", "") for c in result.content)


@pytest.mark.parametrize(
    "args",
    [
        {"view": "positions"},
        {"view": "trade_history"},
        {"view": "overview"},
        {"view": "positions", "deployment_id": "all"},
    ],
)
def test_a_per_deployment_view_without_a_deployment_is_refused(args):
    calls, text = _monitor(args)
    assert calls == [], calls  # nothing read, the portfolio least of all
    assert "missing_deployment_id" in text, text
    assert args["view"] in text


@pytest.mark.parametrize("args", [{}, {"view": "portfolio"}, {"deployment_id": "all"}])
def test_the_portfolio_answers_no_arguments_or_the_portfolio_view(args):
    """Control: the documented portfolio cases still read the summary."""
    calls, text = _monitor(args)
    assert [c[0] for c in calls] == ["/v1/deployments/portfolio/summary"]
    assert json.loads(text)["view"] == "portfolio"


def test_a_per_deployment_view_with_a_deployment_reads_that_view():
    calls, text = _monitor({"view": "positions", "deployment_id": "dep_1"})
    assert [c[0] for c in calls] == ["/v1/deployments/dep_1/positions"]
    assert json.loads(text)["view"] == "positions"


@pytest.mark.parametrize("bad", [{"sort_by": "pnl"}, {"sort_dir": "sideways"}])
def test_an_unknown_trade_sort_is_refused_before_anything_is_read(bad):
    calls, text = _monitor({"deployment_id": "dep_1", "view": "trade_history", **bad})
    assert calls == [], calls
    # The published-enum front door (round 5) refuses it as a usage_error.
    assert "usage_error" in text and next(iter(bad.values())) in text


def test_the_declared_trade_sorts_are_forwarded_exactly():
    calls, _ = _monitor(
        {
            "deployment_id": "dep_1",
            "view": "trade_history",
            "sort_by": "closed_pnl",
            "sort_dir": "asc",
        }
    )
    assert calls == [("/v1/deployments/dep_1/trades", {"sort_by": "closed_pnl", "sort_dir": "asc"})]


def test_the_trade_sorts_are_declared_enums_on_the_listed_schema():
    from keel.mcp.server import create_server

    with _hosted_listed():
        tools = asyncio.run(create_server().list_tools())
    props = next(t for t in tools if t.name == "keel_live_monitor").parameters["properties"]
    assert props["sort_by"]["enum"] == ["trade_time", "notional", "closed_pnl"]
    assert props["sort_dir"]["enum"] == ["asc", "desc"]
