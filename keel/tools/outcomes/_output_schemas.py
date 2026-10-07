"""Declared `outputSchema`s for the view-carrying tools (Q-1788).

A tool in `VIEW_TOOLS` returns a `ToolResult` whose `structuredContent`
is the WHOLE envelope — a success envelope carrying `view`, or a spec
§13.5 error envelope (`code` + `message` + …). FastMCP cannot infer that
from a function with no return annotation, so these tools published no
`outputSchema` at all, and ChatGPT's developer mode flagged every one
("OUTPUT SCHEMA RECOMMENDED"). Every other tool returns `-> str` and
already publishes FastMCP's `{"result": <string>}` wrapper.

The schemas describe what the envelope builders ACTUALLY emit and no
more: the fields every envelope may carry (`_base.OutcomeResult
.to_envelope`), the error fields (`errors.KeelError.to_envelope`,
`_base.envelope_error`), and the `view` each builder produces
(`_backtest_view.build_backtest_view` / `build_comparison_view`,
`_strategy_view.build_view`). Nothing is `required` at the top level —
a success and an error envelope share no field — and every object stays
open (`additionalProperties` is left at its default), because a tool
adds fields through `extra` and a schema that rejects a real envelope is
worse than none. `tests/test_output_schemas.py` validates real
envelopes from each builder and each real error path against these.

Schema text is listed-surface copy: the policy scan reads it too.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


__all__ = ["LISTED_OUTPUT_OMISSIONS", "OUTPUT_SCHEMAS", "output_schema_for"]


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


_STR: dict[str, Any] = {"type": "string"}
_OBJ: dict[str, Any] = {"type": "object"}

#: One headline/"more" tile (`_backtest_view._tile`).
_TILE: dict[str, Any] = {
    "type": "object",
    "required": ["key", "label", "display"],
    "properties": {
        "key": _STR,
        "label": _STR,
        "short_label": _STR,
        "value": {"type": ["number", "string", "null"]},
        "display": _STR,
        "note": _STR,
    },
}

#: `window_block` — the two ends of a run's window, either may be absent.
_WINDOW: dict[str, Any] = {
    "type": ["object", "null"],
    "properties": {
        "start": _nullable("string"),
        # EXCLUSIVE — the first day the run did not cover (R-3: the meaning
        # every frozen connector holds). A range for a reader ends on
        # `last_bar`.
        "end": _nullable("string"),
        # Additive (spec 03 §2.5): the last covered day, the covered-day
        # count, and on a completed run the warm-up and the chart's start.
        "last_bar": _nullable("string"),
        "days": {"type": ["integer", "null"]},
        "warmup_bars": {"type": ["integer", "null"]},
        "chart_start": _nullable("string"),
    },
}

#: The election key every view carries (`_base.election_key`).
_ELECTION: dict[str, Any] = {
    "object": _STR,
    "at": _STR,
    "seq": {"type": "integer"},
}

_BACKTEST_VIEW: dict[str, Any] = {
    "type": "object",
    "description": "One run: status, window, metrics and the tiles in display order.",
    "required": ["kind", "status", "markdown"],
    "properties": {
        "kind": {"const": "backtest"},
        "size": {"enum": ["receipt", "evidence"]},
        "name": _nullable("string"),
        "version": {"type": ["integer", "null"]},
        "status": _STR,
        "window": _WINDOW,
        "metrics": _OBJ,
        "net_of": {"type": "array", "items": _STR},
        "error": _nullable("string"),
        "tiles": {"type": "array", "items": _TILE},
        "more_tiles": {"type": "array", "items": _TILE},
        # The run's own commit's config (Q-1789).
        "config": {
            "type": "object",
            "required": ["line"],
            "properties": {
                "line": _STR,
                "universe": _nullable("string"),
                "clock": _nullable("string"),
                "execution": _OBJ,
                "blocks": {"type": "array", "items": _STR},
            },
        },
        # The platform moved the requested window (Q-1842): the server's
        # sentence naming both dates and why.
        "window_note": _STR,
        # How much of the window held positions (spec 07 §5, Q-1993's stamp).
        "exposure_line": _STR,
        "url_line": _STR,
        "markdown": _STR,
        **_ELECTION,
    },
}

_COMPARISON_VIEW: dict[str, Any] = {
    "type": "object",
    "description": "Several runs: one entry per run, deltas against the first, rows in display order.",
    "required": ["kind", "runs", "rows", "markdown"],
    "properties": {
        "kind": {"const": "comparison"},
        "size": {"const": "comparison"},
        "name": {},
        "window": {},
        "runs": {"type": "array", "items": _OBJ},
        "baseline": {"type": "integer"},
        "deltas": {"type": "array", "items": {"type": ["object", "null"]}},
        "warnings": {"type": "array", "items": _STR},
        "notes": {"type": "array", "items": _STR},
        "overlap": {
            "type": "object",
            "description": "The common window when the runs' windows differ, and how much that matters.",
            "properties": {"start": _STR, "end": _STR, "severity": {"enum": ["note", "warning"]}},
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "label"],
                "properties": {"key": _STR, "label": _STR, "short_label": _STR},
            },
        },
        "url_line": _STR,
        "markdown": _STR,
        **_ELECTION,
    },
}

_STRATEGY_VIEW: dict[str, Any] = {
    "type": "object",
    "description": "One strategy: its structure graph, change marks, evidence and the rendered markdown.",
    "required": ["markdown"],
    "properties": {
        "kind": {"const": "strategy"},
        "name": _nullable("string"),
        "version": {"type": ["integer", "string", "null"]},
        "status": {},
        "validation": {},
        "header": {},
        "structure": {},
        "change": {},
        "evidence": {},
        "categories": {},
        "size": _STR,
        "status_text": {},
        "previous_version": {},
        # A view of a non-HEAD version names HEAD apart (ChatGPT R4 #1).
        "head_version": {"type": ["integer", "null"]},
        "marks": {"type": "boolean"},
        "source": {},
        # A dry run whose source did not parse (Q-1840): the parser's
        # message, and the flag that there is no structure to draw.
        "error": _nullable("string"),
        "parse_error": {"type": "boolean"},
        "url_line": _STR,
        "markdown": _STR,
        **_ELECTION,
    },
}


#: `summary_metrics` — the drawdown's one sign, stated where a model reads
#: the block (Q-1805): the same number and sign as `view.metrics`.
_SUMMARY_METRICS: dict[str, Any] = {
    "type": "object",
    "description": (
        "Headline metrics from the run's sealed results, percents in percent units. "
        "The drawdown is `max_drawdown_pct`: negative or zero, the same number and "
        "sign as `view.metrics`."
    ),
    "properties": {
        "max_drawdown_pct": {
            "type": "number",
            "maximum": 0,
            "description": "Largest peak-to-trough fall in percent, ≤ 0 (−60.9 is a 60.9% fall).",
        },
    },
}


_ARR: dict[str, Any] = {"type": "array"}
_BOOL: dict[str, Any] = {"type": "boolean"}
_INT: dict[str, Any] = {"type": "integer"}
_ANY: dict[str, Any] = {}

#: The shared error set (spec 02 §2.2 — `_channels.ERROR_FIELDS`): every
#: kind carries it, because a view tool's error rides as `structuredContent`
#: (Q-1785) — the spec §13.5 fields, the handoff's refusal fields (Q-1806)
#: and the data lines an error may carry.
_ERROR_PROPS: dict[str, Any] = {
    "code": _STR,
    "message": _STR,
    "what_was_expected": _STR,
    "example": _ANY,
    "suggested_next_action": _OBJ,
    "detail": _OBJ,
    "exit_code": _INT,
    "retryable": _BOOL,
    "docs_url": _nullable("string"),
    "limit_details": _OBJ,
    "limit_view": _OBJ,
    "resume": _OBJ,
    "quota": _ARR,
    "quota_notice": _STR,
    "error_message": _nullable("string"),
    "info": _STR,
    "blocked_action": _STR,
    "reason": _STR,
    "required_actor": _STR,
    "action_url": _STR,
    "cost": _OBJ,
    "talking_points": _ARR,
}

#: Every envelope may carry these (`OutcomeResult.to_envelope`).
_BASE_PROPS: dict[str, Any] = {
    "run_id": _STR,
    "hero_url": _STR,
    "share_url": _nullable("string"),
    "url_line": _STR,
    "resource_uri": _STR,
    "next": _nullable("string"),
}

_OWNERSHIP_PROPS: dict[str, Any] = {
    "ownership_resource_uri": _nullable("string"),
    "ownership_status": _nullable("string"),
    "next_recommended_action": _ANY,
    "missing_evidence": _ARR,
    "live_readiness_blockers": _ARR,
    "projection_available": _BOOL,
    "unavailable_code": _STR,
    "unavailable_reason": _STR,
}

#: The persisted window as keel-api serves it (spec 03 §2.5): the request,
#: what ran (`end_exclusive` half-open, `last_bar` the last covered day), and
#: the start-move reason. Nested members stay open.
_SERVED_WINDOW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requested": _OBJ,
        "ran": _OBJ,
        # The window as the run tool's input (Q-2422): `ran.start` inclusive,
        # `ran.end_exclusive` exclusive — passing it back runs this window.
        "rerun": {
            "type": "object",
            "properties": {"start_date": _STR, "end_date": _STR},
        },
        # Only when an omitted start took the clock's default (Q-2422).
        "default": {
            "type": "object",
            "properties": {"timeframe": _STR, "days": _INT},
        },
        "warmup_bars": {"type": ["integer", "null"]},
        "chart_start": _nullable("string"),
        "start_reason_code": _nullable("string"),
        "start_reason": _nullable("string"),
        "policy": _nullable("string"),
    },
}

#: The BTC hold reference's numbers (spec 03 §2.2) — never a metric of the run.
_REFERENCE: dict[str, Any] = {
    "type": "object",
    "description": "BTC hold, price only, over the run's span (run 1's on a comparison).",
    "properties": {
        "label": _STR,
        "basis": _STR,
        "ret_pct": {"type": "number"},
        "dd_pct": {"type": "number", "maximum": 0},
    },
}

#: How much of the window held positions (spec 07 §5): the worker's stamp
#: verbatim plus the one line the app shows.
_EXPOSURE: dict[str, Any] = {
    "type": "object",
    "description": (
        "Bars that held a position out of the window's bars, and the first and last "
        "such bar. A late first position may be indicator warm-up or the strategy "
        "staying flat. `gross_mean` / `gross_max`: the book's measured gross "
        "(sum of |position value| / equity) over the held bars, on runs that carry it."
    ),
    "properties": {
        "bars_held": _INT,
        "n_bars": _INT,
        "first_position_at": _nullable("string"),
        "last_position_at": _nullable("string"),
        "gross_mean": {"type": "number"},
        "gross_max": {"type": "number"},
        "line": _STR,
    },
}

#: Part of a completed run, as it ran (spec 07 §6): keel-api's block.
_SLICE: dict[str, Any] = {
    "type": "object",
    "description": (
        "Part of this run as it ran, at the run's bar clock: return, P&L on `capital`, "
        "max drawdown (≤ 0) and bars over the slice; positions held at its start carry over."
    ),
    "properties": {
        "bars": _INT,
        "timeframe": _nullable("string"),
        "first_day": _STR,
        "last_day": _STR,
        "capital": {"type": "number"},
        "return_pct": {"type": ["number", "null"]},
        "pnl": {"type": ["number", "null"]},
        "max_drawdown_pct": {"type": "number", "maximum": 0},
        "summary": _STR,
        "basis": _STR,
    },
}

#: `_backtest_view.cost_model_block` — the capital and costs a run's numbers
#: were computed at (Q-2270).
_COST_MODEL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "init_cash": {"type": "number"},
        "fees": {"type": "number"},
        "slippage": {"type": "number"},
        "fees_bps": {"type": "number"},
        "slippage_bps": {"type": "number"},
        "set_for_run": {"type": "array", "items": _STR},
        "line": _STR,
    },
}

_BACKTEST_PROPS: dict[str, Any] = {
    **_BASE_PROPS,
    **_OWNERSHIP_PROPS,
    "view": _BACKTEST_VIEW,
    "summary_metrics": _SUMMARY_METRICS,
    "strategy_config": _STR,
    "status": _STR,
    "strategy_id": _nullable("string"),
    "strategy_name": _nullable("string"),
    "commit_id": _nullable("string"),
    "sequence_number": {"type": ["integer", "null"]},
    "engine": _nullable("string"),
    "queued_at": _nullable("string"),
    "started_at": _nullable("string"),
    "completed_at": _nullable("string"),
    "execution_time_s": {"type": ["number", "null"]},
    "window": _SERVED_WINDOW,
    "window_note": _STR,
    "window_adjusted": _OBJ,
    "quota": _ARR,
    "quota_notice": _STR,
    "remaining": _OBJ,
    "good_result": _OBJ,
    "realism": _OBJ,
    "reference": _REFERENCE,
    "few_fills_note": _STR,
    "exposure": _EXPOSURE,
    "cost_model": _COST_MODEL,
    "slice": _SLICE,
    "notes": _ANY,
    "status_url": _STR,
    "tearsheet_url": _STR,
    "info": _STR,
    "error_message": _nullable("string"),
    "deploy": _STR,
    "terminal": _BOOL,
    "timed_out": _BOOL,
    "polls": _INT,
    "watched_for_s": {"type": "number"},
    "next_action": _OBJ,
    "sync_note": _STR,
    "auto_pushed_commit_id": _nullable("string"),
}

_COMPARISON_PROPS: dict[str, Any] = {
    **_BASE_PROPS,
    "view": _COMPARISON_VIEW,
    "strategy_id": _STR,
    "summary_text": _STR,
    "spec_diffs": _ARR,
    "spec_diff_error": _STR,
    "comparability_warnings": _ARR,
    "quota": _ARR,
    "quota_notice": _STR,
    "reference": _REFERENCE,
    # Q-2223: every hold line `holds` asked for, numbers only — each series
    # rides the card (`references[].series`), as `reference.series` does.
    "references": {
        "type": "array",
        "description": "One hold line per `holds` symbol, price only, over the span every run covers.",
        "items": _REFERENCE,
    },
    "cost_model": _COST_MODEL,
}

_STRATEGY_PROPS: dict[str, Any] = {
    **_BASE_PROPS,
    **_OWNERSHIP_PROPS,
    "view": _STRATEGY_VIEW,
    "strategy_id": _nullable("string"),
    "version": {"type": ["integer", "string", "null"]},
    "parent": _STR,
    "validation": _OBJ,
    "unchanged": _BOOL,
    "compiled": _BOOL,
    "compile_error": _nullable("string"),
    "dry_run": _BOOL,
    "universe": _OBJ,
    "workspace_sync": _OBJ,
    "quota": _ARR,
    "quota_notice": _STR,
    "library_facts": _STR,
    "reference": _REFERENCE,
    "fork_note": _OBJ,
    "metadata": _OBJ,
    "source": _ANY,
    "source_error": _STR,
    "head_summary": _OBJ,
    "recent_runs": {
        "type": "array",
        "description": (
            "The strategy's newest backtest runs, newest first (up to 5): run_id, "
            "status, version, the version's commit message, window, completed_at "
            "and the headline metrics."
        ),
        "items": {
            "type": "object",
            "properties": {
                "run_id": _STR,
                "status": _nullable("string"),
                "version": {"type": ["integer", "string", "null"]},
                "message": _nullable("string"),
                "window": _OBJ,
                "completed_at": _nullable("string"),
                "metrics": _OBJ,
            },
        },
    },
    "versions": _ANY,
    "versions_error": _STR,
    "description_error": _STR,
    "name": _nullable("string"),
    "slug": _nullable("string"),
    "category": _nullable("string"),
    "risk_band": _ANY,
    "headline": _ANY,
    "default_variant_id": _nullable("string"),
    "entry_version": _ANY,
    "data_as_of": _nullable("string"),
    "stale": {"type": ["boolean", "null"]},
    "backtest_window": _ANY,
    "variants": _ANY,
    "source_slug": _nullable("string"),
    "variant_id": _nullable("string"),
    "is_default_variant": {"type": ["boolean", "null"]},
    "mode": _STR,
    "ref_a": _STR,
    "ref_b": _STR,
    "added": _ARR,
    "removed": _ARR,
    "changed": _ARR,
    "reordered": _ANY,
    "component_version_changes": _ANY,
    "declarations": _OBJ,
    "summary_text": _STR,
    "error": _ANY,
    "restored_from_ref": _STR,
    "new_sequence": {"type": ["integer", "null"]},
    "new_commit_id": _nullable("string"),
    "sync_note": _STR,
    "info": _STR,
}

_STATUS_VIEW: dict[str, Any] = {
    "type": "object",
    "description": "The status in words: who is signed in, the quota, what this server carries.",
    "required": ["kind", "markdown"],
    "properties": {
        "kind": {"const": "status"},
        "size": _STR,
        "markdown": _STR,
        **_ELECTION,
    },
}

_STATUS_PROPS: dict[str, Any] = {
    **_BASE_PROPS,
    "view": _STATUS_VIEW,
    "authenticated": _BOOL,
    "app_url": _STR,
    "api_url": _STR,
    "profile": {"enum": ["listed", "full"]},
    "capabilities": {
        "type": "object",
        "description": "What this server carries, derived from the tools it loads.",
        "properties": {
            "research": _BOOL,
            "backtest": _BOOL,
            "monitor": {"enum": ["read-only", "read-write", "none"]},
            "live_actions": {"enum": ["web app", "here"]},
        },
    },
    "identity": _OBJ,
    "identity_error": _STR,
    "entitlements": _OBJ,
    "entitlements_error": _STR,
    "anonymous": _OBJ,
    "pending_claim": _OBJ,
    "toolsets_loaded": _ARR,
    "workflow_routes": _ARR,
}


def _envelope(props: dict[str, Any], summary: str) -> dict[str, Any]:
    """One kind's schema: its `structuredContent` home (spec 02 §2.8) plus
    the shared error set. Top-level `properties` are the declared list;
    `additionalProperties` stays open — while the probe keeps the card rows
    in `structuredContent` they ride as additional fields, and a schema that
    rejects a real envelope is worse than none."""
    return {
        "type": "object",
        "description": summary,
        "properties": {**props, **_ERROR_PROPS},
    }


_BACKTEST = _envelope(
    _BACKTEST_PROPS,
    "A backtest result: `view` renders it; an error carries `code` and `message` instead.",
)
_STRATEGY = _envelope(
    _STRATEGY_PROPS,
    "A strategy result: `view` renders it; an error carries `code` and `message` instead.",
)

#: kind → its declared schema (spec 02 §2.8).
KIND_SCHEMAS: dict[str, dict[str, Any]] = {
    "backtest": _BACKTEST,
    "comparison": _envelope(
        _COMPARISON_PROPS,
        "A comparison of runs: `view` renders it; an error carries `code` and `message` instead.",
    ),
    "strategy": _STRATEGY,
    "status": _envelope(
        _STATUS_PROPS,
        "Keel status: `view` renders it; `capabilities` is what this server carries.",
    ),
}

#: Tool name → its declared output schema. Exactly the `VIEW_TOOLS`.
OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "keel_backtest_run": _BACKTEST,
    "keel_backtest_watch": _BACKTEST,
    "keel_backtest_summarize": _BACKTEST,
    "keel_backtest_compare": KIND_SCHEMAS["comparison"],
    "keel_strategy_get": _STRATEGY,
    "keel_strategy_compose": _STRATEGY,
    "keel_strategy_fork": _STRATEGY,
    "keel_strategy_diff": _STRATEGY,
    "keel_library_get": _STRATEGY,
    "keel_library_fork": _STRATEGY,
    "keel_strategy_restore": _STRATEGY,
    "keel_account_status": KIND_SCHEMAS["status"],
}


#: Top-level properties the LISTED profile's output schemas OMIT (Q-2080,
#: 2026-10-01), per kind. Each is a field the hosted listed server never
#: emits: `deploy` (the full-profile deploy line), `sync_note` and
#: `auto_pushed_commit_id` (local-checkout write-through facts) and
#: `live_readiness_blockers` (the readiness projection's live-trading column,
#: which the listed envelope drops — `_ownership.ownership_envelope_fields`).
#: Declaring them there read as side effects the description never mentioned.
#: `exit_code` is the CLI's process status; a listed error envelope carries
#: none (`_mcp_adapter._wire_error`, Q-2268 round 3), so no listed kind
#: declares it.
LISTED_OUTPUT_OMISSIONS: dict[str, frozenset[str]] = {
    "backtest": frozenset(
        {"deploy", "sync_note", "auto_pushed_commit_id", "live_readiness_blockers", "exit_code"}
    ),
    "strategy": frozenset({"sync_note", "live_readiness_blockers", "exit_code"}),
    "comparison": frozenset({"exit_code"}),
    "status": frozenset({"exit_code"}),
}


def _listed_variant(schema: dict[str, Any], omitted: frozenset[str]) -> dict[str, Any]:
    out = deepcopy(schema)
    for key in omitted:
        out["properties"].pop(key, None)
    return out


def _kind_of(tool_name: str) -> str | None:
    for kind, schema in KIND_SCHEMAS.items():
        if OUTPUT_SCHEMAS.get(tool_name) is schema:
            return kind
    return None


def output_schema_for(tool_name: str) -> dict[str, Any] | None:
    """The declared schema for a view tool, or None (FastMCP infers).

    Under the LISTED profile the kind's `LISTED_OUTPUT_OMISSIONS` leave the
    declared properties (the envelope never carries them there).

    `keel_account_status`'s schema is published only under the non-view probe
    arm: until then it keeps the `{"result": string}` wrapper old connectors
    hold (R-25; `_mcp_adapter.status_wrapped_result`)."""
    if tool_name == "keel_account_status":
        from ._channels import nonview_text_only

        if not nonview_text_only():
            return None
    schema = OUTPUT_SCHEMAS.get(tool_name)
    if schema is None:
        return None
    from ._toolsets import is_listed_profile

    if is_listed_profile():
        omitted = LISTED_OUTPUT_OMISSIONS.get(_kind_of(tool_name) or "", frozenset())
        if omitted:
            return _listed_variant(schema, omitted)
    return schema
