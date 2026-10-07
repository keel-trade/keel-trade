"""Tests for keel_strategy_history + keel_strategy_restore (history navigation).

The agent needs to know what's happened (`log`) and undo / time-travel
(`restore`). Both wrap server-side endpoints — `_log` is read-only,
`_restore` creates a new commit on HEAD.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.errors import KeelError, NotFoundError
from keel.tools.outcomes import OUTCOMES

# Side-effect imports — register the tools.
from keel.tools.outcomes import strategy_log as _log_mod  # noqa: F401
from keel.tools.outcomes import strategy_restore as _restore_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext


@pytest.fixture
def ctx_with_fake_client():
    fake = MagicMock()
    return ToolContext(api_client=fake, is_tty=False, app_url="https://app.usekeel.io"), fake


# ── log ────────────────────────────────────────────────────────────────


def test_log_registered_with_history_tab_hero_url(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    assert "keel_strategy_history" in OUTCOMES
    tool = OUTCOMES["keel_strategy_history"]
    assert tool.cli_path == ("strategy", "log")


def test_log_returns_reverse_chronological_commits(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = [
        {
            "commit_id": "cmt_3",
            "sequence_number": 3,
            "parent_id": "cmt_2",
            "source_hash": "aaa" * 22,
            "message": "tune ROC",
            "created_at": "2026-05-21T12:00:00Z",
            "tags": [],
        },
        {
            "commit_id": "cmt_2",
            "sequence_number": 2,
            "parent_id": "cmt_1",
            "source_hash": "bbb" * 22,
            "message": "add carry",
            "created_at": "2026-05-21T10:00:00Z",
            "tags": [{"name": "v1.0", "tag_type": "semver"}],
        },
        {
            "commit_id": "cmt_1",
            "sequence_number": 1,
            "parent_id": None,
            "source_hash": "ccc" * 22,
            "message": "initial",
            "created_at": "2026-05-21T09:00:00Z",
            "tags": [],
        },
    ]
    env = OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
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
    env = OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_new"}, ctx).to_envelope()
    assert env["count"] == 0
    assert env["head_sequence"] is None
    assert any("keel_strategy_push" in n for n in env["next"])


def test_log_missing_strategy_id_raises(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_history"].handler({}, ctx)
    assert exc.value.error_code == "missing_strategy_id"


def test_log_refuses_a_limit_past_keel_apis_200(ctx_with_fake_client):
    """keel-api's `Query(50, ge=1, le=200)` is the declared bound (Q-2270):
    a value past it is refused, never clamped to 200 with nothing said."""
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = []
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_abc", "limit": 9999}, ctx)
    assert exc.value.error_code == "argument_out_of_range"
    fake.get.assert_not_called()
    OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_abc", "limit": 200}, ctx)
    fake.get.assert_called_once_with("/v1/strategies/str_abc/versions", limit=200)


def test_log_invalid_limit_raises_usage_error(ctx_with_fake_client):
    ctx, _ = ctx_with_fake_client
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_abc", "limit": "abc"}, ctx)
    assert exc.value.error_code == "invalid_argument"


def test_log_handles_future_paginated_shape(ctx_with_fake_client):
    """If the endpoint ever migrates to {data:..., pagination:...}, we
    shouldn't blow up. Defensive — extract_paginated-style."""
    ctx, fake = ctx_with_fake_client
    fake.get.return_value = {
        "data": [
            {
                "commit_id": "cmt_1",
                "sequence_number": 1,
                "parent_id": None,
                "source_hash": "x" * 64,
                "message": "init",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        "pagination": {"cursor": None, "has_more": False},
    }
    env = OUTCOMES["keel_strategy_history"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["count"] == 1


# ── restore ─────────────────────────────────────────────────────────────


def test_restore_registered_creates_new_commit_on_head(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    assert "keel_strategy_restore" in OUTCOMES
    tool = OUTCOMES["keel_strategy_restore"]
    assert tool.cli_path == ("strategy", "restore")

    fake.post.return_value = {
        "strategy_id": "str_abc",
        "current_sequence": 5,
        "commit_id": "cmt_new",
        "source_hash": "newhash" * 8,
    }
    env = tool.handler({"strategy_id": "str_abc", "ref": "3"}, ctx).to_envelope()
    # Right endpoint + body — and NO SDK-default message (spec 03 §2.4):
    # keel-api's "Restored from vN" is the default label.
    fake.post.assert_called_once_with(
        "/v1/strategies/str_abc/versions/restore",
        json={"ref": "3"},
    )
    assert env["restored_from_ref"] == "3"
    assert env["new_sequence"] == 5
    # ONE fact line (spec 02 §2.4 #1(h)), naming listed tools only.
    assert env["next"] == "Restored v3 as v5; keel_strategy_history lists both."
    # The checkout fact is full-profile only, and it is not advice.
    assert "keel_strategy_pull" in env["sync_note"]


def test_restore_next_on_listed_carries_no_local_only_tool(ctx_with_fake_client, monkeypatch):
    """SEED: put the `sync_note` back unconditionally → this reds (the
    listed surface would name `keel_strategy_pull`, a tool it does not serve)."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    ctx, fake = ctx_with_fake_client
    fake.post.return_value = {"current_sequence": 9, "commit_id": "cmt"}
    env = OUTCOMES["keel_strategy_restore"].handler({"strategy_id": "str_abc", "ref": "#4"}, ctx)
    env = env.to_envelope()
    assert env["next"] == "Restored v4 as v9; keel_strategy_history lists both."
    assert "sync_note" not in env
    assert "keel_strategy_pull" not in str(env)


def test_restore_next_names_a_tag_ref_as_given():
    from keel.tools.outcomes.strategy_restore import restore_next

    assert (
        restore_next("champion", 7) == "Restored champion as v7; keel_strategy_history lists both."
    )
    assert (
        restore_next("champion", 7, {"meta": {"restored_from": 2}})
        == "Restored v2 as v7; keel_strategy_history lists both."
    )


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
    assert "keel_strategy_history" in (exc.value.suggestion or "")


def test_restore_unknown_ref_propagates_404(ctx_with_fake_client):
    ctx, fake = ctx_with_fake_client
    fake.post.side_effect = NotFoundError("Version 999 not found")
    with pytest.raises(NotFoundError):
        OUTCOMES["keel_strategy_restore"].handler({"strategy_id": "str_abc", "ref": "999"}, ctx)


def test_a_restore_that_does_not_compile_names_the_fix_forward(ctx_with_fake_client):
    """Spec 03 §2.4 (atomic restore): keel-api 422s RESTORE_DOES_NOT_COMPILE
    and writes nothing; the envelope keeps the code and names the read that
    shows the source. SEED: drop the RESTORE_DOES_NOT_COMPILE branch in
    `strategy_restore._handler` — the recovery-tool assertion reds."""
    import json

    from keel.errors import translate_http_error

    ctx, fake = ctx_with_fake_client
    fake.post.side_effect = translate_http_error(
        422,
        json.dumps(
            {
                "detail": {
                    "code": "RESTORE_DOES_NOT_COMPILE",
                    "message": "v3 does not compile",
                    "compilation_error": "Unknown component 'OldThing'",
                    "restored_from": 3,
                }
            }
        ),
    )
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_restore"].handler({"strategy_id": "str_abc", "ref": "3"}, ctx)
    env = exc.value.to_envelope()
    assert env["code"] == "RESTORE_DOES_NOT_COMPILE"
    assert env["detail"]["compilation_error"] == "Unknown component 'OldThing'"
    assert env["suggested_next_action"]["tool"] == "keel_strategy_get"
    assert env["suggested_next_action"]["args"]["version"] == "3"
    assert "nothing was written" in env["what_was_expected"]
