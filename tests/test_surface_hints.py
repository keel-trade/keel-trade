"""String tests for the cross-surface routing hints (spec 07 R7).

Three mismatch classes, one hint each, shipped inside existing outputs:

* local CLI / local MCP → charts live in the web app (`keel open` /
  `keel_open_in_app`);
* hosted full endpoint → file/workspace work needs the CLI;
* listed connector → live management needs the full endpoint or CLI.

The hints must appear in `keel_status`, `keel_doctor`, and the MCP
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
    assert "keel_open_in_app" in LOCAL_CHARTS_HINT


def test_hosted_hint_points_at_cli_install():
    assert "pipx install keel-trade" in HOSTED_FILES_HINT
    assert "workspace" in HOSTED_FILES_HINT


def test_listed_hint_routes_to_web_app():
    # One-endpoint model (decision 2026-07-19/20): mcp.usekeel.io IS the
    # listed endpoint — there is no separate "full endpoint". Live
    # management routes to the web app via keel_open_in_app; the CLI holds
    # the full local toolset.
    assert "keel_open_in_app" in LISTED_LIVE_HINT
    assert "web app" in LISTED_LIVE_HINT
    assert "https://mcp.usekeel.io/mcp" not in LISTED_LIVE_HINT
    assert "CLI" in LISTED_LIVE_HINT


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


# ─── keel_status carries the hints ──────────────────────────────────────


def test_status_payload_carries_local_hint(clean_surface_env):
    # conftest's autouse _isolate_user_config redirects ~/.keel to tmp,
    # so with KEEL_API_KEY unset there is no auth and the handler skips
    # the identity/entitlement probes.
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes.status import STATUS

    clean_surface_env.delenv("KEEL_API_KEY", raising=False)
    result = STATUS.handler({}, ToolContext())
    assert result.extra["surface_hints"] == [LOCAL_CHARTS_HINT]


# ─── keel_doctor carries the hints (success and failure payloads) ───────


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
    (`keel_open_in_app`), not to a phantom 'full endpoint'. mcp.usekeel.io
    serves THIS same 23-tool surface, so the old copy claiming live
    management lived on a full endpoint there was stale/false — it is gone;
    the operating core carries the correct routing."""
    from keel.mcp.server import LISTED_INSTRUCTIONS

    assert "keel_open_in_app" in LISTED_INSTRUCTIONS
    assert "https://usekeel.io/agents" in LISTED_INSTRUCTIONS
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
    assert "keel_open_in_app" in text
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
        assert "keel_open_in_app" in (listed_server.instructions or "")
        assert "mcp.usekeel.io" not in (listed_server.instructions or "")
    finally:
        _restore_env(saved)
