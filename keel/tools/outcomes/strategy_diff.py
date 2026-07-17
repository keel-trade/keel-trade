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
                "`keel_strategy_log`)."
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
                "(find via `keel_strategy_log`)."
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
                    "Verify both refs exist via `keel_strategy_log "
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
        return OutcomeResult(
            run_id=strategy_id,
            hero_url=f"{ctx.app_url}/strategies/{strategy_id}?compare={ref_a}..{ref_b}",
            share_url=None,
            extra=extra,
        )

    # Local file-pair mode
    source_a = _read_path_or_source(ref_a)
    source_b = _read_path_or_source(ref_b)
    try:
        from keel.tools.local import strategy_diff as local_diff
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Local diff unavailable: {e}",
            suggestion=(
                "The local diff helper failed to import — likely a missing "
                "dependency in the SDK install. Pass `strategy_id=...` to "
                "use the server-side diff instead, or run `keel_doctor`."
            ),
        )
    try:
        result = local_diff(source_a=source_a, source_b=source_b)
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

    # Local diff has its own shape (top-level lists). Translate to the
    # same envelope keys the version-diff branch produces so callers get
    # one consistent shape regardless of mode.
    added = result.get("added", []) or []
    removed = result.get("removed", []) or []
    modified = result.get("changed", []) or result.get("modified", []) or []
    reordered = result.get("reordered", []) or []
    version_bumps = result.get("component_version_changes", {}) or {}
    extra = {
        "mode": "file",
        "ref_a": ref_a,
        "ref_b": ref_b,
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
    }
    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra=extra,
    )


STRATEGY_DIFF = register(
    OutcomeTool(
        name="keel_strategy_diff",
        required_action="strategy.read",
        cli_path=("strategy", "diff"),
        toolset="read-only",
        description=(
            "Compute the structural diff between two strategy sources or two "
            "remote strategy versions. With `strategy_id` set, both refs are "
            "interpreted as commit/tag refs on that strategy. Without "
            "`strategy_id`, both refs are interpreted as local file paths. "
            "Do NOT use to fetch the actual source — call `keel_strategy_get`. "
            "Do NOT use to merge or update — call `keel_strategy_compose`."
        ),
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
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
