"""Base component system — SDK subset.

Only exports StepCategory and registry types needed by the validator.
Runtime base classes (DataSource, SignalTransform, etc.) are excluded.
"""

from .categories import StepCategory
from .registry_types import (
    COMPONENT_REGISTRY,
    ComponentSignature,
    RegistryParamInfo,
    load_registry_from_json,
)

__all__ = [
    "StepCategory",
    "ComponentSignature",
    "RegistryParamInfo",
    "COMPONENT_REGISTRY",
    "load_registry_from_json",
]
