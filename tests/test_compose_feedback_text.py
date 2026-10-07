"""The NL → DSL feedback loop survives the channel rework (LANES.md bar #1).

A compose dry run's model-visible TEXT carries every validation issue it
names with its code, its fix and its `keel_help topic="rule:<CODE>"` route —
through the real handler, the real local validator and the real
`view_tool_result`, with the card-meta and non-view probe arms both OFF and
ON (the channel rework must never cost the agent its feedback).

SEED (run 2026-09-23, reverted by reversing the edit): drop the `fix:` clause
from `_mcp_adapter._issue_line` — `test_the_dry_run_text_names_code_fix_and_route`
reds on every arm while the parse CONTROL stays green.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._channels import CARD_META_ENV, NONVIEW_TEXT_ENV
from keel.tools.outcomes._mcp_adapter import view_tool_result


_bootstrap()

#: Parses, and fails validation: `ROC` has no `window` parameter.
INVALID = """Globals(target_timeframe='1d', bar_offset='12h')
Universe(mode='top_volume', top_n=30, market='perp')
Execution(rebalance='every_bar')
Pipeline([
    PriceDataLoader(),
    ROC(window=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name='bad_param')
"""


class _Client:
    def get(self, path: str, **params: Any) -> Any:
        return {}

    def post(self, path: str, json: dict | None = None, **params: Any) -> Any:
        if path == "/v1/strategies/parse":
            return {"graph": {"blocks": [{"type": "component", "component": "ROC"}]}}
        return {}


@pytest.fixture(autouse=True)
def _compile(monkeypatch):
    monkeypatch.setattr(
        "keel.tools.remote.strategy_compile",
        lambda source, component_lock=None: {"compiled": True, "fingerprint": "f", "graph": {}},
        raising=False,
    )


def _dry_run() -> dict:
    ctx = ToolContext(api_client=_Client(), is_tty=False)
    return (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": INVALID, "dry_run": True, "name": "bad"}, ctx)
        .to_envelope()
    )


def test_the_subject_really_fails_validation() -> None:
    """Non-vacuity: at least one coded error, with a suggestion to render."""
    errors = _dry_run()["validation"]["errors"]
    coded = [e for e in errors if isinstance(e, dict) and e.get("code")]
    assert coded, errors
    assert any(e.get("suggestion") for e in coded), coded


@pytest.mark.parametrize("arm", ["", "1"])
def test_the_dry_run_text_names_code_fix_and_route(monkeypatch, arm: str) -> None:
    monkeypatch.setenv(CARD_META_ENV, arm)
    monkeypatch.setenv(NONVIEW_TEXT_ENV, arm)
    envelope = _dry_run()
    result = view_tool_result(json.dumps(envelope), "keel_strategy_compose")
    text = result.content[0].text
    shown = [e for e in envelope["validation"]["errors"] if isinstance(e, dict) and e.get("code")][
        :5
    ]
    for issue in shown:
        assert issue["code"] in text
        assert f'keel_help topic="rule:{issue["code"]}"' in text
        if issue.get("suggestion"):
            assert str(issue["suggestion"]).strip()[:40] in text, issue
    # Every issue, every field, stays in structuredContent (never truncated).
    assert result.structured_content["validation"] == envelope["validation"]
