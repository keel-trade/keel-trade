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

from keel.errors import (
    EntitlementError,
    KeelError,
    assert_neutral_wall_text,
    project_first_week,
    render_first_week_sentence,
    render_quota_sentence,
)
from keel.hosting import record_outcome
from pipeline_engine.backtest_config import BacktestConfig, adapt_legacy_initial_capital

from . import register
from ._backtest_view import (
    WINDOW_APPROACH_TEXT,
    attach_run_config,
    backtest_next,
    build_backtest_view,
    era_metrics,
    fetch_curve_and_reference,
    is_success,
    notes_block,
    signed_drawdown,
)
from ._base import (
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    listed_schema,
    present_choice,
    present_param_schema,
)
from ._ownership import fetch_ownership_projection, ownership_envelope_fields
from ._surface_hints import tool_ref
from .open_in_app import app_url_for


# Terminal API statuses (lowercased — the API returns lowercase).
_TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}
_SUCCESS_STATUSES = {"succeeded", "completed"}
# A run that ENDED badly within the call (Q-2293). The audit records the run's
# verdict as `outcome.is_error` even though the CALL succeeded and returns a
# normal envelope: "failed" is the worker's FAILED, "timeout" its hard-timeout
# TIMEOUT (served lowercased; note it is not in _TERMINAL_STATUSES, Q-2424).
# "cancelled" is deliberately absent -- a cancel is the user's act, not a
# failure -- and so is every non-terminal status: a run still queued or
# running when the poll budget ends is not an error.
_RUN_FAILED_STATUSES = frozenset({"failed", "timeout"})

# Polling cadence (Q-0573 / cli-backtest-wait-budget-too-short): the two
# surfaces get different wall-clock budgets when `wait=true`. Non-interactive
# callers (MCP tools, `--format json` agents) keep ~90s — a blocking tool
# call inside an agent turn must return promptly, and the envelope's
# status_url handoff covers the rest. Interactive terminals (ctx.is_tty)
# poll up to 5 minutes with a progress line: observed healthy runs take
# ~141s, and a human watching a progress line can always Ctrl-C.
_POLL_INTERVAL_S = 3.0
# The first seconds poll FAST (Q-1870): a healthy run is queued for
# 0.1-0.8 s and executes in 2.4-5.7 s (staging, 2026-09-23), so a flat 3 s
# cadence found a 3.2 s run at 6 s — up to a whole interval of dead wait on
# every call, twice the run itself. Inside `_POLL_FAST_WINDOW_S` the cadence
# is `_POLL_FAST_INTERVAL_S` (never slower than `_POLL_INTERVAL_S`); a GET of
# one run row costs keel-api ~20 ms, so the extra polls are free.
_POLL_FAST_INTERVAL_S = 0.5
_POLL_FAST_WINDOW_S = 12.0
_POLL_MAX_S = 90.0
_POLL_MAX_INTERACTIVE_S = 300.0


def _poll_budget_s(ctx: ToolContext) -> float:
    """Wall-clock polling budget for this surface."""
    return _POLL_MAX_INTERACTIVE_S if ctx.is_tty else _POLL_MAX_S


#: This tool's `present` default, as its parameter description states it.
PRESENT_DEFAULT = "`receipt`"


def _view_size(args: dict) -> str:
    """`receipt` unless the caller asked to present the whole thing.

    A submitted run is a STEP — the default is the one-line receipt that
    opens in place. `present="view"` is the caller saying "this is the
    run we are about to discuss" (BUILD §2.4).
    """
    return "evidence" if present_choice(args) == "view" else "receipt"


#: The submit warnings that mean "the window that ran is not the one asked
#: for" (keel-api `utils/backtest_window.clamp_warning`).
WINDOW_ADJUSTED_CODES = frozenset({"WINDOW_CLAMPED_TO_COVERAGE", "WINDOW_CLAMPED_TO_SERIES_ERA"})

#: What `window_adjusted` carries, when the server sent it.
_WINDOW_ADJUSTED_KEYS = (
    "requested_start",
    "requested_end",
    "effective_start",
    "effective_end",
    "start_reason",
    "series_era_loader",
)


def window_adjusted(submission: Any) -> dict[str, Any] | None:
    """The submit response's window-moved warning, or None (Q-1842).

    keel-api has always sent it — `warnings: [{code: WINDOW_CLAMPED_…,
    message}]` on the 201 — and this tool dropped the whole `warnings` list,
    so a `start_date=2024-01-01` request that ran from 2024-07-27 came back
    as a result over a window nobody asked for, with nothing saying so. The
    message is the server's (it owns the reason); this only relays it.
    """
    warnings = submission.get("warnings") if isinstance(submission, dict) else None
    for warning in warnings or []:
        if not isinstance(warning, dict) or warning.get("code") not in WINDOW_ADJUSTED_CODES:
            continue
        message = warning.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        out: dict[str, Any] = {"code": warning["code"], "message": message.strip()}
        out.update({k: warning[k] for k in _WINDOW_ADJUSTED_KEYS if warning.get(k) is not None})
        return out
    return None


def _attach_view(
    extra: dict[str, Any],
    detail: dict,
    *,
    size: str,
    hero_url: str | None,
    ctx: ToolContext,
    adjusted: dict[str, Any] | None = None,
    reference: dict[str, Any] | None = None,
    prefetched: dict[str, Any] | None = None,
) -> dict | None:
    """Put the backtest `view` + `render` hints on an envelope in progress.

    `prefetched` carries reads the caller already made concurrently
    (Q-1870) — `config` is `run_config`'s answer; absent, it is read here.

    `adjusted` (the window-moved warning) rides three ways, because each
    reader looks in one place: `window_adjusted` (structured), `window_note`
    (the text block's `window:` line — the model's only channel on
    claude.ai) and `view.window_note` (the card's receipt line).

    The run's served facts (the window object; on a completed run the
    reference, realism, sample-size and good-result data) attach here too,
    so every exit of this tool carries the same set (spec 02 §2.4).
    """
    from ._backtest_view import attach_run_facts
    from ._render import card_render_block

    attach_run_facts(extra, detail, client=ctx.get_client(), reference=reference)
    view = build_backtest_view(detail, size=size, url=hero_url, reference=reference)
    if view is not None:
        extra["view"] = view
        if prefetched is not None and "config" in prefetched:
            attach_run_config(extra, view, ctx.get_client(), detail, config=prefetched["config"])
        else:
            attach_run_config(extra, view, ctx.get_client(), detail)
    if adjusted is not None:
        extra["window_adjusted"] = adjusted
        extra["window_note"] = adjusted["message"]
        if view is not None:
            view["window_note"] = adjusted["message"]
    extra["render"] = card_render_block(
        "backtest", fallback_url=hero_url, ctx=ctx, note=extra.get("deploy")
    )
    return view


def _sdk_backtest_config_schema() -> dict[str, Any]:
    """Canonical schema plus the one exact deprecated SDK alias.

    `leverage`'s wording is the model's own (spec 03 §2.8: ONE owner, so the
    CLI, the API docs and this schema say the same); the listed profile
    omits the parameter (`_base.LISTED_SCHEMA_OMISSIONS`).
    """
    schema = BacktestConfig.model_json_schema()
    schema["description"] = (
        "Optional cost-model settings for this run: starting capital, fee rate and "
        "slippage rate. Omitted, the platform defaults apply."
    )
    schema["properties"]["initial_capital"] = {
        **schema["properties"]["init_cash"],
        "description": "Deprecated alias for init_cash; do not provide both.",
        "deprecated": True,
    }
    return schema


def _version_ref(value: Any) -> str | None:
    """`version` as the wire wants it: a decimal string (spec 03 §2.1).

    keel-api's request models are strict, so a JSON integer would 422; an
    integer (or a `#N`-free digit string) is sent as its decimal string, a
    tag / `HEAD~N` / commit id as given. Empty ⇒ not pinned.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    return text or None


def _sequence_range(client: Any, strategy_id: Any) -> str:
    """` (1…7)` — the strategy's version range, or empty.

    Advisory: one read of the strategy row on a refusal path; a failure
    costs the range, never the error the caller is about to see.
    """
    try:
        meta = client.get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — the range is a nicety on an error path
        return ""
    head = meta.get("current_sequence") if isinstance(meta, dict) else None
    if isinstance(head, int) and not isinstance(head, bool) and head >= 1:
        return f" (1…{head})"
    return ""


def _is_pinned(args: dict) -> bool:
    """Whether the caller pinned what runs — `commit_id` OR `version`.

    One predicate for the write-through guard: a pinned run never
    auto-pushes, whichever of the two named the pin (spec 03 §2.1).
    """
    return bool(args.get("commit_id")) or _version_ref(args.get("version")) is not None


def _default_end_date() -> str:
    """Return today's UTC date for open-ended backtest ranges."""
    return datetime.now(UTC).date().isoformat()


# NO hardcoded start (Q-1701). An omitted `start_date` is sent as OMITTED so
# keel-api applies its own floor, which this side cannot compute:
# `max(universe_floor, series_era(timeframe))`
# (services/keel-api/src/utils/backtest_window.py). 15min and coarser are
# served from the venue capture and reach back to 2024 (platform depth floor
# 2024-07-27); a clock finer than the capture is built from the 1m grid and
# can only start at its era (2025-03-22). The per-clock default window is the
# founder's spec-06 ruling (5min 60d, 15min 90d, coarser 5,000 bars,
# era-capped) and likewise belongs to the API. A constant here would arrive as
# an EXPLICIT start and silently defeat all of it.


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
        # The positions view (trade-metrics spec 01 §3); an Era B run's win
        # rate arrives here, never under `win_rate`.
        "position_win_rate",
        "turnover",
        "annual_return_pct",
        "annual_return",
        "volatility",
        "calmar",
        "sortino",
        "trades",
        "num_trades",
    )
    # The trade keys by era (spec 01 §4.2, `era_metrics`).
    read = era_metrics(metrics)
    summary = {k: read[k] for k in canonical_keys if k in read}
    # One drawdown sign on every model-visible block (Q-1805).
    return signed_drawdown(summary) if summary else None


#: Most urgent first — the block whose sentence the agent reads. Every
#: notable unit still rides in `quota`; this only picks the headline.
_TIER_ORDER = ("exhausted", "critical", "warn", "notice")


#: The quota block keys an agent surface passes through (D-12, 04 §4.4):
#: the caller's own numbers and reset. keel-api's block also carries
#: `plan` / `higher_plans` (the other plans' limits, near the wall) and
#: presentation hints; an allow-list — not a strip-list — means a future
#: server field can never reach a surface whose policy was not reviewed
#: for it. `first_week` (connect-onboarding spec 01 §1.8) is present only
#: while a first-week allowance is active, and is itself projected to
#: `errors.FIRST_WEEK_KEYS`.
QUOTA_BLOCK_KEYS: tuple[str, ...] = (
    "unit",
    "limit",
    "used",
    "remaining",
    "resets_at",
    "first_week",
)


def _project_quota_block(block: dict[str, Any]) -> dict[str, Any]:
    out = {k: block[k] for k in QUOTA_BLOCK_KEYS if k in block and k != "first_week"}
    first_week = project_first_week(block.get("first_week"))
    if first_week:
        out["first_week"] = first_week
    return out


def _first_week_sentence(blocks: list[dict[str, Any]]) -> str | None:
    """The first-week sentence from the ``backtest_runs`` block's
    ``first_week`` (spec 01 §1.9) — the sentence counts backtests, so the
    compute unit's block never renders it — or ``None``."""
    for block in blocks:
        if block.get("unit") == "backtest_runs":
            return render_first_week_sentence(block.get("first_week"))
    return None


def _quota_envelope_fields(blocks: Any) -> dict[str, Any]:
    """`quota` (projected) + `quota_notice` (the rendered line), or nothing.

    The sentence is rendered from the MOST URGENT block — a run that is
    both 90% through its compute and 50% through its run count should read
    the compute line — and every block rides in `quota`, projected to
    :data:`QUOTA_BLOCK_KEYS`, so the agent can still report every unit.
    Returns an empty dict when the server reported nothing notable, so the
    keys are absent rather than null (an empty quota key reads as "you have
    none").

    The notice is the same line for every plan (D-12 §4.2): ``4 of 50
    backtests left this week; they reset Mon 29 Sep 00:00 UTC.`` — and
    nothing after it. It used to add the higher plans and the billing link
    near the wall (Q-1806); that is what OpenAI rejected (Q-2080).
    """
    if not isinstance(blocks, list) or not blocks:
        return {}
    # An unlimited unit has no allowance to report (the headroom arm skips
    # it for the same reason), and its projection would be a bare `unit`.
    notable = [b for b in blocks if isinstance(b, dict) and not b.get("unlimited")]
    if not notable:
        return {}
    fields: dict[str, Any] = {"quota": [_project_quota_block(b) for b in notable]}
    ranked = sorted(
        notable,
        key=lambda b: (
            _TIER_ORDER.index(b["tier"]) if b.get("tier") in _TIER_ORDER else len(_TIER_ORDER)
        ),
    )
    sentence = render_quota_sentence(ranked[0])
    if sentence:
        # The first-week sentence rides ONLY a line that renders without it
        # (spec 01 §1.9): the allowance never makes a response carry a quota
        # line, and it is never a `next` step.
        first_week = _first_week_sentence(notable)
        if first_week:
            sentence = f"{sentence} {first_week}"
        fields["quota_notice"] = assert_neutral_wall_text(sentence, free_plan=False)
    return fields


#: The units a backtest run spends — the ones the headroom block reports.
_BACKTEST_UNITS = ("backtest_runs", "backtest_compute_seconds")


def _headroom_blocks(client: Any) -> list[dict[str, Any]]:
    """The backtest units' headroom as quota blocks, for the compare-hint arm
    (spec 03 §2.3): ONE advisory `GET /v1/entitlements` read, made only
    when the submit carried no ladder. Advisory — a failure costs the block,
    never the run; an unlimited unit has nothing to report.
    """
    try:
        payload = client.get("/v1/entitlements")
    except Exception:  # noqa: BLE001 — headroom is advisory
        return []
    balances = payload.get("balances") if isinstance(payload, dict) else None
    blocks: list[dict[str, Any]] = []
    for balance in balances or []:
        if not isinstance(balance, dict) or balance.get("unit") not in _BACKTEST_UNITS:
            continue
        granted, available = balance.get("granted"), balance.get("available")
        if not isinstance(granted, int) or not isinstance(available, int):
            continue
        if granted >= 2147483647:
            continue  # unlimited: "N of ∞" is not a fact worth a line
        block: dict[str, Any] = {
            "unit": balance["unit"],
            "limit": granted,
            "used": balance.get("spent"),
            "remaining": available,
        }
        for key in ("period", "resets_at", "tier", "first_week"):
            if balance.get(key) is not None:
                block[key] = balance[key]
        blocks.append(block)
    return blocks


def _poll_delay(elapsed_s: float) -> float:
    """Seconds to wait before the next status read, `elapsed_s` into the poll."""
    if elapsed_s < _POLL_FAST_WINDOW_S:
        return min(_POLL_FAST_INTERVAL_S, _POLL_INTERVAL_S)
    return _POLL_INTERVAL_S


def _poll_until_terminal(
    client, backtest_id: str, *, budget_s: float, progress: bool = False
) -> dict:
    """Poll `GET /v1/backtests/{id}` until status is terminal or `budget_s`
    is exhausted. Returns the last status snapshot regardless.

    With `progress=True` (interactive terminals) a single self-overwriting
    status line is written to stderr each poll so the user sees liveness
    and knows Ctrl-C is available."""
    import sys

    started = time.monotonic()
    deadline = started + budget_s
    snapshot: dict = {}
    wrote_progress = False
    try:
        while time.monotonic() < deadline:
            snapshot = client.get(f"/v1/backtests/{backtest_id}")
            status = (snapshot.get("status") or "").lower()
            if status in _TERMINAL_STATUSES:
                return snapshot
            if progress:
                elapsed = int(time.monotonic() - started)
                print(
                    f"\r  backtest {backtest_id}: {status or 'queued'} — "
                    f"{elapsed}s elapsed (Ctrl-C to stop waiting)",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                wrote_progress = True
            time.sleep(_poll_delay(time.monotonic() - started))
        return snapshot
    finally:
        if wrote_progress:
            print(file=sys.stderr)


def _retry_args(args: dict) -> dict[str, Any]:
    """The blocked call, as a call that reruns EXACTLY it (Q-1807).

    Every argument the caller passed that this tool declares — the pinned
    ``commit_id`` (including one the write-through guard just pinned),
    ``start_date``, ``end_date``, ``config``, ``present`` … — and nothing
    the caller did not pass. The resume used to carry only strategy_id and
    the dates, so a wall hit on a pinned v3 would, once followed after the
    reset, backtest HEAD: a different version from the one the user asked
    for. Filtered by the tool's own schema so adapter-internal keys never
    leak into an executable call.

    An omitted date stays omitted (Q-2012 / Q-2029). The resume used to add
    the ``end_date`` this call resolved to (the day of the wall), so a call
    made without dates came back as one WITH a date the user never named —
    stale by the reset the resume waits for. Rerun without it, the platform
    applies its default window again.
    """
    from . import get as get_tool

    declared = get_tool("keel_backtest_run").input_schema["properties"]
    return {k: v for k, v in args.items() if k in declared and v is not None}


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = args.get("strategy_id")
    # `start_date` stays None when the caller omitted it: the platform's
    # timeframe-aware floor decides (Q-1701). `end_date` still defaults to
    # today because "now" is not a platform fact the API computes.
    start_date = args.get("start_date")
    end_date = args.get("end_date") or _default_end_date()

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion=f"Pass a strategy_id ({tool_ref('keel_strategy_search')} lists them).",
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
            # pydantic's str() carries `input_value=…`, the error type and an
            # errors.pydantic.dev URL (Q-2273 L3): name the field and the
            # problem only.
            from keel.errors import readable_validation_errors

            problems = (
                readable_validation_errors(exc.errors())
                if isinstance(exc, ValidationError)
                else f"{exc}."
            )
            raise KeelError(
                f"Invalid backtest config: {problems}",
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
    if not _is_pinned(args):
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
        "end_date": end_date,
    }
    if start_date:
        body["start_date"] = start_date
    if args.get("commit_id"):
        body["commit_id"] = args["commit_id"]
    version_ref = _version_ref(args.get("version"))
    if version_ref is not None:
        # keel-api resolves it (sequence first, experiment commits excluded)
        # and refuses `version` beside `commit_id` — never picked silently.
        body["version"] = version_ref
    if canonical_config is not None:
        body["backtest_config"] = canonical_config

    client = ctx.get_client()
    try:
        submission = client.post("/v1/backtests", json=body)
    except EntitlementError as e:
        # Quota wall (spec 03 R1): plan-limit 403s become the shared
        # handoff envelope — exact numbers from the API, the caller's own
        # limit and reset, no plan destination (D-12). Scope-shaped 403s
        # re-raise unchanged (re-auth is agent-recoverable).
        from ._handoff import maybe_quota_handoff

        handoff = maybe_quota_handoff(
            e,
            blocked_action="backtest_run",
            retry_call={"tool": "keel_backtest_run", "args": _retry_args(args)},
        )
        if handoff is not None:
            raise handoff from e
        raise
    except KeelError as e:
        # An inverted window (Q-1741) is the one refusal whose fix is
        # mechanical: the server names both dates, so the next action IS the
        # same call with them swapped. Every other refusal re-raises as sent.
        detail = e.detail or {}
        if detail.get("code") == "WINDOW_INVERTED":
            e.recovery_tool = "keel_backtest_run"
            e.recovery_tool_args = {
                "strategy_id": strategy_id,
                **({"commit_id": args["commit_id"]} if args.get("commit_id") else {}),
                **({"version": args["version"]} if version_ref is not None else {}),
                "start_date": detail.get("requested_end") or end_date,
                "end_date": detail.get("requested_start") or start_date,
            }
            e.suggestion = (
                "start_date must be before end_date. The dates look swapped — "
                "re-run with them the other way round."
            )
        elif detail.get("code") == "VERSION_NOT_FOUND":
            # SDK-envelope fields (spec 03 §2.1), never keel-api body fields:
            # what a ref may be, and the one call that lists the real ones.
            e.recovery_tool = "keel_strategy_history"
            e.recovery_tool_args = {"strategy_id": strategy_id}
            e.suggestion = (
                f"a sequence number{_sequence_range(client, strategy_id)}, a tag, HEAD, "
                "or a commit id of this strategy"
            )
        elif detail.get("code") == "VERSION_AND_COMMIT_ID":
            e.suggestion = "One of `version` or `commit_id`, not both."
        raise

    backtest_id = submission.get("id") or submission.get("backtest_id")
    # The window the platform moved, if it did (Q-1842) — relayed, never inferred.
    adjusted = window_adjusted(submission)
    if not backtest_id:
        raise KeelError(
            "API did not return a backtest id.",
            error_code="invalid_response",
            suggestion=f"{tool_ref('keel_connection_check')} checks the connection to Keel.",
        )

    hero_url = app_url_for("backtest", backtest_id, ctx)
    resource_uri = f"keel://backtest/{backtest_id}/results"
    ownership_fields = (
        ownership_envelope_fields(fetch_ownership_projection(ctx, strategy_id))
        if not args.get("skip_readiness", False)
        else {}
    )

    # Quota visibility (spec 04 R5 + mcp-conversion M1.1/M1.2). Two keys
    # ride the submit response:
    #
    #   `remaining` — the pre-existing sub-20% counters. Unchanged.
    #   `quota`     — one block per consumed unit that has left the `ok`
    #                 tier (50/75/90%/exhausted), projected to its unit,
    #                 limit, used, remaining and reset instant (D-12: the
    #                 server's `plan`/`higher_plans` never pass through).
    #
    # The API carries numbers and never copy (D-10); the SDK renders the
    # one factual sentence from them (`quota_notice`). Before this, the
    # only signal was a bare `{"backtest_runs": 4}` with no denominator
    # and no reset — an agent could not answer "how many are left?" until
    # it had almost none.
    remaining_quota = submission.get("remaining") if isinstance(submission, dict) else None
    quota_blocks = submission.get("quota") if isinstance(submission, dict) else None
    quota_fields = _quota_envelope_fields(quota_blocks)

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
        }
        if divergence_warning:
            # A local-only fact (the write-through pushed first): absent, not
            # null, when nothing was pushed — the listed schema omits it.
            extra["auto_pushed_commit_id"] = args.get("commit_id")
        extra.update(ownership_fields)
        if remaining_quota:
            extra["remaining"] = remaining_quota
        extra.update(quota_fields)
        # A queued run still renders: one line that names the strategy,
        # the window and the fact that it was not finished when checked.
        _attach_view(
            extra,
            {
                "id": backtest_id,
                "status": extra["status"],
                "strategy_id": strategy_id,
                "strategy_name": submission.get("strategy_name"),
                "sequence_number": submission.get("sequence_number"),
                "start_date": submission.get("start_date") or start_date,
                "end_date": submission.get("end_date") or end_date,
                # The overrides keel-api stored (the cost model, Q-2270);
                # the ones sent when an older keel-api echoes none.
                "backtest_config": (
                    submission.get("backtest_config")
                    if isinstance(submission.get("backtest_config"), dict)
                    else body.get("backtest_config")
                ),
            },
            size=_view_size(args),
            hero_url=hero_url,
            ctx=ctx,
            adjusted=adjusted,
        )
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra=extra,
        )

    budget_s = _poll_budget_s(ctx)
    final = _poll_until_terminal(client, backtest_id, budget_s=budget_s, progress=ctx.is_tty)
    final_status = (final.get("status") or "").lower()
    # The run's verdict, onto the request outcome slot ONLY (Q-1617 / Q-2293):
    # the audit row and mcp_tool_call_total then show a failed run as a
    # failure. Nothing reaches the agent -- the envelope below is unchanged,
    # and mcp-server keeps MCP `isError` off for a success envelope. Outside
    # a hosted request there is no slot and this is a no-op.
    if final_status in _RUN_FAILED_STATUSES:
        record_outcome(is_error=True)
    # The detail the view reads: whatever the poll returned, plus the
    # window this call asked for (a snapshot mid-flight may carry
    # neither date, and the receipt's window is not a guess — it is the
    # range this run was submitted over).
    view_detail: dict[str, Any] = {
        **final,
        "id": backtest_id,
        "strategy_id": strategy_id,
        # The RESOLVED window the platform ran, not the request (Q-1701):
        # an omitted start is filled by the coverage floor, and a fine clock
        # is clamped to its grid era. `None` is honest when neither is known.
        "start_date": final.get("start_date") or start_date,
        "end_date": final.get("end_date") or end_date,
    }
    size = _view_size(args)

    extra: dict[str, Any] = {
        "status_url": hero_url,
        "status": final_status or "unknown",
        "strategy_id": strategy_id,
    }
    extra.update(ownership_fields)
    if remaining_quota:
        extra["remaining"] = remaining_quota
    extra.update(quota_fields)
    if divergence_warning:
        extra["sync_note"] = divergence_warning
        extra["auto_pushed_commit_id"] = args.get("commit_id")

    if final_status not in _TERMINAL_STATUSES:
        # Timed out — return cleanly with status_url so the agent can
        # come back later. Do NOT raise: the run is still progressing.
        extra["info"] = (
            f"Backtest still running after {int(budget_s)}s. "
            "Poll status_url or call `keel_backtest_summarize` once complete."
        )
        # No curve on a run that has not finished: there is nothing to
        # draw, and the fetch would cost a round trip per timeout.
        _attach_view(extra, view_detail, size=size, hero_url=hero_url, ctx=ctx, adjusted=adjusted)
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
        _attach_view(extra, view_detail, size=size, hero_url=hero_url, ctx=ctx, adjusted=adjusted)
        return OutcomeResult(
            run_id=backtest_id,
            hero_url=hero_url,
            share_url=None,
            resource_uri=resource_uri,
            extra=extra,
        )

    # Success — populate summary_metrics + tearsheet URL.
    extra["tearsheet_url"] = hero_url
    # The full worker metrics dict, verbatim (the docstring's promised
    # escape hatch): the canonical list above is ordering/labeling only,
    # so new stored keys are never silently dropped from the envelope.
    if final.get("metrics"):
        extra["metrics_raw"] = final["metrics"]
        # What the run has to SAY beside its numbers (Q-1716): the
        # non-result label and the per-asset hygiene notes. The card
        # reads `env.notes` on every backtest-shaped result, and only
        # `keel_backtest_summarize` was attaching it.
        notes = notes_block(final["metrics"])
        if notes:
            extra["notes"] = notes
    if final.get("completed_at"):
        extra["completed_at"] = final["completed_at"]
    if final.get("execution_time") is not None:
        extra["execution_time_s"] = final["execution_time"]

    # The full-profile `deploy:` line (spec 02 §2.4 — the nudge is retired;
    # every profile carries the `good_result:` fact line instead).
    from ._nudge import deploy_line

    deploy = deploy_line(final, strategy_id=strategy_id, ctx=ctx)
    if deploy:
        extra["deploy"] = deploy

    # The card's chart AND the BTC hold reference, fetched ONCE on
    # completion from one `/curve` read and carried at the top level
    # (§2.1) — the run that just finished is the one a user most wants to
    # see drawn, and asking them to call `summarize` for the picture is the
    # extra turn this build removes.
    #
    # The three reads a finished run needs — `/curve` (+ reference), the
    # commit's source (the config line) and the recent-runs listing (the
    # compare hint) — are independent, so they run at once (Q-1870): in
    # sequence they sat between the worker finishing and the result.
    from ._backtest_view import parallel_map, run_config

    reference = None
    prefetched: dict[str, Any] = {}
    line = None
    if is_success(view_detail):
        (curve, reference), prefetched["config"], line = parallel_map(
            lambda read: read(),
            [
                lambda: fetch_curve_and_reference(client, backtest_id),
                lambda: run_config(client, view_detail),
                # The one conditional `next` line (spec 02 §2.4 #1(a)): the
                # compare hint when this run joins a set from the last hour.
                lambda: backtest_next(client, view_detail, None, strategy_id=strategy_id),
            ],
        )
        if curve:
            extra["curve"] = curve

    _attach_view(
        extra,
        view_detail,
        size=size,
        hero_url=hero_url,
        ctx=ctx,
        adjusted=adjusted,
        reference=reference,
        prefetched=prefetched,
    )

    # When the compare hint fires, the headroom data rides too (spec 03
    # §2.3, R-7): the 201's ladder when the server sent one, else ONE
    # advisory entitlements read.
    if line:
        extra["next"] = line
        if "quota" not in extra:
            extra.update(_quota_envelope_fields(_headroom_blocks(client)))

    return OutcomeResult(
        run_id=backtest_id,
        hero_url=hero_url,
        share_url=None,
        summary_metrics=_extract_summary_metrics(final.get("metrics")),
        resource_uri=resource_uri,
        extra=extra,
    )


#: spec 07 §5 (D13), the approved `start_date` text. The default window is
#: keel-api's (`utils/backtest_window.default_window_days`, spec 06); the
#: short-window history is the worker's minimum weights span (spec 07 §3).
#: The listed copy differs by one phrase: "trades" is a listed-banned token
#: (tests/test_policy_scan.py FORBIDDEN_TEXT_RE).
START_DATE_TEXT = (
    "Inclusive start date, YYYY-MM-DD. Optional; when omitted the run uses the default "
    "window for the strategy's timeframe (5min 60 days, 15min 90 days, coarser 5,000 "
    "bars, capped at available data). Any window works, including the last day or "
    "week: a short window's indicators are computed over enough earlier history "
    "automatically, so the strategy trades from the first bar."
)
LISTED_START_DATE_TEXT = START_DATE_TEXT.replace(
    "the strategy trades from the first bar", "the strategy can hold positions from the first bar"
)

#: spec 07 §5 (D13): the two warm-up lines of the tool description. Facts
#: about warm-up only (05 R-L4, Q-2012 / Q-2029): the text used to end
#: "re-run with an earlier `start_date`" — a further spend and a date the
#: user never named.
WARMUP_TEXT = (
    "Warm-up: indicators need history before they produce signals. Short windows get it "
    "automatically, but a strategy with long lookbacks, or a window near the start of "
    "our data, can still spend part of the window warming up. In `exposure`, a first "
    "position late in the window, or few bars held, may be warm-up (or the strategy "
    "choosing to stay flat). `notes` flags a window that was all warm-up."
)

#: The tool's input schema — the shared one; the listed profile serves
#: `LISTED_INPUT_SCHEMA` (R-8: one helper, `_base.listed_schema`).
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["strategy_id"],
    "properties": {
        "strategy_id": {
            "type": "string",
            "description": "The strategy to backtest: a `str_...` id, as `keel_strategy_search` returns.",
            "x-cli-positional": True,
        },
        "start_date": {
            "type": "string",
            "format": "date",
            "description": START_DATE_TEXT,
        },
        "end_date": {
            "type": "string",
            "format": "date",
            "description": (
                "End date, YYYY-MM-DD, exclusive: the run covers bars "
                "before it. Optional; defaults to today's UTC date — "
                "through the last complete UTC day. The default moves "
                "at 00:00 UTC, so runs share one window when each "
                "carries the same end_date."
            ),
        },
        "commit_id": {
            "type": "string",
            "description": (
                "Pin to a specific commit; defaults to strategy HEAD. One of `version` "
                "or `commit_id`: a call carrying both is refused. Historical commits are "
                "listed by `keel_strategy_history`."
            ),
        },
        # Spec 03 §2.1 / spec 01 §2.5's wording. keel-api resolves the ref
        # (sequence number first, experiment commits excluded) and refuses it
        # beside `commit_id`; the SDK sends an integer as its decimal string.
        # The listed schema omits `commit_id` (Q-2080): one selector there.
        "version": {
            "type": ["integer", "string"],
            "minLength": 1,
            "maxLength": 200,
            "description": (
                "Pins the version that runs: a sequence number, tag or commit id. "
                "Omitted: server HEAD. One of `version` or `commit_id`, never both."
            ),
        },
        "config": _sdk_backtest_config_schema(),
        "wait": {
            "type": "boolean",
            "default": True,
            "description": (
                "Block polling for completion — up to ~90s from "
                "non-interactive surfaces (MCP tools, --format json), "
                "up to ~300s in an interactive terminal (with a "
                "progress line; Ctrl-C to stop waiting). On timeout "
                "the result still returns, with `status_url` set for "
                "checking the run later."
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
        "skip_readiness": {
            "type": "boolean",
            "default": False,
            "description": (
                "Leave the strategy's readiness fields (next step, missing evidence) out "
                "of the result."
            ),
        },
        "present": present_param_schema(PRESENT_DEFAULT),
    },
}

#: No write-through on the file-free hosted server, and no margin cap or
#: deprecated capital alias on the listed schema (spec 01 §2.5, spec 03 §2.8).
#: The listed profile carries the two warm-up lines and the window approach
#: sentence (Q-2422) on `start_date` rather than in the tool description: the
#: listed descriptions are at their ratified 13,000-character total
#: (assemble.LISTED_TOTAL_CEILING_CHARS), and parameter copy is outside it.
#: Same words, one place over.
LISTED_INPUT_SCHEMA: dict[str, Any] = listed_schema(
    "keel_backtest_run",
    INPUT_SCHEMA,
    descriptions={
        "start_date": f"{LISTED_START_DATE_TEXT} {WINDOW_APPROACH_TEXT} {WARMUP_TEXT}",
        # One selector on the listed schema (`commit_id` is omitted there).
        "version": (
            "Pins the version that runs: a sequence number, tag or commit id. Omitted: server HEAD."
        ),
        # No terminal on the hosted server: no progress line, no Ctrl-C.
        "wait": (
            "Block polling for completion, up to ~90s. On timeout the result still "
            "returns, with `status_url` set for checking the run later."
        ),
    },
)


BACKTEST_RUN = register(
    OutcomeTool(
        name="keel_backtest_run",
        required_action="backtest.create",
        cli_path=("backtest", "run"),
        toolset="backtest",
        # grounded-in: system/backtest_costs.md:9-18 ("Backtest cost defaults"
        # — 4.5 bps taker + 4.5 bps slippage, ~9 bps per order);
        # the platform's own window floor (Q-1701: no constant here);
        # context-architecture-design §1.1(1) (apply platform defaults
        # without asking unless the user asks or you have a stated reason).
        description=(
            "Submit a NEW backtest run of a strategy — a finished run's result is "
            "`keel_backtest_summarize`, a running one's wait `keel_backtest_watch`. Runs "
            "server HEAD unless `version` pins one (a sequence number, tag or commit id). "
            "Defaults: the window is the platform's default for the strategy's "
            "timeframe (see `start_date`), to today UTC "
            "(`end_date` exclusive) — and a realistic cost model (starting capital, ~4.5 "
            "bps fees + ~4.5 bps slippage); the result names the window, any start move, "
            "and the declarations and pipeline that ran. " + WINDOW_APPROACH_TEXT + " "
            "Returns `run_id`; with "
            "`wait=true` (the default) also "
            "`summary_metrics` and the tearsheet link, or `status_url` on a polling "
            "timeout. Each call spends one run of quota; the result "
            "carries the count left when low, and `keel_plan_usage` reports it before a "
            "set of runs.\n"
            "\n" + WARMUP_TEXT + "\n"
            "\n"
            "Write-through on the local server and the CLI (server HEAD is the source of "
            "truth): a checkout's unpushed edits are pushed first (a generated message, or "
            "`push_message`) and the run pins to that commit, so the code tested is the "
            "code held; `auto_push=false` opts out and raises `local_ahead`; a true "
            "conflict (local edited and server moved) stops with recovery options and "
            "nothing is overwritten."
        ),
        # Listed-profile copy (agent-surface-cleanup spec 01 §2.5, R-4): the same
        # text minus one surface fact — the hosted server is file-free, so the write-through paragraph (a
        # full-profile fact) is not a fact about the tool that reader holds.
        listed_description=(
            "Submit a NEW backtest run of a strategy — a finished run's result is "
            "`keel_backtest_summarize`, a running one's wait `keel_backtest_watch`. Runs "
            "server HEAD unless `version` pins one. "
            "Defaults: the window is the platform's default for the strategy's "
            "timeframe, to today UTC "
            "(`end_date` exclusive) — and a realistic cost model (starting capital, ~4.5 "
            "bps fees + ~4.5 bps slippage); the result names the window, any start move, "
            "and the declarations and pipeline that ran. Returns `run_id`; with "
            "`wait=true` (the default) also "
            "`summary_metrics` and the tearsheet link, or `status_url` on a polling "
            "timeout. Each call spends one run of quota; `keel_plan_usage` reports what "
            "is left."
        ),
        input_schema=INPUT_SCHEMA,
        listed_input_schema=LISTED_INPUT_SCHEMA,
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
