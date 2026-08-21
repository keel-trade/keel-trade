"""Auth commands — login, logout, whoami, status."""

from __future__ import annotations

import click

from keel.cli.agent_mode import is_agent_mode
from keel.cli.main import _get_format
from keel.errors import KeelError
from keel.output import emit, emit_error


def _login_summary(info: dict) -> dict:
    """Concise login confirmation. Same shape across human + JSON so
    agents can parse without dealing with the full /v1/me dump.

    The exhaustive view (entitlements, full scope list, principal
    metadata, etc.) is still one command away: `keel auth status`.
    """
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
            "keel status                              # see entitlements + scopes",
            "keel components search <topic>           # discover components by intent",
            "keel strategy compose --source-file ...  # author a new strategy",
            "keel strategy checkout <id>              # pull existing strategy into local workspace",
        ],
    }
    # Spec 09: surface the claim outcome so the user sees what moved (CL-4)
    # or what is waiting on their decision (CL-8).
    if info.get("claimed_anon_org"):
        summary["claimed_anon_org"] = info["claimed_anon_org"]
    if info.get("pending_claim"):
        pending = info["pending_claim"]
        summary["pending_claim"] = pending
        summary["next"].insert(
            0,
            "keel auth login --attach-anon            # attach your anonymous workspace to this account",
        )
    return summary


@click.group()
def auth() -> None:
    """Authenticate with the Keel platform."""


@auth.command()
@click.option(
    "--key",
    help="API key — skip the browser and paste a token (CI / SSH / Codespaces / WSL).",
)
@click.option(
    "--scope",
    type=click.Choice(["base", "live"]),
    default="base",
    show_default=True,
    help="OAuth scope tier. 'live' pre-checks the live-trading consent box.",
)
@click.option(
    "--api-url",
    help="Override Keel API URL (e.g. staging, self-hosted).",
)
@click.option(
    "--attach-anon/--no-attach-anon",
    "attach_anon",
    default=None,
    help=(
        "Attach (or skip attaching) the anonymous workspace to the "
        "signed-in account without prompting. With a deferred claim "
        "pending, resolves it directly — no browser flow."
    ),
)
@click.pass_context
def login(
    ctx: click.Context,
    key: str | None,
    scope: str,
    api_url: str | None,
    attach_anon: bool | None,
) -> None:
    """Log in to Keel.

    Default: opens a browser, runs the OAuth flow, persists tokens locally.
    For CI / SSH / remote dev environments: use --key with a token from
    https://app.usekeel.io/settings?tab=api-keys.
    """
    from keel.auth import browser_login

    # Spec 09 CL-8: a deferred claim resolves WITHOUT re-running OAuth when
    # the flag is explicit and credentials already exist.
    if attach_anon is not None:
        from keel.anon import is_anon, resolve_pending_claim
        from keel.config import load_config as _load_config

        if _load_config().pending_claim and not is_anon():
            result = resolve_pending_claim(attach_anon)
            emit(result or {"pending_claim": None}, _get_format(ctx))
            return

    # Explicit --key wins regardless of mode.
    if key:
        _do_api_key_login(ctx, key, api_url)
        return

    # Agent mode without --key: legacy stdin paste path (back-compat).
    if is_agent_mode():
        import sys

        stdin_key = sys.stdin.readline().strip()
        if not stdin_key:
            emit_error(
                {
                    "error": "usage_error",
                    "message": (
                        "No API key on stdin. In agent mode, pipe the key in or pass --key <token>."
                    ),
                },
                _get_format(ctx),
            )
            ctx.exit(2)
            return
        _do_api_key_login(ctx, stdin_key, api_url)
        return

    # Interactive default: browser-OAuth loopback flow.
    def _confirm_attach(counts):
        # Spec 09 CL-8 TTY prompt, default Y. Only offered on a real
        # terminal — non-TTY runs defer like MCP.
        from keel.anon import _prompt_counts_line

        return click.confirm(
            f"You have {_prompt_counts_line(counts)} in this anonymous "
            "workspace — attach them to your account?",
            default=True,
            err=True,
        )

    import sys as _sys

    interactive = _sys.stdin.isatty() and _sys.stderr.isatty()
    try:
        info = browser_login(
            api_url=api_url,
            include_live=(scope == "live"),
            attach_decision=attach_anon,
            confirm_attach=_confirm_attach if (interactive and attach_anon is None) else None,
        )
        emit(_login_summary(info), _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)
    except Exception as e:  # noqa: BLE001
        emit_error(e, _get_format(ctx))
        ctx.exit(4)


def _do_api_key_login(ctx: click.Context, key: str, api_url: str | None) -> None:
    """The classic PAT paste path — shared by --key and agent-mode stdin."""
    from keel.auth import store_api_key

    try:
        info = store_api_key(key, api_url=api_url)
        emit(_login_summary(info), _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)
    except Exception as e:  # noqa: BLE001
        emit_error(e, _get_format(ctx))
        ctx.exit(4)


@auth.command()
@click.pass_context
def logout(ctx: click.Context) -> None:
    """Clear stored credentials."""
    from keel.auth import clear_credentials

    clear_credentials()
    emit({"logged_out": True}, _get_format(ctx))


@auth.command()
@click.pass_context
def whoami(ctx: click.Context) -> None:
    """Show current identity, org, and plan."""
    from keel.auth import get_identity

    try:
        info = get_identity()
        emit(info, _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)


@auth.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show entitlement balances and active deployments."""
    from keel.auth import get_status

    try:
        info = get_status()
        emit(info, _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)
