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

    client = ctx.get_client()

    try:
        meta = client.get(f"/v1/strategies/{strategy_id}")
    except NotFoundError:
        raise NotFoundError(
            f"Strategy not found: {strategy_id}",
            suggestion="Run `keel strategy search` to list available strategies.",
        )

    body: dict[str, Any] = {"metadata": meta}

    if include_versions:
        try:
            versions = client.get(f"/v1/strategies/{strategy_id}/versions")
            body["versions"] = versions
        except KeelError as e:
            body["versions_error"] = str(e)

    if include_source:
        try:
            src = client.get(f"/v1/strategies/{strategy_id}/versions/{version}/source")
            body["source"] = src
            body["version"] = version
        except KeelError as e:
            body["source_error"] = str(e)

    body["resource_uri"] = f"keel://strategy/{strategy_id}/source"

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=f"{ctx.app_url}/strategies/{strategy_id}",
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
        description=(
            "Fetch one strategy by id. Returns metadata by default; pass "
            "`include_source=true` to also fetch the DSL source at a given "
            "`version` (default HEAD), and `include_versions=true` to list "
            "every commit. "
            "Do NOT use to enumerate strategies — call `keel_strategy_search`. "
            "Do NOT use to mutate the strategy — call `keel_strategy_compose`."
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
                    "description": "Also list all versions.",
                },
            },
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
