"""D-420d: one shared button pair on every card, in both server profiles, in real Chromium.

The founder's ruling (2026-11-22): every card's actions are one pair. The primary, the card's "Open … in Keel ↗",
is FILLED with Keel's brand purple and light text that passes AA on it in both host themes (ChatGPT: "use brand
accent colors on primary buttons"; its brand rule asks >= 2:1 against white and against #212121). The secondary
(Expand) is quiet: transparent, the host's border token, the host's ink; never a white or host-grey fill. Both 36 px,
44 under 480, one radius and one label style.

``c_button_pair_check.mjs`` draws each card with a real envelope per (host theme × OS scheme × dialect × width),
including the OS-against-host mismatch arms, and reports every link-row action. Asserted per arm: the first action
is the primary (filled with the theme's step, its label >= 4.5:1 on it); every other action is quiet (no fill, its
label >= 4.5:1 on the host's ground); the heights, radius and label style agree across the row and the cards.

Under the listed profile every kind but screener and study serves the LEGACY adapter (host-adapter-listed.js, R-031),
whose row carries no class: card.css also marks the row's first link that opens a URL as the primary.

Seeded red (in this file): (1) card.css without that fallback selector: the listed legacy cards draw an unfilled
primary while the full-profile cards (whose adapter marks the class) stay green, the control; (2) the dark fill at
the accent's mark step (#6b71e3): its white label measures 4.1:1 and every dark arm reds, the light arms are the
control.

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
HARNESS = CARDS / "c_button_pair_check.mjs"
REPO = HERE.parents[3]
PLAYWRIGHT = REPO / "services" / "keel-app" / "node_modules" / "playwright"

#: Every card kind a listed or full host draws, with one real envelope each.
HOUSE = {
    "backtest": "c_backtest.envelope.json",
    "compare": "compare_4.envelope.json",
    "strategy": "strategy_hrp.envelope.json",
    "live": "c_live_overview.envelope.json",
    "screener": "c_screener.envelope.json",
    "study": "study/study-chk_v3.json",
}
#: The primary's fill per HOST theme (card.css `--btn-fill`).
FILL = {"light": (75, 76, 186), "dark": (94, 100, 216)}
FALLBACK = ',\n.link-row > .button-link[href^="http"]:first-child:not(.button-quiet) {\n'
DARK_FILL = "--btn-fill: #5e64d8;"


def _browser() -> None:
    if not shutil.which("node") or not PLAYWRIGHT.exists():
        if os.environ.get("CI"):
            pytest.fail("node or keel-app's playwright is missing under CI (test_card_browser.py)")
        pytest.skip("no node or keel-app playwright here")


def _envelope(name: str) -> dict:
    d = json.loads((CARDS / name).read_text())
    if "structuredContent" not in d:
        # the text block carries the envelope too, as a host's tool result does
        d = {
            "content": [{"type": "text", "text": json.dumps(d)}],
            "structuredContent": d,
            "_meta": {},
        }
    return d


def _html(kind: str, listed: bool, monkeypatch) -> str:
    from keel.widgets import build_card_html

    if listed:
        monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    else:
        monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    return build_card_html(kind)


def _draw(tmp: Path, cards: list[dict], arms=None) -> list[dict]:
    path = tmp / "input.json"
    payload = {"cards": cards}
    if arms:
        payload["arms"] = arms
    path.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), str(path)], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])["arms"]
    # non-vacuity: every arm drew at least one action
    assert out and all(a["buttons"] for a in out), out
    return out


def _rgb(css: str) -> tuple[float, ...]:
    return tuple(float(v) for v in css[css.index("(") + 1 : css.index(")")].split(","))


def _lum(rgb) -> float:
    def ch(v):
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def _hex(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i : i + 2], 16) for i in (1, 3, 5))


def _problems(arm: dict) -> list[str]:
    bad = []
    ground = _hex(arm["ground"])
    first, rest = arm["buttons"][0], arm["buttons"][1:]
    fill = _rgb(first["bg"])
    if tuple(round(v) for v in fill[:3]) != FILL[arm["host"]] or (len(fill) > 3 and fill[3] < 1):
        bad.append(f"primary fill {first['bg']} is not the {arm['host']} step")
    elif _contrast(_rgb(first["fg"]), fill) < 4.5:
        bad.append(f"primary label {first['fg']} on {first['bg']} < 4.5")
    for b in rest:
        bg = _rgb(b["bg"])
        if len(bg) < 4 or bg[3] != 0:
            bad.append(f"quiet {b['text']!r} is filled: {b['bg']}")
        if _contrast(_rgb(b["fg"]), ground) < 4.5:
            bad.append(f"quiet {b['text']!r} label {b['fg']} on {arm['ground']} < 4.5")
    want = 44 if arm["width"] < 480 else 36
    for b in arm["buttons"]:
        if b["h"] != want:
            bad.append(f"{b['text']!r} is {b['h']} px, not {want}")
        if b["radius"] != "8px" or b["font"] != "14px/500":
            bad.append(f"{b['text']!r} shape {b['radius']} {b['font']}")
    return bad


def test_the_fills_hold_the_brand_rules():
    """ChatGPT's brand colour: >= 2:1 against white and against #212121; the label on it >= 4.5:1."""
    white, chat_dark = (255, 255, 255), (33, 33, 33)
    for theme, fill in FILL.items():
        assert _contrast(fill, white) >= 2 and _contrast(fill, chat_dark) >= 2, theme
        assert _contrast(fill, white) >= 4.5, theme  # the white label on the fill


@pytest.mark.parametrize("listed", [False, True], ids=["full", "listed"])
def test_every_card_draws_the_one_pair(tmp_path, monkeypatch, listed):
    _browser()
    cards = [
        {"name": kind, "html": _html(kind, listed, monkeypatch), "envelope": _envelope(env)}
        for kind, env in sorted(HOUSE.items())
    ]
    arms = _draw(tmp_path, cards)
    assert {a["card"] for a in arms} == set(HOUSE)
    # the mismatch arms are present (OS against host)
    assert {(a["os"], a["host"]) for a in arms} >= {("light", "dark"), ("dark", "light")}
    bad = {(a["card"], a["host"], a["os"], a["dialect"], a["width"]): _problems(a) for a in arms}
    assert not {k: v for k, v in bad.items() if v}, bad
    # Expand is drawn where the host offers it (non-vacuity for the quiet half)
    assert any(len(a["buttons"]) == 2 for a in arms)


def test_seeded_without_the_fallback_the_listed_primary_is_unfilled(tmp_path, monkeypatch):
    _browser()
    arms = [["light", "light", "mcp", 760], ["dark", "dark", "mcp", 760]]
    cards = []
    for listed in (False, True):
        html = _html("backtest", listed, monkeypatch)
        assert html.count(FALLBACK) == 1, "the seed's anchor moved"
        cards.append(
            {
                "name": "listed" if listed else "full",
                "html": html.replace(FALLBACK, " {\n"),
                "envelope": _envelope(HOUSE["backtest"]),
            }
        )
    out = _draw(tmp_path, cards, arms)
    for a in out:
        if a["card"] == "listed":
            assert _problems(a), a  # red: the legacy row's primary has no class to match
        else:
            assert not _problems(a), a  # control: the full adapter marks button-primary


def test_seeded_the_accent_mark_as_the_dark_fill_fails_aa(tmp_path, monkeypatch):
    _browser()
    html = _html("backtest", False, monkeypatch)
    assert html.count(DARK_FILL) == 2, "the seed's anchor moved"
    seeded = html.replace(DARK_FILL, "--btn-fill: #6b71e3;")
    arms = [["light", "light", "mcp", 760], ["dark", "dark", "mcp", 760]]
    out = _draw(
        tmp_path, [{"name": "b", "html": seeded, "envelope": _envelope(HOUSE["backtest"])}], arms
    )
    by = {a["host"]: a for a in out}
    assert by["light"]["buttons"] and not _problems(by["light"])  # control
    first = by["dark"]["buttons"][0]
    assert _contrast(_rgb(first["fg"]), _rgb(first["bg"])) < 4.5, first  # red
