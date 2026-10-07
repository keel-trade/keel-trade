"""String tests for the cross-surface routing hints (spec 07 R7).

Three mismatch classes, one hint each, shipped inside existing outputs:

* local CLI / local MCP → charts live in the web app (`keel open` /
  `keel_app_link`);
* hosted endpoint → file/workspace work needs the CLI;
* listed connector → live management is the web-app handoff
  (`keel_app_link`); the local toolset is on the CLI.

The hints must appear in `keel_account_status`, `keel_connection_check`, and the MCP
server instructions for the matching surface — and never leak the wrong
surface's hint.
"""

from __future__ import annotations

import os

import pytest
from keel.tools.outcomes._surface_hints import (
    HOSTED_FILES_HINT,
    LISTED_LIVE_HINT,
    LOCAL_CHARTS_HINT,
    surface_hints,
)


@pytest.fixture
def clean_surface_env(monkeypatch):
    for var in ("KEEL_EXECUTION_MODE", "KEEL_SERVER_PROFILE", "KEEL_TOOLSETS"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ─── The helper picks exactly one hint per surface ──────────────────────


def test_local_surface_gets_charts_hint(clean_surface_env):
    assert surface_hints() == [LOCAL_CHARTS_HINT]


def test_hosted_surface_gets_files_hint(clean_surface_env):
    clean_surface_env.setenv("KEEL_EXECUTION_MODE", "hosted")
    assert surface_hints() == [HOSTED_FILES_HINT]


def test_listed_surface_gets_live_hint(clean_surface_env):
    clean_surface_env.setenv("KEEL_EXECUTION_MODE", "hosted")
    clean_surface_env.setenv("KEEL_SERVER_PROFILE", "listed")
    assert surface_hints() == [LISTED_LIVE_HINT]


# ─── Hint copy: the load-bearing strings (spec 07 R7 examples) ──────────


def test_local_hint_points_at_keel_open():
    assert "keel open" in LOCAL_CHARTS_HINT
    assert "keel_app_link" in LOCAL_CHARTS_HINT


def test_hosted_hint_points_at_cli_install():
    assert "pipx install keel-trade" in HOSTED_FILES_HINT
    assert "workspace" in HOSTED_FILES_HINT


def test_listed_hint_routes_to_web_app():
    # One-endpoint model (decision 2026-07-19/20): mcp.usekeel.io IS the
    # listed endpoint — there is no separate "full endpoint". Live
    # management routes to the web app via keel_app_link. The directory
    # connector's copy names no CLI or other client (Q-2268, round 3).
    assert "keel_app_link" in LISTED_LIVE_HINT
    assert "web app" in LISTED_LIVE_HINT
    assert "https://mcp.usekeel.io/mcp" not in LISTED_LIVE_HINT
    assert "CLI" not in LISTED_LIVE_HINT
    assert "keel open" not in LISTED_LIVE_HINT and "pipx" not in LISTED_LIVE_HINT


def test_listed_hint_passes_research08_string_rules():
    """The listed hint is listed-surface copy — hold it to the same
    forbidden-verb-family rules as the rest of the listed profile."""
    import re

    forbidden = re.compile(
        r"\b("
        r"deploy(?:s|ed|ing|ment|ments)?"
        r"|fund(?:s|ed|ing)?"
        r"|trade[sd]?|trading"
        r"|buy(?:s|ing)?|bought"
        r"|sell(?:s|ing)?|sold"
        r"|wallets?|leverage[sd]?|amounts?|upgrade[sd]?"
        r"|go live|going live|start trading"
        r")\b",
        re.IGNORECASE,
    )
    assert not forbidden.search(LISTED_LIVE_HINT)


# ─── keel_account_status carries the hints ──────────────────────────────────────


def test_status_payload_carries_no_hints_and_says_what_this_server_carries(clean_surface_env):
    """Retired from `keel_account_status` (agent-surface-cleanup spec 02 §2.5): the
    status view's line 3, derived from the loaded tools, is the fact the
    hints restated. `keel_connection_check` keeps its hints (below)."""
    # conftest's autouse _isolate_user_config redirects ~/.keel to tmp,
    # so with KEEL_API_KEY unset there is no auth and the handler skips
    # the identity/entitlement probes.
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes.status import STATUS

    clean_surface_env.delenv("KEEL_API_KEY", raising=False)
    result = STATUS.handler({}, ToolContext())
    assert "surface_hints" not in result.extra
    assert "This server: research" in result.extra["view"]["markdown"]


# ─── keel_connection_check carries the hints (success and failure payloads) ───────


def test_doctor_failure_payload_carries_hints(clean_surface_env):
    from keel.errors import KeelError
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes.doctor import DOCTOR

    # No auth configured (isolated tmp config, no env key) → the auth
    # check fails → KeelError payload carries the hints.
    clean_surface_env.delenv("KEEL_API_KEY", raising=False)
    with pytest.raises(KeelError) as excinfo:
        DOCTOR.handler({}, ToolContext())
    assert excinfo.value.input["surface_hints"] == [LOCAL_CHARTS_HINT]


# ─── Server instructions carry the matching surface hint ────────────────


def _saved_env():
    return {
        k: os.environ.get(k)
        for k in ("KEEL_EXECUTION_MODE", "KEEL_SERVER_PROFILE", "KEEL_TOOLSETS")
    }


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_listed_instructions_route_beyond_chat_to_the_web_app():
    """The listed connector routes 'act beyond chat' to the web-app handoff
    (`keel_app_link`), not to a phantom 'full endpoint'. mcp.usekeel.io
    serves THIS same 23-tool surface, so the old copy claiming live
    management lived on a full endpoint there was stale/false — it is gone;
    the operating core carries the correct routing."""
    from keel.mcp.server import LISTED_INSTRUCTIONS

    assert "keel_app_link" in LISTED_INSTRUCTIONS
    # The per-surface guide URL is on the FULL profiles' SURFACE block.
    # It left the listed string on 2026-09-22 (W2 §2.1) to fit the 2 KB
    # host limit: a URL is not a route a tools-only agent can follow, and
    # `keel_app_link` — asserted above — is the listed route beyond
    # chat. The URL is still asserted where it is served.
    from pipeline_engine.reference.system import assemble

    assert "https://usekeel.io/agents" not in LISTED_INSTRUCTIONS
    for profile in ("full-hosted", "full-local"):
        assert "https://usekeel.io/agents" in assemble.instructions(profile)
    # The stale/false full-endpoint claim must not reappear.
    assert "mcp.usekeel.io" not in LISTED_INSTRUCTIONS
    assert "Live strategy management" not in LISTED_INSTRUCTIONS


def test_hosted_full_instructions_carry_files_hint():
    from keel.mcp.server import _full_instructions

    text = _full_instructions(False, hosted=True)
    assert "file-free" in text
    assert "pipx install keel-trade" in text
    assert "https://usekeel.io/agents" in text


def test_local_full_instructions_carry_charts_hint():
    from keel.mcp.server import _full_instructions

    text = _full_instructions(False, hosted=False)
    assert "keel open" in text
    assert "keel_app_link" in text
    assert "https://usekeel.io/agents" in text


def test_created_server_instructions_match_surface(clean_surface_env):
    saved = _saved_env()
    try:
        from keel.mcp.server import create_server

        os.environ.pop("KEEL_EXECUTION_MODE", None)
        os.environ.pop("KEEL_SERVER_PROFILE", None)
        local_server = create_server()
        assert "keel open" in (local_server.instructions or "")

        os.environ["KEEL_EXECUTION_MODE"] = "hosted"
        hosted_server = create_server()
        assert "file-free" in (hosted_server.instructions or "")

        os.environ["KEEL_SERVER_PROFILE"] = "listed"
        listed_server = create_server()
        # Listed surface routes 'act beyond chat' to the web app, not a
        # phantom full endpoint (the stale claim is gone).
        assert "keel_app_link" in (listed_server.instructions or "")
        assert "mcp.usekeel.io" not in (listed_server.instructions or "")
    finally:
        _restore_env(saved)


# ── tool_ref: how this surface names another outcome (Q-1743) ──────────


@pytest.mark.parametrize(
    "tool",
    ["keel_backtest_run", "keel_backtest_watch", "keel_strategy_search"],
)
def test_tool_ref_names_the_mcp_tool_off_the_cli(tool, monkeypatch):
    import keel.surface as surface
    from keel.tools.outcomes import _bootstrap
    from keel.tools.outcomes._surface_hints import tool_ref

    _bootstrap()
    monkeypatch.setattr(surface, "_SURFACE", "local-mcp")
    assert tool_ref(tool) == f"`{tool}`"


@pytest.mark.parametrize(
    ("tool", "cli"),
    [
        ("keel_backtest_run", "`keel backtest run`"),
        ("keel_backtest_watch", "`keel backtest watch`"),
        ("keel_strategy_search", "`keel strategy search`"),
    ],
)
def test_tool_ref_names_the_mounted_cli_command_on_the_cli(tool, cli, monkeypatch):
    """The CLI spelling is read off the tool's cli_path — and it resolves to a
    command the CLI actually mounts (non-vacuous: a real click lookup)."""
    import keel.surface as surface
    from keel.cli.main import cli as root
    from keel.tools.outcomes._surface_hints import tool_ref

    monkeypatch.setattr(surface, "_SURFACE", "cli")
    ref = tool_ref(tool)
    assert ref == cli
    words = ref.strip("`").split()[1:]
    node = root
    for word in words:
        node = node.commands[word]
    assert node.name == words[-1]
