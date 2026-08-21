"""`keel_backtest_summarize` — summarize a completed backtest.

Per spec §4 #8 (line 282): read-only summary of a terminal-state
backtest. Returns Sharpe/DD/turnover/funding-attribution + share URL
with deep-link to the equity-curve view.

Consolidates legacy `backtest_results`, `backtest_status` (terminal
state), and parts of `backtest_list`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


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
    "turnover",
    "annual_return_pct",
    "annual_return",
    "volatility",
    "calmar",
    "sortino",
    "trades",
    "num_trades",
    "funding_attribution",
)


def _extract_summary_metrics(metrics: dict | None) -> dict | None:
    if not metrics:
        return None
    summary = {k: metrics[k] for k in _CANONICAL_METRIC_KEYS if k in metrics}
    return summary or None


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    backtest_id = args.get("backtest_id")
    if not backtest_id:
        raise KeelError(
            "Missing required `backtest_id`.",
            error_code="missing_backtest_id",
            exit_code=2,
            suggestion="Pass the backtest_id returned by `keel_backtest_run`.",
        )

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
            suggestion="Verify the backtest_id (run `keel backtest run` to create one).",
        )

    status = (detail.get("status") or "").lower()
    hero_url = f"{ctx.app_url}/backtests/{backtest_id}?tab=tearsheet"
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

    # Best-effort presigned URL for the full results.json — only available
    # post-completion. Don't raise on failure (the summary is still useful).
    if status in {"completed", "succeeded"}:
        try:
            results = client.get(f"/v1/backtests/{backtest_id}/results")
            if isinstance(results, dict):
                if results.get("presigned_url"):
                    extra["results_url"] = results["presigned_url"]
                    extra["results_url_expires_in_s"] = results.get("expires_in", 3600)
        except KeelError:
            # Surface no fatal — we already have the headline metrics.
            pass

    # Good-result nudge (spec 03 R3a): exactly one line, exactly when the
    # run's durable metrics.good_result marker is set (spec 02 gate).
    from ._nudge import good_result_nudge

    nudge = good_result_nudge(detail, strategy_id=detail.get("strategy_id"), ctx=ctx)
    if nudge:
        extra["nudge"] = nudge

    # Card + per-surface render hints (spec 06 R2/R3) — render-only,
    # derived from fields already in this envelope.
    from ._render import card_render_block

    extra["render"] = card_render_block(
        "backtest", fallback_url=hero_url, ctx=ctx, embed_id=backtest_id
    )

    return OutcomeResult(
        run_id=backtest_id,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=summary_metrics,
        resource_uri=resource_uri,
        extra=extra,
    )


BACKTEST_SUMMARIZE = register(
    OutcomeTool(
        name="keel_backtest_summarize",
        required_action="backtest.read",
        cli_path=("backtest", "summarize"),
        toolset="backtest",
        # grounded-in: trading_domain.md:62-68 (post-backtest reasoning —
        # diagnose the mechanism not the outcome; changes need a principled
        # reason independent of the backtest; prefer robustness over
        # removing exposure); tool_usage.md:17; reasoning_principles.md.
        description=(
            "Summarize a completed backtest: Sharpe / max drawdown / total "
            "return / turnover / funding-attribution, plus period info and "
            "a presigned `results.json` URL when the run is complete. "
            "Returns `hero_url` deep-linked to the tearsheet view. "
            "BE PROACTIVE: after `keel_backtest_run` returns successfully, "
            "call this automatically with the same backtest_id to enrich "
            "your reply to the user. Don't ask 'do you want the full "
            "metrics?' first — they almost always do. "
            "Then READ the result and reason about WHY: diagnose the "
            "mechanism, not the outcome — 'mean-reversion shorts in a "
            "parabolic breakout with no trend filter' is a mechanism; "
            "'shorts lost money in Nov 2024' is an outcome. Any change you "
            "propose must have a principled reason independent of this "
            "backtest; prefer adding robustness (trend filter, sizing, "
            "regime gate) over removing exposure to dodge one bad window — "
            "that's curve-fitting. "
            "Do NOT use mid-run — agent should poll status_url or wait for "
            "the post-run hook. Call `keel_backtest_run` (with `wait=true`) "
            "for live submission + completion."
        ),
        input_schema={
            "type": "object",
            "required": ["backtest_id"],
            "properties": {
                "backtest_id": {
                    "type": "string",
                    "description": "The backtest_id returned by `keel_backtest_run`.",
                    "x-cli-positional": True,
                },
            },
        },
        annotations={
            "title": "Summarize Backtest Results",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        # Listed-profile copy (spec 01 R3): "funding-attribution" is the
        # crypto-perps metric name, but the listed surface bans the
        # fund* token family outright (research/08) — "carry
        # attribution" is the equivalent finance term.
        listed_description=(
            "Summarize a completed backtest: Sharpe / max drawdown / total "
            "return / turnover / carry attribution, plus period info and "
            "a presigned `results.json` URL when the run is complete. "
            "Returns `hero_url` deep-linked to the tearsheet view. "
            "BE PROACTIVE: after `keel_backtest_run` returns successfully, "
            "call this automatically with the same backtest_id to enrich "
            "your reply to the user. Don't ask 'do you want the full "
            "metrics?' first — they almost always do. "
            "Then READ the result and reason about WHY: diagnose the "
            "mechanism, not the outcome — 'mean-reversion shorts in a "
            "parabolic breakout with no trend filter' is a mechanism; "
            "'shorts lost money in Nov 2024' is an outcome. Any change you "
            "propose must have a principled reason independent of this "
            "backtest; prefer adding robustness (trend filter, sizing, "
            "regime gate) over removing exposure to dodge one bad window — "
            "that's curve-fitting. "
            "Do NOT use mid-run — agent should poll status_url or wait for "
            "the post-run hook. Call `keel_backtest_run` (with `wait=true`) "
            "for submission + completion."
        ),
    )
)
