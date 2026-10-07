"""`keel_components_search` — discover pipeline components.

Per spec §4 (lines 280-281): collapses
`strategy_components_search` + `strategy_components_after` +
`strategy_components_before` + `strategy_components_dump` and the
`keel components list/search/after/before` CLI verbs into one
outcome.

The handler tries the server's `GET /v1/components` (it exists; it needs
`component.list`, which the anonymous tier has since Q-2494) and falls back
to the bundled `keel/data/registry.json` on failure. The bundle is the
snapshot this wheel shipped with, so it can trail the server's current
component versions (Q-2500); the server answer is the current one.

Do NOT use this tool to fetch the full param schema for ONE
component — call `keel_components_get` instead.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# Categories derived from the bundled registry. Keeping this static
# avoids importing the registry just to populate the MCP inputSchema
# enum — every host paying that cost on tool list would be wasteful.
# If a new category lands the schema is updated alongside the registry
# regeneration (see CLAUDE.md "Generated Artifacts").
_CATEGORIES: tuple[str, ...] = (
    "data_loader",
    "data_transform",
    "forecast_composer",
    "forecast_mapper",
    "indicator",
    "position_manager",
    "position_sizer",
    "regime_detector",
    "risk_manager",
    "signal_composer",
    "signal_transform",
    "slot_op",
    "universe_filter",
)


def _clock_direction_enum() -> tuple[str, ...]:
    """The `clock_direction` vocabulary, read from the one definition.

    Unlike `_CATEGORIES` above this is NOT restated here: importing
    `keel.data.registry` costs nothing at tool-list time (the module only
    loads JSON lazily), and a second copy of a four-word enum is a second
    thing to forget to update.
    """
    from keel.data.registry import CLOCK_DIRECTIONS

    return CLOCK_DIRECTIONS


def _format_entry(comp: dict) -> dict:
    """Shape one registry record into the search-result entry."""
    from keel.data.registry import clock_direction_of, position_search_fields

    entry: dict[str, Any] = {
        "name": comp.get("name"),
        "category": comp.get("category"),
        "description": (comp.get("description") or "").strip().split("\n\n")[0].strip()[:200],
        "input_type": comp.get("input_type", "Any"),
        "output_type": comp.get("output_type", "Any"),
    }
    if comp.get("sub_category"):
        entry["sub_category"] = comp["sub_category"]
    entry.update(position_search_fields(comp))
    from pipeline_engine.component_ranking import deprecation_fields

    entry.update(deprecation_fields(comp))
    direction = clock_direction_of(comp)
    if direction != "keep":
        entry["clock_direction"] = direction
    return entry


def _search_bundled(args: dict) -> list[dict]:
    """Fallback path: read from the bundled registry.

    The bundled `search_components` already handles keyword / category /
    type filters. `after` / `before` are layered on top here because
    they require traversing the type graph.
    """
    from keel.data.registry import (
        CLOCK_DIRECTIONS,
        clock_direction_of,
        get_components_after,
        get_components_before,
        keyword_matches,
        rank_components,
        search_components,
    )

    limit = int(args.get("limit") or 20)

    after_name = args.get("after")
    before_name = args.get("before")
    if after_name and before_name:
        raise KeelError(
            "`after` and `before` are mutually exclusive.",
            error_code="usage_error",
            exit_code=2,
            suggestion="Pass either `after` or `before`, not both.",
        )

    if args.get("clock_direction") and args["clock_direction"] not in CLOCK_DIRECTIONS:
        raise KeelError(
            f"Unknown clock_direction {args['clock_direction']!r}.",
            error_code="usage_error",
            exit_code=2,
            suggestion=f"Pass one of {list(CLOCK_DIRECTIONS)}.",
        )

    # Type-flow scoping first — narrows the candidate pool, then we
    # apply the rest of the filters (keyword, category, input/output)
    # in-Python so the agent can combine them naturally.
    candidates: list[dict] | None = None
    if after_name:
        try:
            candidates = get_components_after(after_name)
        except KeyError as e:
            # `str(KeyError)` is the repr of its argument — the message came
            # back wrapped in quotes (Q-2273); its first argument is the text.
            raise KeelError(
                str(e.args[0]) if e.args else "Component not found.",
                error_code="not_found",
                exit_code=3,
                suggestion="Pass a valid component name (see `keel_components_search`).",
            ) from None
    elif before_name:
        try:
            candidates = get_components_before(before_name)
        except KeyError as e:
            # `str(KeyError)` is the repr of its argument — the message came
            # back wrapped in quotes (Q-2273); its first argument is the text.
            raise KeelError(
                str(e.args[0]) if e.args else "Component not found.",
                error_code="not_found",
                exit_code=3,
                suggestion="Pass a valid component name (see `keel_components_search`).",
            ) from None

    include_deprecated = bool(args.get("include_deprecated", False))

    if candidates is not None:
        # Apply the remaining filters in-place; `search_components` only
        # operates over the full bundled list so we filter manually here.
        # Deprecation visibility first (spec 04-R28): the type-flow
        # neighbours of a component are search results like any other.
        from pipeline_engine.component_ranking import visible

        results = [c for c in candidates if visible(c, include_deprecated)]
        keyword = args.get("keyword")
        category = args.get("category")
        sub_category = args.get("sub_category")
        input_type = args.get("input_type")
        output_type = args.get("output_type")
        direction = args.get("clock_direction")
        query = args.get("query")

        if category:
            cat = category.lower()
            results = [c for c in results if (c.get("category") or "").lower() == cat]
        if sub_category:
            # spec 03-R63: case-insensitive exact match, as the bundled helper.
            sc = sub_category.lower()
            results = [c for c in results if (c.get("sub_category") or "").lower() == sc]
        if input_type:
            results = [c for c in results if c.get("input_type") == input_type]
        if output_type:
            results = [c for c in results if c.get("output_type") == output_type]
        if direction:
            results = [c for c in results if clock_direction_of(c) == direction]
        if keyword:
            results = keyword_matches(results, keyword)
        if query:
            # THE one scorer (spec 02 §9.2 item 3) — this overlay used to
            # carry a verbatim copy of the scoring block, which is exactly
            # how two ranking paths drift.
            results = rank_components(results, query)

        return [_format_entry(c) for c in results[:limit]]

    # No after/before — delegate the heavy lifting to the bundled helper.
    kwargs: dict[str, Any] = {"top_k": limit, "include_deprecated": include_deprecated}
    for key in (
        "keyword",
        "category",
        "sub_category",
        "input_type",
        "output_type",
        "clock_direction",
        "query",
    ):
        if args.get(key):
            kwargs[key] = args[key]

    results = search_components(**kwargs)
    # `search_components` already returns the compact format we need.
    return results


def _search_via_api(ctx: ToolContext, args: dict) -> list[dict] | None:
    """Try the keel-api endpoint; return None when filter support is
    insufficient so the caller falls back to the bundled path.

    keel-api's `GET /v1/components` (see the API components router
    components.py:165) currently only honors `category`. If the caller
    asked for any other filter — `keyword`, `query`, `input_type`,
    `output_type`, `after`, `before` — the API would silently return
    all 182 components, breaking the agent's search. So we return None
    here and let `_search_bundled` (which implements every filter
    correctly via `keel.data.registry.search_components`) handle it.

    The API endpoint was never extended to the full filter set, so this
    guard is the steady state rather than a temporary one.

    Also returns None on any client-side or transport failure so
    unauthenticated / offline callers keep working unchanged.
    """
    # Server-side support is `category`-only today. Defer to bundled
    # for anything richer.
    unsupported_filters = {
        "keyword",
        "query",
        "input_type",
        "output_type",
        "clock_direction",
        "sub_category",
        "after",
        "before",
    }
    if any(args.get(k) for k in unsupported_filters):
        return None

    try:
        client = ctx.get_client()
    except Exception:  # noqa: BLE001
        return None
    if not client.has_credentials:
        # Q-2494: a lookup the bundle answers never mints an anonymous
        # workspace (each mint spends the network's daily allowance).
        return None

    # `GET /v1/components` takes `category` only; `limit` is applied by
    # `_handler` to whichever source answered (Q-2270 — it used to ride as a
    # query param the route never declared, so the API path returned the
    # whole catalogue under a result saying `limit: 20`).
    params: dict[str, Any] = {}
    if args.get("category"):
        params["category"] = args["category"]

    try:
        # `KeelClient.get` splats kwargs into the httpx params dict —
        # passing `params=params` would send a single literal `params`
        # query string. Unpack.
        resp = client.get("/v1/components", **params)
    except KeelError as exc:
        if exc.error_code == "rate_limited":
            raise  # Q-2494: a 429 is the caller's to see, never a silent fallback
        return None
    except Exception:  # noqa: BLE001 — component search fetch best-effort → None on failure
        return None

    # API contract: either {"results": [...]} or a bare list. Normalize
    # to a list of compact entries. `GET /v1/components` returns EVERY
    # component (the library resolves blocks through it), so deprecation
    # visibility is applied here, client-side (spec 04-R28).
    from pipeline_engine.component_ranking import visible

    include_deprecated = bool(args.get("include_deprecated", False))
    if isinstance(resp, dict) and "results" in resp:
        rows = resp["results"]
    elif isinstance(resp, list):
        rows = resp
    else:
        return None
    return [
        _format_entry(c) for c in rows if isinstance(c, dict) and visible(c, include_deprecated)
    ]


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    limit = int(args.get("limit") or 20)

    # Future-proof structure: try API first, fall back to bundled.
    results = _search_via_api(ctx, args)
    if results is None:
        results = _search_bundled(args)
    # ONE place applies `limit`, whichever source answered: the bundled
    # search already stops at it, the API returns its whole list (Q-2270).
    results = results[:limit]

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/components",
        share_url=None,
        resource_uri="keel://components/catalog",
        extra={
            "results": results,
            "total": len(results),
            "limit": limit,
        },
    )


COMPONENTS_SEARCH = register(
    OutcomeTool(
        name="keel_components_search",
        required_action="component.list",
        cli_path=("components", "search"),
        toolset="read-only",
        # grounded-in: system/chat/tool_usage.md:21-25 (two-step discovery,
        # new + iterative); system/chat/collaboration.md:39-47 (plan from
        # real types/slots, not names or pattern memory — and :45, search a
        # named domain concept BEFORE selecting; a manual ConstantForecast
        # is not a beta hedge).
        description=(
            "Search the Keel pipeline component catalog — the chosen candidates go to "
            "`keel_components_get_many`, which verifies their types and slots. It "
            "matches `keyword` (narrows), a natural-language `query` (ranks), `category`, "
            "`input_type` / `output_type`, or pipeline position (`after` / `before`); "
            "`clock_direction` "
            "filters to one re-clocking direction (resample raw data fine → coarse, "
            "project signals coarse → fine). Domain concepts (beta hedge, regime) are "
            "components, found here. Returns compact entries (name, "
            "category, description, input/output type); one component's full contract is "
            "`keel_components_get`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Case-insensitive substring match against name or description, "
                        "e.g. `momentum` keeps the components mentioning it. `query` adds "
                        "weighted token scoring across name/category/description."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Free-text semantic query — tokens are matched against name, "
                        "category, and description with weighted scoring (name ×3, category "
                        "×2, description ×1); components scored > 0 return ranked by "
                        "relevance. Combined with `keyword` (or the CLI positional "
                        "keyword), the substring narrows first and the query ranks."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": list(_CATEGORIES),
                    "description": "Restrict to one component category.",
                },
                "sub_category": {
                    "type": "string",
                    "description": (
                        "Restrict to one sub-category, case-insensitive exact match "
                        "(e.g. `trade_reader`, `trade_action`, `trade_factory`)."
                    ),
                },
                "input_type": {
                    "type": "string",
                    "description": (
                        "Restrict to components consuming this type (e.g. `SignalSeries`)."
                    ),
                },
                "output_type": {
                    "type": "string",
                    "description": (
                        "Restrict to components producing this type (e.g. `ForecastSeries`)."
                    ),
                },
                "clock_direction": {
                    "type": "string",
                    "enum": list(_clock_direction_enum()),
                    "description": (
                        "Restrict to components that change the bar clock in one direction. "
                        "`resample` = fine → coarse aggregation of raw data (e.g. 1h OHLCV "
                        "→ 1d). `project` = coarse → fine, holding the last COMPLETED "
                        "coarse bar across the finer grid (e.g. a 1d regime signal driving "
                        "1h execution) — the only safe way to move a signal down. `synth` = "
                        "a data loader minting the entry clock. `keep` = leaves the clock "
                        "untouched (most components)."
                    ),
                },
                "after": {
                    "type": "string",
                    "description": (
                        "Return components that can FOLLOW the named component (their input "
                        "type accepts that component's output)."
                    ),
                },
                "before": {
                    "type": "string",
                    "description": (
                        "Return components that can PRECEDE the named component (their "
                        "output type matches that component's input)."
                    ),
                },
                "include_deprecated": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Also return deprecated components (hidden by default); each "
                        "carries `status` and `replacement_text`. A lookup by name "
                        "(`keel_components_get`) always answers."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "description": (
                        "Maximum number of results (default 20); `total` is the number returned."
                    ),
                },
            },
            "required": [],
        },
        annotations={
            "title": "Search Components",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
