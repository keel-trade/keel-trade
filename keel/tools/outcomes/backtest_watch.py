"""`keel_backtest_watch` - poll an existing backtest until terminal or timeout."""

from __future__ import annotations

import time
from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .backtest_summarize import _extract_summary_metrics


_TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}
_SUCCESS_STATUSES = {"succeeded", "completed"}
_DEFAULT_INTERVAL_S = 5.0
_DEFAULT_TIMEOUT_S = 120.0
_MAX_TIMEOUT_S = 600.0


def _watch_settings(args: dict) -> tuple[float, float]:
    raw_interval = args.get("interval_s")
    raw_timeout = args.get("timeout_s")
    interval_s = float(_DEFAULT_INTERVAL_S if raw_interval is None else raw_interval)
    timeout_s = float(_DEFAULT_TIMEOUT_S if raw_timeout is None else raw_timeout)
    interval_s = max(1.0, min(interval_s, 60.0))
    timeout_s = max(0.0, min(timeout_s, _MAX_TIMEOUT_S))
    return interval_s, timeout_s


def _snapshot_envelope(
    *,
    backtest_id: str,
    detail: dict,
    ctx: ToolContext,
    polls: int,
    watched_for_s: float,
    timed_out: bool,
) -> OutcomeResult:
    status = (detail.get("status") or "").lower()
    terminal = status in _TERMINAL_STATUSES
    hero_url = f"{ctx.app_url}/backtests/{backtest_id}?tab=tearsheet"
    resource_uri = f"keel://backtest/{backtest_id}/results"

    extra: dict[str, Any] = {
        "status": status or "unknown",
        "terminal": terminal,
        "timed_out": timed_out,
        "polls": polls,
        "watched_for_s": round(watched_for_s, 3),
        "status_url": hero_url,
        "strategy_id": detail.get("strategy_id"),
        "strategy_name": detail.get("strategy_name"),
        "commit_id": detail.get("commit_id"),
        "sequence_number": detail.get("sequence_number"),
        "queued_at": detail.get("queued_at"),
        "started_at": detail.get("started_at"),
        "completed_at": detail.get("completed_at"),
        "execution_time_s": detail.get("execution_time"),
    }
    if detail.get("error_message"):
        extra["error_message"] = detail["error_message"]

    summary_metrics = _extract_summary_metrics(detail.get("metrics"))

    if terminal and status in _SUCCESS_STATUSES:
        extra["tearsheet_url"] = hero_url
        try:
            results = ctx.get_client().get(f"/v1/backtests/{backtest_id}/results")
            if isinstance(results, dict) and results.get("presigned_url"):
                extra["results_url"] = results["presigned_url"]
                extra["results_url_expires_in_s"] = results.get("expires_in", 3600)
        except KeelError:
            pass
    elif terminal:
        extra["info"] = f"Backtest terminated with status={status}."
    else:
        extra["next_action"] = {
            "tool": "keel_backtest_watch",
            "args": {"backtest_id": backtest_id},
        }
        extra["info"] = (
            "Backtest is still running. Call `keel_backtest_watch` again or open "
            "`status_url`."
        )

    return OutcomeResult(
        run_id=backtest_id,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=summary_metrics,
        resource_uri=resource_uri,
        extra=extra,
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    backtest_id = args.get("backtest_id")
    if not backtest_id:
        raise KeelError(
            "Missing required `backtest_id`.",
            error_code="missing_backtest_id",
            exit_code=2,
            suggestion="Pass the run_id returned by `keel_backtest_run`.",
        )

    interval_s, timeout_s = _watch_settings(args)
    client = ctx.get_client()
    started = time.monotonic()
    deadline = started + timeout_s
    polls = 0

    while True:
        polls += 1
        try:
            detail = client.get(f"/v1/backtests/{backtest_id}")
        except NotFoundError:
            raise NotFoundError(
                f"Backtest {backtest_id} not found.",
                suggestion="Verify the backtest_id returned by `keel_backtest_run`.",
            )

        status = (detail.get("status") or "").lower()
        now = time.monotonic()
        if status in _TERMINAL_STATUSES:
            return _snapshot_envelope(
                backtest_id=backtest_id,
                detail=detail,
                ctx=ctx,
                polls=polls,
                watched_for_s=now - started,
                timed_out=False,
            )

        if now >= deadline:
            return _snapshot_envelope(
                backtest_id=backtest_id,
                detail=detail,
                ctx=ctx,
                polls=polls,
                watched_for_s=now - started,
                timed_out=True,
            )

        time.sleep(min(interval_s, max(0.0, deadline - now)))


BACKTEST_WATCH = register(
    OutcomeTool(
        name="keel_backtest_watch",
        required_action="backtest.read",
        cli_path=("backtest", "watch"),
        toolset="backtest",
        description=(
            "Poll an existing backtest until it reaches a terminal status or the "
            "timeout elapses. Returns status, final metrics when available, "
            "results_url when complete, and the stable tearsheet hero_url. "
            "Do NOT use to start a new run - call `keel_backtest_run` first. "
            "Do NOT build ad hoc polling loops around `keel_backtest_summarize`; "
            "use this bounded watch helper."
        ),
        input_schema={
            "type": "object",
            "required": ["backtest_id"],
            "properties": {
                "backtest_id": {
                    "type": "string",
                    "description": "The run_id returned by `keel_backtest_run`.",
                    "x-cli-positional": True,
                },
                "interval_s": {
                    "type": "number",
                    "default": _DEFAULT_INTERVAL_S,
                    "description": "Seconds between status checks. Clamped to 1-60.",
                },
                "timeout_s": {
                    "type": "integer",
                    "default": int(_DEFAULT_TIMEOUT_S),
                    "description": "Maximum watch duration in seconds. Clamped to 0-600.",
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
