"""Names keep the part that tells them apart (Q-1780).

ChatGPT pass (staging, 768 px): three strategies "Simple Mean Reversion (Top 20
Perps, 20D)" / "…10D)" / "…30D)". The comparison's column headers all read
"Simple Mean Reversion (To…" — indistinguishable — and each run receipt read
"Simple Mean Reversion (Top 20 Perps…": 10D and 30D could not be told apart
anywhere but the legend.

``tests/fixtures/cards/c_names_check.mjs`` renders those names in real
Chromium and reads what is SHOWN. Skips (never silently passes) without node
or Playwright.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the commit.
Proof it is not vacuous: every name here is too long for its slot (the
receipts are really cut, the full name is recorded beside what is shown), and
the CONTROLS — one strategy's version labels, a short name — come through
unchanged.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "c_names_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def names(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; what a name shows is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("name-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--fixtures", str(FIXTURES)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the names check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def _tail(name: str) -> str:
    """The distinguishing end of a fixture name: "10D" of "…, 10D)"."""
    return name.rsplit(", ", 1)[1].rstrip(")")


def _clean(label: str) -> str:
    return label.split("\n")[0]


def test_the_names_scan_was_not_vacuous(names: dict) -> None:
    tails = {_tail(n) for n in names["names"]}
    assert len(tails) == 3, "the fixture names must differ only at the end"
    for arm in ("runReceipt481", "saveReceipt360"):
        rr = names[arm]["rrName"]
        # Too long for its slot, however it was cut — a quantity the seed
        # below cannot move (it changes HOW the name is cut, not whether).
        too_long = rr["shown"] != rr["full"] or rr["clipped"]
        assert too_long, (arm, "the name was not too long — nothing to test")


def test_a_comparison_labels_runs_by_what_differs(names: dict) -> None:
    """The shared stem is said once, in the header; each column, legend
    entry and narrow block is labelled by the part that differs. CONTROL:
    one strategy's version labels (no long shared stem) are untouched."""
    # SEED: make `distinctLabels` return `none` unconditionally — the three
    # columns read the same truncated stem again and this reds.
    want = [_tail(n) for n in names["names"]]
    wide = names["cross3Wide"]
    assert [_clean(c) for c in wide["columns"]] == want, wide["columns"]
    assert wide["legend"] == want, wide["legend"]
    assert wide["headName"].startswith("Simple Mean Reversion (Top 20 Perps")
    for n in names["names"]:
        assert n in wide["headTitle"], "the full names stay reachable"
    narrow = names["cross3Narrow"]
    assert [lead.replace("BASELINE", "") for lead in narrow["leads"]] == want
    control = names["compare4"]
    assert [_clean(c) for c in control["columns"]] == names["compare4Labels"]


def test_a_cut_receipt_name_keeps_its_end(names: dict) -> None:
    """A name too long for the row is cut in the MIDDLE, so "10D" survives;
    the whole name stays in the title. CONTROL: a name that fits is whole."""
    # SEED: in host-adapter.js make `middleTruncate` return before cutting —
    # the host's trailing ellipsis eats the end again and this reds.
    for arm, idx in (("runReceipt481", 1), ("saveReceipt360", 2)):
        rr = names[arm]["rrName"]
        assert rr["full"] == names["names"][idx]
        assert rr["shown"].endswith(_tail(names["names"][idx]) + ")"), (arm, rr)
        assert "…" in rr["shown"] and not rr["clipped"], (arm, rr)
    whole = names["runReceipt720"]["rrName"]
    assert whole["shown"] == whole["full"] and not whole["clipped"], whole


def test_the_narrow_comparison_names_its_columns_once(names: dict) -> None:
    """Q-1784: under 480 px each run block repeated SHARPE / RETURN / MAX DD
    / FILLS across two lines. The names are now one header row, and every
    block's four numbers sit in that header's columns."""
    # SEED: in card-compare.js `blocks()` put `f.appendChild(el("span", "k",
    # spec.label));` back before the value — the labels repeat and this reds.
    m = names["cross3Narrow"]
    assert [h.lower() for h in m["blockHead"]] == ["return", "max dd", "sharpe", "win rate"]
    assert m["blockLabels"] == 0, "a block repeats the column names"
    assert len(m["colX"]) == 3, "not every run drew a block"
    for cols in m["colX"]:
        assert cols == m["headX"], (cols, m["headX"])
