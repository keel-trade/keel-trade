"""Which known issues and deprecations does a component lock carry (spec 04-R31).

THE one owner of the question "does this strategy pin a component with a
known issue?". The deploy / update / resume refusal (D-42: "upgrade this
strategy first"), the strategy read's derived ``deprecations`` field, the
deployment reads and the web Updates panel all call these two functions and
nothing else, so their answers cannot drift.

**The answer belongs to the PIN.** Each ``(name, version)`` of the lock is
resolved with :func:`get_version`; the latest version's status is never
read. An upgraded strategy pins no known-issue version and therefore carries
none, and a fixed newer version of a component never clears the issue of a
strategy still pinned below it.

SDK-safe: imports only the registry data model (no numpy/pandas), so the
keel-trade wheel can vendor it beside ``registry_types``.

Quick Start:
    >>> from pipeline_engine.base.known_issues import known_issues_for_lock
    >>> hits = known_issues_for_lock({"MaxDrawdownStopLoss": 1, "SMA": 1})
    >>> [h.issue.id for h in hits]  # once the flip has landed
    ['Q-2448']
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pipeline_engine.base.registry_types import (
    ComponentSignature,
    KnownIssue,
    _pin_resolution_error,
    get_version,
)


@dataclass(frozen=True)
class KnownIssueHit:
    """One pinned component version that carries a known issue."""

    component: str
    version: int
    issue: KnownIssue

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "version": self.version,
            "known_issue": self.issue.to_dict(),
        }


@dataclass(frozen=True)
class DeprecationHit:
    """One pinned component version that is deprecated (04-R24's row)."""

    component: str
    version: int
    known_issue: KnownIssue | None
    #: ``replacement_shape.text`` when the deprecation names a shape, else the
    #: plain successor name, else ``None`` (no single successor).
    replacement_text: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "version": self.version,
            "known_issue": self.known_issue.to_dict() if self.known_issue else None,
            "replacement_text": self.replacement_text,
        }


def _pinned(component_versions: Mapping[str, int]) -> list[tuple[str, int, ComponentSignature]]:
    """Resolve every pin, in name order. An unresolvable pin RAISES.

    A lock that names a version the registry no longer has cannot be judged
    (it might be the very version with the issue), so it is never skipped:
    the spec 01 §2.1 structured error says which pin and what to do.
    """
    out: list[tuple[str, int, ComponentSignature]] = []
    for name in sorted(component_versions):
        version = component_versions[name]
        sig = get_version(name, int(version))
        if sig is None:
            raise _pin_resolution_error(name, int(version))
        out.append((name, int(version), sig))
    return out


def replacement_text(sig: ComponentSignature) -> str | None:
    """The one agent-facing line naming what replaces ``sig`` (04-R24/R25)."""
    if sig.replacement_shape is not None:
        return sig.replacement_shape.text
    return sig.replacement


def known_issues_for_lock(component_versions: Mapping[str, int]) -> list[KnownIssueHit]:
    """Every pinned version in ``component_versions`` that declares a known issue.

    Ordered by component name. Empty for a lock with no known-issue pin —
    including every upgraded strategy and every strategy that pins only a
    deprecated component WITHOUT a known issue (PSM, TLRE, SPM: D-23).
    """
    return [
        KnownIssueHit(component=name, version=version, issue=sig.known_issue)
        for name, version, sig in _pinned(component_versions)
        if sig.known_issue is not None
    ]


def deprecations_for_lock(component_versions: Mapping[str, int]) -> list[DeprecationHit]:
    """Every pinned version in ``component_versions`` whose status is deprecated."""
    return [
        DeprecationHit(
            component=name,
            version=version,
            known_issue=sig.known_issue,
            replacement_text=replacement_text(sig),
        )
        for name, version, sig in _pinned(component_versions)
        if sig.status == "deprecated"
    ]


def required_issue_ids(component_versions: Mapping[str, int]) -> list[str]:
    """The distinct known-issue ids a lock carries, sorted (the D-42 gate's set)."""
    return sorted({hit.issue.id for hit in known_issues_for_lock(component_versions)})


__all__ = [
    "DeprecationHit",
    "KnownIssueHit",
    "deprecations_for_lock",
    "known_issues_for_lock",
    "replacement_text",
    "required_issue_ids",
]
