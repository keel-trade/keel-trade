"""The expansion of REGISTERED factory calls (position-layer spec 03-R46..R48, Q-2448).

A call to a component of role ``factory`` (``StopLoss(pct=0.05)``) becomes its
``factory_expansion`` template executed at the call's effective arguments by
``binding.expand_factory`` (the executor the TS validator mirrors): a nested
``PipelineSpec`` named canonically (``"StopLoss(pct=0.05)"``) whose every step
carries the call's location. The validator runs this after passes 4 and 5
have checked the call itself (pass 5b); the resolver runs the same function,
so the compiled blob holds exactly the long form the validator judged.

Quick Start:
    >>> from pipeline_engine.dsl.registered_factories import expand_registered_factories
    >>> # expanded = expand_registered_factories(strategy, registry, report)
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from pipeline_engine.base.registry import MISSING
from pipeline_engine.dsl.spec import (
    ComponentRef,
    ParallelSpec,
    PipelineSpec,
    SlotLoadSpec,
    SourceLocation,
    StepSpec,
    StrategyFile,
    VariableAssignment,
    VariableRef,
)


def expand_registered_factories(
    strategy: StrategyFile,
    registry: Mapping[str, Any],
    report: Callable[[str, SourceLocation, str, str], None],
) -> StrategyFile:
    """Pass 5b: expand every REGISTERED factory call (position-layer spec 03-R47).

    A call to a component of role ``factory`` (``StopLoss``, ``MaxHold`` …)
    becomes its ``factory_expansion`` executed at the call's effective
    arguments (``binding.expand_factory``, the template executor the TS
    validator and the resolver share): a nested ``PipelineSpec`` named
    canonically (``"StopLoss(pct=0.05)"``), recording the call, every step at
    the call's location. ``{"$scope": "prices"}`` resolves to the ``prices``
    of the TradeManager governing the call site (sequential bodies inherit
    it; a Parallel branch inherits its parent's; ``Exposure()`` /
    ``RiskSizer`` end it); ``$bars_of`` divides by ``Globals(target_timeframe=)``.
    A refused expansion reports its code on the call and leaves a refused
    marker the walk reads as an unknown value. A strategy with no factory call
    is returned unchanged (the same object).
    """
    from pipeline_engine.base.registry_types import is_factory
    from pipeline_engine.binding import (
        FactoryExpansionError,
        canonical_factory_name,
        expand_factory,
    )
    from pipeline_engine.validation_shared import TIMEFRAME_MINUTES

    tf = getattr(strategy.globals_, "target_timeframe", None) if strategy.globals_ else None
    kappa_token = tf if isinstance(tf, str) else None
    kappa_minutes = TIMEFRAME_MINUTES.get(kappa_token) if kappa_token else None
    changed = False

    def role_of(sig) -> str | None:
        decl = getattr(sig, "binding", None)
        return decl.get("role") if isinstance(decl, dict) else None

    def effective(step: ComponentRef, sig) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for pname, info in sig.parameters.items():
            if pname in step.params:
                out[pname] = step.params[pname]
            elif info.default is not MISSING:
                out[pname] = info.default
        return out

    def to_spec(plain: dict, location) -> StepSpec:
        if "parallel" in plain:
            return ParallelSpec(
                branches={
                    b: [to_spec(s, location) for s in steps]
                    for b, steps in plain["parallel"].items()
                },
                location=location,
            )
        if "load" in plain:
            return SlotLoadSpec(slot_name=plain["load"], location=location)
        return ComponentRef(
            name=plain["component"], params=dict(plain["params"]), location=location
        )

    def expand_call(step: ComponentRef, sig, prices: str | None) -> PipelineSpec:
        nonlocal changed
        changed = True
        args = effective(step, sig)
        name = canonical_factory_name(step.name, args)
        try:
            plain = expand_factory(
                step.name,
                sig.factory_expansion,
                args,
                kappa_minutes=kappa_minutes,
                kappa_token=kappa_token,
                prices_slot=prices,
            )
        except FactoryExpansionError as err:
            f = err.finding
            report(f.code, step.location, f.detail(), f.fix())
            return PipelineSpec(
                steps=[],
                name=name,
                location=step.location,
                factory_call=step,
                factory_refused=True,
            )
        return PipelineSpec(
            steps=[to_spec(p, step.location) for p in plain],
            name=name,
            location=step.location,
            factory_call=step,
        )

    def walk(steps: list[StepSpec], prices: str | None) -> tuple[list[StepSpec], str | None]:
        out: list[StepSpec] = []
        for step in steps:
            if isinstance(step, ComponentRef):
                sig = registry.get(step.name)
                role = role_of(sig) if sig is not None else None
                if sig is not None and is_factory(sig):
                    if not any(isinstance(v, VariableRef) for v in step.params.values()):
                        out.append(expand_call(step, sig, prices))
                        continue
                elif role == "head":
                    slot = step.params.get("prices", effective(step, sig).get("prices"))
                    prices = slot if isinstance(slot, str) else None
                elif role in ("realizer", "position_sizer"):
                    prices = None
                out.append(step)
            elif isinstance(step, ParallelSpec):
                out.append(
                    ParallelSpec(
                        branches={b: walk(s, prices)[0] for b, s in step.branches.items()},
                        location=step.location,
                    )
                )
            elif isinstance(step, PipelineSpec):
                body, prices = walk(step.steps, prices)
                out.append(
                    PipelineSpec(
                        steps=body,
                        name=step.name,
                        location=step.location,
                        factory_call=step.factory_call,
                        factory_refused=step.factory_refused,
                    )
                )
            else:
                out.append(step)
        return out, prices

    new_vars = []
    for var in strategy.variables:
        if isinstance(var.value, PipelineSpec):
            body, _ = walk(var.value.steps, None)
            new_vars.append(
                VariableAssignment(
                    name=var.name,
                    value=PipelineSpec(
                        steps=body, name=var.value.name, location=var.value.location
                    ),
                    location=var.location,
                )
            )
        else:
            new_vars.append(var)
    main, _ = walk(strategy.pipeline.steps, None)
    if not changed:
        return strategy
    return StrategyFile(
        metadata=strategy.metadata,
        factories=strategy.factories,
        variables=new_vars,
        pipeline=PipelineSpec(
            steps=main, name=strategy.pipeline.name, location=strategy.pipeline.location
        ),
        globals_=strategy.globals_,
        universe=strategy.universe,
        execution=strategy.execution,
    )


__all__ = ["expand_registered_factories"]
