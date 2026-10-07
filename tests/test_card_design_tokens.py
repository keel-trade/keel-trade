"""The card's palette stays derived from the app's, and its type stays on a scale.

Two disciplines that a card.css edit can silently break, neither of which the
contrast guard would notice — a palette can clear WCAG AA while being nobody's
brand, and a type ramp can be perfectly legible while being seven ad-hoc sizes.

HUE. The app (`services/keel-app/src/lib/design/tokens.ts`) declares every
colour as an HSL triple, so its hues are read here rather than computed or
copied: surfaces are commented "hue-locked 220", the accent family is 243, and
the semantic inks have one hue each. The card's DARK theme transcribes the app
where contrast allows — `--fg` IS `--text` and `--good` IS `--green`, byte for
byte — and lifts `--accent` and `--bad` in LIGHTNESS where the app's values
fail AA on a host ground (see test_card_contrast.py, which is why they were
lifted). The card's LIGHT theme has nothing to transcribe: the app ships no
light theme. Hue is what survives both operations, so hue is what is asserted —
it is the difference between a derived light theme and an invented one.

TYPE. Every size in the card surface comes from a named `--t-*` step paired
with a line height. Before 2026-09-22 card.css carried 27 size declarations
across 11 sizes, four of them fractional (10.5, 11.5, 12.5, 13.5) and none
with a declared line height, which is most of why the card read as less
considered than keel-site.

Proof these can fail: set `--accent: #4fc3f7` in a card.css theme block and the
hue case reds naming 199 against 243; write `font-size: 12.5px` anywhere in the
assets and the scale case reds naming the file and value. Proof they are not
vacuous: the app's own blocks must parse and be internally hue-consistent
(so a malformed read cannot pass as "no expectations"), every asserted token
must be present in both themes, and the scale case asserts it actually scanned
a non-empty set of files containing a non-zero number of font declarations.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

from . import card_palette


SDK = Path(__file__).resolve().parents[1]
ASSETS = SDK / "keel" / "widgets" / "assets"
CSS = ASSETS / "card.css"
APP_TOKENS = SDK.parents[2] / "services" / "keel-app" / "src" / "lib" / "design" / "tokens.ts"

#: Hue degrees a card token may sit from its app counterpart. Non-zero because
#: the light theme is derived rather than transcribed, and a lightness lift for
#: AA moves hue slightly in sRGB. Measured spread at introduction: 5.
HUE_TOLERANCE = 8


def _app_hues() -> dict[str, int]:
    """`{"--accent": 243, ...}` read from the app's own HSL declarations."""
    src = APP_TOKENS.read_text(encoding="utf-8")
    found = re.findall(r'token\(\s*"(--[a-z0-9-]+)",\s*"(\d+)\s', src)
    assert found, f"no HSL token declarations parsed out of {APP_TOKENS}"
    return {name: int(h) for name, h in found}


APP = _app_hues()


def _hue_of(hex_value: str) -> int:
    v = hex_value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    r, g, b = (int(v[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, _, _ = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360) % 360


def _hue_distance(a: int, b: int) -> int:
    """Circular, so `--bad` at 0 and 359 are one degree apart, not 359."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


PALETTES = card_palette.resolved_palettes()

#: card token -> the app token whose hue family it belongs to.
LINEAGE = {
    "fg": "--text",
    "muted": "--text-secondary",
    "accent": "--accent",
    "good": "--green",
    "bad": "--rose",
}


@pytest.mark.parametrize("palette", sorted(PALETTES))
@pytest.mark.parametrize("card_token,app_token", sorted(LINEAGE.items()))
def test_card_ink_holds_the_app_hue(
    palette: tuple[str, str], card_token: str, app_token: str
) -> None:
    value = PALETTES[palette].get(card_token)
    assert value, f"card.css has no --{card_token} in the {palette} palette"
    expected = APP[app_token]
    actual = _hue_of(value)
    assert _hue_distance(actual, expected) <= HUE_TOLERANCE, (
        f"--{card_token} {value} in the {palette} palette sits at hue {actual}; "
        f"the app's {app_token} is hue {expected}. The card's palette is derived "
        f"from the app's — a new hue means it no longer is."
    )


def test_both_declaration_sites_of_each_theme_agree() -> None:
    """A viewer in system mode and one who toggled must see the same card.

    card.css declares each theme twice — once for a host that stamps no
    `data-theme` (the default, and so the common case) and once for a host that
    stamps one. Nothing but this makes the two stay equal, and the system-mode
    pair is the easier one to forget: a seeded cyan accent in the media block
    passed both guards on 2026-09-22 because neither read it.
    """
    assert not card_palette.sites_disagreeing(), "theme sites disagree:\n  " + "\n  ".join(
        card_palette.sites_disagreeing()
    )


def test_the_app_surfaces_are_actually_hue_locked() -> None:
    """The premise the lineage rests on, asserted rather than assumed."""
    src = APP_TOKENS.read_text(encoding="utf-8")
    block = re.search(r"export const surfaces = \{(.*?)\} as const;", src, re.S)
    assert block, "the app no longer declares a surfaces block"
    hues = {int(h) for h in re.findall(r'token\(\s*"--[a-z-]+",\s*"(\d+)\s', block.group(1))}
    assert len(hues) == 1, f"the app's surfaces are no longer hue-locked: {sorted(hues)}"


def test_the_dark_theme_still_transcribes_the_app_where_contrast_allows() -> None:
    """Two tokens are the app's values byte for byte; a drift there is a decision."""
    app_src = APP_TOKENS.read_text(encoding="utf-8")

    def app_hex(token: str) -> str:
        m = re.search(rf'token\(\s*"{token}",\s*"[^"]+",\s*"(#[0-9a-f]+)"', app_src)
        assert m, f"{token} not found in the app tokens"
        return m.group(1)

    for how in ("system", "attr"):
        assert PALETTES[("dark", how)]["fg"] == app_hex("--text")
        assert PALETTES[("dark", how)]["good"] == app_hex("--green")


# ── Type scale ───────────────────────────────────────────────────────────────

#: Every file that can declare a font size on the card surface.
TYPE_FILES = [CSS] + sorted(ASSETS.glob("card-*.js"))

RAW_FONT_SIZE = re.compile(r"font(?:-size)?:\s*[^;}\"']*?[0-9.]+px")


@pytest.mark.parametrize("path", TYPE_FILES, ids=lambda p: p.name)
def test_no_raw_pixel_font_sizes(path: Path) -> None:
    raw = RAW_FONT_SIZE.findall(path.read_text(encoding="utf-8"))
    assert not raw, (
        f"{path.name} declares {len(raw)} font size(s) off the scale: {raw[:5]}. "
        f"Use a --t-* step from card.css; add one there if none fits."
    )


def test_every_size_step_is_paired_with_a_line_height() -> None:
    css = CSS.read_text(encoding="utf-8")
    steps = {m for m in re.findall(r"--t-([a-z0-9-]+):\s*[0-9.]+px", css) if not m.endswith("-lh")}
    assert steps, "card.css declares no type scale at all"
    missing = [s for s in sorted(steps) if f"--t-{s}-lh:" not in css]
    assert not missing, f"type steps with no paired line height: {missing}"


def test_the_type_guard_is_not_vacuous() -> None:
    """A scan of nothing, or of files with no type in them, proves nothing."""
    assert len(TYPE_FILES) >= 5, [p.name for p in TYPE_FILES]
    declarations = sum(
        len(re.findall(r"font(?:-size)?:", p.read_text(encoding="utf-8"))) for p in TYPE_FILES
    )
    assert declarations >= 25, f"only {declarations} font declarations scanned"
    # And the matcher must actually recognise what it forbids.
    assert RAW_FONT_SIZE.search("font-size: 12.5px;")
    assert RAW_FONT_SIZE.search("font: 10.5px var(--font);")
    assert not RAW_FONT_SIZE.search("font-size: var(--t-xs);")
