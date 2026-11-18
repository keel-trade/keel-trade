"""AT-34's agent half (Research 07 §10; RL-91 §5): the core agent flows do not
move with Research, in either flag state, on either MCP profile.

For every strategy, backtest, paper and screen tool an agent's core flow uses,
what the tool publishes (description, input schema, annotations, rate tier and
action) is the same with research_served on and off, on the full and the
listed profile, except the four Screens descriptions on the listed profile,
which take 00 §H14's trimmed copy while Research is served (07 §8.5). And a
call through each strategy tool sends keel-api the same request in both
states.

SEED (reverted by reversing the edit): make effective_description append a
space while research_served(); every core tool's on arm reds.
Not vacuous: 9 core tools on 2 profiles in 2 states.
"""

from __future__ import annotations

import pytest

from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import LISTED_RESEARCH_COPY
from keel.tools.outcomes._mcp_adapter import effective_description, effective_input_schema
from keel.tools.outcomes._toolsets import research_served_context


_bootstrap()

CORE = (
    "keel_strategy_compose",
    "keel_strategy_get",
    "keel_strategy_fork",
    "keel_backtest_run",
    "keel_backtest_summarize",
    "keel_screener_run",
    "keel_screener_view",
    "keel_screener_view_set",
    "keel_screener_list",
)


def _published(name: str) -> dict:
    tool = OUTCOMES[name]
    return {
        "description": effective_description(tool),
        "schema": effective_input_schema(tool),
        "annotations": tool.annotations,
        "rate_tier": tool.rate_tier,
        "action": tool.required_action,
    }


@pytest.mark.parametrize("profile", ["full", "listed"])
@pytest.mark.parametrize("name", CORE)
def test_a_core_tool_publishes_the_same_in_both_states(monkeypatch, profile, name) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    monkeypatch.setenv("KEEL_LISTED_SCREENS_HOSTS", "all")
    with research_served_context(False):
        off = _published(name)
    with research_served_context(True):
        on = _published(name)
    if profile == "listed" and name in LISTED_RESEARCH_COPY:
        # 00 §H14: the only move, and only while Research is served.
        assert on.pop("description") == LISTED_RESEARCH_COPY[name]
        off.pop("description")
    assert on == off
    assert off["schema"]["properties"]  # not vacuous


class _Client:
    def __init__(self) -> None:
        self.calls: list = []

    def get(self, path, *, headers=None, **params):
        self.calls.append(("get", path, params))
        return {"strategy_id": "str_a", "name": "s", "version": 1}


@pytest.mark.parametrize("profile", ["full", "listed"])
def test_a_strategy_read_sends_the_same_request_in_both_states(monkeypatch, profile) -> None:
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    sent = {}
    for served in (False, True):
        client = _Client()
        with research_served_context(served):
            try:
                OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_a"}, ToolContext(api_client=client))
            except Exception:  # noqa: BLE001 - a thin fake answer; the request is what is compared
                pass
        sent[served] = client.calls
    assert sent[False] and sent[False] == sent[True]
