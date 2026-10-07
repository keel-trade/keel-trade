"""Shared validation constants, types, and pure functions.

Used by the DSL validator (write time) and by step type extraction
(``pipeline/validator.py``'s surviving ``get_step_types``). The runtime
Layer C PipelineValidator that co-owned this module was retired 2026-08-26
(Q-0689); the ``ErrorCode`` constants class it read went with it — issue
codes live in ``dsl/catalog.py``, the single registry.

This module provides the shared pieces: type transition graph, validation
result types, timeframe tables.

Quick Start:
    >>> from pipeline_engine.validation_shared import (
    ...     TYPE_TRANSITIONS, ValidationResult,
    ... )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pipeline_engine.base.registry import is_compatible  # noqa: F401 — re-export
from pipeline_engine.base.step import PHASE_GROUPS, StepCategory
from timeframes import MINUTES_TO_TOKEN as _tf_minutes_to_token
from timeframes import TIMEFRAME_MINUTES  # noqa: F401 — re-export; see below
from timeframes import bars_per_day as _tf_bars_per_day
from timeframes import match_minutes as _tf_match_minutes
from timeframes import minutes as _tf_minutes


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RESAMPLER CONFIG RULES (source_tf, target_tf, bar_offset)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Single source of truth for resampling constraints. Used by:
#   - TimeframeResampler / TargetTimeframeResampler at runtime
#   - DSL validator pass 9 (catches errors before backtest submission)
#   - TypeScript validator (mirrored in pass9-declarations.ts)
#
# Same error messages across all three so the agent sees identical feedback
# regardless of which layer rejected the config.


# THE ALPHABET IS NOT DEFINED HERE ANY MORE — it lives in ``libs/timeframes``,
# the stdlib-only authority every layer can import, together with every
# representation derived from it (minutes, seconds, a timedelta, a pandas
# offset alias, a PostgreSQL interval, a display label).
#
# It moved because this module is the right home for a VALIDATOR constant and
# the wrong home for a VOCABULARY: ``libs/data_utils`` cannot import
# ``pipeline_engine`` (exec-worker and funding-sync carry data_utils without
# it), so every consumer below this layer hand-copied the table instead — 48
# such copies at the 2026-09-06 census, drifting independently.
#
# Re-exported rather than aliased so all ~40 existing importers, the generated
# ``validation_tables.json``, and every ``from pipeline_engine.validation_shared
# import TIMEFRAME_MINUTES`` are untouched.
#
# ───────────────────────────────────────────────────────────────────────────


def timeframe_to_minutes(tf: str) -> int:
    """Convert a canonical timeframe string to minutes. Raises on unknown.

    Thin wrapper over :func:`timeframes.minutes`, kept because it is the
    spelling ~10 call sites in this package already use.
    """
    return _tf_minutes(tf)


#: Calendar days in a crypto year. FP-003: crypto trades 24/7, so the year is
#: 365 calendar days rather than an equity calendar's 252 trading days. This is
#: a CALENDAR fact and nothing else — see ``bars_per_year`` for why that
#: distinction is load-bearing.
CRYPTO_DAYS_PER_YEAR = 365


def bars_per_year(tf: str) -> float:
    """How many bars of width *tf* fit in one crypto year. Raises on unknown.

    THE distinction this function exists to keep straight (Q-1151): an
    annualisation factor scales with **bars per year**, never with **days per
    year**. Those are the same number only when one bar is one day, and the
    platform's clock alphabet runs from ``5min`` to ``1d`` — a 288x span.

    Reusing the calendar constant ``CRYPTO_DAYS_PER_YEAR`` as a bars-per-year
    factor is the specific defect Q-1151 records at six sites. It is invisible
    while a framework is daily-only and silently wrong the moment anything
    sub-daily composes with it: a per-bar standard deviation annualised with
    ``sqrt(365)`` on a 15min clock understates the true figure by
    ``sqrt(96) = 9.80x``, which turns any absolute threshold that means
    "annualised vol" into a threshold that means nothing.

    Annualising a per-bar standard deviation is therefore
    ``std * sqrt(bars_per_year(tf))``; annualising a per-bar RATE (turnover,
    a mean) is ``rate * bars_per_year(tf)`` — linear, not sqrt.

    >>> bars_per_year("1d")
    365.0
    >>> bars_per_year("15min")
    35040.0
    """
    # Composed from the two facts, each owned exactly once: how many bars fit
    # in a day (``timeframes``, derived from the one alphabet literal) and how
    # many days are in a crypto year (FP-003, above). Computing this from
    # minutes instead would re-encode "1440 minutes in a day" a second time.
    return _tf_bars_per_day(tf) * CRYPTO_DAYS_PER_YEAR


#: Inverse alphabet: minutes → canonical token (11 entries; bijective).
#: Derived in ``libs/timeframes``; re-exported here for the existing importers.
MINUTES_TO_TOKEN = _tf_minutes_to_token

#: The canonical token whose width is ``minutes``, or ``None`` if none is —
#: for recovering a clock from OBSERVED data (the spacing of a weights frame
#: or an equity curve) where the declared clock was not carried alongside.
#:
#: Returns ``None`` rather than snapping: a caller that measured something
#: which is not a platform clock must be able to say so. Written once because
#: the hand-rolled if/elif ladders that did this (backtest_framework's executor
#: and signal optimizer) each covered a different, incomplete subset of the
#: alphabet and disagreed on the finest rung's spelling — and it now lives in
#: ``libs/timeframes`` with the alphabet itself, so a new rung reaches it too.
match_timeframe_minutes = _tf_match_minutes


def render_clock(clock: tuple[int, int]) -> str:
    """Canonical clock rendering — ONE helper drives both engines' message
    text (dsl-mtf-clocks spec 02 §4.2).

    The canonical token, plus ``@`` + offset-literal iff the phase is
    non-zero: ``(240, 0) → "4h"``; ``(1440, 720) → "1d@12h"``. Non-token
    offsets — any positive minute count ``parse_bar_offset_minutes`` admits —
    render canonically as ``@{n}min`` (R02-MIN-12): ``(60, 45) → "1h@45min"``.
    The TS mirror (M4e) reads the same tokens from generated data
    (``validation_tables.json``'s TIMEFRAME_MINUTES); no engine ever does
    arithmetic on these display strings.
    """
    p, o = clock
    base = MINUTES_TO_TOKEN.get(p, f"{p}min")
    if o == 0:
        return base
    tok = MINUTES_TO_TOKEN.get(o)
    return f"{base}@{tok}" if tok is not None else f"{base}@{o}min"


_BAR_OFFSET_UNIT_MINUTES: dict[str, int] = {
    "min": 1,
    "h": 60,
    "d": 24 * 60,
    "w": 7 * 24 * 60,
}

# Unified bar-offset grammar (core-engine-audit spec 02 §4.4, 2026-07-10):
# strict — case-sensitive, no whitespace — matching the TS editor validator's
# regex in pass9-declarations.ts exactly. Python used to accept '12H ' /
# ' 15min' (IGNORECASE + \s*) while TS rejected them: a live, unfixtured
# grammar divergence. Strictness is the safer unification direction and no
# stored strategy depends on the lenient forms (emitters always write
# canonical lowercase). Fixture bar_offset_grammar_reject pins the '12H '
# case in both languages.
_BAR_OFFSET_RE = re.compile(r"^(\d+)(min|h|d|w)$")


def parse_bar_offset_minutes(bar_offset: str) -> int:
    """Parse bar_offset to whole minutes. Raises ValueError on invalid.

    Strict grammar ('15min', '12h', '1d', '90min' — lowercase unit, no
    whitespace; unified with the TS editor validator, spec 02 §4.4) and
    strict about value (positive, whole minutes only — sub-minute offsets
    are nonsense on a platform whose finest declarable clock is 5min).
    """
    if not isinstance(bar_offset, str):
        raise ValueError(
            f"bar_offset ({bar_offset!r}) is not a valid duration. "
            f"Use a value like '15min', '30min', '1h', '12h'."
        )
    match = _BAR_OFFSET_RE.match(bar_offset)
    if not match:
        raise ValueError(
            f"bar_offset ({bar_offset!r}) is not a valid duration. "
            f"Use a value like '15min', '30min', '1h', '12h'."
        )
    n = int(match.group(1))
    unit = match.group(2)
    if n <= 0:
        # Zero is rejected by design (strict value, spec 02 §4.4): an OMITTED
        # bar_offset already means offset 0 (`clocks.kappa_exec`), and one
        # spelling per meaning keeps the emitters canonical. The message says
        # what to write instead — "must be positive" alone sent an agent to
        # '12h', moving its daily close by half a day (Q-1841).
        raise ValueError(
            f"bar_offset ({bar_offset!r}) must be positive. For no offset, omit "
            f"bar_offset: bars then close on the timeframe's own boundary "
            f"(00:00 UTC for '1d')."
        )
    return n * _BAR_OFFSET_UNIT_MINUTES[unit]


def validate_resample_config(
    source_tf: str,
    target_tf: str,
    bar_offset: str | None,
    source_offset_minutes: int = 0,
) -> None:
    """Validate a (source_tf, target_tf, bar_offset) triple. Raises ValueError.

    The phase rows are the **general Theorem-1 form** of
    dsl-multi-timeframe-clocks spec 01 §5.2, landed at the
    ``clock-transform-rebase`` PROMOTED flip (spec 03 §3.5 / §10 row 21,
    resolution R-7): ``o_in`` — the phase of the INPUT clock — is a parameter,
    never assumed 0.

    Rules (``p_in``/``p_out`` = source/target minutes, ``o_in`` =
    ``source_offset_minutes``, ``off`` = the requested offset):

      - ``p_in > p_out``                      → upsampling not supported
      - ``p_in == p_out ∧ off ≠ o_in``        → no valid offset range at same TF
      - ``off ≢ o_in (mod p_in)``             → must be a multiple of source bar size
      - ``off ≥ p_out``                       → must be strictly less than target
                                                (whole-period offsets are silent
                                                no-ops because pandas wraps
                                                mod-period)

    ``source_offset_minutes=0`` (the default) reproduces the historical
    per-hop rule exactly: an input read straight off a loader sits on the
    epoch grid at phase 0. The parameter matters for the **chained-offset**
    shape — ``(15,0) → (60,30) → (240,30)`` under
    ``Globals(bar_offset="30min")`` — which the old ``off % p_in`` form
    rejected at the second hop and the general form legalizes, because the
    target's boundaries never cut a source bar. That legalization is an
    INTENDED verdict change, enumerated in the D5 fire inventory and the D6
    expectations ledger (``prior_semantics: per_hop_reject``); it is the one
    place where validation and runtime BOTH loosen, together, so the two can
    never disagree on a chained shape.
    """
    source_mins = timeframe_to_minutes(source_tf)
    target_mins = timeframe_to_minutes(target_tf)

    if source_mins > target_mins:
        raise ValueError(
            f"Cannot resample {source_tf} → {target_tf}: upsampling not supported "
            f"(source must be ≤ target)."
        )

    if bar_offset is None:
        return

    offset_mins = parse_bar_offset_minutes(bar_offset)
    o_in = source_offset_minutes % source_mins

    if source_mins == target_mins:
        if offset_mins == o_in:
            # κ_out == κ_in: the offset is already the input's own phase, so
            # the "transform" is the identity — a no-op lint, not an error
            # (spec 01 §5.2's RESAMPLER_NOOP row owns it at the write-time
            # surface; the runtime short-circuits it).
            return
        raise ValueError(
            f"bar_offset ({bar_offset}) has no valid value when target_timeframe "
            f"equals the data loader's timeframe ({source_tf}). Remove bar_offset, "
            f"or set a larger target_timeframe."
        )

    if (offset_mins - o_in) % source_mins != 0:
        raise ValueError(
            f"bar_offset ({bar_offset}) must be a multiple of the data loader's "
            f"timeframe ({source_tf})."
        )

    if offset_mins >= target_mins:
        raise ValueError(
            f"bar_offset ({bar_offset}) must be strictly less than target_timeframe "
            f"({target_tf}); whole-period offsets are silent no-ops (pandas wraps "
            f"mod-period). For 'act N bars delayed' tests, use IndexShift_Nbars instead."
        )


def synth_offset_consumed(
    served_minutes: int,
    offset_minutes: int,
    grain_minutes: int | None,
) -> bool:
    """Does a clock-SOURCE loader apply ``Globals.bar_offset`` itself?

    The ONE rule both the write-time clock checker (``dsl/clocks.py``'s synth
    arm, mirrored in the TS engine) and the loaders' runtime roll follow
    (new-data-loaders spec 05 §2 / §2b): a loader that serves a grain
    COARSER than its roll source (``grain``) rolls onto the offset grid
    in-loader — the offset is CONSUMED there — exactly when that roll is
    legal under ``validate_resample_config(grain, served, offset)``:

    - ``served > grain`` — an identity serving (``served == grain``) never
      applies an offset; a downstream coarsen owns it (today's shape), and
      the checker refuses an offset finer than a source bar there;
    - ``offset < served`` — an offset at or beyond the served period cannot
      land on that grid (whole-period offsets wrap); it is left for a
      coarser downstream target;
    - ``offset ≡ 0 (mod grain)`` — the roll's source bars must tile it.

    ``grain_minutes=None`` is the loader that declares no ``grain`` on its
    ``clock_transfer``: the two grain-relative premises are unverifiable and
    are taken as satisfied — ``0 < offset < served`` alone decides. A loader
    should declare its grain so the checker and the runtime agree on the
    identity-serving and sub-grain corners.
    """
    if offset_minutes <= 0 or offset_minutes >= served_minutes:
        return False
    if grain_minutes is None:
        return True
    return served_minutes > grain_minutes and offset_minutes % grain_minutes == 0


def finer_than_native_message(loader: str, served: str, native: str, *, requested_by: str) -> str:
    """The ONE sentence both finer-than-native refusal surfaces render
    (new-data-loaders spec 05 §2b): the write-time LOADER_FINER_THAN_NATIVE
    message (its catalog template renders byte-identically — pinned by
    dsl/clocks_synth_offset_test.py) and the loaders' runtime refusal
    (``components.data_loaders.served_grain.finer_than_native_message``
    delegates here).

    ``requested_by`` names what asked for the finer grain —
    ``Globals(target_timeframe='15min')`` when the loader bound to the
    declaration, ``timeframe='15min'`` when the literal was explicit.
    """
    return (
        f"{loader} serves hourly data; {requested_by} is finer than its {native} "
        f"native grain. Set timeframe='{native}' on the loader and add "
        f"TargetSignalProjector() at the end of the branch — the last completed "
        f"hour is held on the {served} grid."
    )


# NOTE: the ErrorCode string-constants class that used to live here was
# DELETED 2026-08-26 with the Layer C runtime validator (Q-0689) — it was
# read only by that validator. Issue codes have exactly one registry:
# ``pipeline_engine.dsl.catalog.RULES``.


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION TYPES
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# TYPED-ISSUE ENVELOPE SUB-SCHEMAS (dsl-type-system spec 05 §3.2)
#
# JSON-native, stable shapes. Each carries a ``to_dict()`` producing exactly the
# spec 05 §3.2 wire form; the TS mirror (spec 05 §3.6) and the shared
# ``dsl/fixtures/envelope/full_issue.json`` fixture pin these shapes across both
# languages.
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DomainRef:
    """A value-domain lattice element in wire form (spec 05 §3.2).

    ``kind == "top"`` (⊤, no constraint); ``"set"`` (a literal value set, with
    the optional spec 02 ``domain_aliases`` name — Binary/Flag/Mask); or
    ``"interval"`` (a ``[low, high]`` bound).
    """

    kind: Literal["top", "set", "interval"]
    values: tuple[float, ...] | None = None
    low: float | None = None
    high: float | None = None
    alias: str | None = None

    @staticmethod
    def top() -> "DomainRef":
        return DomainRef("top")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "set":
            return {
                "kind": "set",
                "values": [float(v) for v in (self.values or ())],
                "alias": self.alias,
            }
        if self.kind == "interval":
            return {
                "kind": "interval",
                "low": float(self.low),  # type: ignore[arg-type]
                "high": float(self.high),  # type: ignore[arg-type]
                "alias": self.alias,
            }
        return {"kind": "top"}


@dataclass(frozen=True)
class ClockRef:
    """A clock in wire form (dsl-mtf-clocks spec 01 §10; spec 02 §4.1).

    ``timeframe`` carries the canonical TOKEN (e.g. ``"4h"`` — never minutes
    alone) because the alphabet is generated data and both engines manipulate
    tokens; ``period_minutes``/``phase_minutes`` ride along so no engine does
    arithmetic on display strings; ``origin`` is the path of the step that
    last set this clock, in the existing envelope path addressing (§3.1 #7 —
    no new addressing scheme).
    """

    timeframe: str
    period_minutes: int
    phase_minutes: int
    origin: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "period_minutes": self.period_minutes,
            "phase_minutes": self.phase_minutes,
            "origin": list(self.origin),
        }


@dataclass(frozen=True)
class TypeRef:
    """A synthesized or demanded type ``(declared, base, domain, tier, clock)`` (§3.2).

    ``declared`` is the name as shown (refined names verbatim, e.g.
    ``"BinarySignal"``); ``base`` is the checked base (for demands, the
    exp-unfolded parent); ``tier`` is the refinement demand strength
    (``hard`` set / ``soft`` interval / ``none`` ⊤); ``clock`` is the fourth
    member, exactly parallel to ``domain`` (dsl-mtf-clocks spec 01 §10 —
    ``None`` when the value is clock-less). ``to_dict()`` always emits the
    ``clock`` key, matching the envelope's always-present-keys discipline
    (spec 02 §4.1).
    """

    declared: str
    base: str
    domain: DomainRef
    tier: Literal["hard", "soft", "none"] = "none"
    clock: ClockRef | None = None
    #: The position layer's binding facet (spec 03-R4): ``{kind, lineage,
    #: origin}``, present ONLY when the facet is not ``market``. A deliberate
    #: departure from the always-present-keys discipline: omission keeps every
    #: existing trace pin and envelope fixture byte-identical.
    binding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "declared": self.declared,
            "base": self.base,
            "domain": self.domain.to_dict(),
            "tier": self.tier,
            "clock": self.clock.to_dict() if self.clock is not None else None,
        }
        if self.binding is not None:
            out["binding"] = dict(self.binding)
        return out


@dataclass(frozen=True)
class Span:
    """A source-text span in the strategy declaration (§3.2)."""

    line: int
    col: int
    end_line: int
    end_col: int

    def to_dict(self) -> dict[str, int]:
        return {
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


@dataclass(frozen=True)
class ProvenanceHop:
    """One hop of a domain/slot blame chain (origin → transfer → store → load)."""

    kind: Literal["origin", "transfer", "store", "load", "projection"]
    step: str
    path: tuple[str, ...] = ()
    type: TypeRef | None = None
    slot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step": self.step,
            "path": list(self.path),
            "type": self.type.to_dict() if self.type is not None else None,
            "slot": self.slot,
        }


@dataclass(frozen=True)
class ValidOption:
    """A machine-readable candidate, type-fit ranked (§3.2, R6-REQ4)."""

    kind: Literal["component", "value", "key", "slot", "version"]
    value: str | float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "detail": self.detail}


@dataclass(frozen=True)
class SuggestedEdit:
    """A concrete edit payload (§3.2, R6-REQ5) — closed op vocabulary."""

    #: ``upgrade`` (position-layer spec 04-R16, Q-2448): the
    #: POSITION_UPGRADE_AVAILABLE payload — recipe, mode, group, questions and,
    #: for a mechanical group, the verified ``result_source``.
    kind: Literal[
        "set_param", "replace_component", "insert_step", "remove_step", "rename", "upgrade"
    ]
    path: tuple[str, ...]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": list(self.path), "payload": dict(self.payload)}


@dataclass(frozen=True)
class StagingNote:
    """Severity honesty for a staged code (§3.1 #16): ``{stage, terminal_severity}``."""

    stage: str
    terminal_severity: str

    def to_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "terminal_severity": self.terminal_severity}


@dataclass
class ValidationIssue:
    """A single validation finding — the 16-field typed envelope (spec 05 §3.1).

    Five preserved fields (``severity``/``code``/``message``/``location``/
    ``suggestion``) keep their names, types, and meanings so every existing
    consumer is untouched; eleven new fields are all defaulted so the ~60
    non-type-shaped emission sites change zero call sites. ``to_dict()``
    serializes all 16 keys, always present (spec 05 §3.4 stable-shape).
    """

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str
    suggestion: str | None = None
    # ── Envelope extension (dsl-type-system spec 05 §3.1) — 11 new fields ──
    tier: Literal["static", "compile", "runtime", "gate"] = "static"
    path: tuple[str, ...] = ()
    span: Span | None = None
    expected: TypeRef | None = None
    actual: TypeRef | None = None
    provenance: tuple[ProvenanceHop, ...] = ()
    valid_options: tuple[ValidOption, ...] = ()
    suggested_edit: SuggestedEdit | None = None
    applicability: str = "none"
    recoverable: bool = True
    staging: StagingNote | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize all 16 envelope fields, always present (spec 05 §3.4)."""
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
            "tier": self.tier,
            "path": list(self.path),
            "span": self.span.to_dict() if self.span is not None else None,
            "expected": self.expected.to_dict() if self.expected is not None else None,
            "actual": self.actual.to_dict() if self.actual is not None else None,
            "provenance": [h.to_dict() for h in self.provenance],
            "valid_options": [o.to_dict() for o in self.valid_options],
            "suggested_edit": (
                self.suggested_edit.to_dict() if self.suggested_edit is not None else None
            ),
            "applicability": self.applicability,
            "recoverable": self.recoverable,
            "staging": self.staging.to_dict() if self.staging is not None else None,
        }


@dataclass
class TypeFlowEntry:
    """One step's type flow record for pipeline summary."""

    step: str  # e.g. "EWMACrossover(fast=8, slow=32)"
    input_type: str  # e.g. "PriceFrame"
    output_type: str  # e.g. "SignalSeries"
    category: str  # StepCategory value
    #: The output's binding facet (position-layer spec 03-R4), by omission.
    binding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        out: dict[str, Any] = {
            "step": self.step,
            "input_type": self.input_type,
            "output_type": self.output_type,
            "category": self.category,
        }
        if self.binding is not None:
            out["binding"] = dict(self.binding)
        return out


@dataclass
class ValidationResult:
    """Aggregated validation result with structured issues."""

    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    info: list[ValidationIssue] = field(default_factory=list)
    type_flow: list[TypeFlowEntry] = field(default_factory=list)
    slot_types: dict[str, type] = field(default_factory=dict)
    pipeline_summary: str = ""

    def all_issues(self) -> list[ValidationIssue]:
        """Errors, then warnings, then info — flat list for serialization.

        Use when emitting issues to API responses, MCP tool output, or any
        consumer that surfaces issues to the user. Including info ensures
        non-blocking advisory codes (LOCK_DRIFT, future signals) reach the
        renderer instead of being silently dropped.
        """
        return self.errors + self.warnings + self.info

    def explain(self) -> str:
        """Produce readable multi-line output of all issues."""
        lines: list[str] = []

        if self.valid:
            lines.append("Pipeline validation passed.")
        else:
            lines.append("Pipeline validation FAILED.")

        for label, issues in [
            ("ERRORS", self.errors),
            ("WARNINGS", self.warnings),
            ("INFO", self.info),
        ]:
            if issues:
                lines.append(f"\n{label}:")
                for issue in issues:
                    lines.append(f"  [{issue.code}] {issue.message} (at {issue.location})")
                    if issue.suggestion:
                        lines.append(f"    -> {issue.suggestion}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE INDEX
# ═══════════════════════════════════════════════════════════════════════════════

# Group-based index for O(1) phase ordering lookups.
# Categories within the same group share an index, so intra-group
# reordering is allowed; only cross-group backward jumps are violations.
PHASE_INDEX: dict[StepCategory, int] = {}
for _group_idx, _group_cats in enumerate(PHASE_GROUPS):
    for _cat in _group_cats:
        PHASE_INDEX[_cat] = _group_idx


#: The sanctioned universe-mask APPLIER components (rolling-universe P2 (c)).
#: ApplyUniverseMask's registered category (signal_transform) describes its
#: value effect, not its placement: Contract C (projects/rolling-universe/
#: 01-contracts.md §1) fixes its position at the TS→XS boundary of each path,
#: which is phase-VARIABLE by design (signals are computed on the full pool,
#: the mask lands wherever the first cross-sectional op needs it). Keyed by
#: name because no registration metadata isolates "mask applier" today — its
#: population_scope is per_column (it pools nothing). If a second applier
#: ever ships, prefer promoting this to registration metadata over growing
#: the list (flagged in the P2 (c) report).
UNIVERSE_MASK_APPLIERS: frozenset[str] = frozenset({"ApplyUniverseMask"})


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE TRANSITION GRAPH
# ═══════════════════════════════════════════════════════════════════════════════

# Semantic (Annotated) types → canonical semantic name, keyed by the type
# VALUE. Plain aliases (RawSignal = SignalSeries) are the same object at
# runtime, so they merge into their base type's row. Annotated types are
# distinguishable via get_origin() and get their own rows.
#
# Keyed by value, never by id() (Q-2229): typing memoises Annotated[...] only
# in a 128-entry LRU cache, so a long-running process that rebuilds
# BinarySignal (get_type_hints(..., include_extras=True) after an eviction)
# holds an object that is == BinarySignal but is not BinarySignal. An id()-keyed
# table named that object 'Annotated' and the compiler refused the slot. Every
# lookup goes through semantic_type_name(). Populated below TYPE_TRANSITIONS,
# whose keys pick the canonical name of an alias group.
ANNOTATED_SEMANTIC_NAMES: dict[Any, str] = {}


# String-keyed type transition graph (Decision D20).
# Keys are runtime-reconciled type names:
# - Plain aliases (RawSignal = SignalSeries) use base type name ("SignalSeries")
# - Annotated types use semantic names ("NormalizedSignal", "BinarySignal", etc.)
# - Special types: "None" for pipeline entry, "dict" for post-Parallel
TYPE_TRANSITIONS: dict[str, dict[StepCategory, list[str]]] = {
    # === Pipeline entry ===
    "None": {
        StepCategory.DATA_LOADER: ["OHLCVDict", "StreamSeries", "DollarVolumeSeries"],
    },
    # === Phase 1: DATA ===
    "OHLCVDict": {
        # Most data transforms preserve OHLCV shape, while
        # VolatilityAdjustedPriceSeries intentionally reduces close prices to
        # a signal frame for BreakoutDistance.
        StepCategory.DATA_TRANSFORM: ["OHLCVDict", "SignalSeries"],
        # ExtractSeries is a signal transform because it starts a signal branch,
        # but its truthful input is the upstream OHLCV dictionary.
        StepCategory.SIGNAL_TRANSFORM: ["SignalSeries"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries", "OHLCVDict", "BinarySignal"],
        # Indicators may emit a BinarySignal subtype (e.g. SuperTrend/Ichimoku
        # regime signals), not just a generic SignalSeries.
        StepCategory.INDICATOR: ["SignalSeries", "BinarySignal"],
        # ConstantForecast accepts OHLCVDict directly as an index/basket entry point.
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries", "SignalSeries"],
    },
    # === Phase 1b: STREAM DATA (funding rates, OI, premium) ===
    "StreamSeries": {
        StepCategory.DATA_TRANSFORM: ["StreamSeries", "SignalSeries"],
        # A superset of the SignalSeries row below (Q-2114): StreamSeries IS a
        # SignalSeries (types.py), so a threshold or rank transform straight on
        # funding/OI/premium emits what it emits on any other signal. Without
        # BinarySignal/RankSignal here the only clean spelling was a no-op
        # resampler relabelling the stream as SignalSeries first.
        StepCategory.SIGNAL_TRANSFORM: [
            "SignalSeries",
            "NormalizedSignal",
            "StreamSeries",
            "BinarySignal",
            "RankSignal",
        ],
        # Regime detectors return a market-wide ``GlobalSeries`` (1-D
        # ``pd.Series``) rather than the legacy SignalSeries (DataFrame).
        # All 7 registered RegimeDetectors honor this — verified 2026-06-11.
        StepCategory.REGIME_DETECTOR: ["GlobalSeries"],
        StepCategory.INDICATOR: ["SignalSeries"],
    },
    # === Phase 1b': DOLLAR VOLUME (dollar-volume DV4) ===
    # DollarVolumeLoader v2's declared stream — a NewType over StreamSeries,
    # so it may flow wherever a stream may: this row is the StreamSeries row
    # with DollarVolumeSeries admitted wherever StreamSeries is (a
    # type-preserving transform — a clock projector/resampler instantiating
    # ∀T. T → T at T = DollarVolumeSeries — keeps the refined name). It is
    # read by the dollar-volume components through a SLOT, which transitions
    # do not govern (slot compatibility is SLOT_TYPE_MISMATCH's).
    "DollarVolumeSeries": {
        StepCategory.DATA_TRANSFORM: ["DollarVolumeSeries", "StreamSeries", "SignalSeries"],
        # StreamSeries's row (incl. its Q-2114 BinarySignal/RankSignal
        # widening) plus the refined name — a subtype's row is never narrower
        # than its parent's (types_test pins the superset).
        StepCategory.SIGNAL_TRANSFORM: [
            "SignalSeries",
            "NormalizedSignal",
            "StreamSeries",
            "BinarySignal",
            "RankSignal",
            "DollarVolumeSeries",
        ],
        StepCategory.REGIME_DETECTOR: ["GlobalSeries"],
        StepCategory.INDICATOR: ["SignalSeries"],
    },
    # === Phase 3: SIGNAL (within-branch transitions) ===
    "SignalSeries": {
        StepCategory.DATA_TRANSFORM: ["SignalSeries"],
        # BreakoutDistance consumes the signal frame emitted by
        # VolatilityAdjustedPriceSeries while retaining indicator semantics.
        StepCategory.INDICATOR: ["SignalSeries"],
        StepCategory.SIGNAL_TRANSFORM: [
            "NormalizedSignal",
            "BinarySignal",
            "RankSignal",
            "SignalSeries",
        ],
        # See the "StreamSeries" → REGIME_DETECTOR note above — regime
        # detectors collapse cross-sectional input into a market-wide
        # 1-D series.
        StepCategory.REGIME_DETECTOR: ["GlobalSeries"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.UNIVERSE_FILTER: ["SignalSeries", "BinarySignal"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries", "Position"],
        StepCategory.REPORTER: ["SignalSeries"],
    },
    "NormalizedSignal": {
        StepCategory.SIGNAL_TRANSFORM: [
            "NormalizedSignal",
            "BinarySignal",
            "RankSignal",
            "SignalSeries",
        ],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
    },
    "BinarySignal": {
        StepCategory.SIGNAL_TRANSFORM: ["BinarySignal"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries", "Position"],
    },
    "RankSignal": {
        StepCategory.SIGNAL_TRANSFORM: ["NormalizedSignal", "RankSignal", "BinarySignal"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
    },
    # === After Parallel — current is dict[str, Any] (branch results) ===
    "dict": {
        # Signal composers may emit a BinarySignal subtype (e.g. And/Or/Mask
        # combiners), not just a generic SignalSeries.
        StepCategory.SIGNAL_COMPOSER: ["SignalSeries", "BinarySignal"],
        StepCategory.FORECAST_COMPOSER: ["ForecastSeries", "WeightSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["BinarySignal", "WeightSeries", "SignalSeries", "Position"],
    },
    # === Phase 4: FORECAST ===
    "ForecastSeries": {
        StepCategory.SIGNAL_TRANSFORM: ["ForecastSeries", "SignalSeries"],
        StepCategory.FORECAST_COMPOSER: ["ForecastSeries"],
        StepCategory.FORECAST_MAPPER: ["ForecastSeries"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.REPORTER: ["ForecastSeries"],
    },
    # === Phase 5: PORTFOLIO ===
    "WeightSeries": {
        StepCategory.POSITION_SIZER: ["WeightSeries"],
        StepCategory.RISK_MANAGER: ["WeightSeries"],
        StepCategory.POSITION_MANAGER: ["WeightSeries", "Position"],
        StepCategory.EXECUTOR: ["OrderSeries"],
    },
    # === The position layer (position-layer spec 03-R3, Q-2448) ===
    # A TradeManager mints a Position (``Position`` is appended to every row
    # that already has a POSITION_MANAGER entry, above); readers, actions and
    # Exposure() are POSITION_MANAGER steps on it, and RiskSizer is the one
    # POSITION_SIZER that reads it directly. No pre-existing row gains a
    # category, so the transition advisory cannot fire on any strategy that
    # predates the position layer.
    "Position": {
        StepCategory.POSITION_MANAGER: ["Position", "SignalSeries", "BinarySignal"],
        StepCategory.POSITION_SIZER: ["WeightSeries"],
    },
    # === Phase 6-7: EXECUTION & REPORTING ===
    "OrderSeries": {
        StepCategory.REPORTER: ["OrderSeries"],
    },
    # === Market-wide single time series (regime labels, factor exposures) ===
    # Used as the canonical output type for RegimeDetector (via the
    # RegimeLabel alias). A GlobalSeries is consumed by slot reads on
    # RegimeLeverageScaler / MarketLeverageScaler / MarketRiskScaler /
    # RegimeGate, so the chain transitions are intentionally small —
    # most regime detectors end a branch and Store the result for
    # downstream slot reads. Reporters can still emit them for
    # visualization.
    "GlobalSeries": {
        StepCategory.REPORTER: ["GlobalSeries"],
    },
}


def validate_transition_coverage() -> None:
    """Verify TYPE_TRANSITIONS covers all pipeline categories (ARCH-003).

    Raises RuntimeError if any StepCategory is missing from all transition
    paths. Called at module initialization.
    """
    covered_categories: set[StepCategory] = set()
    for transitions in TYPE_TRANSITIONS.values():
        for cat in transitions:
            covered_categories.add(cat)

    all_categories = set(StepCategory) - {StepCategory.SLOT_OP}
    uncovered = all_categories - covered_categories
    if uncovered:
        raise RuntimeError(
            f"TYPE_TRANSITIONS does not cover categories: "
            f"{', '.join(c.value for c in sorted(uncovered, key=lambda c: c.value))}. "
            f"All non-SLOT_OP categories must appear in at least one transition path."
        )


validate_transition_coverage()


def _init_annotated_semantic_names() -> None:
    """Name every Annotated type exported by pipeline_engine.types.

    Exports are grouped by VALUE, so names bound to one object
    (``CappedForecast``/``Forecast``/``ForecastSeries``) form one group. The
    group's canonical name is its unique ``TYPE_TRANSITIONS`` key (the same
    rule ``dsl/fixtures/loader._live_base_vocabulary`` applies). A group with
    no such member, or with two, is a declaration error and fails import:
    a semantic type the transition table cannot key has no correct name.
    """
    from pipeline_engine import types as t

    groups: dict[Any, list[str]] = {}
    for name in dir(t):
        if name.startswith("_"):
            continue
        obj = getattr(t, name)
        if get_origin(obj) is Annotated:
            groups.setdefault(obj, []).append(name)

    for obj, names in groups.items():
        canonical = [n for n in names if n in TYPE_TRANSITIONS]
        if len(canonical) != 1:
            raise RuntimeError(
                f"Annotated types {sorted(names)} in pipeline_engine.types are one "
                f"type by value, and need exactly one TYPE_TRANSITIONS key among "
                f"them to name it; found {canonical}. Fix the types.py "
                f"declaration or the transition table."
            )
        ANNOTATED_SEMANTIC_NAMES[obj] = canonical[0]


_init_annotated_semantic_names()


def semantic_type_name(t: Any) -> str | None:
    """The semantic name of an Annotated type, or None if it has none.

    THE one lookup of ``ANNOTATED_SEMANTIC_NAMES`` (Q-2229). Matches by
    equality, so a rebuilt-but-equal ``Annotated[SignalSeries,
    DiscreteValues(...)]`` names as ``BinarySignal`` exactly as the
    module-level object does. It compares against each entry rather than
    hashing the probe, so a probe with unhashable metadata (which can equal
    no entry) answers None instead of raising. Non-Annotated input answers
    None.
    """
    if get_origin(t) is not Annotated:
        return None
    for semantic_type, name in ANNOTATED_SEMANTIC_NAMES.items():
        if t == semantic_type:
            return name
    return None


def type_to_transition_key(t: type) -> str | None:
    """Map a runtime type to its string key in TYPE_TRANSITIONS.

    Resolution order:
    1. Annotated types -> semantic_type_name (by value)
    2. type(None) -> "None"
    3. dict -> "dict"
    4. NewType -> __qualname__ (e.g., "PriceFrame", "SignalSeries")
    5. Regular type -> __name__

    Returns None if a non-Annotated type has no entry in TYPE_TRANSITIONS.

    Raises:
        TypeError: for an Annotated type that is not a declared semantic type.
            It has no row of its own, and keying it by its base would check
            it against a row that does not describe it. No registered
            component declares one (Q-2229 sweep); declare it in
            pipeline_engine.types with a TYPE_TRANSITIONS row instead.
    """
    # 1. Annotated types: use semantic name
    if get_origin(t) is Annotated:
        name = semantic_type_name(t)
        if name is None:
            raise TypeError(
                f"{t!r} is an Annotated type that is not a declared semantic type "
                f"in pipeline_engine.types, so it has no TYPE_TRANSITIONS row. "
                f"Declare it there with a transition row, or use a declared type."
            )
        return name

    # 2. None type
    if t is type(None):
        return "None"

    # 3. dict type
    if t is dict:
        return "dict"

    # 4. NewType (has __qualname__ like "PriceFrame")
    if hasattr(t, "__supertype__"):
        name = getattr(t, "__qualname__", str(t))
        if name in TYPE_TRANSITIONS:
            return name
        return None

    # 5. Regular class
    name = getattr(t, "__name__", str(t))
    if name in TYPE_TRANSITIONS:
        return name
    return None


def type_name(t: type) -> str:
    """Human-readable type name for error messages."""
    if t is type(None):
        return "None"
    if t is Any:
        return "Any"
    # Annotated types: use semantic name if available
    name = semantic_type_name(t)
    if name is not None:
        return name
    # Union types: render as "A | B | C" rather than a bare "Union"
    origin = get_origin(t)
    if origin is Union or (origin is not None and origin.__class__.__name__ == "UnionType"):
        args = get_args(t)
        return " | ".join(type_name(a) for a in args)
    if hasattr(t, "__name__"):
        return t.__name__
    # NewType objects have __qualname__ or we can use str
    if hasattr(t, "__qualname__"):
        return t.__qualname__
    return str(t)


def _param_target_types(pinfo) -> tuple[type, ...]:
    """Unwrap Optional/Union on a parameter type, returning the non-None members.

    For `float | None` → `(float,)`. For `int | float` → `(int, float)`. For a
    bare `int` → `(int,)`. Used by both the validator (acceptance check) and
    the emitter (canonical numeric rendering).
    """
    import types as _types

    t = pinfo.type_
    origin = get_origin(t)
    if origin is Union or isinstance(t, _types.UnionType):
        return tuple(a for a in get_args(t) if a is not type(None))
    return (t,)


def param_display_type(pinfo) -> str:
    """Frontend/agent-facing type label.

    Single source of truth for the parameter "type" string shown in tool
    results, validator errors, and frontend registry metadata. Strips
    Optional (Union[T, None] → T) so the surface vocabulary stays small:
    "int", "float", "str", "bool", "enum", or a component type name.
    Optional vs required is communicated via the separate ``required``
    field, not the type string.
    """
    t = pinfo.type_
    if get_origin(t) is Literal:
        return "enum"
    targets = _param_target_types(pinfo)
    if len(targets) == 1:
        t = targets[0]
    if get_origin(t) is Literal:
        # Optional[Literal[...]] — a None-sentinel enum param (e.g.
        # SignalResampler v2's target_timeframe): still "enum"; None marks
        # omission and rides the separate `required`/`default` fields.
        return "enum"
    return getattr(t, "__name__", str(t))


def param_type_structure(t: Any) -> dict:
    """Structural descriptor of a declared parameter type (spec 08 §2.1).

    Derived from the ONE type authority — ``RegistryParamInfo.type_`` as
    extracted by the registration ladder (dsl-type-system spec 02 §3.1) —
    exactly as ``write_domain_tables`` serializes the live domain evaluator.
    Both param-value checkers consume this grammar: Python in-process (the
    ``class`` node carries the real class under the non-serialized ``cls``
    key for ``isinstance``), TS via the JSON form shipped per param in
    ``registry_metadata.json`` / the components API (strip with
    :func:`param_type_structure_json`).

    Grammar (spec 08 §2.1): ``any`` | ``none`` | ``class`` | ``literal``
    (type shape only — VALUES stay ``constraints.options``' job) | ``list``
    | ``dict`` | ``union`` | ``opaque`` (the PARAM_TYPE_CHECK_SKIPPED
    tripwire — no current registry param produces it; the registry sweep
    test proves that stays true).
    """
    import types as _types

    if t is Any:
        return {"kind": "any"}
    if t is None or t is type(None):
        return {"kind": "none"}
    origin = get_origin(t)
    if origin is Annotated:
        return param_type_structure(get_args(t)[0])
    if origin is Literal:
        arm_types: list[str] = []
        for arm in get_args(t):
            n = type(arm).__name__
            if n not in arm_types:
                arm_types.append(n)
        return {"kind": "literal", "arm_types": arm_types}
    if origin is Union or isinstance(t, _types.UnionType):
        # Literal arms fold into ONE literal member (mirrors the ladder's
        # options derivation, registration.py — Optional[Literal] and
        # Literal-union params contribute a single options surface).
        members: list[dict] = []
        literal_arm_types: list[str] = []
        saw_literal = False
        for a in get_args(t):
            if get_origin(a) is Literal:
                saw_literal = True
                for arm in get_args(a):
                    n = type(arm).__name__
                    if n not in literal_arm_types:
                        literal_arm_types.append(n)
            else:
                members.append(param_type_structure(a))
        if saw_literal:
            members.insert(0, {"kind": "literal", "arm_types": literal_arm_types})
        return {"kind": "union", "members": members}
    import collections.abc as _abc

    if t is list or origin in (list, _abc.Sequence):
        # Sequence[T] params (ResidualMomentum.benchmarks) take the list
        # semantics: DSL/graph literals are lists; str is rejected even
        # though it is a runtime Sequence (chars-iteration is never the
        # authored intent).
        args = get_args(t)
        return {
            "kind": "list",
            "element": param_type_structure(args[0]) if args else {"kind": "any"},
        }
    if t is dict or origin is dict:
        args = get_args(t)
        return {
            "kind": "dict",
            "key": param_type_structure(args[0]) if args else {"kind": "any"},
            "value": param_type_structure(args[1]) if len(args) > 1 else {"kind": "any"},
        }
    if origin is tuple:
        args = get_args(t)
        # Fixed-arity tuples only (RegimeScale.clip_range: tuple[float,
        # float]); the variadic tuple[T, ...] form stays opaque (tripwire —
        # no current registry param uses it).
        if args and Ellipsis not in args:
            return {"kind": "tuple", "elements": [param_type_structure(a) for a in args]}
        return {"kind": "opaque", "repr": str(t)}
    if origin is None and isinstance(t, type):
        return {"kind": "class", "name": t.__name__, "cls": t}
    return {"kind": "opaque", "repr": str(t)}


def param_type_structure_json(t: Any) -> dict:
    """JSON-serializable form of :func:`param_type_structure` (no ``cls``)."""

    def strip(d: dict) -> dict:
        out = {k: v for k, v in d.items() if k != "cls"}
        if "element" in out:
            out["element"] = strip(out["element"])
        if "key" in out:
            out["key"] = strip(out["key"])
        if "value" in out:
            out["value"] = strip(out["value"])
        if "members" in out:
            out["members"] = [strip(m) for m in out["members"]]
        if "elements" in out:
            out["elements"] = [strip(e) for e in out["elements"]]
        return out

    return strip(param_type_structure(t))


def check_param_structure(desc: dict, value: object) -> str:
    """Check a literal param value against a structural descriptor.

    Returns ``"match"`` | ``"mismatch"`` | ``"unknown"`` (spec 08 §2.2).
    Semantics shared byte-for-byte with the TS checker (rules/params.ts):

    - numeric classes accept int OR float instances, never bool (the
      catalog's documented rule — this is what today's isinstance branch
      got wrong for ``True``-for-int);
    - ``literal`` checks the TYPE SHAPE only; value membership belongs to
      ``constraints.options`` → PARAM_INVALID_OPTION (never double-emits);
    - containers check the container type plus every element;
    - unions match on any arm, and degrade to ``unknown`` (never a false
      reject) when nothing matched but an uncheckable arm exists;
    - ``unknown`` at the top level is the PARAM_TYPE_CHECK_SKIPPED
      tripwire — structurally unreachable for the current registry;
    - a ``VariableRef`` ANYWHERE (top level or nested in a container) is
      statically unresolvable and matches — the declared ``variable-ref``
      skip_when semantics (spec 03 §2.3) extended element-wise. TS mirrors
      this by skipping ``{"$ref": ...}`` objects.
    """
    from pipeline_engine.dsl.spec import VariableRef  # late import — no cycle at load

    if isinstance(value, VariableRef):
        return "match"
    kind = desc["kind"]
    if kind == "any":
        return "match"
    if kind == "none":
        return "match" if value is None else "mismatch"
    if kind == "literal":
        return "match" if type(value).__name__ in desc["arm_types"] else "mismatch"
    if kind == "class":
        name = desc["name"]
        if name in ("int", "float"):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            return "match" if ok else "mismatch"
        if name == "bool":
            return "match" if isinstance(value, bool) else "mismatch"
        if name == "str":
            return "match" if isinstance(value, str) else "mismatch"
        cls = desc.get("cls")
        if cls is None:
            # Serialized descriptor without the live class (the TS side's
            # position): cannot instance-check — never a false reject.
            return "unknown"
        return "match" if isinstance(value, cls) else "mismatch"
    if kind == "list":
        if not isinstance(value, list):
            return "mismatch"
        verdicts = {check_param_structure(desc["element"], v) for v in value}
        if "mismatch" in verdicts:
            return "mismatch"
        return "unknown" if "unknown" in verdicts else "match"
    if kind == "dict":
        if not isinstance(value, dict):
            return "mismatch"
        verdicts = {check_param_structure(desc["key"], k) for k in value}
        verdicts |= {check_param_structure(desc["value"], v) for v in value.values()}
        if "mismatch" in verdicts:
            return "mismatch"
        return "unknown" if "unknown" in verdicts else "match"
    if kind == "tuple":
        # DSL/graph values arrive as lists (JSON) or tuples (Python parse) —
        # accept both, exact arity, element-wise check.
        if not isinstance(value, (list, tuple)) or len(value) != len(desc["elements"]):
            return "mismatch"
        verdicts = {
            check_param_structure(ed, v) for ed, v in zip(desc["elements"], value, strict=True)
        }
        if "mismatch" in verdicts:
            return "mismatch"
        return "unknown" if "unknown" in verdicts else "match"
    if kind == "union":
        verdicts = [check_param_structure(m, value) for m in desc["members"]]
        if "match" in verdicts:
            return "match"
        return "unknown" if "unknown" in verdicts else "mismatch"
    return "unknown"  # opaque — the tripwire


def param_structure_label(desc: dict) -> str:
    """Expected-type label for PARAM_TYPE_MISMATCH, from a descriptor.

    Reproduces :func:`param_display_type` byte-for-byte on the shapes that
    already emit today (class → name, Optional[X] → X, multi-member unions
    → ``"A | B"`` incl. a trailing ``None``, Literal → ``"enum"``) and adds
    structural text for the newly checkable shapes (``list[str]``,
    ``dict[str, float]``). Mirrored in TS (rules/params.ts) so both
    languages render identical messages from the same serialized data.
    """
    kind = desc["kind"]
    if kind == "any":
        return "Any"
    if kind == "none":
        return "None"
    if kind == "literal":
        return "enum"
    if kind == "class":
        return desc["name"]
    if kind == "list":
        if desc["element"]["kind"] == "any":
            return "list"
        return f"list[{param_structure_label(desc['element'])}]"
    if kind == "dict":
        if desc["key"]["kind"] == "any" and desc["value"]["kind"] == "any":
            return "dict"
        return f"dict[{param_structure_label(desc['key'])}, {param_structure_label(desc['value'])}]"
    if kind == "tuple":
        return f"tuple[{', '.join(param_structure_label(e) for e in desc['elements'])}]"
    if kind == "union":
        non_none = [m for m in desc["members"] if m["kind"] != "none"]
        if len(non_none) == 1:
            return param_structure_label(non_none[0])
        return " | ".join(param_structure_label(m) for m in desc["members"])
    return desc.get("repr", "?")  # opaque


def value_structure_label(value: object) -> str:
    """Actual-type label for PARAM_TYPE_MISMATCH, from the literal value.

    Scalars render exactly as the pre-spec-08 ``type(pval).__name__``;
    containers render their element-type union in encounter order
    (``list[str | int]``). Mirrored in TS. NOTE the known whole-float
    asymmetry (loader.py fixture-authoring note): TS cannot distinguish
    ``3.0`` from ``3`` — conformance fixtures use non-whole floats.
    """
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, list):
        parts: list[str] = []
        for v in value:
            lbl = value_structure_label(v)
            if lbl not in parts:
                parts.append(lbl)
        return f"list[{' | '.join(parts)}]" if parts else "list"
    if isinstance(value, dict):
        kparts: list[str] = []
        vparts: list[str] = []
        for k, v in value.items():
            kl = value_structure_label(k)
            vl = value_structure_label(v)
            if kl not in kparts:
                kparts.append(kl)
            if vl not in vparts:
                vparts.append(vl)
        if not kparts:
            return "dict"
        return f"dict[{' | '.join(kparts)}, {' | '.join(vparts)}]"
    return type(value).__name__


def param_accepts_numeric(pinfo, value: object) -> bool:
    """Whether ``value`` is numerically acceptable for ``pinfo``.

    Mirrors Python's runtime laxness: an ``int`` satisfies any param whose
    declared type set includes ``float``. Booleans are excluded (they're an
    ``int`` subclass but rarely intended as numeric). Returns False when the
    param doesn't accept any numeric type.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    targets = _param_target_types(pinfo)
    return float in targets or int in targets


# ═══════════════════════════════════════════════════════════════════════════════
# PARAM RANGE (Q-2242 — hard limits vs typical guidance)
# ═══════════════════════════════════════════════════════════════════════════════
#
# A component's class ``constraints`` dict (merged into ParamInfo.constraints
# at registration) and ``EXECUTION_PARAM_META`` carry one frozen shape per
# numeric parameter (param-domains plan, 2026-10-01):
#
#     "min": <hard lower bound>      # ABSENT = unbounded below
#     "max": <hard upper bound>      # ABSENT = unbounded above
#     "min_exclusive": True          # only present when true (">")
#     "max_exclusive": True          # only present when true ("<")
#     "typical": [lo, hi]            # guidance only — never a validator issue
#     "step": ...                    # unchanged
#
# ``min``/``max`` are the ONLY values the validator rejects (PARAM_OUT_OF_RANGE,
# Python pass 5 + Execution loop, TS rules/params.ts + rules/declarations.ts).
# ``typical`` is what agents, the editor hint, test synthesis and the (dead)
# optimizer grid read. Before Q-2242 the old [min, max] were optimizer/UI
# hints enforced as hard errors; they became ``typical``. Every reader goes
# through these helpers so the surfaces cannot drift on exclusivity or on an
# absent bound.


def hard_bounds(constraints: dict | None) -> tuple[float | None, bool, float | None, bool]:
    """``(min, min_exclusive, max, max_exclusive)`` of a constraints dict.

    An absent ``min``/``max`` is ``None`` (unbounded on that side). The
    exclusivity flags are read only when their bound is present.
    """
    c = constraints or {}
    lo = c.get("min")
    hi = c.get("max")
    lo_excl = bool(c.get("min_exclusive")) if lo is not None else False
    hi_excl = bool(c.get("max_exclusive")) if hi is not None else False
    return lo, lo_excl, hi, hi_excl


def typical_range(constraints: dict | None) -> tuple[float, float] | None:
    """The ``typical`` guidance range as ``(lo, hi)``, or None when absent."""
    t = (constraints or {}).get("typical")
    if not isinstance(t, (list, tuple)) or len(t) != 2:
        return None
    return t[0], t[1]


def value_in_hard_range(value: object, constraints: dict | None) -> bool:
    """True iff a NUMERIC ``value`` satisfies the hard min/max (exclusivity
    honoured). Non-numeric values, bools and NaN never violate a bound (the
    validator's own value-kind guards decide those; NaN fails the finiteness
    premise instead)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    if value != value:  # NaN
        return True
    lo, lo_excl, hi, hi_excl = hard_bounds(constraints)
    if lo is not None and (value < lo or (lo_excl and value == lo)):
        return False
    if hi is not None and (value > hi or (hi_excl and value == hi)):
        return False
    return True


def format_range(
    lo: float | None,
    lo_exclusive: bool,
    hi: float | None,
    hi_exclusive: bool,
    fmt=str,
) -> str:
    """Interval notation for a hard range: ``[2, 100]``, ``(0, 0.5]``,
    ``[0, inf)``, ``(-inf, inf)``. ``fmt`` renders each finite bound (the TS
    mirror passes its Python-float renderer for the Execution block)."""
    lb = "(" if (lo is None or lo_exclusive) else "["
    rb = ")" if (hi is None or hi_exclusive) else "]"
    lo_s = "-inf" if lo is None else fmt(lo)
    hi_s = "inf" if hi is None else fmt(hi)
    return f"{lb}{lo_s}, {hi_s}{rb}"


def range_phrase(
    lo: float | None,
    lo_exclusive: bool,
    hi: float | None,
    hi_exclusive: bool,
    fmt=str,
) -> str:
    """The English a PARAM_OUT_OF_RANGE suggestion/detail completes with.

    Both bounds → ``in range [lo, hi]`` (brackets per exclusivity); one bound
    → ``greater than lo`` / ``no less than lo`` / ``less than hi`` /
    ``no greater than hi``; none → ``any finite number`` (never emitted — a
    param with no bound is never out of range). Mirrored byte-for-byte by
    ``rangePhrase`` in keel-app ``validator/rules/range-phrase.ts``.
    """
    if lo is not None and hi is not None:
        return f"in range {format_range(lo, lo_exclusive, hi, hi_exclusive, fmt)}"
    if lo is not None:
        return f"{'greater than' if lo_exclusive else 'no less than'} {fmt(lo)}"
    if hi is not None:
        return f"{'less than' if hi_exclusive else 'no greater than'} {fmt(hi)}"
    return "any finite number"


def describe_param_range(constraints: dict | None) -> dict | None:
    """Agent-facing rendering of a param's range, labelled so the two ranges
    cannot be misread for each other (plan step 4 — component detail, SDK).

    ``{"hard_limit": "(0, inf)", "typical_guidance": "[0.001, 0.5]", "note": ...}``
    — the same field names and note as the SDK's ``_param_range.param_range``
    so chat and MCP agents read one shape. ``typical_guidance`` is absent when
    the constraints carry none, and the whole result is None when the param
    has neither a bound nor a typical range. ``hard_limit`` is the only range
    the validator enforces.
    """
    lo, lo_excl, hi, hi_excl = hard_bounds(constraints)
    typ = typical_range(constraints)
    if lo is None and hi is None and typ is None:
        return None
    out: dict = {"hard_limit": format_range(lo, lo_excl, hi, hi_excl)}
    if typ is not None:
        out["typical_guidance"] = format_range(typ[0], False, typ[1], False)
    out["note"] = (
        "hard_limit is the only range the validator rejects; typical_guidance is where "
        "values usually sit - a value outside it is valid."
    )
    return out


__all__ = [
    "TypeFlowEntry",
    "ValidationIssue",
    "ValidationResult",
    "DomainRef",
    "ClockRef",
    "TypeRef",
    "Span",
    "ProvenanceHop",
    "ValidOption",
    "SuggestedEdit",
    "StagingNote",
    "TYPE_TRANSITIONS",
    "ANNOTATED_SEMANTIC_NAMES",
    "semantic_type_name",
    "PHASE_INDEX",
    "type_to_transition_key",
    "validate_transition_coverage",
    "is_compatible",
    "type_name",
    "param_display_type",
    "param_accepts_numeric",
    "param_type_structure",
    "param_type_structure_json",
    "check_param_structure",
    "param_structure_label",
    "value_structure_label",
    "_param_target_types",
    "TIMEFRAME_MINUTES",
    "MINUTES_TO_TOKEN",
    "render_clock",
    "timeframe_to_minutes",
    "parse_bar_offset_minutes",
    "validate_resample_config",
    "synth_offset_consumed",
    "finer_than_native_message",
    "hard_bounds",
    "typical_range",
    "value_in_hard_range",
    "format_range",
    "range_phrase",
    "describe_param_range",
]
