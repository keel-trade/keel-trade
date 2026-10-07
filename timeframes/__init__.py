"""The platform's clock alphabet, and every representation derived from it.

One entry in :data:`TIMEFRAME_MINUTES` is the whole cost of adding a clock:
minutes, seconds, milliseconds, a ``timedelta``, a pandas offset alias, a
PostgreSQL interval and a display label are all COMPUTED from it. Consumers
derive; they never re-enumerate. See ``core.py`` for why this module exists,
why it is stdlib-only, and what the census gate does about a new hand-written
alphabet.

Quick Start:
    >>> from timeframes import minutes, pandas_offset, postgres_interval
    >>> minutes("3h"), pandas_offset("3h"), postgres_interval("3h")
    (180, '3h', '3 hours')
"""

from .core import (
    COARSEST_TOKEN,
    FINEST_TOKEN,
    MINUTES_TO_TOKEN,
    TIMEFRAME_MINUTES,
    TIMEFRAME_TOKENS,
    VALID_TIMEFRAMES,
    bars_per_bucket,
    bars_per_day,
    is_whole_multiple,
    label,
    match_minutes,
    milliseconds,
    minutes,
    nearest_token,
    pandas_offset,
    pandas_offset_legacy,
    postgres_interval,
    seconds,
    to_timedelta,
    token_for_minutes,
)


__all__ = [
    # The alphabet and its derived views
    "TIMEFRAME_MINUTES",
    "TIMEFRAME_TOKENS",
    "VALID_TIMEFRAMES",
    "MINUTES_TO_TOKEN",
    "FINEST_TOKEN",
    "COARSEST_TOKEN",
    # Derived representations
    "minutes",
    "seconds",
    "milliseconds",
    "to_timedelta",
    "pandas_offset",
    "pandas_offset_legacy",
    "postgres_interval",
    "label",
    "bars_per_day",
    # Recovering a clock from a measurement
    "token_for_minutes",
    "match_minutes",
    "nearest_token",
    # Grain arithmetic
    "is_whole_multiple",
    "bars_per_bucket",
]
