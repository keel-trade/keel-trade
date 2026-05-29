"""`keel_components_search` — discover pipeline components.

Per spec §4 (lines 280-281): collapses
`strategy_components_search` + `strategy_components_after` +
`strategy_components_before` + `strategy_components_dump` and the
`keel components list/search/after/before` CLI verbs into one
outcome.

For 0.3.0 we still read from the bundled `keel/data/registry.json`
because Phase 2A ships bundled component data. Phase 2C migrates
to a lazy `GET /v1/components` endpoint; this handler is already
structured `try API → fallback bundled` so the migration is a
local swap.

Do NOT use this tool to fetch the full param schema for ONE
component — call `keel_components_compose_help` instead.
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


def _format_entry(comp: dict) -> dict:
    """Shape one registry record into the search-result entry."""
    entry: dict[str, Any] = {
        "name": comp.get("name"),
        "category": comp.get("category"),
        "description": (comp.get("description") or "").strip().split("\n\n")[0].strip()[:200],
        "input_type": comp.get("input_type", "Any"),
        "output_type": comp.get("output_type", "Any"),
    }
    if comp.get("sub_category"):
        entry["sub_category"] = comp["sub_category"]
    return entry


def _search_bundled(args: dict) -> list[dict]:
    """Fallback path: read from the bundled registry.

    The bundled `search_components` already handles keyword / category /
    type filters. `after` / `before` are layered on top here because
    they require traversing the type graph.
    """
    from keel.data.registry import (
        get_components_after,
        get_components_before,
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

    # Type-flow scoping first — narrows the candidate pool, then we
    # apply the rest of the filters (keyword, category, input/output)
    # in-Python so the agent can combine them naturally.
    candidates: list[dict] | None = None
    if after_name:
        try:
            candidates = get_components_after(after_name)
        except KeyError as e:
            raise KeelError(
                str(e),
                error_code="not_found",
                exit_code=3,
                suggestion="Pass a valid component name (see `keel_components_search`).",
            ) from None
    elif before_name:
        try:
            candidates = get_components_before(before_name)
        except KeyError as e:
            raise KeelError(
                str(e),
                error_code="not_found",
                exit_code=3,
                suggestion="Pass a valid component name (see `keel_components_search`).",
            ) from None

    if candidates is not None:
        # Apply the remaining filters in-place; `search_components` only
        # operates over the full bundled list so we filter manually here.
        results = candidates
        keyword = args.get("keyword")
        category = args.get("category")
        input_type = args.get("input_type")
        output_type = args.get("output_type")
        query = args.get("query")

        if category:
            cat = category.lower()
            results = [c for c in results if (c.get("category") or "").lower() == cat]
        if input_type:
            results = [c for c in results if c.get("input_type") == input_type]
        if output_type:
            results = [c for c in results if c.get("output_type") == output_type]
        if keyword:
            kw = keyword.lower()
            results = [
                c
                for c in results
                if kw in (c.get("name") or "").lower()
                or kw in (c.get("description") or "").lower()
            ]
        if query:
            import re

            q_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
            scored = []
            for c in results:
                name_tokens = set(re.findall(r"[a-z0-9]+", (c.get("name") or "").lower()))
                desc_tokens = set(re.findall(r"[a-z0-9]+", (c.get("description") or "").lower()))
                cat_tokens = set(re.findall(r"[a-z0-9]+", (c.get("category") or "").lower()))
                score = (
                    len(q_tokens & name_tokens) * 3.0
                    + len(q_tokens & cat_tokens) * 2.0
                    + len(q_tokens & desc_tokens) * 1.0
                )
                if score > 0:
                    scored.append((score, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            results = [c for _, c in scored]

        return [_format_entry(c) for c in results[:limit]]

    # No after/before — delegate the heavy lifting to the bundled helper.
    kwargs: dict[str, Any] = {"top_k": limit}
    for key in ("keyword", "category", "input_type", "output_type", "query"):
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

    Phase 2C is expected to extend the API endpoint to support the full
    filter set; at that point this guard can be relaxed.

    Also returns None on any client-side or transport failure so
    unauthenticated / offline callers keep working unchanged.
    """
    # Server-side support is `category`-only today. Defer to bundled
    # for anything richer.
    unsupported_filters = {"keyword", "query", "input_type", "output_type", "after", "before"}
    if any(args.get(k) for k in unsupported_filters):
        return None

    try:
        client = ctx.get_client()
    except Exception:  # noqa: BLE001
        return None

    params: dict[str, Any] = {}
    if args.get("category"):
        params["category"] = args["category"]
    if args.get("limit"):
        params["limit"] = int(args["limit"])

    try:
        # `KeelClient.get` splats kwargs into the httpx params dict —
        # passing `params=params` would send a single literal `params`
        # query string. Unpack.
        resp = client.get("/v1/components", **params)
    except Exception:
        return None

    # API contract: either {"results": [...]} or a bare list. Normalize
    # to a list of compact entries.
    if isinstance(resp, dict) and "results" in resp:
        return [_format_entry(c) for c in resp["results"]]
    if isinstance(resp, list):
        return [_format_entry(c) for c in resp]
    return None


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    limit = int(args.get("limit") or 20)

    # Future-proof structure: try API first, fall back to bundled.
    results = _search_via_api(ctx, args)
    if results is None:
        results = _search_bundled(args)

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
        description=(
            "Search the Keel pipeline component catalog by keyword, "
            "semantic query, category, input/output type, or position in "
            "the pipeline (`after`/`before`). Returns compact entries "
            "(name, category, description, input/output type) for the "
            "agent to triage. "
            "Do NOT use to fetch full param schemas of one component — "
            "use `keel_components_compose_help`. "
            "Do NOT use to enumerate strategies — call `keel_strategy_search`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Case-insensitive substring match against name or "
                        "description. The CLI positional arg maps here — "
                        "`keel components search momentum` filters to "
                        "components mentioning 'momentum'. For weighted "
                        "token-scoring across name/category/description, "
                        "use `--query` instead."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Free-text semantic query — tokens are matched against "
                        "name, category, and description with weighted scoring "
                        "(name ×3, category ×2, description ×1). Returns "
                        "components scored > 0 ranked by relevance. Pair with "
                        "`keyword` (or the CLI positional keyword) to first "
                        "narrow by substring, then rank."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": list(_CATEGORIES),
                    "description": "Restrict to one component category.",
                },
                "input_type": {
                    "type": "string",
                    "description": "Restrict to components consuming this type (e.g. `SignalSeries`).",
                },
                "output_type": {
                    "type": "string",
                    "description": "Restrict to components producing this type (e.g. `ForecastSeries`).",
                },
                "after": {
                    "type": "string",
                    "description": (
                        "Return components that can FOLLOW the named component "
                        "(their input type accepts that component's output)."
                    ),
                },
                "before": {
                    "type": "string",
                    "description": (
                        "Return components that can PRECEDE the named component "
                        "(their output type matches that component's input)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of results.",
                },
            },
            "required": [],
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
