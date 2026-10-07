"""The first line of every listed tool description (Q-1798).

claude.ai shows the model ONE line per tool until it loads the tool (Part C
probe): the model picks from those lines. A first line that says what the
tool does but not when to pick it over its neighbours sent the agent to the
wrong one — `keel_strategy_get` (a full 415 px card) only to look up v2's
commit id, which `keel_strategy_history` returns with no card.

The first line is what a host that shows "one-line descriptions" shows: the
text up to the first newline or the end of the first sentence, split the
same way the directive scan splits (`assemble._sentences`).

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the
commit. Proof it is not vacuous: every listed tool is read, and every
neighbour group has at least two members on the surface.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS


#: What a one-line row can carry before a host cuts it.
MAX_FIRST_LINE = 200

#: Tools a model confuses, and the neighbours each first line must name —
#: so the one line itself says when to pick this tool over them.
NEIGHBOURS: dict[str, tuple[str, ...]] = {
    "keel_backtest_run": ("keel_backtest_summarize", "keel_backtest_watch"),
    "keel_backtest_watch": ("keel_backtest_run", "keel_backtest_summarize"),
    "keel_backtest_summarize": ("keel_backtest_compare", "keel_backtest_watch"),
    "keel_backtest_compare": ("keel_backtest_summarize",),
    "keel_strategy_get": ("keel_strategy_history",),
    "keel_strategy_search": ("keel_strategy_get",),
    "keel_strategy_history": ("keel_backtest_run", "keel_strategy_diff"),
    "keel_strategy_diff": ("keel_strategy_history", "keel_backtest_compare"),
    # Compose's first line carries the discovery path instead (Q-1753: it
    # must sit in the first 200 bytes); fork's first line routes back here.
    "keel_strategy_compose": ("keel_components_search", "keel_components_get_many"),
    "keel_strategy_fork": ("keel_strategy_compose",),
    # Spec 03 §2.4: restore joined the listed profile (28 tools).
    "keel_strategy_restore": ("keel_strategy_history", "keel_strategy_get"),
    "keel_components_search": ("keel_components_get_many",),
    "keel_components_get_many": ("keel_components_get",),
    "keel_components_get": ("keel_components_get_many",),
    "keel_library_list": ("keel_strategy_search",),
    "keel_library_get": ("keel_library_fork",),
    "keel_library_fork": ("keel_strategy_fork",),
    "keel_account_status": ("keel_plan_usage", "keel_connection_check"),
    "keel_connection_check": ("keel_account_status",),
    "keel_plan_usage": ("keel_account_status",),
    "keel_strategy_notes_read": ("keel_strategy_notes_add",),
    "keel_strategy_notes_add": ("keel_strategy_notes_read",),
}

_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def first_line(description: str) -> str:
    parts = [s.strip() for s in _SPLIT.split(description or "") if s.strip()]
    return parts[0] if parts else ""


@pytest.fixture(scope="module")
def first_lines() -> dict[str, str]:
    saved = {k: os.environ.get(k) for k in ("KEEL_SERVER_PROFILE", "KEEL_TOOLSETS")}
    os.environ["KEEL_SERVER_PROFILE"] = "listed"
    os.environ.pop("KEEL_TOOLSETS", None)
    try:
        from keel.mcp.server import create_server

        tools = asyncio.run(create_server().list_tools())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {t.name: first_line(t.description or "") for t in tools}


def test_every_listed_tool_was_read(first_lines: dict[str, str]) -> None:
    assert set(first_lines) == LISTED_PROFILE_TOOLS
    assert set(NEIGHBOURS) <= LISTED_PROFILE_TOOLS
    for name, neighbours in NEIGHBOURS.items():
        assert set(neighbours) <= LISTED_PROFILE_TOOLS, name


def test_a_first_line_fits_one_row(first_lines: dict[str, str]) -> None:
    # SEED: restore summarize's old first sentence ("Renders the full result
    # for ONE completed run — the card (or its text form) with return, …") —
    # its 290-char first line reds.
    long = {n: len(line) for n, line in first_lines.items() if len(line) > MAX_FIRST_LINE}
    assert not long, f"first lines past {MAX_FIRST_LINE} chars: {long}"
    assert all(len(line) >= 40 for line in first_lines.values()), first_lines


def test_first_lines_are_distinct(first_lines: dict[str, str]) -> None:
    seen: dict[str, str] = {}
    for name, line in sorted(first_lines.items()):
        key = line.lower()
        assert key not in seen, f"{name} and {seen.get(key)} share a first line: {line!r}"
        seen[key] = name


def test_a_first_line_names_the_neighbours_it_is_picked_over(
    first_lines: dict[str, str],
) -> None:
    """The one line a model sees says when to pick this tool over the one it
    is confused with — by naming that tool."""
    # SEED: in strategy_get.py restore "Renders a saved strategy in full — the
    # structure card / markdown at HEAD or at `version`: …" — the get → log
    # routing (Q-1798's commit-id lookup) leaves the first line and this reds.
    missing = {
        name: [n for n in neighbours if n not in first_lines[name]]
        for name, neighbours in NEIGHBOURS.items()
    }
    missing = {name: gone for name, gone in missing.items() if gone}
    assert not missing, f"first lines missing their neighbours: {missing}"
