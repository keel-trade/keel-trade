"""What a backtest result says about what RAN (Q-1789).

claude.ai, staging dev-f960c13 (chat 7ab81cb7-…): after
`keel_backtest_run` + `keel_backtest_summarize` on "Simple Momentum
(Top 30 Perps)" v4, the model twice recommended "try a rebalance buffer if
it currently rebalances every bar" — v4 already had a 20% buffer. The
result carried the numbers and the window, never the config, so advice
about it was given blind. Every backtest result now carries the run's own
commit's config: one `config: …` line in the text block (claude.ai's only
model channel) and `view.config`.

These drive the REAL `keel_backtest_summarize` / `keel_backtest_watch`
through the real MCP server, with only HTTP faked.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the
commit. Proof it is not vacuous: the control arm (an every-bar strategy)
reads its own, different rebalance, and HEAD's source is never served —
the line can only come from the run's commit.
"""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import patch

import pytest


BUFFERED = """\
Globals(target_timeframe="1d")
Universe(mode="top_volume", top_n=30)
Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge")
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastWeightNormalizer(),
])
"""

EVERY_BAR = BUFFERED.replace(
    'Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", '
    'rebalance_method="to_edge")',
    'Execution(rebalance="every_bar")',
)


def _detail(run_id: str, commit_id: str) -> dict:
    return {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": "str_mom",
        "strategy_name": "Simple Momentum (Top 30 Perps)",
        "commit_id": commit_id,
        "sequence_number": 4,
        "start_date": "2024-08-15",
        "end_date": "2026-02-27",
        "metrics": {"sharpe_ratio": 0.8, "total_return": 12.0, "max_drawdown": 9.0},
    }


def _call(tool: str, args: dict, source: str) -> tuple[str, dict]:
    """(text block, what the card reads) of one real MCP call.

    The card reads `structuredContent` with `_meta["keel/card"]` merged over
    it (host-adapter.js), so the second value is that merge — identical to
    `structuredContent` with the probe flag off, and still the whole view
    with `KEEL_CARD_META_MOVE` on (spec 02 §2.7).
    """
    from keel.mcp.server import create_server

    def fake_get(path, **_kw):
        if path.startswith("/v1/backtests/btr_") and path.count("/") == 3:
            return _detail(path.rsplit("/", 1)[1], "c_run4")
        if path == "/v1/strategies/str_mom/versions/c_run4/source":
            return {"source": source}
        if "/versions/" in path or path.endswith("/source"):
            raise AssertionError(f"read a source other than the run's commit: {path}")
        return {}

    async def go():
        server = create_server()
        return await server.call_tool(tool, args)

    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        result = asyncio.run(go())
    from keel.tools.outcomes._channels import CARD_META_KEY, _merge

    card_view = copy.deepcopy(result.structured_content)
    _merge(card_view, (result.meta or {}).get(CARD_META_KEY) or {})
    return result.content[0].text, card_view


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("keel_backtest_summarize", {"backtest_id": "btr_a"}),
        ("keel_backtest_watch", {"backtest_id": "btr_a"}),
    ],
)
def test_a_buffered_run_says_so_in_the_text_block(tool: str, args: dict) -> None:
    # SEED: drop `("strategy_config", "config")` from `_OPERATIONAL_FIELDS`
    # in `_mcp_adapter.py` — the text block loses the line and this reds.
    text, structured = _call(tool, args, BUFFERED)
    config_lines = [line for line in text.splitlines() if line.startswith("config: ")]
    assert len(config_lines) == 1, text
    line = config_lines[0]
    # The execution as DSL a model can copy (Q-1846), not the card's label.
    assert (
        "Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', "
        "rebalance_method='to_edge')"
    ) in line, line
    assert "universe Top 30 by volume" in line and "clock 1d" in line, line
    assert "PriceDataLoader → ROC → ForecastWeightNormalizer" in line, line
    assert line.startswith("config: v4: "), line
    # One line. 320: the DSL form (Q-1846) spends ~50 characters more than
    # the label did, and the sizing clause (spec 03 §2.7) ~50 more — the
    # two facts an agent needs to reason about a result's size and turnover.
    assert len(line) < 320, len(line)
    assert line.endswith("sizing: ForecastWeightNormalizer 1× gross target"), line
    # The structured view carries the same, for the card and every reader.
    config = structured["view"]["config"]
    assert config["line"] == line.removeprefix("config: ")
    assert config["execution"]["buffer_threshold"] == 0.2
    # And the declared output schema (Q-1788) holds the new fields.
    import jsonschema
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS

    jsonschema.validate(structured, OUTPUT_SCHEMAS[tool])


def test_the_control_reads_its_own_rebalance() -> None:
    """Not vacuous: an every-bar run says every bar — the line reflects the
    commit, not a constant."""
    # SEED: in `config_from_source`, pass `declared` a constant buffered
    # execution in place of the commit's — this control reds.
    text, structured = _call("keel_backtest_summarize", {"backtest_id": "btr_a"}, EVERY_BAR)
    assert "Execution(rebalance='every_bar')" in text, text
    assert "buffered" not in text.split("config: ", 1)[1].splitlines()[0], text
    assert structured["view"]["config"]["execution"]["rebalance"] == "every_bar"


def test_an_unreadable_source_costs_only_the_line() -> None:
    """Advisory: a source that does not parse drops the line; the result
    is otherwise the same result."""
    text, structured = _call("keel_backtest_summarize", {"backtest_id": "btr_a"}, "not dsl(")
    assert "config: " not in text
    assert "config" not in structured["view"]
    assert structured["view"]["tiles"], json.dumps(structured)[:300]
