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
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from string import Formatter
from typing import Iterable


class RuleCategory(str, Enum):
    """Severity-bearing rule classification (Clippy model, spec 02 §5.1).

    A rule's category is its TRUTH — what kind of problem it names — and
    never changes to manage severity. Bake-in severity is the staging
    registry's job (:data:`STAGED_CHANGES`, dsl-type-system spec 05 §2): an
    orthogonal cap, transparently reported, that never touches identity.
    The former ``INCUBATING`` category/status lane was RETIRED by spec 05
    §1.5 (zero rules ever shipped with it): demoting a rule's category to
    manage severity is exactly the identity-mask the niperoy revert exposed.
    """

    CORRECTNESS = "correctness"  # would produce wrong results / crash at runtime
    SUSPICIOUS = "suspicious"  # probably a mistake; strategy still runs
    HYGIENE = "hygiene"  # dead config, no-op steps, unused declarations


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
}

_VALID_SEVERITIES = frozenset({"error", "warning", "info"})
#: ``status="incubating"`` retired with the INCUBATING category lane
#: (dsl-type-system spec 05 §1.5) — bake-in now rides STAGED_CHANGES.
_VALID_STATUSES = frozenset({"active", "deprecated", "reserved"})

#: Severity ordering used by every staging cap/ceiling (info < warning < error).
_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}

#: ``severity_ceiling`` vocabulary — a permanent cap is only meaningful below
#: error, so ``"error"`` is inadmissible (a no-op ceiling is a CatalogError,
#: spec 05 §2.1).
_VALID_SEVERITY_CEILINGS = frozenset({"", "warning", "info"})

#: A fixture waiver must name the covering test as a pytest node id
#: (``path/to/foo_test.py::TestClass::test_name`` or file::test_name),
#: optionally followed by `` — rationale``. Spec 02 §3.5's
#: ``test_fixture_waivers_point_at_real_tests`` (T-10) resolves the node id.
_WAIVER_NODE_RE = re.compile(r"\S+\.py::\S+")


class CatalogError(ValueError):
    """A malformed catalog entry. Raised at import time — never deferred."""


# ═══════════════════════════════════════════════════════════════════════════════
# STAGING (dsl-type-system spec 05 §2) — the STAGED_CHANGES registry
#
# ``STAGED_CHANGES`` ids are the SINGLE staging namespace for the whole wave
# (R-5): every staged behavior in specs 01–04 resolves its activation from
# this registry, and no other staging vocabulary exists. Three first-class
# entry kinds:
#
#   (a) "severity-ramp"  — a new code's staged severity (§1.2's three codes).
#   (b) "firing-surface" — an existing code gaining a firing surface, where a
#       per-rule severity field cannot express the flip (split severity via
#       row-level ``staged_by`` keys on judgment-table rows).
#   (c) "flow-shape"     — a synthesis/reach configuration flip whose states
#       are named configurations, not severities (spec 03 §3.4's flow_config
#       keys ARE these ids; the A3 writer derives each flow_config value from
#       the entry's current state at regen time).
#
# A stage flip is: edit one ``stage=`` token, append one log line, run the
# regen cascade. Interpreter code does not change (01 §2.7).
# ═══════════════════════════════════════════════════════════════════════════════


class Stage(str, Enum):
    """Staged-change emission stage (spec 05 §2.1) — the one-line lever.

    Naming note (spec 05 F15c): ``Stage.INCUBATING`` is an EMITTING severity
    stage (info tier — a transparent cap) and is unrelated to the retired
    ``RuleCategory.INCUBATING`` / ``status="incubating"`` lane (§1.5), which
    named a rule IDENTITY that never blocks. The two values never coexist in
    a shipped artifact: §1.5 removed the old vocabulary in the same change
    that introduced ``staged_changes`` (stage values live only inside
    rule_catalog.json's ``staged_changes`` section, never in per-rule
    status/category).
    """

    DORMANT = "dormant"  # rows generated but inactive; emits NOTHING
    INCUBATING = "incubating"  # emits at info (bake-in; measurement running)
    WARNING = "warning"  # emits at warning (the evidence window)
    PROMOTED = "promoted"  # emits at the rule's category-derived severity


#: Stage ordering for the no-demote invariants (birth → terminal direction).
_STAGE_ORDER = {
    Stage.DORMANT: 0,
    Stage.INCUBATING: 1,
    Stage.WARNING: 2,
    Stage.PROMOTED: 3,
}

#: The §2.2 stage cap. DORMANT is deliberately ABSENT: a dormant code can
#: never be emitted, so there is no severity to cap — ``severity_for`` raises.
_STAGE_CAP = {
    Stage.INCUBATING: "info",
    Stage.WARNING: "warning",
    Stage.PROMOTED: "error",
}

_VALID_STAGED_KINDS = ("severity-ramp", "firing-surface", "flow-shape")

#: The milestone the shipped catalog is at, consulted by the §2.3.1d schedule
#: invariant (R-10): an entry still in its BIRTH state at/after its
#: ``target_milestone`` fails import unless its log carries a founder-logged
#: ``extension`` entry — dormancy is never open-ended. Spec 06 owns the
#: schedule; the milestone driver bumps this constant as milestones land.
#: Bumped M2→M3 at Gate-2 (T-MR-3): every staged change has now LEFT its birth
#: state — the last was empty-parallel-rule (DORMANT→WARNING this batch), which
#: previously blocked the bump (R-10: a DORMANT-birth entry at target_milestone
#: "M3" needs a founder-logged extension). validate_staged_changes accepts M3.
#: Bumped M3→M4 at the dsl-mtf-clocks Gate-2 flip (K18, T-M4f-5): all five M4
#: clock entries left DORMANT in that one commit (four to PROMOTED, one to its
#: permanent WARNING terminal), so no target_milestone="M4" entry sits in its
#: birth state at this milestone (spec 02 §3.3 invariant 5).
CURRENT_MILESTONE = "M4"

_MILESTONE_RE = re.compile(r"^M(\d+)[a-z]?$")

#: A backward stage move is legal ONLY as a revert citing false-positive
#: evidence (§2.3.1b): the log note must carry "revert" plus a non-empty
#: citation. Same shape for the dormancy-extension escape (§2.3.1d).
_REVERT_NOTE_RE = re.compile(r"\brevert\b[\s:—-]+\S", re.IGNORECASE)
_EXTENSION_NOTE_RE = re.compile(r"\bextension\b[\s:—-]+\S", re.IGNORECASE)


def _milestone_num(milestone: str, context: str) -> int:
    m = _MILESTONE_RE.match(milestone)
    if m is None:
        raise CatalogError(f"{context}: target_milestone {milestone!r} is not 'M<n>'")
    return int(m.group(1))


@dataclass(frozen=True)
class StagedChange:
    """One staged behavior change (spec 05 §2.1) — validated at import."""

    key: str  # e.g. "value-domain-rule"
    kind: str  # "severity-ramp" (a) | "firing-surface" (b) | "flow-shape" (c)
    codes: tuple[str, ...]  # catalog codes whose emissions it governs
    #                         (flow-shape: the codes whose REACH the flip
    #                         changes, for allowlist derivation)
    decision: str  # the ratified decision/amendment it executes
    stage: Stage | str  # CURRENT state — the one-line lever. A Stage for
    #                     kinds (a)/(b); a member of ``states`` for kind (c)
    terminal_stage: Stage | str  # PROMOTED, or WARNING for permanent
    #                              advisories; "post" for kind (c)
    target_milestone: str  # R-10: the milestone by which the entry must have
    #                        LEFT its birth state; enforced at import (§2.3.1d)
    evidence: tuple[str, ...]  # NAMED promotion criteria (§2.5)
    log: tuple[tuple[str, str, str], ...]  # append-only (date, stage, note)
    states: tuple[str, ...] = ()  # kind (c) only: the closed configuration
    #                               vocabulary, e.g. ("pre", "post")


@dataclass(frozen=True)
class EngineDivergence:
    """One PASS/ENGINE-level Python↔TS divergence, as catalog data (spec 04 §6).

    Rule-LEVEL divergence is already data (every ``ts_mirrored=False`` Rule
    carries a ``ts_absent_reason``). This is its engine-level sibling: a whole
    pass the TS editor deliberately does not run, recorded so every gap in the
    TS net is enumerable from ``rule_catalog.json`` + ``parity_contract.json``
    alone (R1-I11 / R4-I7). Coherence is enforced at serialization time by
    :func:`parity_contract_to_jsonable`: every code in ``codes`` must resolve
    in :data:`RULES` with ``ts_mirrored=False`` — a divergence over a mirrored
    code is a contradiction and fails regen (spec 04 §6). Closing a gap =
    flipping the rule(s) to ``ts_mirrored=True`` with fixtures AND removing the
    entry, in one change.
    """

    key: str  # stable id, e.g. "ts-pass7-skip"
    surface: str  # the checker surface it names, e.g. "phase-ordering"
    reason: str  # why the TS editor omits it (the ratified rationale)
    codes: tuple[str, ...]  # governed codes — each MUST be ts_mirrored=False
    since: str  # ISO date the divergence was declared


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
    status: str = "active"  # active | deprecated | reserved
    fixture_waiver: str = ""  # non-empty ⇒ exempt from corpus mandate; must
    #                           name the covering test (spec 02 §3.5)
    # ── Staging extension (dsl-type-system spec 05 §2.1) ──────────────────
    staged_by: str = ""  # set on codes whose ENTIRE existence rides one
    #                      staged change; non-empty ⇒ must resolve in
    #                      STAGED_CHANGES (§2.3 invariant 5)
    severity_ceiling: str = ""  # permanent cap applied LAST in §2.2's order
    #                             (min only, never a raise); "" | "warning" |
    #                             "info" — "error" is inadmissible
    recoverable: bool = True  # envelope #15's flag (spec 05 §3.1): False only
    #                           on the seven platform-intervention codes
    #                           (SOURCE_FETCH_FAILED, TYPE_HINTS_UNAVAILABLE,
    #                           BLOB_SCHEMA_UNKNOWN_VERSION, BLOB_OPAQUE_PARAM,
    #                           BLOB_FINGERPRINT_MISMATCH, BLOB_STRUCTURE_INVALID,
    #                           COMPONENT_UNREGISTERED); pinned by
    #                           catalog_test.py's platform-intervention test


def severity_for(
    rule: Rule,
    context: str | None = None,
    *,
    production_mode: bool = False,
    row_staged_by: str | None = None,
) -> str:
    """Resolve a rule's severity from policy (spec 05 §2.2 — one function).

    Resolution order, exactly: base = context override | declared override |
    category policy; then the ``promote_in_production`` adjustment (folded in
    here from the emit() side); then the STAGING CAP (min only, never a
    raise); then the permanent ``severity_ceiling`` (min only). Severity
    ordering: info < warning < error.

    ``context`` selects a declared ``severity_context_overrides`` entry
    (e.g. DICT_INPUT_EXPECTED with ``context="extract"``). An undeclared
    context raises — overrides are declared, never improvised (spec 02 §5.3).

    Staging: ``row_staged_by`` is ROW-KEY FIRST (spec 05 F7c) — when the
    emitting judgment row carries a ``staged_by`` key, IT selects the stage;
    ``rule.staged_by`` is the row-free default; both absent ⇒ PROMOTED (no
    cap). A DORMANT stage raises :class:`CatalogError` — a dormant code can
    never be emitted; silence is structural, not a dropped issue. Kind-(c)
    flow-shape entries carry no severity at all and are ignored here.
    """
    key = row_staged_by or rule.staged_by
    stage = Stage.PROMOTED
    if key:
        change = STAGED_CHANGES.get(key)
        if change is None:
            raise CatalogError(
                f"{rule.code}: staged_by key {key!r} does not resolve in "
                f"STAGED_CHANGES — declare the staged change before emitting."
            )
        if change.kind != "flow-shape":  # kind (c) carries no severity (§2.2)
            stage = Stage(change.stage)
            if stage is Stage.DORMANT:
                raise CatalogError(
                    f"{rule.code}: staged change '{key}' is DORMANT — a dormant "
                    f"code can never be emitted (structural silence, spec 05 §2.2)."
                )

    if context is not None:
        if context not in rule.severity_context_overrides:
            raise CatalogError(
                f"{rule.code}: severity context '{context}' is not declared in "
                f"severity_context_overrides ({sorted(rule.severity_context_overrides)}). "
                f"Declare it in the catalog entry before emitting with it."
            )
        severity = rule.severity_context_overrides[context]
    elif rule.severity_override:
        severity = rule.severity_override
    else:
        severity = SEVERITY_BY_CATEGORY[rule.category]

    if production_mode and rule.promote_in_production:
        severity = "error"

    cap = _STAGE_CAP[stage]
    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[cap]:
        severity = cap
    if rule.severity_ceiling and _SEVERITY_RANK[severity] > _SEVERITY_RANK[rule.severity_ceiling]:
        severity = rule.severity_ceiling
    return severity


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
    "staged_by",
    "severity_ceiling",
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

        # ── Active/deprecated entries: full checks ───────────────────────
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

        # ── Staging fields (dsl-type-system spec 05 §2.1/§2.3) ───────────
        # severity_ceiling vocabulary: "error" is a no-op ceiling and
        # therefore inadmissible, like any unknown value.
        if rule.severity_ceiling not in _VALID_SEVERITY_CEILINGS:
            raise CatalogError(
                f"{rule.code}: invalid severity_ceiling {rule.severity_ceiling!r} "
                f"(allowed: 'warning' | 'info'; 'error' is a no-op ceiling and "
                f"inadmissible — spec 05 §2.1)"
            )
        # Invariant 1c: a staged rule cannot be retired out from under its own
        # protocol (retirement requires first removing the staging under the
        # normal registry-diff/BREAKING_CHANGES review).
        if rule.staged_by and rule.status in ("deprecated", "reserved"):
            raise CatalogError(
                f"{rule.code}: status={rule.status!r} on a rule with non-empty "
                f"staged_by — a staged rule cannot be retired out from under its "
                f"own protocol (spec 05 §2.3 invariant 1c)"
            )

        out[rule.code] = rule
    return out


def validate_staged_changes(
    changes: Iterable[StagedChange],
    rules: dict[str, Rule],
    *,
    baseline_codes: set[str] | None = None,
    current_milestone: str | None = None,
) -> dict[str, StagedChange]:
    """Self-check the staged-change registry (spec 05 §2.3 invariants 1/4/5/6).

    Raises :class:`CatalogError` on the first violation. Called at module
    import on :data:`STAGED_CHANGES` against :data:`RULES`; also called by
    unit tests with deliberately malformed entries.

    ``baseline_codes`` is the §2.3.1d dormant-birth baseline seam: the set of
    codes present in the PREVIOUSLY SHIPPED ``rule_catalog.json`` (merge-base
    — the registry-diff gate's machinery). Import time has no git, so the
    import-time call passes ``None`` and the merge-base test in
    ``catalog_test.py`` wires the real baseline; the structural half (a
    severity-ramp entry's codes must declare ``staged_by`` back at the entry)
    is checked unconditionally, so a fresh StagedChange wrapping a live
    code's emissions (the "quiet-type-mismatch" mask) is unrepresentable.
    """
    milestone = CURRENT_MILESTONE if current_milestone is None else current_milestone
    out: dict[str, StagedChange] = {}
    for c in changes:
        ctx = f"STAGED_CHANGES[{c.key!r}]"
        if not c.key or c.key != c.key.lower():
            raise CatalogError(f"{ctx}: key must be non-empty lower-kebab, got {c.key!r}")
        if c.key in out:
            raise CatalogError(f"Duplicate staged-change key: {c.key}")
        if c.kind not in _VALID_STAGED_KINDS:
            raise CatalogError(f"{ctx}: unknown kind {c.kind!r} (allowed: {_VALID_STAGED_KINDS})")
        if not c.decision:
            raise CatalogError(f"{ctx}: decision is required (the ratified decision it executes)")
        _milestone_num(c.target_milestone, ctx)

        # ── Invariant 5 (coverage coherence): codes resolve; log coherent ──
        if not c.codes:
            raise CatalogError(f"{ctx}: codes must be non-empty")
        for code in c.codes:
            if code not in rules:
                raise CatalogError(f"{ctx}: governed code {code!r} does not resolve in RULES")
        if not c.log:
            raise CatalogError(f"{ctx}: log must be non-empty (append-only (date, stage, note))")

        # Stage vocabulary per kind: Stage members for (a)/(b); a declared
        # ``states`` member for (c) (invariant 5's kind-(c) clause).
        if c.kind == "flow-shape":
            if not c.states:
                raise CatalogError(f"{ctx}: flow-shape entries must declare a states vocabulary")
            valid_stages: tuple[str, ...] = tuple(c.states)
            order = {s: i for i, s in enumerate(c.states)}
        else:
            if c.states:
                raise CatalogError(f"{ctx}: states is flow-shape-only, got {c.states!r}")
            valid_stages = tuple(s.value for s in Stage)
            order = {s.value: _STAGE_ORDER[s] for s in Stage}

        def _stage_value(stage: Stage | str, which: str) -> str:
            value = stage.value if isinstance(stage, Stage) else stage
            if value not in valid_stages:
                raise CatalogError(
                    f"{ctx}: {which} {value!r} outside the entry's stage vocabulary {valid_stages}"
                )
            return value

        stage = _stage_value(c.stage, "stage")
        terminal = _stage_value(c.terminal_stage, "terminal_stage")
        log_stages = []
        for i, item in enumerate(c.log):
            if len(item) != 3 or not all(isinstance(p, str) for p in item):
                raise CatalogError(f"{ctx}: log[{i}] must be a (date, stage, note) str triple")
            log_stages.append(_stage_value(item[1], f"log[{i}] stage"))
        if stage != log_stages[-1]:
            raise CatalogError(
                f"{ctx}: stage {stage!r} != last log entry's stage {log_stages[-1]!r} "
                f"(invariant 5: the log is the audit trail of the lever)"
            )

        # ── Invariant 1a: no silent member above birth ────────────────────
        # DORMANT entries may only form the log's PREFIX (birth + extension
        # entries while still dormant). Once a change has left DORMANT — i.e.
        # has emitted in any shipped configuration — it can never return.
        left_dormant = False
        for i, s in enumerate(log_stages):
            if s != Stage.DORMANT.value:
                left_dormant = True
            elif left_dormant:
                raise CatalogError(
                    f"{ctx}: log[{i}] returns to DORMANT — once a rule has emitted "
                    f"in any shipped configuration it can never be silenced by "
                    f"stage (invariant 1a, GOAL no-demote-to-pass)"
                )

        # ── Invariant 1b: backward moves are reverts, with citation ───────
        for i in range(1, len(log_stages)):
            if order[log_stages[i]] < order[log_stages[i - 1]]:
                note = c.log[i][2]
                if not _REVERT_NOTE_RE.search(note):
                    raise CatalogError(
                        f"{ctx}: log[{i}] moves backward "
                        f"({log_stages[i - 1]}→{log_stages[i]}) without a revert "
                        f"note citing false-positive evidence (invariant 1b: "
                        f"note must carry 'revert' + a non-empty citation)"
                    )

        # ── Invariant 1d: DORMANT birth is baseline-checked AND schedule-bound
        # Structural half: a severity-ramp entry governs codes whose ENTIRE
        # existence rides it — each must declare staged_by back at this entry.
        if c.kind == "severity-ramp":
            for code in c.codes:
                if rules[code].staged_by != c.key:
                    raise CatalogError(
                        f"{ctx}: severity-ramp entry governs {code!r} but that "
                        f"rule's staged_by is {rules[code].staged_by!r} — a "
                        f"severity ramp wraps only codes whose existence rides it "
                        f"(invariant 1d structural half; wrapping a live code is "
                        f"the quiet-type-mismatch mask)"
                    )
        # Baseline half (merge-base seam — see docstring): a DORMANT-born
        # severity-ramp entry may not govern a previously shipped code.
        if (
            baseline_codes is not None
            and c.kind == "severity-ramp"
            and log_stages[0] == Stage.DORMANT.value
        ):
            overlap = sorted(set(c.codes) & baseline_codes)
            if overlap:
                raise CatalogError(
                    f"{ctx}: born DORMANT while governing previously shipped "
                    f"code(s) {overlap} — an EXISTING emitting surface may never "
                    f"enter a stage below its current one (invariant 1d baseline; "
                    f"founder-ack via BREAKING_CHANGES is the only exception)"
                )
        # Schedule half (R-10): still in the birth state at/after the target
        # milestone requires a founder-logged extension entry.
        if stage == log_stages[0] and _milestone_num(milestone, ctx) >= _milestone_num(
            c.target_milestone, ctx
        ):
            has_extension = any(_EXTENSION_NOTE_RE.search(item[2]) for item in c.log)
            if not has_extension:
                raise CatalogError(
                    f"{ctx}: still in its birth state {stage!r} at milestone "
                    f"{milestone} (target_milestone={c.target_milestone}) without a "
                    f"founder-logged extension entry — dormancy is never open-ended "
                    f"(invariant 1d schedule, R-10)"
                )

        # ── Invariant 4: terminal-stage / ceiling coherence ───────────────
        if c.kind != "flow-shape":
            if terminal == Stage.WARNING.value:
                for code in c.codes:
                    if rules[code].severity_ceiling != "warning":
                        raise CatalogError(
                            f"{ctx}: terminal_stage=WARNING requires every governed "
                            f"rule to carry severity_ceiling='warning'; {code!r} has "
                            f"{rules[code].severity_ceiling!r} (invariant 4)"
                        )
                if stage == Stage.PROMOTED.value:
                    raise CatalogError(
                        f"{ctx}: stage=PROMOTED on a WARNING-terminal change "
                        f"(invariant 4: no further stage exists for a permanent "
                        f"advisory)"
                    )
            elif c.kind == "severity-ramp":
                # Vice-versa: a ceiling-carrying rule whose existence rides
                # this ramp must terminate at WARNING.
                for code in c.codes:
                    if rules[code].severity_ceiling == "warning":
                        raise CatalogError(
                            f"{ctx}: governed rule {code!r} carries "
                            f"severity_ceiling='warning' but terminal_stage is "
                            f"{terminal!r} — the permanence must be two-field "
                            f"coherent (invariant 4)"
                        )

        # ── Invariant 5 (cont.): evidence required on unfinished ramps ────
        if order[terminal] > order[stage] and not c.evidence:
            raise CatalogError(
                f"{ctx}: evidence must be non-empty while terminal stage "
                f"{terminal!r} exceeds current stage {stage!r} (invariant 5: "
                f"stage advances cite named §2.5 criteria)"
            )

        out[c.key] = c

    # Rule-side coverage: every rule-level staged_by resolves to an entry that
    # lists the code, and only severity-ramp entries may be rule-level targets
    # (kinds (b)/(c) scope rows/configurations, never whole codes).
    for code, rule in rules.items():
        if not rule.staged_by:
            continue
        change = out.get(rule.staged_by)
        if change is None:
            raise CatalogError(
                f"{code}: staged_by {rule.staged_by!r} does not resolve in "
                f"STAGED_CHANGES (invariant 5)"
            )
        if code not in change.codes:
            raise CatalogError(
                f"{code}: staged_by {rule.staged_by!r} resolves to an entry that "
                f"does not list the code in its codes tuple (invariant 5)"
            )
        if change.kind != "severity-ramp":
            raise CatalogError(
                f"{code}: rule-level staged_by must reference a severity-ramp "
                f"entry; {rule.staged_by!r} is kind {change.kind!r} (row/flow "
                f"scope never governs a whole code)"
            )
    return out


def rules_to_jsonable(
    rules: dict[str, Rule],
    staged_changes: dict[str, StagedChange] | None = None,
) -> dict[str, dict]:
    """Serialize the catalog to the JSON-native shape of rule_catalog.json.

    Sorted by code; every field always present (no conditional omission — a
    stable shape is worth more than a lean file). ``severity`` is the derived
    default-context severity so TS consumers never re-implement the policy;
    for a DORMANT-staged code it is ``""`` — emission is structurally
    impossible, so there is no severity to publish (spec 05 §2.2).

    The top-level ``staged_changes`` section (spec 05 §2.1/§2.2) serializes
    the staging registry — ``staged_changes=None`` publishes the live
    :data:`STAGED_CHANGES`; tests pass an explicit mapping. Its key can never
    collide with a rule code (codes are UPPER_SNAKE).
    """
    staged = STAGED_CHANGES if staged_changes is None else staged_changes
    out: dict[str, dict] = {}
    for code in sorted(rules):
        r = rules[code]
        dormant = bool(
            r.staged_by
            and r.staged_by in staged
            and staged[r.staged_by].kind != "flow-shape"
            and Stage(staged[r.staged_by].stage) is Stage.DORMANT
        )
        entry: dict = {
            "category": r.category.value,
            "severity": "" if (r.status == "reserved" or dormant) else severity_for(r),
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
            "staged_by": r.staged_by,
            "severity_ceiling": r.severity_ceiling,
            "recoverable": r.recoverable,
        }
        out[code] = entry
    out["staged_changes"] = staged_changes_to_jsonable(staged)
    return out


def staged_changes_to_jsonable(changes: dict[str, StagedChange]) -> dict[str, dict]:
    """Serialize :data:`STAGED_CHANGES` for rule_catalog.json (spec 05 §2.2).

    Each entry publishes its current ``stage`` plus its resolved
    ``severity_cap`` so row-scoped consumers (the TS engine, the SDK) never
    re-derive severity from category/override data: a row-scoped surface
    resolves as the published per-rule severity min-ed with the row's staged
    change's published cap. ``severity_cap`` is ``null`` for DORMANT (nothing
    may be emitted) and for kind-(c) flow-shape entries (no severity at all —
    their state feeds the flow_config derivation and the trace config
    vector).
    """
    out: dict[str, dict] = {}
    for key in sorted(changes):
        c = changes[key]
        stage = c.stage.value if isinstance(c.stage, Stage) else c.stage
        terminal = (
            c.terminal_stage.value if isinstance(c.terminal_stage, Stage) else c.terminal_stage
        )
        if c.kind == "flow-shape" or stage == Stage.DORMANT.value:
            cap: str | None = None
        else:
            cap = _STAGE_CAP[Stage(stage)]
        out[key] = {
            "kind": c.kind,
            "codes": list(c.codes),
            "decision": c.decision,
            "stage": stage,
            "terminal_stage": terminal,
            "target_milestone": c.target_milestone,
            "severity_cap": cap,
            "evidence": list(c.evidence),
            "log": [list(item) for item in c.log],
            "states": list(c.states),
        }
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# PARITY CONTRACT (spec 04 §6 — pass/engine-level divergence as data)
#
# The graph-JSON parity boundary (spec 04 §5's pick) + the pass/engine-level
# Python↔TS divergences (R1-I11). Source-of-truth home is HERE beside RULES
# (spec 04 §6 / §9 handshake note 2 — this module is spec 05's home for the
# catalog family). The artifact (parity_contract.json), its writer
# (write_parity_contract), and the freshness/coherence test (T13) live in the
# fixtures package (spec 04 §6). No keel-app copy: the contract is consumed by
# harnesses, CI, and docs, never the browser bundle.
# ═══════════════════════════════════════════════════════════════════════════════

#: The Python↔TS parity boundary (spec 04 §5, as data). The canonical graph
#: model is THE boundary; the ~1.3k-LOC TS parser stays OUTSIDE the
#: conformance/trace net (this project changes no parse semantics). The
#: revisit triggers are the only events that pull text-level parse fixtures in.
PARITY_BOUNDARY: dict[str, object] = {
    "artifact": "graph-json",
    "ts_parser_in_net": False,
    "revisit_triggers": ("parse-semantics-change", "text-shape-divergence"),
}

#: THE engine-divergence set (spec 04 §6). Seeded with the TS pass-7 skip: the
#: editor entrypoint deliberately never runs phase-ordering (too noisy with
#: multi-signal factories); the Python-side data-driven rule stays
#: authoritative and the server re-validates on save. This divergence OUTLIVES
#: the deleted ``pass7-phases.ts`` file — it lives here as data, not as dormant
#: rule logic on disk (spec 04 §1.3).
_ALL_ENGINE_DIVERGENCES: tuple[EngineDivergence, ...] = (
    EngineDivergence(
        key="ts-pass7-skip",
        surface="phase-ordering",
        reason=(
            "editor UX: branch-reset false positives too noisy; AI tools have "
            "registry access (index.ts policy, pre-collapse); Python-side rule "
            "remains authoritative and the server re-validates on save"
        ),
        codes=("PHASE_ORDER_VIOLATION",),
        since="2026-07-22",
    ),
)


def parity_contract_to_jsonable(
    rules: dict[str, Rule],
    divergences: tuple[EngineDivergence, ...] | None = None,
    boundary: dict[str, object] | None = None,
) -> dict:
    """Serialize the parity contract to the JSON-native shape (spec 04 §6).

    Coherence is enforced HERE (so a bad divergence fails regen, per spec 04
    §6): every code named by an ``engine_divergences`` entry must resolve in
    ``rules`` with ``ts_mirrored=False``. A divergence over an unknown code, or
    over a ``ts_mirrored=True`` code, is a contradiction and raises
    :class:`CatalogError`. Duplicate divergence keys also raise. ``divergences``
    / ``boundary`` default to the live module data; tests pass explicit
    mappings to exercise the coherence gate.

    Output shape (stable, every field always present)::

        {
          "parity_boundary": {"artifact", "ts_parser_in_net", "revisit_triggers"},
          "engine_divergences": [
            {"key", "surface", "reason", "codes", "since"}, ...  # sorted by key
          ],
        }
    """
    divs = _ALL_ENGINE_DIVERGENCES if divergences is None else divergences
    bnd = PARITY_BOUNDARY if boundary is None else boundary

    seen: set[str] = set()
    for d in divs:
        if d.key in seen:
            raise CatalogError(f"engine_divergences: duplicate key {d.key!r}")
        seen.add(d.key)
        if not d.codes:
            raise CatalogError(f"engine_divergences[{d.key!r}]: no codes declared")
        for code in d.codes:
            rule = rules.get(code)
            if rule is None:
                raise CatalogError(
                    f"engine_divergences[{d.key!r}]: code {code!r} does not resolve in RULES"
                )
            if rule.ts_mirrored:
                raise CatalogError(
                    f"engine_divergences[{d.key!r}]: code {code!r} is "
                    f"ts_mirrored=True — a pass-level divergence over a mirrored "
                    f"code is a contradiction (spec 04 §6). Flip the rule to "
                    f"ts_mirrored=False, or remove the divergence entry."
                )

    return {
        "parity_boundary": {
            "artifact": bnd["artifact"],
            "ts_parser_in_net": bnd["ts_parser_in_net"],
            "revisit_triggers": list(bnd["revisit_triggers"]),
        },
        "engine_divergences": [
            {
                "key": d.key,
                "surface": d.surface,
                "reason": d.reason,
                "codes": list(d.codes),
                "since": d.since,
            }
            for d in sorted(divs, key=lambda d: d.key)
        ],
    }


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
    Rule(
        code="PIPELINE_VARIABLE_CYCLE",
        category=RuleCategory.CORRECTNESS,
        summary="Pipeline variables reference each other in a cycle.",
        message_template=(
            "Circular variable dependency: {cycle}. Variables cannot "
            "reference each other in a cycle."
        ),
        template_params=("cycle",),
        explain=(
            "The variable-reference graph must be a DAG: the interpreter "
            "inlines a named `Pipeline` variable at every reference "
            "(transparently, like the resolver), so a variable that reaches "
            "itself — directly (`a` uses `a`) or through other variables or "
            "factory bodies (`a` uses `b`, `b` uses `a`) — can never "
            "resolve or run. Detection runs at the structural stage (pass "
            "3b, AFTER factory expansion so cycles routed through factory "
            "bodies are visible as direct variable→variable edges) and "
            "short-circuits validation before any walk that inlines "
            "variable bodies — the pass-6 interpreter walk, pass-7 phase "
            "ordering, and the pass-7b universe-mask taint walk all rely on "
            "this gate for acyclicity. The named cycle is deterministic: it "
            "starts at the first-defined variable still on a cycle and "
            "follows first-defined dependencies (`dsl/resolver.py "
            "find_variable_cycle` — the same oracle behind the resolver's "
            "runtime ResolveError, so both surfaces name the identical "
            "cycle). One issue names one cycle; break it and re-validate to "
            "surface any further cycle."
        ),
        passes=("3",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=("Break the cycle by removing one of the references in {cycle}."),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
    Rule(
        code="FACTORY_CALL_CYCLE",
        category=RuleCategory.CORRECTNESS,
        summary="Factories call each other in a cycle.",
        message_template=(
            "Circular factory dependency: {cycle}. Factories cannot call each other in a cycle."
        ),
        template_params=("cycle",),
        explain=(
            "The factory-call graph must be a DAG: pass-3 expansion inlines "
            "a factory body at every call site and recurses into the inlined "
            "body, so a factory that reaches itself — directly (`f` calls "
            "`f`) or through other factories (`f` calls `g`, `g` calls `f`) "
            "— can never expand, resolve, or run. Detection runs at the "
            "structural stage (pass 3a, BEFORE expansion — the expansion "
            "itself is the walk that would recurse unbounded, which is the "
            "one deliberate placement difference from the variable sibling "
            "PIPELINE_VARIABLE_CYCLE's post-expansion pass 3b) and "
            "short-circuits validation so neither engine's factory-inlining "
            "walk ever sees a cyclic graph. Edges follow expansion exactly: "
            "factory calls used as steps at any structural depth; loops "
            "routed through a named Pipeline variable are NOT factory edges "
            "— expansion leaves VariableRef in place, and pass 3b reports "
            "them as variable cycles. The named cycle is deterministic: it "
            "starts at the first-defined factory still on a cycle and "
            "follows first-defined dependencies (`dsl/validator.py "
            "find_factory_cycle` — the same oracle behind resolve_strategy's "
            "runtime ResolveError, so both surfaces name the identical "
            "cycle). One issue names one cycle; break it and re-validate to "
            "surface any further cycle."
        ),
        passes=("3",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=("Break the cycle by removing one of the factory calls in {cycle}."),
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
        code="COMPONENT_NOT_RUNNABLE",
        category=RuleCategory.CORRECTNESS,
        summary="The resolved component version is a constrained shell that cannot execute.",
        message_template=(
            "'{name}' v{version} does not execute — it is a constrained shell "
            "kept so existing pipelines keep resolving. {redirect}"
        ),
        template_params=("name", "version", "redirect"),
        explain=(
            "The constrain-at-v2 pattern (dsl-mtf-clocks spec 01 §8.4/§8.5, "
            "spec 02 §8.2) retires an operator WITHOUT deleting it: the name "
            "stays registered forever so pinned blobs keep loading, but the "
            "retired version's run() raises unconditionally and directs to the "
            "replacement. This code is the authoring-time half of that pattern "
            "— without it, new authoring against the shell validates GREEN and "
            "then hard-raises mid-run, breaking the DSL's 'validates ⇒ runs' "
            "contract (measured and live for pre-Gate-2 users; audit finding "
            "S1, fixed by deferring the shell's registration to this commit). "
            "DEPRECATED_COMPONENT cannot carry it: green is defined as zero "
            "ERRORS, so a component-level warning still leaves a green "
            "strategy whose run raises. Table-driven and keep-by-omission: the "
            'shell declares `not_runnable = "<redirect>"` at registration and '
            "the generated registry metadata carries it per version, so a "
            "pinned RUNNABLE version of the same component is silent. Minted "
            "unstaged at the Gate-2 flip (T-M4f-5) rather than ramped, because "
            "it protects no pre-existing population by construction: before "
            "this commit the shell was unregistered, so every strategy that "
            "can fire this code is authored after it. Measured 0 fires — "
            "SignalTimeframeConverter has 0 uses in source and 0 across 2,190 "
            "stored blobs (R6 §8), and 0 across the 18 library strategies."
        ),
        passes=("4",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="{redirect}",
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
        code="INVALID_UNIVERSE_LEVERAGE",
        category=RuleCategory.CORRECTNESS,
        summary="Universe max_leverages declaration is malformed.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The committed `universe.max_leverages` map (per-asset venue "
            "maxLeverage, resolved from trading.instruments by "
            "universe_resolve) must be a dict of string symbol -> numeric "
            "(int or float) maxLeverage, with every value finite and > 0. "
            "Three shapes under one code: not-a-dict "
            '("Universe max_leverages must be a dict of symbol -> max '
            'leverage, got {type}."), a non-string key (Python-only — graph '
            "JSON keys are always strings), and a non-numeric / non-finite / "
            "<= 0 value (\"Universe max_leverages['{symbol}'] must be a "
            'finite number > 0, got {value}."). Consumers divide by these '
            "values (PortfolioMarginCap's mm fraction is 1/(2 x maxLev)): a "
            "0 entry is a ZeroDivisionError at run time and a negative entry "
            "flips a margin cap into an amplifier, so malformed entries are "
            "rejected at write time. NaN/Infinity values are unrepresentable "
            "in graph JSON fixtures; those arms are covered by "
            "validator-level unit tests in both engines."
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
        passes=("6", "9"),
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
            "Cannot resample {source_tf} → {target_tf}: resampling goes fine → "
            "coarse, aggregating completed {source_tf} bars into larger ones. "
            "A {target_tf} target is FINER than the source — that would invent "
            "bars. To carry a coarse value onto a finer clock, project it "
            "instead."
        ),
        template_params=("source_tf", "target_tf", "fix_component"),
        suggestion_template=(
            "To go coarse → fine, insert {fix_component} (projection: "
            "forward-fill of the last COMPLETED bar). To aggregate, choose a "
            "target that {source_tf} tiles evenly."
        ),
        explain=(
            "Resampling goes fine → coarse (aggregation, has a method=); "
            "to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). "
            "Resampling can only aggregate upward (e.g. 15min → 1h); a target "
            "below the source would require inventing data. Enforced by the "
            "same validate_resample_config rule table at runtime (as an uncoded "
            "ValueError) so the agent sees identical text at every layer. "
            "Re-based at the clock-transform-rebase PROMOTED flip "
            "(dsl-mtf-clocks spec 02 §2.2/§3.4, T-M4f-5): the pass-6 judgment "
            "row J-COARSEN.direction is now the SOLE emitter, at every "
            "transform site — the historical pass-9 Globals-path block and its "
            "message_override are gone. The K15 template realignment lands "
            "with that removal (it was deferred from the M4b mint precisely "
            "because the override would have desynchronized Python from the "
            "TS engine, which has always rendered this template): both engines "
            "now render the direction vocabulary and the computed "
            "{fix_component} repair. Applicability is MAYBE_INCORRECT, not "
            "machine-applicable: the two repair arms (insert the projector vs "
            "choose a coarser target) are an author decision the checker "
            "cannot make, and no suggested_edit payload is emitted."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        applicability=Applicability.MAYBE_INCORRECT,
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
        passes=("6", "9.resampler"),
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
        passes=("6", "9.resampler"),
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
        passes=("6", "9.resampler"),
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
    # TOMBSTONE (dsl-mtf-clocks spec 02 §2.5, flipped at the
    # clock-transform-rebase PROMOTED commit, T-M4f-5): INVALID_RESAMPLER_CONFIG
    # was the str-dispatch fallback over validate_resample_config's ValueError
    # text in pass 9's _validate_resampler_config. That emitter block is GONE —
    # every arm is now a named pass-6 judgment premise — so the fallback has no
    # body to degrade to. Reserved, never re-minted (protobuf discipline);
    # acknowledged active→reserved in fixtures/BREAKING_CHANGES.md.
    Rule(
        code="INVALID_RESAMPLER_CONFIG",
        category=RuleCategory.CORRECTNESS,
        summary="",
        message_template="",
        template_params=(),
        explain="",
        passes=(),
        surfaces=(),
        ts_mirrored=False,
        status="reserved",
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # M4 clock family (dsl-multi-timeframe-clocks spec 02 §2.1/§2.3) — the
    # five clock outcome classes of spec 01 §11.1 plus the one mechanics code
    # executing spec 01 §8.4's v1-pin mandate. ALL SIX ride DORMANT
    # STAGED_CHANGES keys minted below (§3.2, R-4): the shipped configuration
    # is SILENCE (R-3); measurement runs only under staged_stage_override;
    # flips are Gate-2 founder acts. fixture_waivers are the M4b bridge —
    # the spec 02 §6.2 conformance fixture set lands at M4d and deletes them.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="CLOCK_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="A value produced on one clock is consumed on another.",
        message_template=(
            "'{consumer}' combines values on different clocks: '{actual_field}' is on "
            "{actual_clock}, '{expected_field}' is on {expected_clock}, and this "
            "strategy declares execution on {declared_clock}{via}. Every value a step "
            "consumes must be on one clock. {symptom}"
        ),
        template_params=(
            "consumer",
            "actual_field",
            "actual_clock",
            "expected_field",
            "expected_clock",
            "declared_clock",
            "via",
            "symptom",
            "fix_component",
        ),
        explain=(
            "The headline clock rule (dsl-mtf-clocks spec 01 §6.1 cmix, strict + "
            "slot sites; spec 02 §2.1.1). "
            "Resampling goes fine → coarse (aggregation, has a method=); "
            "to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). "
            "Two values meet at one step but tick on "
            "different clocks — combining them as-is silently re-times the "
            "strategy. The completed-bar rule in industry terms: the projected "
            "value at fine time t is the coarse bar whose close time ≤ t (the "
            "same rule as Nautilus's ts_init-at-close, LEAN's EndTime "
            "transmission, and vectorbt's realign-closing). Projection therefore "
            "HOLDS: a 1d value is held constant across 24 consecutive 1h "
            "execution bars — the hold length is period_coarse / period_fine "
            "bars. What this rule refuses to let happen silently is exactly the "
            "naive pandas idiom: a bare reindex / resample().ffill() over "
            "mismatched labels, which drops or duplicates rows depending on the "
            "consumer. {via} shapes: '' on a composer/record edge; \" via slot "
            "'{name}'\" on the pass-8 slot read (J-SLOTREAD clock half) — at the "
            "slot arm {actual_field} binds the SLOT name and {expected_field} "
            "the consuming input's name (no record fields exist at a slot "
            "read). {symptom} shapes, selected by the consumer's registered "
            "category: (1) intersect (combiner family): 'Combined as-is, only "
            "the bars common to both clocks survive — the strategy would "
            "silently execute on {actual_clock} instead of the declared "
            "{declared_clock}.'; (2) reindex/NaN-hold (gate/scale family): "
            "'Combined as-is, the {actual_clock} value is read on 1 of every "
            "{ratio} {expected_clock} bars; the other {k_bars} are NaN, and NaN "
            "means HOLD.' (ratio = p_actual / p_expected, statically computed); "
            "(3) phase (same period, different offset): 'Both sides run at "
            "{period_tf}, but their bars are labelled {phase_delta} apart — "
            "every read is one bar stale.' Shape 3 fires whenever p_a = p_e and "
            "o_a ≠ o_e; unknown consumers get shape 1 (conservative — rows are "
            "lost, not invented). Wording rule (spec 01 §3.2): labels are "
            "right-edge — the last completed daily bar at 09:00 UTC is "
            "labelled today 00:00 and contains only YESTERDAY's data, so no "
            'message says "today\'s bar" without naming that convention. The '
            "fix is a visible inserted projection step ({fix_component} — "
            "TargetSignalProjector() when the repair clock is the declared "
            "execution clock, SignalProjector(target_timeframe=…) for an "
            "explicit mid-cascade clock), never a silent re-clock."
        ),
        suggestion_template=(
            "Project the {actual_clock} value onto {expected_clock} before "
            "'{consumer}': insert {fix_component} at the end of '{actual_field}'. "
            "Projection holds the last COMPLETED {actual_clock} bar on the finer "
            "grid; resampling is the opposite direction (fine → coarse) and is "
            "not the fix here."
        ),
        passes=("6", "8"),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="clock-mismatch",
        applicability=Applicability.MACHINE_APPLICABLE,
    ),
    Rule(
        code="TERMINAL_CLOCK_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="The pipeline's terminal clock is not the declared execution clock.",
        message_template=(
            "This pipeline produces weights on {actual_clock}, but "
            "Globals(target_timeframe='{declared_tf}') declares execution on "
            "{expected_clock}. {consequence}"
        ),
        template_params=(
            "actual_clock",
            "declared_tf",
            "expected_clock",
            "consequence",
            "fix_component",
            "origin_step",
            "actual_tf",
        ),
        explain=(
            "The terminal clock rule (dsl-mtf-clocks spec 01 §6.4 J-PIPE; spec "
            "02 §2.1.2). "
            "Resampling goes fine → coarse (aggregation, has a method=); "
            "to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). "
            "The outermost pipeline must land on the declared "
            "execution clock κ_exec = (Globals.target_timeframe, "
            "Globals.bar_offset). The mechanism the symptom hides: the executor "
            "reindexes prices onto the weights index, and every coarse "
            "right-edge label is also a fine label, so the run SUCCEEDS on the "
            "wrong clock — the backtest gives no hint. Globals.target_timeframe "
            "is simultaneously the live tick cadence and the stored EventBridge "
            "cron, so the re-declare repair arm changes the deployment "
            "schedule. {consequence} shapes, selected by comparing the terminal "
            "clock to κ_exec: (1) coarser (p_actual > p_exec): 'Deployed, the "
            "live scheduler ticks on {expected_clock} and would refuse "
            "{refuse_ratio} of ticks with DATA_LAG (no order placed, one FAILED "
            "signal_run row per refusal); the backtest completes anyway, with "
            "metrics annualized off the wrong grid.' (refuse_ratio renders as "
            'e.g. "3 of every 4", from p_actual / p_exec); (2) finer '
            "(p_actual < p_exec): 'Deployed, the live scheduler ticks on "
            "{expected_clock} and reads only 1 of every {ratio} produced "
            "weights — the others are computed and never traded; the backtest "
            "executes them all, so live and backtest diverge.' (ratio = p_exec "
            "/ p_actual); (3) phase-only (p_a = p_e, o_a ≠ o_e): 'Both run at "
            "{period_tf}, but the weight labels sit {phase_delta} off the live "
            "tick grid — every live tick reads a one-bar-stale weight.' The "
            "machine edit inserts {fix_component} after the LAST step of the "
            "outermost pipeline; {origin_step} renders the actual clock's "
            "origin, which for a composer-set clock is the composer step "
            "itself. Both repair arms ride valid_options: insert-projector "
            "(primary) and re-declare Globals (named as schedule-changing) — "
            "the checker cannot know which the author meant."
        ),
        suggestion_template=(
            "Project onto the execution clock: insert {fix_component} as the "
            "pipeline's final step (the terminal clock was set at {origin_step}) "
            "— or declare Globals(target_timeframe='{actual_tf}') if "
            "{actual_clock} is the cadence you meant (this also changes the live "
            "tick schedule)."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="clock-mismatch",
        applicability=Applicability.MACHINE_APPLICABLE,
    ),
    Rule(
        code="CLOCK_NOT_HARMONIC",
        category=RuleCategory.CORRECTNESS,
        summary="A re-clock between non-harmonic timeframes (neither tiles the other evenly).",
        message_template=(
            "Cannot {op_word} {source_clock} → {target_clock}: {fine_tf} does "
            "not divide {coarse_tf} evenly. {consequence}"
        ),
        template_params=(
            "op_word",
            "source_clock",
            "target_clock",
            "fine_tf",
            "coarse_tf",
            "consequence",
            "param",
            "fix_value",
        ),
        explain=(
            "The divisibility rule (dsl-mtf-clocks spec 01 §2.3 Theorem 1 / "
            "§5.2; spec 02 §2.1.3). Harmonic means one timeframe tiles the "
            "other evenly — the finer period divides the coarser with no "
            "remainder. Resampling goes fine → coarse (aggregation, has a "
            "method=); to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). {op_word} is the direction word for "
            "the failing rule: 'resample' at a coarsen site, 'project' at a "
            "project site. {consequence} shapes: coarsen ⇒ 'The produced bars "
            "would alternate between {alt_low} and {alt_high} of real time — "
            "the label would lie about its content, and chained re-clocking "
            "would disagree with direct.' (measured: 2h→3h alternates 2 h/4 h "
            "of real coverage); project ⇒ 'The coarse labels would not land on "
            "the fine grid — values would appear between bars instead of at "
            "them.' valid_options is computed from the (P, |) lattice and is "
            "never empty: the gcd entry is the coarsest source that tiles the "
            "target ('nearest' below), the lcm entry the finest target the "
            "source tiles. The suggested_edit is local — set_param on the "
            "offending step's own clock parameter with a per-direction "
            "formula: at resample, the smallest alphabet period the source "
            "divides that is ≥ the requested target; at project, the largest "
            "alphabet divisor of the source ≤ the requested target (a "
            "coarser-than-source projection target is never proposed). At a "
            "globals-wired site (declaration-backed step with no authored "
            "clock param) the emitted issue downgrades to has_placeholders "
            "with both arms in valid_options: the harmonic "
            "Globals(target_timeframe) value (a schedule-changing edit) and "
            "the branch-local explicit-component alternative."
        ),
        suggestion_template=(
            "Choose a harmonic pair: set {param}='{fix_value}' (the nearest "
            "timeframe the source tiles evenly), or re-clock from a source that "
            "divides the target."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="clock-harmonic",
        applicability=Applicability.MACHINE_APPLICABLE,
    ),
    Rule(
        code="PROJECT_WRONG_DIRECTION",
        category=RuleCategory.CORRECTNESS,
        summary="A projector pointed fine → coarse (projection only goes coarse → fine).",
        message_template=(
            "'{step}' asks to project {source_clock} → {target_clock}, but "
            "projection goes coarse → fine only (it holds the last COMPLETED "
            "coarse bar on the finer grid). {target_clock} is coarser than "
            "{source_clock} — going that way is aggregation, which is "
            "resampling's job and is not supported for computed signals."
        ),
        template_params=("step", "source_clock", "target_clock"),
        explain=(
            "The direction lesson (dsl-mtf-clocks spec 01 §5.2/§11.2; spec 02 "
            "§2.1.4). Resampling goes fine → coarse (aggregation, has a "
            "method=); to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). The completed-bar rule in industry "
            "terms: the projected value at fine time t is the coarse bar whose "
            "close time ≤ t. A projector pointed fine → coarse is an attempted "
            "aggregation of a computed signal, which the platform does not "
            "support — the one legal rewrite is coarsen-data-then-compute. "
            "Worked example: instead of\n"
            "    EWMA(window=20), SignalProjector(target_timeframe='4h')   "
            "# 1h signal → 4h: rejected\n"
            "compute on the coarse clock directly:\n"
            "    TimeframeResampler(target_timeframe='4h'), EWMA(window=20)  "
            "# resample RAW data, then compute\n"
            "No suggested_edit is attached: the repair is a re-ordering, and "
            "the edit vocabulary has no move_step — the envelope must not "
            "fabricate one."
        ),
        suggestion_template=(
            "Compute the signal on {target_clock} directly: resample the RAW data "
            "to {target_clock} BEFORE the indicator (TimeframeResampler / "
            "TargetTimeframeResampler), then compute — do not aggregate the "
            "computed signal."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="clock-projector",
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="PROJECT_INPUT_UNCLOCKED",
        category=RuleCategory.CORRECTNESS,
        summary="A projector whose input carries no clock.",
        message_template=(
            "'{step}' has nothing to project: its input carries no clock "
            "({input_desc}). A projector re-clocks an existing time-indexed "
            "value; it cannot create one."
        ),
        template_params=("step", "input_desc"),
        explain=(
            "The totality rule for project sites (dsl-mtf-clocks spec 01 §5.4; "
            "spec 02 §2.1.5). "
            "Resampling goes fine → coarse (aggregation, has a method=); "
            "to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). "
            "A clocked producer is a data loader or a "
            "resampler/projector chain — something that established a clock "
            "from declared literals. {input_desc} names the concrete reachable "
            "shape: 'it is the first step' (the projector received no input at "
            "all), 'a {base} value' (a literal/structural base that carries no "
            "clock), or 'a record' (a Parallel output — project inside the "
            "branch, or after the consumer that combines the branches). No "
            "suggested_edit is attached: auto-applying remove_step would "
            "delete the author's stated intent."
        ),
        suggestion_template=(
            "Place '{step}' after a clocked producer (a data loader or a "
            "resampler chain), or remove it if no re-clock is needed."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="clock-projector",
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="TOMBSTONED_OPTION_PINNED",
        category=RuleCategory.CORRECTNESS,
        summary="A version-locked instance uses a parameter value retired at a later version.",
        message_template=(
            "'{component}' v{version} is pinned with {param}={value}, an option "
            "retired at v{new_version}: {reason}. The pinned instance keeps "
            "loading and running unchanged; new authoring cannot use it."
        ),
        template_params=(
            "component",
            "version",
            "param",
            "value",
            "new_version",
            "reason",
            "replacement",
        ),
        explain=(
            "The v1-pin diagnostic (dsl-mtf-clocks spec 01 §8.4 item 2; spec 02 "
            "§2.3), executing the constrain-at-v2 + tombstone pattern. "
            "Resampling goes fine → coarse (aggregation, has a method=); "
            "to go coarse → fine, project (forward-fill of the last "
            "COMPLETED bar, no method). "
            "Why no "
            "existing code can carry it: COMPONENT_VERSION_PHASED_OUT requires "
            "the pinned version GONE from the registry (v1 stays registered "
            "forever); DEPRECATED_COMPONENT is component-level and SUSPICIOUS; "
            "and under the lock-effective registry the v1 schema ADMITS the "
            "value, so firing PARAM_INVALID_OPTION would break the lock "
            "contract's meaning. Mechanism: pass 5 resolves each component call "
            "against the lock-effective registry; when the resolved version "
            "predates a constraint and the literal value appears in the "
            "generated tombstoned-options table (written from the v2 "
            "registration data, never hand-maintained), this rule fires — "
            "table-driven, O(1) per param. The {reason} for "
            "AlignToFrequency(method='bfill') sizes the lookahead in time: it "
            "reads a bar that closes up to 18 hours in the future, and it "
            "validates clean today. The pinned instance keeps loading and "
            "running because the blob/ABI invariant promises exactly that — "
            "which is also why the terminal severity is capped at a permanent "
            "WARNING (severity_ceiling): a blocking error on re-validating a "
            "locked source would contradict that promise."
        ),
        suggestion_template=(
            "Migrate to {replacement} and re-run. Results may change, because the "
            "option was retired for a correctness reason."
        ),
        passes=("5",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Fires only on version-locked sources resolving to a pre-constraint "
            "version; tombstoned-option data is registry-diff-side and the Python "
            "validator is the gate for locked sources. Mirror deferred until a "
            "measured editor need exists."
        ),
        staged_by="tombstoned-option-pinned",
        severity_ceiling="warning",
        applicability=Applicability.HAS_PLACEHOLDERS,
        # No fixture_waiver: the firing surface LANDED at T-M4e-2. The
        # generated tombstoned-options table (registry_metadata.json, written
        # by write_registry_metadata from the versioned registration data) and
        # the pass-5 lookup that reads it now make this code emit, and its two
        # spec 02 §6.2 fixtures (conformance/TOMBSTONED_OPTION_PINNED/
        # reject_pinned_bfill_v1 + accept_pinned_v1_clean) pin the fire under
        # expected_at_terminal — measured: a v1-locked
        # AlignToFrequency(method="bfill") is silent at the shipped DORMANT
        # stage and fires exactly one permanent WARNING at terminal.
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Runtime-only structured codes (Layer C PipelineValidator + Lookahead).
    # Dormant on production paths today (execution.py skips them in BACKTEST
    # mode); they enter the corpus mandate when the verify-spine work revives
    # them (final report §4.2).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="TYPE_HINTS_UNAVAILABLE",
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
    # TOMBSTONE (dsl-mtf-clocks spec 02 §2.5, flipped with the R-22.2 emitter
    # deletion at T-M4f-5): RESAMPLE_CHECK was a name-substring advisory
    # ("resample" in step_name) in pipeline/lookahead.py that fired 18/18 on
    # the shipped library — a 100 % fire rate carries zero information — and
    # its content (completed-bar handling) is the projector's DEFINITION
    # (spec 01 §3), not a question to ask the author. Emitter deleted; reserved,
    # never re-minted; acknowledged active→reserved in BREAKING_CHANGES.md.
    Rule(
        code="RESAMPLE_CHECK",
        category=RuleCategory.SUSPICIOUS,
        summary="",
        message_template="",
        template_params=(),
        explain="",
        passes=(),
        surfaces=(),
        ts_mirrored=False,
        status="reserved",
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
        recoverable=False,  # platform-intervention (spec 05 §3.1 #15)
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
    # ═══════════════════════════════════════════════════════════════════════
    # Type-theorem codes (dsl-type-system spec 05 §1.2) — minted at M2, ALL
    # DORMANT via STAGED_CHANGES. Each is the catalog NAME of a spec 01 §2.4
    # outcome row produced by the one subsumption relation over value
    # domains; NONE carries rule-specific checking logic in either engine
    # (the difference from the reverted 2026-07-21 bespoke Pass-8 elif).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="VALUE_DOMAIN_MISMATCH",
        category=RuleCategory.CORRECTNESS,  # end-state error (staged, spec 05 §2)
        summary="A statically-known value set violates a hard value-domain demand.",
        message_template=(
            "'{consumer}' expects {expected_display} values in {expected_domain}, "
            "but '{producer}' supplies values {actual_domain}{via}."
        ),
        template_params=(
            "consumer",
            "expected_display",
            "expected_domain",
            "producer",
            "actual_domain",
            "via",
        ),
        explain=(
            "The catalog name of spec 01 §2.4's `eq/sub × viol × hard` outcome "
            "row, in both strict and slot modes (the mode-invariant law): a "
            "statically-known value description that is not `⊑` a hard `Set` "
            "demand.\n\n"
            "**The two-layer model.** Every series type carries two layers: a "
            "BASE (the carrier — `SignalSeries`, `ForecastSeries`, ...) and a "
            "VALUE DOMAIN (what values the series may contain — `Set{-1,0,1}`, "
            "`Interval[0,1]`, or `⊤` when nothing is statically known). A type "
            "check passes only when both layers are subsumed: base compatibility "
            "AND domain subsumption. This code fires when the base fits but the "
            "known value set provably violates the demanded domain.\n\n"
            "**Hard vs soft tiers.** `Set` domains are LAW (hard tier): a "
            "consumer demanding `Set{-1,0,1}` breaks at runtime on other values. "
            "`Interval`/Bounds domains are CONVENTION (soft tier) and belong to "
            "VALUE_BOUNDS_ADVISORY, never to this code.\n\n"
            "**Slot provenance.** The `{via}` placeholder has two documented "
            "shapes (multi-shape pattern, see catalog header): the empty string "
            "on a direct edge, and ` via slot '<name>'` on a slot read — read "
            'it as "the value was stored at X, produced by Y": the blame '
            "chain runs origin → transfer → store → read.\n\n"
            "**Runtime guard.** The runtime guard (`ensure_binary_signal`) "
            "remains permanently authoritative for values the checker cannot "
            "see (`⊤` actuals — those classify `unproven`, VALUE_DOMAIN_"
            "UNPROVEN's territory, never this code). At M3 the guard's failure "
            "mints under this same code in the typed envelope (tier "
            "'runtime') — compile-time and runtime speak one vocabulary for "
            "one contract.\n\n"
            "**Remediation ladder.** Either make the values provably discrete — "
            "threshold via ThresholdCross / AboveThresholdFilter / "
            "RangeSelector before the demanding consumer — or fix the producing "
            "literal (e.g. change `ConstantForecast(value=10)` to a value in "
            "the demanded set)."
        ),
        passes=("6", "8"),  # strict-mode edges + slot reads (pass vocabulary
        #                     retained; pass→judgment mapping is spec 03's)
        surfaces=_PY_TS_RT,
        ts_mirrored=True,  # GOAL done-when 2: mirrored in TS
        suggestion_template=(
            "Feed '{consumer}' a signal whose values are provably in "
            "{expected_domain} — threshold via ThresholdCross / "
            "AboveThresholdFilter / RangeSelector — or change the literal on "
            "'{producer}' to a value in {expected_domain}."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="value-domain-rule",  # STAGED_CHANGES §2.4 entry 1
        recoverable=True,
    ),
    Rule(
        code="VALUE_DOMAIN_UNPROVEN",
        category=RuleCategory.CORRECTNESS,
        severity_ceiling="warning",  # Gate-2 (T-MR-3): PERMANENT WARNING — founder
        #                              ruling 3 (R-7 disposition; Gate-2 rulings
        #                              2026-07-24). d2-refinement-unproven's
        #                              terminal_stage is WARNING; invariant 4
        #                              (spec 05 §2.3) requires the ceiling here.
        summary="A hard value-domain demand receives values the checker cannot prove.",
        message_template=(
            "'{consumer}' expects {expected_display} values in {expected_domain}, "
            "but the values reaching it{via} are not statically known. The runtime "
            "guard will reject out-of-domain values at execution."
        ),
        template_params=("consumer", "expected_display", "expected_domain", "via"),
        explain=(
            "The catalog name of spec 01 §2.4's `eq/sub × unproven × hard` "
            "outcome row (the D2 refinement-unproven advisory): a hard `Set` "
            "demand receiving a value set the checker cannot prove (the actual "
            "domain is `⊤`).\n\n"
            "**Unproven vs violated.** VALUE_DOMAIN_MISMATCH fires when the "
            "known values provably VIOLATE the demand; this code fires when "
            "nothing is provable either way. The distinction matters: an "
            "unproven flow may be perfectly correct at runtime (e.g. a MACD "
            "signal thresholded downstream by the consumer itself).\n\n"
            "**Runtime-guard handoff.** The runtime guard "
            "(`ensure_binary_signal`) stays permanently authoritative here: it "
            "rejects out-of-domain values at execution, so an unproven flow is "
            "guarded, not unguarded. This code is the compile-time heads-up "
            "that the proof obligation was deferred to runtime.\n\n"
            "**Fixing is optional but cheap.** Insert a thresholding step "
            "(ThresholdCross / AboveThresholdFilter / RangeSelector) before "
            "the demanding consumer and the flow becomes provable — the "
            "advisory disappears and the runtime guard can never fire.\n\n"
            "**Category note.** CORRECTNESS because the row is error-ELIGIBLE "
            "after the evidence window (frozen 01 §2.4) — but the corpus "
            "predicts the promotion gate holds it at warning indefinitely "
            "(the benign library `⊤`-into-demander chains would be FPs at "
            "error tier). The staging machinery is indifferent to the "
            "prediction; E-LIB0FP decides."
        ),
        passes=("6", "8"),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "If the signal is meant to be discrete, make it provable: threshold "
            "via ThresholdCross / AboveThresholdFilter / RangeSelector before "
            "'{consumer}'."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="d2-refinement-unproven",  # STAGED_CHANGES §2.4 entry 2
        recoverable=True,
    ),
    Rule(
        code="VALUE_BOUNDS_ADVISORY",
        category=RuleCategory.SUSPICIOUS,  # warning by policy
        severity_ceiling="warning",  # the D10-clarification permanence encoded
        #                              as catalog data (spec 05 §2.1/§2.3 inv 4)
        summary="A soft (interval-tier) value convention is unproven or exceeded.",
        message_template=(
            "'{consumer}' conventionally expects {expected_display} values in "
            "{expected_domain}; the values reaching it{via} {finding}. This is a "
            "convention, not a law — the strategy still runs."
        ),
        template_params=(
            "consumer",
            "expected_display",
            "expected_domain",
            "via",
            "finding",
        ),
        explain=(
            "The catalog name of BOTH soft-tier outcome rows of spec 01 §2.4 "
            "(`eq/sub × unproven × soft` and `eq/sub × viol × soft`) — one "
            "code, two documented message shapes (multi-shape pattern, see "
            "catalog header), because the tier doctrine makes them one class: "
            "soft `Interval`/Bounds domains are conventions, not laws (GOAL "
            "non-goal 3; D10-clarification: warnings by design, never "
            "scheduled for error promotion).\n\n"
            "**The `{finding}` shapes.** Rendered by the emitting site as a "
            "pre-formatted clause: `are not statically known` (the unproven "
            "row) · `are known to include <actual_domain>-values outside it` "
            "(the violated row). The machine-readable distinction rides the "
            "envelope's expected/actual fields, never the prose.\n\n"
            "**Permanently warning-class.** This rule carries "
            "`severity_ceiling='warning'` and its staged change "
            "(`soft-bounds-advisory`) terminates at WARNING — no PROMOTED "
            "stage exists for it (spec 05 §2.3 invariant 4 makes the pair "
            "single-edit-proof). A soft-tier finding never blocks a save, a "
            "backtest, or a deploy.\n\n"
            "**Why no suggestion.** Advisory by design: the convention "
            "(e.g. `NormalizedSignal` in `[-1,1]`, forecast scale `[-20,20]`) "
            "documents intent; exceeding it is legal and sometimes deliberate. "
            "Consumers rank via the envelope's staging field."
        ),
        passes=("6", "8"),
        surfaces=_PY_TS,
        ts_mirrored=True,
        applicability=Applicability.NONE,  # no suggestion_template: advisory
        staged_by="soft-bounds-advisory",  # STAGED_CHANGES §2.4 entry 3
        recoverable=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Structural-layer code (spec 01 §5.1 m7) — minted at M2b engine review
    # (MAJOR-2), DORMANT via STAGED_CHANGES. OUTSIDE spec 05 §2.4's enumerated
    # v1 ledger (founder-flagged): the n≥1 Parallel-record precondition the
    # §2.4 mint wave did not cover.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="EMPTY_PARALLEL",
        category=RuleCategory.CORRECTNESS,  # end-state error (staged, spec 05 §2)
        summary="A Parallel block declares no branches (n = 0).",
        message_template=(
            "Parallel block at {step} declares no branches; a Parallel record "
            "requires at least one branch (n ≥ 1)."
        ),
        template_params=("step",),
        explain=(
            "The catalog name of spec 01 §5.1's finding-m7 structural outcome: "
            "an empty `Parallel{}` (n = 0). The grammar excludes it (§1.1: a "
            "closed record has `n ≥ 1` distinct keys), but exclusion needs an "
            "owner — today neither `dsl/validator.py` nor `parser.py` handles "
            "`Parallel{}`, so `Pipeline([PriceDataLoader, {}, EqualWeightSizer])` "
            "validates and the empty record flows through the sizer's dict-row "
            "skip.\n\n"
            "**The structural layer owns it.** Normative assignment (spec 01 "
            "§5.1 m7): the structural pass (spec 03's checking-as-data walk) "
            "rejects `Parallel{}` with this outcome BEFORE typing, so J-REC "
            "carries `n ≥ 1` as a satisfied precondition it never has to check. "
            "It is a new-coverage outcome class; code and severity are catalog "
            "data (spec 05).\n\n"
            "**Graph vs source.** The persisted GraphModel already rejects an "
            "empty-branch Parallel at deserialization (`graph_to_spec` raises "
            "'missing branches'), so the browser editor never persists one — "
            "this code guards the DSL-source / spec-construction path the graph "
            "deserializer never gates (hence ts_mirrored=False, corpus coverage "
            "via a kind:'dsl' fixture, exactly like FORWARD_REFERENCE).\n\n"
            "**Remediation.** Add at least one branch to the Parallel block, or "
            "remove the block entirely if it is not needed."
        ),
        passes=("6",),  # J-REC (record formation) is the pass-6 position
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "The GraphModel (the browser editor's representation and the "
            "canonical persisted form) rejects empty-branch Parallel blocks at "
            "deserialization (graph_to_spec raises 'missing branches') — "
            "structurally upstream of validation — so the browser validator "
            "never sees a `Parallel{}` to flag. This code guards the DSL-source "
            "path; corpus coverage arrives via a kind:'dsl' fixture (spec 02 "
            "§3.3a), as with FORWARD_REFERENCE."
        ),
        suggestion_template=(
            "Add at least one branch to the Parallel block, or remove it if it is not needed."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="empty-parallel-rule",  # STAGED_CHANGES (M2b review mint)
        recoverable=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Universe-mask discipline codes (rolling-universe P2 group (c) —
    # projects/rolling-universe/01-contracts.md §1/§4, Contract C) — minted
    # 2026-08-02, ALL DORMANT via STAGED_CHANGES. One masked-bit taint walk
    # (validator.py pass 7b; rules/universe-mask.ts in TS) computes all three
    # verdicts from the group-(b) ``population_scope`` registry metadata —
    # the only per-component constant is the documented ApplyUniverseMask
    # applier anchor (validation_shared.UNIVERSE_MASK_APPLIERS).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="XS_BEFORE_UNIVERSE_MASK",
        category=RuleCategory.SUSPICIOUS,
        summary="A cross-sectional pooling step runs on a path the universe mask has not masked.",
        message_template=(
            "'{component}' pools across the cross-section, but the path "
            "reaching it does not pass through the universe mask ({mask})"
            "{cleared}."
        ),
        template_params=("component", "mask", "cleared"),
        explain=(
            "Rolling-universe Contract C (projects/rolling-universe/"
            "01-contracts.md §1): in a strategy that computes a universe mask, "
            "the mask must precede the FIRST `population_universe` op on every "
            "path, and nothing between mask and terminal may resurrect masked "
            "cells with a fill. A pooling step (cross-sectional z-score, rank, "
            "pool-global forecast scaler, leverage cap, ...) that runs on an "
            "un-masked path pools over the full baked pool instead of the "
            "active set — single ops are post-hoc repairable, COMPOSITIONS are "
            "not (per-signal pool-vs-active dispersion reweights any "
            "z-combine; any nonlinearity after a z breaks even the single-op "
            "case).\n\n"
            "**How the verdict is computed.** A masked-bit taint walk in the "
            "pass-7 family: the mask applier (ApplyUniverseMask) sets the bit; "
            "`per_column` components preserve it; combiners AND their "
            "branches' bits; Store/Load carry it through slots; components "
            "whose declared `domain_transfer` can produce constant cells "
            "(`union` with a `const` arm — FillNaN's declaration — or a "
            "whole-output `const` transfer) CLEAR it, because a "
            "fill resurrects masked (NaN) cells into the pool. The check "
            "fires on a `population_universe(source: input)` component (D2 "
            "dual-source entries included) whose input bit is unset.\n\n"
            "**Exemptions.** The mask EMITTERS — `universe_filter`-category "
            "components declaring `population_universe` scope "
            "(RollingUniverseMask, RollingVolumeUniverseMask, "
            "TopNAssetSelector, VolumeUniverseReducer(Any)) — ARE the "
            "selection boundary and are never flagged (decision D3, "
            "projects/rolling-universe/05). Reporter-category components are "
            "skipped (D4). Strategies with no universe mask are out of scope "
            "— Contract B static-pool strategies pool the full universe by "
            "design.\n\n"
            "**Message shapes.** `{cleared}` renders as the empty string on a "
            "never-masked path, and as ` (the mask is cleared upstream by "
            "'<step>')` when a fill between the mask and this step resurrected "
            "masked cells.\n\n"
            "**Remediation.** Move the mask application upstream of the "
            "pooling step (compute TS signals on the full pool — free warm "
            "signals for entrants — and mask at the TS→XS boundary), or "
            "remove the fill sitting between the mask and this step."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Apply the universe mask before '{component}' — e.g. insert "
            "ApplyUniverseMask(mask_slot={mask}) on this path. The mask must "
            "precede the first cross-sectional op on every path, with no fill "
            "in between (rolling-universe Contract C)."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="xs-before-universe-mask",  # STAGED_CHANGES (P2 (c) mint)
        recoverable=True,
    ),
    Rule(
        code="MASK_SLOT_NOT_WIRED",
        category=RuleCategory.SUSPICIOUS,
        summary="A pooling component reads a slot whose stored data never passes through the universe mask.",
        message_template=(
            "'{component}' pools over slot '{slot}' (declared population slot "
            "'{param}'), whose stored data does not pass through the universe "
            "mask ({mask})."
        ),
        template_params=("component", "slot", "param", "mask"),
        explain=(
            "The slot-sourced sibling of XS_BEFORE_UNIVERSE_MASK (rolling-"
            "universe Contract C): a component whose `population_scope` "
            "declares pooling SLOTS (`source: 'slot'`, or a D2 dual-source "
            "`source: 'input'` entry with `slots`) derives its population "
            "from slot data — VolumeWeightedMultiplier ranks by the OHLCV "
            "slot's volume, IDMPortfolioAggregator estimates correlations "
            "over its forecast/vol/OHLCV slots. In a universe-mask strategy, "
            "a declared pooling slot whose stored path never passed through "
            "the mask silently re-admits the full baked pool (the J-007 "
            "VolumeWeightedMultiplier residual, contracts §7).\n\n"
            "**How the verdict is computed.** The same masked-bit taint walk "
            "as XS_BEFORE_UNIVERSE_MASK records each Store's masked bit; for "
            "every declared population slot the wired slot name resolves "
            "through the component's slot-reference param (or the implicit "
            "slot-read name), and an unset stored bit fires — one issue per "
            "un-masked slot. Mask emitters (D3) and reporters (D4) are "
            "exempt; strategies with no universe mask are out of scope.\n\n"
            "**Honest scope note.** Some pooling slots (raw OHLCV) have no "
            "maskable dtype today — masking applies to signal-typed series. "
            "The rule is SUSPICIOUS (warning-class) precisely because the fix "
            "may be a mask-aware component variant rather than a re-wire; "
            "staged DORMANT→WARNING→PROMOTED on evidence."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Store a masked copy of '{slot}' — insert ApplyUniverseMask("
            "mask_slot={mask}) before its Store — and point '{param}' at the "
            "masked slot, or use a mask-aware variant of '{component}' if one "
            "exists."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="mask-slot-not-wired",  # STAGED_CHANGES (P2 (c) mint)
        recoverable=True,
    ),
    Rule(
        code="NONDENSE_TERMINAL_WEIGHTS",
        category=RuleCategory.SUSPICIOUS,
        summary="A universe-mask strategy's terminal weights can be NaN (no terminal densifier).",
        message_template=(
            "Terminal weights can be NaN: the universe mask ({mask}) "
            "introduces NaN cells and no densifier follows '{step}'. The "
            "backtest treats NaN as hold (frozen exits); live force-densifies "
            "by ffill (a masked-out exit is resurrected at its stale weight "
            "and re-traded)."
        ),
        template_params=("mask", "step"),
        explain=(
            "Rolling-universe Contract D (projects/rolling-universe/"
            "01-contracts.md §5): a universe-mask strategy must end with "
            "every terminal cell a real number or an explicit 0.0, never NaN. "
            "Both engine NaN semantics are landmines for masked books: the "
            "backtest kernel treats NaN as hold (frozen uncapped exits — the "
            "−100% wipeout mechanism), and the live executor force-densifies "
            "by ffill then fillna(0.0), so a NaN-masked exit is resurrected "
            "at its last active weight and actively re-traded. Dense loses no "
            "expressiveness: `on_change` rebalancing skips repeated weights, "
            "so dense + ffill + on_change ≡ NaN-hold where hold is wanted.\n\n"
            "**How the verdict is computed.** The masked-bit taint walk "
            "(see XS_BEFORE_UNIVERSE_MASK) IS the NaN-capability decision "
            "for mask-introduced NaN: the terminal step of a mask-using "
            "strategy whose walk bit is still set — no declared densifier "
            "(`domain_transfer` union-with-const, e.g. FillNaN) after the "
            "last mask application — can emit NaN, and fires when the "
            "terminal output type is WeightSeries (weights are what the "
            "engines consume; signal-terminal pipelines are out of scope). "
            "Engine NaN semantics themselves are UNCHANGED by design — "
            "NaN-hold stays a feature for other strategy styles; this rule "
            "only demands the rolling pattern densify its terminal.\n\n"
            "**promote_in_production.** Like UNRESOLVED_UNIVERSE / "
            "STALE_UNIVERSE, deploy/production validation promotes this to "
            "error once the staged change reaches PROMOTED — a live deploy "
            "with resurrectable exits is the exact incident class Contract D "
            "exists to prevent (until then the stage cap keeps it at "
            "warning)."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "End the pipeline with FillNaN(fill_value=0.0) after '{step}' so "
            "every terminal cell is a real number or an explicit 0.0 "
            "(rolling-universe Contract D)."
        ),
        applicability=Applicability.MACHINE_APPLICABLE,
        promote_in_production=True,
        staged_by="nondense-terminal-weights",  # STAGED_CHANGES (P2 (c) mint)
        recoverable=True,
    ),
)


#: The catalog. Keyed by code; validated at import (raises CatalogError).
RULES: dict[str, Rule] = validate_rules(_ALL_RULES)


# ═══════════════════════════════════════════════════════════════════════════════
# THE v1 STAGED-CHANGE ENTRIES (dsl-type-system spec 05 §2.4 — the complete
# initial set). Every entry carries target_milestone="M3" (R-10): each must
# have left its birth state by M3 or hold a founder-logged extension in its
# log. The three flow-shape entries flip independently (spec 03 §3.4); their
# "pre" states compose the m2-compat profile together with the DORMANT
# severity/firing-surface entries (01 §2.7). Schedule owner: spec 06 (dates,
# windows, HUMAN GATEs); this registry owns the machinery and the criteria
# vocabulary (§2.5: E-LIB0FP · E-CONF · E-CORPUS · E-WINDOW · E-PARITY ·
# E-GATE — error promotion of ANY rule requires all six; flip-on to WARNING
# requires E-LIB0FP + E-CONF + E-CORPUS + E-PARITY).
# ═══════════════════════════════════════════════════════════════════════════════

_ERROR_PROMOTION_EVIDENCE = (
    "E-LIB0FP",
    "E-CONF",
    "E-CORPUS",
    "E-WINDOW",
    "E-PARITY",
    "E-GATE",
)
_WARNING_FLIP_EVIDENCE = ("E-LIB0FP", "E-CONF", "E-CORPUS", "E-PARITY")
_FLOW_FLIP_EVIDENCE = ("E-CONF", "E-CORPUS", "E-PARITY")

_ALL_STAGED_CHANGES: tuple[StagedChange, ...] = (
    StagedChange(
        key="value-domain-rule",
        kind="severity-ramp",
        codes=("VALUE_DOMAIN_MISMATCH",),
        decision="GOAL objective (seed theorem)",
        stage=Stage.PROMOTED,  # Gate-2 promotion (T-MR-3): the seed theorem
        #                        goes BLOCKING (error) — GOAL done-when 2
        terminal_stage=Stage.PROMOTED,
        target_milestone="M3",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-23",
                "dormant",
                "born dormant at the M2 mint (T-M2a-3); WARNING flip-on at M3 "
                "skips INCUBATING — the dossier corpus sweep IS the completed "
                "bake-in measurement (spec 05 §2.4).",
            ),
            (
                "2026-07-24",
                "warning",
                "M3 flip-on (T-M3c-3): the seed theorem VALUE_DOMAIN_MISMATCH "
                "arms at WARNING (GOAL done-when 2 first half) — the "
                "intermediate window; terminal PROMOTED stays future "
                "(error-eligible under HUMAN GATE G3, §5.2). CURRENT_MILESTONE "
                "stays M2 (R-10 binds only birth-state entries; this entry has "
                "left birth). Pinned inventory (spec 06 §5.7): exactly 1 fire "
                "at the eventual error tier — prod draft "
                "str_01kthda1vjb082a83ackj7a6bv (0 deployments, "
                "already-runtime-crashing, dossier §4.1) — which is NOT in the "
                "library corpus, so the library re-measure fires 0 (18/18 "
                "unchanged, 0 verdict changes at warning tier). Conformance: "
                "the 3 VALUE_DOMAIN_MISMATCH own-reject fixtures only "
                "(reject_constant_slot_roundtrip, reject_hard_edge_direct, "
                "reject_interval_actual), no cross-fires. ALLOWLIST class-2 "
                "entry + schema-1 re-pins generated from the flip record "
                "(R-1.4).",
            ),
            (
                "2026-07-24",
                "promoted",
                "GATE-2 PROMOTION (T-MR-3): the seed theorem "
                "VALUE_DOMAIN_MISMATCH goes BLOCKING (error) — GOAL done-when 2 "
                "discharged in full. Founder ruling 2 (Gate-2 rulings "
                "2026-07-24, decisions.md): C3 → PROMOTED — the single TP is a "
                "prod DRAFT (0 deployments, not library); the user gets "
                "actionable feedback at edit/deploy time. The five distributed "
                "gates (G1–G4/G6) collapsed into the one Gate-2 sitting (06a "
                "§2); G2's bump ruling (founder ruling 1: NO BUMP, "
                "update-in-place) was delivered BEFORE this flip (R-11, §6 "
                "defensible-0). Realized inventory (spec 06 §5.7): exactly 1 "
                "verdict change whole-cohort — str_01kthda1vjb082a83ackj7a6bv "
                "valid→INVALID with VALUE_DOMAIN_MISMATCH at ERROR — and "
                "nothing else; library error-tier fires 0 (18/18 VALID); the "
                "ConstantForecast v1 refinement stays on v1 (no freeze), so the "
                "error reaches all 98 ConstantForecast users, recorded as a "
                "classifier metadata event (METADATA_EVENTS.md, ruling 1). The "
                "3 VALUE_DOMAIN_MISMATCH own-reject conformance fixtures fold "
                "expected_warnings → expected_errors (valid→false); both "
                "engines emit VDM at ERROR (parity). ALLOWLIST class-2 "
                "promotion entry + schema-1 re-pins generated from the flip "
                "record (R-1.4); 0 schema-0 edits. Per-rule severity revert to "
                "WARNING remains the rollback (spec 06 §5.6).",
            ),
        ),
    ),
    StagedChange(
        key="d2-refinement-unproven",
        kind="severity-ramp",
        codes=("VALUE_DOMAIN_UNPROVEN",),
        decision="D2",
        stage=Stage.WARNING,  # M3 flip-on (T-M3c-2): DORMANT->WARNING, now the
        #                       TERMINAL stage (permanent warning, ruling 3)
        terminal_stage=Stage.WARNING,  # Gate-2 (T-MR-3): PERMANENT WARNING —
        #                                founder ruling 3 (R-7 disposition,
        #                                Gate-2 rulings 2026-07-24). No PROMOTED
        #                                exists; VALUE_DOMAIN_UNPROVEN carries
        #                                severity_ceiling="warning" (invariant 4)
        target_milestone="M3",
        evidence=_WARNING_FLIP_EVIDENCE,  # no error-promotion path exists (§2.5:
        #                                   warning-terminal → the 4 flip-on
        #                                   criteria only, no window, no gate)
        log=(
            ("2026-07-23", "dormant", "born dormant at the M2 mint (T-M2a-3)."),
            (
                "2026-07-24",
                "warning",
                "M3 flip-on (T-M3c-2): VALUE_DOMAIN_UNPROVEN arms at WARNING — "
                "the intermediate warning-evidence window (D2; terminal PROMOTED "
                "stays future/error-eligible, HUMAN GATE G3 governs, reachable "
                "only if a re-measure shows no library fires — spec 06 §5.2). "
                "CURRENT_MILESTONE stays M2 (R-10 binds only birth-state "
                "entries; this entry has left birth). Pinned R-7 union inventory "
                "(spec 06 §5.7): strict-mode refined-input hard-set edges 0 "
                "(both dossier §4.4 edges were declaration artifacts removed by "
                "the REC input-relaxation R-2/R20) PLUS the top-into-demander "
                "slot-read fires (dossier §4.1) — library 1 "
                "(mean-reversion-hyperliquid; the 1-vs-2 candidate pair "
                "resolved to 1 at flip-on: the other library ThresholdCross "
                "user does not fire), staging 1 (already-invalid), prod 2 (both "
                "already-invalid). Conformance: the 3 VALUE_DOMAIN_UNPROVEN "
                "own-reject fixtures + the 2 VALUE_DOMAIN_MISMATCH cross-fires "
                "(accept_top_stays_runtime, reject_hard_edge_direct) — all 5 "
                "pre-declared under expected_at_terminal, surfaced one stage "
                "earlier as warnings. ALLOWLIST class-2 entry + schema-1 "
                "re-pins generated from the flip record (R-1.4).",
            ),
            (
                "2026-07-24",
                "warning",
                "GATE-2 DISPOSITION (T-MR-3): C2 fixed as a PERMANENT WARNING "
                "(founder ruling 3, Gate-2 rulings 2026-07-24, decisions.md; "
                "R-7 disposition). terminal_stage PROMOTED→WARNING and "
                "VALUE_DOMAIN_UNPROVEN gains severity_ceiling='warning' — the "
                "two-field encoding invariant 4 requires (spec 05 §2.3). This "
                "is NOT a revert (the emitting stage is unchanged at WARNING); "
                "it retires the error-eligibility HUMAN GATE G3 by founder "
                "ruling — R-7's honest trajectory (spec 06 §5.2): the expected "
                "inventory contains library fires (mean-reversion-hyperliquid), "
                "which would block E-LIB0FP at error tier indefinitely, so C2 "
                "stays warning on the merits. No conformance fixture severity "
                "moves (VDU already ships at WARNING); the stale VDU→error "
                "expected_at_terminal declarations in the two "
                "VALUE_DOMAIN_MISMATCH cross-fire fixtures are removed (VDU "
                "never reaches error now). No config-vector move (stage "
                "unchanged) — 0 trace re-pins for this entry.",
            ),
        ),
    ),
    StagedChange(
        key="soft-bounds-advisory",
        kind="severity-ramp",
        codes=("VALUE_BOUNDS_ADVISORY",),
        decision="D2 + D10-clarification",
        stage=Stage.WARNING,  # M3 flip-on (T-M3c-1): the first severity flip
        terminal_stage=Stage.WARNING,  # permanent — no further stage exists
        #                                (§2.3 invariant 4)
        target_milestone="M3",
        evidence=_WARNING_FLIP_EVIDENCE,
        log=(
            ("2026-07-23", "dormant", "born dormant at the M2 mint (T-M2a-3)."),
            (
                "2026-07-24",
                "warning",
                "M3 flip-on (T-M3c-1): VALUE_BOUNDS_ADVISORY arms at WARNING — "
                "the permanent soft-tier advisory (D2 + D10-clarification; the "
                "terminal stage, no PROMOTED exists). CURRENT_MILESTONE stays "
                "M2 (R-10 binds only birth-state entries; this entry has left "
                "birth). Ratified fire inventory (decisions.md 2026-07-24, "
                "orchestrator ruling; Gate-2 review flag): library "
                "ai-trading-bot-hyperliquid ×1 / mean-reversion-hyperliquid ×1 "
                "/ momentum-funding-hyperliquid ×2 + the two VALUE_BOUNDS "
                "own-reject conformance fixtures + the "
                "PARAM_TYPE_MISMATCH/accept_optional_numeric ForecastSeries "
                "soft-interval cross-fire. ALLOWLIST class-2 entry + schema-1 "
                "re-pins generated from the flip record (R-1.4).",
            ),
        ),
    ),
    StagedChange(
        key="d4-slot-sib-narrowing",
        kind="firing-surface",
        codes=("SLOT_TYPE_MISMATCH",),  # sib slot row ONLY (row-level
        #                                 staged_by on the A3 judgment row;
        #                                 historical sites stay error)
        decision="D4",
        stage=Stage.PROMOTED,  # Gate-2 promotion (T-MR-3): the sib slot row
        #                        goes BLOCKING (error) on newly-strict sites.
        #                        Historical SLOT_TYPE_MISMATCH sites already
        #                        error via the row-key-first resolution (spec 05
        #                        §2.2) — the promotion is transparent to them.
        terminal_stage=Stage.PROMOTED,
        target_milestone="M3",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            ("2026-07-23", "dormant", "born dormant at the M2 mint (T-M2a-3)."),
            (
                "2026-07-24",
                "warning",
                "M3 flip-on (T-M3c-4): the D4 sib slot row (J-SLOTREAD."
                "sib-narrowing) arms at WARNING — newly-strict slot sites emit "
                "SLOT_TYPE_MISMATCH at warning tier; already-strict historical "
                "sites stay at error via the row-key-first resolution (spec 05 "
                "§2.2). Terminal PROMOTED stays future (error-eligible under "
                "HUMAN GATE G4, §5.2). CURRENT_MILESTONE stays M2 (R-10 binds "
                "only birth-state entries; this entry has left birth). Fire "
                "inventory (spec 06 §5.7, dossier §4.2): 0 — 0 of 1,226 depend "
                "on the leniency; the flip-on enumeration over conformance + "
                "library realizes 0 fires and 0 verdict changes (probe: no "
                "subject exercises the newly-strict sib site). Empty-subjects "
                "ALLOWLIST class-2 entry (no schema-1 behavior divergence; all "
                "139 traces re-pin config-only) + schema-1 re-pins generated "
                "from the flip record (R-1.4).",
            ),
            (
                "2026-07-24",
                "promoted",
                "GATE-2 PROMOTION (T-MR-3): the D4 sib slot row goes BLOCKING "
                "(error) — founder ruling 4 (Gate-2 rulings 2026-07-24, "
                "decisions.md): C4 → PROMOTED (0 fires, future-guard). The "
                "distributed gate G4 collapsed into Gate-2 (06a §2). Realized "
                "inventory: 0 SLOT_TYPE_MISMATCH sib-narrowing fires across "
                "conformance + library + the whole cohort — 0 verdict changes, "
                "0 schema-1 behavior divergence; the newly-strict sib site is "
                "exercised by no subject, so no conformance fixture severity "
                "moves and no library/prod strategy degrades. Historical "
                "SLOT_TYPE_MISMATCH error sites are untouched (row-key-first, "
                "spec 05 §2.2). Empty-subjects ALLOWLIST class-2 promotion "
                "entry; all schema-1 traces re-pin config-only "
                "(d4-slot-sib-narrowing: warning→promoted), 0 schema-0 edits. "
                "Per-rule severity revert to WARNING remains the rollback (spec "
                "06 §5.6).",
            ),
        ),
    ),
    StagedChange(
        key="carrier-fallback-retirement",
        kind="firing-surface",
        codes=("TYPE_MISMATCH", "SLOT_TYPE_MISMATCH"),  # super-carrier rows only
        decision="R19 (R-1.3 foundation amendment)",
        stage=Stage.PROMOTED,  # Gate-2 promotion (T-MR-3): the super-carrier
        #                        rows go BLOCKING (error) on newly-firing
        #                        raw-carrier sites. Already-erroring sites already
        #                        error (row-key-first, spec 05 §2.2) — transparent
        #                        to them.
        terminal_stage=Stage.PROMOTED,
        target_milestone="M3",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            ("2026-07-23", "dormant", "born dormant at the M2 mint (T-M2a-3)."),
            (
                "2026-07-24",
                "warning",
                "M3 flip-on (T-M3c-5): the R19 super-carrier rows "
                "(J-STEP.carrier-fallback strict + the slot carrier row) arm at "
                "WARNING — newly-firing raw-carrier sites (a raw dict / "
                "pd.DataFrame actual crossing a NewType strict or slot demand, "
                "where oracle-1/3 previously accepted via carrier-fallback) emit "
                "TYPE_MISMATCH / SLOT_TYPE_MISMATCH at warning tier; "
                "already-erroring sites stay at error via the row-key-first "
                "resolution (spec 05 §2.2). Terminal PROMOTED stays future "
                "(error-eligible under HUMAN GATE G6, §5.2). CURRENT_MILESTONE "
                "stays M2 (R-10 binds only birth-state entries; this entry has "
                "left birth). The dossier did NOT measure this class (01 §2.4 "
                "R19 note): the flip-on enumeration REQUIRED before the stage "
                "move (spec 06 §5.7 C6) was run over conformance + library and "
                "realizes 0 fires, 0 verdict changes, 0 schema-1 behavior "
                "divergence — no conformance fixture and no library strategy "
                "exercises the raw-carrier-fallback acceptance site (the clean "
                "18/18 library corpus carries no raw dict/DataFrame → NewType "
                "crossing; the conformance corpus types each code via "
                "NewType-cast fixtures). This conformance+library enumeration is "
                "PINNED as the C6 gate baseline; the whole-cohort (staging+prod) "
                "C6 inventory is enumerated at the T-M3c-6 re-measure gate and "
                "gated in --expect from there (spec 06 §5.7). Empty-subjects "
                "ALLOWLIST class-2 entry; all 139 schema-1 traces re-pin "
                "config-only (carrier-fallback-retirement: dormant→warning), 0 "
                "schema-0 edits.",
            ),
            (
                "2026-07-24",
                "promoted",
                "GATE-2 PROMOTION (T-MR-3): the R19 super-carrier rows go "
                "BLOCKING (error) — founder ruling 5 (Gate-2 rulings "
                "2026-07-24, decisions.md): C6 → PROMOTED (0 fires). The "
                "distributed gate G6 collapsed into Gate-2 (06a §2); the "
                "flip-on fire inventory was enumerated and pinned at T-M3c-5 "
                "before the warning window (spec 06 §5.7 C6). Realized "
                "inventory: 0 TYPE_MISMATCH / SLOT_TYPE_MISMATCH carrier-"
                "fallback fires across conformance + library + the whole "
                "cohort — 0 verdict changes, 0 schema-1 behavior divergence; no "
                "raw dict/DataFrame → NewType crossing exists in the corpus, so "
                "no conformance fixture severity moves and no library/prod "
                "strategy degrades. Already-erroring sites are untouched "
                "(row-key-first, spec 05 §2.2). Empty-subjects ALLOWLIST "
                "class-2 promotion entry; all schema-1 traces re-pin "
                "config-only (carrier-fallback-retirement: warning→promoted), 0 "
                "schema-0 edits. Per-rule severity revert to WARNING remains the "
                "rollback (spec 06 §5.6).",
            ),
        ),
    ),
    StagedChange(
        key="extract-projection",
        kind="flow-shape",
        codes=("TYPE_MISMATCH", "COMPOSER_INPUT_TYPE_MISMATCH"),  # reach
        decision="01 §5.2 / R-1.3",
        stage="post",  # M3 flip-on (T-M3b-1): matched-field projection (01 §5.2)
        terminal_stage="post",  # matched-field projection at M3; expected
        #                         verdict deltas 0 (dossier §4.3); allowlist
        #                         entries generated from the flip record (R-1.4)
        target_milestone="M3",
        evidence=_FLOW_FLIP_EVIDENCE,
        log=(
            ("2026-07-23", "pre", "born at 'pre' (m2-compat profile, 01 §2.7)."),
            (
                "2026-07-24",
                "post",
                "M3 flip-on (T-M3b-1): matched-field projection (spec 01 §5.2); "
                "expected verdict deltas 0 (dossier §4.3); ALLOWLIST class-2 "
                "entries + schema-1 re-pins generated from the flip record (R-1.4).",
            ),
        ),
        states=("pre", "post"),
    ),
    StagedChange(
        key="scheme-synthesis",
        kind="flow-shape",
        codes=("TYPE_MISMATCH",),  # synthesis shape; base deltas enumerated in
        #                            spec 03 §3.4(2)
        decision="R-1.3",
        stage="post",  # M3 flip-on (T-M3b-2): J-INST/J-LIT active (01 §3.3/§7.1)
        terminal_stage="post",  # J-INST/J-LIT active at M3; ApplyMask-class
        #                         field_by_param + passthrough-five refined
        #                         inputs enumerated in spec 03 §3.4(2)
        target_milestone="M3",
        evidence=_FLOW_FLIP_EVIDENCE,
        log=(
            ("2026-07-23", "pre", "born at 'pre' (m2-compat profile, 01 §2.7)."),
            (
                "2026-07-24",
                "post",
                "M3 flip-on (T-M3b-2): J-INST/J-LIT activate — ApplyMask-class "
                "field_by_param refinement (ApplyMask/RegimeGate ⇒ the matched "
                "branch's base; seed ApplyMask ⇒ ForecastSeries, spec 01 §11) + "
                "refined inputs surviving the passthrough five (spec 03 §3.4(2)). "
                "Expected verdict deltas 0 (dossier §4.3); ALLOWLIST class-2 "
                "entries + schema-1 re-pins generated from the flip record (R-1.4).",
            ),
        ),
        states=("pre", "post"),
    ),
    StagedChange(
        key="composer-record-reach",
        kind="flow-shape",
        codes=("COMPOSER_KEY_MISMATCH", "EXTRACT_MISSING_KEY", "DICT_INPUT_EXPECTED"),
        decision="R-1.3 / findings-03 F9",
        stage="post",  # M3 flip-on (T-M3b-3): records reach wherever they flow
        terminal_stage="post",  # records reach wherever they flow at M3; both
        #                         §3.4(3) delta shapes enumerated at the M3
        #                         re-measure gate first (spec 05 §1.4)
        target_milestone="M3",
        evidence=_FLOW_FLIP_EVIDENCE,
        log=(
            ("2026-07-23", "pre", "born at 'pre' (m2-compat profile, 01 §2.7)."),
            (
                "2026-07-24",
                "post",
                "M3 flip-on (T-M3b-3): J-COMPOSER/J-PROJ premises read the record "
                "type in τ_cur wherever it flows (records survive Store/Load) — the "
                "adjacency-only look-ahead + stale prev_parallel source retire "
                "(spec 01 §5.2/§5.3; spec 03 §3.4(3)). The two enumerated reach "
                "deltas: (a) Parallel → Store → composer now checked "
                "(COMPOSER_INPUT_TYPE_MISMATCH); (b) the stale-pointer "
                "EXTRACT_MISSING_KEY false positive across an intervening Parallel "
                "now clears. Both pinned as conformance fixtures in both "
                "configurations. Library re-measure verdict deltas 0 "
                "(dossier §4.3); ALLOWLIST class-2 entries + schema-1 re-pins "
                "generated from the flip record (R-1.4).",
            ),
        ),
        states=("pre", "post"),
    ),
    # ── M2b engine-review mint (MAJOR-2), OUTSIDE the §2.4 ledger ──────────
    # The n≥1 Parallel-record precondition (spec 01 §5.1 m7). Born DORMANT for
    # trace parity (01 §2.7); WARNING flip-on rides the same M3 gate as the
    # §2.4 severity ramps. Founder-flagged as an out-of-ledger addition.
    StagedChange(
        key="empty-parallel-rule",
        kind="severity-ramp",
        codes=("EMPTY_PARALLEL",),
        decision="spec 01 §5.1 m7 (structural n≥1 precondition; M2b review MAJOR-2)",
        stage=Stage.WARNING,  # Gate-2 flip-on (T-MR-3): DORMANT→WARNING — leaves
        #                       its birth state, unblocking the R-10 milestone
        #                       token M2→M3 (all staged changes now off birth)
        terminal_stage=Stage.PROMOTED,
        target_milestone="M3",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-23",
                "dormant",
                "born dormant at the M2b engine-review mint (MAJOR-2): the "
                "n≥1 structural Parallel-record precondition (spec 01 §5.1 m7). "
                "Outside the original §2.4 ledger — founder-flagged. WARNING "
                "flip-on scheduled with the M3 ramp wave.",
            ),
            (
                "2026-07-24",
                "warning",
                "GATE-2 flip-on (T-MR-3): EMPTY_PARALLEL arms at WARNING — "
                "founder ruling 7 (Gate-2 rulings 2026-07-24, decisions.md): "
                "EMPTY_PARALLEL armed to WARNING on 0-fire evidence; this leaves "
                "the DORMANT birth state, which unblocks the R-10 milestone "
                "token M2→M3 (spec 05 §2.3 invariant 1d — an entry still at its "
                "DORMANT birth state at CURRENT_MILESTONE='M3' would need a "
                "founder-logged extension; arming it removes that block). The "
                "n≥1 Parallel-record precondition (spec 01 §5.1 m7) is "
                "Python-only (ts_mirrored=False — the GraphModel rejects empty "
                "Parallel blocks at deserialization, no browser emission to "
                "mirror). Fire inventory: the EMPTY_PARALLEL conformance reject "
                "fixture (reject_empty_parallel) folds expected_at_terminal → "
                "shipped expected_warnings (surfaces one stage earlier); "
                "0 library fires (the clean 18/18 corpus carries no empty "
                "Parallel block); the dormant-silence scan is now empty. "
                "Terminal PROMOTED stays future (error-eligible; the founder "
                "did not promote it this sitting). ALLOWLIST class-2 entry + "
                "schema-1 re-pins generated from the flip record (R-1.4); 0 "
                "schema-0 edits.",
            ),
        ),
    ),
    # ── M4 clock-family staged entries (dsl-multi-timeframe-clocks spec 02
    # §3.2, R-4) — the COMPLETE set minted by that project, all birth DORMANT
    # with target_milestone="M4". Pre-Gate-2 user-visible state is SILENCE
    # (R-3): no staged clock code emits at any severity in the shipped
    # configuration; measurement runs ONLY under staged_stage_override.
    # DORMANT→WARNING is itself a founder-approved Gate-2 act; no clock entry
    # leaves DORMANT before the projector pair is registered and the
    # library/conformance corpora re-measure clean (R2-I14 ordering, spec 02
    # §3.3 #2).
    StagedChange(
        key="clock-mismatch",
        kind="severity-ramp",
        codes=("CLOCK_MISMATCH", "TERMINAL_CLOCK_MISMATCH"),
        decision="dsl-mtf-clocks GOAL D2/D5 (K17 Gate-1; spec 02 §3.2)",
        stage=Stage.PROMOTED,  # GATE-2 flip (K18, T-M4f-5)
        terminal_stage=Stage.PROMOTED,
        target_milestone="M4",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-26",
                "dormant",
                "born dormant at the M4b mint (T-M4b-2): the headline clock "
                "ramp — MATCH cmix at composer/record + slot sites "
                "(CLOCK_MISMATCH) and the J-PIPE terminal premise "
                "(TERMINAL_CLOCK_MISMATCH), spec 01 §6. Flips only at Gate-2 "
                "with MEASURED fire counts (K10, D9); the ~339/321 figures "
                "remain predicted (clock_infer approximation) until the M4b/"
                "M4f measurement runs replace them.",
            ),
            (
                "2026-07-26",
                "promoted",
                "GATE-2 flip (K18, decisions.md; task T-M4f-5): straight to "
                "PROMOTED — the founder ruled no warning tier and no migration "
                "machinery ('no extra paths'). Evidence discharged: E-LIB0FP "
                "(18/18 library strategies, ZERO fires shipped AND armed), "
                "E-CONF (conformance accepts clean), E-CORPUS (the real "
                "validator under staged_stage_override over 1,222 stored "
                "strategies — 1,100 prod + 122 staging — zero shipped fires of "
                "any severity-ramp code on every tier that exists), E-WINDOW "
                "(waived by K18 — the founder declined the warning window), "
                "E-PARITY (Py↔TS green), E-GATE (K18). SAFETY GATE PASSED, "
                "verified against the DEPLOYED blob: zero LIVE/DEPLOYING/PAUSED "
                "deployments in the affected set, zero execution_sessions, zero "
                "recent signal_runs. MEASURED blast radius at this flip: "
                "staging 0 verdict changes; prod 336 valid→INVALID, all 336 "
                "DRAFT and private (0 public, 0 unlisted, 0 share links) — they "
                "surface as editor errors when opened and their authors apply "
                "the machine-applicable fix (K18). The ~339/321 predictions are "
                "now MEASURED and the clock_infer model is retired as an "
                "instrument (CLOCK_MISMATCH 339 subjects exact; ≥1-fire 358 "
                "exact).",
            ),
        ),
    ),
    StagedChange(
        key="clock-harmonic",
        kind="severity-ramp",
        codes=("CLOCK_NOT_HARMONIC",),
        decision="dsl-mtf-clocks GOAL D4 hole 1 (K17 Gate-1; spec 02 §3.2)",
        stage=Stage.PROMOTED,  # GATE-2 flip (K18, T-M4f-5)
        terminal_stage=Stage.PROMOTED,
        target_milestone="M4",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-26",
                "dormant",
                "born dormant at the M4b mint (T-M4b-2): the one "
                "verdict-changing TIGHTENING (divisibility over magnitude, "
                "spec 01 §2.3 Theorem 1 — the six magnitude-legal pairs "
                "2h→3h, 3h→4h, 3h→8h, 4h→6h, 6h→8h, 8h→12h become rejects). "
                "Measured cost of the tightening at spec time: 0 fires across "
                "18 library + conformance + 125 staging + 1,087 prod (R6 "
                "§3/§5). Flips independently of clock-mismatch (R2-I2 via "
                "R5), only at Gate-2.",
            ),
            (
                "2026-07-26",
                "promoted",
                "GATE-2 flip (K18, decisions.md; task T-M4f-5): straight to "
                "PROMOTED. The tightening's measured cost stayed ZERO through "
                "every re-measure: 0 fires across the 18 library strategies, "
                "the 151-fixture conformance corpus, 122 staging and 1,100 "
                "prod stored strategies — the six magnitude-legal, "
                "divisibility-illegal pairs (2h→3h, 3h→4h, 3h→8h, 4h→6h, "
                "6h→8h, 8h→12h) are authored by nobody. Flipped independently "
                "of clock-mismatch per R2-I2; the only inhabitants are the two "
                "CLOCK_NOT_HARMONIC conformance rejects.",
            ),
        ),
    ),
    StagedChange(
        key="clock-transform-rebase",
        kind="firing-surface",
        codes=(
            "UPSAMPLE_NOT_SUPPORTED",
            "BAR_OFFSET_NOT_MULTIPLE",
            "BAR_OFFSET_TOO_LARGE",
            "BAR_OFFSET_AT_SAME_TF",
            "RESAMPLER_NOOP",
        ),
        decision="dsl-mtf-clocks spec 02 §2.2 re-base (K17 Gate-1)",
        stage=Stage.PROMOTED,  # GATE-2 flip (K18, T-M4f-5)
        terminal_stage=Stage.PROMOTED,
        target_milestone="M4",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-26",
                "dormant",
                "born dormant at the M4b mint (T-M4b-2): the five resampler-"
                "family codes gain pass-6 judgment rows at EVERY transform "
                "site (row-level staged_by on the clock rows only — the "
                "historical pass-9 Globals-path surface stays PROMOTED "
                "untouched; precedent: carrier-fallback-retirement). While "
                "both emitters coexist the staged rows carry the spec 02 §3.4 "
                "three-conjunct suppression predicate (one emission per "
                "defect on the exact Globals pair; divergent conditions are "
                "real defects and stand). The promotion commit removes the "
                "pass-9 emitter block, keeps the Globals-level κ_exec WF "
                "keeper row, tombstones INVALID_RESAMPLER_CONFIG, carries "
                "the validation_shared.py general-Theorem-1-form change "
                "(spec 03 §10 row 21, R-7 — the chained-offset intended "
                "verdict change), and lands the spec 02 §2.2 "
                "UPSAMPLE_NOT_SUPPORTED template realignment (deferred from "
                "mint: TS renders templates while the Python pass-9 site "
                "overrides with the shared ValueError text — realigning at "
                "mint would break Python↔TS message parity; recorded "
                "T-M4b-2).",
            ),
            (
                "2026-07-26",
                "promoted",
                "GATE-2 flip (K18, decisions.md; task T-M4f-5) — the spec 02 "
                "§3.4 promotion checklist executed in ONE commit: (1) the "
                "pass-9 resampler-family emitter block and the three-conjunct "
                "suppression predicate are REMOVED, so the pass-6 rows are the "
                "sole transform-site emitter at every site; the R-16 "
                "Globals-level κ_exec WF keeper row (J-GLOBALS.exec-wf) STAYS, "
                "closing the flip-ordering window in which a today-PROMOTED "
                "error class would otherwise validate clean; (2) "
                "INVALID_RESAMPLER_CONFIG is tombstoned status='reserved' (its "
                "str-dispatch fallback died with the emitter) with the "
                "BREAKING_CHANGES active→reserved acknowledgment; (3) "
                "validation_shared.validate_resample_config's per-hop offset "
                "arithmetic moves to spec 01 §5.2's general Theorem-1 form "
                "(off_min ≡ o_in mod p_in) so runtime and validation never "
                "disagree on a chained-offset shape — an INTENDED verdict "
                "change (R-7), the one legalization in this flip, enumerated "
                "in the D5/D6 ledgers with prior_semantics=per_hop_reject; (4) "
                "UPSAMPLE_NOT_SUPPORTED's K15 template realignment lands now "
                "that both engines render the template (the message_override "
                "site is gone), so Py↔TS message parity is preserved through "
                "the swap; (5) the three dedupe fixtures re-pin the "
                "post-removal single-emitter behavior. Measured: ZERO library "
                "fires and no shipped verdict change outside the enumerated "
                "conformance fixtures.",
            ),
        ),
    ),
    StagedChange(
        key="tombstoned-option-pinned",
        kind="severity-ramp",
        codes=("TOMBSTONED_OPTION_PINNED",),
        decision="dsl-mtf-clocks spec 01 §8.4 item 2 / spec 02 §2.3 (K17 Gate-1)",
        stage=Stage.WARNING,  # GATE-2 flip (K18, T-M4f-5) — its OWN terminal
        terminal_stage=Stage.WARNING,
        target_milestone="M4",
        evidence=_WARNING_FLIP_EVIDENCE,
        log=(
            (
                "2026-07-26",
                "dormant",
                "born dormant at the M4b mint (T-M4b-2): the v1-pin "
                "CORRECTNESS diagnostic for constrain-at-v2 + tombstone "
                "components. Terminal is a PERMANENT WARNING "
                "(severity_ceiling='warning', invariant 4) — the blob/ABI "
                "invariant promises pinned instances keep loading and "
                "running; founder may elect full error instead (spec 02 §15 "
                "Q1). Firing surface (the generated tombstoned-options "
                "table) arrives with the M4c v2 registrations; pinned "
                "population re-measured at build (predicted until then).",
            ),
            (
                "2026-07-26",
                "warning",
                "GATE-2 flip (K18, decisions.md; task T-M4f-5): DORMANT→"
                "WARNING, which IS this key's declared terminal — the "
                "severity_ceiling='warning' permanence (catalog invariant 4) "
                "is the whole point of the code and does not relax at the "
                "flip. K18 sends every staged key straight to ITS OWN "
                "terminal; for this one that terminal is a permanent warning, "
                "because the diagnostic exists to describe pinned blobs that "
                "must keep loading and running (spec 01 §8.4's 44-blob-pin "
                "invariant). A blocking error here would contradict the "
                "blob/ABI promise it is reporting on. Measurement confirmed "
                "the choice is immaterial to counts either way: 0 shipped "
                "fires across 1,222 stored strategies and 0 across the 18 "
                "library strategies; the only inhabitants are the four "
                "conformance fixtures that pin a v1 lock on a tombstoned "
                "option (AlignToFrequency(method='bfill') and the off-alphabet "
                "SignalResampler(target_timeframe='2d') pair).",
            ),
        ),
    ),
    StagedChange(
        key="clock-projector",
        kind="severity-ramp",
        codes=("PROJECT_WRONG_DIRECTION", "PROJECT_INPUT_UNCLOCKED"),
        decision="dsl-mtf-clocks 00-resolutions R-4 (K17 Gate-1; spec 02 §3.2)",
        stage=Stage.PROMOTED,  # GATE-2 flip (K18, T-M4f-5)
        terminal_stage=Stage.PROMOTED,
        target_milestone="M4",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-07-26",
                "dormant",
                "born dormant at the M4b mint (T-M4b-2): the projector-code "
                "key minted per R-4 — review falsified the 'structurally "
                "zero, entailed' unstaged premise: spec 01 §8.1 classifies "
                "SignalTimeframeConverter and AlignToFrequency as op "
                "'project', so both codes are reachable through LEGACY "
                "components in existing strategies and blobs; the pinned "
                "population is predicted until re-measured at build. Same "
                "ramp as the other clock keys; flips only at Gate-2.",
            ),
            (
                "2026-07-26",
                "promoted",
                "GATE-2 flip (K18, decisions.md; task T-M4f-5): straight to "
                "PROMOTED. R-4's reachability premise was re-measured, not "
                "assumed: the legacy project-classified components "
                "(SignalTimeframeConverter, AlignToFrequency) reach both codes "
                "in principle, and the measured pinned population across "
                "1,100 prod + 122 staging stored strategies is ZERO fires "
                "shipped and zero at terminal — so this key's flip is a "
                "no-op on every real artifact and arms the pair's "
                "authoring-time rejection (GOAL D3) going forward. The M4d "
                "R-23 Any-frontier fix is what makes these codes reachable at "
                "all; without it the rule would have shipped with no firing "
                "surface.",
            ),
        ),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Rolling-universe P2 group (c) entries (2026-08-02) — Contract C's
    # "mask placement enforced, not remembered" (projects/rolling-universe/
    # 01-contracts.md §4; 02-keel-port-design.md §(c)). All three born
    # DORMANT; ladder DORMANT → WARNING → PROMOTED on the standard evidence.
    # ═══════════════════════════════════════════════════════════════════════
    StagedChange(
        key="xs-before-universe-mask",
        kind="severity-ramp",
        codes=("XS_BEFORE_UNIVERSE_MASK",),
        decision="rolling-universe Contract C (01-contracts.md §4, founder-approved 2026-08-01)",
        stage=Stage.DORMANT,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-08-02",
                "dormant",
                "born dormant at the P2 (c) mint: the masked-bit taint walk "
                "over the group-(b) population_scope metadata (input-pooling "
                "ops on un-masked paths in mask-using strategies). D3 "
                "emitter exemption via the universe_filter-category "
                "discriminator; D4 reporter skip. Fires only in strategies "
                "that use a universe mask, so the shipped corpus and the 18 "
                "library strategies are structurally silent.",
            ),
        ),
    ),
    StagedChange(
        key="mask-slot-not-wired",
        kind="severity-ramp",
        codes=("MASK_SLOT_NOT_WIRED",),
        decision="rolling-universe Contract C (01-contracts.md §4, founder-approved 2026-08-01)",
        stage=Stage.DORMANT,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-08-02",
                "dormant",
                "born dormant at the P2 (c) mint: the slot-sourced sibling — "
                "population_universe components with declared pooling slots "
                "(source 'slot', or D2 dual-source input+slots) whose wired "
                "slot's stored path never passes through the mask (the J-007 "
                "VolumeWeightedMultiplier residual, contracts §7). Expected "
                "to fire on the canonical rolling pattern's IDM/VWM wiring "
                "at flip-on — that inventory is the evidence-window "
                "measurement, not a false positive.",
            ),
        ),
    ),
    StagedChange(
        key="nondense-terminal-weights",
        kind="severity-ramp",
        codes=("NONDENSE_TERMINAL_WEIGHTS",),
        decision="rolling-universe Contract D (01-contracts.md §5, founder-approved 2026-08-01)",
        stage=Stage.DORMANT,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-08-02",
                "dormant",
                "born dormant at the P2 (c) mint: a mask-using strategy "
                "whose terminal WeightSeries can still be NaN (walk bit set "
                "at the terminal, no declared densifier). Carries "
                "promote_in_production=True — at PROMOTED, deploy-time "
                "validation blocks (live ffill resurrects NaN-masked exits "
                "at stale weights, contracts §5/§7); the stage cap keeps it "
                "warning until then.",
            ),
        ),
    ),
)

#: THE staging namespace (R-5). Keyed by id; validated at import against
#: RULES (raises CatalogError). The import-time call has no git, so the
#: §2.3.1d merge-base baseline rides catalog_test.py's merge-base test — the
#: structural half is enforced here unconditionally.
STAGED_CHANGES: dict[str, StagedChange] = validate_staged_changes(_ALL_STAGED_CHANGES, RULES)


@contextmanager
def staged_stage_override(key: str, stage: Stage | str):
    """TEST-ONLY: force a staged change's current stage for one block.

    The spec 05 §5.3 harness seam (the switchability 01 S1-AC10 guarantees):
    both parity harnesses run staged rules at their TERMINAL stage through
    this override so reject/accept fixtures stay meaningful at every shipped
    stage, and the §5.3 revert drill exercises the one-line flip without
    touching the shipped registry. Deliberately bypasses the log-coherence
    validation — the override is ephemeral test state, never a shipped stage
    move (those edit ``_ALL_STAGED_CHANGES`` + append a log line + regen).
    Never call this from production code paths.
    """
    original = STAGED_CHANGES[key]
    STAGED_CHANGES[key] = replace(original, stage=stage)
    try:
        yield
    finally:
        STAGED_CHANGES[key] = original


__all__ = [
    "Applicability",
    "CatalogError",
    "CURRENT_MILESTONE",
    "EngineDivergence",
    "PARITY_BOUNDARY",
    "RULES",
    "Rule",
    "RuleCategory",
    "SEVERITY_BY_CATEGORY",
    "STAGED_CHANGES",
    "Stage",
    "StagedChange",
    "Surface",
    "parity_contract_to_jsonable",
    "rules_to_jsonable",
    "severity_for",
    "staged_changes_to_jsonable",
    "staged_stage_override",
    "validate_rules",
    "validate_staged_changes",
]
