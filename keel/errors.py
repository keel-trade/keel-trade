"""Structured errors with exit codes for agent-friendly error handling.

Exit codes:
    0 = success
    1 = general failure
    2 = usage error (bad args)
    3 = not found
    4 = authentication failed
    5 = conflict (already exists)
    6 = insufficient entitlements
    7 = validation failed
"""

from __future__ import annotations


class KeelError(Exception):
    """Base error with structured fields for agent consumption."""

    error_code: str = "error"
    exit_code: int = 1

    retryable: bool = False

    # Subclasses can declare a recovery tool the agent should call next
    # (e.g. AuthError → "keel_auth_login"). Surfaces in
    # `to_envelope()["suggested_next_action"]["tool"]` so MCP agents
    # don't have to parse human-readable hints.
    recovery_tool: str | None = None
    recovery_tool_args: dict | None = None

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        exit_code: int | None = None,
        suggestion: str | None = None,
        docs_url: str | None = None,
        retryable: bool | None = None,
        input: dict | None = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        if exit_code is not None:
            self.exit_code = exit_code
        if retryable is not None:
            self.retryable = retryable
        self.suggestion = suggestion
        self.docs_url = docs_url
        self.input = input

    def to_dict(self) -> dict:
        """Legacy CLI error shape — kept for any caller that still reads it.
        New tools should use `to_envelope()` for the spec §13.5 shape.
        """
        d: dict = {
            "error": self.error_code,
            "message": str(self),
            "exit_code": self.exit_code,
            "retryable": self.retryable,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.docs_url:
            d["docs_url"] = self.docs_url
        if self.input:
            d["input"] = self.input
        return d

    def to_envelope(self) -> dict:
        """Spec §13.5 5-field error envelope. Used by both CLI and MCP
        adapters so agents get the same structured shape regardless of
        access channel.

        Fields:
          code                  — stable identifier
          message               — human-readable summary
          what_was_expected     — expected input or precondition
          example               — request body that WOULD work (best-effort)
          suggested_next_action — ``{tool, args[, reason, docs_url]}``

        ``suggested_next_action`` carries `reason` only when a recovery
        tool is set — otherwise the reason just echoed ``what_was_expected``
        verbatim, which doubled the noise the agent had to parse without
        adding any information. With no recovery tool, the block collapses
        to ``{"tool": null, "args": {}}`` — an unambiguous signal of "no
        automated next action; read what_was_expected".
        """
        next_action: dict = {
            "tool": self.recovery_tool,
            "args": dict(self.recovery_tool_args or {}),
        }
        if self.recovery_tool and self.suggestion:
            # Reason describes WHY this specific tool fixes the error —
            # genuinely distinct from what_was_expected when a tool is named.
            next_action["reason"] = self.suggestion
        if self.docs_url:
            next_action["docs_url"] = self.docs_url

        envelope = {
            "code": self.error_code,
            "message": str(self),
            "what_was_expected": self.suggestion or "Valid input matching the tool's schema.",
            "example": self.input or {},
            "suggested_next_action": next_action,
            # Retain legacy fields below the envelope so CI scripts that
            # check `exit_code` / `retryable` keep working.
            "exit_code": self.exit_code,
            "retryable": self.retryable,
        }
        return envelope


class NotFoundError(KeelError):
    error_code = "not_found"
    exit_code = 3


class AuthError(KeelError):
    error_code = "auth_failed"
    exit_code = 4
    # Agents calling MCP recover by invoking the OAuth-loopback login
    # tool. CLI users see "keel auth login" in the suggestion text.
    recovery_tool = "keel_auth_login"
    recovery_tool_args: dict | None = None


class ConflictError(KeelError):
    error_code = "conflict"
    exit_code = 5


class EntitlementError(KeelError):
    """403 from the API — caller lacks permission OR ran out of quota.

    Two distinct shapes share this exception class because the API
    returns 403 for both:

      * **Scope missing** — caller is authed but the OAuth token doesn't
        carry the required scope tier (e.g. live-trading without
        `runner.*`). Recovery: re-login with `keel_auth_login(scope=
        'live')`. The default `recovery_tool` is set to this case
        because it's actionable from an MCP agent.

      * **Quota exhausted / cap exceeded** — caller has permission
        but has hit a plan limit (e.g. weekly backtest_runs cap on
        free plan). Recovery is NOT a tool call — the user has to
        upgrade their plan via a browser checkout flow. Set
        ``recovery_tool=None`` and surface the billing URL via
        ``docs_url`` + the unit/limit/current/period via ``input``.

    ``translate_http_error(403)`` inspects the response body and
    instantiates the right shape — agents reading the envelope's
    ``suggested_next_action.tool`` see ``keel_auth_login`` only when
    re-auth would actually fix the failure.
    """

    error_code = "insufficient_entitlements"
    exit_code = 6
    # Default = scope-missing case (most common before billing-limit
    # cases are wired). translate_http_error(403) overrides to None
    # when it detects billing-quota reasons in the response body.
    recovery_tool = "keel_auth_login"
    recovery_tool_args: dict | None = {"scope": "live"}


class ValidationError(KeelError):
    error_code = "validation_failed"
    exit_code = 7


class UsageError(KeelError):
    error_code = "usage_error"
    exit_code = 2


def _extract_detail(body: str | None) -> str | None:
    """Pull the human message out of an RFC7807 / FastAPI JSON body.

    Two shapes seen in practice:

    - keel-api custom errors: ``{"type":..., "title":..., "status":...,
      "detail":"<human msg>", ...}`` (RFC7807).
    - FastAPI HTTPException: ``{"detail": <anything>}`` — often nested
      with another dict like ``{"detail": "Source hash mismatch",
      "current_source_hash": "..."}``.

    Prefer the human text. If `detail` is itself a dict, recurse one
    level to find a text field (`detail` / `title` / `message`),
    otherwise summarise the dict keys so the user at least sees what
    the API was complaining about — never `str(dict)`'s Python repr.
    """
    if not body:
        return body
    if not isinstance(body, str) or not body.lstrip().startswith("{"):
        return body
    try:
        import json as _json

        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return body
    if not isinstance(parsed, dict):
        return body
    detail = parsed.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, dict):
        nested = detail.get("detail") or detail.get("title") or detail.get("message")
        if isinstance(nested, str) and nested:
            # Append any structured hints from sibling keys (e.g.
            # `current_source_hash` on 409) so the recovery context
            # the API meant to share isn't dropped on the floor.
            sibling_keys = [k for k in detail.keys() if k not in {"detail", "title", "message"}]
            if sibling_keys:
                hints = ", ".join(f"{k}={detail[k]}" for k in sibling_keys)
                return f"{nested} ({hints})"
            return nested
        # Detail is a dict with no obvious text field — at least summarize
        # the keys so the user can see what fields the API returned.
        return f"{parsed.get('title') or 'API error'} (fields: {', '.join(sorted(detail.keys()))})"
    return parsed.get("title") or parsed.get("message") or body


def translate_http_error(status: int, body: str) -> KeelError:
    """Map HTTP status codes to KeelError subclasses."""
    if status == 401:
        return AuthError(
            "Not authenticated, or session expired. "
            "From an MCP agent: call the `keel_auth_login` tool — it opens a "
            "browser and persists tokens automatically. "
            "From a terminal: run `keel auth login` (browser) or "
            "`keel auth login --key <token>` for CI/SSH/Codespaces (token from "
            "https://app.usekeel.io/settings?tab=api-keys).",
            suggestion=(
                "Call MCP tool `keel_auth_login` (or run `keel auth login` in a terminal)."
            ),
            docs_url="https://app.usekeel.io/settings?tab=api-keys",
        )
    if status == 403:
        return _translate_403(body)
    if status == 404:
        return NotFoundError(
            _extract_detail(body) or "Resource not found",
            suggestion=(
                "Verify the id is correct and that you have access. For "
                "strategies, list yours via `keel_strategy_search`. For "
                "commits/versions, list via `keel_strategy_log`. For "
                "backtests, find via `keel_audit_list_last` or the strategy "
                "page in the web app."
            ),
        )
    if status == 409:
        return ConflictError(
            _extract_detail(body) or "Conflict — resource changed",
            suggestion="Run 'keel strategy pull' to fetch latest, then retry",
        )
    if status == 422:
        return ValidationError(
            _extract_detail(body) or "Validation failed",
            suggestion=(
                "Inspect the message for the failing field. Re-validate locally "
                "via `keel_strategy_compose dry_run=True` if the failure is in "
                "DSL source; check arg types against the tool's input_schema "
                "otherwise."
            ),
        )
    if status == 429:
        return KeelError(
            "Rate limited — too many requests",
            error_code="rate_limited",
            exit_code=1,
            retryable=True,
            suggestion="Wait a few seconds and retry",
        )
    if status >= 500:
        return KeelError(
            f"Server error (HTTP {status})",
            error_code="server_error",
            exit_code=1,
            retryable=True,
            suggestion="Retry in a few seconds. If persistent, check https://status.usekeel.io",
        )
    return KeelError(f"HTTP {status}: {body}", exit_code=1)


# ─── 403 sub-translation ─────────────────────────────────────────────────


# Friendly labels mirroring keel-api/src/errors.py _UNIT_LABELS.
_UNIT_LABELS = {
    "backtest_runs": "backtest runs",
    "backtest_compute_seconds": "backtest compute seconds",
    "eval_runs": "evaluation runs",
    "eval_compute_seconds": "evaluation compute seconds",
    "ai_messages": "AI chat messages",
    "live_strategies_max": "live strategies",
    "symbols_max": "symbols per strategy",
    "feature:full_backtest": "full backtest mode",
    "feature:api_access": "API access",
    "feature:priority_queue": "priority queue",
    "feature:referral_payouts": "referral payouts",
    "feature:team_seats": "team seats",
    "feature:custom_components": "custom components",
}

_BILLING_URL = "https://app.usekeel.io/settings?tab=billing"


def _parse_entitlement_reasons(reasons: list) -> dict | None:
    """Pull plan-limit context out of keel-api's `reasons` list.

    keel-api emits reasons like::

        entitlement:insufficient:backtest_runs:limit=30:current=30
        entitlement:cap_exceeded:live_strategies_max:limit=1:current=1
        entitlement:no_grants:ai_messages:need=1
        entitlement:feature_not_available:feature:priority_queue

    Returns a dict with parsed fields, or None if no entitlement
    reason is present (meaning the 403 is for a different cause —
    likely missing OAuth scope).
    """
    if not reasons or not isinstance(reasons, list):
        return None
    for raw in reasons:
        if not isinstance(raw, str) or not raw.startswith("entitlement:"):
            continue
        parts = raw.split(":")
        if len(parts) < 3:
            continue
        kind = parts[1]
        unit = parts[2]
        # Features may have a sub-name (e.g. `feature:priority_queue`).
        if unit == "feature" and len(parts) >= 4:
            unit = f"feature:{parts[3]}"
            extras_start = 4
        else:
            extras_start = 3
        info: dict = {
            "kind": kind,
            "unit": unit,
            "unit_label": _UNIT_LABELS.get(unit, unit.replace("_", " ")),
        }
        for p in parts[extras_start:]:
            if "=" in p:
                k, v = p.split("=", 1)
                try:
                    info[k] = int(v)
                except ValueError:
                    info[k] = v
        return info
    return None


def _translate_403(body: str) -> "EntitlementError":
    """Distinguish billing-limit 403 from scope-missing 403.

    Billing-limit case (caller's plan can't cover the action) — recovery
    is a browser checkout, NOT a tool call. We surface plan/unit/limit/
    current/period in the envelope and point ``docs_url`` at the billing
    page. ``recovery_tool`` is explicitly None so the agent doesn't
    fall into a "re-auth and try again" loop that can't fix the
    quota.

    Scope-missing case (caller has the permission concept but the
    OAuth token doesn't carry the right scope tier — typically live
    trading without ``runner.*``) — recovery IS a tool call: re-login
    with ``keel_auth_login(scope='live')``. Falls through to the
    default ``EntitlementError`` shape.
    """
    import json as _json

    # Best-effort JSON parse. keel-api emits RFC 7807 Problem Details.
    payload = {}
    if body:
        try:
            payload = _json.loads(body)
        except (ValueError, TypeError):
            payload = {}

    detail_text = (payload.get("detail") if isinstance(payload, dict) else None) or body or ""
    reasons = payload.get("reasons") if isinstance(payload, dict) else None
    parsed = _parse_entitlement_reasons(reasons) if reasons else None

    if parsed:
        # Billing-quota case — point at the billing flow, not re-auth.
        unit_label = parsed["unit_label"]
        limit = parsed.get("limit")
        current = parsed.get("current")
        need = parsed.get("need")
        kind = parsed["kind"]

        # Compose an agent-facing message that's actionable.
        if kind in ("cap_exceeded", "insufficient"):
            usage_blurb = (
                f" (used {current}/{limit})" if current is not None and limit is not None else ""
            )
            msg = (
                f"Plan limit hit: {unit_label}{usage_blurb}. "
                f"This is a billing limit — re-authenticating won't add quota. "
                f"Tell the user how much they've used and direct them to "
                f"{_BILLING_URL} to upgrade. The next plan tier (or any paid "
                f"tier) adds capacity; check `keel_status` for current plan."
            )
        elif kind in ("no_grants", "no_grants_exist"):
            need_blurb = f" (needs {need})" if need is not None else ""
            msg = (
                f"Your plan doesn't include {unit_label}{need_blurb}. "
                f"Direct the user to upgrade at {_BILLING_URL}."
            )
        elif kind == "feature_not_available":
            msg = (
                f"This feature ({unit_label}) is not on your plan. "
                f"Direct the user to upgrade at {_BILLING_URL}."
            )
        else:
            msg = detail_text or f"Plan limit hit on {unit_label}."

        err = EntitlementError(
            msg,
            suggestion=(
                f"Tell the user they've hit a plan limit on {unit_label}. "
                f"Direct them to {_BILLING_URL} to upgrade — no MCP tool can "
                f"increase the quota. Check `keel_status` for the current plan."
            ),
            docs_url=_BILLING_URL,
            input={
                "unit": parsed["unit"],
                "unit_label": unit_label,
                "kind": kind,
                "limit": limit,
                "current": current,
                "need": need,
                "billing_url": _BILLING_URL,
            },
        )
        # Override the class-level recovery_tool for the billing case —
        # re-auth doesn't fix a quota cap.
        err.recovery_tool = None
        err.recovery_tool_args = None
        return err

    # No entitlement reasons → falls back to scope-missing default shape.
    return EntitlementError(
        detail_text
        or (
            "403 Forbidden. If this is a live-trading tool "
            "(deploy/pause/resume/stop), your session likely lacks the "
            "`runner.*` scope tier — re-login with the live consent: "
            "`keel_auth_login(scope='live')` or `keel auth login --scope live`. "
            "Otherwise check `keel_status` for plan limits."
        ),
        suggestion=(
            "Re-login with live scope (`keel_auth_login(scope='live')`) if "
            "this is a live-trading tool, or check `keel_status` for plan limits."
        ),
    )
