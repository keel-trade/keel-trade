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
    """The shipped configuration (m2-compat at HEAD).

    Flow keys resolve from the live ``STAGED_CHANGES`` registry — the same
    source the A3 writer derives ``flow_config`` from (the freshness test
    keeps the generated block equal), so the test-only stage override
    reaches flow shapes too (01 S1-AC10 switchability, spec 05 §5.3).
    """
    flow = {k: str(STAGED_CHANGES[k].stage) for k in FLOW_CONFIG_KEYS}
    return Profile("shipped", flow, R.shipped_staged_active())


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
        return TypeRef(fv.name, tok.base, _domain_ref(tok.domain), R.tier_of(tok.domain))
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
    ) -> None:
        self.registry = registry
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
        self._root_len: int = 0  # outermost pipeline step count (R-17 insert)

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
        self.final = self._pipeline(strategy.pipeline, _NONE_VAL, "pipeline", "root")
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
                bname = step.params.get(role)
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

    def _entry(self, step: str, in_name: str, out_name: str, category: str) -> None:
        if not self.structural:
            self.type_flow.append(
                TypeFlowEntry(
                    step=step, input_type=in_name, output_type=out_name, category=category
                )
            )

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
            self._emit_domain(
                v, step, location, demand, actual, f" via slot '{slot}'" if slot else ""
            )
            return False
        return v.rejects

    def _emit_domain(self, v: R.Verdict, step, location, demand, actual, via) -> None:
        """Compose an ARMED value-domain emission (spec 05 §1.2's three codes)."""
        dom = actual.tok.domain if actual and isinstance(actual.tok, R.Pair) else R.TOP
        kwargs: dict = {
            "consumer": step,
            "expected_display": _display(demand),
            "expected_domain": _render_domain(_demand_domain(demand)),
            "via": via,
        }
        if v.code == "VALUE_DOMAIN_MISMATCH":
            kwargs["producer"] = (actual.src if actual else None) or "the previous step"
            kwargs["actual_domain"] = _render_domain(dom)
        elif v.code == "VALUE_BOUNDS_ADVISORY":
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
            v.code,
            location=location,
            expected=_tok_ref(demand),
            actual=env_actual,
            provenance=prov,
            **kwargs,
        )

    def _bridge(self, cur: FlowVal, expected_type) -> str:
        """`_suggest_type_bridge` re-based on strict-mode table queries (§4.1 #2)."""
        demand = _demand_tok(expected_type)
        names: list[str] = []
        for name, sig in self.registry.items():
            if self._verdict(cur.tok, _demand_tok(sig.input_type), "strict").rejects:
                continue
            if self._verdict(_decl_tok(sig.output_type), demand, "strict").rejects:
                continue
            names.append(name)
            if len(names) >= 3:
                break
        exp_name = type_name(expected_type)
        if names:
            return (
                f"Insert a component that transforms {cur.name} to {exp_name}. "
                f"Options: {', '.join(names)}"
            )
        return f"Expected input type {exp_name}, but previous step outputs {cur.name}."

    # ── the walk (J-PIPE left-fold, spec 01 §8.2) ────────────────────────────

    def _pipeline(self, pipe: PipelineSpec, cur: FlowVal, ctx: str, path: str) -> FlowVal:
        prev_par: FlowVal | None = None
        for i, step in enumerate(pipe.steps):
            spath = f"{path}/{i}"
            if isinstance(step, ComponentRef):
                cur, prev_par = self._component(step, cur, prev_par, ctx, i, spath)
            elif isinstance(step, SlotStoreSpec):
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
                self._entry(f'Load("{step.slot_name}")', cur.name, cur.name, "slot_op")
                self._trace(spath, "slot_load", None, None, cur)
            elif isinstance(step, SlotExtractSpec):
                if not self.structural:
                    cur = self._extract(step, cur, prev_par, spath)
            elif isinstance(step, ParallelSpec):
                cur = self._parallel(step, pipe, i, cur, ctx, spath)
                prev_par = cur
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
        if not self.structural:
            if self.profile.flow["composer-record-reach"] == "post" and isinstance(
                cur.tok, R.Record
            ):
                self._composer_checks(cur.fields or {}, step)
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
            if gated is None:
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
                                suggestion=self._bridge(cur, sig.input_type),
                                row_staged_by=v.staged_by,
                                context=f"{ctx}.step[{i}]",
                                step=step.name,
                                expected=_expected_ref(sig.input_type, sig.input_domain),
                                actual=_actual_ref(cur),
                                path=_path_of(spath),  # envelope machine path (§3.3)
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
            self._entry(
                f"{step.name}({_format_params_brief(step.params)})",
                cur.name,
                type_name(sig.output_type),
                cat,
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
            return FlowVal(_decl_tok(out_t), out_t, out_name, src=step.name)
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
                pair = T.norm(base, R.TOP)
                fv = FlowVal(pair, out_t, pair.base, src=step.name)
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
        transfer = self._resolve_refs(sig.domain_transfer or {"fn": "top"}, step, cur, doms)
        pair = T.norm(base, T.eval_transfer(transfer, doms, step.params))
        name = out_name if self.profile.flow["scheme-synthesis"] == "pre" else pair.base
        fv = FlowVal(pair, obj, name, src=step.name)
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

    def _resolve_refs(self, t: dict, step: ComponentRef, cur: FlowVal, out: dict) -> dict:
        """Pre-resolve transfer input refs to string keys + a domains map."""
        fn = t.get("fn") if isinstance(t, dict) else None
        if fn == "id":
            key, dom = self._ref_domain(t["input"], step, cur)
            out[key] = dom
            return {"fn": "id", "input": key}
        if fn == "union":
            return {
                "fn": "union",
                "args": [self._resolve_refs(a, step, cur, out) for a in t["args"]],
            }
        if fn == "join":
            refs = t["inputs"]
            keys = []
            for ref in list((cur.fields or {})) if refs == "all" else refs:
                key, dom = self._ref_domain(ref, step, cur)
                out[key] = dom
                keys.append(key)
            return {"fn": "join", "inputs": keys}
        return t

    def _ref_domain(self, ref, step: ComponentRef, cur: FlowVal):
        if ref == "input":
            return "input", (cur.tok.domain if isinstance(cur.tok, R.Pair) else R.TOP)
        if isinstance(ref, dict) and "field_by_param" in ref:
            key = step.params.get(ref["field_by_param"])
            fv = (cur.fields or {}).get(key) if isinstance(key, str) else None
            dom = fv.tok.domain if fv is not None and isinstance(fv.tok, R.Pair) else R.TOP
            return (key if isinstance(key, str) else "?unresolved"), dom
        if isinstance(ref, str):  # record-field spelling (join operands)
            fv = (cur.fields or {}).get(ref)
            return ref, (fv.tok.domain if fv and isinstance(fv.tok, R.Pair) else R.TOP)
        raise T.TransferError(f"Unresolvable transfer input ref: {ref!r}")

    # ── J-SLOTREAD (journaled — pass-8 position emission) ────────────────────

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
            demand = _demand_tok(expected)
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
        for bname, bsteps in step.branches.items():
            self.sigma = dict(base)  # branches type from the SNAPSHOT (§6.3 in)
            sub = PipelineSpec(steps=bsteps, name=bname, location=step.location)
            bpath = f"{spath}/{bname}"
            bval = self._pipeline(sub, cur, f"{ctx}.branch[{bname}]", bpath)
            # The §4.3 insert-at-end-of-producer target: this branch's node id
            # + the index of its LAST step (the projector goes after it).
            fields[bname] = _dc_replace(bval, producer=(bpath, len(bsteps) - 1)) if bsteps else bval
            deltas.append({k: v for k, v in self.sigma.items() if base.get(k) is not v})
        self.sigma = base
        for delta in deltas:  # new/changed entries, declaration order (§6.3 out)
            self.sigma.update(delta)
        rec = FlowVal(R.Record(tuple((k, f.tok) for k, f in fields.items())), dict, "dict", fields)
        if not self.structural:
            self._trace(spath, "parallel", None, None, rec)
            if self.profile.flow["composer-record-reach"] == "pre" and i + 1 < len(pipe.steps):
                nxt = pipe.steps[i + 1]
                if isinstance(nxt, ComponentRef):  # today's adjacency look-ahead
                    self._composer_checks(fields, nxt)
        return rec

    # ── J-COMPOSER key/field discipline (spec 01 §5.3) ───────────────────────

    def _composer_checks(self, fields: dict[str, FlowVal], nstep: ComponentRef) -> None:
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
        ci = getattr(sig, "composer_inputs", None)
        if ci is None or not fields:
            return
        if isinstance(ci, dict):
            if not ci:
                return  # explicit opt-out
            for role, expected in ci.items():
                bname = nstep.params.get(role)
                if bname is None or isinstance(bname, VariableRef) or not isinstance(bname, str):
                    continue  # auto-detect / runtime-resolved — declared skips
                actual = fields.get(bname)
                if actual is None:
                    continue  # COMPOSER_KEY_MISMATCH owns this
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
