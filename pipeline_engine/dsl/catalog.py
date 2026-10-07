"""Rule catalog — the single registry of validation issue codes (spec 02, T-1/T-2).

One frozen :class:`Rule` per issue code minted anywhere in the platform:

- the 49 write-time codes emitted by the Python DSL validator
  (``pipeline_engine.dsl.validator`` — static ``emit(...)`` sites + 6 dynamic
  resampler codes assigned by the ValueError→code dispatch),
- the 7 retired Layer C runtime-only codes (``PipelineValidator`` +
  ``LookaheadValidator``, all ``status="reserved"`` tombstones since the
  2026-08-26 Layer C retirement, Q-0689),
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

from timeframes import token_for_minutes


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

#: THE engine-divergence set (spec 04 §6). EMPTY since 2026-08-26: its one
#: seed entry, ``ts-pass7-skip`` (the editor deliberately never ran pass-7
#: phase ordering), retired WITH the rule itself — PHASE_ORDER_VIOLATION is
#: now a reserved tombstone (founder ruling 2026-08-26 on the Q-0685 census),
#: so the two engines genuinely emit the same code set and no divergence
#: needs declaring. New entries require the spec 04 §6 coherence rules
#: (codes must resolve with ``ts_mirrored=False``).
_ALL_ENGINE_DIVERGENCES: tuple[EngineDivergence, ...] = ()


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

_TS_GATE_ONLY_REASON = (
    "Gate-minted code: produced by a keel-api request gate, not by any "
    "validator pass; there is no editor-side emission to mirror."
)
_TS_PARSE_ABSENT_REASON = (
    "Parse-tier code: the browser editor persists a GraphModel and never "
    "parses DSL text — the text path is keel-api's and the SDK's. Its TS "
    "consumers RENDER these codes (they arrive on the wire) but no TS "
    "validator pass can mint one."
)
_TS_ENGINE_BOUNDARY_REASON = (
    "Engine-boundary structured code: minted by a StructuredError subclass "
    "(pipeline_engine.exceptions) at a server-side compile/serialize/load/"
    "verify boundary the browser editor never crosses."
)


# ═══════════════════════════════════════════════════════════════════════════════
# THE POSITION LAYER (position-layer specs 01-04, Q-2448)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Every code the binding facet mints (spec 03 §2.D), the live look-back
# refusal (spec 02-R9), the upgrade offer (spec 04-R9) and the D-42 deploy
# refusal: the 22 codes of reconciliation X-28. (spec 02's
# LIVE_WINDOW_SHORTER_THAN_TRADES is a run-summary note, not a catalog code.) All are born UNSTAGED: each
# needs a position component (TradeManager, a reader, an action, Exposure()),
# so none can fire on any strategy that predates the position layer — the
# COMPONENT_NOT_RUNNABLE precedent. Messages and fixes are the binding
# module's SHAPES (``pipeline_engine.binding``), rendered into ``{detail}`` and
# ``{fix}``, so the static walk, the TS engine and the runtime lift say the
# same words. Lane L1's components are registered, so each code's coverage
# is its conformance/<CODE>/ reject + accept fixtures (written from
# dsl/fixtures/position_stub_corpus.py, the cases' source); the scope
# machine's unit table (binding_test.py) stays as the row-level proof. The
# pre-landing fixture_waiver lines went with the move (Q-2448 integration).


def _binding_rule(
    code: str,
    category: RuleCategory,
    summary: str,
    explain: str,
    *,
    runtime: bool,
    ceiling: str = "",
) -> Rule:
    surfaces = _PY_TS_RT if runtime else _PY_TS
    return Rule(
        code=code,
        category=category,
        summary=summary,
        message_template="{detail}",
        template_params=("detail", "fix"),
        explain=explain,
        passes=("6",),
        surfaces=surfaces,
        ts_mirrored=True,
        suggestion_template="{fix}",
        applicability=Applicability.MAYBE_INCORRECT,
        severity_ceiling=ceiling,
    )


_POSITION_LAYER_RULES: tuple[Rule, ...] = (
    _binding_rule(
        "POSITION_ACTION_NEEDS_MASK",
        RuleCategory.CORRECTNESS,
        "A position action (Exit, Reduce, ScaleIn, AllowEntry) received something other than a 0/1 mask.",
        "An action fires where its mask is exactly 1. It refuses the Position "
        "itself or a record of branches (start the rule with a reader, a "
        "filter, then the action) and a series whose static domain holds a "
        "value outside {0, 1}. A -1/+1 direction gets its own wording "
        "(D-39.7): Exit fires on 1 only, so shorts would never exit. A ⊤ "
        "domain passes statically; the runtime lift asserts the values are "
        "in {0, 1, NaN}. Actions declare no input_domain, so "
        "VALUE_DOMAIN_MISMATCH never double-fires (spec 03-R20).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_REQUIRED",
        RuleCategory.CORRECTNESS,
        "A trade reader, trade operator, action, Exposure() or RiskSizer runs with no open TradeManager.",
        "Readers, SinceEntry, actions, Exposure() and RiskSizer read a trade, "
        "so they need a Position: they sit between TradeManager(...) and "
        "Exposure(). Placing one after the sizer (or after Exposure()) is "
        "Q-2448's shape as a type error; the message then names the step "
        "that already closed the scope (spec 03-R21). The step's output "
        "carries the recovery lineage, so the mistake fires once.",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_LINEAGE_MISMATCH",
        RuleCategory.CORRECTNESS,
        "Values of two TradeManagers meet.",
        "Every Position and every value read from it belongs to the "
        "TradeManager that minted it (its lineage). A rule of one "
        "TradeManager cannot act on another's trade values, a reader cannot "
        "read another's Position, and a Load of another's trade value is "
        "refused (D-15, review B1 E1/E2). The message names both "
        "TradeManagers by location (spec 03-R22).",
        runtime=True,
    ),
    _binding_rule(
        "TRADE_SERIES_LEAK",
        RuleCategory.CORRECTNESS,
        "A trade value is used where a market value is required.",
        "Trade values are bound to their TradeManager's trade windows and "
        "exist only inside its rules. They may not feed a TradeManager's "
        "entries or prices, a reader's or sizer's market slot, be loaded "
        "outside the TradeManager's scope, or end the pipeline (spec 03-R23, "
        "option A §1.5).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_BRANCH_MIXED",
        RuleCategory.CORRECTNESS,
        "Some branches of a rule stage end in an action and some do not.",
        "Inside a TradeManager's scope, a Parallel whose branches end in "
        "actions is one rule stage; a branch that does not end in an action "
        "is reported once, on that branch (D-39.7, gut check P5). A "
        "between-trade branch is told to end with AllowEntry(); any other "
        "with Exit(), Reduce() or ScaleIn() (spec 03-R24).",
        runtime=True,
    ),
    _binding_rule(
        "TRADE_SCOPE_MIX",
        RuleCategory.CORRECTNESS,
        "Trade and between-trade values are combined, or an action reads the wrong scope.",
        "Trade readers (TradeReturn, BarsHeld, ...) describe an open trade; "
        "between-trade readers (BarsSinceExit, LastTradeReturn, ...) describe "
        "the bars between trades. One rule reads one of them: Exit, Reduce "
        "and ScaleIn act during a trade, AllowEntry gates entries between "
        "trades, and SinceEntry aggregates over an open trade (spec 03-R25).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_NEEDS_READER",
        RuleCategory.CORRECTNESS,
        "A rule-building component received the Position itself.",
        "A rule starts with a reader (TradeReturn(), BarsHeld(), ...) or with "
        "Load('<slot>'), never with the Position: a filter, transform or "
        "composer cannot read it. Storing the Position is refused for the "
        "same reason (store a reader's value). The upgrade's most common trap "
        "(review B1 rule 3, gut check P2; spec 03-R26).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_NEEDS_EXPOSURE",
        RuleCategory.CORRECTNESS,
        "A Position is never turned into exposure.",
        "Exposure() is the only way a Position becomes exposure (D-39.1); "
        "RiskSizer sizes it directly. A Position reaching a sizer, a forecast "
        "step or a combiner, a branch that ends with its own TradeManager "
        "open, a second TradeManager while one is open, or a pipeline that "
        "ends with one open is refused. At the terminal it replaces "
        "TERMINAL_NOT_WEIGHTS (single fire; spec 03-R27).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_FEEDBACK",
        RuleCategory.CORRECTNESS,
        "A rule acts on its own TradeManager's realised exposure.",
        "Exposure() realises the plan once, jointly; a rule of the same "
        "TradeManager cannot read the exposure it decides (review B1 rule 4). "
        "Read the trade with a reader instead (spec 03-R28).",
        runtime=True,
    ),
    _binding_rule(
        "POSITION_STATE_NOT_UPSTREAM",
        RuleCategory.CORRECTNESS,
        "A state reader reads a leg no earlier stage can produce.",
        "SoldFraction() needs a Reduce() rule and AddCount() a ScaleIn() rule "
        "in an EARLIER stage of the same Position: a reader sees the rules of "
        "the stages above it (its anchor), never its own stage (review M1, "
        "spec 03-R29).",
        runtime=True,
    ),
    _binding_rule(
        "READER_WINDOW_INVALID",
        RuleCategory.CORRECTNESS,
        "A windowed reader's or factory's window is not valid on this strategy's clock.",
        "A window is a timeframe token ("
        + ", ".join(f"'{token_for_minutes(m)}'" for m in (30, 240, 1440))
        + ", '1w') that is a whole number of the strategy's bars; "
        "anchor='calendar' takes only the reader's calendar windows "
        f"('{token_for_minutes(1440)}', '1w', D-24); and a window "
        "needs Globals(target_timeframe=...) to be declared (D-45). Static "
        "only: the runtime refuses the same windows with a ValueError at "
        "construction or first run (spec 03-R30).",
        runtime=False,
    ),
    _binding_rule(
        "WINDOW_EXCEEDS_LIVE_LOOKBACK",
        RuleCategory.CORRECTNESS,
        "A windowed rule's window is at or above the live look-back (365 days).",
        "Live re-evaluates a strategy over its last 365 days at every bar, so "
        "a window of 365 days or more asks a question live can never answer "
        "and the backtest and live would differ (spec 02-R9, D-46). Use a "
        "window under 365 days.",
        runtime=False,
    ),
    _binding_rule(
        "TRADE_OP_NOT_TRADE_SAFE",
        RuleCategory.CORRECTNESS,
        "A component that is not certified trade-safe runs on trade values.",
        "Only components certified to run on a trade window (prefix-stable, "
        "column-independent, index-preserving, no context reads; verified in "
        "CI by libs/components/trade_safety_test.py) may take a trade value. "
        "Certification is fail-closed and keyed by (name, pinned version, "
        "parameter values) (D-16): an uncertified component, a value outside "
        "the certified narrowing, or an older uncertified pin of a certified "
        "component is refused, each with its own wording. Compute the value "
        "on the market before the TradeManager and Load it instead "
        "(spec 03-R31).",
        runtime=True,
    ),
    _binding_rule(
        "SIZER_ERASES_SIZE",
        RuleCategory.CORRECTNESS,
        "A sizer that sizes by sign receives an exposure with partial or added units.",
        "A sizer version without preserves_size sizes by sign, so a Reduce() "
        "or ScaleIn() rule would do nothing. The message names the newest "
        "version of the sizer that keeps units (review M2, spec 03-R32).",
        runtime=True,
    ),
    _binding_rule(
        "TRADE_RULE_WARMUP",
        RuleCategory.HYGIENE,
        "A rule's window needs more bars after entry than the trade can last.",
        "A rule's mask has a warm-up lower bound W (the sum of its steps' "
        "declared warm-ups). When a full-exit hold rule of the same Position "
        "closes every trade by bar H and W >= H, the rule can never fire "
        "(PLAN A, review M12.1; spec 03-R33).",
        runtime=False,
    ),
    _binding_rule(
        "REENTRY_ANY_BAR_NO_GATE",
        RuleCategory.SUSPICIOUS,
        "TradeManager(reentry='any_bar') has no AllowEntry() rule.",
        "reentry='any_bar' drops the wait-for-reset default, so with no "
        "AllowEntry() gate the TradeManager re-enters on any bar its entry "
        "signal is on, right after every exit (D-26; spec 03-R34).",
        runtime=False,
    ),
    _binding_rule(
        "RETURN_THRESHOLD_SCALE",
        RuleCategory.SUSPICIOUS,
        "A fraction (a trade return) is compared with a threshold of magnitude 1 or more.",
        "TradeReturn() and the other fraction readers are fractions: 0.05 is "
        "5%. A threshold of -5 means -500%, which a stop never reaches "
        "(D-39.7, gut check P3). Read from the comparator's declared "
        "`compares` and the reader's declared `scale`, never from a name "
        "(spec 03-R35).",
        runtime=False,
    ),
    _binding_rule(
        "MARKET_EXPANDING_IN_POSITION",
        RuleCategory.SUSPICIOUS,
        "An expanding (non-causal) market calculation sits inside a trade rule.",
        "A market calculation inside a rule keeps its full-history meaning "
        "(D-33): a cumulative sum counts from the start of the data, not "
        "from the entry. For 'since entry' use SinceEntry() (PLAN A, review "
        "M12.3; spec 03-R36).",
        runtime=False,
    ),
    _binding_rule(
        "MAX_UNITS_STATED",
        RuleCategory.HYGIENE,
        "The TradeManager can hold more than one unit per trade.",
        "max_units is derived from the ScaleIn rules (1 + every add they can "
        "make) unless set (D-39.3). Above 1 it is stated leverage: exposure "
        "up to that many times the sizer's weight per position. The number "
        "is positions.limits' effective_max_units, the function the engine "
        "caps with, so the statement and the cap cannot drift (spec 03-R37). "
        "Info only.",
        runtime=False,
        ceiling="info",
    ),
    _binding_rule(
        "SCALE_IN_NO_ROOM",
        RuleCategory.HYGIENE,
        "An explicit max_units leaves a ScaleIn rule no room to add.",
        "With max_units set explicitly below 1 + the rule's units and no "
        "Reduce() rule to make room, the ScaleIn rule can never add (gut "
        "check X5b, P8; spec 03-R38, reconciliation X-11).",
        runtime=False,
    ),
    Rule(
        code="POSITION_UPGRADE_AVAILABLE",
        category=RuleCategory.SUSPICIOUS,
        summary="Deprecated position components can be upgraded to TradeManager.",
        message_template=(
            "'{manager}' and its exits use deprecated position components "
            "({components}){known_issue_note}. A {mode} upgrade to TradeManager is available."
        ),
        template_params=("manager", "components", "known_issue_note", "mode"),
        explain=(
            "One issue per position group (spec 04-R9, D-19/D-29): a deprecated "
            "position manager (PositionStateMachine, TradeLevelRiskExit, "
            "ScalingPositionManager) with every deprecated exit that reaches it "
            "and every deprecated sizer downstream of it, or a deprecated exit "
            "or sizer that reaches no deprecated manager. Every deprecated "
            "instance belongs to exactly one issue; DEPRECATED_COMPONENT still "
            "fires per instance. The upgrade planner (dsl/upgrade.py) classifies "
            "the group as mechanical (machine-applicable: the issue carries the "
            "rewritten source), assisted (questions to answer) or manual. The "
            "suggestion names no tool; each surface adds its own."
        ),
        passes=("4u",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Needs the unexpanded strategy and the dsl/upgrade.py planner, which "
            "the browser validator does not carry. The editor gets it through "
            "/lock/status."
        ),
        suggestion_template=(
            "Apply this issue's suggested edit. It keeps your rule names and comments. "
            "Save it as a new version, then compare the two versions' backtests."
        ),
        applicability=Applicability.MACHINE_APPLICABLE,
        # Corpus: conformance/POSITION_UPGRADE_AVAILABLE/ (spec 04 A10): the
        # pre-landing fixture_waiver went once pass 4u emitted and the flip
        # made the nine deprecated.
    ),
    Rule(
        code="KNOWN_ISSUE_UPGRADE_REQUIRED",
        category=RuleCategory.CORRECTNESS,
        summary="Deploy, update or resume refused: the strategy pins a component with a known issue.",
        message_template=(
            "{action} blocked: this strategy pins components with a known issue "
            "({known_issue}: {summary}). Upgrade this strategy first."
        ),
        template_params=("action", "known_issue", "summary"),
        explain=(
            "D-42 (founder): deploying, updating or resuming a strategy that "
            "pins a component with a known issue (the five Q-2448 exits and the "
            "deprecated StopDistanceRiskSizer) is refused, with no acknowledgement and no "
            "override. The upgrade is one step: the web button, `keel "
            "strategy upgrade`, or the agent (keel_strategy_upgrade). "
            "Deprecated components WITHOUT a known issue (PositionStateMachine, "
            "TradeLevelRiskExit, ScalingPositionManager) deploy normally. "
            "Minted by keel-api's deploy/update/resume gate (live.py, spec "
            "04); it replaces spec 04's acknowledgement codes, which are "
            "never minted."
        ),
        passes=(),
        surfaces=_GATE_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "A keel-api request gate on deploy/update/resume; the editor "
            "receives it on the wire and renders it, it never mints it."
        ),
        suggestion_template=(
            "Upgrade the strategy (keel strategy upgrade, keel_strategy_upgrade, "
            "or the Upgrade button), then try again."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
    ),
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
            "threshold; otherwise it points at searching the component catalog, "
            "naming no one surface's tool."
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
            "Component '{name}' is deprecated and may be removed in a future version.{known_issue_note}"
        ),
        template_params=("name", "alternative", "known_issue_note"),
        explain=(
            "The component still resolves and runs, but its registry status is "
            "'deprecated'. Prefer the supported alternative before the version "
            "is phased out (see the deprecation-window policy, decision D2). "
            "When the deprecation names a single successor — the class's "
            '`replacement = "<ComponentName>"`, carried in the generated '
            "registry metadata — the suggestion names it (e.g. "
            "RollingNotionalProxyMask -> RollingDollarVolumeMask, dollar-volume "
            "DV6a); otherwise it says 'a supported alternative'. {alternative} "
            "is the quoted successor name or that phrase."
        ),
        passes=("4",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Consider replacing '{name}' with {alternative}.",
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
            "The literal value's structure is not acceptable for the "
            "registry-declared parameter type, checked over the full "
            "declared-type grammar (spec 08): Literal shape (values are "
            "PARAM_INVALID_OPTION's job), Optional/Union arms, list/dict/"
            "tuple containers incl. element types, plain classes. int "
            "satisfies float params and vice versa; bool never satisfies "
            "numeric params. Both languages check the same serialized "
            "descriptor (registry param type_structure)."
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
        summary="Parameter type could not be checked (outside the structural grammar).",
        message_template=(
            "Cannot validate type of parameter '{param}' of '{component}': "
            "complex type {type} is not isinstance-checkable."
        ),
        template_params=("param", "component", "type"),
        explain=(
            "TRIPWIRE ONLY (spec 08, Q-0514): param values are structurally "
            "checked against the declared-type grammar derived from the type "
            "authority (param_type_structure — Literal/Optional/Union/"
            "list/dict/tuple/Sequence/class), so this fires only for a future "
            "exotic declared type outside that grammar. Structurally "
            "unreachable for every current registry param (the deep-sweep "
            "test in validator_test.py proves zero opaque descriptors). "
            "severity_override='info' preserved: a notice that a check did "
            "NOT run, not a suspected authoring mistake."
        ),
        passes=("5",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "TS pass-5 now checks the same structural grammar from the "
            "serialized type_structure descriptor (rules/params.ts); only "
            "this tripwire notice for out-of-grammar declared types stays "
            "Python-only — the generated descriptor cannot carry a shape "
            "the builder does not produce."
        ),
        severity_override="info",
        fixture_waiver=(
            "libs/pipeline_engine/dsl/validator_test.py::"
            "TestParamStructuralTypeChecking::"
            "test_opaque_type_tripwire_emits_notice — no real registry "
            "component can produce an opaque descriptor (the deep-sweep test "
            "asserts that), so a corpus fixture cannot express this rule; "
            "the tripwire is driven through the real validator with a "
            "monkeypatched exotic param type."
        ),
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
            "compile otherwise); (2) the declared `sum_eq` constraint "
            "(schema v2, spec 02 §2.5) — \"Parameter '{param}' of "
            "'{component}' must sum to {value}, got {sum}.\" Driven by an "
            'explicit {"rule": "sum_eq", "params_dict": ..., "value": ...} '
            "declaration since S5/M5 (2026-08-26); it previously fired via "
            "a name-based heuristic over dict params literally named "
            "'weights', which sum_eq replaced with byte-identical messages "
            "(census-gated: every heuristic adopter carries the "
            "declaration)."
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
            "Fires only on the HARD bounds of a param's constraints (`min` / "
            "`max`, each optionally absent = unbounded, each optionally "
            "exclusive via `min_exclusive` / `max_exclusive` — Q-2242); the "
            "`typical` range is guidance and never an issue. Pass-5 registry "
            "constraints — \"Parameter '{param}' of '{component}' value "
            '{value} below minimum {min}." / "... above maximum {max}." for '
            'inclusive bounds, "... must be greater than {min}." / "... must '
            'be less than {max}." for exclusive ones — and the pass-9 '
            'Execution-block range checks — "{param}={value} out of range '
            '[{min}, {max}]" (bracket per exclusivity) with both bounds, '
            '"{param}={value} must be greater than {min}" / "... no less '
            'than {min}" / "... less than {max}" / "... no greater than '
            '{max}" with one (buffer_threshold, min_trade_size, '
            "on_change_tolerance; bounds live in EXECUTION_PARAM_META since "
            "spec 02 T-15). Pass-5 sites attach a 'Change {param} to a value "
            "in range [...]' / '... greater than X.' suggestion; pass-9 sites "
            "don't."
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
        summary="A required parameter group has no member provided.",
        message_template=(
            "Component '{component}' requires {arity} of [{group}], but none provided."
        ),
        template_params=("component", "arity", "group"),
        explain=(
            "Cross-parameter constraint: the component requires {arity} "
            "('exactly one' for rule 'exactly_one'; 'at least one' for the "
            "schema-v2 dual rule 'at_least_one', S5 2026-08-26) member of "
            "the group and the call provides none. The {arity} template "
            "parameter renders the exactly_one message byte-identically to "
            "its pre-v2 fixed text."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Provide one of: {group}.",
        applicability=Applicability.MAYBE_INCORRECT,
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
    Rule(
        code="PARAM_RELATION_VIOLATION",
        category=RuleCategory.CORRECTNESS,
        summary="A declared pairwise parameter relation (le/lt/ge/gt) fails.",
        message_template=(
            "Component '{component}' requires {param_a} {op_text} {param_b}, "
            "but got {param_a}={value_a}, {param_b}={value_b}."
        ),
        template_params=("component", "param_a", "op_text", "param_b", "value_a", "value_b"),
        explain=(
            "Cross-parameter constraint (schema v2, dsl-type-system spec 02 "
            "§2.5, armed at M5/S5 2026-08-26): the component declares a "
            "pairwise relational constraint ('le' ≤ / 'lt' < / 'ge' ≥ / "
            "'gt' >) between two numeric __init__ params, and the effective "
            "(explicit-or-default) values violate it. One code for all four "
            "operators — {op_text} parameterizes the operator exactly as "
            "PARAM_GROUP_CONFLICT parameterizes {arity}. The relation binds "
            "only when both effective operands are literal numbers: a "
            "VariableRef operand skips the constraint (the standing pass-5 "
            "static-check exemption), and an unset Optional operand means "
            "the relation does not bind (ForecastClipper(lower=None, "
            "upper=5) must not fire lower ≤ upper). Every S5 adopter "
            "relation mirrors an existing __init__/library ValueError, so "
            "this is strictly earlier diagnosis of a failure that already "
            "blocked compile/run (the §4 zero-flip sweep evidence in the "
            "relational-constraints staged entry)."
        ),
        passes=("5",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Adjust {param_a} or {param_b} so {param_a} {op_text} {param_b}.",
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="relational-constraints",
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
            "founder question Q1 resolution pending)."
        ),
        passes=("6",),
        surfaces=_PY_TS,
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
            "Error at write time."
        ),
        passes=("6",),
        surfaces=_PY_TS,
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
            "(insert-a-converter guidance)."
        ),
        passes=("6",),
        surfaces=_PY_TS,
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
            "bug, hence warning."
        ),
        passes=("6",),
        surfaces=_PY_TS,
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
            "not a branch of the preceding Parallel."
        ),
        passes=("6",),
        surfaces=_PY_TS,
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
    # Pass 7 (RETIRED 2026-08-26): phase ordering
    # ═══════════════════════════════════════════════════════════════════════
    # PHASE_ORDER_VIOLATION was the pass-7 linear phase-ladder check
    # (DATA→UNIVERSE→SIGNAL→FORECAST→POSITION→OUTPUT backward jumps).
    # Retired by founder ruling 2026-08-26 (S4/F2, re-affirmed on the
    # corrected Q-0685 census): 932 fires across 701 of 1,380 stored
    # strategies — 57 % of prod — with ZERO cases where it was the sole
    # catcher of a real defect (every genuine misordering is co-caught by an
    # error-tier type/terminal rule), while every solo-fire subject was a
    # deliberate, correct pattern (post-forecast vol-targeting, funding-
    # carry mid-chain loads, multi-timeframe resamples — including the live
    # HRP book). The TS editor never ran it (`ts-pass7-skip`, removed with
    # it) and production loading forced BACKTEST mode specifically to skip
    # its runtime arm. Pass 7 deleted from dsl/validator.py; the Layer C
    # `_validate_phases` arm deleted from pipeline/validator.py; the
    # `is_universe_mask_phase_exempt` carve-out retired with them. Phase
    # METADATA (PHASE_INDEX / PHASE_GROUPS) stays — palette ordering,
    # /components/metadata, SDK registry, and the test synthesizer consume
    # it. Reserved, never re-minted (protobuf discipline); acknowledged
    # active→reserved in fixtures/BREAKING_CHANGES.md.
    Rule(
        code="PHASE_ORDER_VIOLATION",
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
            "A Load() reads a slot name that no earlier Store() wrote. Slots "
            'are a single flat namespace written by Store("name") and read '
            'back by Load("name") or by a slot-reference parameter. Two '
            "shapes reach this rule: a typo, and a Store that is there but "
            "cannot be SEEN — Parallel branches type from a snapshot of the "
            "slot table taken before the block, so a branch never sees a "
            "sibling branch's Store (the sibling's writes merge only after "
            "every branch is typed). Store above the Parallel when more than "
            "one branch needs the value."
        ),
        passes=("8",),
        surfaces=_PY_TS,
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
            "slot with no prior Store(). Branch isolation is the shape that "
            "surprises: a Parallel's branches each type from a SNAPSHOT of the "
            "slot table taken before the block and their writes merge only "
            "after every branch is typed, so a reader in branch B cannot see a "
            "Store in branch A no matter what order they are written in. "
            "Adding a second Store of the same name inside B is NOT the fix — "
            "two branches writing one slot is a runtime SlotOverwriteError. "
            "Move the Store (and the steps that produce it) above the Parallel. "
            "The suggestion says which of the two cases this is: it names the "
            "sibling branch when the slot is stored in one."
        ),
        passes=("8",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        # The RENDERED fix is context-selected at the emission site from the
        # EMIT_TEMPLATES `slot-ref-fix` family (judgments.py) — `sibling` when
        # the slot is stored in a sibling Parallel branch, `none` otherwise.
        # This template is the `none` member's text and the documentation of
        # the default; it is not what renders (the J-SLOTREAD.ref-present row
        # declares `suggestion`, so every emission passes one explicitly).
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
        code="DEPRECATED_UNIVERSE_FIELD",
        category=RuleCategory.HYGIENE,
        summary="Universe uses a deprecated field name that has a replacement.",
        message_template=(
            "Universe field '{field}' is deprecated; use '{replacement}' (same meaning)."
        ),
        template_params=("field", "replacement"),
        explain=(
            "A deprecated Universe field name is still accepted with EXACTLY "
            "its replacement's meaning, so the strategy parses, compiles and "
            "resolves as before — this is a naming warning, never an error. "
            "The one alias today (dollar-volume DV6b, 2026-09-24): "
            "`min_trailing_notional_proxy` → `min_trailing_dollar_volume`. The "
            "old name said 'proxy' because the floor was candle volume × close; "
            "since DV1 it is traded dollar volume (Σ trade price × size, "
            "`SUM(pv_sum)` over `bars_1m`; none exists before 2025-03-23) under "
            "either name. Every writer (the DSL printer, the editor, MCP, SDK "
            "and chat tools) emits the new name, so rewriting the Universe "
            "through any of them clears the warning. Declaring BOTH names is "
            "INVALID_UNIVERSE. Minted unstaged: no committed conformance "
            "fixture, pinned trace or library strategy uses the alias "
            "(measured 2026-09-24), so no pinned population moves."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template="Rename '{field}' to '{replacement}'.",
        applicability=Applicability.MACHINE_APPLICABLE,
    ),
    Rule(
        code="UNSUPPORTED_MARKET",
        category=RuleCategory.CORRECTNESS,
        summary=(
            "The strategy names a market, dex or venue Keel does not trade "
            "(Keel trades native Hyperliquid perps only)."
        ),
        message_template=(
            "{field}={value} is not supported{yet} — Keel trades {scope} "
            "only. Remove {field}= or use {field}={supported}.{valid_note}"
        ),
        template_params=("field", "value", "yet", "scope", "supported", "valid_note"),
        explain=(
            "Keel trades Hyperliquid perpetuals and nothing else, so `perp` is "
            "the only market and the default (Q-2363; founder ruling "
            "2026-10-04). Shapes under one code: `Universe(market=...)` "
            "naming anything but `perp`, and a data loader whose static "
            "`market_type` is set to anything but `perp`. The owner of the "
            "supported sets is `pipeline_engine.dsl.spec` (`SUPPORTED_MARKETS`, "
            "`SUPPORTED_DEXES`, `SUPPORTED_EXCHANGES`). "
            '`market="spot"` (a market Hyperliquid lists) gets the sentence '
            "alone; an unknown value adds `Valid: perp.`.\n\n"
            "The same code refuses a data loader whose static `dex_name` is "
            'anything but `"native"` — a HIP-3 builder dex such as `"xyz"`, or '
            '`None` ("every dex") — and one whose `exchange` / `source` names '
            'a venue other than `"hyperliquid"` (Q-2414 / Q-2415; founder '
            'ruling 2026-10-04). Those say "not supported yet": HIP-3 may be '
            "supported later, but today the backtest store holds native perps "
            "only and execution trades native Hyperliquid perps only, so such a "
            "loader ran native-perp data under a false label. Omitting the "
            "parameter is the default (`native` / `hyperliquid`) and is "
            "valid. Saved strategies meet the loader refusal at backtest "
            "submit, deploy and deployment update, where keel-api's "
            "`assert_strategy_valid` re-validates the stored source.\n\n"
            "Why an error and not a label: until Q-2363 `Universe.market` only "
            "chose which listings the resolver checked names against. The "
            "simulation took its market from the data loaders, which the "
            "workers default to perps, and live execution maps coins through "
            'the perp universe only — so a `market="spot"` strategy was '
            "backtested as the perp, charged perp funding, and would have "
            "traded the perp live. Saved strategies that predate the rule "
            "meet the same code and sentence at backtest submit and deploy "
            "(keel-api `assert_universe_resolved`, before any symbol is "
            "classified), and the universe resolver refuses such a "
            "declaration the same way. Removing `market=` or writing "
            '`market="perp"` is the whole fix; it changes nothing else about '
            "the strategy. Minted unstaged: no committed conformance fixture, "
            "pinned trace or library strategy names a market other than perp "
            "(measured 2026-10-04)."
        ),
        passes=("9",),
        surfaces=(Surface.PY_DSL, Surface.TS_EDITOR, Surface.GATE),
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
            "Universe has not been resolved. Save the strategy — the server "
            "resolves the criteria and bakes the asset list into the source on "
            "every save; in the web editor, open the Universe block and change "
            "any field and it re-resolves in place."
        ),
        template_params=(),
        explain=(
            "Non-manual universes must be resolved to a concrete asset list "
            "before production paths. THE SAVE IS THE RESOLVER: keel-api bakes "
            "an unresolved Universe on create and update "
            "(universe_bake.bake_universe_resolution, Q-1504) — the typed "
            "basket for manual, the server resolver for the criteria modes — "
            "because the save is the one moment every surface passes through "
            "and the hosted MCP surface has no resolve step of its own. The "
            "message therefore names the SAVE, not a tool: naming a tool sends "
            "an agent looking for one its host may not expose (D6, "
            "mcp-strategy-view W4 §4). Warning normally; promoted to error "
            "under production_mode (promote_in_production=True). Known "
            "condition divergence: the TS trigger is narrower than Python's "
            "(research/03 §1B; spec 02 Q2 proposes forcing the port).\n\n"
            "**Pre-save context (`pre_save`).** A caller validating a source "
            "it is ABOUT to save (the MCP compose dry run) declares "
            "`pre_save=True`: an unresolved CRITERIA universe (top_volume / "
            "category) is then info, not a warning — the save it precedes is "
            "the resolver, so a warning there was present on every dry run "
            "and taught agents to skim warnings (agent-surface-cleanup "
            "review 06 §3.2 #4). Never under production_mode (the run and "
            "deploy gates still refuse with this code at error), never for a "
            "manual universe the save cannot resolve, and never the "
            "default: a stored source is judged as before."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        promote_in_production=True,
        severity_context_overrides={"pre_save": "info"},
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
            '("resolved has N items but top_n=K implies X–Y"). The count arm '
            "cannot tell a hand-edited list from moved criteria or an "
            "under-supplying venue, so its copy leads with the manual-basket "
            'remedy (Universe(mode="manual", symbols=[…])), names re-resolving '
            'second, and gives "lower top_n" only conditionally (Q-2434). '
            "Warning normally; promoted to error under production_mode "
            "(promote_in_production=True). APPROXIMATE: it reads the list's "
            "shape, never whether the list answers the criteria. "
            "mode='category' is not checked here, and no later runtime check "
            "exists — the workers trade `resolved` as stored. A list carried "
            "across a criteria change is caught at the save instead (keel-api's "
            "carried-list backstop, Q-2435)."
        ),
        passes=("9",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "Never ported to the TS editor validator. The visual Universe "
            "editor re-resolves on every criteria edit (its D-12 criteria-key "
            "machine), so it cannot produce a stale list. Code mode does NOT "
            "auto-resolve: the save re-resolves a list carried across a "
            "criteria change (Q-2435), and the code editor warns in place on a "
            "hand-edited top_volume/category list and offers to make it a "
            "manual basket (Q-2434), outside the validator."
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
            "Globals '{field}' is declared but nothing in the pipeline consumes "
            "it. Either remove the Globals declaration, or add a step that "
            "consumes it (a loader serving coarser than its source applies "
            "bar_offset itself; a TargetTimeframeResampler applies it when the "
            "loader serves its own grain)."
        ),
        template_params=("field",),
        explain=(
            "Dead configuration: the Globals field feeds nothing. The message "
            "names both fix paths (remove, or add a consumer) by design — see "
            "validator_resampler_test.py::TestUnusedGlobalMessage. The "
            "bar_offset arm counts a CLOCK TRANSFER that consumed the offset "
            "(the interpreter walk's offset_consumed fact), not a component "
            "that merely declares a reference to it: since PriceDataLoader v3 "
            "references globals.bar_offset on every strategy, a reference-based "
            "arm would never fire again (new-data-loaders spec 05 §1b)."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
    ),
    Rule(
        code="RESAMPLER_NOOP",
        category=RuleCategory.HYGIENE,
        summary="A re-clocking step targets the clock its input already sits on.",
        message_template=(
            "{step} is a no-op: '{origin_step}' already serves {source_clock}, "
            "which is exactly the {target_tf} clock this step would produce. "
            "Remove the {step}() step. Keep Globals(target_timeframe=...) — it "
            "declares the execution clock."
        ),
        template_params=("step", "target_tf", "source_clock", "origin_step"),
        explain=(
            "Same-clock re-clocking is a wasted step (the 2026-06-06 jeff5908 "
            "case). The runtime short-circuits it cleanly, so this is hygiene, "
            "not correctness. Suppressed when the resampler config already "
            "errored. The message names the ORIGIN step that already serves the "
            "clock — with its phase, so PriceDataLoader() under "
            "Globals(target_timeframe='1d', bar_offset='12h') reads as 'already "
            "serves 1d@12h' — and no longer suggests dropping the Globals line: "
            "since PriceDataLoader v3 the loader FOLLOWS Globals, so that advice "
            "broke the strategy it was given for (Q-1497; new-data-loaders spec "
            "05 §3)."
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
        code="LOADER_FINER_THAN_NATIVE",
        category=RuleCategory.CORRECTNESS,
        summary="A loader is asked to serve a grain finer than its native data.",
        # Byte-identical to validation_shared.finer_than_native_message — the
        # one sentence the runtime refusal renders too (spec 05 §2b); pinned
        # by dsl/clocks_synth_offset_test.py::TestFloorRefusal.
        message_template=(
            "{loader} serves hourly data; {target_desc} is finer than its "
            "{native_tf} native grain. Set timeframe='{native_tf}' on the loader "
            "and add TargetSignalProjector() at the end of the branch — the last "
            "completed hour is held on the {target_tf} grid."
        ),
        template_params=("loader", "native_tf", "target_tf", "target_desc", "fix_component"),
        suggestion_template=(
            "Two edits: timeframe='{native_tf}' on {loader}, then {fix_component} "
            "as the branch's last step (projection holds the last COMPLETED "
            "{native_tf} bar on the {target_tf} grid; no method)."
        ),
        explain=(
            "The native-grain floor of a stream loader (new-data-loaders spec "
            "05 §2b): the funding, open-interest and premium partitions are "
            "ingested hourly and nothing finer exists, so a served grain below "
            "the loader's declared clock_transfer 'floor' is refused at write "
            "time rather than at run time. The fix is the one shape the "
            "platform supports for a finer target: serve the native grain "
            "explicitly (the explicit timeframe= IS the override of Globals) "
            "and project at the end of the branch. Two edits are needed "
            "(set_param + insert_step at the branch end, which the synth site "
            "cannot locate), and the suggested_edit vocabulary carries ONE "
            "edit, so no machine edit is attached: both halves ride "
            "valid_options and the issue is has_placeholders. Fires from the "
            "pass-6 judgment row J-SYNTH.floor (conformance fixtures under "
            "LOADER_FINER_THAN_NATIVE/; the row-level proofs live in "
            "clocks_synth_offset_test.py::TestFloorRefusal)."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="loader-native-floor",
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="LOADER_TIMEFRAME_UNBOUND",
        category=RuleCategory.CORRECTNESS,
        summary="A Globals-bound loader has no timeframe: no literal, no Globals(target_timeframe).",
        # Byte-identical to the D2 runtime refusal in
        # components/data_loaders/price/loader_v3.py (_served_timeframe) —
        # pinned by dsl/clocks_synth_offset_test.py::TestUnboundRefusal.
        message_template=(
            "{loader}: no timeframe to serve — declare Globals(target_timeframe=...) "
            "so the loader follows the strategy's clock, or pass {src}=... for a "
            "branch on its own clock."
        ),
        template_params=("loader", "src"),
        suggestion_template=(
            "Add Globals(target_timeframe=...) above the Pipeline so {loader} "
            "follows the strategy's clock, or set {src}=... on {loader} for a "
            "branch on its own clock."
        ),
        explain=(
            "The write-time half of new-data-loaders spec 05 D2 (Q-1510): a "
            "clock-source loader whose timeframe binds to "
            "Globals.target_timeframe (PriceDataLoader v3, the served-grain "
            "stream family, the flow loaders) serves NOTHING when the strategy "
            "declares no Globals(target_timeframe=...) and passes no literal — "
            "there is no silent default any more, so the runtime refuses the "
            "very first step of the backtest. This row refuses the same shape "
            "at write time, in the same sentence. Fires from the pass-6 "
            "judgment row J-SYNTH.unbound, the synth transfer's first check: "
            "the src param is absent (a VariableRef or a non-string stays "
            "clock-less under R-8 — its own codes own it), its declaration "
            "ref is globals.target_timeframe, no Globals supplies a string, "
            "and the RESOLVED signature carries no string default (a strategy "
            "locked to PriceDataLoader v2 resolves its '15min' default and is "
            "untouched). The output stays clock-less (recovery none); with no "
            "Globals there is no kappa_exec, so no terminal premise stacks on "
            "it. Two fixes, either alone sufficient (declare the Globals, or "
            "pass the literal), both with placeholder values, so no single "
            "machine edit is claimed: has_placeholders. Conformance fixtures "
            "under LOADER_TIMEFRAME_UNBOUND/."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        staged_by="loader-timeframe-unbound",
        applicability=Applicability.HAS_PLACEHOLDERS,
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
            "table (validation_shared.validate_resample_config). Two firing "
            "sites: the coarsen row J-COARSEN.offset-same-tf (a resampler "
            "targeting the clock it receives with a different phase) and, since "
            "new-data-loaders spec 05 §3, the synth row J-SYNTH.offset-same-tf "
            "(a loader serving its own roll grain under an offset finer than "
            "one of its bars — no downstream coarsen could ever take it). Quiet "
            "when the loader CONSUMED the offset by serving a coarser grain."
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
            "bars. Part of the shared resampler rule table. Two firing sites: "
            "the coarsen row J-COARSEN.offset-multiple and, since new-data-"
            "loaders spec 05 §3, the synth row J-SYNTH.offset-multiple (a "
            "loader that would roll its source grain onto an offset the source "
            "bars do not tile — the roll validate_resample_config refuses at "
            "run time, named at write time)."
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
            "strategy. The completed-bar rule: the projected value at fine "
            "time t is the coarse bar whose close time ≤ t. Projection therefore "
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
    # TOMBSTONES (2026-08-26): the Layer C runtime-only codes. Layer C — the
    # runtime PipelineValidator + LookaheadValidator in pipeline/validator.py
    # and pipeline/lookahead.py — was RETIRED by founder-delegated ruling on
    # the Q-0689 research verdict (07-loader-validation-posture.md): its
    # linear type-threading walk predated the DSL's slot/Parallel semantics
    # and false-positived on 44% of shipped valid strategies (8/18 library
    # entries, 3/6 golden blobs — the same FP population that retired
    # PHASE_ORDER_VIOLATION, Q-0685); every execution surface forced it off
    # via BACKTEST mode; and the incident sweep found ZERO historical defects
    # where it would have been the catching layer. The ruling deliberately
    # forecloses the verify-spine reservation these codes were parked for —
    # a future runtime-verification revival gets a FRESH spec and fresh
    # codes, not dead code kept warm. Reserved, never re-minted (protobuf
    # discipline); acknowledged in fixtures/BREAKING_CHANGES.md +
    # METADATA_EVENTS.md.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="TYPE_HINTS_UNAVAILABLE",
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
    Rule(
        code="VALIDATION_DEPTH_EXCEEDED",
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
    Rule(
        code="TRANSITION_INVALID",
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
    Rule(
        code="COMPOSER_MISSING_KEYS",
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
    Rule(
        code="SLOT_SELF_CYCLE",
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
    Rule(
        code="USES_FUTURE_DATA",
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
    # ═══════════════════════════════════════════════════════════════════════
    # PASS 0 — the PARSER's own vocabulary (Q-1695, W4 §2), 2026-09-22.
    #
    # `DSLParseError` was the bootstrap class: an Exception with a line and a
    # column, built for a human reading a CLI. When the rule catalog arrived
    # (2026-07-10) it catalogued what the VALIDATOR minted and modelled the
    # parser's failure as an API-gate wrapper — `PARSE_ERROR`, whose message
    # is `str(exc)` verbatim — because the parser RAISES instead of emitting
    # and there was no issue list to render into. The intake tripwire scans
    # the validator files, so no test ever asked the parser for a code.
    #
    # The consequence: the tier MIGRATION lands in was the one tier with no
    # code, no fix, no explain channel and a different shape on every surface.
    # Eighteen families, enumerated from the 86 real raise sites in parser.py
    # rather than guessed — one code per family, with `{detail}` carrying the
    # site's own prose so the sixty distinct messages stay distinct while the
    # code is stable.
    #
    # THE FIX PROSE LIVES HERE, not at the raise site: `_error` reads
    # `RULES[code].suggestion_template`, so `keel_help rule:<CODE>` and the
    # raised suggestion are the same string by construction. The one
    # site-computed fix is UNKNOWN_DECLARATION_KEY's, which renders a per-key
    # migration when it knows one.
    #
    # `passes=("0",)` is a new pass token — the parse tier precedes pass 1.
    # `ts_mirrored=False` by construction: the browser editor persists a
    # GraphModel and never parses DSL text.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="SYNTAX_ERROR",
        category=RuleCategory.CORRECTNESS,
        summary="The strategy source is not valid Python.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The strategy DSL is a RESTRICTED Python grammar, so the file "
            "must first be valid Python. The message is CPython's own, "
            "verbatim, with its line and column — no rewording, because "
            "nothing a strategy-specific layer could add beats the "
            "interpreter's own diagnosis of an unclosed bracket. This is "
            "the one parse code with no fix prose: there is no migration "
            "and no grammar rule to state, only the syntax error itself."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        applicability=Applicability.NONE,
    ),
    Rule(
        code="IMPORT_NOT_ALLOWED",
        category=RuleCategory.CORRECTNESS,
        summary="A strategy file may not import anything.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Every component is already in scope by name — the parser "
            "resolves names against the live registry, so an import is "
            "never needed and never honoured. It is also a SAFETY boundary: "
            "a strategy is parsed, never executed as Python, and an import "
            "is the first thing an author reaches for when they are trying "
            "to do something the DSL deliberately cannot."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Remove the import. A strategy file declares "
            "Globals/Universe/Execution and one Pipeline; every component "
            "is already in scope by name."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="LEGACY_STRATEGY_CALL",
        category=RuleCategory.CORRECTNESS,
        summary="The pre-2026 Strategy(...) form, with its migration.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Strategy(name=..., asset_class=..., max_assets=..., "
            "pipeline=[...]) was the form before the four declarations. It "
            "is the shape migration ARRIVES in — from Keel's own history "
            "and from other platforms — and until 2026-09-22 it fell into "
            "the generic expression-statement message, which states the "
            "grammar rule and not the translation. "
            "\n\nThe fix carries the four-declaration skeleton verbatim (the "
            "compose description's skeleton, decision #23) and the field- "
            "by-field map: asset_class becomes Universe(market=...), "
            "max_assets becomes Universe(mode='top_volume', top_n=N), "
            "pipeline becomes Pipeline([...]), and name moves to the "
            "strategy's metadata rather than its source."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Split it into the four declarations:\n"
            'Globals(target_timeframe="1d")\n'
            'Universe(mode="manual", symbols=["BTC", "ETH"], market="perp")\n'
            'Execution(rebalance="every_bar")\n'
            "Pipeline([...])\n"
            "Field by field: asset_class=... becomes Universe(market=...); "
            'max_assets=N becomes Universe(mode="top_volume", top_n=N); '
            "pipeline=[...] becomes Pipeline([...]); name=... moves to the "
            "strategy's metadata, not its source."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="UNKNOWN_TOP_LEVEL_STATEMENT",
        category=RuleCategory.CORRECTNESS,
        summary="A top-level statement the grammar does not admit.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The file's top level admits exactly: Globals(...), "
            "Universe(...), Execution(...), factory defs, variable "
            "assignments, and one Pipeline(...). Anything else is this "
            "code. The legacy Strategy(...) call is split out into "
            "LEGACY_STRATEGY_CALL so its author gets the migration rather "
            "than the rule."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Only Globals(...), Universe(...), Execution(...), factory "
            "defs, variable assignments and one Pipeline(...) may appear at "
            "top level."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="CONTROL_FLOW_NOT_ALLOWED",
        category=RuleCategory.CORRECTNESS,
        summary="if / for / while / with / try / class / async in a strategy file.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "A strategy is DECLARATIVE: the source states a pipeline, the "
            "platform compiles and runs it, and a deterministic compile is "
            "what makes a saved strategy reproducible. Control flow has "
            "three declarative answers and the fix names all three: branch "
            "with a Parallel dict, parameterize with a factory, repeat with "
            "a variable."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Strategies are declarative. Branch with a Parallel dict "
            '({{"a": [...], "b": [...]}}), parameterize with a factory (def '
            "f(x): return Pipeline([...])), repeat with a variable."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="DECLARATION_ORDER",
        category=RuleCategory.CORRECTNESS,
        summary="A declaration appears after something it must precede.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Order is Globals, Universe, Execution, then factories and "
            "variables, then Pipeline. It is a READING order rather than a "
            "technical one — a reader meets the clock, then the assets, "
            "then the execution policy, then the logic — and the parser "
            "enforces it so every stored strategy reads the same way."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Order: Globals, Universe, Execution, then factories and variables, then Pipeline."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="DECLARATION_DUPLICATE",
        category=RuleCategory.CORRECTNESS,
        summary="Two of a declaration that may appear once.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Globals, Universe, Execution and Pipeline appear at most once "
            "each. A second one is never a merge and never a last-wins: it "
            "is an edit that landed twice, so the parser refuses rather "
            "than silently choosing one."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=("Merge the two calls into one."),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="MISSING_PIPELINE",
        category=RuleCategory.CORRECTNESS,
        summary="The file declares no Pipeline(...).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The declarations describe the run; the Pipeline IS the run. A "
            "file with declarations and no Pipeline is the shape a half- "
            "applied edit leaves behind. It carries no line number because "
            "the absence has no position."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Add Pipeline([PriceDataLoader(), ...]) after the declarations "
            "— the pipeline is what a run executes; the loader reads its "
            "clock from Globals(target_timeframe=...)."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="UNKNOWN_DECLARATION_KEY",
        category=RuleCategory.CORRECTNESS,
        summary="A declaration parameter that does not exist.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "The message lists the declaration's Available keys. The "
            "SUGGESTION does more when it can: a small migration map turns "
            "the keys the pre-2026 form used into the ones that exist — "
            "asset_class into market, max_assets into mode='top_volume' "
            "plus top_n, assets/tickers into symbols, a Globals-level "
            "timeframe into target_timeframe. Those were named as 'removed' "
            "in one skill file and nowhere the parser could reach."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Check the Available list in the message for the declaration's "
            "keys; a key that MOVED is named with its replacement."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="DECLARATION_ARG_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="A declaration called with the wrong argument shape.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Declarations take keyword arguments with LITERAL values: no "
            "positional args, no **kwargs, no expressions. The literal "
            "requirement is what lets the save bake a resolved universe "
            "back into the source as text."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=("Write Declaration(key=value, ...) with literal values."),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="PIPELINE_ARG_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="Pipeline(...) called with the wrong argument shape.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Pipeline takes one list of steps and an optional name. The "
            "list is positional and required; a keyword that belongs to the "
            "RUN rather than the strategy (a backtest window, a fee model) "
            "is refused here with its own sentence naming where it belongs."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=('Pipeline([...]) takes one list of steps and an optional name="...".'),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="STEP_NOT_A_CALL",
        category=RuleCategory.CORRECTNESS,
        summary="A pipeline step that is not a component call or a structure.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "A step is Component(param=value), a slot op (Store / Load / "
            "Extract), a Parallel dict, or a nested Pipeline([...]). The "
            "refusals an author actually meets are np./pd. prefixes and "
            "inline expressions — both of which mean the same thing: the "
            "computation belongs in a component, where it can be versioned, "
            "typed and reused."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            'A step is Component(param=value), Store("slot")/Load/Extract, '
            'a {{"branch": [...]}} Parallel, or a nested Pipeline([...]). '
            "No pd./np. prefixes, no expressions."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="POSITIONAL_ARGS_NOT_ALLOWED",
        category=RuleCategory.CORRECTNESS,
        summary="A component or factory called with positional arguments.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Every parameter is named. Positional arguments make a "
            "strategy's meaning depend on a component's parameter ORDER, "
            "which a version bump may change; naming them is what lets a "
            "stored strategy survive one."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=("Name every argument: Component(param=value)."),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="PARAM_EXPRESSION_NOT_ALLOWED",
        category=RuleCategory.CORRECTNESS,
        summary="A parameter value that is not a literal or a variable name.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Parameter values are literals (numbers, strings, lists, dicts) "
            "or the name of a variable defined above. No f-strings, no "
            "arithmetic, no calls, no comprehensions, no attribute access. "
            "The restriction is what makes the compile deterministic: the "
            "value in the source IS the value at run time."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "Parameter values are literals (numbers, strings, lists, dicts) "
            "or the name of a variable defined above."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="SLOT_OP_ARG_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="A slot operation called with the wrong arguments.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Store and Load take exactly one string literal; Extract takes "
            "one; StoreValue takes two. The slot NAME must be a literal "
            "because the slot table is resolved at parse time — a computed "
            "name has nothing to resolve against."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            'Store("slot") and Load("slot") take one string literal; '
            'Extract("key") takes one; StoreValue("slot", value) takes two.'
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="PARALLEL_BRANCH_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="A Parallel block whose branch keys are not string literals.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "A Parallel's branch keys are string literals: they become the "
            "record's field names and are referenced by composers and by "
            "Extract, so they must exist at parse time. Dict unpacking is "
            "refused for the same reason."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=('Write {{"name": [step, ...], ...}} with string keys.'),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="FACTORY_DEF_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="A factory def that is not a single return Pipeline([...]).",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "A factory is `def name(param=default): return Pipeline([...])` "
            "— exactly one return statement, no decorators, no *args, no "
            "**kwargs. It is a TEMPLATE the parser expands at each call "
            "site, not a function the platform executes, which is why its "
            "body is a shape rather than code."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "A factory is `def name(param=default): return Pipeline([...])` "
            "— no decorators, no *args, no **kwargs, no other statements."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="VARIABLE_ASSIGN_SHAPE",
        category=RuleCategory.CORRECTNESS,
        summary="A top-level assignment that is neither a sub-pipeline nor a literal.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "A variable holds a sub-pipeline or a literal value. A bare "
            "component call is the mistake that actually arrives — x = "
            "EWMA(window=20) — and its message names the Pipeline form for "
            "that exact variable rather than the general rule."
        ),
        passes=("0",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_PARSE_ABSENT_REASON,
        suggestion_template=(
            "A variable holds a sub-pipeline or a literal: x = "
            "Pipeline([Component(...)]), or x = 20."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="PIPELINE_NOT_BACKTEST_READY",
        category=RuleCategory.CORRECTNESS,
        summary="A valid pipeline that cannot produce weights, refused at the run gate.",
        message_template="{detail}",
        template_params=("detail",),
        explain=(
            "Minted by keel-api's run/deploy gate (`utils/strategy_validation."
            "_assert_backtest_ready`, Q-0675) when a strategy is VALID DSL and "
            "still cannot run: a two-step PriceDataLoader -> "
            "TargetTimeframeResampler pipeline terminates at OHLCV, so "
            "Pipeline.run() returns a dict and the worker dies with a bare "
            "TypeError AFTER the quota unit and the compute reservation have "
            "been debited.\n\n"
            "**Why it is catalogued only now (Q-1698).** It was minted outside "
            "the catalog from 2026-08-24 until 2026-09-22 — the standing "
            "intake rule ('any PR that mints a new structured code MUST add "
            "its catalog entry in the same PR') is enforced by a scan of the "
            "VALIDATOR files, and a keel-api gate mint is outside that scan. "
            "The scan now covers `services/keel-api/src/utils/*.py` gate "
            "mints, so the next one cannot repeat it.\n\n"
            "**Retirement.** TERMINAL_NOT_WEIGHTS / TERMINAL_DICT_NOT_CONSUMED "
            "are the same verdict computed at write time with an explain "
            "channel, a TS mirror and a conformance corpus. While their ramps "
            "are DORMANT this gate code is the only refusal and stays active; "
            "when they arm, the gate reads them from a production_mode "
            "validation and this code retires to `reserved` (the sanctioned "
            "active -> reserved path)."
        ),
        passes=(),
        surfaces=_GATE_ONLY,
        ts_mirrored=False,
        ts_absent_reason=_TS_GATE_ONLY_REASON,
        fixture_waiver=(
            "services/keel-api/tests/test_run_gate_backtest_ready.py::"
            "TestScaffoldIsRefusedAtSubmit — gate code, out of the write-time "
            "corpus's reach (spec 02 §3.3). The CLASS is the node id because "
            "every arm in it covers the code and the waiver resolver's leaf "
            "pattern (`^(\\s*def|class) <name>`) does not match an `async "
            "def`, which every arm here is."
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
        summary="A soft (interval-tier) value convention is provably exceeded.",
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
            "The catalog name of the soft-tier `eq/sub × viol × soft` outcome "
            "row of spec 01 §2.4: a statically-KNOWN value description that "
            "provably leaves a soft `Interval`/Bounds convention. Soft "
            "domains are conventions, not laws (GOAL non-goal 3; "
            "D10-clarification: warnings by design, never scheduled for "
            "error promotion).\n\n"
            "**The unproven shape is RETIRED** (claims model, founder-"
            "ratified 2026-08-23; spec 09 §3.4, staged change "
            "`soft-unproven-silence` at 'post'). The sibling row "
            "`eq/sub × unproven × soft` — 'the values reaching this "
            "convention are not statically known' — is now SILENT, because "
            "it fired on the NORMAL use of the components it guarded (prod "
            "census: essentially no ThresholdCross feed is provably "
            "in-interval), and a permanent warning on ordinary code trains "
            "users to ignore warnings. Absence of proof is no longer "
            "reported at the soft tier; only a witnessed contradiction is. "
            "The HARD tier is untouched — an unproven feed into a `Set` "
            "demand still reports VALUE_DOMAIN_UNPROVEN.\n\n"
            "**The `{finding}` shapes.** Rendered by the emitting site as a "
            "pre-formatted clause. In the shipped configuration only the "
            "violated shape occurs: `are known to include <actual_domain>-"
            "values outside it`. The unproven clause (`are not statically "
            "known`) remains in the template for the pre-flip configuration "
            "the staging machinery can still select (rollback / "
            "`staged_stage_override` in tests). The machine-readable "
            "distinction rides the envelope's expected/actual fields, never "
            "the prose.\n\n"
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
    Rule(
        code="REFINED_ROLE_MISMATCH",
        category=RuleCategory.CORRECTNESS,
        summary="A signal of one refined role feeds a demand for a different one.",
        message_template=(
            "'{consumer}' expects {expected_display} values in {expected_domain}, "
            "but '{producer}' supplies {actual_role}{via} — a different signal "
            "role, and nothing proves its values are in {expected_domain}."
        ),
        template_params=(
            "consumer",
            "expected_display",
            "expected_domain",
            "producer",
            "actual_role",
            "via",
        ),
        explain=(
            "The D-1 role judgment (founder-ratified 2026-08-23; "
            "dsl-type-system decisions.md, ledger Q-0543). A refined base "
            "name states a ROLE — what a series is FOR — and two DISTINCT "
            "refined names are incomparable siblings in the base order "
            "(spec 01 §1.3), so neither can stand in for the other.\n\n"
            "**Why this exists.** The claims model deleted the ladder step "
            "that let a type NAME mint a value FACT, which was right: the "
            "fact was fiction. But one verdict it had carried was TRUE — a "
            "forecast series is not a binary signal — and feeding one to "
            "`TradeManager(entries=...)` is a guaranteed runtime raise (its "
            "{-1, 0, 1} entry contract). With an "
            "honest `⊤` domain the checker cannot PROVE a violation, so the "
            "domain half can only say `unproven`. This code recovers the "
            "verdict from the DECLARED NAMES, minting nothing.\n\n"
            "**Strict precedence — this is what makes it 0-FP.** A proven "
            "domain always outranks a role mismatch:\n"
            "- values PROVABLY in the demanded domain (`sat`) ⇒ accept, role "
            "irrelevant. `ConstantForecast(value=1)` into a BinarySignal "
            "demand proves `Set{1.0} ⊑ Set{-1,0,1}` and stays clean.\n"
            "- values provably OUTSIDE it (`viol`) ⇒ VALUE_DOMAIN_MISMATCH, "
            "which is the stronger, older statement.\n"
            "- values UNPROVEN + a conflicting role ⇒ this code.\n"
            "- values UNPROVEN + a matching or ABSENT role ⇒ "
            "VALUE_DOMAIN_UNPROVEN's warning, unchanged. A bare carrier "
            "declares no role: `Clip` outputs `SignalSeries`, so a "
            "`ThresholdCross → Clip(0,1) → Store → TradeManager(entries=...)` chain stays a "
            "warning — its values really may be binary.\n\n"
            "**Hard demands only.** Soft (`Interval`) domains are "
            "conventions, permanently advisory by GOAL non-goal 3, so a "
            "role conflict at a soft demand is never an error.\n\n"
            "**Remediation.** Convert the role: threshold the signal "
            "(ThresholdCross / AboveThresholdFilter / RangeSelector) before "
            "the consumer, or feed the consumer a series that declares the "
            "role it demands."
        ),
        passes=("6", "8"),
        surfaces=_PY_TS_RT,
        ts_mirrored=True,
        suggestion_template=(
            "Convert the {actual_role} to {expected_display} before "
            "'{consumer}' — threshold it via ThresholdCross / "
            "AboveThresholdFilter / RangeSelector."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        staged_by="role-mismatch-rule",
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
    # ═══════════════════════════════════════════════════════════════════════
    # Threshold-composition + executor-seam advisories (Q-0580) — minted
    # 2026-08-24. Founder authorizations: the F2 batch-2 dossier's
    # unreachable-threshold-arm candidate (dsl-type-system decisions.md
    # 2026-08-23: "dead-threshold-arm rule queued as an independent lane")
    # and the Q-0570 D3 ruling's stage-3 PRICE_MARKS_AUTO follow-up. Both
    # ship UNSTAGED at their category/override-derived severities: neither
    # ever blocks, neither is scheduled for promotion, and neither needs an
    # evidence ramp (warning-tier hygiene + an info notice).
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="UNREACHABLE_THRESHOLD_ARM",
        category=RuleCategory.HYGIENE,  # dead config — warning by policy
        summary="A configured threshold arm provably can never fire.",
        message_template=(
            "The {arm} threshold arm of '{component}' ({param}={value}) can never fire: {reason}."
        ),
        template_params=("arm", "component", "param", "value", "reason"),
        explain=(
            "The F2 batch-2 dossier's dead-arm finding (2026-08-23; queued as "
            "an independent lane in dsl-type-system decisions.md, executed as "
            "Q-0580): the library idiom `ThresholdCross(upper=0.0, "
            "lower=-3.0)` carried a `lower` arm whose -1 output was provably "
            "discarded downstream — a real bug class no other rule sees. A "
            "user who configures an arm that cannot fire believes they have "
            "short (or long) signals they do not have.\n\n"
            "**Two provable shapes, both static.**\n"
            "1. *Mode-gated (dead config):* `mode='long_only'` never "
            "evaluates the lower threshold and `mode='short_only'` never "
            "evaluates the upper one (threshold.py run()); an EXPLICITLY "
            "written opposite-arm param is dead configuration regardless of "
            "its value. Unwritten params (registry defaults) never fire this "
            "— only config the author actually typed can be dead config.\n"
            "2. *Clip-collapsed:* ThresholdCross emits only {-1, 0, +1}, and "
            "for an IMMEDIATELY adjacent Clip, `clip(-1) == clip(0)` exactly "
            "when `clip.lower >= 0` (the lower arm's -1 becomes "
            "indistinguishable from flat) and `clip(+1) == clip(0)` exactly "
            "when `clip.upper <= 0`. Fires only for arms the declared mode "
            "actually evaluates — a mode-disabled arm is shape 1's verdict, "
            "never a double report.\n\n"
            "**Conservative by construction.** Adjacency is strict: any "
            "intervening component, slot op (a Store between them exposes "
            "the un-clipped values to other readers), Parallel boundary, or "
            "statically-unresolvable param (VariableRef / non-literal) means "
            "no verdict. The rule fires only on proof, so it can under-"
            "report but never false-positive. Component anchors "
            "(`ThresholdCross`, `Clip`) are documented per-component "
            "constants in validator.py, the UNIVERSE_MASK_APPLIERS "
            "precedent — no registry metadata isolates 'discrete threshold "
            "with mode-gated arms' today.\n\n"
            "**Hygiene, permanently warning-class by category policy.** The "
            "strategy still runs; the arm is inert. The remediation is to "
            "delete the dead config or express the intent directly "
            "(`mode='long_only'` instead of a clip that discards shorts)."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Remove the dead '{param}' configuration from '{component}', or "
            "change the mode/clip combination so the {arm} arm can take "
            "effect."
        ),
        applicability=Applicability.MAYBE_INCORRECT,
        recoverable=True,
    ),
    Rule(
        code="PRICE_MARKS_AUTO",
        category=RuleCategory.SUSPICIOUS,
        summary="No PriceDataLoader declared — the signal reads no prices (notice).",
        message_template=(
            "This pipeline's signal reads no prices, which is fine: every "
            "backtest's simulation prices are loaded by the platform at the "
            "strategy clock from {venue}. Add PriceDataLoader() only if your "
            "signal should read prices."
        ),
        template_params=("venue",),
        explain=(
            "The Q-0570 stage-3 advisory (founder ruling 2026-08-24), "
            "reworded by release-fixes-2026-10 spec 01 (Q-2284): the prices "
            "a backtest trades at — and the funding a perp backtest charges "
            "— are loaded by the platform for EVERY run, at the Globals "
            "clock, from the market the data loaders name; no loader's "
            "output ever reaches the simulation. A pipeline that declares no "
            "PriceDataLoader is therefore an ordinary shape, not an "
            "exception: PriceDataLoader is a SIGNAL input, never a simulator "
            "dependency, and this notice is where a DSL author first learns "
            "that.\n\n"
            "**When it fires.** The expanded pipeline contains at least one "
            "stream-donor data loader (data_loader category producing the "
            "stream carrier type), no PriceDataLoader anywhere (nested "
            "pipelines, Parallel branches, factory bodies, and named "
            "Pipeline variables included), and the donors agree on a venue. "
            "It never fires when a PriceDataLoader is declared, and never "
            "fires on a pipeline whose loaders name no venue (that shape "
            "raises the executor's own venue error).\n\n"
            "**severity_override='info' (the PARAM_TYPE_CHECK_SKIPPED "
            "precedent, spec 02 §5.3).** This is a notice about platform-"
            "supplied behavior, not a suspected authoring mistake — the "
            "funding-only shape is first-class and correct. Correctness "
            "never depends on this rule; downstream consumers that "
            "serialize only errors+warnings may drop it, by design.\n\n"
            "**Why no suggestion.** The message's second sentence IS the "
            "guidance; a structured fix would wrongly imply the shape needs "
            "fixing."
        ),
        passes=("9",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        severity_override="info",
        applicability=Applicability.NONE,
        recoverable=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # COMPOSITION ADVISORIES (Q-1694, mcp-strategy-view W4 §1.4-§1.7) — minted
    # 2026-09-22. Four SEMANTIC composition facts the type system is silent
    # on by construction (spec 01 scoped it to types + value domains): a
    # forecast's magnitude discarded by a selection sizer, per-branch leverage
    # normalization summed by a concatenator, a fixed per-position weight with
    # no cap, and a boolean mask over a directional signal. Each was measured
    # to validate clean AND compile on 2026-09-22 (`4cc05d665`).
    #
    # Three run at the consumer's J-STEP / J-COMPOSER position in
    # interpreter.py (they are facts about a step GIVEN what flows into it);
    # FIXED_WEIGHT_UNCAPPED is a path fact and runs in validator.py's pass 7d.
    # All four read the LIVE registry for their discriminators (a
    # `target_leverage` / `weight_per_position` / `max_leverage` parameter, an
    # `output_domain`), with one documented per-component anchor set each
    # where no registry declaration isolates the concept — the
    # UNIVERSE_MASK_APPLIERS / _THRESHOLD_ARM_COMPONENT precedent.
    # ═══════════════════════════════════════════════════════════════════════
    Rule(
        code="FORECAST_MAGNITUDE_DISCARDED",
        category=RuleCategory.SUSPICIOUS,
        summary="A selection sizer receives a ForecastSeries — the conviction is discarded.",
        message_template=(
            "'{step}' sizes by membership, but it receives a ForecastSeries "
            "from '{producer}' — the forecast's magnitude (conviction) is "
            "discarded."
        ),
        template_params=("step", "producer"),
        explain=(
            "ForecastSeries values carry conviction (a scaled, capped "
            "expected return). Equal-weight, fixed-weight and binary sizers "
            "treat their input as a SELECTION: sign or membership only. "
            "Feeding them a forecast silently throws the magnitude away — "
            "the strategy runs and reports a number, and the number is not "
            "the strategy you wrote. The type system cannot see it: "
            "ForecastSeries subsumes into a SignalSeries demand "
            "(type_compat_meta.json), which is exactly why every seed of "
            "this mistake validates clean.\n\n"
            "**Forecast to weights** goes through ForecastWeightNormalizer "
            "(simple) or VolTargetWeightConverter (vol-targeted). Equal "
            "weighting IS right after a SELECTOR (TopNAssetSelector, a "
            "filter) where membership is the signal — there the incoming "
            "type is a BinarySignal and this rule does not fire.\n\n"
            "**The anchor set.** The selection sizers are a documented "
            "per-component constant in interpreter.py "
            "(`_SELECTION_SIZERS`): the sizers whose registry input_type is "
            "SignalSeries/BinarySignal and whose semantics size by "
            "membership or a fixed weight. The magnitude-using sizers "
            "(VolWeightSizer, RiskBudgetSizer, RiskParityAllocator, "
            "the deprecated StopDistanceRiskSizer, MarketRiskScaler, MarketLeverageScaler, "
            "BetaEstimator) are excluded. Durable form: an "
            '`input_role="selection"` registry declaration on those '
            "components, at which point the constant is deleted."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Replace '{step}' with ForecastWeightNormalizer(target_leverage=...) "
            "or VolTargetWeightConverter(return_vol_slot=...), or threshold "
            "the forecast first (ThresholdCross) if selection is the intent."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        severity_ceiling="warning",
        staged_by="forecast-magnitude-discarded",
        recoverable=True,
    ),
    Rule(
        code="NORMALIZER_BEFORE_CONCAT",
        category=RuleCategory.CORRECTNESS,
        summary="Concatenated branch targets sum past a fully-invested book.",
        message_template=(
            "Branch '{branch}' ends with '{producer}' "
            "(target_leverage={target}), and '{step}' concatenates {n} such "
            "branches — the book's leverage is the SUM of the branches' "
            "targets ({sum}), not the target."
        ),
        template_params=("branch", "producer", "target", "step", "n", "sum"),
        explain=(
            "WeightConcatenator stacks branch weight books side by side, and "
            "it takes WeightSeries branches only — each branch sizes itself "
            "BEFORE the concatenator (a normalizer after it is a "
            "TYPE_MISMATCH: it takes forecasts, not weights). A sizer with "
            "target_leverage inside a branch makes that branch sum to its "
            "own target, so the concatenated book's gross leverage is the "
            "SUM of the branch targets.\n\n"
            "**The discriminator is the fully-invested book.** The DSL "
            "declares no book target, so the rule fires when the branches' "
            "literal targets sum to MORE than 1.0 and no later step re-levels "
            "the book — a step carrying `target_leverage` or `max_leverage` "
            "(LeverageCap) after the concatenator, found on the same path "
            "transparently through nested Pipeline bodies and variables and "
            "never past a later Parallel. Targets that split one book "
            "(0.5 + 0.5, the shipped long-hype-short-alts-index) are the "
            "deliberate per-sleeve design and stay clean; a non-literal "
            "target is no verdict. Any registered component carrying a "
            "`target_leverage` parameter counts as a normalizing branch "
            "(today: EqualWeightSizer, ForecastWeightNormalizer, "
            "VolWeightSizer); a branch ending in a sizer WITHOUT one "
            "(FixedWeightSizer) does not, and never fires this.\n\n"
            "**Staged (`normalizer-before-concat`), born DORMANT.** At the "
            "mint E-LIB0FP read one fire and it was a false positive — the "
            "0.5 + 0.5 library book above. The sum discriminator is what "
            "separates the two."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Each branch sizes to WeightSeries before '{step}'; the book's "
            "gross leverage is the sum of the branch targets ({sum}) — split "
            "the target across the branches (their target_leverage values "
            "summing to the book you want, e.g. 1.0) or cap after '{step}' "
            "with LeverageCap(max_leverage=1.0)."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        staged_by="normalizer-before-concat",
        recoverable=True,
    ),
    Rule(
        code="FIXED_WEIGHT_UNCAPPED",
        category=RuleCategory.SUSPICIOUS,
        summary="A fixed per-position weight with no leverage cap after it.",
        message_template=(
            "'{step}' allocates {weight} per position with no leverage cap "
            "after it — with k open positions the book is {weight}xk of "
            "equity."
        ),
        template_params=("step", "weight"),
        explain=(
            "FixedWeightSizer assigns the same fraction of equity to every "
            "active position, so total exposure scales with how many are "
            "active — weight_per_position=1.0 and five positions is 5x "
            "leverage. Nothing downstream bounds it unless a LeverageCap "
            "does.\n\n"
            "**How the verdict is computed.** Pass 7d walks the TOP-LEVEL "
            "path, transparent through nested Pipeline bodies and named "
            "Pipeline variables (the C11 rule from pass 7b). A position "
            "sizer carrying a `weight_per_position` parameter fires when no "
            "LATER step on the same path is a risk_manager carrying a "
            "`max_leverage` parameter — both discriminators read from the "
            "live registry, never a component name. A Parallel block is a "
            "BOUNDARY: its branches are not walked and the search for a cap "
            "ends there (multiple consumers — the pass-7c conservatism), so "
            "the rule under-reports rather than false-positives.\n\n"
            "**Warning, permanently.** The shape is deliberate when the "
            "maximum position count is known and the product weight x k is "
            "what you want; the remediation is to pair it with a cap anyway "
            "so the bound is written down."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Add LeverageCap(max_leverage=...) after '{step}', or use "
            "EqualWeightSizer(target_leverage=...), which divides the target "
            "by the position count."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        severity_ceiling="warning",
        staged_by="fixed-weight-uncapped",
        recoverable=True,
    ),
    Rule(
        code="CALENDAR_HOLD_NOOP",
        category=RuleCategory.HYGIENE,
        summary="A calendar hold whose window is no longer than the clock it runs on.",
        message_template=(
            "{step}(duration='{duration}') holds nothing on the {clock} clock: "
            "every bar starts a new window, so the weights pass through "
            "unchanged."
        ),
        template_params=("step", "duration", "clock"),
        explain=(
            "A calendar hold (a WEIGHT-to-WEIGHT step carrying a `duration`, "
            "`WeightCadence` today) samples the intended weights at each "
            "calendar boundary and holds them until the next one. When the "
            "window is no longer than the bar period of the clock the step "
            "runs on, every bar is a boundary and the step changes nothing "
            "but the first, partial window: `duration='1d'` on a daily clock "
            "is a daily rebalance, not a hold (Q-2436, 10 stored strategies "
            "read as a Monday rebalance). The strategy still runs correctly, "
            "so this is hygiene, never an error.\n\n"
            "**How the verdict is computed.** Pass 7e reads the clock the "
            "pass-6 walk resolved at the hold's input and compares its bar "
            "period with the shortest length the duration can have ('Nd' is "
            "N days, 'NM' at least 28·N days). It fires when the duration is "
            "at most the period. A duration given through a variable is not "
            "judged."
        ),
        suggestion_template=(
            "Remove the {step} step to rebalance every bar, or use a longer "
            "duration (e.g. '7d') for a slower rebalance than the clock."
        ),
        passes=("7",),
        surfaces=_PY_ONLY,
        ts_mirrored=False,
        ts_absent_reason=(
            "The TS editor validator does not thread a per-step input clock to "
            "a post-walk pass; the warning is advisory and the Python "
            "validator gates every save, so the editor learns of it on save. "
            "Mirror when a measured editor need exists."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
    ),
    Rule(
        code="MASK_ON_DIRECTIONAL_SIGNAL",
        category=RuleCategory.SUSPICIOUS,
        summary="A boolean mask combiner over a signal that can be -1.",
        message_template=(
            "'{step}' converts its inputs to True/False, but branch "
            "'{branch}' ('{producer}') emits -1/0/+1 — the short side "
            "becomes indistinguishable from the long side."
        ),
        template_params=("step", "branch", "producer"),
        explain=(
            "MaskAnd/MaskOr/AndCombinator/OrCombinator are BOOLEAN: every "
            "input becomes True/False before combining, so a -1 (short) and "
            "a +1 (long) both read as True. Combining two directional "
            "signals this way yields a mask with no direction, and the sizer "
            "after it goes long on everything. Use ApplyMask with one "
            "directional source and one filter for entries; the mask "
            "combiners are right for EXITS (non-directional 0/1) and for "
            "boolean universe filters.\n\n"
            "**How the verdict is computed.** The combiner is identified by "
            "its declared `output_domain` of Set{0, 1} on a "
            "signal_composer — registry data, not a name list. A branch "
            "fires when its PRODUCING component declares -1 in its own "
            "output_domain set. ThresholdCross is read one step further: "
            "its declared domain is [-1, 0, 1] regardless of mode, so the "
            "rule consults the effective `mode` parameter and stays silent "
            "for `long_only` (which emits {0, 1}); that precision is what "
            "keeps the shipped library's long_only exit branch quiet.\n\n"
            "**Warning, permanently.** The mask combiners ARE correct for "
            "exit conditions and boolean filters, and the entry/exit intent "
            "is not statically provable. The rule names the risk and the "
            "alternative."
        ),
        passes=("6",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "For directional entries keep one branch as the signal and use "
            'the other as a filter: {{"signal": [...], "filter": [...]}} '
            '-> ApplyMask(score_signal="signal", filter_signal="filter"). '
            "Keep '{step}' for exit conditions and boolean filters."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        recoverable=True,
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # Terminal + declaration completeness (Q-1694, W4 §1.1-§1.3), 2026-09-22.
    #
    # The platform's most-repeated rule — "a pipeline MUST reach the weight
    # carrier" — was prose in seven places and a rule in none. Measured on
    # `4cc05d665`: a pipeline ending at a SignalSeries, at a ForecastSeries or
    # at a Parallel dict validates clean AND compiles, and the only refusal is
    # a keel-api run gate minting a code the catalog does not contain
    # (PIPELINE_NOT_BACKTEST_READY, Q-1698) — after the quota unit is debited,
    # with a bare TypeError.
    #
    # WHY NO RULE EXISTED. The validator is a TYPE-FLOW checker: passes 6/8
    # are the spec-01 judgments, executed as judgment-table rows. "Complete"
    # is not a judgment in that grammar — J-PIPE folds left and has no
    # terminal premise besides the clock check — so a pipeline that ends
    # anywhere is well-typed. DICT_NOT_CONSUMED is the J-REC consumption row
    # and judges the CONSUMER; a record nothing follows has no judge at all.
    #
    # All three are SUSPICIOUS (decision D-b, the UNRESOLVED_UNIVERSE
    # precedent): the editor/WIP contract and the chat persist gate
    # (stream.py:3488 persists only when `valid`) mean an ERROR here would
    # make every intermediate state of an incremental build un-persistable —
    # loader, then signal, then sizer is the journey the product teaches.
    # Category is the TRUTH and severity is policy, so the two terminal codes
    # carry `promote_in_production` and become errors at the run and deploy
    # gates. MISSING_UNIVERSE left this family on 2026-09-23: the founder's
    # ruling (superseding D-d) makes a universe-less strategy an ERROR at
    # every surface. It is a DECLARATION, above the Pipeline — the
    # incremental loader → signal → sizer journey never passes through its
    # absence, so the WIP argument above does not apply to it.
    Rule(
        code="TERMINAL_NOT_WEIGHTS",
        category=RuleCategory.SUSPICIOUS,
        summary="The pipeline does not reach the weight carrier the engines trade.",
        message_template=(
            "Pipeline ends at '{step}' with {actual}; a run needs {carrier} "
            "(the position weights the executor trades)."
        ),
        template_params=("step", "actual", "carrier", "bridge", "examples"),
        explain=(
            "The backtest kernel and the live executor consume the weight "
            "carrier (or the executor's own output): every pipeline must end "
            "there. A pipeline that ends at OHLCV, a signal or a forecast "
            "validates as TYPES and cannot run — the worker fails after the "
            "quota unit is debited.\n\n"
            "**How the verdict is computed.** Pass 7d reads the WALK's "
            "terminal value, never `type_flow[-1]`: the walk records no "
            "TypeFlowEntry for a Parallel, so the type-flow tail of a "
            "dict-terminal pipeline is its last BRANCH step and reads as a "
            "signal (the misreport this rule's twin, "
            "TERMINAL_DICT_NOT_CONSUMED, exists to catch). The carrier and "
            "the executor output are both DERIVED from the generated "
            "type_transitions table, never spelled. A terminal `Any` (an "
            "unresolved signature) is a skip, not a fire, and the check is "
            "suppressed entirely when the walk already emitted a gating "
            "error on the path — a rejected input makes the terminal type "
            "junk and a second verdict on it is noise.\n\n"
            "**The start state is not a fire.** A pipeline that has only "
            "LOADED data — every top-level step a data_loader or a slot op, "
            "e.g. the `PriceDataLoader()` canvas the app seeds for every new "
            "strategy — has computed nothing yet, so there is nothing to "
            "judge; the run gate still refuses it. Nested bodies, Parallels "
            "and variables are not a start state.\n\n"
            "**Warning while authoring, error at the gates.** Every "
            "incremental build passes through this state and the chat "
            "persists an agent edit only when the payload is valid; an error "
            "would make the intermediate steps un-persistable. "
            "production_mode promotes it, so the run and deploy gates refuse "
            "with this code.\n\n"
            "Signals reach weights through a position sizer; forecasts "
            "through ForecastWeightNormalizer or VolTargetWeightConverter; a "
            "Parallel dict needs a composer first. The suggestion names the "
            "bridging CATEGORY from the type-transition table and up to "
            "three real components of it, searched in the full latest "
            "registry."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Add a {bridge} after '{step}' so the last step outputs {carrier} — e.g. {examples}."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        promote_in_production=True,
        # NO severity_ceiling, deliberately: `promote_in_production` is
        # resolved BEFORE the staging cap and AFTER nothing else, so a
        # permanent warning ceiling would make the flag unreachable and the
        # run/deploy gates could never refuse. The ramp's PROMOTED terminal
        # is what arms the gate half (the NONDENSE_TERMINAL_WEIGHTS shape);
        # the SHIPPED severity stays warning at every stage because the cap
        # is a min and SUSPICIOUS's base is warning.
        staged_by="terminal-not-weights",
        recoverable=True,
    ),
    Rule(
        code="TERMINAL_DICT_NOT_CONSUMED",
        category=RuleCategory.SUSPICIOUS,
        summary="The pipeline ends at a Parallel block; a dict is not tradable.",
        message_template=(
            "Pipeline ends at a Parallel block with branches {branches}; a "
            "dict is not a tradable output."
        ),
        template_params=("branches",),
        explain=(
            "A Parallel block emits dict[branch -> result]. Only a composer, "
            "Extract or a slot reader can consume it; when it is the LAST "
            "step nothing does, and the run cannot produce weights.\n\n"
            "**Why DICT_NOT_CONSUMED cannot see this.** That rule is the "
            "J-REC consumption row and fires on the step AFTER the record — "
            "a non-composer following a dict. A record with NO consumer has "
            "no judge, in either engine. DICT_NOT_CONSUMED stays CORRECTNESS "
            "because there a consumer EXISTS and is the wrong kind: a "
            "mistake, not an unfinished state.\n\n"
            "**Warning while authoring, error at the gates.** The composer "
            "is usually the next edit; production_mode promotes it, so the "
            "run and deploy gates refuse."
        ),
        passes=("7",),
        surfaces=_PY_TS,
        ts_mirrored=True,
        suggestion_template=(
            "Follow the Parallel with a composer that joins the branches "
            "(WeightConcatenator for weight branches, a signal composer such "
            'as ApplyMask/Crossover for signals) or Extract("<branch>") to '
            "select one."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        promote_in_production=True,
        # NO severity_ceiling, deliberately: `promote_in_production` is
        # resolved BEFORE the staging cap and AFTER nothing else, so a
        # permanent warning ceiling would make the flag unreachable and the
        # run/deploy gates could never refuse. The ramp's PROMOTED terminal
        # is what arms the gate half (the NONDENSE_TERMINAL_WEIGHTS shape);
        # the SHIPPED severity stays warning at every stage because the cap
        # is a min and SUSPICIOUS's base is warning.
        staged_by="terminal-dict-not-consumed",
        recoverable=True,
    ),
    Rule(
        code="MISSING_UNIVERSE",
        category=RuleCategory.CORRECTNESS,
        summary="No Universe(...) declaration: every strategy must name the assets it trades.",
        message_template=(
            "No Universe(...) declaration: every strategy must name the assets it trades."
        ),
        template_params=(),
        explain=(
            "Universe(...) is the one declaration every runtime reads for "
            "the asset list: a manual basket holds its symbols; criteria "
            "modes are resolved and baked into `resolved` on save. Without "
            "it a data loader falls back to EVERY asset the store holds — "
            "a run nobody asked for — and a deployment has no resolved list "
            "to schedule against.\n\n"
            "**An error, at every surface.** Founder ruling 2026-09-23 "
            '(superseding decision D-d): "it needs a universe, since data '
            "loader falls back to all assets, we mainly don't want to fail "
            "if can be resolved we auto resolve, but if there is not a valid "
            "universe it should fail\". So the validator, compose's dry run, "
            "the editor and the chat persist gate report it as an error; "
            "keel-api's backtest-submit and deploy gates refuse a stored "
            "strategy without one (`MISSING_UNIVERSE`, 422), and "
            "backtest-worker refuses rather than let the loader substitute "
            "every asset. The editor still saves a draft that carries it "
            "(a save never refuses on validation errors), and every new "
            "editor strategy starts with a Universe.\n\n"
            "**What still resolves automatically, and never fails.** Declare "
            "the set and the platform does the rest: a criteria universe "
            "(`top_volume`, `category`) is resolved and baked into "
            "`resolved` on save (UNRESOLVED_UNIVERSE is info before a save, "
            "never an error), and a manual basket of real tickers is baked "
            "as typed. Only a universe that cannot name a tradable set "
            "fails: none at all (this rule), a manual one with no symbols "
            "(INVALID_UNIVERSE), criteria that resolved to nothing "
            "(EMPTY_UNIVERSE), or a basket none of whose tickers exist on "
            "the venue (UNKNOWN_UNIVERSE_SYMBOLS at the run gates).\n\n"
            "**Choosing the set.** Use the assets the user named; otherwise "
            "a criteria universe that fits the idea (`top_volume` for a "
            "liquid cross-section, `category` for a sector); otherwise ask. "
            "Existing live deployments without a universe keep trading — "
            "scheduled evaluation does not re-validate — but deploying or "
            "updating one requires a Universe.\n\n"
            "**Why UNRESOLVED_UNIVERSE could not cover it.** "
            "_validate_universe runs only `if strategy.universe is not "
            "None`, so the 'criteria never resolved' rule is structurally "
            "unreachable for the ABSENT case. Put asset selection in "
            "Universe — never in StoreValue, VolumeUniverseReducer or a "
            "loader's symbols=."
        ),
        passes=("9",),
        # GATE too: keel-api's backtest-submit and deploy guard
        # (utils/universe_validation) mints this code for a stored blob with
        # no universe, from these same templates.
        surfaces=(Surface.PY_DSL, Surface.TS_EDITOR, Surface.GATE),
        ts_mirrored=True,
        suggestion_template=(
            'Add Universe(mode="manual", symbols=["BTC", "ETH"], market="perp") '
            "above the Pipeline with the assets to trade — or a criteria "
            'universe, Universe(mode="top_volume", top_n=20, market="perp") / '
            'Universe(mode="category", categories=[...], market="perp"), which '
            "the save resolves and bakes. If the request names no assets, ask."
        ),
        applicability=Applicability.HAS_PLACEHOLDERS,
        staged_by="missing-universe",
        recoverable=True,
    ),
    # The position layer (Q-2448): defined above, appended here.
    *_POSITION_LAYER_RULES,
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
        stage=Stage.PROMOTED,
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
            (
                "2026-08-26",
                "promoted",
                "FLIP to the PROMOTED terminal, straight from DORMANT "
                "(founder-approved 2026-08-26; the S2 companion move named "
                "in projects/fable/q4-customer-focus-2026/dsl-core-engine/"
                "03-nan-weight-seam-deferred.md §4 — the authoring-time "
                "fence closes the universe-mask NaN-exit hole for NEW "
                "strategies while the runtime guard stays deferred by "
                "ruling R1). Evidence at flip: 0 of the 18 library "
                "strategies uses a universe mask (measured, the doc's §1 "
                "blast-radius census), so the shipped corpus is "
                "structurally silent; the rule's own reject fixture folds "
                "expected_at_terminal → shipped expected_warnings and is "
                "the sole trace mover. At PROMOTED the category-derived "
                "severity is warning (SUSPICIOUS) at authoring; "
                "promote_in_production=True makes deploy/production "
                "validation emit it at ERROR — the Contract-D deploy gate "
                "is now armed. The sibling P2 (c) keys stay DORMANT.",
            ),
        ),
    ),
    StagedChange(
        key="soft-unproven-silence",
        kind="flow-shape",
        codes=("VALUE_BOUNDS_ADVISORY",),  # firing-surface RETIREMENT: the
        #                                    eq/sub × unproven × soft row only;
        #                                    the viol × soft row keeps firing
        #                                    under soft-bounds-advisory.
        decision=(
            "Claims model, founder-ratified 2026-08-23 (dsl-type-system "
            "decisions.md item 4; spec 09 §3.4 / "
            "specs/review/09-counterproposal-claims-model.md §2.1 item 3)"
        ),
        stage="post",
        terminal_stage="post",
        target_milestone="M5",
        evidence=_FLOW_FLIP_EVIDENCE,
        states=("pre", "post"),
        log=(
            (
                "2026-08-23",
                "pre",
                "born at 'pre' (today's behavior byte-for-byte: the "
                "unproven-soft row emits VALUE_BOUNDS_ADVISORY gated by "
                "soft-bounds-advisory as before). At 'post' the row is "
                "SILENT — both engines' emission gates divert the verdict "
                "to the staged_outcomes measurement channel (spec 09 §3.4: "
                "the advisory fired on the NORMAL use of the component — "
                "prod census: essentially no ThresholdCross feed is "
                "provably in-interval — so a permanent warning there "
                "trains users to ignore warnings; the viol shape, which "
                "witnesses a real contradiction, keeps firing).",
            ),
            (
                "2026-08-23",
                "post",
                "FLIP to the 'post' terminal — founder-ordered silence-"
                "first sequencing (dsl-type-system decisions.md 2026-08-23 "
                "item 4: the flip lands before the Wave-A1 honesty "
                "widenings so the interim advisory-noise spike on the "
                "most-travelled prod paths never appears). Realized library "
                "fire inventory (dsl_type_harness --tiers library, pre-flip "
                "run 2026-08-23): exactly the ratified C1 class retires — "
                "ai-trading-bot-hyperliquid ×1 / mean-reversion-hyperliquid "
                "×1 / momentum-funding-hyperliquid ×2 (all unproven-shape) "
                "— zero error-tier deltas, 18/18 library strategies stay "
                "valid. Conformance: unproven-shape expectations retire "
                "(fixtures re-pointed/renamed in this commit); the viol "
                "reject (reject_set_outside_interval) keeps firing "
                "unchanged. Schema-1 pins restamped (config 'post' + "
                "retired emissions); 0 schema-0 edits (their recorded "
                "vectors replay pre-key configurations). Armed behavior "
                "CORRECTED at recovery (orchestrator, 2026-08-23): a plain "
                "silent accept, NOT a staged_outcomes diversion — that "
                "channel means 'withheld pending arming', and a flow-shape "
                "flip is not a staged OUTCOME (the shadow-parity invariant, "
                "interpreter_test.py `_assert_shadow_parity`); this row is "
                "RETIRED, not withheld, and the schema-1 trace still "
                "records the edge via `_sink_check`.",
            ),
        ),
    ),
    StagedChange(
        key="role-mismatch-rule",
        kind="severity-ramp",
        codes=("REFINED_ROLE_MISMATCH",),
        decision=(
            "D-1, founder-ratified 2026-08-23 (dsl-type-system decisions.md; "
            "ledger Q-0543) — new coverage, so the CODE rides a severity ramp "
            "while the flow-shape half rides `role-survival`"
        ),
        stage=Stage.PROMOTED,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-08-23",
                "dormant",
                "born dormant with the D-1 amendment. TWO keys govern this "
                "one change by construction, because the catalog forbids a "
                "flow-shape from governing a whole code (a row/flow scope "
                "never does): `role-survival` decides whether the ROLE "
                "INFORMATION EXISTS (soft names survive `norm`), and this "
                "ramp decides whether the resulting verdict EMITS. They are "
                "flipped together and a lockstep test pins that — but note "
                "the coupling is safe in one direction by construction: at "
                "`role-survival` = 'pre', `norm` demotes every soft name, so "
                "no actual can carry a conflicting role into the "
                "`unproven × hard` row and the code is unreachable however "
                "this ramp is set.",
            ),
            (
                "2026-08-23",
                "promoted",
                "ARMED to PROMOTED with the D-1 flip (same-day birth+flip, "
                "the `soft-unproven-silence` precedent). Enumerated fire "
                "inventory below on the `role-survival` entry — the two keys "
                "are flipped together and a lockstep test pins that.",
            ),
        ),
    ),
    StagedChange(
        key="role-survival",
        kind="flow-shape",
        codes=("REFINED_ROLE_MISMATCH",),
        decision=(
            "D-1, founder-ratified 2026-08-23 (dsl-type-system decisions.md "
            "'Founder authorizes the whole open-items register'; ledger "
            "Q-0543; orchestration/claims-model-open-items.md §D-1)"
        ),
        stage="post",
        terminal_stage="post",
        target_milestone="M5",
        evidence=_FLOW_FLIP_EVIDENCE,
        states=("pre", "post"),
        log=(
            (
                "2026-08-23",
                "pre",
                "born at 'pre' (today's behavior byte-for-byte: `norm` still "
                "demotes soft refined names, and REFINED_ROLE_MISMATCH never "
                "fires). At 'post' the D-1 amendment is live in BOTH halves: "
                "(1) a SOFT refined name survives `norm` with any domain "
                "including ⊤ — it is a ROLE plus a CAP, not a law, so there "
                "is nothing for an unproven domain to falsify; HARD names "
                "keep full demotion because they DO assert a law. (2) At a "
                "HARD refined demand whose domain half is `unproven`, an "
                "incompatible declared role on the actual becomes an ERROR. "
                "Why both halves are one key: before (1), "
                "norm('ForecastSeries', ⊤) returned ('SignalSeries', ⊤) — "
                "byte-identical to Clip's output — so the role was destroyed "
                "at exactly the moment (2) would need to read it. Restores "
                "the TRUE verdict the ladder step-4 deletion lost (Q-0543: a "
                "forecast into a position manager's entries — TradeManager "
                "today — is a guaranteed runtime raise) without re-admitting any axiom — it reads declared "
                "names and mints nothing.",
            ),
            (
                "2026-08-23",
                "post",
                "FLIP to 'post' (same-day birth+flip, the "
                "`soft-unproven-silence` precedent). FIRE INVENTORY, measured "
                "before arming — see the commit message for the full table. "
                "The two D-1 keys flip TOGETHER (`role-mismatch-rule` to "
                "PROMOTED) and `catalog_test` pins the lockstep.",
            ),
        ),
    ),
    # ── S5/M5: the constraint-schema-v2 relational arming (spec 02 §2.5;
    # S5 arming spec, R2 ruling 2026-08-26) ───────────────────────────────
    StagedChange(
        key="relational-constraints",
        kind="severity-ramp",
        codes=("PARAM_RELATION_VIOLATION",),
        decision="R2 2026-08-26 / D8",
        stage=Stage.PROMOTED,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-08-26",
                "promoted",
                "born PROMOTED at the S5/M5 arming (founder-delegated ruling "
                "2, S5 spec §1.5): every relation the first adopters declare "
                "mirrors an existing __init__/library ValueError — a program "
                "the new error rejects was never runnable, so error-tier is "
                "strictly EARLIER diagnosis of the same failure, and a "
                "WARNING bake-in would knowingly report deployable-but-"
                "crashing configs at the wrong severity. Zero-flip evidence "
                "(S5 §4 sweep, the claims-model re-measure pattern): the "
                "full stored population — prod + staging strategy heads, the "
                "strategy-library entries, and the conformance _valid corpus "
                "— validated at pre-S5 HEAD and at S5 with FULL issue-set "
                "diffs; zero error-tier verdict flips, including on the two "
                "watch-flagged no-runtime-guard adopters (VolatilityFilter "
                "min_periods≤window; RegimeWeightedBlender min_weight_a≤"
                "max_weight_a). The same run proved the weights-heuristic→"
                "sum_eq handover byte-identical on every subject (§1.3). "
                "Subject counts recorded in the commit; the §4.3 founder-"
                "visibility rule (any flip blocks the land) was satisfied "
                "vacuously.",
            ),
        ),
    ),
    StagedChange(
        key="loader-native-floor",
        kind="severity-ramp",
        codes=("LOADER_FINER_THAN_NATIVE",),
        decision="new-data-loaders spec 05 §2b (D8 principles, founder 2026-09-17)",
        stage=Stage.PROMOTED,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-17",
                "promoted",
                "born PROMOTED with the synth-site clock rows (new-data-loaders "
                "spec 05 §3): the row J-SYNTH.floor fires only for a loader "
                "whose clock_transfer declares a served 'floor', and no "
                "registered loader declared one before this commit — the "
                "funding, open-interest and premium family's new versions "
                "(spec 05 M2) are the first adopters, and a strategy that "
                "asks one of them for a finer-than-native grain was never "
                "runnable (the hourly partition is the only one ingested; "
                "the old interval= knob selected a partition that does not "
                "exist, Q-1496). Error-tier is strictly EARLIER diagnosis of "
                "the same failure, with the one supported repair named. "
                "Protects no pre-existing population, so a bake-in stage "
                "would report deployable-but-crashing shapes at the wrong "
                "severity.",
            ),
        ),
    ),
    StagedChange(
        key="loader-timeframe-unbound",
        kind="severity-ramp",
        codes=("LOADER_TIMEFRAME_UNBOUND",),
        decision="new-data-loaders spec 05 §2 D2 (founder 2026-09-17), write-time half (Q-1510)",
        stage=Stage.PROMOTED,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-17",
                "promoted",
                "born PROMOTED with the synth-site row J-SYNTH.unbound (Q-1510): "
                "a Globals-bound loader with no literal and no "
                "Globals(target_timeframe=...) has had no silent default since "
                "PriceDataLoader v3 (spec 05 D2, a7b42c5fb) and the stream/flow "
                "families were born without one, so every strategy the row "
                "refuses is one the runtime already refuses at step 0 of the "
                "backtest, in the very same sentence. A strategy locked to v2 "
                "resolves its '15min' default and never fires. Error-tier is "
                "strictly EARLIER diagnosis of the same failure; protects no "
                "pre-existing runnable population, so a bake-in stage would "
                "report unrunnable shapes at the wrong severity.",
            ),
        ),
    ),
    StagedChange(
        key="forecast-magnitude-discarded",
        kind="severity-ramp",
        codes=("FORECAST_MAGNITUDE_DISCARDED",),
        decision=(
            "mcp-strategy-view GUIDANCE-ARCHITECTURE-SPEC §3 L4 / decision #25 "
            "(2026-09-22): the seven silent structural mistakes become coded "
            "rules; the catalog's own protocol (a new firing surface is born "
            "through STAGED_CHANGES) governs the arming"
        ),
        stage=Stage.WARNING,
        terminal_stage=Stage.WARNING,
        target_milestone="M5",
        evidence=_WARNING_FLIP_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 composition mint (Q-1694). E-LIB0FP "
                "is CLEAN at the mint — 0 of the 18 library heads fire, once "
                "the uniform-magnitude proof discharges the two "
                "ConstantForecast(10.0) -> RegimeGate -> EqualWeightSizer "
                "baskets (regime-gated-crypto-index, regime-rotation-crypto), "
                "whose flowing domain is Set{0.0, 10.0} and therefore carries "
                "no conviction to discard. E-CORPUS is NOT: 25 of the 239 "
                "committed conformance fixtures pair a ForecastScaler (domain "
                "top) with EqualWeightSizer and would gain the warning, and "
                "each of those fixtures is a PINNED golden-trace subject. A "
                "new always-on code that moves a pinned trace is only legal "
                "through a flip record + an m3-flip ALLOWLIST entry + a "
                "re-pin (traces/ALLOWLIST.md, spec 06 §2.4 / spec 04 §2.6), "
                "and a flip record is keyed on a STAGED_CHANGES id — so the "
                "ramp is what MAKES the arming expressible. Flip-on is its "
                "own PR: sweep the 25 fixtures' expected_warnings, re-pin the "
                "schema-1 traces, record the shape.",
            ),
            (
                "2026-09-22",
                "warning",
                "flip record: DORMANT -> WARNING. The four criteria, "
                "re-measured at the flip on the same tree. E-LIB0FP = 0 fires "
                "over all 18 shipped library heads (the uniform-magnitude proof "
                "holds: the two ConstantForecast(10.0) baskets stay silent). "
                "E-CONF = the reject fixture fires and all three controls stay "
                "clean (the normalizer fix, the post-threshold selection shape, "
                "the uniform-magnitude domain). E-CORPUS = 26 conformance "
                "subjects fire, every one a real instance of the shape (a "
                "ForecastScaler at domain top feeding a membership sizer); they "
                "are swept into shipped expected_warnings in this commit and "
                "their schema-1 traces re-pinned under the m3-flip entry "
                "staged:forecast-magnitude-discarded. E-PARITY = both harnesses "
                "assert the same 26 in the shipped configuration. Terminal is "
                "WARNING and permanent: the rule names a discarded conviction, "
                "not a crash, and the deliberate selection-from-forecast design "
                "is exactly what the domain proof separates out.",
            ),
        ),
    ),
    StagedChange(
        key="fixed-weight-uncapped",
        kind="severity-ramp",
        codes=("FIXED_WEIGHT_UNCAPPED",),
        decision=(
            "mcp-strategy-view GUIDANCE-ARCHITECTURE-SPEC §3 L4 / decision #25 "
            "(2026-09-22): the seven silent structural mistakes become coded "
            "rules; the catalog's own protocol governs the arming"
        ),
        stage=Stage.WARNING,
        terminal_stage=Stage.WARNING,
        target_milestone="M5",
        evidence=_WARNING_FLIP_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 composition mint (Q-1694). E-LIB0FP "
                "is clean (no library head uses a fixed per-position weight); "
                "E-CORPUS is not — 4 committed conformance fixtures "
                "(VALUE_DOMAIN_MISMATCH x3, VALUE_DOMAIN_UNPROVEN x1) end at "
                "a FixedWeightSizer with no LeverageCap and are pinned "
                "golden-trace subjects. Same reason as "
                "forecast-magnitude-discarded: moving a pinned trace needs a "
                "flip record, and a flip record needs a staged key.",
            ),
            (
                "2026-09-22",
                "warning",
                "flip record: DORMANT -> WARNING. E-LIB0FP = 0 (no shipped "
                "library head uses a fixed per-position weight). E-CONF = the "
                "reject fires and the three controls stay clean (the cap, the "
                "equal-weight twin, the Parallel boundary). E-CORPUS = 5 "
                "conformance subjects fire (the reject plus the four "
                "VALUE_DOMAIN_* subjects that end at an uncapped "
                "FixedWeightSizer), swept into shipped expected_warnings here "
                "with their traces re-pinned under the m3-flip entry "
                "staged:fixed-weight-uncapped. E-PARITY = both harnesses agree. "
                "Terminal is WARNING and permanent: the shape is deliberate "
                "when the maximum position count is known, and the rule's job "
                "is to make that bound written down rather than assumed.",
            ),
        ),
    ),
    # ── Composition advisories (Q-1694, W4 §1.5), 2026-09-22 ──────────────
    StagedChange(
        key="normalizer-before-concat",
        kind="severity-ramp",
        codes=("NORMALIZER_BEFORE_CONCAT",),
        decision=(
            "mcp-strategy-view KICKOFF.md decision D-c (2026-09-22): "
            "NORMALIZER_BEFORE_CONCAT is born DORMANT and flips on with the "
            "four warning-flip evidences — the catalog's protocol, no exception"
        ),
        stage=Stage.WARNING,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 composition mint (Q-1694). The "
                "verdict is CORRECTNESS — a book at N x the declared target "
                "leverage is a wrong number the user reads as their own "
                "strategy — but E-LIB0FP at the mint is ONE fire and it is a "
                "FALSE POSITIVE: long-hype-short-alts-index deliberately "
                "pairs two ForecastWeightNormalizer(target_leverage=0.5) "
                "branches under a WeightConcatenator so the book lands at "
                "1.0. Flipping on therefore needs the per-sleeve design "
                "distinguished (a declared intent, or the branch targets "
                "summing to a declared book target) before E-LIB0FP can "
                "read zero; until then the code emits nothing and its "
                "reject fixture pins the fire under expected_at_terminal.",
            ),
            (
                "2026-09-23",
                "warning",
                "flip record: DORMANT -> WARNING, after the refinement the "
                "mint named (agent-surface-cleanup spec 04 §2.6, review 06 "
                "M-1): the verdict is now the fully-invested book — the "
                "concatenated normalizing branches' literal targets sum to "
                "MORE than 1.0 and no later step re-levels the book (a step "
                "carrying target_leverage or max_leverage on the downstream "
                "tail, transparent through nested bodies and variables, "
                "stopping at a later Parallel); a non-literal target is no "
                "verdict. E-LIB0FP = 0 fires over all 18 shipped library "
                "heads — long-hype-short-alts-index's 0.5 + 0.5 book is clean "
                "by construction — and expected_issues.json is unchanged. "
                "E-CONF = both rejects fire (1.0 + 1.0; a cap only inside a "
                "later Parallel) and every control stays clean (the 0.5 + 0.5 "
                "book, LeverageCap after, the cap through a variable, one "
                "normalizing branch, unnormalized branches). E-CORPUS = 2 "
                "conformance subjects, both the rule's own rejects, folded "
                "into shipped expected_warnings with their schema-1 traces "
                "re-pinned under the m3-flip entry "
                "staged:normalizer-before-concat. E-PARITY = both harnesses "
                "agree in the shipped configuration. WARNING is a stage cap: "
                "the terminal stays PROMOTED, which needs E-WINDOW and E-GATE.",
            ),
        ),
    ),
    # ── Terminal + declaration completeness (Q-1694, W4 §1.1-§1.3) ────────
    # All three are born DORMANT, and the reason is mechanical rather than a
    # judgement about the rules: each fires on a LARGE, PINNED slice of the
    # committed corpus, and moving a pinned golden trace is legal only
    # through a flip record + an m3-flip ALLOWLIST entry + a re-pin
    # (traces/ALLOWLIST.md, spec 06 §2.4 / spec 04 §2.6). A flip record is
    # KEYED ON A STAGED_CHANGES ID, so the ramp is what makes the arming
    # expressible at all — the same argument the Q-1694 composition mint
    # made, at ten times the population. Each log carries its own measured
    # counts; arming is a separate flip PR that sweeps the fixtures, re-pins
    # the schema-1 traces and records the shape.
    StagedChange(
        key="terminal-not-weights",
        kind="severity-ramp",
        codes=("TERMINAL_NOT_WEIGHTS",),
        decision=(
            "mcp-strategy-view KICKOFF.md decision D-b (2026-09-22): the "
            "terminal completeness codes take the UNRESOLVED_UNIVERSE "
            "precedent — SUSPICIOUS at WARNING with promote_in_production, "
            "the mode the catalog already uses for a rule that must not "
            "block a work-in-progress save"
        ),
        stage=Stage.WARNING,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 terminal mint (Q-1694). E-LIB0FP is "
                "CLEAN: 0 of the 18 shipped library heads end short of the "
                "weight carrier — all 18 declare a sizer. E-CORPUS is not: "
                "128 of the 257 committed conformance fixtures end at a "
                "signal (59), a forecast (58), an unresolved Any (4), a "
                "BinarySignal (3), a GlobalSeries (2), a dict (1) or OHLCV "
                "(1), and every one of them is a PINNED golden-trace "
                "subject. That is not a defect in the corpus — a fixture for "
                "PARAM_INVALID_OPTION has no reason to carry a sizer — which "
                "is exactly why the arming has to sweep them deliberately "
                "rather than ride a mint. 7 further fixtures already carry a "
                "gating walk error and are suppressed by construction.",
            ),
            (
                "2026-09-23",
                "warning",
                "flip record: DORMANT -> WARNING (agent-surface-cleanup review "
                "06 §3.2 #2, Gate-1 approved 2026-09-23: arm the minted rules "
                "instead of keeping their prose). E-LIB0FP = 0 fires over all "
                "18 shipped library heads; expected_issues.json unchanged. "
                "E-CONF = both rejects fire (signal and forecast terminals); "
                "the sizer terminal, the gated-walk suppression and the "
                "START STATE stay silent. The arming needed one refinement, "
                "found by checking legitimate use before arming: a pipeline "
                "that has only LOADED data (every top-level step a "
                "data_loader or a slot op) is the start state — the blank "
                "canvas keel-app seeds for every new strategy and the first "
                "step of every incremental build — and firing there would be "
                "a warning on every user's first render; the run gate still "
                "refuses it (PIPELINE_NOT_BACKTEST_READY). E-CORPUS = 144 "
                "conformance subjects end short of the carrier, folded into "
                "shipped expected_warnings with their schema-1 traces "
                "re-pinned under the m3-flip entry staged:terminal-not-weights "
                "(31 schema-0 anchors join PINNED_ANCHOR_DRIFT). E-PARITY = "
                "both harnesses agree. WARNING is a stage cap: the PROMOTED "
                "terminal (the gate half) needs E-WINDOW and E-GATE.",
            ),
        ),
    ),
    StagedChange(
        key="terminal-dict-not-consumed",
        kind="severity-ramp",
        codes=("TERMINAL_DICT_NOT_CONSUMED",),
        decision=(
            "mcp-strategy-view KICKOFF.md decision D-b (2026-09-22): the "
            "UNRESOLVED_UNIVERSE precedent, SUSPICIOUS at WARNING with "
            "promote_in_production"
        ),
        stage=Stage.WARNING,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 terminal mint (Q-1694). BOTH "
                "evidences are clean at the mint: 0 of the 18 library heads "
                "and 0 of the 257 conformance fixtures end at a Parallel — "
                "measured, not assumed. It is born dormant anyway, on its "
                "TWIN's schedule: TERMINAL_NOT_WEIGHTS and this code are one "
                "verdict split by the shape of the terminal value (a record "
                "or not), they are read from the same walk terminal in the "
                "same pass, and arming one without the other would leave the "
                "dict half of 'the pipeline does not reach weights' silent "
                "while the signal half fires. Its own reject fixture pins "
                "the fire under expected_at_terminal.",
            ),
            (
                "2026-09-23",
                "warning",
                "flip record: DORMANT -> WARNING on its twin's schedule (review "
                "06 §3.2 #2, 2026-09-23). E-LIB0FP = 0 over the 18 library "
                "heads; E-CORPUS = 1 (its own reject — no other fixture ends "
                "at a Parallel), folded into shipped expected_warnings under "
                "the m3-flip entry staged:terminal-dict-not-consumed; E-CONF = "
                "the reject fires, the Parallel-then-composer accept is clean "
                "and TERMINAL_NOT_WEIGHTS does not also fire on the dict "
                "terminal; E-PARITY = both harnesses agree.",
            ),
        ),
    ),
    StagedChange(
        key="missing-universe",
        kind="severity-ramp",
        codes=("MISSING_UNIVERSE",),
        decision=(
            "founder ruling 2026-09-23 (agent-surface-cleanup decisions.md, "
            "superseding mcp-strategy-view KICKOFF.md D-d): a strategy "
            "without a valid universe FAILS — CORRECTNESS at error on every "
            "surface; criteria and real-ticker manual universes still "
            "resolve on save and never fail"
        ),
        stage=Stage.PROMOTED,
        terminal_stage=Stage.PROMOTED,
        target_milestone="M5",
        evidence=_ERROR_PROMOTION_EVIDENCE,
        log=(
            (
                "2026-09-22",
                "dormant",
                "born dormant at the W4 declaration mint (Q-1694). E-LIB0FP "
                "is CLEAN: 0 of the 18 shipped library heads omit "
                "Universe(...). E-CORPUS is the largest in the catalog's "
                "history: 189 of the 257 committed conformance fixtures "
                "carry no universe key, every one a pinned golden-trace "
                "subject. W4 §1.3 offered a scripted always-on sweep as the "
                "honest alternative; it is not available, because an "
                "always-on mint that moves 189 pinned traces needs a flip "
                "record and a flip record needs a staged key. So the ramp is "
                "the mechanism, and the sweep happens at the flip. Note the "
                "asymmetry with the two terminal codes: this one does NOT "
                "carry promote_in_production (D-d), so arming it can never "
                "make the run gate refuse a strategy it accepts today.",
            ),
            (
                "2026-09-23",
                "warning",
                "flip record: DORMANT -> WARNING (agent-surface-cleanup review "
                "06 M-2, the preferred fix: the compose copy said an omitted "
                "universe takes a default, which nothing implements — a "
                "universe-less strategy validated clean with no issue at "
                "all). E-LIB0FP = 0 fires over all 18 shipped library heads "
                "(every head declares a Universe); expected_issues.json "
                "unchanged. E-CONF = the reject fires at the universe "
                "location, the manual-universe accept is clean, a declared "
                "but unresolved universe stays UNRESOLVED_UNIVERSE's, and "
                "production_mode does not promote it. E-CORPUS = 199 "
                "conformance subjects carry no universe key, folded into "
                "shipped expected_warnings under the m3-flip entry "
                "staged:missing-universe (32 schema-0 anchors join "
                "PINNED_ANCHOR_DRIFT). E-PARITY = both harnesses agree. "
                "Checked against legitimate use first: the app's blank canvas "
                "always carries a universe, no product surface validates a "
                "pipeline with its universe held elsewhere, and keel-api's "
                "tolerance of a missing universe is for legacy rows, which "
                "now read the warning they always deserved. Terminal is this "
                "WARNING, permanently (D-d).",
            ),
            (
                "2026-09-23",
                "promoted",
                "flip record: WARNING -> PROMOTED, terminal WARNING -> "
                "PROMOTED, category SUSPICIOUS -> CORRECTNESS, the permanent "
                "warning ceiling dropped. E-GATE = the founder ruling, "
                'verbatim: "it needs a universe, since data loader falls '
                "back to all assets, we mainly don't want to fail if can be "
                "resolved we auto resolve, but if there is not a valid "
                'universe it should fail" (superseding D-d). E-WINDOW = '
                "waived by that ruling: the window exists to find FALSE "
                "positives, and the ruling defines every fire (no Universe "
                "at all) as a true one. E-LIB0FP = 0 fires over all 18 "
                "shipped library heads (each declares a Universe); "
                "expected_issues.json unchanged. E-CONF = the reject is an "
                "error at the universe location; the manual accept is "
                "clean; a declared criteria universe that is not yet baked "
                "stays UNRESOLVED_UNIVERSE (warning; info pre-save), never "
                "this. E-CORPUS = the 207 conformance subjects that carried "
                "no universe (written for other rules) each gained a minimal "
                'Universe(mode="manual", symbols=["BTC", "ETH"], '
                'market="perp") and shed the folded MISSING_UNIVERSE '
                "warning, so every one still tests what it was written for; "
                "the rule's own reject is now an error. E-PARITY = both "
                "harnesses agree, the schema-1 corpus re-pinned under the "
                "m3-flip entry staged:missing-universe (promoted). Run and "
                "deploy gates: keel-api refuses a stored strategy without a "
                "universe (MISSING_UNIVERSE 422) and backtest-worker refuses "
                "instead of letting the loader fall back to every asset; "
                "scheduled evaluation of an EXISTING deployment is not "
                "re-validated, so universe-less deployments keep trading.",
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
