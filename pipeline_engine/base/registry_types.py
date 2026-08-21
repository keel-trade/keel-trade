"""Component registry data model, queries, and type compatibility (SDK-safe).

Contains the data classes, global registry, JSON loading, type compat checks,
and search/query functions. No inspect, no get_type_hints, no numpy/pandas.
Importable by both the monorepo and the SDK.
"""

from __future__ import annotations

import logging
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
        parameters: dict[str, RegistryParamInfo] = {}
        for p in comp.get("parameters", []):
            p_default = p.get("default")
            p_required = p.get("required", False)
            constraints = p.get("constraints", {})
            # Merge top-level min/max/options into constraints
            if p.get("min") is not None:
                constraints.setdefault("min", p["min"])
            if p.get("max") is not None:
                constraints.setdefault("max", p["max"])
            if p.get("options"):
                constraints.setdefault("options", p["options"])

            parameters[p["name"]] = RegistryParamInfo(
                name=p["name"],
                type_=_resolve_param_type(p.get("type", "Any")),
                default=MISSING if p_required and p_default is None else p_default,
                required=p_required,
                description=p.get("description", ""),
                suggestions=p.get("suggestions", []),
                optimizable=p.get("optimizable", True),
                constraints=constraints,
                tier=ParamTier(p.get("tier", "strategy")),
                slot_reference=p.get("slot_reference", False),
                expected_slot_type=(
                    _resolve_type_name(p["expected_slot_type"])
                    if p.get("expected_slot_type")
                    else None
                ),
            )

        # Parse slot reads from implicit_slot_reads + slot_reference params
        slot_reads: dict[str, type] = {}
        for sr_name in comp.get("implicit_slot_reads", []):
            slot_reads[sr_name] = Any
        for pname, pinfo in parameters.items():
            if pinfo.slot_reference and pinfo.expected_slot_type is not None:
                slot_reads[pname] = pinfo.expected_slot_type

        # Parse param constraints
        param_constraints = []
        for c in comp.get("param_constraints", []):
            param_constraints.append(
                {
                    "params": c.get("params", []),
                    "type": c.get("rule", ""),
                }
            )

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
            version=version,
            status=status,
            # Shell redirect (dsl-mtf-clocks spec 02 §8.2) — per-version, like
            # the typing contracts: only the constrained version is unrunnable.
            not_runnable=comp.get("not_runnable"),
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
        )

        if name not in COMPONENT_REGISTRY:
            COMPONENT_REGISTRY[name] = {}
        COMPONENT_REGISTRY[name][version] = sig

        # Also load additional versions if provided
        for ver_str, ver_data in versions_data.items():
            ver_num = int(ver_str)
            if ver_num == version:
                continue  # Already loaded as the primary entry

            ver_params: dict[str, RegistryParamInfo] = {}
            for p in ver_data.get("parameters", []):
                p_default = p.get("default")
                p_required = p.get("required", False)
                ver_params[p["name"]] = RegistryParamInfo(
                    name=p["name"],
                    type_=_resolve_param_type(p.get("type", "Any")),
                    default=MISSING if p_required and p_default is None else p_default,
                    required=p_required,
                    description=p.get("description", ""),
                    suggestions=p.get("suggestions", []),
                    optimizable=p.get("optimizable", True),
                    constraints=p.get("constraints", {}),
                    tier=ParamTier(p.get("tier", "strategy")),
                    slot_reference=p.get("slot_reference", False),
                )

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
                slot_reads=slot_reads,
                slot_writes=[],
                parameters=ver_params or parameters,
                description=comp.get("description", ""),
                usage_hint=comp.get("usage_hint", ""),
                sub_category=comp.get("sub_category"),
                param_constraints=param_constraints,
                declaration_refs=ver_data.get("declaration_refs")
                or comp.get("declaration_refs")
                or {},
                optional_declaration_refs=ver_data.get("optional_declaration_refs")
                or comp.get("optional_declaration_refs")
                or {},
                version=ver_num,
                status=comp.get("status", "active"),
                not_runnable=ver_data.get("not_runnable"),
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
            )
            COMPONENT_REGISTRY[name][ver_num] = ver_sig

    logger.info("Loaded %d components from JSON into COMPONENT_REGISTRY", len(components))


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY ACCESSORS
# ═══════════════════════════════════════════════════════════════════════════════


def get_latest(name: str) -> ComponentSignature | None:
    """Return the highest-version ComponentSignature for *name*, or None."""
    versions = COMPONENT_REGISTRY.get(name)
    if not versions:
        return None
    return versions[max(versions)]


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
    return find_components_accepting(source.output_type)


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
    return find_components_outputting(target.input_type)


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
