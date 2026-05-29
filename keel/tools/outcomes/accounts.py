"""`keel_accounts_list` — enumerate (or detail) Hyperliquid accounts.

Replaces three legacy CLI/MCP commands: `accounts list`, `accounts show`,
and `accounts check-deposit`. The collapsed surface picks the right
endpoint based on whether `account_id` was supplied.

Per spec §4 row 11 + §4 auxiliary table:

- Read-only, idempotent.
- Returns the authenticated `app.usekeel.io/accounts` URL as `hero_url`
  so the user can click straight into the wallet management page.
- `share_url` is always `None` — sharing accounts is intentionally
  unsupported (privacy / security).
- Do NOT use to authorize a new account — that's a web-only flow that
  requires the user to sign an EIP-712 challenge with their wallet.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    account_id: str | None = args.get("account_id")
    client = ctx.get_client()

    # Detail mode — one account.
    if account_id:
        try:
            account = client.get(f"/v1/accounts/{account_id}")
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to fetch account {account_id}: {e}",
                suggestion=(
                    "Verify the account_id with `keel_accounts_list` "
                    "(no argument) to see all available accounts."
                ),
            )
        return OutcomeResult(
            run_id=None,
            hero_url=f"{ctx.app_url}/accounts/{account_id}",
            share_url=None,
            extra={"account": account},
        )

    # List mode — all accounts in org.
    params: dict[str, Any] = {}
    limit = args.get("limit")
    if limit is not None:
        params["limit"] = limit
    cursor = args.get("cursor")
    if cursor:
        params["cursor"] = cursor

    try:
        result = client.get("/v1/accounts", **params)
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to list accounts: {e}",
            suggestion="Run `keel_doctor` to check auth / API reachability.",
        )

    # API canonical shape is {data: [...], pagination: {cursor, has_more}}
    # — same PaginatedResponse used by every list endpoint.
    from ._pagination import extract_paginated

    items, next_cursor = extract_paginated(result)
    extra: dict[str, Any] = {"accounts": items}
    if next_cursor:
        extra["next_cursor"] = next_cursor

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/accounts",
        share_url=None,
        extra=extra,
    )


ACCOUNTS_LIST = register(
    OutcomeTool(
        name="keel_accounts_list",
        required_action="account.read",
        cli_path=("accounts", "list"),
        toolset="read-only",
        description=(
            "List your Hyperliquid accounts (id, label, wallet_address, "
            "status, account_mode, agent_address, expires_at, attached "
            "strategy/deployment). Pass an `account_id` to fetch detail "
            "for a single account. "
            "Do NOT use to authorize a new account — that's a web-only "
            "flow that requires the user to sign an EIP-712 challenge "
            "with their wallet. "
            "Do NOT use to deploy to a live account — call "
            "`keel_live_deploy` with the chosen account_id."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "account_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Optional account id. When set, returns the "
                        "single account's detail; otherwise lists all."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max results when listing (default 20).",
                },
                "cursor": {
                    "type": "string",
                    "description": (
                        "Pagination cursor returned by a previous list call."
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
