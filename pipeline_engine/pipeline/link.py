"""Link pinned output slices in the consumer's context, without database I/O."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any, get_args, get_origin

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
from pipeline_engine.dsl.use import (
    USE_MAX_DEPTH,
    OutputExpansion,
    StoredOutputExpansion,
    UseParseError,
    UseSpec,
    expansion_from_dict,
    expansion_hash,
    iter_uses,
    parse_use_ref,
)


def use_choice_sentence(code: str, names, as_: str | None = None) -> str:
    """The plain sentence naming the outputs a trader can pick instead.

    ``valid`` stays a list (it is the refusal's ``valid_options``); the
    message reads this sentence: names joined with commas, never a list
    literal, and a sentence of its own when nothing fits. keel-app's
    ``useChoiceSentence`` (lib/strategy/dsl/use.ts) renders the same words.
    """
    names = list(names)
    if code == "USE_AS_MISMATCH":
        if not names:
            return f"No output of this version fits as {as_}."
        return f"Outputs that fit: {', '.join(names)}."
    if not names:
        return "This version declares no outputs."
    return f"Available outputs: {', '.join(names)}."


# The template param each list-carrying code renders its ``valid`` through.
_CHOICE_PARAM = {"USE_AS_MISMATCH": "fits", "USE_OUTPUT_NOT_FOUND": "available"}


class UseLinkError(ValueError):
    def __init__(self, code: str, *, location=None, **details):
        # ``details`` (kept on ``self.context``) is the rule template's fields,
        # not a pipeline Context; named apart so the slot-read guard reads it so.
        from pipeline_engine.dsl.catalog import RULES

        self.code = code
        self.location = location
        if code in _CHOICE_PARAM:
            details.setdefault(
                _CHOICE_PARAM[code],
                use_choice_sentence(code, details.get("valid", ()), details.get("as_")),
            )
        self.context = details
        self.message = RULES[code].message_template.format(**details)
        super().__init__(self.message)


class UseDefinitionError(UseLinkError):
    """Carry ordinary definition diagnostics through the early linking gate."""

    def __init__(self, issues):
        self.issues = issues
        first = next(issue for issue in issues if issue.severity == "error")
        self.code = first.code
        self.location = None
        self.context = {}
        self.message = first.message
        ValueError.__init__(self, self.message)


def materialize_uses(strategy: StrategyFile) -> StrategyFile:
    """Give each invoked local definition its own Use callsite and namespace."""
    if not list(iter_uses(strategy)):
        return strategy
    return replace(strategy, pipeline=_inline_variables(strategy), variables=[], factories=[])


def _signature(name, lock=None):
    from pipeline_engine.base.registry import COMPONENT_REGISTRY, get_latest
    from pipeline_engine.registry_loader import ensure_registry_loaded

    ensure_registry_loaded()
    if lock and name in lock:
        return COMPONENT_REGISTRY.get(name, {}).get(lock[name])
    return get_latest(name)


def _slot_parameters(step: ComponentRef, lock=None) -> dict[str, str]:
    sig = _signature(step.name, lock)
    if sig is None:
        return {}
    refs = {}
    for name, param in sig.parameters.items():
        if getattr(param, "slot_reference", False):
            value = step.params.get(name, param.default)
            if isinstance(value, str):
                refs[name] = value
    for name in sig.slot_reads:
        if name not in sig.parameters:
            refs[name] = name
    return refs


def _walk(steps):
    for step in steps:
        yield step
        if isinstance(step, PipelineSpec):
            yield from _walk(step.steps)
        elif isinstance(step, ParallelSpec):
            for branch in step.branches.values():
                yield from _walk(branch)


def _pipeline_text(pipeline: PipelineSpec) -> str:
    from pipeline_engine.dsl.emitter import _emit_pipeline_expr

    return "\n".join(_emit_pipeline_expr(pipeline, 0)) + "\n"


def _parse_slice(text: str) -> PipelineSpec:
    from pipeline_engine.dsl.parser import parse_strategy

    parsed = parse_strategy(text)
    # Slice text has no ambient declarations or unresolved local definitions.
    if (
        parsed.factories
        or parsed.variables
        or parsed.globals_
        or parsed.universe
        or parsed.execution
        or parsed.outputs
    ):
        raise ValueError("An expansion slice must be a standalone Pipeline, without declarations.")
    return parsed.pipeline


def _required_roles(expected):
    """Use the ordinary consumer type contract to disambiguate role slots."""
    if isinstance(expected, tuple):
        alternatives = [_required_roles(item) for item in expected]
        return None if any(item is None for item in alternatives) else set().union(*alternatives)
    if get_origin(expected) is Annotated:
        _, *metadata = get_args(expected)
        for item in metadata:
            values = getattr(item, "values", None)
            if values is not None and set(values) <= {-1, 0, 1}:
                return {"filter", "event", "universe"}
    if getattr(expected, "__name__", None) == "GlobalSeries":
        return {"regime"}
    return None


def _rename_slice(pipeline, *, ref, prefix, output, location, lock=None):
    result = copy.deepcopy(pipeline)
    stores = set()
    for step in _walk(result.steps):
        if isinstance(step, (SlotStoreSpec, SlotStoreValueSpec)):
            stores.add(step.slot_name)
        elif isinstance(step, SlotLoadSpec):
            if step.slot_name not in stores:
                raise UseLinkError(
                    "USE_EXPANSION_NOT_SELF_CONTAINED",
                    ref=ref,
                    slot=step.slot_name,
                    location=location,
                )
        elif isinstance(step, ComponentRef):
            for slot in _slot_parameters(step, lock).values():
                if slot not in stores:
                    raise UseLinkError(
                        "USE_EXPANSION_NOT_SELF_CONTAINED", ref=ref, slot=slot, location=location
                    )
    if (
        not result.steps
        or not isinstance(result.steps[-1], SlotStoreSpec)
        or result.steps[-1].slot_name != output
    ):
        raise UseLinkError("USE_OUTPUT_NOT_COMPUTED", ref=ref, output=output, location=location)
    result.steps.pop()
    if not result.steps:
        raise UseLinkError("USE_OUTPUT_NOT_COMPUTED", ref=ref, output=output, location=location)
    for step in _walk(result.steps):
        if isinstance(step, (SlotStoreSpec, SlotStoreValueSpec, SlotLoadSpec)):
            step.slot_name = f"{prefix}.{step.slot_name}"
        elif isinstance(step, ComponentRef):
            for param, slot in _slot_parameters(step, lock).items():
                if param not in _signature(step.name, lock).parameters:
                    # A hard-coded runtime slot cannot be hygienically renamed.
                    raise UseLinkError(
                        "USE_EXPANSION_NOT_SELF_CONTAINED", ref=ref, slot=slot, location=location
                    )
                step.params[param] = f"{prefix}.{slot}"
        if hasattr(step, "location"):
            step.location = location
    result.location = location
    result.use_ref = ref
    result.name = None
    result.name = "use_" + expansion_hash(_pipeline_text(result))[:20]
    return result


def link_uses(
    strategy: StrategyFile,
    expansions: Mapping[str, OutputExpansion | dict[str, Any]] | None,
    *,
    consumer_id: str | None = None,
    consumer_lock: dict[str, int] | None = None,
) -> StrategyFile:
    """Replace every Use with stored, hygienic steps; preserve no-Use identity."""
    uses = list(iter_uses(strategy))
    if not uses:
        return strategy
    strategy = materialize_uses(strategy)
    result = copy.deepcopy(strategy)
    payloads = {
        ref: expansion_from_dict(value) if isinstance(value, dict) else value
        for ref, value in (expansions or {}).items()
    }
    metadata = []
    # Pre-existing user slots cannot capture an expansion's instance namespace.
    occupied = {
        step.slot_name
        for step in _walk(strategy.pipeline.steps)
        if isinstance(step, (SlotStoreSpec, SlotStoreValueSpec))
    }
    instance = 0

    def link_steps(steps, path, expected=None):
        nonlocal instance
        linked = []
        for index, step in enumerate(steps):
            at = f"{path}.step[{index}]"
            if isinstance(step, UseSpec):
                try:
                    block, version = parse_use_ref(step.ref)
                except UseParseError as exc:
                    raise UseLinkError(
                        "USE_REF_MALFORMED", ref=step.ref, location=step.location
                    ) from exc
                context = {"ref": step.ref, "location": step.location}
                if consumer_id == block:
                    raise UseLinkError("USE_SELF", **context)
                payload = payloads.get(step.ref)
                if payload is None:
                    raise UseLinkError("USE_BLOCK_NOT_FOUND", **context)
                if payload.ref != step.ref:
                    raise UseLinkError("USE_EXPANSION_DRIFT", **context)
                if not payload.source_shown:
                    raise UseLinkError("USE_SOURCE_NOT_SHOWN", **context)
                if not 1 <= payload.depth <= USE_MAX_DEPTH:
                    raise UseLinkError("USE_DEPTH_EXCEEDED", limit=USE_MAX_DEPTH, **context)
                output = payload.outputs.get(step.output)
                if output is None:
                    raise UseLinkError(
                        "USE_OUTPUT_NOT_FOUND",
                        output=step.output,
                        valid=list(payload.outputs),
                        **context,
                    )
                if step.as_ is not None and step.as_ != output.descriptor.role:
                    raise UseLinkError(
                        "USE_AS_MISMATCH",
                        output=step.output,
                        role=output.descriptor.role,
                        as_=step.as_,
                        valid=[
                            name
                            for name, value in payload.outputs.items()
                            if value.descriptor.role == step.as_
                        ],
                        location=step.location,
                    )
                next_type = expected if index == len(steps) - 1 else None
                if index + 1 < len(steps) and isinstance(steps[index + 1], ComponentRef):
                    signature = _signature(steps[index + 1].name, consumer_lock)
                    next_type = signature.input_type if signature else None
                roles = _required_roles(next_type)
                if roles is not None and output.descriptor.role not in roles:
                    raise UseLinkError(
                        "USE_AS_MISMATCH",
                        output=step.output,
                        role=output.descriptor.role,
                        as_="/".join(sorted(roles)),
                        valid=[
                            name
                            for name, candidate in payload.outputs.items()
                            if candidate.descriptor.role in roles
                        ],
                        location=step.location,
                    )
                if expansion_hash(output.slice_text) != output.slice_hash:
                    raise UseLinkError("USE_EXPANSION_DRIFT", **context)
                if not output.slice_text.strip():
                    raise UseLinkError("USE_OUTPUT_NOT_COMPUTED", output=step.output, **context)
                try:
                    pipeline = _parse_slice(output.slice_text)
                except ValueError as exc:
                    raise UseLinkError(
                        "USE_EXPANSION_NOT_SELF_CONTAINED", slot="ambient declarations", **context
                    ) from exc
                if any(isinstance(item, UseSpec) for item in _walk(pipeline.steps)):
                    raise UseLinkError(
                        "USE_EXPANSION_NOT_SELF_CONTAINED", slot="unexpanded Use", **context
                    )
                while True:
                    instance += 1
                    prefix = f"u{instance}"
                    if not any(slot.startswith(prefix + ".") for slot in occupied):
                        break
                linked.append(
                    _rename_slice(
                        pipeline,
                        ref=step.ref,
                        prefix=prefix,
                        output=step.output,
                        location=step.location,
                        lock=consumer_lock,
                    )
                )
                metadata.append(
                    {
                        "path": at,
                        "block": block,
                        "version": version,
                        "output": step.output,
                        "as": step.as_,
                        "source_hash": output.slice_hash,
                    }
                )
            elif isinstance(step, PipelineSpec):
                if step.use_ref is None and step.name and step.name.startswith("use_"):
                    # Detach keeps this content-named container; count the
                    # original instance so later Uses retain their namespace.
                    instance += 1
                linked.append(replace(step, steps=link_steps(step.steps, at)))
            elif isinstance(step, ParallelSpec):
                contracts = {}
                if index + 1 < len(steps) and isinstance(steps[index + 1], ComponentRef):
                    consumer = steps[index + 1]
                    signature = _signature(consumer.name, consumer_lock)
                    if signature and isinstance(signature.composer_inputs, dict):
                        contracts = {
                            consumer.params.get(name, name): value
                            for name, value in signature.composer_inputs.items()
                        }
                linked.append(
                    replace(
                        step,
                        branches={
                            name: link_steps(branch, f"{at}.branch[{name}]", contracts.get(name))
                            for name, branch in step.branches.items()
                        },
                    )
                )
            else:
                linked.append(step)
        return linked

    for factory in result.factories:
        factory.body.steps = link_steps(factory.body.steps, f"factory[{factory.name}]")
    for variable in result.variables:
        if isinstance(variable.value, PipelineSpec):
            variable.value.steps = link_steps(variable.value.steps, f"var[{variable.name}]")
    result.pipeline.steps = link_steps(result.pipeline.steps, "pipeline")
    result.uses = metadata
    return result


def link_notices(strategy, expansions, consumer_lock=None) -> list[dict[str, Any]]:
    notices = []
    strategy = materialize_uses(strategy)
    for path, step in iter_uses(strategy):
        payload = (expansions or {}).get(step.ref)
        if payload is None:
            continue
        if isinstance(payload, dict):
            payload = expansion_from_dict(payload)
        output = payload.outputs.get(step.output)
        if output and output.universe_sensitive:
            notices.append(
                {
                    "code": "USE_UNIVERSE_SENSITIVE",
                    "path": path,
                    "location": step.location,
                    "context": {"ref": step.ref},
                }
            )
        if consumer_lock is not None and any(
            consumer_lock.get(name) != version for name, version in payload.source_lock.items()
        ):
            notices.append(
                {
                    "code": "USE_PIN_DIFFERS",
                    "path": path,
                    "location": step.location,
                    "context": {"ref": step.ref},
                }
            )
    return notices


def detach_use(
    strategy: StrategyFile, at: str, expansions, *, consumer_id=None, consumer_lock=None
) -> StrategyFile:
    """Inline only the selected Use; keep other source references unchanged."""
    strategy = materialize_uses(strategy)
    if at not in dict(iter_uses(strategy)):
        raise ValueError(f"No Use at {at}")
    linked = link_uses(strategy, expansions, consumer_id=consumer_id, consumer_lock=consumer_lock)
    result = copy.deepcopy(strategy)

    def replace_at(original, expanded, path):
        for index, step in enumerate(original):
            here = f"{path}.step[{index}]"
            if here == at:
                original[index] = copy.deepcopy(expanded[index])
                original[index].use_ref = None
                return True
            if isinstance(step, PipelineSpec) and replace_at(
                step.steps, expanded[index].steps, here
            ):
                return True
            if isinstance(step, ParallelSpec):
                for name, branch in step.branches.items():
                    if replace_at(branch, expanded[index].branches[name], f"{here}.branch[{name}]"):
                        return True
        return False

    found = replace_at(result.pipeline.steps, linked.pipeline.steps, "pipeline")
    for target, source in zip(result.variables, linked.variables, strict=True):
        if isinstance(target.value, PipelineSpec):
            found |= replace_at(target.value.steps, source.value.steps, f"var[{target.name}]")
    for target, source in zip(result.factories, linked.factories, strict=True):
        found |= replace_at(target.body.steps, source.body.steps, f"factory[{target.name}]")
    assert found
    return result


_CROSS_SECTIONAL = frozenset(
    {"CrossSectionalZScore", "QuantileRankTransform", "RankTransform", "TopNAssetSelector"}
)

# Exact legacy formulas, scoped to their recorded component version. New
# versions do not inherit a guess. Most transforms use their declared
# trade_safe.warmup algebra instead; unknown legacy temporal semantics remain
# unknown and do not prevent linking.
_LEGACY_WARMUP = {
    ("PriceDataLoader", 3): 0,
    ("FundingDataLoader", 3): 0,
    ("OpenInterestLoader", 2): 0,
    # Pointwise extraction and already-rolled source bars add no intrinsic
    # history. Source gaps remain missing; zero is not a fill guarantee.
    ("ExtractSeries", 2): 0,
    ("DollarVolumeLoader", 2): 0,
    ("NotionalVolumeExtractor", 1): 0,
    ("ATR", 1): {"param": "period"},
    ("MACD", 1): {
        "sub": [
            {
                "add": [
                    {"max": [{"param": "fast_period"}, {"param": "slow_period"}]},
                    {"param": "signal_period"},
                ]
            },
            2,
        ]
    },
    ("CrossSectionalZScore", 1): 0,
    ("QuantileRankTransform", 1): 0,
    ("RankTransform", 1): 0,
    ("TopNAssetSelector", 1): 0,
    ("ROC", 1): {"param": "period"},
    ("RSI", 1): {"param": "period"},
    # These describe first finite output on finite input, not a finite
    # historical dependency horizon. EWMA is recursive but its explicitly
    # configured min_periods still has an exact first-output index.
    ("SMA", 1): {"sub": [{"param": "period"}, 1]},
    ("EWMA", 1): {"sub": [{"coalesce": [{"param": "min_periods"}, {"param": "window"}]}, 1]},
    ("UltimateOscillator", 1): {
        "max": [{"param": "fast"}, {"max": [{"param": "medium"}, {"param": "slow"}]}]
    },
}


def component_lookback(step: ComponentRef, source_lock=None) -> int | None:
    """Exact declared/pinned warm-up, never a parameter-name heuristic."""
    from pipeline_engine.binding import eval_warmup

    sig = _signature(step.name, source_lock)
    if sig is None:
        return None
    params = {name: param.default for name, param in sig.parameters.items()}
    params.update(step.params)
    formula = (
        sig.trade_safe.get("warmup")
        if sig.trade_safe
        else _LEGACY_WARMUP.get((step.name, sig.version))
    )
    return eval_warmup(formula, params) if formula is not None else None


def _inline_variables(strategy):
    """Inline local definitions before slicing, without resolving live data."""
    from pipeline_engine.dsl.validator import (
        _expand_factories,
        _validate_factory_acyclicity,
        _validate_references,
        _validate_variable_acyclicity,
    )

    issues = []
    _validate_references(strategy, issues)
    _validate_factory_acyclicity(strategy, issues)
    if any(issue.severity == "error" for issue in issues):
        raise UseDefinitionError(issues)
    expanded = _expand_factories(strategy, issues)
    _validate_variable_acyclicity(expanded, issues)
    if any(issue.severity == "error" for issue in issues):
        raise UseDefinitionError(issues)
    variables = {variable.name: variable.value for variable in expanded.variables}

    def value(item, seen):
        if isinstance(item, VariableRef):
            if item.name in seen or item.name not in variables:
                raise ValueError(f"Unresolved slice variable {item.name}")
            return value(copy.deepcopy(variables[item.name]), seen | {item.name})
        if isinstance(item, dict):
            return {key: value(child, seen) for key, child in item.items()}
        if isinstance(item, list):
            return [value(child, seen) for child in item]
        if isinstance(item, tuple):
            return tuple(value(child, seen) for child in item)
        if isinstance(item, PipelineSpec):
            return replace(item, steps=[value(child, seen) for child in item.steps])
        if isinstance(item, ParallelSpec):
            return replace(
                item,
                branches={
                    key: [value(child, seen) for child in branch]
                    for key, branch in item.branches.items()
                },
            )
        if isinstance(item, ComponentRef):
            return replace(item, params=value(item.params, seen))
        return copy.deepcopy(item)

    return value(expanded.pipeline, set())


def _backward_slice(strategy, output, source_lock=None):
    """Follow value and slot edges, pruning unrelated producers and branches."""
    pipeline = _inline_variables(strategy)
    edges: dict[tuple, set[tuple]] = {}
    slots: dict[str, tuple] = {}
    nodes = {}
    branch_results = {}
    extract_parallel = {}
    warmups = {}

    def walk(steps, current, path):
        for index, step in enumerate(steps):
            key = (*path, index)
            nodes[key] = step
            deps = set(current)
            if isinstance(step, PipelineSpec):
                current = walk(step.steps, current, key)
                continue
            if isinstance(step, ParallelSpec):
                ends = {
                    name: walk(branch, current, (*key, name))
                    for name, branch in step.branches.items()
                }
                branch_results[key] = ends
                deps = set().union(*ends.values()) if ends else set()
            elif (
                isinstance(step, SlotExtractSpec)
                and len(current) == 1
                and next(iter(current)) in branch_results
            ):
                parent = next(iter(current))
                deps = branch_results[parent].get(step.key, set())
                extract_parallel[key] = parent
            elif isinstance(step, SlotLoadSpec):
                if step.slot_name not in slots:
                    raise UseLinkError(
                        "USE_EXPANSION_NOT_SELF_CONTAINED", ref="source", slot=step.slot_name
                    )
                deps = {slots[step.slot_name]}
            elif isinstance(step, SlotStoreValueSpec):
                deps = set()
            elif isinstance(step, ComponentRef):
                sig = _signature(step.name, source_lock)
                if sig is not None and sig.input_type in (None, type(None)):
                    deps = set()
                for slot in _slot_parameters(step, source_lock).values():
                    if slot not in slots:
                        raise UseLinkError(
                            "USE_EXPANSION_NOT_SELF_CONTAINED", ref="source", slot=slot
                        )
                    deps.add(slots[slot])
            elif isinstance(step, UseSpec):
                raise ValueError("Link Uses before deriving stored output slices")
            edges[key] = deps
            own = 0
            if isinstance(step, ComponentRef):
                own = component_lookback(step, source_lock)
            upstream = [warmups[dep] for dep in deps]
            warmups[key] = (
                None
                if own is None or any(value is None for value in upstream)
                else max(upstream, default=0) + own
            )
            current = {key}
            if isinstance(step, (SlotStoreSpec, SlotStoreValueSpec)):
                slots[step.slot_name] = key
        return current

    walk(pipeline.steps, set(), ())
    if output not in slots:
        raise UseLinkError("USE_OUTPUT_NOT_COMPUTED", ref="source", output=output)
    target = slots[output]
    selected = set()

    def include(key):
        if key in selected:
            return
        selected.add(key)
        for dependency in edges.get(key, ()):
            include(dependency)

    include(target)
    flattened = set()

    def prune(steps, path):
        kept = []
        for index, step in enumerate(steps):
            key = (*path, index)
            if isinstance(step, PipelineSpec):
                inner = prune(step.steps, key)
                if inner:
                    kept.append(replace(step, steps=inner))
            elif isinstance(step, ParallelSpec):
                branches = {
                    name: result
                    for name, branch in step.branches.items()
                    if (result := prune(branch, (*key, name)))
                }
                if key not in selected and len(branches) == 1:
                    flattened.add(key)
                    kept.extend(next(iter(branches.values())))
                elif branches:
                    kept.append(replace(step, branches=branches))
            elif key in selected:
                if extract_parallel.get(key) not in flattened:
                    kept.append(copy.deepcopy(step))
        return kept

    steps = prune(pipeline.steps, ())
    if not steps or all(
        isinstance(step, (SlotStoreSpec, SlotStoreValueSpec, SlotLoadSpec)) for step in _walk(steps)
    ):
        raise UseLinkError("USE_OUTPUT_NOT_COMPUTED", ref="source", output=output)
    if not isinstance(steps[-1], SlotStoreSpec) or steps[-1].slot_name != output:
        steps.extend(
            [SlotLoadSpec(output, pipeline.location), SlotStoreSpec(output, pipeline.location)]
        )
    return replace(pipeline, steps=steps, name=None, use_ref=None), warmups[target]


def output_universe_sensitive(strategy: StrategyFile, slot: str) -> bool:
    pipeline, _ = _backward_slice(strategy, slot)
    return any(
        isinstance(step, ComponentRef) and step.name in _CROSS_SECTIONAL
        for step in _walk(pipeline.steps)
    )


def build_output_expansions(
    strategy, descriptors, *, source_lock=None
) -> dict[str, StoredOutputExpansion]:
    """Source-save projection; never place slice text in fingerprinted outputs."""
    from pipeline_engine.validation_shared import OutputDescriptor

    result = {}
    entries = (
        descriptors.items() if isinstance(descriptors, dict) else ((d.name, d) for d in descriptors)
    )
    for name, descriptor in entries:
        if isinstance(descriptor, dict):
            descriptor = OutputDescriptor.from_dict(descriptor)
        pipeline, warmup = _backward_slice(strategy, name, source_lock)
        text = _pipeline_text(pipeline)
        components = [step for step in _walk(pipeline.steps) if isinstance(step, ComponentRef)]
        loaders = tuple(
            dict.fromkeys(
                step.name
                for step in components
                if (sig := _signature(step.name, source_lock)) is not None
                and sig.input_type in (None, type(None))
            )
        )
        result[name] = StoredOutputExpansion(
            descriptor,
            text,
            expansion_hash(text),
            warmup,
            loaders,
            any(step.name in _CROSS_SECTIONAL for step in components),
        )
    return result


def format_lookback(bars: int | None, timeframe: str) -> str:
    from timeframes import TIMEFRAME_MINUTES

    if bars is None:
        return "Lookback is not yet known for this expansion."
    minutes = bars * TIMEFRAME_MINUTES[timeframe]
    if minutes % 1440 == 0:
        duration = f"{minutes // 1440} days"
    elif minutes % 60 == 0:
        duration = f"{minutes // 60} hours"
    else:
        duration = f"{minutes} minutes"
    return f"{bars} bars = {duration}"
