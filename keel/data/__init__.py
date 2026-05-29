"""Bundled data artifacts for the Keel SDK.

Provides access to registry, reference, patterns, knowledge,
examples, and templates without importing the components package.
"""

from keel.data.registry import get_component_detail, load_registry, search_components


__all__ = [
    "load_registry",
    "search_components",
    "get_component_detail",
]
