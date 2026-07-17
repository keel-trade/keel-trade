"""`keel_open_in_app` (spec 01 R4) — navigation URL correctness.

Pure URL construction per id type, config-driven URL bases
(ToolContext defaults = prod; KEEL_APP_URL / KEEL_SHARE_URL_ROOT env
override on hosted deployments), instructive errors on unknown ids,
and presence on both server profiles.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()

TOOL = OUTCOMES["keel_open_in_app"]


def _run(args: dict, **ctx_kwargs):
    return TOOL.handler(args, ToolContext(**ctx_kwargs))


# ─── URL correctness per id type ────────────────────────────────────────


def test_strategy_id_links_to_overview():
    result = _run({"id": "str_01ABC"})
    assert result.hero_url == "https://app.usekeel.io/strategies/str_01ABC"
    assert result.extra["url"] == result.hero_url
    assert result.extra["target_kind"] == "strategy_overview"


def test_backtest_id_links_to_results_tearsheet():
    result = _run({"id": "btr_01XYZ"})
    assert result.hero_url == "https://app.usekeel.io/backtests/btr_01XYZ?tab=tearsheet"
    assert result.extra["target_kind"] == "backtest_results"


def test_share_id_links_to_public_share_page():
    result = _run({"id": "shr_01PUB"})
    assert result.hero_url == "https://usekeel.io/share/shr_01PUB"
    assert result.extra["target_kind"] == "share_page"


def test_share_url_stays_null_navigation_is_not_publication():
    """share_create is the single tool with a non-null share_url; a
    navigation link must not look like a publication event."""
    for target in ("str_1", "btr_1", "shr_1"):
        assert _run({"id": target}).share_url is None


def test_url_bases_come_from_server_config():
    result = _run(
        {"id": "str_01ABC"},
        app_url="https://staging-app.tailf4d598.ts.net",
        share_url_root="https://staging.usekeel.io/share",
    )
    assert result.hero_url == "https://staging-app.tailf4d598.ts.net/strategies/str_01ABC"
    result = _run(
        {"id": "shr_01PUB"},
        share_url_root="https://staging.usekeel.io/share",
    )
    assert result.hero_url == "https://staging.usekeel.io/share/shr_01PUB"


def test_id_is_trimmed():
    result = _run({"id": "  str_01ABC  "})
    assert result.hero_url == "https://app.usekeel.io/strategies/str_01ABC"


# ─── Errors ─────────────────────────────────────────────────────────────


def test_missing_id_is_instructive():
    with pytest.raises(KeelError) as exc_info:
        _run({})
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "missing_id"
    assert "str_" in json.dumps(envelope)


def test_unknown_prefix_is_instructive_not_a_guess():
    with pytest.raises(KeelError) as exc_info:
        _run({"id": "dep_123"})
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "unknown_id_prefix"
    assert "keel_strategy_search" in json.dumps(envelope)


def test_no_api_call_ever(monkeypatch):
    """Navigation must work with no client and no network."""

    def _boom(self):  # pragma: no cover — must not be called
        raise AssertionError("keel_open_in_app must never construct an API client")

    monkeypatch.setattr(ToolContext, "get_client", _boom)
    assert _run({"id": "str_1"}).hero_url.endswith("/strategies/str_1")


# ─── Adapter env overrides (hosted server config path) ──────────────────


def test_adapter_env_overrides_reach_the_tool(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import _make_handler

    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")
    monkeypatch.setenv("KEEL_SHARE_URL_ROOT", "https://staging.usekeel.io/share")
    handler = _make_handler(TOOL, frozenset())

    env = json.loads(handler(id="str_9"))
    assert env["hero_url"] == "https://staging-app.tailf4d598.ts.net/strategies/str_9"
    env = json.loads(handler(id="shr_9"))
    assert env["hero_url"] == "https://staging.usekeel.io/share/shr_9"


# ─── Registration surface ───────────────────────────────────────────────


def test_tool_is_on_both_profiles(monkeypatch):
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    assert "keel_open_in_app" in LISTED_PROFILE_TOOLS

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    for profile in ("full", "listed"):
        monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
        assert "keel_open_in_app" in loaded_tool_names(OUTCOMES), profile


def test_cli_command_registers():
    """`keel app open <id>` is the CLI face of the same outcome (one
    surface, both channels — CLI/MCP parity rule)."""
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    assert "app" in root.commands
    assert "open" in root.commands["app"].commands


def test_policy_vetted_description():
    """R4's exact description language must stay put (research/08)."""
    assert TOOL.description.startswith(
        "Returns a link to view and manage this strategy in the Keel web app."
    )
    assert TOOL.annotations["readOnlyHint"] is True
    assert TOOL.annotations["title"] == "Open in Keel App"
