"""CARD-FINAL B4: a chart series with a colour slot draws in its legend's colour.

card-study.js sets a series' ``stroke`` (and its band's ``fill``) as SVG attributes from its slot ``c``. A CSS class
rule outranks a presentation attribute, so ``.st-line { stroke: var(--accent) }`` drew every line of an Entries Curve
(yours, random) and a Compare in the accent while the legend showed two colours. The accent is now only for a series
without a slot (``:not([stroke])``). Seen as a user: fx-study-ent_hi at 760 draws yours violet and random teal.

SEED (reverse the edit): put ``stroke: var(--accent, currentColor);`` back in the bare ``.st-line`` rule -> red.
Control: the slot-less rule still gives the accent, and the reference line keeps its muted dashes.
"""

from __future__ import annotations

import re
from pathlib import Path


CSS = (
    Path(__file__).resolve().parents[1] / "keel" / "widgets" / "assets" / "card-study.css"
).read_text()


def _rule(selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", CSS)
    assert m, selector  # non-vacuity: the rule exists
    return m.group(1)


def test_a_coloured_series_is_not_overridden_by_the_class() -> None:
    assert "stroke:" not in _rule(".st-line").replace("stroke-width", "").replace(
        "stroke-linejoin", ""
    )
    assert "fill:" not in _rule(".st-band")


def test_control_a_series_without_a_slot_keeps_the_accent_and_a_reference_its_dashes() -> None:
    assert "var(--accent" in _rule(".st-line:not([stroke])")
    assert "var(--accent" in _rule(".st-band:not([fill])")
    assert "stroke-dasharray" in _rule(".st-line.st-ref")
