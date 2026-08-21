"""Cross-surface routing hints (spec 07 R7 — one-liners, not new tools).

The full option set (web app · listed connector · full hosted endpoint ·
local MCP/CLI · SDK) means users regularly ask one surface for work that
belongs on another. These hints detect the mismatch class for the
CURRENT surface and point across, in one line each. They ship inside
existing outputs (`keel_status`, `keel_doctor`) and the MCP server
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
    "`keel_open_in_app` to open results in the Keel web app."
)

# Hosted (full endpoint): no filesystem — file/workspace asks belong on
# the CLI.
HOSTED_FILES_HINT = (
    "This hosted server is file-free: workspace tools (checkout/push/pull) "
    "are unavailable here. For file-based work, install the CLI: "
    f"`pipx install keel-trade` — per-surface guide: {AGENTS_GUIDE_URL}"
)

# Listed connector: to act on a live strategy, continue in the web app
# (keel_open_in_app); the full local toolset is on the CLI. POLICY-VETTED
# COPY — edit only with a policy-scan pass.
LISTED_LIVE_HINT = (
    "This connector carries research, backtests, and read-only monitoring. "
    "To act on a live strategy, continue in the Keel web app via "
    "`keel_open_in_app`; the full local toolset is on the Keel CLI "
    f"— per-surface guide: {AGENTS_GUIDE_URL}"
)


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
]
