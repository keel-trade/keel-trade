"""Tests for the 3-layer user context (spec §9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli.main import cli
from keel.context import (
    append_user_context,
    init_user_context,
    read_project_context,
    read_user_context,
    write_user_context,
)


runner = CliRunner()


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


# ─── Module behaviour ────────────────────────────────────────────────────


def test_read_user_context_when_missing_returns_empty(tmp_home):
    entry = read_user_context()
    assert entry.layer == "user"
    assert entry.exists is False
    assert entry.body == ""


def test_init_writes_template(tmp_home):
    path = init_user_context(user="trader@example.com")
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "# Keel context for trader@example.com" in body
    assert "## Identity" in body
    assert "## Custom prompt fragments" in body


def test_init_refuses_to_overwrite_without_force(tmp_home):
    init_user_context()
    with pytest.raises(FileExistsError):
        init_user_context(overwrite=False)
    # With force=True it should overwrite cleanly.
    init_user_context(overwrite=True)


def test_append_creates_notes_section(tmp_home):
    write_user_context("# Keel context for me\n\n## Identity\nQuant.\n")
    append_user_context("only trade post-funding spike")
    body = read_user_context().body
    assert "## Notes" in body
    assert "only trade post-funding spike" in body


def test_append_into_existing_notes_section(tmp_home):
    write_user_context("# Keel context\n\n## Notes\n- existing\n")
    append_user_context("second note")
    body = read_user_context().body
    assert body.count("## Notes") == 1
    assert "second note" in body
    assert "existing" in body


def test_project_context_reads_keel_md(tmp_path, monkeypatch):
    (tmp_path / "keel.md").write_text("## Project preference\nUse buffered rebalance.")
    monkeypatch.chdir(tmp_path)
    entry = read_project_context()
    assert entry.exists is True
    assert entry.source.name == "keel.md"
    assert "buffered rebalance" in entry.body


def test_project_context_falls_back_to_claude_md_keel_block(tmp_path, monkeypatch):
    (tmp_path / "CLAUDE.md").write_text(
        "# Repo guide\n\n## Build\n...\n\n## Keel\nLooks at `keel.md` first.\nThen this block.\n\n## Misc\n",
    )
    monkeypatch.chdir(tmp_path)
    entry = read_project_context()
    assert entry.exists is True
    assert entry.source.name == "CLAUDE.md"
    assert "Looks at `keel.md` first" in entry.body
    assert "Then this block" in entry.body
    # Should NOT include sibling sections.
    assert "## Build" not in entry.body
    assert "## Misc" not in entry.body


def test_project_context_returns_missing_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    entry = read_project_context()
    assert entry.exists is False
    assert entry.body == ""


# ─── CLI ─────────────────────────────────────────────────────────────────


def test_cli_context_init_and_show(tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    result = runner.invoke(cli, ["--format", "json", "context", "init"])
    assert result.exit_code == 0, result.output

    show = runner.invoke(cli, ["--format", "json", "context", "show", "--layer", "user"])
    assert show.exit_code == 0, show.output
    import json

    payload = json.loads(show.output)
    assert payload["layer"] == "user"
    assert payload["exists"] is True
    assert "## Identity" in payload["body"]


def test_cli_context_show_project_when_missing(tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    result = runner.invoke(cli, ["--format", "json", "context", "show", "--layer", "project"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.output)
    assert payload["layer"] == "project"
    assert payload["exists"] is False


def test_cli_context_add_appends_note(tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    result = runner.invoke(cli, ["context", "add", "always include AdverseVolCap"])
    assert result.exit_code == 0
    body = read_user_context().body
    assert "always include AdverseVolCap" in body
