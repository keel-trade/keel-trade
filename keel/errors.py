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

import re


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
        #: The server's STRUCTURED refusal (Q-1742): its machine ``code`` and
        #: any sibling fields (``requested_start``, ``issues`` …), exactly as
        #: sent. Rides the envelope as ``detail`` so an agent branches on
        #: ``detail.code`` — the code never belongs in the human ``message``.
        self.detail: dict | None = None

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
            **({"detail": dict(self.detail)} if self.detail else {}),
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
    # Agents calling a LOCAL MCP server recover by invoking the OAuth-
    # loopback login tool; CLI users see "keel auth login" in the suggestion
    # text. `translate_http_error(401)` clears it on a hosted or listed
    # server, where `keel_auth_login` is not registered (spec 05 R-L4).
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
        but has hit a plan limit (e.g. the weekly backtest_runs grant).
        Recovery is NOT a tool call and NOT re-authentication: the limit
        lifts at its reset. Set ``recovery_tool=None`` and surface the
        served quota numbers via ``input`` — no plan destination (D-12).

    ``translate_http_error(403)`` branches on the response body's
    machine ``code`` (``quota_exhausted`` / ``quota_cap_reached`` /
    ``plan_feature_unavailable`` — mcp-conversion D-8) and instantiates
    the right shape — agents reading the envelope's
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


def _problem_code(body: str | None) -> dict | None:
    """The machine half of an RFC 7807 body with a top-level ``code``.

    keel-api's `problem_response` puts ``code`` beside ``type``/``title``/
    ``status``/``detail``; this returns ``{"code": …, <sibling fields>}``
    without the 7807 framing or the human ``detail`` sentence, or ``None``
    for an uncoded body.
    """
    if not isinstance(body, str) or not body.lstrip().startswith("{"):
        return None
    try:
        import json as _json

        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    code = parsed.get("code")
    if not isinstance(code, str) or not code:
        return None
    framing = {"type", "title", "status", "detail", "instance", "message"}
    return {k: v for k, v in parsed.items() if k not in framing}


def _server_detail(body: str | None) -> dict | None:
    """The structured ``detail`` object of a FastAPI error body, or ``None``.

    keel-api's coded refusals raise ``HTTPException(detail={"code": ...,
    "message": ..., <fields>})``; this returns every key except the human
    text, so the machine half reaches the envelope intact (Q-1742).
    """
    if not isinstance(body, str) or not body.lstrip().startswith("{"):
        return None
    try:
        import json as _json

        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return None
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    if not isinstance(detail, dict):
        return None
    out = {k: v for k, v in detail.items() if k not in {"detail", "title", "message"}}
    return out or None


#: Location prefixes FastAPI / pydantic put before the field name — where
#: the value came from, not which field it is.
_LOC_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})


def readable_validation_errors(errors: list) -> str | None:
    """``[{"loc": [...], "msg": ...}, …]`` (FastAPI's 422 list, pydantic's
    ``ValidationError.errors()``) as ``"`limit`: Input should be less than or
    equal to 100; …"`` — field and problem only: never the echoed input, the
    error type or a docs URL. ``None`` when no entry is readable."""
    clauses: list[str] = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        loc = [str(p) for p in (err.get("loc") or ()) if p is not None]
        while loc and loc[0] in _LOC_SOURCES:
            loc = loc[1:]
        msg = str(err.get("msg") or "").strip()
        if err.get("type") == "extra_forbidden":
            msg = "not a recognised field"
        if not msg:
            continue
        clauses.append(f"`{'.'.join(loc)}`: {msg}" if loc else msg)
    return "; ".join(clauses) + "." if clauses else None


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
    if isinstance(detail, list):
        # FastAPI's RequestValidationError: a list of {loc, msg, type, input,
        # ctx}. Echoing the JSON put `{"detail":[{"type":"less_than_equal",…`
        # in front of the user (Q-2273 L3); each entry becomes one
        # "`field`: problem" clause.
        readable = readable_validation_errors(detail)
        if readable:
            return readable
    if isinstance(detail, dict):
        nested = detail.get("detail") or detail.get("title") or detail.get("message")
        if isinstance(nested, str) and nested:
            # A CODED detail is keel-api's complete refusal: its message was
            # written for a human and its fields travel structurally
            # (`KeelError.detail`). Appending them here printed
            # "(code=WINDOW_EMPTY)" into the sentence a user reads on the
            # card (Q-1742) — the code is for the agent, never the prose.
            if isinstance(detail.get("code"), str):
                return nested
            # An UNCODED detail (older shapes, e.g. `current_source_hash` on
            # 409) keeps its sibling hints in the text so the recovery
            # context the API meant to share isn't dropped on the floor.
            sibling_keys = [k for k in detail.keys() if k not in {"detail", "title", "message"}]
            if sibling_keys:
                hints = ", ".join(f"{k}={detail[k]}" for k in sibling_keys)
                return f"{nested} ({hints})"
            return nested
        # Detail is a dict with no obvious text field — at least summarize
        # the keys so the user can see what fields the API returned.
        return f"{parsed.get('title') or 'API error'} (fields: {', '.join(sorted(detail.keys()))})"
    return parsed.get("title") or parsed.get("message") or body


#: keel-api's backtest-WINDOW refusals (``utils/backtest_window.py``). One
#: family, one expectation: none of them is about the strategy source, so the
#: generic "re-validate the DSL" advice sent agents to fix a strategy that was
#: fine (Q-1742).
WINDOW_CODES: frozenset[str] = frozenset(
    {
        "WINDOW_INVERTED",
        "WINDOW_EMPTY",
        "WINDOW_IN_FUTURE",
        "WINDOW_BEFORE_COVERAGE",
        "WINDOW_BEFORE_SERIES_ERA",
        "COVERAGE_UNAVAILABLE",
        "STREAM_ERA_UNMEASURED",
    }
)


def _expectation_for_422(detail: dict | None) -> str:
    """``what_was_expected`` for a 422, specific to the class of refusal.

    Three classes, told apart by the server's machine ``code``: a backtest
    WINDOW refusal (the dates, never the source), any other CODED refusal
    (the server named what it rejected), and an UNCODED one — the
    strategy-validation gate (an ``issues`` list, no code), where the DSL dry
    run is the right advice.
    """
    code = detail.get("code") if isinstance(detail, dict) else None
    if code in WINDOW_CODES:
        return (
            "A backtest window the platform can serve: start_date before end_date "
            "(YYYY-MM-DD), inside the data coverage the message names. The strategy "
            "source is not the problem — change the dates, not the DSL."
        )
    if isinstance(code, str):
        return (
            f"The server refused this input ({code}); the message names what it "
            "rejected. Change that input and retry — resubmitting it unchanged "
            "returns the same refusal."
        )
    return (
        "Inspect the message for the failing field. Re-validate locally "
        "via `keel_strategy_compose dry_run=True` if the failure is in "
        "DSL source; check arg types against the tool's input_schema "
        "otherwise."
    )


def translate_http_error(status: int, body: str) -> KeelError:
    """Map HTTP status codes to KeelError subclasses."""
    if status == 400:
        # keel-api 400s carry instructive remediation in `detail` (e.g. the
        # deploy-intent token verdicts, spec 03 R7). Surface that text as the
        # message instead of a raw `HTTP 400: {json}` dump.
        return UsageError(
            _extract_detail(body) or "Bad request — the server rejected the input.",
            suggestion=(
                "The message names what the server rejected — fix that input "
                "and retry (a 400 is never fixed by retrying unchanged)."
            ),
        )
    if status == 401:
        if not _local_tools_registered():
            # Hosted / listed (spec 05 R-L4): `keel_auth_login` is not on
            # this server's tools/list, so the error must not name it —
            # re-authentication is the MCP client's own OAuth flow (the
            # `HostedAuthError` precedent).
            err = AuthError(
                "Not authenticated, or the session expired. Reconnecting this "
                "connector in the client starts a new session.",
                suggestion="Reconnect the Keel connector in the client, then retry.",
            )
            err.recovery_tool = None
            err.recovery_tool_args = None
            return err
        # Host from the environment, never a literal: a staging agent that
        # hands the user a prod URL sends them to the wrong account
        # (the same defect M3.2 fixed on the billing link).
        api_keys_url = f"{_app_url()}/settings?tab=api-keys"
        return AuthError(
            "Not authenticated, or session expired. "
            "From an MCP agent: call the `keel_auth_login` tool — it opens a "
            "browser and persists tokens automatically. "
            "From a terminal: run `keel auth login` (browser) or "
            f"`keel auth login --key <token>` for CI/SSH/Codespaces (token from {api_keys_url}).",
            suggestion=(
                "Call MCP tool `keel_auth_login` (or run `keel auth login` in a terminal)."
            ),
            docs_url=api_keys_url,
        )
    if status == 403:
        return _translate_403(body)
    if status == 404:
        # `keel_audit_list_last` is not on the listed or hosted surface
        # (spec 05 §4 item 7): named only where this server registers it.
        backtests_where = (
            "`keel_audit_list_last` or the strategy page in the web app"
            if _local_tools_registered()
            else "the strategy page in the web app"
        )
        return NotFoundError(
            _extract_detail(body) or "Resource not found",
            suggestion=(
                "Verify the id is correct and that you have access. For "
                "strategies, list yours via `keel_strategy_search`. For "
                "commits/versions, list via `keel_strategy_history`. For "
                f"backtests, find via {backtests_where}."
            ),
        )
    if status == 409:
        coded = _problem_code(body)
        if coded is None:
            # The HTTPException(detail={code, …}) shape keel-api also uses.
            served = _server_detail(body)
            if isinstance(served, dict) and isinstance(served.get("code"), str):
                coded = served
        if coded is not None:
            # A CODED conflict (RFC 7807 body with a top-level `code`, the
            # Q-1711 arm — `VERSION_NOT_FOUND`, `VERSION_AND_COMMIT_ID`,
            # `PARENT_VERSION_NOT_HEAD`): the code is the envelope's `code`
            # and rides `detail` with its sibling fields, and the generic
            # "pull, then retry" advice — true only of a stale workspace —
            # is not attached to a refusal it does not fix.
            err = ConflictError(
                _extract_detail(body) or "Conflict",
                error_code=coded["code"],
            )
            err.detail = coded
            return err
        # `keel strategy pull` is a CLI step against a local checkout: a
        # hosted or listed server has neither (spec 05 R-L4), so there the
        # suggestion states the conflict and names no step it cannot run.
        return ConflictError(
            _extract_detail(body) or "Conflict — resource changed",
            suggestion=(
                "Run 'keel strategy pull' to fetch latest, then retry"
                if _local_tools_registered()
                else (
                    "The server copy changed after this call's copy was read; "
                    "resubmitting it unchanged returns the same conflict."
                )
            ),
        )
    if status == 422:
        detail = _server_detail(body)
        code = detail.get("code") if isinstance(detail, dict) else None
        err = ValidationError(
            _extract_detail(body) or "Validation failed",
            # keel-api's own machine code IS the envelope `code` (Q-1751):
            # every coded 422 used to flatten to `validation_failed`, so the
            # envelope and the mcp-server audit row (which records `code`)
            # could not tell WINDOW_INVERTED from a DSL error. The class
            # code stays the fallback for an uncoded body.
            error_code=code if isinstance(code, str) and code else None,
            suggestion=_expectation_for_422(detail),
        )
        err.detail = detail
        return err
    if status == 429:
        return rate_limited_error(None)
    if status >= 500:
        return KeelError(
            f"Server error (HTTP {status})",
            error_code="server_error",
            exit_code=1,
            retryable=True,
            suggestion="Retry in a few seconds. If persistent, check https://status.usekeel.io",
        )
    # Any other status (405, 413, 418 …): a plain sentence. The raw body —
    # JSON framing, a request id, an HTML error page — is never the message
    # (Q-2273 L3); a short plain-text detail the server meant for a reader is.
    detail = _extract_detail(body)
    readable = (
        isinstance(detail, str)
        and detail.strip()
        and detail is not body
        and len(detail) <= 300
        and not detail.lstrip().startswith(("{", "[", "<"))
    )
    return KeelError(
        f"Keel refused the request (HTTP {status})" + (f": {detail.strip()}" if readable else "."),
        exit_code=1,
        suggestion="Check the inputs against the tool's schema; resending it unchanged returns the same refusal.",
    )


def transport_error(exc: Exception, *, sent: bool) -> KeelError:
    """A transport failure (no HTTP response) as a plain-language KeelError.

    The exception's own text (``[Errno 61] Connection refused``, ``read timed
    out``) is an operator detail, not a message (Q-2273 L3); the class names
    what happened. Code ``error`` (unchanged), retryable: reaching the server
    again is the remedy — `write_unconfirmed` overrides that for a write.
    """
    import httpx

    if not sent:
        what = "Could not connect to Keel"
    elif isinstance(exc, httpx.TimeoutException):
        what = "Keel did not respond in time"
    else:
        what = "The connection to Keel failed before a response arrived"
    return KeelError(
        f"{what}.",
        retryable=True,
        suggestion="Retry in a few seconds. If it keeps failing, Keel may be unavailable.",
    )


def retry_after_seconds(value: str | None) -> float | None:
    """A ``Retry-After`` header in seconds — either form RFC 9110 §10.2.3
    allows (delay-seconds or an HTTP-date) — or ``None`` when absent or
    unreadable. A date in the past reads as 0."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def rate_limited_error(retry_after_s: float | None) -> KeelError:
    """The ``rate_limited`` refusal, carrying the server's wait when it sent
    one (``detail.retry_after_s``) so the caller can schedule the retry."""
    import math

    if retry_after_s is None:
        message = "Rate limited — too many requests in a short time."
        suggestion = "Wait a few seconds, then retry."
    else:
        wait = max(1, math.ceil(retry_after_s))
        message = f"Rate limited — too many requests. Retry after {wait} seconds."
        suggestion = f"Wait {wait} seconds, then retry."
    err = KeelError(
        message,
        error_code="rate_limited",
        exit_code=1,
        retryable=True,
        suggestion=suggestion,
    )
    if retry_after_s is not None:
        err.detail = {"retry_after_s": max(1, math.ceil(retry_after_s))}
    return err


def write_unconfirmed(method: str, err: KeelError) -> KeelError:
    """A failed WRITE whose outcome is unknown (Q-2273 L1).

    The request reached the server — a 5xx, or a timeout or broken connection
    after sending — so it may have been applied, and keel-api takes no client
    idempotency key: resending it unchanged can create a duplicate. The
    error's code is kept; ``retryable`` becomes False and the message says
    the change may exist and how to find out.
    """
    err.retryable = False
    base = str(err).rstrip(".")
    err.args = (
        f"{base}. This {method.upper()} may still have been applied — check before "
        "sending it again, or it may be made twice.",
    )
    err.suggestion = (
        "Read the result back first (the strategy, its versions or its recent "
        "runs, the share links or notes it would have created) and send the "
        "request again only if the change is not there."
    )
    return err


# ─── The quota vocabulary, and the 403 sub-translation ───────────────────
#
# The SDK keeps NO unit-label table. The label, the numbers, the period and
# the reset instant all ride ON THE WIRE (`platform_auth.quota` serves them
# in the 403 body's `quota` block and in the `quota` list on a success
# response). Two hand-synced label tables used to exist — keel-api's and
# this one — and they had already diverged on `backtest_runs`
# ("backtests" vs "backtest runs"), which is the class of drift that
# produced Q-1590. One producer, one wire field, no copies
# (mcp-conversion M0.6).


#: The machine ``code`` values that mean "a plan limit stopped this"
#: (mcp-conversion D-8 / `platform_auth.quota.DENIAL_CODES`). The client
#: branches on these instead of sniffing ``reasons[]`` prose — the sniffing
#: is what let the producer's vocabulary change underneath the consumer.
QUOTA_CODES: frozenset[str] = frozenset(
    {"quota_exhausted", "quota_cap_reached", "plan_feature_unavailable"}
)

#: The ONLY denial kinds that may be rendered as "the plan does not include
#: X". Every other kind HAD a grant and spent it — saying otherwise to
#: someone who just used thirty of theirs is the user-visible half of
#: Q-1590.
NO_GRANT_KINDS: frozenset[str] = frozenset({"no_grants", "no_cap_grants", "feature_not_available"})

#: period → the phrase that names the window in a sentence.
_PERIOD_PHRASES: dict[str, str] = {
    "daily": "today",
    "weekly": "this week",
    "monthly": "this month",
}


def _app_url() -> str:
    """Keel app origin — the same env the tool adapters and widgets read."""
    import os

    return os.environ.get("KEEL_APP_URL", "https://app.usekeel.io").rstrip("/")


def _local_tools_registered() -> bool:
    """``keel.tools.outcomes._toolsets.local_tools_registered`` — whether an
    error may name `keel_auth_login`, `keel_audit_list_last` or a live-write
    tool (spec 05 R-L4 / §4 item 7). Imported lazily: the toolset module's
    package imports this one."""
    from keel.tools.outcomes._toolsets import local_tools_registered

    return local_tools_registered()


# ─── The neutral-wall guard (mcp-conversion D-12, 2026-09-28) ───────────
#
# Every plan-limit text an agent surface emits — the 403 message and
# suggestion, the handoff's talking points, reason, suggestion and
# `limit_view`, the run's `quota_notice`, `keel_plan_usage`'s talking
# point — states the caller's own limit and reset and stops. OpenAI
# rejected Keel v1.0.0 for "commerce for disallowed offerings" while the
# wall named the other tiers and linked the billing tab (Q-2080). Four
# families are banned, each of which HAS shipped here: a price or billing
# cadence, a capacity promise or pitch verb, a transactional or plan
# destination (checkout, the billing tab, /pricing, "see plans"), and the
# other plans themselves ("higher plans", "plan tier", the builder fee).
#
# ONE plan sentence is allowed, and only on the FREE plan's wall (D-12 Q1,
# its recorded fallback since 2026-10-01 — Q-2268): "Plans are changed in
# the Keel web app." — where plans are changed, with no names, prices,
# comparison, capacity claim, verb or link. A paid user already knows plans
# exist, so their wall carries no plan sentence at all (Q2).
#
# A fifth family, expiry urgency, joined with the first-week allowance
# (connect-onboarding spec 01 §1.9): the one first-week sentence states its
# end instant and nothing else. It is a separate constant so the agent-surface
# guards in tests/ read the SAME pattern rather than a copy.
EXPIRY_URGENCY_PATTERN = (
    r"\bbefore\s+(?:they|it)\s+expires?\b"
    r"|\bdon[’']?t\s+lose\b"
    r"|\buse\s+them\s+before\b"
    r"|\brunning\s+out\b"
    r"|\bexpires?\s+soon\b"
)
FORBIDDEN_UPSELL_RE = re.compile(
    r"\$\s?\d"  # a price
    r"|\b\d+\s?(?:usd|dollars)\b"
    r"|\bper month\b|\bper year\b|\b/mo\b|\bmonthly\b|\bannually\b"
    r"|\badds?\s+capacity\b|\bmore\s+capacity\b|\bextra\s+capacity\b"
    r"|\bunlocks?\b"
    r"|\bupgrad(?:e|es|ed|ing)\b|\bupgrade_\w+"
    r"|\bcheckout\b|\bstripe\b"
    r"|\bact\s+now\b|\bhurry\b|\blimited[\s-]time\b|\bdon'?t\s+miss\b"
    # D-12: the other plans, and every plan destination.
    r"|\bhigher\s+plans?\b|\bplan\s+tiers?\b|\bbuilder\s+fees?\b|\bsee\s+plans\b"
    r"|tab=billing|/pricing\b"
    # Expiry urgency (connect-onboarding spec 01 §1.9): a time-limited
    # allowance is stated as a fact ("…; they end Tue 13 Oct 15:02 UTC."),
    # never as a push to spend it before it goes.
    r"|" + EXPIRY_URGENCY_PATTERN,
    re.IGNORECASE,
)

#: The one other-plans sentence (D-12 Q1) — legal on the free plan only.
_PAID_PLANS_RE = re.compile(r"\bpaid\s+plans?\b", re.IGNORECASE)


def assert_neutral_wall_text(text: str, *, free_plan: bool) -> str:
    """Raise ``ValueError`` if ``text`` is not neutral plan-limit copy (D-12).

    ``free_plan`` is the ONLY switch: it admits the one "Paid plans include
    more …" sentence. Returns ``text`` unchanged — validators behave one
    exact way (repo lesson), so a violation is a programming error that
    fails the call rather than a string quietly edited on the way out.
    """
    if not isinstance(text, str):
        raise ValueError(f"wall text must be a string, got {type(text).__name__}")
    hit = FORBIDDEN_UPSELL_RE.search(text)
    if hit:
        raise ValueError(
            f"wall text uses forbidden plan/upsell language {hit.group(0)!r} "
            f"(mcp-conversion D-12: the caller's own limit and reset only): {text!r}"
        )
    if not free_plan:
        hit = _PAID_PLANS_RE.search(text)
        if hit:
            raise ValueError(
                f"wall text names other plans ({hit.group(0)!r}) off the free plan "
                f"(D-12 Q2: a paid wall states its limit and reset only): {text!r}"
            )
    return text


def _label_for_unit(unit: str) -> str:
    """Label for a unit the SERVER did not label (a keel-api older than the
    quota contract). Mechanical, never a table: the authority is
    ``platform_auth.quota.UNIT_LABELS`` and it is served on the wire."""
    if unit.startswith("feature:"):
        return unit[len("feature:") :].replace("_", " ")
    return unit.replace("_", " ")


def quota_reset_instant(served):
    """The reset instant a server sent, or ``None``.

    Accepts a served quota block (both shapes: ``resets_at`` ISO 8601 from
    ``QuotaView.to_dict()``, or ``reset_epoch`` integer seconds from the
    403 reason vocabulary, which is colon-delimited and so cannot carry an
    ISO timestamp) or a raw value of either kind. Anything unparseable
    reads as "no reset known" — never as a fabricated one, which is the
    whole point: every hardcoded reset claim in this tree was wrong
    because nobody had an instant to read (Q-1597).
    """
    from datetime import datetime, timezone

    if isinstance(served, dict):
        epoch = served.get("reset_epoch")
        raw = served.get("resets_at")
    elif isinstance(served, bool):  # bool is an int — never an instant
        return None
    elif isinstance(served, int):
        epoch, raw = served, None
    else:
        epoch, raw = None, served
    if isinstance(epoch, int) and not isinstance(epoch, bool):
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def format_reset_instant(when) -> str:
    """``"Mon 22 Sep 00:00 UTC"`` — the one reset rendering.

    Built without ``%-d`` (a glibc/BSD extension that raises on Windows,
    where this wheel also runs).
    """
    return f"{when:%a} {when.day} {when:%b %H:%M} UTC"


def quota_period_phrase(block: dict) -> str:
    """`` this week`` / `` today`` / `` this month`` — or empty.

    Empty for a cap (live strategy slots): a slot frees when a deployment
    stops, not when a clock rolls over, so naming a window would be a lie.
    """
    phrase = _PERIOD_PHRASES.get(str(block.get("period") or ""))
    return f" {phrase}" if phrase else ""


def quota_reset_sentence(block: dict) -> str:
    """`` They reset Mon 22 Sep 00:00 UTC.`` — or empty when the unit does
    not refill (a cap) or the server did not send the instant."""
    when = quota_reset_instant(block)
    return f" They reset {format_reset_instant(when)}." if when else ""


def render_quota_sentence(block: dict) -> str | None:
    """ONE factual line from a served quota block (mcp-conversion D-10/M1.2).

    The API carries numbers; the SDK renders the sentence. Facts and stop
    (D-11): both halves of the fraction, the window, the reset instant —
    no tier names, no prices, no recommendation.

        >>> render_quota_sentence({"label": "backtests", "limit": 50,
        ...                        "used": 44, "remaining": 6,
        ...                        "period": "weekly",
        ...                        "resets_at": "2026-09-22T00:00:00Z"})
        '6 of 50 backtests left this week; they reset Mon 22 Sep 00:00 UTC.'

    ``None`` when the block carries no finite allowance to report (an
    unlimited unit, or a server that sent no numbers) — rendering "left"
    without a number is exactly the null-number sentence Q-1590 shipped.
    """
    if not isinstance(block, dict) or block.get("unlimited"):
        return None
    limit = block.get("limit")
    remaining = block.get("remaining")
    if not isinstance(limit, int) or not isinstance(remaining, int):
        return None
    label = block.get("label") or _label_for_unit(str(block.get("unit") or ""))
    when = quota_reset_instant(block)
    reset_clause = f"; they reset {format_reset_instant(when)}" if when else ""
    return f"{remaining} of {limit} {label} left{quota_period_phrase(block)}{reset_clause}."


#: The keys of a served ``first_week`` block (connect-onboarding spec 01
#: §1.8) an agent surface passes through — an allow-list, like the quota
#: block's own, so a future server field never reaches a surface whose
#: policy was not reviewed for it.
FIRST_WEEK_KEYS: tuple[str, ...] = ("granted", "remaining", "ends_at")


def project_first_week(block: object) -> dict | None:
    """A served ``first_week`` block projected to :data:`FIRST_WEEK_KEYS`,
    or ``None`` when the server sent none (the key is absent unless a
    first-week allowance is active — spec 01 §1.8)."""
    if not isinstance(block, dict):
        return None
    projected = {k: block[k] for k in FIRST_WEEK_KEYS if k in block}
    return projected or None


def render_first_week_sentence(block: object) -> str | None:
    """THE one first-week sentence (connect-onboarding spec 01 §1.9, founder
    decision 5), from a served ``first_week`` block::

        >>> render_first_week_sentence({"granted": 200, "remaining": 154,
        ...                             "ends_at": "2026-10-13T15:02:11Z"})
        '154 of 200 first-week backtests left; they end Tue 13 Oct 15:02 UTC.'

    It mirrors :func:`render_quota_sentence`: the caller's own numbers and
    the instant, verbatim from the server, and nothing about what follows
    (D-11(a), D-12). ``None`` unless all three facts arrived — a sentence
    with a missing number or an invented end is the null-number sentence
    Q-1590 shipped.
    """
    if not isinstance(block, dict):
        return None
    granted, remaining = block.get("granted"), block.get("remaining")
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (granted, remaining)):
        return None
    when = quota_reset_instant(block.get("ends_at"))
    if when is None:
        return None
    return f"{remaining} of {granted} first-week backtests left; they end {format_reset_instant(when)}."


def _quota_context(payload: dict, reasons: list | None) -> dict | None:
    """Normalize what the 403 body says about a plan limit, or ``None`` when
    this 403 is NOT one (a scope/role/policy refusal).

    Two producers feed this, and the FIRST is the contract:

    1. **The served block.** keel-api attaches ``code`` + ``quota`` (parsed
       by ``platform_auth.quota.parse_denial_reason`` — the same module that
       FORMATS the reason, proved inverses by a round-trip test). Branching
       on ``code`` is mcp-conversion D-8: one field, and the whole
       string-drift class is gone.
    2. **The reason strings.** Kept ONLY as an explicitly-labelled fallback
       for a keel-api older than that contract, which emitted
       ``limit=``/``current=``/``need=`` and no ``code``. It is a fallback,
       not a second path: it produces the same normalized dict, and a server
       that sends ``code`` never reaches it.
    """
    code = payload.get("code")
    served = payload.get("quota")
    if code is not None and code not in QUOTA_CODES and not isinstance(served, dict):
        # A machine code that is NOT a quota refusal (scope/role/policy/
        # budget, or a provisioning outcome handled above) — never guess.
        return None
    if isinstance(served, dict) and served.get("unit"):
        info = dict(served)
    elif code in QUOTA_CODES:
        # Code without a block: keep the refusal quota-shaped (re-auth still
        # cannot fix it) and fall back to whatever the reasons carry.
        info = _parse_entitlement_reasons(reasons) or {}
        info.setdefault("code", code)
    else:
        info = _parse_entitlement_reasons(reasons) or {}
    if not info.get("unit"):
        return None

    unit = str(info["unit"])
    label = info.get("label") or info.get("unit_label") or _label_for_unit(unit)
    out: dict = {"unit": unit, "label": label, "unit_label": label}
    for key in ("kind", "code", "limit", "used", "remaining", "period", "reset_epoch", "need"):
        if info.get(key) is not None:
            out[key] = info[key]
    if info.get("resets_at") is not None:
        out["resets_at"] = info["resets_at"]
    # The caller's OWN plan — the one plan fact a wall states (D-12). keel-api
    # also serves `higher_plans` (the other plans' limits on this unit, D-10:
    # the API carries numbers); no agent surface renders other plans any
    # more, so it is deliberately not projected here and cannot reach
    # `example`, `limit_details` or any sentence (Q-2080).
    if isinstance(info.get("plan"), str) and info["plan"]:
        out["plan"] = info["plan"]
    # `current` is the legacy name for `used` (the pre-contract vocabulary).
    # Kept as an alias so SDK consumers reading `example.current` keep
    # working; nothing new should read it.
    if info.get("used") is not None:
        out["current"] = info["used"]
    elif info.get("current") is not None:
        out["current"] = info["current"]
        out.setdefault("used", info["current"])
    if out.get("code") is None and out.get("kind") is not None:
        # Old server: derive the code the new one would have sent, so every
        # consumer downstream sees one vocabulary.
        out["code"] = (
            "plan_feature_unavailable"
            if out["kind"] in NO_GRANT_KINDS
            else ("quota_cap_reached" if out["kind"] == "cap_exceeded" else "quota_exhausted")
        )
    return out


def _parse_entitlement_reasons(reasons: list | None) -> dict | None:
    """FALLBACK ONLY — parse plan-limit context out of ``reasons[]``.

    Used when the server sent no machine ``code`` and no ``quota`` block,
    i.e. a keel-api older than the quota contract. That server emitted::

        entitlement:insufficient:backtest_runs:limit=30:current=30
        entitlement:cap_exceeded:live_strategies_max:limit=1:current=1
        entitlement:no_grants:ai_messages:need=1
        entitlement:feature_not_available:feature:priority_queue

    Current servers emit the same five kinds with one symmetric key set
    (``limit=``/``used=``/``remaining=``/``period=``/``reset_epoch=``) AND
    the parsed block on the body, so this function's output is never the
    source of truth for them. Returns ``None`` when no entitlement reason
    is present.
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
        info: dict = {"kind": kind, "unit": unit, "unit_label": _label_for_unit(unit)}
        for p in parts[extras_start:]:
            if "=" in p:
                k, v = p.split("=", 1)
                try:
                    info[k] = int(v)
                except ValueError:
                    info[k] = v
        return info
    return None


#: period → the adjective that titles a limit ("Weekly backtests").
_PERIOD_ADJECTIVE: dict[str, str] = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}


def plan_display_name(plan: str | None) -> str | None:
    """``free`` → ``Free``. The served plan id, capitalised — never a lookup
    table that could name a plan the server did not."""
    return plan.capitalize() if isinstance(plan, str) and plan else None


def quota_amount(unit: str, n: int) -> str:
    """``500`` / ``1,500 s`` — a count, or seconds for a ``*_seconds`` unit,
    whose label ("backtest compute time") carries no unit of its own."""
    return f"{n:,} s" if str(unit).endswith("_seconds") else f"{n:,}"


def is_free_plan(info: dict) -> bool:
    """True when the server named the caller's plan and it is ``free``.

    The one switch on a plan-limit wall (D-12): only the free plan's wall
    carries the "Paid plans include more …" sentence. A server that named no
    plan gets the paid shape — the sentence is never guessed onto a wall.
    """
    plan = info.get("plan")
    return isinstance(plan, str) and plan.strip().lower() == "free"


#: The units whose wall carries the allowance note: what a backtest
#: allowance does NOT pay for (composing, validating, reading results) is
#: true of the backtest units only — on a compose wall it would be false.
_ALLOWANCE_NOTE_UNITS = frozenset({"backtest_runs", "backtest_compute_seconds"})

#: The note (D-12 §4.1, exact copy): what keeps working at the wall.
QUOTA_ALLOWANCE_NOTE = (
    "Composing, validating and reading existing results don't use this allowance."
)


def _is_short_of_need(info: dict) -> bool:
    """True when the refusal fired with allowance still on the meter — a
    submit reserves more than is left (the compute edge, Q-1806)."""
    limit, used, remaining = info.get("limit"), info.get("used"), info.get("remaining")
    return (
        isinstance(limit, int)
        and isinstance(used, int)
        and isinstance(remaining, int)
        and remaining > 0
        and used < limit
    )


def quota_headline(info: dict) -> str:
    """What happened, in one human line — no field names, no instructions.

    ``Weekly backtests used — 50 of 50 on the Free plan`` (free) /
    ``Weekly backtests used — 500 of 500 on your plan`` (any other plan —
    D-12 names only the free plan, the one whose wall explains that paid
    plans include more) / ``Weekly backtest compute time — 10 s of 1,500 s
    left on the Free plan, not enough to start this`` / ``The Free plan does
    not include backtests``. Readable by the person looking at the card AND
    the model reading the envelope; the Q-1806 defect was a card that
    showed the user a line written to steer the model.
    """
    kind = info.get("kind")
    unit = str(info.get("unit") or "")
    label = info.get("label") or info.get("unit_label") or _label_for_unit(unit)
    free = is_free_plan(info)
    on_plan = " on the Free plan" if free else " on your plan"
    if kind in NO_GRANT_KINDS:
        return f"{'The Free plan' if free else 'Your plan'} does not include {label}"
    adjective = _PERIOD_ADJECTIVE.get(str(info.get("period") or ""))
    title = f"{adjective} {label}" if adjective else f"{label[:1].upper()}{label[1:]}"
    limit, used, remaining = info.get("limit"), info.get("used"), info.get("remaining")
    if _is_short_of_need(info):
        return (
            f"{title} — {quota_amount(unit, remaining)} of {quota_amount(unit, limit)} "
            f"left{on_plan}, not enough to start this"
        )
    if isinstance(limit, int) and isinstance(used, int):
        if kind == "cap_exceeded":
            return f"{title} — {used} of {limit} in use{on_plan}, the plan maximum"
        return f"{title} used — {quota_amount(unit, used)} of {quota_amount(unit, limit)}{on_plan}"
    return f"{title} — plan limit reached{on_plan}"


def quota_reset_text(info: dict) -> str | None:
    """``They reset Mon 29 Sep 00:00 UTC.`` — or ``None`` (a cap, or no
    instant the server sent)."""
    return quota_reset_sentence(info).strip() or None


#: The free-plan wall's one plan sentence (D-12 Q1's recorded FALLBACK,
#: taken 2026-10-01 — Q-2268): where plans are changed, and nothing about
#: what other plans include. D-12 first shipped "Paid plans include more
#: <unit>; plans are changed in the Keel web app." and recorded, so nobody
#: re-litigates it, that if a reviewer objects the first clause is dropped
#: and the rest kept. The round-2 audit read the first clause as a plan
#: pitch on the listed surface, so the fallback is now the sentence.
FREE_PLAN_SENTENCE = "Plans are changed in the Keel web app."


def quota_plans_line(info: dict) -> str | None:
    """The one plan sentence (D-12 Q1, its recorded fallback) — FREE plan
    only, else ``None``.

    :data:`FREE_PLAN_SENTENCE`: no plan name, number, price, verb, link or
    capacity claim — only where plans are changed. A paid user already knows
    plans exist, so their wall carries no plan sentence at all (Q2).
    """
    if not is_free_plan(info):
        return None
    return FREE_PLAN_SENTENCE


def quota_allowance_note(info: dict) -> str | None:
    """:data:`QUOTA_ALLOWANCE_NOTE` on a backtest-unit wall, else ``None``."""
    return QUOTA_ALLOWANCE_NOTE if str(info.get("unit") or "") in _ALLOWANCE_NOTE_UNITS else None


def quota_wall_lines(info: dict) -> list[str]:
    """The plan-limit wall, one line per sentence group (D-12 §4.1, exact).

    Free plan::

        Weekly backtests used — 50 of 50 on the Free plan. They reset Mon 29 Sep 00:00 UTC.
        Plans are changed in the Keel web app.
        Composing, validating and reading existing results don't use this allowance.

    Any paid plan: the first and third lines, "on your plan".

    The ONE computation owner of the wall's copy: the 403 ``message``, the
    handoff's ``talking_points`` and its card ``limit_view`` all read these
    lines, so they cannot drift apart. Each line passes
    :func:`assert_neutral_wall_text` before it leaves.
    """
    free = is_free_plan(info)
    first = quota_headline(info) + "."
    reset = quota_reset_text(info)
    if reset:
        first = f"{first} {reset}"
    lines = [first]
    for extra in (quota_plans_line(info), quota_allowance_note(info)):
        if extra:
            lines.append(extra)
    return [assert_neutral_wall_text(line, free_plan=free) for line in lines]


def _quota_message(info: dict) -> str:
    """The message for a plan-limit 403 — :func:`quota_wall_lines`, joined.

    Copy posture (mcp-conversion D-12, 2026-09-28 — decisions.md): the
    caller's own limit and when it resets; on the free plan, that paid plans
    include more; what does not use the allowance. No other plan's name or
    number, no URL, no field names and no imperatives — every card renders
    ``message`` verbatim, and it used to end with a directive to the model
    (Q-1806) and, later, name the other tiers (Q-2080). ``retryable: false``
    and the structured numbers say the rest.
    """
    return " ".join(quota_wall_lines(info))


def _translate_403(body: str) -> "EntitlementError":
    """Distinguish a plan-limit 403 from a scope-missing 403.

    **Plan-limit case** — the body carries a quota ``code`` (or, from an
    older server, an ``entitlement:`` reason). Recovery is neither a tool
    call nor a re-auth. ``recovery_tool`` is explicitly None so the agent
    cannot fall into a "re-auth and try again" loop that can never clear a
    quota, and the served numbers ride in ``input``. There is no
    ``docs_url``: agent surfaces carry no plan destination (D-12, which
    supersedes D-3/D-9 there — the billing tab starts Stripe Checkout).

    **Scope-missing case** — the caller has the permission concept but the
    OAuth token doesn't carry the right scope tier (typically live trading
    without ``runner.*``). Where ``keel_auth_login`` is registered (the CLI
    and local MCP), recovery IS a tool call: re-login with
    ``keel_auth_login(scope='live')``. A hosted or listed server registers
    neither that tool nor a live-write tool, so its refusal names neither.
    """
    import json as _json

    # Best-effort JSON parse. keel-api emits RFC 7807 Problem Details.
    payload = {}
    if body:
        try:
            payload = _json.loads(body)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    detail = payload.get("detail")
    detail_text = detail if isinstance(detail, str) and detail else (body or "")
    code = payload.get("code")

    # Provisioning outcomes (keel-api Q-1519): the token authenticated a real
    # principal, and NEITHER re-auth nor a plan change can fix it — so no
    # recovery tool, and the pointer goes where the fix lives.
    if code == "not_provisioned":
        onboarding_url = f"{_app_url()}/onboarding"
        err = EntitlementError(
            detail_text or "Your account is not set up yet. Complete onboarding to continue.",
            error_code="not_provisioned",
            suggestion=(
                f"The account has no organization yet. Direct the user to finish "
                f"onboarding at {onboarding_url}; re-authenticating will not help."
            ),
            docs_url=onboarding_url,
            input={"code": code, "onboarding_url": onboarding_url},
        )
        err.recovery_tool = None
        err.recovery_tool_args = None
        return err
    if code == "account_deactivated":
        err = EntitlementError(
            detail_text or "This account has been deactivated.",
            error_code="account_deactivated",
            suggestion=(
                "This account is deactivated. Re-authenticating cannot change it; "
                "the user should contact support."
            ),
            input={"code": code},
        )
        err.recovery_tool = None
        err.recovery_tool_args = None
        return err

    info = _quota_context(payload, payload.get("reasons"))

    if info is not None:
        # D-12: facts only, and no destination — no `docs_url`, no URL in
        # `example`. The limit lifts at the reset; the wall says so and stops.
        suggestion = assert_neutral_wall_text(
            f"A plan limit on {info['label']} stopped this call; `example` carries "
            "the numbers and the reset instant. Neither a retry nor a new sign-in "
            "changes it before the reset.",
            free_plan=is_free_plan(info),
        )
        err = EntitlementError(
            _quota_message(info),
            suggestion=suggestion,
            input=info,
        )
        # Override the class-level recovery_tool for the plan-limit case —
        # re-auth doesn't clear a quota.
        err.recovery_tool = None
        err.recovery_tool_args = None
        return err

    if not _local_tools_registered():
        # Hosted / listed: no live-write tool and no `keel_auth_login` is
        # registered here, so the refusal names neither (spec 05 §4 item 7).
        err = EntitlementError(
            detail_text or "403 Forbidden — the server refused this call for this account.",
            suggestion=(
                "The server refused this call for this account; `keel_account_status` "
                "reports the session and its plan limits."
            ),
        )
        err.recovery_tool = None
        err.recovery_tool_args = None
        return err

    # Not a plan limit → the scope-missing default shape.
    return EntitlementError(
        detail_text
        or (
            "403 Forbidden. If this is a live-trading tool "
            "(deploy/pause/resume/stop), your session likely lacks the "
            "`runner.*` scope tier — re-login with the live consent: "
            "`keel_auth_login(scope='live')` or `keel auth login --scope live`. "
            "Otherwise check `keel_account_status` for plan limits."
        ),
        suggestion=(
            "Re-login with live scope (`keel_auth_login(scope='live')`) if "
            "this is a live-trading tool, or check `keel_account_status` for plan limits."
        ),
    )
