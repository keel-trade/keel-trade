"""The position layer's binding facet, scope machine and trade-safety verdict.

Position-layer spec 03 §2.C/§2.D/§2.E (Q-2448). ONE implementation, shared by
the static walk (``dsl/interpreter.py``) and spec 01's runtime lift
(``pipeline_engine.positions.lift``): neither re-implements a verdict, so the
static check equals the runtime, including writes inside branches (AC-3).
The TS engine (``engine/binding.ts``) executes the same rules from the
``binding`` block this module contributes to ``judgment_tables.json``.

Pure stdlib by construction (vendored into the SDK; imported by
``base/registration.py`` at component registration). Nothing here imports
pandas; the only ``pipeline_engine`` import is the ``live_window`` constant.

The model, in one paragraph. Every value carries a **facet** beside its base
type, domain and clock: ``market`` (the default, by omission), a
``position(L, A)`` minted by the ``TradeManager`` at node path L with anchor A
(the ``(rule, kind)`` pairs already on it), ``trade(L)`` / ``flat(L)`` series
bound to L's trade or between-trade windows, or ``realized(L, A)`` (the output
of ``Exposure()`` / ``RiskSizer``). Bodies are **frames**: a nested pipeline,
a variable or a factory expansion shares its enclosing frame (sequential); a
Parallel branch is an isolated frame. The :class:`ScopeMachine` is driven one
event at a time (``step``, ``store``, ``load``, ``enter_branch`` /
``exit_branch`` / ``merge_parallel``, ``finish``) and answers each with the
output facet plus the :class:`Finding` list. A finding is a catalog code, a
message *shape* and its parameters; rendering goes through the generated
``SHAPES`` table, so Python, TS and the runtime say the same words.

Quick Start:
    >>> from pipeline_engine.binding import ScopeMachine, ComponentDecl, MARKET
    >>> m = ScopeMachine()
    >>> head = ComponentDecl(name="TradeManager", version=1, category="position_manager",
    ...     binding={"role": "head", "entry_slots": ["entries"], "market_slots": ["prices"],
    ...              "reentry": {"param": "reentry", "drops_reset_value": "any_bar"},
    ...              "max_units_param": "max_units"})
    >>> out = m.step(head, MARKET, path="root/0", params={"entries": "e"})
    >>> out.value.kind, out.findings
    ('position', [])
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Union

from pipeline_engine.live_window import LIVE_LOOKBACK_DAYS
from timeframes import token_for_minutes


# ═══════════════════════════════════════════════════════════════════════════════
# CLOSED VOCABULARIES (spec 03-R5, R6, R46, R67) — generated into
# judgment_tables.json's ``binding`` block; the TS engine reads them from there
# and never restates them (AC-5).
# ═══════════════════════════════════════════════════════════════════════════════

ROLES = ("head", "reader", "trade_op", "action", "realizer", "position_sizer", "factory")
SCOPES = ("trade", "flat")
SCALES = ("fraction", "price", "bars", "count", "units", "other")
ACTION_KINDS = ("exit", "reduce", "scale_in", "allow_entry")
NEEDS_UPSTREAM = ("reduce", "add", "leg")
FACET_KINDS = ("market", "position", "trade", "flat", "realized")
COMPARE_OPS = ("gt", "ge", "lt", "le")
WARMUP_OPS = ("param", "floordiv", "add", "sub", "coalesce", "max")
NARROWING_KEYS = ("min", "max", "equals", "in")
TEMPLATE_OPS = ("$arg", "$neg", "$bars_of", "$scope")
TEMPLATE_STEP_KINDS = ("component", "load", "parallel", "$when")

#: 03-R67: the sub-category each role must declare (a reader's depends on its
#: scope). Closed; registration refuses a mismatch.
ROLE_SUB_CATEGORIES: dict[str, str | dict[str, str]] = {
    "head": "trade_manager",
    "reader": {"trade": "trade_reader", "flat": "between_trades_reader"},
    "trade_op": "trade_operator",
    "action": "trade_action",
    "realizer": "exposure",
    "position_sizer": "risk_sizing",
    "factory": "trade_factory",
}

#: 03-R67: the closed position-layer sub-category vocabulary, flattened from
#: :data:`ROLE_SUB_CATEGORIES` (one owner). The ``ComponentSelector`` grouping,
#: spec 04-R29's ranking (``component_ranking.POSITION_LAYER_SUB_CATEGORIES``,
#: which must equal this) and spec 05-R19's docstring contract read it.
POSITION_LAYER_SUB_CATEGORIES: frozenset[str] = frozenset(
    sub
    for v in ROLE_SUB_CATEGORIES.values()
    for sub in (v.values() if isinstance(v, dict) else (v,))
)

#: 03-R15.1: a Position reaching an ordinary component of one of these
#: categories is ``POSITION_NEEDS_READER`` (the author was building a rule);
#: any other category is ``POSITION_NEEDS_EXPOSURE`` (sizers included, D-39.1).
RULE_CATEGORIES = (
    "signal_transform",
    "signal_composer",
    "indicator",
    "data_transform",
    "universe_filter",
    "regime_detector",
)

#: The required/optional keys of each role's ``binding`` declaration (03-R5).
_ROLE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "head": (
        frozenset({"entry_slots", "market_slots", "reentry", "max_units_param"}),
        frozenset(),
    ),
    "reader": (
        frozenset({"scope", "scale"}),
        frozenset({"needs_upstream", "leg_param", "market_slots", "window", "warmup"}),
    ),
    "trade_op": (frozenset({"warmup"}), frozenset()),
    "action": (frozenset({"kind"}), frozenset()),
    "realizer": (frozenset(), frozenset()),
    "position_sizer": (frozenset({"market_slots"}), frozenset()),
    "factory": (frozenset(), frozenset()),
}

#: The live look-back of an evaluation, in days (spec 02-R9; the one constant
#: lives in ``pipeline_engine.live_window``, reconciliation X-45). A windowed
#: reader or factory whose window is at or above it can never be answered
#: live, so it is refused statically (``WINDOW_EXCEEDS_LIVE_LOOKBACK``).

#: The ``Position`` base type's name (spec 03-R1; ``types.Position``). A
#: merged rule stage's value is this base (03-R17); the TS engine reads it
#: from the generated table instead of spelling a type-universe name.
#: ``binding_test`` pins it to ``types.Position``.
POSITION_BASE = "Position"

#: The base of a realised exposure (``Exposure()``'s output, spec 03-R14). A
#: Position whose scope is refused as never exposed (a branch that ends with
#: its own scope open, 03-R27) recovers as this base, so the one mistake does
#: not also fire every downstream base-type row (03-R18). ``binding_test``
#: pins it to ``types.SignalSeries``.
REALIZED_BASE = "SignalSeries"

#: D8: the window-token grammar (the ``bar_offset`` grammar).
_WINDOW_RE = re.compile(r"^([1-9][0-9]*)(min|h|d|w)$")
_UNIT_MINUTES = {"min": 1, "h": 60, "d": 1440, "w": 10080}

#: L⊥ — the recovery lineage. It matches every lineage, so one mistake fires
#: exactly one binding code (03-R18).
BOTTOM = "⊥"


def window_minutes(token: Any) -> int | None:
    """Minutes of a D8 window token, or ``None`` when it is not one."""
    if not isinstance(token, str):
        return None
    m = _WINDOW_RE.match(token)
    if m is None:
        return None
    return int(m.group(1)) * _UNIT_MINUTES[m.group(2)]


def same_lineage(a: str | None, b: str | None) -> bool:
    """Lineage equality with the recovery lineage ⊥ matching every lineage."""
    return a == b or a == BOTTOM or b == BOTTOM


# ═══════════════════════════════════════════════════════════════════════════════
# DECLARATION VALIDATION (registration, 03-R5/R6/R8/R46) — loud TypeError,
# the clock_transfer / population_scope admission-gate pattern.
# ═══════════════════════════════════════════════════════════════════════════════


def _need(cond: bool, cls_name: str, what: str) -> None:
    if not cond:
        raise TypeError(f"Component {cls_name}.{what}")


def _param_list(cls_name: str, attr: str, key: str, value: Any, params: Iterable[str]) -> list[str]:
    params = set(params)
    _need(
        isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value),
        cls_name,
        f"{attr}[{key!r}] must be a list of parameter names, got {value!r}",
    )
    for v in value:
        _need(v in params, cls_name, f"{attr}[{key!r}] names {v!r}, which is not a parameter")
    return list(value)


def validate_binding(
    cls_name: str, decl: Any, parameters: Iterable[str], sub_category: str | None
) -> dict:
    """Validate a component's ``binding`` declaration and return it JSON-native.

    Refuses an unknown role, a missing or unknown key, a slot/param key that
    is not a parameter, and a ``sub_category`` other than the role's
    canonical one (03-R5, R67).
    """
    params = list(parameters)
    _need(isinstance(decl, dict), cls_name, f"binding must be a dict, got {type(decl).__name__}")
    role = decl.get("role")
    _need(role in ROLES, cls_name, f"binding['role'] must be one of {list(ROLES)}, got {role!r}")
    required, optional = _ROLE_KEYS[role]
    keys = set(decl) - {"role"}
    missing = required - keys
    _need(not missing, cls_name, f"binding (role {role!r}) is missing key(s) {sorted(missing)}")
    unknown = keys - required - optional
    _need(not unknown, cls_name, f"binding (role {role!r}) has unknown key(s) {sorted(unknown)}")
    out: dict[str, Any] = {"role": role}
    for key in ("entry_slots", "market_slots"):
        if key in decl:
            out[key] = _param_list(cls_name, "binding", key, decl[key], params)
    if role == "head":
        re_ = decl["reentry"]
        _need(
            isinstance(re_, dict) and set(re_) == {"param", "drops_reset_value"},
            cls_name,
            "binding['reentry'] must be {'param': <param>, 'drops_reset_value': <value>}",
        )
        _need(
            re_["param"] in params, cls_name, f"binding['reentry'] param {re_['param']!r} unknown"
        )
        out["reentry"] = {"param": re_["param"], "drops_reset_value": re_["drops_reset_value"]}
        _need(
            decl["max_units_param"] in params,
            cls_name,
            f"binding['max_units_param'] {decl['max_units_param']!r} is not a parameter",
        )
        out["max_units_param"] = decl["max_units_param"]
    if role == "reader":
        _need(decl["scope"] in SCOPES, cls_name, f"binding['scope'] must be one of {list(SCOPES)}")
        _need(decl["scale"] in SCALES, cls_name, f"binding['scale'] must be one of {list(SCALES)}")
        out["scope"], out["scale"] = decl["scope"], decl["scale"]
        if "needs_upstream" in decl:
            nu = decl["needs_upstream"]
            _need(
                nu in NEEDS_UPSTREAM,
                cls_name,
                f"binding['needs_upstream'] must be one of {list(NEEDS_UPSTREAM)}",
            )
            out["needs_upstream"] = nu
            if nu == "leg":
                _need(
                    decl.get("leg_param") in params,
                    cls_name,
                    "binding needs_upstream 'leg' requires 'leg_param' naming a parameter",
                )
                out["leg_param"] = decl["leg_param"]
        _need(
            "leg_param" not in decl or decl.get("needs_upstream") == "leg",
            cls_name,
            "binding['leg_param'] is only meaningful with needs_upstream 'leg'",
        )
        if "window" in decl:
            w = decl["window"]
            _need(
                isinstance(w, dict) and set(w) == {"param", "anchor_param", "calendar_windows"},
                cls_name,
                "binding['window'] must be {'param', 'anchor_param', 'calendar_windows'}",
            )
            _need(w["param"] in params, cls_name, f"binding['window'] param {w['param']!r} unknown")
            _need(
                w["anchor_param"] in params,
                cls_name,
                f"binding['window'] anchor_param {w['anchor_param']!r} unknown",
            )
            cal = w["calendar_windows"]
            _need(
                isinstance(cal, (list, tuple)) and all(window_minutes(c) for c in cal),
                cls_name,
                "binding['window'] calendar_windows must be a list of window tokens",
            )
            out["window"] = {
                "param": w["param"],
                "anchor_param": w["anchor_param"],
                "calendar_windows": list(cal),
            }
    if role in ("reader", "trade_op"):
        wu = decl.get("warmup", 0)
        _need(
            isinstance(wu, int) and not isinstance(wu, bool) and wu >= 0,
            cls_name,
            "binding['warmup'] must be an int >= 0",
        )
        if role == "trade_op" or "warmup" in decl:
            out["warmup"] = wu
    if role == "action":
        _need(
            decl["kind"] in ACTION_KINDS,
            cls_name,
            f"binding['kind'] must be one of {list(ACTION_KINDS)}",
        )
        out["kind"] = decl["kind"]
    canonical = ROLE_SUB_CATEGORIES[role]
    if isinstance(canonical, dict):
        canonical = canonical[out["scope"]]
    _need(
        sub_category == canonical,
        cls_name,
        f"sub_category must be {canonical!r} for binding role {role!r}, got {sub_category!r}",
    )
    return out


def _validate_warmup_expr(cls_name: str, expr: Any, params: set[str]) -> Any:
    if isinstance(expr, int) and not isinstance(expr, bool):
        _need(expr >= 0, cls_name, f"trade_safe warmup literal must be >= 0, got {expr}")
        return expr
    _need(
        isinstance(expr, dict) and len(expr) == 1 and next(iter(expr)) in WARMUP_OPS,
        cls_name,
        f"trade_safe warmup must be an int or one of {list(WARMUP_OPS)}, got {expr!r}",
    )
    op, arg = next(iter(expr.items()))
    if op == "param":
        _need(arg in params, cls_name, f"trade_safe warmup names {arg!r}, which is not a parameter")
        return {"param": arg}
    _need(
        isinstance(arg, (list, tuple)) and len(arg) == 2,
        cls_name,
        f"trade_safe warmup {op!r} takes two operands",
    )
    a, b = arg
    if op in ("floordiv", "add", "sub"):
        _need(
            isinstance(b, int)
            and not isinstance(b, bool)
            and (b > 0 if op in ("floordiv", "sub") else b >= 0),
            cls_name,
            f"trade_safe warmup {op!r} second operand must be a positive int literal",
        )
        return {op: [_validate_warmup_expr(cls_name, a, params), b]}
    return {
        op: [_validate_warmup_expr(cls_name, a, params), _validate_warmup_expr(cls_name, b, params)]
    }


def validate_trade_safe(cls_name: str, decl: Any, parameters: Iterable[str]) -> dict:
    """Validate a ``trade_safe`` declaration (03-R6) and return it JSON-native."""
    params = set(parameters)
    _need(isinstance(decl, dict), cls_name, f"trade_safe must be a dict, got {type(decl).__name__}")
    unknown = set(decl) - {"warmup", "params", "compares", "scale"}
    _need(not unknown, cls_name, f"trade_safe has unknown key(s) {sorted(unknown)}")
    _need("warmup" in decl, cls_name, "trade_safe requires 'warmup'")
    out: dict[str, Any] = {"warmup": _validate_warmup_expr(cls_name, decl["warmup"], params)}
    if "params" in decl:
        narrowing = decl["params"]
        _need(
            isinstance(narrowing, dict) and narrowing,
            cls_name,
            "trade_safe['params'] must be a non-empty dict",
        )
        norm: dict[str, dict] = {}
        for p, spec in sorted(narrowing.items()):
            _need(
                p in params,
                cls_name,
                f"trade_safe['params'] narrows {p!r}, which is not a parameter",
            )
            _need(
                isinstance(spec, dict) and spec and set(spec) <= set(NARROWING_KEYS),
                cls_name,
                f"trade_safe['params'][{p!r}] keys must be within {list(NARROWING_KEYS)}",
            )
            if "in" in spec:
                _need(
                    isinstance(spec["in"], (list, tuple)),
                    cls_name,
                    f"trade_safe['params'][{p!r}]['in'] must be a list",
                )
                spec = {**spec, "in": list(spec["in"])}
            norm[p] = dict(spec)
        out["params"] = norm
    if "compares" in decl:
        c = decl["compares"]
        _need(
            isinstance(c, dict) and {"param", "op"} <= set(c) <= {"param", "op", "inclusive_param"},
            cls_name,
            "trade_safe['compares'] must be {'param', 'op'[, 'inclusive_param']}",
        )
        _need(
            c["param"] in params, cls_name, f"trade_safe['compares'] param {c['param']!r} unknown"
        )
        _need(
            c["op"] in COMPARE_OPS,
            cls_name,
            f"trade_safe['compares'] op must be one of {list(COMPARE_OPS)}",
        )
        if "inclusive_param" in c:
            _need(
                c["inclusive_param"] in params,
                cls_name,
                "trade_safe['compares'] inclusive_param unknown",
            )
        out["compares"] = dict(c)
    if "scale" in decl:
        _need(decl["scale"] == "keep", cls_name, "trade_safe['scale'] may only be 'keep'")
        out["scale"] = "keep"
    return out


def eval_warmup(expr: Any, params: Mapping[str, Any]) -> int | None:
    """Evaluate a warm-up expression at parameter values (``None`` = unknown)."""
    if isinstance(expr, int):
        return expr
    op, arg = next(iter(expr.items()))
    if op == "param":
        v = params.get(arg)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return int(v)
    a = eval_warmup(arg[0], params)
    b = eval_warmup(arg[1], params) if not isinstance(arg[1], int) else arg[1]
    if op == "coalesce":
        return a if a is not None else b
    if a is None or b is None:
        return None if op != "max" else (a if b is None else b)
    if op == "floordiv":
        return a // b
    if op == "add":
        return a + b
    if op == "sub":  # a rolling window of w is first non-NaN at row w - 1
        return max(a - b, 0)
    return max(a, b)


def _narrowing_text(p: str, spec: Mapping[str, Any]) -> str:
    parts = []
    if "equals" in spec:
        parts.append(f"{p} unset" if spec["equals"] is None else f"{p}={spec['equals']!r}")
    if "in" in spec:
        parts.append(f"{p} in {spec['in']!r}")
    if "min" in spec:
        parts.append(f"{p} >= {spec['min']}")
    if "max" in spec:
        parts.append(f"{p} <= {spec['max']}")
    return " and ".join(parts)


def _narrowing_holds(value: Any, spec: Mapping[str, Any]) -> bool:
    if "equals" in spec and value != spec["equals"]:
        return False
    if "in" in spec and value not in spec["in"]:
        return False
    num = isinstance(value, (int, float)) and not isinstance(value, bool)
    if "min" in spec and not (num and value >= spec["min"]):
        return False
    if "max" in spec and not (num and value <= spec["max"]):
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# FACETS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Facet:
    """The binding fact a value carries (spec 03 §2 notation).

    ``anchor`` holds ``(rule name, action kind)`` pairs (position/realized).
    ``scale`` is a bound series' declared scale (03-R7); ``warmup`` its
    warm-up lower bound in bars after entry (03-R33); ``hold`` the bar by
    which a bars-scale comparator mask is certainly 1 (a hold rule's H).
    ``origin`` names the minting TradeManager's location, for messages.
    """

    kind: str = "market"
    lineage: str | None = None
    anchor: frozenset = frozenset()
    scale: str | None = None
    warmup: int = 0
    hold: int | None = None
    origin: str | None = None
    #: the reader that set ``scale`` (kept through ``scale: keep`` ops), so a
    #: threshold diagnostic can name what it compares
    reader: str | None = None

    @property
    def bound(self) -> bool:
        """True for a Position or a trade/flat series (never market/realized)."""
        return self.kind in ("position", "trade", "flat")

    def to_json(self) -> dict | None:
        """03-R4: serialised only when the facet is not ``market`` (by omission)."""
        if self.kind == "market":
            return None
        return {"kind": self.kind, "lineage": self.lineage, "origin": self.origin}


MARKET = Facet()

#: A record value (the output of a Parallel): branch name → facet.
Record = dict
Value = Union[Facet, Record]


def _to_bottom(value: Value) -> Value:
    if isinstance(value, dict):
        return {k: _to_bottom(v) for k, v in value.items()}
    return replace(value, lineage=BOTTOM) if value.bound else value


def _fields(value: Value) -> list[tuple[str | None, Facet]]:
    if isinstance(value, dict):
        out: list[tuple[str | None, Facet]] = []
        for name, f in value.items():
            if isinstance(f, dict):  # nested record: flatten, keep the outer name
                out.extend((name, g) for _, g in _fields(f))
            else:
                out.append((name, f))
        return out
    return [(None, value)]


# ═══════════════════════════════════════════════════════════════════════════════
# FINDINGS, SHAPES, ROWS
# ═══════════════════════════════════════════════════════════════════════════════

#: The binding judgment rows, in CHECK ORDER (03-R18 — the order IS the
#: single-fire precedence at a step: binding rows run before the base and
#: domain rows, and a fired row suppresses the base TYPE_MISMATCH and the
#: transition advisory for that input). ``recovery`` names the facet the
#: step's output carries after the row fires. Generated into
#: ``judgment_tables.json``'s ``binding.rows`` (re-exported by
#: ``dsl/judgments.py``); every ``code`` here is minted by both validators.
BINDING_ROWS: tuple[dict, ...] = (
    {
        "id": "J-BIND.head-open",
        "event": "head",
        "on_fail": {"code": "POSITION_NEEDS_EXPOSURE"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.head-slot",
        "event": "head",
        "on_fail": {"code": "TRADE_SERIES_LEAK"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.scope-required",
        "event": "position-step",
        "on_fail": {"code": "POSITION_REQUIRED"},
        "recovery": "trade-bottom",
    },
    {
        "id": "J-BIND.reader-lineage",
        "event": "reader",
        "on_fail": {"code": "POSITION_LINEAGE_MISMATCH"},
        "recovery": "declared",
    },
    {
        "id": "J-BIND.reader-upstream",
        "event": "reader",
        "on_fail": {"code": "POSITION_STATE_NOT_UPSTREAM"},
        "recovery": "declared",
    },
    {
        "id": "J-BIND.reader-window",
        "event": "reader",
        "on_fail": {"code": "READER_WINDOW_INVALID"},
        "recovery": "declared",
        "static_only": True,
    },
    {
        "id": "J-BIND.reader-lookback",
        "event": "reader",
        "on_fail": {"code": "WINDOW_EXCEEDS_LIVE_LOOKBACK"},
        "recovery": "declared",
        "static_only": True,
    },
    {
        "id": "J-BIND.market-slot",
        "event": "position-step",
        "on_fail": {"code": "TRADE_SERIES_LEAK"},
        "recovery": "declared",
    },
    {
        "id": "J-BIND.trade-op-scope",
        "event": "trade_op",
        "on_fail": {"code": "TRADE_SCOPE_MIX"},
        "recovery": "declared",
    },
    {
        "id": "J-BIND.trade-op-lineage",
        "event": "trade_op",
        "on_fail": {"code": "POSITION_LINEAGE_MISMATCH"},
        "recovery": "declared",
    },
    {
        "id": "J-BIND.action-mask",
        "event": "action",
        "on_fail": {"code": "POSITION_ACTION_NEEDS_MASK"},
        "recovery": "position",
    },
    {
        "id": "J-BIND.action-lineage",
        "event": "action",
        "on_fail": {"code": "POSITION_LINEAGE_MISMATCH"},
        "recovery": "position",
    },
    {
        "id": "J-BIND.action-feedback",
        "event": "action",
        "on_fail": {"code": "POSITION_FEEDBACK"},
        "recovery": "position",
    },
    {
        "id": "J-BIND.action-scope",
        "event": "action",
        "on_fail": {"code": "TRADE_SCOPE_MIX"},
        "recovery": "position",
    },
    {
        "id": "J-BIND.ordinary-position",
        "event": "ordinary",
        "on_fail": {"code": "POSITION_NEEDS_READER"},
        "recovery": "trade-bottom",
    },
    {
        "id": "J-BIND.ordinary-unrealized",
        "event": "ordinary",
        "on_fail": {"code": "POSITION_NEEDS_EXPOSURE"},
        "recovery": "realized",
    },
    {
        "id": "J-BIND.join-scope",
        "event": "ordinary",
        "on_fail": {"code": "TRADE_SCOPE_MIX"},
        "recovery": "first-bound",
    },
    {
        "id": "J-BIND.join-lineage",
        "event": "ordinary",
        "on_fail": {"code": "POSITION_LINEAGE_MISMATCH"},
        "recovery": "first-bound",
    },
    {
        "id": "J-BIND.trade-safe",
        "event": "ordinary",
        "on_fail": {"code": "TRADE_OP_NOT_TRADE_SAFE"},
        "recovery": "first-bound",
    },
    {
        "id": "J-BIND.sizer-erases",
        "event": "ordinary",
        "on_fail": {"code": "SIZER_ERASES_SIZE"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.threshold-scale",
        "event": "ordinary",
        "on_fail": {"code": "RETURN_THRESHOLD_SCALE"},
        "recovery": "none",
        "static_only": True,
    },
    {
        "id": "J-BIND.market-expanding",
        "event": "ordinary",
        "on_fail": {"code": "MARKET_EXPANDING_IN_POSITION"},
        "recovery": "none",
        "static_only": True,
    },
    {
        "id": "J-BIND.store-position",
        "event": "store",
        "on_fail": {"code": "POSITION_NEEDS_READER"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.load-outside",
        "event": "load",
        "on_fail": {"code": "TRADE_SERIES_LEAK"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.load-lineage",
        "event": "load",
        "on_fail": {"code": "POSITION_LINEAGE_MISMATCH"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.branch-open",
        "event": "branch",
        "on_fail": {"code": "POSITION_NEEDS_EXPOSURE"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.branch-mixed",
        "event": "merge",
        "on_fail": {"code": "POSITION_BRANCH_MIXED"},
        "recovery": "position",
    },
    {
        "id": "J-BIND.terminal-open",
        "event": "terminal",
        "on_fail": {"code": "POSITION_NEEDS_EXPOSURE"},
        "recovery": "none",
    },
    {
        "id": "J-BIND.terminal-leak",
        "event": "terminal",
        "on_fail": {"code": "TRADE_SERIES_LEAK"},
        "recovery": "none",
    },
    {
        "id": "J-PLAN.reentry",
        "event": "close",
        "on_fail": {"code": "REENTRY_ANY_BAR_NO_GATE"},
        "recovery": "none",
        "static_only": True,
    },
    {
        "id": "J-PLAN.warmup",
        "event": "close",
        "on_fail": {"code": "TRADE_RULE_WARMUP"},
        "recovery": "none",
        "static_only": True,
    },
    {
        "id": "J-PLAN.max-units",
        "event": "close",
        "on_fail": {"code": "MAX_UNITS_STATED"},
        "recovery": "none",
        "static_only": True,
    },
    {
        "id": "J-PLAN.scale-in-room",
        "event": "close",
        "on_fail": {"code": "SCALE_IN_NO_ROOM"},
        "recovery": "none",
        "static_only": True,
    },
)
_ROW = {r["id"]: r for r in BINDING_ROWS}

#: Every message shape, per code: ``detail`` (the message) and ``fix`` (the
#: suggestion), each a named-placeholder template over the finding's params.
#: Generated into ``judgment_tables.json`` so the TS engine and the runtime
#: say exactly what the static walk says.
SHAPES: dict[str, dict[str, dict[str, str]]] = {
    "POSITION_ACTION_NEEDS_MASK": {
        "not_series": {
            "detail": "'{action}' needs a trade mask, but it received {received}.",
            "fix": "Start the rule with a reader, then a filter, then '{action}'.",
        },
        "directional": {
            "detail": "'{producer}' gives -1/+1 (a direction); '{action}' fires on 1 only, so shorts would never act.",
            "fix": "Use AboveThresholdFilter(threshold=0) for longs, or multiply by TradeDirection() and test < 0 for a directional rule.",
        },
        "not_mask": {
            "detail": "'{producer}' gives values outside 0 and 1; '{action}' fires only where its mask is exactly 1.",
            "fix": "Compare the value with a filter (for example AboveThresholdFilter(...)) before '{action}'.",
        },
    },
    "POSITION_REQUIRED": {
        "default": {
            "detail": "'{component}' reads a trade, so it needs a Position, and no TradeManager is open here.",
            "fix": "Put '{component}' between TradeManager(...) and Exposure().",
        },
        "after_close": {
            "detail": "'{component}' reads a trade, but the TradeManager's Position was already turned into exposure by '{closed_by}'.",
            "fix": "Put '{component}' after the TradeManager's rules, not after '{closed_by}'.",
        },
    },
    "POSITION_LINEAGE_MISMATCH": {
        "default": {
            "detail": "'{component}' mixes values of two TradeManagers: the one at {origin_a} and the one at {origin_b}.",
            "fix": "Keep each TradeManager's rules between it and its own Exposure(); compute shared values on the market before the TradeManager.",
        },
    },
    "TRADE_SERIES_LEAK": {
        "slot": {
            "detail": "'{component}' reads '{param}' as a market value, but it holds a trade value of the TradeManager at {origin}.",
            "fix": "Feed '{param}' a market value computed before the TradeManager; trade values stay inside their TradeManager's rules.",
        },
        "load": {
            "detail": "Load('{slot}') reads a trade value of the TradeManager at {origin} outside that TradeManager's rules.",
            "fix": "Load trade values only inside their own TradeManager's rules, before Exposure().",
        },
        "terminal": {
            "detail": "The pipeline ends with a trade value of the TradeManager at {origin}.",
            "fix": "End the rule with an action (Exit(), Reduce(), ScaleIn() or AllowEntry()), then Exposure() and a sizer.",
        },
    },
    "POSITION_BRANCH_MIXED": {
        "between_trades": {
            "detail": "Branch '{branch}' does not end in an action, while the other branches of this rule stage do.",
            "fix": "End branch '{branch}' with AllowEntry(): it reads between trades.",
        },
        "default": {
            "detail": "Branch '{branch}' does not end in an action, while the other branches of this rule stage do.",
            "fix": "End branch '{branch}' with Exit(), Reduce() or ScaleIn().",
        },
    },
    "TRADE_SCOPE_MIX": {
        "join": {
            "detail": "'{component}' combines a trade value with a between-trade value of the TradeManager at {origin}.",
            "fix": "Use trade values (TradeReturn(), BarsHeld(), ...) for Exit/Reduce/ScaleIn rules and between-trade values (BarsSinceExit(), ...) for AllowEntry() rules, never both in one rule.",
        },
        "trade_action": {
            "detail": "'{component}' acts during a trade, but its mask reads between trades.",
            "fix": "Use AllowEntry() for a between-trade rule, or read a trade value (TradeReturn(), BarsHeld(), ...).",
        },
        "allow_entry": {
            "detail": "'{component}' gates new entries between trades, but its mask reads an open trade.",
            "fix": "Use a between-trade reader (BarsSinceExit(), LastTradeReturn(), ...) under AllowEntry(), or Exit()/Reduce() for a trade rule.",
        },
        "trade_op": {
            "detail": "'{component}' aggregates over an open trade, but its input reads between trades.",
            "fix": "Feed '{component}' a trade value or a market value.",
        },
    },
    "POSITION_NEEDS_READER": {
        "default": {
            "detail": "'{component}' received the Position itself.",
            "fix": "Start the rule with a reader (TradeReturn(), BarsHeld(), ...) or with Load('<slot>').",
        },
        "store": {
            "detail": "Store('{slot}') received the Position itself.",
            "fix": "Store a reader's value (for example TradeReturn()), not the Position.",
        },
    },
    "POSITION_NEEDS_EXPOSURE": {
        "consumer": {
            "detail": "'{component}' received the Position from the TradeManager at {origin}, which is never turned into exposure.",
            "fix": "End the rules with Exposure() (then the sizer), or size the Position with RiskSizer(...).",
        },
        "branch": {
            "detail": "Branch '{branch}' starts the TradeManager at {origin} and ends without turning its Position into exposure.",
            "fix": "End branch '{branch}' with Exposure().",
        },
        "new_head": {
            "detail": "A second TradeManager starts while the Position from the TradeManager at {origin} is still open.",
            "fix": "End the first TradeManager's rules with Exposure() before starting another.",
        },
        "terminal": {
            "detail": "The Position from the TradeManager at {origin} is never turned into exposure.",
            "fix": "End it with Exposure() (then a sizer), or size it with RiskSizer(...).",
        },
    },
    "POSITION_FEEDBACK": {
        "default": {
            "detail": "'{component}' acts on the realised exposure of its own TradeManager (at {origin}).",
            "fix": "A rule cannot read the exposure it decides; read the trade with a reader (TradeReturn(), UnitsHeld(), ...) instead.",
        },
    },
    "POSITION_STATE_NOT_UPSTREAM": {
        "reduce": {
            "detail": "'{component}' reads what was sold, but no earlier stage of this Position has a Reduce() rule.",
            "fix": "Put '{component}' in a later stage, below the Parallel that holds the Reduce() rule.",
        },
        "add": {
            "detail": "'{component}' reads what was added, but no earlier stage of this Position has a ScaleIn() rule.",
            "fix": "Put '{component}' in a later stage, below the Parallel that holds the ScaleIn() rule.",
        },
        "leg": {
            "detail": "'{component}' names rule '{leg}', which no earlier stage of this Position has.",
            "fix": "Name a rule of an earlier stage, or move '{component}' below it.",
        },
    },
    "READER_WINDOW_INVALID": {
        "token": {
            "detail": "'{component}' window {window} is not a timeframe token.",
            # The clock tokens come from the alphabet's owner; '1w' is the
            # readers' calendar-week window (D-24), not a clock token.
            "fix": (
                "Write the window as a count and a unit: "
                + ", ".join(f"'{token_for_minutes(m)}'" for m in (30, 240, 1440))
                + " or '1w'."
            ),
        },
        "multiple": {
            "detail": "'{component}' window '{window}' is not a whole number of {clock} bars.",
            "fix": "Use a window that is a whole multiple of the strategy's {clock} timeframe.",
        },
        "calendar": {
            "detail": "'{component}' window '{window}' cannot use anchor='calendar'; calendar windows are {allowed}.",
            "fix": "Use anchor='rolling' for a '{window}' window, or a calendar window ({allowed}).",
        },
        "no_clock": {
            "detail": "'{component}' window '{window}' needs the strategy's timeframe, and no Globals(target_timeframe=...) is declared.",
            "fix": "Declare Globals(target_timeframe=...) above the Pipeline.",
        },
    },
    "WINDOW_EXCEEDS_LIVE_LOOKBACK": {
        "default": {
            "detail": "'{component}' window '{window}' is at or above the live look-back ({lookback_days} days); live cannot see that history.",
            "fix": "Use a window under {lookback_days} days.",
        },
    },
    "TRADE_OP_NOT_TRADE_SAFE": {
        "uncertified": {
            "detail": "'{component}' v{version} is not certified to run on trade values.",
            "fix": "Compute it on the market before the TradeManager, then read it in the rule with Load('<slot>').",
        },
        "domain": {
            "detail": "'{component}' v{version} is certified on trade values only with {narrowing}.",
            "fix": "Set {narrowing}, or compute it on the market before the TradeManager and Load it.",
        },
        "upgrade": {
            "detail": "'{component}' v{version} is not certified to run on trade values; v{newer} is.",
            "fix": "Upgrade the pin of '{component}' to v{newer}.",
        },
    },
    "SIZER_ERASES_SIZE": {
        "upgrade": {
            "detail": "'{component}' v{version} sizes by sign, so the partial rule '{rule}' would do nothing.",
            "fix": "Use '{component}' v{newer}, which keeps partial and added units.",
        },
        "default": {
            "detail": "'{component}' v{version} sizes by sign, so the partial rule '{rule}' would do nothing.",
            "fix": "Use a sizer that keeps size (FixedWeightSizer), or size the Position with RiskSizer(...).",
        },
    },
    "TRADE_RULE_WARMUP": {
        "default": {
            "detail": "Rule '{rule}' cannot fire: its window needs {warmup} bars after entry, and rule '{hold_rule}' closes every trade by bar {hold}.",
            "fix": "Shorten the window of rule '{rule}', or lengthen rule '{hold_rule}'.",
        },
    },
    "REENTRY_ANY_BAR_NO_GATE": {
        "default": {
            "detail": "The TradeManager at {origin} has reentry='any_bar' and no AllowEntry() rule, so it re-enters on any bar its entry signal is on.",
            "fix": "Add an AllowEntry() rule (for example Cooldown(bars=...)), or drop reentry='any_bar' to wait for the entry signal to reset.",
        },
    },
    "RETURN_THRESHOLD_SCALE": {
        "default": {
            "detail": "'{component}' compares a fraction ({reader}: 0.05 is 5%) with {param}={value}, which is {percent}%.",
            "fix": "For {value}% write {param}={fraction}.",
        },
    },
    "MARKET_EXPANDING_IN_POSITION": {
        "default": {
            "detail": "'{component}' on a market value counts from the start of the data, not from the entry.",
            "fix": "Use SinceEntry() for 'since entry'.",
        },
    },
    "MAX_UNITS_STATED": {
        "default": {
            "detail": "The TradeManager at {origin} can hold up to {units} units per trade: exposure up to {units}x the sizer's weight per position.",
            "fix": "Set TradeManager(max_units=...) to cap it, or keep it if the size is intended.",
        },
    },
    "SCALE_IN_NO_ROOM": {
        "default": {
            "detail": "ScaleIn rule '{rule}' can never add: the TradeManager at {origin} caps a trade at max_units={max_units}.",
            "fix": "Raise max_units to at least {needed}, or remove it (the default allows every add).",
        },
    },
}

#: The codes the runtime lift raises (spec 01-R50). Every other binding code
#: is static only (warnings, info, window checks).
RUNTIME_CODES = frozenset(
    {
        "POSITION_ACTION_NEEDS_MASK",
        "POSITION_REQUIRED",
        "POSITION_LINEAGE_MISMATCH",
        "TRADE_SERIES_LEAK",
        "POSITION_BRANCH_MIXED",
        "TRADE_SCOPE_MIX",
        "POSITION_NEEDS_READER",
        "POSITION_NEEDS_EXPOSURE",
        "POSITION_FEEDBACK",
        "POSITION_STATE_NOT_UPSTREAM",
        "TRADE_OP_NOT_TRADE_SAFE",
        "SIZER_ERASES_SIZE",
    }
)

#: Every code this module can produce.
BINDING_CODES = frozenset(SHAPES)


@dataclass(frozen=True)
class Finding:
    """One binding verdict: a catalog code, a message shape and its params."""

    code: str
    shape: str
    params: Mapping[str, Any]
    row: str

    def detail(self) -> str:
        return SHAPES[self.code][self.shape]["detail"].format(**self.params)

    def fix(self) -> str:
        return SHAPES[self.code][self.shape]["fix"].format(**self.params)

    @property
    def runtime(self) -> bool:
        return self.code in RUNTIME_CODES and not _ROW[self.row].get("static_only", False)


def _finding(row: str, shape: str, **params: Any) -> Finding:
    code = _ROW[row]["on_fail"]["code"]
    if shape not in SHAPES[code]:  # pragma: no cover - programming error, loud
        raise KeyError(f"{code} has no shape {shape!r}")
    return Finding(code, shape, dict(params), row)


class PositionBindingError(ValueError):
    """The runtime twin of a binding finding (spec 01-R50) — THE one class.

    Raised by spec 01's lift with the same code the static walk emits, in
    either form: ``PositionBindingError(finding)`` (the message and fix
    rendered from the same ``SHAPES`` table) or ``PositionBindingError(code,
    message, suggestion=...)`` (an engine-side refusal with its own words).
    ``positions.errors`` re-exports this class (lane L1's handoff), so an
    ``except`` on either name catches every raise. ``.code`` names the rule;
    ``.envelope`` is the runtime-tier issue shape a worker forwards.
    """

    def __init__(
        self,
        finding_or_code: "Finding | str",
        message: str | None = None,
        *,
        suggestion: str | None = None,
    ):
        if isinstance(finding_or_code, Finding):
            finding = finding_or_code
            code = finding.code
            message = finding.detail()
            suggestion = suggestion if suggestion is not None else finding.fix()
            text = f"{code}: {message} {suggestion}"
        else:
            finding = None
            code = finding_or_code
            if message is None:
                raise TypeError("PositionBindingError(code, message) needs a message")
            text = f"{code}: {message}"
        if code not in RUNTIME_CODES:
            raise ValueError(f"unknown position binding code {code!r}")
        super().__init__(text)
        self.finding = finding
        self.code = code
        self.envelope: dict[str, Any] = {
            "tier": "runtime",
            "code": code,
            "severity": "error",
            "message": message,
            "suggestion": suggestion,
            "recoverable": True,
        }


def raise_first_runtime(findings: Iterable[Finding]) -> None:
    """Raise :class:`PositionBindingError` for the first runtime finding."""
    for f in findings:
        if f.runtime:
            raise PositionBindingError(f)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT DECLARATIONS (the machine's view of a registered signature)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ComponentDecl:
    """What the scope machine needs to know about one step.

    Built from a registered signature by :func:`decl_from_signature` (static
    walk and runtime lift alike). ``newer_trade_safe`` / ``newer_preserves_size``
    are the newest registered versions that declare the property, for the
    "upgrade the pin" message shapes.
    """

    name: str
    version: int = 1
    category: str = "signal_transform"
    binding: Mapping[str, Any] | None = None
    trade_safe: Mapping[str, Any] | None = None
    preserves_size: bool = False
    causal: bool = True
    consumes_input: bool = True
    population_scoped: bool = False
    newer_trade_safe: int | None = None
    newer_preserves_size: int | None = None

    @property
    def role(self) -> str | None:
        return None if self.binding is None else self.binding["role"]


def decl_from_signature(sig: Any, versions: Mapping[int, Any] | None = None) -> ComponentDecl:
    """Adapt a ``ComponentSignature`` (and its sibling versions) to a decl."""
    newer_ts = newer_ps = None
    for v, other in sorted((versions or {}).items()):
        if v == sig.version:
            continue
        if getattr(other, "trade_safe", None) is not None:
            newer_ts = v if v > sig.version else newer_ts
        if getattr(other, "preserves_size", False):
            newer_ps = v if v > sig.version else newer_ps
    category = getattr(sig.category, "value", sig.category)
    return ComponentDecl(
        name=sig.name,
        version=sig.version,
        category=category,
        binding=getattr(sig, "binding", None),
        trade_safe=getattr(sig, "trade_safe", None),
        preserves_size=bool(getattr(sig, "preserves_size", False)),
        causal=bool(getattr(sig, "causal", True)),
        consumes_input=getattr(sig, "input_type", None) is not type(None),
        population_scoped=getattr(sig, "population_scope", None) is not None,
        newer_trade_safe=newer_ts,
        newer_preserves_size=newer_ps,
    )


def trade_safe_verdict(decl: ComponentDecl, params: Mapping[str, Any]) -> Finding | None:
    """03-R39: the fail-closed verdict over (name, pinned version, params).

    A missing declaration, a pinned version without one, or a value outside
    the declared narrowing is refused. Membership is never by name alone.
    """
    ts = decl.trade_safe
    if ts is None:
        if decl.newer_trade_safe is not None:
            return _finding(
                "J-BIND.trade-safe",
                "upgrade",
                component=decl.name,
                version=decl.version,
                newer=decl.newer_trade_safe,
            )
        return _finding(
            "J-BIND.trade-safe", "uncertified", component=decl.name, version=decl.version
        )
    for p, spec in (ts.get("params") or {}).items():
        if not _narrowing_holds(params.get(p), spec):
            return _finding(
                "J-BIND.trade-safe",
                "domain",
                component=decl.name,
                version=decl.version,
                narrowing=_narrowing_text(p, spec),
            )
    return None


def effective_max_units(declared: Any, scale_ins: Iterable[Mapping[str, Any]]) -> float:
    """01-R14 / D-39.3: ``max_units`` if set, else 1 + Σ units × times.

    The ONE function: spec 01's engine imports it, so the maximum the
    validator states and the engine's cap cannot drift.
    """
    if declared is not None:
        return float(declared)
    total = 1.0
    for r in scale_ins:
        total += float(r.get("units", 1.0)) * int(r.get("times", 1))
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# THE SCOPE MACHINE (03-R9 … R19)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class _Frame:
    owned: str | None = None
    inherited: str | None = None
    #: the scope's current Position as seen in this frame (the action anchor)
    pos: Facet | None = None
    #: branch names entered while a scope was open (the rule name, D7)
    rule_path: tuple[str, ...] = ()
    branch: str | None = None
    #: the step that closed the last scope of this frame (for the sizer shape)
    closed_by: str | None = None

    @property
    def scope(self) -> str | None:
        return self.owned or self.inherited


@dataclass
class _Rule:
    name: str
    kind: str
    warmup: int
    hold: int | None
    params: Mapping[str, Any]


@dataclass
class _Plan:
    lineage: str
    origin: str
    reentry_any_bar: bool
    max_units: Any
    rules: list[_Rule] = field(default_factory=list)
    closed: bool = False


@dataclass(frozen=True)
class StepOutcome:
    """The machine's answer at one step: the output facet and the findings."""

    value: Value
    findings: list[Finding]

    @property
    def fired(self) -> bool:
        """True when a binding row fired here (suppresses base TYPE_MISMATCH)."""
        return bool(self.findings)


class ScopeMachine:
    """One run's scope state: frames, the plans of every lineage, the verdicts.

    ``kappa_minutes`` is the strategy's execution clock in minutes (``None``
    when no ``Globals(target_timeframe=)`` is declared); only the static
    window checks read it. A fresh machine per run (per eval tick, spec 02 I-9).
    """

    def __init__(self, *, kappa_minutes: int | None = None, kappa_token: str | None = None):
        self.frames: list[_Frame] = [_Frame()]
        self.plans: dict[str, _Plan] = {}
        self.kappa_minutes = kappa_minutes
        self.kappa_token = kappa_token

    # ── frames ────────────────────────────────────────────────────────────────

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    @property
    def scope(self) -> str | None:
        """The effective scope: the frame's ``owned`` if set, else ``inherited``."""
        return self.frame.scope

    def _origin(self, lineage: str | None) -> str:
        if lineage == BOTTOM or lineage is None:
            return "(unknown)"
        plan = self.plans.get(lineage)
        return plan.origin if plan else lineage

    def enter_branch(self, name: str) -> None:
        """Push an isolated Parallel-branch frame (03-R9)."""
        parent = self.frame
        scope = parent.scope
        self.frames.append(
            _Frame(
                inherited=scope,
                pos=parent.pos,
                rule_path=(parent.rule_path + (name,)) if scope else (),
                branch=name,
            )
        )

    def exit_branch(self, value: Value) -> StepOutcome:
        """Pop a branch frame; a branch ending with its own scope open fires.

        Recovery (03-R18): the branch's Position of that lineage leaves as
        ``realized(L, A)``, so the consumer after the Parallel does not fire a
        second ``POSITION_NEEDS_EXPOSURE`` for the same mistake.
        """
        fr = self.frames.pop()
        if fr.owned is None:
            return StepOutcome(value, [])
        finding = _finding(
            "J-BIND.branch-open", "branch", branch=fr.branch, origin=self._origin(fr.owned)
        )
        if isinstance(value, Facet) and value.kind == "position" and value.lineage == fr.owned:
            value = Facet("realized", value.lineage, value.anchor, origin=value.origin)
        return StepOutcome(value, [finding])

    def merge_parallel(self, branches: Mapping[str, Value]) -> StepOutcome:
        """03-R17: a Parallel's value inside an open scope (or a plain record)."""
        scope = self.scope
        if scope is not None:
            positional = {
                n: v
                for n, v in branches.items()
                if isinstance(v, Facet) and v.kind == "position" and same_lineage(v.lineage, scope)
            }
            if positional:
                anchor: frozenset = frozenset()
                for v in positional.values():
                    anchor |= v.anchor
                merged = Facet("position", scope, anchor, origin=self._origin(scope))
                findings = []
                for n, v in branches.items():
                    if n in positional:
                        continue
                    shape = (
                        "between_trades" if isinstance(v, Facet) and v.kind == "flat" else "default"
                    )
                    findings.append(_finding("J-BIND.branch-mixed", shape, branch=n))
                self.frame.pos = merged
                return StepOutcome(merged, findings)
        return StepOutcome(dict(branches), [])

    # ── slots ─────────────────────────────────────────────────────────────────

    def store(self, slot: str, value: Value) -> list[Finding]:
        """03-R16: Store records the facet; a Position itself is refused."""
        for _, f in _fields(value):
            if f.kind == "position":
                return [_finding("J-BIND.store-position", "store", slot=slot)]
        return []

    def load(self, slot: str, facet: Value) -> StepOutcome:
        """03-R16: a bound entry loads only within its own scope.

        Recovery (03-R18): a refused load's value carries L⊥ from here on, so
        the rule that uses it does not fire a second code for the same Load.
        """
        findings: list[Finding] = []
        for _, f in _fields(facet):
            if not f.bound or f.lineage == BOTTOM:
                continue
            scope = self.scope
            if scope is None:
                findings.append(
                    _finding(
                        "J-BIND.load-outside", "load", slot=slot, origin=self._origin(f.lineage)
                    )
                )
            elif not same_lineage(f.lineage, scope):
                findings.append(
                    _finding(
                        "J-BIND.load-lineage",
                        "default",
                        component=f"Load('{slot}')",
                        origin_a=self._origin(f.lineage),
                        origin_b=self._origin(scope),
                    )
                )
            break
        if findings:
            facet = _to_bottom(facet)
        return StepOutcome(facet, findings)

    # ── the pipeline's end ────────────────────────────────────────────────────

    def finish(self, final: Value) -> list[Finding]:
        """01-R10 / 03-R27: a bound series, or a scope left open, at the end.

        A bound series at the end is ``TRADE_SERIES_LEAK`` (it names the more
        specific mistake); otherwise an open scope or a final Position is
        ``POSITION_NEEDS_EXPOSURE``. A recovery value fires nothing.
        """
        fields = _fields(final)
        if any(f.lineage == BOTTOM for _, f in fields):
            return []
        for _, f in fields:
            if f.kind in ("trade", "flat"):
                return [
                    _finding("J-BIND.terminal-leak", "terminal", origin=self._origin(f.lineage))
                ]
        root = self.frames[0]
        if root.owned is not None:
            return [_finding("J-BIND.terminal-open", "terminal", origin=self._origin(root.owned))]
        for _, f in fields:
            if f.kind == "position":
                return [
                    _finding("J-BIND.terminal-open", "terminal", origin=self._origin(f.lineage))
                ]
        return []

    # ── one step ──────────────────────────────────────────────────────────────

    def step(
        self,
        decl: ComponentDecl,
        current: Value,
        *,
        path: str,
        params: Mapping[str, Any] | None = None,
        slots: Mapping[str, Facet] | None = None,
        location: str | None = None,
        domain: frozenset | None = None,
        producer: str | None = None,
    ) -> StepOutcome:
        """Judge one component step and return its output facet.

        ``slots`` maps a parameter name to the facet of the σ entry it reads;
        ``domain`` is the static (or, at run time, observed) value set of a
        series input (``None`` = unknown / ⊤); ``producer`` names the step
        that produced ``current`` (for the action-mask message).
        """
        params = params or {}
        slots = slots or {}
        role = decl.role
        if role == "head":
            return self._head(decl, current, path, params, slots, location)
        if role in ("reader", "trade_op", "action", "realizer", "position_sizer"):
            scope = self.scope
            if scope is None:
                return self._no_scope(decl, role)
            if role == "reader":
                return self._reader(decl, current, scope, params, slots)
            if role == "trade_op":
                return self._trade_op(decl, current, scope, params)
            if role == "action":
                return self._action(decl, current, scope, path, params, domain, producer)
            return self._realize(decl, current, scope, slots, path)
        return self._ordinary(decl, current, params, slots)

    def _no_scope(self, decl: ComponentDecl, role: str) -> StepOutcome:
        closed_by = self.frame.closed_by
        if closed_by is not None:
            f = _finding(
                "J-BIND.scope-required", "after_close", component=decl.name, closed_by=closed_by
            )
        else:
            f = _finding("J-BIND.scope-required", "default", component=decl.name)
        if role == "action":
            out: Facet = Facet("position", BOTTOM)
        elif role in ("realizer", "position_sizer"):
            out = Facet("realized", BOTTOM)
        else:
            scale = (decl.binding or {}).get("scale")
            out = Facet((decl.binding or {}).get("scope", "trade"), BOTTOM, scale=scale)
        return StepOutcome(out, [f])

    def _slot_leaks(
        self, decl: ComponentDecl, slots: Mapping[str, Facet], keys: Iterable[str], row: str
    ) -> list[Finding]:
        out = []
        for p in keys:
            f = slots.get(p)
            if f is not None and f.bound:
                out.append(
                    _finding(
                        row, "slot", component=decl.name, param=p, origin=self._origin(f.lineage)
                    )
                )
        return out

    def _head(self, decl, current, path, params, slots, location) -> StepOutcome:
        b = decl.binding
        findings: list[Finding] = []
        fr = self.frame
        if fr.owned is not None:
            findings.append(_finding("J-BIND.head-open", "new_head", origin=self._origin(fr.owned)))
        findings += self._slot_leaks(
            decl, slots, list(b["entry_slots"]) + list(b["market_slots"]), "J-BIND.head-slot"
        )
        lineage = path
        origin = location or path
        reentry = params.get(b["reentry"]["param"])
        self.plans[lineage] = _Plan(
            lineage=lineage,
            origin=origin,
            reentry_any_bar=reentry == b["reentry"]["drops_reset_value"],
            max_units=params.get(b["max_units_param"]),
        )
        out = Facet("position", lineage, frozenset(), origin=origin)
        fr.owned = lineage
        fr.pos = out
        fr.rule_path = ()
        return StepOutcome(out, findings)

    def _reader(self, decl, current, scope, params, slots) -> StepOutcome:
        b = decl.binding
        findings: list[Finding] = []
        anchor: frozenset = self.frame.pos.anchor if self.frame.pos is not None else frozenset()
        if isinstance(current, Facet) and current.kind == "position":
            if not same_lineage(current.lineage, scope):
                findings.append(
                    _finding(
                        "J-BIND.reader-lineage",
                        "default",
                        component=decl.name,
                        origin_a=self._origin(current.lineage),
                        origin_b=self._origin(scope),
                    )
                )
            anchor = current.anchor
        if not findings and "needs_upstream" in b:
            nu = b["needs_upstream"]
            kinds = {k for _, k in anchor}
            if nu == "reduce" and "reduce" not in kinds:
                findings.append(_finding("J-BIND.reader-upstream", "reduce", component=decl.name))
            elif nu == "add" and "scale_in" not in kinds:
                findings.append(_finding("J-BIND.reader-upstream", "add", component=decl.name))
            elif nu == "leg":
                leg = params.get(b["leg_param"])
                if leg not in {r for r, _ in anchor}:
                    findings.append(
                        _finding("J-BIND.reader-upstream", "leg", component=decl.name, leg=leg)
                    )
        if "window" in b:
            findings += self._window(decl, b["window"], params)
        findings += self._slot_leaks(decl, slots, b.get("market_slots", ()), "J-BIND.market-slot")
        out = Facet(
            b["scope"],
            scope,
            scale=b["scale"],
            warmup=int(b.get("warmup", 0)),
            origin=self._origin(scope),
            reader=decl.name,
        )
        return StepOutcome(out, findings)

    def _window(self, decl, w, params) -> list[Finding]:
        token = params.get(w["param"])
        if token is None:
            return []
        minutes = window_minutes(token)
        if minutes is None:
            return [
                _finding("J-BIND.reader-window", "token", component=decl.name, window=repr(token))
            ]
        if minutes >= LIVE_LOOKBACK_DAYS * 1440:
            return [
                _finding(
                    "J-BIND.reader-lookback",
                    "default",
                    component=decl.name,
                    window=token,
                    lookback_days=LIVE_LOOKBACK_DAYS,
                )
            ]
        anchor = params.get(w["anchor_param"], "calendar")
        if anchor == "calendar" and token not in w["calendar_windows"]:
            return [
                _finding(
                    "J-BIND.reader-window",
                    "calendar",
                    component=decl.name,
                    window=token,
                    allowed=", ".join(repr(c) for c in w["calendar_windows"]),
                )
            ]
        if self.kappa_minutes is None:
            return [_finding("J-BIND.reader-window", "no_clock", component=decl.name, window=token)]
        if minutes % self.kappa_minutes:
            return [
                _finding(
                    "J-BIND.reader-window",
                    "multiple",
                    component=decl.name,
                    window=token,
                    clock=self.kappa_token or f"{self.kappa_minutes}min",
                )
            ]
        return []

    def _trade_op(self, decl, current, scope, params) -> StepOutcome:
        findings: list[Finding] = []
        warm = int(decl.binding.get("warmup", 0))
        scale = reader = None
        if isinstance(current, Facet):
            if current.kind == "position":
                findings.append(
                    _finding("J-BIND.ordinary-position", "default", component=decl.name)
                )
            elif current.kind == "flat":
                findings.append(
                    _finding(
                        "J-BIND.trade-op-scope",
                        "trade_op",
                        component=decl.name,
                        origin=self._origin(scope),
                    )
                )
                return StepOutcome(current, findings)  # recovery: keep the input's facet
            elif current.kind == "trade":
                if not same_lineage(current.lineage, scope):
                    findings.append(
                        _finding(
                            "J-BIND.trade-op-lineage",
                            "default",
                            component=decl.name,
                            origin_a=self._origin(current.lineage),
                            origin_b=self._origin(scope),
                        )
                    )
                warm += current.warmup
                if params.get("agg", "max") != "sum":
                    scale, reader = current.scale, current.reader
        out = Facet(
            "trade", scope, scale=scale, warmup=warm, origin=self._origin(scope), reader=reader
        )
        return StepOutcome(out, findings)

    def _action(self, decl, current, scope, path, params, domain, producer) -> StepOutcome:
        kind = decl.binding["kind"]
        fr = self.frame
        base_pos = fr.pos if fr.pos is not None else Facet("position", scope)
        findings: list[Finding] = []
        warm, hold = 0, None
        if isinstance(current, dict) or (isinstance(current, Facet) and current.kind == "position"):
            what = "a record of branches" if isinstance(current, dict) else "the Position itself"
            findings.append(
                _finding("J-BIND.action-mask", "not_series", action=decl.name, received=what)
            )
        else:
            f = current
            if domain is not None and not set(domain) <= {0.0, 1.0}:
                shape = "directional" if -1.0 in domain else "not_mask"
                findings.append(
                    _finding(
                        "J-BIND.action-mask",
                        shape,
                        action=decl.name,
                        producer=producer or "the previous step",
                    )
                )
            elif f.kind in ("trade", "flat") and not same_lineage(f.lineage, scope):
                findings.append(
                    _finding(
                        "J-BIND.action-lineage",
                        "default",
                        component=decl.name,
                        origin_a=self._origin(f.lineage),
                        origin_b=self._origin(scope),
                    )
                )
            elif f.kind == "realized" and same_lineage(f.lineage, scope):
                findings.append(
                    _finding(
                        "J-BIND.action-feedback",
                        "default",
                        component=decl.name,
                        origin=self._origin(scope),
                    )
                )
            elif kind in ("exit", "reduce", "scale_in") and f.kind == "flat":
                findings.append(
                    _finding("J-BIND.action-scope", "trade_action", component=decl.name)
                )
            elif kind == "allow_entry" and f.kind == "trade":
                findings.append(_finding("J-BIND.action-scope", "allow_entry", component=decl.name))
            if f.kind in ("trade", "flat"):
                warm, hold = f.warmup, f.hold
        rule = ".".join(fr.rule_path) if fr.rule_path else path
        out = replace(
            base_pos,
            kind="position",
            lineage=base_pos.lineage or scope,
            anchor=base_pos.anchor | {(rule, kind)},
        )
        fr.pos = out
        plan = self.plans.get(scope)
        if plan is not None and not findings:
            plan.rules.append(
                _Rule(rule, kind, warm, hold if kind == "exit" else None, dict(params))
            )
        return StepOutcome(out, findings)

    def _realize(self, decl, current, scope, slots, path) -> StepOutcome:
        fr = self.frame
        findings: list[Finding] = []
        if decl.role == "position_sizer":
            findings += self._slot_leaks(
                decl, slots, decl.binding.get("market_slots", ()), "J-BIND.market-slot"
            )
        pos = current if isinstance(current, Facet) and current.kind == "position" else fr.pos
        lineage = pos.lineage if pos is not None and pos.lineage else scope
        anchor = pos.anchor if pos is not None else frozenset()
        out = Facet("realized", lineage, anchor, origin=self._origin(lineage))
        if fr.owned is not None and same_lineage(lineage, fr.owned):
            closing = fr.owned
            fr.owned = None
            fr.pos = None
            fr.rule_path = ()
            fr.closed_by = decl.name
            findings += self._close(closing)
        return StepOutcome(out, findings)

    # ── plan checks at close (03-R19) ─────────────────────────────────────────

    def _close(self, lineage: str) -> list[Finding]:
        plan = self.plans.get(lineage)
        if plan is None or plan.closed:
            return []
        plan.closed = True
        findings: list[Finding] = []
        if plan.reentry_any_bar and not any(r.kind == "allow_entry" for r in plan.rules):
            findings.append(_finding("J-PLAN.reentry", "default", origin=plan.origin))
        holds = [(r.hold, r.name) for r in plan.rules if r.kind == "exit" and r.hold is not None]
        if holds:
            hold, hold_rule = min(holds)
            for r in plan.rules:
                if r.name == hold_rule or r.kind == "allow_entry":
                    continue
                if r.warmup >= hold:
                    findings.append(
                        _finding(
                            "J-PLAN.warmup",
                            "default",
                            rule=r.name,
                            warmup=r.warmup,
                            hold_rule=hold_rule,
                            hold=hold,
                        )
                    )
        scale_ins = [
            {"units": r.params.get("units", 1.0), "times": r.params.get("times", 1)}
            for r in plan.rules
            if r.kind == "scale_in"
        ]
        units = effective_max_units(plan.max_units, scale_ins)
        has_reduce = any(r.kind == "reduce" for r in plan.rules)
        if plan.max_units is not None and scale_ins and not has_reduce:
            for r, si in zip([r for r in plan.rules if r.kind == "scale_in"], scale_ins):
                needed = 1.0 + float(si["units"])
                if float(plan.max_units) < needed:
                    findings.append(
                        _finding(
                            "J-PLAN.scale-in-room",
                            "default",
                            rule=r.name,
                            origin=plan.origin,
                            max_units=_num(plan.max_units),
                            needed=_num(needed),
                        )
                    )
        if units > 1.0:
            findings.append(
                _finding("J-PLAN.max-units", "default", origin=plan.origin, units=_num(units))
            )
        return findings

    # ── ordinary components (03-R15) ──────────────────────────────────────────

    def _ordinary(self, decl, current, params, slots) -> StepOutcome:
        findings: list[Finding] = []
        inputs: list[tuple[str | None, Facet]] = []
        if decl.consumes_input:
            inputs += _fields(current)
        inputs += [(None, f) for f in slots.values()]
        # 1. A Position reaching an ordinary step.
        for branch, f in inputs:
            if f.kind == "position" and f.lineage == BOTTOM:
                # 03-R18: a recovery Position (after a refused action) fires
                # nothing more; the step's value stays a recovery value.
                kind = "trade" if decl.category in RULE_CATEGORIES else "realized"
                return StepOutcome(Facet(kind, BOTTOM), findings)
            if f.kind == "position":
                if decl.category in RULE_CATEGORIES:
                    findings.append(
                        _finding("J-BIND.ordinary-position", "default", component=decl.name)
                    )
                    return StepOutcome(
                        Facet("trade", BOTTOM, origin=self._origin(f.lineage)), findings
                    )
                findings.append(
                    _finding(
                        "J-BIND.ordinary-unrealized",
                        "consumer",
                        component=decl.name,
                        origin=self._origin(f.lineage),
                    )
                )
                # Recovery (03-R18): the consumer is taken as the realisation,
                # so the open scope does not fire again at the terminal.
                fr = self.frame
                if fr.owned is not None and same_lineage(f.lineage, fr.owned):
                    if fr.owned in self.plans:
                        self.plans[fr.owned].closed = True
                    fr.owned, fr.pos, fr.rule_path, fr.closed_by = None, None, (), decl.name
                return StepOutcome(
                    Facet("realized", f.lineage, f.anchor, origin=self._origin(f.lineage)), findings
                )
        bound = [f for _, f in inputs if f.kind in ("trade", "flat")]
        realized = [f for _, f in inputs if f.kind == "realized"]
        recovered = [f for f in bound + realized if f.lineage == BOTTOM]
        if recovered:  # 03-R18: a recovery value never fires a second code
            return StepOutcome(recovered[0], findings)
        # Sizers on a realised exposure with a size action (03-R32 / 01-R30).
        if decl.category == "position_sizer" and not decl.preserves_size and decl.consumes_input:
            cur = current if isinstance(current, Facet) else None
            if cur is not None and cur.kind == "realized":
                partial = sorted(r for r, k in cur.anchor if k in ("reduce", "scale_in"))
                if partial:
                    shape = "upgrade" if decl.newer_preserves_size else "default"
                    findings.append(
                        _finding(
                            "J-BIND.sizer-erases",
                            shape,
                            component=decl.name,
                            version=decl.version,
                            rule=partial[0],
                            newer=decl.newer_preserves_size,
                        )
                    )
        if bound:
            first = bound[0]
            kinds = {f.kind for f in bound}
            if kinds == {"trade", "flat"}:
                findings.append(
                    _finding(
                        "J-BIND.join-scope",
                        "join",
                        component=decl.name,
                        origin=self._origin(first.lineage),
                    )
                )
                return StepOutcome(_to_bottom(first), findings)  # 03-R18 single fire
            lineages = [f.lineage for f in bound]
            for other in lineages[1:]:
                if not same_lineage(other, lineages[0]):
                    findings.append(
                        _finding(
                            "J-BIND.join-lineage",
                            "default",
                            component=decl.name,
                            origin_a=self._origin(lineages[0]),
                            origin_b=self._origin(other),
                        )
                    )
                    return StepOutcome(_to_bottom(first), findings)  # 03-R18 single fire
            verdict = trade_safe_verdict(decl, params)
            if verdict is not None:
                findings.append(verdict)
                return StepOutcome(_to_bottom(first), findings)  # 03-R18 single fire
            ts = decl.trade_safe or {"warmup": 0}
            own = eval_warmup(ts["warmup"], params) or 0
            warm = max(f.warmup for f in bound) + own
            scales = {f.scale for f in bound}
            keep = ts.get("scale") == "keep" and len(scales) == 1
            scale = next(iter(scales)) if keep else None
            readers = sorted({f.reader for f in bound if f.reader and f.scale == "fraction"})
            hold = None
            comp = ts.get("compares")
            if comp is not None:
                in_scales = {f.scale for f in bound}
                value = params.get(comp["param"])
                op = comp["op"]
                if comp.get("inclusive_param") and params.get(comp["inclusive_param"]):
                    op = {"gt": "ge", "lt": "le"}.get(op, op)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    if "fraction" in in_scales and abs(value) >= 1:
                        findings.append(
                            _finding(
                                "J-BIND.threshold-scale",
                                "default",
                                component=decl.name,
                                reader=readers[0] if readers else "a trade return",
                                param=comp["param"],
                                value=_num(value),
                                percent=_num(value * 100),
                                fraction=_num(value / 100),
                            )
                        )
                    if in_scales == {"bars"} and op in ("ge", "gt"):
                        hold = math.ceil(value) if op == "ge" else math.floor(value) + 1
            out = Facet(
                first.kind,
                first.lineage,
                scale=scale,
                warmup=warm,
                hold=hold,
                origin=first.origin,
                reader=first.reader if keep else None,
            )
            return StepOutcome(out, findings)
        if realized:
            return StepOutcome(realized[0], findings)
        if self.scope is not None and not decl.causal and not decl.population_scoped and inputs:
            findings.append(_finding("J-BIND.market-expanding", "default", component=decl.name))
        return StepOutcome(MARKET, findings)


def _num(v: float) -> str:
    """Render a number the way an author writes it (no trailing ``.0``)."""
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(round(f, 10))


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY TEMPLATES (03-R45 … R49): validated at registration, executed by
# both validators and the resolver.
# ═══════════════════════════════════════════════════════════════════════════════


class FactoryExpansionError(ValueError):
    """A template could not be expanded at the given arguments.

    ``code`` names the catalog code the call site reports
    (``READER_WINDOW_INVALID`` for a ``$bars_of`` that is not exact).
    """

    def __init__(self, code: str, finding: Finding | None, message: str):
        self.code = code
        self.finding = finding
        super().__init__(message)


def validate_factory_expansion(cls_name: str, template: Any, parameters: Iterable[str]) -> list:
    """Shape-validate a ``factory_expansion`` template (03-R46)."""
    params = set(parameters)

    def value(v: Any) -> Any:
        if isinstance(v, dict):
            _need(
                len(v) == 1 and next(iter(v)) in TEMPLATE_OPS,
                cls_name,
                f"factory_expansion value op must be one of {list(TEMPLATE_OPS)}, got {v!r}",
            )
            op, arg = next(iter(v.items()))
            if op == "$scope":
                _need(
                    arg == "prices", cls_name, "factory_expansion '$scope' only resolves 'prices'"
                )
            else:
                _need(
                    arg in params,
                    cls_name,
                    f"factory_expansion {op} names {arg!r}, not a parameter",
                )
            return {op: arg}
        _need(
            v is None or isinstance(v, (bool, int, float, str)),
            cls_name,
            f"factory_expansion literal must be JSON-scalar, got {v!r}",
        )
        return v

    def steps(seq: Any) -> list:
        _need(
            isinstance(seq, (list, tuple)) and seq,
            cls_name,
            "factory_expansion must be a non-empty list of steps",
        )
        out = []
        for st in seq:
            _need(
                isinstance(st, dict), cls_name, f"factory_expansion step must be a dict, got {st!r}"
            )
            if "$when" in st:
                _need(
                    set(st) == {"$when", "set", "unset"},
                    cls_name,
                    "a '$when' step is {'$when', 'set', 'unset'}",
                )
                _need(
                    st["$when"] in params,
                    cls_name,
                    f"'$when' names {st['$when']!r}, not a parameter",
                )
                out.append(
                    {"$when": st["$when"], "set": steps(st["set"]), "unset": steps(st["unset"])}
                )
            elif "parallel" in st:
                _need(
                    set(st) == {"parallel"},
                    cls_name,
                    "a parallel step is {'parallel': {branch: [steps]}}",
                )
                br = st["parallel"]
                _need(isinstance(br, dict) and br, cls_name, "a parallel step needs named branches")
                out.append({"parallel": {n: steps(b) for n, b in br.items()}})
            elif "load" in st:
                _need(set(st) == {"load"}, cls_name, "a load step is {'load': <slot or op>}")
                out.append({"load": value(st["load"])})
            else:
                _need(
                    set(st) <= {"component", "params"} and isinstance(st.get("component"), str),
                    cls_name,
                    "a component step is {'component': <Name>, 'params': {...}}",
                )
                ps = st.get("params", {})
                _need(isinstance(ps, dict), cls_name, "component step params must be a dict")
                out.append(
                    {"component": st["component"], "params": {k: value(v) for k, v in ps.items()}}
                )
        return out

    return steps(template)


def template_components(template: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every component name a template can expand to (sorted, unique)."""
    names: set[str] = set()

    def walk(seq):
        for st in seq:
            if "component" in st:
                names.add(st["component"])
            elif "parallel" in st:
                for b in st["parallel"].values():
                    walk(b)
            elif "$when" in st:
                walk(st["set"])
                walk(st["unset"])

    walk(template)
    return sorted(names)


def expand_factory(
    name: str,
    template: Iterable[Mapping[str, Any]],
    args: Mapping[str, Any],
    *,
    kappa_minutes: int | None = None,
    kappa_token: str | None = None,
    prices_slot: str | None = None,
) -> list[dict]:
    """Execute a template at the call's arguments (03-R46).

    Returns plain steps: ``{"component", "params"}``, ``{"load": slot}`` or
    ``{"parallel": {branch: [steps]}}``. A parameter whose resolved value is
    ``None`` is omitted from the expanded step (it takes its default).
    """

    def value(v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        op, arg = next(iter(v.items()))
        if op == "$arg":
            return args.get(arg)
        if op == "$neg":
            a = args.get(arg)
            return None if a is None else -a
        if op == "$scope":
            if prices_slot is None:
                # No governing TradeManager: the call is a rule outside any
                # scope, exactly POSITION_REQUIRED's default shape.
                f = _finding("J-BIND.scope-required", "default", component=name)
                raise FactoryExpansionError(f.code, f, f.detail())
            return prices_slot
        token = args.get(arg)
        if token is None:
            return None
        minutes = window_minutes(token)
        if minutes is None:
            f = _finding("J-BIND.reader-window", "token", component=name, window=repr(token))
        elif kappa_minutes is None:
            f = _finding("J-BIND.reader-window", "no_clock", component=name, window=token)
        elif minutes >= LIVE_LOOKBACK_DAYS * 1440:
            f = _finding(
                "J-BIND.reader-lookback",
                "default",
                component=name,
                window=token,
                lookback_days=LIVE_LOOKBACK_DAYS,
            )
        elif minutes % kappa_minutes:
            f = _finding(
                "J-BIND.reader-window",
                "multiple",
                component=name,
                window=token,
                clock=kappa_token or f"{kappa_minutes}min",
            )
        else:
            return float(minutes // kappa_minutes)
        raise FactoryExpansionError(f.code, f, f.detail())

    def steps(seq) -> list[dict]:
        out: list[dict] = []
        for st in seq:
            if "$when" in st:
                out.extend(steps(st["set"] if args.get(st["$when"]) is not None else st["unset"]))
            elif "parallel" in st:
                out.append({"parallel": {n: steps(b) for n, b in st["parallel"].items()}})
            elif "load" in st:
                out.append({"load": value(st["load"])})
            else:
                ps = {k: value(v) for k, v in st.get("params", {}).items()}
                out.append(
                    {
                        "component": st["component"],
                        "params": {k: v for k, v in ps.items() if v is not None},
                    }
                )
        return out

    return steps(template)


def canonical_factory_name(name: str, args: Mapping[str, Any]) -> str:
    """03-R48: the nested pipeline's canonical name, ``"<Name>(<k>=<v>, …)"``.

    A function of the argument VALUES: an integral float renders as an int
    (``atr=2.0`` and ``atr=2`` are one call, ``TrailingStop(atr=2, period=14)``),
    because a graph carried as JSON loses the float/int spelling and the TS
    twin (``binding.ts`` ``canonicalFactoryName``) cannot recover it. Before
    this, the two engines named the same call differently and the trace pins
    diverged (Q-2448 integration).
    """
    import json

    def _value(v: Any) -> Any:
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, list):
            return [_value(x) for x in v]
        if isinstance(v, dict):
            return {k: _value(x) for k, x in v.items()}
        return v

    inner = ", ".join(
        f"{k}={json.dumps(_value(v), sort_keys=True, separators=(',', ':'))}"
        for k, v in sorted(args.items())
        if v is not None
    )
    return f"{name}({inner})"


# ═══════════════════════════════════════════════════════════════════════════════
# THE GENERATED BLOCK (judgment_tables.json → binding)
# ═══════════════════════════════════════════════════════════════════════════════


def binding_table() -> dict:
    """The ``binding`` block of ``judgment_tables.json`` (03-R18, AC-5).

    Everything the TS engine needs to execute the same rules as this module,
    as data: the closed vocabularies, the rule categories, the rows in check
    order and every message shape. The schema is documented in the lane's
    evidence for the TS lane (L2b).
    """
    return {
        "vocabulary": {
            "roles": list(ROLES),
            "role_sub_categories": {
                k: (dict(v) if isinstance(v, dict) else v) for k, v in ROLE_SUB_CATEGORIES.items()
            },
            "scopes": list(SCOPES),
            "scales": list(SCALES),
            "action_kinds": list(ACTION_KINDS),
            "needs_upstream": list(NEEDS_UPSTREAM),
            "facet_kinds": list(FACET_KINDS),
            "compare_ops": list(COMPARE_OPS),
            "warmup_ops": list(WARMUP_OPS),
            "narrowing_keys": list(NARROWING_KEYS),
            "template_ops": list(TEMPLATE_OPS),
            "template_step_kinds": list(TEMPLATE_STEP_KINDS),
        },
        "rule_categories": list(RULE_CATEGORIES),
        "position_base": POSITION_BASE,
        "realized_base": REALIZED_BASE,
        "bottom_lineage": BOTTOM,
        "live_lookback_days": LIVE_LOOKBACK_DAYS,
        "window_token_pattern": _WINDOW_RE.pattern,
        "window_unit_minutes": dict(_UNIT_MINUTES),
        "runtime_codes": sorted(RUNTIME_CODES),
        "rows": [dict(r) for r in BINDING_ROWS],
        "shapes": {
            c: {s: dict(t) for s, t in sorted(v.items())} for c, v in sorted(SHAPES.items())
        },
    }


__all__ = [
    "ACTION_KINDS",
    "BINDING_CODES",
    "BINDING_ROWS",
    "BOTTOM",
    "COMPARE_OPS",
    "ComponentDecl",
    "FACET_KINDS",
    "Facet",
    "FactoryExpansionError",
    "Finding",
    "LIVE_LOOKBACK_DAYS",
    "POSITION_BASE",
    "REALIZED_BASE",
    "MARKET",
    "NEEDS_UPSTREAM",
    "PositionBindingError",
    "POSITION_LAYER_SUB_CATEGORIES",
    "ROLES",
    "ROLE_SUB_CATEGORIES",
    "RULE_CATEGORIES",
    "RUNTIME_CODES",
    "SCALES",
    "SCOPES",
    "SHAPES",
    "ScopeMachine",
    "StepOutcome",
    "binding_table",
    "canonical_factory_name",
    "decl_from_signature",
    "effective_max_units",
    "eval_warmup",
    "expand_factory",
    "raise_first_runtime",
    "same_lineage",
    "template_components",
    "trade_safe_verdict",
    "validate_binding",
    "validate_factory_expansion",
    "validate_trade_safe",
    "window_minutes",
]
