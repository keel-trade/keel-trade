"""`keel_live_update` — update a live deployment (web handoff).

Deploy-wizard-v2 spec 04 §4: the SDK/MCP producer of the UPDATE intent.
Applying a new strategy version to a LIVE/PAUSED deployment is a human
step in the Keel web app (the same D7 rule that makes going live a web
handoff): this tool never applies an update and accepts NO configuration.
Invoked with `deployment_id` it mints a signed update link via
``POST /v1/deployments/update-intents`` — the body is
``{"deployment_id"}`` and nothing else; the server rejects any extra
field with a 422, so agent-supplied config is structurally impossible —
and returns the shared handoff envelope (`code=handoff_required`) whose
`action_url` opens the web update flow (version diff, any schedule
change, carried execution blocks, all server-derived). `resume` carries
the pollable intent token + its expiry.

Handoff resume (spec 03 R6 semantics, update endpoints): calling with
`intent_token` is a pure status poll of
``POST /v1/deployments/update-intents/status`` — it returns
`handoff_state` (pending|completed|expired); `completed` carries the
deployment status and the applied version pointer, so the agent observes
the human finishing without a browser return.

Mint refusals are NOT masked: a non-LIVE/PAUSED deployment (409) or a
deployment the caller's org doesn't own (404) propagates with the
server's own remediation text — the link is the outcome, so a generic
fallback URL would hide the real answer.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .open_in_app import app_url_for


def _poll_update_intent_status(
    intent_token: str, *, deployment_id: str, ctx: ToolContext
) -> OutcomeResult:
    """Pure server-side status read for an update-intent handoff.

    The executable form of the handoff envelope's ``resume.verify_call``:
    one POST to the status endpoint, nothing else. Completion is DERIVED
    server-side (an UPDATE applied to the deployment at/after mint), so a
    fulfilled link reopened reports completed + the applied version — no
    error, no duplicate update (spec 04 §2/§6).
    """
    from keel.errors import EntitlementError, UsageError

    client = ctx.get_client()
    try:
        resp = client.post(
            "/v1/deployments/update-intents/status", json={"intent_token": intent_token}
        )
    except EntitlementError as e:
        # The status endpoint checks nothing beyond auth + token↔org
        # binding, so an EntitlementError here IS the wrong-org 403 (R7:
        # the class-default scope-relogin remediation would mislead).
        raise KeelError(
            str(e),
            error_code="update_intent_wrong_org",
            exit_code=6,
            suggestion=(
                "This session is authenticated to a different Keel org than "
                "the one that minted the update link. Mint a fresh link from "
                "THIS session (`keel_live_update` with the deployment_id), "
                "or re-login as the org that owns the deployment and retry."
            ),
        ) from e
    except UsageError as e:
        # 400: tampered / truncated / not-an-update-link token (server
        # remediation text rides the message).
        raise KeelError(
            str(e),
            error_code="update_intent_invalid",
            exit_code=7,
            suggestion=(
                "The intent token failed verification (truncated, altered, "
                "or not an update link). Mint a fresh one: `keel_live_update` "
                "with the deployment_id returns `action_url` and "
                "`resume.token`."
            ),
        ) from e

    status = resp.get("status") if isinstance(resp, dict) else None
    if status not in {"pending", "completed", "expired"}:
        # One exact contract; an unknown shape is an error, never guessed at.
        raise KeelError(
            f"Unexpected update-intent status response: {resp!r}",
            error_code="update_intent_status_unexpected",
            exit_code=1,
            retryable=True,
            suggestion=(
                "The server returned an unknown handoff status shape — retry "
                "once; if it persists the API and SDK versions have drifted "
                "(run `keel_connection_check`)."
            ),
        )

    resolved_deployment = resp.get("deployment_id") or deployment_id
    handoff_state: dict[str, Any] = {
        "status": status,
        "intent_id": resp.get("intent_id"),
        "deployment_id": resolved_deployment,
        "strategy_id": resp.get("strategy_id"),
        "expires_at": resp.get("expires_at"),
    }
    extra: dict[str, Any] = {"handoff_state": handoff_state}

    if status == "completed":
        handoff_state["deployment_status"] = resp.get("deployment_status")
        handoff_state["updated_version_string"] = resp.get("updated_version_string")
        extra["note"] = (
            "The human completed the handoff — the update is applied and "
            "takes effect at the next evaluation. No browser return needed; "
            "monitor it from here."
        )
        extra["next_action"] = {
            "tool": "keel_live_monitor",
            "args": {"deployment_id": resolved_deployment},
            "reason": "Inspect the running deployment (status, evaluations, orders).",
        }
        hero = app_url_for("live", resolved_deployment, ctx)
        return OutcomeResult(run_id=resolved_deployment, hero_url=hero, share_url=None, extra=extra)

    if status == "expired":
        handoff_state["remediation"] = resp.get("remediation") or (
            "This update link has expired (links live for up to 1 hour). "
            "Mint a fresh one and hand it to the user again."
        )
        extra["next_action"] = {
            "tool": "keel_live_update",
            "args": {"deployment_id": resolved_deployment},
            "reason": (
                "The link lapsed unused (≤1h lifetime). A fresh call mints a "
                "new update link to hand to the user."
            ),
        }
        return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)

    # pending — the link is live and the human hasn't finished yet.
    extra["note"] = (
        "The human hasn't completed the update yet (the link is still "
        "live). Poll this same call again in a bit."
    )
    extra["next_action"] = {
        "tool": "keel_live_update",
        "args": {"deployment_id": resolved_deployment, "intent_token": intent_token},
        "reason": "Re-poll the handoff status; it flips to 'completed' when the human finishes.",
    }
    return OutcomeResult(run_id=None, hero_url=None, share_url=None, extra=extra)


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = (args.get("deployment_id") or "").strip()
    intent_token = (args.get("intent_token") or "").strip() or None

    if not deployment_id:
        raise KeelError(
            "Missing required `deployment_id` argument.",
            error_code="missing_deployment_id",
            exit_code=2,
            suggestion=(
                "Pass deployment_id positional (CLI) or {deployment_id: ...} "
                "(MCP). Get it from `keel_live_monitor`."
            ),
        )

    # ── Handoff status poll (spec 03 R6 semantics) ─────────────────────
    if intent_token:
        return _poll_update_intent_status(intent_token, deployment_id=deployment_id, ctx=ctx)

    # ── Anonymous sessions: the wall is sign-in (spec 09 CL-1/CL-2) ────
    from ._handoff import _is_anon_session, anon_signin_handoff, update_web_handoff

    if _is_anon_session():
        raise anon_signin_handoff(
            blocked_action="live_update",
            reason=(
                "Updating a live deployment requires a signed-in account — "
                "this workspace is anonymous."
            ),
            context_point=(
                "Updating a live deployment is done by you in the Keel web "
                "app after signing in; the agent never applies the change."
            ),
            retry_call={"tool": "keel_live_update", "args": {"deployment_id": deployment_id}},
        )

    # ── Mint the update link — the tool's whole outcome ────────────────
    # Body is EXACTLY {"deployment_id"}: the endpoint is extra="forbid",
    # and this producer carries the spec 04 §4 invariant — the agent can
    # mint and share a URL; it cannot update and cannot put config into
    # the intent. Refusals (409 non-live, 404 not-yours/missing) propagate
    # with the server's own remediation text — no silent fallback.
    try:
        resp = ctx.get_client().post(
            "/v1/deployments/update-intents", json={"deployment_id": deployment_id}
        )
    except KeelError as e:
        # D-42 / 04-R34: moving a deployment onto a version that pins a
        # known-issue component is refused until the strategy is upgraded;
        # that refusal (wherever keel-api raises it) names the upgrade. Every
        # other refusal propagates with the server's own text, as before.
        from ._known_issue import known_issue_refusal

        refusal = known_issue_refusal(e)
        if refusal is not None:
            raise refusal from e
        raise
    if not isinstance(resp, dict) or not resp.get("handoff_url") or not resp.get("intent_token"):
        raise KeelError(
            f"Unexpected update-intent mint response: {resp!r}",
            error_code="update_intent_mint_unexpected",
            exit_code=1,
            retryable=True,
            suggestion=(
                "The server returned an unknown mint shape — retry once; if "
                "it persists the API and SDK versions have drifted (run "
                "`keel_connection_check`)."
            ),
        )
    raise update_web_handoff(deployment_id=deployment_id, intent=resp, ctx=ctx)


LIVE_UPDATE = register(
    OutcomeTool(
        name="keel_live_update",
        required_action="runner.read",
        cli_path=("live", "update"),
        toolset="live-write",
        # grounded-in: deploy-wizard-v2 specs/04-intents-for-all-flows.md §4
        # (producers: mint + share, never apply, never config) + the D7 rule
        # (live actions are human steps in the web app).
        description=(
            "Update a LIVE or PAUSED deployment to its strategy's current version. "
            "The update is reviewed and confirmed by the human in the Keel WEB APP — "
            "this tool does NOT apply updates and accepts NO configuration: it mints "
            "a signed update link and returns a handoff (`code=handoff_required`) "
            "whose `action_url` opens the web update flow (version diff, any "
            "evaluation-schedule change, carried execution configuration — all "
            "server-derived), plus a `resume` you can poll to observe completion. "
            "Send the user to `action_url`; do not try to update in the terminal. "
            "Handoff resume: calling with `intent_token` (from the envelope's "
            "`resume.token`) is a pure status poll — it returns `handoff_state` "
            "(pending|completed|expired); 'completed' carries the applied version, "
            "so the agent observes the human finishing without a browser return. "
            "Do NOT use to pause/resume/stop/trigger a deployment — use "
            "`keel_live_control`. Do NOT use to go live with a new strategy — use "
            "`keel_live_deploy`."
        ),
        input_schema={
            "type": "object",
            "required": ["deployment_id"],
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "description": ("LIVE/PAUSED deployment to update. From `keel_live_monitor`."),
                },
                "intent_token": {
                    "type": "string",
                    "description": (
                        "Update-intent token from a handoff envelope's "
                        "`resume.token`. When present the call becomes a pure "
                        "status poll: it returns `handoff_state` "
                        "(pending|completed|expired) for that link instead of "
                        "minting a new one — 'completed' includes the applied "
                        "version, so the agent observes the human finishing "
                        "the flow without any browser return."
                    ),
                },
            },
        },
        annotations={
            "title": "Update Live Strategy",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
