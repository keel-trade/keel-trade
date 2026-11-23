"""The channel contract — ONE owner of where each result field goes.

Agent-surface-cleanup spec 02 §2.1–§2.2. A Keel tool result has four homes
for a field, and the assignment is a declared table, never a per-tool
decision:

* ``content[0].text`` — a RENDERING (``view.markdown`` + the catalogue
  lines, ``_mcp_adapter.operational_lines``), not a home of its own;
* ``structuredContent`` — the compact typed envelope (``structured``);
* ``_meta["keel/card"]`` — what only the card draws (``card``): series,
  tiles, render hints, raw metric dumps, presigned URLs;
* envelope only — never over MCP (``drop``): a second copy of a fact another
  home already carries (the CLI human renderer still reads it from the
  envelope dict, which is untouched).

Rules (spec 02 §2.1): every field of a view tool's envelope is in exactly
one of ``structured`` / ``card`` / ``drop`` (R1, guarded by
``tests/test_channels.py`` G1); an error envelope rides WHOLE in
``structuredContent`` with an empty card (R-27, Q-1806); nothing in the card
is needed to read the result (R4).

**The probe gate (spec 02 §2.7, LANES.md).** No host has yet been shown to
hand ``_meta["keel/card"]`` to a card iframe, so the MOVE of the ``card``
rows out of ``structuredContent`` ships behind :data:`CARD_META_ENV`,
default OFF: off, the card rows stay in ``structuredContent`` (today's
shape — every host keeps working) and no ``keel/card`` key is emitted; on,
they move. ``drop`` rows leave in both states (they are duplicates). The
other two probe-gated changes — non-view tools as one text block with no
``structuredContent`` (:data:`NONVIEW_TEXT_ENV`) and retiring the ``card:``
line (:data:`CARD_LINE_RETIRED_ENV`) — are flags here too, each default OFF,
each fully built and tested both ways. The probe flips them; nothing else
may.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


__all__ = [
    "CARD_LINE_RETIRED_ENV",
    "CARD_META_ENV",
    "CARD_META_KEY",
    "CHANNEL_MAP",
    "ERROR_FIELDS",
    "KIND_BY_TOOL",
    "NONVIEW_TEXT_ENV",
    "ChannelSpec",
    "card_line_retired",
    "card_meta_move",
    "is_error_envelope",
    "kind_for_tool",
    "nonview_text_only",
    "partition",
    "unassigned_paths",
]

_log = logging.getLogger(__name__)

#: Probe arm 1 (spec 02 §2.7): move the `card` rows into `_meta["keel/card"]`.
CARD_META_ENV = "KEEL_CARD_META_MOVE"
#: Probe arm 3 (R5, R-25): non-view tools return one text block, no
#: `structuredContent`, no `outputSchema`; reading material renders as markdown.
NONVIEW_TEXT_ENV = "KEEL_NONVIEW_TEXT_ONLY"
#: Probe arm 2 (§2.6, R-9): retire `CARD_SHOWN_LINE`.
CARD_LINE_RETIRED_ENV = "KEEL_CARD_LINE_RETIRED"

#: The `_meta` key a card reads its card-only data from.
CARD_META_KEY = "keel/card"

_TRUE = frozenset({"1", "true", "yes", "on"})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def card_meta_move() -> bool:
    """Probe arm 1 — default OFF."""
    return _flag(CARD_META_ENV)


def nonview_text_only() -> bool:
    """Probe arm 3 — default OFF."""
    return _flag(NONVIEW_TEXT_ENV)


def card_line_retired() -> bool:
    """Probe arm 2 — default OFF."""
    return _flag(CARD_LINE_RETIRED_ENV)


@dataclass(frozen=True)
class ChannelSpec:
    """One kind's assignment: dotted paths per home.

    A path is a top-level key (``curve``), a view member (``view.tiles``), a
    nested member (``metadata.graph``, ``reference.series``) or a member of
    each element of a list (``view.runs[].curve``). ``conditional_card``
    names card paths that stay ``structured`` when their predicate says the
    caller asked for them (``view.source`` with ``include_source``).
    ``open`` kinds place undeclared keys in ``structured`` silently (the live
    kind, whose envelope is not reworked — R-21).
    """

    structured: frozenset[str]
    card: frozenset[str] = frozenset()
    drop: frozenset[str] = frozenset()
    conditional_card: dict[str, Callable[[dict], bool]] = field(default_factory=dict)
    open: bool = False
    #: A kind whose structured home is BOUNDED, not merely partitioned (the
    #: study card, 07 §3.3, RL-58): applied to the copy before the rows move,
    #: e.g. a list becomes its counts. The envelope (CLI, API) is untouched.
    reduce: Callable[[dict], dict] | None = None
    #: D-352 (5): a null member of an object leaves structuredContent (the card reads an absent member and a null
    #: one alike; a model reads neither). A null inside a list keeps its place. The envelope (CLI, API) and the
    #: card dict are untouched.
    omit_nulls: bool = False
    #: D-359 (5) (a): the kind's card rows leave structuredContent whatever the probe flag says. The study kind asks
    #: keel-api for its card (``card=full``) on every profile, so with the flag off (its default, the local full
    #: profile) card_data rode into model context: 12,743 B a study read against 13 §3.5's 8,000.
    always_move_card: bool = False
    #: MCP-FINAL S2 (13 §3.5): a structuredContent bound in bytes, kept at RUNTIME: past it, ``fit_steps`` move members to
    #: the card in order until it fits (``_fit``), never failing the call. 0: unbounded.
    structured_max: int = 0
    fit_steps: tuple[tuple[str, ...], ...] = ()


def _without_nulls(value: Any) -> Any:
    """``value`` with every ``None`` member of every object removed, recursively; list items keep their places."""
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


#: The shared error set (spec 02 §2.2): every kind's output schema carries it,
#: and an error envelope rides whole in `structuredContent`. The spec names
#: sixteen; the plan-wall handoff (`_handoff.HandoffRequired.to_envelope`)
#: carries six more, and they are part of the SAME refusal envelope, so they
#: are listed here rather than left to be discovered at runtime.
ERROR_FIELDS: frozenset[str] = frozenset(
    {
        "code",
        "message",
        "what_was_expected",
        "example",
        "suggested_next_action",
        "detail",
        "exit_code",
        "retryable",
        "docs_url",
        "limit_details",
        "limit_view",
        "resume",
        "quota",
        "quota_notice",
        "error_message",
        "info",
        # The handoff envelope (`HandoffRequired.to_envelope`).
        "blocked_action",
        "reason",
        "required_actor",
        "action_url",
        "cost",
        "talking_points",
    }
)

#: Every envelope may carry these (`OutcomeResult.to_envelope`).
_BASE = frozenset({"share_url", "run_id", "hero_url", "url_line", "resource_uri", "next"})

#: The ownership hint fields (`_ownership.ownership_envelope_fields` and
#: its honest-absence twin).
_OWNERSHIP = frozenset(
    {
        "ownership_resource_uri",
        "ownership_status",
        "next_recommended_action",
        "missing_evidence",
        "live_readiness_blockers",
        "projection_available",
        "unavailable_code",
        "unavailable_reason",
    }
)

_ELECTION = frozenset({"view.object", "view.at", "view.seq"})

_BACKTEST = ChannelSpec(
    structured=_BASE
    | _OWNERSHIP
    | _ELECTION
    | frozenset(
        {
            "status",
            "strategy_id",
            "strategy_name",
            "commit_id",
            "sequence_number",
            "engine",
            "queued_at",
            "started_at",
            "completed_at",
            "execution_time_s",
            "summary_metrics",
            "window",
            "window_note",
            "window_adjusted",
            "strategy_config",
            "quota",
            "quota_notice",
            # The paper/live timeframe heads-up (doc 07 §5, D-14).
            "floor_notice",
            "remaining",
            "good_result",
            "realism",
            # The money it needs to trade as backtested (design doc 14 §11);
            # full profile only (LISTED_OUTPUT_OMISSIONS).
            "capital_guide",
            "reference",
            "few_fills_note",
            "exposure",
            # Which way the positions went (R-003).
            "sides",
            # The capital and costs a run's numbers were computed at (Q-2270).
            "cost_model",
            "slice",
            # The period table (Q-2565): model-read numbers, no card draws it.
            "periods",
            "notes",
            "status_url",
            "tearsheet_url",
            "info",
            "error_message",
            "deploy",
            "terminal",
            "timed_out",
            "polls",
            "watched_for_s",
            "next_action",
            "sync_note",
            "auto_pushed_commit_id",
            "view.kind",
            "view.size",
            "view.name",
            "view.version",
            "view.status",
            "view.window",
            "view.metrics",
            "view.net_of",
            "view.error",
            "view.config",
            "view.window_note",
            "view.exposure_line",
            "view.sides_line",
            # What the counts are, and why a win rate is not measured (R-009).
            "view.count_basis",
            "view.markdown",
        }
    ),
    card=frozenset(
        {
            "curve",
            "render",
            "metrics_raw",
            "results_url",
            "results_url_expires_in_s",
            "reference.series",
            "view.url_line",
            "view.tiles",
            "view.more_tiles",
        }
    ),
    drop=frozenset({"period", "nudge"}),
)

_COMPARISON = ChannelSpec(
    structured=_BASE
    | _ELECTION
    | frozenset(
        {
            "strategy_id",
            "summary_text",
            "spec_diffs",
            "spec_diff_error",
            "comparability_warnings",
            "quota",
            "quota_notice",
            "reference",
            # Q-2223: every asked hold line's numbers (label, ret_pct, dd_pct,
            # span) stay model-visible; its series rides the card below, as
            # `reference.series` does.
            "references",
            "cost_model",
            # The runs' period tables aligned by period (Q-2565).
            "periods",
            "view.kind",
            "view.size",
            "view.name",
            "view.window",
            "view.baseline",
            "view.deltas",
            "view.warnings",
            "view.notes",
            "view.overlap",
            "view.rows",
            "view.runs",
            "view.error",
            "view.markdown",
        }
    ),
    card=frozenset(
        {
            "render",
            "metrics_raw_a",
            "metrics_raw_b",
            "metrics_raw_by_run",
            "reference.series",
            "references[].series",
            "view.url_line",
            "view.runs[].curve",
            # The two-id legacy key: byte-for-byte `spec_diffs[1]`, which the
            # model keeps (Q-1894). Card-routed, not dropped, so the flag-OFF
            # shape every frozen connector holds is unchanged.
            "spec_diff",
        }
    ),
    drop=frozenset(
        {
            "run_a",
            "run_b",
            "runs",
            "performance",
            "cost_profile",
            "performance_by_run",
            "cost_profile_by_run",
        }
    ),
)


#: "Trim for model, keep for cards" (founder, 2026-09-23; Q-1894): the
#: strategy row's storage plumbing — keys, hashes, the lock, lifecycle
#: bookkeeping — and the members `view` already carries for the model (the
#: name, the evidence's run window and version). The model keeps every id,
#: the description, status, deploy and provenance facts; the card keeps all
#: of it (its `normalizeView` reads `name` and the `latest_backtest_*`
#: members as fallbacks, restored by the `_meta` merge). Card rows move only
#: under the probe flag, so the flag-OFF shape is byte-identical.
_STRATEGY_METADATA_PLUMBING: frozenset[str] = frozenset(
    f"metadata.{member}"
    for member in (
        "name",
        "s3_key",
        "component_lock",
        "source_hash",
        "head_source_hash",
        "library_source_hash",
        "created_via",
        "backtest_state",
        "journey_projection",
        "deployment_schedule",
        "visibility",
        "latest_backtest_start_date",
        "latest_backtest_end_date",
        "latest_backtest_sequence",
    )
)


def _source_requested(envelope: dict) -> bool:
    """`view.source` is the model's when the caller asked for the source
    (`include_source=true` puts it at the top level too) or the result is a
    dry run's own source (Q-1840); otherwise it is the card's Code tab."""
    return envelope.get("dry_run") is True or "source" in envelope


_STRATEGY = ChannelSpec(
    structured=_BASE
    | _OWNERSHIP
    | _ELECTION
    | frozenset(
        {
            "strategy_id",
            "version",
            "parent",
            "validation",
            # A compose save that matched HEAD (Q-2102).
            "unchanged",
            "compiled",
            "compile_error",
            "dry_run",
            "universe",
            "workspace_sync",
            "quota",
            "quota_notice",
            # Plans v3 (spec 02 §C4.2, §C4.3a): the limits this source would meet (compose's dry run), what the
            # coin cap kept on a save, and their one line each; names only.
            "plan_findings",
            "plan_findings_notice",
            "plan_check_error",
            "plan_narrowed",
            "plan_narrowed_notice",
            "library_facts",
            "reference",
            "fork_note",
            "metadata",
            # include_source / include_versions (strategy_get).
            "source",
            "source_error",
            "versions",
            "versions_error",
            # fork / library fork / library get / diff / restore facts.
            "description_error",
            "name",
            "slug",
            "category",
            "risk_band",
            "headline",
            "default_variant_id",
            "entry_version",
            "data_as_of",
            "stale",
            "backtest_window",
            "variants",
            "source_slug",
            "variant_id",
            "is_default_variant",
            "mode",
            "ref_a",
            "ref_b",
            "added",
            "removed",
            "changed",
            "reordered",
            "component_version_changes",
            # The declaration half of a diff (Q-1898).
            "declarations",
            "summary_text",
            "error",
            "restored_from_ref",
            # Which version the restore brought back (Q-2270); a model-read
            # number beside the ref it was asked for (Q-2572).
            "restored_from_sequence",
            "new_sequence",
            "new_commit_id",
            "sync_note",
            "info",
            "view.kind",
            "view.name",
            "view.version",
            "view.status",
            "view.validation",
            "view.header",
            "view.change",
            "view.evidence",
            # No evidence, and why, by backtest_state (R-009).
            "view.evidence_note",
            # What a screen produces, each output with its unit (Q-3637):
            # the model reads it, and the card draws it as the Outputs list.
            "view.outputs",
            "view.size",
            "view.status_text",
            "view.previous_version",
            "view.head_version",
            "head_summary",
            # The strategy's newest run ids (Q-2269): what the model reads to
            # pick a run for summarize / compare / share; no card draws it.
            "recent_runs",
            "view.marks",
            "view.error",
            "view.parse_error",
            "view.markdown",
        }
    ),
    card=frozenset(
        {
            "render",
            "metadata.latest_backtest_metrics",
            "view.url_line",
            "view.structure",
            "view.categories",
            "view.source",
        }
    )
    | _STRATEGY_METADATA_PLUMBING,
    drop=frozenset({"metadata.graph", "metadata.source"}),
    conditional_card={"view.source": _source_requested},
)

_STATUS = ChannelSpec(
    structured=_BASE
    | frozenset(
        {
            "authenticated",
            "app_url",
            "api_url",
            "profile",
            "capabilities",
            "identity",
            "identity_error",
            "entitlements",
            "entitlements_error",
            "anonymous",
            "pending_claim",
            # Full profile only (R-22); absent on listed by construction.
            "toolsets_loaded",
            "workflow_routes",
            "view.kind",
            "view.size",
            "view.markdown",
            "view.object",
            "view.at",
            "view.seq",
        }
    ),
    # `live_monitoring_allowed` / `live_trading_allowed` are CLI-envelope only
    # (Q-1964): a field NAMED "live trading allowed: false" read to ChatGPT as
    # a session permission. `capabilities` carries the fact on MCP.
    drop=frozenset(
        {"tools_visible", "surface_hints", "live_monitoring_allowed", "live_trading_allowed"}
    ),
)

_LIVE = ChannelSpec(
    structured=frozenset(),
    card=frozenset({"curve", "equity", "history", "render", "positions[].curve"}),
    open=True,
)


def _study_reduce(envelope: dict) -> dict:
    """07 §3.3's bound: a study read's lists become counts and its check rows
    keep 5 (``_research_view.structured_study``); a plan keeps its step list
    (the plan has no card, and its steps are what it would run)."""
    from ._research_view import structured_study

    if envelope.get("plan_id") is not None:
        return envelope
    card_data = envelope.get("card_data")
    if (
        isinstance(card_data, dict)
        and card_data.get("rows")
        and isinstance(envelope.get("studies"), list)
    ):
        envelope = {
            **envelope,
            "studies": [
                {k: v for k, v in row.items() if k != "card_summary"}
                if isinstance(row, dict)
                else row
                for row in envelope["studies"]
            ],
        }
    reduced = structured_study(envelope)
    view = envelope.get("view")
    if isinstance(view, dict):
        reduced["view"] = dict(view)  # view.markdown leaves by the drop row
    _drop_uniform_results(reduced)
    return reduced


def _drop_uniform_results(reduced: dict) -> None:
    """MCP-FINAL S2: a check row's ``result`` names the result its bar reads. When every row names the same one, the
    study's own subject (its card's footer subject, else its title), it says nothing the study does not: a title of up
    to 200 characters was repeated on each of the five rows. A Compare's rows name different results and keep them."""
    rows = (
        (reduced.get("checks") or {}).get("rows")
        if isinstance(reduced.get("checks"), dict)
        else None
    )
    if not isinstance(rows, list) or not rows:
        return
    # a summary row (luck.summary) reads no one result and serves ``result: null``: it names nothing, so it does not
    # make the rows non-uniform (staging's Checks all carry one, so a set with None never dropped anything)
    named = {r.get("result") for r in rows if isinstance(r, dict) and r.get("result") is not None}
    card = reduced.get("card_summary") if isinstance(reduced.get("card_summary"), dict) else {}
    subject = (card.get("footer") or {}).get("subject") or card.get("title")
    if not subject and isinstance(reduced.get("study"), dict):
        subject = reduced["study"].get("title")
    if len(named) == 1 and subject and named == {subject}:
        for r in rows:
            if isinstance(r, dict):
                r.pop("result", None)


#: The study kind's structured fields: every research envelope field (07 §3.1,
#: §3.3's channel table, the plan, the data page, the brief, the list).
_STUDY_FIELDS = frozenset(
    {
        "study",
        "footing",
        "counts",
        "stale",
        "links",
        "draft",
        # RI-483: the read an edit of its source is made on (keel_research_get).
        "base",
        "progress",
        "not_measured",
        "card_summary",
        "checks",
        "steps",
        "views",
        "notes",
        "notes_total",
        # SV-36 (63deac5b4a): an Entries study's fixed inputs line on the study read
        "entries_inputs_words",
        "saved",
        "page",
        "can_run",
        "can_retry",
        "subjects",
        "quota",
        "quota_notice",
        "plan_id",
        "expires_at",
        "class",
        "writes",
        "linked_reason",
        "issues",
        "runs",
        "addresses_carried",
        "addresses_changed",
        "addresses_moved",
        "estimate",
        "allowance",
        "run_allowed",
        "limits",
        "locked",
        "versions_tried",
        "preview",
        "words",
        "pagination",
        "facts",
        "subject",
        "rows_total",
        "by_reason",
        "studies",
        "studies_total",
        "by_state",
        "projects",
        "projects_total",
        "families_total",
        "set_aside",
        "set_aside_total",
        "planned",
        "planned_total",
        "paper_and_live",
        "paper_and_live_total",
        # RI-592: served fields that had no home, so they only warned: the plan's
        # paged children (07 §3.5), the add-plan's rows (04 §1.7, RI-385), a map
        # data page's filling grid (D-158) and the share read's Keel fact (RI-235).
        "children",
        "added",
        "refused",
        "filling",
        "by_keel",
        # RI-592: a project read through keel_research_get (D-189, D-177): as
        # served today, now homed
        "project_id",
        "name",
        "question",
        "archived",
        "items",
        "items_total",
        # WL-SV-08 (13 §2.1, D8): the one-row receipt's served line (addresses, project) and a project's page; a
        # plan's proposed edit (keel-api's PlanResponse.edit).
        "card_line",
        "link",
        "edit",
        # SERVE's preview read: what the preview changes against the kept version.
        "preview_delta",
        "view.kind",
        "view.size",
        # 13 §2.1 (WL-AG-10): the card picks its layout by it.
        "view.mode",
        "view.version",
    }
)

#: RI-610 (measured, 13 §3.5): a list row keeps, in structuredContent, what the model reads a row by: its id, kind,
#: title and state words (the text names each row too). Everything else a row carries is the card's: measured on
#: keel-api's own list read, a 25-row page of bare rows was 8,575 B (343 B a row) before any card, and a v2 card
#: per row adds about 2.4 KB each. The card's paths are each row's own (same length as `studies`), so the adapter's
#: merge puts them back by index.
_LIST_ROW_CARD = frozenset(
    f"studies[].{field}"
    for field in (
        "card_summary",
        "state",
        "headline",
        "set_aside",
        "archived",
        "maker",
        "project_ids",
        "notes_total",
        "finished_at",
        "version",
        "card_version",
        "plan_id",
        "stale",
    )
)

#: D-363 (2b) (ENGINE 910e68d3fd): a check row's "Your bars: stricter · looser" lens and the template default's words
#: are the app's and the card's (the bar editor), never the model's: a reply quotes the row's bar and value words. In
#: structuredContent they put five real fixtures back over 13 §3.5's 8,000 B (chk_v3 reads 8,029, run-partial 8,491).
#: They ride ``_meta["keel/card"]`` on the reduced rows, so the card's merge puts them back by index.
_CHECK_ROW_CARD = frozenset(
    f"checks.rows[].{field}"
    # CARD-FIX: a row's `sample_line` (D-347, the app's sample line) repeats its `sample_words` on every real row (the
    # D9 string the text quotes); with the lead naming its misses, run-partial read 8,017 B of structuredContent.
    # MCP-FINAL S2: a row's raw ``interval`` ({lo, hi, level, method}) and ``sample`` ({first_day, ...}) dicts are the
    # card's; the model reads the served ``interval_words`` and ``sample_words`` (D9: one string per number) and keeps
    # the raw ``value`` (13 §3.2: raw values stay for an agent that needs precision). A real 7-bar Trader Check spent
    # ~517 B a row against 13 §3.5's ≈ 290.
    for field in ("stricter", "looser", "bar_default_words", "sample_line", "interval", "sample")
)

#: CARD-RENDER (13 §3.5): four real staging plan results read 8,265-14,386 B of structuredContent against 8,000
#: (``filled`` 3.2 KB, ``source`` 2.8 KB, ``words`` 2.5 KB). A plan keeps what the model plans and runs by (its id,
#: steps, counts, issues, the work, count and wall words); these are the card's or the app's, never the model's:
#: ``filled`` and ``words.filled`` (what the template filled in: the footing and the steps say it), the plan card's
#: line and its no-plan-name count and wall (D-381 (1)), and the app's proposal-strip note. A plan's ``source``
#: leaves by the drop row (D-353: structuredContent never carries a source; up to 131,072 B, so not the card's
#: either: the card draws none).
_PLAN_CARD = frozenset(
    {
        "filled",
        "words.filled",
        "words.card_line",
        "words.card_allowance",
        "words.card_limit",
        "words.strip",
        # each step's run words, for the plan card's step list (the text writes the counts itself)
        "steps[].words",
    }
)

_STUDY = ChannelSpec(
    structured=_BASE | _ELECTION | _STUDY_FIELDS,
    # The headline's one home is card_summary; the text carries each measure's
    # interval; view.markdown is rendered into `content` (07 §3.3). RI-592:
    # `mix` (RI-013's `{ref, included}`) is the app's mix row; it names no
    # subject key, so an agent makes a signal by the mix subject's key, never
    # by this, and its up-to-50 refs stay out of the budget. `coin_choices` is
    # New study's Coins row (06 §3.10): the app's, never an agent's. `runs_on` (WL-SV-31) is the home's per-template
    # capability line on a list read, the app's too.
    drop=frozenset({"headline", "view.markdown", "mix", "coin_choices", "runs_on", "source"}),
    # D-332 (6), 13 §3.1, §3.8 (WL-AG-09): the card's own data rides `_meta["keel/card"]`, read by no model: the
    # thumb (up to 3,000 B; a real chk_v3 read was 8,051 B of structuredContent with it), `card_data` on its own
    # top-level key (RI-612: the adapter's merge drops array items past the structured array's length, so 40 card
    # rows can never ride at `checks.rows`), and a list read's per-row cards (RI-610), the same length as the
    # structured `studies`, so they merge back by index. `_study_reduce` drops those row cards outright once the
    # list serves `card_data.rows` (13 §3.5: the card reads those, never both).
    # A data read's rows (`data`, and the library's items) are written whole in the text block (07 §3.2 rule 4,
    # raw values included) and ride the card for the chart card: CARDUI measured a Map's view-0 page at 12,230 B of
    # structuredContent with its 25 rows in it. The page's facts and cursor stay.
    # R4 (06 §3.5): a top row's sparkline is the thumb's own series, card data like the thumb, never the model's.
    card=frozenset({"card_summary.thumb", "card_summary.top5.rows[].spark", "card_data", "data"})
    | _LIST_ROW_CARD
    | _CHECK_ROW_CARD
    | _PLAN_CARD,
    reduce=_study_reduce,
    # D-352 (5): CARDUI measured six real fixtures over 13 §3.5's 8,000 B, 125 B of each chk_v3 read being
    # `prop_target_words: null` on every check row.
    omit_nulls=True,
    always_move_card=True,
    structured_max=8_000,
    # Least read first; each step's members move to the card (the card draws them as before), the text block is
    # untouched (built from the envelope), and the model keeps the study, its counts, links, can_run and the bars'
    # count and met.
    fit_steps=(
        ("checks.rows[].withheld_reason", "checks.rows[].bar", "checks.rows[].op"),
        ("card_summary.numbers[].sample", "card_summary.top5"),
        ("footing", "words", "progress"),
        ("checks.rows", "card_summary.numbers"),
    ),
)

#: kind → its channel spec (spec 02 §3).
CHANNEL_MAP: dict[str, ChannelSpec] = {
    "screener": ChannelSpec(
        structured=_BASE
        | frozenset(
            {
                "view",
                "screener",
                "run",
                "in_flight",
                "headline",
                "manifest",
                "latest_top",
                "labels",
                "passing",
                "actions",
                "copy",
                # keel_screener_view(include_view_source=true), L13 §2a.3.
                "view_source",
                # A screen run spends a backtest run: the submit's counters
                # (`quota` / `quota_notice` are error-set fields), Q-3634.
                "remaining",
                # The ranked output over every coin with a value (keel-api's
                # `completed.range`, Q-3638/Q-3639): small, and the one fact
                # about the coins below the shown rows.
                "range",
                # keel_screener_view_set: the PUT's answer about the write —
                # the revision saved, the view check, the output units.
                "revision",
                "check",
                "units",
                # Plans v3 (spec 02 §C4.3a): what the coin cap kept, and its one line.
                "plan_narrowed",
                "plan_narrowed_notice",
            }
        ),
        card=frozenset({"latest", "hero", "graphs"}),
    ),
    "backtest": _BACKTEST,
    "comparison": _COMPARISON,
    "strategy": _STRATEGY,
    "status": _STATUS,
    "live": _LIVE,
    "study": _STUDY,
}

#: tool → kind. The 12 markdown view tools (three kinds + status) and the
#: card-backed live tools: the monitor (spec 02 §2.2) and the two paper writes
#: (Q-7603).
KIND_BY_TOOL: dict[str, str] = {
    # Research (07 §3.3): the five view tools draw the study kind.
    "keel_research_plan": "study",
    "keel_research_run": "study",
    "keel_research_get": "study",
    "keel_research_view_set": "study",
    "keel_research_brief": "study",
    "keel_screener_run": "screener",
    "keel_screener_view": "screener",
    "keel_screener_view_set": "screener",
    "keel_backtest_run": "backtest",
    "keel_backtest_watch": "backtest",
    "keel_backtest_summarize": "backtest",
    "keel_backtest_compare": "comparison",
    "keel_strategy_get": "strategy",
    "keel_strategy_compose": "strategy",
    "keel_strategy_fork": "strategy",
    "keel_strategy_diff": "strategy",
    "keel_library_get": "strategy",
    "keel_library_fork": "strategy",
    "keel_strategy_restore": "strategy",
    "keel_account_status": "status",
    "keel_live_monitor": "live",
    # A paper run's start and its lifecycle writes draw the live card's paper
    # receipt (Q-7603; S06 §3.5.6, no new card kind), so they take the
    # monitor's registration mode: the envelope rides as structuredContent and
    # the `render` block moves to the card, leaving the text block as before.
    "keel_paper_deploy": "live",
    "keel_paper_control": "live",
}


def kind_for_tool(tool_name: str | None) -> str | None:
    return KIND_BY_TOOL.get(tool_name or "")


def is_error_envelope(envelope: Any) -> bool:
    """A spec §13.5 error envelope (`KeelError.to_envelope`, `envelope_error`,
    the handoff): `code` + `message` + `suggested_next_action`, no `view`."""
    return (
        isinstance(envelope, dict)
        and "view" not in envelope
        and isinstance(envelope.get("code"), str)
        and "message" in envelope
        and "suggested_next_action" in envelope
    )


#: The codes a plan limit refuses with (mcp-conversion D-8, `keel.errors.QUOTA_CODES`): carried as the envelope's own
#: code, or under ``detail.code`` / ``example.code`` beside ``insufficient_entitlements`` (a quota 403, a research wall).
PLAN_LIMIT_CODES = frozenset({"quota_exhausted", "quota_cap_reached", "plan_feature_unavailable"})
#: A plan limit refused under its own code: the screen refresh floor (the plan's fastest auto-refresh, a 403), the
#: coins per screen (a 422, plans v3 spec 02 §C4.3) and the paper/live timeframe floor (a 422 wherever no tool turns
#: it into a handoff). Spec 02 §F G9: a plan limit is never a failed call.
PLAN_LIMIT_OWN_CODES = frozenset(
    {"SCREENER_REFRESH_BELOW_FLOOR", "SCREEN_COINS_OVER_PLAN", "SERVING_CLOCK_BELOW_PLAN_FLOOR"}
)


def is_plan_limit_envelope(envelope: Any) -> bool:
    """An error envelope whose refusal is a PLAN LIMIT (Q-7927): an expected outcome the person can read, not a
    failed call. The MCP adapter leaves ``isError`` off for it, so Claude's listing health badge (which counts
    ``isError: true`` results) does not read a spent allowance as a broken tool; the envelope itself, its code, words
    and "don't retry" instruction, is unchanged.

    One of: a handoff that carries a ``limit_view`` (a backtest or paper wall: a quota or the paper floor); a quota
    code as the envelope's own code; ``insufficient_entitlements`` with a quota code under ``detail`` or ``example``
    (a quota 403, a research wall); a plan-limit code of the API's own (the screen refresh floor). A scope-missing
    403, a sign-in handoff, a 404 or a 5xx stays an error."""
    if not is_error_envelope(envelope):
        return False
    code = envelope.get("code")
    if code == "handoff_required":
        return isinstance(envelope.get("limit_view"), dict)
    if code in PLAN_LIMIT_CODES or code in PLAN_LIMIT_OWN_CODES:
        return True
    if code == "insufficient_entitlements":
        for key in ("detail", "example"):
            inner = envelope.get(key)
            if isinstance(inner, dict) and inner.get("code") in PLAN_LIMIT_CODES:
                return True
    return False


# ── Path helpers ──────────────────────────────────────────────────────


def _split(path: str) -> list[str]:
    return path.split(".")


def _pop_path(obj: Any, parts: list[str]) -> Any:
    """Remove `parts` from `obj` (in place); return what was removed, keeping
    its original nesting (a list member returns a per-index list)."""
    if not parts or not isinstance(obj, dict):
        return None
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        items = obj.get(head[:-2])
        if not isinstance(items, list):
            return None
        taken = [_pop_path(item, rest) if isinstance(item, dict) else None for item in items]
        if not any(t is not None for t in taken):
            return None
        return {head[:-2]: [({} if t is None else t) for t in taken]}
    if head not in obj:
        return None
    if not rest:
        return {head: obj.pop(head)}
    inner = _pop_path(obj[head], rest)
    return None if inner is None else {head: inner}


def _merge(into: dict, extra: dict) -> None:
    """Deep, path-preserving merge (the card's own rule, host-adapter.js)."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        elif isinstance(value, list) and isinstance(into.get(key), list):
            for index, item in enumerate(value):
                if index < len(into[key]) and isinstance(item, dict):
                    if isinstance(into[key][index], dict):
                        _merge(into[key][index], item)
        else:
            into[key] = value


def unassigned_paths(envelope: dict, kind: str) -> list[str]:
    """Top-level keys and `view.*` members no home claims (G1's subject)."""
    spec = CHANNEL_MAP[kind]
    if spec.open or is_error_envelope(envelope):
        return []
    declared = spec.structured | spec.card | spec.drop
    tops = {p.split(".")[0].removesuffix("[]") for p in declared}
    views = {p for p in declared if p.startswith("view.")}
    missing = []
    for key in envelope:
        if key == "view":
            view = envelope["view"]
            # A bare `view` row claims the whole object (the screener spec's
            # one home for its view); only member rows are checked one by one.
            if "view" in declared:
                continue
            if isinstance(view, dict):
                for member in view:
                    if f"view.{member}" not in views and not any(
                        p.startswith(f"view.{member}[]") for p in declared
                    ):
                        missing.append(f"view.{member}")
            continue
        if key not in tops and key not in ERROR_FIELDS:
            missing.append(key)
    return missing


def _bytes(value: Any) -> int:
    import json

    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode())


def _fit(structured: dict, card: dict, spec: ChannelSpec, kind: str) -> None:
    """MCP-FINAL S2 (13 §3.5): structuredContent past ``spec.structured_max`` (a 200-character title, its subject's
    name on every row, a linked study and a Draft) gives members to the card step by step (``spec.fit_steps``) until it
    fits; a step that moves nothing is skipped. Never raises: a call never fails on its size. Logged with what moved."""
    size = _bytes(structured)
    if size <= spec.structured_max:
        return
    moved: list[str] = []
    for step in spec.fit_steps:
        for path in step:
            taken = _pop_path(structured, _split(path))
            if taken is not None:
                _merge(card, taken)
                moved.append(path)
        if _bytes(structured) <= spec.structured_max:
            break
    _log.warning(
        "channel map: %s structuredContent %s B over %s B; moved %s to the card (now %s B)",
        kind, size, spec.structured_max, moved, _bytes(structured),
    )  # fmt: skip


def partition(
    envelope: dict,
    kind: str,
    *,
    move_card: bool | None = None,
) -> tuple[dict, dict]:
    """`(structuredContent, _meta["keel/card"])` for one envelope.

    An error envelope is structured whole, card empty (R-27). Otherwise the
    `drop` rows leave, and — when the probe arm is on — the `card` rows move
    to the card dict, keeping their original paths so the card's deep merge
    puts them back where it reads them. Off, the card dict is empty and the
    card rows stay structured. An undeclared field is structured and logged,
    never silently lost (spec 02 §3).
    """
    if kind not in CHANNEL_MAP or is_error_envelope(envelope):
        return envelope, {}
    spec = CHANNEL_MAP[kind]
    if move_card is None:
        move_card = card_meta_move()
    structured = deepcopy(envelope)
    if spec.reduce is not None:
        structured = spec.reduce(structured)
    for path in sorted(spec.drop):
        _pop_path(structured, _split(path))
    card: dict = {}
    if move_card or spec.always_move_card:
        for path in sorted(spec.card):
            predicate = spec.conditional_card.get(path)
            if predicate is not None and predicate(envelope):
                continue
            taken = _pop_path(structured, _split(path))
            if taken is not None:
                _merge(card, taken)
    if spec.omit_nulls:
        structured = _without_nulls(structured)
    if spec.structured_max:
        _fit(structured, card, spec, kind)
    if not spec.open:
        for path in unassigned_paths(structured, kind):
            _log.warning("channel map: %s field %r has no declared home", kind, path)
    return structured, card
