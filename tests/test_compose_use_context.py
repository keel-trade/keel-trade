"""Compose uses authorized immutable expansions through the ordinary validator."""

from unittest.mock import Mock

import pytest
from keel.tools.outcomes import ToolContext, _bootstrap
from keel.tools.outcomes.strategy_compose import _try_local_validate, _use_context


REF = "str_00000000000000000000000000@1"
CONSUMER = "str_00000000000000000000000001"
SOURCE = f'''Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC"], market="perp")
Execution(rebalance="every_bar")
Pipeline([Use("{REF}", output="score"), ForecastScaler(), ForecastWeightNormalizer()])
'''


@pytest.fixture
def use_context():
    _bootstrap()
    from pipeline_engine.dsl.use import OutputExpansion, StoredOutputExpansion, expansion_hash
    from pipeline_engine.validation_shared import OutputDescriptor

    text = 'Pipeline([PriceDataLoader(timeframe="1h"), ROC(period=3), Store("score")])'
    descriptor = OutputDescriptor(
        name="score",
        value_type="SignalSeries",
        shape="panel",
        domain=None,
        domain_source="none",
        tier=None,
        clock_source=None,
        clock=None,
        bounded=False,
        discrete=False,
        role="signal",
        role_source="inferred",
        unit="percent",
        unit_source="declared",
    )
    expansion = OutputExpansion(
        REF,
        {"score": StoredOutputExpansion(descriptor, text, expansion_hash(text), lookback_bars=3)},
        source_lock={"PriceDataLoader": 2, "ROC": 1},
        source_timeframe="1h",
    )
    return {
        "uses": {REF: expansion.to_dict()},
        "issues": [
            {
                "code": "USE_SOURCE_REMOVED",
                "severity": "info",
                "message": f"Source '{REF}' was removed; its pinned copy is unchanged.",
            }
        ],
    }


def test_persisted_copy_validates_and_preserves_pins_inside_use(use_context):
    result = _try_local_validate(
        SOURCE,
        base_lock={"PriceDataLoader": 3, "ROC": 1},
        use_context=use_context,
        consumer_id=CONSUMER,
    )
    assert result["ok"], result["errors"]
    assert result["lock"]["PriceDataLoader"] == 3 and result["lock"]["ROC"] == 1
    assert {i["code"] for i in result["info"]} >= {"USE_SOURCE_REMOVED", "USE_PIN_DIFFERS"}
    # The old adapter fed no copies: this exact source fails, so the proof
    # cannot pass by suppressing Use errors or by treating compile as validation.
    missing = _try_local_validate(SOURCE, use_context={"uses": {}}, consumer_id=CONSUMER)
    assert not missing["ok"] and missing["errors"][0]["code"] == "USE_BLOCK_NOT_FOUND"


def test_context_names_consumer_and_never_contacts_api_for_no_use(use_context):
    client = Mock()
    client.post.return_value = use_context
    ctx = ToolContext(api_client=client)
    assert _use_context(SOURCE, ctx, CONSUMER) is use_context
    client.post.assert_called_once_with(
        "/v1/strategies/validate", json={"source": SOURCE, "strategy_id": CONSUMER}
    )
    client.reset_mock()
    assert _use_context("Pipeline([PriceDataLoader()])", ctx, CONSUMER) is None
    client.post.assert_not_called()


def test_remote_compile_transmits_consumer_context(monkeypatch):
    from keel.tools import remote

    client = Mock()
    monkeypatch.setattr(remote, "_client", lambda: client)
    remote.strategy_compile(SOURCE, {"ROC": 1}, strategy_id=CONSUMER)
    client.post.assert_called_once_with(
        "/v1/strategies/compile",
        json={
            "source": SOURCE,
            "component_lock": {"ROC": 1},
            "strategy_id": CONSUMER,
        },
    )


def test_compose_dry_run_keeps_context_and_exposes_removed_info(monkeypatch, use_context):
    from keel.tools import remote
    from keel.tools.outcomes.strategy_compose import _handler

    class Client:
        def get(self, path, **kwargs):
            return {"component_lock": {"PriceDataLoader": 3, "ROC": 1}, "lock_hash": "a" * 64}

        def post(self, path, json=None, **kwargs):
            return use_context if path == "/v1/strategies/validate" else {}

    calls = []

    def compile_source(**kwargs):
        calls.append(kwargs)
        return {"compiled": True}

    monkeypatch.setattr(remote, "strategy_compile", compile_source)
    result = _handler(
        {"source": SOURCE, "strategy_id": CONSUMER, "dry_run": True},
        ToolContext(api_client=Client(), is_tty=False),
    )
    assert result.extra["compiled"] and result.extra["validation"]["ok"]
    assert "USE_SOURCE_REMOVED" in {i["code"] for i in result.extra["validation"]["info"]}
    assert (
        calls[0]["strategy_id"] == CONSUMER and calls[0]["component_lock"]["PriceDataLoader"] == 3
    )


def test_source_removed_info_reaches_plain_mcp_text(use_context):
    import json

    from keel.tools.outcomes._mcp_adapter import view_tool_result

    envelope = {
        "validation": {"ok": True, "errors": [], "warnings": [], "info": use_context["issues"]},
        "view": {
            "kind": "strategy",
            "size": "receipt",
            "name": "Pinned consumer",
            "status": "PREVIEW",
            "markdown": "Pinned consumer · valid",
        },
    }
    result = view_tool_result(json.dumps(envelope), "keel_strategy_compose")
    text = result.content[0].text
    assert "1 notice" in text and "USE_SOURCE_REMOVED" in text
    assert "pinned copy is unchanged" in text and "error" not in text.lower()
