"""Server profiles (spec 01 R3) — `full` vs `listed` from one codebase.

Profile selection is deploy-time env (`KEEL_SERVER_PROFILE`), consumed
by the toolset machinery (`_toolsets`) — the same place the hosted
local_only exclusion lives — so no server-side ad-hoc filtering can
drift from it. The `listed` surface is EXACTLY
`LISTED_PROFILE_TOOLS`, independent of `KEEL_TOOLSETS`.

The policy-string rules for the listed surface live in
tests/test_policy_scan.py (the hard gate); this file covers the
machinery + the profile matrix.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._mcp_adapter import (
    effective_annotations,
    effective_description,
    effective_input_schema,
    loaded_tool_names,
)
from keel.tools.outcomes._toolsets import (
    LISTED_PROFILE_TOOLS,
    is_listed_profile,
    server_profile,
)


_bootstrap()

HOSTED_EXCLUDED = {t.name for t in OUTCOMES.values() if t.local_only}
ALL_TOOLSETS_ENV = "read-only,backtest,share,live-read,live-write"


# ---------------------------------------------------------------------------
# server_profile() parsing
# ---------------------------------------------------------------------------


def test_default_profile_is_full(monkeypatch):
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    assert server_profile() == "full"
    assert not is_listed_profile()


@pytest.mark.parametrize("value", ["full", "listed", " FULL ", "Listed"])
def test_valid_profiles_parse(monkeypatch, value):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", value)
    assert server_profile() == value.strip().lower()


@pytest.mark.parametrize("value", ["restricted", "list", "ful", "1", "default"])
def test_invalid_profile_raises_never_falls_back(monkeypatch, value):
    """A typo must never silently widen the surface to `full`."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", value)
    with pytest.raises(ValueError, match="KEEL_SERVER_PROFILE"):
        server_profile()


# ---------------------------------------------------------------------------
# Profile matrix — who sees what (spec 01 R3 verify)
# ---------------------------------------------------------------------------


def test_matrix_full_hosted(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    assert set(loaded_tool_names(OUTCOMES)) == set(OUTCOMES) - HOSTED_EXCLUDED


def test_matrix_full_local_unchanged(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    assert set(loaded_tool_names(OUTCOMES)) == set(OUTCOMES)


def test_matrix_listed_hosted(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    assert set(loaded_tool_names(OUTCOMES)) == LISTED_PROFILE_TOOLS


@pytest.mark.parametrize(
    "toolsets_env",
    ["", "read-only", ALL_TOOLSETS_ENV, "read-only,backtest,share"],
)
def test_listed_surface_is_deterministic_across_toolsets(monkeypatch, toolsets_env):
    """The directory-reviewed registration must never vary with the
    KEEL_TOOLSETS env — an env typo can't widen OR shrink it."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    if toolsets_env:
        monkeypatch.setenv("KEEL_TOOLSETS", toolsets_env)
    else:
        monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    assert set(loaded_tool_names(OUTCOMES)) == LISTED_PROFILE_TOOLS


def test_listed_allow_list_contains_no_local_only_or_live_write():
    for name in LISTED_PROFILE_TOOLS:
        tool = OUTCOMES[name]
        assert not tool.local_only, f"{name} is local_only — cannot be listed"
        assert tool.toolset != "live-write", f"{name} is live-write — cannot be listed"


def test_default_profile_env_absent_behaves_exactly_as_before(monkeypatch):
    """No KEEL_SERVER_PROFILE = the pre-profile behavior (toolset env +
    hosted exclusion only) — existing deployments are untouched."""
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest,share")
    visible = set(loaded_tool_names(OUTCOMES))
    expected = {
        t.name
        for t in OUTCOMES.values()
        if not t.local_only and (t.toolset in {"always", "read-only", "backtest", "share"})
    }
    assert visible == expected


# ---------------------------------------------------------------------------
# Listed overrides are copy-only — behavior identical across profiles
# ---------------------------------------------------------------------------


def _tools_with_schema_override():
    return [t for t in OUTCOMES.values() if t.listed_input_schema is not None]


def test_listed_schema_overrides_keep_the_contract():
    """Property names, types, enums, defaults, and the required list must
    match the shared schema exactly — overrides are wording, not contract."""
    for tool in _tools_with_schema_override():
        shared = tool.input_schema
        listed = tool.listed_input_schema
        assert set(listed["properties"]) == set(shared["properties"]), tool.name
        assert listed.get("required", []) == shared.get("required", []), tool.name
        for pname, pschema in shared["properties"].items():
            listed_p = listed["properties"][pname]
            for contract_key in ("type", "enum", "default"):
                assert listed_p.get(contract_key) == pschema.get(contract_key), (
                    f"{tool.name}.{pname}.{contract_key} diverged between profiles"
                )


def test_effective_surface_switches_with_profile(monkeypatch):
    tool = OUTCOMES["keel_live_monitor"]

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert effective_description(tool) == tool.description
    assert effective_input_schema(tool) == tool.input_schema
    assert effective_annotations(tool) == tool.annotations

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert effective_description(tool) == tool.listed_description
    assert effective_input_schema(tool) == tool.listed_input_schema
    assert effective_annotations(tool)["title"] == tool.listed_title
    # Hints are shared — only the title is overridable copy.
    assert effective_annotations(tool)["readOnlyHint"] is tool.annotations["readOnlyHint"]


def test_tools_without_overrides_serve_shared_copy_on_listed(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    tool = OUTCOMES["keel_strategy_get"]
    assert tool.listed_description is None
    assert effective_description(tool) == tool.description
    assert effective_input_schema(tool) == tool.input_schema


# ---------------------------------------------------------------------------
# End-to-end registration under each profile (real FastMCP)
# ---------------------------------------------------------------------------


def _registered_tools(monkeypatch, profile: str | None):
    import asyncio

    from fastmcp import FastMCP
    from keel.tools.outcomes._mcp_adapter import register_all

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    if profile is None:
        monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    else:
        monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    mcp = FastMCP(name=f"profile-{profile}")
    register_all(mcp, OUTCOMES)
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def test_registration_matrix_end_to_end(monkeypatch):
    full = _registered_tools(monkeypatch, "full")
    assert set(full) == set(OUTCOMES) - HOSTED_EXCLUDED
    # Shared copy on full.
    assert full["keel_live_monitor"].description == OUTCOMES["keel_live_monitor"].description
    assert (
        full["keel_live_monitor"].annotations.title
        == OUTCOMES["keel_live_monitor"].annotations["title"]
    )

    listed = _registered_tools(monkeypatch, "listed")
    assert set(listed) == LISTED_PROFILE_TOOLS
    lm = listed["keel_live_monitor"]
    assert lm.description == OUTCOMES["keel_live_monitor"].listed_description
    assert lm.annotations.title == OUTCOMES["keel_live_monitor"].listed_title
    assert (
        lm.parameters["properties"]["limit"]["description"]
        == OUTCOMES["keel_live_monitor"].listed_input_schema["properties"]["limit"]["description"]
    )


def test_server_instructions_switch_with_profile(monkeypatch):
    from keel.mcp.server import LISTED_INSTRUCTIONS, create_server

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    assert "LIVE WRITE" in (create_server().instructions or "")

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert create_server().instructions == LISTED_INSTRUCTIONS
