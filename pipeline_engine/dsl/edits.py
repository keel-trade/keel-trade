"""Surgical span edits over strategy DSL source (spec 04 stage 3, T7).

Under full-fidelity storage (spec 04, architecture (a)) the user's text is
the stored artifact; the system must never rewrite the whole file to change
one value. This module is the ONLY sanctioned way for tools to mutate DSL
*text*: every edit replaces exactly one parse-derived span and is verified
by a spec-equivalence net before it is returned.

Properties (spec 04 §5):

- **Minimality (I3):** ``apply(T, E)`` differs from ``T`` only inside the
  edited span — every byte outside it is preserved (comments, blank lines,
  parameter spelling, statement ordering).
- **Soundness (I4):** after splicing, the new text is re-parsed and compared
  (modulo source locations) against the intended spec-level mutation. On any
  mismatch the edit **raises** ``EditEquivalenceError`` — a corrupted edit is
  never returned. Callers may then *loudly* fall back to whole-file canonical
  emission (``spec_to_dsl``), which is provably semantics-equivalent.
- **Idempotence (I5):** replace-type edits are idempotent —
  ``apply(apply(T, E), E) == apply(T, E)``. (``insert_step``/``delete_step``
  are structural and inherently not idempotent.)

Span index: ``parser.py``'s ``SourceLocation`` is start-only; this module
re-walks the source with Python ``ast`` (same statement-shape logic as the
parser) and derives end positions from ``end_lineno``/``end_col_offset``.
Paths use the parser's location-context grammar: ``"step[0]"``,
``"step[2].branch[entries].step[1]"``, ``"factory[name].step[0]"``,
``"var[name].step[0]"``; declarations are ``"globals"``, ``"universe"``,
``"execution"``, ``"pipeline"``.

Replacement text comes from the existing emitter (``_emit_value`` /
``_emit_step_expr`` / ``spec_to_dsl``), so Option-C float policy applies to
everything the system writes.

Quick Start:
    >>> from pipeline_engine.dsl.edits import replace_param_value
    >>> src = 'Pipeline([\\n    ROC(period=14),  # momentum\\n])\\n'
    >>> replace_param_value(src, "step[0]", "period", 7)
    'Pipeline([\\n    ROC(period=7),  # momentum\\n])\\n'
"""

from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

from pipeline_engine.constants import MissingType
from pipeline_engine.dsl.parser import DSLParseError, parse_strategy
from pipeline_engine.dsl.spec import (
    ExecutionSpec,
    GlobalsSpec,
    ParallelSpec,
    PipelineSpec,
    SourceLocation,
    StrategyFile,
    UniverseSpec,
)


class EditError(Exception):
    """Base error for the span-edit layer."""


class SpanNotFoundError(EditError):
    """The requested declaration / step / argument has no span in the source."""


class EditEquivalenceError(EditError):
    """The spliced text failed the spec-equivalence net.

    Raised when ``parse(apply(T, E))`` does not equal ``mutate(parse(T), E)``
    (modulo source locations). The edit is never returned; callers may fall
    back — loudly — to whole-file canonical emission.
    """


# ---------------------------------------------------------------------------
# spans + offsets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """Half-open character range ``[start, end)`` into the source string."""

    start: int
    end: int


class _Offsets:
    """Convert ``ast`` (lineno, col_offset) positions to char offsets.

    ``ast`` column offsets are UTF-8 *byte* offsets within the line; this
    converts them to character offsets into the source string.
    """

    def __init__(self, source: str):
        self._lines = source.splitlines(keepends=True)
        self._starts: list[int] = []
        pos = 0
        for line in self._lines:
            self._starts.append(pos)
            pos += len(line)
        self._total = pos

    def pos(self, lineno: int, col_offset: int) -> int:
        line = self._lines[lineno - 1]
        if col_offset == 0:
            col_chars = 0
        else:
            col_chars = len(line.encode("utf-8")[:col_offset].decode("utf-8"))
        return self._starts[lineno - 1] + col_chars

    def span(self, node: ast.AST) -> Span:
        return Span(
            start=self.pos(node.lineno, node.col_offset),
            end=self.pos(node.end_lineno, node.end_col_offset),
        )


# ---------------------------------------------------------------------------
# span index
# ---------------------------------------------------------------------------


@dataclass
class CallSpans:
    """Spans for a declaration call's keyword arguments."""

    span: Span
    args: dict[str, Span]
    insert_at: int  # offset where an appended ``, arg=value`` goes
    has_args: bool


@dataclass
class StepSpans:
    """Spans for one pipeline step."""

    span: Span
    name: str | None  # call name for component/factory/builtin calls
    args: dict[str, Span]  # kwarg name -> VALUE expression span
    arg_order: list[str]


@dataclass
class ListSpans:
    """Spans for a step list (a ``[...]`` inside Pipeline or a branch)."""

    list_span: Span  # includes the brackets
    elements: list[Span]


@dataclass
class SpanIndex:
    """Parse-derived ``{spec_path -> span}`` index over one source string."""

    source: str
    file: StrategyFile
    decls: dict[str, Span] = field(default_factory=dict)
    decl_calls: dict[str, CallSpans] = field(default_factory=dict)
    steps: dict[str, StepSpans] = field(default_factory=dict)
    containers: dict[str, ListSpans] = field(default_factory=dict)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _call_spans(node: ast.Call, off: _Offsets, source: str) -> CallSpans:
    args: dict[str, Span] = {}
    for kw in node.keywords:
        if kw.arg is not None:
            args[kw.arg] = off.span(kw.value)
    if node.keywords:
        insert_at = off.span(node.keywords[-1].value).end
        has_args = True
    else:
        func_end = off.span(node.func).end
        insert_at = source.index("(", func_end) + 1
        has_args = False
    return CallSpans(span=off.span(node), args=args, insert_at=insert_at, has_args=has_args)


def build_span_index(source: str) -> SpanIndex:
    """Build the span index for a parse-accepted source.

    Raises DSLParseError if the source does not parse (the gate is the same
    as every other write path: only parse-accepted text is editable).
    """
    file = parse_strategy(source)
    tree = ast.parse(source)
    off = _Offsets(source)
    idx = SpanIndex(source=source, file=file)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            path = f"factory[{node.name}]"
            idx.decls[path] = Span(off.pos(node.lineno, node.col_offset), off.span(node).end)
            ret = node.body[0]
            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Call):
                _walk_pipeline_call(ret.value, path, idx, off, source)
        elif isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name):
                path = f"var[{target.id}]"
                idx.decls[path] = off.span(node)
                if isinstance(node.value, ast.Call) and _call_name(node.value) == "Pipeline":
                    _walk_pipeline_call(node.value, path, idx, off, source)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            cname = _call_name(node.value)
            if cname in ("Globals", "Universe", "Execution"):
                key = cname.lower()
                idx.decls[key] = off.span(node.value)
                idx.decl_calls[key] = _call_spans(node.value, off, source)
            elif cname == "Pipeline":
                idx.decls["pipeline"] = off.span(node.value)
                _walk_pipeline_call(node.value, "", idx, off, source)
    return idx


def _walk_pipeline_call(
    call: ast.Call, ctx: str, idx: SpanIndex, off: _Offsets, source: str
) -> None:
    if not call.args or not isinstance(call.args[0], ast.List):
        return
    lst = call.args[0]
    idx.containers[ctx] = ListSpans(
        list_span=off.span(lst), elements=[off.span(e) for e in lst.elts]
    )
    for i, elt in enumerate(lst.elts):
        spath = f"{ctx}.step[{i}]" if ctx else f"step[{i}]"
        _walk_step(elt, spath, idx, off, source)


def _walk_step(elt: ast.expr, path: str, idx: SpanIndex, off: _Offsets, source: str) -> None:
    if isinstance(elt, ast.Call):
        name = _call_name(elt)
        info = StepSpans(span=off.span(elt), name=name, args={}, arg_order=[])
        for kw in elt.keywords:
            if kw.arg is not None:
                info.args[kw.arg] = off.span(kw.value)
                info.arg_order.append(kw.arg)
        idx.steps[path] = info
        if name == "Pipeline":
            _walk_pipeline_call(elt, path, idx, off, source)
        return
    if isinstance(elt, ast.Dict):
        idx.steps[path] = StepSpans(span=off.span(elt), name=None, args={}, arg_order=[])
        for k, v in zip(elt.keys, elt.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            bpath = f"{path}.branch[{k.value}]"
            if isinstance(v, ast.List):
                idx.containers[bpath] = ListSpans(
                    list_span=off.span(v), elements=[off.span(e) for e in v.elts]
                )
                for j, e in enumerate(v.elts):
                    _walk_step(e, f"{bpath}.step[{j}]", idx, off, source)
            else:
                _walk_step(v, f"{bpath}.step[0]", idx, off, source)
        return
    idx.steps[path] = StepSpans(span=off.span(elt), name=None, args={}, arg_order=[])


# ---------------------------------------------------------------------------
# spec-path navigation (over the parsed StrategyFile tree)
# ---------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"(step|branch|factory|var)\[([^\]]*)\]")


def _path_tokens(path: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(path):
        m = _PATH_TOKEN.match(path, pos)
        if m is None:
            raise SpanNotFoundError(f"malformed spec path {path!r} at offset {pos}")
        tokens.append((m.group(1), m.group(2)))
        pos = m.end()
        if pos < len(path):
            if path[pos] != ".":
                raise SpanNotFoundError(f"malformed spec path {path!r} at offset {pos}")
            pos += 1
    return tokens


def _resolve_steps_list(file: StrategyFile, container_path: str) -> list:
    """Resolve a container path to the mutable steps list in the spec tree."""
    if container_path in ("", "pipeline"):
        return file.pipeline.steps
    tokens = _path_tokens(container_path)
    cur: Any = None
    i = 0
    kind, arg = tokens[0]
    if kind == "factory":
        match = next((f for f in file.factories if f.name == arg), None)
        if match is None:
            raise SpanNotFoundError(f"no factory named {arg!r}")
        cur = match.body
        i = 1
    elif kind == "var":
        match = next((v for v in file.variables if v.name == arg), None)
        if match is None or not isinstance(match.value, PipelineSpec):
            raise SpanNotFoundError(f"no pipeline variable named {arg!r}")
        cur = match.value
        i = 1
    else:
        cur = file.pipeline
    while i < len(tokens):
        kind, arg = tokens[i]
        if kind == "step":
            steps = cur.steps if isinstance(cur, PipelineSpec) else cur
            if not isinstance(steps, list):
                raise SpanNotFoundError(f"path {container_path!r}: not a step list")
            j = int(arg)
            if j >= len(steps):
                raise SpanNotFoundError(f"path {container_path!r}: step index {j} out of range")
            cur = steps[j]
        elif kind == "branch":
            if not isinstance(cur, ParallelSpec) or arg not in cur.branches:
                raise SpanNotFoundError(f"path {container_path!r}: no branch {arg!r}")
            cur = cur.branches[arg]
        else:
            raise SpanNotFoundError(f"path {container_path!r}: {kind} only allowed first")
        i += 1
    if isinstance(cur, PipelineSpec):
        return cur.steps
    if isinstance(cur, list):
        return cur
    raise SpanNotFoundError(f"path {container_path!r} does not name a step list")


def _split_step_path(step_path: str) -> tuple[str, int]:
    """Split a step path into (container_path, index)."""
    tokens = _path_tokens(step_path)
    if not tokens or tokens[-1][0] != "step":
        raise SpanNotFoundError(f"{step_path!r} is not a step path (must end in step[i])")
    idx = int(tokens[-1][1])
    prefix_len = step_path.rfind(".step[")
    container = step_path[:prefix_len] if prefix_len >= 0 else ""
    return container, idx


def _resolve_step_node(file: StrategyFile, step_path: str) -> tuple[list, int, Any]:
    """Resolve a step path to (parent_steps_list, index, node)."""
    container, i = _split_step_path(step_path)
    steps = _resolve_steps_list(file, container)
    if i >= len(steps):
        raise SpanNotFoundError(f"{step_path!r}: step index {i} out of range")
    return steps, i, steps[i]


_DECL_ATTRS = {"globals": "globals_", "universe": "universe", "execution": "execution"}
_DECL_TYPES = {"globals": GlobalsSpec, "universe": UniverseSpec, "execution": ExecutionSpec}


# ---------------------------------------------------------------------------
# spec equivalence (the net's comparator)
# ---------------------------------------------------------------------------


def spec_equal(a: Any, b: Any) -> bool:
    """Structural spec equality, ignoring source locations.

    Numeric comparison is type-strict (``4 != 4.0``) so Option-C float
    canonicalization drift is caught, matching the parser/emitter contract.
    """
    if isinstance(a, SourceLocation) or isinstance(b, SourceLocation):
        return isinstance(a, SourceLocation) == isinstance(b, SourceLocation)
    # The MISSING sentinel ("this factory param has no default"). Compared by
    # *type*, not identity: both sides carrying a sentinel means both say "no
    # default", which is equal; a sentinel against any real default value is
    # not. ``MISSING`` is a copy-stable singleton so identity would normally
    # suffice, but this net's whole job is to be right — belt and braces, and
    # it keeps the comparator correct even if a sentinel instance ever arrives
    # by a route that bypasses the singleton (this is the exact class of bug
    # that made deepcopy'd ``FactoryParam.default`` stop being ``MISSING``).
    if isinstance(a, MissingType) or isinstance(b, MissingType):
        return isinstance(a, MissingType) == isinstance(b, MissingType)
    if is_dataclass(a) and not isinstance(a, type):
        if type(a) is not type(b):
            return False
        return all(
            spec_equal(getattr(a, f.name), getattr(b, f.name))
            for f in fields(a)
            if f.name != "location"
        )
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        if len(a) != len(b):
            return False
        return all(
            spec_equal(ka, kb) and spec_equal(va, vb)
            for (ka, va), (kb, vb) in zip(a.items(), b.items())
        )
    if isinstance(a, (list, tuple)):
        if type(a) is not type(b) or len(a) != len(b):
            return False
        return all(spec_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (set, frozenset)) and isinstance(b, (set, frozenset)):
        return a == b
    return type(a) is type(b) and a == b


def _verify(new_text: str, expected: StrategyFile, op: str) -> str:
    """The net (I4): re-parse the spliced text, require spec equivalence.

    There is no silent fallback here (per .claude/rules/lessons.md): a failed
    net RAISES. Callers that want the provably-equivalent degradation re-emit
    canonically themselves and flag it.
    """
    try:
        reparsed = parse_strategy(new_text)
    except DSLParseError as exc:
        raise EditEquivalenceError(
            f"span edit {op!r} produced text that does not parse: {exc}"
        ) from exc
    if not spec_equal(reparsed, expected):
        raise EditEquivalenceError(
            f"span edit {op!r} failed the spec-equivalence net: the edited text "
            "does not parse to the intended spec mutation"
        )
    return new_text


def _splice(source: str, span: Span, replacement: str) -> str:
    return source[: span.start] + replacement + source[span.end :]


# ---------------------------------------------------------------------------
# value / fragment rendering + parsing
# ---------------------------------------------------------------------------


def render_value(value: Any, target_type: Any = None, prefer_quote: str | None = None) -> str:
    """Render a Python value as DSL text via the canonical emitter.

    ``prefer_quote='"'`` renders strings double-quoted (matching hand-written
    sources) when safe; the default is the emitter's canonical ``repr``.
    Numeric rendering always goes through the emitter so Option-C float
    policy (declared-float params render ``N.0``) applies.
    """
    from pipeline_engine.dsl.emitter import _dict_value_type, _emit_value, _list_elem_type

    if prefer_quote is None:
        return _emit_value(value, target_type=target_type)
    if isinstance(value, str):
        if prefer_quote == '"' and '"' not in value and "\\" not in value:
            return f'"{value}"'
        return repr(value)
    if isinstance(value, dict):
        v_type = _dict_value_type(target_type)
        items = [
            f"{render_value(k, prefer_quote=prefer_quote)}: "
            f"{render_value(v, target_type=v_type, prefer_quote=prefer_quote)}"
            for k, v in value.items()
        ]
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        elem_type = _list_elem_type(target_type)
        return (
            "["
            + ", ".join(
                render_value(v, target_type=elem_type, prefer_quote=prefer_quote) for v in value
            )
            + "]"
        )
    if isinstance(value, tuple):
        inner = ", ".join(render_value(v, prefer_quote=prefer_quote) for v in value)
        if len(value) == 1:
            inner += ","
        return "(" + inner + ")"
    return _emit_value(value, target_type=target_type)


def _infer_quote(old_text: str) -> str | None:
    m = re.search(r"['\"]", old_text)
    return m.group(0) if m and m.group(0) == '"' else None


def _parse_value_fragment(text: str) -> Any:
    """Parse a rendered value fragment back to its spec-level value."""
    from pipeline_engine.dsl.parser import _parse_param_value

    try:
        expr = ast.parse(text, mode="eval").body
        return _parse_param_value(expr, set())
    except (SyntaxError, DSLParseError) as exc:
        raise EditError(f"rendered value fragment {text!r} does not parse: {exc}") from exc


def _parse_step_fragment(text: str, factory_names: set[str]) -> Any:
    """Parse a rendered step expression back to its StepSpec."""
    from pipeline_engine.dsl.parser import _parse_step

    try:
        expr = ast.parse(text, mode="eval").body
        return _parse_step(expr, factory_names, "edit")
    except (SyntaxError, DSLParseError) as exc:
        raise EditError(f"rendered step fragment does not parse: {exc}") from exc


def _emit_declaration(decl: str, spec_obj: Any) -> str:
    """Emit one declaration statement via the real whole-file emitter."""
    from pipeline_engine.dsl.emitter import spec_to_dsl

    if not isinstance(spec_obj, _DECL_TYPES[decl]):
        raise EditError(
            f"replace_declaration({decl!r}) needs a {_DECL_TYPES[decl].__name__}, "
            f"got {type(spec_obj).__name__}"
        )
    dummy = StrategyFile(
        metadata={},
        factories=[],
        variables=[],
        pipeline=PipelineSpec(steps=[], name=None, location=None),
        globals_=spec_obj if decl == "globals" else None,
        universe=spec_obj if decl == "universe" else None,
        execution=spec_obj if decl == "execution" else None,
    )
    text = spec_to_dsl(dummy)
    prefix = {"globals": "Globals(", "universe": "Universe(", "execution": "Execution("}[decl]
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    raise EditError(f"emitter produced no {decl} declaration line")  # pragma: no cover


def _parse_declaration_fragment(decl: str, decl_text: str) -> Any:
    """Parse a declaration statement back to its spec node (defaults filled)."""
    try:
        mini = parse_strategy(f"{decl_text}\n\nPipeline([])\n")
    except DSLParseError as exc:
        raise EditError(f"rendered declaration does not parse: {exc}") from exc
    return getattr(mini, _DECL_ATTRS[decl])


def _live_registry(registry: dict | None) -> dict:
    """Resolve the emitter registry, ensuring components are registered.

    Engine operations call ``ensure_registry_loaded()`` before touching
    ``COMPONENT_REGISTRY`` (registry_loader contract); without it, Option-C
    typed rendering would silently depend on what the host process happened
    to import — the environment-dependent drift B7 killed.
    """
    from pipeline_engine.dsl.emitter import _resolve_emitter_registry

    if registry is None:
        from pipeline_engine.registry_loader import ensure_registry_loaded

        ensure_registry_loaded()
    return _resolve_emitter_registry(registry)


def _emit_step_text(step: Any, col: int, registry: dict | None = None) -> str:
    """Emit a step expression for splicing at column ``col``."""
    from pipeline_engine.dsl.emitter import _emit_step_expr

    lines = _emit_step_expr(step, col, _live_registry(registry))
    lines = [lines[0].lstrip()] + lines[1:]
    return "\n".join(lines)


def _column_of(source: str, offset: int) -> int:
    line_start = source.rfind("\n", 0, offset) + 1
    return offset - line_start


# ---------------------------------------------------------------------------
# edit primitives
# ---------------------------------------------------------------------------


def replace_param_value(
    source: str,
    step_path: str,
    param: str,
    new_value: Any,
    *,
    registry: dict | None = None,
    prefer_quote: str | None = None,
    index: SpanIndex | None = None,
) -> str:
    """Replace one keyword-argument value of a component / factory call.

    Only the value's span changes; the rendered replacement goes through the
    canonical emitter with the component's declared param type (Option-C).
    ``prefer_quote`` defaults to the old value text's quoting style.
    """
    idx = index if index is not None and index.source == source else build_span_index(source)
    info = idx.steps.get(step_path)
    if info is None:
        raise SpanNotFoundError(f"no step at path {step_path!r}")
    if param not in info.args:
        raise SpanNotFoundError(f"step {step_path!r} has no keyword argument {param!r}")
    span = info.args[param]
    old_text = source[span.start : span.end]

    target_type = None
    if info.name is not None:
        sig = _live_registry(registry).get(info.name)
        pinfo = sig.parameters.get(param) if sig is not None else None
        target_type = pinfo.type_ if pinfo is not None else None

    quote = prefer_quote if prefer_quote is not None else _infer_quote(old_text)
    rendered = render_value(new_value, target_type=target_type, prefer_quote=quote)
    expected_value = _parse_value_fragment(rendered)

    expected = copy.deepcopy(idx.file)
    _steps, _i, node = _resolve_step_node(expected, step_path)
    params = getattr(node, "params", None)
    if params is None:
        params = getattr(node, "args", None)
    if not isinstance(params, dict) or param not in params:
        raise SpanNotFoundError(f"spec node at {step_path!r} has no param {param!r}")
    params[param] = expected_value

    new_text = _splice(source, span, rendered)
    return _verify(new_text, expected, f"replace_param_value({step_path}, {param})")


def replace_decl_arg(
    source: str,
    decl: str,
    arg: str,
    new_value: Any,
    *,
    prefer_quote: str | None = None,
) -> str:
    """Replace an existing Globals/Universe/Execution argument's value span."""
    return _set_decl_arg(source, decl, arg, new_value, insert=False, prefer_quote=prefer_quote)


def set_decl_arg(
    source: str,
    decl: str,
    arg: str,
    new_value: Any,
    *,
    prefer_quote: str | None = None,
) -> str:
    """Replace a declaration argument's value, inserting the arg if absent.

    Insertion appends ``, arg=value`` after the last existing argument (the
    grammar is keyword-only, so position is semantically irrelevant).
    """
    return _set_decl_arg(source, decl, arg, new_value, insert=True, prefer_quote=prefer_quote)


def _set_decl_arg(
    source: str,
    decl: str,
    arg: str,
    new_value: Any,
    *,
    insert: bool,
    prefer_quote: str | None,
) -> str:
    if decl not in _DECL_ATTRS:
        raise SpanNotFoundError(f"unknown declaration {decl!r} (globals/universe/execution)")
    idx = build_span_index(source)
    call = idx.decl_calls.get(decl)
    if call is None:
        raise SpanNotFoundError(f"source has no {decl.capitalize()}(...) declaration")

    target_type = None
    if decl == "execution":
        import typing as _typing

        target_type = _typing.get_type_hints(ExecutionSpec).get(arg)

    expected = copy.deepcopy(idx.file)
    obj = getattr(expected, _DECL_ATTRS[decl])

    if arg in call.args:
        span = call.args[arg]
        old_text = source[span.start : span.end]
        quote = prefer_quote if prefer_quote is not None else _infer_quote(old_text)
        rendered = render_value(new_value, target_type=target_type, prefer_quote=quote)
        replacement_span = span
        replacement = rendered
    elif insert:
        rendered = render_value(new_value, target_type=target_type, prefer_quote=prefer_quote)
        replacement_span = Span(call.insert_at, call.insert_at)
        replacement = f", {arg}={rendered}" if call.has_args else f"{arg}={rendered}"
    else:
        raise SpanNotFoundError(f"{decl.capitalize()}() has no argument {arg!r}")

    if not hasattr(obj, arg):
        raise SpanNotFoundError(f"{decl.capitalize()} has no parameter {arg!r}")
    setattr(obj, arg, _parse_value_fragment(rendered))
    if decl == "execution":
        obj.explicit = frozenset(obj.explicit) | {arg}

    new_text = _splice(source, replacement_span, replacement)
    return _verify(new_text, expected, f"set_decl_arg({decl}, {arg})")


def replace_declaration(source: str, decl: str, new_spec: Any) -> str:
    """Replace (or insert, when absent) a whole Globals/Universe/Execution
    declaration with the canonical emission of ``new_spec``.

    Only the declaration's statement span changes; on insertion, placement
    honors the grammar's ordering rules (Globals < Universe < Pipeline;
    Execution before factories/variables).
    """
    if decl not in _DECL_ATTRS:
        raise SpanNotFoundError(f"unknown declaration {decl!r} (globals/universe/execution)")
    idx = build_span_index(source)
    decl_text = _emit_declaration(decl, new_spec)
    parsed_back = _parse_declaration_fragment(decl, decl_text)

    expected = copy.deepcopy(idx.file)
    setattr(expected, _DECL_ATTRS[decl], parsed_back)

    existing = idx.decls.get(decl)
    if existing is not None:
        new_text = _splice(source, existing, decl_text)
    else:
        insert_at, prefix, suffix = _declaration_insert_point(idx, decl)
        new_text = _splice(source, Span(insert_at, insert_at), f"{prefix}{decl_text}{suffix}")
    return _verify(new_text, expected, f"replace_declaration({decl})")


def _declaration_insert_point(idx: SpanIndex, decl: str) -> tuple[int, str, str]:
    """Choose a grammar-valid insertion point for a missing declaration."""
    order = {"globals": 0, "universe": 1, "execution": 2}
    # Prefer inserting after the closest earlier declaration.
    for earlier in sorted(
        (d for d in ("globals", "universe") if order.get(d, 9) < order[decl]),
        key=lambda d: -order[d],
    ):
        span = idx.decls.get(earlier)
        if span is not None:
            return span.end, "\n\n", ""
    # Else before the first top-level statement (after leading comments).
    first = min(s.start for s in idx.decls.values())
    return first, "", "\n\n"


def replace_step(
    source: str,
    step_path: str,
    new_step: Any,
    *,
    registry: dict | None = None,
) -> str:
    """Replace one pipeline step with the canonical emission of ``new_step``."""
    idx = build_span_index(source)
    info = idx.steps.get(step_path)
    if info is None:
        raise SpanNotFoundError(f"no step at path {step_path!r}")
    col = _column_of(source, info.span.start)
    rendered = _emit_step_text(new_step, col, registry)
    factory_names = {f.name for f in idx.file.factories}
    parsed_back = _parse_step_fragment(rendered, factory_names)

    expected = copy.deepcopy(idx.file)
    steps, i, _node = _resolve_step_node(expected, step_path)
    steps[i] = parsed_back

    new_text = _splice(source, info.span, rendered)
    return _verify(new_text, expected, f"replace_step({step_path})")


def insert_step(
    source: str,
    container_path: str,
    index: int,
    new_step: Any,
    *,
    registry: dict | None = None,
) -> str:
    """Insert a step at ``index`` of a step list (top-level ``""``/"pipeline",
    ``factory[name]``, ``var[name]``, nested ``step[i]``, or a
    ``step[i].branch[name]`` list). Not idempotent by nature."""
    idx = build_span_index(source)
    key = "" if container_path == "pipeline" else container_path
    container = idx.containers.get(key)
    if container is None:
        raise SpanNotFoundError(f"no step list at container path {container_path!r}")
    n = len(container.elements)
    if not 0 <= index <= n:
        raise SpanNotFoundError(f"insert index {index} out of range 0..{n}")

    multiline = "\n" in source[container.list_span.start : container.list_span.end]
    if n:
        anchor_col = _column_of(source, container.elements[min(index, n - 1)].start)
    else:
        anchor_col = _column_of(source, container.list_span.start) + 4
    rendered = _emit_step_text(new_step, anchor_col, registry)
    factory_names = {f.name for f in idx.file.factories}
    parsed_back = _parse_step_fragment(rendered, factory_names)

    indent = " " * anchor_col
    if n == 0:
        at = container.list_span.start + 1
        fragment = (
            f"\n{indent}{rendered},\n{' ' * max(anchor_col - 4, 0)}" if multiline else rendered
        )
    elif index < n:
        at = container.elements[index].start
        fragment = f"{rendered},\n{indent}" if multiline else f"{rendered}, "
    else:
        last_end = container.elements[-1].end
        comma = _scan_for_comma(source, last_end, container.list_span.end - 1)
        if comma is not None:
            at = comma + 1
            fragment = f"\n{indent}{rendered}," if multiline else f" {rendered},"
        else:
            at = last_end
            fragment = f",\n{indent}{rendered}" if multiline else f", {rendered}"

    expected = copy.deepcopy(idx.file)
    steps = _resolve_steps_list(expected, key)
    steps.insert(index, parsed_back)

    new_text = _splice(source, Span(at, at), fragment)
    return _verify(new_text, expected, f"insert_step({container_path}, {index})")


def delete_step(source: str, step_path: str) -> str:
    """Delete one step (and its separating comma). Not idempotent by nature."""
    idx = build_span_index(source)
    info = idx.steps.get(step_path)
    if info is None:
        raise SpanNotFoundError(f"no step at path {step_path!r}")
    container_path, i = _split_step_path(step_path)
    container = idx.containers.get(container_path)
    if container is None:
        raise SpanNotFoundError(
            f"step {step_path!r} is not inside an editable step list "
            "(single-expression branch values cannot be deleted)"
        )
    span = container.elements[i]

    start, end = span.start, span.end
    list_end = container.list_span.end - 1  # position of ']'
    comma = _scan_for_comma(source, end, list_end)
    if comma is not None:
        end = comma + 1
    else:
        prev_floor = container.elements[i - 1].end if i > 0 else container.list_span.start + 1
        back = _scan_back_for_comma(source, start, prev_floor)
        if back is not None:
            start = back

    # Whole-line cleanup: if the deletion leaves only whitespace — or only
    # the step's own trailing same-line comment — remove the line(s)
    # entirely (a trailing comment belongs to the step it annotates).
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", end)
    line_end = len(source) if line_end == -1 else line_end + 1
    remainder = source[end : line_end - 1].strip()
    if source[line_start:start].strip() == "" and (remainder == "" or remainder.startswith("#")):
        start, end = line_start, line_end

    expected = copy.deepcopy(idx.file)
    steps = _resolve_steps_list(expected, container_path)
    del steps[i]

    new_text = _splice(source, Span(start, end), "")
    return _verify(new_text, expected, f"delete_step({step_path})")


def _scan_for_comma(source: str, start: int, stop: int) -> int | None:
    """Find the next ',' in [start, stop) skipping whitespace and comments."""
    pos = start
    while pos < stop:
        ch = source[pos]
        if ch in " \t\r\n":
            pos += 1
        elif ch == "#":
            nl = source.find("\n", pos, stop)
            if nl == -1:
                return None
            pos = nl + 1
        elif ch == ",":
            return pos
        else:
            return None
    return None


def _scan_back_for_comma(source: str, start: int, floor: int) -> int | None:
    """Find a ',' directly before ``start`` (whitespace-separated only)."""
    pos = start - 1
    while pos >= floor:
        ch = source[pos]
        if ch in " \t\r\n":
            pos -= 1
        elif ch == ",":
            return pos
        else:
            return None
    return None


__all__ = [
    "EditEquivalenceError",
    "EditError",
    "Span",
    "SpanIndex",
    "SpanNotFoundError",
    "build_span_index",
    "delete_step",
    "insert_step",
    "render_value",
    "replace_decl_arg",
    "replace_declaration",
    "replace_param_value",
    "replace_step",
    "set_decl_arg",
    "spec_equal",
]
