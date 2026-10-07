"""`keel_help` records the NAME of the document it served (Q-2377).

A connector user whose whole session was `keel_help` calls left an audit row
holding only `args_hash`; what they read was recoverable only by re-hashing
every bundled topic. The tool now records `doc_ref` — the resolved, canonical
name the result already carries in `extra["topic"]` — on the audit row
through the one outcome channel. Never the typed `topic` argument: a lookup
by any spelling records the canonical name, and a MISS records nothing at all
(a miss's only name is the caller's free text). Founder ruling 2026-10-04:
audit row only, not PostHog.

Proof it can fail (run 2026-10-04, reverted by reversing the edit): `_handler`
made to record the raw argument before resolving
(`record_outcome(doc_ref=args.get("topic", "") or "(index)")`) → 6 red: the
spelling, hyphenated-stem, rule, skill and adapter arms (the typed spelling
recorded, not the canonical name) and the slug-shaped miss arm
(`capability_boundariez` reaches the slot). Green under the seed: the
subjects arm, the `(index)` arm, and the free-text miss — `record_outcome`'s
shape gate drops `what is my password` on its own, the second line of
defence. That is why the seed arms use non-canonical spellings.

Proof it is not vacuous: the bundled index is non-empty and contains the
subject topic, the skill list is non-empty, and the miss arm's topic is
asserted absent from the index before it is looked up.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import KeelError
from keel.hosting import close_request_outcome, current_request_outcome, open_request_outcome
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap
from keel.tools.outcomes import help as help_tool
from keel.tools.outcomes._mcp_adapter import _make_handler


_bootstrap()


@pytest.fixture
def slot():
    token = open_request_outcome()
    try:
        yield current_request_outcome()
    finally:
        close_request_outcome(token)


def _call(topic: str | None = None):
    args = {} if topic is None else {"topic": topic}
    return help_tool._handler(args, ToolContext())


def test_the_subjects_exist():
    """Non-vacuity: the index, the topic under test and the skills are real."""
    topics = help_tool._list_bundled_topics()
    assert len(topics) > 10
    assert "capability_boundaries" in topics
    assert "capability_boundariez" not in topics
    assert help_tool._skill_names()


def test_a_hit_by_any_spelling_records_the_canonical_name(slot):
    result = _call("Capability-Boundaries")
    assert result.extra["topic"] == "capability_boundaries"
    assert slot == {"doc_ref": "capability_boundaries"}


def test_the_listed_hyphenated_stem_is_recorded_as_served(slot):
    _call("platform_operations")
    assert slot == {"doc_ref": "platform-operations"}


def test_a_rule_records_the_catalog_code(slot):
    _call("rule:type_mismatch")
    assert slot == {"doc_ref": "rule:TYPE_MISMATCH"}


def test_a_skill_records_the_skill_slug(slot):
    name = help_tool._skill_names()[0]
    _call("skill:" + name.upper().replace("-", "_"))
    assert slot == {"doc_ref": f"skill:{name}"}


def test_the_no_topic_listing_records_the_index(slot):
    _call(None)
    assert slot == {"doc_ref": "(index)"}


def test_control_a_miss_records_nothing(slot):
    with pytest.raises(KeelError) as exc:
        _call("capability_boundariez")
    assert exc.value.error_code == "not_found"
    assert slot == {}


def test_control_a_free_text_miss_records_nothing(slot):
    with pytest.raises(KeelError):
        _call("what is my password")
    assert slot == {}


def test_through_the_mcp_adapter_the_result_is_unchanged(slot):
    """End to end through the hosted handler: the slot gets the served name,
    the envelope the agent reads does not change, and a miss stamps only the
    tool error (`is_error`), never the typed topic."""
    handler = _make_handler(OUTCOMES["keel_help"], frozenset())
    envelope = json.loads(handler(topic="capability-boundaries"))
    assert slot == {"doc_ref": "capability_boundaries"}
    assert "doc_ref" not in envelope
    assert envelope["topic"] == "capability_boundaries"

    slot.clear()
    json.loads(handler(topic="what is my password"))
    assert slot == {"is_error": True}
