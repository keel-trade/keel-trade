"""`keel_components_compose_help` — full schema for one component.

Per spec §4 (lines 280-281): collapses `strategy_component_detail` +
the per-component slice of `dsl_reference` / `strategy_examples` /
`composition_patterns` into one tool the agent calls once it knows
which component it wants to wire up.

For 0.3.0 the data source is the bundled `keel/data/registry.json`.
Phase 2C migrates to `GET /v1/components/{name}`; the handler tries
the API first and falls back to bundled so the migration is local.

Do NOT use to discover components — use `keel_components_search`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


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
    from keel.data.registry import clock_direction_of

    direction = clock_direction_of(detail)
    if direction == "keep":
        return None

    transfer = detail.get("clock_transfer") or {}
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


def _shape_detail_base(detail: dict) -> dict:
    """The clock-independent part of the outcome shape."""
    return {
        "name": detail.get("name"),
        "category": detail.get("category"),
        "sub_category": detail.get("sub_category"),
        "description": (detail.get("description") or "").strip(),
        "input_type": detail.get("input_type"),
        "output_type": detail.get("output_type"),
        "parameters": detail.get("parameters") or [],
        "param_constraints": detail.get("param_constraints") or [],
        "usage_hint": detail.get("usage_hint"),
        "deterministic": detail.get("deterministic"),
        "version": detail.get("version"),
        "latest": detail.get("latest"),
        "status": detail.get("status"),
        "examples": _extract_examples(detail),
        "pitfalls": _extract_pitfalls(detail),
    }


def _detail_via_api(ctx: ToolContext, name: str) -> dict | None:
    """Try `GET /v1/components/{name}`; return None on any failure.

    Phase 2C will land this endpoint. Until then we silently fall back
    so unauthenticated and offline callers keep working.
    """
    try:
        client = ctx.get_client()
    except Exception:  # noqa: BLE001
        return None

    try:
        resp = client.get(f"/v1/components/{name}")
    except Exception:  # noqa: BLE001 — component detail fetch best-effort → None on failure
        return None

    if isinstance(resp, dict) and resp.get("name"):
        return resp
    return None


def _detail_bundled(name: str) -> dict:
    """Read one component from the bundled registry. Raises on miss."""
    from keel.data.registry import get_component_detail

    try:
        return get_component_detail(name)
    except KeyError as e:
        raise NotFoundError(
            f"Component {name!r} not found in registry.",
            suggestion=(
                "Run `keel_components_search` to list available components. "
                "Component names are case-sensitive (e.g. `RSI`, not `rsi`)."
            ),
        ) from e


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    name = (args.get("name") or "").strip()
    if not name:
        raise KeelError(
            "Missing required `name` argument.",
            error_code="missing_name",
            exit_code=2,
            suggestion="Pass a component name, e.g. `keel components compose-help RSI`.",
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
        name="keel_components_compose_help",
        required_action="component.read",
        cli_path=("components", "compose-help"),
        toolset="read-only",
        # grounded-in: tool_usage.md:27 (single-component detail is the
        # right call for a lone edit); collaboration.md §7 (read the full
        # param list incl. slot params before wiring a component in).
        description=(
            "Fetch the full schema/detail contract for ONE known pipeline "
            "component: parameter list, type signature, slot reads/writes, "
            "examples, and common pitfalls. Call this once discovery has "
            "narrowed to a single component you're about to wire — use "
            "`keel_components_search` first to find candidates, and "
            "`keel_components_detail_batch` when you're verifying SEVERAL "
            "at once. The returned contract is the source of truth for "
            "authoring that component's `ComponentRef(...)`: read the exact "
            "parameter names, types, and slot reads/writes here so the DSL "
            "you pass to `keel_strategy_compose` fits on the first try, not "
            "after a dry-run bounce. "
            "Do NOT use to discover components — use `keel_components_search`. "
            "Do NOT use to look up DSL syntax topics — call `keel_help`."
        ),
        input_schema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Component name (case-sensitive), e.g. `RSI`, `RollingZScoreTransform`.",
                    "x-cli-positional": True,
                },
            },
        },
        annotations={
            "title": "Component Composition Help",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
