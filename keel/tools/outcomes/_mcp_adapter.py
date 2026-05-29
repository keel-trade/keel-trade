"""Register OutcomeTools as FastMCP tools.

Each outcome's `input_schema` becomes the MCP `inputSchema`; the
handler is wrapped in a FastMCP `@mcp.tool(...)` registration. Errors
are caught and serialised through the spec §13 envelope — the wire
never sees a Python traceback.
"""

from __future__ import annotations

import json
from typing import Any

from keel.errors import KeelError

from ._base import OutcomeResult, OutcomeTool, ToolContext, envelope_error
from ._toolsets import is_tool_loaded, load_toolsets


def _make_handler(tool: OutcomeTool, toolsets: frozenset[str]):
    """Construct a closure FastMCP can register.

    FastMCP introspects the function signature for runtime argument
    validation; `register_all()` separately publishes the declared
    `input_schema` so tools/list keeps descriptions, enums, formats, and
    required fields. The wrapper:
      1. validates schema args (required + unexpected) and emits the
         spec §13.5 envelope on failure;
      2. assembles a `ToolContext` (MCP is never a TTY),
      3. invokes the handler,
      4. converts return → JSON envelope,
      5. converts errors → spec §13 envelope.

    Unknown args normally fail in FastMCP/Pydantic before this wrapper
    runs because FastMCP does not accept `**kwargs` tool signatures.
    `register_all()` wraps those framework validation errors and
    converts them into the same envelope shape.
    """
    schema = tool.input_schema
    schema_props: dict = schema.get("properties", {})
    schema_required: set = set(schema.get("required", []))
    known_props: set = set(schema_props.keys())

    def handler(**kwargs: Any) -> str:
        # Strip Nones — JSON Schema defaults are handled by the handler
        # itself, not by the adapter.
        args = {k: v for k, v in kwargs.items() if v is not None}

        # ── Pre-flight: validate args via spec §13 envelope ─────────
        # FastMCP's auto-pydantic layer would otherwise raise on
        # missing-required / unexpected-keyword and surface the raw
        # stack trace as opaque text — useless to agents. Validate
        # against the declared input_schema ourselves so the response
        # is always a structured envelope.
        provided = set(args.keys())
        missing_required = sorted(schema_required - provided)
        unexpected = sorted(provided - known_props)
        if missing_required or unexpected:
            problems: list[str] = []
            if missing_required:
                problems.append(
                    f"missing required argument(s): {', '.join(missing_required)}"
                )
            if unexpected:
                problems.append(
                    f"unexpected argument(s) {', '.join(unexpected)} "
                    f"(known: {', '.join(sorted(known_props)) or '(none)'})"
                )
            return json.dumps(
                envelope_error(
                    code="usage_error",
                    message=f"Invalid arguments to {tool.name}: " + "; ".join(problems) + ".",
                    what_was_expected=(
                        f"An arguments object matching the tool's inputSchema "
                        f"(required={sorted(schema_required)}, "
                        f"known={sorted(known_props)})."
                    ),
                    example={k: schema_props[k].get("description", "") for k in sorted(known_props)},
                    suggested_next_action={
                        "tool": tool.name,
                        "args": {k: None for k in missing_required},
                        "reason": (
                            f"Re-call `{tool.name}` with the named arguments above. "
                            f"Drop any unknown arg names; pass the required ones."
                        ),
                    },
                ),
                default=str,
            )

        import os as _os
        _app_url = _os.environ.get("KEEL_APP_URL")
        ctx = ToolContext(
            is_tty=False,
            toolsets=toolsets,
            **({"app_url": _app_url} if _app_url else {}),
        )
        try:
            result: OutcomeResult = tool.handler(args, ctx)
            return json.dumps(result.to_envelope(), default=str)
        except KeelError as e:
            # Spec §13.5 envelope comes straight off KeelError. Same
            # shape on the CLI side via output.emit_error so agents
            # parse one structure across both channels.
            return json.dumps(e.to_envelope(), default=str)
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                envelope_error(
                    code="internal_error",
                    message=f"Unexpected error in {tool.name}: {e}",
                    what_was_expected="A successful tool call.",
                    example={},
                    suggested_next_action={
                        "tool": "keel_doctor",
                        "args": {},
                        "reason": "Run keel doctor to diagnose.",
                    },
                ),
                default=str,
            )

    handler.__name__ = tool.name
    handler.__doc__ = tool.description
    return handler


def _action_for_error(e: KeelError) -> dict:
    """Map a KeelError to a `suggested_next_action`.

    Most legacy errors have a free-text `.suggestion`; we wrap it. New
    handlers should emit structured next-actions directly via
    `envelope_error()` and not raise.
    """
    suggestion = getattr(e, "suggestion", None)
    docs_url = getattr(e, "docs_url", None)
    if suggestion:
        return {"tool": None, "args": {}, "reason": suggestion, "docs_url": docs_url}
    return {"tool": "keel_help", "args": {"topic": "errors"}, "reason": "See error reference."}


def _make_param_synthesized_handler(tool: OutcomeTool, toolsets: frozenset[str]):
    """Wrap the dispatch handler with parameters generated from JSON schema.

    FastMCP needs a real function signature with type annotations to
    derive the MCP tool's inputSchema. We synthesise one here.

    All synthesized params are OPTIONAL (default None) regardless of
    whether the schema marks them required — we want required-arg
    validation to happen in our spec §13.5 envelope (`_make_handler`)
    rather than as a raw pydantic stacktrace upstream. The schema
    keeps its `required: [...]` list so `tools/list` still tells the
    agent which args are mandatory; `_make_handler` enforces it.

    FastMCP does NOT support `**kwargs` in tool signatures
    (function_parsing.py rejects with `ValueError`), so we synthesize an
    exact signature and handle unknown-argument ValidationError at the
    registered tool object layer.
    """
    schema = tool.input_schema
    properties: dict = schema.get("properties", {})

    impl = _make_handler(tool, toolsets)

    params: list[str] = []
    for prop, prop_schema in properties.items():
        py_type = _json_type_to_py(prop_schema)
        # Always include a default so pydantic never raises
        # `missing_argument` upstream — required-arg enforcement lives
        # in `_make_handler`'s envelope-emitting check.
        default = prop_schema.get("default")
        default_repr = repr(default) if default is not None else "None"
        params.append(f"{prop}: {py_type} = {default_repr}")

    params_str = ", ".join(params)
    args_dict_str = (
        ", ".join(f"'{p}': {p}" for p in properties) if properties else ""
    )

    func_src = (
        f"def {tool.name}({params_str}) -> str:\n"
        f"    return _impl(**{{{args_dict_str}}})\n"
    )
    local_ns: dict[str, Any] = {"_impl": impl}
    exec(func_src, local_ns)  # noqa: S102 — controlled code-generation, no user input
    fn = local_ns[tool.name]
    fn.__doc__ = tool.description
    fn.__module__ = "keel.tools.outcomes._mcp_adapter"
    return fn


def _strip_cli_schema_extensions(value: Any) -> Any:
    """Remove CLI-only schema hints before publishing the MCP schema."""
    if isinstance(value, dict):
        return {
            k: _strip_cli_schema_extensions(v)
            for k, v in value.items()
            if not k.startswith("x-cli-")
        }
    if isinstance(value, list):
        return [_strip_cli_schema_extensions(v) for v in value]
    return value


def _mcp_parameters_schema(tool: OutcomeTool) -> dict:
    """Schema shown in MCP tools/list.

    FastMCP's inferred schema loses important contract details because
    all synthesized parameters are optional by design. Publish Keel's
    declared schema instead so agents can see required fields, enum
    values, formats, descriptions, and top-level strictness before they
    call the tool.
    """
    return _strip_cli_schema_extensions(tool.input_schema)


def _validation_error_envelope(tool: OutcomeTool, raw_error: Exception) -> dict:
    """Convert FastMCP/Pydantic argument validation into the Keel error shape."""
    schema = tool.input_schema
    props: dict = schema.get("properties", {})
    known = sorted(props)
    details = []
    try:
        # Pydantic ValidationError exposes machine-readable details.
        errors = raw_error.errors()  # type: ignore[attr-defined]
    except Exception:
        errors = []

    unexpected: list[str] = []
    invalid: list[str] = []
    for err in errors:
        loc = err.get("loc") or ()
        field = str(loc[0]) if loc else "argument"
        err_type = str(err.get("type") or "")
        if err_type == "unexpected_keyword_argument":
            unexpected.append(field)
        else:
            invalid.append(f"{field}: {err.get('msg', err_type)}")

    if unexpected:
        details.append(
            "unexpected argument(s) "
            + ", ".join(sorted(unexpected))
            + f" (known: {', '.join(known) or '(none)'})"
        )
    if invalid:
        details.append("invalid argument value(s): " + "; ".join(invalid))
    if not details:
        details.append(str(raw_error))

    return envelope_error(
        code="usage_error",
        message=f"Invalid arguments to {tool.name}: " + "; ".join(details) + ".",
        what_was_expected=(
            f"An arguments object matching the tool's inputSchema "
            f"(required={schema.get('required', [])}, known={known})."
        ),
        example={k: props[k].get("description", "") for k in known},
        suggested_next_action={
            "tool": tool.name,
            "args": {k: None for k in schema.get("required", [])},
            "reason": (
                f"Re-call `{tool.name}` using only the named arguments from "
                "`tools/list`; drop unknown arg names and fill required fields."
            ),
        },
    )


def _wrap_fastmcp_validation_errors(tool_obj: Any, outcome: OutcomeTool) -> None:
    """Patch one FastMCP tool object to return structured validation errors.

    FastMCP validates call arguments against the generated Python
    function signature before invoking our handler. That is correct for
    rejecting unknown fields, but the raw Pydantic error is not useful
    to agents. Wrapping `run` here keeps the exact-signature behavior
    while preserving Keel's structured error contract.
    """
    from pydantic import ValidationError

    original_run = tool_obj.run

    async def run_with_keel_validation(arguments: dict[str, Any]):
        try:
            return await original_run(arguments)
        except ValidationError as e:
            return tool_obj.convert_result(
                json.dumps(_validation_error_envelope(outcome, e), default=str)
            )

    # FunctionTool is a Pydantic model and disallows assigning undeclared
    # attributes through normal setattr. `run` is a class method, so use
    # object.__setattr__ for this per-instance adapter.
    object.__setattr__(tool_obj, "run", run_with_keel_validation)


def _json_type_to_py(schema: dict) -> str:
    """Map a JSON Schema primitive type to a Python annotation string."""
    t = schema.get("type")
    if "enum" in schema:
        return "str"
    if t == "boolean":
        return "bool"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "array":
        return "list"
    if t == "object":
        return "dict"
    return "str"


def register_all(mcp_server: Any, outcomes: dict[str, OutcomeTool]) -> None:
    """Attach every outcome tool to the MCP server, filtered by KEEL_TOOLSETS.

    Tools whose toolset is not in the active set are NOT registered;
    they don't appear in `tools/list` and `tools/call` returns
    "tool not found" if invoked anyway.
    """
    from fastmcp.tools.function_tool import FunctionTool
    from mcp.types import ToolAnnotations

    toolsets = load_toolsets()
    for tool in outcomes.values():
        if not is_tool_loaded(tool.toolset, toolsets):
            continue
        fn = _make_param_synthesized_handler(tool, toolsets)
        annotations = (
            ToolAnnotations(**tool.annotations)
            if isinstance(tool.annotations, dict)
            else tool.annotations
        )
        tool_obj = FunctionTool.from_function(
            fn,
            name=tool.name,
            description=tool.description,
            annotations=annotations,
        )
        tool_obj.parameters = _mcp_parameters_schema(tool)
        _wrap_fastmcp_validation_errors(tool_obj, tool)
        mcp_server.add_tool(tool_obj)


def loaded_tool_names(outcomes: dict[str, OutcomeTool]) -> list[str]:
    """Return the names of tools that would be registered under the
    current `KEEL_TOOLSETS`. Used by `keel_status`."""
    toolsets = load_toolsets()
    return sorted(
        t.name for t in outcomes.values() if is_tool_loaded(t.toolset, toolsets)
    )
