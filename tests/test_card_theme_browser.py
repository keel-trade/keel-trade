"""Q-7890: a card follows the HOST's theme, never the OS's, in real Chromium.

``c_theme_check.mjs`` draws a served card inside an embedder that behaves as a host does (its root declares the
host's color-scheme and paints the host's ground), once per OS scheme × host theme × dialect (MCP Apps, ChatGPT), and
reads the pixel behind the card and the title's colour. Claude's transparent-theming guide: "Browsers give iframe
documents an opaque canvas backdrop, white in light mode and near-black in dark mode, when the iframe's color-scheme
differs from the embedding page"; the app's theme call "sets `<html data-theme>` and the root `color-scheme`".

Asserted per arm: the card's root color-scheme is the host's; the pixel behind the card is the host's ground (the
canvas stayed transparent); the title clears WCAG AA against it.

Seeded red (in this file): the adapter without the color-scheme line and card.css without its declarations, i.e. the
tree before Q-7890: the OS-dark × host-light arms paint the near-black backdrop under dark text (contrast 1.02), and
the OS-light × host-dark arms a white one under light text (1.05). Control under the
same seed: the arms where the OS and the host agree stay green, so the arm reacts to the disagreement, not to the
harness.

Skips locally without node or keel-app's playwright; under CI it may not (test_card_browser.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
CARDS = HERE / "fixtures" / "cards"
HARNESS = CARDS / "c_theme_check.mjs"
ENVELOPE = CARDS / "study" / "study-chk_v3.json"
REPO = HERE.parents[3]
PLAYWRIGHT = REPO / "services" / "keel-app" / "node_modules" / "playwright"

#: The tree before Q-7890, one exact edit each (adapter, card.css twice).
SEEDS = (
    ("      document.documentElement.style.colorScheme = theme;\n", ""),
    ("  color-scheme: dark; /* Q-7890: the host's scheme, never the OS's */\n", ""),
    ("  color-scheme: light; /* Q-7890 */\n", ""),
)


def _browser() -> None:
    if not shutil.which("node") or not PLAYWRIGHT.exists():
        if os.environ.get("CI"):
            pytest.fail("node or keel-app's playwright is missing under CI (test_card_browser.py)")
        pytest.skip("no node or keel-app playwright here")


#: The house cards (D-420 MCP-CARDS, the integrator's ask) with one real envelope each; under the listed profile every
#: kind but screener and study serves the LEGACY adapter (host-adapter-listed.js, R-031), so both profiles are drawn.
HOUSE = {
    "backtest": "c_backtest.envelope.json",
    "compare": "compare_4.envelope.json",
    "strategy": "strategy_hrp.envelope.json",
    "screener": "c_screener.envelope.json",
}


def _envelope(tmp: Path, name: str) -> Path:
    """A bare card envelope as a host delivers it (structuredContent)."""
    d = json.loads((CARDS / name).read_text())
    if "structuredContent" not in d:
        # the text block carries the envelope too, as a host's tool result does (a "{}" text drew the adapter's
        # empty state, so the theme arms ran on an empty card: SCREENS-CARD's review, 11-22)
        d = {
            "content": [{"type": "text", "text": json.dumps(d)}],
            "structuredContent": d,
            "_meta": {},
        }
    out = tmp / f"env-{name}"
    out.write_text(json.dumps(d), encoding="utf-8")
    return out


def _html(kind: str, listed: bool, monkeypatch) -> str:
    from keel.widgets import build_card_html

    if listed:
        monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    else:
        monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    return build_card_html(kind)


def _draw(tmp: Path, html: str, envelope: Path) -> list[dict]:
    card = tmp / "card.html"
    card.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), "--card", str(card), "--envelope", str(envelope)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    arms = json.loads(proc.stdout.strip().splitlines()[-1])["arms"]
    assert len(arms) == 8 and all(a["titleText"] for a in arms), arms  # non-vacuity
    # non-vacuity: every arm drew its result, never the adapter's empty state
    assert all(a["drawn"] > 0 and not a["empty"] for a in arms), [
        (a["drawn"], a["empty"]) for a in arms
    ]
    return arms


@pytest.mark.parametrize("listed", [False, True], ids=["full", "listed"])
@pytest.mark.parametrize("kind", sorted(HOUSE))
def test_every_house_card_follows_the_host_theme(tmp_path, monkeypatch, kind, listed):
    """The same arm over the house cards in both server profiles: the listed directory's legacy adapter
    (host-adapter-listed.js) carried the same bug, and its fix is in the same commit."""
    _browser()
    arms = _draw(tmp_path, _html(kind, listed, monkeypatch), _envelope(tmp_path, HOUSE[kind]))
    bad = [a for a in arms if not _ok(a)]
    assert not bad, bad


@pytest.mark.parametrize("kind", ["backtest", "strategy"])
def test_seeded_the_listed_adapter_before_q7890_paints_the_os_backdrop(tmp_path, monkeypatch, kind):
    """SEED on the LISTED bytes: the legacy adapter and card.css as they were; OS dark under a light host reds;
    the agreeing arms are the control."""
    _browser()
    html = _html(kind, True, monkeypatch)
    listed_adapter = (
        REPO / "packages/keel-trade/keel-sdk/keel/widgets/assets/host-adapter-listed.js"
    ).read_text()
    assert listed_adapter in html  # non-vacuity: these are the listed directory's legacy bytes
    for old, new in SEEDS:
        assert html.count(old) == 1, old
        html = html.replace(old, new)
    arms = _draw(tmp_path, html, _envelope(tmp_path, HOUSE[kind]))
    by = {(a["os"], a["host"], a["dialect"]): a for a in arms}
    for dialect in ("mcp", "openai"):
        assert not _seen_ok(by[("dark", "light", dialect)]), by[("dark", "light", dialect)]
        assert _seen_ok(by[("light", "light", dialect)]) and _seen_ok(by[("dark", "dark", dialect)])


def _run(tmp: Path, seeded: bool) -> list[dict]:
    from keel.widgets import build_card_html

    html = build_card_html("study")
    if seeded:
        for old, new in SEEDS:
            assert html.count(old) == 1, old
            html = html.replace(old, new)
    card = tmp / "card.html"
    card.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), "--card", str(card), "--envelope", str(ENVELOPE)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    arms = json.loads(proc.stdout.strip().splitlines()[-1])["arms"]
    # non-vacuity: 2 OS × 2 host × 2 dialects, every one drew its title and its result
    assert len(arms) == 8
    assert all(a["titleText"] for a in arms), arms
    assert all(a["drawn"] > 0 and not a["empty"] for a in arms), [
        (a["drawn"], a["empty"]) for a in arms
    ]
    return arms


def _seen_ok(arm: dict) -> bool:
    """What a person sees: the host's own ground behind the card, and a legible title on it."""
    return arm["ground"] == arm["expected"] and (arm["contrast"] or 0) >= 4.5


def _ok(arm: dict) -> bool:
    return arm["scheme"] == arm["host"] and _seen_ok(arm)


def test_the_card_follows_the_host_theme_not_the_os(tmp_path):
    _browser()
    arms = _run(tmp_path, seeded=False)
    bad = [a for a in arms if not _ok(a)]
    assert not bad, bad
    # the disagreeing arms are present (the point of the arm)
    assert {(a["os"], a["host"]) for a in arms} >= {("dark", "light"), ("light", "dark")}


def test_seeded_the_tree_before_q7890_paints_the_os_backdrop(tmp_path):
    _browser()
    arms = _run(tmp_path, seeded=True)
    by = {(a["os"], a["host"], a["dialect"]): a for a in arms}
    for dialect in ("mcp", "openai"):
        # OS dark under a light host: the near-black backdrop under dark text; OS light under a dark host: white
        for disagree in (("dark", "light"), ("light", "dark")):
            red = by[(*disagree, dialect)]
            assert not _seen_ok(red), red
            assert red["ground"] != red["expected"], red
        # control: the OS and the host agree, the person sees the same card
        for agree in (("light", "light"), ("dark", "dark")):
            assert _seen_ok(by[(*agree, dialect)]), by[(*agree, dialect)]
