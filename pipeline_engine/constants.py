"""Shared constants for the pipeline engine.

Provides sentinel values used across multiple modules to avoid identity
divergence from independently created sentinels.
"""

# Sentinel for "no default provided" / "required parameter".
# Distinct from None so we can tell "required param" from "default is None".
MISSING = object()

# Valid target timeframes for strategy pipelines.
# This is the single source of truth — the runtime TimeframeResampler only
# supports these values.  Update this set when adding new timeframe support.
VALID_TIMEFRAMES = frozenset(
    {
        "15min",
        "30min",
        "1h",
        "2h",
        "3h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
    }
)


__all__ = ["MISSING", "VALID_TIMEFRAMES"]
