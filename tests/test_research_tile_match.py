"""SDK-FIX (MCP-FINAL phase 2 S1, staging fc5ff7629a): a Compare read's "In short" printed the FIRST result's Sharpe
for every result ("0.87" and "0.87" for 0.87 and −1.12; "0.76" three times for 0.76, −0.16, −0.089). keel-api served
the right words; ``_research_view._as_drawn`` (D9, 87e6857ada) matched a number to its card tile by ``(ref,
measure)``, and a Compare serves one ``("base.summary", "sharpe")`` tile per result. The tile is now the item's own
only when it is the same number (ref, measure and served value; label where several still qualify); otherwise the
item keeps its own words.

SEED (run, then reversed): ``_as_drawn`` keyed on ``(ref, measure)`` alone (the 87e6857ada body) -> the two Compare
tests and the two-bar Check test red. Control: the D9 parity test (test_research_mcp_final) and the agreeing study_done
fixture still read every tile's words (non-vacuity: every Compare tile is printed, three tiles matched in a Check).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from keel.tools.outcomes import _bootstrap
from keel.tools.outcomes import _research_view as rv

from tests.test_research_view import _shape


_bootstrap()

#: Two staging Compare runs' card_summary, as served (see the file's _provenance).
_COMPARES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_compare_tiles.json").read_text()
)


def _compare_env(name: str) -> dict:
    """The study read of a served Compare: no bars, its headline measures the card's numbers in the served order
    (``research.lifecycle`` builds both from one list), each with its own served words."""
    served = _COMPARES[name]
    env = _shape("study_done")
    env.pop("checks", None)
    if served.get("share_id"):  # a share read names its share, never a study
        env["study"].pop("study_id", None)
        env["study"]["share_id"] = served["share_id"]
    else:
        env["study"]["study_id"] = served["study_id"]
    env["card_summary"] = copy.deepcopy(served["card_summary"])
    env["headline"] = {
        "sentence": served["card_summary"]["headline"]["text"],
        "measures": copy.deepcopy(served["card_summary"]["numbers"]),
    }
    return env


def _in_short(env: dict) -> list[str]:
    lines = rv.study_text(env).splitlines()
    start = lines.index("In short:")
    return [line for line in lines[start + 1 :] if line.startswith("- ")]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "two",
            [
                ("Sharpe (daily) over the window, MACD Crossover Strategy (Crypto)", "0.87"),
                (
                    "Sharpe (daily) over the window, RSI Oversold-Recovery Strategy (Crypto)",
                    "−1.12",
                ),
            ],
        ),
        (
            "three",
            [
                ("Sharpe (daily) over the window, Momentum Strategy on Hyperliquid", "0.76"),
                ("Sharpe (daily) over the window, Regime Rotation (BTC Trend Switch)", "−0.16"),
                ("Sharpe (daily) over the window, MA Crossover (Golden Cross) Strategy", "−0.089"),
            ],
        ),
    ],
)
def test_each_compared_result_says_its_own_sharpe(
    name: str, expected: list[tuple[str, str]]
) -> None:
    lines = _in_short(_compare_env(name))
    tiles = _COMPARES[name]["card_summary"]["numbers"]
    assert len(lines) == len(tiles) >= len(expected) + 1, (
        lines
    )  # non-vacuity: every served tile is printed
    for label, said in expected:
        line = next(line for line in lines if line.startswith(f"- {label}: "))
        assert line.startswith(f"- {label}: {said}, "), line
    # every line carries its own tile's words, in the served order (the card and the text agree, D9)
    for tile, line in zip(tiles, lines, strict=True):
        assert line.startswith(f"- {tile['label']}: {tile['value_words']}, "), (tile, line)
        assert tile["sample_words"] in line, (tile, line)


@pytest.mark.parametrize(
    ("name", "next_day", "week"),
    [
        ("signal", "−0.017", "0.012"),  # stu_01m7rg6pk79460y9c5ck1cgm35: the text said −0.017 twice
        ("share_momentum", "−0.004", "0.015"),  # share sM45cwd8rQqG8m7kSwfg9Dg: −0.004 twice
        ("share_funding", "0.012", "0.027"),  # share 48pe1MnJJcUuXeuZDDsDaDi: 0.012 twice
    ],
)
def test_each_signal_horizon_says_its_own_number(name: str, next_day: str, week: str) -> None:
    """MCP-FINAL p2 (the same S1 on a Signal and on curated shares): both horizons are ``("ic", "ic")`` tiles, and
    the "7 days ahead" line printed the next-day value (and its sample). SEED: the old (ref, measure) match -> this
    reds; control: every other tile's line is its own (non-vacuity: four tiles printed)."""
    lines = _in_short(_compare_env(name))
    tiles = _COMPARES[name]["card_summary"]["numbers"]
    assert len(lines) == len(tiles) == 4, lines
    assert lines[0].startswith(f"- How well it ranks, next day: {next_day}, "), lines[0]
    assert lines[1].startswith(f"- How well it ranks, 7 days ahead: {week}, "), lines[1]
    for tile, line in zip(tiles, lines, strict=True):
        assert tile["sample_words"] in line, (tile, line)


def test_two_horizons_with_one_value_are_told_apart_by_label() -> None:
    """Two ``("ic", "ic")`` tiles that happen to share a value: the label narrows, so each horizon draws its own
    tile's sample; control: an item whose label neither tile carries keeps its own words."""
    env = _compare_env("signal")
    tiles = env["card_summary"]["numbers"]
    tiles[1]["value"] = tiles[0]["value"]
    for m in env["headline"]["measures"][:2]:
        m["value"] = tiles[0]["value"]
        m["sample_words"] = "own"
    lines = _in_short(env)
    assert tiles[0]["sample_words"] in lines[0] and tiles[1]["sample_words"] in lines[1], lines
    item = {**env["headline"]["measures"][0], "label": "Neither", "value_words": "mine"}
    assert rv._as_drawn(env, item)["value_words"] == "mine"


def test_two_bars_on_one_measure_each_say_their_own_number() -> None:
    """A Check whose two rows share ``ref`` and ``measure`` (two results, or a bar read at another ``at``): the row
    whose value is the tile's draws the tile's words, the other keeps its own, never the tile's."""
    env = _shape("study_done")
    rows = env["checks"]["rows"]
    costs = next(r for r in rows if (r["ref"], r["measure"]) == ("costs", "sharpe"))
    other = {
        **copy.deepcopy(costs),
        "label": "Sharpe at twice the costs, second result",
        "bar_index": len(rows),
        "value": 0.42,
        "value_words": "0.42",
        "sample_words": "200 days",
    }
    rows.append(other)
    env["checks"]["count"] = len(rows)
    tile = next(
        t for t in env["card_summary"]["numbers"] if (t["ref"], t["measure"]) == ("costs", "sharpe")
    )
    costs["value_words"], costs["sample_words"] = (
        f"{costs['value_words']} (row)",
        "Nov 17, 2025 to Nov 17, 2026",
    )
    shown = {r["label"]: rv.bar_row(rv._as_drawn(env, r), set()) for r in rows}
    assert shown[costs["label"]].startswith(f"- {costs['label']}: {tile['value_words']}, "), shown[
        costs["label"]
    ]
    assert tile["sample_words"] in shown[costs["label"]] and "(row)" not in shown[costs["label"]]
    assert shown[other["label"]].startswith(f"- {other['label']}: 0.42, "), shown[other["label"]]
    assert "200 days" in shown[other["label"]], shown[other["label"]]


def test_an_ambiguous_tile_leaves_the_item_its_own_words() -> None:
    """Two tiles of one ref, measure and value, neither with the item's label: no tile is the item's own, so the
    item keeps its own words (never the first tile's)."""
    env = _compare_env("two")
    first = env["card_summary"]["numbers"][0]
    twin = {**first, "label": "Another result", "value_words": "0.87 (twin)"}
    env["card_summary"]["numbers"] = [first, twin, *env["card_summary"]["numbers"][1:]]
    item = {**first, "label": "Neither tile's label", "value_words": "0.869"}
    assert rv._as_drawn(env, item)["value_words"] == "0.869"
    # control: the item whose label one tile carries takes that tile's words
    assert rv._as_drawn(env, {**twin, "value_words": "x"})["value_words"] == "0.87 (twin)"
    # an item with no value draws no tile's words (its served reason stays its own)
    assert (
        rv._as_drawn(env, {**first, "value": None, "value_words": "not measured"})["value_words"]
        == "not measured"
    )


# ── JOURNEYS J5/J6 S2: a Check says the strategy as it stands before its bars ────────────────────────────────────


def _stands(env: dict) -> list[dict]:
    """``headline.stands`` as keel-api serves it (research.card_summary.STANDS_ITEMS, worded by research.words)."""
    base = next(
        m
        for m in env["headline"]["measures"]
        if (m["ref"], m["measure"]) == ("base.summary", "sharpe")
    )
    return [
        {**base, "measure": "total_return", "label": "Total return over the window", "value": -0.077,
         "unit": "fraction", "value_words": "−7.7%"},
        copy.deepcopy(base),
        {**base, "measure": "max_drawdown", "label": "Max drawdown over the window", "value": -0.40,
         "unit": "fraction", "value_words": "−40%"},
    ]  # fmt: skip


def test_a_check_says_the_strategy_as_it_stands_before_its_bars() -> None:
    """J5: the real agent read a paired interval as the strategy's Sharpe because a Check read never said the
    strategy's own result. SEED (run, then reversed): ``_headline_lines`` returning [] for a study with bars (the
    old early return) -> this reds; control: the Sharpe line draws its card tile's words (D9)."""
    env = _shape("study_done")
    env["headline"]["stands"] = _stands(env)
    text = rv.study_text(env).splitlines()
    start, bars = (
        text.index("In short:"),
        next(i for i, line in enumerate(text) if line.startswith("Your checks (")),
    )
    assert start < bars, text
    said = text[start + 1 : bars]
    tile = next(
        t
        for t in env["card_summary"]["numbers"]
        if (t["ref"], t["measure"]) == ("base.summary", "sharpe")
    )
    assert [line.split(":", 1)[0] for line in said] == [
        "- Total return over the window",
        "- Sharpe (daily) over the window",
        "- Max drawdown over the window",
    ], said
    assert said[0].startswith("- Total return over the window: −7.7%, ") and "−40%" in said[2], said
    assert said[1].startswith(f"- Sharpe (daily) over the window: {tile['value_words']}, "), said


def test_a_check_with_no_stands_served_says_the_measures_no_bar_states() -> None:
    """A server that serves no ``stands`` (before SDK-FIX): the headline measures no bar row already states (the
    base Sharpe), never a bar's number twice. Control: a study with no bars still says all its measures."""
    env = _shape("study_done")
    text = rv.study_text(env).splitlines()
    start = text.index("In short:")
    said = text[
        start + 1 : text.index(next(line for line in text if line.startswith("Your checks (")))
    ]
    assert [line.split(":", 1)[0] for line in said] == ["- Sharpe (daily) over the window"], said
    bare = _shape("study_done")
    bare.pop("checks")
    assert len(_in_short(bare)) == len(bare["headline"]["measures"][:4]) >= 3


# ── JOURNEYS J5/J6 S2: a done Map says which settings came out best ───────────────────────────────────────────────

#: J5's first Map (stu_01m7xm2zs6vqbs74115xn8dvd6 on staging fc5ff7629a), card_summary.top5 as served.
_MAP_TOP5 = {
    "kind": "best_settings",
    "rows": [
        {"label": "hold 3 · lookback 28", "value": 1.3701819313695736, "unit": "ratio", "slot": 0, "value_words": "1.37"},
        {"label": "hold 7 · lookback 10", "value": 1.2179517000505555, "unit": "ratio", "slot": 0, "value_words": "1.22"},
        {"label": "hold 14 · lookback 14", "value": 0.9335086061236378, "unit": "ratio", "slot": 0, "value_words": "0.93"},
    ],
    "total": 25,
}  # fmt: skip


def test_a_done_map_says_its_best_settings() -> None:
    """J5/J6: a Map's read gave only its nearby numbers, never which settings came out best. SEED (run, then
    reversed): ``_best_settings_line`` returning None -> this reds; control: a Check's top rows (kind checks) give no
    such line."""
    env = _compare_env("two")
    env["card_summary"]["top5"] = copy.deepcopy(_MAP_TOP5)
    text = rv.study_text(env).splitlines()
    assert (
        "Best settings (of 25): hold 3 · lookback 28 at 1.37; hold 7 · lookback 10 at 1.22; hold 14 · lookback 14 at 0.93"
        in text
    ), text
    check = _shape("study_done")
    assert not any(line.startswith("Best settings") for line in rv.study_text(check).splitlines())


# ── JOURNEYS J5/J6 S3: a plan says a lock once, and a map over the bound once ─────────────────────────────────────

_NI = "not included in this account's plan"


def _locked(label: str, **lock) -> dict:
    return {"id": label.lower().replace(" ", "_"), "label": label, "to_run": 0, "runnable": False,
            "locked": {"unit": "feature:research_template_map", "words": f"{label}: {_NI}.", **lock}}  # fmt: skip


def test_a_plan_says_the_plain_lock_once_for_every_step_it_locks() -> None:
    """J5 (a Free Check plan): "Every setting is not included in this account's plan · Re-picking the settings as
    you go is not included … · Nearby settings is not included …". SEED (run, then reversed): each plain lock its own
    part (the old line) -> the clause count reds (3 != 1); control: a size lock keeps its own words."""
    plan = {
        "study": {
            "title": "momentum v3 v1",
            "kind_label": {"key": "check", "name": "Check a strategy"},
        },
        "writes": "a new study, version 1",
        "words": {"work": "20 runs"},
        "steps": [
            {"id": "base", "label": "The backtest", "to_run": 2, "runnable": True},
            _locked("Every setting"),
            _locked("Re-picking the settings as you go"),
            _locked("Nearby settings"),
            {
                "id": "dsr",
                "label": "Sharpe discounted for versions tried",
                "to_run": 1,
                "runnable": True,
            },
        ],
        "issues": [],
        "plan_id": "pln_" + "a" * 64,
        "run_allowed": True,
    }
    text = rv.plan_text(plan)
    steps = next(line for line in text.splitlines() if line.startswith("Steps ("))
    assert steps == (
        "Steps (5): The backtest: 2 runs · Every setting, Re-picking the settings as you go, Nearby settings: "
        f"{_NI} · Sharpe discounted for versions tried: 1 run"
    ), steps
    assert text.count(_NI) == 1, text


def test_a_map_over_the_bound_says_its_size_once() -> None:
    """J6 w14 (a 6×6 map on a 25-setting account): the size was said on each of three Steps parts, then as the
    TOO_MANY_BACKTESTS issue, then as the wall. SEED (run, then reversed): ``walled`` false -> the size returns to the
    Steps line and the issue line (this reds); control: with no wall, each step keeps its served words."""
    size = "This map has 36 settings; the most this account's plan can run is 25. Narrow an axis."
    reads = "Before and after the split: the map it reads has 36 settings; the most this account's plan can run is 25."
    plan = {
        "study": {"title": "momentum v3 v1", "kind_label": {"key": "map", "name": "Map a strategy's settings"}},
        "writes": "a new study, version 1",
        "words": {"work": "14 runs, 25 reused", "limit": f"{size} Pro includes more."},
        "steps": [
            {"id": "grid", "label": "Every setting", "to_run": 0, "runnable": False,
             "locked": {"unit": "research_backtests_max", "words": size, "used": 36, "limit": 25}},
            {"id": "split", "label": "Before and after the split", "to_run": 0, "runnable": False,
             "locked": {"unit": "research_backtests_max", "words": reads,
                        "reads": {"step": "grid", "used": 36, "limit": 25}}},
        ],
        "issues": [{"code": "STUDY_PLAN_TOO_MANY_BACKTESTS", "path": "/steps/grid",
                    "message": "This map has 36 settings; this account runs up to 25 in one study."}],
        "plan_id": "pln_" + "b" * 64,
        "run_allowed": False,
    }  # fmt: skip
    text = rv.plan_text(plan)
    assert text.count("36 settings") == 1, text  # the wall alone
    assert (
        "Steps (2): Every setting: too many settings (below) · Before and after the split: too many"
        in text
    )
    unwalled = rv.plan_text({**plan, "words": {"work": "14 runs, 25 reused"}})
    assert (
        size.rstrip(".") in unwalled and reads.rstrip(".") in unwalled
    )  # control: their own words


def test_the_cli_exits_non_zero_on_a_plan_run_refuses() -> None:
    """J6 w14: the CLI printed the map wall and exited 0. SEED (run, then reversed): ``_refused_plan`` returning False
    -> this reds; controls: a runnable plan and another tool are not refused."""
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._cli_adapter import _refused_plan

    plan_tool = OUTCOMES["keel_research_plan"]
    walled = OutcomeResult(
        extra={"run_allowed": False, "words": {"limit": "This map has 36 settings; …"}}
    )
    assert _refused_plan(plan_tool, walled) is True
    assert (
        _refused_plan(plan_tool, OutcomeResult(extra={"run_allowed": True, "words": {}})) is False
    )
    assert _refused_plan(OUTCOMES["keel_research_get"], walled) is False


def test_a_measure_frozen_with_no_sample_says_none() -> None:
    """MCP-FINAL p2 (share 3aSZNbugZKepjGbKJzDW5ge, a Map): every In-short line ended "sample not stated", as the
    snapshot froze its Neighbours numbers with ``sample: {}``. SEED (run, then reversed): the old ``_sample`` tail ->
    this reds; control: a measure with served sample words keeps them."""
    env = _compare_env("two")
    env["headline"]["measures"] = [
        {"label": "Rank among nearby settings", "value": 2.0, "unit": "count", "ref": "nearby.summary",
         "measure": "rank_among", "sample": {}, "value_words": "2"},
        {**env["headline"]["measures"][0]},
    ]  # fmt: skip
    env["card_summary"]["numbers"] = []
    lines = _in_short(env)
    assert lines[0] == "- Rank among nearby settings: 2, no interval", lines
    assert lines[1].endswith("; 365 days"), lines


def test_a_plan_refusal_says_its_near_miss_once() -> None:
    """MCP-FINAL p2 (p2-plan-fromshare.json): "… is not in Momentum + Funding Strategy on Hyperliquid (6 steps). Did
    you mean step[1].branch[momentum].step[0].period (ROC)? Did you mean … (ROC)?". SEED (run, then reversed): the
    suggestion always appended -> this reds; control: a fix the message does not carry is still written."""
    from keel.tools.outcomes._research_commands import refused_text

    near = "Did you mean step[1].branch[momentum].step[0].period (ROC)?"
    issue = {"code": "STUDY_ADDRESS_NOT_FOUND", "path": "/steps/grid/axes/0", "severity": "error",
             "message": f"var[selection].step[0].period is not in Momentum + Funding (6 steps). {near}",
             "suggestion": near, "valid": ["step[1].branch[momentum].step[0].period"]}  # fmt: skip
    text = refused_text({"issues": [issue]})
    assert text.count("Did you mean") == 1, text
    other = {**issue, "message": "var[selection].step[0].period is not in Momentum + Funding (6 steps).",
             "suggestion": "Copy an address from the address list."}  # fmt: skip
    assert "(6 steps). Copy an address from the address list." in refused_text({"issues": [other]})
