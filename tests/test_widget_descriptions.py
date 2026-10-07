"""What the model is told about the card (spec 02 §2.6, guard G7) — and the
`card:` line's probe-gated retirement (R-9).

Every card-backed listed tool's DESCRIPTOR `_meta` carries
`openai/widgetDescription`: a fact about what the card shows, ≤ 200 chars,
clean under the listed word rules. `CARD_SHOWN_LINE` stays until the probe
flips `KEEL_CARD_LINE_RETIRED` (default OFF): both states are asserted.

SEED (run 2026-09-23, reverted by reversing the edit): insert "deploy" into
the live string — `test_the_descriptions_are_word_rule_clean` reds; the
count/length arms stay green.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from keel.widgets import (
    CARD_TOOLS,
    LISTED_EXCLUDED_CARDS,
    MAX_WIDGET_DESCRIPTION_CHARS,
    WIDGET_DESCRIPTIONS,
)

from tests.test_policy_scan import FORBIDDEN_TEXT_RE, HOST_ADDRESSED_RE, IMPERATIVE_RE


LISTED_KINDS = {"backtest", "compare", "strategy", "live"}


def test_every_listed_kind_is_declared_and_short() -> None:
    assert LISTED_KINDS <= set(WIDGET_DESCRIPTIONS)
    assert len(LISTED_KINDS) == 4
    for kind, text in WIDGET_DESCRIPTIONS.items():
        assert 0 < len(text) <= MAX_WIDGET_DESCRIPTION_CHARS, kind


def test_the_descriptions_are_word_rule_clean() -> None:
    for kind in LISTED_KINDS:
        text = WIDGET_DESCRIPTIONS[kind]
        for rule in (FORBIDDEN_TEXT_RE, IMPERATIVE_RE, HOST_ADDRESSED_RE):
            assert not rule.search(text), (kind, rule.pattern, text)


def test_every_listed_card_tool_publishes_one(monkeypatch) -> None:
    from keel.mcp.server import create_server

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    listed_card_tools = [
        name
        for name, kind in CARD_TOOLS.items()
        if kind not in LISTED_EXCLUDED_CARDS and name in tools
    ]
    # Non-vacuity: CARD_TOOLS minus keel_live_deploy.
    assert len(listed_card_tools) == 10
    for name in listed_card_tools:
        meta = tools[name].to_mcp_tool().meta or {}
        assert meta.get("openai/widgetDescription") == WIDGET_DESCRIPTIONS[CARD_TOOLS[name]], name


@pytest.mark.parametrize("retired", ["", "1"])
def test_the_card_line_is_probe_gated(monkeypatch, retired: str) -> None:
    from keel.tools.outcomes._mcp_adapter import CARD_SHOWN_LINE, view_tool_result

    monkeypatch.setenv("KEEL_CARD_LINE_RETIRED", retired)
    envelope = {"run_id": "str_x", "view": {"kind": "strategy", "markdown": "**X** · v1"}}
    text = view_tool_result(json.dumps(envelope), "keel_strategy_get").content[0].text
    assert (CARD_SHOWN_LINE in text) is (not retired)
    # CONTROL: a tool with no card never carried the line.
    plain = view_tool_result(json.dumps(envelope), "keel_library_get").content[0].text
    assert CARD_SHOWN_LINE not in plain
