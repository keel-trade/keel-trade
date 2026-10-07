"""Listed descriptions describe what the prod configuration returns (Q-2273 L6).

The round-5 audit read seven listed descriptions promising "JSON text in
`result`" — FastMCP's legacy `{"result": string}` wrapper — while the prod
non-view arm (`KEEL_NONVIEW_TEXT_ONLY=1`) registers those tools in `text`
mode: one text block, no `result` field, and markdown for the reading tools.
`keel_strategy_history` promised a source hash its listed entries no longer
carry (Q-2268), and the search's `keyword` matched inside words, so a
`keyword` + `query` call read as if the keyword were ignored.

Arms:

* under the prod flags, no listed tool registered in `text` mode names a
  `result` field (non-vacuous: more than ten such tools);
* the history copy names no field `LISTED_VERSION_FIELDS` drops;
* `keyword` matches a name or a description WORD, never inside a word, name
  matches first (unit, on the bundled catalog's own matcher).

Seed (2026-10-01, reverted by reversing the edit): share's description
regaining "as JSON text in `result`" → the first arm red on
`keel_share_create`.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


_bootstrap()


@pytest.fixture
def prod_listed(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_CARD_META_MOVE", "1")
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", "1")


def test_no_text_mode_tool_promises_a_result_field(prod_listed):
    from keel.tools.outcomes._mcp_adapter import effective_description, registration_mode

    text_mode = [n for n in sorted(LISTED_PROFILE_TOOLS) if registration_mode(n) == "text"]
    assert len(text_mode) > 10, text_mode
    wrong = [n for n in text_mode if "`result`" in effective_description(OUTCOMES[n])]
    assert not wrong, wrong


def test_the_history_copy_names_no_dropped_field(prod_listed):
    from keel.tools.outcomes._listed_projection import LISTED_VERSION_FIELDS
    from keel.tools.outcomes._mcp_adapter import effective_description

    assert "source_hash" not in LISTED_VERSION_FIELDS
    assert "source hash" not in effective_description(OUTCOMES["keel_strategy_history"])


def test_keyword_matches_names_and_description_words_not_inside_words():
    from keel.data.registry import keyword_matches

    catalog = [
        {"name": "Smoother", "description": "Process the series."},
        {"name": "Mapper", "description": "Example: ROC(period=10) -> Mapper."},
        {"name": "SignalROC", "description": "Rate of change of a signal."},
    ]
    assert [c["name"] for c in keyword_matches(catalog, "roc")] == ["SignalROC", "Mapper"]
    assert keyword_matches(catalog, "  ") == catalog
