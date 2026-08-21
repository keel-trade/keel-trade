"""`keel_live_control` — pause / resume / stop / trigger a live deployment.

Per spec §4 #14: the destructive control surface for live deployments.
One tool, one `action` enum, one positional `deployment_id`. Always
routes through host confirmation via `destructiveHint=true`.

Scope (D7, 2026-07-19): every action here operates on an EXISTING
deployment the human already deployed and authorized in the web app —
this is operating a live deployment, not going live. Going live with a
NEW strategy is a WEB handoff (`keel_live_deploy`), never an in-terminal
write. So the lifecycle control below stays a direct action on all
surfaces; `trigger` (an off-schedule rebalance on an already-authorized
deployment) is the borderline case and is flagged for founder review.

Routes used (verified against the API live router):
  - pause   → POST   /v1/live/{id}/pause
  - resume  → POST   /v1/live/{id}/resume
  - stop    → DELETE /v1/live/{id}
  - trigger → POST   /v1/live/{id}/trigger

Do NOT use to go live / deploy a new strategy — call `keel_live_deploy`
(it hands off to the web deploy flow). Do NOT use to read state — call
`keel_live_monitor` instead.
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
    override = bool(args.get("override") or False)

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
    if override and action != "trigger":
        # A-3 (2026-08-10): `override` re-runs a bar that already has a run
        # (the API mints a trigger_override_id past the idempotent dedup);
        # it means nothing on lifecycle actions, and silently accepting it
        # there would teach callers a flag that does nothing.
        raise ValidationError(
            "`override` is only meaningful with action='trigger'.",
            suggestion=(
                "Drop `override`, or use action='trigger' to force a re-run "
                "of a bar that already has a run."
            ),
        )

    # Second lock — `stop` and `trigger` mutate live state; require
    # local arming. `pause` and `resume` are arming-gated too because
    # they affect a real deployment's behavior.
    from keel.permissions import assert_armed_for_account

    assert_armed_for_account(None)  # no account_id in path; cross-account allowed

    method, suffix = _ACTIONS[action]
    path = f"/v1/live/{deployment_id}{suffix}"
    if override:
        path = f"{path}?override=true"

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
            "args": {
                "deployment_id": deployment_id,
                "action": action,
                **({"override": True} if override else {}),
            },
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
        # grounded-in: live_control.py D7 scope note (:7-13 — every action
        # operates on an EXISTING deployment the human already authorized in
        # the web app; going live with a NEW strategy is a web handoff, never
        # an in-terminal write) + _ACTIONS/_NEW_STATE table (per-action
        # lifecycle effect).
        description=(
            "Control an EXISTING live deployment that the human already deployed and "
            "authorized in the Keel web app: pause, resume, stop, or trigger a manual "
            "rebalance. This operates a deployment that is already live — it does not "
            "go live with a new strategy (that is a human step in the web app). "
            "Always routes through host confirmation via `destructiveHint=true`: "
            "`stop` ends the deployment, `pause`/`resume` toggle its schedule, "
            "`trigger` forces one immediate off-schedule rebalance. Get the "
            "`deployment_id` and current state from `keel_live_monitor` first. "
            "Do NOT use to go live / deploy a NEW strategy — that is done by the human "
            "in the Keel web app; call `keel_live_deploy`, which returns a handoff into "
            "that web deploy flow. "
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
                "override": {
                    "type": "boolean",
                    "description": (
                        "With action='trigger' only: force a re-run of a bar "
                        "that ALREADY has a run. Without it a re-trigger of an "
                        "evaluated bar is an idempotent no-op (the run dedup "
                        "wins); with it the API mints a trigger_override_id so "
                        "the new run persists alongside the original. Loosens "
                        "nothing else — authorization and LIVE status still "
                        "apply."
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
