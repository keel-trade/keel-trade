"""Cross-surface routing hints (spec 07 R7 — one-liners, not new tools).

The full option set (web app · the one hosted endpoint, pasted or
installed from a directory · local MCP/CLI · SDK) means users regularly ask one surface for work that
belongs on another. These hints detect the mismatch class for the
CURRENT surface and point across, in one line each. They ship inside
existing outputs (`keel_account_status`, `keel_connection_check`) and the MCP server
instructions — never as new tools.

Listed-profile strings are policy-vetted copy (research/08 rules: no
deploy/fund/trade verb families, no upsell language) — the policy scan
(tests/test_policy_scan.py) gates the server instructions built from
these, and tests/test_surface_hints.py string-tests all three variants.
"""

from __future__ import annotations


AGENTS_GUIDE_URL = "https://usekeel.io/agents"
FULL_ENDPOINT_URL = "https://mcp.usekeel.io/mcp"

# Local CLI / local MCP: the visual loop lives in the web app.
LOCAL_CHARTS_HINT = (
    "Charts and visual review: run `keel open backtest <id>` (CLI) or call "
    "`keel_app_link` to open results in the Keel web app."
)

# Hosted endpoint: no filesystem — file/workspace asks belong on the CLI.
HOSTED_FILES_HINT = (
    "This hosted server is file-free: workspace tools (checkout/push/pull) "
    "are unavailable here. For file-based work, install the CLI: "
    f"`pipx install keel-trade` — per-surface guide: {AGENTS_GUIDE_URL}"
)

# Listed connector: to act on a live strategy, continue in the web app
# (keel_app_link). It names no other client or install (Q-2268, round 3: a
# directory connector's results point at the web app, not at a CLI the host
# cannot run). POLICY-VETTED COPY — edit only with a policy-scan pass.
LISTED_LIVE_HINT = (
    "This connector carries research, backtests, and read-only monitoring. "
    "To act on a live strategy, continue in the Keel web app via "
    "`keel_app_link`."
)


def tool_ref(tool: str) -> str:
    """How THIS surface names another outcome, in backticks (Q-1743).

    One outcome is two spellings: the MCP tool ``keel_backtest_run`` and the
    CLI command ``keel backtest run``. Copy that names the CLI spelling to an
    MCP agent sends it looking for a tool that does not exist (a hosted
    connector has no shell at all), so recovery copy names the outcome
    through here. The CLI spelling is read from the tool's own ``cli_path``
    (what ``_cli_adapter.register_all`` mounts), never re-derived.
    """
    from keel.surface import current_surface

    from . import OUTCOMES

    if current_surface() == "cli":
        path = OUTCOMES[tool].cli_path
        return "`keel " + " ".join(p.replace("_", "-") for p in path) + "`"
    return f"`{tool}`"


def usage_hint(cli: str, mcp: str) -> str:
    """``cli`` on the CLI surface, ``mcp`` on every MCP surface (Q-2273 L4).

    A usage example written as a command (`keel backtest compare a b`) is
    right in a terminal and a call the model cannot make anywhere else; the
    MCP spelling names the tool's arguments instead.
    """
    from keel.surface import current_surface

    return cli if current_surface() == "cli" else mcp


def strategy_ids_hint() -> str:
    """Where strategy ids come from, naming only tools this server has:
    `keel_strategy_search` everywhere, `keel_strategy_workspaces` only where
    the local tools are registered (spec 05 R-L4)."""
    from ._toolsets import local_tools_registered

    if local_tools_registered():
        return (
            "Find ids via `keel_strategy_search`, or `keel_strategy_workspaces` "
            "for locally checked-out ones."
        )
    return "`keel_strategy_search` lists your strategy ids."


def surface_hints() -> list[str]:
    """The cross-surface hints for the currently-running surface."""
    from keel.hosting import is_hosted

    from ._toolsets import is_listed_profile

    if is_listed_profile():
        return [LISTED_LIVE_HINT]
    if is_hosted():
        return [HOSTED_FILES_HINT]
    return [LOCAL_CHARTS_HINT]


__all__ = [
    "AGENTS_GUIDE_URL",
    "FULL_ENDPOINT_URL",
    "HOSTED_FILES_HINT",
    "LISTED_LIVE_HINT",
    "LOCAL_CHARTS_HINT",
    "surface_hints",
    "tool_ref",
]
