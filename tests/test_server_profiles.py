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

import json

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import listed_enum
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
ALL_TOOLSETS_ENV = "read-only,backtest,share,live-read,paper,live-write"


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


def _omitted(tool_name: str, prefix: str = "") -> set[str]:
    """The declared omissions directly under `prefix` (R-8)."""
    from keel.tools.outcomes._base import LISTED_SCHEMA_OMISSIONS

    out = set()
    for path in LISTED_SCHEMA_OMISSIONS.get(tool_name, ()):
        head, _, leaf = path.rpartition(".")
        if head == prefix:
            out.add(leaf)
    return out


def test_listed_schema_overrides_keep_the_contract():
    """Property names, types, enums, defaults, and the required list must
    match the shared schema exactly — overrides are wording, not contract —
    EXCEPT for the declared omissions (`_base.LISTED_SCHEMA_OMISSIONS`,
    R-8): exactly those leave, at exactly their depth, and nothing else."""
    for tool in _tools_with_schema_override():
        shared = tool.input_schema
        listed = tool.listed_input_schema
        omitted = _omitted(tool.name)
        assert set(listed["properties"]) == set(shared["properties"]) - omitted, tool.name
        # The one declared exception (`_base.LISTED_REQUIRED_ADDITIONS`,
        # Q-2268): a parameter whose only alternative the listed schema omits
        # is required there. Nothing else may differ.
        from keel.tools.outcomes._base import LISTED_REQUIRED_ADDITIONS

        shared_required = shared.get("required", [])
        assert listed.get("required", []) == [r for r in shared_required if r not in omitted] + [
            p for p in LISTED_REQUIRED_ADDITIONS.get(tool.name, {}) if p not in shared_required
        ], tool.name
        for pname, pschema in shared["properties"].items():
            if pname in omitted:
                continue
            listed_p = listed["properties"][pname]
            # The declared bounds are contract too (Q-2270): the handler
            # enforces whichever schema the profile serves, so a bound that
            # differs between profiles is a behaviour that differs.
            for contract_key in (
                "type",
                "enum",
                "default",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
            ):
                expected = pschema.get(contract_key)
                if contract_key == "enum" and isinstance(expected, list):
                    # The declared listed spellings (`_base.LISTED_ENUM_RENAMES`,
                    # Q-2080): the same values, renamed where the table says so.
                    expected = listed_enum(tool.name, pname, expected)
                assert listed_p.get(contract_key) == expected, (
                    f"{tool.name}.{pname}.{contract_key} diverged between profiles"
                )
            nested_omitted = _omitted(tool.name, pname)
            if isinstance(pschema.get("properties"), dict):
                assert set(listed_p["properties"]) == set(pschema["properties"]) - nested_omitted, (
                    f"{tool.name}.{pname}"
                )


def test_listed_required_additions_are_only_omitted_alternatives():
    """The `required` exception (Q-2268, round 3) is narrow by construction:
    each row names a parameter that is OPTIONAL on the shared schema, present
    on both schemas, and whose alternative is a declared listed omission of
    the same tool — the one case where the listed surface leaves a caller no
    way to call without it. Non-vacuity: the table is non-empty and compose's
    listed schema really requires `source` while the full one does not.

    Proof it can fail (2026-10-01, reverted by reversing the edit): adding
    `"keel_backtest_run": {"strategy_id": "version"}` to the table reds this
    arm alone (`version` is no omission); emptying the table reds it too (the
    non-vacuity half) and the shared-`required` arm above stays the guard for
    every other tool."""
    from keel.tools.outcomes._base import LISTED_REQUIRED_ADDITIONS, LISTED_SCHEMA_OMISSIONS

    assert LISTED_REQUIRED_ADDITIONS, "no additions declared — the exception is vacuous"
    for tool_name, rows in LISTED_REQUIRED_ADDITIONS.items():
        tool = OUTCOMES[tool_name]
        assert tool.listed_input_schema is not None, tool_name
        for param, alternative in rows.items():
            assert alternative in LISTED_SCHEMA_OMISSIONS.get(tool_name, ()), (tool_name, param)
            assert param in tool.input_schema["properties"], (tool_name, param)
            assert param in tool.listed_input_schema["properties"], (tool_name, param)
            assert param not in tool.input_schema.get("required", []), (tool_name, param)
            assert param in tool.listed_input_schema["required"], (tool_name, param)
    compose = OUTCOMES["keel_strategy_compose"]
    assert compose.listed_input_schema["required"] == ["source"]
    assert "source" not in compose.input_schema.get("required", [])
    assert "source_file" in compose.input_schema["properties"]


def test_every_declared_enum_rename_names_a_real_value():
    """Non-vacuity for the enum rule above: each declared rename targets a
    parameter that exists on the shared schema, and every shared value it
    names is really in that enum (a typo'd value would rename nothing and
    pass)."""
    from keel.tools.outcomes._base import LISTED_ENUM_RENAMES

    assert LISTED_ENUM_RENAMES, "no renames declared — the rule is vacuous"
    for (tool_name, param), renames in LISTED_ENUM_RENAMES.items():
        shared = OUTCOMES[tool_name].input_schema["properties"][param]["enum"]
        listed = OUTCOMES[tool_name].listed_input_schema["properties"][param]["enum"]
        assert set(renames) <= set(shared), (tool_name, param)
        assert set(renames.values()) <= set(listed), (tool_name, param)
        assert not (set(renames.values()) & set(shared)), (tool_name, param)
        assert len(listed) == len(shared)


def test_every_declared_omission_names_a_real_parameter():
    """Non-vacuity for the rule above: each omitted path exists on the
    SHARED schema (a typo'd path would omit nothing and pass)."""
    from keel.tools.outcomes._base import LISTED_SCHEMA_OMISSIONS

    assert sum(len(v) for v in LISTED_SCHEMA_OMISSIONS.values()) >= 5
    for tool_name, paths in LISTED_SCHEMA_OMISSIONS.items():
        tool = OUTCOMES[tool_name]
        assert tool.listed_input_schema is not None, tool_name
        for path in paths:
            node = tool.input_schema
            for part in path.split("."):
                node = node["properties"][part]
            listed_node = tool.listed_input_schema
            *parents, leaf = path.split(".")
            for part in parents:
                listed_node = listed_node["properties"][part]
            assert leaf not in listed_node["properties"], (tool_name, path)


def test_effective_surface_switches_with_profile(monkeypatch):
    tool = OUTCOMES["keel_live_monitor"]

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    # The full profile serves the monitor's paper sentence (released, PT2-91).
    assert tool.paper_description is not None
    assert effective_description(tool) == tool.paper_description
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
    assert full["keel_live_monitor"].description == OUTCOMES["keel_live_monitor"].paper_description
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
    from keel.mcp.server import create_server, listed_instructions

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.setenv("KEEL_TOOLSETS", ALL_TOOLSETS_ENV)
    # The live block is `profiles: [full]` in operating_core.md, so it is
    # in the full assembly and in no listed one. (Pinned on the sentence
    # rather than the old hand-written "LIVE WRITE" heading: the blocks
    # became corpus sections on 2026-09-22 — guidance spec §4.)
    full_instructions = create_server().instructions or ""
    assert "live-write toolset" in full_instructions

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert create_server().instructions == listed_instructions()
    assert "live-write toolset" not in listed_instructions()


# ---------------------------------------------------------------------------
# The operating core's paper section rides keel_paper_deploy (S06 §3.9 row 3)
# ---------------------------------------------------------------------------
#
# Released (the paper_sdk_released fixture), a full-profile server that loads
# keel_paper_deploy carries the `paper` section once, after the live section,
# which is byte-unchanged; one whose toolsets exclude `paper` carries none;
# the listed instructions are untouched. The held arm is
# tests/test_paper_sdk_hold.py (HEAD's instructions, byte for byte).

PAPER_ROW = (
    "PAPER — keel_paper_deploy creates a simulated paper-trading run from a "
    "saved strategy and completes here; running with real capital is a "
    "web-app step."
)


@pytest.mark.parametrize("hosted", [False, True])
def test_full_instructions_carry_the_paper_section_once_when_loaded(
    paper_sdk_released, monkeypatch, hosted
):
    from keel.mcp.server import create_server
    from keel.tools.outcomes import paper_deploy_loaded

    from pipeline_engine.reference.system import assemble
    from tests._full_instructions_head import key, load

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    if hosted:
        monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    else:
        monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    assert paper_deploy_loaded() is True
    text = create_server().instructions
    assert text.count(PAPER_ROW) == 1
    live = assemble.section("live").terse
    assert text.count(live) == 1
    assert text.index(live) < text.index(PAPER_ROW)
    # Everything else is HEAD's: removing the one block gives HEAD's string.
    head = load()["instructions"][key("full-hosted" if hosted else "full-local", False)]
    assert text.replace("\n\n" + PAPER_ROW, "", 1) == head


def test_full_instructions_omit_the_paper_section_when_not_loaded(paper_sdk_released, monkeypatch):
    from keel.mcp.server import create_server
    from keel.tools.outcomes import paper_deploy_loaded

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest")
    assert paper_deploy_loaded() is False
    assert "keel_paper" not in create_server().instructions


def test_listed_instructions_untouched_by_the_paper_section(paper_sdk_released, monkeypatch):
    from keel.mcp.server import create_server

    from tests._listed_head_fixtures import SURFACE_FIXTURE

    head = json.loads(SURFACE_FIXTURE.read_text(encoding="utf-8"))["instructions"]
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)
    text = create_server().instructions
    assert "keel_paper" not in text
    assert text == head


# ---------------------------------------------------------------------------
# The `paper` toolset (S06 §3.3, AC-06-3)
# ---------------------------------------------------------------------------
#
# Released, `paper` rides the default set: both keel_paper_* tools load with
# no KEEL_TOOLSETS; `live-write` alone does not load them, and the `live`
# alias does not expand to `paper`. compute_numbers() counts the local default
# from `_DEFAULT_TOOLSETS` itself. Held, the toolsets and both counts are
# HEAD's. Seed: drop `with_paper_toolset` from `_toolsets.default_toolsets()`
# ⇒ the default arm and the tools_local_default arm both red. Count: the
# default arm loads exactly two keel_paper_* tools, and the live-write arm
# loads at least one live-write tool.

PAPER_TOOLS = {"keel_paper_deploy", "keel_paper_control"}


def _paper_loaded_names(monkeypatch, toolsets_env: str | None) -> set[str]:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    if toolsets_env is None:
        monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    else:
        monkeypatch.setenv("KEEL_TOOLSETS", toolsets_env)
    return set(loaded_tool_names(OUTCOMES))


def test_default_toolsets_load_both_paper_tools(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes import _toolsets

    assert "paper" in _toolsets._DEFAULT_TOOLSETS
    loaded = _paper_loaded_names(monkeypatch, None)
    assert {n for n in loaded if n.startswith("keel_paper_")} == PAPER_TOOLS


def test_live_write_alone_does_not_load_paper(paper_sdk_released, monkeypatch):
    loaded = _paper_loaded_names(monkeypatch, "live-write")
    assert not loaded & PAPER_TOOLS
    live_write = {t.name for t in OUTCOMES.values() if t.toolset == "live-write"}
    assert loaded & live_write, "the live-write toolset did not load; this arm proves nothing"


def test_the_live_alias_does_not_expand_to_paper(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes._toolsets import load_toolsets

    loaded = _paper_loaded_names(monkeypatch, "live")
    assert "paper" not in load_toolsets()
    assert not loaded & PAPER_TOOLS
    assert "keel_live_control" in loaded  # the alias did expand


def test_tools_local_default_counts_the_default_set(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes._toolsets import _DEFAULT_TOOLSETS, is_tool_loaded

    numbers = _checker().compute_numbers()
    expected = sum(1 for t in OUTCOMES.values() if is_tool_loaded(t.toolset, _DEFAULT_TOOLSETS))
    assert numbers["tools_local_default"] == expected
    paper = sum(1 for t in OUTCOMES.values() if t.toolset == "paper")
    assert paper == 2
    assert numbers["tools_local_default"] == HELD_TOOLS_LOCAL_DEFAULT + 2
    assert numbers["tools_registry"] == HELD_TOOLS_REGISTRY + 2


def test_compute_numbers_holds_no_toolset_literal():
    """§3.3: the count reads `_DEFAULT_TOOLSETS`; a hand copy moved nothing."""
    source = _checker_path().read_text(encoding="utf-8")
    body = source[source.index("def compute_numbers") :]
    body = body[: body.index("\ndef ")]
    assert '"live-read"' not in body and "local_default = frozenset" not in body
    assert "_DEFAULT_TOOLSETS as local_default" in body


#: HEAD's numbers at the paper base (48 registered tools; the local default
#: set loads every tool outside live-write).
HELD_TOOLS_REGISTRY = 48
HELD_TOOLS_LOCAL_DEFAULT = 45


def _checker_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "scripts" / "check_surface_routing.py"


def _checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_surface_routing_toolset", _checker_path())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
