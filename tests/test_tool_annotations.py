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
    # Operator-gated READ of the execution HALT rail (Q-0913). Read-only
    # is load-bearing here: the sibling unhalt POST is deliberately not
    # on this surface, so nothing in the catalog can release a halt.
    "keel_accounts_safety": (True, False),
    "keel_audit_list_last": (True, False),
    "keel_backtest_compare": (True, False),
    "keel_backtest_positions": (True, False),
    "keel_backtest_summarize": (True, False),
    "keel_backtest_watch": (True, False),
    "keel_components_get": (True, False),
    "keel_components_get_many": (True, False),
    "keel_components_search": (True, False),
    "keel_deployments_list": (True, False),
    "keel_connection_check": (True, False),
    "keel_help": (True, False),
    "keel_library_get": (True, False),
    "keel_library_list": (True, False),
    "keel_live_monitor": (True, False),
    "keel_live_quality": (True, False),  # projection of sealed receipts — reads only
    "keel_live_receipt": (True, False),  # one sealed episode receipt — reads only
    "keel_app_link": (True, False),  # navigation link builder — no API call
    "keel_strategy_readiness": (True, False),
    "keel_plan_usage": (True, False),  # plan/limits/remaining facts — numbers only
    "keel_account_status": (True, False),
    "keel_strategy_diff": (True, False),
    "keel_strategy_get": (True, False),
    "keel_strategy_history": (True, False),
    "keel_strategy_notes_read": (True, False),
    "keel_strategy_search": (True, False),
    "keel_strategy_status": (True, False),
    "keel_strategy_workspaces": (True, False),
    # Additive writes (create/update — reversible, not destructive)
    "keel_auth_login": (False, False),
    "keel_backtest_run": (False, False),
    "keel_library_fork": (False, False),  # creates a strategy — same class as keel_strategy_fork
    # Mints a signed update-intent link (a handoff producer, spec 04 §4) —
    # it applies nothing and accepts no config, so it is additive, not
    # destructive; the destructive act (the update) is the human's, in-app.
    "keel_live_update": (False, False),
    "keel_strategy_checkout": (False, False),
    "keel_strategy_compose": (False, False),
    "keel_strategy_fork": (False, False),
    "keel_strategy_notes_add": (False, False),
    "keel_strategy_pull": (False, False),
    "keel_strategy_push": (False, False),
    "keel_strategy_restore": (False, False),
    # Position-layer upgrade (spec 04-R21): an applied upgrade is a NEW
    # version; the dry run writes nothing; nothing is deleted.
    "keel_strategy_upgrade": (False, False),
    # Destructive / irreversible
    "keel_auth_logout": (False, True),  # wipes the stored session
    # A delivered note cannot be recalled (the append-only rule, Q-2080):
    # it is not a write the caller can read back and move on from.
    "keel_feedback": (False, True),
    "keel_live_control": (False, True),  # pause/resume/stop live capital
    "keel_live_deploy": (False, True),  # deploys live capital
    # Public disclosure cannot be retracted from recipients (Q-2080; reverses Q-1506).
    "keel_share_create": (False, True),
    "keel_strategy_delete": (False, True),
    "keel_strategy_discard": (False, True),  # deletes local edits
}


def test_catalog_size_matches_expectations():
    """48 tools today (Q-1893 added keel_backtest_positions, Q-2448
    keel_strategy_upgrade). A new tool
    must be added to EXPECTED_HINTS (and a removed one taken out) — that
    diff is the review event."""
    assert set(OUTCOMES) == set(EXPECTED_HINTS), (
        f"catalog drift — unclassified: {sorted(set(OUTCOMES) - set(EXPECTED_HINTS))}, "
        f"stale entries: {sorted(set(EXPECTED_HINTS) - set(OUTCOMES))}"
    )
    assert len(OUTCOMES) == 48


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


# Canonical classification, second pair: name -> (idempotentHint,
# openWorldHint). Both hints are already set on every tool and no test
# pinned them, so a drift was invisible (mcp-strategy-view 02 §2: the
# directory review reads all four). Same rule as the table above —
# changing a tool's classification must show up in this file's diff.
#
# idempotentHint: "calling this again with the same arguments changes
# nothing new". openWorldHint (rule founder-approved 2026-09-25, from
# OpenAI's plugin guidance): True only when the tool reaches the public
# internet / open-ended external entities (a live venue) or changes
# publicly visible state (a public share link). A tool bounded to the
# caller's own Keel account/workspace via Keel's API is False, even though
# Keel is externally hosted. The listed-surface guard and the per-hint
# written grounds live in test_listed_surface_annotations.py +
# keel/tools/outcomes/_annotation_justifications.py.
EXPECTED_BEHAVIOUR_HINTS: dict[str, tuple[bool, bool, str]] = {
    # name: (idempotentHint, openWorldHint, the written reason for both).
    # The reason is the review surface (Q-1748): a hint with no stated
    # ground is how `keel_strategy_fork` shipped idempotentHint=True while
    # every call created a new strategy. Q-1748's open question (the
    # openWorldHint column applied no single rule) is closed by the rule
    # stated above the table.
    "keel_accounts_list": (True, False, "read; reads the Keel API"),
    "keel_accounts_safety": (True, False, "read of the HALT rail; reads the Keel API"),
    "keel_audit_list_last": (True, False, "read; reads the Keel API"),
    "keel_auth_login": (
        True,
        True,
        "re-login lands in the same signed-in state; drives a browser OAuth flow",
    ),
    "keel_auth_logout": (
        True,
        False,
        "clearing an already-cleared session is a no-op; local token store only",
    ),
    "keel_backtest_compare": (True, False, "read; reads the Keel API"),
    "keel_backtest_positions": (True, False, "read; reads the Keel API"),
    "keel_backtest_run": (False, False, "every call queues a NEW run and spends quota"),
    "keel_backtest_summarize": (True, False, "read; reads the Keel API"),
    "keel_backtest_watch": (True, False, "read (polls); reads the Keel API"),
    "keel_components_get": (True, False, "read of the bundled component registry"),
    "keel_components_get_many": (True, False, "read of the bundled component registry"),
    "keel_components_search": (True, False, "read of the bundled component registry"),
    "keel_deployments_list": (True, False, "read; reads the Keel API"),
    "keel_connection_check": (True, False, "read-only diagnosis"),
    "keel_feedback": (
        False,
        True,
        "every call appends a new feedback row; the note leaves the workspace for the "
        "Keel team and an analytics event is recorded (OpenAI scan, 2026-09-30)",
    ),
    "keel_help": (True, False, "read of bundled knowledge"),
    "keel_library_fork": (False, False, "every call creates a NEW strategy; own workspace"),
    "keel_library_get": (True, False, "read; Keel's own curated library via the Keel API"),
    "keel_library_list": (True, False, "read; Keel's own curated library via the Keel API"),
    "keel_live_control": (
        False,
        True,
        "acts on a live venue deployment; each call is a new command",
    ),
    "keel_live_deploy": (
        False,
        True,
        "deploys to a live venue; each call is a new deployment attempt",
    ),
    "keel_live_monitor": (
        True,
        True,
        "read; the positions view is a live read of the caller's linked Hyperliquid "
        "account, an exchange Keel does not control (OpenAI scan, 2026-10-01)",
    ),
    "keel_live_quality": (True, False, "read of sealed receipts; reads the Keel API"),
    "keel_live_receipt": (True, False, "read of one sealed receipt; reads the Keel API"),
    "keel_live_update": (False, True, "every call mints a NEW signed update-intent link"),
    "keel_app_link": (True, False, "builds a link; no API call"),
    "keel_strategy_readiness": (True, False, "read; reads the Keel API"),
    "keel_plan_usage": (True, False, "read; reads the Keel API"),
    "keel_share_create": (False, True, "every call creates a NEW public share link"),
    "keel_account_status": (True, False, "read; reads the Keel API"),
    "keel_strategy_checkout": (
        False,
        False,
        "writes a local workspace from the server; not asserted repeatable",
    ),
    "keel_strategy_compose": (
        False,
        False,
        "without strategy_id every call creates a NEW strategy; every save a new version",
    ),
    "keel_strategy_delete": (False, False, "hard delete; a second call finds nothing (spec §4)"),
    "keel_strategy_diff": (True, False, "read; reads the Keel API"),
    "keel_strategy_discard": (
        True,
        False,
        "discarding already-discarded local edits is a no-op; local files only",
    ),
    "keel_strategy_fork": (
        False,
        True,
        "every call creates a NEW strategy (the Q-1748 finding); `source` accepts any "
        "public share link, so the copied strategy can be another organisation's",
    ),
    "keel_strategy_get": (True, False, "read; reads the Keel API"),
    "keel_strategy_history": (True, False, "read; reads the Keel API"),
    "keel_strategy_notes_read": (True, False, "read; reads the Keel API"),
    "keel_strategy_notes_add": (False, False, "every call appends a memory entry"),
    "keel_strategy_pull": (True, False, "re-pulling an unchanged HEAD leaves the same files"),
    "keel_strategy_push": (False, False, "every push of new edits is a new version"),
    "keel_strategy_restore": (False, False, "every restore writes a new HEAD version"),
    "keel_strategy_upgrade": (
        False,
        False,
        "every applied upgrade writes a new HEAD version; reads and writes the Keel API only",
    ),
    "keel_strategy_search": (True, False, "read; reads the Keel API"),
    "keel_strategy_status": (True, False, "read of local + server state"),
    "keel_strategy_workspaces": (True, False, "read of local workspace directories"),
}


def test_behaviour_hint_table_covers_the_catalog():
    """Not vacuous: the second table classifies exactly the same 48 tools
    as the first, so a new tool cannot slip past it either."""
    assert set(EXPECTED_BEHAVIOUR_HINTS) == set(EXPECTED_HINTS) == set(OUTCOMES)


@pytest.mark.parametrize("name", sorted(EXPECTED_BEHAVIOUR_HINTS))
def test_idempotent_and_open_world_hints_are_pinned(name):
    """Spec 01 R5 + mcp-strategy-view 02 §2: all four annotation hints
    ship on every tool, and all four are reviewed. `openWorldHint` and
    `idempotentHint` were set everywhere and pinned nowhere."""
    # SEED: flip any one row of EXPECTED_BEHAVIOUR_HINTS (e.g.
    # keel_live_deploy -> (True, False)) — reds exactly that tool's
    # parametrisation and nothing else.
    annotations = OUTCOMES[name].annotations
    for hint in ("idempotentHint", "openWorldHint"):
        assert isinstance(annotations.get(hint), bool), f"{name}: {hint} missing"

    expected_idem, expected_open, _why = EXPECTED_BEHAVIOUR_HINTS[name]
    assert annotations["idempotentHint"] is expected_idem, (
        f"{name}: idempotentHint={annotations['idempotentHint']} but classified {expected_idem}"
    )
    assert annotations["openWorldHint"] is expected_open, (
        f"{name}: openWorldHint={annotations['openWorldHint']} but classified {expected_open}"
    )


def test_every_behaviour_hint_carries_a_written_reason():
    """Q-1748: a pinned hint with no stated ground is how a wrong one
    shipped. Every row states why."""
    blank = [n for n, (_i, _o, why) in EXPECTED_BEHAVIOUR_HINTS.items() if not why.strip()]
    assert not blank, f"hints with no written reason: {blank}"


def test_a_create_action_is_never_idempotent():
    """Q-1748: `keel_strategy_fork` and `keel_strategy_compose` (both
    `strategy.create`) claimed idempotentHint=True while every call made a
    new strategy — a false hint a host's approval classifier reads. A tool
    whose scope-gate action CREATES something makes a new thing per call.

    # SEED: set keel_strategy_fork's idempotentHint back to True in
    # strategy_fork.py — this test and its pinned row both red.
    """
    creators = sorted(n for n, t in OUTCOMES.items() if t.required_action.endswith(".create"))
    # Non-vacuous, read from the registry: the create-action tools exist.
    assert {"keel_strategy_fork", "keel_strategy_compose", "keel_backtest_run"} <= set(creators)
    wrong = [n for n in creators if OUTCOMES[n].annotations["idempotentHint"] is not False]
    assert not wrong, f"create-action tools claiming idempotentHint=True: {wrong}"


def test_all_four_hints_survive_the_mcp_annotation_object():
    """The exact object tools/list publishes carries all four — a hint
    the adapter drops is a hint the directory never sees."""
    from mcp.types import ToolAnnotations

    checked = 0
    for name, tool in OUTCOMES.items():
        ta = ToolAnnotations(**tool.annotations)
        assert ta.idempotentHint == tool.annotations["idempotentHint"], name
        assert ta.openWorldHint == tool.annotations["openWorldHint"], name
        checked += 1
    assert checked == len(OUTCOMES) == 48
