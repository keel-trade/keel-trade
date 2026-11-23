"""The first-week clause, pinned once (Q-7601 fix round, doc 07 §3.8).

The staging probe of 2026-11-16 showed every notice, wall and plan-usage line
doubling with "0 of 50 first-week backtests left until Mon 23 Nov 18:37 UTC,
then 18 for this 30-day period": long, a banned window phrase, and read as if
nothing could run until 23 Nov. The rule then: a clause ONLY while the first
week has some left, folded into the backtests line in parentheses, date only;
none once it is spent or expired. Plans v3 (PLANS-REVIEW R16): a bonus that
ends by the reset reads as the period's allowance beside it ("(25 plus your
first-week 50); resets to 25 on Dec 16"), spent or not; a bonus that runs past
the reset keeps the pre-v3 clause. The structured ``first_week`` stays in data.

``fixtures/plan_limits_first_week_golden.json`` is the one golden: inputs in
the probe's shapes, the exact words out. Every other first-week test computes
its expectation from its own data.
"""

import json
from pathlib import Path

import pytest
from keel.errors import (
    render_first_week_clause,
    render_quota_notice,
    translate_http_error,
)
from keel.tools.outcomes.plan_status import _metered_line


GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "plan_limits_first_week_golden.json").read_text()
)


def _wall_body(quota: dict) -> str:
    return json.dumps(
        {
            "title": "Forbidden",
            "status": 403,
            "detail": "Insufficient entitlements",
            "code": quota["code"],
            "quota": quota,
        }
    )


def test_golden_is_not_vacuous():
    """Four cases, two with the first week still running and two spent."""
    cases = {k: v for k, v in GOLDEN.items() if not k.startswith("_")}
    assert set(cases) == {
        "notice_with_extras",
        "notice_extras_spent",
        "wall_extras_spent",
        "plan_usage_start",
    }
    assert all(isinstance(c["expected"], str) and c["expected"] for c in cases.values())


def test_the_notice_folds_the_clause_while_extras_are_left():
    """SEED (run 2026-11-16, reversed): `render_quota_sentence` dropping the
    clause reds this test alone; the spent arms stay green."""
    case = GOLDEN["notice_with_extras"]
    assert render_quota_notice(case["block"]) == case["expected"]


def test_the_notice_says_nothing_once_the_extras_are_spent():
    """SEED (run 2026-11-16, reversed): `render_first_week_clause` without its
    `remaining <= 0` guard reds this test and the wall arm with
    "(0 first-week extras until Mon 23 Nov)"; the running arms stay green."""
    case = GOLDEN["notice_extras_spent"]
    assert render_quota_notice(case["block"]) == case["expected"]
    assert render_first_week_clause(case["block"]) is None


def test_the_wall_has_no_clause_and_keeps_the_structured_first_week():
    case = GOLDEN["wall_extras_spent"]
    e = translate_http_error(403, _wall_body(case["quota"]))
    assert str(e) == case["expected"]
    assert e.input["first_week"] == case["first_week"]


def test_plan_usage_folds_the_clause_into_the_backtests_line():
    """SEED (run 2026-11-16, reversed): `plan_status._metered_line` dropping
    the clause reds this test alone."""
    case = GOLDEN["plan_usage_start"]
    assert _metered_line(case["block"]) == case["expected"]


@pytest.mark.parametrize("name", sorted(k for k in GOLDEN if not k.startswith("_")))
def test_no_golden_line_names_a_window_or_a_time(name):
    """§3.8: no window phrase, and the sentence carries the date only."""
    text = GOLDEN[name]["expected"]
    assert "period" not in text and "UTC" not in text and "then " not in text, text


def test_the_clause_counts_backtests_only():
    """Control: the same running first week on the compute block renders no
    clause (connect-onboarding spec 01 §1.9: the clause is a backtest count)."""
    block = {**GOLDEN["notice_with_extras"]["block"], "unit": "backtest_compute_seconds"}
    assert render_first_week_clause(block) is None
    assert render_first_week_clause(GOLDEN["notice_with_extras"]["block"]) == (
        "(12 first-week extras until Nov 23)"
    )
