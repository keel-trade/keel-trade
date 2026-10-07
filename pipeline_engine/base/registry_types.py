"""Component registry data model, queries, and type compatibility (SDK-safe).

Contains the data classes, global registry, JSON loading, type compat checks,
and search/query functions. No inspect, no get_type_hints, no numpy/pandas.
Importable by both the monorepo and the SDK.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Union, get_args, get_origin

from pipeline_engine.base.categories import StepCategory
from pipeline_engine.constants import MISSING  # noqa: F401 — re-export


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════


class ParamTier(str, Enum):
    """Classification tier for component parameters.

    Progressive disclosure: STRATEGY params are user-tunable and shown by default,
    INFRA params are runtime/config and shown only on request.
    """

    STRATEGY = "strategy"  # User-tunable: signal windows, thresholds, weights
    INFRA = "infra"  # Runtime/config: caching, data sourcing, injection


@dataclass
class RegistryParamInfo:
    """Parameter metadata for a component."""

    name: str
    type_: type
    default: Any
    required: bool
    description: str = ""
    suggestions: list[Any] = field(default_factory=list)
    optimizable: bool = True
    constraints: dict[str, Any] = field(default_factory=dict)
    tier: ParamTier = ParamTier.STRATEGY
    slot_reference: bool = False
    expected_slot_type: type | None = None
    # Per-slot declared domain demand (slot-vocabulary successor, Q-0548):
    # the JSON-native lattice element from a validated `slot_domains` entry
    # ({"set": [...]} | {"interval": [lo, hi]}; aliases resolve at
    # registration). None means no domain demand — J-SLOTREAD checks the
    # base type only, exactly today's behavior.
    expected_slot_domain: dict | None = None


#: A known-issue id is a quality-ledger reference (``Q-NNNN``).
KNOWN_ISSUE_ID_PATTERN = r"^Q-\d{4,}$"
#: Agent-facing one-liners stay one line and short (position-layer spec 04-R2/R3).
KNOWN_ISSUE_SUMMARY_MAX = 240
REPLACEMENT_TEXT_MAX = 240


@dataclass(frozen=True)
class KnownIssue:
    """A defect a DEPRECATED component version keeps, by design (spec 04-R2).

    Deprecated components run byte-identically forever (D-02), so a version
    whose behaviour is wrong in a known way names the ledger entry here. The
    record is VERSION-scoped, like ``not_runnable``: it belongs to the
    version that declared it. A deploy, update or resume of a strategy that
    pins a version carrying one is refused with "upgrade this strategy
    first" (D-42); deprecated versions without one deploy normally.
    """

    id: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "summary": self.summary}


@dataclass(frozen=True)
class ReplacementShape:
    """A structured (graph-shape) successor for a deprecated component (spec 04-R3).

    Where a deprecated component is replaced not by one other component but
    by a SHAPE — a ``TradeManager`` with its exits as rules — the registry
    carries the shape so every agent surface can name it and the upgrade
    planner (``dsl/upgrade.py``) can apply it.

    - ``head``: the registered component the shape is built around (what the
      plain ``replacement`` field names for every existing consumer).
    - ``text``: one agent-facing line naming the long form.
    - ``recipe``: the id of the recipe in ``dsl/upgrade.py`` ``RECIPES``.
    - ``rule`` / ``prelude``: DSL step-list fragments with ``{param}``
      placeholders (``prelude`` may be ``None``).
    - ``uses``: every component name in ``rule``, ``prelude`` and ``head``.
    """

    head: str
    text: str
    recipe: str
    rule: str
    prelude: str | None = None
    uses: tuple[str, ...] = ()

    def to_dict(self, *, with_fragments: bool = False) -> dict[str, Any]:
        """The metadata projection. ``rule``/``prelude`` only on request:
        the TS validator needs ``text`` alone (spec 04-R3)."""
        out: dict[str, Any] = {
            "head": self.head,
            "text": self.text,
            "recipe": self.recipe,
            "uses": list(self.uses),
        }
        if with_fragments:
            out["rule"] = self.rule
            if self.prelude is not None:
                out["prelude"] = self.prelude
        return out


def known_issue_from_json(value: Any, where: str) -> KnownIssue | None:
    """Hydrate a ``known_issue`` metadata value verbatim (``None`` = absent).

    Strict: a present value that is not ``{id, summary}`` raises — the SDK's
    validator must see exactly what the server's sees, never a partial record.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"id", "summary"}:
        raise ValueError(f"{where}: known_issue must be {{id, summary}}, got {value!r}")
    return KnownIssue(id=str(value["id"]), summary=str(value["summary"]))


def replacement_shape_from_json(value: Any, where: str) -> ReplacementShape | None:
    """Hydrate a ``replacement_shape`` metadata value (``None`` = absent)."""
    if value is None:
        return None
    required = {"head", "text", "recipe", "uses"}
    if not isinstance(value, dict) or not required <= set(value):
        raise ValueError(f"{where}: replacement_shape must carry {sorted(required)}, got {value!r}")
    return ReplacementShape(
        head=str(value["head"]),
        text=str(value["text"]),
        recipe=str(value["recipe"]),
        rule=str(value.get("rule", "")),
        prelude=value.get("prelude"),
        uses=tuple(str(u) for u in value["uses"]),
    )


@dataclass
class ComponentSignature:
    """Complete signature of a registered component."""

    cls: type
    name: str
    input_type: type
    output_type: type
    category: StepCategory
    deterministic: bool = True
    slot_reads: dict[str, type] = field(default_factory=dict)
    slot_writes: list[type] = field(default_factory=list)
    parameters: dict[str, RegistryParamInfo] = field(default_factory=dict)
    description: str = ""
    usage_hint: str = ""
    sub_category: str | None = None
    param_constraints: list[dict[str, Any]] = field(default_factory=list)
    declaration_refs: dict[str, str] = field(default_factory=dict)
    optional_declaration_refs: dict[str, str] = field(default_factory=dict)
    # G1-followup-2: per-key dict input contract for composers. Two shapes:
    # - dict[str, type | tuple]   — heterogeneous, role-param-name → expected types
    # - type | tuple[type, ...]    — homogeneous, uniform value type for all branches
    # - None                        — not declared (skip check)
    composer_inputs: Any | None = None
    content_hash: str | None = None
    version: int = 1
    status: str = "active"  # "active" | "deprecated"
    #: A CONSTRAINED SHELL's redirect text (dsl-mtf-clocks spec 02 §8.2).
    #: ``None`` for every runnable component. When set, this version exists
    #: only to keep the name resolvable and its ``run()`` raises
    #: unconditionally — the string names what to use instead, and the
    #: validator turns it into an authoring-time ``COMPONENT_NOT_RUNNABLE``
    #: error so "validates ⇒ runs" holds (audit S1: registering a shell
    #: without this made new authoring validate GREEN onto a raise).
    not_runnable: str | None = None
    #: A DEPRECATED component's successor — the registered component name the
    #: validator's DEPRECATED_COMPONENT warning tells the author to use
    #: instead. ``None`` when the deprecation names no single successor (the
    #: warning then says "a supported alternative"). Registration refuses it
    #: on an active component: only a deprecation has a replacement.
    replacement: str | None = None
    #: A deprecated component's STRUCTURED successor (position-layer spec
    #: 04-R3). When set, ``replacement`` carries its ``head`` so every
    #: name-only consumer keeps working unchanged.
    replacement_shape: ReplacementShape | None = None
    #: A defect this DEPRECATED version keeps by design (spec 04-R2).
    #: Version-scoped, like ``not_runnable``.
    known_issue: KnownIssue | None = None
    changelog: dict[int, str] = field(default_factory=lambda: {1: "Initial release"})
    # ── dsl-type-system normalized typing fields (spec 02 §3.4, T-M1b-3) ──
    # Populated by BOTH registration paths (register_component and
    # _register_slot_op_entry) from the §3.4 effective-transfer derivation,
    # which runs ONCE, in the extractor. All values are JSON-native.
    #
    # type_relation / slot_write_transfer: the normalized DECLARED values
    # (None when the component declares none — schemes and slot writes are
    # "when applicable" fields).
    # domain_transfer / input_domain / output_domain: EFFECTIVE values —
    # always populated on every registered signature (the §3.4 ladder /
    # domain defaults), so no validator ever re-implements the default
    # ladder (R4-I5).
    # record_tolerant: the spec 01 §5.4 record-tolerance waiver as data,
    # two strengths (R-4/R20): "full" | "dict_only" | None.
    # typing_declared: which declaration attributes the author explicitly
    # wrote (sorted tuple; () = pure defaults) — artifact writers use it to
    # serialize declaration-backed effective values (spec 02 §4.1 A1).
    type_relation: dict[str, Any] | None = None
    domain_transfer: dict[str, Any] | None = None
    input_domain: Any | None = None
    output_domain: Any | None = None
    slot_write_transfer: dict[str, Any] | None = None
    record_tolerant: str | None = None
    typing_declared: tuple[str, ...] = ()
    # ── dsl-multi-timeframe-clocks clock-transfer column (spec 01 §5.1) ──
    # The component's declared clock transfer: {"op": "synth"|"coarsen"|
    # "project", "src": <param-or-ref name>, "off"?: <param-or-ref name>}.
    # None means the DEFAULT ``keep`` (κ_out = κ_in) — keep-by-omission, so
    # only the transform-set members (spec 01 §8.1) carry a value. This is
    # the registration surface the dsl clock facet reads (M4c re-sourced it
    # from the former dsl-side table — spec 02 §8.1).
    clock_transfer: dict[str, str] | None = None
    # ── rolling-universe population-scope column (P2 group (b)) ──
    # The component's declared cross-sectional population scope
    # (projects/rolling-universe/05-population-scope-classification.md):
    #   {"kind": "population_universe", "source": "input", "slots"?: [...]}
    #   {"kind": "population_universe", "source": "slot",  "slots":  [...]}
    #   {"kind": "population_fixed", "params": [...]}
    # None means the DEFAULT ``per_column`` (output column i depends only on
    # input column i) — scope-by-omission, exactly like clock_transfer's
    # keep-by-omission, so only pooling components carry a value. The group
    # (c) validator rules (XS_BEFORE_UNIVERSE_MASK et al.) read this surface.
    population_scope: dict[str, Any] | None = None
    # ── position-layer declarations (spec 03-R5..R8, R45, Q-2448) ──
    # All keep-by-omission and validated at registration by
    # ``pipeline_engine.binding``: ``binding`` the component's position role
    # (None = an ordinary component); ``trade_safe`` this version's
    # fail-closed certification to run on bound series (None = not
    # certified); ``preserves_size`` a sizer that keeps partial units;
    # ``causal`` the Step attribute surfaced (False = expanding or
    # cross-sectional); ``factory_expansion`` a registered factory's template.
    binding: dict[str, Any] | None = None
    trade_safe: dict[str, Any] | None = None
    preserves_size: bool = False
    causal: bool = True
    factory_expansion: list[dict[str, Any]] | None = None

    def accepts(self, output_type: type) -> bool:
        """Can this component accept the given output type?"""
        return is_compatible(output_type, self.input_type)

    def can_precede(self, other: ComponentSignature) -> bool:
        """Can this component come before another?"""
        return is_compatible(self.output_type, other.input_type)

    def compute_content_hash(self) -> str:
        """Compute and cache the file-level content hash for this component."""
        if self.content_hash is None:
            from pipeline_engine.base.hashing import compute_component_content_hash

            self.content_hash = compute_component_content_hash(self.cls)
        return self.content_hash


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

COMPONENT_REGISTRY: dict[str, dict[int, ComponentSignature]] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# JSON LOADING (SDK + monorepo)
# ═══════════════════════════════════════════════════════════════════════════════


_BUILTIN_STD_TYPES: dict[str, type] = {
    "None": type(None),
    "NoneType": type(None),
    "dict": dict,
    "list": list,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "bytes": bytes,
}


def _resolve_type_name(name: str) -> type:
    """Resolve a string type name to a type object for registry lookups.

    Resolution order (strict — no silent fallbacks):
      1. ``Any`` → ``typing.Any``
      2. Builtin / stdlib types (``dict``, ``list``, ``tuple``, ``str``, ``int``,
         ``float``, ``bool``, ``None``, etc.) → the actual builtin
      3. Union syntax (``"X | Y"``) → recursively resolved ``Union[X, Y]``
      4. ``DataFrame`` → ``pandas.DataFrame`` if available, else a NewType
         wrapping ``object`` (matches the SDK stub layout)
      5. Anything else → looked up in ``pipeline_engine.types``
      6. **Not found anywhere → raises ImportError loudly.**

    Pre-2026-05-21 this function silently created synthetic placeholder
    types via ``type(name, (), {})`` when the lookup failed. That hid
    real bugs — e.g. when the SDK bundle was missing
    ``pipeline_engine/types.py``, every type became a synthetic stub
    with no ``__supertype__`` attribute, and ``is_compatible(
    StreamSeries, SignalSeries)`` started returning ``False`` even
    though ``StreamSeries`` is declared as a subtype. The validator
    then emitted false ``TYPE_MISMATCH`` errors for working strategies.

    Validators and parsers MUST behave in one exact way and error
    otherwise — no synthetic fallbacks that produce wrong answers.
    """
    if name == "Any":
        return Any

    if name in _BUILTIN_STD_TYPES:
        return _BUILTIN_STD_TYPES[name]

    # Union syntax: "X | Y" or "X | Y | Z" → typing.Union[...]
    if " | " in name:
        from typing import Union

        parts = [_resolve_type_name(p.strip()) for p in name.split(" | ")]
        return Union[tuple(parts)]  # type: ignore[return-value]

    # DataFrame: pandas if available (libs/ env), else the SDK's
    # types-module stub (`_PdStub.DataFrame == object`). Either way we
    # resolve to a real type, not a synthetic placeholder.
    if name == "DataFrame":
        try:
            import pandas as pd

            return pd.DataFrame
        except ImportError:
            # The SDK stub puts DataFrame on its own pd-stub class.
            from pipeline_engine import types as _t

            return getattr(_t, "pd", object).DataFrame  # type: ignore[no-any-return]

    # All other names must come from pipeline_engine.types — the
    # authoritative source of NewType definitions + subtype graph.
    try:
        from pipeline_engine import types as t
    except ImportError as e:
        raise ImportError(
            f"Cannot resolve type {name!r}: pipeline_engine.types module "
            f"is not importable. This is a build/install bug — the SDK "
            f"bundle is missing pipeline_engine/types.py. Regenerate via "
            f"`PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/"
            f"build_data.py`."
        ) from e

    obj = getattr(t, name, None)
    if obj is not None:
        return obj

    raise ImportError(
        f"Unknown type name {name!r} — not a builtin, not a Union, not "
        f"DataFrame, and not declared in pipeline_engine.types. Either "
        f"the registry was built with a newer pipeline_engine.types than "
        f"this install, or the component that uses this type was added "
        f"without updating types.py. Add the NewType declaration to "
        f"libs/pipeline_engine/types.py and regenerate the SDK bundle."
    )


# Param-type display strings that deliberately resolve to ``Any`` — each is an
# ENUMERATED special case whose enforcement rides a different mechanism, never
# a silent fallback (dsl-type-system spec 02 §3.6, R1-I10; the
# no-silent-fallbacks lesson):
#
# - "enum":   Literal-typed params. Acceptance is enforced through
#             ``constraints.options`` (pass 5), not the type object.
# - "Slot":   slot-reference params. Enforcement rides ``slot_reference`` /
#             ``expected_slot_type``; the DSL-side value is a slot-name string.
# - "CachePolicy": a registered component enum class (RT-7 vocabulary) that is
#             not bundled in the SDK; acceptance rides ``constraints.options``.
# - "Sequence" / "Union": legacy display spellings of bare typing generics
#             (no ``__name__``-level builtin equivalent; type-unconstrained).
_OPAQUE_PARAM_TYPE_STRINGS = frozenset({"enum", "Slot", "CachePolicy", "Sequence", "Union"})

# Union-member spellings that are legal inside an "A | B" param-type string
# but cannot be materialized as checkable type objects in the SDK: stdlib
# dotted names, subscripted builtin generics, and monorepo component classes.
# The whole union resolves to ``Any`` (type-unconstrained, matching the
# pre-strict behavior exactly); an unknown member is a loud error.
_OPAQUE_PARAM_MEMBER_RE = None  # compiled lazily below


def _is_param_union_member(member: str) -> bool:
    global _OPAQUE_PARAM_MEMBER_RE
    if member in _BUILTIN_STD_TYPES or member == "None" or member in _OPAQUE_PARAM_TYPE_STRINGS:
        return True
    if _OPAQUE_PARAM_MEMBER_RE is None:
        import re as _re

        _OPAQUE_PARAM_MEMBER_RE = _re.compile(
            r"(?:datetime\.datetime"  # stdlib dotted name
            r"|(?:dict|list|tuple|set|frozenset)\[.+\]"  # subscripted builtin generic
            r"|components\.[A-Za-z0-9_.]+)$"  # monorepo component class path
        )
    return bool(_OPAQUE_PARAM_MEMBER_RE.match(member))


def _resolve_param_type(type_str: str) -> type:
    """Resolve a parameter type string to a Python type — strict resolution.

    Every accepted spelling is explicitly enumerated (R1-I10; spec 02 §3.6):

    1. ``Any`` → ``typing.Any``
    2. builtin scalars/containers (``int``, ``float``, ``str``, ``bool``,
       ``list``, ``dict``, ``tuple``, ``set``, ``frozenset``, ``bytes``,
       ``None``/``NoneType``) → the builtin type
    3. the enumerated opaque vocabulary (``enum``, ``Slot``, ``CachePolicy``,
       ``Sequence``, ``Union``) → ``Any``, each with a documented reason
       (see ``_OPAQUE_PARAM_TYPE_STRINGS``)
    4. ``"A | B"`` unions whose every member is a builtin, ``None``, or an
       enumerated opaque member spelling → ``Any`` (type-unconstrained;
       per-member enforcement is future work — this exactly preserves the
       pre-strict verdicts, which never enforced union param types)
    5. **anything else raises ValueError loudly.**

    Pre-2026-07-22 this function silently degraded every unknown string to
    ``Any`` (``_BUILTIN_TYPES.get(type_str, Any)``), so a typo'd or
    newly-invented param type spelling entered the SDK registry as an
    unconstrained param with no error anywhere. Parsers and resolvers must
    behave in one exact way and error otherwise. The SDK build
    (``packages/keel-trade/keel-sdk/scripts/build_data.py``) runs every
    emitted param-type string through this resolver so an unknown spelling
    fails at BUILD time, never at SDK runtime.

    The display string is NOT the hydrated type: since Q-1870
    ``load_registry_from_json`` rebuilds ``type_`` from the emitted
    ``type_structure`` (:func:`_param_type_from_structure`), which keeps the
    structure the display string flattens away. This resolver remains the
    build-time vocabulary gate only.
    """
    if type_str == "Any":
        return Any

    if type_str in _BUILTIN_STD_TYPES:
        return _BUILTIN_STD_TYPES[type_str]

    if type_str in _OPAQUE_PARAM_TYPE_STRINGS:
        return Any

    if " | " in type_str:
        members = [m.strip() for m in type_str.split(" | ")]
        unknown = [m for m in members if not _is_param_union_member(m)]
        if unknown:
            raise ValueError(
                f"Unknown member(s) {unknown!r} in param type string "
                f"{type_str!r} — not a builtin, not 'None', and not an "
                f"enumerated opaque member spelling. Add the spelling to the "
                f"enumerated vocabulary in "
                f"pipeline_engine/base/registry_types.py (_is_param_union_member) "
                f"with its enforcement story, then rebuild the SDK data "
                f"(PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/"
                f"build_data.py)."
            )
        return Any

    raise ValueError(
        f"Unknown param type string {type_str!r} — not 'Any', not a builtin, "
        f"not in the enumerated opaque vocabulary "
        f"({sorted(_OPAQUE_PARAM_TYPE_STRINGS)}), and not an 'A | B' union. "
        f"No silent degrade-to-Any exists (R1-I10): add the spelling to the "
        f"enumerated vocabulary in pipeline_engine/base/registry_types.py "
        f"with its enforcement story, then rebuild the SDK data "
        f"(PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/"
        f"build_data.py)."
    )


# ─── JSON read-back of the validator-read columns (Q-1870) ─────────────────
#
# The artifact writers (``build_data.py`` for the SDK, ``loader.py`` for the
# metadata endpoint) emit ``type_structure`` per param, ``expected_slot_domain``
# per slot param and ``composer_inputs`` per signature. Until 2026-09-23 the
# hydrator read none of them back: every param's ``type_`` was rebuilt from the
# DISPLAY string (``enum``/``list``/``dict`` — structure flattened away, so
# PARAM_TYPE_MISMATCH had nothing to check), and ``composer_inputs`` /
# ``expected_slot_domain`` arrived as None (COMPOSER_*, the role-keyed clock
# facet and the mask-slot domain demand silently dark on the SDK's local
# dry-run). The SDK conformance parity guard
# (packages/keel-trade/keel-sdk/tests/test_conformance_engine_parity.py) holds
# the JSON-hydrated engine to the live one's verdicts over the whole corpus.

#: Param classes whose live definition is not importable where the registry is
#: JSON-hydrated (the keel-trade SDK, Pyodide): ``pipeline_engine.data_backend.
#: CachePolicy`` (an Enum), ``pipeline_engine.slots.Slot`` (a dataclass) and
#: ``components.data_loaders.cache_backend.DataCacheBackend`` (a
#: runtime-checkable Protocol of five methods). The ONLY thing the validator
#: reads off such a class is ``isinstance(value, cls)`` over a DSL/graph literal
#: (str/int/float/bool/None/list/dict/tuple) — which is False for every one of
#: the three live classes. A marker class carrying the same ``__name__`` gives
#: that identical verdict and renders identically, so this is an enumerated
#: equivalence, not a stand-in: an unlisted class name raises.
_UNBUNDLED_PARAM_CLASS_NAMES = frozenset({"CachePolicy", "DataCacheBackend", "Slot"})
_UNBUNDLED_PARAM_CLASSES: dict[str, type] = {}

_DOMAIN_ELEMENT_KEYS = frozenset({"set", "interval"})


def _param_class_from_name(name: Any, where: str) -> type:
    """Resolve a ``type_structure`` ``class`` node's name — enumerated, strict."""
    if isinstance(name, str) and name in _BUILTIN_STD_TYPES and name not in ("None", "NoneType"):
        return _BUILTIN_STD_TYPES[name]
    if name == "datetime":
        import datetime as _dt

        return _dt.datetime
    if name in _UNBUNDLED_PARAM_CLASS_NAMES:
        cls = _UNBUNDLED_PARAM_CLASSES.get(name)
        if cls is None:
            cls = _UNBUNDLED_PARAM_CLASSES[name] = type(name, (), {"__module__": __name__})
        return cls
    raise ValueError(
        f"{where}: type_structure names class {name!r}, which the JSON hydrator "
        f"cannot resolve — not a builtin, not 'datetime', and not in the "
        f"enumerated unbundled set {sorted(_UNBUNDLED_PARAM_CLASS_NAMES)}. Add it "
        f"to _UNBUNDLED_PARAM_CLASS_NAMES only if no DSL/graph literal can ever "
        f"be an instance of it (pipeline_engine/base/registry_types.py)."
    )


def _param_type_from_structure(desc: Any, options: Any, where: str) -> Any:
    """Rebuild a param's ``type_`` from its emitted ``type_structure``.

    The inverse of ``validation_shared.param_type_structure_json``: ``literal``
    nodes take their values from ``constraints.options`` (the registration
    ladder derives options FROM the Literal, so they are its values; the
    descriptor carries only the arm types). The result is required to
    serialize back to exactly ``desc`` — any node this cannot rebuild
    faithfully (``opaque``, a malformed node, options whose types disagree
    with the declared arms) raises rather than hydrating a looser type.
    """
    from typing import Literal

    def child(node: dict, key: str) -> Any:
        if key not in node:
            raise ValueError(f"{where}: type_structure {node!r} lacks {key!r}")
        return node[key]

    def build(node: Any) -> Any:
        if not isinstance(node, dict) or not isinstance(node.get("kind"), str):
            raise ValueError(f"{where}: malformed type_structure node {node!r}")
        kind = node["kind"]
        if kind == "any":
            return Any
        if kind == "none":
            return type(None)
        if kind == "class":
            return _param_class_from_name(child(node, "name"), where)
        if kind == "literal":
            if not isinstance(options, list) or not options:
                raise ValueError(
                    f"{where}: a literal type_structure needs constraints.options "
                    f"(its values), got {options!r}"
                )
            return Literal[tuple(options)]
        if kind == "list":
            return list[build(child(node, "element"))]
        if kind == "dict":
            return dict[build(child(node, "key")), build(child(node, "value"))]
        if kind in ("tuple", "union"):
            parts = child(node, "elements" if kind == "tuple" else "members")
            if not isinstance(parts, list) or not parts:
                raise ValueError(f"{where}: malformed {kind} type_structure {node!r}")
            built = tuple(build(p) for p in parts)
            return tuple[built] if kind == "tuple" else Union[built]
        raise ValueError(
            f"{where}: type_structure kind {kind!r} cannot be hydrated (an "
            f"'opaque' descriptor has no rebuildable type — the registry sweep "
            f"keeps it unreachable; regenerate if this artifact carries one)"
        )

    t = build(desc)
    from pipeline_engine.validation_shared import param_type_structure_json

    if param_type_structure_json(t) != desc:
        raise ValueError(
            f"{where}: type_structure {desc!r} does not round-trip (rebuilt "
            f"{param_type_structure_json(t)!r}) — the artifact was not written "
            f"by the registry writer; regenerate it (build_data.py)."
        )
    return t


def _slot_domain_from_json(value: Any, slot_reference: bool, where: str) -> dict | None:
    """Read back a per-slot ``expected_slot_domain`` (Q-0548) — strict shape."""
    if value is None:
        return None
    if (
        slot_reference
        and isinstance(value, dict)
        and len(value) == 1
        and set(value) <= _DOMAIN_ELEMENT_KEYS
        and isinstance(next(iter(value.values())), list)
        and (len(value["interval"]) == 2 if "interval" in value else bool(value["set"]))
    ):
        return {k: list(v) for k, v in value.items()}
    raise ValueError(
        f"{where}: expected_slot_domain must be {{'set': [...]}} or "
        f"{{'interval': [lo, hi]}} on a slot_reference param, got {value!r} "
        f"(slot_reference={slot_reference})"
    )


def _composer_inputs_from_json(value: Any, where: str) -> Any:
    """Read back ``composer_inputs`` — the inverse of ``serialize_composer_inputs``.

    ``"T"`` → type, ``["A", "B"]`` → tuple of types, and either shape keyed by
    role in a dict (``{}`` is the explicit opt-out and stays ``{}``). Every
    name resolves through the strict :func:`_resolve_type_name`.
    """
    if value is None:
        return None

    def one(v: Any) -> Any:
        if isinstance(v, str):
            return _resolve_type_name(v)
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            return tuple(_resolve_type_name(x) for x in v)
        raise ValueError(f"{where}: malformed composer_inputs entry {v!r}")

    if isinstance(value, dict):
        return {role: one(v) for role, v in value.items()}
    return one(value)


def _param_from_json(p: dict, where: str, *, merge_top_level: bool) -> RegistryParamInfo:
    """Hydrate one emitted parameter (latest or per-version) into a RegistryParamInfo."""
    where = f"{where}.{p['name']}"
    if "type_structure" not in p:
        raise ValueError(
            f"{where}: parameter carries no type_structure — the artifact predates "
            f"the structural param type (spec 08 §2.1) or was hand-built; "
            f"regenerate it (write_registry_metadata / build_data.py)."
        )
    p_default = p.get("default")
    p_required = p.get("required", False)
    constraints = dict(p.get("constraints") or {})
    if merge_top_level:
        # Merge legacy top-level min/max/options into constraints.
        for key in ("min", "max"):
            if p.get(key) is not None:
                constraints.setdefault(key, p[key])
        if p.get("options"):
            constraints.setdefault("options", p["options"])
    slot_reference = p.get("slot_reference", False)
    return RegistryParamInfo(
        name=p["name"],
        type_=_param_type_from_structure(p["type_structure"], constraints.get("options"), where),
        default=MISSING if p_required and p_default is None else p_default,
        required=p_required,
        description=p.get("description", ""),
        suggestions=p.get("suggestions", []),
        optimizable=p.get("optimizable", True),
        constraints=constraints,
        tier=ParamTier(p.get("tier", "strategy")),
        slot_reference=slot_reference,
        expected_slot_type=(
            _resolve_type_name(p["expected_slot_type"]) if p.get("expected_slot_type") else None
        ),
        expected_slot_domain=_slot_domain_from_json(
            p.get("expected_slot_domain"), slot_reference, where
        ),
    )


def _slot_reads_from_json(
    implicit_reads: list[str], parameters: dict[str, RegistryParamInfo]
) -> dict[str, type]:
    """One signature's slot reads: its implicit reads + its slot-reference params.

    Called once per hydrated signature (latest and each version) with THAT
    signature's own implicit reads and parameters (Q-2202).
    """
    slot_reads: dict[str, type] = {}
    for sr_name in implicit_reads:
        slot_reads[sr_name] = Any
    for pname, pinfo in parameters.items():
        if pinfo.slot_reference and pinfo.expected_slot_type is not None:
            slot_reads[pname] = pinfo.expected_slot_type
    return slot_reads


def _constraints_from_json(raw: list, owner: str) -> list[dict]:
    """One signature's ``param_constraints``, hydrated VERBATIM (Q-1315).

    The generated registry carries exactly the registration-validated
    schema-v2 entries (``rule`` discriminator + the rule's own keys: ``when``
    for requires, ``params_dict``/``value``/``tolerance`` for sum_eq, …), and
    the validator's pass 5 reads those keys straight off the entry. Until
    2026-09-14 the loader re-shaped every entry to the pre-schema-v1
    ``{"params", "type"}`` pair — the very key registration rejects as the
    legacy discriminator — so in every JSON-hydrated environment (the
    keel-trade SDK, Pyodide) constraints arrived with ``rule`` == "" and no
    ``when``/``params_dict``: silently unenforced until the validator learned
    to raise on an unknown rule, then fatal on the first sum_eq component (the
    nightly DSL Pyodide oracle, red since the vendored copy picked that raise
    up). The shape check below is loud rather than lenient: an artifact
    without a ``rule`` was not written by the registry writer and must be
    regenerated, never guessed at (no silent fallbacks).
    """
    constraints = []
    for c in raw:
        if not isinstance(c, dict) or not isinstance(c.get("rule"), str) or not c["rule"]:
            raise ValueError(
                f"Component '{owner}' has a param_constraints entry without a "
                f"'rule' discriminator: {c!r}. The registry artifact was not "
                f"produced by the live registry writer — regenerate it "
                f"(write_registry_metadata / build_data.py) rather than "
                f"loading a hand-built or stale snapshot."
            )
        constraints.append(dict(c))
    return constraints


def _position_fields_from_json(entry: dict) -> dict[str, Any]:
    """The position-layer declarations of one generated entry (spec 03-R62).

    Keep-by-omission on the wire: ``binding`` / ``trade_safe`` /
    ``factory_expansion`` absent = None, ``preserves_size`` absent = False,
    ``causal`` absent = True. The hydrated signature equals the registered
    one, so the vendored validator's binding walk sees the same roles.
    """
    return {
        "binding": entry.get("binding"),
        "trade_safe": entry.get("trade_safe"),
        "preserves_size": bool(entry.get("preserves_size", False)),
        "causal": bool(entry.get("causal", True)),
        "factory_expansion": entry.get("factory_expansion"),
    }


def load_registry_from_json(data: dict | str) -> None:
    """Populate COMPONENT_REGISTRY from JSON data.

    Accepts the same format as ``GET /v1/components/metadata`` response:

    .. code-block:: json

        {
            "components": [
                {
                    "name": "EWMACrossover",
                    "category": "indicator",
                    "input_type": "OHLCVDict",
                    "output_type": "SignalSeries",
                    "parameters": [...],
                    ...
                }
            ]
        }

    Or a path to a JSON file (str).

    This function is used by the SDK to load bundled registry data without
    importing the ``components`` package. In the monorepo, it can be used
    for testing registry-based tools against a static snapshot.
    """
    import json as _json
    from pathlib import Path

    if isinstance(data, (str, Path)):
        path = Path(data)
        data = _json.loads(path.read_text())

    components = data.get("components", [])

    for comp in components:
        name = comp["name"]
        category_str = comp.get("category", "")
        try:
            category = StepCategory(category_str)
        except ValueError:
            logger.warning("Unknown category '%s' for component '%s', skipping", category_str, name)
            continue

        input_type = _resolve_type_name(comp.get("input_type", "Any"))
        output_type = _resolve_type_name(comp.get("output_type", "Any"))

        # Parse parameters
        parameters: dict[str, RegistryParamInfo] = {
            p["name"]: _param_from_json(p, name, merge_top_level=True)
            for p in comp.get("parameters", [])
        }

        slot_reads = _slot_reads_from_json(comp.get("implicit_slot_reads", []), parameters)

        # Hydrate param constraints VERBATIM (Q-1315): see _constraints_from_json.
        param_constraints = _constraints_from_json(comp.get("param_constraints", []), name)

        # Parse version info
        version = comp.get("version", 1)
        status = comp.get("status", "active")
        changelog_raw = comp.get("changelog", {})
        changelog = (
            {int(k): v for k, v in changelog_raw.items()}
            if changelog_raw
            else {1: "Initial release"}
        )

        # Build per-version entries from "versions" field if present
        versions_data = comp.get("versions", {})

        sig = ComponentSignature(
            cls=type(name, (), {"__name__": name}),  # Stub class
            name=name,
            input_type=input_type,
            output_type=output_type,
            category=category,
            deterministic=comp.get("deterministic", True),
            slot_reads=slot_reads,
            slot_writes=[],
            parameters=parameters,
            description=comp.get("description", ""),
            usage_hint=comp.get("usage_hint", ""),
            sub_category=comp.get("sub_category"),
            param_constraints=param_constraints,
            declaration_refs=comp.get("declaration_refs") or {},
            optional_declaration_refs=comp.get("optional_declaration_refs") or {},
            composer_inputs=_composer_inputs_from_json(comp.get("composer_inputs"), name),
            version=version,
            status=status,
            # Shell redirect (dsl-mtf-clocks spec 02 §8.2) — per-version, like
            # the typing contracts: only the constrained version is unrunnable.
            not_runnable=comp.get("not_runnable"),
            # Deprecation successor — component-level, like ``status``.
            replacement=comp.get("replacement"),
            replacement_shape=replacement_shape_from_json(comp.get("replacement_shape"), name),
            # Known issue (spec 04-R2) — VERSION-scoped: the latest entry's
            # own record lives in its per-version spec, mirrored top-level.
            known_issue=known_issue_from_json(
                (versions_data.get(str(version)) or {}).get("known_issue", comp.get("known_issue")),
                name,
            ),
            changelog=changelog,
            # dsl-type-system A1 typing fields (spec 02 §4.1) — hydrated
            # verbatim from the generated artifact; the SDK NEVER re-derives
            # them (tokens-only, §3.6). Absent keys mean the artifact
            # predates A1 or the field carries its default.
            type_relation=comp.get("type_relation"),
            domain_transfer=comp.get("domain_transfer"),
            input_domain=comp.get("input_domain"),
            output_domain=comp.get("output_domain"),
            slot_write_transfer=comp.get("slot_write_transfer"),
            record_tolerant=comp.get("record_tolerant"),
            clock_transfer=comp.get("clock_transfer"),
            population_scope=comp.get("population_scope"),
            # Position layer (spec 03-R62): keep-by-omission, hydrated
            # verbatim (the generated metadata already validated them).
            **_position_fields_from_json(comp),
        )

        if name not in COMPONENT_REGISTRY:
            COMPONENT_REGISTRY[name] = {}
        COMPONENT_REGISTRY[name][version] = sig

        # Also load additional versions if provided
        for ver_str, ver_data in versions_data.items():
            ver_num = int(ver_str)
            if ver_num == version:
                continue  # Already loaded as the primary entry

            ver_params: dict[str, RegistryParamInfo] = {
                p["name"]: _param_from_json(p, f"{name}@v{ver_num}", merge_top_level=False)
                for p in ver_data.get("parameters", [])
            }

            ver_sig = ComponentSignature(
                cls=type(name, (), {"__name__": name}),
                name=name,
                input_type=_resolve_type_name(
                    ver_data.get("input_type", comp.get("input_type", "Any"))
                ),
                output_type=_resolve_type_name(
                    ver_data.get("output_type", comp.get("output_type", "Any"))
                ),
                category=category,
                deterministic=comp.get("deterministic", True),
                # Version-scoped (Q-2202), never inherited from the latest
                # entry: a v2 may add a slot-reference parameter, an implicit
                # read or a cross-param constraint that its v1 never had.
                # Until 2026-09-28 all three came from the latest entry, so an
                # older pin was checked for the NEWER version's slot reads
                # (RollingUniverseMask v1 -> SLOT_REF_NOT_FOUND
                # dollar_volume_slot) and constraints, and a version listing
                # no parameters silently took the latest version's.
                slot_reads=_slot_reads_from_json(
                    ver_data.get("implicit_slot_reads", []), ver_params
                ),
                slot_writes=[],
                parameters=ver_params,
                description=comp.get("description", ""),
                usage_hint=comp.get("usage_hint", ""),
                sub_category=comp.get("sub_category"),
                param_constraints=_constraints_from_json(
                    ver_data.get("param_constraints", []), f"{name}@v{ver_num}"
                ),
                # Version-scoped too (Q-2205): a version that declares no
                # refs (build_data emits None) has none — falling back to the
                # latest entry's made RealizedVolatilityRegime v1 demand v2's
                # globals.target_timeframe (false MISSING_DECLARATION_REF).
                declaration_refs=ver_data.get("declaration_refs") or {},
                optional_declaration_refs=ver_data.get("optional_declaration_refs") or {},
                # Version-scoped (never inherited from the latest entry): the
                # composer role contract is part of a version's signature.
                composer_inputs=_composer_inputs_from_json(
                    ver_data.get("composer_inputs"), f"{name}@v{ver_num}"
                ),
                version=ver_num,
                status=comp.get("status", "active"),
                not_runnable=ver_data.get("not_runnable"),
                replacement=comp.get("replacement"),
                replacement_shape=replacement_shape_from_json(
                    comp.get("replacement_shape"), f"{name}@v{ver_num}"
                ),
                # Version-scoped (spec 04-R2): never inherited from the latest.
                known_issue=known_issue_from_json(
                    ver_data.get("known_issue"), f"{name}@v{ver_num}"
                ),
                changelog={ver_num: ver_data.get("changelog_entry", f"v{ver_num}")},
                # dsl-type-system A1 typing fields — per-version values only
                # (never inherited from the latest entry: typing contracts
                # are version-scoped, exactly like input/output types).
                type_relation=ver_data.get("type_relation"),
                domain_transfer=ver_data.get("domain_transfer"),
                input_domain=ver_data.get("input_domain"),
                output_domain=ver_data.get("output_domain"),
                slot_write_transfer=ver_data.get("slot_write_transfer"),
                record_tolerant=ver_data.get("record_tolerant"),
                # Version-scoped, exactly like the typing contracts above.
                clock_transfer=ver_data.get("clock_transfer"),
                population_scope=ver_data.get("population_scope"),
                **_position_fields_from_json(ver_data),
            )
            COMPONENT_REGISTRY[name][ver_num] = ver_sig

    logger.info("Loaded %d components from JSON into COMPONENT_REGISTRY", len(components))


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT VERSION CEILING (ingestion-unification, GOAL invariant 5)
# ═══════════════════════════════════════════════════════════════════════════════

#: Environment variable holding the per-process version ceiling.
#:
#: WHY THIS EXISTS. A component version can ship code that reads a table the
#: ENVIRONMENT does not have yet: the ctx family's v2 loaders read
#: ``market_data.ctx_1m`` and nothing else (ingestion-unification spec 04
#: R2/R5), and prod's ``ctx_1m`` stays empty until the founder's writer flip
#: plus a multi-hour backfill. An UNPINNED strategy resolves at
#: :func:`get_latest` (``pipeline.compile._resolve_component_class`` arm 2 —
#: defined behaviour, not substitution), so on the release that lands v2 every
#: unpinned ctx strategy would resolve to code the environment cannot serve.
#: The project's dark-by-default invariant (GOAL invariant 5) had a writer-side
#: flag for every new writer and NO consumer-side seam; this is that seam.
#:
#: FORMAT: ``Name=version,Name=version`` — e.g.
#: ``OpenInterestLoader=1,PremiumLoader=1,PredictedFundingLoader=1``.
#: Strict: every entry is ``Name=<int>``, no defaults, no partial entries, no
#: duplicate names. Unset (or empty) is the default EVERYWHERE and means no
#: ceiling — resolution is byte-identical to having no ceiling code at all.
#:
#: SCOPE: the ceiling moves only the UNPINNED answer. An explicit pin is an
#: assertion (spec 01 §2.1 D2, pins-assert) and still resolves above the
#: ceiling — silently downgrading a pin would be exactly the substitution
#: that rule forbids. Pins go through :func:`get_version`, which this does
#: not touch.
VERSION_CEILING_ENV = "KEEL_COMPONENT_VERSION_CEILING"


class VersionCeilingError(RuntimeError):
    """A ``KEEL_COMPONENT_VERSION_CEILING`` that cannot be honoured.

    Deliberately NOT a :class:`~pipeline_engine.exceptions.LockError` /
    ``ComponentVersionError`` subclass: those are per-strategy verdicts that
    callers catch and render as a 422. A ceiling that does not parse, or that
    names a component or a version the registry does not have, is a
    MISCONFIGURATION of the process — it must reach the operator as a process
    failure, not be absorbed into one strategy's error envelope.
    """


#: Parse cache. ``None`` = the environment has not been read yet in this
#: process; ``{}`` = read, no ceiling in force.
_version_ceiling_cache: dict[str, int] | None = None

#: Whether the ceiling has been checked against a loaded registry
#: (:func:`validate_version_ceiling`). Only ever set on success.
_version_ceiling_validated: bool = False


def parse_version_ceiling(raw: str) -> dict[str, int]:
    """Parse a ``Name=version,Name=version`` ceiling string. Strict.

    ONE correct behaviour, else raise (no silent fallbacks in resolvers).
    Every rejection is enumerated rather than defaulted: a ceiling that
    half-parses would silently leave a component unceilinged, which is the
    exact failure the ceiling exists to prevent.
    """
    out: dict[str, int] = {}
    for token in raw.split(","):
        entry = token.strip()
        if not entry:
            raise VersionCeilingError(
                f"{VERSION_CEILING_ENV}={raw!r}: empty entry. The format is "
                f"'Name=version,Name=version' with no trailing or repeated commas."
            )
        name, sep, version_text = entry.partition("=")
        name = name.strip()
        version_text = version_text.strip()
        if not sep or not name or not version_text:
            raise VersionCeilingError(
                f"{VERSION_CEILING_ENV}={raw!r}: entry {entry!r} is not 'Name=version'."
            )
        if not version_text.isdigit():
            raise VersionCeilingError(
                f"{VERSION_CEILING_ENV}={raw!r}: entry {entry!r} has a non-integer "
                f"version {version_text!r}. Versions are positive integers "
                f"('OpenInterestLoader=1'), never 'v1', '1.0' or a range."
            )
        version = int(version_text)
        if version < 1:
            raise VersionCeilingError(
                f"{VERSION_CEILING_ENV}={raw!r}: entry {entry!r} has version {version}; "
                f"component versions start at 1."
            )
        if name in out:
            raise VersionCeilingError(
                f"{VERSION_CEILING_ENV}={raw!r}: component {name!r} appears twice "
                f"(={out[name]} and ={version}). A ceiling names each component once."
            )
        out[name] = version
    return out


def component_version_ceiling() -> dict[str, int]:
    """The version ceiling in force for this process; ``{}`` when unset.

    Read from the environment ONCE and memoised, so the ceiling cannot change
    under a running process and the "one line per process" log below is
    exactly one line. An absent variable and an empty/whitespace one are the
    SAME statement — no ceiling — and both are silent; only a non-empty value
    parses, logs, and constrains.
    """
    global _version_ceiling_cache
    if _version_ceiling_cache is None:
        raw = os.environ.get(VERSION_CEILING_ENV) or ""
        # A failed parse leaves the cache unset on purpose: the next call
        # re-reads and raises again, rather than caching a half-answer.
        parsed = parse_version_ceiling(raw) if raw.strip() else {}
        _version_ceiling_cache = parsed
        if parsed:
            logger.warning(
                "%s in force: %s — unpinned components resolve at or below these "
                "versions (explicit pins are unaffected)",
                VERSION_CEILING_ENV,
                ",".join(f"{n}={v}" for n, v in sorted(parsed.items())),
            )
    return _version_ceiling_cache


def validate_version_ceiling() -> None:
    """Assert every ceiling entry names a registered component AND version.

    Called from ``registry_loader.ensure_registry_loaded()`` — the one point
    where COMPONENT_REGISTRY is guaranteed complete, and the documented
    precondition of every registry lookup. A typo'd component name would
    otherwise ceiling NOTHING while reading as a ceiling in force, which is a
    silent no-op in the dangerous direction: the component the operator meant
    to hold back resolves at latest anyway.

    :func:`get_latest` carries the per-name half of the same assertion, so a
    ceilinged name that is not registered raises at the resolution itself even
    on a path that never called ``ensure_registry_loaded``.
    """
    global _version_ceiling_validated
    if _version_ceiling_validated:
        return
    ceiling = component_version_ceiling()
    if not ceiling:
        return
    problems: list[str] = []
    for name, version in sorted(ceiling.items()):
        versions = COMPONENT_REGISTRY.get(name)
        if not versions:
            problems.append(f"{name}={version}: no such component in the registry")
        elif version not in versions:
            problems.append(
                f"{name}={version}: version {version} is not registered "
                f"(registered: {sorted(versions)})"
            )
    if problems:
        raise VersionCeilingError(
            f"{VERSION_CEILING_ENV} cannot be honoured: " + "; ".join(problems)
        )
    _version_ceiling_validated = True


def _reset_version_ceiling_cache() -> None:
    """Forget the parsed ceiling. TEST-ONLY (and the co-located test proves it).

    Production reads the environment once per process by design; this exists
    so a test can drive several ceilings in one interpreter.
    """
    global _version_ceiling_cache, _version_ceiling_validated
    _version_ceiling_cache = None
    _version_ceiling_validated = False


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY ACCESSORS
# ═══════════════════════════════════════════════════════════════════════════════


def get_latest(name: str) -> ComponentSignature | None:
    """Return the highest-version ComponentSignature for *name*, or None.

    THE UNPINNED ANSWER. With ``KEEL_COMPONENT_VERSION_CEILING`` set for
    *name*, "highest" means the highest registered version AT OR BELOW the
    ceiling (see :data:`VERSION_CEILING_ENV`); names the ceiling does not
    mention are unaffected. Explicit pins do not come through here.

    Raises:
        VersionCeilingError: If the ceiling names *name* but the registry has
            no such component, or does not have the ceilinged version. Both
            are misconfiguration: a ceiling that cannot be honoured must never
            degrade into resolving at latest.
    """
    versions = COMPONENT_REGISTRY.get(name)
    ceiling_version = component_version_ceiling().get(name)
    if ceiling_version is None:
        if not versions:
            return None
        return versions[max(versions)]
    if not versions:
        raise VersionCeilingError(
            f"{VERSION_CEILING_ENV} names {name!r}={ceiling_version}, but "
            f"{name!r} has no registered versions."
        )
    if ceiling_version not in versions:
        raise VersionCeilingError(
            f"{VERSION_CEILING_ENV} names {name!r}={ceiling_version}, but version "
            f"{ceiling_version} of {name!r} is not registered "
            f"(registered: {sorted(versions)})."
        )
    # The ceilinged version is registered (asserted above), so it IS the
    # highest registered version at or below the ceiling.
    return versions[ceiling_version]


def get_version(name: str, version: int) -> ComponentSignature | None:
    """Return a specific version of *name*, or None."""
    versions = COMPONENT_REGISTRY.get(name)
    if not versions:
        return None
    return versions.get(version)


def get_all_versions(name: str) -> dict[int, ComponentSignature]:
    """Return all versions for *name* (empty dict if unknown)."""
    return dict(COMPONENT_REGISTRY.get(name, {}))


def _pin_resolution_error(name: str, pinned: int) -> "Exception":
    """Build the spec-01 §2.1 structured error for an unresolvable version pin.

    The SINGLE construction point for pin-enforcement errors, used by BOTH
    execution paths — blob reconstruct (``compile._resolve_component_class``)
    and live source (``_build_effective_registry`` below) — so the payload
    (code, component, pinned/latest/available versions, changelog,
    remediation) is unified by construction (spec 01 §2.1).

    Returns (never raises) a :class:`~pipeline_engine.exceptions.ComponentVersionError`:

    - name registered but pinned version absent → ``COMPONENT_VERSION_PHASED_OUT``
    - name not registered at all → ``COMPONENT_UNREGISTERED``
    """
    # pipeline_engine.exceptions is import-cycle-free (imports nothing from
    # pipeline_engine) and bundled in the SDK alongside this module.
    from pipeline_engine.exceptions import ComponentVersionError

    versions = get_all_versions(name)
    if not versions:
        return ComponentVersionError(
            code="COMPONENT_UNREGISTERED",
            component=name,
            pinned_version=pinned,
            remediation=(
                f"This strategy references the component '{name}', which is "
                f"no longer available on the platform. Open the strategy in "
                f"the editor, replace or upgrade the component, and re-save — "
                f"or contact support if you believe this component should "
                f"still exist."
            ),
            detail=f"'{name}' has no registered versions (pinned v{pinned}).",
        )

    latest = max(versions)
    changelog: dict[int, str] = {}
    for ver in sorted(versions):
        entry = versions[ver].changelog.get(ver)
        if entry:
            changelog[ver] = entry
    return ComponentVersionError(
        code="COMPONENT_VERSION_PHASED_OUT",
        component=name,
        pinned_version=pinned,
        latest_version=latest,
        available_versions=sorted(versions),
        changelog=changelog,
        remediation=(
            f"This strategy is pinned to a retired version of {name}. Open "
            f"the strategy in the editor and apply the component upgrade "
            f"(Components → Upgrade), then re-run."
        ),
        detail=(
            f"Locked version {pinned} for component '{name}' not found in "
            f"registry; available versions: {sorted(versions)} (latest v{latest})."
        ),
    )


def _build_effective_registry(
    lock: dict[str, int],
) -> dict[str, ComponentSignature]:
    """Build a flat name → ComponentSignature view from a version lock.

    Args:
        lock: Mapping of component name → pinned version number.

    Returns:
        Flat dict suitable for validator / resolver passes.

    Raises:
        ComponentVersionError: If a locked component or version is not
            registered. Subclasses ``LockError``, so every existing
            ``except LockError`` site still catches it; the payload now
            carries the same structured fields as the blob-reconstruct
            path (spec 01 §2.1 unification — one error shape, both paths).
    """
    flat: dict[str, ComponentSignature] = {}
    for name, ver in lock.items():
        sig = get_version(name, ver)
        if sig is None:
            raise _pin_resolution_error(name, ver)
        flat[name] = sig
    return flat


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


def unwrap_annotated(t: type) -> type:
    """Unwrap Annotated[T, ...] to its base type T.

    Returns the type unchanged if it's not an Annotated type.
    Annotated types (e.g., NormalizedSignal = Annotated[SignalSeries, Bounds(-1, 1)])
    wrap a base type with metadata. For compatibility checking, we compare
    the base types — Annotated is covariant (Annotated compatible with base).
    """
    if get_origin(t) is Annotated:
        args = get_args(t)
        if args:
            return args[0]
    return t


def is_factory(sig: ComponentSignature) -> bool:
    """The one predicate for a registered factory (position-layer spec 03-R45).

    A factory is a registered component whose ``run()`` raises: it never
    appears as a step in a blob (its expansion does), so registry-wide sweeps
    that instantiate or run components exclude it through this predicate.
    """
    return sig.binding is not None and sig.binding.get("role") == "factory"


def is_compatible(output_type: type, input_type: type) -> bool:
    """Check if output_type can flow into input_type.

    Handles:
    - Exact match: PriceFrame -> PriceFrame
    - Any matches anything
    - None type handling
    - Union types (asymmetric — see below)
    - Annotated type unwrapping (PEP 593)
    - NewType unwrapping
    - Generic type args

    Union type asymmetry:
        Union *inputs* are compatible if ANY variant matches. The receiving
        step declares "I accept any of these types", so a single concrete
        output satisfying one variant is sufficient.

        Union *outputs* are compatible only if ALL variants match. The
        producing step may emit any of the union members at runtime, so we
        must guarantee that every possible output is accepted by the
        downstream input type.
    """
    if output_type is Any or input_type is Any:
        return True

    if output_type is type(None) and input_type is type(None):
        return True

    if output_type is input_type:
        return True

    # Annotated types: unwrap before further checks.
    # Annotated[SignalSeries, Bounds(-1, 1)] is compatible with SignalSeries.
    # Two different Annotated types with the same base are compatible
    # (covariant semantics — the metadata constrains values, not types).
    output_unwrapped = unwrap_annotated(output_type)
    input_unwrapped = unwrap_annotated(input_type)

    # If either was Annotated, re-check with unwrapped types
    if output_unwrapped is not output_type or input_unwrapped is not input_type:
        return is_compatible(output_unwrapped, input_unwrapped)

    # Union types
    if get_origin(input_type) is Union:
        input_variants = get_args(input_type)
        return any(is_compatible(output_type, v) for v in input_variants)

    if get_origin(output_type) is Union:
        output_variants = get_args(output_type)
        return all(is_compatible(v, input_type) for v in output_variants)

    # NewType handling: if both are NewTypes, they must be the same NewType
    # (identity was already checked above). Different NewTypes wrapping the
    # same base (e.g., PriceFrame vs SignalSeries) are NOT compatible.
    output_is_newtype = hasattr(output_type, "__supertype__")
    input_is_newtype = hasattr(input_type, "__supertype__")

    if output_is_newtype and input_is_newtype:
        # Walk output's supertype chain: StreamSeries → SignalSeries is OK
        sup = getattr(output_type, "__supertype__", None)
        while sup is not None:
            if sup is input_type:
                return True
            sup = getattr(sup, "__supertype__", None)
        return False  # Different NewTypes with no subtype relationship

    # NewType → base type compatibility (e.g., PriceFrame → pd.DataFrame)
    output_base = getattr(output_type, "__supertype__", output_type)
    input_base = getattr(input_type, "__supertype__", input_type)

    # Subtype check.
    # TypeError is caught because issubclass() raises it for non-class args
    # that pass isinstance(x, type) but aren't valid class objects (e.g.,
    # certain generic aliases on older Python versions).
    try:
        if isinstance(output_base, type) and isinstance(input_base, type):
            return issubclass(output_base, input_base)
    except TypeError:
        logger.debug(
            "issubclass(%s, %s) raised TypeError; falling through to generic check",
            output_base,
            input_base,
        )

    # Generic types
    output_origin = get_origin(output_type)
    input_origin = get_origin(input_type)

    if output_origin and input_origin:
        if output_origin is input_origin:
            output_args = get_args(output_type)
            input_args = get_args(input_type)
            if len(output_args) == len(input_args):
                return all(is_compatible(o, i) for o, i in zip(output_args, input_args))

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY QUERIES
# ═══════════════════════════════════════════════════════════════════════════════


def find_components_accepting(input_type: type) -> list[ComponentSignature]:
    """Find all components that can accept the given input type."""
    return [
        sig
        for name in COMPONENT_REGISTRY
        if (sig := get_latest(name)) is not None and is_compatible(input_type, sig.input_type)
    ]


def find_components_outputting(output_type: type) -> list[ComponentSignature]:
    """Find all components that output the given type."""
    return [
        sig
        for name in COMPONENT_REGISTRY
        if (sig := get_latest(name)) is not None and is_compatible(sig.output_type, output_type)
    ]


def find_components_after(component_name: str) -> list[ComponentSignature]:
    """Find all components that can follow the given component.

    Raises:
        KeyError: If component_name is not in the registry.
    """
    if component_name not in COMPONENT_REGISTRY:
        raise KeyError(
            f"Component '{component_name}' not found in registry. "
            f"Available: {list(COMPONENT_REGISTRY.keys())}"
        )
    source = get_latest(component_name)
    return _facet_after(source, find_components_accepting(source.output_type))


def find_components_before(component_name: str) -> list[ComponentSignature]:
    """Find all components that can precede the given component.

    Raises:
        KeyError: If component_name is not in the registry.
    """
    if component_name not in COMPONENT_REGISTRY:
        raise KeyError(
            f"Component '{component_name}' not found in registry. "
            f"Available: {list(COMPONENT_REGISTRY.keys())}"
        )
    target = get_latest(component_name)
    return _facet_before(target, find_components_outputting(target.input_type))


# ── Type-flow neighbours see the binding facet (position-layer spec 03-R65) ───
#
# Base types alone say a TradeReturn's SignalSeries may feed any of ~150
# components, but inside a TradeManager's rules only trade-safe components,
# actions and trade ops may take a trade value (03-R15). The neighbour
# answers therefore narrow by ROLE where the role decides the facet — and
# only there, so every ordinary component's answer is exactly the base-type
# one. The SDK's baked type_graph.json carries these per-component answers
# (build_data.py), so both surfaces return the same set (the Q-0732 parity).


def _role_of(sig: ComponentSignature) -> str | None:
    decl = sig.binding
    return decl.get("role") if isinstance(decl, dict) else None


def _is_mask_domain(domain: Any) -> bool:
    """An output domain within {0, 1} (an action's mask input, 03-R13)."""
    if isinstance(domain, dict):
        if "set" in domain:
            return {float(v) for v in domain["set"]} <= {0.0, 1.0}
        return domain.get("alias") == "Mask"
    return False


def _facet_after(source: ComponentSignature, cands: list[ComponentSignature]) -> list:
    role = _role_of(source)
    if role in ("reader", "trade_op"):
        # A trade (or between-trade) value: certified ops, actions, trade ops.
        return [
            s for s in cands if s.trade_safe is not None or _role_of(s) in ("action", "trade_op")
        ]
    if role == "head":
        # The Position: readers, the realizers, and factories (rule sugar).
        return [
            s for s in cands if _role_of(s) in ("reader", "realizer", "position_sizer", "factory")
        ]
    return cands


def _facet_before(target: ComponentSignature, cands: list[ComponentSignature]) -> list:
    role = _role_of(target)
    if role == "action":
        # An action takes a 0/1 mask over a trade: certified ops and mask readers.
        return [
            s
            for s in cands
            if s.trade_safe is not None
            or (_role_of(s) == "reader" and _is_mask_domain(s.output_domain))
        ]
    if role in ("realizer", "position_sizer"):
        # The Position comes from the head or an action (or a factory call).
        return [s for s in cands if _role_of(s) in ("head", "action", "factory")]
    return cands


def find_components_by_category(category: StepCategory) -> list[ComponentSignature]:
    """Find all components in a category."""
    return [
        sig
        for name in COMPONENT_REGISTRY
        if (sig := get_latest(name)) is not None and sig.category == category
    ]


def search(
    *,
    input_type: type | None = None,
    output_type: type | None = None,
    category: StepCategory | None = None,
    keyword: str | None = None,
    deterministic: bool | None = None,
) -> list[ComponentSignature]:
    """Unified search across all registered components.

    All filters are applied conjunctively (AND). Only components matching
    every provided criterion are returned.

    Args:
        input_type: Filter to components that accept this input type.
        output_type: Filter to components that produce this output type.
        category: Filter to components in this category.
        keyword: Case-insensitive substring match against component name
            and description.
        deterministic: Filter to components by deterministic flag.
            True = cacheable, False = non-cacheable (e.g., data loaders).

    Returns:
        List of matching ComponentSignature objects.
    """
    results: list[ComponentSignature] = [
        sig for name in COMPONENT_REGISTRY if (sig := get_latest(name)) is not None
    ]

    if deterministic is not None:
        results = [sig for sig in results if sig.deterministic == deterministic]

    if input_type is not None:
        results = [sig for sig in results if is_compatible(input_type, sig.input_type)]

    if output_type is not None:
        results = [sig for sig in results if is_compatible(sig.output_type, output_type)]

    if category is not None:
        results = [sig for sig in results if sig.category == category]

    if keyword is not None:
        kw_lower = keyword.lower()
        results = [
            sig
            for sig in results
            if kw_lower in sig.name.lower() or kw_lower in sig.description.lower()
        ]

    return results


__all__ = [
    "KNOWN_ISSUE_ID_PATTERN",
    "KnownIssue",
    "ReplacementShape",
    "known_issue_from_json",
    "replacement_shape_from_json",
    "ParamTier",
    "RegistryParamInfo",
    "ComponentSignature",
    "COMPONENT_REGISTRY",
    "MISSING",
    "load_registry_from_json",
    "get_latest",
    "get_version",
    "get_all_versions",
    "_build_effective_registry",
    "_pin_resolution_error",
    "_resolve_type_name",
    "_resolve_param_type",
    "unwrap_annotated",
    "is_compatible",
    "find_components_accepting",
    "find_components_outputting",
    "find_components_after",
    "find_components_before",
    "find_components_by_category",
    "search",
]
