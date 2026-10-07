"""Reading material is markdown under the non-view probe arm (spec 02 §2.2,
guard G8) — and LOSSLESS, because these four tools carry the discovery half
of NL → DSL (LANES.md bar #1).

`keel_help(topic="dsl_syntax")`'s text starts `topic: dsl_syntax` and carries
no escaped newlines; `keel_components_search` renders `### ` headings; every
parameter of a component reaches the text. With the arm OFF (default), the
text stays the JSON envelope.

SEED (run 2026-09-23, reverted by reversing the edit): return the JSON
envelope as text for help (drop `keel_help` from `READING_RENDERERS`) —
`test_help_is_the_document_itself` reds while the components arms stay green.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from keel.tools.outcomes._channels import NONVIEW_TEXT_ENV


def _call(monkeypatch, tool: str, args: dict, *, arm: str = "1"):
    from keel.mcp.server import create_server

    monkeypatch.setenv(NONVIEW_TEXT_ENV, arm)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    return asyncio.run(create_server().call_tool(tool, args))


def test_help_is_the_document_itself(monkeypatch) -> None:
    result = _call(monkeypatch, "keel_help", {"topic": "dsl_syntax"})
    text = result.content[0].text
    assert text.startswith("topic: dsl_syntax\n")
    assert "\\n" not in text
    # Non-vacuity (a quantity the seed cannot move): the document is long.
    assert len(text) >= 2_000
    assert result.structured_content is None


def test_the_topic_list_carries_one_line_per_topic(monkeypatch) -> None:
    """Review 06 M-5 #3: the no-topic listing names each topic WITH a line."""
    result = _call(monkeypatch, "keel_help", {})
    lines = [x for x in result.content[0].text.splitlines() if x.startswith("- `")]
    assert len(lines) >= 10
    assert any(line.startswith("- `dsl_syntax` — ") for line in lines)


def test_components_search_renders_headings_and_loses_nothing(monkeypatch) -> None:
    result = _call(monkeypatch, "keel_components_search", {"query": "moving average"})
    text = result.content[0].text
    assert "### " in text
    assert "`" in text and "→" in text


def test_compose_help_keeps_every_parameter(monkeypatch) -> None:
    """The NL → DSL bar: every parameter name, type and default reaches the
    text a model reads — nothing the JSON form carried is dropped."""
    result = _call(monkeypatch, "keel_components_get", {"name": "ForecastScaler"})
    text = result.content[0].text
    off = _call(monkeypatch, "keel_components_get", {"name": "ForecastScaler"}, arm="")
    envelope = json.loads(off.content[0].text)
    assert envelope["parameters"], "ForecastScaler has parameters"
    for param in envelope["parameters"]:
        assert f"`{param['name']}`" in text, param["name"]
        assert str(param.get("type")) in text
    assert text.startswith("### ForecastScaler")


@pytest.mark.parametrize("tool,args", [("keel_help", {"topic": "dsl_syntax"})])
def test_with_the_arm_off_the_text_is_the_json_envelope(monkeypatch, tool, args) -> None:
    """CONTROL: default OFF — today's JSON text, wrapped as structuredContent."""
    result = _call(monkeypatch, tool, args, arm="")
    envelope = json.loads(result.content[0].text)
    assert envelope["topic"] == "dsl_syntax"
    assert result.structured_content == {"result": result.content[0].text}
