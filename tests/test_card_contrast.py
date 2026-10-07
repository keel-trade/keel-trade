"""Every card colour clears WCAG AA on every ground a card actually lands on.

Both rulebooks make contrast the one colour rule that genuinely binds us:

* OpenAI Apps SDK — "Text and background must maintain a minimum contrast
  ratio (WCAG AA)."
* Claude design guidelines, audit row 52 — WCAG AA, and colour is never the
  sole carrier of meaning.

The trap this guard exists for, found 2026-09-22 while taking the palette back
from the host: Keel's own grounds are DARKER than the hosts we ship to. The
app's `--accent-light` (#7c75ff) measures 4.60:1 on Keel's `--bg-deep` and
passes, then **4.10:1 on claude.ai's #1f1e1d and fails**. A palette checked
only against our own product ships a sub-AA accent to the host that matters
most. So the grounds below are the HOSTS', not ours.

`--tile` is translucent by design (the card's `--bg` is transparent by
contract, so an opaque surface assumes a ground it cannot know). It is
therefore composited over each ground before measuring, which is what a
reader's eye actually sees.

Proof it can fail: set `--accent: #7c75ff` in the dark blocks of card.css and
this reds naming claude-dark at 4.10:1. Proof it is not vacuous: the ground
list and the token list are both asserted non-empty, and every token parsed
out of card.css must be measured by some case.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import card_palette


CSS = Path(__file__).resolve().parents[1] / "keel" / "widgets" / "assets" / "card.css"

#: The grounds a card is actually painted on. Keel's own are included so the
#: in-app panel is covered, but the HOSTS are the ones that bind.
GROUNDS = {
    "claude-dark": "#1f1e1d",
    "claude-light": "#faf9f5",
    "chatgpt-dark": "#212121",
    "chatgpt-light": "#ffffff",
    "keel-dark": "#0d1421",
}

#: WCAG AA for body text.
AA = 4.5


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(_hex_to_rgb(a)), _relative_luminance(_hex_to_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def composite(rgba: str, ground: str) -> str:
    """Flatten an `rgba(r, g, b, a)` wash onto an opaque ground."""
    m = re.match(r"rgba\(\s*([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)[, /]+([\d.]+)\s*\)", rgba)
    assert m, f"not an rgba() value: {rgba}"
    r, g, b, a = (float(x) for x in m.groups())
    gr, gg, gb = _hex_to_rgb(ground)
    return "#%02x%02x%02x" % (
        round(r * a + gr * (1 - a)),
        round(g * a + gg * (1 - a)),
        round(b * a + gb * (1 - a)),
    )


PALETTES = card_palette.resolved_palettes()

#: Every (palette, ground) pair a card actually lands on. A palette is keyed by
#: theme AND by how the host expresses it: a host that stamps no `data-theme`
#: resolves card.css's `prefers-color-scheme` block, which is the default and
#: therefore the common case. Measuring only the stamped pair left it dark.
CASES = [
    (theme, how, ground)
    for theme, how in PALETTES
    for ground in (
        ("claude-dark", "chatgpt-dark", "keel-dark")
        if theme == "dark"
        else ("claude-light", "chatgpt-light")
    )
]

#: Ink tokens that carry text or a glyph and therefore need AA.
INK = ("fg", "muted", "accent", "good", "bad")


@pytest.mark.parametrize("theme,how,ground_name", CASES)
def test_every_ink_clears_aa_on_the_ground(theme: str, how: str, ground_name: str) -> None:
    ground = GROUNDS[ground_name]
    tokens = PALETTES[(theme, how)]
    failures = []
    for name in INK:
        value = tokens.get(name)
        assert value, f"card.css has no --{name} in the {theme} block"
        ratio = contrast(value, ground)
        if ratio < AA:
            failures.append(
                f"--{name} {value} on {ground_name} {ground} ({theme}/{how}): {ratio:.2f}:1"
            )
    assert not failures, "below WCAG AA:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("theme,how,ground_name", CASES)
def test_every_ink_clears_aa_on_the_raised_tile(theme: str, how: str, ground_name: str) -> None:
    """The tile is translucent, so the surface depends on the ground."""
    ground = GROUNDS[ground_name]
    tokens = PALETTES[(theme, how)]
    tile = tokens.get("tile")
    assert tile, f"card.css has no --tile in the {theme} block"
    surface = composite(tile, ground) if tile.startswith("rgba(") else tile
    failures = []
    for name in INK:
        ratio = contrast(tokens[name], surface)
        if ratio < AA:
            failures.append(
                f"--{name} {tokens[name]} on the tile over {ground_name} ({surface}, {theme}/{how}): {ratio:.2f}:1"
            )
    assert not failures, "below WCAG AA on the raised surface:\n  " + "\n  ".join(failures)


def test_the_guard_is_not_vacuous() -> None:
    """Read from the tree, so no palette edit can quietly empty this."""
    assert len(GROUNDS) >= 5, GROUNDS
    assert len(CASES) >= 10, CASES
    # All four declaration sites must be measured, the default ones included.
    assert {(t, h) for t, h, _ in CASES} == set(PALETTES), CASES
    for key, tokens in PALETTES.items():
        for name in INK + ("tile",):
            assert tokens.get(name), f"--{name} missing from the {key} palette"
    # The two themes must actually differ, or one of them is not a theme.
    assert PALETTES[("light", "attr")]["fg"] != PALETTES[("dark", "attr")]["fg"]
    assert PALETTES[("light", "attr")]["accent"] != PALETTES[("dark", "attr")]["accent"]
    # And the maths must discriminate: black on white is the known extreme.
    assert contrast("#000000", "#ffffff") > 20
    assert contrast("#777777", "#7a7a7a") < 1.2
