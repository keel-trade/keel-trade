"""KEEL_TOOLSETS env parsing.

Spec §4 lines 372-387: agents (and CLI users running MCP) opt into the
live-trading write surface explicitly. Default =
`read-only,backtest,share,live-read`.
The MCP adapter consults this when registering tools; tools whose
toolset isn't in the active set don't appear in `tools/list`.
"""

from __future__ import annotations

import os

from ._base import ALL_TOOLSETS


_DEFAULT_TOOLSETS = frozenset({"always", "read-only", "backtest", "share", "live-read"})
_ALIASES: dict[str, frozenset[str]] = {
    # Backward compatibility for existing MCP host configs. New docs should use
    # `live-write` when they mean deploy/control.
    "live": frozenset({"live-read", "live-write"}),
}


def load_toolsets() -> frozenset[str]:
    """Read `KEEL_TOOLSETS` env, parse, validate.

    `always` is implicit — `keel_status`, `keel_doctor`, `keel_help`
    are always loaded regardless of the env value.
    """
    raw = os.environ.get("KEEL_TOOLSETS")
    if raw is None or not raw.strip():
        return _DEFAULT_TOOLSETS

    parts = {p.strip() for p in raw.split(",") if p.strip()}
    invalid = parts - ALL_TOOLSETS
    if invalid:
        # Fail open to default + warn — never want a typo to lock the agent out
        import logging

        logging.getLogger(__name__).warning(
            "Unknown KEEL_TOOLSETS entries ignored: %s. Valid: %s",
            sorted(invalid),
            sorted(ALL_TOOLSETS),
        )
        parts -= invalid

    expanded: set[str] = set()
    for part in parts:
        expanded.update(_ALIASES.get(part, frozenset({part})))
    if "live-write" in expanded:
        expanded.add("live-read")

    # `always` is implicit
    expanded.add("always")
    return frozenset(expanded)


def is_tool_loaded(tool_toolset: str, active: frozenset[str]) -> bool:
    """Return True if a tool with `tool_toolset` should be exposed
    given the `active` toolset set."""
    return tool_toolset == "always" or tool_toolset in active
