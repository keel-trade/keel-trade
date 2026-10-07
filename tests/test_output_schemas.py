"""The declared output schemas of the view tools (Q-1788).

ChatGPT's developer mode flagged "OUTPUT SCHEMA RECOMMENDED" on the ten
view tools — the only listed tools that published no `outputSchema`. They
now declare one (`keel/tools/outcomes/_output_schemas.py`). A schema that
rejects real output is worse than none (a strict client drops the
result), so this file validates REAL envelopes against it: every view
tool's two real error paths through the real server, success envelopes
from the real `keel_backtest_summarize` / `keel_backtest_compare` handlers
(HTTP faked), a backtest view at every status, and strategy views built
from the card fixtures' real graphs.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the
commit. Proof it is not vacuous: the envelope counts are asserted per
tool, and the schema set is pinned to exactly `VIEW_TOOLS`.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from unittest.mock import patch

import jsonschema
import pytest
from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS
from keel.tools.outcomes._strategy_view import VIEW_TOOLS


CARD_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "cards"

_METRICS = {
    "sharpe_ratio": 1.8,
    "total_return": 42.5,
    "max_drawdown": 12.0,
    "turnover": 12.0,
    "total_trades": 87,
    "win_rate": 51.0,
    "sortino_ratio": 2.1,
    "calmar_ratio": 1.4,
    "profit_factor": 1.3,
    "total_fees_paid": 55.0,
    "funding_attribution": -3.1,
    "sharpe_ratio_active": 1.7,
    "warmup_bars": 30,
}


def _detail(run_id: str, status: str = "COMPLETED", **extra) -> dict:
    return {
        "id": run_id,
        "status": status,
        "strategy_id": "str_mom",
        "strategy_name": "Momentum",
        "sequence_number": 2,
        "start_date": "2024-08-15",
        "end_date": "2026-02-27",
        "metrics": _METRICS if status == "COMPLETED" else None,
        **extra,
    }


def _validate(tool: str, envelope: dict) -> None:
    jsonschema.validate(envelope, OUTPUT_SCHEMAS[tool])


def _listed_tools() -> list:
    saved = {k: os.environ.get(k) for k in ("KEEL_SERVER_PROFILE", "KEEL_TOOLSETS")}
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ.pop("KEEL_TOOLSETS", None)
    try:
        from keel.mcp.server import create_server

        return asyncio.run(create_server().list_tools())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_schema_set_is_exactly_the_view_tools() -> None:
    assert set(OUTPUT_SCHEMAS) == VIEW_TOOLS
    for name, schema in OUTPUT_SCHEMAS.items():
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["type"] == "object", name


def test_every_listed_tool_publishes_an_output_schema() -> None:
    """What ChatGPT flagged: a listed tool with no outputSchema. Every one
    now publishes one — the declared envelope schema for a view tool,
    FastMCP's `{"result": …}` wrapper for the rest."""
    # SEED: in `_mcp_adapter.register_all`, drop the `output_schema=`
    # kwarg — the ten view tools publish none and this reds.
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    tools = _listed_tools()
    assert {t.name for t in tools} == LISTED_PROFILE_TOOLS  # not vacuous
    missing = [t.name for t in tools if not t.to_mcp_tool().outputSchema]
    assert not missing, f"listed tools with no outputSchema: {missing}"
    view = [t for t in tools if t.name in VIEW_TOOLS]
    # Every view tool is on the listed surface (restore joined, spec 03 §2.4).
    assert len(view) == len(VIEW_TOOLS & LISTED_PROFILE_TOOLS) == len(VIEW_TOOLS)
    from keel.tools.outcomes._output_schemas import LISTED_OUTPUT_OMISSIONS

    omitted = set().union(*LISTED_OUTPUT_OMISSIONS.values())
    for t in view:
        if t.name == "keel_account_status":
            # The non-view probe arm is OFF: status keeps the `{"result":
            # string}` wrapper old connectors hold (R-25).
            assert t.to_mcp_tool().outputSchema.get("x-fastmcp-wrap-result"), t.name
            continue
        published = t.to_mcp_tool().outputSchema
        # The listed variant: the kind's schema minus its listed omissions
        # (Q-2080) — the fields the hosted listed server never emits.
        assert not (set(published["properties"]) & omitted), t.name
        assert set(published["properties"]) <= set(OUTPUT_SCHEMAS[t.name]["properties"]), t.name
        assert set(OUTPUT_SCHEMAS[t.name]["properties"]) - set(published["properties"]) <= omitted
    # Non-vacuity: the omissions bite on both kinds.
    backtest = next(t for t in view if t.name == "keel_backtest_run")
    strategy = next(t for t in view if t.name == "keel_strategy_get")
    assert "deploy" in OUTPUT_SCHEMAS["keel_backtest_run"]["properties"]
    assert "deploy" not in backtest.to_mcp_tool().outputSchema["properties"]
    assert "live_readiness_blockers" not in strategy.to_mcp_tool().outputSchema["properties"]


def test_every_view_tool_error_validates_through_the_real_server() -> None:
    """Both real error paths — the handler's missing-argument envelope and
    FastMCP's argument-validation wrapper — return the envelope as
    structuredContent (Q-1785) and it matches the declared schema."""
    from keel.mcp.server import create_server

    async def go() -> int:
        server = create_server()
        seen = 0
        for name in sorted(VIEW_TOOLS - {"keel_account_status"}):
            for args in ({}, {"zz_not_a_param": 1}):
                result = await server.call_tool(name, args)
                envelope = result.structured_content or {}
                if "code" not in envelope:
                    continue  # no required argument: `{}` is a real call
                # The text block leads with the human message and ends in
                # the whole envelope (spec 02 §2.2, R-27).
                text = result.content[0].text
                assert text.startswith(envelope["message"]), (name, args)
                assert json.loads(text[text.index("\n{") + 1 :]) == envelope, (name, args)
                _validate(name, envelope)
                seen += 1
        return seen

    seen = asyncio.run(go())
    # Every tool reached the unknown-argument path at least.
    assert seen >= len(VIEW_TOOLS) - 1, seen  # every view tool but status (no required arg)


def test_real_backtest_envelopes_validate() -> None:
    """Success envelopes from the real handlers, and a backtest view at
    every status the builder knows."""
    # SEED: in `_output_schemas._BACKTEST_VIEW` set
    # `"version": {"type": "string"}` — every backtest envelope reds.
    from keel.tools.outcomes import OUTCOMES, backtest_compare, backtest_summarize  # noqa: F401
    from keel.tools.outcomes._backtest_view import build_backtest_view
    from keel.tools.outcomes._base import ToolContext

    def fake_get(path, **_kw):
        if path.startswith("/v1/backtests/btr_") and path.count("/") == 3:
            return _detail(path.rsplit("/", 1)[1])
        return {}

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        summarize = (
            OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_a"}, ctx).to_envelope()
        )
        compare = (
            OUTCOMES["keel_backtest_compare"]
            .handler({"backtest_ids": ["btr_a", "btr_b", "btr_c"]}, ctx)
            .to_envelope()
        )
    assert summarize["view"]["tiles"] and compare["view"]["rows"]  # not vacuous
    for tool in ("keel_backtest_summarize", "keel_backtest_run", "keel_backtest_watch"):
        _validate(tool, summarize)
    _validate("keel_backtest_compare", compare)

    statuses = ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
    for status in statuses:
        detail = _detail("btr_s", status, error_message="boom" if status == "FAILED" else None)
        for size in ("receipt", "evidence"):
            view = build_backtest_view(detail, size=size, url="https://app.usekeel.io/b")
            for tool in ("keel_backtest_run", "keel_backtest_watch", "keel_backtest_summarize"):
                _validate(tool, {"run_id": "btr_s", "share_url": None, "view": view})


def test_real_strategy_envelopes_validate() -> None:
    """Strategy views built by the real builder from every card fixture
    that carries a server graph — the shape every strategy tool and both
    library tools emit."""
    # SEED: in `_output_schemas._STRATEGY_VIEW` add "kind" to `required`
    # and set `"kind": {"const": "graph"}` — every strategy envelope reds.
    from keel.tools.outcomes._strategy_view import build_view

    built = 0
    for path in sorted(CARD_FIXTURES.glob("strategy_*.envelope.json")):
        fixture = json.loads(path.read_text())
        meta = fixture.get("metadata") or {}
        if not isinstance(meta.get("graph"), dict):
            continue
        view = build_view(meta["graph"], meta, url=fixture.get("hero_url"))
        envelope = {"run_id": "str_x", "share_url": None, "view": view}
        for tool in (
            "keel_strategy_get",
            "keel_strategy_compose",
            "keel_strategy_fork",
            "keel_strategy_diff",
            "keel_library_get",
            "keel_library_fork",
        ):
            _validate(tool, envelope)
        built += 1
    assert built >= 3, built


@pytest.mark.parametrize("tool", sorted(OUTPUT_SCHEMAS))
def test_a_wrong_shape_is_rejected(tool: str) -> None:
    """Control: the schemas discriminate — a view of the wrong type fails."""
    with pytest.raises(jsonschema.ValidationError):
        _validate(tool, {"view": "not an object"})
