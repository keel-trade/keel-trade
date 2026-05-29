"""Step categories and phase ordering constants (SDK-safe).

Pure enum + constants with no runtime dependencies. Importable by both
the monorepo and the SDK without pulling in Context, Slot, or types.
"""

from __future__ import annotations

from enum import Enum


class StepCategory(Enum):
    """Step categories for phase ordering validation.

    14 pipeline categories (ordered) + SLOT_OP (allowed anywhere).
    """

    DATA_LOADER = "data_loader"
    DATA_TRANSFORM = "data_transform"
    UNIVERSE_FILTER = "universe_filter"
    INDICATOR = "indicator"
    SIGNAL_TRANSFORM = "signal_transform"
    SIGNAL_COMPOSER = "signal_composer"
    REGIME_DETECTOR = "regime_detector"
    FORECAST_MAPPER = "forecast_mapper"
    FORECAST_COMPOSER = "forecast_composer"
    POSITION_SIZER = "position_sizer"
    RISK_MANAGER = "risk_manager"
    POSITION_MANAGER = "position_manager"
    EXECUTOR = "executor"
    REPORTER = "reporter"
    SLOT_OP = "slot_op"


PHASE_ORDER: list[StepCategory] = [
    StepCategory.DATA_LOADER,
    StepCategory.DATA_TRANSFORM,
    StepCategory.UNIVERSE_FILTER,
    StepCategory.INDICATOR,
    StepCategory.SIGNAL_TRANSFORM,
    StepCategory.SIGNAL_COMPOSER,
    StepCategory.REGIME_DETECTOR,
    StepCategory.FORECAST_MAPPER,
    StepCategory.FORECAST_COMPOSER,
    StepCategory.POSITION_SIZER,
    StepCategory.RISK_MANAGER,
    StepCategory.POSITION_MANAGER,
    StepCategory.EXECUTOR,
    StepCategory.REPORTER,
]
"""Valid phase ordering. Each category can only follow categories to its left.
SLOT_OP is allowed anywhere and not included in this list."""

PHASE_GROUPS: list[list[StepCategory]] = [
    [StepCategory.DATA_LOADER, StepCategory.DATA_TRANSFORM],
    [StepCategory.UNIVERSE_FILTER],
    [
        StepCategory.INDICATOR,
        StepCategory.SIGNAL_TRANSFORM,
        StepCategory.SIGNAL_COMPOSER,
        StepCategory.REGIME_DETECTOR,
    ],
    [StepCategory.FORECAST_MAPPER, StepCategory.FORECAST_COMPOSER],
    [StepCategory.POSITION_SIZER, StepCategory.RISK_MANAGER, StepCategory.POSITION_MANAGER],
    [StepCategory.EXECUTOR, StepCategory.REPORTER],
]
"""Coarser phase groups for validation. Categories within the same group can
appear in any order; only cross-group backward jumps are violations."""

PHASE_GROUP_NAMES: list[str] = ["DATA", "UNIVERSE", "SIGNAL", "FORECAST", "POSITION", "OUTPUT"]
"""Human-readable names for each phase group."""


__all__ = [
    "StepCategory",
    "PHASE_ORDER",
    "PHASE_GROUPS",
    "PHASE_GROUP_NAMES",
]
