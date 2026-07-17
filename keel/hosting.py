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

import os
from contextvars import ContextVar
from dataclasses import dataclass

from keel.errors import AuthError


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
        suggestion=(
            "Re-authenticate this MCP server from your client: in Claude "
            "Code run /mcp and re-authenticate the keel server (or remove "
            "and re-add it), then retry. If this persists after re-auth, "
            "it is a server-side bug — report it."
        ),
    )


__all__ = [
    "EXECUTION_MODE_ENV",
    "HostedAuthError",
    "RequestCredentials",
    "bind_request_credentials",
    "clear_request_credentials",
    "current_request_credentials",
    "execution_mode",
    "hosted_auth_error",
    "is_hosted",
]
