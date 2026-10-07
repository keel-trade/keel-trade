"""What to say about a component name the registry does not have (Q-2449, Q-1859).

The ONE owner of the unknown-component hint. Every lookup that misses asks
here: the validator's ``UNKNOWN_COMPONENT`` (both its sites), the in-app
agent's ``strategy_component_detail``, and the SDK's bundled-registry miss
(``keel_components_get`` / ``get_many`` / ``compose_help`` on the hosted,
stdio and CLI surfaces — this module is vendored into the wheel). keel-app's
editor reads the same hint table through the generated
``component_name_hints.json`` (``fixtures/loader.write_component_name_hints``)
and runs the same tiers in ``pass4-names.ts``.

Tiers, first hit wins:

(a) case-insensitive exact — ``rsi`` → ``RSI``;
(b) the curated :data:`COMPONENT_NAME_HINTS` — names agents reach for that no
    string metric can find: abbreviations spelled out (``RateOfChange`` →
    ``ROC``), synonyms (``EMA`` → ``EWMA``, ``RollingMean`` → ``SMA``) and
    PATTERN names (``EWMAC`` — a fast/slow ``EWMA`` branch into
    ``Crossover``). Seeded from the prod 404 corpus (Q-2449 RCA); every
    target is asserted to exist in the live registry;
(c) the scored matcher for typos and generic-suffix noise
    (:func:`suggest_component_matches`, moved here from the validator);
(d) nothing — the caller renders an honest "no close match", never a guess.

A hint never RESOLVES: ``EWMAC`` is not a second name for ``Crossover``.
The parser, the lock and the registry keep exactly one name per component;
this only says which real name to write.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


__all__ = [
    "COMPONENT_NAME_HINTS",
    "ComponentNameHint",
    "NameSuggestion",
    "component_name_hints_table",
    "render_component_name_suggestion",
    "suggest_component_matches",
    "suggest_component_names",
]


# Generic camelCase suffix tokens that must not carry a match on their own.
GENERIC_TOKEN_NAMES = frozenset({"transform", "series", "signal", "data", "value"})
# English function words: `of` in `RateOfChange` must never match
# `ChangeOfCharacter` (Q-1859). Dropped from the asked name's tokens.
FUNCTION_WORDS = frozenset({"of", "and", "the", "to", "from", "with", "on", "in"})
_CAMEL_TOKEN_RE = re.compile(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|$)|[a-z]+|\d+")

# The one recipe the crossover pattern names (Crossover's own composition
# example: a fast/slow dict branch, then Crossover).
_CROSSOVER_RECIPE = (
    'a {"fast": [EWMA(window=8)], "slow": [EWMA(window=32)]} branch followed by Crossover()'
)


@dataclass(frozen=True)
class ComponentNameHint:
    """One curated hint: the real component(s) a name means.

    ``pattern`` is set when the asked name is a composition, not a
    component; it is the DSL recipe, and ``targets`` are the components the
    recipe uses.
    """

    targets: tuple[str, ...]
    pattern: str | None = None


#: Asked name (matched case-insensitively) → the components it means.
COMPONENT_NAME_HINTS: dict[str, ComponentNameHint] = {
    # Pattern names: the moving-average crossover (Q-2449).
    "EWMAC": ComponentNameHint(("EWMA", "Crossover"), _CROSSOVER_RECIPE),
    "EWMACrossover": ComponentNameHint(("EWMA", "Crossover"), _CROSSOVER_RECIPE),
    "EMACrossover": ComponentNameHint(("EWMA", "Crossover"), _CROSSOVER_RECIPE),
    "MACrossover": ComponentNameHint(("EWMA", "Crossover"), _CROSSOVER_RECIPE),
    "MovingAverageCrossover": ComponentNameHint(("EWMA", "Crossover"), _CROSSOVER_RECIPE),
    # Synonyms seen in the prod 404 corpus (Q-2449).
    "EMA": ComponentNameHint(("EWMA",)),
    "RollingMean": ComponentNameHint(("SMA",)),
    "StochasticOscillator": ComponentNameHint(("Stochastic",)),
    "ApplyGlobalMask": ComponentNameHint(("ApplyMask",)),
    "VolatilityTargetSizer": ComponentNameHint(("VolTargetWeightConverter",)),
    "InverseVolatilitySizer": ComponentNameHint(("VolWeightSizer",)),
    # Indicator long names → the registered abbreviation (Q-1859).
    "RateOfChange": ComponentNameHint(("ROC",)),
    "RelativeStrengthIndex": ComponentNameHint(("RSI",)),
    "AverageTrueRange": ComponentNameHint(("ATR",)),
    "SimpleMovingAverage": ComponentNameHint(("SMA",)),
    "ExponentialMovingAverage": ComponentNameHint(("EWMA",)),
    "MovingAverageConvergenceDivergence": ComponentNameHint(("MACD",)),
    "CommodityChannelIndex": ComponentNameHint(("CCI",)),
}

_HINTS_BY_FOLDED = {name.lower(): hint for name, hint in COMPONENT_NAME_HINTS.items()}


@dataclass(frozen=True)
class NameSuggestion:
    """The answer for one unknown name: the names to write, and a recipe when
    the asked name is a pattern. Empty ``names`` is tier (d)."""

    names: tuple[str, ...]
    pattern: str | None = None


def _camel_tokens(name: str) -> list[str]:
    """Split a camelCase component name into lowercase tokens.

    `FillNaN` → ['fill', 'nan']; `RollingZScoreTransform` → ['rolling', 'z', 'score', 'transform'].
    """
    return [m.lower() for m in _CAMEL_TOKEN_RE.findall(name)]


def suggest_component_matches(name: str, registry_names: list[str]) -> list[str]:
    """Tier (c): up to 3 registry names scored as close to ``name``.

    Hybrid scoring designed to surface semantically-close matches even when
    a difflib character-sequence ratio is dominated by a shared generic
    suffix (e.g. 'FillNATransform' → 'FillNaN' should beat unrelated
    '*Transform' names that only match on the suffix).

      score = meaningful_token_overlap * 0.5 + difflib_ratio + substring_boost

    - meaningful_token_overlap: count of shared camelCase tokens excluding
      generic suffixes (Transform/Series/Signal/Data/Value) and English
      function words (of/and/the/…, Q-1859). Each shared meaningful token
      outweighs ~0.5 ratio points.
    - difflib_ratio: standard character-sequence similarity (handles typos).
    - substring_boost: +0.3 if either name (lowercased) contains the other.

    Returns names with score >= 0.6 AND within 0.3 of the top score, capped
    at 3 total. Empty list when no candidate clears the bar — callers should
    surface a "no close match" hint rather than a misleading guess.
    """
    if not registry_names:
        return []
    user_meaningful = {
        t for t in _camel_tokens(name) if t not in GENERIC_TOKEN_NAMES and t not in FUNCTION_WORDS
    }
    name_lower = name.lower()
    scored: list[tuple[float, str, int, float]] = []  # score, name, overlap, sub
    for reg_name in registry_names:
        reg_tokens = set(_camel_tokens(reg_name))
        overlap = len(user_meaningful & reg_tokens)
        ratio = difflib.SequenceMatcher(None, name_lower, reg_name.lower()).ratio()
        reg_lower = reg_name.lower()
        # Substring boost only when the shorter string is substantial — short
        # accidental substrings ('ATR' inside 'DropNATransform') are noise.
        shorter_len = min(len(name_lower), len(reg_lower))
        substr = (
            0.3
            if shorter_len >= 5 and (name_lower in reg_lower or reg_lower in name_lower)
            else 0.0
        )
        # Filter: when there's no semantic signal (no shared token, no substring),
        # require a typo-level ratio (>=0.85). Otherwise generic-suffix matches
        # ('*Transform') flood the suggestions with bad guesses.
        if overlap == 0 and substr == 0.0 and ratio < 0.85:
            continue
        scored.append((overlap * 0.5 + ratio + substr, reg_name, overlap, substr))
    if not scored:
        return []
    scored.sort(reverse=True)
    out: list[str] = []
    top_score = scored[0][0]
    for score, rn, _ov, _sub in scored:
        if score < 0.6:
            break
        if out and score < top_score - 0.3:
            break
        out.append(rn)
        if len(out) >= 3:
            break
    return out


def suggest_component_names(name: str, registry_names: list[str]) -> NameSuggestion:
    """Tiers (a) → (d) for one unknown name, against ``registry_names``.

    A curated hint applies only when every target is in ``registry_names``
    (a lock-scoped registry may lack one); otherwise the scorer answers.
    """
    folded = name.strip().lower()
    for reg_name in registry_names:
        if reg_name.lower() == folded:
            return NameSuggestion((reg_name,))
    hint = _HINTS_BY_FOLDED.get(folded)
    if hint is not None and set(hint.targets) <= set(registry_names):
        return NameSuggestion(hint.targets, hint.pattern)
    return NameSuggestion(tuple(suggest_component_matches(name, registry_names)))


def render_component_name_suggestion(name: str, suggestion: NameSuggestion) -> str:
    """The surface-neutral hint text (Q-2273 L4: names no one surface's tool).

    A pattern → "'EWMAC' is a pattern, not a component: <recipe>."; names →
    "Did you mean: A, B?"; nothing → the honest no-match line.
    """
    if suggestion.pattern is not None:
        return f"'{name}' is a pattern, not a component: write {suggestion.pattern}."
    if suggestion.names:
        return f"Did you mean: {', '.join(suggestion.names)}?"
    return (
        f"No close match for '{name}'. Search the component catalog for what the "
        f"step should compute to find the right component."
    )


def component_name_hints_table() -> dict:
    """The JSON projection keel-app's editor reads (``component_name_hints.json``).

    Everything the TS twin needs to run the same tiers: the token sets the
    scorer drops and the curated hints. Sorted for byte-determinism.
    """
    return {
        "generic_tokens": sorted(GENERIC_TOKEN_NAMES),
        "function_words": sorted(FUNCTION_WORDS),
        "hints": [
            {"name": name, "targets": list(hint.targets), "pattern": hint.pattern}
            for name, hint in sorted(COMPONENT_NAME_HINTS.items(), key=lambda kv: kv[0].lower())
        ],
    }
