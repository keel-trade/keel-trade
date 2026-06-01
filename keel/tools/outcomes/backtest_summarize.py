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
        description=(
            "Summarize a completed backtest: Sharpe / max drawdown / total "
            "return / turnover / funding-attribution, plus period info and "
            "a presigned `results.json` URL when the run is complete. "
            "Returns `hero_url` deep-linked to the tearsheet view. "
            "BE PROACTIVE: after `keel_backtest_run` returns successfully, "
            "call this automatically with the same backtest_id to enrich "
            "your reply to the user. Don't ask 'do you want the full "
            "metrics?' first — they almost always do. "
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
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
