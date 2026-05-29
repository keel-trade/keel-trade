"""DSL emitter and graph/spec converters.

This module bridges StrategyFile spec objects and the browser graph model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pipeline_engine.dsl.spec import (
    MISSING,
    ComponentRef,
    ExecutionSpec,
    FactoryCallSpec,
    FactoryDef,
    FactoryParam,
    GlobalsSpec,
    ParallelSpec,
    PipelineSpec,
    SlotExtractSpec,
    SlotLoadSpec,
    SlotStoreSpec,
    SlotStoreValueSpec,
    SourceLocation,
    StrategyFile,
    UniverseSpec,
    VariableAssignment,
    VariableRef,
)


def _block_id(parent_id: str, index: int, block_type: str, label: str = "") -> str:
    seed = f"{parent_id}:{index}:{block_type}:{label}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _loc(context: str, counter: list[int] | None = None) -> SourceLocation:
    """Create a SourceLocation with a monotonically increasing synthetic line number.

    Graph-converted specs have no real source positions. Incrementing line numbers
    preserve definition-before-use ordering so the validator's forward-reference
    check works correctly.
    """
    if counter is not None:
        counter[0] += 1
        return SourceLocation(line=counter[0], col=0, context=context)
    return SourceLocation(line=0, col=0, context=context)


@dataclass
class GraphBlock:
    id: str
    type: str
    component: str | None = None
    params: dict[str, Any] | None = None
    branches: dict[str, list["GraphBlock"]] | None = None
    factoryName: str | None = None
    factoryArgs: dict[str, Any] | None = None
    slotName: str | None = None
    slotValue: Any = None
    extractKey: str | None = None
    variableName: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.component is not None:
            data["component"] = self.component
        if self.params is not None:
            data["params"] = _serialize_value(self.params)
        if self.branches is not None:
            data["branches"] = {
                name: [block.to_dict() for block in blocks]
                for name, blocks in self.branches.items()
            }
        if self.factoryName is not None:
            data["factoryName"] = self.factoryName
        if self.factoryArgs is not None:
            data["factoryArgs"] = _serialize_value(self.factoryArgs)
        if self.slotName is not None:
            data["slotName"] = self.slotName
        if self.slotValue is not None:
            data["slotValue"] = _serialize_value(self.slotValue)
        if self.extractKey is not None:
            data["extractKey"] = self.extractKey
        if self.variableName is not None:
            data["variableName"] = self.variableName
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GraphBlock":
        block_type = data.get("type")
        block_id = data.get("id")
        if not isinstance(block_type, str) or not block_type:
            raise ValueError("Graph block missing non-empty 'type'")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("Graph block missing non-empty 'id'")

        branches_raw = data.get("branches")
        branches: dict[str, list[GraphBlock]] | None = None
        if branches_raw is not None:
            if not isinstance(branches_raw, dict):
                raise ValueError(f"Block '{block_id}' has invalid 'branches'")
            branches = {}
            for branch_name, branch_blocks in branches_raw.items():
                if not isinstance(branch_name, str):
                    raise ValueError(f"Block '{block_id}' branch name must be a string")
                if not isinstance(branch_blocks, list):
                    raise ValueError(f"Block '{block_id}' branch '{branch_name}' must be a list")
                branches[branch_name] = [
                    GraphBlock.from_dict(item) for item in branch_blocks if isinstance(item, dict)
                ]
                if len(branches[branch_name]) != len(branch_blocks):
                    raise ValueError(
                        f"Block '{block_id}' branch '{branch_name}' contains invalid block entries"
                    )

        return GraphBlock(
            id=block_id,
            type=block_type,
            component=data.get("component"),
            params=data.get("params"),
            branches=branches,
            factoryName=data.get("factoryName"),
            factoryArgs=data.get("factoryArgs"),
            slotName=data.get("slotName"),
            slotValue=data.get("slotValue"),
            extractKey=data.get("extractKey"),
            variableName=data.get("variableName"),
        )


@dataclass
class GraphFactoryParam:
    name: str
    default: Any = MISSING
    annotation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.default is not MISSING:
            data["default"] = _serialize_value(self.default)
        if self.annotation is not None:
            data["annotation"] = self.annotation
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GraphFactoryParam":
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Factory param missing non-empty 'name'")
        return GraphFactoryParam(
            name=name,
            default=data.get("default", MISSING),
            annotation=data.get("annotation"),
        )


@dataclass
class GraphFactoryDef:
    name: str
    params: list[GraphFactoryParam]
    body: list[GraphBlock]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": [param.to_dict() for param in self.params],
            "body": [block.to_dict() for block in self.body],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GraphFactoryDef":
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Factory definition missing non-empty 'name'")
        params_raw = data.get("params", [])
        body_raw = data.get("body", [])
        if not isinstance(params_raw, list) or not isinstance(body_raw, list):
            raise ValueError(f"Factory '{name}' has invalid params/body")
        params = [GraphFactoryParam.from_dict(p) for p in params_raw if isinstance(p, dict)]
        if len(params) != len(params_raw):
            raise ValueError(f"Factory '{name}' has invalid param entries")
        body = [GraphBlock.from_dict(b) for b in body_raw if isinstance(b, dict)]
        if len(body) != len(body_raw):
            raise ValueError(f"Factory '{name}' has invalid body block entries")
        return GraphFactoryDef(name=name, params=params, body=body)


@dataclass
class GraphVariableDef:
    name: str
    steps: list[GraphBlock] = field(default_factory=list)
    literalValue: Any = MISSING

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.steps:
            data["steps"] = [block.to_dict() for block in self.steps]
        if self.literalValue is not MISSING:
            data["literalValue"] = _serialize_value(self.literalValue)
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GraphVariableDef":
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Variable definition missing non-empty 'name'")
        steps_raw = data.get("steps", [])
        if steps_raw is None:
            steps_raw = []
        if not isinstance(steps_raw, list):
            raise ValueError(f"Variable '{name}' has invalid 'steps'")
        steps = [GraphBlock.from_dict(b) for b in steps_raw if isinstance(b, dict)]
        if len(steps) != len(steps_raw):
            raise ValueError(f"Variable '{name}' has invalid step entries")
        return GraphVariableDef(
            name=name,
            steps=steps,
            literalValue=data.get("literalValue", MISSING),
        )


@dataclass
class GraphModel:
    blocks: list[GraphBlock]
    factories: list[GraphFactoryDef] = field(default_factory=list)
    variables: list[GraphVariableDef] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    pipelineName: str | None = None
    globals_: dict[str, Any] | None = None
    universe: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if self.factories:
            data["factories"] = [factory.to_dict() for factory in self.factories]
        if self.variables:
            data["variables"] = [var.to_dict() for var in self.variables]
        if self.metadata:
            data["metadata"] = self.metadata
        if self.globals_ is not None:
            data["globals"] = self.globals_
        if self.universe is not None:
            data["universe"] = self.universe
        if self.execution is not None:
            data["execution"] = self.execution
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GraphModel":
        blocks_raw = data.get("blocks")
        if not isinstance(blocks_raw, list):
            raise ValueError("Graph model requires 'blocks' list")
        blocks = [GraphBlock.from_dict(b) for b in blocks_raw if isinstance(b, dict)]
        if len(blocks) != len(blocks_raw):
            raise ValueError("Graph model contains invalid block entries")

        factories_raw = data.get("factories") or []
        variables_raw = data.get("variables") or []
        metadata_raw = data.get("metadata") or {}
        if not isinstance(factories_raw, list):
            raise ValueError("Graph model 'factories' must be a list")
        if not isinstance(variables_raw, list):
            raise ValueError("Graph model 'variables' must be a list")
        if not isinstance(metadata_raw, dict):
            raise ValueError("Graph model 'metadata' must be an object")

        factories = [GraphFactoryDef.from_dict(f) for f in factories_raw if isinstance(f, dict)]
        if len(factories) != len(factories_raw):
            raise ValueError("Graph model contains invalid factory entries")
        variables = [GraphVariableDef.from_dict(v) for v in variables_raw if isinstance(v, dict)]
        if len(variables) != len(variables_raw):
            raise ValueError("Graph model contains invalid variable entries")

        metadata = {str(k): str(v) for k, v in metadata_raw.items()}
        # Read pipelineName for backward compat but discard it — top-level
        # pipeline naming is no longer exposed through the graph model.
        data.get("pipelineName")

        globals_raw = data.get("globals")
        universe_raw = data.get("universe")
        execution_raw = data.get("execution")

        return GraphModel(
            blocks=blocks,
            factories=factories,
            variables=variables,
            metadata=metadata,
            pipelineName=None,
            globals_=globals_raw if isinstance(globals_raw, dict) else None,
            universe=universe_raw if isinstance(universe_raw, dict) else None,
            execution=execution_raw if isinstance(execution_raw, dict) else None,
        )


def _serialize_value(value: Any) -> Any:
    if isinstance(value, VariableRef):
        return {"$ref": value.name}
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def _deserialize_value(value: Any, context: str, counter: list[int] | None = None) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {"$ref"} and isinstance(value["$ref"], str):
        return VariableRef(name=value["$ref"], location=_loc(context, counter))
    if isinstance(value, dict):
        return {k: _deserialize_value(v, context, counter) for k, v in value.items()}
    if isinstance(value, list):
        return [_deserialize_value(v, context, counter) for v in value]
    return value


def graph_to_spec(graph: GraphModel | dict[str, Any]) -> StrategyFile:
    """Convert browser graph model to StrategyFile."""
    model = GraphModel.from_dict(graph) if isinstance(graph, dict) else graph
    # Monotonically increasing line counter so factories (emitted first) get
    # lower line numbers than pipeline steps that reference them.
    lc: list[int] = [0]

    factories: list[FactoryDef] = []
    variables: list[VariableAssignment] = []
    for factory in model.factories:
        params = [
            FactoryParam(
                name=p.name,
                default=_deserialize_value(
                    p.default, f"factory[{factory.name}].param[{p.name}]", lc
                )
                if p.default is not MISSING
                else MISSING,
                annotation=p.annotation,
            )
            for p in factory.params
        ]
        body_steps = [
            _graph_block_to_step(block, f"factory[{factory.name}].{block.id}", lc)
            for block in factory.body
        ]
        if factory.params:
            factories.append(
                FactoryDef(
                    name=factory.name,
                    params=params,
                    body=PipelineSpec(
                        steps=body_steps,
                        name=factory.name,
                        location=_loc(f"factory[{factory.name}]", lc),
                    ),
                    location=_loc(f"factory[{factory.name}]", lc),
                )
            )
        else:
            # Zero-param factory → convert back to VariableAssignment
            variables.append(
                VariableAssignment(
                    name=factory.name,
                    value=PipelineSpec(
                        steps=body_steps,
                        name=factory.name,
                        location=_loc(f"factory[{factory.name}]", lc),
                    ),
                    location=_loc(f"factory[{factory.name}]", lc),
                )
            )

    for var in model.variables:
        if var.literalValue is not MISSING:
            variables.append(
                VariableAssignment(
                    name=var.name,
                    value=_deserialize_value(var.literalValue, f"var[{var.name}]", lc),
                    location=_loc(f"var[{var.name}]", lc),
                )
            )
            continue
        steps = [
            _graph_block_to_step(block, f"var[{var.name}].{block.id}", lc) for block in var.steps
        ]
        variables.append(
            VariableAssignment(
                name=var.name,
                value=PipelineSpec(
                    steps=steps, name=var.name, location=_loc(f"var[{var.name}]", lc)
                ),
                location=_loc(f"var[{var.name}]", lc),
            )
        )

    # Post-pass: demote factory_call(0 args) → VariableRef when the target
    # is a zero-param factory (i.e., a promoted variable).
    variable_names = {v.name for v in variables if isinstance(v.value, PipelineSpec)}

    def _demote_factory_calls(steps: list) -> list:
        result = []
        for step in steps:
            if isinstance(step, FactoryCallSpec) and not step.args and step.name in variable_names:
                result.append(VariableRef(name=step.name, location=step.location))
            elif isinstance(step, ParallelSpec):
                new_branches = {k: _demote_factory_calls(v) for k, v in step.branches.items()}
                result.append(ParallelSpec(branches=new_branches, location=step.location))
            elif isinstance(step, PipelineSpec):
                result.append(
                    PipelineSpec(
                        steps=_demote_factory_calls(step.steps),
                        name=step.name,
                        location=step.location,
                    )
                )
            else:
                result.append(step)
        return result

    pipeline_steps = _demote_factory_calls(
        [_graph_block_to_step(block, f"pipeline.{block.id}", lc) for block in model.blocks]
    )

    # Also demote in factory bodies (factories can call other variables)
    for f in factories:
        f.body = PipelineSpec(
            steps=_demote_factory_calls(f.body.steps),
            name=f.body.name,
            location=f.body.location,
        )

    # Also demote in variable bodies (variables can reference other variables)
    for v in variables:
        if isinstance(v.value, PipelineSpec):
            v.value = PipelineSpec(
                steps=_demote_factory_calls(v.value.steps),
                name=v.value.name,
                location=v.value.location,
            )

    # Reconstruct GlobalsSpec from graph model
    globals_spec = None
    if model.globals_:
        globals_spec = GlobalsSpec(
            target_timeframe=model.globals_.get("target_timeframe"),
            bar_offset=model.globals_.get("bar_offset"),
            location=_loc("globals", lc),
        )

    # Reconstruct UniverseSpec from graph model
    universe_spec = None
    if model.universe:
        universe_spec = UniverseSpec(
            mode=model.universe.get("mode", "manual"),
            market=model.universe.get("market", "perp"),
            symbols=model.universe.get("symbols"),
            categories=model.universe.get("categories"),
            top_n=model.universe.get("top_n"),
            exclusions=model.universe.get("exclusions"),
            inclusions=model.universe.get("inclusions"),
            lookback=model.universe.get("lookback"),
            volume_quartiles=model.universe.get("volume_quartiles"),
            resolved=model.universe.get("resolved"),
            resolved_at=model.universe.get("resolved_at"),
            groups=model.universe.get("groups"),
            location=_loc("universe", lc),
        )

    # Reconstruct ExecutionSpec from graph model
    execution_spec = None
    if model.execution:
        execution_spec = ExecutionSpec(
            rebalance=model.execution.get("rebalance", "every_bar"),
            on_change_tolerance=model.execution.get("on_change_tolerance", 1e-8),
            buffer_threshold=model.execution.get("buffer_threshold"),
            buffer_mode=model.execution.get("buffer_mode", "relative"),
            rebalance_method=model.execution.get("rebalance_method", "to_edge"),
            min_trade_size=model.execution.get("min_trade_size", 0.0),
            location=_loc("execution", lc),
        )

    return StrategyFile(
        metadata=dict(model.metadata) if model.metadata else {},
        factories=factories,
        variables=variables,
        pipeline=PipelineSpec(
            steps=pipeline_steps,
            name=None,
            location=_loc("pipeline", lc),
        ),
        globals_=globals_spec,
        universe=universe_spec,
        execution=execution_spec,
    )


def _graph_block_to_step(block: GraphBlock, context: str, counter: list[int] | None = None):
    if block.type == "component":
        if not block.component:
            raise ValueError(f"Graph block '{block.id}' missing component name")
        # Strip empty-string params — these are unset UI fields, not intentional values.
        # Letting them through would override meaningful defaults (e.g. timeframe='15min').
        raw_params = block.params or {}
        cleaned_params = {k: v for k, v in raw_params.items() if v != ""}
        params = _deserialize_value(cleaned_params, context, counter)
        return ComponentRef(
            name=block.component,
            params=params,
            location=_loc(block.id, counter),
        )
    if block.type == "parallel":
        branches = {}
        if not block.branches:
            raise ValueError(f"Parallel block '{block.id}' missing branches")
        for branch_name, branch_blocks in block.branches.items():
            branches[branch_name] = [
                _graph_block_to_step(b, f"{context}.branch[{branch_name}]", counter)
                for b in branch_blocks
            ]
        return ParallelSpec(branches=branches, location=_loc(block.id, counter))
    if block.type == "slot_store":
        if not block.slotName:
            raise ValueError(f"slot_store block '{block.id}' missing slotName")
        return SlotStoreSpec(slot_name=block.slotName, location=_loc(block.id, counter))
    if block.type == "slot_store_value":
        if not block.slotName:
            raise ValueError(f"slot_store_value block '{block.id}' missing slotName")
        # slotValue is the canonical field; fall back to params.value for compat
        value = block.slotValue
        if value is None and block.params:
            value = block.params.get("value")
        return SlotStoreValueSpec(
            slot_name=block.slotName,
            value=value,
            location=_loc(block.id, counter),
        )
    if block.type == "slot_load":
        if not block.slotName:
            raise ValueError(f"slot_load block '{block.id}' missing slotName")
        return SlotLoadSpec(slot_name=block.slotName, location=_loc(block.id, counter))
    if block.type == "slot_extract":
        key = block.extractKey
        if not key:
            raise ValueError(f"slot_extract block '{block.id}' missing extractKey")
        return SlotExtractSpec(key=key, location=_loc(block.id, counter))
    if block.type == "factory_call":
        if not block.factoryName:
            raise ValueError(f"factory_call block '{block.id}' missing factoryName")
        args = _deserialize_value(block.factoryArgs or {}, context, counter)
        return FactoryCallSpec(name=block.factoryName, args=args, location=_loc(block.id, counter))
    if block.type == "variable_ref":
        if not block.variableName:
            raise ValueError(f"variable_ref block '{block.id}' missing variableName")
        return VariableRef(name=block.variableName, location=_loc(block.id, counter))
    raise ValueError(f"Unsupported graph block type '{block.type}'")


def spec_to_graph(spec: StrategyFile) -> GraphModel:
    """Convert StrategyFile to browser graph model."""
    factories = []
    for factory in spec.factories:
        factories.append(
            GraphFactoryDef(
                name=factory.name,
                params=[
                    GraphFactoryParam(
                        name=param.name,
                        default=param.default,
                        annotation=param.annotation,
                    )
                    for param in factory.params
                ],
                body=_steps_to_graph_blocks(factory.body.steps, factory.name),
            )
        )

    # Unify pipeline variables as zero-param factories.
    # Literal variables stay in the variables array.
    variables: list[GraphVariableDef] = []
    for var in spec.variables:
        if isinstance(var.value, PipelineSpec):
            # Pipeline variable → factory with 0 params
            factories.append(
                GraphFactoryDef(
                    name=var.name,
                    params=[],
                    body=_steps_to_graph_blocks(var.value.steps, var.name),
                )
            )
        else:
            variables.append(
                GraphVariableDef(
                    name=var.name,
                    literalValue=_serialize_value(var.value),
                )
            )

    blocks = _steps_to_graph_blocks(spec.pipeline.steps, "root")

    # Post-pass: convert variable_ref blocks to factory_call when the variable
    # was promoted to a factory (pipeline variables).
    factory_names = {f.name for f in factories}
    _promote_to_factory_calls(blocks, factory_names)
    for f in factories:
        _promote_to_factory_calls(f.body, factory_names)

    # Convert GlobalsSpec to dict for graph model
    globals_dict = None
    if spec.globals_ is not None:
        globals_dict = {}
        if spec.globals_.target_timeframe is not None:
            globals_dict["target_timeframe"] = spec.globals_.target_timeframe
        if spec.globals_.bar_offset is not None:
            globals_dict["bar_offset"] = spec.globals_.bar_offset

    # Convert UniverseSpec to dict for graph model
    universe_dict = None
    if spec.universe is not None:
        universe_dict = {"mode": spec.universe.mode, "market": spec.universe.market}
        for attr in (
            "symbols",
            "categories",
            "top_n",
            "exclusions",
            "inclusions",
            "lookback",
            "volume_quartiles",
            "resolved",
            "resolved_at",
            "groups",
        ):
            val = getattr(spec.universe, attr)
            if val is not None:
                universe_dict[attr] = val

    # Convert ExecutionSpec to dict for graph model — driven by EXECUTION_PARAM_META
    execution_dict = None
    if spec.execution is not None:
        from pipeline_engine.dsl.spec import EXECUTION_PARAM_META

        execution_dict = {}
        ex = spec.execution
        for param_name, meta in EXECUTION_PARAM_META.items():
            val = getattr(ex, param_name, None)
            if val is None:
                continue
            # Only include params relevant to current mode (or mode-independent)
            modes = meta.get("modes")
            if modes and ex.rebalance not in modes and val == meta.get("default"):
                continue
            execution_dict[param_name] = val

    return GraphModel(
        blocks=blocks,
        factories=factories,
        variables=variables,
        metadata=dict(spec.metadata or {}),
        pipelineName=None,
        globals_=globals_dict,
        universe=universe_dict,
        execution=execution_dict,
    )


def _promote_to_factory_calls(blocks: list[GraphBlock], factory_names: set[str]) -> None:
    """Convert variable_ref and forward-referenced component blocks to factory_call."""
    for i, block in enumerate(blocks):
        # variable_ref → factory_call
        if (
            block.type == "variable_ref"
            and block.variableName
            and block.variableName in factory_names
        ):
            blocks[i] = GraphBlock(
                id=block.id,
                type="factory_call",
                factoryName=block.variableName,
                factoryArgs={},
            )
        # component that's actually a forward-referenced factory/variable
        if block.type == "component" and block.component and block.component in factory_names:
            blocks[i] = GraphBlock(
                id=block.id,
                type="factory_call",
                factoryName=block.component,
                factoryArgs=block.params or {},
            )
        if block.type == "parallel" and block.branches:
            for branch_blocks in block.branches.values():
                _promote_to_factory_calls(branch_blocks, factory_names)


def _steps_to_graph_blocks(steps, parent_id: str) -> list[GraphBlock]:
    """Convert a list of spec steps to graph blocks, flattening nested PipelineSpecs."""
    blocks: list[GraphBlock] = []
    for idx, step in enumerate(steps):
        if isinstance(step, PipelineSpec):
            blocks.extend(_steps_to_graph_blocks(step.steps, parent_id))
        else:
            blocks.append(_step_to_graph_block(step, parent_id, idx))
    return blocks


def _step_to_graph_block(step, parent_id: str, index: int) -> GraphBlock:
    if isinstance(step, ComponentRef):
        block_id = _block_id(parent_id, index, "component", step.name)
        return GraphBlock(
            id=block_id,
            type="component",
            component=step.name,
            params=_serialize_value(step.params or {}),
        )
    if isinstance(step, ParallelSpec):
        block_id = _block_id(parent_id, index, "parallel")
        branches = {}
        for branch_name, branch_steps in step.branches.items():
            branches[branch_name] = _steps_to_graph_blocks(
                branch_steps, f"{block_id}:{branch_name}"
            )
        return GraphBlock(id=block_id, type="parallel", branches=branches)
    if isinstance(step, SlotStoreSpec):
        block_id = _block_id(parent_id, index, "slot_store", step.slot_name)
        return GraphBlock(id=block_id, type="slot_store", slotName=step.slot_name)
    if isinstance(step, SlotStoreValueSpec):
        block_id = _block_id(parent_id, index, "slot_store_value", step.slot_name)
        return GraphBlock(
            id=block_id,
            type="slot_store_value",
            slotName=step.slot_name,
            slotValue=step.value,
        )
    if isinstance(step, SlotLoadSpec):
        block_id = _block_id(parent_id, index, "slot_load", step.slot_name)
        return GraphBlock(id=block_id, type="slot_load", slotName=step.slot_name)
    if isinstance(step, SlotExtractSpec):
        block_id = _block_id(parent_id, index, "slot_extract", step.key)
        return GraphBlock(id=block_id, type="slot_extract", extractKey=step.key)
    if isinstance(step, FactoryCallSpec):
        block_id = _block_id(parent_id, index, "factory_call", step.name)
        return GraphBlock(
            id=block_id,
            type="factory_call",
            factoryName=step.name,
            factoryArgs=_serialize_value(step.args or {}),
        )
    if isinstance(step, VariableRef):
        block_id = _block_id(parent_id, index, "variable_ref", step.name)
        return GraphBlock(id=block_id, type="variable_ref", variableName=step.name)
    if isinstance(step, PipelineSpec):
        raise ValueError(
            "PipelineSpec in _step_to_graph_block should be handled by _steps_to_graph_blocks"
        )
    raise ValueError(f"Unsupported step type for graph conversion: {type(step).__name__}")


def spec_to_dsl(spec: StrategyFile) -> str:
    """Emit canonical DSL text from StrategyFile."""
    lines: list[str] = []

    # Emit Globals declaration
    if spec.globals_ is not None:
        globals_args = []
        if spec.globals_.target_timeframe is not None:
            globals_args.append(f"target_timeframe={_emit_value(spec.globals_.target_timeframe)}")
        if spec.globals_.bar_offset is not None:
            globals_args.append(f"bar_offset={_emit_value(spec.globals_.bar_offset)}")
        lines.append(f"Globals({', '.join(globals_args)})")
        lines.append("")

    # Emit Universe declaration
    if spec.universe is not None:
        universe_args = [f"mode={_emit_value(spec.universe.mode)}"]
        if spec.universe.market != "perp":
            universe_args.append(f"market={_emit_value(spec.universe.market)}")
        for attr in (
            "symbols",
            "categories",
            "top_n",
            "exclusions",
            "inclusions",
            "lookback",
            "volume_quartiles",
            "resolved",
            "resolved_at",
            "groups",
        ):
            val = getattr(spec.universe, attr)
            if val is not None:
                universe_args.append(f"{attr}={_emit_value(val)}")
        lines.append(f"Universe({', '.join(universe_args)})")
        lines.append("")

    # Emit Execution declaration — driven by EXECUTION_PARAM_META
    if spec.execution is not None:
        from pipeline_engine.dsl.spec import EXECUTION_PARAM_META

        exec_args = []
        ex = spec.execution
        for param_name, meta in EXECUTION_PARAM_META.items():
            val = getattr(ex, param_name, meta.get("default"))
            if val is None:
                continue
            modes = meta.get("modes")
            is_default = val == meta.get("default")
            # Always emit: rebalance, or params flagged always_emit
            if meta.get("always_emit"):
                exec_args.append(f"{param_name}={_emit_value(val)}")
                continue
            # Mode-specific params: emit if mode is active (even at default)
            if modes:
                if ex.rebalance in modes:
                    exec_args.append(f"{param_name}={_emit_value(val)}")
                # Skip if mode not active (irrelevant param)
                continue
            # Mode-independent params: only emit if non-default
            if not is_default:
                exec_args.append(f"{param_name}={_emit_value(val)}")
        lines.append(f"Execution({', '.join(exec_args)})")
        lines.append("")

    for factory in spec.factories:
        params = []
        for param in factory.params:
            if param.default is MISSING:
                params.append(param.name)
            else:
                params.append(f"{param.name}={_emit_value(param.default)}")
        lines.append(f"def {factory.name}({', '.join(params)}):")
        pipeline_expr = _emit_pipeline_expr(factory.body, indent=4)
        lines.append(f"    return {pipeline_expr[0]}")
        for extra_line in pipeline_expr[1:]:
            lines.append(extra_line)
        lines.append("")

    for var in spec.variables:
        if isinstance(var.value, PipelineSpec):
            expr_lines = _emit_pipeline_expr(var.value, indent=0)
            lines.append(f"{var.name} = {expr_lines[0]}")
            lines.extend(expr_lines[1:])
        else:
            lines.append(f"{var.name} = {_emit_value(var.value)}")
        lines.append("")

    # Clear top-level pipeline name before emitting — inner pipeline names
    # (factories, variables) are kept for debugging, but the top-level name
    # is no longer exposed in DSL output.
    top_pipeline = PipelineSpec(
        steps=spec.pipeline.steps,
        name=None,
        location=spec.pipeline.location,
    )
    main_expr = _emit_pipeline_expr(top_pipeline, indent=0)
    lines.extend(main_expr)

    # Normalize trailing whitespace and final newline
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def _emit_pipeline_expr(pipeline: PipelineSpec, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = ["Pipeline(["]
    for step in pipeline.steps:
        step_lines = _emit_step_expr(step, indent + 4)
        if len(step_lines) == 1:
            lines.append(f"{' ' * (indent + 4)}{step_lines[0]},")
        else:
            lines.append(f"{' ' * (indent + 4)}{step_lines[0]}")
            for mid in step_lines[1:-1]:
                lines.append(mid)
            lines.append(f"{step_lines[-1]},")
    closing = f"{pad}]"
    if pipeline.name is not None:
        closing += f", name={_emit_value(pipeline.name)}"
    closing += ")"
    lines.append(closing)
    return [f"{pad}{line}" if i == 0 else line for i, line in enumerate(lines)]


def _emit_step_expr(step, indent: int) -> list[str]:
    if isinstance(step, ComponentRef):
        return [_emit_call(step.name, step.params)]
    if isinstance(step, FactoryCallSpec):
        return [_emit_call(step.name, step.args)]
    if isinstance(step, SlotStoreSpec):
        return [f"Store({_emit_value(step.slot_name)})"]
    if isinstance(step, SlotStoreValueSpec):
        return [f"StoreValue({_emit_value(step.slot_name)}, {_emit_value(step.value)})"]
    if isinstance(step, SlotLoadSpec):
        return [f"Load({_emit_value(step.slot_name)})"]
    if isinstance(step, SlotExtractSpec):
        return [f"Extract(key={_emit_value(step.key)})"]
    if isinstance(step, VariableRef):
        return [step.name]
    if isinstance(step, PipelineSpec):
        return _emit_pipeline_expr(step, indent)
    if isinstance(step, ParallelSpec):
        pad = " " * indent
        lines = ["{"]
        for branch_name, branch_steps in step.branches.items():
            lines.append(f"{pad}    {_emit_value(branch_name)}: [")
            for branch_step in branch_steps:
                branch_step_lines = _emit_step_expr(branch_step, indent + 8)
                if len(branch_step_lines) == 1:
                    lines.append(f"{pad}        {branch_step_lines[0]},")
                else:
                    lines.append(f"{pad}        {branch_step_lines[0]}")
                    for mid in branch_step_lines[1:-1]:
                        lines.append(mid)
                    lines.append(f"{branch_step_lines[-1]},")
            lines.append(f"{pad}    ],")
        lines.append(f"{pad}}}")
        return lines
    raise ValueError(f"Unsupported step type for DSL emission: {type(step).__name__}")


def _emit_call(name: str, args: dict[str, Any] | None) -> str:
    if not args:
        return f"{name}()"
    ordered = [f"{key}={_emit_value(value)}" for key, value in args.items()]
    return f"{name}({', '.join(ordered)})"


def _emit_value(value: Any) -> str:
    if isinstance(value, VariableRef):
        return value.name
    if value is MISSING:
        return "None"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, dict):
        items = [f"{_emit_value(k)}: {_emit_value(v)}" for k, v in value.items()]
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_emit_value(v) for v in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(_emit_value(v) for v in value)
        if len(value) == 1:
            inner += ","
        return "(" + inner + ")"
    return repr(value)


__all__ = [
    "GraphBlock",
    "GraphFactoryDef",
    "GraphFactoryParam",
    "GraphModel",
    "GraphVariableDef",
    "graph_to_spec",
    "spec_to_dsl",
    "spec_to_graph",
]
