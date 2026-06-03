"""`keel_backtest_run` — submit a backtest, optionally wait for completion.

Per spec §4 #7 (lines 281): submits via `POST /v1/backtests`, optionally
polls `GET /v1/backtests/{id}` until terminal, and surfaces final metrics
+ tearsheet URL.

Consolidates legacy `backtest_run`, `backtest_status` (when called for
live polling), and parts of `backtest_list`. Each call queues a NEW run
— this tool is non-idempotent.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# Terminal API statuses (lowercased — the API returns lowercase).
_TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}
_SUCCESS_STATUSES = {"succeeded", "completed"}

# Polling cadence: ~90s total wall-clock budget when `wait=true`.
_POLL_INTERVAL_S = 3.0
_POLL_MAX_S = 90.0


def _default_end_date() -> str:
    """Return today's UTC date for open-ended backtest ranges."""
    return datetime.now(UTC).date().isoformat()


# Earliest HL data in Keel's cache: BTC/ETH/SOL from 2024-08-15. Newer
# assets (HYPE etc.) join their own series later; the backtester only
# evaluates assets that have data in the requested window, so this is a
# safe blanket default — older requests are accepted but truncated to
# the first available bar per asset.
_DEFAULT_START_DATE = "2024-08-15"


def _default_start_date() -> str:
    return _DEFAULT_START_DATE


def _extract_summary_metrics(metrics: dict | None) -> dict | None:
    """Pull canonical metric keys out of the freeform `metrics` blob.

    The API returns `metrics` as a `dict | None`; backtest workers write
    a variety of keys. We surface the standard set the agent cares about
    and drop everything else into `extra.metrics_raw` so nothing is lost.
    """
    if not metrics:
        return None

    canonical_keys = (
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
    )
    summary = {k: metrics[k] for k in canonical_keys if k in metrics}
    return summary or None


def _poll_until_terminal(client, backtest_id: str) -> dict:
    """Poll `GET /v1/backtests/{id}` until status is terminal or budget
    is exhausted. Returns the last status snapshot regardless."""
    deadline = time.monotonic() + _POLL_MAX_S
    snapshot: dict = {}
    while time.monotonic() < deadline:
        snapshot = client.get(f"/v1/backtests/{backtest_id}")
        status = (snapshot.get("status") or "").lower()
        if status in _TERMINAL_STATUSES:
            return snapshot
        time.sleep(_POLL_INTERVAL_S)
    return snapshot


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id")
    start_date = args.get("start_date") or _default_start_date()
    end_date = args.get("end_date") or _default_end_date()

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass a strategy_id (run `keel strategy search` to list).",
        )

    # ── Local-divergence guard ────────────────────────────────────────
    # Backtests run against SERVER HEAD. If the strategy is checked out
    # locally AND the working copy has unpushed edits, the backtest will
    # silently test the OLD code — the most surprising kind of bug. So:
    #
    #   * If explicit `commit_id` set → user pinned a version, skip check.
    #   * If not checked out → no local copy to worry about, skip.
    #   * If checked out + clean → proceed silently.
    #   * If checked out + ahead + auto_push=True → push first, then run
    #     against the new HEAD (capture commit_id from push result).
    #   * If checked out + ahead + auto_push not set → raise so the agent
    #     can decide (push, force-pull, or pin commit_id).
    #
    # All checks are advisory: wrapped so a workspace bug never blocks
    # an otherwise-valid backtest.
    auto_push = bool(args.get("auto_push", False))
    divergence_warning: str | None = None
    if not args.get("commit_id"):
        try:
            from keel.workspace import (
                _compute_hash,
                get_workspace,
                read_local_source,
            )
            from keel.workspace import (
                push as _ws_push,
            )

            meta = get_workspace(strategy_id)
            if meta is not None:
                local_source = read_local_source(strategy_id)
                local_hash = _compute_hash(local_source)
                if local_hash != meta.source_hash:
                    if auto_push:
                        push_result = _ws_push(
                            strategy_id=strategy_id,
                            message=args.get("push_message") or "Auto-push before backtest",
                        )
                        # `push()` now propagates commit_id via a follow-up
                        # GET on /versions?limit=1. Pin the backtest to it
                        # explicitly so even if HEAD moves between push and
                        # POST /v1/backtests we test the version we just
                        # committed — not whatever happens to be HEAD.
                        pushed_seq = push_result.get("sequence")
                        pushed_hash = (push_result.get("source_hash") or "")[:12]
                        pushed_commit = push_result.get("commit_id")
                        if pushed_commit:
                            args["commit_id"] = pushed_commit
                        commit_str = f"commit_id={pushed_commit}, " if pushed_commit else ""
                        divergence_warning = (
                            f"Local was ahead — auto-pushed (sequence={pushed_seq}, "
                            f"{commit_str}hash={pushed_hash}). Backtest pinned to the "
                            "new commit."
                        )
                    else:
                        raise KeelError(
                            "Local workspace has unpushed edits — backtest would test "
                            "the OLD server version, not your local changes.",
                            error_code="local_ahead",
                            exit_code=2,
                            suggestion=(
                                "Either `keel_strategy_push` first, OR re-run with "
                                "`auto_push=True` to push and backtest in one step, OR "
                                "pass an explicit `commit_id` to pin to a historical "
                                "version. See `keel_strategy_status` for the diff."
                            ),
                        )
        except KeelError:
            raise
        except Exception:
            # Workspace lib problem (offline, missing files, etc.) — don't
            # block the backtest. The server check on `commit_id` will
            # surface any real config issue.
            pass

    body: dict[str, Any] = {
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if args.get("commit_id"):
        body["commit_id"] = args["commit_id"]
    if args.get("config") is not None:
        body["backtest_config"] = args["config"]

    client = ctx.get_client()
    submission = client.post("/v1/backtests", json=body)

    backtest_id = submission.get("id") or submission.get("backtest_id")
    if not backtest_id:
        raise KeelError(
            "API did not return a backtest id.",
            error_code="invalid_response",
            suggestion="Run `keel doctor` to verify API connectivity.",
        )

    hero_url = f"{ctx.app_url}/backtests/{backtest_id}?tab=tearsheet"
    resource_uri = f"keel://backtest/{backtest_id}/results"

    wait = args.get("wait", True)
    if not wait:
        info = "Submitted; not waiting (wait=false). Poll status_url or use keel_backtest_summarize when done."
        if divergence_warning:
            info = f"{divergence_warning} {info}"
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra={
                "status_url": hero_url,
                "status": (submission.get("status") or "queued").lower(),
                "strategy_id": strategy_id,
                "info": info,
                "auto_pushed_commit_id": args.get("commit_id") if divergence_warning else None,
            },
        )

    final = _poll_until_terminal(client, backtest_id)
    final_status = (final.get("status") or "").lower()

    extra: dict[str, Any] = {
        "status_url": hero_url,
        "status": final_status or "unknown",
        "strategy_id": strategy_id,
    }
    if divergence_warning:
        extra["sync_note"] = divergence_warning
        extra["auto_pushed_commit_id"] = args.get("commit_id")

    if final_status not in _TERMINAL_STATUSES:
        # Timed out — return cleanly with status_url so the agent can
        # come back later. Do NOT raise: the run is still progressing.
        extra["info"] = (
            f"Backtest still running after {int(_POLL_MAX_S)}s. "
            "Poll status_url or call `keel_backtest_summarize` once complete."
        )
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra=extra,
        )

    if final_status not in _SUCCESS_STATUSES:
        # Terminal but not successful — failed or cancelled. Surface the
        # error message but return rather than raising so the agent sees
        # the structured envelope.
        extra["error_message"] = final.get("error_message")
        extra["info"] = f"Backtest terminated with status={final_status}."
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra=extra,
        )

    # Success — populate summary_metrics + tearsheet URL.
    extra["tearsheet_url"] = hero_url
    if final.get("completed_at"):
        extra["completed_at"] = final["completed_at"]
    if final.get("execution_time") is not None:
        extra["execution_time_s"] = final["execution_time"]

    return OutcomeResult(
        run_id=backtest_id,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=_extract_summary_metrics(final.get("metrics")),
        resource_uri=resource_uri,
        extra=extra,
    )


BACKTEST_RUN = register(
    OutcomeTool(
        name="keel_backtest_run",
        required_action="backtest.create",
        cli_path=("backtest", "run"),
        toolset="backtest",
        description=(
            "Submit a backtest for a strategy over a date range. Returns "
            "`run_id` (= backtest_id), `status_url`, and — when `wait=true` "
            "(default) and the run finishes within the polling budget — "
            "`tearsheet_url` plus `summary_metrics` (Sharpe, total return, "
            "max drawdown, …). On polling timeout the envelope still "
            "returns cleanly with `status` and `status_url` set. "
            "Each call queues a NEW run — this tool is non-idempotent. "
            'DEFAULTS: when the user says "backtest X" without dates, just '
            "run it — `start_date` defaults to 2024-08-15 (earliest cached "
            "HL data) and `end_date` to today's UTC date. Mention the dates "
            "used in your reply so the user can narrow them if they want. "
            "Do NOT ask the user to pick a date range first. "
            "Pass `commit_id` to backtest a historical commit (find via "
            "`keel_strategy_log`); otherwise runs server HEAD. "
            "Local-divergence guard: if the strategy is checked out AND "
            "local has unpushed edits, raises `local_ahead` so you don't "
            "silently test old code. Either push first, pass an explicit "
            "`commit_id`, or set `auto_push=True` to push + backtest in "
            "one step. "
            "Do NOT use to re-fetch results for an already-completed run — "
            "read the `keel://backtest/<id>/results` resource instead, or call "
            "`keel_backtest_summarize`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy to backtest. Use `keel_strategy_search` to discover.",
                    "x-cli-positional": True,
                },
                "start_date": {
                    "type": "string",
                    "format": "date",
                    "description": (
                        "Inclusive start date, YYYY-MM-DD. Optional; defaults "
                        "to 2024-08-15 (earliest cached HL data) when omitted."
                    ),
                },
                "end_date": {
                    "type": "string",
                    "format": "date",
                    "description": (
                        "Inclusive end date, YYYY-MM-DD. Optional; defaults "
                        "to today's UTC date when omitted."
                    ),
                },
                "commit_id": {
                    "type": "string",
                    "description": (
                        "Pin to a specific commit; defaults to strategy HEAD. "
                        "Find historical commits via `keel_strategy_log`."
                    ),
                },
                "config": {
                    "type": "object",
                    "description": "Worker config overrides (slippage, fees, initial_capital).",
                },
                "wait": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Block up to ~90s polling for completion. On timeout the "
                        "result still returns with `status_url` set so the agent "
                        "can come back later."
                    ),
                },
                "auto_push": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If the local workspace has unpushed edits, push them "
                        "first and backtest the resulting commit. Without this, "
                        "an unpushed local copy raises `local_ahead` to prevent "
                        "silently testing old server code."
                    ),
                },
                "push_message": {
                    "type": "string",
                    "description": (
                        "Commit message to use when `auto_push=True` triggers a "
                        "pre-backtest push. Defaults to 'Auto-push before backtest'."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
