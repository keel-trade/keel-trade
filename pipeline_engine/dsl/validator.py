"""DSL strategy validator — 9-pass validation against ComponentRegistry.

Validates a parsed StrategyFile without constructing Python objects.
Uses ComponentRegistry metadata for type flow, parameter, and slot checking.

Quick Start:
    >>> from pipeline_engine.dsl.validator import validate_strategy
    >>> result = validate_strategy(parsed_strategy)
    >>> print(result.explain())
"""

from __future__ import annotations

import copy
import difflib
import math
import re
import sys
from typing import Any, Iterator, Literal

from pipeline_engine.base.registry import ParamTier
from pipeline_engine.base.step import PHASE_GROUP_NAMES
from pipeline_engine.constants import VALID_TIMEFRAMES
from pipeline_engine.dsl.spec import (
    MISSING,
    ComponentRef,
    ExecutionSpec,
    FactoryCallSpec,
    GlobalsSpec,
    ParallelSpec,
    PipelineSpec,
    SlotExtractSpec,
    SlotLoadSpec,
    SlotStoreSpec,
    SlotStoreValueSpec,
    StepSpec,
    StrategyFile,
    UniverseSpec,
    VariableAssignment,
    VariableRef,
)
from pipeline_engine.validation_shared import (
    PHASE_INDEX,
    TIMEFRAME_MINUTES,
    TYPE_TRANSITIONS,
    TypeFlowEntry,
    ValidationIssue,
    ValidationResult,
    is_compatible,
    param_accepts_numeric,
    param_display_type,
    parse_bar_offset_minutes,
    type_name,
    type_to_transition_key,
    validate_resample_config,
)


_GENERIC_TOKEN_NAMES = {"transform", "series", "signal", "data", "value"}
_CAMEL_TOKEN_RE = re.compile(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|$)|[a-z]+|\d+")


def _camel_tokens(name: str) -> list[str]:
    """Split a camelCase component name into lowercase tokens.

    `FillNaN` → ['fill', 'nan']; `RollingZScoreTransform` → ['rolling', 'z', 'score', 'transform'].
    """
    return [m.lower() for m in _CAMEL_TOKEN_RE.findall(name)]


def _suggest_component_matches(name: str, registry_names: list[str]) -> list[str]:
    """Suggest up to 3 registry names for an unknown component name.

    Hybrid scoring designed to surface semantically-close matches even when
    a difflib character-sequence ratio is dominated by a shared generic
    suffix (e.g. 'FillNATransform' → 'FillNaN' should beat unrelated
    '*Transform' names that only match on the suffix).

      score = meaningful_token_overlap * 0.5 + difflib_ratio + substring_boost

    - meaningful_token_overlap: count of shared camelCase tokens excluding
      generic suffixes (Transform/Series/Signal/Data/Value). Each shared
      meaningful token outweighs ~0.5 ratio points.
    - difflib_ratio: standard character-sequence similarity (handles typos).
    - substring_boost: +0.3 if either name (lowercased) contains the other.

    Returns names with score >= 0.6 AND within 0.3 of the top score, capped
    at 3 total. Empty list when no candidate clears the bar — callers should
    surface a "no close match" hint rather than a misleading guess.
    """
    if not registry_names:
        return []
    user_meaningful = {
        t for t in _camel_tokens(name) if t not in _GENERIC_TOKEN_NAMES
    }
    name_lower = name.lower()
    scored: list[tuple[float, str, int, float]] = []  # score, name, overlap, sub
    for reg_name in registry_names:
        reg_tokens = set(_camel_tokens(reg_name))
        overlap = len(user_meaningful & reg_tokens)
        ratio = difflib.SequenceMatcher(None, name_lower, reg_name.lower()).ratio()
        reg_lower = reg_name.lower()
        # Substring boost only when the shorter string is substantial — short
        # accidental substrings ('ATR' inside 'DropNATransform') are noise.
        shorter_len = min(len(name_lower), len(reg_lower))
        substr = (
            0.3
            if shorter_len >= 5
            and (name_lower in reg_lower or reg_lower in name_lower)
            else 0.0
        )
        # Filter: when there's no semantic signal (no shared token, no substring),
        # require a typo-level ratio (>=0.85). Otherwise generic-suffix matches
        # ('*Transform') flood the suggestions with bad guesses.
        if overlap == 0 and substr == 0.0 and ratio < 0.85:
            continue
        scored.append((overlap * 0.5 + ratio + substr, reg_name, overlap, substr))
    if not scored:
        return []
    scored.sort(reverse=True)
    out: list[str] = []
    top_score = scored[0][0]
    for score, rn, _ov, _sub in scored:
        if score < 0.6:
            break
        if out and score < top_score - 0.3:
            break
        out.append(rn)
        if len(out) >= 3:
            break
    return out


def _is_slot_compatible(stored_type: type, expected_type: type) -> bool:
    """Lenient type check for slot reads.

    Slots are untyped storage at runtime.  The slot_params declarations
    often use SignalSeries as a generic "DataFrame data" type even when
    the actual stored data is WeightSeries or ForecastSeries.  All of
    these are NewType wrappers over DataFrame and interchangeable at
    runtime.

    This function first tries strict ``is_compatible``, then falls back
    to comparing the NewType base types so that sibling NewTypes sharing
    the same ``__supertype__`` (e.g. WeightSeries ↔ SignalSeries, both
    wrapping DataFrame) are treated as compatible.
    """
    if is_compatible(stored_type, expected_type):
        return True
    # Same-base NewTypes are compatible for slot reads
    stored_base = getattr(stored_type, "__supertype__", stored_type)
    expected_base = getattr(expected_type, "__supertype__", expected_type)
    return stored_base is expected_base


def _format_location(loc) -> str:
    """Format a SourceLocation as 'line N, context' for agent-friendly error locations."""
    if hasattr(loc, "line") and loc.line is not None:
        return f"line {loc.line}, {loc.context}"
    return loc.context


def _group_by_severity(
    issues: list[ValidationIssue],
) -> tuple[list[ValidationIssue], list[ValidationIssue], list[ValidationIssue]]:
    """Single-pass grouping of issues into (errors, warnings, info)."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    info: list[ValidationIssue] = []
    for issue in issues:
        if issue.severity == "error":
            errors.append(issue)
        elif issue.severity == "warning":
            warnings.append(issue)
        elif issue.severity == "info":
            info.append(issue)
    return errors, warnings, info


def validate_strategy(
    strategy: StrategyFile,
    lock: dict[str, int] | None = None,
    production_mode: bool = False,
) -> ValidationResult:
    """Validate a parsed StrategyFile against the component registry.

    Runs 9 validation passes:
    1. Variable and factory resolution
    2. Name collision check
    3. Factory expansion
    4. Name resolution (component lookup)
    5. Parameter validation
    6. Type flow validation
    7. Phase ordering
    8. Slot validation
    9. Globals, Universe, and Declaration References

    Args:
        strategy: Parsed strategy file.
        lock: Component version lock. Three modes:
            - dict: Use the provided lock as-is (production path; chat-api
              and keel-api always pass an explicit lock).
            - None: Auto-generate a lock from the strategy using latest
              versions (convenience path for `/v1/strategies/validate`,
              tests, and ad-hoc validation). If auto-generation fails,
              the error is raised — callers must handle it or pass an
              explicit lock.
            - {} (empty dict): Validate against the full latest registry
              with no version pinning. Useful for tests that don't care
              about lock semantics. Explicit opt-in — distinct from
              `None` (which still tries auto-generation).
        production_mode: When True, promotes `UNRESOLVED_UNIVERSE` and
            `STALE_UNIVERSE` from warnings to errors. Used by deploy and
            backtest submit endpoints to refuse strategies that can't run.
            Editor / WIP paths leave this False so users can save unfinished
            strategies. Default False keeps existing callers' behavior intact.
    """
    from pipeline_engine.base.lock import generate_lock
    from pipeline_engine.base.registry import (
        COMPONENT_REGISTRY,
        _build_effective_registry,
        get_latest,
    )
    try:
        from pipeline_engine.registry_loader import ensure_registry_loaded
        ensure_registry_loaded()
    except ImportError:
        pass  # SDK mode: registry loaded from JSON before calling validator

    # Full registry view (all latest) — needed for passes 2 & 4 which must
    # check against ALL known component names, not just locked ones.
    full_registry = {
        name: sig for name in COMPONENT_REGISTRY if (sig := get_latest(name)) is not None
    }

    # Auto-generate lock if not provided. `generate_lock` raises
    # `LockError` when the strategy references unknown components — that's
    # not an internal bug, it's a legitimate validation failure we want to
    # surface to the caller as a structured `ValidationIssue` rather than
    # bubble up as an exception. Catch ONLY LockError (the known failure
    # shape); any other exception (real bug) propagates. Tests that want
    # to bypass auto-gen pass `lock={}` explicitly.
    from pipeline_engine.base.lock import LockError

    lock_gen_issues: list[ValidationIssue] = []
    if lock is None:
        try:
            lock = generate_lock(strategy)
        except LockError as e:
            # Surface as structured issue; validation continues with the
            # full latest registry so passes 2 + 4 can also catch the
            # unknown component(s) with line locations. Attach a suggestion
            # at this site too — pass 4 will emit a parallel issue with a
            # line location, but downstream consumers that only read the
            # first UNKNOWN_COMPONENT should still get useful guidance.
            full_registry_names = list(full_registry.keys())
            # Extract a name from the LockError text — best-effort, falls
            # back to a generic suggestion if we can't parse it.
            err_text = str(e)
            match = re.search(r"'([A-Za-z_][A-Za-z0-9_]*)'", err_text)
            suggestion: str | None = None
            if match:
                bad_name = match.group(1)
                matches = _suggest_component_matches(bad_name, full_registry_names)
                if matches:
                    suggestion = f"Did you mean: {', '.join(matches)}?"
                else:
                    suggestion = (
                        f"No close match for '{bad_name}'. "
                        f"Use `strategy_components_search` (chat) or "
                        f"`keel components list` to find the right component."
                    )
            lock_gen_issues.append(
                ValidationIssue(
                    severity="error",
                    code="UNKNOWN_COMPONENT",
                    message=err_text,
                    location=None,
                    suggestion=suggestion,
                )
            )
            lock = None

    # Build effective registry from lock for passes 5-9. A lock pointing
    # at a non-existent version surfaces as INVALID_VERSION_LOCK (emitted
    # by _validate_names below) rather than an uncaught LockError —
    # parity with TS pass4-names so the AI sees a structured issue, not
    # a crash.
    invalid_lock_keys: set[str] = set()
    if lock is not None:
        # Lazy import to break the dsl ↔ base.lock circular import.
        from pipeline_engine.base.lock import LockError

        try:
            registry = _build_effective_registry(lock)
        except LockError:
            from pipeline_engine.base.registry import get_all_versions

            invalid_lock_keys = {
                name
                for name, ver in lock.items()
                if ver not in (get_all_versions(name) or {})
            }
            safe_lock = {k: v for k, v in lock.items() if k not in invalid_lock_keys}
            registry = _build_effective_registry(safe_lock) if safe_lock else full_registry
    else:
        registry = full_registry

    # Seed with any lock-generation errors so they surface in the final
    # result. Passes 2 + 4 still run with the full latest registry below
    # and will report the same unknown-component issues with line
    # locations attached.
    issues: list[ValidationIssue] = list(lock_gen_issues)
    type_flow: list[TypeFlowEntry] = []
    slot_types: dict[str, type] = {}

    # Pass 1: Variable and factory resolution
    _validate_references(strategy, issues)

    # Pass 2: Name collision check (uses full registry — all known components)
    _validate_name_collisions(strategy, full_registry, issues)

    # Pass 3: Factory expansion
    expanded = _expand_factories(strategy, issues)

    # Only continue to registry-based passes if no structural errors
    structural_errors, structural_warnings, structural_info = _group_by_severity(issues)
    if structural_errors:
        return ValidationResult(
            valid=False,
            errors=structural_errors,
            warnings=structural_warnings,
            info=structural_info,
            type_flow=type_flow,
        )

    # Pass 4: Name resolution (uses full registry — all known components)
    _validate_names(expanded, full_registry, issues, component_lock=lock)

    # Short-circuit if name resolution found unknown components
    name_errors = [i for i in issues if i.code == "UNKNOWN_COMPONENT"]
    if name_errors:
        ne, nw, ni = _group_by_severity(issues)
        return ValidationResult(
            valid=False,
            errors=ne,
            warnings=nw,
            info=ni,
            type_flow=type_flow,
        )

    # Pass 5: Parameter validation
    _validate_params(expanded, registry, issues)

    # Pass 6: Type flow validation
    _validate_type_flow(expanded, registry, issues, type_flow, slot_types)

    # Pass 7: Phase ordering
    _validate_phase_ordering(expanded, registry, issues)

    # Pass 8: Slot validation
    _validate_slots(expanded, registry, issues, slot_types)

    # Pass 9: Globals, Universe, and declaration reference validation
    _validate_declarations(strategy, expanded, registry, issues, production_mode=production_mode)

    errors, warnings, info = _group_by_severity(issues)

    # Build pipeline summary from type flow
    pipeline_summary = _build_pipeline_summary(type_flow)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        info=info,
        type_flow=type_flow,
        slot_types=slot_types,
        pipeline_summary=pipeline_summary,
    )


def _build_pipeline_summary(type_flow: list[TypeFlowEntry]) -> str:
    """Build a human-readable pipeline summary from type flow entries.

    Produces a string like: PriceDataLoader -> EWMACrossover(8,32) -> VolStd -> VolSize(0.12)
    Excludes slot operations (Store/Load) from the summary.
    """
    step_names = []
    for entry in type_flow:
        if entry.category == "slot_op":
            continue
        step_names.append(entry.step)
    return " -> ".join(step_names)


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 1: Variable and factory resolution
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_references(strategy: StrategyFile, issues: list[ValidationIssue]) -> None:
    """Pass 1: Check all VariableRef and FactoryCallSpec names resolve."""
    # Build definitions map: name -> line where defined
    definitions: dict[str, int] = {}
    definition_order: list[str] = []

    for factory in strategy.factories:
        definitions[factory.name] = factory.location.line
        definition_order.append(factory.name)

    for var in strategy.variables:
        definitions[var.name] = var.location.line
        definition_order.append(var.name)

    factory_param_sets: dict[str, set[str]] = {
        f.name: {p.name for p in f.params} for f in strategy.factories
    }

    def _check_ref(name: str, use_line: int, location) -> None:
        """Check a single name reference."""
        loc_str = _format_location(location) if hasattr(location, "line") else location
        if name not in definitions:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="UNDEFINED_VARIABLE",
                    message=f"Undefined reference '{name}'.",
                    location=loc_str,
                    suggestion=f"Define '{name}' before using it.",
                )
            )
            return

        def_line = definitions[name]
        if def_line > use_line or (def_line == use_line and use_line > 0):
            # Forward references are non-blocking — factories/variables are
            # resolved by name, not definition order.  Graph-converted specs
            # have all locations at line 0, so we also skip the degenerate
            # 0 >= 0 case (both defined and used at synthetic line 0).
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="FORWARD_REFERENCE",
                    message=f"Forward reference to '{name}' (defined at line {def_line}).",
                    location=loc_str,
                    suggestion=f"Move the definition of '{name}' before this usage.",
                )
            )

    def _walk_refs_in_steps(
        steps: list[StepSpec],
        context: str,
        use_line_base: int,
        factory_params: set[str] | None = None,
    ) -> None:
        """Walk step list checking all variable/factory references."""
        for step in steps:
            use_line = step.location.line if hasattr(step, "location") else use_line_base
            _walk_refs_in_step(step, context, use_line, factory_params)

    def _walk_refs_in_step(
        step: StepSpec,
        context: str,
        use_line: int,
        factory_params: set[str] | None = None,
    ) -> None:
        """Walk a single step checking references."""
        # Factory bodies are closures — variables are captured at call time,
        # not definition time. Skip forward-reference checks inside factories.
        ref_line = sys.maxsize if factory_params is not None else use_line

        if isinstance(step, VariableRef):
            # Inside factory body: skip refs matching factory param names
            if factory_params and step.name in factory_params:
                return
            _check_ref(step.name, ref_line, step.location)

        elif isinstance(step, ComponentRef):
            # Check VariableRef in params
            for pname, pval in step.params.items():
                if isinstance(pval, VariableRef):
                    if factory_params and pval.name in factory_params:
                        continue
                    _check_ref(pval.name, ref_line, pval.location)

        elif isinstance(step, FactoryCallSpec):
            _check_ref(step.name, ref_line, step.location)
            # Check VariableRef in factory args
            for aname, aval in step.args.items():
                if isinstance(aval, VariableRef):
                    if factory_params and aval.name in factory_params:
                        continue
                    _check_ref(aval.name, ref_line, aval.location)

        elif isinstance(step, ParallelSpec):
            for branch_name, branch_steps in step.branches.items():
                _walk_refs_in_steps(
                    branch_steps, f"{context}.branch[{branch_name}]", use_line, factory_params
                )

        elif isinstance(step, PipelineSpec):
            _walk_refs_in_steps(step.steps, context, use_line, factory_params)

    # Check factory bodies
    for factory in strategy.factories:
        param_names = factory_param_sets[factory.name]
        _walk_refs_in_steps(
            factory.body.steps,
            f"factory[{factory.name}]",
            factory.location.line,
            factory_params=param_names,
        )

    # Check variable values (Pipeline VariableRef in steps)
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            _walk_refs_in_steps(var.value.steps, f"var[{var.name}]", var.location.line)
        elif isinstance(var.value, VariableRef):
            _check_ref(var.value.name, var.location.line, var.value.location)

    # Check main pipeline
    _walk_refs_in_steps(strategy.pipeline.steps, "pipeline", strategy.pipeline.location.line)


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 2: Name collision check
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_name_collisions(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Pass 2: Check DSL names don't collide with component names."""
    factory_names = {f.name for f in strategy.factories}

    for var in strategy.variables:
        if var.name in registry:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="NAME_COLLISION",
                    message=f"Variable '{var.name}' collides with registered component '{var.name}'.",
                    location=_format_location(var.location),
                    suggestion="Rename the variable to avoid collision.",
                )
            )
        if var.name in factory_names:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="NAME_COLLISION",
                    message=f"Variable '{var.name}' collides with factory '{var.name}' (ambiguous).",
                    location=_format_location(var.location),
                    suggestion="Use distinct names for variables and factories.",
                )
            )

    for factory in strategy.factories:
        if factory.name in registry:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="NAME_COLLISION",
                    message=f"Factory '{factory.name}' collides with registered component '{factory.name}'.",
                    location=_format_location(factory.location),
                    suggestion="Rename the factory to avoid collision.",
                )
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 3: Factory expansion
# ═══════════════════════════════════════════════════════════════════════════════


def _expand_factories(strategy: StrategyFile, issues: list[ValidationIssue]) -> StrategyFile:
    """Pass 3: Expand all FactoryCallSpec into PipelineSpec."""
    factory_map = {f.name: f for f in strategy.factories}

    def _expand_step(step: StepSpec) -> StepSpec:
        if isinstance(step, FactoryCallSpec):
            factory = factory_map.get(step.name)
            if factory is None:
                # Will be caught by pass 1 or pass 4
                return step

            # Check params
            required = [p.name for p in factory.params if p.default is MISSING]
            available = {p.name for p in factory.params}

            # Check for missing required params
            for req in required:
                if req not in step.args:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="FACTORY_MISSING_PARAM",
                            message=f"Factory '{step.name}' missing required parameter '{req}'.",
                            location=_format_location(step.location),
                            suggestion=f"Add {req}=<value> to the call.",
                        )
                    )
                    return step

            # Check for unknown params
            for arg_name in step.args:
                if arg_name not in available:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="FACTORY_UNKNOWN_PARAM",
                            message=f"Factory '{step.name}' has no parameter '{arg_name}'. "
                            f"Available: {sorted(available)}.",
                            location=_format_location(step.location),
                            suggestion=f"Remove '{arg_name}' or use one of: {sorted(available)}.",
                        )
                    )
                    return step

            # Build substitution map: param_name -> value
            substitutions: dict[str, Any] = {}
            for param in factory.params:
                if param.name in step.args:
                    substitutions[param.name] = step.args[param.name]
                elif param.default is not MISSING:
                    substitutions[param.name] = param.default
                # else: already errored above

            # Deep-copy factory body and substitute
            expanded_body = copy.deepcopy(factory.body)
            _substitute_params(expanded_body, substitutions)

            # Update location context and preserve factory call info
            expanded_body.location = step.location
            if expanded_body.name is None:
                # Auto-generate name from factory name + args
                arg_parts = [
                    f"{k}={v}" for k, v in step.args.items() if not isinstance(v, VariableRef)
                ]
                expanded_body.name = (
                    f"{step.name}_{'_'.join(arg_parts)}" if arg_parts else step.name
                )

            return _expand_steps_in(expanded_body)

        elif isinstance(step, ParallelSpec):
            new_branches = {}
            for branch_name, branch_steps in step.branches.items():
                new_branches[branch_name] = [_expand_step(s) for s in branch_steps]
            return ParallelSpec(branches=new_branches, location=step.location)

        elif isinstance(step, PipelineSpec):
            return _expand_steps_in(step)

        return step

    def _expand_steps_in(pipeline: PipelineSpec) -> PipelineSpec:
        new_steps = [_expand_step(s) for s in pipeline.steps]
        return PipelineSpec(steps=new_steps, name=pipeline.name, location=pipeline.location)

    # Expand variables
    new_variables = []
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            expanded_value = _expand_steps_in(var.value)
            new_variables.append(
                VariableAssignment(name=var.name, value=expanded_value, location=var.location)
            )
        else:
            new_variables.append(var)

    # Expand main pipeline
    expanded_pipeline = _expand_steps_in(strategy.pipeline)

    return StrategyFile(
        metadata=strategy.metadata,
        factories=strategy.factories,
        variables=new_variables,
        pipeline=expanded_pipeline,
    )


def _substitute_params(pipeline: PipelineSpec, substitutions: dict[str, Any]) -> None:
    """Replace VariableRef nodes matching factory param names with values."""
    for i, step in enumerate(pipeline.steps):
        if isinstance(step, VariableRef):
            if step.name in substitutions:
                val = substitutions[step.name]
                if isinstance(val, VariableRef):
                    pipeline.steps[i] = val
                # Literal values can't be steps — leave as-is (validation will catch)

        elif isinstance(step, ComponentRef):
            for pname, pval in step.params.items():
                if isinstance(pval, VariableRef) and pval.name in substitutions:
                    step.params[pname] = substitutions[pval.name]

        elif isinstance(step, ParallelSpec):
            for branch_steps in step.branches.values():
                temp = PipelineSpec(steps=branch_steps, name=None, location=step.location)
                _substitute_params(temp, substitutions)

        elif isinstance(step, PipelineSpec):
            _substitute_params(step, substitutions)

        elif isinstance(step, FactoryCallSpec):
            for aname, aval in step.args.items():
                if isinstance(aval, VariableRef) and aval.name in substitutions:
                    step.args[aname] = substitutions[aval.name]


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 4: Name resolution
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_names(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    component_lock: dict[str, int] | None = None,
) -> None:
    """Pass 4: Check all ComponentRef.name exist in COMPONENT_REGISTRY.

    Also validates ``component_lock`` entries: each locked version must
    correspond to a real registered version of the component. Otherwise
    emits INVALID_VERSION_LOCK (mirrors TS pass4-names so the editor and
    server agree).
    """
    from pipeline_engine.base.registry import get_all_versions

    registry_names = list(registry.keys())
    for ref in _walk_component_refs(strategy):
        if ref.name not in registry:
            suggestions = _suggest_component_matches(ref.name, registry_names)
            if suggestions:
                suggestion_text = f"Did you mean: {', '.join(suggestions)}?"
            else:
                suggestion_text = (
                    f"No close match for '{ref.name}'. "
                    f"Use `strategy_components_search` (chat) or "
                    f"`keel components list` to find the right component."
                )
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="UNKNOWN_COMPONENT",
                    message=f"Unknown component '{ref.name}'.",
                    location=_format_location(ref.location),
                    suggestion=suggestion_text,
                )
            )
        else:
            # Warn on deprecated components
            sig = registry[ref.name]
            if getattr(sig, "status", None) == "deprecated":
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="DEPRECATED_COMPONENT",
                        message=f"Component '{ref.name}' is deprecated and may be removed in a future version.",
                        location=_format_location(ref.location),
                        suggestion=f"Consider replacing '{ref.name}' with a supported alternative.",
                    )
                )

            # INVALID_VERSION_LOCK — verify the locked version exists.
            if component_lock and ref.name in component_lock:
                locked_version = component_lock[ref.name]
                try:
                    versions = get_all_versions(ref.name) or {}
                except Exception:
                    versions = {}
                if locked_version not in versions:
                    latest = getattr(sig, "version", None)
                    if latest is not None:
                        hint = (
                            f"Available versions: latest is {latest}. "
                            f"Update the lock or remove version pin."
                        )
                    else:
                        hint = f"Remove the version lock for '{ref.name}'."
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="INVALID_VERSION_LOCK",
                            message=f"Component '{ref.name}' is locked to version "
                            f"{locked_version}, which does not exist.",
                            location=_format_location(ref.location),
                            suggestion=hint,
                        )
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 5: Parameter validation
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_params(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Pass 5: Validate component parameters against registry."""
    for ref in _walk_component_refs(strategy):
        sig = registry.get(ref.name)
        if not sig:
            continue  # Already errored in pass 4

        reg_params = sig.parameters

        # Check required params (skip infra — they're injected at runtime)
        for pname, pinfo in reg_params.items():
            if pinfo.tier == ParamTier.INFRA:
                continue
            if pinfo.required and pname not in ref.params:
                default_hint = ""
                if pinfo.suggestions:
                    default_hint = f" (e.g. {pname}={pinfo.suggestions[0]!r})"
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="MISSING_PARAM",
                        message=f"Component '{ref.name}' missing required parameter '{pname}'.",
                        location=_format_location(ref.location),
                        suggestion=f"Add {pname}=<value>{default_hint}.",
                    )
                )

        # Check unknown params — show all params grouped by tier
        for pname in ref.params:
            if pname not in reg_params:
                available_strategy = sorted(
                    p for p, pi in reg_params.items() if pi.tier == ParamTier.STRATEGY
                )
                available_infra = sorted(
                    p for p, pi in reg_params.items() if pi.tier == ParamTier.INFRA
                )
                msg_parts = [f"Component '{ref.name}' has no parameter '{pname}'."]
                msg_parts.append(f" Strategy params: {available_strategy}.")
                if available_infra:
                    msg_parts.append(f" Infra params: {available_infra}.")
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="UNKNOWN_PARAM",
                        message="".join(msg_parts),
                        location=_format_location(ref.location),
                        suggestion=f"Remove '{pname}' or use one of: {available_strategy}.",
                    )
                )
                continue

            pinfo = reg_params[pname]

            pval = ref.params[pname]

            # Skip type checking for VariableRef (can't check statically)
            if isinstance(pval, VariableRef):
                continue

            # Type checking — uses param_display_type for user-visible labels
            # and isinstance against the unwrapped non-None members of the
            # declared type. Numeric interop (int satisfies any float-typed
            # param, incl. Optional[float]) matches Python runtime laxness.
            if pinfo.type_ is not None and pval is not None:
                try:
                    from typing import Any as TypingAny

                    from pipeline_engine.validation_shared import _param_target_types

                    if pinfo.type_ is TypingAny:
                        pass  # untyped — accept anything
                    else:
                        target_types = _param_target_types(pinfo)
                        if isinstance(pval, target_types):
                            pass  # direct match against any non-None member
                        elif param_accepts_numeric(pinfo, pval):
                            pass  # int↔float interop, unwrap-Union aware
                        elif isinstance(pval, str) and pname.endswith("_slot"):
                            pass  # resolver converts str → Slot at runtime
                        else:
                            type_label = param_display_type(pinfo)
                            issues.append(
                                ValidationIssue(
                                    severity="error",
                                    code="PARAM_TYPE_MISMATCH",
                                    message=f"Parameter '{pname}' of '{ref.name}' expects "
                                    f"{type_label}, got {type(pval).__name__}.",
                                    location=_format_location(ref.location),
                                    suggestion=f"Change {pname} to a {type_label} value.",
                                )
                            )
                except TypeError:
                    # Generic aliases (e.g. list[int], dict[str, float]) aren't
                    # isinstance-checkable. Record as info-level issue.
                    issues.append(
                        ValidationIssue(
                            severity="info",
                            code="PARAM_TYPE_CHECK_SKIPPED",
                            message=f"Cannot validate type of parameter '{pname}' of "
                            f"'{ref.name}': complex type {pinfo.type_} is not "
                            f"isinstance-checkable.",
                            location=_format_location(ref.location),
                            suggestion=None,
                        )
                    )

            # Reject non-finite numbers (inf/nan) — these fail at compile
            if isinstance(pval, float) and not math.isfinite(pval):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="PARAM_INVALID_VALUE",
                        message=f"Parameter '{pname}' of '{ref.name}' has invalid value "
                        f"{pval!r}. Infinity and NaN are not allowed.",
                        location=_format_location(ref.location),
                        suggestion=f"Change {pname} to a finite number.",
                    )
                )

            # Constraint checking
            if pinfo.constraints and not isinstance(pval, VariableRef):
                c = pinfo.constraints
                if "min" in c and isinstance(pval, (int, float)) and pval < c["min"]:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="PARAM_OUT_OF_RANGE",
                            message=f"Parameter '{pname}' of '{ref.name}' value {pval} "
                            f"below minimum {c['min']}.",
                            location=_format_location(ref.location),
                            suggestion=f"Change {pname} to a value in range [{c.get('min', '...')}, {c.get('max', '...')}].",
                        )
                    )
                if "max" in c and isinstance(pval, (int, float)) and pval > c["max"]:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="PARAM_OUT_OF_RANGE",
                            message=f"Parameter '{pname}' of '{ref.name}' value {pval} "
                            f"above maximum {c['max']}.",
                            location=_format_location(ref.location),
                            suggestion=f"Change {pname} to a value in range [{c.get('min', '...')}, {c.get('max', '...')}].",
                        )
                    )
                if "options" in c and isinstance(pval, str) and pval not in c["options"]:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="PARAM_INVALID_OPTION",
                            message=f"Parameter '{pname}' of '{ref.name}' value '{pval}' "
                            f"is not a valid option. Valid: {c['options']}.",
                            location=_format_location(ref.location),
                            suggestion=f"Use one of: {c['options']}.",
                        )
                    )

        # Dict weight-sum validation: "weights" params with numeric values must sum to 1.0
        for pname, pval in ref.params.items():
            if (
                pname == "weights"
                and isinstance(pval, dict)
                and pval
                and all(isinstance(v, (int, float)) for v in pval.values())
            ):
                weight_sum = sum(pval.values())
                if abs(weight_sum - 1.0) > 1e-6:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="PARAM_INVALID_VALUE",
                            message=f"Parameter 'weights' of '{ref.name}' must sum to 1.0, "
                            f"got {weight_sum:.6f}.",
                            location=_format_location(ref.location),
                            suggestion="Adjust weight values so they sum to 1.0.",
                        )
                    )

        # Cross-parameter group constraints (param_constraints)
        if sig.param_constraints:
            for constraint in sig.param_constraints:
                group_params = constraint.get("params", [])
                rule = constraint.get("rule", "")
                provided = [p for p in group_params if ref.params.get(p) is not None]

                if rule == "exactly_one":
                    if len(provided) == 0:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="PARAM_GROUP_MISSING",
                                message=f"Component '{ref.name}' requires exactly one of "
                                f"[{', '.join(group_params)}], but none provided.",
                                location=_format_location(ref.location),
                                suggestion=f"Provide one of: {', '.join(group_params)}.",
                            )
                        )
                    elif len(provided) > 1:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="PARAM_GROUP_CONFLICT",
                                message=f"Component '{ref.name}' accepts only one of "
                                f"[{', '.join(group_params)}], but got: [{', '.join(provided)}].",
                                location=_format_location(ref.location),
                                suggestion=f"Remove one of: {', '.join(provided)}.",
                            )
                        )
                elif rule == "at_most_one":
                    if len(provided) > 1:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="PARAM_GROUP_CONFLICT",
                                message=f"Component '{ref.name}' accepts at most one of "
                                f"[{', '.join(group_params)}], but got: [{', '.join(provided)}].",
                                location=_format_location(ref.location),
                                suggestion=f"Remove one of: {', '.join(provided)}.",
                            )
                        )


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 6: Type flow validation
# ═══════════════════════════════════════════════════════════════════════════════


def _suggest_type_bridge(
    current_type: type,
    expected_type: type,
    registry: dict[str, Any],
) -> str:
    """Suggest a component that bridges current_type -> expected_type."""

    # Find components that accept current_type and output expected_type
    candidates = []
    for name, sig in registry.items():
        if is_compatible(current_type, sig.input_type) and is_compatible(
            sig.output_type, expected_type
        ):
            candidates.append(name)

    if candidates:
        examples = candidates[:3]
        return f"Insert a component that transforms {type_name(current_type)} to {type_name(expected_type)}. Options: {', '.join(examples)}"
    return (
        f"Expected input type {type_name(expected_type)}, "
        f"but previous step outputs {type_name(current_type)}."
    )


def _validate_type_flow(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    type_flow: list[TypeFlowEntry],
    slot_types: dict[str, type],
) -> None:
    """Pass 6: Walk expanded pipeline tree tracking output types."""

    # Resolve variable values for type checking
    variable_pipelines: dict[str, PipelineSpec] = {}
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            variable_pipelines[var.name] = var.value

    _validate_pipeline_type_flow(
        strategy.pipeline,
        registry,
        issues,
        type_flow,
        slot_types,
        prev_output_type=type(None),
        variable_pipelines=variable_pipelines,
        context="pipeline",
    )


def _validate_pipeline_type_flow(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    type_flow: list[TypeFlowEntry],
    slot_types: dict[str, type],
    prev_output_type: type,
    variable_pipelines: dict[str, PipelineSpec],
    context: str,
) -> type:
    """Validate type flow for a PipelineSpec, returning final output type."""
    from typing import Any as TypingAny

    from pipeline_engine.base.step import StepCategory

    current_type = prev_output_type
    # Track the most recent Parallel for EXTRACT_MISSING_KEY checking.
    prev_parallel: ParallelSpec | None = None
    # G1-followup-2: per-branch terminal output type for the most recent
    # Parallel. Read by ``_check_composer_input_types`` when the next step
    # is a Composer that declared a per-key contract.
    prev_parallel_branch_types: dict[str, type] = {}

    for i, step in enumerate(pipeline.steps):
        step_context = f"{context}.step[{i}]"

        if isinstance(step, ComponentRef):
            sig = registry.get(step.name)
            if not sig:
                type_flow.append(
                    TypeFlowEntry(
                        step=step.name,
                        input_type=type_name(current_type),
                        output_type="UNRESOLVED",
                        category="error",
                    )
                )
                current_type = TypingAny
                continue

            # DataLoader: generates new data regardless of position.
            # In the runtime PipelineValidator, nested Pipelines starting with
            # a DataSource skip type checks (pipeline_in is type(None)).
            # After factory expansion the DSL validator flattens those nested
            # Pipelines, so DataLoaders appear mid-branch with a non-None
            # current_type. Skip the check unconditionally to match runtime.
            is_data_loader = sig.category == StepCategory.DATA_LOADER

            # After Parallel the pipeline type is dict. TYPE_TRANSITIONS
            # defines which categories are valid after dict (composers,
            # position sizers, etc.). Use that as source of truth.
            is_dict_input = current_type is dict
            _dict_allowed = TYPE_TRANSITIONS.get("dict", {})

            # DICT_INPUT_EXPECTED — composer categories expect dict (the
            # output of a preceding Parallel). Empirically verified
            # (2026-06-11) that a composer with non-dict input crashes at
            # runtime (`ValueError: The truth value of a DataFrame is
            # ambiguous` or similar in step.run()). Severity = error so the
            # save-time validator blocks the strategy — runtime
            # PipelineValidator's lenient warning is the safety net, not
            # the gate.
            composer_categories = {
                StepCategory.SIGNAL_COMPOSER,
                StepCategory.FORECAST_COMPOSER,
            }
            if (
                sig.category in composer_categories
                and not is_dict_input
                and not is_data_loader
                and current_type is not TypingAny
            ):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="DICT_INPUT_EXPECTED",
                        message=f"Composer '{step.name}' expects dict input "
                        f"from Parallel, but got {type_name(current_type)}.",
                        location=_format_location(step.location),
                        suggestion=f"Place a Parallel block before '{step.name}'.",
                    )
                )
                # Skip downstream TYPE_MISMATCH — DICT_INPUT_EXPECTED already
                # describes the same authoring mistake; emitting both
                # would double-warn the user / agent. Mirrors TS pass6.
                type_flow.append(
                    TypeFlowEntry(
                        step=f"{step.name}({_format_params_brief(step.params)})",
                        input_type=type_name(current_type),
                        output_type=type_name(sig.output_type),
                        category=sig.category.value,
                    )
                )
                current_type = sig.output_type
                prev_parallel = None
                continue

            # DICT_NOT_CONSUMED — non-composer step after Parallel discards
            # the dict. Categories listed in TYPE_TRANSITIONS["dict"]
            # (composers + position_sizer + position_manager) and slot ops
            # are the legitimate consumers. Slot-readers
            # (e.g. MaxDrawdownStopLoss) are also exempt — their `run()`
            # treats ``current`` as a passthrough and pulls data from
            # declared slots instead, so they survive a dict input fine.
            # Without this exemption Python would false-positive on
            # strategies the TS canvas validator (which already exempts
            # slot-readers, pass6-types.ts:217) and the runtime both
            # accept.
            #
            # Empirically verified (2026-06-11) that any other category
            # crashes at runtime with `AttributeError: 'dict' object has
            # no attribute 'index'` or similar — the runtime executor
            # doesn't auto-unpack. Severity = error so the validator
            # blocks the strategy at save-time. Short-circuits the
            # downstream TYPE_MISMATCH check (would double-report the
            # same authoring mistake).
            is_slot_reader = bool(getattr(sig, "slot_reads", None))
            if (
                is_dict_input
                and sig.category not in _dict_allowed
                and sig.category != StepCategory.SLOT_OP
                and not is_data_loader
                and not is_slot_reader
            ):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="DICT_NOT_CONSUMED",
                        message=f"Step '{step.name}' follows Parallel but is "
                        f"not a Composer, Extract, or Load — dict input would "
                        f"crash this step at runtime.",
                        location=_format_location(step.location),
                        suggestion="Use a Composer to join branch results, or Extract to select one.",
                    )
                )
                type_flow.append(
                    TypeFlowEntry(
                        step=f"{step.name}({_format_params_brief(step.params)})",
                        input_type=type_name(current_type),
                        output_type=type_name(sig.output_type),
                        category=sig.category.value,
                    )
                )
                current_type = sig.output_type
                prev_parallel = None
                continue

            # Check compatibility
            if current_type is not TypingAny and sig.input_type is not TypingAny:
                skip_check = is_data_loader or (is_dict_input and sig.category in _dict_allowed)
                if not skip_check:
                    if not is_compatible(current_type, sig.input_type):
                        suggestion = _suggest_type_bridge(current_type, sig.input_type, registry)
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="TYPE_MISMATCH",
                                message=f"Type mismatch at {step_context}: "
                                f"'{step.name}' expects {type_name(sig.input_type)} "
                                f"but receives {type_name(current_type)}.",
                                location=_format_location(step.location),
                                suggestion=suggestion,
                            )
                        )

            # TRANSITION_OUTPUT_MISMATCH — author-facing warning when a
            # component's declared output_type disagrees with what the
            # category-level TYPE_TRANSITIONS table says the category
            # produces for this input. Mirrors runtime PipelineValidator
            # and TS pass6 (it's a hint about component metadata, not the
            # user's wiring — the strict per-component TYPE_MISMATCH above
            # is authoritative for user-facing correctness).
            prev_out_name = type_to_transition_key(current_type)
            if prev_out_name is not None and not is_data_loader:
                category_map = TYPE_TRANSITIONS.get(prev_out_name)
                if category_map is not None and sig.category in category_map:
                    expected_outputs = category_map[sig.category]
                    step_out_name = type_to_transition_key(sig.output_type)
                    if (
                        step_out_name is not None
                        and step_out_name not in expected_outputs
                    ):
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                code="TRANSITION_OUTPUT_MISMATCH",
                                message=f"Step '{step.name}' ({sig.category.value}) outputs "
                                f"'{step_out_name}' but transition from "
                                f"'{prev_out_name}' expects one of {expected_outputs}.",
                                location=_format_location(step.location),
                                suggestion=f"Check that '{step.name}' produces one of: {expected_outputs}.",
                            )
                        )

            # Record type flow
            type_flow.append(
                TypeFlowEntry(
                    step=f"{step.name}({_format_params_brief(step.params)})",
                    input_type=type_name(current_type),
                    output_type=type_name(sig.output_type),
                    category=sig.category.value,
                )
            )

            current_type = sig.output_type

        elif isinstance(step, SlotStoreSpec):
            # Pass-through; record slot type
            slot_types[step.slot_name] = current_type
            type_flow.append(
                TypeFlowEntry(
                    step=f'Store("{step.slot_name}")',
                    input_type=type_name(current_type),
                    output_type=type_name(current_type),
                    category="slot_op",
                )
            )

        elif isinstance(step, SlotStoreValueSpec):
            # Pass-through; record slot type based on value
            slot_types[step.slot_name] = type(step.value) if step.value is not None else str
            type_flow.append(
                TypeFlowEntry(
                    step=f'StoreValue("{step.slot_name}", {step.value!r})',
                    input_type=type_name(current_type),
                    output_type=type_name(current_type),
                    category="slot_op",
                )
            )

        elif isinstance(step, SlotLoadSpec):
            stored_type = slot_types.get(step.slot_name)
            if stored_type is not None:
                current_type = stored_type
            type_flow.append(
                TypeFlowEntry(
                    step=f'Load("{step.slot_name}")',
                    input_type=type_name(current_type),
                    output_type=type_name(current_type),
                    category="slot_op",
                )
            )

        elif isinstance(step, SlotExtractSpec):
            # EXTRACT_MISSING_KEY — Extract(key=…) following a Parallel must
            # reference one of the branches by name. Mirrors runtime
            # PipelineValidator so the AI sees this at validate-time, not
            # at backtest. Without a preceding Parallel, emit
            # DICT_INPUT_EXPECTED for the same reason composers do.
            if prev_parallel is None or current_type is not dict:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="DICT_INPUT_EXPECTED",
                        message=f"'Extract' expects dict input from Parallel, "
                        f"but got {type_name(current_type)}.",
                        location=_format_location(step.location),
                        suggestion="Place a Parallel block before 'Extract'.",
                    )
                )
                current_type = TypingAny
            elif step.key not in prev_parallel.branches:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="EXTRACT_MISSING_KEY",
                        message=f"Extract key '{step.key}' not found in Parallel "
                        f"branches: {sorted(prev_parallel.branches.keys())}.",
                        location=_format_location(step.location),
                        suggestion=f"Use one of: {sorted(prev_parallel.branches.keys())}.",
                    )
                )
                current_type = TypingAny
            else:
                # Resolve to the matching branch's final step output type.
                # The branch's type-flow was already recorded — we don't
                # re-walk; we just unblock downstream type checks.
                current_type = TypingAny

        elif isinstance(step, ParallelSpec):
            # Validate each branch independently with isolated slot_types snapshots
            branch_types: dict[str, type] = {}
            for branch_name, branch_steps in step.branches.items():
                branch_slot_types = dict(slot_types)
                branch_pipeline = PipelineSpec(
                    steps=branch_steps,
                    name=branch_name,
                    location=step.location,
                )
                branch_terminal_type = _validate_pipeline_type_flow(
                    branch_pipeline,
                    registry,
                    issues,
                    type_flow,
                    branch_slot_types,
                    prev_output_type=current_type,
                    variable_pipelines=variable_pipelines,
                    context=f"{context}.branch[{branch_name}]",
                )
                branch_types[branch_name] = branch_terminal_type
                # Merge branch stores into parent scope
                slot_types.update(branch_slot_types)

            # After parallel: output is dict
            current_type = dict
            prev_parallel = step
            prev_parallel_branch_types = branch_types

            # D23: Composer key validation on next step
            if i + 1 < len(pipeline.steps):
                next_step = pipeline.steps[i + 1]
                if isinstance(next_step, ComponentRef):
                    _check_composer_keys(step, next_step, registry, issues)
                    # G1-followup-2: also check the composer's per-key
                    # input types against the actual branch terminal types.
                    _check_composer_input_types(next_step, registry, branch_types, issues)
            # prev_parallel stays set; subsequent EXTRACT_MISSING_KEY /
            # DICT_INPUT_EXPECTED checks gate on current_type still being
            # dict, so once a Composer consumes the dict, the stale
            # prev_parallel becomes inert.

        elif isinstance(step, PipelineSpec):
            # Nested sub-pipeline: recurse
            current_type = _validate_pipeline_type_flow(
                step,
                registry,
                issues,
                type_flow,
                slot_types,
                prev_output_type=current_type,
                variable_pipelines=variable_pipelines,
                context=step_context,
            )

        elif isinstance(step, VariableRef):
            # Resolve to pipeline or literal
            if step.name in variable_pipelines:
                var_pipeline = variable_pipelines[step.name]
                current_type = _validate_pipeline_type_flow(
                    var_pipeline,
                    registry,
                    issues,
                    type_flow,
                    slot_types,
                    prev_output_type=current_type,
                    variable_pipelines=variable_pipelines,
                    context=f"var[{step.name}]",
                )
            # Literal variables don't change type flow

    return current_type


def _check_composer_keys(
    parallel: ParallelSpec,
    next_step: ComponentRef,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """D23: Check composer weight keys exist as parallel branch names.

    Missing branches are fine (excluded from combination).
    Extra keys that don't match any branch are errors (typo/stale).
    """
    from pipeline_engine.base.step import StepCategory

    sig = registry.get(next_step.name)
    if not sig:
        return

    if sig.category not in (StepCategory.SIGNAL_COMPOSER, StepCategory.FORECAST_COMPOSER):
        return

    branch_names = set(parallel.branches.keys())

    for param_name, param_value in next_step.params.items():
        if isinstance(param_value, dict) and all(isinstance(k, str) for k in param_value.keys()):
            extra = set(param_value.keys()) - branch_names
            if extra:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="COMPOSER_KEY_MISMATCH",
                        message=f"Composer '{next_step.name}' parameter '{param_name}' has keys "
                        f"not in parallel branches: {sorted(extra)}.",
                        location=_format_location(next_step.location),
                        suggestion=f"Valid branch names: {sorted(branch_names)}.",
                    )
                )


def _composer_accepts(actual: type, expected) -> bool:
    """Compatibility check used by ``_check_composer_input_types``.

    ``expected`` is either a single type or a tuple of accepted types.
    A tuple means "any of these" — we accept the first compat hit.
    Delegates to ``is_compatible`` which already understands NewType
    siblings (e.g. ForecastSeries → SignalSeries) and Annotated subtypes.
    """
    if isinstance(expected, tuple):
        return any(is_compatible(actual, t) for t in expected)
    return is_compatible(actual, expected)


def _format_expected(expected) -> str:
    """Pretty-print ``expected`` (single type or tuple) for error messages."""
    if isinstance(expected, tuple):
        return " or ".join(type_name(t) for t in expected)
    return type_name(expected)


def _check_composer_input_types(
    next_step: ComponentRef,
    registry: dict[str, Any],
    branch_types: dict[str, type],
    issues: list[ValidationIssue],
) -> None:
    """G1-followup-2: per-key dict-shape mismatch check.

    When the step after a Parallel is a Composer that declared a
    ``composer_inputs`` contract, each branch's terminal output type must
    satisfy the role's expected type. Two shapes:

    - ``dict[str, type | tuple[type, ...]]`` — heterogeneous. Each entry
      maps an init-param NAME (e.g. ``signal_key``) to the expected type
      at the branch that the user pointed that param at.
    - ``type | tuple[type, ...]`` — homogeneous. Every branch in the
      Parallel must satisfy this single type.

    Skipped cleanly when:
    - the composer didn't declare ``composer_inputs`` (still being audited)
    - the role param is ``None`` (auto-detect mode in e.g. ``ApplyMask``)
    - the role param is a ``VariableRef`` (resolved at runtime)
    - the role param's branch name isn't in ``branch_types``
      (``COMPOSER_KEY_MISMATCH`` already fires on that)
    """
    sig = registry.get(next_step.name)
    if sig is None:
        return
    composer_inputs = getattr(sig, "composer_inputs", None)
    if composer_inputs is None or not branch_types:
        return

    if isinstance(composer_inputs, dict):
        if not composer_inputs:
            return  # explicit opt-out (e.g. SelectiveCombinator passthrough)
        for role_param, expected in composer_inputs.items():
            branch_name = next_step.params.get(role_param)
            if branch_name is None:
                continue  # auto-detect / param omitted — skip
            if isinstance(branch_name, VariableRef):
                continue
            if not isinstance(branch_name, str):
                continue
            actual = branch_types.get(branch_name)
            if actual is None:
                continue  # COMPOSER_KEY_MISMATCH handles this
            if not _composer_accepts(actual, expected):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="COMPOSER_INPUT_TYPE_MISMATCH",
                        message=(
                            f"Composer '{next_step.name}' role '{role_param}' "
                            f"references branch '{branch_name}' which outputs "
                            f"'{type_name(actual)}', but expects "
                            f"{_format_expected(expected)}."
                        ),
                        location=_format_location(next_step.location),
                        suggestion=(
                            f"Change branch '{branch_name}' to end with a step "
                            f"that outputs {_format_expected(expected)}, or point "
                            f"'{role_param}' at a different branch."
                        ),
                    )
                )
    else:
        # Homogeneous: every branch must match
        for branch_name, actual in branch_types.items():
            if not _composer_accepts(actual, composer_inputs):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="COMPOSER_INPUT_TYPE_MISMATCH",
                        message=(
                            f"Composer '{next_step.name}' expects every Parallel "
                            f"branch to output {_format_expected(composer_inputs)}, "
                            f"but branch '{branch_name}' outputs "
                            f"'{type_name(actual)}'."
                        ),
                        location=_format_location(next_step.location),
                        suggestion=(
                            f"Change branch '{branch_name}' to end with a step "
                            f"that outputs {_format_expected(composer_inputs)}."
                        ),
                    )
                )


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 7: Phase ordering
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_phase_ordering(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Pass 7: Check step categories follow PHASE_ORDER."""

    _check_ordering(strategy.pipeline, registry, issues)


def _check_ordering(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    current_phase_idx: int = 0,
) -> int:
    """Check ordering, returning max phase index."""
    from pipeline_engine.base.step import StepCategory

    max_idx = current_phase_idx

    for step in pipeline.steps:
        if isinstance(step, ComponentRef):
            sig = registry.get(step.name)
            if sig and sig.category != StepCategory.SLOT_OP:
                step_idx = PHASE_INDEX.get(sig.category)
                if step_idx is not None and step_idx < max_idx:
                    expected_group = PHASE_GROUP_NAMES[max_idx]
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="PHASE_ORDER_VIOLATION",
                            message=f"Phase ordering: '{step.name}' ({sig.category.value}) "
                            f"appears after the {expected_group} phase.",
                            location=_format_location(step.location),
                            suggestion=f"Move '{step.name}' earlier in the pipeline, before the {expected_group} phase.",
                        )
                    )
                if step_idx is not None:
                    max_idx = max(max_idx, step_idx)

        elif isinstance(step, ParallelSpec):
            for branch_steps in step.branches.values():
                branch_pipeline = PipelineSpec(
                    steps=branch_steps, name=None, location=step.location
                )
                _check_ordering(branch_pipeline, registry, issues, current_phase_idx=max_idx)

        elif isinstance(step, PipelineSpec):
            _check_ordering(step, registry, issues, current_phase_idx=0)

    return max_idx


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 8: Slot validation
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_slots(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    slot_types: dict[str, type],
) -> None:
    """Pass 8: Validate Store/Load pairs, slot-reference params, slot types."""
    available_slots: dict[str, tuple] = {}
    used_slots: set[str] = set()

    _validate_slots_in_pipeline(
        strategy.pipeline, registry, issues, available_slots, used_slots, slot_types
    )

    # Check for unused stores
    for slot_name, (_, store_loc) in available_slots.items():
        if slot_name not in used_slots:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="SLOT_UNUSED",
                    message=f"Slot '{slot_name}' is stored but never loaded or referenced.",
                    location=_format_location(store_loc),
                )
            )


def _validate_slots_in_pipeline(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    available_slots: dict[str, tuple],
    used_slots: set[str],
    slot_types: dict[str, type],
) -> None:
    """Walk pipeline checking slot availability."""
    for step in pipeline.steps:
        if isinstance(step, SlotStoreSpec):
            stored_type = slot_types.get(step.slot_name)
            if stored_type is not None:
                available_slots[step.slot_name] = (stored_type, step.location)
            else:
                available_slots[step.slot_name] = (type(None), step.location)

        elif isinstance(step, SlotStoreValueSpec):
            value_type = type(step.value) if step.value is not None else str
            available_slots[step.slot_name] = (value_type, step.location)

        elif isinstance(step, SlotLoadSpec):
            used_slots.add(step.slot_name)
            if step.slot_name not in available_slots:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="SLOT_NOT_FOUND",
                        message=f"Load('{step.slot_name}'): no prior Store('{step.slot_name}') found.",
                        location=_format_location(step.location),
                        suggestion=f'Add Store("{step.slot_name}") before this Load.',
                    )
                )

        elif isinstance(step, ComponentRef):
            sig = registry.get(step.name)
            if sig and sig.slot_reads:
                for param_name, expected_type in sig.slot_reads.items():
                    # Implicit slot reads: slot name IS the key, no init parameter
                    if param_name not in sig.parameters:
                        used_slots.add(param_name)
                        continue

                    slot_name_val = step.params.get(param_name)
                    if slot_name_val is None:
                        # Param not provided by user — check default from registry
                        param_info = sig.parameters.get(param_name)
                        if param_info is not None and isinstance(param_info.default, str):
                            slot_name_val = param_info.default
                        # VariableRef defaults can't be validated statically; skip
                    if isinstance(slot_name_val, VariableRef):
                        # VariableRef slot names resolved at runtime; skip static check
                        continue
                    if isinstance(slot_name_val, str):
                        used_slots.add(slot_name_val)
                        if slot_name_val not in available_slots:
                            issues.append(
                                ValidationIssue(
                                    severity="error",
                                    code="SLOT_REF_NOT_FOUND",
                                    message=f"Component '{step.name}' parameter '{param_name}' "
                                    f"references slot '{slot_name_val}' which hasn't been stored.",
                                    location=_format_location(step.location),
                                    suggestion=f'Add Store("{slot_name_val}") before this component.',
                                )
                            )
                        else:
                            # Check type compatibility.
                            # Slot reads use lenient matching: NewTypes sharing
                            # the same base (e.g. WeightSeries and SignalSeries
                            # both wrap DataFrame) are compatible. This matches
                            # runtime behaviour where slots are untyped storage.
                            stored_type, _ = available_slots[slot_name_val]
                            if stored_type is not type(None) and not _is_slot_compatible(
                                stored_type, expected_type
                            ):
                                issues.append(
                                    ValidationIssue(
                                        severity="error",
                                        code="SLOT_TYPE_MISMATCH",
                                        message=f"Component '{step.name}' parameter '{param_name}' "
                                        f"expects slot type {type_name(expected_type)} "
                                        f"but slot '{slot_name_val}' stores {type_name(stored_type)}.",
                                        location=_format_location(step.location),
                                        suggestion=f"Slot '{slot_name_val}' stores {type_name(stored_type)} "
                                        f"but {type_name(expected_type)} is expected.",
                                    )
                                )

        elif isinstance(step, ParallelSpec):
            # Snapshot isolation: branches start with current slots
            branch_written: list[dict[str, tuple]] = []

            for branch_name, branch_steps in step.branches.items():
                branch_slots = dict(available_slots)  # Snapshot
                branch_used = set()
                branch_pipeline = PipelineSpec(
                    steps=branch_steps, name=branch_name, location=step.location
                )
                _validate_slots_in_pipeline(
                    branch_pipeline,
                    registry,
                    issues,
                    branch_slots,
                    branch_used,
                    slot_types,
                )
                used_slots.update(branch_used)
                # Collect new slots written in this branch
                new_slots = {k: v for k, v in branch_slots.items() if k not in available_slots}
                branch_written.append(new_slots)

            # Merge all branch-written slots into parent scope
            for new_slots in branch_written:
                available_slots.update(new_slots)

        elif isinstance(step, PipelineSpec):
            _validate_slots_in_pipeline(
                step, registry, issues, available_slots, used_slots, slot_types
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TREE WALKERS
# ═══════════════════════════════════════════════════════════════════════════════


def _walk_component_refs(strategy: StrategyFile) -> Iterator[ComponentRef]:
    """Walk all ComponentRef nodes in the strategy (expanded form)."""
    # Walk variables
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            yield from _walk_refs_in_pipeline(var.value)

    # Walk main pipeline
    yield from _walk_refs_in_pipeline(strategy.pipeline)


def _walk_refs_in_pipeline(pipeline: PipelineSpec) -> Iterator[ComponentRef]:
    """Walk ComponentRef nodes in a PipelineSpec."""
    for step in pipeline.steps:
        if isinstance(step, ComponentRef):
            yield step
        elif isinstance(step, ParallelSpec):
            for branch_steps in step.branches.values():
                for s in branch_steps:
                    yield from _walk_refs_in_step(s)
        elif isinstance(step, PipelineSpec):
            yield from _walk_refs_in_pipeline(step)


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
# PASS 9: Globals, Universe, and Declaration References
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_TIMEFRAMES = VALID_TIMEFRAMES


def _validate_declarations(
    strategy: StrategyFile,
    expanded: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    production_mode: bool = False,
) -> None:
    """Pass 9: Validate Globals, Universe, and declaration references."""
    # A) Globals validation
    if strategy.globals_ is not None:
        _validate_globals(strategy.globals_, issues)

    # B) Universe validation
    if strategy.universe is not None:
        _validate_universe(strategy.universe, issues, production_mode=production_mode)

    # C) Execution validation
    if strategy.execution is not None:
        _validate_execution(strategy.execution, issues)

    # D) Declaration reference validation
    _validate_declaration_refs(strategy, expanded, registry, issues)

    # E) Unused globals warning
    _warn_unused_globals(strategy, expanded, registry, issues)

    # F) Resampler config validation (source_tf, target_tf, bar_offset rule table)
    _validate_resampler_config(strategy, expanded, issues)


def _validate_globals(globals_: GlobalsSpec, issues: list[ValidationIssue]) -> None:
    """Validate Globals declaration values."""
    loc = "globals"
    if globals_.location:
        loc = _format_location(globals_.location)

    if globals_.target_timeframe is not None:
        if globals_.target_timeframe not in _VALID_TIMEFRAMES:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_GLOBAL",
                    message=f"Globals target_timeframe '{globals_.target_timeframe}' is not a valid "
                    f"timeframe. Valid: {sorted(_VALID_TIMEFRAMES)}",
                    location=loc,
                )
            )

    if globals_.bar_offset is not None:
        # Use the shared parser — permissive about format (any positive
        # whole-minute pandas duration), strict about value. Resampler-config
        # cross-checks (multiple-of-source, less-than-target) run in
        # _validate_resampler_config.
        try:
            parse_bar_offset_minutes(globals_.bar_offset)
        except ValueError as e:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_GLOBAL",
                    message=f"Globals bar_offset: {e}",
                    location=loc,
                )
            )


def _validate_universe(
    universe: UniverseSpec,
    issues: list[ValidationIssue],
    production_mode: bool = False,
) -> None:
    """Validate Universe declaration values.

    Args:
        universe: The parsed Universe spec.
        issues: Issue list to append to.
        production_mode: When True, UNRESOLVED_UNIVERSE and STALE_UNIVERSE are
            errors (block submit). When False (editor / WIP), they're warnings
            so users can save in-progress strategies.
    """
    loc = "universe"
    if universe.location:
        loc = _format_location(universe.location)

    # Mode-specific required fields
    if universe.mode == "manual":
        if not universe.symbols and not universe.resolved:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_UNIVERSE",
                    message="Universe mode='manual' requires 'symbols' or 'resolved' to be set.",
                    location=loc,
                )
            )
    elif universe.mode == "category":
        if not universe.categories:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_UNIVERSE",
                    message="Universe mode='category' requires 'categories' to be set.",
                    location=loc,
                )
            )
    elif universe.mode == "top_volume":
        if universe.top_n is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_UNIVERSE",
                    message="Universe mode='top_volume' requires 'top_n' to be set.",
                    location=loc,
                )
            )
    else:
        issues.append(
            ValidationIssue(
                severity="error",
                code="INVALID_UNIVERSE",
                message=f"Unknown Universe mode '{universe.mode}'. "
                f"Valid modes: manual, category, top_volume",
                location=loc,
            )
        )

    # ── Resolved-list checks ────────────────────────────────────────────────
    # The DSL invariant we want to enforce: every strategy promoted to a
    # production path (deploy / backtest submit) has a resolved asset list
    # baked into its source. The web editor maintains this automatically;
    # CLI / MCP paths must too. This block is the single gate.
    unresolved_severity: Literal["error", "warning"] = (
        "error" if production_mode else "warning"
    )

    has_resolved = bool(universe.resolved)  # non-None and non-empty
    resolved_is_explicit_empty = (
        universe.resolved is not None and len(universe.resolved) == 0
    )

    if not has_resolved:
        # Two sub-cases:
        #   1. resolved is None — never set (typical for new strategies that
        #      were pushed without resolving). For 'manual' mode this is OK
        #      if `symbols` is set, because the resolver derives resolved
        #      from symbols at eval time. For non-manual modes, the resolver
        #      needs the actual list baked in.
        #   2. resolved is [] — explicitly empty. If resolved_at is set, the
        #      resolve call returned zero assets (broken criteria → error).
        #      Otherwise it's a placeholder (treat same as case 1).
        if resolved_is_explicit_empty and universe.resolved_at:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="EMPTY_UNIVERSE",
                    message="Universe 'resolved' list is empty after resolution. "
                    "At least one asset is required — check your criteria "
                    "(mode / categories / exclusions).",
                    location=loc,
                )
            )
        else:
            # Manual mode with explicit `symbols` is self-sufficient; the
            # resolver derives `resolved` from `symbols`. Skip the warning.
            manual_self_sufficient = (
                universe.mode == "manual" and bool(universe.symbols)
            )
            if not manual_self_sufficient:
                issues.append(
                    ValidationIssue(
                        severity=unresolved_severity,
                        code="UNRESOLVED_UNIVERSE",
                        message="Universe has not been resolved. Call universe_resolve "
                        "(MCP tool or `keel universe resolve <file>`) or, in the web "
                        "editor, open the Universe block and change any field — the "
                        "editor auto-resolves and bakes the asset list into the source.",
                        location=loc,
                    )
                )

    # ── Stale-list structural check ────────────────────────────────────────
    # If resolved is populated, sanity-check that it lines up with the
    # criteria in the same DSL. This catches direct hand-edits to the source
    # that change criteria without re-resolving (e.g., bumping top_n from
    # 30 to 50 but leaving the 30-symbol resolved list in place). Without
    # this, the deploy guard accepts a non-empty `resolved` that no longer
    # matches what the strategy declares.
    if has_resolved:
        stale_severity: Literal["error", "warning"] = (
            "error" if production_mode else "warning"
        )
        assert universe.resolved is not None  # for type narrowing
        resolved_count = len(universe.resolved)

        # NOTE: structural checks here are approximate. They catch obvious
        # drift (top_n changed, manual symbols changed) but not every case.
        # Phase 3 (criteria_hash on UniverseSpec) is the rigorous version.
        if universe.mode == "manual" and universe.symbols:
            symbols_set = set(universe.symbols)
            resolved_set = set(universe.resolved)
            # For manual mode, resolved should equal symbols modulo
            # inclusions/exclusions. Compute the expected set.
            expected = symbols_set.copy()
            if universe.exclusions:
                expected -= set(universe.exclusions)
            if universe.inclusions:
                expected |= set(universe.inclusions)
            if expected != resolved_set:
                issues.append(
                    ValidationIssue(
                        severity=stale_severity,
                        code="STALE_UNIVERSE",
                        message="Universe 'resolved' list does not match declared 'symbols' "
                        f"(after exclusions/inclusions). Resolved has {resolved_count} "
                        f"items; criteria imply {len(expected)}. Re-resolve via "
                        "universe_resolve / `keel universe resolve` / web editor.",
                        location=loc,
                    )
                )
        elif universe.mode == "top_volume" and universe.top_n is not None:
            # Approximate expected count for top_volume:
            #   top_n - len(exclusions intersecting resolved) + len(inclusions)
            # We can't know which symbols the resolver pulled before applying
            # exclusions, so we use top_n as a coarse upper bound. Most drift
            # cases (top_n changed) show up as a flat count mismatch.
            inc_count = len(universe.inclusions or [])
            exc_count = len(universe.exclusions or [])
            expected_lower = max(0, universe.top_n - exc_count)
            expected_upper = universe.top_n + inc_count
            if not (expected_lower <= resolved_count <= expected_upper):
                issues.append(
                    ValidationIssue(
                        severity=stale_severity,
                        code="STALE_UNIVERSE",
                        message=f"Universe 'resolved' has {resolved_count} items but "
                        f"top_n={universe.top_n} implies "
                        f"{expected_lower}–{expected_upper} (after exclusions/inclusions). "
                        "Criteria likely changed since last resolve — re-resolve via "
                        "universe_resolve / `keel universe resolve` / web editor.",
                        location=loc,
                    )
                )
        # For mode='category' we can't structurally verify staleness without
        # querying the registry. Leave it to eval-worker / runtime checks.

    # exclusions and inclusions must not overlap
    if universe.exclusions and universe.inclusions:
        overlap = set(universe.exclusions) & set(universe.inclusions)
        if overlap:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INVALID_UNIVERSE",
                    message=f"Universe exclusions and inclusions overlap: {sorted(overlap)}",
                    location=loc,
                )
            )

    # Groups must be subsets of resolved
    if universe.groups and universe.resolved:
        resolved_set = set(universe.resolved)
        for group_name, group_symbols in universe.groups.items():
            not_in_resolved = set(group_symbols) - resolved_set
            if not_in_resolved:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="INVALID_UNIVERSE_GROUP",
                        message=f"Universe group '{group_name}' contains assets not in resolved: "
                        f"{sorted(not_in_resolved)}",
                        location=loc,
                    )
                )


_VALID_REBALANCE = {"every_bar", "on_change", "buffered"}
_VALID_BUFFER_MODE = {"relative", "absolute"}
_VALID_REBALANCE_METHOD = {"to_edge", "to_center"}


def _validate_execution(execution: ExecutionSpec, issues: list[ValidationIssue]) -> None:
    """Validate Execution declaration values."""
    loc = "execution"
    if execution.location:
        loc = _format_location(execution.location)

    # Mode validation
    if execution.rebalance not in _VALID_REBALANCE:
        issues.append(
            ValidationIssue(
                severity="error",
                code="INVALID_EXECUTION",
                message=f"Invalid rebalance mode '{execution.rebalance}'. "
                f"Must be one of: {sorted(_VALID_REBALANCE)}",
                location=loc,
            )
        )
        return  # short-circuit — other checks depend on valid mode

    # Conditional requirements
    if execution.rebalance == "buffered" and execution.buffer_threshold is None:
        issues.append(
            ValidationIssue(
                severity="error",
                code="MISSING_EXECUTION_PARAM",
                message="buffer_threshold is required when rebalance='buffered'",
                location=loc,
                suggestion="Add buffer_threshold=0.10 (10% relative buffer)",
            )
        )

    # Irrelevant param warnings
    if execution.rebalance != "buffered" and execution.buffer_threshold is not None:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="IRRELEVANT_EXECUTION_PARAM",
                message=f"buffer_threshold has no effect when rebalance='{execution.rebalance}'",
                location=loc,
                suggestion="Remove buffer_threshold or switch to rebalance='buffered'",
            )
        )
    if execution.rebalance != "on_change" and execution.on_change_tolerance != 1e-8:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="IRRELEVANT_EXECUTION_PARAM",
                message=f"on_change_tolerance has no effect when rebalance='{execution.rebalance}'",
                location=loc,
            )
        )
    if execution.rebalance != "buffered":
        if execution.buffer_mode != "relative":
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="IRRELEVANT_EXECUTION_PARAM",
                    message=f"buffer_mode has no effect when rebalance='{execution.rebalance}'",
                    location=loc,
                )
            )
        if execution.rebalance_method != "to_edge":
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="IRRELEVANT_EXECUTION_PARAM",
                    message=f"rebalance_method has no effect when rebalance='{execution.rebalance}'",
                    location=loc,
                )
            )

    # Range checks
    if execution.buffer_threshold is not None:
        if not (0.01 <= execution.buffer_threshold <= 0.5):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="PARAM_OUT_OF_RANGE",
                    message=f"buffer_threshold={execution.buffer_threshold} out of range [0.01, 0.5]",
                    location=loc,
                )
            )

    if execution.min_trade_size < 0.0 or execution.min_trade_size > 0.1:
        issues.append(
            ValidationIssue(
                severity="error",
                code="PARAM_OUT_OF_RANGE",
                message=f"min_trade_size={execution.min_trade_size} out of range [0.0, 0.1]",
                location=loc,
            )
        )

    if execution.on_change_tolerance is not None:
        if not (1e-12 <= execution.on_change_tolerance <= 1e-4):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="PARAM_OUT_OF_RANGE",
                    message=f"on_change_tolerance={execution.on_change_tolerance} out of range [1e-12, 1e-4]",
                    location=loc,
                )
            )

    # Value checks
    if execution.buffer_mode not in _VALID_BUFFER_MODE:
        issues.append(
            ValidationIssue(
                severity="error",
                code="INVALID_EXECUTION",
                message=f"Invalid buffer_mode '{execution.buffer_mode}'. "
                f"Must be one of: {sorted(_VALID_BUFFER_MODE)}",
                location=loc,
            )
        )
    if execution.rebalance_method not in _VALID_REBALANCE_METHOD:
        issues.append(
            ValidationIssue(
                severity="error",
                code="INVALID_EXECUTION",
                message=f"Invalid rebalance_method '{execution.rebalance_method}'. "
                f"Must be one of: {sorted(_VALID_REBALANCE_METHOD)}",
                location=loc,
            )
        )


def _validate_declaration_refs(
    strategy: StrategyFile,
    expanded: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Validate declaration references: check all refs resolve against scope."""
    # Build declaration scope
    scope: dict[str, Any] = {}
    if strategy.globals_ is not None:
        if strategy.globals_.target_timeframe is not None:
            scope["globals.target_timeframe"] = strategy.globals_.target_timeframe
        if strategy.globals_.bar_offset is not None:
            scope["globals.bar_offset"] = strategy.globals_.bar_offset
    if strategy.universe is not None:
        if strategy.universe.groups:
            scope["universe.groups"] = strategy.universe.groups

    # Walk all components in expanded pipeline, check declaration refs
    for comp_ref in _walk_component_refs(expanded):
        sig = registry.get(comp_ref.name)
        if sig is None:
            continue

        loc = _format_location(comp_ref.location)

        # Required declaration refs
        for param_name, namespace in sig.declaration_refs.items():
            if namespace == "universe.groups":
                # GroupAssetFilter: param value is the group name, namespace is the groups dict
                group_name = comp_ref.params.get(param_name)
                if isinstance(group_name, str):
                    groups = scope.get("universe.groups", {})
                    if not groups:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="MISSING_DECLARATION_REF",
                                message=f"Component '{comp_ref.name}' parameter '{param_name}' "
                                f"references group '{group_name}' but no Universe groups are defined.",
                                location=loc,
                                suggestion="Add groups to your Universe declaration.",
                            )
                        )
                    elif group_name not in groups:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="MISSING_DECLARATION_REF",
                                message=f"Component '{comp_ref.name}' parameter '{param_name}' "
                                f"references group '{group_name}' which does not exist in Universe. "
                                f"Available groups: {sorted(groups.keys())}",
                                location=loc,
                            )
                        )
            else:
                # Simple scalar ref (e.g., "globals.target_timeframe")
                if namespace not in scope:
                    # Determine available values in the same top-level namespace
                    ns_prefix = namespace.split(".")[0] + "."
                    available = sorted(k for k in scope if k.startswith(ns_prefix))
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="MISSING_DECLARATION_REF",
                            message=f"Component '{comp_ref.name}' requires '{namespace}' "
                            f"but it is not declared in Globals/Universe. "
                            + (f"Available: {available}" if available else "No globals declared."),
                            location=loc,
                            suggestion=f"Add {namespace.split('.')[-1]} to your "
                            f"{namespace.split('.')[0].title()} declaration.",
                        )
                    )

        # Optional declaration refs — no error if missing, but mark as info
        # (no action needed — they'll resolve to None at runtime)


def _warn_unused_globals(
    strategy: StrategyFile,
    expanded: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Warn about globals that are declared but never referenced by any component."""
    if strategy.globals_ is None:
        return

    # Collect declared globals namespaces
    declared: set[str] = set()
    if strategy.globals_.target_timeframe is not None:
        declared.add("globals.target_timeframe")
    if strategy.globals_.bar_offset is not None:
        declared.add("globals.bar_offset")
    if not declared:
        return

    # Collect all referenced globals namespaces from components
    referenced: set[str] = set()
    for comp_ref in _walk_component_refs(expanded):
        sig = registry.get(comp_ref.name)
        if sig is None:
            continue
        for namespace in sig.declaration_refs.values():
            if namespace.startswith("globals."):
                referenced.add(namespace)
        for namespace in sig.optional_declaration_refs.values():
            if namespace.startswith("globals."):
                referenced.add(namespace)

    # Warn about unreferenced globals
    unused = declared - referenced
    for ns in sorted(unused):
        field_name = ns.split(".")[-1]
        issues.append(
            ValidationIssue(
                severity="warning",
                code="UNUSED_GLOBAL",
                message=(
                    f"Globals '{field_name}' is declared but not referenced by any component. "
                    f"Either remove the Globals declaration, or add a component that consumes "
                    f"it (e.g. TargetTimeframeResampler reads target_timeframe)."
                ),
                location="globals",
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# RESAMPLER CONFIG (source_tf, target_tf, bar_offset) — mirrors
# pipeline_engine.validation_shared.validate_resample_config rules across the
# whole strategy.  Catches:
#   - upsampling (source > target)
#   - bar_offset that wouldn't survive the runtime check
#   - the no-op TargetTimeframeResampler that bit jeff5908 on 2026-06-06
# ─────────────────────────────────────────────────────────────────────────────


def _extract_price_loader_timeframe(expanded: StrategyFile) -> str | None:
    """Find the first PriceDataLoader in the pipeline and return its `timeframe` param.

    Returns None if no PriceDataLoader present, the param isn't a literal string,
    or the value isn't a known timeframe key. None means "skip resampler config
    validation" (callers should treat as 'can't enforce'), never "use a default."
    """
    for comp_ref in _walk_component_refs(expanded):
        if comp_ref.name != "PriceDataLoader":
            continue
        tf = comp_ref.params.get("timeframe") if comp_ref.params else None
        if isinstance(tf, str) and tf in TIMEFRAME_MINUTES:
            return tf
        return None
    return None


def _has_target_timeframe_resampler(expanded: StrategyFile) -> bool:
    """True if any TargetTimeframeResampler appears in the pipeline."""
    for comp_ref in _walk_component_refs(expanded):
        if comp_ref.name == "TargetTimeframeResampler":
            return True
    return False


def _validate_resampler_config(
    strategy: StrategyFile,
    expanded: StrategyFile,
    issues: list[ValidationIssue],
) -> None:
    """Cross-component validation of (source_tf, target_tf, bar_offset).

    Pulls `source_tf` from the first PriceDataLoader's `timeframe` param and
    `target_tf` / `bar_offset` from Globals. If any piece is missing or
    can't be parsed, the corresponding rule check is skipped (other passes
    already report missing/invalid Globals + invalid component params).
    """
    if strategy.globals_ is None:
        return
    target_tf = strategy.globals_.target_timeframe
    bar_offset = strategy.globals_.bar_offset
    if target_tf is None:
        # No target → resampler can't even run; downstream component validation
        # already raises if TargetTimeframeResampler is present without it.
        return
    if target_tf not in TIMEFRAME_MINUTES:
        # Already reported as INVALID_GLOBAL by _validate_globals.
        return

    source_tf = _extract_price_loader_timeframe(expanded)
    if source_tf is None:
        # No (parseable) PriceDataLoader — nothing to validate against.
        return

    # Apply the canonical rule table. Each ValueError maps to a specific issue
    # code so the editor + agent can disambiguate.
    try:
        validate_resample_config(source_tf, target_tf, bar_offset)
    except ValueError as e:
        msg = str(e)
        if "upsampling not supported" in msg:
            code = "UPSAMPLE_NOT_SUPPORTED"
        elif "no valid value when target_timeframe equals" in msg:
            code = "BAR_OFFSET_AT_SAME_TF"
        elif "must be a multiple" in msg:
            code = "BAR_OFFSET_NOT_MULTIPLE"
        elif "strictly less than" in msg:
            code = "BAR_OFFSET_TOO_LARGE"
        elif "must be positive" in msg or "whole number of minutes" in msg:
            code = "INVALID_BAR_OFFSET"
        elif "not a valid duration" in msg:
            code = "INVALID_BAR_OFFSET"
        else:
            code = "INVALID_RESAMPLER_CONFIG"
        issues.append(
            ValidationIssue(
                severity="error",
                code=code,
                message=msg,
                location="globals",
            )
        )
        return  # Don't emit the no-op warning when the config is already errored

    # No-op resampler warning: same TF + a TargetTimeframeResampler in the
    # pipeline. The runtime now short-circuits this case cleanly, but the
    # component step itself is wasted and the Globals declaration is redundant.
    if source_tf == target_tf and _has_target_timeframe_resampler(expanded):
        issues.append(
            ValidationIssue(
                severity="warning",
                code="RESAMPLER_NOOP",
                message=(
                    f"TargetTimeframeResampler is a no-op when target_timeframe "
                    f"({target_tf}) equals the data loader's timeframe ({source_tf}). "
                    f"Remove the TargetTimeframeResampler() step (and the redundant "
                    f"Globals(target_timeframe=...) line if nothing else uses it)."
                ),
                location="globals",
            )
        )


def _format_params_brief(params: dict[str, Any]) -> str:
    """Format params dict for brief display."""
    parts = []
    for k, v in params.items():
        if isinstance(v, VariableRef):
            parts.append(f"{k}=${v.name}")
        elif isinstance(v, str):
            parts.append(f'{k}="{v}"')
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


__all__ = [
    "validate_strategy",
]
