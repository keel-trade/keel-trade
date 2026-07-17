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

from pydantic import ValidationError

from keel.errors import EntitlementError, KeelError
from pipeline_engine.backtest_config import BacktestConfig, adapt_legacy_initial_capital

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._ownership import fetch_ownership_projection, ownership_envelope_fields


# Terminal API statuses (lowercased — the API returns lowercase).
_TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}
_SUCCESS_STATUSES = {"succeeded", "completed"}

# Polling cadence: ~90s total wall-clock budget when `wait=true`.
_POLL_INTERVAL_S = 3.0
_POLL_MAX_S = 90.0


def _sdk_backtest_config_schema() -> dict[str, Any]:
    """Canonical schema plus the one exact deprecated SDK alias."""
    schema = BacktestConfig.model_json_schema()
    schema["description"] = "Validated worker financial overrides."
    schema["properties"]["initial_capital"] = {
        **schema["properties"]["init_cash"],
        "description": "Deprecated alias for init_cash; do not provide both.",
        "deprecated": True,
    }
    return schema


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

    # Validate before the divergence guard can reach workspace.push().
    canonical_config: dict[str, float] | None = None
    if args.get("config") is not None:
        try:
            config_input = args["config"]
            if not isinstance(config_input, dict):
                raise TypeError("config must be an object")
            canonical_input = adapt_legacy_initial_capital(config_input)
            canonical_config = BacktestConfig.model_validate(canonical_input).as_overrides()
        except (TypeError, ValueError, ValidationError) as exc:
            raise KeelError(
                f"Invalid backtest config: {exc}",
                error_code="invalid_backtest_config",
                exit_code=2,
                suggestion=(
                    "Use only init_cash, fees, slippage, and leverage; leverage must be "
                    "greater than 0 and at most 100."
                ),
            ) from exc

    # ── Write-through guard (spec 08 R2) ──────────────────────────────
    # Backtests run against SERVER HEAD. If the strategy is checked out
    # locally AND the working copy has unpushed edits, the backtest would
    # silently test the OLD code — the most surprising kind of bug. The
    # DEFAULT is write-through: push the local edits (generated message),
    # pin to the pushed commit, then run. `auto_push=False` is the
    # opt-out (raises `local_ahead` so the agent decides). A true
    # conflict (server moved too) always stops — never force-overwrites.
    #
    #   * If explicit `commit_id` set → user pinned a version, skip check.
    #   * Hosted server → no caller filesystem, guard no-ops (spec 01 R2).
    divergence_warning: str | None = None
    if not args.get("commit_id"):
        from ._sync_guard import write_through_guard

        push_result = write_through_guard(
            args,
            strategy_id=strategy_id,
            action="backtest",
            default_message="Auto-push before backtest",
        )
        if push_result is not None and push_result.get("status") == "pushed":
            # `push()` propagates commit_id via a follow-up GET on
            # /versions?limit=1. Pin the backtest to it explicitly so even
            # if HEAD moves between push and POST /v1/backtests we test
            # the version we just committed — not whatever happens to be
            # HEAD.
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

    body: dict[str, Any] = {
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if args.get("commit_id"):
        body["commit_id"] = args["commit_id"]
    if canonical_config is not None:
        body["backtest_config"] = canonical_config

    client = ctx.get_client()
    try:
        submission = client.post("/v1/backtests", json=body)
    except EntitlementError as e:
        # Quota wall (spec 03 R1): plan-limit 403s become the shared
        # handoff envelope — exact numbers from the API, human-only
        # billing action, do-nothing alternative. Scope-shaped 403s
        # re-raise unchanged (re-auth is agent-recoverable).
        from ._handoff import maybe_quota_handoff

        handoff = maybe_quota_handoff(
            e,
            blocked_action="backtest_run",
            retry_call={
                "tool": "keel_backtest_run",
                "args": {
                    "strategy_id": strategy_id,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            },
        )
        if handoff is not None:
            raise handoff from e
        raise

    backtest_id = submission.get("id") or submission.get("backtest_id")
    if not backtest_id:
        raise KeelError(
            "API did not return a backtest id.",
            error_code="invalid_response",
            suggestion="Run `keel doctor` to verify API connectivity.",
        )

    hero_url = f"{ctx.app_url}/backtests/{backtest_id}?tab=tearsheet"
    resource_uri = f"keel://backtest/{backtest_id}/results"
    ownership_fields = (
        ownership_envelope_fields(fetch_ownership_projection(ctx, strategy_id))
        if not args.get("no_ownership_hint", False)
        else {}
    )

    # Quota visibility (spec 04 R5): the submit response carries `remaining`
    # counters ONLY when a consumed unit is strictly below 20% of the plan
    # quota. Pass through verbatim so the agent can plan ahead of the wall —
    # numbers only, no upsell language.
    remaining_quota = submission.get("remaining") if isinstance(submission, dict) else None

    # NOTE(M1.4, spec 01 R5 — MCP Tasks shape): this immediate-return
    # envelope (run_id + status + status_url, polled via
    # keel_backtest_watch / keel_backtest_summarize) is where
    # spec-conformant task metadata would attach once the MCP Tasks
    # extension (io.modelcontextprotocol/tasks, SEP-2663) finalizes in
    # the 2026-07-28 release. Do NOT add draft-spec fields before the
    # final spec lands — recheck against the published extension on
    # 2026-07-28 (open item recorded in
    # projects/fable/agent-first-build/orchestration/progress.md).
    wait = args.get("wait", True)
    if not wait:
        info = "Submitted; not waiting (wait=false). Poll status_url or use keel_backtest_summarize when done."
        if divergence_warning:
            info = f"{divergence_warning} {info}"
        extra = {
            "status_url": hero_url,
            "status": (submission.get("status") or "queued").lower(),
            "strategy_id": strategy_id,
            "info": info,
            "auto_pushed_commit_id": args.get("commit_id") if divergence_warning else None,
        }
        extra.update(ownership_fields)
        if remaining_quota:
            extra["remaining"] = remaining_quota
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra=extra,
        )

    final = _poll_until_terminal(client, backtest_id)
    final_status = (final.get("status") or "").lower()

    extra: dict[str, Any] = {
        "status_url": hero_url,
        "status": final_status or "unknown",
        "strategy_id": strategy_id,
    }
    extra.update(ownership_fields)
    if remaining_quota:
        extra["remaining"] = remaining_quota
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

    # Good-result nudge (spec 03 R3a): exactly one line, exactly when the
    # run's durable metrics.good_result marker is set (spec 02 gate).
    from ._nudge import good_result_nudge

    nudge = good_result_nudge(final, strategy_id=strategy_id, ctx=ctx)
    if nudge:
        extra["nudge"] = nudge

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
            "Write-through (server HEAD is the source of truth): if the "
            "strategy is checked out locally with unpushed edits, they are "
            "pushed automatically first (generated commit message, or pass "
            "`push_message`) and the backtest pins to the new commit — so "
            "you always test what you actually have. Set `auto_push=False` "
            "to opt out (raises `local_ahead` instead). A true conflict "
            "(local edited AND server moved) always stops with recovery "
            "options — never force-overwrites. "
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
                "config": _sdk_backtest_config_schema(),
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
                    "default": True,
                    "description": (
                        "Write-through default (true): if the local workspace "
                        "has unpushed edits, push them first and backtest the "
                        "resulting commit. Set false to opt out — an unpushed "
                        "local copy then raises `local_ahead` instead of "
                        "silently testing old server code. Conflicts (server "
                        "moved too) always stop regardless."
                    ),
                },
                "push_message": {
                    "type": "string",
                    "description": (
                        "Commit message to use when the write-through guard "
                        "pushes before the backtest. Defaults to 'Auto-push "
                        "before backtest'."
                    ),
                },
                "no_ownership_hint": {
                    "type": "boolean",
                    "default": False,
                    "description": "Omit first-session ownership guidance fields.",
                },
            },
        },
        annotations={
            "title": "Run Backtest",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
