"""`keel open` (spec 06 R4) — captured-open tests.

The AC: `keel open backtest <id>` opens the RIGHT URL, proven via a
captured browser-open call (the browser itself is always mocked). URL
routing must be the same single source of truth as `keel_open_in_app`.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from keel import browser_login
from keel.cli.main import cli


@pytest.fixture
def opened(monkeypatch):
    """Capture the URLs `keel open` hands to the browser."""
    urls: list[str] = []
    monkeypatch.setattr(browser_login, "_try_open_browser", lambda u: urls.append(u) or True)
    return urls


@pytest.mark.parametrize(
    ("kind", "target_id", "expected"),
    [
        ("backtest", "btr_01ABC", "https://app.usekeel.io/backtests/btr_01ABC?tab=tearsheet"),
        ("strategy", "str_01ABC", "https://app.usekeel.io/strategies/str_01ABC"),
        ("live", "dep_01ABC", "https://app.usekeel.io/live/dep_01ABC"),
    ],
)
def test_open_launches_canonical_url(opened, kind, target_id, expected):
    result = CliRunner().invoke(cli, ["open", kind, target_id])
    assert result.exit_code == 0, result.output
    assert opened == [expected]
    # The plain URL line prints regardless of the launch (spec 06 R4).
    assert f"View in Keel: {expected}" in result.output


def test_open_respects_keel_app_url(opened, monkeypatch):
    """KEEL_APP_URL steers the target — same env, same routing as
    keel_open_in_app (one URL source of truth)."""
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.tailf4d598.ts.net")
    result = CliRunner().invoke(cli, ["open", "backtest", "btr_9"])
    assert result.exit_code == 0
    assert opened == ["https://staging-app.tailf4d598.ts.net/backtests/btr_9?tab=tearsheet"]


def test_open_routing_matches_open_in_app(opened):
    """The MCP twin and the CLI verb resolve identical URLs."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    twin = OUTCOMES["keel_open_in_app"].handler({"id": "btr_77"}, ToolContext())
    result = CliRunner().invoke(cli, ["open", "backtest", "btr_77"])
    assert result.exit_code == 0
    assert opened == [twin.hero_url]


def test_open_kind_id_mismatch_fails_instructively(opened):
    result = CliRunner().invoke(cli, ["open", "backtest", "str_01ABC"])
    assert result.exit_code == 2
    assert opened == []  # no browser call on error
    # `emit_error` writes the error envelope to stderr, not stdout.
    payload = json.loads(result.stderr)
    assert payload["code"] == "kind_id_mismatch"
    assert "keel open strategy str_01ABC" in payload["what_was_expected"]


def test_open_rejects_share_ids_with_pointer(opened):
    result = CliRunner().invoke(cli, ["open", "strategy", "shr_01ABC"])
    assert result.exit_code == 2
    assert opened == []
    payload = json.loads(result.stderr)
    assert payload["code"] == "share_id_not_openable_here"
    assert "keel app open shr_01ABC" in payload["what_was_expected"]


def test_open_rejects_unknown_prefix_for_prefixed_kinds(opened):
    result = CliRunner().invoke(cli, ["open", "backtest", "banana"])
    assert result.exit_code == 2
    assert opened == []
    assert json.loads(result.stderr)["code"] == "unknown_id_prefix"


def test_open_refuses_on_hosted_server(opened, monkeypatch):
    """LOCAL envs only (spec 06 R4): the hosted server must never
    attempt a browser open — it points at keel_open_in_app instead."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    result = CliRunner().invoke(cli, ["open", "strategy", "str_01ABC"])
    assert result.exit_code == 2
    assert opened == []
    payload = json.loads(result.stderr)
    assert payload["code"] == "open_not_available_hosted"
    assert "keel_open_in_app" in payload["what_was_expected"]


def test_open_reports_failed_browser_launch(monkeypatch):
    """Launch failure is not an error — the URL line already printed."""
    monkeypatch.setattr(browser_login, "_try_open_browser", lambda _u: False)
    result = CliRunner().invoke(cli, ["open", "strategy", "str_01ABC"])
    assert result.exit_code == 0
    assert "View in Keel: https://app.usekeel.io/strategies/str_01ABC" in result.output
    assert "open the URL above manually" in result.output


def test_open_is_cli_only_never_an_mcp_tool():
    """`keel open` must not appear on any MCP surface (its MCP twin is
    keel_open_in_app, which only RETURNS the URL)."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    assert "keel_open" not in OUTCOMES
    assert not any(t.cli_path == ("open",) for t in OUTCOMES.values())
