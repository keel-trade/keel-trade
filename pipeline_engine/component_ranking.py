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


def description_summary(description: str | None) -> str:
    """The description's first paragraph — the component's own definition.

    The body of a docstring names NEIGHBOURS ("``EWMA`` is exponential
    smoothing — a different curve"), recipes and cross-references; the
    first paragraph says what THIS component is. It is also the line every
    search surface prints for the result.
    """
    return (description or "").strip().split("\n\n")[0]


def text_phrases(text: str | None) -> set[tuple[str, str]]:
    """Adjacent token pairs of free text, in reading order ("moving average")."""
    tokens = _PLAIN_TOKEN_RE.findall((text or "").lower())
    return set(zip(tokens, tokens[1:]))


def compound_partners(
    components: list[dict[str, Any]], query_phrases: set[tuple[str, str]]
) -> dict[str, set[str]]:
    """The query's COMPOUNDS, as token -> the tokens it is bound to.

    A compound is a query phrase (adjacent token pair, ``("moving",
    "average")``) that some candidate's description SUMMARY — the first
    paragraph, where a component says what it is — carries as a phrase. The
    corpus, not a word list, decides what a compound is: "moving average"
    is one because ``SMA`` defines itself by it; "long only" because
    ``ThresholdCross`` does; "index basket" because ``ConstantForecast``
    does.
    """
    carried: set[tuple[str, str]] = set()
    for comp in components:
        carried |= query_phrases & text_phrases(description_summary(comp.get("description")))
    partners: dict[str, set[str]] = {}
    for a, b in carried:
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)
    return partners


def score_component(
    comp: dict[str, Any],
    query_tokens: set[str],
    name_token_weights: dict[str, float] | None = None,
    query_phrases: set[tuple[str, str]] | None = None,
    compounds: dict[str, set[str]] | None = None,
) -> float:
    """Token-overlap relevance: name ×3 (ubiquity-scaled), category ×2, description ×1, phrase ×1.

    The **phrase** arm (Q-1098) counts each adjacent query-token pair
    (``query_phrases``, e.g. ``("moving", "average")``) that also appears
    adjacent in the description. Two words scattered across a long
    docstring are weaker evidence than the phrase the user typed: the SMC
    indicators carry 6–9 KB of prose and matched six of the eight words of
    "price above 50 day moving average bullish filter" — the same count
    as ``SMA`` — without ever saying "moving average".

    ``name_token_weights`` maps a name token to its ubiquity scale
    (spec 02 §9.2 item 8). Absent (the historical single-component call
    shape), every matched name token counts at the full ×3.
    ``query_phrases`` absent means no phrase arm.

    ``compounds`` (``compound_partners``, agent-surface-cleanup spec 04
    §2.3c/§2.4) is the **half-compound rule**: a name token that belongs to
    one of the query's compounds earns the name weight only when the
    component carries the whole compound — a partner token in its name, or
    the phrase in its description; otherwise it counts ×1, the weight a
    description word gets. Measured before it (2026-09-23): bare
    "moving average" answered ``AveragePairwiseCorrelationRegime`` (name
    "average", 5.0) above every moving average (3.0); "long only" answered
    ``LongShortWeightConverter`` (name "long") above ``ThresholdCross``,
    whose first paragraph says "long-only"; "index basket" answered
    ``IndexShiftTransform`` (name "index", a row shift) above
    ``ConstantForecast``. Half a compound is a different word. A component
    that carries the whole compound (``RiskParityAllocator`` on "risk
    parity" by name, ``Crossover`` on "moving average crossover" by its
    description) keeps its weight, and a query with no compound is scored
    exactly as before.
    """
    name_tokens = tokenize_name(comp.get("name"))
    name_hits = query_tokens & name_tokens
    description = comp.get("description")
    description_phrases = text_phrases(description)

    def _name_weight(token: str) -> float:
        full = 3.0 * (1.0 if name_token_weights is None else name_token_weights.get(token, 1.0))
        partners = (compounds or {}).get(token)
        if not partners or partners & name_tokens:
            return full
        if any(
            (token, p) in description_phrases or (p, token) in description_phrases for p in partners
        ):
            return full
        return min(full, 1.0)

    score = sum(_name_weight(t) for t in name_hits)
    score += len(query_tokens & tokenize_text(comp.get("category"))) * 2.0
    score += len(query_tokens & tokenize_text(description)) * 1.0
    if query_phrases:
        score += len(query_phrases & description_phrases) * 1.0
    return score


def summary_hits(comp: dict[str, Any], query_tokens: set[str]) -> int:
    """Query tokens the description's FIRST PARAGRAPH names (a tie-break, Q-1098)."""
    return len(query_tokens & tokenize_text(description_summary(comp.get("description"))))


#: The position layer's closed sub-category vocabulary (position-layer spec
#: 03-R67): the ``TradeManager`` head, the trade and between-trades readers,
#: trade operators, actions, the ``Exposure`` realizer, the position sizer and
#: the factories. Pure data, so the wheel's vendored copy carries it; spec
#: 03's ``binding.POSITION_LAYER_SUB_CATEGORIES`` must equal it (pinned by
#: ``component_ranking_position_test.py``). Read by :func:`rank_key`'s
#: position-layer arm (spec 04-R29).
POSITION_LAYER_SUB_CATEGORIES: frozenset[str] = frozenset(
    {
        "trade_manager",
        "trade_reader",
        "between_trades_reader",
        "trade_operator",
        "trade_action",
        "exposure",
        "risk_sizing",
        "trade_factory",
    }
)


def is_deprecated(comp: dict[str, Any]) -> bool:
    """Whether a search record's LATEST version is ``status="deprecated"``."""
    return (comp.get("status") or "active") == "deprecated"


def visible(comp: dict[str, Any], include_deprecated: bool = False) -> bool:
    """THE search-visibility predicate (position-layer spec 04-R28, D-21).

    Every component SEARCH surface hides a deprecated component by default
    and shows it only when the caller asks (``include_deprecated=True``):
    the SDK's bundled search, ``keel_components_search`` (bundled, after /
    before and API paths), chat's ``strategy_components_search`` and the web
    browser. One predicate, so the surfaces cannot disagree about what
    "hidden" means. Lookups BY NAME (``keel_components_get``,
    ``GET /v1/components/{name}``) never consult it — a pinned strategy's
    component always answers.
    """
    return include_deprecated or not is_deprecated(comp)


def deprecation_fields(comp: dict[str, Any]) -> dict[str, Any]:
    """``{status, replacement_text}`` for a DEPRECATED search row, else ``{}``.

    Spec 04-R26: a search row says it is deprecated only when it is (shown
    with ``include_deprecated=True``); ``replacement_text`` is the structured
    replacement's agent-facing line, or the plain replacement name.
    """
    if not is_deprecated(comp):
        return {}
    shape = comp.get("replacement_shape")
    text = shape.get("text") if isinstance(shape, dict) else None
    if not text and comp.get("replacement"):
        text = f"Use {comp['replacement']}"
    return {"status": "deprecated", "replacement_text": text}


def rank_key(
    comp: dict[str, Any], score: float, summary: int = 0
) -> tuple[int, float, int, int, int, str]:
    """The deterministic ordering chain (spec 02 §9.2 item 2).

    In order:

    1. **status partition** — a component whose latest version is
       ``status="deprecated"`` sinks below every active one regardless of
       score. This is spec 01 §8.4 item 4's "de-rank the legacy components
       below the pair"; it arms for `SignalTimeframeConverter` /
       `AlignToFrequency` when their deferred deprecated flags land at
       Gate-2 (spec 02 §8.2, R-4).
    2. **score**, descending.
    2b. **position layer first** (position-layer spec 04-R29, D-21, D-10) —
       at EQUAL score only, a component whose ``sub_category`` is in
       :data:`POSITION_LAYER_SUB_CATEGORIES` (``TradeManager``, readers,
       actions, ``Exposure``, ``RiskSizer``, factories) outranks the rest, so
       an exit query's tie answers with the trade rule, not a market signal.
       It sits below the score (unrelated queries keep their order) and
       below the status partition (a deprecated component still sinks).
    3. **declaration-backed over explicit** — a component with
       `declaration_refs` (its clock is wired to `Globals`, so it cannot
       drift when the declaration changes) outranks its explicit sibling at
       equal score. This is what puts `TargetSignalResampler` above
       `SignalResampler` and `TargetSignalProjector` above `SignalProjector`
       (founder ruling K15: prefer the `Target*` variants in most places).
    4. **summary hits**, descending (``summary_hits``, Q-1098) — at equal
       score, the component whose own first paragraph names more of the
       query wins. Before this the tie fell to the name, so "price above
       50 day moving average bullish filter" answered ``FairValueGap`` and
       ``LiquiditySweep`` (6-9 KB docstrings, six coincidental body hits)
       above ``SMA`` ("the simple moving average of close", same six). It
       sits BELOW the K15 key on purpose: the explicit clock variants'
       summaries say "multi-timeframe" and the ``Target*`` ones do not, so
       as a score arm it inverted K15 on "multi timeframe".
    5. **name**, ascending — stability only, never a preference.
    """
    status_rank = 1 if is_deprecated(comp) else 0
    position_rank = 0 if comp.get("sub_category") in POSITION_LAYER_SUB_CATEGORIES else 1
    decl_rank = 0 if comp.get("declaration_refs") else 1
    return (status_rank, -score, position_rank, decl_rank, -summary, comp.get("name") or "")


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
    query_phrases = text_phrases(query)
    weights = {t: ubiquity_weight(df) for t, df in name_token_doc_freq(components).items()}
    compounds = compound_partners(components, query_phrases)
    scored = [
        (comp, score_component(comp, query_tokens, weights, query_phrases, compounds))
        for comp in components
    ]
    scored = [(comp, score) for comp, score in scored if score > 0]
    scored.sort(key=lambda item: rank_key(item[0], item[1], summary_hits(item[0], query_tokens)))
    return scored


def rank_components(components: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Ordered components for `query` — see ``rank_components_scored``."""
    return [comp for comp, _ in rank_components_scored(components, query)]


# ─── the lexical/recall split (spec 02 §9.2 items 5 and 8) ───────────────
#
# A surface that pairs this lexical scorer with a RECALL arm (the MCP/
# chat-api tool pairs it with the FAISS index; the SDK/CLI ships no index
# and simply takes the whole chain) has to decide how much of one result
# page the lexical block may occupy. That decision is a ranking judgement,
# so it lives HERE beside the scorer rather than in either caller — the
# same reason the scorer itself moved here (§9.2 item 3, R-25 finding S7).

#: How many slots of a result page stay available to a recall arm when the
#: lexical field has a weak tail. One is deliberate and sufficient: the
#: guarantee this buys is STRUCTURAL — the recall arm always gets to run —
#: not "more semantic answers". Raising it would start displacing lexical
#: hits that a query with a discriminating field earned.
RECALL_RESERVE = 1


def partition_by_strength(
    scored: list[tuple[dict[str, Any], float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a scored field into STRONG evidence and its WEAK tail.

    Strong is "within half the best match's evidence". The cut is
    RELATIVE on purpose: where the whole field scores closely (e.g.
    ``"multi timeframe"``, whose members all match the same one or two
    distinctive tokens) every K15-ordered result still counts as strong,
    and such a query is legitimately answered all-lexically.

    Both lists keep the ``rank_components_scored`` order, so
    ``strong + weak`` is exactly that chain.
    """
    max_score = max((score for _, score in scored), default=0.0)
    cut = max_score / 2.0
    strong = [comp for comp, score in scored if score >= cut]
    weak = [comp for comp, score in scored if score < cut]
    return strong, weak


def lexical_quota(
    strong: list[dict[str, Any]],
    weak: list[dict[str, Any]],
    top_k: int,
) -> int:
    """How many of a ``top_k``-slot page the lexical block may occupy.

    **Why this exists.** ``partition_by_strength``'s relative cut was the
    fix for "any token overlap fills the whole quota and the recall arm
    never fires". It worked for the corpus of the day and then DECAYED, by
    construction: as component docstrings grow denser, more of the field
    clears half the best score, the strong set alone reaches ``top_k``, and
    the recall arm is switched off again on exactly the conceptual queries
    it exists for. Measured 2026-09-04 (Q-1074) on
    ``"hedge against drawdown when market crashes"``: eight strong lexical
    hits, three of them noise (``Displacement``, ``OpenInterestRegime``,
    ``PremiumDiscount``), zero semantic. Nothing in the ranking code had
    changed — 55 docstring examples had been fixed the day before. The
    verdict then flipped back to green on the next regeneration, which is
    the tell: the property was never held, it was being satisfied by
    corpus accident in either direction.

    **The rule.** A field with a WEAK TAIL has, by its own evidence,
    already run out of strong matches somewhere — so the page keeps
    ``RECALL_RESERVE`` slots for the recall arm no matter how many strong
    hits are queued behind them. A field with NO weak tail is uniform:
    every scoring component is within half the leader, the query is
    answered all-lexically, and the quota is the whole page. That is the
    property the relative cut was built to protect and it is preserved
    exactly.

    The quota never drops below 1 — the best lexical hit is never given
    away, however small the page.

    Note what this does NOT fix, so nobody reads more into it: a field
    that is uniform because every member scores the FLOOR (one generic
    description-token coincidence, e.g. ``"fade an overbought move"``,
    where 94 of 222 components tie at 2.0 and the tail is empty) has no
    weak tail and is untouched here. That is a defect in the evidence
    MEASURE — un-IDF'd description overlap — not in the split, and it is
    filed separately (Q-1098).
    """
    if not weak:
        return top_k
    if len(strong) <= top_k - RECALL_RESERVE:
        # The lexical block does not reach the reservation anyway; leaving
        # the quota at top_k keeps the weak-tail backfill able to fill the
        # page when the recall arm comes up short.
        return top_k
    return max(top_k - RECALL_RESERVE, 1)


__all__ = [
    "POSITION_LAYER_SUB_CATEGORIES",
    "RECALL_RESERVE",
    "compound_partners",
    "deprecation_fields",
    "description_summary",
    "is_deprecated",
    "lexical_quota",
    "name_token_doc_freq",
    "partition_by_strength",
    "rank_components",
    "rank_components_scored",
    "rank_key",
    "score_component",
    "summary_hits",
    "text_phrases",
    "tokenize_name",
    "tokenize_text",
    "ubiquity_weight",
    "visible",
]
