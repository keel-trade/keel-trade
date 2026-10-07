"""A backtest result says what capital and costs its numbers were computed at (Q-2270).

`keel_backtest_run` takes `config.init_cash` / `fees` / `slippage` and every
net figure moves with them, yet no run, summarize, watch or compare result
said what they were. `cost_model` is now the effective model (keel-api's
sparse `backtest_config` over `BacktestConfig`'s defaults) with a plain
`costs:` line. Driven over the real FastMCP wire on the hosted listed
server, the Keel API faked at `KeelClient.post` / `.get`.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* delete `extra["cost_model"] = cost_model` from `attach_run_facts` — the
  run, defaults and summarize arms red; the compare arms stay green (compare
  reads `cost_model_block` per run itself).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

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


def _detail(run_id: str, overrides: dict | None) -> dict:
    """A completed run as keel-api serves it: `backtest_config` is the sparse
    overrides the caller set (absent key = default)."""
    out = {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": "str_x",
        "strategy_name": "Momentum",
        "commit_id": "cmt_1",
        "sequence_number": 1,
        "start_date": "2026-01-01",
        "end_date": "2026-09-01",
        "metrics": {"sharpe_ratio": 1.2, "total_return": 0.1, "max_drawdown": -0.05},
        "completed_at": "2026-09-02T00:00:00Z",
    }
    if overrides is not None:
        out["backtest_config"] = overrides
    return out


def _call(tool: str, args: dict, details: dict[str, dict]) -> tuple[list, dict, str]:
    posts: list = []

    def post(self, path, json=None, **_):
        posts.append((path, json))
        cfg = (json or {}).get("backtest_config")
        return {**_detail("btr_new", cfg), "status": "QUEUED", "metrics": None}

    def get(self, path, **params):
        if path.endswith("/curve"):
            return {"points": [], "source_points": 0}
        if path.endswith("/results"):
            return {"presigned_url": "u", "expires_in": 1}
        if path.startswith("/v1/backtests/"):
            return details[path.split("/")[3]]
        if "/versions/" in path:
            return {"source": "Pipeline([])"}
        return {}

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", get),
    ):
        result = asyncio.run(run())
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    body = result.structured_content if isinstance(result.structured_content, dict) else {}
    if isinstance(body.get("result"), str):
        body = json.loads(body["result"])
    return posts, body, text


def test_a_submitted_run_echoes_the_capital_and_costs_it_was_given():
    posts, body, text = _call(
        "keel_backtest_run",
        {
            "strategy_id": "str_x",
            "config": {"init_cash": 5000, "fees": 0.001},
            "wait": False,
            "skip_readiness": True,
        },
        {},
    )
    sent = next(b for p, b in posts if p == "/v1/backtests")
    assert sent["backtest_config"] == {"init_cash": 5000.0, "fees": 0.001}
    model = body["cost_model"]
    assert (model["init_cash"], model["fees_bps"], model["slippage_bps"]) == (5000.0, 10.0, 4.5)
    assert model["set_for_run"] == ["init_cash", "fees"]
    assert (
        "costs: $5,000 starting capital · fees 10 bps and slippage 4.5 bps per fill "
        "(set for this run: capital, fees)"
    ) in text


def test_a_run_with_no_config_names_the_platform_defaults():
    _, body, text = _call(
        "keel_backtest_run",
        {"strategy_id": "str_x", "wait": False, "skip_readiness": True},
        {},
    )
    assert body["cost_model"]["set_for_run"] == []
    assert "costs: $10,000 starting capital" in text and "(platform defaults)" in text


def test_summarize_names_the_runs_cost_model():
    _, body, text = _call(
        "keel_backtest_summarize",
        {"backtest_id": "btr_a"},
        {"btr_a": _detail("btr_a", {"slippage": 0.002})},
    )
    assert body["cost_model"]["slippage_bps"] == 20.0
    assert "slippage 20 bps per fill (set for this run: slippage)" in text


def test_compare_names_one_cost_model_when_every_run_shares_it():
    details = {r: _detail(r, {"init_cash": 2500}) for r in ("btr_a", "btr_b")}
    _, body, text = _call("keel_backtest_compare", {"backtest_ids": ["btr_a", "btr_b"]}, details)
    assert body["cost_model"]["init_cash"] == 2500.0
    assert "costs: $2,500 starting capital" in text


def test_compare_with_different_costs_names_each_runs_and_no_shared_one():
    details = {"btr_a": _detail("btr_a", None), "btr_b": _detail("btr_b", {"fees": 0.002})}
    _, body, text = _call("keel_backtest_compare", {"backtest_ids": ["btr_a", "btr_b"]}, details)
    assert "cost_model" not in body
    runs = body["view"]["runs"]
    assert [r["cost_model"]["fees_bps"] for r in runs] == [4.5, 20.0]
    assert "settings differ" in text  # the existing warning names the difference
