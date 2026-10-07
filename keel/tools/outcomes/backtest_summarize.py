"""`keel_backtest_summarize` — summarize a completed backtest.

Per spec §4 #8 (line 282): read-only summary of a terminal-state
backtest. Returns Sharpe/DD/turnover/funding-attribution + share URL
with deep-link to the equity-curve view.

Consolidates legacy `backtest_results`, `backtest_status` (terminal
state), and parts of `backtest_list`.
"""

from __future__ import annotations

import re
from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._backtest_view import notes_block
from ._base import OutcomeResult, OutcomeTool, ToolContext, listed_schema
from ._surface_hints import tool_ref
from .open_in_app import app_url_for


# Canonical metric keys we surface from the freeform `metrics` blob.
_CANONICAL_METRIC_KEYS = (
    "sharpe",
    "sharpe_ratio",
    "total_return_pct",
    "total_return",
    "max_drawdown_pct",
    "max_drawdown",
    "win_rate_pct",
    "win_rate",
    # Trade-metrics spec 01 §3, §4.2: the positions view beside the trade
    # view. `summary_metrics` reads the stored block through the era read
    # model (`era_metrics`), so an Era B run's position count arrives HERE,
    # as `positions`, never under `total_trades`. `rebalance_legs` stays
    # readable for Era B runs (it is `resizes` under its old name).
    "positions",
    "position_win_rate",
    "position_profit_factor",
    "resizes",
    "avg_holding_duration",
    "turnover",
    "rebalance_legs",
    # Fee drag as a first-class line on every run (Q-0581 / register key
    # no-turnover-cost-feedback): % of capital + % of gross PnL, values
    # copied verbatim from the worker's sealed metrics.
    "total_fees_paid",
    "fees_pct_of_initial",
    "fees_pct_of_gross_profit",
    "fees_pct_of_net_profit",
    "annual_return_pct",
    "annual_return",
    "volatility",
    "calmar",
    "sortino",
    "trades",
    "num_trades",
    "funding_attribution",
    # The worker's own spellings for the ratio/trade keys the tearsheet
    # shows (Q-1505): the card renders these when present.
    "sortino_ratio",
    "calmar_ratio",
    "profit_factor",
    "total_trades",
)

# Points the card chart carries (Q-1505). Small enough to ride inside the
# tool envelope (~240 x 3 numbers), large enough to keep every peak and
# the deepest trough — keel-api's downsample is extreme-preserving.
CURVE_POINTS = 240


def _compact_curve(curve: Any) -> dict | None:
    """``GET /v1/backtests/{id}/curve`` → the envelope's ``curve`` block.

    Columnar triples ``[t, equity, drawdown_pct]`` instead of objects: a
    third the bytes for the same series, and the card reads positions.
    ``None`` when the run stored no curve (the card says so).
    """
    if not isinstance(curve, dict):
        return None
    points = curve.get("points")
    if not isinstance(points, list) or not points:
        return None
    triples = []
    for pt in points:
        if not isinstance(pt, dict):
            continue
        t, eq, dd = pt.get("t"), pt.get("equity"), pt.get("drawdown_pct")
        if not isinstance(t, str) or not isinstance(eq, (int, float)):
            continue
        triples.append([t, eq, dd if isinstance(dd, (int, float)) else 0.0])
    if not triples:
        return None
    return {
        "points": triples,
        "start": curve.get("start"),
        "end": curve.get("end"),
        "source_points": curve.get("source_points"),
    }


#: Moved to `_backtest_view` at Q-1716: `keel_backtest_run` and
#: `keel_backtest_watch` draw the SAME card, which reads `env.notes`,
#: and neither attached the block. One owner, three consumers.
_notes_block = notes_block


def _extract_summary_metrics(metrics: dict | None) -> dict | None:
    if not metrics:
        return None
    from ._backtest_view import era_metrics, signed_drawdown

    # The trade keys by era (spec 01 §4.2): `metrics_raw` stays verbatim
    # (the Q-0415 contract); this block names each count for what it is.
    read = era_metrics(metrics)
    summary = {k: read[k] for k in _CANONICAL_METRIC_KEYS if k in read}
    # One drawdown sign on every model-visible block (Q-1805).
    return signed_drawdown(summary) if summary else None


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _slice_params(args: dict) -> dict[str, Any] | None:
    """`start` / `end` / `capital` as the `/slice` query, or None when the
    caller asked for none of them (the whole-run summary, unchanged)."""
    params: dict[str, Any] = {}
    for key in ("start", "end"):
        value = args.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not _DAY.match(value):
            raise KeelError(
                f"`{key}` must be a date, YYYY-MM-DD (got {value!r}).",
                error_code="invalid_slice_bound",
                exit_code=2,
                suggestion="Pass start/end as YYYY-MM-DD days inside the run's window.",
            )
        params[key] = value
    capital = args.get("capital")
    if capital is not None:
        if isinstance(capital, bool) or not isinstance(capital, (int, float)) or capital <= 0:
            raise KeelError(
                f"`capital` must be a number greater than 0 (got {capital!r}).",
                error_code="invalid_slice_capital",
                exit_code=2,
                suggestion="Pass the starting value to scale the slice to, e.g. 3000.",
            )
        params["capital"] = capital
    return params or None


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    backtest_id = args.get("backtest_id")
    if not backtest_id:
        raise KeelError(
            "Missing required `backtest_id`.",
            error_code="missing_backtest_id",
            exit_code=2,
            suggestion="Pass the backtest_id returned by `keel_backtest_run`.",
        )

    slice_params = _slice_params(args)
    client = ctx.get_client()

    # The `/results` endpoint only works once status == COMPLETED; the
    # backtest-detail GET works at any state. We fetch the detail first
    # so we can surface metrics + period info without a presigned-URL
    # round trip, then attach the presigned `results.json` URL when
    # available.
    try:
        detail = client.get(f"/v1/backtests/{backtest_id}")
    except NotFoundError:
        raise NotFoundError(
            f"Backtest {backtest_id} not found.",
            suggestion=(
                "Verify the backtest_id — it is the backtest_run_id returned when "
                f"the backtest was submitted ({tool_ref('keel_backtest_run')} creates one)."
            ),
        )

    status = (detail.get("status") or "").lower()
    hero_url = app_url_for("backtest", backtest_id, ctx)
    resource_uri = f"keel://backtest/{backtest_id}/results"

    extra: dict[str, Any] = {
        "status": status,
        "strategy_id": detail.get("strategy_id"),
        "strategy_name": detail.get("strategy_name"),
        "commit_id": detail.get("commit_id"),
        "sequence_number": detail.get("sequence_number"),
        "engine": detail.get("engine"),
        "period": {
            "start_date": detail.get("start_date"),
            "end_date": detail.get("end_date"),
        },
        "queued_at": detail.get("queued_at"),
        "started_at": detail.get("started_at"),
        "completed_at": detail.get("completed_at"),
        "execution_time_s": detail.get("execution_time"),
    }
    if detail.get("error_message"):
        extra["error_message"] = detail["error_message"]

    summary_metrics = _extract_summary_metrics(detail.get("metrics"))

    # Full worker metrics dict, verbatim — the canonical list is
    # ordering/labeling only, so stored keys (fee ratios, warnings,
    # wipeout markers, ...) are never silently dropped from the envelope.
    if detail.get("metrics"):
        extra["metrics_raw"] = detail["metrics"]
        # Q-1583 step 4: what the run has to SAY beside its numbers — the
        # non-result label (warm-up consumed the window / no position opened)
        # and the per-asset notes (delisted, unavailable across a recorded
        # gap, absent from the window, data ended, our copy lags) — lifted to
        # a first-class `notes` block so an agent reads them without digging
        # through metrics_raw. Copied verbatim from the worker's durable
        # metrics; nothing is re-derived here.
        notes = _notes_block(detail["metrics"])
        if notes:
            extra["notes"] = notes

    # Best-effort presigned URL for the full results.json — only available
    # post-completion. Don't raise on failure (the summary is still useful).
    # Not on the LISTED profile (Q-2268): a one-hour signed storage URL is
    # internal plumbing no card reads and a chat host cannot use; the
    # tearsheet link is the user's way to the full result.
    from ._toolsets import is_listed_profile

    if status in {"completed", "succeeded"} and not is_listed_profile():
        try:
            results = client.get(f"/v1/backtests/{backtest_id}/results")
            if isinstance(results, dict):
                if results.get("presigned_url"):
                    extra["results_url"] = results["presigned_url"]
                    extra["results_url_expires_in_s"] = results.get("expires_in", 3600)
        except KeelError:
            # Surface no fatal — we already have the headline metrics.
            pass
    # The card chart's series (Q-1505) and the BTC hold reference (spec 03
    # §2.2) ride ONE `/curve` read: best-effort, same boundary as the
    # presigned URL — a KeelError degrades to "no curve" (the card shows a
    # note), programming errors propagate.
    from ._backtest_view import (
        attach_run_config,
        attach_run_facts,
        build_backtest_view,
        fetch_curve_and_reference,
    )

    reference = None
    if status in {"completed", "succeeded"}:
        compact, reference = fetch_curve_and_reference(client, backtest_id, points=CURVE_POINTS)
        if compact:
            extra["curve"] = compact
    if slice_params is not None:
        # Part of the run (spec 07 §6): keel-api's `/slice` is the one owner of
        # the numbers. Asked for explicitly, so a refusal (a bound outside the
        # run names its window; a run still running) is the result, not a
        # silently dropped clause.
        answer = client.get(f"/v1/backtests/{backtest_id}/slice", **slice_params)
        if isinstance(answer, dict) and isinstance(answer.get("slice"), dict):
            extra["slice"] = answer["slice"]

    # The run's served facts: the window object, and on a completed run the
    # reference, realism, sample-size and good-result data (spec 02 §2.4).
    attach_run_facts(extra, detail, client=client, reference=reference)

    # The full-profile `deploy:` line (spec 02 §2.4 — the nudge is retired;
    # the listed profile carries the `good_result:` fact line only).
    from ._nudge import deploy_line

    deploy = deploy_line(detail, strategy_id=detail.get("strategy_id"), ctx=ctx)
    if deploy:
        extra["deploy"] = deploy

    # Card + per-surface render hints (spec 06 R2/R3) — render-only,
    # derived from fields already in this envelope.
    from ._render import card_render_block

    extra["render"] = card_render_block(
        "backtest", fallback_url=hero_url, ctx=ctx, note=extra.get("deploy")
    )

    # `summarize` is the rendering for the run being DISCUSSED, so its
    # view is the evidence block by default (BUILD §2.1) — the one
    # backtest surface that does not take `present`, because a summarize
    # that renders a receipt is a call with no reason to exist. It carries
    # no `next`: the sample-size fact is its own data line now.
    view = build_backtest_view(
        {**detail, "id": backtest_id}, size="evidence", url=hero_url, reference=reference
    )
    if view is not None:
        extra["view"] = view
        attach_run_config(extra, view, client, detail)

    return OutcomeResult(
        run_id=backtest_id,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=summary_metrics,
        resource_uri=resource_uri,
        extra=extra,
    )


#: spec 07 §5 (D13), the approved text for the new parameters. The listed
#: profile's copy differs by ONE word: "trading" is a listed-banned token
#: (tests/test_policy_scan.py FORBIDDEN_TEXT_RE), so it reads "running".
SLICE_PARAM_TEXT = (
    "Optional: report only part of this run (YYYY-MM-DD bounds), scaled to a starting "
    "`capital`, e.g. its last week on $3,000. This is a slice of the run as it was "
    'already trading. For "if I had started on X", run a backtest from X instead.'
)
LISTED_SLICE_PARAM_TEXT = SLICE_PARAM_TEXT.replace("already trading", "already running")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["backtest_id"],
    "properties": {
        "backtest_id": {
            "type": "string",
            "description": "The run's id — the `run_id` that `keel_backtest_run` returns.",
            "x-cli-positional": True,
        },
        "start": {"type": "string", "format": "date", "description": SLICE_PARAM_TEXT},
        "end": {
            "type": "string",
            "format": "date",
            "description": (
                "Last day of the slice, YYYY-MM-DD, inclusive. Optional; defaults to the "
                "run's last day."
            ),
        },
        "capital": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": (
                "Starting value the slice is scaled to, e.g. 3000. Optional; defaults to "
                "the run's own starting capital."
            ),
        },
    },
}
LISTED_INPUT_SCHEMA: dict[str, Any] = listed_schema(
    "keel_backtest_summarize", INPUT_SCHEMA, descriptions={"start": LISTED_SLICE_PARAM_TEXT}
)

BACKTEST_SUMMARIZE = register(
    OutcomeTool(
        name="keel_backtest_summarize",
        required_action="backtest.read",
        cli_path=("backtest", "summarize"),
        toolset="backtest",
        # grounded-in: system/chat/trading_domain.md:1-7 (post-backtest
        # reasoning — diagnose the mechanism not the outcome; changes need a
        # principled reason independent of the backtest; prefer robustness
        # over removing exposure); system/chat/tool_usage.md:17;
        # system/reasoning_principles.md.
        description=(
            "Show ONE completed backtest run in full — several runs together are "
            "`keel_backtest_compare`, a still-running one `keel_backtest_watch`. The card "
            "carries return, max drawdown, Sharpe, win rate, turnover, carry, "
            "the window, the equity curve and the tearsheet link. Complete only at a "
            "terminal status. `exposure` "
            "says how much of the window held positions; `start` / `end` / `capital` "
            "report part of the run as `slice`."
        ),
        input_schema=INPUT_SCHEMA,
        listed_input_schema=LISTED_INPUT_SCHEMA,
        annotations={
            "title": "Summarize Backtest Results",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        # No listed override (agent-surface-cleanup spec 01 §2.5, R-4): the
        # base text is the same on every profile, so it is written in the
        # listed word rules' terms — "carry", and no count word: the count's
        # label is "Trades" (Q-1906) and "trades" is a listed-banned token.
    )
)
