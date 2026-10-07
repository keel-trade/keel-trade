"""The served corpus never writes a Parallel as a call (spec 04 §4 G1, Q-1854).

agent-surface-cleanup spec 04 §2.1. The DSL writes a Parallel as a dict step,
``{"a": [...], "b": [...]}``; ``Parallel(name=[...])`` is the Python API's
keyword-call form and the parser answers it with ``PARALLEL_BRANCH_SHAPE``.
Until 2026-09-23 the corpus an agent reads taught the call form in 33
component docstrings (served by ``keel_components_search`` /
``_detail_batch`` / ``_compose_help`` from ``keel/data/registry.json``) and in
``composition.md`` / ``capability_boundaries.md``.

The scan covers every string an agent can be served from the corpus: every
string value in the bundled ``registry.json``, every file of the vendored
``keel/data/{reference,knowledge,patterns}``, every skill, and the source
``libs/pipeline_engine/reference/**/*.md`` in the monorepo.

SEED (recorded 2026-09-23): write ``Parallel(a=[EWMA(window=8)])`` into
``SMA``'s docstring and regenerate with ``build_data.py`` —
``test_no_served_string_writes_the_call_form`` reds naming
``registry.json:SMA``; reverted by reversing the edit and regenerating. The
pre-rewrite corpus (``git show HEAD~2``) reds with the 33-component list.
Controls: ``test_the_pattern_sees_the_call_form_and_not_the_note`` — the
positive control ``Parallel(x=[SMA()])`` matches, and the one served sentence
that names the Python API's class (composition.md) does not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
DATA = SDK_ROOT / "keel" / "data"

#: The keyword-call form, whitespace (including newlines) allowed after the
#: parenthesis — the frozen pattern of spec 04 §4 G1.
CALL_FORM = re.compile(r"Parallel\(\s*[A-Za-z_]\w*\s*=", re.S)

#: The dict-step form with double-quoted keys (`{"name": [`), counted as the
#: proof the rewrite taught the right form rather than merely deleting.
DICT_STEP = re.compile(r'\{\s*"[A-Za-z_]\w*"\s*:\s*\[')


def _strings(node: object, path: str) -> list[tuple[str, str]]:
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _strings(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [s for i, v in enumerate(node) for s in _strings(v, f"{path}[{i}]")]
    return []


def _registry_strings() -> list[tuple[str, str]]:
    registry = json.loads((DATA / "registry.json").read_text())
    out = []
    for comp in registry["components"]:
        out += _strings(comp, f"registry.json:{comp['name']}")
    return out


def _corpus_files() -> dict[str, list[Path]]:
    groups = {
        "reference": sorted((DATA / "reference").glob("*.md")),
        "knowledge": sorted((DATA / "knowledge").glob("*.md")),
        "patterns": sorted((DATA / "patterns").glob("*.md")),
        "skills": sorted((SDK_ROOT / "keel" / "skills").glob("*/SKILL.md")),
    }
    source = REPO_ROOT / "libs" / "pipeline_engine" / "reference"
    if source.is_dir():  # the monorepo; an installed wheel carries only the vendored copies
        groups["source"] = sorted(source.rglob("*.md"))
    return groups


def _served() -> list[tuple[str, str]]:
    out = _registry_strings()
    for files in _corpus_files().values():
        out += [
            (str(p.relative_to(REPO_ROOT if REPO_ROOT in p.parents else SDK_ROOT)), p.read_text())
            for p in files
        ]
    return out


def test_the_scan_is_not_vacuous():
    groups = _corpus_files()
    # Floors, not equalities: another lane adding a skill or a topic must not
    # red this file. Measured 2026-09-23: 6 reference, 18 knowledge,
    # 11 patterns, 8 skills, 35 source files; 223 components. Knowledge is 14
    # since the layer split (mcp-conversion 05 §3): the wheel serves the
    # shared layer only, and Keel's in-app files are never vendored.
    assert len(groups["reference"]) >= 6
    assert len(groups["knowledge"]) >= 14
    assert len(groups["patterns"]) >= 11
    assert len(groups["skills"]) >= 8
    if "source" in groups:
        assert len(groups["source"]) >= 35
    registry = _registry_strings()
    assert len(registry) >= 300
    assert len({p.split(":")[1].split(".")[0] for p, _ in registry}) >= 200
    taught = [p for p, s in _served() if DICT_STEP.search(s)]
    assert len(taught) >= 25, f"only {len(taught)} served strings write the dict step"


def test_no_served_string_writes_the_call_form():
    offenders = sorted(
        {
            p.split(".description")[0].split(".usage_hint")[0]
            for p, s in _served()
            if CALL_FORM.search(s)
        }
    )
    assert not offenders, (
        "the served corpus writes Parallel(name=...) — the Python API's call "
        "form, which the DSL parser refuses (PARALLEL_BRANCH_SHAPE); write the "
        'dict step {"name": [...]} instead:\n  ' + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    ("text", "matches"),
    [
        ("Parallel(x=[SMA()])", True),
        ("Parallel(\n    fast=[EWMA(window=8)],\n)", True),
        ("the Python pipeline API also has a `Parallel` class that takes branches", False),
        ('{"fast": [EWMA(window=8)], "slow": [EWMA(window=32)]}', False),
    ],
)
def test_the_pattern_sees_the_call_form_and_not_the_note(text, matches):
    assert bool(CALL_FORM.search(text)) is matches


def test_the_served_note_is_the_negative_control():
    """The one sentence that names the Python API's class is served, and the
    pattern does not see it."""
    note = (DATA / "reference" / "composition.md").read_text()
    assert "`Parallel`\nclass" in note or "`Parallel` class" in note
    assert not CALL_FORM.search(note)
