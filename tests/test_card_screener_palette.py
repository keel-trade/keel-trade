"""The screen card's palette is keel-app's, byte for byte (INV-53; spec 06 §D3.3).

`card-screener.css` declares `--sc-0…23` in four sites (two per theme, the
`card_palette` rule). The dark sites must equal keel-app's
`screenMarks.categorical` (tokens.ts), which chartTheme exports as
`signalCategorical`: S1's 24 `CAT` colours in S1's order. The light sites
keep that hue order and clear 3:1 (WCAG 1.4.11, graphical objects) on each
light host ground. The card draws only `var(--sc-N)` strokes, never a hex.

Proof it can fail (seeds run in the commit): change one dark `--sc-7` hex in
card-screener.css → the parity test reds; put `#f0abfc` back as light `--sc-14`
→ the light-contrast test reds at 1.83:1; paste a hex stroke into
card-screener.js → the hex test reds.
Not vacuous: 24 colours parsed from tokens.ts, 4 sites × 24 tokens parsed
from card-screener.css, and chartTheme still aliases `screenMarks.categorical`.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

from .test_card_contrast import GROUNDS, contrast
from .test_compare_palette import _delta_e


SDK = Path(__file__).resolve().parents[1]
REPO = SDK.parents[2]
ASSETS = SDK / "keel" / "widgets" / "assets"
TOKENS = REPO / "services" / "keel-app" / "src" / "lib" / "design" / "tokens.ts"
CHART_THEME = REPO / "services" / "keel-app" / "src" / "lib" / "design" / "chartTheme.ts"
N = 24
#: S1 `CAT` (live-design S1 js/util.js:23), the design of record (R-096/R-097).
#: Pinned here too so the card holds S1's palette even while keel-app's copy
#: moves; the app parity arm below keeps the two from drifting apart.
S1_CAT = [
    "#7c75ff", "#25d0c5", "#f0b133", "#60a5fa", "#f472b6", "#a3e635",
    "#0da2e7", "#fb923c", "#3fcf98", "#b992f7", "#db2777", "#9333ea",
    "#14b8a6", "#ca8a04", "#f0abfc", "#86efac", "#65a30d", "#3b82f6",
    "#c2410c", "#7dd3fc", "#d946ef", "#84cc16", "#0891b2", "#e879f9",
]  # fmt: skip


def _hls(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return colorsys.rgb_to_hls(*(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)))


def _app_categorical() -> list[str]:
    theme = CHART_THEME.read_text(encoding="utf-8")
    assert re.search(r"signalCategorical[^=]*=\s*screenMarks\.categorical", theme), (
        "chartTheme.signalCategorical no longer aliases screenMarks.categorical"
    )
    src = TOKENS.read_text(encoding="utf-8")
    block = re.search(r"screenMarks = \{.*?categorical: \[(.*?)\]", src, re.S)
    assert block, "tokens.ts no longer declares screenMarks.categorical"
    return [h.lower() for h in re.findall(r'"(#[0-9a-fA-F]{6})"', block.group(1))]


def _sites() -> dict[str, list[str]]:
    css = (ASSETS / "card-screener.css").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for sel, body in re.findall(r"(:root[^{]*)\{([^{}]*--sc-0:[^{}]*)\}", css):
        dark = "dark" in sel or 'not([data-theme="light"])' in sel
        tokens = dict(re.findall(r"--sc-(\d+):\s*(#[0-9a-fA-F]{6})", body))
        out[("dark" if dark else "light") + ":" + sel.strip()] = [
            tokens.get(str(i), "").lower() for i in range(N)
        ]
    return out


def test_the_scan_is_not_vacuous() -> None:
    assert len(_app_categorical()) == N
    sites = _sites()
    assert sum(k.startswith("dark") for k in sites) == 2, list(sites)
    assert sum(k.startswith("light") for k in sites) == 2, list(sites)
    for site, hexes in sites.items():
        assert all(hexes), (site, hexes)


def test_dark_sites_equal_s1_cat() -> None:
    for site, hexes in _sites().items():
        if site.startswith("dark"):
            assert hexes == S1_CAT, site


def test_dark_sites_equal_the_app_palette_byte_for_byte() -> None:
    app = _app_categorical()
    for site, hexes in _sites().items():
        if site.startswith("dark"):
            assert hexes == app, site


def test_light_sites_keep_the_order_and_clear_three_to_one() -> None:
    app = S1_CAT
    checked = 0
    for site, hexes in _sites().items():
        if not site.startswith("light"):
            continue
        for i, (light, dark) in enumerate(zip(hexes, app, strict=True)):
            # the same hue, only darker: the order holds (HLS hue within 2 %)
            hl, ll, _ = _hls(light)
            hd, ld, _ = _hls(dark)
            assert min(abs(hl - hd), 1 - abs(hl - hd)) < 0.02 and ll <= ld, (site, i, light, dark)
            for name, ground in GROUNDS.items():
                if "light" in name:
                    ratio = contrast(light, ground)
                    assert ratio >= 3.0, f"{site} --sc-{i} {light} on {name}: {ratio:.2f}"
                    checked += 1
        for i in range(N - 1):  # neighbours in rank stay apart
            assert _delta_e(hexes[i], hexes[i + 1]) >= 20, (site, i)
    assert checked == 2 * N * 2


def test_the_card_strokes_only_through_the_palette_tokens() -> None:
    src = (ASSETS / "card-screener.js").read_text(encoding="utf-8")
    assert "var(--sc-" in src
    assert not re.findall(r'"#[0-9a-fA-F]{3,8}"', src), "card-screener.js carries a hex colour"
    assert re.search(r"PALETTE = 24\b", src)


def _rank_order() -> list[int]:
    src = (ASSETS / "card-screener.js").read_text(encoding="utf-8")
    m = re.search(r"RANK_ORDER = \[([\d,\s]+)\]", src)
    assert m, "card-screener.js declares no RANK_ORDER"
    return [int(x) for x in m.group(1).replace("\n", " ").split(",") if x.strip()]


def test_neighbouring_ranks_never_share_a_look() -> None:
    """Q-3635: the founder's XMR (slot 0, #7c75ff) and SAGA (slot 3,
    #3d91f9) were hard to tell apart as ranks 1 and 4. The card assigns
    palette slots to ranks through RANK_ORDER; any two ranks within four of
    each other (the five lines a card shows inline) sit at ΔE ≥ 40 in BOTH
    themes, and the first eight are pairwise ≥ 20.

    Seed: make RANK_ORDER the identity (0..23) → reds on ranks 0 and 3 in
    light at 30.6. Not vacuous: the order must be a permutation of 24."""
    order = _rank_order()
    assert sorted(order) == list(range(N)), order
    assert order[0] == 0, "rank 1 keeps S1's first colour"
    sites = _sites()
    palettes = [sites[k] for k in sites]
    assert len(palettes) == 4

    def apart(i: int, j: int) -> float:
        return min(_delta_e(p[order[i]], p[order[j]]) for p in palettes)

    for i in range(N):
        for j in range(i + 1, min(N, i + 5)):
            assert apart(i, j) >= 40, (i, j, order[i], order[j], apart(i, j))
    for i in range(8):
        for j in range(i + 1, 8):
            assert apart(i, j) >= 20, (i, j, apart(i, j))
    assert apart(0, 3) >= 40 and apart(0, 9) >= 40 and apart(3, 6) >= 40
