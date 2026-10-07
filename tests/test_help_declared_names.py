"""`keel_help` resolves a doc by the name it declares, and a miss says where (Q-2504).

An agent asked `keel_help("entry_exit")` — the name `entry_exit_patterns.md`
declares in its own `<!-- pattern: entry_exit -->` header and the name the
docs' prose cites it by — and got not_found. It asked `keel_help("Execution")`
— the DSL block documented in `dsl_syntax` — and got a did-you-mean that
named nothing useful and a topic list cut at 20 of 33.

Arms:

1. declared names — each pattern doc's declared name serves that doc, and the
   file stem stays the served topic;
2. a miss names the docs that mention the word, lists EVERY topic, and is
   still not_found (nothing is guessed);
3. every doc-to-doc citation in the bundled docs and skills ("the `x`
   pattern / topic / reference") resolves through the handler.

Controls: a file stem still serves itself; a one-letter typo still gets its
did-you-mean; a word no doc mentions gets no "Mentioned in". Non-vacuity:
the declared-name table and the citation scan are each non-empty, with a
floor.

Proof it can fail (recorded in the commit): against the pre-Q-2504 help.py,
arms 1-3 red (`entry_exit` not_found; no "Mentioned in"; the list cut at
20; the `entry_exit` pattern citation unresolved) while every control stays
green.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes.help import _list_bundled_topics


_bootstrap()

HANDLER = OUTCOMES["keel_help"].handler

# What the three pattern docs declare that their file names do not say.
DECLARED = {
    "entry_exit": "entry_exit_patterns",
    "screen_select": "screen_select_patterns",
    "session_and_structure": "session_and_structure_patterns",
}

_CITATION = re.compile(r"`([A-Za-z0-9_\-]+)` (?:pattern|topic|reference)s?\b")
# `keel_help` is the tool, not a doc ("a `keel_help` topic").
_NOT_A_DOC = {"keel_help"}


def _fetch(topic: str) -> dict:
    return HANDLER({"topic": topic}, ToolContext()).to_envelope()


def _miss(topic: str) -> KeelError:
    with pytest.raises(KeelError) as info:
        _fetch(topic)
    assert info.value.error_code == "not_found"
    return info.value


@pytest.mark.parametrize("declared,stem", sorted(DECLARED.items()))
def test_a_declared_name_serves_its_doc(declared: str, stem: str) -> None:
    envelope = _fetch(declared)
    assert envelope["topic"] == stem
    assert envelope["body"] == _fetch(stem)["body"]
    assert envelope["body"].strip()


def test_every_pattern_doc_declaring_another_name_is_reachable_by_it() -> None:
    root = resources.files("keel.data").joinpath("patterns")
    declared: dict[str, str] = {}
    for entry in root.iterdir():
        if not entry.name.endswith(".md"):
            continue
        head = "\n".join(entry.read_text(encoding="utf-8").splitlines()[:5])
        match = re.search(r"<!--\s*pattern:\s*([A-Za-z0-9_\-]+)\s*-->", head)
        if match and match.group(1) != entry.name[:-3]:
            declared[match.group(1)] = entry.name[:-3]
    # Non-vacuity: the corpus does declare names its files do not carry.
    assert declared == DECLARED
    for name, stem in declared.items():
        assert _fetch(name)["topic"] == stem


def test_a_file_stem_still_serves_itself() -> None:
    assert _fetch("dsl_syntax")["topic"] == "dsl_syntax"
    assert _fetch("entry_exit_patterns")["topic"] == "entry_exit_patterns"


def test_a_miss_names_the_docs_that_mention_the_word() -> None:
    suggestion = _miss("Execution").suggestion or ""
    mentioned = re.search(r"Mentioned in: ([^.]+)\.", suggestion)
    assert mentioned, suggestion
    assert "dsl_syntax" in mentioned.group(1).split(", ")


def test_a_miss_lists_every_topic() -> None:
    suggestion = _miss("Execution").suggestion or ""
    listed = suggestion.split("Known topics: ", 1)[1].split(", ")
    expected = sorted([*_list_bundled_topics(), "skills", "rules"])
    assert listed == expected
    # Non-vacuity: more than the old 20-topic cut.
    assert len(listed) > 20


def test_a_typo_still_gets_its_did_you_mean() -> None:
    suggestion = _miss("dsl_syntx").suggestion or ""
    assert suggestion.startswith("Did you mean: dsl_syntax")


def test_a_word_no_doc_mentions_names_no_doc() -> None:
    suggestion = _miss("collaboration").suggestion or ""
    assert "Mentioned in" not in suggestion
    assert "Known topics: " in suggestion


def _citations() -> list[tuple[str, str]]:
    import keel

    package = Path(keel.__file__).parent
    found: list[tuple[str, str]] = []
    for base in (package / "data", package / "skills"):
        for path in sorted(base.rglob("*.md")):
            for name in _CITATION.findall(path.read_text(encoding="utf-8")):
                if name not in _NOT_A_DOC:
                    found.append((str(path.relative_to(package)), name))
    return found


def test_every_doc_to_doc_citation_resolves() -> None:
    citations = _citations()
    # Non-vacuity: the corpus cites its own docs by name.
    assert len(citations) >= 10, citations
    unresolved = []
    for where, name in citations:
        try:
            _fetch(name)
        except KeelError:
            unresolved.append(f"{where}: `{name}`")
    assert not unresolved, unresolved
