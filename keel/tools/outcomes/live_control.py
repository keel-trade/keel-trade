"""`keel_live_control` — pause / resume / stop / trigger a live deployment.

Per spec §4 #14: the destructive control surface for live deployments.
One tool, one `action` enum, one positional `deployment_id`. Always
routes through host confirmation via `destructiveHint=true`.

Routes used (verified against the API live router):
  - pause   → POST   /v1/live/{id}/pause
  - resume  → POST   /v1/live/{id}/resume
  - stop    → DELETE /v1/live/{id}
  - trigger → POST   /v1/live/{id}/trigger

Do NOT use to deploy a new strategy — call `keel_live_deploy` instead.
Do NOT use to read state — call `keel_live_monitor` instead.
"""

from __future__ import annotations

from keel.errors import EntitlementError, KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


# action → (method, path-suffix)
_ACTIONS: dict[str, tuple[str, str]] = {
    "pause": ("POST", "/pause"),
    "resume": ("POST", "/resume"),
    "stop": ("DELETE", ""),
    "trigger": ("POST", "/trigger"),
}

# Where the deployment ends up in lifecycle terms after each action.
_NEW_STATE: dict[str, str] = {
    "pause": "paused",
    "resume": "active",
    "stop": "stopped",
    "trigger": "rebalance_queued",
}


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    action = (args.get("action") or "").strip()

    if not deployment_id:
        raise KeelError(
            "Missing required `deployment_id` argument.",
            error_code="missing_deployment_id",
            exit_code=2,
            suggestion="Pass deployment_id as the positional argument.",
        )
    if action not in _ACTIONS:
        raise ValidationError(
            f"Unknown action {action!r}. Valid actions: {sorted(_ACTIONS)}",
            suggestion=(
                f"Pass `action` as one of: {', '.join(sorted(_ACTIONS))}. "
                "Each maps to a specific live-deployment state transition."
            ),
        )

    # Second lock — `stop` and `trigger` mutate live state; require
    # local arming. `pause` and `resume` are arming-gated too because
    # they affect a real deployment's behavior.
    from keel.permissions import assert_armed_for_account

    assert_armed_for_account(None)  # no account_id in path; cross-account allowed

    method, suffix = _ACTIONS[action]
    path = f"/v1/live/{deployment_id}{suffix}"

    client = ctx.get_client()
    try:
        if method == "POST":
            result = client.post(path)
        elif method == "DELETE":
            result = client.delete(path)
        else:  # pragma: no cover — _ACTIONS is closed
            raise KeelError(
                f"Internal error: unsupported method {method!r}.",
                error_code="internal_error",
                exit_code=1,
                suggestion=(
                    "This is a bug in the SDK — the action table is supposed to be "
                    "closed. Report it with the failing command + version "
                    "(`keel --version`)."
                ),
            )
    except EntitlementError as e:
        # Scope wall (spec 03 R1): controlling a live deployment without
        # the live scope is a human-consent handoff. Quota-shaped 403s
        # (unlikely here) map to the billing handoff with exact numbers.
        from ._handoff import live_scope_handoff, maybe_quota_handoff

        retry_call = {
            "tool": "keel_live_control",
            "args": {"deployment_id": deployment_id, "action": action},
        }
        handoff = maybe_quota_handoff(e, blocked_action="live_control", retry_call=retry_call)
        if handoff is None:
            handoff = live_scope_handoff(
                e,
                blocked_action="live_control",
                action_url=f"{ctx.app_url}/live/{deployment_id}",
                retry_call=retry_call,
            )
        raise handoff from e

    return OutcomeResult(
        run_id=deployment_id,
        hero_url=f"{ctx.app_url}/live/{deployment_id}",
        share_url=None,
        extra={
            "action": action,
            "new_state": _NEW_STATE[action],
            "result": result,
        },
    )


LIVE_CONTROL = register(
    OutcomeTool(
        name="keel_live_control",
        required_action="runner.pause",
        cli_path=("live", "control"),
        toolset="live-write",
        description=(
            "Pause, resume, stop, or trigger a manual rebalance on a live deployment. "
            "Always routes through host confirmation via `destructiveHint=true` — "
            "`stop` ends the deployment, `pause`/`resume` toggle the schedule, "
            "`trigger` forces an immediate rebalance off-schedule. "
            "Do NOT use to deploy a new strategy — call `keel_live_deploy`. "
            "Do NOT use to read state — call `keel_live_monitor`."
        ),
        input_schema={
            "type": "object",
            "required": ["deployment_id", "action"],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": "Deployment to control. From `keel_live_monitor`.",
                },
                "action": {
                    "type": "string",
                    "enum": sorted(_ACTIONS.keys()),
                    "description": (
                        "Lifecycle action: 'pause' (halt schedule), 'resume' "
                        "(re-enable schedule), 'stop' (terminate deployment), "
                        "'trigger' (force one immediate rebalance)."
                    ),
                },
            },
        },
        annotations={
            "title": "Control Live Strategy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
