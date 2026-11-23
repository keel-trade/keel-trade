"""The screen card draws the SAVED VIEW on the chat cards' face (Q-3635; SCREENS-CARD, D-420..D-420d), in a real MCP
Apps host.

Twelve envelopes (`screener_view_fixtures`, through the SDK's real `build_result`), light and dark, at a phone width
and a wide one, inline; four of them also in Expand at a phone and a desktop size, where the harness then chooses
Show "All" and focuses the Table panel (`c_screener_views_check.mjs`).

Inline (the hosts' budget: one hero and a few data points, two actions):
* a Table view draws the top five of the set as rows (rank, coin, a data bar in the coin's identity colour, the
  value), the first emphasised at the hero size, the column's label once; the baseline strip (every coin a tick,
  zero, the ends named, the top five in their colours); "15 of 179 coins · 10 more in Expand"; what entered and left
  since the last bar as chips; View in Keel (the filled primary) and Expand (quiet);
* the head is the name and one muted line: kind, version, cadence, the data's time; no view revision, no second time
  and no sentence restating the view (Q-7921);
* a Lines view draws the top five in colour over the grey field, end-labelled; a line that leaves the band is capped
  at the plot's edge and its value written there (Q-7924);
* an envelope without a result draws the head and one line; an empty set draws the rule and the baseline.

Expand (a dashboard, D-420d): the controls up top; Lines, the whole passing set as a table (ties ranked "11="), the
universe's distribution, a Scatter when published, visible together; five lines coloured on open and never more than
five identity colours (Q-7923); the scrub readout in words (Q-7925); names never cut; hairline rules; View in Keel
links the focused view (`graphs.views`, the app's `v`); Test as a strategy is the quiet button, never a bare link
(Q-7920).

Seeded reds (in this file, each on the served card HTML with a control that stays green):
Q-7920 the Test link without the button classes; Q-7921 the revision back in the head; Q-7923 Expand opening on
every line; Q-7924 out-of-band points drawn at their true height; Q-7925 the readout as an ISO date; Q-7926 the
adapter's screener exclusion from Expand. Q-7922 (the palette) is test_card_screener_palette.py.
Not vacuous: every arm asserts a positive count or a literal it expects, and `test_the_harness_saw_every_case` pins
the renderings with zero page errors.
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
HARNESS = "tests/fixtures/cards/c_screener_views_check.mjs"

pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and PLAYWRIGHT.exists()),
    reason="node + services/keel-app/node_modules/playwright (installed under CI)",
)

#: The cases also drawn in Expand.
EXPANDED = ("top15", "lines", "movers", "scatter")
#: Light ids per rank (host-adapter.js PALETTE.light.cat, the accent first), as computed RGB.
ACCENT = {"light": "rgb(75, 76, 186)", "dark": "rgb(107, 113, 227)"}


def _run(tmp: pathlib.Path, html: str, cases: dict, **extra) -> list[dict]:
    path = tmp / "input.json"
    path.write_text(json.dumps({"html": html, "cases": cases, "out": str(tmp / "shots"), **extra}))
    proc = subprocess.run(
        ["node", HARNESS, str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=600,
        env={**os.environ, "NO_COLOR": "1"},
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)["rows"]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    envs = card_envelopes()
    tmp = tmp_path_factory.mktemp("views")
    rows = _run(tmp, build_card_html("screener"), envs)
    full = _run(
        tmp_path_factory.mktemp("views-full"),
        build_card_html("screener"),
        {k: envs[k] for k in EXPANDED},
        modes=["fullscreen"],
    )
    return {(r["name"], r["theme"], r["width"], r["mode"]): r for r in rows + full}


def _every(rendered, name, mode="inline"):
    return [r for k, r in rendered.items() if k[0] == name and k[3] == mode]


def test_the_harness_saw_every_case(rendered):
    inline = [r for r in rendered.values() if r["mode"] == "inline"]
    assert len(inline) == len(card_envelopes()) * 2 * 2
    assert len([r for r in rendered.values() if r["mode"] == "fullscreen"]) == len(EXPANDED) * 2 * 2
    for r in rendered.values():
        assert r["errors"] == [], r
        assert r["bodyText"], ("empty body", r["name"], r["theme"], r["width"])
        assert not r["overflow"], (r["name"], r["width"], r["mode"])
        assert not r["isoDate"], (r["name"], r["mode"])


def test_a_table_view_leads_with_the_top_five_the_first_emphasised(rendered):
    for r in _every(rendered, "top15"):
        assert [row[:2] for row in r["rows"]] == [
            ["1", "XMR"],
            ["2", "SAGA"],
            ["3", "HYPE"],
            ["4", "ETH"],
            ["5", "SOL"],
        ], r["rows"]
        assert r["rows"][0][2] == "+83.2%", "the unit sits in the label, not every cell"
        assert r["firstValueSize"] == ["28px"], "the first row's value at the hero size"
        assert r["label"] == ["Funding now, a year"], r["label"]
        assert len(set(r["barFills"])) == 5 and r["barFills"][0] == ACCENT[r["theme"]], r[
            "barFills"
        ]
        assert r["count"] == ["15 of 179 coins · 10 more in Expand"], r["count"]
        assert r["tableRows"] == 0, "the table is Expand's"
    for r in _every(rendered, "above20"):
        assert len(r["rows"]) == 5 and r["count"] == ["8 of 179 coins · 3 more in Expand"]
    for r in _every(rendered, "nofilter"):
        assert len(r["rows"]) == 5 and r["count"] == [
            f"{UNIVERSE} coins · {UNIVERSE - 5} more in Expand"
        ]
    # one sign rule, never the rows shipped (Q-3640): the curated list's ranked column signs as the whole set does
    for name in ("movers", "movers_top", "movers_top_norange"):
        for r in _every(rendered, name):
            assert r["rows"][0] == ["1", "HYPE", "+61.4%"], (name, r["rows"])


def test_the_baseline_strip_places_every_coin_and_names_the_ends(rendered):
    for r in _every(rendered, "top15"):
        # every coin with a value is a tick; the top five drawn over in their colours
        assert r["stripTicks"] == UNIVERSE - 5, r["stripTicks"]
        assert r["stripIds"] and set(r["stripIds"]) == {"XMR", "SAGA", "HYPE", "ETH", "SOL"}
        assert "−140.6% MINA" in r["stripLabels"] and "XMR +83.2%" in r["stripLabels"], r[
            "stripLabels"
        ]
        assert "0" in r["stripLabels"]
    wide = rendered[("top15", "light", 760, "inline")]
    assert "78 below zero" in wide["stripLabels"] and "Across 179 coins" in wide["stripLabels"]
    # the view shape ships no `latest`: the served range's ends, no field
    for r in _every(rendered, "movers_top"):
        assert r["stripTicks"] == 0 and "HYPE +61.4%" in r["stripLabels"], r["stripLabels"]
    # neither `latest` nor a served range: no strip at all, never an invented one
    for r in _every(rendered, "movers_top_norange"):
        assert r["stripLabels"] == [], r["stripLabels"]


def test_the_head_is_one_quiet_line_with_one_time(rendered):
    """Q-7921: no view revision, one time (the data's), no sentence restating the view."""
    for name in ("top15", "view_set", "nofilter"):
        for r in _every(rendered, name):
            assert r["meta"] == ["Screen · v2 · Hourly · as of 5 Nov 13:00 UTC"], (name, r["meta"])
            assert "revision" not in r["text"] and "rev " not in r["text"], r["text"]
            assert "Updated" not in r["text"] and "The screen shows" not in r["text"], r["text"]
            assert r["title"] == ["Top funding rate coins"]


def test_what_moved_since_the_last_bar_is_chips(rendered):
    for r in _every(rendered, "top15_moves"):
        assert r["chips"] == ["▲ SAGA", "▼ LINK"], r["chips"]
        assert r["newTags"] == ["new"], "SAGA's row says it is new"
    for r in _every(rendered, "top15"):
        assert r["chips"] == [] and r["newTags"] == []


def test_two_actions_the_primary_filled_and_a_visible_expand(rendered):
    """Q-7920, Q-7926: View in Keel and Expand on every result, styled; no bare link anywhere."""
    for name in ("top15", "nofilter", "lines", "scatter", "movers"):
        for r in _every(rendered, name):
            assert [a["text"] for a in r["links"]] == ["View in Keel ↗", "Expand"], (
                name,
                r["links"],
            )
            assert (
                "button-primary" in r["links"][0]["cls"] and "button-quiet" in r["links"][1]["cls"]
            )
            assert r["bareAnchors"] == 0, (name, r["text"])
            assert r["minControl"] >= (44 if r["width"] < 480 else 36), (name, r["minControl"])
    for r in _every(rendered, "receipt"):
        assert [a["text"] for a in r["links"]] == ["View in Keel ↗"], (
            "a receipt has nothing to expand"
        )
        assert r["status"] == ["View saved."] and r["meta"] == ["Screen · v2"], r


def test_a_lines_view_caps_the_outlier_at_the_edge_and_writes_its_value(rendered):
    """Q-7924: the 2,500 % spike never leaves the plot; it is capped (dashed) and its value is written there."""
    for r in _every(rendered, "lines"):
        assert r["colouredLines"] == 5 and len(r["colours"]) == 5, r["colours"]
        assert r["field"] >= 10
        assert r["ends"][0] == "XMR +83.2%", r["ends"]
        assert r["clips"] == ["↑ +2,500%"], r["clips"]
        assert r["capRuns"] >= 1
        assert r["checked"] > 100 and r["outside"] == 0, (r["checked"], r["outside"])
        top = max(float(t.replace("%", "").replace("−", "-")) for t in r["yTicks"])
        assert 90 <= top <= 150, r["yTicks"]
        assert len(r["xTicks"]) == 3 and r["xTicks"][0] == "1 Nov" and r["xTicks"][-1] == "5 Nov"


def test_an_empty_set_shows_its_rule_and_the_baseline(rendered):
    for r in _every(rendered, "empty"):
        assert r["rows"] == [] and r["desc"], r
        assert r["count"] == ["0 of 179 coins"], r["count"]
        assert r["stripLabels"], "the baseline shows where every coin sits"
        assert [a["text"] for a in r["links"]] == ["View in Keel ↗"], "nothing to expand"


def test_expand_is_a_dashboard_with_every_view_together(rendered):
    for r in _every(rendered, "top15", "fullscreen"):
        assert r["panels"] == ["Lines", "Table", "dist"], r["panels"]
        assert r["tableRows"] == 15, r["tableRows"]
        assert r["tiedRanks"] == ["11="] * 5, r["tiedRanks"]
        assert r["cutNames"] == 0
        assert r["ruleAlpha"] is not None and r["ruleAlpha"] <= 0.2, r["ruleAlpha"]
        assert r["colouredLines"] == 5, r["colouredLines"]
        assert r["controlsBar"][:3] == ["All views", "Lines", "Table"], r["controlsBar"]
        assert [a["text"] for a in r["links"]] == ["View in Keel ↗", "Test as a strategy"], r[
            "links"
        ]
        assert "button-quiet" in r["links"][1]["cls"] and r["bareAnchors"] == 0
        # Show "All": never a new colour past the five identity slots
        assert r["showAll"]["lines"] == 20 and len(r["showAll"]["colours"]) == 5, r["showAll"][
            "colours"
        ]
        assert r["showAll"]["inkLines"] == 15
        # tap the Table panel's name: it takes the page
        assert r["focusTable"]["panels"] == ["Table", "dist"], r["focusTable"]["panels"]
    for r in _every(rendered, "movers", "fullscreen"):
        assert r["panels"] == ["Lines", "Scatter", "dist", "Table"], r["panels"]
        assert r["tableHeaders"] == [
            "#",
            "",
            "7-day return ↓",
            "Relative volume rank",
            "1-day return",
        ]
        assert (
            "−3.4%" in " ".join(" ".join(row) for row in [r["tableFirst"]]) or r["tableRows"] == 10
        )


def test_expand_reads_the_scrub_in_words(rendered):
    """Q-7925: the readout names the bar in words, UTC, never an ISO stamp."""
    for r in _every(rendered, "lines", "fullscreen"):
        assert r["readout"] and r["readout"][0].startswith("Thu 5 Nov 13:00 UTC"), r["readout"]


def test_view_in_keel_opens_the_focused_view(rendered):
    for r in _every(rendered, "lines", "fullscreen"):
        hero = r["links"][0]["href"]
        assert "v=" not in hero, "the default view is the link itself"
        assert r["focusTable"]["links"][0]["href"] == hero + "&v=All%20coins", r["focusTable"][
            "links"
        ]


# ── seeded reds: one edit to the served card each, the control in the same run ─────────────────────────────────────


def _seeded(tmp_path, old: str, new: str, cases: tuple[str, ...], modes=("inline",)):
    html = build_card_html("screener")
    assert html.count(old) == 1, f"the seed's anchor moved: {old!r}"
    envs = card_envelopes()
    return _run(
        tmp_path,
        html.replace(old, new),
        {k: envs[k] for k in cases},
        themes=["light"],
        widths=[760],
        modes=list(modes),
    )


def test_seeded_q7920_a_bare_test_link_is_caught(tmp_path):
    rows = _seeded(
        tmp_path,
        'el("a", "button-link button-quiet sc-test", test.label)',
        'el("a", "sc-test", test.label)',
        ("top15",),
        ("inline", "fullscreen"),
    )
    by = {r["mode"]: r for r in rows if r["width"] in (760, 1100)}
    assert by["fullscreen"]["bareAnchors"] == 1  # red
    assert by["inline"]["bareAnchors"] == 0  # control: inline carries no Test link


def test_seeded_q7921_the_revision_in_the_head_is_caught(tmp_path):
    rows = _seeded(
        tmp_path,
        "      copy.clock,\n      trouble ? copy.freshness : when,",
        '      copy.clock,\n      screen.view_revision != null\n        ? tpl(env, "view_rev", { n: screen.view_revision })\n        : null,\n      trouble ? copy.freshness : when,',
        ("top15", "nofilter"),
    )
    by = {r["name"]: r for r in rows}
    assert "revision" in by["top15"]["meta"][0]  # red
    assert by["nofilter"]["meta"] == [
        "Screen · v2 · Hourly · as of 5 Nov 13:00 UTC"
    ]  # control: no revision served


def test_seeded_q7923_expand_on_every_line_is_caught(tmp_path):
    rows = _seeded(
        tmp_path,
        "var top = full ? (ui.top != null ? ui.top : TOP) : TOP;",
        "var top = full ? (ui.top != null ? ui.top : 999) : TOP;",
        ("lines",),
        ("inline", "fullscreen"),
    )
    by = {r["mode"]: r for r in rows if r["width"] in (760, 1100)}
    assert by["fullscreen"]["lines"] == 20  # red: every line on open
    assert len(by["fullscreen"]["colours"]) == 5  # the identity slots still cap the colours
    assert by["inline"]["lines"] == 5  # control


def test_seeded_q7924_the_true_height_leaves_the_plot(tmp_path):
    rows = _seeded(
        tmp_path,
        "return y == null || side(y) ? null : [X(j), Y(y)];",
        "return y == null ? null : [X(j), Y(y)];",
        ("lines", "top15"),
        ("fullscreen",),
    )
    by = {(r["name"], r["width"]): r for r in rows}
    assert by[("lines", 1100)]["outside"] > 0  # red
    assert by[("top15", 1100)]["outside"] == 0  # control: no shown line leaves its band


def test_seeded_q7925_an_iso_readout_is_caught(tmp_path):
    rows = _seeded(
        tmp_path,
        '    if (weekday) opts.weekday = "short";',
        "    if (weekday) return d.toISOString().slice(0, 10);",
        ("lines",),
        ("inline", "fullscreen"),
    )
    by = {r["mode"]: r for r in rows if r["width"] in (760, 1100)}
    assert by["fullscreen"]["isoDate"]  # red
    assert not by["inline"]["isoDate"]  # control: the head's time is not the readout's


def test_seeded_q7926_the_screener_exclusion_hides_expand(tmp_path):
    rows = _seeded(
        tmp_path,
        "    if (\n      hostOffersFullscreen() &&",
        '    if (\n      cardKind() !== "screener" &&\n      hostOffersFullscreen() &&',
        ("top15", "receipt"),
    )
    by = {r["name"]: r for r in rows}
    assert [a["text"] for a in by["top15"]["links"]] == ["View in Keel ↗"]  # red: no Expand
    assert [a["text"] for a in by["receipt"]["links"]] == [
        "View in Keel ↗"
    ]  # control: never had one
