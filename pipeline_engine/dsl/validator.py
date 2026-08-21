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
    _SEVERITY_RANK,
    _STAGE_CAP,
    RULES,
    SEVERITY_BY_CATEGORY,
    STAGED_CHANGES,
    CatalogError,
    Stage,
    _template_placeholders,
    severity_for,
)
from pipeline_engine.dsl.judgments import emit_template
from pipeline_engine.dsl.relation import domain_leq
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
from pipeline_engine.dsl.trace_shim import (
    Schema0Sink,
    Schema1Sink,
    activate_trace_sink,
    active_trace_sink,
)
from pipeline_engine.validation_shared import (
    PHASE_INDEX,
    TIMEFRAME_MINUTES,
    UNIVERSE_MASK_APPLIERS,
    StagingNote,
    TypeFlowEntry,
    TypeRef,
    ValidationIssue,
    ValidationResult,
    ValidOption,
    is_universe_mask_phase_exempt,
    param_accepts_numeric,
    param_display_type,
    parse_bar_offset_minutes,
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


def _store_value_slot_type(value: Any) -> type:
    """Slot type recorded for a ``StoreValue`` literal — shared by passes 6 + 8.

    Honest typing: ``type(value)`` — including ``type(None)`` for a literal
    ``None``. ``type(None)`` is already the validator's "unknown stored
    type" sentinel (the interpreter's J-STOREVALUE Σ-lifecycle row):
    pass 8 skips SLOT_TYPE_MISMATCH for
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


def _terminal_severity(rule: Any, change: Any) -> str:
    """The severity a staged code resolves to at its TERMINAL stage (spec 05 §3.1 #16).

    Base = declared override | category policy; capped by the terminal stage
    (PROMOTED → error, WARNING → warning) then the permanent ``severity_ceiling``
    (min only). This is the "error-bound" honesty an agent seeing a warning-stage
    emission needs.
    """
    sev = rule.severity_override or SEVERITY_BY_CATEGORY[rule.category]
    cap = _STAGE_CAP.get(Stage(change.terminal_stage))  # DORMANT absent; terminal never DORMANT
    if cap is not None and _SEVERITY_RANK[sev] > _SEVERITY_RANK[cap]:
        sev = cap
    if rule.severity_ceiling and _SEVERITY_RANK[sev] > _SEVERITY_RANK[rule.severity_ceiling]:
        sev = rule.severity_ceiling
    return sev


def _staging_note(rule: Any, row_staged_by: str | None) -> StagingNote | None:
    """Envelope #16: ``{stage, terminal_severity}`` for an ARMED staged code.

    Row-key first (spec 05 §2.2/F7c). ``None`` for un-staged codes and for
    flow-shape entries (which carry no severity). DORMANT codes never reach here
    — ``severity_for`` raises before the envelope is built.
    """
    key = row_staged_by or rule.staged_by
    if not key:
        return None
    change = STAGED_CHANGES.get(key)
    if change is None or change.kind == "flow-shape":
        return None
    return StagingNote(
        stage=Stage(change.stage).value,
        terminal_severity=_terminal_severity(rule, change),
    )


def emit(
    issues: list[ValidationIssue],
    code: str,
    *,
    location: str | None,
    severity_context: str | None = None,
    production_mode: bool = False,
    suggestion: Any = _UNSET,
    message_override: str | None = None,
    row_staged_by: str | None = None,
    expected: TypeRef | str | None = None,
    actual: TypeRef | str | None = None,
    provenance: tuple = (),
    path: tuple = (),
    span: Any = None,
    valid_options: tuple = (),
    suggested_edit: Any = None,
    applicability_override: str | None = None,
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
    - ``row_staged_by``: the emitting judgment row's staged-change key, when
      it carries one — ROW-KEY-FIRST severity resolution (spec 05 §2.2/F7c):
      the row's key selects the stage cap even when the rule itself carries
      no ``staged_by`` (split severity, e.g. the D4 sib slot row).

    Typed-envelope passthroughs (spec 05 §3.1/§3.3), all defaulted so the
    non-type-shaped sites are unchanged: ``expected``/``actual`` (TypeRef, dual
    purpose — also the ``{expected}``/``{actual}`` prose where the template uses
    them), ``provenance`` (ProvenanceHop chain on domain/slot outcomes),
    ``path``/``span``, ``valid_options`` and ``suggested_edit``. ``applicability``,
    ``recoverable`` and ``staging`` are STAMPED from the catalog (the
    dropped-applicability fix); ``tier`` is always ``"static"`` here.
    """
    rule = RULES.get(code)
    if rule is None:
        raise CatalogError(
            f"emit(): unknown issue code {code!r} — add the catalog entry "
            f"in dsl/catalog.py first (spec 02 §1.4 standing intake rule)."
        )

    # Severity resolves in ONE function (spec 05 §2.2): context/declared
    # overrides, the production_mode promotion, the staging cap, and the
    # severity ceiling all live in severity_for — a DORMANT-staged code
    # raises CatalogError here (structural silence, never a dropped issue).
    severity = severity_for(
        rule,
        context=severity_context,
        production_mode=production_mode,
        row_staged_by=row_staged_by,
    )

    required: set[str] = set()
    if message_override is None:
        required |= _template_placeholders(rule.message_template, code, "message_template")
    render_suggestion = suggestion is _UNSET and bool(rule.suggestion_template)
    if render_suggestion:
        required |= _template_placeholders(rule.suggestion_template, code, "suggestion_template")

    # ``expected``/``actual`` are DUAL-PURPOSE (spec 05 §3.3): the structured
    # ENVELOPE fields AND, where a template references ``{expected}``/``{actual}``,
    # the rendered prose. Inject their string form (a TypeRef renders as its
    # ``declared`` name — byte-identical to today's ``type_name(...)`` strings)
    # into the render namespace only when the template demands them. Every other
    # passthrough (path/span/provenance/valid_options/suggested_edit) is
    # envelope-only and never a template placeholder.
    render_ns = dict(params)
    for _name, _val in (("expected", expected), ("actual", actual)):
        if _name in required and _name not in render_ns and _val is not None:
            render_ns[_name] = _val.declared if isinstance(_val, TypeRef) else _val
    if set(render_ns) != required:
        raise CatalogError(
            f"emit({code}): template params mismatch — required "
            f"{sorted(required)}, got {sorted(render_ns)}."
        )

    if message_override is not None:
        message = message_override
    else:
        message = rule.message_template.format(**render_ns)
    if render_suggestion:
        rendered_suggestion: str | None = rule.suggestion_template.format(**render_ns)
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
            # ── Envelope extension (spec 05 §3.3) ──────────────────────────
            # tier is "static" for every validator/interpreter emission (§3.1
            # #6); the gate/compile/runtime tiers are stamped by their own
            # mints (T-M3a-2). applicability/recoverable/staging are STAMPED
            # from the catalog — the dropped-applicability fix (R6-I1/L102).
            # applicability_override is the dsl-mtf-clocks spec 02 §4.3
            # per-emission DOWNGRADE (machine_applicable → has_placeholders
            # when no single edit legalizes the site); the rule-level value
            # stays the declared default.
            tier="static",
            path=tuple(path),
            span=span,
            expected=expected if isinstance(expected, TypeRef) else None,
            actual=actual if isinstance(actual, TypeRef) else None,
            provenance=tuple(provenance),
            valid_options=tuple(valid_options),
            suggested_edit=suggested_edit,
            applicability=applicability_override or rule.applicability.value,
            recoverable=rule.recoverable,
            staging=_staging_note(rule, row_staged_by),
        )
    )

    # Schema-0 trace observation point 3 (spec 04 §2.2.2) — the single
    # emission site every issue flows through. Observation only: a no-op
    # unless a validate_strategy run activated a sink.
    sink = active_trace_sink()
    if sink is not None:
        sink.on_issue(code, severity, location)


def validate_strategy(
    strategy: StrategyFile,
    lock: dict[str, int] | None = None,
    production_mode: bool = False,
    trace_sink: Schema0Sink | Schema1Sink | None = None,
    resolution_pin: dict[str, int] | None = None,
) -> ValidationResult:
    """Validate a parsed StrategyFile against the component registry.

    Runs 9 validation passes:
    1. Variable and factory resolution
    2. Name collision check
    3. Factory expansion (3a: factory-call acyclicity gates BEFORE the
       expansion, which inlines factory bodies; 3b: variable-reference
       acyclicity after it — the structural gates the factory-inlining
       expansion and the VariableRef-inlining walks rely on)
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
        trace_sink: Optional trace sink (`dsl/trace_shim.py`) — a
            `Schema0Sink` (spec 04 §2.2.2's base trace) or, since the M2c
            cutover, a `Schema1Sink` (spec 04 §2.2's full typed trace: the
            interpreter walk feeds its typed hooks). Observation only —
            with the default `None` (the only shipped configuration) every
            hook site is a one-`if` no-op and behavior is byte-identical.
            When set, the sink records the pass-6 walk, the terminal
            pass-8 store map, every emitted issue, and the assembled
            document (read it via `trace_sink.document` after this
            returns).
        resolution_pin: Recorded-resolution replay seam (dsl-type-system
            spec 04 §2.2.7/§2.4) — trace-harness machinery only, never a
            production input. Maps component name → the REGISTERED version
            the pinned trace recorded (`step.version`). When set, the
            effective registry consulted by passes 5–9 is overlaid with
            exactly those versions AFTER the normal lock path computes its
            view, so registry evolution (a later version moving `latest`)
            can never move a recorded trace. Deliberately NOT a user lock:
            it engages none of the lock channels (no LOCK_DRIFT, no
            INVALID_VERSION_LOCK, no auto-lock change) — those report on
            the LIVE registry by design and keep doing so. A pin entry
            naming an unregistered component/version raises loudly
            (removing a version any pinned trace records is a
            corpus-invalidating breaking change, spec 02's registry-diff
            gate class) — never a silent fallback.
    """
    if trace_sink is None:
        return _validate_strategy_impl(
            strategy, lock, production_mode, trace_sink=None, resolution_pin=resolution_pin
        )
    with activate_trace_sink(trace_sink):
        return _validate_strategy_impl(
            strategy, lock, production_mode, trace_sink=trace_sink, resolution_pin=resolution_pin
        )


def _validate_strategy_impl(
    strategy: StrategyFile,
    lock: dict[str, int] | None,
    production_mode: bool,
    trace_sink: Schema0Sink | Schema1Sink | None,
    resolution_pin: dict[str, int] | None = None,
) -> ValidationResult:
    """Body of :func:`validate_strategy` (docstring there).

    Split out so the schema-0 trace sink can be activated around the whole
    run (the `emit()` observation point reads it via a contextvar) without
    re-indenting the pass spine into a `with` block.
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

    # Recorded-resolution replay (dsl-type-system spec 04 §2.2.7/§2.4):
    # overlay the passes-5–9 registry view with the versions a pinned trace
    # recorded, AFTER the normal lock path computed its view. Resolution
    # only — the lock channels above/below (auto-lock, LOCK_DRIFT,
    # INVALID_VERSION_LOCK) run unchanged against the LIVE registry, which
    # is exactly what they report on. An unresolvable pin entry is a loud
    # error: replaying recorded history requires the recorded version to
    # still be registered (spec 02's add-a-version-keep-the-old
    # discipline); its absence is a corpus-invalidating breaking change,
    # never something to paper over with latest.
    if resolution_pin:
        from pipeline_engine.base.registry import get_version

        pinned_sigs = {}
        unresolvable = {}
        for name, ver in resolution_pin.items():
            sig = get_version(name, ver)
            if sig is None:
                unresolvable[name] = ver
            else:
                pinned_sigs[name] = sig
        if unresolvable:
            raise RuntimeError(
                f"resolution_pin names unregistered component version(s) "
                f"{unresolvable} — a pinned trace recorded them, so they must "
                f"remain registered (spec 04 §2.2.7: recorded resolution binds "
                f"replay; spec 02 add-a-version-keep-the-old). Removing a "
                f"recorded version is a breaking registry change that "
                f"invalidates the trace corpus — handle it through the "
                f"registry-diff gate, never by resolving to latest."
            )
        registry = {**registry, **pinned_sigs}

    # Seed with any lock-generation errors so they surface in the final
    # result. Passes 2 + 4 still run with the full latest registry below
    # and will report the same unknown-component issues with line
    # locations attached.
    issues: list[ValidationIssue] = list(lock_gen_issues)
    type_flow: list[TypeFlowEntry] = []
    slot_types: dict[str, type] = {}
    # Terminal output name of the pass-6 walk for the trace sink. Runs that
    # short-circuit before pass 6 keep the walk's initial fold value
    # (rendered "None") — trace metadata only.
    final_name: str = "None"

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

    # Pass 3a: factory-call acyclicity — must gate BEFORE expansion (unlike
    # its 3b variable sibling, which needs the EXPANDED graph): pass 3
    # inlines factory bodies into their call sites and recurses into the
    # inlined body, so a cyclic factory graph would overflow inside the
    # expansion itself. On a cycle, short-circuit at the structural stage
    # with the passes-1/2/3a issues — exactly the structural-error return
    # below, minus the passes a cyclic graph makes unreachable.
    if _validate_factory_acyclicity(strategy, issues):
        cycle_errors, cycle_warnings, cycle_info = _group_by_severity(issues)
        if trace_sink is not None:
            trace_sink.finalize(final_name)
        return ValidationResult(
            valid=False,
            errors=cycle_errors,
            warnings=cycle_warnings,
            info=cycle_info,
            type_flow=type_flow,
        )

    # Pass 3: Factory expansion
    expanded = _expand_factories(strategy, issues)

    # Pass 3b: variable-reference acyclicity — the structural cycle gate
    # every VariableRef-inlining walk below (passes 6, 7, 7b, 8) relies on.
    _validate_variable_acyclicity(expanded, issues)

    # Only continue to registry-based passes if no structural errors
    structural_errors, structural_warnings, structural_info = _group_by_severity(issues)
    if structural_errors:
        if trace_sink is not None:
            trace_sink.finalize(final_name)
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
        if trace_sink is not None:
            trace_sink.finalize(final_name)
        return ValidationResult(
            valid=False,
            errors=ne,
            warnings=nw,
            info=ni,
            type_flow=type_flow,
        )

    # Pass 5: Parameter validation
    _validate_params(expanded, registry, issues)

    # Pass 6 position: the judgment-table interpreter walk (spec 03 §2.4,
    # M2c cutover). ONE walk carries passes 6+8 under the shipped
    # configuration (m2-compat at HEAD): typing issues emit inline;
    # slot-lifecycle outcomes (SLOT_NOT_FOUND / SLOT_REF_NOT_FOUND /
    # SLOT_TYPE_MISMATCH + usage facts) are journaled on the walk and
    # flushed at the pass-8 position below, preserving today's observable
    # issue order. The old hand-written pass bodies (_validate_type_flow /
    # _validate_slots_in_pipeline) were deleted at the M2e step (spec 03 §6
    # Step 3); this walk is the sole type-flow + slot path. Lazy import:
    # interpreter.py imports emit()/helpers from this module and loads the
    # generated judgment/domain tables at ITS import (loud-fail).
    from pipeline_engine.dsl.interpreter import Walk

    walk = Walk(registry, trace_sink=trace_sink)
    final_name = walk.run(expanded, issues, type_flow, slot_types).name

    # Pass 7: Phase ordering
    _validate_phase_ordering(expanded, registry, issues)

    # Pass 7b: universe-mask discipline (rolling-universe P2 (c)) — the
    # masked-bit taint walk behind the three staged Contract-C codes. Runs
    # after the interpreter walk so the terminal type is already in
    # type_flow; structurally silent while the staged changes are DORMANT.
    _validate_universe_mask_discipline(
        expanded, registry, issues, type_flow, production_mode=production_mode
    )

    # Pass 8 position: Σ-journal flush (flush mode — spec 03 §2.4/§5, R-12)
    _validate_slots(expanded, registry, issues, slot_types, trace_sink=trace_sink, walk=walk)

    # Pass 9: Globals, Universe, and declaration reference validation
    _validate_declarations(
        strategy, expanded, registry, full_registry, issues, production_mode=production_mode
    )

    errors, warnings, info = _group_by_severity(issues)

    # Build pipeline summary from type flow
    pipeline_summary = _build_pipeline_summary(type_flow)

    # Schema-0 trace observation point 4 (spec 04 §2.2.2) — assemble the
    # document once the complete issue set has been observed.
    if trace_sink is not None:
        trace_sink.finalize(final_name)

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


def _collect_variable_refs(spec: Any) -> set[str]:
    """Recursively collect all VariableRef names referenced in a spec tree or value.

    Covers refs used as steps, refs at any depth inside component params and
    factory-call args, and refs inside plain container values (a variable may
    be assigned e.g. ``x = [a, b]``).
    """
    refs: set[str] = set()
    if isinstance(spec, VariableRef):
        refs.add(spec.name)
    elif isinstance(spec, PipelineSpec):
        for step in spec.steps:
            refs |= _collect_variable_refs(step)
    elif isinstance(spec, ParallelSpec):
        for branch_steps in spec.branches.values():
            for step in branch_steps:
                refs |= _collect_variable_refs(step)
    elif isinstance(spec, ComponentRef):
        for pval in spec.params.values():
            refs |= {r.name for r in iter_variable_refs(pval)}
    elif isinstance(spec, FactoryCallSpec):
        # Factory bodies are resolved separately, but the call's args are
        # resolved from the surrounding scope — they are dependencies.
        for aval in spec.args.values():
            refs |= {r.name for r in iter_variable_refs(aval)}
    elif isinstance(spec, (list, tuple, dict)):
        refs |= {r.name for r in iter_variable_refs(spec)}
    return refs


def _variable_deps(variables: list) -> dict[str, set[str]]:
    """Dependency graph: var_name -> the other var_names its value references.

    ``_collect_variable_refs`` walks pipelines, component params (at any
    depth), factory-call args, and plain container values alike; external
    (non-variable) refs are excluded.
    """
    var_names = {v.name for v in variables}
    return {v.name: _collect_variable_refs(v.value) & var_names for v in variables}


def _peel_and_name_cycle(
    deps: dict[str, set[str]], original_order: dict[str, int]
) -> list[str] | None:
    """One concrete cycle in a name-dependency graph, or None when acyclic.

    The shared peel-and-walk behind :func:`find_variable_cycle` and
    :func:`find_factory_cycle` — one algorithm, one deterministic rendering
    in both engines' messages for both cycle classes.

    Deterministic naming: peel every node whose dependencies are all
    resolved (the Kahn fixpoint — what remains is exactly the cycles plus
    their transitive dependents); start from the FIRST-DEFINED remaining
    node and always follow its first-defined remaining dependency until a
    node repeats. The returned list closes the loop explicitly, e.g.
    ``['a', 'b', 'a']`` renders as ``a → b → a``.
    """
    # Fixpoint peel: repeatedly discard nodes with no unresolved deps.
    # Terminates with exactly the on-cycle nodes + their dependents.
    remaining = set(deps)
    changed = True
    while changed:
        changed = False
        for name in list(remaining):
            if not (deps[name] & remaining):
                remaining.discard(name)
                changed = True
    if not remaining:
        return None

    # Every remaining node still depends on at least one other remaining
    # node, so following deps inside the set must revisit a node — walk it
    # (first-defined order at every choice) to name one concrete cycle.
    path: list[str] = []
    index: dict[str, int] = {}
    cur = min(remaining, key=lambda n: original_order[n])
    while cur not in index:
        index[cur] = len(path)
        path.append(cur)
        cur = min(
            (d for d in deps[cur] if d in remaining),
            key=lambda n: original_order[n],
        )
    return path[index[cur] :] + [cur]


def find_variable_cycle(variables: list) -> list[str] | None:
    """One concrete cycle in the variable-reference graph, or None when acyclic.

    The shared cycle oracle behind BOTH surfaces of the circular-variable
    contract: ``_topo_sort_variables`` names its ``ResolveError`` from it
    (the runtime resolve path, core-engine-audit F18) and the validator's
    pass 3b emits ``PIPELINE_VARIABLE_CYCLE`` from it (the write-time path)
    — one algorithm, one deterministic rendering in both engines' messages
    (:func:`_peel_and_name_cycle`; ``['a', 'b', 'a']`` renders ``a → b → a``).
    """
    if not variables:
        return None
    return _peel_and_name_cycle(
        _variable_deps(variables), {v.name: i for i, v in enumerate(variables)}
    )


def _collect_factory_calls(spec: Any) -> set[str]:
    """Recursively collect FactoryCallSpec names in a step tree.

    Edge semantics follow pass-3 expansion EXACTLY — an edge exists where
    ``_expand_factories`` would recurse: factory calls used as steps, at any
    structural depth (parallel branches, nested pipelines). ``VariableRef``
    steps are deliberately NOT followed — expansion leaves them in place
    (variable bodies are expanded separately), so a loop routed through a
    variable body surfaces post-expansion as a direct variable→variable edge
    (pass 3b, ``PIPELINE_VARIABLE_CYCLE``), never as a factory edge.
    Component params and factory-call args hold values, not steps — no edges.
    """
    calls: set[str] = set()
    if isinstance(spec, FactoryCallSpec):
        calls.add(spec.name)
    elif isinstance(spec, PipelineSpec):
        for step in spec.steps:
            calls |= _collect_factory_calls(step)
    elif isinstance(spec, ParallelSpec):
        for branch_steps in spec.branches.values():
            for step in branch_steps:
                calls |= _collect_factory_calls(step)
    return calls


def _factory_deps(factories: list) -> dict[str, set[str]]:
    """Dependency graph: factory_name -> the other factories its body calls.

    Unknown call targets are excluded — they are pass-1/pass-4 territory
    (``UNDEFINED_VARIABLE`` / ``UNKNOWN_COMPONENT``), not cycle edges.
    """
    factory_names = {f.name for f in factories}
    return {f.name: _collect_factory_calls(f.body) & factory_names for f in factories}


def find_factory_cycle(factories: list) -> list[str] | None:
    """One concrete cycle in the factory-call graph, or None when acyclic.

    The shared cycle oracle behind BOTH surfaces of the circular-factory
    contract (the factory sibling of :func:`find_variable_cycle`):
    ``resolve_strategy`` names its ``ResolveError`` from it (the runtime
    resolve path — ``_resolve_factory_call`` inlines factory bodies and
    would recurse unbounded) and the validator's pass 3a emits
    ``FACTORY_CALL_CYCLE`` from it (the write-time path) — one algorithm,
    one deterministic rendering in both engines' messages
    (:func:`_peel_and_name_cycle`; ``['f', 'g', 'f']`` renders ``f → g → f``).

    Checks ALL defined factories, called or not — a cyclic definition can
    never be legitimately called (any call site would recurse pass-3
    expansion unbounded), so the defect is in the definitions themselves.
    """
    if not factories:
        return None
    return _peel_and_name_cycle(
        _factory_deps(factories), {f.name: i for i, f in enumerate(factories)}
    )


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
# PASS 3a: Factory-call acyclicity
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_factory_acyclicity(strategy: StrategyFile, issues: list[ValidationIssue]) -> bool:
    """Pass 3a: the factory-call graph must be a DAG (FACTORY_CALL_CYCLE).

    Runs on the RAW strategy BEFORE pass-3 expansion — the placement is the
    one deliberate difference from its variable sibling (pass 3b runs on
    the EXPANDED strategy): here the overflow site IS the expansion —
    ``_expand_factories`` inlines a factory body at every call site and
    recurses into the inlined body, so a factory that reaches itself —
    directly (``f`` calls ``f``) or mutually (``f`` calls ``g``, ``g``
    calls ``f``) — recurses unbounded before any later pass could look.
    Edges are the ones expansion actually follows (``_collect_factory_calls``:
    factory calls as steps at any structural depth, never through
    ``VariableRef`` — variable-routed loops are pass 3b's
    ``PIPELINE_VARIABLE_CYCLE``, visible post-expansion as direct
    variable→variable edges). The runtime twin is ``resolve_strategy``'s
    ResolveError (same :func:`find_factory_cycle` oracle, same deterministic
    cycle naming); ``verify_spec`` runs this check too, before it expands a
    stored artifact; ``_expand_factories`` keeps a defensive raise for any
    remaining ungated caller.

    Returns True when a cycle was found — the caller must then SKIP pass-3
    expansion entirely and short-circuit at the structural stage.
    """
    cycle = find_factory_cycle(strategy.factories)
    if cycle is None:
        return False
    first = next((f for f in strategy.factories if f.name == cycle[0]), None)
    emit(
        issues,
        "FACTORY_CALL_CYCLE",
        location=_format_location(first.location) if first is not None else None,
        cycle=" → ".join(cycle),
    )
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 3: Factory expansion
# ═══════════════════════════════════════════════════════════════════════════════


def _expand_factories(strategy: StrategyFile, issues: list[ValidationIssue]) -> StrategyFile:
    """Pass 3: Expand all FactoryCallSpec into PipelineSpec."""
    factory_map = {f.name: f for f in strategy.factories}
    # Active-expansion stack — the defensive cycle backstop for UNGATED
    # callers (mirrors the interpreter Walk's _var_stack): a clean error,
    # never a silent skip and never a RecursionError. The gated entry
    # points (validate_strategy's pass 3a, verify_spec) refuse cyclic
    # factory graphs with FACTORY_CALL_CYCLE before this function runs.
    active: list[str] = []

    def _expand_step(step: StepSpec) -> StepSpec:
        if isinstance(step, FactoryCallSpec):
            factory = factory_map.get(step.name)
            if factory is None:
                # Will be caught by pass 1 or pass 4
                return step

            if step.name in active:
                raise RuntimeError(
                    f"Factory-call cycle reached pass-3 expansion: factory "
                    f"'{step.name}' is already being expanded "
                    f"({' → '.join([*active, step.name])}). Cyclic factory "
                    f"graphs fail validation with FACTORY_CALL_CYCLE — run "
                    f"validate_strategy or verify_spec before expanding."
                )

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

            active.append(step.name)
            try:
                return _expand_steps_in(expanded_body)
            finally:
                active.pop()

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

    # Preserve the declaration state (Globals/Universe/Execution) on the
    # expanded file. Pre-M4b the reconstruction silently DROPPED them —
    # harmless while every consumer read declarations from the ORIGINAL
    # strategy (pass 9's split), but the pass-6 clock facet reads κ_exec and
    # the declaration-backed transfer sources from the strategy it walks
    # (the expanded one), so the drop made every globals-wired clock
    # unresolvable (dsl-mtf-clocks T-M4b-1 root-cause fix, not a workaround).
    return StrategyFile(
        metadata=strategy.metadata,
        factories=strategy.factories,
        variables=new_variables,
        pipeline=expanded_pipeline,
        globals_=strategy.globals_,
        universe=strategy.universe,
        execution=strategy.execution,
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
# PASS 3b: Variable-reference acyclicity
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_variable_acyclicity(strategy: StrategyFile, issues: list[ValidationIssue]) -> None:
    """Pass 3b: the variable-reference graph must be a DAG (PIPELINE_VARIABLE_CYCLE).

    Runs on the EXPANDED strategy — factory bodies are already inlined, so a
    cycle routed through a factory (variable ``a``'s body calls factory
    ``f``; ``f``'s body references ``a``) appears as a direct
    variable→variable edge here: exactly the ``variable_pipelines`` graph
    the inlining walks consume. Emitting at the structural stage (the error
    short-circuits before pass 4) is what makes every downstream
    ``VariableRef``-inlining walk safe to recurse without a cycle guard of
    its own: the pass-6 interpreter walk, pass-7 ``_check_ordering``, and
    the pass-7b ``_find_mask_anchor``/``_mask_walk`` all rely on this gate.
    The runtime twin is the resolver's ``_topo_sort_variables`` ResolveError
    (core-engine-audit F18) — same :func:`find_variable_cycle` oracle, same
    deterministic cycle naming, so validate-time and resolve-time name the
    identical cycle. ``verify_spec`` runs this check too (its standalone
    slot walk interprets stored artifacts that may never have been
    validated); the interpreter keeps a defensive raise for any remaining
    ungated caller.
    """
    cycle = find_variable_cycle(strategy.variables)
    if cycle is None:
        return
    first = next((v for v in strategy.variables if v.name == cycle[0]), None)
    emit(
        issues,
        "PIPELINE_VARIABLE_CYCLE",
        location=_format_location(first.location) if first is not None else None,
        cycle=" → ".join(cycle),
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
    from pipeline_engine.base.registry import get_all_versions, get_version

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

            # Constrained shells (dsl-mtf-clocks spec 02 §8.2): the version
            # this strategy will actually RESOLVE to does not execute. Pass 4
            # is handed the latest-registry view, so the effective version is
            # re-derived from the lock here — a blob pinned to a runnable
            # version stays silent (blob/ABI invariant: pinned instances keep
            # loading and running, which is the entire point of constraining
            # at v2 instead of deleting). Only authoring that lands ON the
            # shell errors.
            effective = sig
            if component_lock and ref.name in component_lock:
                pinned = get_version(ref.name, component_lock[ref.name])
                if pinned is not None:
                    effective = pinned
            redirect = getattr(effective, "not_runnable", None)
            if redirect:
                emit(
                    issues,
                    "COMPONENT_NOT_RUNNABLE",
                    location=_format_location(ref.location),
                    name=ref.name,
                    version=getattr(effective, "version", "?"),
                    redirect=redirect,
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


def _literal_in_domain(value: int | float, domain: dict) -> bool:
    """J-PARAM literal inclusion ``Set{v} ⊑ D`` via the shared domain evaluator.

    The pass-5 constraint branches (A3 rows ``J-PARAM.range`` /
    ``J-PARAM.option``) evaluate literal membership through relation.py's
    inclusion evaluator instead of ad-hoc comparisons (spec 03 §1.1). The
    DEMAND side of J-PARAM (only) admits ±inf sentinel endpoints, encoding the
    half-open min-only / max-only constraint forms. Two guards keep the
    premise verdict-identical to today's comparison semantics:

    - NaN never violates a bound (``nan < min`` / ``nan > max`` are both
      False today) — inclusion is vacuously satisfied;
    - an int beyond IEEE-double range (``float()`` raises OverflowError)
      falls back to Python's EXACT int/float comparisons — provably the same
      predicate, evaluated without the lossy coercion.
    """
    if value != value:  # NaN — comparison-based bounds never fire on it
        return True
    try:
        return domain_leq({"set": [float(value)]}, domain)
    except OverflowError:
        lo, hi = domain["interval"]
        return lo <= value <= hi


def _literal_in_options(value: str, options: list) -> bool:
    """J-PARAM ``Set{v} ⊑ Set(options)`` over literal equality (A3 ``J-PARAM.option``).

    Param-literal option universes are string-valued here (the
    ``value-kind:str`` guard admits only str values to this premise), so the
    inclusion operation is literal-equality set membership — spec 01 §7.2's
    "one lattice, two uses" sanction (the ℝ-only/≤8 caps bind flow domains;
    numeric singleton inclusion is served by :func:`_literal_in_domain`).
    """
    return value in options


#: The generated tombstoned-options table, loaded once on first pass-5 run.
_TOMBSTONED_OPTIONS: dict[str, dict] | None = None


def _staged_key_armed(key: str) -> bool:
    """True iff staged change ``key`` is in an EMITTING state (spec 05 §2.2).

    A DORMANT code must be STRUCTURALLY silent — ``severity_for`` raises rather
    than emit one, so an emitting site guards itself instead of relying on the
    catalog to swallow the call. Mirrors ``interpreter.py``'s ``_stage_armed``
    (kept local: the dsl interpreter imports the validator, not the reverse).
    """
    change = STAGED_CHANGES[key]
    if change.kind == "flow-shape":
        return change.stage != "pre"
    return Stage(change.stage) is not Stage.DORMANT


def _tombstoned_options() -> dict[str, dict]:
    """The generated tombstoned-options table (dsl-mtf-clocks spec 02 §2.3).

    Loaded once, lazily, from ``registry_metadata.json`` — the table is
    GENERATED from the versioned registration data (spec 01 §8.4's
    constrain-at-v2 + tombstone pattern as data), never hand-maintained.
    """
    global _TOMBSTONED_OPTIONS
    if _TOMBSTONED_OPTIONS is None:
        from pipeline_engine.dsl.fixtures.loader import load_tombstoned_options

        _TOMBSTONED_OPTIONS = load_tombstoned_options()
    return _TOMBSTONED_OPTIONS


def _tombstone_record(component: str, param: str, version: int, value: Any) -> dict | None:
    """The tombstone record a locked ``component`` v``version`` pin trips, if any.

    O(1) per param over the generated table. Fires only when ALL of:

    1. the resolved (lock-effective) version is a pre-constraint version the
       table names — new authoring resolves to the latest version, where the
       narrowed schema makes the value a plain ``PARAM_INVALID_OPTION``;
    2. the pinned version's own schema ADMITS the value. A value that was
       already invalid at the pinned version is ``PARAM_INVALID_OPTION``'s, not
       this code's — firing here would double-report it;
    3. the latest version no longer admits it. A re-widening at a later version
       retires the tombstone automatically (the table's ``admitted`` is the
       LATEST surface).
    """
    for record in _tombstoned_options().get(component, {}).get(param, ()):
        if record["version"] != version:
            continue
        prior = record["prior_admitted"]
        if prior is not None and value not in prior:
            continue  # already invalid at the pinned version — not ours
        if value in record["admitted"]:
            continue  # still authorable at latest
        return record
    return None


def _validate_params(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Pass 5: Validate component parameters against registry.

    The five J-PARAM branches (type-match, type-check-skipped, finiteness,
    range, options — A3 rows ``J-PARAM.*``) are re-based on the shared
    evaluator at M2c (spec 03 §1.1): the min/max/options premises call the
    inclusion evaluator, the finiteness branch is the ``J-PARAM.finite``
    admission premise, and the type-ladder interop exemptions (int→float
    numeric interop, str for ``*_slot`` params, the VariableRef static-check
    skip) are the declared exemption semantics of the A3 rows. Verdicts,
    emit kwargs, and message rendering are unchanged.
    """
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

            # Type checking (A3 rows J-PARAM.type-match /
            # J-PARAM.type-check-skipped) — uses param_display_type for
            # user-visible labels and isinstance against the unwrapped
            # non-None members of the declared type. The interop exemptions
            # (int→float numeric interop incl. Optional[float]; str for
            # `*_slot` params; the VariableRef skip above) are the declared
            # J-PARAM exemption semantics (spec 03 §1.1).
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

            # J-PARAM.finite (A3): the finiteness premise on numeric
            # literals — the domain lattice admits finite values only
            # (spec 01 §1.4 / §7.2); non-finite literals fail at compile.
            # Strings render from the A3 row's template data (F5.1).
            if isinstance(pval, float) and not math.isfinite(pval):
                emit(
                    issues,
                    "PARAM_INVALID_VALUE",
                    location=_format_location(ref.location),
                    suggestion=emit_template("param-nonfinite-fix").format(param=pname),
                    detail=emit_template("param-nonfinite-detail").format(
                        param=pname, component=ref.name, value=pval
                    ),
                )

            # Constraint checking — A3 rows J-PARAM.range / J-PARAM.option,
            # evaluated through the shared inclusion evaluator (spec 03 §1.1).
            # The two half-open interval premises ARE the failing-bound
            # channel: the below-min and above-max checks are distinct
            # premises with distinct emissions, exactly today's messages.
            # Value-kind guards (int-float for range, str for options) fire
            # per the declared skip_when semantics.
            if pinfo.constraints and not isinstance(pval, VariableRef):
                c = pinfo.constraints
                range_suggestion = emit_template("param-range-fix").format(
                    param=pname, min=c.get("min", "..."), max=c.get("max", "...")
                )
                if (
                    "min" in c
                    and isinstance(pval, (int, float))
                    and not _literal_in_domain(pval, {"interval": [c["min"], math.inf]})
                ):
                    emit(
                        issues,
                        "PARAM_OUT_OF_RANGE",
                        location=_format_location(ref.location),
                        suggestion=range_suggestion,
                        detail=emit_template("param-range-detail", "below-min").format(
                            param=pname, component=ref.name, value=pval, min=c["min"]
                        ),
                    )
                if (
                    "max" in c
                    and isinstance(pval, (int, float))
                    and not _literal_in_domain(pval, {"interval": [-math.inf, c["max"]]})
                ):
                    emit(
                        issues,
                        "PARAM_OUT_OF_RANGE",
                        location=_format_location(ref.location),
                        suggestion=range_suggestion,
                        detail=emit_template("param-range-detail", "above-max").format(
                            param=pname, component=ref.name, value=pval, max=c["max"]
                        ),
                    )
                if (
                    "options" in c
                    and isinstance(pval, str)
                    and not _literal_in_options(pval, c["options"])
                ):
                    emit(
                        issues,
                        "PARAM_INVALID_OPTION",
                        location=_format_location(ref.location),
                        param=pname,
                        component=ref.name,
                        value=pval,
                        options=c["options"],
                    )

        # TOMBSTONED_OPTION_PINNED (dsl-mtf-clocks spec 02 §2.3, executing spec
        # 01 §8.4 item 2). `registry` is the LOCK-EFFECTIVE view, so `sig` IS
        # the resolved version: when a pin resolves to a pre-constraint version
        # and its effective value was retired at a later one, say so. Runs over
        # the registry params (not just the written ones) because a v1 DEFAULT
        # can itself be a retired value — `_effective_param_value` is exactly
        # "the value __init__ would see". Permanent WARNING by catalog ceiling:
        # the blob/ABI invariant promises the pinned instance keeps loading and
        # running, so this reports, never blocks.
        if _staged_key_armed("tombstoned-option-pinned"):
            for pname, pinfo in reg_params.items():
                if pinfo.tier == ParamTier.INFRA:
                    continue
                pval = _effective_param_value(ref.params, reg_params, pname)
                if not isinstance(pval, str):
                    continue  # option surfaces are string-valued; VariableRef unresolvable
                record = _tombstone_record(ref.name, pname, sig.version, pval)
                if record is None:
                    continue
                emit(
                    issues,
                    "TOMBSTONED_OPTION_PINNED",
                    location=_format_location(ref.location),
                    component=ref.name,
                    version=sig.version,
                    param=pname,
                    value=repr(pval),
                    new_version=record["retired_at"],
                    reason=record["reason"],
                    replacement=record["replacement"],
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
# PASS 7: Phase ordering
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_phase_ordering(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    """Pass 7: Check step categories follow PHASE_ORDER.

    Named ``Pipeline``-variable bodies are walked through their references
    (the C11 treatment, pulled forward from the M5 evidence pass):
    ``_check_ordering`` recurses into a ``VariableRef``'s body carrying the
    CURRENT phase state in and the body's resulting state out, exactly like
    the interpreter's transparent inlining (interpreter.py Walk._pipeline's
    VariableRef arm). This is deliberately UNLIKE a nested ``PipelineSpec``
    (an expanded factory body), which resets to phase 0 — multi-signal
    factories legitimately restart at the data phase. Pre-fix, the walk
    skipped ``VariableRef`` steps entirely, so the inline spelling of a
    strategy fired violations its variable spelling did not
    (AUDIT-v16-staging-readiness §1.5). Recursion is safe: pass 3b
    guarantees the variable graph is acyclic before this pass runs.
    """
    variables = {v.name: v.value for v in strategy.variables if isinstance(v.value, PipelineSpec)}
    _check_ordering(strategy.pipeline, registry, issues, variables=variables)


def _check_ordering(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    current_phase_idx: int = 0,
    variables: dict[str, PipelineSpec] | None = None,
) -> int:
    """Check ordering, returning max phase index."""
    from pipeline_engine.base.step import StepCategory

    if variables is None:
        variables = {}
    max_idx = current_phase_idx

    for step in pipeline.steps:
        if isinstance(step, ComponentRef):
            sig = registry.get(step.name)
            if sig and sig.category != StepCategory.SLOT_OP:
                # Universe-mask machinery is phase-transparent (rolling-
                # universe P2 (c)): its placement law is Contract C's
                # path-sensitive taint rule (XS_BEFORE_UNIVERSE_MASK), not
                # the linear phase index — see is_universe_mask_phase_exempt.
                if is_universe_mask_phase_exempt(
                    step.name, sig.category, getattr(sig, "population_scope", None)
                ):
                    continue
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
                _check_ordering(
                    branch_pipeline,
                    registry,
                    issues,
                    current_phase_idx=max_idx,
                    variables=variables,
                )

        elif isinstance(step, PipelineSpec):
            _check_ordering(step, registry, issues, current_phase_idx=0, variables=variables)

        elif isinstance(step, VariableRef):
            # A named Pipeline-variable reference inlines its body (mirrors
            # interpreter.py Walk._pipeline's VariableRef arm): recurse with
            # the CURRENT phase state and carry the body's resulting state
            # OUT — the boundary is transparent, unlike the nested-
            # PipelineSpec reset above. Unresolvable names are pass-1
            # UNDEFINED_VARIABLE territory — skip, exactly like the
            # interpreter. Acyclicity is pass 3b's guarantee.
            vp = variables.get(step.name)
            if vp is not None:
                max_idx = _check_ordering(
                    vp, registry, issues, current_phase_idx=max_idx, variables=variables
                )

    return max_idx


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 7b: Universe-mask discipline (rolling-universe P2 group (c))
#
# One masked-bit taint walk (the pass-7 recursive shape) computes all three
# staged verdicts from the group-(b) ``population_scope`` registry metadata:
#
# - XS_BEFORE_UNIVERSE_MASK — a population_universe(source: input) op on a
#   path whose masked bit is unset, in a strategy that uses a universe mask.
# - MASK_SLOT_NOT_WIRED — a declared pooling slot (source 'slot', or D2
#   input+slots) wired to a slot whose stored path never passed the mask.
# - NONDENSE_TERMINAL_WEIGHTS — the terminal WeightSeries can still be NaN
#   (bit set at the terminal; no declared densifier after the last mask).
#
# Bit semantics (contracts §1): the applier (ApplyUniverseMask) SETS the bit;
# per_column components PRESERVE it; parallel combiners AND their branches'
# bits; Store/Load carry it through slots; components whose declared
# ``domain_transfer`` can produce constant cells (union-with-const — FillNaN's
# declaration — or a data-independent fixed/const transfer) CLEAR it, because
# a fill resurrects masked (NaN) cells into the pool. Mask emitters
# (universe_filter × population_universe — the D3 discriminator) are the
# selection boundary: they set the bit and are never flagged. Reporter
# categories are skipped (D4). Sub-pipeline boundaries are TRANSPARENT: the
# walk recurses through nested PipelineSpec steps AND named
# ``Pipeline``-variable references (the interpreter's VariableRef inlining,
# interpreter.py Walk._pipeline), carrying the bit in and out — the v16 audit's
# C11 finding was this walk dropping the bit at the named-variable boundary,
# false-positive-ing every flagship file that reaches its mask that way
# (AUDIT-v16-staging-readiness §1.5). All three codes are staged DORMANT —
# emission sites are guarded by ``_staged_key_armed`` so the shipped
# configuration is structurally silent (spec 05 §2.2).
# ═══════════════════════════════════════════════════════════════════════════════

#: code → STAGED_CHANGES key for the three P2 (c) rules.
_MASK_RULE_STAGED_KEYS: dict[str, str] = {
    "XS_BEFORE_UNIVERSE_MASK": "xs-before-universe-mask",
    "MASK_SLOT_NOT_WIRED": "mask-slot-not-wired",
    "NONDENSE_TERMINAL_WEIGHTS": "nondense-terminal-weights",
}

_TERMINAL_WEIGHT_TYPE: str | None = None


def _terminal_weight_type() -> str:
    """The terminal weight carrier name, DERIVED from the generated tables.

    Tokens-only discipline (spec 02 §3.6): neither engine hardcodes a
    type-universe name — the weight carrier is the unique output type of the
    RISK_MANAGER category in TYPE_TRANSITIONS (risk managers operate on the
    weight book, so their output type IS the weight carrier; the TS driver
    derives the same name from the served ``type_transitions``). A
    non-singleton derivation raises — revisit the derivation, never guess.
    """
    global _TERMINAL_WEIGHT_TYPE
    if _TERMINAL_WEIGHT_TYPE is None:
        from pipeline_engine.base.step import StepCategory
        from pipeline_engine.validation_shared import TYPE_TRANSITIONS

        outs = {
            out
            for row in TYPE_TRANSITIONS.values()
            for out in row.get(StepCategory.RISK_MANAGER, ())
        }
        if len(outs) != 1:
            raise ValueError(
                f"TYPE_TRANSITIONS no longer yields a unique risk_manager "
                f"output type ({sorted(outs)}) — the terminal-weight-carrier "
                f"derivation behind NONDENSE_TERMINAL_WEIGHTS must be revisited."
            )
        _TERMINAL_WEIGHT_TYPE = next(iter(outs))
    return _TERMINAL_WEIGHT_TYPE


def _population_scope(sig: Any) -> dict:
    """A signature's normalized population scope ({} = per_column default)."""
    return getattr(sig, "population_scope", None) or {}


def _is_mask_emitter(sig: Any) -> bool:
    """D3 discriminator: a dynamic universe-mask emitter.

    ``universe_filter`` category × ``population_universe`` scope cleanly
    separates the emitters (RollingUniverseMask, RollingVolumeUniverseMask,
    TopNAssetSelector, VolumeUniverseReducer(Any)) from the population_fixed
    universe filters (AssetSelect, GroupAssetFilter) and from every
    non-emitter population op — verified over the full registry at the P2
    (c) mint (projects/rolling-universe/05, D3).
    """
    from pipeline_engine.base.step import StepCategory

    return (
        sig.category is StepCategory.UNIVERSE_FILTER
        and _population_scope(sig).get("kind") == "population_universe"
    )


def _transfer_clears_mask(sig: Any) -> bool:
    """True when the declared domain transfer can resurrect masked cells.

    Decided by the A1 domain machinery, not a component list: a ``union``
    transfer with a ``const`` arm declares "output values = input values ∪
    {constant}" — cells may be replaced by a constant (FillNaN's
    ``union(id, const[fill_value])``; ApplyMask / RegimeGate's gated-to-0.0
    branches). A top-level ``const`` transfer (ConstantForecast) replaces the
    whole frame with a data-independent constant — never NaN, the full
    column set again. Either way the masked-NaN taint cannot survive.

    Deliberately NOT a clear: ``fixed`` transfers. Live effective transfers
    use ``fixed`` as a value-DOMAIN statement (``{'fn':'fixed','interval':
    [-20,20]}`` on ForecastScaler/EmpiricalFDM/ForecastCapper; value sets on
    ThresholdCross) — those components preserve NaN cells at runtime, so
    treating ``fixed`` as a fill would false-positive the entire canonical
    forecast stack. Under-approximating here can only miss a fire, never
    invent one — the right bias for a staged SUSPICIOUS rule.
    """
    dt = getattr(sig, "domain_transfer", None)
    if not isinstance(dt, dict):
        return False
    fn = dt.get("fn")
    if fn == "const":
        return True
    if fn == "union":
        return any(isinstance(arg, dict) and arg.get("fn") == "const" for arg in dt.get("args", ()))
    return False


def _find_mask_anchor(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    variables: dict[str, PipelineSpec],
) -> str | None:
    """The strategy's mask display string, or None when no universe mask is used.

    Preference order: the first applier's effective ``mask_slot`` value
    (rendered quoted, so suggestion templates read
    ``ApplyUniverseMask(mask_slot='universe_mask')``), else the first
    applier/emitter component name. ``None`` gates all three rules off —
    Contract B static-pool strategies pool the full universe by design.
    Named ``Pipeline``-variable bodies are scanned through their references
    (C11): a strategy whose only applier lives inside a variable is still a
    mask-using strategy.
    """
    applier_name: str | None = None
    emitter_name: str | None = None

    def scan(p: PipelineSpec) -> str | None:
        nonlocal applier_name, emitter_name
        for step in p.steps:
            if isinstance(step, ComponentRef):
                if step.name in UNIVERSE_MASK_APPLIERS:
                    sig = registry.get(step.name)
                    if sig is not None:
                        value = _effective_param_value(step.params, sig.parameters, "mask_slot")
                        if isinstance(value, str) and value:
                            return f"'{value}'"
                    applier_name = applier_name or step.name
                else:
                    sig = registry.get(step.name)
                    if sig is not None and _is_mask_emitter(sig):
                        emitter_name = emitter_name or step.name
            elif isinstance(step, ParallelSpec):
                for branch_steps in step.branches.values():
                    found = scan(
                        PipelineSpec(steps=branch_steps, name=None, location=step.location)
                    )
                    if found is not None:
                        return found
            elif isinstance(step, PipelineSpec):
                found = scan(step)
                if found is not None:
                    return found
            elif isinstance(step, VariableRef):
                vp = variables.get(step.name)
                if vp is not None:
                    found = scan(vp)
                    if found is not None:
                        return found
        return None

    slot_anchor = scan(pipeline)
    return slot_anchor or applier_name or emitter_name


def _mask_walk(
    pipeline: PipelineSpec,
    registry: dict[str, Any],
    variables: dict[str, PipelineSpec],
    slot_state: dict[str, tuple[bool, str | None]],
    bit: bool,
    cleared_by: str | None,
    fires: list[tuple[str, str | None, dict[str, Any]]],
    mask_disp: str,
) -> tuple[bool, str | None, dict[str, tuple[bool, str | None]] | None, str | None, Any]:
    """The masked-bit taint walk. Returns (bit, cleared_by, branch_bits, last_name, last_loc).

    ``branch_bits`` is the per-branch end state of the immediately preceding
    Parallel output (consumed by Extract; any component collapses it via
    AND). ``fires`` collects (code, location, template-params) — emission is
    the caller's, gated per code by the staged-change arming. ``variables``
    maps named ``Pipeline``-variable names to their bodies (the interpreter's
    ``variable_pipelines`` shape): a ``VariableRef`` step recurses into the
    body with the current state and carries the resulting bit OUT, exactly
    like a nested ``PipelineSpec`` (C11 — the boundary is transparent).
    """
    from pipeline_engine.base.step import StepCategory

    branch_bits: dict[str, tuple[bool, str | None]] | None = None
    last_name: str | None = None
    last_loc: Any = None

    for step in pipeline.steps:
        if isinstance(step, SlotStoreSpec):
            slot_state[step.slot_name] = (bit, cleared_by)
        elif isinstance(step, SlotStoreValueSpec):
            slot_state[step.slot_name] = (False, None)
        elif isinstance(step, SlotLoadSpec):
            bit, cleared_by = slot_state.get(step.slot_name, (False, None))
            branch_bits = None
            last_name = f"Load('{step.slot_name}')"
            last_loc = step.location
        elif isinstance(step, SlotExtractSpec):
            if branch_bits is not None:
                bit, cleared_by = branch_bits.get(step.key, (bit, cleared_by))
            branch_bits = None
        elif isinstance(step, ParallelSpec):
            ends: dict[str, tuple[bool, str | None]] = {}
            for branch_name, branch_steps in step.branches.items():
                b, c, _bb, _ln, _ll = _mask_walk(
                    PipelineSpec(steps=branch_steps, name=None, location=step.location),
                    registry,
                    variables,
                    slot_state,
                    bit,
                    cleared_by,
                    fires,
                    mask_disp,
                )
                ends[branch_name] = (b, c)
            branch_bits = ends
            bit = all(b for b, _c in ends.values()) if ends else bit
            cleared_by = next((c for b, c in ends.values() if not b and c), None)
        elif isinstance(step, PipelineSpec):
            bit, cleared_by, branch_bits, ln, ll = _mask_walk(
                step, registry, variables, slot_state, bit, cleared_by, fires, mask_disp
            )
            if ln is not None:
                last_name, last_loc = ln, ll
        elif isinstance(step, VariableRef):
            # A named Pipeline-variable reference inlines its body (mirrors
            # interpreter.py Walk._pipeline's VariableRef arm): recurse with
            # the current state and carry the resulting bit out. Unresolvable
            # names are pass-1 UNDEFINED_VARIABLE territory — skip, exactly
            # like the interpreter.
            vp = variables.get(step.name)
            if vp is not None:
                bit, cleared_by, branch_bits, ln, ll = _mask_walk(
                    vp, registry, variables, slot_state, bit, cleared_by, fires, mask_disp
                )
                if ln is not None:
                    last_name, last_loc = ln, ll
        elif isinstance(step, ComponentRef):
            sig = registry.get(step.name)
            if sig is None or sig.category is StepCategory.SLOT_OP:
                continue
            last_name, last_loc = step.name, step.location
            if sig.category is StepCategory.DATA_LOADER:
                # A loader replaces the current value with a fresh, un-masked
                # source frame.
                bit, cleared_by, branch_bits = False, None, None
                continue
            if step.name in UNIVERSE_MASK_APPLIERS:
                bit, cleared_by, branch_bits = True, None, None
                continue
            if _is_mask_emitter(sig):
                # D3: the emitter IS the selection boundary — its output (the
                # mask, or the reduced frame) is mask-carrying by definition.
                bit, cleared_by, branch_bits = True, None, None
                continue
            if sig.category is StepCategory.REPORTER:
                # D4: reporters are diagnostic pass-throughs — never checked.
                branch_bits = None
                continue

            scope = _population_scope(sig)
            if scope.get("kind") == "population_universe":
                if scope.get("source") == "input" and not bit:
                    cleared_text = (
                        f" (the mask is cleared upstream by '{cleared_by}')" if cleared_by else ""
                    )
                    fires.append(
                        (
                            "XS_BEFORE_UNIVERSE_MASK",
                            _format_location(step.location),
                            {
                                "component": step.name,
                                "mask": mask_disp,
                                "cleared": cleared_text,
                            },
                        )
                    )
                for slot_param in scope.get("slots") or ():
                    if slot_param in sig.parameters:
                        wired = _effective_param_value(step.params, sig.parameters, slot_param)
                    elif slot_param in getattr(sig, "slot_reads", {}):
                        # Implicit slot reads pool over the slot named by the
                        # declaration itself (FundingDispersionRegime class).
                        wired = slot_param
                    else:
                        wired = None
                    if not isinstance(wired, str) or not wired:
                        continue  # unresolvable statically (ref/None) — no verdict
                    if not slot_state.get(wired, (False, None))[0]:
                        fires.append(
                            (
                                "MASK_SLOT_NOT_WIRED",
                                _format_location(step.location),
                                {
                                    "component": step.name,
                                    "slot": wired,
                                    "param": slot_param,
                                    "mask": mask_disp,
                                },
                            )
                        )

            if _transfer_clears_mask(sig):
                if bit:
                    cleared_by = step.name
                bit = False
            branch_bits = None

    return bit, cleared_by, branch_bits, last_name, last_loc


def _validate_universe_mask_discipline(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    type_flow: list[TypeFlowEntry],
    production_mode: bool = False,
) -> None:
    """Pass 7b: the three staged universe-mask rules (P2 group (c))."""
    armed = {code: _staged_key_armed(key) for code, key in _MASK_RULE_STAGED_KEYS.items()}
    if not any(armed.values()):
        return  # every rule dormant — structurally silent (spec 05 §2.2)

    # Named Pipeline-variable bodies, keyed like the interpreter's
    # ``variable_pipelines`` (Walk.run) — the walk inlines them at each
    # reference so the masked bit crosses the boundary (C11).
    variables = {v.name: v.value for v in strategy.variables if isinstance(v.value, PipelineSpec)}

    mask_disp = _find_mask_anchor(strategy.pipeline, registry, variables)
    if mask_disp is None:
        return  # no universe mask in the strategy — Contract B territory

    slot_state: dict[str, tuple[bool, str | None]] = {}
    fires: list[tuple[str, str | None, dict[str, Any]]] = []
    bit, _cleared, _bb, last_name, last_loc = _mask_walk(
        strategy.pipeline, registry, variables, slot_state, False, None, fires, mask_disp
    )

    # NONDENSE_TERMINAL_WEIGHTS: terminal type from the interpreter's type
    # flow (the existing judgment machinery — pass 6 already computed it);
    # the weight-carrier name is derived from the generated tables.
    terminal_type = type_flow[-1].output_type if type_flow else None
    if bit and last_name is not None and terminal_type == _terminal_weight_type():
        fires.append(
            (
                "NONDENSE_TERMINAL_WEIGHTS",
                _format_location(last_loc) if last_loc is not None else None,
                {"mask": mask_disp, "step": last_name},
            )
        )

    for code, location, params in fires:
        if not armed[code]:
            continue
        emit(issues, code, location=location, production_mode=production_mode, **params)


# ═══════════════════════════════════════════════════════════════════════════════
# PASS 8: Slot validation
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_slots(
    strategy: StrategyFile,
    registry: dict[str, Any],
    issues: list[ValidationIssue],
    slot_types: dict[str, type],
    trace_sink: Schema0Sink | Schema1Sink | None = None,
    walk: Any | None = None,
) -> None:
    """Pass 8: Store/Load pairs, slot-reference params, slot types — two modes.

    Since the M2c cutover the slot facts come from the ONE interpreter walk
    (spec 03 §2.4/§3.3); this exported callable keeps its name and signature
    and carries the R-12 journal-mode entry point (spec 03 §5):

    - **Flush mode** (``walk=…`` — the ``validate_strategy`` path): pass 6's
      interpreter walk already ran and journaled the slot-lifecycle
      outcomes; emit the journal + the terminal slot states (schema-0
      observation point 2, via the walk's sink) + the SLOT_UNUSED usage
      lint, in today's order.
    - **Standalone mode** (``walk=None`` — the ``verify_spec`` path and any
      direct caller): no journal exists; the callable runs the interpreter
      walk itself in structural-only capacity — Σ-lifecycle judgments only
      (J-STORE/J-STOREVALUE/J-LOAD plus J-SLOTREAD binding resolution), Σ
      seeded from the passed ``slot_types`` dict (its meaning is unchanged:
      a caller-provided Σ seed). An empty dict reproduces the
      NoneType-sentinel behavior: SLOT_NOT_FOUND / SLOT_REF_NOT_FOUND still
      fire at every compile boundary while SLOT_TYPE_MISMATCH stays
      S7-suppressed; no typing rows are evaluated, so no non-structural
      issues enter the caller's list.

    Uses a single registry view — the lock-effective registry passed in.
    No fallback to full_registry. A component referenced in source but
    missing from the lock-effective registry surfaces upstream as
    UNKNOWN_COMPONENT (pass 4) or INVALID_VERSION_LOCK (pass 4) — those
    are loud structured errors the user must address. Pass 8 doesn't
    over-helpfully complete the slot_reads contract for a component the
    lock can't account for.

    The old hand-written tree walk (``_validate_slots_in_pipeline``) was
    deleted at the M2e step (spec 03 §6 Step 3); the interpreter's
    Σ-lifecycle judgments are the sole slot path.
    """
    from pipeline_engine.dsl.interpreter import verify_slots_structural

    if walk is not None:
        # Flush mode — the ~30-LOC `_flush_slot_journal` shape of spec 03
        # §2.4, kept behaviorally identical to `Walk.flush` (the
        # standalone/shadow flush): the shadow-parity suite compares the
        # two paths over the full corpus, so any drift between them reds
        # parity. Journal first (walk order), then the terminal slot
        # states (schema-0 observation point 2), then the usage lint.
        for code, loc, kwargs in walk.journal:
            emit(issues, code, location=loc, **kwargs)
        # The J-SLOTREAD clock half (dsl-mtf-clocks spec 02 §4.3): journaled
        # during the walk because the write-side-vs-read-side repair decision
        # needs EVERY reader of the slot. Same position and same callable as
        # ``Walk.flush``'s — the shadow-parity suite compares the two paths.
        walk.flush_clock_slot_reads(issues)
        if trace_sink is not None:
            for slot_name, entry in walk.sigma.items():
                trace_sink.on_slot_state(slot_name, entry.val.name)
        for slot_name, entry in walk.sigma.items():
            if slot_name not in walk.used:
                emit(
                    issues,
                    "SLOT_UNUSED",
                    location=_format_location(entry.location),
                    slot=slot_name,
                )
        return
    verify_slots_structural(strategy, registry, issues, slot_seed=slot_types, trace_sink=trace_sink)


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

    # F) The pass-9 residual: the INVALID_BAR_OFFSET grammar dual dispatch.
    #    The resampler-family emitters moved to pass 6 at the
    #    clock-transform-rebase flip (spec 02 §3.4).
    _validate_bar_offset_grammar(strategy, expanded, issues)


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

    # max_leverages entries must be string symbol -> finite numeric > 0.
    # Consumers divide by these values (PortfolioMarginCap mm fraction is
    # 1/(2 x maxLev)): a 0 entry is a runtime ZeroDivisionError and a negative
    # entry flips a margin cap into an amplifier — reject at write time.
    # Mirrored in TS pass 9 (rules/declarations.ts checkUniverse).
    if universe.max_leverages is not None:
        if not isinstance(universe.max_leverages, dict):
            emit(
                issues,
                "INVALID_UNIVERSE_LEVERAGE",
                location=loc,
                detail=f"Universe max_leverages must be a dict of symbol -> max "
                f"leverage, got {type(universe.max_leverages).__name__}.",
            )
        else:
            for lev_symbol, lev_value in universe.max_leverages.items():
                if not isinstance(lev_symbol, str):
                    emit(
                        issues,
                        "INVALID_UNIVERSE_LEVERAGE",
                        location=loc,
                        detail=f"Universe max_leverages key {lev_symbol!r} must be "
                        f"a string symbol.",
                    )
                    continue
                if (
                    isinstance(lev_value, bool)
                    or not isinstance(lev_value, (int, float))
                    or not math.isfinite(lev_value)
                    or lev_value <= 0
                ):
                    emit(
                        issues,
                        "INVALID_UNIVERSE_LEVERAGE",
                        location=loc,
                        detail=f"Universe max_leverages['{lev_symbol}'] must be a "
                        f"finite number > 0, got {lev_value!r}.",
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


def _execution_valid_options(param: str, valid: Any) -> tuple[ValidOption, ...]:
    """Envelope #12 (spec 05 §3.2): the declared option set as machine-readable
    candidates for an ``INVALID_EXECUTION`` option violation."""
    return tuple(
        ValidOption(kind="value", value=opt, detail=f"valid '{param}'") for opt in sorted(valid)
    )


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
            valid_options=_execution_valid_options("rebalance", _VALID_REBALANCE),
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
            valid_options=_execution_valid_options("buffer_mode", _VALID_BUFFER_MODE),
        )
    if execution.rebalance_method not in _VALID_REBALANCE_METHOD:
        emit(
            issues,
            "INVALID_EXECUTION",
            location=loc,
            param="rebalance_method",
            value=execution.rebalance_method,
            options=sorted(_VALID_REBALANCE_METHOD),
            valid_options=_execution_valid_options("rebalance_method", _VALID_REBALANCE_METHOD),
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
        if strategy.universe.max_leverages:
            scope["universe.max_leverages"] = strategy.universe.max_leverages

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

    **`target_timeframe` is deliberately NOT checked** (dsl-mtf-clocks spec 02
    §2.2, removed at the `clock-mismatch` promotion, T-M4f-5). Under the armed
    J-PIPE terminal premise, `Globals(target_timeframe)` is consumed by every
    strategy's terminal clock check, so the arm is vacuous — and while it fired
    it was a measured FALSE POSITIVE whose suggestion ("remove the
    declaration") breaks every backtest and live deploy if followed (R5
    §2.2.11). The timing is R-18's: removing it before the flip would have
    opened a coverage gap, because the terminal premise was still dormant.
    `bar_offset` keeps its arm — nothing else consumes it.
    """
    if strategy.globals_ is None:
        return

    # Collect declared globals namespaces
    declared: set[str] = set()
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
# BAR-OFFSET GRAMMAR (the pass-9 residual).
#
# The pass-9 resampler-family emitter block is GONE (dsl-mtf-clocks spec 02
# §3.4, T-M4f-5): UPSAMPLE_NOT_SUPPORTED / BAR_OFFSET_AT_SAME_TF /
# BAR_OFFSET_NOT_MULTIPLE / BAR_OFFSET_TOO_LARGE / RESAMPLER_NOOP now fire from
# the pass-6 judgment rows at EVERY transform site (not just the first
# PriceDataLoader vs Globals path), the R-16 Globals-level κ_exec WF keeper row
# covers the loader-free class, and INVALID_RESAMPLER_CONFIG — the str-dispatch
# fallback over the ValueError text — is tombstoned with the dispatch itself.
#
# What survives here is ONE code with nothing to do with transform sites:
# INVALID_BAR_OFFSET, the bar-offset GRAMMAR error that spec 02 §4.4 / T-7
# unified across both engines as a dual dispatch alongside Globals'
# INVALID_GLOBAL. Its reach is preserved EXACTLY (same gating as the removed
# block, same emission shape), so the flip changes no verdict through it.
# ─────────────────────────────────────────────────────────────────────────────


def _extract_price_loader_timeframe(expanded: StrategyFile) -> str | None:
    """Find the first PriceDataLoader in the pipeline and return its `timeframe` param.

    Returns None if no PriceDataLoader present, the param isn't a literal string,
    or the value isn't a known timeframe key. None means "skip" (callers should
    treat as 'can't enforce'), never "use a default."
    """
    for comp_ref in _walk_component_refs(expanded):
        if comp_ref.name != "PriceDataLoader":
            continue
        tf = comp_ref.params.get("timeframe") if comp_ref.params else None
        if isinstance(tf, str) and tf in TIMEFRAME_MINUTES:
            return tf
        return None
    return None


def _validate_bar_offset_grammar(
    strategy: StrategyFile,
    expanded: StrategyFile,
    issues: list[ValidationIssue],
) -> None:
    """The INVALID_BAR_OFFSET dual dispatch (spec 02 §4.4 / T-7).

    All that remains of the pass-9 resampler block after the
    clock-transform-rebase flip. INVALID_BAR_OFFSET is a GRAMMAR error on
    `Globals.bar_offset`, not a transform-site premise, so it has no pass-6
    successor: the clock rows explicitly defer to it (`clocks.py`'s
    unparseable-offset arm and `_clock_globals_keeper`'s parse guard both say
    "the grammar codes own it"). Both engines emit it alongside the
    INVALID_GLOBAL that the Globals format check raises for the same value.

    The gating below reproduces the removed block's reach EXACTLY — Globals
    present, `target_timeframe` a known token, a literal first PriceDataLoader,
    and no upsample (which short-circuited before the offset was ever parsed) —
    so no strategy gains or loses this code at the flip.
    """
    if strategy.globals_ is None:
        return
    target_tf = strategy.globals_.target_timeframe
    bar_offset = strategy.globals_.bar_offset
    if bar_offset is None or target_tf is None:
        return
    if target_tf not in TIMEFRAME_MINUTES:
        # Already reported as INVALID_GLOBAL by _validate_globals.
        return
    source_tf = _extract_price_loader_timeframe(expanded)
    if source_tf is None:
        # No (parseable) PriceDataLoader — outside the historical reach.
        return
    if TIMEFRAME_MINUTES[source_tf] > TIMEFRAME_MINUTES[target_tf]:
        # The upsample arm fired first and returned before parsing the offset.
        return
    try:
        parse_bar_offset_minutes(bar_offset)
    except ValueError as e:
        # message_override: the shared parser's ValueError text IS the message
        # (the catalog template is the `{detail}` passthrough), byte-identical
        # to what the removed dispatch emitted and to the TS mirror's render.
        emit(
            issues,
            "INVALID_BAR_OFFSET",
            location="globals",
            message_override=str(e),
            suggestion=None,
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
