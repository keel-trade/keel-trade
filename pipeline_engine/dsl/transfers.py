"""The closed transfer algebra + renormalization (spec 01 §4, §1.5, §7.1).

Six operations, total, O(1) on bounded elements — the anti-scope-creep fence
(Tier-2 #3): transfers may only pass, join, or union-with-const; no interval
arithmetic, no computed bounds, no SMT (spec 01 §4.4). ``norm`` restores
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


_TRANSFER_FNS = frozenset({"id", "const", "union", "join", "fixed", "top"})


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
        raise TransferError(f"Unknown transfer op {fn!r} (closed 6-op algebra)")

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
    # fn == "fixed": a declared lattice element, independent of inputs.
    if "values" in transfer:
        return {"set": sorted(float(v) for v in transfer["values"])}
    if "interval" in transfer:
        lo, hi = transfer["interval"]
        return {"interval": [float(lo), float(hi)]}
    raise TransferError(f"fixed term missing values/interval: {transfer!r}")


def norm(base: str, domain) -> Pair:
    """Renormalization ``norm(B, d)`` — the Clip rule (spec 01 §1.5).

    Restores WF-PAIR by widening the base to its refinement parent (never
    narrowing the domain) until ``domain ⊑ dom(B)`` holds (trivially for a
    non-refined base). An incoming ``(BinarySignal, ⊤)`` widens to
    ``(SignalSeries, ⊤)``; a domain-preserving ``id`` keeps the refined base.
    """
    current = canon_base(base)
    seen: set[str] = set()
    while True:
        ref = refinement(current)
        if ref is None:  # non-refined base ⇒ always WF
            return Pair(current, domain)
        if domain_leq(domain, ref["dom"]):  # WF-PAIR holds
            return Pair(current, domain)
        if current in seen:  # defensive: refinement graph is a finite DAG
            raise RelationError(f"norm() cycle at base {current!r}")
        seen.add(current)
        current = ref["parent"]  # widen the base, keep the domain


def eval_lit(base_out: str, transfer: dict, params: dict) -> Pair:
    """J-LIT — output refinement from literal params (spec 01 §7.1, synthesis).

    ``step ⇒ norm(B_out, eval(f, params))``. Evaluation is total (params are
    parser-guaranteed literals); an unresolvable ``from_param`` degrades to
    ``top`` inside ``eval_transfer``.
    """
    return norm(base_out, eval_transfer(transfer, {}, params))


__all__ = [
    "TransferError",
    "eval_lit",
    "eval_transfer",
    "norm",
]
