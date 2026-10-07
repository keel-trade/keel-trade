"""The clock facet of the judgment-table walk (dsl-mtf-clocks spec 01 §§2-7).

M4b (T-M4b-1): CARRY/TRANSFORM/MATCH inference riding the pass-6 interpreter
walk. This module owns the pure clock calculus — the transfer algebra
(spec 01 §5.1's 4 ops), Theorem-1 arithmetic (§2.3), κ_exec (§6.4), the
side-condition failure table (§5.2 incl. the R-8 totality rows), and
rendering. The interpreter (``dsl/interpreter.py``) routes the produced fires
through the staged emit-vs-divert gate; severities are catalog data
(spec 05 §2.2), never code.

The spec 02 §3.4 dual-emitter suppression predicate lived here while the
historical pass-9 Globals-path block coexisted with these rows. It is GONE
(T-M4f-5, the clock-transform-rebase PROMOTED flip): the pass-9 emitter was
removed in the same commit, so these rows are the sole transform-site
emitter at every site and there is nothing left to deduplicate against.

Design rules carried from the specs:

- The clock is a pair of ints ``(period_minutes, phase_minutes)`` — tokens
  in generated data, never live type objects (spec 01 §2.2; R2-I9).
- The alphabet is ``TIMEFRAME_MINUTES`` and nothing else (§2.2 #1).
- The checker NEVER manufactures a clock: unresolvable/off-alphabet
  literals yield clock-less outputs and no raise (§2.2 #3 / §5.4, R-8).
- Clock INFERENCE is configuration-independent: dormancy stages the
  EMISSION only, so shipped and terminal-armed walks synthesize identical
  clocks (§6.6 recovery applies in both configurations — the single-fire
  property is structural, not stage-dependent).
- One fire per site: the first failing premise in check order wins and the
  §6.6 recovery clock applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pipeline_engine.validation_shared import (
    MINUTES_TO_TOKEN,
    TIMEFRAME_MINUTES,
    ClockRef,
    SuggestedEdit,
    ValidOption,
    parse_bar_offset_minutes,
    render_clock,
    synth_offset_consumed,
)


Clock = tuple[int, int]  # (period_minutes, phase_minutes)

#: The transform-set ops (spec 01 §5.1). ``keep`` is default-by-omission.
_OPS = ("synth", "coarsen", "project")


def token_for(minutes: int) -> str | None:
    """Canonical alphabet token for a period, or None off-alphabet."""
    return MINUTES_TO_TOKEN.get(minutes)


def _humanize_minutes(minutes: int) -> str:
    """Render a minute count: alphabet token if one exists, else Nmin/Nh."""
    tok = MINUTES_TO_TOKEN.get(minutes)
    if tok is not None:
        return tok
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}min"


def is_subclock(coarse: Clock, fine: Clock) -> bool:
    """Theorem 1 (spec 01 §2.3): ``coarse ⊑_clk fine`` ⟺ p_f | p_c ∧
    o_c ≡ o_f (mod p_f). Two integer ops, O(1)."""
    pf, of = fine
    pc, oc = coarse
    return pc % pf == 0 and (oc - of) % pf == 0


def kappa_exec(globals_) -> Clock | None:
    """κ_exec (spec 01 §6.4) — None when undefined or non-WF.

    Undefined: no Globals, no/off-alphabet ``target_timeframe``. Non-WF:
    ``bar_offset`` unparseable or ``off_min ≥ period`` (the existing
    Globals/offset rules own those errors; J-PIPE never computes with a
    non-WF κ_exec).
    """
    if globals_ is None or globals_.target_timeframe is None:
        return None
    period = TIMEFRAME_MINUTES.get(globals_.target_timeframe)
    if period is None:
        return None
    offset = 0
    if globals_.bar_offset is not None:
        try:
            offset = parse_bar_offset_minutes(globals_.bar_offset)
        except ValueError:
            return None
    if not 0 <= offset < period:
        return None
    return (period, offset)


@dataclass(frozen=True)
class FireEnvelope:
    """The typed-envelope payload of one clock fire (spec 02 §4.1/§4.3).

    Everything here is a pass-through to ``emit()``'s envelope kwargs;
    ``applicability_override`` implements §4.3's per-emission downgrade to
    ``has_placeholders`` (the rule-level value stays the declared default).
    """

    expected: object | None = None  # TypeRef
    actual: object | None = None  # TypeRef
    path: tuple[str, ...] = ()
    provenance: tuple = ()  # ProvenanceHop chain
    valid_options: tuple = ()  # ValidOption entries
    suggested_edit: SuggestedEdit | None = None
    applicability_override: str | None = None
    #: Site-specific suggestion text replacing the rule's rendered
    #: ``suggestion_template`` (the sanctioned ``emit(suggestion=...)`` escape
    #: hatch). Set ONLY on the K16 refusal arms, where the catalog template's
    #: "resampling … is not the fix here" is the exact sentence that is false.
    #: A fire that sets it must NOT pass the suggestion-only template params
    #: (``fix_component`` &c.) — ``emit`` requires an exact param cover.
    suggestion_override: str | None = None


@dataclass(frozen=True)
class ClockFire:
    """One clock-row outcome, pre-routing (emit vs divert is the walk's)."""

    row_id: str  # CLOCK_ROWS id
    code: str
    kwargs: dict  # exact template params for emit()
    row_staged_by: str | None  # clock-transform-rebase on re-based rows
    envelope: FireEnvelope = field(default_factory=FireEnvelope)


@dataclass(frozen=True)
class TransferResult:
    """Clock output + at most one fire (single-fire per site, §6.6).

    ``offset_consumed`` is the walk-level fact ``UNUSED_GLOBAL``'s
    ``bar_offset`` arm reads (new-data-loaders spec 05 §1b/§3): this
    transfer took ``Globals.bar_offset`` — a coarsen resolved it through the
    globals ref (whatever its verdict: the error IS the feedback), or a
    synth loader applied it in-loader / refused it at its own site. A
    reference alone no longer counts as a use.
    """

    clock: Clock | None
    origin: str | None
    fire: ClockFire | None = None
    offset_consumed: bool = False


def _resolve_literal(sig, params: dict, globals_, name: str) -> tuple[str | None, bool]:
    """Resolve a clock-bearing parameter to a literal string.

    Order: explicit literal param → declaration_refs/optional_declaration_refs
    (globals fields — resolved before typing by contract, spec 01 §4.2) →
    the registered string default. Returns ``(literal, via_globals_ref)``;
    ``(None, False)`` when unresolvable (VariableRef, absent required
    param, non-string) — the caller yields a clock-less output, never a
    manufactured clock.
    """
    value = params.get(name)
    if isinstance(value, str):
        return value, False
    if value is not None:
        return None, False  # non-literal (VariableRef etc.) — unresolvable
    ref = sig.declaration_refs.get(name) or sig.optional_declaration_refs.get(name)
    if ref == "globals.target_timeframe" and globals_ is not None:
        v = globals_.target_timeframe
        return (v, True) if isinstance(v, str) else (None, True)
    if ref == "globals.bar_offset" and globals_ is not None:
        v = globals_.bar_offset
        return (v, True) if isinstance(v, str) else (None, True)
    pinfo = sig.parameters.get(name)
    default = getattr(pinfo, "default", None)
    if isinstance(default, str):
        return default, False
    return None, False


def timeframe_unbound(sig, params: dict, globals_, name: str) -> bool:
    """Is a Globals-bound clock param bound to NOTHING (Q-1510)?

    True exactly when the runtime's D2 refusal fires
    (``loader_v3._served_timeframe``, ``flow/base.effective_timeframe``): the
    param is absent (present-but-non-``None`` — a literal, a VariableRef —
    is never unbound; R-8 owns the non-literal), its declaration ref is
    ``globals.target_timeframe``, no ``Globals`` supplies a string for it,
    and the RESOLVED signature carries no string default (v2's ``"15min"``
    keeps a locked strategy runnable, so it keeps it clean here too).
    """
    if params.get(name) is not None:
        return False
    ref = sig.declaration_refs.get(name) or sig.optional_declaration_refs.get(name)
    if ref != "globals.target_timeframe":
        return False
    if globals_ is not None and isinstance(globals_.target_timeframe, str):
        return False
    default = getattr(sig.parameters.get(name), "default", None)
    return not isinstance(default, str)


def _offset_minutes(off_literal: str | None) -> int | None:
    """Offset literal → minutes; 0 when absent; None when unparseable
    (the grammar codes own the error — no clock is computed from it)."""
    if off_literal is None:
        return 0
    try:
        return parse_bar_offset_minutes(off_literal)
    except ValueError:
        return None


def nearest_harmonic_coarsen(p_in: int, p_req: int) -> str | None:
    """R-17 per-direction fix arm at a resample site: the smallest alphabet
    period the source divides that is ≥ the requested target."""
    candidates = [m for m in MINUTES_TO_TOKEN if m % p_in == 0 and m >= p_req]
    return MINUTES_TO_TOKEN[min(candidates)] if candidates else None


def nearest_harmonic_project(p_in: int, p_req: int) -> str | None:
    """R-17 per-direction fix arm at a project site: the largest alphabet
    divisor of the source ≤ the requested target (never coarser than the
    source)."""
    candidates = [m for m in MINUTES_TO_TOKEN if p_in % m == 0 and m <= p_req]
    return MINUTES_TO_TOKEN[max(candidates)] if candidates else None


def _alternation(p_in: int, p_out: int) -> tuple[str, str]:
    """The §2.1.3 coarsen consequence: real-time coverage alternation of a
    non-harmonic target built from p_in bars (measured: 2h→3h ⇒ 2h/4h)."""
    rem = p_out % p_in
    low = p_out - rem
    high = p_out + (p_in - rem)
    return _humanize_minutes(low), _humanize_minutes(high)


def _split_path(step_path: str | None) -> tuple[str, ...]:
    """Trace node id → envelope machine-path segments (one addressing scheme)."""
    return tuple(step_path.split("/")) if step_path else ()


def _explicit_sibling(name: str) -> str:
    """The branch-local explicit alternative of a declaration-backed member
    (TargetTimeframeResampler → TimeframeResampler, etc. — spec 02 §2.1.3)."""
    return name[len("Target") :] if name.startswith("Target") else name


def _harmonic_envelope(
    op_word: str,
    clock_in: Clock,
    clock_out: Clock,
    sig,
    op: str,
    via_globals: bool,
    step_path: str | None,
) -> FireEnvelope:
    """CLOCK_NOT_HARMONIC's machine fix (spec 02 §2.1.3, R-17 per-direction).

    Explicit-param site: a local ``set_param`` on the offending step's own
    clock parameter — value per the per-direction formula (resample: the
    smallest alphabet period the source divides that is ≥ the request;
    project: the largest alphabet divisor of the source ≤ the request — a
    coarser-than-source projection target is never proposed). Globals-wired
    site (declaration-backed step, no authored clock param to set): the
    emitted issue downgrades to ``has_placeholders`` with BOTH arms in
    valid_options — the harmonic ``Globals(target_timeframe)`` value (a
    schedule-changing edit) and the branch-local explicit-component
    alternative.
    """
    p_in, _ = clock_in
    p_out, _ = clock_out
    options = lattice_options(p_in, p_out)
    if op_word == "resample":
        fix_value = nearest_harmonic_coarsen(p_in, p_out)
    else:
        fix_value = nearest_harmonic_project(p_in, p_out)
    fix_value = fix_value or _humanize_minutes(p_in)
    if via_globals:
        sibling = _explicit_sibling(sig.name if sig is not None else "")
        return FireEnvelope(
            path=_split_path(step_path),
            valid_options=(
                ValidOption(
                    "value",
                    fix_value,
                    f"set Globals(target_timeframe='{fix_value}') — the nearest "
                    f"harmonic value; a schedule-changing edit "
                    f"(target_timeframe is also the live tick cadence)",
                ),
                ValidOption(
                    "component",
                    sibling,
                    f"or replace this declaration-backed step with the explicit "
                    f"branch-local {sibling}(target_timeframe='{fix_value}')",
                ),
                *options,
            ),
            suggested_edit=None,
            applicability_override="has_placeholders",
        )
    return FireEnvelope(
        path=_split_path(step_path),
        valid_options=options,
        suggested_edit=SuggestedEdit(
            "set_param",
            _split_path(step_path),
            {"param": _src_param(sig, op), "value": fix_value},
        ),
    )


def evaluate_transfer(
    op: str,
    *,
    step_name: str,
    sig,
    params: dict,
    globals_,
    clock_in: Clock | None,
    origin_in: str | None,
    input_is_first_step: bool,
    input_base: str | None,
    step_path: str | None = None,
) -> TransferResult:
    """Evaluate one clock transfer (spec 01 §5.1/§5.2 + §5.4 totality).

    ``clock_in is None`` means the input is GROUND CLOCK-LESS (Any and
    record inputs never reach here — the caller skips per §5.4). The output
    clock is not yet gated on ``clocked(B_out)`` — the caller drops it when
    the output base carries no clock (§4.1).
    """
    if op not in _OPS:
        raise ValueError(f"unknown clock-transfer op {op!r}")

    if op == "synth":
        return _evaluate_synth(step_name, sig, params, globals_)

    literal, via_globals = _resolve_literal(sig, params, globals_, _src_param(sig, op))
    if literal is None:
        return TransferResult(None, None)
    p_out = TIMEFRAME_MINUTES.get(literal)
    if p_out is None:  # off-alphabet literal — clock-less output (R-8)
        return TransferResult(None, None)

    if op == "coarsen":
        off_literal, off_via_globals = _resolve_literal(sig, params, globals_, "bar_offset")
        off_min = _offset_minutes(off_literal)
        # The coarsen TOOK the global offset whatever it does with it: a
        # resolved globals ref here is a use of Globals.bar_offset even on
        # the error arms and the unparseable arm (the error is the feedback),
        # which is what the historical "any component references it" arm
        # counted too.
        consumed = off_via_globals and off_literal is not None
        if off_min is None:  # unparseable offset: grammar codes own it
            return TransferResult(None, None, None, consumed)
        declared = (p_out, off_min % p_out)
        if clock_in is None:
            # §5.4 totality: side conditions SKIPPED, declared target
            # synthesized, no emission (clock-less-ness is downstream of an
            # already-emitted error or an Any frontier).
            return TransferResult(declared, step_name, None, consumed)
        p_in, o_in = clock_in
        common = {"row_staged_by": "clock-transform-rebase"}
        src_tok = _humanize_minutes(p_in)
        off_disp = off_literal if off_literal is not None else f"{off_min}min"
        # Check order: the historical dispatch's arms first (direction,
        # same-TF, multiple, too-large), with the two new premises (harmonic;
        # general phase form) interleaved at their §5.2 positions.
        if p_out < p_in:
            # K15 realigned template (spec 02 §2.2, landed at the
            # clock-transform-rebase flip): the repair names the projector
            # that carries a coarse value onto the finer declared clock —
            # the globals-wired variant when the declared target IS κ_exec.
            fire = ClockFire(
                "J-COARSEN.direction",
                "UPSAMPLE_NOT_SUPPORTED",
                {
                    "source_tf": src_tok,
                    "target_tf": literal,
                    "fix_component": fix_component_for(declared, kappa_exec(globals_)),
                },
                **common,
            )
            return TransferResult(declared, step_name, fire, consumed)
        if p_out == p_in:
            if off_min != o_in:
                fire = ClockFire(
                    "J-COARSEN.offset-same-tf",
                    "BAR_OFFSET_AT_SAME_TF",
                    {"bar_offset": off_disp, "source_tf": src_tok},
                    **common,
                )
                return TransferResult(declared, step_name, fire, consumed)
            fire = ClockFire(
                "J-TRANSFORM.noop",
                "RESAMPLER_NOOP",
                noop_kwargs(step_name, literal, clock_in, origin_in),
                **common,
            )
            return TransferResult(clock_in, origin_in, fire, consumed)
        if p_out % p_in != 0:
            fire = ClockFire(
                "J-COARSEN.harmonic",
                "CLOCK_NOT_HARMONIC",
                _harmonic_kwargs("resample", clock_in, (p_out, off_min), sig, op),
                row_staged_by=None,
                envelope=_harmonic_envelope(
                    "resample", clock_in, declared, sig, op, via_globals, step_path
                ),
            )
            return TransferResult(declared, step_name, fire, consumed)
        if (off_min - o_in) % p_in != 0:
            # General Theorem-1 phase form (R-7): o_in-relative, never
            # assumed 0 — the chained-offset shape is LEGAL here while
            # today's runtime per-hop rule still rejects it (intended
            # verdict change, enumerated at the transform-rebase flip).
            fire = ClockFire(
                "J-COARSEN.offset-multiple",
                "BAR_OFFSET_NOT_MULTIPLE",
                {"bar_offset": off_disp, "source_tf": src_tok},
                **common,
            )
            return TransferResult(declared, step_name, fire, consumed)
        if off_min >= p_out:
            fire = ClockFire(
                "J-COARSEN.offset-too-large",
                "BAR_OFFSET_TOO_LARGE",
                {"bar_offset": off_disp, "target_tf": literal},
                **common,
            )
            return TransferResult(declared, step_name, fire, consumed)
        return TransferResult((p_out, off_min), step_name, None, consumed)

    # op == "project"
    if clock_in is None:
        if input_is_first_step:
            desc = "it is the first step"
        elif input_base == "record":
            desc = "a record"
        else:
            desc = f"a {input_base or 'clock-less'} value"
        fire = ClockFire(
            "J-PROJECT.input-unclocked",
            "PROJECT_INPUT_UNCLOCKED",
            {"step": step_name, "input_desc": desc},
            row_staged_by=None,
        )
        # §6.6: τ_out unchanged — no clock is manufactured.
        return TransferResult(None, None, fire)
    p_in, o_in = clock_in
    declared = (p_out, o_in % p_out)
    if p_out > p_in:
        fire = ClockFire(
            "J-PROJECT.direction",
            "PROJECT_WRONG_DIRECTION",
            {
                "step": step_name,
                "source_clock": render_clock(clock_in),
                "target_clock": render_clock((p_out, o_in % p_out)),
            },
            row_staged_by=None,
        )
        return TransferResult(declared, step_name, fire)
    if p_out == p_in:
        # κ_out = (p, o_in mod p) = κ_in — the noop lint row.
        fire = ClockFire(
            "J-TRANSFORM.noop",
            "RESAMPLER_NOOP",
            noop_kwargs(step_name, literal, clock_in, origin_in),
            row_staged_by="clock-transform-rebase",
        )
        return TransferResult(clock_in, origin_in, fire)
    if p_in % p_out != 0:
        fire = ClockFire(
            "J-PROJECT.harmonic",
            "CLOCK_NOT_HARMONIC",
            _harmonic_kwargs("project", clock_in, (p_out, o_in % p_out), sig, op),
            row_staged_by=None,
            envelope=_harmonic_envelope(
                "project", clock_in, declared, sig, op, via_globals, step_path
            ),
        )
        return TransferResult(declared, step_name, fire)
    return TransferResult((p_out, o_in % p_out), step_name)


def noop_kwargs(step_name: str, literal: str, clock_in: Clock, origin_in: str | None) -> dict:
    """RESAMPLER_NOOP's template params (spec 05 §3 / Q-1497): the message
    names the STEP that is redundant and the ORIGIN that already serves the
    clock — with its phase, so a bound loader under an offset global reads
    as "already serves 1d@12h" — and no longer suggests dropping Globals
    (the loader follows Globals; the declaration is load-bearing)."""
    return {
        "step": step_name,
        "target_tf": literal,
        "source_clock": render_clock(clock_in),
        "origin_step": origin_in or "the previous step",
    }


def _evaluate_synth(step_name: str, sig, params: dict, globals_) -> TransferResult:
    """The synth op (spec 01 §5.1) with new-data-loaders spec 05 §3's offset arm.

    A clock-SOURCE loader's output clock is ``(served period, phase)`` where
    the phase is ``Globals.bar_offset`` iff the loader CONSUMES it — the
    shared rule ``validation_shared.synth_offset_consumed`` over the served
    period, the offset and the transfer's declared ``grain`` (roll source).
    Otherwise phase 0, exactly as before: an identity serving leaves the
    offset to a downstream coarsen (today's ``PriceDataLoader(timeframe=
    "15min") → TargetTimeframeResampler()`` shape stays byte-identical).

    Check order, single fire (§6.6):

    1. src UNBOUND (Q-1510, spec 05 D2's write-time half: no literal, a
       ``globals.target_timeframe`` ref with no such Globals, no string
       default on the RESOLVED signature) ⇒ ``LOADER_TIMEFRAME_UNBOUND``
       in the runtime's own sentence, clock-less; any other unresolvable
       src (a VariableRef, a non-string) or an off-alphabet token ⇒
       clock-less, no raise (R-8) — its own codes own it;
    2. ``floor`` declared and served finer than it ⇒
       ``LOADER_FINER_THAN_NATIVE`` (the funding family's refusal, spec 05
       §2b: set ``timeframe=`` to the native grain and project at the end
       of the branch); recovery = the declared served clock;
    3. ``off`` declared and resolved, with a ``grain``:
       - served == grain, the loader BOUND to Globals, and an offset finer
         than a served bar ⇒ ``BAR_OFFSET_AT_SAME_TF`` (the declared target
         IS the loader's own grain, and no coarsen downstream could ever
         take a sub-bar offset); recovery = the declared ``(period, offset)``.
         An explicit literal at the grain stays phase 0 — today's shape, the
         downstream coarsen owns the verdict;
       - served > grain, offset < served, not a multiple of the grain ⇒
         ``BAR_OFFSET_NOT_MULTIPLE`` (the roll the loader would attempt is
         the one ``validate_resample_config`` refuses at run time);
    4. consumed ⇒ ``(period, offset)``, ``offset_consumed``; else
       ``(period, 0)``.

    A fire on the offset arms also reports the offset as consumed: the
    error is the feedback, and a second ``UNUSED_GLOBAL`` beside it would
    point the author at the wrong fix (removing the declaration).
    """
    spec = getattr(sig, "clock_transfer", None) or {}
    src = _src_param(sig, "synth")
    literal, src_via_globals = _resolve_literal(sig, params, globals_, src)
    if literal is None:
        if timeframe_unbound(sig, params, globals_, src):
            fire = ClockFire(
                "J-SYNTH.unbound",
                "LOADER_TIMEFRAME_UNBOUND",
                {"loader": step_name, "src": src},
                row_staged_by=None,
                envelope=FireEnvelope(applicability_override="has_placeholders"),
            )
            return TransferResult(None, None, fire)
        return TransferResult(None, None)
    period = TIMEFRAME_MINUTES.get(literal)
    if period is None:  # off-alphabet: clock-less, DEFINED, no raise (R-8)
        return TransferResult(None, None)

    floor_tok = spec.get("floor")
    floor = TIMEFRAME_MINUTES.get(floor_tok) if floor_tok else None
    if floor is not None and period < floor:
        declared_desc = (
            f"Globals(target_timeframe='{literal}')"
            if src_via_globals
            else f"{_src_param(sig, 'synth')}='{literal}'"
        )
        fire = ClockFire(
            "J-SYNTH.floor",
            "LOADER_FINER_THAN_NATIVE",
            {
                "loader": step_name,
                "native_tf": floor_tok,
                "target_tf": literal,
                "target_desc": declared_desc,
                "fix_component": fix_component_for((period, 0), kappa_exec(globals_)),
            },
            row_staged_by=None,
            envelope=FireEnvelope(
                valid_options=(
                    ValidOption(
                        "value",
                        floor_tok,
                        f"set {_src_param(sig, 'synth')}='{floor_tok}' on {step_name} — "
                        f"its native grain; the branch then carries {floor_tok} data",
                    ),
                    ValidOption(
                        "component",
                        fix_component_for((period, 0), kappa_exec(globals_)),
                        f"and end the branch with it: the last COMPLETED {floor_tok} "
                        f"bar is held on the {literal} grid",
                    ),
                ),
                applicability_override="has_placeholders",
            ),
        )
        return TransferResult((period, 0), step_name, fire)

    off_param = spec.get("off")
    if not off_param:
        return TransferResult((period, 0), step_name)
    off_literal, off_via_globals = _resolve_literal(sig, params, globals_, off_param)
    if off_literal is None:
        return TransferResult((period, 0), step_name)
    off_min = _offset_minutes(off_literal)
    if off_min is None:
        # Unparseable: the grammar codes own it (INVALID_GLOBAL /
        # INVALID_BAR_OFFSET); phase 0, and the declaration counts as taken
        # so no UNUSED_GLOBAL is stacked on the grammar error.
        return TransferResult((period, 0), step_name, None, off_via_globals)

    grain_tok = spec.get("grain")
    grain = TIMEFRAME_MINUTES.get(grain_tok) if grain_tok else None
    if grain is not None and off_min < period:
        if period == grain and src_via_globals:
            # The loader BOUND to Globals serves the declared target at its
            # own grain, so "target_timeframe equals the loader's timeframe"
            # is literally true and no downstream coarsen can take a sub-bar
            # offset. An EXPLICIT literal equal to the grain is today's shape
            # (the target lies elsewhere): phase 0, and the downstream
            # coarsen owns the verdict exactly as before v3.
            fire = ClockFire(
                "J-SYNTH.offset-same-tf",
                "BAR_OFFSET_AT_SAME_TF",
                {"bar_offset": off_literal, "source_tf": grain_tok},
                row_staged_by=None,
            )
            return TransferResult((period, off_min), step_name, fire, off_via_globals)
        if period == grain:
            return TransferResult((period, 0), step_name)
        if period > grain and off_min % grain != 0:
            fire = ClockFire(
                "J-SYNTH.offset-multiple",
                "BAR_OFFSET_NOT_MULTIPLE",
                {"bar_offset": off_literal, "source_tf": grain_tok},
                row_staged_by=None,
            )
            return TransferResult((period, off_min), step_name, fire, off_via_globals)

    if synth_offset_consumed(period, off_min, grain):
        return TransferResult((period, off_min), step_name, None, off_via_globals)
    return TransferResult((period, 0), step_name)


def _src_param(sig, op: str) -> str:
    """The transfer's src parameter name from the registration surface.

    M4c re-source (spec 02 §8.1): the signature's own ``clock_transfer``
    declaration carries the name — validated at registration, so a member
    that reaches a transform evaluation always has one.
    """
    spec = getattr(sig, "clock_transfer", None)
    if spec is not None:
        return spec["src"]
    return "target_timeframe"


def _harmonic_kwargs(op_word: str, clock_in: Clock, clock_out: Clock, sig, op: str) -> dict:
    """CLOCK_NOT_HARMONIC's eight template params (spec 02 §2.1.3)."""
    p_in, _ = clock_in
    p_out, _ = clock_out
    fine, coarse = (p_in, p_out) if p_in < p_out else (p_out, p_in)
    if op_word == "resample":
        alt_low, alt_high = _alternation(p_in, p_out)
        consequence = (
            f"The produced bars would alternate between {alt_low} and "
            f"{alt_high} of real time — the label would lie about its "
            f"content, and chained re-clocking would disagree with direct."
        )
        fix_value = nearest_harmonic_coarsen(p_in, p_out)
    else:
        consequence = (
            "The coarse labels would not land on the fine grid — values "
            "would appear between bars instead of at them."
        )
        fix_value = nearest_harmonic_project(p_in, p_out)
    param = _src_param(sig, op)
    return {
        "op_word": op_word,
        "source_clock": render_clock(clock_in),
        "target_clock": render_clock(clock_out),
        "fine_tf": _humanize_minutes(fine),
        "coarse_tf": _humanize_minutes(coarse),
        "consequence": consequence,
        "param": param,
        "fix_value": fix_value or _humanize_minutes(p_in),
    }


# ── J-CLKUNIFORM / slot / terminal kwargs builders (spec 02 §2.1.1/§2.1.2) ──


def fix_component_parts(repair_clock: Clock, kexec: Clock | None) -> tuple[str, dict]:
    """K15 fix component as ``(name, params)`` — the machine-edit payload
    halves (spec 02 §4.3): the globals-wired variant (no params) when the
    repair clock IS the declared execution clock; the explicit mid-cascade
    form (a target_timeframe literal) otherwise."""
    if kexec is not None and repair_clock == kexec:
        return "TargetSignalProjector", {}
    tok = MINUTES_TO_TOKEN.get(repair_clock[0], f"{repair_clock[0]}min")
    return "SignalProjector", {"target_timeframe": tok}


def fix_component_for(repair_clock: Clock, kexec: Clock | None) -> str:
    """K15 default selection (spec 02 §4.3 top arm): the globals-wired
    variant when the repair clock IS the declared execution clock; the
    explicit mid-cascade form otherwise."""
    name, params = fix_component_parts(repair_clock, kexec)
    if not params:
        return f"{name}()"
    return f"{name}(target_timeframe='{params['target_timeframe']}')"


def clock_ref(clock: Clock, origin: tuple[str, ...] = ()) -> ClockRef:
    """Wire ClockRef for a clock (spec 01 §10): canonical token + both
    integer halves + the origin path in the existing addressing scheme."""
    p, o = clock
    return ClockRef(MINUTES_TO_TOKEN.get(p, f"{p}min"), p, o, origin)


@dataclass(frozen=True)
class UniformRepair:
    """The §4.3 repair-target selection at a consumer (all four R-17 arms)."""

    arm: str  # "fine" | "exec" | "none"
    fine_field: str
    fine_clock: Clock
    coarse_field: str  # the COARSEST offending clock's field
    coarse_clock: Clock
    repair_clock: Clock | None  # None on the ELSE arm
    fix_component: str  # display form for {fix_component}
    #: The K15 opposite-branch/opposite-operation shape: the bound fields whose
    #: clock is FINER than κ_exec (strictly, and harmonically — κ_exec ⊑_clk c),
    #: populated ONLY when EVERY off-κ_exec field is of that shape, i.e. when
    #: "resample the raw data onto the declared clock" is the whole repair.
    #: Empty on every other arm; the ELSE guidance falls back to the lattice.
    resample_fields: tuple[tuple[str, Clock], ...] = ()


def repair_lands_on_kexec(repair: Clock | None, kexec: Clock | None) -> bool:
    """K16's machine-edit post-condition (dsl-mtf-clocks GOAL D2; spec 02 §4.3).

    A ``machine_applicable`` CLOCK_MISMATCH edit inserts ONE projector, which
    re-clocks the repaired site — and everything keeping downstream of it — onto
    ``repair``. The edit is therefore honest ONLY when the run that applies it
    lands the pipeline's terminal on the DECLARED execution clock:

    - ``repair == kexec`` — the terminal premise (J-PIPE, §6.4) is satisfied by
      construction, and the R-2 recovery the checker already applied at this
      site synthesized exactly ``kexec``, so every downstream verdict in the
      SAME run was computed against the post-edit clock.
    - ``kexec is None`` — κ_exec is undefined/non-WF, J-PIPE skips (R-8), and
      the Globals rules own that error. There is no declared clock for the
      repair to miss.

    Anything else walks the pipeline OFF the declaration and cannot return:
    projection is coarse → fine only, so a terminal driven finer than κ_exec
    has no projector repair at all. Refusing beats a confidently-wrong edit
    (K16) — the caller downgrades to ``has_placeholders`` and says the true
    thing instead.
    """
    if repair is None:
        return False
    return kexec is None or repair == kexec


def select_uniform_repair(ground: list[tuple[str, Clock]], kexec: Clock | None) -> UniformRepair:
    """Spec 02 §4.3's repair-target selection at a ``cmix`` consumer.

    ``ground`` is the bound fields carrying clocks, in record field order.
    κ_fine = smallest period (tie → phase of κ_exec preferred, then first in
    record field order — ``min``/``max`` return the first extremal element).

    Arms, in preference order:

    1. **κ_exec (R-17 middle arm), whenever it is reachable** — κ_exec defined
       and every bound clock ⊑_clk κ_exec ⇒ repair = κ_exec. This is the
       target R-17 always intended: it is legal (each offender projects onto
       it) AND terminal-correct. One edit per round; the R7 §4.2 loop
       converges. Preferring it over the meet is not a heuristic — whenever
       the meet arm holds with κ_fine = κ_exec, this arm holds too, so the
       meet can only win when it would land somewhere OTHER than the
       declaration.
    2. **κ_fine (the meet)** — only when κ_exec is UNDEFINED, where there is no
       declaration to miss. With κ_exec defined, arm 1 already covers every
       meet that is terminal-correct, and the rest are the K16 divergence: the
       meet is finer than the declaration, the repair walks the whole pipeline
       off κ_exec, and projection (coarse → fine only) cannot bring it back.
    3. **ELSE** — no single projector insertion can legalize the record.
       ``resample_fields`` names the offending branches when the reason is
       "finer than κ_exec" and aggregation onto the declaration is the real
       repair (K15's opposite branch, opposite operation).

    Every arm that returns a ``repair_clock`` passes ``repair_lands_on_kexec``
    — the post-condition is re-checked as a gate below, so no future arm can
    reintroduce a machine edit that misses the declaration.
    """

    def _fine_key(item: tuple[str, Clock]):
        _, (p, o) = item
        phase_pref = 0 if (kexec is not None and o == kexec[1]) else 1
        return (p, phase_pref)

    fine_field, fine_clock = min(ground, key=_fine_key)
    offenders = [(k, c) for k, c in ground if c != fine_clock]
    coarse_field, coarse_clock = max(offenders, key=lambda item: item[1][0])

    # The fields no projector can bring onto the declaration, and — when ALL of
    # them are the finer-than-κ_exec shape (κ_exec ⊑_clk c, the exact dual of
    # the projection test) — the branches whose RAW data must be resampled onto
    # κ_exec instead. A partial set is not actionable guidance: re-clocking one
    # branch while another stays non-harmonic legalizes nothing, so the ELSE
    # arm keeps the lattice answer there.
    off_exec: list[tuple[str, Clock]] = []
    resample_fields: tuple[tuple[str, Clock], ...] = ()
    if kexec is not None:
        off_exec = [(k, c) for k, c in ground if not is_subclock(c, kexec)]
        if off_exec and all(is_subclock(kexec, c) for _, c in off_exec):
            resample_fields = tuple(off_exec)

    if kexec is not None and not off_exec:
        arm, repair = "exec", kexec
    elif kexec is None and all(is_subclock(c, fine_clock) for _, c in offenders):
        arm, repair = "fine", fine_clock
    else:
        arm, repair = "none", None

    # K16 post-condition — the single gate every machine edit passes through.
    if repair is not None and not repair_lands_on_kexec(repair, kexec):
        arm, repair = "none", None

    return UniformRepair(
        arm,
        fine_field,
        fine_clock,
        coarse_field,
        coarse_clock,
        repair,
        # On the ELSE arm the component is named in prose only; no edit attaches.
        fix_component_for(repair if repair is not None else fine_clock, kexec),
        resample_fields,
    )


def lattice_options(p_a: int, p_b: int) -> tuple[ValidOption, ...]:
    """§2.1.3's computed, never-empty lattice guidance (R-19 sanction):
    the gcd entry (the coarsest source that tiles the target) and the lcm
    entry (the finest target the source tiles). ``(P, |)`` is gcd/lcm-closed
    (spec 01 §2.4), so both tokens exist for every alphabet pair."""
    g = math.gcd(p_a, p_b)
    lcm = (p_a * p_b) // g
    fine_tok = _humanize_minutes(min(p_a, p_b))
    coarse_tok = _humanize_minutes(max(p_a, p_b))
    return (
        ValidOption(
            "value",
            MINUTES_TO_TOKEN.get(g, f"{g}min"),
            f"the coarsest timeframe that tiles both {fine_tok} and {coarse_tok} evenly",
        ),
        ValidOption(
            "value",
            MINUTES_TO_TOKEN.get(lcm, f"{lcm}min"),
            f"the finest timeframe both {fine_tok} and {coarse_tok} tile evenly",
        ),
    )


#: The two globals-wired members that aggregate raw data onto the declared
#: clock — named in prose, never auto-inserted: the correct insertion point is
#: BEFORE the branch's indicator (the computed value's own type usually rejects
#: a resampler where the mismatch surfaces), and the checker cannot prove which
#: member fits without walking the branch back to its loader.
_RAW_RESAMPLERS = "TargetTimeframeResampler for bar data, TargetSignalResampler for a signal series"


def _field_list(fields: tuple[tuple[str, Clock], ...]) -> str:
    """``'a' (1h)`` / ``'a' (1h) and 'b' (4h)`` / Oxford-comma'd beyond two."""
    parts = [f"'{k}' ({render_clock(c)})" for k, c in fields]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def resample_up_options(
    fields: tuple[tuple[str, Clock], ...], kexec: Clock
) -> tuple[ValidOption, ...]:
    """The K15 opposite-operation arm of §4.3's ELSE guidance (R-19: computed,
    never empty).

    Fires when the branches that break the record are FINER than κ_exec. The
    lattice pair is actively misleading here — its gcd entry names the finest
    clock in play, which is the clock a projector repair would (wrongly) drive
    the pipeline onto. The one true answer is the declared clock, reached by
    aggregation on the raw data, so that is what the option carries.
    """
    tok = MINUTES_TO_TOKEN.get(kexec[0], f"{kexec[0]}min")
    return (
        ValidOption(
            "value",
            tok,
            f"{_field_list(fields)} runs FINER than the declared "
            f"{render_clock(kexec)} execution clock — resample the RAW data "
            f"feeding it onto {render_clock(kexec)} BEFORE the indicator "
            f"({_RAW_RESAMPLERS}), then compute. Aggregation is fine → coarse; "
            f"projection is coarse → fine, so no projector inserted here "
            f"reaches the declared clock",
        ),
    )


def resample_up_suggestion(fields: tuple[tuple[str, Clock], ...], kexec: Clock) -> str:
    """The site-specific CLOCK_MISMATCH suggestion for the same arm.

    Replaces the rule's rendered ``suggestion_template``, whose closing clause
    ("resampling is the opposite direction … and is not the fix here") is
    exactly the sentence that is false when the offending branch is finer than
    the declaration: resampling IS the fix, on the OTHER branch.
    """
    return (
        f"{_field_list(fields)} runs FINER than the declared "
        f"{render_clock(kexec)} execution clock, so no projector inserted at "
        f"this step can legalize the record — projection goes coarse → fine "
        f"only. Resample the RAW data feeding it onto {render_clock(kexec)} "
        f"BEFORE the indicator ({_RAW_RESAMPLERS}), then compute; the branch "
        f"then arrives on the declared clock and the record agrees. Do not "
        f"aggregate the computed signal."
    )


def terminal_resample_up_options(kterm: Clock, kexec: Clock) -> tuple[ValidOption, ...]:
    """TERMINAL_CLOCK_MISMATCH's guidance when κ_term is FINER than κ_exec.

    ``terminal_valid_options``' insert-projector arm is not legal here (1h → 1d
    is aggregation, which the projector refuses with PROJECT_WRONG_DIRECTION),
    so the menu carries only the repairs that exist.
    """
    tok = MINUTES_TO_TOKEN.get(kexec[0], f"{kexec[0]}min")
    return (
        ValidOption(
            "value",
            tok,
            f"the weights are computed on {render_clock(kterm)}, FINER than "
            f"the declared {render_clock(kexec)} execution clock — resample "
            f"the RAW data onto {render_clock(kexec)} BEFORE the indicators "
            f"that produce them ({_RAW_RESAMPLERS}), then compute",
        ),
    )


def terminal_resample_up_suggestion(kterm: Clock, kexec: Clock) -> str:
    """The matching site-specific suggestion (the template's insert-projector
    sentence is false when κ_term is finer than κ_exec)."""
    return (
        f"This pipeline computes weights on {render_clock(kterm)}, FINER than "
        f"the declared {render_clock(kexec)} execution clock. Appending "
        f"TargetSignalProjector() cannot fix that — projection goes coarse → "
        f"fine only, and {render_clock(kterm)} → {render_clock(kexec)} is "
        f"aggregation. Resample the RAW data onto {render_clock(kexec)} BEFORE "
        f"the indicators that produce these weights ({_RAW_RESAMPLERS}), then "
        f"compute."
    )


def machine_arm_suggestion(rep: UniformRepair, kexec: Clock | None) -> str | None:
    """Suggestion override for a machine-applicable repair, or None to keep the
    catalog template.

    The template reads "Project the {actual_clock} value onto {expected_clock}"
    — and ``{expected_clock}`` is the OTHER side of the mismatch, which is the
    repair target only when κ_fine IS the repair clock (the §4.4 exemplar's
    shape, kept byte-for-byte). On the κ_exec arm with κ_exec strictly finer
    than κ_fine, that sentence would name a clock the inserted projector does
    not land on, and would not say that the second side still needs its own
    round. Both are said here instead.
    """
    if rep.repair_clock is None or kexec is None or rep.repair_clock == rep.fine_clock:
        return None
    return (
        f"Project the {render_clock(rep.coarse_clock)} '{rep.coarse_field}' value onto "
        f"the declared {render_clock(kexec)} execution clock: insert "
        f"{rep.fix_component} at the end of '{rep.coarse_field}'. Projection holds "
        f"the last COMPLETED {render_clock(rep.coarse_clock)} bar on the finer grid; "
        f"resampling is the opposite direction (fine → coarse) and is not the fix "
        f"here. '{rep.fine_field}' is on {render_clock(rep.fine_clock)}, also not the "
        f"declared clock — one insertion per round; re-validate and repeat."
    )


def else_arm_guidance(
    rep: UniformRepair, kexec: Clock | None
) -> tuple[tuple[ValidOption, ...], str | None]:
    """§4.3's ELSE-arm payload: ``(valid_options, suggestion_override)``.

    Three shapes, in order: (1) K15's finer-than-κ_exec branches — the
    aggregation answer, with the template's projection prose replaced; (2) the
    phase-only degenerate case (gcd = lcm) — the O4 bar_offset inheritance ref
    (R02-MIN-04); (3) everything else — the computed gcd/lcm lattice pair.
    """
    if rep.resample_fields and kexec is not None:
        return (
            resample_up_options(rep.resample_fields, kexec),
            resample_up_suggestion(rep.resample_fields, kexec),
        )
    if rep.coarse_clock[0] == rep.fine_clock[0]:
        return phase_inheritance_options(rep.coarse_clock[0]), None
    return lattice_options(rep.coarse_clock[0], rep.fine_clock[0]), None


def phase_inheritance_options(period: int) -> tuple[ValidOption, ...]:
    """The §4.3 ELSE phase-only arm (R02-MIN-04): gcd = lcm = the shared
    period (degenerate), so valid_options instead names the missing
    bar_offset inheritance (spec 01 §8.3 O4's declaration ref)."""
    tf = _humanize_minutes(period)
    return (
        ValidOption(
            "value",
            "globals.bar_offset",
            f"both sides run at {tf} with different phases — align their "
            f"offsets through the shared Globals(bar_offset) declaration "
            f"(the O4 inheritance ref) instead of a per-site literal",
        ),
    )


def mismatch_fix_option(
    fix_name: str,
    actual_clock: Clock,
    actual_field: str,
    repair_clock: Clock,
    *,
    declared: bool,
) -> ValidOption:
    """The §4.4 valid_options component entry for a machine-applicable
    CLOCK_MISMATCH repair.

    ``declared`` selects the exemplar phrasing: True when the repair clock IS
    κ_exec (the §4.4 exhibit's wording, byte-for-byte), False on the K15
    clause-3 arm where an explicit mid-cascade clock is the repair target and
    calling it "the declared execution clock" would be untrue.
    """
    a = render_clock(actual_clock)
    r = render_clock(repair_clock)
    target = f"the declared {r} execution clock" if declared else f"the {r} clock it meets"
    return ValidOption(
        "component",
        fix_name,
        f"projects the {a} {actual_field} signal onto {target} "
        f"(forward-fill of the last COMPLETED {a} bar)",
    )


def terminal_valid_options(kterm: Clock, kexec: Clock) -> tuple[ValidOption, ...]:
    """§4.3's TERMINAL_CLOCK_MISMATCH repair menu — both legal repairs:
    insert-projector (primary) and re-declare Globals (schedule-changing)."""
    kterm_tok = MINUTES_TO_TOKEN.get(kterm[0], f"{kterm[0]}min")
    return (
        ValidOption(
            "component",
            "TargetSignalProjector",
            f"projects the terminal {render_clock(kterm)} signal onto the "
            f"declared {render_clock(kexec)} execution clock (forward-fill "
            f"of the last COMPLETED {render_clock(kterm)} bar)",
        ),
        ValidOption(
            "value",
            kterm_tok,
            f"re-declare Globals(target_timeframe='{kterm_tok}') if "
            f"{render_clock(kterm)} is the cadence you meant — this also "
            f"changes the live tick schedule",
        ),
    )


def symptom_for(actual: Clock, expected: Clock, *, role_form: bool, declared_render: str) -> str:
    """The three {symptom} shapes (spec 02 §2.1.1; measured bases spec 01
    §6.3). Shape 3 whenever p_a = p_e ∧ o_a ≠ o_e; role-form consumers
    (gate/scale reads) get the reindex/NaN-hold shape; everything else the
    conservative intersect shape (rows are lost, not invented)."""
    p_a, o_a = actual
    p_e, o_e = expected
    if p_a == p_e and o_a != o_e:
        period_tf = _humanize_minutes(p_a)
        phase_delta = _humanize_minutes(abs(o_a - o_e))
        return (
            f"Both sides run at {period_tf}, but their bars are labelled "
            f"{phase_delta} apart — every read is one bar stale."
        )
    if role_form and p_a > p_e:
        ratio = p_a // p_e
        k_bars = ratio - 1
        return (
            f"Combined as-is, the {render_clock(actual)} value is read on 1 of "
            f"every {ratio} {render_clock(expected)} bars; the other {k_bars} "
            f"are NaN, and NaN means HOLD."
        )
    return (
        f"Combined as-is, only the bars common to both clocks survive — the "
        f"strategy would silently execute on {render_clock(actual)} instead "
        f"of the declared {declared_render}."
    )


def terminal_consequence(actual: Clock, kexec: Clock) -> str:
    """TERMINAL_CLOCK_MISMATCH's three {consequence} shapes (spec 02
    §2.1.2, R02-MIN-03), selected by comparing κ_term to κ_exec."""
    p_a, o_a = actual
    p_e, o_e = kexec
    expected_render = render_clock(kexec)
    if p_a == p_e:
        period_tf = _humanize_minutes(p_a)
        phase_delta = _humanize_minutes(abs(o_a - o_e))
        return (
            f"Both run at {period_tf}, but the weight labels sit {phase_delta} "
            f"off the live tick grid — every live tick reads a one-bar-stale "
            f"weight."
        )
    if p_a > p_e:
        ratio = p_a // p_e if p_a % p_e == 0 else round(p_a / p_e)
        refuse_ratio = f"{ratio - 1} of every {ratio}"
        return (
            f"Deployed, the live scheduler ticks on {expected_render} and "
            f"would refuse {refuse_ratio} of ticks with DATA_LAG (no order "
            f"placed, one FAILED signal_run row per refusal); the backtest "
            f"completes anyway, with metrics annualized off the wrong grid."
        )
    ratio = p_e // p_a if p_e % p_a == 0 else round(p_e / p_a)
    return (
        f"Deployed, the live scheduler ticks on {expected_render} and reads "
        f"only 1 of every {ratio} produced weights — the others are computed "
        f"and never traded; the backtest executes them all, so live and "
        f"backtest diverge."
    )


__all__ = [
    "Clock",
    "ClockFire",
    "FireEnvelope",
    "MINUTES_TO_TOKEN",
    "TransferResult",
    "UniformRepair",
    "clock_ref",
    "else_arm_guidance",
    "evaluate_transfer",
    "fix_component_for",
    "fix_component_parts",
    "is_subclock",
    "kappa_exec",
    "lattice_options",
    "machine_arm_suggestion",
    "mismatch_fix_option",
    "nearest_harmonic_coarsen",
    "nearest_harmonic_project",
    "phase_inheritance_options",
    "render_clock",
    "repair_lands_on_kexec",
    "resample_up_options",
    "resample_up_suggestion",
    "select_uniform_repair",
    "symptom_for",
    "terminal_consequence",
    "terminal_resample_up_options",
    "terminal_resample_up_suggestion",
    "terminal_valid_options",
    "token_for",
]
