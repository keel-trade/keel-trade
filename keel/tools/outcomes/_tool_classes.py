"""What KIND of step each outcome tool is, for the agent funnel (spec 01 §3).

The agent-funnel measurement asks where a connector user stops: did they
only orient themselves, look around the catalogue, build something, run it,
read the result, share it, or go live? Each tool call answers that by its
class. This module is the ONE map from tool name to class. The hosted MCP
server stamps the class on every tool-call audit row it forwards
(``services/mcp-server``), and the funnel truth query
(``scripts/analytics/agent_funnel.py``) reads the same map, so the two can
never disagree about what a call was.

It is measurement metadata only. Nothing here reaches a tool's description,
schema, result or instructions: an agent never sees a class.

The map was first written in ``scripts/analytics/agent_funnel.py`` and
reviewed with the founder (2026-10-04): ``keel_backtest_compare`` is
``inspect`` and ``keel_share_create`` is ``share``. It is keyed on the
canonical (registered) names, and also lists every deprecated spelling in
``_toolsets.TOOL_ALIASES`` under its new name's class, because a frozen
connector catalog still calls the old names and historical audit rows carry
them. ``tests/test_tool_classes.py`` fails if any registered tool, on any
toolset or profile (local-only tools included), has no class.
"""

from __future__ import annotations

from typing import Literal, get_args

from ._toolsets import TOOL_ALIASES, canonical_tool_name


ToolClass = Literal["orientation", "discovery", "build", "run", "inspect", "share", "live"]

#: Every class, in funnel order.
TOOL_CLASS_NAMES: tuple[str, ...] = get_args(ToolClass)

#: Class per CANONICAL tool name. Add a row when a tool is registered; the
#: test names any tool without one.
_CANONICAL_CLASSES: dict[str, ToolClass] = {
    # orientation: what is Keel, what does my account have, am I connected
    "keel_help": "orientation",
    "keel_account_status": "orientation",
    "keel_plan_usage": "orientation",
    "keel_connection_check": "orientation",
    "keel_feedback": "orientation",
    "keel_auth_login": "orientation",
    "keel_auth_logout": "orientation",
    "keel_accounts_list": "orientation",
    "keel_audit_list_last": "orientation",
    # discovery: components, library, search
    "keel_components_search": "discovery",
    "keel_components_get": "discovery",
    "keel_components_get_many": "discovery",
    "keel_library_list": "discovery",
    "keel_library_get": "discovery",
    "keel_strategy_search": "discovery",
    # build: create or change a strategy
    "keel_strategy_compose": "build",
    "keel_library_fork": "build",
    "keel_strategy_fork": "build",
    "keel_strategy_restore": "build",
    "keel_strategy_notes_add": "build",
    "keel_strategy_delete": "build",
    "keel_strategy_push": "build",
    "keel_strategy_upgrade": "build",
    "keel_strategy_discard": "build",
    # run: start a backtest
    "keel_backtest_run": "run",
    # inspect: read results or saved work
    "keel_backtest_summarize": "inspect",
    "keel_backtest_positions": "inspect",
    "keel_backtest_watch": "inspect",
    "keel_backtest_compare": "inspect",
    "keel_strategy_get": "inspect",
    "keel_strategy_status": "inspect",
    "keel_strategy_diff": "inspect",
    "keel_strategy_history": "inspect",
    "keel_strategy_notes_read": "inspect",
    "keel_strategy_readiness": "inspect",
    "keel_strategy_checkout": "inspect",
    "keel_strategy_pull": "inspect",
    "keel_strategy_workspaces": "inspect",
    "keel_app_link": "inspect",
    # share
    "keel_share_create": "share",
    # live: deploy, monitor, control, and the exchange account it trades on
    "keel_live_deploy": "live",
    "keel_live_control": "live",
    "keel_live_update": "live",
    "keel_live_monitor": "live",
    "keel_live_quality": "live",
    "keel_live_receipt": "live",
    "keel_deployments_list": "live",
    "keel_accounts_safety": "live",
}

#: Class per tool name: every canonical name, plus every deprecated spelling
#: under the class of the tool it now calls (derived, so the two cannot drift).
TOOL_CLASSES: dict[str, ToolClass] = {
    **_CANONICAL_CLASSES,
    **{
        old: _CANONICAL_CLASSES[new]
        for old, new in TOOL_ALIASES.items()
        if new in _CANONICAL_CLASSES
    },
}


def tool_class(name: str) -> ToolClass | None:
    """The class of tool ``name`` (canonical or a deprecated spelling).

    ``None`` for a name that is not a Keel tool, so a caller records absence
    rather than a guess.
    """
    return TOOL_CLASSES.get(canonical_tool_name(name))


__all__ = ["TOOL_CLASSES", "TOOL_CLASS_NAMES", "ToolClass", "tool_class"]
