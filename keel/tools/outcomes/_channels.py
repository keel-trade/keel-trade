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
            "remaining",
            "good_result",
            "realism",
            "reference",
            "few_fills_note",
            "exposure",
            # The capital and costs a run's numbers were computed at (Q-2270).
            "cost_model",
            "slice",
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

#: kind → its channel spec (spec 02 §3).
CHANNEL_MAP: dict[str, ChannelSpec] = {
    "backtest": _BACKTEST,
    "comparison": _COMPARISON,
    "strategy": _STRATEGY,
    "status": _STATUS,
    "live": _LIVE,
}

#: tool → kind. The 12 markdown view tools (three kinds + status) and the one
#: card-backed live tool (spec 02 §2.2).
KIND_BY_TOOL: dict[str, str] = {
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
    for path in sorted(spec.drop):
        _pop_path(structured, _split(path))
    card: dict = {}
    if move_card:
        for path in sorted(spec.card):
            predicate = spec.conditional_card.get(path)
            if predicate is not None and predicate(envelope):
                continue
            taken = _pop_path(structured, _split(path))
            if taken is not None:
                _merge(card, taken)
    if not spec.open:
        for path in unassigned_paths(structured, kind):
            _log.warning("channel map: %s field %r has no declared home", kind, path)
    return structured, card
