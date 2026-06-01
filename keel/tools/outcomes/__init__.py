"""Outcome tool registry — single source of truth for both CLI + MCP.

Each per-family module (`strategy_compose.py`, `backtest_run.py`,
`live_deploy.py`, etc.) adds its `OutcomeTool` instances to `OUTCOMES`
on import. The CLI adapter renders them as Click commands; the MCP
adapter registers them as FastMCP tools.

See `_base.py` for the `OutcomeTool` shape and `projects/agent-v2/
03-ideal-experience-spec.md` §4 for the canonical inventory of 14
primary + 8 auxiliary tools.
"""

from __future__ import annotations

from ._base import OutcomeResult, OutcomeTool, ToolContext, envelope_error
from ._toolsets import load_toolsets


# Mutable registry; per-family modules append to it on import.
OUTCOMES: dict[str, OutcomeTool] = {}


def register(tool: OutcomeTool) -> OutcomeTool:
    """Add a tool to the registry. Returns the tool so callers can
    chain (`X = register(OutcomeTool(...))`)."""
    if tool.name in OUTCOMES:
        raise ValueError(
            f"Duplicate OutcomeTool name: {tool.name!r}. Each tool must register exactly once."
        )
    OUTCOMES[tool.name] = tool
    return tool


def get(name: str) -> OutcomeTool:
    """Look up a tool by canonical MCP name."""
    if name not in OUTCOMES:
        raise KeyError(f"Unknown outcome tool: {name}")
    return OUTCOMES[name]


def all_tools() -> list[OutcomeTool]:
    """Stable-sorted list (by name) — used by tests and `keel_status`."""
    return [OUTCOMES[n] for n in sorted(OUTCOMES)]


def _bootstrap() -> None:
    """Import every per-family module so they self-register on first
    access to `OUTCOMES`. Called lazily by `all_tools()` and by the
    adapters.

    Modules to import are listed explicitly (not auto-discovered) so
    the load order is deterministic and so missing modules surface as
    ImportErrors at startup rather than silent gaps.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    # ruff: noqa: F401 — imports register tools as side effect.
    # Modules added by the family fan-out land here as they ship.
    # Family modules — added incrementally as the fan-out completes.
    # Order is the §4 inventory in the spec.
    from . import (  # always-loaded pilot trio  # components family
        accounts,
        audit,
        auth_login,
        auth_logout,
        backtest_run,
        backtest_summarize,
        backtest_watch,
        components_detail_batch,
        components_help,
        components_search,
        doctor,
        live_control,
        live_deploy,
        live_monitor,
        share_create,
        status,
        strategy_checkout,
        strategy_compose,
        strategy_delete,
        strategy_diff,
        strategy_discard,
        strategy_fork,
        strategy_get,
        strategy_log,
        strategy_memory,
        strategy_pull,
        strategy_push,
        strategy_restore,
        strategy_search,
        strategy_status,
        strategy_workspaces,
    )
    from . import help as _help


_BOOTSTRAPPED = False


__all__ = [
    "OUTCOMES",
    "OutcomeResult",
    "OutcomeTool",
    "ToolContext",
    "all_tools",
    "envelope_error",
    "get",
    "load_toolsets",
    "register",
]
