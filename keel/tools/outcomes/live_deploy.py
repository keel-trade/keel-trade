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

from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
from typing import Any

import yaml

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


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


def _cleanup_expired_previews(
    data: dict[str, Any], *, now: datetime | None = None
) -> None:
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


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    account_id = (args.get("account_id") or "").strip()
    preview = bool(args.get("preview", True))
    schedule = _schedule_value(args.get("schedule"))
    confirmation_token = (args.get("confirmation_token") or "").strip() or None

    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id` argument.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass strategy_id positional (CLI) or {strategy_id: ...} (MCP).",
        )
    if not account_id:
        raise KeelError(
            "Missing required `account_id` argument.",
            error_code="missing_account_id",
            exit_code=2,
            suggestion="Call `keel_accounts_list` first to pick an account_id.",
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
        from keel.permissions import assert_armed_for_account

        assert_armed_for_account(account_id)

    client = ctx.get_client()

    if preview:
        body: dict[str, Any] = {"strategy_id": strategy_id}
        preview_data = client.post("/v1/live/preview", json=body)
        token, expires_at = _store_preview(
            strategy_id=strategy_id,
            account_id=account_id,
            schedule=schedule,
            preview_data=preview_data,
        )
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            extra={
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
                "confirmation_expires_at": expires_at.isoformat().replace(
                    "+00:00", "Z"
                ),
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
    result = client.post("/v1/live", json=body)
    _delete_preview(confirmation_token)
    deployment_id = None
    if isinstance(result, dict):
        deployment_id = result.get("deployment_id") or result.get("id")

    hero_url = (
        f"{ctx.app_url}/live/{deployment_id}" if deployment_id else f"{ctx.app_url}/live"
    )
    return OutcomeResult(
        run_id=deployment_id,
        hero_url=hero_url,
        share_url=None,
        extra={"deployment": result},
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
            "Do NOT use without first calling `keel_accounts_list` to pick `account_id`. "
            "Do NOT use to update an already-deployed strategy's config — use "
            "`keel_live_control`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id", "account_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy to deploy. From `keel_strategy_search`.",
                },
                "account_id": {
                    "type": "string",
                    "description": "Hyperliquid trading account. From `keel_accounts_list`.",
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
                    "description": (
                        "Optional cron expression overriding the strategy's schedule."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
