"""``scripts/release_date.py`` dates the generated docs pages by the keel-trade
release they document (seo-growth-engine research/11 #11).

The current version's date is its CHANGELOG heading, both generated pages
carry it, and a version with no dated heading is refused (seeded), never
guessed. Monorepo only, like ``test_surface_routing.py``: the site files
live outside the SDK.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
SITE_DOCS = REPO_ROOT / "services" / "keel-site" / "content" / "docs"


def _module():
    spec = importlib.util.spec_from_file_location(
        "release_date", SDK_ROOT / "scripts" / "release_date.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_date_is_the_current_versions_changelog_heading():
    rd = _module()
    version = rd.keel_trade_version()
    date = rd.keel_trade_release_date()
    changelog = (SDK_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}] — {date}" in changelog


@pytest.mark.skipif(not SITE_DOCS.is_dir(), reason="monorepo only")
def test_both_generated_pages_carry_it():
    date = _module().keel_trade_release_date()
    for rel in ("sdk/tool-reference.mdx", "agents/setup.mdx"):
        head = (SITE_DOCS / rel).read_text(encoding="utf-8").split("\n---\n", 1)[0]
        assert re.search(rf'^updated: "{date}"$', head, re.M), rel


def test_a_version_with_no_dated_heading_is_refused(tmp_path):
    rd = _module()
    (tmp_path / "pyproject.toml").write_text('version = "9.9.9"\n')
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.0.0] — 2026-01-01\n")
    with pytest.raises(RuntimeError, match=r"9\.9\.9"):
        rd.keel_trade_release_date(tmp_path)
    # Control: the dated version in the same file resolves.
    (tmp_path / "pyproject.toml").write_text('version = "1.0.0"\n')
    assert rd.keel_trade_release_date(tmp_path) == "2026-01-01"
