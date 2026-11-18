"""The closed output-unit vocabulary and its plain-language reading.

Screeners foundation Table 1.2. Values are never guessed from observed numbers.
``preserve`` is a registry relation, not an output unit.

:data:`UNIT_KEYS` is the vocabulary a pipeline output may declare and screens
read; it is unchanged by Research. :data:`RESEARCH_UNIT_KEYS` adds the five
units research measures use (Research 00 §B.6, G14; RL-88 §4, K-7):
``multiple``, ``ratio``, ``probability``, ``r_multiple`` and ``days``. One
formatter, two vocabularies: :func:`unit_words` and :func:`format_unit_value`
take ``keys``, the vocabulary the caller formats from. The default is
:data:`UNIT_KEYS`, so every pipeline and screen caller is exactly as before
(a research unit there still raises); only Research passes
``keys=RESEARCH_UNIT_KEYS`` (SR-61 row 23).
"""

UNIT_KEYS: tuple[str, ...] = (
    "percent",
    "fraction",
    "rate_per_hour",
    "rate_per_year",
    "price",
    "notional",
    "count",
    "score",
    "rank",
    "flag",
    "unknown",
)

_UNIT_WORDS = {
    "percent": "percent × 100: 5.0 = 5%",
    "fraction": "a ratio: 0.05 = 5%",
    "rate_per_hour": "a decimal per hour: 0.05 = 5% per hour",
    "rate_per_year": "a decimal per year: 0.05 = 5% per year",
    "price": "price in quote units",
    "notional": "notional in quote units",
    "count": "a count",
    "score": "a dimensionless score",
    "rank": "a rank or percentile in [0, 1]",
    "flag": "a flag: 0 or 1, or −1, 0 or 1",
    "unknown": "unknown units; no unit is declared or derivable",
}

#: The five research units (Research 00 §B.6). Not pipeline output units: a
#: pipeline output declaring one is still refused (``validation_shared``).
_RESEARCH_UNIT_WORDS = {
    "multiple": "a multiple of a starting value: 1.5 = 1.5× the start",
    "ratio": "a dimensionless ratio",
    "probability": "a probability in [0, 1]",
    "r_multiple": "a multiple of a trade's risk (R): 1.0 = 1R",
    "days": "a length of time in days",
}

RESEARCH_UNIT_KEYS: tuple[str, ...] = (*UNIT_KEYS, *_RESEARCH_UNIT_WORDS)

_ALL_UNIT_WORDS = {**_UNIT_WORDS, **_RESEARCH_UNIT_WORDS}


def unit_words(output: str, unit: str, *, keys: tuple[str, ...] = UNIT_KEYS) -> str:
    """Read an output's stored unit without inventing a display convention.

    ``keys`` is the vocabulary allowed (:data:`UNIT_KEYS` by default;
    Research passes :data:`RESEARCH_UNIT_KEYS`).
    """
    if unit not in keys or unit not in _ALL_UNIT_WORDS:
        raise ValueError(f"Unknown output unit: {unit!r}")
    return f"{output} is {_ALL_UNIT_WORDS[unit]}"


def format_unit_value(value: float, unit: str, *, keys: tuple[str, ...] = UNIT_KEYS) -> str:
    """Render a literal in its declared unit, without changing stored values.

    ``keys`` is the vocabulary allowed (:data:`UNIT_KEYS` by default;
    Research passes :data:`RESEARCH_UNIT_KEYS`).
    """
    import math

    if unit not in keys or unit not in _ALL_UNIT_WORDS:
        raise ValueError(f"Unknown output unit: {unit!r}")
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Unit values must be finite numbers")
    if unit in {"fraction", "rate_per_hour", "rate_per_year"}:
        value *= 100
    text = format(value, ".12g")
    if unit in {"percent", "fraction", "rate_per_hour", "rate_per_year"}:
        text += "%"
    if unit == "rate_per_hour":
        text += " an hour"
    elif unit == "rate_per_year":
        text += " a year"
    elif unit == "multiple":
        text += "×"
    elif unit == "r_multiple":
        text += "R"
    elif unit == "days":
        text += " day" if value == 1 else " days"
    return text
