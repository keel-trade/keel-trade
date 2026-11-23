"""D-420a: tables never draw white lines, on the compare and live cards, both profiles, in real Chromium.

The founder's standing rule. The compare card's grid (`table.cmp`, the narrow `.cmp-blocks` list) and the live card's
tables (`table.rows`, `table.kv`, the narrow `.row-item` list) drew their rules with `--line`, which the host adapter
maps to the host's PRIMARY border: Claude's is 40 % of its ink, so on a dark host every row was a light grey line
(SCREENS-CARD's shots: 15 rules on a comparison, 4 on the live overview). They now draw `--rule`, the host's tertiary
border (15 %), in both themes.

``c_table_rules_check.mjs`` draws each card with a real envelope on Claude's light and dark grounds (its documented
tokens, through the MCP Apps bridge), at 760 and 380 px, and reports every visible table rule's computed colour.
Asserted: the card drew rules (non-vacuity); in dark, no rule composited over the ground reads lighter than it by more
than 1.6:1 (a hairline, never a white line); in both themes every rule is the host's tertiary border (alpha 0.15) on the
full profile, and card.css's own hairline (0.12 light, 0.1 dark) on the listed legacy adapter, which maps no `--rule`.

Seeded red (in this file): card.css's table rules back on `--line`: every dark arm reds (the composited rule reads
~2.6:1 lighter than the ground), while the light arms stay green on the white-line check, the control: on a light
ground the same token is a dark line, unchanged in kind.

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
HARNESS = CARDS / "c_table_rules_check.mjs"
REPO = HERE.parents[3]
PLAYWRIGHT = REPO / "services" / "keel-app" / "node_modules" / "playwright"

#: The cards that draw tables, with a real envelope each.
TABLES = {
    "compare": "compare_4.envelope.json",
    "live": "c_live_overview.envelope.json",
}
GROUND = {"light": (255, 255, 255), "dark": (48, 48, 46)}
#: card.css's table rules, as this change wrote them; the seed puts `--line` back in each.
SEED_ANCHORS = (
    "table.kv td,\ntable.rows td,\ntable.rows th {\n  padding: 4px 8px;\n  border-bottom: 1px solid var(--rule);",
    "  padding: 6px 0;\n  border-bottom: 1px solid var(--rule);\n  font-variant-numeric: tabular-nums;",
    "table.cmp td,\ntable.cmp th {\n  padding: 4px 8px;\n  border-bottom: 1px solid var(--rule);",
    ".cmp-blocks .row-head {\n  padding: 0 0 4px;\n  border-bottom: 1px solid var(--rule);",
)


def _browser() -> None:
    if not shutil.which("node") or not PLAYWRIGHT.exists():
        if os.environ.get("CI"):
            pytest.fail("node or keel-app's playwright is missing under CI (test_card_browser.py)")
        pytest.skip("no node or keel-app playwright here")


def _envelope(name: str) -> dict:
    d = json.loads((CARDS / name).read_text())
    if "structuredContent" not in d:
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


def _draw(tmp: Path, cards: list[dict]) -> list[dict]:
    path = tmp / "input.json"
    path.write_text(json.dumps({"cards": cards}), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), str(path)], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    arms = json.loads(proc.stdout.strip().splitlines()[-1])["arms"]
    # non-vacuity: every arm drew its card and its table rules
    assert arms and all(a["drew"] and a["rules"] for a in arms), arms
    return arms


def _rgba(css: str) -> tuple[float, float, float, float]:
    v = [float(x) for x in css[css.index("(") + 1 : css.index(")")].split(",")]
    return (v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0)


def _lum(rgb) -> float:
    def ch(c):
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _over(rgba, ground) -> tuple[float, float, float]:
    a = rgba[3]
    return tuple(rgba[i] * a + ground[i] * (1 - a) for i in range(3))


def white_lines(arm: dict) -> list[str]:
    """The rules that read as a light line on the ground: composited, lighter than it by more than 1.6:1."""
    ground = GROUND[arm["host"]]
    bad = []
    for r in arm["rules"]:
        seen = _over(_rgba(r["color"]), ground)
        if _lum(seen) > _lum(ground) and (_lum(seen) + 0.05) / (_lum(ground) + 0.05) > 1.6:
            bad.append(f"{r['sel']} {r['color']}")
    return bad


def _cards(monkeypatch, html_of=lambda h: h) -> list[dict]:
    return [
        {
            "name": f"{kind}-{'listed' if listed else 'full'}",
            "html": html_of(_html(kind, listed, monkeypatch)),
            "envelope": _envelope(env),
        }
        for kind, env in sorted(TABLES.items())
        for listed in (False, True)
    ]


def test_no_table_draws_a_white_line_and_every_rule_is_the_hairline(tmp_path, monkeypatch):
    _browser()
    arms = _draw(tmp_path, _cards(monkeypatch))
    assert {a["card"] for a in arms} == {f"{k}-{p}" for k in TABLES for p in ("full", "listed")}
    for arm in arms:
        assert white_lines(arm) == [], (arm["card"], arm["host"], arm["width"], white_lines(arm))
        alphas = {round(_rgba(r["color"])[3], 2) for r in arm["rules"]}
        if arm["card"].endswith("-full"):
            # the full adapter maps --rule to the host's tertiary border
            assert alphas == {0.15}, (arm["card"], arm["host"], alphas)
        else:
            # the listed legacy adapter maps no --rule (its bytes stay as they were): card.css's own hairline
            assert alphas <= {0.1, 0.12}, (arm["card"], arm["host"], alphas)


def test_seeded_the_hosts_primary_border_draws_white_lines_in_dark(tmp_path, monkeypatch):
    _browser()

    def seed(html: str) -> str:
        for anchor in SEED_ANCHORS:
            assert html.count(anchor) == 1, f"the seed's anchor moved: {anchor!r}"
            html = html.replace(anchor, anchor.replace("var(--rule)", "var(--line)"))
        return html

    arms = _draw(tmp_path, _cards(monkeypatch, seed))
    for arm in arms:
        if arm["host"] == "dark":
            assert white_lines(arm), (arm["card"], arm["width"])  # red
        else:
            assert white_lines(arm) == [], (
                arm["card"],
                arm["width"],
            )  # control: a dark line, unchanged in kind
