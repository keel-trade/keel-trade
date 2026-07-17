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
from typing import Any, Callable, Iterator

from pipeline_engine.base.registry import ParamTier
from pipeline_engine.base.step import PHASE_GROUP_NAMES
from pipeline_engine.constants import VALID_TIMEFRAMES
from pipeline_engine.dsl.catalog import (
    RULES,
    CatalogError,
    _template_placeholders,
    severity_for,
)
from pipeline_engine.dsl.spec import (
    EXECUTION_PARAM_META,
    EXECUTION_VALID_BUFFER_MODE,
    EXECUTION_VALID_REBALANCE,
    EXECUTION_VALID_REBALANCE_METHOD,
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
    user_meaningful = {t for t in _camel_tokens(name) if t not in _GENERIC_TOKEN_NAMES}
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
            if shorter_len >= 5 and (name_lower in reg_lower or reg_lower in name_lower)
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


def _store_value_slot_type(value: Any) -> type:
    """Slot type recorded for a ``StoreValue`` literal — shared by passes 6 + 8.

    Honest typing: ``type(value)`` — including ``type(None)`` for a literal
    ``None``. ``type(None)`` is already the validator's "unknown stored
    type" sentinel (see the SlotStoreSpec branch of
    ``_validate_slots_in_pipeline``): pass 8 skips SLOT_TYPE_MISMATCH for
    it, and the resolver builds a NoneType-typed slot — which is exactly
    what the slot holds at runtime. The previous behavior fabricated ``str``
    for None (in two drifted copies), so downstream slot-type decisions were
    made against a type the slot never stores.
    """
    return type(value)


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


#: Sentinel distinguishing "suggestion not passed" (render the rule's
#: suggestion_template, if any) from an explicit ``suggestion=None``.
_UNSET: Any = object()


def emit(
    issues: list[ValidationIssue],
    code: str,
    *,
    location: str | None,
    severity_context: str | None = None,
    production_mode: bool = False,
    suggestion: Any = _UNSET,
    message_override: str | None = None,
    **params: Any,
) -> None:
    """Append a catalog-rendered :class:`ValidationIssue` (spec 02 §2.1).

    Code, severity, and message/suggestion text render from the rule catalog
    (``pipeline_engine.dsl.catalog``) — severity is POLICY, never a per-site
    literal:

    - ``severity`` derives from the rule's category with declared overrides
      only. ``severity_context`` selects a ``severity_context_overrides``
      entry (an undeclared context raises); ``production_mode=True`` promotes
      ``promote_in_production`` rules (UNRESOLVED_UNIVERSE / STALE_UNIVERSE)
      to error — the catalog encoding of validator production semantics.
    - ``message`` renders ``message_template`` from ``params``. Strict: the
      passed params must exactly cover the placeholders of every template
      being rendered — a missing or extra param raises :class:`CatalogError`
      (no silent fallbacks).
    - ``message_override`` is the escape hatch for the documented multi-shape
      sites (the raw LockError text under UNKNOWN_COMPONENT, LOCK_DRIFT's
      missing/unknown shape, the resampler ValueError→code dispatch whose
      text IS the shared rule table's). Every override shape is documented in
      the rule's ``explain``.
    - ``suggestion``: omitted → render the rule's ``suggestion_template`` (if
      any) from ``params``; pass an explicit string/None for dynamically
      computed or site-specific variants.
    """
    rule = RULES.get(code)
    if rule is None:
        raise CatalogError(
            f"emit(): unknown issue code {code!r} — add the catalog entry "
            f"in dsl/catalog.py first (spec 02 §1.4 standing intake rule)."
        )

    severity = severity_for(rule, context=severity_context)
    if production_mode and rule.promote_in_production:
        severity = "error"

    required: set[str] = set()
    if message_override is None:
        required |= _template_placeholders(rule.message_template, code, "message_template")
    render_suggestion = suggestion is _UNSET and bool(rule.suggestion_template)
    if render_suggestion:
        required |= _template_placeholders(rule.suggestion_template, code, "suggestion_template")
    if set(params) != required:
        raise CatalogError(
            f"emit({code}): template params mismatch — required "
            f"{sorted(required)}, got {sorted(params)}."
        )

    if message_override is not None:
        message = message_override
    else:
        message = rule.message_template.format(**params)
    if render_suggestion:
        rendered_suggestion: str | None = rule.suggestion_template.format(**params)
    elif suggestion is _UNSET:
        rendered_suggestion = None
    else:
        rendered_suggestion = suggestion

    issues.append(
        ValidationIssue(
            severity=severity,  # type: ignore[arg-type]
            code=code,
            message=message,
            location=location,  # type: ignore[arg-type]
            suggestion=rendered_suggestion,
        )
    )


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
        lock: Component version lock. Two modes:
            - non-empty dict: Use the provided lock as-is (production path;
              chat-api and keel-api always pass an explicit lock).
            - None or {} (empty dict): Auto-generate a lock from the
              strategy using latest versions (convenience path for
              `/v1/strategies/validate`, tests, and ad-hoc validation).
              An empty dict is normalized to None at this boundary —
              never-pinned means "validate at latest", matching the
              loaders' `{} → None` collapse (core-engine-audit A13).
              Pre-2026-07 behavior built an EMPTY effective registry from
              `{}`, silently skipping semantic passes 5-9 — the banned
              silent-fallback genre; there is no such mode anymore.
        production_mode: When True, promotes `UNRESOLVED_UNIVERSE` and
            `STALE_UNIVERSE` from warnings to errors. Used by deploy and
            backtest submit endpoints to refuse strategies that can't run.
            Editor / WIP paths leave this False so users can save unfinished
            strategies. Default False keeps existing callers' behavior intact.
    """
    from pipeline_engine.base.lock import evolve_lock
    from pipeline_engine.base.registry import (
        COMPONENT_REGISTRY,
        _build_effective_registry,
        get_latest,
    )
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()

    # Full registry view (all latest) — needed for passes 2 & 4 which must
    # check against ALL known component names, not just locked ones.
    full_registry = {
        name: sig for name in COMPONENT_REGISTRY if (sig := get_latest(name)) is not None
    }

    # Never-pinned means "validate at latest": collapse `{}` → None here,
    # exactly like the strategy loaders do (A13). An empty dict must NOT
    # reach `_build_effective_registry` below — it would build an EMPTY
    # registry and semantic passes 5-9 would silently skip every component.
    if lock is not None and len(lock) == 0:
        lock = None

    # Auto-generate lock if not provided. `evolve_lock` raises
    # `LockError` when the strategy references unknown components — that's
    # not an internal bug, it's a legitimate validation failure we want to
    # surface to the caller as a structured `ValidationIssue` rather than
    # bubble up as an exception. Catch ONLY LockError (the known failure
    # shape); any other exception (real bug) propagates.
    from pipeline_engine.base.lock import LockError

    lock_gen_issues: list[ValidationIssue] = []
    if lock is None:
        try:
            lock = evolve_lock({}, strategy)
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
            # message_override: the raw LockError text (documented shape in
            # the catalog entry's explain).
            emit(
                lock_gen_issues,
                "UNKNOWN_COMPONENT",
                location=None,
                message_override=err_text,
                suggestion=suggestion,
            )
            lock = None

    # Build effective registry from lock for passes 5-9. A lock pointing
    # at a non-existent version surfaces as INVALID_VERSION_LOCK (emitted
    # by _validate_names below) rather than an uncaught LockError —
    # parity with TS pass4-names so the AI sees a structured issue, not
    # a crash.
    if lock is not None:
        # Lazy import to break the dsl ↔ base.lock circular import.
        from pipeline_engine.base.lock import LockError

        try:
            registry = _build_effective_registry(lock)
        except LockError:
            from pipeline_engine.base.registry import get_all_versions

            invalid_lock_keys = {
                name for name, ver in lock.items() if ver not in (get_all_versions(name) or {})
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

    # Drift check: when the caller passed a lock (or we successfully
    # auto-generated one), surface any drift from the current registry as
    # informational/warning issues. Operators get visible signal that a
    # locked version is behind latest or no longer in the registry —
    # without breaking validation. Auto-generated locks are fresh by
    # construction so this is a no-op in that case.
    if lock is not None:
        from pipeline_engine.base.lock import check_lock_drift

        # `check_lock_drift` already handles every expected input shape
        # gracefully (unknown components and missing versions come back as
        # LockDrift entries, not exceptions). If it raises, that's a real
        # engine bug — let it propagate. The old `except Exception: pass`
        # here silently discarded the entire LOCK_DRIFT channel on any
        # internal failure (banned silent-fallback pattern).
        for d in check_lock_drift(lock):
            # All drift severities are at `warning` — `info` would be
            # silently dropped by several downstream callers that only
            # serialize errors + warnings (e.g. tools.py:strategy_validate
            # response, keel-api /parse + /lock/validate endpoints,
            # keel-app frontend renderer). Drift is meant to be visible
            # signal, not silent metadata.
            if d.drift_type == "outdated":
                emit(
                    issues,
                    "LOCK_DRIFT",
                    location=None,
                    component=d.component,
                    locked_version=d.locked_version,
                    latest_version=d.latest_version,
                )
            else:  # "missing" or "unknown" — documented override shape
                detail = "; ".join(c for c in d.changes if c) if d.changes else ""
                msg = (
                    f"Component '{d.component}' is locked at v{d.locked_version} "
                    f"but {d.drift_type} from the registry." + (f" {detail}" if detail else "")
                )
                emit(issues, "LOCK_DRIFT", location=None, message_override=msg)

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
    _validate_declarations(
        strategy, expanded, registry, full_registry, issues, production_mode=production_mode
    )

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
# PARAM-VALUE REF WALKERS (shared with the resolver)
# ═══════════════════════════════════════════════════════════════════════════════


def iter_variable_refs(value: Any) -> Iterator[VariableRef]:
    """Yield every ``VariableRef`` in a parsed param value, at any depth.

    Walks lists, tuples, and dict *values*. Dict keys and set elements cannot
    contain refs — ``VariableRef`` is unhashable, so the parser rejects those
    shapes before a spec exists. A bare top-level ref is yielded too.

    This is the shared oracle for "which variables does this value
    reference?" — used by validation pass 1 (unknown/forward-reference
    detection inside containers), factory substitution (pass 3 and the
    resolver), and the resolver's dependency sort. Before B4, only top-level
    refs were seen: a ref nested in a list/dict param passed every validator
    and reached the component constructor as a raw ``VariableRef`` object.
    """
    if isinstance(value, VariableRef):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_variable_refs(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_variable_refs(item)


def substitute_variable_refs(value: Any, resolve: Callable[[VariableRef], Any]) -> Any:
    """Return ``value`` with every nested ``VariableRef`` replaced.

    ``resolve(ref)`` returns the replacement value — or the ref itself to
    leave it in place (factory substitution replaces only factory params) —
    or raises (the resolver's scope lookup raises ``ResolveError`` for
    unknown names, so no raw ref can survive param resolution). Containers
    are rebuilt, never mutated; non-container leaves pass through unchanged.
    Walks the same shapes as ``iter_variable_refs`` (one walker, one
    behavior).
    """
    if isinstance(value, VariableRef):
        return resolve(value)
    if isinstance(value, list):
        return [substitute_variable_refs(v, resolve) for v in value]
    if isinstance(value, tuple):
        return tuple(substitute_variable_refs(v, resolve) for v in value)
    if isinstance(value, dict):
        return {k: substitute_variable_refs(v, resolve) for k, v in value.items()}
    return value


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
            emit(issues, "UNDEFINED_VARIABLE", location=loc_str, name=name)
            return

        def_line = definitions[name]
        if def_line > use_line or (def_line == use_line and use_line > 0):
            # Forward references are non-blocking — factories/variables are
            # resolved by name, not definition order.  Graph-converted specs
            # have all locations at line 0, so we also skip the degenerate
            # 0 >= 0 case (both defined and used at synthetic line 0).
            emit(
                issues,
                "FORWARD_REFERENCE",
                location=loc_str,
                name=name,
                def_line=def_line,
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
            # Check VariableRef in params — at any depth. Refs nested inside
            # list/dict/tuple param values are real references (the parser
            # accepts them; the resolver substitutes them), so unknown names
            # must surface here with the same codes as top-level refs (B4).
            for pname, pval in step.params.items():
                for ref in iter_variable_refs(pval):
                    if factory_params and ref.name in factory_params:
                        continue
                    _check_ref(ref.name, ref_line, ref.location)

        elif isinstance(step, FactoryCallSpec):
            _check_ref(step.name, ref_line, step.location)
            # Check VariableRef in factory args — at any depth (see above)
            for aname, aval in step.args.items():
                for ref in iter_variable_refs(aval):
                    if factory_params and ref.name in factory_params:
                        continue
                    _check_ref(ref.name, ref_line, ref.location)

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

    # Check variable values (Pipeline VariableRef in steps; refs at any
    # depth in literal container values — the parser allows e.g. x = [a, b])
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            _walk_refs_in_steps(var.value.steps, f"var[{var.name}]", var.location.line)
        else:
            for ref in iter_variable_refs(var.value):
                _check_ref(ref.name, var.location.line, ref.location)

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
            emit(
                issues,
                "NAME_COLLISION",
                location=_format_location(var.location),
                suggestion="Rename the variable to avoid collision.",
                kind="Variable",
                name=var.name,
                conflict=f"registered component '{var.name}'",
            )
        if var.name in factory_names:
            emit(
                issues,
                "NAME_COLLISION",
                location=_format_location(var.location),
                suggestion="Use distinct names for variables and factories.",
                kind="Variable",
                name=var.name,
                conflict=f"factory '{var.name}' (ambiguous)",
            )

    for factory in strategy.factories:
        if factory.name in registry:
            emit(
                issues,
                "NAME_COLLISION",
                location=_format_location(factory.location),
                suggestion="Rename the factory to avoid collision.",
                kind="Factory",
                name=factory.name,
                conflict=f"registered component '{factory.name}'",
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
                    emit(
                        issues,
                        "FACTORY_MISSING_PARAM",
                        location=_format_location(step.location),
                        factory=step.name,
                        param=req,
                    )
                    return step

            # Check for unknown params
            for arg_name in step.args:
                if arg_name not in available:
                    emit(
                        issues,
                        "FACTORY_UNKNOWN_PARAM",
                        location=_format_location(step.location),
                        factory=step.name,
                        param=arg_name,
                        available=sorted(available),
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
            # Substitute at any depth — factory params referenced inside
            # list/dict/tuple param values must expand too (B4). Refs not
            # matching a factory param are left in place for later passes.
            for pname, pval in step.params.items():
                step.params[pname] = substitute_variable_refs(
                    pval, lambda r: substitutions.get(r.name, r)
                )

        elif isinstance(step, ParallelSpec):
            for branch_steps in step.branches.values():
                temp = PipelineSpec(steps=branch_steps, name=None, location=step.location)
                _substitute_params(temp, substitutions)

        elif isinstance(step, PipelineSpec):
            _substitute_params(step, substitutions)

        elif isinstance(step, FactoryCallSpec):
            for aname, aval in step.args.items():
                step.args[aname] = substitute_variable_refs(
                    aval, lambda r: substitutions.get(r.name, r)
                )


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
            emit(
                issues,
                "UNKNOWN_COMPONENT",
                location=_format_location(ref.location),
                suggestion=suggestion_text,
                name=ref.name,
            )
        else:
            # Warn on deprecated components
            sig = registry[ref.name]
            if getattr(sig, "status", None) == "deprecated":
                emit(
                    issues,
                    "DEPRECATED_COMPONENT",
                    location=_format_location(ref.location),
                    name=ref.name,
                )

            # INVALID_VERSION_LOCK — verify the locked version exists.
            # `get_all_versions` is a registry dict copy and cannot
            # legitimately fail; if it ever raises, that's an engine bug
            # that must propagate. The old `except Exception: versions = {}`
            # here converted such a bug into a fabricated
            # INVALID_VERSION_LOCK for every locked component — the exact
            # silent-fallback pattern the house rules forbid.
            if component_lock and ref.name in component_lock:
                locked_version = component_lock[ref.name]
                versions = get_all_versions(ref.name) or {}
                if locked_version not in versions:
                    latest = getattr(sig, "version", None)
                    if latest is not None:
                        hint = (
                            f"Available versions: latest is {latest}. "
                            f"Update the lock or remove version pin."
                        )
                    else:
                        hint = f"Remove the version lock for '{ref.name}'."
                    emit(
                        issues,
                        "INVALID_VERSION_LOCK",
                        location=_format_location(ref.location),
                        suggestion=hint,
                        component=ref.name,
                        locked_version=locked_version,
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 5: Parameter validation
# ═══════════════════════════════════════════════════════════════════════════════


def _effective_param_value(
    ref_params: dict[str, Any],
    reg_params: dict[str, Any],
    pname: str,
) -> Any:
    """The value ``__init__`` would see for ``pname``.

    The explicitly written param when present (including an explicit None or
    a VariableRef), else the registry default. Required-without-default params
    that aren't written resolve to None — pass 5's MISSING_PARAM covers that
    case separately.
    """
    if pname in ref_params:
        return ref_params[pname]
    pinfo = reg_params.get(pname)
    if pinfo is None or pinfo.default is MISSING:
        return None
    return pinfo.default


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
                emit(
                    issues,
                    "MISSING_PARAM",
                    location=_format_location(ref.location),
                    component=ref.name,
                    param=pname,
                    default_hint=default_hint,
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
                infra_note = f" Infra params: {available_infra}." if available_infra else ""
                emit(
                    issues,
                    "UNKNOWN_PARAM",
                    location=_format_location(ref.location),
                    component=ref.name,
                    param=pname,
                    strategy_params=available_strategy,
                    infra_note=infra_note,
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
                            emit(
                                issues,
                                "PARAM_TYPE_MISMATCH",
                                location=_format_location(ref.location),
                                param=pname,
                                component=ref.name,
                                expected=type_label,
                                actual=type(pval).__name__,
                            )
                except TypeError:
                    # Generic aliases (e.g. list[int], dict[str, float]) aren't
                    # isinstance-checkable. Record as info-level issue
                    # (severity_override="info" in the catalog).
                    emit(
                        issues,
                        "PARAM_TYPE_CHECK_SKIPPED",
                        location=_format_location(ref.location),
                        param=pname,
                        component=ref.name,
                        type=pinfo.type_,
                    )

            # Reject non-finite numbers (inf/nan) — these fail at compile
            if isinstance(pval, float) and not math.isfinite(pval):
                emit(
                    issues,
                    "PARAM_INVALID_VALUE",
                    location=_format_location(ref.location),
                    suggestion=f"Change {pname} to a finite number.",
                    detail=f"Parameter '{pname}' of '{ref.name}' has invalid value "
                    f"{pval!r}. Infinity and NaN are not allowed.",
                )

            # Constraint checking
            if pinfo.constraints and not isinstance(pval, VariableRef):
                c = pinfo.constraints
                if "min" in c and isinstance(pval, (int, float)) and pval < c["min"]:
                    emit(
                        issues,
                        "PARAM_OUT_OF_RANGE",
                        location=_format_location(ref.location),
                        suggestion=f"Change {pname} to a value in range [{c.get('min', '...')}, {c.get('max', '...')}].",
                        detail=f"Parameter '{pname}' of '{ref.name}' value {pval} "
                        f"below minimum {c['min']}.",
                    )
                if "max" in c and isinstance(pval, (int, float)) and pval > c["max"]:
                    emit(
                        issues,
                        "PARAM_OUT_OF_RANGE",
                        location=_format_location(ref.location),
                        suggestion=f"Change {pname} to a value in range [{c.get('min', '...')}, {c.get('max', '...')}].",
                        detail=f"Parameter '{pname}' of '{ref.name}' value {pval} "
                        f"above maximum {c['max']}.",
                    )
                if "options" in c and isinstance(pval, str) and pval not in c["options"]:
                    emit(
                        issues,
                        "PARAM_INVALID_OPTION",
                        location=_format_location(ref.location),
                        param=pname,
                        component=ref.name,
                        value=pval,
                        options=c["options"],
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
                    emit(
                        issues,
                        "PARAM_INVALID_VALUE",
                        location=_format_location(ref.location),
                        suggestion="Adjust weight values so they sum to 1.0.",
                        detail=f"Parameter 'weights' of '{ref.name}' must sum to 1.0, "
                        f"got {weight_sum:.6f}.",
                    )

        # Cross-parameter constraints (param_constraints, constraint schema v1).
        # Shapes are guaranteed by registration-time validation
        # (pipeline_engine.base.registration._validate_param_constraints):
        # every entry carries a known "rule" discriminator, and "requires"
        # entries carry a non-empty "when" condition dict.
        if sig.param_constraints:
            for constraint in sig.param_constraints:
                group_params = constraint.get("params", [])
                rule = constraint.get("rule", "")
                provided = [p for p in group_params if ref.params.get(p) is not None]

                if rule == "exactly_one":
                    if len(provided) == 0:
                        emit(
                            issues,
                            "PARAM_GROUP_MISSING",
                            location=_format_location(ref.location),
                            component=ref.name,
                            group=", ".join(group_params),
                        )
                    elif len(provided) > 1:
                        emit(
                            issues,
                            "PARAM_GROUP_CONFLICT",
                            location=_format_location(ref.location),
                            component=ref.name,
                            arity="only one",
                            group=", ".join(group_params),
                            provided=", ".join(provided),
                        )
                elif rule == "at_most_one":
                    if len(provided) > 1:
                        emit(
                            issues,
                            "PARAM_GROUP_CONFLICT",
                            location=_format_location(ref.location),
                            component=ref.name,
                            arity="at most one",
                            group=", ".join(group_params),
                            provided=", ".join(provided),
                        )
                elif rule == "requires":
                    # Conditional requirement: when every `when` condition
                    # matches the effective (explicit-or-default) value —
                    # i.e. what __init__ will actually see — every param in
                    # `params` must be provided. Mirrored in the TS editor
                    # validator (pass5-params.ts).
                    when = constraint.get("when") or {}
                    if not when:
                        # Registration mandates a non-empty `when` for
                        # `requires` entries — a missing condition means the
                        # signature bypassed the registration gate. Raise
                        # rather than guess between "unconditional" and
                        # "skip" (no silent fallbacks).
                        raise ValueError(
                            f"Component '{ref.name}' has a 'requires' "
                            f"param_constraints entry without a 'when' "
                            f"condition: {constraint!r}. Fix the registry "
                            f"source (re-register the component or "
                            f"regenerate the registry snapshot)."
                        )
                    cond_values = {
                        k: _effective_param_value(ref.params, reg_params, k) for k in when
                    }
                    # A condition on a variable-bound param can't be evaluated
                    # statically — same policy as pass 5's type checks.
                    if any(isinstance(v, VariableRef) for v in cond_values.values()):
                        continue
                    if all(cond_values[k] == v for k, v in when.items()):
                        # A param is missing when its effective value is None
                        # (unset, or explicitly None). Variable-bound values
                        # are non-None VariableRefs → count as provided.
                        missing = [
                            p
                            for p in group_params
                            if _effective_param_value(ref.params, reg_params, p) is None
                        ]
                        if missing:
                            cond_text = " and ".join(f"{k}={v!r}" for k, v in when.items())
                            emit(
                                issues,
                                "PARAM_REQUIRES_MISSING",
                                location=_format_location(ref.location),
                                component=ref.name,
                                missing=", ".join(missing),
                                condition=cond_text,
                                when_params=" / ".join(when.keys()),
                            )
                else:
                    # Registration (base/registration.py) admits only the
                    # schema-v1 rules, so an unknown rule here means this
                    # signature bypassed @register_component (e.g. a stale
                    # or hand-built registry snapshot). Fail loudly — the
                    # validator does not guess (no silent fallbacks).
                    raise ValueError(
                        f"Component '{ref.name}' has a param_constraints entry "
                        f"with unknown rule {rule!r}: {constraint!r}. Valid "
                        f"rules: ['at_most_one', 'exactly_one', 'requires']. "
                        f"Fix the registry source (re-register the component "
                        f"or regenerate the registry snapshot)."
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
    # (Per-branch terminal types are threaded directly into
    # ``_check_composer_input_types`` as ``branch_types`` below.)
    prev_parallel: ParallelSpec | None = None

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
                emit(
                    issues,
                    "DICT_INPUT_EXPECTED",
                    location=_format_location(step.location),
                    suggestion=f"Place a Parallel block before '{step.name}'.",
                    step=f"Composer '{step.name}'",
                    actual=type_name(current_type),
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
                emit(
                    issues,
                    "DICT_NOT_CONSUMED",
                    location=_format_location(step.location),
                    step=step.name,
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
                        emit(
                            issues,
                            "TYPE_MISMATCH",
                            location=_format_location(step.location),
                            suggestion=suggestion,
                            context=step_context,
                            step=step.name,
                            expected=type_name(sig.input_type),
                            actual=type_name(current_type),
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
                    if step_out_name is not None and step_out_name not in expected_outputs:
                        emit(
                            issues,
                            "TRANSITION_OUTPUT_MISMATCH",
                            location=_format_location(step.location),
                            step=step.name,
                            category=sig.category.value,
                            output=step_out_name,
                            prev_output=prev_out_name,
                            expected_outputs=expected_outputs,
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
            slot_types[step.slot_name] = _store_value_slot_type(step.value)
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
                emit(
                    issues,
                    "DICT_INPUT_EXPECTED",
                    location=_format_location(step.location),
                    severity_context="extract",
                    suggestion="Place a Parallel block before 'Extract'.",
                    step="'Extract'",
                    actual=type_name(current_type),
                )
                current_type = TypingAny
            elif step.key not in prev_parallel.branches:
                emit(
                    issues,
                    "EXTRACT_MISSING_KEY",
                    location=_format_location(step.location),
                    key=step.key,
                    branches=sorted(prev_parallel.branches.keys()),
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
                emit(
                    issues,
                    "COMPOSER_KEY_MISMATCH",
                    location=_format_location(next_step.location),
                    composer=next_step.name,
                    param=param_name,
                    extra=sorted(extra),
                    branches=sorted(branch_names),
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
                emit(
                    issues,
                    "COMPOSER_INPUT_TYPE_MISMATCH",
                    location=_format_location(next_step.location),
                    suggestion=(
                        f"Change branch '{branch_name}' to end with a step "
                        f"that outputs {_format_expected(expected)}, or point "
                        f"'{role_param}' at a different branch."
                    ),
                    detail=(
                        f"Composer '{next_step.name}' role '{role_param}' "
                        f"references branch '{branch_name}' which outputs "
                        f"'{type_name(actual)}', but expects "
                        f"{_format_expected(expected)}."
                    ),
                )
    else:
        # Homogeneous: every branch must match
        for branch_name, actual in branch_types.items():
            if not _composer_accepts(actual, composer_inputs):
                emit(
                    issues,
                    "COMPOSER_INPUT_TYPE_MISMATCH",
                    location=_format_location(next_step.location),
                    suggestion=(
                        f"Change branch '{branch_name}' to end with a step "
                        f"that outputs {_format_expected(composer_inputs)}."
                    ),
                    detail=(
                        f"Composer '{next_step.name}' expects every Parallel "
                        f"branch to output {_format_expected(composer_inputs)}, "
                        f"but branch '{branch_name}' outputs "
                        f"'{type_name(actual)}'."
                    ),
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
                    emit(
                        issues,
                        "PHASE_ORDER_VIOLATION",
                        location=_format_location(step.location),
                        step=step.name,
                        category=sig.category.value,
                        expected_group=expected_group,
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
    """Pass 8: Validate Store/Load pairs, slot-reference params, slot types.

    Uses a single registry view — the lock-effective registry passed in.
    No fallback to full_registry. A component referenced in source but
    missing from the lock-effective registry surfaces upstream as
    UNKNOWN_COMPONENT (pass 4) or INVALID_VERSION_LOCK (pass 4) — those
    are loud structured errors the user must address. Pass 8 doesn't
    over-helpfully complete the slot_reads contract for a component the
    lock can't account for.

    The fallback that existed here (commit 310dc5a8, 2026-06-22) was added
    to suppress noisy false-positive SLOT_UNUSED warnings when the agent
    passed a stale partial lock to validate. That class of bug is fixed
    at the source — see services/chat-api/src/agent/executor.py which now
    evolves the saved lock against in-memory source before calling
    validate — so the fallback is no longer load-bearing.
    """
    available_slots: dict[str, tuple] = {}
    used_slots: set[str] = set()

    _validate_slots_in_pipeline(
        strategy.pipeline,
        registry,
        issues,
        available_slots,
        used_slots,
        slot_types,
    )

    # Check for unused stores
    for slot_name, (_, store_loc) in available_slots.items():
        if slot_name not in used_slots:
            emit(
                issues,
                "SLOT_UNUSED",
                location=_format_location(store_loc),
                slot=slot_name,
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
            available_slots[step.slot_name] = (
                _store_value_slot_type(step.value),
                step.location,
            )

        elif isinstance(step, SlotLoadSpec):
            used_slots.add(step.slot_name)
            if step.slot_name not in available_slots:
                emit(
                    issues,
                    "SLOT_NOT_FOUND",
                    location=_format_location(step.location),
                    slot=step.slot_name,
                )

        elif isinstance(step, ComponentRef):
            # Single registry view. If the lock-effective registry doesn't
            # have this component, pass 4 already emitted UNKNOWN_COMPONENT
            # or INVALID_VERSION_LOCK with line locations — that's the
            # actionable error. We don't fall back to full_registry to mask
            # the gap; SLOT_UNUSED noise on upstream Stores is acceptable
            # in that already-broken state.
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
                            emit(
                                issues,
                                "SLOT_REF_NOT_FOUND",
                                location=_format_location(step.location),
                                component=step.name,
                                param=param_name,
                                slot=slot_name_val,
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
                                emit(
                                    issues,
                                    "SLOT_TYPE_MISMATCH",
                                    location=_format_location(step.location),
                                    component=step.name,
                                    param=param_name,
                                    expected=type_name(expected_type),
                                    slot=slot_name_val,
                                    stored=type_name(stored_type),
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
                step,
                registry,
                issues,
                available_slots,
                used_slots,
                slot_types,
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
    full_registry: dict[str, Any],
    issues: list[ValidationIssue],
    production_mode: bool = False,
) -> None:
    """Pass 9: Validate Globals, Universe, and declaration references.

    `full_registry` is a fallback for the unused-globals existence check —
    same precedent as pass 8: a component missing from the locked view (e.g.
    in-progress strategy with a stale partial lock) would otherwise be
    silently skipped, making globals that only it consumes look unused.
    Semantic checks (declaration-ref correctness) continue to use only the
    locked `registry` because those are version-accurate questions.
    """
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

    # E) Unused globals warning — uses full_registry fallback so we don't
    # false-flag globals consumed by a component missing from the lock.
    _warn_unused_globals(strategy, expanded, registry, full_registry, issues)

    # F) Resampler config validation (source_tf, target_tf, bar_offset rule table)
    _validate_resampler_config(strategy, expanded, issues)


def _validate_globals(globals_: GlobalsSpec, issues: list[ValidationIssue]) -> None:
    """Validate Globals declaration values."""
    loc = "globals"
    if globals_.location:
        loc = _format_location(globals_.location)

    if globals_.target_timeframe is not None:
        if globals_.target_timeframe not in _VALID_TIMEFRAMES:
            emit(
                issues,
                "INVALID_GLOBAL",
                location=loc,
                detail=f"Globals target_timeframe '{globals_.target_timeframe}' is not a valid "
                f"timeframe. Valid: {sorted(_VALID_TIMEFRAMES)}",
            )

    if globals_.bar_offset is not None:
        # Use the shared parser — strict grammar unified with the TS editor
        # validator (spec 02 §4.4): '^(\\d+)(min|h|d|w)$', case-sensitive, no
        # whitespace. Resampler-config cross-checks (multiple-of-source,
        # less-than-target) run in _validate_resampler_config.
        try:
            parse_bar_offset_minutes(globals_.bar_offset)
        except ValueError as e:
            emit(
                issues,
                "INVALID_GLOBAL",
                location=loc,
                detail=f"Globals bar_offset: {e}",
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
            emit(
                issues,
                "INVALID_UNIVERSE",
                location=loc,
                detail="Universe mode='manual' requires 'symbols' or 'resolved' to be set.",
            )
    elif universe.mode == "category":
        if not universe.categories:
            emit(
                issues,
                "INVALID_UNIVERSE",
                location=loc,
                detail="Universe mode='category' requires 'categories' to be set.",
            )
    elif universe.mode == "top_volume":
        if universe.top_n is None:
            emit(
                issues,
                "INVALID_UNIVERSE",
                location=loc,
                detail="Universe mode='top_volume' requires 'top_n' to be set.",
            )
    else:
        emit(
            issues,
            "INVALID_UNIVERSE",
            location=loc,
            detail=f"Unknown Universe mode '{universe.mode}'. "
            f"Valid modes: manual, category, top_volume",
        )

    # ── Resolved-list checks ────────────────────────────────────────────────
    # The DSL invariant we want to enforce: every strategy promoted to a
    # production path (deploy / backtest submit) has a resolved asset list
    # baked into its source. The web editor maintains this automatically;
    # CLI / MCP paths must too. This block is the single gate.
    # production_mode promotion (warning → error) is catalog policy:
    # promote_in_production=True on UNRESOLVED_UNIVERSE / STALE_UNIVERSE.
    has_resolved = bool(universe.resolved)  # non-None and non-empty
    resolved_is_explicit_empty = universe.resolved is not None and len(universe.resolved) == 0

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
            emit(issues, "EMPTY_UNIVERSE", location=loc)
        else:
            # Manual mode with explicit `symbols` is self-sufficient; the
            # resolver derives `resolved` from `symbols`. Skip the warning.
            manual_self_sufficient = universe.mode == "manual" and bool(universe.symbols)
            if not manual_self_sufficient:
                emit(
                    issues,
                    "UNRESOLVED_UNIVERSE",
                    location=loc,
                    production_mode=production_mode,
                )

    # ── Stale-list structural check ────────────────────────────────────────
    # If resolved is populated, sanity-check that it lines up with the
    # criteria in the same DSL. This catches direct hand-edits to the source
    # that change criteria without re-resolving (e.g., bumping top_n from
    # 30 to 50 but leaving the 30-symbol resolved list in place). Without
    # this, the deploy guard accepts a non-empty `resolved` that no longer
    # matches what the strategy declares.
    if has_resolved:
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
                emit(
                    issues,
                    "STALE_UNIVERSE",
                    location=loc,
                    production_mode=production_mode,
                    detail="Universe 'resolved' list does not match declared 'symbols' "
                    f"(after exclusions/inclusions). Resolved has {resolved_count} "
                    f"items; criteria imply {len(expected)}. Re-resolve via "
                    "universe_resolve / `keel universe resolve` / web editor.",
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
                emit(
                    issues,
                    "STALE_UNIVERSE",
                    location=loc,
                    production_mode=production_mode,
                    detail=f"Universe 'resolved' has {resolved_count} items but "
                    f"top_n={universe.top_n} implies "
                    f"{expected_lower}–{expected_upper} (after exclusions/inclusions). "
                    "Criteria likely changed since last resolve — re-resolve via "
                    "universe_resolve / `keel universe resolve` / web editor.",
                )
        # For mode='category' we can't structurally verify staleness without
        # querying the registry. Leave it to eval-worker / runtime checks.

    # exclusions and inclusions must not overlap (mirrored in TS pass 9 —
    # ported 2026-07-10 per spec 02 Q2; fixture universe_exclusions_overlap)
    if universe.exclusions and universe.inclusions:
        overlap = set(universe.exclusions) & set(universe.inclusions)
        if overlap:
            emit(
                issues,
                "INVALID_UNIVERSE",
                location=loc,
                detail=f"Universe exclusions and inclusions overlap: {sorted(overlap)}",
            )

    # Groups must be subsets of resolved
    if universe.groups and universe.resolved:
        resolved_set = set(universe.resolved)
        for group_name, group_symbols in universe.groups.items():
            not_in_resolved = set(group_symbols) - resolved_set
            if not_in_resolved:
                emit(
                    issues,
                    "INVALID_UNIVERSE_GROUP",
                    location=loc,
                    group=group_name,
                    symbols=sorted(not_in_resolved),
                )


# Valid Execution option sets are DERIVED from EXECUTION_PARAM_META in spec.py
# (the single source of truth), not hardcoded here. The TS editor validator
# derives the same sets from the generated execution_param_meta.json, so neither
# validator hardcodes execution literals.
_VALID_REBALANCE = EXECUTION_VALID_REBALANCE
_VALID_BUFFER_MODE = EXECUTION_VALID_BUFFER_MODE
_VALID_REBALANCE_METHOD = EXECUTION_VALID_REBALANCE_METHOD


def _is_param_at_default(execution: ExecutionSpec, param: str) -> bool:
    """True if ``execution.<param>`` still equals its canonical registry default.

    Defaults come from ``EXECUTION_PARAM_META`` (the single source of truth in
    spec.py), NOT hardcoded literals — so an "irrelevant param" warning only
    fires when the user explicitly set a NON-default value in a mode where the
    param has no effect. A param left at its registry default never warns.
    """
    return getattr(execution, param) == EXECUTION_PARAM_META[param]["default"]


def _validate_execution(execution: ExecutionSpec, issues: list[ValidationIssue]) -> None:
    """Validate Execution declaration values."""
    loc = "execution"
    if execution.location:
        loc = _format_location(execution.location)

    # Mode validation
    if execution.rebalance not in _VALID_REBALANCE:
        emit(
            issues,
            "INVALID_EXECUTION",
            location=loc,
            param="rebalance mode",
            value=execution.rebalance,
            options=sorted(_VALID_REBALANCE),
        )
        return  # short-circuit — other checks depend on valid mode

    # Conditional requirements
    if execution.rebalance == "buffered" and execution.buffer_threshold is None:
        emit(
            issues,
            "MISSING_EXECUTION_PARAM",
            location=loc,
            param="buffer_threshold",
            rebalance="buffered",
        )

    # Irrelevant param warnings — the advisory half of the B6 fix.
    #
    # The emitters KEEP every explicitly-set Execution param (spec 04 §4:
    # execution_params_to_emit is the single emit policy for spec_to_dsl AND
    # spec_to_graph); this warning is the channel that informs the user a kept
    # param has no effect in the current mode, replacing the old behavior
    # where spec_to_dsl silently deleted it. A param warns when its mode is
    # inactive AND it was explicitly set (any value, ExecutionSpec.explicit)
    # — key presence in the DSL call / graph dict is the explicitness signal,
    # matching the TS validator, which warns on key presence. The non-default
    # value check is kept as a fallback for programmatically-built specs that
    # don't populate `explicit`: a back-filled registry default never warns
    # (defaults come from EXECUTION_PARAM_META, the single source of truth in
    # spec.py — NOT hardcoded literals. Hardcoding the literal is how B12
    # happened: rebalance_method's default is "to_center", but the validator
    # compared against "to_edge", so every non-buffered strategy left at the
    # default tripped a spurious warning).
    for param_name, meta in EXECUTION_PARAM_META.items():
        modes = meta.get("modes")
        if not modes or execution.rebalance in modes:
            continue
        explicitly_set = param_name in execution.explicit
        if not explicitly_set and _is_param_at_default(execution, param_name):
            continue
        if param_name == "buffer_threshold":
            # Rendered suggestion ({mode} = first mode where the param applies)
            emit(
                issues,
                "IRRELEVANT_EXECUTION_PARAM",
                location=loc,
                param=param_name,
                rebalance=execution.rebalance,
                mode=modes[0],
            )
        else:
            emit(
                issues,
                "IRRELEVANT_EXECUTION_PARAM",
                location=loc,
                suggestion=None,
                param=param_name,
                rebalance=execution.rebalance,
            )

    # Range checks — bounds come from EXECUTION_PARAM_META's min/max keys
    # (libs/pipeline_engine/dsl/spec.py), the single source of truth shared
    # with the TS editor validator (via the generated execution_param_meta
    # .json) and keel-api's /components/metadata. Spec 02 T-15 deleted the
    # three hardcoded literal copies this loop replaces. A ranged param with
    # value None is unset — nothing to range-check (buffer_threshold's
    # missing-when-required case is MISSING_EXECUTION_PARAM above).
    for param_name, meta in EXECUTION_PARAM_META.items():
        if "min" not in meta and "max" not in meta:
            continue
        lo, hi = meta["min"], meta["max"]  # spec_test pins min ⟺ max pairing
        value = getattr(execution, param_name)
        if value is None:
            continue
        if not (lo <= value <= hi):
            emit(
                issues,
                "PARAM_OUT_OF_RANGE",
                location=loc,
                detail=f"{param_name}={value} out of range [{lo}, {hi}]",
            )

    # Value checks
    if execution.buffer_mode not in _VALID_BUFFER_MODE:
        emit(
            issues,
            "INVALID_EXECUTION",
            location=loc,
            param="buffer_mode",
            value=execution.buffer_mode,
            options=sorted(_VALID_BUFFER_MODE),
        )
    if execution.rebalance_method not in _VALID_REBALANCE_METHOD:
        emit(
            issues,
            "INVALID_EXECUTION",
            location=loc,
            param="rebalance_method",
            value=execution.rebalance_method,
            options=sorted(_VALID_REBALANCE_METHOD),
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

    # Walk all components in expanded pipeline, check declaration refs.
    # Uses locked `registry` only (no full_registry fallback) — declaration_refs
    # are a version-specific semantic contract: which params on THIS version of
    # the component reference Globals/Universe namespaces. Falling back to the
    # latest signature here would weaken the version guarantee and could mask
    # real breakage. Cross-component existence checks (passes 2, 4, 8,
    # _warn_unused_globals) use full_registry; semantic checks like this one
    # stay strict.
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
                        emit(
                            issues,
                            "MISSING_DECLARATION_REF",
                            location=loc,
                            suggestion="Add groups to your Universe declaration.",
                            detail=f"Component '{comp_ref.name}' parameter '{param_name}' "
                            f"references group '{group_name}' but no Universe groups are defined.",
                        )
                    elif group_name not in groups:
                        emit(
                            issues,
                            "MISSING_DECLARATION_REF",
                            location=loc,
                            detail=f"Component '{comp_ref.name}' parameter '{param_name}' "
                            f"references group '{group_name}' which does not exist in Universe. "
                            f"Available groups: {sorted(groups.keys())}",
                        )
            else:
                # Simple scalar ref (e.g., "globals.target_timeframe")
                if namespace not in scope:
                    # Determine available values in the same top-level namespace
                    ns_prefix = namespace.split(".")[0] + "."
                    available = sorted(k for k in scope if k.startswith(ns_prefix))
                    emit(
                        issues,
                        "MISSING_DECLARATION_REF",
                        location=loc,
                        suggestion=f"Add {namespace.split('.')[-1]} to your "
                        f"{namespace.split('.')[0].title()} declaration.",
                        detail=f"Component '{comp_ref.name}' requires '{namespace}' "
                        f"but it is not declared in Globals/Universe. "
                        + (f"Available: {available}" if available else "No globals declared."),
                    )

        # Optional declaration refs — no error if missing, but mark as info
        # (no action needed — they'll resolve to None at runtime)


def _warn_unused_globals(
    strategy: StrategyFile,
    expanded: StrategyFile,
    registry: dict[str, Any],
    full_registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Warn about globals that are declared but never referenced by any component.

    Cross-component existence check — falls back to `full_registry` when a
    component isn't in the locked view, otherwise we'd silently skip its
    declaration_refs and produce false-positive UNUSED_GLOBAL warnings.
    Same pattern as pass 8's slot_reads fallback.
    """
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
        sig = registry.get(comp_ref.name) or full_registry.get(comp_ref.name)
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
        emit(issues, "UNUSED_GLOBAL", location="globals", field=field_name)


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
        # message_override: the shared rule table's ValueError text IS the
        # message (validate_resample_config is the single source, shared with
        # the runtime resampler); the catalog templates mirror it verbatim.
        emit(issues, code, location="globals", message_override=msg)
        return  # Don't emit the no-op warning when the config is already errored

    # No-op resampler warning: same TF + a TargetTimeframeResampler in the
    # pipeline. The runtime now short-circuits this case cleanly, but the
    # component step itself is wasted and the Globals declaration is redundant.
    if source_tf == target_tf and _has_target_timeframe_resampler(expanded):
        emit(
            issues,
            "RESAMPLER_NOOP",
            location="globals",
            target_tf=target_tf,
            source_tf=source_tf,
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
