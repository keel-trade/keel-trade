"""`keel_components_get_many` — fetch full detail for many components in one call.

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

import json
from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._surface_hints import usage_hint


#: Names per call (Q-2273 L7). Thirty full contracts came to 109 KB of text,
#: past Claude's 25k-token tool-result limit; ten stay well inside it.
MAX_NAMES = 10
#: Contract text per call. The ten LARGEST contracts are ~108 KB, so the
#: name cap alone does not bound the result: once the contracts returned
#: reach this budget, the rest are named and left for a call of their own.
TEXT_BUDGET = 60_000
OMITTED_CODE = "omitted_for_size"


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    raw_names = args.get("names") or []
    # Accept either a list (canonical) OR a comma-separated string (CLI
    # convenience — Click multi=True returns tuples but if a single
    # `--names a,b,c` form ever reaches here, normalize.)
    if isinstance(raw_names, str):
        names: list[str] = [n.strip() for n in raw_names.split(",") if n.strip()]
    else:
        names = [str(n).strip() for n in raw_names if str(n).strip()]
    # One contract per distinct name: fifty `ROC`s were fifty reads and fifty
    # copies of one table (Q-2273 L7). Order is kept; names are case-sensitive.
    names = list(dict.fromkeys(names))
    if len(names) > MAX_NAMES:
        raise KeelError(
            f"At most {MAX_NAMES} component names per call (got {len(names)} distinct).",
            error_code="usage_error",
            exit_code=2,
            suggestion=(
                f"Pass the first {MAX_NAMES} names now and the rest in another call; "
                "nothing was read."
            ),
            input={"names": names},
        )

    if not names:
        raise KeelError(
            "Missing required `names` argument — pass a list of "
            "component names (e.g. `names=['ROC', 'EWMA', 'ForecastScaler']`).",
            error_code="missing_names",
            exit_code=2,
            suggestion=usage_hint(
                "Run `keel components describe-batch ROC EWMA ForecastScaler`.",
                "Pass `names=['ROC', 'EWMA']`; `keel_components_search` finds names.",
            ),
        )

    # Reuse the single-detail handler for each name. Errors per component
    # (KeyError / NotFoundError) become `{"error": "..."}` entries
    # instead of aborting the whole batch — agents typically want a
    # partial result they can act on.
    from ._backtest_view import parallel_map
    from .components_help import _handler as _single_handler

    def _one(name: str) -> dict[str, Any]:
        try:
            return _single_handler({"name": name}, ctx).to_envelope()
        except KeelError as e:
            # Keep partial — surface the per-component error as data.
            return {
                "error": str(e),
                "error_code": getattr(e, "error_code", "error"),
                "suggestion": getattr(e, "suggestion", None),
            }
        except Exception:  # noqa: BLE001
            return {"error": "Unexpected error reading this component."}

    # The reads run concurrently (they were one sequential GET per name).
    results: dict[str, Any] = dict(zip(names, parallel_map(_one, names), strict=True))

    # Bound the result's size (Q-2273 L7): every contract that fits is
    # returned whole, in the order asked; one that would carry the result
    # past TEXT_BUDGET is named and left out. The first is always returned.
    from ._surface_hints import tool_ref

    used = 0
    for name, entry in results.items():
        if "error" in entry:
            continue
        size = len(json.dumps(entry, default=str))
        if used and used + size > TEXT_BUDGET:
            results[name] = {
                "error": (
                    "Left out to keep this result within what a host shows; "
                    f"read it with {tool_ref('keel_components_get')}."
                ),
                "error_code": OMITTED_CODE,
            }
            continue
        used += size

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/components",
        share_url=None,
        extra={
            "components": results,
            "found": sum(1 for r in results.values() if "error" not in r),
            "missing": sum(
                1 for r in results.values() if "error" in r and r.get("error_code") != OMITTED_CODE
            ),
            "omitted": sum(1 for r in results.values() if r.get("error_code") == OMITTED_CODE),
            "names_requested": names,
        },
    )


COMPONENTS_DETAIL_BATCH = register(
    OutcomeTool(
        name="keel_components_get_many",
        required_action="component.read",
        cli_path=("components", "describe-batch"),
        toolset="read-only",
        # grounded-in: system/chat/tool_usage.md:21-25 (two-step discovery,
        # new + iterative); system/chat/collaboration.md:39-47 (batch-fetch ALL incl.
        # standard components; plan from real type signatures/slots, not
        # names or pattern memory).
        description=(
            "Fetch the full contract of SEVERAL components in one call — one component "
            "alone is `keel_components_get`. Each entry carries the parameter list, type "
            "signature, slot reads and writes and examples — what a pipeline's wiring is "
            "checked against. Candidates come from `keel_components_search`. "
            "The result gives each name's contract with found / missing counts; an "
            "unknown name is reported as missing, not a failed call."
        ),
        input_schema={
            "type": "object",
            "required": ["names"],
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_NAMES,
                    "x-cli-positional": True,  # accept `... describe-batch ROC EWMA Z`
                    "description": (
                        "Component names to look up, up to 10 per call. Case-sensitive — "
                        "matched exactly against the registry (e.g. `['ROC', 'EWMA', "
                        "'ForecastScaler']`)."
                    ),
                },
            },
        },
        annotations={
            "title": "Get Components",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
