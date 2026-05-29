"""Tests for the lightweight-git sync verbs.

Covers `keel_strategy_checkout`, `_push`, `_pull`, `_status`,
`_workspaces`, `_discard` — the cross-surface sync model wrappers
around `keel.workspace.*`. Mock the underlying library functions so
the outcome-tool envelope shape is what's exercised.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from keel.errors import KeelError
from keel.tools.outcomes._base import ToolContext

# Side-effect imports — register the tools.
from keel.tools.outcomes import (  # noqa: F401
    strategy_checkout as _ck,
    strategy_push as _ps,
    strategy_pull as _pl,
    strategy_status as _st,
    strategy_workspaces as _ws,
    strategy_discard as _di,
)
from keel.tools.outcomes import OUTCOMES


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


# ── checkout ──────────────────────────────────────────────────────────


def test_checkout_registered_and_returns_workspace_envelope(ctx):
    assert "keel_strategy_checkout" in OUTCOMES
    tool = OUTCOMES["keel_strategy_checkout"]
    assert tool.cli_path == ("strategy", "checkout")

    fake = {
        "strategy_id": "str_abc", "name": "Test Strat",
        "workspace": "/tmp/ws/str_abc", "file": "/tmp/ws/str_abc/strategy.py",
        "source_hash": "deadbeef" * 8, "sequence": 3, "status": "checked_out",
    }
    with patch("keel.workspace.checkout", return_value=fake):
        env = tool.handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["strategy_id"] == "str_abc"
    assert env["workspace"] == "/tmp/ws/str_abc"
    assert env["file"] == "/tmp/ws/str_abc/strategy.py"
    assert env["sequence"] == 3
    assert env["hero_url"] == "https://app.usekeel.io/strategies/str_abc"
    # Agent-facing next-action hints
    assert any("editor" in n.lower() for n in env["next"])
    assert any("keel_strategy_status" in n for n in env["next"])


def test_checkout_missing_strategy_id_raises_usage_error(ctx):
    tool = OUTCOMES["keel_strategy_checkout"]
    with pytest.raises(KeelError) as exc:
        tool.handler({}, ctx)
    assert exc.value.error_code == "missing_strategy_id"
    assert "keel_strategy_search" in (exc.value.suggestion or "")


def test_checkout_threads_dir_to_workspace_function(ctx):
    """Explicit `dir` arg must reach `workspace.checkout(target_dir=...)`."""
    fake = {
        "strategy_id": "str_abc", "name": "S",
        "workspace": "/elsewhere/str_abc", "file": "/elsewhere/str_abc/strategy.py",
        "source_hash": "h", "sequence": 1, "status": "checked_out", "mode": "explicit",
    }
    with patch("keel.workspace.checkout", return_value=fake) as ck:
        env = OUTCOMES["keel_strategy_checkout"].handler(
            {"strategy_id": "str_abc", "dir": "/elsewhere"}, ctx
        ).to_envelope()
    ck.assert_called_once_with("str_abc", target_dir="/elsewhere")
    assert env["mode"] == "explicit"


def test_checkout_home_mode_surfaces_project_init_hint(ctx):
    """When the lib returns mode=home + a hint, that hint must be the
    first next-step the agent sees — it tells the user how to make the
    file IDE-visible."""
    fake = {
        "strategy_id": "str_abc", "name": "S",
        "workspace": "/home/.keel/workspace/str_abc",
        "file": "/home/.keel/workspace/str_abc/strategy.py",
        "source_hash": "h", "sequence": 1, "status": "checked_out",
        "mode": "home",
        "hint": "Run `keel project init` to make this file IDE-visible.",
    }
    with patch("keel.workspace.checkout", return_value=fake):
        env = OUTCOMES["keel_strategy_checkout"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["mode"] == "home"
    assert env["next"][0].startswith("Run `keel project init`")


def test_checkout_project_mode_no_init_hint(ctx):
    """In project mode the file IS visible — no hint should be inserted."""
    fake = {
        "strategy_id": "str_abc", "name": "S",
        "workspace": "/proj/strategies/str_abc",
        "file": "/proj/strategies/str_abc/strategy.py",
        "source_hash": "h", "sequence": 1, "status": "checked_out", "mode": "project",
    }
    with patch("keel.workspace.checkout", return_value=fake):
        env = OUTCOMES["keel_strategy_checkout"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["mode"] == "project"
    assert not any("keel project init" in n for n in env["next"])


# ── push ──────────────────────────────────────────────────────────────


def test_push_returns_new_sequence_and_recommends_backtest(ctx):
    fake = {
        "strategy_id": "str_abc", "status": "pushed",
        "source_hash": "newhash00" * 7, "sequence": 4, "commit_id": "cmt_xyz",
    }
    with patch("keel.workspace.push", return_value=fake):
        env = OUTCOMES["keel_strategy_push"].handler(
            {"strategy_id": "str_abc", "message": "tune ROC=14"}, ctx
        ).to_envelope()
    assert env["sequence"] == 4
    assert env["status"] == "pushed"
    assert env["message"] == "tune ROC=14"
    # Tells agent to backtest next (since server HEAD just moved)
    assert any("keel_backtest_run" in n for n in env["next"])


def test_push_no_changes_says_so_explicitly(ctx):
    fake = {"strategy_id": "str_abc", "status": "no_changes", "source_hash": "same"}
    with patch("keel.workspace.push", return_value=fake):
        env = OUTCOMES["keel_strategy_push"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["status"] == "no_changes"
    assert any("No local changes" in n or "nothing to push" in n.lower() for n in env["next"])


def test_push_without_workspace_directs_to_checkout(ctx):
    # The wrapped push() raises ValueError when there's nothing to push
    with patch("keel.workspace.push", side_effect=ValueError("No workspace strategy found")):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_strategy_push"].handler({}, ctx)
    assert exc.value.error_code == "not_in_workspace"
    assert "keel_strategy_checkout" in (exc.value.suggestion or "")


# ── pull ──────────────────────────────────────────────────────────────


def test_pull_clean_uses_pull_function(ctx):
    fake = {"strategy_id": "str_abc", "status": "pulled", "source_hash": "h", "sequence": 5}
    with patch("keel.workspace.pull", return_value=fake) as p:
        env = OUTCOMES["keel_strategy_pull"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    p.assert_called_once_with(strategy_id="str_abc")
    # Renamed lib's `sequence` → wire `server_sequence` for parity with
    # the status outcome (also avoids ambiguity with local sequence).
    assert env["server_sequence"] == 5


def test_pull_with_force_calls_pull_force(ctx):
    """`force=True` + explicit strategy_id routes to pull_force (overwrites local)."""
    fake = {"strategy_id": "str_abc", "status": "force_pulled", "source_hash": "h", "sequence": 7}
    with patch("keel.workspace.pull_force", return_value=fake) as pf, \
         patch("keel.workspace.pull") as p:
        OUTCOMES["keel_strategy_pull"].handler({"strategy_id": "str_abc", "force": True}, ctx)
    pf.assert_called_once_with("str_abc")
    p.assert_not_called()


def test_pull_diverged_surfaces_remediation(ctx):
    with patch("keel.workspace.pull", side_effect=ValueError("Local has uncommitted changes (diverged)")):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_strategy_pull"].handler({"strategy_id": "str_abc"}, ctx)
    msg = exc.value.suggestion or ""
    assert "keel_strategy_push" in msg
    assert "force=True" in msg or "force" in msg.lower()


# ── status ────────────────────────────────────────────────────────────


# Lib returns `state` (not `status`) with values "current"/"ahead"/
# "behind"/"conflict"; the outcome wrapper translates current→clean
# and conflict→diverged for the agent-facing vocabulary. These tests
# pass the LIB-shape fake; the wrapper does the translation.
@pytest.mark.parametrize("lib_state,wire_state,expected_hint", [
    ("current", "clean", "no sync action"),
    ("ahead", "ahead", "keel_strategy_push"),
    ("behind", "behind", "keel_strategy_pull"),
    ("conflict", "diverged", "force=True"),
])
def test_status_returns_state_specific_remediation(ctx, lib_state, wire_state, expected_hint):
    fake = {
        "strategy_id": "str_abc", "state": lib_state, "name": "S",
        "local_hash": "abc", "remote_hash": "def",
        "sequence": 4, "workspace": "/tmp/ws",
    }
    with patch("keel.workspace.status", return_value=fake):
        env = OUTCOMES["keel_strategy_status"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["status"] == wire_state
    combined = " ".join(env["next"])
    assert expected_hint in combined, (
        f"state={lib_state}: expected hint '{expected_hint}' in next={env['next']}"
    )


def test_status_auto_detects_workspace_when_no_id(ctx):
    """Omitting strategy_id is valid — status() handles auto-detect."""
    fake = {"strategy_id": "str_detected", "state": "current", "name": "Auto",
            "local_hash": "x", "remote_hash": "x", "sequence": 1, "workspace": "/tmp"}
    with patch("keel.workspace.status", return_value=fake) as s:
        env = OUTCOMES["keel_strategy_status"].handler({}, ctx).to_envelope()
    s.assert_called_once_with(strategy_id=None, recent_commits=5)
    assert env["strategy_id"] == "str_detected"
    assert env["status"] == "clean"  # current → clean translation


def test_status_surfaces_recent_commits_when_lib_returns_them(ctx):
    """Default behavior asks lib for recent commits + surfaces them so
    the agent has 'what just happened' context alongside sync state."""
    fake = {
        "strategy_id": "str_x", "state": "current", "name": "X",
        "local_hash": "h", "remote_hash": "h",
        "sequence": 3, "workspace": "/tmp",
        "recent_commits": [
            {"sequence_number": 3, "commit_id": "cmt_3", "message": "tune ROC"},
            {"sequence_number": 2, "commit_id": "cmt_2", "message": "add carry"},
        ],
    }
    with patch("keel.workspace.status", return_value=fake):
        env = OUTCOMES["keel_strategy_status"].handler({"strategy_id": "str_x"}, ctx).to_envelope()
    assert len(env["recent_commits"]) == 2
    assert env["recent_commits"][0]["message"] == "tune ROC"


def test_status_can_opt_out_of_recent_commits(ctx):
    """`include_recent=False` should pass recent_commits=0 to the lib —
    keeps status cheap for hot polling loops."""
    fake = {"strategy_id": "str_x", "state": "current", "name": "X",
            "local_hash": "h", "remote_hash": "h",
            "sequence": 1, "workspace": "/tmp"}
    with patch("keel.workspace.status", return_value=fake) as s:
        OUTCOMES["keel_strategy_status"].handler(
            {"strategy_id": "str_x", "include_recent": False}, ctx
        )
    s.assert_called_once_with(strategy_id="str_x", recent_commits=0)


def test_status_clamps_recent_commits(ctx):
    fake = {"strategy_id": "str_x", "state": "current", "name": "X",
            "local_hash": "h", "remote_hash": "h",
            "sequence": 1, "workspace": "/tmp"}
    with patch("keel.workspace.status", return_value=fake) as s:
        OUTCOMES["keel_strategy_status"].handler(
            {"strategy_id": "str_x", "recent_commits": 9999}, ctx
        )
    s.assert_called_once_with(strategy_id="str_x", recent_commits=20)


# ── workspaces ────────────────────────────────────────────────────────


def test_workspaces_lists_all_checked_out(ctx):
    from keel.workspace import WorkspaceMeta
    fake_list = [
        WorkspaceMeta(strategy_id="str_a", name="Alpha", source_hash="a" * 64,
                      checked_out_at="2026-05-21T10:00:00Z", current_sequence=2),
        WorkspaceMeta(strategy_id="str_b", name="Beta", source_hash="b" * 64,
                      checked_out_at="2026-05-21T11:00:00Z", current_sequence=5),
    ]
    with patch("keel.workspace.list_workspaces", return_value=fake_list):
        env = OUTCOMES["keel_strategy_workspaces"].handler({}, ctx).to_envelope()
    assert env["count"] == 2
    ids = [w["strategy_id"] for w in env["workspaces"]]
    assert ids == ["str_a", "str_b"]


def test_workspaces_empty_state_hints_at_checkout(ctx):
    with patch("keel.workspace.list_workspaces", return_value=[]):
        env = OUTCOMES["keel_strategy_workspaces"].handler({}, ctx).to_envelope()
    assert env["count"] == 0
    assert any("keel_strategy_checkout" in n for n in env["next"])


def test_workspaces_surfaces_mode_per_entry(ctx, tmp_path, monkeypatch):
    """Each entry should report whether it's project-local (IDE-visible)
    or in the hidden home dir — agents need this to tell the user where
    the file actually is."""
    from keel.workspace import WorkspaceMeta, project_workspace_root

    # Set up a project so find_project_root() finds it
    proj = tmp_path / "myproj"
    (proj / ".keel").mkdir(parents=True)
    (proj / ".keel" / "workspace.yaml").write_text("name: myproj\n")
    proj_ws = project_workspace_root(proj)
    proj_ws.mkdir(parents=True)
    (proj_ws / "str_a").mkdir()  # project-local entry exists on disk

    # And a fake home root with a different entry
    home_root = tmp_path / "fake_home"
    home_root.mkdir()
    (home_root / "str_b").mkdir()

    monkeypatch.chdir(proj)
    monkeypatch.setattr("keel.workspace.WORKSPACE_ROOT", home_root)

    fake_list = [
        WorkspaceMeta(strategy_id="str_a", name="Alpha", source_hash="a" * 64,
                      checked_out_at="2026-05-21T10:00:00Z", current_sequence=2),
        WorkspaceMeta(strategy_id="str_b", name="Beta", source_hash="b" * 64,
                      checked_out_at="2026-05-21T11:00:00Z", current_sequence=5),
    ]
    with patch("keel.workspace.list_workspaces", return_value=fake_list):
        env = OUTCOMES["keel_strategy_workspaces"].handler({}, ctx).to_envelope()

    by_id = {w["strategy_id"]: w for w in env["workspaces"]}
    assert by_id["str_a"]["mode"] == "project"
    assert by_id["str_b"]["mode"] == "home"
    assert env["project_root"] == str(proj)


# ── discard ───────────────────────────────────────────────────────────


def test_discard_local_only_does_not_delete_server(ctx):
    fake = {"strategy_id": "str_abc", "status": "discarded", "workspace": "/tmp/ws/str_abc"}
    with patch("keel.workspace.discard", return_value=fake):
        env = OUTCOMES["keel_strategy_discard"].handler({"strategy_id": "str_abc"}, ctx).to_envelope()
    assert env["status"] == "discarded"
    # CRITICAL — must mention the server-side strategy is NOT deleted
    msg = " ".join(env["next"]).lower()
    assert "server" in msg
    assert "unchanged" in msg or "still" in msg
    assert "keel_strategy_delete" in " ".join(env["next"])


def test_discard_requires_confirm_in_cli_mode():
    """Destructive local-delete should require --yes in CLI (confirm_in_cli=True)."""
    tool = OUTCOMES["keel_strategy_discard"]
    assert tool.confirm_in_cli is True
    assert tool.annotations.get("destructiveHint") is True
