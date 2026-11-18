"""Outcome tool registry — single source of truth for both CLI + MCP.

Each per-family module (`strategy_compose.py`, `backtest_run.py`,
`live_deploy.py`, etc.) adds its `OutcomeTool` instances to `OUTCOMES`
on import. The CLI adapter renders them as Click commands; the MCP
adapter registers them as FastMCP tools.

See `_base.py` for the `OutcomeTool` shape and `projects/agent-v2/
03-ideal-experience-spec.md` §4 for the original tool inventory. The
live registry (`OUTCOMES`, populated by `_bootstrap()`) is the canonical
tool count — never hardcode a number here; docs are drift-gated against
it by `scripts/check_agent_surface_docs.py`.
"""

from __future__ import annotations

from . import _toolsets
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
    # A declared enum or `format: date` is enforced wherever the handler
    # runs — CLI, MCP or a direct call (Q-2273 L5).
    from ._declared_choices import enforce_declared_choices

    object.__setattr__(tool, "handler", enforce_declared_choices(tool, tool.handler))
    OUTCOMES[tool.name] = tool
    return tool


def get(name: str) -> OutcomeTool:
    """Look up a tool by canonical MCP name."""
    if name not in OUTCOMES:
        raise KeyError(f"Unknown outcome tool: {name}")
    return OUTCOMES[name]


def all_tools() -> list[OutcomeTool]:
    """Stable-sorted list (by name) — used by tests and `keel_account_status`."""
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
        accounts_safety,
        audit,
        auth_login,
        auth_logout,
        backtest_compare,
        backtest_positions,
        backtest_run,
        backtest_summarize,
        backtest_watch,
        blocks_get,
        blocks_search,
        components_detail_batch,
        components_help,
        components_search,
        deployments,
        doctor,
        feedback,
        library,
        live_control,
        live_deploy,
        live_monitor,
        live_quality,
        live_receipt,
        live_update,
        open_in_app,
        ownership_status,
        plan_status,
        research_brief,
        research_cancel,
        research_cli,
        research_get,
        research_make_signal,
        research_note,
        research_plan,
        research_run,
        research_view_set,
        screener_action,
        screener_copy,
        screener_describe,
        screener_follow,
        screener_follow_view_set,
        screener_list,
        screener_passing,
        screener_publish,
        screener_refresh,
        screener_run,
        screener_unpublish,
        screener_view,
        screener_view_set,
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
        strategy_upgrade,
        strategy_workspaces,
        use_detach,
        use_preview,
        use_upgrade,
        view_describe,
    )
    from . import help as _help

    # The paper modules register only once the paper SDK is released (S06
    # §3.11 item 1, PT2-28): while `PAPER_SDK_RELEASED` is false the registry,
    # the CLI command tree and every profile's `tools/list` are HEAD's. Read
    # off the module at call time so the `paper_sdk_released` test fixture's
    # setting is the one honoured.
    if _toolsets.PAPER_SDK_RELEASED:
        _register_paper_modules()


#: The paper family modules (S06 §3.2, §3.6), imported by
#: :func:`_register_paper_modules` and only while the paper SDK is released.
#: Each paper tool's task appends its module here as it lands.
_PAPER_MODULES: tuple[str, ...] = ("paper_deploy", "paper_control")


def _register_paper_modules() -> None:
    """Import every module in :data:`_PAPER_MODULES` so its tools register.

    Called by :func:`_bootstrap` when the paper SDK is released, and by the
    `paper_sdk_released` test fixture after it sets the constant. A missing
    module is an ImportError, never a silent gap.
    """
    import importlib

    for name in _PAPER_MODULES:
        importlib.import_module(f"{__name__}.{name}")


def paper_deploy_loaded(active_toolsets: frozenset[str] | None = None) -> bool:
    """Whether this process serves ``keel_paper_deploy`` (S06 §3.9 row 3).

    The operating core's ``paper`` section (``when: paper_loaded``) is
    assembled into the instructions and the served document only when this
    is true. While the paper SDK is held the tool is not registered, so it is
    false on every profile (S06 §3.11 item 1, PT2-28); released, it follows
    the same toolset and profile rules as the tool's own registration.
    ``active_toolsets`` defaults to :func:`load_toolsets`.
    """
    from ._toolsets import is_tool_loaded, load_toolsets

    _bootstrap()
    tool = OUTCOMES.get("keel_paper_deploy")
    if tool is None:  # the held SDK registers no paper tool
        return False
    active = load_toolsets() if active_toolsets is None else active_toolsets
    return is_tool_loaded(
        tool.toolset, active, local_only=tool.local_only, name=tool.name, cli_only=tool.cli_only
    )


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
