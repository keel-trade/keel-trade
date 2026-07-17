"""KEEL_TOOLSETS env parsing + KEEL_SERVER_PROFILE (spec 01 R3).

Spec §4 lines 372-387: agents (and CLI users running MCP) opt into the
live-trading write surface explicitly. Default =
`read-only,backtest,share,live-read`.
The MCP adapter consults this when registering tools; tools whose
toolset isn't in the active set don't appear in `tools/list`.

Server profiles (spec 01 R3 — two registrations of one image):

* ``full`` (default; unlisted endpoint) — no additional restriction on
  top of KEEL_TOOLSETS + the hosted local_only exclusion.
* ``listed`` (directory registration) — the tool surface is EXACTLY
  :data:`LISTED_PROFILE_TOOLS`, independent of ``KEEL_TOOLSETS``. The
  directory-reviewed surface must be deterministic: an env typo must
  never widen (or quietly vary) what a listed connector exposes, so
  this is an explicit allow-list, fail-closed for any new tool until
  it is deliberately added AND passes the policy scan
  (tests/test_policy_scan.py — the research/08 string rules).
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


SERVER_PROFILE_ENV = "KEEL_SERVER_PROFILE"
_VALID_PROFILES = ("full", "listed")

# ── Listed-client brand (spec 04 R2 — per-surface upsell-link policy) ────
# A LISTED registration is reviewed under one directory's policy, and the
# policies differ on indirect subscription upsells: OpenAI's usage policy
# bans selling digital subscriptions "directly or indirectly (for example,
# through freemium upsells)" (research/08 §2), while Anthropic supports
# owned-domain link-outs. Deployments declare which directory a listed
# registration serves via KEEL_LISTED_CLIENT so tools like
# `keel_plan_status` can include or omit billing/manage links accordingly.
# UNSET on a listed profile means SUPPRESSED — a new directory must never
# inherit upsell links by default (fail-safe). Irrelevant on `full`.
LISTED_CLIENT_ENV = "KEEL_LISTED_CLIENT"
_VALID_LISTED_CLIENTS = ("chatgpt", "claude")

# The directory-listable research/backtest/read surface (spec 01 R3).
# EXCLUDED by construction: keel_live_deploy, keel_live_control,
# keel_strategy_delete, every local_only tool, and anything with
# money-movement parameter semantics. Additions require a matching
# policy-scan pass (tests/test_policy_scan.py).
LISTED_PROFILE_TOOLS: frozenset[str] = frozenset(
    {
        # always-on basics
        "keel_status",
        "keel_doctor",
        "keel_help",
        # feedback capture (spec 02 R4 — toolset `always`, never fails,
        # nothing gates on it; must be fileable from every profile)
        "keel_feedback",
        # components
        "keel_components_search",
        "keel_components_compose_help",
        "keel_components_detail_batch",
        # compose / validate
        "keel_strategy_compose",
        # backtest run / results
        "keel_backtest_run",
        "keel_backtest_summarize",
        "keel_backtest_watch",
        # strategy read / history / fork / memory
        "keel_strategy_get",
        "keel_strategy_log",
        "keel_strategy_search",
        "keel_strategy_fork",
        "keel_strategy_memory_read",
        "keel_strategy_memory_write",
        # share + read-only live monitoring + ownership
        "keel_share_create",
        "keel_live_monitor",
        "keel_ownership_status",
        # plan facts (spec 04 R2 — read-only numbers; its manage_url is
        # additionally gated per listed client, see manage_links_allowed)
        "keel_plan_status",
        # navigation bridge into the web app (spec 01 R4 — the ONLY
        # app bridge on the listed profile)
        "keel_open_in_app",
    }
)


def server_profile() -> str:
    """Return the active server profile: ``"full"`` or ``"listed"``.

    Raises ``ValueError`` on any other value — a typo'd profile must
    never silently fall back to the wider ``full`` surface (same
    no-silent-downgrade rule as ``keel.hosting.execution_mode``).
    """
    raw = os.environ.get(SERVER_PROFILE_ENV, "").strip().lower()
    if not raw:
        return "full"
    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"Invalid {SERVER_PROFILE_ENV}={raw!r}. Valid values: {', '.join(_VALID_PROFILES)}."
        )
    return raw


def is_listed_profile() -> bool:
    return server_profile() == "listed"


def listed_client() -> str | None:
    """The directory brand a LISTED registration serves, or ``None``.

    Valid values: ``"chatgpt"``, ``"claude"``, or unset (``None``).
    Raises ``ValueError`` on anything else — a typo'd brand must never
    silently pick a policy branch (same rule as :func:`server_profile`).
    """
    raw = os.environ.get(LISTED_CLIENT_ENV, "").strip().lower()
    if not raw:
        return None
    if raw not in _VALID_LISTED_CLIENTS:
        raise ValueError(
            f"Invalid {LISTED_CLIENT_ENV}={raw!r}. "
            f"Valid values: {', '.join(_VALID_LISTED_CLIENTS)} (or unset)."
        )
    return raw


def manage_links_allowed() -> bool:
    """Per-surface manage/billing-link policy (spec 04 R2).

    ``True`` on every full-profile surface (CLI, local MCP, the unlisted
    hosted endpoint) and on a listed registration explicitly declared
    ``KEEL_LISTED_CLIENT=claude``. ``False`` — links suppressed, facts
    only — on a listed registration declared ``chatgpt`` (the
    indirect-upsell clause, research/08 §2) AND on any listed
    registration with no declared client: suppression is the safe
    default a new directory starts from.
    """
    if not is_listed_profile():
        return True
    return listed_client() == "claude"


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


def is_tool_loaded(
    tool_toolset: str,
    active: frozenset[str],
    *,
    local_only: bool = False,
    name: str = "",
) -> bool:
    """Return True if a tool with `tool_toolset` should be exposed
    given the `active` toolset set.

    ``local_only`` tools (workspace checkout/push/pull/status/discard/
    workspaces + auth login/logout — anything bound to the user's own
    filesystem or browser) are excluded when the SDK runs in hosted
    execution mode (spec 01 R2). The exclusion lives HERE, in the
    toolset machinery, so CLI/local behavior is untouched and no
    server-side ad-hoc filtering can drift from it.

    Under the ``listed`` server profile (spec 01 R3) the surface is
    EXACTLY :data:`LISTED_PROFILE_TOOLS` — ``KEEL_TOOLSETS`` is not
    consulted, so the directory-reviewed registration can never vary
    with toolset env. ``name`` is required for the profile check; an
    empty name under ``listed`` fails closed.
    """
    if local_only:
        from keel.hosting import is_hosted

        if is_hosted():
            return False
    if is_listed_profile():
        return name in LISTED_PROFILE_TOOLS
    return tool_toolset == "always" or tool_toolset in active
