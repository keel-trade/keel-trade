"""How big a resolved universe is allowed to be — the ONE model both gates use.

Two count-bound gates exist and must never drift (F-003, decision D-11):

  - ``services/keel-api/src/utils/universe_validation.py``
    (``assert_universe_resolved`` — the submit-time 422 on deploy/backtest);
  - ``libs/pipeline_engine/dsl/validator.py`` pass 9 (``STALE_UNIVERSE``,
    promoted to error under ``production_mode``).

Both previously carried their own copy of ``top_n - exclusions .. top_n +
inclusions``. That arithmetic was written for CRITERIA DRIFT (``top_n`` edited
after a resolve) BEFORE ``volume_quartiles`` and the dollar-volume floor
(``min_trailing_dollar_volume``, deprecated alias ``min_trailing_notional_proxy``)
existed. Those two are FILTERS: they select a sub-pool and ``top_n`` then caps
it, so a resolved list legitimately smaller than ``top_n`` is the normal
outcome, not staleness. ``UniverseSpec.min_trailing_dollar_volume`` documents
the floor exactly that way ("never a top-N, so the result size varies"), and
``db.queries.universe_resolver.resolve_universe`` slices ``[:top_n]`` AFTER the
quartile bands are cut. The gates read neither field, so "Top 25% + top_n 50"
resolved to 42 names and then refused every submit with a remediation
(re-resolve) that reproduces the same 42 forever.

The rule, stated once:

  - ``top_n`` is the SOLE selector (no quartile filter, no notional floor):
    ``max(0, top_n - len(exclusions)) <= count <= top_n + len(inclusions)`` —
    unchanged, this is the drift check and it still works.
  - A filter is declared: ``1 <= count <= top_n + len(inclusions)``. The lower
    bound drops to 1 because the filter's own yield is unknown here; a resolved
    list that is EMPTY stays the separate ``EMPTY_UNIVERSE`` case (both gates
    check it before this one), so 1 is not a hole.

``reason`` is non-None exactly when the lower bound was dropped, and names the
filter that did it so the gate's message can say why.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


__all__ = [
    "ALL_QUARTILES",
    "MANUAL_BASKET_REMEDY",
    "ResolvedBounds",
    "declared_size_filters",
    "expected_resolved_bounds",
    "stale_count_remedy",
    "top_n_under_supplied",
]

#: All four quartiles selected is NO filter — the resolver skips the band cut
#: on exactly this condition (``set(volume_quartiles) != {"q1","q2","q3","q4"}``
#: in ``resolve_universe``), so the expectation model must agree with it.
ALL_QUARTILES = frozenset({"q1", "q2", "q3", "q4"})


class ResolvedBounds(NamedTuple):
    """Inclusive bounds on ``len(resolved)``, plus why the lower one is what it is.

    ``reason`` is None when ``top_n`` is a TARGET (the drift model); a sentence
    naming the declared filter(s) when ``top_n`` is only a CAP.
    """

    lower: int
    upper: int
    reason: str | None


def declared_size_filters(
    volume_quartiles: Sequence[str] | None,
    min_trailing_dollar_volume: float | None,
) -> tuple[str, ...]:
    """Name each declared criterion that can shrink the pool below ``top_n``.

    Empty tuple ⇒ ``top_n`` is the sole selector. The quartile test mirrors the
    resolver's: an empty list and the all-four set are both "no filter".
    """
    filters: list[str] = []
    if volume_quartiles and set(volume_quartiles) != ALL_QUARTILES:
        filters.append("a volume-quartile filter")
    if min_trailing_dollar_volume is not None:
        filters.append("a trailing dollar-volume floor")
    return tuple(filters)


def expected_resolved_bounds(
    mode: str | None,
    top_n: int | None,
    exclusions: Sequence[str] | None = None,
    inclusions: Sequence[str] | None = None,
    volume_quartiles: Sequence[str] | None = None,
    min_trailing_dollar_volume: float | None = None,
) -> ResolvedBounds | None:
    """Bounds on the resolved count, or None when no count bound applies.

    None (the gate makes no count claim) for every mode but ``top_volume``, and
    for ``top_volume`` without a declared ``top_n`` — ``mode='category'`` and a
    pure quartile selection have no declared size at all.

    Args:
        mode: The Universe mode (``manual`` / ``category`` / ``top_volume``).
        top_n: The declared cap/target.
        exclusions: Declared exclusions — each can remove one name the ranked
            pool supplied, which is why they lower the TARGET's lower bound.
        inclusions: Declared force-includes — each can add one name on top of
            ``top_n``, in every configuration.
        volume_quartiles: Declared quartile bands (see :data:`ALL_QUARTILES`).
        min_trailing_dollar_volume: Declared dollar-liquidity floor, whichever
            name declared it (``spec.dollar_volume_floor``).
    """
    if mode != "top_volume" or top_n is None:
        return None

    inclusion_count = len(inclusions or [])
    exclusion_count = len(exclusions or [])
    upper = top_n + inclusion_count

    filters = declared_size_filters(volume_quartiles, min_trailing_dollar_volume)
    if filters:
        # A filter is declared: top_n is a CAP. Any non-empty count up to the
        # cap (plus force-includes) is legitimate; EMPTY is its own diagnosis.
        joined = " and ".join(filters)
        return ResolvedBounds(
            lower=1,
            upper=upper,
            reason=(
                f"top_n is a cap under {joined}, not a target — the filter can "
                "legitimately select fewer than top_n assets, so only the upper "
                "bound applies here"
            ),
        )

    # top_n is the sole selector: the original drift model, unchanged.
    return ResolvedBounds(lower=max(0, top_n - exclusion_count), upper=upper, reason=None)


#: The remedy that is always correct for a user who wants to choose coins.
MANUAL_BASKET_REMEDY = (
    "If you edited this list by hand: a top-volume list is computed by the resolver, "
    "not typed — to trade coins you choose, declare them as a manual basket: "
    'Universe(mode="manual", symbols=[…]).'
)


def stale_count_remedy(
    actual: int, bounds: ResolvedBounds, top_n: int, *, re_resolve: str = "re-resolve"
) -> str:
    """What to do about a top_volume list whose COUNT is outside ``bounds`` —
    the ONE wording both count gates use (Q-2434 2a).

    The count arm cannot tell WHY the count is off: the list may have been
    edited by hand (leroylouis, 2026-10-04: ``resolved=["MORPHO"]`` under
    ``top_n=15``), the criteria may have moved since the last resolve, or the
    venue may supply fewer than ``top_n`` — and nothing in the source or blob
    records the venue's supply. So the copy leads with the remedy that is
    always correct for someone choosing coins (a manual basket), names
    re-resolving second, and gives the venue reading only CONDITIONALLY
    ("only if the resolver then reports …"). It never presents "lower top_n"
    as the fix: for a hand edit that is wrong advice.

    Args:
        actual: ``len(resolved)``.
        bounds: The window from :func:`expected_resolved_bounds`.
        top_n: The declared ``top_n``.
        re_resolve: How the caller's surface says "re-resolve" (the validator
            names its tools; the submit gate says it plainly).
    """
    if actual < bounds.lower:
        return (
            f"{MANUAL_BASKET_REMEDY} If you did not, {re_resolve}; only if the resolver then "
            f"reports 'venue can supply N of {top_n}', lower top_n to that count."
        )
    return (
        f"{MANUAL_BASKET_REMEDY} Otherwise the criteria changed since the last resolve — "
        f"{re_resolve} before retrying."
    )


def top_n_under_supplied(
    mode: str | None,
    top_n: int | None,
    supply_before_filters: int | None,
    volume_quartiles: Sequence[str] | None = None,
    min_trailing_dollar_volume: float | None = None,
) -> bool:
    """True only when the VENUE cannot supply ``top_n`` — the write-down test.

    The Q-0408 convention writes ``top_n`` down to what was achieved, so a
    permanently unachievable ``top_n`` cannot fail the count gate forever. It
    was measured against the FINAL list, which made it fire on two shapes it
    was never meant for, and both RATCHET (audit 04 U-02, measured 2026-09-16:
    ``top_n=30, exclusions=["S00"]`` walked 30 → 29 → 28 → 27 over three
    resolves):

    - an exclusion removed a name the venue DID supply;
    - a declared filter (bands/floor) selected fewer — where ``top_n`` is a cap
      by D-11 and writing it down destroys the user's declared intent.

    So: under-supplied iff ``top_n`` is the sole selector AND the ranked pool
    the venue supplied — measured BEFORE bands, floor, exclusions and
    inclusions (``UniverseResolution.supply_before_filters``) — is smaller than
    ``top_n``. An unknown supply (None: nothing measured it) is NOT under-supply;
    a write-down needs evidence, never an inference from a short list.
    """
    if mode != "top_volume" or top_n is None or supply_before_filters is None:
        return False
    if declared_size_filters(volume_quartiles, min_trailing_dollar_volume):
        return False
    return supply_before_filters < top_n
