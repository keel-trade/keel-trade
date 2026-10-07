"""Per-field human rendering of the live-execution surface (Q-0913, A8 F-7).

Every fixture below is SHAPED FROM A REAL PRODUCTION ROW, read read-only
from prod on 2026-09-02 (the queries are recorded in the lane report).
Two runs, both cited in `audit-2026-09-02/A8-surfaces.md`:

* ``ern_01m0wtkz1kng3f31p4cs8b76e8`` — 15 sessions, 14 ``SKIPPED/NO_DELTA``
  + 1 ``HALTED/account_invariant_halt:572``, zero order rows, and the
  verbatim ``error_summary`` A8 quotes. This is the bar the audit found
  rendering as an internal session id.
* ``ern_01m1fpn80j7n4ghzmh2xjqj6xj`` — 11 sessions (5 ``FILLED/
  target_filled``, 4 ``SKIPPED/NO_DELTA``, 2 ``SKIPPED/BELOW_MIN_TRADE``),
  11 sealed receipts, notional-weighted arrival slippage
  ``-0.641077210475534`` bps over 5 measured sessions.

The vocabulary is the server's, verified live:
``SKIPPED/NO_DELTA``, ``FILLED/target_filled``, ``SKIPPED/BELOW_MIN_TRADE``,
``RESIDUAL/maker_attempt_budget_exhausted``,
``HALTED/account_invariant_halt:572``.
"""

from __future__ import annotations

import pytest
from keel.output import (
    FIELD_RENDERERS,
    format_execution_row,
    format_execution_rows,
    format_field_line,
    format_human,
    is_execution_rows,
    render_field,
)


# ─── Fixtures shaped from prod ───────────────────────────────────────────

#: A halted bar. Every count is 0 because the account was frozen, NOT
#: because there was nothing to do.
HALTED_ROW: dict = {
    "attempt_id": "ern_01m0wtkz1kng3f31p4cs8b76e8",
    "attempt_kind": "execution",
    "execution_run_id": "ern_01m0wtkz1kng3f31p4cs8b76e8",
    "execution_status": "failed",
    "activity_at": "2026-08-25T16:03:56.602182+00:00",
    "started_at": "2026-08-25T16:03:56.602182+00:00",
    "ended_at": "2026-08-25T18:37:16.827813+00:00",
    "error_summary": (
        "engine sessions terminal: exs_01m0wtkz1kng3f31p4cs8b76e1=HALTED:account_invariant_halt:572"
    ),
    "weight_count": 15,
    "order_count": 0,
    "filled_count": 0,
    "rejected_count": 0,
    "skipped_count": 0,
    "avg_slippage_bps": None,
    "avg_slippage_lane": "SESSION_ARRIVAL_LEDGER_PRICE_ONLY",
    "slippage_measured_sessions": 0,
    "quality_episode_count": 15,
    "quality_sealed_receipt_count": 15,
    "quality_provisional_count": 0,
    # A8 F-2's proposed server fields — the surfaces lane's half.
    "session_outcomes": {
        "SKIPPED/NO_DELTA": 14,
        "HALTED/account_invariant_halt:572": 1,
    },
    "halt": {"event_id": 572, "reason": "account_invariant_halt"},
}

#: A normal multi-episode bar that filled some legs, held some, and
#: refused two under the venue minimum.
MIXED_ROW: dict = {
    "attempt_id": "ern_01m1fpn80j7n4ghzmh2xjqj6xj",
    "attempt_kind": "execution",
    "execution_run_id": "ern_01m1fpn80j7n4ghzmh2xjqj6xj",
    "execution_status": "completed",
    "activity_at": "2026-09-02T00:00:18.456058+00:00",
    "weight_count": 11,
    "order_count": 16,
    "filled_count": 6,
    "rejected_count": 0,
    "skipped_count": 1,
    "avg_slippage_bps": -0.641077210475534,
    "avg_slippage_lane": "SESSION_ARRIVAL_LEDGER_PRICE_ONLY",
    "slippage_measured_sessions": 5,
    "quality_episode_count": 11,
    "quality_sealed_receipt_count": 11,
    "quality_provisional_count": 0,
    "session_outcomes": {
        "FILLED/target_filled": 5,
        "SKIPPED/NO_DELTA": 4,
        "SKIPPED/BELOW_MIN_TRADE": 2,
    },
}

#: A single-episode bar, which is the ONLY shape keel-api serves a
#: run-level `quality_summary` for. No post-cutover production run has
#: this shape (A8 F-1) — it is here so the multi-episode sentence has an
#: honest twin to be distinguished from.
SINGLE_EPISODE_ROW: dict = {
    "attempt_id": "ern_single",
    "attempt_kind": "execution",
    "execution_run_id": "ern_single",
    "execution_status": "completed",
    "activity_at": "2026-09-02T00:00:18.456058+00:00",
    "weight_count": 1,
    "order_count": 1,
    "filled_count": 1,
    "rejected_count": 0,
    "skipped_count": 0,
    "avg_slippage_bps": -0.4185145399,
    "avg_slippage_lane": "SESSION_ARRIVAL_LEDGER_PRICE_ONLY",
    "slippage_measured_sessions": 1,
    "quality_episode_count": 1,
    "quality_sealed_receipt_count": 1,
    "quality_provisional_count": 0,
    "quality_summary": {
        "receipt_id": "rcp_01m1fpn80e3b412yzdadnj5r9k",
        "receipt_version": 1,
        "session_id": "exs_01m1fpn80e3b412yzdadnj5r9k",
        "intent_rev": 1,
        "state": "SEALED",
        "outcome": "FILLED",
        "terminal_reason": "target_filled",
        "decision_bps": "1.2340",
        "arrival_bps": "-0.4185145399",
        "completeness": "COMPLETE",
    },
}

#: A bar the engine has accepted but not yet written sessions for
#: (Q-0626). Its zeros mean "not dispatched", never "traded nothing".
PENDING_ROW: dict = {
    "attempt_id": "ern_pending",
    "attempt_kind": "execution",
    "execution_run_id": "ern_pending",
    "execution_status": "executing",
    "activity_at": "2026-09-02T00:00:18.456058+00:00",
    "weight_count": 11,
    "order_count": 0,
    "filled_count": 0,
    "rejected_count": 0,
    "skipped_count": 0,
    "order_dispatch": "pending",
}

#: Deployment-level, not row-level: `account_warnings` rides on
#: `GET /v1/deployments/{id}`, which is why it is not on any row above.
#: Shaped from the ORDERS_SKIPPED_MIN_NOTIONAL warning A8 F-2 names as the
#: one place BELOW_MIN_TRADE reaches a user today.
ACCOUNT_WARNINGS: list = [
    {
        "kind": "ORDERS_SKIPPED_MIN_NOTIONAL",
        "account_value": 611.42,
        "min_notional": 10.0,
        "detail": "24 of 101 orders in the last 7 days were under the $10 venue minimum.",
        "orders_skipped": 24,
        "orders_total": 101,
        "window_days": 7,
    }
]

#: EXECUTION-ROW keys the fixtures carry that a renderer owns. This tuple
#: is the NON-VACUITY anchor for the no-raw-JSON guard: it is a property
#: of the FIXTURES, so popping a renderer (the seed below) cannot move it
#: (2026-08-25 lesson).
COVERED_KEYS = (
    "avg_slippage_bps",
    "session_outcomes",
    "halt",
    "quality_episode_count",
    "quality_summary",
    "order_dispatch",
)

ALL_ROWS = (HALTED_ROW, MIXED_ROW, SINGLE_EPISODE_ROW, PENDING_ROW)

#: Every registered renderer -> a fixture value that exercises it. The
#: completeness check below reads this, so a new renderer without a
#: fixture fails rather than shipping untested.
RENDERER_FIXTURES: dict = {
    "avg_slippage_bps": MIXED_ROW["avg_slippage_bps"],
    "session_outcomes": MIXED_ROW["session_outcomes"],
    "halt": HALTED_ROW["halt"],
    "quality_episode_count": MIXED_ROW["quality_episode_count"],
    "quality_summary": SINGLE_EPISODE_ROW["quality_summary"],
    "order_dispatch": PENDING_ROW["order_dispatch"],
    "account_warnings": ACCOUNT_WARNINGS,
}


def _has_raw_json(text: str) -> bool:
    """True when a rendered block still contains a serialized structure.

    Keyed on `{"` / `": ` / `[{`, which no rendered sentence produces and
    every `json.dumps` of a non-empty mapping does.
    """
    return '{"' in text or '": ' in text or "[{" in text


# ─── avg_slippage_bps: the number never travels alone ────────────────────


def test_avg_slippage_carries_its_lane_and_population():
    line = format_field_line("avg_slippage_bps", MIXED_ROW["avg_slippage_bps"], MIXED_ROW)
    assert "-0.641 bps" in line
    # Which slippage this is (Q-0342) …
    assert "SESSION_ARRIVAL_LEDGER_PRICE_ONLY" in line
    # … and over how many sessions (Q-0111).
    assert "5 sessions measured" in line


def test_avg_slippage_null_reads_as_absence_never_as_zero():
    line = format_field_line("avg_slippage_bps", None, HALTED_ROW)
    assert "—" in line
    assert "0.000 bps" not in line
    assert "0 sessions measured" in line


def test_avg_slippage_without_a_population_says_so_rather_than_guessing():
    """An older keel-api pod ships no `slippage_measured_sessions`.

    Rendering that as "0 sessions measured" would invent a population the
    server never reported.
    """
    row = {"avg_slippage_bps": -1.5, "avg_slippage_lane": "SESSION_ARRIVAL_LEDGER_PRICE_ONLY"}
    line = format_field_line("avg_slippage_bps", -1.5, row)
    assert "population not reported" in line
    assert "0 sessions" not in line


def test_avg_slippage_singular_session_is_not_pluralized():
    line = format_field_line(
        "avg_slippage_bps",
        SINGLE_EPISODE_ROW["avg_slippage_bps"],
        SINGLE_EPISODE_ROW,
    )
    assert "1 session measured" in line


# ─── session_outcomes: A8 F-2's per-bar reason ───────────────────────────


def test_session_outcomes_renders_the_server_vocabulary_verbatim():
    line = format_field_line("session_outcomes", MIXED_ROW["session_outcomes"], MIXED_ROW)
    assert "11 sessions" in line
    # Highest count first, tokens exactly as the server wrote them.
    assert "FILLED/target_filled 5" in line
    assert "SKIPPED/NO_DELTA 4" in line
    assert "SKIPPED/BELOW_MIN_TRADE 2" in line
    assert line.index("FILLED/target_filled") < line.index("SKIPPED/BELOW_MIN_TRADE")
    assert not _has_raw_json(line)


def test_session_outcomes_distinguishes_a_refused_bar_from_a_flat_one():
    """The whole point of F-2: `order_count == 0` is ambiguous, this is not."""
    flat = format_field_line("session_outcomes", {"SKIPPED/NO_DELTA": 11}, {})
    refused = format_field_line("session_outcomes", {"SKIPPED/BELOW_MIN_TRADE": 11}, {})
    assert flat != refused
    assert "NO_DELTA" in flat and "BELOW_MIN_TRADE" not in flat
    assert "BELOW_MIN_TRADE" in refused


def test_session_outcomes_absent_or_empty_renders_nothing_invented():
    """The server does not serve this field YET (the surfaces lane owns it).

    Until it does, a row without it must render no sessions line at all —
    never "0 sessions", which would be a claim the CLI cannot support.
    """
    assert render_field("session_outcomes", None, {}) is None
    assert render_field("session_outcomes", {}, {}) is None
    block = format_execution_row(MIXED_ROW | {"session_outcomes": None})
    assert "session_outcomes" not in block
    assert "0 sessions —" not in block


# ─── halt: words, not an internal session id ─────────────────────────────


def test_halt_prefers_the_server_rendered_detail():
    """Same contract as `AccountWarning.detail`: render it, never re-derive."""
    detail = "Trading is halted on this account after an invariant breach (event 572)."
    line = format_field_line("halt", {"event_id": 572, "reason": "x", "detail": detail}, {})
    assert line == f"halt: {detail}"


def test_halt_without_a_detail_names_the_event_and_reason_plainly():
    line = format_field_line("halt", HALTED_ROW["halt"], HALTED_ROW)
    assert "account HALTED" in line
    assert "572" in line
    assert "account_invariant_halt" in line
    assert not _has_raw_json(line)


def test_halt_absent_renders_nothing():
    assert render_field("halt", None, {}) is None
    assert "halt:" not in format_execution_row(MIXED_ROW)


# ─── account_warnings: the server's sentence, verbatim ───────────────────


def test_account_warnings_render_detail_and_never_recompute_it():
    line = format_field_line("account_warnings", ACCOUNT_WARNINGS, {})
    assert "24 of 101 orders in the last 7 days were under the $10 venue minimum." in line
    assert "[ORDERS_SKIPPED_MIN_NOTIONAL]" in line
    # The numbers are the server's to phrase — we must not have built our
    # own sentence out of account_value / min_notional.
    assert "611.42" not in line
    assert not _has_raw_json(line)


def test_account_warning_without_detail_says_so_rather_than_inventing_one():
    line = format_field_line("account_warnings", [{"kind": "EXITS_BELOW_VENUE_MINIMUM"}], {})
    assert "(no detail served)" in line


# ─── receipts are per episode (A8 F-1) ───────────────────────────────────


def test_multi_episode_run_says_receipts_are_per_episode():
    line = format_field_line("quality_episode_count", 11, MIXED_ROW)
    assert "11 episodes" in line
    assert "11 sealed receipts" in line
    assert "no single receipt" in line
    assert "keel live receipt" in line


def test_single_episode_run_does_not_claim_multiple():
    line = format_field_line("quality_episode_count", 1, SINGLE_EPISODE_ROW)
    assert "1 episode" in line
    assert "1 sealed receipt" in line
    assert "no single receipt" not in line


def test_quality_summary_renders_the_receipt_address_and_economics():
    line = format_field_line(
        "quality_summary", SINGLE_EPISODE_ROW["quality_summary"], SINGLE_EPISODE_ROW
    )
    assert "SEALED" in line
    assert "FILLED/target_filled" in line
    assert "decision 1.234 bps" in line
    assert "arrival -0.419 bps" in line
    assert "exs_01m1fpn80e3b412yzdadnj5r9k" in line
    assert "rev 1" in line
    assert not _has_raw_json(line)


# ─── Q-0626's third state ────────────────────────────────────────────────


def test_pending_dispatch_is_not_reported_as_traded_nothing():
    block = format_execution_row(PENDING_ROW)
    assert "not dispatched" in block
    assert "orders 0" in block


# ─── The headline guard: no raw JSON on an execution row ─────────────────


@pytest.mark.parametrize(
    "row",
    ALL_ROWS,
    ids=["halted", "mixed", "single_episode", "pending"],
)
def test_execution_row_renders_no_raw_json(row):
    # Non-vacuity: read the FIXTURE, not the registry. The seed in the
    # control test below pops a renderer, which cannot move this count.
    covered = [key for key in COVERED_KEYS if row.get(key) not in (None, {}, [])]
    assert covered, f"fixture {row['attempt_id']} exercises no rendered field"

    block = format_execution_row(row)
    assert not _has_raw_json(block), f"raw JSON survived rendering:\n{block}"
    assert row["attempt_id"] in block


def test_removing_a_renderer_puts_raw_json_back(monkeypatch):
    """Proof the guard above can FAIL, with an honest control arm.

    Seed: drop `session_outcomes` from the registry. Its line must revert
    to serialized JSON (the guard reddens); the identical row rendered
    with the registry intact must stay clean (the control), so the guard
    is reacting to the RENDERER and not to the fixture.
    """
    control = format_execution_row(HALTED_ROW)
    assert not _has_raw_json(control)

    monkeypatch.delitem(FIELD_RENDERERS, "session_outcomes")
    seeded = format_execution_row(HALTED_ROW)

    assert _has_raw_json(seeded), (
        "seeding the defect did not reintroduce raw JSON — the no-raw-JSON "
        "check cannot fail, so it proves nothing"
    )
    assert '"SKIPPED/NO_DELTA": 14' in seeded


def test_every_registered_renderer_is_exercised_by_a_fixture():
    """No renderer ships untested (and the fixture set is non-empty)."""
    assert set(FIELD_RENDERERS) == set(RENDERER_FIXTURES), (
        "a renderer without a fixture (or a fixture without a renderer): "
        f"{sorted(set(FIELD_RENDERERS) ^ set(RENDERER_FIXTURES))}"
    )
    assert RENDERER_FIXTURES
    for key, value in RENDERER_FIXTURES.items():
        context = HALTED_ROW if key == "halt" else MIXED_ROW
        rendered = render_field(key, value, context)
        assert rendered, f"{key} rendered nothing for its fixture"
        assert not _has_raw_json(rendered), f"{key} leaked raw JSON: {rendered}"
    # Row-level coverage is a separate, fixture-owned fact.
    for key in COVERED_KEYS:
        assert any(row.get(key) not in (None, {}, []) for row in ALL_ROWS), (
            f"{key} is a row-level renderer but no execution-row fixture carries it"
        )


# ─── Row detection + the shared entry points ─────────────────────────────


def test_is_execution_rows_is_structural_not_a_guess():
    assert is_execution_rows(list(ALL_ROWS))
    assert not is_execution_rows([])
    assert not is_execution_rows([{"symbol": "BTC", "weight": 1.0}])
    assert not is_execution_rows({"attempt_id": "x", "execution_status": "y"})
    # One row missing a required key disqualifies the whole payload.
    assert not is_execution_rows([MIXED_ROW, {"attempt_id": "x"}])


def test_format_human_routes_execution_rows_through_the_renderers():
    """`format_human` is the finding's cited site — it must not one-line JSON."""
    text = format_human(list(ALL_ROWS))
    assert not _has_raw_json(text)
    assert "SKIPPED/BELOW_MIN_TRADE 2" in text
    assert "SESSION_ARRIVAL_LEDGER_PRICE_ONLY" in text


def test_format_human_dict_uses_the_renderers_for_known_fields():
    text = format_human({"view": "executions", "data": [MIXED_ROW]})
    assert "view: executions" in text
    assert not _has_raw_json(text)
    assert "11 sessions" in text


def test_unknown_nested_values_keep_their_surface_default():
    """Only registered fields change; everything else renders as before."""
    inline = format_field_line("params", {"period": 8}, {}, nested="inline")
    assert inline == 'params: {"period": 8}'
    indented = format_field_line("params", {"period": 8}, {}, nested="indent")
    assert indented.startswith("params: {\n")


def test_format_execution_rows_preserves_server_order():
    text = format_execution_rows([MIXED_ROW, HALTED_ROW])
    assert text.index(MIXED_ROW["attempt_id"]) < text.index(HALTED_ROW["attempt_id"])
