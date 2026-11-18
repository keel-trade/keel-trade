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


__all__ = [
    "LISTED_OUTPUT_OMISSIONS",
    "OUTPUT_SCHEMAS",
    "RESEARCH_INPUT_SCHEMAS",
    "RESEARCH_OUTPUT_SCHEMAS",
    "RESEARCH_RECEIPT_TOOLS",
    "STUDY_SCHEMA",
    "output_schema_for",
]


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
        # Which way the positions went (R-003): the app's one line.
        "sides_line": _STR,
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

#: The run's recorded order realism (spec 03 §2.6): the share of orders under
#: the live minimum at the run's capital and, on runs that carry it (Q-2542),
#: the capital at which at most 25 % / 5 % of its orders would sit under it —
#: the worker's own numbers from the run's order notionals, never recomputed.
_REALISM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "n_orders": _INT,
        "n_below": _INT,
        "share": {"type": ["number", "null"]},
        "avg_order_notional": {"type": ["number", "null"]},
        "min_notional": {"type": ["number", "null"]},
        "truncated": _BOOL,
        "min_capital_for_share": {
            "type": "object",
            "description": (
                "Capital (USD) at which at most 25% / 5% of this run's orders would sit "
                "under the live minimum, from the run's own order sizes; absent on runs "
                "that recorded none."
            ),
            "properties": {"25_pct": {"type": "number"}, "5_pct": {"type": "number"}},
        },
    },
}

#: The money the strategy needs to trade as backtested (design doc 14 §11):
#: the run's capital guide, copied — never recomputed — plus its sentence.
_CAPITAL_GUIDE: dict[str, Any] = {
    "type": "object",
    "description": (
        "What this strategy needs (USD) to place its orders and track its backtest: "
        "suggested_usd follows the backtest, closest_usd tracks it most closely, "
        "minimum_usd is the least it runs from. basis 'backtest' sizes it from the "
        "positions held at once; 'assets' from the asset count. `line` says it plainly. "
        "Not on the listed profile."
    ),
    "properties": {
        "basis": _STR,
        "suggested_usd": _INT,
        "closest_usd": _INT,
        "minimum_usd": _INT,
        "n_held": {"type": ["integer", "null"]},
        "typical_weight": {"type": ["number", "null"]},
        "universe_size": {"type": ["integer", "null"]},
        "line": _STR,
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
        "(sum of |position value| / equity) over the held bars, on runs that carry it. "
        "`bars_net_long` / `bars_net_short`: held bars whose book was net long / net "
        "short, on runs that carry them."
    ),
    "properties": {
        "bars_held": _INT,
        "n_bars": _INT,
        "first_position_at": _nullable("string"),
        "last_position_at": _nullable("string"),
        "gross_mean": {"type": "number"},
        "gross_max": {"type": "number"},
        "bars_net_long": _INT,
        "bars_net_short": _INT,
        "line": _STR,
    },
}

_SIDES: dict[str, Any] = {
    "type": "object",
    "description": (
        "Positions by side: long and short counts (they sum to the position count; one "
        "still open at the end counts on its side), flips (positions that ended by "
        "switching direction on the same bar; each adds one to both sides), and each "
        "side's P&L after fees and before carry, in account currency, with open positions "
        "marked at the last price. Absent on runs recorded before these were stored."
    ),
    "properties": {
        "positions_long": _INT,
        "positions_short": _INT,
        "flips": _INT,
        "pnl_long": {"type": "number"},
        "pnl_short": {"type": "number"},
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

#: One period of a run, as keel-api's `/periods` serves it (Q-2565): the
#: slice owner over a PARTITION of the run's bars by UTC calendar month or
#: quarter — every bar in the period holding the day it covers (Q-2577) — so
#: each period opens at the previous one's close, the bars sum to the run's
#: and the rows compound to the run. `start`/`end` are the covered days of
#: the period's first and last bar, not the calendar edge.
_PERIOD_ROW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": _STR,
        "start": _STR,
        "end": _STR,
        "bars": _INT,
        "return_pct": {"type": ["number", "null"]},
        "max_drawdown_pct": {"type": ["number", "null"], "maximum": 0},
        "equity": {"type": ["object", "null"]},
        "positions": {"type": ["object", "null"]},
        "trades_closed": _INT,
        "partial": _BOOL,
        "warmup": _BOOL,
    },
}

#: `periods` on a single run: the rows, the whole run, and what a period is.
_PERIODS: dict[str, Any] = {
    "type": "object",
    "description": (
        "Period table (`periods` asked): one row per calendar month or quarter of this run "
        "— return, max drawdown (≤ 0, within the period), positions opened and bars — plus "
        "`run`, the whole run. Every bar of the run is in exactly one period (the one holding "
        "the day it covers), so each period opens at the previous one's close, the bars sum "
        "to the run's and the rows compound to the run. `warmup` marks a period before the "
        "first equity move."
    ),
    "properties": {
        "grain": {"type": "string", "enum": ["monthly", "quarterly"]},
        "rows": {"type": "array", "items": _PERIOD_ROW},
        "run": _PERIOD_ROW,
        "basis": _STR,
    },
}

#: `periods` on a comparison: the runs' tables aligned by period label, one
#: value per run (in `labels` order, null where a run has no such period).
_COMPARISON_PERIODS: dict[str, Any] = {
    "type": "object",
    "description": (
        "Period table across the runs (`periods` asked): per calendar month or quarter, "
        "each run's return and max drawdown (≤ 0, within the period) in `labels` order, "
        "null where a run has no such period, plus `run`, the whole run."
    ),
    "properties": {
        "grain": {"type": ["string", "null"], "enum": ["monthly", "quarterly", None]},
        "labels": {"type": "array", "items": _STR},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "period": _STR,
                    "return_pct": _ARR,
                    "max_drawdown_pct": _ARR,
                    "bars": _ARR,
                    "warmup": _ARR,
                    "partial": _ARR,
                },
            },
        },
        "run": _OBJ,
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
    "floor_notice": _STR,
    "remaining": _OBJ,
    "good_result": _OBJ,
    "realism": _REALISM,
    "capital_guide": _CAPITAL_GUIDE,
    "reference": _REFERENCE,
    "few_fills_note": _STR,
    "exposure": _EXPOSURE,
    "sides": _SIDES,
    "cost_model": _COST_MODEL,
    "slice": _SLICE,
    "periods": _PERIODS,
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
    "periods": _COMPARISON_PERIODS,
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
    "restored_from_sequence": {"type": ["integer", "null"]},
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
    rejects a real envelope is worse than none.

    RI-588: a field both sets declare keeps the KIND's declaration (in the
    shared set's position, so no other kind's schema moves). A study's
    `quota_notice` is null on every submit without a notice, and the error
    set's plain string silently replaced its `string | null`, so a strict
    client refused every keel_research_run."""
    properties = {**props, **_ERROR_PROPS}
    properties.update({k: v for k, v in props.items() if k in _ERROR_PROPS})
    return {
        "type": "object",
        "description": summary,
        "properties": properties,
    }


_BACKTEST = _envelope(
    _BACKTEST_PROPS,
    "A backtest result: `view` renders it; an error carries `code` and `message` instead.",
)
_STRATEGY = _envelope(
    _STRATEGY_PROPS,
    "A strategy result: `view` renders it; an error carries `code` and `message` instead.",
)

_SCREENER = _envelope(
    {
        **_BASE_PROPS,
        "view": {
            "type": "object",
            "required": ["kind", "size", "markdown"],
            "properties": {
                "kind": {"const": "screener"},
                "size": {"enum": ["receipt", "view"]},
                "markdown": {"type": "string"},
            },
        },
        **{
            key: {"type": ["object", "null"]}
            for key in (
                "screener",
                "run",
                "in_flight",
                "headline",
                "manifest",
                "latest_top",
                "passing",
                "copy",
            )
        },
        "labels": {"type": ["array", "null"], "items": {"type": "object"}},
        "actions": {"type": ["array", "null"], "items": {"type": "object"}},
        # keel_screener_view(include_view_source=true): the saved view source,
        # its revision and recent revisions (L13 §2a.3).
        "view_source": {"type": ["object", "null"]},
        # keel_screener_run: the backtest runs left after this one (Q-3634).
        "remaining": _OBJ,
        # The ranked output's range over every coin with a value (Q-3639).
        "range": {"type": ["object", "null"]},
        # keel_screener_view_set: the revision saved (the next
        # `expected_revision`), the view check and the output units.
        "revision": {"type": ["integer", "null"]},
        "check": {"type": ["object", "null"]},
        "units": {"type": ["array", "null"], "items": {"type": "object"}},
    },
    "A screen result selected by the API; view.markdown is also returned as text.",
)

#: kind → its declared schema (spec 02 §2.8).
KIND_SCHEMAS: dict[str, dict[str, Any]] = {
    "screener": _SCREENER,
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
    "keel_screener_run": _SCREENER,
    "keel_screener_view": _SCREENER,
    "keel_screener_view_set": _SCREENER,
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


# ---------------------------------------------------------------------------
# Research (spec 07): the day-0 contract rows (BUILD-PLAN §3.10, §4.4)
# ---------------------------------------------------------------------------
#
# The research tools' input and output schemas, checked in before the tools
# themselves so the app, the fixtures and chat build against them. The tool
# modules (`research_*.py`) take their PROPERTIES from here; when they register,
# their names join `VIEW_TOOLS`, `OUTPUT_SCHEMAS` and `KIND_SCHEMAS["study"]`
# beside `_channels.CHANNEL_MAP["study"]` (07 §3.3) in one change. keel-api's
# `services/keel-api/src/schemas/research.py` is the route side of the same
# contract; `tests/test_research_schemas.py` holds the two in step.

_NULL_STR = _nullable("string")

#: `view` with `kind: "study"` (07 §3.1); `markdown` is the text block.
_STUDY_VIEW: dict[str, Any] = {
    "type": "object",
    # `markdown` is the text block (`content`); a study read drops it from
    # structuredContent (07 §3.3: a duplicate, and AT-5's 8,000-byte budget).
    "description": "One study, plan or brief; the text block carries it in words.",
    "required": ["kind", "size"],
    "properties": {
        "kind": {"const": "study"},
        # 13 §2.1 (WL-AG-10): the layout the card draws, set by the SDK for every tool and read.
        "mode": {
            "enum": ["plan", "run", "study", "list", "chart", "addresses", "project", "library"]
        },
        # `progress`: the run card's one fixed height (D-328 D5).
        "size": {"enum": ["receipt", "view", "progress"]},
        "markdown": _STR,
        **_ELECTION,
        "version": {"type": "integer", "minimum": 1},
    },
}

#: The structured home of every research envelope (07 §3.1, §3.3's channel
#: table, §4.1's plan, §3.5's data page, §4.7's brief). `headline` and
#: `view.markdown` are not here: `card_summary` is the headline's one home
#: and the text carries the rest (07 §3.3). `steps` is a list on a plan and
#: `{total, by_status}` on a study read (RL-58).
_STUDY_PROPS: dict[str, Any] = {
    **_BASE_PROPS,
    "view": _STUDY_VIEW,
    # 07 §3.1
    "study": {"type": "object"},
    "footing": {"type": ["object", "null"]},
    "counts": {"type": ["object", "null"]},
    "stale": _ARR,
    "links": _OBJ,
    "draft": {"type": ["object", "null"]},
    # RI-483: a study read's `base` (`{version, revision, evidence_hash}` of the read): what an edit of its source
    # sends with keel_research_plan's edit form; null on a share read.
    "base": {"type": ["object", "null"]},
    "progress": {"type": ["object", "null"]},
    "not_measured": _ARR,
    "card_summary": {"type": ["object", "null"]},
    # 07 §3.3: bounded by construction (RL-58)
    "checks": {
        "type": ["object", "null"],
        "properties": {
            "count": _INT,
            "met": _INT,
            "rows": {"type": "array", "maxItems": 5},
        },
    },
    "steps": {"type": ["array", "object"]},
    "views": {"type": ["array", "object"]},
    "notes": {"type": ["array", "object"]},
    "notes_total": _INT,
    "saved": _NULL_STR,
    # 06 §4.2, §4.9, §9: the study's Page, the viewer's run rights, the stored
    # form (owner only) and the pinned subjects
    "page": {"type": ["object", "null"]},
    "can_run": _BOOL,
    "can_retry": _BOOL,
    # RI-588: a list on a plan; `{total}` on a study read, where the card
    # reducer counts them (`_research_view`, 07 §3.3: the text names them).
    "subjects": {"type": ["array", "object"], "properties": {"total": _INT}},
    # 07 §4.3: the submit's quota (the `research_studies` unit only)
    "quota": _ARR,
    "quota_notice": _NULL_STR,
    # 07 §4.1 / 04 §1.1: the plan
    "plan_id": _STR,
    "expires_at": _STR,
    "class": {"enum": ["new_study", "new_version", "linked_study", "resume", "retry"]},
    "writes": _STR,
    "linked_reason": _NULL_STR,
    "source": _NULL_STR,
    "filled": _ARR,
    "issues": _ARR,
    "runs": _OBJ,
    "addresses_carried": _ARR,
    "addresses_changed": _ARR,
    "addresses_moved": _ARR,
    "estimate": _OBJ,
    "allowance": _OBJ,
    "run_allowed": _BOOL,
    "limits": _ARR,
    "locked": _ARR,
    "versions_tried": {"type": ["object", "array"]},
    "preview": _OBJ,
    "words": _OBJ,
    # 07 §3.5 / §4.5: one chart's rows, and a subject's addresses (§4.12)
    "pagination": _OBJ,
    # WL-SV-08 (13 §2.1, D8): a receipt's served line and a project's page; a plan's edit class (WL-SV-24).
    "card_line": {"type": ["string", "null"]},
    "link": {"type": ["string", "null"]},
    "edit": {"type": ["object", "null"]},
    "preview_delta": {"type": ["object", "array", "null"]},
    # RI-588: a data page's facts; null on the brief (keel-api BriefResponse.facts: Json | None)
    "facts": {"type": ["object", "null"]},
    "subject": _STR,
    "rows_total": _INT,
    "by_reason": _OBJ,
    # 07 §4.4 (no id) and §4.7: the study list and the brief
    "studies": _ARR,
    "studies_total": _INT,
    "by_state": _OBJ,
    "projects": _ARR,
    "projects_total": _INT,
    "families_total": _INT,
    "set_aside": _ARR,
    "set_aside_total": _INT,
    "planned": _ARR,
    "planned_total": _INT,
    "paper_and_live": _ARR,
    "paper_and_live_total": _INT,
    # RI-592: the plan's paged children (07 §3.5), the add-plan's rows (04 §1.7,
    # RI-385), a map data page's filling grid (D-158), the share read's Keel fact
    "children": {"type": ["object", "null"]},
    "added": {"type": ["array", "null"]},
    "refused": {"type": ["array", "null"]},
    "filling": {"type": ["object", "null"]},
    "by_keel": _BOOL,
    # RI-592: a project read (D-189's keel_research_get project_id, D-177's shape)
    "project_id": _STR,
    "name": _STR,
    "question": _NULL_STR,
    "archived": _BOOL,
    # RI-593: `{total}` on MCP (the text names the items, as for subjects)
    "items": {"type": ["array", "object"], "properties": {"total": _INT}},
    "items_total": _INT,
}

STUDY_SCHEMA = _envelope(
    _STUDY_PROPS,
    "A research result selected by the API; view.markdown is also returned as text.",
)

#: The research view tools → their declared output schema (07 §2.2: plan,
#: run, get, view_set and brief carry a view; note, cancel and make_signal
#: answer in text).
RESEARCH_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "keel_research_plan": STUDY_SCHEMA,
    "keel_research_run": STUDY_SCHEMA,
    "keel_research_get": STUDY_SCHEMA,
    "keel_research_view_set": STUDY_SCHEMA,
    "keel_research_brief": STUDY_SCHEMA,
}

#: 07 §2.2's research tools whose mode is "text (receipt line)": note, cancel and make_signal. Their text block is
#: the receipt the handler wrote and the served object rides as `structuredContent`, so a caller reads `ref`, `next`
#: and `progress` as fields (RI-220; `_mcp_adapter.receipt_result`). They declare no output schema (07 §3.3 declares
#: the five view tools'), and none is listed (§2.2), so no connector holds the `{"result": string}` wrapper for them.
RESEARCH_RECEIPT_TOOLS = frozenset(
    {"keel_research_note", "keel_research_cancel", "keel_research_make_signal"}
)

# The research view tools join the registry's kinds (07 §3.3): one schema
# for the study kind, declared by each of the five view tools.
KIND_SCHEMAS["study"] = STUDY_SCHEMA
OUTPUT_SCHEMAS.update(RESEARCH_OUTPUT_SCHEMAS)

_STUDY_ID = {"type": "string", "pattern": "^stu_", "x-cli-positional": True}
_SUBJECT_CALLS = {"type": "array", "items": {"type": "string", "minLength": 1}}
_DATE = {"type": "string", "format": "date"}

#: 07 §4.1's eight plan forms, one flat object (the handler refuses a mixture).
_PLAN_PROPS: dict[str, Any] = {
    "template": {"enum": ["check", "signal", "compare", "map", "entries"]},
    "subjects": {**_SUBJECT_CALLS, "minItems": 1, "maxItems": 50},
    "days": {
        "type": "object",
        "properties": {"start": _DATE, "end": _DATE},
        "additionalProperties": False,
    },
    "split": _DATE,
    "coins": {"type": "string", "maxLength": 500},
    # RI-138/RI-164 (06 §3.10, RL-74): the template form's Costs and run settings; left out, the template's own
    # (4.5 bp a trade each side, no delay, 1x, no cap). The validator judges the ranges.
    "costs": {
        "oneOf": [
            {
                "type": "object",
                "properties": {"fee": {"type": "number"}, "slippage": {"type": "number"}},
                "required": ["fee", "slippage"],
                "additionalProperties": False,
            },
            {"enum": ["none"]},
        ]
    },
    # D-344 (1), 07 §8.4: the listed word is "exposure multiple", never "leverage"; the API field is still
    # `leverage` (research_plan.plan_body maps it). No alias: no released connector or CLI ever took `leverage`
    # (Research was never listed in prod), and PARAM_ALIASES' wire arms are for released renames.
    "delay": {"type": "integer", "minimum": 0, "maximum": 3},
    "exposure_multiple": {"type": "number", "exclusiveMinimum": 0},
    "vol_cap": {"type": "number", "exclusiveMinimum": 0},
    "axes": {**_SUBJECT_CALLS, "minItems": 1, "maxItems": 3},
    "source": {"type": "string", "minLength": 1, "maxLength": 131072},
    "study_id": _STUDY_ID,
    # RI-482/RI-483 (07 §4.1's edit form, D-301): the read an edit's `source` was made on, copied from that read's
    # `base` (keel_research_get serves it). A base that is not the head, or no base while a Draft is ahead of the
    # version that ran, is refused RESEARCH_REVISION_CONFLICT; nothing is planned. keel-api's PlanBase is the shape.
    "base": {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "minimum": 1},
            "revision": {"type": "integer", "minimum": 1},
            "evidence_hash": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "required": ["version", "revision", "evidence_hash"],
        "additionalProperties": False,
    },
    "add": {**_SUBJECT_CALLS, "minItems": 1, "maxItems": 10},
    "to": _STR,
    "share_id": {"type": "string", "minLength": 23, "maxLength": 23},
    "from_plan": {"type": "string", "pattern": "^pln_"},
    "version": {"type": "integer", "minimum": 1},
    "resume": _BOOL,
    "retry": _BOOL,
    # RI-451/RI-463 (07 §4.1's cell form, D-290): one map cell's full report, `study_id` + `cell`. `grid` the map's
    # step id, `at` the cell's setting values (axis name to value), `subject` when the map has several. keel-api's
    # CellForm is the closed shape; the planner judges the cell.
    "cell": {
        "type": "object",
        "properties": {
            "grid": {"type": "string", "minLength": 1, "maxLength": 64},
            "at": {
                "type": "object",
                "maxProperties": 50,
                "additionalProperties": {"type": ["boolean", "integer", "number", "string"]},
            },
            "subject": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "required": ["grid", "at"],
        "additionalProperties": False,
    },
}

_PRESENT = {"type": "string", "enum": ["receipt", "view"]}

#: A3 #7 / WL-AG-16 (07 §8.5, R-25): one line per listed research parameter. The listed ceilings count tool
#: descriptions only (test_description_shape, test_listed_screens), so these cost no listed budget; measured
#: 2026-11-17. `_run`'s plan fields stay listed (removing one breaks a frozen connector, R-25) and say so.
_PLAN_WORDS: dict[str, str] = {
    "template": "The template form: which study to plan. Send it with subjects.",
    "subjects": (
        'Each a study/1 subject call, e.g. `Strategy("str_…@3")`, `Output("str_…@1", output="…")` or '
        'a library ref such as `Output("keel/momentum_7d@1", output="momentum_7d")`; always pinned with @version. '
        "One for check, map and entries; two to eight for compare."
    ),
    "days": 'The days to measure, `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`; left out, the template\'s.',
    "split": "A date that divides the days into before and after (YYYY-MM-DD).",
    "coins": "A coin rule, e.g. `TopByVolume(30, days=30)`; left out, the subject's own coins.",
    "costs": 'Fees and slippage a fill as fractions, `{"fee": 0.00045, "slippage": 0.00045}`, or "none".',
    "delay": "Act this many bars late (0 to 3).",
    "exposure_multiple": "The exposure multiple the backtest runs at (1 = as the strategy sizes it).",
    "vol_cap": "A volatility cap on the book, as a fraction a year.",
    "axes": (
        "map only, 1 to 3 `SettingAxis(address, [values], name=…)` calls; copy each address from "
        "keel_research_get(subject=…)."
    ),
    "source": "A whole study/1 document (the document form, or the edit form with study_id).",
    "study_id": "The study an edit, add, cell or again form changes (stu_…).",
    "base": "The read an edit's source came from, copied from that read's `base` (keel_research_get serves it).",
    "add": 'The add form: subjects or references to add, e.g. `Output("str_…@1", output="…")`.',
    "to": "With add: the step id whose against= list the reference joins.",
    "share_id": "The from-share form: plan a shared study on your own subjects (send your subjects too).",
    "from_plan": "Plan again from an expired or stale plan (pln_…).",
    "version": (
        "With resume or retry: the study version (default its newest). Alone with study_id: plan that "
        "never-run Draft as it is saved."
    ),
    "resume": "With study_id: resume a stopped run of the study.",
    "retry": "With study_id: retry only the runs that failed on Keel's side (free).",
    "cell": 'One map cell\'s full report, `{"grid": step id, "at": {axis: value}}`, with study_id.',
}
_RUN_WORDS: dict[str, str] = {
    "plan_id": "The plan to run, from keel_research_plan (pln_…). Send it alone.",
    "max_runs": "A direct run's ceiling: the most runs it may start. Required without plan_id.",
    "wait": "Wait briefly for the study before answering (default true).",
    **{k: f"Direct run: {v[0].lower()}{v[1:]}" for k, v in _PLAN_WORDS.items()},
    # D-341 (3): the draft form is the plan's only (a Draft's run is planned first, then run by its plan_id).
    "version": "Direct run: with resume or retry, the study version (default its newest).",
}
_GET_WORDS: dict[str, str] = {
    "study_id": "The study to read (stu_…). With no id at all, get lists the user's studies.",
    "share_id": "A shared study's id (23 characters), to read the snapshot.",
    "version": "A study version to read (default the newest that ran).",
    "revision": "A presentation revision of that version.",
    "view": "A chart's index or exact title: read that chart's rows (25 a page).",
    "output": 'A step output to read rows of, e.g. "bars" or "costs.summary".',
    "measures": "With view or output: only these measures (1 to 12).",
    "where": 'With view or output: fix 1 to 4 axes, e.g. `{"lookback": 20}`.',
    "sort": 'With view or output: `{"measure": …, "order": "asc" | "desc"}`.',
    "subject": 'Read a subject\'s settable addresses, e.g. `Strategy("str_…@3")`; send it alone or with include.',
    "include": 'With subject: "all" also lists addresses that cannot be set; with study_id: "source" adds its document.',
    "project_id": "Read a project and its items (prj_…).",
    "cursor": "The cursor a previous page named, to read the next page.",
    "limit": "Rows a page (1 to 25).",
}
_PLAN_PAGING_WORDS: dict[str, str] = {
    "cursor": "Pages the plan's list of the runs it would start (the cursor a previous plan named).",
    "limit": "Children a page (1 to 25).",
}


def _described(props: dict[str, Any], words: dict[str, str]) -> dict[str, Any]:
    """Each property with its one-line description, never mutating a shared fragment (``_STUDY_ID`` is shared)."""
    missing = sorted(set(words) - set(props))
    if missing:
        raise KeyError(f"words for no property: {missing}")
    return {k: ({**v, "description": words[k]} if k in words else v) for k, v in props.items()}


#: The research MCP tools' input properties and required names (07 §4).
#: Each tool module takes its PROPERTIES and REQUIRED from this table; the
#: descriptions (listed copy, 07 §8.5) are the modules'.
RESEARCH_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "keel_research_plan": {
        "type": "object",
        "properties": {
            **_PLAN_PROPS,
            "cursor": _STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "required": [],
    },
    "keel_research_run": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "pattern": "^pln_", "x-cli-positional": True},
            **_PLAN_PROPS,
            "max_runs": {"type": "integer", "minimum": 0},
            "wait": {"type": "boolean", "default": True},
            "present": _PRESENT,
        },
        "required": [],
    },
    "keel_research_get": {
        "type": "object",
        "properties": {
            "study_id": _STUDY_ID,
            "share_id": {"type": "string", "minLength": 23, "maxLength": 23},
            "version": {"type": "integer", "minimum": 1},
            "revision": {"type": "integer", "minimum": 0},
            "view": {"type": ["integer", "string"]},
            "output": _STR,
            "measures": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 12},
            "where": {"type": "object", "minProperties": 1, "maxProperties": 4},
            "sort": {
                "type": "object",
                "properties": {"measure": _STR, "order": {"enum": ["asc", "desc"]}},
                "required": ["measure", "order"],
                "additionalProperties": False,
            },
            "subject": _STR,
            "include": {"enum": ["settable", "all", "source"]},
            # D-189 (07 §5: a read is read-scoped): a project's items by kind and name (D-177).
            "project_id": {"type": "string", "pattern": "^prj_"},
            # R-24 (WL-AG-08): Keel's library, so a hosted agent finds the refs a study names.
            "library": {
                "enum": ["signals", "benchmarks", "strategies", "market_states"],
                "description": (
                    "Lists Keel's library with each item's exact subject call, e.g. "
                    '`Output("keel/momentum_7d@1", output="momentum_7d")`; send it with nothing else.'
                ),
            },
            "cursor": _STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            "present": _PRESENT,
        },
        "required": [],
    },
    "keel_research_view_set": {
        "type": "object",
        "properties": {
            "study_id": _STUDY_ID,
            "source": {"type": "string", "minLength": 1, "maxLength": 131072},
            "expected_revision": {"type": "integer", "minimum": 0},
            "revert_to": {"type": "integer", "minimum": 0},
            # D-191 (RI-169): the title or question alone, written by the server; keel-api's StudyTextSet.
            "set": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "question": {"type": ["string", "null"], "maxLength": 500},
                },
                "minProperties": 1,
                "additionalProperties": False,
            },
            "dry_run": {"type": "boolean", "default": False},
            "label": {"type": "string", "maxLength": 80},
            "present": _PRESENT,
        },
        "required": ["study_id", "expected_revision"],
    },
    "keel_research_brief": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "pattern": "^prj_"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": _STR,
        },
        "required": [],
    },
    "keel_research_note": {
        "type": "object",
        "properties": {
            "anchor": {"type": "string", "pattern": "^(stu|prj)_", "x-cli-positional": True},
            "kind": {
                "enum": [
                    "hypothesis",
                    "note",
                    "decision",
                    "set_aside",
                    "restored",
                    "agent_session",
                ]
            },
            "body": {"type": "string", "minLength": 1, "maxLength": 8192},
        },
        "required": ["anchor", "kind", "body"],
    },
    "keel_research_cancel": {
        "type": "object",
        "properties": {
            "run_set_id": {"type": "string", "pattern": "^rus_", "x-cli-positional": True},
            "study_id": {"type": "string", "pattern": "^stu_"},
            "version": {"type": "integer", "minimum": 1},
        },
        "required": [],
    },
    "keel_research_make_signal": {
        "type": "object",
        "properties": {
            "study_id": _STUDY_ID,
            "version": {"type": "integer", "minimum": 1},
            "subject": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        "required": ["study_id", "version", "subject", "name"],
    },
}


#: WL-AG-15: the other research tools' parameters, each a line on MCP (full profile) and in `keel research … --help`.
_OTHER_WORDS: dict[str, dict[str, str]] = {
    "keel_research_view_set": {
        "study_id": "The study whose presentation changes (stu_…).",
        "source": "The whole study/1 document with the new title, bars or charts (only those may change).",
        "expected_revision": "The revision your read showed; a newer one is refused, nothing is overwritten.",
        "revert_to": "A revision to go back to, instead of source.",
        "set": 'Rename the study or change its question alone, `{"title": …}` or `{"question": …}` (null removes it).',
        "dry_run": "Check the change without saving it.",
        "label": "A short name for this revision.",
    },
    "keel_research_brief": {
        "project_id": "Only this project's studies and notes (prj_…).",
        "limit": "Studies a page (1 to 50, default 25).",
        "cursor": "The cursor the previous brief named.",
    },
    "keel_research_note": {
        "anchor": "The study or project the note is on (stu_… or prj_…).",
        "kind": "hypothesis, decision, note or agent_session; set_aside and restored set a study aside or bring it back.",
        "body": "The note, in your words (up to 8,192 characters).",
    },
    "keel_research_cancel": {
        "run_set_id": "The run to stop (rus_…), from the study's read.",
        "study_id": "Or the study (stu_…), with version.",
        "version": "With study_id: the version whose run stops.",
    },
    "keel_research_make_signal": {
        "study_id": "The study whose mix becomes a signal (stu_…).",
        "version": "The study version the mix is read from.",
        "subject": "The mix subject's key, as the study names it.",
        "name": "The new signal's name.",
    },
}

for _name, _words in (
    ("keel_research_plan", {**_PLAN_WORDS, **_PLAN_PAGING_WORDS}),
    ("keel_research_run", _RUN_WORDS),
    ("keel_research_get", _GET_WORDS),
    *_OTHER_WORDS.items(),
):
    RESEARCH_INPUT_SCHEMAS[_name] = {
        **RESEARCH_INPUT_SCHEMAS[_name],
        "properties": _described(RESEARCH_INPUT_SCHEMAS[_name]["properties"], _words),
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
        {
            "deploy",
            "sync_note",
            "auto_pushed_commit_id",
            "live_readiness_blockers",
            "exit_code",
            # The funding amounts (design doc 14 §11) are for the full
            # profile: the listed connector is for research and backtests.
            "capital_guide",
        }
    ),
    "strategy": frozenset({"sync_note", "live_readiness_blockers", "exit_code"}),
    "comparison": frozenset({"exit_code"}),
    "status": frozenset({"exit_code"}),
    "study": frozenset({"exit_code"}),
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
