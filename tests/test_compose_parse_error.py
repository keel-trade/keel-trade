"""A dry run whose source does not PARSE gets the same result shape as one
that does not validate (Q-1840).

The R3 probe (claude.ai, staging, 2026-09-23) composed
`Execution(buffer=0.2)` and got back the legacy raw envelope —
`{"validation": {...}, "compiled": true, "compile_error": null, ...}` — with
no `view`, so claude.ai's text block was the JSON itself, `compiled: true`
sat beside a parse error, the issue carried no code (no `keel_help
rule:<CODE>` pointer), and the card, handed nothing, drew
"Untitled · PIPELINE · 0 BLOCKS · No backtest yet".

These run the REAL local validator over the probe's own source — nothing
about the parse is stubbed — and fake only the network: the compile
endpoint answers exactly what `routers/sdk.py` answers for a parse error (a
200 whose body is `{compiled: false, errors: [...]}`), and `/parse` answers
400, as it does.

Proof it can fail (run 2026-09-23, each seed reverted by reversing the edit):

* `"compiled": compiled_ok` → `"compiled": bool(compiled)` in
  strategy_compose.py: `test_compiled_is_the_verdict_not_the_body` goes red
  (`True is False`) — the probe's exact contradiction.
* the parse-error branch disabled (`parse_issue = None`): four red — the
  receipt, text-block, no-link and title arms (`'view' in env` first); the
  audit and compiled arms stay green, because they do not depend on it.
* `code` dropped from `parse_error_issue`: two red — the issue arm and the
  text-block arm's missing `rule:PARSE_ERROR` pointer.

Proof it is not vacuous: the probe's source really fails to parse under the
bundled parser (asserted first, from the parser itself), and the control arm
composes a source that DOES parse through the same handler and gets the
ordinary `/parse` preview — so the new branch is chosen by the parse, not
taken for every dry run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()

#: The R3 probe's source, as the agent wrote it (`buffer=` is not a param).
PROBE = """Globals(target_timeframe='1d', bar_offset='12h')
Universe(mode='top_volume', top_n=30, market='perp')
Execution(rebalance='buffered', buffer=0.2)
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='btc_beater')
"""

#: The same strategy, parseable (the control arm).
PARSES = PROBE.replace("buffer=0.2", "buffer_threshold=0.2")

STRATEGY = "str_01k5g8w2y3z4a5b6c7d8e9f0g1"


class _Client:
    """The network, faked the way the server answers it."""

    def __init__(self, saved_name: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.saved_name = saved_name

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path))
        if path == f"/v1/strategies/{STRATEGY}" and self.saved_name:
            return {"strategy_id": STRATEGY, "name": self.saved_name}
        return {}

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        self.calls.append(("POST", path))
        if path == "/v1/strategies/parse":
            from pipeline_engine.dsl.parser import DSLParseError, parse_strategy

            try:
                parse_strategy(json["source"])
            except DSLParseError as e:  # /parse answers 400 on a parse error
                raise RuntimeError(f"400 Parse error: {e}") from e
            return {"graph": {"blocks": [{"type": "component", "component": "ROC"}]}}
        return {}


@pytest.fixture
def compile_refuses(monkeypatch):
    """`/v1/strategies/compile` on a parse error: a 200 with a refusal body."""

    def _compile(source: str, component_lock: Any = None) -> dict:
        from pipeline_engine.dsl.parser import DSLParseError, parse_strategy

        try:
            parse_strategy(source)
        except DSLParseError as e:
            return {"compiled": False, "errors": [f"Parse error: {e}"]}
        return {"compiled": True, "fingerprint": "f", "graph": {}}

    monkeypatch.setattr("keel.tools.remote.strategy_compile", _compile, raising=False)


def _compose(args: dict, client: _Client | None = None) -> dict:
    ctx = ToolContext(api_client=client or _Client(), is_tty=False)
    return OUTCOMES["keel_strategy_compose"].handler({"dry_run": True, **args}, ctx).to_envelope()


def test_the_probe_source_really_does_not_parse():
    """Non-vacuity: the subject is a genuine parse failure."""
    from pipeline_engine.dsl.parser import DSLParseError, parse_strategy

    with pytest.raises(DSLParseError, match="Unknown Execution parameter 'buffer'"):
        parse_strategy(PROBE)
    parse_strategy(PARSES)  # and the control genuinely parses


def test_compiled_is_the_verdict_not_the_body(compile_refuses):
    env = _compose({"source": PROBE, "name": "BTC beater"})
    assert env["compiled"] is False
    assert "Unknown Execution parameter 'buffer'" in env["compile_error"]


def test_the_parse_issue_carries_a_code_and_a_position(compile_refuses):
    env = _compose({"source": PROBE, "name": "BTC beater"})
    (issue,) = env["validation"]["errors"]
    # The parser's own code (Q-1695), not the PARSE_ERROR umbrella: the
    # agent can `keel_help rule:` exactly this failure.
    assert issue["code"] == "UNKNOWN_DECLARATION_KEY"
    assert issue["location"]["line"] == 3
    assert issue["message"].startswith("Parse error at line 3")


def test_a_parse_error_renders_the_preview_receipt(compile_refuses):
    env = _compose({"source": PROBE, "name": "BTC beater"})
    assert "view" in env
    view = env["view"]
    assert view["parse_error"] is True
    assert view["status"] == "PREVIEW"
    assert view["validation"] == {"ok": False, "errors": 1, "warnings": 0}
    first = view["markdown"].splitlines()[0]
    # The name the caller passed, the preview chip, the one error — and
    # never "0 blocks" or "No backtest yet".
    assert first == "**BTC beater** · Preview · 1 error — did not parse"
    assert "Unknown Execution parameter 'buffer'" in view["markdown"]
    assert "0 blocks" not in view["markdown"]
    assert "No backtest" not in view["markdown"]
    # The source rides in the view for the MODEL (structuredContent); the
    # card never shows a dry run's source (Q-1848).
    assert "buffer=0.2" in view["source"]["text"]


def test_the_text_block_is_the_receipt_with_a_rule_pointer(compile_refuses):
    """What claude.ai's model reads: the markdown plus the validation line —
    never the raw JSON envelope it got before."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    env = _compose({"source": PROBE, "name": "BTC beater"})
    result = view_tool_result(json.dumps(env), "keel_strategy_compose")
    text = result.content[0].text
    assert '"compiled"' not in text  # not the JSON envelope
    assert "**BTC beater** · Preview · 1 error" in text
    assert 'keel_help topic="rule:UNKNOWN_DECLARATION_KEY"' in text


def test_the_error_state_offers_no_link(compile_refuses):
    """Even when the dry run edits a saved strategy: that strategy is not
    this source, and this source cannot be saved (ChatGPT re-test)."""
    env = _compose(
        {"source": PROBE, "strategy_id": STRATEGY},
        _Client(saved_name="HYPE MACD cash"),
    )
    assert "hero_url" not in env
    assert "url_line" not in env["view"]


def test_the_title_prefers_the_saved_name_then_the_pipeline_name(compile_refuses):
    edit = _compose(
        {"source": PROBE, "strategy_id": STRATEGY}, _Client(saved_name="HYPE MACD cash")
    )
    assert edit["view"]["name"] == "HYPE MACD cash"
    fresh = _compose({"source": PROBE})
    assert fresh["view"]["name"] == "btc_beater"


def test_the_audit_verdict_is_recorded_apart_from_is_error(compile_refuses):
    from keel.hosting import (
        close_request_outcome,
        current_request_outcome,
        open_request_outcome,
    )

    token = open_request_outcome()
    try:
        _compose({"source": PROBE, "name": "BTC beater"})
        slot = dict(current_request_outcome())
    finally:
        close_request_outcome(token)
    # The rule the parse tripped rides beside the verdict (Q-2368).
    assert slot == {"validation_ok": False, "issue_codes": ["UNKNOWN_DECLARATION_KEY"]}


def test_control_a_source_that_parses_takes_the_ordinary_preview(compile_refuses):
    client = _Client()
    env = _compose({"source": PARSES, "name": "BTC beater"}, client)
    assert env["compiled"] is True
    assert ("POST", "/v1/strategies/parse") in client.calls
    assert env["view"].get("parse_error") is not True
    assert env["view"]["markdown"].startswith("Preview · ")
