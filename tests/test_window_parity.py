"""One window, one display rule — the SDK and card arms of spec 03 guard 12.

A run's window is half-open on the wire: `end` / `end_exclusive` is the first
day the run did NOT cover. Every surface that prints a range prints its LAST
COVERED day (`last_bar`), so the app tearsheet, the SDK's text, the backtest
card and the compare card agree for one run (spec 03 §2.5; the app arm is
keel-app's `window-parity.unit.test.ts` over the SAME fixture).

The fixture is a byte-identical copy of keel-app's
`src/lib/__tests__/window-parity.fixture.json` (lane L3); the identity test
below reds the day the two copies drift.

SEED (run 2026-09-23, reverted by reversing the edit): make `_window_line`
print `end` again (`last = window.get("end")`) — `test_the_sdk_text_prints_the_last_covered_day`
reds; make `host-adapter.js` `fmtWindow` ignore `last_bar` — the card arms
and `test_the_adapter_reads_a_served_last_bar` red while the derived-range
CONTROL stays green.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
from keel.tools.outcomes._backtest_view import _window_line, window_block
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "window-parity.fixture.json"
APP_FIXTURE = (
    HERE.parents[3]
    / "services"
    / "keel-app"
    / "src"
    / "lib"
    / "__tests__"
    / "window-parity.fixture.json"
)
CHECK = HERE / "fixtures" / "cards" / "c_window_parity_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_not_vacuous(fixture: dict) -> None:
    """The one property the rule turns on, asserted on the INPUT: the
    exclusive end and the last covered day differ."""
    ran = fixture["run"]["window"]["ran"]
    assert ran["end_exclusive"] != ran["last_bar"]
    served = fixture["served_last_bar"]["run"]["window"]["ran"]
    assert served["last_bar"] < ran["last_bar"]  # earlier than end − 1 day


def test_the_fixture_matches_the_apps_copy() -> None:
    if not APP_FIXTURE.exists():
        pytest.skip(f"keel-app's copy is not on this tree yet ({APP_FIXTURE})")
    assert FIXTURE.read_bytes() == APP_FIXTURE.read_bytes()


def test_the_sdk_text_prints_the_last_covered_day(fixture: dict) -> None:
    run = fixture["run"]
    view_window = window_block(run["start_date"], run["end_date"], run["window"])
    assert _window_line(view_window) == fixture["expected"]
    # `end` keeps its exclusive meaning on the wire (R-3).
    assert view_window["end"] == run["window"]["ran"]["end_exclusive"]
    assert view_window["last_bar"] == run["window"]["ran"]["last_bar"]
    # The served view window prints the same.
    assert _window_line(fixture["view_window"]) == fixture["expected"]


def test_the_sdk_reads_a_served_last_bar(fixture: dict) -> None:
    served = fixture["served_last_bar"]
    run = served["run"]
    view_window = window_block(run["start_date"], run["end_date"], run["window"])
    assert _window_line(view_window) == served["expected"]


def test_an_older_api_derives_end_minus_one_day(fixture: dict) -> None:
    """CONTROL: no served window object ⇒ the exclusive end minus one day."""
    run = fixture["run"]
    assert _window_line(window_block(run["start_date"], run["end_date"])) == fixture["expected"]


@pytest.fixture(scope="module")
def cards(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; what a card shows is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    out = tmp_path_factory.mktemp("parity-cards")
    for kind in CARD_KINDS:
        (out / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(out), "--fixture", str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the parity check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_backtest_card_prints_the_last_covered_day(cards: dict) -> None:
    assert cards["expected"] in cards["backtest"], cards["backtest"][:400]
    assert "Sep 23, 2026" not in cards["backtest"]


def test_the_compare_card_prints_the_last_covered_day(cards: dict) -> None:
    assert cards["expected"] in cards["compare"], cards["compare"][:400]
    assert "Sep 23, 2026" not in cards["compare"]


def test_the_adapter_reads_a_served_last_bar(cards: dict) -> None:
    assert cards["servedRange"] == cards["servedExpected"]


def test_the_adapter_derives_end_minus_one_without_it(cards: dict) -> None:
    """CONTROL for the served arm: the same run without `last_bar` prints
    end − 1 day — so the served arm's earlier date is the served value."""
    assert cards["derivedRange"] == "Jul 27, 2024 – Sep 22, 2026"


def test_the_chart_readout_names_the_last_covered_day(cards: dict) -> None:
    """Q-1883: the range read "… – Sep 22, 2026" while the chart's
    last-point readout read "Sep 23, 2026 14.5k −21.2% from peak" — the
    curve's last mark sits at the window's exclusive end. The readout names
    the day the range ends on; a curve ending inside the window (CONTROL)
    keeps its own date, so the rule clamps rather than always subtracting."""
    # SEED: in card-backtest.js `setReadout` format `p.t` instead of
    # `p.day` — the at-end arm reads "Sep 23, 2026" and this reds.
    at_end = cards["chartAtEnd"]
    assert at_end["readout"], "no chart readout was drawn"
    assert at_end["readout"].startswith("Sep 22, 2026"), at_end["readout"]
    assert "Sep 23, 2026" not in at_end["body"], at_end["body"][:400]
    inside = cards["chartInside"]
    assert inside["readout"].startswith("Sep 10, 2026"), inside["readout"]
