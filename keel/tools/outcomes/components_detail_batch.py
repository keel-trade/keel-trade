"""`keel_components_detail_batch` — fetch full detail for many components in one call.

Per the canonical compose workflow: after `keel_components_search` returns
a set of candidates, agents should batch-fetch the full spec for ALL
components they plan to wire into a pipeline. Verifying input/output
types, slot reads/writes, parameter constraints, and signature
compatibility BEFORE drafting DSL prevents the common "wrong-shape
component, dry-run fails, agent flails" loop.

This is the per-MCP/CLI port of chat-api's `strategy_component_detail_batch`
(the upstream `pipeline_engine.mcp.tools` module). Same return shape: a dict keyed
by component name. Unknown names return `{"error": "..."}` entries
rather than failing the whole call — partial success is preferred over
all-or-nothing.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    raw_names = args.get("names") or []
    # Accept either a list (canonical) OR a comma-separated string (CLI
    # convenience — Click multi=True returns tuples but if a single
    # `--names a,b,c` form ever reaches here, normalize.)
    if isinstance(raw_names, str):
        names: list[str] = [n.strip() for n in raw_names.split(",") if n.strip()]
    else:
        names = [str(n).strip() for n in raw_names if str(n).strip()]

    if not names:
        raise KeelError(
            "Missing required `names` argument — pass a list of "
            "component names (e.g. `names=['ROC', 'EWMA', 'ForecastScaler']`).",
            error_code="missing_names",
            exit_code=2,
            suggestion=(
                "From MCP: `keel_components_detail_batch(names=['ROC', 'EWMA'])`. "
                "From CLI: `keel components describe-batch ROC EWMA ForecastScaler`."
            ),
        )

    # Reuse the single-detail handler for each name. Errors per component
    # (KeyError / NotFoundError) become `{"error": "..."}` entries
    # instead of aborting the whole batch — agents typically want a
    # partial result they can act on.
    from .components_help import _handler as _single_handler

    results: dict[str, Any] = {}
    for name in names:
        try:
            result = _single_handler({"name": name}, ctx)
            results[name] = result.to_envelope()
        except KeelError as e:
            # Keep partial — surface the per-component error as data.
            results[name] = {
                "error": str(e),
                "error_code": getattr(e, "error_code", "error"),
                "suggestion": getattr(e, "suggestion", None),
            }
        except Exception as e:  # noqa: BLE001
            results[name] = {"error": f"Unexpected error: {e}"}

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/components",
        share_url=None,
        extra={
            "components": results,
            "found": sum(1 for r in results.values() if "error" not in r),
            "missing": sum(1 for r in results.values() if "error" in r),
            "names_requested": names,
        },
    )


COMPONENTS_DETAIL_BATCH = register(
    OutcomeTool(
        name="keel_components_detail_batch",
        required_action="component.read",
        cli_path=("components", "describe-batch"),
        toolset="read-only",
        description=(
            "Fetch the full spec (schema, parameter list, examples, slot "
            "reads/writes, type signature) for SEVERAL components in one "
            "call. The CANONICAL pre-composition step: after "
            "`keel_components_search` surfaces candidates, batch-fetch "
            "details for ALL components you plan to use BEFORE drafting "
            "DSL — verifies input/output types, slot dependencies, and "
            "parameter constraints in one round-trip. Prevents the "
            "common 'wrong-shape component → dry-run fails → re-search → "
            "re-draft' loop. "
            "\n\n"
            "Returns `components` as a dict keyed by name. Unknown names "
            "become `{\"error\": \"...\"}` entries rather than failing the "
            "whole call — partial result is the norm. `found` and "
            "`missing` counts surface in the envelope for quick triage. "
            "\n\n"
            "Recommended pattern (per `strategy-creation` skill): (1) "
            "decompose the user thesis into roles, (2) `keel_components_search` "
            "for each role, (3) mock the pipeline as a list of intended "
            "component refs, (4) `keel_components_detail_batch(names=[...])` "
            "to verify they fit together, (5) draft DSL, (6) dry-run. "
            "Do NOT use for ONE component — call `keel_components_compose_help` "
            "instead (cheaper)."
        ),
        input_schema={
            "type": "object",
            "required": ["names"],
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-cli-positional": True,  # accept `... describe-batch ROC EWMA Z`
                    "description": (
                        "List of component names to look up. "
                        "Case-sensitive — names must match the registry exactly "
                        "(e.g. `['ROC', 'EWMA', 'ForecastScaler']`)."
                    ),
                },
            },
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
