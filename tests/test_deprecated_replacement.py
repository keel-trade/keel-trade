"""The SDK's local validation names a deprecated component's successor.

``registry_metadata.json`` carries ``replacement`` (RollingNotionalProxyMask →
RollingDollarVolumeMask, dollar-volume DV6a), and keel-api's validator reads
it. ``scripts/build_data.py`` did not carry the field into the wheel's
``keel/data/registry.json``, so the SDK / MCP local validator — hydrated from
that file — fell back to "Consider replacing … with a supported alternative"
while the server named the successor (dollar-volume spec 02 requires the
name).

Two arms: the behaviour (local validation names the successor) and the class
(every successor the live registry declares reaches the wheel's registry, so
the next deprecation cannot drop it either).

Seed (2026-09-30): removing the ``replacement`` emit in ``build_data.py`` and
regenerating reds both arms (the suggestion falls back; the census names
RollingNotionalProxyMask); the census's non-vacuity count stays >= 1 through
the seed because it reads the LIVE registry, which the seed does not touch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from keel.tools.local import strategy_validate


SDK_ROOT = Path(__file__).resolve().parents[1]
MONO_ROOT = SDK_ROOT.parents[2]
LIVE_REGISTRY = (
    MONO_ROOT / "libs" / "pipeline_engine" / "dsl" / "fixtures" / "registry_metadata.json"
)
WHEEL_REGISTRY = SDK_ROOT / "keel" / "data" / "registry.json"

DEPRECATED_SOURCE = """Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")
Pipeline([
    PriceDataLoader(),
    Store("ohlcv"),
    RollingNotionalProxyMask(window="24h"),
    Store("liquid"),
    Load("ohlcv"),
    ROC(period=24),
    ApplyUniverseMask(mask_slot="liquid"),
    EqualWeightSizer(target_leverage=1.0),
    FillNaN(fill_value=0.0),
])
"""


def test_local_validation_names_the_successor():
    result = strategy_validate(DEPRECATED_SOURCE)
    deprecated = [i for i in result["issues"] if i["code"] == "DEPRECATED_COMPONENT"]
    assert len(deprecated) == 1, result["issues"]
    assert deprecated[0]["suggestion"] == (
        "Consider replacing 'RollingNotionalProxyMask' with 'RollingDollarVolumeMask'."
    )
    # A deprecation stays a warning: the strategy is valid and runs.
    assert result["valid"] is True


@pytest.mark.skipif(
    not LIVE_REGISTRY.is_file(),
    reason="monorepo-only: the live registry lives in libs/ (absent in the public mirror)",
)
def test_every_declared_successor_reaches_the_wheel_registry():
    live = json.loads(LIVE_REGISTRY.read_text())
    declared = {name: e["replacement"] for name, e in live.items() if e.get("replacement")}
    # Non-vacuity: the live registry really declares at least one successor.
    assert "RollingNotionalProxyMask" in declared
    wheel = {c["name"]: c for c in json.loads(WHEEL_REGISTRY.read_text())["components"]}
    carried = {name: wheel.get(name, {}).get("replacement") for name in declared}
    assert carried == declared
