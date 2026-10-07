"""Hosted-deployment execution mode + per-request credential binding.

The SDK runs in exactly two execution modes, selected by the
``KEEL_EXECUTION_MODE`` env var:

* ``local`` (default) — CLI / stdio MCP on a user's machine. Credentials
  come from ``~/.keel/config.yaml`` / ``KEEL_API_KEY`` as always.
* ``hosted`` — a shared multi-tenant server (services/mcp-server). Every
  request MUST act as the calling principal: the hosting process binds
  the caller's validated Bearer token for the request lifetime via
  :func:`bind_request_credentials`, and every ambient credential source
  (config file, ``KEEL_API_KEY``) is **disabled**. A hosted request with
  no bound credentials fails with an instructive auth error — never a
  silent fallback to pod-ambient credentials (spec 01 R1).

Contextvar propagation: the hosting process binds credentials inside the
per-request context (FastAPI dependency); everything the request spawns
(the inner ASGI task, tool handlers, resource reads) inherits that
context, so concurrent requests with different tokens can never see each
other's credentials.
"""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass

from keel.errors import AuthError


logger = logging.getLogger("keel.hosting")


EXECUTION_MODE_ENV = "KEEL_EXECUTION_MODE"
_VALID_MODES = ("local", "hosted")


def execution_mode() -> str:
    """Return the active execution mode: ``"local"`` or ``"hosted"``.

    Raises ``ValueError`` on any other value — a typo'd mode must never
    silently downgrade a hosted deployment to local-credential behavior.
    """
    raw = os.environ.get(EXECUTION_MODE_ENV, "").strip().lower()
    if not raw:
        return "local"
    if raw not in _VALID_MODES:
        raise ValueError(
            f"Invalid {EXECUTION_MODE_ENV}={raw!r}. Valid values: {', '.join(_VALID_MODES)}."
        )
    return raw


def is_hosted() -> bool:
    return execution_mode() == "hosted"


@dataclass(frozen=True)
class RequestCredentials:
    """The calling principal's credentials for one hosted request."""

    token: str = ""
    api_url: str = ""

    def __repr__(self) -> str:  # never leak the raw token into logs
        return f"RequestCredentials(api_url={self.api_url!r}, token=<redacted>)"


_REQUEST_CREDENTIALS: ContextVar[RequestCredentials | None] = ContextVar(
    "keel_request_credentials", default=None
)


def bind_request_credentials(*, token: str, api_url: str):
    """Bind the caller's credentials for the current request context.

    Returns the contextvars ``Token`` so the caller can reset. The
    hosting process calls this after validating the Bearer, before
    dispatching into the MCP app.
    """
    if not token:
        raise ValueError("bind_request_credentials requires a non-empty token")
    return _REQUEST_CREDENTIALS.set(RequestCredentials(token=token, api_url=api_url))


def clear_request_credentials(reset_token=None) -> None:
    """Clear the binding (or reset to the pre-bind state when the
    contextvars token from :func:`bind_request_credentials` is given)."""
    if reset_token is not None:
        _REQUEST_CREDENTIALS.reset(reset_token)
    else:
        _REQUEST_CREDENTIALS.set(None)


def current_request_credentials() -> RequestCredentials | None:
    """The credentials bound to the current request context, if any."""
    return _REQUEST_CREDENTIALS.get()


# ── Request outcome: the object each tool call produced ────────────────
#
# The hosting process opens one slot per tools/call BEFORE dispatch and reads
# it AFTER; tools record the ids of the objects they created or touched into
# it. The slot is the one channel from a tool to the audit row (Q-1617): the
# tool RESULT never changes (the ids an audit needs are not something an
# agent should be handed back — OpenAI's developer guidelines forbid
# telemetry/trace identifiers in tool responses), and the middleware never
# parses results. Object ids, booleans and two closed Keel vocabularies (the
# validator's rule codes, Q-2368, and the name of a help document Keel
# served, Q-2377) ONLY — never an argument, never a result payload: the
# closed key set is the function signature, and a value that does not have
# its key's shape is dropped, not recorded.
#
# The slot is a mutable dict held in a contextvar so it survives FastMCP
# running a sync tool in a worker thread: anyio copies the context INTO the
# thread, and a copy holds the same dict object, so a write made in the
# thread is visible to the middleware that opened it.

#: The platform id shape — `platform_auth.ids.ID_PATTERN` verbatim (a
#: 3-letter prefix, an underscore, a 26-char lowercase ULID). Re-declared
#: here because the SDK wheel cannot import libs; keel-api's ingest asserts
#: the two stay identical.
OUTCOME_ID_RE = re.compile(r"^[a-z]{3}_[0-9a-z]{26}$")

#: A validation rule code (Q-2368): the upper-snake catalog vocabulary of
#: `pipeline_engine.dsl.catalog.RULES` (`INVALID_UNIVERSE`, `TYPE_MISMATCH`).
#: A shape check, not catalog membership, so a newer SDK naming a new rule is
#: never refused by an older keel-api. No user text has this shape: it has no
#: spaces, no lowercase and no punctuation but `_`. keel-api's ingest asserts
#: the same pattern (`routers/audit.py` `ISSUE_CODE_PATTERN`).
OUTCOME_ISSUE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
#: At most this many distinct codes are recorded per call.
OUTCOME_ISSUE_CODES_MAX = 10
#: The name of a help document Keel served (Q-2377): a bundled doc slug
#: (`capability_boundaries`, `platform-operations`), a reserved index
#: (`skills`, `rules`, `(index)` for the no-topic listing), one skill
#: (`skill:strategy-creation`) or one rule (`rule:TYPE_MISMATCH`). keel-api's
#: ingest asserts the same pattern (`routers/audit.py` `DOC_REF_PATTERN`).
OUTCOME_DOC_REF_RE = re.compile(
    r"^(\(index\)|skill:[a-z0-9_-]{1,64}|rule:[A-Z0-9_]{1,64}|[a-z0-9_-]{1,64})$"
)

_REQUEST_OUTCOME: ContextVar[dict | None] = ContextVar("keel_request_outcome", default=None)


def open_request_outcome():
    """Open a fresh outcome slot for the current request context.

    Returns the contextvars ``Token`` so the opener can reset it in its
    ``finally``. The hosting middleware calls this immediately before
    dispatching a tool; a slot opened by nobody means "local mode" and
    :func:`record_outcome` is then a no-op.
    """
    return _REQUEST_OUTCOME.set({})


def close_request_outcome(reset_token) -> None:
    """Reset the slot to its pre-open state."""
    _REQUEST_OUTCOME.reset(reset_token)


def current_request_outcome() -> dict | None:
    """The outcome recorded so far in this request (``None`` when no slot
    is open). The dict is the live slot — callers copy before mutating."""
    return _REQUEST_OUTCOME.get()


def record_outcome(
    *,
    strategy_id: str | None = None,
    commit_id: str | None = None,
    backtest_run_id: str | None = None,
    dry_run: bool | None = None,
    is_error: bool | None = None,
    validation_ok: bool | None = None,
    issue_codes: list[str] | tuple[str, ...] | None = None,
    doc_ref: str | None = None,
) -> None:
    """Record the object(s) this tool call produced or touched.

    Keyword-only and closed: the signature IS the allow-list. Ids must match
    :data:`OUTCOME_ID_RE`; booleans must be ``bool``. A value that does not
    conform is dropped with a WARNING rather than raised — this runs AFTER
    the tool's side effect, and a telemetry slip must never turn a created
    strategy into an error response. ``None`` values are skipped so a tool
    can record what it knows without erasing what it recorded earlier.
    With no slot open (CLI / local stdio) this is a no-op.

    Who records what: the MCP adapter records what a result already says
    (``run_id`` by prefix, ``extra.strategy_id``, ``extra.dry_run``) once,
    for every tool; a tool records only what its result does not carry
    (``keel_strategy_compose`` records the ``commit_id`` the API returned).

    ``validation_ok`` is the VERDICT of a call that validates (Q-1840), kept
    apart from ``is_error`` on purpose: a dry run that reports a parse error
    did its job — the tool did not fail, so it must not count as a tool
    error in the call metrics — but the row recording it as a plain success
    was the one thing about it that was untrue. Absent when the call made
    no validity verdict.

    ``issue_codes`` says WHICH rules a failed validation tripped (Q-2368):
    the validator's catalog codes, Keel's own vocabulary — never the source,
    a message or an argument. Each element must match
    :data:`OUTCOME_ISSUE_CODE_RE` (a non-conforming element is dropped with
    a WARNING); codes are de-duplicated in order of first appearance and
    capped at :data:`OUTCOME_ISSUE_CODES_MAX`. An empty result records
    nothing.

    ``doc_ref`` is the name of the help document Keel SERVED (Q-2377) —
    Keel's own resource id, like a strategy id — never the topic text the
    caller typed. It must match :data:`OUTCOME_DOC_REF_RE`.
    """
    slot = _REQUEST_OUTCOME.get()
    if slot is None:
        return
    for key, value in (
        ("strategy_id", strategy_id),
        ("commit_id", commit_id),
        ("backtest_run_id", backtest_run_id),
    ):
        if value is None:
            continue
        if not isinstance(value, str) or not OUTCOME_ID_RE.match(value):
            logger.warning("record_outcome dropped %s: not a platform id", key)
            continue
        slot[key] = value
    for key, value in (
        ("dry_run", dry_run),
        ("is_error", is_error),
        ("validation_ok", validation_ok),
    ):
        if value is None:
            continue
        if not isinstance(value, bool):
            logger.warning("record_outcome dropped %s: not a bool", key)
            continue
        slot[key] = value
    if issue_codes is not None:
        codes = _conforming_issue_codes(issue_codes)
        if codes:
            slot["issue_codes"] = codes
    if doc_ref is not None:
        if isinstance(doc_ref, str) and OUTCOME_DOC_REF_RE.match(doc_ref):
            slot["doc_ref"] = doc_ref
        else:
            logger.warning("record_outcome dropped doc_ref: not a served document name")


def _conforming_issue_codes(issue_codes) -> list[str]:
    """``issue_codes`` filtered to the rule-code shape, deduped, capped."""
    if isinstance(issue_codes, str) or not isinstance(issue_codes, (list, tuple)):
        logger.warning("record_outcome dropped issue_codes: not a list of codes")
        return []
    codes: list[str] = []
    for code in issue_codes:
        if not isinstance(code, str) or not OUTCOME_ISSUE_CODE_RE.match(code):
            logger.warning("record_outcome dropped an issue code: not a rule code")
            continue
        if code not in codes:
            codes.append(code)
    return codes[:OUTCOME_ISSUE_CODES_MAX]


class HostedAuthError(AuthError):
    """Hosted request reached credential resolution with no caller token.

    Distinct from the local ``AuthError`` because the recovery is NOT
    ``keel_auth_login`` (that tool is local-only and not registered on
    hosted servers) — the MCP *client* has to re-run its OAuth flow
    against the server.
    """

    recovery_tool = None


def hosted_auth_error() -> HostedAuthError:
    """The one instructive error for a hosted request with no caller
    credentials. Raised instead of ANY ambient fallback."""
    return HostedAuthError(
        "No caller credentials are bound to this request on the hosted "
        "Keel MCP server. Tool calls always act as the calling principal; "
        "ambient server credentials are never used.",
        # Host-neutral (Q-2268): the hosted endpoint serves ChatGPT, claude.ai
        # and Claude Code alike, so the remedy names no one client's menu.
        suggestion=(
            "Re-authenticate Keel from the app you are using: reconnect (or "
            "remove and re-add) the Keel connector, complete its sign-in, then "
            "retry. If this persists after re-authenticating, it is a "
            "server-side bug — report it."
        ),
    )


__all__ = [
    "EXECUTION_MODE_ENV",
    "OUTCOME_DOC_REF_RE",
    "OUTCOME_ID_RE",
    "OUTCOME_ISSUE_CODES_MAX",
    "OUTCOME_ISSUE_CODE_RE",
    "HostedAuthError",
    "RequestCredentials",
    "bind_request_credentials",
    "clear_request_credentials",
    "close_request_outcome",
    "current_request_credentials",
    "current_request_outcome",
    "execution_mode",
    "hosted_auth_error",
    "is_hosted",
    "open_request_outcome",
    "record_outcome",
]
