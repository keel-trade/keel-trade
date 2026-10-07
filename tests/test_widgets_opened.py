"""The opened-card guards (Q-1777 …).

Founder, claude.ai, on an OPENED compose receipt ("Hide pipeline"): the card
drew a raw change line under the chips — `Execution · buffer 0.1 → 0.2 |
Universe · resolved ["AAVE","ARB","AVAX","BNB","B… → —` — "resolve universe
should look better here, i thought we had a good design for that". The good
design is the chips directly above it (`universe Top 30 by volume · HL
perps ▾`, `execution Buffered 0.2 ▾`).

``tests/fixtures/cards/c_opened_check.mjs`` drives the real cards in Chromium
through the real handshake, opens the receipt the way a person does, and
reads what is on screen. Skips (never silently passes) without node or
Playwright.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the commit.
Proof it is not vacuous: the founder's save really carries a 30-symbol list
(asserted from the fixture), the opened arm really opened, and the CONTROL
save (no declaration change) keeps plain, unmarked chips.
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
CHECK = FIXTURES / "c_opened_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


#: A completed run as `GET /v1/backtests/{id}` returns it — the worker's
#: own spellings, so the view's key/sign normalisation is exercised too.
_RUN_METRICS = {
    "sharpe_ratio": 1.8,
    "total_return": 42.5,
    "max_drawdown": 12.0,
    "turnover": 12.0,
    "total_trades": 87,
    "win_rate": 51.0,
    "sortino_ratio": 2.1,
    "calmar_ratio": 1.4,
    "profit_factor": 1.3,
    "total_fees_paid": 55.0,
}


def _served_envelopes() -> tuple[dict, dict]:
    """(summarize envelope, compare envelope) from the REAL tool handlers,
    with only the HTTP layer faked (Q-1787)."""
    from unittest.mock import patch

    from keel.tools.outcomes import OUTCOMES, backtest_compare, backtest_summarize  # noqa: F401
    from keel.tools.outcomes._base import ToolContext

    def fake_get(path, **_kw):
        if path.startswith("/v1/backtests/btr_") and path.count("/") == 3:
            run_id = path.rsplit("/", 1)[1]
            return {
                "id": run_id,
                "status": "COMPLETED",
                "strategy_id": "str_mom",
                "strategy_name": "Momentum",
                "sequence_number": 2 if run_id == "btr_a" else 3,
                "start_date": "2024-08-15",
                "end_date": "2026-02-27",
                "metrics": _RUN_METRICS,
            }
        return {}  # results / curve: best-effort reads, empty is honest

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        summarize = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_a"}, ctx)
        compare = OUTCOMES["keel_backtest_compare"].handler(
            {"backtest_ids": ["btr_a", "btr_b"]}, ctx
        )
    return summarize.to_envelope(), compare.to_envelope()


def _served_arms() -> dict:
    import copy

    bt, cmp_ = _served_envelopes()
    # The override arms: the SAME server envelopes with the list reordered
    # and one label reworded — a card that draws its own list cannot pass.
    bt2, cmp2 = copy.deepcopy(bt), copy.deepcopy(cmp_)
    tiles = bt2["view"]["tiles"]
    bt2["view"]["tiles"] = [dict(tiles[2], label="Sharpe ratio"), tiles[3], tiles[0], tiles[1]]
    rows = cmp2["view"]["rows"]
    cmp2["view"]["rows"] = [dict(rows[2], label="Sharpe ratio"), rows[3], rows[0], rows[1]] + rows[
        4:
    ]
    return {
        # The founder's width (Q-1799): "MAX DRAWDOWN" was cut to "MAX DRAWDO…".
        "servedBacktest481": {"kind": "backtest", "envelope": bt, "width": 481},
        "servedBacktest": {"kind": "backtest", "envelope": bt},
        "servedCompare": {"kind": "compare", "envelope": cmp_},
        "servedBacktestReordered": {"kind": "backtest", "envelope": bt2},
        "servedCompareReordered": {"kind": "compare", "envelope": cmp2},
    }


@pytest.fixture(scope="module")
def served_arms() -> dict:
    return _served_arms()


@pytest.fixture(scope="module")
def opened(tmp_path_factory, served_arms: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the opened card is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("opened-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    served_file = cards / "served.json"
    served_file.write_text(json.dumps(served_arms), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(CHECK),
            "--cards",
            str(cards),
            "--fixtures",
            str(FIXTURES),
            "--served",
            str(served_file),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the opened check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_opened_scan_was_not_vacuous(opened: dict) -> None:
    assert opened["symbols"] >= 10, "the founder's save must carry a real list"
    assert opened["openedSave"]["opened"] and opened["openedPlain"]["opened"]
    plain = opened["openedPlain"]["cfgChips"]
    assert plain and not any(c["changed"] for c in plain), plain


def test_a_declaration_change_rides_its_chip_never_a_raw_line(opened: dict) -> None:
    """The opened save draws no `A | B` sentence and no printed list; the
    changed chips are marked, old → new inside them; a resolved list is
    its count."""
    # SEED: restore `if (marks && view.chg && view.chg.summary)
    # card.appendChild(el("p", "sv-summary", view.chg.summary));` in
    # renderStructure — the pipe line and the raw list return.
    m = opened["openedSave"]
    assert m["summaries"] == [], m["summaries"]
    assert " | " not in m["text"] and '["' not in m["text"], m["text"][:300]
    chips = {c["text"].split("\n")[0]: c for c in m["cfgChips"]}
    assert chips["execution"]["changed"] and "0.1" in chips["execution"]["text"]
    assert "0.2" in chips["execution"]["text"]
    assert chips["universe"]["changed"]
    assert f"{opened['symbols']} assets" in chips["universe"]["text"]


def test_the_change_card_draws_declarations_as_chips_once(opened: dict) -> None:
    """The full change card draws each declaration as its split chip — a
    list as its count — and not again as a summary sentence."""
    # SEED: in declValue return fmtVal(v) for arrays — the change card's
    # universe chip prints the JSON list and this reds.
    m = opened["changeCard"]
    assert m["summaries"] == [], m["summaries"]
    assert '["' not in m["text"]
    assert any(f"{opened['symbols']} assets" in c for c in m["declChips"]), m["declChips"]
    assert any("0.1" in c and "0.2" in c for c in m["declChips"]), m["declChips"]


# ── One Open, and the name gets the room (Q-1778) ───────────────────────────


def test_an_opened_card_has_exactly_one_open(opened: dict) -> None:
    """F-2: one Open affordance per card in every state. Opened in place,
    the action row beneath owns it; the row's own `Open ↗` goes. CONTROL:
    closed, the row's `Open ↗` IS the one."""
    # SEED: delete `body[data-size="receipt-open"] .rr a.rr-act` from the
    # opened-in-place rule in card.css — the opened arms show two Opens.
    for arm in ("openedSave", "openedPlain", "openedLong", "openedRun"):
        assert len(opened[arm]["opens"]) == 1, (arm, opened[arm]["opens"])
        assert opened[arm]["opens"][0] != "Open ↗", arm
    for arm in ("closedLong", "closedRun"):
        assert opened[arm]["opens"] == ["Open ↗"], (arm, opened[arm]["opens"])


def test_the_opened_row_gives_the_name_its_room(opened: dict) -> None:
    """Opened, the change is drawn below, so the row drops its change chip
    and the name is no longer truncated. CONTROL: closed, the change chip
    is there and the long name is truncated — the case the founder saw."""
    # SEED: delete `body[data-size="receipt-open"] .rr .rr-sc` from the
    # rule — the opened long name stays truncated behind the change chip.
    closed = opened["closedLong"]
    assert closed["rowChange"], "the closed row carries its change"
    assert closed["name"]["cut"], "the control is not truncated"
    op = opened["openedLong"]
    assert op["rowChange"] == [], op["rowChange"]
    assert not op["name"]["cut"], op["name"]


# ── No rule above the actions (Q-1779) ──────────────────────────────────────


def test_no_card_draws_a_rule_above_its_actions(opened: dict) -> None:
    """Founder: "a lot have an extra line on the bottom we don't need, below
    the graph". No card kind, closed, opened or full, draws a divider above
    its action band or its own footer — spacing separates them."""
    # SEED: put `border-top: 1px solid var(--line);` back on `.link-row` in
    # card.css — every arm with an action band reds.
    arms = [k for k, v in opened.items() if isinstance(v, dict)]
    with_bands = [k for k in arms if opened[k]["actionBands"] > 0]
    # Not vacuous: every full kind and every opened receipt HAS a band.
    for k in ("fullBacktest", "fullCompare", "fullStrategy", "fullLive", "openedSave", "openedRun"):
        assert k in with_bands, k
    for k in with_bands:
        assert opened[k]["actionRules"] == [], (k, opened[k]["actionRules"])


# ── The headline numbers (founder ruling 2026-09-22, Q-1781) ────────────────

APP_FOUR = ["Return", "Max drawdown", "Sharpe", "Win rate"]


def test_the_backtest_tiles_are_the_apps_first_four(opened: dict) -> None:
    """Return · Max drawdown · Sharpe · Win rate, in that order and those
    words, on an envelope from BEFORE the server served `view.tiles` (the
    legacy list); Return signed and coloured, Max drawdown neutral."""
    # SEED: put the "sharpe" row first in LEGACY_TILES in card-backtest.js
    # — the order reds.
    tiles = opened["fullBacktest"]["tiles"]
    # The tile label is drawn upper-case by CSS; compare case-blind.
    assert [t["label"].lower() for t in tiles[:4]] == [a.lower() for a in APP_FOUR]
    ret, dd = tiles[0], tiles[1]
    assert ret["value"][0] in "+−" and ret["tone"] in ("pos", "neg"), ret
    assert dd["tone"] == "", dd


def test_the_comparison_rows_follow_the_same_four(opened: dict) -> None:
    rows = opened["fullCompare"]["cmpRows"]
    assert rows[:4] == APP_FOUR, rows


def test_a_real_server_envelope_draws_the_servers_four_in_order(
    opened: dict, served_arms: dict
) -> None:
    """The contract (Q-1787): an envelope built by the REAL tool handler
    (`keel_backtest_summarize` / `keel_backtest_compare`, not a hand
    fixture) draws exactly the labels the server served, in its order —
    the card keeps no labels of its own for a current envelope."""
    # SEED: in card-backtest.js `tileList`, ignore `v.tiles` (always take
    # the legacy list) — the override arm draws the legacy order and reds.
    served = served_arms
    bt_labels = [t["label"] for t in served["servedBacktest"]["envelope"]["view"]["tiles"]]
    assert bt_labels == APP_FOUR, bt_labels  # the server's own four, in order
    tiles = opened["servedBacktest"]["tiles"]
    assert [t["label"].lower() for t in tiles[:4]] == [a.lower() for a in APP_FOUR]
    ret, dd = tiles[0], tiles[1]
    assert ret["value"][0] in "+−" and ret["tone"] in ("pos", "neg"), ret
    assert dd["value"].startswith("−") and dd["tone"] == "", dd
    rows = [r["label"] for r in served["servedCompare"]["envelope"]["view"]["rows"]]
    assert opened["servedCompare"]["cmpRows"][:4] == rows[:4] == APP_FOUR
    # The server, not the card, decides: a reordered, relabelled envelope
    # is drawn as served on both cards.
    for arm, key in (("servedBacktestReordered", "tiles"), ("servedCompareReordered", "cmpRows")):
        view = served[arm]["envelope"]["view"]
        want = [t["label"] for t in view.get("tiles") or view.get("rows")][:4]
        got = opened[arm][key][:4]
        got = [g["label"] if isinstance(g, dict) else g for g in got]
        assert [g.lower() for g in got] == [w.lower() for w in want], (arm, got, want)
    # Non-vacuity the seed cannot move: the override really differs from
    # the legacy order, and every arm drew its four.
    assert want[0] != APP_FOUR[0]
    assert all(len(opened[a]["tiles"]) >= 4 for a in ("servedBacktest", "servedBacktestReordered"))


def test_no_tile_label_is_cut_at_a_narrow_host(opened: dict, served_arms: dict) -> None:
    """Q-1799: at 481 px the headline tile read "MAX DRAWDO…". A label that
    does not fit draws the server's `short_label` instead; one that fits
    keeps its full word (the 720 px control)."""
    # SEED: in host-adapter.js `fitTileLabels`, never swap to the short
    # form (`if (false && lab.scrollWidth …`) — the 481 px arm reads a cut
    # "MAX DRAWDOWN" and this reds.
    narrow = opened["servedBacktest481"]["tiles"]
    wide = opened["servedBacktest"]["tiles"]
    assert len(narrow) >= 4 and len(wide) >= 4  # not vacuous
    cut = [t["label"] for t in narrow if t["cut"]]
    assert not cut, f"labels cut at 481 px: {cut}"
    served = served_arms["servedBacktest"]["envelope"]["view"]["tiles"]
    assert served[1]["short_label"] == "Max DD"  # the server says the short form
    assert narrow[1]["label"].lower() == "max dd", narrow[1]
    # Control: where it fits, the full word.
    assert wide[1]["label"].lower() == "max drawdown", wide[1]
    assert not any(t["cut"] for t in wide), wide


# ── Chart text that cannot be misread (Q-1783) ──────────────────────────────

MONTH_YEAR = re.compile(r"^[A-Z][a-z]{2} \d{4}$")


def test_a_time_axis_uses_one_unambiguous_format(opened: dict) -> None:
    """A span of months is labelled month + year on EVERY tick — never
    "Aug 9, 2024 · Apr 24 · Jan 6 · Sep 21, 2026", where "Apr 24" read as a
    date in the wrong year. Both charts, one owner (`H.fmtAxisDates`)."""
    # SEED: in card-compare.js label ticks with
    # `H.fmtDate(new Date(t), j === 0 || j === n - 1)` again — reds.
    for arm in ("fullBacktest", "fullCompare", "openedRun"):
        ticks = opened[arm]["xAxis"]
        assert len(ticks) >= 3, (arm, ticks)
        assert all(MONTH_YEAR.match(t) for t in ticks), (arm, ticks)
        years = [int(t[-4:]) for t in ticks]
        assert years == sorted(years), (arm, ticks)


def test_the_readout_says_its_percentage_is_a_drawdown(opened: dict) -> None:
    """The chart's readout percentage is the drawdown from peak at that
    point; beside a "+37.9%" headline an unlabelled "−13.9%" read as a
    return."""
    # SEED: drop the " from peak" suffix in card-backtest.js's readout —
    # reds here.
    for arm in ("fullBacktest", "openedRun"):
        r = opened[arm]["readout"]
        assert r, (arm, "no readout drawn")
        assert r.endswith("from peak") or r.endswith("at peak"), (arm, r)
