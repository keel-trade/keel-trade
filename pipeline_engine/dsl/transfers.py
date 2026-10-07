"""The closed transfer algebra + renormalization (spec 01 §4, §1.5, §7.1).

Eight operations, total, O(1) on bounded elements — the anti-scope-creep
fence (Tier-2 #3): transfers may only pass, join, union-with-const, or test
``⊑`` against DECLARED literals (``cond_fixed``/``cond_id`` guards); no
interval arithmetic, no computed bounds, no SMT (spec 01 §4.4). ``norm`` restores
well-formedness after every synthesis step by widening the base, never
narrowing the domain (the Clip rule, spec 01 §1.5). J-LIT (spec 01 §7.1)
evaluates a declared transfer over a step's literal params, degrading an
unresolvable ``from_param`` reference to ``top`` (ledger R9) — never to an error.

TOKENS-ONLY: consumes the registry's normalized ``domain_transfer`` JSON
(``base/registration._transfer_to_json`` shape) + the A2 refinement data via
``relation``. It computes on JSON lattice elements only.
"""

from __future__ import annotations

from pipeline_engine.dsl.relation import (
    TOP,
    Pair,
    RelationError,
    canon_base,
    domain_join,
    domain_leq,
    refinement,
)


_TRANSFER_FNS = frozenset({"id", "const", "union", "join", "fixed", "cond_fixed", "cond_id", "top"})


class TransferError(Exception):
    """A malformed transfer term — raised, never defaulted (no silent fallback)."""


def _finite_literal(value) -> bool:
    """True iff ``value`` is a numeric literal usable as a domain element.

    A ``bool`` literal is NOT a numeric literal for the domain rule (spec 01
    §1.2/§6.2); ``None``/str/list/dict and non-finite floats are unresolvable.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _eval_const(transfer: dict, params: dict) -> object:
    """``const(S)`` (spec 01 §4.1): a literal set OR a singleton ``{param(p)}``.

    The ``from_param`` singleton degrades to ``top`` when the param is absent
    or not a finite numeric literal (J-LIT unresolvable degrade — spec 01 §7.1).
    """
    if "values" in transfer:
        return {"set": sorted(float(v) for v in transfer["values"])}
    name = transfer["from_param"]
    value = params.get(name)
    if not _finite_literal(value):
        return TOP  # unresolvable ⇒ top (visible in trace, never an error)
    return {"set": [float(value)]}


def _declared_domain(transfer: dict) -> object:
    """The declared lattice element carried by a ``fixed``/``cond_fixed`` term.

    Both ops name a domain literally (spec 01 §4.1's ``fixed(S)`` and the
    claims-model ``cond_fixed(d, refs)``); this is the one reader for it, so
    the two cannot drift on spelling.
    """
    if "values" in transfer:
        return {"set": sorted(float(v) for v in transfer["values"])}
    if "interval" in transfer:
        lo, hi = transfer["interval"]
        return {"interval": [float(lo), float(hi)]}
    raise TransferError(f"{transfer.get('fn')!r} term missing values/interval: {transfer!r}")


def eval_transfer(transfer: dict, input_domains: dict, params: dict | None = None) -> object:
    """Evaluate a normalized transfer term to a domain (spec 01 §4.2 — total).

    ``input_domains`` maps each input ref (index or record-field key, exactly
    the ``id``/``join`` operands' spellings) to its JSON domain. ``params`` maps
    literal-param names to values (for ``const``'s ``from_param``).
    """
    params = params or {}
    if not isinstance(transfer, dict) or "fn" not in transfer:
        raise TransferError(f"Not a transfer term: {transfer!r}")
    fn = transfer["fn"]
    if fn not in _TRANSFER_FNS:
        raise TransferError(f"Unknown transfer op {fn!r} (closed 7-op algebra)")

    if fn == "top":
        return TOP
    if fn == "id":
        ref = transfer["input"]
        if ref not in input_domains:
            raise TransferError(f"id({ref!r}) has no matching input domain")
        return input_domains[ref]
    if fn == "const":
        return _eval_const(transfer, params)
    if fn == "union":  # union(f′, const(S)): id core ⊔ const arm
        core, arm = transfer["args"]
        return domain_join(
            eval_transfer(core, input_domains, params),
            eval_transfer(arm, input_domains, params),
        )
    if fn == "join":  # n-ary ⊔ over the named inputs / all fields
        inputs = transfer["inputs"]
        refs = list(input_domains) if inputs == "all" else list(inputs)
        operands = []
        for ref in refs:
            if ref not in input_domains:
                raise TransferError(f"join operand {ref!r} has no matching input domain")
            operands.append(input_domains[ref])
        return domain_join(*operands) if operands else TOP
    if fn == "cond_fixed":
        # cond_fixed(d, refs) — the CONDITIONAL establisher (claims model,
        # spec 09 counter-proposal §2.3): d if every named operand's domain
        # is ⊑ d, else ⊤. "In-convention in ⇒ in-convention out" — the honest
        # shape of a convention PRESERVER (mappers, cappers, convex
        # combiners), which `fixed` overstates (it claims the domain
        # unconditionally) and `id` understates-but-lies (it claims the
        # output values are EXACTLY the input's, false wherever the operation
        # computes a new value: input Set{50} capped at 20 emits {20} ⊄ {50}).
        # Fence-clean: one ⊑ test per operand and a DECLARED literal result —
        # no arithmetic, no hull, no computed endpoints (spec 01 §4.4).
        declared = _declared_domain(transfer)
        inputs = transfer.get("inputs", [])
        refs = list(input_domains) if inputs == "all" else list(inputs)
        if not refs:
            # VACUOUS GUARD ⇒ ⊤ (never the declared domain). An empty operand
            # list means NOTHING was verified, and a guard that verifies
            # nothing must not mint a fact — vacuous truth is the wrong
            # reading for a check whose entire purpose is to check. Reachable
            # via ``inputs: "all"`` on a non-record flow value (every
            # non-composer resolves it to []), which would otherwise turn a
            # conditional claim into an unconditional ``fixed`` in disguise —
            # exactly the unaudited-axiom class this programme deletes.
            # ``join`` is conservative in the identical situation (above);
            # so is this. Both engines must agree — the TS twin returns TOP
            # for the same shape. Found by the claims-model adversarial
            # review, 2026-08-23.
            return TOP
        for ref in refs:
            if ref not in input_domains:
                raise TransferError(f"cond_fixed operand {ref!r} has no matching input domain")
            if not domain_leq(input_domains[ref], declared):
                return TOP
        return declared
    if fn == "cond_id":
        # cond_id(input_i, guards) — the CONDITIONAL PRESERVER (slot-
        # vocabulary successor §6), completing the claims-model 2×2:
        # fixed/cond_fixed ESTABLISH (declared d out), id/cond_id PRESERVE
        # (operand d out). d_out = d(input_i) if every guard's resolved
        # domain ⊑ its DECLARED element, else ⊤. Monotone: widening a guard
        # operand can only flip pass→fail (d(input) → ⊤, which is ⊒);
        # widening the input widens the output directly. Fence-clean (spec
        # 01 §4.4): one ⊑ test per guard against a declared literal plus a
        # pass-through — no arithmetic, no hull, no computed endpoints.
        # Registration guarantees a non-empty guard list (the vacuous-guard
        # rule); an empty one here is a malformed term, raised loudly.
        guards = transfer.get("guards")
        if not isinstance(guards, list) or not guards:
            raise TransferError(f"cond_id term needs a non-empty guard list: {transfer!r}")
        in_ref = transfer.get("input")
        if in_ref not in input_domains:
            raise TransferError(f"cond_id({in_ref!r}) has no matching input domain")
        for guard in guards:
            ref = guard["ref"]
            if ref not in input_domains:
                raise TransferError(f"cond_id guard {ref!r} has no matching input domain")
            if not domain_leq(input_domains[ref], guard["within"]):
                return TOP
        return input_domains[in_ref]
    # fn == "fixed": a declared lattice element, independent of inputs.
    return _declared_domain(transfer)


def norm(base: str, domain, *, soft_roles_survive: bool = False) -> Pair:
    """Renormalization ``norm(B, d)`` — the Clip rule (spec 01 §1.5).

    Restores WF-PAIR by widening the base to its refinement parent (never
    narrowing the domain) until ``domain ⊑ dom(B)`` holds (trivially for a
    non-refined base). An incoming ``(BinarySignal, ⊤)`` widens to
    ``(SignalSeries, ⊤)``; a domain-preserving ``id`` keeps the refined base.

    ``soft_roles_survive`` is the D-1 amendment (founder-ratified
    2026-08-23), threaded from the ``role-survival`` flow-shape stage so the
    change is a staged flip like every other verdict-affecting one. At
    ``post``, a SOFT refined name is well-formed with ANY domain and is
    never demoted: it is a ROLE plus a CAP, and a role stays true when the
    values are unknown. HARD names are untouched — they assert a law, so
    ``norm("BinarySignal", ⊤)`` still yields ``(SignalSeries, ⊤)``.

    Why this matters beyond display: before the amendment
    ``norm("ForecastSeries", ⊤)`` returned ``(SignalSeries, ⊤)``, byte-
    identical to what ``Clip`` produces — so the role was destroyed at
    exactly the moment a role check would need to read it, which is why
    Q-0543's true verdict could not be recovered without this.
    """
    current = canon_base(base)
    seen: set[str] = set()
    while True:
        ref = refinement(current)
        if ref is None:  # non-refined base ⇒ always WF
            return Pair(current, domain)
        if soft_roles_survive and ref.get("tier") == "soft":
            return Pair(current, domain)  # D-1: a soft name is WF with any domain
        if domain_leq(domain, ref["dom"]):  # WF-PAIR holds
            return Pair(current, domain)
        if current in seen:  # defensive: refinement graph is a finite DAG
            raise RelationError(f"norm() cycle at base {current!r}")
        seen.add(current)
        current = ref["parent"]  # widen the base, keep the domain


def eval_lit(
    base_out: str, transfer: dict, params: dict, *, soft_roles_survive: bool = False
) -> Pair:
    """J-LIT — output refinement from literal params (spec 01 §7.1, synthesis).

    ``step ⇒ norm(B_out, eval(f, params))``. Evaluation is total (params are
    parser-guaranteed literals); an unresolvable ``from_param`` degrades to
    ``top`` inside ``eval_transfer``. ``soft_roles_survive`` forwards the D-1
    stage to ``norm``.
    """
    return norm(
        base_out, eval_transfer(transfer, {}, params), soft_roles_survive=soft_roles_survive
    )


__all__ = [
    "TransferError",
    "eval_lit",
    "eval_transfer",
    "norm",
]
