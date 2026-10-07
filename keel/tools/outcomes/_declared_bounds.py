"""Declared input bounds, enforced where every handler runs (Q-2270).

A tool's input schema is its contract on every surface: the MCP adapter
publishes it, the CLI renders options from it, and the generated docs quote
it. The adapter's synthesized signature validates each argument's TYPE only
(`_mcp_adapter._json_type_to_py`), so a declared `maximum` or `maxLength` was
a promise nothing kept — `limit=500` reached a handler declared 1–100 and
was clamped to 100 with nothing said, and a value past keel-api's own bound
came back as a raw 422.

This module is the one place a declared bound is enforced: `OutcomeTool`
wraps its handler with `enforce_declared_bounds`, so a value outside a
declared bound is refused with the parameter, the bound and the value named,
before the handler (and keel-api) ever sees it. Handlers no longer clamp.

Checked, per top-level argument that is present and not None:

* numbers — `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`;
* strings — `minLength`, `maxLength`;
* arrays — `minItems`, `maxItems`.

Not checked here: `enum` (the listed schema spells some values differently
and both spellings are accepted — `_base.LISTED_ENUM_RENAMES`), `type` (the
adapter's signature owns it) and nested objects (`config` is validated by
its own model, `BacktestConfig`).
"""

from __future__ import annotations

import functools
from typing import Any, Callable


__all__ = ["bound_violation", "enforce_declared_bounds", "wrap_handler"]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _range_words(spec: dict) -> str:
    """`from 1 to 100` / `at least 1` / `at most 200 characters` …"""
    lo = spec.get("minimum")
    hi = spec.get("maximum")
    if lo is not None and hi is not None:
        return f"from {_fmt(lo)} to {_fmt(hi)}"
    if spec.get("exclusiveMinimum") is not None:
        return f"greater than {_fmt(spec['exclusiveMinimum'])}"
    if lo is not None:
        return f"at least {_fmt(lo)}"
    if hi is not None:
        return f"at most {_fmt(hi)}"
    if spec.get("exclusiveMaximum") is not None:
        return f"less than {_fmt(spec['exclusiveMaximum'])}"
    return ""


def bound_violation(name: str, spec: dict, value: Any) -> str | None:
    """The sentence refusing `value` for parameter `name`, or None when it
    satisfies every bound `spec` declares."""
    if value is None or not isinstance(spec, dict):
        return None
    if _is_number(value):
        lo, hi = spec.get("minimum"), spec.get("maximum")
        xlo, xhi = spec.get("exclusiveMinimum"), spec.get("exclusiveMaximum")
        if (
            (lo is not None and value < lo)
            or (hi is not None and value > hi)
            or (xlo is not None and value <= xlo)
            or (xhi is not None and value >= xhi)
        ):
            return f"`{name}` must be {_range_words(spec)} (got {_fmt(value)})."
        return None
    if isinstance(value, str):
        lo, hi = spec.get("minLength"), spec.get("maxLength")
        if lo is not None and len(value) < lo:
            if lo == 1:
                return f"`{name}` must not be empty."
            return f"`{name}` must be at least {lo} characters (got {len(value)})."
        if hi is not None and len(value) > hi:
            return f"`{name}` must be at most {hi} characters (got {len(value)})."
        return None
    if isinstance(value, (list, tuple)):
        lo, hi = spec.get("minItems"), spec.get("maxItems")
        if lo is not None and len(value) < lo:
            return f"`{name}` takes at least {lo} item(s) (got {len(value)})."
        if hi is not None and len(value) > hi:
            return f"`{name}` takes at most {hi} item(s) (got {len(value)})."
    return None


def _effective_schema(tool: Any) -> dict:
    """The schema served on the active profile — the same choice as
    `_mcp_adapter.effective_input_schema` (imported lazily: that module
    imports this package)."""
    from ._toolsets import is_listed_profile

    listed = getattr(tool, "listed_input_schema", None)
    if listed is not None and is_listed_profile():
        return listed
    return tool.input_schema


def enforce_declared_bounds(tool: Any, args: dict) -> None:
    """Raise a `ValidationError` naming the first argument outside a bound
    its schema declares. Arguments the schema does not declare are not this
    function's business (the adapters own unknown-argument handling)."""
    if not isinstance(args, dict):
        return
    properties = _effective_schema(tool).get("properties") or {}
    for name, spec in properties.items():
        if name not in args:
            continue
        problem = bound_violation(name, spec, args[name])
        if problem is None:
            continue
        from keel.errors import ValidationError

        raise ValidationError(
            problem,
            error_code="argument_out_of_range",
            exit_code=2,
            suggestion=(
                f"Re-call {tool.name} with `{name}` inside the range its input schema "
                "declares; nothing was run."
            ),
            input={name: args[name]},
        )


def wrap_handler(tool: Any, handler: Callable) -> Callable:
    """`handler` with the declared bounds enforced first. Idempotent: an
    already-wrapped handler is returned as it is."""
    if getattr(handler, "__keel_bounds_enforced__", False):
        return handler

    @functools.wraps(handler)
    def bounded(args: dict, ctx: Any) -> Any:
        enforce_declared_bounds(tool, args)
        return handler(args, ctx)

    bounded.__keel_bounds_enforced__ = True  # type: ignore[attr-defined]
    return bounded
