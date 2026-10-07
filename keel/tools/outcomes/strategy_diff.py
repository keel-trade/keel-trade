"""`keel_strategy_diff` — diff two strategy sources or two remote versions.

Replaces: `strategy_diff` (local) + `strategy_version_diff` (remote).

Two modes:
    - File-pair: `ref_a` + `ref_b` are file paths (local DSL files).
    - Version-pair: `strategy_id` set; `ref_a` + `ref_b` are commit/tag refs.

Do NOT use to fetch the source — call `keel_strategy_get` first if needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .open_in_app import app_url_for


def _summarize_changes(
    *,
    added: list,
    removed: list,
    modified: list,
    reordered: list,
    version_bumps: dict,
) -> str | None:
    """Build a one-line summary from the structural diff arrays.

    The server's diff response has rich detail but no narrative text. An
    agent or user reading the envelope shouldn't have to scan five arrays
    to learn "ROC's period went from 20 to 42" — synthesize a line that
    quotes the most informative bits.
    """
    parts: list[str] = []
    if added:
        names = ", ".join(_step_name(s) for s in added[:3])
        more = f" (+{len(added) - 3} more)" if len(added) > 3 else ""
        parts.append(f"added {len(added)}: {names}{more}")
    if removed:
        names = ", ".join(_step_name(s) for s in removed[:3])
        more = f" (+{len(removed) - 3} more)" if len(removed) > 3 else ""
        parts.append(f"removed {len(removed)}: {names}{more}")
    if modified:
        # Quote the first param-change for each modified step so the user
        # sees the actual delta inline (e.g. "ROC.period 20→42").
        bits: list[str] = []
        for step in modified[:3]:
            name = _step_name(step)
            param_changes = step.get("param_changes") if isinstance(step, dict) else None
            if isinstance(param_changes, dict) and param_changes:
                pname, change = next(iter(param_changes.items()))
                if isinstance(change, (list, tuple)) and len(change) == 2:
                    bits.append(f"{name}.{pname} {change[0]}→{change[1]}")
                    continue
            bits.append(name)
        more = f" (+{len(modified) - 3} more)" if len(modified) > 3 else ""
        parts.append(f"modified {len(modified)}: {', '.join(bits)}{more}")
    if reordered:
        parts.append(f"reordered {len(reordered)}")
    if version_bumps:
        parts.append(f"component versions bumped: {len(version_bumps)}")
    if not parts:
        return "Identical — no structural changes between the two versions."
    return "; ".join(parts) + "."


def diff_sources(source_a: str, source_b: str) -> dict[str, Any]:
    """The structural diff between two DSL sources, in envelope keys.

    The ONE source-pair diff in the SDK. `keel_strategy_diff`'s file
    mode is this function, and `keel_strategy_compose` calls it to learn
    what an update changed — neither re-implements the translation from
    `pipeline_engine.dsl.differ`'s shape to the envelope's
    `added/removed/changed/reordered/summary_text` keys.

    Raises whatever the local differ raises; callers that treat a diff
    as advisory catch it.
    """
    from keel.tools.local import strategy_diff as local_diff

    result = local_diff(source_a=source_a, source_b=source_b)
    added = result.get("added", []) or []
    removed = result.get("removed", []) or []
    modified = result.get("changed", []) or result.get("modified", []) or []
    reordered = result.get("reordered", []) or []
    version_bumps = result.get("component_version_changes", {}) or {}
    return {
        "added": added,
        "removed": removed,
        "changed": modified,
        "reordered": reordered,
        "component_version_changes": version_bumps,
        "summary_text": (
            result.get("summary_text")
            or result.get("summary")
            or _summarize_changes(
                added=added,
                removed=removed,
                modified=modified,
                reordered=reordered,
                version_bumps=version_bumps,
            )
        ),
        "error": result.get("error"),
    }


def _step_name(step) -> str:
    if isinstance(step, dict):
        return str(step.get("step_name") or step.get("name") or step.get("component") or "?")
    return str(step)


def _read_path_or_source(value: str) -> str:
    from keel.hosting import is_hosted

    if not is_hosted():
        # Local file paths are a LOCAL-mode convenience only. A hosted
        # server has no caller filesystem — reading pod paths here would
        # be both wrong and an information-disclosure hole.
        p = Path(value)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
    # Treat as already-DSL string only if multi-line; else error out
    if "\n" in value:
        return value
    if is_hosted():
        raise KeelError(
            f"Diff ref is not multi-line DSL: {value!r}. File paths are not "
            "available on the hosted server.",
            error_code="not_found",
            exit_code=3,
            suggestion=(
                "On the hosted server each ref must be either multi-line DSL "
                "text, or (with `strategy_id=...`) a server version ref "
                "(sequence number, commit_id, or tag — find via "
                "`keel_strategy_history`)."
            ),
        )
    raise KeelError(
        f"Diff input not found and not multi-line DSL: {value!r}",
        error_code="not_found",
        exit_code=3,
        suggestion=(
            "Each ref must be either: (a) a path to a .py file, or "
            "(b) multi-line DSL text. For comparing two SERVER versions, "
            "pass `strategy_id=...` AND set each ref to a sequence number, "
            "commit_id, or tag (e.g. `ref_a=3 ref_b=7`)."
        ),
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    ref_a: str = (args.get("ref_a") or "").strip()
    ref_b: str = (args.get("ref_b") or "").strip()
    strategy_id: str | None = args.get("strategy_id")
    if not ref_a or not ref_b:
        raise KeelError(
            "Both `ref_a` and `ref_b` are required.",
            error_code="missing_refs",
            exit_code=2,
            suggestion=(
                "Two modes: (a) file-pair → both refs are .py paths or DSL "
                "strings; (b) version-pair → also pass `strategy_id` and set "
                "both refs to sequence numbers / commit_ids / tags "
                "(find via `keel_strategy_history`)."
            ),
        )

    if strategy_id:
        # Remote version-diff
        client = ctx.get_client()
        try:
            result = client.post(
                f"/v1/strategies/{strategy_id}/versions/diff",
                json={"ref_a": ref_a, "ref_b": ref_b},
            )
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to compute version diff: {e}",
                suggestion=(
                    "Verify both refs exist via `keel_strategy_history "
                    f"{strategy_id}`. Common cause: one ref is a stale "
                    "sequence_number from before a restore reset HEAD."
                ),
            )

        # The keel-api wraps the structural diff under a `changes` dict
        # with keys `added_steps`, `removed_steps`, `modified_steps`,
        # `reordered_steps`, `unchanged_steps`, `component_version_changes`.
        # Hoist the meaningful arrays + synthesize a summary so callers
        # don't need a translation table or empty fallbacks.
        changes = result.get("changes") if isinstance(result, dict) else {}
        changes = changes if isinstance(changes, dict) else {}
        added = changes.get("added_steps", [])
        removed = changes.get("removed_steps", [])
        modified = changes.get("modified_steps", [])
        reordered = changes.get("reordered_steps", [])
        version_bumps = changes.get("component_version_changes", {}) or {}

        extra: dict[str, Any] = {
            "mode": "version",
            "strategy_id": strategy_id,
            "ref_a": ref_a,
            "ref_b": ref_b,
            "added": added,
            "removed": removed,
            "changed": modified,
            "reordered": reordered,
            "component_version_changes": version_bumps,
            "summary_text": _summarize_changes(
                added=added,
                removed=removed,
                modified=modified,
                reordered=reordered,
                version_bumps=version_bumps,
            ),
        }
        # The declaration half (Q-1898): the server's diff compares steps only,
        # so a Globals/Universe/Execution edit read "Identical". Both versions'
        # sources are read once and diffed with the same `_declarations` the
        # compose change view uses.
        source_a = _version_source(client, strategy_id, ref_a)
        source_b = _version_source(client, strategy_id, ref_b)
        if source_a and source_b:
            _add_declarations(extra, source_a, source_b)
        # `?compare=a..b` was never read by any page; `version` is a param the
        # editor genuinely honours, so the link opens the side it diffed TO.
        hero_url = app_url_for("strategy", strategy_id, ctx, query={"version": ref_b})
        view = _version_view(
            source_b, ref_a, ref_b, extra, hero_url, name=_strategy_name(client, strategy_id)
        )
        if view is not None:
            extra["view"] = view
        return OutcomeResult(
            run_id=strategy_id,
            hero_url=hero_url,
            share_url=None,
            extra=extra,
        )

    # Local file-pair mode
    source_a = _read_path_or_source(ref_a)
    source_b = _read_path_or_source(ref_b)
    try:
        # Local diff has its own shape (top-level lists); `diff_sources`
        # translates it to the same envelope keys the version-diff
        # branch produces, so callers get one shape regardless of mode.
        result = diff_sources(source_a, source_b)
    except ImportError as e:
        raise KeelError(
            f"Local diff unavailable: {e}",
            suggestion=(
                "The local diff helper failed to import — likely a missing "
                "dependency in the SDK install. Pass `strategy_id=...` to "
                "use the server-side diff instead, or run `keel_connection_check`."
            ),
        )
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Diff failed: {e}",
            suggestion=(
                "Both sources must be valid Keel DSL — run "
                "`keel_strategy_compose dry_run=True` on each first to surface "
                "the syntax error. For comparing server versions instead, "
                "pass `strategy_id` so refs become sequence numbers."
            ),
        )

    extra = {"mode": "file", "ref_a": ref_a, "ref_b": ref_b}
    extra.update({k: v for k, v in result.items() if k != "error"})
    _add_declarations(extra, source_a, source_b)
    result = {**result, **{k: extra[k] for k in ("declarations", "summary_text") if k in extra}}

    # The change, drawn (PLAN §4.2). The "after" source is the subject:
    # a diff answers "did it change the way I meant", which is a
    # question about what the strategy IS now.
    view = _diff_view(source_b, result)
    if view is not None:
        extra["view"] = view

    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra=extra,
    )


def _version_source(client: Any, strategy_id: str, ref: str) -> str | None:
    """One version's DSL source, or None — advisory, never fails the diff."""
    try:
        payload = client.get(f"/v1/strategies/{strategy_id}/versions/{ref}/source")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    source = payload.get("source") if isinstance(payload, dict) else None
    return source if isinstance(source, str) and source else None


def _add_declarations(extra: dict, source_a: str, source_b: str) -> None:
    """Put the declaration half of the diff on the envelope (Q-1898).

    The step differ compares the PIPELINE only, so two strategies that
    differed only in `Globals(bar_offset=…)` — R4's −7.1% vs +44.6% pair —
    came back "Identical". `declarations` is `_declarations`' own
    `{section: {key: {a, b}}}` block (the shape compose's change view and
    `keel_backtest_compare`'s `spec_diff` carry), and the summary names each
    moved declaration beside the step tally. Advisory: a source that does
    not parse leaves the step diff as it was.
    """
    try:
        from ._declarations import declarations_between
        from ._strategy_view import _change_summary

        declarations = declarations_between(source_a, source_b)
    except Exception:  # noqa: BLE001 — the step half still stands
        return
    if not declarations:
        return
    extra["declarations"] = declarations
    blocks_touched = sum(len(extra.get(k) or []) for k in ("added", "removed", "changed"))
    summary = _change_summary(extra, declarations, blocks_touched)
    if summary:
        extra["summary_text"] = summary


def _strategy_name(client: Any, strategy_id: str) -> str | None:
    """The strategy's name for the card title — advisory, never fails the
    diff (the card read "Untitled" because no name reached it, Q-2273)."""
    try:
        row = client.get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    name = row.get("name") if isinstance(row, dict) else None
    return name if isinstance(name, str) and name.strip() else None


def _version_view(
    source: str | None,
    ref_a: str,
    ref_b: str,
    diff: dict,
    hero_url: str,
    *,
    name: str | None = None,
) -> dict | None:
    """The view for a version-pair diff: `ref_b`'s own graph, marked up.

    `ref_b` is read explicitly rather than taken from the strategy's
    HEAD — a diff between two old versions must not draw the current
    one and mark it with someone else's change.
    """
    if not source:
        return None
    try:
        from pipeline_engine.dsl.emitter import spec_to_graph
        from pipeline_engine.dsl.parser import parse_strategy

        from ._strategy_view import build_view, change_from_diff

        graph = spec_to_graph(parse_strategy(source)).to_dict()
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    # A ref may be a sequence number, a tag, or a commit id. Only a
    # sequence number is a VERSION a reader can be shown — a commit id
    # in the header would be exactly the id the header must not carry.
    numbered = ref_a.isdigit() and ref_b.isdigit()
    change = change_from_diff(
        diff,
        graph,
        from_version=int(ref_a) if numbered else None,
        to_version=int(ref_b) if numbered else None,
    )
    metadata: dict[str, Any] = {"version": int(ref_b)} if ref_b.isdigit() else {}
    if name:
        metadata["name"] = name
    return build_view(graph, metadata, change=change, url=hero_url)


def _diff_view(source_after: str, diff: dict) -> dict | None:
    """The view for a file-pair diff: the "after" graph, marked up.

    Local-mode only — the graph is derived here because there is no
    stored strategy to read one from. Advisory: a source the parser
    rejects yields no view, and the diff envelope is unchanged.
    """
    try:
        from pipeline_engine.dsl.emitter import spec_to_graph
        from pipeline_engine.dsl.parser import parse_strategy

        from ._strategy_view import build_view, change_from_diff

        graph = spec_to_graph(parse_strategy(source_after)).to_dict()
    except Exception:  # noqa: BLE001 — a render nicety never fails a tool call
        return None
    return build_view(graph, {}, change=change_from_diff(diff, graph))


#: The listed (hosted, file-free) parameter copy (Q-1898): the shared
#: schema says "File path (file mode)", which a hosted caller cannot use —
#: the hosted refs are version refs (with `strategy_id`) or DSL source text,
#: and the source-text mode is how two DIFFERENT strategies are compared.
LISTED_INPUT_SCHEMA: dict = {
    "type": "object",
    "required": ["ref_a", "ref_b"],
    "properties": {
        "ref_a": {
            "type": "string",
            "description": (
                "The 'before' side: a version ref (sequence number, tag or commit id) "
                "with `strategy_id`, else DSL source text."
            ),
        },
        "ref_b": {
            "type": "string",
            "description": (
                "The 'after' side: a version ref with `strategy_id`, else DSL source text."
            ),
        },
        "strategy_id": {
            "type": "string",
            "description": "If set, diff two versions of this strategy.",
        },
    },
}


STRATEGY_DIFF = register(
    OutcomeTool(
        name="keel_strategy_diff",
        required_action="strategy.read",
        cli_path=("strategy", "diff"),
        toolset="read-only",
        # grounded-in: system/chat/collaboration.md:52-69 (§4 iterate, one
        # change at a time — confirm you changed ONLY what you intended);
        # system/chat/component_versioning.md:13-end ("Component Version
        # Awareness" — note when a component version changed);
        # system/chat/tool_usage.md:8 (state analysis — what actually differs
        # between two versions).
        description=(
            "What changed between two strategies or two versions of one (versions: "
            "`keel_strategy_history`; several runs' results: `keel_backtest_compare`). "
            "Reports added, removed and modified steps, "
            "parameter and declaration changes (`ROC.period 20→42`) and reordering, "
            "drawn as a diff card. With `strategy_id` the refs are "
            "versions; without it each is a local file path or DSL source, so two "
            "strategies diff by their sources (from `keel_strategy_get`)."
        ),
        # Listed-profile copy (agent-surface-cleanup spec 01 §2.5, R-4): the same
        # text minus one surface fact — the hosted server has no local files, so file mode is not offered
        # there.
        listed_description=(
            "What changed between two strategies or two versions of one (versions: "
            "`keel_strategy_history`; several runs' results: `keel_backtest_compare`). "
            "Reports added, removed and modified steps, "
            "parameter and declaration changes (`ROC.period 20→42`) and reordering, "
            "drawn as a diff card. With `strategy_id` the refs are "
            "versions; without it each is DSL source, so two strategies diff by their "
            "sources (from `keel_strategy_get`)."
        ),
        listed_input_schema=LISTED_INPUT_SCHEMA,
        input_schema={
            "type": "object",
            "required": ["ref_a", "ref_b"],
            "properties": {
                "ref_a": {
                    "type": "string",
                    "description": "File path (file mode) or version ref (version mode).",
                },
                "ref_b": {
                    "type": "string",
                    "description": "File path (file mode) or version ref (version mode).",
                },
                "strategy_id": {
                    "type": "string",
                    "description": "If set, diff two versions of this strategy.",
                },
            },
        },
        annotations={
            "title": "Diff Strategy Versions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
