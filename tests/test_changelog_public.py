"""CHANGELOG.md is public: no internal references in it (Q-3118).

The file ships with the package, PyPI's Changelog link and the public
GitHub mirror show it raw, and keel-site's /changelog renders it. Entries
used to cite ledger ids, spec sections and repo design-doc paths, which no
reader of those surfaces can open. Say what changed for an SDK user instead.

Guarded: ledger ids (``Q-1234``), monorepo project paths (``projects/``),
links that climb out of the package (``../../``) and spec section references
(``spec 07``, ``spec §13.5``). keel-site keeps its render-time sanitiser
(``services/keel-site/src/lib/sdk-changelog.ts``) as a second line, and its
page test asserts the raw file is clean by the same rule.

Non-vacuity: the file has a long dated history and all of it is read.
Seeded: each reference kind in a scratch string is caught and a clean line
is not; appending ``(Q-1234)`` to the real file's text goes red.
"""

from __future__ import annotations

import re
from pathlib import Path


CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

INTERNAL = re.compile(
    r"\bQ-\d{3,4}\b"  # ledger ids
    r"|projects/"  # monorepo project and design-doc paths
    r"|\.\./\.\./"  # links out of the package into the monorepo
    r"|\bspec \d+|\bspec §"  # spec section references
)


def internal_references(text: str) -> list[str]:
    """``line_no: match`` for every internal reference in ``text``."""
    return [
        f"{n}: {m.group(0)}"
        for n, line in enumerate(text.splitlines(), 1)
        for m in INTERNAL.finditer(line)
    ]


def test_changelog_has_no_internal_references() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    releases = re.findall(r"^## \[\d+\.\d+\.\d+\] [—-] \d{4}-\d{2}-\d{2}", text, re.MULTILINE)
    assert len(releases) > 25, "non-vacuity: the whole dated history is read"
    assert internal_references(text) == []


def test_guard_catches_each_kind_and_keeps_clean_prose() -> None:
    seeded = {
        "**A fix (Q-2122).** It works now.": "Q-2122",
        "See `projects/agent-v2/04-plan.md`.": "projects/",
        "Per [the plan](../../docs/plan.md).": "../../",
        "Short windows (new-data-loaders spec 07 §5).": "spec 07",
        "Every tool's spec §13.5 envelope.": "spec §",
    }
    for line, hit in seeded.items():
        assert [r.split(": ", 1)[1] for r in internal_references(line)][:1] == [hit], line
    # Control: ordinary prose, versions, dates, URLs and paths in the package.
    for clean in (
        "Plain sentence with (a normal aside) and `code`.",
        "## [0.9.0] — 2026-10-14",
        "Requires keel-api v1.185 (2026-10-01); see https://usekeel.io/docs/sdk.",
        "One owner, `keel/tools/outcomes/_listed_projection.py`.",
        "EIP-712 signing, Q1 targets and the M-series are not ledger ids.",
    ):
        assert internal_references(clean) == [], clean


def test_guard_reads_the_real_file() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    seeded = text.rstrip("\n") + "\n**A change (Q-1234).**\n"
    assert internal_references(seeded) == [f"{len(seeded.splitlines())}: Q-1234"]
