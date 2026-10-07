"""Canonical financial configuration for every backtest boundary and engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


#: The most gross leverage (sum of |weight| on a bar) the platform runs, in a
#: backtest and live alike (Q-2440, founder ruling 2026-10-05). A bar whose
#: target weights sum past it is scaled down pro-rata to it, and the backtest
#: says so in a run warning. ``LeverageCap(max_leverage=...)`` accepts at most
#: this. ONE owner: the live planner (``execution.engine_planner``, whose
#: images carry no pipeline_engine) mirrors it, pinned equal by its test.
#: ``BacktestConfig.leverage`` is a separate account-margin model.
PLATFORM_MAX_GROSS_LEVERAGE = 10.0


class BacktestConfig(BaseModel):
    """Validated backtest financial settings with existing engine defaults.

    ``as_overrides`` preserves which values the caller explicitly supplied so
    API, database, and queue payloads can remain sparse while both execution
    engines resolve the same defaults from this model.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    init_cash: float = Field(
        default=10_000.0,
        ge=0.0,
        le=1_000_000_000.0,
        allow_inf_nan=False,
        description="Starting capital, in dollars (default 10000).",
    )
    fees: float = Field(
        default=0.00045,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        description="Fee rate per fill, as a decimal (default 0.00045).",
    )
    slippage: float = Field(
        default=0.00045,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        description="Adverse slippage per fill, as a decimal (default 0.00045).",
    )
    leverage: float = Field(
        default=20.0,
        gt=0.0,
        le=100.0,
        allow_inf_nan=False,
        description=(
            "Account margin cap (max 100). Positions are sized by the pipeline; "
            "this only bounds borrowing. Default 20."
        ),
    )

    def as_overrides(self) -> dict[str, float]:
        """Return only settings explicitly supplied by the caller."""
        return self.model_dump(exclude_unset=True)


def adapt_legacy_initial_capital(config: Mapping[str, Any]) -> dict[str, Any]:
    """Map the SDK's one documented legacy alias to the canonical key.

    This is intentionally opt-in at the SDK boundary. API, queue, chat, and
    executor inputs accept canonical keys only.
    """
    adapted = dict(config)
    if "initial_capital" not in adapted:
        return adapted
    if "init_cash" in adapted:
        raise ValueError("backtest config cannot contain both initial_capital and init_cash")
    adapted["init_cash"] = adapted.pop("initial_capital")
    return adapted


__all__ = ["PLATFORM_MAX_GROSS_LEVERAGE", "BacktestConfig", "adapt_legacy_initial_capital"]
