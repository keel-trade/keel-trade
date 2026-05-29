"""Registry loader — SDK stub.

The libs/ version walks `components/` and imports every module to
populate `COMPONENT_REGISTRY`. The SDK doesn't bundle the components
package (heavy pandas/numpy/ta-lib deps), so this stub is a no-op
that ASSUMES `keel.data.registry.load_registry()` was already called
to populate the registry from bundled JSON.

If `ensure_registry_loaded()` is called BEFORE the JSON load, that's
a caller bug — we raise a clear RuntimeError instead of silently
validating against an empty registry.
"""

from __future__ import annotations


def ensure_registry_loaded() -> None:
    """No-op in SDK; registry is hydrated from bundled JSON elsewhere.

    Asserts COMPONENT_REGISTRY is non-empty — if it is, the caller
    skipped `keel.data.registry.load_registry()` and would silently
    validate against an empty registry.
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY

    if not COMPONENT_REGISTRY:
        raise RuntimeError(
            "COMPONENT_REGISTRY is empty in SDK env. The SDK loads the "
            "registry from bundled JSON via `keel.data.registry.load_registry()` "
            "— call that before using the validator or lock-generation "
            "code paths. Validators must not run against an empty registry."
        )


__all__ = ["ensure_registry_loaded"]
