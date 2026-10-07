"""A comparison says what differs between its runs, in words (Q-1802).

Founder, real host: two runs compared with "Periods differ (A: 2024-07-27
00:00:00+00:00..2026-09-23 00:00:00+00:00, B: 2024-07-27 00:00:00+00:00..
2026-09-22 00:00:00+00:00) — absolute metrics are not directly comparable."
Two runs submitted either side of 00:00 UTC: the default window ends at
today UTC (half-open), so the later run has one more complete day. The
sentence printed keel-api's raw stamps, named the runs A/B, and called a
99.9%-shared window "not comparable". A live claude.ai run then compared a
library fork against the agent's baseline whose Execution differed (a 20%
band to its edge vs every bar) and was told only "Different strategies".

Every envelope here comes from the REAL `keel_backtest_compare` handler
(HTTP faked at `KeelClient.get`, keel-api's own wire shapes — including its
`"2024-07-27 00:00:00+00:00"` dates), and the card arms render those
envelopes in real Chromium through
``tests/fixtures/cards/c_compare_windows_check.mjs``. The card arms skip
(never silently pass) without node or Playwright.

Proof it can fail: a ``# SEED:`` per test, run 2026-09-23, recorded in the
commit. Proof it is not vacuous: each arm asserts the handler really
compared the windows it was given (per-run `window` blocks) and the card
really drew every curve — quantities no seed below can move.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_compare as _bt_cmp_mod  # noqa: F401
from keel.tools.outcomes._backtest_view import compare_next, compare_windows
from keel.tools.outcomes._base import ToolContext
from keel.widgets import build_card_html


HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_compare_windows_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"

_SRC = """\
Globals(target_timeframe="1d")
Universe(mode="dynamic", top_n=20)
Execution({execution})
Pipeline([
    PriceDataLoader(timeframe="1d"),
    TimeSeriesMeanReversionForecast(k=20),
    ForecastScaler(),
    ForecastWeightNormalizer(),
])
"""
_EVERY_BAR = 'rebalance="every_bar"'
_BAND_EDGE = (
    'rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge"'
)

#: keel-api's wire shape for a window bound — a midnight datetime.
_STAMP = "{} 00:00:00+00:00"

#: Nothing a reader should ever see in a comparability sentence: a raw
#: ISO date or time, a UTC offset, A/B run names, a raw declaration key or
#: value, a Python literal.
RAW = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|\+00:00|\b[AB]:"
    r"|every_bar|to_edge|to_center|buffer_threshold|buffer_mode|rebalance_method"
    r"|init_cash|funding_included|\bTrue\b|\bFalse\b|\bNone\b"
)

#: The run tool's input pair, the one ISO form a sentence may carry (Q-2422).
INPUT_PAIR = re.compile(r"start_date=\d{4}-\d{2}-\d{2} end_date=\d{4}-\d{2}-\d{2}")


def _detail(i, start, end, *, strategy_id="str_mr", name="Mean reversion", config=None, **extra):
    return {
        "id": f"btr_{strategy_id}_{i}",
        "status": "COMPLETED",
        "strategy_id": strategy_id,
        "strategy_name": name,
        "sequence_number": i + 1,
        "commit_id": f"c_{strategy_id}_{i}",
        "engine": "native",
        "start_date": _STAMP.format(start),
        "end_date": _STAMP.format(end),
        "backtest_config": config,
        "metrics": {"sharpe_ratio": 0.8 + 0.1 * i, "total_return": 20.0 + i, "max_drawdown": -12.0},
        **extra,
    }


def _curve(detail: dict) -> dict:
    """Monthly points across the run's OWN window, so differing windows
    draw curves over differing spans. Equity climbs from 5,000 past
    15,000, so the value axis crosses the 10k boundary where the old
    per-value format switched precision."""
    start = datetime.fromisoformat(detail["start_date"])
    end = datetime.fromisoformat(detail["end_date"])
    points, t, k = [], start, 0
    while t < end:
        points.append({"t": t.isoformat(), "equity": 5000.0 + 420.0 * k, "drawdown_pct": 0})
        t += timedelta(days=30)
        k += 1
    points.append({"t": end.isoformat(), "equity": 5000.0 + 420.0 * k, "drawdown_pct": 0})
    return {"points": points, "source_points": len(points)}


def _envelope(details: list[dict], sources: list[str]) -> dict:
    by_id = {d["id"]: d for d in details}
    by_commit = dict(zip((d["commit_id"] for d in details), sources))

    def fake_get(path, **_kw):
        if path.startswith("/v1/backtests/") and path.endswith("/curve"):
            return _curve(by_id[path.split("/")[3]])
        if path.startswith("/v1/backtests/"):
            return by_id[path.rsplit("/", 1)[1]]
        for d in details:
            base = f"/v1/strategies/{d['strategy_id']}/versions/{d['commit_id']}"
            if path == f"{base}/source":
                return {"source": by_commit[d["commit_id"]]}
            if path == base:
                return {"message": None}
        raise AssertionError(f"unexpected GET {path}")

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        return (
            OUTCOMES["keel_backtest_compare"]
            .handler({"backtest_ids": [d["id"] for d in details]}, ctx)
            .to_envelope()
        )


def _same(execution=_EVERY_BAR) -> str:
    return _SRC.format(execution=execution)


def founder_pair() -> dict:
    """The reported case: one strategy, ends one UTC day apart."""
    details = [_detail(0, "2024-07-27", "2026-09-23"), _detail(1, "2024-07-27", "2026-09-22")]
    return _envelope(details, [_same(), _same()])


def material_set() -> dict:
    """Four runs; two start five months later."""
    details = [
        _detail(0, "2024-07-27", "2026-09-22"),
        _detail(1, "2024-07-27", "2026-09-22"),
        _detail(2, "2025-01-03", "2026-09-22"),
        _detail(3, "2025-01-03", "2026-09-22"),
    ]
    return _envelope(details, [_same()] * 4)


def identical_pair() -> dict:
    """CONTROL: the same window, the same settings — nothing to say."""
    details = [_detail(0, "2024-07-27", "2026-09-22"), _detail(1, "2024-07-27", "2026-09-22")]
    return _envelope(details, [_same(), _same()])


def fork_pair() -> dict:
    """The live claude.ai shape: a library fork (a 20% band traded to its
    edge, $50,000) against the agent's own strategy (every bar, defaults)."""
    details = [
        _detail(0, "2024-07-27", "2026-09-22", strategy_id="str_agent", name="Agent momentum"),
        _detail(
            1,
            "2024-07-27",
            "2026-09-22",
            strategy_id="str_fork",
            name="Library momentum",
            config={"init_cash": 50000.0, "fees": 0.0002},
            metrics={"sharpe_ratio": 1.0, "funding_included": False},
        ),
    ]
    details[0]["metrics"]["funding_included"] = True
    return _envelope(details, [_same(_EVERY_BAR), _same(_BAND_EDGE)])


def _sentences(env: dict) -> list[str]:
    view = env["view"]
    return list(view["warnings"]) + list(view["notes"])


# ── The copy ──────────────────────────────────────────────────────────


def test_the_handler_compared_the_windows_it_was_given() -> None:
    """Non-vacuity: every run carries its own window, normalised from the
    raw keel-api stamps — a quantity no copy or threshold seed can move."""
    env = founder_pair()
    assert [{k: r["window"][k] for k in ("start", "end")} for r in env["view"]["runs"]] == [
        {"start": "2024-07-27", "end": "2026-09-23"},
        {"start": "2024-07-27", "end": "2026-09-22"},
    ]
    # The additive last covered day (spec 03 §2.5): `end` stays exclusive.
    assert [r["window"]["last_bar"] for r in env["view"]["runs"]] == [
        "2026-09-22",
        "2026-09-21",
    ]
    assert len(material_set()["view"]["runs"]) == 4


def test_the_founders_one_day_tail_is_a_note_in_words() -> None:
    """The reported pair: a NOTE naming both runs and the overlap, never a
    'not directly comparable' warning."""
    # SEED: in `compare_windows` set `note = False` — the sentence moves to
    # `warnings` and this reds on the first assertion.
    env = founder_pair()
    assert env["comparability_warnings"] == []
    assert env["view"]["notes"] == [
        "Windows differ by 1 day at the end — v1 ends Sep 22, 2026; v2 ends Sep 21, 2026. "
        "99.9% overlap, too small to change the comparison."
    ]
    # One owner, every surface: the model's text block carries the same line.
    assert env["view"]["notes"][0] in env["view"]["markdown"]
    assert env["view"]["overlap"] == {
        "start": "2024-07-27",
        "end": "2026-09-22",
        "severity": "note",
    }


# ── One display date for a window's end (Q-1903) ─────────────────────
#
# Live retest 2026-09-24 ~00:26Z: compare's note said "v1 ends Sep 24,
# 2026; v2 and v3 end Sep 23, 2026" — the EXCLUSIVE ends — while each
# run's card printed its last covered day (Sep 23 / Sep 22). Every window
# end in prose now reads `window_last_day`.


def test_the_note_ends_each_run_on_the_day_its_card_ends_on() -> None:
    """The note's date per run is the date that run's own range line ends
    on — the two surfaces read one helper.

    SEED (run 2026-09-24, reverted): in `compare_windows`'s `describe`,
    print `_day(key[1].isoformat())` (the exclusive end) — this arm reds on
    "Sep 23" / "Sep 22"; the non-vacuity lines above the verdict stay green.
    """
    from keel.tools.outcomes._backtest_view import _day, _window_line, window_last_day

    env = founder_pair()
    runs = env["view"]["runs"]
    # Non-vacuity (no seed moves these): two runs, two distinct ends, a note.
    assert len(runs) == 2 and runs[0]["window"]["end"] != runs[1]["window"]["end"]
    (note,) = env["view"]["notes"]
    for run in runs:
        last = window_last_day(run["window"])
        assert last is not None
        assert _window_line(run["window"]).endswith(_day(last.isoformat()))
        assert f"{run['label']} ends {_day(last.isoformat())}" in note, (run["label"], note)
        # And never the exclusive bound.
        assert f"{run['label']} ends {_day(run['window']['end'])}" not in note, note


def test_a_served_last_bar_wins_over_end_minus_one() -> None:
    """A run whose served last covered day is not `end - 1` (a capped or
    short run) is named on its served day, in both note shapes.

    SEED (run 2026-09-24, reverted): make `window_last_day` ignore
    `last_bar` (always `end - 1`) — both arms red; the CONTROL (no
    `last_bar`) stays green."""
    got = compare_windows(
        [
            {"start": "2024-07-27", "end": "2026-09-24", "last_bar": "2026-09-20"},
            {"start": "2024-07-27", "end": "2026-09-23"},
        ],
        ["v1", "v2"],
    )
    assert got is not None and "v1 ends Sep 20, 2026; v2 ends Sep 22, 2026" in got["sentence"]
    both = compare_windows(
        [
            {"start": "2024-07-27", "end": "2026-09-24", "last_bar": "2026-09-20"},
            {"start": "2025-01-03", "end": "2026-09-23"},
        ],
        ["v1", "v2"],
    )
    assert both is not None
    assert "v1 covers Jul 27, 2024 – Sep 20, 2026" in both["sentence"], both
    assert "v2 covers Jan 3, 2025 – Sep 22, 2026" in both["sentence"], both


def test_control_without_last_bar_the_end_is_the_day_before_the_bound() -> None:
    from keel.tools.outcomes._backtest_view import window_last_day

    assert window_last_day({"start": "2024-07-27", "end": "2026-09-24"}).isoformat() == "2026-09-23"
    assert window_last_day({"start": "2024-07-27"}) is None
    assert window_last_day(None) is None


def test_a_material_difference_is_a_warning_naming_which_runs_cover_what() -> None:
    # SEED: in `compare_windows`'s `describe`, return `f"{who} start{s}
    # {window[0].isoformat()}"` — the raw date reds the RAW guard below and
    # the exact sentence.
    #
    # Q-2422: the warning ends with the one approach sentence, naming the
    # FIRST run's exact pair (its start and exclusive end — the run tool's
    # input). SEED (run 2026-10-04): name `parsed[-1]` instead of
    # `parsed[0]` → this arm reds on `start_date=2025-01-03`.
    env = material_set()
    assert env["view"]["notes"] == []
    assert env["comparability_warnings"] == [
        "Windows differ by 5 months at the start — v1 and v2 start Jul 27, 2024; "
        "v3 and v4 start Jan 3, 2025. 79.7% overlap: return, drawdown and fee totals cover "
        "different periods. Windows default by clock (shorter on fine clocks). To compare "
        "variants — another clock, parameters or universe — run the first, then pass its "
        "start_date/end_date to the others (v1: start_date=2024-07-27 end_date=2026-09-22); "
        "a finer clock may take longer, and a start before its data is moved and named."
    ]
    assert env["view"]["overlap"]["severity"] == "warning"
    # The pair is v1's served window, exactly.
    v1 = env["view"]["runs"][0]["window"]
    assert (v1["start"], v1["end"]) == ("2024-07-27", "2026-09-22")


def test_no_comparability_sentence_carries_a_raw_value() -> None:
    """The regex guard, over every arm's warnings, notes and markdown
    tail — including the cross-strategy settings sentences."""
    # SEED: in `compare_windows`'s `describe`, return `f"{who} end{s}
    # {window[1].isoformat()}"` — the founder note carries `2026-09-23` and
    # this reds.
    arms = {
        "founder": founder_pair(),
        "material": material_set(),
        "identical": identical_pair(),
        "fork": fork_pair(),
    }
    #
    # One exemption, by FORM (Q-2422): the run tool's own arguments
    # `start_date=YYYY-MM-DD end_date=YYYY-MM-DD` — a pair to copy as input,
    # not a date shown for reading. Any other ISO date still reds.
    checked = pairs = 0
    for arm, env in arms.items():
        for sentence in _sentences(env):
            pairs += len(INPUT_PAIR.findall(sentence))
            assert not RAW.search(INPUT_PAIR.sub("", sentence)), (arm, sentence)
            checked += 1
    assert checked >= 6  # non-vacuous: the arms really said things
    assert pairs >= 1  # and the exemption is exercised, not dead


@pytest.mark.parametrize(
    ("windows", "severity"),
    [
        # 1 day off two years: a note.
        ((("2024-07-27", "2026-09-23"), ("2024-07-27", "2026-09-22")), "note"),
        # 7 days off two years: still a note (both bounds hold).
        ((("2024-07-27", "2026-09-29"), ("2024-07-27", "2026-09-22")), "note"),
        # 8 days off two years: 99% shared, but an edge moved past 7 days.
        # SEED: WINDOW_NOTE_MAX_EDGE_DAYS = 30 — this arm reds.
        ((("2024-07-27", "2026-09-30"), ("2024-07-27", "2026-09-22")), "warning"),
        # 1 day off a 30-day run: 96.7% shared — the overlap bound fails.
        # SEED: WINDOW_NOTE_MIN_OVERLAP = 0.9 — this arm reds.
        ((("2026-08-01", "2026-08-31"), ("2026-08-01", "2026-08-30")), "warning"),
    ],
)
def test_the_severity_threshold(windows, severity) -> None:
    got = compare_windows([{"start": s, "end": e} for s, e in windows], ["v1", "v2"])
    assert got is not None and got["severity"] == severity, got


def test_identical_windows_say_nothing() -> None:
    """CONTROL: a set on one window carries no window sentence, no
    overlap, and no settings sentence."""
    env = identical_pair()
    assert env["comparability_warnings"] == []
    assert env["view"]["notes"] == []
    assert "overlap" not in env["view"]


# ── Settings that change results, named ──────────────────────────────


def test_a_fork_against_the_agents_baseline_names_every_setting_that_differs() -> None:
    """The live claude.ai shape: Execution, capital and costs, and carry —
    each in words, per run, only the fields that differ."""
    # SEED: in `_handler` delete `warnings.extend(declared)` — the Execution
    # line disappears and this reds.
    warnings = fork_pair()["comparability_warnings"]
    assert warnings == [
        "Carry inclusion differs — Agent momentum includes carry; Library momentum excludes "
        "carry. PnL bases differ.",
        "Different strategies with the same pipeline and universe.",
        "Backtest settings differ — Agent momentum uses $10,000 capital, 4.5 bps fees; "
        "Library momentum uses $50,000 capital, 2 bps fees. Every net figure moves with them.",
        "Execution differs — Agent momentum rebalances every bar; Library momentum rebalances "
        "only outside a 20% band of target, trading only to the band edge.",
    ]


def test_within_one_strategy_a_labelled_edit_is_not_repeated_as_a_note() -> None:
    """CONTROL: two versions whose only change is the Execution the labels
    already name — the note would restate the column headers."""
    details = [_detail(0, "2024-07-27", "2026-09-22"), _detail(1, "2024-07-27", "2026-09-22")]
    env = _envelope(details, [_same(_EVERY_BAR), _same('rebalance="on_change"')])
    assert [r["label"] for r in env["view"]["runs"]] == ["v1 · every_bar", "v2 · on_change"]
    assert env["view"]["notes"] == []
    assert env["comparability_warnings"] == []


# ── Prevention: the compare hint pins the window ─────────────────────


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def get(self, path, **_kw):
        assert path == "/v1/backtests"
        return {"data": self.rows, "pagination": {}}


def _row(run_id, minutes_ago, end):
    stamp = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": run_id,
        "status": "COMPLETED",
        "completed_at": stamp,
        "end_date": _STAMP.format(end) if end else None,
    }


def test_the_compare_hint_states_differing_ends_without_proposing_a_run() -> None:
    # SEED (run 2026-09-28): in `compare_next` restore the old tail
    # (`; their end dates differ — end_date="…" on a further run aligns the
    # windows`) — the equality and the `end_date=` assertion on the split
    # arm red; the matching-ends arm and the bare control stay green.
    # Spec 02 §2.4 #1(a) (R-23): a fact; the end-date clause rides only when
    # the ends DIFFER. Spec 05 R-L4 / Q-2012: it proposes no further run and
    # names no date — the set is described, not extended.
    line = compare_next(
        _Client([_row("btr_b", 5, "2026-09-22"), _row("btr_a", 20, "2026-09-22")]),
        strategy_id="str_mr",
        backtest_id="btr_b",
    )
    assert line.endswith("sets them side by side"), line
    split = compare_next(
        _Client([_row("btr_b", 5, "2026-09-23"), _row("btr_a", 20, "2026-09-22")]),
        strategy_id="str_mr",
        backtest_id="btr_b",
    )
    assert split.endswith("sets them side by side; their end dates differ"), split
    assert "end_date=" not in split and "further run" not in split, split
    # CONTROL: a listing without windows adds no pin (never a guessed date).
    bare = compare_next(
        _Client([_row("btr_b", 5, None), _row("btr_a", 20, None)]),
        strategy_id="str_mr",
        backtest_id="btr_b",
    )
    assert bare.endswith("sets them side by side"), bare


def test_the_end_date_parameter_says_it_is_exclusive_and_how_to_share_a_window() -> None:
    from keel.tools.outcomes import _bootstrap

    _bootstrap()
    desc = OUTCOMES["keel_backtest_run"].input_schema["properties"]["end_date"]["description"]
    assert "exclusive" in desc and "Inclusive" not in desc
    # Stated as a fact, not an instruction (Q-1947 round 3 — ChatGPT's review
    # flags listed copy that steers the model).
    assert "runs share one window when each carries the same end_date" in desc


# ── The card ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def shown(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; what the card draws is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    work = tmp_path_factory.mktemp("compare-windows")
    (work / "compare.html").write_text(build_card_html("compare"), encoding="utf-8")
    envs = {"founder": founder_pair(), "material": material_set(), "identical": identical_pair()}
    (work / "envelopes.json").write_text(json.dumps(envs), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(work), "--envelopes", str(work / "envelopes.json")],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the compare-windows check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_card_drew_every_curve(shown: dict) -> None:
    """Non-vacuity for the card arms: a chart with one line per run."""
    assert shown["founder"]["inline"]["curves"] == 2
    assert shown["material"]["inline"]["curves"] == 4
    assert shown["identical"]["full"]["curves"] == 2


def test_a_material_difference_shades_the_uncommon_window(shown: dict) -> None:
    # SEED: in card-compare.js `drawMulti`, delete the `shade.forEach(…)`
    # block — no rect is drawn and this reds.
    for mode in ("inline", "full"):
        m = shown["material"][mode]
        assert m["shaded"] == 1, m
        # Jul 27, 2024 → Jan 3, 2025 of Jul 27, 2024 → Sep 22, 2026 is ~20%
        # of the axis; the plot is narrower than the svg by its gutters.
        assert 0.1 < m["shadedShare"] < 0.25, m
        assert "windows differ" in m["chips"], m
        assert "Shaded: dates not every run covers" in m["ariaLabel"], m


def test_a_one_day_tail_is_a_quiet_note_on_the_card(shown: dict) -> None:
    # SEED: in card-compare.js change the chart's `ov.severity === "warning"`
    # test to `ov` — the one-day sliver is drawn and this reds.
    m = shown["founder"]["inline"]
    assert m["shaded"] == 0, m
    assert "windows differ" not in m["chips"], m
    assert "Windows differ by 1 day at the end" in m["body"], m
    # The receipt no longer claims "same window" — the note says what differs.
    assert "same window" not in m["body"], m
    assert "same costs" in m["body"], m


def test_the_control_card_claims_the_same_window(shown: dict) -> None:
    """CONTROL: identical windows — the receipt says so, nothing is shaded."""
    m = shown["identical"]["inline"]
    assert m["shaded"] == 0 and "same window" in m["body"], m
    assert "windows differ" not in m["chips"], m


def test_the_value_axis_speaks_one_format(shown: dict) -> None:
    """ChatGPT: "5.00k · 10.0k · 15.0k" — three precisions on one axis.
    One unit, the fewest decimals that state every tick."""
    # SEED: in card-compare.js use `H.fmtCompact(v)` for the tick label
    # instead of `yl[j]` — the axis reads 5.00k / 10.0k again and this reds.
    labels = shown["material"]["inline"]["yLabels"]
    assert len(labels) >= 3, labels  # non-vacuous: the axis was drawn
    assert all(re.fullmatch(r"\d+k", label) for label in labels), labels


def test_the_create_default_labels_the_original_version() -> None:
    """ChatGPT: "v1 · Create HYPE Long/Cash MACD Trend" beside a real
    change label. The create default reads "original"; a message that
    merely starts with "Create" on a later version is the user's words."""
    from keel.tools.outcomes.backtest_compare import _version_message

    class Client:
        def __init__(self, message):
            self.message = message

        def get(self, _path):
            return {"message": self.message}

    first = {
        "strategy_id": "s",
        "commit_id": "c",
        "strategy_name": "HYPE MACD",
        "sequence_number": 1,
    }
    later = {**first, "sequence_number": 4}
    assert _version_message(Client("Create HYPE MACD"), first) == "original"
    assert _version_message(Client("Create HYPE MACD"), later) == "original"
    # CONTROL: the user's own words on a later version are kept.
    assert _version_message(Client("Create a faster variant"), later) == "Create a faster variant"
    assert _version_message(Client("MACD · fast 12 → 8"), later) == "MACD · fast 12 → 8"
