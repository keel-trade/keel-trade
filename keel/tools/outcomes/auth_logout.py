"""`keel_auth_logout` — MCP-only tool that clears stored credentials.

Wraps `keel.auth.clear_credentials()` so an agent can sign the user
out and switch accounts without dropping back to a terminal. Same
plumbing as `keel auth logout` (CLI), exposed as an outcome tool.

UX flow:

  1. User says "log out of Keel" / "switch accounts".
  2. Agent calls `keel_auth_logout`.
  3. Tokens in ~/.keel/config.yaml are wiped (api_key + refresh_token
     + token_expires_at + client_name). `api_url` is preserved so the
     next login targets the same deployment.
  4. Response includes a next-hint suggesting `keel_auth_login` if the
     user wants to sign in as a different account.

No CLI binding — `keel auth logout` is already hand-rolled in
`keel.cli.commands.auth`. Setting `mcp_only=True` keeps the CLI
adapter from registering a duplicate command.
"""

from __future__ import annotations

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.auth import clear_credentials

    clear_credentials()
    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/settings",
        share_url=None,
        extra={
            "logged_out": True,
            "authenticated": False,
            "next": [
                "keel_auth_login   # sign in to a different account",
            ],
        },
    )


AUTH_LOGOUT = register(
    OutcomeTool(
        name="keel_auth_logout",
        required_action="",  # no auth required — logout always allowed
        cli_path=("auth", "logout"),  # informational only; mcp_only skips registration
        toolset="always",
        local_only=True,  # wipes ~/.keel/config.yaml; hosted sessions are revoked client-side
        mcp_only=True,
        description=(
            "Clear stored Keel credentials from ~/.keel/config.yaml so the "
            "next tool call is unauthenticated. Use to sign the user out, "
            "or to switch accounts (logout → keel_auth_login). Wipes "
            "api_key, refresh_token, token_expires_at, and client_name; "
            "preserves api_url so the next login targets the same "
            "deployment. Safe to call when already logged out (idempotent). "
            "Do NOT use to recover from a transient auth error — the "
            "client refreshes tokens transparently on 401; only run logout "
            "if the user explicitly asks, or if `keel_auth_login` is "
            "needed to switch identity."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        annotations={
            "title": "Log Out of Keel",
            "readOnlyHint": False,  # wipes ~/.keel/config.yaml
            "destructiveHint": True,  # removes user's session
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
