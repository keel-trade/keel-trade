"""`keel_backtest_watch` - poll an existing backtest until terminal or timeout."""

from __future__ import annotations

import time
from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._backtest_view import (
    attach_run_config,
    backtest_next,
    build_backtest_view,
    fetch_curve_and_reference,
    is_success,
)
from ._base import (
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    present_choice,
    present_param_schema,
)
from ._ownership import fetch_ownership_projection, ownership_envelope_fields
from .backtest_summarize import _extract_summary_metrics
from .open_in_app import app_url_for


#: This tool's `present` default, as its parameter description states it.
PRESENT_DEFAULT = "`receipt`"

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
    return interval_s, timeout_s


def _snapshot_envelope(
    *,
    backtest_id: str,
    detail: dict,
    ctx: ToolContext,
    polls: int,
    watched_for_s: float,
    timed_out: bool,
    include_ownership_hint: bool = True,
    size: str = "receipt",
) -> OutcomeResult:
    status = (detail.get("status") or "").lower()
    terminal = status in _TERMINAL_STATUSES
    hero_url = app_url_for("backtest", backtest_id, ctx)
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
    if detail.get("metrics"):
        # The same hygiene notes the other two backtest tools carry — one
        # card reads `env.notes` for all three (Q-1716).
        from ._backtest_view import notes_block

        notes = notes_block(detail["metrics"])
        if notes:
            extra["notes"] = notes
    if include_ownership_hint and detail.get("strategy_id"):
        extra.update(
            ownership_envelope_fields(fetch_ownership_projection(ctx, str(detail["strategy_id"])))
        )

    summary_metrics = _extract_summary_metrics(detail.get("metrics"))

    if terminal and status in _SUCCESS_STATUSES:
        extra["tearsheet_url"] = hero_url
        from ._toolsets import is_listed_profile

        # No signed storage URL on the LISTED profile (Q-2268) — the same
        # rule as `keel_backtest_summarize`; `tearsheet_url` is the link.
        if not is_listed_profile():
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
            "Backtest is still running. Call `keel_backtest_watch` again or open `status_url`."
        )

    # The run's own rendering (§2.1): a watch that finds a finished run
    # IS that run's result, so it carries the same card `summarize`
    # draws — at its receipt size, because a watch is a step.
    view_detail = {**detail, "id": backtest_id}
    reference = None
    if is_success(view_detail):
        curve, reference = fetch_curve_and_reference(ctx.get_client(), backtest_id)
        if curve:
            extra["curve"] = curve

    from ._backtest_view import attach_run_facts
    from ._render import card_render_block

    # The run's served facts (spec 02 §2.4): the window object, and on a
    # completed run the reference, realism, sample-size and good-result data.
    attach_run_facts(extra, view_detail, client=ctx.get_client(), reference=reference)
    view = build_backtest_view(view_detail, size=size, url=hero_url, reference=reference)
    if view is not None:
        extra["view"] = view
        attach_run_config(extra, view, ctx.get_client(), view_detail)
    extra["render"] = card_render_block("backtest", fallback_url=hero_url, ctx=ctx)

    line = backtest_next(
        ctx.get_client(),
        view_detail,
        view,
        strategy_id=detail.get("strategy_id"),
        few_fills=False,
    )
    if line:
        extra["next"] = line

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
    # A watch is a STEP in a set — the default is the one-line receipt
    # that opens in place (BUILD §2.4).
    size = "evidence" if present_choice(args) == "view" else "receipt"
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
                include_ownership_hint=not args.get("skip_readiness", False),
                size=size,
            )

        if now >= deadline:
            return _snapshot_envelope(
                backtest_id=backtest_id,
                detail=detail,
                ctx=ctx,
                polls=polls,
                watched_for_s=now - started,
                timed_out=True,
                include_ownership_hint=not args.get("skip_readiness", False),
                size=size,
            )

        time.sleep(min(interval_s, max(0.0, deadline - now)))


BACKTEST_WATCH = register(
    OutcomeTool(
        name="keel_backtest_watch",
        required_action="backtest.read",
        cli_path=("backtest", "watch"),
        toolset="backtest",
        # grounded-in: backtest_watch.py _handler (bounded: interval 1-60s and
        # timeout 0-600s, declared and enforced — Q-2270; returns the latest snapshot even when
        # non-terminal) + system/chat/tool_usage.md:17 (once a result exists, reason
        # about the mechanism — the hand-off to keel_backtest_summarize).
        description=(
            "Wait on a running backtest (started by `keel_backtest_run`) until it finishes "
            "or the timeout elapses — a finished run in full is `keel_backtest_summarize`. "
            "It polls to a terminal status (succeeded, failed, cancelled) and returns the "
            "latest snapshot even while the run is going (`terminal=false`: watch again). "
            "Returns the status, and when complete the final metrics and the tearsheet "
            "link `hero_url`."
        ),
        input_schema={
            "type": "object",
            "required": ["backtest_id"],
            "properties": {
                "backtest_id": {
                    "type": "string",
                    "description": "The run's id — the `run_id` that `keel_backtest_run` returns.",
                    "x-cli-positional": True,
                },
                "interval_s": {
                    "type": "number",
                    "default": _DEFAULT_INTERVAL_S,
                    "minimum": 1,
                    "maximum": 60,
                    "description": "Seconds between status checks (1-60).",
                },
                "timeout_s": {
                    "type": "integer",
                    "default": int(_DEFAULT_TIMEOUT_S),
                    "minimum": 0,
                    "maximum": int(_MAX_TIMEOUT_S),
                    "description": "Maximum watch duration in seconds (0-600).",
                },
                "skip_readiness": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Leave the strategy's readiness fields (next step, missing "
                        "evidence) out of the result."
                    ),
                },
                "present": present_param_schema(PRESENT_DEFAULT),
            },
        },
        annotations={
            "title": "Watch Backtest Progress",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
