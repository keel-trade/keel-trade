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

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _login_summary(info: dict) -> dict:
    """Mirror the CLI login summary so CLI + MCP agree."""
    principal = info.get("principal") or {}
    org = info.get("org") or {}
    scopes = info.get("credential_scopes") or []
    is_live = "runner.*" in scopes
    summary = {
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
    # Spec 09: what moved (CL-4) or what awaits the user's decision (CL-8).
    if info.get("claimed_anon_org"):
        summary["claimed_anon_org"] = info["claimed_anon_org"]
    if info.get("pending_claim"):
        summary["pending_claim"] = info["pending_claim"]
        summary["pending_claim_instruction"] = (
            "This account already has strategies. Ask the user whether to "
            "attach the anonymous workspace's work to it, then call "
            "keel_auth_login again with attach_anonymous_work=true or false "
            "— that resolves the pending claim without re-opening the "
            "browser."
        )
    return summary


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.auth import browser_login

    scope = args.get("scope", "base")
    api_url = args.get("api_url")
    attach = args.get("attach_anonymous_work")

    # Spec 09 CL-8: with a deferred claim pending and an explicit decision,
    # resolve it directly — no browser flow.
    if attach is not None:
        from keel.anon import is_anon, resolve_pending_claim
        from keel.config import load_config

        if load_config().pending_claim and not is_anon():
            result = resolve_pending_claim(bool(attach), quiet=True) or {}
            return OutcomeResult(
                run_id=None,
                hero_url=f"{ctx.app_url}/settings",
                share_url=None,
                extra={"pending_claim_resolved": True, **result},
            )

    info = browser_login(
        api_url=api_url,
        include_live=(scope == "live"),
        auth_surface="mcp",
        attach_decision=attach,
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
        local_only=True,  # opens the user's browser + writes ~/.keel/config.yaml; hosted auth is the client's OAuth flow
        mcp_only=True,
        # grounded-in: keel/auth.py:45-46,101-135 (post-login the prior
        # anonymous grant is auto-claimed into the new account, best-effort);
        # config.py:25-27 (anon claim-later tier); auth_login.py docstring
        # (loopback flow, when-to-call, headless caveat).
        description=(
            "Run the OAuth 2.1 + PKCE browser-loopback login flow against Keel "
            "and persist tokens to ~/.keel/config.yaml so subsequent tool "
            "calls are authenticated. Opens the user's browser and waits up "
            "to 5 minutes for sign-in. Call this when `keel_status` returns "
            "`authenticated: false`, or whenever another tool's error "
            "envelope points here as the next action. If the current session "
            "was an anonymous grant, its workspace is auto-claimed into the "
            "signed-in account (best-effort — a failed claim degrades to a "
            "notice, never a failed login). Optional `scope='live'` pre-checks "
            "the live-trading consent box; optional `api_url=...` targets a "
            "non-default Keel deployment such as staging. Returns the same "
            "concise summary as the CLI's `keel auth login`. "
            "Do NOT use to refresh an existing session (the client refreshes "
            "transparently). Do NOT use for headless environments (CI, SSH "
            "without browser forwarding) — there the user should run "
            "`keel auth login --key <token>` from their terminal instead. "
            "If the result carries `pending_claim`, the signed-in account "
            "already has strategies: ask the user whether to attach the "
            "anonymous workspace, then call this tool again with "
            "`attach_anonymous_work` true/false — that resolves the pending "
            "claim without re-opening the browser."
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
                "attach_anonymous_work": {
                    "type": "boolean",
                    "description": (
                        "Spec 09 CL-8: the user's decision on attaching the "
                        "anonymous workspace to an account that already has "
                        "strategies. Pass true/false ONLY after asking the "
                        "user; with a pending claim stored, this resolves it "
                        "without re-running the browser flow. Omit on a "
                        "first login — fresh accounts claim automatically."
                    ),
                },
            },
            "required": [],
        },
        annotations={
            "title": "Log In to Keel",
            "readOnlyHint": False,  # writes ~/.keel/config.yaml
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,  # opens a browser + hits an external IdP
        },
        handler=_handler,
    )
)
