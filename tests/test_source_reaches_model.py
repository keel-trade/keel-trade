"""A requested source reaches the MODEL on every host (Q-1874, R4 probe).

claude.ai hands its model the text block only (Part C probe (e)); ChatGPT's
model reads `structuredContent` and never `_meta`. `keel_strategy_get` with
`include_source=true` must therefore carry the DSL in BOTH — and the text
block used to be `view.markdown` + the catalogue lines, neither of which
carried it, so the R4 claude.ai agent "got the structure card only" and
rebuilt the strategy by guessing parameter names.

Driven through the REAL handler and the REAL adapter, with the card move ON
(staging's `KEEL_CARD_META_MOVE=1`) and OFF.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* delete `("source", "source", DATA)` from `_mcp_adapter._OPERATIONAL_FIELDS`
  — `test_a_requested_source_is_in_the_text_block` reds on both arms; the
  structuredContent assertion and the control stay green;
* drop `"source"` from `_channels._STRATEGY.structured` and add it to its
  `card` set — the structuredContent assertion reds on the ON arm only.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest
from keel.tools.outcomes._channels import CARD_META_ENV, CARD_META_KEY


HERE = pathlib.Path(__file__).resolve().parent
RECORDED = json.loads((HERE / "fixtures" / "channels" / "strategy_get.envelope.json").read_text())
SID = RECORDED["metadata"]["id"]
#: The recording's own DSL — a real strategy, not a stub string.
SOURCE = RECORDED["view"]["source"]["text"]


def _envelope(include_source: bool) -> dict:
    from keel.tools.outcomes import _bootstrap, get
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = MagicMock()

    def fake_get(path, **_kw):
        if path == f"/v1/strategies/{SID}":
            return RECORDED["metadata"]
        if path.endswith("/source"):
            return {"source": SOURCE, "version": "HEAD"}
        return {"data": []}

    client.get.side_effect = fake_get
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io", is_tty=False)
    args = {"strategy_id": SID, "skip_readiness": True}
    if include_source:
        args["include_source"] = True
    return get("keel_strategy_get").handler(args, ctx).to_envelope()


def _result(envelope: dict):
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    return view_tool_result(json.dumps(envelope, default=str), "keel_strategy_get")


#: A line of the DSL that only the source carries (the markdown renders the
#: structure in its own words, never the literal declaration).
NEEDLE = "Globals(target_timeframe='1d', bar_offset='12h')"


def test_the_needle_is_only_in_the_source() -> None:
    """Non-vacuity: the fixture's source is real and its needle is not
    something the structure markdown would print anyway."""
    assert NEEDLE in SOURCE and SOURCE.count("\n") >= 8
    assert NEEDLE not in RECORDED["view"]["markdown"]


@pytest.mark.parametrize("arm", ["", "1"])
def test_a_requested_source_is_in_the_text_block(monkeypatch, arm: str) -> None:
    monkeypatch.setenv(CARD_META_ENV, arm)
    result = _result(_envelope(include_source=True))
    text = result.content[0].text
    assert "\nsource: the DSL at HEAD, " in text, text[-400:]
    assert NEEDLE in text
    # The whole source, not a head of it (this one is far under the cap).
    assert SOURCE.strip("\n") in text
    # structuredContent carries it too (ChatGPT's model channel).
    structured = result.structured_content
    assert structured["source"]["source"] == SOURCE
    # Whatever moved to the card, the source did not leave the model's copy.
    assert "source" not in (result.meta or {}).get(CARD_META_KEY, {})


@pytest.mark.parametrize("arm", ["", "1"])
def test_an_unrequested_source_stays_out_of_the_text_block(monkeypatch, arm: str) -> None:
    """CONTROL: without `include_source` the text block stays the structure
    (the Code tab's `view.source` is the card's, not a `source:` line)."""
    monkeypatch.setenv(CARD_META_ENV, arm)
    result = _result(_envelope(include_source=False))
    text = result.content[0].text
    assert "\nsource: " not in text
    assert NEEDLE not in text
