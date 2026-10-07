"""The comparison card's series palette is legible and distinct (Q-1782).

Light-theme pass on claude.ai (2026-09-22): the baseline read VIOLET (it was
`--accent`, an indigo that reads violet in both themes — "violet first",
where the decided order puts violet LAST), v2's steel-blue sat close to it,
and v3's olive-brown was low-contrast on the light panel. Measured: the light
`--sv-cyan` #0b7fa3 and `--sv-amber` #9a6a12 were 4.35:1 and 4.49:1 on
claude.ai's light ground — under AA. `test_card_contrast.py` never saw them:
its parse reads the ink blocks, not the series blocks.

This reads the series straight out of the two files that own it —
`card-compare.js` (which tokens, in which order) and `card.css` (every site
that declares the series) — and measures every inline series colour on every
host ground of its theme, and every pair of inline colours against each
other (CIE76 ΔE; colour is never the sole carrier, but four curves on one
chart must still be told apart).

Proof it can fail: the seeds named per test (run 2026-09-22, in the commit).
Proof it is not vacuous: the series has six entries, the CSS declares the
series in four sites (two per theme), and every inline token resolves to a
colour in every one of them.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

from . import card_palette
from .test_card_contrast import GROUNDS, contrast


ASSETS = Path(__file__).resolve().parents[1] / "keel" / "widgets" / "assets"
INLINE = 4  # curves drawn inline (card-compare.js INLINE_LINES)
AA = 4.5
MIN_DELTA_E = 30.0


def _series() -> list[str]:
    src = (ASSETS / "card-compare.js").read_text(encoding="utf-8")
    block = re.search(r"var SERIES = \[(.*?)\];", src, re.S)
    assert block, "card-compare.js no longer declares SERIES"
    return re.findall(r'stroke: "var\((--[a-z-]+)\)"', block.group(1))


def _series_sites() -> dict[str, dict[str, str]]:
    """`{theme-site: {token: hex}}` for every block declaring the series,
    plus that theme's ink (`--fg`)."""
    css = (ASSETS / "card.css").read_text(encoding="utf-8")
    ink = card_palette.declared_sites()
    out: dict[str, dict[str, str]] = {}
    for sel, body in re.findall(r"(:root[^{]*)\{([^{}]*--sv-violet[^{}]*)\}", css):
        dark = "dark" in sel or 'not([data-theme="light"])' in sel
        key = ("dark" if dark else "light") + ":" + sel.strip()
        tokens = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", body))
        # The ink tokens a series may point at, resolved for this theme — so
        # a seed that swaps the baseline token still measures, not KeyErrors.
        theme_ink = ink["attr-dark" if dark else "attr-light"]
        tokens["--fg"] = theme_ink["fg"]
        tokens["--accent"] = theme_ink["accent"]
        out[key] = tokens
    return out


def test_the_palette_scan_is_not_vacuous() -> None:
    series = _series()
    assert len(series) == 6, series
    sites = _series_sites()
    assert sum(k.startswith("light") for k in sites) == 2, list(sites)
    assert sum(k.startswith("dark") for k in sites) == 2, list(sites)
    for site, tokens in sites.items():
        for tok in series:
            assert tok in tokens, (site, tok)


def test_the_baseline_is_the_ink_and_violet_is_last() -> None:
    """The baseline is neutral and no series is the accent (it reads
    violet); violet is the last hue."""
    # SEED: put `{ stroke: "var(--accent)", base: true }` back as SERIES[0]
    # in card-compare.js — reds here.
    series = _series()
    assert series[0] == "--fg", series
    assert "--accent" not in series, series
    assert series[-1] == "--sv-violet", series


def test_every_inline_series_colour_clears_aa_on_its_hosts() -> None:
    # SEED: restore the light `--sv-cyan: #0b7fa3` in card.css — reds on
    # claude-light at 4.35:1.
    series = _series()[:INLINE]
    checked = 0
    for site, tokens in _series_sites().items():
        theme = site.split(":")[0]
        for ground_name, ground in GROUNDS.items():
            if theme not in ground_name or ground_name.startswith("keel"):
                continue
            for tok in series:
                ratio = contrast(tokens[tok], ground)
                assert ratio >= AA, f"{site} {tok} {tokens[tok]} on {ground_name}: {ratio:.2f}"
                checked += 1
    assert checked >= 2 * 2 * INLINE * 2


def _lab(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = lin
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(a: str, b: str) -> float:
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b), strict=True)) ** 0.5


def test_the_inline_series_colours_are_told_apart() -> None:
    """Every pair of the four inline colours differs by at least ΔE 30 in
    every site. CONTROL, recorded in the seed: the accent the baseline used
    to be sits within ΔE 15 of violet."""
    # SEED: make SERIES[1] `var(--sv-violet)` and SERIES[0] `var(--accent)`
    # — the accent/violet pair measures ~15 and this reds.
    series = _series()[:INLINE]
    pairs = 0
    for site, tokens in _series_sites().items():
        for a, b in itertools.combinations(series, 2):
            de = _delta_e(tokens[a], tokens[b])
            assert de >= MIN_DELTA_E, f"{site} {a} vs {b}: ΔE {de:.1f}"
            pairs += 1
    assert pairs == 4 * 6
