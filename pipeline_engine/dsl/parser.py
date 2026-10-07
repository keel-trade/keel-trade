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
    """Error raised when the DSL parser encounters invalid syntax.

    ``end_line``/``end_col`` complete the position into a span (Q-1697) —
    the parse tier is the one tier with no issue list to carry a span on, so
    the exception carries it and every consumer that renders a parse failure
    can show the offending source with a caret.

    ``code`` and ``suggestion`` complete it into a structured issue (Q-1695).
    The parse tier is where migration from another platform LANDS, and until
    2026-09-22 it was the one tier with no code, no suggestion and a
    different shape on every surface — a bare 400 string from ``/parse``, a
    `PARSE_ERROR` envelope from ``/validate``, a code-less dict from the SDK,
    a `parse_error` string beside an empty issue list from the chat tools.
    ``code`` is a catalog code, so `keel_help rule:<CODE>` can answer it and
    both halves of an agent's repair loop see the same vocabulary.

    ``str(e)`` is UNCHANGED by both additions, so every existing ``str(exc)``
    consumer keeps its text byte for byte.
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        col: int | None = None,
        end_line: int | None = None,
        end_col: int | None = None,
        code: str = "PARSE_ERROR",
        suggestion: str | None = None,
    ):
        self.line = line
        self.col = col
        self.end_line = end_line
        self.end_col = end_col
        self.code = code
        self.suggestion = suggestion
        if line is not None:
            prefix = f"Parse error at line {line}"
            if col is not None:
                prefix += f", col {col}"
            super().__init__(f"{prefix}: {message}")
        else:
            super().__init__(f"Parse error: {message}")

    def to_issue(self, tier: str = "static") -> Any:
        """This failure as ONE issue in the validator's 16-field envelope.

        The single owner of what a parse failure looks like on the wire
        (Q-1695): keel-api's `/validate`, `/parse`, compile input and backtest
        gate, and the chat tools all render it through here, so the code,
        the fix and the span cannot take a different shape per surface
        again. Returns a ``ValidationIssue`` (imported lazily — the parser
        stays free of the registry-backed module).
        """
        from pipeline_engine.validation_shared import Span, ValidationIssue

        span = None
        location = "source"
        if self.line is not None:
            col = self.col if self.col is not None else 0
            span = Span(
                line=self.line,
                col=col,
                end_line=self.end_line if self.end_line is not None else self.line,
                end_col=self.end_col if self.end_col is not None else col,
            )
            location = f"line {self.line}" + (f", col {self.col}" if self.col is not None else "")
        return ValidationIssue(
            severity="error",
            code=self.code,
            message=str(self),
            location=location,
            suggestion=self.suggestion,
            tier=tier,  # type: ignore[arg-type]
            span=span,
        )


#: The source text of the file currently being parsed, set by
#: :func:`parse_strategy` for the duration of one parse. ``ast`` reports
#: column offsets in UTF-8 BYTES; every consumer of a span slices the source
#: TEXT with it, so ``_loc`` converts — which needs the line it is on.
#: ``None`` (a caller reaching the node helpers without a parse in flight)
#: degrades to the raw byte offset, exactly today's behavior.
_SOURCE_LINES: list[str] | None = None


def _char_col(lineno: int, byte_col: int) -> int:
    """Convert an ``ast`` UTF-8 byte column to a character column.

    The same correction ``dsl/edits.py``'s ``_Offsets.pos`` makes for its
    offset-based spans; kept here rather than shared because that module
    builds an index over the whole source for surgical edits, while this
    needs one line at a time during the parse.
    """
    if byte_col <= 0 or _SOURCE_LINES is None or not (1 <= lineno <= len(_SOURCE_LINES)):
        return byte_col
    line = _SOURCE_LINES[lineno - 1]
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def _loc(node: ast.AST, context: str = "") -> SourceLocation:
    """Create a SourceLocation from an AST node.

    Carries the node's END position too (Q-1697): ``ValidationIssue.span``
    has existed since the M3a envelope and no site ever populated it, so
    every agent counting steps from ``step[7]`` had no line to look at.
    """
    line = getattr(node, "lineno", 0)
    end_line = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    return SourceLocation(
        line=line,
        col=_char_col(line, getattr(node, "col_offset", 0)),
        context=context,
        end_line=end_line,
        end_col=None if end_col is None or end_line is None else _char_col(end_line, end_col),
    )


# ═══════════════════════════════════════════════════════════════════════════
# PARSE-TIER FIX PROSE (Q-1695 — mcp-strategy-view W4 §2)
#
# One string per CODE, because the fix is a property of the family and not of
# the sixty individual messages. Every one of them says what to DO; none
# restates what went wrong (the message already did).
#
# Two carry a MIGRATION rather than a grammar rule, and they are the reason
# this mint exists: the parse tier is where migration from another platform
# lands, and until now it answered `Strategy(name=..., asset_class=...,
# max_assets=..., pipeline=[...])` with "Only Globals(...), Universe(...),
# Execution(...), and Pipeline(...) calls allowed as expression statements" —
# true, and useless to someone holding the old form.
# ═══════════════════════════════════════════════════════════════════════════

#: The four-declaration skeleton, verbatim from the compose description
#: (decision #23). ONE copy; the legacy-form fix renders it.


#: `Universe(...)`/`Globals(...)` keys the pre-2026 form used, and what each
#: became. Rendered by `_declaration_key_fix` when the unknown key hits it —
#: a migration when we know one, the Available list otherwise.
_DECLARATION_KEY_MIGRATION = {
    "asset_class": 'Universe(market="perp") — the venue market, not an asset class',
    "max_assets": 'Universe(mode="top_volume", top_n=N)',
    "assets": "Universe(symbols=[...])",
    "tickers": "Universe(symbols=[...])",
    "timeframe": "Globals(target_timeframe=...)",
    "pipeline": "Pipeline([...]) as its own top-level call",
}


def _declaration_key_fix(key: str, valid: Any = ()) -> str:
    """The fix for an unknown declaration key — a MIGRATION when we know one,
    else the valid keys it is closest to.

    `asset_class` and `max_assets` are the two that actually arrive (the
    pre-2026 `Strategy(...)` form carried them), and they were named as
    "removed" in one skill file and nowhere the parser could reach. The
    near-miss arm is the R3 agent's `Execution(buffer=0.2)` (the card said
    "Buffered 0.2"): "Check the Available list" made it re-read a list to
    find `buffer_threshold`, which the parser can name directly.
    """
    moved = _DECLARATION_KEY_MIGRATION.get(key)
    if moved:
        return f"'{key}' moved: use {moved}."
    close = _closest_keys(key, valid)
    if close:
        return "Did you mean " + " or ".join(f"'{k}'" for k in close) + "?"
    return "Check the Available list in the message for the declaration's keys."


def _closest_keys(key: str, valid: Any, limit: int = 2) -> list[str]:
    """Valid keys that EXTEND the typo first (`buffer` → `buffer_threshold`,
    `buffer_mode`), then difflib's near misses; at most ``limit``."""
    import difflib

    keys = sorted(str(k) for k in valid)
    lowered = key.lower()
    out = [k for k in keys if k.lower().startswith(lowered) and k.lower() != lowered]
    for k in difflib.get_close_matches(key, keys, n=limit, cutoff=0.6):
        if k not in out:
            out.append(k)
    return out[:limit]


def _check_declaration_keys(decl: str, label: str, node: ast.Call, valid: Any) -> None:
    """Every unknown key of ONE declaration, raised together (Q-1841).

    One unknown key keeps its message byte-for-byte; several are named in
    one error, each with its own fix, so `Universe(asset_class=…,
    max_assets=…)` is one round trip instead of two.
    """
    unknown = [kw.arg for kw in node.keywords if kw.arg is not None and kw.arg not in valid]
    if not unknown:
        return
    available = f"Available: {sorted(valid)}"
    if len(unknown) == 1:
        message = f"Unknown {label} '{unknown[0]}'. {available}"
    else:
        names = ", ".join(f"'{k}'" for k in unknown)
        message = f"Unknown {decl} parameters {names}. {available}"
    fixes = [_declaration_key_fix(k, valid) for k in unknown]
    raise _error("UNKNOWN_DECLARATION_KEY", message, node, suggestion=" ".join(fixes))


def _catalog_fix(code: str) -> str | None:
    """The catalog's fix prose for a parse code, rendered.

    ONE owner (Q-1695). The template is rendered with ``str.format`` exactly
    as ``emit()`` renders every other rule's, so a literal brace in the prose
    is written ``{{`` there and reads as ``{`` here — the same convention on
    both tiers rather than two. A code with no declared fix (SYNTAX_ERROR)
    returns None, which is the honest answer: CPython's own diagnosis is the
    whole of what can be said.
    """
    from pipeline_engine.dsl.catalog import RULES

    rule = RULES.get(code)
    if rule is None or not rule.suggestion_template:
        return None
    return rule.suggestion_template.format()


def _error(
    code: str,
    message: str,
    node: ast.AST | None = None,
    *,
    suggestion: str | None = None,
) -> DSLParseError:
    """Create a DSLParseError with a CODE, a location/span, and a fix.

    ``code`` is first and required (Q-1695): the whole point is that no parse
    failure can be raised without one, and a positional default would let the
    next site quietly skip it. Every code is a catalog entry — `catalog_test`
    scans this file the way it scans the validator files, so a new family
    cannot ship uncatalogued.

    ``suggestion`` defaults to the CATALOG's fix prose for the code, so
    ``keel_help rule:<CODE>`` and the raised suggestion are the same string
    by construction — there is no second copy to drift. Pass it explicitly
    only where the fix is site-computed (the per-key declaration migration).
    """
    if suggestion is None:
        suggestion = _catalog_fix(code)
    if node is not None:
        line = getattr(node, "lineno", None)
        end_line = getattr(node, "end_lineno", None)
        end_col = getattr(node, "end_col_offset", None)
        col = getattr(node, "col_offset", None)
        return DSLParseError(
            message,
            line=line,
            col=col if line is None or col is None else _char_col(line, col),
            end_line=end_line,
            end_col=None if end_line is None or end_col is None else _char_col(end_line, end_col),
            code=code,
            suggestion=suggestion,
        )
    return DSLParseError(message, code=code, suggestion=suggestion)


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
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "Unary minus only allowed on numeric literals",
            node,
        )

    if isinstance(node, ast.List):
        return [_parse_param_value(elt, factory_names) for elt in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_parse_param_value(elt, factory_names) for elt in node.elts)

    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                raise _error(
                    "PARAM_EXPRESSION_NOT_ALLOWED",
                    "Dict unpacking (**) not allowed in parameters",
                    node,
                )
            k = _parse_param_value(key, factory_names)
            v = _parse_param_value(value, factory_names)
            result[k] = v
        return result

    if isinstance(node, ast.Set):
        return {_parse_param_value(elt, factory_names) for elt in node.elts}

    # Reject everything else
    if isinstance(node, ast.BinOp):
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "Computed expressions not allowed in parameters",
            node,
        )
    if isinstance(node, ast.JoinedStr):
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "f-strings not allowed",
            node,
        )
    if isinstance(node, ast.Attribute):
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "Attribute access not allowed",
            node,
        )
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "Comprehensions not allowed",
            node,
        )
    if isinstance(node, ast.Call):
        raise _error(
            "PARAM_EXPRESSION_NOT_ALLOWED",
            "Function calls not allowed in parameter values",
            node,
        )

    raise _error(
        "PARAM_EXPRESSION_NOT_ALLOWED",
        f"Unsupported expression type: {type(node).__name__}",
        node,
    )


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
            raise _error(
                "PARAM_EXPRESSION_NOT_ALLOWED",
                f"**kwargs not allowed in {context_name}",
                node,
            )
        params[kw.arg] = _parse_param_value(kw.value, factory_names)
    return params


def _parse_step(node: ast.expr, factory_names: set[str], context: str = "") -> StepSpec:
    """Parse a single step expression from within a Pipeline step list."""
    # Call expression: Pipeline, Store, Load, factory call, or component
    if isinstance(node, ast.Call):
        name = _get_call_name(node)

        if name is None:
            if isinstance(node.func, ast.Attribute):
                raise _error(
                    "STEP_NOT_A_CALL",
                    "Attribute access not allowed",
                    node,
                )
            raise _error(
                "STEP_NOT_A_CALL",
                "Only simple function calls allowed (no method calls)",
                node,
            )

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

        # Parallel(...) is the Python pipeline API's class, not DSL. Without
        # this arm the call failed on its FIRST BRANCH's first component —
        # "Function calls not allowed in parameter values", pointing inside
        # a branch at a perfectly valid `ROC(period=5)` — and sent the R3
        # agent looking for what was wrong with ROC (Q-1841, Q-1695).
        if name == "Parallel":
            raise _error(
                "PARALLEL_BRANCH_SHAPE",
                "Parallel(...) is not a DSL call",
                node,
                suggestion=(
                    "Parallel branches are a dict literal in the step list: "
                    '{"fast": [...], "slow": [...]} — there is no Parallel(...) call.'
                ),
            )

        # Factory call -> FactoryCallSpec
        if name in factory_names:
            if node.args:
                raise _error(
                    "POSITIONAL_ARGS_NOT_ALLOWED",
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
                "POSITIONAL_ARGS_NOT_ALLOWED",
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
            "STEP_NOT_A_CALL",
            "Bare list not allowed as a step. Use Pipeline([...]) for sub-pipelines",
            node,
        )

    # Reject everything else at step level
    if isinstance(node, ast.Constant):
        raise _error(
            "STEP_NOT_A_CALL",
            "Literal values not allowed as pipeline steps",
            node,
        )
    if isinstance(node, ast.BinOp):
        raise _error(
            "STEP_NOT_A_CALL",
            "Computed expressions not allowed",
            node,
        )
    if isinstance(node, ast.JoinedStr):
        raise _error("STEP_NOT_A_CALL", "f-strings not allowed", node)
    if isinstance(node, ast.Attribute):
        raise _error("STEP_NOT_A_CALL", "Attribute access not allowed", node)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise _error("STEP_NOT_A_CALL", "Comprehensions not allowed", node)

    raise _error(
        "STEP_NOT_A_CALL",
        f"Unsupported step expression: {type(node).__name__}",
        node,
    )


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
        raise _error(
            "PIPELINE_ARG_SHAPE",
            f"Expected Pipeline call, got {name}",
            node,
        )

    # Extract step list (first positional arg must be a list)
    if not node.args:
        raise _error(
            "PIPELINE_ARG_SHAPE",
            "Pipeline() requires a step list: Pipeline([step1, step2, ...])",
            node,
        )

    if len(node.args) > 1:
        raise _error(
            "PIPELINE_ARG_SHAPE",
            "Pipeline() takes exactly one positional argument (the step list)",
            node,
        )

    step_list_node = node.args[0]
    if not isinstance(step_list_node, ast.List):
        raise _error(
            "PIPELINE_ARG_SHAPE",
            "Pipeline() argument must be a list: Pipeline([...])",
            node,
        )

    steps = _parse_step_list(step_list_node.elts, factory_names, context)

    # Extract name keyword arg
    pipeline_name: str | None = None
    for kw in node.keywords:
        if kw.arg == "name":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                pipeline_name = kw.value.value
            else:
                raise _error(
                    "PIPELINE_ARG_SHAPE",
                    "Pipeline name must be a string literal",
                    kw.value,
                )
        elif kw.arg in ("mode", "phase_order"):
            raise _error(
                "PIPELINE_ARG_SHAPE",
                f"Pipeline '{kw.arg}' is an execution-time setting, not a DSL setting. "
                f"Configure it via keel backtest/deploy.",
                node,
            )
        else:
            raise _error(
                "PIPELINE_ARG_SHAPE",
                f"Unknown Pipeline keyword argument: {kw.arg}",
                node,
            )

    return PipelineSpec(
        steps=steps,
        name=pipeline_name,
        location=_loc(node, context or "pipeline"),
    )


def _parse_store(node: ast.Call) -> SlotStoreSpec:
    """Parse Store("slot_name") into SlotStoreSpec."""
    if node.keywords:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "Store takes a single positional string argument, not keyword args",
            node,
        )
    if len(node.args) != 1:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            'Store takes exactly one argument: Store("slot_name")',
            node,
        )
    arg = node.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "Store argument must be a string literal",
            node,
        )
    return SlotStoreSpec(
        slot_name=arg.value,
        location=_loc(node, f"store[{arg.value}]"),
    )


def _parse_load(node: ast.Call) -> SlotLoadSpec:
    """Parse Load("slot_name") into SlotLoadSpec."""
    if node.keywords:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "Load takes a single positional string argument, not keyword args",
            node,
        )
    if len(node.args) != 1:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            'Load takes exactly one argument: Load("slot_name")',
            node,
        )
    arg = node.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "Load argument must be a string literal",
            node,
        )
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
            raise _error(
                "SLOT_OP_ARG_SHAPE",
                "Extract argument must be a string literal",
                node,
            )
        return SlotExtractSpec(
            key=arg.value,
            location=_loc(node, f"extract[{arg.value}]"),
        )
    if not node.args and len(node.keywords) == 1 and node.keywords[0].arg == "key":
        kw = node.keywords[0]
        if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, str):
            raise _error(
                "SLOT_OP_ARG_SHAPE",
                "Extract key must be a string literal",
                node,
            )
        return SlotExtractSpec(
            key=kw.value.value,
            location=_loc(node, f"extract[{kw.value.value}]"),
        )
    raise _error(
        "SLOT_OP_ARG_SHAPE",
        'Extract takes one argument: Extract("key") or Extract(key="key")',
        node,
    )


def _parse_store_value(node: ast.Call) -> SlotStoreValueSpec:
    """Parse StoreValue("slot_name", value) into SlotStoreValueSpec."""
    if node.keywords:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "StoreValue takes two positional arguments, not keyword args: "
            'StoreValue("slot_name", value)',
            node,
        )
    if len(node.args) != 2:
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            'StoreValue takes exactly two arguments: StoreValue("slot_name", value)',
            node,
        )
    slot_arg = node.args[0]
    if not isinstance(slot_arg, ast.Constant) or not isinstance(slot_arg.value, str):
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "StoreValue first argument must be a string literal (slot name)",
            node,
        )
    value_arg = node.args[1]
    if not isinstance(value_arg, ast.Constant):
        raise _error(
            "SLOT_OP_ARG_SHAPE",
            "StoreValue second argument must be a literal value",
            node,
        )
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
            raise _error(
                "PARALLEL_BRANCH_SHAPE",
                "Dict unpacking (**) not allowed in parallel spec",
                node,
            )
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise _error(
                "PARALLEL_BRANCH_SHAPE",
                "Parallel branch names must be string literals",
                key if key else node,
            )

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
        raise _error(
            "FACTORY_DEF_SHAPE",
            f"*args not allowed in factory '{name}'",
            node,
        )
    if node.args.kwarg:
        raise _error(
            "FACTORY_DEF_SHAPE",
            f"**kwargs not allowed in factory '{name}'",
            node,
        )

    # Validate: no decorators
    if node.decorator_list:
        raise _error(
            "FACTORY_DEF_SHAPE",
            f"Decorators not allowed on factory '{name}'",
            node,
        )

    # Validate: body must be single return statement
    if len(node.body) != 1:
        raise _error(
            "FACTORY_DEF_SHAPE",
            f"Factory body must be a single return Pipeline([...]) statement, "
            f"got {len(node.body)} statements",
            node,
        )

    stmt = node.body[0]
    if not isinstance(stmt, ast.Return):
        raise _error(
            "FACTORY_DEF_SHAPE",
            "Factory body must be a single return Pipeline([...]) statement",
            stmt,
        )

    if stmt.value is None:
        raise _error(
            "FACTORY_DEF_SHAPE",
            "Factory must return Pipeline([...])",
            stmt,
        )

    # The return value must be a Pipeline(...) call
    if not isinstance(stmt.value, ast.Call) or _get_call_name(stmt.value) != "Pipeline":
        raise _error(
            "FACTORY_DEF_SHAPE",
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
        raise _error(
            "VARIABLE_ASSIGN_SHAPE",
            "Multiple assignment targets not allowed",
            node,
        )

    target = node.targets[0]
    if not isinstance(target, ast.Name):
        raise _error(
            "VARIABLE_ASSIGN_SHAPE",
            "Assignment target must be a simple variable name",
            target,
        )

    var_name = target.id
    value_node = node.value

    # Pipeline call -> PipelineSpec
    if isinstance(value_node, ast.Call) and _get_call_name(value_node) == "Pipeline":
        value = _parse_pipeline_call(value_node, factory_names, f"var[{var_name}]")
    elif isinstance(value_node, ast.Call):
        # Component call as variable -> error (T2.9)
        call_name = _get_call_name(value_node) or "unknown"
        raise _error(
            "VARIABLE_ASSIGN_SHAPE",
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
            raise _error(
                "VARIABLE_ASSIGN_SHAPE",
                "Computed expressions not allowed in variable assignments",
                value_node,
            )
    elif isinstance(value_node, ast.List):
        value = [_parse_param_value(elt, factory_names) for elt in value_node.elts]
    elif isinstance(value_node, ast.Dict):
        value = {}
        for key, val in zip(value_node.keys, value_node.values):
            if key is None:
                raise _error(
                    "VARIABLE_ASSIGN_SHAPE",
                    "Dict unpacking not allowed",
                    value_node,
                )
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
            "VARIABLE_ASSIGN_SHAPE",
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
    "min_trailing_dollar_volume",
    # Deprecated alias of min_trailing_dollar_volume (DV6b): accepted, kept
    # as written; the validator warns (DEPRECATED_UNIVERSE_FIELD).
    "min_trailing_notional_proxy",
    "resolved",
    "resolved_at",
    "groups",
    "max_leverages",
}


def _parse_globals_call(node: ast.Call) -> GlobalsSpec:
    """Parse Globals(...) into GlobalsSpec."""
    if node.args:
        raise _error(
            "DECLARATION_ARG_SHAPE",
            "Globals() takes only keyword arguments",
            node,
        )

    _check_declaration_keys("Globals", "global", node, _VALID_GLOBALS_KEYS)
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error(
                "DECLARATION_ARG_SHAPE",
                "**kwargs not allowed in Globals()",
                node,
            )
        if not isinstance(kw.value, ast.Constant):
            raise _error(
                "DECLARATION_ARG_SHAPE",
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
        raise _error(
            "DECLARATION_ARG_SHAPE",
            "Execution() takes only keyword arguments",
            node,
        )

    _check_declaration_keys("Execution", "Execution parameter", node, _VALID_EXECUTION_KEYS)
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error(
                "DECLARATION_ARG_SHAPE",
                "**kwargs not allowed in Execution()",
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
    # Record which params the author actually wrote. Back-filled defaults are
    # indistinguishable from user input by value alone; the emit policy
    # (spec.execution_params_to_emit) keeps exactly the explicit set, so
    # parse→emit preserves what the user typed — nothing more, nothing less.
    spec_kwargs["explicit"] = frozenset(kwargs)
    return ExecutionSpec(**spec_kwargs)


def _parse_universe_call(node: ast.Call, factory_names: set[str]) -> UniverseSpec:
    """Parse Universe(...) into UniverseSpec."""
    if node.args:
        raise _error(
            "DECLARATION_ARG_SHAPE",
            "Universe() takes only keyword arguments",
            node,
        )

    _check_declaration_keys("Universe", "Universe parameter", node, _VALID_UNIVERSE_KEYS)
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise _error(
                "DECLARATION_ARG_SHAPE",
                "**kwargs not allowed in Universe()",
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
        min_trailing_dollar_volume=kwargs.get("min_trailing_dollar_volume"),
        min_trailing_notional_proxy=kwargs.get("min_trailing_notional_proxy"),
        resolved=kwargs.get("resolved"),
        resolved_at=kwargs.get("resolved_at"),
        groups=kwargs.get("groups"),
        max_leverages=kwargs.get("max_leverages"),
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
    global _SOURCE_LINES
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise DSLParseError(
            f"Invalid Python syntax: {e.msg}",
            line=e.lineno,
            col=e.offset,
            code="SYNTAX_ERROR",
        )

    # Byte→char column correction for every SourceLocation minted below
    # (Q-1697). Module state rather than a threaded argument because `_loc`
    # is called from ~60 node helpers; restored in the `finally` so a nested
    # or concurrent parse can never read another parse's text.
    _prev_source_lines = _SOURCE_LINES
    _SOURCE_LINES = source.splitlines()
    try:
        return _parse_body(source, tree)
    finally:
        _SOURCE_LINES = _prev_source_lines


def _parse_body(source: str, tree: ast.Module) -> StrategyFile:
    """The parse proper (split out so `parse_strategy` owns the source scope)."""
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
                        raise _error(
                            "DECLARATION_DUPLICATE",
                            "Only one Globals declaration allowed",
                            node,
                        )
                    if _seen_universe:
                        raise _error(
                            "DECLARATION_ORDER",
                            "Globals must appear before Universe",
                            node,
                        )
                    if _seen_pipeline:
                        raise _error(
                            "DECLARATION_ORDER",
                            "Globals must appear before Pipeline",
                            node,
                        )
                    _seen_globals = True
                    globals_spec = _parse_globals_call(node.value)
                    continue

                # Universe(...)
                if call_name == "Universe":
                    if _seen_universe:
                        raise _error(
                            "DECLARATION_DUPLICATE",
                            "Only one Universe declaration allowed",
                            node,
                        )
                    if _seen_pipeline:
                        raise _error(
                            "DECLARATION_ORDER",
                            "Universe must appear before Pipeline",
                            node,
                        )
                    _seen_universe = True
                    universe_spec = _parse_universe_call(node.value, factory_names)
                    continue

                # Execution(...)
                if call_name == "Execution":
                    if _seen_execution:
                        raise _error(
                            "DECLARATION_DUPLICATE",
                            "Only one Execution declaration allowed",
                            node,
                        )
                    if _seen_var_or_factory:
                        raise _error(
                            "DECLARATION_ORDER",
                            "Execution must appear before factories and variables",
                            node,
                        )
                    if _seen_pipeline:
                        raise _error(
                            "DECLARATION_ORDER",
                            "Execution must appear before Pipeline",
                            node,
                        )
                    _seen_execution = True
                    execution_spec = _parse_execution_call(node.value, factory_names)
                    continue

                # Pipeline(...)
                if call_name == "Pipeline":
                    if _seen_pipeline:
                        raise _error(
                            "DECLARATION_DUPLICATE",
                            "Strategy file must contain exactly one Pipeline(...) expression, found multiple",
                            node,
                        )
                    _seen_pipeline = True
                    pipeline = _parse_pipeline_call(node.value, factory_names)
                    continue

                # The legacy pre-2026 form (Q-1695). This is the ONE new parser
                # BRANCH in the parse-code mint, and it earns it: migration from
                # another platform and from Keel's own history both land here,
                # and `Strategy(...)` used to fall into the generic
                # expression-statement message, which tells the author the
                # grammar rule and not the translation. `asset_class` /
                # `max_assets` were named as "removed" in exactly one skill file
                # and nowhere the parser could reach.
                if call_name == "Strategy":
                    raise _error(
                        "LEGACY_STRATEGY_CALL",
                        "Strategy(...) is the pre-2026 form; a strategy file declares "
                        "Globals(...), Universe(...), Execution(...) and one Pipeline(...)",
                        node,
                    )

            # Non-Pipeline/Globals/Universe/Execution expression
            raise _error(
                "UNKNOWN_TOP_LEVEL_STATEMENT",
                "Only Globals(...), Universe(...), Execution(...), and Pipeline(...) calls "
                "allowed as expression statements",
                node,
            )

        # Reject everything else
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise _error(
                "IMPORT_NOT_ALLOWED",
                "Imports are not allowed in strategy files",
                node,
            )

        if isinstance(node, ast.If):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Control flow (if/else) is not allowed",
                node,
            )

        if isinstance(node, ast.For):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Control flow (for loops) is not allowed",
                node,
            )

        if isinstance(node, ast.While):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Control flow (while loops) is not allowed",
                node,
            )

        if isinstance(node, ast.With):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Context managers (with) are not allowed",
                node,
            )

        if isinstance(node, ast.AsyncFunctionDef):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Async functions are not allowed",
                node,
            )

        if isinstance(node, ast.ClassDef):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Class definitions are not allowed",
                node,
            )

        if isinstance(node, (ast.Try,)):
            raise _error(
                "CONTROL_FLOW_NOT_ALLOWED",
                "Try/except blocks are not allowed",
                node,
            )

        raise _error(
            "UNKNOWN_TOP_LEVEL_STATEMENT",
            f"Unsupported statement: {type(node).__name__}",
            node,
        )

    if pipeline is None:
        # No node: the absence has no position, which is why this raises
        # DSLParseError directly rather than through `_error`.
        raise DSLParseError(
            "Strategy file must contain a Pipeline(...) expression",
            code="MISSING_PIPELINE",
            suggestion=_catalog_fix("MISSING_PIPELINE"),
        )

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
