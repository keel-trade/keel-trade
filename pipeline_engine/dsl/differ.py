"""Structural diff between two strategies.

Parses both strategies, flattens each to canonical step lists,
then compares to find added, removed, and changed steps.

Quick Start:
    >>> from pipeline_engine.dsl.differ import diff_strategies
    >>> result = diff_strategies(source_a=old, source_b=new)
    >>> print(result["summary"])
"""

from __future__ import annotations

from typing import Any

from pipeline_engine.dsl import parse_strategy
from pipeline_engine.dsl.parser import DSLParseError
from pipeline_engine.dsl.spec import (
    ComponentRef,
    FactoryCallSpec,
    ParallelSpec,
    PipelineSpec,
    SlotExtractSpec,
    SlotLoadSpec,
    SlotStoreSpec,
    SlotStoreValueSpec,
    StepSpec,
    VariableRef,
)


def diff_strategies(
    *,
    source_a: str,
    source_b: str,
) -> dict[str, Any]:
    """Compute structural diff between two strategy sources.

    Args:
        source_a: First strategy DSL source (the "before").
        source_b: Second strategy DSL source (the "after").

    Returns:
        Dict with added, removed, changed steps, and a summary.
    """
    try:
        parsed_a = parse_strategy(source_a)
    except DSLParseError as e:
        return {"error": f"Failed to parse source_a: {e}"}

    try:
        parsed_b = parse_strategy(source_b)
    except DSLParseError as e:
        return {"error": f"Failed to parse source_b: {e}"}

    flat_a = _flatten_steps(parsed_a.pipeline.steps, "pipeline")
    flat_b = _flatten_steps(parsed_b.pipeline.steps, "pipeline")

    # Match by component name, ignoring position/path.
    # This means moving a component into a parallel branch is not a change.
    groups_a: dict[str, list[dict[str, Any]]] = {}
    groups_b: dict[str, list[dict[str, Any]]] = {}
    for s in flat_a:
        groups_a.setdefault(s["component"], []).append(s)
    for s in flat_b:
        groups_b.setdefault(s["component"], []).append(s)

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for comp in sorted(set(groups_a) | set(groups_b)):
        old_list = list(groups_a.get(comp, []))
        new_list = list(groups_b.get(comp, []))

        # First pass: consume exact param matches (no change)
        matched_old: set[int] = set()
        matched_new: set[int] = set()
        for i, old in enumerate(old_list):
            for j, new in enumerate(new_list):
                if j not in matched_new and old["params"] == new["params"]:
                    matched_old.add(i)
                    matched_new.add(j)
                    break

        remaining_old = [s for i, s in enumerate(old_list) if i not in matched_old]
        remaining_new = [s for j, s in enumerate(new_list) if j not in matched_new]

        # Second pass: pair remaining 1:1 as param changes
        pairs = min(len(remaining_old), len(remaining_new))
        for k in range(pairs):
            params_a = remaining_old[k]["params"]
            params_b = remaining_new[k]["params"]
            param_changes = []
            for pk in sorted(set(params_a) | set(params_b)):
                old_val = params_a.get(pk)
                new_val = params_b.get(pk)
                if old_val != new_val:
                    param_changes.append({"param": pk, "old": old_val, "new": new_val})
            changed.append(
                {
                    "path": remaining_new[k]["path"],
                    "component": comp,
                    "param_changes": param_changes,
                }
            )

        # Leftovers
        removed.extend(remaining_old[pairs:])
        added.extend(remaining_new[pairs:])

    # Factory diff
    factories_a = {f.name for f in parsed_a.factories}
    factories_b = {f.name for f in parsed_b.factories}

    # Summary
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if removed:
        parts.append(f"{len(removed)} removed")
    if changed:
        parts.append(f"{len(changed)} changed")
    if not parts:
        parts.append("identical")

    factory_diff = []
    for name in sorted(factories_b - factories_a):
        factory_diff.append({"name": name, "change": "added"})
    for name in sorted(factories_a - factories_b):
        factory_diff.append({"name": name, "change": "removed"})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "factory_changes": factory_diff,
        "summary": " | ".join(parts),
    }


def _flatten_steps(
    steps: list[StepSpec],
    prefix: str,
) -> list[dict[str, Any]]:
    """Flatten a step list to canonical (path, component, params) tuples.

    Exhaustive over every ``StepSpec`` union member — an unhandled member
    raises instead of silently vanishing from the diff (walker-exhaustiveness
    convention, core-engine-audit F8: ``Extract`` steps used to fall through
    here and semantics-changing Extract edits diffed as "identical").
    """
    flat: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        path = f"{prefix}.step[{i}]"

        if isinstance(step, ComponentRef):
            flat.append(
                {
                    "path": path,
                    "component": step.name,
                    "params": _safe_params(step.params),
                }
            )
        elif isinstance(step, SlotStoreSpec):
            flat.append(
                {
                    "path": path,
                    "component": "Store",
                    "params": {"slot_name": step.slot_name},
                }
            )
        elif isinstance(step, SlotStoreValueSpec):
            flat.append(
                {
                    "path": path,
                    "component": "StoreValue",
                    "params": {"slot_name": step.slot_name, "value": step.value},
                }
            )
        elif isinstance(step, SlotLoadSpec):
            flat.append(
                {
                    "path": path,
                    "component": "Load",
                    "params": {"slot_name": step.slot_name},
                }
            )
        elif isinstance(step, SlotExtractSpec):
            flat.append(
                {
                    "path": path,
                    "component": "Extract",
                    "params": {"key": step.key},
                }
            )
        elif isinstance(step, FactoryCallSpec):
            flat.append(
                {
                    "path": path,
                    "component": f"factory:{step.name}",
                    "params": _safe_params(step.args),
                }
            )
        elif isinstance(step, VariableRef):
            flat.append(
                {
                    "path": path,
                    "component": f"var:{step.name}",
                    "params": {},
                }
            )
        elif isinstance(step, ParallelSpec):
            for branch_name, branch_steps in sorted(step.branches.items()):
                branch_path = f"{path}.{branch_name}"
                flat.extend(_flatten_steps(branch_steps, branch_path))
        elif isinstance(step, PipelineSpec):
            sub_name = step.name or f"sub_{i}"
            flat.extend(_flatten_steps(step.steps, f"{path}.{sub_name}"))
        else:
            # Unhandled StepSpec member ⇒ raise. A silent fall-through here
            # DROPS the step from the diff (the F8 Extract bug). Any new
            # StepSpec member must get an explicit branch above; the
            # exhaustiveness test in differ_test.py instantiates every
            # member through this walker.
            raise TypeError(
                f"Unhandled StepSpec member {type(step).__name__} in "
                f"differ._flatten_steps — StepSpec walkers must be exhaustive; "
                f"add an explicit branch for every member."
            )

    return flat


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """Convert params to a safe comparable form."""
    result = {}
    for k, v in params.items():
        if isinstance(v, VariableRef):
            result[k] = f"${v.name}"
        elif isinstance(v, (str, int, float, bool, type(None))):
            result[k] = v
        elif isinstance(v, dict):
            result[k] = _safe_params(v)
        elif isinstance(v, (list, tuple)):
            result[k] = [_safe_params({"_": x})["_"] if isinstance(x, dict) else x for x in v]
        else:
            result[k] = str(v)
    return result


__all__ = [
    "diff_strategies",
]
