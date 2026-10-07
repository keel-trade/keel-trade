"""The request outcome slot — ids and booleans only, never an argument (Q-1617).

`keel.hosting.record_outcome` is the one channel from a tool to its audit
row. Two properties are proven here, each with a seeded arm and a control:

1. The gate. An id-shaped platform id is recorded; an argument VALUE handed
   in under an id key (a thesis, a name, a DSL source) is dropped, not
   recorded. The signature is the closed key set — a wrong KEY is a
   `TypeError` at the call site.
2. The adapter's half. `_make_handler` records what a result already says
   (`run_id` by prefix, `extra.strategy_id`, `extra.dry_run`) and stamps
   `is_error` on every error branch, WITHOUT changing the envelope it
   returns. With no slot open (CLI / local stdio) everything is a no-op.
"""

from __future__ import annotations

import json
import logging

import pytest
from keel.errors import KeelError
from keel.hosting import (
    OUTCOME_ID_RE,
    close_request_outcome,
    current_request_outcome,
    open_request_outcome,
    record_outcome,
)
from keel.tools.outcomes._base import OutcomeResult, OutcomeTool
from keel.tools.outcomes._mcp_adapter import _make_handler, outcome_ref_from_result


STRATEGY = "str_01k5g8w2y3z4a5b6c7d8e9f0g1"
COMMIT = "cmt_01k5g8w2y3z4a5b6c7d8e9f0g2"
RUN = "btr_01k5g8w2y3z4a5b6c7d8e9f0g3"


@pytest.fixture
def slot():
    token = open_request_outcome()
    try:
        yield current_request_outcome()
    finally:
        close_request_outcome(token)


# ── 1. the gate ────────────────────────────────────────────────────────────


def test_id_shape_is_the_platform_id_pattern_verbatim():
    """A 3-letter prefix, an underscore, a 26-char lowercase ULID — the
    `platform_auth.ids.ID_PATTERN` shape, re-declared because the wheel
    cannot import libs. keel-api's ingest test asserts the two agree."""
    assert OUTCOME_ID_RE.pattern == r"^[a-z]{3}_[0-9a-z]{26}$"
    assert OUTCOME_ID_RE.match(STRATEGY)
    assert not OUTCOME_ID_RE.match("str_x")
    assert not OUTCOME_ID_RE.match("Buy BTC when RSI < 30")


def test_platform_ids_and_bools_are_recorded(slot):
    record_outcome(strategy_id=STRATEGY, commit_id=COMMIT, dry_run=False)
    record_outcome(backtest_run_id=RUN, is_error=False)
    assert slot == {
        "strategy_id": STRATEGY,
        "commit_id": COMMIT,
        "backtest_run_id": RUN,
        "dry_run": False,
        "is_error": False,
    }


def test_an_argument_value_under_an_id_key_is_dropped(slot, caplog):
    """Seeded arm: a thesis, a name and a DSL source arrive where ids
    belong. None of them is recorded; the control id beside them is."""
    caplog.set_level(logging.WARNING, logger="keel.hosting")
    record_outcome(strategy_id="Buy BTC when RSI < 30")
    record_outcome(commit_id="MyStrat")
    record_outcome(backtest_run_id="Globals(target_timeframe='1d')")
    record_outcome(strategy_id=STRATEGY)  # control
    assert slot == {"strategy_id": STRATEGY}
    dropped = [r for r in caplog.records if "not a platform id" in r.message]
    assert len(dropped) == 3
    # And the value itself never reaches the log line either.
    assert not any("RSI" in r.getMessage() or "MyStrat" in r.getMessage() for r in dropped)


def test_a_non_bool_under_a_bool_key_is_dropped(slot):
    record_outcome(dry_run="yes")  # type: ignore[arg-type]
    record_outcome(is_error=1)  # type: ignore[arg-type]
    record_outcome(dry_run=True)  # control
    assert slot == {"dry_run": True}


def test_a_wrong_key_is_refused_at_the_call_site(slot):
    """The signature IS the allow-list — there is no dict to smuggle into."""
    with pytest.raises(TypeError):
        record_outcome(source="from keel import *")  # type: ignore[call-arg]
    assert slot == {}


def test_the_key_set_is_the_one_keel_api_ingests():
    """The SDK end of one closed key set (Q-1840). keel-api's `OutcomeRef`
    pins the same literal (`services/keel-api/tests/test_audit.py`) and
    refuses any other key with a 422 on the WHOLE event — so a key added
    here alone would silently cost every audit row that carries it."""
    import inspect

    assert set(inspect.signature(record_outcome).parameters) == {
        "strategy_id",
        "commit_id",
        "backtest_run_id",
        "dry_run",
        "is_error",
        "validation_ok",
        "issue_codes",
        "doc_ref",
    }


# ── Q-2368 / Q-2377: two closed Keel vocabularies beside the ids ───────────


def test_issue_codes_record_rule_codes_deduped_in_first_seen_order(slot):
    record_outcome(issue_codes=["INVALID_UNIVERSE", "TYPE_MISMATCH", "INVALID_UNIVERSE"])
    assert slot == {"issue_codes": ["INVALID_UNIVERSE", "TYPE_MISMATCH"]}


def test_issue_codes_drop_anything_that_is_not_a_rule_code(slot, caplog):
    """Seeded arm: free text, lowercase, a message and a non-string arrive as
    codes. Only the conforming code beside them is recorded, and no dropped
    value reaches the log line."""
    caplog.set_level(logging.WARNING, logger="keel.hosting")
    record_outcome(
        issue_codes=[
            "Buy BTC when RSI < 30",
            "invalid_universe",
            "Unknown Universe mode 'static'",
            7,  # type: ignore[list-item]
            "INVALID_UNIVERSE",  # control
        ]
    )
    assert slot == {"issue_codes": ["INVALID_UNIVERSE"]}
    assert not any("RSI" in r.getMessage() or "static" in r.getMessage() for r in caplog.records)


def test_issue_codes_that_conform_to_nothing_record_nothing(slot):
    record_outcome(issue_codes=["not a code"])
    record_outcome(issue_codes="INVALID_UNIVERSE")  # type: ignore[arg-type]  # a str is not a list
    record_outcome(issue_codes=[])
    assert slot == {}


def test_issue_codes_are_capped():
    from keel.hosting import OUTCOME_ISSUE_CODES_MAX

    token = open_request_outcome()
    try:
        record_outcome(issue_codes=[f"RULE_{i:02d}" for i in range(OUTCOME_ISSUE_CODES_MAX + 5)])
        assert len(current_request_outcome()["issue_codes"]) == OUTCOME_ISSUE_CODES_MAX
    finally:
        close_request_outcome(token)


@pytest.mark.parametrize(
    "doc_ref",
    [
        "capability_boundaries",
        "platform-operations",
        "skills",
        "(index)",
        "skill:strategy-creation",
        "rule:TYPE_MISMATCH",
    ],
)
def test_doc_ref_records_a_served_document_name(slot, doc_ref):
    record_outcome(doc_ref=doc_ref)
    assert slot == {"doc_ref": doc_ref}


@pytest.mark.parametrize(
    "typed",
    ["what is my password", "Capability Boundaries", "skill:../etc", "rule:type mismatch", ""],
)
def test_doc_ref_drops_free_text(slot, typed):
    record_outcome(doc_ref=typed)
    assert slot == {}


def test_validation_ok_records_a_verdict_apart_from_is_error(slot):
    record_outcome(dry_run=True, validation_ok=False)
    record_outcome(validation_ok="no")  # type: ignore[arg-type]  # dropped, not coerced
    assert slot == {"dry_run": True, "validation_ok": False}


def test_none_values_do_not_erase_what_was_recorded(slot):
    record_outcome(strategy_id=STRATEGY)
    record_outcome(strategy_id=None, commit_id=COMMIT)
    assert slot == {"strategy_id": STRATEGY, "commit_id": COMMIT}


def test_no_slot_means_no_op():
    """CLI / local stdio open no slot: recording is silent and harmless."""
    assert current_request_outcome() is None
    record_outcome(strategy_id=STRATEGY)  # must not raise
    assert current_request_outcome() is None


def test_slots_are_per_request():
    t1 = open_request_outcome()
    record_outcome(strategy_id=STRATEGY)
    first = dict(current_request_outcome())
    close_request_outcome(t1)
    t2 = open_request_outcome()
    try:
        assert current_request_outcome() == {}
        assert first == {"strategy_id": STRATEGY}
    finally:
        close_request_outcome(t2)


# ── 2. the adapter's half ──────────────────────────────────────────────────


def _tool(handler) -> OutcomeTool:
    return OutcomeTool(
        name="keel_test_tool",
        cli_path=("test", "tool"),
        toolset="backtest",
        description="Test tool. Do NOT use for anything real.",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        annotations={"title": "Test", "readOnlyHint": True},
        handler=handler,
    )


def test_run_id_prefix_classifies_the_field():
    assert outcome_ref_from_result(OutcomeResult(run_id=STRATEGY)) == {"strategy_id": STRATEGY}
    assert outcome_ref_from_result(OutcomeResult(run_id=RUN)) == {"backtest_run_id": RUN}
    # An unknown prefix names no field; a bare extra id still does.
    assert outcome_ref_from_result(OutcomeResult(run_id="shr_" + "0" * 26)) == {}
    assert outcome_ref_from_result(
        OutcomeResult(run_id=RUN, extra={"strategy_id": STRATEGY, "dry_run": True})
    ) == {"backtest_run_id": RUN, "strategy_id": STRATEGY, "dry_run": True}


def test_adapter_records_the_result_and_returns_it_unchanged(slot):
    result = OutcomeResult(run_id=STRATEGY, hero_url="https://app/x", extra={"version": 3})
    handler = _make_handler(_tool(lambda args, ctx: result), frozenset())
    envelope = json.loads(handler(x="1"))
    assert slot == {"strategy_id": STRATEGY}
    assert envelope == result.to_envelope()
    assert not any(k.endswith("_id") and k != "run_id" for k in envelope)


def test_adapter_stamps_is_error_on_a_keel_error(slot):
    def boom(args, ctx):
        raise KeelError("nope", error_code="usage_error")

    handler = _make_handler(_tool(boom), frozenset())
    envelope = json.loads(handler())
    assert slot == {"is_error": True}
    assert envelope["code"] == "usage_error"


def test_adapter_stamps_is_error_on_an_unexpected_exception(slot):
    def boom(args, ctx):
        raise RuntimeError("bug")

    handler = _make_handler(_tool(boom), frozenset())
    envelope = json.loads(handler())
    assert slot == {"is_error": True}
    assert envelope["code"] == "internal_error"


def test_adapter_stamps_is_error_on_a_preflight_argument_failure(slot):
    handler = _make_handler(_tool(lambda a, c: OutcomeResult()), frozenset())
    envelope = json.loads(handler(x="1", nope="2"))
    assert slot == {"is_error": True}
    assert envelope["code"] == "usage_error"


def test_adapter_leaves_a_read_only_result_with_no_ids_unrecorded(slot):
    """Control: a result that carries no id and no dry_run records nothing,
    so the audit row for it carries no `outcome` at all."""
    handler = _make_handler(_tool(lambda a, c: OutcomeResult(extra={"ok": True})), frozenset())
    handler()
    assert slot == {}
