"""`keel_auth_login` — MCP-only tool that runs the loopback OAuth flow.

Stdio MCP servers (Keel's v1 surface) cannot use Claude Code's built-in
HTTP-MCP auth ceremony — that ceremony is HTTP-transport-only. So we
expose login as an outcome tool the agent calls directly. The agent
runs this when `keel_status` returns `authenticated: false`, or when
any other tool fails with an auth error pointing here.

UX flow:

  1. Agent calls `keel_auth_login` (optionally with `scope="live"` or
     `api_url=...`).
  2. This handler opens a browser on the user's machine via
     `webbrowser.open()`, binds a loopback listener, waits up to 5
     minutes for the redirect.
  3. The user completes sign-in in the browser tab; the page says
     "you can close this tab and return to your terminal".
  4. The handler exchanges the auth code for tokens, persists them to
     `~/.keel/config.yaml`, and returns the same concise summary as the
     CLI's `keel auth login` (authenticated/principal_id/org_id/plan/
     tier + next-hint).

No CLI binding — `keel auth login` is already hand-rolled in
`keel.cli.commands.auth`. Setting `mcp_only=True` keeps the CLI
adapter from registering a duplicate command.
"""

from __future__ import annotations

from typing import Any

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _login_summary(info: dict) -> dict:
    """Mirror the v0.4.1 CLI login summary so CLI + MCP agree."""
    principal = info.get("principal") or {}
    org = info.get("org") or {}
    scopes = info.get("credential_scopes") or []
    is_live = "runner.*" in scopes
    return {
        "authenticated": True,
        "principal_id": principal.get("id"),
        "org_id": org.get("id"),
        "org_name": org.get("name"),
        "plan": org.get("plan"),
        "tier": "live" if is_live else "base",
        "next": [
            "keel_status                 # see entitlements + visible tools",
            "prompts/list                # load strategy-creation before composing",
            "keel_components_search      # discover component candidates",
            "keel_components_detail_batch # fetch full schemas before drafting",
            "keel_strategy_compose       # dry-run first, then save",
        ],
    }


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.auth import browser_login

    scope = args.get("scope", "base")
    api_url = args.get("api_url")

    info = browser_login(
        api_url=api_url,
        include_live=(scope == "live"),
        auth_surface="mcp",
    )
    return OutcomeResult(
        run_id=None,
        hero_url=f"{ctx.app_url}/settings",
        share_url=None,
        extra=_login_summary(info),
    )


AUTH_LOGIN = register(
    OutcomeTool(
        name="keel_auth_login",
        required_action="",  # no auth required to call this!
        cli_path=("auth", "login"),  # informational only; mcp_only skips registration
        toolset="always",
        mcp_only=True,
        description=(
            "Run the OAuth 2.1 + PKCE browser-loopback login flow against Keel "
            "and persist tokens to ~/.keel/config.yaml so subsequent tool calls "
            "are authenticated. Opens the user's browser; waits up to 5 minutes "
            "for them to complete sign-in. Call this when `keel_status` returns "
            "`authenticated: false`, or whenever another tool's error envelope "
            "points here as the next-action. "
            "Optional `scope='live'` pre-checks the live-trading consent box. "
            "Optional `api_url=...` targets a non-default Keel deployment (e.g. "
            "staging). "
            "Returns the same concise summary as the CLI's `keel auth login`. "
            "Do NOT use to refresh an existing session (the client refreshes "
            "transparently). Do NOT use for headless environments (CI, SSH "
            "without browser forwarding) — there the user should run "
            "`keel auth login --key <token>` from their terminal instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["base", "live"],
                    "default": "base",
                    "description": (
                        "OAuth scope tier. 'live' pre-checks the live-trading "
                        "consent on the browser page; the user can still untick it."
                    ),
                },
                "api_url": {
                    "type": "string",
                    "description": (
                        "Override Keel API URL (e.g. a self-hosted instance "
                        "or staging). Default reads from ~/.keel/config.yaml "
                        "or env KEEL_API_URL."
                    ),
                },
            },
            "required": [],
        },
        annotations={
            "readOnlyHint": False,  # writes ~/.keel/config.yaml
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,  # opens a browser + hits an external IdP
        },
        handler=_handler,
    )
)
