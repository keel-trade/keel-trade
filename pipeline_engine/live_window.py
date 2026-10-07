"""The live evaluation look-back: the one constant (position-layer spec 02-R9, X-45).

Live re-evaluates a strategy over its last ``LIVE_LOOKBACK_DAYS`` days at
every bar. The validator refuses a windowed rule at or above it
(``WINDOW_EXCEEDS_LIVE_LOOKBACK``); eval-worker and eval-shard size their
evaluation window with it; ``validation_tables.json`` carries it for the TS
validator as ``live_lookback_days``. Stdlib only.

Quick Start:
    >>> from pipeline_engine.live_window import LIVE_LOOKBACK_DAYS
    >>> LIVE_LOOKBACK_DAYS
    365
"""

from __future__ import annotations


LIVE_LOOKBACK_DAYS = 365

__all__ = ["LIVE_LOOKBACK_DAYS"]
