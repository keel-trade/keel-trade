"""Tests for `keel project init` (spec §10 templates + hooks)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli.main import cli
from keel.project import init_project, render_template


runner = CliRunner()


def test_render_template_includes_required_sections():
    body = render_template(project_name="funding-carry-v2")
    assert "# Project: funding-carry-v2" in body
    assert "## What Keel is" in body
    assert "## Active strategy / dataset" in body
    assert "## Recommended workflow" in body
    assert "## Where to find help" in body


def test_init_project_writes_all_files(tmp_path):
    written = init_project(cwd=tmp_path)
    paths = {w.path.name for w in written}
    assert paths == {"CLAUDE.md", "AGENTS.md", ".cursorrules", "rules.md", "workspace.yaml"}
    # All files should exist
    for w in written:
        assert w.path.exists()
        assert w.skipped is False


def test_init_project_skips_existing_without_force(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# already here\n")
    written = init_project(cwd=tmp_path)
    claude = next(w for w in written if w.path.name == "CLAUDE.md")
    assert claude.skipped is True
    assert claude.existed_before is True
    # And the existing content is preserved.
    assert (tmp_path / "CLAUDE.md").read_text() == "# already here\n"


def test_init_project_overwrites_with_force(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# already here\n")
    written = init_project(cwd=tmp_path, force=True)
    claude = next(w for w in written if w.path.name == "CLAUDE.md")
    assert claude.skipped is False
    assert claude.existed_before is True
    # Content replaced.
    body = (tmp_path / "CLAUDE.md").read_text()
    assert "## What Keel is" in body


def test_cli_project_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["--format", "json", "project", "init"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "files" in data
    assert len(data["files"]) == 5
    assert (tmp_path / "CLAUDE.md").exists()


def test_cli_project_hooks_prints_config():
    result = runner.invoke(cli, ["project", "hooks"])
    assert result.exit_code == 0
    out = result.output
    assert '"hooks"' in out
    assert "keel_backtest_run" in out
    assert "pre_tool_use" in out
