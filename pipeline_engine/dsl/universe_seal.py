"""The resolution seal on a computed Universe list (Q-2434, founder decision 2026-10-07: 2b).

A ``top_volume`` / ``category`` Universe's ``resolved`` list is the resolver's
answer to the declared criteria at ``resolved_at``. Until this module nothing
recorded that it IS that answer, so a list typed by hand read exactly like a
resolved one: the gates checked only its length, and a same-count swap ran
under the "top 15 by volume" label (leroylouis, prod 2026-10-04,
``btr_01m44acyfyvmk78q4bppfkzgvm``).

The seal is one more Universe argument, written by the writers that RESOLVE
(never by a text edit)::

    Universe(mode="top_volume", top_n=15, resolved=[...], resolved_at="...",
             resolution="u1:<C>.<T>.<L>")

- ``C`` (12 hex) — the criteria the resolver read: mode, market, symbols,
  categories, top_n, exclusions, inclusions, lookback, volume_quartiles and the
  dollar-volume floor (either spelling). Set-valued criteria are compared as
  sets (sorted), so a writer that reorders an exclusion list does not break it.
  ``groups`` is not a resolver input and is not sealed.
- ``T`` (8 hex) — the ``resolved_at`` instant the resolution answers for.
- ``L`` (12 hex) — the list's MEMBERS (sorted; members are what trades), bound
  to ``C`` and ``resolved_at``.

The verdict (:func:`seal_verdict`) reads the three parts separately, so a
refusal can say WHICH part moved:

==================  =====================================================
``unsealed``        no seal — legacy lists, library entries imported before
                    the seal, lists written by a surface that does not seal.
                    Today's count check applies, unchanged (grandfathered).
``valid``           the list is the resolution it claims; no count heuristics.
``superseded``      ``resolved_at`` moved: a later resolve rewrote the list
                    without resealing (an older client). The seal no longer
                    speaks for the list — treated exactly as ``unsealed``.
``criteria_moved``  same instant, different criteria: the list answers the
                    old criteria → STALE_UNIVERSE, "re-resolve".
``list_edited``     same criteria and instant, different members: the list
                    was edited by hand → STALE_UNIVERSE with the manual-basket
                    remedy (the leroylouis swap).
``malformed``       the argument is not a seal Keel writes → STALE_UNIVERSE.
==================  =====================================================

The seal is an unkeyed digest: it stops a list from being MISLABELLED by
accident (a hand edit, an agent that rewrote the list), not a determined
forger who recomputes it. Deleting the argument returns a list to the
grandfathered count check, which is what every list had before.

ONE owner of the computation and the refusal wording: the DSL validator
(pass 9) and keel-api's submit/deploy gate both call :func:`seal_verdict` and
:func:`seal_refusal_detail`. The TypeScript twin
(``services/keel-app/src/lib/strategy/universe-seal.ts``) is pinned to this
module by the cross-language vectors in ``fixtures/universe_seal_vectors.json``
(:func:`write_seal_vectors`, freshness-tested).

Pure stdlib (hashlib/json/re) plus ``dsl.spec`` — vendored into the SDK.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


#: Version prefix of every seal this module writes.
SEAL_PREFIX = "u1:"

#: The modes whose ``resolved`` list the resolver COMPUTES from criteria —
#: the only modes a writer seals. A manual basket has its own name check.
SEALED_MODES: frozenset[str] = frozenset({"top_volume", "category"})

_SEAL_RE = re.compile(r"^u1:([0-9a-f]{12})\.([0-9a-f]{8})\.([0-9a-f]{12})$")

#: Verdicts (see the module docstring).
UNSEALED = "unsealed"
VALID = "valid"
SUPERSEDED = "superseded"
CRITERIA_MOVED = "criteria_moved"
LIST_EDITED = "list_edited"
MALFORMED = "malformed"

#: Verdicts under which the seal does not speak and the count check applies.
COUNT_CHECK_VERDICTS: frozenset[str] = frozenset({UNSEALED, SUPERSEDED})
#: Verdicts that refuse (STALE_UNIVERSE).
REFUSING_VERDICTS: frozenset[str] = frozenset({CRITERIA_MOVED, LIST_EDITED, MALFORMED})


def _get(declaration: object, name: str) -> Any:
    if isinstance(declaration, dict):
        return declaration.get(name)
    return getattr(declaration, name, None)


def _number(value: Any) -> Any:
    """Integral numbers as ints, so Python ``1e6`` and JS ``1000000`` agree."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _sorted_or_none(value: Any) -> Any:
    """A set-valued criterion, sorted; an EMPTY list is the same as absent —
    the compiled blob omits empty lists, and a seal must read the same on the
    source and on the blob."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return sorted(str(v) for v in value) or None
    return value


def _floor(declaration: object) -> Any:
    new = _get(declaration, "min_trailing_dollar_volume")
    old = _get(declaration, "min_trailing_notional_proxy")
    if new is not None and old is not None:
        # Ambiguous (INVALID_UNIVERSE elsewhere) — keep both so it never
        # equals either single spelling.
        return [_number(new), _number(old)]
    return _number(new if new is not None else old)


def seal_criteria(declaration: object) -> list[Any]:
    """The criteria the seal covers, as the canonical positional array.

    Works on a ``UniverseSpec``, a compiled blob's ``universe`` dict, or a
    graph's universe dict.
    """
    return [
        _get(declaration, "mode") or "manual",
        _get(declaration, "market") or "perp",
        _sorted_or_none(_get(declaration, "symbols")),
        _sorted_or_none(_get(declaration, "categories")),
        _number(_get(declaration, "top_n")),
        _sorted_or_none(_get(declaration, "exclusions")),
        _sorted_or_none(_get(declaration, "inclusions")),
        _get(declaration, "lookback"),
        _sorted_or_none(_get(declaration, "volume_quartiles")),
        _floor(declaration),
    ]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _h(tag: str, text: str, n: int) -> str:
    return hashlib.sha256(f"{tag}\n{text}".encode()).hexdigest()[:n]


def _parts(declaration: object) -> tuple[str, str, str]:
    criteria = _h("keel-universe-criteria-v1", _canonical(seal_criteria(declaration)), 12)
    resolved_at = str(_get(declaration, "resolved_at") or "")
    at = _h("keel-universe-at-v1", resolved_at, 8)
    members = sorted(str(s) for s in (_get(declaration, "resolved") or []))
    listing = _h("keel-universe-list-v1", f"{criteria}\n{resolved_at}\n{_canonical(members)}", 12)
    return criteria, at, listing


def compute_seal(declaration: object) -> str:
    """The seal for ``declaration``'s current criteria, ``resolved_at`` and list."""
    criteria, at, listing = _parts(declaration)
    return f"{SEAL_PREFIX}{criteria}.{at}.{listing}"


def seal_verdict(declaration: object) -> str:
    """What the declaration's ``resolution`` seal says about its list (see the
    module docstring's table)."""
    seal = _get(declaration, "resolution")
    if seal is None:
        return UNSEALED
    match = _SEAL_RE.match(seal) if isinstance(seal, str) else None
    if match is None:
        return MALFORMED
    criteria, at, listing = _parts(declaration)
    if match.group(2) != at:
        return SUPERSEDED
    if match.group(1) != criteria:
        return CRITERIA_MOVED
    if match.group(3) != listing:
        return LIST_EDITED
    return VALID


def should_seal(declaration: object) -> bool:
    """Whether a RESOLVING writer seals this declaration: a computed mode with
    a non-empty list."""
    return _get(declaration, "mode") in SEALED_MODES and bool(_get(declaration, "resolved"))


def seal_refusal_detail(declaration: object, verdict: str) -> str:
    """The STALE_UNIVERSE sentence for a refusing verdict — the ONE wording the
    validator and the submit/deploy gate share."""
    from pipeline_engine.dsl.universe_expectation import MANUAL_BASKET_REMEDY

    mode = _get(declaration, "mode")
    label = "top-volume" if mode == "top_volume" else f"{mode}"
    resolved_at = _get(declaration, "resolved_at") or "an earlier resolve"
    count = len(_get(declaration, "resolved") or [])
    if verdict == LIST_EDITED:
        return (
            f"Universe 'resolved' ({count} symbols) is not the list the resolver produced "
            f"for these criteria at {resolved_at} — it was edited after the resolve (the "
            "resolution seal no longer matches its members). A list you choose cannot run "
            f"under the {label} label. To trade coins you choose, declare them as a manual "
            'basket: Universe(mode="manual", symbols=[…]). To go back to the computed list, '
            "re-resolve."
        )
    if verdict == CRITERIA_MOVED:
        return (
            f"Universe 'resolved' answers different criteria than the ones declared: the "
            f"criteria changed after the resolve at {resolved_at}. Re-resolve so the list "
            f"answers them. {MANUAL_BASKET_REMEDY}"
        )
    if verdict == MALFORMED:
        return (
            "Universe 'resolution' is not a seal Keel writes (expected "
            "'u1:<criteria>.<instant>.<list>'), so nothing vouches for this list. Re-resolve "
            f"to reseal it. {MANUAL_BASKET_REMEDY}"
        )
    raise ValueError(f"verdict {verdict!r} does not refuse")


def with_seal(declaration: Any) -> Any:
    """``declaration`` (a ``UniverseSpec``) with ``resolution`` set to its fresh
    seal when :func:`should_seal`, else cleared. For writers that RESOLVE."""
    import dataclasses

    return dataclasses.replace(
        declaration, resolution=compute_seal(declaration) if should_seal(declaration) else None
    )


def seal_source(source: str) -> str:
    """``source`` with its Universe's ``resolution`` written for the list it now
    carries — the span edit every RESOLVING writer ends with, right after it
    wrote ``resolved`` / ``resolved_at``.

    Sealable (:func:`should_seal`) → the fresh seal is set; otherwise any seal
    is removed, so a writer that resolved a manual basket never leaves a
    computed-mode seal behind. A source without a Universe is returned as is.
    Raises the edit layer's ``EditError`` like the edits it follows.
    """
    import dataclasses

    from pipeline_engine.dsl.edits import replace_declaration, set_decl_arg
    from pipeline_engine.dsl.parser import parse_strategy

    universe = parse_strategy(source).universe
    if universe is None:
        return source
    if should_seal(universe):
        seal = compute_seal(universe)
        if universe.resolution == seal:
            return source
        return set_decl_arg(source, "universe", "resolution", seal, prefer_quote='"')
    if universe.resolution is None:
        return source
    # Rare (a seal left on a list no writer seals): the Universe statement is
    # re-emitted canonically without it; the rest of the source is untouched.
    return replace_declaration(
        source, "universe", dataclasses.replace(universe, resolution=None, location=None)
    )


# ── Cross-language vectors ─────────────────────────────────────────────────

VECTORS_PATH = Path(__file__).parent / "fixtures" / "universe_seal_vectors.json"

_VECTOR_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "top_volume_basic",
        {
            "mode": "top_volume",
            "market": "perp",
            "top_n": 3,
            "lookback": "7d",
            "resolved": ["BTC", "ETH", "SOL"],
            "resolved_at": "2026-07-06T00:00:00+00:00",
        },
    ),
    (
        "top_volume_reordered_members",
        {
            "mode": "top_volume",
            "market": "perp",
            "top_n": 3,
            "lookback": "7d",
            "resolved": ["SOL", "BTC", "ETH"],
            "resolved_at": "2026-07-06T00:00:00+00:00",
        },
    ),
    (
        "top_volume_swapped_member",
        {
            "mode": "top_volume",
            "market": "perp",
            "top_n": 3,
            "lookback": "7d",
            "resolved": ["BTC", "ETH", "MORPHO"],
            "resolved_at": "2026-07-06T00:00:00+00:00",
        },
    ),
    (
        "top_volume_exclusions_floor_quartiles",
        {
            "mode": "top_volume",
            "top_n": 15,
            "exclusions": ["kPEPE", "BTC"],
            "inclusions": ["HYPE"],
            "volume_quartiles": ["q2", "q1"],
            "min_trailing_dollar_volume": 1000000.0,
            "resolved": ["ETH", "HYPE", "SOL"],
            "resolved_at": "2026-10-01T12:00:00Z",
        },
    ),
    (
        "top_volume_fractional_floor_alias",
        {
            "mode": "top_volume",
            "top_n": 5,
            "min_trailing_notional_proxy": 2500000.5,
            "resolved": ["ETH"],
            "resolved_at": "2026-10-01T12:00:00Z",
        },
    ),
    (
        "top_volume_empty_lists_read_as_absent",
        {
            "mode": "top_volume",
            "top_n": 3,
            "lookback": "7d",
            "exclusions": [],
            "inclusions": [],
            "resolved": ["BTC", "ETH", "SOL"],
            "resolved_at": "2026-07-06T00:00:00+00:00",
        },
    ),
    (
        "category_defi",
        {
            "mode": "category",
            "categories": ["defi", "l1"],
            "top_n": 10,
            "resolved": ["AAVE", "UNI"],
            "resolved_at": "2026-09-01T00:00:00Z",
        },
    ),
    (
        "unicode_symbol",
        {
            "mode": "top_volume",
            "top_n": 1,
            "resolved": ["Ünï"],
            "resolved_at": "",
        },
    ),
]


def seal_vectors() -> dict[str, Any]:
    """The cross-language vectors: each case's declaration, its canonical
    criteria array and its seal."""
    return {
        "_comment": (
            "GENERATED by pipeline_engine.dsl.universe_seal.write_seal_vectors (Q-2434). "
            "The TS twin (keel-app universe-seal.ts) must reproduce every seal byte-exact."
        ),
        "prefix": SEAL_PREFIX,
        "cases": [
            {
                "name": name,
                "universe": universe,
                "criteria": seal_criteria(universe),
                "seal": compute_seal(universe),
            }
            for name, universe in _VECTOR_CASES
        ],
    }


def write_seal_vectors(path: Path = VECTORS_PATH) -> None:
    """Regenerate ``fixtures/universe_seal_vectors.json``."""
    path.write_text(json.dumps(seal_vectors(), ensure_ascii=False, indent=2) + "\n")
