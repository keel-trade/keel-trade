"""The render-cadence card guards (BUILD §4.1–§4.4, §2.9; Q-1688 … Q-1700).

Everything this lane added is a rule about the RENDERED DOM and its REAL
LAYOUT — a receipt is ONE row and draws no action band until it is opened, a
comparison of eight runs does not draw eight inline columns, two frames from
one origin elect a winner, a declaration-only change prints its summary once.
None of that can be asserted on the served JS source, so the assertions below
read measurements taken by ``tests/fixtures/cards/c_cadence_check.mjs``, which
drives the real cards through the real ``ui/initialize`` → ``tool-result``
sequence in real Chromium.

**This file does not run in CI, and that is deliberate** — the python lane
executes inside the keel-runtime image, which has no node, so it skips there.
It is the pre-push proof for the rules that need a layout engine, exactly as
``test_widgets_strategy.py`` is for the strategy card's. Skips (never silently
passes) when node or Playwright is absent.

Proof each guard can fail: every test carries a ``# SEED:`` comment naming the
one-line edit that reds it. The seeds were run on 2026-09-22 and reverted by
reversing the edit; the ones that were exercised while building are recorded
in the commit message.

Proof they are not vacuous: every expected count below is derived from the
FIXTURE JSON (how many runs it carries, which one has no curve, how many
windows it spans, how many lines of source a preview carries) — quantities no
seed in a renderer can move — and ``test_the_cadence_scan_was_not_vacuous``
refuses a run that rendered nothing.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "c_cadence_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"

#: Where the captures go. Artifacts live OUTSIDE the repo
#: (.claude/rules/artifacts.md); the render run writes them there so a
#: reviewer looks at the same pixels the assertions were taken from.
CAPTURES = (
    HERE.parents[4] / "keel-artifacts" / "projects" / "fable" / "mcp-strategy-view" / "captures"
)

_ID_TOKEN_RE = re.compile(r"\b(?:str|btr|dep|cmt|shr|int)_[A-Za-z0-9]+\b")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _wire_bytes(name: str) -> int:
    """The envelope's size ON THE WIRE — the checked-in file is indented."""
    return len(json.dumps(_fixture(name), separators=(",", ":")).encode("utf-8"))


def _compare_card_html() -> str:
    """The comparison card's HTML, however the widget bundle exposes it.

    Lane O owns ``CARD_KINDS``/``CARD_TOOLS``; until ``compare`` lands there,
    the card is composed from the same five placeholders keel-app's own render
    test uses, so this lane's renderer is provable before its registration is.
    ``test_the_cadence_scan_was_not_vacuous`` reports which path was taken.
    """
    if "compare" in CARD_KINDS:
        return build_card_html("compare")
    from keel.widgets import _asset

    return (
        _asset("card.html.tmpl")
        .replace("{{KIND}}", "compare")
        .replace("{{TITLE}}", "Comparison")
        .replace("{{CSS}}", _asset("card.css"))
        .replace("{{ADAPTER_JS}}", _asset("host-adapter.js"))
        .replace("{{CARD_JS}}", _asset("card-compare.js"))
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the cadence rules are DOM rules")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("cadence-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    (cards / "compare.html").write_text(_compare_card_html(), encoding="utf-8")
    argv = [
        "node",
        str(CHECK),
        "--cards",
        str(cards),
        "--fixtures",
        str(FIXTURES),
    ]
    # Captures are written only where the external artifact store already
    # exists — a test never creates one, and nothing renderable ever lands
    # in the repo (.claude/rules/artifacts.md).
    if CAPTURES.parents[2].is_dir():
        argv += ["--shots", str(CAPTURES)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=900, cwd=HERE.parents[4])
    if not proc.stdout.strip():
        pytest.fail(f"the cadence check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


# ── Non-vacuity ─────────────────────────────────────────────────────────────


def test_the_cadence_scan_was_not_vacuous(rendered: dict) -> None:
    """Every arm rendered something, and the fixtures really are the cases
    the rules were written for — counted from the fixture JSON, so no seed in
    a renderer can move them."""
    arms = [k for k, v in rendered.items() if isinstance(v, dict) and "height" in v]
    assert len(arms) >= 30, f"only {len(arms)} arms rendered: {sorted(arms)}"
    for name in arms:
        assert rendered[name]["height"] > 0, f"{name} rendered nothing"

    # The 8-run fixture is the extreme case: eight runs, exactly one with no
    # stored curve, and two distinct windows.
    eight = _fixture("compare_8.envelope.json")["view"]["runs"]
    assert len(eight) == 8
    assert sum(1 for r in eight if not r.get("curve")) == 1
    assert len({(r["metrics"]["fills"]) for r in eight}) == 2, (
        "the 8-run fixture no longer spans two windows"
    )

    # The completed receipt really has a curve to hide while closed.
    curve = _fixture("c_bt_receipt_completed.envelope.json")["curve"]
    assert len(curve["points"]) >= 100

    # The dry runs really carry what their card must NOT show (Q-1848): the
    # clean preview its source, the parse error its parser message.
    src = _fixture("strategy_preview.envelope.json")["view"]["source"]
    assert src and src["lines"] >= 10 and "Pipeline(" in src["text"]
    pe = _fixture("strategy_parse_error.envelope.json")
    assert pe["dry_run"] is True and pe["view"]["parse_error"] is True
    assert "Parse error at line 3" in pe["view"]["markdown"]

    # And the error fixture really is an error envelope with no view.
    err = _fixture("c_bt_error_quota.envelope.json")
    assert err["code"] and err["message"] and "view" not in err


# ── B.1 · the backtest receipt ──────────────────────────────────────────────


def test_backtest_receipt_is_one_row_and_opens_in_place(rendered: dict) -> None:
    """BUILD §4.1: one row, no action band while closed, the evidence layout
    one tap away in place, and the host told about the new height."""
    # SEED: in host-adapter.js `renderLinkRow`, change
    # `getAttribute("data-size") === "receipt"` to `=== "never"` — the
    # bordered band draws while closed and `buttonLinks` is no longer empty
    # (it also pushes `height` past 64).
    m = rendered["receipt"]
    assert 0 < m["height"] < 64, f"the closed receipt is {m['height']}px tall"
    assert m["rowHeight"] >= 36, "the row is not a real target"
    assert m["buttonLinks"] == [], "a closed receipt drew an action band"
    assert m["svgs"] == 0, "a closed receipt drew the chart"
    assert m["dataSize"] == "receipt"
    assert m["disclosureExpanded"] == "false"
    assert m["disclosureHeight"] >= 36
    assert m["disclosureLabel"].startswith("Show result")
    assert m["rrNums"] == ["0.67", "+60.5%", "−43.4%"]
    assert m["rrChips"] == ["v3", "completed"]
    assert m["rrOpenAnchor"] and m["rrOpenAnchor"][0]["href"].startswith("https://")
    assert m["rrOpenAnchor"][0]["title"] == m["rrOpenAnchor"][0]["href"]

    after = m["after"]
    assert after["disclosureExpanded"] == "true"
    assert after["dataSize"] == "receipt-open"
    assert after["svgs"] == 1, "opening the receipt did not draw the curve"
    assert after["tiles"] == 4
    assert len(after["buttonLinks"]) == 2, after["buttonLinks"]
    # `size-changed` twice: at least once when the row mounted, and again
    # when it opened. The MOUNT count alone is not pinned — the adapter
    # coalesces through a ResizeObserver, so one or two there is timing
    # rather than contract (pinning `>= 2` at mount flaked once in a full
    # module run and passed in isolation, 2026-09-22). What IS contract is
    # that the host hears about the new height, and that the total is two.
    assert m["sentSizes"] >= 1
    assert after["sentSizes"] > m["sentSizes"], "the host was never told it grew"
    assert after["sentSizes"] >= 2

    # Under 480 px the row wraps rather than clipping; the design's own
    # measured narrow receipt is 82 px and ours adds the 44 pt tap target.
    narrow = rendered["receiptNarrow"]
    assert 0 < narrow["height"] <= 100, narrow["height"]
    assert narrow["buttonLinks"] == []
    assert narrow["overflowX"] == 0


def test_receipt_states_say_only_what_the_run_knows(rendered: dict) -> None:
    """queued/running carry the window and no numbers; failed carries its
    first line and opens to the whole message; cancelled carries nothing
    else (BUILD §4.1)."""
    # SEED: in card-backtest.js `receiptSpec`, drop the
    # `status === "queued" || status === "running"` branch — the window
    # disappears from both snapshot rows.
    for name in ("receiptQueued", "receiptRunning"):
        m = rendered[name]
        assert m["rrNums"] == [], f"{name} printed numbers it cannot have"
        assert m["rrWindow"], f"{name} lost its window"
        assert m["disclosureLabel"] is None, f"{name} offered a disclosure"
        assert m["buttonLinks"] == []
    assert "queued" in rendered["receiptQueued"]["rrChips"]
    assert "running" in rendered["receiptRunning"]["rrChips"]

    failed = rendered["receiptFailed"]
    full = _fixture("c_bt_receipt_failed.envelope.json")["view"]["error"]
    assert "\n" in full, "the failed fixture has nothing to disclose"
    assert "failed" in failed["rrChips"]
    assert failed["rrErr"] == full.split("\n")[0]
    assert failed["disclosureLabel"].startswith("Show error")
    opened = failed["after"]
    assert any(full.split("\n")[1][:40] in n for n in opened["notes"]), opened["notes"]
    assert opened["tiles"] == 0, "a failed run drew tiles"
    assert opened["svgs"] == 0, "a failed run drew a chart"

    cancelled = rendered["receiptCancelled"]
    assert "cancelled" in cancelled["rrChips"]
    assert cancelled["rrNums"] == []
    assert cancelled["rrWindow"] == ""
    assert cancelled["disclosureLabel"] is None


def test_a_sub_window_receipt_names_its_window(rendered: dict) -> None:
    """Q-1880 (R4): two half-period receipts of one version read
    "v1 completed 1.33 +35.0% −16.4%" and "2.74 +67.6% −11.7%" — nothing
    said which half. A completed receipt now carries its window when the run
    chose one (a named start the platform did not move, or an end well
    before the run completed); the default window stays unstamped.

    # SEED: in card-backtest.js `isSubWindow`, `return false;` as its first
    # line — the test reds at its first sub-window arm ("does not say which
    # window it covers"); the full-window arm needs no window and passes it.
    """
    for name in ("receiptSubWindow", "receiptFirstHalf"):
        m = rendered[name]
        assert m["rrNums"], f"{name} lost its numbers"  # non-vacuity: a completed row
        assert m["rrWindow"], f"{name} does not say which window it covers"
    assert "2025" in rendered["receiptSubWindow"]["rrWindow"]
    full = rendered["receiptFullWindow"]
    assert full["rrNums"] and full["rrWindow"] == ""


def test_a_window_the_server_could_not_date_draws_no_stamp(rendered: dict) -> None:
    """Q-1719 (the shape Q-1709 newly made reachable).

    `view.window` is a DATE pair, and since `4068c5ede` `window_block` is the
    one owner that builds it: a value that is not a date becomes `None` rather
    than being passed through for a renderer to trip over. That makes
    `{start: null, end: null}` a shape the card WILL be handed and that NO
    fixture in the corpus carried — so nothing proved what it draws.

    It must draw no window and no `null`. The rest of the row is unaffected,
    which is what distinguishes "the window is absent" from "the row broke".

    SEED: in card-backtest.js `windowText`, `return String(w.start) + " – " +
    String(w.end)` — this reds on `null` in the drawn text.
    """
    m = rendered["receiptWindowless"]
    assert 0 < m["height"] < 64
    assert m["rrWindow"] == "", f"a dateless window drew {m['rrWindow']!r}"
    for junk in ("null", "undefined", "NaN", "Invalid Date"):
        assert junk not in m["text"], f"{junk!r} in {m['text']!r}"
    # The row is otherwise intact — this is an absent window, not a broken
    # card: the state chip is still there and nothing overflowed.
    assert "queued" in m["rrChips"], m["rrChips"]
    assert m["overflowX"] == 0

    # Not vacuous: the SAME arm with real dates draws a window, so the empty
    # string above is the null handling rather than the card never drawing
    # one. Read from the fixture, which no renderer seed can move.
    assert rendered["receiptQueued"]["rrWindow"]
    window = _fixture("c_bt_receipt_completed.envelope.json")["view"]["window"]
    assert window["start"] and window["end"]


def test_error_envelope_renders_the_error_line_not_the_empty_card(
    rendered: dict,
) -> None:
    """A refusal now MOUNTS the card (run/watch/compare are card tools), and
    it must say what happened — never Untitled over four em dashes."""
    # SEED: in card-backtest.js `errorOf`, `return null` unconditionally —
    # the card falls through to the evidence layout and draws empty tiles.
    for name in ("receiptError", "lookupError", "neverSubmitted", "compareError"):
        m = rendered[name]
        assert m["errLine"], f"{name} drew no error line"
        assert m["tiles"] == 0
        assert "Untitled" not in m["text"]
        assert "—\n—" not in m["text"], "the empty-card state rendered"
        assert 0 < m["height"] < 64
        assert m["buttonLinks"] == [], "an error offered an action it cannot do"
    # The comparison card's lead IS honest for every arm it can receive:
    # it is the comparison card, and `keel_backtest_compare` is the only
    # tool that mounts it, so "unavailable" claims nothing about a
    # submission. It stays.
    assert rendered["compareError"]["errLine"].startswith("Comparison unavailable")


def test_the_backtest_error_lead_names_what_actually_happened(
    rendered: dict,
) -> None:
    """Q-1713: `Backtest not submitted` belongs to the arm where nothing
    was submitted, and to no other.

    `card-backtest.js` is declared by `keel_backtest_run` AND by
    `keel_backtest_summarize` / `keel_backtest_watch` at the tool level,
    and a §13.5 envelope carries no tool name. Staging's summarize on a
    missing id therefore rendered "Backtest not submitted — Backtest …
    not found." — a claim about platform state that was not true, and one
    that sends a reader looking for a run they believe failed to start.

    The two arms below differ in exactly one thing (the envelope shape),
    which is what makes the verdict about the SHAPE rather than about the
    words happening to be present.

    SEED: in `errorOf`, give the §13.5 arm
    `lead: "Backtest not submitted — "` — `lookupError` and
    `receiptError` red, `neverSubmitted` stays green.
    SEED: give the view-error arm `lead: null` — only `neverSubmitted`
    reds, and it names the missing lead.
    """
    expected = rendered["neverSubmitted"]["expected"]
    lead = expected["lead"]

    # A REFUSAL renders the refusal's own words and no invented lead.
    for name, message in (
        ("lookupError", expected["lookupMessage"]),
        ("receiptError", expected["quotaMessage"]),
    ):
        line = rendered[name]["errLine"]
        assert line == message, f"{name}: {line!r}"
        assert "not submitted" not in line.lower(), (
            f"{name} claims a submission that never happened: {line!r}"
        )

    # A run that genuinely never started keeps §4.1's line.
    never = rendered["neverSubmitted"]["errLine"]
    assert never == lead + expected["error"], never

    # Not vacuous, and nothing a renderer seed can move: the three
    # envelopes really are the three shapes this rule is about — two
    # §13.5 refusals with no view, and a view with an error and no
    # status.
    quota = _fixture("c_bt_error_quota.envelope.json")
    assert quota["code"] and quota["message"] and "view" not in quota
    assert expected["quotaMessage"] == quota["message"]
    assert expected["lookupMessage"].startswith("Backtest")
    assert expected["error"] and lead.startswith("Backtest not submitted")


def test_receipt_and_compare_envelopes_stay_inside_the_host_budget() -> None:
    """Q-1689: a completed run envelope ≤ 20 KB, an 8-run comparison ≤ 60 KB
    (BUILD §2.1, §2.2) — measured on the WIRE form, not the indented file.

    **These fixtures bound what the CARD is rendered from, not what the tool
    emits** (Q-1708). `compare_8.envelope.json` carries only `view` and
    `hero_url`, and its curve points are `["2024-08-15", 10000, 0]` — 24 B
    against staging's 45 — so it read 29 KB while a real 8-run result was
    75 KB. The envelope budget is now guarded where it can fail, against an
    envelope the real handler builds:
    `test_outcomes_backtest_compare.test_an_eight_run_compare_envelope_stays_inside_the_host_budget`.
    """
    # SEED: raise the compare fixture's curve to 240 points in
    # make_cadence_fixtures.mjs and regenerate — the 8-run fixture doubles.
    run_bytes = _wire_bytes("c_bt_receipt_completed.envelope.json")
    eight_bytes = _wire_bytes("compare_8.envelope.json")
    assert run_bytes <= 20_000, f"a completed run envelope is {run_bytes} bytes"
    assert eight_bytes <= 60_000, f"an 8-run comparison fixture is {eight_bytes} bytes"
    # Not vacuous: both envelopes actually carry curves, so the budget is
    # bounding something.
    assert len(_fixture("c_bt_receipt_completed.envelope.json")["curve"]["points"]) > 50
    carried = [r for r in _fixture("compare_8.envelope.json")["view"]["runs"] if r.get("curve")]
    assert len(carried) == 7
    assert all(len(r["curve"]["points"]) == 120 for r in carried)


# ── B.2 · the strategy receipt ──────────────────────────────────────────────


def test_strategy_receipt_opens_to_the_structure(rendered: dict) -> None:
    """BUILD §4.2: a save's receipt opens to its structure (`Show pipeline`)."""
    # SEED: in card-strategy.js `renderReceipt`, drop `spec.disclosure` — the
    # label is None and `after` never opens.
    saved = rendered["strategyReceipt"]
    assert 0 < saved["height"] < 64
    assert saved["disclosureLabel"].startswith("Show pipeline")
    assert saved["rrChips"] == ["v3", "valid"]
    # The one change it shows: where it is, and what it was before.
    changed = _fixture("strategy_receipt.envelope.json")["view"]["change"]["changed"][0]
    key, pair = next(iter(changed["params"].items()))
    assert saved["rrHunkKey"].endswith(key)
    assert saved["rrHunkOld"] == str(pair["old"])
    assert saved["rrHunkNew"] == str(pair["new"])
    assert saved["svOpenLinks"] == 0
    opened = saved["after"]
    assert opened["structures"] == 1 and opened["blocks"] == 5
    assert len(opened["buttonLinks"]) == 2


#: What a dry run's card must never show the user (Q-1848).
_INTERNALS = re.compile(
    r"Parse error|\bline \d|\bcol \d|Available:|[A-Z_]{6,}|\berrors?\b|\bwarnings?\b"
    r"|Globals\(|Show (source|preview|pipeline)|ask to save it|Open ↗"
)


def test_a_dry_run_is_one_quiet_draft_check_row(rendered: dict) -> None:
    """Q-1848: a dry run is the agent checking its draft. In real Chromium, in
    both dialects: one row (no disclosure, no action band, a real reported
    height), `Draft check · revising|ready`, muted, and none of the parser's
    or validator's words, rule codes, parameter lists or the source."""
    # SEED: in card-strategy.js `render`, drop `&& !view.preview` from the
    # `view.error` line — the parse-error arms grow past one row and their
    # text carries "Parse error at line 3". SEED: route `view.preview` to
    # `renderReceipt` — `Show pipeline` and `1 error` return.
    arms = {
        "preview": "ready",
        "previewNarrow": "ready",
        "draftParseError": "revising",
        "draftParseErrorOpenAI": "revising",
    }
    for arm, state in arms.items():
        m = rendered[arm]
        assert 0 < m["height"] < 64, (arm, m["height"])
        assert m["rowHeight"] and m["rowHeight"] >= 20, arm
        assert m["dataSize"] == "receipt", arm
        assert m["disclosureLabel"] is None, (arm, m["disclosureLabel"])
        assert m["buttonLinks"] == [] and m["linkRowHidden"], arm
        assert m["rrOpenAnchor"] == [], arm
        assert m["rrChips"] == ["Draft check", state], (arm, m["rrChips"])
        assert m["structures"] == 0 and m["blocks"] == 0, arm
        leak = _INTERNALS.search(m["text"])
        assert leak is None, (arm, leak.group(0) if leak else None, m["text"])
    assert rendered["draftParseError"]["rrName"] == "BTC beater"
    # Both dialects really ran: the ChatGPT arm reported through
    # notifyIntrinsicHeight (the harness relays it as a size post).
    assert rendered["draftParseErrorOpenAI"]["sentSizes"] > 0
    assert rendered["draftParseError"]["sentSizes"] > 0


# ── B.3 · one action row ────────────────────────────────────────────────────


def test_every_card_draws_its_actions_through_the_adapter_row(
    rendered: dict,
) -> None:
    """F-2 / BUILD §4.3: no card draws its own `.sv-link` action row, and
    every card's actions are one or two `.button-link`s."""
    # SEED: in card-strategy.js `footer`, re-append the
    # `el("a", "sv-link sv-open-link", "Open in Keel ↗")` block — the
    # strategy arm's `svOpenLinks` becomes 1.
    kinds = dict(rendered["actionRows"])
    kinds["compare"] = rendered["compare4"]
    assert len(kinds) == 5, sorted(kinds)
    for kind, m in kinds.items():
        assert m["svOpenLinks"] == 0, f"{kind} drew its own open link"
        assert m["svExpand"] == 0, f"{kind} drew its own Expand"
        assert 1 <= len(m["buttonLinks"]) <= 2, f"{kind}: {m['buttonLinks']}"
        assert m["buttonLinks"][-1] == "Expand", f"{kind}: {m['buttonLinks']}"
    # Not vacuous: every kind actually offered its own destination label.
    labels = {kind: m["buttonLinks"][0] for kind, m in kinds.items()}
    assert len(set(labels.values())) >= 4, labels


# ── B.4 · the comparison card ───────────────────────────────────────────────


def test_compare_card_renders_two_four_and_eight(rendered: dict) -> None:
    """BUILD §4.4: ≤ 4 runs is a table with one column per run; > 4 is one
    line per run; under 480 px it is blocks capped at four. Every collapse
    names its remainder."""
    # SEED: in card-compare.js set `INLINE_RUNS = 8` — the 8-run arm draws
    # eight inline columns and `rowItems` collapses to 0.
    two, four, eight = (
        rendered["compare2"],
        rendered["compare4"],
        rendered["compare8"],
    )
    assert two["cmpCols"] == 2 and four["cmpCols"] == 4
    # Q-1781: the app's first four, in its order; `+3 more` reveals the next.
    assert two["cmpRowKeys"] == ["Return", "Max drawdown", "Sharpe", "Win rate"]
    assert four["moreButtons"] == ["+3 more"]
    # A pre-Q-1787 envelope (no full `view.rows`) draws the legacy list —
    # the server's own order and words, never a second set of labels.
    # The count row is served-only (Q-1906): the legacy list carries none.
    assert four["after"]["cmpRowKeys"][-3:] == ["Turnover (× capital)", "Sortino", "Calmar"]
    assert four["after"]["moreButtons"] == []

    n_runs = len(_fixture("compare_8.envelope.json")["view"]["runs"])
    assert eight["tables"] == 0, "eight runs drew an inline table"
    assert eight["rowItems"] == n_runs
    assert any("more columns in fullscreen" in n for n in eight["notes"])

    narrow = rendered["compare8Narrow"]
    assert narrow["rowItems"] == 4, "the narrow cap is not four blocks"
    assert f"+{n_runs - 4} more in fullscreen" in narrow["notes"]
    assert rendered["compare4Narrow"]["rowItems"] == 4

    # The chart: at most four lines inline, the rest named, and the one run
    # with no stored curve named under it.
    assert eight["chartPaths"] == 4
    assert eight["legendRest"] == "+3 in fullscreen"
    assert "no curve for v7" in eight["notes"]
    assert four["legend"] == [
        r["label"] for r in _fixture("compare_4.envelope.json")["view"]["runs"]
    ]

    # Warnings capped at two inline, remainder named.
    warns = _fixture("compare_8.envelope.json")["view"]["warnings"]
    assert len(warns) == 3
    assert warns[0] in eight["notes"] and warns[1] in eight["notes"]
    assert "+1 more" in eight["notes"]

    # No run id anywhere a person reads, on any arm.
    for name in ("compare2", "compare4", "compare8", "compareFull", "compareCross"):
        hits = _ID_TOKEN_RE.findall(rendered[name]["text"])
        assert not hits, f"{name}: ids in visible text: {hits}"

    # The two-id fixture is the founder's v1 → v2, which moved TWO keys, so
    # its labels are bare — the four-run one moved one, so they are not.
    assert two["legend"] == ["v1", "v2"]
    assert all(" · " in lbl for lbl in four["legend"])


def test_compare_series_palette_is_the_decided_order(rendered: dict) -> None:
    """The ink for the baseline (Q-1782: the accent read violet, putting
    violet first), then cyan · amber · green · rose · violet; beyond six
    the sequence repeats DASHED (review page, decided)."""
    # SEED: in card-compare.js move `{ stroke: "var(--sv-violet)" }` to
    # index 2 of SERIES — the four-run arm draws violet where amber belongs.
    # Paths are drawn back to front so the baseline sits on top.
    drawn = list(reversed(rendered["compare4"]["chartStrokes"]))
    assert drawn == [
        "var(--fg)",
        "var(--sv-cyan)",
        "var(--sv-amber)",
        "var(--sv-green)",
    ], drawn
    assert rendered["compare4"]["chartDashed"] == 0
    # Beyond six, dashed. The 8-run fullscreen arm draws seven curves (v7
    # has none), so exactly the eighth run's line is dashed.
    full8 = rendered["compare8Full"]
    assert full8["chartPaths"] == 7
    assert full8["chartDashed"] == 1, "the seventh colour is not dashed"
    assert full8["ddPaths"] == 7, "fullscreen drew no overlaid drawdown pane"
    # Not vacuous: the inline arms drew four DISTINCT strokes.
    assert len(set(rendered["compare4"]["chartStrokes"])) == 4


def test_compare_fullscreen_carries_the_deltas_and_the_per_run_links(
    rendered: dict,
) -> None:
    """Deltas are in the envelope for the model and on screen only in
    fullscreen, where there is room (BUILD §4.4)."""
    # SEED: in card-compare.js `rowFor`, drop the `full &&` from the delta
    # condition — inline tables grow a sub-line under every cell.
    assert rendered["compare4"]["cmpDeltas"] == [], "deltas were drawn inline"
    full = rendered["compareFull"]
    view = _fixture("compare_4.envelope.json")["view"]
    n_runs = len(view["runs"])
    assert full["cmpRowKeys"][-2:] == ["Tearsheet", "Differs from baseline"]
    # The fullscreen arm renders the SERVED row list (c_cadence_check.mjs
    # `SERVED_ROWS`), pinned here to the server's own: the count row draws
    # under its served label "Trades" (Q-1906), never a static one.
    from keel.tools.outcomes._backtest_view import _CARD_ROWS

    assert full["cmpRowKeys"][:-2] == [label for label, _ in _CARD_ROWS]
    assert "Trades" in full["cmpRowKeys"]
    metric_rows = len(full["cmpRowKeys"]) - 2
    assert len(full["cmpDeltas"]) == metric_rows * (n_runs - 1)
    assert any(d.startswith("±") for d in full["cmpDeltas"]), (
        "a delta that rounds to zero must render ± rather than a signed zero"
    )
    assert len(full["cmpAnchors"]) == n_runs
    assert all(a.startswith("https://") for a in full["cmpAnchors"])
    assert "Expand" not in full["buttonLinks"], "fullscreen offered Expand"


def test_compare_across_strategies_offers_no_single_destination(
    rendered: dict,
) -> None:
    """D-n / Q-1686: the strategy editor when all runs share a strategy, and
    NOTHING when they span strategies — never an invented link, and never an
    invitation to ask for one that cannot exist."""
    # SEED: in card-compare.js set `data-no-hero` to "0" unconditionally —
    # the cross arm grows "Ask for the Keel link to view this in the app."
    cross = rendered["compareCross"]
    env = _fixture("compare_cross.envelope.json")
    assert env["hero_url"] is None, "the fixture no longer spans strategies"
    assert cross["buttonLinks"] == ["Expand"], cross["buttonLinks"]
    assert cross["mutedRow"] == "", cross["mutedRow"]
    assert cross["noHero"] == "1"
    labels = [r["label"] for r in env["view"]["runs"]]
    assert cross["legend"] == labels
    assert not any(re.fullmatch(r"v\d+", lbl) for lbl in labels)
    assert any("Different strategies" in n for n in cross["notes"])
    # The control: a comparison that DOES share a strategy keeps its link.
    assert rendered["compare4"]["buttonLinks"][0] == "Open strategy in Keel ↗"
    assert rendered["compare4"]["noHero"] == "0"


def test_the_wordmark_is_absent_on_the_openai_dialect(rendered: dict) -> None:
    """REVIEW §4.4: ChatGPT prints the app's name above every widget, so a
    wordmark inside the card is the name twice. Every other host gets it."""
    # SEED: in host-adapter.js make `wordmark()` `return true;` — all three
    # openai arms flip to True.
    w = rendered["wordmark"]
    assert w["mcpReceipt"] is True
    assert w["mcpEvidence"] is True
    assert w["mcpCompare"] is True
    assert w["openaiReceipt"] is False
    assert w["openaiEvidence"] is False
    assert w["openaiCompare"] is False
    # Not vacuous: three DIFFERENT surfaces were measured in both dialects.
    assert len(w) == 6


# ── Per-kind skeletons ──────────────────────────────────────────────────────


def test_the_skeleton_is_the_shape_its_kind_resolves_into(rendered: dict) -> None:
    """BUILD §4.1: the comparison card resolves into a table over a chart,
    so its skeleton is a table over a chart — not the four tiles every
    other kind promises. A skeleton is a PROMISE about the shape that is
    coming; promising the wrong shape is worse than promising none."""
    # SEED: in card.css drop the
    # `body[data-card="compare"] .skeleton .sk-table { display: block }`
    # rule — the comparison skeleton falls back to a bare body block and
    # the assertion below stops seeing `sk-table`.
    compare = rendered["skeletonCompare"]["skeletonShapes"]
    assert any("sk-table" in c for c in compare), compare
    assert not any("sk-tiles" in c for c in compare), compare
    # Q-1769: a backtest or strategy card can resolve into a ONE-ROW
    # receipt, so before its call's arguments arrive it waits as that row —
    # promising the full card and then collapsing was the defect. The full
    # tiles skeleton for a call that WILL be a view is proved in
    # test_widgets_inflight.py, where the arms send real `tool-input`.
    for kind in ("skeletonBacktest", "skeletonStrategy"):
        shapes = rendered[kind]["skeletonShapes"]
        assert shapes == ["sk-line sk-row"], (kind, shapes)
        assert rendered[kind]["height"] < 64, kind
    # Not vacuous: all three arms really were measured before any result
    # arrived, so a skeleton actually existed to read.
    for kind in ("skeletonCompare", "skeletonBacktest", "skeletonStrategy"):
        assert rendered[kind]["skeletonShapes"], kind
    assert rendered["skeletonCompare"]["height"] > 100
    # And exactly one variant set shows per kind — never two at once.
    assert len(set(compare)) == len(compare)


# ── B.5 · supersession ──────────────────────────────────────────────────────


def test_a_younger_sibling_supersedes_the_older_card(rendered: dict) -> None:
    """BUILD §2.9: two instances of one connector share a sandbox origin, so
    a `keel-cards` BroadcastChannel elects the youngest view of one object.
    The older card re-renders as its receipt with a chip — it never blanks."""
    # SEED: in host-adapter.js `isYounger`, flip the `at` comparison —
    # `return ta > tb` to `return ta < tb` — and the YOUNGER card marks
    # itself superseded while the older one does not. (Flipping the `seq`
    # tie-break instead does NOT red this: the two fixtures differ in `at`,
    # so the tie-break is never reached. Measured 2026-09-22 — a seed that
    # passes is a seed aimed at the wrong line, not a guard that works.)
    m = rendered["supersession"]
    assert m["origin"].startswith("http://127.0.0.1:"), (
        "the arm must run on a real origin; BroadcastChannel is inert on an "
        "opaque one, which would make this pass by never firing"
    )
    assert m["channelAvailable"] is True
    older, younger = m["a"], m["b"]
    assert "superseded by v5" in older["rrChips"], older["rrChips"]
    assert older["dataSize"] == "receipt", "the superseded card is not a receipt"
    assert older["rrOpenAnchor"], "the superseded card lost its link"
    assert older["disclosureLabel"], "the superseded card lost its in-place open"
    assert not any("superseded" in c for c in younger["rrChips"]), younger["rrChips"]

    # Control: the same two keys on DIFFERENT objects supersede nothing —
    # two strategies side by side both stay as they were.
    c = rendered["supersessionControl"]
    a_obj = _fixture("c_bt_superseded_older.envelope.json")["view"]["object"]
    b_obj = _fixture("c_bt_superseded_other_object.envelope.json")["view"]["object"]
    assert a_obj != b_obj, "the control arm no longer differs in object"
    for side in ("a", "b"):
        assert not any("superseded" in chip for chip in c[side]["rrChips"]), (
            f"control {side}: {c[side]['rrChips']}"
        )
    # Not vacuous: both frames really mounted and drew the card.
    for arm in (m, c):
        for side in ("a", "b"):
            assert arm[side]["height"] > 0
            assert arm[side]["rrName"]


# ── Q-1717 · an unsaved preview has no name to be missing ───────────────────


def test_a_saved_receipt_with_no_name_says_untitled(rendered: dict) -> None:
    """Q-1630: a SAVED strategy with no name is a title that genuinely IS
    missing, so its receipt says `Untitled` — never an id.

    (Q-1717's preview arm was retired at the 2026-09-23 integration: a dry
    run no longer renders through `renderReceipt` — it is the Q-1848
    draft-check row — and the server now names it from the source.)

    SEED: in host-adapter.js `receiptRow`, drop the `|| "Untitled"`
    fallback — the arm reds with an empty name.
    """
    unnamed = rendered["receiptUnnamed"]
    assert unnamed["rrName"] == "Untitled", unnamed["rrName"]
    # Not vacuous: the named control draws its own name, so the arm
    # differs from it by the name alone.
    named = rendered["strategyReceipt"]["rrName"]
    assert named and named != "Untitled", named


# ── Q-1700 · the change card's summary has ONE owner ────────────────────────


def test_a_declaration_only_change_prints_its_summary_once(rendered: dict) -> None:
    """Q-1700: a change that touches no block had its summary printed twice —
    once by `renderChange`, once by `hunksFlow`'s empty state.

    Q-1707 note: the shipped "fix" ADDED a second copy of the empty-state
    line instead of deleting it, so the defect this test describes was
    still live when the test was written — it passed because
    `renderChange` stopped calling `hunksFlow` with no hunks, which made
    BOTH copies unreachable. Both lines are now gone.
    """
    # SEED: in card-strategy.js `renderChange`, add
    # `card.appendChild(el("p", "sv-none", view.chg.summary));` above the
    # `card.appendChild(full);` in the no-hunk branch — the count below
    # becomes 2, which IS the shipped defect.
    chg = _fixture("strategy_declaration_change.envelope.json")["view"]["change"]
    assert not chg["added"] and not chg["removed"] and not chg["changed"], (
        "the fixture is no longer a declaration-only change"
    )
    summary = chg["summary_text"]
    assert summary
    m = rendered["declarationChange"]
    # Q-1777: the sentence is now printed ZERO times — a declaration is
    # drawn as its split chip (the next test), and the server's
    # summary_text beside it printed the same change again, as a raw list
    # when a universe resolved. Never twice is still the rule.
    assert m["text"].count(summary) == 0, m["svSummaries"]
    # And the card still shows the pipeline the declaration moved under,
    # rather than an empty hunk box.
    assert m["blocks"] == 5, m["blocks"]
    assert "Show full pipeline" not in m["svLinks"], "there are no changes to swap away from"


# ── Q-1707 · the card draws the declaration that moved ──────────────────────


def test_the_change_card_draws_every_declaration_that_moved(rendered: dict) -> None:
    """Q-1707: a `Globals` / `Universe` / `Execution` edit was invisible —
    the card fell back to a structure with nothing marked. Each declaration
    now draws as its own hunk, `Execution` over `buffer  0.1 → 0.05`."""
    # SEED: in card-strategy.js `renderChange`, delete the two lines
    # `var decl = (view.chg && view.chg.decl) || [];` /
    # `if (decl.length) card.appendChild(declFlow(decl));` — `svDecls`
    # goes empty and every assertion below reds, which IS the shipped
    # defect.
    wire = _fixture("strategy_declaration_change.envelope.json")["view"]["change"]
    # Not vacuous, and keyed on the FIXTURE rather than the render: the
    # wire really carries two declaration keys and no block hunk, so an
    # empty `svDecls` can only mean the card dropped them.
    keys = wire["declarations"]["execution"]
    assert len(keys) == 2, sorted(keys)
    assert not (wire["added"] or wire["removed"] or wire["changed"])

    m = rendered["declarationChange"]
    assert len(m["svDecls"]) == len(keys), m["svDecls"]
    # `buffer_threshold` is said as `buffer` — the word the approved
    # design draws (review §12) — and the old value stays beside the new.
    assert m["svDecls"][0] == {"key": "buffer", "old": "0.1", "now": "0.05"}
    assert m["svDecls"][1] == {"key": "method", "old": "to_center", "now": "to_edge"}
    # Each hunk says which declaration block it belongs to.
    assert m["svDeclCtx"] == ["Execution", "Execution"], m["svDeclCtx"]


def test_one_declaration_edit_is_a_receipt_row(rendered: dict) -> None:
    """A lone declaration edit is the same SIZE of event as a lone param
    edit, so it renders as the one-row receipt the design shows:
    `Execution · buffer  0.1 → 0.05`."""
    # SEED: in card-strategy.js `receiptHunk`, restore the original
    # `if (!hunks.length) return null;` in place of the declaration
    # fallback — the row falls back to the summary note and `rrHunkKey`
    # goes empty.
    wire = _fixture("strategy_declaration_receipt.envelope.json")["view"]
    # Not vacuous: the fixture is a receipt carrying exactly ONE
    # declaration key and no block hunk — quantities no renderer edit
    # can move.
    assert wire["size"] == "receipt"
    assert list(wire["change"]["declarations"]["execution"]) == ["buffer_threshold"]
    assert not (wire["change"]["added"] or wire["change"]["removed"] or wire["change"]["changed"])

    m = rendered["declarationReceipt"]
    assert m["dataSize"] == "receipt", m["dataSize"]
    assert m["rrHunkKey"] == "Execution · buffer", m["rrHunkKey"]
    assert m["rrHunkOld"] == "0.1", m["rrHunkOld"]
    assert m["rrHunkNew"] == "0.05", m["rrHunkNew"]


# ── Layout ──────────────────────────────────────────────────────────────────


def test_nothing_overflows_horizontally_or_scrolls_inside_the_frame(
    rendered: dict,
) -> None:
    """The card system's standing rule, over every new piece at 720 and 390."""
    # SEED: in card.css drop `text-overflow: ellipsis` and `min-width: 48px`
    # from `.rr .rr-name` — the 390 px receipt arms overflow.
    arms = [k for k, v in rendered.items() if isinstance(v, dict) and "overflowX" in v]
    assert len(arms) >= 30, f"only {len(arms)} arms measured"
    bad = [k for k in arms if rendered[k]["overflowX"] != 0]
    assert not bad, f"horizontal overflow on {bad}"
    scrolls = [k for k in arms if rendered[k]["internalScroll"]]
    assert not scrolls, f"internal scrolling on {scrolls}"
    # Not vacuous: the narrow arms really were rendered narrow.
    for name in ("receiptNarrow", "compare4Narrow", "compare8Narrow"):
        assert rendered[name]["height"] > 0
