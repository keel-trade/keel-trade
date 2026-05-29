"""Tests for the workspace module — local checkout/push/pull/status."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from keel.workspace import (
    META_FILE,
    STRATEGY_FILE,
    WorkspaceMeta,
    _compute_hash,
    discard,
    find_project_root,
    find_workspace_strategy,
    get_workspace,
    get_workspace_dir,
    list_workspaces,
    project_workspace_root,
    read_local_source,
    resolve_workspace_dir,
    write_local_source,
)


@pytest.fixture
def workspace_root(tmp_path):
    """Override WORKSPACE_ROOT for tests."""
    with patch("keel.workspace.WORKSPACE_ROOT", tmp_path):
        yield tmp_path


def _create_workspace(root: Path, strategy_id: str, source: str, name: str = "test") -> Path:
    """Helper to create a workspace directory with metadata."""
    ws_dir = root / strategy_id
    ws_dir.mkdir(parents=True)
    (ws_dir / STRATEGY_FILE).write_text(source)

    meta = WorkspaceMeta(
        strategy_id=strategy_id,
        name=name,
        source_hash=_compute_hash(source),
        checked_out_at="2026-04-02T00:00:00Z",
    )
    meta.save(ws_dir)
    return ws_dir


class TestWorkspaceMeta:
    def test_save_and_load(self, workspace_root):
        ws_dir = workspace_root / "test_strategy"
        ws_dir.mkdir()

        meta = WorkspaceMeta(
            strategy_id="test_strategy",
            name="My Strategy",
            source_hash="abc123",
            checked_out_at="2026-04-02T00:00:00Z",
            org_id="org_1",
        )
        meta.save(ws_dir)

        loaded = WorkspaceMeta.load(ws_dir)
        assert loaded.strategy_id == "test_strategy"
        assert loaded.name == "My Strategy"
        assert loaded.source_hash == "abc123"
        assert loaded.org_id == "org_1"


class TestListWorkspaces:
    def test_empty(self, workspace_root):
        assert list_workspaces() == []

    def test_one_workspace(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        result = list_workspaces()
        assert len(result) == 1
        assert result[0].strategy_id == "strat_1"

    def test_multiple_workspaces(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        _create_workspace(workspace_root, "strat_2", "Pipeline([ROC()])")
        result = list_workspaces()
        assert len(result) == 2


class TestGetWorkspace:
    def test_exists(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        meta = get_workspace("strat_1")
        assert meta is not None
        assert meta.strategy_id == "strat_1"

    def test_not_exists(self, workspace_root):
        assert get_workspace("nonexistent") is None


class TestFindWorkspace:
    def test_single_checkout(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        ws = find_workspace_strategy()
        assert ws is not None
        assert ws.strategy_id == "strat_1"

    def test_no_checkouts(self, workspace_root):
        assert find_workspace_strategy() is None

    def test_multiple_checkouts_returns_none(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        _create_workspace(workspace_root, "strat_2", "Pipeline([])")
        assert find_workspace_strategy() is None


class TestReadWriteSource:
    def test_read_source(self, workspace_root):
        source = "Pipeline([ROC(period=8)])"
        _create_workspace(workspace_root, "strat_1", source)
        assert read_local_source("strat_1") == source

    def test_write_source(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "old")
        write_local_source("strat_1", "new")
        assert read_local_source("strat_1") == "new"

    def test_read_nonexistent(self, workspace_root):
        with pytest.raises(FileNotFoundError):
            read_local_source("nonexistent")


class TestComputeHash:
    def test_deterministic(self):
        assert _compute_hash("hello") == _compute_hash("hello")

    def test_different_source(self):
        assert _compute_hash("a") != _compute_hash("b")


class TestStatus:
    def test_current(self, workspace_root):
        source = "Pipeline([])"
        _create_workspace(workspace_root, "strat_1", source)
        from keel.workspace import status

        # No API key → remote check skipped, shows local state only
        result = status("strat_1")
        assert result["strategy_id"] == "strat_1"
        assert result["local_modified"] is False

    def test_local_modified(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "old_source")
        write_local_source("strat_1", "new_source")
        from keel.workspace import status

        result = status("strat_1")
        assert result["local_modified"] is True


class TestDiscard:
    def test_discard(self, workspace_root):
        _create_workspace(workspace_root, "strat_1", "Pipeline([])")
        result = discard("strat_1")
        assert result["status"] == "discarded"
        assert not (workspace_root / "strat_1").exists()

    def test_discard_nonexistent(self, workspace_root):
        with pytest.raises(ValueError):
            discard("nonexistent")


# ── Project-local detection ───────────────────────────────────────────


@pytest.fixture
def in_project(tmp_path, monkeypatch):
    """cwd is inside a Keel project (has `.keel/workspace.yaml`)."""
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    (project_dir / ".keel").mkdir()
    (project_dir / ".keel" / "workspace.yaml").write_text("name: myproj\n")
    monkeypatch.chdir(project_dir)
    return project_dir


@pytest.fixture
def not_in_project(tmp_path, monkeypatch):
    """cwd is OUTSIDE any Keel project (no `.keel/workspace.yaml` ancestors)."""
    plain_dir = tmp_path / "no_project"
    plain_dir.mkdir()
    monkeypatch.chdir(plain_dir)
    return plain_dir


class TestFindProjectRoot:
    def test_finds_marker_in_cwd(self, in_project):
        assert find_project_root() == in_project.resolve()

    def test_finds_marker_in_ancestor(self, in_project, monkeypatch):
        nested = in_project / "deep" / "nested"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert find_project_root() == in_project.resolve()

    def test_none_when_no_marker(self, not_in_project):
        assert find_project_root() is None


class TestResolveWorkspaceDir:
    def test_explicit_dir_wins(self, in_project, tmp_path):
        explicit = tmp_path / "custom"
        path, mode = resolve_workspace_dir("str_x", explicit_dir=explicit)
        assert mode == "explicit"
        assert path == (explicit / "str_x").resolve()

    def test_project_mode_when_in_project(self, in_project):
        path, mode = resolve_workspace_dir("str_x")
        assert mode == "project"
        assert path == in_project / "strategies" / "str_x"

    def test_home_mode_when_outside_project(self, not_in_project, workspace_root):
        path, mode = resolve_workspace_dir("str_x")
        assert mode == "home"
        assert path == workspace_root / "str_x"


class TestProjectLocalListing:
    def test_list_includes_project_workspaces(self, in_project, workspace_root):
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "strat_proj", "Pipeline([])", name="proj_strat")

        result = list_workspaces()
        ids = {ws.strategy_id for ws in result}
        assert "strat_proj" in ids

    def test_project_dedupes_with_home(self, in_project, workspace_root):
        # Same id checked out in both — project should win.
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "shared", "proj_src", name="from_proj")
        _create_workspace(workspace_root, "shared", "home_src", name="from_home")

        result = list_workspaces()
        shared_entries = [ws for ws in result if ws.strategy_id == "shared"]
        assert len(shared_entries) == 1
        assert shared_entries[0].name == "from_proj"

    def test_get_workspace_prefers_project(self, in_project, workspace_root):
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "str_x", "proj_src", name="from_proj")
        _create_workspace(workspace_root, "str_x", "home_src", name="from_home")

        meta = get_workspace("str_x")
        assert meta is not None
        assert meta.name == "from_proj"

    def test_read_local_source_uses_project_workspace(self, in_project, workspace_root):
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "str_x", "PROJECT_SRC", name="p")
        _create_workspace(workspace_root, "str_x", "HOME_SRC", name="h")

        assert read_local_source("str_x") == "PROJECT_SRC"

    def test_write_local_source_targets_existing_project_workspace(
        self, in_project, workspace_root
    ):
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "str_x", "old_src", name="p")

        write_local_source("str_x", "new_src")
        # Project copy was updated
        assert (project_root_path / "str_x" / STRATEGY_FILE).read_text() == "new_src"

    def test_discard_removes_project_workspace_not_home(
        self, in_project, workspace_root
    ):
        project_root_path = project_workspace_root(in_project)
        project_root_path.mkdir(parents=True)
        _create_workspace(project_root_path, "str_x", "proj_src", name="p")

        result = discard("str_x")
        assert result["status"] == "discarded"
        # Project copy is gone
        assert not (project_root_path / "str_x").exists()


# ── Notes breadcrumb ──────────────────────────────────────────────────


class TestNotesAppend:
    def test_append_creates_notes_file_if_missing(self, workspace_root):
        from keel.workspace import _append_notes_entry

        ws_dir = workspace_root / "str_x"
        ws_dir.mkdir()
        _append_notes_entry(ws_dir, "push → sequence=2")

        notes = (ws_dir / "notes.md").read_text()
        assert "push → sequence=2" in notes
        assert notes.startswith("# Workspace notes")

    def test_append_idempotently_grows_existing_notes(self, workspace_root):
        from keel.workspace import _append_notes_entry

        ws_dir = workspace_root / "str_x"
        ws_dir.mkdir()
        _append_notes_entry(ws_dir, "first")
        _append_notes_entry(ws_dir, "second")

        notes = (ws_dir / "notes.md").read_text()
        assert "first" in notes
        assert "second" in notes
        # New line for each — two entries means two timestamped lines
        assert notes.count("- ") == 2

    def test_append_swallows_oserror(self, workspace_root):
        """notes.md is best-effort — push must NEVER fail because of it."""
        from keel.workspace import _append_notes_entry

        # nonexistent dir → would normally raise; helper should swallow
        _append_notes_entry(workspace_root / "nope", "entry")


class TestStatusRecentCommits:
    def test_status_default_skips_recent_commits(self, workspace_root):
        from keel.workspace import status

        _create_workspace(workspace_root, "str_x", "src")
        result = status("str_x")
        # No remote (no API key) + recent_commits=0 default
        assert "recent_commits" not in result

    def test_status_with_recent_commits_zero_does_nothing(self, workspace_root):
        from keel.workspace import status

        _create_workspace(workspace_root, "str_x", "src")
        result = status("str_x", recent_commits=0)
        assert "recent_commits" not in result


class TestNormalizePaginatedVersions:
    """Helper handles all three /versions response shapes the API uses."""

    def test_bare_list(self):
        from keel.workspace import _normalize_paginated_versions

        rows = [{"commit_id": "cmt_1"}, {"commit_id": "cmt_2"}]
        assert _normalize_paginated_versions(rows) == rows

    def test_data_envelope(self):
        from keel.workspace import _normalize_paginated_versions

        assert _normalize_paginated_versions(
            {"data": [{"commit_id": "cmt_1"}], "pagination": {"has_more": False}}
        ) == [{"commit_id": "cmt_1"}]

    def test_versions_envelope(self):
        from keel.workspace import _normalize_paginated_versions

        assert _normalize_paginated_versions(
            {"versions": [{"commit_id": "cmt_1"}]}
        ) == [{"commit_id": "cmt_1"}]

    def test_unknown_shape_returns_empty(self):
        from keel.workspace import _normalize_paginated_versions

        assert _normalize_paginated_versions(None) == []
        assert _normalize_paginated_versions(42) == []
        assert _normalize_paginated_versions("oops") == []

    def test_filters_non_dict_entries(self):
        from keel.workspace import _normalize_paginated_versions

        assert _normalize_paginated_versions(
            [{"commit_id": "ok"}, "junk", None, 42]
        ) == [{"commit_id": "ok"}]


class TestFetchLatestCommitId:
    """Helper is best-effort — never raises, returns None on failure."""

    def test_returns_commit_id_from_list_response(self):
        from unittest.mock import MagicMock

        from keel.workspace import _fetch_latest_commit_id

        client = MagicMock()
        client.get.return_value = [
            {"commit_id": "cmt_head", "sequence_number": 5},
            {"commit_id": "cmt_old"},
        ]
        assert _fetch_latest_commit_id(client, "str_x") == "cmt_head"
        client.get.assert_called_once_with("/v1/strategies/str_x/versions", limit=1)

    def test_returns_none_on_client_error(self):
        from unittest.mock import MagicMock

        from keel.workspace import _fetch_latest_commit_id

        client = MagicMock()
        client.get.side_effect = RuntimeError("API exploded")
        # Must NOT raise — caller's happy path already succeeded.
        assert _fetch_latest_commit_id(client, "str_x") is None

    def test_returns_none_on_empty_versions(self):
        from unittest.mock import MagicMock

        from keel.workspace import _fetch_latest_commit_id

        client = MagicMock()
        client.get.return_value = []
        assert _fetch_latest_commit_id(client, "str_x") is None
