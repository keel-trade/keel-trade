"""SMC trader words find the zone entry signals and the sweep-wick level (research/13, lane P3b).

The bundled registry is searched the way ``keel_components_search`` searches
it. A trader asking for a "short order block" or a "supply zone entry" must
get the signed entry component first, not only the plain-number zone it is
built on, and "stop beyond the sweep wick" must name ``SweepLevel``.
"""

from __future__ import annotations

import pytest
from keel.data import registry as R


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("order block entry", "OrderBlockEntry"),
        ("short order block", "OrderBlockEntry"),
        ("supply zone entry", "OrderBlockEntry"),
        ("demand zone entry", "OrderBlockEntry"),
        ("enter at the 50% of an order block", "OrderBlockEntry"),
        ("fair value gap entry", "FairValueGapEntry"),
        ("breaker retest", "BreakerBlockEntry"),
        ("stop beyond the sweep wick", "SweepLevel"),
        ("sweep wick level", "SweepLevel"),
    ],
)
def test_trader_words_rank_the_component_first(query, first):
    names = [c["name"] for c in R.search_components(query=query, top_k=5)]
    assert names and names[0] == first, names


def test_the_parent_zone_still_ranks_beside_its_entry():
    """Control: the entry does not hide the zone (the stop / target prices)."""
    names = [c["name"] for c in R.search_components(query="order block", top_k=5)]
    assert "OrderBlock" in names and "OrderBlockEntry" in names, names
