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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


# ── The released paper SDK (paper-trading-v2 S06 §3.11 item 1; PT2-28, PT2-91) ─
#
# The paper CLI and MCP tools landed on main dark behind this constant while
# the founder held the `sdk-v*` wheel (PT2-28). PT2-91 (2026-10-14) released
# them: the wheel, the CLI and the local MCP server carry paper. Every paper
# surface — the paper modules' registration, the `paper` toolset, each paper
# touch-up of an existing tool and the listed flag — still reads this one
# constant, so the registry and every public render `check_surface_routing.py`
# writes agree on one value.
#
# A CONSTANT, not an environment read: nothing at runtime sets it. The hosted
# connector is NOT governed by it: the listed surface serves paper only to the
# hosts `KEEL_LISTED_PAPER_HOSTS` names (FDR-10; Claude per PT2-90, ChatGPT
# waits for its own founder decision).
PAPER_SDK_RELEASED: bool = True

#: The toolset the paper tools load under (S06 §3.3). Held with the SDK.
PAPER_TOOLSET = "paper"


def with_paper_toolset(toolsets: frozenset[str]) -> frozenset[str]:
    """``toolsets`` plus :data:`PAPER_TOOLSET` once the paper SDK is released.

    The one place a toolset constant learns ``paper``: ``_DEFAULT_TOOLSETS``
    here and ``_base.ALL_TOOLSETS`` are both built through it, so while
    :data:`PAPER_SDK_RELEASED` is false neither holds ``paper`` (S06 §3.3).
    """
    return toolsets | {PAPER_TOOLSET} if PAPER_SDK_RELEASED else toolsets


def default_toolsets() -> frozenset[str]:
    """The toolsets loaded when ``KEEL_TOOLSETS`` is unset (S06 §3.3).

    ``{always, read-only, backtest, share, live-read}``, plus ``paper`` once the
    paper SDK is released: a paper run must be creatable and stoppable with
    zero opt-in, so paper rides the default set, never ``live-write``.
    :data:`_DEFAULT_TOOLSETS` is this, evaluated at import (the
    ``paper_sdk_released`` test fixture re-evaluates it after setting the
    constant).
    """
    return with_paper_toolset(frozenset({"always", "read-only", "backtest", "share", "live-read"}))


_DEFAULT_TOOLSETS = default_toolsets()

#: Toolsets in the order every surface lists them.
TOOLSET_ORDER: tuple[str, ...] = (
    "always",
    "read-only",
    "backtest",
    "share",
    "live-read",
    PAPER_TOOLSET,
    "live-write",
)


def live_write_opt_in() -> str:
    """The ``KEEL_TOOLSETS`` value that adds ``live-write`` to the default set.

    ``KEEL_TOOLSETS`` REPLACES the default set, so a hint that types the set
    drops whatever joins the default later: until Q-3095 every hint typed the
    pre-paper set and following it unloaded the paper tools. Every surface
    that prints the opt-in reads it here (``status``'s live route, the
    generated tool references, the AGENTS.md inventory heading; the two
    hand-written docs are pinned to it by ``tests/test_live_write_opt_in.py``).
    """
    wanted = default_toolsets() | {"live-write"}
    unknown = wanted - set(TOOLSET_ORDER)
    if unknown:
        raise ValueError(f"toolset(s) {sorted(unknown)} have no place in TOOLSET_ORDER")
    return "KEEL_TOOLSETS=" + ",".join(t for t in TOOLSET_ORDER if t in wanted)


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


LISTED_PAPER_ENV = "KEEL_LISTED_PAPER"

# ── Per-host listed paper (founder ruling FDR-10, 2026-10-12) ───────────
#
# ONE hosted endpoint serves claude.ai and ChatGPT, but the two directories
# review on different clocks: Claude reads the live server, ChatGPT reviews a
# scanned 29-tool snapshot. So the listed paper exposure is switched PER HOST:
# ``KEEL_LISTED_PAPER_HOSTS`` is a comma list of host keys (``claude``,
# ``chatgpt``) or ``all``. Unset/empty is off for every host (the default).
# ``KEEL_LISTED_PAPER`` (the original single switch) is a DEPRECATED alias:
# on means ``all``. A host key names the AUTHENTICATED caller's OAuth client,
# bound per request by the hosted server (``listed_host_context``); a caller
# with no host key (an unknown or custom client, a local listed server) is
# covered only by ``all``.
LISTED_PAPER_HOSTS_ENV = "KEEL_LISTED_PAPER_HOSTS"
#: The host keys a hosted request can carry.
LISTED_HOSTS: tuple[str, ...] = ("claude", "chatgpt")
_LISTED_HOST_ALL = "all"

#: The current request's host key (``None`` outside a hosted request, or for
#: an unknown client). Set only through :func:`listed_host_context`.
_LISTED_HOST: ContextVar[str | None] = ContextVar("keel_listed_host", default=None)


def _legacy_listed_paper_on() -> bool:
    raw = os.environ.get(LISTED_PAPER_ENV, "").strip().lower()
    if raw in ("", "0", "false"):
        return False
    if raw not in ("1", "true"):
        raise ValueError(
            f"Invalid {LISTED_PAPER_ENV}={raw!r}. Valid values: 1, true, 0, false (or unset)."
        )
    return True


def listed_paper_hosts() -> frozenset[str]:
    """The host keys the listed paper surface is on for (FDR-10).

    ``KEEL_LISTED_PAPER_HOSTS`` parsed (each entry one of :data:`LISTED_HOSTS`
    or ``all``), plus ``all`` when the deprecated ``KEEL_LISTED_PAPER`` is on.
    Any other entry raises ``ValueError`` (no silent fallback, the
    :func:`server_profile` rule), so an invalid setting fails server
    creation. A non-empty set also raises while :data:`PAPER_SDK_RELEASED` is
    false in this process (PT2-28): it would list a tool the held SDK does not
    register. Read on every call, never cached.
    """
    raw = os.environ.get(LISTED_PAPER_HOSTS_ENV, "")
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    invalid = parts - {*LISTED_HOSTS, _LISTED_HOST_ALL}
    if invalid:
        raise ValueError(
            f"Invalid {LISTED_PAPER_HOSTS_ENV}={raw!r}: unknown host key(s) "
            f"{sorted(invalid)}. Valid: {', '.join(LISTED_HOSTS)}, {_LISTED_HOST_ALL} (or unset)."
        )
    if _legacy_listed_paper_on():
        parts.add(_LISTED_HOST_ALL)
    if parts and not PAPER_SDK_RELEASED:
        raise ValueError(
            f"{LISTED_PAPER_ENV}/{LISTED_PAPER_HOSTS_ENV}={sorted(parts)!r} is refused: the "
            "paper SDK is held (PAPER_SDK_RELEASED is False until the wheel's follow-up, "
            "PT2-28), so the listed profile cannot carry a paper tool the SDK does not register."
        )
    return frozenset(parts)


def listed_host() -> str | None:
    """The current request's host key, or ``None`` (unknown client / no request)."""
    return _LISTED_HOST.get()


@contextmanager
def listed_host_context(host: str | None) -> Iterator[None]:
    """Bind ``host`` as the caller's host key for the enclosed work.

    ``host`` must be one of :data:`LISTED_HOSTS` or ``None``; anything else
    raises, so a mapping bug can never bind a key no switch names.
    """
    if host is not None and host not in LISTED_HOSTS:
        raise ValueError(f"Unknown listed host key {host!r}. Valid: {', '.join(LISTED_HOSTS)}.")
    token = _LISTED_HOST.set(host)
    try:
        yield
    finally:
        _LISTED_HOST.reset(token)


def listed_paper_enabled() -> bool:
    """Whether the listed (hosted connector) profile carries paper for the
    CURRENT caller (S06 §3.4.2; per host since FDR-10).

    True when :func:`listed_paper_hosts` holds ``all`` or the caller's host key
    (:func:`listed_host`). Unset ``KEEL_LISTED_PAPER_HOSTS`` and
    ``KEEL_LISTED_PAPER`` is off for every caller. An invalid value raises
    ``ValueError``; any non-empty value raises while the paper SDK is held
    (PT2-28). Read on every call, never cached, so one process serves both
    states, one per caller.
    """
    hosts = listed_paper_hosts()
    if not hosts:
        return False
    return _LISTED_HOST_ALL in hosts or listed_host() in hosts


#: The tools the listed flag adds (S06 §3.4.2, §3.4.3; S10 §3-4, PT2-90): exact
#: names, never a prefix. Both are simulated-only: `keel_paper_deploy` starts a
#: paper run and `keel_paper_control` pauses, resumes, stops, restarts or
#: updates one. No live tool is ever added (PT2-27).
LISTED_PAPER_TOOLS: frozenset[str] = frozenset({"keel_paper_deploy", "keel_paper_control"})


# ── Per-host listed screens (screeners L13, founder 2026-11-03) ──────────
#
# The same shape as the paper switch above: ``KEEL_LISTED_SCREENS_HOSTS`` is a
# comma list of host keys (``claude``, ``chatgpt``) or ``all``; unset or empty
# is off for every host, and the listed surface is then the base's, byte for
# byte (tests/test_listed_screens.py proves it against the frozen head). On,
# the caller's host gets :data:`LISTED_SCREEN_TOOLS`, the screen card, the
# screen-aware ``keel_strategy_compose`` copy, the ``screen-creation`` prompt
# and the SCREENS instruction line. The full/local profile carries all of that
# unflagged: the flag governs only the reviewed hosted surface.
LISTED_SCREENS_HOSTS_ENV = "KEEL_LISTED_SCREENS_HOSTS"

#: The tools the screens flag adds to the listed surface: exact names. Each
#: reads or runs a screen (a pipeline with ``Outputs`` instead of
#: ``Execution``); none places an order or moves money. Publishing a screen
#: stays in the Keel web app (``keel_app_link``, founder F2).
LISTED_SCREEN_TOOLS: frozenset[str] = frozenset(
    {
        "keel_screener_run",
        "keel_screener_view",
        "keel_screener_view_set",
        "keel_screener_list",
    }
)


def listed_screens_hosts() -> frozenset[str]:
    """The host keys the listed screens surface is on for.

    ``KEEL_LISTED_SCREENS_HOSTS`` parsed: each entry one of
    :data:`LISTED_HOSTS` or ``all``. Any other entry raises ``ValueError``
    (no silent fallback), so an invalid setting fails server creation. Read on
    every call, never cached.
    """
    raw = os.environ.get(LISTED_SCREENS_HOSTS_ENV, "")
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    invalid = parts - {*LISTED_HOSTS, _LISTED_HOST_ALL}
    if invalid:
        raise ValueError(
            f"Invalid {LISTED_SCREENS_HOSTS_ENV}={raw!r}: unknown host key(s) "
            f"{sorted(invalid)}. Valid: {', '.join(LISTED_HOSTS)}, {_LISTED_HOST_ALL} (or unset)."
        )
    return frozenset(parts)


def listed_screens_enabled() -> bool:
    """Whether the listed profile carries screens for the CURRENT caller.

    True when :func:`listed_screens_hosts` holds ``all`` or the caller's host
    key (:func:`listed_host`). Unset is off for every caller.
    """
    hosts = listed_screens_hosts()
    if not hosts:
        return False
    return _LISTED_HOST_ALL in hosts or listed_host() in hosts


def screens_served() -> bool:
    """Whether the active surface serves screens: always on the full profile,
    on the listed profile only while :func:`listed_screens_enabled`."""
    return not is_listed_profile() or listed_screens_enabled()


#: The knowledge topics that teach screens. A listed caller without the
#: screens flag is not served them: they describe tools that surface does not
#: register (R-L4); every other surface serves them.
SCREEN_TOPICS: frozenset[str] = frozenset({"screener", "view_language", "screen_recipes"})


def screen_topic_withheld(topic: str) -> bool:
    """Whether the active surface withholds the (normalized) ``topic``."""
    return topic in SCREEN_TOPICS and not screens_served()


# ── Research (spec 07 §5.3, RL-91): one runtime flag, learned per request ──
#
# No environment variable or constant switches Research. keel-api answers
# every research call (``RESEARCH_PAUSED`` while ``research.enabled`` is off),
# and the hosted server learns the flag per caller from ``/v1/me``'s
# ``me.research`` (``services/mcp-server/src/auth.py``), binding it here for
# the request with :func:`research_served_context`. Unbound, the full profile
# and the CLI serve the verbs and let the API answer (true), and the listed
# profile serves none (false: the listing fails closed, and a listed snapshot
# with nothing bound is the frozen head). The hosted listed server binds true
# while it registers its tools and per caller from /v1/me for each request.
#
# A LOCAL surface (the CLI, a full-profile ``keel mcp serve``) has one caller,
# so unbound it reads that caller's /v1/me the same way, cached
# (``_research_local.py``; FINAL-LIST §D): while research.enabled is off it
# registers no research verb, tool, topic or card, so the one flag hides
# Research on the local surfaces too. Unbound on the hosted full profile
# stays true (its main binds per request).

#: The listed research tools (07 A1, the founder's 2026-11-11 listing ruling).
LISTED_RESEARCH_TOOLS: frozenset[str] = frozenset(
    {"keel_research_plan", "keel_research_run", "keel_research_get"}
)

#: RI-168 (06 §4.1 "Use with your agent", A-06-46): the keel-api route each listed research tool calls, as the app's
#: dialog shows it beside the tool and its CLI form. test_surface_routing drives each tool's handler and proves the
#: route it sends is this one.
RESEARCH_TOOL_ROUTES: dict[str, tuple[str, str]] = {
    "keel_research_plan": ("POST", "/v1/research/plans"),
    "keel_research_run": ("POST", "/v1/research/runs"),
    "keel_research_get": ("GET", "/v1/research/studies/{study_id}"),
}

#: The knowledge topics that teach Research (07 §4.10); ``step:<Name>`` too.
RESEARCH_TOPICS: frozenset[str] = frozenset({"research", "research_steps"})
RESEARCH_STEP_TOPIC_PREFIX = "step:"

#: The card kind the research tools draw (07 §3.3).
RESEARCH_CARD_KIND = "study"

_RESEARCH_SERVED: ContextVar[bool | None] = ContextVar("keel_research_served", default=None)


def research_served() -> bool:
    """Whether this request serves Research: what the caller's ``/v1/me`` said
    (bound per request on the hosted server); unbound, false on the listed
    profile, true on the hosted full profile, and on a local surface what
    this process's own caller's ``/v1/me`` says (``_research_local``)."""
    bound = _RESEARCH_SERVED.get()
    if bound is not None:
        return bound
    if is_listed_profile():
        return False
    from keel.hosting import is_hosted

    if is_hosted():
        return True
    from ._research_local import local_research_served

    return local_research_served()


def is_research_tool(name: str) -> bool:
    """Whether ``name`` is one of Research's tools or CLI verbs (07 §2, §5)."""
    return name.startswith("keel_research_")


def research_served_bound() -> bool | None:
    """What this request BOUND for Research (``research_served_context``, the hosted server's ``/v1/me``), or None
    when nothing bound it. D-358: a core surface's Research wording rides a bound yes only, so an unbound full
    profile publishes its pre-Research text byte for byte."""
    return _RESEARCH_SERVED.get()


@contextmanager
def research_served_context(served: bool) -> Iterator[None]:
    """Bind whether Research is served for the enclosed work (one request)."""
    if not isinstance(served, bool):
        raise TypeError(f"research_served must be a bool, got {served!r}")
    token = _RESEARCH_SERVED.set(served)
    try:
        yield
    finally:
        _RESEARCH_SERVED.reset(token)


def research_topic_withheld(topic: str) -> bool:
    """Whether the active request withholds the (normalized) ``topic``."""
    is_research = topic in RESEARCH_TOPICS or topic.startswith(RESEARCH_STEP_TOPIC_PREFIX)
    return is_research and not research_served()


def listed_profile_tools() -> frozenset[str]:
    """The listed surface's tool set: :data:`LISTED_PROFILE_TOOLS` (29), plus
    :data:`LISTED_PAPER_TOOLS` while :func:`listed_paper_enabled` is true, plus
    :data:`LISTED_SCREEN_TOOLS` while :func:`listed_screens_enabled` is true,
    plus :data:`LISTED_RESEARCH_TOOLS` while :func:`research_served` is true.
    Read at call time; a server evaluates it at creation."""
    tools = LISTED_PROFILE_TOOLS
    if listed_paper_enabled():
        tools = tools | LISTED_PAPER_TOOLS
    if listed_screens_enabled():
        tools = tools | LISTED_SCREEN_TOOLS
    if research_served():
        # 07 §5.3: the three listed research tools, while this request serves
        # Research (the caller's /v1/me carries me.research on the hosted
        # server; true by default for the full profile and the CLI).
        tools = tools | LISTED_RESEARCH_TOOLS
    return tools


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
    from ._base import ALL_TOOLSETS

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
    cli_only: bool = False,
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

    ``cli_only`` tools are CLI commands with no MCP registration on any
    profile (``OutcomeTool.cli_only``): a UI-shaped or redundant command an
    agent reaches through another tool (screeners L13 §2b).
    """
    if cli_only:
        return False
    if local_only:
        from keel.hosting import is_hosted

        if is_hosted():
            return False
    if is_listed_profile():
        # The listed flag adds exactly LISTED_PAPER_TOOLS (S06 §3.4.2, S10 §3).
        return name in listed_profile_tools()
    if is_research_tool(name) and not research_served():
        return False  # research.enabled off for this caller (FINAL-LIST §D)
    return tool_toolset == "always" or tool_toolset in active
