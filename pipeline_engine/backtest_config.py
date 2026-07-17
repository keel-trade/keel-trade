"""Canonical financial configuration for every backtest boundary and engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
        description="Starting capital in USD (default: 10000).",
    )
    fees: float = Field(
        default=0.00045,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        description="Per-trade fee rate as a decimal (default: 0.00045).",
    )
    slippage: float = Field(
        default=0.00045,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        description="Per-trade adverse slippage as a decimal (default: 0.00045).",
    )
    leverage: float = Field(
        default=20.0,
        gt=0.0,
        le=100.0,
        allow_inf_nan=False,
        description="Maximum leverage cap; must be greater than 0 and at most 100 (default: 20).",
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


__all__ = ["BacktestConfig", "adapt_legacy_initial_capital"]
