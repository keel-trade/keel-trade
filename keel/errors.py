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
from dataclasses import dataclass


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
    #: The server's ``reasons`` for a permission-shaped 403 (e.g.
    #: ``credential:scope_denied:backtest.create``), kept so a tool can tell
    #: which action was refused (paper-trading-v2 S06 §3.2.4a). Not part of
    #: the envelope: the refusal's wire shape is unchanged.
    server_reasons: tuple[str, ...] = ()


class ValidationError(KeelError):
    error_code = "validation_failed"
    exit_code = 7


class UsageError(KeelError):
    error_code = "usage_error"
    exit_code = 2


#: keel-api's paper routes answer ``404 {"code": "paper_not_enabled"}`` while
#: paper is off on that environment (S06 §3.2.4 (c), IC-01-11).
PAPER_NOT_ENABLED_CODE = "paper_not_enabled"
PAPER_NOT_ENABLED_MESSAGE = "Paper trading is not available on this Keel environment yet."

#: keel-api's research routes answer ``404 {"detail": "Research is paused.",
#: "code": "RESEARCH_PAUSED"}`` while ``research.enabled`` is off (Research 07
#: §3.4, RL-91 §4). The SDK serves keel-api's sentence, typed on the code,
#: with no next action and no link.
RESEARCH_PAUSED_CODE = "RESEARCH_PAUSED"


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


def _refusal_detail(body: str | None) -> tuple[str | None, dict | None]:
    """keel-api's structured refusal, read the ONE way keel-api reads it.

    Mirrors ``services/keel-api/src/errors.py`` ``refusal_code`` (Q-2368), the
    reader behind the ``api_request refused`` line, so the envelope ``code``
    the agent and the audit row see is the code keel-api logged for the same
    response (Q-2564). Three body shapes carry a code: an RFC 7807 /
    ``CodedHTTPException`` body with a top-level ``code`` (``_problem_code``);
    an ``HTTPException(detail={"code": …})`` (``_server_detail``); and the
    validator-refusal shape ``{"detail": …, "issues": [issue, …]}`` — the
    parse and unknown-Universe-mode 400s (Q-1695/Q-2207) and the lock-gate
    422 — whose code is the FIRST issue's catalog ``code``. A top-level code
    wins. Returns ``(code, machine_half)``; a body naming no code gives
    ``(None, …)`` rather than a fabricated pseudo-code (Q-0425).
    """
    coded = _problem_code(body)
    if coded is not None:
        return coded["code"], coded
    served = _server_detail(body)
    if served is None:
        return None, None
    code = served.get("code")
    if isinstance(code, str) and code:
        return code, served
    issues = served.get("issues")
    if isinstance(issues, list) and issues and isinstance(issues[0], dict):
        first = issues[0].get("code")
        if isinstance(first, str) and first:
            return first, served
    return None, served


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


#: keel-api's backtest SIZE refusal (``utils/backtest_memory.py``, Q-3316):
#: the run is too large to run. Like the window family it is
#: never about the strategy source, and unlike it the dates are only one of
#: three ways to fit — fewer symbols and a coarser clock are the others.
RUN_SIZE_CODES: frozenset[str] = frozenset({"BACKTEST_TOO_LARGE"})


#: keel-api's paper/live TIMEFRAME-FLOOR refusal (plan change spec 01 §C10,
#: contract §L7): the plan runs paper and live strategies at or above a clock
#: floor, and this strategy's ``target_timeframe`` is below it. A plan limit
#: (doc 07 §5, D-14): ``floor``, ``requested``, the caller's plan and the
#: served ``higher_plans`` travel on ``detail``, the message is the floor
#: wall (:func:`floor_wall_lines`), and ``keel_paper_deploy`` turns it into
#: the plan-limit handoff with a card.
PLAN_FLOOR_CODES: frozenset[str] = frozenset({"SERVING_CLOCK_BELOW_PLAN_FLOOR"})

#: The suggestion on a timeframe-floor refusal (D-14 rule 5): an instruction,
#: never a pitch. Backtests are not floored (D-16).
PLAN_FLOOR_SUGGESTION = (
    "Plan limit; resubmitting it unchanged returns the same refusal. Change the "
    "strategy's `target_timeframe` to `floor` or slower; backtests run at every timeframe."
)


def _expectation_for_422(detail: dict | None) -> str:
    """``what_was_expected`` for a 422, specific to the class of refusal.

    Five classes, told apart by the server's machine ``code``: a paper/live
    TIMEFRAME-FLOOR refusal (a plan limit, held to the neutral-wall scan), a
    backtest SIZE refusal (the run's cells, never the source), a backtest
    WINDOW refusal (the dates, never the source), any other CODED refusal
    (the server named what it rejected), and an UNCODED one — the
    strategy-validation gate (an ``issues`` list, no code), where the DSL dry
    run is the right advice.
    """
    code = detail.get("code") if isinstance(detail, dict) else None
    if code in PLAN_FLOOR_CODES:
        return assert_plan_copy(PLAN_FLOOR_SUGGESTION)
    if code in RUN_SIZE_CODES:
        return (
            "A smaller backtest: fewer symbols, a coarser timeframe, or a shorter "
            "window. The message names the largest of each that fits. The strategy "
            "source is valid; resubmitting the same run returns the same refusal."
        )
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
        # A CODED 400 — the parse / unknown-Universe-mode save refusals, whose
        # code is their first issue's (Q-2564) — carries keel-api's code as the
        # envelope `code` and its machine half under `detail`, exactly as the
        # 409 and 422 arms do (Q-1742/Q-1751): the audit row and the agent
        # then read PARAM_EXPRESSION_NOT_ALLOWED, not `usage_error`, which is
        # what a wrong-ARGUMENT call reads. The class and its exit code stay:
        # an uncoded 400 IS a wrong-argument call, and live_deploy /
        # live_update catch the token verdicts by class.
        code, served = _refusal_detail(body)
        err = UsageError(
            _extract_detail(body) or "Bad request — the server rejected the input.",
            error_code=code,
            suggestion=(
                "The message names what the server rejected — fix that input "
                "and retry (a 400 is never fixed by retrying unchanged)."
            ),
        )
        if code is not None:
            err.detail = served
        return err
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
        coded = _problem_code(body)
        if coded is not None and coded.get("code") == PAPER_NOT_ENABLED_CODE:
            # Paper-trading-v2 S06 §3.2.4 (c) (PT2-12, IC-01-11): every paper
            # route answers 404 with this code while keel-api's flag is off.
            # Typed on the CODE, never the sentence; no suggestion — neither a
            # retry, a sign-in nor a plan change fixes it (the
            # `not_provisioned` arm's model).
            err = NotFoundError(PAPER_NOT_ENABLED_MESSAGE, error_code=PAPER_NOT_ENABLED_CODE)
            err.detail = coded
            return err
        if coded is not None and coded.get("code") == RESEARCH_PAUSED_CODE:
            # Research 07 §3.4: one code, keel-api's one sentence, nothing to
            # retry, no sign-in, no plan change and no link.
            err = NotFoundError(
                _extract_detail(body) or "Research is paused.", error_code=RESEARCH_PAUSED_CODE
            )
            err.detail = coded
            err.recovery_tool, err.recovery_tool_args, err.suggestion = None, None, None
            err.retryable = False
            return err
        # `keel_audit_list_last` is not on the listed or hosted surface
        # (spec 05 §4 item 7): named only where this server registers it.
        backtests_where = (
            "`keel_audit_list_last` or the strategy page in the web app"
            if _local_tools_registered()
            else "the strategy page in the web app"
        )
        err = NotFoundError(
            _extract_detail(body) or "Resource not found",
            suggestion=(
                "Verify the id is correct and that you have access. For "
                "strategies, list yours via `keel_strategy_search`. For "
                "commits/versions, list via `keel_strategy_history`. For "
                f"backtests, find via {backtests_where}."
            ),
        )
        # A coded HTTPException(detail={code, …}) 404 keeps its machine half
        # (Q-1742's rule): `unknown_variant`'s `known_variant_ids`, or
        # `unknown_library_entry`'s `known_slugs` (R-011), ride `detail` so a
        # tool can name the valid ids. The class code is unchanged.
        served = _server_detail(body)
        if isinstance(served, dict) and isinstance(served.get("code"), str):
            err.detail = served
        elif coded is not None:
            # A CodedHTTPException 404 (top-level `code` beside a string
            # `detail`) keeps its code too, so a tool can name the next step
            # for it — a screen with no run yet (Q-3634) — whichever of the
            # two coded shapes keel-api raised it in.
            err.detail = coded
        return err
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
        code, detail = _refusal_detail(body)
        err = ValidationError(
            _extract_detail(body) or "Validation failed",
            # keel-api's own machine code IS the envelope `code` (Q-1751):
            # every coded 422 used to flatten to `validation_failed`, so the
            # envelope and the mcp-server audit row (which records `code`)
            # could not tell WINDOW_INVERTED from a DSL error. Read through
            # the one refusal reader (Q-2564), so an issues-only body (the
            # lock-gate 422's LOCK_ERROR) names its gate issue too. The class
            # code stays the fallback for an uncoded body.
            error_code=code,
            suggestion=_expectation_for_422(detail),
        )
        err.detail = detail
        if (
            code in PLAN_FLOOR_CODES
            and isinstance(detail, dict)
            and isinstance(detail.get("floor"), str)
        ):
            # The floor wall in the one grammar (doc 07 §5): the served floor,
            # the strategy's clock, the plans that run faster. `detail` keeps
            # the machine half with the other plans as NAMES only (founder
            # Q1): keel-api's `higher_plans` may carry their floors.
            info = floor_info(detail)
            err.args = (" ".join(floor_wall_lines(info)),)
            projected = {k: detail[k] for k in ("code", "floor", "requested") if k in detail}
            for key in ("plan", "plan_name"):
                if isinstance(detail.get(key), str):
                    projected[key] = detail[key]
            if "higher_plans" in detail or "more_plans" in detail:
                projected["more_plans"] = more_plan_names(detail)
            err.detail = projected
        return err
    if status == 429:
        return rate_limited_error(None, server_words(body))
    if status >= 500:
        err = KeelError(
            f"Server error (HTTP {status})",
            error_code="server_error",
            exit_code=1,
            retryable=True,
            suggestion="Retry in a few seconds. If persistent, check https://status.usekeel.io",
        )
        # The server's own sentence, when it sent a short plain one (keel-api's
        # paper routes answer a 503 with fixed prose such as "The run is
        # paused safely; retry the same update."). Kept off the envelope: only
        # a tool that knows its route's prose renders it (S10, PT2-90).
        # Only a 503 (the paper routes' fixed prose); an unhandled 500's body
        # ("An unexpected error occurred.") must not replace the SDK's
        # write-unconfirmed message (S10 re-review R1).
        detail = _extract_detail(body)
        err.server_message = (
            detail.strip()
            if status == 503
            and isinstance(detail, str)
            and detail.strip()
            and detail is not body
            and len(detail) <= 300
            and not detail.lstrip().startswith(("{", "[", "<"))
            else None
        )
        # A CODED 5xx is a deliberate refusal (keel-api's screen refresh
        # capacity, its storage-unavailable read): keep its code, its machine
        # half and its words, and its own ``retryable`` verdict when it states
        # one (an exhausted capacity does not free itself). ``refused`` tells
        # the client nothing was applied, so it is not a write-unconfirmed.
        code, served = _refusal_detail(body)
        if code is not None:
            err.error_code = code
            err.detail = served
            err.refused = True
            words = _extract_detail(body)
            if isinstance(words, str) and words.strip() and len(words) <= 300:
                err.args = (words.strip(),)
            if isinstance(served.get("retryable"), bool):
                err.retryable = served["retryable"]
            if isinstance(served.get("next"), str) and served["next"]:
                err.suggestion = served["next"]
        return err
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


def server_words(body: str | None) -> str | None:
    """The server's own short plain sentence from a refusal body, or ``None``
    (JSON framing, an HTML page or a long dump is never a message)."""
    detail = _extract_detail(body)
    if (
        isinstance(detail, str)
        and detail.strip()
        and detail is not body
        and len(detail) <= 300
        and not detail.lstrip().startswith(("{", "[", "<"))
    ):
        return detail.strip()
    return None


#: Server words that already state when to retry ("Retry after 15 seconds.",
#: "try again in 2 minutes"): the message then adds only the plan-limit half,
#: never a second retry time (Q-7601 fix round).
_STATES_RETRY_RE = re.compile(
    r"\b(?:retry|try\s+again)\b[^.]*?\b\d+\s*(?:s|secs?|seconds?|mins?|minutes?|h|hours?)\b",
    re.IGNORECASE,
)


def rate_limited_error(retry_after_s: float | None, words: str | None = None) -> KeelError:
    """The ``rate_limited`` refusal: the server's own words when it sent a
    sentence, then the one instruction (doc 07 §5) — a rate limit is not a
    plan limit, and the wait is the server's (``detail.retry_after_s``).

    When the server's words already say when to retry, the message adds
    only ``Rate limit, not a plan limit.`` (``Rate limit exceeded. Retry
    after 15 seconds. Rate limit, not a plan limit.``); the suggestion keeps
    the full instruction with the wait."""
    import math

    wait = None if retry_after_s is None else max(1, math.ceil(retry_after_s))
    retry = f"retry in {wait} s" if wait is not None else "retry in a few seconds"
    instruction = f"Rate limit, not a plan limit; {retry}."
    if isinstance(words, str) and words.strip():
        said = words.strip().rstrip(".")
        tail = "Rate limit, not a plan limit." if _STATES_RETRY_RE.search(said) else instruction
        message = f"{said}. {tail}"
    else:
        message = f"Too many requests in a short time. {instruction}"
    err = KeelError(
        message,
        error_code="rate_limited",
        exit_code=1,
        retryable=True,
        suggestion=instruction,
    )
    if wait is not None:
        err.detail = {"retry_after_s": wait}
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

#: period → the phrase that names the window in a sentence. ``30d`` is the
#: per-account 30-day cycle (plan change spec 01 §C5/§C11): "this 30-day
#: period", never "monthly" — that word is a billing cadence the plan-copy
#: scan refuses, and the cycle is not a calendar month. Read by
#: keel_account_status only; plan-limit copy names no window (D-14 rule 8).
_PERIOD_PHRASES: dict[str, str] = {
    "daily": "today",
    "weekly": "this week",
    "monthly": "this month",
    "30d": "this 30-day period",
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


# ─── The plan-copy guard (mcp-conversion D-14, 2026-11-16; was D-12) ─────
#
# Every plan-limit text an agent surface emits — the 403 message and
# suggestion, the handoff's talking points, reason, suggestion and
# `limit_view`, the run's `quota_notice`, `keel_plan_usage`'s talking
# points — states the caller's own numbers and reset, and names the plans
# that include more of THAT limit (D-14, doc 07 §3). What it may never carry
# is what OpenAI's rule bars and what Keel v1.0.0 was rejected for (Q-2080):
# a price or billing cadence, a link to pricing, billing or checkout, and a
# pitch. The plan NAMES are not banned here: they reach copy only through
# the renderer below (`own_plan_name`, `more_clause`), from served data, and
# tests/test_policy_scan.py holds every payload to "a plan name appears only
# in the own-plan or more clause, and only for a served higher plan".
#
# Expiry urgency joined with the first-week allowance (connect-onboarding
# spec 01 §1.9): the one first-week clause states its end date and nothing
# else. It is a separate constant so the agent-surface guards in
# tests/ read the SAME pattern rather than a copy.
EXPIRY_URGENCY_PATTERN = (
    r"\bbefore\s+(?:they|it)\s+expires?\b"
    r"|\bdon[’']?t\s+lose\b"
    r"|\buse\s+them\s+before\b"
    r"|\brunning\s+out\b"
    r"|\bexpires?\s+soon\b"
)
FORBIDDEN_UPSELL_RE = re.compile(
    r"\$\s?\d"  # a price
    r"|\b\d+\s?(?:usd|dollars)\b|\busd\s?\d"
    r"|\bper month\b|\bper year\b|\b/mo\b|\bmonthly\b|\bannually\b"
    r"|\badds?\s+capacity\b|\bmore\s+capacity\b|\bextra\s+capacity\b"
    r"|\bunlocks?\b"
    r"|\bupgrad(?:e|es|ed|ing)\b|\bupgrade_\w+"
    r"|\bcheckout\b|\bstripe\b"
    r"|\bact\s+now\b|\bhurry\b|\blimited[\s-]time\b|\bdon'?t\s+miss\b"
    # Plan destinations, and the retired ladder's phrasing (Q-2080).
    r"|\bhigher\s+plans?\b|\bplan\s+tiers?\b|\bbuilder\s+fees?\b|\bsee\s+plans\b"
    r"|tab=billing|/pricing\b|/billing\b|/checkout\b"
    # Expiry urgency (connect-onboarding spec 01 §1.9): a time-limited
    # allowance is stated as a fact ("…; they end Tue 13 Oct 15:02 UTC."),
    # never as a push to spend it before it goes.
    r"|" + EXPIRY_URGENCY_PATTERN,
    re.IGNORECASE,
)

#: Pitch words (D-14 §7): refused on PLAN-LIMIT copy. Kept apart from
#: :data:`FORBIDDEN_UPSELL_RE`, which the shared talking-point validator also
#: reads for sign-in and consent walls, where "now" or "only" is a plain word.
PITCH_WORDS_RE = re.compile(
    r"\bnow\b|\bonly\b|\blimited\b|\bdeals?\b|\bsave\b|\bsavings?\b", re.IGNORECASE
)

#: Where plans are changed: plain words, never a URL (D-14 rule 4).
WEB_APP_SENTENCE = "Plans are changed in the Keel web app."

#: The one description clause of every tool whose call spends a backtest
#: (doc 07 §5 "Descriptions", rule 8: one clause, no number, no plan name):
#: `keel_backtest_run` and `keel_screener_run` (a screen run is counted as a
#: backtest run).
BACKTEST_ALLOWANCE_CLAUSE = "Uses one backtest from the plan's allowance; see `keel_plan_usage`."

#: The same clause for `keel_research_run`, which spends a study (D-344 (4), CONFORM-MCP F6; doc 07 §5
#: "Consumable (backtests, compute, studies)": one clause, no number, no plan name).
RESEARCH_ALLOWANCE_CLAUSE = (
    "New work uses one study from the plan's allowance; see `keel_plan_usage`."
)


def assert_plan_copy(text: str) -> str:
    """Raise ``ValueError`` if ``text`` is not legal plan-limit copy (D-14 §7).

    No price, currency or billing cadence; no pricing, billing or checkout
    destination; no pitch word. Returns ``text`` unchanged — validators
    behave one exact way (repo lesson), so a violation is a programming error
    that fails the call rather than a string quietly edited on the way out.
    """
    if not isinstance(text, str):
        raise ValueError(f"plan copy must be a string, got {type(text).__name__}")
    hit = FORBIDDEN_UPSELL_RE.search(text) or PITCH_WORDS_RE.search(text)
    if hit:
        raise ValueError(
            f"plan copy uses forbidden price/link/pitch language {hit.group(0)!r} "
            f"(mcp-conversion D-14 §7): {text!r}"
        )
    return text


def _label_for_unit(unit: str) -> str:
    """Label for a unit the SERVER did not label (a keel-api older than the
    quota contract). Mechanical: the authority is
    ``platform_auth.quota.UNIT_LABELS``, served on the wire; plan-limit copy
    reads :data:`LIMIT_UNITS` first (``_unit_label``)."""
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
    """`` this week`` / `` today`` / `` this month`` / `` this 30-day period``
    — or empty.

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


#: The keys of a served ``first_week`` block (connect-onboarding spec 01
#: §1.8) an agent surface passes through — an allow-list, like the quota
#: block's own, so a future server field never reaches a surface whose
#: policy was not reviewed for it. ``then_remaining`` / ``then_limit`` (plan
#: change spec 01 §C6, both pots) are the plan's own pot once the first-week
#: part is gone, served only where that part is spent first (v2 Free).
FIRST_WEEK_KEYS: tuple[str, ...] = (
    "granted",
    "remaining",
    "ends_at",
    "then_remaining",
    "then_limit",
)


def project_first_week(block: object) -> dict | None:
    """A served ``first_week`` block projected to :data:`FIRST_WEEK_KEYS`,
    or ``None`` when the server sent none (the key is absent unless a
    first-week allowance is active — spec 01 §1.8)."""
    if not isinstance(block, dict):
        return None
    projected = {k: block[k] for k in FIRST_WEEK_KEYS if k in block}
    return projected or None


def _is_count(n: object) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def render_first_week_clause(block: object) -> str | None:
    """THE first-week clause (Q-7601 fix round, D-14 rule 8), folded into a
    backtests line in parentheses, from the block's own served ``first_week``::

        >>> render_first_week_clause({"unit": "backtest_runs", "first_week":
        ...     {"granted": 50, "remaining": 12, "ends_at": "2026-11-23T18:37:25Z"}})
        '(12 first-week extras until Mon 23 Nov)'

    The served ``limit`` / ``remaining`` already include the first-week part
    (``platform_auth.entitlements.get_consumable_balance``: ``granted`` adds
    the grant while it is active), so the clause says how many of those are
    first-week extras and the date they go, nothing more: no window phrase,
    no "then" pot, no time of day (the exact instant stays in the structured
    ``first_week.ends_at``).

    ``None`` — no clause at all — unless the block counts backtests (the
    clause is a backtest count, connect-onboarding spec 01 §1.9), the
    first-week part still has some left, and its facts arrived whole. A
    spent or expired first week says nothing: "0 of 50 first-week backtests
    left until …" read as if nothing could run until that date (staging
    probe, 2026-11-16).
    """
    if not isinstance(block, dict) or str(block.get("unit") or "") != "backtest_runs":
        return None
    first_week = block.get("first_week")
    if not isinstance(first_week, dict):
        return None
    granted, remaining = first_week.get("granted"), first_week.get("remaining")
    if not (_is_count(granted) and _is_count(remaining)) or remaining <= 0:
        return None
    when = quota_reset_instant(first_week.get("ends_at"))
    if when is None:
        return None
    return f"({remaining:,} first-week extras until {format_reset_date(when)})"


def first_week_from_reason(info: dict) -> dict | None:
    """The both-pots first week a 403 carries (``platform_auth.quota``'s
    ``first_week_*`` reason keys), as a ``first_week`` block — or ``None``.
    Structured data only: the wall's line folds
    :func:`render_first_week_clause` from it.

    Only the full bonus-first set is read (granted, remaining, end, and the
    plan's own pot): a v1 wall carries the first pair alone and keeps
    today's lines exactly.
    """
    keys = (
        "first_week_granted",
        "first_week_remaining",
        "first_week_ends_epoch",
        "first_week_then_remaining",
        "first_week_then_limit",
    )
    if not all(_is_count(info.get(k)) for k in keys):
        return None
    when = quota_reset_instant(info["first_week_ends_epoch"])
    if when is None:
        return None
    return {
        "granted": info["first_week_granted"],
        "remaining": info["first_week_remaining"],
        "ends_at": when.isoformat().replace("+00:00", "Z"),
        "then_remaining": info["first_week_then_remaining"],
        "then_limit": info["first_week_then_limit"],
    }


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
    label = _unit_label(info)
    out: dict = {"unit": unit, "label": label, "unit_label": label}
    for key in ("kind", "code", "limit", "used", "remaining", "period", "reset_epoch", "need"):
        if info.get(key) is not None:
            out[key] = info[key]
    if info.get("resets_at") is not None:
        out["resets_at"] = info["resets_at"]
    # The caller's OWN plan, and the NAMES of the plans that include more of
    # this unit (D-14). keel-api's `higher_plans` also carries those plans'
    # limits (D-10: the API carries numbers); only the names are projected,
    # as `more_plans`, so no other plan's number reaches `example`,
    # `limit_details` or any sentence (founder Q1, Q-2080). An absent
    # `higher_plans` projects nothing: no more clause is ever guessed.
    if isinstance(info.get("plan"), str) and info["plan"]:
        out["plan"] = info["plan"]
    if isinstance(info.get("plan_name"), str) and info["plan_name"].strip():
        out["plan_name"] = info["plan_name"].strip()
    if isinstance(info.get("higher_plans"), list):
        out["more_plans"] = more_plan_names(info)
    # Both pots (plan change spec 01 §C6): the first-week part and the plan's
    # own pot, served on the 403 only where the first-week part is spent
    # first. Absent on every other wall, which therefore keeps its lines.
    first_week = first_week_from_reason(info)
    if first_week is not None:
        out["first_week"] = first_week
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
        info: dict = {"kind": kind, "unit": unit}
        for p in parts[extras_start:]:
            if "=" in p:
                k, v = p.split("=", 1)
                try:
                    info[k] = int(v)
                except ValueError:
                    info[k] = v
        return info
    return None


# ─── Plan limits the agent can explain (mcp-conversion D-14, doc 07) ─────
#
# ONE renderer for every plan-limit sentence on an agent surface: the wall
# (403 message, handoff talking points, card), the near-limit notice on a
# successful result, and `keel_plan_usage`. It is keyed by limit KIND, not by
# unit, and every plan name, number and floor in its output is SERVED data
# (doc 07 §6.1): a new plan, a rename, a re-priced tier or a new limit is a
# `plans.yaml` edit, never an SDK change. The one SDK table is
# :data:`LIMIT_UNITS` (unit → label and kind); a test reads plans.yaml and
# fails when a unit a listed tool can hit has no row, or a row's kind
# disagrees with the unit's ``type`` there.

#: The four limit kinds (doc 07 §5). plans.yaml's ``type`` maps onto them:
#: consumable → consumable, cap → cap, boolean → feature; a floor is a
#: ``setting`` or a plan attr (``paper_live_clock_floor``).
CONSUMABLE = "consumable"
CAP = "cap"
FLOOR = "floor"
FEATURE = "feature"
LIMIT_KINDS: tuple[str, ...] = (CONSUMABLE, CAP, FLOOR, FEATURE)


@dataclass(frozen=True)
class LimitUnit:
    """One unit's row: what the renderer needs that the wire does not carry.

    ``label`` is ``platform_auth.quota.UNIT_LABELS``'s noun (a test pins the
    two equal; the served label still wins at runtime). ``held`` / ``frees`` / ``slot``
    are a cap's words: what a used slot is doing, how one frees, and the
    one-slot noun of the last-slot notice. ``absent`` is a feature wall's
    own head and remedy, for a unit whose generic sentence reads badly.
    """

    label: str
    kind: str
    held: str = "in use"
    frees: str | None = None
    slot: str | None = None
    absent: tuple[str, str] | None = None


#: The one unit table (doc 07 §6.1). A new unit is one row.
LIMIT_UNITS: dict[str, LimitUnit] = {
    "backtest_runs": LimitUnit("backtests", CONSUMABLE),
    "backtest_compute_seconds": LimitUnit("backtest compute time", CONSUMABLE),
    "research_studies": LimitUnit("studies", CONSUMABLE),
    "research_compute_seconds": LimitUnit("research time", CONSUMABLE),
    "paper_strategies_max": LimitUnit(
        "paper strategies",
        CAP,
        held="running",
        frees="stop one to free it",
        slot="paper slot",
    ),
    "live_strategies_max": LimitUnit(
        "live strategies",
        CAP,
        held="running",
        frees="stop one to free it",
        slot="live slot",
    ),
    "screeners_live_max": LimitUnit(
        "auto-refreshing screens", CAP, frees="turn one off to free it"
    ),
    "screeners_published_max": LimitUnit("public screens", CAP, frees="unpublish one to free it"),
    "screeners_private_max": LimitUnit(
        "private auto-refreshing screens",
        CAP,
        frees="turn one off to free it",
        absent=("Private screen auto-refresh", "publish it to keep refreshing"),
    ),
    "screeners_max": LimitUnit("saved screens", CAP),
    "research_shares_max": LimitUnit("shared studies", CAP),
    "research_saved_studies_max": LimitUnit("saved studies", CAP),
    "research_evidence_bytes_max": LimitUnit("stored study results", CAP),
    "feature:research": LimitUnit("running studies", FEATURE),
    "feature:research_copy": LimitUnit("making a study yours", FEATURE),
    "feature:research_agent": LimitUnit("research with your agent", FEATURE),
    "feature:research_repick": LimitUnit("Re-picking settings", FEATURE),
    "feature:research_template_check": LimitUnit("Check a strategy", FEATURE),
    "feature:research_template_signal": LimitUnit("Study a signal", FEATURE),
    "feature:research_template_compare": LimitUnit("Compare", FEATURE),
    "feature:research_template_map": LimitUnit("Map a strategy's settings", FEATURE),
    "feature:research_template_entries": LimitUnit("Entries and exits", FEATURE),
    "screener_refresh_min_minutes": LimitUnit("screen refresh", FLOOR),
    "paper_live_clock_floor": LimitUnit("paper and live timeframe", FLOOR),
}

#: The more clause's verb by kind, singular then plural (doc 07 §4/§5):
#: "Pro includes more." / "Trader and Pro include more." · caps "run more" ·
#: floors "run faster" · features "include it".
_MORE_VERBS: dict[str, tuple[str, str]] = {
    CONSUMABLE: ("includes more", "include more"),
    CAP: ("runs more", "run more"),
    FLOOR: ("runs faster", "run faster"),
    FEATURE: ("includes it", "include it"),
}

#: The notice tiers that carry the more clause (founder Q3, doc 07 §9): 75%,
#: 90% and exhausted. The 50% notice stays a bare count.
MORE_CLAUSE_TIERS: frozenset[str] = frozenset({"warn", "critical", "exhausted"})


def join_plan_names(names: list[str]) -> str:
    """``Pro`` · ``Trader and Pro`` · ``A, B and C``."""
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def more_clause(kind: str, names: list[str] | tuple[str, ...] | None) -> str | None:
    """THE more clause (doc 07 §4), or ``None`` when no plan includes more.

    ``names`` are the served display names of the plans that beat the
    caller on this limit, in catalog order. Empty (the top plan) → ``None``:
    Pro gets nothing.

        >>> more_clause("consumable", ["Trader", "Pro"])
        'Trader and Pro include more.'
        >>> more_clause("cap", ["Pro"])
        'Pro runs more.'
    """
    if kind not in _MORE_VERBS:
        raise ValueError(f"unknown limit kind {kind!r} (one of {LIMIT_KINDS})")
    clean = [n.strip() for n in (names or ()) if isinstance(n, str) and n.strip()]
    if not clean:
        return None
    one, many = _MORE_VERBS[kind]
    return f"{join_plan_names(clean)} {one if len(clean) == 1 else many}."


def plan_display_name(plan: str | None) -> str | None:
    """``free`` → ``Free``. The served plan id, capitalised — the fallback
    for a server that sent no ``plan_name`` (the same identity, never a
    lookup table that could name a plan the server did not)."""
    return plan.capitalize() if isinstance(plan, str) and plan else None


def own_plan_name(info: dict) -> str | None:
    """The caller's own plan's display name: the served ``plan_name``, else
    the served plan id capitalised, else ``None``."""
    if not isinstance(info, dict) or info.get("plan") == "anon":
        return None  # an anonymous workspace is not a plan; it reads "this plan"
    name = info.get("plan_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return plan_display_name(info.get("plan"))


def more_plan_names(info: dict) -> list[str]:
    """The names of the plans that include more of this limit, from served
    data only: the projected ``more_plans`` (names), else keel-api's
    ``higher_plans`` entries' ``name`` (the served plan id capitalised for
    an entry with none). Absent → ``[]``: no clause is ever guessed."""
    if not isinstance(info, dict):
        return []
    projected = info.get("more_plans")
    if isinstance(projected, list):
        return [n for n in projected if isinstance(n, str) and n.strip()]
    served = info.get("higher_plans")
    if not isinstance(served, list):
        return []
    names: list[str] = []
    for entry in served:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        name = name.strip() if isinstance(name, str) and name.strip() else None
        name = name or plan_display_name(entry.get("plan"))
        if name and name not in names:
            names.append(name)
    return names


def limit_unit(unit: object) -> LimitUnit | None:
    return LIMIT_UNITS.get(str(unit or ""))


def limit_kind(info: dict) -> str:
    """The kind a refusal or block renders as. A no-grant denial is a
    FEATURE wall whatever its unit's type (Free has no private screens: a
    cap unit, refused as "isn't on Free"); otherwise the unit's row, then
    the server's own code."""
    kind, code = info.get("kind"), info.get("code")
    if kind in NO_GRANT_KINDS or code == "plan_feature_unavailable":
        return FEATURE
    if kind in (FLOOR, "clock_below_floor"):
        return FLOOR
    row = limit_unit(info.get("unit"))
    if row is not None:
        return row.kind
    if kind == "cap_exceeded" or code == "quota_cap_reached":
        return CAP
    return CONSUMABLE


def limit_more_clause(info: dict) -> str | None:
    """The more clause for this refusal or block, from its served names."""
    return more_clause(limit_kind(info), more_plan_names(info))


def _unit_label(info: dict) -> str:
    unit = str(info.get("unit") or "")
    served = info.get("label") or info.get("unit_label")
    if isinstance(served, str) and served:
        return served
    row = limit_unit(unit)
    return row.label if row is not None else _label_for_unit(unit)


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def format_reset_date(when) -> str:
    """``Sat 5 Dec`` — the date a sentence carries (doc 07 §4). The exact
    instant stays in the structured ``resets_at``."""
    return f"{when:%a} {when.day} {when:%b}"


def quota_amount(unit: str, n: int) -> str:
    """``500`` / ``1,500 s`` — a count, or seconds for a ``*_seconds`` unit,
    whose label ("backtest compute time") carries no unit of its own."""
    return f"{n:,} s" if str(unit).endswith("_seconds") else f"{n:,}"


def _is_short_of_need(info: dict) -> bool:
    """True when the refusal fired with allowance still on the meter — a
    submit reserves more than is left (the compute edge, Q-1806)."""
    limit, used, remaining = info.get("limit"), info.get("used"), info.get("remaining")
    return (
        _is_count(limit)
        and _is_count(used)
        and _is_count(remaining)
        and remaining > 0
        and used < limit
    )


def quota_headline(info: dict) -> str:
    """What happened, in one human line — no field names, no instructions.

    ``Backtests: 25 of 25 used on Free`` · ``Backtest compute time: 10 s of
    750 s left on Free, not enough to start this`` · ``Paper strategies: 1 of
    1 running on Free`` · ``Private screen auto-refresh isn't on Free`` ·
    ``Free doesn't include Compare``. Readable by the person looking at the
    card AND the model reading the envelope (Q-1806).
    """
    unit = str(info.get("unit") or "")
    label = _unit_label(info)
    plan = own_plan_name(info)
    on = f"on {plan}" if plan else "on this plan"
    kind = limit_kind(info)
    row = limit_unit(unit)
    if kind == FEATURE:
        if row is not None and row.absent:
            return f"{row.absent[0]} isn't on {plan or 'this plan'}"
        return f"{plan or 'This plan'} doesn't include {label}"
    limit, used, remaining = info.get("limit"), info.get("used"), info.get("remaining")
    title = _capital(label)
    if kind == CAP:
        if _is_count(limit) and _is_count(used):
            held = row.held if row is not None else "in use"
            return f"{title}: {used:,} of {limit:,} {held} {on}"
        return f"{title}: plan limit reached {on}"
    if _is_short_of_need(info):
        return (
            f"{title}: {quota_amount(unit, remaining)} of {quota_amount(unit, limit)} "
            f"left {on}, not enough to start this"
        )
    if _is_count(limit) and _is_count(used):
        return f"{title}: {quota_amount(unit, used)} of {quota_amount(unit, limit)} used {on}"
    return f"{title}: plan limit reached {on}"


def quota_after_clause(info: dict) -> str | None:
    """What follows the headline: ``resets Sat 5 Dec`` (a consumable with a
    served instant) · a cap's ``stop one to free it`` (or ``it doesn't
    reset``) · a feature's own remedy · ``None``."""
    kind = limit_kind(info)
    row = limit_unit(info.get("unit"))
    if kind == FEATURE:
        return row.absent[1] if row is not None and row.absent else None
    if kind == CAP:
        return (row.frees if row is not None and row.frees else None) or "it doesn't reset"
    when = quota_reset_instant(info)
    return f"resets {format_reset_date(when)}" if when else None


def quota_reset_text(info: dict) -> str | None:
    """The card's second line: :func:`quota_after_clause` as a sentence."""
    after = quota_after_clause(info)
    return f"{_capital(after)}." if after else None


def quota_plans_line(info: dict) -> str | None:
    """The wall's plan line (doc 07 §4): the more clause, then where plans
    are changed — or ``None`` when no plan includes more (Pro, or a server
    that named none: never guessed)."""
    more = limit_more_clause(info)
    return f"{more} {WEB_APP_SENTENCE}" if more else None


#: The units whose wall carries the allowance note: what a backtest
#: allowance does NOT pay for (composing, validating, reading results) is
#: true of the backtest units only — on a compose wall it would be false.
_ALLOWANCE_NOTE_UNITS = frozenset({"backtest_runs", "backtest_compute_seconds"})

#: The note (D-12 §4.1, kept by D-14): what keeps working at the wall.
QUOTA_ALLOWANCE_NOTE = (
    "Composing, validating and reading existing results don't use this allowance."
)


def quota_allowance_note(info: dict) -> str | None:
    """:data:`QUOTA_ALLOWANCE_NOTE` on a backtest-unit wall, else ``None``."""
    return QUOTA_ALLOWANCE_NOTE if str(info.get("unit") or "") in _ALLOWANCE_NOTE_UNITS else None


def quota_is_concurrent_cap(info: dict) -> bool:
    """True for a limit on how many run AT ONCE (a cap, no reset): it never
    refills on a clock, so "lifts at the reset" is false for it (QA-W1)."""
    return limit_kind(info) == CAP and quota_reset_instant(info) is None


def quota_wall_lines(info: dict) -> list[str]:
    """The plan-limit wall, one line per sentence group (doc 07 §4/§5)::

        Backtests: 25 of 25 used on Free; resets Sat 5 Dec.
        Trader and Pro include more. Plans are changed in the Keel web app.
        Composing, validating and reading existing results don't use this allowance.

        Paper strategies: 1 of 1 running on Free; stop one to free it.
        Trader and Pro run more. Plans are changed in the Keel web app.

    Pro: no plan line. The ONE computation owner of the wall's copy: the
    403 ``message``, the handoff's ``talking_points`` and its card
    ``limit_view`` all read these lines, so they cannot drift apart. Each
    line passes :func:`assert_plan_copy` before it leaves.
    """
    after = quota_after_clause(info)
    clause = render_first_week_clause(info)
    head = quota_headline(info) + (f" {clause}" if clause else "")
    lines = [head + (f"; {after}." if after else ".")]
    for extra in (quota_plans_line(info), quota_allowance_note(info)):
        if extra:
            lines.append(extra)
    return [assert_plan_copy(line) for line in lines]


#: The agent's instruction at a wall (doc 07 §4, D-14 rule 5): what not to
#: do, and what to tell the user. Never a pitch.
_WAIT_FOR: dict[str, str] = {
    CONSUMABLE: "don't retry before the reset",
    CAP: "don't retry before a slot frees",
    FEATURE: "retrying doesn't change it",
    FLOOR: "resubmitting it unchanged returns the same refusal",
}


def quota_suggestion(info: dict) -> str:
    """``Plan limit; don't retry before the reset. Tell the user the numbers
    and which plans include more.`` — the more half only when the server
    named a plan that does."""
    kind = limit_kind(info)
    tell = "the floor" if kind == FLOOR else "the numbers"
    if kind == FEATURE:
        tell = "what this plan includes"
    tail = f"Tell the user {tell}"
    if more_plan_names(info):
        verb = {FLOOR: "run faster", FEATURE: "include it"}.get(kind, "include more")
        tail += f" and which plans {verb}"
    return assert_plan_copy(f"Plan limit; {_WAIT_FOR[kind]}. {tail}.")


def render_quota_sentence(block: dict) -> str | None:
    """ONE factual count line from a served quota block (doc 07 §5)::

        >>> render_quota_sentence({"unit": "backtest_runs", "label": "backtests",
        ...     "limit": 25, "used": 21, "remaining": 4,
        ...     "resets_at": "2026-12-05T00:00:00Z"})
        '4 of 25 backtests left; resets Sat 5 Dec.'

    A cap counts what is held, and the last slot says so: ``Last paper slot
    in use (1 of 1).`` ``None`` when the block carries no finite allowance
    (an unlimited unit, or a server that sent no numbers) — rendering "left"
    without a number is exactly the null-number sentence Q-1590 shipped.
    """
    if not isinstance(block, dict) or block.get("unlimited"):
        return None
    limit, remaining = block.get("limit"), block.get("remaining")
    if not _is_count(limit) or not _is_count(remaining):
        return None
    unit = str(block.get("unit") or "")
    label = _unit_label(block)
    row = limit_unit(unit)
    if limit_kind(block) == CAP:
        used = block.get("used") if _is_count(block.get("used")) else max(0, limit - remaining)
        if remaining == 0 and row is not None and row.slot:
            return f"Last {row.slot} in use ({used:,} of {limit:,})."
        held = row.held if row is not None else "in use"
        return f"{_capital(label)}: {used:,} of {limit:,} {held}."
    when = quota_reset_instant(block)
    reset_clause = f"; resets {format_reset_date(when)}" if when else ""
    clause = render_first_week_clause(block)
    extras = f" {clause}" if clause else ""
    return (
        f"{quota_amount(unit, remaining)} of {quota_amount(unit, limit)} {label} "
        f"left{extras}{reset_clause}."
    )


def notice_more_clause(block: dict) -> str | None:
    """A notice's more clause: only at the warn, critical and exhausted
    tiers (founder Q3), from the block's served names."""
    if not isinstance(block, dict) or block.get("tier") not in MORE_CLAUSE_TIERS:
        return None
    return limit_more_clause(block)


def render_quota_notice(block: dict) -> str | None:
    """The near-limit notice: :func:`render_quota_sentence` (which folds the
    first-week clause in, while one has some left) and the more clause at
    the warn, critical and exhausted tiers::

        4 of 25 backtests left; resets Sat 5 Dec. Trader and Pro include more.
        37 of 75 backtests left (12 first-week extras until Mon 23 Nov); resets Wed 16 Dec. Trader and Pro include more.
    """
    sentence = render_quota_sentence(block)
    if sentence is None:
        return None
    parts = [sentence, notice_more_clause(block)]
    return assert_plan_copy(" ".join(p for p in parts if p))


def _quota_message(info: dict) -> str:
    """The message for a plan-limit 403 — :func:`quota_wall_lines`, joined.

    Copy posture (mcp-conversion D-14, 2026-11-16 — decisions.md): the
    caller's own numbers and reset, the plans that include more of this
    limit, where plans are changed, and what does not use the allowance. No
    price, URL, field name or pitch — every card renders ``message``
    verbatim (Q-1806, Q-2080). ``retryable: false``, the suggestion and the
    structured numbers say the rest.
    """
    return " ".join(quota_wall_lines(info))


# ─── The paper/live timeframe floor (doc 07 §5, D-14) ────────────────────


def floor_info(detail: dict) -> dict:
    """A served floor refusal or ``serving_floor`` block as the renderer's
    info dict (kind FLOOR)."""
    out: dict = {"unit": "paper_live_clock_floor", "kind": FLOOR}
    for key in ("floor", "requested", "plan", "plan_name", "higher_plans", "more_plans"):
        if detail.get(key) is not None:
            out[key] = detail[key]
    return out


def floor_headline(info: dict) -> str:
    """``Paper and live on Free need 1h or slower; this is 15min`` — the
    served floor and requested clock, verbatim."""
    plan = own_plan_name(info)
    where = f"on {plan}" if plan else "on this plan"
    head = f"Paper and live {where} need {info['floor']} or slower"
    requested = info.get("requested")
    return f"{head}; this is {requested}" if isinstance(requested, str) and requested else head


def floor_change_line(info: dict, *, card: bool = False) -> str:
    """What to change: the DSL field for the agent (the handoff's
    ``message`` and talking points), plain words on the card's
    ``limit_view.reset`` (which draws no code)."""
    what = "the strategy's timeframe" if card else "`target_timeframe`"
    return f"Change {what} to {info['floor']} or slower."


def floor_wall_lines(info: dict, *, card: bool = False) -> list[str]:
    """The floor refusal's wall (doc 07 §5)::

        Paper and live on Free need 1h or slower; this is 15min.
        Change `target_timeframe` to 1h or slower.
        Trader and Pro run faster. Plans are changed in the Keel web app.

    ``card`` renders the change line in the card's plain words.
    """
    lines = [floor_headline(info) + ".", floor_change_line(info, card=card)]
    more = limit_more_clause(info)
    if more:
        lines.append(f"{more} {WEB_APP_SENTENCE}")
    return [assert_plan_copy(line) for line in lines]


def floor_notice(block: object) -> str | None:
    """The backtest result's heads-up from a served ``serving_floor``::

        Note: paper and live on Free need 1h or slower; this is 15min. Trader and Pro run faster.

    ``None`` without a served floor."""
    if not isinstance(block, dict) or not isinstance(block.get("floor"), str):
        return None
    info = floor_info(block)
    head = floor_headline(info)
    sentence = f"Note: {head[:1].lower()}{head[1:]}."
    more = limit_more_clause(info)
    return assert_plan_copy(f"{sentence} {more}" if more else sentence)


#: What a research refusal with its own RESEARCH_* code expects.
RESEARCH_REFUSAL_EXPECTED = "The message says what this call needs."


def _research_refusal(payload: dict) -> "EntitlementError | None":
    """A research route's 403 (RI-237; 07 §3.4), or ``None`` for any other body.

    keel-api's research routes raise ``HTTPException(403, detail={"code",
    "message", "unit"})``: the code is one of the existing quota codes
    (``quota_exhausted``, ``quota_cap_reached``, ``plan_feature_unavailable``)
    or a ``RESEARCH_*`` code, and the message is the served sentence
    (``libs/research/words.py``, RL-53). Until RI-237 the nested shape fell to
    the scope-missing arm: code ``None``, the raw body as the message, and on
    the full profile "Re-login with live scope". Now the message is the served
    words, ``detail`` keeps the machine half (``code``, ``unit``), nothing
    re-authenticates, and a ``RESEARCH_*`` code is the envelope's own code (as
    a screen refusal's is). The quota codes keep ``insufficient_entitlements``
    with the specific code in ``detail.code`` (07 §3.4's row). A nested dict
    without a string message, or with any other code, is not this shape.
    """
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    code, words = detail.get("code"), detail.get("message")
    if not isinstance(code, str) or not isinstance(words, str) or not words.strip():
        return None
    own = code.startswith("RESEARCH_")
    if code not in QUOTA_CODES and not own:
        return None
    err = EntitlementError(
        words.strip(),
        # D-14 rule 5 (ENGINE relay, D-350): a research wall carries the mature tools' instruction ("Plan limit;
        # don't retry before the reset. Tell the user the numbers …"), from the same quota_suggestion over the unit.
        suggestion=RESEARCH_REFUSAL_EXPECTED if own else quota_suggestion({**detail, "code": code}),
    )
    if own:
        err.error_code = code
    err.detail = {k: v for k, v in detail.items() if k != "message"}
    err.recovery_tool = None
    err.recovery_tool_args = None
    return err


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
    served_reasons = payload.get("reasons")
    reasons = tuple(
        r
        for r in (served_reasons if isinstance(served_reasons, list) else ())
        if isinstance(r, str)
    )

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

    # RI-237 (research spec 07 §3.3-§3.4): a research route refuses with its own
    # served words nested under `detail` ({"code", "message", "unit"}); read that
    # shape before anything below, so the words are the message and the raw body
    # never is. Every other 403 takes the arms below exactly as before.
    research = _research_refusal(payload)
    if research is not None:
        return research

    info = _quota_context(payload, payload.get("reasons"))

    if info is not None:
        # D-14: the agent's instruction, never a pitch — and no destination
        # (no `docs_url`, no URL in `example`). A metered limit lifts at the
        # reset; a cap when a slot frees (QA-W1); a feature not at all.
        suggestion = quota_suggestion(info)
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

    # A CODED non-quota 403 (a feature flag such as SCREENER_PUBLISH_DISABLED)
    # keeps its machine half on `detail`, as the 404/409/422 arms do (Q-1742),
    # so a command can name the refusal's own next step (spec 06 §D1.7).
    machine, served = _refusal_detail(body)
    coded = served if machine is not None and machine not in QUOTA_CODES else None
    # A screen refusal (SCREENER_REFRESH_BELOW_FLOOR, a feature flag) is the
    # API's own coded 403, never a missing scope: it keeps its code and words.
    screen_refusal = coded is not None and machine.startswith(("SCREENER_", "SCREEN_"))
    if screen_refusal and not (isinstance(detail, str) and detail):
        # keel-api's HTTPException(detail={"code", "detail": <words>, ...})
        # nests its sentence; the raw body is never the message.
        words = _extract_detail(body)
        if isinstance(words, str) and words.strip() and not words.lstrip().startswith("{"):
            detail_text = words.strip()

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
        err.server_reasons = reasons
        err.detail = coded
        if screen_refusal:
            err.error_code = machine
        return err

    # Not a plan limit → the scope-missing default shape.
    err = EntitlementError(
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
    err.server_reasons = reasons
    err.detail = coded
    if screen_refusal:
        # A coded non-quota refusal (SCREENER_REFRESH_BELOW_FLOOR) is not a
        # missing scope: no re-login, and the envelope carries its own code.
        err.error_code = machine
        err.recovery_tool = None
        err.recovery_tool_args = None
        err.suggestion = None
    return err
