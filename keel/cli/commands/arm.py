"""`keel arm` — manage the local live-trading arming layer.

Spec §7 "Live trading arming — the second lock" (lines 855-902 + 873-883
for the command surface).
"""

from __future__ import annotations

from datetime import timedelta

import click

from keel.cli.main import _get_format
from keel.output import emit


_TTL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_ttl(s: str | None) -> timedelta | None:
    """Parse strings like `30m`, `24h`, `7d` into a timedelta."""
    if not s:
        return None
    s = s.strip().lower()
    if not s:
        return None
    unit = s[-1]
    if unit not in _TTL_UNITS or not s[:-1].isdigit():
        raise click.BadParameter(
            f"Invalid TTL {s!r}. Use forms like 30m, 24h, 7d."
        )
    seconds = int(s[:-1]) * _TTL_UNITS[unit]
    return timedelta(seconds=seconds)


@click.group()
def arm() -> None:
    """Local live-trading arming layer (per machine / per project)."""


@arm.command("status")
@click.pass_context
def arm_status(ctx: click.Context) -> None:
    """Show the current arming state — file source, accounts, expiry, time left."""
    from keel.permissions import read_arm_status

    status = read_arm_status()
    expires_iso = status.expires_at.isoformat() if status.expires_at else None
    payload = {
        "armed": status.armed,
        "source": status.source,
        "accounts": status.accounts,
        "expires_at": expires_iso,
        "expiring_soon": status.expiring_soon(),
        "warning": status.warning,
        "reason": status.reason,
    }
    emit(payload, _get_format(ctx))


@arm.group("live")
def arm_live() -> None:
    """Arm / renew / one-shot the live-trading layer."""


@arm_live.command("set")
@click.option("--account", "account_id", required=True, help="Account id to arm for.")
@click.option("--ttl", "ttl_str", help="Duration string (e.g. 24h, 7d). Default 7 days.")
@click.option("--once", is_flag=True, help="Arm for one call (60-second window) only.")
@click.option("--renew", is_flag=True, help="Refresh the TTL on the existing armed record.")
@click.pass_context
def arm_live_set(
    ctx: click.Context,
    account_id: str,
    ttl_str: str | None,
    once: bool,
    renew: bool,
) -> None:
    """Arm live trading for the given account.

    By default arms for 7 days (the AWS-CLI / sudo timestamp pattern).
    Pass --once for a 60-second one-shot, or --renew to extend an
    existing armed record without changing accounts or limits.
    """
    if once and renew:
        raise click.UsageError("--once and --renew are mutually exclusive.")
    ttl = _parse_ttl(ttl_str)
    from keel.permissions import write_arm

    status = write_arm(account_id=account_id, ttl=ttl, once=once, renew=renew)
    emit(
        {
            "armed": status.armed,
            "accounts": status.accounts,
            "expires_at": status.expires_at.isoformat() if status.expires_at else None,
            "ttl_applied": str(ttl) if ttl else None,
            "mode": "once" if once else ("renew" if renew else "set"),
        },
        _get_format(ctx),
    )


@arm.command("disarm")
@click.option(
    "--emergency",
    is_flag=True,
    help="Also clear the account list — for kill-switch scenarios.",
)
@click.pass_context
def arm_disarm(ctx: click.Context, emergency: bool) -> None:
    """Disarm live trading (sets `armed: false`)."""
    from keel.permissions import disarm

    status = disarm(emergency=emergency)
    emit({"armed": status.armed, "source": status.source}, _get_format(ctx))
