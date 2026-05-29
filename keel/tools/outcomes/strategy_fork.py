"""`keel_strategy_fork` — fork a strategy by id or share-link id.

Replaces: `sharing_fork`, `sharing_fork_by_id`, and the write portion of
`strategy_import_share`.

The single positional `source` arg auto-detects: values that look like a
Keel strategy id (`str_*`) hit `POST /v1/strategies/{id}/fork`; everything
else is treated as a share-link id and goes through
`POST /v1/strategies/fork-with-edits`.

Do NOT use to compose a new strategy from scratch — call `keel_strategy_compose`.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _looks_like_strategy_id(value: str) -> bool:
    return value.startswith("str_")


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    source: str = (args.get("source") or "").strip()
    if not source:
        raise KeelError(
            "Missing required `source` (strategy_id or share_id).",
            error_code="missing_source",
            exit_code=2,
            suggestion=(
                "Pass `source=str_abc` to fork an existing strategy, OR "
                "`source=<share_link_id>` from a `keel://share/<id>` URL. "
                "Find ids via `keel_strategy_search`."
            ),
        )

    name: str | None = args.get("name")
    target_workspace_id: str | None = args.get("target_workspace_id")

    client = ctx.get_client()

    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if target_workspace_id:
        body["target_workspace_id"] = target_workspace_id

    if _looks_like_strategy_id(source):
        try:
            # Always send an object body (even if empty). The
            # /v1/strategies/{id}/fork endpoint requires a JSON body
            # (ForkStrategyRequest); passing `None` causes the API to
            # 422 with `Field required` since the body is missing
            # entirely. {} is accepted (all fields optional).
            result = client.post(f"/v1/strategies/{source}/fork", json=body)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to fork strategy {source}: {e}",
                suggestion=(
                    "Verify the strategy id is correct + you have read access "
                    "(`keel_strategy_get {source}`). If the source strategy "
                    "is in another org, you need a share link instead — "
                    "pass `source=<share_link_id>`."
                ),
            )
    else:
        # share-id path: fork-with-edits
        try:
            payload = dict(body)
            payload["share_link_id"] = source
            result = client.post("/v1/strategies/fork-with-edits", json=payload)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to fork share {source}: {e}",
                suggestion=(
                    "Verify the share link is still valid and not expired. "
                    "Share links can also be revoked by the creator. If you "
                    "have a strategy id instead, pass it as `source=str_...`."
                ),
            )

    new_sid = result.get("strategy_id") or result.get("id")
    extra: dict[str, Any] = {
        "strategy_id": new_sid,
        "parent": source,
    }

    return OutcomeResult(
        run_id=new_sid,
        hero_url=f"{ctx.app_url}/strategies/{new_sid}" if new_sid else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=extra,
    )


STRATEGY_FORK = register(
    OutcomeTool(
        name="keel_strategy_fork",
        required_action="strategy.create",
        cli_path=("strategy", "fork"),
        toolset="backtest",
        description=(
            "Fork a strategy into your org. The `source` argument accepts either "
            "a Keel strategy id (`str_*`) or a share-link id; the tool auto-detects "
            "which endpoint to call. "
            "Do NOT use to compose a new strategy from scratch — call "
            "`keel_strategy_compose`. "
            "Do NOT use to create a share link — call `keel_share_create`."
        ),
        input_schema={
            "type": "object",
            "required": ["source"],
            "properties": {
                "source": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "Strategy id (`str_*`) or share-link id.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional new name for the fork.",
                },
                "target_workspace_id": {
                    "type": "string",
                    "description": "Workspace to place the fork in (default: org default).",
                },
            },
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
