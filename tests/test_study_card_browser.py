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
* the rendered height is the layout's reserved height for its band (±1 px), nothing inside is clipped, and no
  size change is posted between the first render and +3 s, nor on hover;
* hover at three positions reads the served ``read`` words for that index; ← → move it;
* fullscreen draws one tab per chart (axes on every chart tab) and "Your checks" lists its rows with their words;
* an older card for the same study becomes its receipt with the adapter's superseded words;
* a refusal draws the served words and one link, and no price, "upgrade", plan name or billing URL.

Seeded reds, each with a control on the same fixture (13 §7.1's table; the source guard is
``test_policy_scan.py``'s, the 40,000-byte bound ``test_study_card_budget.py``'s):

1. today's-before-RI-602 parser (``study_card_v1.js``, pinned) on the served b64 thumb: "picture drawn" reds;
2. one number's ``value_words`` dropped: "every tile shows its words" reds;
3. the list routed through the study layout: "never Untitled" reds;
4. a billing URL in the refusal's card words: the wall scan reds;
5. an unclamped lead: "nothing is clipped" reds.

Skips locally without node or keel-app's playwright; under CI it may not (test_card_browser.py).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from collections import Counter
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
    ("study", "1"): (506, 640),
    ("study", "0"): (430, 564),
    ("run", None): (358, 472),
    ("list", None): (326, 360),
    ("chart", None): (336, 370),
    ("refusal", None): (144, 192),
}
RAW_FLOAT = re.compile(r"\d\.\d{4,}")
ISO = re.compile(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d")
#: 13 §7.4's wall: a card names no price, upgrade, plan or billing destination.
WALL = re.compile(
    r"\$\d|\bper month\b|\bupgrade\b|\b(Starter|Trader|Pro|Premium)\b|pricing|billing|checkout",
    re.I,
)


def _browser() -> None:
    if not shutil.which("node") or not PLAYWRIGHT.exists():
        if os.environ.get("CI"):
            pytest.fail("node or keel-app's playwright is missing under CI (test_card_browser.py)")
        pytest.skip("no node or keel-app playwright here")


def _card_html(tmp: Path, js: str | None = None, css_extra: str = "") -> Path:
    from keel.widgets import build_card_html

    html = build_card_html("study")
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
    assert {"study", "run", "list", "chart", "refusal"} <= layouts, layouts
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
    for r in _renders(drawn, "study", "run"):
        summary = _envelope(_fixture(r["name"])).get("card_summary") or {}
        study = _envelope(_fixture(r["name"])).get("study") or {}
        kind = (summary.get("kind_label") or study.get("kind_label") or {}).get("short")
        assert r["kindChip"] == kind, (r["name"], r["kindChip"], kind)
        state = summary.get("state_words") or study.get("state_words")
        if state:
            assert state in r["headChips"], (r["name"], r["headChips"])


def test_every_tile_shows_its_served_words_and_no_raw_number(drawn) -> None:
    checked = 0
    for r in _renders(drawn, "study"):
        numbers = (_envelope(_fixture(r["name"])).get("card_summary") or {}).get("numbers") or []
        assert len(r["tiles"]) == min(4, len(numbers)), r["name"]  # D-332 (1): only what is served
        for tile, n in zip(r["tiles"], numbers, strict=False):
            assert tile["value"] == (n.get("value_words") or ""), (r["name"], tile, n)
            assert tile["sample"] == (n.get("sample_words") or ""), (r["name"], tile, n)
            assert tile["value"], (r["name"], "a served number with no words", n)
            checked += 1
    assert checked > 20
    for r in _renders(drawn):
        assert not RAW_FLOAT.search(r["text"]), (r["name"], RAW_FLOAT.findall(r["text"]))
        assert not ISO.search(r["text"]), (r["name"], ISO.findall(r["text"]))


def test_the_link_is_the_served_one_and_expand_only_where_offered(drawn) -> None:
    for r in _renders(drawn):
        env = _envelope(_fixture(r["name"]))
        links = [x for x in r["links"] if x["text"] != "Expand"]
        if r["layout"] == "refusal":
            want = ((env.get("detail") or {}).get("links") or {}).get("app")
        else:
            want = (env.get("links") or {}).get("app") or env.get("hero_url")
        if r["layout"] in (None, ""):
            continue  # a receipt row: its own Open, no link row
        assert [x["href"] for x in links] == ([want] if want else []), (r["name"], links, want)
        offers = r["dialect"] == "mcp"  # the MCP host lists fullscreen; the ChatGPT shim lists none
        assert r["expand"] == (offers and not r["noExpand"]), (r["name"], r["dialect"], r["expand"])


def test_every_inline_card_is_its_reserved_height_and_nothing_is_clipped(drawn) -> None:
    measured = 0
    for r in _renders(drawn, "study", "run", "list", "chart", "refusal"):
        key = (r["name"], r["dialect"], r["width"], r["theme"])
        want = RESERVED[(r["layout"], r["stripAttr"] if r["layout"] == "study" else None)][
            1 if r["width"] < 480 else 0
        ]
        assert r["reserved"] == want, (key, r["reserved"], want)
        if r["terminal"]:  # D-354 (4): a terminal run card is as tall as its content, not the frame
            assert r["layout"] == "run", key
            if r["name"] in ("run-failed", "run-stopped"):  # words only: well under the frame
                assert r["height"] < want - 100, (key, r["height"], want)
        else:
            assert abs(r["height"] - want) <= 1, (key, r["height"], want)
        assert r["clipped"] == [], (key, r["clipped"])
        assert r["overflowX"] <= 1, key
        if r["dialect"] == "mcp":
            assert set(r["sizesAfterFirstRender"]) == {r["height"]}, (key, r["sizes"])
        measured += 1
    assert measured > 100


#: Run cards that draw nothing but their header, held as a ratchet (reds on a new one). Empty since SERVE serves a
#: failed study's card_summary ("Didn't finish") and progress (the platform words); run-failed was blank before.
KNOWN_BLANK_RUNS: set[str] = set()


def test_every_run_card_draws_served_words_beyond_its_header(drawn) -> None:
    blank = set()
    for r in _renders(drawn, "run"):
        # Each header line is taken off once: a body line that repeats a chip's words still counts.
        body = Counter(ln for ln in r["text"].split("\n") if ln.strip())
        body -= Counter(["Keel", r["headName"], *r["headChips"]])
        if not [
            ln for ln in body.elements() if not ln.startswith("Open the study") and ln != "Expand"
        ]:
            blank.add(r["name"])
    assert blank == KNOWN_BLANK_RUNS, blank


# ── charts ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_key_chart_draws_its_served_positions_and_tick_words(drawn) -> None:
    charts = thumbs = 0
    for r in _renders(drawn, "study", "chart"):
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


def test_fullscreen_draws_a_tab_per_chart_and_your_checks(drawn) -> None:
    assert drawn["full"]
    for name, f in drawn["full"].items():
        data = _envelope(_fixture(name)).get("card_data") or {}
        want = (["key"] if data.get("key") else []) + [
            f"v{e['index']}" for e in data.get("charts") or [] if e.get("title")
        ]
        if not want:
            continue
        assert f["tabs"][: len(want)] == want, (name, f["tabs"], want)
        for p, tab in zip(f["panels"], f["tabs"], strict=True):
            entry = next((e for e in data.get("charts") or [] if f"v{e['index']}" == tab), None)
            kind = ((entry or {}).get("chart") or {}).get("kind")
            if (
                kind == "table"
            ):  # a served table: its header and up to 10 rows; no rows, no bare header
                rows = len((entry.get("chart") or {}).get("rows") or [])
                assert p["tableRows"] == (1 + min(10, rows) if rows else 0), (name, tab, p)
            elif tab == "key" or (entry and entry.get("form") == "chart"):
                assert p["svg"], (name, tab)
            if entry and entry.get("family") == "Checks":
                rows = (data.get("checks") or {}).get("rows") or []
                assert p["checks"] == min(40, len(rows)), (name, p)
                for row, words in zip(rows, p["checkWords"], strict=False):
                    assert row["label"] in words and (row["locked"] or row["value_words"] in words)


def test_an_older_card_for_the_same_study_becomes_its_receipt(drawn) -> None:
    s = drawn["supersede"]
    assert s, "the supersede pair was not drawn"
    assert s["older"]["rr"] and (
        "superseded" in s["older"]["rr"] or "shown below" in s["older"]["rr"]
    )
    assert s["newer"]["rr"] is None and s["newer"]["layout"] == "study"


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
    """13 §7.4 (WL-CU-14): the rendered text of every card, every layout and dialect, carries none of the wall's
    words. View titles are data, not copy: a listed policy word in one ("Trade stats") is reported, not failed."""
    titles = []
    for r in _renders(drawn):
        assert not WALL.search(r["text"]), (r["name"], WALL.findall(r["text"]))
        if re.search(r"\btrade", r["text"], re.I):
            titles.append(r["name"])
    if titles:
        print("view titles carrying a listed policy word (data, reported):", sorted(set(titles)))


def test_a_map_draws_its_axis_names_and_a_key_for_its_outlined_cells(drawn) -> None:
    """D-352 (3): the served axis names beside the axes, and one line naming what each outline means, in the served
    words, only for the outlines the chart carries."""
    seen = 0
    for r in _renders(drawn, "study", "chart"):
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


def test_your_checks_draws_its_rows_and_the_head_no_wordmark(drawn) -> None:
    """D-352 (1): a Checks tile with served rows draws them, never "Not run yet". (4): no "Keel" wordmark glued before
    the title; the maker is its served words, as a chip."""
    tiles = makers = 0
    for r in _renders(drawn, "study"):
        assert r["brand"] == 0, (r["name"], r["dialect"])
        env = _envelope(_fixture(r["name"]))
        maker = (env.get("card_summary") or {}).get("maker_words")
        if maker:
            assert maker in r["headChips"], (r["name"], r["headChips"])
            makers += 1
        data = env.get("card_data") or {}
        rows = (data.get("checks") or {}).get("rows") or []
        shown = [e for e in data.get("charts") or [] if e.get("title")][: 3 if r["narrow"] else 4]
        if rows and any(e.get("family") == "Checks" for e in shown):
            assert r["stripChecks"] == min(3, len(rows)), (r["name"], r["stripChecks"])
            assert (data.get("copy") or {}).get("not_run") not in r["text"], r["name"]
            tiles += 1
    assert tiles > 0 and makers > 0


def test_terminal_run_cards_take_their_content_height_and_live_ones_keep_the_frame(drawn) -> None:
    """D-354 (4): failed, stopped and done-with-failures render at their content height; queued and running keep the
    reserved frame they update in."""
    by = {}
    for r in _renders(drawn, "run"):
        state = (_envelope(_fixture(r["name"])).get("study") or {}).get("state")
        by.setdefault(state, set()).add(r["terminal"])
    assert (
        by.get("failed") == {True}
        and by.get("cancelled") == {True}
        and by.get("completed_partial") == {True}
    ), by
    assert by.get("running") == {False} and by.get("queued", {False}) == {False}, by
    live = [r for r in _renders(drawn, "run") if r["name"] in ("run-running", "run-queued")]
    assert live and not [r for r in live if r["terminal"]]


def test_a_chart_card_says_its_title_once_and_its_key_sentence_once(drawn) -> None:
    """D-354 (1): a single-chart card drops a chart title equal to the card title. (2): the served key sentence is
    not drawn beside a key line; where none is drawn it is its own line, whole."""
    checked = 0
    for r in _renders(drawn, "chart", "study"):
        if r["key"] != "chart":
            continue
        if r["layout"] == "chart":
            assert r["chartName"] != r["headName"], (r["name"], r["chartName"])
        data = _envelope(_fixture(r["name"])).get("card_data") or {}
        key = data.get("key") or {}
        note = key.get("note_words") if r["layout"] == "study" else None
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


def test_seed_a_number_without_words_is_caught(tmp_path) -> None:
    _browser()
    fx = _fixture("study-chk_v3-summary")
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert all(t["value"] for t in control["tiles"])
    seeded_fx = copy.deepcopy(fx)
    seeded_fx["structuredContent"]["card_summary"]["numbers"][0].pop("value_words")
    seeded = _one(tmp_path, _card_html(tmp_path), {"x": seeded_fx})[0]
    assert seeded["tiles"][0]["value"] == ""  # red for "every tile shows its served words"
    assert not RAW_FLOAT.search(seeded["text"])  # and still no raw float: the em dash, from the CSS


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
    assert control["layout"] == "list" and "Untitled" not in control["text"]
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


def test_seed_an_unclamped_lead_is_clipped(tmp_path) -> None:
    _browser()
    fx = copy.deepcopy(_fixture("study-chk_v3"))
    fx["structuredContent"]["card_summary"]["lead"] = " ".join(["a long served lead"] * 12)
    control = _one(tmp_path, _card_html(tmp_path), {"x": fx})[0]
    assert control["clipped"] == []
    (tmp_path / "seed").mkdir()
    unclamp = (
        ".st-lead{height:auto!important;-webkit-line-clamp:unset!important;display:block!important}"
    )
    seeded = _one(tmp_path / "seed", _card_html(tmp_path / "seed", css_extra=unclamp), {"x": fx})[0]
    assert seeded["clipped"], seeded  # red for "nothing is clipped within the reserved height"
