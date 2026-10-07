"""The judgment-table interpreter walk (dsl-type-system spec 03 §2-§3, T-M2b-3).

ONE walk implements passes 6+8: J-PIPE left-folds J-STEP by step kind;
J-REC/J-PROJ carry closed records through Parallel/Extract; Σ is threaded
with spec 01 §6.3's unified snapshot/last-write-wins merge; slot-lifecycle
outcomes (J-LOAD/J-SLOTREAD + the usage lint) are JOURNALED and flushed at
the pass-8 position (§2.4); the four §3.5 recovery rows are the only
checker-introduced ``Any``. The m2-compat profile (all three ``flow_config``
keys ``pre`` + the pre-flip relation vector) reproduces today's
``_validate_type_flow``/``_validate_slots`` verdicts, kwargs, TypeFlowEntry
list, slot boundary map, and schema-0 trace bit-for-bit; the end-state
profile adds J-INST/J-LIT base refinement, precise Extract projection, and
flow-wide composer/record reach (§3.4).

LANDS BESIDE (spec 03 §6 Step 1): ``validate_strategy`` still runs the old
code — nothing here is wired into the live pass spine. The shadow parity
harness in ``interpreter_test.py`` is the only caller.

TOKENS-ONLY (spec 02 §3.6): checking decisions consult ``relation``/
``transfers`` over generated tables and canonical ``type_name()`` strings —
never ``is_compatible``/``_is_slot_compatible``/``issubclass``/
``__supertype__``. ``FlowVal.obj`` carries the originating declared type
object solely for the §2.5 resolver boundary projection and live-parity
rendering; no relation is ever derived from it. Staged verdicts resolve
their stage from ``STAGED_CHANGES`` (spec 05 §2.2): DORMANT entries (and
pre-state flow shapes) divert to the ``staged_outcomes`` channel — a
dormant code must stay structurally silent — while ARMED stages
(INCUBATING/WARNING/PROMOTED, or a post-state flow shape) surface through
``emit()`` with stage-resolved severity (spec 03 §3.4, 01 S1-AC10).

Missing tables raise a loud ``ImportError`` naming the regen command (no
silent fallbacks — lessons.md).
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any as TypingAny
from typing import Union, get_args, get_origin

from pipeline_engine.binding import MARKET, Facet, ScopeMachine, decl_from_signature
from pipeline_engine.dsl import clocks as C
from pipeline_engine.dsl import relation as R
from pipeline_engine.dsl import transfers as T
from pipeline_engine.dsl.catalog import RULES, STAGED_CHANGES, Stage
from pipeline_engine.dsl.judgments import FLOW_CONFIG_KEYS, emit_template
from pipeline_engine.dsl.spec import (
    ComponentRef,
    ParallelSpec,
    PipelineSpec,
    SlotExtractSpec,
    SlotLoadSpec,
    SlotStoreSpec,
    SlotStoreValueSpec,
    StrategyFile,
    VariableRef,
)
from pipeline_engine.dsl.validator import (
    _format_location,
    _format_params_brief,
    _store_value_slot_type,
    emit,
)
from pipeline_engine.validation_shared import (
    UNIVERSE_MASK_APPLIERS,
    DomainRef,
    ProvenanceHop,
    SuggestedEdit,
    TypeFlowEntry,
    TypeRef,
    type_name,
)


def _require(loader_name: str) -> dict:
    """Load a generated table once, loud-failing with the exact regen command."""
    from pipeline_engine.dsl.fixtures import loader

    try:
        return getattr(loader, f"load_{loader_name}")()
    except FileNotFoundError as exc:  # pragma: no cover - build error path
        raise ImportError(
            f"{loader_name}.json is missing — regenerate with: "
            f'python -c "from pipeline_engine.dsl.fixtures.loader import '
            f'write_{loader_name}; write_{loader_name}()"'
        ) from exc


_A3 = _require("judgment_tables")
_A6 = _require("validation_tables")
_ROWS: dict[str, dict] = {r["id"]: r for rows in _A3["step_kinds"].values() for r in rows}
_ROWS.update({r["id"]: r for r in _A3["exemptions"]})
_CODE = {rid: row["on_fail"]["code"] for rid, row in _ROWS.items() if "on_fail" in row}

# J-REC.empty-parallel (spec 01 §5.1 m7): a structural precondition, not a
# relation query — its code comes from the A3 row and its staged governance
# from the catalog rule's staged_by, so the check stays checking-as-data.
_EMPTY_PARALLEL_ROW = "J-REC.empty-parallel"
_EMPTY_PARALLEL_CODE = _CODE.get(_EMPTY_PARALLEL_ROW)
_EMPTY_PARALLEL_STAGED_BY = (
    RULES[_EMPTY_PARALLEL_CODE].staged_by if _EMPTY_PARALLEL_CODE in RULES else ""
) or None
_TRANSITIONS: dict[str, dict[str, list[str]]] = _A6["type_transitions"]
#: The Position base's live type (spec 03-R1): a Parallel that merges a rule
#: stage yields it (03-R17).
from pipeline_engine.types import Position as _POSITION_TYPE  # noqa: E402
from pipeline_engine.types import SignalSeries as _REALIZED_TYPE  # noqa: E402


_DICT_ROW_CATS = frozenset(_TRANSITIONS["dict"])
_COMPOSER_CATS = frozenset({"signal_composer", "forecast_composer"})


@dataclass(frozen=True)
class Profile:
    """A named staged configuration: the three flow keys + the relation vector."""

    name: str
    flow: dict[str, str]  # §3.4 flow_config keys → "pre" | "post"
    staged: dict[str, bool]  # STAGED_CHANGES id → fired (relation vector)

    def __post_init__(self) -> None:
        if set(self.flow) != set(FLOW_CONFIG_KEYS):
            raise ValueError(f"flow keys must be exactly {sorted(FLOW_CONFIG_KEYS)}")


M2_COMPAT_PROFILE = Profile("m2-compat", {k: "pre" for k in FLOW_CONFIG_KEYS}, R.M2_COMPAT)
END_STATE_PROFILE = Profile("end-state", {k: "post" for k in FLOW_CONFIG_KEYS}, R.END_STATE)


def shipped_profile() -> Profile:
    """The shipped configuration — end-state FLOW, sub-terminal relations.

    NOT m2-compat: all five flow keys have been at ``post`` since the M3
    flip-on (`composer-record-reach` since 2026-07-24), so ``.flow`` equals
    ``END_STATE_PROFILE.flow`` and only ``.staged`` is still short of the end
    state (19 of 21 fired at HEAD; `xs-before-universe-mask` and
    `mask-slot-not-wired` remain DORMANT). ``M2_COMPAT_PROFILE`` is a
    test/pinning profile, never what ``validate_strategy`` runs.

    Flow keys resolve from the live ``STAGED_CHANGES`` registry — the same
    source the A3 writer derives ``flow_config`` from (the freshness test
    keeps the generated block equal), so the test-only stage override
    reaches flow shapes too (01 S1-AC10 switchability, spec 05 §5.3).
    """
    flow = {k: str(STAGED_CHANGES[k].stage) for k in FLOW_CONFIG_KEYS}
    return Profile("shipped", flow, R.shipped_staged_active())


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITION ADVISORIES (Q-1694 — mcp-strategy-view W4 §1.4/§1.5/§1.7)
#
# Three SEMANTIC facts about a step given what flows INTO it, so they live at
# the consumer's J-STEP / J-COMPOSER position rather than in a validator pass.
# Every discriminator that CAN be read from the registry is
# (``target_leverage``, ``output_domain``); the one concept the registry does
# not isolate — "this sizer sizes by membership, not by magnitude" — is a
# documented per-component anchor set, the ``_THRESHOLD_ARM_COMPONENT`` /
# ``UNIVERSE_MASK_APPLIERS`` precedent.
# ═══════════════════════════════════════════════════════════════════════════

#: Position sizers that size by MEMBERSHIP (sign, or a fixed per-position
#: weight) and therefore discard the magnitude of whatever flows in. These are
#: the sizers whose registry ``input_type`` is SignalSeries/BinarySignal AND
#: whose documented semantics are selection-shaped; the magnitude-USING
#: sizers (VolWeightSizer, RiskBudgetSizer, RiskParityAllocator,
#: StopDistanceRiskSizer, MarketRiskScaler, MarketLeverageScaler,
#: BetaEstimator) are deliberately absent. Durable form: an
#: ``input_role="selection"`` registry declaration, at which point this
#: constant is deleted (the anchor is named in the rule's explain so the
#: follow-up is discoverable from the catalog alone).
_SELECTION_SIZERS = frozenset(
    {
        "BinaryToWeight",
        "DynamicEqualWeightAllocator",
        "EqualWeightAllocator",
        "EqualWeightSizer",
        "FixedWeightAllocator",
        "FixedWeightSizer",
        "ManualWeightAllocator",
    }
)


def _forecast_carrier() -> str:
    """The forecast carrier name, DERIVED from the generated A6 table.

    Tokens-only discipline (spec 02 §3.6): neither engine hardcodes a
    type-universe name. The forecast carrier is the unique output type of the
    ``forecast_mapper`` category in TYPE_TRANSITIONS (a forecast mapper's
    whole job is producing the conviction-bearing series) — the same
    derivation shape ``validator._terminal_weight_type()`` uses for the
    weight carrier, and the TS engine derives the same name from the served
    ``type_transitions``. A non-singleton derivation raises; never guess.

    That carrier SUBSUMES into a SignalSeries demand (type_compat_meta),
    which is exactly why the type system is silent on this shape.
    """
    outs = {out for row in _TRANSITIONS.values() for out in row.get("forecast_mapper", ())}
    if len(outs) != 1:
        raise ValueError(
            f"TYPE_TRANSITIONS no longer yields a unique forecast_mapper "
            f"output type ({sorted(outs)}) — the forecast-carrier derivation "
            f"behind FORECAST_MAGNITUDE_DISCARDED must be revisited."
        )
    return next(iter(outs))


_FORECAST_CARRIER = _forecast_carrier()


def _binary_carrier() -> str:
    """The binary carrier, DERIVED: the one transition key a signal_transform
    must PRESERVE (its ``signal_transform`` row is exactly itself).

    Tokens-only (spec 02 §3.6): the engine never spells a type-universe name;
    the TS twin derives the same key from the same served table. A
    non-singleton derivation raises — revisit, never guess.
    """
    keys = [k for k, row in _TRANSITIONS.items() if row.get("signal_transform") == [k]]
    if len(keys) != 1:
        raise ValueError(
            f"TYPE_TRANSITIONS no longer yields a unique self-preserving "
            f"signal_transform carrier ({sorted(keys)}) — the long-only bridge "
            f"behind TRANSITION_OUTPUT_MISMATCH must be revisited."
        )
    return keys[0]


_BINARY_CARRIER = _binary_carrier()


def _transition_suggestion(step: str, category: str, prev_key: str, expected: list) -> str:
    """TRANSITION_OUTPUT_MISMATCH's fix, selected at the site (spec 04 §2.3(a)).

    A signal_transform that turns a {-1, 0, +1} signal into a SignalSeries is
    almost always an attempt at long/cash (Clip(0, 1)); the fix that keeps the
    binary carrier is ThresholdCross(mode='long_only'). Every other shape
    renders the catalog template — rendered HERE, because an explicit
    suggestion is never re-rendered by emit().
    """
    if prev_key == _BINARY_CARRIER and category == "signal_transform":
        return emit_template("transition-binary-bridge").format(step=step)
    return RULES["TRANSITION_OUTPUT_MISMATCH"].suggestion_template.format(
        step=step, expected_outputs=expected
    )


#: The composer that stacks branch weight books side by side. The only
#: ``forecast_composer`` with a ``dict -> WeightSeries`` signature today; the
#: anchor is documented in NORMALIZER_BEFORE_CONCAT's explain.
_WEIGHT_CONCATENATOR = "WeightConcatenator"

#: The parameter that makes a step normalize its OWN output to a leverage
#: target. Registry-driven: any component carrying it sums to it alone, so N
#: such branches sum to N x target after concatenation.
_LEVERAGE_TARGET_PARAM = "target_leverage"

#: The parameters that set a WEIGHT BOOK's gross leverage when they sit on a
#: step after a WeightConcatenator: a normalizer's target or a LeverageCap's
#: ceiling (spec 04 §2.6 — the shapes NORMALIZER_BEFORE_CONCAT's own
#: suggestion names). Registry parameters, never component names.
_BOOK_LEVEL_PARAMS = ("target_leverage", "max_leverage")

#: Float slack on "the branch targets sum to at most 1.0" — 0.3 + 0.3 + 0.4
#: is a fully-invested book, not 1.0000000000000002 of one.
_BOOK_SUM_TOLERANCE = 1e-9

#: The staged changes governing the two composition ramps born DORMANT
#: (KICKOFF D-c for the concatenator rule; the pinned-golden-trace protocol
#: for the forecast rule — see their catalog log entries). A DORMANT code
#: must be STRUCTURALLY silent: ``severity_for`` raises rather than emit one,
#: so each site guards itself.
#: The slot-reference PARAMETERS that read a return-volatility series
#: (VolWeightSizer / RiskBudgetSizer `vol_slot`, VolTargetWeightConverter /
#: VolAttenuator / VolFloorScale / AdverseVolCap / IDMPortfolioAggregator
#: `return_vol_slot`, RiskParityAllocator `volatility_slot`). A documented
#: anchor set — the `_SELECTION_SIZERS` precedent: the slot TYPE is plain
#: SignalSeries, so no registry metadata isolates "a volatility" today, and
#: SLOT_REF_NOT_FOUND's fix names the producer every reader documents
#: (`ReturnVolatility() -> Store(...)`) instead of a type-blind Store. Durable
#: form: a refined slot type on these parameters, at which point this is
#: deleted.
_VOL_SLOT_PARAMS = frozenset({"vol_slot", "return_vol_slot", "volatility_slot"})

_NORMALIZER_CONCAT_KEY = "normalizer-before-concat"
_FORECAST_MAGNITUDE_KEY = "forecast-magnitude-discarded"

#: The boolean domain a mask combiner declares as its output: everything it
#: receives is coerced to True/False before combining.
_BOOLEAN_DOMAIN = (0.0, 1.0)

#: The discrete-threshold component whose DECLARED domain ([-1, 0, 1]) is
#: wider than what a given ``mode`` can emit. Mirrors validator.py's pass-7c
#: ``_THRESHOLD_ARM_COMPONENT`` / ``_MODE_EVALUATED_ARMS``: only the modes
#: that evaluate the LOWER arm can put a -1 on the wire.
_MODE_GATED_DIRECTIONAL = "ThresholdCross"
_MODES_EMITTING_SHORT = frozenset({"symmetric", "short_only"})


def _domain_set(sig) -> tuple[float, ...] | None:
    """The declared ``output_domain`` as a sorted float tuple, or None."""
    dom = getattr(sig, "output_domain", None)
    if isinstance(dom, dict) and "set" in dom:
        try:
            return tuple(sorted(float(v) for v in dom["set"]))
        except (TypeError, ValueError):  # pragma: no cover - malformed table
            return None
    return None


def _terminal_weight_key() -> str:
    """The transition key of the weight carrier, DERIVED from the A6 table.

    Tokens-only (spec 02 §3.6): the weight carrier is the unique output of
    the RISK_MANAGER category — the same derivation
    ``validator._terminal_weight_type()`` makes, repeated here over the
    generated table the interpreter already holds so the bridge's reorder arm
    spells no type-universe name.
    """
    outs = {out for row in _TRANSITIONS.values() for out in row.get("risk_manager", ())}
    if len(outs) != 1:
        raise ValueError(
            f"TYPE_TRANSITIONS no longer yields a unique risk_manager output "
            f"type ({sorted(outs)}) — the bridge's reorder arm must be revisited."
        )
    return next(iter(outs))


def _demand_tok_name(base: str):
    """A demand token for a transition-key NAME (the two-hop middle type)."""
    return R.Pair(_base_of(base), R.TOP)


def _uniform_magnitude(tok) -> bool:
    """Does the flowing domain PROVE every non-zero value shares one magnitude?

    A ForecastSeries DECLARES conviction, so the sizer-discards-magnitude
    verdict is the default reading; this is the proof that discharges it. A
    statically-known finite domain whose non-zero members share one absolute
    value carries selection (and sign) but no conviction — ``Set{10.0}`` from
    a ConstantForecast, ``Set{0.0, 10.0}`` after a regime gate, ``Set{-1, 0,
    1}`` after a threshold — and equal-weighting it discards nothing.
    Measured 2026-09-22: this is exactly what separates the two shipped
    library entries that pair ConstantForecast(10.0) with EqualWeightSizer
    (a gated basket, correct) from the M-01 seed (a scaled ROC at domain
    ``top``, conviction thrown away). ``top`` and intervals are NOT a proof,
    so they fire.
    """
    dom = tok.domain if isinstance(tok, R.Pair) else None
    if not isinstance(dom, dict) or "set" not in dom:
        return False
    try:
        mags = {abs(float(v)) for v in dom["set"] if float(v) != 0.0}
    except (TypeError, ValueError):  # pragma: no cover - malformed table
        return False
    return len(mags) <= 1


def _effective_param(ref: ComponentRef, sig, name: str):
    """The value ``__init__`` would see: the written param, else the default.

    A ``VariableRef`` (or any non-literal) is returned as-is; every caller
    treats a non-literal as "no verdict", so an unresolvable parameter can
    only ever SUPPRESS a fire.
    """
    if name in ref.params:
        return ref.params[name]
    pinfo = sig.parameters.get(name) if sig is not None else None
    return getattr(pinfo, "default", None) if pinfo is not None else None


def _is_filter_mask_domain(dom) -> bool:
    """A statically-known 0/1 FILTER mask: a Set of {0, 1} that contains 0."""
    if not isinstance(dom, dict) or "set" not in dom:
        return False
    try:
        vals = {float(v) for v in dom["set"]}
    except (TypeError, ValueError):  # pragma: no cover - malformed table
        return False
    return 0.0 in vals and vals <= {0.0, 1.0}


def _is_number(value) -> bool:
    """A literal int/float (never a bool, never a VariableRef)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _render_leverage(value) -> str:
    """Render a numeric parameter for the message, float-style on both engines."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return repr(float(value))


def _branch_store_map(branches: dict[str, list]) -> dict[str, str]:
    """``{slot name -> branch that stores it}`` for one Parallel (W4 §4.1).

    A STATIC pre-scan of the branch step lists, not a Σ read: the whole point
    is to see stores the reader's Σ snapshot cannot (spec 01 §6.3), including
    branches that have not been typed yet. Nested Parallels are NOT descended
    — a slot written two levels down is invisible to a sibling for the same
    reason and the nested block's own frame answers for it. Declaration order
    breaks ties, matching Σ's last-write-wins merge only in the sense that
    either branch name is a true answer to "where is it stored".
    """
    out: dict[str, str] = {}
    for bname, bsteps in branches.items():
        for st in bsteps:
            if isinstance(st, (SlotStoreSpec, SlotStoreValueSpec)):
                out.setdefault(st.slot_name, bname)
    return out


@dataclass(frozen=True)
class FlowVal:
    """A flowing type: relation token + boundary object + live-parity name."""

    tok: object  # R.Pair | R.Record | R.ANY
    obj: object  # originating declared type object (§2.5 boundary projection)
    name: str  # type_name() rendering — trace/TypeFlowEntry parity
    fields: dict[str, "FlowVal"] | None = None  # records: declaration order
    src: str | None = None  # producing step name — armed value-domain blame
    # ── The clock facet (dsl-mtf-clocks spec 01 §4.1, M4b) ────────────────
    # Present exactly on Pair tokens whose base is clocked(B); records/Any/
    # None/unions carry None (their FIELDS may carry clocks). Inference is
    # configuration-independent — dormancy stages the EMISSION only.
    clock: tuple[int, int] | None = None  # (period_minutes, phase_minutes)
    clock_origin: str | None = None  # the step that last SET this clock
    # ── Envelope companions (spec 02 §4.1/§4.3, M4d) ──────────────────────
    # clock_origin_path: the SETTING step's node id in the one addressing
    # scheme (ClockRef.origin); clock_hops: the ProvenanceHop chain of the
    # clock-relevant steps (origin → transfer/projection); producer: the
    # (branch node id, last step index) of the Parallel branch that produced
    # this record field — the §4.3 insert-at-end-of-producer target.
    clock_origin_path: tuple[str, ...] = ()
    clock_hops: tuple = ()
    producer: tuple[str, int] | None = None
    # The producing ``ComponentRef`` itself (Q-1694). ``src`` carries the
    # producer's NAME, which is enough to blame a step but not to read its
    # PARAMS — and two of the composition advisories are decided by a
    # parameter of the producer (``target_leverage``'s value;
    # ThresholdCross's ``mode``, which narrows a declared {-1,0,1} domain to
    # {0,1}). A name lookup cannot serve them: the M-23 shape is two
    # same-named producers in sibling branches with DIFFERENT modes. Carried
    # through Σ by J-LOAD (which restores the stored FlowVal whole), so a
    # branch that only ``Load``s still knows what produced the value.
    src_ref: object | None = None
    # ── The binding facet (position-layer spec 03 §2.C) ───────────────────
    # A ``binding.Facet`` for a Position, a trade/flat series or a realised
    # exposure; ``None`` is ``market`` (every value that predates the position
    # layer — by omission, so no existing FlowVal changes). Records carry
    # their facets on their FIELDS. Σ entries keep the FlowVal whole, so a
    # Store/Load carries the facet with it (03-R16).
    facet: object | None = None


def _facet_of(fv: "FlowVal"):
    """The scope machine's view of a FlowVal: a Facet, or a record of them."""
    if isinstance(fv.tok, R.Record) and fv.fields is not None:
        return {k: _facet_of(f) for k, f in fv.fields.items()}
    return fv.facet if fv.facet is not None else MARKET


def _with_facet(fv: "FlowVal", facet) -> "FlowVal":
    """Attach a machine verdict's facet to a FlowVal (records keep theirs)."""
    if isinstance(facet, dict) or isinstance(fv.tok, R.Record):
        return fv
    return _dc_replace(fv, facet=None if facet.kind == "market" else facet)


def _binding_json(fv: "FlowVal") -> dict | None:
    facet = fv.facet
    return facet.to_json() if isinstance(facet, Facet) else None


def _set_domain(fv: "FlowVal") -> frozenset | None:
    """A FlowVal's static value set when its domain is a finite set, else None."""
    tok = fv.tok
    if isinstance(tok, R.Pair) and isinstance(tok.domain, dict) and "set" in tok.domain:
        try:
            return frozenset(float(v) for v in tok.domain["set"])
        except (TypeError, ValueError):  # pragma: no cover - malformed table
            return None
    return None


@dataclass
class _SlotEntry:
    """One Σ entry: the stored FlowVal + store-site provenance (spec 01 §6.1)."""

    val: FlowVal
    location: object
    # The Store step's node id (one addressing scheme) — the §4.3 write-side
    # clock repair inserts the projector BEFORE this step (spec 02 §4.3).
    store_path: str | None = None


@dataclass(frozen=True)
class StagedOutcome:
    """A relation/flip outcome whose code is staged-dormant — never emit()ed."""

    code: str
    staged_by: str
    outcome: str  # "error" | "advisory"
    mode: str
    step: str
    location: str | None
    slot: str | None = None
    provenance: str | None = None  # store-site location for slot reads


_ANY_VAL = FlowVal(R.ANY, TypingAny, "Any")
_NONE_VAL = FlowVal(R.Pair("NoneType"), type(None), "None")


def _base_of(name: str) -> str:
    return "NoneType" if name == "None" else name


def _is_union(t) -> bool:
    return get_origin(t) is Union or isinstance(t, types.UnionType)


def _demand_tok(t, dom=None):
    """Demand-side token for a declared input/role type (naming, not relation)."""
    if t is TypingAny:
        return R.ANY
    if isinstance(t, tuple):  # tuple-spelled union demand (e.g. slot_reads role)
        return R.Union(tuple(_demand_tok(a) for a in t))
    if _is_union(t):
        return R.Union(tuple(_demand_tok(a) for a in get_args(t)))
    d = R.TOP if dom in (None, "top") else dom
    return R.Pair(_base_of(type_name(t)), d)


def _decl_tok(t):
    """Actual-side token for a declared output type (decl form, spec 01 §1.5)."""
    if t is TypingAny:
        return R.ANY
    if _is_union(t):
        return R.Union(tuple(_decl_tok(a) for a in get_args(t)))
    return R.decl_type(_base_of(type_name(t)))


def _finite_num(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def _literal_val(value) -> FlowVal:
    """J-STOREVALUE literal typing: base = type(v), domain = Set{float(v)} (§6.2)."""
    obj = _store_value_slot_type(value)
    dom = {"set": [float(value)]} if _finite_num(value) else R.TOP
    name = type_name(obj)
    return FlowVal(R.Pair(_base_of(name), dom), obj, name, src=f"StoreValue({value!r})")


def _from_live(t) -> FlowVal:
    """Seed a FlowVal from a live type object (structural-mode Σ seeding only)."""
    if t is TypingAny:
        return _ANY_VAL
    name = type_name(t)
    return FlowVal(R.Pair(_base_of(name)), t, name)


def _fmt_expected(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(type_name(x) for x in expected)
    return type_name(expected)


def _stage_armed(key: str) -> bool:
    """True iff staged change ``key`` is in an EMITTING state (spec 05 §2.2)."""
    change = STAGED_CHANGES[key]
    if change.kind == "flow-shape":
        return change.stage != "pre"
    return Stage(change.stage) is not Stage.DORMANT


def _render_domain(d) -> str:
    """Render a JSON domain-lattice element for issue text (spec 01 §1.4)."""
    if isinstance(d, dict) and "set" in d:
        return "Set{" + ", ".join(f"{float(v):g}" for v in d["set"]) + "}"
    if isinstance(d, dict) and "interval" in d:
        return f"Interval[{float(d['interval'][0]):g}, {float(d['interval'][1]):g}]"
    return "⊥" if d == R.BOTTOM else "⊤"


def _demand_domain(tok):
    """The demand-side domain a relation query checked against (spec 01 §1.5)."""
    if isinstance(tok, R.Union):
        doms = [d for v in tok.variants if (d := _demand_domain(v)) != R.TOP]
        return doms[0] if doms else R.TOP
    if isinstance(tok, R.Pair):
        return R.exp_demand(tok.base).domain if R.is_refined(tok.base) else tok.domain
    return R.TOP


def _display(tok) -> str:
    """Demand-token display name — matches the sites' type_name renderings."""
    if isinstance(tok, R.Union):
        return " or ".join(_display(v) for v in tok.variants)
    return tok.base if isinstance(tok, R.Pair) else "Any"


# ── Typed-issue envelope builders (dsl-type-system spec 05 §3.2/§3.3) ────────
# The interpreter has the Σ typed state by construction (spec 01 §6.1), so it is
# the population site for the envelope's expected/actual/path (type outcomes) and
# provenance (domain/slot outcomes). Every ``declared`` string is byte-identical
# to the prose these sites rendered before, so ``{expected}``/``{actual}`` in the
# catalog message templates are unchanged.

#: Reverse of spec 02 ``domain_aliases`` (a value set → its Binary/Flag/Mask name).
_DOMAIN_ALIAS_BY_SET: dict[tuple[float, ...], str] = {
    tuple(sorted(float(v) for v in spec["set"])): name
    for name, spec in R._A2.get("domain_aliases", {}).items()
    if "set" in spec
}


def _domain_ref(d) -> DomainRef:
    """Relation JSON domain element → wire DomainRef (spec 05 §3.2)."""
    if isinstance(d, dict) and "set" in d:
        vals = tuple(float(v) for v in d["set"])
        return DomainRef("set", values=vals, alias=_DOMAIN_ALIAS_BY_SET.get(tuple(sorted(vals))))
    if isinstance(d, dict) and "interval" in d:
        lo, hi = d["interval"]
        return DomainRef("interval", low=float(lo), high=float(hi))
    return DomainRef.top()


def _path_of(spath: str) -> tuple[str, ...]:
    """Envelope machine path (spec 05 §3.1 #7): the trace node id split into
    segments. ``"/".join(_path_of(spath)) == spath`` — one addressing scheme,
    two renderings (the wire array here; spec 04's trace node id there)."""
    return tuple(spath.split("/"))


def _base_ref(name: str, dom=R.TOP) -> TypeRef:
    """A bare-base TypeRef (declared == base). ``declared`` is the caller's string."""
    return TypeRef(name, name, _domain_ref(dom), R.tier_of(dom))


def _expected_ref(t, dom=None) -> TypeRef | None:
    """Demanded type → wire TypeRef. ``declared`` is EXACTLY ``type_name(t)`` (the
    prose these sites rendered), ``base`` the exp-unfolded parent (spec 05 §3.2)."""
    if t is TypingAny:
        return None
    declared = type_name(t)
    if isinstance(t, tuple) or _is_union(t):
        return TypeRef(declared, declared, DomainRef.top(), "none")
    exp = R.exp_demand(_base_of(declared))
    d = exp.domain if dom in (None, "top") else dom
    return TypeRef(declared, exp.base, _domain_ref(d), R.tier_of(d))


def _actual_ref(fv: "FlowVal") -> TypeRef | None:
    """Synthesized FlowVal → wire TypeRef. ``declared == fv.name`` (spec 05 §3.2)."""
    tok = fv.tok
    if isinstance(tok, R.Pair):
        return TypeRef(
            fv.name,
            tok.base,
            _domain_ref(tok.domain),
            R.tier_of(tok.domain),
            binding=_binding_json(fv),
        )
    if isinstance(tok, R.Record):
        return TypeRef(fv.name, "dict", DomainRef.top(), "none")
    return TypeRef(fv.name, fv.name, DomainRef.top(), "none")


#: The §4.1 placeholder hop rendered when a CLOCK_MISMATCH's repair IS the
#: missing projector (spec 01 §9.4's exemplar shape: the chain ends in
#: ``projection — MISSING —``).
_MISSING_PROJECTION = "— MISSING —"

#: Transfer op → the ``ProvenanceHop.kind`` its clock-setting emits (§4.1:
#: "a real projector step emits a projection hop").
_HOP_KIND_BY_OP = {"synth": "origin", "coarsen": "transfer", "project": "projection"}


def _clocked_ref(fv: "FlowVal", clock: tuple[int, int]) -> TypeRef | None:
    """``_actual_ref`` with the fourth member populated (spec 01 §10): the
    value's wire TypeRef carrying the ClockRef whose origin is the node id of
    the step that last SET this clock."""
    ref = _actual_ref(fv)
    if ref is None:
        return None
    return _dc_replace(ref, clock=C.clock_ref(clock, fv.clock_origin_path))


def _tok_ref(tok) -> TypeRef | None:
    """Demand token → wire TypeRef (the value-domain composition site, §3.3).

    The wire ``domain``/``tier`` report the demand-side domain the relation
    query CHECKED against — ``_demand_domain`` (exp-unfolds a refined base per
    spec 01 §1.5, byte-identical to the message's ``_demand_domain`` render and
    the schema-1 ``check.expected`` projection), NOT the token's raw
    ``.domain``. Direct-input callers thread the effective ``input_domain`` into
    the token, so ``tok.domain`` already equalled it there; the composer
    field-demand path builds the token without a domain, so reading
    ``tok.domain`` under-populated the envelope (domain=⊤/tier=none) even while
    the rendered message showed the true demand interval. Both paths now agree.
    """
    if isinstance(tok, R.Pair):
        d = _demand_domain(tok)
        return TypeRef(_display(tok), tok.base, _domain_ref(d), R.tier_of(d))
    if isinstance(tok, R.Union):
        return TypeRef(_display(tok), _display(tok), DomainRef.top(), "none")
    return None


class Walk:
    """One interpreter run over an EXPANDED strategy (pass-6 + pass-8 semantics).

    ``run()`` is the pass-6 position (typing issues emitted inline; slot
    outcomes journaled); ``flush()`` is the pass-8 position (journal + the
    SLOT_UNUSED lint + the schema-0 terminal slot states). ``structural=True``
    is the §5 standalone verify capacity: Σ-lifecycle judgments only, no
    typing rows, stores typed from ``store_seed`` (NoneType sentinel absent).
    """

    def __init__(
        self,
        registry,
        profile: Profile | None = None,
        trace_sink=None,
        structural: bool = False,
        store_seed: dict | None = None,
        bridge_registry: dict | None = None,
    ) -> None:
        self.registry = registry
        # The registry the TYPE_MISMATCH bridge SEARCHES (Q-1696). It is a
        # different question from the one every CHECK asks. `self.registry`
        # is the lock-scoped effective view — exactly right for
        # version-accurate checking, and fatal for a search whose entire
        # purpose is to find a component the strategy does NOT contain: for
        # the `ROC -> LeverageCap` seed the lock holds four entries, so the
        # search could only ever name a component already in the pipeline
        # and every real strategy got the fallback restatement instead.
        # Defaults to the checking view so an un-passed caller behaves as
        # before rather than silently searching a wider registry.
        self.bridge_registry = bridge_registry if bridge_registry is not None else registry
        self.profile = profile or shipped_profile()
        self.staged = dict(self.profile.staged)
        self.sink = trace_sink
        # Schema-1 typed hooks (spec 04 §2.3): present only when the sink
        # carries them (Schema1Sink); None (production) does zero work.
        self.ev = trace_sink if hasattr(trace_sink, "on_event") else None
        self.structural = structural
        self.store_seed = store_seed or {}
        self.sigma: dict[str, _SlotEntry] = {}
        self.journal: list[tuple[str, str, dict]] = []
        self.used: set[str] = set()
        self.pairs: dict[str, object] = {}  # trace path → synthesized token
        # D-1 (role-survival): resolved ONCE per walk. Both halves of the
        # amendment read it — `norm` (soft names survive) and the `_gate`
        # role judgment — so they can never disagree about the active stage.
        self._soft_roles_survive = self.profile.flow["role-survival"] == "post"
        self.staged_outcomes: list[StagedOutcome] = []
        self.issues: list = []
        self.type_flow: list[TypeFlowEntry] = []
        self.variable_pipelines: dict[str, PipelineSpec] = {}
        # Active VariableRef inline stack — the defensive cycle backstop for
        # UNGATED callers (see _pipeline's VariableRef arm). The gated entry
        # points (validate_strategy's pass-3b structural short-circuit,
        # verify_spec) refuse cyclic specs with PIPELINE_VARIABLE_CYCLE
        # before this walk ever runs.
        self._var_stack: set[str] = set()
        self.final: FlowVal = _NONE_VAL
        # Pass-8 clock journal (spec 02 §4.3 slot shape): every slot clock
        # read is journaled during the walk; the write-side-vs-read-side
        # repair decision needs ALL readers of a slot, so emission happens
        # at the pass-8 flush position.
        self._clock_slot_journal: list[dict] = []
        # Branch-isolation context for the J-SLOTREAD.ref-present fix (W4
        # §4.1, Q-1696). A stack, one frame per ENCLOSING Parallel, pushed in
        # `_parallel`: `(slot -> branch that stores it, branch being typed)`.
        # The store map is a cheap pre-scan of the branch step lists, so it
        # holds EVERY branch's writes including branches not yet typed — which
        # is the point: the Σ snapshot (§6.3) is exactly what hides them, and
        # the reader needs to be told the writer exists somewhere it cannot
        # see. Empty stack ⇒ top level ⇒ the `none` shape.
        self._branch_frames: list[tuple[dict[str, str], str]] = []
        # The DOWNSTREAM context for NORMALIZER_BEFORE_CONCAT's look-ahead
        # (agent-surface-cleanup spec 04 §2.6): one ``[steps, index]`` frame
        # per pipeline body being walked (the enclosing bodies of a nested
        # Pipeline / variable inline included), and a ``None`` BOUNDARY frame
        # per Parallel branch being typed — a step after the Parallel receives
        # the record, not this branch's book, so nothing there caps it.
        self._tail_frames: list[list | None] = []
        self._root_len: int = 0  # outermost pipeline step count (R-17 insert)
        # Did ANY clock transfer take Globals.bar_offset (new-data-loaders
        # spec 05 §1b)? A coarsen that resolved it through its globals ref,
        # or a synth loader that applied it in-loader / refused it at its
        # site. UNUSED_GLOBAL's bar_offset arm reads this at the pass-9
        # position — a component's mere REFERENCE no longer counts as a use.
        self.offset_consumed: bool = False
        # The position layer's scope machine (spec 03 §2.C): one per run, the
        # same verdict functions the runtime lift calls. None in structural
        # mode (Σ-lifecycle judgments only).
        self.binding: ScopeMachine | None = None
        self._binding_decls: dict[tuple[str, int], object] = {}
        # Steps declaring a ``duration`` parameter, with the clock the walk
        # resolved at their input — read by the validator's pass 7e
        # (CALENDAR_HOLD_NOOP, Q-2436). A record, never an emission here.
        self.duration_steps: list[tuple[ComponentRef, object, object, str]] = []

    # ── entry points ─────────────────────────────────────────────────────────

    def run(
        self, strategy: StrategyFile, issues: list, type_flow: list, slot_types: dict
    ) -> FlowVal:
        """Pass-6 position: the single walk over the expanded strategy."""
        self.issues, self.type_flow = issues, type_flow
        self.variable_pipelines = {
            v.name: v.value for v in strategy.variables if isinstance(v.value, PipelineSpec)
        }
        # ── Clock context (dsl-mtf-clocks spec 01 §6.4) ───────────────────
        # κ_exec is a global constant of the check (None when undefined or
        # non-WF — J-PIPE then skips). The spec 02 §3.4 dual-emitter
        # suppression predicate that used to run beside it is gone with the
        # pass-9 emitter (T-M4f-5): these rows are now the only emitter.
        self._globals = getattr(strategy, "globals_", None)
        self._kexec = None if self.structural else C.kappa_exec(self._globals)
        self._root_len = len(strategy.pipeline.steps)
        if not self.structural:
            tok = getattr(self._globals, "target_timeframe", None) if self._globals else None
            self.binding = ScopeMachine(
                kappa_minutes=self._kexec[0] if self._kexec else None,
                kappa_token=tok if isinstance(tok, str) else None,
            )
        self.final = self._pipeline(strategy.pipeline, _NONE_VAL, "pipeline", "root")
        if self.binding is not None:  # 01-R10 / 03-R27: the pipeline's end
            self._binding_emit(self.binding.finish(_facet_of(self.final)), None)
        if not self.structural:
            self._clock_globals_keeper()
            self._clock_terminal()
        slot_types.update({name: e.val.obj for name, e in self.sigma.items()})
        if self.ev is not None:  # schema-1 terminal (full Σ pairs — S1-AC3)
            self.ev.on_terminal(self.final, {k: e.val for k, e in self.sigma.items()})
        return self.final

    # ── The clock facet (dsl-mtf-clocks spec 01 §§4-7, M4b — T-M4b-1) ───────

    def _route_clock_fire(
        self, fire: "C.ClockFire | None", location: str | None, issues: list | None = None
    ) -> None:
        """Emit-vs-divert for one clock-row outcome (spec 05 §2.2; R-3).

        The staged gate: DORMANT keys divert to ``staged_outcomes``
        (structural silence — the measurement channel); armed keys surface
        through ``emit()`` with stage-resolved severity and the fire's
        typed-envelope payload (spec 02 §4.1/§4.3). ``issues`` overrides the
        sink for pass-8-position emissions.

        The §3.4 suppression step that used to precede the gate is gone with
        the pass-9 emitter (T-M4f-5) — no fire is ever another emitter's now.
        """
        if fire is None:
            return
        key = fire.row_staged_by or RULES[fire.code].staged_by
        if key and not _stage_armed(key):
            outcome = "advisory" if fire.code == "RESAMPLER_NOOP" else "error"
            self.staged_outcomes.append(
                StagedOutcome(fire.code, key, outcome, "clock", fire.row_id, location)
            )
            return
        env = fire.envelope
        # The K16 refusal arms replace the rule's rendered suggestion_template
        # (emit's sanctioned per-site override). Their fires drop the
        # suggestion-only template params, so the exact-cover check still holds.
        override = (
            {} if env.suggestion_override is None else {"suggestion": env.suggestion_override}
        )
        emit(
            self.issues if issues is None else issues,
            fire.code,
            location=location,
            row_staged_by=fire.row_staged_by,
            **override,
            expected=env.expected,
            actual=env.actual,
            path=env.path,
            provenance=env.provenance,
            valid_options=env.valid_options,
            suggested_edit=env.suggested_edit,
            applicability_override=env.applicability_override,
            **fire.kwargs,
        )

    def _clock_globals_keeper(self) -> None:
        """The R-16 Globals-level κ_exec WF keeper (spec 02 §3.4): the
        bar_offset-vs-declared-target_timeframe arithmetic at the Globals
        declaration itself. A pass-9-successor row that is NOT removed at
        the clock-transform-rebase flip — it closes the flip-ordering window
        for the loader-free class pass 9 covers only via the loader."""
        g = self._globals
        if g is None or g.target_timeframe is None or g.bar_offset is None:
            return
        period = C.TIMEFRAME_MINUTES.get(g.target_timeframe)
        if period is None:
            return
        try:
            off_min = C.parse_bar_offset_minutes(g.bar_offset)
        except ValueError:
            return  # the grammar codes own it (INVALID_GLOBAL / INVALID_BAR_OFFSET)
        if off_min < period:
            return
        fire = C.ClockFire(
            "J-GLOBALS.exec-wf",
            "BAR_OFFSET_TOO_LARGE",
            {"bar_offset": g.bar_offset, "target_tf": g.target_timeframe},
            row_staged_by="clock-transform-rebase",
        )
        self._route_clock_fire(fire, "globals")

    def _clock_terminal(self) -> None:
        """J-PIPE's terminal clock premise (spec 01 §6.4): the outermost
        pipeline's clock must equal κ_exec. Totality (R-8): a clock-less
        terminal is ``cfree`` ⇒ skip; an undefined/non-WF κ_exec ⇒ skip (the
        existing Globals rules own that error).

        Machine fix (R-17, spec 02 §4.3): insert AFTER the last step of the
        OUTERMOST pipeline — always type-correct under the unbounded scheme
        and always a top-level path, never mid-branch (a terminal clock last
        set inside a Parallel branch cannot draw the projector into a
        branch). The inserted projector IS the new terminal step, carrying
        κ_exec ⇒ the fix component is always TargetSignalProjector(). Both
        legal repairs ride valid_options (insert-projector primary;
        re-declare Globals, named as schedule-changing).

        K16 post-condition: that edit is only claimed when the projector can
        actually reach κ_exec — κ_term ⊑_clk κ_exec. A terminal driven FINER
        than the declaration (the shape the meet-first repair used to produce)
        would answer the appended projector with PROJECT_WRONG_DIRECTION, so
        the emission refuses the edit and gives the aggregation guidance
        instead (``clocks.terminal_resample_up_*``).
        """
        kexec = self._kexec
        kterm = self.final.clock
        if kexec is None or kterm is None or kterm == kexec:
            return
        g = self._globals
        origin = self.final.clock_origin or self.final.src or "the pipeline"
        actual_ref = _actual_ref(self.final)
        if actual_ref is not None:
            actual_ref = _dc_replace(
                actual_ref, clock=C.clock_ref(kterm, self.final.clock_origin_path)
            )
        expected_ref = _actual_ref(self.final)
        if expected_ref is not None:
            expected_ref = _dc_replace(expected_ref, clock=C.clock_ref(kexec, ("globals",)))
        kwargs = {
            "actual_clock": C.render_clock(kterm),
            "declared_tf": g.target_timeframe if g is not None else "",
            "expected_clock": C.render_clock(kexec),
            "consequence": C.terminal_consequence(kterm, kexec),
        }
        if C.is_subclock(kterm, kexec):
            env = C.FireEnvelope(
                expected=expected_ref,
                actual=actual_ref,
                path=("root",),
                provenance=tuple(self.final.clock_hops),
                valid_options=C.terminal_valid_options(kterm, kexec),
                suggested_edit=SuggestedEdit(
                    "insert_step",
                    ("root",),
                    {
                        "after": str(self._root_len - 1),
                        "component": "TargetSignalProjector",
                        "params": {},
                    },
                ),
            )
            kwargs |= {  # suggestion_template-only params
                "fix_component": "TargetSignalProjector()",
                "origin_step": origin,
                "actual_tf": C.token_for(kterm[0]) or f"{kterm[0]}min",
            }
        else:
            env = C.FireEnvelope(
                expected=expected_ref,
                actual=actual_ref,
                path=("root",),
                provenance=tuple(self.final.clock_hops),
                valid_options=C.terminal_resample_up_options(kterm, kexec),
                suggested_edit=None,
                applicability_override="has_placeholders",
                suggestion_override=C.terminal_resample_up_suggestion(kterm, kexec),
            )
        fire = C.ClockFire(
            "J-PIPE.terminal-clock",
            "TERMINAL_CLOCK_MISMATCH",
            kwargs,
            row_staged_by=None,
            envelope=env,
        )
        self._route_clock_fire(fire, "pipeline")

    def _clock_uniform(
        self, fields: dict[str, FlowVal], sig, step: ComponentRef, loc: str, spath: str
    ):
        """J-CLKUNIFORM (spec 01 §6.3): equality over the field keys the
        consumer's composer_inputs actually bind. Returns the clock context
        ``(clock, origin, origin_path, hops)`` the consumer's output carries:
        the common clock on agreement, the R-2 recovery (κ_exec when
        defined+WF, clock-less otherwise) on mismatch. Clock-less fields are
        ``cfree`` and skip (§6.1).

        The mismatch arm builds the full §4.3/§4.4 envelope: the repair-target
        selection (all four R-17 arms, ``clocks.select_uniform_repair``), both
        `ClockRef`-carrying TypeRefs, the coarse field's provenance chain
        ending in the ``projection — MISSING —`` placeholder hop (§4.1), and
        the ``insert_step`` edit at the END of the coarse field's producing
        branch. The ELSE arm attaches no edit and downgrades the emission to
        ``has_placeholders`` with the aggregation answer (K16's finer-than-
        κ_exec shape), or lattice/phase-inheritance guidance otherwise.
        """
        ci = getattr(sig, "composer_inputs", None)
        role_form = isinstance(ci, dict) and bool(ci)
        bound: list[tuple[str, FlowVal]] = []
        if role_form:
            for role in ci:
                if role in step.params:
                    bname = step.params.get(role)
                else:
                    # Q-1509: the role is OMITTED, so run() binds the
                    # component's OWN declared default (the Q-1283 rule
                    # _composer_checks already applies) — Crossover() over
                    # {"fast", "slow"} binds both fields. Reading params
                    # only left the idiomatic spelling clock-less, so the
                    # terminal premise could never fire through it. A
                    # ``None``/MISSING default (auto-detect / required
                    # role) binds nothing, exactly as before.
                    pinfo = sig.parameters.get(role)
                    bname = getattr(pinfo, "default", None)
                if isinstance(bname, str) and bname in fields:
                    bound.append((bname, fields[bname]))
        elif ci is not None:  # homogeneous form: every field is bound
            bound = list(fields.items())
        else:
            return None, None, (), ()  # no composer_inputs ⇒ K empty ⇒ no demand
        ground = [(k, f.clock) for k, f in bound if f.clock is not None]
        if not ground:
            return None, None, (), ()
        by_field = dict(bound)
        composer_path = _path_of(spath)
        distinct = {c for _, c in ground}
        if len(distinct) == 1:
            # Uniform: the output carries the common clock; a
            # J-CLKUNIFORM-set clock's origin is the composer step (R-17).
            first = by_field[ground[0][0]]
            return next(iter(distinct)), step.name, composer_path, tuple(first.clock_hops)
        # cmix — CLOCK_MISMATCH at the consumer (spec 01 §9.3/§9.4 shape).
        kexec = self._kexec
        rep = C.select_uniform_repair(ground, kexec)
        actual_fv = by_field[rep.coarse_field]
        expected_fv = by_field[rep.fine_field]
        env = self._mismatch_envelope(
            rep, actual_fv, expected_fv, kexec, composer_path, actual_fv.producer
        )
        kwargs = {
            "consumer": step.name,
            "actual_field": rep.coarse_field,
            "actual_clock": C.render_clock(rep.coarse_clock),
            "expected_field": rep.fine_field,
            "expected_clock": C.render_clock(rep.fine_clock),
            "declared_clock": C.render_clock(kexec) if kexec else "(undeclared)",
            "via": "",
            "symptom": C.symptom_for(
                rep.coarse_clock,
                rep.fine_clock,
                role_form=role_form,
                declared_render=C.render_clock(kexec) if kexec else "(undeclared)",
            ),
        }
        # {fix_component} is a suggestion_template-only param; an overridden
        # suggestion must not pass it (emit requires an exact param cover).
        if env.suggestion_override is None:
            kwargs["fix_component"] = rep.fix_component
        fire = C.ClockFire(
            "J-CLKUNIFORM.consumer",
            "CLOCK_MISMATCH",
            kwargs,
            row_staged_by=None,
            envelope=env,
        )
        self._route_clock_fire(fire, loc)
        # R-2 recovery: synthesize κ_exec (single-fire in every branch
        # order — the all-keep tail reaches J-PIPE already on κ_exec).
        if kexec is None:
            return None, None, (), ()
        return kexec, step.name, composer_path, ()

    def _mismatch_envelope(
        self,
        rep: "C.UniformRepair",
        actual_fv: FlowVal,
        expected_fv: FlowVal,
        kexec,
        site_path: tuple[str, ...],
        insert_at: tuple[str, int] | None,
        *,
        extra_provenance: tuple = (),
    ) -> "C.FireEnvelope":
        """The shared CLOCK_MISMATCH envelope builder (spec 02 §4.3).

        ``insert_at`` is ``(container node id, index of the step to insert
        after)`` — the producing branch's last step at a composer site, the
        step before the ``Store`` at a write-side slot repair — or ``None``
        when no single insertion legalizes the site, which is exactly the
        ``has_placeholders`` downgrade condition.
        """
        expected_ref = _clocked_ref(expected_fv, rep.fine_clock)
        actual_ref = _clocked_ref(actual_fv, rep.coarse_clock)
        provenance = (
            tuple(actual_fv.clock_hops)
            + tuple(extra_provenance)
            + (ProvenanceHop("projection", _MISSING_PROJECTION, site_path),)
        )
        if rep.repair_clock is None or insert_at is None:
            # ELSE arm (§4.3): no single projector insertion can legalize the
            # record — K15's aggregation answer when the offending branches are
            # finer than κ_exec, else the lattice pair or the phase-only
            # degenerate case's bar_offset inheritance ref (R02-MIN-04).
            options, suggestion = C.else_arm_guidance(rep, kexec)
            return C.FireEnvelope(
                expected=expected_ref,
                actual=actual_ref,
                path=site_path,
                provenance=provenance,
                valid_options=options,
                suggested_edit=None,
                applicability_override="has_placeholders",
                suggestion_override=suggestion,
            )
        fix_name, fix_params = C.fix_component_parts(rep.repair_clock, kexec)
        container, after = insert_at
        return C.FireEnvelope(
            expected=expected_ref,
            actual=actual_ref,
            path=site_path,
            provenance=provenance,
            valid_options=(
                C.mismatch_fix_option(
                    fix_name,
                    rep.coarse_clock,
                    rep.coarse_field,
                    rep.repair_clock,
                    declared=rep.repair_clock == kexec,
                ),
            ),
            suggested_edit=SuggestedEdit(
                "insert_step",
                _path_of(container),
                {"after": str(after), "component": fix_name, "params": fix_params},
            ),
            suggestion_override=C.machine_arm_suggestion(rep, kexec),
        )

    def _clock_stamp(
        self,
        fv: FlowVal,
        sig,
        step: ComponentRef,
        cur: FlowVal,
        loc: str,
        clock_in,
        origin_in,
        skip: bool,
        spath: str = "",
        path_in: tuple[str, ...] = (),
        hops_in: tuple = (),
    ) -> FlowVal:
        """Attach the output clock per the component's clock transfer.

        ``skip`` marks a failed NON-clock premise or a gated record
        discipline (§6.6 row 1 / §5.4 record row): κ_out = keep(κ_cur), no
        transform rows evaluated. Clock presence is gated on clocked(B_out)
        (spec 01 §4.1); union/Any/record outputs stay clock-less.

        The envelope companions ride the same synthesis (spec 02 §4.1): a
        transfer that SETS a clock appends its ``ProvenanceHop`` — ``origin``
        at a synth site, ``transfer`` at a coarsen site, ``projection`` at a
        real projector — and moves ``ClockRef.origin`` to this step's node id;
        a keep leaves both untouched.
        """
        is_pair = isinstance(fv.tok, R.Pair)
        if not self.structural and "duration" in getattr(sig, "parameters", {}):
            self.duration_steps.append((step, sig, clock_in, loc))
        # The registration surface is the transfer authority (spec 02 §8.1,
        # M4c re-source): the resolved signature carries its own
        # version-scoped clock_transfer; absent ⇒ keep-by-omission.
        spec = sig.clock_transfer if sig is not None else None
        path, hops = path_in, tuple(hops_in)
        if skip or spec is None:
            clock, origin = clock_in, origin_in
        else:
            res = C.evaluate_transfer(
                spec["op"],
                step_name=step.name,
                sig=sig,
                params=step.params,
                globals_=self._globals,
                clock_in=clock_in,
                origin_in=origin_in,
                input_is_first_step=cur.name == "None",
                input_base=(
                    "record"
                    if isinstance(cur.tok, R.Record)
                    else (cur.tok.base if isinstance(cur.tok, R.Pair) else None)
                ),
                step_path=spath,
            )
            self._route_clock_fire(res.fire, loc)
            if res.offset_consumed:
                self.offset_consumed = True
            clock, origin = res.clock, res.origin
            set_here = clock is not None and (clock, origin) != (clock_in, origin_in)
            if set_here:
                path = _path_of(spath)
                hops = hops + (
                    ProvenanceHop(
                        _HOP_KIND_BY_OP.get(spec["op"], "transfer"),
                        step.name,
                        path,
                        _dc_replace(_actual_ref(fv), clock=C.clock_ref(clock, path)),
                    ),
                )
        if clock is None or not is_pair or not R.is_clocked(fv.tok.base):
            return fv
        return _dc_replace(
            fv, clock=clock, clock_origin=origin, clock_origin_path=path, clock_hops=hops
        )

    def flush(self, issues: list) -> None:
        """Pass-8 position: journaled slot issues, terminal states, usage lint."""
        for code, loc, kwargs in self.journal:
            emit(issues, code, location=loc, **kwargs)
        self.flush_clock_slot_reads(issues)
        if self.sink is not None:
            for name, e in self.sigma.items():
                self.sink.on_slot_state(name, e.val.name)
        for name, e in self.sigma.items():
            if name not in self.used:
                emit(
                    issues,
                    _CODE["X.slot-unused-lint"],
                    location=_format_location(e.location),
                    slot=name,
                )

    def flush_clock_slot_reads(self, issues: list) -> None:
        """The J-SLOTREAD clock half at the pass-8 position (spec 02 §4.3).

        Selection is identical to the composer arm (``select_uniform_repair``
        over the stored and reading clocks); the message binding is fixed by
        R02-MIN-02 — ``{actual_field}`` is the SLOT name and
        ``{expected_field}`` the consuming input, whichever side is coarser.

        Insert side (R-17 (c)): when EVERY reader of the slot demands the same
        clock, one projector inserted BEFORE the ``Store`` heals them all —
        that is the machine edit. Otherwise Σ legitimately serves readers on
        different clocks and re-clocking the stored value would break the
        readers whose context matched it, so the emission carries no edit and
        downgrades to ``has_placeholders``.
        """
        if not self._clock_slot_journal:
            return
        demands: dict[str, set] = {}
        for rd in self._clock_slot_journal:
            demands.setdefault(rd["slot"], set()).add(rd["reader"].clock)
        kexec = self._kexec
        for rd in self._clock_slot_journal:
            stored, reader = rd["stored"], rd["reader"]
            if stored.clock == reader.clock:
                continue
            slot, pname = rd["slot"], rd["param"]
            rep = C.select_uniform_repair([(slot, stored.clock), (pname, reader.clock)], kexec)
            coarse_fv = stored if rep.coarse_field == slot else reader
            fine_fv = reader if coarse_fv is stored else stored
            # Write-side insertion is only the repair when the STORED value is
            # the coarse side (projection goes coarse → fine) AND every reader
            # of the slot demands one clock. A param-bound read has no
            # read-side insertion point in the flow (the value is consumed as
            # a parameter, not as the step's input), so the other arm carries
            # no edit at all.
            store_path = rd["store_path"]
            insert_at = None
            if rep.coarse_field == slot and len(demands[slot]) == 1 and store_path:
                container, _, last = store_path.rpartition("/")
                if container and last.isdigit():
                    insert_at = (container, int(last) - 1)
            env = self._mismatch_envelope(
                rep,
                coarse_fv,
                fine_fv,
                kexec,
                _path_of(rd["path"]),
                insert_at,
                extra_provenance=(rd["store_provenance"],),
            )
            kwargs = {
                "consumer": rd["step"],
                "actual_field": slot,  # the SLOT name (R02-MIN-02)
                "actual_clock": C.render_clock(stored.clock),
                "expected_field": pname,  # the consuming input
                "expected_clock": C.render_clock(reader.clock),
                "declared_clock": C.render_clock(kexec) if kexec else "(undeclared)",
                "via": f" via slot '{slot}'",
                "symptom": C.symptom_for(
                    stored.clock,
                    reader.clock,
                    role_form=False,
                    declared_render=C.render_clock(kexec) if kexec else "(undeclared)",
                ),
            }
            if env.suggestion_override is None:  # suggestion_template-only param
                kwargs["fix_component"] = rep.fix_component
            fire = C.ClockFire(
                "J-SLOTREAD.clock",
                "CLOCK_MISMATCH",
                kwargs,
                row_staged_by=None,
                envelope=env,
            )
            # No effect on flow synthesis (spec 01 §6.6 slot row).
            self._route_clock_fire(fire, rd["loc"], issues)

    # ── shared machinery ─────────────────────────────────────────────────────

    def _trace(self, path: str, kind: str, comp, ver, val: FlowVal) -> None:
        self.pairs[path] = val.tok
        if self.sink is not None:
            self.sink.on_step(self.sink.next_n, path, kind, comp, ver, val.name)
            if self.ev is not None:
                self.ev.on_event(path, kind, comp, ver, val)

    def _sink_check(self, sig, cur: FlowVal, loader: bool, dict_in: bool, cat: str, gated) -> None:
        """Schema-1 ``check`` capture (spec 04 §2.2) — trace evidence only.

        Re-derives the input-edge verdict from the pure relation (identical
        to the engine's under the active profile: at m2-compat no staged row
        fires, so ``subsumes`` alone decides); never touches engine state.
        """
        ev = self.ev
        if gated is not None:  # dict gates suppress the input check (§2.3)
            ev.on_check(None, f"issue:{gated}")
            return
        if loader:  # entry axiom — loaders never take an input demand
            ev.on_check(None, "skip:loader-entry")
            return
        demand = (
            R.ANY if sig.input_type is TypingAny else _demand_tok(sig.input_type, sig.input_domain)
        )
        if cur.obj is not TypingAny and sig.input_type is not TypingAny:
            if dict_in and cat in _DICT_ROW_CATS:  # W3 strict-check-skip set
                ev.on_check(demand, "accept")
                ev.on_mark("waiver:record-tolerant")
                return
            v = self._verdict(cur.tok, demand, "strict")
            if v.rejects:
                ev.on_check(demand, "issue:TYPE_MISMATCH")
                return
            matched = None
            if isinstance(demand, R.Union):  # S3 first-match index, canonical order
                variants = R._canonical_union_variants(demand.variants)
                for idx, variant in enumerate(variants):
                    if self._verdict(cur.tok, variant, "strict").outcome == "accept":
                        matched = idx
                        break
            ev.on_check(demand, "accept", matched)
            return
        ev.on_check(demand, "accept")  # S1 gradual frontier

    def _entry(
        self, step: str, in_name: str, out_name: str, category: str, binding: dict | None = None
    ) -> None:
        if not self.structural:
            self.type_flow.append(
                TypeFlowEntry(
                    step=step,
                    input_type=in_name,
                    output_type=out_name,
                    category=category,
                    binding=binding,
                )
            )

    # ── The binding facet (position-layer spec 03 §2.C, P03-10…P03-17) ───────

    def _binding_emit(self, findings, location) -> bool:
        """Emit the machine's findings; True when one of them is an error.

        An error here is the single fire for its input (03-R18): the caller
        then skips the base TYPE_MISMATCH and the transition advisory.
        """
        fired = False
        for f in findings:
            emit(self.issues, f.code, location=location, detail=f.detail(), fix=f.fix())
            fired = fired or RULES[f.code].category.value == "correctness"
        return fired

    def _binding_decl(self, sig):
        key = (sig.name, sig.version)
        decl = self._binding_decls.get(key)
        if decl is None:
            from pipeline_engine.base.registry import get_all_versions

            try:
                versions = get_all_versions(sig.name)
            except KeyError:  # a test-registered or effective-only signature
                versions = {sig.version: sig}
            decl = self._binding_decls[key] = decl_from_signature(sig, versions)
        return decl

    def _binding_step(self, sig, step: ComponentRef, cur: "FlowVal", loc, spath: str):
        """Drive the scope machine for one component; (facet, fired)."""
        params = {name: _effective_param(step, sig, name) for name in sig.parameters}
        slots: dict = {}
        for pname in sig.slot_reads:
            slot = params.get(pname)
            entry = self.sigma.get(slot) if isinstance(slot, str) else None
            if entry is not None and not isinstance(entry.val.tok, R.Record):
                slots[pname] = _facet_of(entry.val)
        out = self.binding.step(
            self._binding_decl(sig),
            _facet_of(cur),
            path=spath,
            params=params,
            slots=slots,
            location=loc,
            domain=_set_domain(cur),
            producer=cur.src,
        )
        return out.value, self._binding_emit(out.findings, loc)

    def _journal_row(self, row_id: str, location, **kwargs) -> None:
        self.journal.append((_CODE[row_id], _format_location(location), kwargs))

    def _verdict(self, actual_tok, demand_tok, mode: str) -> R.Verdict:
        if isinstance(actual_tok, R.Record):
            actual_tok = R.Pair("dict")  # §5.4 erase; staging decides the rest
        return R.subsumes(actual_tok, demand_tok, mode, staged=self.staged)

    def _gate(
        self,
        v: R.Verdict,
        *,
        step: str,
        location: str | None,
        mode: str,
        slot: str | None = None,
        provenance: str | None = None,
        demand=None,
        actual: FlowVal | None = None,
    ) -> bool:
        """True iff the CALLER's base-code row should emit (spec 05 §2.2).

        Staged verdicts resolve their stage from ``STAGED_CHANGES``: DORMANT
        (and pre-state flow shapes) divert to ``staged_outcomes`` and stay
        structurally silent; ARMED stages surface through ``emit()`` with
        stage-resolved severity. Armed base-code verdicts (TYPE_MISMATCH /
        SLOT_TYPE_MISMATCH firing-surface rows) return True — the caller
        emits its own row and passes ``row_staged_by=v.staged_by`` so the
        stage cap applies; armed value-domain verdicts are composed here.
        """
        if v.staged_by and v.outcome in ("error", "advisory"):
            if not _stage_armed(v.staged_by):
                self.staged_outcomes.append(
                    StagedOutcome(
                        v.code, v.staged_by, v.outcome, mode, step, location, slot, provenance
                    )
                )
                return False
            if v.code in ("TYPE_MISMATCH", "SLOT_TYPE_MISMATCH", "EMPTY_PARALLEL"):
                # Caller-emitted staged codes: the base-code firing surfaces
                # (TYPE_MISMATCH / SLOT_TYPE_MISMATCH) plus the structural
                # EMPTY_PARALLEL gate — the caller emits its own row with
                # ``row_staged_by`` so the stage cap applies. Only value-domain
                # verdicts (spec 05 §1.2's three codes) are composed below.
                return True
            if (
                v.code == "VALUE_BOUNDS_ADVISORY"
                and v.domain_class == "unproven"
                and self.profile.flow["soft-unproven-silence"] == "post"
            ):
                # The claims-model unproven-soft retirement (spec 09 §3.4):
                # at 'post' the row is structurally SILENT — a plain accept.
                # NOT a staged_outcomes diversion: that channel means "withheld
                # pending arming" (the dormant-silence channel a severity ramp
                # or the M4b clock facet uses), and a flow-shape flip is not a
                # staged OUTCOME. This row is RETIRED, not withheld — there is
                # no future arming collecting evidence here. The edge itself
                # stays visible in the schema-1 trace, which re-derives every
                # input-edge verdict independently of emission (``_sink_check``)
                # — spec 09 §3.4's "recorded in the trace, not surfaced as an
                # issue". The viol shape (a witnessed contradiction) keeps
                # emitting above.
                return False
            if (
                v.code == "VALUE_DOMAIN_UNPROVEN"
                and self._soft_roles_survive
                and self._role_conflict(demand, actual)
            ):
                # D-1's role judgment (ledger Q-0543). Gating on the
                # VALUE_DOMAIN_UNPROVEN row IS the precedence, expressed
                # structurally rather than re-derived: that row is reached
                # only for `unproven` at a HARD demand, so `sat` has already
                # accepted above (a proven domain outranks any role
                # mismatch), `viol` has already gone to
                # VALUE_DOMAIN_MISMATCH, and soft demands never land here —
                # keeping soft domains permanently advisory per GOAL
                # non-goal 3. All that remains to ask is whether the two
                # DECLARED names name different roles.
                self._emit_domain(
                    v,
                    step,
                    location,
                    demand,
                    actual,
                    f" via slot '{slot}'" if slot else "",
                    code="REFINED_ROLE_MISMATCH",
                )
                return False
            self._emit_domain(
                v, step, location, demand, actual, f" via slot '{slot}'" if slot else ""
            )
            return False
        return v.rejects

    @staticmethod
    def _role_conflict(demand, actual) -> bool:
        """Do the actual and the demand declare DIFFERENT refined roles?

        Reads the RAW declared bases on both sides — the demand token keeps
        its refined name (``exp`` unfolds inside ``subsumes``, not here), and
        the actual keeps its own under D-1's `norm`. Delegates the rule
        itself to ``relation.role_conflict`` so both engines and both call
        sites share one definition.
        """
        if not isinstance(demand, R.Pair) or actual is None:
            return False  # union/record demands carry no single role
        tok = actual.tok
        if not isinstance(tok, R.Pair):
            return False
        return R.role_conflict(tok.base, demand.base)

    def _emit_domain(
        self, v: R.Verdict, step, location, demand, actual, via, *, code: str | None = None
    ) -> None:
        """Compose an ARMED value-domain emission (spec 05 §1.2's three codes).

        ``code`` overrides the verdict's own code for D-1's role judgment,
        which re-labels an `unproven × hard` verdict as
        REFINED_ROLE_MISMATCH. The verdict object is otherwise unchanged —
        the relation still classified the DOMAIN half exactly as before; the
        role only decides which code carries it to the user.
        """
        code = code or v.code
        dom = actual.tok.domain if actual and isinstance(actual.tok, R.Pair) else R.TOP
        kwargs: dict = {
            "consumer": step,
            "expected_display": _display(demand),
            "expected_domain": _render_domain(_demand_domain(demand)),
            "via": via,
        }
        if code == "REFINED_ROLE_MISMATCH":
            kwargs["producer"] = (actual.src if actual else None) or "the previous step"
            kwargs["actual_role"] = (
                actual.tok.base if actual and isinstance(actual.tok, R.Pair) else "a value"
            )
        elif code == "VALUE_DOMAIN_MISMATCH":
            kwargs["producer"] = (actual.src if actual else None) or "the previous step"
            kwargs["actual_domain"] = _render_domain(dom)
            if step in UNIVERSE_MASK_APPLIERS and _is_filter_mask_domain(dom):
                # The two mask systems (M-20): a 0/1 filter mask belongs to
                # ApplyMask, not to a 1.0/NaN universe-mask applier.
                kwargs["suggestion"] = emit_template("domain-filter-mask-applier").format(
                    consumer=step
                )
        elif code == "VALUE_BOUNDS_ADVISORY":
            kwargs["finding"] = (
                "are not statically known"
                if v.domain_class == "unproven"
                else f"are known to include {_render_domain(dom)}-values outside it"
            )
        # Envelope (spec 05 §3.3): a value-domain outcome carries expected/actual
        # and a one-hop provenance origin (the producing step + its typed value).
        env_actual = _actual_ref(actual) if actual is not None else None
        prov: tuple = ()
        if actual is not None:
            prov = (ProvenanceHop("origin", (actual.src or step), (), env_actual, None),)
        emit(
            self.issues,
            code,
            location=location,
            expected=_tok_ref(demand),
            actual=env_actual,
            provenance=prov,
            **kwargs,
        )

    def _bridge_categories(self, actual_key: str | None, demand_key: str | None) -> list[str]:
        """The categories whose TRANSITION turns ``actual`` into ``demand``.

        One lookup in the generated A6 ``type_transitions`` — the table that
        has always known the answer and was never consulted here. Sorted so
        the rendered suggestion is deterministic across engines.
        """
        if actual_key is None or demand_key is None:
            return []
        row = _TRANSITIONS.get(actual_key) or {}
        return sorted(cat for cat, outs in row.items() if demand_key in outs)

    def _bridge_examples(self, cur: FlowVal, demand, category: str) -> list[str]:
        """Up to three FULL-registry components of ``category`` that bridge.

        Searches ``bridge_registry`` (the latest full registry), never the
        lock-scoped checking view — the whole point is to name something the
        strategy does NOT contain. ``slot_op`` is excluded by construction:
        Store/Load/StoreValue declare ``Any -> Any``, sit first in
        COMPONENT_REGISTRY order, and therefore answered EVERY query before
        this — measured 2026-09-22, the Options arm fired on 90 of 90 ordered
        carrier pairs and every answer was `Store, Load, StoreValue`.
        """
        out: list[str] = []
        for name in sorted(self.bridge_registry):
            sig = self.bridge_registry[name]
            cat = getattr(sig.category, "value", None)
            if cat != category or cat == "slot_op":
                continue
            # Never suggest a fix that earns its own warning: a deprecated
            # component fires DEPRECATED_COMPONENT the moment the author
            # follows the advice, and a constrained shell (`not_runnable`)
            # redirects to a different name at run time.
            if getattr(sig, "status", "active") != "active" or getattr(sig, "not_runnable", None):
                continue
            if self._verdict(cur.tok, _demand_tok(sig.input_type), "strict").rejects:
                continue
            if self._verdict(_decl_tok(sig.output_type), demand, "strict").rejects:
                continue
            out.append(name)
            if len(out) >= 3:
                break
        return out

    def _bridge(self, cur: FlowVal, expected_type, step_name: str = "this step") -> str:
        """The TYPE_MISMATCH fix: name the BRIDGING CATEGORY, then components.

        Q-1696. Three facts decide which member of the `bridge-*` template
        family renders, and all three are read from generated tables or the
        registry — never guessed:

        1. ``actual is None`` at the head of the pipeline is not a conversion
           problem at all; `data_loader` is the only category transitioning
           FROM nothing, so the fix is prose about starting with a loader.
        2. A WeightSeries flowing into a signal demand, produced by a
           position sizer, is a REORDER — the sizer is in the wrong place, and
           telling the author to insert a converter sends them to change the
           consumer's output instead (the M-22 shape, where the transition
           advisory used to blame `PositionStateMachine` for the sizer's
           position).
        3. Otherwise: the bridging categories from TYPE_TRANSITIONS, one hop
           or two, with up to three FULL-registry components each.
        """
        demand = _demand_tok(expected_type)
        exp_name = type_name(expected_type)
        prev = cur.src or "the previous step"

        # (1) nothing flows in yet — the loader shape.
        if cur.name in ("None", "NoneType"):
            return emit_template("bridge-loader")

        actual_key = R.transition_key(cur.name)
        demand_key = R.transition_key(exp_name)

        # (2) a sizer sitting before the step that should feed it.
        prev_sig = self.registry.get(cur.src) if cur.src else None
        if (
            prev_sig is not None
            and getattr(prev_sig.category, "value", None) == "position_sizer"
            and actual_key is not None
            and demand_key is not None
            and actual_key == _terminal_weight_key()
            and demand_key != actual_key
        ):
            return emit_template("bridge-reorder").format(prev=prev, step=step_name)

        # (3) the table-derived bridge, one hop then two.
        for category in self._bridge_categories(actual_key, demand_key):
            examples = self._bridge_examples(cur, demand, category)
            if examples:
                return emit_template("bridge-category").format(
                    category=category,
                    prev=prev,
                    step=step_name,
                    actual=cur.name,
                    expected=exp_name,
                    examples=", ".join(examples),
                )
        two_hop = self._bridge_two_hop(cur, demand, actual_key, demand_key, exp_name)
        if two_hop is not None:
            return two_hop
        return emit_template("bridge-none").format(actual=cur.name, expected=exp_name)

    def _bridge_two_hop(self, cur: FlowVal, demand, actual_key, demand_key, exp_name: str):
        """`RankSignal -> WeightSeries` via `forecast_mapper -> position_sizer`.

        Capped at two hops (W4 §3.2): a third would name a restructuring the
        author is better off reading from the component reference. Returns
        ``None`` when no two-hop path exists, so the caller falls through to
        the honest `bridge-none`.
        """
        if actual_key is None or demand_key is None:
            return None
        row = _TRANSITIONS.get(actual_key) or {}
        for cat_1 in sorted(row):
            for mid_key in sorted(row[cat_1]):
                if mid_key == actual_key:
                    continue
                for cat_2 in self._bridge_categories(mid_key, demand_key):
                    ex_1 = self._bridge_examples(cur, _demand_tok_name(mid_key), cat_1)
                    if not ex_1:
                        continue
                    ex_2 = [
                        name
                        for name in sorted(self.bridge_registry)
                        if (sig := self.bridge_registry[name]) is not None
                        and getattr(sig.category, "value", None) == cat_2
                        and cat_2 != "slot_op"
                        and getattr(sig, "status", "active") == "active"
                        and not getattr(sig, "not_runnable", None)
                        and not self._verdict(_decl_tok(sig.output_type), demand, "strict").rejects
                    ][:3]
                    if not ex_2:
                        continue
                    return emit_template("bridge-two-hop").format(
                        expected=exp_name,
                        actual=cur.name,
                        category_1=cat_1,
                        examples_1=", ".join(ex_1),
                        category_2=cat_2,
                        examples_2=", ".join(ex_2),
                    )
        return None

    # ── the walk (J-PIPE left-fold, spec 01 §8.2) ────────────────────────────

    def _pipeline(self, pipe: PipelineSpec, cur: FlowVal, ctx: str, path: str) -> FlowVal:
        frame: list = [pipe.steps, 0]
        self._tail_frames.append(frame)
        try:
            return self._pipeline_steps(pipe, cur, ctx, path, frame)
        finally:
            self._tail_frames.pop()

    def _downstream_steps(self) -> list:
        """Every step that consumes the CURRENT step's output, in order.

        The rest of the innermost body, then the rest of each enclosing body
        (a nested Pipeline or an inlined variable hands its output on to the
        step after it), up to the nearest Parallel branch boundary.
        """
        out: list = []
        for frame in reversed(self._tail_frames):
            if frame is None:
                break
            steps, idx = frame
            out.extend(steps[idx + 1 :])
        return out

    def _pipeline_steps(
        self, pipe: PipelineSpec, cur: FlowVal, ctx: str, path: str, frame: list
    ) -> FlowVal:
        prev_par: FlowVal | None = None
        for i, step in enumerate(pipe.steps):
            frame[1] = i
            spath = f"{path}/{i}"
            if isinstance(step, ComponentRef):
                cur, prev_par = self._component(step, cur, prev_par, ctx, i, spath)
            elif isinstance(step, SlotStoreSpec):
                if self.binding is not None:
                    self._binding_emit(
                        self.binding.store(step.slot_name, _facet_of(cur)),
                        _format_location(step.location),
                    )
                val = self._seeded(step.slot_name) if self.structural else cur
                self.sigma[step.slot_name] = _SlotEntry(val, step.location, spath)
                self._entry(f'Store("{step.slot_name}")', cur.name, cur.name, "slot_op")
                if self.ev is not None:
                    self.ev.on_slot_write(step.slot_name, val)
                self._trace(spath, "slot_store", None, None, cur)
            elif isinstance(step, SlotStoreValueSpec):
                lit = _literal_val(step.value)
                self.sigma[step.slot_name] = _SlotEntry(lit, step.location, spath)
                self._entry(
                    f'StoreValue("{step.slot_name}", {step.value!r})', cur.name, cur.name, "slot_op"
                )
                if self.ev is not None:
                    self.ev.on_slot_write(step.slot_name, lit)
                    if step.value is None:  # S7 sentinel row (spec 01 §2.1)
                        self.ev.on_check(None, "skip:none-storevalue")
                self._trace(spath, "slot_store_value", None, None, cur)
            elif isinstance(step, SlotLoadSpec):
                self.used.add(step.slot_name)
                entry = self.sigma.get(step.slot_name)
                if entry is None:  # §3.5 R4: τ unchanged; SLOT_NOT_FOUND journaled
                    self._journal_row("J-LOAD.slot-present", step.location, slot=step.slot_name)
                    if self.ev is not None:
                        self.ev.on_check(None, f"issue:{_CODE['J-LOAD.slot-present']}")
                else:
                    cur = entry.val  # J-LOAD restores the FULL pair (spec 01 §6.2)
                    if self.binding is not None:
                        loaded = self.binding.load(step.slot_name, _facet_of(cur))
                        self._binding_emit(loaded.findings, _format_location(step.location))
                        cur = _with_facet(cur, loaded.value)
                self._entry(f'Load("{step.slot_name}")', cur.name, cur.name, "slot_op")
                self._trace(spath, "slot_load", None, None, cur)
            elif isinstance(step, SlotExtractSpec):
                if not self.structural:
                    cur = self._extract(step, cur, prev_par, spath)
            elif isinstance(step, ParallelSpec):
                cur = self._parallel(step, pipe, i, cur, ctx, spath)
                prev_par = cur
            elif isinstance(step, PipelineSpec) and step.factory_refused:
                # A registered factory whose expansion was refused (pass 5b,
                # spec 03-R47): the call carries the issue; its output is
                # unknown (§3.5 R1's recovery, never a pass-through). It
                # carries R1's checker signature too, the UNRESOLVED
                # type-flow entry, so the Any it hands on has a visible
                # origin (end_state_test's no-manufactured-Any scan).
                self._entry(step.name or "factory", cur.name, "UNRESOLVED", "error")
                cur = _ANY_VAL
            elif isinstance(step, PipelineSpec):
                sub = f"{spath}/!{step.name}" if step.name is not None else spath
                cur = self._pipeline(step, cur, f"{ctx}.step[{i}]", sub)
            elif isinstance(step, VariableRef):
                vp = self.variable_pipelines.get(step.name)
                if vp is not None:
                    # Defensive cycle backstop — a clean error, never a
                    # silent skip and never a stack overflow. Cyclic specs
                    # are refused upstream (validator pass 3b / verify_spec:
                    # PIPELINE_VARIABLE_CYCLE), so a revisit here means an
                    # ungated caller handed the walk an unvalidated spec.
                    if step.name in self._var_stack:
                        raise RuntimeError(
                            f"Pipeline-variable cycle reached the interpreter "
                            f"walk: variable '{step.name}' is already being "
                            f"inlined (active: {sorted(self._var_stack)}). "
                            f"Cyclic specs fail validation with "
                            f"PIPELINE_VARIABLE_CYCLE — run validate_strategy "
                            f"or verify_spec before interpreting."
                        )
                    self._var_stack.add(step.name)
                    try:
                        cur = self._pipeline(vp, cur, f"var[{step.name}]", f"{spath}/!{step.name}")
                    finally:
                        self._var_stack.discard(step.name)
        return cur

    def _seeded(self, name: str) -> FlowVal:
        seed = self.store_seed.get(name)
        return _from_live(seed) if seed is not None else _NONE_VAL  # S7 sentinel

    # ── J-STEP (components) ──────────────────────────────────────────────────

    def _component(self, step: ComponentRef, cur: FlowVal, prev_par, ctx: str, i: int, spath: str):
        sig = self.registry.get(step.name)
        if not sig:  # §3.5 R1: unresolved signature ⇒ UNRESOLVED entry + τ := Any
            self._entry(step.name, cur.name, "UNRESOLVED", "error")
            cur = _ANY_VAL
            self._trace(spath, "component", step.name, None, cur)
            return cur, prev_par
        loc = _format_location(step.location)
        cat = sig.category.value
        loader = cat == "data_loader"
        dict_in = cur.obj is dict
        gated: str | None = None  # the fired dict-gate code, if any
        input_rejected = False  # base-half short-circuit for the clock facet
        bind_facet, bind_fired = None, False
        if self.binding is not None:
            bind_facet, bind_fired = self._binding_step(sig, step, cur, loc, spath)
        if not self.structural:
            if (
                not bind_fired
                and self.profile.flow["composer-record-reach"] == "post"
                and isinstance(cur.tok, R.Record)
            ):
                self._composer_checks(cur.fields or {}, step, tail=self._downstream_steps())
            if cat in _COMPOSER_CATS and not dict_in and not loader and cur.obj is not TypingAny:
                emit(
                    self.issues,
                    _CODE["J-COMPOSER.dict-expected"],
                    location=loc,
                    suggestion=emit_template("parallel-before-composer").format(
                        component=step.name
                    ),
                    step=emit_template("composer-step-display").format(component=step.name),
                    actual=_actual_ref(cur),
                )
                gated = _CODE["J-COMPOSER.dict-expected"]  # suppresses same-step checks (§2.3)
            elif (
                dict_in
                and cat not in _DICT_ROW_CATS
                and cat != "slot_op"
                and not loader
                and not sig.slot_reads
            ):
                emit(self.issues, _CODE["J-REC.consumption"], location=loc, step=step.name)
                gated = _CODE["J-REC.consumption"]
            if gated is None and not bind_fired:
                if cur.obj is not TypingAny and sig.input_type is not TypingAny:
                    # strict-check-skip set: loaders + dict-row categories ONLY (R-4)
                    if not (loader or (dict_in and cat in _DICT_ROW_CATS)):
                        demand = _demand_tok(sig.input_type, sig.input_domain)
                        v = self._verdict(cur.tok, demand, "strict")
                        input_rejected = v.outcome == "error"
                        if self._gate(
                            v,
                            step=step.name,
                            location=loc,
                            mode="strict",
                            demand=demand,
                            actual=cur,
                        ):
                            emit(
                                self.issues,
                                _CODE["J-STEP.input-check"],
                                location=loc,
                                suggestion=self._bridge(cur, sig.input_type, step.name),
                                row_staged_by=v.staged_by,
                                context=f"{ctx}.step[{i}]",
                                step=step.name,
                                expected=_expected_ref(sig.input_type, sig.input_domain),
                                actual=_actual_ref(cur),
                                path=_path_of(spath),  # envelope machine path (§3.3)
                            )
                # Composition advisory (Q-1694 / W4 §1.4): a selection
                # sizer fed a forecast. Runs only when the step's own input
                # check ACCEPTED — a rejected input already carries the
                # verdict, and what flows on from it is `Any`/junk, so a
                # second opinion on it is noise (the `gated` discipline,
                # §2.3). The declared NAME is the discriminator, not the
                # relation: a ForecastSeries subsumes into the SignalSeries
                # demand these sizers declare, which is precisely why the
                # type system is silent here.
                if (
                    not input_rejected
                    and cat == "position_sizer"
                    and cur.name == _FORECAST_CARRIER
                    and step.name in _SELECTION_SIZERS
                    and not _uniform_magnitude(cur.tok)
                    and _stage_armed(_FORECAST_MAGNITUDE_KEY)
                ):
                    emit(
                        self.issues,
                        code="FORECAST_MAGNITUDE_DISCARDED",
                        location=loc,
                        step=step.name,
                        producer=cur.src or "the previous step",
                    )
                prev_key = R.transition_key(cur.name)
                if prev_key is not None and not loader:
                    row = _TRANSITIONS.get(prev_key)
                    if row is not None and cat in row:
                        out_key = R.transition_key(type_name(sig.output_type))
                        if out_key is not None and out_key not in row[cat]:
                            emit(
                                self.issues,
                                _CODE["J-STEP.transition-advisory"],
                                location=loc,
                                step=step.name,
                                category=cat,
                                output=out_key,
                                prev_output=prev_key,
                                expected_outputs=row[cat],
                                suggestion=_transition_suggestion(
                                    step.name, cat, prev_key, row[cat]
                                ),
                            )
            if self.ev is not None:
                self._sink_check(sig, cur, loader, dict_in, cat, gated)
            # ── Clock facet input resolution (spec 01 §6.1/§6.3) ──────────
            # Record input: J-CLKUNIFORM over the bound field keys decides
            # the consumer's clock (common κ | R-2 recovery); the record
            # itself is clock-less. Gated record discipline (§5.4) and a
            # failed base premise (§6.6 row 1) skip the transform rows and
            # keep κ_cur.
            if isinstance(cur.tok, R.Record) and gated is None:
                clock_in, clock_origin, clock_path, clock_hops = self._clock_uniform(
                    cur.fields or {}, sig, step, loc, spath
                )
            else:
                clock_in, clock_origin = cur.clock, cur.clock_origin
                clock_path, clock_hops = cur.clock_origin_path, cur.clock_hops
            out = self._synthesize(
                sig,
                step,
                cur,
                loc,
                f"{ctx}.step[{i}]",
                spath,
                clock_in=clock_in,
                clock_origin=clock_origin,
                clock_path=clock_path,
                clock_hops=clock_hops,
                clock_skip=input_rejected or gated is not None,
            )
            if bind_facet is not None:
                out = _with_facet(out, bind_facet)
            self._entry(
                f"{step.name}({_format_params_brief(step.params)})",
                cur.name,
                type_name(sig.output_type),
                cat,
                binding=_binding_json(out),
            )
        else:
            out = cur
        self._slot_reads(sig, step, cur, loc, spath)  # pass-8 runs regardless of pass-6 gates
        cur = out
        if gated:
            prev_par = None
        if not self.structural:
            self._trace(spath, "component", step.name, sig.version, cur)
        return cur, prev_par

    # ── synthesis: J-INST / J-LIT / declared output + norm (spec 01 §3-§4) ───

    def _synthesize(
        self,
        sig,
        step: ComponentRef,
        cur: FlowVal,
        loc: str,
        ctx: str,
        spath: str,
        *,
        clock_in=None,
        clock_origin=None,
        clock_path: tuple[str, ...] = (),
        clock_hops: tuple = (),
        clock_skip: bool = False,
    ) -> FlowVal:
        out_t = sig.output_type
        # A SCHEME member's output position is the type VARIABLE, not a
        # declared type. An UNBOUNDED `∀T. T → T` (the projector pair —
        # dsl-mtf-clocks spec 01 §8.2) has no name for `T` in the Python
        # registry, so it necessarily spells the output `Any`: `output_type
        # is Any` alone therefore does NOT mean "declared frontier". A
        # declared `type_relation` makes this a BINDING SITE (parent §2.1 S5
        # ⇒ J-INST, parent §3.3) whose instantiation carries base, domain AND
        # clock through (spec 01 §4.3's triple; visible at §9.1 step 3r.4).
        # A genuinely-untyped output — no relation — stays the declared `Any`
        # frontier (parent §10.2), clock-less per spec 01 §4.1. The frontier
        # still propagates from the BINDING SOURCE below (`base is None`),
        # which is the case spec 01 §5.4's Any row actually describes.
        instantiates = bool(self.profile.flow["scheme-synthesis"] == "post" and sig.type_relation)
        if out_t is TypingAny and not instantiates:
            return _ANY_VAL  # Any is clock-less (spec 01 §4.1)
        out_name = type_name(out_t)
        if _is_union(out_t):
            # Unions carry no clock (spec 01 §4.1).
            return FlowVal(_decl_tok(out_t), out_t, out_name, src=step.name, src_ref=step)
        # A variable output has no declared base to fall back on: the
        # unresolvable-binding degrade of an UNBOUNDED scheme is `T := Any`
        # (parent §3.2 — F is the declared output base, and that is `Any`
        # only when the declaration itself is a frontier), so `None` here
        # routes that degrade back to the frontier below.
        base: str | None = None if out_t is TypingAny else R.canon_base(_base_of(out_name))
        obj = out_t
        if instantiates:
            src = self._rel_source(sig, step, cur)
            base, degraded = self._inst_base(sig, step, src, base, loc, ctx, spath)
            if base is None:  # Any frontier propagates visibly (spec 01 §3.3)
                return _ANY_VAL
            if out_t is TypingAny and not degraded and src is not None:
                # `T`'s boundary object is the BOUND value's, never the `Any`
                # spelling — otherwise the downstream demand edge would keep
                # skipping checks against a frontier the scheme has resolved.
                obj = src.obj
            if degraded:  # bound failure ⇒ (F, ⊤) — spec 01 §8.1/§3.2
                pair = T.norm(base, R.TOP, soft_roles_survive=self._soft_roles_survive)
                fv = FlowVal(pair, out_t, pair.base, src=step.name, src_ref=step)
                # Failed NON-clock premise: κ_out = keep(κ_cur) (§6.6 row 1).
                return self._clock_stamp(
                    fv,
                    sig,
                    step,
                    cur,
                    loc,
                    clock_in,
                    clock_origin,
                    True,
                    spath,
                    clock_path,
                    clock_hops,
                )
        doms: dict[str, object] = {}
        transfer = self._resolve_refs(
            sig.domain_transfer or {"fn": "top"}, step, cur, doms, sig=sig
        )
        pair = T.norm(
            base,
            T.eval_transfer(transfer, doms, step.params),
            soft_roles_survive=self._soft_roles_survive,
        )
        name = out_name if self.profile.flow["scheme-synthesis"] == "pre" else pair.base
        fv = FlowVal(pair, obj, name, src=step.name, src_ref=step)
        return self._clock_stamp(
            fv,
            sig,
            step,
            cur,
            loc,
            clock_in,
            clock_origin,
            clock_skip,
            spath,
            clock_path,
            clock_hops,
        )

    def _rel_source(self, sig, step: ComponentRef, cur: FlowVal) -> FlowVal | None:
        """The value `T` binds to — flow-directed, O(1) (parent §3.2/§3.3).

        ``None`` is the unresolvable binding (absent/non-literal param, or a
        key the input record does not carry), which J-INST degrades.
        """
        rel_out = sig.type_relation.get("output")
        if rel_out == "input":
            return cur
        if isinstance(rel_out, dict) and "field_by_param" in rel_out:
            key = step.params.get(rel_out["field_by_param"])
            if isinstance(key, str) and cur.fields:
                return cur.fields.get(key)
        return None

    def _inst_base(
        self,
        sig,
        step: ComponentRef,
        src: FlowVal | None,
        default: str | None,
        loc: str,
        ctx: str,
        spath: str,
    ) -> tuple[str | None, bool]:
        """J-INST: bind T flow-directed; bound-check; degrade T := (F, ⊤) (§3.2).

        Returns ``(base, degraded)`` — ``base is None`` propagates the Any
        frontier; ``degraded`` marks a FAILED bound premise, whose recovery
        synthesis is ``(F, ⊤)`` (spec 01 §8.1's J-INST row), never the
        transfer-computed domain.
        """
        bound = sig.type_relation.get("bound")
        if src is not None and src.tok is R.ANY:
            return None, False
        if src is None or not isinstance(src.tok, R.Pair):
            # Unresolvable-binding degrade (§3.2): T := (F, ⊤) via transfer ⊤
            # refs. ``default is None`` is the unbounded variable-output case
            # — F is the declared output base, i.e. the `Any` frontier.
            return (R.canon_base(bound) if bound else default), False
        if bound:
            v = self._verdict(R.Pair(src.tok.base), R.Pair(bound), "strict")
            if v.rejects or (v.staged_by and v.outcome in ("error", "advisory")):
                # New firing surface of the scheme-synthesis flip (spec 03
                # §3.4(2)): pre-state diverts, post-state emits (spec 05 §2.2).
                key = v.staged_by or "scheme-synthesis"
                if _stage_armed(key):
                    emit(
                        self.issues,
                        _CODE["J-STEP.synthesize"],
                        location=loc,
                        row_staged_by=v.staged_by,
                        context=ctx,
                        step=step.name,
                        expected=_base_ref(R.canon_base(bound)),
                        actual=_base_ref(src.tok.base, src.tok.domain),
                        path=_path_of(spath),  # envelope machine path (§3.3)
                    )
                else:
                    self.staged_outcomes.append(
                        StagedOutcome(
                            v.code or _CODE["J-STEP.synthesize"],
                            key,
                            "error",
                            "strict",
                            step.name,
                            loc,
                        )
                    )
                return R.canon_base(bound), True
        return R.canon_base(src.tok.base), False

    def _resolve_refs(self, t: dict, step: ComponentRef, cur: FlowVal, out: dict, sig=None) -> dict:
        """Pre-resolve transfer input refs to string keys + a domains map."""
        fn = t.get("fn") if isinstance(t, dict) else None
        if fn == "id":
            key, dom = self._ref_domain(t["input"], step, cur, sig)
            out[key] = dom
            return {"fn": "id", "input": key}
        if fn == "union":
            return {
                "fn": "union",
                "args": [self._resolve_refs(a, step, cur, out, sig) for a in t["args"]],
            }
        if fn in ("join", "cond_fixed"):
            # Same operand shape for both: a named-ref list or "all". They
            # differ only in what eval_transfer does with the resolved
            # domains (⊔ vs the ⊑ guard), never in how refs resolve.
            refs = t["inputs"]
            keys = []
            for ref in list((cur.fields or {})) if refs == "all" else refs:
                key, dom = self._ref_domain(ref, step, cur, sig)
                out[key] = dom
                keys.append(key)
            return {**t, "inputs": keys}
        if fn == "cond_id":
            # cond_id: the pass-through input + each guard's ref resolve
            # through the ONE ref resolver (slot-vocabulary successor §6.1);
            # eval_transfer then tests each guard's resolved domain ⊑ its
            # declared element and passes the input's domain through.
            key, dom = self._ref_domain(t["input"], step, cur, sig)
            out[key] = dom
            guards = []
            for guard in t["guards"]:
                gkey, gdom = self._ref_domain(guard["ref"], step, cur, sig)
                out[gkey] = gdom
                guards.append({"ref": gkey, "within": guard["within"]})
            return {"fn": "cond_id", "input": key, "guards": guards}
        return t

    def _ref_domain(self, ref, step: ComponentRef, cur: FlowVal, sig=None):
        if ref == "input":
            return "input", (cur.tok.domain if isinstance(cur.tok, R.Pair) else R.TOP)
        if isinstance(ref, dict) and "slot" in ref:
            # The slot-operand ref {"slot": p} (slot-vocabulary successor
            # §5.2, Q-0549 second half): p names the component's OWN declared
            # slot-read param; the param→slot binding resolves EXACTLY as
            # J-SLOTREAD resolves it (explicit param, else registry default,
            # else — implicit reads — the param name IS the slot name), and
            # the domain is read from Σ at the step's own position in the
            # walk (the same Σ J-SLOTREAD reads; parallel-branch snapshot
            # discipline applies unchanged). Degrade-to-⊤, never error, on
            # unresolvable bindings (VariableRef, unbound optional slot,
            # absent store, record-valued entry) — a guard whose premise
            # cannot be established fails honestly to ⊤ (the transfer
            # widens); the EXISTENCE diagnostics stay owned by J-SLOTREAD.
            pname = ref["slot"]
            sval = step.params.get(pname)
            if sval is None and sig is not None:
                pinfo = sig.parameters.get(pname)
                if pinfo is not None and isinstance(pinfo.default, str):
                    sval = pinfo.default
                elif pname not in sig.parameters and pname in sig.slot_reads:
                    sval = pname  # implicit read: the slot name IS the key
            if not isinstance(sval, str):
                return f"slot:{pname}", R.TOP
            entry = self.sigma.get(sval)
            if entry is not None and isinstance(entry.val.tok, R.Pair):
                return f"slot:{sval}", entry.val.tok.domain
            return f"slot:{sval}", R.TOP
        if isinstance(ref, dict) and "field_by_param" in ref:
            key = step.params.get(ref["field_by_param"])
            fv = (cur.fields or {}).get(key) if isinstance(key, str) else None
            dom = fv.tok.domain if fv is not None and isinstance(fv.tok, R.Pair) else R.TOP
            return (key if isinstance(key, str) else "?unresolved"), dom
        if isinstance(ref, dict) and isinstance(ref.get("field"), str):
            # The dict-shaped record-key spelling {"field": k} (spec 02 §2.2
            # INPUT_REF) — registration admits it, so the evaluator must
            # resolve it. It and the bare-string spelling below are two
            # spellings of ONE meaning (a literal record key), resolved
            # identically from the current record's fields (Q-0549 fix, S5
            # §1.6 — this used to fall through to TransferError:
            # accepted-at-registration, crash-at-evaluation).
            key = ref["field"]
            fv = (cur.fields or {}).get(key)
            return key, (fv.tok.domain if fv and isinstance(fv.tok, R.Pair) else R.TOP)
        if isinstance(ref, str):  # record-field spelling (join operands)
            fv = (cur.fields or {}).get(ref)
            return ref, (fv.tok.domain if fv and isinstance(fv.tok, R.Pair) else R.TOP)
        raise T.TransferError(f"Unresolvable transfer input ref: {ref!r}")

    # ── J-SLOTREAD (journaled — pass-8 position emission) ────────────────────

    def _slot_ref_fix(
        self, slot: str, param: str | None = None, component: str | None = None
    ) -> str:
        """The context-selected SLOT_REF_NOT_FOUND fix (W4 §4.1, Q-1696).

        `sibling` when the read sits inside a Parallel branch and SOME OTHER
        branch of the SAME Parallel stores the slot — the one case where the
        catalog's default fix ("add a Store before this component") produces a
        second writer of one slot name and a runtime SlotOverwriteError.
        Innermost enclosing Parallel first: that is the block whose snapshot
        hid the store. Every other case keeps the default text.
        """
        for stores, branch in reversed(self._branch_frames):
            other = stores.get(slot)
            if other is not None and other != branch:
                return emit_template("slot-ref-fix", "sibling").format(
                    slot=slot, other=other, branch=branch
                )
        if param in _VOL_SLOT_PARAMS and component is not None:
            return emit_template("slot-ref-fix", "none-vol").format(slot=slot, component=component)
        return emit_template("slot-ref-fix", "none").format(slot=slot)

    def _slot_reads(self, sig, step: ComponentRef, cur: FlowVal, loc: str, spath: str) -> None:
        if not sig.slot_reads:
            return
        for pname, expected in sig.slot_reads.items():
            implicit = pname not in sig.parameters  # implicit read: slot name IS the key
            if implicit:
                # The slot name is hard-coded in the component (implicit_slot_reads
                # declaration surface, registration.py) — same existence contract
                # as an explicit slot-reference parameter, and the TS engine has
                # journaled these since M2d (interpreter.ts slotReads). Skipping
                # the sigma check here let a pipeline validate clean and die at
                # runtime with SlotNotFoundError (Q-0459).
                sval = pname
            else:
                sval = step.params.get(pname)
                if sval is None:
                    pinfo = sig.parameters.get(pname)
                    if pinfo is not None and isinstance(pinfo.default, str):
                        sval = pinfo.default
                if isinstance(sval, VariableRef) or not isinstance(sval, str):
                    continue  # runtime-resolved / unresolvable binding — declared skip
            self.used.add(sval)
            entry = self.sigma.get(sval)
            if entry is None:
                self._journal_row(
                    "J-SLOTREAD.ref-present",
                    step.location,
                    component=step.name,
                    param=pname,
                    slot=sval,
                    suggestion=self._slot_ref_fix(sval, pname, step.name),
                )
                continue
            if self.structural:  # §5: binding resolution only, no typing rows
                continue
            if implicit:
                # Existence-only for implicit reads (exact TS parity: the TS
                # engine carries implicit read NAMES, not types, so a typed
                # J-SLOTREAD.type-check here would be a Python-only emission
                # and break conformance parity — typed implicit checks are a
                # declared follow-up, not smuggled in).
                continue
            # Per-slot domain demand (slot-vocabulary successor, Q-0548): the
            # declared `slot_domains` element rides the SAME ≤_slot query the
            # base half always made — `_demand_tok`'s dom argument existed
            # for input_domain (J-STEP); slot sites now pass it too. No new
            # judgment, no new verdict rows: the §2.4 slot column already
            # defines every outcome (domain viol|hard → VALUE_DOMAIN_MISMATCH
            # via `value-domain-rule`; unproven|hard → the staged D2
            # advisory), and the five live BinarySignal slots proved the
            # domain half in production.
            pinfo_dom = sig.parameters.get(pname)
            slot_dom = getattr(pinfo_dom, "expected_slot_domain", None)
            demand = _demand_tok(expected, slot_dom)
            v = self._verdict(entry.val.tok, demand, "slot")
            if self._gate(
                v,
                step=step.name,
                location=loc,
                mode="slot",
                slot=sval,
                provenance=_format_location(entry.location),
                demand=demand,
                actual=entry.val,
            ):
                self._journal_row(
                    "J-SLOTREAD.type-check",
                    step.location,
                    row_staged_by=v.staged_by,
                    component=step.name,
                    param=pname,
                    # Slot type outcome (spec 05 §3.3): the demanded type as a
                    # TypeRef (base+domain), rendering byte-identically to the
                    # prior ``type_name(expected)`` prose. The A3 judgment row
                    # (J-SLOTREAD.type-check) declares these kwargs
                    # (checking-as-data, judgments_test), so path + slot-chain
                    # provenance ride here in lock-step with the row.
                    expected=_expected_ref(expected),
                    slot=sval,
                    stored=entry.val.name,
                    path=_path_of(spath),  # envelope machine path (§3.1 #7)
                    # Slot blame chain (§3.1 #11): the store hop that produced the
                    # value read here — origin step + its statically-typed value.
                    provenance=(
                        ProvenanceHop(
                            "store", entry.val.src or sval, (), _actual_ref(entry.val), sval
                        ),
                    ),
                )
            elif v.outcome != "error":
                # ── J-SLOTREAD clock half (spec 01 §6.2, M4b) ─────────────
                # The multi-input MATCH at a slot read: the stored clock vs
                # the READING step's flow-input clock. Base failure above
                # short-circuits (§6.1); either side clock-less ⇒ cfree.
                # JOURNALED, not emitted (spec 02 §4.3 slot shape): the
                # write-side-vs-read-side repair decision needs EVERY reader
                # of the slot, so the envelope can only be built once the
                # walk has seen them all — the pass-8 flush position.
                if entry.val.clock is not None and cur.clock is not None:
                    self._clock_slot_journal.append(
                        {
                            "slot": sval,
                            "param": pname,
                            "step": step.name,
                            "loc": loc,
                            "path": spath,
                            "stored": entry.val,
                            "reader": cur,
                            "store_path": entry.store_path,
                            "store_provenance": ProvenanceHop(
                                "store",
                                entry.val.src or sval,
                                _path_of(entry.store_path) if entry.store_path else (),
                                _actual_ref(entry.val),
                                sval,
                            ),
                        }
                    )

    # ── J-PROJ (Extract) — staged premise source + projection (§3.4 1/3) ─────

    def _extract(self, step: SlotExtractSpec, cur: FlowVal, prev_par, spath: str) -> FlowVal:
        loc = _format_location(step.location)
        post_reach = self.profile.flow["composer-record-reach"] == "post"
        src = cur if post_reach else prev_par
        is_rec = (
            isinstance(cur.tok, R.Record)
            if post_reach
            else (prev_par is not None and cur.obj is dict)
        )
        if not is_rec:  # §3.5 R2
            row = _ROWS["J-PROJ.record-ness"]["on_fail"]
            emit(
                self.issues,
                row["code"],
                location=loc,
                severity_context=row["severity_context"],
                step=emit_template("extract-step-display"),
                actual=_actual_ref(cur),
            )
            if self.ev is not None:
                self.ev.on_check(None, f"issue:{row['code']}")
            cur = _ANY_VAL
        elif step.key not in (src.fields or {}):  # §3.5 R3
            emit(
                self.issues,
                _CODE["J-PROJ.key-member"],
                location=loc,
                key=step.key,
                branches=sorted(src.fields or {}),
            )
            if self.ev is not None:
                self.ev.on_check(None, f"issue:{_CODE['J-PROJ.key-member']}")
            cur = _ANY_VAL
        elif self.profile.flow["extract-projection"] == "post":
            cur = src.fields[step.key]  # the matched field's type — PRECISE (§5.2)
        else:
            cur = _ANY_VAL  # pre: today's L71 widening, bit-for-bit
        self._trace(spath, "slot_extract", None, None, cur)
        return cur

    # ── J-REC (Parallel) + the unified Σ merge (spec 01 §5.1/§6.3) ───────────

    def _empty_parallel_verdict(self) -> R.Verdict:
        """J-REC.empty-parallel — the n≥1 record precondition (spec 01 §5.1 m7).

        Structural: no relation query, so the profile RELATION VECTOR
        (``self.staged``) stands in for "does this staged change fire in this
        configuration" — exactly what a relation query consults. Off in the
        vector ⇒ a plain accept, so the shipped m2-compat vector carries ZERO
        staged outcomes (the corpus shadow-parity invariant). On ⇒ the staged
        EMPTY_PARALLEL verdict, whose emit-vs-divert ``_gate`` then resolves
        from the GLOBAL stage (``_stage_armed``, the FIX-A discipline): DORMANT
        diverts to ``staged_outcomes`` (structurally silent), an armed stage
        surfaces — mirroring a relation-produced staged code exactly.
        """
        if not self.staged.get(_EMPTY_PARALLEL_STAGED_BY, False):
            return R.Verdict("accept")
        return R.Verdict("error", code=_EMPTY_PARALLEL_CODE, staged_by=_EMPTY_PARALLEL_STAGED_BY)

    def _parallel(
        self, step: ParallelSpec, pipe: PipelineSpec, i: int, cur: FlowVal, ctx: str, spath: str
    ) -> FlowVal:
        if not step.branches:  # J-REC n≥1 precondition (spec 01 §5.1 m7, structural)
            if self._gate(
                self._empty_parallel_verdict(),
                step=spath,
                location=_format_location(step.location),
                mode="strict",
            ):
                emit(
                    self.issues,
                    _EMPTY_PARALLEL_CODE,
                    location=_format_location(step.location),
                    step=spath,
                    row_staged_by=_EMPTY_PARALLEL_STAGED_BY,
                )
        base = self.sigma
        fields: dict[str, FlowVal] = {}
        deltas: list[dict[str, _SlotEntry]] = []
        branch_stores = _branch_store_map(step.branches)
        for bname, bsteps in step.branches.items():
            self.sigma = dict(base)  # branches type from the SNAPSHOT (§6.3 in)
            sub = PipelineSpec(steps=bsteps, name=bname, location=step.location)
            bpath = f"{spath}/{bname}"
            self._branch_frames.append((branch_stores, bname))
            self._tail_frames.append(None)  # a branch's book ends at the record
            if self.binding is not None:
                self.binding.enter_branch(bname)
            try:
                bval = self._pipeline(sub, cur, f"{ctx}.branch[{bname}]", bpath)
            finally:
                self._tail_frames.pop()
                self._branch_frames.pop()
            if self.binding is not None:
                opened = _facet_of(bval)
                closed = self.binding.exit_branch(opened)
                self._binding_emit(closed.findings, _format_location(step.location))
                if (
                    closed.findings
                    and isinstance(opened, Facet)
                    and opened.kind == "position"
                    and isinstance(closed.value, Facet)
                ):
                    # 03-R18: the never-exposed Position recovers as the
                    # exposure it should have become, so the one mistake fires
                    # no downstream base-type row (a composer's field check).
                    bval = _dc_replace(
                        _from_live(_REALIZED_TYPE),
                        facet=closed.value,
                        clock=bval.clock,
                        clock_origin=bval.clock_origin,
                        clock_origin_path=bval.clock_origin_path,
                        clock_hops=bval.clock_hops,
                    )
                else:
                    bval = _with_facet(bval, closed.value)
            # The §4.3 insert-at-end-of-producer target: this branch's node id
            # + the index of its LAST step (the projector goes after it).
            fields[bname] = _dc_replace(bval, producer=(bpath, len(bsteps) - 1)) if bsteps else bval
            deltas.append({k: v for k, v in self.sigma.items() if base.get(k) is not v})
        self.sigma = base
        for delta in deltas:  # new/changed entries, declaration order (§6.3 out)
            self.sigma.update(delta)
        rec = FlowVal(R.Record(tuple((k, f.tok) for k, f in fields.items())), dict, "dict", fields)
        if self.binding is not None:
            merged = self.binding.merge_parallel({k: _facet_of(f) for k, f in fields.items()})
            self._binding_emit(merged.findings, _format_location(step.location))
            if isinstance(merged.value, Facet) and merged.value.kind == "position":
                # One rule stage (03-R17): the Parallel's value is the Position.
                rec = _dc_replace(
                    _from_live(_POSITION_TYPE),
                    facet=merged.value,
                    clock=cur.clock,
                    clock_origin=cur.clock_origin,
                    clock_origin_path=cur.clock_origin_path,
                    clock_hops=cur.clock_hops,
                )
        if not self.structural:
            self._trace(spath, "parallel", None, None, rec)
            if self.profile.flow["composer-record-reach"] == "pre" and i + 1 < len(pipe.steps):
                nxt = pipe.steps[i + 1]
                if isinstance(nxt, ComponentRef):  # today's adjacency look-ahead
                    # The frame still points at the Parallel, so the
                    # downstream list starts WITH `nxt`; its tail is the rest.
                    self._composer_checks(fields, nxt, tail=self._downstream_steps()[1:])
        return rec

    # ── J-COMPOSER key/field discipline (spec 01 §5.3) ───────────────────────

    def _composer_checks(
        self, fields: dict[str, FlowVal], nstep: ComponentRef, *, tail: list | None = None
    ) -> None:
        sig = self.registry.get(nstep.name)
        if not sig:
            return
        loc = _format_location(nstep.location)
        if sig.category.value in _COMPOSER_CATS:
            bnames = set(fields)
            for pname, pval in nstep.params.items():
                if isinstance(pval, dict) and all(isinstance(k, str) for k in pval):
                    extra = set(pval) - bnames
                    if extra:
                        emit(
                            self.issues,
                            _CODE["J-COMPOSER.key-member"],
                            location=loc,
                            composer=nstep.name,
                            param=pname,
                            extra=sorted(extra),
                            branches=sorted(bnames),
                        )
        self._composition_checks(fields, nstep, sig, loc, tail or [])
        ci = getattr(sig, "composer_inputs", None)
        if ci is None or not fields:
            return
        if isinstance(ci, dict):
            if not ci:
                return  # explicit opt-out
            for role, expected in ci.items():
                bname = nstep.params.get(role)
                if bname is None and role not in nstep.params:
                    # Q-1283: the role is OMITTED, so run() binds the
                    # component's OWN declared default — omitting the role is
                    # the idiomatic way to write these components, which is
                    # why this seam mattered. The EFFECTIVE key is that
                    # default, and the registry already carries it, so the
                    # very same membership question applies. The declared
                    # default is also the discriminator, needing no new rule:
                    #   * non-``None`` str (SignalRatio's "numerator")
                    #     — a real branch name ⇒ checkable, below;
                    #   * ``None`` (ApplyMask's score_signal/filter_signal)
                    #     — the documented auto-detect ⇒ MUST stay skipped;
                    #   * ``MISSING`` (RegimeGate's signal_key, a required
                    #     param) — pass 5's business, not this arm's.
                    # Only the first is a str, so the skip below carries the
                    # other two unchanged.
                    pinfo = sig.parameters.get(role)
                    if pinfo is not None:
                        bname = pinfo.default
                if bname is None or isinstance(bname, VariableRef) or not isinstance(bname, str):
                    continue  # auto-detect / runtime-resolved — declared skips
                actual = fields.get(bname)
                if actual is None:
                    # The role names no branch. Nothing else catches it: the
                    # key-member arm above walks only DICT-valued params
                    # (ForecastCombiner's ``weights``), and a role key is a
                    # plain string — so this validated clean at every
                    # severity and only the component's run() raised, at
                    # backtest time (Q-1278). Same judgment row, same kwargs:
                    # the offending key is the one "extra" member.
                    emit(
                        self.issues,
                        _CODE["J-COMPOSER.key-member"],
                        location=loc,
                        composer=nstep.name,
                        param=role,
                        extra=[bname],
                        branches=sorted(fields),
                    )
                    continue
                v = self._field_rejects(actual, expected, nstep, loc)
                if v is not None:
                    self._emit_field_check(
                        "role", nstep, bname, actual, _fmt_expected(expected), loc, v, role=role
                    )
        else:  # homogeneous form: every field vs the one demand
            for bname, actual in fields.items():
                v = self._field_rejects(actual, ci, nstep, loc)
                if v is not None:
                    self._emit_field_check(
                        "homogeneous", nstep, bname, actual, _fmt_expected(ci), loc, v
                    )

    # ── Composition advisories at the J-COMPOSER position (Q-1694) ──────────

    def _composition_checks(
        self,
        fields: dict[str, FlowVal],
        nstep: ComponentRef,
        sig,
        loc: str | None,
        tail: list,
    ) -> None:
        """NORMALIZER_BEFORE_CONCAT + MASK_ON_DIRECTIONAL_SIGNAL (W4 §1.5/§1.7).

        Both are facts about the RECORD a composer receives, so they read the
        branch fields the J-COMPOSER row already has. Each fires at most once
        per composer (naming the first offending branch in declaration
        order): the mistake is the composition, not each branch, and N
        near-identical issues on one step is the noise the catalog's
        cascade-suppression discipline exists to avoid.
        """
        if not fields:
            return
        if nstep.name == _WEIGHT_CONCATENATOR and _stage_armed(_NORMALIZER_CONCAT_KEY):
            self._normalizer_before_concat(fields, nstep, loc, tail)
        if _domain_set(sig) == _BOOLEAN_DOMAIN:
            self._mask_on_directional(fields, nstep, loc)

    def _normalizer_before_concat(
        self, fields: dict[str, FlowVal], nstep: ComponentRef, loc: str | None, tail: list
    ) -> None:
        """Branches whose own leverage targets sum past a fully-invested book
        (W4 §1.5; agent-surface-cleanup spec 04 §2.6).

        The DISCRIMINATOR is the parameter, not the category: any component
        carrying ``target_leverage`` normalizes its own output to it, so the
        concatenated book's gross leverage is the SUM of the branch targets.
        That is a deliberate per-sleeve design when the targets split one
        book (0.5 + 0.5, the shipped long-hype-short-alts-index), and the
        mistake when they do not. The DSL declares no book target, so the
        verdict is the fully-invested book: the literal targets sum to MORE
        than 1.0 and nothing downstream re-levels the book (a later step
        carrying ``target_leverage`` or ``max_leverage`` — a normalizer or a
        LeverageCap on the concatenated weights). One normalizing branch is
        harmless (its target IS the book's), so the verdict needs at least
        two. A non-literal target (a VariableRef) means NO verdict: it can
        only ever suppress a fire.
        """
        offenders: list[tuple[str, str, object]] = []
        for bname, val in fields.items():
            ref = val.src_ref
            if not isinstance(ref, ComponentRef):
                continue
            psig = self.registry.get(ref.name)
            if psig is None or _LEVERAGE_TARGET_PARAM not in psig.parameters:
                continue
            offenders.append((bname, ref.name, _effective_param(ref, psig, _LEVERAGE_TARGET_PARAM)))
        if len(offenders) < 2:
            return
        targets = [t for _b, _p, t in offenders]
        if not all(_is_number(t) for t in targets):
            return  # a non-literal target: no verdict
        total = sum(float(t) for t in targets)
        if total <= 1.0 + _BOOK_SUM_TOLERANCE:
            return  # the branches split one fully-invested book
        if self._book_releveled_downstream(tail):
            return
        branch, producer, target = offenders[0]
        emit(
            self.issues,
            code="NORMALIZER_BEFORE_CONCAT",
            location=loc,
            branch=branch,
            producer=producer,
            target=_render_leverage(target),
            step=nstep.name,
            n=len(offenders),
            sum=_render_leverage(total),
        )

    def _book_releveled_downstream(self, tail: list) -> bool:
        """Does a step AFTER the concatenator set the book's leverage?

        Walks the downstream steps in order, transparent through nested
        Pipeline bodies and inlined variables (the pass-7d path rule
        FIXED_WEIGHT_UNCAPPED uses), and stops at the first Parallel: a later
        Parallel starts new branches, and nothing inside it re-levels this
        book. A step re-levels when its registry signature carries
        ``target_leverage`` or ``max_leverage`` with a value that is not
        None (a non-literal counts — it can only suppress).
        """
        stack: list[list] = [list(reversed(tail))]
        seen_vars: set[str] = set()
        while stack:
            pending = stack[-1]
            if not pending:
                stack.pop()
                continue
            st = pending.pop()
            if isinstance(st, ParallelSpec):
                return False
            if isinstance(st, PipelineSpec):
                stack.append(list(reversed(st.steps)))
                continue
            if isinstance(st, VariableRef):
                vp = self.variable_pipelines.get(st.name)
                if vp is not None and st.name not in seen_vars:
                    seen_vars.add(st.name)
                    stack.append(list(reversed(vp.steps)))
                continue
            if not isinstance(st, ComponentRef):
                continue
            sig = self.registry.get(st.name)
            if sig is None:
                continue
            for pname in _BOOK_LEVEL_PARAMS:
                if pname in sig.parameters and _effective_param(st, sig, pname) is not None:
                    return True
        return False

    def _mask_on_directional(
        self, fields: dict[str, FlowVal], nstep: ComponentRef, loc: str | None
    ) -> None:
        """A boolean combiner over a branch that can emit -1 (W4 §1.7).

        The combiner is identified by its DECLARED ``output_domain`` of
        Set{0, 1}; the offending branch by its producer's own declared set
        containing -1. ThresholdCross is read one step further because its
        declared domain is mode-independent: ``long_only`` emits {0, 1} and
        is excluded, which is what keeps the shipped library's long_only exit
        branch silent. An unresolvable ``mode`` (a VariableRef) is treated as
        "no verdict" — under-report, never false-positive.
        """
        for bname, val in fields.items():
            ref = val.src_ref
            if not isinstance(ref, ComponentRef):
                continue
            psig = self.registry.get(ref.name)
            dom = _domain_set(psig)
            if dom is None or -1.0 not in dom:
                continue
            if ref.name == _MODE_GATED_DIRECTIONAL:
                mode = _effective_param(ref, psig, "mode")
                if not isinstance(mode, str) or mode not in _MODES_EMITTING_SHORT:
                    continue
            emit(
                self.issues,
                code="MASK_ON_DIRECTIONAL_SIGNAL",
                location=loc,
                step=nstep.name,
                branch=bname,
                producer=ref.name,
            )
            return

    def _emit_field_check(
        self,
        shape: str,
        nstep: ComponentRef,
        bname: str,
        actual: FlowVal,
        expected_display: str,
        loc: str,
        v: R.Verdict,
        role: str | None = None,
    ) -> None:
        """J-COMPOSER.field-check emission — strings are A3 row template data."""
        data = {
            "composer": nstep.name,
            "branch": bname,
            "actual": actual.name,
            "expected_display": expected_display,
            "role": role,
        }
        emit(
            self.issues,
            _CODE["J-COMPOSER.field-check"],
            location=loc,
            suggestion=emit_template("composer-field-fix", shape).format(**data),
            detail=emit_template("composer-field-detail", shape).format(**data),
            row_staged_by=v.staged_by,
        )

    def _field_rejects(
        self, actual: FlowVal, expected, nstep: ComponentRef, loc: str
    ) -> R.Verdict | None:
        """The rejecting verdict when the caller's field-check row must emit."""
        demand = (
            R.Union(tuple(_demand_tok(x) for x in expected))
            if isinstance(expected, tuple)
            else _demand_tok(expected)
        )
        v = self._verdict(actual.tok, demand, "strict")
        fire = self._gate(
            v, step=nstep.name, location=loc, mode="strict", demand=demand, actual=actual
        )
        return v if fire else None


def verify_slots_structural(
    strategy: StrategyFile,
    registry,
    issues: list,
    slot_seed: dict | None = None,
    trace_sink=None,
) -> None:
    """Spec 03 §5 standalone journal-mode entry point (the ``verify_spec`` path).

    Runs the walk in structural-only capacity: Σ-lifecycle judgments
    (J-STORE/J-STOREVALUE/J-LOAD + J-SLOTREAD binding resolution) and the
    usage lint. No typing rows are evaluated — with the empty seed every
    store records the NoneType sentinel, reproducing today's standalone
    ``_validate_slots(expanded, registry, issues, {})`` exactly:
    SLOT_NOT_FOUND / SLOT_REF_NOT_FOUND still fire at every compile
    boundary while SLOT_TYPE_MISMATCH stays S7-suppressed.
    """
    walk = Walk(
        registry,
        profile=M2_COMPAT_PROFILE,
        trace_sink=trace_sink,
        structural=True,
        store_seed=slot_seed or {},
    )
    walk.run(strategy, issues, [], {})
    walk.flush(issues)


__all__ = [
    "END_STATE_PROFILE",
    "FlowVal",
    "M2_COMPAT_PROFILE",
    "Profile",
    "StagedOutcome",
    "Walk",
    "shipped_profile",
    "verify_slots_structural",
]
