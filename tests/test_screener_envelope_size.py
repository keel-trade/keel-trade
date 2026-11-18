"""The actual SDK/MCP split fits both shapes and passing stays text."""

import json
from pathlib import Path

import pytest
from jsonschema import validate
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._channels import CARD_META_ENV, partition
from keel.tools.outcomes._mcp_adapter import registration_mode, result_for_mode
from keel.tools.outcomes._output_schemas import output_schema_for
from keel.tools.outcomes._screener_view import build_result


FIXTURES = Path(__file__).parent / "fixtures/cards"


def compact(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


@pytest.mark.parametrize("shape", ["card", "fallback"])
@pytest.mark.parametrize("size", ["receipt", "view"])
def test_actual_envelope_budgets(shape, size, monkeypatch):
    monkeypatch.setenv(CARD_META_ENV, "true" if shape == "card" else "false")
    source = json.loads((FIXTURES / f"screener_{shape}.json").read_text())
    env = build_result(source, ToolContext(), size=size).to_envelope()
    structured, card = partition(env, "screener")
    validate(structured, output_schema_for("keel_screener_view"))
    assert compact(structured) < (8000 if shape == "card" else 14000)
    assert compact({"structuredContent": structured, "_meta": card}) < 60000
    assert len(env["view"]["markdown"].encode()) < 3000
    if shape == "card" and size == "view":
        assert "hero" in card and "hero" not in structured
    if shape == "fallback":
        assert "latest" not in structured
    if size == "receipt":
        assert "hero" not in structured and "hero" not in card


def test_passing_always_text_only(monkeypatch):
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", "false")
    env = build_result(
        json.loads((FIXTURES / "screener_fallback.json").read_text()), ToolContext()
    ).to_envelope()
    mode = registration_mode("keel_screener_passing")
    assert mode == "text" and output_schema_for("keel_screener_passing") is None
    result = result_for_mode(mode, json.dumps(env), "keel_screener_passing")
    assert result.structured_content is None and not result.meta
    assert result.content[0].text == env["view"]["markdown"]


@pytest.mark.parametrize("shape", ["card", "fallback"])
@pytest.mark.parametrize("size", ["receipt", "view"])
def test_long_token_budget_fixture(shape, size, monkeypatch):
    monkeypatch.setenv(CARD_META_ENV, "true" if shape == "card" else "false")
    source = json.loads((FIXTURES / f"screener_{shape}.json").read_text())
    source["screener"]["name"] = "𐀀" * 80
    complete = source["completed"]
    complete["labels"] = [{"label": "𐀀" * 32, "unit": "percent"} for _ in range(3)]
    assets = [f"LONGSYMBOL{i:02}" for i in range(50)]
    complete["latest"]["rows"] = [[a, -1.234e-5, -1.234e10, -1.234e-5] for a in assets]
    complete["latest_top"]["rows"] = complete["latest"]["rows"][:5]
    count, n = (20, 150) if shape == "card" else (8, 60)
    complete["hero"]["assets"] = assets[:count]
    complete["hero"]["values"] = [
        [-1.234e-5 if j % 2 else -1.234e10 for j in range(n)] for _ in range(count)
    ]
    complete["hero"]["n"] = n
    complete["hero"]["anchor_values"] = {a: [1735732800000, 1234] for a in assets[:count]}
    env = build_result(source, ToolContext(), size=size).to_envelope()
    structured, card = partition(env, "screener")
    assert compact(structured) < (8000 if shape == "card" else 14000)
    assert compact({"structuredContent": structured, "_meta": card}) < 60000
    assert len(env["view"]["markdown"].encode()) < 3000


def test_full_passing_lists_text_budget():
    source = json.loads((FIXTURES / "screener_fallback.json").read_text())
    symbols = [f"LONGSYMBOL{i:02}" for i in range(50)]
    passing = {"now": {"assets": symbols}, "entered": symbols, "left": symbols, "counts": {"passing": 50, "entered": 50, "left": 50}}
    env = build_result(source, ToolContext(), full_passing=passing).to_envelope()
    text = env["view"]["markdown"]
    assert len(text.encode()) <= 4500
    for symbol in symbols:
        assert text.count(symbol) == 3
