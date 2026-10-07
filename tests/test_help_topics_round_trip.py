"""`keel_help` index ↔ fetch round trip (Q-1456).

The no-topic call lists what the tool can fetch, and an agent picks its
next call from that list. Until 2026-09-16 the list and the fetch used two
different spellings of a filename: `_list_bundled_topics` advertised the
raw stem while `_try_bundled` opened `{_normalize_topic(request)}.md`, so
the one hyphenated file in the corpus (`platform-operations.md`, added
2026-09-01) was listed and unfetchable under every spelling — with a
did-you-mean that suggested the very name typed. Nothing exercised the
index against the fetch, which is the guard this file adds.

Three arms, on quantities a seeded index defect cannot move:

1. the round trip — EVERY listed topic, fetched through the handler,
   returns a body and echoes the listed spelling (so the resource URI it
   carries resolves too);
2. non-vacuity — the list is the whole corpus (a floor on its size), the
   hyphenated file that failed is in it by name, and normalization is
   injective over the corpus (two stems folding to one slug would make
   the fetch ambiguous, and the index would silently hide one of them);
3. the folded spellings — an agent's reasonable guesses for the
   hyphenated name (`platform_operations`, `Platform Operations`) reach
   the same document.

Proof it can fail (recorded in the commit): seeding a bogus stem into the
index reds arm 1 on that stem only; renaming the hyphenated file's stem in
the index (not on disk) reds arm 1 and arm 2's by-name pin.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from keel.data.knowledge import load_section
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._toolsets import SERVER_PROFILE_ENV
from keel.tools.outcomes.help import _list_bundled_topics, _normalize_topic


_bootstrap()

HANDLER = OUTCOMES["keel_help"].handler

# The one non-snake_case stem in the corpus — the case that was broken.
HYPHENATED_TOPIC = "platform-operations"

# Reserved namespaces that are real topics but not bundled DOCS: they route
# into `keel.skills` and the validation catalog instead of `keel.data`. The
# round trip below still redeems both, exactly as listed.
RESERVED_TOPICS = ("skills", "rules")


def _index() -> list[str]:
    return list(HANDLER({}, ToolContext()).to_envelope()["topics"])


def _fetch(topic: str) -> dict:
    return HANDLER({"topic": topic}, ToolContext()).to_envelope()


def test_index_is_the_whole_corpus_and_normalization_is_injective():
    """Non-vacuity for the round trip below."""
    listed = _index()
    for reserved in RESERVED_TOPICS:
        assert reserved in listed, (
            f"{reserved!r} is a fetchable topic but the index omits it — an agent "
            "picks its next call from this list"
        )
    docs = [t for t in listed if t not in RESERVED_TOPICS]
    assert len(docs) >= 15, f"the index lists only {len(docs)} docs — is keel.data built?"
    assert HYPHENATED_TOPIC in docs, (
        f"{HYPHENATED_TOPIC!r} is not in the index — the arm that was broken is no "
        "longer exercised; if the file was renamed, move this pin to the new stem"
    )
    assert sorted(docs) == sorted(_list_bundled_topics())
    folded = [_normalize_topic(t) for t in docs]
    assert len(set(folded)) == len(folded), (
        "two bundled stems fold to one slug — the fetch is ambiguous and the index "
        f"hides one of them: {sorted(t for t in docs if folded.count(_normalize_topic(t)) > 1)}"
    )


#: The SERVED doc set, pinned (mcp-conversion 05 §3, G2 "vendored = served =
#: reference"). The wheel carries only the files reference/LAYERS.yaml
#: declares `reference` or `sectioned`; a topic appearing here that is not
#: in this list is Keel's in-app guidance leaking onto the agent surface,
#: and one disappearing is a shared doc lost. Change it together with
#: LAYERS.yaml, never on its own.
SERVED_DOCS = (
    "backtest_costs",
    "best_practices",
    "capability_boundaries",
    "combining_signals",
    "common_mistakes",
    "composition",
    "composition_mechanics",
    "data_loading",
    "dsl_syntax",
    "entry_exit_patterns",
    "forecast_pipeline",
    "improvement_ladders",
    "mistakes",
    "normalization",
    "operating_core",
    "phases",
    "pipeline_system",
    "platform-operations",
    "position_sizing",
    "reasoning_principles",
    "regime_conditioning",
    "risk_management",
    "screen_select_patterns",
    "session_and_structure_patterns",
    "slots",
    "strategy_paths",
    "strategy_patterns",
    "tool_usage",
    "trading_domain",
    "types",
    "universe_selection",
)

#: Keel's in-app-only guidance (system/chat/ in the monorepo): never served.
IN_APP_ONLY = (
    "collaboration",
    "strategy_phases",
    "editor_ui",
    "component_versioning",
    "costs_and_fees",
)


def test_the_served_docs_are_exactly_the_pinned_shared_layer():
    """G2 on the tool path and the resource path: `keel_help()` lists, and
    `resources/list` + the knowledge resource template enumerate, exactly
    the pinned shared docs; every in-app-only topic misses cleanly.

    # SEED: copy libs/pipeline_engine/reference/system/chat/editor_ui.md into
    # keel/data/knowledge/ — the pin reds on `editor_ui`. Revert by deleting
    # the copy.
    """
    from keel.errors import KeelError
    from keel.mcp.server import _bundled_knowledge_sections

    docs = sorted(t for t in _index() if t not in RESERVED_TOPICS)
    assert docs == sorted(SERVED_DOCS)
    knowledge = set(_bundled_knowledge_sections())
    for topic in IN_APP_ONLY:
        assert topic not in docs and topic not in knowledge, topic
        with pytest.raises(KeelError) as err:
            _fetch(topic)
        assert err.value.error_code == "not_found"
    # Control + non-vacuity: a shared doc in the same knowledge directory is
    # served, and both enumerations read something.
    assert "composition_mechanics" in knowledge and "backtest_costs" in knowledge
    assert len(knowledge) >= 14 and len(docs) == len(SERVED_DOCS)


def test_every_listed_topic_is_fetchable_under_its_listed_spelling():
    """Q-1456. The list is a promise; every entry must be redeemable
    EXACTLY as listed, and the result must say which entry it redeemed."""
    listed = _index()
    assert listed, "the index is empty — nothing to round-trip"
    failures: list[str] = []
    for topic in listed:
        try:
            out = _fetch(topic)
        except Exception as exc:  # noqa: BLE001 — collect every miss, then report
            failures.append(f"{topic}: {exc}")
            continue
        if not out.get("body"):
            failures.append(f"{topic}: empty body")
        elif out.get("topic") != topic:
            failures.append(f"{topic}: result echoes {out.get('topic')!r}")
    assert not failures, "listed but unfetchable:\n" + "\n".join(failures)


def test_a_bundled_docs_resource_uri_resolves():
    """The echoed `topic` is what `keel://knowledge/{section}` opens
    verbatim, so the listed spelling — not the folded one — must be it."""
    out = _fetch(HYPHENATED_TOPIC)
    assert out["source"] == "bundled"
    assert out["resource_uri"] == f"keel://knowledge/{HYPHENATED_TOPIC}"
    assert load_section(HYPHENATED_TOPIC) == out["body"]


@pytest.mark.parametrize(
    "spelling",
    ["platform-operations", "platform_operations", "Platform Operations", "PLATFORM-OPERATIONS"],
)
def test_the_hyphenated_topic_fetches_under_every_reasonable_spelling(spelling):
    out = _fetch(spelling)
    assert out["topic"] == HYPHENATED_TOPIC
    assert "## " in out["body"]


def test_round_trip_holds_over_a_real_listed_server(monkeypatch):
    """The path a hosted tools-only client takes: index, then fetch, both
    through FastMCP on the listed profile."""
    monkeypatch.setenv(SERVER_PROFILE_ENV, "listed")
    from keel.mcp.server import create_server

    async def go():
        server = create_server()
        index = json.loads((await server.call_tool("keel_help", {})).content[0].text)
        misses = []
        for topic in index["topics"]:
            res = json.loads(
                (await server.call_tool("keel_help", {"topic": topic})).content[0].text
            )
            if res.get("ok") is False or not res.get("body"):
                misses.append(topic)
        return index["topics"], misses

    topics, misses = asyncio.run(go())
    assert HYPHENATED_TOPIC in topics
    assert misses == [], f"listed on the hosted surface but unfetchable there: {misses}"
