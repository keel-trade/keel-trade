"""DSL spec data structures — the contract between parser, validator, and resolver.

All dataclasses in this module represent the parsed AST of a .strategy file.
They are pure data containers with no methods or validation logic.

Quick Start:
    >>> from pipeline_engine.dsl.spec import StrategyFile, ComponentRef
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pipeline_engine.constants import MISSING  # noqa: F401 — re-export


@dataclass
class SourceLocation:
    """Source position for error messages.

    The context field uses a grammar:
    "step[N]", "branch[name].step[N]", "var[name]", "store[name]",
    "load[name]", "factory[name]", "factory_call[name]", "pipeline"

    ``end_line``/``end_col`` complete the position into a SPAN (Q-1697).
    Both DEFAULT to ``None`` so every existing constructor keeps working and
    a spec built from a GraphModel — which has no source text, and whose
    editor addresses blocks by ``blockId`` rather than by line — keeps
    reporting ``span: null``, which is correct for it. The parser fills them
    from ``ast``'s ``end_lineno``/``end_col_offset``, available on every node
    since Python 3.8 and dropped here since the bootstrap.

    COLUMNS ARE CHARACTER OFFSETS, not ``ast``'s UTF-8 BYTE offsets: the
    parser converts on the way in (``parser._loc``), because every consumer
    of a span slices the SOURCE TEXT with it (`source_lines[line-1][col:end_col]`)
    and a multi-byte character above the step would otherwise shift the
    caret. ``col`` has carried the raw byte offset since the bootstrap and is
    converted with it, so the two halves of a span are in one unit.
    """

    line: int
    col: int
    context: str
    # ``compare=False`` is load-bearing, not tidiness (Q-1697): a
    # SourceLocation's IDENTITY has always been "same start, same context",
    # and keel-api's save path compares parsed declarations field-by-field to
    # decide whether a re-save is a no-op (`universe_criteria`,
    # `test_criteria_ignore_the_resolution_outputs`). The END of a Universe
    # call moves when its baked `resolved=[...]` list changes while the
    # CRITERIA do not — so letting the span into equality would turn every
    # re-resolve into a spurious new commit. The span is positional metadata
    # for rendering a caret, never identity.
    end_line: int | None = field(default=None, compare=False)
    end_col: int | None = field(default=None, compare=False)


@dataclass
class VariableRef:
    """Reference to a previously defined variable."""

    name: str
    location: SourceLocation


@dataclass
class ComponentRef:
    """A reference to a component by name with parameters."""

    name: str
    params: dict[str, Any | VariableRef]
    location: SourceLocation


@dataclass
class SlotStoreSpec:
    """Store current pipeline data to a named slot (DSL syntax, not a component)."""

    slot_name: str
    location: SourceLocation


@dataclass
class SlotStoreValueSpec:
    """Store a fixed literal value into a named slot (DSL syntax)."""

    slot_name: str
    value: Any
    location: SourceLocation


@dataclass
class SlotLoadSpec:
    """Load data from a named slot into pipeline flow (DSL syntax, not a component)."""

    slot_name: str
    location: SourceLocation


@dataclass
class SlotExtractSpec:
    """Extract a single value from a dict (after Parallel). DSL syntax: Extract('key')."""

    key: str
    location: SourceLocation


@dataclass
class ParallelSpec:
    """Parallel branch specification (dict literal in DSL)."""

    branches: dict[str, list[StepSpec]]
    location: SourceLocation


@dataclass
class PipelineSpec:
    """Parsed pipeline specification."""

    steps: list[StepSpec]
    name: str | None
    location: SourceLocation
    #: Position-layer spec 03-R47: the registered-factory call this nested
    #: pipeline is the expansion of (validation only; never compiled or
    #: emitted — the resolver's own expansion carries only the canonical
    #: name). ``None`` for every other pipeline, so existing specs compare
    #: and serialise exactly as before.
    factory_call: "ComponentRef | None" = None
    #: True when the call's expansion was refused (an inexact ``$bars_of``, a
    #: ``$scope`` with no governing TradeManager): the issue is on the call,
    #: and the walk treats the call's output as unknown.
    factory_refused: bool = False


@dataclass
class FactoryParam:
    """A parameter in a factory definition."""

    name: str
    default: Any = field(default=MISSING)
    annotation: str | None = None


@dataclass
class FactoryDef:
    """A parameterized sub-pipeline template."""

    name: str
    params: list[FactoryParam]
    body: PipelineSpec
    location: SourceLocation


@dataclass
class FactoryCallSpec:
    """A call to a DSL-defined factory."""

    name: str
    args: dict[str, Any | VariableRef]
    location: SourceLocation


@dataclass
class VariableAssignment:
    """A variable definition: name = Pipeline(...) or name = literal."""

    name: str
    value: PipelineSpec | Any
    location: SourceLocation


@dataclass
class GlobalsSpec:
    """Top-level Globals declaration — pipeline-wide configuration."""

    target_timeframe: str | None = None
    bar_offset: str | None = None
    location: SourceLocation | None = None


@dataclass
class UniverseSpec:
    """Top-level Universe declaration — asset selection criteria + committed list."""

    mode: str = "manual"
    market: str = "perp"
    symbols: list[str] | None = None
    categories: list[str] | None = None
    top_n: int | None = None
    exclusions: list[str] | None = None
    inclusions: list[str] | None = None
    lookback: str | None = None
    volume_quartiles: list[str] | None = None
    # Dollar-liquidity floor (component-expansion spec 04 §1): keep assets
    # whose trailing-24h traded dollar volume (Σ trade price × size,
    # `SUM(pv_sum)` over `bars_1m`; there is none before 2025-03-23) as of resolution
    # ≥ this value (quote units). A THRESHOLD applied in every mode after
    # mode resolution and before exclusions/inclusions — never a top-N, so
    # the result size varies. Read it through `dollar_volume_floor(u)`, never
    # one field: the value may be declared under either name.
    min_trailing_dollar_volume: float | None = None
    # DEPRECATED alias of `min_trailing_dollar_volume` (dollar-volume DV6b),
    # SAME meaning. Kept as written so a saved strategy parses, compiles and
    # resolves exactly as before; the validator warns
    # (DEPRECATED_UNIVERSE_FIELD) and every writer emits the new name.
    # Declaring both in one Universe is an error (INVALID_UNIVERSE).
    min_trailing_notional_proxy: float | None = None
    resolved: list[str] | None = None
    resolved_at: str | None = None
    groups: dict[str, list[str]] | None = None
    max_leverages: dict[str, float] | None = None
    location: SourceLocation | None = None


#: The markets a strategy may name — Hyperliquid perpetuals, and nothing else
#: (Q-2363; founder ruling 2026-10-04: perp is the only market and the
#: default, and any other value is refused with a clear "not supported").
#: THE owner: ``Universe.market`` and every data loader's ``market_type`` are
#: checked against it (validator UNSUPPORTED_MARKET, the keel-api submit /
#: deploy gate). Nothing downstream honours another market — the simulation
#: and live execution read perps — so a value outside this tuple was never a
#: different market, only a perp run under a false label. libs/db's resolver
#: keeps a pinned twin (``db.queries.universe_resolver.SUPPORTED_MARKETS``;
#: it does not import the DSL).
SUPPORTED_MARKETS: tuple[str, ...] = ("perp",)

#: Markets Hyperliquid lists that Keel does not trade. Naming one is not a
#: typo, so its refusal omits the "Valid" list an unknown value gets.
VENUE_MARKETS_NOT_SUPPORTED: tuple[str, ...] = ("spot",)

#: The dexes a data loader may name (``dex_name``) — Hyperliquid's native
#: perp dex, and nothing else (Q-2414; founder ruling 2026-10-04: refused at
#: validation as "not supported yet"). Every registered loader defaults to
#: ``"native"``. A HIP-3 builder dex (``"xyz"``) or ``None`` ("every dex")
#: was never a different market — the backtest store holds native perps only
#: and execution has no HIP-3 support — only native-perp data under a false
#: label. HIP-3 SYMBOLS are refused separately, per symbol, by the universe
#: classifier (``db.queries.market_data.HIP3_NOT_SUPPORTED``).
SUPPORTED_DEXES: tuple[str, ...] = ("native",)

#: The venues a data loader may name — ``PriceDataLoader`` / ``SymbolLoader``
#: ``exchange`` and the stream loaders' ``source`` — Hyperliquid only
#: (Q-2415). Nothing serves or trades another venue: the cache key carries
#: none and live execution trades Hyperliquid. Twin of
#: ``db.venue_identity.HL_EXCHANGE_SLUG`` (this module does not import
#: libs/db; a test pins the two).
SUPPORTED_EXCHANGES: tuple[str, ...] = ("hyperliquid",)

#: The data-loader parameters the loader half of UNSUPPORTED_MARKET judges,
#: by FIELD (never by class): every data loader whose signature declares one
#: is checked on its static value.
LOADER_MARKET_FIELDS: tuple[str, ...] = ("market_type", "dex_name", "exchange", "source")

#: Every field UNSUPPORTED_MARKET judges → (its supported values, whether
#: ``None`` is the field's own default — "not declared" — and so supported,
#: whether its refusal is "not supported yet"). ``market`` / ``market_type``
#: default to None (perp). ``dex_name`` / ``exchange`` / ``source`` default to
#: a supported string, so an explicit ``None`` is a declaration ("every dex",
#: "no venue") and is refused; an OMITTED loader field never arrives here as
#: None — the validator reads the registered default.
_MARKET_FIELDS: dict[str, tuple[tuple[str, ...], bool, bool]] = {
    "market": (SUPPORTED_MARKETS, True, False),
    "market_type": (SUPPORTED_MARKETS, True, False),
    "dex_name": (SUPPORTED_DEXES, False, True),
    "exchange": (SUPPORTED_EXCHANGES, False, True),
    "source": (SUPPORTED_EXCHANGES, False, True),
}


def unsupported_market_params(value: object, *, field: str = "market") -> dict[str, str] | None:
    """The ``UNSUPPORTED_MARKET`` template params for a declared market, dex
    or venue, or None when it is supported.

    ``value`` is ``Universe.market`` (``field="market"``) or one of a data
    loader's :data:`LOADER_MARKET_FIELDS`. For the two market fields ``None``
    is "not declared" — the default, perp — and is supported; every other
    value, the empty string included, must be in :data:`SUPPORTED_MARKETS`.
    ``dex_name`` must be in :data:`SUPPORTED_DEXES` and ``exchange`` /
    ``source`` in :data:`SUPPORTED_EXCHANGES`, ``None`` included; their
    sentence says "not supported yet" (Q-2414 / Q-2415).
    """
    supported, none_is_default, not_yet = _MARKET_FIELDS[field]
    if value is None and none_is_default:
        return None
    if isinstance(value, str) and value in supported:
        return None
    rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
    if not_yet:
        return {
            "field": field,
            "value": rendered,
            "yet": " yet",
            "scope": "native Hyperliquid perpetuals",
            "supported": json.dumps(supported[0]),
            "valid_note": "",
        }
    valid_note = (
        "" if value in VENUE_MARKETS_NOT_SUPPORTED else f" Valid: {', '.join(SUPPORTED_MARKETS)}."
    )
    return {
        "field": field,
        "value": rendered,
        "yet": "",
        "scope": "Hyperliquid perpetuals",
        "supported": json.dumps(SUPPORTED_MARKETS[0]),
        "valid_note": valid_note,
    }


def unsupported_market_message(value: object, *, field: str = "market") -> str | None:
    """The one ``UNSUPPORTED_MARKET`` sentence (the catalog's template), or
    None when ``value`` is supported — e.g. ``market="spot" is not supported —
    Keel trades Hyperliquid perpetuals only. Remove market= or use
    market="perp".`` or ``dex_name="xyz" is not supported yet — Keel trades
    native Hyperliquid perpetuals only. Remove dex_name= or use
    dex_name="native".`` Surfaces outside the validator (the keel-api gates)
    render through here so they cannot word it differently."""
    params = unsupported_market_params(value, field=field)
    if params is None:
        return None
    from pipeline_engine.dsl.catalog import RULES

    return RULES["UNSUPPORTED_MARKET"].message_template.format(**params)


#: The Universe dollar-volume floor field (dollar-volume DV6b).
DOLLAR_VOLUME_FLOOR_FIELD = "min_trailing_dollar_volume"

#: Deprecated Universe field names → the field each is an alias of. An alias
#: has EXACTLY its replacement's meaning; it is accepted by every surface that
#: parses a declaration and emitted by none. The one alias today:
#: ``min_trailing_notional_proxy`` — named for the candle proxy it measured
#: before DV1 (2026-09-24) made the floor traded dollar volume.
UNIVERSE_FIELD_ALIASES: dict[str, str] = {
    "min_trailing_notional_proxy": DOLLAR_VOLUME_FLOOR_FIELD,
}


class AmbiguousUniverseFieldError(ValueError):
    """A declaration sets a field AND its deprecated alias — which one wins is
    not a question the platform answers (the validator reports it as
    ``INVALID_UNIVERSE``; the resolver refuses it)."""


def ambiguous_universe_fields(declaration: object) -> list[tuple[str, str]]:
    """Every ``(alias, replacement)`` pair BOTH set on ``declaration``.

    ``declaration`` is anything carrying the fields as attributes
    (``UniverseSpec``, keel-api's ``ResolveUniverseRequest``) or a ``dict``
    (the compiled blob's ``universe`` map, a graph's universe).
    """

    def _get(name: str) -> Any:
        if isinstance(declaration, dict):
            return declaration.get(name)
        return getattr(declaration, name, None)

    return [
        (alias, target)
        for alias, target in UNIVERSE_FIELD_ALIASES.items()
        if _get(alias) is not None and _get(target) is not None
    ]


def dollar_volume_floor(declaration: object) -> float | None:
    """The declared dollar-volume floor, whichever name declared it.

    The ONE reader of the floor: ``min_trailing_dollar_volume``, or its
    deprecated alias ``min_trailing_notional_proxy`` (same meaning). Works on
    an attribute carrier or a ``dict`` (see :func:`ambiguous_universe_fields`).

    Raises:
        AmbiguousUniverseFieldError: both names are set. Never silently
            picks one — the validator reports this before any reader runs.
    """
    if ambiguous_universe_fields(declaration):
        raise AmbiguousUniverseFieldError(
            "Universe declares both min_trailing_dollar_volume and its deprecated "
            "alias min_trailing_notional_proxy — set only min_trailing_dollar_volume."
        )
    if isinstance(declaration, dict):
        new = declaration.get(DOLLAR_VOLUME_FLOOR_FIELD)
        old = declaration.get("min_trailing_notional_proxy")
    else:
        new = getattr(declaration, DOLLAR_VOLUME_FLOOR_FIELD, None)
        old = getattr(declaration, "min_trailing_notional_proxy", None)
    return new if new is not None else old


def dollar_volume_floor_to_emit(universe: UniverseSpec) -> list[tuple[str, Any]]:
    """The floor as a WRITER emits it: ``[(name, value)]``, possibly empty.

    A declared alias is written under its replacement's name (DV6b: every
    writer emits ``min_trailing_dollar_volume``). A declaration carrying BOTH
    names is emitted as written — rewriting it would pick a winner and hide
    the ambiguity the validator exists to report.
    """
    new = universe.min_trailing_dollar_volume
    old = universe.min_trailing_notional_proxy
    if new is not None and old is not None:
        return [(DOLLAR_VOLUME_FLOOR_FIELD, new), ("min_trailing_notional_proxy", old)]
    value = new if new is not None else old
    return [] if value is None else [(DOLLAR_VOLUME_FLOOR_FIELD, value)]


#: UniverseSpec fields that are the RESULT of a resolution, not its inputs:
#: the list, the instant it answers for, and the venue leverage map read
#: alongside it. They answer the criteria they were resolved for and no other.
UNIVERSE_RESOLUTION_FIELDS: frozenset[str] = frozenset({"resolved", "resolved_at", "max_leverages"})

#: UniverseSpec fields that are positional render metadata, never identity
#: (Q-1697: "the span is positional metadata for rendering a caret, never
#: identity"). The parse POSITION of the call moves with any edit above it.
UNIVERSE_POSITIONAL_FIELDS: frozenset[str] = frozenset({"location"})


def universe_criteria(universe: UniverseSpec) -> tuple:
    """The declaration's selection criteria as a comparable tuple — THE one
    "are these the same universe criteria?" owner (Q-2435).

    Every ``UniverseSpec`` field EXCEPT the resolution outputs
    (:data:`UNIVERSE_RESOLUTION_FIELDS`) and the parse position
    (:data:`UNIVERSE_POSITIONAL_FIELDS`). Derived from the dataclass, so a
    field added to ``UniverseSpec`` is compared by default (the fail-safe
    direction: an unknown field reads as "changed", which re-resolves); the
    spec test pins every field's classification so a new one is a decision.

    Three callers share it, so they cannot disagree: keel-api's PRIOR pin
    (an unchanged re-save keeps HEAD's resolution), keel-api's carried-list
    backstop (HEAD's list under different criteria is re-resolved), and
    :func:`universe_spec_with_declared_state` (both ``universe_set`` twins
    carry the resolution only when the criteria are unchanged).

    ``location`` is excluded because a comment line added above the Universe
    moved it and, with it, the whole tuple: the PRIOR pin then declined and an
    unrelated edit re-resolved the universe (measured in prod, 2026-09-21), and
    the constructor (which builds a spec with no location) could never equal a
    parsed declaration.

    A deprecated alias and its replacement are ONE criterion (DV6b): a re-save
    whose only change is the writer renaming the floor resolves identically.
    Both declared stays two entries — ambiguous is never "equal" to either
    spelling.
    """

    def _freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(_freeze(v) for v in value)
        return value

    from dataclasses import fields as _fields

    excluded = UNIVERSE_RESOLUTION_FIELDS | UNIVERSE_POSITIONAL_FIELDS
    values = {
        f.name: getattr(universe, f.name) for f in _fields(universe) if f.name not in excluded
    }
    for alias, target in UNIVERSE_FIELD_ALIASES.items():
        if values.get(target) is None and values.get(alias) is not None:
            values[target], values[alias] = values[alias], None
    return tuple((name, _freeze(value)) for name, value in values.items())


def universe_spec_with_declared_state(
    existing: UniverseSpec | None,
    *,
    mode: str,
    market: str = "perp",
    symbols: list[str] | None = None,
    categories: list[str] | None = None,
    top_n: int | None = None,
    exclusions: list[str] | None = None,
    inclusions: list[str] | None = None,
    lookback: str | None = None,
    volume_quartiles: list[str] | None = None,
    min_trailing_dollar_volume: float | None = None,
) -> UniverseSpec:
    """The replacement Universe for a CRITERIA edit — carrying what it is not editing.

    A ``universe set`` writes criteria. Everything else the declaration
    carries is STATE the caller said nothing about, and rebuilding the spec
    from the call's arguments alone deletes it silently:

    - ``groups`` — hand-authored symbol groups; ALWAYS carried;
    - ``min_trailing_dollar_volume`` — the dollar-liquidity floor, so the NEXT
      resolve silently returns a different universe; carried unless replaced;
    - ``resolved`` / ``resolved_at`` / ``max_leverages`` — the RESOLUTION.
      Carried only when the call leaves the criteria as declared
      (:func:`universe_criteria` equal): an edit that changes nothing must
      not un-resolve the strategy (``UNRESOLVED_UNIVERSE`` at the next
      submit, naming nothing the user did — audit 04 U-18).

    When the criteria DID change, the resolution is written as None (Q-2435).
    The old list answers the old criteria: carried, the save stored it
    verbatim, so "exclude BTC" still traded BTC and a switch to the "defi"
    category ran the previous top-20 under the new label. Every save resolves
    a Universe that carries no list (keel-api's bake, Q-1504), so dropping it
    costs nothing; the SDK's default ``universe_set`` resolves in the same
    call. Until 2026-10-03 a separate ``universe_resolve`` step followed every
    ``universe_set`` and replaced the carried list; Q-2283 removed that step,
    which is what turned the carry into a defect.

    Both ``universe_set`` implementations build their spec here (the rich
    ``pipeline_engine.mcp.tools`` one and the SDK's lightweight
    ``keel.tools.local`` twin, which reaches it through the vendored
    ``pipeline_engine.dsl`` subset), so the two cannot drift again: audit 04
    U-18 found the rich one dropping the floor and the leverage map, and the
    SDK one dropping those PLUS ``resolved``/``resolved_at``/``groups``.

    The floor is the one carried field with an argument: passing it REPLACES
    the declared value, omitting it KEEPS it. Criteria arguments are always
    written as given — including None, which is how a criterion is cleared.
    This is a WRITER: a floor carried from the deprecated alias
    ``min_trailing_notional_proxy`` is written as ``min_trailing_dollar_volume``
    (same meaning, DV6b). A declaration carrying both names is ambiguous and
    raises :class:`AmbiguousUniverseFieldError` unless the call replaces the
    floor.
    """
    candidate = UniverseSpec(
        mode=mode,
        market=market,
        symbols=symbols,
        categories=categories,
        top_n=top_n,
        exclusions=exclusions,
        inclusions=inclusions,
        lookback=lookback,
        volume_quartiles=volume_quartiles,
        min_trailing_dollar_volume=(
            min_trailing_dollar_volume
            if min_trailing_dollar_volume is not None
            else (dollar_volume_floor(existing) if existing else None)
        ),
        groups=existing.groups if existing else None,
    )
    if existing is not None and universe_criteria(candidate) == universe_criteria(existing):
        candidate.resolved = existing.resolved
        candidate.resolved_at = existing.resolved_at
        candidate.max_leverages = existing.max_leverages
    return candidate


@dataclass
class ExecutionSpec:
    """Top-level Execution declaration — how target weights translate into trades.

    ``explicit`` records which params the author actually set — keys present
    in the ``Execution(...)`` call (parser) or in the graph's execution dict
    (``graph_to_spec``). The other fields are back-filled registry defaults,
    indistinguishable from user input by value alone; ``explicit`` is what
    lets the emit policy (``execution_params_to_emit``) keep exactly what the
    user wrote instead of silently dropping mode-irrelevant params (B6).
    Programmatic constructors that want their params emitted must populate
    ``explicit`` — an empty set emits only ``always_emit`` params.
    """

    rebalance: str = "every_bar"
    on_change_tolerance: float = 1e-8
    buffer_threshold: float | None = None
    buffer_mode: str = "relative"
    rebalance_method: str = "to_center"
    min_trade_size: float = 0.0
    location: SourceLocation | None = None
    explicit: frozenset[str] = frozenset()


# ── Canonical metadata for Execution params ──────────────────────────────
# Single source of truth. Emitters, validators, UI, and consumers all
# derive their behavior from this registry. To add a param: add the field
# to ExecutionSpec above, then add an entry here. Everything else adapts.

EXECUTION_PARAM_META: dict[str, dict] = {
    "rebalance": {
        "type": "select",
        "default": "every_bar",
        "options": ["every_bar", "on_change", "buffered"],
        "always_emit": True,
        "description": "When the engine trades. every_bar: every bar. on_change: only when weights change. buffered: only when positions drift outside a buffer band.",
    },
    "on_change_tolerance": {
        "type": "number",
        "default": 1e-8,
        # Numeric range (spec 02 T-15): min/max/step live HERE, the single
        # source of truth. The Python validator, the TS editor validator
        # (rules/declarations.ts via the generated execution_param_meta.json),
        # and keel-api's /components/metadata all derive their range checks
        # from these keys — the three hardcoded literal copies are deleted.
        #
        # Shape since Q-2242 (param-domains plan, frozen format): `min`/`max`
        # are HARD limits, either may be absent (unbounded), `min_exclusive`
        # / `max_exclusive` only present when true, `typical` is guidance
        # (the old [min, max]) that no validator reads. spec 02 §Execution:
        # the consumers (nb/simulation.py:207 skip when |dw| < tol;
        # eval-worker handler admit when |dw| >= tol) have no upper limit and
        # tol=0 means "every change trades" — a valid, documented setting.
        "min": 0.0,
        "typical": [1e-12, 1e-4],
        "modes": ["on_change"],
        "description": "Weight changes smaller than this are ignored. Default 1e-8 filters floating-point noise.",
    },
    "buffer_threshold": {
        "type": "number",
        "default": None,
        # Hard range (0, inf) since Q-2242 — spec 02 §Execution: no consumer
        # has a numerical floor or ceiling (Q-0636 already found the band
        # math has none; a tiny band degenerates toward every_bar), and
        # brokers/base.py:282 treats threshold <= 0 as "buffer DISABLED",
        # which is why 0 itself is excluded — a buffered mode with no buffer
        # is a configuration error, not a band. The old [0.001, 0.5] (a
        # daily-frequency sanity rail) is now the typical guidance range.
        "min": 0.0,
        "min_exclusive": True,
        "typical": [0.001, 0.5],
        "step": 0.001,
        "modes": ["buffered"],
        "required_for": ["buffered"],
        "description": "Width of the no-trade buffer band. Fraction of target (relative), portfolio value (absolute), or trailing mean |target weight| (reference). Typical: 0.05-0.30 daily; cost-referenced intraday bands may need 0.001-0.01.",
    },
    "buffer_mode": {
        "type": "select",
        "default": "relative",
        "options": ["relative", "absolute", "reference"],
        "modes": ["buffered"],
        "description": "How the buffer band is computed. relative: fraction of target position size. absolute: fraction of portfolio value. reference: fraction of the asset's trailing 30-day mean |target weight| — proportional tolerance that does not pinch to zero as a signal decays.",
    },
    "rebalance_method": {
        "type": "select",
        "default": "to_center",
        "options": ["to_edge", "to_center"],
        "modes": ["buffered"],
        "description": "When position breaches the buffer: to_edge trades minimum to nearest band edge (20-40% less turnover). to_center trades all the way to target.",
    },
    "min_trade_size": {
        "type": "number",
        "default": 0.0,
        # Hard [0, inf) since Q-2242 — spec 02 §Execution: brokers/base.py:283
        # `weight_floored = min_trade_size > 0` (0 = disabled, documented); a
        # floor above 0.1 of portfolio weight is unusual but not invalid.
        "min": 0.0,
        "typical": [0.0, 0.1],
        "step": 0.001,
        "description": "Minimum trade size as portfolio weight fraction. Trades smaller than this are skipped. 0 = disabled.",
    },
}


def execution_params_to_emit(execution: ExecutionSpec) -> list[tuple[str, Any]]:
    """THE single Execution emit-inclusion policy (B6 fix) — both serializers use it.

    Returns ``(param_name, value)`` pairs in ``EXECUTION_PARAM_META`` order.
    A param is emitted iff:

    - it is flagged ``always_emit`` (``rebalance``), OR it is in
      ``execution.explicit`` (the user set it), AND
    - its value is not ``None`` (``None`` means unset — it is also not
      representable in the graph dict, where JSON ``null`` and absent are
      both read as "not set" by the TS emitter).

    ``spec_to_dsl`` renders this list as ``Execution(...)`` args and
    ``spec_to_graph`` dict-ifies the *same* list, so one save can never again
    persist a DSL and a graph that disagree about the Execution param set
    (final report B6: ``spec_to_dsl`` dropped set-but-mode-irrelevant params
    that ``spec_to_graph`` kept). Mode-irrelevant explicit params are KEPT —
    the validator's ``IRRELEVANT_EXECUTION_PARAM`` advisory informs instead
    of the emitter deleting user data.
    """
    out: list[tuple[str, Any]] = []
    for param_name, meta in EXECUTION_PARAM_META.items():
        if not meta.get("always_emit") and param_name not in execution.explicit:
            continue
        val = getattr(execution, param_name)
        if val is None:
            continue
        out.append((param_name, val))
    return out


# Derived constants (use these instead of hardcoding param names)
EXECUTION_PARAM_NAMES: set[str] = set(EXECUTION_PARAM_META.keys())
EXECUTION_VALID_REBALANCE: set[str] = set(EXECUTION_PARAM_META["rebalance"]["options"])
EXECUTION_VALID_BUFFER_MODE: set[str] = set(EXECUTION_PARAM_META["buffer_mode"]["options"])
EXECUTION_VALID_REBALANCE_METHOD: set[str] = set(
    EXECUTION_PARAM_META["rebalance_method"]["options"]
)


@dataclass
class StrategyFile:
    """Top-level parsed strategy file."""

    metadata: dict[str, str]
    factories: list[FactoryDef]
    variables: list[VariableAssignment]
    pipeline: PipelineSpec
    globals_: GlobalsSpec | None = None
    universe: UniverseSpec | None = None
    execution: ExecutionSpec | None = None


# Union type for steps in a pipeline
StepSpec = (
    ComponentRef
    | ParallelSpec
    | PipelineSpec
    | VariableRef
    | SlotStoreSpec
    | SlotStoreValueSpec
    | SlotLoadSpec
    | SlotExtractSpec
    | FactoryCallSpec
)


__all__ = [
    "DOLLAR_VOLUME_FLOOR_FIELD",
    "LOADER_MARKET_FIELDS",
    "MISSING",
    "SUPPORTED_DEXES",
    "SUPPORTED_EXCHANGES",
    "SUPPORTED_MARKETS",
    "UNIVERSE_FIELD_ALIASES",
    "UNIVERSE_POSITIONAL_FIELDS",
    "UNIVERSE_RESOLUTION_FIELDS",
    "VENUE_MARKETS_NOT_SUPPORTED",
    "AmbiguousUniverseFieldError",
    "ComponentRef",
    "ExecutionSpec",
    "FactoryCallSpec",
    "FactoryDef",
    "FactoryParam",
    "GlobalsSpec",
    "ParallelSpec",
    "PipelineSpec",
    "SlotLoadSpec",
    "SlotExtractSpec",
    "SlotStoreSpec",
    "SlotStoreValueSpec",
    "SourceLocation",
    "StepSpec",
    "StrategyFile",
    "UniverseSpec",
    "VariableAssignment",
    "VariableRef",
    "ambiguous_universe_fields",
    "dollar_volume_floor",
    "dollar_volume_floor_to_emit",
    "execution_params_to_emit",
    "universe_criteria",
    "universe_spec_with_declared_state",
    "unsupported_market_message",
    "unsupported_market_params",
]
