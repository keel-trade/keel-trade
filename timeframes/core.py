"""The platform's clock alphabet, and every representation derived from it.

WHY THIS MODULE EXISTS
======================

The platform has always had exactly one clock alphabet. What it did not have
was anything *forcing* a consumer to derive from it — so every consumer that
needed a timeframe in some other shape (a ``timedelta``, a pandas offset
alias, a PostgreSQL interval, an enum member, a bar count) re-encoded two
things by hand: **the token set**, and **the mapping**. Each hand copy then
drifted silently, and the failure was always the same shape — a clock that is
already declarable does not work somewhere, and no test notices, because the
assertions were parametrized off the same hand table they were testing.

A census on 2026-09-06 counted 48 such hand-written alphabets. The defects
they had already produced, all found in a single day:

* three drifted pandas-offset tables — a live ``KeyError`` on ``3h`` (Q-1108)
* a second alphabet gating ``Globals`` — a declarable clock refused (L1)
* an exec-worker mirror leaving a ``5min`` message with no staleness deadline
* a chart union drawing 15m candles under 5min trade markers (L37)
* two clock-detection ladders covering different four-clock subsets, leaving
  8 of 11 clocks unoptimizable
* ``TimeFrame`` missing ``3h``; ``_TF_TO_DELTA`` missing ``2h``/``3h``/``6h``/``12h``
* a market-data endpoint whose arithmetic assumed the 15-minute venue capture
  was the finest grain that exists (``5 // 15 == 0``)

THE CONTRACT
============

**One entry in :data:`TIMEFRAME_MINUTES` is the whole cost of adding a clock.**
Every representation below is computed from that table, so a new rung reaches
every consumer that derives, with no second edit and nothing to remember.

Consumers MUST derive rather than re-enumerate. A new hand-written alphabet is
caught by the census gate (``infrastructure/ci/contract_copy_census.py``),
which fails CI unless the site is acknowledged in
``infrastructure/ci/TIMEFRAME_ALPHABETS.md`` with a stated reason. Genuinely
foreign vocabularies are legitimate acknowledgements — the Hyperliquid capture
alphabet (``15m``/``1h``) is a *venue* vocabulary translated at a boundary, and
unifying it would create a mapping layer rather than remove one. "It would be
annoying to migrate" is not.

WHY IT LIVES IN ITS OWN LIB
===========================

The alphabet was defined in ``pipeline_engine.validation_shared``, which is the
right home for a *validator* constant and the wrong home for a *vocabulary*:
``libs/data_utils`` cannot import ``pipeline_engine`` (two images — exec-worker
and funding-sync — carry ``data_utils`` without it, and ``data_utils`` lazily
defers ``price_data`` precisely to keep the scientific stack off the
funding-sync CronJob). A vocabulary every layer needs must sit below all of
them, so this module is **stdlib-only, on purpose**: no pandas, no numpy, no
project imports. Keep it that way — the moment it grows a dependency, the
layers that cannot afford that dependency go back to hand-copying.

``pipeline_engine.validation_shared`` re-exports :data:`TIMEFRAME_MINUTES` from
here, so every existing importer and every generated artifact is unchanged.

Quick Start:
    >>> from timeframes import minutes, pandas_offset, to_timedelta
    >>> minutes("3h")
    180
    >>> pandas_offset("3h")
    '3h'
    >>> to_timedelta("5min")
    datetime.timedelta(seconds=300)
"""

from __future__ import annotations

import re
from datetime import timedelta


# ═══════════════════════════════════════════════════════════════════════════
# THE ALPHABET — the one literal in this module
# ═══════════════════════════════════════════════════════════════════════════

#: Canonical token -> bar width in whole minutes. **The single source of
#: truth for what clocks the platform has.** Ordered ascending by width;
#: insertion order is part of the contract (the generated
#: ``validation_tables.json`` preserves it, and the TS side reads that order).
#:
#: Adding a clock is one entry here. Every function below derives from it, and
#: the module-scope consistency check at the bottom of this file refuses a
#: token whose SPELLING disagrees with its declared width — so ``"3h": 200``
#: cannot be committed.
TIMEFRAME_MINUTES: dict[str, int] = {
    "5min": 5,
    "15min": 15,
    "30min": 30,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


# ═══════════════════════════════════════════════════════════════════════════
# THE GRAMMAR — how a token spells a width
# ═══════════════════════════════════════════════════════════════════════════

#: Token grammar: a positive integer followed by a unit. Strict and
#: case-sensitive, matching the bar-offset grammar the DSL validator and its
#: TS mirror already share (``validation_shared._BAR_OFFSET_RE``).
_TOKEN_RE = re.compile(r"^(\d+)(min|h|d)$")

#: Unit -> minutes. The only arithmetic fact about the grammar.
_UNIT_MINUTES: dict[str, int] = {"min": 1, "h": 60, "d": 1440}

#: Unit -> pandas 2.x offset alias. ``min``/``h`` are already pandas spellings;
#: only the day differs in case. Deriving the alias from the unit is what makes
#: ``3h`` work without anyone adding a row: the three drifted offset tables of
#: Q-1108 each had to be edited by hand, and each was edited a different amount.
_PANDAS_UNIT: dict[str, str] = {"min": "min", "h": "h", "d": "D"}

#: Unit -> the legacy pandas alias (``T``/``H``/``D``). Deprecated by pandas
#: 2.2 with a ``FutureWarning`` and removed in 3.0; kept only so a caller
#: pinning historical behaviour can ask for it explicitly rather than
#: hand-writing a fourth table. New code wants :func:`pandas_offset`.
_PANDAS_LEGACY_UNIT: dict[str, str] = {"min": "T", "h": "H", "d": "D"}

#: Unit -> the singular PostgreSQL interval unit. Rendered with an ``s`` when
#: the count is not 1, which reproduces the hand-written interval strings
#: exactly (``30 minutes``, ``1 hour``, ``2 hours``, ``1 day``).
_SQL_UNIT: dict[str, str] = {"min": "minute", "h": "hour", "d": "day"}

#: Unit -> the human display unit, singular.
_LABEL_UNIT: dict[str, str] = {"min": "minute", "h": "hour", "d": "day"}


def _parse(tf: str) -> tuple[int, str]:
    """Split a canonical token into ``(count, unit)``. Raises on anything else.

    Membership in :data:`TIMEFRAME_MINUTES` is checked FIRST, so a
    well-formed-but-unknown token (``"7h"``, ``"1w"``) is refused as loudly as
    a malformed one. Every derivation below goes through here, which is what
    makes "is this a platform clock?" a single question with a single answer
    instead of eleven membership tests written eleven ways.
    """
    if not isinstance(tf, str) or tf not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"Unknown timeframe {tf!r}. "
            f"Valid: {', '.join(TIMEFRAME_TOKENS)}. "
            f"To add a clock, add ONE entry to timeframes.TIMEFRAME_MINUTES — "
            f"every representation derives from it."
        )
    match = _TOKEN_RE.match(tf)
    if match is None:  # pragma: no cover — the consistency check forbids it
        raise ValueError(f"Timeframe token {tf!r} does not match the token grammar")
    return int(match.group(1)), match.group(2)


# ═══════════════════════════════════════════════════════════════════════════
# DERIVED VIEWS OF THE ALPHABET
# ═══════════════════════════════════════════════════════════════════════════

#: Every canonical token, ascending by width. Use this instead of writing a
#: list of tokens.
TIMEFRAME_TOKENS: tuple[str, ...] = tuple(TIMEFRAME_MINUTES)

#: Set form, for membership tests.
VALID_TIMEFRAMES: frozenset[str] = frozenset(TIMEFRAME_MINUTES)

#: Inverse alphabet: width in minutes -> canonical token. Bijective.
MINUTES_TO_TOKEN: dict[int, str] = {m: tf for tf, m in TIMEFRAME_MINUTES.items()}

#: The finest and coarsest clocks the platform has. Derived, so a new rung at
#: either end moves them without an edit.
FINEST_TOKEN: str = TIMEFRAME_TOKENS[0]
COARSEST_TOKEN: str = TIMEFRAME_TOKENS[-1]


# ═══════════════════════════════════════════════════════════════════════════
# DERIVED REPRESENTATIONS — one function per shape a consumer needs
# ═══════════════════════════════════════════════════════════════════════════


def minutes(tf: str) -> int:
    """Bar width in whole minutes. Raises on an unknown token."""
    _parse(tf)
    return TIMEFRAME_MINUTES[tf]


def seconds(tf: str) -> int:
    """Bar width in whole seconds."""
    return minutes(tf) * 60


def milliseconds(tf: str) -> int:
    """Bar width in whole milliseconds — the venue REST/WS time unit."""
    return minutes(tf) * 60_000


def to_timedelta(tf: str) -> timedelta:
    """Bar width as a stdlib :class:`datetime.timedelta`.

    Stdlib rather than ``pd.Timedelta`` so this module stays importable
    without pandas; ``pd.Timedelta`` accepts a ``timedelta`` directly, so a
    pandas caller writes ``pd.Timedelta(to_timedelta(tf))``.
    """
    return timedelta(minutes=minutes(tf))


def pandas_offset(tf: str) -> str:
    """The pandas 2.x offset alias — ``'5min'``, ``'3h'``, ``'1D'``.

    For ``resample``/``date_range``/``Timedelta``. Derived from the token's
    own unit, so every clock in the alphabet has one by construction. This is
    the representation that had drifted into three separate hand tables
    (Q-1108), each missing a different subset, one of which raised ``KeyError``
    on ``3h`` — a clock users can already declare.
    """
    count, unit = _parse(tf)
    return f"{count}{_PANDAS_UNIT[unit]}"


def pandas_offset_legacy(tf: str) -> str:
    """The pre-2.2 pandas alias — ``'5T'``, ``'3H'``, ``'1D'``.

    Deprecated by pandas (``FutureWarning`` since 2.2, removed in 3.0). Exists
    only so a caller that must reproduce a historical string asks for it
    explicitly instead of hand-writing a table of them. Prefer
    :func:`pandas_offset`.
    """
    count, unit = _parse(tf)
    return f"{count}{_PANDAS_LEGACY_UNIT[unit]}"


def postgres_interval(tf: str) -> str:
    """The PostgreSQL interval literal — ``'30 minutes'``, ``'1 hour'``.

    For ``time_bucket()`` and any interval arithmetic. Rendered from the
    token's own unit rather than from minutes: ``interval '1 day'`` and
    ``interval '1440 minutes'`` are NOT interchangeable to Postgres (a day is
    stored in the ``days`` field and buckets differently under a session time
    zone), so the unit is load-bearing and is preserved here.
    """
    count, unit = _parse(tf)
    word = _SQL_UNIT[unit]
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def label(tf: str) -> str:
    """Human display form — ``'5 minutes'``, ``'1 hour'``, ``'1 day'``."""
    count, unit = _parse(tf)
    word = _LABEL_UNIT[unit]
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def bars_per_day(tf: str) -> float:
    """How many bars of width ``tf`` fit in a 24-hour day.

    The number that hand-written docstrings kept spelling as the literal
    ``96`` — true only at ``15min``.
    """
    return 1440 / minutes(tf)


# ═══════════════════════════════════════════════════════════════════════════
# RECOVERING A CLOCK FROM A MEASUREMENT
# ═══════════════════════════════════════════════════════════════════════════


def token_for_minutes(width: int) -> str | None:
    """The canonical token exactly ``width`` minutes wide, or ``None``."""
    return MINUTES_TO_TOKEN.get(width)


def match_minutes(width: float, *, rel_tolerance: float = 0.01) -> str | None:
    """The canonical token whose width is ``width``, or ``None`` if none is.

    For recovering a clock from OBSERVED data — the spacing of an equity curve
    or a weights frame — where the declared clock was not carried alongside.
    The tolerance is RELATIVE so one bound is meaningful at 5 minutes and at a
    day alike.

    Returns ``None`` rather than snapping: a caller that measured something
    which is not a platform clock must be able to say so. Silently rounding an
    unrecognised 7-hour spacing to ``"8h"`` reports a wrong answer
    confidently — see :func:`nearest_token` for the case where snapping IS
    what the caller wants.
    """
    for token, exact in TIMEFRAME_MINUTES.items():
        if abs(width - exact) <= rel_tolerance * exact:
            return token
    return None


def nearest_token(width: float) -> str:
    """The canonical token CLOSEST to ``width`` minutes; ties resolve finer.

    Deliberately total, for display-side callers that must render something
    (a chart picking bars for an equity curve). A caller that needs to know
    whether the measurement WAS a platform clock wants :func:`match_minutes`.
    """
    return min(
        TIMEFRAME_MINUTES,
        key=lambda tf: (abs(TIMEFRAME_MINUTES[tf] - width), TIMEFRAME_MINUTES[tf]),
    )


def is_whole_multiple(coarse: str, fine: str) -> bool:
    """Whether one ``coarse`` bar tiles a whole number of ``fine`` bars.

    The precondition every bucket-aggregation argument rests on: a timeframe
    that is not a whole multiple of its base grain lets a base bar straddle a
    bucket boundary.
    """
    return minutes(coarse) % minutes(fine) == 0


def bars_per_bucket(coarse: str, fine: str) -> int:
    """How many ``fine`` bars one ``coarse`` bar contains. Raises if it is not
    a whole number — the caller's aggregation would be unsound.

    This is the arithmetic the market-data endpoint got wrong by fixing its
    base grain at 15 minutes: ``5 // 15 == 0`` silently reported that a 5min
    bucket holds no bars at all.
    """
    if not is_whole_multiple(coarse, fine):
        raise ValueError(
            f"{coarse} ({minutes(coarse)}min) is not a whole multiple of "
            f"{fine} ({minutes(fine)}min); a {fine} bar would straddle a "
            f"{coarse} bucket boundary."
        )
    return minutes(coarse) // minutes(fine)


# ═══════════════════════════════════════════════════════════════════════════
# CONSISTENCY CHECK — the table cannot disagree with itself
# ═══════════════════════════════════════════════════════════════════════════
#
# Runs at import, over the real table. A token whose SPELLING does not equal
# its declared width ("3h": 200) is refused here rather than producing a
# plausible wrong answer in whichever derivation happens to read it first.
# This is what lets every function above trust `_parse` — the grammar and the
# table are the same fact stated twice, and this is what makes them stay that
# way.
#
# It is a FUNCTION over an argument, not a module-scope loop, so the test can
# hand it a deliberately broken table and watch it raise. A check that can
# only ever run against the one correct table has never been shown to fail.


def validate_alphabet(table: dict[str, int]) -> None:
    """Raise ``ValueError`` unless ``table`` is a well-formed clock alphabet.

    Well-formed means: every token matches the grammar, every token's spelling
    equals its declared width, no two tokens share a width, and the table is
    ordered ascending (the generated ``validation_tables.json`` preserves that
    order and the TS side reads it).
    """
    for token, declared in table.items():
        match = _TOKEN_RE.match(token) if isinstance(token, str) else None
        if match is None:
            raise ValueError(
                f"TIMEFRAME_MINUTES token {token!r} does not match the token "
                f"grammar {_TOKEN_RE.pattern!r}"
            )
        spelled = int(match.group(1)) * _UNIT_MINUTES[match.group(2)]
        if spelled != declared:
            raise ValueError(
                f"TIMEFRAME_MINUTES token {token!r} spells {spelled} minutes "
                f"but declares {declared}"
            )
    widths = list(table.values())
    if len(set(widths)) != len(widths):
        raise ValueError("TIMEFRAME_MINUTES is not bijective — two tokens share a width")
    if widths != sorted(widths):
        raise ValueError(
            "TIMEFRAME_MINUTES must be ordered ascending by width — the generated "
            "validation_tables.json preserves this order and the TS side reads it"
        )


validate_alphabet(TIMEFRAME_MINUTES)
