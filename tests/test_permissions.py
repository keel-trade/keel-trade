"""Tests for the local live-trading arming layer (spec §7)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from keel.permissions import (
    DEFAULT_TTL,
    ONCE_TTL,
    assert_armed_for_account,
    disarm,
    read_arm_status,
    write_arm,
)


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    """Re-root `Path.home()` for permissions.yaml reads/writes."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_unarmed_when_no_file_exists(tmp_home, monkeypatch):
    # Pin cwd to a directory with no .keel/permissions.yaml either.
    monkeypatch.chdir(tmp_home)
    status = read_arm_status()
    assert status.armed is False
    assert status.source == "none"
    assert "permissions file" in (status.reason or "").lower()


def test_write_arm_defaults_to_7_days(tmp_home):
    status = write_arm(account_id="acct_x")
    assert status.armed is True
    assert status.accounts == ["acct_x"]
    # Should expire in ~7 days
    delta = status.expires_at - datetime.now(timezone.utc)
    assert timedelta(days=6) < delta <= DEFAULT_TTL + timedelta(seconds=5)
    persisted = yaml.safe_load((tmp_home / ".keel" / "permissions.yaml").read_text())
    assert "max_notional_usd" not in persisted["live_trading"]


def test_once_arms_for_60_seconds(tmp_home):
    status = write_arm(account_id="acct_x", once=True)
    delta = status.expires_at - datetime.now(timezone.utc)
    assert ONCE_TTL - timedelta(seconds=2) <= delta <= ONCE_TTL + timedelta(seconds=5)
    assert status.accounts == ["acct_x"]


def test_renew_keeps_account_list_and_extends_ttl(tmp_home):
    write_arm(account_id="acct_x", ttl=timedelta(seconds=10))
    initial = read_arm_status()
    write_arm(account_id="acct_x", renew=True)
    renewed = read_arm_status()
    assert renewed.expires_at > initial.expires_at
    assert renewed.accounts == ["acct_x"]


def test_expired_file_returns_unarmed(tmp_home):
    perms = tmp_home / ".keel" / "permissions.yaml"
    perms.parent.mkdir(parents=True, exist_ok=True)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    perms.write_text(
        yaml.safe_dump(
            {"live_trading": {"armed": True, "accounts": ["acct_x"], "expires": past}}
        )
    )
    status = read_arm_status()
    assert status.armed is False
    assert "expired" in (status.reason or "").lower()


def test_missing_expires_treated_as_disarmed(tmp_home):
    perms = tmp_home / ".keel" / "permissions.yaml"
    perms.parent.mkdir(parents=True, exist_ok=True)
    perms.write_text(
        yaml.safe_dump({"live_trading": {"armed": True, "accounts": ["acct_x"]}})
    )
    status = read_arm_status()
    assert status.armed is False
    assert "expires" in (status.reason or "").lower()


def test_project_file_overrides_home(tmp_home, monkeypatch):
    # Home arms acct_home; project arms acct_project — project wins.
    home_perms = tmp_home / ".keel" / "permissions.yaml"
    home_perms.parent.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    home_perms.write_text(
        yaml.safe_dump(
            {"live_trading": {"armed": True, "accounts": ["acct_home"], "expires": future}}
        )
    )

    project_dir = tmp_home / "proj"
    project_dir.mkdir()
    proj_perms = project_dir / ".keel" / "permissions.yaml"
    proj_perms.parent.mkdir()
    proj_perms.write_text(
        yaml.safe_dump(
            {"live_trading": {"armed": True, "accounts": ["acct_project"], "expires": future}}
        )
    )

    monkeypatch.chdir(project_dir)
    status = read_arm_status()
    assert status.source == "project"
    assert status.accounts == ["acct_project"]


def test_disarm_sets_armed_false(tmp_home):
    write_arm(account_id="acct_x")
    status = disarm()
    assert status.armed is False


def test_assert_armed_raises_when_disarmed(tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    from keel.errors import KeelError

    with pytest.raises(KeelError) as exc:
        assert_armed_for_account("acct_x")
    assert exc.value.error_code == "live_trading_disarmed"


def test_assert_armed_account_scoped(tmp_home):
    write_arm(account_id="acct_x")
    # Arming exists but for a different account — should raise.
    from keel.errors import KeelError

    with pytest.raises(KeelError) as exc:
        assert_armed_for_account("acct_OTHER")
    assert exc.value.error_code == "live_trading_disarmed"


def test_assert_armed_passes_when_armed_for_account(tmp_home):
    write_arm(account_id="acct_x")
    status = assert_armed_for_account("acct_x")
    assert status.armed is True


def test_arm_status_output_has_no_notional_cap(tmp_home):
    from click.testing import CliRunner

    from keel.cli.main import cli

    write_arm(account_id="acct_x")
    result = CliRunner().invoke(cli, ["--format", "json", "arm", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["accounts"] == ["acct_x"]
    assert "max_notional_usd" not in payload
