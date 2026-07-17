"""Hosted toolset profile (spec 01 R2).

A ``hosted`` execution mode excludes the 8 filesystem/browser-bound
tools, expressed in the toolset machinery (`_toolsets.is_tool_loaded` +
`OutcomeTool.local_only`) — NOT ad-hoc server-side filtering — so
CLI/local behavior is unchanged. Workspace-optional tools no-op their
local branches server-side.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from keel.errors import KeelError
from keel.hosting import bind_request_credentials, clear_request_credentials
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._mcp_adapter import loaded_tool_names


HOSTED_EXCLUDED = {
    "keel_strategy_checkout",
    "keel_strategy_push",
    "keel_strategy_pull",
    "keel_strategy_status",
    "keel_strategy_discard",
    "keel_strategy_workspaces",
    "keel_auth_login",
    "keel_auth_logout",
}

ALL_TOOLSETS_ENV = "read-only,backtest,share,live-read,live-write"


@pytest.fixture(autouse=True)
def _bootstrap_registry():
    _bootstrap()
    clear_request_credentials()
    yield
    clear_request_credentials()


# ---------------------------------------------------------------------------
# Toolset machinery — the exclusion itself
# ---------------------------------------------------------------------------


def test_exactly_the_8_fs_bound_tools_are_local_only():
    """The spec 01 R2 list, exactly — no more, no fewer. A new
    FS-bound tool must declare local_only=True to join this set."""
    local_only = {t.name for t in OUTCOMES.values() if t.local_only}
    assert local_only == HOSTED_EXCLUDED


def test_hosted_loads_everything_except_the_8(monkeypatch):
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    hosted = set(loaded_tool_names(OUTCOMES))
    assert hosted == set(OUTCOMES) - HOSTED_EXCLUDED
    assert hosted.isdisjoint(HOSTED_EXCLUDED)


def test_local_mode_tool_surface_is_unchanged(monkeypatch):
    """CLI/local MCP keeps the full surface — the exclusion never fires
    outside hosted mode."""
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    assert set(loaded_tool_names(OUTCOMES)) == set(OUTCOMES)


def test_cli_registration_ignores_hosted_exclusion(monkeypatch):
    """The CLI adapter registers from OUTCOMES directly (all commands,
    every mode) — hosted exclusion must not leak into the CLI even if
    the env var were set in the CLI process."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)

    def _resolve(path: tuple[str, ...]):
        node = root
        for part in path:
            node = node.commands.get(part)
            if node is None:
                return None
        return node

    for name in HOSTED_EXCLUDED:
        if OUTCOMES[name].mcp_only:
            # auth login/logout: the CLI surface is hand-rolled in
            # keel.cli (not adapter-generated) — out of adapter scope.
            continue
        assert _resolve(OUTCOMES[name].cli_path) is not None, f"CLI lost {name}"


# ---------------------------------------------------------------------------
# Workspace-optional tools — hosted no-op of local branches
# ---------------------------------------------------------------------------


def test_strategy_diff_hosted_never_reads_pod_files(monkeypatch, tmp_path):
    """An existing pod file path must NOT be readable through diff refs
    hosted-side (information disclosure + wrong semantics)."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    from keel.tools.outcomes.strategy_diff import _read_path_or_source

    secret = tmp_path / "secret.py"
    secret.write_text("TOP-SECRET-POD-CONTENT")

    with pytest.raises(KeelError) as exc_info:
        _read_path_or_source(str(secret))
    assert "TOP-SECRET-POD-CONTENT" not in str(exc_info.value.to_envelope())
    assert "hosted" in str(exc_info.value).lower()

    # Multi-line inline DSL still passes straight through.
    dsl = "Globals(target_timeframe='4h')\nPipeline(steps=[])"
    assert _read_path_or_source(dsl) == dsl


def test_strategy_diff_local_still_reads_files(monkeypatch, tmp_path):
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    from keel.tools.outcomes.strategy_diff import _read_path_or_source

    f = tmp_path / "s.py"
    f.write_text("local file contents")
    assert _read_path_or_source(str(f)) == "local file contents"


def test_strategy_compose_hosted_rejects_source_file(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    from keel.tools.outcomes.strategy_compose import _read_source

    f = tmp_path / "strategy.py"
    f.write_text("Globals()")
    with pytest.raises(KeelError) as exc_info:
        _read_source({"source_file": str(f)})
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "usage_error"
    assert "source" in envelope["what_was_expected"]
    # Inline source is unaffected.
    assert _read_source({"source": "Globals()"}) == "Globals()"


def test_backtest_run_hosted_skips_divergence_guard(monkeypatch):
    """Hosted: no caller FS → the local-divergence guard must not even
    probe the workspace layer (pod paths are meaningless)."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")

    def _boom(*a, **k):  # pragma: no cover — must not be called
        raise AssertionError("workspace layer must not be touched hosted-side")

    monkeypatch.setattr("keel.workspace.get_workspace", _boom)
    bind_request_credentials(token="caller-tok", api_url="https://staging-api.test")

    submitted = {"id": "bt_hosted1", "status": "queued", "strategy_id": "strat_x"}
    with patch("keel.client.KeelClient.post", return_value=submitted):
        result = OUTCOMES["keel_backtest_run"].handler(
            {
                "strategy_id": "strat_x",
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "wait": False,
            },
            ToolContext(is_tty=False),
        )
    assert result.run_id == "bt_hosted1"


def test_strategy_search_hosted_never_lists_local_workspaces(monkeypatch):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")

    def _boom(*a, **k):  # pragma: no cover — must not be called
        raise AssertionError("local workspace listing must not run hosted-side")

    monkeypatch.setattr("keel.workspace.list_workspaces", _boom)
    bind_request_credentials(token="caller-tok", api_url="https://staging-api.test")

    remote = {"items": [{"id": "strat_1", "name": "Alpha", "org_id": "org_1"}]}
    with patch("keel.client.KeelClient.get", return_value=remote):
        # is_tty=True is the worst case — the only branch that merges
        # local workspaces in local mode.
        result = OUTCOMES["keel_strategy_search"].handler({}, ToolContext(is_tty=True))
    ids = {r["strategy_id"] for r in result.extra["results"]}
    assert ids == {"strat_1"}


def test_hosted_status_reports_hosted_visible_tools(monkeypatch):
    """keel_status's tools_visible mirrors the hosted registration set."""
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    bind_request_credentials(token="caller-tok", api_url="https://staging-api.test")

    with (
        patch(
            "keel.auth.get_identity",
            return_value={
                "principal": {"id": "prn_1"},
                "org": {"id": "org_1", "name": "o", "plan": "free"},
                "credential_scopes": [],
            },
        ),
        patch("keel.client.KeelClient.get", return_value={"balances": []}),
    ):
        result = OUTCOMES["keel_status"].handler({}, ToolContext(is_tty=False))
    visible = set(result.extra["tools_visible"])
    assert visible == set(OUTCOMES) - HOSTED_EXCLUDED
    assert json.loads(json.dumps(result.extra))  # envelope stays serializable
