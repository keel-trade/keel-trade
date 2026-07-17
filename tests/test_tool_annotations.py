"""Annotation sweep gate (spec 01 R5) — every outcome tool carries
`title` + `readOnlyHint`/`destructiveHint`, correctly classified.

Directory submissions pass/fail on tool annotations, so this scan is a
hard gate: it fails when a tool is missing an annotation, when the
hints contradict each other, when a tool drifts from the canonical
classification table below, or when a NEW tool ships without being
classified here.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap


_bootstrap()


# Canonical classification: name -> (readOnlyHint, destructiveHint).
# This table IS the review surface: changing a tool's safety class must
# show up in this file's diff, and a new tool fails the sweep until it
# is deliberately classified.
EXPECTED_HINTS: dict[str, tuple[bool, bool]] = {
    # Read-only surfaces
    "keel_accounts_list": (True, False),
    "keel_audit_list_last": (True, False),
    "keel_backtest_summarize": (True, False),
    "keel_backtest_watch": (True, False),
    "keel_components_compose_help": (True, False),
    "keel_components_detail_batch": (True, False),
    "keel_components_search": (True, False),
    "keel_doctor": (True, False),
    "keel_help": (True, False),
    "keel_live_monitor": (True, False),
    "keel_open_in_app": (True, False),  # navigation link builder — no API call
    "keel_ownership_status": (True, False),
    "keel_plan_status": (True, False),  # plan/limits/remaining facts — numbers only
    "keel_status": (True, False),
    "keel_strategy_diff": (True, False),
    "keel_strategy_get": (True, False),
    "keel_strategy_log": (True, False),
    "keel_strategy_memory_read": (True, False),
    "keel_strategy_search": (True, False),
    "keel_strategy_status": (True, False),
    "keel_strategy_workspaces": (True, False),
    # Additive writes (create/update — reversible, not destructive)
    "keel_auth_login": (False, False),
    "keel_backtest_run": (False, False),
    "keel_feedback": (False, False),  # appends a feedback row; never fails, never gates
    "keel_strategy_checkout": (False, False),
    "keel_strategy_compose": (False, False),
    "keel_strategy_fork": (False, False),
    "keel_strategy_memory_write": (False, False),
    "keel_strategy_pull": (False, False),
    "keel_strategy_push": (False, False),
    "keel_strategy_restore": (False, False),
    # Destructive / irreversible
    "keel_auth_logout": (False, True),  # wipes the stored session
    "keel_live_control": (False, True),  # pause/resume/stop live capital
    "keel_live_deploy": (False, True),  # deploys live capital
    "keel_share_create": (False, True),  # irreversible public exposure
    "keel_strategy_delete": (False, True),
    "keel_strategy_discard": (False, True),  # deletes local edits
}


def test_catalog_size_matches_expectations():
    """37 tools today. A new tool must be added to EXPECTED_HINTS (and
    a removed one taken out) — that diff is the review event."""
    assert set(OUTCOMES) == set(EXPECTED_HINTS), (
        f"catalog drift — unclassified: {sorted(set(OUTCOMES) - set(EXPECTED_HINTS))}, "
        f"stale entries: {sorted(set(EXPECTED_HINTS) - set(OUTCOMES))}"
    )
    assert len(OUTCOMES) == 37


@pytest.mark.parametrize("name", sorted(EXPECTED_HINTS))
def test_tool_annotations_complete_and_correct(name):
    tool = OUTCOMES[name]
    annotations = tool.annotations
    assert isinstance(annotations, dict), f"{name}: annotations must be a dict"

    # title — present, human-readable, short (host UIs truncate).
    title = annotations.get("title")
    assert isinstance(title, str) and title.strip(), f"{name}: missing title"
    assert len(title) <= 60, f"{name}: title too long for host UIs: {title!r}"
    assert title != name, f"{name}: title must be human-readable, not the tool name"

    # readOnlyHint / destructiveHint — present, boolean, as classified.
    for hint in ("readOnlyHint", "destructiveHint"):
        assert isinstance(annotations.get(hint), bool), f"{name}: {hint} missing or non-bool"

    expected_ro, expected_destr = EXPECTED_HINTS[name]
    assert annotations["readOnlyHint"] is expected_ro, (
        f"{name}: readOnlyHint={annotations['readOnlyHint']} but classified {expected_ro}"
    )
    assert annotations["destructiveHint"] is expected_destr, (
        f"{name}: destructiveHint={annotations['destructiveHint']} but classified {expected_destr}"
    )

    # A tool can't be both read-only and destructive.
    assert not (annotations["readOnlyHint"] and annotations["destructiveHint"]), (
        f"{name}: readOnlyHint and destructiveHint are mutually exclusive"
    )

    # Semantic cross-checks against the scope-gate action.
    action = tool.required_action
    if action.endswith(".delete"):
        assert annotations["destructiveHint"] is True, (
            f"{name}: delete-action tools must be destructive"
        )
    if annotations["readOnlyHint"]:
        assert not action.endswith((".create", ".update", ".delete")), (
            f"{name}: readOnlyHint=True contradicts mutating action {action!r}"
        )


def test_titles_are_unique():
    titles = [t.annotations["title"] for t in OUTCOMES.values()]
    dupes = {x for x in titles if titles.count(x) > 1}
    assert not dupes, f"duplicate titles confuse host tool pickers: {dupes}"


def test_annotations_are_valid_mcp_tool_annotations():
    """Every annotations dict must construct mcp.types.ToolAnnotations —
    the exact object the MCP adapter publishes to tools/list."""
    from mcp.types import ToolAnnotations

    for name, tool in OUTCOMES.items():
        ta = ToolAnnotations(**tool.annotations)
        assert ta.title == tool.annotations["title"], name
        assert ta.readOnlyHint == tool.annotations["readOnlyHint"], name
        assert ta.destructiveHint == tool.annotations["destructiveHint"], name


def test_registered_fastmcp_tools_carry_annotations(monkeypatch):
    """tools/list-visible FastMCP tool objects carry the annotations —
    proving the sweep survives the registration path end-to-end."""
    monkeypatch.setenv("KEEL_TOOLSETS", "read-only,backtest,share,live-read,live-write")
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    from fastmcp import FastMCP
    from keel.tools.outcomes._mcp_adapter import register_all

    mcp = FastMCP(name="annotations-scan")
    register_all(mcp, OUTCOMES)

    import asyncio

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert set(tools) == set(OUTCOMES)
    for name, tool_obj in tools.items():
        ann = tool_obj.annotations
        assert ann is not None, f"{name}: registered tool lost annotations"
        assert ann.title == OUTCOMES[name].annotations["title"], name
        assert ann.readOnlyHint == OUTCOMES[name].annotations["readOnlyHint"], name
        assert ann.destructiveHint == OUTCOMES[name].annotations["destructiveHint"], name
