"""Every Keel tool has exactly one funnel class (agent-funnel-measurement 1.1).

`keel.tools.outcomes._tool_classes.TOOL_CLASSES` is the single map the hosted
MCP server stamps on each tool-call audit row and the funnel truth query
reads. A tool with no class is a call the funnel files as "unclassified", so
the map must cover:

* every tool in `OUTCOMES`, the registry `_mcp_adapter.register_all` and the
  CLI adapter both serve from, on every toolset and profile, local-only tools
  included (not just `LISTED_PROFILE_TOOLS`);
* every deprecated spelling in `TOOL_ALIASES`, under the class of the tool it
  now calls.

And it must hold nothing else: a stale name would be a class for a call that
cannot happen.

Proof it can fail (run 2026-10-04): delete the `"keel_live_receipt"` row from
`_CANONICAL_CLASSES` — `test_every_registered_tool_has_a_class` reds naming it,
and `test_every_alias_has_its_canonical_class` stays green (control). Delete
the `keel_strategy_readiness` row instead — both red, the alias test naming
`keel_ownership_status`. Add a `"keel_live_status"` row (the analytics map's
old guess; never a registered tool) — `test_the_map_names_only_tools_that_exist`
reds naming it, the coverage tests stay green.
"""

from __future__ import annotations

from typing import get_args

from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._tool_classes import (
    TOOL_CLASS_NAMES,
    TOOL_CLASSES,
    ToolClass,
    tool_class,
)
from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS, TOOL_ALIASES


_bootstrap()


def test_the_registry_is_the_whole_surface():
    """Non-vacuity: the sweep below covers the full registry, not a subset.

    The registry holds local-only tools (auth, push/pull) and live-write
    tools that the listed profile never serves; if it held only the listed
    surface, the coverage test would prove nothing about them.
    """
    assert len(OUTCOMES) >= 40
    local_only = {n for n, t in OUTCOMES.items() if t.local_only}
    assert {"keel_auth_login", "keel_strategy_push"} <= local_only
    assert set(OUTCOMES) - LISTED_PROFILE_TOOLS, "registry is only the listed surface"
    assert len(TOOL_ALIASES) >= 10


def test_every_registered_tool_has_a_class():
    missing = sorted(n for n in OUTCOMES if n not in TOOL_CLASSES)
    assert not missing, f"registered tools with no funnel class: {missing}"


def test_every_alias_has_its_canonical_class():
    wrong = {
        old: (TOOL_CLASSES.get(old), TOOL_CLASSES.get(new))
        for old, new in TOOL_ALIASES.items()
        if old not in TOOL_CLASSES or TOOL_CLASSES[old] != TOOL_CLASSES.get(new)
    }
    assert not wrong, f"deprecated names without their tool's class: {wrong}"


def test_the_map_names_only_tools_that_exist():
    stale = sorted(set(TOOL_CLASSES) - set(OUTCOMES) - set(TOOL_ALIASES))
    assert not stale, f"classes for names that are neither a tool nor an alias: {stale}"


def test_every_class_is_one_of_the_seven():
    assert TOOL_CLASS_NAMES == get_args(ToolClass)
    assert TOOL_CLASS_NAMES == (
        "orientation",
        "discovery",
        "build",
        "run",
        "inspect",
        "share",
        "live",
    )
    assert set(TOOL_CLASSES.values()) <= set(TOOL_CLASS_NAMES)
    # Every class is used, so none is a dead option the funnel will never see.
    assert set(TOOL_CLASSES.values()) == set(TOOL_CLASS_NAMES)


def test_founder_reviewed_rows():
    """The two rulings made when the map was reviewed (2026-10-04)."""
    assert tool_class("keel_backtest_compare") == "inspect"
    assert tool_class("keel_share_create") == "share"
    assert tool_class("keel_backtest_run") == "run"


def test_tool_class_canonicalises_and_refuses_unknown_names():
    assert tool_class("keel_ownership_status") == "inspect"
    assert tool_class("keel_strategy_readiness") == "inspect"
    assert tool_class("keel_strategy_log") == "inspect"
    assert tool_class("keel_not_a_tool") is None
    assert tool_class("") is None
