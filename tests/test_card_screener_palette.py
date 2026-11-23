"""The screen card's identity colours are the face's validated palette (Q-7922; D-420a, D-420c).

Until 2026-11-22 the card ranked coins through S1's 24 `CAT` colours, pinned here by NORMAL-vision delta E only;
CARD-RESEARCH's validator measured ranks 1 and 2 (light) at a colour-blind delta E of 1.9 and ranks 2 and 3 (dark) at
1.4. The card now colours ranks 1-5 from ``KeelHost.palette().ids`` (host-adapter.js; the accent first), the one
palette the study card shares, and every other coin is the grey field.

This module ports the dataviz skill's validator checks the palette was chosen with (Machado 2009 CVD at severity 1,
OKLab delta E x100, CARD-RESEARCH RESEARCH.md §5) and holds the five identity slots the card draws to them, per theme: every adjacent
pair >= 8 for protan and deutan readers, >= 15 for normal vision, and every slot >= 3:1 (WCAG 1.4.11) against each
host's ground for that theme. The card and its stylesheet carry no colour of their own for an identity.

Proof it can fail (in this file): the same checks over S1's ranks 1-2 light (#7c75ff, #df4df9) and 2-3 dark
(#f0abfc, #7dd3fc) red; control: the validated palette green under the same function.
Not vacuous: 5 slots parsed per theme from host-adapter.js, and the card reads them (`H.palette().ids`).
"""

from __future__ import annotations

import math
import re
from pathlib import Path


SDK = Path(__file__).resolve().parents[1]
ASSETS = SDK / "keel" / "widgets" / "assets"
#: The hosts' grounds per theme: Claude's primary and secondary backgrounds, ChatGPT's reference dark.
GROUNDS = {
    "light": ["#ffffff", "#f5f4ed", "#f5f4f0"],
    "dark": ["#30302e", "#262624", "#262522", "#212121"],
}
#: The ranks the card colours (card-screener.js `IDS`); the palette's sixth slot (violet) holds 2.6:1 on Claude's
#: dark ground, so ranks past five are ink grey.
IDS = 5
MACHADO = {
    "protan": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deutan": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
}


def _lin(h: str) -> list[float]:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return [ch(int(h[i : i + 2], 16) / 255) for i in (1, 3, 5)]


def _oklab(rgb: list[float]) -> tuple[float, float, float]:
    r, g, b = rgb
    lc = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    mc = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    sc = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc,
        1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc,
        0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc,
    )


def _simulate(h: str, kind: str) -> list[float]:
    r, g, b = _lin(h)
    return [max(0.0, min(1.0, row[0] * r + row[1] * g + row[2] * b)) for row in MACHADO[kind]]


def delta_e(h1: str, h2: str, kind: str | None = None) -> float:
    a = _oklab(_simulate(h1, kind) if kind else _lin(h1))
    b = _oklab(_simulate(h2, kind) if kind else _lin(h2))
    return 100 * math.dist(a, b)


def _lum(h: str) -> float:
    r, g, b = _lin(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(h1: str, h2: str) -> float:
    x, y = sorted((_lum(h1), _lum(h2)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def problems(slots: list[str], theme: str) -> list[str]:
    bad = []
    for a, b in zip(slots, slots[1:], strict=False):
        cvd = min(delta_e(a, b, "protan"), delta_e(a, b, "deutan"))
        if cvd < 8:
            bad.append(f"{a}/{b} colour-blind dE {cvd:.1f} < 8")
        if delta_e(a, b) < 15:
            bad.append(f"{a}/{b} normal dE {delta_e(a, b):.1f} < 15")
    for s in slots:
        for g in GROUNDS[theme]:
            if contrast(s, g) < 3:
                bad.append(f"{s} on {g} {contrast(s, g):.2f}:1 < 3")
    return bad


def identity_slots() -> dict[str, list[str]]:
    """The card's identity order per theme: host-adapter.js PALETTE[theme].cat with the accent (its last) first."""
    src = (ASSETS / "host-adapter.js").read_text()
    out = {}
    for theme in ("light", "dark"):
        block = re.search(theme + r":\s*\{\s*cat:\s*\[([^\]]*)\]", src)
        assert block, f"host-adapter.js PALETTE.{theme}.cat moved"
        cat = re.findall(r'"(#[0-9a-f]{6})"', block.group(1))
        out[theme] = [cat[-1], *cat[:-1]][:IDS]
    return out


def test_the_scan_is_not_vacuous() -> None:
    slots = identity_slots()
    assert {t: len(s) for t, s in slots.items()} == {"light": IDS, "dark": IDS}
    card = (ASSETS / "card-screener.js").read_text()
    assert "H.palette().ids" in card and "--sc-id-" in card
    assert f"var IDS = {IDS};" in card


def test_the_identity_slots_pass_the_validator_per_theme() -> None:
    for theme, slots in identity_slots().items():
        assert problems(slots, theme) == [], (theme, problems(slots, theme))


def test_seeded_s1_neighbours_fail_and_the_face_palette_is_the_control() -> None:
    """The palette the card drew before Q-7922: ranks 1-2 light and 2-3 dark, as RANK_ORDER assigned them."""
    assert any("colour-blind" in p for p in problems(["#7c75ff", "#df4df9"], "light"))
    assert any("colour-blind" in p for p in problems(["#f0abfc", "#7dd3fc"], "dark"))
    for theme, slots in identity_slots().items():
        assert problems(slots[:2], theme) == []


def test_the_card_carries_no_identity_colour_of_its_own() -> None:
    """Every identity stroke and fill is `var(--sc-id-N)`, written from the palette; the stylesheet's only colours
    are the host-ink greys of the field."""
    js = (ASSETS / "card-screener.js").read_text()
    css = (ASSETS / "card-screener.css").read_text()
    assert re.findall(r"#[0-9a-fA-F]{6}\b", js) == []
    assert re.findall(r"#[0-9a-fA-F]{3,6}\b", css) == []
