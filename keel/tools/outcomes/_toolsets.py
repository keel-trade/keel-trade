"""KEEL_TOOLSETS env parsing + KEEL_SERVER_PROFILE (spec 01 R3).

Spec §4 lines 372-387: agents (and CLI users running MCP) opt into the
live-trading write surface explicitly. Default =
`read-only,backtest,share,live-read`.
The MCP adapter consults this when registering tools; tools whose
toolset isn't in the active set don't appear in `tools/list`.

Server profiles (spec 01 R3; D1 2026-07-19 — ONE hosted endpoint):

* ``full`` (default) — the CLI + local ``keel mcp serve`` surface: no
  additional restriction on top of KEEL_TOOLSETS + the hosted
  local_only exclusion. This is NOT a hosted endpoint. Since the D1
  cutover there is no separate "full" hosted registration; live-write
  and local-file tools live on the local/CLI surface only.
* ``listed`` — THE single hosted profile. The one ``mcp.usekeel.io``
  registration serves BOTH the paste-URL custom connector AND the
  directory listing; its tool surface is EXACTLY
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

# ── Renamed tools: the old names stay CALLABLE, never LISTED ─────────────
# Renamed 2026-10-01 (Q-2080, OpenAI's plugin tool scan: "name appears
# unclear or does not communicate its purpose" on keel_ownership_status,
# and the same class on doctor / compose_help / detail_batch / memory_* /
# plan_status / status / open_in_app / strategy_log). `tools/list`
# advertises ONLY the new names on every profile; `tools/call` with an old
# name still runs the renamed tool, on the hosted endpoint and on a local
# stdio server alike (`keel.mcp.server.KeelMCP.call_tool` canonicalises
# before dispatch, so the gate, the audit row and the metrics all see the
# new name). A host that froze its catalog before the rename keeps working.
#
# DEPRECATED since 2026-10-01: the old names are removed no earlier than
# 2027-01-01 (one quarter), so every frozen connector catalog has been
# refreshed. Nothing Keel serves — no description, instruction, skill or
# doc — names an old spelling (tests/test_surface_layers.py G6 proves it).
TOOL_ALIASES: dict[str, str] = {
    "keel_ownership_status": "keel_strategy_readiness",
    "keel_doctor": "keel_connection_check",
    "keel_components_compose_help": "keel_components_get",
    "keel_components_detail_batch": "keel_components_get_many",
    "keel_strategy_memory_read": "keel_strategy_notes_read",
    "keel_strategy_memory_write": "keel_strategy_notes_add",
    "keel_plan_status": "keel_plan_usage",
    "keel_status": "keel_account_status",
    "keel_open_in_app": "keel_app_link",
    "keel_strategy_log": "keel_strategy_history",
}

#: Renamed PARAMETERS, per tool: old argument name → new. Same rule as the
#: tool aliases — the schema advertises only the new name; a call carrying
#: the old one is rewritten before validation (`_mcp_adapter._make_handler`),
#: and the CLI keeps the old flag as a hidden alias (`_cli_adapter`).
#: The OLD name wins when a call carries both (Q-2267): FastMCP's synthesized
#: signature hands the handler the new name's schema DEFAULT whether or not
#: the caller sent it, so "new wins" silently dropped every old-name call.
PARAM_ALIASES: dict[str, dict[str, str]] = {
    "keel_strategy_get": {"no_ownership_hint": "skip_readiness"},
    "keel_strategy_status": {"no_ownership_hint": "skip_readiness"},
    "keel_backtest_run": {"no_ownership_hint": "skip_readiness"},
    "keel_backtest_watch": {"no_ownership_hint": "skip_readiness"},
}

#: LISTED-ONLY renamed parameters: an input the listed schema omits in favour
#: of an equivalent one it keeps. A frozen ChatGPT/claude.ai catalog made
#: before the omission still sends the old input; dropping it would run
#: something other than what was asked (a `commit_id`-pinned backtest ran
#: HEAD — found by the final functionality check, 2026-10-01), so it is
#: rewritten to the kept input instead. `version` accepts a commit id. The
#: full/local profile keeps the old input as a real parameter.
LISTED_PARAM_ALIASES: dict[str, dict[str, str]] = {
    "keel_backtest_run": {"commit_id": "version"},
}

#: RETIRED arguments, per tool: inputs a schema no longer declares on ANY
#: profile but a call may still carry, accepted and dropped without a word
#: (Q-2264 removed them; Q-2267 keeps the frozen catalogs working). ChatGPT
#: and claude.ai freeze a connector's catalog at connect time, so a connector
#: made before the removal still sends `tag` / `owner` / `share_id` to
#: `keel_strategy_search`; refusing them as `unexpected argument(s)` would
#: break every such connector for inputs that never matched anything. The
#: listed schema's own omissions (`_base.LISTED_SCHEMA_OMISSIONS`) are the
#: listed-only twin of this table; the CLI generates no option for either.
RETIRED_ARGUMENTS: dict[str, frozenset[str]] = {
    "keel_strategy_search": frozenset({"tag", "owner", "share_id"}),
}


def canonical_tool_name(name: str) -> str:
    """The registered name for ``name`` — itself, or the tool an old
    spelling now calls. Unknown names pass through untouched, so the
    server's own not-found path answers them."""
    return TOOL_ALIASES.get(name, name)


def aliases_of(canonical: str) -> frozenset[str]:
    """Every deprecated spelling that still calls ``canonical``."""
    return frozenset(old for old, new in TOOL_ALIASES.items() if new == canonical)


# ── Listed-client brand — RETAINED, UNUSED (mcp-conversion D-1) ─────────
# A LISTED registration is reviewed under one directory's policy, and the
# policies differ on indirect subscription upsells: OpenAI's usage policy
# bans selling digital subscriptions "directly or indirectly (for example,
# through freemium upsells)" (research/08 §2), while Anthropic supports
# owned-domain link-outs. This env var existed so `keel_plan_usage` could
# include or omit its manage link per directory.
#
# Since 2026-09-19 NO policy reads it, and since D-12 (2026-09-28) no
# surface serves a plan link at all (see `manage_links_allowed` below). The
# variable stays declared and validated as a documented escape hatch for a
# future per-brand endpoint. Irrelevant on `full`, and on `listed` too.
LISTED_CLIENT_ENV = "KEEL_LISTED_CLIENT"
_VALID_LISTED_CLIENTS = ("chatgpt", "claude")

# THE single hosted profile's tool surface (spec 01 R3; D1 2026-07-19).
# This one allow-list is what `mcp.usekeel.io` serves to BOTH the
# paste-URL custom connector and the directory listing — there is no
# separate "full" hosted endpoint. Read / research / backtest / share
# tools only.
# EXCLUDED by construction (stay CLI/local-only): keel_live_deploy +
# keel_live_control (live-write), keel_strategy_delete (destructive),
# keel_accounts_list
# (account / wallet work belongs in the web), keel_audit_list_last
# (activity exposure), every local_only tool, and anything with
# money-movement parameter semantics. Additions require a matching
# policy-scan pass (tests/test_policy_scan.py).
LISTED_PROFILE_TOOLS: frozenset[str] = frozenset(
    {
        # always-on basics
        "keel_account_status",
        "keel_connection_check",
        "keel_help",
        # feedback capture (spec 02 R4 — toolset `always`, never fails,
        # nothing gates on it; must be fileable from every profile)
        "keel_feedback",
        # components
        "keel_components_search",
        "keel_components_get",
        "keel_components_get_many",
        # compose / validate
        "keel_strategy_compose",
        # backtest run / results
        "keel_backtest_run",
        "keel_backtest_summarize",
        "keel_backtest_watch",
        # several runs, once (render-cadence decision #19, Q-1688): the
        # set-of-runs rendering. Read-only, ids only, no money or live
        # semantics — the same class as keel_strategy_diff (D1
        # 2026-07-19). Without it the listed surface (which is what
        # mcp.usekeel.io serves to claude.ai and ChatGPT) has NO
        # comparison tool, so the agent renders N single-run cards
        # instead of one comparison. Its shared description names
        # "trades"/"funding"; the listed copy says "carry" and names no count.
        "keel_backtest_compare",
        # one run's positions (each entry to exit), bounded (Q-1893,
        # founder decision 2026-09-23): a summary by default, one page per
        # asset/window scope. Read-only, ids only — the keel_backtest_compare
        # class. Shipped as keel_backtest_round_trips; renamed to "positions"
        # the same day (founder: "round trips" doesn't read to users).
        # "trade" is a forbidden listed name stem.
        "keel_backtest_positions",
        # library — verified entries: read + fork (founder ruling
        # 2026-08-21: all three on the hosted surface; fork follows the
        # keel_strategy_fork precedent — same strategy.create class, no
        # money/live semantics; policy-scan sweep applies as to every
        # listed tool)
        "keel_library_list",
        "keel_library_get",
        "keel_library_fork",
        # strategy read / history / fork / memory
        "keel_strategy_get",
        "keel_strategy_history",
        # read-only version/source diff (D1 2026-07-19: the one benign
        # read wrongly excluded before — no money/wallet params, no
        # forbidden verbs; policy-scan green)
        "keel_strategy_diff",
        "keel_strategy_search",
        "keel_strategy_fork",
        # Agent-surface-cleanup spec 03 §2.4 (R-26): restore creates a
        # FORWARD commit and deletes nothing — the recovery for a set of
        # variants that left HEAD somewhere else ("a variant is a version").
        # 29 listed tools (keel_backtest_positions made 29, Q-1893).
        "keel_strategy_restore",
        "keel_strategy_notes_read",
        "keel_strategy_notes_add",
        # share + read-only live monitoring + ownership
        "keel_share_create",
        "keel_live_monitor",
        "keel_strategy_readiness",
        # plan facts (spec 04 R2, minimized by D-12 — the caller's own plan,
        # usage and reset only; no link, see manage_links_allowed)
        "keel_plan_usage",
        # navigation bridge into the web app (spec 01 R4 — the ONLY
        # app bridge on the listed profile)
        "keel_app_link",
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


def local_tools_registered() -> bool:
    """True where the local/CLI-only tools exist — so a result may name them.

    ``keel_auth_login``, ``keel_strategy_push`` / ``checkout``,
    ``keel_audit_list_last`` and the live-write tools are served by the CLI
    and a local ``keel mcp serve`` on the full profile only. A hosted server
    registers no ``local_only`` tool, and the listed profile serves exactly
    :data:`LISTED_PROFILE_TOOLS`. A result, error or hint that names a tool
    absent from the caller's ``tools/list`` sends the model to a call it
    cannot make (spec 05 R-L4 / §4 item 7), so every such mention asks here.
    """
    from keel.hosting import is_hosted

    return not is_hosted() and not is_listed_profile()


def listed_client() -> str | None:
    """The directory brand a LISTED registration serves, or ``None``.

    Valid values: ``"chatgpt"``, ``"claude"``, or unset (``None``).
    Raises ``ValueError`` on anything else — a typo'd brand must never
    silently pick a policy branch (same rule as :func:`server_profile`).

    **Currently unused by any policy** (mcp-conversion D-1, ratified
    2026-09-19). ``KEEL_LISTED_CLIENT`` is kept as a documented escape
    hatch for a future per-brand endpoint — the capability to differ is
    retained, the behaviour is not — and it is still validated here so a
    typo cannot sit unnoticed in a deployment until the day it matters.
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
    """The plan-destination link policy: NO link, every surface.

    Always ``False`` (mcp-conversion D-12, founder ruling 2026-09-28).
    No agent surface — CLI, local MCP, the one hosted endpoint — emits a
    billing, pricing, checkout or plan-page link: not on a plan-limit
    wall, not from ``keel_plan_usage``, not from ``keel_account_status``. A wall
    states the caller's own limit and its reset; plans are changed in the
    Keel web app, which the user reaches on their own.

    **Why the previous answer (``True``, D-1/D-3/D-9) was wrong.** It
    served ``{app}/settings?tab=billing&from=agent`` everywhere on the
    claim that the page "initiates nothing" (D-9). That claim was false:
    the billing tab's plan buttons go straight to ``checkout.stripe.com``
    (``keel-app/src/lib/mutations/billing.ts``), and OpenAI's guidelines
    bar linking "to a page that explicitly initiates the process to
    upgrade, subscribe, or complete a purchase". OpenAI rejected Keel
    v1.0.0 for "commerce for disallowed offerings" (Q-2080). D-12
    supersedes D-1/D-3/D-9 for every agent surface.

    Kept as a named function rather than deleted: it is the seam a
    future per-brand endpoint would reopen (with ``listed_client()``
    above), and specs reference it by name. It takes no argument and
    reads no environment — there is nothing here to configure wrong on
    the day a directory listing is reviewed.
    """
    return False


def load_toolsets() -> frozenset[str]:
    """Read `KEEL_TOOLSETS` env, parse, validate.

    `always` is implicit — `keel_account_status`, `keel_connection_check`, `keel_help`
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
