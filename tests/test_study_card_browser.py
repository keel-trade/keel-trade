"""The study card, rendered from real envelopes in real Chromium (research spec 13 §7.1, WL-CU-12).

``tests/fixtures/cards/study/*.json`` are real tool results (``research.card_fixtures``: the real planner and
executors, keel-api's own routes on a disposable Postgres, the SDK's real tools with the prod card flags), each
``{content, structuredContent, _meta}`` as a host receives it. ``c_study_card_check.mjs`` draws the served
``build_card_html("study")`` with every one under both host dialects (an MCP Apps host posting the tool result; a
``window.openai`` host) at 360, 479, 481 and 760 px, light and dark, and reports what a person sees. This file
asserts 13 §7.1's list:

* the header has the kind chip and the state words; never "Untitled";
* the key chart draws at least its served positions and exactly its served tick words; a thumb draws marks;
* every tile shows its served value and sample words; no visible text is a raw float or a bare ISO timestamp;
* the link row's link is the served one; Expand only where the host lists fullscreen and the layout has one;
* MCP-CARDS (D-420): a face (a done study, a run, a plan, the list) is as tall as its content, with no blank band
  under its last line (supersedes D-332 / D-381's reserved frames inline); the chart read and the refusal keep their
  reserved heights; nothing inside is clipped, and no size change is posted between the first render and +3 s, nor
  on hover; the face draws its served hero and tiles, your bars as pips, a run's dots and its going and next steps;
* hover at three positions reads the served ``read`` words for that index; ← → move it;
* the face draws up to 3 served thumbnails with visible marks and "+N more charts" (D-420d);
* fullscreen is a dashboard (D-420d): selectors and the study link up top, the key chart large, every view a grid
  tile, the check rows with their marks and views, the numbers; a tile focuses and "All charts" goes back;
* an older card for the same study becomes its receipt with the adapter's superseded words;
* a refusal draws the served words and one link, and no price, "upgrade", plan name or billing URL.

Seeded reds, each with a control on the same fixture (13 §7.1's table; the source guard is
``test_policy_scan.py``'s, the 40,000-byte bound ``test_study_card_budget.py``'s):

1. today's-before-RI-602 parser (``study_card_v1.js``, pinned) on the served b64 thumb: "picture drawn" reds;
2. one served tile's value dropped: "every tile shows its words" reds;
3. the list routed through the study layout: "never Untitled" reds;
4. a billing URL in the refusal's card words: the wall scan reds;
5. the old 428 px frame forced back on a running face: "no blank band" reds.

Skips locally without node or keel-app's playwright; under CI it may not (test_card_browser.py).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
CARDS = HERE / "fixtures" / "cards"
FIXTURES = CARDS / "study"
HARNESS = CARDS / "c_study_card_check.mjs"
V1_CARD = CARDS / "study_card_v1.js"
REPO = HERE.parents[3]
PLAYWRIGHT = REPO / "services" / "keel-app" / "node_modules" / "playwright"

#: 13 §4.2 as built (card-study.css): the reserved height per layout, wide (>= 480) and narrow (< 480).
RESERVED = {
    ("study", "1"): (522, 626),
    ("study", "0"): (446, 550),
    ("run", None): (428, 532),
    ("list", None): (342, 346),
    ("chart", None): (352, 356),
    ("refusal", None): (144, 192),
}
#: MCP-CARDS (D-420): the inline face's layouts. A face is as tall as its content (both hosts' rule), which
#: supersedes D-332 / D-381's reserved frames inline; the chart read and the refusal keep theirs until phase 2.
FACE_DONE = ("face-study", "face-run-done")
FACE = (*FACE_DONE, "face-run", "face-plan", "face-list")
RAW_FLOAT = re.compile(r"\d\.\d{4,}")
ISO = re.compile(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d")
#: 13 §7.4's wall, as Research D-422 / plans spec 02 §C3.2 leave it: a card shows no price, no pitch word and no
#: plans, billing or checkout destination anywhere; a plan's NAME appears only inside the limit text a person reached
#: (D-422: "Trader and Pro include more. Plans are changed in the Keel web app."), never as a badge or a chip.
WALL = re.compile(
    r"\$\d|\bper month\b|\b(upgrade|unlock|deal)\b|pricing|billing|checkout",
    re.I,
)
PLAN_NAMES = re.compile(r"\b(Starter|Trader|Quant|Pro|Premium)\b")
#: a link a card may never carry (D-12 / D-422: the one link is the study, a view of it, or Research home)
PLAN_LINK = re.compile(r"billing|pricing|checkout|/plans?\b|tab=billing|upgrade", re.I)


def _outside_limit_text(r: dict) -> str:
    """The card's text with the limit text it drew (a refusal's row) taken out: where no plan may be named."""
    return r["text"].replace(r["refusal"], "") if r.get("refusal") else r["text"]


def _browser() -> None:
    if not shutil.which("node") or not PLAYWRIGHT.exists():
        if os.environ.get("CI"):
            pytest.fail("node or keel-app's playwright is missing under CI (test_card_browser.py)")
        pytest.skip("no node or keel-app playwright here")


def _card_html(
    tmp: Path,
    js: str | None = None,
    css_extra: str = "",
    seed: tuple[str, str] | None = None,
) -> Path:
    from keel.widgets import build_card_html

    html = build_card_html("study")
    if seed is not None:  # a seeded defect: one exact edit of the served card (adapter, js or css)
        assert html.count(seed[0]) == 1, seed[0]
        html = html.replace(seed[0], seed[1])
    if js is not None:
        served = (
            REPO / "packages/keel-trade/keel-sdk/keel/widgets/assets/card-study.js"
        ).read_text()
        assert html.count(served) == 1
        html = html.replace(served, js)
    if css_extra:
        html = html.replace("</style>", css_extra + "</style>", 1)
    path = tmp / "study.html"
    path.write_text(html, encoding="utf-8")
    return path


def _run(card: Path, fixtures: Path, **opts: str) -> dict:
    cmd = ["node", str(HARNESS), "--card", str(card), "--fixtures", str(fixtures)]
    for k, v in opts.items():
        cmd += [f"--{k}", v]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


#: Served study fixtures also drawn WITHOUT their card channel's card_data (``<name>~thumb``): the reply a host gets
#: when the card data is absent (keel-api before it serves card_data, a host that drops `_meta`), which the card
#: draws from card_summary's thumb. Derived from the real envelope by deleting one member, nothing invented.
THUMB_ONLY = ("study-chk_v3-summary", "study-map_v3", "study-cmp_sig")


def _thumb_only(fx: dict) -> dict:
    fx = copy.deepcopy(fx)
    card = (fx.get("_meta") or {}).get("keel/card") or {}
    assert card.pop("card_data", None), (
        "a thumb-only fixture is derived from one that serves card_data"
    )
    fx["structuredContent"].pop("card_data", None)
    return fx


def _fixture(name: str) -> dict:
    base, _, variant = name.partition("~")
    fx = json.loads((FIXTURES / f"{base}.json").read_text(encoding="utf-8"))
    return _thumb_only(fx) if variant == "thumb" else fx


def _envelope(fx: dict) -> dict:
    """The envelope the card reads: structuredContent with the card channel merged back (the adapter's merge)."""
    env = copy.deepcopy(fx["structuredContent"])
    card = (fx.get("_meta") or {}).get("keel/card") or {}

    def merge(into, extra):
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(into.get(k), dict):
                merge(into[k], v)
            elif v is not None or k not in into:
                into[k] = v

    merge(env, card)
    return env


@pytest.fixture(scope="module")
def drawn(tmp_path_factory) -> dict:
    _browser()
    tmp = tmp_path_factory.mktemp("study-card")
    fixtures = tmp / "fixtures"
    fixtures.mkdir()
    for path in FIXTURES.glob("*.json"):
        (fixtures / path.name).write_bytes(path.read_bytes())
    for name in THUMB_ONLY:
        (fixtures / f"{name}~thumb.json").write_text(
            json.dumps(_fixture(f"{name}~thumb")), encoding="utf-8"
        )
    out = _run(_card_html(tmp), fixtures)
    keep = os.environ.get(
        "KEEL_STUDY_CARD_DRAWN"
    )  # a local copy of the measurements, for reading a failure
    if keep:
        Path(keep).write_text(json.dumps(out), encoding="utf-8")
    return out


# ── non-vacuity ───────────────────────────────────────────────────────────────────────────────────────────────


def test_the_fixtures_are_real_and_every_layout_is_drawn(drawn) -> None:
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    names = set(manifest["fixtures"])
    assert {"study-chk_v3", "study-map_v3", "study-cmp_v3v4", "list", "chart-map_v3-view0",
            "run-running", "run-done", "refusal-limit", "refusal-notfound"} <= names  # fmt: skip
    layouts = {r["layout"] for r in drawn["renders"]}
    assert {*FACE, "chart", "refusal"} <= layouts, layouts
    assert {r["dialect"] for r in drawn["renders"]} == {"mcp", "openai"}
    assert {r["width"] for r in drawn["renders"]} == {360, 479, 481, 760}
    assert len(drawn["renders"]) >= 6 * len(names)


# ── every render ──────────────────────────────────────────────────────────────────────────────────────────────


def _renders(drawn, *layouts):
    return [r for r in drawn["renders"] if not layouts or r["layout"] in layouts]


def test_no_render_errs_and_every_card_is_the_v2_template(drawn) -> None:
    for r in _renders(drawn):
        key = (r["name"], r["dialect"], r["width"], r["theme"])
        assert not r["errors"] and not r["renderError"], (key, r["errors"], r["renderError"])
        assert r["tpl"] == "study/2", key
        assert "Untitled" not in r["text"], key


def test_the_header_names_the_kind_and_the_state(drawn) -> None:
    """The face's head (D-420): the title, then one muted line naming the kind, the version once, the note chips, the
    state and the maker, all as served."""
    seen = 0
    for r in _renders(drawn, *FACE_DONE, "face-run", "face-plan"):
        env = _envelope(_fixture(r["name"]))
        summary, study = env.get("card_summary") or {}, env.get("study") or {}
        assert r["faceTitle"] == (study.get("title") or summary.get("title")), (
            r["name"],
            r["faceTitle"],
        )
        kind = (summary.get("kind_label") or study.get("kind_label") or {}).get("short")
        assert kind and r["faceMeta"].startswith(kind), (r["name"], r["faceMeta"], kind)
        state = summary.get("state_words") or study.get("state_words")
        hero_state = ((env.get("card_data") or {}).get("hero") or {}).get("state")
        if r["layout"] == "face-run" and hero_state:
            # polish 1: a run's meta says its state without the count its hero says
            assert hero_state in r["faceMeta"] and r["hero"]["value"] not in r["faceMeta"], (
                r["name"],
                r["faceMeta"],
            )
        elif state and r["layout"] != "face-plan":
            assert state in r["faceMeta"], (r["name"], r["faceMeta"])
        version = summary.get("version") or study.get("version")
        assert r["faceMeta"].count(f"v{version}") == 1, (r["name"], r["faceMeta"])
        seen += 1
    assert seen > 20


def test_every_tile_shows_its_served_words_and_no_raw_number(drawn) -> None:
    """The face (D-420): the served hero and at most three served tiles, each its value, short label and bar words,
    whole; no raw float, no ISO time anywhere."""
    checked = older = 0
    for r in _renders(drawn, *FACE_DONE):
        env = _envelope(_fixture(r["name"]))
        data = env.get("card_data") or {}
        hero, tiles = data.get("hero"), data.get("tiles") or []
        if not hero:
            # a server older than the face (or a share, phase 2): its first served number is the hero, whole
            first = next(
                n
                for n in (env.get("card_summary") or {}).get("numbers") or []
                if n.get("value_words")
            )
            assert r["hero"]["value"] == (first.get("value_short") or first["value_words"]), (
                r["name"],
                r["hero"],
            )
            older += 1
            continue
        assert r["hero"]["value"] == hero["value"] and r["hero"]["label"] == hero["label"], (
            r["name"],
            r["hero"],
        )
        assert len(r["faceTiles"]) == len(tiles) <= 3, (r["name"], r["faceTiles"])
        for drawn_tile, t in zip(r["faceTiles"], tiles, strict=True):
            assert drawn_tile["value"] == t["value"] and drawn_tile["label"] == t["label"], (
                r["name"],
                drawn_tile,
            )
            assert drawn_tile["sub"] == (t.get("sub") or ""), (r["name"], drawn_tile, t)
            assert drawn_tile["value"], (r["name"], "a served tile with no value", t)
            checked += 1
    assert checked > 20 and older > 0
    for r in _renders(drawn):
        assert not RAW_FLOAT.search(r["text"]), (r["name"], RAW_FLOAT.findall(r["text"]))
        assert not ISO.search(r["text"]), (r["name"], ISO.findall(r["text"]))


def _sample_forms(served: str) -> list[str]:
    """CARD-360: a tile's sample as drawn: the served words whole, or cut at a " · " segment end with " …" (the
    tile's title carries them whole)."""
    parts = served.split(" · ")
    return [served] + [" · ".join(parts[:n]) + " …" for n in range(1, len(parts))]


def test_the_link_is_the_served_one_and_expand_only_where_offered(drawn) -> None:
    for r in _renders(drawn):
        env = _envelope(_fixture(r["name"]))
        links = [x for x in r["links"] if x["text"] != "Expand"]
        if r["layout"] == "refusal":
            # D-328 D3 / D-381: a refusal's one link is its served study or Research home (a plan limit's too)
            want = ((env.get("detail") or {}).get("links") or {}).get("app")
        else:
            want = (env.get("links") or {}).get("app") or env.get("hero_url")
        if r["layout"] in (None, ""):
            continue  # a receipt row: its own Open, no link row
        assert [x["href"] for x in links] == ([want] if want else []), (r["name"], links, want)
        offers = r["dialect"] == "mcp"  # the MCP host lists fullscreen; the ChatGPT shim lists none
        assert r["expand"] == (offers and not r["noExpand"]), (r["name"], r["dialect"], r["expand"])


def test_every_inline_card_fits_its_content_and_nothing_is_clipped(drawn) -> None:
    """D-420 (supersedes D-332 / D-381 inline): a face is as tall as what it draws, with no blank band under its last
    line (CARD-RESEARCH baseline: a running card ~40 % empty); the chart read and the refusal keep their reserved
    heights. Nothing is clipped, nothing scrolls sideways, no size change after the first render."""
    measured = faces = 0
    for r in _renders(drawn, *FACE, "chart", "refusal"):
        key = (r["name"], r["dialect"], r["width"], r["theme"])
        if r["face"]:
            assert r["blank"] is not None and 0 <= r["blank"] <= 2, (key, r["blank"])
            faces += 1
        else:
            want = RESERVED[(r["layout"], None)][1 if r["width"] < 480 else 0]
            assert r["reserved"] == want, (key, r["reserved"], want)
            assert abs(r["height"] - want) <= 1, (key, r["height"], want)
        assert r["clipped"] == [], (key, r["clipped"])
        assert r["overflowX"] <= 1, key
        if r["dialect"] == "mcp":
            assert set(r["sizesAfterFirstRender"]) == {r["height"]}, (key, r["sizes"])
        measured += 1
    assert measured > 100 and faces > 80


#: Run cards that draw nothing but their header, held as a ratchet (reds on a new one). Empty since SERVE serves a
#: failed study's card_summary ("Didn't finish") and progress (the platform words); run-failed was blank before.
KNOWN_BLANK_RUNS: set[str] = set()


def test_every_run_card_draws_served_words_beyond_its_header(drawn) -> None:
    """A run face draws its hero (runs done), its run dots, and its steps or its served stopped words."""
    blank = set()
    for r in _renders(drawn, "face-run"):
        said = r["faceSteps"] or r["heroAside"] or r["dots"] or r["runWords"]
        if not (r["hero"] or r["heroAside"] or r["runWords"]) or not said:
            blank.add(r["name"])
    assert blank == KNOWN_BLANK_RUNS, blank


# ── charts ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_key_chart_draws_its_served_positions_and_tick_words(drawn) -> None:
    charts = thumbs = 0
    for r in _renders(drawn, *FACE_DONE, "chart"):
        env = _envelope(_fixture(r["name"]))
        key = (env.get("card_data") or {}).get("key")
        if r["key"] == "chart":
            served = [t["w"] for ax in ("x", "y") for t in ((key.get(ax) or {}).get("ticks") or [])]
            assert sorted(r["ticks"]) == sorted(served), (r["name"], r["ticks"], served)
            if key["kind"] == "line":
                y = key["series"][0]["y"]
                served = sum(1 for i in range(0, len(y), 2) if y[i : i + 2] != "..")
                assert r["keyLinePoints"] >= served, (r["name"], r["keyLinePoints"], served)
            assert r["keyMarks"] > 0, r["name"]
            charts += 1
        elif r["key"] == "thumb":
            assert r["pictureMarks"] >= 2, r["name"]
            thumbs += 1
    assert charts > 0 and thumbs > 0


def test_hover_and_keys_read_the_served_words(drawn) -> None:
    assert drawn["hover"], "no key chart was hovered"
    for name, h in drawn["hover"].items():
        key = _envelope(_fixture(name))["card_data"]["key"]
        read = key.get("read") or {}
        seen = set()
        for at, words in h["reads"]:
            assert at not in (None, ""), (name, h["reads"])
            i = int(at)
            seen.add(i)
            if key["kind"] == "line":
                assert read["x"].split("|")[i] in words, (name, i, words)
                assert read["y"][0].split("|")[i] in words, (name, i, words)
            elif key["kind"] == "heat":
                assert read["cells"].split("|")[i] in words, (name, i, words)
        assert len(seen) == 3, (name, h["reads"])  # three positions, three indexes
        assert h["keys"] == ["0", "1"], (name, h["keys"])


def _chart_link(data: dict, v: str) -> str | None:
    tpl = (data.get("links") or {}).get("chart")
    return tpl.replace("{v}", v) if tpl and v else None


def test_expand_is_a_dashboard_of_every_chart_focus_and_back(drawn) -> None:
    """D-420d (founder: "the expand view can show more too, instead of the same thing just bigger"): Expand draws the
    selectors (Charts · Checks · Numbers, the sections present) and the study's one link up top, the key chart large
    with its axes, every other view as a grid tile at once (2-3 columns at 760 px) that draws marks or served words,
    never an empty box, each with its own link to its view; "Your checks" as rows with their mark, words and, where
    the row names its view, that view's small chart; the numbers. A tap focuses a tile's chart, large with its axes and
    link, and the served "All charts" goes back. A thumbnail tapped inline asks for fullscreen and opens on its chart.
    Non-vacuity: at least one study draws a check row's chart and one taps a thumbnail."""
    assert drawn["full"]
    row_charts = taps = dashboards = 0
    for name, f in drawn["full"].items():
        env = _envelope(_fixture(name))
        state = (env.get("study") or {}).get("state")
        if not f["dash"]["drawn"]:
            # nothing to show yet keeps its face in fullscreen: in flight, stopped empty, or a Draft
            assert state in (
                "planned",
                "running",
                "queued",
                "cancel_requested",
                "cancelled",
                "failed",
            ), (name, state)
            continue
        dashboards += 1
        data = env.get("card_data") or {}
        copy_ = data.get("copy") or {}
        summary = env.get("card_summary") or {}
        d = f["dash"]
        entries = [e for e in data.get("charts") or [] if e.get("title")]
        grid = [e for e in entries if e.get("family") != "Checks"]
        rows = (data.get("checks") or {}).get("rows") or []
        numbers = [n for n in summary.get("numbers") or [] if n.get("label")]
        want_seg = [
            w
            for w, on in (
                (copy_.get("seg_charts") or copy_.get("charts"), bool(data.get("key") or grid)),
                (copy_.get("seg_checks"), bool(rows)),
                (copy_.get("seg_numbers"), bool(numbers)),
            )
            if on and w
        ]
        assert d["seg"] == want_seg, (name, d["seg"], want_seg)
        assert d["segHidden"] == (len(want_seg) < 2), (name, d["segHidden"])
        url = (env.get("links") or {}).get("app") or env.get("hero_url")
        assert d["open"] == url and (d["linkRowHidden"] or not url), (name, d["open"], url)
        if data.get("key"):
            assert d["keyMarks"] > 0 and d["keyTicks"] > 0, (name, d["keyMarks"], d["keyTicks"])
        assert [int(t["index"]) for t in d["tiles"]] == [e["index"] for e in grid], name
        for t, e in zip(d["tiles"], grid, strict=True):
            assert t["title"] == e["title"], (name, t)
            assert t["marks"] > 0 or t["text"], (name, "an empty tile", t)  # never an empty box
            if t["drawn"] == "chart":
                assert t["marks"] > 0, (name, t)
            assert t["open"] == _chart_link(data, e.get("v")), (name, t["open"])
        if len(d["tiles"]) >= 2:
            assert len({t["left"] for t in d["tiles"]}) >= 2, (name, "one column at 760 px")
        assert len(d["checkRows"]) == len(rows), (name, len(d["checkRows"]), len(rows))
        by_label = {r["label"]: r for r in rows}
        for drawn_row in d["checkRows"]:
            row = next(r for label, r in by_label.items() if label in drawn_row["text"])
            assert row["locked"] or row["value_words"] in drawn_row["text"], (name, drawn_row)
            if isinstance(row.get("view"), int):
                assert drawn_row["chart"] == str(row["view"]) and drawn_row["chartMarks"] > 0, (
                    name,
                    drawn_row,
                )
                row_charts += 1
            else:
                assert drawn_row["chart"] is None, (name, drawn_row)
        assert len(d["numbers"]) == len(numbers), (name, d["numbers"])
        if grid:
            fo, back = f["focus"], f["back"]
            assert fo["shown"] and fo["dashHidden"] and fo["index"] == str(grid[0]["index"]), (
                name,
                fo,
            )
            if grid[0].get("form") == "chart" and (grid[0].get("chart") or {}).get("kind") not in (
                "table",
                None,
            ):
                assert fo["svg"] and fo["ticks"] > 0, (name, fo)
            assert fo["back"] == copy_.get("all_charts"), (name, fo["back"])
            assert fo["open"] == _chart_link(data, grid[0].get("v")), (name, fo["open"])
            assert back == {"dashHidden": False, "focusHidden": True}, (name, back)
        if f["tap"]:
            tap = f["tap"]
            assert ["rdm", "fullscreen"] in tap["calls"], (name, tap)
            assert tap["layout"] == "full" and tap["focus"] == tap["index"], (name, tap)
            taps += 1
    assert dashboards >= 6 and row_charts > 0 and taps > 0, (dashboards, row_charts, taps)


def test_the_face_draws_up_to_three_thumbnails_that_have_values(drawn) -> None:
    """D-420d (founder: "bring back up to 3 small thumbnails plus +N more charts ... never an empty or degenerate box";
    supersedes DESIGN.md §6's strip removal): a done face draws exactly the served ``card_data.thumbs`` under its key
    chart, each with visible marks in a real picture box and its view's title, three across even at 360 px, and the
    served "+N more charts". Non-vacuity: thumbnails drawn on several studies at both widths."""
    seen = 0
    for r in _renders(drawn, *FACE_DONE):
        data = _envelope(_fixture(r["name"])).get("card_data") or {}
        served = data.get("thumbs") or []
        assert [int(t["index"]) for t in r["thumbs"]] == served, (r["name"], r["thumbs"], served)
        by = {e["index"]: e for e in data.get("charts") or []}
        for t in r["thumbs"]:
            assert t["marks"] > 0 and t["w"] > 40 and t["h"] >= 40, (r["name"], r["width"], t)
            assert t["caption"] == by[int(t["index"])]["title"] == t["title"], (r["name"], t)
            assert 1 <= t["captionLines"] <= 2, (r["name"], r["width"], t)
        if served:
            assert len({t["top"] for t in r["thumbs"]}) == 1, (r["name"], r["width"], "not one row")
            assert r["more"] == ((data.get("copy") or {}).get("more_views") or ""), (
                r["name"],
                r["more"],
            )
            seen += 1
    assert seen > 4, seen


def test_seed_a_thumbnail_with_no_visible_marks_reds(tmp_path) -> None:
    """D-420d's "never an empty box" has a seeded red: the thumbnails' marks hidden -> a thumbnail with no visible
    mark, red; control: the same fixture as served draws marks in every thumbnail."""
    _browser()
    fx = _fixture("study-chk_v3")
    assert _envelope(fx)["card_data"]["thumbs"]  # non-vacuity
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["thumbs"] and all(t["marks"] > 0 for t in control["thumbs"]), control["thumbs"]
    (tmp_path / "seed").mkdir()
    hide = ".sf-mini .st-line,.sf-mini .st-bar,.sf-mini .st-bin,.sf-mini .st-dot,.sf-mini .st-cell{display:none}"
    seeded = _one(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=hide), {"x": fx})[0]
    assert [t for t in seeded["thumbs"] if t["marks"] == 0], seeded["thumbs"]  # red


def test_an_older_card_for_the_same_study_becomes_its_receipt(drawn) -> None:
    s = drawn["supersede"]
    assert s, "the supersede pair was not drawn"
    assert s["older"]["rr"] and (
        "superseded" in s["older"]["rr"] or "shown below" in s["older"]["rr"]
    )
    assert s["newer"]["rr"] is None and s["newer"]["layout"] in FACE_DONE


def test_a_refusal_draws_its_words_one_link_and_no_wall(drawn) -> None:
    refusals = _renders(drawn, "refusal")
    assert refusals
    for r in refusals:
        env = _envelope(_fixture(r["name"]))
        detail = env.get("detail") or {}
        want = detail.get("card_words") or env["message"].split("\n")[0]
        assert r["refusal"] == want, (r["name"], r["refusal"])
        assert len([x for x in r["links"] if x["text"] != "Expand"]) <= 1


def test_no_card_draws_a_price_a_plan_an_upgrade_or_billing(drawn) -> None:
    """13 §7.4 (WL-CU-14) as Research D-422 leaves it: the rendered text of every card, every layout and dialect,
    carries no price, pitch word or billing destination; a plan is named only inside the limit text a person reached
    (never a badge or chip); no link goes to plans, billing or checkout. View titles are data, not copy: a listed
    policy word in one ("Trade stats") is reported, not failed."""
    titles = []
    for r in _renders(drawn):
        assert not WALL.search(r["text"]), (r["name"], WALL.findall(r["text"]))
        assert not PLAN_NAMES.search(_outside_limit_text(r)), (
            r["name"],
            PLAN_NAMES.findall(r["text"]),
        )
        assert not [x for x in r["links"] if PLAN_LINK.search(x["href"] or "")], (
            r["name"],
            r["links"],
        )
        if re.search(r"\btrade", r["text"], re.I):
            titles.append(r["name"])
    if titles:
        print("view titles carrying a listed policy word (data, reported):", sorted(set(titles)))


def test_a_map_draws_its_axis_names_and_a_key_for_its_outlined_cells(drawn) -> None:
    """D-352 (3): the served axis names beside the axes, and one line naming what each outline means, in the served
    words, only for the outlines the chart carries."""
    seen = 0
    for r in _renders(drawn, *FACE_DONE, "chart"):
        if r["key"] != "chart":
            continue
        data = _envelope(_fixture(r["name"])).get("card_data") or {}
        key = data.get("key") or {}
        if r["layout"] == "chart":
            continue  # the chart read draws its own view's chart; its key is checked in the study layout
        if key.get("kind") != "heat":
            continue
        axes = [(key.get(a) or {}).get("label") for a in ("y", "x")]
        assert r["axisNames"] == [w for w in axes if w], (r["name"], r["axisNames"])
        copy, outline = data.get("copy") or {}, key.get("outline") or {}
        want = [
            w
            for present, w in (
                (outline.get("high"), copy.get("highest")),
                (outline.get("low"), copy.get("lowest")),
                (
                    key.get("ring") not in (None, ""),
                    (key.get("read") or {}).get("ring_words") or copy.get("yours"),
                ),
            )
            if present and w
        ]
        assert r["heatKey"] == want, (
            r["name"],
            r["heatKey"],
            want,
        )  # no outlines served, no key line
        seen += bool(want)
    assert seen > 0


def _pip_state(row: dict) -> str:
    met = row.get("met")
    return "met" if met is True else "not_met" if met is False else "unmeasured"


def test_your_bars_are_pips_and_the_head_no_wordmark(drawn) -> None:
    """D-420: a Check with bars you set draws one pip per bar (met filled, not met hollow, not measured dashed) beside
    its hero, each pip's title its row's served words; a Check with no bars draws none (D-407: no verdict). D-352
    (4): no "Keel" wordmark before the title; the maker is its served words on the meta line."""
    pips = makers = plain = 0
    for r in _renders(drawn, *FACE_DONE):
        assert r["brand"] == 0, (r["name"], r["dialect"])
        env = _envelope(_fixture(r["name"]))
        maker = (env.get("card_summary") or {}).get("maker_words")
        if maker:
            assert maker in r["faceMeta"], (r["name"], r["faceMeta"])
            makers += 1
        data = env.get("card_data") or {}
        rows = [
            row for row in (data.get("checks") or {}).get("rows") or [] if not row.get("locked")
        ]
        if any(row.get("bar_words") for row in rows):
            # polish 5: met first, then not met, then not measured, each group in the served order
            order = {"met": 0, "not_met": 1, "unmeasured": 2}
            ranked = sorted(enumerate(rows), key=lambda kb: (order[_pip_state(kb[1])], kb[0]))
            want = [_pip_state(b) for _, b in ranked]
            assert r["pips"] == want, (r["name"], r["pips"], want)
            assert all(
                b["label"] in title for (_, b), title in zip(ranked, r["pipTitles"], strict=True)
            )
            # polish 4: a tile held to a bar wears that bar's pip; a tile with no bar wears none
            for t in r["faceTiles"]:
                assert (t["pip"] or None) == t["state"], (r["name"], t)
            pips += 1
        else:
            assert r["pips"] == [], (r["name"], r["pips"])  # D-407: no bars, no verdict marks
            plain += 1
    assert pips > 0 and makers > 0 and plain > 0


def test_run_cards_take_their_content_height_live_or_terminal(drawn) -> None:
    """D-420 (supersedes D-354 (4)'s live frame): queued, running, stopped and failed run cards are all as tall as
    what they say; a stopped or failed one is marked terminal, a live one is not; the run dots count the runs."""
    by = {}
    for r in _renders(drawn, "face-run"):
        env = _envelope(_fixture(r["name"]))
        state = (env.get("study") or {}).get("state")
        by.setdefault(state, set()).add(r["terminal"])
        p = env.get("progress") or {}
        if p.get("children"):
            assert len(r["dots"]) == min(int(p["children"]), 96), (r["name"], len(r["dots"]))
            assert r["dots"].count("done") >= min(int(p.get("done") or 0), 96) - 1, (
                r["name"],
                r["dots"],
            )
    assert by.get("running") == {False} and by.get("cancelled", {True}) == {True}, by


def test_a_chart_card_says_its_title_once_and_its_key_sentence_once(drawn) -> None:
    """D-354 (1): a single-chart card drops a chart title equal to the card title. (2): the served key sentence is
    not drawn beside a key line; where none is drawn it is its own line, whole."""
    checked = 0
    for r in _renders(drawn, "chart", *FACE_DONE):
        if r["key"] != "chart":
            continue
        if r["layout"] == "chart":
            assert r["chartName"] != r["faceTitle"], (r["name"], r["chartName"])
        data = _envelope(_fixture(r["name"])).get("card_data") or {}
        key = data.get("key") or {}
        # D-420: the face draws no chart note (one caption line; the note is in Expand)
        note = None
        if r["layout"] in FACE_DONE and key.get("note_words"):
            assert key["note_words"] not in r["plainText"], (r["name"], key["note_words"])
            checked += 1
        if r["heatKey"]:
            notes = {key.get("note_words")} | {
                (e.get("chart") or {}).get("note_words") for e in data.get("charts") or []
            }
            assert not [n for n in notes if n and n in r["text"]], (r["name"], r["text"][:200])
            checked += 1
        elif note:
            assert r["note"] == note, (r["name"], r["note"], note)
            checked += 1
    assert checked > 0


def test_no_served_action_label_is_drawn_as_plain_text(drawn) -> None:
    """D-354 (3): an action's words appear only on the control that does it: no served action label (the copy's
    open_* labels, a served next step's label) in a card's text outside its links and buttons."""
    labels = set()
    for r in _renders(drawn):
        env = _envelope(_fixture(r["name"]))
        copy = (env.get("card_data") or {}).get("copy") or {}
        acts = {v for k, v in copy.items() if k.startswith("open_") and v}
        acts |= {
            n.get("label") for n in env.get("next") or [] if isinstance(n, dict) and n.get("label")
        }
        labels |= acts
        for label in acts:
            assert label not in r["plainText"], (r["name"], label)
    assert labels


# ── seeded reds, each with its control ──────────────────────────────────────────────────────────────────────


def _seed_dir(tmp: Path, names: dict[str, dict]) -> Path:
    d = tmp / "fixtures"
    d.mkdir(exist_ok=True)
    for name, fx in names.items():
        (d / f"{name}.json").write_text(json.dumps(fx), encoding="utf-8")
    return d


def _one(tmp: Path, card: Path, fixtures: dict[str, dict]) -> list[dict]:
    out = _run(
        card, _seed_dir(tmp, fixtures), widths="760", themes="dark", dialects="mcp", full="0"
    )
    return out["renders"]


def test_seed_the_v1_parser_draws_no_picture(tmp_path) -> None:
    _browser()
    fx = _fixture("study-chk_v3-summary~thumb")
    assert ((_envelope(fx).get("card_summary") or {}).get("thumb") or {}).get("facts", {}).get(
        "enc"
    ) == "b64"
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["key"] == "thumb" and control["pictureMarks"] >= 2  # control: the v2 decoder
    (tmp_path / "seed").mkdir()
    seeded = _one(tmp_path / "seed", _card_html(tmp_path / "seed", V1_CARD.read_text()), {"x": fx})[
        0
    ]
    assert seeded["pictureMarks"] == 0  # red: the v1 parser draws no picture


def test_seed_a_tile_without_its_value_is_caught(tmp_path) -> None:
    """Seeded red for "every tile shows its served words": a served tile's value removed draws an empty value;
    control: the real fixture's tiles all carry theirs."""
    _browser()
    fx = _fixture("study-chk_v3")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["faceTiles"] and all(t["value"] for t in control["faceTiles"])
    seeded_fx = copy.deepcopy(fx)
    seeded_fx["_meta"]["keel/card"]["card_data"]["tiles"][0]["value"] = ""
    seeded = _one(tmp_path, _card_html(tmp_path), {"x": seeded_fx})[0]
    assert seeded["faceTiles"][0]["value"] == ""  # red for "every tile shows its served words"
    assert not RAW_FLOAT.search(seeded["text"])


def test_a_ringed_cell_is_keyed_in_its_served_words(tmp_path) -> None:
    """D-354 (5c): the map key names the ringed cell (your setting) in the served ``read.ring_words``. No real fixture
    rings a cell today (map_v3's setting is off its grid), so the ring and its words are set on the real map_v3
    answer; control: the same answer unringed draws no such item."""
    _browser()
    fx = _fixture("study-map_v3")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    ringed = copy.deepcopy(fx)
    key = ringed["_meta"]["keel/card"]["card_data"]["key"]
    key["ring"] = 5
    key.setdefault("read", {})["ring_words"] = "Your setting: Lookback 2, Hold 6: 0.41"
    (tmp_path / "r").mkdir()
    seeded = _one(tmp_path / "r", _card_html(tmp_path / "r"), {"x": ringed})[0]
    assert "Your setting: Lookback 2, Hold 6: 0.41" not in control["heatKey"]
    assert seeded["heatKey"][-1] == "Your setting: Lookback 2, Hold 6: 0.41", seeded["heatKey"]
    assert seeded["clipped"] == [], seeded["clipped"]


def test_seed_the_list_through_the_study_layout_reads_untitled(tmp_path) -> None:
    _browser()
    fx = _fixture("list")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["layout"] == "face-list" and "Untitled" not in control["text"]
    seeded_fx = copy.deepcopy(fx)
    seeded_fx["structuredContent"]["view"]["mode"] = "study"
    seeded = _one(tmp_path, _card_html(tmp_path), {"x": seeded_fx})[0]
    assert "Untitled" in seeded["text"]  # red for "never Untitled"


def test_seed_a_billing_url_in_the_refusal_reds_the_wall_scan(tmp_path) -> None:
    _browser()
    fx = _fixture("refusal-limit")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert not WALL.search(control["text"])
    seeded_fx = copy.deepcopy(fx)
    detail = seeded_fx["structuredContent"]["detail"]
    detail["card_words"] = (
        detail.get("card_words") or ""
    ) + " See https://app.usekeel.io/settings/billing."
    seeded = _one(tmp_path, _card_html(tmp_path), {"x": seeded_fx})[0]
    assert WALL.search(seeded["text"])  # red for the wall scan


def test_d422_a_limit_card_names_the_plans_in_its_text_and_nowhere_else(tmp_path) -> None:
    """Research D-422 (amends D-381 (1) to mcp-conversion D-14; plans spec 02 §C3.2): a limit card draws the served
    wall with the plans that include more ("Trader and Pro include more. Plans are changed in the Keel web app."),
    names only: no price, no pitch word, no plan badge, and its one link is the study or Research home, never plans,
    billing or checkout. Control: the refusal draws the clause and the guard is clean. SEEDS, each red: a plan chip
    on a study card (a badge); the refusal's link pointed at billing."""
    _browser()
    fx = _fixture("refusal-limit")
    env = _envelope(fx)
    clause = re.search(r"([A-Z][\w ,]*?) includes? more\.", env["message"])
    assert clause and "Plans are changed in the Keel web app." in env["message"]
    names = [n for n in re.split(r",\s*|\s+and\s+", clause.group(1)) if n]
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["layout"] == "refusal" and clause.group(0) in control["refusal"], control[
        "refusal"
    ]
    assert names and all(re.search(rf"\b{re.escape(n)}\b", control["refusal"]) for n in names), (
        names
    )
    assert not WALL.search(control["text"]) and not PLAN_NAMES.search(_outside_limit_text(control))
    links = [x["href"] for x in control["links"] if x["text"] != "Expand"]
    assert links == [env["detail"]["links"]["app"]] and not PLAN_LINK.search(links[0]), links
    # SEED 1: a plan badge on a study card
    badge = copy.deepcopy(_fixture("study-chk_v3"))
    badge["_meta"]["keel/card"]["card_data"]["chips"] = ["Runs on Trader"]
    (tmp_path / "s1").mkdir()
    s1 = _one(tmp_path / "s1", _card_html(tmp_path / "s1"), {"x": badge})[0]
    assert PLAN_NAMES.search(_outside_limit_text(s1)), s1["text"][:300]  # red
    # SEED 2: the limit card's link at billing
    billing = copy.deepcopy(fx)
    billing["structuredContent"]["detail"]["links"]["app"] = (
        "https://app.usekeel.io/settings?tab=billing"
    )
    (tmp_path / "s2").mkdir()
    s2 = _one(tmp_path / "s2", _card_html(tmp_path / "s2"), {"x": billing})[0]
    assert [x for x in s2["links"] if PLAN_LINK.search(x["href"] or "")], s2["links"]  # red


def _overlaps(renders: list[dict]) -> list[tuple]:
    return [
        (r["name"], r["dialect"], r["width"], r["theme"], r["overlap"]["pairs"])
        for r in renders
        if r["overlap"]["pairs"]
    ]


def test_no_two_blocks_of_any_card_overlap(drawn) -> None:
    """MCP-CARDS (founder 11-22: "this one card looks a bit messed up", the failed run's dots drawn over its words):
    in every fixture, dialect, theme and width the harness draws, no two blocks of the card overlap: the body's
    blocks and the actions, and the blocks inside the run lead, the hero, the words, the tiles and the thumbnails.
    Non-vacuity: every card compared at least two boxes, over a thousand in all."""
    renders = _renders(drawn)
    assert not _overlaps(renders), _overlaps(renders)[:8]
    assert all(r["overlap"]["compared"] >= 2 for r in renders), [
        (r["name"], r["overlap"]) for r in renders if r["overlap"]["compared"] < 2
    ][:5]
    assert sum(r["overlap"]["compared"] for r in renders) > 1000


def test_no_two_blocks_overlap_at_a_phone_and_a_wide_desktop(tmp_path) -> None:
    """The same probe at 390 and 1000 px (the founder's widths beside the suite's 360, 479, 481 and 760), both
    dialects and both themes, over every fixture. Non-vacuity: every fixture drawn in all eight arms."""
    _browser()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for path in FIXTURES.glob("*.json"):
        if path.name != "MANIFEST.json":
            (fixtures / path.name).write_bytes(path.read_bytes())
    out = _run(_card_html(tmp_path), fixtures, widths="390,1000", themes="light,dark", full="0")
    renders = out["renders"]
    names = {r["name"] for r in renders}
    assert len(names) >= 20 and len(renders) >= 8 * 20, (len(names), len(renders))
    assert not _overlaps(renders), _overlaps(renders)[:8]
    assert all(r["overlap"]["compared"] >= 2 for r in renders)


def test_seed_the_failed_cards_words_over_its_dots_reds_the_overlap_probe(tmp_path) -> None:
    """The founder's failed card has a seeded red: its words pulled 6 px into its dots (the negative margin it had,
    in a plain block) -> the probe names the dots × words pair. Control: the card as built draws the dots, then each
    served sentence on its own line, with nothing over anything, and never "on our side"."""
    _browser()
    fx = {"x": _fixture("run-failed")}
    control = _one(tmp_path, _card_html(tmp_path), fx)[0]
    assert control["layout"] == "face-run" and control["dots"], control["layout"]
    assert control["overlap"]["pairs"] == [] and control["overlap"]["compared"] >= 4, control[
        "overlap"
    ]
    stopped = _envelope(fx["x"])["progress"]["stopped"]
    assert len(control["asideLines"]) >= 2 and " ".join(control["asideLines"]) == stopped, control[
        "asideLines"
    ]
    assert "on our side" not in control["text"]
    (tmp_path / "seed").mkdir()
    pulled = ".sf-run-lead{display:block!important}.sf-hero-aside{margin-top:-6px!important}"
    seeded = _one(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=pulled), fx)[0]
    assert any("sf-progress" in p and "sf-hero-aside" in p for p in seeded["overlap"]["pairs"]), (
        seeded["overlap"]
    )  # red


def test_seed_a_reserved_frame_on_the_face_leaves_a_blank_band(tmp_path) -> None:
    """D-420's content height has a seeded red: the old reserved frame (428 px) forced back on a running face leaves a
    blank band under its last line; control: the face as served has none."""
    _browser()
    fx = _fixture("run-running")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["layout"] == "face-run" and 0 <= control["blank"] <= 2, control["blank"]
    (tmp_path / "seed").mkdir()
    frame = "body[data-face] .keel-card{height:428px!important;box-sizing:border-box}"
    seeded = _one(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=frame), {"x": fx})[0]
    assert seeded["blank"] > 40, seeded["blank"]  # red: the blank band the frame left


def test_d381_no_card_state_leaves_its_reserved_space_empty(drawn) -> None:
    """D-381 (2) on the face (D-420): a queued or running card draws its run dots and the step going plus the next,
    in served words; a get of a running study the same; a stopped study with a key chart is its done face; a Draft no
    run has touched shows its plan's words; the meta line never repeats... and no face leaves a blank band (the
    height test). Control: a done face draws no step list."""
    seen = {"run-running": 0, "run-queued": 0, "study-running": 0, "study-planned": 0}
    for r in _renders(drawn):
        env = _envelope(_fixture(r["name"]))
        data = env.get("card_data") or {}
        steps = data.get("steps") or []
        if r["name"] in seen and r["name"] != "study-planned":
            assert r["layout"] == "face-run", (r["name"], r["layout"])
            assert steps and r["faceSteps"], (r["name"], r["dialect"], r["width"])
            assert r["faceSteps"][0]["label"] == steps[0]["label"], (r["name"], r["faceSteps"])
            assert len(r["faceSteps"]) == min(2, len(steps))
            seen[r["name"]] += 1
        if r["layout"] in FACE_DONE:
            assert r["faceSteps"] == [], r["name"]  # control
        if r["name"] == "study-planned":
            draft = (env.get("study") or {}).get("draft") or {}
            chips = data.get("chips") or []
            assert r["layout"] == "face-plan", r["layout"]
            said = draft.get("words")
            assert (not said or said in r["text"]) and all(c in r["faceMeta"] for c in chips), (
                r["text"],
                chips,
            )
            seen["study-planned"] += 1
    assert all(seen.values()), seen


# ── CARD-360: words cut at 360 px (CARD-FINAL's B4 matrix, four S3s) ─────────────────────────────────────────────


def _draw(tmp: Path, card: Path, fixtures: dict[str, dict], widths: str = "360,760") -> dict:
    out = _run(
        card, _seed_dir(tmp, fixtures), widths=widths, themes="light", dialects="mcp", full="0"
    )
    return {(r["name"], r["width"]): r for r in out["renders"]}


def _map_6x6() -> dict:
    """study-map_v3 carrying a real 6 x 6 map that writes its values in its cells (``study_key_map_6x6.json``)."""
    fx = copy.deepcopy(_fixture("study-map_v3"))
    key = json.loads((CARDS / "study_key_map_6x6.json").read_text(encoding="utf-8"))["key"]
    assert key["values_in_cells"] and key["cols"] == key["rows"] == 6
    fx["_meta"]["keel/card"]["card_data"]["key"] = key
    return fx


#: The guard in card-study.js cellValues: a value that overflows its cell drops the whole layer.
CELL_GUARD = "        plot.removeChild(layer);\n        return;\n"


def test_card360_a_map_value_is_drawn_whole_or_not_at_all(tmp_path) -> None:
    """CARD-360 #1 (map-done, chart-map at 360 px): a 6-column map's values fit a 50 px cell, but their 18 px line
    overflowed the 15 px row the size gate allows, so every value was clipped. The cell's line is the tiny line (14
    px), and a layer with ANY value overflowing its cell is dropped whole (the readout still says every value).
    Non-vacuity: 36 values at 760 and at 360. SEEDS: (a) the 18 px line back -> the guard drops the layer at 360,
    "drawn at 360" reds; (b) the guard removed under a 30 px stress font (the face's cells are larger; was 16 px) -> values are
    half drawn, "never cut" reds;
    control for (b): the same stress WITH the guard draws none and cuts none."""
    _browser()
    fx = {"m": _map_6x6()}
    r = _draw(tmp_path, _card_html(tmp_path), fx)
    for w in (360, 760):
        assert r[("m", w)]["cut"]["cellWords"] == 36, (
            w,
            r[("m", w)]["cut"],
        )  # non-vacuity: every value drawn
        assert r[("m", w)]["cut"]["cells"] == [], (w, r[("m", w)]["cut"]["cells"])
    (tmp_path / "a").mkdir()
    # the face's rows are taller (a 6-row map at 360 px has ~24 px rows): the seed squeezes the plot to ~10 px rows,
    # which the 14 px line does not fit
    line18 = ".sf-chart .st-chart-body{height:78px!important}"
    a = _draw(tmp_path / "a", _card_html(tmp_path / "a", css_extra=line18), fx, "360")[("m", 360)]
    assert a["cut"]["cellWords"] == 0 and a["cut"]["cells"] == [], a[
        "cut"
    ]  # red for "drawn at 360"
    stress = ".st-cellwords{font-size:30px!important}"
    (tmp_path / "c").mkdir()
    c = _draw(tmp_path / "c", _card_html(tmp_path / "c", css_extra=stress), fx, "360")[("m", 360)]
    assert c["cut"]["cellWords"] == 0 and c["cut"]["cells"] == [], c[
        "cut"
    ]  # control: the guard holds
    (tmp_path / "b").mkdir()
    b_card = _card_html(tmp_path / "b", css_extra=stress, seed=(CELL_GUARD, "        break;\n"))
    b = _draw(tmp_path / "b", b_card, fx, "360")[("m", 360)]
    assert b["cut"]["cells"], b["cut"]  # red for "never cut"


def test_card360_a_face_tile_keeps_its_value_whole(tmp_path) -> None:
    """D-420 (supersedes CARD-360 #2: the face's tiles carry no sample; the caption says the window once): a tile's
    value is never cut at 360 or 760 px; a long label may ellipsize, its title carrying the label whole.
    Non-vacuity: the signal study's labels are long. SEED: tiles squeezed to 40 px -> a value is cut, red."""
    _browser()
    fx = {"s": _fixture("study-sig_lowvol")}
    tiles = (_envelope(fx["s"]).get("card_data") or {}).get("tiles") or []
    assert tiles and max(len(t["label"]) for t in tiles) > 24  # non-vacuity
    r = _draw(tmp_path, _card_html(tmp_path), fx)
    for w in (360, 760):
        rec = r[("s", w)]
        assert [t["value"] for t in rec["faceTiles"]] == [t["value"] for t in tiles], (
            w,
            rec["faceTiles"],
        )
        assert not [t for t in rec["faceTiles"] if t["cut"]], (w, rec["faceTiles"])
        assert all(
            d["title"] == t.get("title") for t, d in zip(tiles, rec["faceTiles"], strict=True)
        )
    (tmp_path / "seed").mkdir()
    squeeze = (
        ".sf-tile{max-width:40px!important;overflow:hidden}.sf-tile-v{max-width:40px!important}"
    )
    s = _draw(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=squeeze), fx, "360")[
        ("s", 360)
    ]
    assert [t for t in s["faceTiles"] if t["cut"]], s["faceTiles"]  # red


def test_card360_a_plan_row_gives_its_work_words_their_own_line(tmp_path) -> None:
    """CARD-360 #3 (plan-check, plan-map at 360 px: "22 runs, 1 reused · under a …"): below 480 px a plan row's
    work words have line 2 to themselves (two lines before a cut) and Open / Show steps sit on line 3; from 480 px
    up the row is as it was (actions on line 2). Non-vacuity: plan-map's words are longer than a 360 px line. SEED:
    placeKeepActions never runs -> the actions stay on line 2 and the words are cut, both arms red."""
    _browser()
    plans = HERE / "fixtures" / "cards" / "plan"
    fx = {
        n: {"structuredContent": json.loads((plans / f"{n}.json").read_text(encoding="utf-8"))}
        for n in ("plan-check", "plan-map")
    }
    r = _draw(tmp_path, _card_html(tmp_path), fx, "360,479,481,760")
    for n in fx:
        for w in (360, 479):
            assert r[(n, w)]["cut"]["keepWords"] == [], (n, w, r[(n, w)]["cut"])
            assert r[(n, w)]["cut"]["keepActsLine"] == "rr-l3", (n, w, r[(n, w)]["cut"])
        for w in (481, 760):
            assert r[(n, w)]["cut"]["keepActsLine"] == "rr-l2", (
                n,
                w,
                r[(n, w)]["cut"],
            )  # wide: unchanged
    (tmp_path / "seed").mkdir()
    seed = ("    placeKeepActions(row);\n", "")
    s = _draw(tmp_path / "seed", _card_html(tmp_path / "seed", seed=seed), fx, "360")
    assert s[("plan-map", 360)]["cut"]["keepActsLine"] == "rr-l2"  # red for "actions on line 3"
    assert s[("plan-map", 360)]["cut"]["keepWords"], s[("plan-map", 360)][
        "cut"
    ]  # red for "words not cut"


def test_card360_a_legend_is_its_own_line_on_the_face(tmp_path) -> None:
    """CARD-360 #4 on the face (D-420): the chart's name and its resting readout share line 1; a legend is its own
    line under them at every width, so neither the name nor a key is cut ("Grow… — Momentum rotation v4" at 360).
    SEED: the legend put back inline (no wrap) -> at 360 the name or a key is cut, red; control: 760 holds both."""
    _browser()
    fx = copy.deepcopy(_fixture("study-cmp_v3v4"))
    key = fx["_meta"]["keel/card"]["card_data"]["key"]
    assert key["title"] == "Growth" and len(key["series"]) == 2
    key["title"] = "Growth of 1"
    r = _draw(tmp_path, _card_html(tmp_path), {"c": fx})
    for w in (360, 760):
        cut = r[("c", w)]["cut"]
        assert cut["legendBelow"] and not cut["titleName"] and not cut["legend"], (w, cut)
        assert r[("c", w)]["clipped"] == [], (w, r[("c", w)]["clipped"])
    (tmp_path / "seed").mkdir()
    inline = (
        ".sf-chart .st-chart-title{flex-wrap:nowrap!important}"
        ".sf-chart .st-chart-title .st-legend{order:0!important;flex:0 1 auto!important;white-space:nowrap}"
        ".sf-chart .st-readout{flex-basis:auto!important}"
    )
    s = _draw(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=inline), {"c": fx}, "360")[
        ("c", 360)
    ]
    assert s["cut"]["titleName"] or s["cut"]["legend"] or not s["cut"]["legendBelow"], s[
        "cut"
    ]  # red
