"""`keel_strategy_get` — fetch one strategy (metadata, optional source/versions).

Replaces: `strategy_show`, `strategy_versions`, `strategy_source`, and the
read side of `strategy_import_share`.

Do NOT use to enumerate strategies — call `keel_strategy_search`.
Do NOT use to mutate the strategy — call `keel_strategy_compose`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._listed_projection import LISTED_SOURCE_FIELDS, listed_strategy_metadata, pick
from ._ownership import fetch_ownership_projection, ownership_envelope_fields
from ._surface_hints import tool_ref


def read_source(client: Any, strategy_id: str, version: str = "HEAD") -> str | None:
    """One strategy version's DSL source, best-effort.

    Advisory like the graph read beside it: the Code tab is a rendering
    nicety, so a source that will not read returns None and the envelope is
    simply built without it. Never raises into a caller that already wrote.
    """
    try:
        payload = client.get(f"/v1/strategies/{strategy_id}/versions/{version}/source")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("source")
    return text if isinstance(text, str) and text.strip() else None


def _read_version_payload(client: Any, strategy_id: str, version: str) -> dict | None:
    """keel-api's `{source, sequence_number, commit_id, ...}` for one
    version, best-effort (a render nicety, like `read_source`)."""
    try:
        payload = client.get(f"/v1/strategies/{strategy_id}/versions/{version}/source")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("source")
    return payload if isinstance(text, str) and text.strip() else None


def _graph_for_source(source: Any) -> dict | None:
    """The graph keel-api would derive for `source` — the SAME two calls its
    `include=graph` makes (`parse_strategy` → `spec_to_graph`), run here
    because the server derives a graph for HEAD only. None when it will not
    parse: the view is then absent, never HEAD's structure wearing the
    requested version's label."""
    if not isinstance(source, str) or not source.strip():
        return None
    try:
        from pipeline_engine.dsl import parse_strategy
        from pipeline_engine.dsl.emitter import spec_to_graph

        return spec_to_graph(parse_strategy(source)).to_dict()
    except Exception:  # noqa: BLE001 — an unparseable old dialect costs the view
        return None


def _version_view_inputs(
    meta: Any, version: str, payload: Any, source_text: Any
) -> tuple[dict | None, dict, Any]:
    """`(graph, view metadata, head version or None)` for the view.

    `version` at HEAD (or resolving to HEAD's sequence) is the server's graph
    and row, unchanged. Any other version is described WHOLLY as that version
    (ChatGPT R4 #1: `version=1` returned v1's source inside a view labelled v4
    drawing HEAD's structure and evidence): its own graph, its own sequence,
    no HEAD-only fields (status, validation, the latest run), and HEAD
    returned apart.
    """
    row = meta if isinstance(meta, dict) else {}
    head = row.get("current_sequence")
    requested = payload.get("sequence_number") if isinstance(payload, dict) else None
    if (
        str(version).upper() == "HEAD"
        or not isinstance(requested, int)
        or isinstance(requested, bool)
        or requested == head
    ):
        return row.get("graph"), row, None
    view_meta = {
        key: row.get(key)
        for key in ("id", "strategy_id", "name", "strategy_name")
        if row.get(key) is not None
    }
    view_meta["current_sequence"] = requested
    return _graph_for_source(source_text), view_meta, head


#: Completed runs the evidence choice reads (newest first by queue time).
EVIDENCE_LISTING_LIMIT = 50

#: Runs `recent_runs` names (Q-2269) — enough to answer "my latest run" and
#: "my last two" without a second tool.
RECENT_RUNS_LIMIT = 5


def _list_runs(client: Any, strategy_id: str) -> list[dict] | None:
    """One strategy's `/v1/backtests` listing rows (newest first by queue
    time), or None when the listing fails — advisory like every render read."""
    from ._pagination import extract_paginated

    try:
        payload = client.get("/v1/backtests", strategy_id=strategy_id, limit=EVIDENCE_LISTING_LIMIT)
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    rows, _ = extract_paginated(payload)
    return [r for r in rows if isinstance(r, dict)]


def _representative_evidence(rows: list[dict] | None, view_meta: dict) -> dict | None:
    """The run the view quotes (Q-1879): the requested version's newest
    full-window run, else the newest full-window run of any version, else a
    sub-window run marked as one. None ⇒ `build_view` keeps the metadata's
    latest-run answer (the listing failed or held nothing completed)."""
    from ._strategy_view import choose_evidence

    if rows is None:
        return None
    evidence, decided = choose_evidence(rows, version=view_meta.get("current_sequence"))
    return evidence if decided else None


def recent_runs(rows: list[dict] | None, *, limit: int = RECENT_RUNS_LIMIT) -> list[dict]:
    """`recent_runs` (Q-2269): the strategy's newest runs, every status, newest
    first — `run_id`, `status`, `version`, its commit `message` (Q-2505:
    which variant that version is; null when the commit has none),
    `window`, `completed_at` and the
    headline metrics as the backtest card quotes them (`view_metrics`, so the
    drawdown sign and the active-period Sharpe agree with
    `keel_backtest_summarize`). No listed tool listed a strategy's runs, so
    "summarize my latest backtest" and "compare my last two" had no run id
    to start from. Empty when the listing failed or held nothing."""
    from ._backtest_view import HEADLINE_TILES, _completed_at, view_metrics, window_block

    def when(row: dict) -> Any:
        from datetime import datetime

        stamp = _completed_at(row)
        return stamp.replace(tzinfo=None) if stamp is not None else datetime.min

    out: list[dict] = []
    for row in sorted(rows or [], key=when, reverse=True)[:limit]:
        run_id = row.get("id") or row.get("backtest_run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        metrics = view_metrics(row.get("metrics"))
        headline = {k: metrics[k] for k, _label in HEADLINE_TILES if k in metrics}
        out.append(
            {
                "run_id": run_id,
                "status": row.get("status"),
                "version": row.get("sequence_number"),
                # keel-api's run row carries the version's commit message
                # (Q-2505); empty is null, never invented.
                "message": row.get("commit_message") or None,
                "window": window_block(
                    row.get("start_date"), row.get("end_date"), row.get("window")
                ),
                "completed_at": row.get("completed_at"),
                "metrics": headline,
            }
        )
    return out


def view_for_strategy(
    client: Any,
    strategy_id: str,
    ctx: ToolContext,
    *,
    diff: Any = None,
    from_version: Any = None,
    source: Any = None,
) -> dict | None:
    """Read one strategy's server-derived graph and build its `view`.

    The ONE place a tool that just WROTE a strategy (fork, library fork,
    a persisted compose) reads back what the server now holds. `diff` is
    a raw `strategy_diff.diff_sources` result, keyed to THIS graph's
    block ids here — the only place both halves are in hand. Advisory by
    construction: a read that fails, or a source the server could not
    parse, returns None and the caller's envelope is unchanged — the
    write already succeeded and a rendering must never undo it.
    """
    from ._strategy_view import build_view, change_from_diff
    from .open_in_app import app_url_for

    try:
        meta = client.get(f"/v1/strategies/{strategy_id}", include="graph")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    if not isinstance(meta, dict):
        return None
    graph = meta.get("graph")
    change = (
        change_from_diff(
            diff,
            graph,
            from_version=from_version,
            to_version=meta.get("current_sequence"),
        )
        if diff is not None
        else None
    )
    # A caller that just WROTE the source already holds it — compose passes
    # what it sent rather than paying for a read-back of its own bytes.
    if source is None:
        source = read_source(client, strategy_id)
    return build_view(
        graph,
        meta,
        change=change,
        url=app_url_for("strategy", strategy_id, ctx),
        source=source,
        # The same evidence choice `keel_strategy_get` makes (Q-1879), so a
        # save's card and the next read's card quote the same run.
        evidence=_representative_evidence(_list_runs(client, strategy_id), meta),
    )


def _listed_versions(payload: Any) -> dict[str, Any]:
    """`include_versions` on listed: each row as `keel_strategy_history`
    returns it there (`strategy_log.version_entry`), under the same `data`
    key keel-api's listing uses."""
    from keel.workspace import _normalize_paginated_versions

    from .strategy_log import version_entry

    return {"data": [version_entry(v, listed=True) for v in _normalize_paginated_versions(payload)]}


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id: str = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass the strategy id as the positional argument.",
        )

    version: str = (args.get("version") or "HEAD").strip() or "HEAD"
    include_source: bool = bool(args.get("include_source", False))
    include_versions: bool = bool(args.get("include_versions", False))
    include_ownership_hint: bool = not bool(args.get("skip_readiness", False))

    client = ctx.get_client()

    try:
        # `include=graph` asks keel-api for the derived graph model
        # (pipeline blocks, universe, globals, execution) beside the
        # metadata — the strategy card's glass box renders from it
        # (Q-1505, D2.2 render-only: the server derives, the card
        # draws). Best-effort on the server side: a source that will
        # not parse comes back without `graph`, never as an error.
        meta = client.get(f"/v1/strategies/{strategy_id}", include="graph")
    except NotFoundError:
        raise NotFoundError(
            f"Strategy not found: {strategy_id}",
            suggestion=f"{tool_ref('keel_strategy_search')} lists the available strategies.",
        )

    from ._toolsets import is_listed_profile

    # The LISTED result carries allow-listed projections of keel-api's rows
    # (Q-2268, `_listed_projection`): no org id, storage key, content or
    # lock hash, deployment id or raw commit provenance. The view below is
    # built from the whole row either way; the CLI keeps the whole row.
    listed = is_listed_profile()
    body: dict[str, Any] = {"metadata": listed_strategy_metadata(meta) if listed else meta}

    if include_versions:
        try:
            versions = client.get(f"/v1/strategies/{strategy_id}/versions")
            body["versions"] = _listed_versions(versions) if listed else versions
        except KeelError as e:
            body["versions_error"] = str(e)

    source_text: str | None = None
    version_payload: dict | None = None
    if include_source:
        try:
            src = client.get(f"/v1/strategies/{strategy_id}/versions/{version}/source")
            body["source"] = pick(src, LISTED_SOURCE_FIELDS) if listed else src
            body["version"] = version
            if isinstance(src, dict) and isinstance(src.get("source"), str):
                source_text = src["source"]
                version_payload = src
        except KeelError as e:
            body["source_error"] = str(e)

    body["resource_uri"] = f"keel://strategy/{strategy_id}/source"
    if include_ownership_hint:
        body.update(ownership_envelope_fields(fetch_ownership_projection(ctx, strategy_id)))

    # V-6 (ratified 2026-09-19): every strategy link lands in the editor.
    # Routed through the ONE owner of app-URL shape, never an f-string.
    from .open_in_app import app_url_for

    hero_url = app_url_for("strategy", strategy_id, ctx)

    # The derived view (PLAN §4.2) — the structure, the header, the size
    # and the markdown every text host relays. Best-effort: a source the
    # server could not parse comes back without `graph`, and the envelope
    # simply carries no view rather than failing the read.
    from ._strategy_view import build_view

    # The Code tab renders the strategy's own source, so the view carries it.
    # Reuse what `include_source` already fetched; only pay for a second read
    # when the caller did not ask for the source itself.
    if source_text is None:
        version_payload = _read_version_payload(client, strategy_id, version)
        source_text = version_payload.get("source") if version_payload else None
    graph, view_meta, head = _version_view_inputs(meta, version, version_payload, source_text)
    runs = _list_runs(client, strategy_id)
    # The strategy's newest runs, every status (Q-2269): the run ids
    # `keel_backtest_summarize`, `keel_backtest_compare` and
    # `keel_share_create` take, so "my latest backtest" resolves from here.
    body["recent_runs"] = recent_runs(runs)
    evidence = _representative_evidence(runs, view_meta)
    view = build_view(
        graph,
        view_meta,
        url=hero_url,
        source=source_text,
        evidence=evidence,
        head_version=head,
    )
    if view is not None:
        body["view"] = view
    if head is not None:
        # HEAD's context, beside the requested version's view — never in it
        # (ChatGPT R4 #1).
        body["head_summary"] = {
            "version": head,
            "name": meta.get("name") if isinstance(meta, dict) else None,
        }

    # Card + per-surface render hints (spec 06 R2/R3) — render-only.
    from ._render import card_render_block

    body["render"] = card_render_block(
        "strategy",
        fallback_url=hero_url,
        ctx=ctx,
        embed_id=strategy_id,
    )

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=hero_url,
        share_url=None,
        resource_uri=f"keel://strategy/{strategy_id}/source",
        extra=body,
    )


STRATEGY_GET = register(
    OutcomeTool(
        name="keel_strategy_get",
        required_action="strategy.read",
        cli_path=("strategy", "get"),
        toolset="read-only",
        # grounded-in: system/chat/tool_usage.md:8 (state analysis — what does
        # the current pipeline produce, is the change compatible with its
        # structure); system/chat/collaboration.md §4/§7 (plan the change from the real types/slots,
        # not memory); context-architecture-design Part F (search → get → fork).
        description=(
            "Render a saved strategy as its structure card — a version's commit id alone "
            "is `keel_strategy_history`, with no card. `version` (HEAD, tag, sequence number or "
            "commit id) selects the version the card's structure, evidence and Code tab "
            "describe and `include_source=true` returns (a non-HEAD read names HEAD apart, "
            "as `head_summary`); `include_versions=true` lists recent versions; "
            "`recent_runs` lists its newest runs (up to 5) by the `run_id` "
            "`keel_backtest_summarize`, `keel_backtest_compare` and `keel_share_create` "
            "take. Changing it is `keel_strategy_compose`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id (e.g. `str_abc123`).",
                },
                "version": {
                    "type": "string",
                    "default": "HEAD",
                    "description": "Version ref (HEAD, tag, sequence number, or commit id).",
                },
                "include_source": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also fetch the DSL source at `version`.",
                },
                "include_versions": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also list the most recent versions (up to 50, newest first).",
                },
                "skip_readiness": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Leave the strategy's readiness fields (next step, missing "
                        "evidence) out of the result."
                    ),
                },
            },
        },
        annotations={
            "title": "Get Strategy",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
