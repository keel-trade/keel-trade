"""The `rule:<CODE>` explain channel — guidance spec §3 L3 / §5.

`keel_help(topic="rule:TYPE_MISMATCH")` serves the validation catalog's
own `explain` prose, so a validation error names its code and the code
fetches its lesson (Rust's `--explain`). Before this, 49 KB of `explain`
text existed in `pipeline_engine/dsl/catalog.py` and was reachable from
no agent surface at all.

The source is `pipeline_engine.dsl.catalog.RULES`, imported directly:
the SDK VENDORS `pipeline_engine`, so there is one catalog rather than a
bundled JSON copy that could drift from the validator whose errors it
explains (decision #35, review E1). The freshness guard is therefore a
byte-identity test between the two files, not a regeneration step.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap
from keel.tools.outcomes import help as help_tool

from pipeline_engine.dsl.catalog import RULES, severity_for


_bootstrap()

SDK_ROOT = Path(__file__).resolve().parents[1]


def _call(topic: str | None = None):
    args = {} if topic is None else {"topic": topic}
    return help_tool._handler(args, ToolContext()).to_envelope()


# ── The channel serves the catalog's own prose ──────────────────────────


def test_rule_topic_serves_the_catalogs_explain_text_byte_for_byte():
    """# SEED: in help.py's `explain_for`, return `rule.message_template`
    as "explain" instead of `rule.explain`. Revert by reversing that edit
    (never `git checkout` — shared tree)."""
    env = _call("rule:TYPE_MISMATCH")
    assert env["source"] == "rule"
    assert env["code"] == "TYPE_MISMATCH"
    assert env["explain"] == RULES["TYPE_MISMATCH"].explain
    # Non-vacuity on a quantity the seed cannot fake: the catalog's
    # explain prose is substantial, and the message template it could be
    # confused with is a different, much shorter string.
    assert len(env["explain"]) > 200
    assert env["explain"] != env["message_template"]
    assert env["explain"] in env["body"]


def test_every_code_in_the_catalog_is_served():
    """Non-vacuity for the whole channel, over `len(RULES)` — never a
    literal count, which would pass a catalog that lost half its rules."""
    assert len(RULES) > 50, "the catalog import resolved to something near-empty"
    for code, rule in RULES.items():
        payload = help_tool.explain_for(code)
        assert payload["code"] == code
        assert payload["explain"] == rule.explain
        assert payload["summary"] == rule.summary
        assert payload["related"], f"{code}: no related topics — the pass map has a hole"


def test_severity_is_what_the_validator_would_report():
    """The category is what an agent gets wrong, so the payload carries
    the resolved severity (`severity_for`, overrides and ceiling applied),
    not the raw category.

    A DORMANT ramp is the one case with no reportable severity at all:
    `severity_for` refuses to resolve it, so the payload says `null` and
    names the stage rather than inventing a level.
    """
    checked = dormant = 0
    for code, rule in RULES.items():
        payload = help_tool.explain_for(code)
        if payload["stage"] == "dormant":
            dormant += 1
            assert payload["severity"] is None, f"{code}: dormant but carries a severity"
            continue
        checked += 1
        assert payload["severity"] == severity_for(rule)
    # Non-vacuity on quantities no severity edit can move: most of the
    # catalog is resolvable, and the dormant arm is actually exercised.
    assert checked > 50, f"only {checked} rules resolved a severity"
    assert dormant, "no dormant rule in the catalog — the null-severity arm is untested"
    # And the two fields are genuinely different quantities here.
    differing = [
        c
        for c, r in RULES.items()
        if help_tool.explain_for(c)["severity"] != str(getattr(r.category, "value", ""))
    ]
    assert differing, "severity and category coincide everywhere — the check proves nothing"


def test_every_related_topic_is_fetchable():
    """An index entry is only useful if the call it names works."""
    stems = {help_tool._normalize_topic(t) for t in help_tool._list_bundled_topics()}
    assert stems, "the bundled doc index is empty — nothing could resolve"
    for code in RULES:
        for topic in help_tool.explain_for(code)["related"]:
            assert help_tool._normalize_topic(topic) in stems, (
                f"{code}: related topic {topic!r} is not a bundled doc"
            )


def test_the_pass_map_covers_every_pass_the_catalog_uses():
    """A rule in a pass family nobody mapped would silently fall through
    to the default pair; assert the map is complete instead."""
    used = {str(p) for rule in RULES.values() for p in rule.passes}
    assert used, "no rule declares a pass — the catalog shape changed"
    unmapped = sorted(used - set(help_tool._RELATED_BY_PASS))
    assert not unmapped, f"validator passes with no related-topic mapping: {unmapped}"


def test_case_and_separator_folding():
    """An agent pasting `rule:type-mismatch` out of prose must not miss a
    code the validator spells `TYPE_MISMATCH`."""
    for spelling in ("rule:type-mismatch", "rule:Type_Mismatch", "RULE:TYPE MISMATCH"):
        assert _call(spelling)["code"] == "TYPE_MISMATCH"


def test_a_non_active_code_is_found_not_missed():
    """An old error in a pasted trace still explains itself: a reserved or
    deprecated code is served, with its status said out loud."""
    inactive = [
        c for c, r in RULES.items() if str(getattr(r.status, "value", r.status)) != "active"
    ]
    assert inactive, "no non-active code in the catalog — this arm proves nothing"
    env = _call(f"rule:{inactive[0]}")
    assert env["status"] != "active"
    assert env["status"] in env["body"]


# ── Misses are loud and self-correcting ─────────────────────────────────


def test_unknown_code_raises_not_found_with_close_matches_and_the_version():
    """# SEED: make `_rule_result` return an empty OutcomeResult instead of
    raising — this goes red on the KeelError."""
    with pytest.raises(KeelError) as exc:
        _call("rule:TYPE_MISMACH")
    err = exc.value
    assert err.error_code == "not_found"
    assert "TYPE_MISMATCH" in err.suggestion, "no close match offered for a one-letter typo"
    assert 'topic="rules"' in err.suggestion
    # A lagging pipx install explains itself rather than looking like a bug.
    from keel import __version__

    assert __version__ in err.suggestion


def test_a_code_with_no_close_match_still_names_the_index():
    with pytest.raises(KeelError) as exc:
        _call("rule:ZZZZZZZZ")
    assert 'topic="rules"' in exc.value.suggestion


# ── The index topic ─────────────────────────────────────────────────────


def test_rules_index_lists_every_code():
    env = _call("rules")
    assert env["source"] == "rules"
    assert {r["code"] for r in env["rules"]} == set(RULES)
    assert len(env["rules"]) == len(RULES)
    # Reserved codes are placeholders with no prose yet; every ACTIVE code
    # must carry the one line the index exists to show.
    active = [r for r in env["rules"] if r["status"] == "active"]
    assert len(active) > 50, f"only {len(active)} active codes — the filter ate the index"
    for row in active:
        assert row["summary"], f"{row['code']}: no summary in the index"
    assert str(len(RULES)) in env["info"]


def test_the_reserved_names_shadow_no_bundled_doc():
    """`rules` and `rule:` take precedence over the bundled index, so a doc
    by either name would become unfetchable (the `skills` precedent)."""
    stems = {help_tool._normalize_topic(t) for t in help_tool._list_bundled_topics()}
    assert "rules" not in stems
    assert not any(s.startswith("rule:") for s in stems)


def test_the_no_topic_listing_names_the_channel():
    env = _call(None)
    assert "rules" in env["topics"]
    assert 'topic="rule:<CODE>"' in env["info"]


# ── The description carries the channel (A6) ────────────────────────────


def test_the_help_description_routes_to_the_channel_within_the_host_cut():
    """A6: `rule:<CODE>` joins the table of contents in the same commit as
    the channel — a listed topic the tool cannot serve would break "the
    description must match the tool's actual behavior", and a channel no
    description names is unreachable on claude.ai."""
    tool = OUTCOMES["keel_help"]
    assert "rule:<CODE>" in tool.description
    assert "`rules`" in tool.description
    # Claude Code's hard cut is 2,048 BYTES (decision #38).
    assert len(tool.description.encode()) <= 2048
    param = tool.input_schema["properties"]["topic"]["description"]
    assert "rule:TYPE_MISMATCH" in param and "`rules`" in param


# ── Freshness: one catalog, not two ─────────────────────────────────────
#
# The channel explains the errors the VALIDATOR emits, so a vendored copy
# that drifts from `libs/pipeline_engine/dsl/catalog.py` serves the wrong
# lesson at the wrong severity — silently, because both files parse. That
# guard is decision #35's, and it lives once, in
# `tests/test_guidance_guards.py::test_the_vendored_rule_catalog_is_byte_identical`
# (spec §5 "catalog identity"). It is deliberately NOT duplicated here: two
# implementations of one measurement is a defect, not redundancy
# (`.claude/rules/lessons.md`, 2026-08-06).


def test_the_channel_reads_the_vendored_catalog_not_a_bundled_copy():
    """What this file owns of that contract: the SOURCE. Review E1 — the
    SDK vendors `pipeline_engine`, so `rule:<CODE>` imports the catalog
    directly; a bundled `rule_catalog.json` would be a second catalog and
    a second thing to keep fresh.

    # SEED: add `keel/data/rules/rule_catalog.json` and read it in
    # `explain_for` — this goes red on the bundled-file assertion.
    """
    import inspect

    source = inspect.getsource(help_tool.explain_for)
    assert "from pipeline_engine.dsl.catalog import RULES" in source
    assert "json" not in source, "the explain channel grew a JSON read"
    assert not (SDK_ROOT / "keel" / "data" / "rules").exists(), (
        "a bundled rule catalog appeared beside the vendored module — one "
        "catalog, not two (decision #35 / review E1)"
    )
    # Non-vacuity: the import the assertion pins actually resolves, and to
    # a real catalog rather than an empty stand-in.
    assert len(RULES) > 50


# ── End to end through a real server ────────────────────────────────────


def test_a_tools_only_client_reaches_a_rule_in_one_call():
    from keel.mcp.server import create_server

    async def go(topic):
        server = create_server()
        result = await server.call_tool("keel_help", {"topic": topic})
        return json.loads(result.content[0].text)

    env = asyncio.run(go("rule:SLOT_REF_NOT_FOUND"))
    assert env["code"] == "SLOT_REF_NOT_FOUND"
    assert env["explain"] == RULES["SLOT_REF_NOT_FOUND"].explain
    index = asyncio.run(go("rules"))
    assert len(index["rules"]) == len(RULES)
