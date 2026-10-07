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
