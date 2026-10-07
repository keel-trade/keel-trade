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
fork tool's description below, one route line in `keel_account_status`
first_session, and the strategy-fork-and-iterate skill's tool list.
Nothing in build/compose guidance references the library; the
from-thesis path (`strategy-creation`) is unchanged. Do not add
library references to knowledge files without a new founder decision.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import STRATEGY_NAME_FACT, OutcomeResult, OutcomeTool, ToolContext


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


def _headline(headline: Any) -> Any:
    """The entry's headline metrics at display precision (Q-1877): the
    library stores `"sharpe": 0.7554992060140537`, and `keel_library_list`'s
    JSON — its text block — handed that to the model verbatim. Rounded by
    the backtest view's one precision owner; the keys are unchanged."""
    from ._backtest_view import display_number

    if not isinstance(headline, dict):
        return headline
    return {key: display_number(key, value) for key, value in headline.items()}


def _entry_row(entry: dict) -> dict[str, Any]:
    """The compact per-entry projection both list and get share."""
    return {
        "slug": entry.get("slug"),
        "name": entry.get("name"),
        "category": entry.get("category"),
        "risk_band": entry.get("risk_band"),
        "headline": _headline(entry.get("headline")),
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
            suggestion="Check auth with `keel_account_status`; the library needs an authenticated session.",
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


def _served_variants(variants: Any) -> Any:
    """The entry's variants as this surface returns them: on the LISTED
    profile each is `_listed_projection.LISTED_LIBRARY_VARIANT_FIELDS`
    (Q-2268); the CLI keeps keel-api's rows whole."""
    from ._toolsets import is_listed_profile

    if not isinstance(variants, list) or not is_listed_profile():
        return variants
    from ._listed_projection import LISTED_LIBRARY_VARIANT_FIELDS, pick

    return [pick(v, LISTED_LIBRARY_VARIANT_FIELDS) for v in variants]


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
            "variants": _served_variants(result.get("variants")),
        }
    )
    # The entry's structure, drawn the way the editor draws it (PLAN
    # §4.2) — so "what IS this strategy" is answered before the fork,
    # not after it. The library payload carries the graph only when the
    # entry publishes one; absent, the envelope is unchanged.
    view = _library_view(result, hero_url=f"{ctx.app_url}/library/{slug}")
    if view is not None:
        extra["view"] = view
    facts = library_facts_line(result)
    if facts:
        # The text block's `facts:` line (Q-1843). With a view, a host's
        # text block is the view's markdown plus the operational lines —
        # and the markdown draws only the STRUCTURE, so the verified facts
        # this tool's description promises reached the model on no host
        # that hides `structuredContent` (claude.ai).
        extra["library_facts"] = facts
    # No hold line (founder, 2026-09-23, Q-1901): `GET /v1/library/{slug}`
    # is asked for the package only — keel-api reads BTC closes only under
    # `references=true`, which this tool never sends — and a `reference` an
    # older keel-api still sends unasked is not projected. The package stores
    # no benchmark figure of its own.
    return OutcomeResult(
        hero_url=f"{ctx.app_url}/library/{slug}",
        extra=extra,
    )


#: Variants named on the facts line, best Sharpe first, before "+N more".
MAX_FACT_VARIANTS = 3

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(value: Any) -> str | None:
    from datetime import date

    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _metrics_phrase(
    sharpe: Any,
    ret: Any,
    drawdown: Any,
    trips: Any = None,
    *,
    positions: Any = None,
    full: bool = True,
) -> str:
    """`Sharpe 0.76 · return +56.5% · max drawdown −39.7% · 1,143 trades · 512 positions`.

    The library stores percents in percent units and the drawdown as a
    positive magnitude; it is said once, negative, with the card system's
    minus (the backtest view's rule). The positions count rides beside the
    trades when the headline carries it (trade-metrics spec 01 §9 L2, L5)."""
    bits: list[str] = []
    s, r, d, t = _num(sharpe), _num(ret), _num(drawdown), _num(trips)
    p = _num(positions)
    if s is not None:
        bits.append(f"Sharpe {s:.2f}")
    if r is not None:
        bits.append(f"return {'+' if r >= 0 else '−'}{abs(r):.1f}%")
    if d is not None and full:
        bits.append(f"max drawdown −{abs(d):.1f}%")
    if full:
        from ._backtest_view import count_label

        if t is not None:
            bits.append(f"{int(t):,} {count_label()}")
        if p is not None:
            bits.append(f"{int(p):,} {count_label('positions')}")
    return " · ".join(bits)


def library_facts_line(entry: Any) -> str | None:
    """The entry's verified facts as ONE line for the model (Q-1843).

    Exactly what `keel_library_get`'s description promises — headline
    metrics, backtest window, data freshness, the published variants — read
    from the payload keel-api already returns (`routers/library.py`), never
    recomputed. None when the entry carries none of them.
    """
    if not isinstance(entry, dict):
        return None
    parts: list[str] = []
    window = entry.get("backtest_window")
    if isinstance(window, dict):
        start, end = _day(window.get("start")), _day(window.get("end"))
        if start and end:
            parts.append(f"verified run {start} – {end}")
    headline = entry.get("headline")
    if isinstance(headline, dict):
        phrase = _metrics_phrase(
            headline.get("sharpe"),
            headline.get("total_return_pct"),
            headline.get("max_drawdown_pct"),
            headline.get("trades"),
            positions=headline.get("positions"),
        )
        if phrase:
            parts.append(phrase)
    as_of = _day(entry.get("data_as_of"))
    if as_of:
        parts.append(f"data as of {as_of}" + (" (stale)" if entry.get("stale") else ""))
    variants = [
        v
        for v in entry.get("variants") or []
        if isinstance(v, dict) and v.get("publishable", True) is not False
    ]
    if variants:
        default = next((v for v in variants if v.get("is_default")), None)
        # Name only settings a fork can take (Q-2498): a variant keel-api
        # marks `forkable: false` is counted, never recommended.
        ranked = sorted(
            (
                v
                for v in variants
                if v.get("forkable", True) is not False
                and _num((v.get("metrics") or {}).get("sharpe_ratio")) is not None
            ),
            key=lambda v: -_num(v["metrics"]["sharpe_ratio"]),  # type: ignore[operator]
        )
        named = []
        for v in ranked[:MAX_FACT_VARIANTS]:
            m = v.get("metrics") or {}
            tag = " (default)" if v is default else ""
            named.append(
                f"{v.get('label') or v.get('variant_id')}{tag}: "
                + _metrics_phrase(m.get("sharpe_ratio"), m.get("total_return"), None, full=False)
            )
        text = f"{len(variants)} variant{'' if len(variants) == 1 else 's'}"
        if named:
            text += " — " + "; ".join(named)
            if len(ranked) > len(named):
                text += f"; +{len(ranked) - len(named)} more"
        if default is not None and default not in ranked[:MAX_FACT_VARIANTS]:
            text += f"; default {default.get('label') or default.get('variant_id')}"
        parts.append(text)
    return " · ".join(parts) or None


def _library_view(entry: Any, *, hero_url: str) -> dict | None:
    """The `view` for one library entry, from the graph it publishes.

    A library entry is a published, immutable strategy: its version is
    the entry version, its status is PUBLISHED, and its verified numbers
    already ride the envelope's own fields — the view carries no
    evidence block rather than re-deriving one (one computation owner
    per measurement).
    """
    from ._strategy_view import build_view

    if not isinstance(entry, dict):
        return None
    metadata = {
        "name": entry.get("name") or entry.get("slug"),
        "status": "PUBLISHED",
        "version": entry.get("entry_version"),
    }
    return build_view(entry.get("graph"), metadata, url=hero_url)


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
                "variants: a mistyped id returns `unknown_variant` with the "
                "known ids, and a variant marked `forkable: false` cannot be "
                "forked."
            ),
        )

    new_sid = result.get("strategy_id")
    extra: dict[str, Any] = {
        "strategy_id": new_sid,
        "name": result.get("name"),
        "source_slug": result.get("source_slug"),
        "entry_version": result.get("entry_version"),
        "variant_id": result.get("variant_id"),
        "is_default_variant": result.get("is_default_variant"),
    }
    # What the fork now holds, read back from the server (PLAN §4.2) —
    # the same view every strategy-shaped tool carries. Advisory: the
    # fork has already happened.
    from .open_in_app import app_url_for
    from .strategy_get import view_for_strategy

    if new_sid:
        view = view_for_strategy(client, new_sid, ctx)
        if view is not None:
            extra["view"] = view

    return OutcomeResult(
        run_id=new_sid,
        hero_url=app_url_for("strategy", new_sid, ctx) if new_sid else f"{ctx.app_url}/library",
        share_url=None,
        extra=extra,
    )


LIBRARY_LIST = register(
    OutcomeTool(
        name="keel_library_list",
        required_action="strategy.read",
        cli_path=("library", "list"),
        toolset="read-only",
        description=(
            "List the Keel Library — the verified, backtested strategy entries published "
            "in the product; the caller's own strategies are `keel_strategy_search`. Each "
            "row carries the slug, name, category, risk band and headline metrics from the "
            "entry's verified run. One entry's full facts are `keel_library_get`; starting "
            "from one is `keel_library_fork`."
        ),
        input_schema={"type": "object", "properties": {}},
        annotations={
            "title": "List Library Entries",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
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
            "Read one Keel Library entry's verified facts before a fork — forking it is "
            "`keel_library_fork`. They are its headline metrics, backtest window, data "
            "freshness and published variants (parameter presets with their own metrics), "
            "from the library's verified run; a fork's own numbers come from its own "
            "`keel_backtest_run`."
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
            "openWorldHint": False,
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
        # grounded-in: system/chat/collaboration.md §4 (iterate on a copy — the smallest
        # change, one at a time) + spec 08 §5 (library fork provenance: the
        # forked strategy is a normal, editable strategy; entries immutable)
        # + q3-wedges 03 §4.1 C5 (fork-and-verify is the product's trust
        # mechanic — the entry's numbers are the library's run, the user's
        # evidence comes from their own re-run).
        description=(
            "Fork a verified Keel Library entry into the caller's org as an editable "
            "strategy — a copy of a Keel strategy or a share link is "
            "`keel_strategy_fork`. The fork is a full copy with its own history; its "
            "evidence comes from its own `keel_backtest_run`, not the entry's published "
            "metrics. A `variant_id` from `keel_library_get` selects a published preset; "
            "`ma-crossover-crypto` is the simple entry new web users start from."
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
                        "Optional variant preset from `keel_library_get`; omitted, the "
                        "entry's default applies."
                    ),
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                    "description": f"Optional name for the forked strategy. {STRATEGY_NAME_FACT}",
                },
            },
        },
        annotations={
            "title": "Fork Library Entry",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_fork_handler,
    )
)
