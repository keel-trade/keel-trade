"""`keel_accounts_safety` — read one account's execution HALT state.

Q-0913 / audit A8 F-7: `keel accounts list` returns no safety state and
nothing on the CLI or MCP surface ever called
`GET /internal/v1/execution-safety/{account_id}` (keel-api
`routers/execution_safety.py::read_execution_account_safety`). An agent
therefore reported a halted account as healthy — chat-api's
`get_deployment_status` has the same gap (A8 F-2) — while `keel arm live`
is a LOCAL file that has nothing to do with the SERVER halt.

READ ONLY, DELIBERATELY
-----------------------
The sibling `POST .../{account_id}/unhalt` is NOT exposed here and must
not be. Releasing a halt is an operator ceremony with a compare-and-set on
the exact halt event, an idempotency key, a reason, and — for the service
arm — the named human whose authority it acts on; the database re-decides
every condition through the command trigger. That is not a CLI flag, and a
tool that made it one would invite an agent to clear a funds-safety halt.

THE 403 IS EXPECTED FOR ALMOST EVERYONE
---------------------------------------
The route admits only an allowlisted platform or on-call operator
(`auth/operator.py::require_execution_unhalt_operator`): a Clerk user
present in `platform.execution_routing_operators` or the V150
`execution_oncall_operators`. Org role, plan, and OAuth scope grant
nothing here. The SDK's generic 403 translation suggests re-authenticating
with a wider scope, which for THIS route is false — re-login cannot add
operator authority. The handler below replaces it with the truth.
"""

from __future__ import annotations

from keel.errors import EntitlementError, KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


#: What the caller is told when the operator gate refuses. Deliberately
#: names the gate and says re-auth is not the fix, instead of inheriting
#: the generic "re-login with a wider scope" 403 copy.
_OPERATOR_ONLY_MESSAGE = (
    "Execution safety state is operator-only. This account's HALT rail is "
    "readable by allowlisted platform and on-call operators, and by nobody "
    "else — org role, plan tier and OAuth scope grant no access to it."
)
_OPERATOR_ONLY_SUGGESTION = (
    "Do NOT retry and do NOT re-authenticate: no login scope adds operator "
    "authority. Tell the user the account's halt state is visible to Keel "
    "operators only, and ask a Keel operator if a deployment looks halted. "
    "Bar-level outcomes that do not need this gate are on "
    "`keel_live_monitor` with view='executions'."
)


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    account_id = (args.get("account_id") or "").strip()
    if not account_id:
        raise ValidationError(
            "`keel_accounts_safety` needs an `account_id`.",
            suggestion=(
                "List accounts with `keel_accounts_list`, or read `account_id` "
                "off a row from `keel_deployments_list`."
            ),
        )

    client = ctx.get_client()
    try:
        safety = client.get(f"/internal/v1/execution-safety/{account_id}")
    except EntitlementError as e:
        # The operator gate, not an entitlement or a scope tier. Chain so
        # the server's own denial text stays inspectable.
        raise KeelError(
            _OPERATOR_ONLY_MESSAGE,
            error_code="operator_only",
            exit_code=6,
            suggestion=_OPERATOR_ONLY_SUGGESTION,
            input={"account_id": account_id, "server_denial": str(e)},
        ) from e
    except KeelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeelError(
            f"Failed to read execution safety for {account_id}: {e}",
            suggestion=(
                "Verify the account_id with `keel_accounts_list`. A 404 means "
                "this account has no execution-safety row at all."
            ),
        )

    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/accounts/{account_id}",
        share_url=None,
        extra={"account_id": account_id, "execution_safety": safety},
    )


ACCOUNTS_SAFETY = register(
    OutcomeTool(
        name="keel_accounts_safety",
        required_action="account.read",
        cli_path=("accounts", "safety"),
        toolset="live-read",
        # grounded-in: accounts_safety.py module docstring (read-only by
        # design; the unhalt ceremony is not a CLI flag) + keel-api
        # routers/execution_safety.py::read_execution_account_safety (the
        # ExecutionAccountSafetyView fields returned verbatim) +
        # auth/operator.py::require_execution_unhalt_operator (the
        # allowlist that produces the 403 this handler retranslates).
        description=(
            "Read one Hyperliquid account's execution SAFETY state: whether the "
            "account is halted, which halt event is current, how many frozen "
            "sessions and unresolved cancels the release still waits on, the "
            "halt's authority tier, and the block reasons. "
            "A halted account executes nothing, so this is the answer to 'why "
            "has this deployment done nothing since <time>' when the bar-level "
            "view shows no attempts at all. "
            "OPERATOR-GATED: allowlisted platform and on-call operators only. "
            "Everyone else gets a refusal that no re-login can fix — report it "
            "as 'operator-only', never as an authentication problem. "
            "Do NOT use to release a halt: clearing one is an operator ceremony "
            "with a compare-and-set on the exact halt event, and it is "
            "deliberately not exposed on this surface. "
            "Do NOT use for local arming — `keel arm live` is a local file and "
            "is unrelated to this server-side halt."
        ),
        input_schema={
            "type": "object",
            "required": ["account_id"],
            "properties": {
                "account_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": (
                        "Account to inspect — from `keel_accounts_list` or a "
                        "`keel_deployments_list` row."
                    ),
                },
            },
        },
        annotations={
            "title": "Read Account Execution Safety",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
