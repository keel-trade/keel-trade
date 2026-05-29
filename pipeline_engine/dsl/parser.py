"""AST-based parser for Pipeline DSL strategy files.

Takes a .strategy file (valid Python syntax) and produces a StrategyFile
tree of spec dataclasses. Only allows the restricted constructs from
spec Section 2.2 — everything else is a parse error.

Quick Start:
    >>> from pipeline_engine.dsl.parser import parse_strategy
    >>> sf = parse_strategy('Pipeline([ROC(period=8)], name="test")')
"""

from __future__ import annotations

import ast
import re
from typing import Any

from pipeline_engine.dsl.spec import (
    EXECUTION_PARAM_NAMES,
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
    StepSpec,
    StrategyFile,
    UniverseSpec,
    VariableAssignment,
    VariableRef,
)


class DSLParseError(Exception):
    """Error raised when the DSL parser encounters invalid syntax."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        self.line = line
        self.col = col
        if line is not None:
            prefix = f"Parse error at line {line}"
            if col is not None:
                prefix += f", col {col}"
            super().__init__(f"{prefix}: {message}")
        else:
            super().__init__(f"Parse error: {message}")


def _loc(node: ast.AST, context: str = "") -> SourceLocation:
    """Create a SourceLocation from an AST node."""
    return SourceLocation(
        line=getattr(node, "lineno", 0),
        col=getattr(node, "col_offset", 0),
        context=context,
    )


def _error(message: str, node: ast.AST | None = None) -> DSLParseError:
    """Create a DSLParseError with location from an AST node."""
    if node is not None:
        return DSLParseError(
            message,
            line=getattr(node, "lineno", None),
            col=getattr(node, "col_offset", None),
        )
    return DSLParseError(message)


def _get_call_name(node: ast.Call) -> str | None:
    """Extract function name from a Call node, or None if not a simple Name."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _extract_metadata(source: str) -> dict[str, str]:
    """Extract key-value metadata from leading comment lines.

    Parses lines like ``# name: momentum_carry`` at the top of the file,
    stopping at the first non-comment, non-blank line.
    """
    metadata: dict[str, str] = {}
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        match = re.match(r"^#\s*(\w+)\s*:\s*(.+)$", stripped)
        if match:
            metadata[match.group(1)] = match.group(2).strip()
    return metadata


def _parse_param_value(node: ast.expr, factory_names: set[str]) -> Any:
    """Parse a parameter value from an AST expression node.

    Returns Python literals or VariableRef for Name nodes.
    """
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return VariableRef(name=node.id, location=_loc(node, f"param_ref[{node.id}]"))

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)):
            return -node.operand.value
        raise _error("Unary minus only allowed on numeric literals", node)

    if isinstance(node, ast.List):
        return [_parse_param_value(elt, factory_names) for elt in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_parse_param_value(elt, factory_names) for elt in node.elts)

    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                raise _error("Dict unpacking (**) not allowed in parameters", node)
            k = _parse_param_value(key, factory_names)
            v = _parse_param_value(value, factory_names)
            result[k] = v
        return result

    if isinstance(node, ast.Set):
        return {_parse_param_value(elt, factory_names) for elt in node.elts}

    # Reject everything else
    if isinstance(node, ast.BinOp):
        raise _error("Computed expressions not allowed in parameters", node)
    if isinstance(node, ast.JoinedStr):
        raise _error("f-strings not allowed", node)
    if isinstance(node, ast.Attribute):
        raise _error("Attribute access not allowed", node)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise _error("Comprehensions not allowed", node)
    if isinstance(node, ast.Call):
        raise _error("Function calls not allowed in parameter values", node)

    raise _error(f"Unsupported expression type: {type(node).__name__}", node)


def _parse_keyword_args(
    keywords: list[ast.keyword],
    factory_names: set[str],
    context_name: str,
    node: ast.Call,
) -> dict[str, Any]:
    """Parse keyword arguments, rejecting positional args."""
    params: dict[str, Any] = {}
    for kw in keywords:
        if kw.arg is None:
            raise _error(f"**kwargs not allowed in {context_name}", node)
        params[kw.arg] = _parse_param_value(kw.value, factory_names)
    return params


def _parse_step(node: ast.expr, factory_names: set[str], context: str = "") -> StepSpec:
    """Parse a single step expression from within a Pipeline step list."""
    # Call expression: Pipeline, Store, Load, factory call, or component
    if isinstance(node, ast.Call):
        name = _get_call_name(node)

        if name is None:
            if isinstance(node.func, ast.Attribute):
                raise _error("Attribute access not allowed", node)
            raise _error("Only simple function calls allowed (no method calls)", node)

        # Pipeline(...) -> PipelineSpec
        if name == "Pipeline":
            return _parse_pipeline_call(node, factory_names, context)

        # Store("slot_name") -> SlotStoreSpec
        if name == "Store":
            return _parse_store(node)

        # Load("slot_name") -> SlotLoadSpec
        if name == "Load":
            return _parse_load(node)

        # StoreValue("slot_name", value) -> SlotStoreValueSpec
        if name == "StoreValue":
            return _parse_store_value(node)

        # Extract("key") -> SlotExtractSpec
        if name == "Extract":
            return _parse_extract(node)

        # Factory call -> FactoryCallSpec
        if name in factory_names:
            if node.args:
                raise _error(
                    f"Positional args not allowed in factory call '{name}', "
                    f"use {name}(param=value)",
                    node,
                )
            args = _parse_keyword_args(node.keywords, factory_names, f"factory call '{name}'", node)
            return FactoryCallSpec(
                name=name,
                args=args,
                location=_loc(node, f"factory_call[{name}]"),
            )

        # Component call -> ComponentRef
        if node.args:
            raise _error(
                f"Positional args not allowed, use {name}(param=value)",
                node,
            )
        params = _parse_keyword_args(node.keywords, factory_names, f"component '{name}'", node)
        return ComponentRef(
            name=name,
            params=params,
            location=_loc(node, context or f"component[{name}]"),
        )

    # Dict literal -> ParallelSpec
    if isinstance(node, ast.Dict):
        return _parse_parallel(node, factory_names, context)

    # Name -> VariableRef
    if isinstance(node, ast.Name):
        return VariableRef(
            name=node.id,
            location=_loc(node, context or f"ref[{node.id}]"),
        )

    # List -> inline step list (each element is a step) — used in parallel branches
    if isinstance(node, ast.List):
        # This shouldn't happen at step level since steps are always inside a list
        # but the list itself appears as a branch value in ParallelSpec
        raise _error(
            "Bare list not allowed as a step. Use Pipeline([...]) for sub-pipelines",
            node,
        )

    # Reject everything else at step level
    if isinstance(node, ast.Constant):
        raise _error("Literal values not allowed as pipeline steps", node)
    if isinstance(node, ast.BinOp):
        raise _error("Computed expressions not allowed", node)
    if isinstance(node, ast.JoinedStr):
        raise _error("f-strings not allowed", node)
    if isinstance(node, ast.Attribute):
        raise _error("Attribute access not allowed", node)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise _error("Comprehensions not allowed", node)

    raise _error(f"Unsupported step expression: {type(node).__name__}", node)


def _parse_step_list(
    nodes: list[ast.expr], factory_names: set[str], context: str = ""
) -> list[StepSpec]:
    """Parse a list of step expressions."""
    steps: list[StepSpec] = []
    for i, node in enumerate(nodes):
        step_context = f"{context}.step[{i}]" if context else f"step[{i}]"
        steps.append(_parse_step(node, factory_names, step_context))
    return steps


def _parse_pipeline_call(
    node: ast.Call, factory_names: set[str], context: str = ""
) -> PipelineSpec:
    """Parse a Pipeline(...) call into a PipelineSpec."""
    name = _get_call_name(node)
    if name != "Pipeline":
        raise _error(f"Expected Pipeline call, got {name}", node)

    # Extract step list (first positional arg must be a list)
    if not node.args:
        raise _error("Pipeline() requires a step list: Pipeline([step1, step2, ...])", node)

    if len(node.args) > 1:
        raise _error("Pipeline() takes exactly one positional argument (the step list)", node)

    step_list_node = node.args[0]
    if not isinstance(step_list_node, ast.List):
        raise _error("Pipeline() argument must be a list: Pipeline([...])", node)

    steps = _parse_step_list(step_list_node.elts, factory_names, context)

    # Extract name keyword arg
    pipeline_name: str | None = None
    for kw in node.keywords:
        if kw.arg == "name":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                pipeline_name = kw.value.value
            else:
                raise _error("Pipeline name must be a string literal", kw.value)
        elif kw.arg in ("mode", "phase_order"):
            raise _error(
                f"Pipeline '{kw.arg}' is an execution-time setting, not a DSL setting. "
                f"Configure it via keel backtest/deploy.",
                node,
            )
        else:
            raise _error(f"Unknown Pipeline keyword argument: {kw.arg}", node)

    return PipelineSpec(
        steps=steps,
        name=pipeline_name,
        location=_loc(node, context or "pipeline"),
    )


def _parse_store(node: ast.Call) -> SlotStoreSpec:
    """Parse Store("slot_name") into SlotStoreSpec."""
    if node.keywords:
        raise _error("Store takes a single positional string argument, not keyword args", node)
    if len(node.args) != 1:
        raise _error('Store takes exactly one argument: Store("slot_name")', node)
    arg = node.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        raise _error("Store argument must be a string literal", node)
    return SlotStoreSpec(
        slot_name=arg.value,
        location=_loc(node, f"store[{arg.value}]"),
    )


def _parse_load(node: ast.Call) -> SlotLoadSpec:
    """Parse Load("slot_name") into SlotLoadSpec."""
    if node.keywords:
        raise _error("Load takes a single positional string argument, not keyword args", node)
    if len(node.args) != 1:
        raise _error('Load takes exactly one argument: Load("slot_name")', node)
    arg = node.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        raise _error("Load argument must be a string literal", node)
    return SlotLoadSpec(
        slot_name=arg.value,
        location=_loc(node, f"load[{arg.value}]"),
    )


def _parse_extract(node: ast.Call) -> SlotExtractSpec:
    """Parse Extract("key") or Extract(key="key") into SlotExtractSpec."""
    # Accept either positional or keyword 'key' arg
    if len(node.args) == 1 and not node.keywords:
        arg = node.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            raise _error("Extract argument must be a string literal", node)
        return SlotExtractSpec(
            key=arg.value,
            location=_loc(node, f"extract[{arg.value}]"),
        )
    if not node.args and len(node.keywords) == 1 and node.keywords[0].arg == "key":
        kw = node.keywords[0]
        if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, str):
            raise _error("Extract key must be a string literal", node)
        return SlotExtractSpec(
            key=kw.value.value,
            location=_loc(node, f"extract[{kw.value.value}]"),
        )
    raise _error('Extract takes one argument: Extract("key") or Extract(key="key")', node)


def _parse_store_value(node: ast.Call) -> SlotStoreValueSpec:
    """Parse StoreValue("slot_name", value) into SlotStoreValueSpec."""
    if node.keywords:
        raise _error(
            "StoreValue takes two positional arguments, not keyword args: "
            'StoreValue("slot_name", value)',
            node,
        )
    if len(node.args) != 2:
        raise _error(
            'StoreValue takes exactly two arguments: StoreValue("slot_name", value)',
            node,
        )
    slot_arg = node.args[0]
    if not isinstance(slot_arg, ast.Constant) or not isinstance(slot_arg.value, str):
        raise _error("StoreValue first argument must be a string literal (slot name)", node)
    value_arg = node.args[1]
    if not isinstance(value_arg, ast.Constant):
        raise _error("StoreValue second argument must be a literal value", node)
    return SlotStoreValueSpec(
        slot_name=slot_arg.value,
        value=value_arg.value,
        location=_loc(node, f"store_value[{slot_arg.value}]"),
    )


def _parse_parallel(node: ast.Dict, factory_names: set[str], context: str = "") -> ParallelSpec:
    """Parse a dict literal into ParallelSpec."""
    branches: dict[str, list[StepSpec]] = {}
    for key, value in zip(node.keys, node.values):
        if key is None:
            raise _error("Dict unpacking (**) not allowed in parallel spec", node)
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise _error("Parallel branch names must be string literals", key if key else node)

        branch_name = key.value
        branch_context = f"{context}.branch[{branch_name}]" if context else f"branch[{branch_name}]"

        if isinstance(value, ast.List):
            branches[branch_name] = _parse_step_list(value.elts, factory_names, branch_context)
        else:
            # Single step as branch value (e.g., factory call)
            branches[branch_name] = [_parse_step(value, factory_names, branch_context)]

    return ParallelSpec(
        branches=branches,
        location=_loc(node, context or "parallel"),
    )


def _parse_factory_def(node: ast.FunctionDef, factory_names: set[str]) -> FactoryDef:
    """Parse a factory definition (def name(...): return Pipeline([...]))."""
    name = node.name

    # Validate: no *args, **kwargs
    if node.args.vararg:
        raise _error(f"*args not allowed in factory '{name}'", node)
    if node.args.kwarg:
        raise _error(f"**kwargs not allowed in factory '{name}'", node)

    # Validate: no decorators
    if node.decorator_list:
        raise _error(f"Decorators not allowed on factory '{name}'", node)

    # Validate: body must be single return statement
    if len(node.body) != 1:
        raise _error(
            f"Factory body must be a single return Pipeline([...]) statement, "
            f"got {len(node.body)} statements",
            node,
        )

    stmt = node.body[0]
    if not isinstance(stmt, ast.Return):
        raise _error(
            "Factory body must be a single return Pipeline([...]) statement",
            stmt,
        )

    if stmt.value is None:
        raise _error("Factory must return Pipeline([...])", stmt)

    # The return value must be a Pipeline(...) call
    if not isinstance(stmt.value, ast.Call) or _get_call_name(stmt.value) != "Pipeline":
        raise _error(
            "Factory body must be a single return Pipeline([...]) statement",
            stmt.value,
        )

    # Extract parameters
    params: list[FactoryParam] = []
    args = node.args

    # Combine positional args with their defaults
    # defaults are right-aligned: if 3 args and 1 default, args[2] has the default
    n_args = len(args.args)
    n_defaults = len(args.defaults)
    defaults_offset = n_args - n_defaults

    for i, arg in enumerate(args.args):
        default_idx = i - defaults_offset
        if default_idx >= 0:
            default_value = _parse_param_value(args.defaults[default_idx], factory_names)
        else:
            default_value = MISSING

        annotation_str = None
        if arg.annotation:
            annotation_str = ast.dump(arg.annotation)

        params.append(FactoryParam(name=arg.arg, default=default_value, annotation=annotation_str))

    # Parse the Pipeline body with factory_names that includes this factory
    # (for recursive factories — though spec doesn't allow them, keep consistent)
    body = _parse_pipeline_call(stmt.value, factory_names, f"factory[{name}]")

    return FactoryDef(
        name=name,
        params=params,
        body=body,
        location=_loc(node, f"factory[{name}]"),
    )


def _parse_variable_assignment(node: ast.Assign, factory_names: set[str]) -> VariableAssignment:
    """Parse a variable assignment: name = Pipeline([...]) or name = literal."""
    # Must be a single target, simple Name
    if len(node.targets) != 1:
        raise _error("Multiple assignment targets not allowed", node)

    target = node.targets[0]
    if not isinstance(target, ast.Name):
        raise _error("Assignment target must be a simple variable name", target)

    var_name = target.id
    value_node = node.value

    # Pipeline call -> PipelineSpec
    if isinstance(value_node, ast.Call) and _get_call_name(value_node) == "Pipeline":
        value = _parse_pipeline_call(value_node, factory_names, f"var[{var_name}]")
    elif isinstance(value_node, ast.Call):
        # Component call as variable -> error (T2.9)
        call_name = _get_call_name(value_node) or "unknown"
        raise _error(
            f"Component calls can only appear inside Pipeline step lists. "
            f"Use: {var_name} = Pipeline([{call_name}(...)]) instead of "
            f"{var_name} = {call_name}(...)",
            value_node,
        )
    elif isinstance(value_node, ast.Constant):
        value = value_node.value
    elif isinstance(value_node, ast.UnaryOp) and isinstance(value_node.op, ast.USub):
        if isinstance(value_node.operand, ast.Constant) and isinstance(
            value_node.operand.value, (int, float)
        ):
            value = -value_node.operand.value
        else:
            raise _error("Computed expressions not allowed in variable assignments", value_node)
    elif isinstance(value_node, ast.List):
        value = [_parse_param_value(elt, factory_names) for elt in value_node.elts]
    elif isinstance(value_node, ast.Dict):
        value = {}
        for key, val in zip(value_node.keys, value_node.values):
            if key is None:
                raise _error("Dict unpacking not allowed", value_node)
            k = _parse_param_value(key, factory_names)
            v = _parse_param_value(val, factory_names)
            value[k] = v
    elif isinstance(value_node, ast.Tuple):
        value = tuple(_parse_param_value(elt, factory_names) for elt in value_node.elts)
    elif isinstance(value_node, ast.Name):
        # Variable referencing another variable — allowed as a literal copy
        value = VariableRef(name=value_node.id, location=_loc(value_node, f"var[{var_name}]"))
    else:
        raise _error(
            f"Variable assignments must be Pipeline([...]) or literal values. "
            f"Got: {type(value_node).__name__}",
            value_node,
        )

    return VariableAssignment(
        name=var_name,
        value=value,
        location=_loc(node, f"var[{var_name}]"),
    )


_VALID_GLOBALS_KEYS = {"target_timeframe", "bar_offset"}

_VALID_UNIVERSE_KEYS = {
    "mode",
    "market",
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
}


def _parse_globals_call(node: ast.Call) -> GlobalsSpec:
    """Parse Globals(...) into GlobalsSpec."""
    if node.args:
        raise _error("Globals() takes only keyword arguments", node)

    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error("**kwargs not allowed in Globals()", node)
        if kw.arg not in _VALID_GLOBALS_KEYS:
            raise _error(
                f"Unknown global '{kw.arg}'. Available: {sorted(_VALID_GLOBALS_KEYS)}",
                node,
            )
        if not isinstance(kw.value, ast.Constant):
            raise _error(
                f"Globals({kw.arg}=...) value must be a string literal",
                kw.value,
            )
        kwargs[kw.arg] = kw.value.value

    return GlobalsSpec(
        target_timeframe=kwargs.get("target_timeframe"),
        bar_offset=kwargs.get("bar_offset"),
        location=_loc(node, "globals"),
    )


_VALID_EXECUTION_KEYS = EXECUTION_PARAM_NAMES


def _parse_execution_call(node: ast.Call, factory_names: set[str]) -> ExecutionSpec:
    """Parse Execution(...) into ExecutionSpec."""
    if node.args:
        raise _error("Execution() takes only keyword arguments", node)

    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error("**kwargs not allowed in Execution()", node)
        if kw.arg not in _VALID_EXECUTION_KEYS:
            raise _error(
                f"Unknown Execution parameter '{kw.arg}'. "
                f"Available: {sorted(_VALID_EXECUTION_KEYS)}",
                node,
            )
        kwargs[kw.arg] = _parse_param_value(kw.value, factory_names)

    # Derive defaults from EXECUTION_PARAM_META (single source of truth)
    from pipeline_engine.dsl.spec import EXECUTION_PARAM_META

    spec_kwargs: dict[str, Any] = {"location": _loc(node, "execution")}
    for param_name, meta in EXECUTION_PARAM_META.items():
        if param_name in kwargs:
            spec_kwargs[param_name] = kwargs[param_name]
        elif meta.get("default") is not None:
            spec_kwargs[param_name] = meta["default"]
    return ExecutionSpec(**spec_kwargs)


def _parse_universe_call(node: ast.Call, factory_names: set[str]) -> UniverseSpec:
    """Parse Universe(...) into UniverseSpec."""
    if node.args:
        raise _error("Universe() takes only keyword arguments", node)

    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error("**kwargs not allowed in Universe()", node)
        if kw.arg not in _VALID_UNIVERSE_KEYS:
            raise _error(
                f"Unknown Universe parameter '{kw.arg}'. Available: {sorted(_VALID_UNIVERSE_KEYS)}",
                node,
            )
        kwargs[kw.arg] = _parse_param_value(kw.value, factory_names)

    return UniverseSpec(
        mode=kwargs.get("mode", "manual"),
        market=kwargs.get("market", "perp"),
        symbols=kwargs.get("symbols"),
        categories=kwargs.get("categories"),
        top_n=kwargs.get("top_n"),
        exclusions=kwargs.get("exclusions"),
        inclusions=kwargs.get("inclusions"),
        lookback=kwargs.get("lookback"),
        volume_quartiles=kwargs.get("volume_quartiles"),
        resolved=kwargs.get("resolved"),
        resolved_at=kwargs.get("resolved_at"),
        groups=kwargs.get("groups"),
        location=_loc(node, "universe"),
    )


def parse_strategy(source: str) -> StrategyFile:
    """Parse a strategy DSL source string into a StrategyFile.

    Args:
        source: The DSL source code (valid Python syntax with restricted constructs).

    Returns:
        StrategyFile containing metadata, factories, variables, and the pipeline.

    Raises:
        DSLParseError: If the source contains disallowed constructs or is malformed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise DSLParseError(f"Invalid Python syntax: {e.msg}", line=e.lineno, col=e.offset)

    metadata = _extract_metadata(source)
    factories: list[FactoryDef] = []
    variables: list[VariableAssignment] = []
    pipeline: PipelineSpec | None = None
    globals_spec: GlobalsSpec | None = None
    universe_spec: UniverseSpec | None = None
    execution_spec: ExecutionSpec | None = None
    factory_names: set[str] = set()

    # Track declaration ordering for enforcement
    _seen_globals = False
    _seen_universe = False
    _seen_execution = False
    _seen_var_or_factory = False
    _seen_pipeline = False

    for node in tree.body:
        # Function definition -> Factory
        if isinstance(node, ast.FunctionDef):
            _seen_var_or_factory = True
            factory = _parse_factory_def(node, factory_names)
            factories.append(factory)
            factory_names.add(factory.name)
            continue

        # Assignment -> Variable
        if isinstance(node, ast.Assign):
            _seen_var_or_factory = True
            var = _parse_variable_assignment(node, factory_names)
            variables.append(var)
            continue

        # Expression statement -> Globals, Universe, or Pipeline
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call):
                call_name = _get_call_name(node.value)

                # Globals(...)
                if call_name == "Globals":
                    if _seen_globals:
                        raise _error("Only one Globals declaration allowed", node)
                    if _seen_universe:
                        raise _error("Globals must appear before Universe", node)
                    if _seen_pipeline:
                        raise _error("Globals must appear before Pipeline", node)
                    _seen_globals = True
                    globals_spec = _parse_globals_call(node.value)
                    continue

                # Universe(...)
                if call_name == "Universe":
                    if _seen_universe:
                        raise _error("Only one Universe declaration allowed", node)
                    if _seen_pipeline:
                        raise _error("Universe must appear before Pipeline", node)
                    _seen_universe = True
                    universe_spec = _parse_universe_call(node.value, factory_names)
                    continue

                # Execution(...)
                if call_name == "Execution":
                    if _seen_execution:
                        raise _error("Only one Execution declaration allowed", node)
                    if _seen_var_or_factory:
                        raise _error("Execution must appear before factories and variables", node)
                    if _seen_pipeline:
                        raise _error("Execution must appear before Pipeline", node)
                    _seen_execution = True
                    execution_spec = _parse_execution_call(node.value, factory_names)
                    continue

                # Pipeline(...)
                if call_name == "Pipeline":
                    if _seen_pipeline:
                        raise _error(
                            "Strategy file must contain exactly one Pipeline(...) expression, found multiple",
                            node,
                        )
                    _seen_pipeline = True
                    pipeline = _parse_pipeline_call(node.value, factory_names)
                    continue

            # Non-Pipeline/Globals/Universe/Execution expression
            raise _error(
                "Only Globals(...), Universe(...), Execution(...), and Pipeline(...) calls "
                "allowed as expression statements",
                node,
            )

        # Reject everything else
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise _error("Imports are not allowed in strategy files", node)

        if isinstance(node, ast.If):
            raise _error("Control flow (if/else) is not allowed", node)

        if isinstance(node, ast.For):
            raise _error("Control flow (for loops) is not allowed", node)

        if isinstance(node, ast.While):
            raise _error("Control flow (while loops) is not allowed", node)

        if isinstance(node, ast.With):
            raise _error("Context managers (with) are not allowed", node)

        if isinstance(node, ast.AsyncFunctionDef):
            raise _error("Async functions are not allowed", node)

        if isinstance(node, ast.ClassDef):
            raise _error("Class definitions are not allowed", node)

        if isinstance(node, (ast.Try,)):
            raise _error("Try/except blocks are not allowed", node)

        raise _error(f"Unsupported statement: {type(node).__name__}", node)

    if pipeline is None:
        raise DSLParseError("Strategy file must contain a Pipeline(...) expression")

    return StrategyFile(
        metadata=metadata,
        factories=factories,
        variables=variables,
        pipeline=pipeline,
        globals_=globals_spec,
        universe=universe_spec,
        execution=execution_spec,
    )


__all__ = [
    "DSLParseError",
    "parse_strategy",
]
