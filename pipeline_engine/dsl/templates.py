"""Strategy templates and default directory configuration.

Provides the canonical template definitions and strategy directory path used by
both the CLI (``keel pipeline new``) and MCP tools (``strategy_new``).

Quick Start:
    >>> from pipeline_engine.dsl.templates import TEMPLATES, get_strategy_dir
    >>> get_strategy_dir()
    PosixPath('/home/user/.keel/strategies')
    >>> sorted(TEMPLATES.keys())
    ['basic', 'carry', 'momentum', 'multi_factor']
"""

from __future__ import annotations

from pathlib import Path


DEFAULT_STRATEGY_DIR = Path.home() / ".keel" / "strategies"


def get_strategy_dir() -> Path:
    """Return the default strategy directory."""
    return DEFAULT_STRATEGY_DIR


TEMPLATES = {
    "basic": {
        "name": "basic",
        "description": "Single indicator -> forecast -> size",
        "content": (
            """\
Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=[], resolved_at="")

Execution(rebalance='every_bar')

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="{name}")
"""
        ),
    },
    "momentum": {
        "name": "momentum",
        "description": "EWMA with cross-sectional processing",
        "content": (
            """\
Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=[], resolved_at="")

Execution(rebalance='every_bar')

xs_post = Pipeline([
    CrossSectionalZScore(),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
], name="xs_post_process")

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    EWMA(window=8),
    xs_post,
    ForecastWeightNormalizer(target_leverage=1.0),
], name="{name}")
"""
        ),
    },
    "multi_factor": {
        "name": "multi_factor",
        "description": "Parallel branches with ensemble",
        "content": (
            """\
Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=[], resolved_at="")

Execution(rebalance='every_bar')

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    {{
        "momentum": [
            ROC(period=8),
            ForecastScaler(avg_abs_target=10.0),
        ],
        "mean_reversion": [
            RSI(period=14),
            ForecastScaler(avg_abs_target=10.0),
        ],
    }},
    ForecastCombiner(weights="equal"),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="{name}")
"""
        ),
    },
    "carry": {
        "name": "carry",
        "description": "Funding-carry — short positive funding, long negative funding",
        "content": (
            """\
Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=[], resolved_at="")

Execution(rebalance='every_bar')

# Funding carry: short instruments with positive funding (collect from longs),
# long instruments with negative funding (collect from shorts). VolatilityStandardizer
# reads OHLCV from the "ohlcv_1d" slot to scale the signal by realised volatility.
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    Store("ohlcv_1d"),
    FundingDataLoader(),
    SignalResampler(method="mean"),
    NegateTransform(),
    CrossSectionalZScore(),
    VolatilityStandardizer(signal_type="percentage", ohlcv_slot="ohlcv_1d"),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="{name}")
"""
        ),
    },
}

__all__ = [
    "DEFAULT_STRATEGY_DIR",
    "TEMPLATES",
    "get_strategy_dir",
]
