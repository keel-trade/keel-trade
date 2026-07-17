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


def strategy_validate(
    source: str,
    component_lock: dict[str, int] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Validate a strategy source — full 9-pass validation."""
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy, validate_strategy
    from pipeline_engine.dsl.parser import DSLParseError

    try:
        parsed = parse_strategy(source)
    except DSLParseError as e:
        return {
            "valid": False,
            "issues": [{"severity": "error", "message": str(e)}],
            "errors": [{"severity": "error", "message": str(e)}],
            "warnings": [],
            "type_flow": [],
        }

    result = validate_strategy(parsed, lock=component_lock)

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

    result = validate_strategy(parsed, lock=component_lock)

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

    Lightweight reimplementation: parse + validate type flow,
    then map each step's output type to a readiness stage.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy, validate_strategy
    from pipeline_engine.dsl.parser import DSLParseError

    try:
        parsed = parse_strategy(source)
    except DSLParseError as e:
        return {"stage": "unparseable", "error": str(e)}

    result = validate_strategy(parsed)

    if not result.valid:
        return {
            "stage": "invalid",
            "errors": [i.to_dict() for i in result.errors],
        }

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
    }

    final_type = "unknown"
    if result.type_flow:
        final_type = result.type_flow[-1].output_type

    stage = stage_map.get(final_type, "unknown")
    backtest_ready = stage in ("portfolio", "execution")

    return {
        "stage": stage,
        "final_output_type": final_type,
        "backtest_ready": backtest_ready,
        "step_count": len(result.type_flow),
        "type_flow": [e.to_dict() for e in result.type_flow],
    }


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
) -> dict[str, Any]:
    """Set or replace universe criteria on a strategy.

    Accepts the full selector set (mode, market, symbols, categories, top_n,
    exclusions, inclusions, lookback (7d/30d/90d), volume_quartiles (q1-q4)) for
    parity with the web editor and the in-cluster universe_set tool.

    NOTE: this only writes the criteria. To bake the concrete asset list into
    the source (which `deploy` and `backtest_submit` require), call
    `universe_resolve(source)` on the returned source. The web editor does
    both in one step; CLI / agent flows chain the two calls.
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
    from pipeline_engine.dsl.emitter import spec_to_dsl
    from pipeline_engine.dsl.spec import UniverseSpec

    parsed = parse_strategy(source)

    parsed.universe = UniverseSpec(
        mode=mode,
        market=market,
        symbols=symbols or [],
        categories=categories or [],
        top_n=top_n,
        exclusions=exclusions or [],
        inclusions=inclusions or [],
        lookback=lookback,
        volume_quartiles=volume_quartiles or [],
    )

    new_source = spec_to_dsl(parsed)
    return {"source": new_source, "universe": _universe_to_dict(parsed.universe)}


def universe_resolve(source: str) -> dict[str, Any]:
    """Resolve a strategy's universe and bake the resolved list back into source.

    Reads the `Universe(...)` declaration from `source`, calls the Keel API to
    resolve the criteria into a concrete symbol list, and returns the same
    source with `resolved=[...]` and `resolved_at=...` baked in. No criteria
    arguments — the DSL is the source of truth.

    Use this whenever the universe is unresolved or its criteria changed (the
    `deploy` and `backtest_submit` endpoints will refuse strategies that have
    no resolved list, so call this between `universe_set` and submit).

    Args:
        source: The strategy DSL source string.

    Returns:
        dict with keys:
          - source: updated DSL source with resolved/resolved_at baked in
          - resolved: list[str] of asset symbols
          - resolved_at: ISO-8601 UTC timestamp
          - count: len(resolved)

    Raises:
        ValueError: source has no Universe declaration.
        KeelError: API call failed (unauthenticated, network, criteria invalid, etc.).
    """
    _ensure_registry()

    from pipeline_engine.dsl import parse_strategy
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

    # Lazy import — only this tool actually needs the HTTP client. Keeps the
    # rest of the offline tools surface zero-network at import time.
    from keel.client import KeelClient

    client = KeelClient()
    result = client.post("/v1/universe/resolve", json=body)

    parsed.universe.resolved = list(result["resolved"])
    parsed.universe.resolved_at = result["resolved_at"]
    new_source = spec_to_dsl(parsed)

    return {
        "source": new_source,
        "resolved": parsed.universe.resolved,
        "resolved_at": parsed.universe.resolved_at,
        "count": len(parsed.universe.resolved),
    }


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
    """Convert UniverseSpec to a serializable dict."""
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
    if getattr(uni, "resolved", None):
        d["resolved"] = uni.resolved
    if getattr(uni, "resolved_at", None):
        d["resolved_at"] = uni.resolved_at
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
    """Check component version drift against current registry."""
    _ensure_registry()

    if component_lock is None:
        return {"status": "unknown", "message": "No component lock provided"}

    from pipeline_engine.base.registry import get_latest

    drift = []
    for name, locked_version in component_lock.items():
        sig = get_latest(name)
        if sig is None:
            drift.append(
                {"name": name, "locked": locked_version, "latest": None, "status": "unknown"}
            )
        elif sig.version != locked_version:
            drift.append(
                {
                    "name": name,
                    "locked": locked_version,
                    "latest": sig.version,
                    "status": "drift",
                }
            )

    status = "current" if not drift else "drift"
    return {"status": status, "drift": drift, "component_lock": component_lock}


def strategy_lock_upgrade(
    source: str,
    component_lock: dict[str, int] | None = None,
    components: list[str] | None = None,
) -> dict[str, Any]:
    """Upgrade component versions in a lock."""
    _ensure_registry()

    if component_lock is None:
        result = strategy_lock_generate(source)
        return {
            "component_lock": result["component_lock"],
            "upgraded": list(result["component_lock"].keys()),
        }

    from pipeline_engine.base.registry import get_latest

    new_lock = dict(component_lock)
    upgraded = []
    targets = components or list(component_lock.keys())

    for name in targets:
        if name not in new_lock:
            continue
        sig = get_latest(name)
        if sig and sig.version != new_lock[name]:
            new_lock[name] = sig.version
            upgraded.append(name)

    return {"component_lock": new_lock, "upgraded": upgraded}


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
