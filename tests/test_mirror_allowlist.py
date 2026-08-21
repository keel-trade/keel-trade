"""Monorepo gate: the public-mirror G1 allowlist covers the real staged tree.

The publication boundary (spec 07 §8.4, contract SC-6) is enforced by Gate G1
in ``.github/workflows/sync-keel-trade-public.yml`` — it walks the fully staged
public tree and fails closed if any file matches no glob in
``packages/keel-trade/public-overlay/.mirror-allowlist``. That gate runs ONLY
inside the sync workflow, which fires only on an ``sdk-v*`` tag. So allowlist
drift (a new public file class with no matching glob) is latent until release:
the next sync fails closed and publishes nothing.

This test reproduces G1's walk against the LIVE working tree so that drift
reddens on the introducing PR instead of at release time. It:

  * derives the staged file set exactly as the workflow does — ``git ls-files``
    under ``keel-sdk/`` (the rsync source), minus the workflow's rsync excludes,
    minus its explicit ``rm`` list, then the ``public-overlay/`` files layered on
    top (minus ``OVERLAY_NOTES.md``) — reading those rules OUT OF the workflow
    itself so the test can't silently diverge from CI; and
  * runs the REAL ``mirror_gates.gate_allowlist`` (the same function CI runs)
    over that staged tree.

If this test fails with "match no allowlist pattern", a new public file class
was added without a same-PR ``.mirror-allowlist`` edit — add the glob (loud by
construction, per the manifest header).
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up until we find the monorepo markers (works in worktrees + CI)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "mirror_gates.py").is_file() and (
            parent / "packages" / "keel-trade" / "keel-sdk"
        ).is_dir():
            return parent
    raise RuntimeError("could not locate monorepo root from test file")


REPO_ROOT = _repo_root()
SDK_REL = "packages/keel-trade/keel-sdk"
OVERLAY_REL = "packages/keel-trade/public-overlay"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync-keel-trade-public.yml"
MANIFEST = REPO_ROOT / OVERLAY_REL / ".mirror-allowlist"


def _load_mirror_gates():
    path = REPO_ROOT / ".github" / "scripts" / "mirror_gates.py"
    spec = importlib.util.spec_from_file_location("mirror_gates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_workflow_rules(text: str) -> tuple[set[str], list[str]]:
    """Extract the sync's file-selection rules from the workflow, so this test
    stays coupled to CI rather than hardcoding a copy that can drift.

    Returns ``(excludes, rm_list)``:
      * ``excludes`` — union of every ``--exclude='X'`` (rsync patterns matched
        against each path component; the union is applied to both the SDK and
        overlay enumerations — the overlay-only ``OVERLAY_NOTES.md`` and the
        SDK-only cache names are each a no-op on the other tree).
      * ``rm_list`` — the ``public/<path>`` files the workflow deletes from the
        staged tree after the SDK rsync.
    """
    excludes = set(re.findall(r"--exclude='([^']+)'", text))

    # rm list: scope to the `rm -fv ...` command region so unrelated `public/`
    # mentions (rsync targets, prose) can't leak in.
    m = re.search(r"rm -fv\b(.*?)(?:\n\s*- name:|\n\s*#)", text, re.DOTALL)
    rm_region = m.group(1) if m else ""
    # capture group is the path AFTER `public/`, i.e. the staged-tree-relative path
    rm_list = re.findall(r"public/(\S+)", rm_region)

    # Fail-closed structural checks: if the workflow's shape changed enough to
    # break parsing, error loudly rather than model an empty/partial staged set.
    assert excludes, "no rsync --exclude patterns parsed from the workflow"
    assert "__pycache__" in excludes, "expected __pycache__ in rsync excludes"
    assert "OVERLAY_NOTES.md" in excludes, "expected OVERLAY_NOTES.md overlay exclude"
    assert rm_list, "no `rm -fv public/...` deletions parsed from the workflow"
    return excludes, rm_list


def _ls_files(rel_dir: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", rel_dir],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _is_excluded(rel_posix: str, patterns: set[str]) -> bool:
    parts = Path(rel_posix).parts
    return any(fnmatch.fnmatchcase(part, pat) for part in parts for pat in patterns)


def _build_staged_tree(dest: Path) -> None:
    """Materialise the staged public tree the sync would produce.

    G1 (``gate_allowlist``) only inspects path names, never file contents, so
    empty files at the correct relative paths are a faithful stand-in.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    excludes, rm_list = _parse_workflow_rules(text)

    # 1. rsync SDK contents -> public root
    for f in _ls_files(SDK_REL):
        rel = f[len(SDK_REL) + 1 :]
        if _is_excluded(rel, excludes):
            continue
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    # 2. explicit rm of monorepo-only files
    for rel in rm_list:
        (dest / rel).unlink(missing_ok=True)

    # 3. public-overlay wins over the rsync
    for f in _ls_files(OVERLAY_REL):
        rel = f[len(OVERLAY_REL) + 1 :]
        if _is_excluded(rel, excludes):
            continue
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


@pytest.fixture(scope="module")
def staged_tree(tmp_path_factory) -> Path:
    tree = tmp_path_factory.mktemp("public-mirror-staged")
    _build_staged_tree(tree)
    return tree


def test_g1_allowlist_covers_the_live_staged_tree(staged_tree: Path) -> None:
    """The REAL Gate G1 must pass over the current working tree.

    This is the drift tripwire: a new public file class with no ``.mirror-allowlist``
    glob turns this red on the PR that adds it, not at the next ``sdk-v*`` release.
    """
    mirror_gates = _load_mirror_gates()
    rc = mirror_gates.gate_allowlist(staged_tree, MANIFEST)
    assert rc == 0, (
        "Gate G1 is RED: a staged public-mirror file matches no glob in "
        f"{MANIFEST.relative_to(REPO_ROOT)}. Add the glob there (same PR) — see "
        "the offending paths printed above. Without this, the next sdk-v* sync "
        "fails closed and publishes nothing."
    )


def test_widget_assets_are_staged_and_matched(staged_tree: Path) -> None:
    """Guard the specific class the audit caught: the MCP-Apps card widget
    assets (added after sdk-v0.6.1). They must be present in the staged tree
    (they ship in the wheel — DP2 requires them publicly verifiable) and each
    must match an allowlist glob."""
    mirror_gates = _load_mirror_gates()
    patterns = mirror_gates._read_pattern_lines(MANIFEST, "G1 allowlist manifest")

    asset_dir = staged_tree / "keel" / "widgets" / "assets"
    staged = sorted(
        p.relative_to(staged_tree).as_posix() for p in asset_dir.rglob("*") if p.is_file()
    )
    assert staged, "widget assets missing from staged tree — enumeration model drifted"

    for rel in staged:
        assert any(fnmatch.fnmatchcase(rel, pat) for pat in patterns), (
            f"{rel} is staged for the public mirror but matches no allowlist glob"
        )


def test_overlay_notes_and_rm_list_are_not_staged(staged_tree: Path) -> None:
    """The maintainer-only OVERLAY_NOTES.md and the monorepo-only rm-list files
    must not reach the staged tree (sanity check on the reproduction model)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    _, rm_list = _parse_workflow_rules(text)

    assert not (staged_tree / "OVERLAY_NOTES.md").exists()
    for rel in rm_list:
        assert not (staged_tree / rel).exists(), f"{rel} should have been rm'd from the staged tree"
