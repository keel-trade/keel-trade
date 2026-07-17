"""`keel_live_deploy` — preview + deploy a strategy to a live account.

Per spec §4 #12: the canonical destructive tool with confirmation.

Two-call dance:
  1. First call with `preview=true` (default) → `POST /v1/live/preview`
     returns derived schedule + estimated slippage/fees + a
     short-lived local `confirmation_token`. The agent presents this to
     the user.
  2. Second call with `preview=false` + the same `confirmation_token`
     validates the local preview record and then calls `POST /v1/live`,
     returning the deployment id and authenticated dashboard URL.

Do NOT use without first calling `keel_accounts_list` to pick
`account_id`. Do NOT use to update an already-deployed strategy's
config — use `keel_live_control`.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from keel.errors import EntitlementError, KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _live_wall_handoff(e: EntitlementError, *, blocked_action: str, strategy_id: str, ctx):
    """Map a live-path 403 to the shared handoff envelope (spec 03 R1).

    Quota shape (e.g. live_strategies_max cap) → billing handoff with the
    API's exact numbers. Scope shape (token lacks `runner.*`) → live-consent
    handoff; the human can also act directly on the strategy overview
    (their web session doesn't depend on this token's scopes).
    """
    from ._handoff import live_scope_handoff, maybe_quota_handoff

    retry_call = {
        "tool": "keel_live_deploy",
        "args": {"strategy_id": strategy_id, "preview": True},
    }
    handoff = maybe_quota_handoff(e, blocked_action=blocked_action, retry_call=retry_call)
    if handoff is not None:
        return handoff
    return live_scope_handoff(
        e,
        blocked_action=blocked_action,
        action_url=f"{ctx.app_url}/strategies/{strategy_id}",
        retry_call=retry_call,
    )


PREVIEW_TTL = timedelta(minutes=10)
PREVIEW_FILE = "live-previews.yaml"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _preview_store_path() -> Path:
    return Path.home() / ".keel" / PREVIEW_FILE


def _load_preview_store() -> dict[str, Any]:
    path = _preview_store_path()
    if not path.exists():
        return {"previews": {}}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"previews": {}}
    if not isinstance(data, dict):
        return {"previews": {}}
    previews = data.get("previews")
    if not isinstance(previews, dict):
        data["previews"] = {}
    return data


def _write_preview_store(data: dict[str, Any]) -> None:
    path = _preview_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def _cleanup_expired_previews(data: dict[str, Any], *, now: datetime | None = None) -> None:
    now = now or _utcnow()
    previews = data.setdefault("previews", {})
    if not isinstance(previews, dict):
        data["previews"] = {}
        return
    expired = []
    for token, record in previews.items():
        if not isinstance(record, dict):
            expired.append(token)
            continue
        expires_at = _parse_iso(record.get("expires_at"))
        if expires_at is None or expires_at <= now:
            expired.append(token)
    for token in expired:
        previews.pop(token, None)


def _schedule_value(schedule: Any) -> str | None:
    if schedule is None:
        return None
    schedule_s = str(schedule).strip()
    return schedule_s or None


def _store_preview(
    *,
    strategy_id: str,
    account_id: str,
    schedule: str | None,
    preview_data: Any,
) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(24)
    expires_at = _utcnow() + PREVIEW_TTL
    data = _load_preview_store()
    _cleanup_expired_previews(data)
    data.setdefault("previews", {})[token] = {
        "strategy_id": strategy_id,
        "account_id": account_id,
        "schedule": schedule,
        "preview": preview_data,
        "created_at": _utcnow().isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }
    _write_preview_store(data)
    return token, expires_at


def _consume_preview(
    *,
    token: str | None,
    strategy_id: str,
    account_id: str,
    schedule: str | None,
    consume: bool,
) -> dict[str, Any]:
    if not token:
        raise KeelError(
            "Actual live deploy requires a confirmation_token from a preview.",
            error_code="missing_confirmation_token",
            exit_code=2,
            suggestion=(
                "Call `keel_live_deploy` with `preview=True`, show the preview "
                "to the user, then call again with `preview=False` and the "
                "returned `confirmation_token`."
            ),
        )

    data = _load_preview_store()
    previews = data.get("previews") if isinstance(data.get("previews"), dict) else {}
    record = previews.get(token)
    if not isinstance(record, dict):
        _cleanup_expired_previews(data)
        _write_preview_store(data)
        raise KeelError(
            "confirmation_token is unknown or expired.",
            error_code="confirmation_token_invalid",
            exit_code=6,
            suggestion="Run a fresh live deploy preview and use the new confirmation_token.",
        )

    expires_at = _parse_iso(record.get("expires_at"))
    if expires_at is None or expires_at <= _utcnow():
        previews.pop(token, None)
        _cleanup_expired_previews(data)
        _write_preview_store(data)
        raise KeelError(
            "confirmation_token has expired.",
            error_code="confirmation_token_expired",
            exit_code=6,
            suggestion="Run a fresh live deploy preview and confirm it before deploying.",
        )

    _cleanup_expired_previews(data)

    expected = {
        "strategy_id": strategy_id,
        "account_id": account_id,
        "schedule": schedule,
    }
    actual = {
        "strategy_id": record.get("strategy_id"),
        "account_id": record.get("account_id"),
        "schedule": record.get("schedule"),
    }
    if actual != expected:
        raise KeelError(
            "confirmation_token does not match this live deploy request.",
            error_code="confirmation_token_mismatch",
            exit_code=6,
            suggestion=(
                "Use the exact strategy_id, account_id, and schedule from the "
                "preview, or run a fresh preview for this deploy request."
            ),
        )

    if consume:
        previews.pop(token, None)
        _write_preview_store(data)
    else:
        _write_preview_store(data)
    return record


def _delete_preview(token: str | None) -> None:
    if not token:
        return
    data = _load_preview_store()
    previews = data.get("previews") if isinstance(data.get("previews"), dict) else {}
    previews.pop(token, None)
    data["previews"] = previews
    _write_preview_store(data)


def _poll_intent_status(intent_token: str, *, strategy_id: str, ctx: ToolContext) -> OutcomeResult:
    """Pure server-side status read for a deploy-intent handoff (spec 03 R6).

    This is the executable form of the handoff envelope's
    ``resume.verify_call``: no preview call, no write-through guard, no
    account requirement — it answers exactly one question ("did the human
    finish the handoff?") from server state via
    ``POST /v1/live/deploy-intents/status``. Nothing relies on a browser
    session: completion is DERIVED server-side from the deployment the
    wizard created, so the agent observes it without the user returning
    to the tab.
    """
    from keel.errors import EntitlementError, UsageError

    client = ctx.get_client()
    try:
        resp = client.post("/v1/live/deploy-intents/status", json={"intent_token": intent_token})
    except EntitlementError as e:
        # The status endpoint checks nothing beyond auth + token↔org
        # binding, so an EntitlementError here IS the wrong-org 403. The
        # class-default "re-login with live scope" remediation would
        # mislead — name the actual fix (R7).
        raise KeelError(
            str(e),
            error_code="deploy_intent_wrong_org",
            exit_code=6,
            suggestion=(
                "This session is authenticated to a different Keel org than "
                "the one that minted the deploy link. Mint a fresh link from "
                "THIS session (`keel_live_deploy` with preview=True returns "
                "`handoff_url` + `deploy_intent.intent_token`), or re-login "
                "as the org that owns the strategy and retry."
            ),
        ) from e
    except UsageError as e:
        # 400: tampered / truncated / not-a-deploy-link token (server
        # remediation text rides the message).
        raise KeelError(
            str(e),
            error_code="deploy_intent_invalid",
            exit_code=7,
            suggestion=(
                "The intent token failed verification (truncated, altered, or "
                "not a deploy link). Mint a fresh one: `keel_live_deploy` with "
                "preview=True returns `handoff_url` and "
                "`deploy_intent.intent_token`."
            ),
        ) from e

    status = resp.get("status") if isinstance(resp, dict) else None
    if status not in {"pending", "completed", "expired"}:
        # One exact contract; an unknown shape is an error, never guessed at.
        raise KeelError(
            f"Unexpected deploy-intent status response: {resp!r}",
            error_code="deploy_intent_status_unexpected",
            exit_code=1,
            retryable=True,
            suggestion=(
                "The server returned an unknown handoff status shape — retry "
                "once; if it persists the API and SDK versions have drifted "
                "(run `keel_doctor`)."
            ),
        )

    handoff_state: dict[str, Any] = {
        "status": status,
        "intent_id": resp.get("intent_id"),
        "strategy_id": resp.get("strategy_id") or strategy_id,
        "expires_at": resp.get("expires_at"),
    }
    extra: dict[str, Any] = {"handoff_state": handoff_state}

    if status == "completed":
        deployment_id = resp.get("deployment_id")
        handoff_state["deployment_id"] = deployment_id
        handoff_state["deployment_status"] = resp.get("deployment_status")
        extra["note"] = (
            "The human completed the handoff — the strategy is deployed. "
            "No browser return needed; monitor it from here."
        )
        extra["next_action"] = {
            "tool": "keel_live_monitor",
            "args": {"deployment_id": deployment_id} if deployment_id else {},
            "reason": "Inspect the running deployment (status, evaluations, orders).",
        }
        hero = f"{ctx.app_url}/live/{deployment_id}" if deployment_id else f"{ctx.app_url}/live"
        return OutcomeResult(run_id=deployment_id, hero_url=hero, share_url=None, extra=extra)

    if status == "expired":
        handoff_state["remediation"] = resp.get("remediation") or (
            "This deploy link has expired (links live for up to 1 hour). "
            "Mint a fresh one and hand it to the user again."
        )
        extra["next_action"] = {
            "tool": "keel_live_deploy",
            "args": {"strategy_id": handoff_state["strategy_id"], "preview": True},
            "reason": (
                "The link lapsed unused (≤1h lifetime). A fresh preview mints "
                "a new handoff_url + intent token to hand to the user."
            ),
        }
        return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)

    # pending — the link is live and the human hasn't finished yet.
    extra["note"] = (
        "The human hasn't completed the handoff yet (the link is still "
        "live). Poll this same call again in a bit — or check "
        "`keel_accounts_list` to see whether an account got linked mid-flow."
    )
    extra["next_action"] = {
        "tool": "keel_live_deploy",
        "args": {"strategy_id": handoff_state["strategy_id"], "intent_token": intent_token},
        "reason": "Re-poll the handoff status; it flips to 'completed' when the human finishes.",
    }
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)


def _org_has_no_accounts(ctx: ToolContext) -> bool:
    """True only when the org's account list is verifiably empty.

    Any fetch failure returns False — the caller then raises its normal
    error; we never claim "no linked account" on a guess.
    """
    try:
        result = ctx.get_client().get("/v1/accounts", limit=1)
    except Exception:  # noqa: BLE001 — check is advisory; caller keeps its own error
        return False
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return len(data) == 0
    if isinstance(result, list):
        return len(result) == 0
    return False


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    account_id = (args.get("account_id") or "").strip()
    preview = bool(args.get("preview", True))
    schedule = _schedule_value(args.get("schedule"))
    confirmation_token = (args.get("confirmation_token") or "").strip() or None
    intent_token = (args.get("intent_token") or "").strip() or None

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id` argument.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass strategy_id positional (CLI) or {strategy_id: ...} (MCP).",
        )

    # ── Handoff status poll (spec 03 R6) ──────────────────────────────
    # `intent_token` + preview phase = the resume path of a handoff
    # envelope: a pure status read. Runs BEFORE the account check —
    # the whole point of the handoff is that no account is linked yet.
    if preview and intent_token:
        return _poll_intent_status(intent_token, strategy_id=strategy_id, ctx=ctx)

    if not account_id:
        # No account_id given. If the org genuinely has NO linked account,
        # this is not an agent usage error — it's the human account-linking
        # wall (spec 03 R1): emit the shared handoff envelope instead of
        # telling the agent to pick from an empty list. If accounts exist
        # (or the check itself fails), the classic usage error stands.
        if _org_has_no_accounts(ctx):
            from ._handoff import unlinked_account_handoff

            raise unlinked_account_handoff(
                blocked_action="live_deploy",
                strategy_id=strategy_id,
                ctx=ctx,
            )
        raise KeelError(
            "Missing required `account_id` argument.",
            error_code="missing_account_id",
            exit_code=2,
            suggestion="Call `keel_accounts_list` first to pick an account_id.",
        )

    # ── Write-through guard (spec 08 R2) ──────────────────────────────
    # Deploys reference SERVER HEAD. A deploy must never reference a
    # strategy whose local working copy is silently ahead. Preview phase:
    # local-ahead → push first by default (write-through), then preview
    # the pushed state; `auto_push=False` opts out (raises `local_ahead`).
    # A true conflict (server moved too) always stops — never forces.
    from ._sync_guard import write_through_guard

    sync_note: str | None = None
    if preview:
        push_result = write_through_guard(
            args,
            strategy_id=strategy_id,
            action="live-deploy preview",
            default_message="Auto-push before live deploy",
        )
        if push_result is not None and push_result.get("status") == "pushed":
            pushed_seq = push_result.get("sequence")
            pushed_commit = push_result.get("commit_id")
            commit_str = f", commit_id={pushed_commit}" if pushed_commit else ""
            sync_note = (
                f"Local was ahead — auto-pushed (sequence={pushed_seq}"
                f"{commit_str}) so the preview reflects your local edits. "
                "The deploy will run this new server HEAD."
            )

    # Second lock — local arming. Preview mode still works without
    # arming (you can inspect what would happen), but the actual deploy
    # (preview=False) requires a matching preview token and an armed
    # account.
    if not preview:
        _consume_preview(
            token=confirmation_token,
            strategy_id=strategy_id,
            account_id=account_id,
            schedule=schedule,
            consume=False,
        )
        # Check-only: if local moved AFTER the preview, pushing now would
        # deploy something the user never previewed. Stop and require a
        # fresh preview instead (which write-throughs by default).
        write_through_guard(
            args,
            strategy_id=strategy_id,
            action="live deploy",
            default_message="Auto-push before live deploy",
            check_only=True,
            check_only_suggestion=(
                "Local edits appeared after the preview. Re-run "
                "`keel_live_deploy` with `preview=True` — it pushes local "
                "edits by default and issues a fresh confirmation_token "
                "for what will actually deploy."
            ),
        )
        from keel.permissions import assert_armed_for_account

        assert_armed_for_account(account_id)

    client = ctx.get_client()

    if preview:
        body: dict[str, Any] = {"strategy_id": strategy_id}
        try:
            preview_data = client.post("/v1/live/preview", json=body)
        except EntitlementError as e:
            raise _live_wall_handoff(
                e, blocked_action="live_deploy", strategy_id=strategy_id, ctx=ctx
            ) from e
        token, expires_at = _store_preview(
            strategy_id=strategy_id,
            account_id=account_id,
            schedule=schedule,
            preview_data=preview_data,
        )
        extra: dict[str, Any] = {}
        if sync_note:
            extra["sync_note"] = sync_note

        # Deploy-intent deep link (spec 03 R2): the preview additionally
        # returns `handoff_url` — a signed, short-lived link into the
        # standalone handoff flow, prefilled with server-computed sizing.
        # Best-effort (older APIs / missing scope → omitted); NEVER minted
        # on the listed directory profile (mint_deploy_intent gates it).
        from ._handoff import mint_deploy_intent

        intent = mint_deploy_intent(ctx, strategy_id)
        if intent:
            extra["handoff_url"] = intent["handoff_url"]
            extra["deploy_intent"] = {
                "intent_token": intent.get("intent_token"),
                "expires_at": intent.get("expires_at"),
                "suggested_config": intent.get("suggested_config"),
            }
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            extra={
                **extra,
                "preview": {
                    "strategy_id": strategy_id,
                    "account_id": account_id,
                    "schedule": schedule,
                    "derived_schedule": (preview_data or {}).get("derived_schedule")
                    if isinstance(preview_data, dict)
                    else None,
                    "weights": (preview_data or {}).get("weights")
                    if isinstance(preview_data, dict)
                    else None,
                    "est_slippage": (preview_data or {}).get("est_slippage")
                    if isinstance(preview_data, dict)
                    else None,
                    "est_fees": (preview_data or {}).get("est_fees")
                    if isinstance(preview_data, dict)
                    else None,
                    "raw": preview_data,
                },
                "confirmation_token": token,
                "confirmation_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "next_action": {
                    "tool": "keel_live_deploy",
                    "args": {
                        "strategy_id": strategy_id,
                        "account_id": account_id,
                        "preview": False,
                        "confirmation_token": token,
                        **({"schedule": schedule} if schedule else {}),
                    },
                    "reason": (
                        "Call again with preview=false and this confirmation_token "
                        "to actually deploy after user confirms."
                    ),
                },
            },
        )

    # Actual deploy.
    body = {"strategy_id": strategy_id, "account_id": account_id}
    if schedule:
        body["schedule"] = schedule
    if intent_token:
        # Telemetry-only handoff attribution (spec 03 R2/R6): a valid token
        # bound to this exact strategy+org makes the server emit
        # `handoff_completed`; an invalid/expired/mismatched token is logged
        # server-side and never fails the deploy.
        body["intent_token"] = intent_token
    try:
        result = client.post("/v1/live", json=body)
    except EntitlementError as e:
        raise _live_wall_handoff(
            e, blocked_action="live_deploy", strategy_id=strategy_id, ctx=ctx
        ) from e
    except NotFoundError as e:
        # keel-api: NotFoundError("account", id) → detail "account not
        # found: <id>". The account either was never linked or is gone —
        # a human wall, not a retryable agent error. Strategy-not-found
        # stays a plain NotFoundError.
        if "account not found" in str(e).lower():
            from ._handoff import unlinked_account_handoff

            raise unlinked_account_handoff(
                blocked_action="live_deploy",
                strategy_id=strategy_id,
                ctx=ctx,
                detail=str(e),
            ) from e
        raise
    _delete_preview(confirmation_token)
    deployment_id = None
    if isinstance(result, dict):
        deployment_id = result.get("deployment_id") or result.get("id")

    hero_url = f"{ctx.app_url}/live/{deployment_id}" if deployment_id else f"{ctx.app_url}/live"
    extra: dict[str, Any] = {"deployment": result}
    # Quota visibility (spec 04 R5): the deploy response carries `remaining`
    # counters (e.g. {"live_slots": 0}) ONLY when remaining capacity is
    # strictly below 20% of the plan cap. Surface it top-level so agents can
    # plan ahead of the wall — numbers only, no upsell language.
    if isinstance(result, dict) and result.get("remaining"):
        extra["remaining"] = result["remaining"]
    return OutcomeResult(
        run_id=deployment_id,
        hero_url=hero_url,
        share_url=None,
        extra=extra,
    )


LIVE_DEPLOY = register(
    OutcomeTool(
        name="keel_live_deploy",
        required_action="runner.create",
        cli_path=("live", "deploy"),
        toolset="live-write",
        description=(
            "Deploy a strategy to a live Hyperliquid account. THIS WILL PLACE REAL ORDERS. "
            "First call returns a preview (derived schedule, estimated slippage/fees) + a "
            "short-lived local confirmation_token. Second call with `preview=false` and "
            "that confirmation_token actually deploys. The host also gates the destructive "
            "action via `destructiveHint=true`. "
            "Write-through (server HEAD is the source of truth): if the strategy is "
            "checked out locally with unpushed edits, the preview pushes them first "
            "(set `auto_push=False` to opt out) so the deploy never references a "
            "strategy whose local copy is silently ahead; true conflicts stop with "
            "recovery options. "
            "Do NOT use without first calling `keel_accounts_list` to pick `account_id`. "
            "Do NOT use to update an already-deployed strategy's config — use "
            "`keel_live_control`. "
            "Handoff resume: calling with `intent_token` (from a handoff envelope's "
            "`resume.token`) and preview=true is a pure status poll — it returns "
            "`handoff_state` (pending|completed|expired) so the agent observes the "
            "human completing the handoff flow without a browser return."
        ),
        input_schema={
            "type": "object",
            # `account_id` is enforced by the HANDLER, not the schema
            # (spec 03 R1): an org with zero linked accounts must receive
            # the account-linking handoff envelope — a schema-required
            # arg would stop that call at the adapter pre-flight with a
            # generic usage error and dead-end the path to live.
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy to deploy. From `keel_strategy_search`.",
                },
                "account_id": {
                    "type": "string",
                    "description": (
                        "Hyperliquid trading account. From `keel_accounts_list`. "
                        "Required to deploy — omitting it when no account is "
                        "linked yet returns the account-linking handoff "
                        "envelope instead of an error."
                    ),
                },
                "preview": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), return preview + confirmation_token only. "
                        "If false, require `confirmation_token` and actually deploy."
                    ),
                },
                "confirmation_token": {
                    "type": "string",
                    "description": (
                        "Short-lived token returned by the preview call. Required "
                        "when `preview=false`; must match strategy_id, account_id, "
                        "and schedule."
                    ),
                },
                "schedule": {
                    "type": "string",
                    "description": ("Optional cron expression overriding the strategy's schedule."),
                },
                "intent_token": {
                    "type": "string",
                    "description": (
                        "Deploy-intent token from a handoff envelope's "
                        "`resume.token` (or an earlier preview's "
                        "`deploy_intent.intent_token`). With `preview=true` "
                        "(default) the call becomes a pure status poll: it "
                        "returns `handoff_state` (pending|completed|expired) "
                        "for that handoff link instead of running a preview — "
                        "'completed' includes the deployment_id, so the agent "
                        "observes the human finishing the flow without any "
                        "browser return. With `preview=false` it rides along "
                        "the actual deploy for handoff attribution (telemetry "
                        "only; never fails the deploy)."
                    ),
                },
                "auto_push": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Write-through default (true): if the strategy is "
                        "checked out locally with unpushed edits, the preview "
                        "pushes them first so the deploy references what you "
                        "actually have. Set false to opt out — local-ahead "
                        "then raises `local_ahead`. Conflicts (server moved "
                        "too) always stop regardless."
                    ),
                },
                "push_message": {
                    "type": "string",
                    "description": (
                        "Commit message when the preview's write-through guard "
                        "pushes local edits. Defaults to 'Auto-push before "
                        "live deploy'."
                    ),
                },
            },
        },
        annotations={
            "title": "Deploy Strategy Live",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
