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

Follow-up (2026-10-09, options B and C): six agents asked for `universe`.

4. a knowledge doc declares a name agents ask by with `<!-- topic: x -->`
   (same reader as the pattern header); `universe` serves `universe_selection`;
5. two docs declaring one name, or a declaration that names another doc's
   file, is a test failure — never a silent first-wins;
6. a miss lists first the doc whose NAME contains the word, then the rest
   in the old order (`costs` → `backtest_costs` before `trading_domain`).

Follow-up (2026-10-09b): `data`, `strategies` and `position_manager` missed
for three more agents; each is declared on its owning doc (rows in
``DECLARED_TOPICS``) and served under every spelling by arm 4.

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
from keel.tools.outcomes.help import (
    _DECLARATION_HEAD_LINES,
    _DECLARED_NAME,
    _bundled_index,
    _declared_aliases,
    _declared_names,
    _list_bundled_topics,
    _read_bundled,
    declaration_conflicts,
)


_bootstrap()

HANDLER = OUTCOMES["keel_help"].handler

# What the three pattern docs declare that their file names do not say.
DECLARED = {
    "entry_exit": "entry_exit_patterns",
    "screen_select": "rank_select_patterns",
    "session_and_structure": "session_and_structure_patterns",
}

# What a knowledge doc declares with `<!-- topic: x -->` (Q-2504 follow-up B).
DECLARED_TOPICS = {
    # L13: the strategy pattern's old slug, kept after the rename so a frozen
    # catalog or an old transcript still resolves it.
    "screen_select_patterns": "rank_select_patterns",
    "universe": "universe_selection",
    # Q-2581: the trader phrases for a price-level stop and an R target.
    "structure_stop": "entry_exit_patterns",
    "risk_reward": "entry_exit_patterns",
    # Wave 2026-10-09b: the three words agents asked by and missed.
    # `data`: the doc that teaches every loader and the clock rules in full.
    "data": "data_loading",
    # `strategies` (plural): the catalog of strategy kinds; the named-first
    # hint cannot see it (`strategies` is not a word of `strategy_patterns`).
    "strategies": "strategy_patterns",
    # `position_manager` is the PHASE CATEGORY (no component carries the
    # name); the doc that teaches that layer in full owns it.
    "position_manager": "entry_exit_patterns",
    # Q-3637 (claude.ai screens test, 2026-11-05): an agent asked for
    # `screener_view` to learn the view syntax and got not_found. The screen's
    # view is the view language; the screen itself is the screener reference.
    "screener_view": "view_language",
    "screen_view": "view_language",
    "screens": "screener",
    "screen": "screener",
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


# ---- Follow-up B: `<!-- topic: x -->` on knowledge docs ---------------------


@pytest.mark.parametrize("declared,stem", sorted(DECLARED_TOPICS.items()))
@pytest.mark.parametrize("spelling", [str.lower, str.capitalize, str.upper])
def test_a_declared_topic_serves_its_doc(declared: str, stem: str, spelling) -> None:
    envelope = _fetch(spelling(declared))
    assert envelope["topic"] == stem
    assert envelope["body"] == _fetch(stem)["body"]


def test_the_declared_name_table_is_exactly_the_corpus_declarations() -> None:
    # Non-vacuity: three pattern names plus every `<!-- topic: x -->`, read
    # off the docs; another source of aliases (a hand table) would change this.
    assert _declared_aliases(_bundled_index()) == {**DECLARED, **DECLARED_TOPICS}


def test_no_two_docs_declare_one_name() -> None:
    index = _bundled_index()
    pairs = _declared_names(index)
    # Non-vacuity: every pattern doc declares its name, plus the topic header.
    assert len(pairs) >= 12, pairs
    assert declaration_conflicts(pairs, index) == []


def _declarations_below_the_head(body: str) -> list[str]:
    tail = "\n".join(body.splitlines()[_DECLARATION_HEAD_LINES:])
    return [m.group(0) for m in _DECLARED_NAME.finditer(tail)]


def test_no_declaration_sits_below_the_lines_the_reader_reads() -> None:
    # The reader reads the first _DECLARATION_HEAD_LINES lines only, so a
    # sixth header line is silently dead (entry_exit_patterns.md fills all
    # five since 10-09b). Non-vacuity: the corpus has declarations to check.
    index = _bundled_index()
    assert len(_declared_names(index)) >= 12
    dead = {
        stem: found
        for stem, subdir in index.values()
        if (found := _declarations_below_the_head(_read_bundled(stem, subdir)))
    }
    assert dead == {}, dead


def test_the_head_guard_reds_on_a_declaration_past_the_head() -> None:
    head = "<!-- keywords: x -->\n" + "<!-- topic: a -->\n" * (_DECLARATION_HEAD_LINES - 1)
    assert _declarations_below_the_head(head + "\n# Doc\n") == []  # control
    assert _declarations_below_the_head(head + "<!-- topic: b -->\n# Doc\n") == [
        "<!-- topic: b -->"
    ]


def test_the_duplicate_guard_reds_on_two_docs_declaring_one_topic() -> None:
    bodies = {
        "universe_selection": "<!-- topic: universe -->\n## Universe Selection\n",
        "data_loading": "<!-- pattern: data_loading -->\n<!-- topic: Universe -->\n# Data\n",
        "dsl_syntax": "## DSL\n",
    }
    index = {stem: (stem, "knowledge") for stem in bodies}
    pairs = _declared_names(index, read=lambda stem, _subdir: bodies[stem])
    problems = declaration_conflicts(pairs, index)
    assert problems == ["'universe' declared by ['data_loading', 'universe_selection']"]
    # Control: the same corpus with one declaration is clean.
    bodies["data_loading"] = "<!-- pattern: data_loading -->\n# Data\n"
    pairs = _declared_names(index, read=lambda stem, _subdir: bodies[stem])
    assert declaration_conflicts(pairs, index) == []


def test_the_guard_reds_on_a_declaration_a_file_name_shadows() -> None:
    bodies = {
        "universe_selection": "## U\n",
        "data_loading": "<!-- topic: universe_selection -->\n",
    }
    index = {stem: (stem, "knowledge") for stem in bodies}
    pairs = _declared_names(index, read=lambda stem, _subdir: bodies[stem])
    assert declaration_conflicts(pairs, index) == [
        "'universe_selection' declared by 'data_loading' is the file name of another doc"
    ]


# ---- Follow-up C: the doc named for the word leads the hint -----------------


def _mentioned(topic: str) -> list[str]:
    suggestion = _miss(topic).suggestion or ""
    found = re.search(r"Mentioned in: ([^.]+)\.", suggestion)
    assert found, suggestion
    return found.group(1).split(", ")


@pytest.mark.parametrize(
    "word,named",
    [
        # The old order led with trading_domain (more mentions, a bold line).
        ("costs", "backtest_costs"),
        # The old order did not list universe_selection at all.
        ("selection", "universe_selection"),
    ],
)
def test_a_miss_lists_the_doc_named_for_the_word_first(word: str, named: str) -> None:
    assert _mentioned(word)[0] == named


def test_a_miss_no_doc_is_named_for_keeps_the_old_order() -> None:
    # Control: no doc name contains `execution`; the 10-06 order stands.
    assert _mentioned("Execution") == ["capability_boundaries", "entry_exit_patterns", "dsl_syntax"]
