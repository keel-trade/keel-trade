"""The judgment-table CONTENT (artifact A3 — spec 03 §2.3, spec 02 §4.1).

This module is the single Python source of truth for ``judgment_tables.json``:
the per-step-kind ROWs both interpreters (Python here, TS in spec 04) execute —
mode of each check, premise lookups, side conditions, the FOUR
recovery rows (spec 03 §3.5), staged-variant rows whose mandatory ``staged_by``
resolves in ``STAGED_CHANGES`` (R-5), and the exemption/skip rows. The
``flow_config`` block is NOT stored here — it is derived at regen time from the
three flow-shape ``STAGED_CHANGES`` stages by ``write_judgment_tables`` (a
hand-edited value is rejected by freshness).

CONTENT is spec 03's; the artifact plumbing/paths/freshness are spec 02's
(``write_judgment_tables`` in ``dsl/fixtures/loader.py``). The interpreter walk
that consumes these rows is T-M2b-3; this module and its writer are M2b.
"""

from __future__ import annotations


# ── Closed vocabularies (spec 03 §2.3) — the test enumerates against these ───
JUDGMENTS = (
    "J-STEP",
    "J-PIPE",
    "J-INST",
    "J-REC",
    "J-PROJ",
    "J-COMPOSER",
    "J-STORE",
    "J-STOREVALUE",
    "J-LOAD",
    "J-SLOTREAD",
    "J-LIT",
    "J-PARAM",
)
MODES = ("synthesis", "checking")
RELATION_MODES = ("strict", "transition", "slot", None)
STEP_KINDS = (
    "component",
    "composer",
    "slot_store",
    "slot_store_value",
    "slot_load",
    "slot_extract",
    "parallel",
    "nested_pipeline",
    "variable_ref",
)
PREMISES = (
    "current-is-record",
    "key-in-record",
    "slot-in-sigma",
    "input-satisfies",
    "bound-satisfies",
    "field-satisfies",
    "literal-in-domain",
    "literal-type-matches",
    "literal-type-known",
    "literal-finite",
    "record-consumed",
    "category-plausible",
    "signature-resolved",
    "composer-key-member",
    "slot-ref-in-sigma",
    "slot-used",
    "base-compatible",
    "branches-nonempty",  # J-REC n≥1 structural precondition (spec 01 §5.1 m7)
)
EMIT_TERMS = (
    "expected",
    "actual",
    "step",
    "context",
    "component",
    "param",
    "value",
    "options",
    "slot",
    "stored",
    "branches-sorted",
    "location",
    "failing-bound",
    # F5.1 faithfulness terms (review MAJOR-1): the walk-state derivations the
    # live emission sites actually compose, so rows describe the REAL kwargs.
    "key",  # the Extract projection key (J-PROJ)
    "category",  # the step's registry category (transition advisory)
    "output",  # transition_key(declared output)
    "prev-output",  # transition_key(previous output)
    "expected-outputs",  # the transition row's allowed output keys
    "extra-keys-sorted",  # composer dict-param keys minus branch names, sorted
    "param-type",  # the declared (non-isinstance-checkable) param type
    "suggestion-bridge",  # the dynamic insert-a-converter suggestion (TYPE_MISMATCH)
    # Envelope machine channels (spec 05 §3.1 #7/#11, §3.3) — the typed
    # judgments populate these at the subsumption-relation type edges: ``path``
    # is the machine step path (``"/".join(path)`` == spec 04's trace node id)
    # on every ≤-relation type outcome; ``provenance`` is the slot blame chain
    # (store → read) on the slot type outcome.
    "path",
    "provenance",
)
_EMIT_TERM_PREFIXES = ("detail-template:", "suggestion-template:")

# ── Emission string templates (spec 03 §2.3 F5.1/F5.2) ───────────────────────
# Referenced from rows via "suggestion-template:<id>" / "detail-template:<id>"
# emit-kwarg terms. Literal suggestion/detail strings are TABLE DATA, never
# engine code; a dict value is a documented multi-shape family keyed by the
# emitting site's shape discriminator (role/homogeneous, below-min/above-max).
EMIT_TEMPLATES: dict[str, str | dict[str, str]] = {
    "parallel-before-composer": "Place a Parallel block before '{component}'.",
    "composer-step-display": "Composer '{component}'",
    "extract-step-display": "'Extract'",
    "composer-field-fix": {
        "role": (
            "Change branch '{branch}' to end with a step that outputs "
            "{expected_display}, or point '{role}' at a different branch."
        ),
        "homogeneous": (
            "Change branch '{branch}' to end with a step that outputs {expected_display}."
        ),
    },
    "composer-field-detail": {
        "role": (
            "Composer '{composer}' role '{role}' references branch '{branch}' "
            "which outputs '{actual}', but expects {expected_display}."
        ),
        "homogeneous": (
            "Composer '{composer}' expects every Parallel branch to output "
            "{expected_display}, but branch '{branch}' outputs '{actual}'."
        ),
    },
    "param-nonfinite-fix": "Change {param} to a finite number.",
    "param-nonfinite-detail": (
        "Parameter '{param}' of '{component}' has invalid value {value!r}. "
        "Infinity and NaN are not allowed."
    ),
    "param-range-fix": "Change {param} to a value in range [{min}, {max}].",
    "param-range-detail": {
        "below-min": "Parameter '{param}' of '{component}' value {value} below minimum {min}.",
        "above-max": "Parameter '{param}' of '{component}' value {value} above maximum {max}.",
    },
}


def emit_template(template_id: str, shape: str | None = None) -> str:
    """Resolve an EMIT_TEMPLATES entry (multi-shape families need ``shape``)."""
    entry = EMIT_TEMPLATES[template_id]
    return entry[shape] if isinstance(entry, dict) else entry


SKIP_TOKENS = (
    "any-frontier",
    "data-loader",
    "record-consumption-exempt",
    "record-strict-skip",
    "variable-ref",
    "nonetype-sentinel",
    "unresolvable-binding",
    "value-kind:int-float",
    "value-kind:str",
    "non-adjacent-record",
)
RECOVERY_TYPES = ("any", "unchanged")

# The three flow-shape STAGED_CHANGES ids — the ONLY flow_config keys (§2.3).
FLOW_CONFIG_KEYS = ("extract-projection", "scheme-synthesis", "composer-record-reach")


def emit_term_ok(term: str) -> bool:
    return term in EMIT_TERMS or term.startswith(_EMIT_TERM_PREFIXES)


# ═══════════════════════════════════════════════════════════════════════════
# Per-step-kind judgment ROWs (the 16 re-based codes + staged + recovery rows)
# ═══════════════════════════════════════════════════════════════════════════

STEP_KIND_ROWS: dict[str, list[dict]] = {
    "component": [
        # R1 recovery: unresolved signature — §3.5. No emission at this row
        # (the UNKNOWN_COMPONENT/lock outcomes fire in earlier passes).
        {
            "id": "J-STEP.unresolved-signature",
            "judgment": "J-STEP",
            "mode": "checking",
            "relation_mode": None,
            "premise": "signature-resolved",
            "emit_kwargs": {},
            "on_fail_recovery": {"type": "any"},
        },
        # Pass-6 strict input check → TYPE_MISMATCH (J-STEP ⇐).
        {
            "id": "J-STEP.input-check",
            "judgment": "J-STEP",
            "mode": "checking",
            "relation_mode": "strict",
            "premise": "input-satisfies",
            "on_fail": {"code": "TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-bridge",
                "context": "context",
                "step": "step",
                "expected": "expected",
                "actual": "actual",
                "path": "path",  # envelope machine step path (spec 05 §3.3)
            },
            "skip_when": ["any-frontier", "record-strict-skip"],
        },
        # super-carrier retirement (staged): pre = base passes (oracle 1
        # carrier fallback), post = base error. Row-level staged_by (R-5).
        # Fires through the input-check emission site — identical kwargs.
        {
            "id": "J-STEP.carrier-fallback",
            "judgment": "J-STEP",
            "mode": "checking",
            "relation_mode": "strict",
            "premise": "base-compatible",
            "on_fail": {"code": "TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-bridge",
                "context": "context",
                "step": "step",
                "expected": "expected",
                "actual": "actual",
                "path": "path",  # mirrors J-STEP.input-check (shared host site)
            },
            "staged": {
                "pre": "base-passes",
                "post": "base-error",
                "staged_by": "carrier-fallback-retirement",
            },
        },
        # Pass-6 transition advisory (side condition) → TRANSITION_OUTPUT_MISMATCH.
        {
            "id": "J-STEP.transition-advisory",
            "judgment": "J-STEP",
            "mode": "checking",
            "relation_mode": "transition",
            "premise": "category-plausible",
            "on_fail": {"code": "TRANSITION_OUTPUT_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "step": "step",
                "category": "category",
                "output": "output",
                "prev_output": "prev-output",
                "expected_outputs": "expected-outputs",
            },
        },
        # Synthesis shape (staged flow key scheme-synthesis): pre = declared
        # output; post = J-INST / J-LIT (spec 03 §3.4(2)). A post-state bound
        # failure emits TYPE_MISMATCH with the bound as the expected.
        {
            "id": "J-STEP.synthesize",
            "judgment": "J-INST",
            "mode": "synthesis",
            "relation_mode": "strict",
            "premise": "bound-satisfies",
            "on_fail": {"code": "TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "context": "context",
                "step": "step",
                "expected": "expected",
                "actual": "actual",
                "path": "path",  # envelope machine step path (spec 05 §3.3)
            },
            "staged": {
                "pre": "declared-output",
                "post": "inst-lit",
                "staged_by": "scheme-synthesis",
            },
        },
        # ── Pass-5 J-PARAM rows (the 5 re-based param checks) ──────────────
        {
            "id": "J-PARAM.type-match",
            "judgment": "J-PARAM",
            "mode": "checking",
            "relation_mode": None,
            "premise": "literal-type-matches",
            "on_fail": {"code": "PARAM_TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "param": "param",
                "component": "component",
                "expected": "expected",
                "actual": "actual",
            },
        },
        {
            "id": "J-PARAM.type-check-skipped",
            "judgment": "J-PARAM",
            "mode": "checking",
            "relation_mode": None,
            "premise": "literal-type-known",
            "on_fail": {"code": "PARAM_TYPE_CHECK_SKIPPED"},
            "emit_kwargs": {
                "location": "location",
                "param": "param",
                "component": "component",
                "type": "param-type",
            },
            "skip_when": ["variable-ref"],
        },
        {
            "id": "J-PARAM.finite",
            "judgment": "J-PARAM",
            "mode": "checking",
            "relation_mode": None,
            "premise": "literal-finite",
            "on_fail": {"code": "PARAM_INVALID_VALUE"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-template:param-nonfinite-fix",
                "detail": "detail-template:param-nonfinite-detail",
            },
        },
        # The failing-bound channel selects the detail shape (below-min /
        # above-max) — the multi-shape key of the param-range-detail family.
        {
            "id": "J-PARAM.range",
            "judgment": "J-PARAM",
            "mode": "checking",
            "relation_mode": None,
            "premise": "literal-in-domain",
            "on_fail": {"code": "PARAM_OUT_OF_RANGE"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-template:param-range-fix",
                "detail": "detail-template:param-range-detail",
            },
        },
        {
            "id": "J-PARAM.option",
            "judgment": "J-PARAM",
            "mode": "checking",
            "relation_mode": None,
            "premise": "literal-in-domain",
            "on_fail": {"code": "PARAM_INVALID_OPTION"},
            "emit_kwargs": {
                "location": "location",
                "param": "param",
                "component": "component",
                "value": "value",
                "options": "options",
            },
        },
    ],
    "composer": [
        # Composer key/field checks (staged reach: composer-record-reach).
        {
            "id": "J-COMPOSER.key-member",
            "judgment": "J-COMPOSER",
            "mode": "checking",
            "relation_mode": None,
            "premise": "composer-key-member",
            "on_fail": {"code": "COMPOSER_KEY_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "composer": "component",
                "param": "param",
                "extra": "extra-keys-sorted",
                "branches": "branches-sorted",
            },
            "staged": {
                "pre": "adjacency-only",
                "post": "flow-wide",
                "staged_by": "composer-record-reach",
            },
        },
        # The role/homogeneous composer_inputs shapes select the template
        # family member (the catalog's documented multi-shape pattern).
        {
            "id": "J-COMPOSER.field-check",
            "judgment": "J-COMPOSER",
            "mode": "checking",
            "relation_mode": "strict",
            "premise": "field-satisfies",
            "on_fail": {"code": "COMPOSER_INPUT_TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-template:composer-field-fix",
                "detail": "detail-template:composer-field-detail",
            },
            "staged": {
                "pre": "adjacency-only",
                "post": "flow-wide",
                "staged_by": "composer-record-reach",
            },
        },
        {
            "id": "J-COMPOSER.dict-expected",
            "judgment": "J-COMPOSER",
            "mode": "checking",
            "relation_mode": None,
            "premise": "current-is-record",
            "on_fail": {"code": "DICT_INPUT_EXPECTED"},
            "emit_kwargs": {
                "location": "location",
                "suggestion": "suggestion-template:parallel-before-composer",
                "step": "detail-template:composer-step-display",
                "actual": "actual",
            },
        },
    ],
    "slot_store": [
        {
            "id": "J-STORE.pass-through",
            "judgment": "J-STORE",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "signature-resolved",
            "emit_kwargs": {},
        },
    ],
    "slot_store_value": [
        {
            "id": "J-STOREVALUE.literal",
            "judgment": "J-STOREVALUE",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "literal-type-known",
            "emit_kwargs": {},
            "skip_when": ["value-kind:int-float", "value-kind:str", "nonetype-sentinel"],
        },
    ],
    "slot_load": [
        # R4 recovery: Load of an absent slot — τ unchanged, journaled.
        {
            "id": "J-LOAD.slot-present",
            "judgment": "J-LOAD",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "slot-in-sigma",
            "on_fail": {"code": "SLOT_NOT_FOUND"},
            "emit_kwargs": {"location": "location", "slot": "slot"},
            "on_fail_recovery": {"type": "unchanged"},
        },
        # J-SLOTREAD: slot-mode subsumption at the read site.
        {
            "id": "J-SLOTREAD.ref-present",
            "judgment": "J-SLOTREAD",
            "mode": "checking",
            "relation_mode": None,
            "premise": "slot-ref-in-sigma",
            "on_fail": {"code": "SLOT_REF_NOT_FOUND"},
            "emit_kwargs": {
                "location": "location",
                "component": "component",
                "param": "param",
                "slot": "slot",
            },
        },
        {
            "id": "J-SLOTREAD.type-check",
            "judgment": "J-SLOTREAD",
            "mode": "checking",
            "relation_mode": "slot",
            "premise": "slot-in-sigma",
            "on_fail": {"code": "SLOT_TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "component": "component",
                "param": "param",
                "expected": "expected",
                "slot": "slot",
                "stored": "stored",
                "path": "path",  # envelope machine step path (spec 05 §3.3)
                "provenance": "provenance",  # slot blame chain (store → read)
            },
            "skip_when": ["nonetype-sentinel"],
        },
        # D4 slot-sib narrowing (staged): pre = oracle-3-exact leniency
        # (both bare NewTypes), post = base error. Row-level staged_by (R-5).
        # Fires through the type-check emission site — identical kwargs.
        {
            "id": "J-SLOTREAD.sib-narrowing",
            "judgment": "J-SLOTREAD",
            "mode": "checking",
            "relation_mode": "slot",
            "premise": "base-compatible",
            "on_fail": {"code": "SLOT_TYPE_MISMATCH"},
            "emit_kwargs": {
                "location": "location",
                "component": "component",
                "param": "param",
                "expected": "expected",
                "slot": "slot",
                "stored": "stored",
                "path": "path",  # mirrors J-SLOTREAD.type-check (shared host site)
                "provenance": "provenance",
            },
            "staged": {
                "pre": "lenient-accept",
                "post": "base-error",
                "staged_by": "d4-slot-sib-narrowing",
            },
        },
    ],
    "slot_extract": [
        # R2 recovery: Extract with non-record current → DICT_INPUT_EXPECTED.
        # The suggestion renders from the RULE's catalog suggestion_template.
        {
            "id": "J-PROJ.record-ness",
            "judgment": "J-PROJ",
            "mode": "checking",
            "relation_mode": None,
            "premise": "current-is-record",
            "on_fail": {"code": "DICT_INPUT_EXPECTED", "severity_context": "extract"},
            "emit_kwargs": {
                "location": "location",
                "step": "detail-template:extract-step-display",
                "actual": "actual",
            },
            "on_fail_recovery": {"type": "any"},
        },
        # R3 recovery: Extract with missing key → EXTRACT_MISSING_KEY.
        {
            "id": "J-PROJ.key-member",
            "judgment": "J-PROJ",
            "mode": "checking",
            "relation_mode": None,
            "premise": "key-in-record",
            "on_fail": {"code": "EXTRACT_MISSING_KEY"},
            "emit_kwargs": {
                "location": "location",
                "key": "key",
                "branches": "branches-sorted",
            },
            "on_fail_recovery": {"type": "any"},
            "staged": {
                "pre": "adjacency-only",
                "post": "flow-wide",
                "staged_by": "composer-record-reach",
            },
        },
        # J-PROJ synthesis (staged flow key extract-projection): pre = Any,
        # post = matched field type (spec 03 §3.4(1)).
        {
            "id": "J-PROJ.project",
            "judgment": "J-PROJ",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "key-in-record",
            "emit_kwargs": {},
            "staged": {
                "pre": "any-projection",
                "post": "matched-field",
                "staged_by": "extract-projection",
            },
        },
    ],
    "parallel": [
        # Structural n≥1 precondition (spec 01 §5.1 m7): an empty Parallel{} is
        # rejected BEFORE typing. Staged via the rule-level staged_by on
        # EMPTY_PARALLEL (severity-ramp, DORMANT) — no row-level staged block,
        # so the interpreter reads the code here and its dormancy from the
        # catalog (checking-as-data, spec 03 §2.3).
        {
            "id": "J-REC.empty-parallel",
            "judgment": "J-REC",
            "mode": "checking",
            "relation_mode": None,
            "premise": "branches-nonempty",
            "on_fail": {"code": "EMPTY_PARALLEL"},
            # Emits at the armed gate exactly like J-REC.consumption: location +
            # step (FIX-A F5.1 — the row names the REAL kwargs the site passes).
            "emit_kwargs": {"location": "location", "step": "step"},
        },
        {
            "id": "J-REC.formation",
            "judgment": "J-REC",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "signature-resolved",
            "emit_kwargs": {},
        },
        # DICT_NOT_CONSUMED lives at the record-consumption boundary (§5.4).
        # The suggestion renders from the RULE's catalog suggestion_template.
        {
            "id": "J-REC.consumption",
            "judgment": "J-REC",
            "mode": "checking",
            "relation_mode": None,
            "premise": "record-consumed",
            "on_fail": {"code": "DICT_NOT_CONSUMED"},
            "emit_kwargs": {"location": "location", "step": "step"},
            "skip_when": ["record-consumption-exempt"],
        },
    ],
    "nested_pipeline": [
        {
            "id": "J-PIPE.nested",
            "judgment": "J-PIPE",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "signature-resolved",
            "emit_kwargs": {},
        },
    ],
    "variable_ref": [
        {
            "id": "J-STEP.variable-ref-skip",
            "judgment": "J-STEP",
            "mode": "synthesis",
            "relation_mode": None,
            "premise": "signature-resolved",
            "emit_kwargs": {},
            "skip_when": ["variable-ref"],
        },
    ],
}

# ── Cross-kind exemption + degrade rows (spec 03 §2.3 "exemptions") ──────────
EXEMPTION_ROWS: list[dict] = [
    {
        "id": "X.any-frontier",
        "judgment": "J-STEP",
        "mode": "checking",
        "relation_mode": "strict",
        "premise": "input-satisfies",
        "emit_kwargs": {},
        "skip_when": ["any-frontier"],
    },
    {
        "id": "X.unresolvable-binding",
        "judgment": "J-INST",
        "mode": "synthesis",
        "relation_mode": None,
        "premise": "bound-satisfies",
        "emit_kwargs": {},
        "skip_when": ["unresolvable-binding"],
    },
    {
        "id": "X.slot-unused-lint",
        "judgment": "J-SLOTREAD",
        "mode": "checking",
        "relation_mode": None,
        "premise": "slot-used",
        "on_fail": {"code": "SLOT_UNUSED"},
        "emit_kwargs": {"location": "location", "slot": "slot"},
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# The §1.6 site-level census (40 distinct catalog codes; both dual-lane sites)
# ═══════════════════════════════════════════════════════════════════════════
# lane "A" = re-based onto a spec-01 judgment (16 codes); lane "B" = unchanged
# R5 data rule (24 codes). PARAM_OUT_OF_RANGE and PARAM_INVALID_VALUE each have
# TWO sites (one lane-A re-based, one lane-B frozen — the dual-lane convention).
# The three type-theorem codes (VALUE_DOMAIN_MISMATCH / VALUE_DOMAIN_UNPROVEN /
# VALUE_BOUNDS_ADVISORY) are RELATION rows, NOT §1.6 census codes (born at M2).

SITE_CENSUS: tuple[dict, ...] = (
    # ── pass 5: 5 lane-A (J-PARAM) + 5 lane-B ────────────────────────────
    {
        "code": "PARAM_TYPE_MISMATCH",
        "pass": "5",
        "site": "type-check",
        "lane": "A",
        "judgment": "J-PARAM",
    },
    {
        "code": "PARAM_TYPE_CHECK_SKIPPED",
        "pass": "5",
        "site": "type-skip",
        "lane": "A",
        "judgment": "J-PARAM",
    },
    {
        "code": "PARAM_INVALID_VALUE",
        "pass": "5",
        "site": "nonfinite",
        "lane": "A",
        "judgment": "J-PARAM",
    },
    {
        "code": "PARAM_INVALID_VALUE",
        "pass": "5",
        "site": "weights-sum",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "PARAM_OUT_OF_RANGE",
        "pass": "5",
        "site": "constraint",
        "lane": "A",
        "judgment": "J-PARAM",
    },
    {
        "code": "PARAM_INVALID_OPTION",
        "pass": "5",
        "site": "option-check",
        "lane": "A",
        "judgment": "J-PARAM",
    },
    {"code": "MISSING_PARAM", "pass": "5", "site": "required", "lane": "B", "judgment": None},
    {"code": "UNKNOWN_PARAM", "pass": "5", "site": "unknown", "lane": "B", "judgment": None},
    {
        "code": "PARAM_GROUP_CONFLICT",
        "pass": "5",
        "site": "schema-v1",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "PARAM_GROUP_MISSING",
        "pass": "5",
        "site": "schema-v1",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "PARAM_REQUIRES_MISSING",
        "pass": "5",
        "site": "schema-v1",
        "lane": "B",
        "judgment": None,
    },
    # ── pass 6: 7 lane-A ─────────────────────────────────────────────────
    {
        "code": "TYPE_MISMATCH",
        "pass": "6",
        "site": "input-check",
        "lane": "A",
        "judgment": "J-STEP",
    },
    {
        "code": "TRANSITION_OUTPUT_MISMATCH",
        "pass": "6",
        "site": "transition",
        "lane": "A",
        "judgment": "J-STEP",
    },
    {
        "code": "DICT_INPUT_EXPECTED",
        "pass": "6",
        "site": "extract",
        "lane": "A",
        "judgment": "J-PROJ",
    },
    {
        "code": "DICT_NOT_CONSUMED",
        "pass": "6",
        "site": "consumption",
        "lane": "A",
        "judgment": "J-REC",
    },
    {
        "code": "EXTRACT_MISSING_KEY",
        "pass": "6",
        "site": "key-member",
        "lane": "A",
        "judgment": "J-PROJ",
    },
    {
        "code": "COMPOSER_KEY_MISMATCH",
        "pass": "6",
        "site": "key-member",
        "lane": "A",
        "judgment": "J-COMPOSER",
    },
    {
        "code": "COMPOSER_INPUT_TYPE_MISMATCH",
        "pass": "6",
        "site": "field-check",
        "lane": "A",
        "judgment": "J-COMPOSER",
    },
    # ── pass 7: 1 lane-B ─────────────────────────────────────────────────
    {
        "code": "PHASE_ORDER_VIOLATION",
        "pass": "7",
        "site": "ordering",
        "lane": "B",
        "judgment": None,
    },
    # ── pass 8: 4 lane-A ─────────────────────────────────────────────────
    {"code": "SLOT_NOT_FOUND", "pass": "8", "site": "load", "lane": "A", "judgment": "J-LOAD"},
    {
        "code": "SLOT_REF_NOT_FOUND",
        "pass": "8",
        "site": "ref",
        "lane": "A",
        "judgment": "J-SLOTREAD",
    },
    {
        "code": "SLOT_TYPE_MISMATCH",
        "pass": "8",
        "site": "slot-read",
        "lane": "A",
        "judgment": "J-SLOTREAD",
    },
    {
        "code": "SLOT_UNUSED",
        "pass": "8",
        "site": "journal-lint",
        "lane": "A",
        "judgment": "J-SLOTREAD",
    },
    # ── pass 9: 18 exclusive lane-B + PARAM_OUT_OF_RANGE lane-B (:2452) ───
    {
        "code": "PARAM_OUT_OF_RANGE",
        "pass": "9",
        "site": "execution-range",
        "lane": "B",
        "judgment": None,
    },
    {"code": "EMPTY_UNIVERSE", "pass": "9", "site": "universe", "lane": "B", "judgment": None},
    {"code": "INVALID_EXECUTION", "pass": "9", "site": "execution", "lane": "B", "judgment": None},
    {"code": "INVALID_GLOBAL", "pass": "9", "site": "global", "lane": "B", "judgment": None},
    {"code": "INVALID_UNIVERSE", "pass": "9", "site": "universe", "lane": "B", "judgment": None},
    {
        "code": "INVALID_UNIVERSE_GROUP",
        "pass": "9",
        "site": "universe",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "IRRELEVANT_EXECUTION_PARAM",
        "pass": "9",
        "site": "execution",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "MISSING_DECLARATION_REF",
        "pass": "9",
        "site": "declaration",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "MISSING_EXECUTION_PARAM",
        "pass": "9",
        "site": "execution",
        "lane": "B",
        "judgment": None,
    },
    {"code": "STALE_UNIVERSE", "pass": "9", "site": "universe", "lane": "B", "judgment": None},
    {"code": "UNRESOLVED_UNIVERSE", "pass": "9", "site": "universe", "lane": "B", "judgment": None},
    {"code": "UNUSED_GLOBAL", "pass": "9", "site": "global", "lane": "B", "judgment": None},
    {
        "code": "BAR_OFFSET_AT_SAME_TF",
        "pass": "6.clock",
        "site": "transform-site",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "BAR_OFFSET_NOT_MULTIPLE",
        "pass": "6.clock",
        "site": "transform-site",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "BAR_OFFSET_TOO_LARGE",
        "pass": "6.clock",
        "site": "transform-site",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "INVALID_BAR_OFFSET",
        "pass": "9.resampler",
        "site": "resampler",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "RESAMPLER_NOOP",
        "pass": "6.clock",
        "site": "transform-site",
        "lane": "B",
        "judgment": None,
    },
    {
        "code": "UPSAMPLE_NOT_SUPPORTED",
        "pass": "6.clock",
        "site": "transform-site",
        "lane": "B",
        "judgment": None,
    },
)


# ═══════════════════════════════════════════════════════════════════════════
# The CLOCK facet (dsl-multi-timeframe-clocks spec 01 §§4-7; spec 02 §7.2's
# judgment_tables delta). Checking-as-data: the Python checker (dsl/clocks.py
# + the interpreter's clock facet) executes THESE tables; the TS mirror (M4e)
# consumes the same generated block from judgment_tables.json. The block is
# additive — the TS interpreter validates only flow_config/step_kinds/
# exemptions and ignores extra top-level keys until its clock evaluator lands.
# ═══════════════════════════════════════════════════════════════════════════


def clock_transfers() -> dict[str, dict]:
    """The per-component clock-transfer column (spec 01 §5.1 — 3 ops + default).

    DEFAULT IS ``keep`` BY OMISSION: every registered component absent from
    this table carries ``keep`` (κ_out = κ_in; clock-less in ⇒ clock-less
    out). ``src``/``off`` are PARAMETER NAMES; resolution is param-literal
    first, then the signature's declaration_refs/optional_declaration_refs
    (so the globals-wired variants are ordinary coarsen/project whose source
    resolves to globals.target_timeframe — no fourth op exists for them).

    M4c re-sourced this from the component REGISTRATION surface (spec 02
    §8.1 — the M4b dsl-side literal table is gone): each transform-set
    member declares ``clock_transfer`` at ``@register_component`` time
    (shape-validated there), and this derivation reads the LATEST version
    per name. The interpreter's walk reads the resolved signature's own
    ``sig.clock_transfer`` (version-scoped); this name-keyed view exists
    for the generated ``judgment_tables.json`` block the TS mirror consumes
    (M4e) and for tests.
    """
    from pipeline_engine.base.registry_types import COMPONENT_REGISTRY, get_latest
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()
    out: dict[str, dict] = {}
    for name in sorted(COMPONENT_REGISTRY):
        sig = get_latest(name)
        if sig is not None and sig.clock_transfer is not None:
            out[name] = dict(sig.clock_transfer)
    return out


#: Closed vocabularies for the clock rows (spec 01 §§5-6). ``J-TRANSFORM`` is
#: the shared row family over both transform ops (the κ_out = κ_in noop row
#: fires at coarsen AND project sites).
CLOCK_JUDGMENTS = (
    "J-COARSEN",
    "J-PROJECT",
    "J-TRANSFORM",
    "J-CLKUNIFORM",
    "J-SLOTREAD",
    "J-PIPE",
    "J-GLOBALS",
)
#: §6.6 recovery-clock tokens: what clock τ_out carries after the row fires.
#: declared-target — the clock the author asked for (rejected re-clocks keep
#: checking downstream on the author's intent, no cascade); unchanged — τ_out
#: identity (PROJECT_INPUT_UNCLOCKED: no clock is manufactured);
#: kappa-exec-or-clockless — the R-2 J-CLKUNIFORM recovery (κ_exec when
#: defined and WF, clock-less otherwise — single-fire in every branch order);
#: terminal — terminal row, nothing downstream; none — no synthesis effect.
CLOCK_RECOVERY_TOKENS = (
    "declared-target",
    "unchanged",
    "kappa-exec-or-clockless",
    "terminal",
    "none",
)

#: The clock judgment rows, in CHECK ORDER (the order IS the single-fire
#: precedence at a transform site — the first failing premise fires, §6.6
#: recovery applies, no second fire at the same site). Row-level ``staged_by``
#: marks the re-based codes' pass-6 sites (clock-transform-rebase, kind (b));
#: rows without one resolve staging from the RULE's own severity-ramp key.
#: These rows are the SOLE transform-site emitter: the historical pass-9
#: Globals-path block and the spec 02 §3.4 ``suppress: "pass9-dedupe"``
#: column that deduplicated against it were both removed at the
#: clock-transform-rebase PROMOTED flip (T-M4f-5). The ``J-GLOBALS.exec-wf``
#: row is the R-16 Globals-level κ_exec WF keeper — the pass-9 successor that
#: §3.4 explicitly KEEPS, so the loader-free class stays covered.
CLOCK_ROWS: tuple[dict, ...] = (
    {
        "id": "J-COARSEN.offset-too-large",
        "judgment": "J-COARSEN",
        "on_fail": {"code": "BAR_OFFSET_TOO_LARGE"},
        "staged_by": "clock-transform-rebase",
        "recovery": "declared-target",
    },
    {
        "id": "J-COARSEN.direction",
        "judgment": "J-COARSEN",
        "on_fail": {"code": "UPSAMPLE_NOT_SUPPORTED"},
        "staged_by": "clock-transform-rebase",
        "recovery": "declared-target",
    },
    {
        "id": "J-COARSEN.offset-same-tf",
        "judgment": "J-COARSEN",
        "on_fail": {"code": "BAR_OFFSET_AT_SAME_TF"},
        "staged_by": "clock-transform-rebase",
        "recovery": "declared-target",
    },
    {
        "id": "J-COARSEN.harmonic",
        "judgment": "J-COARSEN",
        "on_fail": {"code": "CLOCK_NOT_HARMONIC"},
        "staged_by": None,
        "recovery": "declared-target",
    },
    {
        "id": "J-COARSEN.offset-multiple",
        "judgment": "J-COARSEN",
        "on_fail": {"code": "BAR_OFFSET_NOT_MULTIPLE"},
        "staged_by": "clock-transform-rebase",
        "recovery": "declared-target",
    },
    {
        "id": "J-PROJECT.input-unclocked",
        "judgment": "J-PROJECT",
        "on_fail": {"code": "PROJECT_INPUT_UNCLOCKED"},
        "staged_by": None,
        "recovery": "unchanged",
    },
    {
        "id": "J-PROJECT.direction",
        "judgment": "J-PROJECT",
        "on_fail": {"code": "PROJECT_WRONG_DIRECTION"},
        "staged_by": None,
        "recovery": "declared-target",
    },
    {
        "id": "J-PROJECT.harmonic",
        "judgment": "J-PROJECT",
        "on_fail": {"code": "CLOCK_NOT_HARMONIC"},
        "staged_by": None,
        "recovery": "declared-target",
    },
    {
        "id": "J-TRANSFORM.noop",
        "judgment": "J-TRANSFORM",
        "on_fail": {"code": "RESAMPLER_NOOP"},
        "staged_by": "clock-transform-rebase",
        "recovery": "none",
    },
    {
        "id": "J-CLKUNIFORM.consumer",
        "judgment": "J-CLKUNIFORM",
        "on_fail": {"code": "CLOCK_MISMATCH"},
        "staged_by": None,
        "recovery": "kappa-exec-or-clockless",
    },
    {
        "id": "J-SLOTREAD.clock",
        "judgment": "J-SLOTREAD",
        "on_fail": {"code": "CLOCK_MISMATCH"},
        "staged_by": None,
        "recovery": "none",
    },
    {
        "id": "J-PIPE.terminal-clock",
        "judgment": "J-PIPE",
        "on_fail": {"code": "TERMINAL_CLOCK_MISMATCH"},
        "staged_by": None,
        "recovery": "terminal",
    },
    {
        "id": "J-GLOBALS.exec-wf",
        "judgment": "J-GLOBALS",
        "on_fail": {"code": "BAR_OFFSET_TOO_LARGE"},
        "staged_by": "clock-transform-rebase",
        "recovery": "none",
    },
)


def iter_rows():
    """Every judgment ROW (step-kind rows + exemption rows), in table order."""
    for kind in STEP_KINDS:
        for row in STEP_KIND_ROWS.get(kind, []):
            yield kind, row
    for row in EXEMPTION_ROWS:
        yield "exemptions", row


def build_judgment_table(flow_config: dict[str, str]) -> dict:
    """Assemble the A3 object from this module's ROWs + a derived ``flow_config``.

    ``flow_config`` MUST be exactly ``FLOW_CONFIG_KEYS`` (the writer derives its
    values from the live ``STAGED_CHANGES`` flow-shape stages; hand edits are
    rejected by freshness). Raises on any drift so a malformed call is loud.
    """
    if tuple(sorted(flow_config)) != tuple(sorted(FLOW_CONFIG_KEYS)):
        raise ValueError(
            f"flow_config keys must be exactly {sorted(FLOW_CONFIG_KEYS)}; "
            f"got {sorted(flow_config)}"
        )
    transfers = clock_transfers()
    return {
        "flow_config": {k: flow_config[k] for k in FLOW_CONFIG_KEYS},
        "step_kinds": {k: list(STEP_KIND_ROWS.get(k, [])) for k in STEP_KINDS},
        "exemptions": list(EXEMPTION_ROWS),
        # The M4 clock facet (dsl-mtf-clocks spec 02 §7.2): per-component
        # transfer column (derived from the live registration surface —
        # hand edits are impossible by construction) + the clock judgment
        # rows. Additive block — the TS interpreter ignores it until its
        # clock evaluator lands (M4e).
        "clock": {
            "transfers": {k: dict(transfers[k]) for k in sorted(transfers)},
            "rows": [dict(r) for r in CLOCK_ROWS],
        },
    }


__all__ = [
    "CLOCK_JUDGMENTS",
    "CLOCK_RECOVERY_TOKENS",
    "CLOCK_ROWS",
    "clock_transfers",
    "EMIT_TEMPLATES",
    "EMIT_TERMS",
    "EXEMPTION_ROWS",
    "FLOW_CONFIG_KEYS",
    "JUDGMENTS",
    "MODES",
    "PREMISES",
    "RECOVERY_TYPES",
    "RELATION_MODES",
    "SITE_CENSUS",
    "SKIP_TOKENS",
    "STEP_KINDS",
    "STEP_KIND_ROWS",
    "build_judgment_table",
    "emit_template",
    "emit_term_ok",
    "iter_rows",
]
