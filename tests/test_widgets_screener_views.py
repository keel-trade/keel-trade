"""The screen card draws the SAVED VIEW (Q-3635), in a real MCP Apps host.

Six envelopes (`screener_view_fixtures`), light and dark, at a phone width
and a wide one, through `c_screener_views_check.mjs`:

* a Table default draws a ranked table over the view's declared columns
  with the passing set's rows (15 for a Top 15, 8 for a rule, 10 for the
  curated list; 25 then "+N more" counted against the set, never the
  universe), the label as written, the unit once, no tiles;
* a Lines default draws a y axis with ticks and a time axis, and holds the
  shown lines to a robust band, marking the one that leaves it;
* an envelope without a result draws one receipt line, never an empty body;
* the header name is in the UI face; the meta line carries the view
  revision; a view with no filter says nothing about passing; a tie at the
  cutoff is named.

The 2026-11-05 polish batch (Q-3640) adds three cases and three rules:

* every column signs by ONE rule (keel-app's `isSigned`), never by the rows
  shipped: the founder's curated list, shipped as the view shape (`latest_top`
  only), signs its ranked 7-day return `+61.4%` beside the `+9.2%` of the next
  column, and still does with no `completed.range` served;
* the ranked output's range over every coin (`completed.range`) is one quiet
  line under the rows, through `copy.card.range`, only when the rows show
  fewer coins than carry a value: never on a receipt, never unserved;
* the meta line prints the server's cadence word as given ("Hourly"): the
  card adds no " bars";
* a `keel_screener_view_set` envelope that carries the full result draws
  exactly what `keel_screener_view` draws.

Seeds, each run before this landed (reverse the edit, never `git checkout`):
* `outputSigned`: return `anyBelowZero(column(latestTable(env), name))` →
  the sign arm reds on movers_top (`61.4%`), movers_top_norange and the
  Lines legend; movers (whole `latest` shipped) stays green — the control;
* `table`/`numbers`: drop the `rangeLine(...)` calls → the range arm reds on
  every completed case, the receipt arm stays green;
* `render`: `copy.clock + " bars"` → the meta arm reds (the card said it).
* `viewPlan`: return `t.columns.slice(1)` for `cols` unconditionally →
  movers draws every output, the declared-columns arm reds (and `test_the_
  table_is_the_views_columns` names it);
* `table`: `rows.slice(0, 5)` in place of `ROWS_INLINE` → the row-count arms
  red on top15 (5 ≠ 15) and nofilter (5 ≠ 25);
* `render`: `return` before `H.receipt` in the no-manifest branch → the
  receipt arm reds with an empty body;
* `drawLines`: drop the `tick.ticks.forEach` block → the axis arm reds;
  set `hi = pool[pool.length - 1]` unconditionally → the clipping arm reds
  (no marker, the 100% tick becomes 2,500%);
* `setRows`: count against `universe.total` → "+164 more" appears on top15.
Not vacuous: every arm asserts a positive count or a literal it expects, and
`test_the_harness_saw_every_case` pins 36 renderings with zero page errors.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest
from keel.widgets import build_card_html

from .screener_view_fixtures import UNIVERSE, card_envelopes


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parents[2]
PLAYWRIGHT = REPO / "services" / "keel-app" / "node_modules" / "playwright"

pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and PLAYWRIGHT.exists()),
    reason="node + services/keel-app/node_modules/playwright (installed under CI)",
)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("views")
    path = out / "input.json"
    path.write_text(
        json.dumps(
            {
                "html": build_card_html("screener"),
                "cases": card_envelopes(),
                "out": str(out / "shots"),
            }
        )
    )
    proc = subprocess.run(
        ["node", "tests/fixtures/cards/c_screener_views_check.mjs", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        env={**os.environ, "NO_COLOR": "1"},
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    rows = json.loads(proc.stdout)["rows"]
    return {(r["name"], r["theme"], r["width"]): r for r in rows}


def _every(rendered, name):
    return [r for k, r in rendered.items() if k[0] == name]


#: The founder's six cases plus the three Q-3640 added.
CASES = 9


def test_the_harness_saw_every_case(rendered):
    assert len(rendered) == CASES * 2 * 2
    for r in rendered.values():
        assert r["errors"] == [], r
        assert r["bodyText"], ("empty body", r["name"], r["theme"], r["width"])
        assert not r["overflow"], (r["name"], r["width"])
        assert r["ellipsis"] == 0, (r["name"], r["width"])
        assert r["wrapped"] == 0, ("a cell wrapped", r["name"], r["width"])


def test_a_table_default_draws_the_passing_set_not_five_or_the_universe(rendered):
    for r in _every(rendered, "top15"):
        assert r["tableRows"] == 15, r
        assert r["nums"] == 0, "no tiles on a Table view"
        assert r["lines"] == 0
        assert r["more"] == [], "15 of 15 shown: nothing is more"
        assert "more in Keel" not in r["text"]
        assert r["firstRow"][:2] == ["1", "XMR"]
        assert r["firstRow"][2] == "+83.2%", "the unit sits in the header, not every cell"
        assert r["bars"] == 15 and r["divBars"] == 0, (
            "neutral bars, no zero line: nothing shown is negative"
        )
    for r in _every(rendered, "above20"):
        assert r["tableRows"] == 8, r
    for name in ("movers", "movers_top", "movers_top_norange"):
        for r in _every(rendered, name):
            assert r["tableRows"] == 10, r
    for r in _every(rendered, "nofilter"):
        assert r["tableRows"] == 25, r
        assert r["more"] == [f"+{UNIVERSE - 25} more in Keel"], r["more"]


def test_the_table_is_the_views_columns_with_whole_labels_and_the_unit_once(rendered):
    for r in _every(rendered, "top15"):
        assert r["headers"] == ["#", "", "Funding now, a year ↓"], r["headers"]
        # the header, the view's sentence and the tie note say it; no cell does
        assert r["text"].count("a year") == 3, r["text"]
        assert "a year" not in " ".join(r["firstRow"])
    wide = rendered[("movers", "light", 760)]
    assert wide["headers"] == ["#", "", "7-day return ↓", "Relative volume rank", "1-day return"]
    narrow = rendered[("movers", "light", 360)]
    assert narrow["headers"] == ["#", "", "7-day return ↓", "Relative volume rank"]
    assert narrow["firstRow"] == ["1", "HYPE", "+61.4%", "0.32"]


def test_the_header_is_plain_and_the_meta_line_carries_the_revision(rendered):
    for r in _every(rendered, "top15"):
        assert r["headName"] == "Top funding rate coins"
        assert "Mono" not in r["headFont"] and "monospace" not in r["headFont"].lower(), r[
            "headFont"
        ]
        # contract field 2: the server's cadence word as given, nothing added
        assert r["meta"] == ["Hourly · as of 5 Nov 13:00 UTC · view rev 3 · Updated 4 minutes ago"]
        assert "bars" not in r["text"], "the card adds no word to the cadence"
        assert r["pass"] == [
            "15 of 179 pass nowThe screen shows the top 15 by Funding now, a year."
        ]
        assert r["tie"] == ["8 coins tie at +10.9% a year at the cutoff"]
    for r in _every(rendered, "above20"):
        assert r["tie"] == []
        assert r["pass"][0].startswith("8 of 179 pass now")
    for r in _every(rendered, "nofilter"):
        assert r["pass"] == [] and r["tie"] == [], "no filter: no pass sentence"
        assert "pass" not in r["text"]
        assert "view rev" not in r["text"], "no saved view: no revision"


def test_a_lines_default_has_axes_and_holds_the_shown_lines_to_a_band(rendered):
    for r in _every(rendered, "lines"):
        assert r["nums"] == 5 and r["numLabel"] == ["Funding now, a year"]
        assert r["numValues"] == ["+83.2%", "+40.5%", "+31.0%", "+29.0%", "+27.0%"]
        assert r["more"] == [f"+{UNIVERSE - 5} more in Keel"]
        assert r["lines"] == 5 and r["field"] >= 5
        assert len(set(r["strokes"])) == 5, "five distinct colours"
        assert len(r["yTicks"]) >= 3, r["yTicks"]
        assert len(r["xTicks"]) == 3 and r["xTicks"][0] == "1 Nov" and r["xTicks"][-1] == "5 Nov"
        # the 2,500% spike did not flatten the picture: the top tick is near the data
        top = max(float(t.replace("%", "").replace("−", "-")) for t in r["yTicks"])
        assert 90 <= top <= 150, r["yTicks"]
        assert r["clips"] == 1, "the spiking line is marked at the edge"
        assert r["ends"][0] == "XMR +83.2%"
        assert r["tableRows"] == 0


def test_an_envelope_without_a_result_draws_a_receipt_line(rendered):
    for r in _every(rendered, "receipt"):
        assert r["receipt"] == ["View saved. · view rev 4"], r
        assert r["headName"] == "Top funding rate coins"
        assert r["tableRows"] == 0 and r["nums"] == 0 and r["lines"] == 0


def _cells(r, column):
    """Every cell of one column of the ranked table, by header index."""
    i = r["headers"].index(column)
    return [row[i] for row in r["rows"]]


def test_every_column_signs_by_one_rule_never_by_the_rows_shipped(rendered):
    """Q-3640: the founder's card printed `60.9%` in the ranked column beside
    `+49.8%` in the next, because the sign was read off the rows the view
    envelope shipped — the top of the ranking, one-signed by construction."""
    for name in ("movers_top", "movers_top_norange", "movers"):
        for r in _every(rendered, name):
            assert r["firstRow"][:3] == ["1", "HYPE", "+61.4%"], (name, r["firstRow"])
            ranked = _cells(r, "7-day return ↓")
            assert len(ranked) == 10 and all(c.startswith("+") for c in ranked), (name, ranked)
        wide = rendered[(name, "light", 760)]
        one_day = _cells(wide, "1-day return")
        assert "−3.4%" in one_day, (name, one_day)
        assert all(c[0] in "+−" for c in one_day), (name, one_day)
    # the control: the whole cross-section shipped, the same cells
    assert (
        rendered[("movers", "light", 760)]["rows"] == rendered[("movers_top", "light", 760)]["rows"]
    )
    # a rank is ordinal: never signed
    assert _cells(rendered[("movers_top", "light", 760)], "Relative volume rank")[0] == "0.32"
    # the same rule signs the tie line and the Lines legend
    assert rendered[("top15", "dark", 360)]["tie"] == ["8 coins tie at +10.9% a year at the cutoff"]
    assert rendered[("lines", "light", 360)]["numValues"][0] == "+83.2%"


RANGE_FUNDING = "Across 179 coins: −140.6% (MINA) to +83.2% (XMR) · 78 below zero"
RANGE_MOVERS = "Across 179 coins: −36.2% (YTT) to +61.4% (HYPE) · 71 below zero"


def test_the_range_over_every_coin_is_one_line_under_the_rows(rendered):
    """Contract field 1 through `copy.card.range` (+ `range_below`): drawn when
    the rows show fewer coins than carry a value, in the column's unit and
    sign; absent on a receipt and when the server serves no range."""
    for name, words in (
        ("top15", RANGE_FUNDING),
        ("above20", RANGE_FUNDING),
        ("nofilter", RANGE_FUNDING),
        ("lines", RANGE_FUNDING),
        ("movers", RANGE_MOVERS),
        ("movers_top", RANGE_MOVERS),
    ):
        for r in _every(rendered, name):
            assert r["range"] == [words], (name, r["range"])
    for name in ("receipt", "movers_top_norange"):
        for r in _every(rendered, name):
            assert r["range"] == [] and "Across" not in r["text"], (name, r["text"])
    # under the table's "+N more" line, and under the Lines legend
    for r in _every(rendered, "nofilter"):
        assert r["order"].index("sc-more") < r["order"].index("sc-range")
    for r in _every(rendered, "lines"):
        assert (
            r["order"].index("sc-nums")
            < r["order"].index("sc-range")
            < r["order"].index("sc-chart")
        )


def test_a_view_set_envelope_draws_what_a_view_draws(rendered):
    """Lane B: keel_screener_view_set returns the full screen envelope; the
    card has no special path for it. Same source, bumped revision."""
    for theme in ("light", "dark"):
        for width in (360, 760):
            saved = rendered[("view_set", theme, width)]
            viewed = rendered[("top15", theme, width)]
            assert saved["meta"] == [
                "Hourly · as of 5 Nov 13:00 UTC · view rev 4 · Updated 4 minutes ago"
            ]
            assert saved["text"].replace("view rev 4", "view rev 3") == viewed["text"]
            assert saved["rows"] == viewed["rows"] and saved["receipt"] == []
            assert "View saved" not in saved["text"], "the save words belong to the receipt"


def test_the_other_views_are_named(rendered):
    assert "More views in Keel: Lines" in rendered[("top15", "light", 360)]["text"]
    assert "More views in Keel: Lines · Scatter" in rendered[("movers", "dark", 760)]["text"]
    assert "More views" not in rendered[("nofilter", "light", 360)]["text"], (
        "one view: nothing else"
    )
