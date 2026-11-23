"""G1, the SDK half (value-and-pricing spec 02 §F): every unit a tool can hit has a before / near / at row in
``keel.limit_copy``, and every row renders byte-identically to the table platform_auth generates from plans.yaml
(``libs/platform_auth/fixtures/limit_copy_walls.json``).

The SDK venv has no ``platform_auth``, so the server's half is read from what it ships: the walls fixture (every
catalog plan x row x slot, with the facts it was rendered from), plans.yaml (the units), and the libs table of units
no agent wall names (``NOT_AN_AGENT_LIMIT``, a dict literal read from source, the way
``test_limit_unit_labels_match_the_served_label_table`` reads ``UNIT_LABELS``).
"""

from __future__ import annotations

import ast
import json
from datetime import datetime

import pytest
from keel import limit_copy as lc

from . import _plan_catalog as cat


WALLS = cat.REPO_ROOT / "libs" / "platform_auth" / "fixtures" / "limit_copy_walls.json"
LIBS_LIMIT_COPY = cat.REPO_ROOT / "libs" / "platform_auth" / "limit_copy.py"


def _table() -> dict:
    return json.loads(WALLS.read_text())


def _walls(key: str = "walls") -> list[dict]:
    return _table()[key]


def _libs_exempt() -> dict[str, str]:
    tree = ast.parse(LIBS_LIMIT_COPY.read_text())
    return next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and getattr(node.target, "id", "") == "NOT_AN_AGENT_LIMIT"
    )


def _facts(raw: dict) -> dict:
    out = dict(raw)
    if isinstance(out.get("resets_at"), str):
        out["resets_at"] = datetime.fromisoformat(out["resets_at"].replace("Z", "+00:00"))
    return out


def _render(wall: dict) -> str | None:
    return lc.render_limit(
        wall["unit"],
        wall["slot"],
        plan_name=cat.display_name(wall["plan"], wall["version"]),
        higher_names=wall["names"],
        **_facts(wall["facts"]),
    )


def _assert_renders(walls: list[dict]) -> None:
    for wall in walls:
        assert _render(wall) == wall["text"], wall
        if wall["suggestion"] is not None:
            assert (
                lc.render_suggestion(wall["unit"], limit=wall["facts"].get("limit"))
                == wall["suggestion"]
            ), wall
        assert lc.row_kind(wall["unit"], wall["facts"].get("limit")) == wall["kind"], wall


def test_every_wall_renders_identically_through_the_sdk():
    """Each (plan, unit, slot) of the server's table, rendered by the SDK from the same served facts, is the same
    text and the same suggestion, and the row's kind is the server's.

    Non-vacuity: the table is non-empty, covers every catalog plan and every slot, and includes the v3 coin cap,
    the split paper floor, the saved-screen cap and the map size."""
    walls = _walls()
    assert _table()["plans_changed"] == lc.PLANS_CHANGED
    assert len(walls) >= 100, len(walls)
    assert {w["plan"] for w in walls} == {p for p, _ in cat.paid_rows()} | {"free"}
    assert {w["slot"] for w in walls} == set(lc.SLOTS)
    units = {w["unit"] for w in walls}
    for needed in (
        "screen_assets_max",
        "paper_clock_floor",
        "screeners_max",
        "research_backtests_max",
    ):
        assert needed in units, needed
    _assert_renders(walls)
    # The words the integrator named (PLANS-REVIEW R3-R7), present in the table the SDK just matched.
    texts = {(w["plan"], w["unit"], w["slot"]): w["text"] for w in walls}
    assert "Trader, Quant and Pro run more." in texts[("free", "paper_strategies_max", "at")]
    assert "Trader, Quant and Pro include more." in texts[("free", "screeners_max", "at")]
    assert "Trader, Quant and Pro screen every coin." in texts[("free", "screen_assets_max", "at")]
    assert texts[("free", "backtest_compute_seconds", "at")].startswith(
        "Backtest time on Free is used up until Dec 5."
    )
    assert not any(w["unit"] == "backtest_compute_seconds" and w["slot"] != "at" for w in walls), (
        "compute is silent: no before or near"
    )
    assert "Screen runs use the same backtests." in texts[("free", "backtest_runs", "at")]
    assert not any("Screen runs use" in w["text"] for w in walls if w["slot"] != "at"), (
        "the screens fact is said only at the backtest wall"
    )
    assert texts[("free", "feature:research_template_signal", "at")].startswith(
        "Signal studies are included on Quant and Pro. Free runs the Check"
    )


def test_every_legacy_wall_renders_identically_through_the_sdk():
    """The same for every other plan an org can hold (v1, v2; PLANS-REVIEW R2, R30): a legacy Trader's higher plans
    name the current Trader ("The current Trader plan, Quant and Pro include more."), and a cap of 0 reads as a
    feature ("Private auto-refreshing screens are included on Trader, Quant and Pro.").

    Non-vacuity: both cases are in the table."""
    walls = _walls("legacy_walls")
    assert len(walls) >= 100, len(walls)
    assert any(
        "the current Trader plan" in w["text"].lower().replace("trader", "Trader") for w in walls
    )
    assert any(w["kind"] == "feature" and not w["unit"].startswith("feature:") for w in walls)
    _assert_renders(walls)


def test_every_first_week_wall_renders_identically_through_the_sdk():
    """A meter's sentences while its first-week bonus is in the count (R16): "20 plus your first-week 20; 20 come
    back on Dec 5". Non-vacuity: every slot of the backtests and AI messages meters is there."""
    walls = _walls("first_week_walls")
    assert {(w["unit"], w["slot"]) for w in walls} >= {
        ("backtest_runs", "before"),
        ("backtest_runs", "near"),
        ("backtest_runs", "at"),
        ("ai_messages", "at"),
    }
    assert any("plus your first-week" in w["text"] for w in walls)
    _assert_renders(walls)


def test_the_tier_rule_is_the_servers():
    """The near rule (R17) is platform_auth's ``quota.tier_for``: the same constants and the same tier at every
    vector of the shared ``tiers`` table (75/90% only on a meter of 10 or more; a smaller meter only at its last
    unit; AI messages at 2 left; compute silent). Non-vacuity: the table walks small, AI, percentage and silent
    meters and holds every tier."""
    tiers = cat.limit_copy_cases()["tiers"]
    assert lc.tier_rule() == tiers["rule"]
    cases = tiers["cases"]
    assert {c["unit"] for c in cases} >= {"research_studies", "ai_messages", "backtest_runs"}
    assert {c["tier"] for c in cases} == {"ok", "notice", "warn", "critical", "exhausted"}
    for case in cases:
        assert lc.tier_for(case["unit"], limit=case["limit"], used=case["used"]) == case["tier"], (
            case
        )
        # Q-8111: the words rule beside it (a silent meter's tier computes; it is never worded until spent)
        assert (
            lc.worded_tier(case["unit"], limit=case["limit"], used=case["used"])
            == case["worded_tier"]
        ), case
    assert lc.tier_for("research_studies", limit=2, used=1) == "critical"
    assert lc.tier_for("research_studies", limit=2, used=0) == "ok"
    assert lc.tier_for("ai_messages", limit=5, used=3) == "critical"
    assert lc.tier_for("backtest_compute_seconds", limit=600, used=590) == "critical"
    assert lc.worded_tier("backtest_compute_seconds", limit=600, used=590) == "ok"


def test_the_every_coin_limit_is_the_servers():
    assert cat.limit_copy_cases()["every_coin_limit"] == lc.EVERY_COIN_LIMIT
    assert sorted(lc.RUN_MORE_UNITS) == cat.limit_copy_cases()["run_more_units"]


def test_the_sdk_rows_are_exactly_the_units_an_agent_can_meet():
    """The SDK's rows are plans.yaml's units plus both floors, less the libs' ``NOT_AN_AGENT_LIMIT``: exactly the
    set platform_auth's G1 (``limit_rows_missing``) holds its own ``LIMIT_ROWS`` to. A new plans.yaml unit reds here
    until the SDK has its row (or the libs table its reason).

    Non-vacuity: the set is non-empty and includes ``screen_assets_max``; the exemption table was read."""
    exempt = _libs_exempt()
    assert "eval_runs" in exempt and len(exempt) >= 10, sorted(exempt)
    expected = (set(cat.units()) | set(lc.FLOOR_UNITS)) - set(exempt)
    assert "screen_assets_max" in expected and len(expected) >= 20, sorted(expected)
    assert set(lc.LIMIT_ROWS) == expected, {
        "missing": sorted(expected - set(lc.LIMIT_ROWS)),
        "extra": sorted(set(lc.LIMIT_ROWS) - expected),
    }
    assert set(lc.LABELS) >= set(lc.LIMIT_ROWS)


def test_the_parity_seed_reds(monkeypatch):
    """SEED: a template drifting from the server's (one word in the coin cap's wall) reds the parity check; the
    control arm (every other row) stays green."""
    import dataclasses

    row = lc.LIMIT_ROWS["screen_assets_max"]
    seeded = dataclasses.replace(row, at=row.at.replace("cover up to", "include up to"))
    monkeypatch.setitem(lc.LIMIT_ROWS, "screen_assets_max", seeded)
    drifted = [w for w in _walls() if _render(w) != w["text"]]
    assert drifted and {w["unit"] for w in drifted} == {"screen_assets_max"}, drifted
    assert all(w["slot"] == "at" for w in drifted)


def test_a_missing_value_raises_rather_than_rendering_a_hole():
    with pytest.raises(ValueError, match="no value for"):
        lc.render_limit("backtest_runs", "at", plan_name="Free", higher_names=[], used=3)


def test_the_top_plan_gets_no_clause_and_no_plans_sentence():
    text = lc.render_limit(
        "screeners_max", "at", plan_name="Pro", higher_names=[], used=50, limit=50
    )
    assert (
        text == "Saved screens: 50 of 50 on Pro. Delete or archive one, or run it without saving."
    )


def test_the_unit_verb_and_tier_seeds_red(monkeypatch):
    """SEED: paper and live caps reading "include more" (no run-more units) reds exactly the paper and live walls,
    with the saved-screen cap's "include more" (the control) still matching; a small-meter threshold of 5 reds the
    tier table on the meters between 5 and 10 only."""
    monkeypatch.setattr(lc, "RUN_MORE_UNITS", frozenset())
    drifted = {w["unit"] for w in _walls() if _render(w) != w["text"]}
    assert drifted == {"paper_strategies_max", "live_strategies_max"}, drifted
    monkeypatch.undo()

    monkeypatch.setattr(lc, "TIER_SMALL_LIMIT", 5)
    wrong = [
        c
        for c in cat.limit_copy_cases()["tiers"]["cases"]
        if lc.tier_for(c["unit"], limit=c["limit"], used=c["used"]) != c["tier"]
    ]
    assert wrong and all(5 <= c["limit"] < 10 for c in wrong), wrong
