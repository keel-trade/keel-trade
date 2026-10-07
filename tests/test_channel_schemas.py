"""Schemas match the channel table (spec 02 §2.8, guard G5).

Each kind's declared output schema lists, at the top level, EXACTLY its
`structuredContent` home (`_channels.CHANNEL_MAP[kind].structured`) plus the
shared error set — two owners of one list, kept in step by this test. A new
top-level field is declared in both, or this reds.

SEED (run 2026-09-23, reverted by reversing the edit): add a property to the
backtest schema only (`"zz_new": _STR` in `_output_schemas._BACKTEST_PROPS`)
— `test_each_kind_schema_is_its_structured_home` reds for `backtest` while
the other kinds stay green.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes._channels import CHANNEL_MAP, ERROR_FIELDS
from keel.tools.outcomes._output_schemas import KIND_SCHEMAS


#: Per-kind structured counts as declared (spec 02 §4 G5's non-vacuity).
MINIMUM_STRUCTURED = {"backtest": 27, "comparison": 13, "strategy": 26, "status": 17}


def _top_level(paths: frozenset[str]) -> set[str]:
    return {p.split(".")[0].removesuffix("[]") for p in paths}


def test_the_sets_are_not_vacuous() -> None:
    assert len(ERROR_FIELDS) >= 16
    for kind, minimum in MINIMUM_STRUCTURED.items():
        assert len(CHANNEL_MAP[kind].structured) >= minimum, kind


@pytest.mark.parametrize("kind", sorted(KIND_SCHEMAS))
def test_each_kind_schema_is_its_structured_home(kind: str) -> None:
    declared = set(KIND_SCHEMAS[kind]["properties"])
    expected = _top_level(CHANNEL_MAP[kind].structured) | ERROR_FIELDS
    assert declared == expected, {
        "schema only": sorted(declared - expected),
        "table only": sorted(expected - declared),
    }


def test_no_card_or_drop_row_is_declared() -> None:
    """A card/drop-only top-level field never appears in a schema — the
    schema is the model's contract, not the card's."""
    for kind, schema in KIND_SCHEMAS.items():
        spec = CHANNEL_MAP[kind]
        card_only = {p for p in spec.card | spec.drop if "." not in p}
        assert not (card_only & set(schema["properties"])), (kind, card_only)
