"""Written grounds for the safety hints of every LISTED-profile tool.

The annotation VALUES live on each ``OutcomeTool`` (``annotations=``) and
are what ``tools/list`` publishes. This module holds the one-line REASON
behind each of ``readOnlyHint`` / ``destructiveHint`` / ``openWorldHint``
for the 29 tools the hosted endpoint serves (``LISTED_PROFILE_TOOLS``).
Directory submission asks for "a justification for each value" (OpenAI
O-3); keeping them beside the catalog in ONE place means a changed hint
and its changed reason land in the same diff.

Nothing here is sent over the wire. The reasons never enter ``annotations``
or any description; they are review material only (exported to the
ChatGPT submission form, gated by
``tests/test_listed_surface_annotations.py``).

THE ANNOTATION POLICY (founder-approved 2026-10-01, Q-2080; the rules are
OpenAI's plugin guidelines, "MCP requirements > Tools", and the app-review
"Tool hint annotations" text):

* ``readOnlyHint`` — ``true`` only for a tool whose ACTION writes nothing
  the caller can later read back. Incidental operational logging, the
  audit row and analytics telemetry are not the tool's action and never
  decide a hint.

* ``destructiveHint`` — the APPEND-ONLY RULE. A write tool may be
  ``destructiveHint: false`` ONLY if it is append-only: every call adds a
  new record or a new version; nothing is edited in place or deleted; the
  history is the system of record, so what was there before the call is
  still there after it. Every such tool is named in :data:`APPEND_ONLY`
  with its one-line reason, and the guard holds the two in step: a write
  tool with ``destructiveHint: false`` that is not in the set reds, and so
  does a set entry that is not such a tool. "It can be undone" is NOT a
  reason (OpenAI: being able to undo an action does not by itself justify
  ``false``) — the reason is that nothing was replaced to begin with.
  ``true`` is for the irreversible: ``keel_share_create`` (a public
  disclosure cannot be retracted from recipients who already opened or
  copied it; revoking the link does not reach them) and ``keel_feedback``
  (a note sent to the Keel team cannot be recalled).

* ``openWorldHint`` — ``true`` only for a tool that reaches the public
  internet, an independently controlled external system, or an open-ended
  set of entities, or that changes publicly visible state. A tool bounded
  to the caller's own Keel account and workspace is ``false`` even though
  Keel itself is externally hosted. On the listed surface exactly four are
  open-world: ``keel_share_create`` (publishes at a public URL),
  ``keel_feedback`` (the note is delivered to the Keel team, outside the
  caller's workspace), ``keel_live_monitor`` (the ``positions`` view is a
  live read of the caller's linked Hyperliquid account, an exchange Keel
  does not control) and ``keel_strategy_fork`` (accepts a share link whose
  source is shown, so its source can be another organisation's strategy).
"""

from __future__ import annotations

from typing import NamedTuple


class AnnotationJustification(NamedTuple):
    """One line per safety hint, stating why the value is what it is."""

    read_only: str
    destructive: str
    open_world: str


#: The listed tools whose ``openWorldHint`` is ``true``. Every other listed
#: tool is ``false``; the guard pins both directions.
LISTED_OPEN_WORLD_TOOLS: frozenset[str] = frozenset(
    {"keel_share_create", "keel_feedback", "keel_live_monitor", "keel_strategy_fork"}
)

#: The listed tools whose ``destructiveHint`` is ``true`` — the irreversible
#: ones. Every other listed WRITE tool is append-only (:data:`APPEND_ONLY`).
LISTED_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"keel_share_create", "keel_feedback"})

#: The append-only rule, applied: every listed write tool that carries
#: ``destructiveHint: false``, with the one line that makes it append-only.
#: A tool whose action replaces or deletes anything the caller can read back
#: does not belong here; it is ``destructiveHint: true`` and absent.
APPEND_ONLY: dict[str, str] = {
    "keel_strategy_compose": (
        "Saves are new versions in an append-only history; the strategy points at the "
        "latest; nothing is deleted. A strategy's name and thesis are set only when it "
        "is created on this surface."
    ),
    "keel_strategy_restore": (
        "A restore is a new version equal to an earlier one, in the same append-only "
        "history; the strategy points at the latest; nothing is deleted."
    ),
    "keel_strategy_notes_add": "Each call appends one note; no note is edited or removed.",
    "keel_strategy_fork": (
        "Creates a new strategy with its own history; the source strategy is left as it was."
    ),
    "keel_library_fork": (
        "Creates a new strategy in the caller's workspace; the library entry is read, not changed."
    ),
    "keel_backtest_run": "Each call creates a new run record; no run is edited or removed.",
}

_OWN_ACCOUNT = "Talks only to Keel's API about the caller's own account; reaches nothing public."
_OWN_WORKSPACE = (
    "Acts only on the caller's own private Keel workspace via Keel's API; nothing is published."
)
_BUNDLED = "Served from the component registry bundled with Keel; no external system is contacted."
_NO_WRITE = "Writes nothing, so it can neither delete nor overwrite."

LISTED_ANNOTATION_JUSTIFICATIONS: dict[str, AnnotationJustification] = {
    # ── always-on basics ────────────────────────────────────────────────
    "keel_account_status": AnnotationJustification(
        "Reports sign-in, account and quota facts; changes nothing.",
        _NO_WRITE,
        _OWN_ACCOUNT,
    ),
    "keel_connection_check": AnnotationJustification(
        "Runs connection checks and reports the results; changes nothing.",
        _NO_WRITE,
        _OWN_ACCOUNT,
    ),
    "keel_help": AnnotationJustification(
        "Returns bundled help text; changes nothing.",
        _NO_WRITE,
        "Served from help content bundled with Keel; no external system is contacted.",
    ),
    "keel_feedback": AnnotationJustification(
        "Sends a feedback note, which Keel stores for its team.",
        "Irreversible: a note delivered to the Keel team cannot be recalled.",
        "The note leaves the caller's workspace and is delivered to the Keel team; "
        "nothing is published.",
    ),
    # ── components ──────────────────────────────────────────────────────
    "keel_components_search": AnnotationJustification(
        "Searches the component catalog; changes nothing.", _NO_WRITE, _BUNDLED
    ),
    "keel_components_get": AnnotationJustification(
        "Returns one component's contract; changes nothing.", _NO_WRITE, _BUNDLED
    ),
    "keel_components_get_many": AnnotationJustification(
        "Returns several components' contracts; changes nothing.", _NO_WRITE, _BUNDLED
    ),
    # ── compose / validate ──────────────────────────────────────────────
    "keel_strategy_compose": AnnotationJustification(
        "Saves a strategy (a new strategy, or a new version of one) in the caller's workspace.",
        "Append-only: " + APPEND_ONLY["keel_strategy_compose"],
        _OWN_WORKSPACE + " Strategies stay private unless shared with keel_share_create.",
    ),
    # ── backtest run / results ──────────────────────────────────────────
    "keel_backtest_run": AnnotationJustification(
        "Queues a new backtest run, which is recorded in the caller's workspace.",
        "Append-only: " + APPEND_ONLY["keel_backtest_run"],
        "Simulates on Keel's historical data inside the caller's workspace; "
        "places no orders and publishes nothing.",
    ),
    "keel_backtest_summarize": AnnotationJustification(
        "Reads the results of a finished backtest; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_backtest_watch": AnnotationJustification(
        "Polls the status of a backtest run; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_backtest_compare": AnnotationJustification(
        "Reads several backtest runs side by side; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_backtest_positions": AnnotationJustification(
        "Reads the positions of one backtest run; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    # ── library ─────────────────────────────────────────────────────────
    "keel_library_list": AnnotationJustification(
        "Lists entries in Keel's strategy library; changes nothing.",
        _NO_WRITE,
        "Reads Keel's own curated library, a bounded first-party catalog, via Keel's API.",
    ),
    "keel_library_get": AnnotationJustification(
        "Reads one library entry; changes nothing.",
        _NO_WRITE,
        "Reads Keel's own curated library, a bounded first-party catalog, via Keel's API.",
    ),
    "keel_library_fork": AnnotationJustification(
        "Copies a library entry into the caller's workspace as a new strategy.",
        "Append-only: " + APPEND_ONLY["keel_library_fork"],
        _OWN_WORKSPACE,
    ),
    # ── strategy read / history / fork / notes ──────────────────────────
    "keel_strategy_get": AnnotationJustification(
        "Reads one strategy; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_strategy_history": AnnotationJustification(
        "Reads a strategy's version history; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_strategy_diff": AnnotationJustification(
        "Compares two strategy versions; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_strategy_search": AnnotationJustification(
        "Searches the caller's strategies; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_strategy_fork": AnnotationJustification(
        "Creates a new strategy copied from an existing one.",
        "Append-only: " + APPEND_ONLY["keel_strategy_fork"],
        "Accepts a share link whose source is shown as its source, so the strategy copied can "
        "belong to another organisation; the copy itself lands in the caller's own workspace.",
    ),
    "keel_strategy_restore": AnnotationJustification(
        "Writes an earlier version forward as the strategy's new latest version.",
        "Append-only: " + APPEND_ONLY["keel_strategy_restore"],
        _OWN_WORKSPACE,
    ),
    "keel_strategy_notes_read": AnnotationJustification(
        "Reads a strategy's saved notes; changes nothing.", _NO_WRITE, _OWN_WORKSPACE
    ),
    "keel_strategy_notes_add": AnnotationJustification(
        "Appends a note to a strategy's saved notes.",
        "Append-only: " + APPEND_ONLY["keel_strategy_notes_add"],
        _OWN_WORKSPACE,
    ),
    # ── share + live monitoring + readiness ─────────────────────────────
    "keel_share_create": AnnotationJustification(
        "Creates a new public share link for a strategy or backtest.",
        "Irreversible: the link discloses the shared data publicly, and revoking it later "
        "does not retract what recipients already opened or copied.",
        "Publishes the strategy or backtest at a public URL that anyone with the link can open.",
    ),
    "keel_live_monitor": AnnotationJustification(
        "Reads the status and performance of the caller's own running strategies; changes nothing.",
        _NO_WRITE,
        "The positions view is a live read of the caller's linked Hyperliquid account, an "
        "exchange Keel does not control; it reads only, and only the caller's own account.",
    ),
    "keel_strategy_readiness": AnnotationJustification(
        "Reads what evidence one of the caller's strategies has and still needs; changes nothing.",
        _NO_WRITE,
        _OWN_WORKSPACE,
    ),
    # ── plan facts + app link ───────────────────────────────────────────
    "keel_plan_usage": AnnotationJustification(
        "Reads the caller's own plan limits and usage; changes nothing.",
        _NO_WRITE,
        _OWN_ACCOUNT,
    ),
    "keel_app_link": AnnotationJustification(
        "Builds a link into the Keel app; changes nothing.",
        _NO_WRITE,
        "Returns a URL for the caller's own Keel app (or an existing share page); "
        "contacts no system to do so.",
    ),
}


# ── The listed paper tools (S10 §3-4, PT2-90; served only while the listed
# flag is on for the caller's host, FDR-10) ───────────────────────────────
#
# Kept apart from the 29 rows above so the HEAD surface's registry, which the
# ChatGPT submission export reads, is unchanged while the flag is off for
# ChatGPT. :func:`served_justifications` and the two set helpers below are the
# served view: the 29 rows, plus these exactly when `listed_paper_enabled()`.
# The same policy applies: `keel_paper_deploy` is append-only (each call
# creates a new run), `keel_paper_control` is destructive (`stop` ends a record
# for good and `update` replaces the version a run executes), and neither is
# open-world (a simulated run on Keel's own data, in the caller's workspace).

#: The listed paper write tools that are append-only, with the reason.
LISTED_PAPER_APPEND_ONLY: dict[str, str] = {
    "keel_paper_deploy": (
        "Each call creates a new simulated paper run record; no run is edited or removed."
    ),
}

#: The listed paper tools whose ``destructiveHint`` is ``true``.
LISTED_PAPER_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"keel_paper_control"})

#: The listed paper tools whose ``openWorldHint`` is ``true``: none.
LISTED_PAPER_OPEN_WORLD_TOOLS: frozenset[str] = frozenset()

_SIMULATED_OWN_WORKSPACE = (
    "A simulated run on Keel's own market data inside the caller's workspace; it places "
    "no orders, contacts no exchange account and publishes nothing."
)

LISTED_PAPER_ANNOTATION_JUSTIFICATIONS: dict[str, AnnotationJustification] = {
    "keel_paper_deploy": AnnotationJustification(
        "Starts a new simulated paper run, which is recorded in the caller's workspace.",
        "Append-only: " + LISTED_PAPER_APPEND_ONLY["keel_paper_deploy"],
        _SIMULATED_OWN_WORKSPACE,
    ),
    "keel_paper_control": AnnotationJustification(
        "Pauses, resumes, stops, restarts or updates one of the caller's paper runs.",
        "Irreversible in two modes: stop ends the run's record for good, and update "
        "replaces the strategy version the run executes; restart also ends the current "
        "record.",
        _SIMULATED_OWN_WORKSPACE,
    ),
}


# ── The listed screen tools (screeners L13; served only while
# `listed_screens_enabled()` for the caller's host) ─────────────────────────
#
# Kept apart for the same reason as the paper rows: the base registry the
# ChatGPT export reads is unchanged while the flag is off. A screen run is a
# backtest-class job in the caller's workspace; a view save keeps every earlier
# revision (`strategy.result_view_revisions`), so it is append-only, not
# destructive; neither reaches anything public.

LISTED_SCREEN_APPEND_ONLY: dict[str, str] = {
    "keel_screener_run": "Each call creates a new screen run record; no run is edited or removed.",
    "keel_screener_view_set": (
        "Each save adds a new view revision; earlier revisions stay stored and can be "
        "restored as a new revision."
    ),
}

#: The listed screen tools whose ``destructiveHint`` is ``true``: none.
LISTED_SCREEN_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset()

#: The listed screen tools whose ``openWorldHint`` is ``true``: none.
LISTED_SCREEN_OPEN_WORLD_TOOLS: frozenset[str] = frozenset()

LISTED_SCREEN_ANNOTATION_JUSTIFICATIONS: dict[str, AnnotationJustification] = {
    "keel_screener_run": AnnotationJustification(
        "Queues a new screen run, which is recorded in the caller's workspace.",
        "Append-only: " + LISTED_SCREEN_APPEND_ONLY["keel_screener_run"],
        "Computes on Keel's own market data inside the caller's workspace; places no "
        "orders and publishes nothing.",
    ),
    "keel_screener_view": AnnotationJustification(
        "Reads a finished screen run and, on request, its saved view; changes nothing.",
        _NO_WRITE,
        _OWN_WORKSPACE,
    ),
    "keel_screener_view_set": AnnotationJustification(
        "Saves a new view revision for one of the caller's screens (a dry run saves nothing).",
        "Append-only: " + LISTED_SCREEN_APPEND_ONLY["keel_screener_view_set"],
        _OWN_WORKSPACE,
    ),
    "keel_screener_list": AnnotationJustification(
        "Lists the caller's screens, the ones they follow, or Keel's curated screens; "
        "changes nothing.",
        _NO_WRITE,
        "Reads the caller's own workspace and Keel's own curated list through Keel's API.",
    ),
}


# ── The listed research tools (07 §5.3, RL-91; served only while the request
# serves Research, `_toolsets.research_served()`, from the caller's /v1/me) ──
#
# Kept apart for the same reason as the paper and screen rows. A plan runs
# nothing and saves no study (keel-api keeps the plan 24 hours under its
# `plan_id` so a run runs exactly it: a quote the caller reads back, not saved
# work); a run saves the study as a new study or a new version and adds new
# runs. D-344 (3), CONFORM-MCP F4: 07 §4.3 replaces a never-run Draft head in
# place when its edit is run, so the reason says so: that Draft holds no runs
# and no results, and no version that ran, and no run, is edited or removed
# (resume adds the next submission, an engine change plans version v+1). A read
# changes nothing. All three take a `share_id`, so a study shared by link can
# be read or run on the caller's own subjects.

LISTED_RESEARCH_APPEND_ONLY: dict[str, str] = {
    "keel_research_run": (
        "Each call saves a new study or a new study version and adds new runs; no version "
        "that ran and no run is edited or removed. The one record a run saves over is the "
        "study's own never-run Draft, a document that holds no runs or results."
    ),
}

#: The listed research tools whose ``destructiveHint`` is ``true``: none.
LISTED_RESEARCH_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset()

#: The listed research tools whose ``openWorldHint`` is ``true``: none.
LISTED_RESEARCH_OPEN_WORLD_TOOLS: frozenset[str] = frozenset()

_RESEARCH_SCOPE = (
    "Reads the caller's own studies, or a study shared by link, through Keel's API; "
    "publishes nothing."
)

LISTED_RESEARCH_ANNOTATION_JUSTIFICATIONS: dict[str, AnnotationJustification] = {
    "keel_research_plan": AnnotationJustification(
        "Returns what a study would run, reuse and spend; runs nothing and saves no study. "
        "Keel keeps the plan for 24 hours under its plan_id so a run runs exactly it: a quote "
        "the caller reads back, not saved work.",
        "Saves nothing to the caller's studies or strategies, so it can neither delete nor "
        "overwrite.",
        _RESEARCH_SCOPE,
    ),
    "keel_research_run": AnnotationJustification(
        "Saves the study and starts its runs, which are recorded in the caller's workspace.",
        # AUDIT S3 #13: for directory review the reason says what it adds and the one thing it saves over (a never-run
        # Draft), never "Append-only:", which that second sentence contradicts
        LISTED_RESEARCH_APPEND_ONLY["keel_research_run"],
        "Computes on Keel's own market data inside the caller's workspace; places no orders "
        "and publishes nothing. With a share_id it copies the shared study's published "
        "snapshot (never its author's source) into the caller's workspace, as a read of a "
        "share does; unlike a strategy fork, no other organisation's source is taken.",
    ),
    "keel_research_get": AnnotationJustification(
        "Reads a study's results, progress or chart rows, or lists the caller's studies; "
        "changes nothing.",
        _NO_WRITE,
        _RESEARCH_SCOPE,
    ),
}


def served_justifications() -> dict[str, AnnotationJustification]:
    """The rows for the listed surface this caller is served: the 29, plus the
    paper, screen and research rows while each is served (read at call time)."""
    from ._toolsets import listed_paper_enabled, listed_screens_enabled, research_served

    rows = dict(LISTED_ANNOTATION_JUSTIFICATIONS)
    if listed_paper_enabled():
        rows.update(LISTED_PAPER_ANNOTATION_JUSTIFICATIONS)
    if listed_screens_enabled():
        rows.update(LISTED_SCREEN_ANNOTATION_JUSTIFICATIONS)
    if research_served():
        rows.update(LISTED_RESEARCH_ANNOTATION_JUSTIFICATIONS)
    return rows


def served_append_only() -> dict[str, str]:
    """:data:`APPEND_ONLY` for the served surface (each family's rows while served)."""
    from ._toolsets import listed_paper_enabled, listed_screens_enabled, research_served

    rows = dict(APPEND_ONLY)
    if listed_paper_enabled():
        rows.update(LISTED_PAPER_APPEND_ONLY)
    if listed_screens_enabled():
        rows.update(LISTED_SCREEN_APPEND_ONLY)
    if research_served():
        rows.update(LISTED_RESEARCH_APPEND_ONLY)
    return rows


def served_destructive_tools() -> frozenset[str]:
    """:data:`LISTED_DESTRUCTIVE_TOOLS` for the served surface."""
    from ._toolsets import listed_paper_enabled, listed_screens_enabled, research_served

    tools = LISTED_DESTRUCTIVE_TOOLS
    if listed_paper_enabled():
        tools = tools | LISTED_PAPER_DESTRUCTIVE_TOOLS
    if listed_screens_enabled():
        tools = tools | LISTED_SCREEN_DESTRUCTIVE_TOOLS
    if research_served():
        tools = tools | LISTED_RESEARCH_DESTRUCTIVE_TOOLS
    return tools


def served_open_world_tools() -> frozenset[str]:
    """:data:`LISTED_OPEN_WORLD_TOOLS` for the served surface."""
    from ._toolsets import listed_paper_enabled, listed_screens_enabled, research_served

    tools = LISTED_OPEN_WORLD_TOOLS
    if listed_paper_enabled():
        tools = tools | LISTED_PAPER_OPEN_WORLD_TOOLS
    if listed_screens_enabled():
        tools = tools | LISTED_SCREEN_OPEN_WORLD_TOOLS
    if research_served():
        tools = tools | LISTED_RESEARCH_OPEN_WORLD_TOOLS
    return tools
