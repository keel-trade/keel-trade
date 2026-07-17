"""Rule catalog — the single registry of validation issue codes (spec 02, T-1/T-2).

One frozen :class:`Rule` per issue code minted anywhere in the platform:

- the 49 write-time codes emitted by the Python DSL validator
  (``pipeline_engine.dsl.validator`` — static ``emit(...)`` sites + 6 dynamic
  resampler codes assigned by the ValueError→code dispatch),
- the 7 runtime-only structured codes (Layer C ``PipelineValidator`` +
  ``LookaheadValidator``),
- the 12 engine-boundary structured codes minted by ``StructuredError``
  subclasses at compile/serialize/load/verify boundaries (specs 01/03 plus
  the C4 slot value_type pair, intake per §1.4),
- the 3 gate codes minted by keel-api request gates
  (PARSE_ERROR / LOCK_ERROR / SOURCE_FETCH_FAILED).

Severity is POLICY, not a per-site literal: it derives from
:class:`RuleCategory` via :data:`SEVERITY_BY_CATEGORY`, with rare declared
overrides (``severity_override`` / ``severity_context_overrides``). Message
text is a named-placeholder template seeded from today's f-string literals.

This module is deliberately dependency-free (stdlib only) so it can be
imported by the validator, the fixture generators, and tooling without
circular imports.

Import-time self-checks (:func:`validate_rules`) raise :class:`CatalogError`
on any malformed entry — duplicate codes, template placeholders not declared
in ``template_params``, ``ts_mirrored=False`` without a reason, waivers that
don't name a covering test, populated ``reserved`` entries. There are no
silent fallbacks: a broken catalog is an import error, never a degraded one
(house rule — ``.claude/rules/lessons.md``, "Never Add Silent Fallbacks").

Standing intake rule (spec 02 §1.4): any PR that mints a new structured code
MUST add its catalog entry in the same PR. The enumeration-completeness tests
in ``catalog_test.py`` scan the live emission sites and fail on any code that
is minted but not cataloged (or cataloged but no longer minted).

NOTE (stage 1/2, T-3..T-8, 2026-07-10): the emission sites now CONSUME this
catalog. The Python validator renders code + severity + message/suggestion
templates through ``pipeline_engine.dsl.validator.emit``; the TS editor
validator consumes the generated ``rule_catalog.json`` through its
``catalog.ts`` helper. Severity is policy here — a per-site literal severity
no longer exists on either side.

Quick Start:
    >>> from pipeline_engine.dsl.catalog import RULES, severity_for
    >>> RULES["TYPE_MISMATCH"].category
    <RuleCategory.CORRECTNESS: 'correctness'>
    >>> severity_for(RULES["TYPE_MISMATCH"])
    'error'
    >>> severity_for(RULES["DICT_INPUT_EXPECTED"], context="extract")
    'warning'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from string import Formatter
from typing import Iterable


class RuleCategory(str, Enum):
    """Severity-bearing rule classification (Clippy model, spec 02 §5.1)."""

    CORRECTNESS = "correctness"  # would produce wrong results / crash at runtime
    SUSPICIOUS = "suspicious"  # probably a mistake; strategy still runs
    HYGIENE = "hygiene"  # dead config, no-op steps, unused declarations
    INCUBATING = "incubating"  # new rule in bake-in; never blocks


class Applicability(str, Enum):
    """rustc suggestion-applicability model (spec 02 §1.1)."""

    MACHINE_APPLICABLE = "machine_applicable"  # editor/agent may auto-apply
    MAYBE_INCORRECT = "maybe_incorrect"
    HAS_PLACEHOLDERS = "has_placeholders"
    NONE = "none"


class Surface(str, Enum):
    """Where a code is minted as a STRUCTURED issue (not merely enforced)."""

    PY_DSL = "py_dsl"  # Python DSL validator (Layer A)
    TS_EDITOR = "ts_editor"  # browser validator (Layer B)
    RUNTIME = "runtime"  # Layer C/D structured codes
    GATE = "gate"  # minted by an API gate (PARSE_ERROR, LOCK_ERROR, ...)


#: Category → severity policy (spec 02 §5.1). Hygiene is warning per the
#: spec's proposal ("warning→info (founder taste; propose warning)").
SEVERITY_BY_CATEGORY: dict[RuleCategory, str] = {
    RuleCategory.CORRECTNESS: "error",
    RuleCategory.SUSPICIOUS: "warning",
    RuleCategory.HYGIENE: "warning",
    RuleCategory.INCUBATING: "info",
}

_VALID_SEVERITIES = frozenset({"error", "warning", "info"})
_VALID_STATUSES = frozenset({"active", "incubating", "deprecated", "reserved"})

#: A fixture waiver must name the covering test as a pytest node id
#: (``path/to/foo_test.py::TestClass::test_name`` or file::test_name),
#: optionally followed by `` — rationale``. Spec 02 §3.5's
#: ``test_fixture_waivers_point_at_real_tests`` (T-10) resolves the node id.
_WAIVER_NODE_RE = re.compile(r"\S+\.py::\S+")


class CatalogError(ValueError):
    """A malformed catalog entry. Raised at import time — never deferred."""


@dataclass(frozen=True)
class Rule:
    """One validation issue code and its policy metadata (spec 02 §1.1)."""

    code: str  # permanent; NEVER reused (spec 02 §6 never-reuse gate)
    category: RuleCategory  # severity derives from this (spec 02 §5)
    summary: str  # one line, for docs index
    message_template: str  # "{param}"-style named placeholders
    template_params: tuple[str, ...]  # declared params; checked at catalog import
    explain: str  # agent/user-readable long docs (markdown)
    passes: tuple[str, ...]  # e.g. ("5",) or ("9.resampler",); () = no write-time pass
    surfaces: tuple[Surface, ...]
    ts_mirrored: bool  # False ⇒ ts_absent_reason required
    ts_absent_reason: str = ""
    severity_override: str = ""  # rare; justification cited in explain (spec 02 §5.3)
    severity_context_overrides: dict[str, str] = field(default_factory=dict)
    suggestion_template: str = ""  # optional structured-fix text
    applicability: Applicability = Applicability.NONE
    promote_in_production: bool = False  # production_mode promotion as data (§5.4)
    status: str = "active"  # active | incubating | deprecated | reserved
    fixture_waiver: str = ""  # non-empty ⇒ exempt from corpus mandate; must
    #                           name the covering test (spec 02 §3.5)


def severity_for(rule: Rule, context: str | None = None) -> str:
    """Resolve a rule's severity from policy.

    ``context`` selects a declared ``severity_context_overrides`` entry
    (e.g. DICT_INPUT_EXPECTED with ``context="extract"``). An undeclared
    context raises — overrides are declared, never improvised (spec 02 §5.3).
    """
    if context is not None:
        if context not in rule.severity_context_overrides:
            raise CatalogError(
                f"{rule.code}: severity context '{context}' is not declared in "
                f"severity_context_overrides ({sorted(rule.severity_context_overrides)}). "
                f"Declare it in the catalog entry before emitting with it."
            )
        return rule.severity_context_overrides[context]
    if rule.severity_override:
        return rule.severity_override
    return SEVERITY_BY_CATEGORY[rule.category]


def _template_placeholders(template: str, code: str, which: str) -> set[str]:
    """Extract named placeholders from a template; reject positional ones."""
    names: set[str] = set()
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as e:
        raise CatalogError(f"{code}: {which} is not a parseable format string: {e}") from e
    for _literal, field_name, _spec, _conv in parsed:
        if field_name is None:
            continue
        if field_name == "" or field_name.isdigit():
            raise CatalogError(
                f"{code}: {which} uses a positional placeholder "
                f"('{{{field_name}}}'); templates must use named placeholders."
            )
        # Reject attribute/index access — templates are flat named params.
        base = field_name.split(".")[0].split("[")[0]
        if base != field_name:
            raise CatalogError(
                f"{code}: {which} placeholder '{{{field_name}}}' uses attribute/index "
                f"access; templates must use flat named placeholders."
            )
        names.add(field_name)
    return names


_RESERVED_MUST_BE_EMPTY = (
    "summary",
    "message_template",
    "explain",
    "ts_absent_reason",
    "severity_override",
    "suggestion_template",
    "fixture_waiver",
)


def validate_rules(rules: Iterable[Rule]) -> dict[str, Rule]:
    """Self-check a rule collection and return it keyed by code.

    Raises :class:`CatalogError` on the first violation. Called at module
    import on :data:`RULES`; also called directly by unit tests with
    deliberately malformed entries.
    """
    out: dict[str, Rule] = {}
    for rule in rules:
        if rule.code in out:
            raise CatalogError(f"Duplicate rule code: {rule.code}")
        if not rule.code or rule.code != rule.code.upper():
            raise CatalogError(f"Rule code must be non-empty UPPER_SNAKE: {rule.code!r}")
        if rule.status not in _VALID_STATUSES:
            raise CatalogError(f"{rule.code}: unknown status {rule.status!r}")

        if rule.status == "reserved":
            # Reserved = a retired code name that may never be re-minted
            # (protobuf reserved-field discipline). The entry is a tombstone:
            # nothing but the code + status may be populated.
            populated = [
                f
                for f in _RESERVED_MUST_BE_EMPTY
                if getattr(rule, f) != ""  # noqa: PLC1901 — explicit empty-string check
            ]
            if rule.template_params or rule.passes or rule.surfaces:
                populated.extend(
                    f for f in ("template_params", "passes", "surfaces") if getattr(rule, f)
                )
            if rule.severity_context_overrides:
                populated.append("severity_context_overrides")
            if rule.applicability is not Applicability.NONE:
                populated.append("applicability")
            if rule.promote_in_production or rule.ts_mirrored:
                populated.append("promote_in_production/ts_mirrored")
            if populated:
                raise CatalogError(
                    f"{rule.code}: status='reserved' entries are tombstones; "
                    f"populated fields not allowed: {sorted(set(populated))}"
                )
            out[rule.code] = rule
            continue

        # ── Active/incubating/deprecated entries: full checks ────────────
        if not rule.summary:
            raise CatalogError(f"{rule.code}: summary is required")
        if not rule.message_template:
            raise CatalogError(f"{rule.code}: message_template is required")
        if not rule.explain:
            raise CatalogError(f"{rule.code}: explain is required")
        if not rule.surfaces:
            raise CatalogError(f"{rule.code}: surfaces must be non-empty")

        # Template placeholders must exactly match the declared params —
        # an undeclared placeholder OR an unused declared param is drift.
        used = _template_placeholders(rule.message_template, rule.code, "message_template")
        if rule.suggestion_template:
            used |= _template_placeholders(
                rule.suggestion_template, rule.code, "suggestion_template"
            )
        declared = set(rule.template_params)
        if len(rule.template_params) != len(declared):
            raise CatalogError(f"{rule.code}: duplicate names in template_params")
        if used - declared:
            raise CatalogError(
                f"{rule.code}: template placeholders not declared in "
                f"template_params: {sorted(used - declared)}"
            )
        if declared - used:
            raise CatalogError(
                f"{rule.code}: template_params declared but unused in any "
                f"template: {sorted(declared - used)}"
            )

        # ts_mirrored ↔ surfaces ↔ reason coherence.
        if rule.ts_mirrored:
            if rule.ts_absent_reason:
                raise CatalogError(f"{rule.code}: ts_absent_reason set on a ts_mirrored=True rule")
            if Surface.TS_EDITOR not in rule.surfaces:
                raise CatalogError(f"{rule.code}: ts_mirrored=True but TS_EDITOR not in surfaces")
        else:
            if not rule.ts_absent_reason:
                raise CatalogError(f"{rule.code}: ts_mirrored=False requires ts_absent_reason")
            if Surface.TS_EDITOR in rule.surfaces:
                raise CatalogError(f"{rule.code}: TS_EDITOR in surfaces but ts_mirrored=False")

        # Write-time codes must declare the pass(es) they run in; codes with
        # no write-time surface must not claim one.
        if Surface.PY_DSL in rule.surfaces and not rule.passes:
            raise CatalogError(f"{rule.code}: PY_DSL surface requires passes")
        if Surface.PY_DSL not in rule.surfaces and rule.passes:
            raise CatalogError(f"{rule.code}: passes declared without PY_DSL surface")

        # Severity policy fields.
        if rule.severity_override and rule.severity_override not in _VALID_SEVERITIES:
            raise CatalogError(f"{rule.code}: invalid severity_override {rule.severity_override!r}")
        for ctx, sev in rule.severity_context_overrides.items():
            if not ctx or sev not in _VALID_SEVERITIES:
                raise CatalogError(
                    f"{rule.code}: invalid severity_context_overrides entry {ctx!r}: {sev!r}"
                )

        # A rule that ships a fix must declare how applicable it is
        # (ESLint/rustc discipline, spec 02 §1.2).
        if rule.suggestion_template and rule.applicability is Applicability.NONE:
            raise CatalogError(f"{rule.code}: suggestion_template set but applicability is NONE")
        if not rule.suggestion_template and rule.applicability is not Applicability.NONE:
            raise CatalogError(f"{rule.code}: applicability set without a suggestion_template")

        # Waiver discipline: a waiver must name the covering test.
        if rule.fixture_waiver and not _WAIVER_NODE_RE.search(rule.fixture_waiver):
            raise CatalogError(
                f"{rule.code}: fixture_waiver must name the covering test as a "
                f"pytest node id (path/to/x_test.py::test_name), got: "
                f"{rule.fixture_waiver!r}"
            )

        # Incubating lane coherence (spec 02 §5.2).
        if (rule.category is RuleCategory.INCUBATING) != (rule.status == "incubating"):
            raise CatalogError(
                f"{rule.code}: category INCUBATING and status 'incubating' must be set together"
            )

        out[rule.code] = rule
    return out


def rules_to_jsonable(rules: dict[str, Rule]) -> dict[str, dict]:
    """Serialize the catalog to the JSON-native shape of rule_catalog.json.

    Sorted by code; every field always present (no conditional omission — a
    stable shape is worth more than a lean file). ``severity`` is the derived
    default-context severity so TS consumers never re-implement the policy.
    """
    out: dict[str, dict] = {}
    for code in sorted(rules):
        r = rules[code]
        entry: dict = {
            "category": r.category.value,
            "severity": severity_for(r) if r.status != "reserved" else "",
            "severity_override": r.severity_override,
            "severity_context_overrides": dict(sorted(r.severity_context_overrides.items())),
            "summary": r.summary,
            "message_template": r.message_template,
            "template_params": list(r.template_params),
            "explain": r.explain,
            "passes": list(r.passes),
            "surfaces": [s.value for s in r.surfaces],
            "ts_mirrored": r.ts_mirrored,
            "ts_absent_reason": r.ts_absent_reason,
            "suggestion_template": r.suggestion_template,
            "applicability": r.applicability.value,
            "promote_in_production": r.promote_in_production,
            "status": r.status,
            "fixture_waiver": r.fixture_waiver,
        }
        out[code] = entry
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# THE CATALOG
#
# Seeded 2026-07-09 from the live emission-site literals (verified by the
# enumeration tests in catalog_test.py). Multi-shape codes — where today's
# emission sites render more than one sentence under one code — carry either
# a parameterization that reaches every current shape, or a "{detail}"
# passthrough template with the concrete shapes documented in `explain`.
# T-3/T-4 (emission-site conversion) is where templates become the single
# rendering path; refining a "{detail}" template then is a catalog-only diff.
# ═══════════════════════════════════════════════════════════════════════════════

_PY_TS = (Surface.PY_DSL, Surface.TS_EDITOR)
_PY_ONLY = (Surface.PY_DSL,)
_PY_TS_RT = (Surface.PY_DSL, Surface.TS_EDITOR, Surface.RUNTIME)
_RT_ONLY = (Surface.RUNTIME,)
_GATE_ONLY = (Surface.GATE,)

_TS_RUNTIME_ONLY_REASON = (
    "Runtime-only structured code (Layer C/D): minted by the runtime "
    "PipelineValidator/LookaheadValidator over live step instances, which the "
    "browser editor never has."
)
_TS_GATE_ONLY_REASON = (
    "Gate-minted code: produced by a keel-api request gate, not by any "
    "validator pass; there is no editor-side emission to mirror."
)
_TS_ENGINE_BOUNDARY_REASON = (
    "Engine-boundary structured code: minted by a StructuredError subclass "
    "(pipeline_engine.exceptions) at a server-side compile/serialize/load/"
    "verify boundary the browser editor never crosses."
)


_ALL_RULES: tuple[Rule, ...] = (
    # ═══════════════════════════════════════════════════════════════════════
    # Pre-pass (lock handling) + Pass 4 (name resolution)
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="UNKNOWN_COMPONENT",
        category=RuleCategory.CORRECTNESS,
        summary="Component name does not exist in the registry.",
        message_template="Unknown component '{name}'.",
        template_params=("name", "matches"),
        explain=(
            "The referenced name is not a registered component. Emitted at two "
            "sites: pass 4 name resolution (with a line location) and the "
            "pre-pass lock generator (where the message is the raw LockError "
            "text when auto-generating a lock fails on an unknown name). The "
            "suggestion carries fuzzy-match candidates when any score above the "
            "threshold; otherwise it points at component search "
            "(`keel components list` / `strategy_components_search`)."
        ),
        passes=("pre", "4"),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Did you mean: {matches}?",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="LOCK_DRIFT",
        category=RuleCategory.SUSPICIOUS,
        summary="Component lock is behind, or missing from, the live registry.",
        message_template=(
            "Component '{component}' is locked at v{locked_version}; latest is "
            "v{latest_version}. Lock is current-but-behind; upgrade with the "
            "lock-upgrade endpoint if you want the newer behavior."
        ),
        template_params=("component", "locked_version", "latest_version"),
        explain=(
            "The strategy's component lock disagrees with the current registry. "
            "Two shapes today: 'outdated' (the template above) and "
            "'missing'/'unknown' (\"Component '{component}' is locked at "
            'v{locked_version} but {drift_type} from the registry." plus '
            "detail). All drift severities are warning — info would be dropped "
            "by downstream error+warning-only serializers. Non-blocking by "
            "design: drift is visible signal, not a gate."
        ),
        passes=("pre",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Requires a live registry probe (check_lock_drift against the "
            "server-side COMPONENT_REGISTRY latest versions); the browser "
            "validator has no registry-latest view to compare a lock against."
        ),
        fixture_waiver=(
            "libs/pipeline_engine/dsl/validator_test.py::TestT8SlotValidation::"
            "test_lock_drift_missing_version_warns — a write-time fixture would "
            "break whenever component versions move; covered by unit tests that "
            "drive check_lock_drift against the live registry with stale locks "
            "(see also test_lock_drift_all_severities_are_warning)."
        ),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 1: variable / factory reference resolution
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="UNDEFINED_VARIABLE",
        category=RuleCategory.CORRECTNESS,
        summary="Reference to a variable or factory that is never defined.",
        message_template="Undefined reference '{name}'.",
        template_params=("name",),
        explain=(
            "A step or parameter references a DSL variable/factory name with no "
            "definition anywhere in the file. The strategy cannot resolve; "
            "define the name or fix the typo."
        ),
        passes=("1",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Define '{name}' before using it.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="FORWARD_REFERENCE",
        category=RuleCategory.SUSPICIOUS,
        summary="A name is used before the line that defines it.",
        message_template="Forward reference to '{name}' (defined at line {def_line}).",
        template_params=("name", "def_line"),
        explain=(
            "The name resolves, but its definition appears later in the file "
            "than this usage. Non-blocking: factories/variables are hoisted at "
            "resolve time, but forward references read poorly and often signal "
            "an editing mistake."
        ),
        passes=("1",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "The editor's graph model deliberately has no line ordering "
            "(documented at pass4-names.ts), so use-before-definition is "
            "undetectable in the browser. Corpus coverage arrives via a "
            "kind:'dsl' fixture (spec 02 §3.3a, T-12)."
        ),
        suggestion_template="Move the definition of '{name}' before this usage.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 2: name collisions
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="NAME_COLLISION",
        category=RuleCategory.CORRECTNESS,
        summary="A DSL variable/factory name shadows a component or factory.",
        message_template="{kind} '{name}' collides with {conflict}.",
        template_params=("kind", "name", "conflict"),
        explain=(
            "A user-defined name is ambiguous with a registered name. Three "
            "current shapes, all reachable from the template: variable vs "
            "registered component, variable vs factory ('(ambiguous)'), and "
            "factory vs registered component. Rename the user-defined side."
        ),
        passes=("2",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Rename '{name}' to avoid the collision.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 3: factory expansion
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="FACTORY_MISSING_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Factory call omits a required factory parameter.",
        message_template="Factory '{factory}' missing required parameter '{param}'.",
        template_params=("factory", "param"),
        explain=(
            "A factory invocation is missing a parameter the factory body "
            "requires (no default declared). Expansion stops for this call "
            "until the argument is supplied."
        ),
        passes=("3",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Add {param}=<value> to the call.",
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="FACTORY_UNKNOWN_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Factory call passes a parameter the factory does not declare.",
        message_template=(
            "Factory '{factory}' has no parameter '{param}'. Available: {available}."
        ),
        template_params=("factory", "param", "available"),
        explain=(
            "A factory invocation passes an argument name the factory signature "
            "doesn't declare — usually a typo of one of the available names."
        ),
        passes=("3",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Remove '{param}' or use one of: {available}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 4: name resolution (continued)
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="DEPRECATED_COMPONENT",
        category=RuleCategory.SUSPICIOUS,
        summary="Component is registered but marked deprecated.",
        message_template=(
            "Component '{name}' is deprecated and may be removed in a future version."
        ),
        template_params=("name",),
        explain=(
            "The component still resolves and runs, but its registry status is "
            "'deprecated'. Prefer the supported alternative before the version "
            "is phased out (see the deprecation-window policy, decision D2)."
        ),
        passes=("4",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Consider replacing '{name}' with a supported alternative.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="INVALID_VERSION_LOCK",
        category=RuleCategory.CORRECTNESS,
        summary="Component lock pins a version that does not exist.",
        message_template=(
            "Component '{component}' is locked to version {locked_version}, which does not exist."
        ),
        template_params=("component", "locked_version", "latest"),
        explain=(
            "The strategy's component lock references a version absent from the "
            "registry's version set for that component. Surfaced as a "
            "structured issue (parity with TS pass 4) instead of an uncaught "
            "LockError. Suggestion falls back to 'Remove the version lock' when "
            "the registry has no latest version to report."
        ),
        passes=("4",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Available versions: latest is {latest}. Update the lock or remove version pin."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 5: parameter validation
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="MISSING_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Required component parameter not provided.",
        message_template="Component '{component}' missing required parameter '{param}'.",
        template_params=("component", "param", "default_hint"),
        explain=(
            "A registry-declared required parameter (non-infra tier) is absent "
            "from the component call. The suggestion appends an example value "
            "when the registry declares suggestions ({default_hint} renders "
            "empty otherwise)."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Add {param}=<value>{default_hint}.",
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="UNKNOWN_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Component call passes a parameter the registry does not declare.",
        message_template=(
            "Component '{component}' has no parameter '{param}'. "
            "Strategy params: {strategy_params}.{infra_note}"
        ),
        template_params=("component", "param", "strategy_params", "infra_note"),
        explain=(
            "The parameter name doesn't exist on the component (any tier). The "
            "message lists strategy-tier params and, when present, an "
            "' Infra params: [...].' note ({infra_note} renders empty otherwise)."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Remove '{param}' or use one of: {strategy_params}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="PARAM_TYPE_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Parameter value has the wrong type.",
        message_template=("Parameter '{param}' of '{component}' expects {expected}, got {actual}."),
        template_params=("param", "component", "expected", "actual"),
        explain=(
            "The literal value's runtime type is not acceptable for the "
            "registry-declared parameter type (int satisfies float params; "
            "bool never satisfies numeric params)."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Change {param} to a {expected} value.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="PARAM_TYPE_CHECK_SKIPPED",
        category=RuleCategory.SUSPICIOUS,
        summary="Parameter type could not be checked (non-isinstance-checkable).",
        message_template=(
            "Cannot validate type of parameter '{param}' of '{component}': "
            "complex type {type} is not isinstance-checkable."
        ),
        template_params=("param", "component", "type"),
        explain=(
            "Generic alias types (list[int], dict[str, float], ...) are not "
            "isinstance-checkable, so the type check is skipped and recorded "
            "at info severity. severity_override='info' preserves the "
            "pre-catalog literal: this is a notice that a check did NOT run, "
            "not a suspected authoring mistake — warning would overstate it."
        ),
        passes=("5",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "The TS pass-5 type switch silently accepts unknown/complex "
            "declared types (pass5-params.ts) instead of emitting a "
            "check-skipped notice."
        ),
        severity_override="info",
    ),
    Rule(
        code="PARAM_INVALID_VALUE",
        category=RuleCategory.CORRECTNESS,
        summary="Parameter value is semantically invalid (non-finite; bad weights).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Two current shapes under one code: (1) non-finite numbers — "
            "\"Parameter '{param}' of '{component}' has invalid value "
            '{value}. Infinity and NaN are not allowed." (these fail at '
            'compile otherwise); (2) the weights-sum heuristic — "Parameter '
            "'weights' of '{component}' must sum to 1.0, got {sum}.\" for "
            "dict-valued params literally named 'weights'."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="PARAM_OUT_OF_RANGE",
        category=RuleCategory.CORRECTNESS,
        summary="Numeric parameter outside its declared or execution-block range.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Three current shapes under one code: pass-5 registry constraints — "
            "\"Parameter '{param}' of '{component}' value {value} below "
            'minimum {min}." / "... above maximum {max}." — and the '
            'pass-9 Execution-block range checks — "{param}={value} out of '
            'range [{min}, {max}]" (buffer_threshold, min_trade_size, '
            "on_change_tolerance; range literals move into EXECUTION_PARAM_META "
            "in spec 02 T-15). Pass-5 sites attach a 'Change {param} to a "
            "value in range [...]' suggestion; pass-9 sites don't."
        ),
        passes=("5", "9"),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="PARAM_INVALID_OPTION",
        category=RuleCategory.CORRECTNESS,
        summary="String parameter not in the declared options set.",
        message_template=(
            "Parameter '{param}' of '{component}' value '{value}' is not a "
            "valid option. Valid: {options}."
        ),
        template_params=("param", "component", "value", "options"),
        explain=(
            "The registry declares an options list for this parameter and the "
            "provided string is not in it."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Use one of: {options}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="PARAM_GROUP_MISSING",
        category=RuleCategory.CORRECTNESS,
        summary="An exactly-one parameter group has no member provided.",
        message_template=(
            "Component '{component}' requires exactly one of [{group}], but none provided."
        ),
        template_params=("component", "group"),
        explain=(
            "Cross-parameter constraint (schema v1, rule 'exactly_one'): the "
            "component requires exactly one member of the group and the call "
            "provides none."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Provide one of: {group}.",
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/dsl/validator_test.py::TestParamGroupConstraints"
            "::test_exactly_one_group_missing_errors — no LIVE component "
            "declares an exactly_one/at_most_one group (the slot-pair shape "
            "that used them was retired), so a conformance fixture cannot fire "
            "this through the real registry; covered by an injected probe "
            "component on both sides (TS mirror: keel-app validator/__tests__/"
            "validator.unit.test.ts 'param group constraints')."
        ),
    ),
    Rule(
        code="PARAM_GROUP_CONFLICT",
        category=RuleCategory.CORRECTNESS,
        summary="Mutually-exclusive parameters provided together.",
        message_template=(
            "Component '{component}' accepts {arity} of [{group}], but got: [{provided}]."
        ),
        template_params=("component", "arity", "group", "provided"),
        explain=(
            "Cross-parameter constraint (schema v1): more than one member of an "
            "'exactly_one' ({arity}='only one') or 'at_most_one' "
            "({arity}='at most one') group was provided."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Remove one of: {provided}.",
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/dsl/validator_test.py::TestParamGroupConstraints"
            "::test_exactly_one_group_conflict_errors — same probe-component "
            "coverage as PARAM_GROUP_MISSING (see that entry's waiver): no "
            "live component declares these groups, so the corpus cannot fire "
            "the code through the real registry."
        ),
    ),
    Rule(
        code="PARAM_REQUIRES_MISSING",
        category=RuleCategory.CORRECTNESS,
        summary="A conditionally-required parameter is missing.",
        message_template=(
            "Component '{component}' requires parameter(s) [{missing}] when {condition}."
        ),
        template_params=("component", "missing", "condition", "when_params"),
        explain=(
            "Cross-parameter constraint (schema v1, rule 'requires', audit B5/"
            "A9): when every `when` condition matches the effective "
            "(explicit-or-default) value, each `params` member must be "
            "provided. Landed 2026-07-09 with a conformance-style parity "
            "fixture in both languages (now fixtures/conformance/PARAM_REQUIRES_MISSING/), "
            "which this entry references instead of waivering (spec 02 §1.4)."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Set {missing} or change {when_params}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 6: type flow
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="DICT_INPUT_EXPECTED",
        category=RuleCategory.CORRECTNESS,
        summary="Composer/Extract used without a preceding Parallel dict.",
        message_template="{step} expects dict input from Parallel, but got {actual}.",
        template_params=("step", "actual"),
        explain=(
            "Composers and Extract consume the dict a Parallel block produces; "
            "here the previous step outputs something else. Template reaches "
            "both current shapes via {step} = \"Composer '<name>'\" or "
            "\"'Extract'\". Declared context override: the Extract site emits "
            "at warning (validation continues with type Any), the composer "
            "site at error — one code, two declared severities (spec 02 §5.3, "
            "founder question Q1 resolution pending). The runtime Layer C "
            "validator emits the same code at warning."
        ),
        passes=("6",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        severity_context_overrides={"extract": "warning"},
        suggestion_template="Place a Parallel block before {step}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="DICT_NOT_CONSUMED",
        category=RuleCategory.CORRECTNESS,
        summary="Step after Parallel cannot consume the branch dict.",
        message_template=(
            "Step '{step}' follows Parallel but is not a Composer, Extract, or "
            "Load — dict input would crash this step at runtime."
        ),
        template_params=("step",),
        explain=(
            "Parallel outputs dict[branch → result]; only Composers, Extract, "
            "and Load (slot readers exempted both sides) can follow it. "
            "Error at write time; the runtime Layer C validator emits the same "
            "code at warning."
        ),
        passes=("6",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        suggestion_template=("Use a Composer to join branch results, or Extract to select one."),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="TYPE_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Step input type incompatible with the previous step's output.",
        message_template=(
            "Type mismatch at {context}: '{step}' expects {expected} but receives {actual}."
        ),
        template_params=("context", "step", "expected", "actual"),
        explain=(
            "The declared input type of this step is not compatible (per "
            "is_compatible + the TYPE_TRANSITIONS graph) with what the previous "
            "step produces. Blocking: the pipeline would crash or silently "
            "mis-compute. The write-time suggestion is computed dynamically "
            "(insert-a-converter guidance); the runtime Layer C validator "
            "emits the same code with its own phrasing (\"Step '<s>' expects X "
            'but receives Y from previous step").'
        ),
        passes=("6",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
    ),
    Rule(
        code="TRANSITION_OUTPUT_MISMATCH",
        category=RuleCategory.SUSPICIOUS,
        summary="Declared output type disagrees with the category transition table.",
        message_template=(
            "Step '{step}' ({category}) outputs '{output}' but transition from "
            "'{prev_output}' expects one of {expected_outputs}."
        ),
        template_params=("step", "category", "output", "prev_output", "expected_outputs"),
        explain=(
            "The component's declared output_type is not among what "
            "TYPE_TRANSITIONS allows for its category after the previous "
            "output. Usually a component-authoring smell rather than a strategy "
            "bug, hence warning. Also emitted by the runtime Layer C validator."
        ),
        passes=("6",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        suggestion_template="Check that '{step}' produces one of: {expected_outputs}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="EXTRACT_MISSING_KEY",
        category=RuleCategory.CORRECTNESS,
        summary="Extract key not present in the preceding Parallel's branches.",
        message_template=("Extract key '{key}' not found in Parallel branches: {branches}."),
        template_params=("key", "branches"),
        explain=(
            "Extract selects one branch result by name; the requested key is "
            "not a branch of the preceding Parallel. Also emitted by the "
            "runtime Layer C validator."
        ),
        passes=("6",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        suggestion_template="Use one of: {branches}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="COMPOSER_KEY_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Composer dict-param references branch names that don't exist.",
        message_template=(
            "Composer '{composer}' parameter '{param}' has keys not in parallel branches: {extra}."
        ),
        template_params=("composer", "param", "extra", "branches"),
        explain=(
            "A dict-valued composer parameter (e.g. weights) names branches "
            "that the preceding Parallel does not produce. The inverse check "
            "(branches missing from the dict) is the runtime-only "
            "COMPOSER_MISSING_KEYS."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Valid branch names: {branches}.",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="COMPOSER_INPUT_TYPE_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Parallel branch output type violates the composer's input contract.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The composer declares composer_inputs (per-role or homogeneous) "
            "and a referenced branch's final output type doesn't satisfy it. "
            "Two current shapes: per-role — \"Composer '{composer}' role "
            "'{role}' references branch '{branch}' which outputs "
            "'{actual}', but expects {expected}.\" — and homogeneous — "
            "\"Composer '{composer}' expects every Parallel branch to output "
            "{expected}, but branch '{branch}' outputs '{actual}'.\" Both "
            "attach change-the-branch suggestions dynamically."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 7: phase ordering
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="PHASE_ORDER_VIOLATION",
        category=RuleCategory.SUSPICIOUS,
        summary="Step category appears after a later pipeline phase.",
        message_template=(
            "Phase ordering: '{step}' ({category}) appears after the {expected_group} phase."
        ),
        template_params=("step", "category", "expected_group"),
        explain=(
            "Cross-group backward jumps in the DATA→SIGNAL→FORECAST→PORTFOLIO→"
            "EXECUTION phase ordering. Warning at write time (multi-timeframe "
            "patterns legitimately reorder); the runtime Layer C validator "
            "promotes it to error in STRICT mode."
        ),
        passes=("7",),
        surfaces=(Surface.PY_DSL, Surface.RUNTIME),
        ts_mirrored=False,
        ts_absent_reason=(
            "pass7-phases.ts implements the check but the editor entrypoint "
            "(index.ts validate()) deliberately does not run pass 7 — too "
            "noisy with multi-signal factories (false positives from branch "
            "resets). ts_mirrored is the parity-assertion scope and must "
            "reflect what validate() actually emits, not what dead-in-"
            "production code could emit (flipped by spec 02 T-9; the pass "
            "function keeps its direct unit tests)."
        ),
        suggestion_template=(
            "Move '{step}' earlier in the pipeline, before the {expected_group} phase."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 8: slots
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="SLOT_UNUSED",
        category=RuleCategory.SUSPICIOUS,
        summary="Slot is stored but never loaded or referenced.",
        message_template="Slot '{slot}' is stored but never loaded or referenced.",
        template_params=("slot",),
        explain=(
            "A Store() writes a slot that no Load(), slot-reference parameter, "
            "or implicit slot read ever consumes. Dead state — usually a "
            "leftover from an edit. Factory/variable bodies are scanned for "
            "slot loads to avoid false positives."
        ),
        passes=("8",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="SLOT_NOT_FOUND",
        category=RuleCategory.CORRECTNESS,
        summary="Load references a slot with no prior Store.",
        message_template="Load('{slot}'): no prior Store('{slot}') found.",
        template_params=("slot",),
        explain=(
            "A Load() reads a slot name that no earlier Store() wrote. Also "
            "emitted by the runtime Layer C validator (\"Step '<s>' reads slot "
            "'<slot>' but no prior step writes to it\")."
        ),
        passes=("8",),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        suggestion_template='Add Store("{slot}") before this Load.',
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="SLOT_REF_NOT_FOUND",
        category=RuleCategory.CORRECTNESS,
        summary="Slot-reference parameter names a slot that was never stored.",
        message_template=(
            "Component '{component}' parameter '{param}' references slot "
            "'{slot}' which hasn't been stored."
        ),
        template_params=("component", "param", "slot"),
        explain=(
            "A slot_reference parameter (or *_slot-convention read) points at a "
            "slot with no prior Store()."
        ),
        passes=("8",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template='Add Store("{slot}") before this component.',
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="SLOT_TYPE_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Stored slot type incompatible with the reader's expected type.",
        message_template=(
            "Component '{component}' parameter '{param}' expects slot type "
            "{expected} but slot '{slot}' stores {stored}."
        ),
        template_params=("component", "param", "expected", "slot", "stored"),
        explain=(
            "The type stored into the slot is not compatible with the reader's "
            "declared expected_slot_type. Compatibility is the generated "
            "slot_compat_meta.json relation (is_compatible OR same __supertype__ "
            "base) — the model-citizen generated artifact both validators share."
        ),
        passes=("8",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=("Slot '{slot}' stores {stored} but {expected} is expected."),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 9: declarations (Globals / Universe / Execution)
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="INVALID_GLOBAL",
        category=RuleCategory.CORRECTNESS,
        summary="Globals declaration value is malformed.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Two current shapes: \"Globals target_timeframe '{value}' is not "
            'a valid timeframe. Valid: {timeframes}" and "Globals '
            'bar_offset: {error}" (the shared parse_bar_offset_minutes '
            "ValueError text). Note: TS additionally maps bad bar_offset "
            "formats that Python codes as INVALID_BAR_OFFSET onto this code — "
            "spec 02 §4.4 unifies that."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="INVALID_UNIVERSE",
        category=RuleCategory.CORRECTNESS,
        summary="Universe declaration is structurally invalid for its mode.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Five current shapes under one code: mode='manual' without "
            "symbols/resolved; mode='category' without categories; "
            "mode='top_volume' without top_n; unknown mode (\"Unknown Universe "
            "mode '{mode}'. Valid modes: manual, category, top_volume\"); and "
            '"Universe exclusions and inclusions overlap: {overlap}" (the '
            "overlap check is Python-only today — TS pass 9 has no "
            "exclusions∩inclusions check; spec 02 Q2 proposes forcing the "
            "port)."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="EMPTY_UNIVERSE",
        category=RuleCategory.CORRECTNESS,
        summary="Resolved universe is empty.",
        message_template=(
            "Universe 'resolved' list is empty after resolution. At least one "
            "asset is required — check your criteria (mode / categories / "
            "exclusions)."
        ),
        template_params=(),
        explain=(
            "The baked resolved asset list is empty — nothing to trade. Also "
            "enforced by an UNCODED keel-api guard (utils/universe_validation."
            "py raises HTTP 422 without a structured code) and by eval-worker "
            "runtime failure; only the write-time surfaces mint this code."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="UNRESOLVED_UNIVERSE",
        category=RuleCategory.SUSPICIOUS,
        summary="Universe criteria never resolved into a baked asset list.",
        message_template=(
            "Universe has not been resolved. Call universe_resolve (MCP tool or "
            "`keel universe resolve <file>`) or, in the web editor, open the "
            "Universe block and change any field — the editor auto-resolves and "
            "bakes the asset list into the source."
        ),
        template_params=(),
        explain=(
            "Non-manual universes must be resolved to a concrete asset list "
            "before production paths. Warning normally; promoted to error under "
            "production_mode (promote_in_production=True encodes that hook as "
            "data — wiring it into gates is out of this spec's scope). Known "
            "condition divergence: the TS trigger is narrower than Python's "
            "(research/03 §1B; spec 02 Q2 proposes forcing the port)."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        promote_in_production=True,
    ),
    Rule(
        code="STALE_UNIVERSE",
        category=RuleCategory.SUSPICIOUS,
        summary="Baked resolved list no longer matches the declared criteria.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Structural staleness check between `resolved` and the declared "
            "criteria. Two current shapes: manual-mode symbol/exclusion "
            "arithmetic mismatch, and top_volume expected-count range mismatch "
            '("resolved has N items but top_n=K implies X–Y"). Both direct '
            "the user to re-resolve. Warning normally; promoted to error under "
            "production_mode (promote_in_production=True). mode='category' "
            "staleness is left to eval-worker/runtime checks."
        ),
        passes=("9",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Never ported to the TS editor validator; the editor auto-resolves "
            "on any Universe edit, so a stale baked list is primarily a "
            "server-side (CLI/agent-authored source) concern today."
        ),
        promote_in_production=True,
    ),
    Rule(
        code="INVALID_UNIVERSE_GROUP",
        category=RuleCategory.CORRECTNESS,
        summary="Universe group contains assets outside the resolved list.",
        message_template=("Universe group '{group}' contains assets not in resolved: {symbols}"),
        template_params=("group", "symbols"),
        explain=(
            "Groups must be subsets of the resolved asset list; group members "
            "outside it would silently never trade."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="INVALID_EXECUTION",
        category=RuleCategory.CORRECTNESS,
        summary="Execution declaration value not in its valid option set.",
        message_template="Invalid {param} '{value}'. Must be one of: {options}",
        template_params=("param", "value", "options"),
        explain=(
            "rebalance / buffer_mode / rebalance_method outside the option sets "
            "derived from EXECUTION_PARAM_META (spec.py — the single source of "
            "truth; TS derives the same sets from the generated "
            "execution_param_meta.json). {param} renders as the display name "
            "('rebalance mode', 'buffer_mode', 'rebalance_method'). An invalid "
            "rebalance mode short-circuits the remaining Execution checks."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="MISSING_EXECUTION_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Execution param required by the selected mode is missing.",
        message_template="{param} is required when rebalance='{rebalance}'",
        template_params=("param", "rebalance"),
        explain=(
            "Conditional requirement inside the Execution block: today the "
            "single case is buffer_threshold when rebalance='buffered'."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Add buffer_threshold=0.10 (10% relative buffer)",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="IRRELEVANT_EXECUTION_PARAM",
        category=RuleCategory.SUSPICIOUS,
        summary="Execution param explicitly set in a mode where it has no effect.",
        message_template="{param} has no effect when rebalance='{rebalance}'",
        template_params=("param", "rebalance", "mode"),
        explain=(
            "The advisory half of the B6 emit policy (spec 04 T2, landed "
            "2026-07-09): the emitters KEEP every explicitly-set Execution "
            "param, and this warning informs the user a kept param has no "
            "effect in the current rebalance mode. Fires when the param's "
            "mode is inactive AND it was explicitly set — ANY value including "
            "the default (ExecutionSpec.explicit; key presence in the DSL "
            "call / graph dict is the explicitness signal, matching TS). A "
            "back-filled registry default (absent key) never warns; the "
            "non-default fallback covers programmatically-built specs that "
            "don't populate `explicit`. One loop site over "
            "EXECUTION_PARAM_META covers buffer_threshold / "
            "on_change_tolerance / buffer_mode / rebalance_method; only "
            "buffer_threshold attaches the suggestion ({mode} = the first "
            "mode where the param applies). Execution-family conformance "
            "fixtures are authored on these post-T2 semantics (spec 02 §7 "
            "stage-3 amendment)."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Remove {param} or switch to rebalance='{mode}'",
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="MISSING_DECLARATION_REF",
        category=RuleCategory.CORRECTNESS,
        summary="Component requires a Globals/Universe declaration that is absent.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The component's registry entry declares declaration_refs (e.g. "
            "'globals.target_timeframe', 'universe.groups.<param>') that the "
            "strategy does not satisfy. Three current shapes: group ref with no "
            "groups defined; group ref naming a nonexistent group (lists "
            "available); scalar ref with the namespace undeclared (lists "
            "available or 'No globals declared.'). Two of the three sites "
            "attach add-the-declaration suggestions dynamically."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="UNUSED_GLOBAL",
        category=RuleCategory.HYGIENE,
        summary="Globals field declared but consumed by no component.",
        message_template=(
            "Globals '{field}' is declared but not referenced by any component. "
            "Either remove the Globals declaration, or add a component that "
            "consumes it (e.g. TargetTimeframeResampler reads target_timeframe)."
        ),
        template_params=("field",),
        explain=(
            "Dead configuration: the Globals field feeds nothing. The message "
            "names both fix paths (remove, or add a consumer) by design — see "
            "validator_resampler_test.py::TestUnusedGlobalMessage."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="RESAMPLER_NOOP",
        category=RuleCategory.HYGIENE,
        summary="TargetTimeframeResampler resamples to the source timeframe.",
        message_template=(
            "TargetTimeframeResampler is a no-op when target_timeframe "
            "({target_tf}) equals the data loader's timeframe ({source_tf}). "
            "Remove the TargetTimeframeResampler() step (and the redundant "
            "Globals(target_timeframe=...) line if nothing else uses it)."
        ),
        template_params=("target_tf", "source_tf"),
        explain=(
            "Same-timeframe resampling is a wasted step and a redundant Globals "
            "line (the 2026-06-06 jeff5908 case). The runtime short-circuits it "
            "cleanly, so this is hygiene, not correctness. Suppressed when the "
            "resampler config already errored."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Pass 9 — resampler rule table (dynamic ValueError→code dispatch).
    # Messages are the validate_resample_config ValueError texts verbatim
    # (validation_shared.py — shared with the runtime resampler, which raises
    # the same ValueErrors UNCODED at runtime; hence no RUNTIME surface).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="UPSAMPLE_NOT_SUPPORTED",
        category=RuleCategory.CORRECTNESS,
        summary="target_timeframe is smaller than the data loader's timeframe.",
        message_template=(
            "Cannot resample {source_tf} → {target_tf}: upsampling not "
            "supported (source must be ≤ target)."
        ),
        template_params=("source_tf", "target_tf"),
        explain=(
            "Resampling can only aggregate upward (e.g. 15min → 1h); a target "
            "below the source would require inventing data. Enforced by the "
            "same validate_resample_config rule table at runtime (as an uncoded "
            "ValueError) so the agent sees identical text at every layer."
        ),
        passes=("9.resampler",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="BAR_OFFSET_AT_SAME_TF",
        category=RuleCategory.CORRECTNESS,
        summary="bar_offset set while target timeframe equals the source.",
        message_template=(
            "bar_offset ({bar_offset}) has no valid value when target_timeframe "
            "equals the data loader's timeframe ({source_tf}). Remove "
            "bar_offset, or set a larger target_timeframe."
        ),
        template_params=("bar_offset", "source_tf"),
        explain=(
            "At source == target there is no valid offset range; the offset "
            "would be a silent no-op or wrap. Part of the shared resampler rule "
            "table (validation_shared.validate_resample_config)."
        ),
        passes=("9.resampler",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="BAR_OFFSET_NOT_MULTIPLE",
        category=RuleCategory.CORRECTNESS,
        summary="bar_offset is not a multiple of the source bar size.",
        message_template=(
            "bar_offset ({bar_offset}) must be a multiple of the data loader's "
            "timeframe ({source_tf})."
        ),
        template_params=("bar_offset", "source_tf"),
        explain=(
            "Offsets that don't align to source bars would shift labels between "
            "bars. Part of the shared resampler rule table."
        ),
        passes=("9.resampler",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="BAR_OFFSET_TOO_LARGE",
        category=RuleCategory.CORRECTNESS,
        summary="bar_offset is >= the target timeframe (silent no-op).",
        message_template=(
            "bar_offset ({bar_offset}) must be strictly less than "
            "target_timeframe ({target_tf}); whole-period offsets are silent "
            "no-ops (pandas wraps mod-period). For 'act N bars delayed' tests, "
            "use IndexShift_Nbars instead."
        ),
        template_params=("bar_offset", "target_tf"),
        explain=(
            "pandas wraps offsets modulo the period, so a whole-period offset "
            "silently does nothing — the most dangerous shape of this mistake. "
            "Part of the shared resampler rule table."
        ),
        passes=("9.resampler",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="INVALID_BAR_OFFSET",
        category=RuleCategory.CORRECTNESS,
        summary="bar_offset is not a parseable positive duration.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The shared bar-offset parser rejected the value. Two ValueError "
            'shapes: "bar_offset ({value}) is not a valid duration. Use a '
            "value like '15min', '30min', '1h', '12h'.\" and \"bar_offset "
            '({value}) must be positive." Reached through the pass-9 '
            "resampler dispatch, in addition to the INVALID_GLOBAL the "
            "Globals format check emits for the same value. Unified "
            "2026-07-10 (spec 02 §4.4 / T-7): both validators emit this code "
            "from the resampler path, and the grammar is the strict "
            "'^(\\d+)(min|h|d|w)$' — case-sensitive, no whitespace — in both "
            "languages ('12H ' rejects everywhere; fixture "
            "bar_offset_grammar_reject pins it)."
        ),
        passes=("9.resampler",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="INVALID_RESAMPLER_CONFIG",
        category=RuleCategory.CORRECTNESS,
        summary="Fallback bucket for unrecognized resampler-config failures.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The ValueError→code dispatch in _validate_resampler_config maps "
            "every known validate_resample_config failure to a specific code; "
            "this is the fallback for text it doesn't recognize. By "
            "construction unreachable while the mapping is exhaustive — kept so "
            "a future rule-table addition degrades to a coded issue instead of "
            "an uncaught exception."
        ),
        passes=("9.resampler",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Python-only fallback bucket of the dynamic dispatch; TS has no "
            "equivalent path (its resampler checks emit specific codes only)."
        ),
        fixture_waiver=(
            "libs/pipeline_engine/dsl/validator_resampler_test.py::"
            "TestBarOffsetRuleTable — fallback bucket, unreachable while the "
            "ValueError→code mapping is exhaustive over the rule table this "
            "class exercises; a write-time fixture cannot trigger it."
        ),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Runtime-only structured codes (Layer C PipelineValidator + Lookahead).
    # Dormant on production paths today (execution.py skips them in BACKTEST
    # mode); they enter the corpus mandate when the verify-spine work revives
    # them (final report §4.2).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="TYPE_HINTS_UNAVAILABLE",
        category=RuleCategory.SUSPICIOUS,
        summary="Runtime validator cannot resolve a step's type information.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Layer C only: get_type_hints failed or the step class declares no "
            'Generic[In, Out]/typed run(). Two shapes: "Cannot extract type '
            "hints for step '{step}': {error}\" and \"Step '{step}' "
            '({class}) has no resolvable type information...". NO covering '
            "test exists today (verified 2026-07-09) — coverage arrives with "
            "the verify-spine revival; not waivered because the write-time "
            "corpus mandate never binds RUNTIME-only codes."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
    ),
    Rule(
        code="VALIDATION_DEPTH_EXCEEDED",
        category=RuleCategory.SUSPICIOUS,
        summary="Runtime validation recursion cap reached.",
        message_template="Validation depth limit ({limit}) exceeded at {context}",
        template_params=("limit", "context"),
        explain=(
            "Layer C only: nested Pipeline/Parallel recursion exceeded the "
            "validator's depth cap — usually a circular pipeline reference."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        suggestion_template=("Reduce nesting depth or check for circular pipeline references"),
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/validator_test.py::"
            "TestRecursiveValidation::test_depth_limit — runtime-only code, out "
            "of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="TRANSITION_INVALID",
        category=RuleCategory.SUSPICIOUS,
        summary="Step category is not a valid transition from the previous type.",
        message_template=(
            "Step '{step}' ({category}) is not a valid transition from type '{prev_type}'"
        ),
        template_params=("step", "category", "prev_type", "valid_categories"),
        explain=(
            "Layer C only: the category-level cousin of TYPE_MISMATCH — the "
            "step's category has no TYPE_TRANSITIONS entry for the previous "
            "output type, regardless of declared input types."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        suggestion_template=("Valid categories after '{prev_type}': {valid_categories}"),
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/validator_test.py::"
            "TestTypeTransitionIntegration::test_invalid_category_transition_warns "
            "— runtime-only code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="COMPOSER_MISSING_KEYS",
        category=RuleCategory.SUSPICIOUS,
        summary="Composer expects branch keys the preceding Parallel lacks.",
        message_template=(
            "Composer '{composer}' expects keys {expected} but Parallel "
            "provides {provided}. Missing: {missing}"
        ),
        template_params=("composer", "expected", "provided", "missing"),
        explain=(
            "Layer C only: the inverse of write-time COMPOSER_KEY_MISMATCH — "
            "the composer's dict param names branches the Parallel does not "
            "provide."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        suggestion_template="Add branches {missing} to the preceding Parallel",
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/validator_test.py::"
            "TestComposerKeyValidation::test_composer_missing_keys_warning — "
            "runtime-only code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="SLOT_SELF_CYCLE",
        category=RuleCategory.SUSPICIOUS,
        summary="Step both reads and writes the same slot.",
        message_template="Step '{step}' both reads and writes slot '{slot}'",
        template_params=("step", "slot"),
        explain=(
            "Layer C only: a step whose slot reads and writes intersect — a "
            "potential circular dependency."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        suggestion_template=(
            "Consider using separate slots for input and output to avoid "
            "potential circular dependencies"
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/validator_test.py::"
            "TestValidateNoCycles::test_self_cycle_detected — runtime-only "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="USES_FUTURE_DATA",
        category=RuleCategory.CORRECTNESS,
        summary="Step flags itself as using future data (look-ahead bias).",
        message_template=(
            "Step '{step}' has uses_future=True, which indicates potential look-ahead bias"
        ),
        template_params=("step",),
        explain=(
            "LookaheadValidator static analysis: a step attribute declares "
            "uses_future=True. Error severity — look-ahead invalidates every "
            "backtest number downstream. Currently skipped in BACKTEST mode "
            "(execution.py) — i.e. effectively dormant until the verify-spine "
            "work."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        suggestion_template=(
            "Remove uses_future=True from '{step}' or verify this is "
            "intentional for research/debugging only"
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/lookahead_test.py::"
            "TestLookaheadStaticAnalysis::test_uses_future_error — runtime-only "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="RESAMPLE_CHECK",
        category=RuleCategory.SUSPICIOUS,
        summary="Step appears to resample; verify incomplete-bar handling.",
        message_template=(
            "Step '{step}' appears to resample data; verify incomplete bar "
            "handling to prevent look-ahead"
        ),
        template_params=("step",),
        explain=(
            "LookaheadValidator heuristic: a DATA_TRANSFORM step whose name "
            "contains 'resample' gets an advisory nudge. "
            "severity_override='info' preserves the pre-catalog literal: a "
            "name-based heuristic with no evidence of a mistake must stay "
            "advisory."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_RUNTIME_ONLY_REASON,
        severity_override="info",
        suggestion_template=(
            "Ensure resampling drops or marks the current incomplete bar to "
            "avoid future data leakage"
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/lookahead_test.py::"
            "TestLookaheadStaticAnalysis::test_resample_info — runtime-only "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Engine-boundary structured codes — minted by StructuredError subclasses
    # (pipeline_engine.exceptions) at compile/serialize/load/verify boundaries.
    # Landed via specs 01 (S1/S3: version pins) and 03 (S4: blob schema v2 +
    # verify()); cataloged per the §1.4 standing intake rule. Their messages
    # are NOT catalog-rendered — StructuredError packs remediation-first
    # ("{remediation} [{code}] {detail}") — so every template here is the
    # {detail} passthrough and `explain` names the exception class + payload.
    # RUNTIME surface: server-side enforcement, no write-time pass, no gate.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="PARAM_NOT_SERIALIZABLE",
        category=RuleCategory.CORRECTNESS,
        summary="Component parameter value has no JSON representation (RT-6).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SerializationContractError (pipeline/compile.py): serializing a "
            "live pipeline found a parameter value with no JSON encoding and "
            "the parameter is not declared in env_injected_params. Replaces "
            'the silent {"__type__": "opaque"} tag (nothing writes it '
            "anymore; the v1 reader still decodes it for legacy blobs). "
            "Payload: component, param, value_type."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestSerializeUnknownType::test_serialize_unknown_type_raises — "
            "engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="PARAM_NOT_READABLE",
        category=RuleCategory.CORRECTNESS,
        summary="An __init__ parameter has no readable instance attribute (RT-8).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SerializationContractError (pipeline/compile.py): an __init__ "
            "parameter has no readable attribute on the instance (tried `k` "
            "and `_k`), so serialization would silently drop it — the hole "
            "that swallowed signals_list and chunk_config (B2). Payload: "
            "component, param."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/roundtrip_test.py::"
            "TestSweepDetectsPlantedViolations::"
            "test_nondefault_sweep_detects_silent_param_drop — engine-boundary "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="SIGNATURE_NOT_SERIALIZABLE",
        category=RuleCategory.CORRECTNESS,
        summary="Component __init__ signature cannot be serialized faithfully.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SerializationContractError (pipeline/compile.py): the component's "
            "__init__ is uninspectable, or takes **kwargs (RT-4 — the old "
            "vars() scrape leaked computed attributes into the blob). The "
            "registration gate rejects such components, so this is an "
            "internal-error path for ad-hoc unregistered step classes. NO "
            "dedicated covering test exists (verified 2026-07-10) — not "
            "waivered because the write-time corpus mandate never binds "
            "RUNTIME-only codes."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
    ),
    Rule(
        code="SLOT_TYPE_UNRESOLVABLE",
        category=RuleCategory.CORRECTNESS,
        summary="Slot typed outside the platform slot-value-type registry (write).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SlotTypeResolutionError (pipeline/compile.py, write side of the "
            "T10 slot-op value_type fix): a slot is typed with something "
            "outside the enumerated _resolve_slot_value_type registry, so "
            "the compiled blob could never restore it faithfully. Refused at "
            "COMPILE — never record a name the reader cannot resolve back "
            "(no store-now-fail-at-load blobs). Payload: slot, value_type."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestSlotTypeStructuredErrors::"
            "test_write_unresolvable_slot_type_code_and_payload — "
            "engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="SLOT_TYPE_UNKNOWN",
        category=RuleCategory.CORRECTNESS,
        summary="Stored blob's slot value_type name no longer resolves (read).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SlotTypeResolutionError (pipeline/compile.py, read side of the "
            "T10 slot-op value_type fix): a stored blob carries a value_type "
            "name that no longer resolves. The writer proved the name "
            "resolvable at compile time (SLOT_TYPE_UNRESOLVABLE gate), so "
            "this means a platform type was removed/renamed after the blob "
            "was written — engine drift. Hard error, never a silent Any "
            "substitution. Payload: slot, value_type."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestSlotTypeStructuredErrors::"
            "test_read_unknown_slot_type_code_and_payload — engine-boundary "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="BLOB_SCHEMA_UNKNOWN_VERSION",
        category=RuleCategory.CORRECTNESS,
        summary="Stored blob's schema_version is outside the supported set.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "BlobSchemaError (pipeline/compile.py, version-gated reader): "
            "schema_version is missing or outside the enumerated supported "
            'set. Never "try and see". Payload: schema_version.'
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::TestSchemaVersion::"
            "test_reader_rejects_unknown_schema_version — engine-boundary "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="BLOB_OPAQUE_PARAM",
        category=RuleCategory.CORRECTNESS,
        summary="Schema-v2 blob carries an opaque param tag (corrupt blob).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "BlobSchemaError (pipeline/compile.py): a schema-v2 blob carries "
            "an opaque type tag — v2 writers can never produce one, so the "
            "blob is corrupt or hand-edited. v1 blobs keep the legacy "
            "deserialize-to-None behavior. Payload: schema_version + "
            "code-specific fields."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestSerializeUnknownType::test_deserialize_opaque_strict_raises — "
            "engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="BLOB_FINGERPRINT_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="Blob round-trip fingerprint self-check failed at load.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "BlobSchemaError (pipeline/compile.py): the round-trip "
            "fingerprint self-check failed for a schema-v2 blob (v1 "
            "mismatches are WARN + metric — recompiling v1 blobs under v2 "
            "serialize rules legitimately shifts fingerprints). Payload: "
            "schema_version + expected/actual fingerprints."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestFromCompiledErrors::test_from_compiled_fingerprint_tampered — "
            "engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="BLOB_STRUCTURE_INVALID",
        category=RuleCategory.CORRECTNESS,
        summary="Compiled blob failed the structural verify_blob walk.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "BlobVerificationError (pipeline/verify.py, spec 03 §4.2/T5): the "
            "compiled blob failed the structural verify walk. Carries the "
            "FULL violations list (never just the first). Raised at the write "
            "path before store_blob and by the golden-blob CI gate. Payload: "
            "violations, schema_version."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/verify_test.py::"
            "TestVerifyBlobRejects::test_all_violations_reported_not_just_first "
            "— engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    Rule(
        code="SPEC_STRUCTURALLY_INVALID",
        category=RuleCategory.CORRECTNESS,
        summary="Parsed StrategyFile failed the structural verify_spec check.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "SpecVerificationError (dsl/verify.py, spec 03 §4.2/T9): a parsed "
            "StrategyFile failed the structural boundary check — which "
            "delegates to the DSL validator's own pass functions (one truth) "
            "and RAISES where the validator collects. Carries the stage that "
            "produced the spec (parse, graph_to_spec, eval-worker) and the "
            "violations list. Subclasses ValueError so existing boundary "
            "catches keep working. Payload: stage, violations."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/dsl/verify_test.py::TestVerifySpecRejects::"
            "test_unknown_component — engine-boundary code, out of the "
            "write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="COMPONENT_VERSION_PHASED_OUT",
        category=RuleCategory.CORRECTNESS,
        summary="Artifact pins a component version gone from the registry.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "ComponentVersionError (base/registry_types.py, spec 01 §2.1 "
            "pins-assert): a stored artifact pins a version phased out per "
            "the §1.4 deprecation process. Subclasses BOTH CompileError (blob "
            "path) and LockError (source path) so the payload is identical "
            "on both. Payload: component, pinned_version, latest_version, "
            "available_versions, changelog + upgrade remediation."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestVersionPinEnforcement::"
            "test_pinned_absent_version_raises_phased_out — engine-boundary "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="COMPONENT_UNREGISTERED",
        category=RuleCategory.CORRECTNESS,
        summary="Artifact references a component absent from the registry.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "ComponentVersionError (base/registry_types.py + "
            "pipeline/compile.py): the referenced class is not in the "
            "registry at all — deleted/renamed without the §1.4 process, or "
            "a pre-registry artifact. Payload: component, module + "
            "remediation."
        ),
        passes=(),
        surfaces=_RT_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_ENGINE_BOUNDARY_REASON,
        fixture_waiver=(
            "libs/pipeline_engine/pipeline/compile_test.py::"
            "TestFromCompiledErrors::test_from_compiled_unknown_component_error "
            "— engine-boundary code, out of the write-time corpus's reach "
            "(spec 02 §3.3)."
        ),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Gate codes — minted by keel-api request gates (spec 02 §1.4 intake rule
    # covers these from the moment they enter RULES).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="PARSE_ERROR",
        category=RuleCategory.CORRECTNESS,
        summary="Strategy source failed to parse at an API gate.",
        message_template="{error}",
        template_params=("error",),
        explain=(
            "Minted by keel-api when DSLParseError is raised while validating "
            "saved source (backtest submit gate, routers/backtests.py 422 "
            "payload; /validate endpoint, routers/strategies.py). The message "
            "is the parser's error text verbatim."
        ),
        passes=(),
        surfaces=_GATE_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_GATE_ONLY_REASON,
        fixture_waiver=(
            "services/keel-api/tests/test_strategies.py::TestValidateEndpoint::"
            "test_syntax_error_returns_invalid_with_parse_error — gate code, "
            "out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
    Rule(
        code="LOCK_ERROR",
        category=RuleCategory.CORRECTNESS,
        summary="Strategy component lock references an unknown version at a gate.",
        message_template="{error}",
        template_params=("error",),
        explain=(
            "Minted by keel-api's backtest submit gate when building the "
            "lock-effective registry raises KeyError/LockError (422 payload, "
            "routers/backtests.py). The message is the exception text verbatim. "
            "NO covering test exists today (verified 2026-07-09: no keel-api "
            "test asserts this code) — not waivered because the write-time "
            "corpus mandate never binds GATE-only codes; flagged for the gate "
            "test backlog."
        ),
        passes=(),
        surfaces=_GATE_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_GATE_ONLY_REASON,
    ),
    Rule(
        code="SOURCE_FETCH_FAILED",
        category=RuleCategory.CORRECTNESS,
        summary="Submit gate could not fetch strategy source; submission blocked.",
        message_template="{error}",
        template_params=("error",),
        explain=(
            "Minted by keel-api's fail-closed backtest submit gate (audit B9, "
            "tracker A6): after bounded S3 retries the source still couldn't "
            "be fetched, so the backtest is NOT queued and a 503 carries this "
            "code. The message is the last exception's text. Fail-closed by "
            "design — a transient storage blip must never disable the "
            "submit-time validation gate."
        ),
        passes=(),
        surfaces=_GATE_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_GATE_ONLY_REASON,
        fixture_waiver=(
            "services/keel-api/tests/test_backtests.py::TestSubmitS3FailClosed::"
            "test_persistent_s3_failure_returns_503_and_does_not_submit — gate "
            "code, out of the write-time corpus's reach (spec 02 §3.3)."
        ),
    ),
)


#: The catalog. Keyed by code; validated at import (raises CatalogError).
RULES: dict[str, Rule] = validate_rules(_ALL_RULES)


__all__ = [
    "Applicability",
    "CatalogError",
    "RULES",
    "Rule",
    "RuleCategory",
    "SEVERITY_BY_CATEGORY",
    "Surface",
    "rules_to_jsonable",
    "severity_for",
    "validate_rules",
]
