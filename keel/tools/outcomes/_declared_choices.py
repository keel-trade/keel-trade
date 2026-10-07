"""Declared ``enum`` and ``format`` enforced where every handler runs (Q-2273 L5).

A tool's input schema is its published contract, but the adapter's
synthesized signature types every enum parameter as ``str``
(`_mcp_adapter._json_type_to_py`) and nothing read ``format``, so a value
outside the published set reached the handler and was treated as a default:
``present="huge"`` read as ``receipt``, ``kind="rant"`` was stored,
``category="nonsense"`` filtered to an empty result, and
``start_date="2025-13-45"`` queued a backtest. The round-5 audit saw each.

`register()` wraps every handler with `enforce_declared_choices`, so the
CLI, MCP and a direct call all refuse such a value with ``usage_error``
naming the parameter, the value sent and the values the schema publishes —
before the handler, and keel-api, ever see it. (Declared numeric/length
BOUNDS are the sibling module's, `_declared_bounds`, Q-2270.)

Checked, per top-level argument that is present and not None:

* ``enum`` against BOTH spellings a value may arrive in: the shared
  schema's and the listed schema's (`_base.LISTED_ENUM_RENAMES` renames three
  `keel_live_monitor` views; a frozen catalog may send either). The refusal
  lists the spelling the ACTIVE profile publishes and echoes the value sent.
* ``format: date`` — a calendar date written ``YYYY-MM-DD``.

An empty string is left to the handler (several read it as "absent"). An
array's ``items.enum`` is the handler's: `keel_backtest_compare` normalises
`holds` (case, duplicates) before refusing with ``bad_compare_args``.

A parameter whose handler already refused bad values under its own code
keeps that code (`OWNED_CODES`) — the wrapper's message, the handler's
error class and code — so no caller branching on a code sees it change.
"""

from __future__ import annotations

import functools
import re
from datetime import date
from typing import Any, Callable


__all__ = ["choice_violation", "enforce_declared_choices"]

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Id parameters → the prefix their ids carry (``None``: no fixed prefix).
#: An id goes into a URL path, so anything but letters, digits, ``_`` and
#: ``-`` is refused (Q-2273): ``strategy_id="str_x/versions"`` read the
#: versions endpoint. An id with ANOTHER known prefix is named for what it
#: is, with the tool that reads it.
ID_PARAMS: dict[str, str | None] = {
    "strategy_id": "str_",
    "backtest_id": "btr_",
    "backtest_ids": "btr_",
    "deployment_id": "dep_",
    "target_id": None,
    "id": None,
    "slug": None,
    "variant_id": None,
    "execution_run_id": None,
}
_ID_SHAPE = re.compile(r"[A-Za-z0-9_-]+")
#: A known prefix → (what the id is, the listed tool that reads it).
_KNOWN_PREFIXES: dict[str, tuple[str, str]] = {
    "str_": ("a strategy id", "keel_strategy_get"),
    "btr_": ("a backtest run id", "keel_backtest_summarize"),
    "dep_": ("a running strategy's id", "keel_live_monitor"),
}

#: (tool, parameter) → (error class name, code) for the parameters whose
#: handler refused a bad value under its own code before Q-2273. Every other
#: refusal is ``usage_error``.
OWNED_CODES: dict[tuple[str, str], tuple[str, str]] = {
    ("keel_backtest_summarize", "start"): ("KeelError", "invalid_slice_bound"),
    ("keel_backtest_summarize", "end"): ("KeelError", "invalid_slice_bound"),
    ("keel_share_create", "permission"): ("KeelError", "invalid_permission"),
    ("keel_live_monitor", "view"): ("ValidationError", "validation_failed"),
    ("keel_live_control", "action"): ("ValidationError", "validation_failed"),
}


def _props(schema: dict | None) -> dict:
    return (schema or {}).get("properties", {}) or {}


def _allowed(tool: Any, name: str) -> list | None:
    """Every accepted spelling of ``name``'s enum across both schemas, or
    ``None`` when neither declares one."""
    values: list = []
    for schema in (tool.input_schema, tool.listed_input_schema):
        for v in (_props(schema).get(name) or {}).get("enum") or []:
            if v not in values:
                values.append(v)
    return values or None


def _published(tool: Any, name: str) -> list:
    """The enum the ACTIVE profile publishes for ``name`` (for the message)."""
    from ._mcp_adapter import effective_input_schema

    spec = _props(effective_input_schema(tool)).get(name) or {}
    return list(spec.get("enum") or []) or (_allowed(tool, name) or [])


def _refusal(tool: Any, name: str, message: str, suggestion: str, value: Any) -> Exception:
    from keel import errors

    cls_name, code = OWNED_CODES.get((tool.name, name), ("UsageError", "usage_error"))
    cls = getattr(errors, cls_name)
    # The owned refusals' exit codes as they were: ValidationError's own (7),
    # every other one 2 (a usage error).
    exit_code = None if cls is errors.ValidationError else 2
    return cls(
        message, error_code=code, exit_code=exit_code, suggestion=suggestion, input={name: value}
    )


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value.strip()):
        return False
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def choice_violation(tool: Any, args: dict) -> Exception | None:
    """The refusal for the first argument outside its declared enum or
    format, or ``None`` when every present argument is inside it."""
    names = list(dict.fromkeys([*_props(tool.input_schema), *_props(tool.listed_input_schema)]))
    for name in names:
        value = args.get(name)
        if value is None or value == "":
            continue
        allowed = _allowed(tool, name)
        if allowed is not None and value not in allowed:
            shown = ", ".join(f"`{v}`" for v in _published(tool, name))
            return _refusal(
                tool,
                name,
                f"`{name}` must be one of {shown} (got {value!r}).",
                f"Re-call `{tool.name}` with `{name}` set to one of the listed values.",
                value,
            )
        formats = {
            (_props(schema).get(name) or {}).get("format")
            for schema in (tool.input_schema, tool.listed_input_schema)
        }
        if "date" in formats and not _is_iso_date(value):
            return _refusal(
                tool,
                name,
                f"`{name}` must be a calendar date written YYYY-MM-DD (got {value!r}).",
                f"Re-call `{tool.name}` with `{name}` like `2025-01-31`.",
                value,
            )
        if name in ID_PARAMS:
            refusal = _id_violation(tool, name, value)
            if refusal is not None:
                return refusal
    return None


def _id_violation(tool: Any, name: str, value: Any) -> Exception | None:
    """An id that cannot be one, or that is another kind of id."""
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        text = item.strip()
        if name == "deployment_id" and text == "all":
            continue
        if not _ID_SHAPE.fullmatch(text):
            return _refusal(
                tool,
                name,
                f"`{name}` is not an id (got {item!r}); ids are letters, digits, `_` and `-`.",
                f"Pass the id exactly as a Keel result gave it to `{tool.name}`.",
                value,
            )
        expected = ID_PARAMS[name]
        for prefix, (what, reader) in _KNOWN_PREFIXES.items():
            if expected and prefix != expected and text.startswith(prefix):
                return _refusal(
                    tool,
                    name,
                    f"`{name}` takes a `{expected}…` id; {item!r} is {what}.",
                    f"`{reader}` reads {what}; `{tool.name}` needs a `{expected}…` id.",
                    value,
                )
    return None


def enforce_declared_choices(tool: Any, handler: Callable) -> Callable:
    """``handler`` refusing any argument outside its declared enum/format."""

    @functools.wraps(handler)
    def checked(args: dict, ctx: Any) -> Any:
        refusal = choice_violation(tool, args or {})
        if refusal is not None:
            raise refusal
        return handler(args, ctx)

    return checked
