"""Component-search ranking — THE one scorer, for every search surface.

Contract of record: dsl-multi-timeframe-clocks `specs/02-validator-surface.md`
§9 (the measured mechanism in §9.1, the fix in §9.2, the golden queries in
§9.3). Founder ruling K15 — prefer the declaration-backed ``Target*``
variants in most places — is delivered by the ordering chain below.

**Why this module exists at all.** The scorer was born in
``packages/keel-trade/keel-sdk/keel/data/registry.py`` and copied verbatim
into the SDK's after/before overlay; §9.2 item 3 collapsed those two copies
into one. The R-25 audit then measured the SAME divergence one layer out:
the MCP/chat-api surface (``pipeline_engine.mcp.tools``) ranked by FAISS
distance alone and still answered ``"resample signal"`` with
``SignalResampler`` above ``TargetSignalResampler`` — K15 delivered in the
SDK, undelivered where AI authors actually call. Two rankings is two
answers to one question, so the scorer moved HERE: the one place both the
monorepo (``libs/``) and the shipped ``keel-trade`` wheel can import.

**How the wheel gets it.** ``packages/keel-trade/keel-sdk/scripts/build_data.py``
vendors this module into the wheel's ``pipeline_engine`` subset (the same
mechanism that ships ``validation_shared.py`` / ``constants.py``), and
``keel.data.registry`` re-exports the names from it. Pure stdlib on
purpose: no pandas, no numpy, no registry import — it scores plain dicts,
so the SDK's bundled-JSON records and the live ``ComponentRegistry``
records go through identical code.

Quick Start:
    >>> from pipeline_engine.component_ranking import rank_components
    >>> rank_components(records, "resample signal")  # ordered, score-0 dropped
"""

from __future__ import annotations

import math
import re
from typing import Any


# CamelCase-aware: split on case boundaries BEFORE lowercasing, so
# `TimeframeResampler` yields {"timeframe", "resampler"} instead of the
# single token "timeframeresampler" that could never equal a query word.
# Until this landed the ×3 name weight was dead for every camelCase
# component name — all five re-clocking components scored exactly 2
# (description-only) on "multi timeframe" and the tie fell to insertion
# order, i.e. alphabetically, i.e. `SignalResampler` first (spec 02 §9.1).
#
# SPEC DEVIATION (recorded): spec 02 §9.2 item 1 writes the regex as
# `[A-Z][a-z0-9]*|[a-z0-9]+`. That form shatters every acronym-named
# component into single letters — `RSI` → {"r","s","i"}, `MACD` →
# {"m","a","c","d"} — so the ×3 weight that fix is FOR would go dead for
# the ~20 acronym components instead. Measured: under the literal regex
# the query "rsi" no longer returns RSI in its top 3, "roc" loses ROC
# entirely, and "macd" drops MACD to third. The leading
# `[A-Z]+(?![a-z])` alternative keeps a run of capitals together
# (`SignalROC` → {"signal","roc"}, `MACDHistogram` → {"macd",
# "histogram"}) and serves the fix's stated intent exactly.
_CAMEL_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# Free text (query, category, description) is prose, not identifiers.
_PLAIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize_name(name: str | None) -> set[str]:
    """Tokenize a component NAME (an identifier) — CamelCase aware."""
    return {t.lower() for t in _CAMEL_TOKEN_RE.findall(name or "")}


def tokenize_text(text: str | None) -> set[str]:
    """Tokenize free text (query / category / description)."""
    return set(_PLAIN_TOKEN_RE.findall((text or "").lower()))


def name_token_doc_freq(components: list[dict[str, Any]]) -> dict[str, int]:
    """Document frequency of each name token across the candidate corpus."""
    df: dict[str, int] = {}
    for comp in components:
        for token in tokenize_name(comp.get("name")):
            df[token] = df.get(token, 0) + 1
    return df


def ubiquity_weight(df: int) -> float:
    """Per-token scale for the ×3 name weight (spec 02 §9.2 item 8).

    ``1 / (1 + ln(df))`` — a token unique to one component name keeps the
    full weight (df=1 ⇒ 1.0); one shared by 11 names (e.g. "signal")
    contributes ×0.29 of it. Ubiquitous name tokens are near-stopwords for
    discovery: matching them says almost nothing about WHICH component the
    query means, and before this scaling any query containing "signal"
    answered with a wall of ``Signal*`` names above the component whose
    description actually matched.
    """
    return 1.0 / (1.0 + math.log(df)) if df >= 1 else 1.0


def score_component(
    comp: dict[str, Any],
    query_tokens: set[str],
    name_token_weights: dict[str, float] | None = None,
) -> float:
    """Token-overlap relevance: name ×3 (ubiquity-scaled), category ×2, description ×1.

    ``name_token_weights`` maps a name token to its ubiquity scale
    (spec 02 §9.2 item 8). Absent (the historical single-component call
    shape), every matched name token counts at the full ×3.
    """
    name_hits = query_tokens & tokenize_name(comp.get("name"))
    if name_token_weights is None:
        score = len(name_hits) * 3.0
    else:
        score = sum(3.0 * name_token_weights.get(t, 1.0) for t in name_hits)
    score += len(query_tokens & tokenize_text(comp.get("category"))) * 2.0
    score += len(query_tokens & tokenize_text(comp.get("description"))) * 1.0
    return score


def rank_key(comp: dict[str, Any], score: float) -> tuple[int, float, int, str]:
    """The deterministic ordering chain (spec 02 §9.2 item 2).

    In order:

    1. **status partition** — a component whose latest version is
       ``status="deprecated"`` sinks below every active one regardless of
       score. This is spec 01 §8.4 item 4's "de-rank the legacy components
       below the pair"; it arms for `SignalTimeframeConverter` /
       `AlignToFrequency` when their deferred deprecated flags land at
       Gate-2 (spec 02 §8.2, R-4).
    2. **score**, descending.
    3. **declaration-backed over explicit** — a component with
       `declaration_refs` (its clock is wired to `Globals`, so it cannot
       drift when the declaration changes) outranks its explicit sibling at
       equal score. This is what puts `TargetSignalResampler` above
       `SignalResampler` and `TargetSignalProjector` above `SignalProjector`
       (founder ruling K15: prefer the `Target*` variants in most places).
    4. **name**, ascending — stability only, never a preference.
    """
    status_rank = 1 if (comp.get("status") or "active") == "deprecated" else 0
    decl_rank = 0 if comp.get("declaration_refs") else 1
    return (status_rank, -score, decl_rank, comp.get("name") or "")


def rank_components_scored(
    components: list[dict[str, Any]], query: str
) -> list[tuple[dict[str, Any], float]]:
    """Score `components` against `query` and order them (spec 02 §9.2).

    Returns ``(component, score)`` pairs so callers can distinguish strong
    matches from marginal ones (the semantic-recall arm cuts on score, not
    list position). Components scoring 0 are dropped, matching the
    historical contract. Name-token ubiquity weights (§9.2 item 8) are
    computed over the candidate corpus passed in, so a pre-filtered
    candidate set (where every name shares the filter's vocabulary)
    degrades gracefully toward the unweighted behavior.
    """
    query_tokens = tokenize_text(query)
    weights = {t: ubiquity_weight(df) for t, df in name_token_doc_freq(components).items()}
    scored = [(comp, score_component(comp, query_tokens, weights)) for comp in components]
    scored = [(comp, score) for comp, score in scored if score > 0]
    scored.sort(key=lambda item: rank_key(item[0], item[1]))
    return scored


def rank_components(components: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Ordered components for `query` — see ``rank_components_scored``."""
    return [comp for comp, _ in rank_components_scored(components, query)]


__all__ = [
    "name_token_doc_freq",
    "rank_components",
    "rank_components_scored",
    "rank_key",
    "score_component",
    "tokenize_name",
    "tokenize_text",
    "ubiquity_weight",
]
