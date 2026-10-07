"""What an upgrade of a strategy's pins would change — the one owner.

Dollar-volume spec 02 §2 (DV13/DV14, Contract): an upgrade is *move the pin,
then — only if the component's interface changed — recompose*. This module
answers, for a parsed strategy and its component lock, what moving each pin
would do, and moves pins on request. keel-api (``GET lock/status``,
``POST lock/upgrade``, the SDK router) and the agent tools (MCP/chat
``strategy_components_drift`` / ``strategy_components_upgrade``) all call it,
so a drift entry means the same thing on every surface.

It never rewrites a strategy and never persists anything (core-engine-audit
D2/I5: every change is submitted by the user or the agent).

Public API:
    - ``drift_entries(strategy, lock, components=None)`` — one JSON-ready
      dict per drifting pin (the Contract's drift entry):
      ``component``, ``locked_version``, ``latest_version``, ``drift_type``
      (``outdated`` | ``deprecated`` | ``missing`` | ``unknown``),
      ``changes``, ``replacement``, ``interface`` (``params_added``
      ``[{name, required, slot_type}]``, ``params_removed`` ``[name]``),
      ``breaking`` and ``issues_at_target``. A ``deprecated`` entry whose
      pinned version declares them also carries ``replacement_shape``
      (``{head, text, recipe}``), ``known_issue`` (``{id, summary}``) and
      ``upgrade`` (``{mode, questions, lock_changes, result_source?,
      component_lock?}``, the position-layer planner's group payload —
      position-layer spec 04-R27); keyword ``source`` lets a mechanical
      upgrade carry its ``result_source`` and the lock it is valid under.
    - ``bump_pins(strategy, lock, components=None)`` — move the requested
      pins (breaking or not) to latest and validate there. Persists nothing.

``breaking`` is DECIDED, never guessed from changelog text: the strategy is
validated at the lock with ONLY that pin bumped, and the bump is breaking
when that introduces an error the strategy does not already have at its
current lock. ``issues_at_target`` are the issues the bump introduces. A pin
whose target is deprecated for a named successor is always breaking — the
upgrade is a swap to ``replacement``, which only a recomposition can do — and
an ``unknown`` component (not registered at all) is breaking with no target.

Quick Start:
    >>> from pipeline_engine.base.lock_upgrade import bump_pins, drift_entries
    >>> entries = drift_entries(parsed, lock)
    >>> result = bump_pins(parsed, lock, components=["RollingUniverseMask"])
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from pipeline_engine.base.lock import LockDrift, check_lock_drift, upgrade_lock_entries
from pipeline_engine.dsl.spec import StrategyFile


#: Drift types whose pin can be moved to the component's latest version.
BUMPABLE = frozenset({"outdated", "missing"})


def _issue_key(issue: Any) -> tuple[str, str, str, str]:
    """Identity of a validation issue across two validations of one source."""
    return (issue.severity, issue.code, str(issue.location), issue.message)


def _interface_diff(name: str, locked_version: int, latest_version: int) -> dict[str, list]:
    """Params the latest version adds (with ``required`` and the slot type a
    slot-reference param reads) and removes, from the registry. Empty when
    either side is not registered (``missing`` / ``unknown``) or the pin
    does not move (``deprecated``)."""
    from pipeline_engine.base.registry import get_version

    diff: dict[str, list] = {"params_added": [], "params_removed": []}
    if latest_version == locked_version:
        return diff
    old = get_version(name, locked_version)
    new = get_version(name, latest_version)
    if old is None or new is None:
        return diff
    for pname in sorted(set(new.parameters) - set(old.parameters)):
        info = new.parameters[pname]
        slot_type = None
        if info.slot_reference and info.expected_slot_type is not None:
            slot_type = getattr(info.expected_slot_type, "__name__", str(info.expected_slot_type))
        diff["params_added"].append(
            {"name": pname, "required": bool(info.required), "slot_type": slot_type}
        )
    diff["params_removed"] = sorted(set(old.parameters) - set(new.parameters))
    return diff


def drift_entries(
    strategy: StrategyFile,
    lock: dict[str, int],
    components: Collection[str] | None = None,
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """The Contract's drift entries for ``strategy`` pinned at ``lock``.

    Args:
        strategy: The parsed strategy the lock belongs to (not mutated).
        lock: Its component lock (not mutated).
        components: Restrict to these component names; None means every pin.
        source: The strategy's DSL text, when the caller holds it. A
            deprecated entry's ``upgrade`` then carries the verified
            ``result_source`` of a fully mechanical upgrade (spec 04-R27).

    Returns:
        One dict per drifting pin, in ``check_lock_drift`` order (by name).
        Empty when nothing drifts.
    """
    from pipeline_engine.dsl.validator import validate_strategy

    drifts = check_lock_drift(lock)
    if components is not None:
        wanted = set(components)
        drifts = [d for d in drifts if d.component in wanted]
    if not drifts:
        return []

    base = validate_strategy(strategy, lock=lock)
    base_issues = base.all_issues()
    base_keys = {_issue_key(i) for i in base_issues}

    entries: list[dict[str, Any]] = []
    plan_cache: dict[str, Any] = {}
    for d in drifts:
        entry = _entry(d, strategy, lock, base_issues, base_keys, validate_strategy)
        if d.drift_type == "deprecated":
            entry.update(_deprecation_extras(d, strategy, lock, source, plan_cache))
        entries.append(entry)
    return entries


def _deprecation_extras(
    d: LockDrift,
    strategy: StrategyFile,
    lock: dict[str, int],
    source: str | None,
    plan_cache: dict[str, Any],
) -> dict[str, Any]:
    """``replacement_shape`` / ``known_issue`` / ``upgrade`` of a deprecated pin.

    Position-layer spec 04-R27 (Q-2448). Keep-by-omission: a deprecated pin
    with a plain-name replacement and no position group (``RollingNotional
    ProxyMask``) gets nothing, so its entry is byte-identical to before.
    The known issue is the PINNED version's (04-R31: pins, never latest).
    """
    from pipeline_engine.base.registry import get_latest, get_version

    out: dict[str, Any] = {}
    pinned = get_version(d.component, d.locked_version)
    sig = pinned or get_latest(d.component)
    shape = getattr(sig, "replacement_shape", None) if sig is not None else None
    if shape is not None:
        out["replacement_shape"] = {"head": shape.head, "text": shape.text, "recipe": shape.recipe}
    issue = getattr(pinned, "known_issue", None) if pinned is not None else None
    if issue is not None:
        out["known_issue"] = issue.to_dict()
    upgrade = _upgrade_payload(d.component, strategy, lock, source, plan_cache)
    if upgrade is not None:
        out["upgrade"] = upgrade
    return out


def _upgrade_payload(
    component: str,
    strategy: StrategyFile,
    lock: dict[str, int],
    source: str | None,
    plan_cache: dict[str, Any],
) -> dict[str, Any] | None:
    """The planner's view of the groups ``component`` belongs to, or None.

    ``mode`` is the worst of those groups (manual > assisted > mechanical),
    ``questions`` their scripted questions and ``lock_changes`` the pins their
    upgrade moves (``{component, from, to, reason}``, one per component; a
    rule op whose pinned version is not certified on trade values moves to
    the certified one — spec 04-R18's proven exception). ``result_source`` is
    present only when ``source`` is held and EVERY group of the strategy is
    mechanical, so no answer is needed; it is the verified text (04-R18) and
    is omitted, with ``unavailable`` naming the failed check, when
    verification refuses it. Beside it, ``component_lock`` is the evolved
    lock that text was verified under: the text is valid ONLY at that lock
    when a pin moved, so whoever saves ``result_source`` saves that lock
    with it (Q-2448, L3b-fix) — re-evolving the stored lock keeps the old pin.
    """
    from pipeline_engine.dsl.upgrade import (
        UpgradeVerificationError,
        plan_upgrade,
        upgrade_strategy_source,
    )

    if "plan" not in plan_cache:
        plan_cache["plan"] = plan_upgrade(strategy, lock)
    plan = plan_cache["plan"]
    groups = [
        g
        for g in plan.groups
        if g.manager == component or any(r.component == component for r in g.replaces)
    ]
    if not groups:
        return None
    modes = {g.mode for g in groups}
    mode = next(m for m in ("manual", "assisted", "mechanical") if m in modes)
    changes: dict[str, dict[str, Any]] = {}
    for g in groups:
        for c in g.lock_changes:
            changes.setdefault(c.component, c.to_dict())
    payload: dict[str, Any] = {
        "mode": mode,
        "questions": [q.to_dict() for g in groups for q in g.questions],
        "lock_changes": [changes[k] for k in sorted(changes)],
    }
    if source is not None and plan.mode == "mechanical":
        if "result" not in plan_cache:
            # The one engine (04-R21): it returns the verified text AND the
            # lock it verified it under. apply_upgrade returns the text only.
            try:
                plan_cache["result"] = upgrade_strategy_source(source, lock)
            except UpgradeVerificationError as exc:
                plan_cache["result"] = exc
        result = plan_cache["result"]
        if isinstance(result, dict) and isinstance(result.get("source"), str):
            payload["result_source"] = result["source"]
            payload["component_lock"] = result["component_lock"]
        elif isinstance(result, UpgradeVerificationError):
            payload["unavailable"] = result.to_dict().get("check") or "verification"
    return payload


def _entry(
    d: LockDrift,
    strategy: StrategyFile,
    lock: dict[str, int],
    base_issues: list,
    base_keys: set,
    validate_strategy,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "component": d.component,
        "locked_version": d.locked_version,
        "latest_version": d.latest_version,
        "drift_type": d.drift_type,
        "changes": list(d.changes),
        "replacement": d.replacement,
        "interface": _interface_diff(d.component, d.locked_version, d.latest_version),
        "breaking": False,
        "issues_at_target": [],
    }
    if d.drift_type == "deprecated":
        # Nothing newer to pin: the upgrade is the swap to the successor.
        entry["breaking"] = True
        entry["issues_at_target"] = [
            i.to_dict()
            for i in base_issues
            if i.code == "DEPRECATED_COMPONENT" and f"'{d.component}'" in i.message
        ]
        return entry
    if d.drift_type not in BUMPABLE or d.latest_version <= 0:
        # "unknown": no registered target to validate at — a swap, like a
        # deprecation.
        entry["breaking"] = True
        return entry

    target = validate_strategy(strategy, lock={**lock, d.component: d.latest_version})
    introduced = [i for i in target.all_issues() if _issue_key(i) not in base_keys]
    entry["issues_at_target"] = [i.to_dict() for i in introduced]
    entry["breaking"] = d.replacement is not None or any(i.severity == "error" for i in introduced)
    return entry


def bump_pins(
    strategy: StrategyFile,
    lock: dict[str, int],
    components: Collection[str] | None = None,
) -> dict[str, Any]:
    """Move pins to their latest versions and validate there. Persists nothing.

    Bumps every ``outdated`` / ``missing`` pin in ``components`` (all drifting
    pins when None), breaking or not — the "try it" step: the caller sees
    the validation at the new lock and recomposes or saves.

    Returns:
        ``component_lock`` (the bumped lock), ``upgraded``
        (``{name: [from, to]}``), ``valid`` and ``issues`` (the validation at
        the bumped lock), and ``changes`` (the drift entries, computed at the
        ORIGINAL lock, of every requested drifting pin — including
        ``deprecated`` / ``unknown`` ones, which have nothing to bump and say
        what to swap instead).
    """
    from pipeline_engine.dsl.validator import validate_strategy

    changes = drift_entries(strategy, lock, components)
    bumps = {
        e["component"]: e["latest_version"]
        for e in changes
        if e["drift_type"] in BUMPABLE and e["latest_version"] > 0
    }
    new_lock = upgrade_lock_entries(lock, bumps) if bumps else dict(sorted(lock.items()))
    validation = validate_strategy(strategy, lock=new_lock)
    return {
        "component_lock": new_lock,
        "upgraded": {name: [lock[name], version] for name, version in sorted(bumps.items())},
        "valid": validation.valid,
        "issues": [i.to_dict() for i in validation.all_issues()],
        "changes": changes,
    }


__all__ = ["BUMPABLE", "bump_pins", "drift_entries"]
