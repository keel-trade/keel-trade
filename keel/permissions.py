"""Local live-trading arming layer.

Spec §7 "Live trading arming — the second lock" (lines 855-902).

The OAuth `live` scope says "I trust this client to make live calls"
at the server level. Arming says "live calls are OK right now" at
the machine/project level. Two layers, both opt-in.

Files searched (first match wins):
  1. `<cwd>/.keel/permissions.yaml` — per-project arming
  2. `<cwd>/keel.md` (legacy) — looked at but not parsed for arming
  3. `~/.keel/permissions.yaml` — per-machine arming

`expires` is REQUIRED — files without an expiry value disarm. Default
TTL when writing via `keel arm live` is 7 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_TTL = timedelta(days=7)
ONCE_TTL = timedelta(seconds=60)
EXPIRY_WARNING_WINDOW = timedelta(hours=24)


def home_permissions_path() -> Path:
    return Path.home() / ".keel" / "permissions.yaml"


def project_permissions_path(cwd: Path | None = None) -> Path:
    cwd = cwd or Path.cwd()
    return cwd / ".keel" / "permissions.yaml"


@dataclass
class ArmStatus:
    """Resolved arming state for the current process."""

    armed: bool
    expires_at: datetime | None
    accounts: list[str]
    source: str  # "project" | "home" | "none"
    warning: str | None = None
    reason: str | None = None  # set when armed=False; explains why

    def expiring_soon(self) -> bool:
        if not (self.armed and self.expires_at):
            return False
        return self.expires_at - _utcnow() <= EXPIRY_WARNING_WINDOW


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    try:
        # Accept "Z" suffix and bare ISO8601
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _load_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_arm_status(cwd: Path | None = None) -> ArmStatus:
    """Resolve the active arming state from project + home files.

    Project file overrides home. `expires` is mandatory; a missing or
    past expiry returns `armed=False` with a clear reason.
    """
    project = _load_file(project_permissions_path(cwd))
    home = _load_file(home_permissions_path())

    chosen, source = (None, "none")
    if project and "live_trading" in project:
        chosen, source = project["live_trading"], "project"
    elif home and "live_trading" in home:
        chosen, source = home["live_trading"], "home"

    if not isinstance(chosen, dict):
        return ArmStatus(
            armed=False,
            expires_at=None,
            accounts=[],
            source=source,
            reason="No permissions file found — run `keel arm live set --account <id>` to arm.",
        )

    armed_flag = bool(chosen.get("armed"))
    expires_raw = chosen.get("expires")
    expires_at = _parse_iso(expires_raw) if isinstance(expires_raw, str) else None

    if not armed_flag:
        return ArmStatus(
            armed=False,
            expires_at=expires_at,
            accounts=[],
            source=source,
            reason="`armed: false` in permissions file.",
        )
    if expires_at is None:
        return ArmStatus(
            armed=False,
            expires_at=None,
            accounts=[],
            source=source,
            reason="Permissions file missing required `expires` timestamp.",
        )
    if expires_at <= _utcnow():
        return ArmStatus(
            armed=False,
            expires_at=expires_at,
            accounts=[],
            source=source,
            reason=(
                f"Arming expired at {expires_at.isoformat()}. Renew with "
                "`keel arm live set --account <id> --renew`."
            ),
        )

    accounts = chosen.get("accounts") or []
    if not isinstance(accounts, list):
        accounts = []
    status = ArmStatus(
        armed=True,
        expires_at=expires_at,
        accounts=[str(a) for a in accounts],
        source=source,
    )
    if status.expiring_soon():
        status.warning = (
            f"live_trading_expiring_soon: arming expires at {expires_at.isoformat()}. "
            "Run `keel arm live set --account <id> --renew` to extend."
        )
    return status


def assert_armed_for_account(account_id: str | None, cwd: Path | None = None) -> ArmStatus:
    """Raise a structured error when arming is missing/expired/scope-too-narrow.

    Used by `keel_live_deploy` and `keel_live_control` outcome handlers
    before they hit the API.
    """
    from keel.errors import KeelError

    status = read_arm_status(cwd)
    if not status.armed:
        raise KeelError(
            "Live trading is disarmed on this machine.",
            error_code="live_trading_disarmed",
            exit_code=6,
            suggestion=status.reason
            or "Run `keel arm live set --account <account_id>` to arm for 7 days.",
        )
    if account_id and status.accounts and account_id not in status.accounts:
        raise KeelError(
            f"Live trading is armed but not for account {account_id!r}.",
            error_code="live_trading_disarmed",
            exit_code=6,
            suggestion=(
                f"Add the account to the armed list with "
                f"`keel arm live set --account {account_id}` (or add `--once`)."
            ),
        )
    return status


def write_arm(
    *,
    account_id: str | None = None,
    ttl: timedelta | None = None,
    once: bool = False,
    renew: bool = False,
    path: Path | None = None,
) -> ArmStatus:
    """Write or refresh the home permissions file.

    - `once=True` arms for `ONCE_TTL` (60s) and lists exactly one account.
    - `renew=True` resets the TTL on the existing armed state without
      changing accounts.
    - Otherwise writes a fresh armed record with `ttl` (default 7 days).
    """
    target = path or home_permissions_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_file(target) or {}
    block = existing.get("live_trading") if isinstance(existing.get("live_trading"), dict) else {}

    now = _utcnow()
    if once:
        ttl = ONCE_TTL
    elif renew and not ttl:
        ttl = DEFAULT_TTL
    elif not ttl:
        ttl = DEFAULT_TTL

    accounts = list(block.get("accounts") or [])
    if account_id:
        if once:
            accounts = [account_id]
        elif account_id not in accounts:
            accounts.append(account_id)

    block.update(
        {
            "armed": True,
            "accounts": accounts,
            "expires": (now + ttl).isoformat().replace("+00:00", "Z"),
        }
    )

    existing["live_trading"] = block
    target.write_text(yaml.safe_dump(existing, sort_keys=True), encoding="utf-8")
    return read_arm_status()


def disarm(path: Path | None = None, *, emergency: bool = False) -> ArmStatus:
    target = path or home_permissions_path()
    existing = _load_file(target) or {}
    if "live_trading" in existing and isinstance(existing["live_trading"], dict):
        existing["live_trading"]["armed"] = False
        if emergency:
            existing["live_trading"]["accounts"] = []
        target.write_text(yaml.safe_dump(existing, sort_keys=True), encoding="utf-8")
    return read_arm_status()
