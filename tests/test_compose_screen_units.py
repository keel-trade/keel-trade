"""Compose reads a screen's unit sentences back (screeners L13 §2a.1).

The validator states, per screen output, what a value means
(`ValidationResult.units`); compose's dry run and save carry them under
`validation.units` so an agent quotes the unit instead of guessing. A strategy
has no outputs, so its validation block is unchanged.
"""

from __future__ import annotations

from keel.tools.outcomes import strategy_compose


SCREEN = """Globals(target_timeframe="1h")
Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")
Outputs(slots=["ret_1d"])
Pipeline([PriceDataLoader(), ROC(period=24), Store("ret_1d")])
"""

STRATEGY = """Globals(target_timeframe="1d")
Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")
Execution(rebalance="buffered", buffer_threshold=0.2, buffer_mode="relative", rebalance_method="to_edge")
Pipeline([
    PriceDataLoader(),
    ROC(period=20),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="s")
"""


def test_a_screen_carries_its_unit_sentences_and_a_strategy_none():
    """# SEED (L13): drop `"units": list(...)` from `keel/tools/local.py`'s
    strategy_validate return — the screen arm reds. Reverse the edit."""
    screen = strategy_compose._try_local_validate(SCREEN)
    strategy = strategy_compose._try_local_validate(STRATEGY)
    # Non-vacuous: both validated.
    assert screen["ok"], screen
    assert strategy["ok"], strategy
    assert [u["output"] for u in screen["units"]] == ["ret_1d"]
    assert screen["units"][0]["unit"] and screen["units"][0]["sentence"]
    assert strategy["units"] == []


def test_the_sdk_validator_states_the_unit_the_server_does():
    """Q-3637: the bundled registry carried no `unit` / `unit_by_param`, so the
    SDK's walk (the hosted MCP server's, which runs no `libs/components`)
    read every output as `unknown` while keel-api read `percent`. The arm
    above passed regardless: `"unknown"` is truthy.

    # SEED (2026-11-05): drop `unit=comp.get("unit"),` from the vendored
    # `pipeline_engine/base/registry_types.py` hydrator — this reds with
    # `unknown`; the arm above (truthiness only) stays green, which is the
    # gap this closes. Reverse the edit."""
    screen = strategy_compose._try_local_validate(SCREEN)
    assert screen["units"][0]["unit"] == "percent", screen["units"]
    assert "5.0 = 5%" in screen["units"][0]["sentence"]
    funding = strategy_compose._try_local_validate(
        SCREEN.replace(
            'PriceDataLoader(), ROC(period=24), Store("ret_1d")',
            'FundingDataLoader(agg="last"), Scale(by=8760, unit="rate_per_year"), Store("ret_1d")',
        )
    )
    # unit_by_param (the loader's agg) feeds Scale's known-input rule.
    assert funding["units"][0]["unit"] == "rate_per_year", funding["units"]
