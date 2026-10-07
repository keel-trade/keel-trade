"""A parameter's two ranges, labelled so an agent cannot misread one for the other.

Q-2242 (param-domains, 2026-10-01). A numeric parameter's ``constraints``
carry two different things:

* ``min`` / ``max`` — the HARD limits, the only values the validator
  rejects (``PARAM_OUT_OF_RANGE``). Absent = unbounded on that side;
  ``min_exclusive`` / ``max_exclusive`` (present only when true) make a
  bound strict.
* ``typical: [lo, hi]`` — GUIDANCE for choosing a value. A value outside it
  is valid; the validator never reports it.

Before Q-2242 the old ``[min, max]`` were optimizer/UI hints enforced as
hard errors. The raw keys still reach every reader verbatim; this module adds
the rendered, labelled form both the JSON envelope (``range``) and the
markdown parameter table (``hard limit`` / ``typical (guidance)`` columns)
show. A constraints dict from a registry that predates ``typical`` renders a
hard limit only — never an invented typical range.
"""

from __future__ import annotations

import math
from typing import Any


__all__ = [
    "RANGE_KEYS",
    "RANGE_NOTE",
    "format_interval",
    "hard_limit",
    "param_range",
    "typical_range",
]

#: One line, once per component, wherever a parameter carries a range.
RANGE_NOTE = (
    "hard_limit is the only range the validator rejects; typical_guidance is where "
    "values usually sit - a value outside it is valid."
)

#: Constraint keys the two labelled renderings fully express.
RANGE_KEYS = ("min", "max", "min_exclusive", "max_exclusive", "typical")


def _num(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return str(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_interval(
    lo: Any, lo_exclusive: bool, hi: Any, hi_exclusive: bool, *, inf: str = "inf"
) -> str:
    """Interval notation: ``[2, 100]``, ``(0, inf)``, ``[1, inf)``, ``(-inf, 0.5]``.

    ``None`` is an unbounded side (always an open bracket). ``inf`` is the
    infinity glyph — ``"inf"`` for JSON (``json.dumps`` would escape ``∞``),
    ``"∞"`` for markdown a model reads as text.
    """
    left = "(" if lo is None or lo_exclusive else "["
    right = ")" if hi is None or hi_exclusive else "]"
    lo_s = f"-{inf}" if lo is None else _num(lo)
    hi_s = inf if hi is None else _num(hi)
    return f"{left}{lo_s}, {hi_s}{right}"


def hard_limit(constraints: dict | None, *, inf: str = "inf") -> str | None:
    """The hard range, or None when the param has no bound and no typical range.

    A param with a ``typical`` range but no bound renders ``(-inf, inf)`` —
    "unbounded" said out loud, so the typical range beside it cannot be
    mistaken for a limit.
    """
    c = constraints or {}
    lo = c.get("min") if _is_number(c.get("min")) else None
    hi = c.get("max") if _is_number(c.get("max")) else None
    if lo is None and hi is None and typical_range(c) is None:
        return None
    return format_interval(
        lo,
        bool(c.get("min_exclusive")) and lo is not None,
        hi,
        bool(c.get("max_exclusive")) and hi is not None,
        inf=inf,
    )


def typical_range(constraints: dict | None, *, inf: str = "inf") -> str | None:
    """``typical`` as a closed interval, or None when the constraints carry none."""
    t = (constraints or {}).get("typical")
    if not isinstance(t, (list, tuple)) or len(t) != 2 or not all(_is_number(v) for v in t):
        return None
    return format_interval(t[0], False, t[1], False, inf=inf)


def param_range(constraints: dict | None) -> dict | None:
    """The labelled JSON form: ``{"hard_limit": ..., "typical_guidance": ...}``.

    ``typical_guidance`` is absent when the constraints carry no ``typical``;
    the whole value is None when the param has neither.
    """
    hard = hard_limit(constraints)
    if hard is None:
        return None
    out = {"hard_limit": hard}
    typical = typical_range(constraints)
    if typical is not None:
        out["typical_guidance"] = typical
    return out
