"""Component version lock — pin, update, and drift-check component versions.

Provides functions to generate, update, and audit version locks for strategies.
A lock is a ``dict[str, int]`` mapping component names to pinned version numbers.

Quick Start:
    >>> from pipeline_engine.base.lock import generate_lock, check_lock_drift
    >>> lock = generate_lock(parsed_strategy)
    >>> drifts = check_lock_drift(lock)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from pipeline_engine.dsl.spec import (
    ComponentRef,
    ParallelSpec,
    PipelineSpec,
    StepSpec,
    StrategyFile,
)


@dataclass(frozen=True)
class LockDrift:
    """A single component whose lock diverges from the registry."""

    component: str
    locked_version: int
    latest_version: int
    drift_type: str  # "outdated" | "missing" | "unknown"
    changes: list[str]


class LockError(Exception):
    """Raised when lock generation or update fails."""


# ═══════════════════════════════════════════════════════════════════════════════
# TREE WALKERS
# ═══════════════════════════════════════════════════════════════════════════════


def walk_component_refs(strategy: StrategyFile) -> Iterator[ComponentRef]:
    """Walk all ComponentRef nodes in a parsed strategy.

    Yields every component reference found in variables, factory definitions,
    and the main pipeline, including inside parallel branches and nested pipelines.
    """
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            yield from _walk_refs_in_pipeline(var.value)

    # Walk factory bodies — components inside factories must also be locked
    for factory in strategy.factories:
        yield from _walk_refs_in_pipeline(factory.body)

    yield from _walk_refs_in_pipeline(strategy.pipeline)


def _walk_refs_in_pipeline(pipeline: PipelineSpec) -> Iterator[ComponentRef]:
    """Walk ComponentRef nodes in a PipelineSpec."""
    for step in pipeline.steps:
        yield from _walk_refs_in_step(step)


def _walk_refs_in_step(step: StepSpec) -> Iterator[ComponentRef]:
    """Walk ComponentRef nodes in a single step."""
    if isinstance(step, ComponentRef):
        yield step
    elif isinstance(step, ParallelSpec):
        for branch_steps in step.branches.values():
            for s in branch_steps:
                yield from _walk_refs_in_step(s)
    elif isinstance(step, PipelineSpec):
        yield from _walk_refs_in_pipeline(step)


# ═══════════════════════════════════════════════════════════════════════════════
# LOCK GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


def generate_lock(strategy: StrategyFile) -> dict[str, int]:
    """Generate a version lock from a parsed strategy.

    Walks all component references in the strategy and pins each to the
    latest available version. Raises on unknown or deprecated components.

    Args:
        strategy: Parsed strategy file.

    Returns:
        Lock mapping component name → version number.

    Raises:
        LockError: If a component is unknown or deprecated.
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY, get_latest
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    lock: dict[str, int] = {}
    for ref in walk_component_refs(strategy):
        if ref.name in lock:
            continue
        if ref.name not in COMPONENT_REGISTRY:
            raise LockError(
                f"Unknown component '{ref.name}' — cannot generate lock. "
                f"Ensure all components are registered."
            )
        sig = get_latest(ref.name)
        if sig is None:
            raise LockError(f"No versions found for component '{ref.name}'.")
        if sig.status == "deprecated":
            raise LockError(
                f"Component '{ref.name}' is deprecated (v{sig.version}). "
                f"Replace it before generating a lock."
            )
        lock[ref.name] = sig.version

    return dict(sorted(lock.items()))


def update_lock(
    strategy: StrategyFile,
    existing_lock: dict[str, int],
) -> dict[str, int]:
    """Update an existing lock: preserve pins, add new, drop removed.

    Components present in both the strategy and existing lock keep their
    pinned version. New components get the latest version. Components no
    longer in the strategy are dropped.

    Args:
        strategy: Parsed strategy file.
        existing_lock: Previous lock to preserve pins from.

    Returns:
        Updated lock mapping.

    Raises:
        LockError: If a locked version no longer exists in the registry,
            or a new component is unknown/deprecated.
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY, get_latest, get_version
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    # Collect all component names referenced in strategy
    referenced: set[str] = set()
    for ref in walk_component_refs(strategy):
        referenced.add(ref.name)

    lock: dict[str, int] = {}
    for name in referenced:
        if name not in COMPONENT_REGISTRY:
            raise LockError(
                f"Unknown component '{name}' — cannot update lock. "
                f"Ensure all components are registered."
            )
        if name in existing_lock:
            # Preserve existing pin — verify it still exists
            pinned = existing_lock[name]
            if get_version(name, pinned) is None:
                raise LockError(
                    f"Locked version {pinned} for component '{name}' no longer exists in registry."
                )
            lock[name] = pinned
        else:
            # New component — pin to latest
            sig = get_latest(name)
            if sig is None:
                raise LockError(f"No versions found for component '{name}'.")
            if sig.status == "deprecated":
                raise LockError(
                    f"Component '{name}' is deprecated (v{sig.version}). Replace it before locking."
                )
            lock[name] = sig.version

    return dict(sorted(lock.items()))


# ═══════════════════════════════════════════════════════════════════════════════
# DRIFT CHECK
# ═══════════════════════════════════════════════════════════════════════════════


def check_lock_drift(lock: dict[str, int]) -> list[LockDrift]:
    """Compare a lock against the current registry to detect drift.

    Returns a list of LockDrift entries for components that are outdated,
    missing from the registry, or have newer versions available.

    Args:
        lock: Lock mapping to check.

    Returns:
        List of drift entries (empty if lock is fully current).
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY, get_latest, get_version
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    drifts: list[LockDrift] = []
    for name, pinned in sorted(lock.items()):
        if name not in COMPONENT_REGISTRY:
            drifts.append(
                LockDrift(
                    component=name,
                    locked_version=pinned,
                    latest_version=0,
                    drift_type="unknown",
                    changes=["Component not found in registry"],
                )
            )
            continue

        sig = get_version(name, pinned)
        if sig is None:
            latest = get_latest(name)
            drifts.append(
                LockDrift(
                    component=name,
                    locked_version=pinned,
                    latest_version=latest.version if latest else 0,
                    drift_type="missing",
                    changes=[f"Locked version {pinned} not found in registry"],
                )
            )
            continue

        latest = get_latest(name)
        if latest and latest.version > pinned:
            # Collect changelog entries for versions after the pinned one
            changes = []
            all_versions = COMPONENT_REGISTRY.get(name, {})
            for ver in sorted(all_versions.keys()):
                if ver > pinned:
                    ver_sig = all_versions[ver]
                    entry = ver_sig.changelog.get(ver, f"v{ver}")
                    changes.append(f"v{ver}: {entry}")
            drifts.append(
                LockDrift(
                    component=name,
                    locked_version=pinned,
                    latest_version=latest.version,
                    drift_type="outdated",
                    changes=changes,
                )
            )

    return drifts


__all__ = [
    "LockDrift",
    "LockError",
    "check_lock_drift",
    "generate_lock",
    "update_lock",
    "walk_component_refs",
]
