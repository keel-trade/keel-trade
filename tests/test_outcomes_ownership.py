"""Tests for first-session ownership outcome surface."""

from __future__ import annotations

from typing import Any

import pytest
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()


class _FakeClient:
    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("GET", path, params or None))
        return self.payloads.get(path, {})


def test_ownership_status_registers_cli_path():
    tool = OUTCOMES["keel_ownership_status"]

    assert tool.cli_path == ("ownership", "status")
    assert tool.input_schema["properties"]["strategy_id"]["x-cli-positional"] is True


def test_ownership_status_returns_projection_fields():
    client = _FakeClient(
        {
            "/v1/strategy-work-sessions": {"items": [{"session_id": "sws_1"}]},
            "/v1/strategy-work-sessions/sws_1/ownership": {
                "overall_status": "owned_baseline",
                "next_recommended_action": {"kind": "show_failure_modes"},
                "missing_evidence": ["failure_modes"],
                "live_readiness_blockers": ["no_diagnosis"],
            },
        }
    )
    ctx = ToolContext(api_client=client)

    env = OUTCOMES["keel_ownership_status"].handler(
        {"strategy_id": "str_1"},
        ctx,
    ).to_envelope()

    assert env["resource_uri"] == "keel://ownership/strategy/str_1"
    assert env["ownership_status"] == "owned_baseline"
    assert env["next_recommended_action"]["kind"] == "show_failure_modes"
    assert env["missing_evidence"] == ["failure_modes"]
