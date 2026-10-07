"""Component version lock — pin, evolve, and drift-check component versions.

A strategy's "lock" is a ``dict[str, int]`` mapping component names to pinned
version numbers. Package-lock semantics: a strategy compiled with v1 of a
component stays on v1 until the user explicitly upgrades — regardless of
what versions ship later.

Public API:
    - ``evolve_lock(prev_lock, strategy)`` — derive the working lock for a
      strategy from the previous lock + current source. Preserve existing
      pins; auto-add new components at latest; drop removed components.
      The ONE source of lock evolution. Same function whether ``prev_lock``
      is ``{}`` (brand-new strategy) or an existing lock from a previous
      commit.
    - ``upgrade_lock_entries(lock, upgrades)`` — explicit user-driven version
      bump. Each entry in ``upgrades`` must reference a component already in
      the lock AND a target version that exists in the registry. This is
      what backs the components-upgrade UX.
    - ``check_lock_drift(lock)`` — UX helper. Compares a lock to the current
      registry, returns entries that are outdated / deprecated / missing /
      unknown. Informational only — never feeds back into validate / compile.

Quick Start:
    >>> from pipeline_engine.base.lock import evolve_lock, check_lock_drift
    >>> lock = evolve_lock({}, parsed_strategy)   # fresh
    >>> lock = evolve_lock(prev_lock, parsed_strategy)  # subsequent edit
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

# LockError is DEFINED in pipeline_engine.exceptions (spec 01 T3: the shared
# structured-error module, so ComponentVersionError can subclass both
# CompileError and LockError without an import cycle). This module remains
# its canonical import path — same class identity, re-exported here.
from pipeline_engine.exceptions import LockError


@dataclass(frozen=True)
class LockDrift:
    """A single component whose lock diverges from the registry."""

    component: str
    locked_version: int
    latest_version: int
    drift_type: str  # "outdated" | "deprecated" | "missing" | "unknown"
    changes: list[str]
    #: The named successor when the component's LATEST version is deprecated
    #: for one (``ComponentSignature.replacement``) — on a ``deprecated``
    #: entry always, on an ``outdated`` one when the newer version it points
    #: at is itself deprecated. None otherwise.
    replacement: str | None = None


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


def evolve_lock(
    prev_lock: dict[str, int],
    strategy: StrategyFile,
) -> dict[str, int]:
    """Evolve a component lock to match a strategy's current source.

    The single source of lock evolution. Package-lock semantics:

    - Components in source AND in ``prev_lock`` → preserve the pinned version.
    - Components in source but NOT in ``prev_lock`` → pin to ``get_latest()``.
    - Components in ``prev_lock`` but NOT in source → drop from the lock.

    Pass ``prev_lock={}`` for a brand-new strategy with no prior lock.

    A deprecated latest pins like any other version (Q-0684, ruled
    2026-08-26): deprecation is advice, and validity belongs to the rule
    catalog, not to lock generation. The catalog's ``DEPRECATED_COMPONENT``
    warning (SUSPICIOUS — "still resolves and runs") carries the
    upgrade steer; hard retirement is ``COMPONENT_NOT_RUNNABLE``. Refusing
    here used to convert into a location-less ``UNKNOWN_COMPONENT`` error
    on the validator's auto-lock path, suppressing every real diagnostic.

    Args:
        prev_lock: Previous lock (or ``{}`` for a fresh derivation).
        strategy: Parsed strategy file.

    Returns:
        Evolved lock mapping component name → version number.

    Raises:
        LockError: If a referenced component is unknown, or a preserved
            pin's version no longer exists in the registry. A pin that
            can't be preserved is the user's signal to run
            ``upgrade_lock_entries`` and migrate.
    """
    from pipeline_engine.base.registry import COMPONENT_REGISTRY, get_latest, get_version
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    referenced: set[str] = set()
    for ref in walk_component_refs(strategy):
        referenced.add(ref.name)

    lock: dict[str, int] = {}
    for name in referenced:
        if name not in COMPONENT_REGISTRY:
            raise LockError(
                f"Unknown component '{name}' — cannot evolve lock. "
                f"Ensure all components are registered."
            )
        if name in prev_lock:
            pinned = prev_lock[name]
            if get_version(name, pinned) is None:
                raise LockError(
                    f"Locked version {pinned} for component '{name}' no longer exists "
                    f"in the registry. Use upgrade_lock_entries to migrate the pin."
                )
            lock[name] = pinned
        else:
            sig = get_latest(name)
            if sig is None:
                raise LockError(f"No versions found for component '{name}'.")
            lock[name] = sig.version

    # Position-layer spec 03-R50: a registered factory's pinned version names
    # the components its expansion compiles to; pin those too (preserving a
    # prior pin, else latest), so compile never meets an unpinned expansion
    # step. Template components are never factories (refused
    # by position_factories_test), so one pass suffices.
    from pipeline_engine.base.registry_types import is_factory
    from pipeline_engine.binding import template_components

    for name in sorted(referenced):
        sig = get_version(name, lock[name])
        if sig is None or not is_factory(sig):
            continue
        for part in template_components(sig.factory_expansion):
            if part in lock:
                continue
            if part not in COMPONENT_REGISTRY:
                raise LockError(
                    f"Factory '{name}' v{sig.version} expands to unknown component "
                    f"'{part}' — cannot evolve lock. Ensure all components are registered."
                )
            if part in prev_lock and get_version(part, prev_lock[part]) is not None:
                lock[part] = prev_lock[part]
            else:
                lock[part] = get_latest(part).version

    return dict(sorted(lock.items()))


def upgrade_lock_entries(
    lock: dict[str, int],
    upgrades: dict[str, int],
) -> dict[str, int]:
    """Explicit user-driven version bump.

    Returns a new lock with the requested entries bumped to the specified
    target versions. Used by the components-upgrade UX path; never invoked
    silently by validate / compile.

    Args:
        lock: The current lock to base the upgrade on.
        upgrades: ``{component_name: target_version}``. Each entry must:
            (a) name a component already in ``lock`` (cannot upgrade something
                the strategy doesn't reference), and
            (b) reference a target version that exists in the registry.

    Returns:
        New lock with the requested upgrades applied.

    Raises:
        LockError: If an upgrade target doesn't exist in the registry, or
            a component being upgraded isn't in the current lock.
    """
    from pipeline_engine.base.registry import get_version
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    new_lock = dict(lock)
    for name, target in upgrades.items():
        if name not in lock:
            raise LockError(
                f"Cannot upgrade '{name}': not in current lock. "
                f"Add the component to the strategy source first."
            )
        if get_version(name, target) is None:
            raise LockError(f"Cannot upgrade '{name}' to v{target}: version not in registry.")
        new_lock[name] = target

    return dict(sorted(new_lock.items()))


# ═══════════════════════════════════════════════════════════════════════════════
# DRIFT CHECK
# ═══════════════════════════════════════════════════════════════════════════════


def check_lock_drift(lock: dict[str, int]) -> list[LockDrift]:
    """Compare a lock against the current registry to detect drift.

    Returns a list of LockDrift entries, one per pin that is:

    - ``outdated`` — a newer version is registered (``changes`` lists the
      changelog entries in between; ``replacement`` is set when that newer
      version is itself deprecated for a named successor);
    - ``deprecated`` — pinned AT the component's latest version, which is
      deprecated for a named successor (``replacement``). Nothing newer
      exists, so the upgrade is a swap to the successor (Q-2201). A
      deprecation that names no successor has no upgrade to offer and is not
      listed; the validator's ``DEPRECATED_COMPONENT`` warning covers it;
    - ``missing`` — the pinned version is not registered;
    - ``unknown`` — the component is not registered at all.

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
        successor = (
            latest.replacement if latest is not None and latest.status == "deprecated" else None
        )
        if latest and latest.version == pinned and successor:
            drifts.append(
                LockDrift(
                    component=name,
                    locked_version=pinned,
                    latest_version=pinned,
                    drift_type="deprecated",
                    changes=[f"deprecated: replaced by {successor}"],
                    replacement=successor,
                )
            )
        elif latest and latest.version > pinned:
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
                    replacement=successor,
                )
            )

    return drifts


__all__ = [
    "LockDrift",
    "LockError",
    "check_lock_drift",
    "evolve_lock",
    "upgrade_lock_entries",
    "walk_component_refs",
]
