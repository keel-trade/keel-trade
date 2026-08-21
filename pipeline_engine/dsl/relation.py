"""THE ONE subsumption relation and its three query modes (spec 01 §2, D1).

This module is the formal core of the re-founded checker: a single relation
``(B₁, d₁) ≤ₘ (B₂, d₂)`` computed from a base-half classification, a domain-half
classification, one tier lookup, and a mode policy — the retirement of the
three-plus live oracles (``is_compatible`` / ``TYPE_TRANSITIONS`` /
``_is_slot_compatible`` / ``_composer_accepts``) into query modes over
GENERATED data.

TOKENS-ONLY (spec 02 §3.6 direction 2, spec 03 §2.2). Every answer comes from
the generated ``domain_tables.json`` (A2 — the ``base_order`` reflexive-transitive
closure, ``base_refinements`` exp-forms, ``identity_aliases``) plus the ~30-LOC
domain evaluator (``domain_leq`` / ``domain_join`` — the R4-I3
evaluated-not-enumerated exception) and ``validation_tables.json`` (A6 —
transition mode). It NEVER calls ``is_compatible`` / ``_is_slot_compatible`` /
``issubclass``, never walks ``__supertype__``, never compares live type identity.

No silent fallbacks (lessons.md): a missing table raises a loud ``ImportError``
naming the regen command; an undefined relation query raises ``RelationError``
(totality is proven by ``relation_test.test_decision_table_total``, not a
default branch).
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline_engine.dsl.catalog import RULES, STAGED_CHANGES, Stage, severity_for


# ── Domain lattice JSON encoding (spec 01 §1.4) ──────────────────────────────
# "top" | {"set": [floats]} | {"interval": [lo, hi]} | "bottom" (⊥ — internal
# identity for ⊔; never declared, never synthesized). These are the exact
# shapes A2 subsumption_vectors / registry normalized fields ship.
TOP = "top"
BOTTOM = "bottom"

# ── ABI base partition (spec 01 §1.2 — frozen names, not derived) ────────────
# Structural CARRIERS: a super-* actual that is one of these classifies
# ``super-carrier`` (oracle 1/3's carrier fallback), everything else nominal.
_STRUCTURAL_CARRIERS = frozenset({"dict", "DataFrame", "Series", "list", "str"})


class RelationError(Exception):
    """An undefined relation query — never swallowed, never defaulted."""


def _require_tables() -> dict:
    """Load A2 once, loud-failing with the exact regen command if absent."""
    try:
        from pipeline_engine.dsl.fixtures.loader import load_domain_tables

        return load_domain_tables()
    except FileNotFoundError as exc:  # pragma: no cover - build error path
        raise ImportError(
            "domain_tables.json (A2) is missing — regenerate with: "
            'python -c "from pipeline_engine.dsl.fixtures.loader import '
            'write_domain_tables; write_domain_tables()"'
        ) from exc


_A2 = _require_tables()
_BASE_ORDER: dict[str, list[str]] = _A2["base_order"]
_BASE_REFINEMENTS: dict[str, dict] = _A2["base_refinements"]
_IDENTITY_ALIASES: dict[str, str] = _A2["identity_aliases"]


def _carrier(base: str) -> str:
    """The eventual structural root of ``base`` in ≤_B (spec 01 §1.2 carrier)."""
    supers = _BASE_ORDER.get(base, [base])
    roots = [s for s in supers if _BASE_ORDER.get(s, [s]) == [s]]
    if not roots:
        return base
    # bool reaches int (its only root); every nominal reaches exactly one.
    return min(roots, key=lambda s: (len(_BASE_ORDER.get(s, [s])), s))


def _direct_parent(base: str) -> str:
    """The immediate ≤_B super of ``base`` (the one-level ``__supertype__``)."""
    supers = [s for s in _BASE_ORDER.get(base, [base]) if s != base]
    if not supers:
        return base
    # The closest proper super has the LONGEST closure (most specific).
    return max(supers, key=lambda s: (len(_BASE_ORDER.get(s, [s])), s))


_CARRIER = {b: _carrier(b) for b in _BASE_ORDER}
_DIRECT_PARENT = {b: _direct_parent(b) for b in _BASE_ORDER}

# ── clocked(B) — the clock-bearing predicate (dsl-mtf-clocks spec 01 §4.1) ───
# Generated data riding A2 (shipped with the base-order closure; spec 02
# §7.2). A missing key is a build error: regenerate domain_tables.json.
CLOCKED_BASES: frozenset = frozenset(_A2["clocked_bases"])


def is_clocked(base: str) -> bool:
    """True iff pairs with this base carry a clock (spec 01 §4.1 clocked(B)).

    Evaluated over the GENERATED ``clocked_bases`` list (B ≤_B DataFrame ∨
    B ≤_B Series ∨ B = OHLCVDict) — never derived from live type objects.
    Unknown bases are clock-less (the calculus never manufactures a clock).
    """
    return canon_base(base) in CLOCKED_BASES


# ═══════════════════════════════════════════════════════════════════════════
# The domain evaluator (spec 01 §1.4 — the R4-I3 exception: ~30 LOC of CODE,
# constrained by the A2 subsumption_vectors). Operates on JSON encodings only.
# ═══════════════════════════════════════════════════════════════════════════


def _dkind(d) -> str:
    if d == TOP:
        return "top"
    if d == BOTTOM:
        return "bottom"
    if isinstance(d, dict) and set(d) == {"set"}:
        return "set"
    if isinstance(d, dict) and set(d) == {"interval"}:
        return "interval"
    raise RelationError(f"Not a domain lattice element: {d!r}")


def domain_leq(d1, d2) -> bool:
    """The domain order ``⊑`` (spec 01 §1.4). Total over the lattice."""
    k1, k2 = _dkind(d1), _dkind(d2)
    if k1 == "bottom":
        return True
    if k2 == "top":
        return True
    if k1 == "top" or k2 == "bottom":
        return False
    if k1 == "set" and k2 == "set":
        return set(map(float, d1["set"])) <= set(map(float, d2["set"]))
    if k1 == "set" and k2 == "interval":
        lo, hi = d2["interval"]
        return all(lo <= float(v) <= hi for v in d1["set"])
    if k1 == "interval" and k2 == "interval":
        (a, b), (c, d) = d1["interval"], d2["interval"]
        return c <= a and b <= d
    return False  # Interval ⊑ Set: never (v1 — no degenerate-interval norm)


def domain_join(*ds):
    """Operand-preserving n-ary ``⊔`` (spec 01 §1.4 / §4). NOT a binary fold."""
    ops = [d for d in ds if _dkind(d) != "bottom"]
    if not ops:
        return BOTTOM
    if any(_dkind(d) == "top" for d in ops):
        return TOP
    if all(_dkind(d) == "set" for d in ops):
        union: set = set()
        for d in ops:
            union |= set(float(v) for v in d["set"])
        return {"set": sorted(union)} if len(union) <= 8 else TOP
    for cand in ops:  # ≥1 interval: the unique operand bounding all, else ⊤
        if all(domain_leq(d, cand) for d in ops):
            return cand
    return TOP


# ═══════════════════════════════════════════════════════════════════════════
# Type tokens, canonicalization, and the exp/decl demand forms (spec 01 §1.5)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Pair:
    """A synthesized/declared type ``(B, d)`` — base name × JSON domain."""

    base: str
    domain: object = TOP


# Special-form sentinels (spec 01 §1.1 grammar).
ANY = "Any"
NONE = "None"


@dataclass(frozen=True)
class Union:
    """A declaration-side union demand (S3) or declared multi-output (S4)."""

    variants: tuple


@dataclass(frozen=True)
class Record:
    """A closed record (Parallel output) — routes to §5.4 (S6)."""

    fields: tuple  # ((key, type), ...)


@dataclass(frozen=True)
class TypeVarRef:
    """A type-variable occurrence on the expected side — a binding site (S5)."""

    name: str = "T"


def canon_base(name: str) -> str:
    """Identity-alias canonicalization (spec 01 §1.2 — the explicit map)."""
    return _IDENTITY_ALIASES.get(name, name)


def is_refined(name: str) -> bool:
    return canon_base(name) in _BASE_REFINEMENTS


def refinement(name: str) -> dict | None:
    """The A2 ``base_refinements`` entry (parent / dom / tier / exp) or None."""
    return _BASE_REFINEMENTS.get(canon_base(name))


def decl_type(name: str) -> Pair:
    """Synthesis side: the declared name IS the base (spec 01 §1.5)."""
    c = canon_base(name)
    ref = _BASE_REFINEMENTS.get(c)
    return Pair(c, ref["dom"]) if ref else Pair(c, TOP)


def exp_demand(name: str) -> Pair:
    """Demand side: ``exp`` unfolds refined names (spec 01 §1.5)."""
    c = canon_base(name)
    ref = _BASE_REFINEMENTS.get(c)
    return Pair(ref["exp"]["base"], ref["exp"]["domain"]) if ref else Pair(c, TOP)


# ═══════════════════════════════════════════════════════════════════════════
# Base-half + domain-half classification (spec 01 §2.2 / §2.3)
# ═══════════════════════════════════════════════════════════════════════════

BASE_CLASSES = ("eq", "sub", "super-nom", "super-carrier", "sib", "disj")
DOMAIN_CLASSES = ("sat", "unproven", "viol")
TIERS = ("none", "soft", "hard")
MODES = ("strict", "slot", "transition")


def classify_base(actual: str, expected: str) -> str:
    """Base-half class (spec 01 §2.2). ``expected`` is already exp-unfolded."""
    a, e = canon_base(actual), canon_base(expected)
    if a == e:
        return "eq"
    if a not in _BASE_ORDER or e not in _BASE_ORDER:
        raise RelationError(f"Unknown base in ≤_B query: {actual!r} → {expected!r}")
    if e in _BASE_ORDER[a]:  # a <_B e proper
        return "sub"
    if a in _BASE_ORDER[e]:  # e <_B a proper
        return "super-carrier" if a in _STRUCTURAL_CARRIERS else "super-nom"
    return "sib" if _CARRIER[a] == _CARRIER[e] else "disj"


def classify_domain(d_actual, d_expected) -> str:
    """Domain-half class (spec 01 §2.3)."""
    if domain_leq(d_actual, d_expected):
        return "sat"
    if _dkind(d_actual) == "top" and _dkind(d_expected) != "top":
        return "unproven"
    return "viol"


def tier_of(d_expected) -> str:
    """The demand-side tier (spec 01 §1.4/§2.3)."""
    k = _dkind(d_expected)
    return {"top": "none", "set": "hard", "interval": "soft"}[k] if k != "bottom" else "none"


# ═══════════════════════════════════════════════════════════════════════════
# The decision table (spec 01 §2.4) — resolved against the staged config vector
# ═══════════════════════════════════════════════════════════════════════════

# Domain-half outcome rows (eq/sub, or super-carrier pre-flip). Each maps to a
# catalog code + the STAGED_CHANGES id governing its activation (spec 05).
# (domain_class, tier) → (outcome-kind, code, staged_by)
_DOMAIN_ROWS: dict[tuple[str, str], tuple[str, str | None, str | None]] = {
    ("sat", "none"): ("accept", None, None),
    ("sat", "soft"): ("accept", None, None),
    ("sat", "hard"): ("accept", None, None),
    ("unproven", "soft"): ("advisory", "VALUE_BOUNDS_ADVISORY", "soft-bounds-advisory"),
    ("unproven", "hard"): ("error", "VALUE_DOMAIN_UNPROVEN", "d2-refinement-unproven"),
    ("viol", "soft"): ("advisory", "VALUE_BOUNDS_ADVISORY", "soft-bounds-advisory"),
    ("viol", "hard"): ("error", "VALUE_DOMAIN_MISMATCH", "value-domain-rule"),
}

_BASE_ERROR_CODE = {"strict": "TYPE_MISMATCH", "slot": "SLOT_TYPE_MISMATCH"}


@dataclass(frozen=True)
class Verdict:
    """A resolved relation outcome under a given staged configuration.

    ``outcome`` ∈ {accept, advisory, error, skip, route-inst, route-record}.
    ``rejects`` is True only for a fired base/domain ERROR (an advisory never
    rejects). ``code``/``staged_by`` carry the catalog row for the interpreter
    to render severity from (spec 01 §2.7: severity is catalog data, never
    interpreter code).
    """

    outcome: str
    code: str | None = None
    staged_by: str | None = None
    base_class: str | None = None
    domain_class: str | None = None
    tier: str | None = None

    @property
    def rejects(self) -> bool:
        return self.outcome == "error"

    @property
    def accepts(self) -> bool:
        return self.outcome in ("accept", "advisory", "skip")


def _left_birth(key: str) -> bool:
    """True once staged change ``key`` has LEFT its birth state (fires/flips)."""
    ch = STAGED_CHANGES[key]
    if ch.kind == "flow-shape":
        return ch.stage != "pre"
    stage = ch.stage.value if isinstance(ch.stage, Stage) else ch.stage
    return stage != Stage.DORMANT.value


def shipped_staged_active() -> dict[str, bool]:
    """The shipped configuration vector (all DORMANT/pre at HEAD = m2-compat)."""
    return {key: _left_birth(key) for key in STAGED_CHANGES}


M2_COMPAT: dict[str, bool] = {key: False for key in STAGED_CHANGES}
END_STATE: dict[str, bool] = {key: True for key in STAGED_CHANGES}


def _armed_kind(kind: str, code: str, staged_by: str) -> str:
    """Stage-resolve an armed domain-row outcome kind (spec 05 §2.2).

    The row literal encodes the TERMINAL-stage shape. An armed change at a
    sub-terminal emitting stage (INCUBATING/WARNING) resolves to a
    non-blocking ``advisory`` — the stage cap is severity policy, and an
    outcome only ``rejects`` when its stage-resolved severity is ``error``.
    DORMANT (a profile-forced fire, e.g. the END_STATE test profile) keeps
    the terminal-shape literal: there is no live severity to resolve.
    """
    change = STAGED_CHANGES.get(staged_by)
    if change is None or change.kind == "flow-shape" or Stage(change.stage) is Stage.DORMANT:
        return kind
    return "error" if severity_for(RULES[code], row_staged_by=staged_by) == "error" else "advisory"


def _domain_verdict(dom_cls: str, tier: str, staged: dict[str, bool], base_class: str) -> Verdict:
    if dom_cls == "sat":
        return Verdict("accept", base_class=base_class, domain_class="sat", tier=tier)
    row = _DOMAIN_ROWS.get((dom_cls, tier))
    if row is None:
        raise RelationError(f"Undefined domain row: ({dom_cls!r}, {tier!r})")
    kind, code, staged_by = row
    fires = staged_by is None or staged.get(staged_by, False)
    if not fires:  # dormant ⇒ silent (m2-compat base-level accept)
        return Verdict("accept", base_class=base_class, domain_class=dom_cls, tier=tier)
    if staged_by is not None:
        kind = _armed_kind(kind, code, staged_by)
    return Verdict(
        kind, code=code, staged_by=staged_by, base_class=base_class, domain_class=dom_cls, tier=tier
    )


def decide(
    base_class: str,
    dom_cls: str,
    tier: str,
    mode: str,
    *,
    staged: dict[str, bool],
    both_bare: bool = False,
    same_direct_parent: bool = False,
) -> Verdict:
    """The pure decision-table verdict at the CLASS level (spec 01 §2.4).

    Total over (base_class × dom_cls × tier × mode); an unknown combination
    raises ``RelationError`` (totality is proven by enumeration, not a
    default). ``both_bare`` / ``same_direct_parent`` carry the slot-``sib``
    leniency scope (finding M6) that ``subsumes`` computes from the raw names.
    """
    if base_class not in BASE_CLASSES:
        raise RelationError(f"Unknown base class {base_class!r}")
    if mode not in ("strict", "slot"):
        raise RelationError(f"decide() is strict/slot only; got {mode!r}")

    if base_class in ("eq", "sub"):
        return _domain_verdict(dom_cls, tier, staged, base_class)

    if base_class == "super-carrier":
        if staged.get("carrier-fallback-retirement", False):  # flipped ⇒ base error
            return Verdict(
                "error",
                code=_BASE_ERROR_CODE[mode],
                staged_by="carrier-fallback-retirement",
                base_class=base_class,
            )
        # Pre-flip: base half PASSES (oracle 1/3 carrier fallback) → domain half.
        return _domain_verdict(dom_cls, tier, staged, base_class)

    if base_class == "sib":
        if mode == "slot" and both_bare and same_direct_parent:
            if staged.get("d4-slot-sib-narrowing", False):  # flipped ⇒ base error
                return Verdict(
                    "error",
                    code="SLOT_TYPE_MISMATCH",
                    staged_by="d4-slot-sib-narrowing",
                    base_class=base_class,
                )
            return Verdict("accept", base_class=base_class)  # oracle-3-exact leniency
        return Verdict("error", code=_BASE_ERROR_CODE[mode], base_class=base_class)

    # super-nom / disj: unconditional base error (both modes).
    return Verdict("error", code=_BASE_ERROR_CODE[mode], base_class=base_class)


# ═══════════════════════════════════════════════════════════════════════════
# Special forms S1–S7 (spec 01 §2.1) + the public entry point
# ═══════════════════════════════════════════════════════════════════════════


def _canonical_union_variants(variants) -> list:
    """Canonical union order (spec 01 §2.1): canonicalize, dedup, byte-sort."""
    seen: dict[str, object] = {}
    for v in variants:
        key = canon_base(v.base) if isinstance(v, Pair) else str(v)
        seen.setdefault(key, v)
    return [seen[k] for k in sorted(seen)]


def subsumes(
    actual, expected, mode: str = "strict", *, staged: dict[str, bool] | None = None
) -> Verdict:
    """``actual ≤ₘ expected`` — the ONE relation (spec 01 §2).

    ``actual``/``expected`` are type tokens (``Pair``, ``Union``, ``Record``,
    ``TypeVarRef``, or the ``ANY``/``NONE`` sentinels). ``mode`` ∈ {strict,
    slot} (transition mode is a separate advisory query — ``transition_check``).
    ``staged`` is the active-configuration vector; the default is the shipped
    stages (m2-compat at HEAD).
    """
    if staged is None:
        staged = shipped_staged_active()
    if mode not in ("strict", "slot"):
        raise RelationError(f"subsumes() mode must be strict/slot; got {mode!r}")

    # S1: gradual frontier.
    if actual is ANY or expected is ANY:
        return Verdict("accept")
    # S2: both None (entry).
    if actual is NONE and expected is NONE:
        return Verdict("accept")
    # S5: expected type-variable occurrence — a binding site, not a check.
    if isinstance(expected, TypeVarRef):
        return Verdict("route-inst")
    # S6: actual is a record — route to §5.4.
    if isinstance(actual, Record):
        return Verdict("route-record")
    # S3: expected union — ∃-rule over verdicts, canonical order, first-match.
    if isinstance(expected, Union):
        first_advisory: Verdict | None = None
        for variant in _canonical_union_variants(expected.variants):
            v = subsumes(actual, variant, mode, staged=staged)
            # S7 inside a union demand: a stored NoneType sentinel skips the
            # read in slot mode (StoreValue(None)) regardless of the demand
            # shape — the skip surfaces per-variant and satisfies the ∃ (spec
            # 01 §2.1, S7). Without this the whole union would fall through to
            # a spurious base error.
            if v.outcome == "skip":
                return v
            if v.outcome == "accept":
                return v
            if v.outcome == "advisory" and first_advisory is None:
                first_advisory = v
        return first_advisory or Verdict("error", code=_BASE_ERROR_CODE[mode])
    # S4: actual union — ∀-rule (severity-max across variants).
    if isinstance(actual, Union):
        worst = Verdict("accept")
        for variant in _canonical_union_variants(actual.variants):
            v = subsumes(variant, expected, mode, staged=staged)
            if v.outcome == "error":
                return v
            if v.outcome == "advisory" and worst.outcome == "accept":
                worst = v
        return worst

    if not isinstance(actual, Pair) or not isinstance(expected, Pair):
        raise RelationError(f"Non-pair relation query: {actual!r} ≤ {expected!r}")

    # S7: slot mode, stored NoneType sentinel (StoreValue(None)) — skip.
    if mode == "slot" and canon_base(actual.base) == "NoneType":
        return Verdict("skip")

    # Base + domain halves. ``expected`` carries its RAW (possibly refined)
    # declared base; exp-unfold happens here so the leniency scope (finding
    # M6) can still see whether the demand was refined.
    a = Pair(canon_base(actual.base), actual.domain)
    e_unfolded = exp_demand(expected.base)
    e_base, e_dom = e_unfolded.base, e_unfolded.domain
    if _dkind(expected.domain) != "top" and not is_refined(expected.base):
        e_dom = expected.domain  # non-refined demand may carry input_domain

    bc = classify_base(a.base, e_base)
    both_bare = not is_refined(actual.base) and not is_refined(expected.base)
    same_dp = _DIRECT_PARENT.get(a.base) == _DIRECT_PARENT.get(e_base)
    dc = classify_domain(a.domain, e_dom)
    tier = tier_of(e_dom)
    return decide(
        bc, dc, tier, mode, staged=staged, both_bare=both_bare, same_direct_parent=same_dp
    )


# ═══════════════════════════════════════════════════════════════════════════
# Transition mode (spec 01 §2.5) — advisory category-plausibility, never blocks
# ═══════════════════════════════════════════════════════════════════════════


def _require_transitions() -> dict:
    try:
        from pipeline_engine.dsl.fixtures.loader import load_validation_tables

        return load_validation_tables()["type_transitions"]
    except FileNotFoundError as exc:  # pragma: no cover - build error path
        raise ImportError(
            "validation_tables.json (A6) is missing — regenerate with: "
            'python -c "from pipeline_engine.dsl.fixtures.loader import '
            'write_validation_tables; write_validation_tables()"'
        ) from exc


_TYPE_TRANSITIONS = _require_transitions()


def transition_key(name: str) -> str | None:
    """``key(τ)`` (spec 01 §2.5): the transition-table key, or None to SKIP."""
    if name in (NONE, "NoneType", None):
        return "None"
    c = canon_base(name)
    return c if c in _TYPE_TRANSITIONS else None


def transition_check(actual_name: str, category: str, output_name: str) -> bool:
    """True ⇒ a TRANSITION_OUTPUT_MISMATCH advisory should fire (spec 01 §2.5).

    Silent (False) unless ``key(actual)`` is defined, its category row exists,
    and ``key(output)`` is not in that row. Domains are ignored here.
    """
    k = transition_key(actual_name)
    if k is None:
        return False
    row = _TYPE_TRANSITIONS.get(k)
    if not row or category not in row:
        return False
    ko = transition_key(output_name)
    if ko is None:
        return False
    return ko not in row[category]


__all__ = [
    "ANY",
    "BASE_CLASSES",
    "BOTTOM",
    "DOMAIN_CLASSES",
    "END_STATE",
    "M2_COMPAT",
    "MODES",
    "NONE",
    "Pair",
    "Record",
    "RelationError",
    "TIERS",
    "TOP",
    "TypeVarRef",
    "Union",
    "Verdict",
    "canon_base",
    "classify_base",
    "classify_domain",
    "decide",
    "decl_type",
    "domain_join",
    "domain_leq",
    "exp_demand",
    "is_refined",
    "refinement",
    "shipped_staged_active",
    "subsumes",
    "tier_of",
    "transition_check",
    "transition_key",
]
