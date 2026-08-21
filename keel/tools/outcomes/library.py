"""`keel_library_*` — the verified Keel Library on the agent surface.

Three additive outcome tools wrapping the existing keel-api library
endpoints (spec 08): list published entries, read one entry's verified
facts, and fork an entry into the caller's org as a normal editable
strategy. The library is the product's set of verified, backtested,
immutable entries — forking one is the supported "start from something
proven" path (the same mechanic the web app's Library tab uses).

Guidance boundary (founder ruling 2026-08-21, Q-ledger entry for this
lane): the library is UNLOCKED here, not pushed. Exactly three
one-sentence touchpoints exist across the whole agent surface — the
fork tool's description below, one route line in `keel_status`
first_session, and the strategy-fork-and-iterate skill's tool list.
Nothing in build/compose guidance references the library; the
from-thesis path (`strategy-creation`) is unchanged. Do not add
library references to knowledge files without a new founder decision.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# keel-api's CreatedVia vocabulary (schemas/library.py, spec 05 B1.3) keyed
# by the SDK's self-declared surface (keel/surface.py). The endpoint
# defaults omitted values to "app", which would mislabel agent forks —
# so the handler always sends the mapped value explicitly.
_SURFACE_TO_CREATED_VIA: dict[str, str] = {
    "cli": "cli",
    "local-mcp": "mcp-local",
    "hosted-mcp": "mcp-remote",
    "sdk": "sdk",
    "web": "app",
    "chat": "chat",
}


def _created_via() -> str:
    from keel.surface import current_surface

    return _SURFACE_TO_CREATED_VIA.get(current_surface(), "sdk")


def _entry_row(entry: dict) -> dict[str, Any]:
    """The compact per-entry projection both list and get share."""
    return {
        "slug": entry.get("slug"),
        "name": entry.get("name"),
        "category": entry.get("category"),
        "risk_band": entry.get("risk_band"),
        "headline": entry.get("headline"),
        "default_variant_id": entry.get("default_variant_id"),
    }


def _list_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    client = ctx.get_client()
    try:
        result = client.get("/v1/library")
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to list library entries: {e}",
            suggestion="Check auth with `keel_status`; the library needs an authenticated session.",
        )

    entries = [_entry_row(e) for e in result.get("entries", [])]
    return OutcomeResult(
        hero_url=f"{ctx.app_url}/library",
        extra={
            "count": result.get("count", len(entries)),
            "data_as_of": result.get("data_as_of"),
            "entries": entries,
        },
    )


def _get_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    slug: str = (args.get("slug") or "").strip()
    if not slug:
        raise KeelError(
            "Missing required `slug`.",
            error_code="missing_slug",
            exit_code=2,
            suggestion="Pass the entry slug from `keel_library_list` (e.g. `ma-crossover-crypto`).",
        )

    client = ctx.get_client()
    try:
        result = client.get(f"/v1/library/{slug}")
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fetch library entry {slug!r}: {e}",
            suggestion="Verify the slug via `keel_library_list` — slugs are kebab-case.",
        )

    extra = _entry_row(result)
    extra.update(
        {
            "entry_version": result.get("entry_version"),
            "data_as_of": result.get("data_as_of"),
            "stale": result.get("stale"),
            "backtest_window": result.get("backtest_window"),
            "variants": result.get("variants"),
        }
    )
    return OutcomeResult(
        hero_url=f"{ctx.app_url}/library/{slug}",
        extra=extra,
    )


def _fork_handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    slug: str = (args.get("slug") or "").strip()
    if not slug:
        raise KeelError(
            "Missing required `slug`.",
            error_code="missing_slug",
            exit_code=2,
            suggestion=(
                "Pass the entry slug from `keel_library_list` "
                "(e.g. `ma-crossover-crypto`). To fork one of your own "
                "strategies by id instead, use `keel_strategy_fork`."
            ),
        )

    body: dict[str, Any] = {"created_via": _created_via()}
    if args.get("variant_id"):
        body["variant_id"] = args["variant_id"]
    if args.get("name"):
        body["name"] = args["name"]

    client = ctx.get_client()
    try:
        result = client.post(f"/v1/library/{slug}/fork", json=body)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to fork library entry {slug!r}: {e}",
            suggestion=(
                "Verify the slug via `keel_library_list`. If you passed a "
                "`variant_id`, check it against `keel_library_get`'s "
                "variants — some settings are not yet forkable and return "
                "`variant_not_forkable`."
            ),
        )

    new_sid = result.get("strategy_id")
    return OutcomeResult(
        run_id=new_sid,
        hero_url=f"{ctx.app_url}/strategies/{new_sid}" if new_sid else f"{ctx.app_url}/library",
        share_url=None,
        extra={
            "strategy_id": new_sid,
            "name": result.get("name"),
            "source_slug": result.get("source_slug"),
            "entry_version": result.get("entry_version"),
            "variant_id": result.get("variant_id"),
            "is_default_variant": result.get("is_default_variant"),
        },
    )


LIBRARY_LIST = register(
    OutcomeTool(
        name="keel_library_list",
        required_action="strategy.read",
        cli_path=("library", "list"),
        toolset="read-only",
        description=(
            "List the Keel Library — the verified, backtested strategy "
            "entries published in the product. Each row carries the slug, "
            "name, category, risk band, and headline metrics from the "
            "entry's verified run. Read an entry's full facts with "
            "`keel_library_get`; start from one with `keel_library_fork`."
        ),
        input_schema={"type": "object", "properties": {}},
        annotations={
            "title": "List Library Entries",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_list_handler,
    )
)


LIBRARY_GET = register(
    OutcomeTool(
        name="keel_library_get",
        required_action="strategy.read",
        cli_path=("library", "get"),
        toolset="read-only",
        description=(
            "Read one Keel Library entry's verified facts: headline "
            "metrics, backtest window, data freshness, and the published "
            "variants (parameter presets with their own metrics). Use this "
            "as the evidence source BEFORE forking — the numbers are the "
            "library's verified run, not a promise about any fork."
        ),
        input_schema={
            "type": "object",
            "required": ["slug"],
            "properties": {
                "slug": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Library entry slug (kebab-case, from `keel_library_list`).",
                },
            },
        },
        annotations={
            "title": "Get Library Entry",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_get_handler,
    )
)


LIBRARY_FORK = register(
    OutcomeTool(
        name="keel_library_fork",
        required_action="strategy.create",
        cli_path=("library", "fork"),
        toolset="backtest",
        # grounded-in: collaboration.md §4 (iterate on a copy — the smallest
        # change, one at a time) + spec 08 §5 (library fork provenance: the
        # forked strategy is a normal, editable strategy; entries immutable)
        # + q3-wedges 03 §4.1 C5 (fork-and-verify is the product's trust
        # mechanic — the entry's numbers are the library's run, the user's
        # evidence comes from their own re-run).
        description=(
            "Fork a verified Keel Library entry into the caller's org as a "
            "normal, editable strategy — the supported way to start from "
            "something proven instead of composing from scratch. For a "
            "first strategy, `ma-crossover-crypto` is the simple default "
            "new web users get. The fork is a full copy — edit it and run "
            "backtests like any other strategy. The entry's published "
            "metrics belong to "
            "the library's verified run — produce the fork's own evidence "
            "with `keel_backtest_run`. "
            "To fork one of YOUR strategies or a share link, use "
            "`keel_strategy_fork` (it takes ids, not slugs)."
        ),
        input_schema={
            "type": "object",
            "required": ["slug"],
            "properties": {
                "slug": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Library entry slug (kebab-case, from `keel_library_list`).",
                },
                "variant_id": {
                    "type": "string",
                    "description": (
                        "Optional variant preset from `keel_library_get`; "
                        "omit for the entry's default."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Optional name for the forked strategy.",
                },
            },
        },
        annotations={
            "title": "Fork Library Entry",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_fork_handler,
    )
)
