"""Opening a receipt on ChatGPT (Q-1774).

Founder-confirmed on ChatGPT: a human click on "Show result" (backtest
receipt) or "Show pipeline" (strategy receipt) had NO visible effect, while
the same clicks open in place on claude.ai. ChatGPT sizes the widget frame
itself (BUILD §4.5): the in-place open grew a document the frame never grew
to show. On ChatGPT the disclosure now asks for fullscreen — where every
card draws its full layout — and opens the Keel link when fullscreen is not
granted.

``tests/fixtures/cards/c_openai_open_check.mjs`` clicks the real disclosure
in real Chromium against an openai-globals host that records every
``requestDisplayMode`` / ``openExternal`` call. Skips (never silently passes)
without node or Playwright.

Proof it can fail: ``# SEED:`` below, run 2026-09-22, recorded in the commit.
Proof it is not vacuous: every arm starts as ONE receipt row with no opened
body (the click had something to change), and the MCP Apps control arm DOES
open in place — the same click, the same fixture — so the ChatGPT arms'
behaviour is the dialect's, not a dead button.
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
CHECK = FIXTURES / "c_openai_open_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"


@pytest.fixture(scope="module")
def clicks(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; opening a card is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("open-cards")
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
        pytest.fail(f"the open check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def _calls(arm: dict, kind: str) -> list:
    return [c[1] for c in (arm["after"]["calls"] or []) if c[0] == kind]


def test_the_open_scan_was_not_vacuous(clicks: dict) -> None:
    for name in ("grantBacktest", "grantStrategy", "denyBacktest", "noApiBacktest", "mcpBacktest"):
        before = clicks[name]["before"]
        assert before["receiptRows"] == 1 and not before["opened"], name
    # CONTROL: on MCP Apps the same click opens in place, and asks the host
    # for nothing.
    mcp = clicks["mcpBacktest"]["after"]
    assert mcp["opened"] and mcp["tiles"] > 0
    assert "ui/request-display-mode" not in mcp["sent"]


def test_show_on_chatgpt_asks_for_fullscreen_and_draws_the_full_card(clicks: dict) -> None:
    """The click is a request the host can honour, and once granted the card
    is its full layout — tiles for a run, the pipeline for a strategy."""
    # SEED: in host-adapter.js change `if (isOpenAI && !body) openOnChatGPT(
    # spec, toggle);` to call `toggle()` — the click opens in place inside a
    # frame ChatGPT never grows, no display mode is requested, and this reds.
    bt = clicks["grantBacktest"]
    assert _calls(bt, "rdm") == [{"mode": "fullscreen"}]
    assert bt["after"]["mode"] == "fullscreen"
    assert bt["after"]["tiles"] > 0 and bt["after"]["receiptRows"] == 0
    st = clicks["grantStrategy"]
    assert _calls(st, "rdm") == [{"mode": "fullscreen"}]
    assert st["after"]["mode"] == "fullscreen"
    assert st["after"]["blocks"] > 0
    assert _calls(bt, "ext") == [] and _calls(st, "ext") == []


def test_without_fullscreen_the_keel_link_opens(clicks: dict) -> None:
    """A host that declines fullscreen, or has no display-mode API, still
    takes the reader somewhere real: the run's page in Keel."""
    # SEED: make `fallback` in openOnChatGPT call `toggle()` only — the deny
    # and no-API arms stop opening the link and this reds.
    url = clicks["btUrl"]
    assert url
    for name in ("denyBacktest", "noApiBacktest"):
        arm = clicks[name]
        assert _calls(arm, "ext") == [{"href": url}], name
        assert arm["after"]["mode"] == "inline", name
    assert _calls(clicks["noApiBacktest"], "rdm") == []


def test_chatgpt_never_draws_the_wordmark_in_any_state(clicks: dict) -> None:
    """ChatGPT names the app above every widget itself (REVIEW §4.4), so no
    state of any card draws "Keel" there: receipts, the in-flight row, a
    full card's own head, fullscreen (Q-1776). CONTROL: MCP Apps still
    draws it."""
    # SEED: delete the `:root[data-dialect="openai"] .brand, … .sv-brand`
    # rule in card.css — the in-flight and full strategy arms red.
    for name in (
        "grantBacktest",
        "grantStrategy",
        "openaiInflight",
        "openaiInflightView",
        "openaiStrategyFull",
    ):
        for state in ("before", "after"):
            assert clicks[name][state]["brands"] == 0, (name, state)
    assert "Running backtest…" in clicks["openaiInflight"]["before"]["text"]
    assert clicks["openaiStrategyFull"]["before"]["blocks"] > 0
    assert clicks["mcpStrategyFull"]["before"]["brands"] >= 1
    assert clicks["mcpBacktest"]["before"]["brands"] >= 1
