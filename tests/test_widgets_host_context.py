"""One host context, one owner, for every card capture (Q-1718).

Card review is done by looking at PNGs, and the render-cadence work treats
the measured heights as a budget. Two harnesses screenshot the same cards —
``scripts/card_preview.mjs`` (the sanctioned per-lane tool, whose header
invites any lane to pick it up) and ``tests/fixtures/cards/c_cadence_check.mjs``
(the cadence guard, which also writes shots) — and until 2026-09-22 each
declared its own ``hostContext``. They disagreed in two ways that both change
the pixels:

* ``availableDisplayModes`` — ``card_preview.mjs`` declared none, so
  ``host-adapter.js``'s ``hostOffersFullscreen()`` was false and the card never
  drew its **Expand** button. Measured across the real-envelope lane's 14
  cases: Expand appears in 13 (+1 laid-out box) and moves the HEIGHT where it
  wraps (``strategy-dryrun-open`` 374 → 390 px at 720, 470 → 494 px at 390).
* the style variables — eight against five. The three missing ones are exactly
  what ``TOKEN_MAP`` reads for ``--good`` and ``--bad``, so every coloured
  number in a cadence shot was card.css's fallback rather than the host's.

So a lane picking up either tool captured a different card than the other, and
the two sets of PNGs could not be compared.

These assertions are on the SOURCE rather than on a render, deliberately: the
defect is a second DECLARATION, and a rendered capture cannot tell you how many
declarations produced it. They also run everywhere — the DOM guards in
``test_widgets_cadence.py`` skip without node, which is the environment the CI
lane actually has.

Proof each can fail: every assertion below carries a ``SEED:`` comment naming
the one-line edit that reds it; they were run on 2026-09-22 and reversed.
Proof they are not vacuous: the owner's declaration is checked against
``host-adapter.js``'s live ``TOKEN_MAP`` — every colour token the adapter maps
must be supplied, in BOTH themes, with the same key set. A stub that declared
``{}`` would satisfy "nobody else declares a palette" and fails this.
"""

from __future__ import annotations

import pathlib
import re


HERE = pathlib.Path(__file__).resolve().parent
SDK = HERE.parent
ASSETS = SDK / "keel" / "widgets" / "assets"

#: The one owner.
OWNER = SDK / "scripts" / "card_host_context.mjs"

#: The harnesses that SCREENSHOT cards. `c_card_check.mjs` is deliberately not
#: here: it takes no shots, and its whole job includes asserting "Expand only
#: where the host lists fullscreen", so it varies `modes` per arm. It still
#: takes the constant from the owner rather than re-declaring the context.
CAPTURE_HARNESSES = (
    SDK / "scripts" / "card_preview.mjs",
    SDK / "tests" / "fixtures" / "cards" / "c_cadence_check.mjs",
)

#: A harness declaring its own palette writes one of these names as a literal.
_PALETTE_LITERAL = re.compile(r'"--color-(?:background|text|border)-[a-z]+"\s*:')
#: …and its own mode list looks like this.
_MODES_LITERAL = re.compile(r"availableDisplayModes\s*:\s*\[")


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def test_the_host_context_has_exactly_one_owner() -> None:
    """Neither capture harness declares a palette or a mode list of its own.

    SEED: paste the old `const VARS = {dark: {"--color-background-primary":
    "#262522", ...}}` block back into `card_preview.mjs` — this reds naming
    that file.
    SEED: put `availableDisplayModes: ["inline"]` back into
    `c_cadence_check.mjs`'s `ui/initialize` reply — same.
    """
    owner = _read(OWNER)
    # Not vacuous: the owner really does declare both things, so their
    # absence from the harnesses below is single-ownership rather than
    # nobody declaring anything.
    assert _PALETTE_LITERAL.search(owner), f"{OWNER.name} declares no palette"
    assert '"inline"' in owner and '"fullscreen"' in owner

    for harness in CAPTURE_HARNESSES:
        src = _read(harness)
        assert "card_host_context.mjs" in src, (
            f"{harness.name} does not import the shared host context"
        )
        palette = _PALETTE_LITERAL.findall(src)
        assert not palette, f"{harness.name} declares its own palette: {palette}"
        modes = _MODES_LITERAL.findall(src)
        assert not modes, f"{harness.name} declares its own availableDisplayModes"


def _token_map_colour_candidates() -> dict[str, list[str]]:
    """`{card token: [host var names]}` for every COLOUR token the adapter maps.

    Read out of the shipped `host-adapter.js`, never typed here — the point of
    the check is that the declaration keeps up with the adapter.
    """
    src = _read(ASSETS / "host-adapter.js")
    body = re.search(r"var TOKEN_MAP = \{(.*?)\n  \};", src, re.S)
    assert body, "host-adapter.js no longer declares TOKEN_MAP"
    out: dict[str, list[str]] = {}
    for token, names in re.findall(r'"(--[a-z-]+)":\s*\[([^\]]*)\]', body.group(1)):
        candidates = re.findall(r'"(--[a-z-]+)"', names)
        colour = [n for n in candidates if n.startswith("--color-")]
        if colour:
            out[token] = colour
    return out


def test_the_declared_palette_covers_every_colour_token_the_adapter_maps() -> None:
    """The non-vacuity arm: the shared context is a real host, not a stub.

    A harness that hands the card an empty `styles.variables` captures
    card.css's fallback palette rather than the host's, which is exactly the
    difference the cadence shots carried for `--good` / `--bad`. So every
    colour token `host-adapter.js` knows how to read must be supplied, in both
    themes, by the same key set.

    SEED: delete `"--color-text-success"` from BOTH themes in
    `card_host_context.mjs` — this reds naming `--good`.
    SEED: delete it from `dark` only — this reds on the key-set comparison.
    """
    owner = _read(OWNER)
    themes = {}
    for theme in ("dark", "light"):
        block = re.search(rf"\n  {theme}: \{{(.*?)\n  \}},", owner, re.S)
        assert block, f"card_host_context.mjs declares no {theme} palette"
        themes[theme] = set(re.findall(r'"(--[a-z-]+)":', block.group(1)))

    assert themes["dark"] == themes["light"], (
        f"the two themes declare different variables: {themes['dark'] ^ themes['light']}"
    )
    # Not vacuous, and nothing in a harness can move it: the token list comes
    # from the shipped adapter.
    mapped = _token_map_colour_candidates()
    assert len(mapped) >= 5, f"only {len(mapped)} colour tokens parsed: {mapped}"

    for token, candidates in sorted(mapped.items()):
        for theme, declared in themes.items():
            assert declared & set(candidates), (
                f"{theme}: nothing supplies {token} "
                f"(adapter reads {candidates}, context declares {sorted(declared)})"
            )
