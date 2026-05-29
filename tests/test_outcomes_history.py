"""Tests for keel_strategy_log + keel_strategy_restore (history navigation).

The agent needs to know what's happened (`log`) and undo / time-travel
(`restore`). Both wrap server-side endpoints — `_log` is read-only,
`_restore` creates a new commit on HEAD.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from keel.errors import KeelError, NotFoundError
from keel.tools.outcomes._base import ToolContext

# Side-effect imports — register the tools.
from keel.tools.outcomes import (  # noqa: F401
    strategy_log as _log_mod,
    strategy_restore as _restore_mod,
)
from keel.tools.outcomes import OUTCOMES


@pytest.fixture
def ctx_with_fake_client():
    fake = MagicMock()
    return ToolContext(api_client=fake, is_tty=False,
                       app_url="https://app.usekeel.io"), fake


# ── log ────────────────────────────────────────────────────────────────


def test_log_registered_with_history_tab_hero_url(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    assert "keel_strategy_log" in OUTCOMES
    tool = OUTCOMES["keel_strategy_log"]
    assert tool.cli_path == ("strategy", "log")


def test_log_returns_reverse_chronological_commits(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = [
        {"commit_id": "cmt_3", "sequence_number": 3, "parent_id": "cmt_2",
         "source_hash": "aaa" * 22, "message": "tune ROC", "created_at": "2026-05-21T12:00:00Z",
         "tags": []},
        {"commit_id": "cmt_2", "sequence_number": 2, "parent_id": "cmt_1",
         "source_hash": "bbb" * 22, "message": "add carry", "created_at": "2026-05-21T10:00:00Z",
         "tags": [{"name": "v1.0", "tag_type": "semver"}]},
        {"commit_id": "cmt_1", "sequence_number": 1, "parent_id": None,
         "source_hash": "ccc" * 22, "message": "initial", "created_at": "2026-05-21T09:00:00Z",
         "tags": []},
    ]
    env = OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    # Endpoint called correctly with default limit
    fake.get.assert_called_once_with("/v1/strategies/str_abc/versions", limit=50)
    assert env["count"] == 3
    assert env["head_sequence"] == 3
    assert env["commits"][0]["sequence_number"] == 3
    assert env["commits"][0]["message"] == "tune ROC"
    assert env["commits"][1]["tags"][0]["name"] == "v1.0"
    # Hero URL points at the history tab so the user can click through
    assert "/strategies/str_abc" in env["hero_url"]


def test_log_empty_history_hints_at_first_push(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = []
    env = OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_new"}, ctx).to_envelope()
    assert env["count"] == 0
    assert env["head_sequence"] is None
    assert any("keel_strategy_push" in n for n in env["next"])


def test_log_missing_strategy_id_raises(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_log"].handler({}, ctx)
    assert exc.value.error_code == "missing_strategy_id"


def test_log_clamps_limit_to_200(ctx_with_fake_client):
    """Per keel-api router clamp; we mirror client-side so agents get
    consistent behavior."""
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = []
    OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_abc", "limit": 9999}, ctx)
    fake.get.assert_called_once_with("/v1/strategies/str_abc/versions", limit=200)


def test_log_invalid_limit_raises_usage_error(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_abc", "limit": "abc"}, ctx)
    assert exc.value.error_code == "invalid_argument"


def test_log_handles_future_paginated_shape(ctx_with_fake_client):
    """If the endpoint ever migrates to {data:..., pagination:...}, we
    shouldn't blow up. Defensive — extract_paginated-style."""
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = {
        "data": [{"commit_id": "cmt_1", "sequence_number": 1, "parent_id": None,
                  "source_hash": "x" * 64, "message": "init", "created_at": "2026-01-01T00:00:00Z"}],
        "pagination": {"cursor": None, "has_more": False},
    }
    env = OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["count"] == 1


# ── restore ─────────────────────────────────────────────────────────────


def test_restore_registered_creates_new_commit_on_head(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    assert "keel_strategy_restore" in OUTCOMES
    tool = OUTCOMES["keel_strategy_restore"]
    assert tool.cli_path == ("strategy", "restore")

    fake.post.return_value = {
        "strategy_id": "str_abc", "current_sequence": 5,
        "commit_id": "cmt_new", "source_hash": "newhash" * 8,
    }
    env = tool.handler({"strategy_id": "str_abc", "ref": "3"}, ctx).to_envelope()
    # Right endpoint + body
    fake.post.assert_called_once_with(
        "/v1/strategies/str_abc/versions/restore",
        json={"ref": "3", "message": "Restore version 3"},
    )
    assert env["restored_from_ref"] == "3"
    assert env["new_sequence"] == 5
    # Tells the agent the local workspace needs to pull
    assert any("keel_strategy_pull" in n for n in env["next"])
    # And to backtest the restored version
    assert any("keel_backtest_run" in n for n in env["next"])


def test_restore_with_custom_message(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.post.return_value = {"current_sequence": 5, "commit_id": "cmt"}
    OUTCOMES["keel_strategy_restore"].handler(
        {"strategy_id": "str_abc", "ref": "cmt_xyz", "message": "Revert bad change"}, ctx
    )
    body = fake.post.call_args.kwargs["json"]
    assert body["message"] == "Revert bad change"


def test_restore_missing_ref_directs_to_log(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_restore"].handler({"strategy_id": "str_abc"}, ctx)
    assert exc.value.error_code == "missing_ref"
    assert "keel_strategy_log" in (exc.value.suggestion or "")


def test_restore_unknown_ref_propagates_404(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.post.side_effect = NotFoundError("Version 999 not found")
    with pytest.raises(NotFoundError):
        OUTCOMES["keel_strategy_restore"].handler(
            {"strategy_id": "str_abc", "ref": "999"}, ctx
        )
