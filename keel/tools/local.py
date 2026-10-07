"""Local tool implementations — capability-detected delegation to pipeline_engine.

═════════════════════════════════════════════════════════════════════════════
                          ⚠  DUPLICATION BOUNDARY  ⚠
═════════════════════════════════════════════════════════════════════════════

Every public function here has a sibling in the upstream `pipeline_engine.mcp.tools` module
(used by chat-api). The TWO IMPLEMENTATIONS EXIST BY DESIGN:

* **the upstream `pipeline_engine.mcp.tools` module** — the rich, full-fat orchestrator.
  Imports the resolver, introspection, pipeline.compile (pandas, numpy,
  ta-lib). Lives in-cluster (chat-api, keel-api workers). 2070 LOC.

* **`keel/tools/local.py`** — the lightweight re-implementation. Reads the
  bundled `keel/data/registry.json` snapshot, only depends on
  `pipeline_engine.dsl.*` (parser/validator/emitter — copied subset, NO
  pipeline.compile). Ships in the pipx-installed wheel. 778 LOC.

The SDK CANNOT bundle the rich orchestrator because `pipeline.compile`
pulls in the full execution engine (pandas/numpy/ta-lib), which would
make `pipx install keel-trade` an impossibly heavy install. The SDK
build script (`scripts/build_data.py`) intentionally excludes `mcp/`,
`resolver.py`, `introspection.py`, `pipeline/`, and `registry_loader.py`
from the bundle.

POLICY FOR EDITING TOOL SEMANTICS:

  1. If you fix a bug or change behaviour in any function here, check
     the same-named function in the upstream `pipeline_engine.mcp.tools` module and
     apply the same fix there. Same in reverse.
  2. When the bundled `keel/data/registry.json` snapshot gets stale,
     regenerate via `PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py`.
  3. The parity test `tests/test_implementations_parity.py` is the
     contract — when both implementations are importable in the same
     env, both must produce the same shape of output. The test SKIPS
     gracefully when only one is reachable (which is the common case
     today; the upstream `pipeline_engine` gets shadowed by the SDK-bundled
     copy in our dev env, and `libs/` isn't present at all in the
     pipx-installed wheel).

The `_delegate_or_fallback` helper below picks rich automatically when
`pipeline_engine.mcp.tools` is importable, else falls back to bundled.
Today this is a no-op in every env (rich always shadowed or absent),
but the seam is in place: any future env where both coexist will
collapse the divergence automatically. See
`projects/agent-v2/06-prod-readiness-followups.md` for the multi-day
unification proposal.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from functools import lru_cache
from typing import Any, Callable

from keel.data.registry import (
    _ensure_loaded,
)
from keel.data.registry import (
    get_component_detail as _get_detail,
)
from keel.data.registry import (
    get_components_after as _get_after,
)
from keel.data.registry import (
    get_components_before as _get_before,
)
from keel.data.registry import (
    get_components_dump as _get_dump,
)
from keel.data.registry import (
    search_components as _search,
)


_LOGGER = logging.getLogger(__name__)


def _ensure_registry():
    """Ensure the bundled component registry is loaded (no-op when rich
    path is active — `pipeline_engine` owns its own registry hydration)."""
    _ensure_loaded()


# ─── Capability detection — single source of truth for all delegations ──


@lru_cache(maxsize=1)
def _rich_module():
    """Return `pipeline_engine.mcp.tools` if importable, else None.

    Cached: importability of `pipeline_engine` is determined by the Python
    env at process start, so a single check per process is enough.
    """
    try:
        return importlib.import_module("pipeline_engine.mcp.tools")
    except ImportError:
        return None


def _delegate_or_fallback(fn_name: str, fallback: Callable[..., Any], /, **kwargs: Any):
    """Call `pipeline_engine.mcp.tools.{fn_name}(**kwargs)` if available,
    else invoke `fallback(**kwargs)`.

    Both implementations may have slightly different keyword-only sets
    (rich uses explicit kwargs; bundled accepts catch-all). We filter
    `kwargs` to whichever target's signature accepts them so a stray
    arg doesn't trigger an `unexpected_keyword_argument` error at the
    seam.

    Real errors from either implementation (DSL parse errors,
    NotFoundError, etc.) propagate unchanged — only `ImportError` on
    the rich side falls back to bundled. Anything else surfaces to the
    caller so legitimate failures aren't masked.
    """
    rich_mod = _rich_module()
    rich_fn = getattr(rich_mod, fn_name, None) if rich_mod is not None else None
    target = rich_fn if rich_fn is not None else fallback
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        # Builtins/C functions — pass kwargs through unfiltered.
        return target(**kwargs)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if has_var_keyword:
        return target(**kwargs)
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return target(**accepted)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


def strategy_components_search(**kwargs) -> list[dict[str, Any]]:
    """Search components by criteria. Delegates to pipeline_engine if available."""
    _ensure_registry()
    return _delegate_or_fallback("strategy_components_search", _search, **kwargs)


def strategy_component_detail(
    name: str, component_lock: dict[str, int] | None = None
) -> dict[str, Any]:
    """Get full specification for a component."""
    _ensure_registry()
    return _delegate_or_fallback(
        "strategy_component_detail",
        _get_detail,
        name=name,
        component_lock=component_lock,
    )


def strategy_components_after(name: str) -> list[dict[str, Any]]:
    """Find components that can follow a given component."""
    _ensure_registry()
    return _delegate_or_fallback("strategy_components_after", _get_after, name=name)


def strategy_components_before(name: str) -> list[dict[str, Any]]:
    """Find components that can precede a given component."""
    _ensure_registry()
    return _delegate_or_fallback("strategy_components_before", _get_before, name=name)


def strategy_components_dump() -> list[dict[str, Any]]:
    """Bulk dump of all components."""
    _ensure_registry()
    return _delegate_or_fallback("strategy_components_dump", _get_dump)


def dsl_reference(topic: str | None = None) -> dict[str, Any]:
    """Load DSL reference documentation.

    Not delegated: the bundled `keel/data/reference/` source ships with
    the wheel and is the canonical doc surface for the SDK. The rich
    `pipeline_engine.mcp.tools.dsl_reference` reads a different source
    (the upstream pipeline_engine reference) and the shape evolved
    independently — parity not yet verified.
    """
    from keel.data.reference import load_reference

    return load_reference(topic)


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


def parse_error_issue(exc: Exception) -> dict[str, Any]:
    """A parse failure as ONE validation issue, in the envelope's own shape.

    Until Q-1840 the parse arm returned `{severity, message}` and nothing
    else — no code, so the text block had no `keel_help rule:<CODE>` pointer
    and the model had nowhere to look (R3 probe, 2026-09-23). The code is
    the parser's own when it carries one (the parse-tier codes, Q-1695),
    else the catalog's gate code `PARSE_ERROR`; the position rides as
    `location` so a renderer can name the line without re-parsing the text.
    """
    issue: dict[str, Any] = {
        "severity": "error",
        "code": getattr(exc, "code", None) or "PARSE_ERROR",
        "message": str(exc),
    }
    suggestion = getattr(exc, "suggestion", None)
    if isinstance(suggestion, str) and suggestion:
        issue["suggestion"] = suggestion
    line = getattr(exc, "line", None)
    if isinstance(line, int):
        location: dict[str, Any] = {"line": line}
        col = getattr(exc, "col", None)
        if isinstance(col, int):
            location["col"] = col
        issue["location"] = location
    return issue


def strategy_validate(
    source: str,
    component_lock: dict[str, int] | None = None,
    *,
    pre_save: bool = False,
    **kwargs,
) -> dict[str, Any]:
    """Validate a strategy source — full 9-pass validation.

    ``pre_save=True`` is for a DRY RUN of a save (the compose dry run): the
    save resolves a criteria universe server-side, so the engine reports
    UNRESOLVED_UNIVERSE as info there (review 06 §3.2 #4). Saves, the
    validate tool and every backtest gate keep the default full severity.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy, validate_strategy
    from pipeline_engine.dsl.parser import DSLParseError

    try:
        parsed = parse_strategy(source)
    except DSLParseError as e:
        issue = parse_error_issue(e)
        return {
            "valid": False,
            "issues": [issue],
            "errors": [issue],
            "warnings": [],
            "type_flow": [],
        }

    result = validate_strategy(parsed, lock=component_lock, pre_save=pre_save, source=source)

    errors = [i.to_dict() for i in result.errors]
    warnings = [i.to_dict() for i in result.warnings]
    info = [i.to_dict() for i in result.info]

    # Build globals dict
    globals_dict: dict[str, Any] | None = None
    if parsed.globals_:
        globals_dict = {
            k: v for k, v in vars(parsed.globals_).items() if k != "location" and v is not None
        }

    # Build universe dict
    universe_dict: dict[str, Any] | None = None
    if parsed.universe:
        universe_dict = _universe_to_dict(parsed.universe)

    return {
        "valid": result.valid,
        "issues": errors + warnings,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "type_flow": [e.to_dict() for e in result.type_flow],
        "pipeline_summary": result.pipeline_summary,
        "component_lock": component_lock,
        "globals": globals_dict,
        "universe": universe_dict,
    }


def strategy_explain(
    source: str,
    component_lock: dict[str, int] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Explain a strategy's structure — lightweight version using parser + registry.

    This is a reimplementation of pipeline_engine.dsl.explainer without
    the describe_step/registry_loader/introspection imports.
    """
    _ensure_registry()

    from pipeline_engine.base.registry import get_latest, get_version
    from pipeline_engine.dsl import parse_strategy, validate_strategy
    from pipeline_engine.dsl.parser import DSLParseError
    from pipeline_engine.dsl.spec import (
        ComponentRef,
        FactoryCallSpec,
        ParallelSpec,
        PipelineSpec,
        SlotLoadSpec,
        SlotStoreSpec,
        SlotStoreValueSpec,
        VariableRef,
    )
    from pipeline_engine.validation_shared import type_name

    try:
        parsed = parse_strategy(source)
    except DSLParseError as e:
        return {
            "strategy_name": None,
            "step_count": 0,
            "valid": False,
            "issues": [{"severity": "error", "message": str(e)}],
            "type_flow": [],
            "slot_usage": {"stores": [], "loads": []},
            "factories": [],
            "variables": [],
            "steps": [],
            "summary": f"Parse error: {e}",
        }

    result = validate_strategy(parsed, lock=component_lock, source=source)

    def _serialize_args(args):
        out = {}
        for k, v in args.items():
            if isinstance(v, VariableRef):
                out[k] = f"${v.name}"
            else:
                out[k] = v
        return out

    def _explain_step(step):
        if isinstance(step, ComponentRef):
            info = {
                "type": "component",
                "name": step.name,
                "params": _serialize_args(step.params),
            }
            lock = component_lock or {}
            if step.name in lock:
                sig = get_version(step.name, lock[step.name])
                if sig is None:
                    sig = get_latest(step.name)
            else:
                sig = get_latest(step.name)
            if sig:
                info["category"] = sig.category.value
                info["input_type"] = type_name(sig.input_type)
                info["output_type"] = type_name(sig.output_type)
                info["description"] = (sig.description or "").strip().split("\n\n")[0].strip()
            else:
                info["category"] = "unknown"
                info["description"] = f"Component '{step.name}' not found"
            return info
        elif isinstance(step, ParallelSpec):
            return {
                "type": "parallel",
                "branch_count": len(step.branches),
                "branches": {
                    n: [_explain_step(s) for s in steps] for n, steps in step.branches.items()
                },
            }
        elif isinstance(step, PipelineSpec):
            return {
                "type": "pipeline",
                "name": step.name,
                "steps": [_explain_step(s) for s in step.steps],
            }
        elif isinstance(step, SlotStoreSpec):
            return {"type": "store", "slot_name": step.slot_name}
        elif isinstance(step, SlotStoreValueSpec):
            return {"type": "store_value", "slot_name": step.slot_name, "value": step.value}
        elif isinstance(step, SlotLoadSpec):
            return {"type": "load", "slot_name": step.slot_name}
        elif isinstance(step, FactoryCallSpec):
            return {"type": "factory_call", "name": step.name, "args": _serialize_args(step.args)}
        elif isinstance(step, VariableRef):
            return {"type": "variable_ref", "name": step.name}
        else:
            return {"type": "unknown"}

    def _count_steps(steps):
        count = 0
        for step in steps:
            count += 1
            if isinstance(step, ParallelSpec):
                for bs in step.branches.values():
                    count += _count_steps(bs)
            elif isinstance(step, PipelineSpec):
                count += _count_steps(step.steps)
        return count

    stores, loads = [], []

    def _collect_slots(steps):
        for step in steps:
            if isinstance(step, SlotStoreSpec):
                stores.append(step.slot_name)
            elif isinstance(step, SlotStoreValueSpec):
                stores.append(step.slot_name)
            elif isinstance(step, SlotLoadSpec):
                loads.append(step.slot_name)
            elif isinstance(step, ParallelSpec):
                for bs in step.branches.values():
                    _collect_slots(bs)
            elif isinstance(step, PipelineSpec):
                _collect_slots(step.steps)

    _collect_slots(parsed.pipeline.steps)
    steps_explained = [_explain_step(s) for s in parsed.pipeline.steps]
    step_count = _count_steps(parsed.pipeline.steps)
    strategy_name = parsed.metadata.get("name")

    factories = [
        {
            "name": f.name,
            "params": [{"name": p.name, "default": p.default} for p in f.params],
            "step_count": _count_steps(f.body.steps),
        }
        for f in parsed.factories
    ]

    variables = [
        {"name": v.name, "is_pipeline": isinstance(v.value, PipelineSpec)} for v in parsed.variables
    ]

    summary_parts = [f"Strategy '{strategy_name or 'unnamed'}': {step_count} steps"]
    if factories:
        summary_parts.append(f"{len(factories)} factories")
    if variables:
        summary_parts.append(f"{len(variables)} variables")
    if stores:
        summary_parts.append(f"stores: {', '.join(sorted(set(stores)))}")
    if loads:
        summary_parts.append(f"loads: {', '.join(sorted(set(loads)))}")
    summary_parts.append("valid" if result.valid else f"{len(result.errors)} errors")

    return {
        "strategy_name": strategy_name,
        "step_count": step_count,
        "valid": result.valid,
        "issues": [i.to_dict() for i in result.errors + result.warnings],
        "type_flow": [e.to_dict() for e in result.type_flow],
        "slot_usage": {"stores": sorted(set(stores)), "loads": sorted(set(loads))},
        "factories": factories,
        "variables": variables,
        "steps": steps_explained,
        "summary": " | ".join(summary_parts),
    }


def strategy_diff(source_a: str, source_b: str) -> dict[str, Any]:
    """Structural diff between two strategies."""
    _ensure_registry()

    from pipeline_engine.dsl.differ import diff_strategies

    return diff_strategies(source_a=source_a, source_b=source_b)


def pipeline_stage(source: str) -> dict[str, Any]:
    """Assess pipeline completeness toward backtest readiness.

    The TERMINAL type and the READINESS verdict come from the shared owner
    (`pipeline_engine.dsl.validator`, Q-1699). This function used to
    reimplement both, and the two implementations disagreed twice over:

      * it returned `stage: "invalid"` for ANY strategy with errors — losing
        the stage the caller asked for — while the hosted twin reported
        `stage: complete, backtest_ready: True` on the very same seeded
        SLOT_REF_NOT_FOUND / TYPE_MISMATCH sources. Now BOTH report the
        stage AND refuse readiness, because readiness is one function.
      * both read `type_flow[-1]`, which names the last BRANCH step of a
        terminal Parallel — so mistake M-12 read as a normal
        work-in-progress signal in every surface that asked.

    The stage NAMES stay local: they are this surface's vocabulary
    (`portfolio` / `execution`), not a platform fact.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy, validate_strategy
    from pipeline_engine.dsl.parser import DSLParseError
    from pipeline_engine.dsl.validator import is_backtest_ready, terminal_output_name

    try:
        parsed = parse_strategy(source)
    except DSLParseError as e:
        return {"stage": "unparseable", "error": str(e)}

    result = validate_strategy(parsed, source=source)

    # Map the final output type to a readiness stage
    stage_map = {
        "OHLCVDict": "data",
        "StreamSeries": "data",
        "SignalSeries": "signal",
        "NormalizedSignal": "signal",
        "BinarySignal": "signal",
        "RankSignal": "signal",
        "ForecastSeries": "forecast",
        "WeightSeries": "portfolio",
        "OrderSeries": "execution",
        # A pipeline that ENDS at a Parallel emits dict[branch -> result],
        # which nothing trades — its own stage, never "signal".
        "dict": "parallel",
    }

    final_type = terminal_output_name(parsed, result.type_flow) or "unknown"
    stage = stage_map.get(final_type, "unknown")

    out: dict[str, Any] = {
        "stage": stage,
        "final_output_type": final_type,
        "backtest_ready": is_backtest_ready(parsed, result),
        "valid": result.valid,
        "step_count": len(result.type_flow),
        "type_flow": [e.to_dict() for e in result.type_flow],
    }
    if result.errors:
        out["errors"] = [i.to_dict() for i in result.errors]
    return out


def strategy_examples(**kwargs) -> dict[str, Any]:
    """Browse or search strategy examples."""
    from keel.data.examples import strategy_examples as _examples

    return _examples(**kwargs)


def composition_patterns(query: str) -> dict[str, Any]:
    """Search composition patterns by query."""
    from keel.data.patterns import search_patterns

    patterns = search_patterns(query)
    return {"patterns": patterns, "query": query, "match_count": len(patterns)}


def strategy_new_inline(
    name: str,
    template: str = "basic",
    strategy_dir: str | None = None,
) -> dict[str, Any]:
    """Create a new strategy from a template."""
    from keel.data.templates import create_from_template

    return create_from_template(name, template, strategy_dir)


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


def require_supported_market(market: object) -> None:
    """Raise ``ValidationError(UNSUPPORTED_MARKET)`` with the validator's one
    sentence when ``market`` is not a market Keel trades (Q-2363, Q-2416)."""
    from keel.errors import ValidationError
    from pipeline_engine.dsl.spec import unsupported_market_message

    message = unsupported_market_message(market)
    if message is not None:
        raise ValidationError(message, error_code="UNSUPPORTED_MARKET")


def universe_set(
    source: str,
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
    resolve: bool = True,
) -> dict[str, Any]:
    """Set or replace universe criteria on a strategy, and resolve it.

    Accepts the full selector set (mode, market, symbols, categories, top_n,
    exclusions, inclusions, lookback (7d/30d/90d), volume_quartiles (q1-q4),
    min_trailing_dollar_volume — the dollar-liquidity floor: trailing-24h
    traded dollar volume, Σ trade price × size over 1m bars; there is none
    before 2025-03-23) for parity with the web editor and the in-cluster
    universe_set tool. A floor declared under the deprecated alias
    `min_trailing_notional_proxy` is carried and written back under the new
    name (DV6b: this tool is a writer).

    Declared state this call takes no argument for is CARRIED: `groups`, and
    the floor when the caller passes None. The resolution (`resolved` /
    `resolved_at` / `max_leverages`) is carried only when the call leaves the
    criteria as declared — an edit that changes nothing must not un-resolve
    the strategy (audit 04 U-18). A criteria change drops it (Q-2435): the old
    list answers the old criteria, and carried it ran under the new label.
    The spec is built by the SAME constructor the rich twin uses
    (`pipeline_engine.dsl.spec.universe_spec_with_declared_state`, in the
    bundled DSL subset), so the two cannot diverge again.

    The universe is RESOLVED IN THE SAME CALL by default (Q-2283, spec 03 U2):
    the returned source carries the baked asset list, exactly what
    `universe_resolve` returns, so no second call is needed. A manual basket
    is classified by the server: HIP-3 and unknown symbols are dropped from
    `resolved` and named in `resolution_note` (e.g. "Resolved 2 of 3 symbols:
    BTC, ETH. Dropped xyz:AAPL — listed on Hyperliquid (HIP-3); Keel does not
    support HIP-3 markets yet — support is coming soon."); a basket with
    nothing tradeable raises `ValidationError` with code
    `UNIVERSE_NOTHING_TRADEABLE`. `resolve=False` writes the criteria only,
    offline — the next save resolves them server-side.

    Like `universe_resolve`, the rewrite is a SURGICAL SPAN EDIT of the
    `Universe(...)` statement only (twin of the rich
    `pipeline_engine.mcp.tools.universe_set`) — comments and formatting
    elsewhere in the file survive byte-exact. `reformatted: True` in the
    result means the equivalence net failed and the file was re-emitted
    canonically instead.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.edits import EditError, replace_declaration
    from pipeline_engine.dsl.emitter import spec_to_dsl
    from pipeline_engine.dsl.spec import universe_spec_with_declared_state

    # Q-2416: refuse an unsupported market BEFORE writing anything — with
    # `resolve=False` nothing else would, and the file would carry e.g.
    # market="spot" until the next save refused it.
    require_supported_market(market)

    parsed = parse_strategy(source)

    new_universe = universe_spec_with_declared_state(
        parsed.universe,
        mode=mode,
        market=market,
        symbols=symbols,
        categories=categories,
        top_n=top_n,
        exclusions=exclusions,
        inclusions=inclusions,
        lookback=lookback,
        volume_quartiles=volume_quartiles,
        min_trailing_dollar_volume=min_trailing_dollar_volume,
    )

    try:
        new_source = replace_declaration(source, "universe", new_universe)
        reformatted = False
    except EditError:
        _LOGGER.warning(
            "universe_set: span edit failed; falling back to canonical re-emission "
            "(comments and formatting outside Universe(...) will be lost)",
            exc_info=True,
        )
        new_source = None
        reformatted = True

    parsed.universe = new_universe
    if new_source is None:
        new_source = spec_to_dsl(parsed)

    if not resolve:
        return {
            "source": new_source,
            "universe": _universe_to_dict(parsed.universe),
            "reformatted": reformatted,
        }
    # Resolve in the same call (spec 03 U2) — through the one server resolver
    # every write path uses; its refusal (UNIVERSE_NOTHING_TRADEABLE) is this
    # call's refusal.
    resolution = universe_resolve(new_source)
    resolved_universe = parse_strategy(resolution["source"]).universe
    out: dict[str, Any] = {
        **resolution,
        "universe": _universe_to_dict(resolved_universe),
        "reformatted": reformatted or bool(resolution.get("reformatted")),
    }
    return out


def universe_resolve(source: str, client: Any = None) -> dict[str, Any]:
    """Resolve a strategy's universe and bake the resolved list back into source.

    Reads the `Universe(...)` declaration from `source`, calls the Keel API to
    resolve the criteria into a concrete symbol list, and returns the same
    source with `resolved=[...]` and `resolved_at=...` baked in. No criteria
    arguments — the DSL is the source of truth.

    Use this whenever the universe is unresolved or its criteria changed (the
    `deploy` and `backtest_submit` endpoints will refuse strategies that have
    no resolved list, so call this between `universe_set` and submit).

    EVERY declared selector is forwarded to the resolver — `mode`, `market`,
    `symbols`, `categories`, `top_n`, `exclusions`, `inclusions`, `lookback`,
    `volume_quartiles` — plus the current `resolved` list as
    `current_resolved` so the API can return a diff. Dropping any of them
    would silently resolve on the server default (e.g. a declared
    `lookback="90d"` ranking on 7 days of volume) — the exact silent-fallback
    class the repo's lessons file forbids.

    The updated source is produced by SURGICAL SPAN EDITS over the existing
    `Universe(...)` call (twin of `pipeline_engine.mcp.tools.universe_resolve`
    and the chat-api agent path): only the `resolved` / `resolved_at` /
    `max_leverages` argument spans change, so comments, headers, formatting
    and statement order survive byte-exact. If the span edit's
    spec-equivalence net fails, the fallback to whole-file canonical emission
    is LOUD — logged, and flagged as ``reformatted: True`` in the result.

    Args:
        source: The strategy DSL source string.
        client: the API client to resolve through (an outcome tool passes its
            context's client, so a hosted call uses the caller's credentials);
            None builds a ``KeelClient`` from the local credentials.

    Returns:
        dict with keys:
          - source: updated DSL source with resolved/resolved_at baked in
          - resolved: list[str] of asset symbols
          - resolved_at: ISO-8601 UTC timestamp
          - count: len(resolved)
          - reformatted: True only when the span edit failed and the whole
            file was re-emitted canonically (comments lost)
          - max_leverages: per-asset venue max leverage, when the API
            returns it (omitted otherwise)
          - diff: {"added": [...], "removed": [...]}, when the API returns it
          - pool_size / band_size / supply_before_filters / rankings: the
            SELECTION EVIDENCE (D-12), passed through when the server reports
            it — the live pool a band was cut over, the band's size before
            `top_n` caps it, the ranked names the venue supplied before any
            filter, and {symbol: {rank, trailing_notional}} for the resolved
            list. Omitted by an older server; never fabricated.
          - resolution_note / dropped: what a manual basket (or inclusions)
            kept and dropped and why — HIP-3 and unknown symbols are dropped
            (Q-2283); omitted when every named symbol is tradeable
          - top_n_written_down: present only when the VENUE could not supply
            `top_n` and this call wrote it down (see below)

    Raises:
        ValueError: source has no Universe declaration.
        KeelError: API call failed (unauthenticated, network, criteria invalid, etc.)
            — including `ValidationError` code `UNIVERSE_NOTHING_TRADEABLE` when
            a manual basket holds no symbol Keel can trade.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.edits import EditError, set_decl_arg
    from pipeline_engine.dsl.emitter import spec_to_dsl

    parsed = parse_strategy(source)
    if parsed.universe is None:
        raise ValueError("Strategy has no Universe declaration. Add one with universe_set first.")

    u = parsed.universe
    body: dict[str, Any] = {
        "mode": u.mode,
        "market": u.market or "perp",
    }
    # Only include criteria fields that are populated. The API accepts them
    # as optional and validates by mode.
    if u.symbols:
        body["symbols"] = list(u.symbols)
    if u.categories:
        body["categories"] = list(u.categories)
    if u.top_n is not None:
        body["top_n"] = u.top_n
    if u.exclusions:
        body["exclusions"] = list(u.exclusions)
    if u.inclusions:
        body["inclusions"] = list(u.inclusions)
    if u.lookback:
        body["lookback"] = u.lookback
    if u.volume_quartiles:
        body["volume_quartiles"] = list(u.volume_quartiles)
    # The dollar-volume floor under its current name, even when the source
    # declares the deprecated alias min_trailing_notional_proxy (DV6b: this
    # tool is a writer); both names are sent as declared, so the API 422s the
    # ambiguity instead of this tool picking a winner. Both helpers ship in
    # the vendored DSL subset (build_data.py copies spec.py verbatim).
    from pipeline_engine.dsl.spec import dollar_volume_floor_to_emit

    floor_fields = dict(dollar_volume_floor_to_emit(u))
    body.update(floor_fields)
    floor = floor_fields.get("min_trailing_dollar_volume")
    if u.resolved:
        body["current_resolved"] = list(u.resolved)

    # Lazy import — only this tool actually needs the HTTP client. Keeps the
    # rest of the offline tools surface zero-network at import time.
    if client is None:
        from keel.client import KeelClient

        client = KeelClient()
    result = client.post("/v1/universe/resolve", json=body)

    resolved = list(result["resolved"])
    resolved_at = result["resolved_at"]
    # Venue metadata rides the same resolution: ResolveUniverseResponse
    # carries the per-symbol maxLeverage map (audit BLOCKER 2's proper fix).
    # Bake it only when present and non-empty — older servers omit the field
    # and this path must keep working against them. Never fabricate a map.
    raw_leverages = result.get("max_leverages")
    max_leverages = dict(raw_leverages) if raw_leverages else None

    parsed.universe.resolved = resolved
    parsed.universe.resolved_at = resolved_at
    if max_leverages is not None:
        parsed.universe.max_leverages = max_leverages

    # Under-SUPPLY honesty (cohort Lane U, `universe-resolve-topn-underfill`):
    # when the VENUE's qualifying pool is smaller than the declared top_n, a
    # resolve that writes back only `resolved` leaves top_n=200/resolved=177
    # in the source and the STALE_UNIVERSE gate then fails every submit with
    # a remediation (re-resolve) that reproduces the same state forever.
    # Write top_n down to the achievable count and say so.
    #
    # Measured against the FINAL list (what this did until 2026-09-16) the test
    # fires on two shapes it was never meant for, and both RATCHET:
    # `top_n=30, exclusions=["BTC"]` walked 30 → 29 → 28 per call, and a q1
    # band under `top_n=50` was rewritten to the band size — the exact silent
    # rewrite of a user's declared intent that D-11 rejected. The decision is
    # the ONE predicate the API and the rich twin use, bundled in the DSL
    # subset, reading the pool size the server measured BEFORE bands, floor,
    # exclusions and inclusions.
    from pipeline_engine.dsl.universe_expectation import top_n_under_supplied

    # `u.top_n` IS the requested value — this call sent it. The server's echo
    # is read only to notice an older server (it predates `requested_top_n`).
    requested_top_n = u.top_n
    supply = result.get("supply_before_filters")
    sdk_warnings: list[str] = []
    underfilled = top_n_under_supplied(u.mode, requested_top_n, supply, u.volume_quartiles, floor)
    if underfilled and supply is not None:
        parsed.universe.top_n = supply
        # No warning added here on purpose: the server emits the under-supply
        # sentence from the SAME predicate, and the CLI renders
        # `top_n_written_down` below it. A third phrasing of one fact is noise.
    elif (
        supply is None
        and u.mode == "top_volume"
        and requested_top_n is not None
        and len(resolved) < requested_top_n
    ):
        # An older server does not report the pool size, and a short list alone
        # cannot tell under-supply from an exclusion or a declared filter. Say
        # that, rather than guess and rewrite the declaration on a guess.
        sdk_warnings.append(
            f"{len(resolved)} of a requested top_n={requested_top_n} resolved, and this "
            "server does not report `supply_before_filters` — the shortfall could be the "
            "venue's pool, your exclusions, or a declared filter, so top_n was left "
            "unchanged. Upgrade the server, or set top_n yourself."
        )

    try:
        new_source = set_decl_arg(source, "universe", "resolved", resolved)
        new_source = set_decl_arg(new_source, "universe", "resolved_at", resolved_at)
        if max_leverages is not None:
            new_source = set_decl_arg(new_source, "universe", "max_leverages", max_leverages)
        if underfilled and supply is not None:
            new_source = set_decl_arg(new_source, "universe", "top_n", supply)
        reformatted = False
    except EditError:
        _LOGGER.warning(
            "universe_resolve: span edit failed; falling back to canonical re-emission "
            "(comments and formatting outside Universe(...) will be lost)",
            exc_info=True,
        )
        new_source = spec_to_dsl(parsed)
        reformatted = True

    out: dict[str, Any] = {
        "source": new_source,
        "resolved": resolved,
        "resolved_at": resolved_at,
        "count": len(resolved),
        "reformatted": reformatted,
    }
    if max_leverages is not None:
        out["max_leverages"] = max_leverages
    if result.get("diff"):
        out["diff"] = result["diff"]
    # ONE-SNAPSHOT label (Q-0983): the server names the instant its SQL window
    # ended at and says the list is a snapshot, not a membership rule. Pass
    # both through verbatim when present; older servers omit them.
    for key in ("as_of", "snapshot_note"):
        if result.get(key):
            out[key] = result[key]
    # Server-side listing advisories (unknown/delisted symbols, under-supply,
    # and — since D-11 — the "top_n is a cap under a filter" sentence) pass
    # through verbatim, followed by anything only this side knows: that it
    # wrote top_n down, or that it could not tell whether it should.
    warnings = list(result.get("warnings") or []) + sdk_warnings
    if warnings:
        out["warnings"] = warnings
    if underfilled and supply is not None:
        out["top_n_written_down"] = {
            "requested": requested_top_n,
            "achievable": supply,
            "reason": f"venue can supply {supply} of {requested_top_n} requested assets",
        }
    # The pool the bands were cut over and the ranking that produced the list —
    # the server computes them from the same query (D-12). Passed through when
    # present so an agent can explain a filtered list instead of restating it.
    for key in ("pool_size", "band_size", "supply_before_filters", "rankings"):
        if result.get(key) is not None:
            out[key] = result[key]
    # What the resolution kept and dropped from the symbols the user named,
    # and why (Q-2283, spec 03 U4) — the server's sentence, verbatim.
    for key in ("resolution_note", "dropped"):
        if result.get(key):
            out[key] = result[key]
    return out


def universe_get(source: str) -> dict[str, Any]:
    """Read universe configuration from a strategy."""
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy

    parsed = parse_strategy(source)
    if parsed.universe is None:
        return {"universe": None}
    return {"universe": _universe_to_dict(parsed.universe)}


def universe_add_group(
    source: str,
    name: str,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Add a named group to the universe."""
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.emitter import spec_to_dsl

    parsed = parse_strategy(source)
    if parsed.universe is None:
        from pipeline_engine.dsl.spec import UniverseSpec

        parsed.universe = UniverseSpec(mode="manual", market="perp")

    if parsed.universe.groups is None:
        parsed.universe.groups = {}

    if name in parsed.universe.groups:
        raise ValueError(f"Group '{name}' already exists")

    parsed.universe.groups[name] = symbols or []

    new_source = spec_to_dsl(parsed)
    return {"source": new_source, "universe": _universe_to_dict(parsed.universe)}


def universe_modify_group(
    source: str,
    name: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    """Modify an existing universe group."""
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.emitter import spec_to_dsl

    parsed = parse_strategy(source)
    if parsed.universe is None or parsed.universe.groups is None:
        raise ValueError("No universe groups defined")
    if name not in parsed.universe.groups:
        raise ValueError(f"Group '{name}' not found")

    current = list(parsed.universe.groups[name])
    if add:
        current.extend(s for s in add if s not in current)
    if remove:
        current = [s for s in current if s not in remove]
    parsed.universe.groups[name] = current

    new_source = spec_to_dsl(parsed)
    return {"source": new_source, "universe": _universe_to_dict(parsed.universe)}


def universe_remove_group(source: str, name: str) -> dict[str, Any]:
    """Remove a named group from the universe."""
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.emitter import spec_to_dsl

    parsed = parse_strategy(source)
    if parsed.universe is None or parsed.universe.groups is None:
        raise ValueError("No universe groups defined")
    if name not in parsed.universe.groups:
        raise ValueError(f"Group '{name}' not found")

    del parsed.universe.groups[name]

    new_source = spec_to_dsl(parsed)
    return {"source": new_source, "universe": _universe_to_dict(parsed.universe)}


def _universe_to_dict(uni) -> dict[str, Any]:
    """Convert UniverseSpec to a serializable dict.

    Every declared selector is reported — a reader that hides `lookback` /
    `volume_quartiles` / `max_leverages` tells the caller the declaration
    says less than it does, which is how those values get dropped on the
    next rewrite (v16 staging audit §6.1).
    """
    d: dict[str, Any] = {"mode": uni.mode, "market": uni.market}
    if uni.symbols:
        d["symbols"] = uni.symbols
    if uni.categories:
        d["categories"] = uni.categories
    if uni.top_n is not None:
        d["top_n"] = uni.top_n
    if uni.exclusions:
        d["exclusions"] = uni.exclusions
    if uni.inclusions:
        d["inclusions"] = uni.inclusions
    if getattr(uni, "lookback", None):
        d["lookback"] = uni.lookback
    if getattr(uni, "volume_quartiles", None):
        d["volume_quartiles"] = uni.volume_quartiles
    # Both floor names, AS DECLARED: this reports the declaration (the
    # deprecated alias must stay visible, or it reads as "no floor"). Twin of
    # pipeline_engine.mcp.tools.universe_get.
    if getattr(uni, "min_trailing_dollar_volume", None) is not None:
        d["min_trailing_dollar_volume"] = uni.min_trailing_dollar_volume
    if getattr(uni, "min_trailing_notional_proxy", None) is not None:
        d["min_trailing_notional_proxy"] = uni.min_trailing_notional_proxy
    if getattr(uni, "resolved", None):
        d["resolved"] = uni.resolved
    if getattr(uni, "resolved_at", None):
        d["resolved_at"] = uni.resolved_at
    if getattr(uni, "max_leverages", None):
        d["max_leverages"] = uni.max_leverages
    if uni.groups:
        d["groups"] = uni.groups
    return d


# ═══════════════════════════════════════════════════════════════════════════════
# LOCK TOOLS (local — generate lock from bundled registry)
# ═══════════════════════════════════════════════════════════════════════════════


def strategy_lock_generate(source: str) -> dict[str, Any]:
    """Generate a component lock from a strategy source."""
    _ensure_registry()

    from pipeline_engine.base.registry import get_latest
    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.spec import ComponentRef, ParallelSpec, PipelineSpec

    parsed = parse_strategy(source)
    lock: dict[str, int] = {}

    def _walk(steps):
        for step in steps:
            if isinstance(step, ComponentRef):
                sig = get_latest(step.name)
                if sig:
                    lock[step.name] = sig.version
            elif isinstance(step, ParallelSpec):
                for branch_steps in step.branches.values():
                    _walk(branch_steps)
            elif isinstance(step, PipelineSpec):
                _walk(step.steps)

    _walk(parsed.pipeline.steps)
    for factory in parsed.factories:
        _walk(factory.body.steps)

    return {"component_lock": lock}


def strategy_lock_status(
    source: str, component_lock: dict[str, int] | None = None
) -> dict[str, Any]:
    """Check component version drift: keel-api's ``POST /v1/strategies/lock/check``, offline.

    Each drift entry (``breaking``, ``issues_at_target``, ``interface``,
    ``replacement``, ``changes``) is decided by validating the strategy with
    only that pin bumped — ``pipeline_engine.base.lock_upgrade``, the one
    owner keel-api and the agent tools share (dollar-volume spec 02 §2),
    vendored into this bundle. "Latest" is this wheel's bundled registry.
    Returns ``{status, drift, component_lock}``; with no lock, a fresh one is
    current by construction.
    """
    _ensure_registry()

    from pipeline_engine.base.lock import evolve_lock
    from pipeline_engine.base.lock_upgrade import drift_entries
    from pipeline_engine.dsl import parse_strategy

    parsed = parse_strategy(source)
    if component_lock is None:
        return {"status": "current", "drift": [], "component_lock": evolve_lock({}, parsed)}
    drift = drift_entries(parsed, component_lock)
    return {
        "status": "drift" if drift else "current",
        "drift": drift,
        "component_lock": component_lock,
    }


def strategy_lock_upgrade(
    source: str,
    component_lock: dict[str, int] | None = None,
    components: list[str] | None = None,
) -> dict[str, Any]:
    """Move pins to latest and validate there: keel-api's ``POST /v1/strategies/lock/upgrade``, offline.

    The "try it" step (spec 02 §3): bumps the requested pins (every drifting
    pin when ``components`` is omitted), breaking or not, validates the
    unchanged source at the new lock, and persists nothing. Returns
    ``{component_lock, upgraded, valid, issues, changes}``; a breaking bump
    comes back invalid with the issues to recompose against. Same owner as
    the drift check (``lock_upgrade.bump_pins``).
    """
    _ensure_registry()

    from pipeline_engine.base.lock import evolve_lock
    from pipeline_engine.base.lock_upgrade import bump_pins
    from pipeline_engine.dsl import parse_strategy

    parsed = parse_strategy(source)
    if component_lock is None:
        # A fresh lock is at latest by construction.
        return {
            "component_lock": evolve_lock({}, parsed),
            "upgraded": [],
            "valid": None,
            "issues": [],
            "changes": [],
        }
    result = bump_pins(parsed, dict(component_lock), components)
    return {
        "component_lock": result["component_lock"],
        "upgraded": sorted(result["upgraded"]),
        "valid": result["valid"],
        "issues": result["issues"],
        "changes": result["changes"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# New "components" surface — preferred names (2026-06-29 lock collapse).
# The old strategy_lock_* names are kept above as the implementations; these
# are aliases so SDK callers can use either name. New code should use the
# strategy_components_* names.
# ─────────────────────────────────────────────────────────────────────────────
strategy_components_drift = strategy_lock_status
strategy_components_upgrade = strategy_lock_upgrade


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


def strategy_checkout(strategy_id: str) -> dict[str, Any]:
    """Check out a platform strategy for local editing."""
    from keel.workspace import checkout

    return checkout(strategy_id)


def strategy_push(
    strategy_id: str | None = None,
    message: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Push local changes to the platform."""
    from keel.workspace import push

    return push(strategy_id=strategy_id, message=message, force=force)


def strategy_pull(strategy_id: str | None = None) -> dict[str, Any]:
    """Pull latest source from the platform."""
    from keel.workspace import pull

    return pull(strategy_id=strategy_id)


def strategy_status(strategy_id: str | None = None) -> dict[str, Any]:
    """Show local vs remote sync state."""
    from keel.workspace import status

    return status(strategy_id=strategy_id)


def strategy_workspaces() -> dict[str, Any]:
    """List all checked-out strategies."""
    from keel.workspace import list_workspaces

    workspaces = list_workspaces()
    return {
        "workspaces": [
            {
                "strategy_id": ws.strategy_id,
                "name": ws.name,
                "source_hash": ws.source_hash[:12],
                "checked_out_at": ws.checked_out_at,
            }
            for ws in workspaces
        ],
        "count": len(workspaces),
    }


def strategy_discard(strategy_id: str | None = None) -> dict[str, Any]:
    """Remove a local workspace."""
    from keel.workspace import discard

    return discard(strategy_id=strategy_id)


def strategy_find_local(directory: str | None = None) -> dict[str, Any]:
    """Find local strategy files and list checked-out workspaces.

    Scans ~/.keel/strategies/ (where 'keel strategy new' writes files),
    the current working directory, and an optional extra directory for .py
    and .strategy files that contain Pipeline definitions. Also lists any
    strategies checked out to ~/.keel/workspace/.

    Call this FIRST when looking for strategies — before strategy_list
    which requires authentication.
    """
    from pathlib import Path

    keel_strategies_dir = Path.home() / ".keel" / "strategies"
    cwd = Path.cwd()

    # Collect unique directories to scan
    scan_dirs: list[Path] = []
    if keel_strategies_dir.is_dir():
        scan_dirs.append(keel_strategies_dir)
    if cwd != keel_strategies_dir:
        scan_dirs.append(cwd)
    if directory:
        extra = Path(directory)
        if extra.is_dir() and extra not in scan_dirs:
            scan_dirs.append(extra)

    local_files = []
    seen_paths: set[str] = set()

    for search_dir in scan_dirs:
        for pattern in ("*.py", "*.strategy"):
            for f in sorted(search_dir.glob(pattern)):
                path_str = str(f.resolve())
                if path_str in seen_paths:
                    continue
                seen_paths.add(path_str)
                try:
                    content = f.read_text(errors="ignore")
                    if "Pipeline(" in content or "pipeline" in content.lower():
                        local_files.append(
                            {
                                "path": str(f),
                                "name": f.stem,
                                "size": f.stat().st_size,
                                "location": str(search_dir),
                            }
                        )
                except OSError:
                    continue

    # Also check workspaces
    workspaces = []
    try:
        from keel.workspace import list_workspaces

        for ws in list_workspaces():
            workspaces.append(
                {
                    "strategy_id": ws.strategy_id,
                    "name": ws.name,
                    "source_hash": ws.source_hash[:12],
                }
            )
    except Exception:  # noqa: BLE001, S110 — workspace enrichment best-effort; partial result acceptable
        pass

    return {
        "local_files": local_files,
        "workspaces": workspaces,
        "scanned_directories": [str(d) for d in scan_dirs],
    }
