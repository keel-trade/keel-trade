"""`keel_components_get` — full schema for one component.

Per spec §4 (lines 280-281): collapses `strategy_component_detail` +
the per-component slice of `dsl_reference` / `strategy_examples` /
`composition_patterns` into one tool the agent calls once it knows
which component it wants to wire up.

The data source is the bundled `keel/data/registry.json`. The planned
`GET /v1/components/{name}` endpoint never shipped; the handler still
tries the API first and falls back to bundled, so bundled data is the
path in practice.

Do NOT use to discover components — use `keel_components_search`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._param_range import RANGE_NOTE, param_range
from ._surface_hints import usage_hint


def _extract_examples(detail: dict) -> list[str]:
    """Pull example snippets out of a component's description blob.

    The bundled registry stores descriptions as multi-paragraph
    markdown-ish text. Each component typically has an `Example:`
    block of one or two pipeline snippets. We extract those so the
    agent doesn't have to scan the whole description.
    """
    desc = (detail.get("description") or "").strip()
    if not desc:
        return []

    examples: list[str] = []
    in_block = False
    buf: list[str] = []
    for line in desc.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("example"):
            if buf:
                examples.append("\n".join(buf).strip())
                buf = []
            in_block = True
            continue
        if in_block:
            if not stripped and buf:
                examples.append("\n".join(buf).strip())
                buf = []
                in_block = False
                continue
            if stripped:
                buf.append(stripped)
    if buf:
        examples.append("\n".join(buf).strip())

    return [e for e in examples if e][:2]


def _extract_pitfalls(detail: dict) -> list[str]:
    """Surface "pitfall" / "warning" / "note" lines from the description."""
    desc = (detail.get("description") or "").strip()
    if not desc:
        return []

    pitfalls: list[str] = []
    for line in desc.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith(("warning:", "note:", "pitfall:", "caution:")):
            pitfalls.append(stripped)
    return pitfalls[:5]


#: Plain-words legality condition per transfer direction
#: (dsl-multi-timeframe-clocks spec 01 §5.1/§5.2, surfaced per spec 02 §9.2
#: item 6). Sourced from the declared `clock_transfer`, NEVER regex-mined
#: from the docstring.
_CLOCK_LEGALITY: dict[str, str] = {
    "synth": (
        "Mints the entry clock — everything downstream inherits it until "
        "another clock-changing component re-clocks the branch."
    ),
    "resample": (
        "Legal only fine → coarse: the target period must be a whole "
        "multiple of the input period. Aggregating 1h bars to 1d is fine; "
        "asking for 5min out of 1h is an upsample and is rejected."
    ),
    "project": (
        "Legal only coarse → fine: the input period must be a whole "
        "multiple of the target period. Holds the last COMPLETED coarse "
        "bar across the finer grid, so no future information leaks back."
    ),
    "keep": "Leaves the bar clock untouched — output is on the input's clock.",
}

#: The one thing every clock-changing component must say it does NOT do
#: (R7-I13). Without it the predictable agent error is "I moved the branch
#: to 4h, so the clock system will adjust my lookback".
_CLOCK_NEVER = (
    "Never rescales parameters: an integer lookback is a count of BARS on "
    "whatever clock the branch is on (`ROC(period=6)` is 6 hours at 1h and "
    "24 hours at 4h). Re-clocking a branch does not re-interpret it — "
    "restate the window yourself."
)


def _clock_block(detail: dict) -> dict | None:
    """The Clock block (spec 02 §9.2 item 6), or None for `keep` components.

    Every field is read from the registration surface: `clock_transfer`
    gives the direction and the parameter that names the target clock;
    `declaration_refs` says whether that parameter is wired to `Globals`
    (so it tracks the declaration) or must be passed explicitly.
    """
    from keel.data.registry import clock_direction_of, clock_transfer_of

    direction = clock_direction_of(detail)
    if direction == "keep":
        return None

    transfer = clock_transfer_of(detail) or {}
    src = transfer.get("src")
    decl_refs = detail.get("declaration_refs") or {}
    optional_refs = detail.get("optional_declaration_refs") or {}

    if src and src in decl_refs:
        clock_source = (
            f"`{src}` is declaration-backed — bound to `{decl_refs[src]}`, "
            f"so it tracks the declaration and cannot drift."
        )
    elif src and src in optional_refs:
        clock_source = (
            f"`{src}` is explicit; it may optionally be wired to `{optional_refs[src]}` instead."
        )
    elif src:
        clock_source = f"`{src}` is explicit — you pass the target clock."
    else:
        clock_source = "The target clock is fixed by the component."

    block = {
        "direction": direction,
        "clock_source": clock_source,
        "legality": _CLOCK_LEGALITY[direction],
        "never": _CLOCK_NEVER,
    }
    if transfer.get("off"):
        block["bar_offset"] = (
            f"Grid phase comes from `{transfer['off']}` "
            f"(`{optional_refs.get(transfer['off'], 'globals.bar_offset')}`)."
        )
    return block


def _shape_detail(detail: dict) -> dict:
    """Project the bundled registry record into the outcome shape."""
    clock = _clock_block(detail)
    if clock is not None:
        return {**_shape_detail_base(detail), "clock": clock}
    return _shape_detail_base(detail)


def _with_ranges(parameters: list) -> tuple[list, bool]:
    """Each parameter plus its labelled ``range`` (Q-2242), copied — the
    registry cache's dicts are never mutated. Returns whether any carried one."""
    out: list = []
    any_range = False
    for p in parameters:
        if isinstance(p, dict):
            rng = param_range(p.get("constraints"))
            if rng is not None:
                p = {**p, "range": rng}
                any_range = True
        out.append(p)
    return out, any_range


def _shape_detail_base(detail: dict) -> dict:
    """The clock-independent part of the outcome shape."""
    parameters, any_range = _with_ranges(detail.get("parameters") or [])
    shaped = {
        "name": detail.get("name"),
        "category": detail.get("category"),
        "sub_category": detail.get("sub_category"),
        "description": (detail.get("description") or "").strip(),
        "input_type": detail.get("input_type"),
        "output_type": detail.get("output_type"),
        "parameters": parameters,
        "param_constraints": detail.get("param_constraints") or [],
        "usage_hint": detail.get("usage_hint"),
        "deterministic": detail.get("deterministic"),
        "version": detail.get("version"),
        "latest": detail.get("latest"),
        "status": detail.get("status"),
        "examples": _extract_examples(detail),
        "pitfalls": _extract_pitfalls(detail),
    }
    if any_range:
        shaped["range_note"] = RANGE_NOTE
    shaped.update(_position_block(detail))
    shaped.update(deprecation_record(detail))
    return shaped


#: The clock a factory's documented example is expanded on (a ``window=``
#: needs one); stated in the ``expands_to_note`` so the text is never read
#: as clock-independent.
_EXAMPLE_CLOCK = ("1h", 60)


def _position_block(detail: dict) -> dict:
    """The position-layer declarations (spec 03-R62), keep-by-omission.

    ``binding`` (the role), ``trade_safe`` (this latest version's
    certification), ``preserves_size``; for a registered factory its
    ``factory_expansion`` template and ``expands_to`` — the long form, as DSL
    text, of the documented example arguments (03-R51).
    """
    out: dict[str, Any] = {}
    for key in ("binding", "trade_safe", "preserves_size", "factory_expansion"):
        if detail.get(key) is not None and detail.get(key) is not False:
            out[key] = detail[key]
    template = detail.get("factory_expansion")
    if template:
        example = next(iter(_extract_examples(detail)), None)
        text = _expands_to(detail.get("name") or "", template, example)
        if text is not None:
            out["expands_to"] = text
            out["expands_to_note"] = (
                f"The long form of {example} on a {_EXAMPLE_CLOCK[0]} clock under a "
                "TradeManager(prices='ohlcv'): what validation, the canvas and the "
                "compiled strategy hold."
            )
    return out


def _expands_to(name: str, template: list, example: str | None) -> str | None:
    """Render a factory's expansion at its example's literal arguments."""
    import ast

    from pipeline_engine.binding import expand_factory

    if not example:
        return None
    try:
        call = ast.parse(example.strip(), mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call) or getattr(call.func, "id", None) != name:
        return None
    args = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    steps = expand_factory(
        name,
        template,
        args,
        kappa_minutes=_EXAMPLE_CLOCK[1],
        kappa_token=_EXAMPLE_CLOCK[0],
        prices_slot="ohlcv",
    )
    return _render_steps(steps)


def _render_steps(steps: list) -> str:
    parts: list[str] = []
    for st in steps:
        if "parallel" in st:
            inner = ", ".join(f"{k!r}: [{_render_steps(v)}]" for k, v in st["parallel"].items())
            parts.append("{" + inner + "}")
        elif "load" in st:
            parts.append(f"Load({st['load']!r})")
        else:
            kw = ", ".join(f"{k}={v!r}" for k, v in st["params"].items())
            parts.append(f"{st['component']}({kw})")
    return ", ".join(parts)


def deprecation_record(detail: dict) -> dict:
    """A deprecated component's full deprecation record (position-layer 04-R26).

    ``replacement`` (the successor's name), ``replacement_shape`` (``{head,
    text, recipe}`` — the agent-facing line and the recipe id; the DSL
    fragments stay in the bundled registry for the planner) and
    ``known_issue`` (``{id, summary}``). Empty for an active component and
    keep-by-omission for each key, so every other record is byte-identical.
    """
    if detail.get("status") != "deprecated":
        return {}
    out: dict = {}
    if detail.get("replacement"):
        out["replacement"] = detail["replacement"]
    shape = detail.get("replacement_shape")
    if isinstance(shape, dict) and shape.get("head"):
        out["replacement_shape"] = {
            k: shape.get(k) for k in ("head", "text", "recipe") if shape.get(k) is not None
        }
    issue = detail.get("known_issue")
    if isinstance(issue, dict) and issue.get("id"):
        out["known_issue"] = {"id": issue.get("id"), "summary": issue.get("summary")}
    return out


def _detail_via_api(ctx: ToolContext, name: str) -> dict | None:
    """Try `GET /v1/components/{name}`; return None on any failure.

    The endpoint exists (it needs `component.read`, which the anonymous tier
    has since Q-2494) and returns the server's CURRENT record; on None the
    bundled registry answers, which is this wheel's release snapshot and can
    trail the server's versions (Q-2500). Offline callers keep working.
    """
    try:
        client = ctx.get_client()
    except Exception:  # noqa: BLE001
        return None
    if not client.has_credentials:
        # Q-2494: a lookup the bundle answers never mints an anonymous
        # workspace (each mint spends the network's daily allowance).
        return None

    try:
        resp = client.get(f"/v1/components/{name}")
    except KeelError as exc:
        if exc.error_code == "rate_limited":
            raise  # Q-2494: a 429 is the caller's to see, never a silent fallback
        return None
    except Exception:  # noqa: BLE001 — component detail fetch best-effort → None on failure
        return None

    if isinstance(resp, dict) and resp.get("name"):
        return resp
    return None


def _detail_bundled(name: str) -> dict:
    """Read one component from the bundled registry. Raises on miss."""
    from keel.data.registry import get_component_detail, get_components_dump
    from pipeline_engine.dsl.component_names import (
        render_component_name_suggestion,
        suggest_component_names,
    )

    try:
        return get_component_detail(name)
    except KeyError as e:
        # The one hint owner (Q-2449): the real name for a case slip, a
        # synonym or a spelled-out abbreviation, the recipe for a pattern
        # name the docs teach (`EWMAC`), else an honest no-match.
        names = [c["name"] for c in get_components_dump()]
        hint = render_component_name_suggestion(name, suggest_component_names(name, names))
        raise NotFoundError(
            f"Component {name!r} not found in registry.",
            suggestion=f"{hint} Run `keel_components_search` to list available components.",
        ) from e


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    name = (args.get("name") or "").strip()
    if not name:
        raise KeelError(
            "Missing required `name` argument.",
            error_code="missing_name",
            exit_code=2,
            suggestion=usage_hint(
                "Pass a component name, e.g. `keel components compose-help RSI`.",
                'Pass a component name, e.g. `name="RSI"`; `keel_components_search` finds names.',
            ),
        )

    detail = _detail_via_api(ctx, name)
    if detail is None:
        detail = _detail_bundled(name)

    shaped: dict[str, Any] = _shape_detail(detail)

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/components/{name}",
        share_url=None,
        resource_uri=f"keel://components/{name}/schema",
        extra=shaped,
    )


COMPONENTS_COMPOSE_HELP = register(
    OutcomeTool(
        name="keel_components_get",
        required_action="component.read",
        cli_path=("components", "compose-help"),
        toolset="read-only",
        # grounded-in: system/chat/tool_usage.md:23 (single-component detail
        # is the call for a lone edit); system/chat/collaboration.md:77-79
        # (§6 — read the full param list incl. slot params before wiring a
        # component in).
        description=(
            "Fetch the full contract for ONE known pipeline component — several at once "
            "are `keel_components_get_many`. It carries the parameter list, type "
            "signature, slot reads and writes, examples and common pitfalls: the exact "
            "names, types and slots a `ComponentRef(...)` in the DSL passed to "
            "`keel_strategy_compose` has to match. `name` is case-sensitive. Discovering "
            "components is `keel_components_search`."
        ),
        input_schema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Component name (case-sensitive), e.g. `RSI`, `RollingZScoreTransform`."
                    ),
                    "x-cli-positional": True,
                },
            },
        },
        annotations={
            "title": "Get Component",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
