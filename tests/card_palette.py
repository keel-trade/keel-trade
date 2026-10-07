"""Read card.css's palette from EVERY site that declares it.

Not a test module — the shared parse behind test_card_contrast.py and
test_card_design_tokens.py.

card.css declares each theme twice, because a host may or may not stamp a
theme onto the root element:

* `:root` (bare) + `@media (prefers-color-scheme: dark)` — what a viewer whose
  host sends NO `data-theme` sees. This is the default state, so it is the one
  most viewers actually get.
* `:root[data-theme="light"]` / `:root[data-theme="dark"]` — what a viewer sees
  once a host stamps an explicit choice.

Both guards originally read only the `[data-theme]` pair, which left the
system-default palette — the more-used one — unmeasured. Found 2026-09-22 when
a seeded cyan accent landed in the media-query block and the hue guard passed.
`declared_themes()` returns every site; `assert_sites_agree()` is what keeps
the duplicates from drifting apart, since a divergence means a viewer in system
mode sees a different card than one who toggled.
"""

from __future__ import annotations

import re
from pathlib import Path


CSS = Path(__file__).resolve().parents[1] / "keel" / "widgets" / "assets" / "card.css"

#: Tokens that carry a colour. Type-scale and radius tokens are not palette.
_COLOUR = re.compile(r"--([a-z-]+):\s*((?:#[0-9a-fA-F]{3,8})|(?:rgba?\([^)]*\)))\s*;")


def _parse(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _COLOUR.findall(block)}


def _block(css: str, pattern: str) -> str:
    m = re.search(pattern, css, re.S)
    assert m, f"card.css no longer declares a block matching {pattern!r}"
    return m.group(1)


def declared_sites() -> dict[str, dict[str, str]]:
    """`{site name: {token: value}}` for all four declaration sites."""
    css = CSS.read_text(encoding="utf-8")
    return {
        "root-default": _parse(_block(css, r"\n:root \{(.*?)\n\}")),
        "media-dark": _parse(
            _block(
                css,
                r"@media \(prefers-color-scheme: dark\) \{\s*"
                r":root:not\(\[data-theme=\"light\"\]\) \{(.*?)\n  \}",
            )
        ),
        "attr-dark": _parse(_block(css, r':root\[data-theme="dark"\] \{(.*?)\n\}')),
        "attr-light": _parse(_block(css, r':root\[data-theme="light"\] \{(.*?)\n\}')),
    }


#: Which site serves a theme in each of the two host behaviours. These are
#: ALTERNATIVES, not layers: a host that stamps nothing resolves the `system`
#: site and never the `attr` one, and vice versa. The bare `:root` is the base
#: under both, since every other site overrides only what changes.
SITES_FOR = {
    ("light", "system"): (),
    ("light", "attr"): ("attr-light",),
    ("dark", "system"): ("media-dark",),
    ("dark", "attr"): ("attr-dark",),
}


def resolved_palettes() -> dict[tuple[str, str], dict[str, str]]:
    """`{(theme, host behaviour): {token: value}}` — every palette a viewer gets.

    Four entries, because measuring only the `attr` pair leaves the default
    state unmeasured, and the default state is the common one.
    """
    sites = declared_sites()
    out = {}
    for key, overrides in SITES_FOR.items():
        merged = dict(sites["root-default"])
        for name in overrides:
            merged.update(sites[name])
        out[key] = merged
    return out


def sites_disagreeing() -> list[str]:
    """Tokens whose two resolutions of one theme differ.

    A divergence means a viewer in system mode sees a different card than one
    whose host stamped an explicit choice — always a mistake, never a design.
    """
    palettes = resolved_palettes()
    bad = []
    for theme in ("light", "dark"):
        system, attr = palettes[(theme, "system")], palettes[(theme, "attr")]
        for token in sorted(set(system) | set(attr)):
            if system.get(token) != attr.get(token):
                bad.append(
                    f"--{token} resolves to {system.get(token)} for a host that stamps no "
                    f"data-theme but {attr.get(token)} for one that stamps {theme}"
                )
    return bad
