"""The handoff envelope — spec 03 R1 (agent-first-build M3.1).

ONE shared response structure returned by ANY outcome tool that hits a
human-required wall (plan/quota limit, live consent scope, account
linking). Agents parse a single shape across every wall instead of
tool-specific error prose:

    {
      blocked_action:  what the agent tried ("backtest_run", "live_deploy", ...)
      reason:          why a human is required (one sentence)
      required_actor:  "human"                     (always — the wall's contract)
      action_url:      where the human acts (billing page, handoff deep
                       link, strategy overview — always an owned URL)
      limit_details:   exact numbers FROM THE API (never invented) — quota
                       walls only
      cost:            exact numbers FROM THE API (never invented) — e.g.
                       the server-computed sizing suggestion — when relevant
      talking_points:  honest lines the agent can relay verbatim. ALWAYS
                       includes the do-nothing alternative; never "earn X%";
                       drawdown named whenever performance numbers appear.
      resume:          how the agent resumes after the human acts:
                       {token} (pollable deploy-intent token) and/or
                       {verify_call: {tool, args, reason}}
    }

These keys ride TOP-LEVEL on the standard spec §13.5 error envelope
(``code=handoff_required`` plus message/what_was_expected/example/
suggested_next_action), so existing envelope consumers keep working and
handoff-aware agents get the R1 fields without unwrapping.

Precedents this follows: ``HostedAuthError`` (keel/hosting.py, M1.1) and
``workspace.build_conflict_envelope`` (spec 08 R4) — a KeelError subclass
carrying structured context, raised by handlers, serialized identically
by the CLI and MCP adapters.

Adopters (M3.1): ``keel_backtest_run`` (quota), ``keel_strategy_compose``
(plan caps), ``keel_live_deploy`` (live scope / unlinked account / preview
handoff_url), ``keel_live_control`` (scope). ``keel_plan_status`` adopts
in M4.2. The LISTED directory profile never emits deploy-intent links —
``mint_deploy_intent`` is profile-gated (research/08 policy boundary;
``keel_open_in_app`` is the listed bridge).
"""

from __future__ import annotations

import re
from typing import Any

from keel.errors import EntitlementError, KeelError

from ._base import ToolContext
from ._toolsets import is_listed_profile


__all__ = [
    "HandoffRequired",
    "live_scope_handoff",
    "maybe_quota_handoff",
    "mint_deploy_intent",
    "unlinked_account_handoff",
    "validate_talking_points",
]


# Honesty guard (research/08 + agent-first-keel honesty rules): talking
# points must never promise returns. Word-boundary so "earned fees ledger"
# style identifiers elsewhere aren't affected — this scans only the
# talking_points strings passed to HandoffRequired.
_FORBIDDEN_TALKING_POINT_RE = re.compile(r"\bearns?\b|\bguaranteed?\b", re.IGNORECASE)
_DO_NOTHING_RE = re.compile(r"\bdo(ing)?\s+nothing\b", re.IGNORECASE)


def validate_talking_points(talking_points: list[str]) -> list[str]:
    """Structural honesty validation for `talking_points` (spec 03 R1).

    ONE validator for every surface that emits talking points — the
    handoff envelope below and `keel_plan_status`'s upsell-suppressed
    output (spec 04 R2) both go through here, so the honesty rules can
    never fork: non-empty strings, the do-nothing alternative named, no
    return-promising language. Returns the list; raises ``ValueError``
    otherwise (validators behave one exact way — repo lesson)."""
    if (
        not isinstance(talking_points, list)
        or not talking_points
        or not all(isinstance(tp, str) and tp.strip() for tp in talking_points)
    ):
        raise ValueError("talking_points must be a non-empty list of non-empty strings")
    if not any(_DO_NOTHING_RE.search(tp) for tp in talking_points):
        raise ValueError(
            "talking_points must include the do-nothing alternative (spec 03 R1 honesty rule)"
        )
    for tp in talking_points:
        if _FORBIDDEN_TALKING_POINT_RE.search(tp):
            raise ValueError(f"talking point uses forbidden return-promising language: {tp!r}")
    return list(talking_points)


class HandoffRequired(KeelError):
    """A tool hit a wall only a human can clear (spec 03 R1).

    Constructor validates the envelope invariants structurally — a
    handoff without a do-nothing talking point, or with return-promising
    language, is a programming error and raises immediately (repo lesson:
    validators behave one exact way; no silent fallback).
    """

    error_code = "handoff_required"
    exit_code = 6

    def __init__(
        self,
        message: str,
        *,
        blocked_action: str,
        reason: str,
        action_url: str,
        talking_points: list[str],
        resume: dict[str, Any],
        limit_details: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
        suggestion: str | None = None,
        docs_url: str | None = None,
        input: dict | None = None,
    ) -> None:
        if not blocked_action or not isinstance(blocked_action, str):
            raise ValueError("HandoffRequired requires a non-empty blocked_action")
        if not reason or not isinstance(reason, str):
            raise ValueError("HandoffRequired requires a non-empty reason")
        if not action_url or not isinstance(action_url, str):
            raise ValueError("HandoffRequired requires a non-empty action_url")
        try:
            validate_talking_points(talking_points)
        except ValueError as e:
            raise ValueError(f"HandoffRequired {e}") from None
        if not isinstance(resume, dict) or not (resume.get("token") or resume.get("verify_call")):
            raise ValueError("HandoffRequired resume must carry a `token` and/or a `verify_call`")

        super().__init__(message, suggestion=suggestion, docs_url=docs_url, input=input)
        self.blocked_action = blocked_action
        self.reason = reason
        self.action_url = action_url
        self.talking_points = list(talking_points)
        self.resume = dict(resume)
        self.limit_details = dict(limit_details) if limit_details is not None else None
        self.cost = dict(cost) if cost is not None else None

    def to_envelope(self) -> dict:
        envelope = super().to_envelope()
        envelope["blocked_action"] = self.blocked_action
        envelope["reason"] = self.reason
        envelope["required_actor"] = "human"
        envelope["action_url"] = self.action_url
        if self.limit_details is not None:
            envelope["limit_details"] = self.limit_details
        if self.cost is not None:
            envelope["cost"] = self.cost
        envelope["talking_points"] = list(self.talking_points)
        envelope["resume"] = dict(self.resume)
        return envelope


# ─── Deploy-intent minting (spec 03 R2 client half) ─────────────────────


def mint_deploy_intent(ctx: ToolContext, strategy_id: str) -> dict[str, Any] | None:
    """Mint a signed deploy-intent via ``POST /v1/live/deploy-intents``.

    Returns the mint response dict (``handoff_url``, ``intent_token``,
    ``expires_at``, ``suggested_config``, ...) or ``None`` when a link
    cannot/must not be issued:

    * LISTED profile → ALWAYS ``None`` (policy: the directory-listed
      surface never emits deploy-intent links; ``keel_open_in_app`` is
      its only app bridge — research/08).
    * Endpoint unavailable / caller lacks the scope / any API error →
      ``None``. This is a legitimate best-effort fallback chain, not a
      silent behavior fork: both outcomes are correct destinations —
      callers fall back to an owned app URL and the envelope still
      validates. Sizing inside ``suggested_config`` is server-computed
      (drawdown rule); this client never sends sizing (the endpoint
      rejects any extra field with a 422).
    """
    if is_listed_profile():
        return None
    try:
        resp = ctx.get_client().post("/v1/live/deploy-intents", json={"strategy_id": strategy_id})
    except Exception:  # noqa: BLE001 — best-effort deep link; owned-URL fallback is equivalent
        return None
    if not isinstance(resp, dict) or not resp.get("handoff_url"):
        return None
    return resp


def _intent_cost(intent: dict[str, Any] | None) -> dict[str, Any] | None:
    """Exact server-computed sizing numbers for the envelope's ``cost``.

    Straight from the mint response — never invented. ``None`` when the
    server had no sizing evidence (no drawdown-bearing backtest yet).
    """
    if not intent:
        return None
    suggested = intent.get("suggested_config")
    if not isinstance(suggested, dict) or suggested.get("sizing_usd") is None:
        return None
    return {
        "suggested_sizing_usd": suggested["sizing_usd"],
        "sizing_basis": suggested.get("sizing_basis"),
    }


# ─── Builders (the adoption surface) ─────────────────────────────────────


def _quota_details(e: EntitlementError) -> dict[str, Any] | None:
    """Parsed plan-limit numbers from ``translate_http_error``'s 403 branch.

    Returns ``None`` when the EntitlementError is NOT the quota shape
    (i.e. it's the scope-missing shape) — the discriminator is the parsed
    ``input`` dict that ``_translate_403`` attaches only for entitlement
    reasons, plus its explicit ``recovery_tool=None`` override.
    """
    if e.recovery_tool is not None:
        return None
    if not isinstance(e.input, dict) or not e.input.get("unit"):
        return None
    return e.input


def maybe_quota_handoff(
    e: EntitlementError,
    *,
    blocked_action: str,
    retry_call: dict[str, Any],
) -> HandoffRequired | None:
    """Build the plan-limit handoff from a quota-shaped EntitlementError.

    Returns ``None`` when ``e`` is not the quota shape (callers re-raise
    the original error). ``limit_details`` carries the EXACT numbers the
    API returned in its entitlement reasons — nothing is invented; fields
    the API didn't send are omitted.
    """
    parsed = _quota_details(e)
    if parsed is None:
        return None

    unit_label = parsed.get("unit_label") or parsed.get("unit")
    billing_url = parsed.get("billing_url") or (e.docs_url or "")
    limit = parsed.get("limit")
    current = parsed.get("current")
    need = parsed.get("need")

    limit_details: dict[str, Any] = {"unit": parsed.get("unit"), "unit_label": unit_label}
    if parsed.get("kind") is not None:
        limit_details["kind"] = parsed["kind"]
    if limit is not None:
        limit_details["limit"] = limit
    if current is not None:
        limit_details["current"] = current
    if need is not None:
        limit_details["need"] = need

    if limit is not None and current is not None:
        usage_point = f"You've used {current} of {limit} {unit_label} on the current plan."
    elif need is not None:
        usage_point = f"The current plan doesn't include {unit_label} (needs {need})."
    else:
        usage_point = f"The current plan's {unit_label} allowance is exhausted."

    talking_points = [
        usage_point,
        (
            "Only a human can change the plan — upgrading at the link adds "
            "capacity (plan prices and limits are shown there)."
        ),
        (
            "Doing nothing is also fine: nothing is lost — existing "
            "strategies and results stay available on the current plan."
        ),
    ]

    return HandoffRequired(
        str(e),
        blocked_action=blocked_action,
        reason=(
            f"Plan limit on {unit_label} — adding capacity is a billing "
            "action only a human can take."
        ),
        action_url=billing_url,
        talking_points=talking_points,
        resume={
            "verify_call": {
                **retry_call,
                "reason": "Re-run the blocked call after the human finishes at action_url.",
            }
        },
        limit_details=limit_details,
        suggestion=e.suggestion,
        docs_url=e.docs_url,
    )


def live_scope_handoff(
    e: KeelError,
    *,
    blocked_action: str,
    action_url: str,
    retry_call: dict[str, Any],
) -> HandoffRequired:
    """Build the live-consent handoff for a scope-missing 403.

    The token can't be widened by the agent — granting the live scope is
    a browser consent only the human can approve. ``action_url`` is the
    in-app place where the human can perform the action directly (their
    web session doesn't depend on the agent token's scopes).

    The ``resume.verify_call`` is surface-aware so it is executable
    EXACTLY as written (spec 03 R6): locally the recovery tool is
    ``keel_auth_login`` (browser consent); on HOSTED servers that tool is
    not registered — re-auth is the MCP *client's* OAuth flow (the
    HostedAuthError precedent), so the verify_call is the retry itself
    with the client re-auth named in its reason.
    """
    from keel.hosting import is_hosted

    hosted = is_hosted()
    if hosted:
        ways_forward = (
            "Two ways forward: re-authenticate this MCP server approving "
            "the live-trading scope (in Claude Code: /mcp → re-authenticate "
            "the keel server), or act directly in the Keel app at the link."
        )
        verify_call: dict[str, Any] = {
            **retry_call,
            "reason": (
                "Hosted MCP sessions re-authorize through the client's OAuth "
                "flow, not a tool call: after the human re-authenticates this "
                "server with the live scope approved, re-run this exact call — "
                "it succeeds once the token carries the scope."
            ),
        }
    else:
        ways_forward = (
            "Two ways forward: approve a re-login with live scope "
            "(`keel_auth_login` with scope='live', or `keel auth login "
            "--scope live` in a terminal), or act directly in the Keel "
            "app at the link."
        )
        verify_call = {
            "tool": "keel_auth_login",
            "args": {"scope": "live"},
            "reason": (
                "Opens the browser consent for the live scope; after the "
                f"human approves, retry `{retry_call.get('tool', blocked_action)}`."
            ),
            "then_retry": retry_call,
        }

    talking_points = [
        (
            "Live-trading actions require an explicit human consent (the "
            "'live' scope) that agents cannot grant themselves."
        ),
        ways_forward,
        (
            "Doing nothing is also fine — nothing deploys or changes on a "
            "live account without your explicit approval."
        ),
    ]
    return HandoffRequired(
        str(e),
        blocked_action=blocked_action,
        reason=(
            "The session token lacks the live-trading scope; granting it "
            "is a human browser-consent step."
        ),
        action_url=action_url,
        talking_points=talking_points,
        resume={"verify_call": verify_call},
        suggestion=getattr(e, "suggestion", None),
        docs_url=getattr(e, "docs_url", None),
    )


def unlinked_account_handoff(
    *,
    blocked_action: str,
    strategy_id: str,
    ctx: ToolContext,
    detail: str | None = None,
) -> HandoffRequired:
    """Build the account-linking handoff (deploy without a linked account).

    Linking a Hyperliquid account requires the user's own wallet
    signatures (two EIP-712 payloads) — inherently human. On non-listed
    profiles this mints a deploy-intent deep link (spec 03 R2) so the
    human lands in the standalone handoff flow that chains account link →
    deploy; when minting isn't possible the owned ``/deploy/{strategy_id}``
    entry path is the fallback (same flow, no prefill token).
    """
    intent = mint_deploy_intent(ctx, strategy_id)
    if intent:
        action_url = intent["handoff_url"]
    else:
        action_url = f"{ctx.app_url}/deploy/{strategy_id}"

    if intent and intent.get("intent_token"):
        # Round-trip resumption (spec 03 R6): the verify_call IS the poll —
        # `keel_live_deploy` with the intent token (preview phase) reads the
        # handoff's server-side status and returns `handoff_state`;
        # 'completed' carries the deployment_id, no browser return needed.
        resume: dict[str, Any] = {
            "token": intent["intent_token"],
            "verify_call": {
                "tool": "keel_live_deploy",
                "args": {"strategy_id": strategy_id, "intent_token": intent["intent_token"]},
                "reason": (
                    "Polls this handoff's server-side status: returns "
                    "handoff_state.status == 'completed' with the "
                    "deployment_id once the human finishes at action_url "
                    "(pending while they work; an expired link explains how "
                    "to mint a fresh one)."
                ),
            },
        }
    else:
        resume = {
            "verify_call": {
                "tool": "keel_accounts_list",
                "args": {},
                "reason": (
                    "After the human links an account, it appears here — then "
                    f"retry `{blocked_action}` with its account_id."
                ),
            }
        }

    reason_text = (
        "No linked, authorized Hyperliquid account — linking requires the "
        "user's own wallet signatures, which agents cannot perform."
    )
    talking_points = [
        (
            "Going live needs a linked Hyperliquid account; linking is done "
            "by you (two wallet signatures) at the link — the flow chains "
            "account linking and the deploy review in one pass."
        ),
        (
            "Every deploy step is reviewed and confirmed by you there; any "
            "sizing suggestion shown is computed from the backtest's max "
            "drawdown, and losses up to at least that drawdown should be "
            "expected at any size."
        ),
        (
            "Doing nothing is also fine — nothing is deployed and no funds "
            "move; the strategy and its backtests stay saved."
        ),
    ]
    return HandoffRequired(
        detail or reason_text,
        blocked_action=blocked_action,
        reason=reason_text,
        action_url=action_url,
        talking_points=talking_points,
        resume=resume,
        cost=_intent_cost(intent),
        suggestion=(
            "Send the user to action_url to link an account and review the "
            "deploy; then run resume.verify_call exactly as written to "
            "observe completion (no browser return needed)."
        ),
    )
